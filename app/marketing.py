"""حملات التسويق — الإنشاء، المعاينة، الإرسال، الإيقاف، السجل.

الـendpoint. يعني لو اتغير `app.push`، الحملة مش بتتأثر.

`app.push` واثق وبيعرف **إزاي** يبعت إشعار لاشتراك واحد. الموديول ده
بيعرف **مين** يستقبل و**إمتى**، ومش بيفتكر في التوقيع ولا في
الـendpoint. يعني لو اتغير `app.push`، الحملة مش بتتأثر.

الفصل ده مقصود عشان المرحلة الجاية (واتساب) تضيف قناة تانية من غير
ما تلمس ده.

## ليه `require_push_subscription=True` في الإرسال

عميل مالوش اشتراك push مش بيبقى مستلم — مفيش "to" حد مش
موجود. العدّاد `recipients` معناه **أجهزة** مش **عملاء** (عميل واحد
بجهازين = سطرين). عشان كده `total_recipients` بيتقارن بعدد
الاشتراكات مش بعدد العملاء.

## ليه الحلقة بتقطع (chunk)

الإرسال 4000 رسالة sync بياخد دقايق. من غير تقطيع، الـHTTP request
بيوصل حد البوابة(proxy timeout) والأدمن هيشوف "فشل" رغم إن نصف
الرسائل اتبعت. التقطيع بيعمل commit كل 50 رسالة، فـ:
- اللي اتبعت بيبقى **محفوظ** حتى لو العملية اتقطعت.
- الإلغاء بيبقى محسوس خلال 50 رسالة مش في آخر الحملة.
- `campaign.status` بيتقرا من الداتابيز كل شريحة، فدوس "إيقاف" من
  تاب تاني بيشتغل فعلاً.

## ليه مش background tasks ولا Celery

`FastAPI.BackgroundTasks` بيموت مع الـrequest أو مع الـworker.
سطر أوامر واحد بيشغّل campaign مسائي مش هيلفّ-taifa. الحل الوحيد
السليم هو process دائم، وده بنية مش feature.

الحل اللي بنفّذه: **lazy tick**. `run_due_campaigns` بتتفادى في
كل مرة الأدمن يفتح صفحة الحملات أو يدوس "شغّل المستحق". يعني
الحملة المجدولة بتنفّذ أول ما حد يفتح اللوحة. ده ماشي مع بنية
مونوليث، وبيبقى **صريح** في الواجهة إن مفيش daemon.

## حالة "الإيقاف" أخدام بصراحة

مفيش استرجاع. لما دوس الأدمن "إيقاف"، الرسالة اللي المزوّد
سلّمها خلاص **توصّل**. اللي بنقدر نوقفه هو: الرسائل اللي لسه
مش اتبعت. عشان كده `campaign.status` بيبقى `cancelled` مش
`completed`، والتقرير بيقول "اتبعت X من Y، الباقي اتلغى".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app import push
from app.models import (
    Campaign,
    CampaignRecipient,
    Customer,
    Order,
    PushSubscription,
    WhatsAppAttempt,
)
from app.segments import (
    AudienceParams,
    AudienceStats,
    audience_customer_ids,
    audience_stats,
    parse_params,
    resolve_audience,
)

logger = logging.getLogger(__name__)

# ============================================================
# الثوابت
# ============================================================

STATUS_DRAFT = "draft"
STATUS_SCHEDULED = "scheduled"
STATUS_SENDING = "sending"
STATUS_COMPLETED = "completed"
STATUS_PARTIAL = "partially_completed"
STATUS_CANCELLED = "cancelled"
STATUS_FAILED = "failed"

CHANNEL_PUSH = "push"
CHANNEL_WHATSAPP = "whatsapp"
CHANNEL_BOTH = "both"

#: القنوات اللي تقدر تتنفّذ **دلوقتي**. واتساب بقت منفّذة في
#: Phase 5 — بس تنفيذها محتاج إعدادات: من غيرها الحملة بتتقفل
#: بـ`ChannelNotReady` **قبل** ما تتحوّل لـ`sending`.
EXECUTABLE_CHANNELS = frozenset({CHANNEL_PUSH, CHANNEL_WHATSAPP, CHANNEL_BOTH})

#: Campaign statuses اللي يقدر يتحوّل لـsending. `completed` و
#: `cancelled` نهائيين — إعادة إرسال حملة اتلغت معناها الأدمن غيّر
#: رأيه اعتم’d حملة جديدة.
SENDABLE_FROM = frozenset({STATUS_DRAFT, STATUS_SCHEDULED})

#: كم رسالة قبل ما نعمل commit ونراجع حالة الإلغاء.
SEND_CHUNK_SIZE = 50

#: أقصى عدد متلقين لحملة واحدة. حماية من "اخترت الكل" بالغلط:
#: 100 ألف إشعار في نص ساعة مش حملة، ده incident. الحد قابل
#: للraise لو المطعم كبر فعلاً.
MAX_RECIPIENTS = 20_000

#: `audience` value بنكتبه في `push_notifications`.
#:
#: **مقصود إنه مختلف** عن `order_status` و`new_order`. طلبات
#: الخدمة بتتجاهل الرفض، والحملات التسويقية لا. لو استعملنا نفس
#: القيمة، لو كود بعد كده عمل فلتر "غيّر إشعارات الخدمة" هيلاقي
#: الخدمة" وهو بيكتم العروض كمان.
AUDIENCE_MARKETING = "marketing"

#: رابط الرجوع لو الأدمن ما حددش رابط. `/` صفحة المنيو.
DEFAULT_CAMPAIGN_URL = "/"


class CampaignError(ValueError):
    """مشكلة في الحملة → 400 (مش 500)."""


class ChannelNotReady(CampaignError):
    """القناة معروفة بس مش جاهزة للإرسال — الرسالة بتقول ليه.

    رفعنا الحالة دي في Phase 5 عن حالتين كانت بتاخد نفس الاسم:
    "القناة لسه منفّذتش" (خلاص بقت غلط — بقت منفّذة) و"الإعدادات
    ناقصة" (اللي محتاج يتقال فعلاً). الأدمن بيشوف الفرق في
    الرسالة، والاختبارات بيفرّقوا في الكود.
    """


# ============================================================
# التحقق
# ============================================================


#: **`channel` مش هنا عن قصد.** القناة عمود لوحده في `campaigns`
#: بيتقفل وقت الإنشاء؛ تخزينها هنا كان هيسمح يعدّلها بعد المعاينة
#: من غير ما الأدمن يشوف، والتقرير يبقى غلط.
#:
#: الحقول اللي بتتخزّن في `campaigns.audience_params`.
#:
#: **مش كل حقول `AudienceParams`:** `respect_opt_out` دايماً `True`
#: (دايماً بنحترم الرفض)، و`require_push_subscription` بيتحدّد في مسار
#: الإرسال (`False` للمعاينة، `True` للبعت)، و`source` بييجي من
#: `resolve_audience` لو كان الجمهور `imported`. تخزينهم هيخلّي
#: الحملة تسجّل قيم مش الأدمن اللي اختارها.
_AUDIENCE_PARAM_FIELDS = (
    "recently_active_days",
    "inactive_days",
    "high_spending_minimum",
    "high_spending_multiplier",
)


def _params_to_dict(params: Optional["AudienceParams"]) -> dict:
    """يتسلسل `AudienceParams` للعمود JSON.

    `Decimal` بيتحوّل لـ`str` مش float: `high_spending_minimum` فلوس،
    و`float` بيبوظ تمثيل الكسور العشرية (0.1 مش 0.1 بالظبط).
    `parse_params` بيرجّعه لـ`Decimal` من النص، فالدورة مغلقة بلا فقد.
    """
    if params is None:
        return {}
    stored: dict = {}
    for field_name in _AUDIENCE_PARAM_FIELDS:
        value = getattr(params, field_name, None)
        if value is None:
            continue
        stored[field_name] = str(value) if isinstance(value, Decimal) else value
    return stored


def validate_audience_fields(audience_type: str, segment_key: Optional[str]) -> None:
    """يفرض التناسق بين نوع الجمهور والشريحة.

    نفس القاعدة اللي `ck_campaigns_audience_segment` بيفرضها في
    الداتابيز. التكرار هنا مقصود: الـCHECK بيحمي الداتابيز من
    `INSERT` جاي من برّه (SQL، migration، سكربت)، والوظيفة دي
    بترجّع رسالة عربية بدل `IntegrityError`.
    """
    if audience_type not in {"all", "segment", "imported"}:
        raise CampaignError(f"نوع جمهور مش معروف: {audience_type}")
    if audience_type == "segment":
        if not segment_key:
            raise CampaignError("اختار شريحة، أو غيّر نوع الجمهور")
        # بيرمي `AudienceError` لو المفتاح مش معروف.
        resolve_audience("segment", segment_key)
    elif segment_key:
        raise CampaignError("الشريحة بتتفحص مع الجمهور من نوع 'segment' بس")


def validate_channel(channel: str) -> None:
    if channel not in {CHANNEL_PUSH, CHANNEL_WHATSAPP, CHANNEL_BOTH}:
        raise CampaignError(f"قناة مش معروفة: {channel}")


def is_executable(channel: str) -> bool:
    return channel in EXECUTABLE_CHANNELS


# ============================================================
# البناء والمعاينة
# ============================================================


def build_payload(campaign: Campaign) -> dict:
    """الـpayload اللي هيتأرّع.

    ملاحظة على الطول: `title` عمود 160 و`message` عمود `Text`، بس
    `PushNotification.title` 160 و`body` 300. التقصير هنا بيحمي
    جدول السجل من `DataError` لو حجم رسالة كبير، وبيحمي العميل من
    إشعار بيقص في نصّه.

    ## `order_id` بيبقى `None`

    ده **مقصود**، وبيفصل تسويقي عن خدمي: قوائم
    `/api/push/notifications` بتفلتر بـ`order_id`، فإشعار الحملة
    مش بيظهر في صفحة تتبع أي طلب. (شوف `audience` كمان.)
    """
    title = (campaign.title or "").strip()[:160]
    message = (campaign.message or "").strip()[:300]
    payload: dict = {
        "title": title or "عرض من المطعم",
        "body": message,
        "url": (campaign.link_url or "").strip() or DEFAULT_CAMPAIGN_URL,
        "audience": AUDIENCE_MARKETING,
        "order_id": None,
        "campaign_id": campaign.id,
        "kind": "campaign",
    }
    if campaign.image_url:
        payload["icon"] = campaign.image_url.strip()[:600]
    return payload


@dataclass
class AudiencePreview:
    """معاينة قبل الإرسال — اللي بيشوفه الأدمن."""

    audience_type: str
    segment_key: Optional[str]
    segment_label: str
    stats: AudienceStats
    channel: str
    executable: bool
    #: رسالة تحذير. `None` يعني مفيش تحذير.
    warning: Optional[str] = None
    #: أول 5 جهات اتصال عشان الأدمن يشوف إن الجمهور صح.
    samples: List[tuple[str, str, int, Decimal]] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "audience_type": self.audience_type,
            "segment_key": self.segment_key,
            "segment_label": self.segment_label,
            "channel": self.channel,
            "executable": self.executable,
            "warning": self.warning,
            "stats": self.stats.as_dict(),
        }


def preview_audience(
    db: Session,
    audience_type: str,
    segment_key: Optional[str] = None,
    *,
    channel: str = CHANNEL_PUSH,
    params: Optional[AudienceParams] = None,
    sample_size: int = 5,
) -> AudiencePreview:
    """أرقام الجمهور **قبل** الإرسال.

    `require_push_subscription=True` هنا مش `False` عادي: الأدمن
    لازم يشوف الرقم اللي هيتبعت فعلاً ( devices )، مش رقم
    العملاء. لو عرضنا 3000 عميل و1100 منهم بلا اشتراك، الأدمن
    هيضغط "إرسال" ويكتشف بعدين إن 1900 ما وصلهمش.
    """
    validate_audience_fields(audience_type, segment_key)
    validate_channel(channel)

    send_params = (params or AudienceParams()).evolve(require_push_subscription=True)
    query = resolve_audience(audience_type, segment_key, send_params)
    stats = audience_stats(db, audience_type, segment_key, send_params)

    samples = _sample_recipients(db, audience_type, segment_key, send_params, sample_size)

    warning: Optional[str] = None
    if not is_executable(channel):
        warning = (
            "قناة واتساب لسه منفّذتش (Phase 5). الحملة هتتسجّل بس مش هتتنفّذ."
        )
    elif stats.recipients == 0:
        warning = "مفيش حد هيستقبل. راجع الفلتر أو الـopt-out."
    elif stats.recipients > MAX_RECIPIENTS:
        warning = (
            f"الجمهور {stats.recipients} — أكبر من الحد {MAX_RECIPIENTS}. "
            "قسّم الشريحة أو ارفع الحد في الكود."
        )
    elif query.is_everyone:
        warning = f"ده كل العملاء ({stats.recipients}). هتيبعت لكل حد مش رفض."

    return AudiencePreview(
        audience_type=audience_type,
        segment_key=segment_key,
        segment_label=query.segment.label,
        stats=stats,
        channel=channel,
        executable=is_executable(channel),
        warning=warning,
        samples=samples,
    )


def _sample_recipients(
    db: Session,
    audience_type: str,
    segment_key: Optional[str],
    params: AudienceParams,
    limit: int,
) -> List[tuple[str, str, int, Decimal]]:
    """أول 5 أسماء + أرقام من الجمهور.

    الغرض **التحقق البصري**: لو الأدمن طلب "3+ طلبات" وشاف 12 اسم
    وأرقام،Tuple محق. الأرقام دي برضه بتتحط في التقرير بعد الإرسال،
    فمش مخصوص بالمعاينة.
    """
    if limit <= 0:
        return []
    ids = audience_customer_ids(
        db, audience_type, segment_key, params, limit=limit
    )
    if not ids:
        return []
    rows = db.execute(
        select(
            Customer.name,
            Customer.phone,
            func.count(Order.id),
            func.coalesce(func.sum(Order.total), 0),
        )
        .outerjoin(Order, Order.customer_id == Customer.id)
        .where(Customer.id.in_(ids))
        .group_by(Customer.id)
    ).all()
    return [(r[0] or "—", r[1] or "", int(r[2] or 0), Decimal(str(r[3] or 0))) for r in rows]


# ============================================================
# إنشاء الحملة
# ============================================================


def create_campaign(
    db: Session,
    *,
    name: str,
    title: str,
    message: str,
    audience_type: str,
    channel: str = CHANNEL_PUSH,
    segment_key: Optional[str] = None,
    image_url: Optional[str] = None,
    link_url: Optional[str] = None,
    scheduled_at: Optional[datetime] = None,
    admin_id: Optional[int] = None,
    params: Optional[AudienceParams] = None,
) -> Campaign:
    """يعمل حملة بحالة `draft` أو `scheduled`.

    ## ليه `draft` دايماً أول

    إنشاء حملة مش معناه إرسال. الأدمن يختار الجمهور، بشوف
    الأرقام، يتأكد من الرسالة، وبعدين يدوس "إرسال". `scheduled_at`
    بتخلي الحالة `scheduled` بس لو الوقت في المستقبل.
    """
    validate_audience_fields(audience_type, segment_key)
    validate_channel(channel)

    if scheduled_at is not None and scheduled_at <= _now_utc():
        # وقت ماضٍ = "ابعت دلوقتي" مش "جدولته في الماضي". بنرفض
        # عشان الأدمن ما يفتكرش إنه اتجدولت.
        raise CampaignError("وقت الجدولة لازم يكون في المستقبل")

    status = STATUS_SCHEDULED if scheduled_at else STATUS_DRAFT
    campaign = Campaign(
        name=(name or "").strip()[:120],
        title=(title or "").strip()[:160],
        message=(message or "").strip(),
        audience_type=audience_type,
        segment_key=segment_key if audience_type == "segment" else None,
        channel=channel,
        status=status,
        image_url=(image_url or "").strip()[:600] or None,
        link_url=(link_url or "").strip()[:600] or None,
        scheduled_at=scheduled_at,
        created_by_admin_id=admin_id,
        audience_params=_params_to_dict(params),
    )
    db.add(campaign)
    db.commit()
    db.refresh(campaign)
    return campaign


def schedule_campaign(db: Session, campaign: Campaign, when: datetime) -> Campaign:
    """يعدّل موعد حملة في `draft`."""
    if campaign.status != STATUS_DRAFT:
        raise CampaignError("الحملة دي مش مسودة")
    if when <= _now_utc():
        raise CampaignError("وقت الجدولة لازم يكون في المستقبل")
    campaign.scheduled_at = when
    campaign.status = STATUS_SCHEDULED
    db.commit()
    db.refresh(campaign)
    return campaign


def cancel_campaign(db: Session, campaign: Campaign, reason: str = "أوقفها الأدمن") -> Campaign:
    """يوقف الحملة.

    **مش بيلغي حاجة اتبعت.** الرسالة اللي المزوّد سلّمها بتوصل
    للعميل. اللي بنوقفه هو الباقي. لو الحملة كانت في `sending`،
    # الحالة هنا هيتبان في الـchunk الجاي.

    الحالات النهائية (`completed` / `cancelled` / `failed`) مش
    بتتقبل: campaign اتلغت خلاص، إعادة الإلغاء بتلفظ.
    """
    if campaign.status in {STATUS_COMPLETED, STATUS_CANCELLED, STATUS_FAILED}:
        raise CampaignError("الحملة دي خلصت — مفيش حاجة تتوقف")
    campaign.status = STATUS_CANCELLED
    campaign.cancelled_at = _now_utc()
    campaign.cancel_reason = (reason or "")[:300]
    db.commit()
    db.refresh(campaign)
    return campaign


# ============================================================
# الإرسال
# ============================================================


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _refresh_status(db: Session, campaign: Campaign) -> str:
    """يقرأ الحالة الحديثة من الداتابيز وتحدّث الكائن.

    الاستعلام المباشر أوضح من `db.expire()` هنا: إحنا عايزين
    **نعرف** إن حالة تانية اتغيّرت، مش بس نعمل invalidate. وقلة
    الاستعلامات مقصودة — ده بيتنفّذ مرة كل `chunk_size` رسالة.
    """
    db.refresh(campaign)
    return campaign.status


def _recipient_rows_for(db: Session, campaign: Campaign, params: AudienceParams) -> int:
    """يبني سطور `campaign_recipients` من الجمهور. **مش بيبعت.**

    فصل "تحديد المستلمين" عن "الإرسال" مقصود: لو الـresolve فشل
    (استعلام غلط، جمهور اتغيّر في نفس اللحظة) الحملة بتفضل
    `draft`/`scheduled` ومفيش أرقام مضلّلة في التقرير.

    ## ليه `limit=MAX_RECIPIENTS + 1`

    عشان نرفض الجمهور الضخم **من غير ما نحمّله**. `COUNT(*)` هيدّي
    رقم دقيق بس مش هيقولنا مين. لما نطلب `MAX+1` سطر بس: لو رجعوا
    كلهم، يبقى فيه أكتر من الحد وبنرفض. لو رجع أقل، يبقى ده
    الجمهور كامل. ذاكرة محدودة وخطأ واضح.
    """
    customer_ids = audience_customer_ids(
        db,
        campaign.audience_type,
        campaign.segment_key,
        params,
        limit=MAX_RECIPIENTS + 1,
    )
    if len(customer_ids) > MAX_RECIPIENTS:
        raise CampaignError(
            f"الجمهور أكبر من الحد المسموح ({MAX_RECIPIENTS}). "
            "قسّم الشريحة أو ارفع الحد في الكود."
        )
    if not customer_ids:
        return 0

    # اشتراك واحد لكل جهاز. العميل بجهازين = سطرين (بعتنا
    # رسالتين لأجهزة تانية — ده المطلوب).
    subs = (
        db.execute(
            select(PushSubscription).where(
                PushSubscription.customer_id.in_(customer_ids),
                PushSubscription.status == "active",
            )
        )
        .scalars()
        .all()
    )
    if not subs:
        return 0

    customers = {
        c.id: c
        for c in db.execute(select(Customer).where(Customer.id.in_(customer_ids)))
        .scalars()
        .all()
    }

    for sub in subs:
        customer = customers.get(sub.customer_id) if sub.customer_id else None
        db.add(
            CampaignRecipient(
                campaign_id=campaign.id,
                customer_id=sub.customer_id,
                subscription_id=sub.id,
                customer_name=customer.name if customer else None,
                customer_phone=customer.phone if customer else None,
                # السطر ده للإشعار بس. نفس العميل في حملة `both`
                # هيبقى ليه سطر تاني مستقل بيبدأ من `whatsapp`.
                channel=CHANNEL_PUSH,
                status="pending",
            )
        )
    db.flush()
    return len(subs)


def _result_to_status(result: push.SendResult) -> tuple[str, Optional[str]]:
    """يترجم نتيجة `app.push` لحالة السطر.

    الفصل ده مقصود: `app.push` بيفهم المزوّد، والموديول ده بيفهم
    التقرير. لو `app.push` غيّر معنى `status`، التغيير هنا بس.
    """
    if result.sent:
        return "sent", None
    if result.status == "skipped":
        return "skipped", "الاشتراك مش نشط دلوقتي"
    if result.endpoint_invalid:
        return "invalid", "المزوّد رفض الـendpoint"
    return "failed", (result.error or "فشل الإرسال")[:300]


def _params_for_campaign(campaign: Campaign) -> AudienceParams:
    """يقرأ الـparams اللي الحملة اتبنت عليها.

    **مصدر الحقيقة هو العمود، مش الـargument.** لو `send_campaign`
    اشتغلت بالـparams اللي جايلة في الـcall، الأدمن يقدر يعدّل
    المعاينة ويضغط إرسال من غير ما يسجّل — ويرجع نفس الفجوة اللي
    العمود ده اتضاف عشانها.

    `{}` (الحملة القديمة أو اللي اتعملت من غير معاملات) بيرجّع
    الـdefaults بالظبط زي ما كانت.
    """
    stored = campaign.audience_params or {}
    return parse_params(
        recently_active_days=stored.get("recently_active_days"),
        inactive_days=stored.get("inactive_days"),
        high_spending_minimum=stored.get("high_spending_minimum"),
        high_spending_multiplier=stored.get("high_spending_multiplier"),
    )


def send_campaign(
    db: Session,
    campaign: Campaign,
    *,
    params: Optional[AudienceParams] = None,
    chunk_size: int = SEND_CHUNK_SIZE,
    whatsapp_provider=None,
    whatsapp_config=None,
) -> Campaign:
    """ينفّذ الحملة. **متزامن**، بيتقطع كل `chunk_size` رسالة.

    ## ليه بيبدأ بـ`status='sending'` + commit

    عشان لو حد تاني فتح الصفحة في نص الإرسال يشوف "قيد الإرسال"
    مش "مسودة" ويقدر يدوس إيقاف. من غير الـcommit الأول، الـupdate
    مايبانش لحد transaction تاني.

    ## `params` argument

    اختياري و**للاختبارات بس**. من غيره بنقرأ من
    `campaign.audience_params` — نفس القيم اللي شافتها الأدمن في
    المعاينة. لو حد مرر `params` صراحة، بنستخدمه عشان نختبر حالة
    معيّنة بدون ما نعدّل في الداتابيز.

    ## `whatsapp_provider` / `whatsapp_config`

    حقن للمزوّد والإعدادات — **للاختبارات بس**، عشان نختبر
    الإرسال من غير Evolution حقيقي. من غيرهم بنقرأ من الـDB
    والبيئة زي ما الأدمن ظبّط.

    ## القنوات المستقلة

    حملة `both` = **مرحلتين في نفس النداء**: إشعار، وبعدين
    واتساب. كل قناة لها سطورها في `campaign_recipients` (عمود
    `channel`)، ونتائجها بتتخمّد في عدّادات الحملة. لو قناة
    فشلت، التانية **بتكمل** — مزوّد واتساب واقع مش معناه إن
    إشعارات العميل ماتت.

    ## ليه فحص جاهزية واتساب **قبل** `sending`

    لو المزوّد مش مظبوط، الأدمن دوس إرسال، والحملة اتعلقت في
    `sending` لحد ما حد يدخل يدوي ويظبطها. الفحص قبل تغيير
    الحالة بيخلي الرفض مالوش أثر: الحملة فضلت `draft` والأدمن
    شاف رسالة "ظبّط الإعدادات" ورجع للوحة.
    """
    if campaign.status not in SENDABLE_FROM:
        raise CampaignError(
            f"الحملة في حالة {campaign.status} — مش هتتنفّذ تاني"
        )
    if not is_executable(campaign.channel):
        raise ChannelNotReady(f"قناة مش معروفة: {campaign.channel}")

    base_params = params if params is not None else _params_for_campaign(campaign)
    channels = _channels_for(campaign.channel)

    # فحص الجاهزية **قبل** `sending` — شوف الـdocstring.
    whatsapp_service = None
    if CHANNEL_WHATSAPP in channels:
        whatsapp_service = _require_whatsapp_service(db, whatsapp_config)

    campaign.status = STATUS_SENDING
    campaign.started_at = _now_utc()
    campaign.sent_count = 0
    campaign.failed_count = 0
    campaign.invalid_count = 0
    campaign.total_recipients = 0
    db.commit()

    totals = _Counters()
    cancelled_mid_flight = False

    for channel in channels:
        try:
            if channel == CHANNEL_PUSH:
                chunk = _run_push_channel(db, campaign, base_params, chunk_size)
            else:
                chunk = _run_whatsapp_channel(
                    db,
                    campaign,
                    base_params,
                    whatsapp_service,
                    provider=whatsapp_provider,
                    config=whatsapp_config,
                )
        except Exception:
            db.rollback()
            campaign = db.get(Campaign, campaign.id)
            if campaign is not None:
                campaign.status = STATUS_DRAFT
                campaign.started_at = None
                db.commit()
            logger.exception(
                "campaign_channel_failed",
                extra={"campaign_id": campaign.id if campaign else None, "channel": channel},
            )
            raise
        totals.add(chunk)
        _write_counters(db, campaign, totals)
        db.commit()
        if chunk.cancelled:
            cancelled_mid_flight = True
            break

    return _finalize(
        db,
        campaign,
        totals.sent,
        totals.failed,
        totals.invalid,
        totals.skipped,
        cancelled_mid_flight,
    )


def _channels_for(channel: str) -> List[str]:
    """ترتيب التنفيذ. `push` الأول عمداً.

    الإشعار أرخص وأسرع، فلو الحملة اتلغت في نصها يبقى العميل
    وصله إشعار قبل ما يتعب نفسه في رسالة واتساب.
    """
    if channel == CHANNEL_BOTH:
        return [CHANNEL_PUSH, CHANNEL_WHATSAPP]
    return [channel]


def _require_whatsapp_service(db: Session, config=None):
    """يجهّز خدمة واتساب أو بيرمي `ChannelNotReady` برسالة مفهومة.

    الاستيراد جوه الدالة مش عشان الدالة بطيئة — عشان
    `app.whatsapp.service` بيستورد `app.marketing` (للـconstants)،
    فالاستيراد وقت التحميل كان هيعمل دورة.
    """
    from app.whatsapp import base as wa_base
    from app.whatsapp import service as wa_service

    try:
        if config is None:
            config = wa_service.load_config(db)
        config.assert_sendable()
    except wa_base.WhatsAppConfigurationError as exc:
        raise ChannelNotReady(f"إعدادات واتساب ناقصة: {exc}") from exc
    return wa_service


@dataclass
class _Counters:
    """عدّادات مرحلة واحدة من الحملة.

    الـdataclass مش زينة: هي عشان `totals.add(chunk)` بدل سلسلة
    `sent += chunk.sent` بتتنسى لو قناة جديدة اتضافت بكرة.
    """

    processed: int = 0
    sent: int = 0
    failed: int = 0
    invalid: int = 0
    skipped: int = 0
    cancelled: bool = False

    def add(self, other: "_Counters") -> None:
        self.processed += other.processed
        self.sent += other.sent
        self.failed += other.failed
        self.invalid += other.invalid
        self.skipped += other.skipped
        self.cancelled = self.cancelled or other.cancelled


def _run_push_channel(
    db: Session,
    campaign: Campaign,
    base_params: AudienceParams,
    chunk_size: int,
) -> _Counters:
    """مرحلة الإشعارات. بترجّع العدّادات بتاعتها بس — مش بتلمس
    حالة الحملة النهائية."""
    send_params = base_params.evolve(require_push_subscription=True)
    counters = _Counters()
    recipient_count = _recipient_rows_for(db, campaign, send_params)
    if recipient_count == 0:
        return counters

    payload = build_payload(campaign)
    chunk_size = max(1, int(chunk_size))
    subscription_cache: dict[int, PushSubscription] = {}

    while True:
        # شريحة: هنلفّ على الـpending بس. اللي خلصناه اتغيّر حالته،
        # فمش هيبان تاني. الفلتر على `channel` مهم: حملة `both`
        # فيها سطور واتساب مستنية، دي مش بتاعة المرحلة دي.
        pending = db.execute(
            select(CampaignRecipient)
            .where(
                CampaignRecipient.campaign_id == campaign.id,
                CampaignRecipient.channel == CHANNEL_PUSH,
                CampaignRecipient.status == "pending",
            )
            .order_by(CampaignRecipient.id)
            .limit(chunk_size)
        ).scalars().all()
        if not pending:
            break

        for recipient in pending:
            sub = subscription_cache.get(recipient.subscription_id or -1)
            if sub is None and recipient.subscription_id:
                sub = db.get(PushSubscription, recipient.subscription_id)
                if sub is not None:
                    subscription_cache[recipient.subscription_id] = sub

            if sub is None:
                # الاشتراك اتمسح (cascade) أو مش موجود. مش فشل
                # إرسال — مفيش حاجة نبعتها.
                recipient.status = "skipped"
                recipient.error = "الاشتراك بقى مش موجود"
                counters.skipped += 1
                counters.processed += 1
                continue

            try:
                result = push.send_to_subscription(
                    db, sub, payload=payload, audience=AUDIENCE_MARKETING
                )
                recipient.status, recipient.error = _result_to_status(result)
                recipient.response_code = result.response_code
                if result.sent:
                    recipient.sent_at = _now_utc()
                    counters.sent += 1
                elif recipient.status == "skipped":
                    counters.skipped += 1
                elif recipient.status == "invalid":
                    counters.invalid += 1
                else:
                    counters.failed += 1
            except Exception:
                # `send_to_subscription` متعاقد إنه ما بيرميش. لو
                # رمي، يبقى فيه bug — سطر واحد ميت، والباقي عايش.
                logger.exception(
                    "campaign_send_raised",
                    extra={"campaign_id": campaign.id, "recipient_id": recipient.id},
                )
                recipient.status = "failed"
                recipient.error = "خطأ داخلي وقت الإرسال"
                counters.failed += 1
            counters.processed += 1

        # commit الشريحة + مراجعة الإلغاء. لو الأدمن دوس "إيقاف"
        # من تاب تاني، الحالة بقت `cancelled` في الداتابيز وده
        # اللي بنقراه.
        db.commit()
        if _refresh_status(db, campaign) == STATUS_CANCELLED:
            counters.cancelled = True
            break

    return counters


def _run_whatsapp_channel(
    db: Session,
    campaign: Campaign,
    base_params: AudienceParams,
    whatsapp_service,
    *,
    provider=None,
    config=None,
) -> _Counters:
    """مرحلة واتساب. بترجّع العدّادات بتاعتها بس.

    **الخدمة بتعمل `db.refresh` جوّاها**: الإلغاء لازم يشتغل زي
    ما بيشتغل مع الـpush. الخدمة بترجّع `cancelled` لو الحالة
    اتغيّرت في تاب تاني، والعدّادات اللي رجعت بتkon اللي اتنفّذ.
    """
    import asyncio

    counters = _Counters()
    summary = whatsapp_service.enqueue_recipients(db, campaign, base_params)
    counters.invalid = summary.invalid
    counters.skipped = summary.opted_out
    if summary.queued == 0:
        db.commit()
        return counters

    result = asyncio.run(
        whatsapp_service.run_campaign(db, campaign, provider=provider, config=config)
    )
    counters.sent = result.sent
    counters.failed = result.failed
    counters.invalid += result.invalid
    counters.skipped += result.skipped
    counters.processed = counters.sent + counters.failed + counters.invalid + counters.skipped
    counters.cancelled = result.cancelled
    return counters


def _write_counters(
    db: Session,
    campaign: Campaign,
    totals: _Counters,
) -> None:
    """يكتب العدادات المجمّعة على الحملة.

    بتاخد `_Counters` مش ست أرقام، لأن الحملة ممكن يكون ليها
    مرحلتين (إشعار وواتساب) وكل واحدة بتجيب أرقامها. لو الدالة
    كانت ست positional parameters، إضافة قناة جديدة معناها نسيان
    تعديل النداء.

    `processed` هو مجموع اللي اتعاملنا معاهم فعلاً — مش عدد
    السطور في الطابور. الفرق مهم: لو عميل رفض الإذن بين
    التجهيز والإرسال، السطر مش هيتحسب مرتين.

    ملاحظة: `invalid` **مش جزء** من `failed` في التقرير العام،
    بس بيتحسب في `total` عشان يبقى مفهوم: "1200 بعت، 30 رفض
    المزوّد، 5 فشلوا" هيبقى أوضح من "1200 بعت، 35 فشلوا".
    """
    campaign.sent_count = totals.sent
    campaign.failed_count = totals.failed
    campaign.invalid_count = totals.invalid
    campaign.total_recipients = totals.processed
    db.flush()


def _finalize(
    db: Session,
    campaign: Campaign,
    sent: int,
    failed: int,
    invalid: int,
    skipped: int,
    cancelled: bool,
) -> Campaign:
    """يحدّد الحالة النهائية ويكمّل الأرقام.

    `skipped` بيتحسب ضمن "اللي ما اتبعتش" عشان التقرير ميكذبش:
    حملة 1000 مستلم و700 `skipped` معناها إن 300 وصلوا، مش 1000.
    """
    attempted = sent + failed + invalid + skipped
    if not cancelled:
        campaign.total_recipients = attempted
        if sent == 0:
            campaign.status = STATUS_FAILED
        elif failed or invalid or skipped:
            campaign.status = STATUS_PARTIAL
        else:
            campaign.status = STATUS_COMPLETED
    else:
        # الإلغاء: اللي اتبعت اتبعت. بنثبت الأرقام زي ما هي
        # ونسيب `total_recipients` زي ما اتحسب لحظة الإيقاف.
        campaign.status = STATUS_CANCELLED
        campaign.cancelled_at = campaign.cancelled_at or _now_utc()
    campaign.completed_at = _now_utc()
    db.commit()
    db.refresh(campaign)
    return campaign


# ============================================================
# الجدولة (lazy tick)
# ============================================================


def due_campaigns(db: Session, limit: int = 5) -> List[Campaign]:
    """الحملات المستحقة دلوقتي.

    `limit` افتراضي 5: لو 50 حملة مستحقة مع بعض، الأدمن فتح
