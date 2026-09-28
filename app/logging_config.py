"""إعداد اللوجات للتطبيق.

الهدف observability خفيف و production-friendly بدون أي منصة monitoring:
- كل سطر لوج بيطلع على stdout (مناسب لـ Docker logs و log collectors).
--formatter نصي للقراءة، أو JSON سطر-سطر للتجميع.
- request id بيتحقن في كل لوج عشان تقدر تتبّع طلب واحد من أوله لآخره.
- مفيش أي تسجيل لكلمات مرور أو توكنز أو أسرار أو أرقام عملاء كاملة.
  الأرقام بتتمويه عبر app.utils.mask_phone.
"""

from __future__ import annotations

import contextvars
import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any, Dict

from app.config import settings

# بيتحفظ في context خاص بكل request، فأي لوج بيتكتب جوه request بيلاقي الـid
# متاح من غير ما نمرّره يدوي في كل مكان.
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")

# حقول LogRecord القياسية اللي بننسخها الأول. أي حاجة تانية تتحط في
# extra بتظهر كـfield مستقل في الـJSON.
_RESERVED_RECORD_FIELDS = frozenset(
    {
        "args", "asctime", "created", "exc_info", "exc_text", "filename", "funcName",
        "levelname", "levelno", "lineno", "message", "module", "msecs", "msg", "name",
        "pathname", "process", "processName", "relativeCreated", "stack_info",
        "thread", "threadName", "taskName",
    }
)


class RequestIdFilter(logging.Filter):
    """يحط request_id على كل record قبل ما يتنسّق."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


def _extras(record: logging.LogRecord) -> Dict[str, Any]:
    return {
        key: value
        for key, value in record.__dict__.items()
        if key not in _RESERVED_RECORD_FIELDS and key != "request_id" and not key.startswith("_")
    }


class JsonFormatter(logging.Formatter):
    """سطر JSON واحد لكل لوج — مناسب لـLoki / Elasticsearch / CloudWatch."""

    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        payload.update(_extras(record))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class TextFormatter(logging.Formatter):
    """تنسيق مقروء للعين، مع الـextras في آخر السطر."""

    default_time_format = "%Y-%m-%dT%H:%M:%S%z"

    def __init__(self, fmt: str | None = None, datefmt: str | None = None) -> None:
        super().__init__(
            fmt or "%(asctime)s %(levelname)-7s [%(request_id)s] %(name)s: %(message)s",
            datefmt or self.default_time_format,
        )

    def format(self, record: logging.LogRecord) -> str:
        # الـfilter بيحط الـrequest_id، بس لو حد استخدم الـformatter من غير
        # الـfilter (اختبار، أو logger جديد) ما nenefshش على KeyError.
        if not hasattr(record, "request_id"):
            record.request_id = request_id_var.get()
        base = super().format(record)
        extras = _extras(record)
        if not extras:
            return base
        rendered = " ".join(f"{key}={value}" for key, value in extras.items())
        return f"{base} | {rendered}"


def configure_logging() -> None:
    """/setup اللوجات على root logger. آمن للاستدعاء أكتر من مرة.

    مهم: بنشيل من root بس الـhandlers اللي إحنا ركبناهم (المعلّمين
    بعلامة خاصة). أي handler تاني موجود — زي اللي pytest بيركبه عشان
    يلتقط اللوج في الاختبارات، أو أي host بيظبط logging بنفسه — بيتساب
    زي ما هو. المسح الأعمى كان بيكسر الاختبارات بصمت.
    """
    level_name = (settings.log_level or "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        JsonFormatter() if settings.log_format.lower() == "json" else TextFormatter()
    )
    handler.addFilter(RequestIdFilter())
    handler._restaurant_handler = True  # type: ignore[attr-defined]

    root = logging.getLogger()
    # نستبدل بتاعنا لو موجود، من غير ما نلمس أي حاجة تانية.
    for existing in list(root.handlers):
        if getattr(existing, "_restaurant_handler", False):
            root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)

    _reenable_app_loggers()

    # بنعمل access logging بأنفسنا (فيه request_id ومدة الطلب)، فبنوقف
    # لوج uvicorn.access عشان مفيش تكرار.
    logging.getLogger("uvicorn.access").disabled = True
    # أخطاء uvicorn بتفضل تمر على root handler بالتنسيق بتاعنا.
    for name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.propagate = True


def _reenable_app_loggers() -> None:
    """بيرجّع الـloggers بتاعتنا لو حد عطّلها.

    `alembic upgrade head` بيقرأ `alembic.ini` وبيستدعي
    `logging.config.fileConfig`، ودي بـdefault بتعطّل كل الـloggers
    الموجودة بالفعل (`disable_existing_loggers=True`). النتيجة إن أول
    migration تشغّلها في نفس العملية بتمسح كل لوجات `app.*` بصمت
    ومحدش بياخد باله.

    إحنا أصحاب الـloggers دي، فبنرجّعها. بنمسّ غير اللي تحت نطاقنا.
    """
    manager = logging.Logger.manager
    for name, logger in list(manager.loggerDict.items()):
        if name == "app" or name.startswith("app."):
            if isinstance(logger, logging.Logger) and logger.disabled:
                logger.disabled = False
                logger.propagate = True
