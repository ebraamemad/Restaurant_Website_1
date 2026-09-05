"""اختبارات المرحلة الثالثة: First-Order-Only Coupon."""
from __future__ import annotations

import sys
sys.stdout.reconfigure(encoding="utf-8")

from decimal import Decimal
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.database import engine, get_db
from app.main import app
from app.models import (
    Admin, Category, Coupon, Customer, DeliveryOption, Order, Product, RestaurantSettings,
)
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


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {extra}")


def make_payload(phone: str, coupon_code: str = None, address: str = "عنوان الاختبار") -> dict:
    p = {
        "customer_name": "عميل اختبار",
        "customer_phone": phone,
        "customer_address": address,
        "items": [{"product_id": None, "quantity": 2}],
        "delivery_option_id": None,
    }
    if coupon_code:
        p["coupon_code"] = coupon_code
    return p


try:
    now = datetime.now(timezone.utc)

    db = TestingSessionLocal()
    settings = db.scalar(select(RestaurantSettings))
    if settings is None:
        settings = RestaurantSettings(restaurant_name="مطعم الاختبار", whatsapp_number="201000000000", currency="EGP")
        db.add(settings)

    admin = Admin(username="stage3-admin", password_hash=hash_password("stage3-pass"), full_name="Stage 3")
    db.add(admin)

    cat = Category(name="مرحلة3", slug="stage3-cat", is_active=True)
    db.add(cat)
    db.flush()

    product = Product(
        category=cat, name="منتج اختبار", slug="stage3-product",
        base_price=Decimal("100"), is_active=True, is_available=True,
    )
    db.add(product)
    db.flush()

    delivery = DeliveryOption(name="توصيل3", fee=Decimal("10"), is_pickup=False, is_active=True)
    db.add(delivery)
    db.flush()

    normal_coupon = Coupon(
        code="NORMAL10", discount_type="percentage", discount_value=Decimal("10"),
        minimum_order=Decimal("0"), start_date=now - timedelta(days=1), end_date=now + timedelta(days=30),
        usage_limit=None, usage_count=0, is_active=True, first_order_only=False,
    )
    db.add(normal_coupon)

    first_coupon = Coupon(
        code="FIRSTONLY", discount_type="percentage", discount_value=Decimal("15"),
        minimum_order=Decimal("0"), start_date=now - timedelta(days=1), end_date=now + timedelta(days=30),
        usage_limit=None, usage_count=0, is_active=True, first_order_only=True,
    )
    db.add(first_coupon)

    first_min = Coupon(
        code="FIRSTMIN", discount_type="fixed", discount_value=Decimal("20"),
        minimum_order=Decimal("50"), start_date=now - timedelta(days=1), end_date=now + timedelta(days=30),
        usage_limit=None, usage_count=0, is_active=True, first_order_only=True,
    )
    db.add(first_min)

    db.commit()

    db = TestingSessionLocal()
    product = db.scalars(select(Product).where(Product.slug == "stage3-product")).first()
    delivery = db.scalars(select(DeliveryOption).where(DeliveryOption.name == "توصيل3")).first()

    def make(phone, coupon=None):
        p = make_payload(phone, coupon)
        p["items"][0]["product_id"] = product.id
        p["delivery_option_id"] = delivery.id
        return p

    print("== 1. Normal coupon works as before (first_order_only=false) ==")
    r = client.post("/orders", json=make("01030000011", "NORMAL10"))
    check("NORMAL10 بدون طلب سابق -> 200", r.status_code == 200, r.text[:100])
    if r.status_code == 200:
        check("الخصم = 10% من 200 = 20", r.json()["discount_amount"] == "20.00", r.json()["discount_amount"])

    print("== 2. First-order coupon with no previous order -> PASS ==")
    r = client.post("/orders", json=make("01030000012", "FIRSTONLY"))
    check("FIRSTONLY بدون طلب سابق -> 200", r.status_code == 200, r.text[:100])
    if r.status_code == 200:
        check("الخصم = 15% من 200 = 30", r.json()["discount_amount"] == "30.00", r.json()["discount_amount"])

    print("== 3. First-order coupon after previous order -> REJECT ==")
    r = client.post("/orders", json=make("01030000012", "FIRSTONLY"))
    check("FIRSTONLY مع طلب سابق -> 400", r.status_code == 400, f"got {r.status_code}")
    check("رسالة الخطأ تحتوي 'أول فقط' أو 'سابق'", "أول" in r.json()["detail"] or "سابق" in r.json()["detail"], r.text)

    print("== 4. Phone normalization: different formats of same number ==")
    # All three formats normalize to the same number (20130000012)
    r = client.post("/orders", json=make("01030000013", "FIRSTONLY"))
    check("01030000013 (صحيحة) -> 200", r.status_code == 200, f"got {r.status_code}")
    r = client.post("/orders", json=make("+201030000013", "FIRSTONLY"))
    check("+201030000013 (نفس الرقم) -> 400", r.status_code == 400, f"got {r.status_code}")
    r = client.post("/orders", json=make("201030000013", "FIRSTONLY"))
    check("201030000013 (نفس الرقم) -> 400", r.status_code == 400, f"got {r.status_code}")

    print("== 5. Normal coupon works for any order (existing or new) ==")
    r = client.post("/orders", json=make("01030000012", "NORMAL10"))
    check("NORMAL10 مع طلب سابق -> 200", r.status_code == 200, f"got {r.status_code}")
    if r.status_code == 200:
        check("الخصم = 10% = 20", r.json()["discount_amount"] == "20.00", r.json()["discount_amount"])

    print("== 6. API preview with first-order coupon ==")
    r_new = client.post("/api/orders/preview", json={
        "items": [{"product_id": product.id, "quantity": 1}],
        "coupon_code": "FIRSTONLY",
        "customer_phone": "01030000014",
    })
    check("API preview FIRSTONLY + عميل جديد -> 200", r_new.status_code == 200, f"got {r_new.status_code}")

    r_existing = client.post("/api/orders/preview", json={
        "items": [{"product_id": product.id, "quantity": 1}],
        "coupon_code": "FIRSTONLY",
        "customer_phone": "01030000013",
    })
    check("API preview FIRSTONLY + عميل موجود -> 400", r_existing.status_code == 400, f"got {r_existing.status_code}")

    print("== 7. Pricing is correct for both coupon types ==")
    r_normal = client.post("/orders", json=make("01030000015", "NORMAL10"))
    check("NORMAL10 يحسب الخصم = 10% = 20", r_normal.status_code == 200 and r_normal.json()["discount_amount"] == "20.00", r_normal.text[:100])

    r_first = client.post("/orders", json=make("01030000016", "FIRSTONLY"))
    check("FIRSTONLY يحسب الخصم = 15% = 30", r_first.status_code == 200 and r_first.json()["discount_amount"] == "30.00", r_first.text[:100])

    print("== 8. Minimum order check still works for first-order coupon ==")
    r_low = client.post("/orders", json={
        "customer_name": "عميل جديد", "customer_phone": "01030000017",
        "customer_address": "عنوان", "items": [{"product_id": product.id, "quantity": 1}],
        "delivery_option_id": delivery.id, "coupon_code": "FIRSTMIN",
    })
    check("FIRSTMIN مع حد أدنى 50 لكن الطلب 100 -> 200 (يكفي)", r_low.status_code == 200, f"got {r_low.status_code}")

    print("== 9. Cancelled orders don't count for first-order check ==")
    r_new = client.post("/orders", json=make("01030000018", "FIRSTONLY"))
    check("طلب جديد برقم جديد -> 200", r_new.status_code == 200, f"got {r_new.status_code}")
    order_id = r_new.json()["order_id"]
    db = TestingSessionLocal()
    cancelled = db.get(Order, order_id)
    cancelled.status = "cancelled"
    db.add(cancelled)
    db.commit()
    r_after = client.post("/orders", json=make("01030000018", "FIRSTONLY"))
    check("بعد إلغاء الطلب -> 200 (مش counting)", r_after.status_code == 200, f"got {r_after.status_code}")

    print("== 10. Backend rejects bypass attempt ==")
    r_bypass = client.post("/orders", json=make("01030000012", "FIRSTONLY"))
    check("first-order coupon لعميل موجود -> 400", r_bypass.status_code == 400, f"got {r_bypass.status_code}")

    print("== 11. Usage count increments correctly ==")
    db = TestingSessionLocal()
    first_c = db.scalars(select(Coupon).where(Coupon.code == "FIRSTONLY")).first()
    check("FIRSTONLY usage_count >= 1", first_c.usage_count >= 1, f"got {first_c.usage_count}")
    normal_c = db.scalars(select(Coupon).where(Coupon.code == "NORMAL10")).first()
    check("NORMAL10 usage_count >= 1", normal_c.usage_count >= 1, f"got {normal_c.usage_count}")

    print("== 12. Admin creates coupon with first_order_only ==")
    login_page = client.get("/admin/login")
    csrf = client.cookies.get("admin_csrf")
    login = client.post("/admin/login", data={"username": "stage3-admin", "password": "stage3-pass", "csrf_token": csrf}, follow_redirects=False)
    check("Admin login -> 303", login.status_code == 303)

    coupons_page = client.get("/admin/coupons")
    check("Admin coupons page -> 200", coupons_page.status_code == 200)

    csrf = client.cookies.get("admin_csrf")
    start_dt = (now + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
    end_dt = (now + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M")
    create = client.post("/admin/coupons", data={
        "code": "ADMINTEST", "discount_type": "percentage", "discount_value": "20",
        "minimum_order": "0", "start_date": start_dt, "end_date": end_dt,
        "is_active": "true", "first_order_only": "true", "csrf_token": csrf,
    }, follow_redirects=False)
    check("Admin create first-order coupon -> 303", create.status_code == 303)

    coupons_after = client.get("/admin/coupons")
    check("Admin sees 'ADMINTEST' coupon", "ADMINTEST" in coupons_after.text)
    check("Admin sees 'first_order_only' checkbox", 'للطلب الأول فقط' in coupons_after.text or 'first_order_only' in coupons_after.text)

    print("== Summary ==")
    print(f"  PASSED: {PASS}")
    print(f"  FAILED: {FAIL}")

finally:
    transaction.rollback()
    connection.close()
    app.dependency_overrides.clear()
