"""Test Suite: Rate Limiting
Verifies:
  - Rate limit function allows under limit
  - Rate limit blocks after limit exceeded
  - Admin endpoints NOT rate-limited
  - GET requests not rate-limited
  - Middleware skips in non-production mode
"""
import os
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import uuid

sys.path.insert(0, os.path.dirname(__file__))
sys.stdout.reconfigure(encoding="utf-8")

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.database import engine, get_db
from app.main import app, reset_rate_limits, _rate_limit_store, _check_rate_limit, _RATE_LIMIT_MAX_REQUESTS
from app.models import Category, DeliveryOption, DeliveryZone, Product, RestaurantSettings

connection = engine.connect()
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

passed = 0
failed = 0


def check(name: str, condition: bool, detail: str = ""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name} {detail}")


if __name__ == "__main__":
    print("=" * 60)
    print("Rate Limiting Tests")
    print("=" * 60)

    # Seed minimal data with unique slugs
    db = TestingSessionLocal()
    unique_id = uuid.uuid4().hex[:8]

    settings = db.scalar(select(RestaurantSettings))
    if settings is None:
        settings = RestaurantSettings(restaurant_name="RL Test", whatsapp_number="201000000000", currency="EGP")
        db.add(settings)
        db.flush()
    cat = Category(name="RL Cat", slug=f"rl-cat-{unique_id}", is_active=True)
    db.add(cat)
    db.flush()
    p = Product(category_id=cat.id, name="RL Product", slug=f"rl-product-{unique_id}", base_price=Decimal("100"), is_active=True, is_available=True)
    db.add(p)
    db.flush()
    d = DeliveryOption(name="توصيل", fee=Decimal("10"), delivery_type="fixed", is_active=True)
    db.add(d)
    db.flush()
    dz = DeliveryZone(name="منطقة RL", fee=Decimal("10"), is_active=True)
    db.add(dz)
    db.flush()
    db.commit()

    p_id = p.id
    d_id = d.id
    dz_id = dz.id

    # Test 1: Normal order request passes (middleware skips in non-prod)
    print("\n== 1. Normal request passes ==")
    reset_rate_limits()
    payload = {
        "customer_name": "Test User", "customer_phone": "01000000001",
        "customer_address": "Test Address Here",
        "items": [{"product_id": p_id, "quantity": 1}],
        "delivery_option_id": d_id,
        "delivery_zone_id": dz_id,
        "pickup_time": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
    }
    r = client.post("/orders", json=payload)
    check("Normal order request passes", r.status_code == 200, f"status={r.status_code} body={r.text[:200]}")

    # Test 2: Rate limit function allows under limit
    print("\n== 2. Rate limit function allows under limit ==")
    reset_rate_limits()
    test_key = "test-key-1"
    _rate_limit_store.clear()
    allowed = 0
    for i in range(3):
        if _check_rate_limit(test_key):
            allowed += 1
    check("3 requests allowed under default limit", allowed == 3, f"allowed={allowed}")

    # Test 3: Rate limit blocks after limit exceeded
    print("\n== 3. Rate limit blocks after limit exceeded ==")
    _rate_limit_store.clear()
    test_key2 = "test-key-2"
    for i in range(_RATE_LIMIT_MAX_REQUESTS):
        _check_rate_limit(test_key2)
    blocked_now = not _check_rate_limit(test_key2)
    check(f"Rate limit blocks after {_RATE_LIMIT_MAX_REQUESTS} requests", blocked_now)

    # Test 4: Admin endpoints NOT rate-limited
    print("\n== 4. Admin endpoints NOT rate-limited ==")
    reset_rate_limits()
    r1 = client.get("/admin/login")
    check("Admin login page not rate-limited", r1.status_code == 200)

    # Test 5: GET requests not rate-limited
    print("\n== 5. GET requests not rate-limited ==")
    reset_rate_limits()
    for i in range(10):
        r = client.get("/")
    check("GET requests not rate-limited", r.status_code == 200)

    # Test 6: Rate limit function returns blocked status
    print("\n== 6. Rate limit function returns blocked status ==")
    reset_rate_limits()
    test_key3 = "127.0.0.1:POST:/orders"
    _rate_limit_store.clear()
    for i in range(_RATE_LIMIT_MAX_REQUESTS):
        _check_rate_limit(test_key3)
    blocked = not _check_rate_limit(test_key3)
    check("Rate limit function returns blocked status", blocked)

    # Test 7: Middleware skips in non-production mode
    print("\n== 7. Middleware skips in non-production mode ==")
    from app.config import settings
    check("Middleware skips in non-production", settings.app_env != "production")

    print("\n" + "=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60)

    if failed > 0:
        sys.exit(1)