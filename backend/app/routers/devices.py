from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.errors import ApiError
from app.core.security import generate_device_key, hash_password
from app.models.models import Device, TelemetryReading, User
from app.schemas.device import (
    DeviceRegisterRequest, DeviceRegisterResponse,
    DeviceListResponse, DeviceOut, DeviceDetailResponse,
    DeviceStatusResponse, ChannelLabelsUpdateRequest,
)

router = APIRouter(prefix="/api/v1/devices", tags=["devices"])
settings = get_settings()


def _status_for(device: Device) -> str:
    if not device.last_seen_at:
        return "offline"
    age = datetime.now(timezone.utc) - device.last_seen_at.replace(tzinfo=timezone.utc)
    return "online" if age <= timedelta(seconds=settings.DEVICE_OFFLINE_AFTER_SECONDS) else "offline"


def _to_out(device: Device) -> DeviceOut:
    return DeviceOut(
        device_id=device.device_id,
        name=device.name,
        is_active=device.is_active,
        firmware_version=device.firmware_version,
        last_seen_at=device.last_seen_at,
        status=_status_for(device),
        channel_labels=device.channel_labels or {},
    )


@router.post("/register", response_model=DeviceRegisterResponse, status_code=201)
def register_device(
    payload: DeviceRegisterRequest,
    db: Session = Depends(get_db),
    x_provision_key: str | None = Header(default=None),
):
    """
    One-time device provisioning.

    Auth model: requires the shared DEVICE_PROVISION_KEY (set by the
    installer/admin, kept out of firmware source control - flashed via
    build-time config, see esp32-firmware/README). This key can ONLY create
    devices; it cannot read/write telemetry or commands. After registration,
    the device uses its own unique device_key for all further calls.

    MVP simplification: any caller with the provisioning key can register a
    device (no per-installation admin login yet). In production this would
    require an authenticated installer/user session as well.
    """
    if x_provision_key != settings.DEVICE_PROVISION_KEY:
        raise ApiError("UNAUTHORIZED", "Invalid or missing provisioning key.", 401)

    existing = db.query(Device).filter(Device.device_id == payload.device_id).first()
    if existing:
        raise ApiError("DEVICE_ALREADY_EXISTS", "A device with this device_id is already registered.", 409)

    device_key = generate_device_key()
    device = Device(
        device_id=payload.device_id,
        device_key_hash=hash_password(device_key),
        name=payload.name or "Home Energy Monitor",
        firmware_version=payload.firmware_version or "unknown",
    )
    db.add(device)
    db.commit()
    db.refresh(device)

    return DeviceRegisterResponse(device_id=device.device_id, device_key=device_key)


@router.get("", response_model=DeviceListResponse)
def list_devices(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    devices = db.query(Device).filter(Device.owner_id == user.id).all()
    # MVP note: devices are not yet auto-assigned to a user at registration
    # (no installer-login step). Fall back to showing unowned devices too,
    # so a fresh demo setup is visible immediately.
    if not devices:
        devices = db.query(Device).filter(Device.owner_id.is_(None)).all()
    return DeviceListResponse(devices=[_to_out(d) for d in devices])


@router.get("/{device_id}", response_model=DeviceDetailResponse)
def get_device(device_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    device = db.query(Device).filter(Device.device_id == device_id).first()
    if not device:
        raise ApiError("DEVICE_NOT_FOUND", "Device does not exist.", 404)
    return DeviceDetailResponse(device=_to_out(device))


@router.get("/{device_id}/status", response_model=DeviceStatusResponse)
def get_device_status(device_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    device = db.query(Device).filter(Device.device_id == device_id).first()
    if not device:
        raise ApiError("DEVICE_NOT_FOUND", "Device does not exist.", 404)

    latest = (
        db.query(TelemetryReading)
        .filter(TelemetryReading.device_id == device_id)
        .order_by(TelemetryReading.created_at.desc())
        .first()
    )
    latest_dict = None
    if latest:
        latest_dict = {
            "voltage": latest.voltage, "current": latest.current, "power": latest.power,
            "energy_kwh": latest.energy_kwh, "frequency": latest.frequency,
            "temperature": latest.temperature, "humidity": latest.humidity,
            "channel_status": latest.channel_status, "device_ip": latest.device_ip,
            "created_at": latest.created_at.isoformat(),
        }

    return DeviceStatusResponse(
        device_id=device.device_id,
        status=_status_for(device),
        last_seen_at=device.last_seen_at,
        ip_address=device.last_ip,
        firmware_version=device.firmware_version,
        latest_reading=latest_dict,
    )


@router.patch("/{device_id}/channels", response_model=DeviceDetailResponse)
def update_channel_labels(
    device_id: str,
    payload: ChannelLabelsUpdateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Rename appliance channels, e.g. {"1": "Air Conditioner", "2": "Fan"}."""
    device = db.query(Device).filter(Device.device_id == device_id).first()
    if not device:
        raise ApiError("DEVICE_NOT_FOUND", "Device does not exist.", 404)

    device.channel_labels = payload.channel_labels
    db.commit()
    db.refresh(device)
    return DeviceDetailResponse(device=_to_out(device))
