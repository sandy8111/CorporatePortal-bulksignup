from datetime import date, timedelta

from tests.conftest import ADMIN, mails

UEN = "197401143C"
PDF = b"%PDF-1.4\n%test contract\n"
AO = {"full_name": "Tan Mei Ling", "id_type": "NRIC", "id_number": "S1234567D",
      "email": "meiling@acme.sg", "mobile": "91234567", "designation": "IT Manager",
      "merchant_code": "corp-direct-01"}


def iccid(i): return f"896501{i:013d}"  # 19 digits


def load_inventory(client, category="ACME", esim=3, physical=2, numbers=5):
    sims = "iccid,sim_type,category\n" + "".join(
        f"{iccid(i)},{'ESIM' if i < esim else 'PHYSICAL'},{category}\n" for i in range(esim + physical))
    r = client.post("/api/admin/inventory/sims", headers=ADMIN, files={"file": ("s.csv", sims)})
    assert r.status_code == 200, r.text
    nums = "msisdn,category\n" + "".join(f"{90000000 + i},{category}\n" for i in range(numbers))
    r = client.post("/api/admin/inventory/numbers", headers=ADMIN, files={"file": ("n.csv", nums)})
    assert r.status_code == 200, r.text
    return r.json()


def start(client, category="ACME"):
    r = client.post("/api/signups", json={"uen": UEN, "company_name": "Acme Pte Ltd",
                                          "company_address": "1 Raffles Place", "category": category})
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    r = client.post(f"/api/signups/{sid}/contract", files={"file": ("contract.pdf", PDF, "application/pdf")})
    assert r.status_code == 200, r.text
    r = client.put(f"/api/signups/{sid}/account-officer", json=AO)
    assert r.status_code == 200, r.text
    return sid


def test_full_signup_flow(client, s3):
    report = load_inventory(client)
    assert (report["accepted"], report["rejected_count"], report["paired"]) == (5, 0, 5)
    assert len(report["pairs"]) == 5 and all(p["iccid"] and p["msisdn"] for p in report["pairs"])

    sid = start(client)
    keys = [o["Key"] for o in s3.list_objects_v2(Bucket="corpone-test-contracts")["Contents"]]
    assert len(keys) == 1 and keys[0].startswith(f"contracts/{UEN}/{sid}/")

    assert client.get(f"/api/signups/{sid}/availability").json() == {"category": "ACME", "ESIM": 3, "PHYSICAL": 2}
    r = client.post(f"/api/signups/{sid}/cart", json={"plan_code": "FLASH-20", "sim_type": "ESIM", "quantity": 2})
    assert r.status_code == 200, r.text
    r = client.post(f"/api/signups/{sid}/cart", json={"plan_code": "GLARE-40", "sim_type": "PHYSICAL", "quantity": 1})
    cart = r.json()
    assert cart["line_count"] == 3 and cart["monthly_total"] == "80.00"
    assert all(l["msisdn"] for l in cart["lines"])

    # not enough stock
    r = client.post(f"/api/signups/{sid}/cart", json={"plan_code": "RAY-8", "sim_type": "ESIM", "quantity": 5})
    assert r.status_code == 409

    r = client.post(f"/api/signups/{sid}/submit")
    assert r.status_code == 200, r.text
    acct = r.json()["account_number"]

    a = client.get(f"/api/accounts/{acct}").json()
    assert a["order_status"] == "COMPLETED"
    assert a["billing_account_ref"].startswith("BA-")
    assert {l["status"] for l in a["lines"]} == {"PROVISIONED"}
    assert sum(l["esim_qr_sent"] for l in a["lines"]) == 2

    esim_mails = mails("eSIM activation")
    assert len(esim_mails) == 1
    msg = esim_mails[0]
    assert msg["To"] == AO["email"]
    pngs = [p for p in msg.walk() if p.get_content_type() == "image/png"]
    assert len(pngs) == 2 and pngs[0].get_payload(decode=True).startswith(b"\x89PNG")

    # cannot resubmit
    assert client.post(f"/api/signups/{sid}/submit").status_code == 409


def test_inventory_validation_rejects_bad_rows(client):
    sims = "iccid,sim_type,category\n123,ESIM,ACME\n" + f"{iccid(1)},ESIM,ACME\n{iccid(1)},ESIM,ACME\n{iccid(2)},FOO,ACME\n"
    r = client.post("/api/admin/inventory/sims", headers=ADMIN, files={"file": ("s.csv", sims)}).json()
    assert r["accepted"] == 1 and r["rejected_count"] == 3
    nums = "91234567\n71234567\n9123\n"
    r = client.post("/api/admin/inventory/numbers", headers=ADMIN, data={"category": "ACME"},
                    files={"file": ("n.csv", nums)}).json()
    assert r["accepted"] == 1 and r["rejected_count"] == 2 and r["paired"] == 1


