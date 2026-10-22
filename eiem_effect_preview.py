"""Offline object preview using original mesh/texture references and timeline."""
import json
import hashlib
import math
import copy
from pathlib import Path
import bpy
from mathutils import Matrix,Quaternion,Vector
if __package__:
    from . import eiem_effect_simulation as sim
    from . import eiem_effect_assets as assets
else:
    import eiem_effect_simulation as sim
    import eiem_effect_assets as assets
_sync=None
_root_of=None
_busy=False
_history={}
_templates={}
_signatures={}
_shapes={}
_node_signatures={}
_asset_revisions={}
POLL_INTERVAL=.1
BASIS=Matrix(((-1,0,0),(0,0,-1),(0,1,0)))

def _collection(root):
    name=root.get('eff_effect_collection')
    return bpy.data.collections.get(name) or root.users_collection[0]
def _resources(root,edit):
    return {r['identity']:r for r in edit.state['catalog'].get('preview',{}).get('resources',[]) if r.get('file')}
def _target(edit,identity,path):
    ptr=edit.value(identity,path)
    refs=edit.objects[identity].get('references',[])
    field='$.'+'.'.join(map(str,path))
    # Resolve edited PPtr as well as unchanged original references.
    row=next((r for r in refs if r['fileId']==ptr.get('m_FileID') and r['pathId']==ptr.get('m_PathID')),None)
    if row:return row.get('identity')
    own=edit.objects[identity]['native']['cab'].lower()
    cab=own if ptr.get('m_FileID')==0 else next(((r.get('cab') or '').lower() for o in edit.objects.values()
        if o['native']['cab'].lower()==own for r in o['references'] if r['fileId']==ptr.get('m_FileID') and r.get('cab')),None)
    return (cab+':'+str(ptr.get('m_PathID'))) if cab and ptr.get('m_PathID') else None

def _texture_coordinates(nodes,links,texture,settings):
    uv=nodes.new('ShaderNodeTexCoord');mapping=nodes.new('ShaderNodeVectorMath');mapping.operation='MULTIPLY_ADD'
    scale=settings.get('m_Scale',{});offset=settings.get('m_Offset',{})
    links.new(uv.outputs['UV'],mapping.inputs[0]);mapping.inputs[1].default_value=(scale.get('x',1),scale.get('y',1),1);mapping.inputs[2].default_value=(offset.get('x',0),offset.get('y',0),0)
    links.new(mapping.outputs['Vector'],texture.inputs['Vector'])
def _material(root,edit,renderer,resources):
    mid=assets.renderer_material(root,edit,renderer) if edit.value(renderer).get('m_Materials') else None
    return _material_asset(root,edit,mid,resources)

