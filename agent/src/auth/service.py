"""Auth service: password hashing, JWT, user CRUD, and dev-mode fallback.

Design notes:
- When a database is configured (``DATABASE_URL`` set) AND a ``JWT_SECRET`` is
  present, full multi-user auth is active. ``is_auth_enabled()`` returns True.
- When either is missing, auth is inert: the server trusts loopback clients and
  treats all non-loopback requests as a single implicit ``system`` user only if
  they present the legacy shared ``API_AUTH_KEY`` (preserving prior behaviour).
- A persistent ``system`` user row is lazily created in the DB so that
  loopback/legacy access still gets a stable ``user_id`` for data namespacing.
"""

from __future__ import annotations

import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt
from sqlalchemy import select

from src.db.base import get_session, is_db_enabled
from src.db.models import User

logger = logging.getLogger(__name__)

# Stable id for the implicit dev-mode / loopback / legacy system user.
# A hex UUID-like constant keeps it compatible with the per-user storage layout.
SYSTEM_USER_ID = "00000000000000000000000000000000"
SYSTEM_USER_EMAIL = "system@local"

# JWT algorithm and claim keys.
_JWT_ALGORITHM = "HS256"
_TOKEN_TYPE_ACCESS = "access"
_TOKEN_TYPE_REFRESH = "refresh"


class AuthError(Exception):
    """Raised for authentication/authorization failures (maps to HTTP 401/409)."""


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------

def _jwt_secret() -> str:
    return os.getenv("JWT_SECRET", "").strip()


def _access_expire_delta() -> timedelta:
    minutes = int(os.getenv("JWT_ACCESS_EXPIRE_MINUTES", "30"))
    return timedelta(minutes=minutes)


def _refresh_expire_delta() -> timedelta:
    days = int(os.getenv("JWT_REFRESH_EXPIRE_DAYS", "7"))
    return timedelta(days=days)


def is_auth_enabled() -> bool:
    """Return True when full multi-user JWT auth is active.

    Requires both a database backend and a JWT signing secret.
    """
    return is_db_enabled() and bool(_jwt_secret())


def _legacy_api_key_enabled() -> bool:
    """Return whether the legacy shared API_AUTH_KEY path is permitted."""
    return os.getenv("VIBE_TRADING_LEGACY_API_KEY", "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    """Return a bcrypt hash of *password* (UTF-8, cost 12)."""
    salt = bcrypt.gensalt(rounds=12)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    """Return True if *password* matches *hashed* (constant-time)."""
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------

def _encode_token(user_id: str, token_type: str, delta: timedelta) -> str:
    secret = _jwt_secret()
    if not secret:
        raise AuthError("JWT auth is not configured (missing JWT_SECRET)")
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "type": token_type,
        "iat": int(now.timestamp()),
        "exp": int((now + delta).timestamp()),
        "jti": secrets.token_urlsafe(16),
    }
    return jwt.encode(payload, secret, algorithm=_JWT_ALGORITHM)


def create_access_token(user_id: str) -> str:
    return _encode_token(user_id, _TOKEN_TYPE_ACCESS, _access_expire_delta())


def create_refresh_token(user_id: str) -> str:
    return _encode_token(user_id, _TOKEN_TYPE_REFRESH, _refresh_expire_delta())


def decode_token(token: str) -> dict:
    """Decode and verify a JWT. Raises AuthError on any failure."""
    secret = _jwt_secret()
    if not secret:
        raise AuthError("JWT auth is not configured (missing JWT_SECRET)")
    try:
        return jwt.decode(token, secret, algorithms=[_JWT_ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("Token expired") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthError("Invalid token") from exc


def decode_access_token(token: str) -> str:
    """Verify an access token and return the subject user_id."""
    payload = decode_token(token)
    if payload.get("type") != _TOKEN_TYPE_ACCESS:
        raise AuthError("Not an access token")
    user_id = payload.get("sub")
    if not user_id:
        raise AuthError("Token missing subject")
    return user_id


def decode_refresh_token(token: str) -> str:
    """Verify a refresh token and return the subject user_id."""
    payload = decode_token(token)
    if payload.get("type") != _TOKEN_TYPE_REFRESH:
        raise AuthError("Not a refresh token")
    user_id = payload.get("sub")
    if not user_id:
        raise AuthError("Token missing subject")
    return user_id


# ---------------------------------------------------------------------------
# User CRUD
# ---------------------------------------------------------------------------

def _normalize_email(email: str) -> str:
    return email.strip().lower()


def get_user_by_id(user_id: str) -> Optional[User]:
    with get_session() as session:
        if session is None:
            return None
        return session.get(User, user_id)


def get_user_by_email(email: str) -> Optional[User]:
    with get_session() as session:
        if session is None:
            return None
        stmt = select(User).where(User.email == _normalize_email(email))
        return session.execute(stmt).scalar_one_or_none()


def create_user(email: str, password: str, name: str = "") -> User:
    """Register a new user. Raises AuthError on duplicate email."""
    if not is_auth_enabled():
        raise AuthError("User registration is disabled (auth not configured)")
    if len(password) < 8:
        raise AuthError("Password must be at least 8 characters")
    normalized = _normalize_email(email)
    if get_user_by_email(normalized) is not None:
        raise AuthError("Email already registered", )

    user = User(
        email=normalized,
        name=name.strip(),
        hashed_password=hash_password(password),
    )
    with get_session() as session:
        if session is None:
            raise AuthError("Database unavailable")
        session.add(user)
        session.flush()
        # Detach so the object remains usable after session close.
        session.refresh(user)
        session.expunge(user)
    return user


def authenticate(email: str, password: str) -> User:
    """Verify credentials and return the user. Raises AuthError on failure."""
    user = get_user_by_email(email)
    if user is None or not verify_password(password, user.hashed_password):
        raise AuthError("Invalid email or password")
    if not user.is_active:
        raise AuthError("Account is disabled")
    return user


# ---------------------------------------------------------------------------
# Dev-mode system user (loopback / legacy API key)
# ---------------------------------------------------------------------------

def get_system_user() -> User:
    """Return an in-memory system user for inert/loopback/legacy access.

    No DB row is required; this keeps data namespacing stable without a DB.
    """
    return User(
        id=SYSTEM_USER_ID,
        email=SYSTEM_USER_EMAIL,
        name="System",
        hashed_password="",  # never used for login
        is_active=True,
    )


def get_or_create_system_user() -> User:
    """Return the system user. When a DB is present, ensure a row exists.

    This gives loopback/legacy clients a stable ``user_id`` for per-user
    storage even in multi-user deployments.
    """
    if not is_db_enabled():
        return get_system_user()
    with get_session() as session:
        if session is None:
            return get_system_user()
        existing = session.get(User, SYSTEM_USER_ID)
        if existing is None:
            system = User(
                id=SYSTEM_USER_ID,
                email=SYSTEM_USER_EMAIL,
                name="System",
                hashed_password="!",  # invalid hash → cannot login
                is_active=True,
            )
            session.add(system)
            session.flush()
            session.refresh(system)
            session.expunge(system)
            return system
        session.expunge(existing)
        return existing


def resolve_legacy_api_key(token: str) -> Optional[User]:
    """If legacy API key mode is enabled, validate *token* against API_AUTH_KEY.

    Returns the system user on match, else None.
    """
    if not _legacy_api_key_enabled():
        return None
    import hmac

    expected = os.getenv("API_AUTH_KEY", "").strip()
    if not expected:
        return None
    if hmac.compare_digest(token, expected):
        return get_or_create_system_user()
    return None
