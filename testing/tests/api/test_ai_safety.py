import pytest

from pymol.ai.protocol import AiPlan
from pymol.ai.safety import (
    PlanValidationError,
    is_destructive_command,
    validate_plan,
)


def test_plan_rejects_more_than_10_commands():
    plan = AiPlan(summary="too many", commands=["zoom"] * 11)
    with pytest.raises(PlanValidationError):
        validate_plan(plan)


def test_destructive_plan_is_flagged():
    plan = AiPlan(summary="cleanup", commands=["delete all"])
    result = validate_plan(plan)
    assert result.destructive
    assert any("Destructive commands detected" in w for w in plan.warnings)


def test_non_destructive_plan_not_flagged():
    plan = AiPlan(summary="style", commands=["show cartoon"])
    result = validate_plan(plan)
    assert not result.destructive
    assert not is_destructive_command("show cartoon")


def test_blocked_python_command_is_rejected():
    plan = AiPlan(summary="unsafe", commands=["/import os"])
    with pytest.raises(PlanValidationError):
        validate_plan(plan)
