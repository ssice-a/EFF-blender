"""Actual tangtang loop: Trail geometry/material, reverse seek and raw-source isolation."""
import hashlib
import importlib
import json
from pathlib import Path
import sys
import bpy

addon_path,source,output=sys.argv[sys.argv.index('--')+1:]
sys.path.insert(0,str(Path(addon_path).resolve().parent))
addon=importlib.import_module(Path(addon_path).name);addon.register()
editor=addon.eiem_blender_addon.effect_editor
root=editor.import_source(bpy.context,source,preserve_source=True)
edit=editor.synchronize(root)
baseline=editor.assets.export_document(root,edit)
assert not baseline['objects'] and not baseline.get('assets')
# Isolate this emitter for the real geometry checks, keeping other source
# components untouched. Hiding is a preview option, never an author edit.
scene=bpy.context.scene;scene.render.fps=30;scene.frame_start=1
values=[]
for frame in (16,31,46,31):
 scene.frame_set(frame);editor.preview.refresh(root,editor.synchronize(root))
 assert not root.get('预览错误'),root.get('预览错误')
 objs=[o for o in root.children_recursive if o.get('eff_preview_trail') and not o.hide_viewport]
 assert len(objs)==1
 obj=objs[0]
 assert obj.data.materials and obj.data.uv_layers.active
 co=[0.]*(len(obj.data.vertices)*3);obj.data.vertices.foreach_get('co',co)
 values.append(dict(frame=frame,vertices=len(obj.data.vertices),faces=len(obj.data.polygons),
                    digest=hashlib.sha256(repr(co).encode()).hexdigest(),material=obj.data.materials[0].name))
 assert editor.assets.export_document(root,editor.synchronize(root))==baseline,'trail preview wrote native changes'
assert any(v['faces'] for v in values),'original enabled TrailModule never produced geometry'
assert values[1]==values[3],'reverse seek retained stale trail history'
out=Path(output);out.mkdir(parents=True,exist_ok=True)
(out/'verification.json').write_text(json.dumps(values,ensure_ascii=False,indent=2),encoding='utf-8')
bpy.ops.wm.save_as_mainfile(filepath=str(out/'trail-playback.blend'))
print('PASS actual TrailModule paths, original slot-1 material, geometry, reverse seek and no native edits',flush=True)
addon.unregister()
