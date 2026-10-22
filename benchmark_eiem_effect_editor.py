"""Isolated, real-source effect UI/preview benchmark. Never opens a user scene."""
import cProfile
import importlib
import io
import json
import pstats
import statistics
import sys
import time
from pathlib import Path
import bpy

addon_path, source_root, destination = sys.argv[sys.argv.index('--') + 1:]
sys.path.insert(0, str(Path(addon_path).resolve().parent))
addon = importlib.import_module(Path(addon_path).name)
addon.register()
editor = addon.eiem_blender_addon.effect_editor
scene = bpy.context.scene
scene.render.fps = 30
roots = []
results = {}
for path in sorted(Path(source_root).rglob('effect.json')):
    start = time.perf_counter()
    roots.append(editor.import_source(bpy.context, path.parent))
    results[path.parent.name] = {'import_ms': (time.perf_counter()-start)*1000}
scene.frame_set(151)

class Layout:
    def __init__(self):
        self.labels = []
        self.props = 0
    def __getattr__(self, name):
        if name in ('row', 'column', 'box', 'split', 'grid_flow'):
            return lambda *a, **k: self
        return lambda *a, **k: None
    def panel(self, *a, **k):
        return self, None if k.get('default_closed') else self
    def prop(self, *a, **k):
        self.props += 1
    def label(self, **k):
        self.labels.append(k.get('text', ''))
    def operator(self, *a, **k):
        return type('Op', (), {})()

def measure(fn, count=7):
    times = []
    for _ in range(count):
        start = time.perf_counter()
        fn()
        times.append((time.perf_counter()-start)*1000)
    return {'median_ms': round(statistics.median(times), 3), 'max_ms': round(max(times), 3)}

profile = cProfile.Profile()
profile.enable()
for root in roots:
    edit = editor.editor(root)
    node = next(o for o in editor.objects(root) if any(edit.objects[i]['classId']==198 for i in json.loads(o.get('eff_effect_components', '[]'))))
    identity = next(i for i in json.loads(node['eff_effect_components']) if edit.objects[i]['classId']==198)
    bpy.context.view_layer.objects.active = root
    layout = Layout()
    draw = lambda: editor.EFF_PT_effect_object.draw(type('Panel', (), {'layout': layout})(), bpy.context)
    stats = results[Path(edit.state['source']).name]
    stats['draw_root'] = measure(draw)
    stats['draw_props'] = layout.props//7
    stats['sync'] = measure(lambda: editor.synchronize(root))
    stats['preview'] = measure(lambda: editor.preview.refresh(root, editor.editor(root)))
    key = editor.field_key(identity, ['InitialModule','startSpeed','scalar'])
    original = node[key]
    def drag():
        node[key] = float(node[key]) + .001
        editor.preview.live_update()
    stats['drag_timer'] = measure(drag)
    node[key] = original
    editor.synchronize(root)
    stats['particles'] = sum(o.get('预览粒子数', 0) for o in root.children_recursive)
results['idle_timer'] = measure(editor.preview.live_update)
profile.disable()
stream = io.StringIO()
pstats.Stats(profile, stream=stream).sort_stats('cumtime').print_stats(25)
output = Path(destination)
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding='utf-8')
output.with_suffix('.profile.txt').write_text(stream.getvalue(), encoding='utf-8')
print(json.dumps(results, ensure_ascii=False))
addon.unregister()
