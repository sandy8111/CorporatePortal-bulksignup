import base64
import json
import os
from datetime import datetime, timezone

import boto3
import pytest
from botocore.stub import ANY, Stubber

from corpone_assistant import app as assistant
from corpone_assistant import identity, tools
from corpone_assistant.config import Config
from corpone_assistant.memory import ShortTermMemory
from corpone_assistant.redact import redact

os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
CONF = Config(gateway_url="https://gw.example/mcp", region="ap-southeast-1", model_id="test-model",
              memory_id="corpone_stm-Ab12Cd34Ef", history_turns=2)


def token(groups=None, sub="11111111-2222-3333-4444-555555555555"):
    body = {"sub": sub, "client_id": "abc", "token_use": "access"}
    if groups is not None:
        body["cognito:groups"] = groups
    enc = base64.urlsafe_b64encode(json.dumps(body).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJub25lIn0.{enc}.sig"


# ---------------------------------------------------------------- identity, redaction, tool filter
def test_identity_from_verified_token():
    c = identity.claims(token(["ops"]))
    assert identity.role(c) == "ops" and identity.actor_id(c) == "11111111-2222-3333-4444-555555555555"
    assert identity.role(identity.claims(token())) == "officer"            # no group: least privilege
    assert identity.role(identity.claims(token("ops"))) == "ops"
    assert identity.claims("not-a-jwt") == {} and identity.actor_id({}) == "anonymous"
    assert identity.bearer({"authorization": "Bearer abc"}) == "abc" and identity.bearer({}) == ""


def test_redaction_masks_nric_and_email_only():
    assert redact("NRIC S1234567D, fin g7654321x, tan.mei@acme.com.sg, line 91234567") == \
        "NRIC <nric>, fin <nric>, <email>, line 91234567"


class T:
    def __init__(self, name):
        self.tool_name = name


def test_role_tool_filter_matches_policy_split():
    all_tools = [T(f"CorpOnePortal___{n}") for n in tools.ROLE_TOOLS["ops"]] + [T("Other___uploadSims")]
    assert {tools.plain_name(t.tool_name) for t in tools.for_role(all_tools, "officer")} == {"listPlans", "validateUen"}
    assert len(tools.for_role(all_tools, "ops")) == 6 and tools.for_role(all_tools, "nobody") == []


# ---------------------------------------------------------------- memory (requests checked against the real API model)
def test_memory_reads_last_turns_and_writes_redacted_event():
    client = boto3.client("bedrock-agentcore", region_name="ap-southeast-1")
    mem = ShortTermMemory(client, "corpone_stm-Ab12Cd34Ef")
    ev = lambda i, u, a: {"memoryId": "corpone_stm-Ab12Cd34Ef", "actorId": "u1", "sessionId": "s1", "eventId": f"e{i}",
                          "eventTimestamp": datetime(2026, 11, 14, 10, i, tzinfo=timezone.utc),
                          "payload": [{"conversational": {"role": "USER", "content": {"text": u}}},
                                      {"conversational": {"role": "ASSISTANT", "content": {"text": a}}}]}
    with Stubber(client) as stub:
        stub.add_response("list_events", {"events": [ev(3, "q3", "a3"), ev(1, "q1", "a1")], "nextToken": "n"},
                          {"memoryId": "corpone_stm-Ab12Cd34Ef", "actorId": "u1", "sessionId": "s1", "includePayloads": True, "maxResults": 100})
        stub.add_response("list_events", {"events": [ev(2, "q2", "a2")]},
                          {"memoryId": "corpone_stm-Ab12Cd34Ef", "actorId": "u1", "sessionId": "s1", "includePayloads": True,
                           "maxResults": 100, "nextToken": "n"})
        msgs = mem.history("u1", "s1", turns=2)
        stub.add_response("create_event", {"event": {"memoryId": "corpone_stm-Ab12Cd34Ef", "actorId": "u1", "sessionId": "s1", "eventId": "e9",
                                                     "eventTimestamp": datetime.now(timezone.utc), "payload": []}},
                          {"memoryId": "corpone_stm-Ab12Cd34Ef", "actorId": "u1", "sessionId": "s1", "eventTimestamp": ANY,
                           "payload": [{"conversational": {"role": "USER", "content": {"text": "my NRIC is <nric>"}}},
                                       {"conversational": {"role": "ASSISTANT", "content": {"text": "noted, <email>"}}}]})
        mem.save_turn("u1", "s1", "my NRIC is S1234567D", "noted, a@b.co")
    assert [m["content"][0]["text"] for m in msgs] == ["q2", "a2", "q3", "a3"]     # oldest first, last 2 turns
    assert msgs[0]["role"] == "user" and msgs[1]["role"] == "assistant"
    assert ShortTermMemory(client, "").history("u1", "s1", 5) == []


# ---------------------------------------------------------------- request handling with fakes
class FakeClient:
    def __init__(self, names):
        self.names = names
        self.entered = False

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, *a):
        return False

    def list_tools_sync(self):
        return [T(f"CorpOnePortal___{n}") for n in self.names]


