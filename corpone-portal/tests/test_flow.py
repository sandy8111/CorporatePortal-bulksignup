import re
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


def allocate(client, category="ACME", uen=UEN, active=True):
    r = client.put("/api/admin/categories", headers=ADMIN, json={"category": category, "uen": uen, "active": active})
    assert r.status_code == 200, r.text
    return r.json()


def start(client, category="ACME"):
    """Company, contract and AO steps, then pick the SIM category (allocated to the company first)."""
    allocate(client, category)
    r = client.post("/api/signups", json={"uen": UEN, "company_name": "Acme Pte Ltd",
                                          "company_address": "1 Raffles Place"})
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    r = client.post(f"/api/signups/{sid}/contract", files={"file": ("contract.pdf", PDF, "application/pdf")})
    assert r.status_code == 200, r.text
    r = client.put(f"/api/signups/{sid}/account-officer", json=AO)
    assert r.status_code == 200, r.text
    r = client.put(f"/api/signups/{sid}/category", json={"category": category})
    assert r.status_code == 200, r.text
    return sid


def test_full_signup_flow(client, s3):
    report = load_inventory(client)
    assert (report["accepted"], report["rejected_count"], report["paired"]) == (5, 0, 5)
    assert len(report["pairs"]) == 5 and all(p["iccid"] and p["msisdn"] for p in report["pairs"])

    sid = start(client)
    keys = [o["Key"] for o in s3.list_objects_v2(Bucket="corpone-test-contracts")["Contents"]]
    assert len(keys) == 1 and keys[0].startswith(f"contracts/{UEN}/{sid}/")

    assert client.get(f"/api/signups/{sid}/availability").json() == {"category": "ACME", "ESIM": 3, "PHYSICAL": 2,
                                                                      "numbers_available": 0, "sims_waiting": 0}
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
    r = client.post("/api/signups", json={"uen": UEN, "company_name": "Acme"})
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
    r = client.post("/api/signups", json={"uen": UEN, "company_name": "Acme"})
    assert r.status_code == 403


def test_blacklist_added_after_draft_blocks_submit(client):
    load_inventory(client)
    sid = start(client)
    client.post(f"/api/signups/{sid}/cart", json={"plan_code": "RAY-8", "sim_type": "ESIM", "quantity": 1})
    client.post("/api/admin/blacklist", headers=ADMIN, json={
        "subject_type": "ACCOUNT_OFFICER", "id_type": "NRIC", "id_value": "S1234567D", "start_date": str(date.today())})
    assert client.post(f"/api/signups/{sid}/submit").status_code == 422


def test_invalid_uen_and_merchant_and_contract(client):
    r = client.post("/api/signups", json={"uen": "201912345A", "company_name": "X"})
    assert r.status_code == 422
    sid = client.post("/api/signups", json={"uen": UEN, "company_name": "X"}).json()["id"]
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
        script = re.search(r'src="(static/v/[0-9a-f]{12}/app\.js)"', r.text)  # relative, so the prefix is kept
        assert r.status_code == 200 and script
        assert client.get(f"{prefix}/{script[1]}").status_code == 200
        assert client.get(prefix + "/static/app.js").status_code == 200
        assert client.get(prefix + "/api/plans").status_code == 200
        assert client.get(prefix + "/admin").status_code == 200


def test_sim_list_pairing_and_cart_details(client):
    load_inventory(client, esim=3, physical=1, numbers=3)  # 4 SIMs, 3 numbers -> 1 SIM waits
    sid = start(client)

    lst = client.get(f"/api/signups/{sid}/sims").json()
    assert lst["total"] == 4 and lst["counts"] == {"AVAILABLE": 1, "PAIRED": 3, "MINE": 0, "NUMBERS_WAITING": 0}
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
    assert lst["total"] == 1 and set(lst["counts"]) == {"AVAILABLE", "PAIRED", "MINE", "NUMBERS_WAITING"}
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


