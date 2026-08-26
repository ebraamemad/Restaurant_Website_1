from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.deps import verify_admin_csrf
from app.models import Admin
from app.security import AUTH_COOKIE_NAME, create_access_token, csrf_field, verify_password

router = APIRouter(prefix="/admin", dependencies=[Depends(verify_admin_csrf)])
TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals["csrf_field"] = csrf_field


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse("admin/login.html", {"request": request, "error": None})


@router.post("/login")
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
) -> Response:
    admin = db.scalar(select(Admin).where(Admin.username == username, Admin.is_active.is_(True)))
    if admin is None or not verify_password(password, admin.password_hash):
        return templates.TemplateResponse("admin/login.html", {"request": request, "error": "بيانات الدخول غير صحيحة"}, status_code=400)

    response = RedirectResponse("/admin", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        AUTH_COOKIE_NAME,
        create_access_token(admin.username),
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=settings.access_token_expire_minutes * 60,
    )
    return response


@router.post("/logout")
def logout() -> Response:
    response = RedirectResponse("/admin/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(AUTH_COOKIE_NAME)
    return response
