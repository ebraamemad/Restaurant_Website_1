"""Web Push — Phase 3.

## ليه الخدمة دي في ملف لوحدها

كل إرسال إشعار — طلب جديد للأدمن، أو تغيير حالة للعميل — لازم يمر من
هنا. السبب مش التنظيم، السبب **الإرسال نفسه بيفشل**:

- مزوّد الـpush (FCM / Mozilla / Apple) بيرجع أخطاء، وممكن يرد بعد
  timeout. أي استثناء في ده لازم **مايوقعش** الطلب ولا تغيير الحالة.
- المزوّد بيرجّع `404`/`410` لما الـendpoint يبقى مش صالح. ده مش خطأ
  في الكود، ده معلومة: لازم نوقف إرسال لهذا الجهاز.
- الendpoint ممكن يكون اتقفل من غير ما حد يعلم (المستخدم مسح الموقع،
  غيّر إعدادات المتصفح، أو عمل uninstall للـPWA).

عشان كده: `send_to_subscription` **ما بيرميش استثناءات**، وكل حاجة
بتتحفظ في `push_notifications` بدل ما تضيع.

## ليه الإرسال **متزامن** ومش طابور

الحملة التسويقية (المرحلة الجاية) هي اللي محتاجة طابور وretry وجدولة.
إشعار تغيير حالة طلب **متأخر** — لو اتأخر 5 دقايق، قيمته صفر، والعميل
بيشوف الحالة في صفحة التتبّع على أي حال. طابور هنا معناه تأخير
الإشعار في اللحظة اللي بتفرق فيها.

فالسلوك الحالي: إرسال **بعد** الـcommit، بشكل **متزامن**، مع عزل
الفشل عن العملية الأساسية. لو المزوّد بطيء، الطلب بياخد timeout
واحد (`push_send_timeout_seconds`) وخلاص.

## VAPID

`get_vapid_private_pem()` في `app.config` هي الدالة الوحيدة اللي تقرا
المفتاح الخاص، والدالة دي داخلية. **العميل ما بياخدش غير
`get_vapid_public_key_browser()`** — وهي اللي بتتحوّل لـ
`applicationServerKey`.

الفرق بين `get_vapid_public_key()` (نقطة 64 بايت، بتتخزّن في `.env`)
و`get_vapid_public_key_browser()` (نقطة 65 بايت مع `0x04`، دي اللي
المتصفح بياخدها) مقصود ومكتوب في `app/config.py`.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import (
    get_vapid_private_pem,
    get_vapid_public_key,
    get_vapid_public_key_browser,
    push_is_configured,
    settings,
)
from app.models import (
    Admin,
    Order,
    OrderStatusHistory,
    PushNotification,
    PushSubscription,
)

logger = logging.getLogger("app.push")

# audience بتاعة كل إشعار
AUDIENCE_ADMIN = "admin"
AUDIENCE_CUSTOMER = "customer"

# حالة الاشتراك
STATUS_ACTIVE = "active"
STATUS_INVALID = "invalid"

# نتيجة المحاولة في السجل
RESULT_SENT = "sent"
RESULT_FAILED = "failed"
RESULT_SKIPPED = "skipped"

# عمر الإشعار بالثواني (TTL). FCM بيرفض الحمولات الكبيرة،
# وشكوى "الرسالة طويلة" من أدمن أسوأ من رسالة مقصوصة.
MAX_TTL_SECONDS = 86400


# ============================================================
# VAPID
# ============================================================


def is_configured() -> bool:
    """هل الإرسال ممكن أصلاً؟ بيتسأل في كل UI فيه زر إشعارات."""
    return push_is_configured()


def public_key() -> Optional[str]:
    """المفتاح العام بالشكل اللي **المتصفح** بياخده.

    **العميل بياخد المفتاح العام بس.** المفتاح الخاص مبيوصلش لصفحة
    HTML ولا لـAPI response ولا لـtemplate context. لو المفتاح العام
    مش متظبَط، بترجّع None والـUI بيقول "الإشعارات مش متاحة دلوقتي"
    بدل ما يطلق error في الكونسول.

    **65 بايت مع `0x04`:** `pushManager.subscribe({applicationServerKey})`
    بيرفض النقطة الناقصة بـ`InvalidAccessError`، والعَرَض إن المستخدم
    يقرأ "تم التفعيل" وما يجيلوش إشعار. تفاصيل الصيغة في
    `app.config.get_vapid_public_key_browser`.
    """
    return get_vapid_public_key_browser()


def public_key_env_format() -> Optional[str]:
    """المفتاح العام بصيغة `.env` (64 بايت) — للعرض الإداري/debugging.

    مش اللي بتروح للمتصفح؛ `public_key()` هي. اتنين موجودين عمداً
    عشان ما نخلطش بين اللي بيتخزّن واللي بيتسلّم.
    """
    return get_vapid_public_key()


# ============================================================
# التسجيل
# ============================================================


def _b64_to_urlsafe(value: str) -> str:
    """الـ`auth` بيجي من المتصفح base64url؛ `pywebpush` بيطلبه من غير padding.

    **مش بنستخدم `b64decode` هنا:** الـ`-` و`_` في base64url هما
    أسماء عادي، و`b64decode` بيشيلهم — فـ`auth` واحد بيفشل على
    التوقيع من غير رسالة خطأ واضحة.
    """
    import base64

    padded = value + "=" * (-len(value) % 4)
    return base64.urlsafe_b64encode(base64.urlsafe_b64decode(padded)).rstrip(b"=").decode("ascii")


def register_subscription(
    db: Session,
    *,
    endpoint: str,
    p256dh: str,
    auth: str,
    customer_id: Optional[int] = None,
    admin_id: Optional[int] = None,
    user_agent: Optional[str] = None,
    device: Optional[str] = None,
) -> PushSubscription:
    """يسجّل (أو يجدّد) اشتراك جهاز.

    ## ليه upsert مش insert

    نفس المتصفح بيرسل نفس الـendpoint في كل تسجيل. INSERT هيمشي أول
    مرة وبعدها يفشل على الفهرس الفريد. الـupsert بيرجّع الصف الموجود
    وبيحدّث `created_at`/الربط، فمفيش ازدواج في الإشعارات.

    ## ليه `status='active'` بيتقفل تاني

    المستخدم ممكن يكون عمل unsubscribe من جهاز، أو حد عمل
    unsubscribe ليه، وبعدين رجع سجّل تاني من نفس الجهاز. لازم الإشعار
    يوصله تاني. فالتسجيل بيعيد تنشيط الاشتراك دايماً.

    ## ليه `failure_count` بيتصفّر

    الاشتراك اللي اتعلّم إنه فاشل 20 مرة واتسجّل من جديد = اشتراك
    جديد. العدّاد القديم مش منتسب للجهاز الذي جاي من الخريطة السابقة.
    """
    existing = db.scalar(select(PushSubscription).where(PushSubscription.endpoint == endpoint))

    if existing is not None:
        # إعادة تسجيل: نفس ال′endpoint رجع. المفاتيح ممكن تكون اتغيّرت
        # (المتصح عمل key rotation)، فبنحدّثها. والstatus بيرجع
        # active عشى الإشعار يوصله تاني.
        existing.p256dh = p256dh
        existing.auth = auth
        existing.customer_id = customer_id
        existing.admin_id = admin_id
        existing.user_agent = user_agent
        existing.device = device
        existing.status = STATUS_ACTIVE
        existing.invalid_at = None
        existing.failure_count = 0
        db.add(existing)
        db.flush()
        return existing

    subscription = PushSubscription(
        endpoint=endpoint,
        p256dh=p256dh,
        auth=auth,
        customer_id=customer_id,
        admin_id=admin_id,
        user_agent=user_agent,
        device=device,
        status=STATUS_ACTIVE,
    )
    db.add(subscription)
    db.flush()
    return subscription


def find_subscription(db: Session, endpoint: str) -> Optional[PushSubscription]:
    return db.scalar(select(PushSubscription).where(PushSubscription.endpoint == endpoint))


def find_all(db: Session) -> List[PushSubscription]:
    """كل الاشتراكات، بدون فلتر حالة.

    للاستخدام الإداري/الاختبار فقط — مفيش endpoint بيرجّع ده للعميل
    (يعني كل اشتراكات كل العملاء). لو محتاج لوحة تحكم للاشتراكات
    في مرحلة جاية، ابدأ من هنا.
    """
    return list(db.scalars(select(PushSubscription).order_by(PushSubscription.id)))


def unsubscribe(db: Session, endpoint: str, *, audience: Optional[str] = None) -> bool:
    """يلغي اشتراك. بيرجّع True لو اتمسح فعلاً.

    **ليه بنمسح الصف بدل ما ننبّض `status`:** الاشتراك الملغى لازم
    يبقى مش موجود خالص. لو سبنا الصف، أي استعلام "اشتراكاتي" هيطلّع
    جهاز العميل خلّص الإشعارات — وده أسوأ من مسح صف.
    """
    subscription = find_subscription(db, endpoint)
    if subscription is None:
        return False
    if audience is not None:
        if audience == AUDIENCE_ADMIN and subscription.admin_id is None:
            return False
        if audience == AUDIENCE_CUSTOMER and subscription.customer_id is None:
            return False
    db.delete(subscription)
    db.flush()
    return True


def subscriptions_for_customer(db: Session, customer_id: int) -> List[PushSubscription]:
    return list(
        db.scalars(
            select(PushSubscription).where(
                PushSubscription.customer_id == customer_id,
                PushSubscription.status == STATUS_ACTIVE,
            )
        )
    )


def subscriptions_for_order(db: Session, order: Order) -> List[PushSubscription]:
    """اشتراكات العميل اللي عمل الطلب ده.

    **مربوطين بالطلب مش بالرقم:** العميل يقدر يكون عنده 3 موبايل
    (تابلت، لواحد الشغل، PWA مثبّت) — كلهم لازم يوصلهم إشعار حالة
    الطلب ده.

    **العملاء المسجّلون من غير اشتراك** (`customer_id is None`)
    مستبعدين: من غير اشتراك مفيش حاجة نبعت لها. اشتراك من غير
    `customer_id` معناها المتصفح لسه ما عملش أول طلب، وده طبيعي.
    """
    if order.customer_id is None:
        return []
    return subscriptions_for_customer(db, order.customer_id)


def subscriptions_for_admin(db: Session, admin_id: int) -> List[PushSubscription]:
    """اشتراكات أدمن واحد بعينه.

    بنستخدمها في `GET /admin/api/push/status` عشان الزرار في اللوحة
    يعكس حالة **جهاز الأدمن ده**، مش حالة أي حد تاني في المطعم.
    """
    return list(
        db.scalars(
            select(PushSubscription).where(
                PushSubscription.admin_id == admin_id,
                PushSubscription.status == STATUS_ACTIVE,
            )
        )
    )


def active_admin_subscriptions(db: Session) -> List[PushSubscription]:
    """كل اشتراكات الأدمن النشطة.

    طلب جديد = لكل الأدمن، مش للأدمن اللي عمل آخر طلب. لو ربطناه
    بـ"الأدمن المسؤول عن الطلب"، نص الإشعارات هتضيع. عدد المكالمات
    والتكلفة صغيرة (جدول صغير جدا، وإرسال واحد لكل صف).
    """
    return list(
        db.scalars(
            select(PushSubscription)
            .where(
                PushSubscription.admin_id.isnot(None),
                PushSubscription.status == STATUS_ACTIVE,
            )
            .order_by(PushSubscription.id)
        )
    )


# ============================================================
# الإرسال
# ============================================================


@dataclass
class SendResult:
    """نتيجة محاولة واحدة. مش exception — عشان الفصل ميتكسرش."""

    subscription_id: Optional[int]
    sent: bool
    status: str
    response_code: Optional[int] = None
    #: `True` لما المزوّد قال إن الـendpoint مش صالح anymore.
    endpoint_invalid: bool = False
    error: Optional[str] = None

    def as_record(self) -> dict[str, Any]:
        return {
            "subscription_id": self.subscription_id,
            "sent": self.sent,
            "status": self.status,
            "response_code": self.response_code,
            "endpoint_invalid": self.endpoint_invalid,
        }


@dataclass
class SendSummary:
    total: int = 0
    sent: int = 0
    failed: int = 0
    skipped: int = 0
    invalid: int = 0
    results: List[SendResult] = field(default_factory=list)

    @property
    def attempted(self) -> int:
        return self.sent + self.failed


def _record(
    db: Session,
    subscription: Optional[PushSubscription],
    *,
    audience: str,
    title: str,
    body: str,
    url: Optional[str],
    order_id: Optional[int],
    result_status: str,
    response_code: Optional[int] = None,
) -> None:
    """يسجّل محاولة إرسال في `push_notifications`.

    **بيستخدم nested transaction** عشان لو الكتابة فشلت ما نعملش
    rollback للصف اللي اتحفظ فعلاً. الإشعار ثانوي — فشلت الكتابة
    بتاعته ما تبكسرش الطلب.
    """
    try:
        with db.begin_nested():
            db.add(
                PushNotification(
                    subscription_id=subscription.id if subscription else None,
                    order_id=order_id,
                    audience=audience,
                    title=title,
                    body=body,
                    url=url,
                    status=result_status,
                    response_code=response_code,
                )
            )
    except SQLAlchemyError:
        logger.exception("push_notification_record_failed", extra={"audience": audience})


def _mark_invalid(db: Session, subscription: PushSubscription) -> None:
    """الـendpoint بقى مش صالح. علّمه بدل ما تبعته تاني.

    **مش بنمسح الصف:** الصف ده فيه سجل `push_notifications` مرتبط
    بـ`CASCADE`، فمسحه بيمسح التاريخ كمان. `status='invalid'` بيقول
    "جرّبنا ده واشتغلش" من غير ما نضيّع الـaudit.

    # ليه `is_active` مش بتاع الجدول ده
    الاشتراك بيشتغل بالـ`status` بتاعه هو مش بـ`admin.is_active`:
    لو الأدمن قفل حسابه، الاشتراكات بتاعته لازم تفضل محفوظة (لو رجع
    حسابه تلاقي إشعاراته شغالة) بس مش لازم تتبعت.
    """
    subscription.status = STATUS_INVALID
    subscription.invalid_at = datetime.now(timezone.utc)
    try:
        with db.begin_nested():
            db.add(subscription)
    except SQLAlchemyError:
        logger.exception("push_mark_invalid_failed")


def _bump_failure(db: Session, subscription: PushSubscription) -> None:
    subscription.failure_count = (subscription.failure_count or 0) + 1
    try:
        with db.begin_nested():
            db.add(subscription)
    except SQLAlchemyError:
        logger.exception("push_bump_failure_failed")


def send_to_subscription(
    db: Session,
    subscription: PushSubscription,
    *,
    payload: dict[str, Any],
    audience: str,
    order_id: Optional[int] = None,
) -> SendResult:
    """يحاول يبعت لاشتراك واحد. **ما بيرميش استثناءات أبداً.**

    دي الدالة اللي بتحمي الطلب: أي حاجة تحصل جوه — المزوّد رجع 500،
    الـendpoint باظ، مهلة اتعدّت، bug في الكود نفسه — بترجّع
    `SendResult` بدل ما تطلع لـcaller.
    """
    title = str(payload.get("title") or "")
    body = str(payload.get("body") or "")
    url = payload.get("url")
    url = str(url) if url else None

    if subscription.status != STATUS_ACTIVE:
        result = SendResult(
            subscription_id=subscription.id,
            sent=False,
            status=RESULT_SKIPPED,
        )
        _record(
            db,
            subscription,
            audience=audience,
            title=title,
            body=body,
            url=url,
            order_id=order_id,
            result_status=RESULT_SKIPPED,
        )
        return result

    if not is_configured():
        result = SendResult(
            subscription_id=subscription.id,
            sent=False,
            status=RESULT_SKIPPED,
        )
        _record(
            db,
            subscription,
            audience=audience,
            title=title,
            body=body,
            url=url,
            order_id=order_id,
            result_status=RESULT_SKIPPED,
        )
        return result

    try:
        from pywebpush import WebPushException, webpush
    except ImportError:  # pragma: no cover - pywebpush في requirements
        logger.error("pywebpush_not_installed")
        result = SendResult(
            subscription_id=subscription.id,
            sent=False,
            status=RESULT_SKIPPED,
        )
        _record(
            db,
            subscription,
            audience=audience,
            title=title,
            body=body,
            url=url,
            order_id=order_id,
            result_status=RESULT_SKIPPED,
        )
        return result

    try:
        webpush(
            subscription_info={
                "endpoint": subscription.endpoint,
                "keys": {
                    "p256dh": subscription.p256dh,
                    "auth": _b64_to_urlsafe(subscription.auth),
                },
            },
            data=json.dumps(payload, ensure_ascii=False),
            vapid_private_key=get_vapid_private_pem(),
            vapid_claims={"sub": settings.vapid_subject},
            ttl=MAX_TTL_SECONDS,
            timeout=settings.push_send_timeout_seconds,
        )
    except WebPushException as exc:
        response = getattr(exc, "response", None)
        status_code = getattr(response, "status_code", None)
        # 404/410 = ال′endpoint مات. ده معناه ما نبعتهوش تاني.
        endpoint_invalid = status_code in (404, 410)
        logger.warning(
            "push_send_failed",
            extra={
                "event": "push_send_failed",
                "subscription_id": subscription.id,
                "audience": audience,
                "response_code": status_code,
                "endpoint_invalid": endpoint_invalid,
            },
        )
        if endpoint_invalid:
            _mark_invalid(db, subscription)
        else:
            _bump_failure(db, subscription)
        result = SendResult(
            subscription_id=subscription.id,
            sent=False,
            status=RESULT_FAILED,
            response_code=status_code,
            endpoint_invalid=endpoint_invalid,
            error=str(exc)[:200],
        )
        _record(
            db,
            subscription,
            audience=audience,
            title=title,
            body=body,
            url=url,
            order_id=order_id,
            result_status=RESULT_FAILED,
            response_code=status_code,
        )
        return result
    except Exception:
        # استثناء مش متوقع (timeout, DNS, bug). بنسجّله ونتعامل معاه
        # كـfailure عادي. **الهدف إن الطلب يفضل محفوظ.**
        logger.exception(
            "push_send_unexpected_error",
            extra={
                "event": "push_send_unexpected_error",
                "subscription_id": subscription.id,
                "audience": audience,
            },
        )
        _bump_failure(db, subscription)
        result = SendResult(
            subscription_id=subscription.id,
            sent=False,
            status=RESULT_FAILED,
            error="unexpected",
        )
        _record(
            db,
            subscription,
            audience=audience,
            title=title,
            body=body,
            url=url,
            order_id=order_id,
            result_status=RESULT_FAILED,
        )
        return result

    # نجاح
    subscription.failure_count = 0
    subscription.last_success_at = datetime.now(timezone.utc)
    try:
        with db.begin_nested():
            db.add(subscription)
    except SQLAlchemyError:
        logger.exception("push_mark_success_failed")

    result = SendResult(
        subscription_id=subscription.id,
        sent=True,
        status=RESULT_SENT,
        response_code=200,
    )
    _record(
        db,
        subscription,
        audience=audience,
        title=title,
        body=body,
        url=url,
        order_id=order_id,
        result_status=RESULT_SENT,
        response_code=200,
    )
    return result


def send_to_many(
    db: Session,
    subscriptions: Sequence[PushSubscription],
    *,
    payload: dict[str, Any],
    audience: str,
    order_id: Optional[int] = None,
) -> SendSummary:
    """يبعت لنفس الـpayload على عدة اشتراكات، و**بيكمّل لو واحد فشل**.

    دي نقطة حرجة: لو الاشتراك الأول رجّع 500، التانية ليهم استحقاق
    بنفس القدر. الحلقة مافيش `break` فيها، و`send_to_subscription`
    نفسها ما بترميش.
    """
    summary = SendSummary(total=len(subscriptions))
    for subscription in subscriptions:
        result = send_to_subscription(
            db,
            subscription,
            payload=payload,
            audience=audience,
            order_id=order_id,
        )
        summary.results.append(result)
        if result.sent:
            summary.sent += 1
        elif result.status == RESULT_SKIPPED:
            summary.skipped += 1
        else:
            summary.failed += 1
        if result.endpoint_invalid:
            summary.invalid += 1
    return summary


def send_and_commit(
    db: Session,
    subscriptions: Sequence[PushSubscription],
    *,
    payload: dict[str, Any],
    audience: str,
    order_id: Optional[int] = None,
) -> SendSummary:
    """`send_to_many` + commit للتعديلات على الاشتراكات والسجل.

    **الـcommit جزء من العقد مش تحسين:** لازم تحديث `status`/
    `failure_count` وسجل `push_notifications` يبقى محفوظ. لو الـcommit
    رجع error، بنتعامل معاه كـpush failure — الطلب نفسه اتحفظ قبل
    كده بـcommit تاني.
    """
    summary = send_to_many(
        db,
        subscriptions,
        payload=payload,
        audience=audience,
        order_id=order_id,
    )
    try:
        db.commit()
    except SQLAlchemyError:
        logger.exception("push_post_send_commit_failed")
        try:
            db.rollback()
        except SQLAlchemyError:
            logger.exception("push_post_send_rollback_failed")
    return summary


# ============================================================
# سجل حالة الطلب
# ============================================================


def record_status_change(
    db: Session,
    order: Order,
    status: str,
    *,
    admin_id: Optional[int] = None,
    admin_username: Optional[str] = None,
    note: Optional[str] = None,
    synthetic: bool = False,
    at: Optional[datetime] = None,
) -> Optional[OrderStatusHistory]:
    """يسجّل تغيّر حالة. **ما بيرميش استثناءات.**

    السجل ده مش حرج للتشغيل: لو فشل، الطلب اتغيّر خلاص والحالة
    الصح في `orders.status`. بنسجّل الفشل وبنكمل.

    # `admin_username` denormalized
    لو الحساب اتمسح بعدين، السجل يفضل يقول مين اللي غيّر. من غيرها
    السجل يقول "Admin 3" بعد ما الحساب يبقى `SET NULL` ومفيش رقم.

    ## ليه `at` بارامتر
    للـbackfill بس: `changed_at` بيرجعي وقت `created_at` بتاع الطلب
    عشان نعرف نت sortable. في التشغيل العادي بنسيب `None` والعمود
    بيتقفل على `now()`.
    """
    try:
        with db.begin_nested():
            row = OrderStatusHistory(
                order_id=order.id,
                status=status,
                changed_at=at or datetime.now(timezone.utc),
                changed_by_admin_id=admin_id,
                changed_by_username=admin_username,
                note=(note or None),
                synthetic=synthetic,
            )
            db.add(row)
            db.flush()
        return row
    except SQLAlchemyError:
        logger.exception(
            "status_history_write_failed",
            extra={"event": "status_history_write_failed", "order_id": order.id},
        )
        return None


def status_history(db: Session, order_id: int) -> List[OrderStatusHistory]:
    """سجل الحالات بترتيب الوقت، **الأحدث الأول**.

    الترتيب معكوس عن الـSQL: العميل بيفتح `/track` عايز يشوف آخر
    حاجة حصلت، مش أول حاجة. من غير `.desc()` كان لازم الـAPI
    يعمل `reversed()` في كل نداء، ومينفعش ننسى.
    """
    return list(
        db.scalars(
            select(OrderStatusHistory)
            .where(OrderStatusHistory.order_id == order_id)
            .order_by(OrderStatusHistory.changed_at.desc(), OrderStatusHistory.id.desc())
        )
    )


def first_status_at(db: Session, order_id: int) -> dict[str, datetime]:
    """أول وقت لكل حالة ظهرت فيه.

    استعلام واحد بدل `status_history()` في كل مرة، عشان صفحة التتبّع
    تبني الخريطة من request واحد.
    """
    from sqlalchemy import func

    rows = db.execute(
        select(OrderStatusHistory.status, func.min(OrderStatusHistory.changed_at))
        .where(OrderStatusHistory.order_id == order_id)
        .group_by(OrderStatusHistory.status)
    ).all()
    return {status: at for status, at in rows if at is not None}


# ============================================================
# قوالب الإشعارات
# ============================================================

# مثال payload: {"title": "...", "body": "...", "url": "/track?t=...",
# "order_id": 1042, "status": "preparing"}


def order_status_payload(
    order: Order,
    status_label: str,
    *,
    tracking_url: str,
) -> dict[str, Any]:
    """إشعار تغيير حالة للعميل.

    **الاسم مش في الـbody:** الإشعار بيظهر على شاشة مقفولة، واسم
    المطعم هو اسم التطبيق كده. رقم الطلب كافي يربط الإشعار بالطلب.
    """
    return {
        "title": f"طلبك #{order.id}: {status_label}",
        "body": f"إجمالي الطلب {order.total} — اضغط لمتابعة الطلب",
        "url": tracking_url,
        "order_id": order.id,
        "status": order.status,
    }


def new_order_payload(order: Order) -> dict[str, Any]:
    """إشعار "طلب جديد" للأدمن. الرابط بيروح لصفحة الطلب في اللوحة."""
    return {
        "title": f"🔔 طلب جديد #{order.id}",
        "body": f"{order.customer_name} — {order.total}",
        "url": f"/admin/orders/{order.id}",
        "order_id": order.id,
    }
