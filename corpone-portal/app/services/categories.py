"""SIM inventory categories pre-allocated by the back office to companies (UEN)."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CategoryAllocation
from app.services import inventory


def for_company(db: Session, uen: str) -> list[CategoryAllocation]:
    return list(db.scalars(select(CategoryAllocation).where(
        CategoryAllocation.uen == uen, CategoryAllocation.active.is_(True)).order_by(CategoryAllocation.category)))


def is_allocated(db: Session, uen: str, category: str | None) -> bool:
    return bool(category) and db.scalar(select(CategoryAllocation.id).where(
        CategoryAllocation.category == category, CategoryAllocation.uen == uen,
        CategoryAllocation.active.is_(True))) is not None


def ready_counts(db: Session) -> dict[str, dict]:
    """Paired lines ready to allocate, per category and SIM type."""
    return {c["category"]: c.get("paired_available", {}) for c in inventory.summary(db)}


def view(a: CategoryAllocation, ready: dict[str, dict]) -> dict:
    r = ready.get(a.category, {})
    return {"category": a.category, "uen": a.uen, "description": a.description, "active": a.active,
            "ESIM": r.get("ESIM", 0), "PHYSICAL": r.get("PHYSICAL", 0)}
