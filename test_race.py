"""اختبار race condition باستخدام TestClient لكن بدون savepoint transaction."""
import sys
sys.stdout.reconfigure(encoding="utf-8")

import threading
from decimal import Decimal
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.database import engine, get_db
from app.main import app
from app.models import (
    Admin, Category, Coupon, DeliveryOption, Product, RestaurantSettings,
)
from app.security import hash_password

now = datetime.now(timezone.utc)

# Create a new session factory WITHOUT savepoint mode
# This allows each request to have its own transaction
SL = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def og():
    s = SL()
    try:
        yield s
    finally:
        s.close()


# Don't override get_db - let TestClient use the normal one
# But we need to seed the database

# Seed using the normal session
sess = SL()
sess.add(RestaurantSettings(restaurant_name="X", whatsapp_number="201000000000", currency="EGP"))
sess.add(Admin(username="rc", password_hash=hash_password("p"), full_name="R"))
cat = Category(name="r", slug="rc-cat", is_active=True)
sess.add(cat)
sess.flush()
prod = Product(category=cat, name="P", slug="rc-p", base_price=Decimal("100"), is_active=True, is_available=True)
sess.add(prod)
sess.flush()
opt = DeliveryOption(name="D", fee=Decimal("10"), is_pickup=False, is_active=True)
sess.add(opt)
coupon = Coupon(
    code="RACE10", discount_type="percentage", discount_value=Decimal("10"),
    minimum_order=Decimal("0"),
    start_date=now - timedelta(days=1), end_date=now + timedelta(days=30),
    usage_limit=None, usage_count=0, is_active=True, first_order_only=True,
)
sess.add(coupon)
sess.commit()
sess.close()

PHONE = "01090000001"
results = []


def fire_request(name):
    c = TestClient(app)
    r = c.post("/orders", json={
        "customer_name": f"Customer {name}", "customer_phone": PHONE,
        "customer_address": "Address Test 123 Street", "items": [{"product_id": prod.id, "quantity": 1}],
        "delivery_option_id": opt.id, "coupon_code": "RACE10",
    })
    results.append((name, r.status_code, r.text[:120]))


t1 = threading.Thread(target=fire_request, args=("A",))
t2 = threading.Thread(target=fire_request, args=("B",))

t1.start()
t2.start()
t1.join()
t2.join()

print("Results:")
for n, code, text_ in results:
    print(f"  {n}: status={code} body={text_}")

successes = [r for r in results if r[1] == 200]
print(f"\nSuccesses: {len(successes)}/2")
if len(successes) == 1:
    print("Race condition prevented: one order succeeded, the other rejected")
elif len(successes) == 2:
    print("RACE CONDITION NOT PREVENTED: BOTH ORDERS SUCCEEDED")
else:
    print("Unexpected: no successes")

# Cleanup
with engine.connect() as conn:
    trans = conn.begin()
    try:
        conn.execute(text("DELETE FROM order_item_addons WHERE order_item_id IN (SELECT id FROM order_items WHERE order_id IN (SELECT id FROM orders WHERE customer_phone = '01090000001' OR customer_phone LIKE '201090000001' OR customer_phone LIKE '+201090000001'))"))
        conn.execute(text("DELETE FROM order_item_options WHERE order_item_id IN (SELECT id FROM order_items WHERE order_id IN (SELECT id FROM orders WHERE customer_phone = '01090000001' OR customer_phone LIKE '201090000001' OR customer_phone LIKE '+201090000001'))"))
        conn.execute(text("DELETE FROM order_items WHERE order_id IN (SELECT id FROM orders WHERE customer_phone = '01090000001' OR customer_phone LIKE '201090000001' OR customer_phone LIKE '+201090000001')"))
        conn.execute(text("DELETE FROM orders WHERE customer_phone = '01090000001' OR customer_phone LIKE '201090000001' OR customer_phone LIKE '+201090000001'"))
        conn.execute(text("DELETE FROM customers WHERE phone = '1090000001'"))
        conn.execute(text("DELETE FROM coupons WHERE code = 'RACE10'"))
        conn.execute(text("DELETE FROM products WHERE slug = 'rc-p'"))
        conn.execute(text("DELETE FROM categories WHERE slug = 'rc-cat'"))
        conn.execute(text("DELETE FROM delivery_options WHERE name = 'D'"))
        conn.execute(text("DELETE FROM restaurant_settings WHERE restaurant_name = 'X'"))
        conn.execute(text("DELETE FROM admins WHERE username = 'rc'"))
        trans.commit()
    except Exception:
        trans.rollback()
        raise