'''
Settings configuration with production safety validation.
'''
import base64
import logging
from functools import lru_cache

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger("app.config")

# البيئة اللي نعتبرها مش production. أي اسم تاني بيتعامل معاه على إنه
# production: الغلط في `APP_ENV` كان بيلغي كل فحوصات الأمان بصمت، وده
# بالظبط الطريق اللي كوكيز من غير Secure بتوصله production. فالأفضل نبدأ
# من الافتراضي الآمن. لو حد فعلاً بيشغّل بيئة على HTTP عادي (staging
# داخلي مثلاً) يقدر ي-opt out صريح بـALLOW_INSECURE_COOKIES_IN_PRODUCTION.
NON_PRODUCTION_ENV_NAMES = frozenset(
    {"development", "dev", "test", "testing", "local"}
)
JSON_LOG_FORMATS = frozenset({"json"})


class Settings(BaseSettings):
    app_name: str = "Restaurant"
    app_env: str = "development"
    app_secret_key: str
    database_url: str
    admin_username: str = "admin"
    admin_password: str = "admin12345"
    cookie_secure: bool = True
    access_token_expire_minutes: int = 60 * 24

    # Phase 0 — إعدادات الحماية والحماية من المحاولات
    # عدد المحاولات الفاشلة المسموح بها لنفس (IP + اسم المستخدم) في نافذة زمنية
    admin_login_max_attempts: int = 5
    # عدد المحاولات الفاشلة المسموح بها لنفس الـIP أيًا كان اسم المستخدم
    # (بيغطي password spraying على أكتر من حساب)
    admin_login_max_attempts_per_ip: int = 20
    # نافذة زمنية بالـدقائق — بعد ما تعدي، المحاولات القديمة بتتنسي
    admin_login_window_minutes: int = 15
    # سقف لعدد المسجلات القديمة لنفس الـkey (تنضيف وقائي)
    admin_login_max_rows_per_key: int = 50

    # Phase 3 — Web Push (VAPID)
    # المفتاح الخاص **لازم يفضل على السيرفر**. `get_public_vapid_key` هي
    # الدالة الوحيدة المسموح لها تقرأ `vapid_private_key`، وهي بترجّع
    # المفتاح العام بس. أي endpoint بيرجّع للعميل بيرجّع العام بس — وده
    # اللي بيتحوّل لـ`applicationServerKey` في المتصفح.
    vapid_subject: str = "mailto:admin@example.com"
    vapid_private_key: str | None = None
    vapid_public_key: str | None = None
    # مهلة الإرسال. الـpush بيبعت طلب شبكة، والطلب ده جاي من مسار طلب
    # حقيقي أو من تغيير حالة في اللوحة، فمفيش داعي إنه يعلّق المسار كله لو
    # المزوّد بطيء. القيمة الافتراضية 10 ثواني.
    push_send_timeout_seconds: int = 10
    # نطاقات مزوّدي الـpush المسموح تسجيلهم. **قائمة أمان مش تفضيل.**
    # الـendpoint بيتخزّن من المتصفح، وبعدين السيرفر بيبعت له POST. لو
    # قبلنا أي نطاق، الـendpoint بيبقى **SSRF**: حد بيسجّل
    # `https://169.254.169.254/latest/meta-data/` أو `https://localhost:8000/admin`
    # والسيرفر يعمل لهم طلب من جوه الشبكة الداخلية — والرد بيرجع في اللوج.
    # القيم دي نطاقات المزوّدين الحقيقيين بس. لو عندك web-push
    # self-hosted، ضيف نطاقه هنا (عن طريق `.env`، مش بالكود).
    push_allowed_endpoint_hosts: str = (
        "fcm.googleapis.com,"
        "updates.push.services.mozilla.com,"
        "web.push.apple.com,"
        "push.services.mozilla.com"
    )

    # Phase 5 — WhatsApp (Evolution API)
    #
    # **الأسرار هنا من الـenvironment، مش من الـDB.** الـDB قابلة
    # للقراءة والتعديل من لوحة الأدمن، والـ`.env` لأ. لو حطّينا الـkey
    # في الجدول كان كل-admin عنده صلاحية يقراه من أي شاشة. هنا
    # `get_api_key()` هي الدالة الوحيدة المسموح لها تقرأ
    # `evolution_api_key`، و`get_vapid_public_key_browser`-analog
    # بتاعنا (`masked_...`) بيرجّع قناع مش قيمة.
    whatsapp_enabled: bool = False
    #: `evolution` هو المزوّد الوحيد المنفّذ دلوقتي. القيمة موجودة
    #: كعمود صراحةً عشان لما ييجى مزوّد رسمي نضيفه من غير ما نغيّر
    #: شكل البيانات ولا الـmigration.
    whatsapp_provider: str = "evolution"
    evolution_base_url: str | None = None
    evolution_instance: str | None = None
    evolution_api_key: str | None = None
    #: مهلة الطلب الثابتة لكل نداء مزوّد. من غيرها مزوّد معلّق
    #: بيبطّل حملة كاملة — وHTTP request معلّق مفيش حد بيعرف.
    whatsapp_request_timeout_seconds: float = 20.0
    #: رقم بتبعتله رسالة الاختبار. لازم يكون **رقمك أنت** — مش رقم
    #: عميل. رسالة اختبار على رقم عميل = دوسة على عميل حقيقي.
    whatsapp_test_phone: str | None = None
    whatsapp_test_message: str = "رسالة اختبار"
    #: يسمح بالاتصال بـloopback بس (`127.0.0.1` / `::1`) للتطوير
    #: على Evolution محلي. **مش كل الشبكة الداخلية** — الاستثناء
    #: اتضيّق عشان كان بي.skip الفحص كله، فأي حد يقدر يوجّه
    #: السيرفر لـ`169.254.169.254`.
    #:
    #: الافتراضي `false` = مقفول. في production الاستثناء ده
    #: **بيتتجاهل بالكامل** حتى لو حد wrote `true` بالغلط.
    whatsapp_allow_private_hosts: bool = False
    #: النطاقات المسموح الاتصال بيها وقت التشغيل.
    #: **قائمة أمان مش تفضيل** — نفس منطق `push_allowed_endpoint_hosts`.
    #: من غيرها أي نطاق برّه المتصفح المزوّد مسموح. `*` مرفوض.
    whatsapp_allowed_hosts: str = ""

    # Phase 0 — سلوك الكوكيز في الإنتاج
    # لو True، التطبيق بيفشل في الإقلاع في production لو الكوكيز مش secure.
    # ده يمنع نشر production على HTTP عادي بالغلط.
    allow_insecure_cookies_in_production: bool = False
    # كوكيز SameSite — lax افتراضيًا. "strict" أدق لكنه بيكسر
    # الوصول عن طريق لينك جاي من موقع تاني.
    cookie_samesite: str = "lax"

    # Phase 0 — اللوجات
    log_level: str = "INFO"
    # "json" للـaggregators، "text" للقراءة
    log_format: str = "text"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def is_production(self) -> bool:
        """هل نعامل التطبيق على إنه production؟

        Default آمن: أي اسم بيئة مش معروف بيتعامل معاه على إنه production.
        """
        return self.app_env.strip().lower() not in NON_PRODUCTION_ENV_NAMES

    @model_validator(mode="after")
    def _validate_production_secrets(self) -> "Settings":
        if self.is_production:
            if not self.app_secret_key or self.app_secret_key == "change-me":
                raise ValueError(
                    "APP_SECRET_KEY must be set to a strong random value in production. "
                    "Generate one with: python -c 'import secrets; print(secrets.token_hex(32))'"
                )
            if not self.admin_password or self.admin_password == "admin12345":
                raise ValueError(
                    "ADMIN_PASSWORD must be set to a strong value in production. "
                    "The default 'admin12345' is not allowed in production mode."
                )
            if len(self.admin_password) < 8:
                raise ValueError(
                    "ADMIN_PASSWORD must be at least 8 characters in production."
                )
        return self

    @model_validator(mode="after")
    def _validate_production_cookies(self) -> "Settings":
        if self.is_production and not self.cookie_secure:
            if not self.allow_insecure_cookies_in_production:
                raise ValueError(
                    "COOKIE_SECURE must be true in production: session cookies would "
                    "otherwise be sent over plain HTTP. Terminate TLS in front of the "
                    "app and keep COOKIE_SECURE=true, or set "
                    "ALLOW_INSECURE_COOKIES_IN_PRODUCTION=true only for a deliberate "
                    "plain-HTTP staging environment."
                )
        return self

    @model_validator(mode="after")
    def _validate_production_logging(self) -> "Settings":
        """لوج JSON في production: الدخول لـaggregators بيحصل سطر-سطر، والنص
        بيضيع فيه. مش fail-fast — تحذير فقط عشان ما نبوّضش نشر بسبب log format."""
        if self.is_production and self.log_format.strip().lower() not in JSON_LOG_FORMATS:
            logger.warning(
                "LOG_FORMAT is %r in production; a collector will not parse it. "
                "Use 'json' if logs are shipped anywhere.",
                self.log_format,
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


def is_allowed_push_endpoint_host(host: str) -> bool:
    """هل `host` من مزوّدي الـpush المسموح لهم؟

    **المقارنة بتطابق النطاق بالكامل**، مش `endswith`. لو استعملنا
    `endswith("googleapis.com")`، النطاق `evilgoogleapis.com` هيعدّي
    — وده بالظبط اللي الـallowlist موجود عشان يمنعه. كمان
    `attacker.com/?x=fcm.googleapis.com` مش هيمرّي لأن `urlparse`
    بيرجّع الـhost بس من غير الـpath.

    النطاق الفرعي بيمرّي: `fcm.googleapis.com` و
    `sub.fcm.googleapis.com` —     نطاقات المزوّدين بيتمددوا، والنطاقات الخطرين بتكسر.
    """
    normalized = (host or "").strip().lower().rstrip(".")
    if not normalized:
        return False
    allowed = settings.push_allowed_endpoint_hosts
    for candidate in allowed.split(","):
        candidate = candidate.strip().lower().rstrip(".")
        if not candidate:
            continue
        if normalized == candidate or normalized.endswith("." + candidate):
            return True
    return False


# ============================================================
# VAPID — Phase 3 (Web Push)
# ============================================================
#
# مفاتيح VAPID في `.env` لازم تكون **base64url** وده نفس الإخراج اللي
# `web-push` بتولّده (المفتاح العام = 65 بايت: `04` + نقطة P-256 غير
# مضغوطة، من غير padding). `pywebpush` بيشتغل بـPEM، فبنحوّل هنا مرة واحدة
# بدل ما نخزّن PEM في `.env` ونخلط صيغتين.
#
# **ليه التحويل في كود التطبيق مش في `.env`:** لو حُفظ المفتاح كـPEM في
# `.env`، أي حد بيفتح الملف هيشوف OpenSSH-style header، والنسخ واللصق بيتكسر
# بسهولة. base64url سطر واحد مفيش فيه سطور تتلخبط.
#
# `vapid_public_key` في `.env` مضاعف 32 بايت لـX (سطح المنحنى) وده
# المطلوب من الـbrowser — مش النقطة الكاملة.

# بايت واحد بيفرق: النقطة غير المضغوطة بتبدأ بـ0x04. المتصفح بيستقبل
# الـpublic key من الـ`applicationServerKey` وبيلفّها بنفسه، فلازم نديه
# 65 بايت (مع الـprefix) مش 64.
_UNCOMPRESSED_POINT_PREFIX = b"\x04"


def _b64url_decode(value: str) -> bytes:
    """فك ترميز base64url مع/من الـpadding."""
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def normalize_vapid_private_key(value: str) -> str:
    """بيحوّل المفتاح الخاص لصيغة PEM اللي `pywebpush` بيقراها.

    ## الصيغ المقبولة

    `-` **32 بايت raw** (اللي `webpush.generateVapidKeys()` في Node
      بتطلعه). ده الشكل الشائع في التوثيق.
    - **DER/PKCS8 base64url** (اللي `cryptography` بتطلعه). ده اللي
      أغلب أدوات سطر الأوامر بتعمله.

    قبل كنا بنقبل 32 بايت بس، وده كان معناه: أي حد عمل المفاتيح بـ
    `openssl` أو أي Python snippet لقي إشعاراته مش شغّالة، والسبب
    المبين في اللوج كان مش واضح (`32 بايت مطلوب` وهو شايف مفتاح
    48 بايت في الـENV).

    - **PEM جاهز**: نمرّره زي ما هو — وده تفسير "المفتاح الخاص
      متظبَط أصلاً" حرفياً.
    """
    stripped = value.strip()
    if "BEGIN" in stripped and "PRIVATE KEY" in stripped:
        return stripped

    raw = _b64url_decode(stripped)

    if len(raw) == 32:
        # Node web-push style: الرقم السري نفسه كـscalar.
        key = ec.derive_private_key(int.from_bytes(raw, "big"), ec.SECP256R1())
    else:
        # DER/PKCS8. لو مش DER، الخطأ بيبان هنا بالرسالة الصح.
        try:
            key = serialization.load_der_private_key(raw, password=None)
        except Exception as exc:
            raise ValueError(
                f"مفتاح VAPID الخاص لازم يكون 32 بايت raw أو DER/PKCS8، "
                f"فعلي={len(raw)} بايت: {exc}"
            ) from exc
        if not isinstance(key, ec.EllipticCurvePrivateKey):
            raise ValueError("مفتاح VAPID الخاص لازم يكون مفتاح EC")

    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")


def normalize_vapid_public_key(value: str) -> str:
    """بيحوّل public key لـbase64url بالشكل اللي المتصفح بياخده.

    بيقبل الصيغتين اللي في التداول: 64 بايت (نقطة P-256 من غير الـprefix)
    و65 بايت (مع `0x04` في الأول). لو 65، الـprefix بيتشال لأننا بنرجّعه
    لـ`.env`-style value ونضيفه تاني عند التسليم للمتصفح.

    الطبيعي في `.env` هو **64 بايت** (X‖Y بلا prefix). ومعيار `0x04`
    بيتشال **بس** من تمثيل الـ65 بايت، لأن أول بايت في مفتاح 64 بايت
    ممكن يكون `0x04` نفسه من غير ما يبقى prefix.
    """
    stripped = value.strip()
    if stripped.startswith("-----BEGIN PUBLIC KEY"):
        loaded = serialization.load_pem_public_key(stripped.encode("ascii"))
        raw = loaded.public_bytes(
            serialization.Encoding.X962,
            serialization.PublicFormat.UncompressedPoint,
        )
        if raw.startswith(_UNCOMPRESSED_POINT_PREFIX):
            raw = raw[1:]
        return _b64url_encode(raw)
    raw = _b64url_decode(stripped)
    # الـprefix بيتشال **بس** من النقطة غير المضغوطة الحقيقية (65 بايت).
    # مفتاح X‖Y خام (64 بايت) ممكن يبدأ بـ0x04 بالصدفة، وشيله بيقصّره
    # لـ63 بايت وبيرفضه — وده كان بيطفي الإشعارات عشوائياً (~1/256).
    if len(raw) == 65 and raw.startswith(_UNCOMPRESSED_POINT_PREFIX):
        raw = raw[1:]
    if len(raw) != 64:
        raise ValueError(f"مفتاح VAPID العام لازم يكون 64 بايت، فعلي={len(raw)}")
    return _b64url_encode(raw)


@lru_cache(maxsize=1)
def get_vapid_private_pem() -> str | None:
    """مفتاح VAPID الخاص بصيغة PEM، أو None لو الإرسال مش مُفعّل.

    **الدالة دي داخلية.** أي حاجة غير `app.push` المفروض ما تستدعيهاش.
    """
    raw = settings.vapid_private_key
    if not raw or not raw.strip():
        return None
    try:
        return normalize_vapid_private_key(raw)
    except Exception:
        logger.exception("vapid_private_key_is_invalid")
        return None


@lru_cache(maxsize=1)
def get_vapid_public_key() -> str | None:
    """مفتاح VAPID **العام** في صيغة `.env` (نقطة 64 بايت، بلا prefix).

    دي القيمة اللي بتتخزّن في `VAPID_PUBLIC_KEY`. **مش** هي اللي
    بتروح للمتصفح — للـbrowser شوف `get_vapid_public_key_browser`.
    """
    raw = settings.vapid_public_key
    if raw and raw.strip():
        try:
            return normalize_vapid_public_key(raw)
        except Exception:
            logger.exception("vapid_public_key_is_invalid")
            return None
    # لو الـpublic key مش متظبَط: نستخرجه من الخاص. أحسن من إنك تقعد
    # تشتغل من غير إشعارات، وبيفضل صحيح لأنه جاي من نفس الـprivate key.
    try:
        pem = get_vapid_private_pem()
        if not pem:
            return None
        loaded = serialization.load_pem_private_key(pem.encode("ascii"), password=None)
        raw_point = loaded.public_key().public_bytes(
            serialization.Encoding.X962,
            serialization.PublicFormat.UncompressedPoint,
        )
        return _b64url_encode(raw_point[1:])
    except Exception:
        logger.exception("vapid_public_key_derivation_failed")
        return None


@lru_cache(maxsize=1)
def get_vapid_public_key_browser() -> str | None:
    """المفتاح العام بالشكل اللي **`pushManager.subscribe()` بياخده**.

    ## ليه الدالة دي موجودة أصلاً

    `applicationServerKey` بيبقى **النقطة غير المضغوطة كاملة = 65 بايت**:
    `0x04` + X (32) + Y (32). الـ`VAPID_PUBLIC_KEY` في `.env` بيتخزّن
    64 بايت (X+Y بس) عشان مينفعش نقارنه مع أي حاجة ونفسه في كل الأدوات
    اللي بتطبعه.

    من غير الـ`0x04`، `subscribe()` بيرمي `InvalidAccessError` في Chrome
    و Firefox، والعَرَض: المستخدم يضغط "فعّل"، يقرأ الإذن granted، وبعدين
    **مفيش إشعار ولا رسالة خطأ**. أصعب نوع bug في الميزة دي كلها.
    """
    normalized = get_vapid_public_key()
    if not normalized:
        return None
    raw = _b64url_decode(normalized)
    # `get_vapid_public_key` مضمون **64 بايت (X‖Y) من غير prefix**، فبندّي
    # المتصفح النقطة كاملة بـ`0x04` في الأول دايماً. الفحص القديم
    # (`if not raw.startswith(0x04)`) كان بيسيب المفتاح 64 بايت لما أول
    # بايت من X يطلع `0x04` بالصدفة (~1/256)، و`subscribe()` بيقلب
    # InvalidAccessError من غير أي رسالة واضحة للمستخدم.
    return _b64url_encode(_UNCOMPRESSED_POINT_PREFIX + raw)


def push_is_configured() -> bool:
    """هل الإرسال ممكن أصلاً؟ بيتسأل في كل UI بيتحكم في زر الإشعارات.

    **`vapid_subject` جزء من الشرط** مش رفاهية: RFC 8292 بتقول الـ`sub`
    claim مطلوب، وFCM/Mozilla بيرفضوا الطلب كله من غيره بـ401. من غير
    الفحص ده كان كل زر الإشعارات هيقول "مفعّل" وكل إرسال هيفشل بصمت
    في اللوج.
    """
    subject = (settings.vapid_subject or "").strip()
    if not subject:
        return False
    return bool(get_vapid_private_pem() and get_vapid_public_key())


settings = get_settings()