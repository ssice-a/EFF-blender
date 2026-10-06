"""Publish game-ready reload inputs using the DLL's data structures.

This module only resolves user-selected game paths and invokes the canonical
disk compiler. It does not parse resources or implement a second exporter.
"""
from pathlib import Path
import json
import os
import re
import shutil
import subprocess

EXPECTED_PROTOCOL = 'EFF_OFFLINE_PACKAGE_PROTOCOL'
EXPECTED_COMPILER = 'EFF_NATIVE_PACK_V35_1'
COMPILERS = {35: EXPECTED_COMPILER, 36: 'EFF_NATIVE_PACK_V36_ASSEMBLY_1'}
STRUCTURES = dict(
    manifest='author-graph+inputs+roots+closure+parts+materials/le64/sha256',
    author='mod-ini/parts+replace+resources+controls',
    resource='block-directory/le64/sha256/type-tree',
    current='key+manifest-sha256/tsv')
MOD_SET_STRUCTURE = 'authors+shared-inputs/transaction'
MANIFEST_ENCODING = 'self-described-roots/le64'
LEGACY_ENCODINGS = {
    'EFF_NATIVE_PACK_V35_1': 'legacy-root-markers/le64',
    'EFF_NATIVE_PACK_V36_ASSEMBLY_1': 'legacy-root-addresses/le64'}
COMPILER_ARGUMENTS = ['author', 'baseline', 'cache']
LEGACY_ARGUMENTS = [*COMPILER_ARGUMENTS, 'assembly']
STATIC_SOURCE_INPUTS = 'mod-static-catalog/selected-sources'


def _structures(value):
    """Read field contracts; release/version/ABI labels are informational."""
    if not isinstance(value, dict) or value.get('format') != EXPECTED_PROTOCOL:
        raise ValueError('资源编译器没有 EFF 数据结构声明')
    declared = value.get('structures')
    if declared is None:
        # Old tools declare these exact layouts through their dialect markers.
        # Recognising them selects a parser, without requiring this build label.
        if value.get('compiler') not in COMPILERS.values():
            raise ValueError('旧资源编译器没有可识别的数据结构声明')
        declared = dict(STRUCTURES)
        declared['manifestEncoding'] = LEGACY_ENCODINGS[value['compiler']]
        declared['manifestInputs'] = [declared['manifestEncoding']]
        if 'modSetVersion' in value:
            declared['modSet'] = MOD_SET_STRUCTURE
    if not isinstance(declared, dict) or any(declared.get(k) != v for k, v in STRUCTURES.items()):
        raise ValueError('资源编译器数据结构不兼容：' + json.dumps(declared, ensure_ascii=False))
    if (not isinstance(declared.get('manifestEncoding', MANIFEST_ENCODING), str) or
            ('manifestInputs' in declared and
             (not isinstance(declared['manifestInputs'], list) or
              not declared['manifestInputs'] or
              any(not isinstance(item, str) or not item for item in declared['manifestInputs'])))):
        raise ValueError('资源编译器清单字段布局声明无效')
    return declared


def _require_manifest_reader(produced, consumer):
    # Older DLLs do not understand the new self-describing header. This is an
    # actual byte-layout difference, even if their informational ABI is equal.
    encoding = produced.get('manifestEncoding', MANIFEST_ENCODING)
    accepted = consumer.get('manifestInputs', [MANIFEST_ENCODING, *LEGACY_ENCODINGS.values()])
    if encoding not in accepted:
        raise ValueError('DLL 无法读取编译器输出的清单字段布局：' + str(encoding))


