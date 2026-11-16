"""Effect empty objects, typed inline blocks; live reload belongs to eff.dll."""
import copy
import hashlib
import json
import math
import subprocess
import tempfile
import time
import uuid
import sys
from functools import lru_cache
from dataclasses import dataclass
from pathlib import Path
import bpy
from bpy.props import BoolProperty, EnumProperty, PointerProperty, StringProperty, IntProperty, FloatProperty, CollectionProperty
from bpy_extras.io_utils import ImportHelper, ExportHelper
from mathutils import Matrix, Quaternion, Vector
if __package__:
    from . import eiem_blender_controls as switch_controls
    from . import eiem_effect_document as document
    from . import eiem_effect_preview as preview
    from . import eiem_effect_ui as effect_ui
    from . import eiem_effect_assets as assets
    from . import eiem_effect_material as material_bridge
    from . import eiem_effect_topology as topology
    from . import eiem_effect_game_resources as game_resources
    from . import eiem_effect_curve_editor as curve_editor
    from . import eiem_effect_gradient_editor as gradient_editor
    from . import eiem_effect_publish as effect_publish
    from . import eiem_effect_clipboard as block_clipboard
    from . import eiem_effect_mount_targets as mount_targets
else:
    import eiem_blender_controls as switch_controls
    import eiem_effect_document as document
    import eiem_effect_preview as preview
    import eiem_effect_ui as effect_ui
    import eiem_effect_assets as assets
    import eiem_effect_material as material_bridge
    import eiem_effect_topology as topology
    import eiem_effect_game_resources as game_resources
    import eiem_effect_curve_editor as curve_editor
    import eiem_effect_gradient_editor as gradient_editor
    import eiem_effect_publish as effect_publish
    import eiem_effect_clipboard as block_clipboard
    import eiem_effect_mount_targets as mount_targets
_CACHE={}
_BUILD_RUNNING=False
_EXPORT_JOB=None
_ENUM_CACHE={}
_FIELD_ROWS={}
_MESH_PICK_PENDING=set()
BASIS=Matrix(((-1,0,0,0),(0,0,-1,0),(0,1,0,0),(0,0,0,1)))

def root_of(obj):
    while obj:
        # Membership avoids copying a multi-megabyte document on every parent
        # visit (panel drawing, depsgraph invalidation and material ownership).
        if 'eff_effect_document' in obj:return obj
        obj=obj.parent
    return None
def active(context):
    root=root_of(context.object)
    material=getattr(context,'material',None)
    return root or (root_of(material.eff_effect_author) if material and material.eff_effect_author else None)

MESH_ATTACHMENT='MESH_ATTACHMENT'
REPLACE_GAME_PREFAB='REPLACE_GAME_PREFAB'

def validate_replacement_prefab_path(value):
    path=str(value or '').strip()
    pieces=path.split('/')
    if (not path.startswith('assets/') or not path.endswith('.prefab') or
            '\\' in path or any(piece in ('','.','..') for piece in pieces)):
        raise ValueError('目标游戏 Prefab 必须是规范的 assets/.../*.prefab 逻辑路径')
    return path

def effect_usage_updated(cfg,context):
    if cfg.usage!=REPLACE_GAME_PREFAB or cfg.replacement_prefab.strip():return
    root=cfg.id_data
    try:
        state=json.loads(root.get('eff_effect_document','{}'))
        path=validate_replacement_prefab_path(state.get('catalog',{}).get('prefab',''))
        cfg.replacement_prefab=path
    except (TypeError,ValueError):
        pass

def require_publishable_effect_usage(root):
    cfg=root.eff_effect
    if cfg.usage==REPLACE_GAME_PREFAB:
        validate_replacement_prefab_path(cfg.replacement_prefab)

def _selected_effect_root(obj):
    if isinstance(obj,EffectSelection):return obj.root
    # Resource Meshes and preview geometry are not effect export selections.
    if getattr(obj,'type',None)!='EMPTY':return None
    root=root_of(obj)
    if obj==root:return root
    if root and (obj.get('eff_effect_node') or obj.get('eff_effect_identity')):return root
    return None

def export_roots(selected=(),meshes=(),addon=None):
    """Project explicit selections only; a mount never selects its effect."""
    roots={_selected_effect_root(obj) for obj in selected}
    roots.discard(None)
    result=sorted(roots,key=lambda root:root.name)
    identities=[root.get('eff_effect_id') for root in result]
    document.require(all(identities) and len(identities)==len(set(identities)) if result else True,
        '所选特效含重复来源身份，请通过导入创建独立特效，不能直接复制特效根')
    return result

@dataclass(frozen=True)
class EffectSelection:
    root: object
    emitters: object = None

def export_selections(selected=(),meshes=(),addon=None):
    scopes={}
    for obj in selected:
        if isinstance(obj,EffectSelection):
            root,ids=obj.root,obj.emitters
        else:
            root=_selected_effect_root(obj)
            if root is None:continue
            identity=obj.get('eff_effect_node')
            # Imported nodes expose their GameObject identity as eff_effect_identity.
            identity=identity or obj.get('eff_effect_identity')
            node=next((n for n in editor(root).state['catalog']['hierarchy']
                       if n['identity']==identity),None)
            ids=(identity,) if obj!=root and node and any(editor(root).objects[i]['classId']==198
                for i in node.get('components',[])) else None
        if root not in scopes:scopes[root]=ids
        elif scopes[root] is not None:
            scopes[root]=None if ids is None else tuple(sorted(set(scopes[root])|set(ids)))
    return [EffectSelection(root,scopes[root]) for root in export_roots(scopes)]

def export_payload(selection,snapshot=None):
    from . import eiem_effect_scope
    edit=synchronize(selection.root)
    return eiem_effect_scope.payload(edit,assets.export_document(selection.root,edit,snapshot),selection.emitters)
def editor(root):
    raw=root['eff_effect_document'];key=root.as_pointer()
    if key not in _CACHE or _CACHE[key][0]!=raw:_CACHE[key]=(raw,document.Editor(json.loads(raw)))
    return _CACHE[key][1]
def store(root,edit):
    root['eff_effect_document']=document.dumps(edit.state)
    _CACHE[root.as_pointer()]=(root['eff_effect_document'],edit)
def objects(root):return [root]+list(root.children_recursive)
@lru_cache(maxsize=32768)
def _field_key(identity,path):return 'fx_'+hashlib.sha256((identity+document.dumps(path)).encode()).hexdigest()[:24]
def field_key(identity,path):return _field_key(identity,tuple(path))
def field_rows(obj,edit=None):
    raw=obj.get('eff_effect_fields','[]');key=obj.as_pointer()
    if key not in _FIELD_ROWS or _FIELD_ROWS[key][0]!=raw:
        rows=json.loads(raw);root=root_of(obj)
        for row in rows:
            if row['kind'] in ('color','gradient_time'):
                # IDProperty string reads copy the complete native document.
                # Resolve it once for this object, never once per proxy field.
                if edit is None:edit=editor(root)
                value=edit.value(row['identity'],row['path'])
                row['_last']=tuple(value[k] for k in 'rgba') if row['kind']=='color' else value/65535
        _FIELD_ROWS[key]=(raw,rows)
    return _FIELD_ROWS[key][1]
def label(name):
    name=str(name)
    return effect_ui.module_name(name) if name in effect_ui.MODULES else effect_ui.name(name)
def native_fields(edit,identity,path=()):
    value=edit.value(identity,path);schema=edit.schema(identity,path)
    if schema['type'].startswith('PPtr<') or schema['type']=='TypelessData':return
    if isinstance(value,dict):
        if value.get('encoding') in ('base64','author-payload'):return
        for key in value:yield from native_fields(edit,identity,tuple(path)+(key,))
    elif isinstance(value,list):
        for i in range(len(value)):yield from native_fields(edit,identity,tuple(path)+(i,))
    elif edit.editable(identity,path):yield list(path),schema['type'],value
def populate(obj,edit,identities):
    entries=[]
    obj.eff_effect_choices.clear()
    obj.eff_effect_angles.clear()
    for identity in identities:
        if identity not in edit.state['values']:continue
        for path,kind,value in native_fields(edit,identity):
            key=field_key(identity,path)
            wide=kind in document.INTS and (document.INTS[kind][0]<-2147483648 or document.INTS[kind][1]>2147483647)
            if kind=='bool':obj[key]=bool(value)
            elif kind in document.INTS:obj[key]=str(value) if wide else int(value)
            elif kind in ('float','double'):
                if not isinstance(value,(float,int)):continue
                obj[key]=float(value)
            elif kind=='string':obj[key]=value
            else:continue
            description=kind+' · '+'.'.join(map(str,path))+(' · 精确十进制整数' if wide else '')
            obj.id_properties_ui(key).update(description=description)
            unit=effect_ui.angle_unit(path)
            if unit:
                angle=obj.eff_effect_angles.add();angle.name=key
            parent=edit.value(identity,path[:-1]) if path else None
            items=effect_ui.enum_items(path,parent)
            if items and not wide and kind in document.INTS:
                if not any(item[3]==value for item in items):items.append((str(value),'unknown ('+str(value)+')','',value))
                items=[(item[0],item[1],description,item[3]) for item in items]
                obj.id_properties_ui(key).update(items=items)
                choice=obj.eff_effect_choices.add();choice.name=key;choice.choices=document.dumps(items)
            if kind in document.INTS and not wide:
                lo,hi=document.INTS[kind];obj.id_properties_ui(key).update(min=max(lo,-2147483648),max=min(hi,2147483647))
            if path[-1] in ('m_NumColorKeys','m_NumAlphaKeys'):obj.id_properties_ui(key).update(min=0,max=8)
            entries.append(dict(key=key,identity=identity,path=path,kind=kind,wide=wide))
        def proxies(value,path):
            if isinstance(value,dict):
                if set(value)==set('rgba') and all(type(value[k]) in (float,int) for k in 'rgba') and edit.editable(identity,path):
                    key='fxv_'+field_key(identity,path)[3:];obj[key]=[float(value[k]) for k in 'rgba']
                    obj.id_properties_ui(key).update(subtype='COLOR',soft_min=0,soft_max=1,description='RGBA · '+'.'.join(map(str,path)))
                    entries.append(dict(key=key,identity=identity,path=path,kind='color',wide=False))
                if 'm_NumColorKeys' in value and 'm_NumAlphaKeys' in value:
                    for prefix in ('ctime','atime'):
                        for i in range(8):
                            native=prefix+str(i)
                            if native not in value:continue
                            key='fxu_'+field_key(identity,path+[native])[3:];obj[key]=float(value[native])/65535
                            obj.id_properties_ui(key).update(min=0,max=1,description='归一化渐变位置；写回原生 UInt16 (0..65535)')
                            entries.append(dict(key=key,identity=identity,path=path+[native],kind='gradient_time',wide=False))
                for k,v in value.items():proxies(v,path+[k])
            elif isinstance(value,list):
                for i,v in enumerate(value):proxies(v,path+[i])
        proxies(edit.value(identity),[])
    obj['eff_effect_fields']=document.dumps(entries);obj['eff_effect_components']=document.dumps(identities)
    obj['eff_effect_angles_version']=1
    _FIELD_ROWS.pop(obj.as_pointer(),None)
    gradient_editor.prepare(obj,edit,identities)
class EffectAuthorSync:
    """Flush Blender author projections into one native effect document."""
    def __init__(self,root):
        self.root=root
        self.edit=editor(root)

    def _remove_deleted_emitters(self):
        live={obj.get('eff_effect_node') for obj in objects(self.root) if obj.get('eff_effect_node')}
        removed=[]
        while True:
            hierarchy=self.edit.state['catalog']['hierarchy']
            missing=[node for node in hierarchy if node['identity'] not in live and
                     any(self.edit.objects[i]['classId']==198 for i in node['components'])]
            leaf=next((node for node in reversed(missing) if not node['children']),None)
            if leaf is None:
                if missing:
                    document.require(False,'删除的发射器仍有原生子 GameObject；请先移除其子发射器或恢复该 Empty')
                break
            identity=leaf['identity']
            topology.remove(self.edit,identity)
            removed.append(identity)
        if removed:
            prefix=self.root.get('eff_effect_id','')
            for helper in curve_editor.helpers(self.root):
                target=helper.get('eff_curve_target')
                owner=helper.get('eff_curve_emitter_node') or (target.get('eff_effect_node') if target else '')
                if owner in removed or not target:
                    curve_editor.close_projection(helper,flush=False)
            for obj in list(preview._collection(self.root).objects):
                if obj.get('eff_preview') and any(
                        obj.get('eff_preview_key','').startswith(prefix+identity) for identity in removed):
                    bpy.data.objects.remove(obj,do_unlink=True)
        return removed

    def _fields(self):
        seen=set()
        for obj in objects(self.root):
            node=obj.get('eff_effect_node')
            if node:
                document.require(node not in seen,'发射器来源身份重复；请用“添加发射器”，不要直接 Shift+D')
                seen.add(node)
        changed=set()
        for obj in objects(self.root):
            for row in field_rows(obj):
                if row['key'] not in obj:continue
                value=obj[row['key']]
                if row['kind'] in ('color','gradient_time'):
                    # The friendly proxy and native leaf fields are both
                    # editable; the last synchronized value resolves conflicts.
                    path=row['path'];current=self.edit.value(row['identity'],path)
                    proxy=tuple(value) if row['kind']=='color' else float(value)
                    previous=row.get('_last',tuple(current[k] for k in 'rgba') if row['kind']=='color' else current/65535)
                    if proxy!=previous:
                        if row['kind']=='color':
                            for k,v in zip('rgba',proxy):current[k]=float(v)
                        else:self.edit.value(row['identity'],path[:-1])[path[-1]]=int(round(proxy*65535))
                        changed.add(row['identity'])
                    current=self.edit.value(row['identity'],path)
                    fresh=tuple(current[k] for k in 'rgba') if row['kind']=='color' else current/65535
                    if proxy!=fresh:obj[row['key']]=list(fresh) if row['kind']=='color' else fresh
                    row['_last']=fresh
                    if proxy!=previous or proxy!=fresh:
                        if row['kind']=='color':
                            for k in 'rgba':obj[field_key(row['identity'],path+[k])]=float(current[k])
                        else:obj[field_key(row['identity'],path)]=int(current)
                    continue
                if row['wide']:value=int(value,10)
                if self.edit.value(row['identity'],row['path'])==value:continue
                kind=row['kind']
                if kind in document.INTS:
                    lo,hi=document.INTS[kind];document.require(type(value) is int and lo<=value<=hi,'整数超出原生范围')
                elif kind in ('float','double'):
                    import math
                    document.require(type(value) in (int,float) and math.isfinite(value) and (kind!='float' or abs(value)<=3.4028234663852886e38),'需要原生范围内的有限浮点数')
                elif kind=='bool':document.require(type(value) is bool,'需要布尔值')
                self.edit.value(row['identity'],row['path'][:-1])[row['path'][-1]]=value
                changed.add(row['identity'])
        return changed

    def _transforms(self):
        # Root TRS is the mount offset; source child Empties are native emitters.
        for obj in objects(self.root):
            if obj==self.root or obj.get('eff_preview'):continue
            identity=next((i for i in json.loads(obj.get('eff_effect_components','[]')) if self.edit.objects[i]['classId']==4),None)
            if not identity:continue
            # matrix_local may be stale immediately after a parent move or seek.
            value=self.edit.value(identity);matrix=obj.matrix_parent_inverse@obj.matrix_basis;last=obj.get('eff_effect_last_matrix')
            flat=[matrix[i][j] for i in range(4) for j in range(4)]
            moved=last is not None and any(abs(a-b)>1e-5 for a,b in zip(last,flat))
            if moved:
                p,q,s=(BASIS.inverted()@matrix@BASIS).decompose()
                for name,keys,values in (('m_LocalPosition','xyz',p),('m_LocalRotation','xyzw',(q.x,q.y,q.z,q.w)),('m_LocalScale','xyz',s)):
                    for key,number in zip(keys,values):
                        value[name][key]=float(number);prop=field_key(identity,[name,key])
                        if prop in obj:obj[prop]=float(number)
            else:
                p=value.get('m_LocalPosition',{});q=value.get('m_LocalRotation',{});s=value.get('m_LocalScale',{})
                desired=BASIS@Matrix.LocRotScale(Vector(tuple(p.get(k,0) for k in 'xyz')),Quaternion((q.get('w',1),q.get('x',0),q.get('y',0),q.get('z',0))),Vector(tuple(s.get(k,1) for k in 'xyz')))@BASIS.inverted()
                basis=obj.matrix_parent_inverse.inverted()@desired
                if any(abs(obj.matrix_basis[i][j]-basis[i][j])>1e-6 for i in range(4) for j in range(4)):
                    obj.matrix_basis=basis
            local=obj.matrix_parent_inverse@obj.matrix_basis
            flat=[local[i][j] for i in range(4) for j in range(4)]
            if last is None or list(last)!=flat:obj['eff_effect_last_matrix']=flat

    def _normalize(self,changed):
        reordered=set()
        def visit(identity,value):
            if isinstance(value,dict):
                if isinstance(value.get('m_Curve'),list):
                    before=list(value['m_Curve']);value['m_Curve'].sort(key=lambda key:key['time'])
                    if before!=value['m_Curve']:reordered.add(identity)
                if isinstance(value.get('m_Bursts'),list):value['m_BurstCount']=len(value['m_Bursts'])
                for child in value.values():visit(identity,child)
            elif isinstance(value,list):
                for child in value:visit(identity,child)
        for identity in changed:visit(identity,self.edit.state['values'][identity])
        return reordered

    def flush(self):
        removed=self._remove_deleted_emitters()
        changed=self._fields()
        self._transforms()
        reordered=self._normalize(changed)
        material_bridge.synchronize(self.root,self.edit,sys.modules[__name__])
        if gradient_editor.synchronize(self.root,self.edit):store(self.root,self.edit)
        if reordered:
            for obj in objects(self.root):
                ids=json.loads(obj.get('eff_effect_components','[]'))
                if reordered.intersection(ids):populate(obj,self.edit,ids)
        # Keep native array counts aligned with Graph Editor undo changes.
        if curve_editor.synchronize(self.root,self.edit):store(self.root,self.edit)
        if removed:store(self.root,self.edit)
        return self.edit


