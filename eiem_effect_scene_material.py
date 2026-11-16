"""Reuse ordinary EFF Materials in particle Renderers, independent of scene ownership.

Only the selected Material and its actual texture slots are read. Game resources
stay logical references; source snapshots supply their exact native provenance.
No installed-game index or search by Blender object name participates.
"""
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import bpy

from . import eiem_effect_document as document
from . import eiem_effect_game_resources as game_resources
from . import eiem_effect_material as bridge
from . import eiem_effect_resources as resources
from . import eiem_native_export as native_export


IMPORT_KEY = "eff_material_import"


class Import:
    """Called only by the existing material import operations."""
    def __init__(self):
        self.sources = {}

    def available(self, package):
        """Legacy display-only packages remain importable without native data."""
        path = Path(package) / 'source/manifest.json'
        if not path.is_file():
            return False
        manifest = json.loads(path.read_bytes())
        return bool(manifest.get('serializedFiles') and manifest.get('resources'))

    def source(self, package):
        package = str(Path(package).resolve())
        if package not in self.sources:
            modules, dll = native_export.pack_modules()
            self.sources[package] = modules['author_source'].AuthorSource(
                package, modules['author_source'].AuthorCore(dll))
        return self.sources[package]

    def logical(self, source, cab, pid, kind):
        identity = cab.casefold() + ':' + str(pid)
        row = next((r for r in source.manifest['resources']
                    if r.get('identity', '').casefold() == identity and r.get('logicalPath')), None)
        path, asset = (row['logicalPath'], row['name']) if row else ('', '')
        if not path:
            # Shader assets are not necessarily in the display resource list.
            # Read the selected native file's real AssetBundle container map.
            file = next(r for r in source.manifest['serializedFiles'] if r['cab'].casefold() == cab.casefold())
            for obj in file['objects']:
                if obj['classId'] != 142:
                    continue
                tree = source.tree(dict(native=dict(cab=file['cab'], pathId=obj['pathId'])))
                for entry in tree['m_Container'].items():
                    ptr = entry['second']['asset']
                    if ptr['m_FileID'].data == 0 and ptr['m_PathID'].data == pid:
                        path = entry['first'].data
                        break
                if path:
                    break
        document.require(path, '来源未记录资源的逻辑路径：' + identity)
        return dict(path=path, asset=asset, type=kind, previewSource=str(source.package),
                    resourceIdentity=identity, resourceClassId=48 if kind == 'Shader' else 28,
                    **game_resources.source_proof(source.package, source.manifest))

    @staticmethod
    def pointer_source(source, owner_cab, pointer):
        fid, pid = pointer['m_FileID'], pointer['m_PathID']
        if not pid:
            return None
        file = next(r for r in source.manifest['serializedFiles'] if r['cab'].casefold() == owner_cab.casefold())
        cab = file['cab'] if not fid else file['externals'][fid - 1]['cab']
        return cab, pid

    def origin(self, material):
        path = str(material.get('eiem_source', '')).strip()
        package = material.get('eiem_author_package', '')
        document.require(game_resources.valid_logical_path(path),
                         '材质“' + material.name + '”尚未选择游戏 Shader 来源，请先在 EFF 材质面板指定源材质')
        document.require(package, '材质缺少本地来源记录：' + material.name)
        source = self.source(package)
        resource = source.resolve('Material', path, str(material.get('eiem_name', material.name)))
        tree = copy.deepcopy(source.tree(resource))
        return source, resource['native']['cab'], tree

    def capture(self, material):
        source, cab, tree = self.origin(material)
        data = document._native_plain(tree)
        bindings, profiles = [], {}
        for path, ptr in resources.pointers(data):
            if not ptr['m_PathID']:continue
            is_texture = path[:2] == ('m_SavedProperties','m_TexEnvs') and path[-1] == 'm_Texture'
            field = game_resources.field_path(path)
            origin = self.pointer_source(source, cab, ptr)
            document.require(origin, '材质资源缺少来源记录：' + field)
            logical = self.logical(source, *origin, 'Texture' if is_texture else 'Shader')
            bindings.append(dict(field=field, **logical))
            if is_texture:
                texture = source.tree(dict(native=dict(cab=origin[0],pathId=origin[1])))
                name = data['m_SavedProperties']['m_TexEnvs'][path[2]]['first']
                profiles[name] = dict(colorSpace=texture['m_ColorSpace'].data)
        record = dict(format='EFF_MATERIAL_IMPORT/1', source=material.get('eiem_source',''), value=data,
                      bindings=bindings, textures=profiles)
        record.update(game_resources.source_proof(source.package, source.manifest))
        remember_images(material, record)
        material[IMPORT_KEY] = document.dumps(record)
        for item in self.sources.values():item.assert_current()
        return record


