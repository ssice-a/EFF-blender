"""Real Blender authoring and export test, only inside a caller's temp folder."""
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import bpy
import bmesh

addon_path, output = map(Path, sys.argv[sys.argv.index("--") + 1:])
spec = importlib.util.spec_from_file_location("eiem_switch_test", addon_path)
addon = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = addon
spec.loader.exec_module(addon)
addon.register()
assert hasattr(bpy.types, "VIEW3D_PT_eiem_switches")
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)

mesh = bpy.data.meshes.new("Source")
verts = [(x + dx, dy, 0) for x in (0, 2, 4, 6)
         for dx, dy in ((0, 0), (0.8, 0), (0, 0.8))]
mesh.from_pydata(verts, [], [(i, i + 1, i + 2) for i in range(0, 12, 3)])
mesh["eiem_section"] = "MeshSource"
mesh["eiem_asset"] = "SourceAsset"
mesh["eiem_source"] = "assets/test/source.asset"
mesh["eiem_coordinate_space"] = "unity-y-up-left-handed"
obj = bpy.data.objects.new("Always", mesh)
bpy.context.scene.collection.objects.link(obj)
obj["eiem_render_section"] = "RenderSource"
obj["eiem_render_asset"] = "SourceAsset"
obj["eiem_author_package"] = str(output / "offline")
obj["eiem_original_material_sections_json"] = json.dumps({"0": "MaterialBase"})
material = addon.load_material(Path(), "MaterialBase", {
    "source": "assets/test/base.mat", "name": "Base", "float.Value": "0"})
mesh.materials.append(material)
arm = addon.make_armature("SkeletonSharedRig", {
    "coordinate": "unity-y-up-left-handed",
    "nodes": [('', -1, (0,0,0), (0,0,0,1), (1,1,1))] +
             [(name, 0, (0,0,-i), (0,0,0,1), (1,1,1))
              for i,name in enumerate(('Root','Tip','Unused'))]}, bpy.context.scene.collection)
for name in ("Root", "Tip", "Unused"):
    obj.vertex_groups.new(name=name)
obj.vertex_groups[0].add(list(range(6)), 1.0, "REPLACE")
obj.vertex_groups[1].add(list(range(6, 12)), 1.0, "REPLACE")
obj.modifiers.new("Skin", "ARMATURE").object = arm
obj["eiem_bone_palette_json"] = "[1,2,3]"
obj["eiem_bindposes_json"] = json.dumps([[int(r == c) for r in range(4) for c in range(4)]] * 3)
obj["eiem_bone_hashes_json"] = "[10,20,30]"
obj["eiem_bone_paths_json"] = '["Root","Tip","Unused"]'

def separate(source, minimum, maximum, name):
    bpy.ops.object.select_all(action="DESELECT")
    source.select_set(True)
    bpy.context.view_layer.objects.active = source
    before = set(bpy.data.objects)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="DESELECT")
    edit = bmesh.from_edit_mesh(source.data)
    for v in edit.verts:
        v.select_set(minimum <= v.co.x <= maximum)
    edit.select_flush_mode()
    bmesh.update_edit_mesh(source.data)
    bpy.ops.mesh.separate(type="SELECTED")
    bpy.ops.object.mode_set(mode="OBJECT")
    part, = set(bpy.data.objects) - before
    part.name = name
    return part

top = separate(obj, 2, 5, "Top")
accessory = separate(obj, 6, 7, "Accessory")
assert [len(o.data.polygons) for o in (obj, top, accessory)] == [1, 2, 1]
for part in (top, accessory):
    assert part.data["eiem_section"] == "MeshSource"
    assert part["eiem_bone_palette_json"] == "[1,2,3]"
    assert [g.name for g in part.vertex_groups] == ["Root", "Tip", "Unused"]

accessory.shape_key_add(name="Basis")
shape = accessory.shape_key_add(name="StockingBlend")
shape.data[0].co.z += .1
shape.value = .2
shape_control = addon.add_shape_control(accessory, shape.name, automatic=True)
shape_control.hotkey_speed = .5
assert addon.set_shape_control_hotkey(
    accessory, shape_control, "F8", "INCREASE") == "F8"
assert addon.set_shape_control_hotkey(
    accessory, shape_control, "F9", "DECREASE") == "F9"
