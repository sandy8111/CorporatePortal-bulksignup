"""SIM / number inventory upload, auto-pairing and cart allocation."""
import csv
import io
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    CartLine, InventoryStatus, MobileNumber, Plan, Signup, Sim, SimType,
)
from app.validators import ValidationError, validate_category, validate_iccid, validate_msisdn

MAX_ROWS = 50_000
LIST_CAP = 500  # max rows echoed back in an upload report, and max page size for inventory lists


@dataclass
class UploadReport:
    accepted: int = 0
    rejected: list[dict] = field(default_factory=list)
    pairs: list[dict] = field(default_factory=list)   # pairs that include a SIM/number from this upload
    items: list[dict] = field(default_factory=list)   # accepted rows and their pairing outcome
    paired_existing: int = 0                          # older waiting SIMs/numbers paired as a side effect

    @property
    def paired(self) -> int:
        return len(self.pairs)

    def as_dict(self) -> dict:
        return {"accepted": self.accepted, "rejected_count": len(self.rejected),
                "rejected": self.rejected[:LIST_CAP], "paired": self.paired,
                "pairs": self.pairs[:LIST_CAP], "items": self.items[:LIST_CAP],
                "paired_existing": self.paired_existing}


_STATUS_TEXT = {
    InventoryStatus.AVAILABLE: "not paired yet",
    InventoryStatus.PAIRED: "paired and ready",
    InventoryStatus.RESERVED: "reserved in a cart",
    InventoryStatus.ASSIGNED: "in use on an active line",
}


def _already_uploaded(line_no: int, value: str, item: Sim | MobileNumber, category: str) -> dict:
    """Reject row for an existing SIM/number. Details are only shown when the existing record is in the
    row's own (already validated and authorised) category, so other categories are never revealed."""
    row = {"row": line_no, "value": value, "code": "already_uploaded", "existing": None,
           "error": "Already uploaded under another category"}
    if item.category != category:
        return row
    if isinstance(item, Sim):
        partner = item.number.msisdn if item.number else None
        row["existing"] = {"iccid": item.iccid, "sim_type": item.sim_type.value, "status": item.status.value,
                           "msisdn": partner}
        paired_with, waiting_for = partner, "a number"
    else:
        partner = item.sim.iccid if item.sim else None
        row["existing"] = {"msisdn": item.msisdn, "status": item.status.value, "iccid": partner}
        paired_with, waiting_for = f"SIM {partner}", "a SIM"
    row["error"] = (f"Already uploaded: paired with {paired_with}, {_STATUS_TEXT[item.status]}" if partner
                    else f"Already uploaded: not paired yet (waiting for {waiting_for})")
    return row


def _row_category(row: dict, default_category: str | None, force_category: bool = False) -> str:
    category = validate_category(row.get("category") or default_category or "")
    if force_category and category != default_category:
        raise ValidationError(f"Category must be {default_category} for this sign-up")
    return category


def _pair_categories(db: Session, report: UploadReport, categories: set[str], new_key: str, new_values: set[str]):
    """Run auto-pairing and keep only pairs that involve this upload's rows in report.pairs."""
    for c in sorted(categories):
        for p in auto_pair(db, c):
            if p[new_key] in new_values:
                report.pairs.append(p)
            else:
                report.paired_existing += 1


def _rows(raw: bytes, key_column: str) -> list[dict]:
    """Parse CSV with an optional header row. Without a header, the file is one value per line."""
    text = raw.decode("utf-8-sig", errors="replace")
    reader = list(csv.reader(io.StringIO(text)))
    reader = [r for r in reader if any(c.strip() for c in r)]
    if not reader:
        return []
    header = [h.strip().lower() for h in reader[0]]
    if key_column in header:
        return [dict(zip(header, (c.strip() for c in r))) for r in reader[1:]]
    return [{key_column: r[0].strip()} for r in reader]


