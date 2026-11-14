"""Every request the deployment would send, checked against the real AWS API models (no AWS calls)."""
import json
import os
import re

import boto3
import pytest
from botocore.validate import ParamValidator

from corpone_infra.config import InfraConfig
from corpone_infra.plan import EVALUATORS, placeholder_outputs, policy_files, render_policy, steps

os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")


@pytest.fixture
def cfg():
    return InfraConfig(project="corpone-agentcore", region="ap-southeast-1", account_id="111122223333",
                       portal_gateway_url="https://portal.example.sg/gateway",
                       model_id="apac.anthropic.claude-sonnet-4-5", agent_image_uri=
                       "111122223333.dkr.ecr.ap-southeast-1.amazonaws.com/corpone-assistant:1",
                       policy_mode="LOG_ONLY", eval_sampling_percent=10, memory_expiry_days=7)


UNCHECKED = set()


def java_regex(pat):
    """AWS models use Java regex; translate the constructs Python lacks (\\p{..} classes, possessive +)."""
    py = (pat.replace(r"\p{L}", "a-zA-Z\u00C0-\uFFFF").replace(r"\p{N}", "0-9").replace(r"\p{Alnum}", "a-zA-Z0-9")
             .replace(r"\p{Punct}", "!-/:-@\\[-`{-~").replace(r"\p{Space}", "\\s").replace(r"\p{Z}", "\\s")
             .replace(r"\p{M}", "\u0300-\u036f").replace(r"\p{Print}", " -~"))
    py = re.sub(r"\]\+\+", "]+", py)
    try:
        return re.compile(py, re.S)
    except re.error:
        return None


def pattern_problems(shape, value, where="$"):
    """botocore checks types and lengths; this adds the regex patterns and enums from the model."""
    found = []
    if shape.type_name == "structure" and isinstance(value, dict):
        for k, v in value.items():
            if k in shape.members:
                found += pattern_problems(shape.members[k], v, f"{where}.{k}")
    elif shape.type_name == "list" and isinstance(value, list):
        for i, v in enumerate(value):
            found += pattern_problems(shape.member, v, f"{where}[{i}]")
    elif shape.type_name == "map" and isinstance(value, dict):
        for k, v in value.items():
            found += pattern_problems(shape.value, v, f"{where}.{k}")
    elif shape.type_name == "string" and isinstance(value, str):
        pat = shape.metadata.get("pattern")
        if pat:
            rx = java_regex(pat)
            if rx is None:
                UNCHECKED.add(pat)
            elif not rx.fullmatch(value):
                found.append(f"{where}={value[:60]!r} !~ {pat}")
        if getattr(shape, "enum", None) and value not in shape.enum:
            found.append(f"{where}={value!r} not in {shape.enum}")
    return found


def built(cfg):
    out = placeholder_outputs(cfg)
    return [(s, s.build(out)) for s in steps(cfg)]


def test_every_request_matches_the_aws_api_model(cfg):
    validator = ParamValidator()
    clients = {}
    for step, params in built(cfg):
        client = clients.setdefault(step.service, boto3.client(step.service, region_name=cfg.region))
        op_name = client.meta.method_to_api_mapping[step.operation]
        shape = client.meta.service_model.operation_model(op_name).input_shape
        report = validator.validate(params, shape)
        assert not report.has_errors(), f"{step.key}: {report.generate_report()}"
        assert pattern_problems(shape, params) == [], step.key
    assert len(UNCHECKED) <= 3, f"patterns that could not be checked: {UNCHECKED}"


def test_order_dependencies_and_outputs(cfg):
    keys = [s.key for s in steps(cfg)]
    assert keys.index("gateway_role") < keys.index("gateway") < keys.index("gateway_target") < keys.index("policy_10_everyone_read_plans_and_uen")
    assert keys.index("policy_engine") < keys.index("gateway") and keys.index("memory") < keys.index("runtime") < keys.index("online_eval")
    produced = {o for s in steps(cfg) for o in s.outputs}
    assert {"gateway_url", "gateway_arn", "memory_id", "runtime_id", "credential_provider_arn", "app_client_id"} <= produced