def _material_asset(root,edit,mid,resources):
    from . import eiem_effect_material as material_bridge
    matname='EFF preview '+root['eff_effect_id']+' '+str(mid)
    author=assets.material_object(root,mid)
    mat=next((m for m in bpy.data.materials if author and m.eff_effect_author==author),None) or bpy.data.materials.get(matname)
    if mat and author:mat.eff_effect_author=author
    if mat and mat.name.startswith('EFF preview ') and edit.objects.get(mid,{}).get('name'):mat.name=edit.objects[mid]['name']
    if not mat:mat=bpy.data.materials.new(edit.objects.get(mid,{}).get('name') or matname)
    if author:mat.eff_effect_author=author
    if author:material_bridge.setup(mat,root,edit,mid)
    signature=hashlib.sha256((json.dumps(edit.state['values'].get(mid,{}),sort_keys=True)+str(assets.texture_signature(root,mid))+str(material_bridge.signature(author))).encode()).hexdigest()
    if mat.get('eff_source_signature')==signature:return mat
    mat['eff_source_signature']=signature;mat.use_nodes=True
    nodes=mat.node_tree.nodes;nodes.clear();links=mat.node_tree.links
    output=nodes.new('ShaderNodeOutputMaterial');shader=nodes.new('ShaderNodeBsdfPrincipled')
    shader.inputs['Roughness'].default_value=.7;shader.inputs['Emission Strength'].default_value=1
    links.new(shader.outputs['BSDF'],output.inputs['Surface'])
    attr=nodes.new('ShaderNodeVertexColor');attr.layer_name='EFFColor'
    links.new(attr.outputs['Color'],shader.inputs['Base Color']);links.new(attr.outputs['Color'],shader.inputs['Emission Color']);links.new(attr.outputs['Alpha'],shader.inputs['Alpha'])
    if mid in edit.state['values']:
        fields=edit.value(mid).get('m_SavedProperties',{});textures=fields.get('m_TexEnvs',[])
        floats={r['first']:r['second'] for r in fields.get('m_Floats',[])};tints={r['first']:r['second'] for r in fields.get('m_Colors',[])}
        tint=tints.get('_TintColor',tints.get('_BaseColor',tints.get('_Color',dict(r=1,g=1,b=1,a=1))))
        rgb=nodes.new('ShaderNodeMixRGB');rgb.blend_type='MULTIPLY';rgb.inputs[0].default_value=1
        links.new(attr.outputs['Color'],rgb.inputs[1]);rgb.inputs[2].default_value=tuple(tint.get(k,1) for k in ('r','g','b','a'))
        color_socket=rgb.outputs[0];alpha_socket=attr.outputs['Alpha']
        alpha_factor=nodes.new('ShaderNodeMath');alpha_factor.operation='MULTIPLY';links.new(alpha_socket,alpha_factor.inputs[0]);alpha_factor.inputs[1].default_value=tint.get('a',1)*floats.get('_TintColorAlpha',1)*floats.get('_ProcedureAlpha',1);alpha_socket=alpha_factor.outputs[0]
        strength=floats.get('_TintColorIntensity',1)*floats.get('_ExpIntensity',1)
        shader.inputs['Emission Strength'].default_value=strength
        # Favor the material's actual main image rather than an arbitrary dependency.
        priorities=('_MainTex','_BaseMap','_BaseTex','_DiffuseTex','_AlbedoTex','_Tex')
        for index,row in sorted(enumerate(textures),key=lambda p:priorities.index(p[1]['first']) if p[1]['first'] in priorities else len(priorities)):
            if row['first'] not in priorities:continue
            try:tid=_target(edit,mid,['m_SavedProperties','m_TexEnvs',index,'second','m_Texture'])
            except (KeyError,StopIteration):continue
            image=assets.image_for_slot(root,edit,mid,index,resources)
            if not image:continue
            root['预览贴图槽']=row['first']+' → '+image.name
            tex=nodes.new('ShaderNodeTexImage');tex.image=image;_texture_coordinates(nodes,links,tex,row['second'])
            mult=nodes.new('ShaderNodeMixRGB');mult.blend_type='MULTIPLY';mult.inputs[0].default_value=1
            links.new(tex.outputs['Color'],mult.inputs[1]);links.new(color_socket,mult.inputs[2]);color_socket=mult.outputs[0]
            alpha=nodes.new('ShaderNodeMath');alpha.operation='MULTIPLY';links.new(tex.outputs['Alpha'],alpha.inputs[0]);links.new(alpha_socket,alpha.inputs[1]);alpha_socket=alpha.outputs[0]
            break
        if floats.get('_UseMask',0):
            for index,row in enumerate(textures):
                if row['first']!='_MaskTex':continue
                tid=_target(edit,mid,['m_SavedProperties','m_TexEnvs',index,'second','m_Texture'])
                image=assets.image_for_slot(root,edit,mid,index,resources)
                if not image:continue
                tex=nodes.new('ShaderNodeTexImage');tex.image=image;_texture_coordinates(nodes,links,tex,row['second']);channels=nodes.new('ShaderNodeSeparateColor');links.new(tex.outputs['Color'],channels.inputs['Color'])
                alpha=nodes.new('ShaderNodeMath');alpha.operation='MULTIPLY';links.new(alpha_socket,alpha.inputs[0]);links.new(channels.outputs['Red'],alpha.inputs[1]);alpha_socket=alpha.outputs[0]
                root['预览遮罩槽']='_MaskTex → '+image.name
        links.new(color_socket,shader.inputs['Base Color']);links.new(color_socket,shader.inputs['Emission Color']);links.new(alpha_socket,shader.inputs['Alpha'])
    try:mat.surface_render_method='DITHERED'
    except (AttributeError,TypeError):pass
    mat.use_backface_culling=False
    return mat
