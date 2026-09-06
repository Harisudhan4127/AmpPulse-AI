"""
Database engine & session management (SQLAlchemy).

MVP uses SQLite (single file, zero setup, perfect for a laptop/local host).
To move to Postgres for production, only DATABASE_URL changes
(e.g. postgresql://user:pass@host:5432/amppulse) - the models/queries
elsewhere in the app do not need to change because SQLAlchemy abstracts
the dialect.
"""
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, declarative_base

from app.core.config import get_settings

settings = get_settings()

connect_args = {"check_same_thread": False} if settings.DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(settings.DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def ensure_schema_compatible() -> None:
    """
    Idempotent micro-migration for pre-existing SQLite databases.

    Base.metadata.create_all() creates missing tables but never alters
    existing ones, so new columns added after a DB was first created need a
    manual ALTER TABLE. Safe to run on every startup; no-ops when the column
    already exists (fresh installs get the columns from create_all()).
    """
    inspector = inspect(engine)
    if "telemetry_readings" in inspector.get_table_names():
        cols = {c["name"] for c in inspector.get_columns("telemetry_readings")}
        if "device_ip" not in cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE telemetry_readings ADD COLUMN device_ip VARCHAR(45)"))
    if "devices" in inspector.get_table_names():
        cols = {c["name"] for c in inspector.get_columns("devices")}
        if "last_ip" not in cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE devices ADD COLUMN last_ip VARCHAR(45)"))


def get_db():
    """FastAPI dependency: yields a DB session and always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
