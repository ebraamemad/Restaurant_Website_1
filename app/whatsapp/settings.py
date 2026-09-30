"""إعدادات واتساب — منين بتتجيب، ومنين بتتّكل، وإيه اللي بيتمنع.

## مصدرين، بترتيب أولوية

1. **الجدول** `whatsapp_config` (سطر واحد) — اللي الأدمن بيعدّله من
   اللوحة: تشغيل/إيقاف، الفاصل، حجم الدفعة، المحاولات.
2. **الـenvironment** (`app/config.py`) — الـbase URL والـAPI key.

**السر من الـenvironment بس، والجدول مالوش عمود سر خالص.** السبب:
الجدول بيقراه أي admin query وبياخد نسخة في كل backup، والـ`.env`
لأ. يعني `whatsapp_config` فيه **عمود `api_key` نهائياً** — مش
عمود فاضي، مش عمود مشفّر. `has_api_key` في الجدول بيقول "فيه مفتاح
مُعدّ على السيرفر؟" من غير ما يخزّنه.

الجدول فيه عمود `base_url` و`instance` — دول مش أسرار، وهما اللي
الأدمن لازم يعدّلهم من غير ما يبقى عليه SSH.

## ليه الـURL بيتفحص

الـbase URL بينكتبه **admin**، والسيرفر بيبعت POST عليه. من غير فحص،
admin واحد يقدر يحوّل السيرفر لـSSRF client: `http://169.254.169.254/`
(بيانات السحابة)، أو `http://localhost:5432/`، أو شبكة داخلية.

`validate_base_url` بيمنع: `http` في production، ومضيف في URL
(`user:pass@host` — ده سر مكدّس في الـlog)، وم.port غريب، ومفيش host،
وكل IP من الـreserved ranges.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings

from .base import WhatsAppConfigurationError

# ============================================================
# حدود
# ============================================================

#: أقصى فاصل بين رسالتين. فوق ده الحملة بتتعلّق ساعتين والـadmin
#: بيظن إن في hang. القيمة دي **سقف تشغيلي مش توصية**.
MAX_INTERVAL_MS = 60_000
MIN_INTERVAL_MS = 0

#: أقصى حجم دفعة. الدفعة = عدد رسايل بنبعتهم قبل ما نراجع الإلغاء
#: ونعمل commit. الدفعة الكبيرة = تأخير commit طويل = فيه حملات فاتوا وقت ما يتلغوا.
MAX_BATCH_SIZE = 100

#: أقصى محاولات لرسالة واحدة. 0 = محاولة واحدة وخلاص.
MAX_RETRIES = 5


def _clamp(value: Optional[int], low: int, high: int, fallback: int) -> int:
    if value is None:
        return fallback
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(low, min(high, number))


@dataclass(frozen=True)
class WhatsAppConfig:
    """الإعدادات **بعد الدمج** — الشكل الوحيد اللي الـprovider بيشوفه.

    `api_key` هنا هي القيمة الحقيقية، والـobject ده **مالوش**
    `__repr__` بيطبعها (شوف `safe_dict` للعرض). اللي بياخد
    `WhatsAppConfig` لازم يكون server-side.
    """

    enabled: bool = False
    provider: str = "evolution"
    base_url: str = ""
    instance: str = ""
    api_key: str = ""
    sending_enabled: bool = False
    message_interval_ms: int = 1_000
    batch_size: int = 20
    max_retries: int = 2
    timeout_seconds: float = 20.0
    test_phone: str = ""
    allow_private_hosts: bool = True
    allowed_hosts: frozenset[str] = frozenset()

    # ------------------------------------------------------------
    # العرض
    # ------------------------------------------------------------

    @property
    def api_key_masked(self) -> str:
        """شكل العرض. **الطريقة الوحيدة** المسموح بيها output."""
        from .redact import mask_secret

        return mask_secret(self.api_key)

    @property
    def has_api_key(self) -> bool:
        return bool(self.api_key)

    def safe_dict(self) -> dict:
        """كل حاجة **غير السر**، جاهزة للـtemplate.

        مفيش مسار بيرجّع `asdict(config)` للواجهة — دي هي. لو ضفت
        حقل جديد، لازم تقرر: يعرض ولا مش يعرض. الافتراضي مش
        يعرض.
        """
        return {
            "enabled": self.enabled,
            "provider": self.provider,
            "base_url": self.base_url,
            "instance": self.instance,
            "has_api_key": self.has_api_key,
            "api_key_masked": self.api_key_masked,
            "sending_enabled": self.sending_enabled,
            "message_interval_ms": self.message_interval_ms,
            "batch_size": self.batch_size,
            "max_retries": self.max_retries,
            "timeout_seconds": self.timeout_seconds,
            "test_phone": self.test_phone,
            "allow_private_hosts": self.allow_private_hosts,
        }

    # ------------------------------------------------------------
    # الاشتقاقات
    # ------------------------------------------------------------

    @property
    def ready(self) -> bool:
        """هل الإرسال ممكن أصلاً؟

        **تلات شروط لازم مع بعض**: معطّلة؟ مفيش مفتاح؟ مفيش
        instance؟ أي واحد ناقص = الإرسال لازم يرفض قبل ما يطلع
        شبكة، مش بعد 400 رسالة فاشلة.
        """
        return bool(self.enabled and self.sending_enabled and self.api_key and self.instance)

    @property
    def interval_seconds(self) -> float:
        return self.message_interval_ms / 1000.0

    def assert_sendable(self) -> None:
        """بيرمي لو الإرسال مش ممكن. **قبل** أي اتصال."""
        problems = []
        if not self.enabled:
            problems.append("القناة مقفولة")
        if not self.sending_enabled:
            problems.append("الإرسال مقفول")
        if not self.base_url:
            problems.append("مفيش رابط المزوّد")
        if not self.instance:
            problems.append("مفيش اسم instance")
        if not self.api_key:
            problems.append("مفيش مفتاح API")
        if problems:
            raise WhatsAppConfigurationError("الإعدادات ناقصة: " + "، ".join(problems))


# ============================================================
# فحص الـURL (SSRF)
# ============================================================

_ALLOWED_SCHEMES = frozenset({"http", "https"})


#: المنافذ المسموح بيها وقت التشغيل. غيرها = منفذ عشوائي على
#: عنوان متاح، وده باب جانبي لخدمة داخلية.
DEFAULT_PORTS = {"https": 443, "http": 80}


def validate_base_url(raw: str, *, allow_private: bool = False) -> str:
    """يتحقّق من `base_url` ويرجّعه نضيف.

    بيرمي `WhatsAppConfigurationError` — دي مشكلة إعداد، مش مشكلة
    مزوّد، والـUI بتعرضها كـ"ظبّظ الإعدادات" مش "فشل إرسال".

    **الترتيب مقصود**: الشكل، بعدين النطاق، بعدين المنفذ، وأخيرًا
    العنوان. كل خطوة بتفشل مغلق — مفيش مسار بيوصل لآخر step وهو
    لسه متأكّد من اللي قبله.

    `allow_private` استثناء **محدود وموسوع** للتطوير على Evolution
    محلي: loopback بس، وبـ`http`، ومش في production. مش "اقبل أي
    شبكة داخلية".
    """
    candidate = (raw or "").strip().rstrip("/")
    if not candidate:
        raise WhatsAppConfigurationError("رابط المزوّد فاضي")

    parts = urlsplit(candidate)
    if parts.scheme not in _ALLOWED_SCHEMES:
        raise WhatsAppConfigurationError("الرابط لازم يكون http أو https")
    if not parts.hostname:
        raise WhatsAppConfigurationError("الرابط من غير اسم نطاق")
    if parts.username or parts.password:
        # سر مدفون جوه الرابط = بيتطبع في أي log بيطبع الرابط.
        raise WhatsAppConfigurationError("الرابط مش المسموح فيه مستخدم أو كلمة سر")
    if parts.query or parts.fragment:
        raise WhatsAppConfigurationError("الرابط مش المسموح فيه query أو fragment")
    if parts.scheme == "http" and settings.is_production:
        raise WhatsAppConfigurationError("في production لازم يكون الرابط https")

    host = _normalize_host(parts.hostname)

    # القائمة المسموحة حدود مش تفضيل.
    raw_allowlist = (settings.whatsapp_allowed_hosts or "").strip()
    if "*" in raw_allowlist:
        raise WhatsAppConfigurationError(
            "القائمة المسموحة فيها wildcard — مش مسموح"
        )
    allowlist = _allowed_host_set()
    if allowlist and host not in allowlist:
        raise WhatsAppConfigurationError(
            "النطاق ده مش في WHATSAPP_ALLOWED_HOSTS"
        )

    port = _safe_port(parts)
    dev_loopback = bool(allow_private) and not settings.is_production
    addresses = _resolve_host(host)
    trusted = bool(allowlist) and host in allowlist

    if trusted:
        # الأدمن سمّح بالنطاق ده صراحة في السيرفر، فبنثق فيه.
        return candidate

    if dev_loopback and addresses and all(a.is_loopback for a in addresses):
        # تطوير محلي: loopback على أي منفذ (Evolution محلي غالبًا 8080).
        return candidate

    if port is not None and port != DEFAULT_PORTS[parts.scheme]:
        raise WhatsAppConfigurationError("المنفذ %s مش مسموح" % port)

    if not addresses:
        # فشل DNS = مش قادرين نثبت إنه آمن = رفض.
        raise WhatsAppConfigurationError("مقدرناش نحلّ النطاق ده")
    if any(_is_blocked_address(address) for address in addresses):
        raise WhatsAppConfigurationError(
            "النطاق ده داخلي أو محجوز. للتطوير على Evolution محلي، "
            "فعّل WHATSAPP_ALLOW_PRIVATE_HOSTS في السيرفر — مش من اللوحة."
        )
    return candidate


def _safe_port(parts) -> Optional[int]:
    """المنفذ لو موجود. منفذ مش مفهوم = رفض، مش تجاهل.

    `urlsplit().port` بيرمي `ValueError` على منفذ مش رقم، ولو
    فاضيناه كان `http://host:abc/` هيعدّي زي `http://host/`.
    """
    try:
        return parts.port
    except ValueError:
        raise WhatsAppConfigurationError("المنفذ مش صحيح")


def _normalize_host(host: str) -> str:
    """شكل موحّد لمقابلة النطاق: حروف صغيرة، بلا أقواس، وبلا نقطة أخيرة."""
    return (host or "").strip().lower().strip("[]").rstrip(".")


def _allowed_host_set() -> frozenset[str]:
    raw = (settings.whatsapp_allowed_hosts or "").strip()
    if not raw:
        return frozenset()
    return frozenset(
        _normalize_host(item) for item in raw.split(",") if item.strip()
    )


def _resolve_host(host: str) -> list:
    """كل عناوين الـhost. **فشل DNS = لائحة فاضية** (fail closed)."""
    name = (host or "").strip().strip("[]")
    if not name:
        return []
    if name in {"localhost", "localhost.localdomain", "ip6-localhost"}:
        return [ipaddress.ip_address("127.0.0.1")]
    # mDNS والأسماء الداخلية — مابعدشي عن شبكة ال‎DNS.
    if name.endswith(".local") or name.endswith(".internal"):
        return []
    try:
        return [ipaddress.ip_address(name)]
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(name, None)
    except (socket.gaierror, UnicodeError, OSError):
        return []
    out = []
    for info in infos:
        try:
            out.append(ipaddress.ip_address(info[4][0]))
        except ValueError:
            # عنوان مش IP = شكل ما نورساش. نعتبره مرفوض.
            return []
    return out


def _is_blocked_address(address) -> bool:
    """العنوان ده آمن نتكلّم معاه؟

    **fail closed**: أي عنوان في نطاق محجوز → مرفوض. الاختيار
    بـloopback بس هنا مقصود — الاستثناء للتطوير بييجي من فوق
    (`dev_loopback`) مش من هنا.
    """
    return bool(
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    )


# ============================================================
# الدمج
# ============================================================

#: القيم الافتراضية للجدول. نستخدمها للـINSERT لما الجدول فاضي، و
#: للـfallback لو أي عمود اتلف.
DEFAULTS = {
    "enabled": False,
    "sending_enabled": False,
    "message_interval_ms": 1_000,
    "batch_size": 20,
    "max_retries": 2,
}


def load_config(db: Session) -> WhatsAppConfig:
    """الإعدادات الفعلية بعد الدمج.

    **بترمي** `WhatsAppConfigurationError` لو الـDB فيه قيمة مش
    صالحة (فاصل سالب، دفعة 5000).Silent ignore كان هيخلي الأدمن
    يضبط رقم ويشوف رقم تاني شغال — والسبب مستحيل يتتبع.
    """
    from app.models import WhatsAppConfigRow

    row = db.scalar(select(WhatsAppConfigRow).limit(1))

    enabled = _db_or(row, "enabled", DEFAULTS["enabled"])
    sending_enabled = _db_or(row, "sending_enabled", DEFAULTS["sending_enabled"])
    interval = _clamp(
        _db_or(row, "message_interval_ms", DEFAULTS["message_interval_ms"]),
        MIN_INTERVAL_MS,
        MAX_INTERVAL_MS,
        DEFAULTS["message_interval_ms"],
    )
    batch = _clamp(
        _db_or(row, "batch_size", DEFAULTS["batch_size"]),
        1,
        MAX_BATCH_SIZE,
        DEFAULTS["batch_size"],
    )
    retries = _clamp(
        _db_or(row, "max_retries", DEFAULTS["max_retries"]),
        0,
        MAX_RETRIES,
        DEFAULTS["max_retries"],
    )

    base_url = (settings.evolution_base_url or "").strip().rstrip("/")
    if row is not None and (row.base_url or "").strip():
        base_url = (row.base_url or "").strip().rstrip("/")

    instance = (settings.evolution_instance or "").strip()
    if row is not None and (row.instance or "").strip():
        instance = (row.instance or "").strip()

    # السر من الـenvironment فقط. الجدول **مالوش عمود `api_key`** —
    # مش بيتخزّن خالص، لا فين.
    api_key = (settings.evolution_api_key or "").strip()

    allow_private = bool(settings.whatsapp_allow_private_hosts)

    config = WhatsAppConfig(
        enabled=bool(enabled and settings.whatsapp_enabled),
        provider=(settings.whatsapp_provider or "evolution").strip().lower(),
        base_url=base_url,
        instance=instance,
        api_key=api_key,
        sending_enabled=bool(sending_enabled),
        message_interval_ms=interval,
        batch_size=batch,
        max_retries=retries,
        timeout_seconds=float(settings.whatsapp_request_timeout_seconds or 20.0),
        test_phone=(settings.whatsapp_test_phone or "").strip(),
        allow_private_hosts=allow_private,
        allowed_hosts=_allowed_host_set(),
    )
    if config.base_url:
        # بنفحص بدري: URL غلط يتكتب في اللوحة يبقى error واضح دلوقتي
        # مش 400 رسالة فاشلة بعدين.
        object.__setattr__(
            config,
            "base_url",
            validate_base_url(config.base_url, allow_private=allow_private),
        )
    return config


def _db_or(row, field_name: str, fallback):
    if row is None:
        return fallback
    value = getattr(row, field_name, None)
    return fallback if value is None else value