"""اختبارات المرحلة الثانية داخل transaction مع rollback."""
from decimal import Decimal
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.database import engine, get_db
from app.main import app
from app.models import Admin, Category, Customer, DeliveryOption, DeliveryZone, Product, ProductOption, ProductOptionGroup
from app.security import hash_password

connection = engine.connect()
transaction = connection.begin()
Session = sessionmaker(bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False)

def override_get_db():
    db = Session()
    try:
        yield db
    finally:
        db.close()

app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)
db = Session()
admin = Admin(username="phase2-admin", password_hash=hash_password("phase2-pass"), full_name="Phase 2")
cat = Category(name="مرحلة ثانية", slug="phase2-category", is_active=True)
product = Product(category=cat, name="منتج سعره حسب الخيار", slug="phase2-product", base_price=Decimal("0"), is_active=True, is_available=True)
db.add_all([admin, cat, product])
db.flush()
group = ProductOptionGroup(product_id=product.id, name="الحجم", is_required=True, allow_multiple=False, is_available=True)
db.add(group)
db.flush()
option = ProductOption(product_id=product.id, group_id=group.id, name="كبير", price_adjustment=Decimal("50"), is_available=True)
db.add(option)
delivery = DeliveryOption(name="Phase2 توصيل", fee=Decimal("10"), is_pickup=False, is_active=True)
zone = DeliveryZone(name="Phase2 الفيوم", fee=Decimal("20"), is_active=True)
db.add_all([delivery, zone])
db.commit()

try:
    no_option = client.post("/api/orders/preview", json={"items": [{"product_id": product.id, "quantity": 1}], "delivery_option_id": delivery.id})
    assert no_option.status_code == 400
    preview = client.post("/api/orders/preview", json={"items": [{"product_id": product.id, "quantity": 1, "option_ids": [option.id]}], "delivery_option_id": delivery.id, "delivery_zone_id": zone.id})
    assert preview.status_code == 200 and preview.json()["subtotal"] == "50.00" and preview.json()["delivery_fee"] == "20.00"
    payload = {"customer_name": "عميل المرحلة", "customer_phone": "01099999999", "customer_address": "شارع الاختبار", "items": [{"product_id": product.id, "quantity": 1, "option_ids": [option.id]}], "delivery_option_id": delivery.id, "delivery_zone_id": zone.id}
    first = client.post("/orders", json=payload)
    second = client.post("/orders", json=payload)
    assert first.status_code == 200 and second.status_code == 200
    customers = db.scalars(select(Customer).where(Customer.phone == "01099999999")).all()
    assert len(customers) == 1
    assert db.scalar(select(Product).where(Product.id == product.id)).base_price == Decimal("0.00")
    assert client.get("/api/delivery-zones").status_code == 200
    assert client.post("/api/orders/preview", json={"items": [{"product_id": product.id, "quantity": 1, "option_ids": [option.id]}], "delivery_option_id": delivery.id}).status_code == 400
    login_page = client.get("/admin/login")
    csrf = client.cookies.get("admin_csrf")
    login = client.post("/admin/login", data={"username": "phase2-admin", "password": "phase2-pass", "csrf_token": csrf}, follow_redirects=False)
    assert login.status_code == 303
    customers_page = client.get("/admin/customers")
    assert customers_page.status_code == 200 and "01099999999" in customers_page.text
    export = client.get("/admin/customers/export")
    assert export.status_code == 200 and "رقم الهاتف" in export.text and export.headers["content-type"].startswith("text/csv")
    print("Phase 2: all checks passed")
finally:
    transaction.rollback()
    connection.close()
    app.dependency_overrides.clear()