def _compiler_protocol(path, expected=None):
    """Validate an offline compiler before it can publish ``current.tsv``."""
    path = Path(path)
    try:
        result = subprocess.run(
            [str(path), '--protocol'], capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=5, check=False,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError('无法查询资源编译器协议：' + str(path)) from error
    if result is not None and result.returncode == 0:
        for line in reversed(result.stdout.splitlines()):
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            declared = _structures(value)
            if 'structures' not in value:
                value.setdefault('arguments', LEGACY_ARGUMENTS)
            if isinstance(expected, dict):
                required = _structures(expected)
                if any(declared.get(k) != required[k] for k in STRUCTURES):
                    raise ValueError('资源编译器与 DLL 的数据结构不兼容')
                if 'modSet' in required and declared.get('modSet') != required['modSet']:
                    raise ValueError('资源编译器不支持 DLL 的完整作者集合结构')
                _require_manifest_reader(declared, required)
            value['structures'] = declared
            return value
        raise ValueError('资源编译器没有返回完整的 EFF 协议声明：' + str(path))
    raise ValueError('资源编译器协议查询失败：' + str(path) + '\n' +
                     (result.stderr or result.stdout)[-1600:])


def _select_compiler(candidates, expected=None):
    errors = []
    for candidate in candidates:
        candidate = Path(candidate)
        if not candidate.is_file():
            continue
        try:
            protocol = _compiler_protocol(candidate, expected)
        except ValueError as error:
            errors.append(str(error))
            continue
        return candidate, protocol
    detail = '; '.join(errors) or '未找到 eff_resource_pack.exe'
    raise ValueError('没有与生产 DLL 匹配的资源编译器：' + detail)


def _game_protocol(game):
    """Installations can declare their field contracts without pinning releases."""
    declaration = Path(game) / 'plugin/package-protocol.json'
    if not declaration.is_file():
        return None
    try:
        value = json.loads(declaration.read_text('utf-8-sig'))
    except (OSError, ValueError) as error:
        raise ValueError('游戏离线资源协议声明无效：' + str(declaration)) from error
    _structures(value)
    return value


def reload_context(destination):
    destination = Path(destination).resolve()
    configured = os.environ.get('EFF_RELOAD_GAME')
    game = Path(configured).resolve() if configured else None
    # A normal export to <game>/plugin/mods/<Mod> needs no machine-specific
    # config. Any other destination still requires EFF_RELOAD_GAME.
    if game is None:
        for parent in destination.parents:
            if parent.name.lower() == 'plugin' and ((parent / 'resource-reload-baseline/baseline.tsv').is_file()
                    or (parent / 'tools/eff_resource_pack.exe').is_file()):
                game = parent.parent
                break
    if game is None:
        return None
    baseline = game / 'plugin/resource-reload-baseline'
    mods = (game / 'plugin/mods').resolve()
    cache = game / 'plugin/resource-reload-cache' if destination.parent == mods else destination.with_name(destination.name + '.reload')
    protocol = _game_protocol(game)
    candidates = [game / 'plugin/tools/eff_resource_pack.exe',
                  Path(__file__).parent / 'nativepack/eff_resource_pack.exe',
                  Path(__file__).resolve().parents[2] / 'bin/resource-pack-v35/eff_resource_pack.exe',
                  Path(__file__).resolve().parents[2] / 'bin/resource-pack/eff_resource_pack.exe']
    compiler, declaration = _select_compiler(candidates, protocol)
    if not (baseline / 'baseline.tsv').is_file() and declaration.get('staticSourceInputs') != STATIC_SOURCE_INPUTS:
        raise ValueError('完整离线热重载需要声明兼容数据结构的 eff_resource_pack.exe 和游戏来源基线')
    return compiler, baseline, cache, game


def prepare_static_sources(destination, source_package, context, previous=None):
    compiler, baseline, cache, game = context
    protocol = _compiler_protocol(compiler, _game_protocol(game))
    if protocol.get('staticSourceInputs') != STATIC_SOURCE_INPUTS:
        return  # Existing prepared static baselines remain readable.
    # Full exports start with an empty staging directory. Seed the previous
    # descriptor so the source tool can validate its request hash and reuse it;
    # changed declarations still take the tool's normal discovery path.
    descriptor = Path(destination) / 'source-inputs.bin'
    if previous is not None and not descriptor.is_file():
        prior = Path(previous) / 'source-inputs.bin'
        if prior.is_file():
            shutil.copyfile(prior, descriptor)
    configured = os.environ.get('EFF_SOURCE_INDEX')
    index = Path(configured).resolve() if configured else None
    source_package = Path(source_package).resolve()
    if index is None:
        for parent in (source_package, *source_package.parents):
            candidate = parent / 'index/endfield_assets.eidx'
            if candidate.is_file():
                index = candidate
                break
    # An unchanged descriptor can be reused by the tool without opening an
    # index. An index is required only for initial or changed source identities.
    index = index or source_package / 'index/endfield_assets.eidx'
    tool = Path(game) / 'plugin/tools/static-sources/EndfieldVfsProbe.exe'
    if not tool.is_file():
        tool = Path(__file__).parent / 'nativepack/static-sources/EndfieldVfsProbe.exe'
    if not tool.is_file():
        tool = Path(__file__).resolve().parents[2] / 'tools/EndfieldVfsProbe/bin/Release/net9.0/EndfieldVfsProbe.exe'
    if not tool.is_file():
        raise ValueError('缺少静态来源准备工具，请安装配套插件工具')
    command = [str(tool), '--prepare-static-sources', str(game), str(index), str(destination), str(compiler)]
    result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace',
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), check=False)
    if result.returncode:
        raise ValueError('静态来源准备失败，作者文件尚未发布：' + (result.stderr or result.stdout)[-1600:])
    if not (Path(destination) / 'source-inputs.bin').is_file():
        raise ValueError('静态来源工具没有生成 source-inputs.bin')


