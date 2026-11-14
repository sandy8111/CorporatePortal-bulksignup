"""Step 1 groundwork for AgentCore: tool names, Gateway OpenAPI, client IP behind proxies, tracing hygiene."""
import pytest
from fastapi.testclient import TestClient

from app.gateway.agent_openapi import AGENT_TOOLS, build, problems
from app.gateway.inbound import gateway_app
from tests.conftest import ADMIN


def test_every_partner_operation_has_a_stable_unique_id():
    ops = [op for path in gateway_app.openapi()["paths"].values() for op in path.values()]
    ids = [op["operationId"] for op in ops]
    assert len(ids) == len(set(ids)) == 8
    assert set(AGENT_TOOLS) < set(ids) and {"uploadSims", "uploadNumbers"} <= set(ids)


def test_agent_openapi_is_gateway_compatible():
    doc = build("https://portal.example.sg/gateway/")
    assert problems(doc) == []
    assert doc["openapi"].startswith("3.0") and doc["servers"] == [{"url": "https://portal.example.sg/gateway"}]
    ops = {op["operationId"]: op for p in doc["paths"].values() for op in p.values()}
    assert set(ops) == set(AGENT_TOOLS)                       # uploads are never exposed to agents
    for op in ops.values():
        assert all(p["in"] != "header" for p in op.get("parameters", []))   # auth comes from the Gateway
        assert op.get("description") or op.get("summary")
    body = ops["checkBlacklist"]["requestBody"]["content"]["application/json"]["schema"]
    assert body["properties"]["on_date"] == {"type": "string", "format": "date"}   # anyOf[date,null] flattened
    assert problems({"servers": [{"url": "https://{host}/x"}], "paths": {"/a": {"get": {"anyOf": []}}}})


@pytest.fixture
def proxied(monkeypatch):
    from app.config import get_settings
    from app.netutil import _networks
    monkeypatch.setattr(get_settings(), "trusted_proxies", "127.0.0.1,10.0.0.0/8")
    _networks.cache_clear()
    yield
    _networks.cache_clear()


def _ip(headers, peer="127.0.0.1"):
    from starlette.requests import Request
    from app.netutil import client_ip
    scope = {"type": "http", "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
             "client": (peer, 1234)}
    return client_ip(Request(scope))


def test_client_ip_trusts_only_listed_proxies(proxied):
    assert _ip({"X-Forwarded-For": "203.0.113.9"}) == "203.0.113.9"                    # from nginx on 127.0.0.1
    assert _ip({"X-Forwarded-For": "1.1.1.1, 203.0.113.9, 10.1.2.3"}) == "203.0.113.9"  # spoofed 1.1.1.1 ignored
    assert _ip({"X-Forwarded-For": "203.0.113.9"}, peer="198.51.100.7") == "198.51.100.7"  # untrusted peer
    assert _ip({}) == "127.0.0.1"


def test_client_ip_ignores_header_when_no_proxies_configured():
    assert _ip({"X-Forwarded-For": "203.0.113.9"}) == "127.0.0.1"


def test_sign_in_rate_limit_is_per_real_client_behind_proxy(proxied):
    from app import auth
    from app.main import app
    hit = []
    real = auth._ip_limiter.hit
    auth._ip_limiter.hit = lambda key, limit: (hit.append(key), real(key, limit))[1]
    try:
        with TestClient(app, client=("127.0.0.1", 50000)) as via_nginx:   # the proxy's address
            via_nginx.post("/api/auth/otp/request", json={"email": "a@acme.sg"},
                           headers={"X-Forwarded-For": "203.0.113.50"})
    finally:
        auth._ip_limiter.hit = real
    assert hit == ["ip:203.0.113.50"]


def test_scrubbing_helpers():
    from app.telemetry import caller_kind, scrub_query, scrub_text
    assert scrub_query("/api/signups/x/sims?q=S1234567D&limit=5") == "/api/signups/x/sims?q=_&limit=_"
    assert scrub_query("q=91234567") == "q=_" and scrub_query("/api/plans") == "/api/plans"
    assert scrub_text("bad NRIC S1234567D for tan@acme.sg") == "bad NRIC <nric> for <email>"
    assert [caller_kind(c) for c in ("-", "admin", "api-client:3", "tan@acme.sg")] == ["anonymous", "admin", "api-client", "ao"]


def test_traces_carry_request_id_and_no_personal_data(client, monkeypatch):
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from app import main, telemetry
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "otel_enabled", True)
    exporter = InMemorySpanExporter()
    monkeypatch.setattr(telemetry, "setup", lambda app, exporter_=exporter, _real=telemetry.setup: _real(app, exporter_))
    app = main.create_app()
    with TestClient(app) as c:
        c.cookies.update(client.cookies)
        r = c.get("/api/merchants?q=S1234567D&email=tan%40acme.sg")
        c.get("/api/admin/inventory/summary", headers=ADMIN)
    server = [s for s in exporter.get_finished_spans() if s.kind.name == "SERVER"]
    assert server, "no server spans recorded"
    dump = " ".join(f"{k}={v}" for s in server for k, v in s.attributes.items())
    assert "S1234567D" not in dump and "tan" not in dump and "acme" not in dump
    first = next(s for s in server if "/api/merchants" in str(s.attributes.get("url.path", s.attributes.get("http.target", ""))))
    assert first.attributes["corpone.request_id"] == r.headers["x-request-id"]
    assert first.attributes["corpone.caller_kind"] == "ao"
    assert any(s.attributes.get("corpone.caller_kind") == "admin" for s in server)
