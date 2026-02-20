from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass
class ToolExecutionResult:
    ok: bool
    command: str
    error: str = ""
    feedback_lines: List[str] = field(default_factory=list)


def _safe_feedback(cmd) -> List[str]:
    get_feedback = getattr(cmd, "_get_feedback", None)
    if not callable(get_feedback):
        return []

    feedback = get_feedback() or []
    return [str(x) for x in feedback]


def run_pymol_command(cmd, command: str) -> ToolExecutionResult:
    command = str(command or "").strip()
    if not command:
        return ToolExecutionResult(ok=False, command=command, error="empty command")

    # Clear stale feedback so captured lines correspond to this call.
    _safe_feedback(cmd)

    try:
        result = cmd._parser.parse(command)
    except Exception as exc:  # noqa: BLE001
        lines = _safe_feedback(cmd)
        return ToolExecutionResult(
            ok=False,
            command=command,
            error=str(exc),
            feedback_lines=lines,
        )

    lines = _safe_feedback(cmd)
    if result != 1:
        return ToolExecutionResult(
            ok=False,
            command=command,
            error="parser returned failure",
            feedback_lines=lines,
        )

    return ToolExecutionResult(ok=True, command=command, feedback_lines=lines)
