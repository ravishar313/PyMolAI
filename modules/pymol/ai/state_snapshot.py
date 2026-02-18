from __future__ import annotations

from typing import Dict, List, Any


def _as_list(value) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def build_viewer_state_snapshot(
    cmd,
    *,
    max_objects: int = 30,
    max_selections: int = 20,
    recent_tool_results: List[Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    objects = _as_list(cmd.get_names("objects"))
    enabled_objects = _as_list(cmd.get_names("objects", enabled_only=1))
    selections = _as_list(cmd.get_names("public_selections", enabled_only=1))

    selection_counts: Dict[str, int] = {}
    for name in selections[:max_selections]:
        try:
            selection_counts[name] = int(cmd.count_atoms(name))
        except Exception:
            selection_counts[name] = -1

    try:
        vis = cmd.get_vis()
    except Exception:
        vis = {}

    try:
        view = cmd.get_view(0, 1)
    except Exception:
        view = []

    try:
        viewport = cmd.get_viewport(0, 1)
    except Exception:
        viewport = []

    try:
        object_list = _as_list(cmd.get_object_list("(all)", 1))
    except Exception:
        object_list = []

    recent = list(recent_tool_results or [])[-10:]

    return {
        "objects": objects[:max_objects],
        "enabled_objects": enabled_objects[:max_objects],
        "object_list": object_list[:max_objects],
        "selections": selections[:max_selections],
        "selection_counts": selection_counts,
        "vis": vis,
        "view": view,
        "viewport": viewport,
        "recent_tool_results": recent,
    }
