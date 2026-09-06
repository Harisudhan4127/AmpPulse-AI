"""
Security primitives.

Two separate identities exist in this system:
  1. USERS  - people using the frontend. Authenticated with email/password ->
     short-lived JWT bearer token.
  2. DEVICES - ESP32 units. Authenticated with a long-lived per-device API key
     issued at registration time, sent as the "X-Device-Key" header.

Devices never receive user credentials. Users never receive device keys.
This separation is what lets us treat the ESP32 as an "untrusted client":
compromising one device key only affects that one device.
"""
import secrets
from datetime import datetime, timedelta, timezone

from jose import jwt, JWTError
from passlib.context import CryptContext

from app.core.config import get_settings

settings = get_settings()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ---------- User password hashing ----------
def hash_password(plain: str) -> str:
    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


# ---------- User JWT tokens ----------
def create_access_token(subject: str, role: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.JWT_EXPIRE_MINUTES)
    payload = {"sub": subject, "role": role, "exp": expire}
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except JWTError:
        return None


# ---------- Device API keys ----------
def generate_device_key() -> str:
    """Long, random, unguessable per-device API key issued at registration."""
    return f"dvk_{secrets.token_urlsafe(32)}"
