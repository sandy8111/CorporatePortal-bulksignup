"""Helpers for notebooks/deploy_to_production.ipynb: preflight checks, test users, smoke tests, policy and
observability verification. Every function takes its AWS clients / HTTP client as arguments so it can be
tested without AWS.
"""
import asyncio
import concurrent.futures
import secrets
import shutil
import string
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OFFICER_TOOLS = {"listPlans", "validateUen"}
ALL_TOOLS = OFFICER_TOOLS | {"getInventorySummary", "checkBlacklist", "getAccountStatus", "retryProvisioning"}


# ---------------------------------------------------------------- preflight
def aws_identity(sts) -> dict:
    ident = sts.get_caller_identity()
    return {"account": ident["Account"], "arn": ident["Arn"]}


def agentcore_available(ctl) -> tuple[bool, str]:
    try:
        ctl.list_gateways(maxResults=1)
        return True, "AgentCore control plane answers in this region"
    except Exception as exc:  # EndpointConnectionError, AccessDenied, ...
        return False, f"{type(exc).__name__}: {exc}"[:200]


def model_available(bedrock, model_id: str) -> tuple[bool, str]:
    """Inference profiles (us./eu./apac./global. prefixes) and plain foundation model ids."""
    try:
        if model_id.split(".", 1)[0] in ("us", "eu", "apac", "global", "jp", "au"):
            p = bedrock.get_inference_profile(inferenceProfileIdentifier=model_id)
            return p.get("status") == "ACTIVE", f"inference profile {p.get('status')}"
        m = bedrock.get_foundation_model(modelIdentifier=model_id)["modelDetails"]
        state = m.get("modelLifecycle", {}).get("status", "?")
        return state == "ACTIVE", f"foundation model {state}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"[:200]


def portal_checks(http, base_url: str, api_key: str) -> dict:
    """Call each read tool's endpoint with the API key. 2xx, or 404 for a made-up account, means the key works
    and has the scope; 401/403 means it doesn't. Write endpoints are never called."""
    probes = {
        "listPlans": ("GET", "/v1/plans", (200,)),
        "validateUen": ("GET", "/v1/validate/uen/197401143C", (200,)),
        "getInventorySummary": ("GET", "/v1/inventory/summary", (200,)),
        "checkBlacklist": ("POST", "/v1/blacklist/check", (200,)),
        "getAccountStatus": ("GET", "/v1/accounts/CA0000000000", (200, 404)),
    }
    out = {}
    for tool, (method, path, ok) in probes.items():
        body = {"subject_type": "COMPANY", "id_type": "UEN", "id_value": "197401143C"} if method == "POST" else None
        try:
            r = http.request(method, base_url.rstrip("/") + path, json=body, headers={"X-API-Key": api_key}, timeout=15)
            out[tool] = {"status": r.status_code, "ok": r.status_code in ok}
        except Exception as exc:
            out[tool] = {"status": None, "ok": False, "error": type(exc).__name__}
    return out


def service_access(session) -> list[tuple[str, bool, str]]:
    """One harmless read per service the deployment needs, to catch missing IAM permissions or an organization
    SCP that blocks the region before anything is created."""
    probes = [
        ("cognito-idp", "list_user_pools", {"MaxResults": 1}),
        ("iam", "list_roles", {"MaxItems": 1}),
        ("ecr", "describe_repositories", {"maxResults": 1}),
        ("bedrock-agentcore-control", "list_memories", {"maxResults": 1}),
        ("bedrock-agentcore-control", "list_policy_engines", {"maxResults": 1}),
        ("bedrock-agentcore-control", "list_online_evaluation_configs", {"maxResults": 1}),
        ("logs", "describe_log_groups", {"limit": 1}),
        ("xray", "get_trace_segment_destination", {}),
    ]
    out = []
    for svc, op, params in probes:
        try:
            getattr(session.client(svc), op)(**params)
            out.append((f"access {svc}:{op}", True, "ok"))
        except Exception as exc:
            code = getattr(exc, "response", {}).get("Error", {}).get("Code", type(exc).__name__)
            scp = "service control policy" in str(exc)
            out.append((f"access {svc}:{op}", False, f"{code}{' (blocked by an organization SCP)' if scp else ''}"))
    return out


def docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    return subprocess.run(["docker", "info"], capture_output=True).returncode == 0


def run_test_suites() -> tuple[bool, str]:
    r = subprocess.run([str(ROOT / "scripts" / "test_all.sh")], capture_output=True, text=True,
                       env={"PY": sys.executable, "PATH": "/usr/bin:/bin", "HOME": str(Path.home())})
    return r.returncode == 0, (r.stdout + r.stderr)[-800:]


# ---------------------------------------------------------------- test users and tokens
def strong_password(length=20) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*-_=+"
    while True:
        p = "".join(secrets.choice(alphabet) for _ in range(length))
        if (any(c.islower() for c in p) and any(c.isupper() for c in p) and any(c.isdigit() for c in p)
                and any(c in "!@#$%^&*-_=+" for c in p)):
            return p


