"""
User preferences router.

Lets the frontend store a per-user direct ESP32 IP address so the web app can
connect straight to the device's own web server (port 80) for live data and
relay control, instead of going through the backend. The IP is remembered
across sessions (keyed by the logged-in user).

Endpoints:
    GET  /api/v1/user/esp32
        Returns the saved IP (and a live reachability probe result).
    PUT  /api/v1/user/esp32
        Saves the ESP32 IP for the current user.
    DELETE /api/v1/user/esp32
        Clears the saved IP.
"""
import urllib.request
import socket
import time

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.errors import ApiError
from app.models.models import User

router = APIRouter(prefix="/api/v1/user", tags=["user"])


class Esp32SettingsRequest(BaseModel):
    """The ESP32 IP/port the user wants to connect to directly."""
    ip: str = Field(..., min_length=1, max_length=45, examples=["192.168.1.7"])
    port: int = Field(default=80, ge=1, le=65535)


class Esp32SettingsResponse(BaseModel):
    success: bool = True
    ip: str | None = None
    port: int = 80
    connected: bool = False
    latency_ms: float | None = None
    error: str | None = None


def _probe(ip: str, port: int, timeout: float = 1.5) -> tuple[bool, float | None, str | None]:
    """
    Cheap reachability probe: opens a TCP connection to the ESP32's web
    server. Returns (ok, latency_ms, error). We intentionally avoid a full
    HTTP GET here so we don't stall the request on a slow/unreachable device.
    """
    try:
        start = time.perf_counter()
        with socket.create_connection((ip.strip(), port), timeout=timeout):
            latency = (time.perf_counter() - start) * 1000
            return True, round(latency, 1), None
    except socket.timeout:
        return False, None, "TIMEOUT - check the IP and confirm the ESP32 is on the same Wi-Fi"
    except socket.gaierror:
        return False, None, "INVALID_ADDRESS - use a numeric IP like 192.168.1.7"
    except OSError as e:
        return False, None, f"UNREACHABLE - {e}"


@router.get("/esp32", response_model=Esp32SettingsResponse)
def get_esp32_settings(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Return the saved ESP32 address with a live reachability probe."""
    if not user.esp32_ip:
        return Esp32SettingsResponse(ip=None, port=80, connected=False)

    port = 80
    ok, latency, err = _probe(user.esp32_ip, port)
    return Esp32SettingsResponse(
        ip=user.esp32_ip,
        port=port,
        connected=ok,
        latency_ms=latency,
        error=err,
    )


@router.put("/esp32", response_model=Esp32SettingsResponse)
def set_esp32_settings(
    payload: Esp32SettingsRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Save the ESP32 address for this user and return its reachability."""
    ip = payload.ip.strip()
    port = payload.port
    if not ip:
        raise ApiError("VALIDATION_ERROR", "ESP32 IP cannot be empty.", 422)

    ok, latency, err = _probe(ip, port)
    user.esp32_ip = ip
    db.commit()

    return Esp32SettingsResponse(
        ip=ip,
        port=port,
        connected=ok,
        latency_ms=latency,
        error=err,
    )


@router.delete("/esp32", response_model=Esp32SettingsResponse)
def clear_esp32_settings(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Forget the saved ESP32 address (backend fallback mode)."""
    user.esp32_ip = None
    db.commit()
    return Esp32SettingsResponse(ip=None, port=80, connected=False)