def _mesh_template(root,edit,renderer,resources):
    node=next((o for o in [root]+list(root.children_recursive)
               if renderer in json.loads(o.get('eff_effect_components','[]'))),None)
    if node and node.eff_effect.particle_mesh and edit.value(renderer).get('m_RenderMode')==4:
        return assets.preview_mesh(node.eff_effect.particle_mesh)
    mid=_target(edit,renderer,['m_Mesh']) if 'm_Mesh' in edit.value(renderer) else None
    if edit.value(renderer).get('m_RenderMode')==4 and mid in resources:
        file=str(Path(edit.state['source'])/resources[mid]['file'])
        if file in _templates:return _templates[file]
        data=json.loads(Path(file).read_text('utf-8-sig'))
        vertices=[Vector(v) for v in data['vertices']]
        faces=[tuple(data['indices'][i:i+3]) for i in range(0,len(data['indices']),3)]
        raw=data.get('uv') or [];stride=len(raw)//len(vertices) if vertices else 0
        uv=[tuple(raw[i*stride:i*stride+2]) if stride>=2 else (0,0) for i in range(len(vertices))]
        _templates[file]=(vertices,faces,uv,True)
        return _templates[file]
    if edit.value(renderer).get('m_RenderMode')==4:raise ValueError('来源 Mesh 缺少预览资源，请重新导出该特效来源')
    return [Vector((-.5,-.5,0)),Vector((.5,-.5,0)),Vector((.5,.5,0)),Vector((-.5,.5,0))],[(0,1,2,3)],[(0,0),(1,0),(1,1),(0,1)],False
def _preview_object(root,node,identity):
    key=root['eff_effect_id']+identity
    obj=next((o for o in _collection(root).objects if o.get('eff_preview_key')==key),None)
    if obj:return obj
    mesh=bpy.data.meshes.new('EFF preview particles');obj=bpy.data.objects.new('预览粒子 '+node.name,mesh)
    _collection(root).objects.link(obj);obj.parent=node;obj.hide_select=True;obj['eff_preview']=True;obj['eff_preview_key']=key
    return obj
