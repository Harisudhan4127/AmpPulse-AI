from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class CommandCreateRequest(BaseModel):
    """Sent by the frontend (via backend) to control a relay channel."""
    action: Literal["relay_set"] = "relay_set"
    channel: int = Field(..., ge=1, le=2)
    value: Literal["ON", "OFF"]


class CommandOut(BaseModel):
    id: int
    action: str
    channel: int
    value: str
    status: str
    created_at: datetime

    class Config:
        from_attributes = True


class CommandCreateResponse(BaseModel):
    success: bool = True
    command: CommandOut


class PendingCommandsResponse(BaseModel):
    """Returned to the ESP32 when it polls GET /devices/{id}/commands."""
    success: bool = True
    commands: list[CommandOut]


class CommandAckRequest(BaseModel):
    command_id: int
    status: Literal["acked", "failed"]


class CommandAckResponse(BaseModel):
    success: bool = True
    command_id: int
    status: str
