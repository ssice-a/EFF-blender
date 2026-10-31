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
    from . import eiem_effect_curve_editor as curve_editor
    from . import eiem_effect_gradient_editor as gradient_editor
    from . import eiem_effect_publish as effect_publish
else:
    import eiem_blender_controls as switch_controls
    import eiem_effect_document as document
    import eiem_effect_preview as preview
    import eiem_effect_ui as effect_ui
    import eiem_effect_assets as assets
    import eiem_effect_material as material_bridge
    import eiem_effect_topology as topology
    import eiem_effect_curve_editor as curve_editor
    import eiem_effect_gradient_editor as gradient_editor
    import eiem_effect_publish as effect_publish
_CACHE={}
_BUILD_RUNNING=False
_EXPORT_JOB=None
_ENUM_CACHE={}
_FIELD_ROWS={}
BASIS=Matrix(((-1,0,0,0),(0,0,-1,0),(0,1,0,0),(0,0,0,1)))

def root_of(obj):
    while obj:
        if obj.get('eff_effect_document'):return obj
        obj=obj.parent
    return None
def active(context):
    root=root_of(context.object)
    material=getattr(context,'material',None)
    return root or (root_of(material.eff_effect_author) if material and material.eff_effect_author else None)

def export_roots(selected=(),meshes=(),addon=None):
    """Resolve selection once; emitters/resources select their entire effect."""
    roots={root_of(obj) for obj in selected}
    roots.discard(None)
    if meshes:
        owners={addon.mesh_export_template(obj) for obj in meshes}
        roots.update(root for root in bpy.context.scene.objects
            if root.get('eff_effect_document') and root.eff_effect.enabled
            and root.eff_effect.owner in owners)
    result=sorted(roots,key=lambda root:root.name)
    identities=[root.get('eff_effect_id') for root in result]
    document.require(all(identities) and len(identities)==len(set(identities)) if result else True,
        '所选特效含重复来源身份，请通过导入创建独立特效，不能直接复制特效根')
    return result
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
def field_rows(obj):
    raw=obj.get('eff_effect_fields','[]');key=obj.as_pointer()
    if key not in _FIELD_ROWS or _FIELD_ROWS[key][0]!=raw:
        rows=json.loads(raw);root=root_of(obj)
        for row in rows:
            if row['kind'] in ('color','gradient_time'):
                value=editor(root).value(row['identity'],row['path'])
                row['_last']=tuple(value[k] for k in 'rgba') if row['kind']=='color' else value/65535
        _FIELD_ROWS[key]=(raw,rows)
    return _FIELD_ROWS[key][1]
def label(name):
    name=str(name)
    return effect_ui.MODULES[name][0] if name in effect_ui.MODULES else (name if name and not name.lower().startswith(('unk','unnamed')) else 'unknown')
def native_fields(edit,identity,path=()):
    value=edit.value(identity,path);schema=edit.schema(identity,path)
    if schema['type'].startswith('PPtr<') or schema['type']=='TypelessData':return
    if isinstance(value,dict):
        if value.get('encoding')=='base64':return
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
    _FIELD_ROWS.pop(obj.as_pointer(),None)
    gradient_editor.prepare(obj,edit,identities)