class FakeAgent:
    def __init__(self, conf, system_prompt, agent_tools, history):
        self.system_prompt, self.tools, self.history = system_prompt, agent_tools, history
        self.messages = [{"role": "assistant", "content": [{"toolUse": {"name": "CorpOnePortal___listPlans"}}]}]

    def __call__(self, prompt):
        class R:
            message = {"content": [{"text": f"answer to: {prompt}"}]}
        return R()


class FakeMemory:
    enabled = True

    def __init__(self):
        self.saved = []

    def history(self, actor, session, turns):
        return [{"role": "user", "content": [{"text": "earlier"}]}]

    def save_turn(self, *args):
        self.saved.append(args)


def run(groups, prompt="Which plans are there?"):
    seen = {}

    def connect(url, tok):
        seen["url"], seen["token"] = url, tok
        return FakeClient(sorted(tools.ROLE_TOOLS["ops"]))

    def build(conf, sp, ts, hist):
        seen["agent"] = FakeAgent(conf, sp, ts, hist)
        return seen["agent"]

    mem = FakeMemory()
    tok = token(groups)
    out = assistant.handle({"prompt": prompt}, {"Authorization": f"Bearer {tok}"}, "sess-1", conf=CONF,
                           connect=connect, build_agent=build, memory=mem)
    return out, seen, mem, tok


def test_officer_gets_two_tools_and_users_own_token_is_forwarded():
    out, seen, mem, tok = run(None)
    assert out["status"] == "success" and out["metadata"]["role"] == "officer"
    assert out["metadata"]["tools_allowed"] == ["listPlans", "validateUen"]
    assert seen["token"] == tok and seen["url"] == CONF.gateway_url              # identity propagated
    assert "account officer" in seen["agent"].system_prompt and seen["agent"].history[0]["content"][0]["text"] == "earlier"
    assert out["metadata"]["tools_used"] == ["listPlans"]
    assert mem.saved == [("11111111-2222-3333-4444-555555555555", "sess-1", "Which plans are there?",
                          "answer to: Which plans are there?")]


def test_ops_gets_all_six_tools_and_ops_prompt():
    out, seen, _, _ = run(["ops"])
    assert out["metadata"]["role"] == "ops" and len(out["metadata"]["tools_allowed"]) == 6
    assert 'yes, retry' in seen["agent"].system_prompt


def test_rejects_missing_token_prompt_or_config():
    assert assistant.handle({"prompt": "hi"}, {}, "s", conf=CONF)["error"] == "Missing bearer token"
    assert assistant.handle({}, {"Authorization": "Bearer x"}, "s", conf=CONF)["error"] == "No prompt provided"
    assert assistant.handle({"warmup": True}, {}, None, conf=CONF) == {"status": "warmed"}
    assert "not configured" in assistant.handle({"prompt": "hi"}, {"Authorization": "Bearer x"}, "s", conf=Config())["error"]


def test_memory_failure_does_not_break_the_answer():
    class Broken(FakeMemory):
        def save_turn(self, *a):
            raise RuntimeError("throttled")
    out = assistant.handle({"prompt": "hi"}, {"Authorization": f"Bearer {token()}"}, "s", conf=CONF,
                           connect=lambda u, t: FakeClient(["listPlans"]), build_agent=FakeAgent, memory=Broken())
    assert out["status"] == "success"


# ---------------------------------------------------------------- through the real AgentCore Runtime app
def test_runtime_app_passes_authorization_header_and_session(monkeypatch):
    from starlette.testclient import TestClient
    captured = {}

    def fake_handle(payload, headers, session_id, **kw):
        captured.update(payload=payload, headers=headers, session=session_id)
        return {"status": "success", "response": "ok"}

    monkeypatch.setattr(assistant, "handle", fake_handle)
    app = assistant.create_app()
    with TestClient(app) as c:
        r = c.post("/invocations", json={"prompt": "hello"},
                   headers={"Authorization": "Bearer abc.def.ghi",
                            "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": "session-0123456789-0123456789-0123"})
        ping = c.get("/ping")
    assert r.status_code == 200 and r.json()["response"] == "ok" and ping.status_code == 200
    assert captured["payload"] == {"prompt": "hello"}
    assert identity.bearer(captured["headers"]) == "abc.def.ghi"
    assert captured["session"] == "session-0123456789-0123456789-0123"


def test_runtime_app_hides_exception_details_with_personal_data(monkeypatch):
    from starlette.testclient import TestClient
    monkeypatch.setattr(assistant, "handle", lambda *a, **k: (_ for _ in ()).throw(ValueError("bad S1234567D for x@y.com")))
    with TestClient(assistant.create_app()) as c:
        body = c.post("/invocations", json={"prompt": "hi"}, headers={"Authorization": "Bearer t"}).json()
    assert body["status"] == "error" and "S1234567D" not in body["error"] and "x@y.com" not in body["error"]
