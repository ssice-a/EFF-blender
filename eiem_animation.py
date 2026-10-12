"""Import offline-evaluated EFF bone animation onto an existing source armature."""
import hashlib
import json
import math
from pathlib import Path

import bpy
from mathutils import Matrix, Quaternion, Vector


def read_library(path):
    data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if data.get('format') != 'EFF_ANIMATION_LIBRARY' or data.get('coordinate') != 'unity-y-up-left-handed':
        raise ValueError('请选择 EFF 解包导出的 .eanim 动画库')
    nodes = {}
    for node in data['skeleton']:
        path = node['path']
        if path in nodes or (node['parent'] is not None and node['parent'] not in nodes):
            raise ValueError('动画库骨骼路径重复或父子顺序错误：' + path)
        _matrix(node['position'] + node['rotation'] + node['scale'])
        nodes[path] = node
    if not nodes or not data['clips']:
        raise ValueError('动画库没有骨架或动画')
    for clip in data['clips']:
        times = clip['times']
        if not times or times[0] != 0 or not all(math.isfinite(t) and t >= 0 for t in times):
            raise ValueError('动画时间无效：' + clip['name'])
        if any(b <= a for a, b in zip(times, times[1:])) or times[-1] != clip['duration']:
            raise ValueError('动画时间不连续或时长不一致：' + clip['name'])
        if clip['kind'] not in {'Emotion', 'Pose', 'Anim'}:
            raise ValueError('未知动画类型：' + clip['kind'])
        paths = set()
        for track in clip['tracks']:
            path = track['path']
            track_times = track.get('times', times)
            if path not in nodes or path in paths or len(track['samples']) != len(track_times):
                raise ValueError('动画骨骼或采样数量错误：' + path)
            if not track_times or track_times[0] != 0 or track_times[-1] != clip['duration'] or any(b <= a for a, b in zip(track_times, track_times[1:])):
                raise ValueError('动画轨道时间错误：' + path)
            paths.add(path)
            for sample in track['samples']:
                _matrix(sample)
    return data


def _matrix(sample):
    if len(sample) != 10 or not all(math.isfinite(v) for v in sample):
        raise ValueError('动画 TRS 必须包含 10 个有限数值')
    q = Quaternion((sample[6], *sample[3:6]))
    if abs(q.magnitude - 1) > 0.001:
        raise ValueError('动画旋转四元数未归一化')
    return Matrix.LocRotScale(Vector(sample[:3]), q, Vector(sample[7:]))


def target_armature(context):
    obj = context.active_object
    if obj and obj.type == 'ARMATURE':
        return obj
    if obj and obj.type == 'MESH':
        rigs = {m.object for m in obj.modifiers if m.type == 'ARMATURE' and m.object}
        if len(rigs) == 1:
            return rigs.pop()
    raise ValueError('先选中 EFF 骨架，或只绑定一个 EFF 骨架的网格')


