from enum import Enum

from pydantic import BaseModel


class ToolRunStatus(str, Enum):
    pending = "pending"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"


class ToolSpec(BaseModel):
    name: str
    requires_confirm: bool = True


class ToolRegistry:
    SPECS: dict[str, ToolSpec] = {
        "create_reminder": ToolSpec(name="create_reminder"),
        "create_calendar_event": ToolSpec(name="create_calendar_event"),
        "schedule_notification": ToolSpec(name="schedule_notification"),
    }

    @classmethod
    def get(cls, name: str) -> ToolSpec | None:
        return cls.SPECS.get(name)
