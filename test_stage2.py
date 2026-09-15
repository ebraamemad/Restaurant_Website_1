"""اختبارات المرحلة الثانية: البحث في الطلبات."""
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

admin = Admin(username="stage2-admin", password_hash=hash_password("stage2-pass"), full_name="Stage 2")
db.add(admin)

cat = Category(name="مرحلة2", slug="stage2-category", is_active=True)
db.add(cat)
db.flush()

product = Product(category=cat, name="منتج اختبار", slug="stage2-product", base_price=Decimal("100"), is_active=True, is_available=True)
db.add(product)
db.flush()

delivery = DeliveryOption(name="توصيل مرحلة2", fee=Decimal("10"), is_pickup=False, is_active=True)
db.add(delivery)
db.flush()

zone = DeliveryZone(name="منطقة مرحلة2", fee=Decimal("10"), is_active=True)
db.add(zone)
db.flush()

db.commit()

try:
    db = TestingSessionLocal()
    product = db.scalars(select(Product).where(Product.slug == "stage2-product")).first()
    delivery = db.scalars(select(DeliveryOption).where(DeliveryOption.name == "توصيل مرحلة2")).first()
    zone = db.scalars(select(DeliveryZone).where(DeliveryZone.name == "منطقة مرحلة2")).first()

    now = datetime.now(timezone.utc)

    payload1 = {
        "customer_name": "أحمد محمد",
        "customer_phone": "01012345678",
        "customer_address": "عنوان الاختبار",
        "items": [{"product_id": product.id, "quantity": 2}],
        "delivery_option_id": delivery.id,
        "delivery_zone_id": zone.id,
    }
    payload2 = {
        "customer_name": "محمد علي",
        "customer_phone": "+201234567890",
        "customer_address": "عنوان آخر",
        "items": [{"product_id": product.id, "quantity": 1}],
        "delivery_option_id": delivery.id,
        "delivery_zone_id": zone.id,
    }

    r1 = client.post("/orders", json=payload1)
    assert r1.status_code == 200
    order1_id = r1.json()["order_id"]

    r2 = client.post("/orders", json=payload2)
    assert r2.status_code == 200
    order2_id = r2.json()["order_id"]

    login_page = client.get("/admin/login")
    csrf = client.cookies.get("admin_csrf")
    login = client.post("/admin/login", data={"username": "stage2-admin", "password": "stage2-pass", "csrf_token": csrf}, follow_redirects=False)
    assert login.status_code == 303

    page = client.get("/admin/orders")
    assert page.status_code == 200
    assert "أحمد محمد" in page.text
    assert "محمد علي" in page.text

    page = client.get(f"/admin/orders?q={order1_id}")
    assert page.status_code == 200
    assert "أحمد محمد" in page.text
    assert "محمد علي" not in page.text

    page = client.get("/admin/orders?q=أحمد")
    assert page.status_code == 200
    assert "أحمد محمد" in page.text
    assert "محمد علي" not in page.text

    page = client.get("/admin/orders?q=01012345678")
    assert page.status_code == 200
    assert "أحمد محمد" in page.text

    page = client.get("/admin/orders?q=+201012345678")
    assert page.status_code == 200
    assert "أحمد محمد" in page.text

    page = client.get("/admin/orders?q=201012345678")
    assert page.status_code == 200
    assert "أحمد محمد" in page.text

    page = client.get("/admin/orders?q=201234567890")
    assert page.status_code == 200
    assert "محمد علي" in page.text

    page = client.get("/admin/orders?q=غير موجود")
    assert page.status_code == 200
    assert "أحمد محمد" not in page.text
    assert "محمد علي" not in page.text

    page = client.get("/admin/orders?q=أحمد&status_filter=pending")
    assert page.status_code == 200
    assert "أحمد محمد" in page.text

    today = now.strftime("%Y-%m-%d")
    page = client.get(f"/admin/orders?q=أحمد&date_from={today}&date_to={today}")
    assert page.status_code == 200
    assert "أحمد محمد" in page.text

    page = client.get(f"/admin/orders?status_filter=pending")
    assert page.status_code == 200
    assert "أحمد محمد" in page.text
    assert "محمد علي" in page.text

    page = client.get(f"/admin/orders?status_filter=delivered")
    assert page.status_code == 200
    assert "أحمد محمد" not in page.text
    assert "محمد علي" not in page.text

    page = client.get("/admin/orders?page=1")
    assert page.status_code == 200

    print("Stage 2: all checks passed")
finally:
    transaction.rollback()
    connection.close()
    app.dependency_overrides.clear()
