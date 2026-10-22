"""Material UI policy. All edits retain the existing EFF material wire format.

Profiles describe verified Shader families, not arbitrary similarly named
properties. Legacy snapshots omit the Shader name, so recognise their full
characteristic field/texture signature; an explicit different Shader wins.
Opening the UI never applies a preset or changes an authored field.
"""


def field(material, name, kind='float', default=''):
    return material.get('eiem_' + kind + '.' + name, default)


def truth(value):
    try:
        return float(value) != 0
    except (TypeError, ValueError):
        return str(value).lower() == 'true'


def texture_slots(material):
    return sorted({key.split('.', 1)[1] for key in material.keys()
                   if key.startswith(('eiem_texture.', 'eiem_texture_scale.', 'eiem_texture_offset.'))})


def profile(material):
    shader = str(material.get('eiem_shader', '')).strip()
    if shader:
        return {'HGRP/CharacterNPR': 'character', 'HGRP/CharacterNPR_VFX': 'vfx',
                'HGRP/Effect/VFXDitherAlpha': 'vfx_dither'}.get(shader)
    def declared(slot):
        return any(prefix + slot in material for prefix in
                   ('eiem_texture.', 'eiem_texture_scale.', 'eiem_texture_offset.'))
    if (all('eiem_float.' + name in material for name in
            ('_SurfaceType', '_UseMetallicGlossMap', '_UseBumpMap', '_UseDiffRampMap',
             '_UseSpecRampMap', '_EnableCharacterVFX', '_EnableAlphaTest'))
            and all(declared(slot) for slot in ('_BaseMap', '_BumpMap', '_MetallicGlossMap', '_VFXSpecialMainTex', '_VFXSpecialBlendTex'))):
        return 'character'
    if (all('eiem_float.' + name in material for name in
            ('_UseBlend', '_UseMask', '_UseDisturb', '_UseMainTexAsAlpha'))
            and all(declared(slot) for slot in ('_MainTex', '_BlendTex', '_MaskTex'))
            and 'eiem_value4._MainTexUVSpeed' in material):
        return 'vfx'
    return None


def feature(key, label, switch=None, keyword=None, slots=(), prefixes=(), names=(), parent=None):
    return dict(key=key, label=label, switch=switch, keyword=keyword,
                slots=slots, prefixes=prefixes, names=names, parent=parent)


CHARACTER = (
    feature('pbr', 'PBR 参数贴图', '_UseMetallicGlossMap', '_METALLICSPECGLOSSMAP',
            ('_MetallicGlossMap',), ('_Metallic', '_Smoothness', '_Specular'), ('_WorkflowMode', '_PBRInput')),
    feature('normal', '法线', '_UseBumpMap', '_NORMALMAP', ('_BumpMap',), ('_Bump',)),
    feature('diffuse', '明暗 Ramp', '_UseDiffRampMap', '_DIFF_RAMP_ON',
            ('_DiffRampMap',), ('_DiffRamp',), ('_RampCurveOffset',)),
    feature('specular', '高光 Ramp 与虹彩', '_UseSpecRampMap', '_SPEC_RAMP_ON',
            ('_SpecRampMap',), ('_SpecRamp',)),
    feature('anisotropy', '各向异性高光', '_UseAnisotropy', '_ANISOTROPY_SPECULAR_ON',
            (), ('_Anisotropy',)),
    feature('clearcoat', '清漆涂层', '_ClearCoat', '_CLEARCOAT',
            ('_ClearCoatMask',), ('_ClearCoat',)),
    feature('fresnel', '菲涅耳边缘光', '_EnableStylizedFresnel', '_STYLIZED_FRESNEL',
            ('_StylizedFresnelNoiseMap',), ('_StylizedFresnel', '_StylizedNoise')),
    feature('emission', '自发光', '_UseEmission', '_EMISSION',
            ('_EmissionMap',), names=('_EmissionColor', '_EmissionBrightness')),
    feature('breath', '呼吸动画', '_EmissionAlphaBrightBreath',
            prefixes=('_EmissionAlphaBrightBreath',), parent='emission'),
    feature('vfx', '角色 VFX 与 UV 动画', '_EnableCharacterVFX', '_CHARACTER_VFX_SPECIAL',
            ('_VFXSpecialMainTex', '_VFXSpecialBlendTex'), ('_VFX',),
            ('_EnableVFXColorAdjustment', '_BaseMapUVSpeed', '_EmissionMapUVSpeed', '_UseVFXMainTexAsAlpha')),
    feature('parallax', '视差层次', '_UseParallax', '_PARALLAX_MAP',
            ('_ParallaxTex',), ('_Parallax',)),
    feature('outline', '描边', '_EnableOutline', slots=('_OutlineMask',),
            prefixes=('_Outline',), names=('_EnableOutlineMask',)),
)
VFX = (
    feature('main', '特效主图与颜色', slots=('_MainTex',),
            prefixes=('_MainTex', '_TintColor'), names=('_MainUVSet', '_UseMainTexAsAlpha', '_MainUVWeights')),
    feature('blend', '混合图层', '_UseBlend', '_USE_BLEND', ('_BlendTex',),
            ('_BlendTex', '_BlendTint', '_BlendUV')),
    feature('mask', '透明遮罩', '_UseMask', '_USE_MASK', ('_MaskTex',),
            ('_MaskTex', '_MaskUV'), ('_UseMaskTexAsAlpha',)),
    feature('disturb', 'UV 扰动', '_UseDisturb', '_USE_DISTURB', ('_DisturbTex1',),
            ('_Disturb',), ('_Bi_Disturb',)),
    feature('normal', '法线', '_EnableNormalMap', '_NORMAL_MAP', ('_NormalMap',), ('_NormalMap',), ('_NormalScale',)),
    feature('fresnel', '菲涅耳边缘光', '_UseFresnel', '_USE_FRESNEL', prefixes=('_Fresnel',)),
    feature('emission', '特效发光', '_UseEmission', '_EMISSION', prefixes=('_Emission',)),
)
# The pink-petal source Shader's native keyword table was checked directly.
# Use its own main2/dissolve switches instead of CharacterNPR_VFX's blend layer.
VFX_DITHER = tuple(item for item in VFX if item['key'] not in ('blend', 'emission')) + (
    feature('main2', '第二主图', '_UseMain2', '_USE_MAIN2', ('_MainTex2',),
            ('_MainTex2',), ('_UseMainTex2AsAlpha',)),
    feature('dissolve', '溶解', '_UseDissolve', '_USE_DISSOLVE', ('_DissolveTex',), ('_Dissolve',)),
)
PROFILES = {'character': CHARACTER, 'vfx': VFX, 'vfx_dither': VFX_DITHER}

