from __future__ import annotations

from typing import Any, Dict
from uuid import uuid4

from pydantic import BaseModel, Field

from ..session.models import utc_now


class SystemEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    session_id: str
    thread_id: str
    ticket_id: str
    event_type: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utc_now)
