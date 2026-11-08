"""Blender preview instances: original static renderers, clip overlays and VAT."""
import json
import importlib
from pathlib import Path

import bpy
from mathutils import Matrix, Quaternion, Vector

_MODULE_PREFIX = (__package__ + ".") if __package__ else ""


def _module(name):
    return importlib.import_module(_MODULE_PREFIX + name)


assets = _module("eiem_effect_assets")
timeline = _module("eiem_effect_timeline")
Reader = _module("eiem_effect_native").Reader

_sessions = {}
_enum_items = {}
BASIS = Matrix(((-1, 0, 0), (0, 0, -1), (0, 1, 0)))


class Session:
    def __init__(self, edit):
        self.reader = Reader(edit)
        self.timeline = None
        self.meshes, self.textures = {}, {}

    def clips(self, edit):
        if self.timeline is None:
            self.timeline = timeline.Timeline(edit, self.reader)
        return self.timeline

    def mesh(self, identity):
        if identity not in self.meshes:
            self.meshes[identity] = self.reader.mesh(identity)
        return self.meshes[identity]

    def texture(self, identity):
        if identity not in self.textures:
            loaded = self.reader.float_texture(identity)
            if loaded[0].nbytes + sum(value[0].nbytes for value in self.textures.values()) > 128 * 1024 * 1024:
                raise ValueError('VAT 数据纹理缓存超过 128 MiB 预览预算；原始资源未修改')
            self.textures[identity] = loaded
        return self.textures[identity]


def session(edit):
    key = edit.state['source'], edit.state['sourceManifestSha256']
    if key not in _sessions:
        _sessions[key] = Session(edit)
    _sessions[key].reader.edit = edit
    return _sessions[key]


def clip_items(root, edit):
    key = edit.state['source'], edit.state['sourceManifestSha256']
    if key not in _enum_items:
        _enum_items[key] = [('NONE', '不播放来源片段', '仅使用作者参数与当前姿态')]
        _enum_items[key] += [(identity, clip.name, '%.3f 秒；%g fps；独立片段，不推测技能状态机' %
                             (clip.stop - clip.start, clip.rate)) for identity, clip in session(edit).clips(edit).clips.items()]
    return _enum_items[key]


class Frame:
    def __init__(self, root, edit, time):
        self.root, self.edit, self.time = root, edit, time
        self.session = session(edit)
        self.transforms, self.renderers, self.warnings = {}, {}, []
        if root.eff_effect.source_animation and root.eff_effect.preview_clip != 'NONE':
            self.transforms, self.renderers, self.warnings = self.session.clips(edit).evaluate(
                edit, root.eff_effect.preview_clip, time, root.eff_effect.clip_loop)
        root_components = json.loads(root.get('eff_effect_components', '[]'))
        if any(identity in self.transforms for identity in root_components):
            self.warnings.append('根 Transform 动画尚未回放；根仍使用作者挂载偏移')
        self.placements = {}

    def placement(self, node):
        preview = _module("eiem_effect_preview")
        key = node.as_pointer()
        if key in self.placements:
            return self.placements[key]
        if node == self.root:
            result = preview.mount_matrix(self.root)
        elif node.parent:
            ids = json.loads(node.get('eff_effect_components', '[]'))
            tid = next((i for i in ids if i in self.transforms), None)
            if tid:
                data = self.transforms[tid]
                p, q, s = data['m_LocalPosition'], data['m_LocalRotation'], data['m_LocalScale']
                local = BASIS.to_4x4() @ Matrix.LocRotScale(Vector(tuple(p[k] for k in 'xyz')),
                        Quaternion((q['w'], q['x'], q['y'], q['z'])), Vector(tuple(s[k] for k in 'xyz'))) @ BASIS.to_4x4().inverted()
            else:
                local = node.matrix_parent_inverse @ node.matrix_basis
            result = self.placement(node.parent) @ local
        else:
            result = node.matrix_world.copy()
        self.placements[key] = result
        return result

    def renderer_edit(self, renderer):
        return timeline.overlay(self.edit, self.renderers[renderer]) if renderer in self.renderers else self.edit

    def at(self, node, time):
        return Frame(self.root, self.edit, time).placement(node)

    def visible(self, node):
        while node:
            identity = node.get('eff_effect_node')
            if identity in self.edit.state['values'] and not self.edit.value(identity).get('m_IsActive', True):
                return False
            if node == self.root:
                break
            node = node.parent
        return True


def _pointer(edit, owner, path):
    preview = _module("eiem_effect_preview")
    return preview._target(edit, owner, path)