# Frequently edited controls first. Less common source fields remain in the
# feature's secondary foldout and in the complete raw view, without crowding
# the primary workflow or pretending every saved legacy field is necessary.
PRIMARY = {
    'pbr': ('_Metallic', '_Specular', '_Smoothness'),
    'normal': ('_BumpScale', '_NormalScale', '_NormalMapUVSpeed'),
    'diffuse': ('_DiffRampAddictive', '_RampCurveOffset'),
    'specular': ('_SpecRampIridescentMode',),
    'anisotropy': ('_AnisotropyValue', '_AnisotropyDirection', '_AnisotropyIntensityMultiplier'),
    'clearcoat': ('_ClearCoatColor', '_ClearCoatSmoothness', '_ClearCoatMetallic', '_ClearCoatNormalMode'),
    'fresnel': ('_StylizedFresnelColor', '_StylizedFresnelAmount', '_StylizedFresnelPow',
                '_StylizedFresnelNoiseSpeed', '_StylizedNoiseContrast', '_FresnelColor', '_FresnelPower', '_FresnelFlip'),
    'emission': ('_EmissionColor', '_EmissionBrightness'),
    'breath': ('_EmissionAlphaBrightBreathSpeed', '_EmissionAlphaBrightBreathScaleMin', '_EmissionAlphaBrightBreathScaleMax'),
    'vfx': ('_BaseMapUVSpeed', '_EmissionMapUVSpeed', '_VFXSpecialParam',
            '_UseVFXMainTexAsAlpha', '_VFXColor', '_VFXColorIntensity', '_VFXColorAlpha',
            '_VFXBlendTint', '_VFXSpecialBlendTexRForDisturb', '_VFXAlphaAffectTotalMat'),
    'parallax': ('_ParallaxColor', '_ParallaxScale', '_ParallaxMarchNum', '_ParallaxUseNormal'),
    'outline': ('_OutlineWidth', '_EnableOutlineMask', '_OutlineColorBrightness'),
    'main': ('_TintColor', '_TintColorIntensity', '_TintColorAlpha', '_UseMainTexAsAlpha', '_MainTexUVSpeed', '_MainUVSet'),
    'blend': ('_BlendTint', '_BlendTexUVSpeed', '_BlendUVSet'),
    'mask': ('_UseMaskTexAsAlpha', '_MaskTexUVSpeed', '_MaskUVSet'),
    'disturb': ('_DisturbIntensity', '_DisturbUIntensity1', '_DisturbVIntensity1', '_DisturbUVSpeed1', '_Bi_Disturb'),
    'main2': ('_MainTex2Color', '_UseMainTex2AsAlpha', '_MainTex2UVSpeed', '_MainTex2BlendMode'),
    'dissolve': ('_DissolveScheduleOffset', '_DissolveEdgeSharp', '_DissolveEmissiveEdge'),
}


