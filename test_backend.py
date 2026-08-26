"""
اختبار Backend فقط — من غير لمس الـFrontend.

بيشتغل جوه Transaction واحد بيتعمله Rollback في الآخر، فمفيش أي بيانات
بتتكتب فعليًا على Database الحقيقية. كل الـseed والطلبات بتتمسح بعد الاختبار.

التشغيل: .venv\\Scripts\\python.exe test_backend.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal

sys.stdout.reconfigure(encoding="utf-8")

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.database import engine, get_db
from app.main import app
from app.models import (
    Admin,
    Addon,
    Category,
    Coupon,
    DeliveryOption,
    Order,
    Product,
    ProductOption,
    ProductOptionGroup,
    RestaurantSettings,
)
from app.security import hash_password

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


# ====== إعداد جلسة اختبار جوه transaction خارجية (Rollback في الآخر) ======
connection = engine.connect()
outer_transaction = connection.begin()
TestingSessionLocal = sessionmaker(
    bind=connection,
    join_transaction_mode="create_savepoint",
    autoflush=False,
    expire_on_commit=False,
)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)


# ====== Seed بيانات الاختبار ======
now = datetime.now(timezone.utc)


def seed() -> dict:
    db = TestingSessionLocal()

    settings = db.scalar(select(RestaurantSettings))
    if settings is None:
        settings = RestaurantSettings(
            restaurant_name="مطعم الاختبار",
            whatsapp_number="201000000000",
            currency="EGP",
        )
        db.add(settings)
        db.flush()
    settings.restaurant_name = "مطعم الاختبار"
    settings.whatsapp_number = "201000000000"
    settings.currency = "EGP"

    admin = Admin(username="test-admin-x", password_hash=hash_password("testpass123"), full_name="Test Admin")
    db.add(admin)

    pizza = Category(name="بيتزا", slug="pizza-test", is_active=True)
    drinks = Category(name="مشروبات", slug="drinks-test", is_active=False)
    db.add_all([pizza, drinks])
    db.flush()

    p1 = Product(
        category_id=pizza.id, name="بيتزا مارجريتا", slug="pizza-margherita-test",
        base_price=Decimal("100.00"), is_active=True, is_available=True,
    )
    p2 = Product(
        category_id=pizza.id, name="بيتزا خضار", slug="pizza-veg-test",
        base_price=Decimal("80.00"), is_active=True, is_available=True,
    )
    p3 = Product(
        category_id=pizza.id, name="منتج معطل", slug="inactive-product-test",
        base_price=Decimal("50.00"), is_active=False, is_available=False,
    )
    db.add_all([p1, p2, p3])
    db.flush()

    o_small = ProductOption(product_id=p1.id, name="صغير", price_adjustment=Decimal("0.00"), is_available=True, sort_order=1)
    o_med = ProductOption(product_id=p1.id, name="وسط", price_adjustment=Decimal("20.00"), is_available=True, sort_order=2)
    o_large = ProductOption(product_id=p1.id, name="كبير", price_adjustment=Decimal("40.00"), is_available=True, sort_order=3)
    db.add_all([o_small, o_med, o_large])

    a_cheese = Addon(product_id=p1.id, name="جبنة زيادة", price=Decimal("10.00"), is_active=True)
    a_olives = Addon(product_id=p1.id, name="زيتون", price=Decimal("5.00"), is_active=True)
    a_mush = Addon(product_id=p2.id, name="مشروم", price=Decimal("8.00"), is_active=True)
    db.add_all([a_cheese, a_olives, a_mush])

    # مجموعة حجم اختيارية لبيتزا مارجريتا (مش إجبارية عشان الطلبات القديمة من غير خيار تفضل شغالة)
    g_p1_size = ProductOptionGroup(
        product_id=p1.id, name="الحجم", is_required=False, allow_multiple=False,
        min_selections=0, max_selections=None, sort_order=1, is_available=True,
    )
    db.add(g_p1_size)
    db.flush()
    o_small.group_id = g_p1_size.id
    o_med.group_id = g_p1_size.id
    o_large.group_id = g_p1_size.id

    # منتج جديد: كريب بانية — مثال الخيارات المطلوبة والاختيار المتعدد من المتطلبات
    p4 = Product(
        category_id=pizza.id, name="كريب بانية", slug="crepe-pane-test",
        base_price=Decimal("100.00"), is_active=True, is_available=True,
    )
    db.add(p4)
    db.flush()

    # مجموعة حجم إجبارية: صغير 100 / وسط 120 / كبير 150 (فرق سعر +0 / +20 / +50)
    g_crepe_size = ProductOptionGroup(
        product_id=p4.id, name="الحجم", is_required=True, allow_multiple=False,
        min_selections=0, max_selections=None, sort_order=1, is_available=True,
    )
    db.add(g_crepe_size)
    db.flush()
    c_small = ProductOption(product_id=p4.id, group_id=g_crepe_size.id, name="صغير", price_adjustment=Decimal("0.00"), is_available=True, sort_order=1)
    c_med = ProductOption(product_id=p4.id, group_id=g_crepe_size.id, name="وسط", price_adjustment=Decimal("20.00"), is_available=True, sort_order=2)
    c_large = ProductOption(product_id=p4.id, group_id=g_crepe_size.id, name="كبير", price_adjustment=Decimal("50.00"), is_available=True, sort_order=3)
    db.add_all([c_small, c_med, c_large])

    # مجموعة إضافات اختيار متعدد (حد أقصى 2): موزاريلا +20 / قطعة بانية +40 / كاتشب +5
    g_crepe_extra = ProductOptionGroup(
        product_id=p4.id, name="الإضافات", is_required=False, allow_multiple=True,
        min_selections=0, max_selections=2, sort_order=2, is_available=True,
    )
    db.add(g_crepe_extra)
    db.flush()
    e_moz = ProductOption(product_id=p4.id, group_id=g_crepe_extra.id, name="موزاريلا", price_adjustment=Decimal("20.00"), is_available=True, sort_order=1)
    e_pane = ProductOption(product_id=p4.id, group_id=g_crepe_extra.id, name="قطعة بانية", price_adjustment=Decimal("40.00"), is_available=True, sort_order=2)
    e_ketch = ProductOption(product_id=p4.id, group_id=g_crepe_extra.id, name="كاتشب", price_adjustment=Decimal("5.00"), is_available=True, sort_order=3)
    db.add_all([e_moz, e_pane, e_ketch])

    d_delivery = DeliveryOption(name="توصيل", fee=Decimal("15.00"), is_active=True, sort_order=1)
    d_pickup = DeliveryOption(name="استلام من المطعم", fee=Decimal("0.00"), is_active=True, sort_order=2)
    db.add_all([d_delivery, d_pickup])

    c_save10 = Coupon(
        code="SAVE10", discount_type="percentage", discount_value=Decimal("10.00"),
        minimum_order=Decimal("0.00"), start_date=now - timedelta(days=1),
        end_date=now + timedelta(days=30), is_active=True,
    )
    c_flat20 = Coupon(
        code="FLAT20", discount_type="fixed", discount_value=Decimal("20.00"),
        minimum_order=Decimal("50.00"), start_date=now - timedelta(days=1),
        end_date=now + timedelta(days=30), is_active=True,
    )
    c_expired = Coupon(
        code="EXPIRED", discount_type="percentage", discount_value=Decimal("50.00"),
        minimum_order=Decimal("0.00"), start_date=now - timedelta(days=30),
        end_date=now - timedelta(days=1), is_active=True,
    )
    c_inactive = Coupon(
        code="NOTACTIVE", discount_type="percentage", discount_value=Decimal("10.00"),
        minimum_order=Decimal("0.00"), start_date=now - timedelta(days=1),
        end_date=now + timedelta(days=30), is_active=False,
    )
    c_minorder = Coupon(
        code="MINORDER", discount_type="percentage", discount_value=Decimal("10.00"),
        minimum_order=Decimal("1000.00"), start_date=now - timedelta(days=1),
        end_date=now + timedelta(days=30), is_active=True,
    )
    c_limited = Coupon(
        code="LIMITED", discount_type="percentage", discount_value=Decimal("10.00"),
        minimum_order=Decimal("0.00"), start_date=now - timedelta(days=1),
        end_date=now + timedelta(days=30), usage_limit=1, usage_count=1, is_active=True,
    )
    db.add_all([c_save10, c_flat20, c_expired, c_inactive, c_minorder, c_limited])

    db.commit()
    return {
        "p1": p1.id, "p2": p2.id, "p3": p3.id, "p4": p4.id,
        "o_small": o_small.id, "o_med": o_med.id, "o_large": o_large.id,
        "a_cheese": a_cheese.id, "a_olives": a_olives.id, "a_mush": a_mush.id,
        "d_delivery": d_delivery.id, "d_pickup": d_pickup.id,
        "g_crepe_size": g_crepe_size.id, "g_crepe_extra": g_crepe_extra.id,
        "c_small": c_small.id, "c_med": c_med.id, "c_large": c_large.id,
        "e_moz": e_moz.id, "e_pane": e_pane.id, "e_ketch": e_ketch.id,
    }


ids = seed()
print("Seed تم داخل transaction (هيتلغي في الآخر)\n")

# ====== 1) Categories ======
print("== Categories ==")
r = client.get("/api/categories")
check("GET /api/categories -> 200", r.status_code == 200, r.text[:100])
slugs = [c["slug"] for c in r.json()]
check("الفئات النشطة فقط (فيه pizza-test)", "pizza-test" in slugs)
check("الفئات المعطلة متظلمة (مفيش drinks-test)", "drinks-test" not in slugs)

# ====== 2) Products list & filter ======
print("== Products ==")
r = client.get("/api/products")
check("GET /api/products -> 200", r.status_code == 200, r.text[:100])
names = [p["name"] for p in r.json()]
check("المنتجات النشطة بس", "بيتزا مارجريتا" in names and "منتج معطل" not in names)

r = client.get("/api/products?category=pizza-test")
check("GET /api/products?category -> 200", r.status_code == 200)
check("فلتر الفئة بيرجع منتجاتها النشطة (3)", len(r.json()) == 3, str(len(r.json())))

r = client.get("/api/products?category=not-found")
check("فئة مش موجودة -> 404", r.status_code == 404)

# ====== 3) Search ======
print("== Search ==")
r = client.get("/api/products?q=مارجريتا")
check("بحث عربي بيرجع المنتج الصحيح", r.status_code == 200 and any(p["slug"] == "pizza-margherita-test" for p in r.json()))
r = client.get("/api/products?q=مفيش كده")
check("بحث بلا نتيجة بيرجع فاضي", r.status_code == 200 and len(r.json()) == 0)

# ====== 4) Product details + options + addons ======
print("== Product details ==")
r = client.get("/api/products/pizza-margherita-test")
check("GET تفاصيل منتج -> 200", r.status_code == 200, r.text[:100])
detail = r.json()
check("السعر الأساسي من الـDB = 100", detail["base_price"] == "100.00", detail.get("base_price"))
check("الخيارات موجودة (3)", len(detail["options"]) == 3, str(len(detail["options"])))
check("تعديل سعر خيار كبير = 40", any(o["name"] == "كبير" and o["price_adjustment"] == "40.00" for o in detail["options"]))
check("الإضافات موجودة (2)", len(detail["addons"]) == 2, str(len(detail["addons"])))
r = client.get("/api/products/not-found-test")
check("منتج مش موجود -> 404", r.status_code == 404)

# ====== 5) Delivery options ======
print("== Delivery options ==")
r = client.get("/api/delivery-options")
check("GET خيارات التوصيل -> 200", r.status_code == 200)
check("خيارين نشطين", len(r.json()) == 2)
check("رسوم التوصيل من الـDB = 15", any(d["name"] == "توصيل" and d["fee"] == "15.00" for d in r.json()))

# ====== 6) Coupon validation ======
print("== Coupon validation ==")
valid_payload = {"code": "SAVE10", "items": [{"product_id": ids["p1"], "quantity": 1}]}
r = client.post("/api/coupons/validate", json=valid_payload)
check("كوبون صالح -> valid=true", r.status_code == 200 and r.json()["valid"] is True, r.text[:150])
check("قيمة الخصم 10% من 100 = 10", r.json()["discount_amount"] == "10.00", r.json().get("discount_amount"))
check("الإجمالي الفرعي محسوب في الـBackend = 100", r.json()["subtotal"] == "100.00")

r = client.post("/api/coupons/validate", json={"code": "EXPIRED", "items": [{"product_id": ids["p1"], "quantity": 1}]})
check("كوبون منتهي -> valid=false", r.status_code == 200 and r.json()["valid"] is False)
r = client.post("/api/coupons/validate", json={"code": "NOTACTIVE", "items": [{"product_id": ids["p1"], "quantity": 1}]})
check("كوبون معطل -> valid=false", r.status_code == 200 and r.json()["valid"] is False)
r = client.post("/api/coupons/validate", json={"code": "MINORDER", "items": [{"product_id": ids["p1"], "quantity": 1}]})
check("كوبون تحت الحد الأدنى -> valid=false", r.status_code == 200 and r.json()["valid"] is False)
r = client.post("/api/coupons/validate", json={"code": "LIMITED", "items": [{"product_id": ids["p1"], "quantity": 1}]})
check("كوبون مستنفد الاستخدامات -> valid=false", r.status_code == 200 and r.json()["valid"] is False)

# ====== 7) Order preview (الحساب من الـBackend) ======
print("== Order preview ==")
# بيتزا كبير (100+40) + جبنة زيادة (10) + زيتون (5) = 155 * كمية 2 = 310
preview_payload = {
    "items": [
        {"product_id": ids["p1"], "quantity": 2, "option_id": ids["o_large"], "addon_ids": [ids["a_cheese"], ids["a_olives"]]},
    ],
    "delivery_option_id": ids["d_delivery"],
    "coupon_code": "SAVE10",
}
r = client.post("/api/orders/preview", json=preview_payload)
check("معاينة الطلب -> 200", r.status_code == 200, r.text[:200])
pv = r.json()
check("سعر الوحدة = 100+40 = 140 (الإضافات منفصلة)", pv["items"][0]["unit_price"] == "140.00", pv["items"][0]["unit_price"])
check("إجمالي الإضافات = 15", pv["items"][0]["addons_total"] == "15.00", pv["items"][0]["addons_total"])
check("إجمالي السطر = 2*(140+15) = 310", pv["items"][0]["line_total"] == "310.00", pv["items"][0]["line_total"])
check("الإجمالي الفرعي = 310", pv["subtotal"] == "310.00", pv["subtotal"])
check("الخصم 10% = 31", pv["discount_amount"] == "31.00", pv["discount_amount"])
check("رسوم التوصيل = 15", pv["delivery_fee"] == "15.00", pv["delivery_fee"])
# الإجمالي = 310 - 31 + 15 = 294
check("الإجمالي النهائي = 294", pv["total"] == "294.00", pv["total"])

# ====== 8) Order creation ======
print("== Order creation ==")
order_payload = {
    "customer_name": "أحمد محمد",
    "customer_phone": "01012345678",
    "customer_address": "شارع التحرير، القاهرة",
    "notes": "من غير بصل",
    "items": [
        {"product_id": ids["p1"], "quantity": 2, "option_id": ids["o_large"], "addon_ids": [ids["a_cheese"], ids["a_olives"]]},
        {"product_id": ids["p2"], "quantity": 1},
    ],
    "delivery_option_id": ids["d_delivery"],
    "coupon_code": "SAVE10",
    "pickup_time": (now + timedelta(days=1)).isoformat(),
}
r = client.post("/orders", json=order_payload)
check("إنشاء طلب -> 200", r.status_code == 200, r.text[:300])
created = r.json()
# subtotal = 310 (بيتزا كبير) + 80 (خضار) = 390; discount 10% = 39; fee 15 => total 366
check("الإجمالي النهائي محفوظ = 366", created["total"] == "366.00", created.get("total"))
check("الخصم = 39", created["discount_amount"] == "39.00", created.get("discount_amount"))
check("رسوم التوصيل = 15", created["delivery_fee"] == "15.00", created.get("delivery_fee"))
check("الإجمالي الفرعي = 390", created["subtotal"] == "390.00", created.get("subtotal"))
check("رابط واتساب موجود", created["whatsapp_url"].startswith("https://wa.me/"), created.get("whatsapp_url"))
order_id = created["order_id"]

# التحقق من الـsnapshot في الـDB
db = TestingSessionLocal()
order = db.get(Order, order_id)
check("الطلب محفوظ في الـDB", order is not None)
check("الإجمالي محفوظ في الـDB = 366", Decimal(order.total) == Decimal("366.00"), str(order.total))
check("coupon_id محفوظ", order.coupon_id is not None)
check("delivery_option_id محفوظ", order.delivery_option_id is not None)
check("pickup_time (الجدولة) محفوظة", order.pickup_time is not None)
check("عدد الأسطر = 2", len(order.items) == 2)
line1 = order.items[0]
check("لقطة سعر الوحدة للسطر الأول = 100+40 = 140 (الإضافات منفصلة)", Decimal(line1.unit_price) == Decimal("140.00"), str(line1.unit_price))
check("إجمالي السطر الأول = 310", Decimal(line1.line_total) == Decimal("310.00"), str(line1.line_total))
check("الإضافات محفوظة snapshot (2)", len(line1.addons) == 2)

# التحقق من خصم استخدام الكوبون
save10 = db.scalar(select(Coupon).where(Coupon.code == "SAVE10"))
check("usage_count اتزاد بعد الطلب", save10.usage_count == 1, str(save10.usage_count))

# ====== 9) رفض الأسعار/المدخلات غير الصالحة ======
print("== Rejection & security ==")
bad = dict(order_payload)
bad["items"] = [{"product_id": ids["p3"], "quantity": 1}]  # منتج معطل
r = client.post("/orders", json=bad)
check("منتج معطل -> 400", r.status_code == 400, str(r.status_code))

bad["items"] = [{"product_id": 999999, "quantity": 1}]
r = client.post("/orders", json=bad)
check("منتج مش موجود -> 400", r.status_code == 400, str(r.status_code))

bad = dict(order_payload)
bad["items"] = [{"product_id": ids["p1"], "quantity": 1, "option_id": 999999}]
r = client.post("/orders", json=bad)
check("خيار مش صالح -> 400", r.status_code == 400, str(r.status_code))

bad = dict(order_payload)
bad["items"] = [{"product_id": ids["p1"], "quantity": 1, "addon_ids": [ids["a_mush"]]}]  # إضافة منتج تاني
r = client.post("/orders", json=bad)
check("إضافة مش تابعة للمنتج -> 400", r.status_code == 400, str(r.status_code))

bad = dict(order_payload)
bad["coupon_code"] = "EXPIRED"
r = client.post("/orders", json=bad)
check("كوبون منتهي -> 400", r.status_code == 400, str(r.status_code))

bad = dict(order_payload)
bad["delivery_option_id"] = 999999
r = client.post("/orders", json=bad)
check("خيار توصيل مش موجود -> 400", r.status_code == 400, str(r.status_code))

bad = dict(order_payload)
bad["pickup_time"] = (now - timedelta(days=1)).isoformat()  # موعد في الماضي
r = client.post("/orders", json=bad)
check("موعد في الماضي -> 400", r.status_code == 400, str(r.status_code))

# محاولة تزويد سعر من العميل — الحقل غير مقبول أصلًا فبيتجاهله الـBackend
tampered = dict(order_payload)
tampered["items"] = [{"product_id": ids["p1"], "quantity": 1, "price": "1.00", "unit_price": "1.00", "line_total": "1.00"}]
r = client.post("/orders", json=tampered)
check("السعر المُرسَل من العميل بيتجاهل (الطلب اتقبل)", r.status_code == 200, str(r.status_code))
if r.status_code == 200:
    # 100 (السعر من الـDB) - 10 (كوبون 10%) + 15 (توصيل) = 105
    check("الإجمالي اتحسب من الـDB مش من العميل = 105", r.json()["total"] == "105.00", r.json()["total"])

# ====== 10) سيناريوهات المنتجات المطلوبة ======
print("== Product scenarios (no addon / one addon / many addons / many sizes) ==")

def make_order(items, **kw):
    payload = {
        "customer_name": "عميل تجريبي",
        "customer_phone": "01000000001",
        "customer_address": "شارع الاختبار، القاهرة",
        "items": items,
    }
    payload.update(kw)
    return payload

# (أ) منتج بدون Add-ons ولا خيارات: بيتزا خضار سادة = 80
r = client.post("/orders", json=make_order([{"product_id": ids["p2"], "quantity": 1}]))
check("منتج بدون إضافات -> 200", r.status_code == 200, r.text[:200])
check("الإجمالي = 80 (سعر المنتج فقط)", r.json()["total"] == "80.00", r.json().get("total"))

# (ب) منتج بـ Add-on واحد: بيتزا خضار + مشروم(+8) = 88
r = client.post("/orders", json=make_order([{"product_id": ids["p2"], "quantity": 1, "addon_ids": [ids["a_mush"]]}]))
check("منتج بإضافة واحدة -> 200", r.status_code == 200, r.text[:200])
check("الإجمالي = 88 (80 + 8)", r.json()["total"] == "88.00", r.json().get("total"))

# (ج) منتج بعدة Add-ons: بيتزا مارجريتا + جبنة(+10) + زيتون(+5) = 115
r = client.post("/orders", json=make_order([{"product_id": ids["p1"], "quantity": 1, "addon_ids": [ids["a_cheese"], ids["a_olives"]]}]))
check("منتج بعدة إضافات -> 200", r.status_code == 200, r.text[:200])
check("الإجمالي = 115 (100 + 10 + 5)", r.json()["total"] == "115.00", r.json().get("total"))

# (د) منتج بعدة أحجام: كريب بانية — صغير 100 / وسط 120 / كبير 150
r = client.post("/orders", json=make_order([{"product_id": ids["p4"], "quantity": 1, "option_ids": [ids["c_small"]]}]))
check("كريب حجم صغير -> 200 والإجمالي 100", r.status_code == 200 and r.json()["total"] == "100.00", r.text[:200])
r = client.post("/orders", json=make_order([{"product_id": ids["p4"], "quantity": 1, "option_ids": [ids["c_med"]]}]))
check("كريب حجم وسط -> الإجمالي 120", r.status_code == 200 and r.json()["total"] == "120.00", r.json().get("total"))
r = client.post("/orders", json=make_order([{"product_id": ids["p4"], "quantity": 1, "option_ids": [ids["c_large"]]}]))
check("كريب حجم كبير -> الإجمالي 150", r.status_code == 200 and r.json()["total"] == "150.00", r.json().get("total"))

# حجم + اختيار متعدد: كريب وسط(+20) + موزاريلا(+20) + كاتشب(+5) = 145
r = client.post("/orders", json=make_order([{"product_id": ids["p4"], "quantity": 1, "option_ids": [ids["c_med"], ids["e_moz"], ids["e_ketch"]]}]))
check("حجم + إضافتين متعددتين -> الإجمالي 145", r.status_code == 200 and r.json()["total"] == "145.00", r.json().get("total"))

print("== Option group rules (required / multi-select / max) ==")
# مجموعة الحجم إجبارية: كريب بدون حجم -> 400
r = client.post("/orders", json=make_order([{"product_id": ids["p4"], "quantity": 1}]))
check("منتج بمجموعة إجبارية بدون اختيار -> 400", r.status_code == 400, str(r.status_code))

# اختيار اكتر من خيار في مجموعة اختيار واحد -> 400
r = client.post("/orders", json=make_order([{"product_id": ids["p4"], "quantity": 1, "option_ids": [ids["c_small"], ids["c_med"]]}]))
check("أكتر من خيار في مجموعة اختيار واحد -> 400", r.status_code == 400, str(r.status_code))

# اختيار متعدد فوق الحد الأقصى (3 والحد 2) -> 400
r = client.post("/orders", json=make_order([{"product_id": ids["p4"], "quantity": 1, "option_ids": [ids["c_small"], ids["e_moz"], ids["e_pane"], ids["e_ketch"]]}]))
check("اختيار متعدد فوق الحد الأقصى -> 400", r.status_code == 400, str(r.status_code))

# خيار من مجموعة منتج تاني -> 400
r = client.post("/orders", json=make_order([{"product_id": ids["p1"], "quantity": 1, "option_ids": [ids["c_large"]]}]))
check("خيار تابع لمنتج تاني -> 400", r.status_code == 400, str(r.status_code))

print("== Item note & option snapshot ==")
# ملاحظة خاصة بالمنتج + حفظ لقطة الخيارات في الـDB
r = client.post("/orders", json=make_order(
    [{"product_id": ids["p4"], "quantity": 2, "option_ids": [ids["c_large"], ids["e_pane"]], "item_note": "من غير مخلل"}],
))
check("طلب بملاحظة وخيارات -> 200", r.status_code == 200, r.text[:200])
# (100+50+40)*2 = 380
check("الإجمالي = 380 ((100+50+40)*2)", r.json()["total"] == "380.00", r.json().get("total"))
db = TestingSessionLocal()
saved = db.get(Order, r.json()["order_id"])
check("الملاحظة الخاصة محفوظة", saved.items[0].item_note == "من غير مخلل", str(saved.items[0].item_note))
check("لقطة الخيارات محفوظة (خيارين)", len(saved.items[0].options) == 2, str(len(saved.items[0].options)))
check("اسم الحجم في اللقطة", any(o.name == "كبير" for o in saved.items[0].options))
check("سعر الوحدة المحفوظ = 190 (100+50+40)", Decimal(saved.items[0].unit_price) == Decimal("190.00"), str(saved.items[0].unit_price))

# ====== 11) Routes الحالية لسه شغالة ======
print("== Backward compatibility (Routes الحالية) ==")
r = client.get("/")
check("GET / الصفحة الرئيسية -> 200", r.status_code == 200, str(r.status_code))
r = client.get("/search?q=بيتزا")
check("GET /search الحالي -> 200", r.status_code == 200, str(r.status_code))
r = client.get("/products/pizza-margherita-test")
check("GET /products/{slug} الحالي -> 200", r.status_code == 200, str(r.status_code))

# ====== 12) Admin options CRUD ======
print("== Admin options CRUD ==")
r = client.get(f"/admin/products/{ids['p4']}/options", follow_redirects=False)
check("صفحة الخيارات بدون دخول -> 303", r.status_code == 303, str(r.status_code))

r = client.get("/admin/login")
csrf = client.cookies.get("admin_csrf")
check("كوكي CSRF صدر من صفحة الدخول", bool(csrf))
r = client.post(
    "/admin/login",
    data={"username": "test-admin-x", "password": "testpass123", "csrf_token": csrf},
    follow_redirects=False,
)
check("تسجيل دخول الأدمن -> 303", r.status_code == 303, str(r.status_code))


def admin_post(url, data):
    d = dict(data)
    d["csrf_token"] = client.cookies.get("admin_csrf")
    return client.post(url, data=d, follow_redirects=False)


r = client.get(f"/admin/products/{ids['p4']}/options")
check("صفحة الخيارات بعد الدخول -> 200", r.status_code == 200, str(r.status_code))

r = admin_post(
    f"/admin/products/{ids['p4']}/options/groups",
    {"name": "إضافات جديدة", "min_selections": 0, "max_selections": "1", "sort_order": 5, "is_available": "true"},
)
check("إضافة مجموعة خيارات -> 303", r.status_code == 303, str(r.status_code))
db = TestingSessionLocal()
new_group = db.scalar(
    select(ProductOptionGroup).where(
        ProductOptionGroup.product_id == ids["p4"], ProductOptionGroup.name == "إضافات جديدة"
    )
)
check("المجموعة اتحفطت في الـDB", new_group is not None)
if new_group is not None:
    check("الحد الأقصى للمجموعة اتحفظ = 1", new_group.max_selections == 1, str(new_group.max_selections))

    r = admin_post(
        f"/admin/products/{ids['p4']}/options",
        {"group_id": new_group.id, "name": "خيار جديد", "price_adjustment": "7.50", "sort_order": 0, "is_available": "true"},
    )
    check("إضافة خيار للمجموعة -> 303", r.status_code == 303, str(r.status_code))
    new_option = db.scalar(
        select(ProductOption).where(ProductOption.product_id == ids["p4"], ProductOption.name == "خيار جديد")
    )
    check("الخيار اتحفظ ومربوط بالمجموعة", new_option is not None and new_option.group_id == new_group.id)
    new_option_id = new_option.id if new_option is not None else None
    new_group_id = new_group.id
    if new_option is not None:
        check("سعر الخيار اتحفظ = 7.50", Decimal(new_option.price_adjustment) == Decimal("7.50"), str(new_option.price_adjustment))
        r = admin_post(f"/admin/products/{ids['p4']}/options/{new_option.id}/delete", {})
        check("حذف الخيار -> 303", r.status_code == 303, str(r.status_code))

    r = admin_post(f"/admin/products/{ids['p4']}/options/groups/{new_group.id}/delete", {})
    check("حذف المجموعة -> 303", r.status_code == 303, str(r.status_code))

    # جلسة جديدة للتأكد من الحذف الفعلي (تفادي ObjectDeletedError من الـidentity map)
    verify_db = TestingSessionLocal()
    check("الخيار اتحذف فعلًا", verify_db.scalar(select(ProductOption).where(ProductOption.id == new_option_id)) is None)
    check("المجموعة اتحذفت فعلًا (وخياراتها معاها)", verify_db.scalar(select(ProductOptionGroup).where(ProductOptionGroup.id == new_group_id)) is None)

# ====== Rollback ======
outer_transaction.rollback()
connection.close()
app.dependency_overrides.clear()

print(f"\nالنتيجة: {PASS} نجح / {FAIL} فشل")
if FAIL:
    print("فيه اختبارات فشلت — راجع التفاصيل فوق.")
    sys.exit(1)
else:
    print("كل اختبارات الـBackend نجحت. مفيش بيانات اتكتبت على الـDB (Rollback تم).")