top_group = addon.create_switch_group("TopSwitch", "F6", [top])
acc_group = addon.create_switch_group("AccessorySwitch", "F7", [accessory])
# Group membership is explicit and independent: the same Mesh can be added to
# two groups, then removed from one without changing the other.
assert obj not in addon.switch_members(top_group)
addon.add_switch_members(top_group, [obj], state=addon.switch_states(top_group)[0])
assert obj in addon.switch_members(top_group)
assert obj in addon.switch_meshes(addon.switch_states(top_group)[0])
addon.add_switch_members(acc_group, [obj])
assert obj in addon.switch_members(acc_group)
addon.remove_switch_members(top_group, [obj])
assert obj not in addon.switch_members(top_group)
assert obj in addon.switch_members(acc_group)
addon.remove_switch_members(acc_group, [obj])
assert obj not in addon.switch_members(acc_group)
shape.value = .8
accessory.hide_set(True)
addon.capture_switch_state(acc_group, addon.switch_states(acc_group)[1])
accessory.hide_set(False)

# Blender keyboard events are recorded into the finite runtime key vocabulary;
# duplicate assignments are allowed; the user owns key routing.
class KeyEvent:
    value = "PRESS"
    ctrl = shift = alt = False
    def __init__(self, event_type, **modifiers):
        self.type = event_type
        for key, value in modifiers.items(): setattr(self, key, value)

assert addon.switch_key_from_event(KeyEvent("A", ctrl=True, shift=True)) == "CTRL+SHIFT+A"
assert addon.switch_key_from_event(KeyEvent("ONE", alt=True)) == "ALT+1"
assert addon.switch_key_from_event(KeyEvent("PAGE_DOWN")) == "PAGEDOWN"
assert addon.switch_key_from_event(KeyEvent("NUMPAD_0")) == "NUMPAD0"
assert addon.switch_key_from_event(KeyEvent("NUMPAD_7", ctrl=True, alt=True)) == "CTRL+ALT+NUMPAD7"
assert addon.switch_key_from_event(KeyEvent("NUMPAD_PLUS", shift=True)) == "SHIFT+NUMPADPLUS"
assert addon.switch_key_from_event(KeyEvent("NUMPAD_MINUS")) == "NUMPADMINUS"
assert addon.switch_key_from_event(KeyEvent("NUMPAD_ASTERIX")) == "NUMPADMULTIPLY"
assert addon.switch_key_from_event(KeyEvent("NUMPAD_SLASH")) == "NUMPADDIVIDE"
assert addon.switch_key_from_event(KeyEvent("NUMPAD_PERIOD")) == "NUMPADDECIMAL"
assert addon.set_switch_group_key(top_group, "Ctrl+Shift+F8") == "CTRL+SHIFT+F8"
assert addon.set_switch_group_key(acc_group, "shift+ctrl+f8") == "CTRL+SHIFT+F8"
assert acc_group["eiem_key"] == top_group["eiem_key"]
addon.set_switch_group_key(acc_group, "F7")
assert addon.set_switch_group_key(
    top_group, "Ctrl+Alt+Numpad7") == "CTRL+ALT+NUMPAD7"
assert top_group["eiem_key"] == "CTRL+ALT+NUMPAD7"
variant = top.copy()
variant.data = top.data.copy()
variant.name = "Variant"
bpy.context.scene.collection.objects.link(variant)
variant_material = material.copy()
variant_material["eiem_source"] = "assets/test/other.mat"
variant.data.materials[0] = variant_material
variant_state = addon.add_switch_state(top_group, "Other style")
addon.assign_switch_meshes(variant_state, [variant])
variant_state.objects.link(top)  # one Mesh may be visible in several snapshots
assert variant not in addon.switch_meshes(addon.switch_states(top_group)[0])
assert top in addon.switch_meshes(addon.switch_states(top_group)[0])
assert top in addon.switch_meshes(variant_state)

# Drag/reorder semantics change the cycle sequence while stable state values
# and snapshot membership remain attached to their original collections.
state_values = {state.name: value for state, value in zip(
    addon.switch_states(top_group), addon.switch_state_values(top_group))}
assert addon.reorder_switch_state(top_group, variant_state, 1) == 1
assert [state.name for state in addon.switch_states(top_group)] == [
    "款式 1", "Other style", "款式 2"]
assert {state.name: value for state, value in zip(
    addon.switch_states(top_group), addon.switch_state_values(top_group))} == state_values

# Default and preview are distinct. Restoring recovers pre-existing eye state.
accessory.hide_set(True)
addon.preview_switch(top_group, next(
    state for state in addon.switch_states(top_group) if state.name == "款式 2"))
addon.preview_switch(acc_group, addon.switch_states(acc_group)[0])
assert top.hide_get() and variant.hide_get() and not accessory.hide_get()
assert abs(shape.value - .8) < 1e-6
assert addon.switch_states(top_group)[0]["eiem_default"]
addon.restore_switch_preview()
assert not top.hide_get() and not variant.hide_get() and accessory.hide_get()
assert abs(shape.value - .8) < 1e-6

