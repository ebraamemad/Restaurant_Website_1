from __future__ import annotations

import hmac
from typing import Optional
from typing_extensions import Annotated

from fastapi import Cookie, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Admin
from app.security import AUTH_COOKIE_NAME, CSRF_COOKIE_NAME, decode_access_token, verify_csrf_token

DbSession = Annotated[Session, Depends(get_db)]
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def get_current_admin(
    db: DbSession,
    token: Annotated[Optional[str], Cookie(alias=AUTH_COOKIE_NAME)] = None,
) -> Admin:
    if not token:
        raise HTTPException(status.HTTP_303_SEE_OTHER, headers={"Location": "/admin/login"})
    username = decode_access_token(token)
    if username is None:
        raise HTTPException(status.HTTP_303_SEE_OTHER, headers={"Location": "/admin/login"})
    admin = db.scalar(select(Admin).where(Admin.username == username, Admin.is_active.is_(True)))
    if admin is None:
        raise HTTPException(status.HTTP_303_SEE_OTHER, headers={"Location": "/admin/login"})
    return admin


async def verify_admin_csrf(request: Request) -> None:
    # يقرأ الـform داخل الـdependency: هنا الكاش على نفس request الخاص
    # بالـhandler فيستمر عمل Form(...) بعد ذلك بدون مشاكل.
    if request.method not in UNSAFE_METHODS:
        return
    sent = request.headers.get("x-csrf-token")
    if not sent:
        try:
            form = await request.form()
            sent = form.get("csrf_token")
        except Exception:
            sent = None
    cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
    if not (sent and cookie_token and verify_csrf_token(sent) and hmac.compare_digest(sent, cookie_token)):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "CSRF token missing or invalid")
