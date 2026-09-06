"""
Reusable FastAPI dependencies for authentication/authorization.

Two independent guards:
  - get_current_user   -> protects USER-facing endpoints (frontend calls)
  - get_current_device -> protects DEVICE-facing endpoints (ESP32 calls)

Keeping them separate means a stolen device key can never be used to call
user endpoints, and a stolen user token can never impersonate a device.
"""
from fastapi import Depends, Header
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import decode_access_token, verify_password
from app.core.errors import ApiError
from app.models.models import User, Device


def get_current_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise ApiError("UNAUTHORIZED", "Missing or invalid Authorization header.", 401)

    token = authorization.split(" ", 1)[1]
    payload = decode_access_token(token)
    if not payload:
        raise ApiError("INVALID_TOKEN", "Session token is invalid or expired.", 401)

    user = db.query(User).filter(User.email == payload.get("sub")).first()
    if not user:
        raise ApiError("UNAUTHORIZED", "User no longer exists.", 401)
    return user


def get_current_device(
    device_id: str,
    x_device_key: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> Device:
    """
    Validates the ESP32's per-device API key against the hashed value stored
    at registration time. The path parameter device_id must match the key's
    owning device - this stops device A's key from being used to post data
    as device B.
    """
    if not x_device_key:
        raise ApiError("UNAUTHORIZED", "Missing X-Device-Key header.", 401)

    device = db.query(Device).filter(Device.device_id == device_id).first()
    if not device:
        raise ApiError("DEVICE_NOT_FOUND", "Device does not exist.", 404)

    if not device.is_active:
        raise ApiError("DEVICE_DISABLED", "Device has been disabled.", 403)

    if not verify_password(x_device_key, device.device_key_hash):
        raise ApiError("UNAUTHORIZED", "Invalid device key.", 401)

    return device
