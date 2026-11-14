import pytest

from app.gateway.outbound import CircuitOpenError, GatewayError, gateway
from tests.conftest import ADMIN
from tests.test_flow import PDF, load_inventory, start


def make_key(client, scopes, rate=None):
    r = client.post("/api/admin/gateway/clients", headers=ADMIN,
                    json={"name": "partner", "scopes": scopes, "rate_limit_per_min": rate})
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['api_key']}"}, r.json()["id"]


def test_gateway_auth_scopes_and_audit(client):
    assert client.get("/gateway/v1/plans").status_code == 401
    assert client.get("/gateway/v1/plans", headers={"Authorization": "Bearer cpk_bad_key"}).status_code == 401
    h, cid = make_key(client, ["plans:read"])
    r = client.get("/gateway/v1/plans", headers=h)
    assert r.status_code == 200 and len(r.json()) == 3 and r.headers["X-RateLimit-Limit"] == "60"
    assert client.get("/gateway/v1/inventory/summary", headers=h).status_code == 403

    audit = client.get("/api/admin/gateway/audit", headers=ADMIN).json()
    assert [a["status"] for a in audit[:4]] == [403, 200, 401, 401]
    assert audit[0]["client"] == "partner"

    client.delete(f"/api/admin/gateway/clients/{cid}", headers=ADMIN)
    assert client.get("/gateway/v1/plans", headers=h).status_code == 401


def test_gateway_rate_limit(client):
    h, _ = make_key(client, ["plans:read"], rate=2)
    assert [client.get("/gateway/v1/plans", headers=h).status_code for _ in range(3)] == [200, 200, 429]


def test_unknown_scope_rejected(client):
    r = client.post("/api/admin/gateway/clients", headers=ADMIN, json={"name": "x", "scopes": ["root"]})
    assert r.status_code == 400


def test_gateway_validation_and_accounts(client):
    h, _ = make_key(client, ["validation:read", "accounts:read", "inventory:read", "inventory:write"])
    assert client.get("/gateway/v1/validate/uen/197401143C", headers=h).json() == \
        {"uen": "197401143C", "valid": True, "blacklisted": False}
    assert client.get("/gateway/v1/validate/uen/123", headers=h).json()["valid"] is False
    r = client.post("/gateway/v1/blacklist/check", headers=h,
                    json={"subject_type": "ACCOUNT_OFFICER", "id_type": "NRIC", "id_value": "S1234567D"})
    assert r.json()["blacklisted"] is False
    r = client.post("/gateway/v1/inventory/numbers", headers=h, data={"category": "ACME"},
                    files={"file": ("n.csv", "81234567\n")})
    assert r.json()["accepted"] == 1

    load_inventory(client)
    sid = start(client)
    client.post(f"/api/signups/{sid}/cart", json={"plan_code": "RAY-8", "sim_type": "PHYSICAL", "quantity": 1})
    acct = client.post(f"/api/signups/{sid}/submit").json()["account_number"]
    a = client.get(f"/gateway/v1/accounts/{acct}", headers=h).json()
    assert a["order_status"] == "COMPLETED"


def test_outbound_retries_then_succeeds(client):
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise GatewayError("503", retryable=True)
        return "ok"

    assert gateway.call("network", "test", flaky) == "ok" and len(calls) == 3
    log = client.get("/api/admin/gateway/outbound", headers=ADMIN).json()["calls"]
    assert [c["outcome"] for c in log[:3]] == ["ok", "retry", "retry"]


def test_outbound_non_retryable_fails_fast(client):
    calls = []

    def bad():
        calls.append(1)
        raise GatewayError("400", retryable=False)

    with pytest.raises(GatewayError):
        gateway.call("billing", "test", bad)
    assert len(calls) == 1


def test_circuit_breaker_opens_and_resets(client):
    def down():
        raise GatewayError("down", retryable=True)

    for _ in range(2):  # 3 attempts each -> 6 consecutive failures >= threshold 5
        with pytest.raises(GatewayError):
            gateway.call("network", "test", down)
    with pytest.raises(CircuitOpenError):
        gateway.call("network", "test", lambda: "never called")
    assert client.get("/api/admin/gateway/outbound", headers=ADMIN).json()["upstreams"]["network"]["circuit"] == "open"
    client.post("/api/admin/gateway/outbound/reset", headers=ADMIN)
    assert gateway.call("network", "test", lambda: "ok") == "ok"


def test_mcp_tools_go_through_gateway(client, monkeypatch):
    import mcp_server.server as srv
    h, _ = make_key(client, ["plans:read", "validation:read"])

    class _Wrapped:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def request(self, method, path, **kw):
            return client.request(method, "/gateway" + path, headers=h, **kw)

    monkeypatch.setattr(srv, "_client", lambda: _Wrapped())
    assert [p["code"] for p in srv.list_plans()] == ["RAY-8", "FLASH-20", "GLARE-40"]
    assert srv.validate_uen("197401143C")["valid"] is True
    assert srv.inventory_summary()["error"] == "HTTP 403"  # key lacks inventory:read


def test_gateway_openapi_is_separate(client):
    spec = client.get("/gateway/openapi.json").json()
    assert spec["info"]["title"] == "CorpOne Partner & AI Gateway"
    assert all(p.startswith("/v1/") for p in spec["paths"])
