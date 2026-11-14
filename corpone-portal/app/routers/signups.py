"""Account-officer facing sign-up wizard API."""
import json
from decimal import Decimal

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import current_ao
from app.db import SessionLocal, get_db
from app.models import (
    AoSession, CartLine, CorporateAccount, IdType, InventoryStatus, Merchant, Plan, Signup, SignupStatus, Sim,
    SimType, SubjectType,
)
from app.schemas import AccountOfficerIn, CartAdd, CategoryChoice, SignupCreate
from app.services import ai, categories, inventory, orders, storage
from app.services.blacklist import active_entry
from app.validators import ValidationError

router = APIRouter(prefix="/api", tags=["sign-up"])


def _bad(msg: str, code: int = 400):
    raise HTTPException(status_code=code, detail=msg)


def _owned(db: Session, signup_id: str, ao: AoSession) -> Signup:
    s = db.get(Signup, signup_id)
    if not s or s.owner_email != ao.email:  # same 404 either way: don't reveal other AOs' sign-ups
        _bad("Sign-up not found", 404)
    return s


def _draft(db: Session, signup_id: str, ao: AoSession) -> Signup:
    s = _owned(db, signup_id, ao)
    if s.status is not SignupStatus.DRAFT:
        _bad("Sign-up has already been submitted", 409)
    return s


def _with_category(db: Session, s: Signup) -> str:
    """The sign-up's SIM category, which must still be allocated to its company."""
    if not s.category:
        _bad("Choose a SIM category first", 409)
    if not categories.is_allocated(db, s.uen, s.category):
        _bad(f"SIM category {s.category} is no longer allocated to UEN {s.uen}; choose another category", 409)
    return s.category


def _cart(s: Signup) -> dict:
    lines = [{
        "id": cl.id, "iccid": cl.sim.iccid, "msisdn": cl.number.msisdn,
        "sim_type": cl.sim.sim_type.value, "plan_code": cl.plan.code, "plan_name": cl.plan.name,
        "monthly_price": str(cl.plan.monthly_price),
    } for cl in s.cart_lines]
    by_plan: dict[str, dict] = {}
    for cl in s.cart_lines:
        b = by_plan.setdefault(cl.plan.code, {"plan_name": cl.plan.name, "unit_price": str(cl.plan.monthly_price),
                                              "ESIM": 0, "PHYSICAL": 0, "subtotal": Decimal("0")})
        b[cl.sim.sim_type.value] += 1
        b["subtotal"] += cl.plan.monthly_price
    for b in by_plan.values():
        b["subtotal"] = str(b["subtotal"])
    total = sum((cl.plan.monthly_price for cl in s.cart_lines), Decimal("0"))
    return {"lines": lines, "summary": list(by_plan.values()), "line_count": len(lines),
            "monthly_total": str(total)}


def _view(s: Signup, db: Session) -> dict:
    out = {
        "id": s.id, "status": s.status.value, "uen": s.uen, "company_name": s.company_name,
        "company_address": s.company_address, "category": s.category,
        "contract_uploaded": bool(s.contract_key), "contract_filename": s.contract_filename,
        "account_officer": None if not s.ao_id_number else {
            "full_name": s.ao_full_name, "id_type": s.ao_id_type.value,
            "id_number_masked": s.ao_id_number[0] + "****" + s.ao_id_number[-4:],
            "email": s.ao_email, "mobile": s.ao_mobile, "designation": s.ao_designation,
        },
        "merchant_code": s.merchant_code,
        "contract_ai_review": json.loads(s.contract_ai_review) if s.contract_ai_review else None,
        "cart": _cart(s),
    }
    if s.account_id:
        out["account_number"] = db.get(CorporateAccount, s.account_id).account_number
    return out


@router.get("/plans")
def list_plans(db: Session = Depends(get_db)):
    return [{"code": p.code, "name": p.name, "monthly_price": str(p.monthly_price),
             "data_gb": p.data_gb, "description": p.description}
            for p in db.scalars(select(Plan).where(Plan.active.is_(True)).order_by(Plan.monthly_price))]


@router.get("/merchants")
def active_merchants(_: AoSession = Depends(current_ao), db: Session = Depends(get_db)):
    """Active delegated merchant codes, for the account officer step's drop-down."""
    return [{"code": m.code, "name": m.name}
            for m in db.scalars(select(Merchant).where(Merchant.active.is_(True)).order_by(Merchant.code))]


@router.get("/merchants/{code}/validate")
def validate_merchant(code: str, _: AoSession = Depends(current_ao), db: Session = Depends(get_db)):
    m = orders.active_merchant(db, code)
    if not m:
        _bad("Merchant code is not valid or not active", 404)
    return {"code": m.code, "name": m.name}