def test_category_is_chosen_from_company_allocations(client):
    load_inventory(client, category="ACME-SIM-001", esim=2, physical=1, numbers=3)
    allocate(client, "ACME-SIM-001")
    allocate(client, "ACME-SIM-002")
    allocate(client, "OTHERCO-SIM-001", uen="53312345C")  # another company's category
    sid = client.post("/api/signups", json={"uen": UEN, "company_name": "Acme"}).json()["id"]
    assert client.get(f"/api/signups/{sid}").json()["category"] is None

    # nothing that touches inventory works until a category is chosen
    assert client.get(f"/api/signups/{sid}/availability").status_code == 409
    assert client.get(f"/api/signups/{sid}/sims").status_code == 409
    assert client.post(f"/api/signups/{sid}/sims", files={"file": ("s.csv", f"{iccid(50)},ESIM\n")}).status_code == 409
    assert client.post(f"/api/signups/{sid}/cart", json={"plan_code": "RAY-8", "sim_type": "ESIM", "quantity": 1}).status_code == 409

    d = client.get(f"/api/signups/{sid}/categories").json()
    assert [c["category"] for c in d["categories"]] == ["ACME-SIM-001", "ACME-SIM-002"]
    assert d["categories"][0] == {"category": "ACME-SIM-001", "uen": UEN, "scope": "COMPANY", "description": "",
                                  "active": True, "allocated": True, "ESIM": 2, "PHYSICAL": 1}
    assert client.put(f"/api/signups/{sid}/category", json={"category": "OTHERCO-SIM-001"}).status_code == 403
    assert client.put(f"/api/signups/{sid}/category", json={"category": "NOT-ALLOCATED"}).status_code == 403

    assert client.put(f"/api/signups/{sid}/category", json={"category": "acme-sim-001"}).json()["category"] == "ACME-SIM-001"
    assert client.get(f"/api/signups/{sid}/availability").json() == {"category": "ACME-SIM-001", "ESIM": 2, "PHYSICAL": 1,
                                                                      "numbers_available": 0, "sims_waiting": 0}
    r = client.post(f"/api/signups/{sid}/sims", data={"sim_type": "ESIM"}, files={"file": ("s.csv", f"{iccid(50)}\n")}).json()
    assert r["items"][0]["category"] == "ACME-SIM-001"

    # can't switch category while the cart has lines from the current one
    client.post(f"/api/signups/{sid}/cart", json={"plan_code": "RAY-8", "sim_type": "ESIM", "quantity": 1})
    assert client.put(f"/api/signups/{sid}/category", json={"category": "ACME-SIM-002"}).status_code == 409

    # back office withdraws the allocation: the draft can't take more lines or submit
    allocate(client, "ACME-SIM-001", active=False)
    assert client.get(f"/api/signups/{sid}/categories").json()["categories"][0]["category"] == "ACME-SIM-002"
    r = client.post(f"/api/signups/{sid}/cart", json={"plan_code": "RAY-8", "sim_type": "ESIM", "quantity": 1})
    assert r.status_code == 409 and "no longer available" in r.json()["detail"]
    assert client.post(f"/api/signups/{sid}/submit").status_code == 409


def test_admin_category_allocations(client):
    assert client.get("/api/admin/categories").status_code == 401
    assert client.put("/api/admin/categories", headers=ADMIN, json={"category": "X", "uen": "2026TC809K"}).status_code == 422
    allocate(client, "ACME-SIM-001")
    moved = client.put("/api/admin/categories", headers=ADMIN,
                       json={"category": "ACME-SIM-001", "uen": "53312345C", "description": "moved"}).json()
    assert moved["uen"] == "53312345C" and moved["description"] == "moved"
    rows = client.get("/api/admin/categories", headers=ADMIN).json()
    assert [(r["category"], r["uen"]) for r in rows] == [("ACME-SIM-001", "53312345C")]
    assert client.get(f"/api/admin/categories?uen={UEN}", headers=ADMIN).json() == []


