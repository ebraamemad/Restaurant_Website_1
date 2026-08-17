from __future__ import annotations

from typing import Optional
from typing_extensions import Annotated

from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Admin
from app.security import AUTH_COOKIE_NAME, decode_access_token

DbSession = Annotated[Session, Depends(get_db)]


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