def synchronize(root):
    edit=editor(root)
    seen=set()
    for obj in objects(root):
        node=obj.get('eff_effect_node')
        if node:
            document.require(node not in seen,'发射器来源身份重复；请用“添加发射器”，不要直接 Shift+D')
            seen.add(node)
    changed=set()
    for obj in objects(root):
        for row in field_rows(obj):
            if row['key'] not in obj:continue
            value=obj[row['key']]
            if row['kind'] in ('color','gradient_time'):
                # Both the friendly widget and the advanced native fields are
                # editable. Last synchronized value disambiguates the writer.
                path=row['path'];current=edit.value(row['identity'],path)
                proxy=tuple(value) if row['kind']=='color' else float(value)
                previous=row.get('_last',tuple(current[k] for k in 'rgba') if row['kind']=='color' else current/65535)
                if proxy!=previous:
                    if row['kind']=='color':
                        for k,v in zip('rgba',proxy):current[k]=float(v)
                    else:edit.value(row['identity'],path[:-1])[path[-1]]=int(round(proxy*65535))
                    changed.add(row['identity'])
                current=edit.value(row['identity'],path)
                fresh=tuple(current[k] for k in 'rgba') if row['kind']=='color' else current/65535
                if proxy!=fresh:obj[row['key']]=list(fresh) if row['kind']=='color' else fresh
                row['_last']=fresh
                if proxy!=previous or proxy!=fresh:
                    if row['kind']=='color':
                        for k in 'rgba':obj[field_key(row['identity'],path+[k])]=float(current[k])
                    else:obj[field_key(row['identity'],path)]=int(current)
                continue
            if row['wide']:value=int(value,10)
            if edit.value(row['identity'],row['path'])!=value:
                kind=row['kind']
                if kind in document.INTS:
                    lo,hi=document.INTS[kind];document.require(type(value) is int and lo<=value<=hi,'整数超出原生范围')
                elif kind in ('float','double'):
                    import math
                    document.require(type(value) in (int,float) and math.isfinite(value) and (kind!='float' or abs(value)<=3.4028234663852886e38),'需要原生范围内的有限浮点数')
                elif kind=='bool':document.require(type(value) is bool,'需要布尔值')
                edit.value(row['identity'],row['path'][:-1])[row['path'][-1]]=value
                changed.add(row['identity'])
    # Root TRS is the mount offset; source child Empties are native emitters.
    # Both inline Transform fields and ordinary Blender gizmos edit that child.
    for obj in objects(root):
        if obj==root or obj.get('eff_preview'):continue
        identity=next((i for i in json.loads(obj.get('eff_effect_components','[]')) if edit.objects[i]['classId']==4),None)
        if not identity:continue
        # matrix_local can include stale parent evaluation immediately after
        # moving a root or seeking the timeline. The authoring basis and parent
        # inverse define the local transform without that depsgraph dependency.
        value=edit.value(identity);matrix=obj.matrix_parent_inverse@obj.matrix_basis;last=obj.get('eff_effect_last_matrix')
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
            obj.matrix_basis=obj.matrix_parent_inverse.inverted()@desired
        local=obj.matrix_parent_inverse@obj.matrix_basis
        obj['eff_effect_last_matrix']=[local[i][j] for i in range(4) for j in range(4)]
    reordered=set()
    def normalize(value):
        if isinstance(value,dict):
            if isinstance(value.get('m_Curve'),list):
                before=list(value['m_Curve']);value['m_Curve'].sort(key=lambda k:k['time'])
                if before!=value['m_Curve']:reordered.add(identity)
            if isinstance(value.get('m_Bursts'),list):value['m_BurstCount']=len(value['m_Bursts'])
            for child in value.values():normalize(child)
        elif isinstance(value,list):
            for child in value:normalize(child)
    for identity in changed:normalize(edit.state['values'][identity])
    material_bridge.synchronize(root,edit,sys.modules[__name__])
    if gradient_editor.synchronize(root,edit):store(root,edit)
    if reordered:
        for obj in objects(root):
            ids=json.loads(obj.get('eff_effect_components','[]'))
            if reordered.intersection(ids):populate(obj,edit,ids)
    # A native Graph Editor undo step can precede the next timer tick. Keep the
    # document's array sizes consistent with the projected ID-property fields.
    if curve_editor.synchronize(root,edit):store(root,edit)
    return edit
def refresh_fields(root):
    edit=editor(root)
    for obj in objects(root):
        ids=json.loads(obj.get('eff_effect_components','[]'))
        if ids:populate(obj,edit,ids)
def import_source(context,source):
    return import_workspace(context, document.load_source(source))

