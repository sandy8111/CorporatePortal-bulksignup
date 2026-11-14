"""Back office view of sign-ups that have lines in their cart: drafts still being filled, and submitted orders."""
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models import CartLine, CorporateAccount, Line, Signup, SignupStatus

PAGE_CAP = 200


def _with_lines():
    return select(Signup).where(Signup.cart_lines.any())


def _summary(s: Signup, account_number: str | None) -> dict:
    lines = s.cart_lines
    return {
        "id": s.id, "status": s.status.value, "uen": s.uen, "company_name": s.company_name,
        "category": s.category, "ao_email": s.owner_email, "ao_name": s.ao_full_name,
        "merchant_code": s.merchant_code, "line_count": len(lines),
        "esim": sum(1 for l in lines if l.sim.sim_type.value == "ESIM"),
        "physical": sum(1 for l in lines if l.sim.sim_type.value == "PHYSICAL"),
        "monthly_total": str(sum((l.plan.monthly_price for l in lines), Decimal("0"))),
        "created_at": s.created_at.isoformat(),
        "submitted_at": s.submitted_at.isoformat() if s.submitted_at else None,
        "account_number": account_number,
    }


def list_orders(db: Session, status: SignupStatus | None = None, q: str | None = None,
                limit: int = 50, offset: int = 0) -> dict:
    base = _with_lines()
    if q:
        like = f"%{q.strip()}%"
        base = base.where(or_(Signup.uen.ilike(like), Signup.company_name.ilike(like),
                              Signup.owner_email.ilike(like), Signup.id.ilike(like)))
    sub = base.subquery()
    found = dict(db.execute(select(sub.c.status, func.count()).group_by(sub.c.status)).all())
    counts = {st.value: found.get(st, 0) for st in SignupStatus}
    stmt = base.where(Signup.status == status) if status else base
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.options(selectinload(Signup.cart_lines).selectinload(CartLine.plan),
                                   selectinload(Signup.cart_lines).selectinload(CartLine.sim))
                      .order_by(Signup.created_at.desc())
                      .limit(max(1, min(limit, PAGE_CAP))).offset(max(0, offset))).all()
    accounts = dict(db.execute(select(CorporateAccount.id, CorporateAccount.account_number).where(
        CorporateAccount.id.in_([s.account_id for s in rows if s.account_id]))).all())
    return {"total": total, "counts": counts,
            "orders": [_summary(s, accounts.get(s.account_id)) for s in rows]}


def order_detail(db: Session, signup_id: str) -> dict | None:
    s = db.get(Signup, signup_id)
    if s is None or not s.cart_lines:
        return None
    account = db.get(CorporateAccount, s.account_id) if s.account_id else None
    live = {l.sim_id: l for l in db.scalars(select(Line).where(Line.account_id == account.id))} if account else {}
    lines = []
    for cl in s.cart_lines:
        l = live.get(cl.sim_id)
        lines.append({
            "msisdn": cl.number.msisdn, "iccid": cl.sim.iccid, "sim_type": cl.sim.sim_type.value,
            "plan": cl.plan.name, "monthly_price": str(cl.plan.monthly_price),
            "inventory_status": cl.sim.status.value,
            "line_status": l.status.value if l else None, "network_ref": l.network_ref if l else None,
            "error": l.last_error if l else None, "esim_qr_sent": bool(l and l.esim_qr_sent_at),
        })
    return _summary(s, account.account_number if account else None) | {
        "company_address": s.company_address, "ao_mobile": s.ao_mobile, "lines": lines}
