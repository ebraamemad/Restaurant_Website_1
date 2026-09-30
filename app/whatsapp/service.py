"""تشغيل حملة واتساب: تحديد المستلمين، الطابور، الإرسال، الإلغاء.

## حدود هذا الملف

هذا الملف **مش بيفتكر في HTTP**. بيشتغل على `WhatsAppProvider` بس
(العقد في `base.py`، التنفيذ في `evolution.py`). لو احنا غيّرنا
المزوّد، كل سطور الإرسال في المشروع هتفضل زي ما هي.

الحاجات اللي الملف ده مسؤول عنها:

- تجهيز سطور الطابور من الجمهور (`enqueue_recipients`)
- التنفيذ مع فاصل زمني و.commit على دفعات (`run_campaign`)
- إعادة المحاولة المحدودة، وتسجيل كل محاولة
- احترام الإلغاء واحترام رفض الإذن وقت الإرسال
- إنهاء الحملة بالأرقام الصح

## ليه `asyncio` جوه مشروع sync

المزوّد async (httpx)، وحملة 5000 رسالة بفاصل ثانيتين = 3 ساعات
من الذاكرة لو الـloop كان متجمد بـ`time.sleep`. الـdatabase
operations **لسه sync** — مش هنعمل `AsyncSession` في Phase 5، ده
تغيير كبير من غير داعي. فالإرسال async، والجلسة sync، والنادي من
`marketing.send_campaign` بيعمل `asyncio.run`.

## اللي الكود ده **مش** بيعمله

مفيش فاصل زمني، ولا حجم دفعة، ولا احترام `429` بيضمن إن رقمك مش
هيتحظر. مفيش حاجة في الكود بتضمن ده — مفيش حاجة في WhatsApp API
بتقدر. `Evolution API` تكامل غير رسمي، ومخاطرة الحظر قايمة. اللي
بنعمله: إرسال متباعد، opt-out محترم، log كامل، وإلغاء فوري. ده بيقلّل
المخاطرة، مش بيلغيها. لو محتاج ضمان، استخدم مزوّد رسمي.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional, Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Campaign, CampaignRecipient, Customer, WhatsAppAttempt
from app.utils import is_valid_egypt_phone, normalize_phone

from .base import (
    ConnectionStatus,
    SendOutcome,
    SendState,
    WhatsAppConfigurationError,
    WhatsAppMessage,
    WhatsAppProvider,
)
from .evolution import EvolutionProvider
from .settings import WhatsAppConfig, load_config

logger = logging.getLogger(__name__)

#: بادئة مصر للرقم international. المشروع كله أرقام مصرية
#: (`app.utils.is_valid_egypt_phone` بتفرض كده)، فالتحويل ثابت.
#: لو القناة بقت لدولة تانية، ده أول مكان هيتغيّر.
DIALABLE_PREFIX = "20"

#: سقف زمني لتشغيل الحملة الواحدة. بعده بنوقف ونسيب الباقي
#: `pending` — الحملة تبقى `sending` والأدمن يقدر يكملها. بدون
#: السقف ده، مزوّد بيرجّع `429` على كل رسالة = حملة ما بتخلصش.
MAX_RUNTIME_SECONDS = 60 * 60

#: سقف_backoff للمحاولة الواحدة. التكرار: 2، 4، 8، 16، 30، 30…
MAX_BACKOFF_SECONDS = 30.0

#: statuses اللي معناها "خلصنا، ماتتعملش تانية".
TERMINAL_STATUSES = frozenset({"sent", "failed", "skipped", "invalid"})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def to_dialable(raw_phone: Optional[str]) -> str:
    """الرقم بالشكل اللي المزوّد بيفهمه: `201012345678`.

    بيرجّع نص فاضي لو الرقم مش موبايل مصري — **مفيش تخمين ولا
    محاولة تانية**. رقم غلط في الطابور = رسالة ما بتوصلش،
    والأدمن محتاج يشوف السبب.
    """
    normalized = normalize_phone(raw_phone or "")
    if not is_valid_egypt_phone(normalized):
        return ""
    return f"{DIALABLE_PREFIX}{normalized}"


def build_provider(config: WhatsAppConfig) -> WhatsAppProvider:
    """اختيار المزوّد من `config.provider`.

    **Unknown provider بيرمي** — مينفعش نرجع `None` أو نستخدم
    Evolution كـfallback. مزوّد واحد مش متعرف عليه = خطأ إعداد
    يفضّل يظهر في اللوحة على إننا نرسل ببروتوكول غلط.
    """
    name = (config.provider or "").strip().lower()
    if name == "evolution":
        return EvolutionProvider(config)
    raise WhatsAppConfigurationError(f"مزوّد غير معروف: {name or '(فاضي)'}")


def provider_for(db: Session) -> tuple[WhatsAppConfig, WhatsAppProvider]:
    """الإعدادات + المزوّد من نفس القراءة. بيرمي لو الإعدادات ناقصة."""
    config = load_config(db)
    config.assert_sendable()
    return config, build_provider(config)


# ============================================================
# اختبار الاتصال والرسالة
# ============================================================


async def check_connection(db: Session) -> ConnectionStatus:
    """يفحص الاتصال. **ما بيرميش أبدًا** — النتيجة بتتعرض للمستخدم.

    `load_config` ممكن يرمي (`validate_base_url`)، وده هو **النتيجة نها** — مش
    استثناء من القاعدة. بنحوّله لـ`ERROR` مع
    الرسالة، عشان صفحة الإعدادات تقدر تعرضها بدل 500.
    """
    try:
        config = load_config(db)
        provider = build_provider(config)
    except WhatsAppConfigurationError as exc:
        return ConnectionStatus(state=_error_state(), message=str(exc), provider="evolution")
    except Exception:  # noqa: BLE001 — الواجهة بتعرض النتيجة مش الرمي
        logger.exception("whatsapp_connection_setup_failed")
        return ConnectionStatus(
            state=_error_state(), message="فشل تجهيز الاتصال بالمزوّد", provider="evolution"
        )

    try:
        return await provider.test_connection()
    finally:
        await provider.aclose()


def _error_state():
    from .base import ConnectionState

    return ConnectionState.ERROR


async def send_test_message(
    db: Session,
    to: str,
    body: str,
) -> SendOutcome:
    """رسالة واحدة لرقم واحد. **مفيش سطر في الداتابيز.**

    رسالة الاختبار مش حملة: ليها مفيش `campaign_id`، ومفيش تقرير
    ليها. لو العدد كبير جدًا، الـbody بتاعها لازم يبقى قصير —
    الـbody بيتخزّن في المزوّد.

    **الرقم لازم يكون للمتجر نفسه**: الأدمن بيكتب رقمه في
    `WHATSAPP_TEST_PHONE`، والـUI بيعرض القناع بتاعه.
    """
    config = load_config(db)
    config.assert_sendable()
    dialable = to_dialable(to)
    if not dialable:
        raise WhatsAppConfigurationError("رقم رسالة الاختبار مش رقم موبايل مصري صحيح")
    provider = build_provider(config)
    try:
        return await provider.send_text(
            WhatsAppMessage(to=dialable, body=(body or "").strip()[:1000], reference=None)
        )
    finally:
        await provider.aclose()


# ============================================================
# تجهيز الطابور
# ============================================================


@dataclass
class QueueSummary:
    """نتيجة التجهيز. الأرقام دي بتروح للتقرير."""

    queued: int = 0
    invalid: int = 0
    opted_out: int = 0
    duplicate: int = 0


def enqueue_recipients(
    db: Session,
    campaign: Campaign,
    params,
) -> QueueSummary:
    """يبني سطور واتساب من الجمهور. **مش بيبعت.**

    ## ليه مختلف عن `_recipient_rows_for` بتاع الـpush

    الـpush بيفتكر في `PushSubscription` (عميل بجهازين = سطرين).
    واتساب بيفتكر في **الرقم**: عميل واحد = رسالة واحدة. وكمان
    واتساب مش محتاج اشتراك — العميل اللي عنده طلب قبل سنة
    ورقمه محفوظ هو بالظبط الجمهور.

    ## ليه `marketing_opt_out` بيتشيك هنا

    مش بس وقت الإرسال. لو العميل رفض بعد ما اتحسب الجمهور،
    السطر بيروح `skipped` والسبب مكتوب — بدل ما التقرير يقول
    "فشل" لعميل رفض يوصله.
    """
    from app.marketing import MAX_RECIPIENTS
    from app.segments import audience_customer_ids

    customer_ids = audience_customer_ids(
        db,
        campaign.audience_type,
        campaign.segment_key,
        params.evolve(respect_opt_out=False, require_push_subscription=False),
        limit=MAX_RECIPIENTS + 1,
    )
    summary = QueueSummary()
    if not customer_ids:
        return summary
    if len(customer_ids) > MAX_RECIPIENTS:
        # نفس قاعدة الـpush: جبينا `MAX+1`، فلو رجعوا كلهم يبقى
        # فيه أكتر من الحد. الرفض هنا أحسن من حملة مالهاش أولوية
        # قانونية عند المزوّد، وأحسن من بومبة في الذاكرة.
        from app.marketing import CampaignError

        raise CampaignError(
            f"الجمهور أكبر من الحد المسموح ({MAX_RECIPIENTS}). "
            "قسّم الشريحة أو ارفع الحد في الكود."
        )

    # إعادة استدعاء `enqueue_recipients` على نفس الحملة (إعادة
    # تشغيل، أو double-click) ماينفعش يضاعف السطور: العميل
    # الواحد يفضل رسالة واحدة. المكرر بيروح في `duplicate`.
    already_queued = set(
        db.execute(
            select(CampaignRecipient.customer_id).where(
                CampaignRecipient.campaign_id == campaign.id,
                CampaignRecipient.channel == "whatsapp",
            )
        )
        .scalars()
        .all()
    )

    customers = (
        db.execute(select(Customer).where(Customer.id.in_(customer_ids)))
        .scalars()
        .all()
    )

    seen_customers: set[int] = set()
    for customer in customers:
        if customer.id in seen_customers:
            summary.duplicate += 1
            continue
        seen_customers.add(customer.id)
        if customer.id in already_queued:
            summary.duplicate += 1
            continue

        dialable = to_dialable(customer.phone)
        row = CampaignRecipient(
            campaign_id=campaign.id,
            customer_id=customer.id,
            customer_name=customer.name,
            customer_phone=dialable or customer.phone,
            channel="whatsapp",
            status="pending",
        )
        if customer.marketing_opt_out:
            row.status = "skipped"
            row.error = "العميل رفض الرسائل التسويقية"
            summary.opted_out += 1
        elif not dialable:
            row.status = "invalid"
            row.error = "رقم الموبايل مش صحيح"
            summary.invalid += 1
        else:
            summary.queued += 1
        db.add(row)

    db.flush()
    return summary


# ============================================================
# التنفيذ
# ============================================================


@dataclass
class RunResult:
    sent: int = 0
    failed: int = 0
    invalid: int = 0
    skipped: int = 0
    #: سطور لسه `pending` لما خلص الوقت — محتاجة تشغيل تاني.
    pending: int = 0
    cancelled: bool = False
    stopped_reason: str = ""
    per_channel: dict[str, dict[str, int]] = field(default_factory=dict)


class _Sleeper(Protocol):
    async def __call__(self, seconds: float) -> None: ...


async def _noop_sleep(seconds: float) -> None:
    """نامي فعلاً. الاختبارات بتحطّ ده مكانه."""
    await asyncio.sleep(seconds)


def _backoff_seconds(attempt: int) -> float:
    """تأخير متصاعد ومحدود.

    `2 ** (attempt-1)` مع سقف 30 ثانية. السقف مهم: من غيره
    المحاولة السادسة هتنام 32 ثانية، والحملة بتتأخر ساعة على
    رسالة واحدة صعبة.
    """
    if attempt <= 1:
        return 1.0
    return float(min(2 ** (attempt - 1), int(MAX_BACKOFF_SECONDS)))


async def run_campaign(
    db: Session,
    campaign: Campaign,
    *,
    provider: Optional[WhatsAppProvider] = None,
    config: Optional[WhatsAppConfig] = None,
    max_runtime_seconds: float = MAX_RUNTIME_SECONDS,
    sleep: _Sleeper = _noop_sleep,
    batch_size: Optional[int] = None,
) -> RunResult:
    """ينفّذ حملة واتساب لحد ما تخلص أو تتفلغ أو يخلص الوقت.

    **السلوك:**
    - بيبعت شريحة شريحة، وبيعمل `commit` بعد كل شريحة — الإلغاء
      بيشتغل من أي تاب تاني.
    - الفاصل بين الرسايل من `config.message_interval_ms`.
    - الفشل المؤقت بيتعاد لحد `max_retries`، وكل محاولة سطر في
      `whatsapp_attempts`.
    - لو العميل رفض بعد التجهيز، السطر بيروح `skipped` **قبل** أي
      اتصال بالمزوّد. رفض الإذن مش بند نكلّم عنه المزوّد أصلاً.
    - **ما بيرميش** — يرجّع `RunResult` وفيه سبب الوقوف.
    """
    from app.marketing import STATUS_CANCELLED

    owns_provider = provider is None
    if config is None:
        config = load_config(db)
    if provider is None:
        config.assert_sendable()
        provider = build_provider(config)

    size = int(batch_size or config.batch_size)
    size = max(1, min(size, 100))
    result = RunResult()
    deadline = time.monotonic() + max(0.0, float(max_runtime_seconds))
    body = (campaign.message or "").strip()
    interval = config.interval_seconds

    # شريط تقدّم في اللوج: سطر "بدأ" وسطر "خلص". سطر في كل رسالة
    # كان هيبقى 5000 سطر لوج لحملة واحدة، مش معلومة.
    logger.info(
        "whatsapp_campaign_start campaign_id=%s channel=%s provider=%s",
        campaign.id,
        campaign.channel,
        provider.name,
    )

    try:
        while True:
            db.refresh(campaign)
            if campaign.status == STATUS_CANCELLED:
                result.cancelled = True
                result.stopped_reason = "أوقفها الأدمن"
                break
            if time.monotonic() >= deadline:
                result.stopped_reason = "خلص الوقت المخصص للتشغيل"
                break
            if not body:
                result.stopped_reason = "نص الرسالة فاضي"
                break

            due = _due_rows(db, campaign.id, size)
            if not due:
                break

            for recipient in due:
                if time.monotonic() >= deadline:
                    result.stopped_reason = "خلص الوقت المخصص للتشغيل"
                    break
                await _process_row(
                    db,
                    campaign,
                    recipient,
                    provider=provider,
                    body=body,
                    config=config,
                    result=result,
                    sleep=sleep,
                    is_cancelled=lambda: _is_cancelled(db, campaign),
                )
                if interval > 0:
                    await sleep(interval)

            db.commit()
            db.refresh(campaign)
            if result.stopped_reason:
                break
    finally:
        if owns_provider:
            await provider.aclose()

    result.pending = _count_pending(db, campaign.id)
    logger.info(
        "whatsapp_campaign_end campaign_id=%s sent=%s failed=%s invalid=%s "
        "skipped=%s pending=%s reason=%s",
        campaign.id,
        result.sent,
        result.failed,
        result.invalid,
        result.skipped,
        result.pending,
        result.stopped_reason or "-",
    )
    return result


async def _process_row(
    db: Session,
    campaign: Campaign,
    recipient: CampaignRecipient,
    *,
    provider: WhatsAppProvider,
    body: str,
    config: WhatsAppConfig,
    result: RunResult,
    sleep: _Sleeper,
    is_cancelled: Callable[[], bool],
) -> None:
    """سطر واحد: فحص الإذن، إرسال، تسجيل، إعادة محاولة."""
    if recipient.status in TERMINAL_STATUSES:
        return

    # 1) رفض الإذن بيتشيك **قبل** الاتصال. عميل رفض الإذن مش
    #    بند نكلّم عنه المزوّد بيه.
    if _opted_out(db, recipient.customer_id):
        recipient.status = "skipped"
        recipient.error = "العميل رفض الرسائل التسويقية"
        db.flush()
        result.skipped += 1
        return

    dialable = to_dialable(recipient.customer_phone) or recipient.customer_phone
    if not dialable:
        recipient.status = "invalid"
        recipient.error = "رقم الموبايل مش صحيح"
        db.flush()
        result.invalid += 1
        return

    while True:
        if is_cancelled():
            return

        recipient.attempts = (recipient.attempts or 0) + 1
        started = time.monotonic()
        try:
            outcome = await provider.send_text(
                WhatsAppMessage(
                    to=dialable,
                    body=body,
                    reference=f"campaign-{campaign.id}-row-{recipient.id}",
                )
            )
        except Exception:  # noqa: BLE001 — العقد says ما بيرميش
            # لو رمي، يبقى فيه bug في المزوّد. سطر واحد ميت
            # والباقي عايش — زي سلوك الـpush.
            logger.exception(
                "whatsapp_send_raised campaign_id=%s recipient_id=%s",
                campaign.id,
                recipient.id,
            )
            outcome = SendOutcome(
                state=SendState.FAILED, error="خطأ داخلي وقت الإرسال"
            )
        duration_ms = int((time.monotonic() - started) * 1000)

        _record_attempt(db, recipient, provider.name, outcome, duration_ms)
        # المزوّد بيتخزّن **وقت الإرسال** على السطر. لو الأدمن غيّر
        # المزوّد بكرة، التقرير القديم يفضل يقول "ده اتبعت بـ
        # Evolution" — مش "اتغير لمزوّد تاني وبقى غلط".
        recipient.provider = provider.name[:30]
        _apply_outcome(recipient, outcome)

        if outcome.state is SendState.SENT:
            recipient.next_attempt_at = None
            result.sent += 1
            break
        if not outcome.retryable:
            result.failed += 1
            break
        if recipient.attempts > config.max_retries:
            # رصيد المحاولات خلص. السطر لازم **يخرج** من `pending`
            # هنا: `_apply_outcome` سيب السطر `pending` لأن المزوّد
            # قال "مش دلوقتي"، فلو مايشيلناش الحالة هيفضل يتفعى
            # من `_due_rows` للأبد — حلقة لا نهائية على حملة واحدة.
            recipient.status = "failed"
            recipient.error = (
                f"فشل {recipient.attempts} مرات: {outcome.error or 'خطأ مؤقت'}"
            )[:300]
            recipient.next_attempt_at = None
            result.failed += 1
            break

        # مؤقت وعندنا رصيد محاولات: بنجدول المحاولة الجاية
        # وننام. `next_attempt_at` بيتخزّن عشان لو العملية اتقفلت
        # في النص، السطر يفضل مستني ومحدش يقول "اتبعت".
        delay = _backoff_seconds(recipient.attempts)
        recipient.next_attempt_at = _now() + timedelta(seconds=delay)
        db.flush()
        logger.info(
            "whatsapp_retry_scheduled campaign_id=%s recipient_id=%s attempt=%s delay=%.1fs",
            campaign.id,
            recipient.id,
            recipient.attempts,
            delay,
        )
        await sleep(delay)


def _record_attempt(
    db: Session,
    recipient: CampaignRecipient,
    provider_name: str,
    outcome: SendOutcome,
    duration_ms: int,
) -> None:
    """سطر واحد في `whatsapp_attempts` لكل نداء مزوّد."""
    db.add(
        WhatsAppAttempt(
            recipient_id=recipient.id,
            campaign_id=recipient.campaign_id,
            attempt_number=recipient.attempts or 1,
            provider=provider_name,
            state=outcome.state.value,
            provider_status=(outcome.provider_status or None) and outcome.provider_status[:60],
            provider_message_id=(outcome.provider_message_id or None)
            and outcome.provider_message_id[:120],
            http_status=outcome.http_status,
            error=(outcome.error or None) and outcome.error[:300],
            # `raw` خلاص متنفّض في المزوّد؛ الطول سقف تاني هنا.
            raw=(outcome.raw or None) and outcome.raw[:600],
            duration_ms=duration_ms,
        )
    )


def _apply_outcome(
    recipient: CampaignRecipient,
    outcome: SendOutcome,
) -> None:
    """ينقل نتيجة المحاولة على السطر.

    `SKIPPED` المؤقت بيفضل `pending` — المزوّد قال "مش دلوقتي"،
    مش "فشل". و`SKIPPED` الدائم بيروح `skipped`.
    """
    if outcome.provider_status:
        recipient.provider_status = outcome.provider_status[:60]
    recipient.response_code = outcome.http_status

    if outcome.state is SendState.SENT:
        recipient.status = "sent"
        recipient.error = None
        recipient.sent_at = _now()
        return
    if outcome.state is SendState.SKIPPED and outcome.retryable:
        # مؤقت: السطر يفضل مستني إعادة.
        recipient.error = (outcome.error or "المزوّد طلب إعادة")[:300]
        return
    if outcome.state is SendState.SKIPPED:
        recipient.status = "skipped"
        recipient.error = (outcome.error or "المزوّد تخطّى الرسالة")[:300]
        return
    recipient.status = "failed"
    recipient.error = (outcome.error or "فشل الإرسال")[:300]


def _due_rows(db: Session, campaign_id: int, limit: int) -> list[CampaignRecipient]:
    """السطور المستحقة دلوقتي: `pending` و`next_attempt_at` فات أو NULL."""
    now = _now()
    return (
        db.execute(
            select(CampaignRecipient)
            .where(
                CampaignRecipient.campaign_id == campaign_id,
                CampaignRecipient.channel == "whatsapp",
                CampaignRecipient.status == "pending",
                (CampaignRecipient.next_attempt_at.is_(None))
                | (CampaignRecipient.next_attempt_at <= now),
            )
            .order_by(CampaignRecipient.id)
            .limit(limit)
        )
        .scalars()
        .all()
    )


def _count_pending(db: Session, campaign_id: int) -> int:
    return int(
        db.execute(
            select(func.count())
            .select_from(CampaignRecipient)
            .where(
                CampaignRecipient.campaign_id == campaign_id,
                CampaignRecipient.channel == "whatsapp",
                CampaignRecipient.status == "pending",
            )
        ).scalar_one()
    )


def _opted_out(db: Session, customer_id: Optional[int]) -> bool:
    if not customer_id:
        return False
    value = db.execute(
        select(Customer.marketing_opt_out).where(Customer.id == customer_id)
    ).scalar_one_or_none()
    return bool(value)


def _is_cancelled(db: Session, campaign: Campaign) -> bool:
    from app.marketing import STATUS_CANCELLED

    try:
        db.refresh(campaign)
    except Exception:  # noqa: BLE001 — الجلسة ممكن تكون اتقفلت، نكمل
        return False
    return campaign.status == STATUS_CANCELLED


# ============================================================
# تشغيل sync من نقطة الدخول sync
# ============================================================


def run_campaign_sync(db: Session, campaign: Campaign, **kwargs) -> RunResult:
    """غلاف sync للـendpoint. بيرجّع `RunResult` زي ما هي."""
    return asyncio.run(run_campaign(db, campaign, **kwargs))


# ============================================================
# التقرير
# ============================================================


def counts_by_channel(db: Session, campaign_id: int) -> dict[str, dict[str, int]]:
    """الأرقام لكل قناة على حدة.

    `group by channel + status` بيترجم سطرين لكل سطر `NULL` لـ
    `CampaignRecipient.status`. ده اللي بيخلّي تقرير `both` يقول
    "12 إشعار + 9 واتساب" بدل رقم مخلوط.
    """
    rows = db.execute(
        select(
            CampaignRecipient.channel,
            CampaignRecipient.status,
            func.count(),
        )
        .where(CampaignRecipient.campaign_id == campaign_id)
        .group_by(CampaignRecipient.channel, CampaignRecipient.status)
    ).all()

    out: dict[str, dict[str, int]] = {}
    for channel, status, count in rows:
        bucket = out.setdefault(channel, {})
        bucket[status] = int(count)
    for channel in ("push", "whatsapp"):
        out.setdefault(channel, {})
    return out