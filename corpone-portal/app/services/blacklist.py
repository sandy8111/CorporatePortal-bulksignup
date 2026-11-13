from datetime import date

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import BlacklistEntry, IdType, SubjectType


def active_entry(db: Session, subject_type: SubjectType, id_type: IdType, id_value: str,
                 on: date | None = None) -> BlacklistEntry | None:
    """Return the blacklist entry in force on `on` (default today), if any.

    An entry is in force when start_date <= on and (end_date is null or end_date >= on).
    """
    on = on or date.today()
    stmt = (
        select(BlacklistEntry)
        .where(
            BlacklistEntry.subject_type == subject_type,
            BlacklistEntry.id_type == id_type,
            BlacklistEntry.id_value == id_value.upper(),
            BlacklistEntry.start_date <= on,
            or_(BlacklistEntry.end_date.is_(None), BlacklistEntry.end_date >= on),
        )
        .limit(1)
    )
    return db.scalars(stmt).first()
