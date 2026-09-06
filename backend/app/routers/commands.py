from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_device, get_current_user
from app.core.errors import ApiError
from app.models.models import Device, Command, User
from app.schemas.command import (
    CommandCreateRequest, CommandCreateResponse, CommandOut,
    PendingCommandsResponse, CommandAckRequest, CommandAckResponse,
)

router = APIRouter(prefix="/api/v1/devices", tags=["commands"])


@router.post("/{device_id}/commands", response_model=CommandCreateResponse, status_code=201)
def create_command(
    device_id: str,
    payload: CommandCreateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Called by the frontend (e.g. 'Turn OFF Fan' button) to queue a command."""
    device = db.query(Device).filter(Device.device_id == device_id).first()
    if not device:
        raise ApiError("DEVICE_NOT_FOUND", "Device does not exist.", 404)

    command = Command(
        device_id=device_id,
        action=payload.action,
        channel=payload.channel,
        value=payload.value,
        status="pending",
    )
    db.add(command)
    db.commit()
    db.refresh(command)

    return CommandCreateResponse(command=CommandOut.model_validate(command))


@router.get("/{device_id}/commands", response_model=PendingCommandsResponse)
def get_pending_commands(
    device_id: str,
    db: Session = Depends(get_db),
    device: Device = Depends(get_current_device),
):
    """
    Polled periodically by the ESP32 (e.g. every 3-5s).

    Communication pattern decision: POLLING was chosen over WebSocket/MQTT
    for the MVP because (a) command latency of a few seconds is acceptable
    for relay toggling, (b) it reuses the exact same HTTP+JSON stack as
    telemetry - no second protocol/library/port on the ESP32, and (c) it is
    trivially firewall/NAT-friendly for a local Wi-Fi network. See docs for
    when to upgrade to MQTT/WebSocket in production (many devices / need for
    sub-second control).
    """
    pending = (
        db.query(Command)
        .filter(Command.device_id == device_id, Command.status == "pending")
        .order_by(Command.created_at.asc())
        .all()
    )
    now = datetime.now(timezone.utc)
    for c in pending:
        c.status = "delivered"
        c.delivered_at = now
    db.commit()

    return PendingCommandsResponse(commands=[CommandOut.model_validate(c) for c in pending])


@router.post("/{device_id}/commands/ack", response_model=CommandAckResponse)
def ack_command(
    device_id: str,
    payload: CommandAckRequest,
    db: Session = Depends(get_db),
    device: Device = Depends(get_current_device),
):
    """Called by the ESP32 after it has actually toggled the relay."""
    command = (
        db.query(Command)
        .filter(Command.id == payload.command_id, Command.device_id == device_id)
        .first()
    )
    if not command:
        raise ApiError("COMMAND_NOT_FOUND", "Command does not exist for this device.", 404)

    command.status = payload.status
    command.acked_at = datetime.now(timezone.utc)
    db.commit()

    return CommandAckResponse(command_id=command.id, status=command.status)
