"""حماية لوحة الإدارة من محاولات الدخول العنيفة (brute force).

الفكرة: كل محاولة فاشلة بتتسجل في جدول `admin_login_attempts`، وبعد عدد
محدد من المحاولات في نافذة زمنية، أي محاولة تانية من نفس الـIP أو من نفس
(الـIP + اسم المستخدم) بترفض مؤقتًا.

قرارات تصميم مقصودة:
- **PostgreSQL، مش Redis**: التطبيق monolith أصلًا، والجدول صغير جدًا
  (صف واحد لكل محاولة فاشلة، وبينتنضف خارج النافذة). مفيش خدمة إضافية
  يتوقع حد يشغلها أو يراقبها.
- **مش قفل دائم**: النافذة منزلقة (sliding window)، فبعد 15 دقيقة من آخر
  محاولة فاشلة، الأدمن بيرجع يقدر يدخل عادي. مفيش حالة "محظور للأبد".
- **مش بيكشف وجود المستخدم**: العدّاد بيتحسب على أي اسم يُرسَل، سواء موجود
  في الداتابيز أو لأ. والرسالة المرجعة واحدة في كل الحالات.
- **مفيش بيانات شخصية مخزنة**: بنخزن SHA-256 HMAC من الـIP ومن (الـIP +
  اليوزر)، مش القيم نفسها.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import Request
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import AdminLoginAttempt

logger = logging.getLogger("app.login_guard")

# رسالة موحّدة: نفس النص سواء السبب كان باسورد غلط أو محاولات كتير،
# عشان مفيش أي استنتاج ممكن عن يوزرnames موجودة من رد الفعل.
LOGIN_ERROR_MESSAGE = "بيانات الدخول غير صحيحة"
LOGIN_THROTTLED_MESSAGE = "محاولات دخول كثيرة فاشلة. من فضلك انتظر قليلًا ثم حاول مرة أخرى."


@dataclass(frozen=True)
class ThrottleDecision:
    """نتيجة فحص الإيقاف المؤقت قبل السماح بمحاولة دخول."""

    allowed: bool
    retry_after_seconds: int = 0
    remaining: int = 0


def client_ip(request: Request) -> str:
    """IP الخاص بالعميل.

    بيجي من request.client، وuvicorn بيصحّحه للـIP الحقيقي لو اتشغّل
    بـ--forwarded-allow-ips (مهم جدًا لو في reverse proxy قدام التطبيق).
    """
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def _digest(value: str) -> str:
    """SHA-256 HMAC بـAPP_SECRET_KEY.

    مش hash عادي: مع salt سري، فحتى لو الداتابيز اتسرقت الأعمدة دي
    مش قابلة للعكس بـrainbow tables.
    """
    return hmac.new(
        settings.app_secret_key.encode("utf-8"), value.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def ip_hash_for(ip: str) -> str:
    return _digest(f"login-ip|{ip}")


def key_hash_for(ip: str, username: str) -> str:
    # بنطبّع الـusername (قص الفراغات + حروف صغيرة) عشان مسافات أو حالة
    # الأحرف ما تقدرش تلاعب بالعدّاد وتعدّي الحد.
    return _digest(f"login-key|{ip}|{(username or '').strip().lower()}")


def _window_start() -> datetime:
    return datetime.now(timezone.utc) - timedelta(minutes=settings.admin_login_window_minutes)


def _prune(db: Session, key_hash: str) -> None:
    """يشيل المحاولات القديمة لنفس الـkey.

    عشان كمان: العدّاد بيحسب من المحاولات جوه النافذة بس، فالمحاولات
    الخارجة منها مش ب takeoff من الحساب — هي بتتخزن بس. التنضيف بيخلي
    الجدول ما يكبرش بلا حدود.
    """
    db.execute(
        delete(AdminLoginAttempt).where(
            AdminLoginAttempt.key_hash == key_hash,
            AdminLoginAttempt.attempted_at < _window_start(),
        )
    )


def check_login_allowed(db: Session, ip: str, username: str) -> ThrottleDecision:
    """بيت called قبل أي تحقق من الباسورد.

    بيرجع allowed=False لو اتExceeded الحد، مع كام ثانية تفضل متبقية.
    """
    key_hash = key_hash_for(ip, username)
    ip_hash = ip_hash_for(ip)
    since = _window_start()

    _prune(db, key_hash)

    # عدّاد (IP + username) — بيغطي تخمين الباسورد لحساب واحد
    key_count = db.scalar(
        select(func.count())
        .select_from(AdminLoginAttempt)
        .where(
            AdminLoginAttempt.key_hash == key_hash,
            AdminLoginAttempt.attempted_at >= since,
        )
    ) or 0

    # عدّاد الـIP — بيغطي password spraying على أكتر من حساب
    ip_count = db.scalar(
        select(func.count())
        .select_from(AdminLoginAttempt)
        .where(
            AdminLoginAttempt.ip_hash == ip_hash,
            AdminLoginAttempt.attempted_at >= since,
        )
    ) or 0

    key_limit = settings.admin_login_max_attempts
    ip_limit = settings.admin_login_max_attempts_per_ip

    if key_count >= key_limit or ip_count >= ip_limit:
        return ThrottleDecision(
            allowed=False,
            retry_after_seconds=_seconds_until_oldest_expires(db, key_hash, ip_hash, since),
            remaining=0,
        )

    return ThrottleDecision(
        allowed=True,
        remaining=max(0, min(key_limit - key_count, ip_limit - ip_count)),
    )


def _seconds_until_oldest_expires(
    db: Session, key_hash: str, ip_hash: str, since: datetime
) -> int:
    """كام ثانية لازم تعدّي قبل ما أقدم محاولة في النافذة تخرج منها.

    الحساب على أقدم محاولة في العدّادات اللي وصلوا للحد — لأنها هي اللي
    هتسيب مكان للأدمن.
    """
    oldest_key = db.scalar(
        select(func.min(AdminLoginAttempt.attempted_at)).where(
            AdminLoginAttempt.key_hash == key_hash,
            AdminLoginAttempt.attempted_at >= since,
        )
    )
    oldest_ip = db.scalar(
        select(func.min(AdminLoginAttempt.attempted_at)).where(
            AdminLoginAttempt.ip_hash == ip_hash,
            AdminLoginAttempt.attempted_at >= since,
        )
    )
    candidates = [dt for dt in (oldest_key, oldest_ip) if dt is not None]
    if not candidates:
        return settings.admin_login_window_minutes * 60

    oldest = min(candidates)
    if oldest.tzinfo is None:
        oldest = oldest.replace(tzinfo=timezone.utc)
    expires_at = oldest + timedelta(minutes=settings.admin_login_window_minutes)
    seconds = int((expires_at - datetime.now(timezone.utc)).total_seconds())
    return max(seconds, 1)


def record_login_failure(db: Session, ip: str, username: str) -> None:
    """تسجيل محاولة فاشلة. مفيش commit هنا — الـcaller بيتحكم في الـtransaction."""
    key_hash = key_hash_for(ip, username)
    db.add(AdminLoginAttempt(ip_hash=ip_hash_for(ip), key_hash=key_hash))
    # لازم flush قبل الـcount: الـsession شغال بـautoflush=False، فالصف
    # الجديد لسه في الذاكرة والـCOUNT مش هيشوفه. من غير Flush السقف مش
    # هيتفعّل خالص. الـflush كمان بيملأ attempted_at من الـserver default،
    # وهو محتاج للترتيب في القص.
    db.flush()

    # سقف صلب: حتى لو حد بيلاعب بالعدّاد، عدد الصفوف لنفس الـkey محدود.
    stored = db.scalar(
        select(func.count())
        .select_from(AdminLoginAttempt)
        .where(AdminLoginAttempt.key_hash == key_hash)
    ) or 0
    cap = settings.admin_login_max_rows_per_key
    if stored > cap:
        # نمسي الأقدم ونسيب الأحدث cap صف. بنجيب الـids الأول بدل
        # DELETE ... ORDER BY مباشرة، لأن sqlalchemy.delete مافيهوش
        # order_by والـORM delete variant محتاج synchronization mode.
        stale_ids = db.scalars(
            select(AdminLoginAttempt.id)
            .where(AdminLoginAttempt.key_hash == key_hash)
            .order_by(AdminLoginAttempt.attempted_at.desc(), AdminLoginAttempt.id.desc())
            .offset(cap)
        ).all()
        if stale_ids:
            db.execute(delete(AdminLoginAttempt).where(AdminLoginAttempt.id.in_(stale_ids)))

    logger.warning(
        "admin_login_failed",
        extra={"event": "admin_login_failed", "key_hash": key_hash[:12]},
    )


def record_login_success(db: Session, ip: str, username: str) -> None:
    """بعد دخول ناجح: امسح عدّاد هذا الـkey.

    عشان الأدمن اللي غلط مرتين يقدر يدخل عادي، ولما يدخل فعلاً ما يفضلش
    تحت التهديد. عدّاد الـIP لوحده لسه عليه تسجيل من محاولات تانية.
    """
    key_hash = key_hash_for(ip, username)
    db.execute(delete(AdminLoginAttempt).where(AdminLoginAttempt.key_hash == key_hash))
    logger.info("admin_login_succeeded", extra={"event": "admin_login_succeeded", "key_hash": key_hash[:12]})


def clear_all_attempts(db: Session) -> int:
    """مسح كل السجلات — للاختبارات، ولما الأدمن يتنسي كلمة السر."""
    result = db.execute(delete(AdminLoginAttempt))
    return int(result.rowcount or 0)
