"""Check real native schema roundtrips and build friendly-widget edits."""
import importlib
import json
import subprocess
import sys
from pathlib import Path
import bpy

addon_path, source_root, output=sys.argv[sys.argv.index('--')+1:]
sys.path.insert(0,str(Path(addon_path).resolve().parent))
addon=importlib.import_module(Path(addon_path).name);addon.register()
editor=addon.eiem_blender_addon.effect_editor
codec=importlib.import_module(Path(addon_path).name+'.nativepack.type_tree')
output=Path(output);output.mkdir(parents=True,exist_ok=True)
paths=sorted(Path(source_root).rglob('effect.json'));count=0
for source in paths:
    state=editor.document.load_source(source.parent)
    manifest=json.loads((source.parent/'source/manifest.json').read_text('utf-8-sig'))
    for spec in state['catalog']['objects']:
        if spec['classId'] not in (198,199) or spec['state']!='parsed':continue
        schema=state['schemas'][spec['schema']]
        tree=codec.Schema.compile(schema['nodes'])
        serialized=next(f for f in manifest['serializedFiles'] if f['cab'].casefold()==spec['native']['cab'].casefold())
        contents=(source.parent/serialized['snapshot']['file']).read_bytes()
        start=spec['native']['offset'];raw=contents[start:start+spec['native']['size']]
        assert codec.encode(codec.decode(tree,raw))==raw
        count+=1
print('PASS unchanged native roundtrip',count,flush=True)
source=next(p for p in paths if 'pink' in p.parent.name)
root=editor.import_source(bpy.context,source.parent);edit=editor.editor(root)
node=next(o for o in editor.objects(root) if any(edit.objects[i]['classId']==198 for i in json.loads(o.get('eff_effect_components','[]'))))
identity=next(i for i in json.loads(node['eff_effect_components']) if edit.objects[i]['classId']==198)
node.eff_effect_choices[editor.field_key(identity,['InitialModule','startLifetime','minMaxState'])].value='3'
node[editor.field_key(identity,['InitialModule','startLifetime','minScalar'])]=2.5
node[editor.field_key(identity,['InitialModule','startLifetime','scalar'])]=4.5
color_path=['InitialModule','startColor','maxColor']
node['fxv_'+editor.field_key(identity,color_path)[3:]]=[.8,.5,.7,.6]
time_path=['ColorModule','gradient','maxGradient','atime1']
if 'fxu_'+editor.field_key(identity,time_path)[3:] in node:
    node['fxu_'+editor.field_key(identity,time_path)[3:]]=.75
edit=editor.synchronize(root)
assert edit.value(identity,['InitialModule','startLifetime','minScalar'])==2.5
assert edit.value(identity,time_path)==49151
edits=output/'friendly-widget.effedit.json';editor.document.write_edits(edit.state,edits)
compiler=Path(addon_path).resolve().parents[1]/'tools/AnimeStudio/AnimeStudio.GUI/bin/Debug/net9.0-windows/AnimeStudio.GUI.exe'
result=subprocess.run([str(compiler),'--build-eff-effects',str(source.parent),str(edits),str(output/'build')],capture_output=True,text=True,encoding='utf-8',errors='replace')
(output/'build.log').write_text(result.stdout+result.stderr,encoding='utf-8')
assert result.returncode==0,(result.returncode,result.stdout,result.stderr)
assert (output/'build'/'build.json').is_file() and (output/'build'/'runtime.bin').is_file()
print('PASS friendly lifetime/color/normalized gradient widgets -> native compiler build',flush=True)
addon.unregister()
