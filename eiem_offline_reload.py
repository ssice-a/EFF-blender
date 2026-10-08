"""Compile one staged Mod with this add-on's own author tools."""
from pathlib import Path
import json
import math
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
COMPILED_MOD = 'compiled-mod/source-author-spans+object-layouts+type-dictionary+relocations/le64/sha256'
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
    """Read the compiler's declared byte structures before author export."""
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
            if parent.name.lower() == 'plugin' and (parent.parent / 'Endfield_Data').is_dir():
                game = parent.parent
                break
    if game is None:
        raise ValueError('离线编译需要游戏安装路径；导出到游戏 plugin/mods，或设置 EFF_RELOAD_GAME')
    baseline = destination / 'compiled.bin'
    cache = destination
    protocol = _game_protocol(game)
    if protocol is not None and protocol.get('compiledMod') != COMPILED_MOD:
        raise ValueError('当前 DLL 不支持独立 Mod 编译结构，请安装配套 DLL')
    candidates = [Path(__file__).parent / 'nativepack/eff_resource_pack.exe']
    compiler, declaration = _select_compiler(candidates, protocol)
    if declaration.get('compiledMod') != COMPILED_MOD:
        raise ValueError('插件缺少支持独立 Mod 编译结果的配套工具，请安装完整插件包')
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
    tool = Path(__file__).parent / 'nativepack/eff_source_prepare.exe'
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
    consumer = _game_protocol(game)
    protocol = _compiler_protocol(compiler, consumer)
    if protocol.get('compiledMod') != COMPILED_MOD or (consumer is not None and consumer.get('compiledMod') != COMPILED_MOD):
        raise ValueError('插件或 DLL 不支持独立 Mod 编译结构，请更新配套文件')
    destination = Path(destination).resolve()
    command = [str(compiler), '--export-mod', str(destination), str(game)]
    result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace',
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), check=False)
    if result.returncode:
        raise ValueError('离线资源构建失败，游戏继续保留上一完整候选：' + (result.stderr or result.stdout)[-1600:])
    if any(row.startswith('OFFLINE-SOURCE-LOOKUP-PENDING ') for row in result.stdout.splitlines()):
        raise ValueError('静态来源未准备完整，请重新生成该 Mod 的来源清单；旧候选未发布为新导出结果')
    artifact = destination / 'compiled.bin'
    if not artifact.is_file():
        raise ValueError('资源编译没有生成完整的 compiled.bin')
    import hashlib
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest().upper()
    costs = {}
    for row in result.stdout.splitlines():
        fields = row.split()
        if len(fields) == 3 and fields[0] == 'COMPILED-MOD-COST':
            try:
                elapsed = float(fields[2])
            except ValueError:
                continue
            if math.isfinite(elapsed) and elapsed >= 0:
                costs[fields[1]] = elapsed
    return dict(offlineReloadPrepared=True, offlineReloadKey=digest,
                offlineReloadManifestHash=digest, offlineReloadCache=str(destination),
                offlineCompiler=protocol.get('compiler', str(compiler)),
                offlineCompilerCostsMs=costs)