def upload_sims(db: Session, raw: bytes, default_category: str | None,
                default_sim_type: SimType | None, force_category: bool = False) -> UploadReport:
    """Load SIMs into inventory. With force_category, every row must belong to default_category."""
    rows = _rows(raw, "iccid")
    if len(rows) > MAX_ROWS:
        raise ValidationError(f"File has more than {MAX_ROWS} rows")
    report = UploadReport()
    existing = {s.iccid: s for s in db.scalars(
        select(Sim).options(selectinload(Sim.number))
        .where(Sim.iccid.in_([r.get("iccid", "").strip() for r in rows])))}
    seen: set[str] = set()
    categories: set[str] = set()
    new_sims: list[Sim] = []

    for line_no, row in enumerate(rows, start=1):
        try:
            iccid = validate_iccid(row.get("iccid", ""))
            if iccid in seen:
                raise ValidationError("Listed twice in this file")
            category = _row_category(row, default_category, force_category)
            if iccid in existing:
                report.rejected.append(_already_uploaded(line_no, iccid, existing[iccid], category))
                continue
            sim_type_raw = (row.get("sim_type") or (default_sim_type.value if default_sim_type else "")).upper()
            sim_type_raw = {"E-SIM": "ESIM", "PSIM": "PHYSICAL", "PHYSICAL_SIM": "PHYSICAL"}.get(sim_type_raw, sim_type_raw)
            try:
                sim_type = SimType(sim_type_raw)
            except ValueError:
                raise ValidationError("sim_type must be ESIM or PHYSICAL") from None
        except ValidationError as exc:
            report.rejected.append({"row": line_no, "value": row.get("iccid", ""), "error": str(exc)})
            continue
        seen.add(iccid)
        categories.add(category)
        sim = Sim(iccid=iccid, sim_type=sim_type, category=category,
                  smdp_address=row.get("smdp_address") or None, matching_id=row.get("matching_id") or None)
        db.add(sim)
        new_sims.append(sim)
        report.accepted += 1

    db.flush()
    _pair_categories(db, report, categories, "iccid", seen)
    paired_to = {p["iccid"]: p["msisdn"] for p in report.pairs}
    report.items = [{"iccid": s.iccid, "sim_type": s.sim_type.value, "category": s.category,
                     "msisdn": paired_to.get(s.iccid)} for s in new_sims]
    db.commit()
    return report


def upload_numbers(db: Session, raw: bytes, default_category: str | None) -> UploadReport:
    rows = _rows(raw, "msisdn")
    if len(rows) > MAX_ROWS:
        raise ValidationError(f"File has more than {MAX_ROWS} rows")
    report = UploadReport()
    seen: set[str] = set()
    categories: set[str] = set()
    parsed: list[tuple[int, dict, str | None, str | None]] = []
    for line_no, row in enumerate(rows, start=1):
        try:
            parsed.append((line_no, row, validate_msisdn(row.get("msisdn", "")), None))
        except ValidationError as exc:
            parsed.append((line_no, row, None, str(exc)))
    existing = {n.msisdn: n for n in db.scalars(
        select(MobileNumber).options(selectinload(MobileNumber.sim))
        .where(MobileNumber.msisdn.in_([p[2] for p in parsed if p[2]])))}
    new_numbers: list[MobileNumber] = []

    for line_no, row, msisdn, error in parsed:
        try:
            if error:
                raise ValidationError(error)
            if msisdn in seen:
                raise ValidationError("Listed twice in this file")
            category = _row_category(row, default_category)
            if msisdn in existing:
                report.rejected.append(_already_uploaded(line_no, msisdn, existing[msisdn], category))
                continue
        except ValidationError as exc:
            report.rejected.append({"row": line_no, "value": row.get("msisdn", ""), "error": str(exc)})
            continue
        seen.add(msisdn)
        categories.add(category)
        num = MobileNumber(msisdn=msisdn, category=category)
        db.add(num)
        new_numbers.append(num)
        report.accepted += 1

    db.flush()
    _pair_categories(db, report, categories, "msisdn", seen)
    paired_to = {p["msisdn"]: p["iccid"] for p in report.pairs}
    report.items = [{"msisdn": n.msisdn, "category": n.category, "iccid": paired_to.get(n.msisdn)}
                    for n in new_numbers]
    db.commit()
    return report


def auto_pair(db: Session, category: str) -> list[dict]:
    """Pair unpaired SIMs with unpaired numbers of the same category, oldest first. Returns the new pairs."""
    sims = db.scalars(
        select(Sim).where(Sim.category == category, Sim.status == InventoryStatus.AVAILABLE,
                          Sim.number_id.is_(None))
        .order_by(Sim.id).with_for_update(skip_locked=True)
    ).all()
    if not sims:
        return []
    numbers = db.scalars(
        select(MobileNumber).where(MobileNumber.category == category,
                                   MobileNumber.status == InventoryStatus.AVAILABLE)
        .order_by(MobileNumber.id).limit(len(sims)).with_for_update(skip_locked=True)
    ).all()
    now = datetime.now(timezone.utc)
    pairs = []
    for sim, number in zip(sims, numbers):
        sim.number_id = number.id
        sim.status = InventoryStatus.PAIRED
        sim.paired_at = now
        number.status = InventoryStatus.PAIRED
        pairs.append({"iccid": sim.iccid, "msisdn": number.msisdn, "sim_type": sim.sim_type.value,
                      "category": category})
    db.flush()
    return pairs


# Filters for the inventory/pairing lists. NUMBERS_WAITING lists numbers that have no SIM yet.
SIM_FILTERS = {"AVAILABLE", "PAIRED", "RESERVED", "ASSIGNED", "NUMBERS_WAITING"}
AO_FILTERS = {"AVAILABLE", "PAIRED", "MINE", "NUMBERS_WAITING"}  # an AO never sees other carts' or live lines


def _like(q: str) -> str:
    escaped = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _pair_row(sim: Sim | None, num: MobileNumber | None, in_cart: set[int]) -> dict:
    return {
        "iccid": sim.iccid if sim else None,
        "sim_type": sim.sim_type.value if sim else None,
        "sim_status": sim.status.value if sim else None,
        "msisdn": num.msisdn if num else None,
        "number_status": num.status.value if num else None,
        # paired SIM and number should always move together; flag drift for the back office
        "status_mismatch": bool(sim and num and sim.status != num.status),
        "in_my_cart": bool(sim and sim.id in in_cart),
        "uploaded_at": (sim or num).created_at.isoformat(),
        "paired_at": sim.paired_at.isoformat() if sim and sim.paired_at else None,
    }


