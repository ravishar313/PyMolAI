from __future__ import annotations

import base64
import os
import tempfile
from typing import Dict, Any


def capture_viewer_snapshot(
    cmd,
    *,
    width: int = 1024,
    height: int = 0,
) -> Dict[str, Any]:
    fd = None
    path = None
    try:
        fd, path = tempfile.mkstemp(suffix=".png", prefix="pymol_ai_")
        os.close(fd)
        fd = None

        cmd.png(path, width=width, height=height, ray=0, quiet=1, prior=0)

        with open(path, "rb") as handle:
            blob = handle.read()

        data_url = "data:image/png;base64," + base64.b64encode(blob).decode("ascii")

        return {
            "ok": True,
            "image_data_url": data_url,
            "meta": {
                "width": width,
                "height": height,
                "bytes": len(blob),
            },
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "error": str(exc),
        }
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except Exception:
                pass
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass
