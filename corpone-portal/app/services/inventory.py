"""SIM / number inventory upload, auto-pairing and cart allocation."""
import csv
import io
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    CartLine, InventoryStatus, MobileNumber, Plan, Signup, Sim, SimType,
)
from app.validators import ValidationError, validate_category, validate_iccid, validate_msisdn

MAX_ROWS = 50_000


@dataclass
class UploadReport:
    accepted: int = 0
    rejected: list[dict] = field(default_factory=list)
    paired: int = 0

    def as_dict(self) -> dict:
        return {"accepted": self.accepted, "rejected_count": len(self.rejected),
                "rejected": self.rejected[:500], "paired": self.paired}


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
    existing = set(db.scalars(select(Sim.iccid).where(Sim.iccid.in_([r.get("iccid", "") for r in rows]))))
    seen: set[str] = set()
    categories: set[str] = set()

    for line_no, row in enumerate(rows, start=1):
        try:
            iccid = validate_iccid(row.get("iccid", ""))
            if iccid in existing or iccid in seen:
                raise ValidationError("Duplicate ICCID")
            category = validate_category(row.get("category") or default_category or "")
            if force_category and category != default_category:
                raise ValidationError(f"Category must be {default_category} for this sign-up")
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
        db.add(Sim(
            iccid=iccid, sim_type=sim_type, category=category,
            smdp_address=row.get("smdp_address") or None,
            matching_id=row.get("matching_id") or None,
        ))
        report.accepted += 1

    db.flush()
    report.paired = sum(auto_pair(db, c) for c in categories)
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
    existing = set(db.scalars(select(MobileNumber.msisdn).where(
        MobileNumber.msisdn.in_([p[2] for p in parsed if p[2]]))))

    for line_no, row, msisdn, error in parsed:
        try:
            if error:
                raise ValidationError(error)
            if msisdn in existing or msisdn in seen:
                raise ValidationError("Duplicate number")
            category = validate_category(row.get("category") or default_category or "")
        except ValidationError as exc:
            report.rejected.append({"row": line_no, "value": row.get("msisdn", ""), "error": str(exc)})
            continue
        seen.add(msisdn)
        categories.add(category)
        db.add(MobileNumber(msisdn=msisdn, category=category))
        report.accepted += 1

    db.flush()
    report.paired = sum(auto_pair(db, c) for c in categories)
    db.commit()
    return report


def auto_pair(db: Session, category: str) -> int:
    """Pair unpaired SIMs with unpaired numbers of the same category, oldest first."""
    sims = db.scalars(
        select(Sim).where(Sim.category == category, Sim.status == InventoryStatus.AVAILABLE,
                          Sim.number_id.is_(None))
        .order_by(Sim.id).with_for_update(skip_locked=True)
    ).all()
    if not sims:
        return 0
    numbers = db.scalars(
        select(MobileNumber).where(MobileNumber.category == category,
                                   MobileNumber.status == InventoryStatus.AVAILABLE)
        .order_by(MobileNumber.id).limit(len(sims)).with_for_update(skip_locked=True)
    ).all()
    now = datetime.now(timezone.utc)
    for sim, number in zip(sims, numbers):
        sim.number_id = number.id
        sim.status = InventoryStatus.PAIRED
        sim.paired_at = now
        number.status = InventoryStatus.PAIRED
    db.flush()
    return min(len(sims), len(numbers))


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
