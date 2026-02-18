from __future__ import annotations

from collections import deque
from typing import Any, Dict, Optional, Tuple
import json


class DoomLoopDetector:
    def __init__(self, threshold: int = 3):
        self.threshold = max(2, int(threshold))
        self._recent: deque[Tuple[str, str, bool]] = deque(maxlen=self.threshold)

    def _normalize_args(self, arguments: Dict[str, Any]) -> str:
        try:
            return json.dumps(arguments, sort_keys=True, ensure_ascii=False)
        except Exception:
            return "{}"

    def add_call(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        validation_required: bool = False,
    ) -> Optional[Dict[str, Any]]:
        if tool_name == "capture_viewer_snapshot":
            return None

        sig = (tool_name, self._normalize_args(arguments), bool(validation_required))
        self._recent.append(sig)

        if len(self._recent) < self.threshold:
            return None

        first = self._recent[0]
        if all(item == first for item in self._recent):
            return {
                "tool_name": tool_name,
                "call_count": self.threshold,
                "loop_type": "exact_match",
            }

        return None

    def clear(self):
        self._recent.clear()
