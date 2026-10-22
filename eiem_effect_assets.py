"""Authored particle meshes and texture pixels, cloned into source-native assets.

The preserved effect is never changed. Each binding gets its own native clone;
the compiler validates the clone against the source schema before publishing.
"""
import base64
import copy
import hashlib
import json
import re
from pathlib import Path

import bpy
from mathutils import Matrix, Vector

if __package__:
    from . import eiem_effect_document as document
else:
    import eiem_effect_document as document

BASIS = Matrix(((-1, 0, 0), (0, 0, -1), (0, 1, 0)))


def material_object(root, identity):
    return next((o for o in root.children_recursive
                 if o.get('eff_effect_resource') and
                 identity in json.loads(o.get('eff_effect_components', '[]'))), None)


def source_meshes(root):
    return {o.get('eff_effect_mesh_source'): o for o in root.children_recursive
            if o.type == 'MESH' and o.get('eff_effect_mesh_source') and not o.get('eff_effect_mesh_variant')}


def material_identity(root, material):
    author = material.eff_effect_author if material else None
    if author and author.parent == root:
        return next(iter(json.loads(author.get('eff_effect_components', '[]'))), None)
    return None


def renderer_material(root, edit, renderer, index=0):
    """Mesh slots are the authoring UI; Unity still binds on the Renderer."""
    from . import eiem_effect_preview as preview
    node = next((o for o in [root] + list(root.children_recursive)
                 if renderer in json.loads(o.get('eff_effect_components', '[]'))), None)
    mesh = node.eff_effect.particle_mesh if node and edit.value(renderer).get('m_RenderMode') == 4 else None
    if mesh and mesh.data.materials and index == 0:
        # The current particle writer accepts one used face material. Joining
        # geometry may leave unused slots, so follow the faces rather than slot 0.
        used = {p.material_index for p in mesh.data.polygons}
        slot = next(iter(used)) if len(used) == 1 else index
        mat = mesh.material_slots[slot].material if slot < len(mesh.material_slots) else None
        identity = material_identity(root, mat)
        if identity:
            return identity
    return preview._target(edit, renderer, ['m_Materials', index])


def expand_meshes(context, root, edit):
    """Create the editable source assets once, preserving existing user meshes."""
    if root.get('eff_effect_assets_version', 0) >= 1:
        return
    from . import eiem_effect_preview as preview
    resources = preview._resources(root, edit)
    collection = bpy.data.collections.get(root.get('eff_effect_assets_collection', ''))
    if not collection:
        collection = bpy.data.collections.new('特效资源 · ' + root.name)
        preview._collection(root).children.link(collection)
        root['eff_effect_assets_collection'] = collection.name
    meshes = source_meshes(root)
    for identity, resource in resources.items():
        if resource.get('kind') != 'Mesh' or identity in meshes:
            continue
        data = json.loads((Path(edit.state['source']) / resource['file']).read_text('utf-8-sig'))
        name = resource.get('name') or 'unknown Mesh'
        mesh = bpy.data.meshes.new(name)
        vertices = data['vertices']
        faces = [tuple(reversed(data['indices'][i:i + 3])) for i in range(0, len(data['indices']), 3)]
        mesh.from_pydata([BASIS @ Vector(v) for v in vertices], [], faces)
        mesh.update()
        raw = data.get('uv') or []
        stride = len(raw) // len(vertices) if vertices else 0
        if stride >= 2:
            layer = mesh.uv_layers.new(name='UV0')
            for loop in mesh.loops:
                offset = loop.vertex_index * stride
                layer.data[loop.index].uv = raw[offset:offset + 2]
        normals = data.get('normals') or []
        if len(normals) == len(vertices) * 3:
            mesh.normals_split_custom_set_from_vertices([BASIS @ Vector(normals[i:i + 3]) for i in range(0, len(normals), 3)])
        color = mesh.color_attributes.new(name='EFFColor', type='FLOAT_COLOR', domain='POINT')
        color.data.foreach_set('color', [1.] * (len(vertices) * 4))
        obj = bpy.data.objects.new(name, mesh)
        collection.objects.link(obj)
        obj.parent = root
        obj.hide_render = True
        obj['eff_particle_template'] = True
        obj['eff_effect_mesh_source'] = identity
        meshes[identity] = obj
        # Importing and exporting without edits must retain the native source.
        if mesh.uv_layers.active and mesh.polygons:
            obj['eff_effect_mesh_baseline'] = snapshot_digest(mesh_snapshot(obj))
        obj.hide_set(True)
    bindings = {}
    for node in [root] + list(root.children_recursive):
        ids = json.loads(node.get('eff_effect_components', '[]'))
        renderer = next((i for i in ids if edit.objects[i]['classId'] == 199), None)
        if not renderer or edit.value(renderer).get('m_RenderMode') != 4:
            continue
        identity = preview._target(edit, renderer, ['m_Mesh'])
        mesh = meshes.get(identity)
        if not mesh:
            continue
        mids = tuple(preview._target(edit, renderer, ['m_Materials', i])
                     for i in range(len(edit.value(renderer).get('m_Materials', []))))
        key = (identity, mids)
        if key not in bindings:
            if any(k[0] == identity for k in bindings):
                variant = mesh.copy(); variant.data = mesh.data.copy()
                collection.objects.link(variant)
                variant['eff_effect_mesh_source'] = identity
                variant['eff_effect_mesh_variant'] = True
                variant.hide_set(True)
                mesh = variant
            mesh.data.materials.clear()
            for mid in mids:
                if mid in edit.state['values']:
                    mesh.data.materials.append(preview._material_asset(root, edit, mid, resources))
            bindings[key] = mesh
        if not node.eff_effect.particle_mesh:
            node.eff_effect.particle_mesh = bindings[key]
    root['eff_effect_assets_version'] = 1


