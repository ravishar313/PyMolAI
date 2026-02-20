from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class AiPlan:
    summary: str
    commands: List[str]
    warnings: List[str] = field(default_factory=list)
    reasoning: str = ""

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AiPlan":
        summary = str(data.get("summary", "")).strip()
        commands_raw = data.get("commands", [])
        warnings_raw = data.get("warnings", [])
        reasoning = str(data.get("reasoning", "")).strip()

        if isinstance(commands_raw, str):
            commands_raw = [line for line in commands_raw.splitlines() if line.strip()]
        if not isinstance(commands_raw, list):
            raise ValueError("'commands' must be a list or string")
        if isinstance(warnings_raw, str):
            warnings_raw = [warnings_raw]

        commands = [str(c).strip() for c in commands_raw if str(c).strip()]
        warnings = [str(w).strip() for w in warnings_raw if str(w).strip()]

        if not summary:
            raise ValueError("missing plan summary")
        if not commands:
            raise ValueError("plan must contain at least one command")

        return cls(summary=summary, commands=commands, warnings=warnings, reasoning=reasoning)


@dataclass
class PendingApproval:
    plan: AiPlan
    destructive: bool
    stage: str = "approve"
