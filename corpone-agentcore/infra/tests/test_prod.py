"""Production-notebook helpers, with AWS calls stubbed against the real API models."""
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import botocore.session
import httpx
import pytest
from botocore.stub import Stubber

from corpone_infra import prod

os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
BC = botocore.session.get_session()
POOL, CLIENT = "ap-southeast-1_AbCdEf123", "1example23456789abcdefghij"


def client(svc):
    return BC.create_client(svc, region_name="ap-southeast-1")


def test_strong_password_meets_pool_policy():
    for _ in range(50):
        p = prod.strong_password()
        assert len(p) == 20 and any(c.isupper() for c in p) and any(c.islower() for c in p)
        assert any(c.isdigit() for c in p) and any(not c.isalnum() for c in p)


def test_ensure_user_and_token():
    cog = client("cognito-idp")
    with Stubber(cog) as st:
        st.add_response("admin_create_user", {"User": {"Username": "ops1@corpone.sg"}},
                        {"UserPoolId": POOL, "Username": "ops1@corpone.sg", "MessageAction": "SUPPRESS",
                         "UserAttributes": [{"Name": "email", "Value": "ops1@corpone.sg"},
                                            {"Name": "email_verified", "Value": "true"}]})
        st.add_response("admin_set_user_password", {}, {"UserPoolId": POOL, "Username": "ops1@corpone.sg",
                                                        "Password": "Pw-1234567890abcd", "Permanent": True})
        st.add_response("admin_add_user_to_group", {}, {"UserPoolId": POOL, "Username": "ops1@corpone.sg", "GroupName": "ops"})
        prod.ensure_user(cog, POOL, "ops1@corpone.sg", "ops", "Pw-1234567890abcd")
        st.add_response("admin_initiate_auth", {"AuthenticationResult": {"AccessToken": "tok"}},
                        {"UserPoolId": POOL, "ClientId": CLIENT, "AuthFlow": "ADMIN_USER_PASSWORD_AUTH",
                         "AuthParameters": {"USERNAME": "ops1@corpone.sg", "PASSWORD": "Pw-1234567890abcd"}})
        assert prod.access_token(cog, POOL, CLIENT, "ops1@corpone.sg", "Pw-1234567890abcd") == "tok"
        st.add_client_error("admin_create_user", "UsernameExistsException")
        st.add_response("admin_set_user_password", {})
        st.add_response("admin_add_user_to_group", {})
        prod.ensure_user(cog, POOL, "ops1@corpone.sg", "ops", "Pw-1234567890abcd")   # re-run is fine


def test_model_available_both_kinds():
    br = client("bedrock")
    with Stubber(br) as st:
        st.add_response("get_inference_profile", {"inferenceProfileName": "x", "inferenceProfileArn": "arn:aws:bedrock:ap-southeast-1:111122223333:inference-profile/apac.x",
                                                  "models": [{"modelArn": "arn:aws:bedrock:ap-southeast-1::foundation-model/anthropic.x"}], "inferenceProfileId": "apac.x", "status": "ACTIVE", "type": "SYSTEM_DEFINED"},
                        {"inferenceProfileIdentifier": "apac.anthropic.claude-x"})
        assert prod.model_available(br, "apac.anthropic.claude-x")[0]
        st.add_response("get_foundation_model", {"modelDetails": {"modelArn": "arn:aws:bedrock:ap-southeast-1::foundation-model/m", "modelId": "m",
                                                                  "modelLifecycle": {"status": "LEGACY"}}},
                        {"modelIdentifier": "anthropic.claude-old"})
        ok, detail = prod.model_available(br, "anthropic.claude-old")
        assert not ok and "LEGACY" in detail
        st.add_client_error("get_foundation_model", "AccessDeniedException")
        assert not prod.model_available(br, "anthropic.nope")[0]


def test_portal_checks_report_scope_per_tool():
    def handler(req):
        if req.headers.get("x-api-key") != "cpk_good":
            return httpx.Response(401)
        if req.url.path.endswith("/inventory/summary"):
            return httpx.Response(403)            # key lacks inventory:read
        if "/accounts/" in req.url.path:
            return httpx.Response(404)            # made-up account: scope OK
        return httpx.Response(200, json={})
    http = httpx.Client(transport=httpx.MockTransport(handler))
    res = prod.portal_checks(http, "https://portal.example.sg/gateway/", "cpk_good")
    assert {k: v["ok"] for k, v in res.items()} == {"listPlans": True, "validateUen": True, "getInventorySummary": False,
                                                    "checkBlacklist": True, "getAccountStatus": True}
    assert all(not v["ok"] for v in prod.portal_checks(http, "https://p/gateway", "cpk_bad").values())


