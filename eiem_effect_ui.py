"""Author-facing Unity module UI; native paths and types remain authoritative.

Mappings verified against exported TypeTrees and UnityCsReference module editors.
No arbitrary module graph or synthetic lifetime/fade fields are serialized.
"""
import json
import textwrap

GROUPS = (
    ('运动', ('VelocityModule','InheritVelocityModule','LifetimeByEmitterSpeedModule','ForceModule','ExternalForcesModule','ClampVelocityModule','NoiseModule')),
    ('外观随生命周期 / 速度变化', ('ColorModule','SizeModule','RotationModule','ColorBySpeedModule','SizeBySpeedModule','RotationBySpeedModule','UVModule')),
    ('交互与扩展', ('CollisionModule','TriggerModule','SubModule','LightsModule','TrailModule','CustomDataModule')),
)
MODULES = {
    'InitialModule': ('发射器基础', '系统播放周期与每个粒子出生时的状态。'),
    'EmissionModule': ('发射数量', '按秒 / 按移动距离连续发射，或在指定时间爆发。'),
    'ShapeModule': ('发射形状与范围', '决定粒子出生位置和初始发射方向。'),
    'VelocityModule': ('速度随生命周期', '在初速度之外叠加直线、轨道和径向运动。'),
    'InheritVelocityModule': ('继承发射器速度', '粒子继承挂点运动；离线暂未还原。'),
    'LifetimeByEmitterSpeedModule': ('寿命随发射器速度', '用挂点速度调整出生寿命；离线暂未还原。'),
    'ForceModule': ('力随生命周期', '持续加速度；离线暂未还原。'),
    'ExternalForcesModule': ('外部力场', '读取场景中的力场组件；离线暂未还原。'),
    'ClampVelocityModule': ('速度限制与阻力', '限制速度、阻尼与空气阻力；离线暂未还原。'),
    'NoiseModule': ('噪声扰动', '影响位置、旋转或大小；离线仅近似位置摆动。'),
    'ColorModule': ('颜色与淡入淡出', '按粒子的已过寿命改变颜色 / 透明度；淡入淡出编辑 Alpha。'),
    'SizeModule': ('大小随生命周期', '曲线乘以出生大小。'),
    'RotationModule': ('旋转随生命周期', '随时间积分角速度。'),
    'ColorBySpeedModule': ('颜色随速度', '以速度范围映射颜色；离线暂未还原。'),
    'SizeBySpeedModule': ('大小随速度', '以速度范围映射大小；离线暂未还原。'),
    'RotationBySpeedModule': ('旋转随速度', '以速度范围映射角速度；离线暂未还原。'),
    'UVModule': ('贴图序列帧', '在粒子贴图图集中选择帧；支持网格图集预览。'),
    'CollisionModule': ('粒子碰撞', '平面 / 场景碰撞与碰撞后寿命损失；离线暂未还原。'),
    'TriggerModule': ('粒子触发器', '粒子进入 / 离开碰撞体的响应；离线暂未还原。'),
    'SubModule': ('子发射器', '出生、碰撞、死亡等事件关联其他粒子系统；离线暂未还原。'),
    'LightsModule': ('粒子灯光', '按粒子分配来源灯光；离线暂未还原。'),
    'TrailModule': ('粒子拖尾', '沿粒子路径生成带状几何；离线暂未还原。'),
    'CustomDataModule': ('着色器自定义数据', '向 Shader 顶点流提供向量 / 颜色；需匹配渲染器与 Shader。'),
}
LABELS = {
    'type':'类型', 'placementMode':'采样位置模式',
    'm_MeshMaterialIndex':'Mesh 材质槽', 'm_UseMeshMaterialIndex':'限定材质槽',
    'm_MeshSpawn':'Mesh 表面发射', 'm_TextureClipChannel':'贴图裁剪通道',
    'm_TextureClipThreshold':'裁剪下限', 'm_TextureClipThresholdUpper':'裁剪上限',
    'm_TextureUVChannel':'贴图 UV 通道', 'm_TextureColorAffectsParticles':'贴图颜色影响粒子',
    'm_TextureAlphaAffectsParticles':'贴图 Alpha 影响粒子', 'm_TextureBilinearFiltering':'双线性过滤',
    'multiplyColliderForceByParticleSize':'碰撞受力乘大小',
    'multiplyColliderForceByParticleSpeed':'碰撞受力乘速度',
    'multiplyColliderForceByCollisionAngle':'碰撞受力乘入射角',
    'm_MeshDistribution':'多 Mesh 分配方式', 'spread':'分布步进', 'value':'数值',
    'mode0':'自定义流 1', 'mode1':'自定义流 2',
    'vectorComponentCount0':'流 1 分量数', 'vectorComponentCount1':'流 2 分量数',
    **{'vector'+str(s)+'_'+str(i):'流 '+str(s+1)+' · '+'XYZW'[i] for s in range(2) for i in range(4)},
    'color0':'流 1 颜色', 'color1':'流 2 颜色',
    'lengthInSec':'系统周期 (s)', 'looping':'循环播放', 'prewarm':'预热一轮', 'startDelay':'开始延迟 (s)',
    'simulationSpeed':'播放速度', 'moveWithTransform':'模拟空间', 'moveWithCustomTransform':'自定义空间挂点',
    'scalingMode':'缩放方式', 'playOnAwake':'启用时播放', 'useUnscaledTime':'不受时间缩放影响',
    'autoRandomSeed':'自动随机种子', 'randomSeed':'随机种子', 'stopAction':'停止后行为', 'cullingMode':'屏外模拟',
    'emitterVelocityMode':'挂点速度来源', 'ringBufferMode':'环形缓冲', 'ringBufferLoopRange':'寿命循环范围',
    'startLifetime':'出生寿命 (s)', 'startSpeed':'初速度 (单位/s)', 'startColor':'出生颜色',
    'startSize':'出生大小', 'startSizeY':'大小 Y', 'startSizeZ':'大小 Z', 'size3D':'分别设置 XYZ 大小',
    'startRotation':'出生旋转 Z (°)', 'startRotationX':'出生旋转 X (°)', 'startRotationY':'出生旋转 Y (°)',
    'rotation3D':'分别设置 XYZ 旋转', 'randomizeRotationDirection':'反向旋转概率',
    'gravityModifier':'重力倍率', 'maxNumParticles':'最大同时存活数', 'customEmitterVelocity':'自定义挂点速度',
    'rateOverTime':'每秒发射数', 'rateOverDistance':'每单位移动发射数', 'm_Bursts':'定时爆发',
    'scaleMode':'缩放来源', 'particleSystemScalingMode':'粒子缩放方式', 'axisBitMask':'参与计算的轴遮罩',
    'time':'时间 (s)', 'countCurve':'发射数量', 'cycleCount':'重复次数 (0=无限)', 'repeatInterval':'间隔 (s)', 'probability':'触发概率',
    'angle':'锥角 (°)', 'length':'长度', 'radius':'半径', 'arc':'圆弧 (°)', 'radiusThickness':'半径厚度比例',
    'boxThickness':'盒壳厚度', 'donutRadius':'圆环管半径', 'm_Position':'形状位置', 'm_Rotation':'形状旋转 (°)', 'm_Scale':'形状尺寸',
    'randomDirectionAmount':'随机方向比例', 'sphericalDirectionAmount':'球面方向比例', 'randomPositionAmount':'随机位置量',
    'alignToDirection':'朝向发射方向', 'm_Mesh':'粒子 Mesh', 'm_Mesh1':'Mesh 2', 'm_Mesh2':'Mesh 3', 'm_Mesh3':'Mesh 4',
    'm_MeshRenderer':'来源 MeshRenderer', 'm_SkinnedMeshRenderer':'来源蒙皮 Renderer', 'm_Sprite':'Sprite', 'm_SpriteRenderer':'Sprite Renderer',
    'm_Texture':'形状采样贴图', 'm_UseMeshColors':'采用 Mesh 顶点颜色', 'm_MeshNormalOffset':'沿法线偏移',
    'curve':'倍率曲线', 'gradient':'颜色 / Alpha', 'separateAxes':'分别控制 XYZ', 'separateAxis':'分别限制 XYZ',
    'x':'X', 'y':'Y', 'z':'Z', 'w':'W', 'r':'R', 'g':'G', 'b':'B', 'a':'Alpha',
    'inWorldSpace':'使用世界坐标', 'orbitalX':'绕 X 轴角速度', 'orbitalY':'绕 Y 轴角速度', 'orbitalZ':'绕 Z 轴角速度',
    'orbitalOffsetX':'轨道中心 X', 'orbitalOffsetY':'轨道中心 Y', 'orbitalOffsetZ':'轨道中心 Z', 'radial':'径向速度', 'speedModifier':'速度倍率',
    'randomizePerFrame':'每帧重新随机', 'm_Mode':'模式', 'm_Curve':'变化曲线', 'm_Range':'速度范围', 'range':'范围',
    'multiplierCurve':'外力倍率', 'influenceFilter':'力场过滤方式', 'influenceMask':'力场层遮罩', 'influenceList':'力场引用',
    'magnitude':'速度上限', 'dampen':'超速阻尼', 'drag':'阻力', 'multiplyDragByParticleSize':'阻力乘粒子大小', 'multiplyDragByParticleVelocity':'阻力乘粒子速度',
    'strength':'扰动强度', 'strengthY':'强度 Y', 'strengthZ':'强度 Z', 'frequency':'频率', 'damping':'频率阻尼',
    'octaves':'叠加层数', 'octaveMultiplier':'层强度倍率', 'octaveScale':'层频率倍率', 'quality':'质量', 'scrollSpeed':'噪声滚动速度',
    'remapEnabled':'重新映射', 'remap':'映射 X', 'remapY':'映射 Y', 'remapZ':'映射 Z',
    'positionAmount':'位置影响量', 'rotationAmount':'旋转影响量', 'sizeAmount':'大小影响量',
    'mode':'模式', 'timeMode':'帧推进方式', 'fps':'帧率', 'frameOverTime':'帧随生命周期', 'startFrame':'起始帧比例',
    'tilesX':'图集列数', 'tilesY':'图集行数', 'animationType':'动画范围', 'rowIndex':'行号', 'rowMode':'选行方式', 'cycles':'循环次数',
    'speedRange':'速度范围', 'uvChannelMask':'UV 通道遮罩', 'sprites':'Sprite 序列', 'flipU':'U 翻转比例', 'flipV':'V 翻转比例',
    'm_Planes':'碰撞平面', 'm_Dampen':'碰撞阻尼', 'm_Bounce':'反弹', 'm_EnergyLossOnCollision':'寿命损失',
    'minKillSpeed':'低于此速度消亡', 'maxKillSpeed':'高于此速度消亡', 'radiusScale':'碰撞半径倍率', 'collidesWith':'碰撞层遮罩',
    'colliderForce':'碰撞体受力', 'maxCollisionShapes':'碰撞体上限', 'voxelSize':'缓存体素大小', 'collisionMessages':'发送碰撞回调',
    'collidesWithDynamic':'包含动态碰撞体', 'interiorCollisions':'内部碰撞', 'collisionMode':'碰撞维度',
    'inside':'位于内部', 'outside':'位于外部', 'enter':'进入时', 'exit':'离开时', 'colliderQueryMode':'碰撞体查询', 'primitives':'触发碰撞体',
    'subEmitters':'子发射器关联', 'emitter':'粒子系统', 'properties':'继承选项遮罩', 'emitProbability':'发射概率',
    'ratio':'粒子比例', 'light':'灯光来源', 'randomDistribution':'随机分配', 'color':'采用粒子颜色', 'intensity':'强度',
    'rangeCurve':'灯光范围曲线', 'intensityCurve':'灯光强度曲线', 'maxLights':'灯光上限',
    'lifetime':'拖尾寿命 (s)', 'minVertexDistance':'顶点最小间隔', 'textureMode':'贴图铺设', 'ribbonCount':'带状分组数',
    'worldSpace':'世界空间', 'dieWithParticles':'随粒子消亡', 'sizeAffectsWidth':'大小影响宽度', 'sizeAffectsLifetime':'大小影响寿命',
    'inheritParticleColor':'继承粒子颜色', 'generateLightingData':'生成光照数据', 'shadowBias':'阴影偏移',
    'splitSubEmitterRibbons':'分开子发射器带状条', 'attachRibbonsToTransform':'拖尾连接挂点',
    'colorOverLifetime':'拖尾颜色随寿命', 'widthOverTrail':'沿拖尾宽度', 'colorOverTrail':'沿拖尾颜色',
    'm_Enabled':'启用', 'm_RenderMode':'渲染方式', 'm_Materials':'材质', 'm_RenderAlignment':'朝向空间', 'm_SortMode':'排序方式',
    'm_MinParticleSize':'屏幕最小大小', 'm_MaxParticleSize':'屏幕最大大小', 'm_CameraVelocityScale':'相机速度拉伸',
    'm_VelocityScale':'粒子速度拉伸', 'm_LengthScale':'长度倍率', 'm_Pivot':'中心偏移', 'm_Flip':'随机翻转',
    'm_SortingFudge':'排序偏移', 'm_SortingOrder':'排序顺序', 'm_AllowRoll':'允许 Billboard 翻滚',
    'm_UseCustomVertexStreams':'自定义顶点流', 'm_EnableGPUInstancing':'GPU 实例化',
    'm_MotionVectors':'运动矢量',
    'effectLogicCfg':'游戏整体播放控制', 'isLoop':'整体循环', 'duration':'整体持续时间 (s)', 'randomDelay':'随机延迟',
    'delay':'延迟 (s)', 'fadeoutTime':'整体淡出 (s)', 'autoFade':'自动淡入淡出', 'startFadeTime':'开始淡入 (s)', 'endFadeTime':'结束淡出 (s)',
    'timeScaleMode':'时间缩放模式', 'followMoveAnimPlaybackSpeed':'跟随移动动画速度',
}
ENUMS = {
    'moveWithTransform': ('局部：粒子跟随挂点','世界：已发射粒子留在世界','自定义 Transform'),
    'scalingMode': ('整个层级','当前节点','仅发射形状'),
    'scaleMode': ('世界缩放','当前节点缩放'),
    'particleSystemScalingMode': ('整个层级','当前节点','仅发射形状'),
    'stopAction': ('无','禁用对象','销毁对象','回调'),
    'cullingMode': ('自动','暂停后追帧','暂停','始终模拟'),
    'ringBufferMode': ('关闭','暂停直到被替换','循环直到被替换'),
    'emitterVelocityMode': ('Transform','Rigidbody','自定义'),
    'minMaxState': ('常量','曲线','随机双曲线','随机范围'),
    'm_RenderMode': ('Billboard 面片','速度拉伸面片','水平面片','垂直面片','Mesh 网格','不渲染'),
    'm_RenderAlignment': ('视图','世界','局部','朝向相机','速度'),
    'm_SortMode': ('不排序','距相机距离','旧粒子在前','新粒子在前','相机深度','距离反序','深度反序'),
    'm_MotionVectors': ('仅相机运动','物体运动','强制无运动'),
    'ShapeModule.type': ('球体','球壳','半球','半球壳','圆锥底面','盒体','Mesh','圆锥底边','圆锥体积','圆锥壳','圆盘','圆边','线段','MeshRenderer','SkinnedMeshRenderer','盒壳','盒边','圆环','矩形','Sprite','SpriteRenderer'),
    'UVModule.mode': ('网格图集','Sprite 列表'),
    'UVModule.timeMode': ('生命周期','速度','FPS'),
    'UVModule.animationType': ('整个图集','单行'),
    'UVModule.rowMode': ('指定行','随机行','Mesh 索引'),
    'NoiseModule.quality': ('低','中','高'),
    'InheritVelocityModule.m_Mode': ('出生时继承','持续继承当前速度'),
    'CollisionModule.type': ('平面','世界'),
    'CollisionModule.collisionMode': ('3D','2D'),
    'CollisionModule.quality': ('高','中','低'),
    'TriggerModule.inside': ('忽略','消亡','回调'),
    'TriggerModule.outside': ('忽略','消亡','回调'),
    'TriggerModule.enter': ('忽略','消亡','回调'),
    'TriggerModule.exit': ('忽略','消亡','回调'),
    'TriggerModule.colliderQueryMode': ('关闭','一个碰撞体','全部碰撞体'),
    'TrailModule.mode': ('单粒子','带状'),
    'TrailModule.textureMode': ('拉伸','平铺','按段分布','每段重复','静态 UV'),
    'SubModule.subEmitters.type': ('出生','碰撞','死亡','触发','手动'),
    'CustomDataModule.mode0': ('关闭','向量','颜色'),
    'CustomDataModule.mode1': ('关闭','向量','颜色'),
    'ShapeModule.radius.mode': ('随机','循环','往返','爆发均布'),
    'ShapeModule.arc.mode': ('随机','循环','往返','爆发均布'),
    'ShapeModule.m_MeshSpawn.mode': ('随机','循环','往返','爆发均布'),
    'ShapeModule.placementMode': ('顶点','边','三角面'),
    'ShapeModule.m_TextureClipChannel': ('R','G','B','Alpha'),
}
COLOR_MODES = ('颜色','渐变','随机双色','随机双渐变','渐变随机取色')

