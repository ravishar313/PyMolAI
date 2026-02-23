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

    # Allow efficient multi-command tool calls by treating newline-separated
    # blocks as a sequence of individual PyMOL commands.
    lines = [part.strip() for part in command.replace("\r\n", "\n").split("\n") if part.strip()]
    subcommands = lines or [command]
    total = len(subcommands)

    # Clear stale feedback so captured lines correspond to this call.
    _safe_feedback(cmd)

    all_feedback: List[str] = []
    for i, subcommand in enumerate(subcommands, start=1):
        try:
            result = cmd._parser.parse(subcommand)
        except Exception as exc:  # noqa: BLE001
            feedback_lines = _safe_feedback(cmd)
            all_feedback.extend(feedback_lines)
            return ToolExecutionResult(
                ok=False,
                command=command,
                error="subcommand %d/%d failed: %s" % (i, total, str(exc)),
                feedback_lines=all_feedback,
            )

        feedback_lines = _safe_feedback(cmd)
        all_feedback.extend(feedback_lines)
        if result != 1:
            return ToolExecutionResult(
                ok=False,
                command=command,
                error="subcommand %d/%d failed: parser returned failure" % (i, total),
                feedback_lines=all_feedback,
            )

    return ToolExecutionResult(ok=True, command=command, feedback_lines=all_feedback)
