from datetime import datetime, timezone

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/api/v1/health")
def health():
    """
    Used by: frontend (to show 'backend reachable/unreachable'), ESP32
    (optional pre-flight check before posting telemetry), monitoring tools.
    """
    return {"success": True, "status": "ok", "server_time": datetime.now(timezone.utc).isoformat()}
