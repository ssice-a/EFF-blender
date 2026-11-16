"""Recover a missing inline Material editor without borrowing another root's UI.

Run with factory startup: -- ADDON SOURCE OUTPUT.
"""
import copy
import importlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import bpy

addon_path, source, output = map(Path, sys.argv[sys.argv.index('--') + 1:])
output.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(addon_path.resolve().parent))
addon = importlib.import_module(addon_path.name)
addon.register()
api = addon.eiem_blender_addon.effect_editor
root = api.import_source(bpy.context, str(source))
edit = api.editor(root)
node = next(o for o in api.objects(root) if any(
    edit.objects[i]['classId'] == 199
    for i in json.loads(o.get('eff_effect_components', '[]'))))
renderer = next(i for i in json.loads(node['eff_effect_components'])
                if edit.objects[i]['classId'] == 199)
mid = api.preview._target(edit, renderer, ['m_Materials', 0])
author = api.assets.material_object(root, mid)
material = api.material_bridge.material_for(author)
assert author and material

# The native document and Renderer still reference this material, but its
# editing object was moved into a different effect root in an older project.
donor = api.import_source(bpy.context, str(source))
donor.name = 'Other effect root'
donor_author = api.assets.material_object(donor, mid)
donor_material = api.material_bridge.material_for(donor_author)
bpy.data.objects.remove(donor_author, do_unlink=True)
bpy.data.materials.remove(donor_material, do_unlink=True)
author.parent = donor
material.eiem_editor_values['eiem_float._TintColorIntensity'].scalar = 77
assert api.assets.material_object(root, mid) is None
baseline = copy.deepcopy(edit.state)
counts = len(bpy.data.objects), len(bpy.data.materials)

class Layout:
    def __init__(self):
        self.actions = []
    def panel(self, *args, **kwargs):return self, self
    def label(self, **kwargs):pass
    def operator(self, name, **kwargs):
        op = SimpleNamespace()
        self.actions.append((name, kwargs, op))
        return op

layout = Layout()
api.effect_ui.Draw(api, node, edit, renderer).reference(layout, ['m_Materials', 0])
assert (len(bpy.data.objects), len(bpy.data.materials)) == counts, 'panel draw created native/editor assets'
assert edit.state == baseline, 'panel draw modified the native document'
action = next((op for name, _, op in layout.actions if name == 'eiem.effect_resource'), None)
assert action is not None, 'missing Material editor has no recovery action at its Renderer reference'
assert action.identity == mid and not action.select_object

bpy.context.view_layer.objects.active = node
node.select_set(True)
assert bpy.ops.eiem.effect_resource(identity=mid, select_object=False) == {'FINISHED'}
recovered = api.assets.material_object(root, mid)
assert recovered and recovered != author and recovered.parent == root
assert bpy.context.object == node, 'inline recovery jumped away from the emitter'
assert author.parent == donor and api.material_bridge.material_for(author) == material
assert material.eiem_editor_values['eiem_float._TintColorIntensity'].scalar == 77
recovered_material = api.material_bridge.material_for(recovered)
assert recovered_material and recovered_material != material
assert api.synchronize(root).value(mid) == baseline['values'][mid], 'recovery changed native material values'
assert not api.assets.export_document(root, api.synchronize(root)).get('assets'), 'opening the editor duplicated unedited game resources'
counts = len(bpy.data.objects), len(bpy.data.materials)
assert bpy.ops.eiem.effect_resource(identity=mid, select_object=False) == {'FINISHED'}
assert (len(bpy.data.objects), len(bpy.data.materials)) == counts, 'repeated recovery duplicated the editor'
try:
    result = bpy.ops.eiem.effect_resource(identity=renderer, select_object=False)
except RuntimeError as error:
    assert 'Material' in str(error)
else:
    assert result == {'CANCELLED'}
assert (len(bpy.data.objects), len(bpy.data.materials)) == counts, 'invalid target created an editor'

recovered_material.eiem_editor_values['eiem_float._TintColorIntensity'].scalar = 2.5
api.store(root, api.synchronize(root))
names = root.name, recovered.name, recovered_material.name
project = output / 'material-recovery.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(project))
bpy.ops.wm.open_mainfile(filepath=str(project), load_ui=False)
root, recovered = (bpy.data.objects[n] for n in names[:2])
assert api.assets.material_object(root, mid) == recovered
assert api.material_bridge.material_for(recovered).name == names[2]
assert api.material_bridge.material_for(recovered)['eiem_float._TintColorIntensity'] == '2.5'
print('PASS inline material recovery: read-only draw, exact root, native values, game references, idempotence and save/reopen')
addon.unregister()
