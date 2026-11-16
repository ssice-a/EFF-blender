"""Portable author baselines keep editable data, not game shader programs.

The original source directory is never changed. Terminal game shaders retain
their exact native identity, logical paths and preview name in the catalog;
the game's prefab lease supplies their programs. Explicitly embedded bundles
and opaque resources retain their snapshots. Script libraries retain the
catalog/template closure, including implicit native script-table references.
"""
import copy
import json
import struct
from pathlib import Path

if __package__:
    from . import eiem_effect_document as document
    from .eiem_effect_native import Reader
else:
    import eiem_effect_document as document
    from eiem_effect_native import Reader


def _object_table(data, row):
    """Locate preserved v22 metadata using the source's exact type extents."""
    document.require(len(data) >= 48 and struct.unpack_from('>I', data, 8)[0] == 22 and
                     data[16] == 0, '作者底稿需要完整的小端 v22 元数据')
    metadata, size, offset = struct.unpack_from('>Iqq', data, 20)
    document.require((metadata, size, offset) == (row['header']['metadataSize'],
                     row['snapshot']['size'], row['header']['dataOffset']) and
                     48 + metadata <= len(data), '作者底稿头范围不一致')
    at = data.index(b'\0', 48) + 1 + 4
    document.require(data[at] == 1, '作者底稿缺少 TypeTree')
    at += 1
    count = struct.unpack_from('<i', data, at)[0]
    at += 4
    document.require(count == len(row['types']), '作者底稿类型数量不一致')
    for spec in row['types']:
        class_id, _, script = struct.unpack_from('<iBh', data, at)
        document.require((class_id, script) == (spec['classId'], spec['scriptTypeIndex']),
                         '作者底稿类型标识不一致')
        at += 7 + (16 if class_id == 114 else 0) + 16
        nodes, strings = struct.unpack_from('<ii', data, at)
        document.require(nodes == spec['typeTreeNodes'] and strings >= 0,
                         '作者底稿类型范围不一致')
        at += 8 + nodes * 32 + strings
        dependencies = struct.unpack_from('<i', data, at)[0]
        document.require(dependencies >= 0, '作者底稿类型依赖无效')
        at += 4 + dependencies * 4
    count_at = at
    count = struct.unpack_from('<i', data, at)[0]
    at = (at + 7) & ~3
    document.require(count == len(row['objects']), '作者底稿对象数量不一致')
    for obj in row['objects']:
        pid, start, length, kind = struct.unpack_from('<qqIi', data, at)
        document.require((pid, start + offset, length, kind) ==
                         (obj['pathId'], obj['offset'], obj['size'], obj['typeIndex']),
                         '作者底稿对象表不一致')
        at += 24
    table_end = at
    scripts = []
    count = struct.unpack_from('<i', data, at)[0]
    at += 4
    document.require(0 <= count <= (48 + metadata - at) // 12, '作者底稿脚本表无效')
    for _ in range(count):
        file_id = struct.unpack_from('<i', data, at)[0]
        at = (at + 7) & ~3
        path_id = struct.unpack_from('<q', data, at)[0]
        at += 8
        cab = row['cab'] if file_id == 0 else next(
            (r['cab'] for r in row.get('externals', []) if r['fileId'] == file_id), None)
        document.require(cab is not None, '作者底稿脚本依赖文件不存在')
        if path_id:
            scripts.append(cab.casefold() + ':' + str(path_id))
    return count_at, table_end, scripts


def _script_projection(source, manifest, catalog, workspace, edit):
    """Trim verified script-only libraries; mixed/opaque sources stay whole.

    Source Bundle preload entries index the donor's entire script library and
    are not roots of an editable author snapshot. Native runtime compilation
    constructs its own bundle/container from the retained Prefab references.
    """
    embedded = set(manifest.get('runtimeEmbeddedBundles', []))
    candidates = [r for r in manifest.get('serializedFiles', [])
                  if r['sourceBundle'] not in embedded and not r.get('externals') and
                  all(o['classId'] in (115, 142) for o in r['objects']) and
                  any(o['classId'] == 115 for o in r['objects'])]
    if not candidates:
        return {}
    required = {o['identity'].casefold() for o in catalog['objects']}
    required.update(r['identity'].casefold() for r in manifest.get('resources', []) if r.get('identity'))
    required.update(r['asset']['identity'].casefold() for r in manifest.get('containerEntries', [])
                    if (r.get('asset') or {}).get('identity'))
    for values in (workspace.get('author_assets', []), workspace.get('added', []),
                   (edit or {}).get('assets', []), (edit or {}).get('authorMeshes', []),
                   (edit or {}).get('emitters', {}).get('added', [])):
        required.update(r['template'].casefold() for r in values if r.get('template'))
    for row in manifest.get('serializedFiles', []):
        with document.safe_path(source, row['snapshot']['file']).open('rb') as stream:
            metadata = stream.read(48 + row['header']['metadataSize'])
        _, _, scripts = _object_table(metadata, row)
        required.update(scripts)
    replacements = {}
    for row in candidates:
        specs = [o for o in catalog['objects'] if o['native']['cab'].casefold() == row['cab'].casefold()]
        if any(o['classId'] != 115 or o['state'] != 'parsed' or
               o.get('referenceCoverage') != 'all-typed-pointers' or o.get('references') for o in specs):
            continue
        kept = [o for o in row['objects'] if row['cab'].casefold() + ':' + str(o['pathId']) in required]
        if not kept or len(kept) == len(row['objects']) or any(o['classId'] == 142 for o in kept):
            continue
        data = document.safe_path(source, row['snapshot']['file']).read_bytes()
        document.require(len(data) == row['snapshot']['size'] and document.digest(data) == row['snapshot']['sha256'].upper(),
                         '作者脚本底稿摘要不一致')
        count_at, table_end, _ = _object_table(data, row)
        metadata = bytearray(data[48:count_at])
        metadata.extend(struct.pack('<i', len(kept)))
        metadata.extend(bytes((-len(metadata)) % 4))
        table_at = len(metadata)
        metadata.extend(bytes(24 * len(kept)))
        metadata.extend(data[table_end:48 + row['header']['metadataSize']])
        offset = (48 + len(metadata) + 15) & ~15
        payloads = bytearray()
        for index, obj in enumerate(kept):
            payloads.extend(bytes((-len(payloads)) % 8))
            start = len(payloads)
            payloads.extend(data[obj['offset']:obj['offset'] + obj['size']])
            struct.pack_into('<qqIi', metadata, table_at + index * 24,
                             obj['pathId'], start, obj['size'], obj['typeIndex'])
            obj['offset'] = offset + start
        header = bytearray(data[:48])
        struct.pack_into('>Iqq', header, 20, len(metadata), offset + len(payloads), offset)
        result = bytes(header + metadata + bytes(offset - 48 - len(metadata)) + payloads)
        row['objects'] = kept
        row['header'].update(metadataSize=len(metadata), dataOffset=offset, fileSize=len(result))
        row['snapshot'].update(size=len(result), sha256=document.digest(result))
        _object_table(result, row)
        rows = {o['pathId']: o for o in kept}
        for spec in specs:
            spec['native']['offset'] = rows[spec['native']['pathId']]['offset']
        for resource in manifest.get('resources', []):
            native = resource.get('native', {})
            if native.get('cab', '').casefold() == row['cab'].casefold() and native.get('pathId') in rows:
                native['offset'] = rows[native['pathId']]['offset']
        replacements[row['snapshot']['file']] = result
    return replacements


def author_projection(source, workspace, edit):
    source = Path(source)
    manifest = json.loads((source / 'source/manifest.json').read_bytes())
    catalog = workspace.get('catalog', {})
    if not catalog.get('objects'):
        return {}, set(), workspace, edit
    workspace = dict(workspace, catalog=copy.deepcopy(catalog))
    catalog = workspace['catalog']
    edit = dict(edit) if edit is not None else None
    replacements = _script_projection(source, manifest, catalog, workspace, edit)
    embedded = set(manifest.get('runtimeEmbeddedBundles', []))
    objects = catalog['objects']
    candidates = []
    for row in manifest.get('serializedFiles', []):
        specs = [o for o in objects if o['native']['cab'].casefold() == row['cab'].casefold()]
        if (row['sourceBundle'] not in embedded and specs and
                all(o['classId'] == 48 and o['state'] == 'raw-backed' and
                    o.get('logicalPaths') and not o.get('references') for o in specs) and
                all(o['classId'] in (48, 142, 200) for o in row['objects']) and
                not row.get('externals')):
            candidates.append((row, specs))
    if not candidates and not replacements:
        return {}, set(), workspace, edit

    projected = {o['identity']: o for o in workspace['catalog']['objects']}
    reader = Reader(document.Editor(dict(workspace, source=str(source))))
    omitted = set()
    cabs = set()
    for row, specs in candidates:
        for original in specs:
            # A bounded native read verifies the source and preserves the real
            # shader family used by the material UI and offline preview.
            spec = projected[original['identity']]
            spec['shaderName'] = reader.shader_name(original['identity'])
            spec['state'] = 'game-reference'
            spec['reason'] = 'Unmodified game shader supplied by the source prefab lease'
        omitted.add(row['snapshot']['file'])
        cabs.add(row['cab'].casefold())
    manifest['serializedFiles'] = [r for r in manifest['serializedFiles'] if r['cab'].casefold() not in cabs]
    replacements.update({
        'source/manifest.json': document.dumps(manifest).encode('utf-8'),
        'effect.json': document.dumps(workspace['catalog']).encode('utf-8'),
    })
    receipt = json.loads((source / 'source/generated-files.json').read_bytes())
    for name in omitted:
        receipt['files'].pop(name, None)
    for name, data in replacements.items():
        receipt['files'][name] = document.digest(data)
    replacements['source/generated-files.json'] = document.dumps(receipt).encode('utf-8')
    for field, name in (('sourceManifestSha256', 'source/manifest.json'), ('sourceEffectSha256', 'effect.json')):
        workspace[field] = document.digest(replacements[name])
        if edit is not None:
            edit[field] = workspace[field]
    return replacements, omitted, workspace, edit
