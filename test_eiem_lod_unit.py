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
