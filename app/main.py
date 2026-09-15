'''
Application entry point with security middleware.
'''
import os
import time
from collections import defaultdict
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.routers import admin, admin_auth, api, public
from app.security import CSRF_COOKIE_NAME, create_csrf_token, verify_csrf_token

BASE_DIR = Path(__file__).resolve().parent
UPLOADS_DIR = BASE_DIR / "uploads"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title=settings.app_name)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
app.mount("/uploads", StaticFiles(directory=UPLOADS_DIR), name="uploads")


@app.get("/health", include_in_schema=False)
def health() -> JSONResponse:
    return JSONResponse({"status": "ok"})


def _is_admin_path(path: str) -> bool:
    return path == "/admin" or path.startswith("/admin/")


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
            samesite="lax",
        )
    return response


app.include_router(public.router)
app.include_router(api.router)
app.include_router(admin_auth.router)
app.include_router(admin.router)