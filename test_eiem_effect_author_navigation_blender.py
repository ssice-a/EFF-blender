"""Real weapon scene: preview switches, burst timing, and mount navigation."""
import importlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import bpy

addon_path, project, output = map(Path, sys.argv[sys.argv.index('--') + 1:])
sys.path.insert(0, str(addon_path.resolve().parent))
addon = importlib.import_module(addon_path.name)
addon.register()
bpy.ops.wm.open_mainfile(filepath=str(project))
ed = addon.eiem_blender_addon.effect_editor
scene = bpy.context.scene
roots = [o for o in scene.objects if o.get('eff_effect_document')]
baselines = {r.name: ed.synchronize(r).export() for r in roots}
for root in roots:
    root.eff_effect.preview = False
scene.frame_set(44)
root = bpy.data.objects['基础特效_闪电扩散']
ed.preview._pending_updates.clear()
root.eff_effect.preview = True
assert root.as_pointer() in ed.preview._pending_updates, 'preview checkbox did not queue a refresh'
for key, (_, nodes) in list(ed.preview._pending_updates.items()):
    ed.preview._pending_updates[key] = (0, nodes)
ed.preview.live_update()
objects = [o for o in root.children_recursive if o.get('eff_preview_geometry_nodes')]
assert objects and sum(o.get('预览粒子数', 0) for o in objects) == 0, 'fixture should be in a burst gap'

def select(obj):
    for other in bpy.context.selected_objects: other.select_set(False)
    obj.hide_set(False); obj.select_set(True); bpy.context.view_layer.objects.active = obj

class Layout:
    def __init__(self, records=None): self.records = records if records is not None else []
    def row(self, **kw): return Layout(self.records)
    def box(self): return Layout(self.records)
    def column(self, **kw): return Layout(self.records)
    def panel(self, *args, **kw): return Layout(self.records), None
    def separator(self, **kw): pass
    def label(self, **kw):
        assert kw.get('icon') != 'ERROR', kw
        self.records.append(('label', kw.get('text', '')))
    def prop(self, obj, path, **kw): self.records.append(('prop', path))
    def operator(self, name, **kw):
        op = SimpleNamespace(idname=name)
        self.records.append(('operator', op))
        return op

def draw(panel):
    layout = Layout()
    panel.draw(SimpleNamespace(layout=layout), bpy.context)
    return layout.records

select(root)
records = draw(ed.EFF_PT_effect_object)
assert any(kind == 'label' and '存活粒子 0' in value for kind, value in records)
assert bpy.ops.eiem.effect_preview_seek() == {'FINISHED'}
assert scene.frame_current != 44
assert sum(o.get('预览粒子数', 0) for o in objects) > 0
assert bpy.ops.eiem.effect_mount_select(root_name=root.name, direction='MOUNT') == {'FINISHED'}
assert bpy.context.object == root.eff_effect.mount
assert ed.EFF_PT_effect_mount.poll(bpy.context)
assert not ed.EFF_PT_effect_object.poll(bpy.context), 'mount was presented as an emitter'
records = draw(ed.EFF_PT_effect_mount)
assert any(kind == 'label' and '当前编辑：挂点' in value for kind, value in records)
assert any(kind == 'operator' and value.idname == 'eiem.effect_mount_select' for kind, value in records)
assert bpy.ops.eiem.effect_mount_select(root_name=root.name, direction='EFFECT') == {'FINISHED'}
assert bpy.context.object == root

results = []
for name in ('基础特效_星环组', '基础特效_闪电扩散', '基础特效_周期折射冲击波'):
    root = bpy.data.objects[name]; select(root)
    root.eff_effect.preview = True
    assert bpy.ops.eiem.effect_preview_seek() == {'FINISHED'}
    bpy.context.view_layer.update()
    graph = bpy.context.evaluated_depsgraph_get()
    instances = [len(item.object.data.polygons) for item in graph.object_instances if item.is_instance and item.parent
                 and ed.root_of(item.parent.original) == root]
    assert instances and all(instances), name
    results.append(dict(root=name, frame=scene.frame_current, instances=len(instances)))
    root.eff_effect.preview = False
    for key, (_, nodes) in list(ed.preview._pending_updates.items()):
        ed.preview._pending_updates[key] = (0, nodes)
    ed.preview.live_update()
    assert all(o.hide_viewport for o in root.children_recursive if o.get('eff_preview_geometry_nodes'))
assert {r.name: ed.synchronize(r).export() for r in roots} == baselines, 'preview/navigation changed native fields'
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(dict(passed=True, checkboxQueuesRefresh=True,
    mountsSeparated=True, burstSeek=True, nativeUnchanged=True, previews=results),
    ensure_ascii=False, indent=2), 'utf-8')
addon.unregister()
print('PASS preview toggles, burst seek, real instances and separate mount/effect navigation')