def features(material):
    return tuple(item for item in PROFILES.get(profile(material), ())
                 if item['switch'] is None or 'eiem_float.' + item['switch'] in material)


def get_feature(material, key):
    return next((item for item in features(material) if item['key'] == key), None)


def active(material, item):
    if item['switch'] is None:
        return True
    return truth(field(material, item['switch']))


def set_feature(material, key, enabled):
    item = get_feature(material, key)
    if item is None or item['switch'] is None:
        raise ValueError('来源材质没有声明此功能：' + key)
    material['eiem_float.' + item['switch']] = '1' if enabled else '0'
    if item['keyword']:
        material['eiem_keyword.' + item['keyword']] = '1' if enabled else '0'
    if profile(material) == 'character':
        if key == 'breath' and enabled:
            # This Shader's breath calculation exists only in its VFX branch.
            set_feature(material, 'emission', True)
            set_feature(material, 'vfx', True)
        elif key == 'vfx' and not enabled and get_feature(material, 'breath'):
            set_feature(material, 'breath', False)
    # Texture rows are virtual until the author selects a file. Do not assign
    # images, overwrite source textures, or turn an empty inherited slot into
    # an authored override just to make a folder button visible.


def set_switch(material, name, enabled):
    """Synchronise known switches even when edited in the raw parameter view."""
    for item in features(material):
        if item['switch'] == name:
            set_feature(material, item['key'], enabled)
            return
    material['eiem_float.' + name] = '1' if enabled else '0'
    extra = {'_EnableOutlineMask': '_OUTLINE_MASK', '_EnableAlphaTest': '_ALPHATEST_ON',
             '_EnablePlanarReflection': '_PLANAR_REFLECTION', '_UseShadowLutTex': '_SHADOW_LUT_TEX'}
    if profile(material) == 'character' and name in extra:
        material['eiem_keyword.' + extra[name]] = '1' if enabled else '0'


SURFACE_ITEMS = (
    ('OPAQUE', '不透明', '完整表面，不使用透明混合或 Alpha 裁剪', 0),
    ('CUTOUT', 'Alpha 裁剪（镂空）', '按 D 图 Alpha 丢弃像素，自动同步裁剪关键字和队列', 1),
    ('TRANSPARENT', '半透明（预乘混合）', '参考原生 cloth07，保留当前深度写入和双面设置', 2),
    ('CUSTOM', '自定义组合', '原有字段组合保留，可在原始参数中查看', 3),
)


def surface_mode(material):
    transparent = truth(field(material, '_SurfaceType', default='0'))
    clip = truth(field(material, '_EnableAlphaTest', default='0'))
    if transparent:
        try:
            premultiplied = float(field(material, '_BlendMode', default='4')) == 4
        except (TypeError, ValueError):
            premultiplied = False
        if clip or not premultiplied:
            return 3
        return 2
    return 1 if clip else 0


def set_surface(material, mode):
    if profile(material) != 'character':
        raise ValueError('此表面预设只适用于 CharacterNPR')
    if mode not in (0, 1, 2):
        return
    transparent, clip = mode == 2, mode == 1
    changes = {'_SurfaceType': int(transparent), '_EnableAlphaTest': int(clip),
               '_BlendMode': 4 if transparent else 0, '_AlphaPremultiply': int(transparent),
               '_SrcBlend': 1, '_DstBlend': 10 if transparent else 0,
               '_AlphaSrcBlend': 1, '_AlphaDstBlend': 10 if transparent else 0,
               '_ZTest': 4 if transparent else 3}
    for name, value in changes.items():
        material['eiem_float.' + name] = str(value)
    for name, value in (('_ALPHATEST_ON', clip), ('_ALPHABLEND_ON', transparent)):
        material['eiem_keyword.' + name] = str(int(value))
    material['eiem_render_queue'] = str(3000 if transparent else 2450 if clip else 2000)
    material['eiem_tag.RenderType'] = 'Transparent' if transparent else 'Opaque'
    for name, value in (('_AlphaClipThreshold', '0.5'), ('_ZWrite', '1'), ('_TransparentDepthWrite', '1')):
        if 'eiem_float.' + name not in material:
            material['eiem_float.' + name] = value


def property_name(key):
    return key.removeprefix('eiem_').partition('.')[2]


