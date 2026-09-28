"""Background-only shape control authoring; never accesses a live user scene."""
import importlib.util
import sys
import re
from pathlib import Path
from types import SimpleNamespace
import bpy

addon_path, output = map(Path, sys.argv[sys.argv.index("--") + 1:])
spec = importlib.util.spec_from_file_location("eiem_shapes_test", addon_path)
addon = importlib.util.module_from_spec(spec)
spec.loader.exec_module(addon)
addon.register()
assert hasattr(bpy.types.Scene, "eiem_export_mod_name")
assert addon.mod_export_directory(output) == output
assert addon.mod_export_directory(output, output / "custom_mod") == output / "custom_mod"
assert addon.mod_export_directory("", output / "custom_mod") == output / "custom_mod"
assert addon.mod_export_directory(output, mod_name="服装测试") == output / "服装测试"
try:
    addon.mod_export_directory(output, mod_name="../outside")
    raise AssertionError("path traversal accepted as Mod folder name")
except ValueError:
    pass
mesh = bpy.data.meshes.new("Source")
mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
mesh["eiem_section"] = "MeshSource"
mesh["eiem_asset"] = "SourceAsset"
mesh["eiem_coordinate_space"] = "unity-y-up-left-handed"
obj = bpy.data.objects.new("Source", mesh)
bpy.context.scene.collection.objects.link(obj)
obj["eiem_render_asset"] = "SourceAsset"
obj["eiem_render_section"] = "RenderSource"
obj["eiem_author_package"] = str(output / "offline")
bpy.context.view_layer.objects.active = obj
obj.select_set(True)
basis = obj.shape_key_add(name="Renamed Basis")
key = obj.shape_key_add(name="Inflate")
key.data[0].co.z += 0.5
key.value = 0.25
control = addon.add_shape_control(obj, key.name)
control.label = "衣服鼓起"
assert control.default == 0.25
assert len(mesh.eiem_shape_controls) == 1
assert addon.add_shape_control(obj, key.name) == control

# Invoke the actual panel drawing with a layout recorder inside real Blender.
class Layout:
    def __getattr__(self, name):
        def call(*args, **kwargs):
            if name == "prop": assert hasattr(args[0], args[1])
            return self
        return call
addon.EFF_PT_shape_controls.draw(SimpleNamespace(layout=Layout()), bpy.context)
bpy.ops.wm.save_as_mainfile(filepath=str(output / "author.blend"))
bpy.ops.wm.open_mainfile(filepath=str(output / "author.blend"))
obj = bpy.data.objects["Source"]
control = obj.data.eiem_shape_controls[0]  # reload invalidates pre-save RNA references
assert obj.data.eiem_shape_controls[0].shape == "Inflate"
assert obj.data.eiem_shape_controls[0].label == "衣服鼓起"

package = output / "package"
addon.export_package(package, [obj], [])
ini = (package / "mod.ini").read_text(encoding="utf-8")
variable = re.search(r"shape\.Inflate=(\$\w+)", ini).group(1)
assert "[UIMod]" not in ini
assert not (package / "ui.lua").exists()
assert ("persist %s=0.25" % variable) in ini and "[Constants]" in ini
payload = addon.read_mesh(next((package / "meshes").glob("*.mesh")))
assert payload["blend_channels"][0][0] == "Inflate"
assert payload["blend_weights"] == [100.0]  # NOT the current .25 slider weight
assert payload["blend_vertices"][0][1] == (0.0, 0.0, 0.5)

