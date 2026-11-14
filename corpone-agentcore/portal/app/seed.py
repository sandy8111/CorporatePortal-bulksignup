from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Merchant, Plan

PLANS = [
    # Data allowances are placeholders; set them to CorpOne's actual plan definitions.
    dict(code="RAY-8", name="$8 Ray Plan", monthly_price=Decimal("8.00"), data_gb=10,
         description="Entry corporate plan: 10GB data, 100 mins local calls"),
    dict(code="FLASH-20", name="$20 Flash Plan", monthly_price=Decimal("20.00"), data_gb=100,
         description="Mid-tier plan: 100GB 5G data, unlimited local calls"),
    dict(code="GLARE-40", name="$40 Glare Plan", monthly_price=Decimal("40.00"), data_gb=300,
         description="Premium plan: 300GB 5G data, unlimited calls, 10GB roaming"),
]

MERCHANTS = [
    dict(code="CORP-DIRECT-01", name="CorpOne Enterprise Direct Sales"),
]


def seed(db: Session) -> None:
    for p in PLANS:
        if not db.scalars(select(Plan).where(Plan.code == p["code"])).first():
            db.add(Plan(**p))
    for m in MERCHANTS:
        if not db.scalars(select(Merchant).where(Merchant.code == m["code"])).first():
            db.add(Merchant(**m))
    db.commit()
