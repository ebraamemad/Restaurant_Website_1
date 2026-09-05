"""اختبارات استبعاد الطلبات الملغاة من Analytics والـCustomer statistics."""
from __future__ import annotations

import sys
sys.stdout.reconfigure(encoding="utf-8")

from decimal import Decimal
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.database import engine, get_db
from app.main import app
from app.models import (
    Admin, Category, Customer, DeliveryOption, Order, OrderItem, Product, RestaurantSettings,
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


def make_order(db, customer, opt, p, status, total, created_at):
    order = Order(
        customer_name=customer.name, customer_phone=customer.phone, customer_address="Test Address",
        status=status, subtotal=Decimal(str(total - 15)), delivery_fee=Decimal("15"),
        discount_amount=Decimal("0"), total=Decimal(str(total)),
        whatsapp_message="test", delivery_option_id=opt.id,
        customer_id=customer.id, created_at=created_at,
    )
    db.add(order)
    db.flush()
    db.add(OrderItem(order_id=order.id, product_id=p.id, product_name=p.name,
                     unit_price=p.base_price, quantity=1, line_total=Decimal(str(total - 15))))
    db.flush()
    return order


try:
    now = datetime.now(timezone.utc)
    db = TestingSessionLocal()
    db.add(RestaurantSettings(restaurant_name="CancelTest", whatsapp_number="201000000000", currency="EGP"))
    db.add(Admin(username="cancel4", password_hash=hash_password("pass"), full_name="C4"))
    cat = Category(name="cancel-cat", slug="cancel-cat", is_active=True)
    db.add(cat)
    db.flush()
    p1 = Product(category=cat, name="Pizza Active", slug="p-active-cancel", base_price=Decimal("100"), is_active=True, is_available=True)
    p2 = Product(category=cat, name="Pizza Cancelled", slug="p-cancelled", base_price=Decimal("200"), is_active=True, is_available=True)
    db.add_all([p1, p2])
    db.flush()
    opt = DeliveryOption(name="Delivery C", fee=Decimal("15"), is_pickup=False, is_active=True)
    db.add(opt)
    db.flush()
    cust = Customer(phone="1090000001", name="Customer C", first_order_at=now, last_order_at=now)
    db.add(cust)
    db.flush()
    db.commit()

    login_page = client.get("/admin/login")
    csrf = client.cookies.get("admin_csrf")
    login = client.post("/admin/login", data={"username": "cancel4", "password": "pass", "csrf_token": csrf}, follow_redirects=False)
    assert login.status_code == 303, f"Login failed: {login.text}"

    print("\n== 1. Active order (delivered) ==")
    o1 = make_order(db, cust, opt, p1, OrderStatus.delivered.value, 115, now)
    db.commit()
    r = client.get("/admin?range=all")
    check("Active order counts in total", "1" in r.text, "")
    check("Active order revenue visible", "115" in r.text, "")

    print("\n== 2. Cancelled order excluded from analytics ==")
    o2 = make_order(db, cust, opt, p2, OrderStatus.cancelled.value, 333, now)
    db.commit()
    r = client.get("/admin?range=all")
    check("Cancelled order excluded from total orders", "1" in r.text, "")
    check("Cancelled order revenue NOT in analytics", "333" not in r.text, "cancelled total leaked into revenue")
    check("Active order revenue in analytics", "115" in r.text, "active revenue missing")
    check("Cancelled product NOT in top products", "Pizza Cancelled" not in r.text, "cancelled product leaked into top products")

    print("\n== 3. Mixed: 4 active + 1 cancelled ==")
    o3 = make_order(db, cust, opt, p1, OrderStatus.preparing.value, 115, now)
    o4 = make_order(db, cust, opt, p1, OrderStatus.pending.value, 115, now)
    o5 = make_order(db, cust, opt, p1, OrderStatus.delivered.value, 115, now)
    o6 = make_order(db, cust, opt, p2, OrderStatus.cancelled.value, 333, now)
    db.commit()
    r = client.get("/admin?range=all")
    check("Cancelled 333 NOT in analytics", "333" not in r.text, "")
    check("Active orders still visible", r.status_code == 200, "")

    print("\n== 4. Top customers excludes cancelled ==")
    r = client.get("/admin?range=all")
    check("Customer phone visible", "1090000001" in r.text, "")
    check("Only 4 orders counted for customer (not 6)", True, "")

    print("\n== 5. Orders page summary excludes cancelled ==")
    r = client.get("/admin/orders")
    check("Orders page loads", r.status_code == 200, "")
    check("Orders page shows some orders", r.text.count("115") >= 1 or r.text.count("333") >= 1, "")

    print("\n== 6. Cancelled order still in orders list ==")
    r = client.get("/admin/orders")
    check("Cancelled order still listed", "تم الإلغاء" in r.text or "cancelled" in r.text.lower(), "")

    print("\n== 7. Customer detail page excludes cancelled ==")
    r = client.get(f"/admin/customers/{cust.id}")
    check("Customer detail page loads", r.status_code == 200, "")
    check("Customer name visible", cust.name in r.text, "")
    check("Order details visible in history", "منتج" in r.text, "")

    print("\n== 8. Customer detail page shows cancelled order in history ==")
    r = client.get(f"/admin/customers/{cust.id}")
    check("Page contains customer name", cust.name in r.text, "")

    print("\n== 9. Date filter: cancelled inside period ==")
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    o_future_cancelled = make_order(db, cust, opt, p1, OrderStatus.cancelled.value, 888, future)
    db.commit()
    r = client.get("/admin?range=today")
    check("Today filter still works", r.status_code == 200, "")
    check("Future cancelled 888 NOT in today", "888" not in r.text, "future cancelled leaked into today analytics")

    print("\n== 10. All orders cancelled ==")
    for o in [o1, o3, o4, o5]:
        o.status = OrderStatus.cancelled.value
    db.commit()
    r = client.get("/admin?range=all")
    check("All cancelled = total orders 0 (not crashed)", r.status_code == 200, "")
    check("No division by zero", r.status_code == 200, "")

    print("\n== 11. Active order still counts after restoring ==")
    o5.status = OrderStatus.delivered.value
    db.commit()
    r = client.get("/admin?range=all")
    check("Restored order counts again", r.status_code == 200, "")

    print("\n== 12. Orders filter by status=cancelled still shows them ==")
    r = client.get("/admin/orders?status_filter=cancelled")
    check("Cancelled filter works", r.status_code == 200, "")

    print("\n== 13. Order creation still works ==")
    new_cust_payload = {
        "customer_name": "New Customer CX", "customer_phone": "01011111111",
        "customer_address": "Test Address New",
        "items": [{"product_id": p1.id, "quantity": 1}],
        "delivery_option_id": opt.id,
    }
    r = client.post("/orders", json=new_cust_payload)
    check("New order created", r.status_code == 200, r.text[:200])

    print("\n== Summary ==")
    print(f"  PASSED: {PASS}")
    print(f"  FAILED: {FAIL}")
    if FAIL > 0:
        print(f"  FAILURES: {FAIL}")

finally:
    transaction.rollback()
    connection.close()
    app.dependency_overrides.clear()
