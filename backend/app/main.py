"""
AmpPulse AI - Local Backend (MVP)

Run with:
    uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

Architecture reminder (see docs/ARCHITECTURE.md for full detail):
    ESP32  --Wi-Fi/HTTP/JSON-->  This backend  --REST/JSON-->  Frontend
    This backend is the ONLY component that talks to the database.
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.database import (
    Base, engine, SessionLocal, ensure_schema_compatible,
)
from app.core.errors import ApiError, api_error_handler, validation_error_handler, unhandled_error_handler
from app.core.security import hash_password
from app.models.models import User, UserEsp32
from app.routers import auth, devices, telemetry, commands, health, user_settings

settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Create tables if they don't exist yet (MVP approach instead of a full
    # migration tool - see docs/PRODUCTION.md for Alembic migration plan).
    Base.metadata.create_all(bind=engine)
    ensure_schema_compatible()

    # Seed one demo user so the frontend login works out of the box.
    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.email == settings.SEED_USER_EMAIL).first()
        if not existing:
            db.add(User(
                email=settings.SEED_USER_EMAIL,
                password_hash=hash_password(settings.SEED_USER_PASSWORD),
                role="home",
            ))
            db.commit()

        # One-time migration: the old single esp32_ip column -> user_esp32s.
        # Once moved, the legacy column is cleared so this never re-runs.
        for legacy in db.query(User).filter(User.esp32_ip.isnot(None)).all():
            ip = legacy.esp32_ip.strip()
            already = db.query(UserEsp32).filter(
                UserEsp32.user_id == legacy.id,
                UserEsp32.ip == ip,
                UserEsp32.port == 80,
            ).first()
            if ip and not already:
                db.add(UserEsp32(user_id=legacy.id, ip=ip, port=80, name="ESP32"))
            legacy.esp32_ip = None
        db.commit()
    finally:
        db.close()

    yield


app = FastAPI(
    title="AmpPulse AI Backend",
    description="Local IoT backend for ESP32-based smart energy monitoring.",
    version="1.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_exception_handler(ApiError, api_error_handler)
app.add_exception_handler(RequestValidationError, validation_error_handler)
app.add_exception_handler(Exception, unhandled_error_handler)

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(devices.router)
app.include_router(telemetry.router)
app.include_router(commands.router)
app.include_router(user_settings.router)


@app.get("/")
def root():
    return {
        "success": True,
        "service": "AmpPulse AI Backend",
        "docs": "/docs",
        "health": "/api/v1/health",
    }