def _vat(frame, edit, mid, mesh, material):
    vat = _module("eiem_effect_vat")
    data = edit.value(mid)
    floats = {row['first']: row['second'] for row in data.get('m_SavedProperties', {}).get('m_Floats', [])}
    keywords = data.get('m_ValidKeywords', data.get('m_ShaderKeywords', '').split())
    if '_VAT_SOFTBODY' not in keywords:
        if floats.get('_EnableHoudiniVAT', 0):
            raise ValueError('VAT 类型未验证：' + str(floats.get('_HoudiniVATType')))
        return mesh['vertices'], mesh.get('normals'), None
    if material.get('eiem_shader') not in ('HGRP/Effect/VFXWaterRefract', 'HGRP/Effect/VFXBaseV2'):
        raise ValueError('此 Shader 的 Softbody VAT 布局尚未验证')
    textures = data['m_SavedProperties']['m_TexEnvs']
    def texture(slot):
        index = next((i for i, row in enumerate(textures) if row['first'] == slot), None)
        if index is None:
            return None
        target = _pointer(edit, mid, ['m_SavedProperties', 'm_TexEnvs', index, 'second', 'm_Texture'])
        if not target:
            return None
        # A replaced data texture must not silently keep sampling source bytes.
        bridge = _module("eiem_effect_material")
        setting = assets.texture_slot(frame.root, mid, slot)
        if bridge.texture_path(frame.root, mid, slot) or setting and setting.image:
            raise ValueError('VAT 数据贴图已替换；新数据的浮点预览尚未接入：' + slot)
        return frame.session.texture(target)
    position = texture('_PositionTexture')
    if position is None:
        raise ValueError('VAT 缺少 _PositionTexture')
    return vat.softbody(mesh, floats, position, texture('_RotationTexture'), frame.time)


def static_renderers(frame, resources, diagnostics):
    preview = _module("eiem_effect_preview")
    root, edit = frame.root, frame.edit
    for node in [root] + list(root.children_recursive):
        if node.get('eff_preview') or node.get('eff_particle_template'):
            continue
        ids = json.loads(node.get('eff_effect_components', '[]'))
        renderer = next((i for i in ids if edit.objects[i]['classId'] == 23), None)
        mesh_filter = next((i for i in ids if edit.objects[i]['classId'] == 33), None)
        if not renderer or not mesh_filter:
            continue
        mesh_id = _pointer(edit, mesh_filter, ['m_Mesh'])
        if not mesh_id:
            diagnostics.add('静态 Mesh 引用缺失：' + node.name)
            continue
        try:
            source = frame.session.mesh(mesh_id)
        except (ValueError, KeyError) as error:
            diagnostics.add('静态 Mesh：' + str(error))
            continue
        current = frame.renderer_edit(renderer)
        materials = current.value(renderer).get('m_Materials', [])
        # Unity renders extra materials on the final submesh again. Separate
        # instances preserve these passes rather than losing slots 1..N.
        for slot in range(max(len(materials), len(source['submeshes']))):
            obj = preview._preview_object(root, node, renderer + '/static/' + str(slot))
            obj.name = '原网格 · ' + node.name + ' · 材质 ' + str(slot)
            obj['eff_preview_static'] = True
            visible = root.eff_effect.preview and frame.visible(node) and current.value(renderer).get('m_Enabled', True)
            obj.hide_viewport = obj.hide_render = not visible
            if not visible:
                continue
            material_slot = min(slot, len(materials) - 1)
            mid = _pointer(current, renderer, ['m_Materials', material_slot]) if materials else None
            mat = preview._material_asset(root, current, mid, resources, instance=renderer + '/static/' + str(slot))
            vertices, normals, display = source['vertices'], source.get('normals'), None
            if mid and root.eff_effect.vat_preview:
                try:
                    vertices, normals, display = _vat(frame, current, mid, source, mat)
                except (ValueError, KeyError) as error:
                    diagnostics.add(str(error))
                    obj['VAT 提示'] = str(error)
            if display is not None:
                obj['eff_preview_vat'] = True
                obj['VAT 帧'] = display
            elif 'eff_preview_vat' in obj:
                del obj['eff_preview_vat']
            faces = source['submeshes'][min(slot, len(source['submeshes']) - 1)]
            mesh = obj.data
            if len(mesh.vertices) != len(vertices) or len(mesh.polygons) != len(faces):
                mesh.clear_geometry()
                mesh.from_pydata([BASIS @ Vector(v) for v in vertices], [], [tuple(reversed(f)) for f in faces])
                for uv_slot, values in source['uvs'].items():
                    layer = mesh.uv_layers.new(name='UV' + str(uv_slot))
                    layer.data.foreach_set('uv', [float(v) for loop in mesh.loops for v in values[loop.vertex_index, :2]])
                mesh.uv_layers.active_index = 0
                color = mesh.color_attributes.new(name='EFFColor', type='FLOAT_COLOR', domain='POINT')
                values = source['colors']
                color.data.foreach_set('color', values.ravel() if values is not None and values.shape[1] == 4 else [1.] * (len(vertices) * 4))
            else:
                mesh.vertices.foreach_set('co', [float(v) for point in vertices for v in BASIS @ Vector(point)])
            mesh.update()
            if normals is not None:
                mesh.normals_split_custom_set_from_vertices([BASIS @ Vector(n) for n in normals])
            if not mesh.materials:
                mesh.materials.append(mat)
            elif mesh.materials[0] != mat:
                mesh.materials[0] = mat
            obj.matrix_basis = node.matrix_world.inverted_safe() @ frame.placement(node)
            obj['预览支持提示'] = '来源 MeshRenderer / 子网格 / 多材质；像素着色为 Blender 近似'
            diagnostics.add('水材质的折射 / 深度 / 光照为 Blender 近似')


