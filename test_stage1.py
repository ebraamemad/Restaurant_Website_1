"""Stage 1: Customer detail page tests."""
from decimal import Decimal
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.database import engine, get_db
from app.main import app
from app.models import Admin, Category, Customer, DeliveryOption, DeliveryZone, Order, Product, RestaurantSettings
from app.security import hash_password

connection = engine.connect()
transaction = connection.begin()
TestingSessionLocal = sessionmaker(bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)
db = TestingSessionLocal()

settings = db.scalar(select(RestaurantSettings))
if settings is None:
    settings = RestaurantSettings(restaurant_name="مطعم الاختبار", whatsapp_number="201000000000", currency="EGP")
    db.add(settings)
    db.flush()

admin = Admin(username="stage1-admin", password_hash=hash_password("stage1-pass"), full_name="Stage 1")
db.add(admin)

cat = Category(name="مرحلة1", slug="stage1-category", is_active=True)
db.add(cat)
db.flush()

product = Product(category=cat, name="product اختبار1", slug="stage1-product1", base_price=Decimal("100"), is_active=True, is_available=True)
product2 = Product(category=cat, name="product اختبار2", slug="stage1-product2", base_price=Decimal("50"), is_active=True, is_available=True)
db.add_all([product, product2])
db.flush()

delivery = DeliveryOption(name="توصيل مرحلة1", fee=Decimal("10"), delivery_type="fixed", is_active=True)
db.add(delivery)
db.flush()

zone = DeliveryZone(name="منطقة مرحلة1", fee=Decimal("10"), is_active=True)
db.add(zone)
db.flush()

db.commit()

try:
    phone = "01012345678"
    now = datetime.now(timezone.utc)

    payload1 = {
        "customer_name": "عميل8",
        "customer_phone": phone,
        "customer_address": "عنوان الاختبار1",
        "items": [{"product_id": product.id, "quantity": 2}],
        "delivery_option_id": delivery.id,
        "delivery_zone_id": zone.id,
    }
    payload2 = {
        "customer_name": "عميل8",
        "customer_phone": phone,
        "customer_address": "عنوان الاختبار2",
        "items": [{"product_id": product2.id, "quantity": 3}],
        "delivery_option_id": delivery.id,
        "delivery_zone_id": zone.id,
    }

    r1 = client.post("/orders", json=payload1)
    assert r1.status_code == 200, f"Order 1 failed: {r1.text}"
    order1_id = r1.json()["order_id"]

    r2 = client.post("/orders", json=payload2)
    assert r2.status_code == 200, f"Order 2 failed: {r2.text}"
    order2_id = r2.json()["order_id"]

    customer = db.scalar(select(Customer).where(Customer.phone == "1012345678"))
    assert customer is not None, "Customer should be created"

    login_page = client.get("/admin/login")
    csrf = client.cookies.get("admin_csrf")
    login = client.post("/admin/login", data={"username": "stage1-admin", "password": "stage1-pass", "csrf_token": csrf}, follow_redirects=False)
    assert login.status_code == 303, "Admin login should redirect"

    detail_page = client.get(f"/admin/customers/{customer.id}")
    assert detail_page.status_code == 200, f"Customer detail page failed: {detail_page.status_code}"
    assert "1012345678" in detail_page.text, "Phone number should be in detail page"
    assert "عميل8" in detail_page.text, "Customer name should be in detail page"
    assert "إجمالي الطلبات" in detail_page.text, "Should show total orders label"
    assert "إجمالي المشتريات" in detail_page.text, "Should show total spent label"
    assert "أول طلب" in detail_page.text, "Should show first order label"
    assert "آخر طلب" in detail_page.text, "Should show last order label"
    assert "متوسط قيمة الطلب" in detail_page.text, "Should show average order value label"
    assert "تواصل عبر WhatsApp" in detail_page.text, "Should show WhatsApp button"
    assert f"#{order1_id}" in detail_page.text, f"Order 1 ID #{order1_id} should be in page"
    assert f"#{order2_id}" in detail_page.text, f"Order 2 ID #{order2_id} should be in page"
    assert "قيد الانتظار" in detail_page.text or "pending" in detail_page.text.lower(), "Order status should be visible"

    not_found = client.get("/admin/customers/999999")
    assert not_found.status_code == 404, "Non-existent customer should return 404"

    print("Stage 1: all checks passed")
finally:
    transaction.rollback()
    connection.close()
    app.dependency_overrides.clear()