def _shape(root,node,identity,data):
    key=root['eff_effect_id']+identity+'shape';collection=_collection(root)
    obj=next((o for o in collection.objects if o.get('eff_preview_key')==key),None)
    if not obj:
        mesh=bpy.data.meshes.new('EFF emitter bounds');obj=bpy.data.objects.new('发射范围 '+node.name,mesh);collection.objects.link(obj);obj.parent=node;obj.hide_select=True;obj['eff_preview']=True;obj['eff_preview_key']=key
    shape=data.get('ShapeModule',{});kind=shape.get('type',0);verts=[];edges=[]
    obj.hide_viewport=not (root.eff_effect.show_shape and shape.get('enabled',False));obj.hide_render=True
    signature=json.dumps(shape,sort_keys=True)
    if _shapes.get(obj.as_pointer())==signature:return
    _shapes[obj.as_pointer()]=signature
    scale=shape.get('m_Scale',{});offset=shape.get('m_Position',{})
    arc=math.radians(max(0,min(360,shape.get('arc',{}).get('value',360))))
    full_arc=abs(arc-math.tau)<1e-8
    def ring(z,r):
        begin=len(verts);count=48 if full_arc else 49
        for i in range(count):
            az=i/48*arc;verts.append((r*math.cos(az),r*math.sin(az),z))
            if i:edges.append((begin+i-1,begin+i))
        if full_arc:edges.append((begin+47,begin))
        return begin,count
    if kind in (5,15,16,18):
        verts=[(x,y,z) for x in (-.5,.5) for y in (-.5,.5) for z in ((0,) if kind==18 else (-.5,.5))]
        edges=[(a,b) for a in range(len(verts)) for b in range(a+1,len(verts)) if sum(verts[a][i]!=verts[b][i] for i in range(3))==1]
    elif kind==12:
        radius=shape.get('radius',{}).get('value',1);verts=[(-radius,0,0),(radius,0,0)];edges=[(0,1)]
    elif kind==17:
        radius=shape.get('radius',{}).get('value',1);minor=shape.get('donutRadius',.2)
        for angle in (0,math.pi/2,math.pi,3*math.pi/2):
            ring(minor*math.sin(angle),radius+minor*math.cos(angle))
        for az in ((0,arc/4,arc/2,arc*3/4) if full_arc else (0,arc/2,arc)):
            begin=len(verts)
            for i in range(24):
                angle=i/24*math.tau;r=radius+minor*math.cos(angle);verts.append((r*math.cos(az),r*math.sin(az),minor*math.sin(angle)));edges.append((begin+i,begin+(i+1)%24))
    else:
        radius=shape.get('radius',{}).get('value',1);length=shape.get('length',1);angle=math.radians(shape.get('angle',25))
        if kind in (0,1,2,3):arc=math.tau;full_arc=True
        rings=[]
        for z in ((0,length) if kind in (4,7,8,9) else (0,)):
            r=radius+z*math.tan(angle) if kind in (4,7,8,9) else radius
            rings.append(ring(z,r))
            if not full_arc:
                center=len(verts);verts.append((0,0,z));edges.extend(((rings[-1][0],center),(center,rings[-1][0]+rings[-1][1]-1)))
        if kind in (4,7,8,9):
            edges.extend((rings[0][0]+i,rings[1][0]+i) for i in ((0,12,24,36) if full_arc else (0,12,24,36,48)))
        elif kind in (0,1,2,3):
            for axis in (0,1):
                begin=len(verts)
                count=25 if kind in (2,3) else 48
                for i in range(count):
                    az=i/24*math.pi if kind in (2,3) else i/48*math.tau;p=[radius*math.cos(az),0,radius*math.sin(az)]
                    if axis:p=[0,p[0],p[2]]
                    verts.append(p)
                    if i:edges.append((begin+i-1,begin+i))
                if kind not in (2,3):edges.append((begin+47,begin))
    transformed=[BASIS@Vector(sim.shape_transform(shape,v)) for v in verts]
    obj.data.clear_geometry();obj.data.from_pydata(transformed,edges,[]);obj.display_type='WIRE';obj.show_in_front=True
    obj.hide_viewport=not (root.eff_effect.show_shape and shape.get('enabled',False));obj.hide_render=True
    obj['发射器形状']=sim.SHAPES.get(kind,'unknown')+' ('+str(kind)+')';obj['eff_shape_type']=kind

def mount_rig(owner):
    """Resolve explicit skinning, parenting, or the exact preserved import link."""
    if not owner:return None
    rig=next((m.object for m in owner.modifiers if m.type=='ARMATURE' and m.object),None)
    if rig:return rig
    parent=owner.parent
    while parent:
        if parent.type=='ARMATURE':return parent
        parent=parent.parent
    section=owner.get('eiem_skeleton');identity=owner.get('eiem_import_id')
    if not section or not identity:return None
    matches=[o for o in bpy.context.scene.objects if o.type=='ARMATURE'
             and o.get('eiem_section')==section and o.get('eiem_import_id')==identity]
    return matches[0] if len(matches)==1 else None


