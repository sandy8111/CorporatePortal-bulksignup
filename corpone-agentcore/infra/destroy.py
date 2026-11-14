"""Delete everything infra/deploy.py created, newest first, using infra/build/state-<project>-<region>.json.

  python infra/destroy.py          # list what would be deleted
  python infra/destroy.py --yes    # delete it
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from corpone_infra.config import InfraConfig  # noqa: E402

BUILD = Path(__file__).resolve().parent / "build"


def actions(cfg, out):
    n = cfg.names()
    ctl = "bedrock-agentcore-control"
    acts = []
    if "online_eval_id" in out:
        acts.append((ctl, "delete_online_evaluation_config", {"onlineEvaluationConfigId": out["online_eval_id"]}))
    if "runtime_id" in out:
        acts.append((ctl, "delete_agent_runtime", {"agentRuntimeId": out["runtime_id"]}))
    if "memory_id" in out:
        acts.append((ctl, "delete_memory", {"memoryId": out["memory_id"]}))
    for k, v in sorted(out.items(), reverse=True):
        if k.startswith("policy_") and k.endswith("_id") and k not in ("policy_engine_id",):
            acts.append((ctl, "delete_policy", {"policyEngineId": out["policy_engine_id"], "policyId": v}))
    if "kill_switch_policy_id" in out:
        acts.append((ctl, "delete_policy", {"policyEngineId": out["policy_engine_id"], "policyId": out["kill_switch_policy_id"]}))
    if "target_id" in out:
        acts.append((ctl, "delete_gateway_target", {"gatewayIdentifier": out["gateway_id"], "targetId": out["target_id"]}))
    if "gateway_id" in out:
        acts.append((ctl, "delete_gateway", {"gatewayIdentifier": out["gateway_id"]}))
    if "policy_engine_id" in out:
        acts.append((ctl, "delete_policy_engine", {"policyEngineId": out["policy_engine_id"]}))
    if "credential_provider_arn" in out:
        acts.append((ctl, "delete_api_key_credential_provider", {"name": n["credential_provider"]}))
    for role, policy in (("eval_role", "agentcore-evaluations"), ("runtime_role", "agentcore-runtime"),
                         ("gateway_role", "agentcore-gateway")):
        if f"{role}_arn" in out:
            acts.append(("iam", "delete_role_policy", {"RoleName": n[role], "PolicyName": policy}))
            acts.append(("iam", "delete_role", {"RoleName": n[role]}))
    if "login_branding_id" in out:
        acts.append(("cognito-idp", "delete_managed_login_branding",
                     {"ManagedLoginBrandingId": out["login_branding_id"], "UserPoolId": out["user_pool_id"]}))
    if "user_pool_id" in out and "login_domain" in out:
        acts.append(("cognito-idp", "delete_user_pool_domain", {"Domain": out["login_domain"], "UserPoolId": out["user_pool_id"]}))
    if "user_pool_id" in out:
        acts.append(("cognito-idp", "delete_user_pool", {"UserPoolId": out["user_pool_id"]}))
    return acts


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--yes", action="store_true", help="really delete")
    a = ap.parse_args(argv)
    cfg = InfraConfig()
    STATE = BUILD / f"state-{cfg.dashed}-{cfg.region}.json"
    if not STATE.exists():
        sys.exit(f"Nothing to destroy for project {cfg.project} in {cfg.region}: {STATE} not found")
    print(f"Project {cfg.project} in {cfg.region} ({STATE.name})")
    out = json.loads(STATE.read_text())["outputs"]
    acts = actions(cfg, out)
    for svc, op, params in acts:
        print(f"{'DELETE' if a.yes else 'would delete'}: {svc}.{op} {params}")
    if not a.yes:
        print("\nRe-run with --yes to delete.")
        return
    import boto3
    from botocore.exceptions import ClientError
    clients = {}
    for svc, op, params in acts:
        client = clients.setdefault(svc, boto3.client(svc, region_name=cfg.region))
        for attempt in range(12):
            try:
                getattr(client, op)(**params)
                break
            except ClientError as exc:
                code = exc.response["Error"]["Code"]
                if code in ("ResourceNotFoundException", "NoSuchEntity", "NoSuchEntityException"):
                    break
                if code in ("ConflictException", "ValidationException", "DeleteConflict") and attempt < 11:
                    time.sleep(10)        # children still deleting
                    continue
                raise
    STATE.rename(STATE.with_suffix(".destroyed.json"))
    print("Destroyed. State moved to", STATE.with_suffix(".destroyed.json"))


if __name__ == "__main__":
    main()