plan = addon.plan_switch_export([top])
assert plan["objects"] == [top] and len(plan["groups"]) == 1
plan = addon.plan_switch_export([obj, top, accessory, variant])
assert set(plan["objects"]) == {obj, top, accessory, variant}
assert len(plan["sources"]) == 1 and len(plan["groups"]) == 2
declarations, bindings, hotkeys = addon.plan_shape_controls(plan["objects"])
assert len(hotkeys) == 2
assert {entry["key"] for entry in hotkeys} == {"F8", "F9"}
assert next(entry for entry in hotkeys if entry["key"] == "F8")["target"] == 1
assert next(entry for entry in hotkeys if entry["key"] == "F9")["target"] == 0
assert len(bpy.data.armatures) == 1
saved = output / "author.blend"
bpy.ops.wm.save_as_mainfile(filepath=str(saved))
bpy.ops.wm.open_mainfile(filepath=str(saved))
top = bpy.data.objects["Top"]
obj = bpy.data.objects["Always"]
accessory = bpy.data.objects["Accessory"]
variant = bpy.data.objects["Variant"]
top_group = bpy.data.collections["TopSwitch"]
acc_group = bpy.data.collections["AccessorySwitch"]
assert len(addon.switch_groups()) == 2
assert top_group["eiem_key"] == "CTRL+ALT+NUMPAD7"
selected = [obj, top, accessory, variant]
plan = addon.plan_switch_export(selected)
package = output / "package"
stats = addon.export_package(package, selected, [])
assert stats == {"meshes": 1, "materials": 2, "textures": 0, "skeletons": 0,
                 "physics": 0, "prefabs": 0}, stats
text = (package / "mod.ini").read_text(encoding="utf-8")
assert text.count("asset=SourceAsset") >= 1
assert "handling=skip" not in text
assert text.count("[KeySwitch") == 2
assert text.count("[KeyShape") == 2
assert "key=CTRL+ALT+NUMPAD7" in text
assert "key=F8" in text and "key=F9" in text
assert text.count("speed=0.5") == 2
assert "type=hold" in text and "=" + str(1) in text
assert "||" in text
assert text.count("[Render") == 1
assert "submesh_visible." in text
assert "partner." not in text
payloads = [addon.read_mesh(p) for p in (package / "meshes").glob("*.mesh")]
assert len(payloads) == 1 and len(payloads[0]["indices"]) == 18
for p in payloads:
    assert len(p["bindposes"]) == 3 and p["bone_hashes"] == [10, 20, 30]
    assert p["bone_paths"] == ["Root", "Tip", "Unused"]
    assert len(p["skin"]) == p["vertex_count"]

# Skin edits must invalidate the session cache.  A stale cache here would
# silently ship the previous vertex-group weights and bone palette.
cached_mesh, = (package / "meshes").glob("*.mesh")
before_skin = addon.read_mesh(cached_mesh)["skin"]
accessory.vertex_groups[0].add([0], .25, "REPLACE")
addon.export_package(package, selected, [])
after_skin = addon.read_mesh(cached_mesh)["skin"]
assert before_skin != after_skin

# Shape-key coordinates are serialized into the same Mesh resource.  They
# need their own content fingerprint because changing a key can leave the
# owning ID's revision unchanged in Blender.
before_shape_bytes = cached_mesh.read_bytes()
shape_block = accessory.data.shape_keys.key_blocks["StockingBlend"]
shape_block.data[0].co.z += .125
addon.export_package(package, selected, [])
after_shape_bytes = cached_mesh.read_bytes()
assert before_shape_bytes != after_shape_bytes
shape_block.data[0].co.z -= .125

# Export checkbox off keeps the authored default appearance and shape sliders,
# without exporting any key bindings. It does not change the saved project.
static_package = output / "without-switches"
assert bpy.ops.eiem.export_package.get_rna_type().properties[
    "include_switches"].default is True
assert addon.export_package(static_package, selected, [],
                            include_switches=False)["meshes"] == 1
static_ini = (static_package / "mod.ini").read_text(encoding="utf-8")
assert "[KeySwitch" not in static_ini and "[KeyShape" not in static_ini
assert "[KeyModUI]" not in static_ini and "$switch_" not in static_ini
assert "submesh_visible." not in static_ini
assert "[ShapeControl" in static_ini and "shape." in static_ini
assert len(addon.plan_switch_export(selected)["groups"]) == 2

# The off option must also allow an intentionally unused/invalid key binding.
acc_group["eiem_key"] = "Ctrl+Ctrl+F7"
assert addon.export_package(output / "without-keys", selected, [],
                            include_switches=False)["meshes"] == 1
acc_group["eiem_key"] = "F7"

# Preview state is never export selection: default off still exports geometry.
empty_state = next(state for state in addon.switch_states(top_group)
                   if state.name == "款式 2")