def import_animations(path, rig, basis, kind='ALL', name_filter='', preview=False):
    if rig.type != 'ARMATURE':
        raise ValueError('动画目标必须是骨架')
    data = read_library(path)
    clips = [c for c in data['clips'] if (kind == 'ALL' or c['kind'].upper() == kind)
             and name_filter.casefold() in c['name'].casefold()]
    if not clips:
        raise ValueError('没有符合筛选条件的动画')
    nodes = {n['path']: n for n in data['skeleton']}
    tracked = {t['path'] for clip in clips for t in clip['tracks']}
    required = set(tracked)
    for path in tracked:
        parent = nodes[path]['parent']
        while parent is not None:
            required.add(parent)
            parent = nodes[parent]['parent']
    ordered = [n for n in data['skeleton'] if n['path'] in required]
    by_path = {}
    for bone in rig.data.bones:
        if 'eiem_path' not in bone:
            continue
        source_path = str(bone['eiem_path'])
        if source_path in by_path:
            raise ValueError('目标骨架有重复的来源路径：' + source_path)
        by_path[source_path] = bone.name
    if not by_path:
        raise ValueError('目标骨架没有 EFF 来源路径，请先导入相应的 EFF 模型')
    inverse = basis.inverted()
    rest_world = {}
    rest_local = {}
    for node in ordered:
        local = _matrix(node['position'] + node['rotation'] + node['scale'])
        parent = node['parent']
        if node['path'] in by_path:
            bone = rig.data.bones[by_path[node['path']]]
            fields = [bone.get('eiem_local_' + f) for f in ('position', 'rotation', 'scale')]
            if any(v is None for v in fields):
                raise ValueError('目标骨架缺少来源参考姿态：' + node['path'])
            native_local = _matrix([v for field in fields for v in field])
            if node['path'] in tracked and max(abs(local[r][c] - native_local[r][c]) for r in range(4) for c in range(4)) > 0.0001:
                raise ValueError('动画面部参考姿态与目标不同：' + node['path'])
            # Facial animation is local. The model's body reference pose can differ
            # from the Unity Avatar's default body pose without changing the face mapping.
            local = native_local
        rest_local[node['path']] = local
        world = rest_world[parent] @ local if parent is not None else local
        rest_world[node['path']] = world
        if node['path'] in by_path:
            bone = rig.data.bones[by_path[node['path']]]
            actual_parent = str(bone.parent.get('eiem_path')) if bone.parent else None
            if actual_parent != parent:
                raise ValueError('目标骨架父子关系不同：' + node['path'])
            expected = basis @ world @ inverse
            if max(abs(expected[r][c] - bone.matrix_local[r][c]) for r in range(4) for c in range(4)) > 0.0001:
                raise ValueError('目标骨架参考姿态已修改或不属于此动画库：' + node['path'])
        elif node.get('deform', False):
            raise ValueError('目标缺少原始加权骨骼，请重新导入对应模型：' + node['path'])

    context = bpy.context
    active, selected = context.view_layer.objects.active, list(context.selected_objects)
    original_mode = context.object.mode if context.object else 'OBJECT'
    original_action = rig.animation_data.action if rig.animation_data else None
    original_slot = getattr(rig.animation_data, 'action_slot', None) if rig.animation_data else None
    old_pose = {b.name: (b.rotation_mode, b.matrix_basis.copy()) for b in rig.pose.bones}
    made, helpers, result = [], [], []
    try:
        if original_mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        missing = [n for n in ordered if n['path'] not in by_path]
        if missing:
            for obj in context.selected_objects:
                obj.select_set(False)
            rig.select_set(True)
            context.view_layer.objects.active = rig
            bpy.ops.object.mode_set(mode='EDIT')
            for node in missing:
                path = node['path']
                bone = rig.data.edit_bones.new(path.rsplit('/', 1)[-1] or 'root')
                if node['parent'] is not None:
                    bone.parent = rig.data.edit_bones[by_path[node['parent']]]
                bone.length = 0.01
                bone.matrix = basis @ rest_world[path] @ inverse
                bone.use_deform = False
                bone['eiem_path'] = path
                bone['eiem_skeleton_source'] = True
                bone['eiem_source_parent'] = node['parent'] or ''
                for field in ('position', 'rotation', 'scale'):
                    bone['eiem_local_' + field] = node[field]
                bone['eiem_animation_source_helper'] = True
                by_path[path] = bone.name
                helpers.append(bone.name)
            bpy.ops.object.mode_set(mode='OBJECT')
            for name in helpers:
                bone = rig.data.bones[name]
                bone['eiem_rest_display'] = [bone.matrix_local[r][c] for r in range(4) for c in range(4)]

        fps = context.scene.render.fps / context.scene.render.fps_base
        binding = [(p, by_path[p], list(rig.data.bones[by_path[p]].matrix_local)) for p in sorted(required)]
        binding_key = repr(binding)
        avatar_name = data['avatar'].removeprefix('data_facemorph_avatar_')
        for clip in clips:
            digest = hashlib.sha256((data['avatarIdentity'] + binding_key + str(fps) +
                                    json.dumps(clip, sort_keys=True, separators=(',', ':'))).encode('utf-8')).hexdigest()
            existing = next((a for a in bpy.data.actions if a.get('eiem_animation_source_hash') == digest), None)
            if existing:
                result.append(existing)
                continue
            suffix = clip['name'].removeprefix('data_facialmorph_').removeprefix(clip['kind'].lower() + '_')
            action = bpy.data.actions.new('EFF_' + avatar_name + '_' + clip['kind'] + '_' + suffix)
            made.append(action)
            action.use_fake_user = True
            action['eiem_animation_source_hash'] = digest
            action['eiem_animation_source'] = clip['name']
            action['eiem_animation_identity'] = clip.get('identity') or clip['name']
            action['eiem_animation_avatar'] = data['avatar']
            action['eiem_animation_kind'] = clip['kind']
            slot = None
            if hasattr(action, 'slots'):
                slot = action.slots.new(id_type='OBJECT', name=rig.name)
                layer = action.layers.new('Bone animation')
                curves = layer.strips.new(type='KEYFRAME').channelbag(slot, ensure=True).fcurves
            else:
                curves = action.fcurves
            tracks = {t['path']: t for t in clip['tracks']}
            # Constant tracks may have just their endpoints; interpolate their local TRS at shared times.
            samples = {p: [] for p in tracks}
            for time_index, time in enumerate(clip['times']):
                worlds = {}
                for node in ordered:
                    p = node['path']; parent = node['parent']
                    if p in tracks:
                        track = tracks[p]
                        if track.get('times') is None:
                            value = track['samples'][time_index]
                        else:
                            if len(track['samples']) > 2 or any(a != b for a, b in zip(track['samples'][0], track['samples'][-1])):
                                raise ValueError('简化轨道必须是恒定 TRS：' + p)
                            value = track['samples'][0]
                        local = _matrix(value)
                    else:
                        local = rest_local[p]
                    world = worlds[parent] @ local if parent is not None else local
                    worlds[p] = world
                    if p not in tracks:
                        continue
                    bone = rig.data.bones[by_path[p]]
                    kwargs = {'invert': True}
                    if parent is not None:
                        kwargs.update(parent_matrix=basis @ worlds[parent] @ inverse,
                                      parent_matrix_local=bone.parent.matrix_local)
                    local_basis = bone.convert_local_to_pose(basis @ world @ inverse, bone.matrix_local, **kwargs)
                    loc, rot, scale = local_basis.decompose()
                    if samples[p] and sum(samples[p][-1][1][i] * rot[i] for i in range(4)) < 0:
                        rot.negate()
                    samples[p].append((tuple(loc), tuple(rot), tuple(scale)))
            frames = [1 + t * fps for t in clip['times']]
            for path, values in samples.items():
                pb = rig.pose.bones[by_path[path]]
                for channel, field in enumerate(('location', 'rotation_quaternion', 'scale')):
                    for component in range(4 if channel == 1 else 3):
                        numbers = [v[channel][component] for v in values]
                        indices = [0, len(frames) - 1] if len(frames) > 2 and max(numbers) - min(numbers) <= 1e-8 else range(len(frames))
                        curve = curves.new(data_path=pb.path_from_id(field), index=component)
                        curve.keyframe_points.add(len(indices))
                        curve.keyframe_points.foreach_set('co', [v for i in indices for v in (frames[i], numbers[i])])
                        for key in curve.keyframe_points:
                            key.interpolation = 'CONSTANT' if clip['kind'] != 'Anim' else 'LINEAR'
                        curve.update()
            action.asset_mark()
            action.asset_data.description = data['avatar'] + ': ' + clip['name']
            result.append(action)
        for path in tracked:
            bone = rig.pose.bones[by_path[path]]
            matrix = bone.matrix_basis.copy()
            bone.rotation_mode = 'QUATERNION'
            bone.matrix_basis = matrix
        if preview:
            rig.animation_data_create()
            rig.animation_data.action = result[0]
            if hasattr(result[0], 'slots'):
                rig.animation_data.action_slot = result[0].slots[0]
            context.scene.frame_set(context.scene.frame_current, subframe=context.scene.frame_subframe)
        return {'created': len(made), 'reused': len(result) - len(made),
                'actions': [a.name for a in result], 'helpers': helpers, 'skipped': data.get('skipped', [])}
    except Exception:
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        if rig.animation_data:
            rig.animation_data.action = original_action
            if original_action and hasattr(rig.animation_data, 'action_slot'):
                rig.animation_data.action_slot = original_slot
        for action in made:
            bpy.data.actions.remove(action)
        if helpers:
            context.view_layer.objects.active = rig
            rig.select_set(True)
            bpy.ops.object.mode_set(mode='EDIT')
            for name in helpers:
                rig.data.edit_bones.remove(rig.data.edit_bones[name])
            bpy.ops.object.mode_set(mode='OBJECT')
        for name, (mode, matrix) in old_pose.items():
            rig.pose.bones[name].rotation_mode = mode
            rig.pose.bones[name].matrix_basis = matrix
        raise
    finally:
        for obj in context.selected_objects:
            obj.select_set(False)
        for obj in selected:
            obj.select_set(True)
        context.view_layer.objects.active = active
        if original_mode != 'OBJECT' and active:
            bpy.ops.object.mode_set(mode=original_mode)