def import_workspace(context,state):
    edit=document.Editor(state)
    # The visible particle module is authoritative for a newly authored effect.
    # The original source and other components are preserved. Advanced users
    # may explicitly re-enable this one known automatic scaling rule.
    edit.use_particle_emission()
    collection=bpy.data.collections.new(Path(state['catalog']['prefab']).stem);context.scene.collection.children.link(collection)
    nodes={}
    for node in state['catalog']['hierarchy']:
        obj=bpy.data.objects.new(node['name'] or 'unknown',None);obj.empty_display_type='PLAIN_AXES';obj.empty_display_size=.15
        collection.objects.link(obj);obj['eff_effect_node']=node['identity'];nodes[node['identity']]=obj
    root=nodes[state['catalog']['root']];root['eff_effect_document']=document.dumps(state);root['eff_effect_id']=uuid.uuid4().hex
    compiler=Path(__file__).resolve().parent.parent/'AnimeStudio/AnimeStudio.GUI/bin/Debug/net9.0-windows/AnimeStudio.GUI.exe'
    if compiler.is_file():root.eff_effect.compiler=str(compiler)
    root['eff_effect_collection']=collection.name
    for node in state['catalog']['hierarchy']:
        obj=nodes[node['identity']]
        if node['parent']:
            parent=next(n for n in state['catalog']['hierarchy'] if n['transform']==node['parent']);obj.parent=nodes[parent['identity']]
        transform=edit.state['values'].get(node['transform'],{});p=transform.get('m_LocalPosition',{});q=transform.get('m_LocalRotation',{});s=transform.get('m_LocalScale',{})
        pos=Vector(tuple(p.get(k,0) for k in ('x','y','z'))) if obj!=root else Vector((0,0,0))
        rot=Quaternion((q.get('w',1),q.get('x',0),q.get('y',0),q.get('z',0)))
        obj.matrix_basis=BASIS@Matrix.LocRotScale(pos,rot,Vector(tuple(s.get(k,1) for k in ('x','y','z'))))@BASIS.inverted()
        local=obj.matrix_parent_inverse@obj.matrix_basis
        obj['eff_effect_last_matrix']=[local[i][j] for i in range(4) for j in range(4)]
        populate(obj,edit,[i for i in node['components'] if i in edit.state['values'] and edit.objects[i]['classId']!=1])
    for spec in edit.objects.values():
        if not spec.get('owner') and spec['classId']==21 and spec['identity'] in edit.state['values']:
            obj=bpy.data.objects.new(spec.get('name') or 'unknown material',None);obj.parent=root;obj.empty_display_type='CUBE';obj.empty_display_size=.04
            collection.objects.link(obj);obj['eff_effect_resource']=True;populate(obj,edit,[spec['identity']])
            assets.setup_textures(root,edit,obj,spec['identity'])
            preview._material_asset(root,edit,spec['identity'],preview._resources(root,edit))
    assets.expand_meshes(context,root,edit)
    for obj in context.selected_objects:obj.select_set(False)
    root['eff_effect_ui_version']=2
    root.select_set(True);context.view_layer.objects.active=root;preview.refresh(root,edit)
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

class EFF_PG_effect_object(bpy.types.PropertyGroup):
    enabled:BoolProperty(name='随 Mod 导出',default=True)
    preview:BoolProperty(name='离线粒子预览',default=True)
    show_shape:BoolProperty(name='显示发射范围',default=True)
    all_lods:BoolProperty(name='覆盖全部 LOD',default=True,
        description='跟随挂载部件的全部模型 LOD；导出的特效不因原生距离或质量 LOD 降级关闭。关闭则保留来源特效的降级设置')
    owner:PointerProperty(name='挂载目标 Mesh',type=bpy.types.Object,poll=lambda self,obj:obj.type=='MESH' and not obj.get('eff_preview'))
    bone:StringProperty(name='挂载骨骼',description='使用完整骨骼路径；事件触发暂不支持')
    toggle_keys:CollectionProperty(type=EFF_PG_effect_key)
    build_path:StringProperty(name='原生构建目录',subtype='DIR_PATH')
    compiler:StringProperty(name='AnimeStudio',subtype='FILE_PATH')
    output:StringProperty(name='旧独立 Mod 目录',subtype='DIR_PATH',options={'HIDDEN'})
    status:StringProperty(options={'HIDDEN'})
    particle_mesh:PointerProperty(name='自定义粒子 Mesh',type=bpy.types.Object,
        poll=lambda self,obj:obj.type=='MESH' and not obj.get('eff_preview'),
        description='使用该对象当前网格及修改器；旋转/缩放会烘焙，位置不影响发射区域。每个网格一个特效材质')

class EFF_PG_effect_texture(bpy.types.PropertyGroup):
    source_name:StringProperty(name='来源贴图')
    image:PointerProperty(name='替换图片',type=bpy.types.Image,description='留空保留来源；指定图片随特效打包，支持绘制后和打包图片')
    linear:BoolProperty(name='线性数据',default=False,description='遮罩/法线等数据图开启；普通颜色图关闭')
    mipmaps:BoolProperty(name='生成 Mipmap',default=True)
    wrap:EnumProperty(name='平铺',items=[('0','重复',''),('1','钳制',''),('2','镜像',''),('3','单次镜像','')],default='0')
    filter:EnumProperty(name='过滤',items=[('0','最近邻',''),('1','双线性',''),('2','三线性','')],default='1')
