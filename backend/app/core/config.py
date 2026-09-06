"""
Central application configuration.

All values are read from environment variables (or a local .env file).
This keeps secrets and machine-specific values (DB path, ports, keys) out of
source code, and is the SAME pattern used when this backend is later
deployed to a cloud VM / container - only the .env values change.
"""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    HOST: str = "0.0.0.0"
    PORT: int = 8000

    DATABASE_URL: str = "sqlite:///./amppulse.db"

    JWT_SECRET: str = "insecure-dev-secret-change-me"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 1440

    DEVICE_PROVISION_KEY: str = "insecure-dev-provision-key"

    CORS_ORIGINS: str = "http://localhost:5500,http://127.0.0.1:5500"

    SEED_USER_EMAIL: str = "demo@amppulse.ai"
    SEED_USER_PASSWORD: str = "Demo@12345"

    # Tariff used for cost estimation in the MVP (INR per kWh - simple slab-free rate).
    # Real EB tariff slabs are a "Production" enhancement (see docs).
    ENERGY_RATE_PER_KWH: float = 6.0

    # A device is considered OFFLINE if no telemetry/heartbeat received within this window.
    DEVICE_OFFLINE_AFTER_SECONDS: int = 90

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
