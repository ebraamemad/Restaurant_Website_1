'''
Application entry point with security middleware.
'''
import logging
import os
import re
import time
import uuid
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import engine, get_db
from app.logging_config import configure_logging, request_id_var
from app.routers import admin, admin_auth, api, marketing, public
from app.security import CSRF_COOKIE_NAME, create_csrf_token, verify_csrf_token

configure_logging()
logger = logging.getLogger("app.main")

BASE_DIR = Path(__file__).resolve().parent
UPLOADS_DIR = BASE_DIR / "uploads"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
SW_FILE = BASE_DIR / "static" / "js" / "sw.js"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Uvicorn بيقبل --forwarded-allow-ips. من غيره، أي reverse proxy قدام
    # التطبيق بيخلي كل الطلبات تبان جاية من 127.0.0.1، والـrate limiting
    # يبقى بلا معنى.
    forwarded_allow_ips = os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1")
    logger.info("uvicorn forwarded_allow_ips=%s", forwarded_allow_ips)
    yield
    engine.dispose()


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
app.mount("/uploads", StaticFiles(directory=UPLOADS_DIR), name="uploads")


@app.get("/health", include_in_schema=False)
def health() -> JSONResponse:
    """Liveness probe — قصير ومفيش dependencies.

    متعمد إنه ما يتكلمش مع الداتابيز: لو الـcontainer بيستجيب، التطبيق
    شغال. مشاكل الداتابيزReady بدل health.
    """
    return JSONResponse({"status": "ok"})


@app.get("/ready", include_in_schema=False)
def ready(db: Session = Depends(get_db)) -> JSONResponse:
    """Readiness probe — بيتأكد إن التطبيق يقدر يخدم فعليًا دلوقتي.

    بيستخدم نفس الـpool، فلو الـDB مش راد بيرجّع 503 والـload balancer
    يوجّه traffic لمكان تاني.
    """
    started = time.perf_counter()
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        logger.exception("readiness_db_check_failed", extra={"event": "readiness_db_check_failed"})
        return JSONResponse(
            {"status": "unavailable", "detail": "database unreachable"},
            status_code=503,
        )
    logger.info(
        "readiness_ok",
        extra={
            "event": "readiness_ok",
            "duration_ms": round((time.perf_counter() - started) * 1000, 2),
        },
    )
    return JSONResponse({"status": "ready"})


@app.get("/sw.js", include_in_schema=False)
def service_worker() -> FileResponse:
    """تقديم service worker من root range.

    scope الـservice worker بيتحدد بالمسار اللي الملف اتserve منه. لو
    اتserve من /static/js/sw.js، الـscope بيبقى /static/js/ — فمبتشتغلش على
    صفحات الـHTML أصلاً، وده كان سبب الـoffline cache ما بيشتغلش.
    """
    return FileResponse(
        SW_FILE,
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
    )


def _is_admin_path(path: str) -> bool:
    return path == "/admin" or path.startswith("/admin/")


def _is_api_path(path: str) -> bool:
    return path == "/api" or path.startswith("/api/")


# مسارات بيرجّعوا JSON/ملف مش HTML — متحوّلهاش لصفحة 404.
_NON_HTML_PREFIXES = ("/static/", "/uploads/")
_NON_HTML_PATHS = frozenset(
    {
        "/docs",
        "/redoc",
        "/openapi.json",
        "/docs/oauth2-redirect",
        "/health",
        "/ready",
        "/sw.js",
        "/manifest.json",
    }
)


def _wants_html_404(path: str) -> bool:
    if _is_api_path(path) or _is_admin_path(path):
        return False
    if path in _NON_HTML_PATHS:
        return False
    if path.startswith(_NON_HTML_PREFIXES):
        return False
    return True


@app.exception_handler(404)
async def not_found_handler(request: Request, exc: Exception) -> Response:
    """صفحات العميل بتاخد صفحة 404HTML، والـAPI بتفضل JSON.

    من غير ده، أي لينك قديم أو صنف اتشال من القائمة بيرجّع
    `application/json` للعميل — يعني HTML خام في نص الصفحة.
    """
    if not _wants_html_404(request.url.path):
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    db = None
    try:
        db = next(get_db())
        restaurant_settings = public.get_settings(db)
    except Exception:
        logger.exception("public_404_settings_lookup_failed")
        restaurant_settings = None
    finally:
        if db is not None:
            db.close()
    if restaurant_settings is None:
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    return public.templates.TemplateResponse(
        request=request,
        name="public/404.html",
        context={"request": request, "settings": restaurant_settings},
        status_code=404,
    )