def ensure_user(cognito, pool_id: str, email: str, group: str, password: str) -> None:
    """Create (or reset) an admin-created test user with a permanent password, in one group."""
    try:
        cognito.admin_create_user(UserPoolId=pool_id, Username=email, MessageAction="SUPPRESS",
                                  UserAttributes=[{"Name": "email", "Value": email},
                                                  {"Name": "email_verified", "Value": "true"}])
    except cognito.exceptions.UsernameExistsException:
        pass
    cognito.admin_set_user_password(UserPoolId=pool_id, Username=email, Password=password, Permanent=True)
    cognito.admin_add_user_to_group(UserPoolId=pool_id, Username=email, GroupName=group)


def access_token(cognito, pool_id: str, client_id: str, email: str, password: str) -> str:
    """Server-side sign-in (ADMIN_USER_PASSWORD_AUTH needs IAM credentials, so it can't be used from a browser)."""
    r = cognito.admin_initiate_auth(UserPoolId=pool_id, ClientId=client_id, AuthFlow="ADMIN_USER_PASSWORD_AUTH",
                                    AuthParameters={"USERNAME": email, "PASSWORD": password})
    return r["AuthenticationResult"]["AccessToken"]


def delete_user(cognito, pool_id: str, email: str) -> None:
    try:
        cognito.admin_delete_user(UserPoolId=pool_id, Username=email)
    except cognito.exceptions.UserNotFoundException:
        pass


# ---------------------------------------------------------------- Gateway (MCP) and Runtime
def _sync(coro):
    """Run a coroutine even inside Jupyter's already-running event loop."""
    with concurrent.futures.ThreadPoolExecutor(1) as pool:
        return pool.submit(asyncio.run, coro).result()


async def _mcp(gateway_url, token, tool=None, args=None):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    async with streamablehttp_client(gateway_url, headers={"Authorization": f"Bearer {token}"}) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            if tool is None:
                listed = await session.list_tools()
                return [t.name for t in listed.tools]
            result = await session.call_tool(tool, args or {})
            text = " ".join(getattr(c, "text", "") for c in result.content)
            return {"is_error": bool(result.isError), "text": text[:400]}


def gateway_tools(gateway_url, token) -> list[str]:
    return _sync(_mcp(gateway_url, token))


def gateway_call(gateway_url, token, tool, args=None) -> dict:
    try:
        return _sync(_mcp(gateway_url, token, tool, args))
    except Exception as exc:
        return {"is_error": True, "text": f"{type(exc).__name__}: {exc}"[:400]}


def plain(name: str) -> str:
    return name.split("___", 1)[1] if "___" in name else name


def verify_policy(list_tools, call_tool, officer_token, ops_token, enforced: bool) -> dict:
    """Talk to the Gateway directly as each role. In ENFORCE mode the Policy engine must hide and deny the ops-only
    tools for an officer; in LOG_ONLY mode everything is allowed and only logged."""
    officer = {plain(t) for t in list_tools(officer_token)}
    ops = {plain(t) for t in list_tools(ops_token)}
    target = next((t.split("___")[0] for t in list_tools(ops_token) if "___" in t), "CorpOnePortal")
    denied = call_tool(officer_token, f"{target}___getInventorySummary", {})
    allowed = call_tool(officer_token, f"{target}___listPlans", {})
    checks = {
        "ops sees all 6 tools": ops == ALL_TOOLS,
        "officer can call listPlans": not allowed["is_error"],
    }
    if enforced:
        checks["officer sees only listPlans and validateUen"] = officer == OFFICER_TOOLS
        checks["officer is denied getInventorySummary"] = denied["is_error"]
    return {"passed": all(checks.values()), "checks": checks, "officer_tools": sorted(officer),
            "ops_tools": sorted(ops), "officer_inventory_call": denied}


def invoke_runtime(region, runtime_arn, token, session_id, prompt) -> dict:
    sys.path.insert(0, str(ROOT / "evals"))
    from run_offline_eval import invoke
    return invoke(region, runtime_arn, token, session_id, prompt)


# ---------------------------------------------------------------- observability and evaluation
def recent_span_count(logs, service_name: str, minutes: int = 30) -> int:
    start = int((time.time() - minutes * 60) * 1000)
    try:
        r = logs.filter_log_events(logGroupName="aws/spans", startTime=start, filterPattern=f'"{service_name}"', limit=25)
        return len(r.get("events", []))
    except logs.exceptions.ResourceNotFoundException:
        return 0


def online_eval_status(ctl, config_id: str) -> dict:
    r = ctl.get_online_evaluation_config(onlineEvaluationConfigId=config_id)
    return {"status": r.get("status"), "executionStatus": r.get("executionStatus"),
            "sampling": r.get("rule", {}).get("samplingConfig", {}).get("samplingPercentage")}
