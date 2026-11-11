"""Publish standalone effect-only Format 3 Mods."""
from pathlib import Path
import tempfile


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _valid_prefab_path(value):
    path = str(value or '').strip()
    return (path.startswith('assets/') and path.endswith('.prefab') and
            '\\' not in path and all(part not in ('', '.', '..')
                                     for part in path.split('/')))


def write_effect_directory(staging, mod_id, effects, author_format3,
                           read_native_build, format_io):
    """Write and validate a complete standalone effect-only package."""
    staging = Path(staging).resolve()
    _require(staging.is_dir(), '特效 Mod 暂存目录不存在')
    _require(effects, '请选择至少一个特效')
    package_effects = []
    author_files = {}
    identities = set()
    author_names = set()
    for source in effects:
        effect = dict(source)
        effect_id = str(effect.get('id', '')).strip()
        _require(effect_id, '特效缺少稳定 ID')
        mode = effect.get('mode', 'mesh_attachment')
        _require(mode in ('mesh_attachment', 'replace_game_prefab'),
                 '特效用途值无效：' + str(mode))
        if mode == 'replace_game_prefab':
            target = effect.get('replacementPrefab', '')
            _require(_valid_prefab_path(target),
                     '替换游戏特效时必须指定规范的 assets/.../*.prefab 逻辑路径')
            effect.update(targetPrefab=target, rendererPath='', targetPath='',
                          targetAsset='', bones=[], part='', bone='')
        identity = author_format3.identifier(effect_id)
        _require(identity and identity not in identities,
                 '特效 ID 为空或重复：' + effect_id)
        identities.add(identity)
        author_name = identity + '.effeffect'
        _require(author_name not in author_names,
                 '特效作者快照文件名冲突：' + author_name)
        author_names.add(author_name)

        author_data = effect.pop('_authorData', None)
        _require(isinstance(author_data, (bytes, bytearray)) and author_data,
                 '特效缺少可编辑作者快照：' + effect_id)
        snapshot = format_io.decode_format3_effect_snapshot(bytes(author_data))
        saved_attachment = snapshot.get('attachment', {})
        _require(str(saved_attachment.get('id', '')) == effect_id,
                 '特效作者快照 ID 与运行时挂载不一致：' + effect_id)
        if mode == 'replace_game_prefab':
            _require(saved_attachment.get('mode') == mode and
                     saved_attachment.get('replacementPrefab') == effect['replacementPrefab'],
                     '特效作者快照替换目标不一致：' + effect_id)
        else:
            for field in ('targetPrefab', 'rendererPath', 'targetPath', 'targetAsset'):
                _require(saved_attachment.get(field, '') == effect.get(field, ''),
                         '特效作者快照来源证明不一致：' + effect_id + '/' + field)

        native = effect.pop('_native', None)
        if native is None:
            build = effect.get('build')
            _require(build, '特效尚未构建：' + effect_id)
            native = read_native_build(build)
        _require(isinstance(native, dict) and native.get('bundles'),
                 '特效原生构建结果为空：' + effect_id)
        key = str(native.get('key', ''))
        _require(key and author_format3.identifier(key) == key,
                 '特效原生资源 ID 无效：' + effect_id)

        # An effect-only Mod resolves its exact source PFB/Renderer at runtime;
        # it must not depend on a Part supplied by a character replacement.
        effect['part'] = ''
        effect['native'] = native
        author_files[author_name] = bytes(author_data)
        package_effects.append(effect)

    author_format3.write_format3_directory(
        staging, mod_id, [], {}, [], [], [], [], package_effects,
        author_files=author_files, mod_type='effect')
    manifest = author_format3.read_format3_directory(staging)
    _require(manifest.get('modType') == 'effect',
             'Format 3 writer did not produce an effect-only Mod')
    return manifest


def publish(destination, effects):
    """Build a validated candidate and transactionally publish it."""
    from . import eiem_format, eiem_native_export

    destination = Path(destination).resolve()
    _require(destination.name, '特效 Mod 输出路径无效')
    sources = {Path(row['_source']).resolve() for row in effects if row.get('_source')}
    _require(all(destination != source and source not in destination.parents and
                 destination not in source.parents
                 for source in sources), '不能将特效 Mod 写入 .efffx 来源目录')

    modules, _ = eiem_native_export.pack_modules()
    author_format3 = modules['author_format3']
    read_native_build = modules['effect_native'].read_native_build
    destination.parent.mkdir(parents=True, exist_ok=True)
    mod_id = destination.name
    if destination.exists() and any(destination.iterdir()):
        if not (destination / 'mod.ini').is_file():
            raise ValueError('不能覆盖缺少 mod.ini 的目录，请选择新的特效 Mod 目录')
        previous = eiem_native_export.publication_inventory(destination)
        _require(previous.get('modType') == 'effect',
                 '输出目录是角色 Mod；请通过角色的完整 Format 3 导出更新特效')
        mod_id = previous['modId']
    mod_id = author_format3.identifier(mod_id)
    for manifest in destination.parent.rglob('mod.ini'):
        if manifest.parent.resolve() == destination:
            continue
        try:
            ini = author_format3.configparser.ConfigParser(
                interpolation=None, delimiters=('=',), strict=True)
            ini.optionxform = str
            with manifest.open(encoding='utf-8-sig') as stream:
                ini.read_file(stream)
            sibling_id = ini.get('Mod', 'id', fallback='')
        except (OSError, author_format3.configparser.Error):
            continue
        if sibling_id == mod_id:
            raise ValueError('已有 Mod 使用相同 ID：' + mod_id)

    with tempfile.TemporaryDirectory(prefix='.' + destination.name + '.effect-',
                                     dir=destination.parent) as temporary:
        staging = Path(temporary) / 'candidate'
        staging.mkdir()
        candidate = write_effect_directory(
            staging, mod_id, effects, author_format3,
            read_native_build, eiem_format)

        def reader(path):
            return author_format3.read_format3_directory(path)

        eiem_native_export.publish(staging, destination, reader, candidate=candidate)
    return False
