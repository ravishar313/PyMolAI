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


def _selection_atom_ids(cmd, selection: str, limit: int) -> List[str]:
    out: List[str] = []
    try:
        cmd.iterate(
            "(%s)" % (selection,),
            (
                'out.append("%s/%s/%s/%s/%s/%s/%s" % '
                "(model, chain, segi, resi, resn, name, index))"
            ),
            space={"out": out},
        )
    except Exception:
        return []
    if limit <= 0:
        return []
    return out[:limit]


def build_viewer_state_snapshot(
    cmd,
    *,
    max_objects: int = 30,
    max_selections: int = 20,
    max_selection_atoms: int = 12,
    max_detailed_selections: int = 4,
    max_selection_atom_count_for_details: int = 200,
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

    selection_atom_ids: Dict[str, List[str]] = {}
    selection_atom_ids_truncated: Dict[str, bool] = {}
    detailed = 0
    for name in selections[:max_selections]:
        if detailed >= max(0, int(max_detailed_selections)):
            break
        count = int(selection_counts.get(name, -1))
        if count <= 0:
            continue
        if count > max(1, int(max_selection_atom_count_for_details)):
            continue
        ids = _selection_atom_ids(cmd, name, max(1, int(max_selection_atoms)))
        if not ids:
            continue
        selection_atom_ids[name] = ids
        selection_atom_ids_truncated[name] = bool(count > len(ids))
        detailed += 1

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
        "selection_atom_ids": selection_atom_ids,
        "selection_atom_ids_truncated": selection_atom_ids_truncated,
        "vis": vis,
        "view": view,
        "viewport": viewport,
        "recent_tool_results": recent,
    }
