# Production Migration Plan & Readiness Checklist

## Migration plan (Stage 1 → Stage 2 → Stage 3)
See `docs/ARCHITECTURE.md` §5 for the full diagrams. Summary of concrete steps:

### Stage 1 → Stage 2 (Cloud/VPS backend)
1. Provision a small VPS (or managed container service).
2. Switch `DATABASE_URL` to a managed Postgres instance; run schema creation
   (or Alembic migrations — see `docs/DATABASE.md`).
3. Put the FastAPI app behind Nginx/Caddy with a TLS certificate (Let's
   Encrypt) → **HTTPS is now enforced**.
4. Change ESP32 `config.h`: `BACKEND_HOST` → public domain, add TLS support
   in `api_client.cpp` (use `WiFiClientSecure` instead of plain `WiFiClient`/
   `HTTPClient` defaults) or keep the ESP32 on a local gateway that forwards
   to the cloud over TLS.
5. Replace ESP32's `millis()/1000` timestamp with real NTP time
   (`configTime()` + `time()`), since server receipt time should not be the
   only authoritative timestamp once devices may buffer offline data.
6. Rotate `JWT_SECRET` and `DEVICE_PROVISION_KEY` to values generated for
   production, stored in a secret manager, not `.env` in source control.
7. Host the frontend as static files on a CDN or the same VPS.

**No changes needed**: API routes/JSON contracts, database schema, ESP32
sensor-reading and command-application logic, relay control code.

### Stage 2 → Stage 3 (Full production)
1. Add a load balancer / API gateway in front of multiple backend instances
   (the FastAPI app is already stateless — JWT auth, no server-side session
   — so horizontal scaling requires no code change).
2. Add a proper device-provisioning/admin flow (replace shared provisioning
   key with authenticated installer accounts + per-installation tokens).
3. Add rate limiting (per-device and per-user) at the gateway or app layer.
4. Add a message queue only if telemetry ingestion volume requires
   decoupling ingestion from processing (not needed at small scale).
5. Add OTA firmware updates (signed images) so devices can be patched
   without physical access.
6. Add monitoring/alerting (Prometheus/Grafana or a hosted equivalent),
   database backups (automated snapshots), and structured logging with
   retention.
7. Introduce a cache layer (e.g. Redis) if dashboard read load grows beyond
   what Postgres comfortably serves directly.

## Production-readiness checklist
- [ ] HTTPS/TLS enforced end-to-end (frontend↔backend, ESP32↔backend)
- [ ] Database migrated to Postgres with automated backups
- [ ] Alembic (or equivalent) migrations replacing `create_all()`
- [ ] `JWT_SECRET` and `DEVICE_PROVISION_KEY` rotated to unique production
      values, stored in a secret manager
- [ ] Device provisioning requires authenticated installer/admin session
- [ ] Device key rotation/revocation endpoint implemented
- [ ] Rate limiting on auth and telemetry endpoints
- [ ] CORS restricted to exact production frontend origin(s)
- [ ] Structured logging with retention + alerting on auth-failure spikes
- [ ] Health check endpoint wired into uptime monitoring
- [ ] Device heartbeat/offline detection tuned for production network
      conditions (current 90s threshold assumes a stable local network)
- [ ] OTA firmware update mechanism with signed images
- [ ] Horizontal scaling tested (multiple backend instances behind LB)
- [ ] Load testing performed for expected device count and telemetry rate
- [ ] Password policy enforced (min length/complexity) for user accounts
- [ ] Frontend served over CDN with cache-busting for updates
- [ ] Incident response runbook for "backend down" / "device mass offline"
      scenarios

## What must NOT change during migration
Per the ESP32-side API contract requirement: firmware should continue to
call the same `/api/v1/devices/{id}/data`, `/commands`, `/commands/ack`
endpoints with the same JSON shapes. Only `BACKEND_HOST`/`BACKEND_PORT` (and
eventually TLS transport) change in `config.h` — no firmware logic rewrite
should be required to move from laptop to cloud.
