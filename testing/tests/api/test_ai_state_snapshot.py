from types import SimpleNamespace

from pymol.ai.state_snapshot import build_viewer_state_snapshot


class DummyCmd:
    def get_names(self, type_name, enabled_only=0):
        if type_name == "objects":
            return ["obj1", "obj2", "obj3"] if enabled_only == 0 else ["obj1"]
        if type_name == "public_selections":
            return ["sel1", "sel2", "sel3"]
        return []

    def count_atoms(self, selection):
        return {"sel1": 10, "sel2": 5, "sel3": 2}.get(selection, 0)

    def get_vis(self):
        return {"obj1": 1}

    def get_view(self, output=0, quiet=1):
        return [0.0] * 18

    def get_viewport(self, output=0, quiet=1):
        return [800, 600]

    def get_object_list(self, selection="(all)", quiet=1):
        return ["obj1", "obj2"]


def test_snapshot_schema_and_limits():
    snap = build_viewer_state_snapshot(
        DummyCmd(),
        max_objects=2,
        max_selections=2,
        recent_tool_results=[{"command": "zoom", "ok": True, "error": ""}],
    )

    assert snap["objects"] == ["obj1", "obj2"]
    assert snap["enabled_objects"] == ["obj1"]
    assert snap["selections"] == ["sel1", "sel2"]
    assert "selection_counts" in snap
    assert "view" in snap
    assert "viewport" in snap
    assert "recent_tool_results" in snap


def test_snapshot_handles_empty_state():
    class EmptyCmd:
        def get_names(self, *args, **kwargs):
            return []
        def count_atoms(self, selection):
            return 0
        def get_vis(self):
            return {}
        def get_view(self, output=0, quiet=1):
            return []
        def get_viewport(self, output=0, quiet=1):
            return []
        def get_object_list(self, selection="(all)", quiet=1):
            return []

    snap = build_viewer_state_snapshot(EmptyCmd())
    assert snap["objects"] == []
    assert snap["selections"] == []
