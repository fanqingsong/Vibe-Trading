"""FastAPI auth routes: register / login / refresh / me.

Also exposes the dependency callables used by protected endpoints across the
app: ``require_user`` and ``require_user_event_stream``. The registration of the
router itself happens in ``agent/api_server.py`` via ``register_auth_routes``.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src.auth import schemas
from src.auth.service import (
    AuthError,
    authenticate,
    create_access_token,
    create_refresh_token,
    create_user,
    decode_access_token,
    decode_refresh_token,
    get_or_create_system_user,
    get_system_user,
    get_user_by_id,
    is_auth_enabled,
    resolve_legacy_api_key,
)
from src.db.models import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

_security = HTTPBearer(auto_error=False)


def _is_local_client(request: Request) -> bool:
    """Return True for trusted local (loopback / Docker gateway) clients."""
    from src.auth.loopback import request_is_local

    return request_is_local(request)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

def _make_token_pair(user: User) -> schemas.TokenResponse:
    return schemas.TokenResponse(
        access_token=create_access_token(user.id),
        refresh_token=create_refresh_token(user.id),
        user=schemas.UserResponse.from_user(user),
    )


@router.post(
    "/register",
    response_model=schemas.TokenResponse,
    status_code=status.HTTP_201_CREATED,
)
def register(req: schemas.RegisterRequest) -> schemas.TokenResponse:
    """Register a new account and return a token pair."""
    if not is_auth_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="User registration is disabled (auth not configured)",
        )
    try:
        user = create_user(email=req.email, password=req.password, name=req.name)
    except AuthError as exc:
        msg = str(exc)
        code = (
            status.HTTP_409_CONFLICT
            if "already" in msg
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(status_code=code, detail=msg) from exc
    return _make_token_pair(user)


@router.post("/login", response_model=schemas.TokenResponse)
def login(req: schemas.LoginRequest) -> schemas.TokenResponse:
    """Authenticate and return a token pair."""
    if not is_auth_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Login is disabled (auth not configured)",
        )
    try:
        user = authenticate(email=req.email, password=req.password)
    except AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc
    return _make_token_pair(user)


@router.post("/refresh", response_model=schemas.TokenResponse)
def refresh(req: schemas.RefreshRequest) -> schemas.TokenResponse:
    """Exchange a valid refresh token for a new token pair."""
    if not is_auth_enabled():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Auth is not configured",
        )
    try:
        user_id = decode_refresh_token(req.refresh_token)
    except AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc
    user = get_user_by_id(user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account no longer exists or is disabled",
        )
    return _make_token_pair(user)


@router.get("/status")
def auth_status() -> dict:
    """Report whether multi-user auth is active (used by the frontend)."""
    return {"enabled": is_auth_enabled()}


# ---------------------------------------------------------------------------
# Dependencies (shared with the rest of the app)
# ---------------------------------------------------------------------------

def _resolve_user_from_bearer(
    request: Request,
    cred: Optional[HTTPAuthorizationCredentials],
    *,
    allow_query: bool = False,
    query_token: Optional[str] = None,
) -> Optional[User]:
    """Resolve a user from a Bearer JWT (or legacy API key), or None.

    Never raises; callers decide how to handle a missing user.
    """
    token = ""
    if cred and cred.credentials:
        token = cred.credentials
    elif allow_query and query_token:
        token = query_token

    if token:
        # 1) Try JWT when auth is fully configured.
        if is_auth_enabled():
            try:
                user_id = decode_access_token(token)
            except AuthError:
                user_id = None
            if user_id:
                user = get_user_by_id(user_id)
                if user and user.is_active:
                    return user
        # 2) Legacy shared API key path.
        legacy = resolve_legacy_api_key(token)
        if legacy is not None:
            return legacy
    return None


async def require_user(
    request: Request,
    cred: Optional[HTTPAuthorizationCredentials] = Security(_security),
) -> User:
    """Require an authenticated user.

    Resolution order:
    1. Loopback client → implicit system user (dev mode).
    2. Valid JWT access token → that user.
    3. Legacy API_AUTH_KEY (when enabled) → system user.
    4. Otherwise 401.

    The resolved user is injected as ``current_user`` for per-user storage.
    """
    if _is_local_client(request):
        return get_or_create_system_user()

    user = _resolve_user_from_bearer(request, cred)
    if user is not None:
        return user

    if is_auth_enabled():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Authentication required for non-local access",
    )


async def require_user_event_stream(
    request: Request,
    token: Optional[str] = Query(None),
    cred: Optional[HTTPAuthorizationCredentials] = Security(_security),
) -> User:
    """SSE-compatible auth. EventSource cannot set headers, so accept the
    token via query string as a fallback."""
    if _is_local_client(request):
        return get_or_create_system_user()

    user = _resolve_user_from_bearer(
        request, cred, allow_query=True, query_token=token
    )
    if user is not None:
        return user

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
    )


# ``require_auth`` alias keeps call-site diffs minimal for endpoints that don't
# need the user object (they still get auth-gated).
require_auth = require_user