def owner(material, key, items=None):
    name = property_name(key)
    # Specific groups win over the PBR prefix (_Specular vs _SpecRamp), and
    # animation parameters remain with the VFX branch that actually uses them.
    items = features(material) if items is None else items
    for item in items:
        if name == item['switch'] or name == item['keyword'] or name in item['names'] or name in item['slots']:
            return item['key']
    # _MainTex2 fields belong to the second layer rather than _MainTex.
    matching = [(len(prefix), item['key']) for item in items for prefix in item['prefixes']
                if name.startswith(prefix)]
    if matching:
        return max(matching, key=lambda match: match[0])[1]
    if name in ('_BaseColor', '_BaseMap', '_Color'):
        return 'surface'
    return None


def visible_slots(material, item):
    if not active(material, item):
        return ()
    declared = set(texture_slots(material))
    return tuple(slot for slot in item['slots'] if slot in declared)


LABELS = {
    '_BaseColor': '基础颜色与透明度', '_BaseMap': '基础颜色 D（A 为透明度）',
    '_BumpMap': '法线贴图 N', '_BumpScale': '法线强度', '_MetallicGlossMap': 'PBR 参数贴图 P',
    '_Metallic': '金属度', '_Smoothness': '光滑度', '_Specular': '高光强度',
    '_EmissionMap': '发光贴图 E', '_EmissionColor': '发光颜色', '_EmissionBrightness': '发光亮度',
    '_EmissionAlphaBrightBreathSpeed': '呼吸速度', '_EmissionAlphaBrightBreathScaleMin': '最暗倍率',
    '_EmissionAlphaBrightBreathScaleMax': '最亮倍率', '_BaseMapUVSpeed': 'D 图滚动速度（U/V）',
    '_EmissionMapUVSpeed': 'E 图滚动速度（U/V）', '_AlphaClipThreshold': '镂空阈值',
    '_ClearCoatColor': '涂层颜色', '_ClearCoatSmoothness': '涂层光滑度',
    '_ClearCoatMetallic': '涂层金属度', '_ClearCoatNormalMode': '涂层法线来源',
    '_ClearCoatMask': '清漆覆盖遮罩', '_SpecRampMap': '高光颜色 Ramp',
    '_SpecRampIridescentMode': '虹彩模式', '_DiffRampMap': '明暗颜色 Ramp',
    '_StylizedFresnelColor': '边缘颜色（A 为发光贡献）', '_StylizedFresnelPow': '边缘集中程度',
    '_StylizedFresnelAmount': '边缘强度', '_StylizedFresnelNoiseMap': '边缘噪波遮罩',
    '_StylizedFresnelNoiseSpeed': '边缘噪波速度', '_StylizedNoiseContrast': '噪波对比度',
    '_VFXSpecialMainTex': 'VFX 主图／固定遮罩', '_VFXSpecialBlendTex': 'VFX 混合／流动图',
    '_VFXSpecialParam': '滚动速度（主图 U/V、混合图 U/V）',
    '_VFXColor': 'VFX 主图颜色', '_VFXColorIntensity': 'VFX 主图亮度',
    '_VFXColorAlpha': 'VFX 主图不透明度', '_VFXBlendTint': 'VFX 混合图颜色与亮度',
    '_VFXSpecialBlendTexRForDisturb': '混合图 R 扰动主图 UV', '_UseVFXMainTexAsAlpha': '主图 R 作为遮罩',
    '_VFXAlphaAffectTotalMat': 'VFX 遮罩影响整个表面', '_VFXFresnelAffectOpacity': 'VFX 边缘影响透明度',
    '_ParallaxTex': '视差图', '_ParallaxScale': '视差深度', '_ParallaxMarchNum': '视差采样步数',
    '_ParallaxColor': '视差颜色', '_ParallaxUseNormal': '视差使用法线贴图',
    '_OutlineMask': '描边遮罩', '_OutlineWidth': '描边宽度', '_EnableOutlineMask': '使用描边遮罩',
    '_MainTex': '特效主图', '_BlendTex': '混合图', '_MaskTex': '透明遮罩',
    '_DisturbTex1': 'UV 扰动图', '_NormalMap': '法线图', '_TintColor': '主图颜色',
    '_TintColorIntensity': '主图亮度', '_TintColorAlpha': '主图不透明度', '_BlendTint': '混合颜色与亮度',
    '_MainTexUVSpeed': '主图速度（U/V）', '_BlendTexUVSpeed': '混合图速度（U/V）',
    '_MaskTexUVSpeed': '遮罩速度（U/V）', '_UseMainTexAsAlpha': '主图 R 作为透明度',
    '_UseMaskTexAsAlpha': '遮罩 R 作为透明度', '_Cull': '绘制面', '_ZWrite': '深度写入',
    '_TransparentDepthWrite': '透明深度写入', '_ZTest': '深度比较',
}


def label(key):
    return LABELS.get(property_name(key), property_name(key)) or '渲染队列'
