"""Who may use a SIM inventory category.

- Allocated to a company (uen set): only that company's account officers can choose it.
- For all companies: an allocation row with no UEN, or a category that has SIMs/numbers but no
  allocation row at all (not delegated to anyone).
- An inactive allocation hides the category from everyone.
"""
from sqlalchemy import or_, select, union
from sqlalchemy.orm import Session

from app.models import CategoryAllocation, MobileNumber, Sim
from app.services import inventory


def _inventory_categories(db: Session) -> set[str]:
    return set(db.scalars(union(select(Sim.category), select(MobileNumber.category))))


def _unallocated(db: Session) -> list[str]:
    allocated = set(db.scalars(select(CategoryAllocation.category)))
    return sorted(_inventory_categories(db) - allocated)


def for_company(db: Session, uen: str) -> list[dict]:
    """Categories this company can choose: its own, then those open to all companies."""
    rows = db.scalars(select(CategoryAllocation).where(
        CategoryAllocation.active.is_(True),
        or_(CategoryAllocation.uen == uen, CategoryAllocation.uen.is_(None))))
    out = [_entry(a.category, a.uen, a.description, True, True) for a in rows]
    out += [_entry(c, None, "", True, False) for c in _unallocated(db)]
    return sorted(out, key=lambda e: (e["scope"] != "COMPANY", e["category"]))


def all_categories(db: Session) -> list[dict]:
    """Back office view: every allocation row plus inventory categories that have none."""
    out = [_entry(a.category, a.uen, a.description, a.active, True)
           for a in db.scalars(select(CategoryAllocation))]
    out += [_entry(c, None, "", True, False) for c in _unallocated(db)]
    return sorted(out, key=lambda e: (e["uen"] or "", e["category"]))


def is_allocated(db: Session, uen: str, category: str | None) -> bool:
    """Whether the company may use the category."""
    if not category:
        return False
    a = db.scalars(select(CategoryAllocation).where(CategoryAllocation.category == category)).first()
    if a is not None:
        return a.active and (a.uen is None or a.uen == uen)
    return category in _inventory_categories(db)


def _entry(category: str, uen: str | None, description: str, active: bool, has_row: bool) -> dict:
    return {"category": category, "uen": uen, "scope": "COMPANY" if uen else "ALL",
            "description": description, "active": active, "allocated": has_row}


def with_counts(entries: list[dict], db: Session) -> list[dict]:
    """Add paired lines ready to allocate, per SIM type."""
    ready = {c["category"]: c.get("paired_available", {}) for c in inventory.summary(db)}
    return [e | {"ESIM": ready.get(e["category"], {}).get("ESIM", 0),
                 "PHYSICAL": ready.get(e["category"], {}).get("PHYSICAL", 0)} for e in entries]


def ready_counts(db: Session) -> dict[str, dict]:
    """Paired lines ready to allocate, per category and SIM type."""
    return {c["category"]: c.get("paired_available", {}) for c in inventory.summary(db)}