def test_pairing_respects_category(client):
    load_inventory(client, category="ACME", esim=2, physical=0, numbers=0)
    nums = "msisdn,category\n81111111,OTHER\n"
    r = client.post("/api/admin/inventory/numbers", headers=ADMIN, files={"file": ("n.csv", nums)}).json()
    assert r["paired"] == 0
    nums = "msisdn,category\n82222222,ACME\n"
    assert client.post("/api/admin/inventory/numbers", headers=ADMIN,
                       files={"file": ("n.csv", nums)}).json()["paired"] == 1


def test_ao_sim_upload_locked_to_signup_category(client):
    sid = start(client)
    sims = f"iccid,sim_type,category\n{iccid(1)},ESIM,ACME\n{iccid(2)},ESIM,OTHERCO\n"
    r = client.post(f"/api/signups/{sid}/sims", files={"file": ("s.csv", sims)}).json()
    assert r["accepted"] == 1 and r["rejected_count"] == 1


def test_blacklisted_account_officer_rejected(client):
    today = date.today()
    r = client.post("/api/admin/blacklist", headers=ADMIN, json={
        "subject_type": "ACCOUNT_OFFICER", "id_type": "NRIC", "id_value": "s1234567d",
        "reason": "fraud", "start_date": str(today - timedelta(days=1)), "end_date": str(today + timedelta(days=30))})
    assert r.status_code == 201, r.text
    r = client.post("/api/signups", json={"uen": UEN, "company_name": "Acme", "category": "ACME"})
    sid = r.json()["id"]
    r = client.put(f"/api/signups/{sid}/account-officer", json=AO)
    assert r.status_code == 403


def test_expired_blacklist_entry_is_ignored(client):
    today = date.today()
    client.post("/api/admin/blacklist", headers=ADMIN, json={
        "subject_type": "ACCOUNT_OFFICER", "id_type": "NRIC", "id_value": "S1234567D",
        "start_date": str(today - timedelta(days=60)), "end_date": str(today - timedelta(days=1))})
    client.post("/api/admin/blacklist", headers=ADMIN, json={
        "subject_type": "ACCOUNT_OFFICER", "id_type": "NRIC", "id_value": "S1234567D",
        "start_date": str(today + timedelta(days=5))})
    start(client)  # neither entry is in force today


def test_blacklisted_company_rejected(client):
    client.post("/api/admin/blacklist", headers=ADMIN, json={
        "subject_type": "COMPANY", "id_type": "UEN", "id_value": UEN, "start_date": str(date.today())})
    r = client.post("/api/signups", json={"uen": UEN, "company_name": "Acme", "category": "ACME"})
    assert r.status_code == 403


def test_blacklist_added_after_draft_blocks_submit(client):
    load_inventory(client)
    sid = start(client)
    client.post(f"/api/signups/{sid}/cart", json={"plan_code": "RAY-8", "sim_type": "ESIM", "quantity": 1})
    client.post("/api/admin/blacklist", headers=ADMIN, json={
        "subject_type": "ACCOUNT_OFFICER", "id_type": "NRIC", "id_value": "S1234567D", "start_date": str(date.today())})
    assert client.post(f"/api/signups/{sid}/submit").status_code == 422


def test_invalid_uen_and_merchant_and_contract(client):
    r = client.post("/api/signups", json={"uen": "201912345A", "company_name": "X", "category": "ACME"})
    assert r.status_code == 422
    sid = client.post("/api/signups", json={"uen": UEN, "company_name": "X", "category": "ACME"}).json()["id"]
    r = client.post(f"/api/signups/{sid}/contract", files={"file": ("c.pdf", b"not a pdf", "application/pdf")})
    assert r.status_code == 400
    r = client.put(f"/api/signups/{sid}/account-officer", json={**AO, "merchant_code": "NOPE"})
    assert r.status_code == 400


def test_cart_remove_releases_inventory(client):
    load_inventory(client)
    sid = start(client)
    cart = client.post(f"/api/signups/{sid}/cart", json={"plan_code": "RAY-8", "sim_type": "ESIM", "quantity": 3}).json()
    assert client.get(f"/api/signups/{sid}/availability").json()["ESIM"] == 0
    client.delete(f"/api/signups/{sid}/cart/{cart['lines'][0]['id']}")
    assert client.get(f"/api/signups/{sid}/availability").json()["ESIM"] == 1


