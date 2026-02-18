from pymol.ai.vision_capture import capture_viewer_snapshot


class DummyCmdOk:
    def __init__(self):
        self.calls = []

    def get_viewport(self, output=0, quiet=1):
        return [800, 400]

    def png(self, path, width=0, height=0, ray=0, quiet=1, prior=0):
        self.calls.append((width, height, prior))
        with open(path, "wb") as handle:
            handle.write(b"\x89PNG\r\n\x1a\n")


class DummyCmdFail:
    def png(self, path, width=0, height=0, ray=0, quiet=1, prior=0):
        raise RuntimeError("png failed")


class DummyCmdPriorFail:
    def __init__(self):
        self.calls = []

    def get_viewport(self, output=0, quiet=1):
        return [640, 480]

    def png(self, path, width=0, height=0, ray=0, quiet=1, prior=0):
        self.calls.append((width, height, prior))
        if prior == 1:
            raise RuntimeError("no prior image")
        with open(path, "wb") as handle:
            handle.write(b"\x89PNG\r\n\x1a\n")


def test_capture_success_data_url():
    cmd = DummyCmdOk()
    result = capture_viewer_snapshot(cmd, width=100, height=0)
    assert result["ok"] is True
    assert result["image_data_url"].startswith("data:image/png;base64,")
    assert result["meta"]["bytes"] > 0
    assert result["meta"]["width"] == 800
    assert result["meta"]["height"] == 400
    assert result["meta"]["requested_width"] == 100
    assert result["meta"]["requested_height"] == 0
    assert cmd.calls[0] == (0, 0, 1)


def test_capture_prior_fallback_to_render():
    cmd = DummyCmdPriorFail()
    result = capture_viewer_snapshot(cmd, width=100, height=0)
    assert result["ok"] is True
    assert len(cmd.calls) == 2
    assert cmd.calls[0] == (0, 0, 1)
    assert cmd.calls[1] == (0, 0, 0)


def test_capture_failure_shape():
    result = capture_viewer_snapshot(DummyCmdFail(), width=100, height=0)
    assert result["ok"] is False
    assert "error" in result