@router.post("/signups", status_code=201)
def create_signup(body: SignupCreate, ao: AoSession = Depends(current_ao), db: Session = Depends(get_db)):
    if active_entry(db, SubjectType.COMPANY, IdType.UEN, body.uen):
        _bad(f"Company {body.uen} is blacklisted and cannot sign up", 403)
    s = Signup(uen=body.uen, company_name=body.company_name.strip(),
               company_address=body.company_address.strip(), owner_email=ao.email)
    db.add(s)
    db.commit()
    return _view(s, db)


@router.put("/signups/{signup_id}/company")
def update_company(signup_id: str, body: SignupCreate, ao: AoSession = Depends(current_ao),
                   db: Session = Depends(get_db)):
    """Edit UEN, company name and address of a draft. A new UEN is re-validated like a new sign-up."""
    s = _draft(db, signup_id, ao)
    uen_changed = body.uen != s.uen
    if uen_changed:
        if active_entry(db, SubjectType.COMPANY, IdType.UEN, body.uen):
            _bad(f"Company {body.uen} is blacklisted and cannot sign up", 403)
        if s.cart_lines:
            _bad("Remove the lines in your cart before changing the UEN: they come from the "
                 f"SIM category allocated to {s.uen}", 409)
        # A different UEN is a different company: its signed contract and SIM category don't carry over.
        s.contract_key = s.contract_filename = s.contract_ai_review = None
        if not categories.is_allocated(db, body.uen, s.category):
            s.category = None
    s.uen, s.company_name, s.company_address = body.uen, body.company_name.strip(), body.company_address.strip()
    db.commit()
    out = _view(s, db)
    out["uen_changed"] = uen_changed
    return out


@router.get("/signups")
def my_signups(ao: AoSession = Depends(current_ao), db: Session = Depends(get_db)):
    rows = db.scalars(select(Signup).where(Signup.owner_email == ao.email).order_by(Signup.created_at.desc())).all()
    return [{"id": s.id, "status": s.status.value, "uen": s.uen, "company_name": s.company_name,
             "line_count": len(s.cart_lines), "created_at": s.created_at.isoformat()} for s in rows]


@router.get("/signups/{signup_id}")
def get_signup(signup_id: str, ao: AoSession = Depends(current_ao), db: Session = Depends(get_db)):
    return _view(_owned(db, signup_id, ao), db)


def _review_contract_in_background(signup_id: str, pdf: bytes) -> None:
    db = SessionLocal()
    try:
        s = db.get(Signup, signup_id)
        result = ai.review_contract(pdf, s.uen, s.company_name)
        s = db.get(Signup, signup_id)
        s.contract_ai_review = json.dumps(result)
        db.commit()
    finally:
        db.close()


@router.post("/signups/{signup_id}/contract")
async def upload_contract(signup_id: str, background: BackgroundTasks, file: UploadFile = File(...),
                          ao: AoSession = Depends(current_ao), db: Session = Depends(get_db)):
    s = _draft(db, signup_id, ao)
    data = await file.read()
    try:
        key = storage.store_contract(s.uen, s.id, file.filename, file.content_type, data)
    except storage.ContractRejected as exc:
        _bad(str(exc))
    s.contract_key, s.contract_filename = key, file.filename
    s.contract_ai_review = json.dumps({"status": "pending"}) if ai.enabled() else None
    db.commit()
    if ai.enabled():
        background.add_task(_review_contract_in_background, s.id, data)
    return {"contract_uploaded": True, "contract_filename": file.filename, "ai_review": ai.enabled()}


@router.put("/signups/{signup_id}/account-officer")
def set_account_officer(signup_id: str, body: AccountOfficerIn, ao: AoSession = Depends(current_ao),
                        db: Session = Depends(get_db)):
    s = _draft(db, signup_id, ao)
    if body.email and str(body.email).lower() != ao.email:
        _bad("Account officer email must be the email you signed in with")
    if active_entry(db, SubjectType.ACCOUNT_OFFICER, body.id_type, body.id_number):
        _bad("Account officer is blacklisted and cannot be used for this sign-up", 403)
    if not orders.active_merchant(db, body.merchant_code):
        _bad("Merchant code is not valid or not active")
    s.ao_full_name, s.ao_id_type, s.ao_id_number = body.full_name.strip(), body.id_type, body.id_number
    s.ao_email, s.ao_mobile, s.ao_designation = ao.email, body.mobile, body.designation.strip()
    s.merchant_code = body.merchant_code
    db.commit()
    return _view(s, db)


@router.get("/signups/{signup_id}/categories")
def signup_categories(signup_id: str, ao: AoSession = Depends(current_ao), db: Session = Depends(get_db)):
    """SIM categories allocated to this sign-up's company, with paired lines ready per SIM type."""
    s = _owned(db, signup_id, ao)
    ready = categories.ready_counts(db)
    return {"uen": s.uen, "selected": s.category,
            "categories": [categories.view(a, ready) for a in categories.for_company(db, s.uen)]}