def test_verify_policy_in_both_modes():
    t = lambda names: [f"CorpOnePortal___{n}" for n in names]
    tools = {"officer": t(prod.OFFICER_TOOLS), "ops": t(prod.ALL_TOOLS)}
    calls = lambda tok, tool, args: {"is_error": tok == "officer" and tool.endswith("getInventorySummary"), "text": ""}
    r = prod.verify_policy(lambda tok: tools[tok], calls, "officer", "ops", enforced=True)
    assert r["passed"] and r["officer_tools"] == ["listPlans", "validateUen"]
    leaky = {"officer": t(prod.ALL_TOOLS), "ops": t(prod.ALL_TOOLS)}
    r = prod.verify_policy(lambda tok: leaky[tok], lambda *a: {"is_error": False, "text": ""}, "officer", "ops", enforced=True)
    assert not r["passed"] and not r["checks"]["officer sees only listPlans and validateUen"]
    r = prod.verify_policy(lambda tok: leaky[tok], lambda *a: {"is_error": False, "text": ""}, "officer", "ops", enforced=False)
    assert r["passed"]                               # LOG_ONLY: only logged, nothing hidden


def test_observability_helpers():
    logs, ctl = client("logs"), client("bedrock-agentcore-control")
    with Stubber(logs) as st:
        st.add_response("filter_log_events", {"events": [{"message": "a"}, {"message": "b"}]})
        assert prod.recent_span_count(logs, "corpone_agentcore_assistant.DEFAULT") == 2
        st.add_client_error("filter_log_events", "ResourceNotFoundException")
        assert prod.recent_span_count(logs, "x") == 0
    with Stubber(ctl) as st:
        st.add_response("get_online_evaluation_config", {
            "onlineEvaluationConfigArn": "arn:aws:bedrock-agentcore:ap-southeast-1:111122223333:online-evaluation-config/x",
            "onlineEvaluationConfigId": "corpone_online-AbCdE12345", "onlineEvaluationConfigName": "corpone_online",
            "status": "ACTIVE", "executionStatus": "ENABLED",
            "rule": {"samplingConfig": {"samplingPercentage": 10.0}},
            "dataSourceConfig": {"cloudWatchLogs": {"logGroupNames": ["aws/spans"], "serviceNames": ["s"]}},
            "evaluators": [], "evaluationExecutionRoleArn": "arn:aws:iam::111122223333:role/r",
            "createdAt": "2026-11-14T00:00:00Z", "updatedAt": "2026-11-14T00:00:00Z"},
            {"onlineEvaluationConfigId": "corpone_online-AbCdE12345"})
        assert prod.online_eval_status(ctl, "corpone_online-AbCdE12345") == {"status": "ACTIVE", "executionStatus": "ENABLED", "sampling": 10.0}


@pytest.fixture
def local_mcp_server():
    """The portal's own MCP server over streamable HTTP, to exercise the real MCP client code."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    portal = Path(__file__).resolve().parents[2] / "portal"
    proc = subprocess.Popen([sys.executable, "-m", "mcp_server.server", "--http"], cwd=portal,
                            env={**os.environ, "MCP_PORT": str(port), "CORPONE_GATEWAY_URL": "http://127.0.0.1:9/gateway"},
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    yield f"http://127.0.0.1:{port}/mcp"
    proc.terminate()
    proc.wait(5)


def test_mcp_client_lists_and_calls_tools(local_mcp_server):
    names = prod.gateway_tools(local_mcp_server, "any-token")
    assert {"list_plans", "validate_uen", "retry_provisioning"} <= set(names)
    r = prod.gateway_call(local_mcp_server, "any-token", "list_plans", {})
    assert "error" in r["text"].lower() or r["is_error"]     # the portal isn't running: tool reports the failure
    assert prod.gateway_call("http://127.0.0.1:9/mcp", "t", "x")["is_error"]


def test_service_access_reports_scp_denials():
    class Sess:
        def client(self, svc):
            c = client(svc)
            if svc == "xray":
                st = Stubber(c)
                st.add_client_error("get_trace_segment_destination", "AccessDeniedException",
                                    "not authorized ... with an explicit deny in a service control policy")
                st.activate()
            else:
                st = Stubber(c)
                for op in ("list_user_pools", "list_roles", "describe_repositories", "list_memories",
                           "list_policy_engines", "list_online_evaluation_configs", "describe_log_groups"):
                    try:
                        st.add_response(op, {})
                    except Exception:
                        pass
                st.activate()
            return c
    res = {name: (ok, detail) for name, ok, detail in prod.service_access(Sess())}
    assert res["access xray:get_trace_segment_destination"] == (False, "AccessDeniedException (blocked by an organization SCP)")
