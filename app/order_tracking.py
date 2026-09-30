"""تتبّع طلب العميل وسجل طلباته — Phase 2.

## ليه token مش رقم طلب ولا رقم هاتف

V2 مفيهاش حسابات عملاء، فالتتبّع لازم يكون بالـtoken:

- **رقم الطلب تسلسلي**: حد يخمّن `/track?id=2478` ويشوف طلب حد تاني.
  الأرقام عندنا صغيرة (12 طلب في داتابيز التطوير)، فالتخمين بياخد ثواني.
- **رقم الهاتف**: الرقم بيظهر في رسالة واتساب، وفي نتائج البحث، وفي
  أخطاء الويب. وبما إن التحقق لازم يكون behind token، ترك الرقم
  مفتوح من غير token بيفسد فايدة الـtoken كلها.

الـtoken 32 بايت عشوائي من `secrets`، والداتابيز شايفة SHA-256 بتاعه بس.
يعني dump للداتابيز = صفر روابط تتبّع شغّالة.

## ليه `hmac.compare_digest`

الـlookup عادي `==` على hex ثابت الطول (64 حرف)، فمفيش timing attack
عملي على مستوى الـSQL. الفحص التاني موجود عشان نتأكد إن الهاش اللي رجع
من الـDB هو اللي طلبناه بالظبط، قبل ما نرجع للعميل بيانات الطلب.
"""

from __future__ import annotations

import hmac
from typing import List, Optional

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import Order, OrderItem
from app.schemas import (
    OrderItemOut,
    OrderStatus,
    OrderStatusEventOut,
    OrderStatusStepOut,
    TrackedOrderOut,
    coerce_order_status,
)
from app.utils import hash_tracking_token


# ============================================================
# تسميات الحالات — عربي، للعميل
# ============================================================
# التسمية منفصلة عن `OrderStatus` عن قصد: `out_for_delivery` اسم تقني
# مش معروض. تغيير التسمية ما بيأثرش على الداتابيز ولا على أي استعلام.
STATUS_LABELS: dict[OrderStatus, str] = {
    OrderStatus.pending: "تم استلام الطلب",
    OrderStatus.confirmed: "تم تأكيد الطلب",
    OrderStatus.preparing: "جاري التحضير",
    OrderStatus.ready: "الطلب جاهز",
    OrderStatus.out_for_delivery: "في الطريق إليك",
    OrderStatus.completed: "تم التسليم",
    OrderStatus.delivered: "تم التسليم",
    OrderStatus.cancelled: "ملغي",
}

# المسار اللي الواجهة ترسمه كشريط تقدم. `confirmed` مش فيه عن قصد:
# من ناحية العميل "تم التأكيد" و"جاري التحضير" نفس اللحظة عملياً،
# والتأكيد بيحصل في ثواني. لو ضفناه كخطوة مستقلة، الشريط هيبان عليه
# لمعة بتعدّي قبل ما العميل يقراها. الحالة بتفضل حالة حقيقية
# (الأدمن يقدر يحدّدها) وتسميتها بتفضل في `STATUS_LABELS` لأي حاجة
# تحتاج تعرضها.
TIMELINE: tuple[OrderStatus, ...] = (
    OrderStatus.pending,
    OrderStatus.preparing,
    OrderStatus.ready,
    OrderStatus.out_for_delivery,
    OrderStatus.completed,
)


def status_label(value: str) -> str:
    """تسمية آمنة لأي نص مخزّن — حالة غريبة ماتفرقش."""
    try:
        return STATUS_LABELS[coerce_order_status(value)]
    except ValueError:
        return "غير معروف"


# ============================================================
# البحث بالـtoken
# ============================================================


def find_order_by_token(db: Session, token: str) -> Optional[Order]:
    """يلاقي الطلب بـtoken، أو None لو التوكن مش صح.

    مفيش رمي استثناء هنا: نفس الدالة بتخدم صفحة HTML و JSON API، وكل
    واحد فيهم بيحوّل None لـ404 بطريقته.
    """
    if not token or not token.strip():
        return None
    digest = hash_tracking_token(token.strip())
    order = db.scalar(
        select(Order)
        .where(Order.order_tracking_hash == digest)
        .options(
            selectinload(Order.items).selectinload(OrderItem.options),
            selectinload(Order.items).selectinload(OrderItem.addons),
        )
    )
    if order is None:
        return None
    # فحص تاني: نتأكد إن الهاش اللي رجع من الـDB هو اللي احسبناه بالظبط
    # قبل ما نرجع أي بيانات للعميل. `hmac.compare_digest` مش لأن الـSQL
    # قابل للـtiming (أصلاً مش ده المقصود) — ده assertion إن السطر اللي
    # رجع مطابق لللي اتحسب.
    stored = order.order_tracking_hash
    if stored is None or not hmac.compare_digest(str(stored), digest):
        return None
    return order


def require_order_by_token(db: Session, token: str) -> Order:
    order = find_order_by_token(db, token)
    if order is None:
        # رسالة واحدة للتوكن الفارغ، والتوكن الغلط، والطلب اللي مش
        # موجود. مفيش أي لازمة للفرق: "الطلب مش موجود" بتقول
        # الحد نفسه إن في طلبات أصلاً.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "الطلب مش موجود أو الرابط غير صحيح")
    return order


# ============================================================
# بناء الرد
# ============================================================


