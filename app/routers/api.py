"""
JSON API العام — واجهات جديدة للـFrontend بدون كسر الـRoutes الحالية.

كل الأسعار هنا من Database فقط. الـPreview والكوبون بيستخدموا نفس قلب
الحسابات في app/ordering.py المستخدم في POST /orders، فالنتيجة واحدة دائمًا.
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.models import Category, Coupon, DeliveryOption, Product, ProductOptionGroup
from app.ordering import (
    ZERO,
    calc_coupon_discount,
    compute_pricing,
    compute_subtotal,
    coupon_error,
    resolve_priced_lines,
)
from app.routers.public import get_settings
from app.schemas import (
    AddonOut,
    CategoryOut,
    CouponValidateIn,
    CouponValidateOut,
    DeliveryOptionOut,
    OrderPreviewIn,
    OrderPreviewItemOut,
    OrderPreviewOut,
    ProductDetailOut,
    ProductOptionOut,
    ProductOut,
    OptionGroupOut,
)
from app.utils import build_search_variants

router = APIRouter(prefix="/api")


@router.get("/categories", response_model=List[CategoryOut])
def list_categories(db: Session = Depends(get_db)) -> List[Category]:
    return list(
        db.scalars(
            select(Category)
            .where(Category.is_active.is_(True))
            .order_by(Category.sort_order, Category.name)
        ).all()
    )


@router.get("/categories/{category_slug}/products", response_model=List[ProductOut])
def category_products(category_slug: str, db: Session = Depends(get_db)) -> List[Product]:
    category = db.scalar(
        select(Category).where(Category.slug == category_slug, Category.is_active.is_(True))
    )
    if category is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Category not found")
    return list(
        db.scalars(
            select(Product)
            .where(Product.category_id == category.id, Product.is_active.is_(True))
            .options(selectinload(Product.category))
            .order_by(Product.sort_order, Product.name)
        ).all()
    )


@router.get("/products", response_model=List[ProductOut])
def list_products(
    db: Session = Depends(get_db),
    category: Optional[str] = None,
    q: Optional[str] = None,
) -> List[Product]:
    statement = select(Product).where(Product.is_active.is_(True))

    if category:
        category_obj = db.scalar(
            select(Category).where(Category.slug == category, Category.is_active.is_(True))
        )
        if category_obj is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Category not found")
        statement = statement.where(Product.category_id == category_obj.id)

    query = (q or "").strip()
    if query:
        terms = build_search_variants(query)
        if terms:
            statement = statement.where(or_(*[Product.name.ilike(f"%{term}%") for term in terms]))
        else:
            statement = statement.where(Product.name.ilike(f"%{query}%"))

    return list(
        db.scalars(
            statement.options(selectinload(Product.category)).order_by(Product.sort_order, Product.name)
        ).all()
    )


@router.get("/products/{product_slug}", response_model=ProductDetailOut)
def product_detail_api(product_slug: str, db: Session = Depends(get_db)) -> ProductDetailOut:
    product = db.scalar(
        select(Product)
        .where(Product.slug == product_slug, Product.is_active.is_(True))
        .options(
            selectinload(Product.category),
            selectinload(Product.options),
            selectinload(Product.addons),
            selectinload(Product.option_groups).selectinload(ProductOptionGroup.options),
        )
    )
    if product is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found")

    detail = ProductDetailOut.model_validate(product)
    detail.options = [
        ProductOptionOut.model_validate(o)
        for o in sorted((o for o in product.options if o.is_available), key=lambda o: (o.sort_order, o.name))
    ]
    detail.addons = [
        AddonOut.model_validate(a)
        for a in sorted((a for a in product.addons if a.is_active), key=lambda a: (a.sort_order, a.name))
    ]
    groups_out = []
    for group in sorted(
        (g for g in product.option_groups if g.is_available), key=lambda g: (g.sort_order, g.name)
    ):
        group_out = OptionGroupOut.model_validate(group)
        group_out.options = [
            ProductOptionOut.model_validate(o)
            for o in sorted((o for o in group.options if o.is_available), key=lambda o: (o.sort_order, o.name))
        ]
        groups_out.append(group_out)
    detail.option_groups = groups_out
    return detail


@router.get("/delivery-options", response_model=List[DeliveryOptionOut])
def list_delivery_options(db: Session = Depends(get_db)) -> List[DeliveryOption]:
    return list(
        db.scalars(
            select(DeliveryOption)
            .where(DeliveryOption.is_active.is_(True))
            .order_by(DeliveryOption.sort_order, DeliveryOption.name)
        ).all()
    )


@router.post("/coupons/validate", response_model=CouponValidateOut)
def validate_coupon_endpoint(payload: CouponValidateIn, db: Session = Depends(get_db)) -> CouponValidateOut:
    # الإجمالي الفرعي بيتحسب هنا في الـBackend من Database — مش من العميل.
    lines = resolve_priced_lines(db, payload.items)
    subtotal = compute_subtotal(lines)

    coupon = db.scalar(select(Coupon).where(func.upper(Coupon.code) == payload.code.strip().upper()))
    reason = coupon_error(coupon, subtotal)
    if reason is not None:
        return CouponValidateOut(
            valid=False,
            code=payload.code.strip(),
            subtotal=subtotal,
            discount_amount=ZERO,
            message=reason,
        )
    return CouponValidateOut(
        valid=True,
        code=coupon.code,
        discount_type=coupon.discount_type,
        discount_value=coupon.discount_value,
        discount_amount=calc_coupon_discount(coupon, subtotal),
        subtotal=subtotal,
        message="الكوبون صالح",
    )


@router.post("/orders/preview", response_model=OrderPreviewOut)
def preview_order(payload: OrderPreviewIn, db: Session = Depends(get_db)) -> OrderPreviewOut:
    """حساب كامل للطلب بدون أي حفظ في Database — للعرض قبل تأكيد الطلب."""
    settings = get_settings(db)

    coupon_valid: Optional[bool] = None
    coupon_message: Optional[str] = None
    has_coupon = bool(payload.coupon_code and payload.coupon_code.strip())

    pricing = compute_pricing(
        db,
        payload.items,
        delivery_option_id=payload.delivery_option_id,
        coupon_code=payload.coupon_code if has_coupon else None,
    )
    if has_coupon:
        coupon_valid = True
        coupon_message = "الكوبون صالح"

    items_out = [
        OrderPreviewItemOut(
            product_id=line.product.id,
            name=line.display_name,
            unit_price=line.unit_price,
            quantity=line.quantity,
            addons_total=line.addons_total,
            line_total=line.line_total,
            options=[o.name for o in line.options],
            item_note=line.item_note,
        )
        for line in pricing.lines
    ]

    return OrderPreviewOut(
        items=items_out,
        subtotal=pricing.subtotal,
        discount_amount=pricing.discount_amount,
        delivery_fee=pricing.delivery_fee,
        total=pricing.total,
        currency=settings.currency,
        coupon_valid=coupon_valid,
        coupon_message=coupon_message,
    )
