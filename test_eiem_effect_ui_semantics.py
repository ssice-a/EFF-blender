import unittest

import eiem_effect_ui as ui


class EffectUISemanticsTests(unittest.TestCase):
    def test_native_storage_type_wins_over_zero_one_values(self):
        self.assertEqual(ui.field_semantic(['strength'], 'float'), 'float')
        self.assertEqual(ui.field_semantic(['enabled'], 'float'), 'float')
        self.assertEqual(ui.field_semantic(['enabled'], 'bool'), 'boolean')
        self.assertEqual(ui.field_semantic(['m_Enabled'], 'UInt8', class_id=114), 'boolean')

    def test_verified_particle_enums_keep_native_ordinals(self):
        self.assertEqual([item[3] for item in ui.enum_items(['ExternalForcesModule', 'influenceFilter'])],
                         [0, 1, 2])
        self.assertEqual([item[1] for item in ui.enum_items(['m_MaskInteraction'])],
                         ['不与 Sprite Mask 交互', '仅显示在 Mask 内', '仅显示在 Mask 外'])
        self.assertEqual(ui.field_semantic(['m_MaskInteraction'], 'int'), 'enum')

    def test_verified_flags_have_named_bits_without_dropping_unknown_bits(self):
        self.assertEqual(ui.bitmask_spec(['axisBitMask'])['bits'], ((0, 'X'), (1, 'Y'), (2, 'Z')))
        self.assertEqual(ui.bitmask_spec(['UVModule', 'uvChannelMask'])['bits'],
                         ((0, 'UV0'), (1, 'UV1'), (2, 'UV2'), (3, 'UV3')))
        self.assertEqual(len(ui.bitmask_spec(['CollisionModule', 'collidesWith', 'm_Bits'])['bits']), 32)
        self.assertEqual(ui.field_semantic(['m_LightModeMask'], 'unsigned int'), 'bitmask')
        self.assertEqual(ui.toggle_bit(0xFFFFFFFF, 31), 0x7FFFFFFF)
        self.assertEqual(ui.toggle_bit(-1, 2), -5)
        self.assertEqual(ui.field_semantic(['axisBitMask'], 'float'), 'bitmask')
        self.assertEqual(ui.field_semantic(['axisBitMask'], 'string'), 'native')
        self.assertEqual(ui.bitmask_display_value(7.0, 'float'), 7)
        self.assertEqual(ui.bitmask_display_value('4294967295', 'unsigned int'), 0xFFFFFFFF)
        self.assertEqual(ui.bitmask_display_value('-1', 'int'), 0xFFFFFFFF)
        self.assertIsNone(ui.bitmask_display_value('01', 'unsigned int'))
        self.assertIsNone(ui.bitmask_display_value(7.25, 'float'))
        self.assertIsNone(ui.bitmask_display_value(float(1 << 25), 'float'))
        self.assertEqual(ui.bitmask_spec(['SubModule', 'subEmitters', 0, 'properties', 'm_Bits'])['bits'],
                         ((0, '颜色'), (1, '大小'), (2, '旋转'), (3, '寿命'), (4, '系统持续时间')))

    def test_mask_toggle_preserves_exact_native_numeric_type(self):
        self.assertEqual(ui.toggle_native_bit(3, 1, 'int'), 1)
        self.assertEqual(ui.toggle_native_bit(3.0, 1, 'float'), 1.0)
        with self.assertRaises(ValueError):
            ui.toggle_native_bit(1.5, 0, 'float')
        with self.assertRaises(ValueError):
            ui.toggle_native_bit(float(1 << 25), 0, 'float')


if __name__ == '__main__':
    unittest.main()
