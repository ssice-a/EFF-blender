"""Run in a disposable Blender with the actual flower author and mount snapshots."""
import copy
import importlib
import json
import math
from pathlib import Path
import sys
import time
import bpy
from mathutils import Matrix, Quaternion, Vector

addon_path, snapshot = sys.argv[sys.argv.index('--')+1:]
snapshot=Path(snapshot)
sys.path.insert(0,str(Path(addon_path).resolve().parent))
addon=importlib.import_module(Path(addon_path).name);addon.register()
mod=addon.eiem_blender_addon;editor=mod.effect_editor;preview=editor.preview
state=json.loads((snapshot/'live-state.json').read_text('utf-8'))
mount=json.loads((snapshot/'live-mount.json').read_text('utf-8'))
root=editor.import_source(bpy.context,state['source'])
edit=editor.editor(root)
assert list(edit.emission_controllers()),'fixture needs the real automatic emission component'
controllers={i for i,_ in edit.emission_controllers()}
for cid,value in edit.emission_controllers():
    assert value=={**edit.state['original'][cid],'m_Enabled':0},'import modified unrelated controller fields'
for identity,value in edit.state['values'].items():
    if identity not in controllers:assert value==edit.state['original'][identity],'import changed unrelated source values'
particle_id=next(i for i in state['values'] if i.endswith('-2912475041744378721'))
edit.state['values'][particle_id]=copy.deepcopy(state['values'][particle_id])
editor.store(root,edit);editor.refresh_fields(root)
node=next(o for o in editor.objects(root) if particle_id in json.loads(o.get('eff_effect_components','[]')))
for cid,_ in edit.emission_controllers(json.loads(node['eff_effect_components'])):
    switch=node.eff_effect_choices[editor.field_key(cid,['m_Enabled'])]
    assert switch.value=='0'
    switch.value='1';assert editor.synchronize(root).value(cid)['m_Enabled']==1
    switch.value='0';assert editor.synchronize(root).value(cid)['m_Enabled']==0
rig=mod.make_armature('Parity rig',mount,bpy.context.collection)
owner=bpy.data.objects.new('Parity owner',bpy.data.meshes.new('Parity owner'))
bpy.context.collection.objects.link(owner);owner.modifiers.new('Rig','ARMATURE').object=rig
root.eff_effect.owner=owner;root.eff_effect.bone=mount['bone']
bpy.context.view_layer.update()

native=Matrix.Identity(4)
for path,parent,p,q,s in mount['nodes']:
    native=native@Matrix.LocRotScale(Vector(p),Quaternion((q[3],q[0],q[1],q[2])),Vector(s))
basis=editor.BASIS
expected=rig.matrix_world@basis@native@basis.inverted()@root.matrix_local
actual=preview.mount_matrix(root)
assert max(abs(actual[i][j]-expected[i][j]) for i in range(4) for j in range(4))<1e-5
assert (actual.translation-root.matrix_world.translation).length>.5,'fixture must detect omitted mount'
# Applying/removing a mesh modifier must not erase the preserved source link.
owner['eiem_import_id']=rig['eiem_import_id']='parity-import'
owner['eiem_skeleton']=rig['eiem_section']='parity-skeleton'
owner.modifiers.remove(owner.modifiers[0])
assert preview.mount_rig(owner)==rig
assert max(abs(preview.mount_matrix(root)[i][j]-expected[i][j]) for i in range(4) for j in range(4))<1e-5
other=bpy.data.objects.new('Unrelated rig',rig.data.copy());bpy.context.collection.objects.link(other)
other['eiem_import_id']='other-import';other['eiem_section']='parity-skeleton'
assert preview.mount_rig(owner)==rig,'mount selected another import with the same section'
other['eiem_import_id']='parity-import'
assert preview.mount_rig(owner) is None,'ambiguous preserved rig link was guessed'
bpy.data.objects.remove(other,do_unlink=True)
root_before=root.matrix_basis.copy();state_before=editor.document.dumps(edit.state)
data=edit.value(particle_id)
effective=preview.effective_particle_data(edit,node,data,preview.emitter_matrix(root,node))
assert effective['EmissionModule']['rateOverTime']==data['EmissionModule']['rateOverTime']
assert not list(preview.automatic_emission(edit,node))
# Advanced opt-in retains the native rule and remains editable.
for _,controller in edit.emission_controllers(json.loads(node['eff_effect_components'])):controller['m_Enabled']=1
editor.store(root,edit);editor.refresh_fields(root)
state_before=editor.document.dumps(edit.state)
effective=preview.effective_particle_data(edit,node,data,preview.emitter_matrix(root,node))
assert abs(effective['EmissionModule']['rateOverTime']['scalar']-4)<1e-5
assert data['EmissionModule']['rateOverTime']['scalar']==18
assert effective['InitialModule']['startLifetime']==data['InitialModule']['startLifetime']
scene=bpy.context.scene;scene.render.fps=30;scene.frame_set(85)
start=time.perf_counter()
for _ in range(10):preview.refresh(root,edit)
elapsed=(time.perf_counter()-start)*100
assert root.matrix_basis==root_before
assert editor.document.dumps(edit.state)==state_before,'preview rewrote author parameters'
bounds=next(o for o in root.children_recursive if o.get('eff_preview_key')==root['eff_effect_id']+particle_id+'shape')
bpy.context.view_layer.update()
placement=preview.emitter_matrix(root,node)
assert max(abs(bounds.matrix_world[i][j]-placement[i][j]) for i in range(4) for j in range(4))<1e-4
# The shape wireframe must show the same half arc as actual particle births.
half_arc=copy.deepcopy(effective)
half_arc['ShapeModule']=dict(enabled=True,type=8,radius=dict(value=1),arc=dict(value=180),length=1,angle=25)
preview._shape(root,node,particle_id,half_arc)
assert min((preview.BASIS.inverted()@v.co).y for v in bounds.data.vertices)>=-1e-6
assert all(max(edge.vertices)<len(bounds.data.vertices) for edge in bounds.data.edges)
preview._shape(root,node,particle_id,effective)
signature=preview.author_signature(root)
rig.pose.bones[mount['bone']].rotation_mode='XYZ';rig.pose.bones[mount['bone']].rotation_euler.x=.4
bpy.context.view_layer.update()
assert preview.author_signature(root)!=signature,'pose changes did not invalidate preview'

# Only the explicit user operator disables the source controller.
bpy.context.view_layer.objects.active=node
assert bpy.ops.eiem.effect_use_particle_emission(object_name=node.name)=={'FINISHED'}
edit=editor.editor(root)
assert not list(preview.automatic_emission(edit,node))
assert edit.value(particle_id)==data,'emission control changed other particle fields'
for _,controller in edit.emission_controllers(json.loads(node['eff_effect_components'])):controller['m_Enabled']=1
editor.store(root,edit);editor.refresh_fields(root)
assert bpy.ops.eiem.effect_continuous(object_name=node.name,identity=particle_id,rate=12,capacity=40)=={'FINISHED'}
edit=editor.editor(root)
assert not list(preview.automatic_emission(edit,node)),'continuous preset left a hidden quantity override'
assert edit.value(particle_id)['EmissionModule']['rateOverTime']['scalar']==12
print('PASS actual flower -> native-scale bone mount, automatic rate, pose invalidation, bounds and author preservation')
print('PREVIEW_MS',round(elapsed,3))
addon.unregister()
