"""Run in factory-startup Blender. Check RNA values and read-only panel drawing."""
import importlib.util
from pathlib import Path
import sys
import bpy

root=Path(__file__).parent
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
    before = (set(mat.keys()), len(mat.eiem_editor_values), len(mat.eiem_editor_groups))
    editor.draw(Layout([]), mat)
    after = (set(mat.keys()), len(mat.eiem_editor_values), len(mat.eiem_editor_groups))
    assert after == before, 'panel draw mutated Material data; Blender forbids these writes'
    editor._prepare_pending()
    fields=[];editor.draw(Layout(fields),mat)
    assert ('eiem_float._UseEmission','boolean') in fields
    assert ('eiem_float._EmissionBrightness','scalar') not in fields
    assert ('eiem_texture._EmissionMap','text') in fields, 'source emission texture hidden while emission is off'
    assert ('eiem_texture_scale._HighlightMap','vector2') not in fields, 'unused empty transform was exposed'
    assert '_HighlightMap' in editor.texture_slots(mat)
    row=editor._row(mat,'eiem_float._UseEmission');row.boolean=True
    assert mat['eiem_float._UseEmission']=='1'
    fields=[];editor.draw(Layout(fields),mat)
    assert ('eiem_float._EmissionBrightness','scalar') in fields
    color=editor._row(mat,'eiem_value4._EmissionColor');assert color.color[0]==4, 'HDR color was clamped'
    color.color=(3,0.4,0.2,1);assert float(mat['eiem_value4._EmissionColor'].split(',')[0])==3
    queue=editor._row(mat,'eiem_render_queue');queue.integer=2999;assert mat['eiem_render_queue']=='2999'
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
    assert ('eiem_texture._ClearCoatMask','text') in fields, 'bound paths should stay at the top even when a feature is off'
    assert ('eiem_float._ClearCoatSmoothness','scalar') not in fields
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
    effect=bpy.data.materials.new('Dedicated VFX Editor Test')
    try:
        effect['eiem_shader']='HGRP/CharacterNPR_VFX'
        effect['eiem_float._UseBlend']='0';effect['eiem_float._UseMask']='0'
        for slot in ('_MainTex','_BlendTex','_MaskTex'):
            effect['eiem_texture_scale.'+slot]='1,1'
        editor._prepare_material(effect)
        effect.eiem_editor_features['blend'].enabled=True
        effect.eiem_editor_features['mask'].enabled=True
        effect.eiem_editor_groups['blend'].expanded=False
        fields=[];editor.draw(Layout(fields),effect)
        for slot in ('_MainTex','_BlendTex','_MaskTex'):
            assert ('eiem_texture.'+slot,'text') in fields
        assert effect['eiem_keyword._USE_BLEND']=='1' and effect['eiem_keyword._USE_MASK']=='1'
        assert 'eiem_keyword._CHARACTER_VFX_SPECIAL' not in effect
    finally:
        bpy.data.materials.remove(effect)
finally:
    bpy.data.materials.remove(mat)
    package.unregister()
print('PASS material editor: read-only draw, deferred schema initialisation, reopen all groups, left labels, typed values, HDR, portable payload')
