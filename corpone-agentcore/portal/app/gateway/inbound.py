"""Inbound API gateway (/gateway/v1) for partner systems and AI agents.

Auth: `Authorization: Bearer cpk_<prefix>_<secret>` or `X-API-Key: ...`. Keys are stored hashed and
carry space-separated scopes; each key has its own per-minute rate limit. Every request is audited.
"""
import hashlib
import hmac
import secrets
import time
from datetime import date, datetime, timezone

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.netutil import client_ip
from app.db import SessionLocal, get_db
from app.models import ApiClient, GatewayAuditLog, IdType, Plan, SimType, SubjectType
from app.ratelimit import SlidingWindowLimiter
from app.services import inventory, orders
from app.services.blacklist import active_entry
from app.validators import ValidationError, validate_category, validate_person_id, validate_uen

SCOPES = {
    "plans:read": "List corporate plans",
    "validation:read": "Validate UEN and check blacklist status",
    "inventory:read": "Read SIM/number inventory summary",
    "inventory:write": "Upload SIM and number inventory",
    "accounts:read": "Read corporate account and line provisioning status",
    "provisioning:write": "Retry provisioning for an account",
}
limiter = SlidingWindowLimiter(window_seconds=60)


# --------------------------------------------------------------------------- key management

def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def issue_key(db: Session, name: str, scopes: list[str], rate_limit_per_min: int | None) -> tuple[ApiClient, str]:
    unknown = set(scopes) - SCOPES.keys()
    if unknown or not scopes:
        raise ValidationError(f"Unknown or empty scopes: {sorted(unknown)}; valid: {sorted(SCOPES)}")
    prefix = secrets.token_hex(4)
    secret = secrets.token_urlsafe(32)
    client = ApiClient(name=name, key_prefix=prefix, key_hash=_hash(secret), scopes=" ".join(sorted(set(scopes))),
                       rate_limit_per_min=rate_limit_per_min or get_settings().gateway_default_rate_per_min)
    db.add(client)
    db.commit()
    return client, f"cpk_{prefix}_{secret}"


def _authenticate(db: Session, raw: str) -> ApiClient | None:
    parts = raw.split("_", 2)
    if len(parts) != 3 or parts[0] != "cpk":
        return None
    client = db.scalars(select(ApiClient).where(ApiClient.key_prefix == parts[1])).first()
    if client is None or not client.active or not hmac.compare_digest(client.key_hash, _hash(parts[2])):
        return None
    return client


def require(scope: str):
    def dep(request: Request, db: Session = Depends(get_db),
            authorization: str | None = Header(default=None), x_api_key: str | None = Header(default=None)):
        raw = x_api_key or (authorization[7:].strip() if authorization and authorization.lower().startswith("bearer ") else "")
        client = _authenticate(db, raw) if raw else None
        if client is None:
            raise HTTPException(401, "Missing or invalid API key", headers={"WWW-Authenticate": "Bearer"})
        request.state.client_id = client.id
        if scope not in client.scopes.split():
            raise HTTPException(403, f"API key lacks scope '{scope}'")
        ok, remaining, retry = limiter.hit(f"client:{client.id}", client.rate_limit_per_min)
        request.state.rate_headers = {"X-RateLimit-Limit": str(client.rate_limit_per_min),
                                      "X-RateLimit-Remaining": str(remaining)}
        if not ok:
            raise HTTPException(429, "Rate limit exceeded",
                                headers={"Retry-After": str(int(retry) + 1), **request.state.rate_headers})
        client.last_used_at = datetime.now(timezone.utc)
        db.commit()
        return client
    return dep


# --------------------------------------------------------------------------- app

gateway_app = FastAPI(
    title="CorpOne Partner & AI Gateway", version="1.0.0",
    description="Scoped, rate-limited API for partner systems and AI agents. "
                "Authenticate with `Authorization: Bearer <api key>`.",
)


@gateway_app.middleware("http")
async def audit(request: Request, call_next):
    start = time.monotonic()
    response = await call_next(request)
    for k, v in getattr(request.state, "rate_headers", {}).items():
        response.headers.setdefault(k, v)
    try:
        with SessionLocal() as db:
            db.add(GatewayAuditLog(client_id=getattr(request.state, "client_id", None), method=request.method,
                                   path=request.url.path[:500], status_code=response.status_code,
                                   latency_ms=int((time.monotonic() - start) * 1000),
                                   remote_ip=client_ip(request)))
            db.commit()
    except Exception:  # auditing must not break the response
        pass
    return response


@gateway_app.exception_handler(ValidationError)
async def _validation(_, exc: ValidationError):
    return JSONResponse({"detail": str(exc)}, status_code=400)


class BlacklistCheck(BaseModel):
    subject_type: SubjectType
    id_type: IdType
    id_value: str
    on_date: date | None = None