def test_identity_gateway_and_runtime_settings(cfg):
    req = dict((s.key, p) for s, p in built(cfg))
    gw = req["gateway"]
    assert gw["authorizerType"] == "CUSTOM_JWT" and gw["policyEngineConfiguration"]["mode"] == "LOG_ONLY"
    assert gw["authorizerConfiguration"]["customJWTAuthorizer"]["discoveryUrl"].endswith(
        "/ap-southeast-1_AbCdEf123/.well-known/openid-configuration")
    tgt = req["gateway_target"]["credentialProviderConfigurations"][0]
    assert tgt["credentialProviderType"] == "API_KEY"
    assert tgt["credentialProvider"]["apiKeyCredentialProvider"]["credentialParameterName"] == "X-API-Key"
    tools = json.loads(req["gateway_target"]["targetConfiguration"]["mcp"]["openApiSchema"]["inlinePayload"])
    assert {op["operationId"] for p in tools["paths"].values() for op in p.values()} == {
        "listPlans", "validateUen", "getInventorySummary", "checkBlacklist", "getAccountStatus", "retryProvisioning"}
    rt = req["runtime"]
    assert rt["requestHeaderConfiguration"]["requestHeaderAllowlist"] == ["Authorization"]
    assert rt["environmentVariables"]["GATEWAY_URL"].endswith("/mcp") and "apiKey" not in json.dumps(rt)
    assert req["memory"]["eventExpiryDuration"] == 7 and "memoryStrategies" not in req["memory"]   # short-term only
    ev = req["online_eval"]
    assert ev["rule"]["samplingConfig"]["samplingPercentage"] == 10
    assert [e["evaluatorId"] for e in ev["evaluators"]] == EVALUATORS


def test_secrets_are_marked_and_roles_are_scoped(cfg):
    req = dict((s.key, (s, p)) for s, p in built(cfg))
    step, params = req["credential_provider"]
    assert step.secret_params == ("apiKey",) and params["apiKey"].startswith("cpk_")
    gw_policy = json.loads(req["gateway_role_policy"][1]["PolicyDocument"])
    actions = {a for st in gw_policy["Statement"] for a in st["Action"]}
    assert {"bedrock-agentcore:AuthorizeAction", "bedrock-agentcore:PartiallyAuthorizeActions",
            "bedrock-agentcore:GetPolicyEngine"} <= actions
    for st in gw_policy["Statement"]:
        assert all(":111122223333:" in r for r in st["Resource"]), st["Sid"]
    trust = json.loads(req["gateway_role"][1]["AssumeRolePolicyDocument"])["Statement"][0]
    assert trust["Condition"]["StringEquals"]["aws:SourceAccount"] == "111122223333"


def test_cedar_policies_render_with_gateway_and_target(cfg):
    arn = placeholder_outputs(cfg)["gateway_arn"]
    files = policy_files()
    assert [p.name for p in files] == ["10_everyone_read_plans_and_uen.cedar", "20_ops_read_back_office.cedar",
                                       "30_ops_retry_provisioning.cedar", "40_forbid_retry_unless_ops.cedar"]
    for p in files + policy_files(optional=True):
        text = render_policy(p.read_text(), cfg, arn)
        assert "{{" not in text
        for action in re.findall(r'AgentCore::Action::"([^"]+)"', text):
            assert re.fullmatch(r"CorpOnePortal___[A-Za-z]+", action), action
        assert ('resource == AgentCore::Gateway::"' + arn + '"' in text) or "resource is AgentCore::Gateway" in text
    ops_only = render_policy(files[1].read_text(), cfg, arn)
    assert 'principal.hasTag("cognito:groups")' in ops_only and 'like "*\\"ops\\"*"' in ops_only
    every_tool = {"listPlans", "validateUen", "getInventorySummary", "checkBlacklist", "getAccountStatus", "retryProvisioning"}
    rendered = [render_policy(p.read_text(), cfg, arn) for p in files]
    permitted = set(re.findall(r'CorpOnePortal___(\w+)', " ".join(t for t in rendered if "\npermit(" in "\n" + t.split("*/")[-1])))
    assert permitted == every_tool          # every agent tool is permitted for someone; nothing else is


def test_config_problems():
    bad = InfraConfig(portal_gateway_url="http://x", policy_mode="ON", eval_sampling_percent=0, memory_expiry_days=3)
    assert len(bad.problems(for_apply=False)) == 4
    os.environ.pop("PORTAL_API_KEY", None)
    assert any("PORTAL_API_KEY" in p for p in InfraConfig(portal_gateway_url="https://x").problems(for_apply=True))
    n = InfraConfig(project="CorpOne Agent-Core").names()
    assert n["memory"] == "CorpOne_Agent_Core_stm" and n["gateway"] == "CorpOne-Agent-Core-gateway"
