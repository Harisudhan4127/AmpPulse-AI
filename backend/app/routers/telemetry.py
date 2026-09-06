from collections import defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.deps import get_current_device, get_current_user
from app.core.errors import ApiError
from app.models.models import Device, TelemetryReading, User
from app.schemas.telemetry import (
    TelemetryIn, TelemetryAck, TelemetryHistoryResponse, ReadingOut,
    SummaryResponse, DailySummary,
)

router = APIRouter(prefix="/api/v1/devices", tags=["telemetry"])
settings = get_settings()


@router.post("/{device_id}/data", response_model=TelemetryAck, status_code=201)
def ingest_telemetry(
    device_id: str,
    payload: TelemetryIn,
    db: Session = Depends(get_db),
    device: Device = Depends(get_current_device),
):
    """
    Called by the ESP32 periodically (e.g. every 5-10s) to push a reading.

    Auth: X-Device-Key header, validated against this exact device_id
    (see get_current_device). Body device_id must match the path/header
    identity, otherwise a device could push data while claiming another
    device's identity.
    """
    if payload.device_id != device_id:
        raise ApiError("DEVICE_ID_MISMATCH", "Body device_id does not match URL device_id.", 400)

    reading = TelemetryReading(
        device_id=device_id,
        voltage=payload.voltage,
        current=payload.current,
        power=payload.power,
        energy_kwh=payload.energy_kwh,
        frequency=payload.frequency,
        power_factor=payload.power_factor,
        temperature=payload.temperature,
        humidity=payload.humidity,
        channel_status=payload.channel_status or {},
        device_reported_at=payload.timestamp,
        device_ip=payload.device_ip,
    )
    db.add(reading)

    device.last_seen_at = datetime.now(timezone.utc)
    if payload.device_ip:
        device.last_ip = payload.device_ip
    db.commit()
    db.refresh(reading)

    return TelemetryAck(received_at=reading.created_at, reading_id=reading.id)


@router.get("/{device_id}/data", response_model=TelemetryHistoryResponse)
def get_telemetry_history(
    device_id: str,
    limit: int = Query(default=100, ge=1, le=2000),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Called by the frontend for charts / live monitoring tables."""
    device = db.query(Device).filter(Device.device_id == device_id).first()
    if not device:
        raise ApiError("DEVICE_NOT_FOUND", "Device does not exist.", 404)

    readings = (
        db.query(TelemetryReading)
        .filter(TelemetryReading.device_id == device_id)
        .order_by(TelemetryReading.created_at.desc())
        .limit(limit)
        .all()
    )
    return TelemetryHistoryResponse(
        device_id=device_id,
        readings=[ReadingOut.model_validate(r) for r in reversed(readings)],
    )


@router.get("/{device_id}/summary", response_model=SummaryResponse)
def get_summary(
    device_id: str,
    days: int = Query(default=7, ge=1, le=31),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """
    Simple energy-cost summary for dashboard KPIs and bill prediction.

    MVP approach: flat rate (ENERGY_RATE_PER_KWH) x cumulative energy delta
    per day, then linear-project the rest of the month from the daily
    average so far. This is intentionally simple; see docs for how a real
    AI/tariff-slab model would replace this function later without changing
    the API contract (same SummaryResponse shape).
    """
    device = db.query(Device).filter(Device.device_id == device_id).first()
    if not device:
        raise ApiError("DEVICE_NOT_FOUND", "Device does not exist.", 404)

    since = datetime.now(timezone.utc) - timedelta(days=days)
    readings = (
        db.query(TelemetryReading)
        .filter(TelemetryReading.device_id == device_id, TelemetryReading.created_at >= since)
        .order_by(TelemetryReading.created_at.asc())
        .all()
    )

    # Group by day, compute energy delta (last - first cumulative kWh reading of that day)
    by_day: dict[str, list[float]] = defaultdict(list)
    for r in readings:
        if r.energy_kwh is not None:
            day_key = r.created_at.date().isoformat()
            by_day[day_key].append(r.energy_kwh)

    daily: list[DailySummary] = []
    for day_key in sorted(by_day.keys()):
        values = by_day[day_key]
        delta = max(values) - min(values) if len(values) > 1 else 0.0
        daily.append(DailySummary(
            date=day_key,
            energy_kwh=round(delta, 3),
            estimated_cost=round(delta * settings.ENERGY_RATE_PER_KWH, 2),
        ))

    today_key = datetime.now(timezone.utc).date().isoformat()
    today_energy = next((d.energy_kwh for d in daily if d.date == today_key), 0.0)
    today_cost = round(today_energy * settings.ENERGY_RATE_PER_KWH, 2)

    avg_daily_energy = (sum(d.energy_kwh for d in daily) / len(daily)) if daily else 0.0
    projected_month_cost = round(avg_daily_energy * 30 * settings.ENERGY_RATE_PER_KWH, 2)

    return SummaryResponse(
        device_id=device_id,
        today_energy_kwh=today_energy,
        today_cost=today_cost,
        projected_month_cost=projected_month_cost,
        daily=daily,
    )
