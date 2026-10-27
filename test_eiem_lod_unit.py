import unittest

import eiem_lod


class Mesh:
    type = "MESH"

    def __init__(self, level):
        self.name = "BodyLOD%d" % level
        self.data = {
            "eiem_section": "MeshBodyLOD%d" % level,
            "eiem_asset": "BodyLOD%d" % level,
            "eiem_source": "assets/body_lod%d.fbx" % level,
            "eiem_target_asset": "BodyLOD%d" % level,
            "eiem_target_path": "assets/body_lod%d.fbx" % level,
        }
        self._values = {
            "eiem_render_asset": "BodyLOD%d" % level,
            "eiem_render_section": "RenderBodyLOD%d" % level,
            "eiem_author_package": "authoring/body",
        }

    def get(self, key, default=None):
        return self._values.get(key, default)

    def __getitem__(self, key):
        return self._values[key]

    def __contains__(self, key):
        return key in self._values


class LodTests(unittest.TestCase):
    def test_custom_mesh_keeps_its_resource_identity_when_attached_to_renderer_lod(self):
        render_lods = [Mesh(level) for level in range(4)]
        for level, render in enumerate(render_lods):
            render._values['eiem_render_asset'] = 'S_wpn_sword_0021_01_lod%d' % level
            render._values['eiem_render_section'] = 'RenderS_wpn_sword_0021_01_lod%d' % level
        custom = Mesh(5)
        custom.name = 'MeshS_wpn_sword_0021_01_lod005_13'
        custom._values['eiem_render_asset'] = 'S_wpn_sword_0021_01_lod3'
        custom._values['eiem_render_section'] = 'RenderS_wpn_sword_0021_01_lod3'
        custom.data['eiem_section'] = custom.name
        custom.data['eiem_source'] = 'assets/effects/s_fx_endminf_wep.fbx'
        custom.data['eiem_target_path'] = 'assets/effects/s_fx_endminf_wep.fbx'
        custom.data['eiem_target_asset'] = 'S_wpn_sword_0021_01_lod005'
        self.assertEqual(eiem_lod.mesh_lod_level(custom), 3)
        candidates = render_lods + [custom]
        self.assertEqual(eiem_lod.discover_mesh_lods(custom, candidates), {3})

        plan = dict(objects=[custom], groups=[], bindings={}, hidden=set())
        expanded = eiem_lod.expand_lod_plan(
            plan, [0, 1, 2, 3], candidates,
            lambda obj: (obj.data['eiem_target_path'], obj.data['eiem_target_asset']))

        self.assertEqual(expanded['objects'], [custom])
        self.assertEqual(custom.data['eiem_target_asset'], 'S_wpn_sword_0021_01_lod005')
        self.assertEqual(custom.data['eiem_target_path'],
                         'assets/effects/s_fx_endminf_wep.fbx')

    def test_render_section_wins_when_render_asset_names_an_independent_mesh_lod(self):
        custom = Mesh(5)
        custom.name = 'MeshS_wpn_sword_0021_01_lod005_13'
        custom._values['eiem_render_asset'] = 'S_wpn_sword_0021_01_lod005'
        custom._values['eiem_render_section'] = 'RenderS_wpn_sword_0021_01_lod3_14'
        custom.data['eiem_section'] = custom.name
        custom.data['eiem_source'] = 'assets/effects/s_fx_endminf_wep.fbx'
        custom.data['eiem_target_path'] = 'assets/effects/s_fx_endminf_wep.fbx'
        custom.data['eiem_target_asset'] = 'S_wpn_sword_0021_01_lod005'

        self.assertEqual(eiem_lod.mesh_lod_level(custom), 3)
        self.assertEqual(eiem_lod.discover_mesh_lods(custom, [custom]), {3})

    def test_export_rule_uses_render_lod_and_preserves_mesh_resource_identity(self):
        custom = Mesh(5)
        custom.name = 'MeshS_wpn_sword_0021_01_lod005_13'
        custom._values['eiem_render_asset'] = 'S_wpn_sword_0021_01_lod005'
        custom._values['eiem_render_section'] = 'RenderS_wpn_sword_0021_01_lod3_14'
        custom.data['eiem_section'] = custom.name
        custom.data['eiem_source'] = 'assets/effects/s_fx_endminf_wep.fbx'
        custom.data['eiem_target_path'] = 'assets/effects/s_fx_endminf_wep.fbx'
        custom.data['eiem_target_asset'] = 'S_wpn_sword_0021_01_lod005'

        self.assertEqual(eiem_lod.mesh_export_target(custom),
                         ('assets/effects/s_fx_endminf_wep.fbx',
                          'S_wpn_sword_0021_01_lod005', 3))

    def test_selected_lod0_prefers_same_mesh_source_over_independent_lod3_mesh(self):
        lod0 = Mesh(0)
        lod0._values['eiem_render_asset'] = 'S_wpn_sword_0021_01_lod0'
        lod0._values['eiem_render_section'] = 'RenderS_wpn_sword_0021_01_lod0'
        lod0.data['eiem_section'] = 'MeshS_wpn_sword_0021_01_lod0_0'
        lod0.data['eiem_asset'] = 'S_wpn_sword_0021_01_lod0'
        lod0.data['eiem_target_asset'] = 'S_wpn_sword_0021_01_lod0'
        lod0.data['eiem_source'] = 'assets/weapons/sword.fbx'
        lod0.data['eiem_target_path'] = 'assets/weapons/sword.fbx'

        lod3 = Mesh(3)
        lod3._values['eiem_render_asset'] = 'S_wpn_sword_0021_01_lod3'
        lod3._values['eiem_render_section'] = 'RenderS_wpn_sword_0021_01_lod3_9'
        lod3.data['eiem_section'] = 'MeshS_wpn_sword_0021_01_lod3_9'
        lod3.data['eiem_asset'] = 'S_wpn_sword_0021_01_lod3'
        lod3.data['eiem_target_asset'] = 'S_wpn_sword_0021_01_lod3'
        lod3.data['eiem_source'] = 'assets/weapons/sword.fbx'
        lod3.data['eiem_target_path'] = 'assets/weapons/sword.fbx'

        custom = Mesh(5)
        custom.name = 'MeshS_wpn_sword_0021_01_lod005_13'
        custom._values['eiem_render_asset'] = 'S_wpn_sword_0021_01_lod005'
        custom._values['eiem_render_section'] = 'RenderS_wpn_sword_0021_01_lod3_14'
        custom.data['eiem_section'] = custom.name
        custom.data['eiem_asset'] = 'S_wpn_sword_0021_01_lod005'
        custom.data['eiem_target_asset'] = 'S_wpn_sword_0021_01_lod005'
        custom.data['eiem_source'] = 'assets/effects/s_fx_endminf_wep.fbx'
        custom.data['eiem_target_path'] = 'assets/effects/s_fx_endminf_wep.fbx'

        plan = dict(objects=[lod0], groups=[], bindings={}, hidden=set())
        expanded = eiem_lod.expand_lod_plan(
            plan, [0, 3], [custom, lod0, lod3],
            lambda obj: (obj.data['eiem_target_path'], obj.data['eiem_target_asset']))
        lod3 = next(obj for obj in expanded['objects']
                    if eiem_lod.mesh_lod_level(obj) == 3)

        self.assertIs(eiem_lod.mesh_export_template(lod3), lod0)
        self.assertEqual(eiem_lod.mesh_export_target(lod3),
                         ('assets/weapons/sword.fbx',
                          'S_wpn_sword_0021_01_lod3', 3))

    def test_package_lods_use_selected_lod0_template(self):
        lod0 = Mesh(0)
        lod0._values['eiem_render_asset'] = 'S_actor_body_lod0'
        rows = [dict(type='Mesh', logicalPath='assets/body_lod0.asset', name='S_actor_body_lod0'),
                dict(type='Mesh', logicalPath='assets/body.fbx', name='S_actor_body_lod1'),
                dict(type='Material', logicalPath='assets/body.mat', name='S_actor_body_lod2')]
        candidates = [lod0] + eiem_lod.source_mesh_candidates('authoring/body', rows)
        self.assertEqual(eiem_lod.discover_mesh_lods(lod0, candidates), {0, 1})
        plan = dict(objects=[lod0], groups=[], bindings={lod0: ('$style', [0])}, hidden=set())
        expanded = eiem_lod.expand_lod_plan(plan, [0, 1, 2], candidates,
                                          lambda o: (o.data['eiem_target_path'], o.data['eiem_target_asset']))
        views = sorted(expanded['objects'], key=eiem_lod.mesh_lod_level)
        self.assertEqual(len(views), 2)
        self.assertIs(eiem_lod.mesh_export_template(views[1]), lod0)
        self.assertEqual(views[1].data['eiem_target_path'], 'assets/body.fbx')
        self.assertEqual(expanded['bindings'][views[1]], ('$style', [0]))

    def test_discovery_and_template_expansion(self):
        lod0, lod1 = Mesh(0), Mesh(1)
        candidates = [lod0, lod1]
        self.assertEqual(eiem_lod.discover_mesh_lods(lod0, candidates), {0, 1})

        plan = {
            "objects": [lod0],
            "groups": [],
            "bindings": {lod0: ("$style", [0])},
            "hidden": set(),
        }
        expanded = eiem_lod.expand_lod_plan(
            plan, [0, 1], candidates,
            lambda obj: ("source", obj.get("eiem_render_asset", "")),
        )
        self.assertEqual([eiem_lod.mesh_lod_level(obj)
                          for obj in expanded["objects"]], [0, 1])
        self.assertEqual(expanded["bindings"][expanded["objects"][1]],
                         ("$style", [0]))
        self.assertIs(eiem_lod.mesh_export_template(expanded["objects"][1]),
                      lod0)

    def test_missing_lod_is_not_invented(self):
        lod0 = Mesh(0)
        plan = {"objects": [lod0], "groups": [], "bindings": {}, "hidden": set()}
        with self.assertRaisesRegex(ValueError, "不存在"):
            eiem_lod.expand_lod_plan(
                plan, [4], [lod0], lambda obj: ("source", obj.name))

    def test_duplicate_object_order_is_shared_across_lods(self):
        original, duplicate, target = Mesh(0), Mesh(0), Mesh(1)
        original.name, duplicate.name = 'Sleeve', 'Sleeve.001'
        plan = dict(objects=[original, duplicate], groups=[], bindings={}, hidden=set())
        expanded = eiem_lod.expand_lod_plan(plan, [0, 1], [original, target],
                                          lambda o: ('source', o.get('eiem_render_asset')))
        self.assertEqual([[eiem_lod.mesh_export_template(o).name for o in group]
                          for group in expanded['sources']],
                         [['Sleeve', 'Sleeve.001'], ['Sleeve', 'Sleeve.001']])


if __name__ == "__main__":
    unittest.main()