def snapshot_digest(snapshot):
    return hashlib.sha256(repr(snapshot).encode('utf-8')).hexdigest()


def texture_slot(root, identity, prop):
    obj = material_object(root, identity)
    return obj.eff_effect_textures.get(prop) if obj else None


def setup_textures(root, edit, obj, identity):
    """Do not reset saved author selections when upgrading an old .blend."""
    from . import eiem_effect_preview as preview
    resources = preview._resources(root, edit)
    native = None
    for index, row in enumerate(edit.value(identity).get('m_SavedProperties', {}).get('m_TexEnvs', [])):
        if obj.eff_effect_textures.get(row['first']):
            continue
        slot = obj.eff_effect_textures.add()
        slot.name = row['first']
        tid = preview._target(edit, identity, ['m_SavedProperties', 'm_TexEnvs', index, 'second', 'm_Texture'])
        resource = resources.get(tid)
        if resource:
            slot.source_name = resource.get('name', tid)
        if tid and edit.objects[tid]['classId'] == 28:
            if native is None:native = NativeAssets(edit, root['eff_effect_id'])
            tree, _ = native.tree(tid)
            slot.linear = tree['m_ColorSpace'].data == 0
            slot.mipmaps = tree['m_MipCount'].data > 1
            settings = tree['m_TextureSettings']
            slot.wrap = str(settings['m_WrapU'].data)
            slot.filter = str(settings['m_FilterMode'].data)
        # Loading every image on import is unnecessary; the preview loads the
        # main/mask images. An empty replacement means retain the source.


def texture_signature(root, identity):
    obj = material_object(root, identity)
    return tuple((s.name, s.image.as_pointer() if s.image else 0,
                  bool(s.image and s.image.is_dirty), tuple(s.image.size) if s.image else (),
                  s.linear, s.mipmaps, s.wrap, s.filter)
                 for s in obj.eff_effect_textures) if obj else ()


