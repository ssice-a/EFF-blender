"""Author archives retain native templates without unrelated script libraries."""
import copy
import json
from pathlib import Path
import struct
import tempfile
import unittest

import eiem_effect_document as document
import eiem_effect_source as source


def native_file(cab, objects, scripts=(), externals=()):
    types = sorted({kind for _, kind, _ in objects})
    metadata = bytearray(b'2021.3.34f5\0' + struct.pack('<iBi', 19, 1, len(types)))
    for kind in types:
        metadata.extend(struct.pack('<iBh', kind, 0, -1))
        metadata.extend(bytes((16 if kind == 114 else 0) + 16))
        metadata.extend(struct.pack('<iii', 0, 0, 0))
    metadata.extend(struct.pack('<i', len(objects)))
    metadata.extend(bytes((-len(metadata)) % 4))
    table = len(metadata)
    metadata.extend(bytes(24 * len(objects)))
    metadata.extend(struct.pack('<i', len(scripts)))
    for fid, pid in scripts:
        metadata.extend(struct.pack('<i', fid))
        metadata.extend(bytes((-len(metadata)) % 4))
        metadata.extend(struct.pack('<q', pid))
    metadata.extend(struct.pack('<i', len(externals)))
    for external in externals:
        metadata.extend(b'\0' + bytes(16) + struct.pack('<i', 0) + external.encode() + b'\0')
    metadata.extend(struct.pack('<i', 0) + b'\0')
    offset = (48 + len(metadata) + 15) & ~15
    payloads = bytearray()
    rows = []
    for index, (pid, kind, payload) in enumerate(objects):
        payloads.extend(bytes((-len(payloads)) % 8))
        start = len(payloads)
        payloads.extend(payload)
        struct.pack_into('<qqIi', metadata, table + index * 24, pid, start, len(payload), types.index(kind))
        rows.append(dict(pathId=pid, offset=offset + start, size=len(payload), typeIndex=types.index(kind), classId=kind))
    header = bytearray(48)
    struct.pack_into('>I', header, 8, 22)
    struct.pack_into('>Iqq', header, 20, len(metadata), offset + len(payloads), offset)
    data = bytes(header + metadata + bytes(offset - 48 - len(metadata)) + payloads)
    row = dict(cab=cab, sourceBundle='Bundles/' + cab, snapshot=dict(file='source/' + cab + '.bytes',
        size=len(data), sha256=document.digest(data)), header=dict(metadataSize=len(metadata),
        dataOffset=offset, fileSize=len(data), version=22, endian=0), objects=rows,
        externals=[dict(fileId=i+1, cab=c) for i, c in enumerate(externals)],
        types=[dict(index=i, classId=c, scriptTypeIndex=-1, typeTreeNodes=0) for i, c in enumerate(types)])
    return data, row


class EffectSourceTests(unittest.TestCase):
    def fixture(self, directory, *, embedded=False, opaque=False, implicit=False):
        data, row = native_file('CAB-library', [(1, 115, b'needed-script'),
            (2, 115, b'optional-template'), (3, 115, b'unrelated-script'), (4, 142, b'old-library-index')])
        native = row['objects'][0]
        spec = dict(identity='cab-library:1', classId=115, type='MonoScript',
            state='raw-backed' if opaque else 'parsed', schema='schemas/script.json', data='objects/script.json',
            native=dict(cab=row['cab'], pathId=1, offset=native['offset'], size=native['size']),
            references=[], referenceCoverage='all-typed-pointers')
        manifest = dict(serializedFiles=[row], resources=[dict(identity=spec['identity'], native=copy.deepcopy(spec['native']))])
        files = {row['snapshot']['file']: data}
        if implicit:
            extra, extra_row = native_file('CAB-consumer', [(10, 114, b'component')], scripts=[(1, 2)], externals=['CAB-library'])
            files[extra_row['snapshot']['file']] = extra
            manifest['serializedFiles'].append(extra_row)
        if embedded:
            manifest['runtimeEmbeddedBundles'] = [row['sourceBundle']]
        catalog = dict(objects=[spec])
        files.update({'effect.json': document.dumps(catalog).encode(), 'source/manifest.json': document.dumps(manifest).encode(),
                      'schemas/script.json': b'{}', 'objects/script.json': b'{}'})
        receipt = dict(format='EFF_GENERATED_FILES/1', files={k: document.digest(v) for k, v in files.items()})
        files['source/generated-files.json'] = document.dumps(receipt).encode()
        for name, payload in files.items():
            target = directory / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
        workspace = dict(format='EFF_EFFECT_WORKSPACE', version=1, catalog=catalog,
                         sourceManifestSha256=document.digest(files['source/manifest.json']),
                         sourceEffectSha256=document.digest(files['effect.json']))
        return workspace, files

    def test_script_library_keeps_catalog_and_native_bytes_updates_all_offsets_and_hashes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace, files = self.fixture(root)
            before = copy.deepcopy(workspace)
            edit = dict(sourceManifestSha256=workspace['sourceManifestSha256'], sourceEffectSha256=workspace['sourceEffectSha256'])
            replacements, omitted, projected, delta = source.author_projection(root, workspace, edit)
            manifest = json.loads(replacements['source/manifest.json'])
            row = manifest['serializedFiles'][0]
            self.assertEqual([r['pathId'] for r in row['objects']], [1])
            data = replacements[row['snapshot']['file']]
            obj = row['objects'][0]
            self.assertEqual(data[obj['offset']:obj['offset'] + obj['size']], b'needed-script')
            self.assertEqual(projected['catalog']['objects'][0]['native']['offset'], obj['offset'])
            self.assertEqual(manifest['resources'][0]['native']['offset'], obj['offset'])
            self.assertEqual(projected['sourceManifestSha256'], delta['sourceManifestSha256'])
            receipt = json.loads(replacements['source/generated-files.json'])
            for name, payload in replacements.items():
                if name != 'source/generated-files.json':
                    self.assertEqual(receipt['files'][name], document.digest(payload))
            self.assertFalse(omitted)
            self.assertEqual(workspace, before)
            self.assertEqual({name: (root / name).read_bytes() for name in files}, files)

    def test_implicit_native_script_and_author_asset_templates_are_roots(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace, _ = self.fixture(root, implicit=True)
            replacements, _, _, _ = source.author_projection(root, workspace, None)
            rows = json.loads(replacements['source/manifest.json'])['serializedFiles'][0]['objects']
            self.assertEqual([r['pathId'] for r in rows], [1, 2])
            workspace['author_assets'] = [dict(template='cab-library:3')]
            replacements, _, _, _ = source.author_projection(root, workspace, None)
            rows = json.loads(replacements['source/manifest.json'])['serializedFiles'][0]['objects']
            self.assertEqual([r['pathId'] for r in rows], [1, 2, 3])

    def test_embedded_and_opaque_libraries_remain_complete(self):
        for options in (dict(embedded=True), dict(opaque=True)):
            with self.subTest(**options), tempfile.TemporaryDirectory() as directory:
                workspace, _ = self.fixture(Path(directory), **options)
                replacements, omitted, _, _ = source.author_projection(directory, workspace, None)
                self.assertFalse(replacements)
                self.assertFalse(omitted)

    def test_corrupt_script_snapshot_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace, files = self.fixture(root)
            path = root / 'source/CAB-library.bytes'
            data = bytearray(path.read_bytes()); data[-1] ^= 1; path.write_bytes(data)
            with self.assertRaisesRegex(ValueError, '摘要'):
                source.author_projection(root, workspace, None)


if __name__ == '__main__':
    unittest.main()