def list_pairings(db: Session, category: str, status: str | None = None, q: str | None = None,
                  limit: int = 200, offset: int = 0, signup_id: str | None = None) -> dict:
    """SIM <-> number pairs in a category with the status of both sides.

    With signup_id (account officer view) only free inventory (AVAILABLE, PAIRED) and that sign-up's own
    cart lines are visible; reserved lines in other carts and live lines are never returned."""
    limit, offset = max(1, min(limit, LIST_CAP)), max(0, offset)
    ao_view = signup_id is not None
    allowed = AO_FILTERS if ao_view else SIM_FILTERS
    if status and status not in allowed:
        raise ValidationError(f"Unknown status filter; use one of {', '.join(sorted(allowed))}")
    in_cart = {sid for (sid,) in db.execute(select(CartLine.sim_id).where(CartLine.signup_id == signup_id))} \
        if ao_view else set()
    free = (InventoryStatus.AVAILABLE, InventoryStatus.PAIRED)

    if status == "NUMBERS_WAITING":
        stmt = select(MobileNumber).options(selectinload(MobileNumber.sim)).where(
            MobileNumber.category == category, ~MobileNumber.sim.has())
        if q:
            stmt = stmt.where(MobileNumber.msisdn.like(_like(q), escape="\\"))
        order = MobileNumber.id.desc()
    else:
        stmt = select(Sim).options(selectinload(Sim.number)).where(Sim.category == category)
        if ao_view:
            stmt = stmt.where(or_(Sim.status.in_(free), Sim.id.in_(in_cart or {-1})))
        if status == "MINE":
            stmt = stmt.where(Sim.id.in_(in_cart or {-1}))
        elif status:
            stmt = stmt.where(Sim.status == InventoryStatus(status))
        if q:
            like = _like(q)
            stmt = stmt.outerjoin(MobileNumber, Sim.number_id == MobileNumber.id).where(
                Sim.iccid.like(like, escape="\\") | MobileNumber.msisdn.like(like, escape="\\"))
        order = Sim.id.desc()

    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = db.scalars(stmt.order_by(order).limit(limit).offset(offset)).all()
    pairs = [_pair_row(None, n, in_cart) for n in rows] if status == "NUMBERS_WAITING" \
        else [_pair_row(s, s.number, in_cart) for s in rows]

    sim_counts = dict(db.execute(select(Sim.status, func.count()).where(Sim.category == category)
                                 .group_by(Sim.status)).all())
    waiting = free_number_count(db, category)
    if ao_view:
        counts = {st.value: sim_counts.get(st, 0) for st in free} | {"MINE": len(in_cart), "NUMBERS_WAITING": waiting}
    else:
        counts = {st.value: sim_counts.get(st, 0) for st in InventoryStatus} | {"NUMBERS_WAITING": waiting}
    return {"category": category, "total": total, "limit": limit, "offset": offset,
            "counts": counts, "pairs": pairs}


def free_number_count(db: Session, category: str) -> int:
    """Numbers in the category with no SIM yet, i.e. available to pair with newly uploaded SIMs."""
    return db.scalar(select(func.count()).select_from(MobileNumber).where(
        MobileNumber.category == category, ~MobileNumber.sim.has()))


def summary(db: Session) -> list[dict]:
    out: dict[str, dict] = {}
    for cat, sim_type, status, n in db.execute(
            select(Sim.category, Sim.sim_type, Sim.status, func.count()).group_by(
                Sim.category, Sim.sim_type, Sim.status)):
        c = out.setdefault(cat, {"category": cat, "sims": {}, "numbers": {}, "paired_available": {}})
        c["sims"].setdefault(sim_type.value, {})[status.value] = n
        if status is InventoryStatus.PAIRED:
            c["paired_available"][sim_type.value] = n
    for cat, status, n in db.execute(
            select(MobileNumber.category, MobileNumber.status, func.count()).group_by(
                MobileNumber.category, MobileNumber.status)):
        c = out.setdefault(cat, {"category": cat, "sims": {}, "numbers": {}, "paired_available": {}})
        c["numbers"][status.value] = n
    return sorted(out.values(), key=lambda c: c["category"])


def allocate(db: Session, signup: Signup, plan: Plan, sim_type: SimType, quantity: int) -> list[CartLine]:
    """Reserve `quantity` paired SIM+number lines of the sign-up's category into its cart."""
    sims = db.scalars(
        select(Sim).where(Sim.category == signup.category, Sim.sim_type == sim_type,
                          Sim.status == InventoryStatus.PAIRED)
        .order_by(Sim.id).limit(quantity).with_for_update(skip_locked=True)
    ).all()
    if len(sims) < quantity:
        raise ValidationError(
            f"Only {len(sims)} paired {sim_type.value} line(s) available in category "
            f"{signup.category}; requested {quantity}")
    lines = []
    for sim in sims:
        sim.status = InventoryStatus.RESERVED
        sim.number.status = InventoryStatus.RESERVED
        line = CartLine(signup_id=signup.id, sim_id=sim.id, number_id=sim.number_id, plan_id=plan.id)
        db.add(line)
        lines.append(line)
    db.commit()
    return lines


def release(db: Session, line: CartLine) -> None:
    line.sim.status = InventoryStatus.PAIRED
    line.number.status = InventoryStatus.PAIRED
    db.delete(line)
    db.commit()