def mount_matrix(root):
    """The same bone-local offset exported by attachment(), without reparenting.

    Blender edit bones drop native axis scale. Reconstruct the preserved rest
    hierarchy and apply the pose delta rather than using the display bone alone.
    """
    cfg=root.eff_effect;owner=cfg.owner
    rig=mount_rig(owner)
    if not rig or cfg.bone not in rig.data.bones:return root.matrix_world.copy()
    bone=rig.data.bones[cfg.bone];chain=[];parent=bone
    while parent:chain.append(parent);parent=parent.parent
    native=Matrix.Identity(4);basis=BASIS.to_4x4()
    for item in reversed(chain):
        p,q,s=(item.get('eiem_local_'+k) for k in ('position','rotation','scale'))
        if p is None or q is None or s is None:
            native=None;break
        native=native@Matrix.LocRotScale(Vector(p),Quaternion((q[3],q[0],q[1],q[2])),Vector(s))
    pose=rig.pose.bones[cfg.bone]
    rest=basis@native@basis.inverted() if native is not None else bone.matrix_local
    return rig.matrix_world@pose.matrix@bone.matrix_local.inverted()@rest@root.matrix_local


def emitter_matrix(root,node):
    return mount_matrix(root)@root.matrix_world.inverted()@node.matrix_world


def automatic_emission(edit,node):
    for identity,value in edit.emission_controllers(json.loads(node.get('eff_effect_components','[]'))):
        if value.get('m_Enabled'):yield identity,value


def effective_particle_data(edit,node,data,placement):
    """Mirror the known source script on a copy; never rewrite author fields.

    GameAssembly VFXAutoEmissiveRate.AppyValue changes constantMax only,
    sets scalingMode, and caps maxParticles at 1000. Curve multipliers and
    constantMin remain untouched. Other script behavior stays unsupported.
    """
    for identity,script in automatic_emission(edit,node):
        data=copy.deepcopy(data)
        scale=placement.to_scale() if script.get('scaleMode',0)==0 else node.matrix_basis.to_scale()
        factor=math.prod(v for i,v in enumerate(scale) if int(script.get('axisBitMask',7))&(1<<i))
        rate=data.get('EmissionModule',{}).get('rateOverTime',{})
        if rate.get('minMaxState',0) in (0,3):rate['scalar']=min(1000,factor)*script.get('rateOverTime',0)
        data['scalingMode']=script.get('particleSystemScalingMode',2)
        initial=data.get('InitialModule',{})
        initial['maxNumParticles']=min(1000,initial.get('maxNumParticles',1000))
    return data


def birth_matrices(node,sampled,scene,data,root=None):
    if data.get('moveWithTransform',0)!=1:return {}
    fps=scene.render.fps/scene.render.fps_base;speed=data.get('simulationSpeed',1) or 1
    delay=sim.scalar(data.get('startDelay',{}));frames={round(scene.frame_start+(p['born']+delay)/speed*fps,4) for p in sampled}
    # Static hierarchies need no timeline evaluation. Animation is sampled at
    # birth, including arbitrary seeks, rather than borrowing today's transform.
    chain=[];parent=node
    while parent:chain.append(parent);parent=parent.parent
    if root and root.eff_effect.owner:
        rig=mount_rig(root.eff_effect.owner)
        if rig:chain.append(rig)
    current=lambda:emitter_matrix(root,node) if root else node.matrix_world.copy()
    if not any(o.animation_data or o.constraints or o.type=='ARMATURE' and any(b.constraints for b in o.pose.bones) for o in chain):return {f:current() for f in frames}
    original=scene.frame_current;sub=scene.frame_subframe;result={}
    try:
        for f in sorted(frames):
            scene.frame_set(math.floor(f),subframe=f-math.floor(f));result[f]=current()
    finally:scene.frame_set(original,subframe=sub)
    return result
