from datetime import datetime, timedelta, timezone

from app.db import SessionLocal
from app.models import OtpChallenge
from tests.conftest import AO_EMAIL, last_otp, login, mails

UEN = "197401143C"


def test_wizard_requires_login(anon):
    assert anon.post("/api/signups", json={"uen": UEN, "company_name": "A", "category": "ACME"}).status_code == 401
    assert anon.get("/api/plans").status_code == 200  # plans are public


def test_otp_email_and_login(client):
    m = mails("sign-in code", AO_EMAIL)[-1]
    assert "expires in 5 minutes" in m.get_body(("plain",)).get_content()
    assert client.get("/api/auth/me").json() == {"email": AO_EMAIL}
    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").status_code == 401


def test_bearer_token_works(anon):
    token = login(anon, "ops@acme.sg")
    anon.cookies.clear()
    assert anon.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["email"] == "ops@acme.sg"


def test_wrong_code_attempts_exhaust(anon):
    anon.post("/api/auth/otp/request", json={"email": "x@acme.sg"})
    good = last_otp("x@acme.sg")
    bad = "000000" if good != "000000" else "111111"
    for i in range(5):
        r = anon.post("/api/auth/otp/verify", json={"email": "x@acme.sg", "code": bad})
        assert r.status_code == 401
    assert "Too many" in r.json()["detail"]
    # even the right code no longer works
    assert anon.post("/api/auth/otp/verify", json={"email": "x@acme.sg", "code": good}).status_code == 401


def test_code_is_single_use_and_expires(anon):
    anon.post("/api/auth/otp/request", json={"email": "y@acme.sg"})
    code = last_otp("y@acme.sg")
    assert anon.post("/api/auth/otp/verify", json={"email": "y@acme.sg", "code": code}).status_code == 200
    assert anon.post("/api/auth/otp/verify", json={"email": "y@acme.sg", "code": code}).status_code == 401

    with SessionLocal() as db:  # make the next code look old
        db.query(OtpChallenge).delete()
        db.commit()
    anon.post("/api/auth/otp/request", json={"email": "z@acme.sg"})
    with SessionLocal() as db:
        db.query(OtpChallenge).update({"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)})
        db.commit()
    r = anon.post("/api/auth/otp/verify", json={"email": "z@acme.sg", "code": last_otp("z@acme.sg")})
    assert r.status_code == 401 and "expired" in r.json()["detail"]


def test_resend_cooldown(anon):
    assert anon.post("/api/auth/otp/request", json={"email": "c@acme.sg"}).status_code == 202
    r = anon.post("/api/auth/otp/request", json={"email": "c@acme.sg"})
    assert r.status_code == 429 and "Retry-After" in r.headers


def test_signups_are_private_to_their_owner(client, anon):
    sid = client.post("/api/signups", json={"uen": UEN, "company_name": "A", "category": "ACME"}).json()["id"]
    login(anon, "intruder@evil.sg")
    assert anon.get(f"/api/signups/{sid}").status_code == 404
    assert anon.post(f"/api/signups/{sid}/submit").status_code == 404
    assert [s["id"] for s in client.get("/api/signups").json()] == [sid]


def test_ao_email_must_match_login(client):
    sid = client.post("/api/signups", json={"uen": UEN, "company_name": "A", "category": "ACME"}).json()["id"]
    ao = {"full_name": "Tan", "id_type": "NRIC", "id_number": "S1234567D", "mobile": "91234567",
          "merchant_code": "CORP-DIRECT-01"}
    assert client.put(f"/api/signups/{sid}/account-officer", json={**ao, "email": "other@x.sg"}).status_code == 400
    r = client.put(f"/api/signups/{sid}/account-officer", json=ao)
    assert r.status_code == 200 and r.json()["account_officer"]["email"] == AO_EMAIL


def test_fixed_test_otp(anon, monkeypatch):
    from app.config import get_settings
    assert anon.get("/api/auth/options").json() == {"test_mode": False}
    monkeypatch.setattr(get_settings(), "dev_fixed_otp", "123456")
    assert anon.get("/api/auth/options").json() == {"test_mode": True, "test_code": "123456"}
    anon.post("/api/auth/otp/request", json={"email": "tester@acme.sg"})
    assert anon.post("/api/auth/otp/verify", json={"email": "tester@acme.sg", "code": "654321"}).status_code == 401
    r = anon.post("/api/auth/otp/verify", json={"email": "tester@acme.sg", "code": "123456"})
    assert r.status_code == 200 and anon.get("/api/auth/me").json()["email"] == "tester@acme.sg"
