from pymol.ai.doom_loop_detector import DoomLoopDetector


def test_exact_match_loop_detection():
    detector = DoomLoopDetector(threshold=3)
    assert detector.add_call("run_pymol_command", {"command": "zoom"}) is None
    assert detector.add_call("run_pymol_command", {"command": "zoom"}) is None
    loop = detector.add_call("run_pymol_command", {"command": "zoom"})
    assert loop
    assert loop["loop_type"] == "exact_match"


def test_assistant_intent_repeat_detection():
    detector = DoomLoopDetector(threshold=3)
    intent = "I will set this up step by step and apply electrostatics for you."
    assert detector.add_assistant_intent(intent) is None
    assert detector.add_assistant_intent(intent) is None
    loop = detector.add_assistant_intent(intent)
    assert loop
    assert loop["loop_type"] == "assistant_intent_repeat"


def test_command_family_oscillation_detection():
    detector = DoomLoopDetector(threshold=3)
    assert detector.add_call("run_pymol_command", {"command": "select fmn, resn FMN"}) is None
    assert detector.add_call("run_pymol_command", {"command": "show surface, fmn"}) is None
    loop = detector.add_call("run_pymol_command", {"command": "select binding_site, fmn expand 5"})
    assert loop
    assert loop["loop_type"] in {"command_family_repeat", "command_family_oscillation"}
