from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Optional, Type, Union

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import distinct, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.deps import get_current_admin, verify_admin_csrf
from app.models import Admin, Addon, Category, Order, OrderItem, Product, RestaurantSettings
from app.schemas import OrderStatus
from app.security import csrf_field
from app.utils import make_slug, save_image

router = APIRouter(prefix="/admin", dependencies=[Depends(get_current_admin), Depends(verify_admin_csrf)])
TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals["csrf_field"] = csrf_field
UPLOADS_DIR = Path(__file__).resolve().parents[1] / "uploads"
STATUS_LABELS = {
    OrderStatus.pending.value: "قيد الانتظار",
    OrderStatus.preparing.value: "جاري التجهيز",
    OrderStatus.delivered.value: "تم التسليم",
    OrderStatus.cancelled.value: "تم الإلغاء",
}


def redirect(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=status.HTTP_303_SEE_OTHER)


def unique_slug(db: Session, model: Union[Type[Category], Type[Product]], value: str, current_id: Optional[int] = None) -> str:
    base_slug = make_slug(value)
    slug = base_slug
    counter = 2
    while True:
        statement = select(model).where(model.slug == slug)
        if current_id is not None:
            statement = statement.where(model.id != current_id)
        exists = db.scalar(statement)
        if exists is None:
            return slug
        slug = f"{base_slug}-{counter}"
        counter += 1


def get_or_create_settings(db: Session) -> RestaurantSettings:
    settings = db.scalar(select(RestaurantSettings))
    if settings is None:
        settings = RestaurantSettings()
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


def get_dashboard_stats(db: Session):
    top_products = db.execute(
        select(OrderItem.product_name, func.sum(OrderItem.quantity))
        .group_by(OrderItem.product_name)
        .order_by(func.sum(OrderItem.quantity).desc(), OrderItem.product_name)
        .limit(5)
    ).all()
    top_customers = db.execute(
        select(Order.customer_phone, func.count(Order.id))
        .group_by(Order.customer_phone)
        .order_by(func.count(Order.id).desc(), Order.customer_phone)
        .limit(5)
    ).all()
    return top_products, top_customers


@router.get("", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db), admin: Admin = Depends(get_current_admin)) -> HTMLResponse:
    now = datetime.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=today_start.weekday())
    month_start = today_start.replace(day=1)
    delivered_sales = db.scalar(select(func.coalesce(func.sum(Order.total), 0)).where(Order.status == OrderStatus.delivered.value)) or Decimal("0")
    top_products, top_customers = get_dashboard_stats(db)
    context = {
        "request": request,
        "admin": admin,
        "today_orders": db.scalar(select(func.count(Order.id)).where(Order.created_at >= today_start)),
        "week_orders": db.scalar(select(func.count(Order.id)).where(Order.created_at >= week_start)),
        "month_orders": db.scalar(select(func.count(Order.id)).where(Order.created_at >= month_start)),
        "total_sales": delivered_sales,
        "customers_count": db.scalar(select(func.count(distinct(Order.customer_phone)))),
        "latest_orders": db.scalars(
            select(Order)
            .options(selectinload(Order.items).selectinload(OrderItem.addons))
            .order_by(Order.created_at.desc())
            .limit(8)
        ).all(),
        "top_products": top_products,
        "top_customers": top_customers,
        "status_labels": STATUS_LABELS,
    }
    return templates.TemplateResponse("admin/dashboard.html", context)


@router.get("/categories", response_class=HTMLResponse)
def categories_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    categories = db.scalars(select(Category).order_by(Category.sort_order, Category.name)).all()
    return templates.TemplateResponse("admin/categories.html", {"request": request, "categories": categories})


