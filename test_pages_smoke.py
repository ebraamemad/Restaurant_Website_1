"""فحص سريع للصفحات بعد تطوير نظام الخيارات — داخل transaction بيتلغي في الآخر."""
from __future__ import annotations

import json
import re
import sys
from decimal import Decimal

sys.stdout.reconfigure(encoding="utf-8")

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.database import engine, get_db
from app.main import app
from app.models import Addon, Category, Product, ProductOption, ProductOptionGroup

PASS = 0
FAIL = 0


def check(name, condition, extra=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {extra}")


connection = engine.connect()
outer_transaction = connection.begin()
TestingSessionLocal = sessionmaker(bind=connection, join_transaction_mode="create_savepoint", autoflush=False, expire_on_commit=False)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)

db = TestingSessionLocal()
cat = Category(name="فحص", slug="smoke-cat", is_active=True)
db.add(cat); db.flush()
prod = Product(category_id=cat.id, name="كريب فحص", slug="smoke-crepe", base_price=Decimal("100.00"), is_active=True, is_available=True, description="وصف تجريبي")
db.add(prod); db.flush()
size = ProductOptionGroup(product_id=prod.id, name="الحجم", is_required=True, allow_multiple=False, sort_order=1)
extra = ProductOptionGroup(product_id=prod.id, name="إضافات", allow_multiple=True, max_selections=2, sort_order=2)
db.add_all([size, extra]); db.flush()
db.add_all([
    ProductOption(product_id=prod.id, group_id=size.id, name="صغير", price_adjustment=Decimal("0.00"), sort_order=1),
    ProductOption(product_id=prod.id, group_id=size.id, name="وسط", price_adjustment=Decimal("20.00"), sort_order=2),
    ProductOption(product_id=prod.id, group_id=extra.id, name="موزاريلا", price_adjustment=Decimal("20.00"), sort_order=1),
    Addon(product_id=prod.id, name="كاتشب", price=Decimal("5.00"), is_active=True),
])
db.commit()

print("== صفحة تفاصيل المنتج ==")
r = client.get("/products/smoke-crepe")
check("صفحة التفاصيل -> 200", r.status_code == 200, str(r.status_code))
html = r.text
check("فيها حقل الملاحظة الخاصة", "ملاحظة خاصة بالمنتج" in html)
check("فيها نص (مطلوب) للمجموعة الإجبارية", "(مطلوب)" in html)

# نتأكد إن x-data بيسلّم 3 JSON سليمة لـAlpine (single-quoted attribute)
m = re.search(r"x-data='([^']*)'", html)
check("x-data محدد بـsingle quotes ومكتمل", bool(m))
parsed = []
if m:
    expr = m.group(1)
    check("الـcomponent هو productDetail", expr.startswith("productDetail("))
    args_str = expr[expr.index("(") + 1:expr.rindex(")")]
    decoder = json.JSONDecoder()
    idx = 0
    while idx < len(args_str) and len(parsed) < 3:
        while idx < len(args_str) and args_str[idx] in " ,":
            idx += 1
        if idx >= len(args_str):
            break
        obj, idx = decoder.raw_decode(args_str, idx)
        parsed.append(obj)
check("اتسلّموا 3 JSON arguments (product, addons, option_groups)", len(parsed) == 3, str(len(parsed)))
if len(parsed) == 3:
    product_json, addons_json, groups_json = parsed
    check("اسم المنتج فكّ ترميزه صح", product_json.get("name") == "كريب فحص", str(product_json.get("name")))
    check("الإضافات فيها كاتشب", any(a["name"] == "كاتشب" for a in addons_json))
    check("وصلت مجموعتين خيارات", len(groups_json) == 2, str(len(groups_json)))
    names = {g["name"] for g in groups_json}
    check("أسماء المجموعات صح", names == {"الحجم", "إضافات"}, str(names))
    size_group = next((g for g in groups_json if g["name"] == "الحجم"), {})
    check("مجموعة الحجم إجبارية ولها خيارين", size_group.get("is_required") is True and len(size_group.get("options", [])) == 2)
    extra_group = next((g for g in groups_json if g["name"] == "إضافات"), {})
    check("مجموعة الإضافات اختيار متعدد بحد أقصى 2", extra_group.get("allow_multiple") is True and extra_group.get("max_selections") == 2)

print("== الصفحة الرئيسية ==")
r = client.get("/")
check("الرئيسية -> 200", r.status_code == 200, str(r.status_code))
check("منتج له خيارات بيقترح 'اختر الخيارات'", "اختر الخيارات" in r.text)

print("== API تفاصيل المنتج ==")
r = client.get("/api/products/smoke-crepe")
check("API تفاصيل -> 200", r.status_code == 200, str(r.status_code))
data = r.json()
groups = data.get("option_groups", [])
check("option_groups كاملة (2)", len(groups) == 2, str(len(groups)))
check("مجموعة الحجم بإعداداتها", any(g["name"] == "الحجم" and g["is_required"] and len(g["options"]) == 2 for g in groups))
check("مجموعة إضافات اختيار متعدد max=2", any(g["name"] == "إضافات" and g["allow_multiple"] and g["max_selections"] == 2 for g in groups))

outer_transaction.rollback()
connection.close()
app.dependency_overrides.clear()

print(f"\nفحص الصفحات: {PASS} نجح / {FAIL} فشل")
sys.exit(0 if FAIL == 0 else 1)
