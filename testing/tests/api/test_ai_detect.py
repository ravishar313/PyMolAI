from pymol.ai.detect import is_direct_command, should_route_to_ai
from pymol.shortcut import Shortcut


class DummyCmd:
    def __init__(self):
        self.kwhash = Shortcut(["show", "hide", "color", "zoom", "fetch"])


def test_detect_known_command_is_direct():
    cmd = DummyCmd()
    assert is_direct_command("color red", cmd)
    assert not should_route_to_ai("color red", cmd)


def test_detect_natural_language_routes_to_ai():
    cmd = DummyCmd()
    text = "please show the protein as cartoon and color by chain"
    assert should_route_to_ai(text, cmd)


def test_detect_command_word_with_prose_routes_to_ai():
    cmd = DummyCmd()
    text = "show the protein as cartoon and then color by chain"
    assert should_route_to_ai(text, cmd)


def test_detect_ambiguous_defaults_to_direct():
    cmd = DummyCmd()
    assert is_direct_command("x = 1", cmd)
    assert not should_route_to_ai("x = 1", cmd)