def test_company_details_editable_after_validation(client, s3):
    load_inventory(client)
    sid = start(client)  # contract uploaded, category ACME (allocated to UEN) chosen
    base = {"uen": UEN, "company_name": "Acme Holdings Pte Ltd", "company_address": "2 Shenton Way"}

    # name/address only: contract and category are kept
    r = client.put(f"/api/signups/{sid}/company", json=base).json()
    assert (r["company_name"], r["company_address"], r["uen_changed"]) == ("Acme Holdings Pte Ltd", "2 Shenton Way", False)
    assert r["contract_uploaded"] and r["category"] == "ACME"

    # invalid or blacklisted UEN is refused and nothing changes
    assert client.put(f"/api/signups/{sid}/company", json={**base, "uen": "2026TC809K"}).status_code == 422
    client.post("/api/admin/blacklist", headers=ADMIN, json={
        "subject_type": "COMPANY", "id_type": "UEN", "id_value": "53312345C", "start_date": str(date.today())})
    assert client.put(f"/api/signups/{sid}/company", json={**base, "uen": "53312345C"}).status_code == 403

    # UEN can't change while the cart holds the old company's lines
    line = client.post(f"/api/signups/{sid}/cart", json={"plan_code": "RAY-8", "sim_type": "ESIM", "quantity": 1}).json()["lines"][0]
    assert client.put(f"/api/signups/{sid}/company", json={**base, "uen": "201912345R"}).status_code == 409
    client.delete(f"/api/signups/{sid}/cart/{line['id']}")

    # new UEN: contract must be uploaded again and the old company's category is cleared
    r = client.put(f"/api/signups/{sid}/company", json={**base, "uen": "201912345R"}).json()
    assert r["uen"] == "201912345R" and r["uen_changed"]
    assert not r["contract_uploaded"] and r["category"] is None
    assert client.get(f"/api/signups/{sid}").json()["uen"] == "201912345R"

    # submitted sign-ups can't be edited
    sid2 = start(client)
    client.post(f"/api/signups/{sid2}/cart", json={"plan_code": "RAY-8", "sim_type": "ESIM", "quantity": 1})
    assert client.post(f"/api/signups/{sid2}/submit").status_code == 200
    assert client.put(f"/api/signups/{sid2}/company", json=base).status_code == 409


def test_merchant_dropdown_lists_active_codes(client, anon):
    client.put("/api/admin/merchants", headers=ADMIN, json={"code": "corp-dealer-02", "name": "Dealer Two"})
    client.put("/api/admin/merchants", headers=ADMIN, json={"code": "OLD-01", "name": "Old", "active": False})
    assert client.get("/api/merchants").json() == [
        {"code": "CORP-DEALER-02", "name": "Dealer Two"},
        {"code": "CORP-DIRECT-01", "name": "CorpOne Enterprise Direct Sales"}]
    assert anon.get("/api/merchants").status_code == 401


def test_numbers_available_before_pairing(client):
    load_inventory(client, esim=2, physical=0, numbers=4)  # 2 pairs, 2 numbers free
    sid = start(client)
    a = client.get(f"/api/signups/{sid}/availability").json()
    assert (a["numbers_available"], a["sims_waiting"], a["ESIM"]) == (2, 0, 2)
    free = client.get(f"/api/signups/{sid}/sims?status=NUMBERS_WAITING").json()
    assert free["total"] == 2 and all(p["iccid"] is None and p["number_status"] == "AVAILABLE" for p in free["pairs"])

    # uploading SIMs uses up the free numbers; extra SIMs wait for a number
    sims = "".join(f"{iccid(i)}\n" for i in range(10, 13))
    r = client.post(f"/api/signups/{sid}/sims", data={"sim_type": "ESIM"}, files={"file": ("s.csv", sims)}).json()
    assert (r["accepted"], r["paired"]) == (3, 2)
    a = client.get(f"/api/signups/{sid}/availability").json()
    assert (a["numbers_available"], a["sims_waiting"], a["ESIM"]) == (0, 1, 4)


