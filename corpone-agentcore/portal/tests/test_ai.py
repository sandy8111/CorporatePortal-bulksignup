import json
import time
from types import SimpleNamespace

import pytest

from app.config import get_settings
from app.services import ai
from tests.test_flow import PDF, UEN


def _response(payload: dict, stop_reason="end_turn"):
    return SimpleNamespace(stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=json.dumps(payload))])


@pytest.fixture
def fake_ai(monkeypatch):
    calls = []
    state = {"resp": None}

    class FakeMessages:
        def create(self, **kw):
            calls.append(kw)
            return state["resp"]

    monkeypatch.setattr(get_settings(), "ai_provider", "anthropic")
    monkeypatch.setattr(ai, "_client", lambda: SimpleNamespace(beta=SimpleNamespace(messages=FakeMessages())))
    return calls, state


GOOD = {"is_service_agreement": True, "company_name_in_document": "ACME PTE. LTD.", "uen_in_document": UEN,
        "appears_signed": True, "issues": [], "summary": "Corporate mobile service agreement."}


def test_review_matches(fake_ai):
    calls, state = fake_ai
    state["resp"] = _response(GOOD)
    r = ai.review_contract(PDF, UEN, "Acme Pte Ltd")
    assert r["status"] == "ok" and r["uen_match"] and r["name_match"]
    kw = calls[0]
    assert kw["model"] == "claude-opus-5-5"
    assert kw["fallbacks"] == "default" and kw["betas"] == ["server-side-fallback-2026-07-01"]
    assert kw["output_config"]["format"]["type"] == "json_schema"
    assert kw["messages"][0]["content"][0]["type"] == "document"


def test_review_flags_mismatch_and_unsigned(fake_ai):
    _, state = fake_ai
    state["resp"] = _response({**GOOD, "uen_in_document": "00192200M", "appears_signed": False})
    r = ai.review_contract(PDF, UEN, "Acme Pte Ltd")
    assert r["status"] == "attention" and not r["uen_match"]
    assert any("differs" in f for f in r["flags"]) and any("signed" in f for f in r["flags"])


def test_refusal_is_unavailable_not_error(fake_ai):
    _, state = fake_ai
    state["resp"] = _response({}, stop_reason="refusal")
    assert ai.review_contract(PDF, UEN, "Acme")["status"] == "unavailable"


def test_disabled_by_default():
    assert ai.review_contract(PDF, UEN, "Acme")["status"] == "disabled"


def test_contract_upload_triggers_review(client, fake_ai):
    _, state = fake_ai
    state["resp"] = _response(GOOD)
    sid = client.post("/api/signups", json={"uen": UEN, "company_name": "Acme Pte Ltd"}).json()["id"]
    r = client.post(f"/api/signups/{sid}/contract", files={"file": ("c.pdf", PDF, "application/pdf")})
    assert r.json()["ai_review"] is True
    review = client.get(f"/api/signups/{sid}").json()["contract_ai_review"]
    assert review["status"] == "ok"