@gateway_app.get("/v1/plans", tags=["plans"], summary="List corporate plans", operation_id="listPlans",
                 description="Active monthly plans with code, name, price and data allowance.")
def plans(_=Depends(require("plans:read")), db: Session = Depends(get_db)):
    return [{"code": p.code, "name": p.name, "monthly_price": str(p.monthly_price), "data_gb": p.data_gb,
             "description": p.description}
            for p in db.scalars(select(Plan).where(Plan.active.is_(True)).order_by(Plan.monthly_price))]


@gateway_app.get("/v1/validate/uen/{uen}", tags=["validation"], summary="Validate a Singapore UEN", operation_id="validateUen",
                 description="Checks a UEN's format and check letter (business, local company, other entity) and whether "
                             "the company is blacklisted. Returns valid=false with a reason for a malformed UEN.")
def check_uen(uen: str, _=Depends(require("validation:read")), db: Session = Depends(get_db)):
    try:
        norm = validate_uen(uen)
    except ValidationError as exc:
        return {"uen": uen, "valid": False, "reason": str(exc)}
    entry = active_entry(db, SubjectType.COMPANY, IdType.UEN, norm)
    return {"uen": norm, "valid": True, "blacklisted": entry is not None}


@gateway_app.post("/v1/blacklist/check", tags=["validation"], summary="Is a company or account officer blacklisted?",
                  operation_id="checkBlacklist",
                  description="Back-office only. Looks up a UEN or NRIC/FIN/passport in the blacklist on a date (default today).")
def check_blacklist(body: BlacklistCheck, _=Depends(require("validation:read")), db: Session = Depends(get_db)):
    value = validate_uen(body.id_value) if body.id_type is IdType.UEN else validate_person_id(body.id_type, body.id_value)
    e = active_entry(db, body.subject_type, body.id_type, value, on=body.on_date)
    return {"blacklisted": e is not None, "id_value": value,
            "entry": None if e is None else {"start_date": e.start_date.isoformat(),
                                             "end_date": e.end_date.isoformat() if e.end_date else None,
                                             "reason": e.reason}}


@gateway_app.get("/v1/inventory/summary", tags=["inventory"], summary="SIM/number inventory by category",
                 operation_id="getInventorySummary",
                 description="Back-office only. Counts of SIMs (by type and status) and numbers per category, and lines ready.")
def inv_summary(_=Depends(require("inventory:read")), db: Session = Depends(get_db)):
    return inventory.summary(db)


@gateway_app.post("/v1/inventory/sims", tags=["inventory"], summary="Upload SIM CSV (auto-pairs with available numbers)",
                  operation_id="uploadSims")
async def inv_sims(request: Request, file: UploadFile = File(...), category: str | None = Form(None),
                   sim_type: SimType | None = Form(None),
                   _=Depends(require("inventory:write")), db: Session = Depends(get_db)):
    return inventory.upload_sims(db, await file.read(), validate_category(category) if category else None,
                                 sim_type, src=_gateway_source(request, file)).as_dict()


@gateway_app.post("/v1/inventory/numbers", tags=["inventory"], summary="Upload number CSV (added as available; pairs when SIMs are uploaded)",
                  operation_id="uploadNumbers")
async def inv_numbers(request: Request, file: UploadFile = File(...), category: str | None = Form(None),
                      _=Depends(require("inventory:write")), db: Session = Depends(get_db)):
    return inventory.upload_numbers(db, await file.read(), validate_category(category) if category else None,
                                    src=_gateway_source(request, file)).as_dict()


def _gateway_source(request: Request, file: UploadFile) -> inventory.UploadSource:
    return inventory.UploadSource(filename=file.filename or "", source="GATEWAY",
                                  uploaded_by=f"api-client:{getattr(request.state, 'client_id', '?')}")


@gateway_app.get("/v1/accounts/{account_number}", tags=["accounts"], summary="Account and line provisioning status",
                 operation_id="getAccountStatus",
                 description="Back-office only. Order status, billing reference and each line's provisioning status for an "
                             "account number such as CA0123456789.")
def account(account_number: str, _=Depends(require("accounts:read")), db: Session = Depends(get_db)):
    a = orders.find_account(db, account_number)
    if not a:
        raise HTTPException(404, "Account not found")
    return orders.account_view(db, a)


@gateway_app.post("/v1/accounts/{account_number}/retry-provisioning", tags=["accounts"],
                  summary="Retry failed or pending lines", operation_id="retryProvisioning",
                  description="Ops only. Re-runs provisioning for the account's failed or pending lines (idempotent).")
def retry(account_number: str, _=Depends(require("provisioning:write")), db: Session = Depends(get_db)):
    a = orders.find_account(db, account_number)
    if not a:
        raise HTTPException(404, "Account not found")
    orders.provision(db, a.id)
    db.refresh(a)
    return orders.account_view(db, a)