def image_for_slot(root, edit, identity, index, resources):
    from . import eiem_effect_preview as preview
    from . import eiem_effect_material as material_bridge
    row = edit.value(identity)['m_SavedProperties']['m_TexEnvs'][index]
    authored = material_bridge.image(root, identity, row['first'])
    if authored:return authored
    replacement = texture_slot(root, identity, row['first'])
    if replacement and replacement.image:
        return replacement.image
    target = preview._target(edit, identity, ['m_SavedProperties', 'm_TexEnvs', index, 'second', 'm_Texture'])
    resource = resources.get(target)
    return bpy.data.images.load(str(Path(edit.state['source']) / resource['file']), check_existing=True) if resource else None


def evaluated_mesh(obj, depsgraph):
    if obj.mode == 'EDIT':
        obj.update_from_editmode()
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
    document.require(mesh and len(mesh.polygons), '指定的粒子 Mesh 没有面')
    return evaluated, mesh


def preview_mesh(obj):
    evaluated, mesh = evaluated_mesh(obj, bpy.context.evaluated_depsgraph_get())
    try:
        mesh.calc_loop_triangles()
        document.require(mesh.uv_layers.active is not None, '粒子 Mesh 需要 UV 贴图坐标')
        transform = BASIS.inverted() @ obj.matrix_basis.to_3x3()
        vertices, faces, uv = [], [], []
        for triangle in mesh.loop_triangles:
            start = len(vertices)
            for loop in triangle.loops:
                vertices.append(transform @ mesh.vertices[mesh.loops[loop].vertex_index].co)
                uv.append(tuple(mesh.uv_layers.active.data[loop].uv))
            # BASIS reflects handedness. Native export uses the same reversal.
            faces.append((start, start + 2, start + 1))
        return vertices, faces, uv, True
    finally:
        evaluated.to_mesh_clear()


def mesh_snapshot(obj):
    from . import eiem_blender_addon as addon
    evaluated, mesh = evaluated_mesh(obj, bpy.context.evaluated_depsgraph_get())
    temporary = None
    try:
        document.require(mesh.uv_layers.active is not None, '粒子 Mesh 需要 UV 贴图坐标')
        used = {p.material_index for p in mesh.polygons}
        document.require(len(used) == 1, '当前每个自定义粒子 Mesh 支持一个材质；请将面指定到同一材质槽')
        baked = mesh.copy()
        for semantic,kind in (('tangent','FLOAT_VECTOR'),('tangent_sign','FLOAT')):
            name=addon.resolve_author_attribute(baked,'point',semantic,kind)
            if name and baked.attributes.get(name):baked.attributes.remove(baked.attributes[name])
        if 'eiem_normal_baseline_crc' in baked:del baked['eiem_normal_baseline_crc']
        # A Blender-authored UVMap is a valid UV0; normalize only this temporary
        # snapshot so the shared writer can calculate matching tangent frames.
        active=baked.uv_layers.active
        if active.name!='UV0':
            if baked.uv_layers.get('UV0'):baked.uv_layers['UV0'].name='__EFF_previous_UV0'
            active.name='UV0'
        for key in ('author_uv_slots_json','eff_uv_slots_json'):
            if key in baked:del baked[key]
        # The ordinary Mesh writer handles X reflection. Bake the remaining
        # Y-up conversion and the author's rotation/scale, excluding placement.
        baked.transform((Matrix(((1, 0, 0), (0, 0, 1), (0, -1, 0))) @ obj.matrix_basis.to_3x3()).to_4x4())
        for polygon in baked.polygons:
            polygon.material_index = 0
        baked['eiem_coordinate_space'] = 'unity-y-up-left-handed'
        baked.update()
        temporary = bpy.data.objects.new('__EFF_particle_export', baked)
        result = addon.write_mesh(None, temporary, return_snapshot=True)
        document.require(not result['skin'] and not result['blend_frames'], '粒子网格按当前静态形状导出')
        return result
    finally:
        evaluated.to_mesh_clear()
        if temporary:
            data = temporary.data
            bpy.data.objects.remove(temporary)
            bpy.data.meshes.remove(data)


