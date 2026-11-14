"""Deploy (or preview) the CorpOne AgentCore stack.

  python infra/deploy.py                      # dry run: list every AWS request, nothing is created
  python infra/deploy.py --apply              # create what is missing; resumable (infra/build/state-<project>-<region>.json)
  python infra/deploy.py --apply --policy-mode ENFORCE   # switch the Gateway's Policy engine to enforcing
  python infra/deploy.py --apply --kill-switch on|off    # incident: deny / restore every tool call

Settings come from environment variables (infra/.env.example). PORTAL_API_KEY is read from the environment,
sent only to AgentCore Identity, and never written to the state file or printed.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from corpone_infra.config import InfraConfig  # noqa: E402
from corpone_infra.plan import openapi_payload, placeholder_outputs, policy_files, render_policy, steps  # noqa: E402

BUILD = Path(__file__).resolve().parent / "build"


def state_path(cfg) -> Path:
    """One state file per project and region, so a test stack can never be mistaken for production."""
    return BUILD / f"state-{cfg.dashed}-{cfg.region}.json"
SECRET_OUTPUTS = {"portal_api_key", "openapi"}


def dig(obj, path):
    for part in path.split("."):
        obj = obj[part]
    return obj


def masked(params, secret_params):
    shown = json.loads(json.dumps(params, default=str))
    for k in secret_params:
        if k in shown:
            shown[k] = "****"
    for k, v in shown.items():
        if isinstance(v, str) and len(v) > 300:
            shown[k] = v[:120] + f"... ({len(v)} chars)"
    if "targetConfiguration" in shown:
        shown["targetConfiguration"] = {"mcp": {"openApiSchema": {"inlinePayload": "<agent OpenAPI from portal code>"}}}
    return shown


def load_state(cfg):
    p = state_path(cfg)
    state = json.loads(p.read_text()) if p.exists() else {"outputs": {}, "done": []}
    state["_path"] = str(p)
    return state


def save_state(state):
    p = Path(state["_path"])
    p.parent.mkdir(parents=True, exist_ok=True)
    clean = {"outputs": {k: v for k, v in state["outputs"].items() if k not in SECRET_OUTPUTS}, "done": state["done"]}
    p.write_text(json.dumps(clean, indent=2))


def dry_run(cfg):
    out = placeholder_outputs(cfg)
    print(f"DRY RUN · project={cfg.project} region={cfg.region} · nothing will be created\n")
    for i, s in enumerate(steps(cfg), 1):
        print(f"{i:2}. {s.what}\n    {s.service}.{s.operation}  ->  outputs: {', '.join(s.outputs) or '-'}")
        print("    " + json.dumps(masked(s.build(out), s.secret_params), default=str)[:400])
    problems = cfg.problems(for_apply=True)
    if problems:
        print("\nTo deploy, fix:\n  - " + "\n  - ".join(problems))


def wait(client, step, outputs, timeout=900):
    w = step.wait
    params = {w.id_param: outputs[w.id_output], **{k: outputs[v] for k, v in w.extra.items()}}
    start = time.time()
    while True:
        resp = getattr(client, w.operation)(**params)
        status = dig(resp, w.status_path)
        if status in w.ready:
            return resp
        if status in w.failed or status.endswith("_FAILED") or status.endswith("UNSUCCESSFUL"):
            raise RuntimeError(f"{step.key} {status}: {resp.get('statusReasons') or resp.get('failureReason') or ''}")
        if time.time() - start > timeout:
            raise TimeoutError(f"{step.key} still {status} after {timeout}s")
        print(f"    ... {status}")
        time.sleep(5)


def call_with_iam_retry(fn, params, attempts=8):
    """New IAM roles take a few seconds to be usable by AgentCore; retry the create while they propagate."""
    from botocore.exceptions import ClientError
    for i in range(attempts):
        try:
            return fn(**params)
        except ClientError as exc:
            msg = str(exc)
            if i < attempts - 1 and exc.response["Error"]["Code"] in ("ValidationException", "AccessDeniedException") \
                    and ("role" in msg.lower() or "assume" in msg.lower()):
                time.sleep(10)
                continue
            raise


def apply(cfg, state, only_policy_mode=None):
    import boto3
    from botocore.exceptions import ClientError
    session = boto3.Session(region_name=cfg.region)
    cfg.account_id = cfg.account_id or session.client("sts").get_caller_identity()["Account"]
    clients = {}
    out = state["outputs"]
    out["portal_api_key"] = os.environ["PORTAL_API_KEY"]
    out["openapi"] = openapi_payload(cfg)
    out["login_domain"] = cfg.login_domain

    if only_policy_mode:
        ctl = session.client("bedrock-agentcore-control")
        gw = ctl.get_gateway(gatewayIdentifier=out["gateway_id"])
        keep = {k: gw[k] for k in ("name", "roleArn", "protocolType", "authorizerType") if k in gw}
        for k in ("description", "protocolConfiguration", "authorizerConfiguration"):
            if gw.get(k):
                keep[k] = gw[k]
        ctl.update_gateway(gatewayIdentifier=out["gateway_id"], **keep,
                           policyEngineConfiguration={"arn": out["policy_engine_arn"], "mode": only_policy_mode})
        print(f"Gateway policy mode set to {only_policy_mode}")
        return

    for s in steps(cfg):
        if s.key in state["done"]:
            continue
        client = clients.setdefault(s.service, session.client(s.service))
        params = s.build(out)
        print(f"-> {s.what}")
        try:
            resp = call_with_iam_retry(getattr(client, s.operation), params)
        except ClientError as exc:
            code = exc.response["Error"]["Code"]
            if s.operation == "create_role" and code == "EntityAlreadyExists":
                resp = client.get_role(RoleName=params["RoleName"])
            elif s.operation == "create_group" and code == "GroupExistsException":
                resp = {}
            else:
                raise
        for name, path in s.outputs.items():
            out[name] = dig(resp, path)
        if s.wait:
            wait(client, s, out)
        state["done"].append(s.key)
        save_state(state)
    print("\nDeployed. Outputs (no secrets) in", state["_path"])
    for k in ("gateway_url", "runtime_arn", "memory_id", "user_pool_id", "app_client_id", "online_eval_id"):
        print(f"  {k:16} {out.get(k)}")
    env = write_portal_env(cfg, out)
    print(f"\nAssistant page: {cfg.assistant_url}   (hosted login: {cfg.login_url})")
    print(f"Restart the portal with the settings in {env} to switch the page on.")
    if cfg.policy_mode == "LOG_ONLY":
        print("\nPolicy is LOG_ONLY: check decisions in CloudWatch, then run with --policy-mode ENFORCE.")


def portal_env(cfg, out) -> dict:
    """Settings that switch on the portal's /assistant page (no secrets)."""
    return {"CORPONE_ASSISTANT_RUNTIME_ARN": out["runtime_arn"], "CORPONE_ASSISTANT_REGION": cfg.region,
            "CORPONE_ASSISTANT_LOGIN_URL": cfg.login_url, "CORPONE_ASSISTANT_CLIENT_ID": out["app_client_id"]}


