import pytest

from pymol.ai.protocol import AiPlan


def test_plan_from_dict_accepts_string_commands():
    plan = AiPlan.from_dict({"summary": "x", "commands": "show cartoon\nzoom"})
    assert plan.commands == ["show cartoon", "zoom"]


def test_plan_requires_summary_and_commands():
    with pytest.raises(ValueError):
        AiPlan.from_dict({"summary": "", "commands": []})
