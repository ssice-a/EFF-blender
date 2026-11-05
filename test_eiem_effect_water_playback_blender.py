"""Real tangtang water source: static passes, floating VAT and clip seek isolation."""
import hashlib
import importlib
import json
from pathlib import Path
import sys
import bpy

addon_path, source, output = sys.argv[sys.argv.index('--') + 1:]
sys.path.insert(0, str(Path(addon_path).resolve().parent))
addon = importlib.import_module(Path(addon_path).name); addon.register()
editor = addon.eiem_blender_addon.effect_editor
root = editor.import_source(bpy.context, source, preserve_source=True)
root.eff_effect.preview_particles = False
edit = editor.synchronize(root)
baseline = editor.assets.export_document(root, edit)
assert not baseline['objects'] and not baseline.get('assets'), 'research import changed native source'
assert root.eff_effect.source_animation and root.eff_effect.preview_clip != 'NONE'
scene = bpy.context.scene; scene.render.fps = 60; scene.frame_start = 1; scene.frame_end = 122
rows = []
def sample(frame):
    scene.frame_set(frame); editor.preview.refresh(root, editor.synchronize(root))
    assert not root.get('预览错误'), root.get('预览错误')
    objs = [o for o in root.children_recursive if o.get('eff_preview_static')]
    assert len(objs) == 3, 'source extra material passes were lost'
    assert all(len(o.data.vertices) == 5040 and len(o.data.polygons) == 8272 for o in objs)
    assert all(o.data.uv_layers.get('UV1') for o in objs), 'VAT lookup UV1 was lost'
    assert all(o.get('eff_preview_vat') for o in objs), [o.get('VAT 提示') for o in objs]
    row = dict(frame=frame, vatFrames=[o['VAT 帧'] for o in objs])
    values=[]
    for o in objs:
        co=[0.]*(len(o.data.vertices)*3);o.data.vertices.foreach_get('co',co)
        values.append(hashlib.sha256(repr(co).encode()).hexdigest())
    row['digest']=values;rows.append(row)
    assert editor.assets.export_document(root, editor.synchronize(root)) == baseline, 'preview polluted author state'
    return values
print('IMPORT PASS', flush=True)
a=sample(61);b=sample(101);c=sample(61)
assert a==c and a!=b, 'VAT did not animate deterministically on reverse seek'
assert rows[0]['vatFrames'][0] < rows[1]['vatFrames'][0]
path=Path(output);path.mkdir(parents=True,exist_ok=True)
name=root.name
bpy.ops.wm.save_as_mainfile(filepath=str(path/'water-playback.blend'))
bpy.ops.wm.open_mainfile(filepath=str(path/'water-playback.blend'))
root=bpy.data.objects[name]
scene=bpy.context.scene
assert sample(61)==a, 'save/reopen changed sampled geometry'
(path/'verification.json').write_text(json.dumps(rows,indent=2),encoding='utf-8')
print('PASS native static passes, UV1, RGBAHalf VAT, source animation, reverse seek, no author changes and save/reopen',flush=True)
addon.unregister()
