from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class DeviceRegisterRequest(BaseModel):
    """
    Sent ONCE by an ESP32 (or by an installer via the frontend) to register a
    brand-new physical device. Requires the shared provisioning key - NOT the
    per-device key, because the per-device key does not exist yet.
    """
    device_id: str = Field(..., min_length=3, max_length=64, examples=["ESP32_001"])
    name: Optional[str] = Field(default="Home Energy Monitor", max_length=128)
    firmware_version: Optional[str] = "unknown"


class DeviceRegisterResponse(BaseModel):
    success: bool = True
    device_id: str
    device_key: str  # returned ONCE - the device must store this; never shown again
    message: str = "Store this device_key securely on the device. It will not be shown again."


class DeviceOut(BaseModel):
    device_id: str
    name: str
    is_active: bool
    firmware_version: str
    last_seen_at: Optional[datetime] = None
    last_ip: Optional[str] = None
    status: str  # "online" / "offline" (derived, not stored)
    channel_labels: dict

    class Config:
        from_attributes = True


class DeviceListResponse(BaseModel):
    success: bool = True
    devices: list[DeviceOut]


class DeviceDetailResponse(BaseModel):
    success: bool = True
    device: DeviceOut


class DeviceStatusResponse(BaseModel):
    success: bool = True
    device_id: str
    status: str
    last_seen_at: Optional[datetime] = None
    ip_address: Optional[str] = None
    firmware_version: Optional[str] = None
    latest_reading: Optional[dict] = None


class ChannelLabelsUpdateRequest(BaseModel):
    channel_labels: dict[str, str]