def synchronize(root):
    from .eiem_effect_scene_material import migrate_origins
    edit=editor(root)
    if migrate_origins(edit):store(root,edit)
    return EffectAuthorSync(root).flush()
def refresh_fields(root):
    edit=editor(root)
    for obj in objects(root):
        ids=json.loads(obj.get('eff_effect_components','[]'))
        if ids:populate(obj,edit,ids)
def import_source(context,source,preserve_source=False,effect_root=None):
    return import_workspace(context, document.load_source(source),preserve_source=preserve_source,
                            effect_root=effect_root)

def import_workspace(context,state,preserve_source=False,effect_id=None,effect_root=None):
    edit=document.Editor(state)
    from .eiem_effect_scene_material import migrate_origins
    migrate_origins(edit)
    # The visible particle module is authoritative for a newly authored effect.
    # The original source and other components are preserved. Advanced users
    # may explicitly re-enable this one known automatic scaling rule.
    if not preserve_source:edit.use_particle_emission()
    collection=bpy.data.collections.new(Path(state['catalog']['prefab']).stem);context.scene.collection.children.link(collection)
    nodes={};host_matrix=effect_root.matrix_basis.copy() if effect_root else None
    for node in state['catalog']['hierarchy']:
        if node['identity']==state['catalog']['root'] and effect_root:
            obj=effect_root;obj.name=node['name'] or obj.name
        else:
            obj=bpy.data.objects.new(node['name'] or 'unknown',None)
        obj.empty_display_type='PLAIN_AXES';obj.empty_display_size=.15
        if collection not in obj.users_collection:collection.objects.link(obj)
        obj['eff_effect_node']=node['identity'];nodes[node['identity']]=obj
    root=nodes[state['catalog']['root']];root['eff_effect_document']=document.dumps(state)
    # A Format 3 import can keep its validated attachment identity. Source
    # imports still get a fresh ID unless the caller supplies one explicitly.
    root['eff_effect_id']=str(effect_id or uuid.uuid4().hex)
    if preserve_source:
        root.eff_effect.source_animation=True
        clips=preview.playback.clip_items(root,edit)
        if len(clips)==2:root.eff_effect.preview_clip=clips[1][0]
    compiler=effect_publish.compiler_path(required=False)
    if compiler and compiler.is_file():root.eff_effect.compiler=str(compiler)
    root['eff_effect_collection']=collection.name
    for node in state['catalog']['hierarchy']:
        obj=nodes[node['identity']]
        if node['parent']:
            parent=next(n for n in state['catalog']['hierarchy'] if n['transform']==node['parent']);obj.parent=nodes[parent['identity']]
        transform=edit.state['values'].get(node['transform'],{});p=transform.get('m_LocalPosition',{});q=transform.get('m_LocalRotation',{});s=transform.get('m_LocalScale',{})
        pos=Vector(tuple(p.get(k,0) for k in ('x','y','z'))) if obj!=root else Vector((0,0,0))
        rot=Quaternion((q.get('w',1),q.get('x',0),q.get('y',0),q.get('z',0)))
        if obj==root and host_matrix is not None:obj.matrix_basis=host_matrix
        else:obj.matrix_basis=BASIS@Matrix.LocRotScale(pos,rot,Vector(tuple(s.get(k,1) for k in ('x','y','z'))))@BASIS.inverted()
        local=obj.matrix_parent_inverse@obj.matrix_basis
        obj['eff_effect_last_matrix']=[local[i][j] for i in range(4) for j in range(4)]
        populate(obj,edit,[i for i in node['components'] if i in edit.state['values'] and edit.objects[i]['classId']!=1])
    for spec in edit.objects.values():
        if not spec.get('owner') and spec['classId']==21 and spec['identity'] in edit.state['values']:
            assets.ensure_material_object(root,edit,spec['identity'],populate)
            preview._material_asset(root,edit,spec['identity'],preview._resources(root,edit))
    assets.expand_meshes(context,root,edit)
    for obj in context.selected_objects:obj.select_set(False)
    root['eff_effect_ui_version']=2
    root.select_set(True);context.view_layer.objects.active=root;preview.refresh(root,edit);preview.prime_signature(root,edit)
    return root

def choice_items(self,context):
    raw=self.choices
    if raw not in _ENUM_CACHE:_ENUM_CACHE[raw]=[tuple(item) for item in json.loads(raw or '[]')]
    return _ENUM_CACHE[raw]

class EFF_PG_effect_choice(bpy.types.PropertyGroup):
    choices:StringProperty(options={'HIDDEN'})
    value:EnumProperty(name='模式',items=choice_items,get=lambda self:int(self.id_data.get(self.name,0)),set=lambda self,value:self.id_data.__setitem__(self.name,int(value)))

class EFF_PG_effect_angle(bpy.types.PropertyGroup):
    # Explicit numeric degrees are independent of the scene's rotation-unit
    # preference. The sole stored author value stays in native radians, so
    # UI reloads, raw edits and undo cannot apply a second conversion.
    degrees:FloatProperty(name='角度',precision=3,
        description='角度制；旋转为 °，角速度为 °/s。自动换算为游戏原生弧度',
        get=lambda self:math.degrees(float(self.id_data.get(self.name,0))),
        set=lambda self,value:self.id_data.__setitem__(self.name,math.radians(value)))

class EFF_PG_effect_key(bpy.types.PropertyGroup):
    key:StringProperty(name='按键')

def preview_settings_updated(cfg,context):
    # A saved PointerProperty toggle need not produce a geometry update.
    # Queue the same author-refresh path used by depsgraph edits, even while
    # the timeline is stopped, rather than waiting for another frame change.
    root=root_of(cfg.id_data)
    if root:preview.schedule_refresh(root)


def clear_mesh_pick_slots():
    pending=tuple(_MESH_PICK_PENDING);_MESH_PICK_PENDING.clear()
    for obj in pending:
        try:obj.eff_effect.particle_mesh_pick=None
        except ReferenceError:pass
    return None


def mesh_pick_updated(cfg,context):
    mesh=cfg.particle_mesh_pick
    if mesh is None:return
    cfg.particle_mesh=mesh
    preview_settings_updated(cfg,context)
    # Native eyedropper checks that its sampled ID was assigned before exiting.
    # Reset afterwards so the next redraw has an empty slot with an eyedropper,
    # while the actual author binding stays intact (also on Escape/cancel).
    _MESH_PICK_PENDING.add(cfg.id_data)
    if not bpy.app.timers.is_registered(clear_mesh_pick_slots):
        bpy.app.timers.register(clear_mesh_pick_slots,first_interval=.1)


def place_particle_mesh(node, context):
    """Adopt a Mesh's current world appearance into the author Transform once.

    The preview's mount mapping also describes the exported attachment. Undo
    that mapping here so owner/mount/root offsets are not added a second time.
    Subsequent author moves remain independent; export never moves the emitter.
    """
    root=root_of(node);mesh=node.eff_effect.particle_mesh
    document.require(root and mesh,'请选择 Mesh 发射器并指定调用的 Mesh')
    context.view_layer.update()
    synchronize(root)
    desired=assets.mesh_placement_matrix(mesh)
    node.matrix_world=root.matrix_world@preview.mount_matrix(root).inverted()@desired
    context.view_layer.update()
    edit=synchronize(root);store(root,edit)
    preview.schedule_refresh(root)
    return edit


def mesh_binding_updated(cfg,context):
    mesh=cfg.particle_mesh
    if mesh and not mesh.get('eff_effect_mesh_source') and not mesh.get('eff_particle_template'):
        if root_of(cfg.id_data):place_particle_mesh(cfg.id_data,context)
    preview_settings_updated(cfg,context)


class EFF_PG_effect_object(bpy.types.PropertyGroup):
    enabled:BoolProperty(name='随 Mod 导出',default=True)
    preview:BoolProperty(name='离线粒子预览',default=True,update=preview_settings_updated)
    preview_particles:BoolProperty(name='预览粒子',default=True,update=preview_settings_updated,description='关闭后可单独研究静态水网格和 VAT，减少预览开销')
    trail_preview:BoolProperty(name='预览 Trail / Ribbon',default=True,update=preview_settings_updated,description='有限时间采样重建拖尾，支持倒退；自定义脚本带状网格不在此范围')
    show_shape:BoolProperty(name='显示发射范围',default=True,update=preview_settings_updated)
    source_animation:BoolProperty(name='播放来源动画',default=False,
        update=preview_settings_updated,description='只采样原始 AnimationClip，Transform 和材质动画只作用于预览副本，不写回作者参数')
    preview_clip:EnumProperty(name='来源片段',update=preview_settings_updated,items=lambda self,context:preview.playback.clip_items(self.id_data,editor(self.id_data)) if self.id_data.get('eff_effect_document') else [('NONE','无来源片段','')])
    clip_loop:BoolProperty(name='循环所选片段',default=False,update=preview_settings_updated,description='研究时手动循环；不表示原 Animator 会这样切换')
    vat_preview:BoolProperty(name='Softbody VAT 网格变形',default=True,
        update=preview_settings_updated,description='从原生浮点数据纹理和 UV1 查找坐标重放；不把 PNG 当作顶点数据')
    all_lods:BoolProperty(name='覆盖全部 LOD',default=True,
        description='精确关联目标网格的全部已知 LOD，共用一个稳定挂点与播放实例；同时解除特效自身的距离或质量降级。关闭则只跟随所选网格并保留来源特效降级')
    usage:EnumProperty(name='特效用途',items=[
        (MESH_ATTACHMENT,'挂载到 Mesh','Mesh 命中时，在该模型实例上挂载特效；不会替换游戏原特效'),
        (REPLACE_GAME_PREFAB,'替换游戏特效','按游戏逻辑 Prefab 替换资源；该 Prefab 的所有调用都会受影响')],
        default=MESH_ATTACHMENT,update=effect_usage_updated)
    replacement_prefab:StringProperty(name='目标游戏 Prefab',description='游戏中特效 Prefab 的规范逻辑路径，例如 assets/.../effect.prefab')
    owner:PointerProperty(name='挂载目标 Mesh',type=bpy.types.Object,update=preview_settings_updated,poll=lambda self,obj:obj.type=='MESH' and not obj.get('eff_preview'))
    mount:PointerProperty(name='挂载 Empty',type=bpy.types.Object,update=preview_settings_updated,poll=lambda self,obj:__import__(__package__+'.eiem_effect_mounts',fromlist=['eligible']).eligible(obj),description='可选。相对目标 Mesh/骨骼的作者挂点；自动导出父级和动画，无需额外选中')
    bone:StringProperty(name='挂载骨骼',update=preview_settings_updated,description='留空时挂到目标 Mesh 的 Renderer Transform；事件触发暂不支持')
    toggle_keys:CollectionProperty(type=EFF_PG_effect_key)
    build_path:StringProperty(name='原生构建目录',subtype='DIR_PATH')
    compiler:StringProperty(name='AnimeStudio',subtype='FILE_PATH')
    output:StringProperty(name='旧独立 Mod 目录',subtype='DIR_PATH',options={'HIDDEN'})
    status:StringProperty(options={'HIDDEN'})
    particle_mesh:PointerProperty(name='自定义粒子 Mesh',type=bpy.types.Object,update=mesh_binding_updated,
        poll=lambda self,obj:obj.type=='MESH' and not obj.get('eff_preview'),
        description='以物体原点为旋转轴心，选取时采用当前位置和朝向；保留父级缩放。之后使用发射器变换独立摆放，不修改源 Mesh')
    particle_mesh_pick:PointerProperty(name='吸取 Mesh',type=bpy.types.Object,
        options={'SKIP_SAVE'},update=mesh_pick_updated,
        poll=lambda self,obj:obj.type=='MESH' and not obj.get('eff_preview'),
        description='点击吸管，从视图或大纲选择 Mesh；取消保留当前 Mesh，选择后仍可继续吸取更换')

