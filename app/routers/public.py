from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.models import (
    Category,
    Coupon,
    Customer,
    Order,
    OrderItem,
    OrderItemAddon,
    OrderItemOption,
    Product,
    ProductOptionGroup,
    RestaurantSettings,
)
from app.ordering import compute_pricing, validate_scheduled_time
from app.schemas import AddonOut, OrderCreate, OrderCreated, OrderStatus, ProductDetailOut
from app.utils import build_search_variants, build_whatsapp_message, build_whatsapp_url, normalize_phone, parse_theme_colors

router = APIRouter()
TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals["theme_colors"] = parse_theme_colors


def get_settings(db: Session) -> RestaurantSettings:
    settings = db.scalar(select(RestaurantSettings))
    if settings is None:
        settings = RestaurantSettings()
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


@router.get("/", response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    restaurant_settings = get_settings(db)
    categories = db.scalars(
        select(Category)
        .where(Category.is_active.is_(True))
        .options(selectinload(Category.products))
        .order_by(Category.sort_order, Category.name)
    ).all()
    products = db.scalars(
        select(Product)
        .where(Product.is_active.is_(True))
        .options(selectinload(Product.options), selectinload(Product.option_groups))
        .order_by(Product.sort_order, Product.name)
    ).all()
    return templates.TemplateResponse(
        "public/home.html",
        {"request": request, "settings": restaurant_settings, "categories": categories, "products": products},
    )


@router.get("/search", response_class=HTMLResponse)
def search_products(request: Request, q: str = "", db: Session = Depends(get_db)) -> HTMLResponse:
    restaurant_settings = get_settings(db)
    statement = select(Product).where(Product.is_active.is_(True))
    query = (q or "").strip()

    if query:
        terms = build_search_variants(query)
        if terms:
            conditions = [Product.name.ilike(f"%{term}%") for term in terms]
            statement = statement.where(or_(*conditions))
        else:
            statement = statement.where(Product.name.ilike(f"%{query}%"))
    else:
        statement = statement.order_by(Product.sort_order, Product.name)

    products = db.scalars(
        statement.options(selectinload(Product.options), selectinload(Product.option_groups)).order_by(
            Product.sort_order, Product.name
        )
    ).all()
    return templates.TemplateResponse(
        "public/partials/product_grid.html",
        {"request": request, "settings": restaurant_settings, "products": products},
    )


@router.get("/products/{product_slug}", response_class=HTMLResponse)
def product_detail(request: Request, product_slug: str, db: Session = Depends(get_db)) -> HTMLResponse:
    restaurant_settings = get_settings(db)
    product = db.scalar(
        select(Product)
        .where(Product.slug == product_slug, Product.is_active.is_(True), Product.is_available.is_(True))
        .options(
            selectinload(Product.category),
            selectinload(Product.addons),
            selectinload(Product.options),
            selectinload(Product.option_groups).selectinload(ProductOptionGroup.options),
        )
    )
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found")
    active_addons = sorted(
        (a for a in product.addons if a.is_active), key=lambda a: (a.sort_order, a.name)
    )
    product_data = ProductDetailOut.model_validate(product).model_dump(mode="json")
    addon_data = [AddonOut.model_validate(a).model_dump(mode="json") for a in active_addons]

    # مجموعات الخيارات المتاحة فقط، مع خياراتها المتاحة ومرتبة.
    option_groups_data = []
    for group in sorted((g for g in product.option_groups if g.is_available), key=lambda g: (g.sort_order, g.name)):
        options = sorted((o for o in group.options if o.is_available), key=lambda o: (o.sort_order, o.name))
        option_groups_data.append(
            {
                "id": group.id,
                "name": group.name,
                "is_required": group.is_required,
                "allow_multiple": group.allow_multiple,
                "min_selections": group.min_selections,
                "max_selections": group.max_selections,
                "options": [
                    {"id": o.id, "name": o.name, "price_adjustment": float(o.price_adjustment)}
                    for o in options
                ],
            }
        )

    return templates.TemplateResponse(
        "public/product_detail.html",
        {
            "request": request,
            "settings": restaurant_settings,
            "product": product_data,
            "addons": addon_data,
            "option_groups": option_groups_data,
        },
    )


@router.post("/orders", response_model=OrderCreated)
def create_order(payload: OrderCreate, db: Session = Depends(get_db)) -> OrderCreated:
    # 1) موعد الطلب المجدول (استلام/توصيل) لازم يكون في المستقبل.
    scheduled_time = validate_scheduled_time(payload.pickup_time)

    # 2) كل الحسابات تتعاد من Database — لا ثقة في أي سعر قادم من العميل.
    pricing = compute_pricing(
        db,
        payload.items,
        delivery_option_id=payload.delivery_option_id,
        delivery_zone_id=payload.delivery_zone_id,
        coupon_code=payload.coupon_code,
        customer_phone=payload.customer_phone,
    )

    # 3) العنوان مطلوب للتوصيل فقط — الاستلام من المطعم لا يحتاج عنوان.
    address = (payload.customer_address or "").strip() or None
    delivery_option = pricing.delivery_option
    if delivery_option is not None and not delivery_option.is_pickup and not address:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "العنوان مطلوب لطلبات التوصيل")

    # 4) بناء الطلب مع حفظ لقطة الأسعار والتفصيلة الكاملة وقت الإنشاء.
    order = Order(
        customer_name=payload.customer_name,
        customer_phone=payload.customer_phone,
        customer_address=address or "",
        notes=payload.notes,
        status=OrderStatus.pending.value,
        subtotal=pricing.subtotal,
        delivery_fee=pricing.delivery_fee,
        discount_amount=pricing.discount_amount,
        total=pricing.total,
        whatsapp_message="",
        delivery_option_id=delivery_option.id if delivery_option else None,
        delivery_zone_id=pricing.delivery_zone.id if pricing.delivery_zone else None,
        coupon_id=pricing.coupon.id if pricing.coupon else None,
        pickup_time=scheduled_time,
    )

    message_items: List[dict] = []
    for line in pricing.lines:
        order_item = OrderItem(
            product_id=line.product.id,
            product_name=line.display_name,
            unit_price=line.unit_price,
            quantity=line.quantity,
            line_total=line.line_total,
            item_note=line.item_note,
        )
        for addon in line.addons:
            order_item.addons.append(
                OrderItemAddon(addon_id=addon.id, name=addon.name, price=addon.price)
            )
        for option in line.options:
            order_item.options.append(
                OrderItemOption(
                    option_id=option.id,
                    group_name=option.group.name if option.group is not None else "",
                    name=option.name,
                    price_adjustment=option.price_adjustment,
                )
            )
        order.items.append(order_item)

        message_items.append(
            {
                "name": line.product.name,
                "quantity": line.quantity,
                "line_total": line.line_total,
                "options": [(o.name, o.price_adjustment) for o in line.options],
                "addons": [(a.name, a.price) for a in line.addons],
                "note": line.item_note,
            }
        )

    # 5) تجميع الطلبات تحت عميل واحد حسب الرقم الموحد.
    normalized_phone = normalize_phone(payload.customer_phone)
    customer = db.scalar(select(Customer).where(Customer.phone == normalized_phone)) if normalized_phone else None
    now = order.created_at or datetime.now(timezone.utc)
    if customer is None and normalized_phone:
        customer = Customer(phone=normalized_phone, name=payload.customer_name.strip(), first_order_at=now, last_order_at=now)
        db.add(customer)
        try:
            db.flush()
        except IntegrityError:
            db.expire_all()
            customer = db.scalar(select(Customer).where(Customer.phone == normalized_phone))
            if customer is None:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "العميل غير موجود")
    elif customer is not None:
        customer.name = payload.customer_name.strip() or customer.name
        customer.last_order_at = now
    order.customer_id = customer.id if customer else None

    # 6) منع race condition: التحقق من كوبون first-order-only مع قفل العهدة.
    if pricing.coupon is not None and pricing.coupon.first_order_only:
        # Lock the Customer row to prevent concurrent first-order coupon usage
        db.scalar(select(Customer).where(Customer.id == customer.id).with_for_update())
        # Lock the Coupon row to prevent concurrent first-order-only coupon usage
        db.scalar(select(Coupon).where(Coupon.id == pricing.coupon.id).with_for_update())
        from sqlalchemy import func as sa_func
        db_digits = sa_func.regexp_replace(
            sa_func.regexp_replace(
                sa_func.regexp_replace(Order.customer_phone, r"\D", "", "g"),
                r"^0", "",
            ),
            r"^20", "",
        )
        existing_order = db.scalar(
            select(Order.id).where(
                db_digits == normalized_phone,
                Order.status != "cancelled",
            )
        )
        if existing_order is not None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "هذا الكوبون للطلب الأول فقط — لديك طلب سابق")

    # 7) خصم استخدام الكوبون بعد نجاح الحساب.
    if pricing.coupon is not None:
        pricing.coupon.usage_count += 1
        db.add(pricing.coupon)

    # 8) حفظ الطلب أولًا لمعرفة رقمه، ثم بناء رسالة WhatsApp برقم الطلب.
    db.add(order)
    db.flush()

    restaurant_settings = get_settings(db)
    message = build_whatsapp_message(
        restaurant_settings,
        order.id,
        payload.customer_name,
        payload.customer_phone,
        address,
        message_items,
        subtotal=pricing.subtotal,
        discount_amount=pricing.discount_amount,
        delivery_fee=pricing.delivery_fee,
        delivery_name=(f"{delivery_option.name} - {pricing.delivery_zone.name}" if delivery_option and pricing.delivery_zone else delivery_option.name if delivery_option else None),
        is_pickup=bool(delivery_option and delivery_option.is_pickup),
        total=pricing.total,
        notes=payload.notes,
        pickup_time=scheduled_time,
        coupon_code=pricing.coupon_code,
    )
    order.whatsapp_message = message

    db.commit()
    db.refresh(order)
    return OrderCreated(
        order_id=order.id,
        status=order.status,
        subtotal=order.subtotal,
        discount_amount=order.discount_amount,
        delivery_fee=order.delivery_fee,
        total=order.total,
        currency=restaurant_settings.currency,
        whatsapp_url=build_whatsapp_url(restaurant_settings.whatsapp_number, message),
    )
