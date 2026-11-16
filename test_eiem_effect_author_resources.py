"""Integration check against a real exported effect edit, without Blender/index.

python test_eiem_effect_author_resources.py <edits.json> [compiler.exe output]
"""
import copy
import json
from pathlib import Path
import sys

import eiem_effect_author_resources as assembly
import eiem_effect_document as document
import eiem_effect_publish as publish
from nativepack.effect_native import read_native_build


payload = json.loads(Path(sys.argv[1]).read_bytes())
state = document.load_source(payload['source'])
document.apply_edit(state, payload)
edit = document.Editor(state)
before = copy.deepcopy(payload)
compiled, resources = assembly.externalize(edit, payload)
assert payload == before, 'publication rewrote the offline author snapshot'
assert 'assets' not in compiled
assert resources['materials'] and resources['textures']
assert compiled['authorReferences'], 'authored component bindings were lost'
assert all(r['identity'] not in {a['identity'] for a in payload.get('assets', [])}
           for r in compiled['objects'])
assert all(r['owner'] not in {a['identity'] for a in payload.get('assets', [])}
           for r in compiled.get('gameReferences', []))
declared = {r['id'] for kind in ('textures', 'meshes') for r in resources[kind]}
declared.update(resources['materials'])
assert all(r['resource'] in declared for r in compiled['authorReferences'])
assert all(binding['texture'] in declared for material in resources['materials'].values()
           for binding in material['textureEdits'])
for material in resources['materials'].values():
    assert material['sourcePath'].startswith('assets/')
    assert material['authorData'].startswith(b'format=EFFMAT\n')
    assert not material['fieldData'], 'Material retained a private native payload'

runtime = dict(authorResources=[dict(resource=r['resource'], type=r['type'])
                              for r in compiled['authorReferences']])
rows = [dict(_native=copy.deepcopy(runtime), _resources=copy.deepcopy(resources)) for _ in range(2)]
shared = assembly.merge_resource_sets(rows)
assert len(shared['materials']) == len(resources['materials'])
assert len(shared['meshes']) == len(resources['meshes'])
assert len(shared['textures']) == len(resources['textures'])
assert rows[0]['native'] == rows[1]['native'], 'identical root resources were duplicated'
collision = [dict(_native=copy.deepcopy(runtime), _resources=copy.deepcopy(resources)) for _ in range(2)]
changed = next(iter(collision[1]['_resources']['materials'].values()))
changed['authorData'] += b'float._Brightness=2\n'
combined = assembly.merge_resource_sets(collision)
assert len(combined['materials']) == len(resources['materials']) + 1
assert len({r['id'] for r in combined['materials'].values()}) == len(combined['materials'])
assert collision[0]['native'] != collision[1]['native'], 'colliding authored names were not rebound'

if len(sys.argv) > 3:
    build = publish.Build(payload, sys.argv[2], Path(sys.argv[3]), 'ordinary-resources-regression',
                          read_native_build, compiled_payload=compiled, resources=resources)
    output = build.run()
    native = read_native_build(output)
    assert len(native['authorResources']) == len({(r['type'], r['resource']) for r in compiled['authorReferences']})
    assert not any(r['type'] in ('Texture', 'Material', 'Mesh') for r in native['gameResources']), \
        'authored dependencies remained private effect game resources'
    built = assembly.built_resources(resources, output)
    assert all(row['native']['data'] for row in built['meshes'])
    for row in built['meshes']:
        assert row['native']['path'].startswith('assets/eff/meshes/')
    print('PASS real compiler, component relocation bindings, independent Mesh native archive')
print('PASS ordinary effect resources, portable snapshot unchanged, shared resources and name collisions')
