"""
Database schema.

Entities & relationships:

  User (1) ----- (many) Device
    A user (home owner) owns one or more ESP32 devices.

  Device (1) ----- (many) TelemetryReading
    Each reading is one JSON payload posted by the ESP32 (voltage, current,
    power, energy, temperature, humidity, per-channel relay state).
    This is an append-only time-series table - the core of "Live Monitoring",
    "Energy Usage" and "Monthly Reports".

  Device (1) ----- (many) Command
    A command is created by the backend (on behalf of a user action, e.g.
    "turn off Fan") and consumed by the ESP32 the next time it polls for
    commands. Kept as a small queue/log rather than a fire-and-forget value
    so we have an audit trail and can detect "pending too long" (device
    unreachable).

Indexes:
  - Device.device_id is unique + indexed (primary lookup key from ESP32).
  - TelemetryReading(device_id, created_at) composite index for fast
    "latest reading" / "readings in range" queries used by dashboards.
  - Command(device_id, status) index for the ESP32 polling query
    ("give me my pending commands").
"""
from datetime import datetime, timezone

from sqlalchemy import (
    Column, Integer, String, Float, Boolean, DateTime, ForeignKey, Index, JSON
)
from sqlalchemy.orm import relationship

from app.core.database import Base


def utcnow():
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(32), default="home", nullable=False)  # home/organization/industrial/government
    created_at = Column(DateTime, default=utcnow)

    devices = relationship("Device", back_populates="owner", cascade="all, delete-orphan")


class Device(Base):
    __tablename__ = "devices"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(String(64), unique=True, index=True, nullable=False)  # e.g. "ESP32_001"
    device_key_hash = Column(String(255), nullable=False)  # hashed, never stored in plaintext
    name = Column(String(128), default="Home Energy Monitor")
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    firmware_version = Column(String(32), default="unknown")
    last_seen_at = Column(DateTime, nullable=True)
    last_ip = Column(String(45), nullable=True)  # most recent IP reported by the ESP32
    is_active = Column(Boolean, default=True)  # soft-disable a device without deleting history
    created_at = Column(DateTime, default=utcnow)

    # channel labels, e.g. {"1": "Fan", "2": "Air Conditioner"} - editable by the user
    channel_labels = Column(JSON, default=lambda: {"1": "Appliance 1", "2": "Appliance 2"})

    owner = relationship("User", back_populates="devices")
    readings = relationship("TelemetryReading", back_populates="device", cascade="all, delete-orphan")
    commands = relationship("Command", back_populates="device", cascade="all, delete-orphan")


class TelemetryReading(Base):
    __tablename__ = "telemetry_readings"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(String(64), ForeignKey("devices.device_id"), nullable=False)

    # Electrical parameters (from ZMPT101B + PZEM-004T, PZEM simulated for now)
    voltage = Column(Float, nullable=True)          # V
    current = Column(Float, nullable=True)          # A
    power = Column(Float, nullable=True)             # W
    energy_kwh = Column(Float, nullable=True)        # cumulative kWh counter reported by device
    frequency = Column(Float, nullable=True)          # Hz
    power_factor = Column(Float, nullable=True)

    # Environment (DHT22)
    temperature = Column(Float, nullable=True)        # °C
    humidity = Column(Float, nullable=True)            # %

    # Relay/channel status, e.g. {"1": "ON", "2": "OFF"}
    channel_status = Column(JSON, default=dict)

    device_reported_at = Column(Integer, nullable=True)  # ESP32 epoch seconds, as sent
    device_ip = Column(String(45), nullable=True)  # IP the ESP32 reported on that reading
    created_at = Column(DateTime, default=utcnow, index=True)  # server receipt time (authoritative)

    device = relationship("Device", back_populates="readings")


Index("ix_telemetry_device_created", TelemetryReading.device_id, TelemetryReading.created_at)


class Command(Base):
    __tablename__ = "commands"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(String(64), ForeignKey("devices.device_id"), nullable=False)

    action = Column(String(32), nullable=False)   # "relay_set"
    channel = Column(Integer, nullable=False)      # 1 or 2
    value = Column(String(16), nullable=False)     # "ON" / "OFF"

    status = Column(String(16), default="pending")  # pending -> delivered -> acked / failed
    created_at = Column(DateTime, default=utcnow)
    delivered_at = Column(DateTime, nullable=True)
    acked_at = Column(DateTime, nullable=True)

    device = relationship("Device", back_populates="commands")


Index("ix_command_device_status", Command.device_id, Command.status)
