"""/assistant page and its relay to AgentCore Runtime."""
import re

import httpx
import pytest

from app.config import get_settings
from app.routers import assistant

ARN = "arn:aws:bedrock-agentcore:us-east-1:111122223333:runtime/corpone_agentcore_test_assistant-AbCdE12345"
SID = "assistant-0b6c2a4e-1f0d-4d0e-9a51-2b6f1f2d8a11"


@pytest.fixture
def on(monkeypatch):
    s = get_settings()
    for k, v in {"assistant_runtime_arn": ARN, "assistant_region": "us-east-1", "assistant_client_id": "abc123client",
                 "assistant_login_url": "https://corpone-agentcore-test-778102.auth.us-east-1.amazoncognito.com/"}.items():
        monkeypatch.setattr(s, k, v)
    assistant.limiter._hits.clear() if hasattr(assistant.limiter, "_hits") else None
    sent = {}

    def fake_post(url, body, headers):
        sent.update(url=url, body=body, headers=headers)
        return httpx.Response(sent.get("status", 200), json=sent.get("reply", {
            "status": "success", "response": "Ray, Flash and Glare.", "metadata": {"role": "officer", "tools_used": ["listPlans"]}}))
    monkeypatch.setattr(assistant, "_post", fake_post)
    return sent


def test_page_is_served_with_fingerprinted_script(anon):
    r = anon.get("/assistant")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    assert re.search(r'src="static/v/[0-9a-f]{12}/assistant\.js"', r.text)
    assert anon.get("/ports/8501/assistant").status_code == 200


def test_config_off_until_deployed(anon, on, monkeypatch):
    assert anon.get("/api/assistant/config").json() == {
        "enabled": True, "login_url": "https://corpone-agentcore-test-778102.auth.us-east-1.amazoncognito.com",
        "client_id": "abc123client", "scopes": "openid email profile"}
    monkeypatch.setattr(get_settings(), "assistant_runtime_arn", "")
    assert anon.get("/api/assistant/config").json() == {"enabled": False}
    assert anon.post("/api/assistant/invoke", json={"prompt": "hi", "session_id": SID},
                     headers={"Authorization": "Bearer " + "t" * 30}).status_code == 503


def test_relay_forwards_the_users_token_to_the_runtime(anon, on):
    r = anon.post("/api/assistant/invoke", json={"prompt": "Which plans?", "session_id": SID},
                  headers={"Authorization": "Bearer eyJ.user.token-0123456789"})
    assert r.status_code == 200 and r.json() == {"status": "success", "response": "Ray, Flash and Glare.",
                                                  "role": "officer", "tools_used": ["listPlans"]}
    assert on["url"] == ("https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/"
                         "arn%3Aaws%3Abedrock-agentcore%3Aus-east-1%3A111122223333%3Aruntime%2F"
                         "corpone_agentcore_test_assistant-AbCdE12345/invocations?qualifier=DEFAULT")
    assert on["headers"]["Authorization"] == "Bearer eyJ.user.token-0123456789"      # passed through unchanged
    assert on["headers"]["X-Amzn-Bedrock-AgentCore-Runtime-Session-Id"] == SID and on["body"] == {"prompt": "Which plans?"}
    assert r.headers["cache-control"] == "no-store, private"


def test_relay_rejects_bad_requests_and_maps_errors(anon, on):
    h = {"Authorization": "Bearer " + "t" * 30}
    assert anon.post("/api/assistant/invoke", json={"prompt": "hi", "session_id": SID}).status_code == 401
    assert anon.post("/api/assistant/invoke", json={"prompt": "hi", "session_id": "short"}, headers=h).status_code == 400
    assert anon.post("/api/assistant/invoke", json={"prompt": "", "session_id": SID}, headers=h).status_code == 422
    on["status"] = 403
    assert anon.post("/api/assistant/invoke", json={"prompt": "hi", "session_id": SID}, headers=h).status_code == 401
    on["status"] = 500
    assert anon.post("/api/assistant/invoke", json={"prompt": "hi", "session_id": SID}, headers=h).status_code == 502


def test_relay_rate_limit_per_client(anon, on, monkeypatch):
    monkeypatch.setattr(get_settings(), "assistant_rate_per_min", 2)
    monkeypatch.setattr(assistant, "limiter", assistant.SlidingWindowLimiter(window_seconds=60))
    h = {"Authorization": "Bearer " + "t" * 30}
    codes = [anon.post("/api/assistant/invoke", json={"prompt": "hi", "session_id": SID}, headers=h).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_runtime_url_override(on, monkeypatch):
    monkeypatch.setattr(get_settings(), "assistant_runtime_url", "https://vpce-123.bedrock-agentcore.us-east-1.vpce.amazonaws.com/x")
    assert assistant.runtime_url() == "https://vpce-123.bedrock-agentcore.us-east-1.vpce.amazonaws.com/x"
