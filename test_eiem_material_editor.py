"""Run in factory-startup Blender. Check RNA values and read-only panel drawing."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys
import bpy

parser=argparse.ArgumentParser()
parser.add_argument('--package-root',type=Path,default=Path(__file__).parent)
args=parser.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else [])
root=args.package_root.resolve()
spec=importlib.util.spec_from_file_location('EFFMaterialEditorTest',root/'__init__.py',submodule_search_locations=[str(root)])
package=importlib.util.module_from_spec(spec);sys.modules[spec.name]=package;spec.loader.exec_module(package);package.register()
editor=package.eiem_blender_addon.material_editor
mat=bpy.data.materials.new('Editor Test')
mat['eiem_source']='assets/test.mat'
mat['eiem_float._UseEmission']='0'
mat['eiem_keyword._EMISSION']='0'
mat['eiem_float._EmissionBrightness']='8'
mat['eiem_value4._EmissionColor']='4,0.2,0.1,1'
mat['eiem_float._SurfaceType']='0'
mat['eiem_render_queue']=2000
mat['eiem_texture._BaseMap']='source.png'
mat['eiem_texture._EmissionMap']='source_emission.png'
mat['eiem_texture_scale._HighlightMap']='1,1'
mat['eiem_texture_offset._HighlightMap']='0,0'
mat['eiem_texture_scale._EmissionMap']='1,1'

queue_mat=bpy.data.materials.new('Default Queue Editor Test')
queue_mat['eiem_source']='assets/test.mat'

class Layout:
    def __init__(self,fields,labels=None):
        self.fields=fields
        self.labels=labels if labels is not None else []
        self.alignment='EXPAND'
    def box(self):return Layout(self.fields,self.labels)
    def row(self,**kwargs):return Layout(self.fields,self.labels)
    def split(self,**kwargs):return Layout(self.fields,self.labels)
    def label(self,**kwargs):self.labels.append((kwargs.get('text'),self.alignment))
    def prop(self,owner,name,**kwargs):
        getattr(owner,name)
        self.fields.append((getattr(owner,'key',''),name))
    def operator(self,*args,**kwargs):return type('Operation',(),{})()

try:
    properties=list(bpy.types.Material.bl_rna.properties.keys())
    for small in ('eiem_editor_groups','eiem_editor_features','eiem_material_animations'):
        assert properties.index(small)<properties.index('eiem_editor_values'), 'large collection precedes '+small
    for name in ('expanded','details_expanded'):
        assert not editor.EFF_PG_material_group.bl_rna.properties[name].is_animatable, 'UI fold state became animatable'
    assert 'uv_expanded' not in editor.EFF_PG_material_value.bl_rna.properties
    color_property=editor.EFF_PG_material_value.bl_rna.properties['color']
    hdr = bpy.data.materials.new('Uncapped blend brightness')
    hdr['eiem_value4._BlendTint'] = '1,0.5,0.25,1'
    hdr.eiem_blend_brightness = 128
    assert hdr.eiem_blend_brightness == 128, 'HDR brightness was arbitrarily capped'
    assert hdr['eiem_value4._BlendTint'] == '128,64,32,1', 'HDR lost its tint'
    assert (color_property.hard_min,color_property.hard_max)==(0.0,1.0)
    assert (color_property.soft_min,color_property.soft_max)==(0.0,1.0)
    parameter_key = 'eiem_value4._BaseColor'
    hdr[parameter_key] = '0.25,0.5,0.75,1.5'
    status = bpy.ops.eiem.material_parameter(material_name=hdr.name, key=parameter_key,
                                             channel='a', label='透明度', widget='SLIDER', maximum=2)
    assert status == {'FINISHED'}
    data, = editor.parameter_specs(hdr)
    assert (data['target'], data['property'], data['channel'], data['default']) == (
        'material.color', '_BaseColor', 'a', 1.5)
    assert (data['label'], data['maximum']) == ('透明度', 2)
    assert hdr[parameter_key] == '0.25,0.5,0.75,1.5'
    overview = Layout([])
    editor.EFF_PT_material_parameters.draw(type('Panel', (), {'layout': overview})(), bpy.context)
    assert [label for label, _ in overview.labels] == [
        hdr.name, '透明度', '_BaseColor A', '默认值 1.5 · 滑块 0–2']
    hdr['eff_preview_instance'] = True
    overview = Layout([])
    editor.EFF_PT_material_parameters.draw(type('Panel', (), {'layout': overview})(), bpy.context)
    assert [label for label, _ in overview.labels] == ['当前工程没有已公开的材质参数']
    del hdr['eff_preview_instance']
    exporter = package.eiem_blender_addon.native_export
    source = exporter.material_parameter_program('', {'MaterialCloth': [data]})
    assert 'material=MaterialCloth\nproperty=_BaseColor\nchannel=a\n' in source
    assert 'default=1.5\npublic=true\nlabel=透明度\nui.type=slider\nmin=0\nmax=2\n' in source
    assert bpy.ops.eiem.material_parameter(material_name=hdr.name, key=parameter_key, channel='a',
                                           label='透明度', widget='SLIDER', maximum=1,
                                           default_value=.625) == {'FINISHED'}
    data, = editor.parameter_specs(hdr)
    assert (data['default'], data['maximum']) == (.625, 1)
    assert hdr[parameter_key] == '0.25,0.5,0.75,0.625'
    assert bpy.ops.eiem.material_parameter(material_name=hdr.name, key=parameter_key,
                                           parameter_id=parameter_key + ':a', remove=True) == {'FINISHED'}
    assert not list(editor.parameter_specs(hdr))
    editor._prepare_material(queue_mat)
    queue_row=queue_mat.eiem_editor_values['eiem_render_queue']
    assert queue_row.render_queue==2000, 'an absent native queue must display its actual opaque queue'
    fields=[];editor.draw(Layout(fields),queue_mat)
    assert ('eiem_render_queue','render_queue') in fields, 'queue control must be visible in raw material view'
    assert 'eiem_render_queue' not in queue_mat, 'displaying the default must not author a queue override'
    queue_mat['eiem_shader']='HGRP/CharacterNPR'
    editor._prepare_material(queue_mat)
    fields=[];editor.draw(Layout(fields),queue_mat)
    assert ('eiem_render_queue','render_queue') in fields, 'queue control must be visible in CharacterNPR view'
    queue_property=editor.EFF_PG_material_value.bl_rna.properties['render_queue']
    assert (queue_property.hard_min,queue_property.hard_max)==(0,5000)
    assert 'eiem_render_queue' not in queue_mat, 'profile setup must not author a queue override'
    payload,_=package.eiem_blender_addon.material_override_payload(queue_mat,{},force=True)
    assert 'render_queue=2000' in payload, 'default queue was not serialized as an actual value'
    queue_mat['eiem_float._SurfaceType']='1'
    queue_mat['eiem_float._BlendMode']='4'
    payload,_=package.eiem_blender_addon.material_override_payload(queue_mat,{},force=True)
    assert 'render_queue=3000' in payload, 'transparent default queue was not inferred'
    del queue_mat['eiem_float._SurfaceType'];del queue_mat['eiem_float._BlendMode']
    queue_row.render_queue=2001
    payload,_=package.eiem_blender_addon.material_override_payload(queue_mat,{},force=True)
    assert 'render_queue=2001' in payload, 'queue control did not reach the native export payload'

    before = (set(mat.keys()), len(mat.eiem_editor_values), len(mat.eiem_editor_groups))
    editor.draw(Layout([]), mat)
    after = (set(mat.keys()), len(mat.eiem_editor_values), len(mat.eiem_editor_groups))
    assert after == before, 'panel draw mutated Material data; Blender forbids these writes'
    editor._prepare_pending()
    fields=[];editor.draw(Layout(fields),mat)
    assert ('eiem_float._UseEmission','boolean') in fields
    assert ('eiem_float._EmissionBrightness','scalar') not in fields
    assert ('eiem_texture._EmissionMap','text') in fields, 'unknown shader raw view must show disabled source textures'
    assert ('eiem_texture_scale._HighlightMap','vector2') not in fields, 'unused empty transform was exposed'
    assert '_HighlightMap' in editor.texture_slots(mat)
    row=editor._row(mat,'eiem_float._UseEmission');row.boolean=True
    assert mat['eiem_float._UseEmission']=='1'
    fields=[];editor.draw(Layout(fields),mat)
    assert ('eiem_float._EmissionBrightness','scalar') in fields
    color=editor._row(mat,'eiem_value4._EmissionColor')
    assert all(abs(actual-expected)<1e-6 for actual,expected in zip(color.color,(1.0,0.2,0.1,1.0))), 'normal color widget must display clamped HDR values'
    assert mat['eiem_value4._EmissionColor']=='4,0.2,0.1,1', 'reading normal color must preserve stored HDR data'
    color.color=(3,0.4,0.2,1)
    written=tuple(float(value) for value in mat['eiem_value4._EmissionColor'].split(','))
    assert all(0.0 <= value <= 1.0 for value in written), 'normal color input must stay in 0-1'
    assert all(abs(actual-expected)<1e-6 for actual,expected in zip(written,(1,0.4,0.2,1)))
    mat.eiem_editor_advanced=True
    fields=[];editor.draw(Layout(fields),mat)
    assert ('eiem_value4._EmissionColor','vector4') in fields, 'raw mode must expose numeric HDR editing'
    color.vector4=(4,0.2,0.1,1)
    written=tuple(float(value) for value in mat['eiem_value4._EmissionColor'].split(','))
    assert all(abs(actual-expected)<1e-6 for actual,expected in zip(written,(4,0.2,0.1,1)))
    mat.eiem_editor_advanced=False
    queue=editor._row(mat,'eiem_render_queue');queue.render_queue=2999;assert mat['eiem_render_queue']=='2999'
    # New imported fields must initialise without writing during draw, even
    # when the corresponding category/texture transform has never been opened.
    extra = {
        'eiem_float._EnableOutline':'1', 'eiem_float._OutlineWidth':'0.5',
        'eiem_float._Smoothness':'0.6', 'eiem_value4._DissolveUVSpeed':'0,1,0,0',
        'eiem_float._FaceSDFOffset':'0.2', 'eiem_float._WindStrength':'0.5',
        'eiem_int._ExtraInteger':'3', 'eiem_color._BaseColor':'1,0.5,0.2,1',
        'eiem_tag.RenderType':'Transparent', 'eiem_section':'Material_1',
    }
    for key,value in extra.items():mat[key]=value
    before=(set(mat.keys()),len(mat.eiem_editor_values),len(mat.eiem_editor_groups))
    editor.draw(Layout([]),mat)
    assert before==(set(mat.keys()),len(mat.eiem_editor_values),len(mat.eiem_editor_groups))
    editor._prepare_pending()
    assert not editor._PENDING_MATERIALS
    for group in mat.eiem_editor_groups:group.expanded=True
    mat.eiem_editor_uv_expanded=True;mat.eiem_editor_info_expanded=True
    values={key:str(value) for key,value in mat.items() if not key.startswith('eiem_editor_')}
    controls=([(r.name,r.key) for r in mat.eiem_editor_values],[(g.name,g.expanded) for g in mat.eiem_editor_groups])
    labels=[];fields=[]
    for _ in range(3):editor.draw(Layout(fields,labels),mat)
    assert values=={key:str(value) for key,value in mat.items() if not key.startswith('eiem_editor_')}
    assert controls==([(r.name,r.key) for r in mat.eiem_editor_values],[(g.name,g.expanded) for g in mat.eiem_editor_groups])
    for key in extra:
        assert any(field[0]==key for field in fields),f'expanded section lost {key}'
    assert ('eiem_texture_scale._EmissionMap','vector2') in fields
    assert ('eiem_source','text') in fields
    for label in ('_EmissionBrightness','_BaseColor','_ExtraInteger','_BaseMap','渲染队列'):
        assert (label,'LEFT') in labels,f'{label} is not left aligned'
    # Collapse/reopen state stays on the material, rather than being reset by draw.
    mat.eiem_editor_groups['surface'].expanded=False
    fields=[];editor.draw(Layout(fields),mat)
    assert ('eiem_color._BaseColor','color') not in fields
    assert ('eiem_int._ExtraInteger','integer') in fields
    mat.eiem_editor_groups['surface'].expanded=True
    fields=[];editor.draw(Layout(fields),mat)
    assert ('eiem_color._BaseColor','color') in fields
    mat.eiem_editor_search='emission'
    mat['eiem_texture._BaseMap']=''
    mat['eiem_texture._EmissionMap']=''
    values,images=package.eiem_blender_addon.material_override_payload(mat,{},force=True)
    assert not any('editor_' in line for line in values), 'UI state leaked into native material fields'
    assert not any(line.startswith('texture.') for line in values), 'empty slot was exported'

    # Existing CharacterNPR projects get paired feature controls without
    # mutating source fields on opening, drawing, or schema preparation.
    mat['eiem_shader']='HGRP/CharacterNPR'
    for name in ('_ClearCoat','_EnableCharacterVFX','_EmissionAlphaBrightBreath','_UseSpecRampMap',
                 '_EnableStylizedFresnel','_EnableAlphaTest'):
        mat['eiem_float.'+name]='0'
    for slot in ('_ClearCoatMask','_VFXSpecialMainTex','_VFXSpecialBlendTex','_SpecRampMap'):
        mat['eiem_texture_scale.'+slot]='1,1'
        mat['eiem_texture_offset.'+slot]='0,0'
    mat['eiem_float._ClearCoatSmoothness']='0.94'
    mat['eiem_float._EmissionAlphaBrightBreathSpeed']='1.6'
    mat['eiem_value4._BaseMapUVSpeed']='0,0,7,8'
    mat.eiem_editor_search='';mat.eiem_editor_advanced=False
    def native():return {key:str(value) for key,value in mat.items() if not key.startswith('eiem_editor_')}
    before=native();editor._prepare_material(mat)
    fields=[];editor.draw(Layout(fields),mat)
    assert before==native(), 'profile setup/draw rewrote an old source material'
    assert ('eiem_float._ClearCoatSmoothness','scalar') not in fields
    assert ('eiem_texture._ClearCoatMask','text') not in fields
    mat.eiem_editor_features['clearcoat'].enabled=True
    assert mat['eiem_keyword._CLEARCOAT']=='1'
    fields=[];editor.draw(Layout(fields),mat)
    assert ('eiem_texture._ClearCoatMask','text') in fields, 'enabled empty declared slot needs no add-slot action'
    assert ('eiem_float._ClearCoatSmoothness','scalar') in fields
    assert 'eiem_texture._ClearCoatMask' not in mat, 'virtual slot wrote an empty native override'
    mat.eiem_editor_features['clearcoat'].enabled=False
    assert mat['eiem_float._ClearCoatSmoothness']=='0.94'
    mat['eiem_texture._ClearCoatMask']='preserved-mask.png'
    fields=[];editor.draw(Layout(fields),mat)
    assert ('eiem_texture._ClearCoatMask','text') not in fields, 'disabled clearcoat must hide its bound texture path'
    assert mat['eiem_texture._ClearCoatMask']=='preserved-mask.png', 'hiding a disabled texture must preserve its binding'
    assert ('eiem_float._ClearCoatSmoothness','scalar') not in fields
    mat.eiem_editor_features['clearcoat'].enabled=True
    fields=[];editor.draw(Layout(fields),mat)
    assert ('eiem_texture._ClearCoatMask','text') in fields, 're-enabling clearcoat must restore its bound texture row'
    assert mat['eiem_texture._ClearCoatMask']=='preserved-mask.png'
    mat.eiem_editor_features['clearcoat'].enabled=False
    mat['eiem_texture._ClearCoatMask']=''
    mat.eiem_editor_features['vfx'].enabled=True
    mat.eiem_editor_groups['vfx'].expanded=False
    fields=[];editor.draw(Layout(fields),mat)
    for slot in ('_VFXSpecialMainTex','_VFXSpecialBlendTex'):
        assert ('eiem_texture.'+slot,'text') in fields
        assert fields.index(('eiem_texture.'+slot,'text')) < fields.index(('vfx','enabled')), 'texture path was buried inside feature controls'
    editor._row(mat,'eiem_value4._BaseMapUVSpeed').uv_speed=(0,0.2)
    assert mat['eiem_value4._BaseMapUVSpeed'].split(',')[2:]==['7','8']
    mat.eiem_editor_features['breath'].enabled=True
    assert mat['eiem_keyword._EMISSION']=='1' and mat['eiem_keyword._CHARACTER_VFX_SPECIAL']=='1'
    mat.eiem_editor_surface='CUTOUT'
    assert mat['eiem_keyword._ALPHATEST_ON']=='1' and mat['eiem_render_queue']=='2450'
    mat['eiem_float._ZWrite']='1'
    mat.eiem_editor_surface='TRANSPARENT'
    assert mat['eiem_keyword._ALPHATEST_ON']=='0' and mat['eiem_keyword._ALPHABLEND_ON']=='1'
    assert mat['eiem_float._AlphaPremultiply']=='1' and mat['eiem_float._ZWrite']=='1'
    values,_=package.eiem_blender_addon.material_override_payload(mat,{},force=True)
    for value in ('keyword._EMISSION=1','keyword._CHARACTER_VFX_SPECIAL=1',
                  'keyword._ALPHABLEND_ON=1','float._SurfaceType=1','float._AlphaPremultiply=1'):
        assert value in values, 'paired UI state did not reach author payload: '+value
    assert not any('editor_' in value for value in values)
    assert not any(value.startswith('texture._VFXSpecial') for value in values)
    before=native();controls=len(mat.eiem_editor_values)
    for _ in range(3):editor.draw(Layout([]),mat)
    assert native()==before and controls==len(mat.eiem_editor_values), 'profile draw mutated state'
    mat.eiem_editor_advanced=True
    for group in mat.eiem_editor_groups:group.expanded=True
    fields=[];editor.draw(Layout(fields),mat)
    assert ('eiem_float._ClearCoatSmoothness','scalar') in fields, 'raw escape hatch lost a disabled field'
    stockings = bpy.data.materials.new('CharacterNPR stockings')
    stockings['eiem_shader'] = 'HGRP/CharacterNPR'
    stockings['eiem_source'] = 'assets/test.mat'
    original = {
        'float._SurfaceType': '1', 'keyword._ALPHABLEND_ON': '1',
        'float._SilkStockings': '0', 'float._SilkStockingsAdvance': '0',
        'value4._SilkStockingsColor': '0.25,0.25,0.25,1',
        'float._SilkStockingsMinAffect': '0.05', 'float._SilkStockingsMaxAffect': '0.9',
        'texture._BaseMap': 'assets/test_d.tga',
        'texture_scale._SilkStockingsMask': '1,1',
    }
    for key, value in original.items():
        stockings['eiem_' + key] = value
    stockings['eiem_baseline_json'] = json.dumps(original)
    editor._prepare_material(stockings)
    fields = []
    editor.draw(Layout(fields), stockings)
    assert ('stockings', 'enabled') in fields
    assert ('eiem_texture._SilkStockingsMask', 'text') not in fields
    stockings.eiem_editor_features['stockings'].enabled = True
    fields = []
    editor.draw(Layout(fields), stockings)
    assert ('eiem_texture._SilkStockingsMask', 'text') in fields
    assert ('eiem_float._SilkStockingsAdvance', 'boolean') in fields
    color = stockings.eiem_editor_values['eiem_value4._SilkStockingsColor']
    color.color = (0, 0, 0, 1)
    values, _ = package.eiem_blender_addon.material_override_payload(stockings, {})
    assert {'float._SilkStockings=1', 'keyword._SILK_STOCKINGS=1',
            'value4._SilkStockingsColor=0,0,0,1'} <= set(values)
    assert not any(value.startswith(('texture.', 'float._SurfaceType=', 'keyword._ALPHABLEND_ON=',
                                     'keyword._STYLIZED_FRESNEL=')) for value in values)
    stockings.eiem_editor_values['eiem_float._SilkStockings'].boolean = False
    assert not stockings.eiem_editor_features['stockings'].enabled
    assert stockings['eiem_keyword._SILK_STOCKINGS'] == '0'
    assert stockings['eiem_value4._SilkStockingsColor'] == '0,0,0,1'
    effect=bpy.data.materials.new('Dedicated VFX Editor Test')
    try:
        effect['eiem_shader']='HGRP/CharacterNPR_VFX'
        for name,value in (('_SurfaceType','1'),('_EnableAlphaTest','0'),
                           ('_BlendMode','0'),('_AlphaPremultiply','0'),
                           ('_SrcBlend','1'),('_DstBlend','10'),
                           ('_AlphaSrcBlend','1'),('_AlphaDstBlend','10'),
                           ('_ZWrite','0'),('_ZTest','4')):
            effect['eiem_float.'+name]=value
        effect['eiem_keyword._ALPHABLEND_ON']='1'
        effect['eiem_keyword._ALPHATEST_ON']='0'
        effect['eiem_value4._TintColor']='9999.28906,9794.01562,9999.75781,1'
        effect['eiem_float._UseBlend']='0';effect['eiem_float._UseMask']='0'
        effect['eiem_float._UseDisturb']='0';effect['eiem_float._EnableNormalMap']='0'
        effect['eiem_float._UseEmission']='0'
        for slot, fields in editor.features.VFX_UV.items():
            effect['eiem_float.'+fields[0]]='0'
            effect['eiem_value4.'+editor.features.VFX_UV_WEIGHTS[slot]]='1,0,0,0'
            effect['eiem_value4.'+fields[1]]='0,0,0,0'
            effect['eiem_float.'+fields[2]]='0'
            effect['eiem_value4.'+fields[3]]='1,0,0,1'
        disabled_slots=('_BlendTex','_MaskTex','_DisturbTex1','_NormalMap','_EmissionTex')
        for slot in ('_MainTex',*disabled_slots,'_UnknownSourceTex'):
            effect['eiem_texture.'+slot]=slot+'.png'
            effect['eiem_texture_scale.'+slot]='1,1'
            effect['eiem_texture_offset.'+slot]='0.25,0.5'
        editor._prepare_material(effect)
        editor._row(effect,'eiem_float._MainTexUVRotate').scalar=90
        assert abs(float(effect['eiem_value4._MainTexUVRotateMat'].split(',')[1])-1)<1e-6
        editor._row(effect,'eiem_float._BlendUVSet').choice='UV1'
        assert effect['eiem_value4._BlendTexUVWeights']=='0,1,0,0'
        fields=[];editor.draw(Layout(fields),effect)
        assert ('eiem_float._MainTexUVRotate','scalar') not in fields, 'static angle should stay in secondary parameters'
        assert ('eiem_float._MainUVSet','choice') in fields
        assert ('','eiem_editor_vfx_surface') in fields, 'VFX surface mixing selector is missing'
        assert editor.features.surface_mode(effect)==1, 'source Alpha blend should be recognized'
        animation=package.eiem_blender_addon.material_animation
        assert not hasattr(animation, 'add_uv_spin'), 'PolarUV must not generate a material animation clip'
        for group in effect.eiem_editor_groups:group.expanded=True
        editor._row(effect,'eiem_float._UseBlend').boolean=True
        editor._row(effect,'eiem_float._MainUVSet').choice='POLARUV'
        assert effect['eiem_float._MainUVSet']=='2', 'PolarUV must use the native UV mode enum'
        assert effect['eiem_value4._MainTexUVWeights']=='0,0,1,0', 'PolarUV must update the shader UV weights'
        editor._row(effect,'eiem_float._BlendUVSet').choice='SCREENUV'
        assert effect['eiem_float._BlendUVSet']=='3'
        assert effect['eiem_value4._BlendTexUVWeights']=='0,0,0,1'
        editor._row(effect,'eiem_value4._MainTexUVSpeed').uv_speed=(0,-0.05)
        editor._row(effect,'eiem_value4._BlendTexUVSpeed').uv_speed=(0,0.12)
        main_speed=tuple(float(v) for v in effect['eiem_value4._MainTexUVSpeed'].split(','))
        blend_speed=tuple(float(v) for v in effect['eiem_value4._BlendTexUVSpeed'].split(','))
        assert abs(main_speed[0])<1e-6 and abs(main_speed[1]+0.05)<1e-6
        assert abs(blend_speed[0])<1e-6 and abs(blend_speed[1]-0.12)<1e-6
        fields=[];editor.draw(Layout(fields),effect)
        assert ('eiem_float._UsePolarUV','boolean') not in fields
        assert ('eiem_float._MainUVSet','choice') in fields
        assert ('eiem_value4._MainTexUVSpeed','uv_speed') in fields
        assert ('eiem_value4._BlendTexUVSpeed','uv_speed') in fields
        assert ('eiem_float._MainTexUVRotate','scalar') not in fields
        assert ('','eiem_editor_vfx_surface') in fields, 'VFX surface mixing selector is missing'
        assert editor.features.surface_mode(effect)==1, 'source Alpha blend should be recognized'
        effect.eiem_editor_groups['main'].details_expanded=True
        fields=[];editor.draw(Layout(fields),effect)
        assert ('eiem_float._MainTexUVRotate','scalar') in fields, 'static angle must remain available in secondary parameters'
        editor._row(effect,'eiem_float._UseBlend').boolean=False
        texture_fields=[]
        editor._draw_texture(Layout(texture_fields),effect,'_BlendTex')
        assert ('eiem_texture._BlendTex','text') in texture_fields
        assert not any(name in ('uv_expanded','uv_spin_enabled','vector2') for _,name in texture_fields), 'texture row must contain only its path and picker'
        assert not effect.eiem_material_animations, 'editing native UV controls must not create a material animation'
        # Structure cache must follow external key/profile changes and RNA repair.
        keys,_=editor._schema(effect)
        effect['eiem_float._NewRuntimeParameter']='2'
        assert 'eiem_float._NewRuntimeParameter' in editor._schema(effect)[0]
        del effect['eiem_float._NewRuntimeParameter']
        assert tuple(keys)==tuple(editor._schema(effect)[0])
        editor._row(effect,'eiem_float._MainUVSet').key='broken'
        assert not editor._controls_ready(effect)
        editor._prepare_material(effect)
        assert editor._controls_ready(effect)
        before={key:str(value) for key,value in effect.items() if not key.startswith('eiem_editor_')}
        fields=[];editor.draw(Layout(fields),effect)
        for slot in disabled_slots:
            assert ('eiem_texture.'+slot,'text') not in fields, 'disabled feature exposed '+slot
            assert ('eiem_texture_scale.'+slot,'vector2') not in fields, 'disabled feature exposed UV controls for '+slot
        for slot in ('_MainTex','_UnknownSourceTex'):
            assert ('eiem_texture.'+slot,'text') in fields, 'unconditional or unrecognised texture was hidden: '+slot
        assert before=={key:str(value) for key,value in effect.items() if not key.startswith('eiem_editor_')}, 'visibility changed native material data'
        effect.eiem_editor_advanced=True
        for group in effect.eiem_editor_groups:group.expanded=True
        fields=[];editor.draw(Layout(fields),effect)
        for slot in disabled_slots:
            assert ('eiem_texture.'+slot,'text') in fields, 'raw view lost disabled texture '+slot
        effect.eiem_editor_advanced=False
        tint=editor._row(effect,'eiem_value4._TintColor')
        assert all(abs(actual-1.0)<1e-6 for actual in tint.color), 'normal TintColor widget should display old 10000-like data as white'
        assert effect['eiem_value4._TintColor']=='9999.28906,9794.01562,9999.75781,1', 'opening TintColor must not overwrite stored data'
        tint.color=(1,1,1,1)
        assert effect['eiem_value4._TintColor']=='1,1,1,1', 'explicit normal TintColor edit must write normalized white'
        effect.eiem_editor_features['blend'].enabled=True
        effect.eiem_editor_features['mask'].enabled=True
        effect.eiem_editor_features['disturb'].enabled=True
        effect.eiem_editor_features['normal'].enabled=True
        effect.eiem_editor_features['emission'].enabled=True
        effect.eiem_editor_groups['blend'].expanded=False
        fields=[];editor.draw(Layout(fields),effect)
        for slot in ('_MainTex',*disabled_slots):
            assert ('eiem_texture.'+slot,'text') in fields
            assert effect['eiem_texture.'+slot]==slot+'.png', 're-enabling changed the texture binding'
            assert effect['eiem_texture_scale.'+slot]=='1,1' and effect['eiem_texture_offset.'+slot]=='0.25,0.5'
        effect.eiem_editor_groups['blend'].expanded=True
        # Each texture layer exposes its native UV mode and independent speed.
        _,_,items,ownership=editor._material_structure(effect)
        for item in items:
            slots=[slot for slot in item['slots'] if slot in editor.features.VFX_UV]
            if not slots:
                continue
            effect.eiem_editor_groups[item['key']].expanded=True
            effect.eiem_editor_groups[item['key']].details_expanded=True
            fields=[]
            editor._draw_feature(Layout(fields),effect,item,ownership,items,'vfx')
            for slot in slots:
                uv=editor.features.VFX_UV[slot]
                assert ('eiem_float.'+uv[0],'choice') in fields, 'layer lost UV mode selector: '+slot
                assert ('eiem_value4.'+uv[1],'uv_speed') in fields, 'layer lost independent UV speed: '+slot
                assert ('eiem_float.'+uv[2],'scalar') in fields, 'layer lost static UV angle: '+slot
                assert not any(key=='eiem_value4.'+uv[3] for key,_ in fields), 'derived GPU matrix crowded normal UI'
                assert ('eiem_texture_scale.'+slot,'vector2') in fields, 'layer lost texture tiling: '+slot
                assert ('eiem_texture_offset.'+slot,'vector2') in fields, 'layer lost texture offset: '+slot
            if item['key']=='main':
                assert ('eiem_float._UsePolarUV','boolean') not in fields, 'PolarUV must be selected per texture layer'
        labels=[];editor.draw(Layout([],labels),effect)
        for label in ('Shader 主色','Shader 中标记 HDR / Gamma','Mask 只影响 Alpha'):
            assert any(label in text for text,_ in labels), 'missing shader-backed guidance: '+label+' in '+repr(labels)
        assert effect['eiem_keyword._USE_BLEND']=='1' and effect['eiem_keyword._USE_MASK']=='1'
        assert 'eiem_keyword._CHARACTER_VFX_SPECIAL' not in effect
    finally:
        bpy.data.materials.remove(effect)
    # Live upgrade can move the RNA definition to the end without clearing
    # saved IDProperties, row identities, UV folds, or native values.
    rows_before=[(r.name,r.key) for r in mat.eiem_editor_values]
    groups_before=[(g.name,g.expanded,g.details_expanded) for g in mat.eiem_editor_groups]
    native_before=native()
    del bpy.types.Material.eiem_editor_values
    bpy.types.Material.eiem_editor_values=bpy.props.CollectionProperty(type=editor.EFF_PG_material_value)
    assert rows_before==[(r.name,r.key) for r in mat.eiem_editor_values]
    assert groups_before==[(g.name,g.expanded,g.details_expanded) for g in mat.eiem_editor_groups]
    assert native_before==native(), 'RNA rebind changed native material values'
finally:
    bpy.data.materials.remove(queue_mat)
    bpy.data.materials.remove(mat)
    package.unregister()
print('PASS material editor: read-only draw, native PolarUV controls, per-texture UV modes/speeds, texture-only rows, raw view, left labels, typed values, HDR, portable payload')