def write_portal_env(cfg, out) -> Path:
    p = BUILD / f"portal-assistant-{cfg.dashed}-{cfg.region}.env"
    p.write_text("".join(f"{k}={v}\n" for k, v in portal_env(cfg, out).items()))
    return p


def kill_switch(cfg, state, on: bool):
    import boto3
    ctl = boto3.client("bedrock-agentcore-control", region_name=cfg.region)
    out = state["outputs"]
    if on:
        path = policy_files(optional=True)[0]
        r = ctl.create_policy(policyEngineId=out["policy_engine_id"], name="stop_all_tool_calls",
                              definition={"cedar": {"statement": render_policy(path.read_text(), cfg, out["gateway_arn"])}},
                              validationMode="IGNORE_ALL_FINDINGS", description="incident kill switch")
        out["kill_switch_policy_id"] = r["policyId"]
        print("Kill switch ON: every tool call is denied (if the Gateway policy mode is ENFORCE).")
    else:
        ctl.delete_policy(policyEngineId=out["policy_engine_id"], policyId=out.pop("kill_switch_policy_id"))
        print("Kill switch OFF.")
    save_state(state)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="create resources (default: dry run)")
    ap.add_argument("--policy-mode", choices=["LOG_ONLY", "ENFORCE"], help="switch the deployed Gateway's policy mode")
    ap.add_argument("--kill-switch", choices=["on", "off"])
    a = ap.parse_args(argv)
    cfg = InfraConfig()
    problems = cfg.problems(for_apply=a.apply and not a.kill_switch)
    if not a.apply:
        return dry_run(cfg)
    if problems:
        sys.exit("Cannot deploy:\n  - " + "\n  - ".join(problems))
    state = load_state(cfg)
    if a.kill_switch:
        return kill_switch(cfg, state, a.kill_switch == "on")
    apply(cfg, state, only_policy_mode=a.policy_mode)


if __name__ == "__main__":
    main()
