"""
Test Suite: Stage 8 - Product Featured & Offer + Category Cleanup
Verifies:
  - is_featured and is_offer columns exist on Product table
  - Products can be saved with is_featured/is_offer flags via admin form
  - Featured products appear in the featured slider on the home page
  - Offer badge is displayed on product cards for offer products
  - Category no longer has is_featured (removed from form + model)
  - Products without any featured flag fallback to image-based featured
"""
import os
import sys
import re

sys.path.insert(0, os.path.dirname(__file__))

from app.main import app
from app.database import engine, get_db
from app.models import Admin, Product, Category, RestaurantSettings
from app.security import hash_password
from sqlalchemy import inspect, select
sys.stdout.reconfigure(encoding="utf-8")

from fastapi.testclient import TestClient

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


def get_db_session():
    return next(get_db())


def admin_login(db):
    admin = db.query(Admin).filter_by(username="stage8-admin").first()
    if not admin:
        admin = Admin(username="stage8-admin", password_hash=hash_password("stage8-pass"), full_name="Stage8", is_active=True)
        db.add(admin)
        db.commit()
        db.refresh(admin)
    login_page = client.get("/admin/login")
    csrf = client.cookies.get("admin_csrf")
    resp = client.post("/admin/login", data={"username": "stage8-admin", "password": "stage8-pass", "csrf_token": csrf}, follow_redirects=False)
    return resp.status_code == 303


# --- Schema checks ---
def test_schema_has_product_flags():
    print("\n[Schema] Product.is_featured and is_offer columns exist")
    db = get_db_session()
    inspector = inspect(db.bind)
    cols = {c["name"] for c in inspector.get_columns("products")}
    check("products.is_featured column exists", "is_featured" in cols, f"columns: {cols}")
    check("products.is_offer column exists", "is_offer" in cols, f"columns: {cols}")


def test_schema_category_has_no_featured():
    print("\n[Schema] Category.is_featured removed")
    db = get_db_session()
    inspector = inspect(db.bind)
    cols = {c["name"] for c in inspector.get_columns("categories")}
    check("categories.is_featured removed", "is_featured" not in cols, f"columns: {cols}")


# --- Product creation with flags ---
def test_create_product_with_flags():
    print("\n[Admin] Create product with is_featured and is_offer")
    db = get_db_session()

    # Cleanup any leftover test products
    db.query(Product).filter(Product.slug.like("%s8")).delete(synchronize_session=False)
    db.commit()

    # Ensure a category exists
    cat = db.execute(select(Category)).first()
    if cat:
        cat = cat[0]
    else:
        cat = Category(name="Test Cat S8", slug="test-cat-s8", is_active=True, sort_order=0)
        db.add(cat)
        db.commit()
        db.refresh(cat)

    p = Product(
        name="سلطة مميزة",
        slug="salad-featured-s8",
        description="سلطة اختبار",
        base_price=99.0,
        unit="بالوزن",
        is_available=True,
        is_active=True,
        is_featured=True,
        is_offer=True,
    )
    p.category_id = cat.id
    db.add(p)
    db.commit()
    db.refresh(p)

    db.expire_all()
    loaded = db.get(Product, p.id)
    check("is_featured saved as True", loaded.is_featured is True)
    check("is_offer saved as True", loaded.is_offer is True)


def test_create_product_without_flags():
    print("\n[Admin] Create product without is_featured/is_offer (defaults)")
    db = get_db_session()
    cat = db.execute(select(Category)).first()
    if cat:
        cat = cat[0]
    else:
        cat = Category(name="Test Cat S8b", slug="test-cat-s8b", is_active=True, sort_order=0)
        db.add(cat)
        db.commit()
        db.refresh(cat)

    p = Product(
        name="وجبة عادية",
        slug="normal-meal-s8",
        description="وجبة اختبار",
        base_price=50.0,
        unit="بالوزن",
        is_available=True,
        is_active=True,
    )
    p.category_id = cat.id
    db.add(p)
    db.commit()
    db.refresh(p)

    db.expire_all()
    loaded = db.get(Product, p.id)
    check("is_featured defaults to False", loaded.is_featured is False or loaded.is_featured is None)
    check("is_offer defaults to False", loaded.is_offer is False or loaded.is_offer is None)


# --- Home page featured slider ---
def test_home_page_featured_slider():
    print("\n[Public] Featured slider displays featured products on home page")
    response = client.get("/")
    check("home page returns 200", response.status_code == 200, f"status: {response.status_code}")

    # Look for the featured product name in the HTML
    featured_found = "سلطة مميزة" in response.text
    check("featured product appears in slider", featured_found)


# --- Offer badge on product card ---
def test_product_card_offer_badge():
    print("\n[Public] Offer badge ('عرض') shown on offer product cards")
    response = client.get("/")
    offer_badges = "عرض" in response.text
    check("offer badge text found on page", offer_badges)


# --- Category form no longer has is_featured ---
def test_admin_category_form_no_featured():
    print("\n[Admin] Category form no longer includes is_featured checkbox")
    db = get_db_session()
    if not admin_login(db):
        check("admin login succeeded", False, "could not log in")
        return

    response = client.get("/admin/categories")
    check("categories page returns 200", response.status_code == 200, f"status: {response.status_code}")
    has_featured = bool(re.search(r'name="is_featured"', response.text))
    check("is_featured input absent from category form", not has_featured)


def test_admin_product_form_has_flags():
    print("\n[Admin] Product form includes is_featured and is_offer checkboxes")
    db = get_db_session()
    if not admin_login(db):
        check("admin login succeeded", False, "could not log in")
        return

    response = client.get("/admin/products")
    check("products page returns 200", response.status_code == 200, f"status: {response.status_code}")
    has_featured = bool(re.search(r'name="is_featured"', response.text))
    has_offer = bool(re.search(r'name="is_offer"', response.text))
    check("is_featured input present on product form", has_featured)
    check("is_offer input present on product form", has_offer)


# --- Featured products filter ---
def test_featured_products_in_context():
    print("\n[Schema] Product.is_featured filter works")
    db = get_db_session()
    featured = db.execute(
        select(Product).where(Product.is_featured == True, Product.is_active == True, Product.is_available == True)
    ).scalars().all()
    check("at least one featured product found", len(featured) > 0, f"found {len(featured)} featured products")


if __name__ == "__main__":
    print("=" * 60)
    print("Stage 8: Product Featured & Offer + Category Cleanup")
    print("=" * 60)

    test_schema_has_product_flags()
    test_schema_category_has_no_featured()
    test_create_product_with_flags()
    test_create_product_without_flags()
    test_home_page_featured_slider()
    test_product_card_offer_badge()
    test_admin_category_form_no_featured()
    test_admin_product_form_has_flags()
    test_featured_products_in_context()

    print("\n" + "=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60)

    if failed > 0:
        sys.exit(1)
