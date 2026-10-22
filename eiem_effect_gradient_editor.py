"""Native ColorRamp widgets projected onto Unity's independent RGB/Alpha keys.

The effect document remains authoritative. Node groups only hold editable UI
projections; opening a widget never changes or resamples the source gradient.
"""
import copy
import hashlib
import importlib
import json
import math

import bpy
from bpy.props import StringProperty

TREE = 'eff_effect_gradient_tree'
RECORDS = 'eff_gradient_records'
_cache = {}


def api():
    return importlib.import_module((__package__ + '.' if __package__ else '') + 'eiem_effect_editor')


def metadata(tree):
    raw = tree.get(RECORDS, '{}')
    key = tree.as_pointer()
    if key not in _cache or _cache[key][0] != raw:
        _cache[key] = (raw, json.loads(raw))
    return _cache[key][1]


def remember(tree, records):
    raw = api().document.dumps(records)
    tree[RECORDS] = raw
    _cache[tree.as_pointer()] = (raw, records)


def tree_for(obj):
    tree = obj.get(TREE)
    return tree if isinstance(tree, bpy.types.ShaderNodeTree) and tree.get('eff_gradient_owner') == obj else None


def key_for(identity, path):
    return hashlib.sha256((identity + json.dumps(path)).encode()).hexdigest()[:24]


def geometry(node):
    ramp = node.color_ramp
    return [ramp.interpolation, ramp.color_mode,
            [[float(e.position), list(e.color)] for e in ramp.elements]]


def signature(obj):
    tree = tree_for(obj)
    return [(node.name, geometry(node)) for node in tree.nodes if node.type == 'VALTORGB'] if tree else None


def gradients(edit, identities):
    def visit(value, path):
        if isinstance(value, dict):
            if {'m_NumColorKeys', 'm_NumAlphaKeys', 'ctime0', 'atime0'} <= value.keys():
                yield path, value
            else:
                for name, child in value.items():
                    yield from visit(child, path + [name])
        elif isinstance(value, list):
            for i, child in enumerate(value):
                yield from visit(child, path + [i])
    for identity in identities:
        if identity in edit.state['values'] and edit.objects[identity]['classId'] == 198:
            for path, value in visit(edit.value(identity), []):
                yield identity, path, value


def project(tree, record, value):
    for channel, count, prefix in (('rgb', 'm_NumColorKeys', 'ctime'), ('alpha', 'm_NumAlphaKeys', 'atime')):
        node = tree.nodes.get(record[channel])
        if node is None:
            node = tree.nodes.new('ShaderNodeValToRGB')
            node.name = record[channel]
        ramp = node.color_ramp
        keys = sorted((value[prefix + str(i)] / 65535,
                       tuple(value['key' + str(i)][c] for c in 'rgb') + (1.0,) if channel == 'rgb'
                       else (1.0, 1.0, 1.0, value['key' + str(i)]['a']))
                      for i in range(value[count]))
        # Blender requires at least two elements. Keep 0/1-key sources intact
        # until the user actually changes the projection.
        if not keys: keys = [(0.0, (1.0,) * 4), (1.0, (1.0,) * 4)]
        elif len(keys) == 1: keys = [(0.0, keys[0][1]), (1.0, keys[0][1])]
        while len(ramp.elements) > 2: ramp.elements.remove(ramp.elements[-1])
        for element, (position, color) in zip(list(ramp.elements), keys[:2]):
            element.position = position
            element.color = color
        for position, color in keys[2:]: ramp.elements.new(position).color = color
        ramp.color_mode = 'RGB'
        ramp.interpolation = 'CONSTANT' if value['m_Mode'] == 1 else 'LINEAR'
    record['source'] = copy.deepcopy(value)
    record['view'] = [geometry(tree.nodes[record[c]]) for c in ('rgb', 'alpha')]


def prepare(obj, edit, identities):
    rows = list(gradients(edit, identities))
    tree = tree_for(obj)
    if not rows: return
    if tree is None:
        tree = bpy.data.node_groups.new('.EFF 渐变 · ' + obj.name, 'ShaderNodeTree')
        obj[TREE] = tree
        tree['eff_gradient_owner'] = obj
    records = metadata(tree)
    for identity, path, value in rows:
        key = key_for(identity, path)
        record = records.get(key)
        if record is None:
            record = dict(identity=identity, path=path, rgb=key + '_rgb', alpha=key + '_alpha')
            records[key] = record
            project(tree, record, value)
        elif value != record['source']:
            project(tree, record, value)
    remember(tree, records)


