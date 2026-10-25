import unittest
from types import SimpleNamespace
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from Blender import eiem_blender_addon as addon


class Mesh:
    def __init__(self, name, section='', visible=True, hidden=False, hide_render=False):
        self.name = name
        self.type = 'MESH'
        self.data = {'eiem_section': section}
        self.visible = visible
        self.hide_viewport = False
        self.hidden = hidden
        self.hide_render = hide_render

    def visible_get(self, view_layer=None):
        return self.visible

    def hide_get(self, view_layer=None):
        return self.hidden


class ExportScopeTests(unittest.TestCase):
    def setUp(self):
        self.view_layer = object()
        self.visible_selected = Mesh('selected', 'MeshA')
        self.visible_other = Mesh('unselected', 'MeshB')
        self.hidden = Mesh('hidden', 'MeshC', visible=False, hidden=True)
        self.render_hidden = Mesh('render_hidden', 'MeshD', hide_render=True)
        self.unrelated = Mesh('unrelated')
        self.context = SimpleNamespace(
            scene=SimpleNamespace(objects=[self.visible_selected, self.visible_other,
                                           self.hidden, self.render_hidden,
                                           self.unrelated]),
            view_layer=self.view_layer)

    def test_full_scope_uses_selected_meshes_only(self):
        meshes = addon.export_meshes_for_scope(
            self.context, [self.visible_selected], 'ALL')
        self.assertEqual(meshes, [self.visible_selected])

    def test_partial_and_effect_physics_only_scopes_keep_selection(self):
        selected = [self.visible_selected]
        self.assertEqual(addon.export_meshes_for_scope(
            self.context, selected, 'MATERIALS'), selected)
        self.assertEqual(addon.export_meshes_for_scope(
            self.context, [], 'ALL', effect_only=True), [])
        self.assertEqual(addon.export_meshes_for_scope(
            self.context, selected, 'ALL', physics_only=True), selected)


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromTestCase(ExportScopeTests))
    if not result.wasSuccessful():
        raise AssertionError('EFF export scope regression test failed')