def enum_items(path, value=None):
    names = ENUMS.get('.'.join(str(p) for p in path if not isinstance(p, int))) or ENUMS.get(path[-1])
    if path[-1]=='m_Enabled' and isinstance(value,dict) and 'axisBitMask' in value and 'particleSystemScalingMode' in value:
        names=('关闭','开启')
    if path[-1]=='minMaxState' and isinstance(value, dict) and 'maxGradient' in value:
        names = COLOR_MODES
    if path[-1]=='m_Mode' and isinstance(value,dict) and 'm_NumColorKeys' in value:
        names=('混合插值','固定颜色')
    return [(str(i), name, '', i) for i,name in enumerate(names)] if names else None

def name(key):
    text = str(key)
    if text.lower().startswith(('unk','unnamed')):
        return 'unknown'
    return LABELS.get(text, text)


def angle_unit(path):
    """Only verified native radian multipliers; curve keys remain unitless.

    Shape rotations/angles are already degrees; Transform uses quaternions.
    Neither should receive this conversion.
    """
    if len(path) != 3 or path[-1] not in ('scalar', 'minScalar'):return None
    if path[0] == 'InitialModule' and path[1] in ('startRotation', 'startRotationX', 'startRotationY'):return '°'
    if path[0] in ('RotationModule', 'RotationBySpeedModule') and path[1] in ('curve', 'x', 'y', 'z'):return '°/s'
    return None