def update(root,edit,scene,changed_nodes=None):
    time=(scene.frame_current+scene.frame_subframe-scene.frame_start)/(scene.render.fps/scene.render.fps_base)
    resources=_resources(root,edit);diagnostics=set()
    for node in list(root.children_recursive)+[root]:
        if node.get('eff_preview'):continue
        if changed_nodes is not None and node.as_pointer() not in changed_nodes:continue
        ids=json.loads(node.get('eff_effect_components','[]'))
        renderer=next((i for i in ids if edit.objects[i]['classId']==199),None)
        for identity in ids:
            if edit.objects[identity]['classId']!=198 or not renderer:continue
            placement=emitter_matrix(root,node);data=effective_particle_data(edit,node,edit.value(identity),placement)
            _shape(root,node,identity,data);obj=_preview_object(root,node,identity)
            shape_key=root['eff_effect_id']+identity+'shape'
            bounds=next((o for o in _collection(root).objects if o.get('eff_preview_key')==shape_key),None)
            if bounds:bounds.matrix_basis=node.matrix_world.inverted()@placement
            visible=root.eff_effect.preview and bool(edit.value(renderer).get('m_Enabled',True))
            obj.hide_viewport=not visible;obj.hide_render=not visible
            if not visible:continue
            sampled=sim.particles(data,time,seed=int(root['eff_effect_id'][:8],16))
            matrices=birth_matrices(node,sampled,scene,data,root);inverse=node.matrix_world.inverted()
            vertices,faces,uv,is_mesh=_mesh_template(root,edit,renderer,resources)
            mat=_material(root,edit,renderer,resources)
            out=[];polygons=[];colors=[];uvs=[]
            # Camera-facing billboards use the actual scene camera, or the active viewport orientation.
            camera=scene.camera
            face=Quaternion()
            if camera:face=camera.matrix_world.to_quaternion()
            else:
                area=next((a for screen in bpy.data.screens for a in screen.areas if a.type=='VIEW_3D'),None)
                if area:face=area.spaces.active.region_3d.view_rotation
            for particle in sampled:
                frame=placement
                if data.get('moveWithTransform',0)==1:
                    f=round(scene.frame_start+(particle['born']+sim.scalar(data.get('startDelay',{})))/(data.get('simulationSpeed',1) or 1)*(scene.render.fps/scene.render.fps_base),4)
                    frame=matrices[f]
                linear=frame.to_3x3()
                if data.get('scalingMode',0)==2:linear=frame.to_quaternion().to_matrix()
                elif data.get('scalingMode',0)==1:
                    linear=frame.to_quaternion().to_matrix()@Matrix.Diagonal(node.matrix_basis.to_scale())
                origin=BASIS@Vector(particle['origin'])
                motion=BASIS@(Vector(particle['local_position'])-Vector(particle['origin']))
                pos=frame@origin+linear@motion+BASIS@Vector(particle['world_offset'])
                x,y,z=particle['rotations'];rotation=Quaternion((0,1,0),y)@Quaternion((1,0,0),x)@Quaternion((0,0,1),z)
                offset=len(out)
                for v in vertices:
                    scaled=Vector(tuple(a*b for a,b in zip(v,particle['sizes'])))
                    delta=linear@(BASIS@(rotation@scaled)) if is_mesh else face@(Quaternion((0,0,1),z)@scaled)
                    out.append(inverse@(pos+delta))
                polygons.extend(tuple(offset+i for i in f) for f in faces);uvs.extend(sim.uv_transform(data.get('UVModule',{}),particle,u) for u in uv);colors.extend([particle['color']]*len(vertices))
            mesh=obj.data
            # Reuse topology while dragging speed/color/size at the same time.
            # A changed particle count or source mesh invalidates the topology.
            topology=(len(out),tuple(faces),len(sampled))
            if _node_signatures.get(obj.as_pointer())==topology:
                mesh.vertices.foreach_set('co',[v for point in out for v in point]);mesh.update()
            else:
                mesh.clear_geometry();mesh.from_pydata(out,[],polygons)
                _node_signatures[obj.as_pointer()]=topology
            if not mesh.materials:mesh.materials.append(mat)
            elif mesh.materials[0]!=mat:mesh.materials[0]=mat
            uv_layer=mesh.uv_layers.get('UVMap') or mesh.uv_layers.new(name='UVMap')
            indices=[0]*len(mesh.loops);mesh.loops.foreach_get('vertex_index',indices)
            uv_layer.data.foreach_set('uv',[v for i in indices for v in uvs[i]])
            color=mesh.color_attributes.get('EFFColor') or mesh.color_attributes.new(name='EFFColor',type='FLOAT_COLOR',domain='POINT')
            color.data.foreach_set('color',[v for tint in colors for v in tint])
            obj['预览粒子数']=len(sampled)
            unsupported=[k for k,v in data.items() if k.endswith('Module') and isinstance(v,dict) and v.get('enabled') and k not in ('InitialModule','ShapeModule','EmissionModule','SizeModule','RotationModule','ColorModule','VelocityModule','NoiseModule')]
            unsupported=[k for k in unsupported if k!='UVModule']
            if data.get('NoiseModule',{}).get('enabled'):unsupported.append('NoiseModule（近似摆动）')
            velocity=data.get('VelocityModule',{})
            if velocity.get('enabled') and any(sim.scalar(velocity.get(k,{})) for k in ('orbitalX','orbitalY','orbitalZ','radial')):unsupported.append('轨道 / 径向速度（暂未还原）')
            if data.get('moveWithTransform',0)==2:unsupported.append('自定义模拟空间（暂按局部预览）')
            if data.get('UVModule',{}).get('enabled') and data['UVModule'].get('timeMode')==1:unsupported.append('贴图序列帧按速度（暂按寿命预览）')
            if mat.get('eiem_shader')=='HGRP/Effect/VFXDitherAlpha':unsupported.append('材质抖动透明（渐隐仅近似，可能提前不可见）')
            if sim.scalar(data.get('EmissionModule',{}).get('rateOverDistance',{})):unsupported.append('rateOverDistance')
            if edit.value(renderer).get('m_RenderMode',0) not in (0,4):unsupported.append('Renderer mode '+str(edit.value(renderer).get('m_RenderMode'))+'（按普通 billboard 预览）')
            diagnostics.update(unsupported)
            if any(automatic_emission(edit,node)):
                diagnostics.add('高级自动数量规则已启用（预览已计入）')
            if root.eff_effect.preview:obj['预览支持提示']=', '.join(sorted(unsupported)) or '基础模块预览'
            if not resources:root['预览资源提示']='来源未包含预览资产；重新用配套 AnimeStudio 导出可取得原 Mesh/贴图'
    # Selective updates retain warnings from other emitters.
    if changed_nodes is not None:
        for obj in root.children_recursive:
            if obj.get('eff_preview_key') and not obj.get('eff_shape_type') and obj.get('预览支持提示') not in (None,'基础模块预览'):
                diagnostics.update(obj['预览支持提示'].split(', '))
    root['预览未还原模块']=', '.join(sorted(diagnostics)) or '无额外模块'