@router.post("/categories")
async def save_category(
    id: Optional[int] = Form(default=None),
    name: str = Form(...),
    description: Optional[str] = Form(default=None),
    sort_order: int = Form(default=0),
    is_active: bool = Form(default=False),
    is_featured: bool = Form(default=False),
    image: Optional[UploadFile] = File(default=None),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    category = db.get(Category, id) if id else Category()
    if category is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Category not found")
    category.name = name
    category.slug = unique_slug(db, Category, name, id)
    category.description = description
    category.sort_order = sort_order
    category.is_active = is_active
    category.is_featured = is_featured
    if image and image.filename:
        category.image_url = await save_image(image, UPLOADS_DIR / "categories")
    db.add(category)
    db.commit()
    return redirect("/admin/categories")


@router.post("/categories/{category_id}/delete")
def delete_category(category_id: int, db: Session = Depends(get_db)) -> RedirectResponse:
    category = db.get(Category, category_id)
    if category is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Category not found")
    has_products = db.scalar(select(func.count(Product.id)).where(Product.category_id == category_id))
    if has_products:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Move or delete products before deleting this category")
    db.delete(category)
    db.commit()
    return redirect("/admin/categories")


@router.get("/products", response_class=HTMLResponse)
def products_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    categories = db.scalars(select(Category).order_by(Category.sort_order, Category.name)).all()
    products = db.scalars(select(Product).options(selectinload(Product.category)).order_by(Product.sort_order, Product.name)).all()
    return templates.TemplateResponse("admin/products.html", {"request": request, "categories": categories, "products": products})


@router.post("/products")
async def save_product(
    id: Optional[int] = Form(default=None),
    category_id: int = Form(...),
    name: str = Form(...),
    description: Optional[str] = Form(default=None),
    base_price: Decimal = Form(...),
    unit: str = Form(default="piece"),
    sort_order: int = Form(default=0),
    is_available: bool = Form(default=False),
    is_active: bool = Form(default=False),
    image: Optional[UploadFile] = File(default=None),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    if db.get(Category, category_id) is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Category does not exist")
    product = db.get(Product, id) if id else Product()
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found")
    product.category_id = category_id
    product.name = name
    product.slug = unique_slug(db, Product, name, id)
    product.description = description
    product.base_price = base_price
    product.unit = unit
    product.sort_order = sort_order
    product.is_available = is_available
    product.is_active = is_active
    if image and image.filename:
        product.image_url = await save_image(image, UPLOADS_DIR / "products")
    db.add(product)
    db.commit()
    return redirect("/admin/products")


@router.post("/products/{product_id}/delete")
def delete_product(product_id: int, db: Session = Depends(get_db)) -> RedirectResponse:
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found")
    db.delete(product)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        product.is_active = False
        db.add(product)
        db.commit()
    return redirect("/admin/products")


@router.get("/products/{product_id}/addons", response_class=HTMLResponse)
def manage_addons(product_id: int, request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found")
    addons = db.scalars(
        select(Addon).where(Addon.product_id == product_id).order_by(Addon.sort_order, Addon.name)
    ).all()
    return templates.TemplateResponse(
        "admin/addons.html",
        {"request": request, "product": product, "addons": addons},
    )


@router.post("/products/{product_id}/addons")
async def save_addon(
    product_id: int,
    id: Optional[int] = Form(default=None),
    name: str = Form(...),
    price: Decimal = Form(...),
    is_active: bool = Form(default=False),
    sort_order: int = Form(default=0),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found")
    addon = db.get(Addon, id) if id else Addon(product_id=product_id)
    if id and (addon is None or addon.product_id != product_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Addon not found")
    addon.name = name
    addon.price = price
    # الفورم مبتبعتش الحقل لما الصينبوكس يكون فاضي، فالافتراضي لازم False وإلا تعطيل الإضافة مستحيل.
    addon.is_active = is_active
    addon.sort_order = sort_order
    db.add(addon)
    db.commit()
    return redirect(f"/admin/products/{product_id}/addons")


@router.post("/products/{product_id}/addons/{addon_id}/delete")
def delete_addon(product_id: int, addon_id: int, db: Session = Depends(get_db)) -> RedirectResponse:
    addon = db.get(Addon, addon_id)
    if addon is None or addon.product_id != product_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Addon not found")
    db.delete(addon)
    db.commit()
    return redirect(f"/admin/products/{product_id}/addons")


@router.get("/orders", response_class=HTMLResponse)
def orders_page(request: Request, status_filter: Optional[str] = None, db: Session = Depends(get_db)) -> HTMLResponse:
    statement = select(Order).options(selectinload(Order.items)).order_by(Order.created_at.desc())
    if status_filter:
        statement = statement.where(Order.status == status_filter)
    orders = db.scalars(statement).all()
    return templates.TemplateResponse(
        "admin/orders.html",
        {"request": request, "orders": orders, "statuses": list(OrderStatus), "status_labels": STATUS_LABELS},
    )


@router.post("/orders/{order_id}/status")
def update_order_status(order_id: int, status_value: OrderStatus = Form(...), db: Session = Depends(get_db)) -> RedirectResponse:
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")
    order.status = status_value.value
    db.commit()
    return redirect("/admin/orders")


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    return templates.TemplateResponse("admin/settings.html", {"request": request, "settings": get_or_create_settings(db)})


@router.post("/settings")
async def save_settings(
    restaurant_name: str = Form(...),
    whatsapp_number: str = Form(...),
    phone_number: Optional[str] = Form(default=None),
    address: Optional[str] = Form(default=None),
    working_hours: Optional[str] = Form(default=None),
    currency: str = Form(default="EGP"),
    facebook_url: Optional[str] = Form(default=None),
    tiktok_url: Optional[str] = Form(default=None),
    additional_info: Optional[str] = Form(default=None),
    logo: Optional[UploadFile] = File(default=None),
    banner: Optional[UploadFile] = File(default=None),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    settings = get_or_create_settings(db)
    settings.restaurant_name = restaurant_name
    settings.whatsapp_number = whatsapp_number
    settings.phone_number = phone_number
    settings.address = address
    settings.working_hours = working_hours
    settings.currency = currency
    settings.facebook_url = facebook_url or None
    settings.tiktok_url = tiktok_url or None
    settings.additional_info = additional_info or None
    if logo and logo.filename:
        settings.logo_url = await save_image(logo, UPLOADS_DIR / "logos")
    if banner and banner.filename:
        settings.banner_url = await save_image(banner, UPLOADS_DIR / "banners")
    db.commit()
    return redirect("/admin/settings")