class EFF_OT_effect_import(bpy.types.Operator,ImportHelper):
    bl_idname='eiem.effect_import';bl_label='导入 EFF 特效为空物体';bl_options={'UNDO'}
    filter_glob:StringProperty(default='effect.json',options={'HIDDEN'})
    def execute(self,context):
        try:
            path=Path(self.filepath);import_source(context,path if path.is_dir() else path.parent);return {'FINISHED'}
        except Exception as e:self.report({'ERROR'},str(e));return {'CANCELLED'}
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
            edit.set_scalar(self.identity,['looping'],True)
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
def pointer_items(self,context):
    root=active(context)
    if not root:return [('NULL','无','')]
    edit=editor(root);kind=edit.schema(self.identity,json.loads(self.path))['type'][5:-1]
    _ENUM_CACHE['pointers']=[('NULL','无','')]+[(o['identity'],o.get('name') or o['type'],o['identity']) for o in edit.objects.values() if document.pointer_type_matches(kind,o['type'])]
    return _ENUM_CACHE['pointers']
class EFF_OT_effect_pointer(bpy.types.Operator):
    bl_idname='eiem.effect_pointer';bl_label='选择来源资源';bl_options={'UNDO'}
    identity:StringProperty();path:StringProperty();target:EnumProperty(items=pointer_items)
    def invoke(self,context,event):return context.window_manager.invoke_props_dialog(self,width=480)
    def draw(self,context):self.layout.prop(self,'target')
    def execute(self,context):
        try:
            root=active(context);edit=synchronize(root);path=json.loads(self.path);edit.set_pointer(self.identity,path,None if self.target=='NULL' else self.target)
            if edit.objects[self.identity]['classId']==199:
                node=next((o for o in objects(root) if self.identity in json.loads(o.get('eff_effect_components','[]'))),None)
                if node and path==['m_Mesh']:node.eff_effect.particle_mesh=assets.source_meshes(root).get(self.target)
                elif node and node.eff_effect.particle_mesh and len(path)==2 and path[0]=='m_Materials':
                    mesh=node.eff_effect.particle_mesh.data
                    if path[1]<len(mesh.materials) and self.target!='NULL':mesh.materials[path[1]]=preview._material_asset(root,edit,self.target,preview._resources(root,edit))
            store(root,edit);preview.refresh(root,edit);return {'FINISHED'}
        except Exception as e:self.report({'ERROR'},str(e));return {'CANCELLED'}
class EFF_OT_effect_resource(bpy.types.Operator):
    bl_idname='eiem.effect_resource';bl_label='编辑特效材质和贴图'
    identity:StringProperty()
    def execute(self,context):
        root=active(context);obj=assets.material_object(root,self.identity)
        if not obj:return {'CANCELLED'}
        for selected in context.selected_objects:selected.select_set(False)
        obj.select_set(True);context.view_layer.objects.active=obj
        return {'FINISHED'}

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
            root=active(context);edit=synchronize(root);store(root,edit);compiler=Path(bpy.path.abspath(root.eff_effect.compiler))
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
    if not owner:raise ValueError(root.name+' 缺少挂载目标 Mesh')
    rig=preview.mount_rig(owner)
    if not rig or not cfg.bone or cfg.bone not in rig.data.bones:raise ValueError('必须选择目标骨架中的挂载骨骼')
    bone=str(rig.data.bones[cfg.bone].get('eiem_path',cfg.bone));p=owner.data
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
    anchor=paths.get((use.get('skinningRoot') or {}).get('identity'))
    if anchor is None:raise ValueError('来源缺少骨架根证明')
    palette=[]
    for ptr in use.get('bones',[]):
        value=paths.get(ptr.get('identity'))
        if value is None or not (value==anchor or value.startswith(anchor+'/')):raise ValueError('来源骨骼不属于已验证骨架')
        palette.append(value)
    if not palette:raise ValueError('来源 Mesh 没有骨骼调色板')
    toggle_keys=[switch_controls.validate_switch_key(item.key) for item in cfg.toggle_keys]
    document.require(len(toggle_keys)==len(set(toggle_keys)),'特效显隐按键不能重复')
    return dict(id=root['eff_effect_id'],label=root.name,part=part,bone=bone,autoplay=True,keys=toggle_keys,build=bpy.path.abspath(cfg.build_path),position=list(position),rotation=[rotation.x,rotation.y,rotation.z,rotation.w],scale=list(scale),targetPath=path,targetAsset=asset,bones=palette,targetPrefab=proof['prefab'],rendererPath=use['ownerPath'])
