"""Author compiler contracts: exact Mesh scope and new topology isolation."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.nativepack import physics
from tools.nativepack.type_tree import Schema, from_data, decode, encode
from tools.nativepack.resource_file import encode_resource, decode_resource


def schema(kind, name, children=()):
    return Schema(kind, name, False, list(children))


def array(name, element):
    return schema('vector', name, [schema('Array', 'Array', [schema('int', 'size'), element])])


def ref(name):
    return schema('PPtr<Object>', name, [schema('int', 'm_FileID'), schema('SInt64', 'm_PathID')])


class PhysicsCompilerTests(unittest.TestCase):
    def test_existing_resource_layout_keeps_targets_and_topology(self):
        writer = physics.Writer()
        writer.text(physics.STRUCTURE); writer.text('rig-id')
        writer.n(1); writer.text('root'); writer.text(''); writer.n(1); writer.n(0)
        writer.numbers((0, 0, 0, 0, 0, 0, 1, 1, 1, 1))
        writer.n(0); writer.n(1)
        writer.text('collider-id'); writer.text('sphere'); writer.text('root'); writer.text('CAB-1')
        writer.n(456); writer.numbers((0, 0, 0, 0, 0, 0, 1))
        writer.n(1); writer.text('group-id'); writer.text('tail'); writer.text('CAB-1')
        writer.n(123); writer.n(1); writer.text('assets/coat'); writer.text('Coat_LOD0')
        writer.n(2); writer.text('root/fixed'); writer.n(0)
        writer.text('root/fixed/tail'); writer.n(1)
        writer.n(1); writer.text('collider-id')
        layout = physics.read_resource_layout(bytes(writer.data))
        self.assertEqual(layout['identity'], 'rig-id')
        self.assertEqual(layout['skeleton'], [('root', '', True, False, (0, 0, 0, 0, 0, 0, 1, 1, 1, 1))])
        self.assertEqual(layout['groups']['group-id']['targets'], [('assets/coat', 'Coat_LOD0')])
        self.assertEqual(layout['groups']['group-id']['nodes'], [('root/fixed', False), ('root/fixed/tail', True)])
        with self.assertRaisesRegex(ValueError, 'unreferenced'):
            physics.read_resource_layout(bytes(writer.data) + b'\0')

    def test_mesh_scope_tracks_weighted_parts_and_all_declared_lods(self):
        group = {'name': 'tail', 'nodes': [{'bone': 'root/fixed', 'role': 'FIXED'},
                                         {'bone': 'root/fixed/tail', 'role': 'MOVE'}]}
        used = {10: {'root/fixed/tail'}, 20: {'root/fixed'}, 30: {'other'}}
        parts = {10: ['tail-blue', 'tail-red'], 20: ['body'], 30: ['other']}
        rules = [{'parts': ['tail-blue', 'tail-red'], 'targets': [
                    {'sourcePath': 'assets/tail0', 'sourceAsset': 'Tail0'},
                    {'sourcePath': 'assets/tail1', 'sourceAsset': 'Tail1'}]},
                 {'parts': ['body'], 'targets': [{'sourcePath': 'assets/body', 'sourceAsset': 'Body'}]},
                 {'parts': ['other'], 'targets': [{'sourcePath': 'assets/other', 'sourceAsset': 'Other'}]}]
        self.assertEqual(physics.group_mesh_targets(group, used, rules, parts),
                         [('assets/tail0', 'Tail0'), ('assets/tail1', 'Tail1')])
        with self.assertRaisesRegex(ValueError, 'no selected weighted Mesh'):
            physics.group_mesh_targets(group, {20: used[20]}, rules, parts)

    def test_new_cloth_does_not_inherit_donor_topology_or_references(self):
        ptr = {'m_FileID': 3, 'm_PathID': 999}
        curve = schema('Curve', 'damping', [array('m_Curve', schema('Key', 'data', [schema('float', 'value')]))])
        data = schema('Cloth', 'serializeData', [schema('int', 'clothType'), schema('int', 'connectionMode'),
                array('sourceRenderers', ref('data')), array('paintMaps', ref('data')),
                array('rootBones', ref('data')), array('ignoreFromRootBones', ref('data')),
                schema('Collision', 'colliderCollisionConstraint', [array('colliderList', ref('data'))]), curve])
        built = schema('Data2', 'serializeData2', [schema('Prebuild', 'preBuildData', [
                schema('bool', 'enabled'), array('transformArray', ref('data'))])])
        template_schema = schema('MonoBehaviour', 'Base', [ref('m_GameObject'), schema('bool', 'm_Enabled'),
                ref('m_Script'), schema('string', 'm_Name'), data, built])
        original = from_data(template_schema, dict(m_GameObject=ptr, m_Enabled=1, m_Script=ptr, m_Name='donor',
                serializeData=dict(clothType=1, connectionMode=4, sourceRenderers=[ptr], paintMaps=[ptr],
                    rootBones=[ptr], ignoreFromRootBones=[ptr], colliderCollisionConstraint={'colliderList': [ptr]},
                    damping={'m_Curve': [{'value': 5.0}]}),
                serializeData2={'preBuildData': {'enabled': 1, 'transformArray': [ptr]}}))
        saved = encode(original)
        result = physics.cloth_template(original, {'name': 'new-tail', 'nativeParameters': [
                  {'path': 'serializeData.damping.m_Curve.0.value', 'floating': True, 'value': .2},
                  {'path': 'serializeData.damping.m_Curve.1.value', 'floating': True, 'value': .8}]})
        self.assertEqual(encode(original), saved)
        self.assertEqual(result['m_Script'].plain(), ptr)
        self.assertEqual(result['m_GameObject'].plain(), {'m_FileID': 0, 'm_PathID': 0})
        self.assertEqual(result['serializeData']['rootBones'].items(), [])
        self.assertEqual(result['serializeData2']['preBuildData']['transformArray'].items(), [])
        self.assertEqual(result['serializeData2']['preBuildData']['enabled'].data, 0)
        self.assertEqual([key['value'].data for key in result['serializeData']['damping']['m_Curve'].items()], [.2, .8])
        self.assertEqual(encode(decode(template_schema, encode(result))), encode(result))

    def test_generic_resource_roundtrip_requires_nonzero_kind(self):
        data = encode_resource(physics.KIND, '00'*16, {'@physics': b'author input'})
        self.assertEqual(decode_resource(data, 0)['kind'], physics.KIND)
        damaged = bytearray(data); damaged[-1] ^= 1
        with self.assertRaises(ValueError): decode_resource(damaged, 0)
        with self.assertRaises(ValueError): encode_resource(0, '00'*16, {})


if __name__ == '__main__': unittest.main()