def _step_reached(current: OrderStatus, step: OrderStatus) -> bool:
    if current == OrderStatus.cancelled:
        return False
    if current == OrderStatus.delivered:
        # legacy V1: نفس المرحلة النهائية عشان الشريط يوصل للآخر
        current = OrderStatus.completed
    try:
        return TIMELINE.index(step) <= TIMELINE.index(current)
    except ValueError:
        return False


def _reached_steps(
    order: Order, first_seen: Optional[dict] = None
) -> List[OrderStatusStepOut]:
    """خطوات الـtimeline اللي الطلب وصلها.

    `current` بتحدد آخر خطوة، واللي قبلها كلهم `reached=True`. ده
    بيتعامل مع التخطي صح: لو طلب راح من `pending` لـ`preparing` (العميل
    ما استناش التأكيد)، الخطوة اللي في النص بتظهر كأنها وصلت. لو
    عملنا `reached` بالظبط للخطوات السابقة بس، الشريط كان هيبان عليه
    خطوة مش مقطوعة.

    **`first_seen` هو أول وقت وصلنا فيه لكل حالة** (Phase 3). من غيره
    كان `at` فاضي لكل الخطوات غير الأولى، وصفحة التتبّع بتقول "من غير
    وقت" steps حتى لو الطلب مت follow-up من ساعة.
    """
    current = coerce_order_status(order.status)
    first_seen = first_seen or {}
    return [
        OrderStatusStepOut(
            status=step.value,
            label=STATUS_LABELS[step],
            reached=_step_reached(current, step),
            # أول مرة وصلنا الحالة دي. لو مفيش سطر (طلب قديم مالوش
            # سجل) نرجع لأول خطوة بس زي V1.
            at=first_seen.get(step.value)
            or (order.created_at if step is OrderStatus.pending else None),
        )
        for step in TIMELINE
    ]


def _items_out(order: Order) -> List[OrderItemOut]:
    """أسطر الطلب من اللقطات المخزّنة وقت الطلب، مش من المنتجات الحالية.

    لو رجعنا لـ`Product` هنا، أي تعديل سعر أو اسم بعد الطلب هيتغيّر في
    سجل العميل. الـsnapshot في `order_items` موجود أصلاً — ده اللي
    بنعرضه.
    """
    return [
        OrderItemOut(
            product_id=item.product_id,
            name=item.product_name,
            quantity=item.quantity,
            unit_price=item.unit_price,
            line_total=item.line_total,
            options=[o.name for o in item.options],
            addons=[a.name for a in item.addons],
            item_note=item.item_note,
        )
        for item in order.items
    ]


def build_tracked_order(
    order: Order,
    currency: str,
    *,
    db: Optional[Session] = None,
) -> TrackedOrderOut:
    """يبني رد صفحة التتبّع من الـOrder.

    **مفيش هاتف ولا عنوان ولا بريد** في الرد، عن قصد: لو leakedوا من
    أي endpoint تاني، رمز التتبّع بيبقى بلا قيمة. العميل عارف طلبه من
    التوكن اللي معاه.

    **`db` اختياري** عشان `build_tracked_order` يفضل نداءً نضيفاً
    يستدعيه أي كود (اختبارات، سكربت) من غير session. من غير `db` بنرد
    بـ`steps` زي V1 و`events` فاضي — والواجهة بتتحمّل ده.
    """
    current = coerce_order_status(order.status)
    customer_note = (order.notes or "").strip() or None

    # سجل الحالة: قراءة واحدة بتدّي كل حاجة محتاجينها.
    #
    # **ليه lazy:** `order.status_history` relationship بيحتاج session
    # شغّالة. من غير `db` مش هنجيبه أصلاً.
    first_seen: dict = {}
    events: List[OrderStatusEventOut] = []
    if db is not None:
        from app import push as push_service

        first_seen = push_service.first_status_at(db, order.id)
        events = [
            OrderStatusEventOut(
                status=event.status,
                status_label=status_label(event.status),
                at=event.changed_at,
                by=event.changed_by_username,
                synthetic=bool(event.synthetic),
            )
            # الأحدث فوق: العميل بيفتح الصفحة عايز آخر حاجة حصلت.
            for event in push_service.status_history(db, order.id)
        ]

    cancel_reason: Optional[str] = None
    if current == OrderStatus.cancelled:
        # مافيش عمود "سبب الإلغاء" في V1. لو الأدمن كتب ملاحظة وقت
        # الإلغاء، هي تظهر كسبب. غير كده رسالة عامة.
        cancel_reason = customer_note or "تم إلغاء الطلب"

    return TrackedOrderOut(
        order_id=order.id,
        status=current.value,
        status_label=STATUS_LABELS[current],
        created_at=order.created_at,
        delivery_fee_pending=bool(order.delivery_fee_pending),
        delivery_fee=order.delivery_fee,
        subtotal=order.subtotal,
        discount_amount=order.discount_amount,
        total=order.total,
        currency=currency,
        items=_items_out(order),
        steps=_reached_steps(order, first_seen),
        events=events,
        # `call_center`: الرسوم لسه متحددةش. الرقم في `delivery_fee`
        # صفر مؤقت، والواجهة تعرض النص ده بدله.
        delivery_fee_label="الرسوم هتحدد من المطعم" if order.delivery_fee_pending else None,
        customer_note=customer_note,
        cancel_reason=cancel_reason,
    )
