from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class TelemetryIn(BaseModel):
    """
    JSON body POSTed by the ESP32 for each periodic reading.
    device_id is also required in the body (in addition to the X-Device-Key
    header) so the payload is self-describing and easy to replay/test with
    curl without needing to inspect headers.
    """
    device_id: str
    timestamp: int = Field(..., description="ESP32 epoch seconds (millis()/1000 or NTP time)")

    voltage: Optional[float] = Field(default=None, ge=0, le=400)
    current: Optional[float] = Field(default=None, ge=0, le=100)
    power: Optional[float] = Field(default=None, ge=0, le=20000)
    energy_kwh: Optional[float] = Field(default=None, ge=0)
    frequency: Optional[float] = Field(default=None, ge=0, le=100)
    power_factor: Optional[float] = Field(default=None, ge=0, le=1)

    temperature: Optional[float] = Field(default=None, ge=-40, le=125)
    humidity: Optional[float] = Field(default=None, ge=0, le=100)

    channel_status: Optional[dict[str, str]] = Field(default_factory=dict)
    status: str = Field(default="online")
    device_ip: Optional[str] = Field(default=None, max_length=45,
                                     description="ESP32's current IP address")

    @field_validator("channel_status")
    @classmethod
    def validate_channel_status(cls, v):
        for ch, val in (v or {}).items():
            if val not in ("ON", "OFF"):
                raise ValueError(f"channel_status[{ch}] must be 'ON' or 'OFF'")
        return v


class TelemetryAck(BaseModel):
    success: bool = True
    received_at: datetime
    reading_id: int


class ReadingOut(BaseModel):
    voltage: Optional[float]
    current: Optional[float]
    power: Optional[float]
    energy_kwh: Optional[float]
    frequency: Optional[float]
    power_factor: Optional[float]
    temperature: Optional[float]
    humidity: Optional[float]
    channel_status: dict
    device_ip: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class TelemetryHistoryResponse(BaseModel):
    success: bool = True
    device_id: str
    readings: list[ReadingOut]


class DailySummary(BaseModel):
    date: str
    energy_kwh: float
    estimated_cost: float


class SummaryResponse(BaseModel):
    success: bool = True
    device_id: str
    today_energy_kwh: float
    today_cost: float
    projected_month_cost: float
    daily: list[DailySummary]
