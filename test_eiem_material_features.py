"""Shader-family-aware editor policy; no Blender or game needed."""
import copy
import unittest

import eiem_material_features as ui


def character():
    material = {'eiem_shader': 'HGRP/CharacterNPR', 'eiem_source': 'assets/test.mat'}
    for item in ui.CHARACTER:
        if item['switch']:
            material['eiem_float.' + item['switch']] = '0'
        for slot in item['slots']:
            material['eiem_texture_scale.' + slot] = '1,1'
            material['eiem_texture_offset.' + slot] = '0,0'
    material.update({'eiem_float._SurfaceType': '0', 'eiem_float._ZWrite': '1',
                     'eiem_float._TransparentDepthWrite': '1', 'eiem_float._Cull': '0',
                     'eiem_float._EnableAlphaTest': '0', 'eiem_texture_scale._BaseMap': '1,1'})
    return material


class MaterialFeatures(unittest.TestCase):
    def test_legacy_signature_and_explicit_other_shader(self):
        material = character()
        del material['eiem_shader']
        self.assertEqual(ui.profile(material), 'character')
        before = copy.deepcopy(material)
        ui.features(material)
        ui.visible_slots(material, ui.get_feature(material, 'clearcoat'))
        self.assertEqual(material, before, 'read-only inspection changed a material')
        material['eiem_shader'] = 'Unrelated/Shader'
        self.assertIsNone(ui.profile(material))
        with self.assertRaises(ValueError):
            ui.set_feature(material, 'clearcoat', True)
        self.assertNotIn('eiem_keyword._CLEARCOAT', material)

    def test_switches_write_matching_keywords_and_preserve_settings(self):
        material = character()
        material['eiem_texture._ClearCoatMask'] = 'my-mask.png'
        material['eiem_float._ClearCoatSmoothness'] = '0.94'
        material['eiem_value4._ClearCoatColor'] = '0.2,0.3,1,1'
        for enabled in (True, False, True):
            ui.set_feature(material, 'clearcoat', enabled)
            self.assertEqual(material['eiem_keyword._CLEARCOAT'], str(int(enabled)))
            self.assertEqual(ui.visible_slots(material, ui.get_feature(material, 'clearcoat')),
                             ('_ClearCoatMask',) if enabled else ())
        self.assertEqual(material['eiem_texture._ClearCoatMask'], 'my-mask.png')
        self.assertEqual(material['eiem_float._ClearCoatSmoothness'], '0.94')
        self.assertEqual(material['eiem_value4._ClearCoatColor'], '0.2,0.3,1,1')

    def test_enabled_empty_vfx_slots_are_virtual_and_declared(self):
        material = character()
        ui.set_feature(material, 'vfx', True)
        self.assertEqual(ui.visible_slots(material, ui.get_feature(material, 'vfx')),
                         ('_VFXSpecialMainTex', '_VFXSpecialBlendTex'))
        self.assertFalse(any(key.startswith('eiem_texture.') for key in material))
        del material['eiem_texture_scale._VFXSpecialBlendTex']
        del material['eiem_texture_offset._VFXSpecialBlendTex']
        self.assertEqual(ui.visible_slots(material, ui.get_feature(material, 'vfx')), ('_VFXSpecialMainTex',))

    def test_breath_requires_emission_and_vfx(self):
        material = character()
        material['eiem_float._EmissionAlphaBrightBreathSpeed'] = '1.6'
        ui.set_feature(material, 'breath', True)
        for name in ('_UseEmission', '_EnableCharacterVFX', '_EmissionAlphaBrightBreath'):
            self.assertEqual(ui.field(material, name), '1')
        for name in ('_EMISSION', '_CHARACTER_VFX_SPECIAL'):
            self.assertEqual(ui.field(material, name, 'keyword'), '1')
        ui.set_feature(material, 'vfx', False)
        self.assertEqual(ui.field(material, '_EmissionAlphaBrightBreath'), '0')
        self.assertEqual(ui.field(material, '_UseEmission'), '1')
        self.assertEqual(ui.field(material, '_EmissionAlphaBrightBreathSpeed'), '1.6')

    def test_surface_recipes_preserve_depth_cull_and_texture(self):
        material = character()
        material['eiem_texture._BaseMap'] = 'lace.png'
        material['eiem_float._AlphaClipThreshold'] = '0.37'
        for mode, queue, tag in ((1, '2450', 'Opaque'), (2, '3000', 'Transparent'), (0, '2000', 'Opaque')):
            ui.set_surface(material, mode)
            self.assertEqual(ui.surface_mode(material), mode)
            self.assertEqual(material['eiem_render_queue'], queue)
            self.assertEqual(material['eiem_tag.RenderType'], tag)
            self.assertEqual(ui.field(material, '_ZWrite'), '1')
            self.assertEqual(ui.field(material, '_TransparentDepthWrite'), '1')
            self.assertEqual(ui.field(material, '_Cull'), '0')
            self.assertEqual(ui.field(material, '_AlphaClipThreshold'), '0.37')
            self.assertEqual(material['eiem_texture._BaseMap'], 'lace.png')
        material['eiem_float._ZWrite'] = '0'
        ui.set_surface(material, 2)
        self.assertEqual(ui.field(material, '_ZWrite'), '0', 'explicit author depth choice was lost')
        self.assertEqual(ui.field(material, '_AlphaPremultiply'), '1')
        self.assertEqual(ui.field(material, '_ALPHABLEND_ON', 'keyword'), '1')
        self.assertEqual(ui.field(material, '_ALPHATEST_ON', 'keyword'), '0')

    def test_custom_surface_is_not_normalized_when_read(self):
        material = character()
        material['eiem_float._SurfaceType'] = '1'
        material['eiem_float._BlendMode'] = '1'
        before = dict(material)
        self.assertEqual(ui.surface_mode(material), 3)
        ui.set_surface(material, 3)
        self.assertEqual(material, before)

    def test_dynamic_fields_and_texture_transforms_stay_with_their_feature(self):
        material = character()
        for key, owner in (('eiem_value4._EmissionMapUVSpeed', 'vfx'),
                           ('eiem_float._EmissionAlphaBrightBreathSpeed', 'breath'),
                           ('eiem_float._SpecRampIridescentMode', 'specular'),
                           ('eiem_texture_scale._VFXSpecialMainTex', 'vfx'),
                           ('eiem_texture._ClearCoatMask', 'clearcoat'),
                           ('eiem_float._EnableStylizedFresnel', 'fresnel')):
            self.assertEqual(ui.owner(material, key), owner)

    def test_dedicated_vfx_uses_its_own_keyword_family(self):
        material = {'eiem_shader': 'HGRP/CharacterNPR_VFX', 'eiem_float._UseBlend': '0',
                    'eiem_float._UseMask': '0', 'eiem_texture_scale._BlendTex': '1,1',
                    'eiem_texture_scale._MaskTex': '1,1'}
        ui.set_feature(material, 'blend', True)
        ui.set_feature(material, 'mask', True)
        self.assertEqual(ui.field(material, '_USE_BLEND', 'keyword'), '1')
        self.assertEqual(ui.field(material, '_USE_MASK', 'keyword'), '1')
        self.assertNotIn('eiem_keyword._CHARACTER_VFX_SPECIAL', material)
        with self.assertRaises(ValueError):
            ui.set_surface(material, 1)

    def test_petal_shader_groups_and_switches_use_its_own_fields(self):
        material = {'eiem_shader': 'HGRP/Effect/VFXDitherAlpha',
                    'eiem_float._UseMain2': '0', 'eiem_float._UseDissolve': '0',
                    'eiem_float._EnableNormalMap': '1', 'eiem_float._UseDisturb': '0',
                    'eiem_texture_scale._MainTex2': '1,1'}
        self.assertEqual(ui.owner(material, 'eiem_value4._MainTex2Color'), 'main2')
        self.assertEqual(ui.owner(material, 'eiem_value4._MainTexUVSpeed'), 'main')
        self.assertEqual(ui.owner(material, 'eiem_float._NormalScale'), 'normal')
        for feature, keyword in (('main2', '_USE_MAIN2'), ('dissolve', '_USE_DISSOLVE'),
                                 ('normal', '_NORMAL_MAP'), ('disturb', '_USE_DISTURB')):
            ui.set_feature(material, feature, True)
            self.assertEqual(ui.field(material, keyword, 'keyword'), '1')
            ui.set_feature(material, feature, False)
            self.assertEqual(ui.field(material, keyword, 'keyword'), '0')
        self.assertNotIn('blend', [item['key'] for item in ui.features(material)])
        self.assertNotIn('eiem_keyword._CHARACTER_VFX_SPECIAL', material)


if __name__ == '__main__':
    unittest.main()
