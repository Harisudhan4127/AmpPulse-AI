# Database Schema

Engine: SQLite for MVP (`backend/amppulse.db`, auto-created on first run via
`Base.metadata.create_all()` in `app/main.py`). Swappable to Postgres for
production by changing only `DATABASE_URL` — no model code changes needed
since access goes through SQLAlchemy's ORM, not raw SQL.

## Entities & relationships

```
User (1) ──── (many) Device
Device (1) ──── (many) TelemetryReading
Device (1) ──── (many) Command
```

- A **User** (home owner) owns zero or more **Devices**.
- A **Device** (one ESP32 unit) produces many **TelemetryReadings** (append-only
  time series) and receives many **Commands** (a small queue/audit log).

## Tables

### `users`
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| email | VARCHAR(255) UNIQUE, indexed | login identity |
| password_hash | VARCHAR(255) | bcrypt hash, never plaintext |
| role | VARCHAR(32) | home / organization / industrial / government |
| created_at | DATETIME | |

### `devices`
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| device_id | VARCHAR(64) UNIQUE, indexed | e.g. `ESP32_001`, primary lookup key from ESP32 |
| device_key_hash | VARCHAR(255) | bcrypt hash of the device's API key |
| name | VARCHAR(128) | display name |
| owner_id | INTEGER FK → users.id | nullable (device may be unassigned initially) |
| firmware_version | VARCHAR(32) | reported at registration/update |
| last_seen_at | DATETIME | updated on every telemetry POST; drives online/offline status |
| is_active | BOOLEAN | soft-disable without deleting history |
| channel_labels | JSON | e.g. `{"1": "Air Conditioner", "2": "Fan"}` |
| created_at | DATETIME | |

### `telemetry_readings`
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| device_id | VARCHAR(64) FK → devices.device_id | |
| voltage | FLOAT | V (ZMPT101B) |
| current | FLOAT | A (PZEM-004T, simulated for now) |
| power | FLOAT | W |
| energy_kwh | FLOAT | cumulative counter as reported by device |
| frequency | FLOAT | Hz |
| power_factor | FLOAT | 0–1 |
| temperature | FLOAT | °C (DHT22) |
| humidity | FLOAT | % (DHT22) |
| channel_status | JSON | `{"1": "ON", "2": "OFF"}` |
| device_reported_at | INTEGER | ESP32 epoch seconds, as sent (not authoritative) |
| created_at | DATETIME, indexed | server receipt time (authoritative for ordering) |

**Composite index**: `(device_id, created_at)` — supports the two most
frequent queries: "latest reading for device X" and "readings for device X
in date range" (used by `/status`, `/data`, `/summary`).

### `commands`
| Column | Type | Notes |
|---|---|---|
| id | INTEGER PK | |
| device_id | VARCHAR(64) FK → devices.device_id | |
| action | VARCHAR(32) | currently only `"relay_set"` |
| channel | INTEGER | 1 or 2 |
| value | VARCHAR(16) | `"ON"` / `"OFF"` |
| status | VARCHAR(16) | `pending` → `delivered` → `acked`/`failed` |
| created_at | DATETIME | |
| delivered_at | DATETIME | set when ESP32 polls and receives it |
| acked_at | DATETIME | set when ESP32 confirms applied/failed |

**Composite index**: `(device_id, status)` — supports the ESP32's polling
query ("give me my pending commands for this device_id") efficiently.

## Why no separate "appliances" table (MVP)
The hardware is fixed at 2 relay channels per device, so appliance identity
is represented as `channel_labels` on the `Device` row rather than a
first-class table. If/when a device supports N dynamically-added appliances
(e.g. via expansion boards), promote this to an `appliances` table with
`device_id`, `channel`, `name`, `rated_power_w` — the API and frontend
already treat channels as data, so this migration is additive, not breaking.

## Migrations
MVP uses `Base.metadata.create_all()` at startup (creates tables if absent,
never alters existing ones). For production, adopt **Alembic**:
```bash
pip install alembic
alembic init migrations
# then generate/apply versioned migrations instead of create_all()
```
This preserves data across schema changes, which `create_all()` cannot do.
