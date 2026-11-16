"""Author Empty dependencies. Mesh targeting and mount ownership stay separate."""
import json
import math
import subprocess
import tempfile
import uuid
from pathlib import Path
import bpy
from mathutils import Matrix, Quaternion, Vector


def eligible(obj):
    return obj.type == 'EMPTY' and not any(obj.get(k) for k in
        ('eff_effect_id', 'eff_effect_node', 'eff_effect_resource', 'eff_identity', 'eff_preview', 'eff_effect_root', 'eff_source_identity'))


def linked_effects(obj):
    """Find effects moved by this author Empty, without treating it as a root."""
    if not obj or not eligible(obj): return []
    result = []
    for root in bpy.context.scene.objects:
        if not root.get('eff_effect_document'): continue
        cfg = root.eff_effect
        if cfg.usage != 'MESH_ATTACHMENT': continue
        current = cfg.mount
        while current and current != cfg.owner:
            if current == obj:
                result.append(root); break
            current = current.parent
    return sorted(result, key=lambda root: root.name)


def chain(root):
    cfg = root.eff_effect
    if not cfg.mount: return []
    result = []; current = cfg.mount
    while current and current != cfg.owner:
        if not eligible(current):
            raise ValueError('挂点及必要父级必须是作者 Empty：' + current.name)
        if current.constraints or current.parent_type != 'OBJECT':
            raise ValueError('挂点暂不支持约束或骨骼父级；请在特效中选择基础骨骼：' + current.name)
        result.append(current); current = current.parent
    return list(reversed(result))


def identity(obj):
    if not obj.get('eff_mount_id'): obj['eff_mount_id'] = uuid.uuid4().hex[:16]
    return obj['eff_mount_id']


def dependencies(roots):
    return set(obj for root in roots for obj in chain(root))


def graphs(roots, animation, basis):
    by_top = {}
    for root in roots:
        nodes = chain(root)
        if nodes: by_top.setdefault(nodes[0], set()).update(nodes)
    result = {}; scene = bpy.context.scene
    fps = scene.render.fps / scene.render.fps_base
    for top, objects in by_top.items():
        def depth(obj):
            count=0
            while obj.parent: count+=1;obj=obj.parent
            return count
        ordered = sorted(objects, key=lambda o: (depth(o), o.name_full))
        # Public paths mirror the author's names. They are also used when
        # compiling AnimationClip bindings, so no post-export renaming occurs.
        paths = {}; nodes = []
        for obj in ordered:
            import re
            name = re.sub(r'[/\\:\x00-\x1f]', '_', obj.name).strip() or 'Mount'
            base = (paths[obj.parent] + '/' if obj.parent in paths else '') + name
            path, suffix = base, 2
            while path in paths.values():
                path = base + '_' + str(suffix);suffix += 1
            paths[obj] = path
            matrix = obj.matrix_parent_inverse @ obj.matrix_basis
            p, q, s = (basis.inverted() @ matrix @ basis).decompose()
            if not all(math.isfinite(v) for v in (*p, *q, *s)): raise ValueError('挂点变换包含非有限数值：' + obj.name)
            nodes.append(dict(path=path, label=obj.name, position=list(p),
                              rotation=[q.x,q.y,q.z,q.w], scale=list(s)))
        graph_name = re.sub(r'[^A-Za-z0-9_.-]+', '_', top.name).strip('_.-') or 'Mount'
        used = {entry[0]['id'] for entry in result.values()}
        graph_id, suffix = graph_name, 2
        while graph_id in used:
            graph_id = graph_name + '_' + str(suffix);suffix += 1
        graph = dict(id=graph_id, nodes=nodes, animations=[])
        ids = {c.identity for o in ordered for c in o.eiem_bone_animations if c.enabled}
        for group in sorted(ids):
            members = [(o,c) for o,c in animation.bone_group_members(group) if o in objects and c.enabled]
            c = members[0][1]
            duration = (c.end-c.start)/fps
            if duration <= 0: raise ValueError('挂点动画范围无效：' + c.label)
            count = max(2, math.ceil(duration*c.sample_rate)+1); tracks = []
            for obj, member in members:
                if obj.animation_data and (obj.animation_data.drivers or any(not t.mute for t in obj.animation_data.nla_tracks)):
                    raise ValueError('挂点动画请先将驱动器/NLA烘焙为独立 Action：' + obj.name)
                found = animation.bone_curves(obj, member)
                if not found:
                    continue
                saved = obj.matrix_basis.copy(); samples = [[],[],[]]
                try:
                    for i in range(count):
                        obj.matrix_basis = saved
                        frame = c.start + (c.end-c.start)*i/(count-1)
                        for curve in found['Transform']:
                            getattr(obj,curve.data_path)[curve.array_index] = curve.evaluate(frame)
                        p,q,s = (basis.inverted() @ obj.matrix_parent_inverse @ obj.matrix_basis @ basis).decompose()
                        values = (list(p),[q.x,q.y,q.z,q.w],list(s))
                        if any(not math.isfinite(v) for value in values for v in value):
                            raise ValueError('挂点动画含无效变换：' + obj.name)
                        # Keep quaternions on one hemisphere for native interpolation.
                        if samples[1] and sum(a*b for a,b in zip(samples[1][-1],values[1]))<0:
                            values[1][:] = [-v for v in values[1]]
                        for target,value in zip(samples,values): target.append(value)
                finally: obj.matrix_basis = saved
                for kind,values in zip(('Position','Rotation','Scale'),samples):
                    tracks.append(dict(path=paths[obj],kind=kind,samples=values))
            if tracks:
                graph['animations'].append(dict(id=group,label=c.label,key=c.hotkey,
                    duration=duration,speed=c.speed,speedVariable=c.speed_variable or None,
                    loop=c.loop,autoplay=c.autoplay,sample_rate=c.sample_rate,sample_count=count,
                    tracks=tracks))
        for obj in ordered: result[obj] = (graph, paths[obj])
    return result


