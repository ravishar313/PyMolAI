from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class UiRole(str, Enum):
    USER = "user"
    AI = "ai"
    TOOL_START = "tool_start"
    TOOL_RESULT = "tool_result"
    SYSTEM = "system"
    REASONING = "reasoning"
    ERROR = "error"


@dataclass
class UiEvent:
    role: UiRole
    text: str
    ok: Optional[bool] = None
    # Tool events may populate:
    # tool_call_id, tool_name, tool_args, tool_command, tool_result_json.
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolCall:
    tool_call_id: str
    name: str
    arguments: Dict[str, Any]
    arguments_json: str = "{}"


@dataclass
class VisualValidation:
    validated: bool
    used_screenshot: bool
    warning: str = ""
