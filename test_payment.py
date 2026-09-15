"""
اختبارات طرق الدفع — القبول/الرفض/الحفظ/عرض الرسالة.

التشغيل: .venv\\Scripts\\python.exe test_payment.py
"""
from __future__ import annotations

import sys

sys.stdout.reconfigure(encoding="utf-8")

from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.database import engine, get_db
from app.main import app
from app.models import Admin, Category, DeliveryOption, Order, Product, RestaurantSettings
from app.security import hash_password

connection = engine.connect()
transaction = connection.begin()
TestingSessionLocal = sessionmaker(
    bind=connection, join_transaction_mode="create_savepoint", autoflush=False, expire_on_commit=False,
)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)

PASS = 0
FAIL = 0


def check(name: str, condition: bool, extra: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {extra}")


def base_payload(payment_method):
    p = {
        "customer_name": "عميل تجريبي",
        "customer_phone": "01000000000",
        "customer_address": None,
        "notes": None,
        "delivery_option_id": None,
        "delivery_zone_id": None,
        "coupon_code": None,
        "pickup_time": None,
        "items": [{"product_id": 0, "quantity": 1}],
    }
    if payment_method is not None:
        p["payment_method"] = payment_method
    return p


try:
    db = TestingSessionLocal()
    settings = db.scalar(select(RestaurantSettings))
    if settings is None:
        settings = RestaurantSettings()
        db.add(settings)
    settings.restaurant_name = "دفع تيست"
    settings.whatsapp_number = "201000000000"
    settings.currency = "EGP"

    admin = Admin(username="pay-admin", password_hash=hash_password("pass"), full_name="P")
    db.add(admin)

    cat = Category(name="pay-cat", slug="pay-cat", is_active=True)
    db.add(cat)
    db.flush()
    p1 = Product(category=cat, name="بيتزا دفع", slug="pay-pizza", base_price=Decimal("100"), is_active=True, is_available=True)
    db.add(p1)
    opt_pickup = DeliveryOption(name="استلام", fee=Decimal("0"), delivery_type="pickup", is_active=True, sort_order=1)
    db.add(opt_pickup)
    db.flush()
    db.commit()

    pid = p1.id
    pickup_id = opt_pickup.id

    print("\n== 1. الثلاث طرق مقبولة ==")

    for method, label in [("cash", "كاش"), ("ewallet", "محفظة إلكترونية"), ("card", "فيزا")]:
        r = client.post("/orders", json={
            "customer_name": "عميل",
            "customer_phone": "01000000001",
            "items": [{"product_id": pid, "quantity": 1}],
            "delivery_option_id": pickup_id,
            "payment_method": method,
        })
        check(f"{method} → 200", r.status_code == 200, r.text[:200])
        if r.status_code == 200:
            order_id = r.json()["order_id"]
            db2 = TestingSessionLocal()
            saved = db2.get(Order, order_id)
            check(f"{method} محفوظ في الـDB", saved.payment_method == method, str(saved.payment_method))
            check(f"رسالة واتساب بـ {label}", f"طريقة الدفع: {label}" in saved.whatsapp_message,
                  f"مشتاح في: {saved.whatsapp_message}")
            db2.close()

    print("\n== 2. قيمة غير صحيحة مرفوضة ==")

    r = client.post("/orders", json={
        "customer_name": "عميل",
        "customer_phone": "01000000002",
        "items": [{"product_id": pid, "quantity": 1}],
        "delivery_option_id": pickup_id,
        "payment_method": "bitcoin",
    })
    check("bitcoin → 422", r.status_code == 422, str(r.status_code))

    print("\n== 3. القيمة الافتراضية عند الإرسال بدونها ==")

    r = client.post("/orders", json={
        "customer_name": "عميل",
        "customer_phone": "01000000003",
        "items": [{"product_id": pid, "quantity": 1}],
        "delivery_option_id": pickup_id,
    })
    check("بدون payment_method → 200 (افتراضي cash)", r.status_code == 200, r.text[:200])
    if r.status_code == 200:
        order_id = r.json()["order_id"]
        db2 = TestingSessionLocal()
        saved = db2.get(Order, order_id)
        check("Default payment_method = cash", saved.payment_method == "cash", str(saved.payment_method))
        check("رسالة واتساب مشتقة من القيمة الافتراضية", "طريقة الدفع: كاش" in saved.whatsapp_message)
        db2.close()

    print("\n== 4. payment_method ينقذ مع الـOrder ==")

    r = client.post("/orders", json={
        "customer_name": "عميل",
        "customer_phone": "01000000004",
        "items": [{"product_id": pid, "quantity": 2}],
        "delivery_option_id": pickup_id,
        "payment_method": "ewallet",
    })
    check("طلب ewallet → 200", r.status_code == 200, r.text[:200])
    if r.status_code == 200:
        order_id = r.json()["order_id"]
        db2 = TestingSessionLocal()
        saved = db2.get(Order, order_id)
        check("payment_method محفوظ = ewallet", saved.payment_method == "ewallet", str(saved.payment_method))
        check("WhatsApp يوضح ewallet", "طريقة الدفع: محفظة إلكترونية" in saved.whatsapp_message)
        db2.close()

    print(f"\n== Summary ==")
    print(f"PASSED: {PASS}")
    print(f"FAILED: {FAIL}")

    if FAIL > 0:
        sys.exit(1)

finally:
    transaction.rollback()
    connection.close()
    app.dependency_overrides.clear()