def particle_position(frame, node, data, particle, at=None):
    sim = _module("eiem_effect_simulation")
    placement = frame.placement(node) if at is None else frame.at(node, at)
    if data.get('moveWithTransform', 0) == 1:
        born = (particle['born'] + sim.scalar(data.get('startDelay', {}))) / (data.get('simulationSpeed', 1) or 1)
        placement = frame.at(node, born)
    linear = placement.to_3x3()
    if data.get('scalingMode', 0) == 2:
        linear = placement.to_quaternion().to_matrix()
    elif data.get('scalingMode', 0) == 1:
        linear = placement.to_quaternion().to_matrix() @ Matrix.Diagonal(node.matrix_basis.to_scale())
    origin = BASIS @ Vector(particle['origin'])
    motion = BASIS @ (Vector(particle['local_position']) - Vector(particle['origin']))
    return placement @ origin + linear @ motion + BASIS @ Vector(particle['world_offset'])


def trails(frame, node, identity, renderer, data, resources, diagnostics):
    preview = _module("eiem_effect_preview")
    sim = _module("eiem_effect_simulation")
    sampler = _module("eiem_effect_trails")
    module = data.get('TrailModule', {})
    key = identity + '/trails'
    existing = next((o for o in root_collection(frame.root).objects if o.get('eff_preview_key') == frame.root['eff_effect_id'] + key), None)
    visible = (frame.root.eff_effect.preview and frame.root.eff_effect.preview_particles and
               frame.root.eff_effect.trail_preview and frame.visible(node) and module.get('enabled') and
               frame.edit.value(renderer).get('m_Enabled', True))
    if not visible:
        if existing:existing.hide_viewport = existing.hide_render = True
        return
    paths, warnings = sampler.paths(data, frame.time, int(frame.root['eff_effect_id'][:8], 16))
    diagnostics.update(warnings)
    obj = existing or preview._preview_object(frame.root, node, key)
    obj.name = '预览拖尾 · ' + node.name; obj['eff_preview_trail'] = True
    obj.hide_viewport = obj.hide_render = False
    camera = bpy.context.scene.camera
    view = camera.matrix_world.to_quaternion() @ Vector((0, 0, 1)) if camera else Vector((0, 0, 1))
    inverse = node.matrix_world.inverted_safe()
    vertices, faces, colors, uvs = [], [], [], []
    for path in paths:
        points = [(particle_position(frame, node, data, particle, at if module.get('worldSpace') else None), particle)
                  for at, particle in path]
        points = sampler.filtered(points, max(0, module.get('minVertexDistance', 0)))
        if len(points) < 2:
            continue
        distances = [0.]
        for a, b in zip(points, points[1:]):distances.append(distances[-1] + (b[0] - a[0]).length)
        total = max(distances[-1], 1e-10); start = len(vertices)
        for index, (point, particle) in enumerate(points):
            tangent = points[min(len(points) - 1, index + 1)][0] - points[max(0, index - 1)][0]
            side = tangent.cross(view)
            if side.length_squared < 1e-12:side = tangent.cross(Vector((0, 1, 0)))
            if side.length_squared < 1e-12:side = Vector((1, 0, 0))
            phase = distances[index] / total
            width = sim.scalar(module.get('widthOverTrail', 1), phase, particle['random'], fallback=1)
            if module.get('sizeAffectsWidth'):width *= particle['size']
            side.normalize(); side *= width * .5
            vertices.extend((inverse @ (point - side), inverse @ (point + side)))
            tint = particle['color'] if module.get('inheritParticleColor', True) else (1, 1, 1, 1)
            color_life = sim.color(module.get('colorOverLifetime', {}), particle['phase'], particle['random'])
            color_trail = sim.color(module.get('colorOverTrail', {}), phase, particle['random'])
            rgba = tuple(a * b * c for a, b, c in zip(tint, color_life, color_trail))
            colors.extend((rgba, rgba))
            mode = module.get('textureMode', 0)
            u = distances[index] if mode == 1 else index / (len(points) - 1) if mode == 2 else phase
            if mode not in (0, 1, 2):diagnostics.add('Trail 贴图模式 %s 暂按拉伸预览' % mode)
            uvs.extend(((u, 0), (u, 1)))
            if index:faces.append((start + index * 2 - 2, start + index * 2, start + index * 2 + 1, start + index * 2 - 1))
    mesh = obj.data; mesh.clear_geometry(); mesh.from_pydata(vertices, [], faces)
    layer = mesh.uv_layers.new(name='UVMap')
    layer.data.foreach_set('uv', [v for loop in mesh.loops for v in uvs[loop.vertex_index]])
    color = mesh.color_attributes.new(name='EFFColor', type='FLOAT_COLOR', domain='POINT')
    color.data.foreach_set('color', [v for row in colors for v in row])
    current = frame.renderer_edit(renderer)
    if len(current.value(renderer).get('m_Materials', [])) > 1:
        mid = _pointer(current, renderer, ['m_Materials', 1])
        mat = preview._material_asset(frame.root, current, mid, resources, instance=renderer + '/trails')
        mesh.materials.clear(); mesh.materials.append(mat)
    else:
        diagnostics.add('Trail 缺少独立拖尾材质槽，沿用粒子材质')
        mesh.materials.clear(); mesh.materials.append(preview._material(frame.root, current, renderer, resources))
    obj['预览支持提示'] = '；'.join(warnings)
    obj['拖尾路径数'] = len(paths)