def assemble(roots, rows, animation, basis):
    table = graphs(roots, animation, basis)
    for root,row in zip(roots,rows):
        if root.eff_effect.mount:
            graph,node = table[root.eff_effect.mount]
            row.update(authorHierarchy=graph, mountNode=node)


def compile_clips(roots, rows, include_hotkeys=True):
    built = {};assets_used=set()
    for root,row in zip(roots,rows):
        graph = row.get('authorHierarchy')
        if not graph: continue
        output = []
        for clip in graph['animations']:
            key = (graph['id'],clip['id'])
            if key not in built:
                import re
                stem = re.sub(r'[^A-Za-z0-9_.-]+','_',clip['label']).strip('_.-') or 'Animation'
                base='assets/eff/animations/'+graph['id']+'_'+stem
                asset,suffix=base+'.anim',2
                while asset.casefold() in assets_used:
                    asset=base+'_'+str(suffix)+'.anim';suffix+=1
                assets_used.add(asset.casefold())
                request = dict(format='EFF_NATIVE_ANIMATION',version=1,asset=asset,
                    name=clip['label'],sampleRate=(clip['sample_count']-1)/clip['duration'],
                    loop=clip['loop'],tracks=clip['tracks'])
                from . import eiem_effect_publish
                compiler = str(eiem_effect_publish.compiler_path(bpy.path.abspath(root.eff_effect.compiler) if root.eff_effect.compiler else ''))
                if not Path(compiler).is_file(): raise ValueError('挂点动画需要配套 AnimeStudio 构建工具')
                with tempfile.TemporaryDirectory(prefix='eff-mount-animation-') as temporary:
                    source=Path(temporary)/'request.json';target=Path(temporary)/'clip.ab'
                    source.write_text(json.dumps(request),encoding='utf-8')
                    completed=subprocess.run([compiler,'--build-eff-animation',str(source),str(target)],
                        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),timeout=120,capture_output=True)
                    if completed.returncode or not target.is_file():
                        error=Path(str(target)+'.error.txt')
                        raise ValueError('挂点原生动画构建失败：'+(error.read_text('utf-8') if error.exists() else completed.stderr.decode(errors='replace')))
                    built[key] = dict(clip,key=clip['key'] if include_hotkeys else '',hierarchy=graph['id'],asset=asset,data=target.read_bytes())
            output.append(built[key])
        row['_mount_native'] = output


def restore(root, row, cache, collection, basis):
    graph=row.get('authorHierarchy')
    if not graph: return
    key=(graph['id'],root.eff_effect.owner.as_pointer() if root.eff_effect.owner else 0,root.eff_effect.bone)
    if key not in cache:
        nodes={}
        for node in graph['nodes']:
            obj=bpy.data.objects.new(node['label'],None);collection.objects.link(obj)
            obj['eff_mount_id']=node['path'].rsplit('/',1)[-1].removeprefix('N_')
            parent=node['path'].rsplit('/',1)[0] if '/' in node['path'] else ''
            obj.parent=nodes.get(parent,root.eff_effect.owner)
            q=node['rotation'];obj.matrix_basis=basis@Matrix.LocRotScale(Vector(node['position']),Quaternion((q[3],*q[:3])),Vector(node['scale']))@basis.inverted()
            nodes[node['path']]=obj
        from . import eiem_shape_animation as animation
        for clip in graph.get('animations',()):
            targets=sorted({t['path'] for t in clip['tracks']})
            for path in targets:
                obj=nodes[path];obj.rotation_mode='QUATERNION'
                action=bpy.data.actions.new(clip['label']);obj.animation_data_create();obj.animation_data.action=action
                # Reconstruct editable keys on Blender's timeline from exported samples.
                tracks={t['kind']:t['samples'] for t in clip['tracks'] if t['path']==path}
                count=len(next(iter(tracks.values())));fps=bpy.context.scene.render.fps/bpy.context.scene.render.fps_base
                for i in range(count):
                    p=tracks['Position'][i];q=tracks['Rotation'][i];s=tracks['Scale'][i]
                    obj.matrix_basis=basis@Matrix.LocRotScale(Vector(p),Quaternion((q[3],*q[:3])),Vector(s))@basis.inverted()
                    for prop in ('location','rotation_quaternion','scale'):
                        obj.keyframe_insert(prop,frame=1+clip['duration']*fps*i/(count-1),group='Transform')
                descriptor=animation.current_bone_descriptor(obj)
                settings=dict(label=clip['label'],enabled=True,start=1,end=1+clip['duration']*fps,
                    loop=clip['loop'],autoplay=clip['autoplay'],speed=clip['speed'],sample_rate=clip['sample_rate'],hotkey=clip['key'])
                member=animation.append_bone_member(obj,clip['id'],descriptor,settings)
                member.speed_variable=clip.get('speedVariable') or ''
                bpy.context.scene.frame_set(bpy.context.scene.frame_current)
        cache[key]=nodes
    root.eff_effect.mount=cache[key][row['mountNode']]
