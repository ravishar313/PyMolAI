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

    def iterate(self, selection, expression, space=None):
        space = space or {}
        out = space.setdefault("out", [])
        atoms = {
            "sel1": [
                "obj1/A//10/GLY/CA/1",
                "obj1/A//10/GLY/N/2",
                "obj1/A//10/GLY/C/3",
            ],
            "sel2": [
                "obj1/A//20/SER/OG/4",
                "obj1/A//20/SER/CB/5",
            ],
        }
        key = str(selection).strip().strip("()")
        out.extend(atoms.get(key, []))


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
    assert snap["selection_atom_ids"]["sel1"] == [
        "obj1/A//10/GLY/CA/1",
        "obj1/A//10/GLY/N/2",
        "obj1/A//10/GLY/C/3",
    ]
    assert snap["selection_atom_ids_truncated"]["sel1"] is True
    assert snap["selection_atom_ids_truncated"]["sel2"] is True
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
        def iterate(self, selection, expression, space=None):
            return None

    snap = build_viewer_state_snapshot(EmptyCmd())
    assert snap["objects"] == []
    assert snap["selections"] == []


def test_snapshot_skips_large_selections_for_atom_details():
    class LargeSelCmd(DummyCmd):
        def count_atoms(self, selection):
            return {"sel1": 1000, "sel2": 5, "sel3": 2}.get(selection, 0)

    snap = build_viewer_state_snapshot(
        LargeSelCmd(),
        max_selection_atoms=2,
        max_detailed_selections=2,
        max_selection_atom_count_for_details=100,
    )
    assert "sel1" not in snap["selection_atom_ids"]
    assert "sel2" in snap["selection_atom_ids"]
    assert len(snap["selection_atom_ids"]["sel2"]) == 2
