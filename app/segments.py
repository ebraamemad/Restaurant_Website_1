"""تقسيم العملاء إلى شرائح — الخدمة الوحيدة اللي بتحسب الجمهور.

## ليه خدمة لوحدها

التسويق بيحتاج نفس الحساب في 5 أماكن: معاينة الجمهور قبل الإرسال،
الإرسال نفسه، عدد المتلقين في التقرير، شاشة الشرائح، وربط
المستوردين. لو كل واحد من دول كتب شرطه بنفسه،بقى عندنا 5 نسخ من
"3+ طلبات" هتختلف مع الوقت، وأول واحدة بتتأخر هتبعت لعميل غلط.

فالمصدر الوحيد للحقيقة هنا: `resolve_audience` بترجّع
`AudienceQuery`، و`audience_customer_ids` و`audience_stats` بيبنوا
على **نفس** الـregistry ونفس دالة الفلترة. مفيش نسخة تانية من
منطق الشرائح في المشروع كله.

## ليه SQL مش Python

الشرائح كلها aggregations على الطلبات (عدد، مجموع، آخر تاريخ).
عملها في Python معناها نحمّل كل الطلبات في الذاكرة ونعدّها — عند
100 ألف طلب ده بطيء وبياكل الرام. الـsubquery ده بيشتغل في
الداتابيز، ويرجّع الـcustomer IDs بس.

## ليه `cancelled` مستبعد

الطلب الملغي مش إنفاق. لو حسبناه، "الأكثر إنفاقًا" هيطلع عميل
ألغى طلبه بـ800 جنيه قبل عميل اشترى 600 فعلاً، و"راجع" هيحسب
واحد رجع طلبه مش عميل. فلتر `status != 'cancelled'` في
`_order_stats` — **مكان واحد**، مش في كل شرط.

## `recently_active` / `inactive`

افتراضياً 30 يوم و90 يوم. القيم دي **برامترات** مش ثوابت متحجّرة:
`AudienceParams` بياخدها، والـregistry بيقراها من هناك. الأدمن
يقدر يعدّلها من الشاشة من غير لمس الكود.

الافتراضي معقول للمطعم المصري: 30 يوم = "لسه فاكراك"، 90 يوم =
"غاب شهور، رسالة رجوعه ممكنة". مش قانون فيزيائي — قرار تسويقي.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Callable, List, Optional

from sqlalchemy import Select, and_, func, select
from sqlalchemy.orm import Session

from app.models import Customer, Order, PushSubscription
from app.schemas import OrderStatus


class AudienceError(ValueError):
    """خطأ في تعريف الجمهور — دايماً 400 مش 500.

    `app.routers.marketing` بيحوّلها لـ`HTTPException(400)`. لو رميت
    `KeyError` مباشرة، `admin.py` بيستخدم `KeyError` كإشارة "السطر
    مش موجود" (404)، فكانت الرسالة غلط والأدمن هيفتكر إن فيه bug.
    """


# ============================================================
# معاملات العتبات
# ============================================================


@dataclass
class AudienceParams:
    """عتبة القطع لكل شريحة — قابلة للتعديل من غير لمس الكود.

    الافتراضيات مكتوبة هنا مرة واحدة. `high_spending` محسوب **نسبة**
    مش رقم مطلق: المطعم الصغير متوسطه 120 جنيه، والكبير 600.
    رقم مطلق واحد بيوصّل لعميل صفر اشتراكات في الحالة دي، فبنحسب
    النسبة من الداتابيز نفسها.
    """

    #: "عالي الإنفاق" = أنفق 2× متوسط قيمة الطلب (على الأقل حد أدنى).
    high_spending_multiplier: float = 2.0
    #: حد أدنى بالجنيه. من غيره، مطعم متوسطه 5 جنيه معناه "كل
    #: العملاء عملاء كبار" والحملة بتضيّع معناها.
    high_spending_minimum: Decimal = Decimal("200.00")
    #: نشط = طلب في آخر N يوم.
    recently_active_days: int = 30
    #: خامل = ما طلبش من N يوم.
    inactive_days: int = 90
    #: نتجاهل العملاء اللي رفضوا العروض.
    respect_opt_out: bool = True
    #: نتجاهل اللي مالهمش اشتراك push شغّال.
    #:
    #: `False` لصفحة الشرائح (الأدمن عايز يشوف كل الناس) و`True`
    #: للإرسال — مش بنبعت لمحدش مالهوش اشتراك أصلاً.
    require_push_subscription: bool = False
    #: نطابق على `customers.source`. `None` = كل المصادر.
    source: Optional[str] = None

    def validate(self) -> None:
        """يرمي لو العتبات متناقضة.

        `recently_active_days >= inactive_days` معناها شريحتين
        متعارضتين: حد ممكن يكون في الاتنين أو في ولا واحدة. ده مش
        bug في البيانات — ده اختيار سيّئ. بنمنعه عند المصدر بدل ما
        نكتشفه في تقرير بع newsletters بعت لـ900 حد و450 منهم غلط.
        """
        if self.recently_active_days < 1:
            raise AudienceError("مدة النشاط لازم تكون يوم على الأقل")
        if self.inactive_days < 1:
            raise AudienceError("مدة الخمول لازم تكون يوم على الأقل")
        if self.recently_active_days >= self.inactive_days:
            raise AudienceError(
                "مدة النشاط لازم تكون أقل من مدة الخمول، وإلا الشريحتين بقوا بعض"
            )
        if self.high_spending_multiplier < 1:
            raise AudienceError("معامل الإنفاق العالي لازم يكون 1 أو أكتر")
        if self.high_spending_minimum < 0:
            raise AudienceError("الحد الأدنى للإنفاق مش هيكون بالسالب")

    def evolve(self, **changes) -> "AudienceParams":
        """نسخة جديدة بمعاملات مختلفة — الـ`self` ما بتتغيرش.

        بنستخدم ده بدل التعديل المباشر لأن `resolve_audience` محتاج
        يشيل شرط من غير ما يبقى الشرط شيل من كود الاستدعاء التاني.
        `vars(self)` بيدي القيم الفعلية مش الافتراضيات، فالنسخة
        الجديدة بتحافظ على أي تعديل سابق.
        """
        merged = {**vars(self), **changes}
        return AudienceParams(**merged)


# ============================================================
# إحصائيات الطلبات — الـsubquery المشترك
# ============================================================


def _is_counted_order() -> object:
    """الطلب اللي بيتحسب في كل الإحصائيات.

    من غير استثناء مش واضح: الطلب الملغي بيتشال من `order_count` و
    `total_spent` و`last_order_at` مع بعض. لو شيلناه من المجموع بس،
    `high_spending` بيبقى غلط لأن عميل طلب 5 مرات ورجعهم كلهم هيبان
    "فوق المتوسط" من غير ما يدفع حاجة.
    """
    return Order.status != OrderStatus.cancelled.value


def _order_stats() -> Select:
    """تجميع إحصائيات الطلبات لكل عميل.

    عمود `order_count` بيطلع `0` مش `NULL` للعميل اللي مالوش
    طلبات (من `COALESCE` في الفلترة). ده اللي بيخلّي "الكل" تيجب
    المستوردين، و"طلب واحد" تيجب اللي عندهم طلب واحد بالظبط من غير
    ما نكتب `COALESCE` في كل شرط.
    """
    counted = and_(_is_counted_order(), Order.customer_id.isnot(None))
    return (
        select(
            Order.customer_id.label("customer_id"),
            func.count(Order.id).label("order_count"),
            func.coalesce(func.sum(Order.total), 0).label("total_spent"),
            func.max(Order.created_at).label("last_order_at"),
            func.min(Order.created_at).label("first_order_at"),
        )
        .where(counted)
        .group_by(Order.customer_id)
        .subquery()
    )


def _order_count(stats) -> object:
    return func.coalesce(stats.c.order_count, 0)


def _total_spent(stats) -> object:
    return func.coalesce(stats.c.total_spent, 0)


def _last_order_at(stats) -> object:
    return stats.c.last_order_at


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _active_subscription_exists() -> object:
    """`EXISTS` على اشتراك push شغّال للعميل ده.

    `EXISTS` مش `JOIN`: العميل ممكن يكون عنده جهازين، والـJOIN
    هيدّيه سطرين. السطرين مenta الأرقام (عدد devices)، بس هيدوّروا
   _COUNT(DISTINCT) ونجمعلهم مرتين. `EXISTS` بتقول "فيه واحد على
    الأقل" بصف واحد.
    """
    return (
        select(PushSubscription.id)
        .where(
            PushSubscription.customer_id == Customer.id,
            PushSubscription.status == "active",
        )
        .exists()
    )


def _base_statement(params: AudienceParams) -> Select:
    """الـSELECT الأساس: عميل + إحصائياته + فلاتر الأمان.

    كل شريحة بتاخد ده وبتضيف شرط واحد. الفلاتر الأساسية
    (الـopt-out، المصدر، الاشتراك) هنا عشان **ما ينفعش** شريحة
    تتخطاها بالغلط: لو `high_spending` نسيت الـopt-out، الحملة
    بتتجاهل رفض العميل silently.
    """
    stats = _order_stats()
    statement = select(Customer, stats).outerjoin(stats, stats.c.customer_id == Customer.id)
    if params.respect_opt_out:
        statement = statement.where(Customer.marketing_opt_out.is_(False))
    if params.source is not None:
        statement = statement.where(Customer.source == params.source)
    if params.require_push_subscription:
        statement = statement.where(_active_subscription_exists())
    return statement


# ============================================================
# شروط الشرائح
#
# كل شرط بياخد `(params, stats)` وبيرجّع `statement` معدّل. الـ
# `stats` جاي من `_base_statement` عشان كل الشرائح تشترك في **نفس**
# الـsubquery — لو كل واحد عمل `outerjoin` لـsubquery مستقل، PostgreSQL
# هيعمل ctEs منفصل للحالة دي.
# ============================================================


def _apply_all(params: AudienceParams, statement: Select, stats) -> Select:
    """كل العملاء اللي مش رفضوا.

    مفيش شرط إضافي — الفلاتر الأساسية في `resolve_audience` كفاية.
    الدالة موجودة عشان الـregistry شكله واحد (كل شريحة عندها
    `apply`)، مش عشان فيها منطق.
    """
    return statement


def _apply_one_time(params: AudienceParams, statement: Select, stats) -> Select:
    """طلب واحد **بالظبط**. مش "1 أو أكتر" — الـ"أو أكتر" دي returning."""
    return statement.where(_order_count(stats) == 1)


def _apply_returning(params: AudienceParams, statement: Select, stats) -> Select:
    """اتنين طلبات أو أكتر — الراجع للمطعم."""
    return statement.where(_order_count(stats) >= 2)


def _apply_min_orders(minimum: int):
    """مصنع لـ"3+ طلبات" و"5+ طلبات" بدل كاتبين نفس الشرط مرتين."""

    def apply(params: AudienceParams, statement: Select, stats) -> Select:
        return statement.where(_order_count(stats) >= minimum)

    return apply


def _apply_high_spending(params: AudienceParams, statement: Select, stats) -> Select:
    """أنفق 2× متوسط قيمة الطلب، وبحد أدنى مطلق.

    ## ليه متوسط **الطلبات** مش متوسط **العملاء**

    متوسط الطلبات = `sum(total) / count(orders)`. متوسط الإنفاق لكل
    عميل = `sum / count(distinct customer)`. الفرق مهم: عميل واحد
    بـ10 طلبات بيزوّد المتوسط كله، فلو حسبنا متوسط العملاء هنقول
    "العميل ده عادي" وهو أكتر حد خلّى المتوسطات تتغيّر. المتوسطات
    لازم تكون على مستوى الطلب عشان تبقى مقياس صادق.

    ## ليه الـsubquery التاني

    `avg_order_value` محتاج `count(*)` و`sum(total)` على مستوى كل
    الطلبات. الـsubquery الأول (per-customer) بيجمّع، فمش هينفع
    نقسم على خالص. فبنعمل واحد تاني على مستوى الجدول كله، وبنقارن
    `total_spent` (per-customer) بيه.
    """
    global_avg = select(func.coalesce(func.avg(Order.total), 0)).where(_is_counted_order()).scalar_subquery()
    threshold = func.greatest(
        global_avg * params.high_spending_multiplier,
        params.high_spending_minimum,
    )
    return statement.where(_total_spent(stats) >= threshold)


def _apply_recently_active(params: AudienceParams, statement: Select, stats) -> Select:
    """طلب في آخر `recently_active_days` يوم.

    بنقارن `max(created_at)` مش `customers.last_order_at` — العمود
    ده denormalized وبيتحدّث وقت إنشاء الطلب، فلو اتلغبط مرة في
    البيانات الـsegmentation هيبقى غلط بصمت. المصدر هو الطلب نفسه.
    """
    since = _now_utc() - timedelta(days=params.recently_active_days)
    return statement.where(_last_order_at(stats) >= since)


def _apply_inactive(params: AudienceParams, statement: Select, stats) -> Select:
    """ما طلبش من `inactive_days` يوم.

    **العملاء اللي مالهمش طلبات أصلاً مش في الشريحة دي.** عميل
    مستورد جديد لسه ما طلبش هو "خامل" تقنياً، بس معناه في بيزنس
    "لسه عميل جديد مش عميل راجع". خلطهم معناه حملة رجوع بتبعت
    لبرضو لحد ما اشترى مننا. الـpredicate هنا `last_order_at IS NOT
    NULL AND < cutoff` — التلات شروط لازم مع بعض.
    """
    cutoff = _now_utc() - timedelta(days=params.inactive_days)
    return statement.where(
        and_(_last_order_at(stats).isnot(None), _last_order_at(stats) < cutoff)
    )


# ============================================================
# الـregistry
# ============================================================


@dataclass(frozen=True)
class Segment:
    """شريحة واحدة — تعريفها في مكان واحد.

    `apply` بياخد الـparams وقت التنفيذ مش وقت الـimport، عشان تغيير
    العتبة يبقى تغيير قيمة مش إعادة تحميل موديول.
    """

    key: str
    label: str
    description: str
    apply: Callable[[AudienceParams, Select, object], Select]
    #: إزاي نصفّح الرقم للعرض. مفيش فايدة من "أعلى إنفاق" فيها ناس
    #: ما اشتروش.
    requires_orders: bool = True
    #: ملاحظة بتظهر تحت الاسم في الشاشة.
    hint: str = ""


def _registry() -> dict[str, Segment]:
    """بناء الـregistry.

    مبنية جوه دالة مش متغير عام: `AudienceParams` بيتبني في
    الاختبارات بعتبات مختلفة، ومن غير دالة كل `import` هيبني
    الـregistry تاني بـparams ثابتة. غير كده الـregistry `frozen`
    وبيفضل واحد طول عمر العملية.
    """
    return {
        "all": Segment(
            key="all",
            label="كل العملاء",
            description="كل عميل مش رفض العروض — بما فيهم اللي ما طلبوش.",
            apply=_apply_all,
            requires_orders=False,
            hint="الأوسع. راجع العدد قبل الإرسال.",
        ),
        "one_time": Segment(
            key="one_time",
            label="طلب واحد",
            description="عملاء عندهم طلب واحد بس — فرصة تحويل لعميل راجع.",
            apply=_apply_one_time,
        ),
        "returning": Segment(
            key="returning",
            label="راجع (2+)",
            description="اتنين طلبات أو أكتر — عملاء عرفوا المنتج.",
            apply=_apply_returning,
        ),
        "orders_3plus": Segment(
            key="orders_3plus",
            label="3 طلبات أو أكتر",
            description="عملاء متكررين بدرجة.",
            apply=_apply_min_orders(3),
        ),
        "orders_5plus": Segment(
            key="orders_5plus",
            label="5 طلبات أو أكتر",
            description="العملاء الأثقل سلوكاً.",
            apply=_apply_min_orders(5),
        ),
        "high_spending": Segment(
            key="high_spending",
            label="إنفاق عالي",
            description="أنفق 2× متوسط الطلب على الأقل، وبحد أدنى مطلق.",
            apply=_apply_high_spending,
            hint="العتبة بتتحسب من متوسط المطعم، مش رقم ثابت.",
        ),
        "recently_active": Segment(
            key="recently_active",
            label="نشط حديث",
            description="طلب في آخر 30 يوم.",
            apply=_apply_recently_active,
            hint="نافذة قابلة للتعديل من الشاشة.",
        ),
        "inactive": Segment(
            key="inactive",
            label="خامل",
            description="ما طلبش من 90 يوم — قائمة إعادة.",
            apply=_apply_inactive,
            hint="اللي بسّطوا وما طلبوش مش فيهم.",
        ),
    }


SEGMENTS: dict[str, Segment] = _registry()


def get_segment(key: str) -> Segment:
    """يرمي `AudienceError` لـkey مش معروف (→ 400)."""
    segment = SEGMENTS.get(key)
    if segment is None:
        raise AudienceError(f"شريحة مش معروفة: {key}")
    return segment


def list_segments() -> List[Segment]:
    return list(SEGMENTS.values())


def parse_params(
    *,
    recently_active_days: Optional[int] = None,
    inactive_days: Optional[int] = None,
    high_spending_minimum: Optional[Decimal] = None,
    high_spending_multiplier: Optional[float] = None,
) -> AudienceParams:
    """يبني `AudienceParams` من query string مع clamping.

    الأدمن بيبعت الأرقام دي من `<input type=number>`، فأي حاجة
    جاية من الـbrowser مش موثوقة. `int(value)` بيرمي `ValueError`
    على نص، و`Decimal("abc")` بيرمي `decimal.InvalidOperation` —
    الاتنين هتطلع 500 من غير ه.Handle. هنا بنمسكهم ونرجع للافتراضي
    بدل ما نكسر الصفحة.
    """
    params = AudienceParams()

    def as_int(value, fallback: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return fallback

    def as_decimal(value, fallback: Decimal) -> Decimal:
        try:
            return Decimal(str(value))
        except Exception:
            return fallback

    def as_float(value, fallback: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return fallback

    params.recently_active_days = as_int(recently_active_days, params.recently_active_days)
    params.inactive_days = as_int(inactive_days, params.inactive_days)
    params.high_spending_minimum = as_decimal(
        high_spending_minimum, params.high_spending_minimum
    )
    params.high_spending_multiplier = as_float(
        high_spending_multiplier, params.high_spending_multiplier
    )
    params.validate()
    return params


# ============================================================
# الجمهور
# ============================================================


@dataclass
class AudienceStats:
    """أرقام الشريحة — بتتجاوب قبل الإرسال.

    ## ليه الأرقام دي منفصلة عن `total_recipients`

    `total_recipients` = اللي **اتبعت فعلاً** (نتايج). الأرقام دي =
    اللي **ممكن** يتبعت (تقدير). الفرق بينهم بيساوي الـleaks:
    عملاء رفضوا، وناس مالهمش اشتراك. الأدمن لازم يشوف الفرق قبل
    ما يدوس "إرسال".
    """

    total: int = 0
    opted_out: int = 0
    no_subscription: int = 0
    recipients: int = 0
    total_spent: Decimal = Decimal("0.00")
    average_order_value: Decimal = Decimal("0.00")
    order_count: int = 0

    def as_dict(self) -> dict:
        return {
            "total": self.total,
            "opted_out": self.opted_out,
            "no_subscription": self.no_subscription,
            "recipients": self.recipients,
            "total_spent": str(self.total_spent),
            "average_order_value": str(self.average_order_value),
            "order_count": self.order_count,
        }


@dataclass
class AudienceQuery:
    """نتيجة `resolve_audience` — جاهزة للتنفيذ."""

    segment: Segment
    params: AudienceParams
    statement: Select
    stats_subquery: object
    #: شريحة "الكل" مالهاش شرط. الشاشة بتعرضها كـwarning.
    is_everyone: bool = False


def resolve_audience(
    audience_type: str,
    segment_key: Optional[str] = None,
    params: Optional[AudienceParams] = None,
) -> AudienceQuery:
    """يبني استعلام الجمهور **من غير ما يمسّ الداتابيز**.

    الفصل ده مقصود: الدالة بتختار الشريحة وتبني الـWHERE، والـcount
    والـIDs في دوال تانية. ده بيخلّي اختبار منطق الشرائح مفيش
    فيه database أصلاً — نتحقق من الـSQL اللي اتبنى، مش من نتيجة
    على بيانات معيّنة.

    ## `audience_type='imported'`

    بتاخد نفس مسار "الكل" (`all`) بس بـ`source='import'`. ده بيخلّي
    استيراد جهات الاتصال مفيد من غير ما نعمل جدول خاص بجمهور
    المستوردين.
    """
    params = params or AudienceParams()
    params.validate()

    if audience_type != "segment" and segment_key:
        # `all`/`imported` بيستهناوا الـ`segment_key` لوحدهم. ده بيخلي
        # طلب متناقض (نوع جمهور "الكل" + شريحة) يعدّي صامت، والأدمن
        # يفتكر إنه بيبعت لشرحة وهو بيبعت للكل — أو العكس. نفس القاعدة
        # اللي `ck_campaigns_audience_segment` بيفرضها في الداتابيز.
        raise AudienceError("الشريحة بتتفحص مع الجمهور من نوع 'segment' بس")

    if audience_type == "all":
        segment = SEGMENTS["all"]
        effective = params
        is_everyone = True
    elif audience_type == "imported":
        segment = SEGMENTS["all"]
        effective = params.evolve(source="import")
        is_everyone = False
    elif audience_type == "segment":
        if not segment_key:
            raise AudienceError("لازم تختار شريحة")
        segment = get_segment(segment_key)
        effective = params
        is_everyone = segment.key == "all"
    else:
        raise AudienceError(f"نوع جمهور مش معروف: {audience_type}")

    stats = _order_stats()
    statement = select(Customer, stats).outerjoin(stats, stats.c.customer_id == Customer.id)
    if effective.respect_opt_out:
        statement = statement.where(Customer.marketing_opt_out.is_(False))
    if effective.source is not None:
        statement = statement.where(Customer.source == effective.source)
    if effective.require_push_subscription:
        statement = statement.where(_active_subscription_exists())

    statement = segment.apply(effective, statement, stats)
    return AudienceQuery(
        segment=segment,
        params=effective,
        statement=statement,
        stats_subquery=stats,
        is_everyone=is_everyone,
    )


def _count_of(db: Session, statement: Select) -> int:
    return int(db.scalar(select(func.count()).select_from(statement.subquery())) or 0)


def audience_customer_ids(
    db: Session,
    audience_type: str,
    segment_key: Optional[str] = None,
    params: Optional[AudienceParams] = None,
    *,
    limit: Optional[int] = None,
) -> List[int]:
    """الـcustomer IDs المستهدفة، مرتبة بأحدث طلب.

    مرتّبة بـ`last_order_at DESC` عشان لو حصل `limit` (حملة تجريبية
    على 50 مثلاً) تجيب الناس النشطين، مش عشوائي.
    """
    query = resolve_audience(audience_type, segment_key, params)
    order_column = func.coalesce(query.stats_subquery.c.last_order_at, Customer.created_at)
    statement = query.statement.order_by(order_column.desc(), Customer.id)
    if limit is not None:
        if limit < 1:
            raise AudienceError("الحد لازم يكون 1 على الأقل")
        statement = statement.limit(limit)
    # `with_only_columns` على نفس الـstatement — **مش** `select_from(subquery)`.
    #
    # الفرق ده مش cosmetic: `select(Customer.id).select_from(statement.subquery())`
    # بيخلّي SQLAlchemy يرجّع `customers` للـFROM الخارجي، لأن عمود
    # `Customer.id` مش موجود جوه الـsubquery. النتيجة
    # `FROM (الفرز والفلتر) AS anon_1, customers` = **منتج ديكارتّي**،
    # وبيقرأ الـid من `customers` مش من الفرع المُفلتر. يعني
    # `audience_customer_ids` كانت بترجّع **كل** العملاء × عدد الصفوف —
    # والرافضين ellipt-out بينزلوا في السطر.
    rows = db.execute(statement.with_only_columns(Customer.id)).all()
    return [int(row[0]) for row in rows]


def audience_stats(
    db: Session,
    audience_type: str,
    segment_key: Optional[str] = None,
    params: Optional[AudienceParams] = None,
) -> AudienceStats:
    """أرقام المعاينة: كام واحد، كام رفض، كام مالوش اشتراك.

    ## ليه بنحسب الفروق في نفس المرور

    لو حسبنا كل واحد لوحده هنعمل 3 queries. الأهم إن الأرقام دي
    بتتحسب من **نفس** الـregistry اللي هيتبعت منه الإشعار. أي
    فرق بين المعاينة والإرسال معناه إننا وعودنا بحاجة غير اللي
    بنبعته.
    """
    base_params = params or AudienceParams()

    # المصفوفة دي **معرّفة هنا**، مش موروثة من الـcaller.
    #
    # `params.require_push_subscription` بيعتمد على مين نادى: `True` من
    # مسار الإرسال/المعاينة، `False` من اللوحة والشرائح. لو بنينا
    # `recipients` على ده، نفس الشريحة هترجّع رقمين مختلفين حسب الصفحة
    # اللي اتنادى منها — و"اللي هيتبعت فعلاً" رقم واحد يا ريت.
    #
    #     respect_opt_out | require_push | المعنى
    #     --------------- | ------------- | -----------------------------------------
    #     recipients      | نعم          | هيتبعت فعلاً: شريحة ∩ مش رافض ∩ له اشتراك
    #     reachable       | لا            | شريحة ∩ مش رافض → الفرق = مالهمش اشتراك
    #     total           | لا / لا       | الشريحة كلها → الفرق = الرافضين
    stats_params = base_params.evolve(
        respect_opt_out=True, require_push_subscription=True
    )
    query = resolve_audience(audience_type, segment_key, stats_params)
    stats = query.stats_subquery

    # `SUM` على الأرقام دي مش اختصار — **شرط صحة الـSQL**.
    # أول ما `count(customers.id)` يدخل الـselect list، PostgreSQL بيعتبر
    # الاستعلام كله aggregate، فأي عمود تاني مش مُجمَّع لازم يكون في
    # `GROUP BY`. من غير الـ`SUM` دي: `GroupingError: column
    # "anon_1.order_count" must appear in the GROUP BY clause` — وده
    # كان الـ500 على `/admin/marketing`.
    #
    # النتيجة صف واحد بس (حتى لو الجمهور فاضي: `COUNT` = 0 و`SUM` = NULL
    # → `COALESCE` = 0)، فالجمع باPython كان زايدة.
    row = db.execute(
        query.statement.with_only_columns(
            func.coalesce(func.sum(_order_count(stats)), 0),
            func.coalesce(func.sum(_total_spent(stats)), 0),
            func.count(Customer.id),
        )
    ).one()
    order_count_total = int(row[0] or 0)
    spent_total = Decimal(str(row[1] or 0))
    recipients = int(row[2] or 0)

    # الشريحة كلها: لا الرفض ولا الاشتراك بيتحسبوا.
    total = _count_of(
        db,
        resolve_audience(
            audience_type,
            segment_key,
            stats_params.evolve(
                respect_opt_out=False, require_push_subscription=False
            ),
        ).statement,
    )
    # الشريحة بعد استبعاد الرافضين، **قبل** شرط الاشتراك.
    reachable = _count_of(
        db,
        resolve_audience(
            audience_type,
            segment_key,
            stats_params.evolve(require_push_subscription=False),
        ).statement,
    )

    # الأرقام دي **متتابعة**: `total = opted_out + no_subscription +
    # recipients`. عشان كده الفرق بين كل واحد والتالي، مش بين `total`
    # و`recipients`:
    #   opted_out      = total     - reachable   (الرافضين)
    #   no_subscription= reachable - recipients  (مالهمش اشتراك)
    # لو حسبنا `opted_out = total - recipients` كانت عميلة رافضة **و**
    # مالها اشتراك بتتحسب مرتين، والرقم بيكبر عن الرافضين الحقيقيين.
    return AudienceStats(
        total=total,
        opted_out=max(total - reachable, 0),
        no_subscription=max(reachable - recipients, 0),
        recipients=recipients,
        total_spent=spent_total,
        average_order_value=(
            (spent_total / order_count_total).quantize(Decimal("0.01"))
            if order_count_total
            else Decimal("0.00")
        ),
        order_count=order_count_total,
    )


def segment_breakdown(
    db: Session, params: Optional[AudienceParams] = None
) -> List[tuple[Segment, int]]:
    """عدد كل شريحة — لصفحة الشرائح.

    ## ليه `respect_opt_out` بيتقفل هنا

    صفحة الشرائح بتقول "كام عميل في الشريحة دي" للاختيار والتخطيط.
    لو قلنا 40 والتسعة رفضوا، الأدمن هيختار شريحة ويتبعت لـ31.
    الأرقام دي **قبل** احترام الرفض؛ الأرقام اللي بتتبعت فعلاً في
    `audience_stats`. منعنا الخلط.

    ## التكرار مقبول ومتعمد

    استعلام لكل شريحة (7 إجمالاً) بدل واحد. ده رقم ثابت معروف مش
    حلقة بتكبر. البديل كان CASE expressions في CTE واحد — أطول
    بكتير وأصعب في القراءة، ومفيش فايدة من تكتيك هنا.
    """
    show_params = (params or AudienceParams()).evolve(respect_opt_out=False)
    out: List[tuple[Segment, int]] = []
    for segment in SEGMENTS.values():
        out.append((segment, _count_of(db, resolve_audience("segment", segment.key, show_params).statement)))
    return out
