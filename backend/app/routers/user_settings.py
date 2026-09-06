"""
User preferences router.

Lets the frontend store MULTIPLE per-user ESP32 direct-connection addresses so
the web app can connect straight to each device's own web server (port 80)
for live data and relay control, instead of going through the backend. The
list is remembered across sessions (keyed by the logged-in user).

Endpoints:
    GET    /api/v1/user/esp32s
           Returns every saved ESP32 with a live reachability probe.
    POST   /api/v1/user/esp32
           Adds a new ESP32 address for the current user (re-probes it).
    PUT    /api/v1/user/esp32/{esp32_id}
           Updates an existing entry (ip / port / name) and re-probes it.
    DELETE /api/v1/user/esp32/{esp32_id}
           Forgets one saved ESP32.
"""
import socket
import time

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.errors import ApiError
from app.models.models import User, UserEsp32

router = APIRouter(prefix="/api/v1/user", tags=["user"])


class UserEsp32Add(BaseModel):
    """Payload to save a new ESP32 direct-connection target."""
    ip: str = Field(..., min_length=1, max_length=45, examples=["192.168.1.7"])
    port: int = Field(default=80, ge=1, le=65535)
    name: str | None = Field(default=None, max_length=64)


class UserEsp32Update(BaseModel):
    """Partial update of a saved ESP32 entry."""
    ip: str | None = Field(default=None, min_length=1, max_length=45)
    port: int | None = Field(default=None, ge=1, le=65535)
    name: str | None = Field(default=None, max_length=64)


class UserEsp32Out(BaseModel):
    id: int
    name: str
    ip: str
    port: int
    connected: bool
    latency_ms: float | None
    error: str | None

    class Config:
        from_attributes = True


class Esp32ListResponse(BaseModel):
    success: bool = True
    devices: list[UserEsp32Out]


class Esp32DetailResponse(BaseModel):
    success: bool = True
    device: UserEsp32Out


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


def _to_out(entry: UserEsp32) -> UserEsp32Out:
    ok, latency, err = _probe(entry.ip, entry.port)
    return UserEsp32Out(
        id=entry.id,
        name=entry.name,
        ip=entry.ip,
        port=entry.port,
        connected=ok,
        latency_ms=latency,
        error=err,
    )


def _owned(db: Session, user: User, esp32_id: int) -> UserEsp32:
    entry = db.query(UserEsp32).filter(
        UserEsp32.id == esp32_id, UserEsp32.user_id == user.id
    ).first()
    if not entry:
        raise ApiError("ESP32_NOT_FOUND", "No such saved ESP32 for this account.", 404)
    return entry


@router.get("/esp32s", response_model=Esp32ListResponse)
def list_esp32_settings(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Return every saved ESP32 address with a live reachability probe."""
    entries = db.query(UserEsp32).filter(UserEsp32.user_id == user.id).order_by(UserEsp32.id).all()
    return Esp32ListResponse(devices=[_to_out(e) for e in entries])


@router.post("/esp32", response_model=Esp32DetailResponse, status_code=201)
def add_esp32_settings(
    payload: UserEsp32Add,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Save a new ESP32 target (deduped by ip+port) and probe it."""
    ip = payload.ip.strip()
    if not ip:
        raise ApiError("VALIDATION_ERROR", "ESP32 IP cannot be empty.", 422)

    existing = db.query(UserEsp32).filter(
        UserEsp32.user_id == user.id,
        UserEsp32.ip == ip,
        UserEsp32.port == payload.port,
    ).first()
    if existing:
        raise ApiError("DUPLICATE_ESP32", "This ESP32 is already saved for this account.", 409)

    entry = UserEsp32(user_id=user.id, ip=ip, port=payload.port,
                      name=(payload.name or "ESP32").strip() or "ESP32")
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return Esp32DetailResponse(device=_to_out(entry))


@router.put("/esp32/{esp32_id}", response_model=Esp32DetailResponse)
def update_esp32_settings(
    esp32_id: int,
    payload: UserEsp32Update,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Update ip/port/name of one saved ESP32 and re-probe it."""
    entry = _owned(db, user, esp32_id)
    if payload.ip is not None and payload.ip.strip():
        entry.ip = payload.ip.strip()
    if payload.port is not None:
        entry.port = payload.port
    if payload.name is not None:
        entry.name = payload.name.strip() or "ESP32"
    db.commit()
    db.refresh(entry)
    return Esp32DetailResponse(device=_to_out(entry))


@router.delete("/esp32/{esp32_id}", response_model=Esp32ListResponse)
def delete_esp32_settings(
    esp32_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Forget one saved ESP32 (backend fallback mode for that device)."""
    entry = _owned(db, user, esp32_id)
    db.delete(entry)
    db.commit()
    entries = db.query(UserEsp32).filter(UserEsp32.user_id == user.id).order_by(UserEsp32.id).all()
    return Esp32ListResponse(devices=[_to_out(e) for e in entries])