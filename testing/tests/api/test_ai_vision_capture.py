from pymol.ai.vision_capture import capture_viewer_snapshot


class DummyCmdOk:
    def __init__(self):
        self.last_width = None
        self.last_height = None

    def get_viewport(self, output=0, quiet=1):
        return [800, 400]

    def png(self, path, width=0, height=0, ray=0, quiet=1, prior=0):
        self.last_width = width
        self.last_height = height
        with open(path, "wb") as handle:
            handle.write(b"\x89PNG\r\n\x1a\n")


class DummyCmdFail:
    def png(self, path, width=0, height=0, ray=0, quiet=1, prior=0):
        raise RuntimeError("png failed")


def test_capture_success_data_url():
    cmd = DummyCmdOk()
    result = capture_viewer_snapshot(cmd, width=100, height=0)
    assert result["ok"] is True
    assert result["image_data_url"].startswith("data:image/png;base64,")
    assert result["meta"]["bytes"] > 0
    assert cmd.last_width == 100
    assert cmd.last_height == 50


def test_capture_failure_shape():
    result = capture_viewer_snapshot(DummyCmdFail(), width=100, height=0)
    assert result["ok"] is False
    assert "error" in result
