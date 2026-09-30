from __future__ import annotations

import csv
import io
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional, Type, Union

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import distinct, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app import push
from app.database import get_db
from app.deps import get_current_admin, verify_admin_csrf
from app.models import (
    Admin,
    Addon,
    Branch,
    Category,
    Coupon,
    Customer,
    DeliveryOption,
    DeliveryZone,
    Order,
    OrderItem,
    PaymentMethod,
    Product,
    ProductOption,
    ProductOptionGroup,
    RestaurantSettings,
)
from app.order_tracking import status_label as order_tracking_status_label
from app.ordering import ZERO, quantize_money
from app.schemas import (
    AdminDeliveryFeeIn,
    OrderStatus,
    PaymentStatusUpdateIn,
    PushStatusOut,
    PushSubscriptionIn,
    PushSubscriptionOut,
    PushUnsubscribeIn,
    allowed_next_statuses,
    coerce_order_status,
    is_transition_allowed,
)
from app.security import csrf_field
from app.utils import (
    PAYMENT_METHOD_LABELS,
    THEME_DEFAULTS,
    build_search_variants,
    build_whatsapp_url,
    make_slug,
    normalize_hex_color,
    normalize_phone,
    parse_ordering_time,
    parse_theme_colors,
    save_image,
)

router = APIRouter(prefix="/admin", dependencies=[Depends(get_current_admin), Depends(verify_admin_csrf)])
logger = logging.getLogger(__name__)
TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals["csrf_field"] = csrf_field
templates.env.globals["theme_colors"] = parse_theme_colors
templates.env.globals["whatsapp_url"] = lambda phone: build_whatsapp_url(phone, "")
templates.env.globals["payment_labels"] = PAYMENT_METHOD_LABELS
UPLOADS_DIR = Path(__file__).resolve().parents[1] / "uploads"
# تسميات الحالات في لوحة التحكم، مفصولة عن `app/order_tracking.py` عن
# قصد: الأدمن بيشوف "قيد الانتظار" والعميل بيشوف "تم استلام الطلب" لنفس
# الحالة. تغيير تسمية الأدمن ما يمسّش حاجة بتظهر للعميل.
STATUS_LABELS = {
    OrderStatus.pending.value: "قيد الانتظار",
    OrderStatus.confirmed.value: "تم التأكيد",
    OrderStatus.preparing.value: "جاري التجهيز",
    OrderStatus.ready.value: "جاهز للتسليم",
    OrderStatus.out_for_delivery.value: "في الطريق",
    OrderStatus.completed.value: "تم التسليم",
    # legacy V1 — لسه في طلبات بالحالة دي
    OrderStatus.delivered.value: "تم التسليم",
    OrderStatus.cancelled.value: "تم الإلغاء",
}

PAYMENT_STATUS_LABELS = {
    "pending": "لم يتم الدفع",
    "confirmed": "مؤكد",
    "paid": "مدفوع",
    "failed": "فشل الدفع",
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
        .join(Order, OrderItem.order_id == Order.id)
        .where(Order.status != OrderStatus.cancelled.value)
        .group_by(OrderItem.product_name)
        .order_by(func.sum(OrderItem.quantity).desc(), OrderItem.product_name)
        .limit(5)
    ).all()
    top_customers = db.execute(
        select(Order.customer_phone, func.count(Order.id), func.coalesce(func.sum(Order.total), 0))
        .where(Order.status != OrderStatus.cancelled.value)
        .group_by(Order.customer_phone)
        .order_by(func.count(Order.id).desc(), Order.customer_phone)
        .limit(5)
    ).all()
    return top_products, top_customers


def dashboard_analytics(
    db: Session,
    start_dt: Optional[datetime] = None,
    end_dt: Optional[datetime] = None,
):
    """يحسب إحصائيات الـDashboard لفترة محددة (مستبعدة طلبات cancelled).

    لو start_dt و end_dt = None يرجع الإحصائيات الإجمالية (بدون فلتر تاريخ).
    """
    valid_order = Order.status != OrderStatus.cancelled.value
    date_filter = []
    if start_dt is not None:
        date_filter.append(Order.created_at >= start_dt)
    if end_dt is not None:
        date_filter.append(Order.created_at < end_dt)

    def _apply_date(stmt):
        if date_filter:
            stmt = stmt.where(*date_filter)
        return stmt

    totals_stmt = _apply_date(
        select(
            func.count(Order.id),
            func.coalesce(func.sum(Order.total), 0),
        ).where(valid_order)
    )
    total_orders, total_revenue = db.execute(totals_stmt).one()
    total_orders = int(total_orders or 0)
    total_revenue = Decimal(total_revenue or 0)
    aov = (total_revenue / Decimal(total_orders)) if total_orders else Decimal("0")

    distinct_customers_stmt = _apply_date(
        select(func.count(distinct(Order.customer_id))).where(
            valid_order, Order.customer_id.is_not(None)
        )
    )
    distinct_customers = int(db.scalar(distinct_customers_stmt) or 0)

    if start_dt is None:
        total_customers = int(db.scalar(select(func.count(Customer.id))) or 0)
        new_customers = total_customers
        returning_customers = 0
    else:
        new_customers = int(
            db.scalar(
                select(func.count(Customer.id)).where(Customer.first_order_at >= start_dt)
            ) or 0
        )
        returning_customers = max(distinct_customers - new_customers, 0)

    pickup_stmt = _apply_date(
        select(func.count(Order.id))
        .join(DeliveryOption, Order.delivery_option_id == DeliveryOption.id)
        .where(valid_order, DeliveryOption.delivery_type == "pickup")
    )
    delivery_stmt = _apply_date(
        select(func.count(Order.id))
        .join(DeliveryOption, Order.delivery_option_id == DeliveryOption.id)
        .where(valid_order, DeliveryOption.delivery_type.in_(["fixed", "zone"]))
    )
    pickup_orders = int(db.scalar(pickup_stmt) or 0)
    delivery_orders = int(db.scalar(delivery_stmt) or 0)

    coupon_orders_stmt = _apply_date(
        select(func.count(Order.id)).where(valid_order, Order.coupon_id.is_not(None))
    )
    coupon_orders = int(db.scalar(coupon_orders_stmt) or 0)

    top_products_stmt = _apply_date(
        select(
            OrderItem.product_name,
            func.sum(OrderItem.quantity),
            func.coalesce(func.sum(OrderItem.line_total), 0),
        )
        .join(Order, OrderItem.order_id == Order.id)
        .where(valid_order)
        .group_by(OrderItem.product_name)
        .order_by(func.sum(OrderItem.quantity).desc(), OrderItem.product_name)
        .limit(5)
    )
    top_products = db.execute(top_products_stmt).all()

    top_coupons_stmt = _apply_date(
        select(
            Coupon.code,
            func.count(Order.id),
        )
        .join(Order, Order.coupon_id == Coupon.id)
        .where(valid_order, Order.coupon_id.is_not(None))
        .group_by(Coupon.code)
        .order_by(func.count(Order.id).desc(), Coupon.code)
        .limit(5)
    )
    top_coupons = db.execute(top_coupons_stmt).all()

    return {
        "total_orders": total_orders,
        "total_revenue": total_revenue,
        "aov": aov,
        "active_customers": distinct_customers,
        "new_customers": new_customers,
        "returning_customers": returning_customers,
        "pickup_orders": pickup_orders,
        "delivery_orders": delivery_orders,
        "coupon_orders": coupon_orders,
        "top_products": top_products,
        "top_coupons": top_coupons,
    }


