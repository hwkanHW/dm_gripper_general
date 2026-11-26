"""Public HTTP and WebSocket command schema (normalized controls)."""
from typing import Literal
from pydantic import BaseModel, Field


class GripperCommand(BaseModel):
    side: Literal["left", "right", "all"] = "all"
    position: float = Field(..., ge=0, le=1)
    speed: float | None = Field(default=None, ge=0, le=1)
    torque: float | None = Field(default=None, ge=0, le=1)


