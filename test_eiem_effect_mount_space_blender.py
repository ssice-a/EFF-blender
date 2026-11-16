"""Native static Renderer mounts must match the Mesh editing frame."""
import importlib
import importlib.util
import json
import math
from pathlib import Path
import sys
import tempfile

import bpy
from mathutils import Matrix, Quaternion, Vector

folder = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('effect_mount_space_test', folder/'__init__.py',
                                             submodule_search_locations=[str(folder)])
addon = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = addon
spec.loader.exec_module(addon)
addon.register()
editor = addon.eiem_blender_addon.effect_editor
mounts = importlib.import_module(spec.name+'.eiem_effect_mounts')
basis = editor.BASIS
mesh_basis = Matrix.Diagonal(Vector((-1, 1, 1, 1)))

def trs(node):
    p, q, s = node['position'], node['rotation'], node['scale']
    return Matrix.LocRotScale(Vector(p), Quaternion((q[3], *q[:3])), Vector(s))

with tempfile.TemporaryDirectory(prefix='eff-mount-space-') as temporary:
    package = Path(temporary)
    (package/'source').mkdir()
    owner = bpy.data.objects.new('Sword', bpy.data.meshes.new('Sword'))
    bpy.context.scene.collection.objects.link(owner)
    owner['eiem_author_package'] = str(package)
    owner['eiem_source_owner_path'] = 'Meshes/lod0/Sword_lod0'
    owner.data['eiem_source'] = 'assets/weapons/sword.fbx'
    owner.data['eiem_asset'] = 'Sword_lod0'
    owner.data['eiem_coordinate_space'] = 'unity-y-up-left-handed'
    resources = [dict(type='Mesh', identity='M'+str(i), name='Sword_lod'+str(i),
                      logicalPath=owner.data['eiem_source']) for i in range(4)]
    uses = [dict(ownerPath='Meshes/lod'+str(i)+'/Sword_lod'+str(i),
                 mesh=dict(identity='M'+str(i)), bones=[]) for i in range(4)]
    source = dict(position=[.1,.2,-.3], rotation=[-math.sqrt(.5),0,0,math.sqrt(.5)], scale=[1,2,3])
    hierarchy = [dict(path='Meshes/lod0', transform='L', localPosition=[0,0,0],
                      localRotation=[0,0,0,1], localScale=[1,1,1]),
                 dict(path=uses[0]['ownerPath'], transform='R', localPosition=source['position'],
                      localRotation=source['rotation'], localScale=source['scale'])]
    (package/'source/manifest.json').write_text(json.dumps(dict(prefab='assets/weapons/sword.prefab',
        resources=resources, rendererUses=uses, hierarchy=hierarchy)), encoding='utf-8')
    root = bpy.data.objects.new('Spark', None)
    bpy.context.scene.collection.objects.link(root)
    root['eff_effect_id'] = 'spark'
    root.eff_effect.owner = owner
    root.location = (.03,-.02,.1)
    root.rotation_euler = (.1,.2,.3)
    mount = bpy.data.objects.new('Pivot', None)
    bpy.context.scene.collection.objects.link(mount)
    mount.parent = owner
    mount.location = (0,0,.366)
    mount.rotation_euler = (.2,-.1,.3)
    node = bpy.data.objects.new('Emitter', None)
    bpy.context.scene.collection.objects.link(node)
    node.parent = root
    node.location = (0,0,.25)
    bpy.context.view_layer.update()
    for all_lods in (True,False):
        root.eff_effect.all_lods = all_lods
        for has_mount in (False,True):
            root.eff_effect.mount = mount if has_mount else None
            row = editor.attachment(root)
            assert row.get('lodAnchor'), 'static Renderer mount lacks the Mesh-to-Transform basis'
            actual = Matrix.Identity(4)
            for value in row['lodAnchor']['nodes']:
                actual = actual @ trs(value)
            if has_mount:
                graph, path = mounts.graphs([root], None, basis)[mount]
                for value in graph['nodes']:
                    actual = actual @ trs(value)
            actual = actual @ trs(row) @ basis.inverted() @ root.matrix_world.inverted() @ node.matrix_world @ basis
            # The preview and Mesh geometry share Blender model space. The
            # native Renderer applies its original TRS to raw Mesh coordinates.
            expected = trs(source) @ mesh_basis.inverted() @ editor.preview.emitter_matrix(root,node) @ basis
            for point in ((0,0,0),(1,0,0),(0,1,0),(0,0,1)):
                error = (actual @ Vector(point) - expected @ Vector(point)).length
                assert error < 1e-5, ('static effect axis mismatch',all_lods,has_mount,point,error)
    assert addon.eiem_blender_addon.unity_to_blender((1,2,3)) == (-1,2,3)
addon.unregister()
print('PASS: Mesh/Transform basis; static source/all LOD; Empty mount; rotated nonuniform source; position and all axes')
