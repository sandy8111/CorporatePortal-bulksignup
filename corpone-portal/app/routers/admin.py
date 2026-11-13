"""Back-office API: inventory, blacklist, merchants, provisioning retries. Requires X-Admin-Token."""
import secrets

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Header, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from pydantic import BaseModel, Field

from app.gateway import inbound
from app.gateway.outbound import gateway as outbound_gateway
from app.models import (
    ApiClient, BlacklistEntry, CorporateAccount, GatewayAuditLog, Merchant, OutboundCallLog, SimType,
)
from app.schemas import BlacklistIn, MerchantIn
from app.services import inventory, orders
from app.validators import ValidationError, validate_category


def require_admin(x_admin_token: str = Header(default="")):
    if not secrets.compare_digest(x_admin_token, get_settings().admin_token):
        raise HTTPException(status_code=401, detail="Invalid admin token")


router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])


@router.post("/inventory/sims")
async def upload_sims(file: UploadFile = File(...), category: str | None = Form(None),
                      sim_type: SimType | None = Form(None), db: Session = Depends(get_db)):
    try:
        cat = validate_category(category) if category else None
        return inventory.upload_sims(db, await file.read(), cat, sim_type).as_dict()
    except ValidationError as exc:
        raise HTTPException(400, str(exc))


@router.post("/inventory/numbers")
async def upload_numbers(file: UploadFile = File(...), category: str | None = Form(None),
                         db: Session = Depends(get_db)):
    try:
        cat = validate_category(category) if category else None
        return inventory.upload_numbers(db, await file.read(), cat).as_dict()
    except ValidationError as exc:
        raise HTTPException(400, str(exc))


@router.get("/inventory/summary")
def inventory_summary(db: Session = Depends(get_db)):
    return inventory.summary(db)


@router.post("/inventory/pair")
def pair(category: str, db: Session = Depends(get_db)):
    try:
        n = inventory.auto_pair(db, validate_category(category))
    except ValidationError as exc:
        raise HTTPException(400, str(exc))
    db.commit()
    return {"paired": n}


def _bl(e: BlacklistEntry) -> dict:
    return {"id": e.id, "subject_type": e.subject_type.value, "id_type": e.id_type.value,
            "id_value": e.id_value, "reason": e.reason, "start_date": e.start_date.isoformat(),
            "end_date": e.end_date.isoformat() if e.end_date else None}


@router.get("/blacklist")
def list_blacklist(db: Session = Depends(get_db)):
    return [_bl(e) for e in db.scalars(select(BlacklistEntry).order_by(BlacklistEntry.id.desc()))]


@router.post("/blacklist", status_code=201)
def add_blacklist(body: BlacklistIn, db: Session = Depends(get_db)):
    e = BlacklistEntry(**body.model_dump())
    db.add(e)
    db.commit()
    return _bl(e)


@router.delete("/blacklist/{entry_id}", status_code=204)
def delete_blacklist(entry_id: int, db: Session = Depends(get_db)):
    e = db.get(BlacklistEntry, entry_id)
    if not e:
        raise HTTPException(404, "Entry not found")
    db.delete(e)
    db.commit()


@router.get("/merchants")
def list_merchants(db: Session = Depends(get_db)):
    return [{"code": m.code, "name": m.name, "active": m.active} for m in db.scalars(select(Merchant))]


@router.put("/merchants")
def upsert_merchant(body: MerchantIn, db: Session = Depends(get_db)):
    code = body.code.strip().upper()
    m = db.scalars(select(Merchant).where(Merchant.code == code)).first() or Merchant(code=code)
    m.name, m.active = body.name, body.active
    db.add(m)
    db.commit()
    return {"code": m.code, "name": m.name, "active": m.active}


def _account(db: Session, account_number: str) -> CorporateAccount:
    a = db.scalars(select(CorporateAccount).where(CorporateAccount.account_number == account_number)).first()
    if not a:
        raise HTTPException(404, "Account not found")
    return a


@router.post("/accounts/{account_number}/retry-provisioning")
def retry_provisioning(account_number: str, db: Session = Depends(get_db)):
    a = _account(db, account_number)
    orders.provision(db, a.id)
    return {"lines": {l.number.msisdn: l.status.value for l in a.lines}}


@router.post("/accounts/{account_number}/resend-esim")
def resend_esim(account_number: str, db: Session = Depends(get_db)):
    a = _account(db, account_number)
    for l in a.lines:
        l.esim_qr_sent_at = None
    return {"sent": orders.send_esim_qr_codes(db, a)}


# --------------------------------------------------------------------------- API gateway management

class ApiClientIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    scopes: list[str]
    rate_limit_per_min: int | None = Field(default=None, ge=1, le=10000)


def _client_view(c: ApiClient) -> dict:
    return {"id": c.id, "name": c.name, "key_prefix": f"cpk_{c.key_prefix}_…", "scopes": c.scopes.split(),
            "rate_limit_per_min": c.rate_limit_per_min, "active": c.active,
            "created_at": c.created_at.isoformat(),
            "last_used_at": c.last_used_at.isoformat() if c.last_used_at else None}


@router.get("/gateway/scopes")
def gateway_scopes():
    return inbound.SCOPES


@router.get("/gateway/clients")
def list_api_clients(db: Session = Depends(get_db)):
    return [_client_view(c) for c in db.scalars(select(ApiClient).order_by(ApiClient.id.desc()))]


@router.post("/gateway/clients", status_code=201)
def create_api_client(body: ApiClientIn, db: Session = Depends(get_db)):
    try:
        client, key = inbound.issue_key(db, body.name.strip(), body.scopes, body.rate_limit_per_min)
    except ValidationError as exc:
        raise HTTPException(400, str(exc))
    return {**_client_view(client), "api_key": key, "note": "Store this key now; it cannot be shown again."}


@router.delete("/gateway/clients/{client_id}", status_code=204)
def revoke_api_client(client_id: int, db: Session = Depends(get_db)):
    c = db.get(ApiClient, client_id)
    if not c:
        raise HTTPException(404, "API client not found")
    c.active = False
    db.commit()


@router.get("/gateway/audit")
def gateway_audit(limit: int = 100, db: Session = Depends(get_db)):
    names = {c.id: c.name for c in db.scalars(select(ApiClient))}
    rows = db.scalars(select(GatewayAuditLog).order_by(GatewayAuditLog.id.desc()).limit(min(limit, 1000)))
    return [{"at": r.created_at.isoformat(), "client": names.get(r.client_id, "-"), "method": r.method,
             "path": r.path, "status": r.status_code, "latency_ms": r.latency_ms, "ip": r.remote_ip} for r in rows]


@router.get("/gateway/outbound")
def outbound_status(limit: int = 100, db: Session = Depends(get_db)):
    rows = db.scalars(select(OutboundCallLog).order_by(OutboundCallLog.id.desc()).limit(min(limit, 1000)))
    return {"upstreams": outbound_gateway.status(),
            "calls": [{"at": r.created_at.isoformat(), "upstream": r.upstream, "operation": r.operation,
                       "attempt": r.attempt, "outcome": r.outcome, "latency_ms": r.latency_ms, "error": r.error}
                      for r in rows]}


@router.post("/gateway/outbound/reset", status_code=204)
def outbound_reset():
    outbound_gateway.reset()