# ---------------------------------------------------------------------------
# Rate limiting (in-memory, fixed-window)
# ---------------------------------------------------------------------------
# Simple in-memory rate limiter suitable for single-instance deployments.
# For multi-instance / distributed setups, replace with Redis-backed limiter.

_RATE_LIMIT_WINDOW_SECONDS = 60
_RATE_LIMIT_MAX_REQUESTS = int(os.environ.get("RATE_LIMIT_MAX_REQUESTS", "200"))
_rate_limit_store: dict[str, list[float]] = defaultdict(list)


def reset_rate_limits() -> None:
    """Clear all rate limit counters. Useful for testing."""
    _rate_limit_store.clear()


def _rate_limit_key(request: Request) -> str:
    """Build a rate-limit key from client IP + method + path."""
    client_ip = request.client.host if request.client else "unknown"
    return f"{client_ip}:{request.method}:{request.url.path}"


def _check_rate_limit(key: str) -> bool:
    """Return True if the request is allowed, False if rate-limited."""
    now = time.monotonic()
    window_start = now - _RATE_LIMIT_WINDOW_SECONDS
    timestamps = _rate_limit_store[key]
    # Prune old entries
    timestamps[:] = [t for t in timestamps if t > window_start]
    if len(timestamps) >= _RATE_LIMIT_MAX_REQUESTS:
        return False
    timestamps.append(now)
    return True


# Paths that should be rate-limited (public order submission + coupon validation)
_RATE_LIMITED_PATHS = frozenset({
    "/orders",
    "/api/orders/preview",
    "/api/coupons/validate",
})


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    # Skip rate limiting in non-production environments to allow testing
    if settings.app_env != "production":
        return await call_next(request)
    path = request.url.path
    if request.method in ("POST", "PUT", "PATCH") and path in _RATE_LIMITED_PATHS:
        key = _rate_limit_key(request)
        if not _check_rate_limit(key):
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Rate limit exceeded. Please try again later.",
                    "retry_after": _RATE_LIMIT_WINDOW_SECONDS,
                },
                headers={"Retry-After": str(_RATE_LIMIT_WINDOW_SECONDS)},
            )
    return await call_next(request)


@app.middleware("http")
async def admin_csrf_cookie(request: Request, call_next):
    path = request.url.path
    state_token = None
    need_cookie = False
    if _is_admin_path(path):
        cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
        if cookie_token and verify_csrf_token(cookie_token):
            state_token = cookie_token
        else:
            state_token = create_csrf_token()
            need_cookie = True
        request.state.csrf_token = state_token

    response = await call_next(request)

    if need_cookie and state_token:
        response.set_cookie(
            CSRF_COOKIE_NAME,
            state_token,
            httponly=False,
            secure=settings.cookie_secure,
            samesite=settings.cookie_samesite,
        )
    return response


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Headers حماية أساسية على كل response.

    المدة بتاعت التنقل غير المهمة، والـAlpine local بدل CDN — مفيش طلبات
    خارجية من متصفح العميل.
    """
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Permissions-Policy", "geolocation=(), camera=(), microphone=()"
    )
    if settings.is_production:
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    return response


@app.middleware("http")
async def request_logging(request: Request, call_next):
    """لوج لكل طلب: method + path + status + مدة التنفيذ + request id.

    المسار بيتسجل من غير query string، لأن الـquery ممكن يحتوي كلام العميل
    أو بيانات. مفيش PII ولا أسرار في اللوج.
    """
    inbound = request.headers.get("x-request-id", "")
    request_id = re.sub(r"[^A-Za-z0-9_\-]", "", inbound)[:64] or uuid.uuid4().hex
    token = request_id_var.set(request_id)
    started = time.perf_counter()
    try:
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "request_failed",
                extra={
                    "event": "request_failed",
                    "method": request.method,
                    "path": request.url.path,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                },
            )
            raise
        # لازم السطر ده جوه الـtry: لو رجعنا الـContextVar قبل ما نسجل،
        # الـRequestIdFilter هيلاقي اللاج فاضي وrequest_id بيبقى "-".
        logger.info(
            "request",
            extra={
                "event": "request",
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            },
        )
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        request_id_var.reset(token)


app.include_router(public.router)
app.include_router(api.router)
app.include_router(admin_auth.router)
app.include_router(admin.router)
app.include_router(marketing.router)