def test_provisioning_failure_and_retry(client, monkeypatch):
    from app.services import telco_api
    load_inventory(client)
    sid = start(client)
    client.post(f"/api/signups/{sid}/cart", json={"plan_code": "RAY-8", "sim_type": "ESIM", "quantity": 2})

    from app.gateway.outbound import GatewayError
    calls = {"n": 0}
    real = telco_api.MockNetworkApi.activate_line

    def flaky(self, **kw):
        calls["n"] += 1
        if calls["n"] <= 3:  # first line fails on all 3 gateway attempts (1 + 2 retries)
            raise GatewayError("HLR timeout", retryable=True)
        return real(self, **kw)

    monkeypatch.setattr(telco_api.MockNetworkApi, "activate_line", flaky)
    acct = client.post(f"/api/signups/{sid}/submit").json()["account_number"]
    a = client.get(f"/api/accounts/{acct}").json()
    assert a["order_status"] == "PARTIALLY_FAILED"
    assert sorted(l["status"] for l in a["lines"]) == ["FAILED", "PROVISIONED"]
    assert len(mails("eSIM activation")) == 1  # QR only for the provisioned line

    r = client.post(f"/api/admin/accounts/{acct}/retry-provisioning", headers=ADMIN)
    assert set(r.json()["lines"].values()) == {"PROVISIONED"}
    a = client.get(f"/api/accounts/{acct}").json()
    assert a["order_status"] == "COMPLETED" and all(l["esim_qr_sent"] for l in a["lines"])
    assert len(mails("eSIM activation")) == 2


def test_admin_requires_token(client):
    assert client.get("/api/admin/blacklist").status_code == 401


def test_served_with_and_without_port_proxy_prefix(client):
    for prefix in ("", "/ports/8077"):
        r = client.get(prefix + "/")
        assert r.status_code == 200 and 'src="static/app.js"' in r.text
        assert client.get(prefix + "/static/app.js").status_code == 200
        assert client.get(prefix + "/api/plans").status_code == 200
        assert client.get(prefix + "/admin").status_code == 200


def test_sim_list_pairing_and_cart_details(client):
    load_inventory(client, esim=3, physical=1, numbers=3)  # 4 SIMs, 3 numbers -> 1 SIM waits
    sid = start(client)

    lst = client.get(f"/api/signups/{sid}/sims").json()
    assert lst["total"] == 4 and lst["counts"] == {"AVAILABLE": 1, "PAIRED": 3, "MINE": 0}
    paired = [x for x in lst["pairs"] if x["sim_status"] == "PAIRED"]
    assert all(x["msisdn"] and x["number_status"] == "PAIRED" for x in paired)
    waiting = [x for x in lst["pairs"] if x["sim_status"] == "AVAILABLE"]
    assert waiting[0]["msisdn"] is None and waiting[0]["number_status"] is None

    # re-uploading existing SIMs says whether and with which number they are paired
    sims = f"iccid,sim_type\n{paired[0]['iccid']},ESIM\n{waiting[0]['iccid']},PHYSICAL\n{iccid(99)},ESIM\n"
    r = client.post(f"/api/signups/{sid}/sims", files={"file": ("s.csv", sims)}).json()
    assert r["accepted"] == 1 and r["items"][0] == {"iccid": iccid(99), "sim_type": "ESIM",
                                                    "category": "ACME", "msisdn": None}
    errs = {x["value"]: x for x in r["rejected"]}
    assert f"paired with {paired[0]['msisdn']}" in errs[paired[0]["iccid"]]["error"]
    assert errs[paired[0]["iccid"]]["existing"]["msisdn"] == paired[0]["msisdn"]
    assert "not paired yet" in errs[waiting[0]["iccid"]]["error"]

    # a new number pairs with the oldest waiting SIM and the report says which
    r = client.post("/api/admin/inventory/numbers", headers=ADMIN, data={"category": "ACME"},
                    files={"file": ("n.csv", "88888888\n")}).json()
    assert r["pairs"] == [{"iccid": waiting[0]["iccid"], "msisdn": "88888888",
                           "sim_type": waiting[0]["sim_type"], "category": "ACME"}]

    # adding to cart returns exactly the lines added; list shows them as in my cart
    cart = client.post(f"/api/signups/{sid}/cart", json={"plan_code": "RAY-8", "sim_type": "ESIM", "quantity": 2}).json()
    assert len(cart["added"]) == 2 and cart["line_count"] == 2
    mine = client.get(f"/api/signups/{sid}/sims?status=MINE").json()
    assert {x["iccid"] for x in mine["pairs"]} == {l["iccid"] for l in cart["added"]}
    assert all(x["in_my_cart"] and x["sim_status"] == x["number_status"] == "RESERVED" for x in mine["pairs"])
    assert client.get(f"/api/signups/{sid}/sims?q={cart['added'][0]['msisdn']}").json()["total"] == 1