addon.set_switch_default(top_group, empty_state)
addon.preview_switch(top_group, empty_state)
addon.export_package(output / "default-off", selected, [])
default_static = addon.plan_switch_export(selected, include_switches=False)
assert top in default_static["hidden"] and variant in default_static["hidden"]
addon.export_package(output / "default-off-static", selected, [],
                     include_switches=False)
assert "[KeySwitch" not in (output / "default-off-static/mod.ini").read_text(
    encoding="utf-8")
addon.set_switch_default(top_group, addon.switch_states(top_group)[0])

# Validation errors must leave a previously working package untouched.
baseline = {str(p.relative_to(package)): p.read_bytes() for p in package.rglob("*") if p.is_file()}
def unchanged_on_error():
    try:
        addon.export_package(package, selected, [])
    except ValueError:
        pass
    else:
        raise AssertionError("expected export validation error")
    assert baseline == {str(p.relative_to(package)): p.read_bytes() for p in package.rglob("*") if p.is_file()}

acc_group["eiem_key"] = "CTRL+ALT+NUMPAD7"
unchanged_on_error()
acc_group["eiem_key"] = "F7"
acc_group["eiem_key"] = "Ctrl+Ctrl+F7"
unchanged_on_error()
acc_group["eiem_key"] = "F7"
# A Mesh may belong to several switch groups. The overlap is exported as
# unconditional visibility metadata so the runtime can apply last-operation
# precedence without creating another Renderer.
addon.switch_states(acc_group)[0].objects.link(top)
overlap_plan = addon.plan_switch_export(selected)
assert len(overlap_plan["bindings"][top]) == 2
overlap_package = output / "overlap"
addon.export_package(overlap_package, selected, [])
overlap_text = (overlap_package / "mod.ini").read_text(encoding="utf-8")
assert "visibility.0.variable=" in overlap_text
assert "visibility.0.masks=" in overlap_text
assert "visibility.0.controlled=" in overlap_text
addon.switch_states(acc_group)[0].objects.unlink(top)
top["eiem_bone_paths_json"] = '["Root","Missing","Unused"]'
unchanged_on_error()
top["eiem_bone_paths_json"] = '["Root","Tip","Unused"]'

# UI operator properties may retain the name of a state deleted by the prior
# click. The list-side minus must always resolve its current active index.
bpy.context.scene.eiem_switch_active = top_group
top_group.eiem_switch_state_index = 1
assert bpy.ops.eiem.switch_state(
    action="REMOVE_ACTIVE", state_name="Already Deleted") == {'FINISHED'}
assert bpy.data.collections.get("Other style") is None
assert [state.name for state in addon.switch_states(top_group)] == [
    "款式 1", "款式 2"]
assert bpy.data.objects.get("Variant") == variant

# Blender invokes Panel.draw with a restricted context that forbids writes to
# ID data-blocks. Drawing must clamp a stale list index locally, never assign it.
class RestrictedGroup:
    def __init__(self):
        object.__setattr__(self, "name", "Restricted")
        object.__setattr__(self, "eiem_switch_state_index", 99)
    def __setattr__(self, name, value):
        if name == "eiem_switch_state_index":
            raise AttributeError("Writing to ID classes in this context is not allowed")
        object.__setattr__(self, name, value)
    def get(self, name, default=None):
        return {"eiem_switch_group": True, "eiem_key": "F12"}.get(name, default)

class PanelLayout:
    def __getattr__(self, name):
        def call(*args, **kwargs):
            return self
        return call

restricted_group = RestrictedGroup()
restricted_states = [SimpleNamespace(name="A"), SimpleNamespace(name="B")]
saved_functions = addon.switch_groups, addon.switch_members, addon.switch_states
try:
    addon.switch_groups = lambda scene: [restricted_group]
    addon.switch_members = lambda group: []
    addon.switch_states = lambda group: restricted_states
    restricted_context = SimpleNamespace(
        scene=SimpleNamespace(eiem_switch_active=restricted_group),
        object=None)
    addon.EFF_PT_switches.draw(
        SimpleNamespace(layout=PanelLayout()), restricted_context)
finally:
    addon.switch_groups, addon.switch_members, addon.switch_states = saved_functions

# Deleting an authoring group removes its snapshots but never its Meshes.
addon.delete_switch_group(acc_group)
assert bpy.data.objects.get("Accessory") == accessory
assert bpy.data.collections.get("AccessorySwitch") is None
assert len(addon.switch_groups()) == 1
assert bpy.context.scene.eiem_switch_active is None

# Re-registering the add-on does not erase project semantics.
addon.unregister()
addon.register()
assert len(addon.plan_switch_export(selected)["groups"]) == 1

# Exported switch conditions are executable EFF syntax rather than ordinary
# INI keys.  They must not prevent the same package from being imported again.
assert addon.import_package(package, clean=True) == 1
addon.unregister()
print("EFF_SWITCHES_OK", stats)