def capture_from_editor(material, edit, identity=None):
    """Recover old scene import metadata from already loaded EFF data only."""
    source = str(material.get('eiem_source',''))
    asset = str(material.get('eiem_name',''))
    if identity is None:
        matches = [i for i,s in edit.objects.items() if s['classId']==21 and any(
            p['path'].casefold()==source.casefold() and (not asset or p.get('name')==asset)
            for p in s.get('logicalPaths',[]))]
        if len(matches)!=1:return None
        identity = matches[0]
    if identity not in edit.state['values']:return None
    bindings, profiles = [], {}
    for path, ptr, target, ref in resources.references(edit, identity):
        if not ptr['m_PathID'] and not ref.get('gameLogical'):continue
        spec = edit.objects.get(target,{})
        logical = copy.deepcopy(ref.get('gameLogical'))
        if not logical:
            logical = next((copy.deepcopy(r['gameLogical']) for s in edit.objects.values()
                            for r in s.get('references',[]) if r.get('gameLogical') and (
                                r.get('gameLogical',{}).get('resourceIdentity','').casefold()==str(target).casefold())),None)
        paths=spec.get('logicalPaths',[])
        if not logical and paths:
            chosen=paths[0]
            logical=dict(path=chosen['path'],asset=chosen.get('name',''),type=spec['type'],
                         previewSource=spec.get('gameSource') or edit.state['source'],
                         resourceIdentity=target,resourceClassId=spec['classId'])
            # Retain the actual source lease already declared by this workspace.
            proof=next((r.get('gameLogical') for s in edit.objects.values() for r in s.get('references',[])
                        if r.get('gameLogical',{}).get('previewSource')==logical['previewSource'] and r['gameLogical'].get('sourcePrefab')),None)
            if proof:logical.update({k:proof[k] for k in game_resources.PROOF_FIELDS if k in proof and k not in ('resourceIdentity','resourceClassId')})
        if not logical:return None
        bindings.append(dict(field=game_resources.field_path(path),**logical))
        if spec.get('classId')==28:
            row=next((r for r in edit.state['catalog'].get('preview',{}).get('resources',[]) if r['identity']==target),{})
            name=edit.value(identity)['m_SavedProperties']['m_TexEnvs'][path[2]]['first']
            if 'colorSpace' in row:profiles[name]=dict(colorSpace=row['colorSpace'])
    record=dict(format='EFF_MATERIAL_IMPORT/1',source=source,value=copy.deepcopy(edit.value(identity)),bindings=bindings,textures=profiles)
    from . import eiem_effect_material_origin as material_origin
    material_origin._retain_container(edit, record, identity)
    remember_images(material, record)
    material[IMPORT_KEY]=document.dumps(record)
    return record