@router.put("/signups/{signup_id}/category")
def choose_category(signup_id: str, body: CategoryChoice, ao: AoSession = Depends(current_ao),
                    db: Session = Depends(get_db)):
    s = _draft(db, signup_id, ao)
    if not categories.is_allocated(db, s.uen, body.category):
        _bad(f"SIM category {body.category} is not allocated to UEN {s.uen}", 403)
    if s.cart_lines and body.category != s.category:
        _bad("Remove the lines in your cart before changing the SIM category", 409)
    s.category = body.category
    db.commit()
    return _view(s, db)


@router.post("/signups/{signup_id}/sims")
async def upload_signup_sims(signup_id: str, file: UploadFile = File(...),
                             sim_type: SimType | None = Form(None), ao: AoSession = Depends(current_ao),
                             db: Session = Depends(get_db)):
    """AO uploads SIMs for this company's category; they auto-pair with numbers of that category."""
    s = _draft(db, signup_id, ao)
    category = _with_category(db, s)
    try:
        report = inventory.upload_sims(db, await file.read(), category, sim_type, force_category=True)
    except ValidationError as exc:
        _bad(str(exc))
    return report.as_dict()


@router.get("/signups/{signup_id}/sims")
def list_signup_sims(signup_id: str, status: str | None = None, q: str | None = None,
                     limit: int = Query(200, ge=1, le=inventory.LIST_CAP), offset: int = Query(0, ge=0),
                     ao: AoSession = Depends(current_ao), db: Session = Depends(get_db)):
    """Free SIM <-> number pairs in this sign-up's category plus this sign-up's own cart lines.
    status: AVAILABLE|PAIRED|MINE|NUMBERS_WAITING. Lines reserved by other sign-ups and live lines are not shown."""
    s = _owned(db, signup_id, ao)
    category = _with_category(db, s)
    try:
        return inventory.list_pairings(db, category, status=status, q=q, limit=limit, offset=offset,
                                       signup_id=s.id)
    except ValidationError as exc:
        _bad(str(exc))


@router.get("/signups/{signup_id}/availability")
def availability(signup_id: str, ao: AoSession = Depends(current_ao), db: Session = Depends(get_db)):
    s = _draft(db, signup_id, ao)
    category = _with_category(db, s)
    paired = categories.ready_counts(db).get(category, {})
    sims_waiting = db.scalar(select(func.count()).select_from(Sim).where(
        Sim.category == category, Sim.status == InventoryStatus.AVAILABLE))
    return {"category": category, "ESIM": paired.get("ESIM", 0), "PHYSICAL": paired.get("PHYSICAL", 0),
            "numbers_available": inventory.free_number_count(db, category), "sims_waiting": sims_waiting}


@router.post("/signups/{signup_id}/cart")
def add_to_cart(signup_id: str, body: CartAdd, ao: AoSession = Depends(current_ao), db: Session = Depends(get_db)):
    s = _draft(db, signup_id, ao)
    _with_category(db, s)
    plan = db.scalars(select(Plan).where(Plan.code == body.plan_code, Plan.active.is_(True))).first()
    if not plan:
        _bad("Unknown plan")
    try:
        added = inventory.allocate(db, s, plan, body.sim_type, body.quantity)
    except ValidationError as exc:
        _bad(str(exc), 409)
    added_ids = {cl.id for cl in added}
    db.refresh(s)
    cart = _cart(s)
    cart["added"] = [l for l in cart["lines"] if l["id"] in added_ids]
    return cart


@router.delete("/signups/{signup_id}/cart/{line_id}")
def remove_from_cart(signup_id: str, line_id: int, ao: AoSession = Depends(current_ao),
                     db: Session = Depends(get_db)):
    s = _draft(db, signup_id, ao)
    line = db.get(CartLine, line_id)
    if not line or line.signup_id != s.id:
        _bad("Cart line not found", 404)
    inventory.release(db, line)
    db.refresh(s)
    return _cart(s)


def _provision_in_background(account_id: int) -> None:
    db = SessionLocal()
    try:
        orders.provision(db, account_id)
    finally:
        db.close()


@router.post("/signups/{signup_id}/submit")
def submit_signup(signup_id: str, background: BackgroundTasks, ao: AoSession = Depends(current_ao),
                  db: Session = Depends(get_db)):
    s = _draft(db, signup_id, ao)
    _with_category(db, s)
    try:
        account = orders.submit(db, s)
    except ValidationError as exc:
        db.rollback()
        _bad(str(exc), 422)
    background.add_task(_provision_in_background, account.id)
    return {"status": s.status.value, "account_number": account.account_number,
            "line_count": len(account.lines)}


@router.get("/accounts/{account_number}")
def get_account(account_number: str, ao: AoSession = Depends(current_ao), db: Session = Depends(get_db)):
    a = orders.find_account(db, account_number)
    if not a or a.account_officer.email != ao.email:
        _bad("Account not found", 404)
    return orders.account_view(db, a)