class NativeAssets:
    def __init__(self, edit, key):
        from .nativepack import type_tree
        self.codec, self.edit, self.key = type_tree, edit, key
        self.source = Path(edit.state['source'])
        self.manifest = json.loads((self.source / 'source/manifest.json').read_bytes())
        self.receipt = json.loads((self.source / 'source/generated-files.json').read_bytes())['files']
        self.files, self.rows, self.additions = {}, {}, []

    def tree(self, identity):
        spec = self.edit.objects[identity]
        if spec.get('template'):spec = self.edit.objects[spec['template']]
        cab = spec['native']['cab']
        if cab not in self.files:
            row = next(r for r in self.manifest['serializedFiles'] if r['cab'].lower() == cab.lower())
            raw = document.safe_path(self.source, row['snapshot']['file']).read_bytes()
            document.require(document.digest(raw) == row['snapshot']['sha256'].upper(), '原生底稿摘要不匹配')
            self.files[cab], self.rows[cab] = raw, row
        schema_raw = document.safe_path(self.source, spec['schema']).read_bytes()
        document.require(document.digest(schema_raw) == self.receipt[spec['schema']].upper(), '资源结构摘要不匹配')
        schema = self.codec.Schema.compile(json.loads(schema_raw)['nodes'])
        row = next(r for r in self.rows[cab]['objects'] if r['pathId'] == spec['native']['pathId'])
        raw = self.files[cab][row['offset']:row['offset'] + row['size']]
        tree = self.codec.decode(schema, raw)
        document.require(self.codec.encode(tree) == raw, '原生资源未修改往返失败')
        return tree, raw

    def add(self, template, label, tree):
        spec = self.edit.objects[template]
        cab = spec['native']['cab']
        pid = int.from_bytes(hashlib.sha256((self.key + ':' + label).encode()).digest()[:8], 'little', signed=True)
        document.require(pid != 0 and not any(r['pathId'] == pid for r in self.rows[cab]['objects']), '新增资源身份冲突')
        _, original = self.tree(template)
        identity = cab.lower() + ':' + str(pid)
        document.require(not any(a['identity'] == identity for a in self.additions), '新增资源重复')
        self.additions.append(dict(identity=identity, template=template,
                                   sourceObjectSha256=document.digest(original),
                                   payloadBase64=base64.b64encode(self.codec.encode(tree)).decode('ascii')))
        return identity

    def pointer(self, owner, target):
        owner_cab = self.edit.objects[owner]['native']['cab'].lower()
        cab, pid = target.rsplit(':', 1)
        if owner_cab == cab.lower():
            fid = 0
        else:
            ids = {r['fileId'] for o in self.edit.objects.values()
                   if o['native']['cab'].lower() == owner_cab for r in o['references']
                   if (r.get('cab') or '').lower() == cab.lower() and r['fileId'] > 0}
            document.require(len(ids) == 1, '资源不在该材质/渲染器的来源依赖中')
            fid = ids.pop()
        return dict(m_FileID=fid, m_PathID=int(pid))


