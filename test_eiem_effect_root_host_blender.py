"""A selected Empty becomes the actual authoring root without losing its transform."""
import importlib
import sys
from pathlib import Path

import bpy
from mathutils import Matrix

addon_path=sys.argv[sys.argv.index('--')+1]
sys.path.insert(0,str(Path(addon_path).resolve().parent))
addon=importlib.import_module(Path(addon_path).name);addon.register()
editor=addon.eiem_blender_addon.effect_editor

host=bpy.data.objects.new('武器特效根',None)
bpy.context.scene.collection.objects.link(host)
host.empty_display_type='PLAIN_AXES'
host.matrix_basis=Matrix.Translation((1.25,-.5,2.0))
expected=host.matrix_basis.copy()
bpy.context.view_layer.objects.active=host;host.select_set(True)
assert editor.EFF_PT_effect_empty_create.poll(bpy.context)

root_id='author-cab:1';transform_id='author-cab:2'
state=dict(format='EFF_EFFECT_WORKSPACE',version=1,
    catalog=dict(prefab='assets/test/weapon_effect.prefab',root=root_id,objects=[],hierarchy=[dict(identity=root_id,name='武器特效根',
        transform=transform_id,parent=None,children=[],components=[])]),
    values={transform_id:dict(m_LocalPosition=dict(x=0.,y=0.,z=0.),
        m_LocalRotation=dict(x=0.,y=0.,z=0.,w=1.),m_LocalScale=dict(x=1.,y=1.,z=1.))})
old_expand=editor.assets.expand_meshes;old_refresh=editor.preview.refresh;old_prime=editor.preview.prime_signature
editor.assets.expand_meshes=lambda *args,**kwargs:None
editor.preview.refresh=lambda *args,**kwargs:None
editor.preview.prime_signature=lambda *args,**kwargs:None
try:created=editor.import_workspace(bpy.context,state,effect_root=host)
finally:
    editor.assets.expand_meshes=old_expand;editor.preview.refresh=old_refresh;editor.preview.prime_signature=old_prime

assert created is host
assert created.get('eff_effect_document') and created.get('eff_effect_node')==root_id
assert all(abs(created.matrix_basis[i][j]-expected[i][j])<1e-6 for i in range(4) for j in range(4))
assert editor.EFF_PT_effect_object.poll(bpy.context)
print('PASS selected Empty is reused as the effect root; its transform is preserved')
addon.unregister()
