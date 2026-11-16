import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import eiem_effect_material_origin as origin


class MaterialOriginTests(unittest.TestCase):
    def clone(self, external=False):
        shader = dict(m_FileID=1, m_PathID=42)
        native = dict(m_Name='Original', m_Shader=shader, parameter=1)
        changed = dict(m_Name='Renamed star', m_Shader=shader, parameter=3)
        refs = [dict(gameLogical=dict(previewSource='exact-donor'))] if external else []
        objects = {'template': dict(classId=21, name='Original', sourcePrefab='assets/star.prefab',
            logicalPaths=[dict(path='assets/star.mat', name='Original')]),
            'clone': dict(classId=21, name='Renamed star', template='template', references=refs)}
        asset = dict(identity='clone', template='template')
        return SimpleNamespace(objects=objects, state=dict(author_assets=[asset],
            original={'template': native, 'clone': copy.deepcopy(changed)},
            values={'template': copy.deepcopy(native), 'clone': changed}))

    def test_renamed_native_clone_keeps_template_baseline_and_user_changes(self):
        edit = self.clone()
        before = copy.deepcopy(edit.state['values'])
        with patch.object(origin.resources, 'references', return_value=[]):
            self.assertTrue(origin.migrate_origins(edit))
            record = origin.material_for_origin(edit, 'clone')
        self.assertEqual(record['source'], 'assets/star.mat')
        self.assertEqual(record['asset'], 'Original')
        self.assertEqual(record['value']['parameter'], 1)
        self.assertEqual(edit.state['values'], before)
        self.assertEqual(record['sourcePrefab'], 'assets/star.prefab')

    def test_external_donor_must_not_be_replaced_by_native_schema_template(self):
        with self.assertRaises(ValueError):
            origin.material_for_origin(self.clone(external=True), 'clone')

    def test_different_shader_cannot_use_local_template_as_provenance(self):
        edit = self.clone()
        edit.state['original']['clone']['m_Shader'] = dict(m_FileID=1, m_PathID=99)
        with self.assertRaises(ValueError):
            origin.material_for_origin(edit, 'clone')

    def test_existing_material_keeps_exact_container_for_runtime_lookup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "source").mkdir()
            prefab = "assets/effects/star.prefab"
            identity = "cab:1"
            manifest = dict(format="EFFSOURCE", prefab=prefab, root=identity,
                vfsFingerprint="fixture", hierarchy=[dict(identity=identity, name="star")],
                containerEntries=[dict(logicalPath=prefab, asset=dict(identity=identity))])
            (root / "source/manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            (root / "effect.json").write_text(json.dumps(dict(objects=[dict(classId=21,
                logicalPaths=[dict(path="assets/effects/star.mat", name="Star")])])), encoding="utf-8")
            record = dict(source="assets/effects/star.mat", asset="Star", value={}, bindings=[])
            asset = dict(identity="author:1", sourceMaterial=copy.deepcopy(record))
            donor = dict(classId=21, name="Star", logicalPaths=[dict(path=record["source"], name="Star")],
                         gameSource=directory)
            edit = SimpleNamespace(objects={"author:1":dict(classId=21), "donor:1":donor},
                state=dict(author_assets=[asset], source=directory))
            self.assertTrue(origin.migrate_origins(edit))
            self.assertEqual(asset["sourceMaterial"]["sourcePrefab"], prefab)
            self.assertFalse(origin.migrate_origins(edit))


if __name__ == "__main__":
    unittest.main()
