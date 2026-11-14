"""deploy.py / destroy.py behaviour with a fake AWS session (requests still checked against the real models)."""
import json
import os

import boto3
import pytest
from botocore.validate import ParamValidator

import deploy
import destroy
from corpone_infra.config import InfraConfig
from corpone_infra.plan import placeholder_outputs, steps

os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
KEY = "cpk_live0001_" + "s" * 32
import botocore.session  # noqa: E402
_BOTOCORE = botocore.session.get_session()


@pytest.fixture
def env(monkeypatch, tmp_path):
    for k, v in {"PORTAL_GATEWAY_URL": "https://portal.example.sg/gateway", "AWS_REGION": "ap-southeast-1",
                 "MODEL_ID": "apac.anthropic.claude-sonnet-4-5", "PORTAL_API_KEY": KEY, "AWS_ACCOUNT_ID": "111122223333",
                 "AGENT_IMAGE_URI": "111122223333.dkr.ecr.ap-southeast-1.amazonaws.com/corpone-assistant:1"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(deploy, "BUILD", tmp_path)
    monkeypatch.setattr(destroy, "BUILD", tmp_path)
    monkeypatch.setattr(deploy.time, "sleep", lambda s: None)
    return tmp_path


class FakeClient:
    """Answers create_* with the outputs the plan expects and get_* with a ready status."""
    def __init__(self, service, log, fail_on=None):
        self.service, self.log, self.fail_on = service, log, fail_on
        self.model = _BOTOCORE.create_client(service, region_name="ap-southeast-1").meta

    def __getattr__(self, op):
        def call(**params):
            api = self.model.method_to_api_mapping[op]
            report = ParamValidator().validate(params, self.model.service_model.operation_model(api).input_shape)
            assert not report.has_errors(), report.generate_report()
            self.log.append((self.service, op, params))
            if op == self.fail_on:
                raise RuntimeError("boom")
            if op.startswith("get_"):
                status = {"get_policy_engine": "ACTIVE", "get_policy": "ACTIVE"}.get(op, "READY")
                return {"memory": {"status": "ACTIVE"}} if op == "get_memory" else {"status": status}
            fake = placeholder_outputs(InfraConfig())
            for s in steps(InfraConfig()):
                if s.operation == op and s.outputs:
                    resp = {}
                    for name, path in s.outputs.items():
                        node = resp
                        parts = path.split(".")
                        for p in parts[:-1]:
                            node = node.setdefault(p, {})
                        node[parts[-1]] = fake.get(name, f"{name}-value")
                    return resp
            return {}
        return call


class FakeSession:
    def __init__(self, log, fail_on=None):
        self.log, self.fail_on = log, fail_on

    def client(self, service):
        return FakeClient(service, self.log, self.fail_on)


def test_dry_run_creates_nothing_and_masks_the_key(env, capsys, monkeypatch):
    monkeypatch.setattr(boto3, "Session", lambda **k: pytest.fail("dry run must not create an AWS session"))
    deploy.main([])
    text = capsys.readouterr().out
    assert "DRY RUN" in text and KEY not in text and '"apiKey": "****"' in text
    assert "create_gateway_target" in text and "Cedar policy 40_forbid_retry_unless_ops.cedar" in text


def test_apply_runs_every_step_once_resumes_and_never_saves_the_key(env, monkeypatch):
    log = []
    monkeypatch.setattr(boto3, "Session", lambda **k: FakeSession(log, fail_on="create_memory"))
    with pytest.raises(RuntimeError):
        deploy.main(["--apply"])
    first = [op for _, op, _ in log if op.startswith(("create_", "put_"))]
    assert first[-1] == "create_memory" and "create_agent_runtime" not in first
    saved = (env / "state-corpone-agentcore-ap-southeast-1.json").read_text()
    assert KEY not in saved and "openapi" not in json.loads(saved)["outputs"]

    log.clear()
    monkeypatch.setattr(boto3, "Session", lambda **k: FakeSession(log))
    deploy.main(["--apply"])
    second = [op for _, op, _ in log if op.startswith(("create_", "put_"))]
    assert second[0] == "create_memory" and "create_gateway" not in second       # resumed, nothing repeated
    state = json.loads((env / "state-corpone-agentcore-ap-southeast-1.json").read_text())
    assert len(state["done"]) == len(steps(InfraConfig())) and KEY not in json.dumps(state)


def test_api_key_goes_only_to_agentcore_identity(env, monkeypatch):
    log = []
    monkeypatch.setattr(boto3, "Session", lambda **k: FakeSession(log))
    deploy.main(["--apply"])
    with_key = [(svc, op) for svc, op, p in log if KEY in json.dumps(p, default=str)]
    assert with_key == [("bedrock-agentcore-control", "create_api_key_credential_provider")]


def test_destroy_lists_then_deletes_in_reverse_order(env, monkeypatch, capsys):
    log = []
    monkeypatch.setattr(boto3, "Session", lambda **k: FakeSession(log))
    deploy.main(["--apply"])
    destroy.main([])
    assert "would delete" in capsys.readouterr().out and (env / "state-corpone-agentcore-ap-southeast-1.json").exists()
    deleted = []
    monkeypatch.setattr(boto3, "client", lambda svc, region_name=None: FakeClient(svc, deleted))
    destroy.main(["--yes"])
    ops = [op for _, op, _ in deleted]
    assert ops[0] == "delete_online_evaluation_config" and ops[-1] == "delete_user_pool"
    assert ops.index("delete_gateway_target") < ops.index("delete_gateway") < ops.index("delete_policy_engine")
    assert ops.count("delete_policy") == 4 and ops.count("delete_role") == 3
    assert not (env / "state-corpone-agentcore-ap-southeast-1.json").exists() and (env / "state-corpone-agentcore-ap-southeast-1.destroyed.json").exists()


def test_policy_mode_switch_request_is_valid(env, monkeypatch):
    log = []
    monkeypatch.setattr(boto3, "Session", lambda **k: FakeSession(log))
    deploy.main(["--apply"])
    log.clear()

    class GW(FakeClient):
        def __getattr__(self, op):
            if op == "get_gateway":
                return lambda **p: {"name": "corpone-agentcore-gateway", "roleArn": "arn:aws:iam::111122223333:role/r",
                                    "protocolType": "MCP", "authorizerType": "CUSTOM_JWT",
                                    "authorizerConfiguration": {"customJWTAuthorizer": {"discoveryUrl": "https://x/.well-known/openid-configuration"}}}
            return super().__getattr__(op)

    monkeypatch.setattr(boto3, "Session", lambda **k: type("S", (), {"client": lambda self, s: GW(s, log)})())
    deploy.main(["--apply", "--policy-mode", "ENFORCE"])
    upd = next(p for _, op, p in log if op == "update_gateway")
    assert upd["policyEngineConfiguration"]["mode"] == "ENFORCE" and upd["authorizerType"] == "CUSTOM_JWT"


def test_state_is_separate_per_project_and_region(env, monkeypatch):
    log = []
    monkeypatch.setattr(boto3, "Session", lambda **k: FakeSession(log))
    deploy.main(["--apply"])
    monkeypatch.setenv("PROJECT", "corpone-agentcore-test")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    assert deploy.load_state(InfraConfig())["done"] == []          # a test stack starts from nothing
    assert sorted(p.name for p in env.glob("state-*.json")) == ["state-corpone-agentcore-ap-southeast-1.json"]
