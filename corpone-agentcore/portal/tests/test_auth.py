from datetime import datetime, timedelta, timezone

from app.db import SessionLocal
from app.models import OtpChallenge
from tests.conftest import AO_EMAIL, last_otp, login, mails

UEN = "197401143C"


def test_wizard_requires_login(anon):
    assert anon.post("/api/signups", json={"uen": UEN, "company_name": "A"}).status_code == 401
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
    sid = client.post("/api/signups", json={"uen": UEN, "company_name": "A"}).json()["id"]
    login(anon, "intruder@evil.sg")
    assert anon.get(f"/api/signups/{sid}").status_code == 404
    assert anon.post(f"/api/signups/{sid}/submit").status_code == 404
    assert [s["id"] for s in client.get("/api/signups").json()] == [sid]


def test_ao_email_must_match_login(client):
    sid = client.post("/api/signups", json={"uen": UEN, "company_name": "A"}).json()["id"]
    ao = {"full_name": "Tan", "id_type": "NRIC", "id_number": "S1234567D", "mobile": "91234567",
          "merchant_code": "CORP-DIRECT-01"}
    assert client.put(f"/api/signups/{sid}/account-officer", json={**ao, "email": "other@x.sg"}).status_code == 400
    r = client.put(f"/api/signups/{sid}/account-officer", json=ao)
    assert r.status_code == 200 and r.json()["account_officer"]["email"] == AO_EMAIL


def test_fixed_test_otp_only_for_test_account(anon, monkeypatch):
    from app.config import get_settings
    assert anon.get("/api/auth/options").json() == {"test_mode": False}
    monkeypatch.setattr(get_settings(), "dev_fixed_otp", "123456")
    # the code alone does nothing: the test account must be named explicitly
    assert anon.get("/api/auth/options").json() == {"test_mode": False}
    monkeypatch.setattr(get_settings(), "dev_fixed_otp_email", "Admin@Corp1.com.sg")
    # the fixed code is never sent to the browser
    assert anon.get("/api/auth/options").json() == {"test_mode": True, "test_email": "admin@corp1.com.sg"}

    # the test account (any letter case) signs in with the fixed code
    anon.post("/api/auth/otp/request", json={"email": "Admin@Corp1.com.sg"})
    assert last_otp("admin@corp1.com.sg") == "123456"
    assert anon.post("/api/auth/otp/verify", json={"email": "admin@corp1.com.sg", "code": "654321"}).status_code == 401
    r = anon.post("/api/auth/otp/verify", json={"email": "admin@corp1.com.sg", "code": "123456"})
    assert r.status_code == 200 and anon.get("/api/auth/me").json()["email"] == "admin@corp1.com.sg"

    # any other email still gets a random code, so 123456 does not work there
    monkeypatch.setattr("app.auth._generate_code", lambda email: "777777" if email != "admin@corp1.com.sg" else "123456")
    anon.post("/api/auth/otp/request", json={"email": "tester@acme.sg"})
    assert last_otp("tester@acme.sg") == "777777"
    assert anon.post("/api/auth/otp/verify", json={"email": "tester@acme.sg", "code": "123456"}).status_code == 401


def test_fixed_code_not_used_for_other_emails(monkeypatch):
    from app import auth
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "dev_fixed_otp", "123456")
    assert len({auth._generate_code("admin@corp1.com.sg") for _ in range(20)}) > 1  # no email set: random
    monkeypatch.setattr(get_settings(), "dev_fixed_otp_email", "admin@corp1.com.sg")
    assert auth._generate_code("admin@corp1.com.sg") == "123456"
    others = {auth._generate_code("someone@acme.sg") for _ in range(20)}
    assert others != {"123456"} and all(len(c) == 6 and c.isdigit() for c in others)
