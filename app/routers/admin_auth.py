from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.deps import verify_admin_csrf
from app.login_guard import (
    LOGIN_ERROR_MESSAGE,
    LOGIN_THROTTLED_MESSAGE,
    check_login_allowed,
    client_ip,
    record_login_failure,
    record_login_success,
)
from app.models import Admin
from app.security import AUTH_COOKIE_NAME, create_access_token, csrf_field, verify_login_credentials

logger = logging.getLogger("app.admin_auth")

router = APIRouter(prefix="/admin", dependencies=[Depends(verify_admin_csrf)])
TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals["csrf_field"] = csrf_field


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("admin/login.html", {"request": request, "error": None})


def _login_error(request: Request, message: str, status_code: int) -> HTMLResponse:
    return templates.TemplateResponse(
        "admin/login.html",
        {"request": request, "error": message},
        status_code=status_code,
    )


@router.post("/login")
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
) -> Response:
    # 1) الإيقاف المؤقت بيت checked قبل أي شغل bcrypt — عشان الهجوم ما
    #    ياخدش CPU بتطبيقنا، والرد أسرع.
    ip = client_ip(request)
    decision = check_login_allowed(db, ip, username)
    if not decision.allowed:
        logger.warning(
            "admin_login_throttled",
            extra={
                "event": "admin_login_throttled",
                "retry_after": decision.retry_after_seconds,
            },
        )
        response = _login_error(request, LOGIN_THROTTLED_MESSAGE, status.HTTP_429_TOO_MANY_REQUESTS)
        response.headers["Retry-After"] = str(decision.retry_after_seconds)
        return response

    # 2) بنقارن بنفس الشغل (bcrypt) سواء اليوزر موجود أو لأ، فالتوقيت
    #    ما بيفضحش مين موجود.
    admin = db.scalar(select(Admin).where(Admin.username == username, Admin.is_active.is_(True)))
    if not verify_login_credentials(password, admin.password_hash if admin else None):
        record_login_failure(db, ip, username)
        db.commit()
        # نفس الرسالة في كل الأحوال: باسورد غلط، يوزر مش موجود،
        # أو حساب معطّل — مفيش استنتاج ممكن.
        return _login_error(request, LOGIN_ERROR_MESSAGE, status.HTTP_400_BAD_REQUEST)

    # 3) دخول ناجح: نمسح عدّاد هذا الـkey فورًا.
    record_login_success(db, ip, username)
    db.commit()

    response = RedirectResponse("/admin", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        AUTH_COOKIE_NAME,
        create_access_token(admin.username),
        httponly=True,
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        max_age=settings.access_token_expire_minutes * 60,
    )
    return response


@router.post("/logout")
def logout() -> Response:
    response = RedirectResponse("/admin/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(AUTH_COOKIE_NAME, samesite=settings.cookie_samesite)
    return response
