"""Real source import must tolerate independently saved Selection/bind poses."""
import importlib.util
import copy
import json
import sys
from pathlib import Path

import bpy


addon_path, evidence, output = map(Path, sys.argv[sys.argv.index("--") + 1:])
spec = importlib.util.spec_from_file_location("eiem_mapping_test", addon_path)
addon = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = addon
spec.loader.exec_module(addon)
addon.register()
try:
    native = addon.physics_authoring.native
    payload = native.source.read_evidence(evidence)
    rig, objects = native.import_source(evidence)
    groups = [obj for obj in objects if obj.eiem_physics.kind == "NATIVE_GROUP"]
    assert len(objects) == len(payload["components"])
    assert len(groups) == sum(c["type"] == "BeyondBoneCloth" for c in payload["components"])
    unresolved = []
    for group in groups:
        if group.get(native.SELECTION_PREVIEW_ERROR):
            unresolved.append(group.name)
            assert json.loads(group[native.NODE_SAMPLE_CACHE]) == {}
            assert not any(child.get("eiem_physics_preview") == native.NODE_RADIUS_PREVIEW_MARKER
                           for child in group.children)
            continue
        graph = native.group_local_selection_graph(group, include_mapping=True)
        positions, attributes, parents, depths, mapping = graph
        assert len(mapping) == len(positions)
        assert len(set(mapping.values())) == len(mapping)
        assert all(-1 <= parent < len(positions) for parent in parents)
        assert all(0.0 <= depth <= 1.0 for depth in depths)
        assert native.NODE_SAMPLE_CACHE in group
    # These are the real failing saved-pose groups, not coincident points whose
    # identity cannot be proven. They must retain usable bone/curve previews.
    offset_groups = {"MC_pendant", "MC_pantsL", "MC_pantsR", "MC_sideHair"}
    assert not offset_groups.intersection(native.record(group)["name"]
                                          for group in groups if group.get(native.SELECTION_PREVIEW_ERROR))
    saved_group = next(group for group in groups if native.record(group)["name"] in offset_groups)
    component = native.record(saved_group)
    selection = component["fields"]["serializeData2"]["selectionData"]
    from mathutils import Vector
    def points(record):
        return [Vector(tuple(row[axis] for axis in "xyz")) for row in
                record["fields"]["serializeData2"]["selectionData"]["positions"]]
    baseline = native.prebuild_selection_graph(payload, component, points(component),
                                               [row["Value"] for row in selection["attributes"]])
    reordered = copy.deepcopy(component)
    reordered_selection = reordered["fields"]["serializeData2"]["selectionData"]
    reordered_selection["positions"].reverse()
    reordered_selection["attributes"].reverse()
    shuffled = native.prebuild_selection_graph(payload, reordered, points(reordered),
                                               [row["Value"] for row in reordered_selection["attributes"]])
    assert all(shuffled[4][identity] == len(baseline[0]) - index - 1 for identity, index in baseline[4].items())
    assert all(shuffled[3][shuffled[4][identity]] == baseline[3][index] for identity, index in baseline[4].items())
    # Coincident points cannot be disambiguated by array order.
    try:
        native.unique_selection_points([Vector()], {"a": Vector(), "b": Vector()})
        raise AssertionError("ambiguous source points were accepted")
    except native.SelectionPreviewError:
        pass
    # Unprovable prebuild geometry degrades the preview, never the source import
    # or parameter editor. Removing stale caches must not trigger it on draw.
    unknown = copy.deepcopy(payload)
    changed = next(c for c in unknown["components"] if c["source"] == component["source"])
    changed["fields"]["serializeData2"]["selectionData"]["positions"][0]["x"] += 1.0
    original_snapshot = native.snapshot
    original_record = native.record
    try:
        native.snapshot = lambda obj: unknown if obj == saved_group else original_snapshot(obj)
        native.record = lambda obj: changed if obj == saved_group else original_record(obj)
        native.rebuild(saved_group)
        assert saved_group.get(native.SELECTION_PREVIEW_ERROR)
        assert json.loads(saved_group[native.NODE_SAMPLE_CACHE]) == {}
        assert all(native.native_bone_sample(saved_group, bone) is None for bone in rig.data.bones)
        assert saved_group["eiem_physics_angle_preview"] == "unmapped-selection"
    finally:
        native.snapshot = original_snapshot
        native.record = original_record
        native.rebuild(saved_group)
    assert not saved_group.get(native.SELECTION_PREVIEW_ERROR)
    file = output / "source.physics"
    native.export_source(file, objects)
    exported = native.source.read(file)
    assert exported["components"] == payload["components"]
    assert exported["transforms"] == payload["transforms"]
    print("EFF_NATIVE_SELECTION_MAPPING_OK " + json.dumps({"groups": len(groups), "components": len(objects),
                                                           "unresolved": unresolved}, ensure_ascii=True), flush=True)
finally:
    addon.unregister()
