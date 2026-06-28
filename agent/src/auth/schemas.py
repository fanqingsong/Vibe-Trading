"""Pydantic request/response schemas for auth endpoints."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    """Registration payload."""

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    name: str = Field(default="", max_length=255)


class LoginRequest(BaseModel):
    """Login payload."""

    email: EmailStr
    password: str


class RefreshRequest(BaseModel):
    """Refresh payload."""

    refresh_token: str


class UserResponse(BaseModel):
    """Public user representation."""

    id: str
    email: str
    name: str = ""
    is_active: bool = True

    @classmethod
    def from_user(cls, user) -> "UserResponse":
        return cls(id=user.id, email=user.email, name=user.name, is_active=user.is_active)


class TokenResponse(BaseModel):
    """JWT token pair returned by login/register/refresh."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: Optional[UserResponse] = None
