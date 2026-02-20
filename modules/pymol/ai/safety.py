from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable

from .protocol import AiPlan

MAX_COMMANDS = 10

_DESTRUCTIVE_PREFIXES = (
    "delete",
    "remove",
    "reinitialize",
    "reset",
)

_RE_BROAD_EDIT = re.compile(r"^alter\b.*\b(all|\*)\b", re.IGNORECASE)
_RE_BLOCKED_PREFIX = re.compile(r"^(/|!|python\s+|_($|\s+))", re.IGNORECASE)


class PlanValidationError(ValueError):
    pass


@dataclass
class SafetyResult:
    destructive: bool


def is_destructive_command(command: str) -> bool:
    text = command.strip().lower()
    if not text:
        return False

    for prefix in _DESTRUCTIVE_PREFIXES:
        if text == prefix or text.startswith(prefix + " "):
            return True

    if _RE_BROAD_EDIT.search(text):
        return True

    return False


def is_blocked_command(command: str) -> bool:
    text = command.strip()
    if not text:
        return True
    return bool(_RE_BLOCKED_PREFIX.search(text))


def classify_plan(commands: Iterable[str]) -> SafetyResult:
    destructive = any(is_destructive_command(c) for c in commands)
    return SafetyResult(destructive=destructive)


def validate_plan(plan: AiPlan) -> SafetyResult:
    for command in plan.commands:
        if is_blocked_command(command):
            raise PlanValidationError(
                "plan contains blocked command syntax: %r" % (command,)
            )

    count = len(plan.commands)
    if count > MAX_COMMANDS:
        raise PlanValidationError(
            "plan has %d commands, limit is %d" % (count, MAX_COMMANDS)
        )

    result = classify_plan(plan.commands)
    if result.destructive:
        warning = "Destructive commands detected. Extra confirmation required."
        if warning not in plan.warnings:
            plan.warnings.append(warning)

    return result
