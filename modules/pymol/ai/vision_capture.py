from __future__ import annotations

import base64
import os
import tempfile
from typing import Dict, Any


def _safe_viewport(cmd) -> tuple[int, int]:
    try:
        vp = cmd.get_viewport(output=0, quiet=1)
        if isinstance(vp, (list, tuple)) and len(vp) >= 2:
            w = int(vp[0])
            h = int(vp[1])
            if w > 0 and h > 0:
                return (w, h)
    except Exception:
        pass
    return (0, 0)


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

        requested_width = max(0, int(width or 0))
        requested_height = max(0, int(height or 0))
        vpw, vph = _safe_viewport(cmd)

        # Non-invasive capture: never resize the live viewport for AI snapshots.
        # 1) Try prior framebuffer image (fast path, no redraw/mutation)
        # 2) Fallback to current viewport render (still width=0,height=0)
        try:
            cmd.png(path, width=0, height=0, ray=0, quiet=1, prior=1)
        except Exception:
            cmd.png(path, width=0, height=0, ray=0, quiet=1, prior=0)

        with open(path, "rb") as handle:
            blob = handle.read()

        data_url = "data:image/png;base64," + base64.b64encode(blob).decode("ascii")

        return {
            "ok": True,
            "image_data_url": data_url,
            "meta": {
                "width": vpw if vpw > 0 else requested_width,
                "height": vph if vph > 0 else requested_height,
                "bytes": len(blob),
                "requested_width": requested_width,
                "requested_height": requested_height,
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