def parse_dashboard_range(request: Request) -> tuple[Optional[datetime], Optional[datetime], str]:
    """يدعم range: today / 7d / 30d / all + تواريخ مخصصة from/to."""
    range_value = (request.query_params.get("range") or "all").lower().strip()
    if range_value not in {"today", "7d", "30d", "all"}:
        range_value = "all"
    now = datetime.now(timezone.utc)
    if range_value == "today":
        start_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt = start_dt + timedelta(days=1)
    elif range_value == "7d":
        start_dt = now - timedelta(days=7)
        end_dt = now + timedelta(days=1)
    elif range_value == "30d":
        start_dt = now - timedelta(days=30)
        end_dt = now + timedelta(days=1)
    else:
        start_dt = None
        end_dt = None
    return start_dt, end_dt, range_value


def customer_rows(
    db: Session,
    sort: str = "spent",
    start_dt: Optional[datetime] = None,
    end_dt: Optional[datetime] = None,
    min_orders: Optional[int] = None,
    q: Optional[str] = None,
):
    valid_order = Order.status != OrderStatus.cancelled.value
    total_orders = func.count(Order.id).filter(valid_order)
    total_spent = func.coalesce(func.sum(Order.total).filter(valid_order), 0)
    statement = (
        select(Customer, total_orders.label("total_orders"), total_spent.label("total_spent"))
        .outerjoin(Order, Order.customer_id == Customer.id)
        .group_by(Customer.id)
    )
    if start_dt is not None:
        statement = statement.where(Order.created_at >= start_dt)
    if end_dt is not None:
        statement = statement.where(Order.created_at < end_dt)
    if min_orders is not None:
        statement = statement.having(total_orders >= min_orders)
    if q:
        normalized = normalize_phone(q)
        conditions = [Customer.name.ilike(f"%{q.lower()}%")]
        if normalized:
            phone_patterns = [f"%{normalized}%"]
            if normalized.startswith("0") and len(normalized) > 1:
                phone_patterns.append(f"%{normalized[1:]}%")
            elif normalized.startswith("20") and len(normalized) > 2:
                phone_patterns.append(f"%0{normalized[2:]}%")
            conditions.append(or_(*[Customer.phone.like(p) for p in phone_patterns]))
        statement = statement.where(or_(*conditions))
    if sort == "orders":
        statement = statement.order_by(total_orders.desc(), total_spent.desc())
    elif sort == "last":
        statement = statement.order_by(Customer.last_order_at.desc())
    else:
        statement = statement.order_by(total_spent.desc(), total_orders.desc())
    return db.execute(statement).all()


@router.get("", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db), admin: Admin = Depends(get_current_admin)) -> HTMLResponse:
    start_dt, end_dt, range_value = parse_dashboard_range(request)
    analytics = dashboard_analytics(db, start_dt, end_dt)
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=today_start.weekday())
    month_start = today_start.replace(day=1)
    latest_orders_query = (
        select(Order)
        .options(
            selectinload(Order.items).selectinload(OrderItem.addons),
            selectinload(Order.items).selectinload(OrderItem.options),
            selectinload(Order.delivery_option),
            selectinload(Order.coupon),
            selectinload(Order.branch),
        )
        .order_by(Order.created_at.desc())
        .limit(8)
    )
    if start_dt is not None:
        latest_orders_query = latest_orders_query.where(Order.created_at >= start_dt)
        if end_dt is not None:
            latest_orders_query = latest_orders_query.where(Order.created_at < end_dt)
    latest_orders = db.scalars(latest_orders_query).all()
    _, top_customers = get_dashboard_stats(db)
    # نفس `allowed_by_order` بتاع صفحة الطلبات: الجدول في
    # `partials/orders_table.html` بيعرض أزرار الخطوات الجاية، والـ
    # partial ده مشترك بين اللوحة وصفحة الطلبات.
    allowed_by_order: dict[int, list[str]] = {}
    for order in latest_orders:
        try:
            current = coerce_order_status(order.status)
        except ValueError:
            allowed_by_order[order.id] = [s.value for s in OrderStatus]
            continue
        allowed_by_order[order.id] = [
            s.value for s in allowed_next_statuses(current)
        ] or [order.status]

    context = {
        "request": request,
        "admin": admin,
        "settings": get_or_create_settings(db),
        "range_value": range_value,
        "analytics": analytics,
        "latest_orders": latest_orders,
        "allowed_by_order": allowed_by_order,
        "top_customers": top_customers,
        "status_labels": STATUS_LABELS,
    }
    return templates.TemplateResponse("admin/dashboard.html", context)


@router.get("/categories", response_class=HTMLResponse)
def categories_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    categories = db.scalars(select(Category).order_by(Category.sort_order, Category.name)).all()
    error = request.query_params.get("error")
    error_message = None
    if error == "has_products":
        error_message = "لا يمكن حذف هذا القسم لأنه يحتوي على منتجات. يرجى نقل المنتجات أو حذفها أولًا ثم إعادة المحاولة."
    return templates.TemplateResponse(
        "admin/categories.html",
        {"request": request, "categories": categories, "error_message": error_message},
    )


@router.post("/categories")
async def save_category(
    id: Optional[int] = Form(default=None),
    name: str = Form(...),
    description: Optional[str] = Form(default=None),
    sort_order: int = Form(default=0),
    is_active: bool = Form(default=False),
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
    if image and image.filename:
        category.image_url = await save_image(image, UPLOADS_DIR / "categories")
    db.add(category)
    db.commit()
    return redirect("/admin/categories")


@router.post("/categories/{category_id}/delete")
def delete_category(category_id: int, request: Request, db: Session = Depends(get_db)) -> RedirectResponse:
    category = db.get(Category, category_id)
    if category is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Category not found")
    has_products = db.scalar(select(func.count(Product.id)).where(Product.category_id == category_id))
    if has_products:
        return redirect("/admin/categories?error=has_products")
    db.delete(category)
    db.commit()
    return redirect("/admin/categories")


@router.get("/products", response_class=HTMLResponse)
def products_page(request: Request, q: Optional[str] = None, category_id: Optional[int] = None, db: Session = Depends(get_db)) -> HTMLResponse:
    categories = db.scalars(select(Category).order_by(Category.sort_order, Category.name)).all()
    statement = select(Product).options(selectinload(Product.category))
    query = (q or "").strip()
    if query:
        terms = build_search_variants(query)
        if terms:
            statement = statement.where(or_(*[Product.name.ilike(f"%{term}%") for term in terms]))
        else:
            statement = statement.where(Product.name.ilike(f"%{query}%"))
    if category_id:
        statement = statement.where(Product.category_id == category_id)
    products = db.scalars(statement.order_by(Product.sort_order, Product.name)).all()
    return templates.TemplateResponse(
        "admin/products.html",
        {"request": request, "categories": categories, "products": products, "q": query, "category_id": category_id},
    )


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
    is_featured: bool = Form(default=False),
    is_offer: bool = Form(default=False),
    image: Optional[UploadFile] = File(default=None),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    if base_price < 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "السعر الأساسي لا يمكن أن يكون سالبًا")
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
    product.is_featured = is_featured
    product.is_offer = is_offer
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


@router.get("/products/{product_id}/options", response_class=HTMLResponse)
def manage_options(product_id: int, request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found")
    groups = db.scalars(
        select(ProductOptionGroup)
        .where(ProductOptionGroup.product_id == product_id)
        .options(selectinload(ProductOptionGroup.options))
        .order_by(ProductOptionGroup.sort_order, ProductOptionGroup.name)
    ).all()
    for group in groups:
        group.options = sorted(group.options, key=lambda o: (o.sort_order, o.name))
    return templates.TemplateResponse(
        "admin/options.html",
        {"request": request, "product": product, "groups": groups},
    )


@router.post("/products/{product_id}/options/groups")
def save_option_group(
    product_id: int,
    id: Optional[int] = Form(default=None),
    name: str = Form(...),
    is_required: bool = Form(default=False),
    allow_multiple: bool = Form(default=False),
    min_selections: int = Form(default=0),
    max_selections_raw: Optional[str] = Form(default=None, alias="max_selections"),
    sort_order: int = Form(default=0),
    is_available: bool = Form(default=False),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found")
    group = db.get(ProductOptionGroup, id) if id else ProductOptionGroup(product_id=product_id)
    if id and (group is None or group.product_id != product_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Option group not found")
    max_selections: Optional[int] = None
    if max_selections_raw not in (None, ""):
        try:
            max_selections = int(max_selections_raw)
        except ValueError:
            max_selections = None
    if min_selections < 0:
        min_selections = 0
    if max_selections is not None and max_selections < 1:
        max_selections = None
    group.name = name
    group.is_required = is_required
    group.allow_multiple = allow_multiple
    group.min_selections = min_selections
    group.max_selections = max_selections
    group.sort_order = sort_order
    group.is_available = is_available
    db.add(group)
    db.commit()
    return redirect(f"/admin/products/{product_id}/options")


@router.post("/products/{product_id}/options/groups/{group_id}/delete")
def delete_option_group(product_id: int, group_id: int, db: Session = Depends(get_db)) -> RedirectResponse:
    group = db.get(ProductOptionGroup, group_id)
    if group is None or group.product_id != product_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Option group not found")
    db.delete(group)
    db.commit()
    return redirect(f"/admin/products/{product_id}/options")


@router.post("/products/{product_id}/options")
def save_product_option(
    product_id: int,
    group_id: int = Form(...),
    id: Optional[int] = Form(default=None),
    name: str = Form(...),
    price_adjustment: Decimal = Form(default=Decimal("0.00")),
    is_available: bool = Form(default=False),
    sort_order: int = Form(default=0),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found")
    group = db.get(ProductOptionGroup, group_id)
    if group is None or group.product_id != product_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Option group not found")
    option = db.get(ProductOption, id) if id else ProductOption(product_id=product_id)
    if id and (option is None or option.product_id != product_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Option not found")
    option.group_id = group.id
    option.name = name
    option.price_adjustment = price_adjustment
    option.is_available = is_available
    option.sort_order = sort_order
    db.add(option)
    db.commit()
    return redirect(f"/admin/products/{product_id}/options")


@router.post("/products/{product_id}/options/{option_id}/delete")
def delete_product_option(product_id: int, option_id: int, db: Session = Depends(get_db)) -> RedirectResponse:
    option = db.get(ProductOption, option_id)
    if option is None or option.product_id != product_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Option not found")
    db.delete(option)
    db.commit()
    return redirect(f"/admin/products/{product_id}/options")


ORDERS_PAGE_SIZE = 25


def _build_orders_base_query(
    db: Session,
    status_filter: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    q: Optional[str] = None,
):
    statement = select(Order)
    filters = []

    if status_filter:
        filters.append(Order.status == status_filter)
    if date_from:
        parsed_from = datetime.strptime(date_from, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        filters.append(Order.created_at >= parsed_from)
    if date_to:
        parsed_to = datetime.strptime(date_to, "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1)
        filters.append(Order.created_at < parsed_to)

    search_query = (q or "").strip()
    if search_query:
        order_id_match: Optional[int] = None
        if search_query.isdigit() and len(search_query) <= 8:
            order_id_match = int(search_query)

        search_conditions = []
        if order_id_match is not None:
            search_conditions.append(Order.id == order_id_match)
        search_conditions.append(func.lower(Order.customer_name).like(f"%{search_query.lower()}%"))
        normalized = normalize_phone(search_query)
        if normalized:
            phone_patterns = [f"%{normalized}%"]
            if normalized.startswith("0") and len(normalized) > 1:
                phone_patterns.append(f"%{normalized[1:]}%")
            elif normalized.startswith("20") and len(normalized) > 2:
                phone_patterns.append(f"%0{normalized[2:]}%")
            search_conditions.append(or_(*[Order.customer_phone.like(p) for p in phone_patterns]))
        filters.append(or_(*search_conditions))

    for f in filters:
        statement = statement.where(f)
    return statement


@router.get("/orders", response_class=HTMLResponse)
def orders_page(
    request: Request,
    status_filter: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    q: Optional[str] = None,
    page: int = 1,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    try:
        if date_from:
            datetime.strptime(date_from, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "صيغة التاريخ غير صحيحة")
    try:
        if date_to:
            datetime.strptime(date_to, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "صيغة التاريخ غير صحيحة")

    base_query = _build_orders_base_query(db, status_filter, date_from, date_to, q)
    base_subquery = base_query.subquery()

    orders_count = db.scalar(select(func.count()).select_from(base_subquery)) or 0
    sales_total = db.scalar(select(func.coalesce(func.sum(base_subquery.c.total), 0))) or 0

    try:
        page = max(1, int(page))
    except (TypeError, ValueError):
        page = 1

    total_pages = max(1, (orders_count + ORDERS_PAGE_SIZE - 1) // ORDERS_PAGE_SIZE) if orders_count else 1
    if page > total_pages:
        page = total_pages
    offset = (page - 1) * ORDERS_PAGE_SIZE

    statement = (
        base_query
        .options(
            selectinload(Order.items).selectinload(OrderItem.addons),
            selectinload(Order.items).selectinload(OrderItem.options),
            selectinload(Order.delivery_option),
            selectinload(Order.coupon),
            selectinload(Order.branch),
        )
        .offset(offset)
        .limit(ORDERS_PAGE_SIZE)
    )
    orders = db.scalars(statement).all()

    # لكل طلب: الحالات المسموح ينتقل لها من حالته الحالية. القائمة
    # بتعرض الخيارات دي بس بدل كل الحالات، عشان `<select>` مقصدش
    # يرمي 400 على الموظف لما يختار حالة مش على مسار الطلب. التحقق في
    # `update_order_status` بيفضل موجود — الـdropdown راحة للمستخدم،
    # مش بوابة أمان.
    allowed_by_order: dict[int, list[str]] = {}
    for order in orders:
        try:
            current = coerce_order_status(order.status)
        except ValueError:
            # صف بحالة V1/تالفة: نعرض كل الحالات عشان الأدمن يقدر
            # يصلّحه. التحقق في الـPOST بيرمي الـValueError دي كمان.
            allowed_by_order[order.id] = [s.value for s in OrderStatus]
            continue
        allowed_by_order[order.id] = [
            s.value for s in allowed_next_statuses(current)
        ] or [order.status]

    search_query = (q or "").strip()
    return templates.TemplateResponse(
        "admin/orders.html",
        {"request": request, "orders": orders, "statuses": list(OrderStatus), "status_labels": STATUS_LABELS,
         "allowed_by_order": allowed_by_order,
         "status_filter": status_filter, "date_from": date_from or "", "date_to": date_to or "",
         "q": search_query, "page": page, "total_pages": total_pages,
         "orders_count": orders_count, "sales_total": sales_total},
    )


# ---------------------------------------------------------------------------
# Phase 3 — إشعارات تغيير الحالة + سجل الحالة
# ---------------------------------------------------------------------------
#
# ## نفس قاعدة `public.py`
#
# تغيير حالة الطلب في الداتابيز **أهم** من إشعار العميل. لو العميل مش
# مشترك، أو المزوّد واقع، أو في bug — الطلب لازم يتغيّر. عشان كده
# الإشعار **بعد** الـcommit دايماً، والـhelpers دي بتفضل فاضية الأيدي.
#
# ## ليه `admin: Admin = Depends(get_current_admin)` في الـsignature
#
# الـrouter متعمّل عليه `dependencies=[Depends(get_current_admin)]` على
# مستوى الراوتر كله — فالـdependency شغّالة بس مش متاحة كـparameter،
# و`get_current_admin` **مش** بيحط نفسه في `request.state`. فإما نضيفها
# في الـsignature (وFastAPI بيعمل cache، فمش قراءة زيادة)، أو نخزّنها
# في `request.state` — والأولى أوضح.


def _safe_record_status(
    db: Session, order: Order, status_value: str, admin: Optional[Admin]
) -> None:
    """يسجّل سطر حالة جديد — ما بيرميش.

    لو الأدمن مش معروف (مفيش `request.state.admin`)، السجل بيتسجل
    من غير `changed_by_*`. **مش بنرمي** — مفيش داعي نقفل شاشة الأدمن
    عشان سطر في جدول تاريخ.
    """
    try:
        push.record_status_change(
            db,
            order,
            status_value,
            admin_id=admin.id if admin is not None else None,
            admin_username=admin.username if admin is not None else None,
        )
    except Exception:
        logger.exception(
            "admin_status_history_write_failed",
            extra={"event": "admin_status_history_write_failed", "order_id": order.id},
        )
        try:
            db.rollback()
        except Exception:
            logger.exception("admin_status_history_rollback_failed")


def _safe_notify_customer(db: Session, order: Order) -> None:
    """إشعار تغيير الحالة للعميل — ما بيرميش.

    **الـtracking URL:** رابط التتبّع الكامل بالـtoken **مش متاح هنا**
    — السيرفر بيخزّن hash التوكن بس (عمداً، عشان تسريب الداتابيز ما
    يديش روابط تتبّع). فالإشعار بيروح لصفحة السجل
    (`/orders/history`) بدل `/track?token=...`، وهي الصفحة اللي على
    جهاز العميل نفسه فبتقرا التوكنات من `localStorage` وبتفتح طلباته.

    ده أفضل من رابط مكسور، والأمانة بتضرب عرضين: مفيش توكن في
    الداتابيز، ومفيش رابط ينتهي بخطأ 404.
    """
    try:
        if not push.is_configured():
            return
        subscriptions = push.subscriptions_for_order(db, order)
        if not subscriptions:
            return
        summary = push.send_and_commit(
            db,
            subscriptions,
            payload=push.order_status_payload(
                order,
                admin_status_label(order.status),
                tracking_url="/orders/history",
            ),
            audience=push.AUDIENCE_CUSTOMER,
            order_id=order.id,
        )
        logger.info(
            "admin_order_status_push_sent",
            extra={
                "event": "admin_order_status_push_sent",
                "order_id": order.id,
                "status": order.status,
                "total": summary.total,
                "sent": summary.sent,
                "failed": summary.failed,
                "invalid": summary.invalid,
            },
        )
    except Exception:
        logger.exception(
            "admin_order_status_push_failed",
            extra={
                "event": "admin_order_status_push_failed",
                "order_id": order.id,
            },
        )
        try:
            db.rollback()
        except Exception:
            logger.exception("admin_order_status_push_rollback_failed")


def admin_status_label(value: str) -> str:
    """تسمية الأدمن للحالة، مع fallback للتسمية اللي للعميل.

    `STATUS_LABELS` (أدمن) و`STATUS_LABELS` في `app.order_tracking.py`
    (عميل) **مختلفين عن قصد**: "قيد الانتظار" مقابل "تم استلام الطلب".
    الإشعار بيتبعت للعميل، فالتسمية الصح هي اللي بتظهر في `/track`.
    بنستخدم تسمية الأدمن بس لو مفيش مقابل في تسمية العميل.
    """
    try:
        return order_tracking_status_label(value)
    except Exception:
        return STATUS_LABELS.get(value, value)


@router.post("/orders/{order_id}/status")
def update_order_status(
    order_id: int,
    admin: Admin = Depends(get_current_admin),
    status_value: OrderStatus = Form(...),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """تغيير حالة الطلب.

    **بيتحقق من صحة الانتقال**، مش بس إن الحالة جديدة معروفة. من غير
    ده، أي حد يقدر يرجّع طلب مسلّم لـ`pending` بضغطة على `<select>`
    قديم في صفحة مفتوحة — وديBug طريقة بتأكل طلبات من غير ما حد ياخد
    باله.

    الانتقال لنفس الحالة مسموح (الـform بيرسل القيمة الحالية عند فتح
    الصفحة). `confirmed → preparing` مسموح ومقصود: موظف ممكن يكون أكّد
    وبعدين حد كمله، والانتظار لحد التأكيد بس إتفهّم.

    ملاحظة: `payment_status` **مش** بيتغير هنا. حالة الدفع منفصلة عن
    حالة الطلب (كاش لسه ما اتدفعش = `preparing` + `pending`)، ولها
    endpoint لوحدها: `/orders/{id}/payment`.
    """
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")

    try:
        current = coerce_order_status(order.status)
    except ValueError:
        # صف بحالة غريبة (legacy). أي حالة جديدة مقبولة منه، عشان
        # الأدمن يقدر يصلّح صف تالف من غير تدخل يدوي في الداتابيز.
        order.status = status_value.value
        _safe_record_status(db, order, status_value.value, admin)
        db.commit()
        _safe_notify_customer(db, order)
        return redirect(f"/admin/orders/{order_id}")

    if not is_transition_allowed(current, status_value):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"مش ممكن تنقل الطلب من «{STATUS_LABELS.get(current.value, current.value)}» "
            f"إلى «{STATUS_LABELS.get(status_value.value, status_value.value)}»",
        )

    order.status = status_value.value
    # سطر السجل **قبل** الـcommit عشان يتحفظ مع تغيير الحالة في معاملة
    # واحدة: يا يتسجل الاثنان يا ما يتسجلش. لو فشلت الكتابة، الحالة
    # نفسها هتتغير والنظام هيكمّل شغال (الـhelper ما بيرميش).
    _safe_record_status(db, order, status_value.value, admin)
    db.commit()

    # الإشعار **بعد** الـcommit: الإرسال بيبعت على الشبكة، ولو جا قبل
    # الـcommit كان أي timeout هيعمل rollback لتغيير الحالة نفسه.
    _safe_notify_customer(db, order)
    return redirect(f"/admin/orders/{order_id}")


# ---------------------------------------------------------------------------
# Phase 3 — اشتراك إشعارات الأدمن
# ---------------------------------------------------------------------------
#
# ## ليه النداء من `/admin/api/push/*` مش `/api/push/*`
#
# عشان نضرب بحاجتين بنفس الطلب:
# 1. `/api` خاصة للعميل ومحميجة برمز التتبّع بس. مسار الأدمن محمي
#    بجلسة الأدمن + CSRF. خلط الاتنين معناه endpoint بيبقى محمي
#    بإيهما في حالة سكربت.
# 2. `verify_admin_csrf` على مستوى الراوتر: أي `POST` من `/admin/*`
#    لازم يكون عنده CSRF token. لو الـsubscribe اتنادى بـ`fetch` من
#    صفحة الأدمن بدون ما نبعته، الـrequest هيترفض بـ403 — وده بالظبط
#    اللي عايزينه.
#
# **ليه `admin_id` مطلوب في كل اشتراك:** طلب واحد ممكن يبقى مفتوح
# من 3 موظفين. كل واحد فيهم عايز إشعار على جهازه. لو اشتركنا
# بendpoint واحد من غير `admin_id`، تغيير حالة في موبايل أي حد بيوصل
# للأدمن ده. `endpoint` مفتاح فريد، flog = آخر تسجيل يكسب.
#
# **Mobile بدون VAPID:** موبايل iOS/Android بيشتغل push من غير VAPID،
# لكن **الويب** محتاجه. زرار الأدمن بيختفي لو `enabled = false`.


def _admin_push_out(admin: Admin, *, created: bool) -> PushSubscriptionOut:
    return PushSubscriptionOut(
        subscribed=True,
        created=created,
        public_key=push.public_key(),
        enabled=True,
    )


@router.get("/api/push/public-key")
def admin_push_public_key() -> dict:
    """نفس `/api/push/public-key` — النسخة الأدمن.

    نفس الدالة بالظبط بالتصميم: المفتاح عام، ومفيش سبب نخفيه عن
    الأدمن (هو أصلاً بيشوف كل حاجة). الغرق في نسخة الأدمن إنه
    بياخد CSRF تلقائياً على أي `POST` لاحق.
    """
    return {"public_key": push.public_key(), "enabled": push.is_configured()}


@router.get("/api/push/status", response_model=PushStatusOut)
def admin_push_status(
    admin: Admin = Depends(get_current_admin), db: Session = Depends(get_db)
) -> PushStatusOut:
    """هل جهاز الأدمن ده مشترك في إشعارات الطلبات الجديدة؟

    `subscribed` بيتحسب من اشتراكات **هذا الأدمن** مش كل الأدمن. لو
    كل الأدمن مشترك، مينفعش نخفي الزرار من اللي لسه ما اشتركش
    ويظهر له رسالة "مفعّل" وهي مش مفعّلة على جهازه.
    """
    subscribed = bool(push.subscriptions_for_admin(db, admin.id))
    return PushStatusOut(
        supported=push.is_configured(),
        # زي ما في `public`: المتصفح هو اللي بيعرف الإذن الحقيقي.
        permission="default",
        subscribed=subscribed,
        public_key=push.public_key(),
        server_enabled=push.is_configured(),
    )


@router.post("/api/push/subscribe", response_model=PushSubscriptionOut)
def admin_push_subscribe(
    payload: PushSubscriptionIn,
    request: Request,
    admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> PushSubscriptionOut:
    """اشتراك جهاز الأدمن في "طلب جديد".

    **idempotent** على نفس الـendpoint: فتح اللوحة مرتين أو reload
    بعد Ctrl+F5 مايعملشش صف جديد.

    **تسجيل نفس الجهاز من حسابين:** الـendpoint فريد عالمياً. لو
    موظف اشترك من جهازه، ثم موظف تاني فتح نفس اللاب على نفس
    الجهاز، السجل بيتنقل للأخير (`admin_id` بيتحدّث). ده سلوك مقصود:
    الجهاز نفسه، والمستخدم الفعلي هو اللي قاعد قدامه دلوقتي.
    """
    if not push.is_configured():
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "الإشعارات غير متاحة حالياً"
        )

    existing = push.find_subscription(db, payload.endpoint)
    push.register_subscription(
        db,
        endpoint=payload.endpoint,
        p256dh=payload.p256dh,
        auth=payload.auth,
        customer_id=None,
        admin_id=admin.id,
        user_agent=request.headers.get("user-agent", "")[:400] or None,
        device=payload.device,
    )
    db.commit()
    return _admin_push_out(admin, created=existing is None)


@router.post("/api/push/unsubscribe", response_model=dict)
def admin_push_unsubscribe(
    payload: PushUnsubscribeIn,
    admin: Admin = Depends(get_current_admin),
    db: Session = Depends(get_db),
) -> dict:
    """إلغاء اشتراك الأدمن.

    **بيتحقق إن الاشتراك ده للأدمن ده:** `/admin` كله محمي
    بجلسة، بس زميل ممكن يكون مشترك على نفس الـLAN — والـendpoint لو
    حد بعت endpoint زميله من غير ما يبقى زميله، من غير الفحص ده
    هنلغي إشعارات حد تاني.
    """
    subscription = push.find_subscription(db, payload.endpoint)
    if subscription is None:
        return {"unsubscribed": False}
    if subscription.admin_id != admin.id:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "مش مسموح تلغي اشتراك حد تاني"
        )
    push.unsubscribe(db, payload.endpoint)
    db.commit()
    return {"unsubscribed": True}


@router.get("/orders/{order_id}", response_class=HTMLResponse)
def order_detail_page(order_id: int, request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    """صفحة الطلب الواحد — كل التفاصيل في مكان واحد.

    V1 كان بيخلّي الموظف يفتح صفحة الطلبات ويدوّر على السطر الصح من غير
    ما يشوف تفاصيل. وبما إن دلوقتي في لقطة رسوم + لقطات أسماء + حالة
    دفع + تتبع، كل ده محتاج شاشة.
    """
    order = db.scalar(
        select(Order)
        .where(Order.id == order_id)
        .options(
            selectinload(Order.items).selectinload(OrderItem.options),
            selectinload(Order.items).selectinload(OrderItem.addons),
            selectinload(Order.delivery_option),
            selectinload(Order.delivery_zone),
            selectinload(Order.branch),
            selectinload(Order.coupon),
            selectinload(Order.customer),
        )
    )
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")

    try:
        current_status = coerce_order_status(order.status)
    except ValueError:
        current_status = None

    return templates.TemplateResponse(
        "admin/order_detail.html",
        {
            "request": request,
            "order": order,
            "current_status": current_status,
            "allowed_statuses": allowed_next_statuses(current_status) if current_status else (),
            # صف بحالة V1/تالفة: `allowed_statuses` فاضي، و`<select>`
            # التصليح بيحتاج كل الحالات عشان يبقى فيه حاجة تختارها.
            "statuses": list(OrderStatus),
            "status_labels": STATUS_LABELS,
            "payment_status_labels": PAYMENT_STATUS_LABELS,
            "settings": get_or_create_settings(db),
            "subtotal_label": lambda value: f"{value:.2f}",
        },
    )


@router.post("/orders/{order_id}/payment")
def update_order_payment(
    order_id: int,
    payload: PaymentStatusUpdateIn = Form(...),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """تحديث حالة الدفع من الأدمن.

    الداتايز بتتحقق بـ`PaymentStatus` على مستوى الـschema، فحالة مش
    معروفة بترجّع 422 من FastAPI قبل ما الكود يوصل هنا.

    `payment_confirmed_at` بيتحط أول مرة بس لحد `paid`: العميل بيسأل
    "امتى اتسجل الدفع؟" وسؤال واحد. لو الأدمن عدّل الحالة بعد كده،
    الوقت الأصلي هو المهم.

    حالة الطلب **مش** بتتغير هنا. طلب اتشال كاش (الدفع `failed`) لسه
    محتاج مراجعة من المخزن أو يفضل ملغي.
    """
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")

    order.payment_status = payload.payment_status.value
    if payload.payment_reference is not None:
        stripped = payload.payment_reference.strip()
        order.payment_reference = stripped or None
    if payload.payment_status.value == "paid" and order.payment_confirmed_at is None:
        order.payment_confirmed_at = datetime.now(timezone.utc)

    db.commit()
    return redirect(f"/admin/orders/{order_id}")


@router.post("/orders/{order_id}/delivery-fee")
def set_order_delivery_fee(
    order_id: int,
    payload: AdminDeliveryFeeIn = Form(...),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """تحديد رسوم طلب `call_center` يدوياً.

    بتعيد حساب الإجمالي كمان — الرسوم مش بتتحدّد مستقلة عن الإجمالي،
    `total = subtotal - discount + delivery_fee`. لو بسطرنا العمود ده
    من غير الإجمالي، العرض في اللوحة وفي صفحة العميل هيبقوا
    متعارضين. الخصم بيتساب من الـsubtotal زي ما هو، فالنتيجة النهائية
    للعميل هي اللي بتتغير بزيادة/نقص الرسوم.

    `delivery_fee_pending` بيبقى false بعدها: الأدمن حدّد رقم، فمفيش
    حاجة معلّقة.
    """
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Order not found")

    fee = quantize_money(payload.delivery_fee)
    order.delivery_fee = fee
    order.delivery_fee_pending = False
    order.total = quantize_money(
        order.subtotal - order.discount_amount + fee
    )
    if order.total < ZERO:
        # الخصم أكبر من الـsubtotal + رسوم. V1 كان بيقفل الإجمالي عند
        # صفر وقت الإنشاء، فلو الأدمن رفع الرسوم بعد كده مش لازم
        # الإجمالي يبقى بالسالب. الصفر أقل معقول.
        order.total = ZERO

    db.commit()
    return redirect(f"/admin/orders/{order_id}")


def _coupon_status(coupon: Coupon, now: datetime) -> str:
    if not coupon.is_active:
        return "معطل"
    if now < coupon.start_date:
        return "لم يبدأ"
    if now > coupon.end_date:
        return "منتهي"
    if coupon.usage_limit is not None and coupon.usage_count >= coupon.usage_limit:
        return "مستنفد"
    return "فعال"


@router.get("/coupons", response_class=HTMLResponse)
def coupons_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    coupons = db.scalars(select(Coupon).order_by(Coupon.created_at.desc())).all()
    return templates.TemplateResponse(
        "admin/coupons.html",
        {"request": request, "coupons": coupons, "now": datetime.now(timezone.utc), "status_of": _coupon_status},
    )


@router.post("/coupons")
def save_coupon(
    id: Optional[int] = Form(default=None),
    code: str = Form(...),
    discount_type: str = Form(...),
    discount_value: Decimal = Form(...),
    minimum_order: Decimal = Form(default=Decimal("0.00")),
    start_date: datetime = Form(...),
    end_date: datetime = Form(...),
    usage_limit_raw: Optional[str] = Form(default=None, alias="usage_limit"),
    is_active: bool = Form(default=False),
    first_order_only: bool = Form(default=False),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    normalized_code = code.strip().upper()
    if not normalized_code:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Coupon code is required")
    if discount_type not in ("percentage", "fixed"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid discount type")
    if discount_value <= 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Discount value must be greater than zero")
    if discount_type == "percentage" and discount_value > 100:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Percentage discount cannot exceed 100")
    if end_date <= start_date:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "End date must be after start date")

    usage_limit: Optional[int] = None
    if usage_limit_raw not in (None, ""):
        try:
            usage_limit = int(usage_limit_raw)
        except ValueError:
            usage_limit = None
    if usage_limit is not None and usage_limit < 1:
        usage_limit = None

    coupon = db.get(Coupon, id) if id else Coupon(usage_count=0)
    if id and coupon is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Coupon not found")

    # البحث case-insensitive لأن التحقق عند الطلب يعتمد على upper().
    existing = db.scalar(select(Coupon).where(func.upper(Coupon.code) == normalized_code))
    if existing is not None and existing.id != coupon.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Coupon code already exists")

    coupon.code = normalized_code
    coupon.discount_type = discount_type
    coupon.discount_value = discount_value
    coupon.minimum_order = minimum_order
    coupon.start_date = start_date
    coupon.end_date = end_date
    coupon.usage_limit = usage_limit
    coupon.is_active = is_active
    coupon.first_order_only = first_order_only
    db.add(coupon)
    db.commit()
    return redirect("/admin/coupons")


@router.post("/coupons/{coupon_id}/delete")
def delete_coupon(coupon_id: int, db: Session = Depends(get_db)) -> RedirectResponse:
    coupon = db.get(Coupon, coupon_id)
    if coupon is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Coupon not found")
    # orders.coupon_id FK هو ON DELETE SET NULL فالحذف آمن — الخصم المحفوظ في الطلبات يفضل موجود.
    db.delete(coupon)
    db.commit()
    return redirect("/admin/coupons")


@router.get("/delivery-options", response_class=HTMLResponse)
def delivery_options_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    options = db.scalars(select(DeliveryOption).order_by(DeliveryOption.sort_order, DeliveryOption.name)).all()
    zones = db.scalars(select(DeliveryZone).order_by(DeliveryZone.sort_order, DeliveryZone.name)).all()
    return templates.TemplateResponse("admin/delivery_options.html", {"request": request, "options": options, "zones": zones})


@router.post("/delivery-zones")
def save_delivery_zone(
    id: Optional[int] = Form(default=None),
    name: str = Form(...),
    fee: Decimal = Form(default=Decimal("0.00")),
    sort_order: int = Form(default=0),
    is_active: bool = Form(default=False),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    if fee < 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "رسوم المنطقة لا يمكن أن تكون سالبة")
    zone = db.get(DeliveryZone, id) if id else DeliveryZone()
    if id and zone is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "منطقة التوصيل غير موجودة")
    zone.name = name.strip()
    zone.fee = fee
    zone.sort_order = sort_order
    zone.is_active = is_active
    db.add(zone)
    db.commit()
    return redirect("/admin/delivery-options")


@router.post("/delivery-zones/{zone_id}/delete")
def delete_delivery_zone(zone_id: int, db: Session = Depends(get_db)) -> RedirectResponse:
    """إلغاء تفعيل منطقة، مش حذفها.

    `db.delete` هنا كان بيحط `delivery_zone_id = NULL` في كل الطلبات
    اللي استخدمت المنطقة. الرسوم في الطلب بتفضل (في `orders.delivery_fee`)
    بس **اسم المنطقة** بيضيع، فلما الأدمن يفتح طلب من شهر فاتح بيشوف
    "توصيل" من غير اسم المنطقة اللي اتحسبت عليه الرسوم.

    soft delete بتخفي المنطقة من اختيار العميل الجديد وبتسيب كل
    الأرقام في مكانها. إعادة التفعيل لوحدها من نفس الشاشة.
    """
    zone = db.get(DeliveryZone, zone_id)
    if zone is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "منطقة التوصيل غير موجودة")
    zone.is_active = False
    db.commit()
    return redirect("/admin/delivery-options")


@router.get("/customers", response_class=HTMLResponse)
def customers_page(
    request: Request,
    sort: str = "spent",
    q: Optional[str] = None,
    range: str = "all",
    min_orders: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    if sort not in {"spent", "orders", "last"}:
        sort = "spent"

    start_dt, end_dt, _ = parse_dashboard_range(request)
    if date_from:
        try:
            start_dt = datetime.strptime(date_from, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            start_dt = None
    if date_to:
        try:
            end_dt = datetime.strptime(date_to, "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1)
        except ValueError:
            end_dt = None

    if min_orders is not None and min_orders < 1:
        min_orders = None

    return templates.TemplateResponse(
        "admin/customers.html",
        {
            "request": request,
            "customers": customer_rows(db, sort, start_dt, end_dt, min_orders, q),
            "sort": sort,
            "q": q or "",
            "range": range,
            "min_orders": min_orders,
            "date_from": date_from or "",
            "date_to": date_to or "",
            "settings": get_or_create_settings(db),
        },
    )


@router.get("/customers/export")
def export_customers(
    request: Request,
    sort: str = "spent",
    q: Optional[str] = None,
    range: str = "all",
    min_orders: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    db: Session = Depends(get_db),
) -> Response:
    # نفس منطق `customers_page` بالظبط. قبل التصحيح كان `range` متقبل
    # في الـsignature ومبتقراش منه حاجة: الأدمن بيفلتر "آخر 30 يوم"،
    # الشاشة بتظهر 12 عميل، والملف Between لو.out 400. الشاشة والملف
    # لازم يكونوا نفس الـquery، وإلا حد بيتعامل مع أرقام غلط.
    start_dt, end_dt, _ = parse_dashboard_range(request)
    if date_from:
        try:
            start_dt = datetime.strptime(date_from, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    if date_to:
        try:
            end_dt = datetime.strptime(date_to, "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1)
        except ValueError:
            pass
    if min_orders is not None and min_orders < 1:
        min_orders = None
    customers = customer_rows(db, sort, start_dt, end_dt, min_orders, q)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["الاسم", "رقم الهاتف", "عدد الطلبات", "إجمالي المشتريات", "أول طلب", "آخر طلب"])
    for customer, total_orders, total_spent in customers:
        writer.writerow([customer.name or "", customer.phone, total_orders, total_spent, customer.first_order_at, customer.last_order_at])
    return Response(
        content="\ufeff" + output.getvalue(), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=customers.csv"},
    )


@router.get("/customers/{customer_id}", response_class=HTMLResponse)
def customer_detail(request: Request, customer_id: int, db: Session = Depends(get_db)) -> HTMLResponse:
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Customer not found")

    orders = db.scalars(
        select(Order)
        .where(Order.customer_id == customer_id)
        .options(
            selectinload(Order.items).selectinload(OrderItem.addons),
            selectinload(Order.items).selectinload(OrderItem.options),
            selectinload(Order.delivery_option),
            selectinload(Order.coupon),
            selectinload(Order.branch),
        )
        .order_by(Order.created_at.desc())
    ).all()

    total_orders = sum(1 for o in orders if o.status != OrderStatus.cancelled.value)
    total_spent = sum(Decimal(o.total) for o in orders if o.status != OrderStatus.cancelled.value)
    avg_order = (total_spent / total_orders) if total_orders > 0 else Decimal("0.00")

    return templates.TemplateResponse(
        "admin/customer_detail.html",
        {
            "request": request,
            "customer": customer,
            "orders": orders,
            "total_orders": total_orders,
            "total_spent": total_spent,
            "avg_order": avg_order,
            "status_labels": STATUS_LABELS,
            "settings": get_or_create_settings(db),
        },
    )


@router.post("/delivery-options")
def save_delivery_option(
    id: Optional[int] = Form(default=None),
    name: str = Form(...),
    description: Optional[str] = Form(default=None),
    fee: Decimal = Form(default=Decimal("0.00")),
    estimated_time: Optional[str] = Form(default=None),
    sort_order: int = Form(default=0),
    delivery_type: str = Form(default="fixed"),
    is_active: bool = Form(default=False),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    if delivery_type not in {"pickup", "fixed", "zone", "call_center"}:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "نوع التوصيل غير صالح")
    if fee < 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "الرسوم لا يمكن أن تكون سالبة")
    option = db.get(DeliveryOption, id) if id else DeliveryOption()
    if id and option is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "طريقة التوصيل غير موجودة")
    option.name = name.strip()
    option.description = description or None
    # `pickup` و`call_center` الرسوم فيهم مش مستخدمة في الحساب (صفر +
    # pending). بنحفظ الرقم اللي بيه الأدمن عايز clarifies الأرقام، بس
    # `compute_pricing` هو اللي بيتجاهله — الحسبة-side مش form-side.
    option.fee = fee
    option.estimated_time = estimated_time or None
    option.sort_order = sort_order
    option.delivery_type = delivery_type
    option.is_active = is_active
    if delivery_type == "zone":
        zone_count = db.scalar(select(func.count(DeliveryZone.id)).where(DeliveryZone.is_active.is_(True)))
        if zone_count == 0:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "لا يمكن تفعيل التوصيل حسب المنطقة قبل إضافة منطقة توصيل واحدة على الأقل")
    db.add(option)
    db.commit()
    return redirect("/admin/delivery-options")


@router.post("/delivery-options/{option_id}/delete")
def delete_delivery_option(option_id: int, db: Session = Depends(get_db)) -> RedirectResponse:
    option = db.get(DeliveryOption, option_id)
    if option is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "طريقة التوصيل غير موجودة")
    # نفس منطق المنطقة: soft delete مش حذف. `delivery_option_id = NULL`
    # كانت بتخلي طلب قديم يفقد اسم طريقة التوصيل اللي اتحسبت عليه.
    option.is_active = False
    db.commit()
    return redirect("/admin/delivery-options")


# ============================================================
# Phase 2 — إدارة طرق الدفع
# ============================================================


@router.get("/payment-methods", response_class=HTMLResponse)
def payment_methods_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    methods = db.scalars(
        select(PaymentMethod).order_by(PaymentMethod.sort_order, PaymentMethod.name)
    ).all()
    return templates.TemplateResponse(
        "admin/payment_methods.html",
        {
            "request": request,
            "methods": methods,
            "usage_counts": _payment_method_usage(db),
            "settings": get_or_create_settings(db),
        },
    )


def _payment_method_usage(db: Session) -> dict[int, int]:
    """عدد الطلبات لكل طريقة — عشان الأدمن يفهم impact الإلغاء.

    من غير الأرقام دي، الأدمن ممكن يدوس "تعطيل" على طريقة فيها 200
    طلب من غير ما يعرف. الأرقام للعلم فقط: التعطيل مسموح في كل الأحوال
    (الطلبات القديمة شايفة اللقطة بتاعتها).
    """
    rows = db.execute(
        select(Order.payment_method_id, func.count(Order.id))
        .where(Order.payment_method_id.is_not(None))
        .group_by(Order.payment_method_id)
    ).all()
    return {row[0]: row[1] for row in rows if row[0] is not None}


@router.post("/payment-methods")
def save_payment_method(
    id: Optional[int] = Form(default=None),
    code: str = Form(...),
    name: str = Form(...),
    description: Optional[str] = Form(default=None),
    kind: str = Form(default="offline"),
    sort_order: int = Form(default=0),
    is_active: bool = Form(default=False),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    if kind not in {"offline", "online"}:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "نوع الدفع غير صالح")
    normalized_code = code.strip().lower()
    if not normalized_code:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "كود طريقة الدفع مطلوب")
    if not re.fullmatch(r"[a-z0-9_]{1,20}", normalized_code):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "الكود لازم يكون حروف إنجليزية صغيرة وأرقام و _ بس، 20 حرف على الأكثر",
        )
    stripped_name = name.strip()
    if not stripped_name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "اسم طريقة الدفع مطلوب")

    existing = db.scalar(select(PaymentMethod).where(PaymentMethod.code == normalized_code))
    if existing is not None and existing.id != id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "الكود مستخدم في طريقة دفع تانية")

    method = db.get(PaymentMethod, id) if id else PaymentMethod()
    if id and method is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "طريقة الدفع غير موجودة")
    method.code = normalized_code
    method.name = stripped_name
    method.description = (description or "").strip() or None
    method.kind = kind
    method.sort_order = sort_order
    method.is_active = is_active
    db.add(method)
    db.commit()
    return redirect("/admin/payment-methods")


@router.post("/payment-methods/{method_id}/delete")
def delete_payment_method(method_id: int, db: Session = Depends(get_db)) -> RedirectResponse:
    """إلغاء تفعيل طريقة دفع.

    soft delete تاني، لنفس سبب المناطق والطلب: `payment_method_id` هو
    `ON DELETE SET NULL`، فالحذف الحقيقي هيسيب الطلبات من غير طريقة
    دفع. مع التعطيل، الطلبات القديمة بتفضل رابطة بالطريقة ولو معطّلة،
    والعميل بياخدMethod جديدة من القائمة النشطة بس.

    **مفيش حذف حقيقي هنا** — وده مقصود: طريقة الدفع جزء من تاريخ
    الفاتورة، زي طريقة التوصيل بالظبط.
    """
    method = db.get(PaymentMethod, method_id)
    if method is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "طريقة الدفع غير موجودة")
    method.is_active = False
    db.commit()
    return redirect("/admin/payment-methods")


@router.get("/branches", response_class=HTMLResponse)
def branches_page(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    branches = db.scalars(
        select(Branch).order_by(Branch.is_default.desc(), Branch.name)
    ).all()
    return templates.TemplateResponse(
        "admin/branches.html",
        {
            "request": request,
            "branches": branches,
            "settings": get_or_create_settings(db),
        },
    )


@router.post("/branches")
def save_branch(
    id: Optional[int] = Form(default=None),
    name: str = Form(...),
    address: Optional[str] = Form(default=None),
    phone: Optional[str] = Form(default=None),
    opening_hours: Optional[str] = Form(default=None),
    is_active: bool = Form(default=True),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    cleaned_name = name.strip()
    if len(cleaned_name) < 2:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "اسم الفرع قصير أوي")

    branch = db.get(Branch, id) if id else None
    if id and branch is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "الفرع غير موجود")
    if branch is None:
        branch = Branch(is_default=False)
    branch.name = cleaned_name
    branch.address = address.strip() or None if address else None
    branch.phone = phone.strip() or None if phone else None
    branch.opening_hours = opening_hours.strip() or None if opening_hours else None
    branch.is_active = is_active
    db.add(branch)
    db.commit()
    return redirect("/admin/branches")


@router.post("/branches/{branch_id}/default")
def set_default_branch(
    branch_id: int, db: Session = Depends(get_db)
) -> RedirectResponse:
    """عين فرع افتراضي واحد بس.

    الفهرس الجزئي `ix_branches_single_default` في الداتابيز بيمنع اتنين
    مع بعض على مستوى الـdatabase، فمش محتاجين منطق تاني هنا.
    """
    branch = db.get(Branch, branch_id)
    if branch is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "الفرع غير موجود")
    if not branch.is_active:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "مينفعش تخلي فرع معطّل هو الافتراضي — شغّله الأول",
        )
    for other in db.scalars(select(Branch).where(Branch.is_default.is_(True))).all():
        other.is_default = False
        db.add(other)
    branch.is_default = True
    db.add(branch)
    db.commit()
    return redirect("/admin/branches")


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
    ordering_open_time: Optional[str] = Form(default=None),
    ordering_close_time: Optional[str] = Form(default=None),
    currency: str = Form(default="EGP"),
    facebook_url: Optional[str] = Form(default=None),
    tiktok_url: Optional[str] = Form(default=None),
    description: Optional[str] = Form(default=None),
    additional_info: Optional[str] = Form(default=None),
    primary_color: Optional[str] = Form(default=None),
    secondary_color: Optional[str] = Form(default=None),
    accent_color: Optional[str] = Form(default=None),
    logo: Optional[UploadFile] = File(default=None),
    banner: Optional[UploadFile] = File(default=None),
    app_icon: Optional[UploadFile] = File(default=None),
    pwa_name: Optional[str] = Form(default=None),
    pwa_short_name: Optional[str] = Form(default=None),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    theme = dict(THEME_DEFAULTS)
    submitted = {
        "primary": primary_color,
        "secondary": secondary_color,
        "accent": accent_color,
    }
    for key, raw_value in submitted.items():
        if raw_value is None or not str(raw_value).strip():
            continue
        normalized = normalize_hex_color(raw_value)
        if normalized is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Invalid {key} color — expected hex like #059669")
        theme[key] = normalized

    # ساعات استقبال الطلبات — اختيارية. لو واحدة بس اتبعتت من غير التانية
    # بنرفض عشان ما يحصلش طلب ميتقفلش أو مفتوحش على نصه.
    try:
        open_time = parse_ordering_time(ordering_open_time, "الفتح")
        close_time = parse_ordering_time(ordering_close_time, "الإغلاق")
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    if (open_time is None) != (close_time is None):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "لازم تحدد وقت الفتح ووقت الإغلاق مع بعض، أو تسيب الاتنين فاضيين",
        )

    # أسماء التطبيق (PWA). فاضية = نستخدم اسم المطعم زي ما كان.
    pwa_name = (pwa_name or "").strip() or None
    pwa_short_name = (pwa_short_name or "").strip() or None
    if pwa_name is not None and len(pwa_name) > 160:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "اسم التطبيق طويل أوي — الحد 160 حرف",
        )
    if pwa_short_name is not None and len(pwa_short_name) > 12:
        # 12 هو حد `short_name` في الـmanifest: المتصفح بيقصّ الاسم
        # الطويل على أي حال، فنرفض بدل ما نخزّن حاجة بتتقصّ على
        # الشاشة الرئيسية وتبقى شكلها غلط.
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "الاسم المختصر للتطبيق لازم يكون 12 حرف أو أقل",
        )

    settings = get_or_create_settings(db)
    settings.restaurant_name = restaurant_name
    settings.whatsapp_number = whatsapp_number
    settings.phone_number = phone_number
    settings.address = address
    settings.working_hours = working_hours
    settings.ordering_open_time = open_time
    settings.ordering_close_time = close_time
    settings.currency = currency
    settings.facebook_url = facebook_url or None
    settings.tiktok_url = tiktok_url or None
    settings.description = description or None
    settings.additional_info = additional_info or None
    settings.theme_colors = json.dumps(theme)
    if logo and logo.filename:
        settings.logo_url = await save_image(logo, UPLOADS_DIR / "logos")
    if banner and banner.filename:
        settings.banner_url = await save_image(banner, UPLOADS_DIR / "banners")
    if app_icon and app_icon.filename:
        settings.app_icon_url = await save_image(app_icon, UPLOADS_DIR / "icons")
    settings.pwa_name = pwa_name
    settings.pwa_short_name = pwa_short_name
    db.commit()
    return redirect("/admin/settings")