def hint(layout, text, icon='NONE'):
    import bpy
    region=getattr(bpy.context,'region',None)
    width=max(14,min(65,int((getattr(region,'width',480)-70)/14)))
    for i,line in enumerate(textwrap.wrap(text,width=width)):
        layout.label(text=line,icon=icon if i==0 else 'NONE')

class Draw:
    def __init__(self, api, obj, edit, identity):
        self.api, self.obj, self.edit, self.identity = api, obj, edit, identity
    def key(self, path):
        return self.api.field_key(self.identity, path)
    def live(self, path):
        return self.obj.get(self.key(path), self.edit.value(self.identity, path))
    def prop(self, layout, path, text=None):
        key = self.key(path)
        if key in self.obj:
            angle=self.obj.eff_effect_angles.get(key)
            choice=self.obj.eff_effect_choices.get(key)
            if angle:
                title=name(path[-1]) if text is None else text
                unit=angle_unit(path)
                if title and unit and unit not in title:title+=' ('+unit+')'
                layout.prop(angle,'degrees',text=title)
            elif choice:layout.prop(choice,'value',text=name(path[-1]) if text is None else text)
            else:layout.prop(self.obj, '["'+key+'"]', text=name(path[-1]) if text is None else text)
    def block(self, layout, path, title, closed=False, enabled=False):
        header, body = layout.panel(self.key(path), default_closed=closed)
        if enabled and self.key(path+['enabled']) in self.obj:
            self.prop(header, path+['enabled'], '')
        header.label(text=title)
        if enabled and not self.live(path+['enabled']):
            header.label(text='未启用')
        return body
    def array_op(self, layout, path, op, index=0, text='', icon='ADD'):
        button=layout.operator('eiem.effect_array',text=text,icon=icon)
        button.identity=self.identity;button.path=json.dumps(path);button.operation=op;button.index=index
    def color(self, layout, path, title):
        key='fxv_'+self.key(path)[3:]
        if key in self.obj:
            layout.prop(self.obj,'["'+key+'"]',text=title)
        else:
            row=layout.row(align=True);row.label(text=title)
            for component in 'rgba':self.prop(row,path+[component],component.upper())
    def curve(self, layout, path, title):
        # Native normalized key values remain available for exact editing.
        value=self.edit.value(self.identity,path)
        keys=value.get('m_Curve',[])
        layout.label(text=title+' · 横轴 0→1')
        row=layout.row(align=True);row.label(text='时间');row.label(text='数值')
        for i,_ in enumerate(keys):
            row=layout.row(align=True)
            self.prop(row,path+['m_Curve',i,'time'],'')
            self.prop(row,path+['m_Curve',i,'value'],'')
            self.array_op(row,path+['m_Curve'],'REMOVE',i,icon='X')
        self.array_op(layout,path+['m_Curve'],'ADD',text='添加关键帧')
        detail=self.block(layout,path+['_tangents'],'切线与插值',True)
        if detail:
            for i,key in enumerate(keys):
                row=detail.row(align=True);row.label(text=str(i+1))
                for k in ('inSlope','outSlope'):self.prop(row,path+['m_Curve',i,k],{'inSlope':'入切线','outSlope':'出切线'}[k])
                for k in ('weightedMode','inWeight','outWeight'):
                    if k in key:self.prop(detail,path+['m_Curve',i,k])
            for k in ('m_PreInfinity','m_PostInfinity'):
                if k in value:self.prop(detail,path+[k])
    def gradient(self,layout,path,title):
        self.api.gradient_editor.draw(layout,self,path,title)
        layout=self.block(layout,path+['_exact_gradient'],'精确数值 / 原生渐变点',True)
        if not layout:return
        value=self.edit.value(self.identity,path)
        for field,label in (('m_NumColorKeys','颜色点数'),('m_NumAlphaKeys','透明度点数')):self.prop(layout,path+[field],label)
        for alpha,count,prefix in ((False,'m_NumColorKeys','ctime'),(True,'m_NumAlphaKeys','atime')):
            layout.label(text='透明度（0 隐藏 / 1 可见）' if alpha else '颜色')
            for i in range(min(8,max(0,int(self.live(path+[count]))))):
                row=layout.row(align=True)
                timekey='fxu_'+self.key(path+[prefix+str(i)])[3:]
                if timekey in self.obj:row.prop(self.obj,'["'+timekey+'"]',text='位置')
                else:self.prop(row,path+[prefix+str(i)],'位置 / 65535')
                if alpha:self.prop(row,path+['key'+str(i),'a'],'Alpha')
                else:self.color(row,path+['key'+str(i)],'')
        self.prop(layout,path+['m_Mode'],'插值')
    def value(self,layout,path,title=None):
        value=self.edit.value(self.identity,path);title=name(path[-1]) if title is None else title
        if isinstance(value,dict) and 'minMaxState' in value:
            mode=int(self.live(path+['minMaxState']))
            row=layout.row(align=True)
            if 'maxGradient' not in value and mode in (0,3):
                # One row for the usual constant/range edit, like Unity's
                # MinMaxCurve. Mode is a named menu, never a raw type badge.
                if mode==0:self.prop(row,path+['scalar'],title)
                else:
                    self.prop(row,path+['minScalar'],title+' 最小')
                    self.prop(row,path+['scalar'],'最大')
                self.prop(row,path+['minMaxState'],'')
                return
            self.prop(row,path+['minMaxState'],title)
            if 'maxGradient' in value:
                if mode in (0,2):
                    if mode==2:self.color(layout,path+['minColor'],'最小颜色')
                    self.color(layout,path+['maxColor'],'最大颜色' if mode==2 else '颜色')
                else:
                    if mode==3:self.gradient(layout,path+['minGradient'],'渐变 A')
                    self.gradient(layout,path+['maxGradient'],'渐变 B' if mode==3 else '渐变')
            else:
                self.prop(layout,path+['scalar'],'曲线倍率')
                self.api.curve_editor.draw_summary(layout,self,path)
                detail=self.block(layout,path+['_exact_keys'],'精确数值 / 原生关键点与切线',True)
                if detail:
                    hint(detail,'下列数值为原生归一化值；图形编辑器纵轴已乘曲线倍率，旋转显示角度。')
                    if mode==2:self.curve(detail,path+['minCurve'],'曲线 A')
                    self.curve(detail,path+['maxCurve'],'曲线 B' if mode==2 else '变化曲线')
            return
        if isinstance(value,dict) and 'm_Curve' in value and isinstance(value['m_Curve'],list):
            layout.label(text=title)
            self.api.curve_editor.draw_summary(layout,self,path)
            detail=self.block(layout,path+['_exact_keys'],'精确数值 / 原生关键点与切线',True)
            if detail:self.curve(detail,path,'变化曲线')
            return
        if isinstance(value,dict) and set(value)==set('rgba'):
            self.color(layout,path,title);return
        if isinstance(value,dict) and set(value).issubset(set('xyzw')):
            row=layout.row(align=True);row.label(text=title)
            for k in value:self.prop(row,path+[k],k.upper())
            return
        if isinstance(value,dict) and set(value)>=set(('value','mode','spread','speed')):
            self.prop(layout,path+['value'],title)
            return
        schema=self.edit.schema(self.identity,path)
        if schema['type'].startswith('PPtr<'):
            target=self.api.preview._target(self.edit,self.identity,path);spec=self.edit.objects.get(target,{})
            row=layout.row(align=True);row.label(text=title+': '+(spec.get('name') or spec.get('type') or '无'))
            if self.edit.editable(self.identity,path):
                op=row.operator('eiem.effect_pointer',text='',icon='EYEDROPPER');op.identity=self.identity;op.path=json.dumps(path)
            return
        if isinstance(value,list):
            layout.label(text=title+' · '+str(len(value))+' 项')
            for i,item in enumerate(value):
                item_schema=self.edit.schema(self.identity,path+[i])
                if item_schema['type'].startswith('PPtr<'):
                    self.value(layout,path+[i],title+' '+str(i+1))
                    continue
                if isinstance(item,dict):
                    row=layout.row(align=True);row.label(text=title+' '+str(i+1))
                    if self.edit.editable(self.identity,path):
                        self.array_op(row,path,'COPY',i,icon='DUPLICATE');self.array_op(row,path,'REMOVE',i,icon='X')
                    for k in item:self.value(layout,path+[i,k])
                else:self.value(layout,path+[i],str(i+1))
            if self.edit.editable(self.identity,path):self.array_op(layout,path,'ADD',text='添加'+title)
            return
        if isinstance(value,dict):
            body=self.block(layout,path,title,True)
            if body:
                for k in value:self.value(body,path+[k])
            return
        self.prop(layout,path,title)
    def fields(self,layout,path,fields):
        value=self.edit.value(self.identity,path)
        for key in fields:
            if key not in value:continue
            title=None
            if key=='curve':
                if path and path[0] in ('RotationModule','RotationBySpeedModule'):title='角速度 Z (°/s)' if self.live(path+['separateAxes']) else '角速度 (°/s)'
                elif path and path[0] in ('SizeModule','SizeBySpeedModule'):title='大小 X 倍率' if self.live(path+['separateAxes']) else '大小倍率'
            elif path and path[0] in ('RotationModule','RotationBySpeedModule') and key in ('x','y','z'):
                title='角速度 '+key.upper()+' (°/s)'
            self.value(layout,path+[key],title)
    def advanced(self,layout,path,used):
        extra=[k for k in self.edit.value(self.identity,path) if k not in set(used)|{'enabled','m_GameObject'}]
        if not extra:return
        body=self.block(layout,path+['_advanced'],'高级 / 其余原生字段',True)
        if body:
            for k in extra:self.api.draw_value(body,self.obj,self.edit,self.identity,path+[k],self.edit.value(self.identity,path+[k]))
    def main(self,layout):
        system=['lengthInSec','looping','prewarm','startDelay','simulationSpeed','moveWithTransform','scalingMode','playOnAwake','autoRandomSeed']
        self.fields(layout,[],system)
        if not self.live(['autoRandomSeed']):self.value(layout,['randomSeed'])
        if self.live(['moveWithTransform'])==2:self.value(layout,['moveWithCustomTransform'])
        layout.separator();layout.label(text='粒子出生设置')
        initial=['startLifetime','startSpeed','startColor','size3D','startSize']
        if self.live(['InitialModule','size3D']):initial+=['startSizeY','startSizeZ']
        initial+=['rotation3D']
        if self.live(['InitialModule','rotation3D']):initial+=['startRotationX','startRotationY']
        initial+=['startRotation','gravityModifier','maxNumParticles']
        self.fields(layout,['InitialModule'],initial)
        self.advanced(layout,['InitialModule'],initial)
        self.advanced(layout,[],system+['moveWithCustomTransform']+list(MODULES))
    def module(self,layout,module):
        data=self.edit.value(self.identity,[module]);title,helptext=MODULES[module]
        body=self.block(layout,[module],title,module not in ('InitialModule','EmissionModule','ShapeModule'),module!='InitialModule')
        if not body:return
        hint(body,helptext,icon='INFO')
        body.enabled=bool(self.live([module,'enabled']))
        if module=='InitialModule':self.main(body);return
        if module=='EmissionModule':
            op=body.operator('eiem.effect_continuous',text='设为持续发射…',icon='TIME');op.object_name=self.obj.name;op.identity=self.identity
            hint(body,'每秒发射与定时爆发可同时生效；达到最大同时存活数后会暂停生成。')
            scripts=list(self.api.preview.automatic_emission(self.edit,self.obj))
            if scripts:
                warning=body
                hint(warning,'已启用高级自动数量规则，会覆盖这里的每秒发射数。',icon='INFO')
                op=warning.operator('eiem.effect_use_particle_emission',text='使用下方的发射数量',icon='CHECKMARK');op.object_name=self.obj.name
            self.fields(body,[module],['rateOverTime','rateOverDistance','m_Bursts']);return
        if module=='ShapeModule':
            kind=int(self.live([module,'type']));fields=['type']
            if kind not in (5,15,16,18,6,13,14,19,20):fields+=['radius']
            if kind in (0,1,2,3,4,7,8,9,10,11):fields+=['radiusThickness']
            if kind in (4,7,8,9):fields+=['angle','length']
            if kind in (4,7,8,9,10,11,17):fields+=['arc']
            if kind==17:fields+=['donutRadius']
            if kind in (15,16):fields+=['boxThickness']
            if kind in (6,13,14):fields+=['m_Mesh','m_MeshRenderer','m_SkinnedMeshRenderer','m_UseMeshColors','m_MeshNormalOffset']
            if kind in (19,20):fields+=['m_Sprite','m_SpriteRenderer']
            fields+=['m_Position','m_Rotation','m_Scale','alignToDirection','randomDirectionAmount','sphericalDirectionAmount','randomPositionAmount']
        else:
            fields=[k for k in data if k!='enabled']
            if module in ('SizeModule','SizeBySpeedModule','RotationModule','RotationBySpeedModule'):
                fields=['separateAxes']+(['x','curve','y','z'] if self.live([module,'separateAxes']) else ['curve'])+(['range'] if 'range' in data else [])
            if module=='ClampVelocityModule':
                fields=['separateAxis']+(['x','y','z'] if self.live([module,'separateAxis']) else ['magnitude'])+['inWorldSpace','dampen','drag','multiplyDragByParticleSize','multiplyDragByParticleVelocity']
            if module=='NoiseModule':
                fields=['separateAxes','strength']+(['strengthY','strengthZ'] if self.live([module,'separateAxes']) else [])+['frequency','damping','octaves','octaveMultiplier','octaveScale','quality','scrollSpeed','positionAmount','rotationAmount','sizeAmount','remapEnabled']
                if self.live([module,'remapEnabled']):fields+=['remap','remapY','remapZ']
            if module=='UVModule':
                fields=['mode']
                if int(self.live([module,'mode']))==0:
                    fields+=['tilesX','tilesY','animationType','timeMode']
                    time_mode=int(self.live([module,'timeMode']))
                    fields+=['fps'] if time_mode==2 else ['speedRange','frameOverTime'] if time_mode==1 else ['frameOverTime']
                    fields+=['startFrame','cycles']
                    if int(self.live([module,'animationType']))==1:
                        fields+=['rowMode']
                        if int(self.live([module,'rowMode']))==0:fields+=['rowIndex']
                else:fields+=['sprites','frameOverTime','startFrame','cycles']
                fields+=['uvChannelMask','flipU','flipV']
            if module=='CustomDataModule':
                fields=[]
                for stream in range(2):
                    mode=int(self.live([module,'mode'+str(stream)]));fields+=['mode'+str(stream)]
                    if mode==1:fields+=['vectorComponentCount'+str(stream)]+['vector'+str(stream)+'_'+str(i) for i in range(min(4,max(0,int(self.live([module,'vectorComponentCount'+str(stream)])))))]
                    if mode==2:fields+=['color'+str(stream)]
        self.fields(body,[module],fields);self.advanced(body,[module],fields)
    def particle(self,layout):
        hint(layout,'取消勾选仅停用模块；在原位置重新勾选即可恢复。',icon='INFO')
        for module in ('InitialModule','EmissionModule','ShapeModule'):self.module(layout,module)
        for title,modules in GROUPS:
            available=[m for m in modules if m in self.edit.value(self.identity)]
            if not available:continue
            layout.label(text=title)
            for module in available:self.module(layout,module)
    def renderer(self,layout):
        body=self.block(layout,['_renderer'],'渲染：面片 / Mesh / 材质',True)
        if not body:return
        fields=['m_Enabled','m_RenderMode','m_Materials']
        mode=int(self.live(['m_RenderMode']))
        if mode==4:fields+=['m_Mesh','m_Mesh1','m_Mesh2','m_Mesh3']
        if mode==1:fields+=['m_CameraVelocityScale','m_VelocityScale','m_LengthScale']
        fields+=['m_RenderAlignment','m_Pivot','m_Flip','m_SortMode','m_SortingFudge','m_SortingOrder','m_MinParticleSize','m_MaxParticleSize','m_AllowRoll','m_MotionVectors']
        self.fields(body,[],fields);self.advanced(body,[],fields)
        if mode==4:
            body.separator();body.prop(self.obj.eff_effect,'particle_mesh')
            op=body.operator('eiem.effect_mesh_edit',text='编辑粒子 Mesh',icon='MESH_DATA')
            op.object_name=self.obj.name;op.renderer=self.identity
            body.label(text='网格的 EFF 材质槽会导出到粒子渲染器；对象位置不影响发射区域。')
            body.label(text='支持网格/UV/静态修改器；当前每个网格一个材质。')
        for index in range(len(self.edit.value(self.identity).get('m_Materials',[]))):
            mid=self.api.assets.renderer_material(self.api.root_of(self.obj),self.edit,self.identity,index)
            spec=self.edit.objects.get(mid,{})
            row=body.row(align=True);row.label(text='使用材质 '+str(index+1)+'：'+(spec.get('name') or '无'))
            if mid:
                op=row.operator('eiem.effect_resource',text='编辑材质 / 贴图',icon='MATERIAL');op.identity=mid

    def material(self,layout):
        self.api.material_bridge.draw(layout,self.obj)
    def component(self,layout,spec):
        kind=(spec.get('script') or {}).get('class') or spec['type']
        if spec['classId']==198:self.particle(layout)
        elif spec['classId']==199:self.renderer(layout)
        elif spec['classId']==21:self.material(layout)
        elif kind=='EffectSetting':
            body=self.block(layout,['_game'],'游戏整体控制（所有发射器）',True)
            if body:
                data=self.edit.value(self.identity)
                if 'effectLogicCfg' in data:self.fields(body,['effectLogicCfg'],list(data['effectLogicCfg']))
                body.label(text='游戏脚本控制；离线粒子预览暂不执行此组件。',icon='INFO')
                self.advanced(body,[],['effectLogicCfg'])
        elif kind=='VFXAutoEmissiveRate':
            body=self.block(layout,['_automatic_emission'],'高级：按缩放自动计算数量',True)
            if body:
                hint(body,'默认关闭，直接使用“发射数量”。仅需按挂点缩放改数量时开启。',icon='INFO')
                self.value(body,['m_Enabled'],title='启用自动数量')
                settings=body
                self.value(settings,['rateOverTime'],title='单位缩放发射数')
                self.fields(settings,[],['scaleMode','particleSystemScalingMode','axisBitMask'])
                hint(settings,'轴遮罩：X=1，Y=2，Z=4；相加选择，例如 XYZ=7。')
                hint(settings,'对应原组件 VFXAutoEmissiveRate；开启后会修改随机范围的上界。')
        elif spec['classId']==4:
            body=self.block(layout,['_transform'],'节点变换',True)
            if body:
                body.prop(self.obj,'location',text='位置');body.prop(self.obj,'rotation_euler',text='旋转');body.prop(self.obj,'scale',text='缩放')
        else:
            body=self.block(layout,['_component'],kind+' · 来源组件',True)
            if body:
                for k,v in self.edit.value(self.identity).items():
                    if k not in ('m_GameObject','m_Script','references'):self.value(body,[k])