def export_attachments(meshes,parts,addon,effect_roots=()):
    selected={addon.mesh_export_template(obj) for obj in meshes};result=[]
    declared={part['partId'] for part in parts}
    for root in export_roots(effect_roots,meshes,addon):
        owner=root.eff_effect.owner;part=''
        if owner in selected:
            splits=list(addon.native_export.split_material(addon.native_export._cached_mesh_snapshot(addon,owner)[0]))
            pid=addon.native_export.mesh_part_id(owner.name,splits[0][0],len(splits))
            document.require(pid in declared,root.name+' 的挂载网格未包含在导出部件中')
            part='Part'+pid.removeprefix('Mesh')
        ensure_built(root)
        result.append(attachment(root,part,addon))
    return result

def effect_codec():
    from . import eiem_native_export
    modules,_=eiem_native_export.pack_modules()
    return __import__(modules['ini_package'].__package__+'.runtime_controls',fromlist=['publish_effect_mod'])

def effect_cache():
    return bpy.utils.user_resource('DATAFILES',path='eiem/effect-builds',create=True)

def build_request(root,payload=None):
    if payload is None:payload=assets.export_document(root,synchronize(root))
    return effect_publish.Build(payload,bpy.path.abspath(root.eff_effect.compiler),effect_cache(),root['eff_effect_id'],effect_codec().native_build)

def ensure_built(root):
    if _BUILD_RUNNING:raise ValueError('已有特效正在构建，请完成后再导出人物')
    # A full export uses current author values too; an old manual build path
    # cannot silently publish a stale version of the effect.
    attachment(root)
    request=build_request(root);root.eff_effect.build_path=str(request.run())
    return request

