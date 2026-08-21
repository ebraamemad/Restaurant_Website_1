from __future__ import annotations

import re
import unicodedata
from decimal import Decimal
from typing import List, Tuple

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.models import Addon, Category, Order, OrderItem, OrderItemAddon, Product, StoreSettings
from app.schemas import AddonOut, OrderCreate, OrderCreated, ProductDetailOut
from app.utils import build_whatsapp_message, build_whatsapp_url

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def get_settings(db: Session) -> StoreSettings:
    settings = db.scalar(select(StoreSettings))
    if settings is None:
        settings = StoreSettings()
        db.add(settings)
        db.commit()
        db.refresh(settings)
    return settings


def normalize_search_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    normalized = normalized.lower().strip()
    normalized = re.sub(r"[^a-z0-9\u0600-\u06ff]+", " ", normalized)
    return " ".join(normalized.split())


def build_search_variants(value: str) -> List[str]:
    raw = (value or "").strip()
    if not raw:
        return []

    variants = {raw, normalize_search_text(raw)}
    cleaned = normalize_search_text(raw)
    if cleaned:
        variants.add(cleaned.replace(" ", ""))
        variants.add(cleaned.replace("ة", "ه"))
        variants.add(cleaned.replace("ي", "ى"))
        variants.add(cleaned.replace("ى", "ي"))

    return [variant for variant in variants if variant]


@router.get("/", response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    store_settings = get_settings(db)
    categories = db.scalars(
        select(Category)
        .where(Category.is_active.is_(True))
        .options(selectinload(Category.products))
        .order_by(Category.sort_order, Category.name)
    ).all()
    products = db.scalars(
        select(Product)
        .where(Product.is_active.is_(True))
        .order_by(Product.sort_order, Product.name)
    ).all()
    return templates.TemplateResponse(
        "public/home.html",
        {"request": request, "settings": store_settings, "categories": categories, "products": products},
    )


@router.get("/search", response_class=HTMLResponse)
def search_products(request: Request, q: str = "", db: Session = Depends(get_db)) -> HTMLResponse:
    store_settings = get_settings(db)
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

    products = db.scalars(statement.order_by(Product.sort_order, Product.name)).all()
    return templates.TemplateResponse(
        "public/partials/product_grid.html",
        {"request": request, "settings": store_settings, "products": products},
    )


@router.get("/products/{product_slug}", response_class=HTMLResponse)
def product_detail(request: Request, product_slug: str, db: Session = Depends(get_db)) -> HTMLResponse:
    store_settings = get_settings(db)
    product = db.scalar(
        select(Product)
        .where(Product.slug == product_slug, Product.is_active.is_(True), Product.is_available.is_(True))
        .options(selectinload(Product.category), selectinload(Product.addons))
    )
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found")
    active_addons = [a for a in product.addons if a.is_active]
    product_data = ProductDetailOut.model_validate(product).model_dump(mode="json")
    addon_data = [AddonOut.model_validate(a).model_dump(mode="json") for a in active_addons]
    return templates.TemplateResponse(
        "public/product_detail.html",
        {"request": request, "settings": store_settings, "product": product_data, "addons": addon_data},
    )


@router.post("/orders", response_model=OrderCreated)
def create_order(payload: OrderCreate, db: Session = Depends(get_db)) -> OrderCreated:
    product_ids = [item.product_id for item in payload.items]
    products = db.scalars(
        select(Product)
        .where(
            Product.id.in_(product_ids),
            Product.is_active.is_(True),
            Product.is_available.is_(True),
        )
        .options(selectinload(Product.addons))
    ).all()
    products_by_id = {product.id: product for product in products}
    if len(products_by_id) != len(product_ids):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Some products are unavailable")

    # Collect all addon IDs across all cart items and fetch them
    all_addon_ids: set[int] = set()
    for item in payload.items:
        all_addon_ids.update(item.addon_ids)

    addons_by_id = {}
    if all_addon_ids:
        addons = db.scalars(
            select(Addon).where(
                Addon.id.in_(all_addon_ids),
                Addon.is_active.is_(True),
            )
        ).all()
        addons_by_id = {addon.id: addon for addon in addons}
        if len(addons_by_id) != len(all_addon_ids):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Some addons are unavailable")

    total = Decimal("0.00")
    order = Order(
        customer_name=payload.customer_name,
        customer_phone=payload.customer_phone,
        customer_address=payload.customer_address,
        notes=payload.notes,
        total=Decimal("0.00"),
        whatsapp_message="",
    )

    message_items: List[Tuple[str, int, Decimal, Decimal]] = []

    for item in payload.items:
        product = products_by_id[item.product_id]
        selected_addons = [addons_by_id[aid] for aid in item.addon_ids if aid in addons_by_id]
        addons_total = sum((a.price for a in selected_addons), Decimal("0.00"))
        unit_price_with_addons = product.price + addons_total
        line_total = unit_price_with_addons * item.quantity
        total += line_total

        order_item = OrderItem(
            product_id=product.id,
            product_name=product.name,
            unit_price=product.price,
            quantity=item.quantity,
            line_total=line_total,
        )
        for addon in selected_addons:
            order_item.addons.append(
                OrderItemAddon(name=addon.name, price=addon.price)
            )
        order.items.append(order_item)

        addon_suffix = ""
        if selected_addons:
            addon_suffix = " + " + ", ".join(a.name for a in selected_addons)
        message_items.append((product.name + addon_suffix, item.quantity, product.price, line_total))

    order.total = total

    store_settings = get_settings(db)
    message = build_whatsapp_message(
        store_settings,
        payload.customer_name,
        payload.customer_phone,
        payload.customer_address,
        message_items,
        total,
        payload.notes,
    )
    order.whatsapp_message = message

    db.add(order)
    db.commit()
    db.refresh(order)
    return OrderCreated(
        order_id=order.id,
        total=order.total,
        whatsapp_url=build_whatsapp_url(store_settings.whatsapp_number, message),
    )
