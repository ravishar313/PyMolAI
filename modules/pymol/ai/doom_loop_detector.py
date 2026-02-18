from __future__ import annotations

from collections import deque
import json
import re
from collections import Counter
from typing import Any, Dict, Optional, Tuple


class DoomLoopDetector:
    def __init__(self, threshold: int = 3):
        self.threshold = max(2, int(threshold))
        self._recent: deque[Tuple[str, str, bool]] = deque(maxlen=self.threshold)
        self._recent_families: deque[str] = deque(maxlen=max(4, self.threshold))
        self._recent_intents: deque[str] = deque(maxlen=max(4, self.threshold))

    def _normalize_args(self, arguments: Dict[str, Any]) -> str:
        try:
            return json.dumps(arguments, sort_keys=True, ensure_ascii=False)
        except Exception:
            return "{}"

    @staticmethod
    def _normalize_intent_text(text: str) -> str:
        raw = str(text or "").strip().lower()
        if not raw:
            return ""
        raw = re.sub(r"[\s]+", " ", raw)
        raw = re.sub(r"[^\w\s]", "", raw)
        return raw.strip()

    @staticmethod
    def _command_family(tool_name: str, arguments: Dict[str, Any]) -> str:
        if tool_name == "run_pymol_command":
            command = str(arguments.get("command") or "").strip().lower()
            if not command:
                return "run_pymol_command"
            return "cmd:%s" % (command.split(None, 1)[0],)
        return str(tool_name or "unknown").strip().lower() or "unknown"

    @staticmethod
    def _is_oscillation(seq) -> bool:
        if len(seq) < 3:
            return False
        kinds = set(seq)
        if len(kinds) != 2:
            return False
        for i in range(2, len(seq)):
            if seq[i] != seq[i - 2]:
                return False
        for i in range(1, len(seq)):
            if seq[i] == seq[i - 1]:
                return False
        return True

    def add_assistant_intent(self, assistant_text: str) -> Optional[Dict[str, Any]]:
        norm = self._normalize_intent_text(assistant_text)
        if not norm:
            return None

        self._recent_intents.append(norm)
        if len(self._recent_intents) < self.threshold:
            return None

        window = list(self._recent_intents)[-self.threshold :]
        counts = Counter(window)
        top, count = counts.most_common(1)[0]
        if len(top) >= 20 and count >= self.threshold - 1:
            return {
                "loop_type": "assistant_intent_repeat",
                "call_count": count,
                "assistant_intent": top,
            }
        return None

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
        family = self._command_family(tool_name, arguments)
        self._recent_families.append(family)

        if len(self._recent) < self.threshold:
            return None

        first = self._recent[0]
        if all(item == first for item in self._recent):
            return {
                "tool_name": tool_name,
                "call_count": self.threshold,
                "loop_type": "exact_match",
            }

        if len(self._recent_families) >= self.threshold:
            families = list(self._recent_families)[-self.threshold :]
            if all(f == families[0] for f in families):
                return {
                    "tool_name": tool_name,
                    "call_count": self.threshold,
                    "loop_type": "command_family_repeat",
                    "command_family": families[0],
                }

            if self._is_oscillation(families):
                return {
                    "tool_name": tool_name,
                    "call_count": self.threshold,
                    "loop_type": "command_family_oscillation",
                    "command_family_sequence": families,
                }

        return None

    def clear(self):
        self._recent.clear()
        self._recent_families.clear()
        self._recent_intents.clear()
