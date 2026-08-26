from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Request
from jose import JWTError, jwt
from markupsafe import Markup
from passlib.context import CryptContext

from app.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
ALGORITHM = "HS256"
AUTH_COOKIE_NAME = "admin_access_token"
CSRF_COOKIE_NAME = "admin_csrf"


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, password_hash: str) -> bool:
    return pwd_context.verify(plain_password, password_hash)


def create_access_token(subject: str) -> str:
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_expire_minutes)
    payload = {"sub": subject, "exp": expires_at}
    return jwt.encode(payload, settings.app_secret_key, algorithm=ALGORITHM)


def decode_access_token(token: str) -> Optional[str]:
    try:
        payload = jwt.decode(token, settings.app_secret_key, algorithms=[ALGORITHM])
    except JWTError:
        return None
    subject = payload.get("sub")
    return subject if isinstance(subject, str) else None


def _csrf_signature(nonce: str) -> str:
    return hmac.new(settings.app_secret_key.encode(), nonce.encode(), hashlib.sha256).hexdigest()


def create_csrf_token() -> str:
    nonce = secrets.token_hex(16)
    return f"{nonce}.{_csrf_signature(nonce)}"


def verify_csrf_token(token: Optional[str]) -> bool:
    if not token or "." not in token:
        return False
    nonce, signature = token.rsplit(".", 1)
    if not nonce:
        return False
    return hmac.compare_digest(signature, _csrf_signature(nonce))


def csrf_field(request: Request) -> Markup:
    token = getattr(request.state, "csrf_token", "") or ""
    return Markup(f'<input type="hidden" name="csrf_token" value="{token}">')
