r"""Seed خيارات التوصيل/الاستلام وكوبون ترحيبي لقاعدة البيانات المحلية.

التشغيل: .venv\Scripts\python.exe -m app.seed_delivery
idempotent: بيتخطى العناصر الموجودة بالفعل.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select

from app.database import SessionLocal
from app.models import Coupon, DeliveryOption


def main() -> None:
    with SessionLocal() as db:
        delivery_specs = [
            {"name": "توصيل", "fee": Decimal("15.00"), "is_pickup": False, "estimated_time": "30-45 دقيقة", "sort_order": 1},
            {"name": "استلام من المطعم", "fee": Decimal("0.00"), "is_pickup": True, "estimated_time": None, "sort_order": 2},
        ]
        for spec in delivery_specs:
            existing = db.scalar(select(DeliveryOption).where(DeliveryOption.name == spec["name"]))
            if existing is None:
                db.add(DeliveryOption(**spec, is_active=True))

        if db.scalar(select(Coupon).where(Coupon.code == "WELCOME10")) is None:
            now = datetime.now(timezone.utc)
            db.add(
                Coupon(
                    code="WELCOME10",
                    discount_type="percentage",
                    discount_value=Decimal("10.00"),
                    minimum_order=Decimal("50.00"),
                    start_date=now - timedelta(days=1),
                    end_date=now + timedelta(days=30),
                    usage_limit=None,
                    usage_count=0,
                    is_active=True,
                )
            )
        db.commit()
    print("Delivery options and welcome coupon are ready.")


if __name__ == "__main__":
    main()
