from __future__ import annotations

import re


_RE_ASSIGNMENT = re.compile(r"^[A-Za-z_]\w*\s*=")
_RE_PY_CALL = re.compile(r"^[A-Za-z_]\w*\s*\(")

_PY_LEADS = {
    "import",
    "from",
    "for",
    "if",
    "while",
    "def",
    "class",
    "try",
    "with",
    "return",
    "print",
}

_NL_HINTS = {
    "show",
    "hide",
    "color",
    "make",
    "please",
    "can",
    "could",
    "load",
    "fetch",
    "zoom",
    "orient",
    "display",
}

_PROSE_MARKERS = {
    " please ",
    " can you ",
    " could you ",
    " for me ",
    " then ",
    " and ",
    " with ",
    " the ",
    " this ",
    " as ",
    " by ",
}


def _looks_like_prose(text: str, cmd) -> bool:
    lowered = " " + text.lower().strip() + " "
    if any(marker in lowered for marker in _PROSE_MARKERS):
        return True

    first = text.split(None, 1)[0]
    remainder = text[len(first) :].strip()
    if not remainder:
        return False

    words = re.findall(r"[A-Za-z]+", remainder)
    if len(words) >= 4 and "," not in text and "=" not in text and "(" not in text:
        kwhash = getattr(cmd, "kwhash", None)
        if kwhash is not None and first in kwhash:
            return True

    return False


def is_direct_command(text: str, cmd) -> bool:
    stripped = text.strip()
    if not stripped:
        return True

    if stripped.startswith(("/", "@")):
        return True

    if stripped.startswith("_"):
        return True

    if ";" in stripped:
        return True

    if _RE_ASSIGNMENT.match(stripped) or _RE_PY_CALL.match(stripped):
        return True

    first = stripped.split(None, 1)[0]
    if first in _PY_LEADS:
        return True

    kwhash = getattr(cmd, "kwhash", None)
    if kwhash is not None and first in kwhash:
        return True

    return False


def should_route_to_ai(text: str, cmd) -> bool:
    stripped = text.strip()
    if not stripped:
        return False

    if is_direct_command(stripped, cmd):
        return _looks_like_prose(stripped, cmd)

    words = re.findall(r"[A-Za-z]+", stripped)
    if len(words) < 3:
        return False

    if stripped.endswith("?"):
        return True

    if words and words[0].lower() in _NL_HINTS:
        return True

    # Conservative fallback: prefer preserving native command flow.
    return False
