"""User authentication subsystem: password hashing, JWT issuance/verification,
user registration/lookup, and a dev-mode system user fallback."""

from src.auth.service import (
    SYSTEM_USER_ID,
    AuthError,
    authenticate,
    create_access_token,
    create_refresh_token,
    create_user,
    decode_token,
    get_or_create_system_user,
    get_system_user,
    get_user_by_email,
    get_user_by_id,
    hash_password,
    is_auth_enabled,
    verify_password,
)

__all__ = [
    "SYSTEM_USER_ID",
    "AuthError",
    "authenticate",
    "create_access_token",
    "create_refresh_token",
    "create_user",
    "decode_token",
    "get_or_create_system_user",
    "get_system_user",
    "get_user_by_email",
    "get_user_by_id",
    "hash_password",
    "is_auth_enabled",
    "verify_password",
]