def test_pages_link_fingerprinted_assets_and_are_not_cached(client):
    for page, script in (("/", "app.js"), ("/admin", "admin.js"), ("/ports/8501/", "app.js")):
        r = client.get(page)
        assert r.headers["cache-control"] == "no-store"
        links = re.findall(r'(?:src|href)="(static/[^"]+)"', r.text)
        assert any(re.fullmatch(rf"static/v/[0-9a-f]{{12}}/{script}", l) for l in links), links
        assert any(re.fullmatch(r"static/v/[0-9a-f]{12}/styles\.css", l) for l in links), links
        for link in links:
            a = client.get("/" + link)
            assert a.status_code == 200 and "immutable" in a.headers["cache-control"]
    assert client.get("/static/v/abc/app.js").headers["cache-control"] == "no-store"  # old URL: current file, uncached
    assert client.get("/static/v/abc/../main.py").status_code == 404
    assert client.get("/static/v/abc/secret.txt").status_code == 404
    assert client.get("/static/app.js").headers["cache-control"] == "no-cache"


def test_category_for_all_companies(client):
    load_inventory(client, category="POOL-OPEN", esim=1, physical=0, numbers=1)    # never allocated
    load_inventory(client, category="POOL-SHARED", esim=0, physical=0, numbers=0)
    allocate(client, "POOL-SHARED", uen=None)                                      # explicitly for all
    client.put("/api/admin/categories", headers=ADMIN, json={"category": "POOL-SHARED", "uen": "", "description": "Shared pool"})
    allocate(client, "ACME-SIM-001")                                               # Acme only
    allocate(client, "OTHER-SIM-001", uen="53312345C")                             # another company only
    allocate(client, "POOL-OFF", uen=None, active=False)                           # hidden from everyone

    sid = client.post("/api/signups", json={"uen": UEN, "company_name": "Acme"}).json()["id"]
    d = client.get(f"/api/signups/{sid}/categories").json()["categories"]
    assert [(c["category"], c["scope"]) for c in d] == [
        ("ACME-SIM-001", "COMPANY"), ("POOL-OPEN", "ALL"), ("POOL-SHARED", "ALL")]
    assert next(c for c in d if c["category"] == "POOL-SHARED")["description"] == "Shared pool"
    for cat, code in (("POOL-OPEN", 200), ("POOL-SHARED", 200), ("OTHER-SIM-001", 403), ("POOL-OFF", 403), ("NOPE", 403)):
        assert client.put(f"/api/signups/{sid}/category", json={"category": cat}).status_code == code, cat

    # an open category works end to end: upload, availability, cart
    client.put(f"/api/signups/{sid}/category", json={"category": "POOL-OPEN"})
    assert client.get(f"/api/signups/{sid}/availability").json()["ESIM"] == 1
    assert client.post(f"/api/signups/{sid}/cart", json={"plan_code": "RAY-8", "sim_type": "ESIM", "quantity": 1}).status_code == 200

    # allocating an open category to a company takes it away from everyone else
    allocate(client, "POOL-OPEN", uen="53312345C")
    assert client.post(f"/api/signups/{sid}/cart", json={"plan_code": "RAY-8", "sim_type": "ESIM", "quantity": 1}).status_code == 409

    # back office sees every category, including ones never allocated
    rows = {r["category"]: r for r in client.get("/api/admin/categories", headers=ADMIN).json()}
    assert rows["POOL-SHARED"]["scope"] == "ALL" and rows["POOL-SHARED"]["allocated"]
    assert rows["POOL-OFF"]["active"] is False and rows["OTHER-SIM-001"]["uen"] == "53312345C"
