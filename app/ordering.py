"""
قلب حسابات الطلبات — المصدر الوحيد للحقيقة في التسعير.

القاعدة: لا ثقة في أي سعر قادم من العميل. كل الأسعار تُعاد قراءتها من
Database لحظة الحساب: سعر المنتج الأساسي + تعديل سعر الخيار (Option) +
أسعار الإضافات (Add-ons) + رسوم التوصيل + خصم الكوبون.

تستخدمه 3 مسارات:
- POST /orders           (إنشاء الطلب وحفظ الأسعار)
- POST /api/orders/preview   (معاينة الحساب بدون حفظ)
- POST /api/coupons/validate (التحقق من الكوبون ضد السلة)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, List, Optional, Tuple

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models import Addon, Coupon, DeliveryOption, DeliveryZone, Order, Product, ProductOption, ProductOptionGroup
from app.utils import ensure_aware, normalize_phone

if TYPE_CHECKING:
    from app.schemas import CartItemIn

TWO_PLACES = Decimal("0.01")
ZERO = Decimal("0.00")


def quantize_money(value: Decimal) -> Decimal:
    return value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


@dataclass
class PricedLine:
    """سطر طلب محسوب بالكامل من Database."""

    product: Product
    quantity: int
    options: List[ProductOption]
    addons: List[Addon]
    unit_price: Decimal  # سعر المنتج + تعديلات أسعار الخيارات (لقطة وقت الطلب)
    addons_total: Decimal
    line_total: Decimal
    display_name: str
    item_note: Optional[str] = None


@dataclass
class PricingResult:
    lines: List[PricedLine] = field(default_factory=list)
    subtotal: Decimal = ZERO
    coupon: Optional[Coupon] = None
    coupon_code: Optional[str] = None
    discount_amount: Decimal = ZERO
    delivery_option: Optional[DeliveryOption] = None
    delivery_zone: Optional[DeliveryZone] = None
    delivery_fee: Decimal = ZERO
    total: Decimal = ZERO
    # `call_center`: الرسوم لسه متحددةش والأدمن هيحدّدها. الـfee بتبقى صفر
    # بس `delivery_fee_pending` بيبقى true عشان ماحدش يقرأ الصفر ده على
    # أنه رسوم نهائية. بيبقى false في كل الطرق التانية.
    delivery_fee_pending: bool = False


def resolve_priced_lines(db: Session, items: List["CartItemIn"]) -> List[PricedLine]:
    """يتحقق من المنتجات ومجموعات الخيارات والإضافات ويعيد كل سطر بسعره المحسوب."""
    if not items:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "الطلب يجب أن يحتوي على منتج واحد على الأقل")

    product_ids = [item.product_id for item in items]
    products = db.scalars(
        select(Product).where(
            Product.id.in_(product_ids),
            Product.is_active.is_(True),
            Product.is_available.is_(True),
        )
    ).all()
    products_by_id = {product.id: product for product in products}
    if any(pid not in products_by_id for pid in product_ids):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "بعض المنتجات غير متاحة حاليًا")

    # دمج option_id (للتوافق مع الشكل القديم) مع option_ids.
    merged_options_per_item: List[List[int]] = []
    for item in items:
        merged = list(item.option_ids)
        if item.option_id is not None:
            merged.append(item.option_id)
        merged_options_per_item.append(list(dict.fromkeys(merged)))

    option_ids = {oid for merged in merged_options_per_item for oid in merged}
    options_by_id = {}
    if option_ids:
        options = db.scalars(
            select(ProductOption)
            .where(
                ProductOption.id.in_(option_ids),
                ProductOption.is_available.is_(True),
            )
            .options(selectinload(ProductOption.group))
        ).all()
        options_by_id = {option.id: option for option in options}

    groups = db.scalars(
        select(ProductOptionGroup).where(
            ProductOptionGroup.product_id.in_(product_ids),
            ProductOptionGroup.is_available.is_(True),
        )
    ).all()
    groups_by_id = {group.id: group for group in groups}
    groups_by_product: dict[int, List[ProductOptionGroup]] = {}
    for group in groups:
        groups_by_product.setdefault(group.product_id, []).append(group)

    addon_ids = {addon_id for item in items for addon_id in item.addon_ids}
    addons_by_id = {}
    if addon_ids:
        addons = db.scalars(
            select(Addon).where(Addon.id.in_(addon_ids), Addon.is_active.is_(True))
        ).all()
        addons_by_id = {addon.id: addon for addon in addons}

    lines: List[PricedLine] = []
    for item, merged_option_ids in zip(items, merged_options_per_item):
        product = products_by_id[item.product_id]

        selected_options: List[ProductOption] = []
        for option_id in merged_option_ids:
            option = options_by_id.get(option_id)
            if option is None or option.product_id != product.id:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    f"خيار غير صالح للمنتج: {product.name}",
                )
            if option.group_id is not None:
                group = groups_by_id.get(option.group_id)
                if group is None or group.product_id != product.id:
                    raise HTTPException(
                        status.HTTP_400_BAD_REQUEST,
                        f"مجموعة الخيار غير متاحة للمنتج: {product.name}",
                    )
            selected_options.append(option)

        # التحقق من قواعد كل مجموعة خيارات: مطلوب/اختيار متعدد/حد أدنى/حد أقصى.
        selection_counts: dict[int, int] = {}
        for option in selected_options:
            key = option.group_id if option.group_id is not None else -1
            selection_counts[key] = selection_counts.get(key, 0) + 1

        for group in groups_by_product.get(product.id, []):
            count = selection_counts.get(group.id, 0)
            if not group.allow_multiple and count > 1:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    f"لا يمكن اختيار أكثر من خيار من \"{group.name}\" للمنتج: {product.name}",
                )
            if group.max_selections is not None and count > group.max_selections:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    f"الحد الأقصى للاختيارات من \"{group.name}\" هو {group.max_selections}",
                )
            min_required = group.min_selections or 0
            if group.is_required:
                min_required = max(1, min_required)
            if count < min_required:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    f"يجب اختيار {min_required} على الأقل من \"{group.name}\" للمنتج: {product.name}",
                )

        selected_addons: List[Addon] = []
        # منع تكرار نفس الإضافة داخل السطر الواحد حتى لا تُحتسب مرتين.
        for addon_id in dict.fromkeys(item.addon_ids):
            addon = addons_by_id.get(addon_id)
            if addon is None or addon.product_id != product.id:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    f"إضافة غير صالحة للمنتج: {product.name}",
                )
            selected_addons.append(addon)

        options_adjustment = sum((option.price_adjustment for option in selected_options), ZERO)
        unit_price = quantize_money(product.base_price + options_adjustment)
        addons_total = quantize_money(sum((a.price for a in selected_addons), ZERO))
        line_total = quantize_money((unit_price + addons_total) * item.quantity)

        display_name = product.name
        if selected_options:
            display_name += " (" + "، ".join(option.name for option in selected_options) + ")"

        item_note = item.item_note.strip() if item.item_note and item.item_note.strip() else None

        lines.append(
            PricedLine(
                product=product,
                quantity=item.quantity,
                options=selected_options,
                addons=selected_addons,
                unit_price=unit_price,
                addons_total=addons_total,
                line_total=line_total,
                display_name=display_name,
                item_note=item_note,
            )
        )

    return lines


def compute_subtotal(lines: List[PricedLine]) -> Decimal:
    return quantize_money(sum((line.line_total for line in lines), ZERO))


def coupon_error(
    coupon: Optional[Coupon],
    subtotal: Decimal,
    customer_phone: Optional[str] = None,
    db: Optional[Session] = None,
    now: Optional[datetime] = None,
) -> Optional[str]:
    """يعيد سبب رفض الكوبون أو None لو صالح. لا يرمي استثناءات."""
    now = ensure_aware(now or datetime.now(timezone.utc))
    if coupon is None:
        return "كود الكوبون غير صحيح"
    if not coupon.is_active:
        return "هذا الكوبون غير مفعّل"
    if now < ensure_aware(coupon.start_date):
        return "هذا الكوبون لم يبدأ بعد"
    if now > ensure_aware(coupon.end_date):
        return "هذا الكوبون انتهى"
    if coupon.usage_limit is not None and coupon.usage_count >= coupon.usage_limit:
        return "تم استنفاد عدد استخدامات هذا الكوبون"
    if subtotal < coupon.minimum_order:
        return f"الحد الأدنى للطلب لاستخدام هذا الكوبون هو {coupon.minimum_order:.2f}"
    if coupon.first_order_only and db is not None and customer_phone:
        normalized = normalize_phone(customer_phone)
        if normalized:
            db_digits = func.regexp_replace(
                func.regexp_replace(
                    func.regexp_replace(Order.customer_phone, r"\D", "", "g"),
                    r"^0", "",
                ),
                r"^20", "",
            )
            existing = db.scalar(
                select(Order.id).where(
                    db_digits == normalized,
                    Order.status != "cancelled",
                )
            )
            if existing is not None:
                return "هذا الكوبون للطلب الأول فقط — لديك طلب سابق"
    return None


def calc_coupon_discount(coupon: Coupon, subtotal: Decimal) -> Decimal:
    if coupon.discount_type == "percentage":
        discount = subtotal * coupon.discount_value / Decimal(100)
    else:
        discount = min(coupon.discount_value, subtotal)
    return quantize_money(discount)


def validate_coupon(
    db: Session,
    code: str,
    subtotal: Decimal,
    customer_phone: Optional[str] = None,
) -> Tuple[Coupon, Decimal]:
    """بحث + تحقق صارم من الكوبون، يرمي 400 برسالة واضحة لو غير صالح."""
    normalized_code = (code or "").strip()
    if not normalized_code:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "كود الكوبون مطلوب")
    coupon = db.scalar(select(Coupon).where(func.upper(Coupon.code) == normalized_code.upper()))
    reason = coupon_error(coupon, subtotal, customer_phone, db)
    if reason is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, reason)
    return coupon, calc_coupon_discount(coupon, subtotal)


def resolve_delivery_option(db: Session, delivery_option_id: Optional[int]) -> Optional[DeliveryOption]:
    if delivery_option_id is None:
        return None
    delivery_option = db.get(DeliveryOption, delivery_option_id)
    if delivery_option is None or not delivery_option.is_active:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "خيار التوصيل المحدد غير متاح")
    return delivery_option


def resolve_delivery_zone(db: Session, delivery_zone_id: Optional[int]) -> Optional[DeliveryZone]:
    if delivery_zone_id is None:
        return None
    zone = db.get(DeliveryZone, delivery_zone_id)
    if zone is None or not zone.is_active:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "منطقة التوصيل المحددة غير متاحة")
    return zone


def compute_pricing(
    db: Session,
    items: List["CartItemIn"],
    delivery_option_id: Optional[int] = None,
    delivery_zone_id: Optional[int] = None,
    coupon_code: Optional[str] = None,
    customer_phone: Optional[str] = None,
) -> PricingResult:
    """الحساب الكامل للطلب من Database: الأسطر + الخصم + الرسوم + الإجمالي."""
    lines = resolve_priced_lines(db, items)
    subtotal = compute_subtotal(lines)

    delivery_option = resolve_delivery_option(db, delivery_option_id)
    delivery_zone = resolve_delivery_zone(db, delivery_zone_id)
    if delivery_zone is not None and delivery_option is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "يجب اختيار طريقة التوصيل قبل المنطقة")
    if delivery_zone is not None and delivery_option is not None and delivery_option.delivery_type == "pickup":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "لا يمكن اختيار منطقة مع الاستلام من المطعم")
    if delivery_option is not None and delivery_option.delivery_type == "zone" and delivery_zone is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "اختر منطقة التوصيل")

    # =============================================================
    # حساب الرسوم حسب نوع التسليم
    # =============================================================
    # `pickup`: الرسوم صفر **إلزامي**، مش merely convention. لو الأدمن
    # حط رقماً في خانة الرسوم لطريقة استلام، الكود هنا بيلغيه — عشان
    # العميل اللي بيستلم من المطعم مش المفروض يدفع رسوم توصيل.
    #
    # `fixed`: `delivery_options.fee`.
    #
    # `zone`: `delivery_zones.fee` للمنطقة المختارة (المتحقق منها في
    # resolve_delivery_zone). لو اتسابت منطقة مع fixed، fee الجدول هو
    # السائد.
    #
    # `call_center`: صفر + `delivery_fee_pending=True`. ده هو الفرق
    # الجوهري عن V1: V1 كان بيرفض أي نوع مش معروف بـ400، فمفيش
    # طريقة "العميل يطلب والأدمن يحدّد" كانت موجودة أصلاً.
    delivery_fee_pending = False
    if delivery_option is None:
        delivery_fee = ZERO
    elif delivery_option.delivery_type == "pickup":
        delivery_fee = ZERO
    elif delivery_option.delivery_type == "call_center":
        # المنطقة بتتفضل مطلوبة لو موجودة، بس مش بتأثر على الرسوم.
        delivery_fee = ZERO
        delivery_fee_pending = True
    elif delivery_option.delivery_type == "zone":
        delivery_fee = quantize_money(delivery_zone.fee)  # منطقة مطلوبة ومتحقق منها فوق
    else:  # fixed
        delivery_fee = quantize_money(delivery_option.fee)

    coupon: Optional[Coupon] = None
    discount_amount = ZERO
    if coupon_code and coupon_code.strip():
        coupon, discount_amount = validate_coupon(db, coupon_code, subtotal, customer_phone)

    total = quantize_money(subtotal - discount_amount + delivery_fee)
    if total < ZERO:
        total = ZERO

    return PricingResult(
        lines=lines,
        subtotal=subtotal,
        coupon=coupon,
        coupon_code=coupon.code if coupon is not None else None,
        discount_amount=discount_amount,
        delivery_option=delivery_option,
        delivery_zone=delivery_zone,
        delivery_fee=delivery_fee,
        total=total,
        delivery_fee_pending=delivery_fee_pending,
    )


def resolve_payment_method(db: Session, payment_method_id: Optional[int]):
    """يجيب طريقة الدفع المفعّلة، أو يرفض الطلب لو غير صالحة.

    الرفض ضروري مش تحسين: العميل بيبعت `payment_method_id` من المتصفح،
    فلو قبلنا أي رقم كنا هنسجل طلب بمطريقة دفع مفعّلة الأدمن قفلها من
    قبل كده، أو برقم أصلاً مش موجود.
    """
    from app.models import PaymentMethod  # استيراد محلي: Models هيكل ثقيل

    if payment_method_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "اختر طريقة الدفع")
    method = db.get(PaymentMethod, payment_method_id)
    if method is None or not method.is_active:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "طريقة الدفع المحددة غير متاحة")
    return method


def validate_scheduled_time(pickup_time: Optional[datetime]) -> Optional[datetime]:
    """موعد الطلب (استلام أو توصيل مجدول) لازم يكون في المستقبل."""
    if pickup_time is None:
        return None
    scheduled = ensure_aware(pickup_time)
    if scheduled <= datetime.now(timezone.utc):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "موعد الطلب يجب أن يكون في المستقبل")
    return scheduled