class ExportBatch:
    """One selection, one destination, one publication for all effect roots."""
    def __init__(self,roots,destination,game_directory=''):
        self.roots=export_roots(roots);self.destination=Path(destination).resolve()
        self.game_directory=game_directory
        self.requests=[];self.index=0;self.current=None;self.stats=None;self.owns_build=False
        self.started=time.perf_counter()
        self.costs=dict(author=0.,compile=0.,publish=0.);self.compile_started=0.
        document.require(self.roots,'请选择 EFF 网格或特效根 / 发射器')
        document.require(not _BUILD_RUNNING,'已有特效正在构建')
        from . import eiem_offline_reload
        game=next((p.parent for p in self.destination.parents if p.name.lower()=='plugin' and (p/'mods') in self.destination.parents),None)
        if game:
            protocol=eiem_offline_reload._game_protocol(game)
            expected={'standaloneEffect':'mod-ini/type=effect/independent-resource-graph/1','effectTarget':'source-prefab+renderer+mesh+bone-palette/strings/1'}
            document.require(protocol and all(protocol.get('structures',{}).get(k)==v for k,v in expected.items()),'当前游戏 DLL 未声明独立特效和来源挂载支持，请安装配套 eff.dll 和 package-protocol.json')
        for root in self.roots:
            attachment(root)
            edit=synchronize(root);store(root,edit)
            self.requests.append(build_request(root))
        self.costs['author']=(time.perf_counter()-self.started)*1000

    def poll(self):
        global _BUILD_RUNNING,_EXPORT_JOB
        if not self.owns_build:
            document.require(not _BUILD_RUNNING,'已有特效正在构建')
            _BUILD_RUNNING=True;_EXPORT_JOB=self;self.owns_build=True
        if self.current:
            if self.current.process.poll() is None:return False
            self.current.finish();self.current.close();self.current=None;self.index+=1
            self.costs['compile']+=(time.perf_counter()-self.compile_started)*1000
        while self.index<len(self.requests):
            request=self.requests[self.index]
            if request.cached():self.index+=1;continue
            self.current=request;self.compile_started=time.perf_counter();request.start();return False
        phase=time.perf_counter()
        rows=[]
        for root,request in zip(self.roots,self.requests):
            current=document.dumps(assets.export_document(root,synchronize(root))).encode('utf-8')
            document.require(current==request.author,'构建期间特效有修改，请再次导出；上一 Mod 保留')
            row=attachment(root);row['build']=str(request.output)
            from . import eiem_native_export
            owner=root.eff_effect.owner
            candidates=[eiem_native_export.mesh_part_id(owner.name,0,1)]
            candidates.extend(eiem_native_export.mesh_part_id(owner.name,i,2) for i in range(len(owner.data.materials)))
            row['_partCandidates']=['Part'+value.removeprefix('Mesh') for value in candidates]
            rows.append(row)
        self.costs['author']+=(time.perf_counter()-phase)*1000
        from . import eiem_effect_export
        phase=time.perf_counter()
        character_update=eiem_effect_export.publish(self.destination,rows,self.game_directory)
        self.costs['publish']=(time.perf_counter()-phase)*1000
        elapsed=time.perf_counter()-self.started
        for root,request in zip(self.roots,self.requests):
            root.eff_effect.build_path=str(request.output)
            root.eff_effect.status=('已更新角色包特效' if character_update else '已导出特效')+'（%.2f 秒%s）'%(elapsed,'，复用构建' if request.reused else '')
        self.stats=dict(meshes=0,materials=0,textures=0,effects=len(rows),exportSeconds=elapsed,
            resourceScope='ALL',effectOnly=True,characterEffectUpdate=character_update,effectBuilds=sum(not r.reused for r in self.requests),
            exportCostsMs=dict(self.costs))
        print('[EFF] 特效导出：整理 %.2fs / 构建 %.2fs / 发布 %.2fs；复用 %d / 构建 %d'%(
            self.costs['author']/1000,self.costs['compile']/1000,self.costs['publish']/1000,
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
def draw_value(layout,obj,edit,identity,path,value):
    if isinstance(value,dict) and ('m_Curve' in value or ('minMaxState' in value and 'maxCurve' in value)):
        effect_ui.Draw(sys.modules[__name__],obj,edit,identity).value(layout,path);return
    name=label(path[-1]);schema=edit.schema(identity,path)
    if schema['type'].startswith('PPtr<'):
        target=preview._target(edit,identity,path);spec=edit.objects.get(target,{})
        resolved=(spec.get('name') or spec.get('type') or str(value.get('m_PathID',0))) if value.get('m_PathID') else '无'
        row=layout.row();row.label(text=name+' ['+schema['type']+'] '+resolved)
        if edit.editable(identity,path):
            op=row.operator('eiem.effect_pointer',text='',icon='EYEDROPPER');op.identity=identity;op.path=document.dumps(path)
        return
    if isinstance(value,(dict,list)):
        if isinstance(value,dict) and value.get('encoding')=='base64':layout.label(text=name+' [原生字节，保留]');return
        header,body=layout.panel(field_key(identity,path),default_closed=False);header.label(text=name+(' ['+str(len(value))+']' if isinstance(value,list) else ''))
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
    if is_emitter(obj) and obj!=root:
        op=row.operator('eiem.effect_emitter_remove',text='删除发射器',icon='TRASH');op.object_name=obj.name
    if obj!=root:select(layout,root,'返回特效根：挂载 / 导出','EMPTY_AXIS')
    header,body=layout.panel('eff_structure',default_closed=obj!=root)
    header.label(text='特效结构 · 点击切换编辑对象')
    if body:
        effect_ui.hint(body,'特效根管理挂载与导出；每个发射器分别控制一组粒子。')
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
            select(row,target,('发射器' if is_emitter(target) else '节点')+' · '+target.name,'OUTLINER_OB_EMPTY')
        materials=[o for o in objects(root) if o.get('eff_effect_resource') and any(edit.objects[i]['classId']==21 for i in ids(o))]
        if materials:
            body.separator();effect_ui.hint(body,'材质由发射器的渲染块引用；贴图在材质里修改。')
            for target in materials:select(body,target,'材质 · '+target.name,'MATERIAL')
        meshes=[o for o in objects(root) if o.get('eff_particle_template') and o.type=='MESH']
        if meshes:
            body.separator();effect_ui.hint(body,'网格可直接编辑或合并模型；材质在网格的材质属性中编辑。')
            for target in meshes:select(body,target,'Mesh · '+target.name,'MESH_DATA')
        if not root.get('eff_effect_assets_version'):
            body.operator('eiem.effect_assets_expand',icon='MESH_DATA')
        effect_ui.hint(body,'选择发射器只编辑该发射器；选择根可查看全部发射器。')
    header,body=layout.panel('eff_edit_guide',default_closed=True)
    header.label(text='怎么改：花瓣常用参数')
    if body:
        for text in ('形状 → 发射形状与范围',
                     '数量 → 发射数量 / 每秒发射数',
                     '生命周期 → 发射器基础 / 出生寿命',
                     '随机旋转 → 发射器基础 / 出生旋转 / 随机范围',
                     '渐隐 → 颜色与淡入淡出 / 渐变的 Alpha',
                     '网格 → 渲染 / 自定义粒子 Mesh',
                     '贴图 → 渲染 / 编辑材质与贴图'):
            effect_ui.hint(body,text)
        effect_ui.hint(body,'模块开关控制是否生效，标题左侧三角控制展开。每种模块仅一份；关闭保留全部参数。',icon='INFO')

class EFF_PT_effect_object(bpy.types.Panel):
    bl_label='EFF 特效 · 发射器';bl_idname='EFF_PT_effect_object';bl_space_type='PROPERTIES';bl_region_type='WINDOW';bl_context='data'
    bl_order=-100
    @classmethod
    def poll(cls,context):return bool(context.object and root_of(context.object) and (context.object.type=='EMPTY' or context.object.get('eff_particle_template')))
    def draw(self,context):
        layout=self.layout;obj=context.object;root=root_of(obj)
        try:
            edit=editor(root)
            layout.use_property_split=True;layout.use_property_decorate=False
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
                row=layout.row(align=True);row.prop(cfg,'preview');row.prop(cfg,'show_shape')
                if root.get('预览错误'):layout.label(text=str(root['预览错误'])[:150],icon='ERROR')
                for key,title in (('mount','挂载与显隐'),('export','构建工具'),('diagnostics','预览支持与来源信息')):
                    header,body=layout.panel('eff_'+key,default_closed=True);header.label(text=title)
                    if not body:continue
                    if key=='mount':
                        body.prop(cfg,'enabled');body.prop(cfg,'all_lods');body.prop(cfg,'owner')
                        if cfg.all_lods:effect_ui.hint(body,'导出时解除来源特效的距离/画质降级；同骨架的模型 LOD 共用一次播放。')
                        rig=preview.mount_rig(cfg.owner)
                        if rig:body.prop_search(cfg,'bone',rig.data,'bones')
                        else:body.prop(cfg,'bone')
                        body.label(text='随挂载 Mesh 显隐；触发事件暂不支持')
                        effect_ui.hint(body,'预览按所选骨骼的位置与朝向计算；根物体变换作为骨骼局部偏移。')
                        key_box=body.box();key_header=key_box.row(align=True);key_header.label(text='特效显隐按键')
                        op=key_header.operator('eiem.effect_key',text='录制按键',icon='REC');op.action='ADD';op.root_name=root.name
                        for index,item in enumerate(cfg.toggle_keys):
                            row=key_box.row(align=True);row.label(text=item.key,icon='EVENT_F')
                            op=row.operator('eiem.effect_key',text='',icon='X');op.action='REMOVE';op.root_name=root.name;op.index=index
                        if not cfg.toggle_keys:effect_ui.hint(key_box,'未设置时默认随挂载 Mesh 显示；录制多个按键后，任一按键都会切换此特效。')
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
            targets=[o for o in objects(root) if not o.get('eff_preview') and not o.get('eff_effect_resource') and not o.get('eff_particle_template')] if obj==root else [obj]
            for target in targets:
                target_layout=layout
                if obj==root and target!=root:
                    heading,target_layout=layout.panel('node_'+str(target.as_pointer()),default_closed=False);heading.label(text='发射器 · '+target.name if any(edit.objects[i]['classId']==198 for i in json.loads(target.get('eff_effect_components','[]'))) else target.name)
                    if not target_layout:continue
                ids=json.loads(target.get('eff_effect_components','[]'))
                if any(edit.objects[i]['classId']==198 for i in ids):
                    op=target_layout.operator('eiem.effect_block',text='启用模块…',icon='ADD');op.object_name=target.name
                for identity in sorted(ids,key=lambda i:edit.objects[i]['classId']==4):
                    if identity not in edit.state['values']:continue
                    effect_ui.Draw(sys.modules[__name__],target,edit,identity).component(target_layout,edit.objects[identity])
        except Exception as e:layout.label(text=str(e)[:180],icon='ERROR')

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
        for obj in objects(root):
            gradient_editor.prepare(obj,editor(root),json.loads(obj.get('eff_effect_components','[]')))
        # Prepare degree controls without replacing any native author value.
        for obj in objects(root):
            if obj.get('eff_effect_angles_version')==1:continue
            for field in field_rows(obj):
                if effect_ui.angle_unit(field['path']) and obj.eff_effect_angles.get(field['key']) is None:
                    angle=obj.eff_effect_angles.add();angle.name=field['key']
            obj['eff_effect_angles_version']=1
        for obj in objects(root):
            for identity in json.loads(obj.get('eff_effect_components','[]')):
                if editor(root).objects[identity]['classId']==21:
                    assets.setup_textures(root,editor(root),obj,identity)
                    material=material_bridge.material_for(obj)
                    if material:material_bridge.setup(material,root,editor(root),identity)
        if root.get('eff_effect_ui_version',0)>=2:continue
        # Migrate the old raw-only UI in memory, retaining pending native edits.
        edit=synchronize(root);store(root,edit);refresh_fields(root);root['eff_effect_ui_version']=2
CLASSES=(EFF_PG_effect_choice,EFF_PG_effect_angle,EFF_PG_effect_key,EFF_PG_effect_object,EFF_PG_effect_texture,EFF_OT_effect_import,EFF_OT_effect_block,EFF_OT_effect_array,EFF_OT_effect_use_particle_emission,EFF_OT_effect_continuous,EFF_OT_effect_emitter_add,EFF_OT_effect_emitter_remove,EFF_OT_effect_pointer,EFF_OT_effect_resource,EFF_OT_effect_select,EFF_OT_effect_key,EFF_OT_effect_texture_open,EFF_OT_effect_mesh_edit,EFF_OT_effect_assets_expand,EFF_OT_effect_export,EFF_OT_effect_build,EFF_OT_effect_preview,EFF_PT_effect_object)
def menu_import(self,context):self.layout.operator('eiem.effect_import',text='EFF 特效（空物体）')
def menu_add(self,context):self.layout.operator('eiem.effect_import',text='EFF 特效来源',icon='EMPTY_AXIS')
def register():
    for cls in CLASSES:bpy.utils.register_class(cls)
    bpy.types.Object.eff_effect=PointerProperty(type=EFF_PG_effect_object)
    bpy.types.Object.eff_effect_choices=CollectionProperty(type=EFF_PG_effect_choice)
    bpy.types.Object.eff_effect_angles=CollectionProperty(type=EFF_PG_effect_angle)
    bpy.types.Object.eff_effect_textures=CollectionProperty(type=EFF_PG_effect_texture)
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
    gradient_editor.unregister()
    curve_editor.unregister()
    preview.unregister();_CACHE.clear()
    if bpy.app.timers.is_registered(upgrade_loaded):bpy.app.timers.unregister(upgrade_loaded)
    _FIELD_ROWS.clear();_field_key.cache_clear()
    if upgrade_loaded in bpy.app.handlers.load_post:bpy.app.handlers.load_post.remove(upgrade_loaded)
    for handlers,fn in ((bpy.app.handlers.save_pre,save_pre),(bpy.app.handlers.load_post,clear_cache),(bpy.app.handlers.undo_post,clear_cache),(bpy.app.handlers.redo_post,clear_cache)):
        if fn in handlers:handlers.remove(fn)
    bpy.types.TOPBAR_MT_file_import.remove(menu_import);bpy.types.VIEW3D_MT_add.remove(menu_add);del bpy.types.Object.eff_effect;del bpy.types.Object.eff_effect_choices;del bpy.types.Object.eff_effect_angles;del bpy.types.Object.eff_effect_textures;del bpy.types.Material.eff_effect_author
    for cls in reversed(CLASSES):bpy.utils.unregister_class(cls)