class EFF_PG_effect_texture(bpy.types.PropertyGroup):
    source_name:StringProperty(name='来源贴图')
    source_metadata:StringProperty(options={'HIDDEN'})
    image:PointerProperty(name='替换图片',type=bpy.types.Image,description='留空保留来源；指定图片随特效打包，支持绘制后和打包图片')
    linear:BoolProperty(name='线性数据',default=False,description='遮罩/法线等数据图开启；普通颜色图关闭')
    mipmaps:BoolProperty(name='生成 Mipmap',default=True)
    wrap:EnumProperty(name='平铺',items=[('0','重复',''),('1','钳制',''),('2','镜像',''),('3','单次镜像','')],default='0')
    filter:EnumProperty(name='过滤',items=[('0','最近邻',''),('1','双线性',''),('2','三线性','')],default='1')
class EFF_OT_effect_import(bpy.types.Operator,ImportHelper):
    bl_idname='eiem.effect_import';bl_label='导入 EFF 特效为空物体';bl_options={'UNDO'}
    filter_glob:StringProperty(default='effect.json',options={'HIDDEN'})
    preserve_source:BoolProperty(name='研究原版：保留来源控制',default=False,
        description='保留自动数量组件并启用来源动画预览；不自动推测技能状态机或脚本时序')
    def execute(self,context):
        try:
            path=Path(self.filepath);import_source(context,path if path.is_dir() else path.parent,preserve_source=self.preserve_source);return {'FINISHED'}
        except Exception as e:self.report({'ERROR'},str(e));return {'CANCELLED'}

