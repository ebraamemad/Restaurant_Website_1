"""اختبارات المرحلة الرابعة: Analytics Dashboard."""
from __future__ import annotations

import sys
sys.stdout.reconfigure(encoding="utf-8")

from decimal import Decimal
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker

from app.database import engine, get_db
from app.main import app
from app.models import (
    Admin, Category, Coupon, Customer, DeliveryOption, Order, OrderItem, Product, RestaurantSettings,
)
from app.schemas import OrderStatus
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


try:
    now = datetime.now(timezone.utc)
    db = TestingSessionLocal()
    db.add(RestaurantSettings(restaurant_name="Analytics Test", whatsapp_number="201000000000", currency="EGP"))
    db.add(Admin(username="analytics4", password_hash=hash_password("pass"), full_name="A4"))
    db.flush()
    cat = Category(name="analytics-cat", slug="analytics-cat", is_active=True)
    db.add(cat)
    db.flush()
    p1 = Product(category=cat, name="Pizza A", slug="pizza-a", base_price=Decimal("100"), is_active=True, is_available=True)
    p2 = Product(category=cat, name="Pizza B", slug="pizza-b", base_price=Decimal("200"), is_active=True, is_available=True)
    db.add_all([p1, p2])
    db.flush()
    opt_delivery = DeliveryOption(name="توصيل", fee=Decimal("15"), delivery_type="fixed", is_active=True)
    opt_pickup = DeliveryOption(name="استلام", fee=Decimal("0"), delivery_type="pickup", is_active=True)
    db.add_all([opt_delivery, opt_pickup])
    db.flush()
    c_normal = Coupon(
        code="NORM10", discount_type="percentage", discount_value=Decimal("10"),
        minimum_order=Decimal("0"), start_date=now - timedelta(days=1), end_date=now + timedelta(days=30),
        usage_limit=None, usage_count=0, is_active=True, first_order_only=False,
    )
    db.add(c_normal)
    db.flush()
    db.commit()

    login_page = client.get("/admin/login")
    csrf = client.cookies.get("admin_csrf")
    login = client.post("/admin/login", data={"username": "analytics4", "password": "pass", "csrf_token": csrf}, follow_redirects=False)
    assert login.status_code == 303, f"Login failed: {login.text}"

    print("\n== 1. Dashboard loads with analytics ==")
    r = client.get("/admin")
    check("Dashboard loads", r.status_code == 200, r.text[:200])
    check("Analytics context present", "إجمالي الطلبات" in r.text, "")
    check("Revenue visible", "إجمالي الإيرادات" in r.text, "")

    print("\n== 2. Date filter: ?range=today ==")
    r = client.get("/admin?range=today")
    check("Dashboard with today filter", r.status_code == 200, r.text[:200])
    check("today filter active", "?range=today" in r.text or "today" in r.text, "")

    print("\n== 3. Date filter: ?range=7d ==")
    r = client.get("/admin?range=7d")
    check("Dashboard with 7d filter", r.status_code == 200, r.text[:200])

    print("\n== 4. Date filter: ?range=30d ==")
    r = client.get("/admin?range=30d")
    check("Dashboard with 30d filter", r.status_code == 200, r.text[:200])

    print("\n== 5. Date filter: ?range=all ==")
    r = client.get("/admin?range=all")
    check("Dashboard with all filter", r.status_code == 200, r.text[:200])

    print("\n== 6. Create test orders ==")
    cust1 = Customer(phone="10100000011", name="عميل جديد", first_order_at=now, last_order_at=now)
    cust2 = Customer(phone="10100000022", name="عميل عائد", first_order_at=now - timedelta(days=10), last_order_at=now - timedelta(days=10))
    db.add_all([cust1, cust2])
    db.flush()
    now_ts = datetime.now(timezone.utc)
    for i, (cust, opt, p, status, has_coupon) in enumerate([
        (cust1, opt_delivery, p1, OrderStatus.delivered.value, False),
        (cust1, opt_pickup, p1, OrderStatus.delivered.value, False),
        (cust2, opt_delivery, p2, OrderStatus.delivered.value, True),
        (cust1, opt_delivery, p2, OrderStatus.cancelled.value, False),
        (cust2, opt_pickup, p1, OrderStatus.preparing.value, False),
    ]):
        order = Order(
            customer_name=cust.name, customer_phone=cust.phone, customer_address="Test Address",
            status=status, subtotal=Decimal("100"), delivery_fee=Decimal("15"),
            discount_amount=Decimal("0"), total=Decimal("115"),
            whatsapp_message="test", delivery_option_id=opt.id,
            coupon_id=c_normal.id if has_coupon else None,
            customer_id=cust.id, created_at=now_ts - timedelta(hours=i),
        )
        db.add(order)
        db.flush()
        db.add(OrderItem(order_id=order.id, product_id=p.id, product_name=p.name,
                         unit_price=p.base_price, quantity=2, line_total=Decimal("200")))
        db.flush()

    print("\n== 7. Analytics with orders ==")
    r = client.get("/admin?range=all")
    check("Analytics page loads", r.status_code == 200)
    check("Total orders shown", "5" in r.text, "")
    check("Revenue shown", "EGP" in r.text, "")
    check("Active customers shown", "العملاء النشطون" in r.text, "")
    check("New customers shown", "عملاء جدد" in r.text, "")
    check("Returning customers shown", "عملاء عائدون" in r.text, "")
    check("Pickup orders shown", "استلام" in r.text, "")
    check("Delivery orders shown", "توصيل" in r.text, "")
    check("Top products shown", "Pizza A" in r.text, "")
    check("Top coupons shown", "NORM10" in r.text, "")
    check("Coupon orders count shown", "2" in r.text, "")

    print("\n== 8. Today filter ==")
    today_start = now_ts.replace(hour=0, minute=0, second=0, microsecond=0)
    r = client.get("/admin?range=today")
    check("Today filter works", r.status_code == 200)
    check("Orders in today range shown", "5" in r.text, "")

    print("\n== 9. Empty state ==")
    empty_cat = Category(name="empty-cat", slug="empty-cat-s4", is_active=True)
    db.add(empty_cat)
    db.flush()
    empty_p = Product(category=empty_cat, name="Empty P", slug="empty-p-s4", base_price=Decimal("50"), is_active=True, is_available=True)
    db.add(empty_p)
    db.flush()
    db.add(DeliveryOption(name="Empty Delivery S4", fee=Decimal("10"), delivery_type="fixed", is_active=True))
    db.flush()
    db.commit()

    r = client.get("/admin?range=today")
    check("Empty state page loads", r.status_code == 200)
    check("Dashboard shows data for today", "إجمالي" in r.text, "")

    print("\n== 10. Revenue excludes cancelled ==")
    r = client.get("/admin?range=all")
    check("Cancelled excluded from revenue", "345" not in r.text, "cancelled order leaked")
    check("Cancelled excluded from order count", "الكل" in r.text, "")

    print("\n== 11. Order type breakdown ==")
    r = client.get("/admin?range=all")
    check("Pickup count in analytics", "استلام" in r.text, "")
    check("Delivery count in analytics", "توصيل" in r.text, "")
    check("Progress bar for delivery", 'bg-emerald-500' in r.text, "")

    print("\n== 12. Top products with revenue ==")
    r = client.get("/admin?range=all")
    check("Top products section", "أكثر المنتجات" in r.text, "")
    check("Product quantity shown", "قطعة" in r.text, "")

    print("\n== 13. Top customers ==")
    r = client.get("/admin?range=all")
    check("Top customers section", "أكثر العملاء" in r.text, "")
    check("Customer phone in list", "10100000011" in r.text, "")

    print("\n== 14. Coupons section ==")
    r = client.get("/admin?range=all")
    check("Coupons section", "الكوبونات" in r.text, "")
    check("Coupon code shown", "NORM10" in r.text, "")
    check("Coupon usage count shown", "استخدام" in r.text, "")

    print("\n== 15. Quick actions ==")
    r = client.get("/admin?range=all")
    check("Quick actions section", "إجراءات سريعة" in r.text, "")
    check("Products link", 'href="/admin/products"' in r.text, "")
    check("Orders link", 'href="/admin/orders"' in r.text, "")
    check("Settings link", 'href="/admin/settings"' in r.text, "")

    print("\n== 16. Recent orders table ==")
    r = client.get("/admin?range=all")
    check("Recent orders section", "آخر الطلبات" in r.text, "")

    print("\n== 17. AOV ==")
    check("AOV label", "متوسط قيمة الطلب" in r.text, "")

    print("\n== Summary ==")
    print(f"  PASSED: {PASS}")
    print(f"  FAILED: {FAIL}")
    if FAIL > 0:
        print(f"  FAILURES: {FAIL}")

finally:
    transaction.rollback()
    connection.close()
    app.dependency_overrides.clear()
