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
from app.models import (
    Category,
    Coupon,
    DeliveryOption,
    DeliveryZone,
    PaymentMethod,
    Product,
    ProductOptionGroup,
)
from app.order_tracking import build_tracked_order, require_order_by_token
from app.ordering import (
    ZERO,
    calc_coupon_discount,
    compute_pricing,
    compute_subtotal,
    coupon_error,
    quantize_money,
    resolve_priced_lines,
)
from app.routers.public import get_settings
from app.schemas import (
    AddonOut,
    CartItemIn,
    CategoryOut,
    CouponValidateIn,
    CouponValidateOut,
    DeliveryOptionOut,
    DeliveryZoneOut,
    OrderPreviewIn,
    OrderPreviewItemOut,
    OrderPreviewOut,
    PaymentMethodOut,
    ProductDetailOut,
    ProductOptionOut,
    ProductOut,
    OptionGroupOut,
    ReorderIn,
    ReorderLine,
    ReorderOut,
    ReorderSkipped,
    TrackedOrderOut,
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


@router.get("/products/by-id/{product_id}", response_model=ProductDetailOut)
def product_by_id_api(product_id: int, db: Session = Depends(get_db)) -> ProductDetailOut:
    """منتج واحد بالـid، بنفس شكل `/products/{slug}`.

   ده endpoint موجود مش للعرض — وجوده عشان إعادة الطلب في `cart.js`:
    السيرفر بيرجّع أسطر إعادة الطلب بالـid بس (هو اللي بيقدر يتحقق
    منها)، والجافاسكريبت محتاج اسم المنتج وسعره عشان يعرض السطر في
    السلة.

    **التحقق/security زي `/products/{slug}` بالظبط**: مفعّل ومتاح فقط،
    والخيارات والإضافات المعروضة متاحة ونشطة بس. راجع `product_detail_api`
    فوق للسبب — الشكل واحد والفلتر واحد.
    """
    product = db.scalar(
        select(Product)
        .where(Product.id == product_id, Product.is_active.is_(True))
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
        for o in sorted(
            (o for o in product.options if o.is_available),
            key=lambda o: (o.sort_order, o.name),
        )
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
            for o in sorted(
                (o for o in group.options if o.is_available), key=lambda o: (o.sort_order, o.name)
            )
        ]
        groups_out.append(group_out)
    detail.option_groups = groups_out
    return detail


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


@router.get("/delivery-zones", response_model=List[DeliveryZoneOut])
def list_delivery_zones(db: Session = Depends(get_db)) -> List[DeliveryZone]:
    return list(
        db.scalars(
            select(DeliveryZone)
            .where(DeliveryZone.is_active.is_(True))
            .order_by(DeliveryZone.sort_order, DeliveryZone.name)
        ).all()
    )


@router.post("/coupons/validate", response_model=CouponValidateOut)
def validate_coupon_endpoint(payload: CouponValidateIn, db: Session = Depends(get_db)) -> CouponValidateOut:
    # الإجمالي الفرعي بيتحسب هنا في الـBackend من Database — مش من العميل.
    lines = resolve_priced_lines(db, payload.items)
    subtotal = compute_subtotal(lines)

    coupon = db.scalar(select(Coupon).where(func.upper(Coupon.code) == payload.code.strip().upper()))
    reason = coupon_error(coupon, subtotal, customer_phone=payload.customer_phone, db=db)
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
        delivery_zone_id=payload.delivery_zone_id,
        coupon_code=payload.coupon_code if has_coupon else None,
        customer_phone=payload.customer_phone,
    )
    if has_coupon:
        if pricing.coupon is not None:
            coupon_valid = True
            coupon_message = "الكوبون صالح"
        else:
            coupon_valid = False
            coupon_message = "الكوبون غير صالح"

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
        # `call_center`: الواجهة تعرض "الرسوم هتحدد" بدل رقم صفر مضلّل.
        delivery_fee_pending=pricing.delivery_fee_pending,
    )


@router.get("/orders/track", response_model=TrackedOrderOut)
def track_order(token: str, db: Session = Depends(get_db)) -> TrackedOrderOut:
    """تتبّع طلب واحد بالـtoken.

    404 واحد للتوكن الغلط والطلب غير الموجود: message "الطلب مش موجود"
    بتقول لمخمن الـtoken إن في طلبات أصلاً. التحقق نفسه على هاش
    256-بت، فمفيش فايدة لمحاولات تخمين.
    """
    order = require_order_by_token(db, token)
    return build_tracked_order(order, get_settings(db).currency)