class EFF_OT_effect_create_blank(bpy.types.Operator,ImportHelper):
    bl_idname='eiem.effect_create_blank';bl_label='从空白创建 EFF 特效';bl_options={'UNDO'}
    filter_glob:StringProperty(default='effect.json',options={'HIDDEN'})
    target_mesh_name:StringProperty(name='挂载目标 Mesh',description='选择具有游戏来源证明的 Mesh 对象')
    root_name:StringProperty(name='特效根名称',default='New Effect')
    output_directory:StringProperty(name='输出目录',subtype='DIR_PATH')
    host_object_name:StringProperty(options={'HIDDEN'})
    def invoke(self,context,event):
        active_object=context.active_object
        if active_object and active_object.type=='MESH':self.target_mesh_name=active_object.name
        elif active_object and active_object.type=='EMPTY' and not root_of(active_object):
            self.host_object_name=active_object.name;self.root_name=active_object.name
            parent=active_object.parent
            while parent:
                if parent.type=='MESH':self.target_mesh_name=parent.name;break
                parent=parent.parent
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}
    def draw(self,context):
        self.layout.prop_search(self,'target_mesh_name',bpy.data,'objects',text='挂载目标 Mesh')
        self.layout.prop(self,'root_name')
        self.layout.prop(self,'output_directory')
    def execute(self,context):
        try:
            host=bpy.data.objects.get(self.host_object_name) if self.host_object_name else None
            document.require(not self.host_object_name or host and host.type=='EMPTY' and not root_of(host),
                '用于创建特效根的 Empty 已不存在或已经属于其他特效')
            owner=bpy.data.objects.get(self.target_mesh_name)
            document.require(owner and owner.type=='MESH' and not owner.get('eff_preview'),
                'Select an imported game Mesh with source proof as the mount target')
            path=str(owner.data.get('eiem_target_path','') or owner.data.get('eiem_source',''))
            asset=str(owner.data.get('eiem_target_asset','') or owner.data.get('eiem_asset',''))
            document.require(path and asset,'Mount target Mesh has no game logical path/asset identity')
            proof_path=Path(owner.get('eiem_author_package',''))/'source/manifest.json'
            document.require(proof_path.is_file(),'Mount target Mesh has no EFF source proof')
            proof=json.loads(proof_path.read_text('utf-8-sig'))
            source=next((row for row in proof.get('resources',[]) if row.get('type')=='Mesh' and
                row.get('logicalPath')==path and row.get('name')==asset),None)
            uses=[row for row in proof.get('rendererUses',[]) if source and
                row.get('mesh',{}).get('identity')==source['identity']]
            document.require(uses,'Source manifest has no Renderer proof for this Mesh')
            owner_path=str(owner.get('eiem_source_owner_path',''))
            document.require(any(row.get('ownerPath')==owner_path for row in uses) or len(uses)==1,
                'Mount target Mesh has ambiguous source Renderers')
            target_prefab=str(proof.get('prefab',''))
            document.require(target_prefab.startswith('assets/') and target_prefab.endswith('.prefab') and '..' not in target_prefab,
                'Mount target source manifest has an invalid Prefab path')
            donor=Path(self.filepath).resolve()
            if donor.is_file():donor=donor.parent
            document.load_source(donor)
            command=[str(effect_publish.compiler_path())]
            slug=''.join(c if c.isalnum() or c in ('-','_') else '_' for c in self.root_name).strip('_') or 'New_Effect'
            folder=Path(bpy.path.abspath(self.output_directory)).resolve() if self.output_directory else donor.parent/'created-effects'
            package=folder/(slug+'.efffx')
            result=subprocess.run(command+['--create-eff-effect',str(donor),target_prefab,str(package),self.root_name],
                capture_output=True,text=True,encoding='utf-8',errors='replace',creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            document.require(result.returncode==0,(result.stderr or result.stdout)[-1600:] or 'AnimeStudio 创建失败')
            root=import_source(context,package,effect_root=host)
            root.eff_effect.owner=owner
            attachment(root)
            self.report({'INFO'},'已创建空白作者特效：'+str(package))
            return {'FINISHED'}
        except Exception as error:
            self.report({'ERROR'},str(error));return {'CANCELLED'}

class EFF_OT_effect_edit_import(bpy.types.Operator,ImportHelper):
    bl_idname='eiem.effect_edit_import';bl_label='导入 EFF 特效编辑';bl_options={'UNDO'}
    filter_glob:StringProperty(default='*.effedit.json',options={'HIDDEN'})
    def execute(self,context):
        try:
            edit=document.load_edit(self.filepath)
            state=document.load_source(edit['source'])
            document.apply_edit(state,edit)
            import_workspace(context,state,preserve_source=True)
            self.report({'INFO'},'已按来源摘要导入 EFF 特效编辑')
            return {'FINISHED'}
        except Exception as error:
            self.report({'ERROR'},str(error));return {'CANCELLED'}


class EFF_OT_effect_research_report(bpy.types.Operator):
    bl_idname='eiem.effect_research_report';bl_label='生成原始资源 / 动画报告'
    bl_description='把各材质参数、贴图逻辑路径、原生流位置和 AnimationClip 绑定写入 Blender 文本块'
    def execute(self,context):
        try:
            root=active(context);document.require(root,'请选择特效根或发射器')
            report=preview.playback.research_report(root,synchronize(root))
            name='EFF 来源报告 · '+root.name
            text=bpy.data.texts.get(name) or bpy.data.texts.new(name)
            text.clear();text.write(json.dumps(report,ensure_ascii=False,indent=2))
            self.report({'INFO'},'报告已写入文本编辑器：'+name)
            return {'FINISHED'}
        except Exception as error:self.report({'ERROR'},str(error));return {'CANCELLED'}
def module_items(self,context):
    root=active(context)
    if not root:return []
    edit=editor(root);items=[]
    target=bpy.data.objects.get(self.object_name) if self.object_name else context.object
    if not target or root_of(target)!=root:return []
    for identity in json.loads(target.get('eff_effect_components','[]')):
        if edit.objects[identity]['classId']!=198:continue
        for name,value in edit.value(identity).items():
            if name=='InitialModule' or not name.endswith('Module') or not isinstance(value,dict) or 'enabled' not in value:continue
            if not target.get(field_key(identity,[name,'enabled']),value['enabled']):
                helptext=effect_ui.MODULES.get(name,('',name))[1]
                items.append((identity+'|'+name,label(name),helptext))
    _ENUM_CACHE['modules']=items
    return _ENUM_CACHE['modules']
class EFF_OT_effect_block(bpy.types.Operator):
    bl_idname='eiem.effect_block';bl_label='启用粒子模块';bl_options={'UNDO'}
    bl_description='启用发射器已有的模块槽；也可直接勾选对应模块标题前的开关，不重置参数'
    module:EnumProperty(name='模块',items=module_items)
    object_name:StringProperty(options={'HIDDEN'})
    def invoke(self,context,event):
        items=module_items(self,context)
        if not items:self.report({'INFO'},'此发射器的全部模块均已启用');return {'CANCELLED'}
        self.module=items[0][0]
        return context.window_manager.invoke_props_dialog(self,width=440)
    def draw(self,context):
        self.layout.label(text='每种模块只有一份；启用会保留已有参数。')
        self.layout.prop(self,'module')
    def execute(self,context):
        try:
            root=active(context);target=bpy.data.objects.get(self.object_name) if self.object_name else context.object
            document.require(target and root_of(target)==root,'请选择当前特效的发射器')
            edit=synchronize(root);identity,name=self.module.split('|')
            document.require(identity in json.loads(target.get('eff_effect_components','[]')) and edit.objects[identity]['classId']==198,'模块不属于当前发射器')
            edit.set_scalar(identity,[name,'enabled'],True)
            store(root,edit);refresh_fields(root);preview.refresh(root,edit);return {'FINISHED'}
        except Exception as e:self.report({'ERROR'},str(e));return {'CANCELLED'}
class EFF_OT_effect_array(bpy.types.Operator):
    bl_idname='eiem.effect_array';bl_label='修改爆发 / 曲线 / 资源条目';bl_options={'UNDO'}
    identity:StringProperty();path:StringProperty();operation:EnumProperty(items=[(x,x,'') for x in ('ADD','COPY','REMOVE')]);index:IntProperty(default=0)
    def execute(self,context):
        try:
            root=active(context);edit=synchronize(root);edit.array_change(self.identity,json.loads(self.path),self.operation,self.index)
            store(root,edit);refresh_fields(root);preview.refresh(root,edit);return {'FINISHED'}
        except Exception as e:self.report({'ERROR'},str(e));return {'CANCELLED'}

class EFF_OT_effect_mask_toggle(bpy.types.Operator):
    bl_idname='eiem.effect_mask_toggle';bl_label='切换原生位标志';bl_options={'UNDO'}
    object_name:StringProperty(options={'HIDDEN'});identity:StringProperty(options={'HIDDEN'})
    path:StringProperty(options={'HIDDEN'});bit:IntProperty(options={'HIDDEN'},min=0,max=31)
    def execute(self,context):
        try:
            target=bpy.data.objects.get(self.object_name);root=root_of(target)
            document.require(root and self.identity in json.loads(target.get('eff_effect_components','[]')),
                            '位标志不属于当前特效组件')
            path=json.loads(self.path);spec=effect_ui.bitmask_spec(path)
            document.require(spec and self.bit in {bit for bit,_ in spec['bits']},'位标志超出字段语义范围')
            edit=synchronize(root)
            native_type=edit.schema(self.identity,path)['type']
            current=edit.value(self.identity,path)
            semantic=effect_ui.field_semantic(path,native_type,
                edit.value(self.identity,path[:-1]),edit.objects[self.identity].get('classId'))
            document.require(semantic=='bitmask','字段类型与位掩码语义不匹配')
            edit.set_scalar(self.identity,path,effect_ui.toggle_native_bit(current,self.bit,native_type))
            store(root,edit);refresh_fields(root);preview.schedule_refresh(root)
            return {'FINISHED'}
        except Exception as error:self.report({'ERROR'},str(error));return {'CANCELLED'}

class EFF_OT_effect_bool_toggle(bpy.types.Operator):
    bl_idname='eiem.effect_bool_toggle';bl_label='切换原生布尔字段';bl_options={'UNDO'}
    object_name:StringProperty(options={'HIDDEN'});identity:StringProperty(options={'HIDDEN'})
    path:StringProperty(options={'HIDDEN'})
    def execute(self,context):
        try:
            target=bpy.data.objects.get(self.object_name);root=root_of(target)
            document.require(root and self.identity in json.loads(target.get('eff_effect_components','[]')),
                            '布尔字段不属于当前特效组件')
            path=json.loads(self.path);edit=synchronize(root)
            schema=edit.schema(self.identity,path);current=edit.value(self.identity,path)
            semantic=effect_ui.field_semantic(path,schema['type'],
                edit.value(self.identity,path[:-1]),edit.objects[self.identity].get('classId'))
            document.require(semantic=='boolean','字段未被定义为布尔值')
            value=(not current) if type(current) is bool else (0 if bool(current) else 1)
            edit.set_scalar(self.identity,path,value)
            store(root,edit);refresh_fields(root);preview.schedule_refresh(root)
            return {'FINISHED'}
        except Exception as error:self.report({'ERROR'},str(error));return {'CANCELLED'}

class EFF_OT_effect_module_copy(bpy.types.Operator):
    bl_idname='eiem.effect_module_copy';bl_label='复制特效模块块';bl_options={'REGISTER'}
    object_name:StringProperty(options={'HIDDEN'});identity:StringProperty(options={'HIDDEN'});module:StringProperty(options={'HIDDEN'})
    def execute(self,context):
        try:
            root=active(context);document.require(root,'请选择当前特效')
            edit=synchronize(root)
            payload=block_clipboard.copy_module(edit,self.identity,self.module)
            encoded=block_clipboard.encode(payload)
            document.require(len(encoded)<=8*1024*1024,'特效模块块过大，暂不支持直接放入系统剪贴板')
            context.window_manager.clipboard=encoded
            self.report({'INFO'},'已复制特效模块块：'+self.module)
            return {'FINISHED'}
        except Exception as error:self.report({'ERROR'},str(error));return {'CANCELLED'}

class EFF_OT_effect_module_paste(bpy.types.Operator):
    bl_idname='eiem.effect_module_paste';bl_label='粘贴特效模块块';bl_options={'REGISTER','UNDO'}
    object_name:StringProperty(options={'HIDDEN'});identity:StringProperty(options={'HIDDEN'});module:StringProperty(options={'HIDDEN'})
    def execute(self,context):
        try:
            root=active(context);document.require(root,'请选择当前特效')
            payload=block_clipboard.decode(context.window_manager.clipboard)
            document.require(payload['root']['module']==self.module,'剪贴板模块与当前模块不匹配')
            trial=block_clipboard.paste_module(synchronize(root),self.identity,payload)
            store(root,trial);refresh_fields(root);preview.refresh(root,trial)
            self.report({'INFO'},'已粘贴特效模块块：'+self.module)
            return {'FINISHED'}
        except Exception as error:self.report({'ERROR'},str(error));return {'CANCELLED'}

class EFF_OT_effect_emitter_copy(bpy.types.Operator):
    bl_idname='eiem.effect_emitter_copy';bl_label='复制特效';bl_options={'REGISTER'}
    object_name:StringProperty(options={'HIDDEN'});identity:StringProperty(options={'HIDDEN'})
    def execute(self,context):
        try:
            root=active(context);document.require(root,'请选择当前特效')
            payload=block_clipboard.copy_emitter(assets.clipboard_snapshot(root,synchronize(root)),self.identity)
            encoded=block_clipboard.encode(payload)
            document.require(len(encoded)<=8*1024*1024,'特效剪贴板数据过大，暂不支持直接放入系统剪贴板')
            context.window_manager.clipboard=encoded
            self.report({'INFO'},'已复制完整特效及其子发射器')
            return {'FINISHED'}
        except Exception as error:self.report({'ERROR'},str(error));return {'CANCELLED'}

class EFF_OT_effect_emitter_paste(bpy.types.Operator):
    bl_idname='eiem.effect_emitter_paste';bl_label='粘贴特效';bl_options={'REGISTER','UNDO'}
    object_name:StringProperty(options={'HIDDEN'})
    def execute(self,context):
        created=[]
        before={name:{item.as_pointer() for item in getattr(bpy.data,name)}
                for name in ('objects','materials','meshes','images')}
        root=None;previous=None
        try:
            root=active(context);document.require(root,'请选择当前特效')
            previous=root['eff_effect_document']
            payload=block_clipboard.decode(context.window_manager.clipboard)
            document.require(payload['kind']=='emitter','剪贴板内容不是完整特效')
            target=bpy.data.objects.get(self.object_name) if self.object_name else context.object
            document.require(target and root_of(target)==root,'请选择目标特效节点')
            edit=synchronize(root)
            parent_identity=target.get('eff_effect_node') or edit.state['catalog']['root']
            trial,nodes=block_clipboard.paste_emitter(edit,parent_identity,payload)
            collection=preview._collection(root)
            by_identity={o.get('eff_effect_node'):o for o in objects(root) if o.get('eff_effect_node')}
            transform_objects={identity:obj for obj in objects(root)
                               for identity in json.loads(obj.get('eff_effect_components','[]'))
                               if identity in trial.objects and trial.objects[identity]['classId']==4}
            for node in nodes:
                obj=bpy.data.objects.new(node['name'] or '新特效',None);collection.objects.link(obj)
                obj.empty_display_type='PLAIN_AXES';obj.empty_display_size=.15
                obj['eff_effect_node']=node['identity'];created.append(obj);by_identity[node['identity']]=obj
                transform_objects[node['transform']]=obj
            for node in nodes:
                obj=by_identity[node['identity']]
                parent_obj=transform_objects.get(node['parent']) or by_identity.get(node['parent'])
                document.require(parent_obj,'无法建立特效父子关系')
                obj.parent=parent_obj
                transform=trial.value(node['transform']);p=transform.get('m_LocalPosition',{});q=transform.get('m_LocalRotation',{});s=transform.get('m_LocalScale',{})
                obj.matrix_basis=BASIS@Matrix.LocRotScale(Vector(tuple(p.get(k,0) for k in 'xyz')),Quaternion((q.get('w',1),q.get('x',0),q.get('y',0),q.get('z',0))),Vector(tuple(s.get(k,1) for k in 'xyz')))@BASIS.inverted()
                obj['eff_effect_last_matrix']=[obj.matrix_basis[i][j] for i in range(4) for j in range(4)]
                populate(obj,trial,[i for i in node['components'] if trial.objects[i]['classId']!=1])
                renderer=next((i for i in node['components'] if trial.objects[i]['classId']==199),None)
                if renderer:
                    mesh=assets.source_meshes(root).get(preview._target(trial,renderer,['m_Mesh']))
                    if mesh:obj.eff_effect.particle_mesh=mesh
            assets.ensure_pasted_assets(context,root,trial,populate)
            store(root,trial);refresh_fields(root);preview.refresh(root,trial)
            for item in context.selected_objects:item.select_set(False)
            if created:
                created[-1].select_set(True);context.view_layer.objects.active=created[-1]
            self.report({'INFO'},'已粘贴完整特效及其子发射器')
            return {'FINISHED'}
        except Exception as error:
            if root and previous is not None:root['eff_effect_document']=previous
            for name in ('objects','materials','meshes','images'):
                data=getattr(bpy.data,name)
                for item in list(data):
                    if item.as_pointer() not in before[name]:data.remove(item,do_unlink=True)
            self.report({'ERROR'},str(error));return {'CANCELLED'}

class EFF_OT_effect_use_particle_emission(bpy.types.Operator):
    bl_idname='eiem.effect_use_particle_emission';bl_label='使用粒子模块的发射数量';bl_options={'REGISTER','UNDO'}
    bl_description='关闭当前发射器的来源自动发射脚本，保留每秒发射数、寿命和其他设置'
    object_name:StringProperty(options={'HIDDEN'})
    def execute(self,context):
        try:
            obj=bpy.data.objects.get(self.object_name);root=root_of(obj) if obj else None
            document.require(root,'请选择当前特效的发射器')
            edit=synchronize(root)
            edit.use_particle_emission(json.loads(obj.get('eff_effect_components','[]')))
            store(root,edit);refresh_fields(root);preview.refresh(root,edit)
            return {'FINISHED'}
        except Exception as e:self.report({'ERROR'},str(e));return {'CANCELLED'}

class EFF_OT_effect_continuous(bpy.types.Operator):
    bl_idname='eiem.effect_continuous';bl_label='设为持续发射';bl_options={'REGISTER','UNDO'}
    bl_description='启用循环和固定每秒发射，移除爆发与按距离发射；保留粒子的寿命、形状与外观'
    object_name:StringProperty(options={'HIDDEN'})
    identity:StringProperty(options={'HIDDEN'})
    rate:FloatProperty(name='每秒发射数',default=5,min=.01,max=100000,precision=2)
    capacity:IntProperty(name='最大同时存活数',default=100,min=1,max=20000,
        description='至少为每秒数量 × 最长寿命，并留出余量；不足时会暂停生成')
    def invoke(self,context,event):
        root=active(context)
        if not root:return {'CANCELLED'}
        edit=synchronize(root)
        self.rate=max(.01,float(edit.value(self.identity,['EmissionModule','rateOverTime','scalar'])))
        self.capacity=max(1,int(edit.value(self.identity,['InitialModule','maxNumParticles'])))
        return context.window_manager.invoke_props_dialog(self,width=440)
    def draw(self,context):
        self.layout.prop(self,'rate');self.layout.prop(self,'capacity')
        effect_ui.hint(self.layout,'开启循环；每秒数量改为常量；清除全部定时爆发及按距离发射。')
        effect_ui.hint(self.layout,'存活上限建议 ≥ 每秒数量 × 最长寿命，并留出余量。')
    def execute(self,context):
        try:
            root=active(context);target=bpy.data.objects.get(self.object_name) if self.object_name else context.object
            document.require(target and root_of(target)==root,'请选择当前特效的发射器')
            edit=synchronize(root)
            document.require(self.identity in json.loads(target.get('eff_effect_components','[]')) and edit.objects[self.identity]['classId']==198,'粒子系统不属于当前发射器')
            edit.keep_emitter_running(self.identity)
            edit.set_scalar(self.identity,['EmissionModule','enabled'],True)
            for field,number in (('rateOverTime',self.rate),('rateOverDistance',0.0)):
                edit.set_scalar(self.identity,['EmissionModule',field,'minMaxState'],0)
                edit.set_scalar(self.identity,['EmissionModule',field,'scalar'],float(number))
            emission=edit.value(self.identity,['EmissionModule']);emission['m_Bursts']=[];emission['m_BurstCount']=0
            edit.use_particle_emission(json.loads(target.get('eff_effect_components','[]')))
            edit.set_scalar(self.identity,['InitialModule','maxNumParticles'],self.capacity)
            store(root,edit);refresh_fields(root);preview.refresh(root,edit)
            self.report({'INFO'},'已设为每秒 '+format(self.rate,'g')+' 个，循环持续发射；爆发已清除')
            return {'FINISHED'}
        except Exception as error:self.report({'ERROR'},str(error));return {'CANCELLED'}

def emitter_items(self,context):
    root=active(context)
    if not root:return []
    edit=editor(root)
    rows=[(n['identity'],n['name'],'复制该发射器当前的粒子模块、渲染器和组件为独立发射器')
          for n in edit.state['catalog']['hierarchy'] if any(edit.objects[i]['classId']==198 for i in n['components'])]
    active_ids={row[0] for row in rows}
    rows += [(n['identity'],'来源模板 · '+n['name'],'已删除的来源发射器仍可作为新增模板')
             for n in edit.state.get('source_hierarchy',[]) if n['identity'] not in active_ids and
             any(edit.objects[i]['classId']==198 for i in n['components'])]
    _ENUM_CACHE['emitter_templates']=rows
    return _ENUM_CACHE['emitter_templates']

class EFF_OT_effect_emitter_add(bpy.types.Operator):
    bl_idname='eiem.effect_emitter_add';bl_label='添加发射器';bl_options={'REGISTER','UNDO'}
    bl_description='以现有发射器为模板创建独立的游戏粒子系统；模块与 Transform 独立，Mesh 和材质先共用'
    template:EnumProperty(name='发射器模板',items=emitter_items)
    name:StringProperty(name='新发射器名称',default='新发射器')
    def invoke(self,context,event):
        rows=emitter_items(self,context)
        if not rows:self.report({'ERROR'},'当前来源没有可用的发射器模板');return {'CANCELLED'}
        selected=context.object.get('eff_effect_node') if context.object else None
        self.template=selected if selected in {r[0] for r in rows} else rows[0][0]
        return context.window_manager.invoke_props_dialog(self,width=440)
    def draw(self,context):
        self.layout.prop(self,'template');self.layout.prop(self,'name')
        effect_ui.hint(self.layout,'新增独立发射器；初始共用来源 Mesh / 材质，可在渲染设置中更换。')
    def execute(self,context):
        obj=None
        try:
            root=active(context);document.require(root,'请选择特效根或发射器')
            # Prepare a detached document so a rejected operation cannot leave
            # a half-authored native topology in the cached scene state.
            edit=document.Editor(copy.deepcopy(synchronize(root).state))
            source=next((o for o in objects(root) if o.get('eff_effect_node')==self.template),None)
            clone=topology.duplicate(edit,self.template,self.name.strip() or '新发射器')
            if source==root:
                transform=edit.value(clone['transform'])
                transform['m_LocalPosition']=dict(x=0.0,y=0.0,z=0.0)
                transform['m_LocalRotation']=dict(x=0.0,y=0.0,z=0.0,w=1.0)
                transform['m_LocalScale']=dict(x=1.0,y=1.0,z=1.0)
            obj=bpy.data.objects.new(clone['name'],None);preview._collection(root).objects.link(obj)
            obj.empty_display_type='PLAIN_AXES';obj.empty_display_size=source.empty_display_size if source else .15
            obj.parent=next(o for o in objects(root) if any(edit.objects[i]['classId']==4 and i==clone['parent'] for i in json.loads(o.get('eff_effect_components','[]'))))
            transform=edit.value(clone['transform']);p=transform['m_LocalPosition'];q=transform['m_LocalRotation'];s=transform['m_LocalScale']
            obj.matrix_basis=BASIS@Matrix.LocRotScale(Vector(tuple(p[k] for k in 'xyz')),Quaternion((q['w'],q['x'],q['y'],q['z'])),Vector(tuple(s[k] for k in 'xyz')))@BASIS.inverted()
            obj['eff_effect_node']=clone['identity'];obj['eff_effect_last_matrix']=[obj.matrix_basis[i][j] for i in range(4) for j in range(4)]
            renderer=next(i for i in clone['components'] if edit.objects[i]['classId']==199)
            obj.eff_effect.particle_mesh=source.eff_effect.particle_mesh if source else assets.source_meshes(root).get(preview._target(edit,renderer,['m_Mesh']))
            populate(obj,edit,[i for i in clone['components'] if edit.objects[i]['classId']!=1])
            store(root,edit)
            for selected in context.selected_objects:selected.select_set(False)
            obj.select_set(True);context.view_layer.objects.active=obj
            try:preview.refresh(root,edit)
            except Exception as error:root['预览错误']=str(error)
            self.report({'INFO'},'已添加独立发射器：'+obj.name)
            return {'FINISHED'}
        except Exception as error:
            if obj and obj.name in bpy.data.objects and not any(n['identity']==obj.get('eff_effect_node') for n in editor(root).state['catalog']['hierarchy']):
                bpy.data.objects.remove(obj,do_unlink=True)
            self.report({'ERROR'},str(error));return {'CANCELLED'}

class EFF_OT_effect_emitter_remove(bpy.types.Operator):
    bl_idname='eiem.effect_emitter_remove';bl_label='删除发射器';bl_options={'REGISTER','UNDO'}
    bl_description='从当前特效移除此发射器及其预览；共享的 Mesh、材质和其他发射器保留，可撤销'
    object_name:StringProperty(options={'HIDDEN'})
    def execute(self,context):
        try:
            root=active(context);obj=bpy.data.objects.get(self.object_name) if self.object_name else context.object
            document.require(obj and root_of(obj)==root,'请选择当前特效的发射器')
            for child in list(obj.children_recursive):
                document.require(child.get('eff_preview'),'请先处理该发射器的子对象')
            edit=document.Editor(copy.deepcopy(synchronize(root).state))
            identity=obj.get('eff_effect_node')
            for helper in curve_editor.helpers(root):
                if helper.get('eff_curve_target')==obj or helper.get('eff_curve_emitter_node')==identity:
                    curve_editor.close_projection(helper,flush=False)
            topology.remove(edit,obj.get('eff_effect_node'))
            store(root,edit)
            context.view_layer.objects.active=root;root.select_set(True)
            for child in list(obj.children_recursive):bpy.data.objects.remove(child,do_unlink=True)
            bpy.data.objects.remove(obj,do_unlink=True)
            preview.clear_cache()
            try:preview.refresh(root,edit)
            except Exception as error:root['预览错误']=str(error)
            return {'FINISHED'}
        except Exception as error:self.report({'ERROR'},str(error));return {'CANCELLED'}
def logical_asset_text(edit,identity):
    spec=edit.objects.get(identity,{})
    paths=sorted({(str(row.get('path','')),str(row.get('name','')))
                  for row in spec.get('logicalPaths') or [] if row.get('path')})
    if paths:
        path,name=paths[0]
        suffix=' (+%d)'%(len(paths)-1) if len(paths)>1 else ''
        return path+(' · '+name if name else '')+suffix
    return (spec.get('name') or spec.get('type') or '来源资源')+' · '+identity

def draw_pointer(layout,edit,identity,path,value,title=None):
    target=preview._target(edit,identity,path)
    binding=next((r.get('gameLogical') for r in edit.objects[identity].get('references',[])
                  if r.get('field')==game_resources.field_path(path) and r.get('gameLogical')),None)
    row=layout.row(align=True)
    resource=(logical_asset_text(edit,target) if target else
              (str(binding.get('path',''))+(' · '+str(binding.get('asset','')) if binding.get('asset') else '') if binding else
        ('未解析 PPtr %s:%s'%(value.get('m_FileID',0),value.get('m_PathID',0)) if value.get('m_PathID') else '无')))
    row.label(text=(title or label(path[-1]))+'：'+resource)
    if not edit.editable(identity,path):return
    op=row.operator('eiem.effect_pointer',text='',icon='EYEDROPPER')
    op.identity=identity;op.path=document.dumps(path)
    spec=edit.objects.get(target,{})
    logical=next((r for r in spec.get('logicalPaths') or [] if r.get('path')),None)
    op.logical_path=binding.get('path','') if binding else (logical.get('path','') if logical else '')
    op.asset_name=binding.get('asset','') if binding else (logical.get('name','') if logical else spec.get('name',''))
    op.source_prefab=binding.get('sourcePrefab','') if binding else ''

class EFF_OT_effect_pointer(bpy.types.Operator):
    bl_idname='eiem.effect_pointer';bl_label='按逻辑路径绑定资源';bl_options={'UNDO'}
    bl_description='绑定游戏逻辑资源；游戏资源由游戏加载，不会复制进 Mod'
    identity:StringProperty(options={'HIDDEN'});path:StringProperty(options={'HIDDEN'})
    logical_path:StringProperty(name='游戏逻辑路径')
    asset_name:StringProperty(name='资产名')
    source_prefab:StringProperty(name='来源 Prefab（可选）',description='游戏中的 assets/.../*.prefab 逻辑路径，无须导入本地；用于不能独立加载的子资源')
    clear_reference:BoolProperty(name='清空引用',default=False)
    def invoke(self,context,event):
        if not self.logical_path:
            root=active(context)
            if root:
                edit=editor(root);identity_path=json.loads(self.path)
                binding=next((r.get('gameLogical') for r in edit.objects[self.identity].get('references',[])
                              if r.get('field')==game_resources.field_path(identity_path) and r.get('gameLogical')),None)
                if binding:
                    self.logical_path=binding.get('path','');self.asset_name=binding.get('asset','');self.source_prefab=binding.get('sourcePrefab','')
                target=preview._target(edit,self.identity,identity_path)
                spec=edit.objects.get(target,{})
                logical=next((r for r in spec.get('logicalPaths') or [] if r.get('path')),None)
                if logical and not self.logical_path:
                    self.logical_path=logical['path'];self.asset_name=logical.get('name','')
        return context.window_manager.invoke_props_dialog(self,width=560)
    def draw(self,context):
        self.layout.prop(self,'logical_path')
        self.layout.prop(self,'asset_name')
        self.layout.prop(self,'source_prefab')
        self.layout.prop(self,'clear_reference')
        if not self.clear_reference:
            effect_ui.hint(self.layout,'游戏资源由 DLL 按逻辑路径请求；绑定和导出均不查询解包索引。')
            effect_ui.hint(self.layout,'未导入本地像素的资源可能无法离线预览，游戏加载时验证路径和类型。')
    def execute(self,context):
        try:
            root=active(context);edit=synchronize(root);path=json.loads(self.path)
            if self.clear_reference:
                edit.set_pointer(self.identity,path,None);game_resources.clear_binding(edit,self.identity,path);target=None
            else:
                pointer_type=edit.schema(self.identity,path)['type']
                document.require(pointer_type.startswith('PPtr<'),'当前字段不是资源引用')
                target=game_resources.bind_logical(edit,self.identity,path,self.logical_path,self.asset_name,
                                                   pointer_type[5:-1],source_prefab=self.source_prefab)
            if edit.objects[self.identity]['classId']==199:
                node=next((o for o in objects(root) if self.identity in json.loads(o.get('eff_effect_components','[]'))),None)
                if node and path==['m_Mesh']:node.eff_effect.particle_mesh=assets.source_meshes(root).get(target) if target else None
                elif node and node.eff_effect.particle_mesh and len(path)==2 and path[0]=='m_Materials':
                    mesh=node.eff_effect.particle_mesh.data
                    if path[1]<len(mesh.materials):mesh.materials[path[1]]=preview._material_asset(root,edit,target,preview._resources(root,edit)) if target else None
            store(root,edit);preview.refresh(root,edit);return {'FINISHED'}
        except Exception as e:self.report({'ERROR'},str(e));return {'CANCELLED'}
class EFF_OT_effect_resource(bpy.types.Operator):
    bl_idname='eiem.effect_resource';bl_label='编辑特效材质和贴图';bl_options={'UNDO'}
    identity:StringProperty()
    select_object:BoolProperty(default=True,options={'HIDDEN'})
    def execute(self,context):
        try:
            root=active(context);document.require(root,'请选择当前特效')
            edit=synchronize(root)
            obj=assets.ensure_material_object(root,edit,self.identity,populate)
            if self.select_object:
                for selected in context.selected_objects:selected.select_set(False)
                obj.hide_set(False);obj.select_set(True);context.view_layer.objects.active=obj
            preview.schedule_refresh(root)
            return {'FINISHED'}
        except Exception as error:self.report({'ERROR'},str(error));return {'CANCELLED'}

class EFF_OT_effect_select(bpy.types.Operator):
    bl_idname='eiem.effect_select';bl_label='切换特效编辑对象'
    bl_description='选择特效根、发射器或材质空物体，直接编辑该对象的参数'
    object_name:StringProperty(options={'HIDDEN'})
    def execute(self,context):
        root=active(context);obj=bpy.data.objects.get(self.object_name)
        if not root or not obj or root_of(obj)!=root or context.view_layer.objects.get(obj.name)!=obj:
            self.report({'ERROR'},'该对象不在当前特效或当前视图层中');return {'CANCELLED'}
        for selected in context.selected_objects:selected.select_set(False)
        obj.hide_set(False);obj.select_set(True);context.view_layer.objects.active=obj
        return {'FINISHED'}

class EFF_OT_effect_key(bpy.types.Operator):
    bl_idname='eiem.effect_key';bl_label='录制特效显隐按键';bl_options={'REGISTER','UNDO'}
    bl_description='按下任一已录制按键都会切换同一特效；特效仍受挂载 Mesh 显隐控制'
    action:EnumProperty(items=[('ADD','添加','录制一个按键'),('REMOVE','删除','删除按键')])
    root_name:StringProperty(options={'HIDDEN'})
    index:IntProperty(default=-1,options={'HIDDEN'})
    def _status(self,context,text=None):
        workspace=getattr(context,'workspace',None)
        if workspace and hasattr(workspace,'status_text_set'):workspace.status_text_set(text=text)
    def invoke(self,context,event):
        root=bpy.data.objects.get(self.root_name) if self.root_name else root_of(context.object)
        if self.action=='REMOVE':return self.execute(context)
        if not root or not root.get('eff_effect_document'):
            self.report({'ERROR'},'请选择特效根或发射器');return {'CANCELLED'}
        self.root_name=root.name
        self._status(context,'录制特效显隐按键：请按一个键或组合键；Esc 取消')
        context.window_manager.modal_handler_add(self)
        self.report({'INFO'},'请按下用于切换此特效显隐的按键；Esc 取消')
        return {'RUNNING_MODAL'}
    def execute(self,context):
        if self.action!='REMOVE':return {'CANCELLED'}
        root=bpy.data.objects.get(self.root_name)
        if not root or not root.get('eff_effect_document'):
            self.report({'ERROR'},'特效根已不存在');return {'CANCELLED'}
        keys=root.eff_effect.toggle_keys
        if not 0<=self.index<len(keys):
            self.report({'ERROR'},'按键条目已不存在');return {'CANCELLED'}
        keys.remove(self.index)
        return {'FINISHED'}
    def modal(self,context,event):
        if event.value!='PRESS':return {'RUNNING_MODAL'}
        if event.type=='ESC' and not (event.ctrl or event.shift or event.alt):
            self._status(context);return {'CANCELLED'}
        if event.type in {'LEFT_CTRL','RIGHT_CTRL','LEFT_SHIFT','RIGHT_SHIFT','LEFT_ALT','RIGHT_ALT','OSKEY'}:
            return {'RUNNING_MODAL'}
        root=bpy.data.objects.get(self.root_name)
        try:
            document.require(root and root.get('eff_effect_document'),'特效根已不存在')
            key=switch_controls.switch_key_from_event(event)
            existing=[switch_controls.validate_switch_key(item.key) for item in root.eff_effect.toggle_keys]
            document.require(key not in existing,'该按键已存在；每个按键只需添加一次')
            item=root.eff_effect.toggle_keys.add();item.key=key
        except ValueError as error:
            self._status(context,'无法录制：%s；请按其他键，Esc 取消'%error)
            self.report({'WARNING'},str(error));return {'RUNNING_MODAL'}
        self._status(context);self.report({'INFO'},'已添加特效显隐按键：'+key)
        return {'FINISHED'}
    def cancel(self,context):self._status(context)

class EFF_OT_effect_texture_open(bpy.types.Operator,ImportHelper):
    bl_idname='eiem.effect_texture_open';bl_label='选择特效贴图';bl_options={'UNDO'}
    identity:StringProperty();slot:StringProperty()
    filter_glob:StringProperty(default='*.png;*.tga;*.dds;*.jpg;*.jpeg;*.tif;*.tiff;*.exr;*.bmp',options={'HIDDEN'})
    def execute(self,context):
        try:
            root=active(context);item=assets.texture_slot(root,self.identity,self.slot)
            if not item:raise ValueError('材质贴图槽已不存在')
            item.image=bpy.data.images.load(self.filepath,check_existing=True)
            if item.linear:item.image.colorspace_settings.name='Non-Color'
            preview.refresh(root,synchronize(root));return {'FINISHED'}
        except Exception as error:self.report({'ERROR'},str(error));return {'CANCELLED'}

class EFF_OT_effect_mesh_place(bpy.types.Operator):
    bl_idname='eiem.effect_mesh_place';bl_label='采用 Mesh 当前变换';bl_options={'UNDO'}
    bl_description='把源物体的原点位置和朝向写入发射器变换；保留父级尺寸，预览和游戏共用同一变换'
    object_name:StringProperty()
    def execute(self,context):
        try:
            node=bpy.data.objects.get(self.object_name) if self.object_name else context.object
            place_particle_mesh(node,context)
            self.report({'INFO'},'已采用 Mesh 的原点位置和朝向；后续可独立调整发射器变换')
            return {'FINISHED'}
        except Exception as error:self.report({'ERROR'},str(error));return {'CANCELLED'}


class EFF_OT_effect_mesh_edit(bpy.types.Operator):
    bl_idname='eiem.effect_mesh_edit';bl_label='提取来源 Mesh 供编辑';bl_options={'UNDO'}
    object_name:StringProperty();renderer:StringProperty()
    def execute(self,context):
        try:
            node=bpy.data.objects[self.object_name];root=root_of(node);edit=synchronize(root)
            obj=node.eff_effect.particle_mesh
            if not obj:
                vertices,faces,uv,is_mesh=preview._mesh_template(root,edit,self.renderer,preview._resources(root,edit))
                mesh=bpy.data.meshes.new('花瓣可编辑网格')
                mesh.from_pydata([assets.BASIS@v for v in vertices],[],[tuple(reversed(f)) for f in faces]);mesh.update()
                layer=mesh.uv_layers.new(name='UV0')
                for loop in mesh.loops:layer.data[loop.index].uv=uv[loop.vertex_index]
                obj=bpy.data.objects.new('粒子 Mesh · '+node.name,mesh)
                assets_collection=bpy.data.collections.get('EFF 粒子资源编辑')
                if not assets_collection:
                    assets_collection=bpy.data.collections.new('EFF 粒子资源编辑');context.scene.collection.children.link(assets_collection)
                assets_collection.objects.link(obj);obj.hide_render=True
                obj['eff_particle_template']=True
                mat=preview._material(root,edit,self.renderer,preview._resources(root,edit))
                mesh.materials.append(mat)
                node.eff_effect.particle_mesh=obj
            for selected in context.selected_objects:selected.select_set(False)
            obj.hide_set(False);obj.select_set(True);context.view_layer.objects.active=obj
            self.report({'INFO'},'可正常编辑网格和 UV；回到发射器空物体查看粒子预览，使用渲染块所列特效材质')
            return {'FINISHED'}
        except Exception as error:self.report({'ERROR'},str(error));return {'CANCELLED'}

class EFF_OT_effect_assets_expand(bpy.types.Operator):
    bl_idname='eiem.effect_assets_expand';bl_label='展开引用的 Mesh 与材质';bl_options={'UNDO'}
    bl_description='为旧工程补齐来源网格和材质；保留已指定的自定义网格与材质参数'
    def execute(self,context):
        try:
            root=active(context);document.require(root,'请选择特效根或发射器')
            assets.expand_meshes(context,root,synchronize(root))
            preview.refresh(root,synchronize(root))
            self.report({'INFO'},'引用的网格已展开；在特效结构中直接选择编辑，贴图沿用材质页的来源引用')
            return {'FINISHED'}
        except Exception as error:self.report({'ERROR'},str(error));return {'CANCELLED'}

class EFF_OT_effect_export(bpy.types.Operator,ExportHelper):
    bl_idname='eiem.effect_export';bl_label='导出特效编辑';filename_ext='.effedit.json'
    def execute(self,context):
        try:
            root=active(context);edit=synchronize(root);store(root,edit);assets.write_document(root,edit,self.filepath);return {'FINISHED'}
        except Exception as e:self.report({'ERROR'},str(e));return {'CANCELLED'}
class EFF_OT_effect_build(bpy.types.Operator,ExportHelper):
    bl_idname='eiem.effect_build';bl_label='构建特效资源';filename_ext='.efffxbuild'
    _process=None;_timer=None;_job=None;_log=None;_root=None;_owns_build=False
    def execute(self,context):
        global _BUILD_RUNNING,_EXPORT_JOB
        try:
            if _BUILD_RUNNING:raise ValueError('已有特效正在构建')
            root=active(context);edit=synchronize(root);store(root,edit);compiler=effect_publish.compiler_path(bpy.path.abspath(root.eff_effect.compiler) if root.eff_effect.compiler else '')
            if not compiler.is_file():raise ValueError('请指定配套 AnimeStudio')
            self._job=tempfile.TemporaryDirectory(prefix='EFF-effect-build-');job=Path(self._job.name);assets.write_document(root,edit,job/'edits.json')
            self._log=(job/'build.log').open('w',encoding='utf-8')
            self._process=subprocess.Popen([str(compiler),'--build-eff-effects',edit.state['source'],str(job/'edits.json'),str(Path(self.filepath).resolve())],stdout=self._log,stderr=self._log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            self._root=root;_BUILD_RUNNING=True;self._owns_build=True;_EXPORT_JOB=self;root.eff_effect.status='正在离线构建…';self._timer=context.window_manager.event_timer_add(.1,window=context.window);context.window_manager.modal_handler_add(self)
            return {'RUNNING_MODAL'}
        except Exception as e:self._cleanup(context);self.report({'ERROR'},str(e));return {'CANCELLED'}
    def modal(self,context,event):
        if event.type!='TIMER' or self._process.poll() is None:return {'PASS_THROUGH'}
        good=self._process.returncode==0;self._root.eff_effect.status='构建完成；导出 Mod 后在游戏按特效重载键' if good else '构建失败'
        if good:self._root.eff_effect.build_path=str(Path(self.filepath).resolve())
        else:
            self._log.flush();self.report({'ERROR'},(Path(self._job.name)/'build.log').read_text('utf-8',errors='replace')[-1000:])
        self._cleanup(context);return {'FINISHED'} if good else {'CANCELLED'}
    def _cleanup(self,context):
        global _BUILD_RUNNING,_EXPORT_JOB
        if self._timer:context.window_manager.event_timer_remove(self._timer);self._timer=None
        if self._process and self._process.poll() is None:self._process.terminate();self._process.wait()
        if self._log:self._log.close();self._log=None
        if self._job:self._job.cleanup();self._job=None
        if self._owns_build:_BUILD_RUNNING=False;self._owns_build=False
        if _EXPORT_JOB is self:_EXPORT_JOB=None
    def cancel(self,context):self._cleanup(context)
def attachment(root,part='',addon=None):
    cfg=root.eff_effect;owner=cfg.owner
    stored = root.get('eff_effect_attachment')
    if cfg.usage==REPLACE_GAME_PREFAB:
        target=validate_replacement_prefab_path(cfg.replacement_prefab)
        position,rotation,scale=(BASIS.inverted()@root.matrix_local@BASIS).decompose()
        toggle_keys=[switch_controls.validate_switch_key(item.key) for item in cfg.toggle_keys]
        document.require(len(toggle_keys)==len(set(toggle_keys)),'effect toggle keys must be unique')
        return dict(id=root['eff_effect_id'],label=root.name,mode='replace_game_prefab',
                    replacementPrefab=target,targetPrefab=target,rendererPath='',
                    targetPath='',targetAsset='',bones=[],part='',bone='',autoplay=True,keys=toggle_keys,
                    build=bpy.path.abspath(cfg.build_path),position=list(position),
                    rotation=[rotation.x,rotation.y,rotation.z,rotation.w],scale=list(scale))
    if not owner and stored:
        try:
            declared=json.loads(stored) if isinstance(stored,str) else dict(stored)
        except (TypeError, ValueError) as error:
            raise ValueError(root.name+' attachment declaration is damaged') from error
        for key in ('targetPath','targetAsset','targetPrefab','rendererPath'):
            if not declared.get(key):
                raise ValueError(root.name+' attachment declaration lacks '+key)
        position,rotation,scale=(BASIS.inverted()@root.matrix_local@BASIS).decompose()
        bone=str(cfg.bone or declared.get('bone',''))
        toggle_keys=[switch_controls.validate_switch_key(item.key) for item in cfg.toggle_keys]
        document.require(len(toggle_keys)==len(set(toggle_keys)),'effect toggle keys must be unique')
        row=dict(declared)
        row.update(id=root['eff_effect_id'],label=root.name,
                   mode='mesh_attachment',part=part or declared.get('part',''),bone=bone,autoplay=True,
                   keys=toggle_keys,build=bpy.path.abspath(cfg.build_path),
                   position=list(position),rotation=[rotation.x,rotation.y,rotation.z,rotation.w],
                   scale=list(scale))
        row['lodCoverage']='all' if cfg.all_lods else 'source'
        if not cfg.all_lods:row['lodTargets']=[]
        elif not row.get('lodTargets') and declared.get('lodCoverage')!='all' and mount_targets.LOD_TOKEN_RE.search(row['rendererPath']):
            raise ValueError('旧特效没有跨 LOD 挂点来源，请在挂载目标中重新选择原始 Mesh 后导出')
        return row
    if not owner:raise ValueError(root.name+' 缺少挂载目标 Mesh')
    rig=preview.mount_rig(owner)
    if cfg.bone and (not rig or cfg.bone not in rig.data.bones):raise ValueError('挂载骨骼不属于目标 Mesh 的骨架')
    bone=str(rig.data.bones[cfg.bone].get('eiem_path',cfg.bone)) if cfg.bone else '';p=owner.data
    path=str(p.get('eiem_target_path','') or p.get('eiem_source',''));asset=str(p.get('eiem_target_asset','') or p.get('eiem_asset',''))
    if not path or not asset:raise ValueError('挂载 Mesh 缺少精确游戏来源')
    position,rotation,scale=(BASIS.inverted()@root.matrix_local@BASIS).decompose()
    proof=json.loads((Path(owner.get('eiem_author_package',''))/'source/manifest.json').read_text('utf-8-sig'))
    source=next((r for r in proof.get('resources',[]) if r.get('type')=='Mesh' and r.get('logicalPath')==path and r.get('name')==asset),None)
    uses=[r for r in proof.get('rendererUses',[]) if source and r.get('mesh',{}).get('identity')==source['identity']]
    if not uses:raise ValueError('来源清单没有目标 Mesh 的 Renderer 证明')
    owner_path=str(owner.get('eiem_source_owner_path',''))
    use=next((u for u in uses if u['ownerPath']==owner_path),uses[0] if len(uses)==1 else None)
    if not use:raise ValueError('来源含多个 Mesh 消费者，请明确原生 Renderer 路径')
    paths={h['transform']:h['path'] for h in proof['hierarchy']}
    anchor_identity=(use.get('skinningRoot') or {}).get('identity')
    anchor=paths.get(anchor_identity) if anchor_identity else None
    palette=[]
    source_bones=use.get('bones',[])
    if source_bones and anchor is None:raise ValueError('来源骨骼调色板缺少骨架根证明')
    for ptr in source_bones:
        value=paths.get(ptr.get('identity'))
        if value is None or not (value==anchor or value.startswith(anchor+'/')):raise ValueError('来源骨骼不属于已验证骨架')
        palette.append(value)
    if bone and (not palette or not anchor or not (bone==anchor or bone.startswith(anchor+'/'))):
        raise ValueError('骨骼挂载需要目标 Mesh 的完整骨架调色板')
    toggle_keys=[switch_controls.validate_switch_key(item.key) for item in cfg.toggle_keys]
    document.require(len(toggle_keys)==len(set(toggle_keys)),'特效显隐按键不能重复')
    targets=mount_targets.lod_targets(proof,use) if cfg.all_lods else []
    return dict(id=root['eff_effect_id'],label=root.name,mode='mesh_attachment',part=part,bone=bone,autoplay=True,keys=toggle_keys,build=bpy.path.abspath(cfg.build_path),position=list(position),rotation=[rotation.x,rotation.y,rotation.z,rotation.w],scale=list(scale),targetPath=path,targetAsset=asset,bones=palette,targetPrefab=proof['prefab'],rendererPath=use['ownerPath'],lodCoverage='all' if cfg.all_lods else 'source',lodTargets=targets,lodAnchor=mount_targets.lod_anchor(proof,use,targets,bone,mesh_space=True))
def export_attachments(meshes,parts,addon,effect_roots=()):
    selected={addon.mesh_export_template(obj) for obj in meshes};result=[]
    declared={part['partId'] for part in parts}
    selections=export_selections(effect_roots,meshes,addon)
    for selection in selections:
        root=selection.root
        require_publishable_effect_usage(root)
        owner=root.eff_effect.owner;part=''
        if owner in selected:
            splits=list(addon.native_export.split_material(addon.native_export._cached_mesh_snapshot(addon,owner)[0]))
            pid=addon.native_export.mesh_part_id(owner.name,splits[0][0],len(splits))
            document.require(pid in declared,root.name+' 的挂载网格未包含在导出部件中')
            part='Part'+pid.removeprefix('Mesh')
        request=ensure_built(root,export_payload(selection))
        row=attachment(root,part,addon);row['_authorEdit']=request.portable_payload
        from . import eiem_effect_author_resources
        row['_native']=effect_codec().read_native_build(request.output)
        row['_resources']=eiem_effect_author_resources.built_resources(request.resources,request.output)
        result.append(row)
    from . import eiem_effect_mounts
    eiem_effect_mounts.assemble(export_roots(effect_roots,meshes,addon),result,addon.shape_animation,BASIS)
    return result

def effect_codec():
    from . import eiem_native_export
    modules,_=eiem_native_export.pack_modules()
    return modules['effect_native']

def effect_cache():
    return bpy.utils.user_resource('DATAFILES',path='eiem/effect-builds',create=True)

def build_request(root,payload=None):
    if payload is None:payload=assets.export_document(root,synchronize(root))
    from . import eiem_effect_author_resources
    compiled,resources=eiem_effect_author_resources.externalize(synchronize(root),payload)
    compiler=effect_publish.compiler_path(bpy.path.abspath(root.eff_effect.compiler) if root.eff_effect.compiler else '')
    return effect_publish.Build(payload,compiler,effect_cache(),root['eff_effect_id'],effect_codec().read_native_build,
                                compiled_payload=compiled,resources=resources)

def ensure_built(root,payload=None):
    if _BUILD_RUNNING:raise ValueError('已有特效正在构建，请完成后再导出人物')
    # A full export uses current author values too; an old manual build path
    # cannot silently publish a stale version of the effect.
    attachment(root)
    request=build_request(root,payload);root.eff_effect.build_path=str(request.run())
    return request

class ExportBatch:
    """One selection, one destination, one publication for all effect roots."""
    def __init__(self,roots,destination):
        self.selections=export_selections(roots);self.roots=[s.root for s in self.selections];self.destination=Path(destination).resolve()
        self.requests=[];self.states=[];self.index=0;self.current=None;self.stats=None;self.owns_build=False
        self.started=time.perf_counter()
        self.costs=dict(author=0.,compile=0.,verify=0.,publish=0.);self.compile_started=0.
        self.snapshot=assets.ExportSnapshot()
        document.require(self.roots,'请选择 EFF 网格或特效根 / 发射器')
        document.require(not _BUILD_RUNNING,'已有特效正在构建')
        for selection in self.selections:
            root=selection.root
            require_publishable_effect_usage(root)
            attachment(root)
            edit=synchronize(root);store(root,edit)
            payload=export_payload(selection,self.snapshot)
            # The clean baseline is loaded separately; only its source path is
            # needed here, not a second deep copy of the entire edited catalog.
            self.states.append(dict(source=edit.state['source']))
            self.requests.append(build_request(root,payload))
        self.costs['author']=(time.perf_counter()-self.started)*1000

    def poll(self):
        global _BUILD_RUNNING,_EXPORT_JOB
        if not self.owns_build:
            document.require(not _BUILD_RUNNING,'已有特效正在构建')
            _BUILD_RUNNING=True;_EXPORT_JOB=self;self.owns_build=True
        if self.current:
            if self.current.process.poll() is None:return False
            # The final input signature is checked once, immediately before
            # publishing. AnimeStudio also validates the source while building.
            self.current.finish(validate_inputs=False);self.current.close();self.current=None;self.index+=1
            self.costs['compile']+=(time.perf_counter()-self.compile_started)*1000
        while self.index<len(self.requests):
            request=self.requests[self.index]
            if request.cached():self.index+=1;continue
            self.current=request;self.compile_started=time.perf_counter();request.start();return False
        phase=time.perf_counter()
        rows=[]
        from . import eiem_effect_export, eiem_format
        effect_baselines={}
        for selection,request,state in zip(self.selections,self.requests,self.states):
            root=selection.root
            current=document.dumps(export_payload(selection,self.snapshot)).encode('utf-8')
            document.require(current==request.author,'构建期间特效有修改，请再次导出；上一 Mod 保留')
            row=attachment(root);row['part']='';row['build']=str(request.output)
            row['_native']=effect_codec().read_native_build(request.output)
            from . import eiem_effect_author_resources
            row['_resources']=eiem_effect_author_resources.built_resources(request.resources,request.output)
            row['_source']=state['source']
            source=state['source']
            if source not in effect_baselines:
                effect_baselines[source]=document.load_source(source)
            rows.append(row)
        from . import eiem_effect_mounts, eiem_shape_animation
        eiem_effect_mounts.assemble(self.roots,rows,eiem_shape_animation,BASIS)
        for row,request,state in zip(rows,self.requests,self.states):
            row['_authorData']=eiem_format.encode_format3_effect_author(state['source'],effect_baselines[state['source']],{k:v for k,v in row.items() if not k.startswith('_')},request.portable_payload)
        eiem_effect_mounts.compile_clips(self.roots,rows)
        self.costs['author']+=(time.perf_counter()-phase)*1000
        phase=time.perf_counter()
        for request in self.requests:
            document.require(effect_publish.inputs_signature(request.payload,request.compiler)==request.inputs,
                             '构建后特效来源或 AnimeStudio 已改变，请重新导出；上一 Mod 保留')
        self.costs['verify']=(time.perf_counter()-phase)*1000
        phase=time.perf_counter()
        character_update=eiem_effect_export.publish(self.destination,rows)
        self.costs['publish']=(time.perf_counter()-phase)*1000
        elapsed=time.perf_counter()-self.started
        for root,request in zip(self.roots,self.requests):
            root.eff_effect.build_path=str(request.output)
            root.eff_effect.status=('已更新角色包特效' if character_update else '已导出特效')+'（%.2f 秒%s）'%(elapsed,'，复用构建' if request.reused else '')
        self.stats=dict(meshes=0,materials=0,textures=0,effects=len(rows),exportSeconds=elapsed,
            resourceScope='ALL',effectOnly=True,characterEffectUpdate=character_update,effectBuilds=sum(not r.reused for r in self.requests),
            exportCostsMs=dict(self.costs))
        print('[EFF] 特效导出：整理 %.2fs / 构建 %.2fs / 校验 %.2fs / 发布 %.2fs；复用 %d / 构建 %d'%(
            self.costs['author']/1000,self.costs['compile']/1000,self.costs['verify']/1000,self.costs['publish']/1000,
            sum(r.reused for r in self.requests),self.stats['effectBuilds']))
        self.close();return True

    def run(self):
        try:
            while not self.poll():
                self.current.process.wait()
            return self.stats
        finally:self.close()

    def close(self):
        global _BUILD_RUNNING,_EXPORT_JOB
        for request in self.requests:request.close()
        if self.owns_build:
            _BUILD_RUNNING=False;self.owns_build=False
        if _EXPORT_JOB is self:_EXPORT_JOB=None

    def cancel(self,context=None):self.close()
class EFF_OT_effect_preview(bpy.types.Operator):
    bl_idname='eiem.effect_preview';bl_label='刷新离线预览';bl_options={'UNDO'}
    def execute(self,context):
        try:
            root=active(context);edit=synchronize(root);store(root,edit);preview.refresh(root,edit);return {'FINISHED'}
        except Exception as e:self.report({'ERROR'},str(e));return {'CANCELLED'}
class EFF_OT_effect_preview_seek(bpy.types.Operator):
    bl_idname='eiem.effect_preview_seek';bl_label='查看发射时刻'
    bl_description='从开头查找有存活粒子的时间帧；只移动时间线，不修改原生发射时序'
    def execute(self,context):
        try:
            root=active(context);document.require(root,'请选择特效根或发射器')
            edit=synchronize(root);scene=context.scene;fps=scene.render.fps/scene.render.fps_base
            systems=[];horizon=0
            for obj in objects(root):
                ids=json.loads(obj.get('eff_effect_components','[]'))
                if obj.get('eff_preview'):continue
                renderer=next((i for i in ids if edit.objects[i]['classId']==199),None)
                if not renderer or not edit.value(renderer).get('m_Enabled',True):continue
                for identity in ids:
                    if edit.objects[identity]['classId']!=198:continue
                    data=edit.value(identity);speed=data.get('simulationSpeed',1)
                    if speed<=0:continue
                    systems.append((obj,data,renderer))
                    delay=preview.sim.scalar(data.get('startDelay',{}))
                    horizon=max(horizon,(delay+data.get('lengthInSec',5))/speed)
            document.require(systems,'此特效没有可预览的粒子发射器，请使用时间线检查网格或来源动画')
            chosen=None
            # Bound work for arbitrarily long systems, retaining individual
            # frames for normal short effects. Never change native duration.
            last=max(1,math.ceil(horizon*fps));step=max(1,math.ceil(last/600))
            for offset in range(1,last+1,step):
                time_value=offset/fps;frame=preview.playback.Frame(root,edit,time_value)
                for obj,data,renderer in systems:
                    if not frame.visible(obj):continue
                    placement=frame.placement(obj)
                    effective=preview.effective_particle_data(edit,obj,data,placement)
                    sampled=preview.sim.particles(effective,time_value,seed=document.seed(root['eff_effect_id']))
                    if any(p['color'][3]>.01 and max(abs(v) for v in p['sizes'])>1e-5 for p in sampled):
                        chosen=scene.frame_start+offset;break
                if chosen is not None:break
            document.require(chosen is not None,'本轮未找到非零大小和透明度的粒子，请检查发射、寿命和颜色曲线')
            root.eff_effect.preview=True
            scene.frame_set(chosen)
            preview.refresh(root,edit);preview.prime_signature(root,edit)
            for area in context.screen.areas if context.screen else ():
                if area.type=='VIEW_3D':
                    area.spaces.active.shading.type='MATERIAL'
                    area.spaces.active.overlay.show_face_orientation=False
            self.report({'INFO'},'已定位第 %d 帧；播放时间线查看完整周期'%chosen)
            return {'FINISHED'}
        except Exception as e:self.report({'ERROR'},str(e));return {'CANCELLED'}


class EFF_OT_effect_mount_select(bpy.types.Operator):
    bl_idname='eiem.effect_mount_select';bl_label='切换挂点 / 特效内容'
    root_name:StringProperty(options={'HIDDEN'})
    direction:EnumProperty(items=[('MOUNT','编辑挂点','位置、旋转、缩放和整体动画'),('EFFECT','编辑特效内容','发射器、网格、材质和粒子曲线')])
    def execute(self,context):
        from . import eiem_effect_mounts as mounts
        try:
            root=context.scene.objects.get(self.root_name)
            document.require(root and root.get('eff_effect_document'),'关联特效不在当前场景')
            document.require(root_of(context.object)==root or root in mounts.linked_effects(context.object),'当前对象不属于该特效或它的挂点')
            target=root.eff_effect.mount if self.direction=='MOUNT' else root
            document.require(target and context.view_layer.objects.get(target.name)==target,'挂点不在当前视图层；请先设置挂载 Empty')
            for obj in context.selected_objects:obj.select_set(False)
            target.hide_set(False);target.select_set(True);context.view_layer.objects.active=target
            return {'FINISHED'}
        except Exception as e:self.report({'ERROR'},str(e));return {'CANCELLED'}


def draw_preview_controls(layout,root):
    cfg=root.eff_effect;box=layout.box();box.label(text='Blender 预览 · 不影响随 Mod 导出',icon='HIDE_OFF')
    row=box.row(align=True);row.prop(cfg,'preview');row.prop(cfg,'show_shape')
    if cfg.preview:
        count=sum(int(o.get('预览粒子数',0)) for o in root.children_recursive if o.get('eff_preview_geometry_nodes') and not o.hide_viewport)
        box.label(text='第 %d 帧 · 存活粒子 %d'%(bpy.context.scene.frame_current,count))
        if not count:effect_ui.hint(box,'当前无存活粒子：爆发特效有发射间隔，请播放时间线或查看发射时刻。',icon='INFO')
    else:box.label(text='此组预览已关闭',icon='HIDE_ON')
    row=box.row(align=True);row.operator('eiem.effect_preview',text='刷新当前帧',icon='FILE_REFRESH');row.operator('eiem.effect_preview_seek',icon='PREVIEW_RANGE')
    if root.get('预览错误'):effect_ui.hint(box,str(root['预览错误']),icon='ERROR')
    if root.eff_effect.usage==MESH_ATTACHMENT:
        mount=layout.box();mount.label(text='挂载与运动 · 整组特效的位置 / 动画',icon='EMPTY_AXIS')
        mount.label(text='目标 Mesh：'+(cfg.owner.name if cfg.owner else '未设置'))
        mount.label(text='挂点：'+(cfg.mount.name if cfg.mount else '未设置（跟随目标 Mesh / 骨骼）'))
        if cfg.mount:
            op=mount.operator('eiem.effect_mount_select',text='编辑挂点变换 / 动画',icon='EMPTY_AXIS');op.root_name=root.name;op.direction='MOUNT'
        effect_ui.hint(mount,'下面编辑特效内容；挂点的位置和 K 帧不在发射器模块里。')


class EFF_PT_effect_mount(bpy.types.Panel):
    bl_label='EFF 特效挂点 · 位置与动画';bl_idname='EFF_PT_effect_mount'
    bl_space_type='PROPERTIES';bl_region_type='WINDOW';bl_context='data';bl_order=-101
    @classmethod
    def poll(cls,context):
        from . import eiem_effect_mounts
        return bool(eiem_effect_mounts.linked_effects(context.object))
    def draw(self,context):
        from . import eiem_effect_mounts
        obj=context.object;layout=self.layout
        layout.use_property_split=False;layout.use_property_decorate=False
        layout.label(text='当前编辑：挂点 · '+obj.name,icon='EMPTY_AXIS')
        effect_ui.hint(layout,'移动或 K 帧影响关联特效的整体位置、旋转与缩放；此对象不是发射器。')
        layout.prop(obj,'location');layout.prop(obj,'rotation_quaternion' if obj.rotation_mode=='QUATERNION' else 'rotation_axis_angle' if obj.rotation_mode=='AXIS_ANGLE' else 'rotation_euler');layout.prop(obj,'scale')
        layout.label(text='关联特效 · 发射器 / 网格 / 材质',icon='PARTICLES')
        for root in eiem_effect_mounts.linked_effects(obj):
            op=layout.operator('eiem.effect_mount_select',text='编辑特效内容 · '+root.name,icon='PARTICLES');op.root_name=root.name;op.direction='EFFECT'
        effect_ui.hint(layout,'挂点可在统一 EFF 动画面板中使用 Action；修改粒子请进入上面的特效内容。')

def draw_value(layout,obj,edit,identity,path,value):
    if isinstance(value,dict) and ('m_Curve' in value or ('minMaxState' in value and 'maxCurve' in value)):
        effect_ui.Draw(sys.modules[__name__],obj,edit,identity).value(layout,path);return
    name=label(path[-1]);schema=edit.schema(identity,path)
    if schema['type'].startswith('PPtr<'):
        draw_pointer(layout,edit,identity,path,value,title=name+' ['+schema['type']+']')
        return
    if isinstance(value,(dict,list)):
        if isinstance(value,dict) and value.get('encoding') in ('base64','author-payload'):
            layout.label(text=name+' [原生字节，保留]');return
        header,body=layout.panel(field_key(identity,path),default_closed=True);header.label(text=name+(' ['+str(len(value))+']' if isinstance(value,list) else ''))
        if isinstance(value,dict) and 'enabled' in value:
            prop=field_key(identity,path+['enabled'])
            if prop in obj:header.prop(obj,'["'+prop+'"]',text='')
        if body:
            if isinstance(value,list):
                if edit.editable(identity,path):
                    op=body.operator('eiem.effect_array',text='添加条目',icon='ADD');op.identity=identity;op.path=document.dumps(path);op.operation='ADD'
                for i,child in enumerate(value):
                    if edit.editable(identity,path):
                        row=body.row()
                        for operation,icon in (('COPY','DUPLICATE'),('REMOVE','X')):
                            op=row.operator('eiem.effect_array',text='',icon=icon);op.identity=identity;op.path=document.dumps(path);op.operation=operation;op.index=i
                    draw_value(body,obj,edit,identity,path+[i],child)
            else:
                for key,child in value.items():
                    if key!='enabled':draw_value(body,obj,edit,identity,path+[key],child)
        return
    key=field_key(identity,path);row=layout.row(align=True)
    if key in obj:effect_ui.Draw(sys.modules[__name__],obj,edit,identity).prop(row,path)
    else:row.label(text=name+' ['+schema['type']+'] '+str(value))
def draw_structure(layout,obj,root,edit):
    def ids(target):return json.loads(target.get('eff_effect_components','[]'))
    def is_emitter(target):return any(edit.objects[i]['classId']==198 for i in ids(target))
    def select(body,target,title,icon):
        row=body.row(align=True);row.enabled=target!=obj
        op=row.operator('eiem.effect_select',text=title,icon=icon);op.object_name=target.name
    role='特效根' if obj==root else ('发射器' if is_emitter(obj) else ('粒子网格' if obj.get('eff_particle_template') else ('材质' if obj.get('eff_effect_resource') else '节点')))
    layout.label(text='当前编辑：'+role+' · '+obj.name)
    if is_emitter(obj):
        op=layout.operator('eiem.effect_curve_edit',text='编辑全部曲线',icon='GRAPH');op.object_name=obj.name
    row=layout.row(align=True)
    row.operator('eiem.effect_emitter_add',text='添加发射器…',icon='ADD')
    if is_emitter(obj):
        op=row.operator('eiem.effect_emitter_copy',text='',icon='COPYDOWN');op.object_name=obj.name;op.identity=obj.get('eff_effect_node','')
        op=row.operator('eiem.effect_emitter_remove',text='删除发射器',icon='X');op.object_name=obj.name
    if obj==root or is_emitter(obj):
        op=row.operator('eiem.effect_emitter_paste',text='',icon='PASTEDOWN');op.object_name=obj.name
    if obj!=root:select(layout,root,'返回特效根：挂载 / 导出','EMPTY_AXIS')
    if obj==root:
        # List the live emitters directly, including those below source helper
        # GameObjects. Technical hierarchy foldouts must not hide author entry points.
        emitters=[target for target in objects(root) if is_emitter(target)]
        quick=layout.box();quick.label(text='发射器 · '+str(len(emitters)),icon='PARTICLES')
        for target in emitters:
            renderer=next((i for i in ids(target) if edit.objects[i]['classId']==199),None)
            data=edit.state['values'].get(renderer,{})
            mode=int(target.get(field_key(renderer,['m_RenderMode']),data.get('m_RenderMode',-1))) if renderer else -1
            modes=effect_ui.ENUMS['m_RenderMode']
            kind=modes[mode] if 0<=mode<len(modes) else 'ParticleSystem'
            select(quick,target,target.name+' · '+kind,'MESH_DATA' if mode==4 else 'PARTICLES')
        if not emitters:effect_ui.hint(quick,'暂无发射器，点击上方“添加发射器…”创建。')
    header,body=layout.panel('eff_structure',default_closed=True)
    header.label(text='GameObject 层级 · 点击切换编辑对象')
    if body:
        effect_ui.hint(body,'节点和组件来自原 Prefab；ParticleSystem 控制粒子，Renderer 控制绘制。')
        select(body,root,'特效根 · '+root.name,'EMPTY_AXIS')
        nodes={o.get('eff_effect_node'):o for o in objects(root) if o.get('eff_effect_node')}
        for node in edit.state['catalog']['hierarchy']:
            target=nodes.get(node['identity'])
            if target is None or target==root:continue
            row=body.row(align=True)
            # Preserve the actual Prefab parent/child relationship in the navigator.
            depth=0;parent=target.parent
            while parent and parent!=root:depth+=1;parent=parent.parent
            if depth:row.separator(factor=min(depth,4))
            select(row,target,('发射器' if is_emitter(target) else 'GameObject')+' · '+target.name,'OUTLINER_OB_EMPTY')
        materials=[o for o in objects(root) if o.get('eff_effect_resource') and any(edit.objects[i]['classId']==21 for i in ids(o))]
        if materials:
            body.separator();effect_ui.hint(body,'资源入口：Renderer → m_Materials → Material → m_TexEnvs → Texture。')
            for target in materials:select(body,target,'材质 · '+target.name,'MATERIAL')
        meshes=[o for o in objects(root) if o.get('eff_particle_template') and o.type=='MESH']
        if meshes:
            body.separator();effect_ui.hint(body,'网格可直接编辑或合并模型；材质在网格的材质属性中编辑。')
            for target in meshes:select(body,target,'Mesh · '+target.name,'MESH_DATA')
        if not root.get('eff_effect_assets_version'):
            body.operator('eiem.effect_assets_expand',icon='MESH_DATA')
        effect_ui.hint(body,'选择发射器只编辑该发射器；选择根可查看全部发射器。')
    header,body=layout.panel('eff_edit_guide',default_closed=True)
    header.label(text='怎么改 · 原生字段速查')
    if body:
        for text in ('出生区域 → ParticleSystem → ShapeModule',
                     '数量 → EmissionModule → rateOverTime / m_Bursts',
                     '出生寿命 / 随机旋转 → InitialModule',
                     '系统周期 / 循环 → ParticleSystem 系统字段',
                     '渐隐 → ColorModule → 渐变 Alpha',
                     '面片 / Mesh → ParticleSystemRenderer → m_RenderMode',
                     '贴图 → Renderer → m_Materials → Material → m_SavedProperties → m_TexEnvs'):
            effect_ui.hint(body,text)
        effect_ui.hint(body,'模块开关控制是否生效，标题左侧三角控制展开。每种模块仅一份；关闭保留全部参数。',icon='INFO')

class EFF_PT_effect_object(bpy.types.Panel):
    bl_label='EFF 特效 · 原生结构';bl_idname='EFF_PT_effect_object';bl_space_type='PROPERTIES';bl_region_type='WINDOW';bl_context='data'
    bl_order=-100
    @classmethod
    def poll(cls,context):return bool(context.object and root_of(context.object) and (context.object.type=='EMPTY' or context.object.get('eff_particle_template')))
    def draw(self,context):
        layout=self.layout;obj=context.object;root=root_of(obj)
        try:
            edit=editor(root)
            layout.use_property_split=False;layout.use_property_decorate=False
            if obj==root:draw_preview_controls(layout,root)
            draw_structure(layout,obj,root,edit)
            warnings=str(root.get('预览未还原模块',''))
            if warnings and warnings!='无额外模块':
                effect_ui.hint(layout,'预览差异提示：'+warnings,icon='INFO')
            if obj.get('eff_particle_template'):
                effect_ui.hint(layout,'Tab 编辑顶点 / UV；合并模型时让此网格为最后选中的活动对象。')
                effect_ui.hint(layout,'游戏材质沿用此网格的 EFF 材质槽；在材质属性中修改贴图和参数。')
                return
            if obj==root:
                cfg=root.eff_effect
                for key,title in (('timeline','原始动画 / VAT'),('mount','用途与挂载'),('export','构建工具'),('diagnostics','预览支持与来源信息')):
                    header,body=layout.panel('eff_'+key,default_closed=True);header.label(text=title)
                    if not body:continue
                    if key=='timeline':
                        body.prop(cfg,'source_animation');body.prop(cfg,'preview_clip');body.prop(cfg,'clip_loop');body.prop(cfg,'vat_preview');body.prop(cfg,'preview_particles');body.prop(cfg,'trail_preview')
                        clip=preview.playback.session(edit).clips(edit).clips.get(cfg.preview_clip)
                        if clip:body.label(text='%.3f s · %g fps · %d 标量轨道'%(clip.stop-clip.start,clip.rate,clip.total))
                        body.operator('eiem.effect_research_report',icon='TEXT')
                        effect_ui.hint(body,'使用 Blender 时间轴；片段覆盖只作用于预览，不会写回作者参数。')
                        effect_ui.hint(body,'独立片段不代表完整技能；Animator 状态切换、游戏脚本和原像素 Shader 仍需核对。')
                    elif key=='mount':
                        body.prop(cfg,'usage',text='特效用途')
                        if cfg.usage==MESH_ATTACHMENT:
                            body.prop(cfg,'enabled');body.prop(cfg,'all_lods');body.prop(cfg,'owner');body.prop(cfg,'mount')
                            if cfg.all_lods:effect_ui.hint(body,'目标的全部已知 LOD 共用稳定挂点与一次播放；同时解除特效自身的距离/画质降级。')
                            rig=preview.mount_rig(cfg.owner)
                            if rig:body.prop_search(cfg,'bone',rig.data,'bones',text='挂载骨骼 (留空为 Renderer)')
                            else:body.prop(cfg,'bone',text='挂载骨骼 (留空为 Renderer)')
                            body.label(text='仅在命中该 Mesh 时挂载；不会替换游戏原特效')
                            effect_ui.hint(body,'选择 Empty 可添加可动画挂点；留空跟随 Renderer/骨骼。导出自动收集挂点，无需额外选中。')
                            key_box=body.box();key_header=key_box.row(align=True);key_header.label(text='特效显隐按键')
                            op=key_header.operator('eiem.effect_key',text='录制按键',icon='REC');op.action='ADD';op.root_name=root.name
                            for index,item in enumerate(cfg.toggle_keys):
                                row=key_box.row(align=True);row.label(text=item.key,icon='EVENT_F')
                                op=row.operator('eiem.effect_key',text='',icon='X');op.action='REMOVE';op.root_name=root.name;op.index=index
                            if not cfg.toggle_keys:effect_ui.hint(key_box,'未设置时默认随挂载 Mesh 显示；录制多个按键后，任一按键都会切换此特效。')
                        else:
                            body.prop(cfg,'replacement_prefab',text='目标游戏 Prefab')
                            body.label(text='会影响该逻辑 Prefab 的所有调用',icon='INFO')
                            effect_ui.hint(body,'导出按此逻辑 Prefab 命中，在游戏加载该特效时替换；播放和挂点由游戏调用者管理。')
                    elif key=='export':
                        effect_ui.hint(body,'选中一个或多个特效，用“文件 → 导出 → EFF Mod 包”统一导出。')
                        advanced,settings=body.panel('eff_export_advanced',default_closed=True);advanced.label(text='编译器 / 编辑数据')
                        if settings:
                            settings.prop(cfg,'compiler');settings.prop(cfg,'build_path')
                            settings.operator('eiem.effect_build');settings.operator('eiem.effect_export');settings.operator('eiem.effect_preview')
                        if cfg.status:body.label(text=cfg.status[:150])
                    else:
                        for name in ('预览未还原模块','预览资源提示','预览贴图槽','预览遮罩槽'):
                            if root.get(name):body.label(text=name+'：'+str(root[name])[:180],icon='INFO')
            hierarchy=edit.state['catalog']['hierarchy']
            source_nodes={node['identity']:node for node in hierarchy}
            if obj==root:
                nodes={o.get('eff_effect_node'):o for o in objects(root) if o.get('eff_effect_node')}
                targets=[nodes[node['identity']] for node in hierarchy if node['identity'] in nodes]
            else:targets=[obj]
            for target in targets:
                target_layout=layout
                node=source_nodes.get(target.get('eff_effect_node'))
                if obj==root and target!=root:
                    heading,target_layout=layout.panel('node_'+str(target.as_pointer()),default_closed=True)
                    heading.label(text='GameObject · '+target.name)
                    if not target_layout:continue
                ids=([identity for identity in node['components'] if edit.objects[identity]['classId']!=1]
                     if node else json.loads(target.get('eff_effect_components','[]')))
                if any(edit.objects[i]['classId']==198 for i in ids):
                    op=target_layout.operator('eiem.effect_block',text='启用模块…',icon='ADD');op.object_name=target.name
                for identity in ids:
                    spec=edit.objects[identity]
                    script=spec.get('script') or {}
                    kind=spec.get('type','组件')
                    title=kind
                    if script.get('class'):
                        title+=' · '+'.'.join(part for part in (script.get('namespace'),script['class']) if part)
                    if identity not in edit.state['values'] or spec['classId'] not in document.EDITABLE:title+='（只读保留）'
                    component_header,component_body=target_layout.panel(
                        'component_'+field_key(identity,['_source_component']),default_closed=True)
                    component_header.label(text=title)
                    if component_body:
                        if identity in edit.state['values']:
                            effect_ui.Draw(sys.modules[__name__],target,edit,identity).component(component_body,spec)
                        else:
                            effect_ui.hint(component_body,'来源未解析，原生数据原样保留：'+str(spec.get('reason') or spec.get('state') or kind),icon='INFO')
        except Exception as e:layout.label(text=str(e)[:180],icon='ERROR')

class EFF_PT_effect_empty_create(bpy.types.Panel):
    bl_label='EFF 特效 · 创建根';bl_idname='EFF_PT_effect_empty_create';bl_space_type='PROPERTIES';bl_region_type='WINDOW';bl_context='object'
    bl_order=100
    @classmethod
    def poll(cls,context):
        obj=context.object
        return bool(obj and obj.type=='EMPTY' and not root_of(obj))
    def draw(self,context):
        obj=context.object;layout=self.layout
        layout.label(text='将当前 Empty 转为特效根：'+obj.name,icon='OUTLINER_OB_EMPTY')
        op=layout.operator('eiem.effect_create_blank',text='创建特效根…',icon='PARTICLES')
        op.host_object_name=obj.name
        effect_ui.hint(layout,'选择同版本特效来源作为类型模板；可在向导中指定挂载目标 Mesh。')

@bpy.app.handlers.persistent
def save_pre(_):
    for root in bpy.data.objects:
        if root.get('eff_effect_document'):store(root,synchronize(root))
@bpy.app.handlers.persistent
def clear_cache(*_):_CACHE.clear();_FIELD_ROWS.clear();_field_key.cache_clear();preview.clear_cache();curve_editor.clear_cache();gradient_editor.clear_cache()

@bpy.app.handlers.persistent
def upgrade_loaded(*_):
    for root in bpy.data.objects:
        if not root.get('eff_effect_document'):continue
        edit=editor(root);members=objects(root)
        for obj in members:
            gradient_editor.prepare(obj,edit,json.loads(obj.get('eff_effect_components','[]')))
        # Prepare degree controls without replacing any native author value.
        for obj in members:
            if obj.get('eff_effect_angles_version')==1:continue
            for field in field_rows(obj,edit):
                if effect_ui.angle_unit(field['path']) and obj.eff_effect_angles.get(field['key']) is None:
                    angle=obj.eff_effect_angles.add();angle.name=field['key']
            obj['eff_effect_angles_version']=1
        for obj in members:
            for identity in json.loads(obj.get('eff_effect_components','[]')):
                if edit.objects[identity]['classId']==21:
                    assets.setup_textures(root,edit,obj,identity)
                    material_bridge.ensure_material(root,edit,identity)
        if root.get('eff_effect_ui_version',0)>=2:
            preview.schedule_refresh(root)
            continue
        # Migrate the old raw-only UI in memory, retaining pending native edits.
        edit=synchronize(root);store(root,edit);refresh_fields(root);root['eff_effect_ui_version']=2
        preview.schedule_refresh(root)
CLASSES=(EFF_PG_effect_choice,EFF_PG_effect_angle,EFF_PG_effect_key,EFF_PG_effect_object,EFF_PG_effect_texture,EFF_OT_effect_import,EFF_OT_effect_create_blank,EFF_OT_effect_edit_import,EFF_OT_effect_research_report,EFF_OT_effect_block,EFF_OT_effect_array,EFF_OT_effect_mask_toggle,EFF_OT_effect_bool_toggle,EFF_OT_effect_module_copy,EFF_OT_effect_module_paste,EFF_OT_effect_emitter_copy,EFF_OT_effect_emitter_paste,EFF_OT_effect_use_particle_emission,EFF_OT_effect_continuous,EFF_OT_effect_emitter_add,EFF_OT_effect_emitter_remove,EFF_OT_effect_pointer,EFF_OT_effect_resource,EFF_OT_effect_select,EFF_OT_effect_key,EFF_OT_effect_texture_open,EFF_OT_effect_mesh_edit,EFF_OT_effect_assets_expand,EFF_OT_effect_export,EFF_OT_effect_build,EFF_OT_effect_preview,EFF_PT_effect_object,EFF_PT_effect_empty_create)
CLASSES+=(EFF_OT_effect_preview_seek,EFF_OT_effect_mount_select,EFF_PT_effect_mount)
CLASSES+=(EFF_OT_effect_mesh_place,)

def menu_import(self,context):
    self.layout.operator('eiem.effect_import',text='EFF 特效（空物体）')
    self.layout.operator('eiem.effect_edit_import',text='EFF 特效编辑（.effedit.json）')
def menu_add(self,context):self.layout.operator('eiem.effect_import',text='EFF 特效来源',icon='EMPTY_AXIS')
def register():
    for cls in CLASSES:bpy.utils.register_class(cls)
    bpy.types.Object.eff_effect=PointerProperty(type=EFF_PG_effect_object)
    bpy.types.Object.eff_effect_choices=CollectionProperty(type=EFF_PG_effect_choice)
    bpy.types.Object.eff_effect_angles=CollectionProperty(type=EFF_PG_effect_angle)
    bpy.types.Object.eff_effect_textures=CollectionProperty(type=EFF_PG_effect_texture)
    bpy.types.Object.eff_effect_material=PointerProperty(type=bpy.types.Material)
    bpy.types.Material.eff_effect_author=PointerProperty(type=bpy.types.Object)
    bpy.types.TOPBAR_MT_file_import.append(menu_import);bpy.types.VIEW3D_MT_add.append(menu_add)
    bpy.app.handlers.save_pre.append(save_pre)
    for handlers in (bpy.app.handlers.load_post,bpy.app.handlers.undo_post,bpy.app.handlers.redo_post):handlers.append(clear_cache)
    bpy.app.handlers.load_post.append(upgrade_loaded)
    # Add-on enable uses _RestrictData. Migrate after registration completes.
    bpy.app.timers.register(upgrade_loaded,first_interval=.1)
    preview.register(synchronize,root_of)
    curve_editor.register()
    gradient_editor.register()
def unregister():
    if _EXPORT_JOB:_EXPORT_JOB.cancel(bpy.context)
    if bpy.app.timers.is_registered(clear_mesh_pick_slots):bpy.app.timers.unregister(clear_mesh_pick_slots)
    clear_mesh_pick_slots()
    gradient_editor.unregister()
    curve_editor.unregister()
    preview.unregister();_CACHE.clear()
    if bpy.app.timers.is_registered(upgrade_loaded):bpy.app.timers.unregister(upgrade_loaded)
    _FIELD_ROWS.clear();_field_key.cache_clear()
    if upgrade_loaded in bpy.app.handlers.load_post:bpy.app.handlers.load_post.remove(upgrade_loaded)
    for handlers,fn in ((bpy.app.handlers.save_pre,save_pre),(bpy.app.handlers.load_post,clear_cache),(bpy.app.handlers.undo_post,clear_cache),(bpy.app.handlers.redo_post,clear_cache)):
        if fn in handlers:handlers.remove(fn)
    bpy.types.TOPBAR_MT_file_import.remove(menu_import);bpy.types.VIEW3D_MT_add.remove(menu_add);del bpy.types.Object.eff_effect;del bpy.types.Object.eff_effect_choices;del bpy.types.Object.eff_effect_angles;del bpy.types.Object.eff_effect_textures;del bpy.types.Object.eff_effect_material;del bpy.types.Material.eff_effect_author
    for cls in reversed(CLASSES):bpy.utils.unregister_class(cls)