def test_ao_list_hides_other_carts_and_bad_params(client):
    load_inventory(client, esim=3, physical=0, numbers=3)
    other, mine = start(client), start(client)
    client.post(f"/api/signups/{other}/cart", json={"plan_code": "RAY-8", "sim_type": "ESIM", "quantity": 2})
    lst = client.get(f"/api/signups/{mine}/sims").json()
    assert lst["total"] == 1 and set(lst["counts"]) == {"AVAILABLE", "PAIRED", "MINE"}
    assert all(x["sim_status"] == "PAIRED" for x in lst["pairs"])
    for bad in ("status=RESERVED", "status=ASSIGNED", "limit=-1", "limit=0", "limit=501", "offset=-1"):
        assert client.get(f"/api/signups/{mine}/sims?{bad}").status_code in (400, 422), bad
    # LIKE wildcards are literal, not "match everything"
    assert client.get(f"/api/signups/{mine}/sims?q=_").json()["total"] == 0
    assert client.get(f"/api/signups/{mine}/sims?q=%25").json()["total"] == 0


def test_admin_pairing_list_shows_both_sides(client):
    load_inventory(client, esim=2, physical=0, numbers=4)  # 2 pairs + 2 numbers waiting for a SIM
    d = client.get("/api/admin/inventory/pairings?category=ACME", headers=ADMIN).json()
    assert d["counts"]["PAIRED"] == 2 and d["counts"]["NUMBERS_WAITING"] == 2
    assert all(p["iccid"] and p["msisdn"] and p["sim_status"] == p["number_status"] == "PAIRED"
               and not p["status_mismatch"] for p in d["pairs"])
    w = client.get("/api/admin/inventory/pairings?category=ACME&status=NUMBERS_WAITING", headers=ADMIN).json()
    assert w["total"] == 2 and all(p["iccid"] is None and p["number_status"] == "AVAILABLE" for p in w["pairs"])
    assert client.get("/api/admin/inventory/pairings?category=ACME&status=MINE", headers=ADMIN).status_code == 400
    assert client.get("/api/admin/inventory/pairings?category=ACME").status_code == 401


def test_other_category_sim_not_revealed(client):
    load_inventory(client, category="OTHERCO", esim=1, physical=0, numbers=1)
    sid = start(client)  # category ACME
    r = client.post(f"/api/signups/{sid}/sims", files={"file": ("s.csv", f"{iccid(0)}\n")}).json()
    assert r["rejected"][0]["error"] == "Already uploaded under another category"
    assert r["rejected"][0]["existing"] is None
    # naming the other category in the row is refused before anything about the SIM is looked at
    r = client.post(f"/api/signups/{sid}/sims", files={"file": ("s.csv", f"iccid,category\n{iccid(0)},OTHERCO\n")}).json()
    assert r["rejected"][0]["error"] == "Category must be ACME for this sign-up" and "existing" not in r["rejected"][0]
    # an invalid category in the row does not reveal details either (admin upload, no default)
    r = client.post("/api/admin/inventory/sims", headers=ADMIN,
                    files={"file": ("s.csv", f"iccid,sim_type,category\n{iccid(0)},ESIM,bad!\n")}).json()
    assert "existing" not in r["rejected"][0] and "Category" in r["rejected"][0]["error"]


def test_request_log_line_and_request_id(client, caplog):
    import logging
    with caplog.at_level(logging.INFO, logger="corpone.api"):
        r = client.get("/api/signups")
        client.get("/api/admin/inventory/summary", headers=ADMIN)
        client.get("/api/admin/inventory/summary")
        client.get("/static/app.js")
        client.post("/api/auth/otp/verify", json={"email": "x@acme.sg", "code": "123456"})
        echoed = client.get("/healthz", headers={"X-Request-ID": "abc-123"})
    rid = r.headers["x-request-id"]
    lines = [rec.getMessage() for rec in caplog.records if rec.name == "corpone.api"]
    assert any(l.startswith("GET /api/signups -> 200") and f"by=meiling@acme.sg id={rid}" in l for l in lines)
    assert any("/api/admin/inventory/summary -> 200" in l and "by=admin" in l for l in lines)
    assert any("/api/admin/inventory/summary -> 401" in l and "by=-" in l for l in lines)
    assert not any("/static/" in l or "/healthz" in l for l in lines)
    assert not any("123456" in l for l in lines)  # bodies are never logged
    assert "x-request-id" not in echoed.headers  # health checks are skipped entirely