@router.post("/orders/reorder", response_model=ReorderOut)
def reorder_order(payload: ReorderIn, db: Session = Depends(get_db)) -> ReorderOut:
    """إعادة طلب قديم للسلة — بتتحقق مع الحالة الحالية.

    **مفيش إنشاء طلب هنا.** الرد فيه سطور للسلة، والعميل بيراجع قبل ما
    يأكد. إعادة الطلب فيها قرار (هل لسه عايز نفس الحاجة؟)، والتكرار
    الآلي بيكون في طلبات غلط.

    **السطور بتتبني من الطلب نفسه، مش من العميل.** `require_order_by_token`
    بيرجّع الطلب بعد التحقق بالهاش، وبنقرا `order_items` بتاعه هو —
    فالـtoken مش إلا تصريح على طلب واحد بعينه. لو سِبنا السطور تجي من
    العميل، الـtoken كان هيبقى تصريح بقراءة أي طلب، وده بيوسّع حدود
    الإذن زيادة عن إعادة ترتيب السلة.

    كل سطر بيتحقّق مع السعر والحالة الحاليين في `resolve_priced_lines`:
    منتج اتشال أو خيار بقى unavailable أو سعر اتغير → السطر بيرجع في
    `skipped` مع سبب، والعميل بيشوف السبب بدل ما الطلب يفشل كله أو
    (أسوأ) يسجّل منتج مش متاح.

    كما كل نداءات `POST /orders`: بلا rate limiting في المرحلة دي. ده
    متعمد ومذكور في التقرير النهائي كـfollow-up. السيرفر بيحقّق التتبع
    بالهاش، وPOST ده بيعيد تسعير شغل شغلي بسيط — مش عين على قاعدة
    بيانات العميل.
    """
    order = require_order_by_token(db, payload.token)
    settings = get_settings(db)

    order_items = sorted(order.items, key=lambda i: i.id)
    wanted = payload.line_indexes
    if wanted:
        by_position = {index: item for index, item in enumerate(order_items)}
        sources = [by_position.get(index) for index in wanted]
    else:
        sources = list(order_items)

    # بنSkip السطور اللي مathingش بدل ما نرمي 400 على الطلب كله: العميل
    # ممكن عايز 3 من 5 أصناف، ويرفض الطلب كله عشان واحد اتشال
    # أسوأ. `resolve_priced_lines` بيرمي على أول مشكلة، فنجيبه سطر
    # سطر عشان نعرف مين بالظبط هو اللي مش صالح.
    lines: List[ReorderLine] = []
    skipped: List[ReorderSkipped] = []
    subtotal = ZERO

    for source in sources:
        if source is None:
            skipped.append(
                ReorderSkipped(
                    product_id=-1,
                    reason="السطر ده مش موجود في الطلب anymore",
                )
            )
            continue
        if source.product_id is None:
            # المنتج اتشال من الكتالوج completely بعد ما اتطلب.
            skipped.append(
                ReorderSkipped(
                    product_id=-1,
                    reason=f'"{source.product_name}" بقى غير متاح',
                )
            )
            continue
        candidate = CartItemIn(
            product_id=source.product_id,
            quantity=source.quantity,
            # `option_id`/`addon_id` بتبقى `NULL` لو العنصر اتشال بعد
            # الطلب. السطر بيتخطّى مع سبب من `resolve_priced_lines`
            # في الحالة دي، وده صح: ما نقدرش نرجّعه من غير ما نعرف
            # سعره الحالي.
            option_ids=[o.option_id for o in source.options if o.option_id is not None],
            addon_ids=[a.addon_id for a in source.addons if a.addon_id is not None],
        )
        try:
            priced = resolve_priced_lines(db, [candidate])
        except HTTPException as exc:
            skipped.append(
                ReorderSkipped(
                    product_id=source.product_id,
                    reason=str(exc.detail),
                )
            )
            continue
        line = priced[0]
        lines.append(
            ReorderLine(
                product_id=line.product.id,
                quantity=line.quantity,
                option_ids=[o.id for o in line.options],
                addon_ids=[a.id for a in line.addons],
            )
        )
        subtotal += line.line_total

    if not lines:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "مفيش أصناف من الطلب القديم متاحة دلوقتي. "
            + (
                "الأسباب: " + "، ".join(s.reason for s in skipped[:3])
                if skipped
                else ""
            ),
        )

    return ReorderOut(
        lines=lines,
        skipped=skipped,
        subtotal=quantize_money(subtotal),
        currency=settings.currency,
    )


@router.get("/payment-methods", response_model=List[PaymentMethodOut])
def list_payment_methods(db: Session = Depends(get_db)) -> List[PaymentMethodOut]:
    """طرق الدفع المفعلة — اللي يقدر يختارها العميل في الـcheckout.

    المفعّلة بس: لو الأدمن قفل طريقة، هيختفي فوراً من اختيار العميل
    وتقع في الخلفية. الرجوع لـID بيتحقق في `POST /orders` — لو حد بعت
    method معطّلة يدوي، `resolve_payment_method` بيرفض بـ400.

    الترتيب `sort_order` الأول عشان الأدمن يتحكم في ترتيب العرض، وبعدها
    الاسم. مفيش `is_active` في الرد: العميل بيشوف المفعّل بس.
    """
    methods = db.scalars(
        select(PaymentMethod)
        .where(PaymentMethod.is_active.is_(True))
        .order_by(PaymentMethod.sort_order, PaymentMethod.name)
    ).all()
    return [PaymentMethodOut.model_validate(m) for m in methods]