addon.export_package(output / "no-ui", [obj], [])
no_ui_ini = (output / "no-ui/mod.ini").read_text(encoding="utf-8")
assert "[UIMod]" not in no_ui_ini
assert "[ShapeControl1]" in no_ui_ini
assert "variable=" + variable in no_ui_ini and "label=衣服鼓起" in no_ui_ini
assert "min=0\nmax=1" in no_ui_ini
assert "[KeyShape" not in no_ui_ini and "shape.Inflate=" + variable in no_ui_ini
assert not (output / "no-ui/ui.lua").exists()
# Directional shape keys are continuous hold bindings, not duplicate cycle
# endpoints. The runtime advances the variable while the key is down.
control.hotkey_increase = "F7"
control.hotkey_speed = 0.5
hold_package = output / "hold"
addon.export_package(hold_package, [obj], [])
hold_ini = (hold_package / "mod.ini").read_text(encoding="utf-8")
assert "[KeyShape1]" in hold_ini and "type=hold" in hold_ini
assert "speed=0.5" in hold_ini and "\n" + variable + "=1\n" in hold_ini
control.hotkey_increase = ""
addon.export_package(output / "always-ui", [obj], [])
always_ini = (output / "always-ui/mod.ini").read_text(encoding="utf-8")
assert "[UIMod]" not in always_ini and "[KeyModUI]" not in always_ini

# A switched Mesh stays on the source Renderer; visibility changes its submesh.
addon.create_switch_group("Cloth", "F6", [obj])
addon.export_package(output / "switched", [obj], [])
ini = (output / "switched/mod.ini").read_text(encoding="utf-8")
render = ini.split("[RenderSource]", 1)[1]
assert "partner." not in ini and "submesh_visible.0" in render
assert ("shape.Inflate=" + variable) in render

# Shared resource controls are emitted once; distinct resources remain independent.
copy = obj.copy()
copy.name = "Shared"
bpy.context.scene.collection.objects.link(copy)
controls, bindings, hotkeys = addon.plan_shape_controls([obj, copy])
assert len(controls) == 1 and bindings[obj] == bindings[copy]
copy.data = obj.data.copy()
copy["eiem_render_asset"] = "OtherAsset"
copy.data["eiem_target_asset"] = "OtherAsset"
controls, bindings, hotkeys = addon.plan_shape_controls([obj, copy])
assert len(controls) == 2 and bindings[obj] != bindings[copy]
copy.data.eiem_shape_controls[0].shape = "Missing"
try:
    addon.plan_shape_controls([copy])
    raise AssertionError("Missing channel accepted")
except ValueError:
    pass
obj.data.shape_keys.use_relative = False
try:
    addon.export_package(output / "bad", [obj], [])
    raise AssertionError("Absolute shapes accepted")
except ValueError:
    pass
assert not (output / "bad/mod.ini").exists()
obj.data.shape_keys.use_relative = True
stale_ui = output / "stale-ui"
addon.export_package(stale_ui, [obj], [])
assert not (stale_ui / "ui.lua").exists()
addon.export_package(stale_ui, [obj], [], include_switches=False)
assert not (stale_ui / "ui.lua").exists()
assert "[UIMod]" not in (stale_ui / "mod.ini").read_text(encoding="utf-8")

# Repeating one destination reuses the binary Mesh instead of entering the
# expensive Blender RNA serialization path again.
cache_probe = output / "cache-probe"
original_write_mesh = addon.write_mesh
write_calls = []
def counted_write_mesh(path, value):
    write_calls.append(value.name)
    return original_write_mesh(path, value)
addon.write_mesh = counted_write_mesh
try:
    addon.export_package(cache_probe, [obj], [])
    first_write_count = len(write_calls)
    mesh_file = next((cache_probe / "meshes").glob("*.mesh"))
    (cache_probe / "meshes" / "stale.mesh").write_bytes(b"stale")
    addon.export_package(cache_probe, [obj], [])
    assert first_write_count == 1 and len(write_calls) == first_write_count
    assert not (cache_probe / "meshes" / "stale.mesh").exists()
    mesh_file.unlink()
    addon.export_package(cache_probe, [obj], [])
    assert mesh_file.is_file()
finally:
    addon.write_mesh = original_write_mesh
addon.unregister()
addon.register()
assert obj.data.eiem_shape_controls[0].shape == "Inflate"
addon.unregister()
print("EFF_SHAPES_OK")
