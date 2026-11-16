"""Real mixed effect: find a nested Mesh emitter and pick any scene Mesh."""
import importlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import bpy


addon_path, fixture = map(Path, sys.argv[sys.argv.index('--') + 1:])
sys.path.insert(0, str(addon_path.resolve().parent))
addon = importlib.import_module(addon_path.name)
addon.register()
fx = addon.eiem_blender_addon.effect_editor
payload = json.loads(fixture.read_bytes())
state = fx.document.load_source(payload['source'])
fx.document.apply_edit(state, payload)
root = fx.import_workspace(bpy.context, state, preserve_source=True)
root.eff_effect.preview = False
edit = fx.editor(root)
before = fx.document.dumps(edit.export())
emitter = next(o for o in fx.objects(root) if o.name == '网格自旋_fx_nr007')
emitter.name = '重命名后的 Mesh 发射器'


class Layout:
    def __init__(self, records=None):
        self.records = [] if records is None else records

    def row(self, **kw): return Layout(self.records)
    def box(self): return Layout(self.records)
    def column(self, **kw): return Layout(self.records)
    def split(self, **kw): return Layout(self.records)
    def panel(self, *args, **kw): return Layout(self.records), None
    def separator(self, **kw): pass
    def label(self, **kw):
        assert kw.get('icon') != 'ERROR', kw
        self.records.append(('label', kw.get('text', '')))
    def prop(self, owner, name, **kw):
        getattr(owner, name) if not name.startswith('[') else None
        self.records.append(('prop', name))
    def operator(self, name, **kw):
        op = SimpleNamespace(idname=name, text=kw.get('text', ''))
        self.records.append(('operator', op))
        return op


layout = Layout()
fx.draw_structure(layout, root, root, edit)
choices = [op for kind, op in layout.records
           if kind == 'operator' and op.idname == 'eiem.effect_select']
assert emitter.name in [op.object_name for op in choices], 'folded root UI hides nested Mesh emitter'
assert any(emitter.name in op.text and 'Mesh' in op.text for op in choices), 'root does not show the actual emitter name and render mode'
assert len(choices) == 4, 'root shortcuts must list only its four live emitters'

renderer = next(i for i in json.loads(emitter['eff_effect_components']) if edit.objects[i]['classId'] == 199)
layout = Layout()
fx.effect_ui.Draw(fx, emitter, edit, renderer).renderer(layout)
assert next(name for kind, name in layout.records if kind == 'prop') == 'particle_mesh', 'scene Mesh picker is buried after source fields'
assert ('prop', 'particle_mesh_pick') in layout.records, 'bound Mesh has only the native clear X; independent empty eyedropper is missing'
prop = emitter.eff_effect.bl_rna.properties['particle_mesh']
assert prop.type == 'POINTER' and prop.fixed_type.identifier == 'Object', 'Mesh selector must use Blender native Object picking'
picker = emitter.eff_effect.bl_rna.properties['particle_mesh_pick']
assert picker.type == 'POINTER' and picker.fixed_type.identifier == 'Object'
assert picker.is_skip_save, 'temporary picking UI must not become saved author data'
original = emitter.eff_effect.particle_mesh
assert original and emitter.eff_effect.particle_mesh_pick is None, 'eyedropper must stay empty while actual Mesh remains bound'
mesh = bpy.data.objects.new('任意场景网格', bpy.data.meshes.new('任意场景网格数据'))
bpy.context.scene.collection.objects.link(mesh)
emitter.eff_effect.particle_mesh_pick = None
assert emitter.eff_effect.particle_mesh == original, 'canceling a pick cleared the existing Mesh'
emitter.eff_effect.particle_mesh_pick = mesh
assert emitter.eff_effect.particle_mesh == mesh, 'native picker update did not bind the sampled scene Mesh'
assert emitter.eff_effect.particle_mesh_pick == mesh, 'picker reset before native eyedropper can verify its result'
assert bpy.app.timers.is_registered(fx.clear_mesh_pick_slots), 'picker reset was not scheduled'
assert fx.clear_mesh_pick_slots() is None
assert emitter.eff_effect.particle_mesh_pick is None and emitter.eff_effect.particle_mesh == mesh, 'reset must restore the empty eyedropper without clearing the binding'
emitter.eff_effect.particle_mesh_pick = original
assert emitter.eff_effect.particle_mesh == original, 'eyedropper cannot replace an already bound Mesh a second time'
fx.clear_mesh_pick_slots()
fx.preview._pending_updates.clear()
emitter.eff_effect.particle_mesh_pick = None
assert not fx.preview._pending_updates, 'canceling a pick queued an unnecessary preview rebuild'
assert fx.document.dumps(edit.export()) == before, 'navigation rewrote native effect data'
addon.unregister()
assert not bpy.app.timers.is_registered(fx.clear_mesh_pick_slots), 'disabled add-on retained the native picker reset callback'
print('PASS nested Mesh navigation, native eyedropper visible with binding retained, pick/re-pick/cancel/reset, transient UI, source unchanged')