#: الصفحة مرة واحدة مش هيبعت 2500 رسالة. الباقي الجاية مع
    فتح بعده.
    """
    now = _now_utc()
    return (
        db.execute(
            select(Campaign)
            .where(
                Campaign.status == STATUS_SCHEDULED,
                Campaign.scheduled_at.isnot(None),
                Campaign.scheduled_at <= now,
            )
            .order_by(Campaign.scheduled_at)
            .limit(limit)
        )
        .scalars()
        .all()
    )


def run_due_campaigns(db: Session, admin_id: Optional[int] = None) -> List[Campaign]:
    """ينفّذ المستحق. بيتنادى من صفحة الحملات.

    ## ليه نستثني الفاشلة

    حملة واتساب مجدولة هتفشل بـ`ChannelNotReady` في كل tick لو ما
    استثنيناهاش. ده_expected وماينفعش يبقى error في اللوج.
    بنرجعها بحالتها وخلاص.
    """
    ran: List[Campaign] = []
    for campaign in due_campaigns(db):
        if not is_executable(campaign.channel):
            continue
        try:
            ran.append(send_campaign(db, campaign))
        except CampaignError:
            logger.info(
                "campaign_due_skipped",
                extra={"campaign_id": campaign.id, "channel": campaign.channel},
            )
        except Exception:
            logger.exception("campaign_due_failed", extra={"campaign_id": campaign.id})
            db.rollback()
    return ran


# ============================================================
# السجل
# ============================================================


@dataclass
class HistoryFilters:
    status: Optional[str] = None
    channel: Optional[str] = None
    q: Optional[str] = None
    page: int = 1
    per_page: int = 25


def campaign_history(
    db: Session, filters: Optional[HistoryFilters] = None
) -> tuple[List[Campaign], int]:
    """الحملات + العدد الكلي. مرتّبة بالأحدث.

    `campaigns.created_at` عليه index، فالترتيب ده index scan
    مش sort. الـcount بيبقى على نفس الـWHERE — مفيش query تاني.
    """
    filters = filters or HistoryFilters()
    page = max(1, filters.page)
    per_page = min(100, max(1, filters.per_page))

    statement = select(Campaign)
    count_statement = select(func.count(Campaign.id))
    if filters.status:
        statement = statement.where(Campaign.status == filters.status)
        count_statement = count_statement.where(Campaign.status == filters.status)
    if filters.channel:
        statement = statement.where(Campaign.channel == filters.channel)
        count_statement = count_statement.where(Campaign.channel == filters.channel)
    if filters.q:
        pattern = f"%{filters.q.strip()}%"
        statement = statement.where(
            Campaign.name.ilike(pattern) | Campaign.title.ilike(pattern)
        )
        count_statement = count_statement.where(
            Campaign.name.ilike(pattern) | Campaign.title.ilike(pattern)
        )

    total = int(db.scalar(count_statement) or 0)
    rows = (
        db.execute(
            statement.order_by(Campaign.created_at.desc(), Campaign.id.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
        .scalars()
        .all()
    )
    return list(rows), total


def recipient_breakdown(db: Session, campaign_id: int) -> dict[str, int]:
    """عدد كل حالة في كولڤ واحد — لتقرير الحملة.

    استعلام واحد بـ`GROUP BY` مش 5 counts.
    """
    rows = db.execute(
        select(CampaignRecipient.status, func.count(CampaignRecipient.id))
        .where(CampaignRecipient.campaign_id == campaign_id)
        .group_by(CampaignRecipient.status)
    ).all()
    counts = {status: 0 for status in ("pending", "sent", "failed", "skipped", "invalid")}
    for status, count in rows:
        counts[status] = int(count)
    return counts


def recipient_breakdown_by_channel(
    db: Session, campaign_id: int
) -> dict[str, dict[str, int]]:
    """نفس العدّاد بس مقسوم على القنوات.

    حملة `both` فيها سطر لكل قناة لنفس العميل. رقم واحد مخلوط
    بيقول "20 رسالة اتبعتت" والأدمن مش عارف 12 دي إشعارات ولا
    8 واتساب — والتقرير المضلّل ده أسوأ من تقرير ناقص.
    """
    from app.whatsapp import service as whatsapp_service

    return whatsapp_service.counts_by_channel(db, campaign_id)


def cancelled_recipients(db: Session, campaign: Campaign) -> int:
    """عدد السطور اللي زر الإيقاف مسكها قبل ما تتبعت.

    **مش حالة سطر.** الإلغاء بيوقّف التشغيل ويسيب الباقي `pending`
    عن قصد — السطر ده يفضل قابل للاستئناف. فالرقم بيتحسب من حالة
    الحملة مش من عمود: حملة ملغاة فيها 30 سطر `pending` معناها 30
    رسالة اتلغى إرسالها، وحملة شغالة فيها 30 `pending` معناها 30
    لسه ما وصلتش الدور لها. نفس قاعدة البيانات، معنيين مختلفين.
    """
    if campaign.status != STATUS_CANCELLED:
        return 0
    return int(
        db.execute(
            select(func.count(CampaignRecipient.id)).where(
                CampaignRecipient.campaign_id == campaign.id,
                CampaignRecipient.status == "pending",
            )
        ).scalar_one()
    )


def recipient_attempts(db: Session, campaign_id: int, limit: int = 50):
    """آخر محاولات واتساب للحملة، وسطر العميل بتاع كل واحدة.

    **ليه دي مش `whatsapp_attempts` لوحدها**: السطر نفسه بيقول
    "المحاولة التلاتة رجعت 429"، مش مين. الأدمن محتاج يربطها
    بالعميل وبالقناة عشان يقرر: أعمل إعادة لكل الحملة، ولا أوقف
    المزوّد، ولا العميل ده مش على واتساب أصلاً.

    الأحدث أولاً + سقف — حملة 20 ألف عميل ممكن سجلّها 60 ألف سطر،
    والصفحة دي تقرير مش تنزيل.

    **اللي مش بيتعرض**: `WhatsAppAttempt.raw`. العمود ده رد المزوّد
    المختصر وممكن يبان فيه اللي ما لهاش لازمة. سبب المحاولة
    (`error`) متقصوص ومتنضّف في المزوّد وهو كفاية.
    """
    return (
        db.execute(
            select(WhatsAppAttempt)
            .options(joinedload(WhatsAppAttempt.recipient))
            .where(WhatsAppAttempt.campaign_id == campaign_id)
            .order_by(WhatsAppAttempt.id.desc())
            .limit(limit)
        )
        .scalars()
        .unique()
        .all()
    )


def failed_recipients(db: Session, campaign_id: int, limit: int = 50):
    """أول الـfailures مع سبب كل واحد — عشان الأدمن يفهم المشكلة."""
    return (
        db.execute(
            select(CampaignRecipient)
            .where(
                CampaignRecipient.campaign_id == campaign_id,
                CampaignRecipient.status.in_(("failed", "invalid", "skipped")),
            )
            .order_by(CampaignRecipient.id)
            .limit(limit)
        )
        .scalars()
        .all()
    )


# ============================================================
# الرفض
# ============================================================


def set_marketing_opt_out(
    db: Session, customer: Customer, opt_out: bool
) -> Customer:
    """يغيّر موافقة العميل.

    ملاحظة على التصميم: ده **مش** بيلغي اشتراك الـpush. العميل
    ممكن يكون عايز "طلبك في الطريق" (transaction) بس مش عايز عروض.
    إشعار العروض خالص يُقفله العميل نفسه من
    إعدادات المتصفح أو زرار الإشعارات — ده شغله.
    وده نفس الحقل في `Customer.marketing_opt_out`.
    """
    customer.marketing_opt_out = opt_out
    customer.marketing_opt_out_at = _now_utc() if opt_out else None
    db.commit()
    db.refresh(customer)
    return customer
