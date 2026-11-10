from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict

class ChannelCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    type: Literal["telegram", "discord", "slack", "generic"]
    config: dict = Field(default_factory=dict)  # bot_token, webhook_secret, etc.

class ChannelOut(BaseModel):
    id: str
    name: str
    type: str
    webhook_url: str | None = None
    enabled: bool
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)

class MonitorCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    script: str = Field(..., min_length=1, max_length=20_000)
    language: Literal["python", "bash"] = "python"
    interval_seconds: int = Field(60, ge=30, le=86_400)
    timeout_seconds: int = Field(30, ge=5, le=60)
    trigger_type: Literal["schedule", "event"] = "schedule"
    channel_id: str | None = None
    event_filter: dict = Field(default_factory=dict)

class MonitorUpdate(BaseModel):
    name: str | None = None
    script: str | None = None
    language: Literal["python", "bash"] | None = None
    interval_seconds: int | None = Field(None, ge=30, le=86_400)
    timeout_seconds: int | None = Field(None, ge=5, le=60)
    enabled: bool | None = None
    event_filter: dict | None = None

class MonitorOut(BaseModel):
    id: str
    name: str
    language: str
    interval_seconds: int
    timeout_seconds: int
    enabled: bool
    trigger_type: str
    channel_id: str | None
    event_filter: dict
    next_run_at: datetime
    last_run_at: datetime | None
    last_status: str | None
    last_exit_code: int | None
    last_stdout: str | None
    last_stderr: str | None
    last_error: str | None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)

class RunOut(BaseModel):
    id: str
    monitor_id: str
    event_id: str | None
    status: str
    started_at: datetime
    finished_at: datetime | None
    exit_code: int | None
    stdout: str | None
    stderr: str | None
    error: str | None
    model_config = ConfigDict(from_attributes=True)

class EventOut(BaseModel):
    id: str
    channel_id: str
    payload: dict
    reply_to: dict | None
    status: str
    error: str | None
    created_at: datetime
    processed_at: datetime | None
    model_config = ConfigDict(from_attributes=True)