def texture_tree(native, image, settings):
    import sys
    vendor=Path(__file__).parent/'vendor'
    if vendor.is_dir() and str(vendor) not in sys.path:sys.path.insert(0,str(vendor))
    from PIL import Image
    import numpy as np
    width, height = map(int, image.size)
    document.require(0 < width <= 16384 and 0 < height <= 16384, '贴图尺寸必须为 1～16384')
    document.require(image.source in ('FILE', 'GENERATED'), '请选择普通二维图片；序列、影片和 UDIM 尚不支持')
    # Blender byte-image pixels retain their encoded values; float buffers are
    # linear. Only float color buffers need sRGB encoding. Both are bottom-up,
    # and alpha is never gamma transformed. Read live painted/packed pixels.
    pixels = np.empty(width * height * 4, dtype=np.float32)
    image.pixels.foreach_get(pixels)
    pixels = pixels.reshape(height, width, 4)
    document.require(np.all(np.isfinite(pixels)), '贴图包含非有限像素')
    if image.is_float and not settings.linear:
        rgb = pixels[:, :, :3]
        pixels[:, :, :3] = np.where(rgb <= .0031308, rgb * 12.92, 1.055 * np.maximum(rgb, 0) ** (1 / 2.4) - .055)
    bitmap = Image.fromarray(np.rint(np.clip(pixels, 0, 1) * 255).astype(np.uint8), 'RGBA')
    payload, count = bytearray(), 0
    while True:
        payload.extend(bitmap.tobytes()); count += 1
        if not settings.mipmaps or bitmap.size == (1, 1):
            break
        bitmap = bitmap.resize((max(1, bitmap.width // 2), max(1, bitmap.height // 2)), Image.Resampling.BOX)
    tree = copy.deepcopy(native)
    document.require(tree['m_ImageCount'].data == 1 and tree['m_TextureDimension'].data == 2,
                     '来源必须是二维 Texture2D')
    for field, value in dict(m_Name=image.name, m_Width=width, m_Height=height,
                             m_CompleteImageSize=len(payload), m_MipsStripped=0,
                             m_TextureFormat=4, m_MipCount=count, m_IsPreProcessed=False,
                             m_StreamingMipmaps=False, m_ColorSpace=0 if settings.linear else 1).items():
        tree.set(field, value)
    tree.set('image data', bytes(payload))
    tree['m_PlatformBlob'].set_items(b'')
    for field, value in dict(offset=0, size=0, path='').items():
        tree['m_StreamData'].set(field, value)
    for field, value in dict(m_FilterMode=int(settings.filter), m_WrapU=int(settings.wrap),
                             m_WrapV=int(settings.wrap), m_WrapW=int(settings.wrap)).items():
        tree['m_TextureSettings'].set(field, value)
    return tree


def export_document(root, edit):
    """Snapshot user assets at build/export time; preview remains lightweight."""
    from . import eiem_effect_preview as preview
    from .nativepack import mesh as mesh_codec
    result = edit.export()
    result['lodCoverage'] = 'all' if root.eff_effect.all_lods else 'source'
    native = NativeAssets(edit, root['eff_effect_id'])
    render_edits = {}
    material_clones = {}
    mesh_clones = {}
    snapshots = {}
    imported_meshes = source_meshes(root)
    def mesh_target(template, selected):
        key = selected.as_pointer()
        if key not in snapshots:
            snapshot = mesh_snapshot(selected)
            snapshots[key] = snapshot, snapshot_digest(snapshot)
        snapshot, signature = snapshots[key]
        if selected.get('eff_effect_mesh_source') == template and selected.get('eff_effect_mesh_baseline') == signature:
            return None
        key = (template, signature)
        if key not in mesh_clones:
            tree, _ = native.tree(template)
            tree, _ = mesh_codec.native_mesh(tree, snapshot, selected.name)
            mesh_clones[key] = native.add(template, template + '/mesh/' + signature, tree)
        return mesh_clones[key]
    for node in [root] + list(root.children_recursive):
        if node.get('eff_preview'):
            continue
        ids = json.loads(node.get('eff_effect_components', '[]'))
        renderer = next((i for i in ids if edit.objects[i]['classId'] == 199), None)
        if not renderer:
            continue
        current = copy.deepcopy(edit.value(renderer))
        custom = node.eff_effect.particle_mesh if current.get('m_RenderMode') == 4 else None
        if custom and custom.data.polygons and custom.data.materials:
            used = {p.material_index for p in custom.data.polygons}
            document.require(len(used) == 1, '当前每个粒子 Mesh 支持一个面材质；合并后请把所有面指定到需要的 EFF 材质')
            index = next(iter(used))
            mat = custom.material_slots[index].material if index < len(custom.material_slots) else None
            document.require(material_identity(root, mat), '粒子 Mesh 的面材质必须是当前特效导入的 EFF 材质；Blender 普通节点 Shader 不能直接导出到游戏')
        for field in ('m_Mesh', 'm_Mesh1', 'm_Mesh2', 'm_Mesh3'):
            if field not in current or current.get('m_RenderMode') != 4:
                continue
            template = preview._target(edit, renderer, [field])
            selected = custom if field == 'm_Mesh' else imported_meshes.get(template)
            if not selected:
                continue
            if not template:
                template = next((i for i, o in edit.objects.items() if o['classId'] == 43
                                 and any((r.get('cab') or '').lower() == o['native']['cab'].lower()
                                         for r in edit.objects[renderer]['references'])), None)
            document.require(template, '该来源没有可供粒子网格使用的 Mesh 结构模板')
            target = mesh_target(template, selected)
            if target:current[field] = native.pointer(renderer, target)
        for index in range(len(current.get('m_Materials', []))):
            mid = renderer_material(root, edit, renderer, index)
            if mid:
                current['m_Materials'][index] = native.pointer(renderer, mid)
            material_obj = material_object(root, mid)
            from . import eiem_effect_material as material_bridge
            slots = [(s, material_bridge.image(root, mid, s.name) or s.image)
                     for s in material_obj.eff_effect_textures] if material_obj else []
            slots = [(s, image) for s, image in slots if image]
            if not slots:
                continue
            if mid not in material_clones:
                tree, _ = native.tree(mid)
                data = copy.deepcopy(edit.value(mid))
                for ti, row in enumerate(data.get('m_SavedProperties', {}).get('m_TexEnvs', [])):
                    selected = next(((s, image) for s, image in slots if s.name == row['first']), None)
                    if not selected:
                        continue
                    slot, image = selected
                    tid = preview._target(edit, mid, ['m_SavedProperties', 'm_TexEnvs', ti, 'second', 'm_Texture'])
                    if not tid:
                        tid = next((preview._target(edit, mid, ['m_SavedProperties', 'm_TexEnvs', j, 'second', 'm_Texture'])
                                    for j in range(len(data['m_SavedProperties']['m_TexEnvs']))
                                    if preview._target(edit, mid, ['m_SavedProperties', 'm_TexEnvs', j, 'second', 'm_Texture'])), None)
                    document.require(tid and edit.objects[tid]['classId'] == 28, '此贴图槽缺少二维纹理结构模板')
                    texture, _ = native.tree(tid)
                    target = native.add(tid, mid + '/texture/' + slot.name, texture_tree(texture, image, slot))
                    row['second']['m_Texture'] = native.pointer(mid, target)
                # Material JSON has no byte blobs. from_data accepts the same
                # flattened vector/map representation as effect author JSON.
                tree = native.codec.from_data(tree.schema, data)
                material_clones[mid] = native.add(mid, mid + '/material', tree)
            current['m_Materials'][index] = native.pointer(renderer, material_clones[mid])
        if current != edit.value(renderer):
            render_edits[renderer] = current
    # A source Mesh can also be used by a mesh-shaped emitter or a component.
    # Keep those real references connected to the same edited resource.
    for identity, spec in edit.objects.items():
        if spec['classId'] == 199 or identity not in edit.state['values']:
            continue
        current = None
        for ref in spec.get('references', []):
            if ref.get('type') not in ('Mesh', None):
                continue
            path = [int(index) if index else field for field, index in re.findall(r'\.([^\.\[\]]+)|\[(\d+)\]', ref['field'])]
            template = preview._target(edit, identity, path)
            selected = imported_meshes.get(template)
            if not selected:
                continue
            target = mesh_target(template, selected)
            if not target:
                continue
            if current is None:current = copy.deepcopy(edit.value(identity))
            parent = current
            for key in path[:-1]:parent = parent[key]
            parent[path[-1]] = native.pointer(identity, target)
        if current is not None:render_edits[identity] = current
    for identity, data in render_edits.items():
        if edit.objects[identity].get('template'):
            added = next(a for a in result['emitters']['added'] if a['identity'] == identity)
            added['data'] = data
            continue
        result['objects'] = [o for o in result['objects'] if o['identity'] != identity]
        result['objects'].append(dict(identity=identity, sourceDataSha256=edit.state['hashes'][identity], data=data))
    if native.additions:
        result['assets'] = native.additions
    return result


def write_document(root, edit, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(document.dumps(export_document(root, edit)), encoding='utf-8')
    temporary.replace(path)