def refresh(root,edit,changed_nodes=None):
    global _busy
    old=_busy;_busy=True
    try:update(root,edit,bpy.context.scene,changed_nodes)
    finally:_busy=old

@bpy.app.handlers.persistent
def asset_changed(scene,depsgraph):
    if _busy:return
    for update in depsgraph.updates:
        item=update.id.original;key=item.as_pointer()
        if key in _asset_revisions:_asset_revisions[key]+=1

def author_signature(root):
    from . import eiem_effect_material as material_bridge
    from . import eiem_effect_gradient_editor as gradient_editor
    signature={}
    for obj in [root]+list(root.children_recursive):
        if obj.get('eff_preview'):continue
        custom=obj.eff_effect.particle_mesh
        if custom:
            _asset_revisions.setdefault(custom.data.as_pointer(),0)
            _asset_revisions.setdefault(custom.as_pointer(),0)
        mesh_signature=(custom.as_pointer(),custom.data.as_pointer(),_asset_revisions.get(custom.data.as_pointer(),0),_asset_revisions.get(custom.as_pointer(),0),tuple(v for row in custom.matrix_basis for v in row)) if custom else None
        signature[obj.as_pointer()]=(tuple(obj.matrix_world[i][j] for i in range(4) for j in range(4)),tuple((k,tuple(obj[k]) if k.startswith('fxv_') else obj[k]) for k in obj.keys() if k.startswith(('fx_','fxv_','fxu_'))),mesh_signature,
            tuple((s.name,s.image.as_pointer() if s.image else 0,s.linear,s.mipmaps,s.wrap,s.filter) for s in obj.eff_effect_textures),gradient_editor.signature(obj))
    signature['config']=(root.eff_effect.preview,root.eff_effect.show_shape,root['eff_effect_document'],
        root.eff_effect.owner.as_pointer() if root.eff_effect.owner else 0,root.eff_effect.bone,tuple(v for row in mount_matrix(root) for v in row))
    signature['materials']=tuple(material_bridge.signature(o) for o in root.children_recursive if o.get('eff_effect_resource'))
    return signature