def decode(record, view):
    require = api().document.require
    require(record['source']['m_Mode'] in (0, 1), '此渐变插值模式尚不支持色带编辑，请使用精确数值')
    result = copy.deepcopy(record['source'])
    changed_modes = [v[0] for v, old in zip(view, record['view']) if v[0] != old[0]]
    require(len(set(changed_modes)) <= 1, '两条色带的插值需一致，请选择线性或固定')
    mode = changed_modes[0] if changed_modes else view[0][0]
    require(mode in ('LINEAR', 'CONSTANT'), '游戏渐变只支持 Linear（线性）或 Constant（固定）')
    result['m_Mode'] = int(mode == 'CONSTANT')
    for index, (count, prefix, components) in enumerate((('m_NumColorKeys', 'ctime', 'rgb'), ('m_NumAlphaKeys', 'atime', 'a'))):
        shape = view[index]
        require(shape[1] == 'RGB', '游戏渐变使用 RGB 插值，请将色带颜色模式设为 RGB')
        require(2 <= len(shape[2]) <= 8, '每条色带最多 8 个标记；请用减号删除多余标记')
        # Editing RGB must not resample Alpha, nor expand a 0/1-key source.
        if shape[2] == record['view'][index][2]: continue
        result[count] = len(shape[2])
        for i, (position, rgba) in enumerate(shape[2]):
            require(all(math.isfinite(v) for v in [position, *rgba]), '渐变需要有限数值')
            result[prefix + str(i)] = int(round(position * 65535))
            for component in components:
                result['key' + str(i)][component] = float(rgba['rgba'.index(component)])
    return result


def push_fields(obj, edit, identity, path):
    editor = api()
    for row in editor.field_rows(obj):
        if row['identity'] != identity or row['path'][:len(path)] != path: continue
        value = edit.value(identity, row['path'])
        if row['kind'] == 'color':
            obj[row['key']] = [float(value[c]) for c in 'rgba']
            row['_last'] = tuple(value[c] for c in 'rgba')
        elif row['kind'] == 'gradient_time':
            obj[row['key']] = value / 65535
            row['_last'] = value / 65535
        else: obj[row['key']] = str(value) if row['wide'] else value


def synchronize(root, edit):
    updates = []
    refreshes = []
    for obj in api().objects(root):
        tree = tree_for(obj)
        if tree is None: continue
        for record in metadata(tree).values():
            if record['identity'] not in edit.state['values']: continue
            value = edit.value(record['identity'], record['path'])
            if value != record['source']:
                refreshes.append((tree, record, value))
                continue
            view = [geometry(tree.nodes[record[c]]) for c in ('rgb', 'alpha')]
            if view != record['view']:
                updates.append((obj, tree, record, view, decode(record, view)))
    # Decode/validate every pending ramp before committing any author data.
    dirty = set()
    for tree, record, value in refreshes:
        project(tree, record, value)
        dirty.add(tree)
    for obj, tree, record, view, value in updates:
        edit.value(record['identity'], record['path'][:-1])[record['path'][-1]] = value
        push_fields(obj, edit, record['identity'], record['path'])
        # Keep selection and dragging uninterrupted; do not rebuild elements.
        for channel in ('rgb', 'alpha'):
            ramp = tree.nodes[record[channel]].color_ramp
            ramp.interpolation = 'CONSTANT' if value['m_Mode'] == 1 else 'LINEAR'
            for element in ramp.elements:
                if channel == 'rgb': element.alpha = 1.0
                else: element.color = (1.0, 1.0, 1.0, element.alpha)
        record['source'] = copy.deepcopy(value)
        record['view'] = [geometry(tree.nodes[record[c]]) for c in ('rgb', 'alpha')]
        dirty.add(tree)
    for tree in dirty: remember(tree, metadata(tree))
    return bool(updates)


def draw(layout, draw, path, title):
    tree = tree_for(draw.obj)
    record = metadata(tree).get(key_for(draw.identity, path)) if tree else None
    layout.label(text=title + ' · 拖动标记调整位置')
    if record:
        body = layout.column()
        body.enabled = draw.edit.value(draw.identity, path)['m_Mode'] in (0, 1)
        for channel, label in (('rgb', '颜色'), ('alpha', '透明度 Alpha（0 透明 / 1 可见）')):
            body.label(text=label)
            body.template_color_ramp(tree.nodes[record[channel]], 'color_ramp', expand=True)
        try: decode(record, [geometry(tree.nodes[record[c]]) for c in ('rgb', 'alpha')])
        except ValueError as error:
            api().effect_ui.hint(layout, str(error), icon='ERROR')
            op = layout.operator('eiem.effect_gradient_reset', text='恢复当前参数的色带', icon='FILE_REFRESH')
            op.object_name = draw.obj.name
            op.identity = draw.identity
            op.path = json.dumps(path)
    else:
        layout.label(text='色带准备中；可先使用精确数值', icon='INFO')
    api().effect_ui.hint(layout, '选中标记后点色块改颜色 / Alpha；＋添加、－删除，每条最多 8 个。插值使用 Linear 或 Constant。')


class EFF_OT_effect_gradient_reset(bpy.types.Operator):
    bl_idname = 'eiem.effect_gradient_reset'
    bl_label = '恢复当前参数的色带'
    bl_options = {'REGISTER', 'UNDO'}
    object_name: StringProperty()
    identity: StringProperty()
    path: StringProperty()

    def execute(self, context):
        obj = bpy.data.objects[self.object_name]
        tree = tree_for(obj)
        path = json.loads(self.path)
        edit = api().editor(api().root_of(obj))
        records = metadata(tree)
        project(tree, records[key_for(self.identity, path)], edit.value(self.identity, path))
        remember(tree, records)
        return {'FINISHED'}


def clear_cache(): _cache.clear()


def register(): bpy.utils.register_class(EFF_OT_effect_gradient_reset)


def unregister():
    clear_cache()
    bpy.utils.unregister_class(EFF_OT_effect_gradient_reset)
