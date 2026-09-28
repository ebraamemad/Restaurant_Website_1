'''
Settings configuration with production safety validation.
'''
import logging
from functools import lru_cache

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


settings = get_settings()