def curve_refreshed(root,nodes):
    """A curve refresh already covered these emitters, but no unrelated edits."""
    previous=_signatures.get(root.as_pointer())
    if previous is None:return
    current=author_signature(root)
    previous['config']=previous['config'][:2]+(current['config'][2],)+previous['config'][3:]
    for node in nodes:
        if node in current:previous[node]=current[node]


def live_update():
    if not _sync:return None
    if _busy or bpy.context.screen and bpy.context.screen.is_animation_playing:return POLL_INTERVAL
    for root in list(bpy.context.scene.objects):
        if not root.get('eff_effect_document'):continue
        signature=author_signature(root)
        key=root.as_pointer()
        previous=_signatures.get(key)
        if previous==signature:continue
        changed={k for k,v in signature.items() if previous is None or previous.get(k)!=v}
        # A resource or ancestor change can affect every emitter. Scalar edits
        # on an emitter only resimulate that emitter.
        if 'config' in changed or 'materials' in changed:changed=None
        elif any(o.as_pointer() in changed and (o==root or o.get('eff_effect_resource') or any(not c.get('eff_preview') for c in o.children)) for o in [root]+list(root.children_recursive)):changed=None
        _signatures[key]=signature
        try:
            refresh(root,_sync(root),changed)
            if '预览错误' in root:del root['预览错误']
        except Exception as error:root['预览错误']=str(error)
    return POLL_INTERVAL
@bpy.app.handlers.persistent
def frame(scene,*_):
    global _busy
    if _busy or not _sync:return
    _busy=True
    try:
        for root in list(scene.objects):
            if root.get('eff_effect_document'):
                try:
                    update(root,_sync(root),scene)
                    if '预览错误' in root:del root['预览错误']
                except Exception as e:root['预览错误']=str(e)
    finally:_busy=False
def register(sync,root_of):
    global _sync,_root_of
    _sync=sync;_root_of=root_of
    if frame not in bpy.app.handlers.frame_change_post:bpy.app.handlers.frame_change_post.append(frame)
    if asset_changed not in bpy.app.handlers.depsgraph_update_post:bpy.app.handlers.depsgraph_update_post.append(asset_changed)
    if not bpy.app.timers.is_registered(live_update):bpy.app.timers.register(live_update,first_interval=POLL_INTERVAL,persistent=True)

def clear_cache():
    _history.clear();_templates.clear();_signatures.clear();_shapes.clear();_node_signatures.clear();_asset_revisions.clear()
def unregister():
    global _sync,_root_of
    if frame in bpy.app.handlers.frame_change_post:bpy.app.handlers.frame_change_post.remove(frame)
    if asset_changed in bpy.app.handlers.depsgraph_update_post:bpy.app.handlers.depsgraph_update_post.remove(asset_changed)
    if bpy.app.timers.is_registered(live_update):bpy.app.timers.unregister(live_update)
    clear_cache();_sync=None;_root_of=None
