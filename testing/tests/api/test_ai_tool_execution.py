from types import SimpleNamespace

from pymol.ai.tool_execution import run_pymol_command


class _DummyCmd:
    def __init__(self):
        self.commands = []
        self._feedback = []
        self._parser = SimpleNamespace(parse=self._parse)

    def _parse(self, command):
        text = str(command or "").strip()
        self.commands.append(text)
        self._feedback.append("ran:%s" % (text,))
        if text.startswith("show "):
            return 0
        return 1

    def _get_feedback(self):
        out = list(self._feedback)
        self._feedback = []
        return out


def test_multiline_block_executes_each_subcommand():
    cmd = _DummyCmd()
    result = run_pymol_command(
        cmd,
        "select ligands, resn ORO or resn FMN\nshow sticks, ligands",
    )

    assert result.ok is False
    assert cmd.commands == [
        "select ligands, resn ORO or resn FMN",
        "show sticks, ligands",
    ]
    assert "subcommand 2/2 failed" in result.error


def test_multiline_block_success():
    cmd = _DummyCmd()
    result = run_pymol_command(
        cmd,
        "select ligands, resn ORO or resn FMN\ncolor yellow, ligands and elem C",
    )

    assert result.ok is True
    assert cmd.commands == [
        "select ligands, resn ORO or resn FMN",
        "color yellow, ligands and elem C",
    ]
    assert result.error == ""
    assert any(line.startswith("ran:select ligands") for line in result.feedback_lines)