class Materials:
    def __init__(self, native, result, snapshot):
        self.native, self.result, self.snapshot = native, result, snapshot
        self.selected = {}

    def export(self, material):
        key = material.as_pointer()
        if key in self.selected:return self.selected[key]
        record = json.loads(material.get(IMPORT_KEY,'null'))
        if not record or record.get('source') != material.get('eiem_source',''):
            author = material.eff_effect_author
            if author:
                from . import eiem_effect_editor as editor
                root = editor.root_of(author)
                donor = editor.synchronize(root) if root else self.native.edit
                identity = next((i for i in json.loads(author['eff_effect_components'])
                                 if donor.objects[i]['classId'] == 21), None)
                record = capture_from_editor(material, donor, identity)
            else:
                record = capture_from_editor(material, self.native.edit)
        document.require(record, '材质缺少已导入的游戏 Shader 数据，请通过现有 EFF 材质导入按钮导入：' + material.name)
        template = next(i for i,s in self.native.edit.objects.items()
                        if s['classId']==21 and not s.get('gameSource') and not s.get('template'))
        local, _ = self.native.tree(template)
        tree = self.native.codec.from_data(local.schema, record['value'])
        overrides = {k[5:]:str(material[k]) for k in material.keys()
                     if k.startswith(bridge.PREFIXES) or k=='eiem_render_queue'}
        native_export.material_fields(tree,overrides,self.native.codec.encode)
        data = document._native_plain(tree)
        refs = {r['field']:copy.deepcopy(r) for r in record['bindings']}
        for path,ptr in list(resources.pointers(data)):
            field=game_resources.field_path(path)
            is_texture=path[:2]==('m_SavedProperties','m_TexEnvs') and path[-1]=='m_Texture'
            name=data['m_SavedProperties']['m_TexEnvs'][path[2]]['first'] if is_texture else ''
            value=str(material.get('eiem_texture.'+name,'')).strip() if is_texture else ''
            slot=author_slot(material,name) if is_texture else None
            image=slot.image if slot else None
            if is_texture and value and not game_resources.valid_logical_path(value):
                filename=str(Path(bpy.path.abspath(value,library=material.library)).resolve())
                candidates=[n.image for n in material.node_tree.nodes if n.type=='TEX_IMAGE' and n.image] if material.node_tree else []
                candidates.extend(bpy.data.images)
                image=next((i for i in candidates if i.filepath and
                            str(Path(bpy.path.abspath(i.filepath,library=i.library)).resolve()).casefold()==filename.casefold()),None)
                if image is None:image=bpy.data.images.load(filename,check_existing=False)
            if is_texture and image and unchanged_image(image, record['textures'].get(name, {})):
                image = None  # An untouched imported preview is still a game resource.
            if is_texture and image:
                texture_template=next(i for i,s in self.native.edit.objects.items()
                                      if s['classId']==28 and not s.get('gameSource') and not s.get('template'))
                texture,_=self.native.tree(texture_template)
                texture=copy.deepcopy(texture)
                profile=record['textures'].get(name,{})
                texture.set('m_ColorSpace',profile.get('colorSpace',0 if image.colorspace_settings.name=='Non-Color' else 1))
                settings=slot or SimpleNamespace(mipmaps=str(image.get('eiem_mipmaps','true')).lower()=='true',
                                                filter=str(image.get('eiem_filter','1')),wrap=str(image.get('eiem_wrap','0')))
                from .eiem_effect_assets import texture_tree
                original_ref = refs.get(field, {})
                tid=self.native.add(texture_template,'scene-material/'+material.name_full+'/texture/'+name,
                                    texture_tree(texture,image,settings,self.snapshot),
                                    sourceTexture=dict(path=original_ref.get('path', ''), asset=original_ref.get('asset', '')))
                self.native.add_preview(tid,image,texture['m_ColorSpace'].data)
                parent=data
                for part in path[:-1]:parent=parent[part]
                parent[path[-1]]=self.native.pointer(template,tid)
                refs.pop(field,None)
                continue
            if is_texture and value and game_resources.valid_logical_path(value):
                inherited=refs.get(field)
                if not inherited or inherited['path'].casefold()!=value.casefold():
                    refs[field]=dict(field=field,path=value,asset='',type='Texture')
            if field in refs:
                parent=data
                for part in path[:-1]:parent=parent[part]
                parent[path[-1]]=dict(m_FileID=0,m_PathID=0)
            else:document.require(not ptr['m_PathID'],'材质资源缺少逻辑引用或作者资源：'+field)
        encoded=self.native.codec.from_data(local.schema,data)
        mid=self.native.add(template,'scene-material/'+material.name_full,encoded,
                            sourceMaterial=copy.deepcopy(record))
        self.result.setdefault('gameReferences',[]).extend(dict(owner=mid,**r) for r in refs.values())
        self.selected[key]=mid
        return mid


def author_slot(material, name):
    author = material.eff_effect_author
    return author.eff_effect_textures.get(name) if author else None


from .eiem_effect_material_origin import material_for_origin, migrate_origins


def image_path(image):
    return str(Path(bpy.path.abspath(image.filepath, library=image.library)).resolve()) if image.filepath else ''


def remember_images(material, record):
    """Remember imported game previews, without treating them as authored textures."""
    images = list(bpy.data.images)
    for name, profile in record['textures'].items():
        path = str(material.get('eiem_texture.' + name, ''))
        if not path or game_resources.valid_logical_path(path):
            continue
        path = str(Path(bpy.path.abspath(path, library=material.library)).resolve())
        image = next((i for i in images if image_path(i).casefold() == path.casefold()), None)
        field = '$.m_SavedProperties.m_TexEnvs[' + str(next(
            i for i,r in enumerate(record['value']['m_SavedProperties']['m_TexEnvs']) if r['first']==name)) + '].second.m_Texture'
        ref = next((r for r in record['bindings'] if r['field'] == field), {})
        if image and str(image.get('eiem_source', '')).casefold() == ref.get('path','').casefold() and not image.is_dirty:
            profile['importedFile'] = path
            profile['importedSha256'] = hashlib.sha256(Path(path).read_bytes()).hexdigest()


def unchanged_image(image, profile):
    path = image_path(image)
    return bool(not image.is_dirty and profile.get('importedSha256') and
                path.casefold() == profile.get('importedFile','').casefold() and
                Path(path).is_file() and hashlib.sha256(Path(path).read_bytes()).hexdigest() == profile['importedSha256'])