def root_collection(root):
    return bpy.data.collections.get(root.get('eff_effect_collection')) or root.users_collection[0]


def research_report(root, edit):
    bridge = _module("eiem_effect_material")
    current = session(edit)
    clips = current.clips(edit)
    materials=[]
    for obj in root.children_recursive:
        if not obj.get('eff_effect_resource'):continue
        material=bridge.material_for(obj)
        if not material:continue
        mid=next((i for i in json.loads(obj.get('eff_effect_components','[]')) if edit.objects[i]['classId']==21),None)
        if not mid:continue
        assets.setup_textures(root,edit,obj,mid)
        materials.append(dict(identity=mid,name=material.name,shader=material.get('eiem_shader'),
            nativeProperties=edit.value(mid),textures=[dict(slot=s.name,**json.loads(s.source_metadata or '{}')) for s in obj.eff_effect_textures]))
    tracks=[]
    for identity,clip in clips.clips.items():
        bindings=[]
        for binding in clip.bindings:
            item=dict(binding)
            item['paths']=[r['name'] for r in clips.paths.get(binding['path'],[])]
            if binding['customType']==22:
                item['resolvedProperties']=sorted({target[0] for m in materials
                    if (target:=timeline.material_property(m['nativeProperties'],binding['attribute']))})
                item['component']=None if binding['attribute']&0x80000000 else (binding['attribute']>>28)&3
            bindings.append(item)
        tracks.append(dict(identity=identity,name=clip.name,start=clip.start,stop=clip.stop,sampleRate=clip.rate,
            loop=clip.loop,scalarCurves=clip.total,bindings=bindings,warnings=clip.warnings))
    return dict(format='EFF_EFFECT_RESEARCH/1',source=edit.state['source'],prefab=edit.state['catalog']['prefab'],
        sourceManifestSha256=edit.state['sourceManifestSha256'],materials=materials,clips=tracks,
        renderers=current.reader.manifest['rendererUses'],warnings=clips.warnings,
        sourceWarnings=edit.state['catalog'].get('warnings', []),
        previewWarnings=root.get('预览未还原模块', ''),
        limitations=['独立 AnimationClip 采样不代表完整技能 / Animator 状态机与游戏脚本。',
                     '根 Transform / Euler / PPtr / 未解码 ACL 轨道未回放；未解析绑定保留警告。',
                     'Softbody VAT 顶点适配限已核对的 VFXWaterRefract / VFXBaseV2 数据契约；不是通用 VAT 或骨骼蒙皮支持。',
                     '粒子网格 / 自定义顶点流 VAT 未回放；静态 MeshRenderer 预览读取来源 Mesh，不代表编辑该 Mesh 的完整导出支持。',
                     'Trail / Ribbon 为有限时间采样重建；材质折射、深度、光照和部分自定义通道为 Blender 近似。'])


def clear_cache():
    _sessions.clear()
    _enum_items.clear()