def compile_reload(destination, context=None):
    context = context if context is not None else reload_context(destination)
    if context is None:
        return dict(offlineReloadPrepared=False, offlineReloadReason='workspace-author-export')
    compiler, baseline, cache, game = context
    protocol = _compiler_protocol(compiler, _game_protocol(game))
    arguments = protocol.get('arguments', COMPILER_ARGUMENTS)
    if arguments not in (COMPILER_ARGUMENTS, LEGACY_ARGUMENTS):
        raise ValueError('资源编译器命令参数结构不兼容：' + str(arguments))
    destination = Path(destination).resolve()
    mods = (game / 'plugin/mods').resolve()
    global_cache = (game / 'plugin/resource-reload-cache').resolve()
    installed = destination.parent == mods and Path(cache).resolve() == global_cache
    if installed and protocol['structures'].get('modSet') == MOD_SET_STRUCTURE:
        command = [str(compiler), '--mods', str(mods), str(baseline), str(cache)]
    else:
        if installed:
            authors = [path.resolve() for path in mods.iterdir()
                       if path.is_dir() and (path / 'mod.ini').is_file()]
            if authors != [destination]:
                raise ValueError('当前编译器不支持多角色 Mod 组合的完整作者集合结构，不能向公共缓存发布单个 Mod')
        command = [str(compiler), str(destination), str(baseline), str(cache)]
    if arguments == LEGACY_ARGUMENTS:
        command.append(str(game / 'GameAssembly.dll'))
    result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace',
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), check=False)
    if result.returncode:
        raise ValueError('离线资源构建失败，游戏继续保留上一完整候选：' + (result.stderr or result.stdout)[-1600:])
    if any(row.startswith('OFFLINE-SOURCE-LOOKUP-PENDING ') for row in result.stdout.splitlines()):
        raise ValueError('静态来源未准备完整，请重新生成该 Mod 的来源清单；旧候选未发布为新导出结果')
    rows = (cache / 'current.tsv').read_text('utf-8-sig').splitlines()
    if len(rows) != 2 or len(rows[0].split('\t')) != 2 or rows[0].split('\t')[0] != 'EFF_RESOURCE_RELOAD_CURRENT':
        raise ValueError('离线构建未发布完整候选清单')
    pointer = rows[1].split('\t')
    if len(pointer) != 2 or any(not re.fullmatch(r'[0-9A-F]{64}', value) for value in pointer):
        raise ValueError('离线构建发布的候选指针无效')
    key, digest = pointer
    return dict(offlineReloadPrepared=True, offlineReloadKey=key, offlineReloadManifestHash=digest,
                offlineReloadCache=str(cache), offlineCompiler=protocol.get('compiler', str(compiler)))
