"""Compile one staged Mod with this add-on's own author tools."""
from pathlib import Path
import json
import math
import os
import re
import shutil
import subprocess
import hashlib
import contextlib
import struct

EXPECTED_PROTOCOL = 'EFF_OFFLINE_PACKAGE_PROTOCOL'
EXPECTED_COMPILER = 'EFF_NATIVE_PACK_V35_1'
COMPILERS = {35: EXPECTED_COMPILER, 36: 'EFF_NATIVE_PACK_V36_ASSEMBLY_1'}
STRUCTURES = dict(
    manifest='author-graph+inputs+roots+closure+parts+materials/le64/sha256',
    author='mod-ini/parts+replace+resources+controls',
    resource='block-directory/le64/sha256/type-tree',
    current='key+manifest-sha256/tsv')
MOD_SET_STRUCTURE = 'authors+shared-inputs/transaction'
COMPILED_MOD = 'native-view-index+assembly-dictionary+verified-source-stamps+per-input-proofs/le64/sha256'
MANIFEST_ENCODING = 'self-described-roots/le64'
LEGACY_ENCODINGS = {
    'EFF_NATIVE_PACK_V35_1': 'legacy-root-markers/le64',
    'EFF_NATIVE_PACK_V36_ASSEMBLY_1': 'legacy-root-addresses/le64'}
COMPILER_ARGUMENTS = ['author', 'baseline', 'cache']
LEGACY_ARGUMENTS = [*COMPILER_ARGUMENTS, 'assembly']
STATIC_SOURCE_INPUTS = 'mod-static-catalog/selected-sources'
SOURCE_PREPARATION = 'in-process/index-vfs+author-declarations'
EXPORT_CARRYOVER = 'validated-source-selection+published-discoveries'


@contextlib.contextmanager
def publication_lock(destination):
    """Share the runtime block writer's mutex, only during final publication."""
    if os.name != 'nt':
        yield
        return
    import ctypes
    from ctypes import wintypes
    path = str((Path(destination).resolve() / 'compiled.bin').resolve()).replace('\\', '/')
    path = path.translate(str.maketrans('ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'))
    name = 'Local\\EFFModBlocks-' + hashlib.sha256(path.encode('utf-8')).hexdigest().upper()
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    api.CreateMutexW.restype = wintypes.HANDLE
    api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    api.WaitForSingleObject.restype = wintypes.DWORD
    api.ReleaseMutex.argtypes = [wintypes.HANDLE]
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = api.CreateMutexW(None, False, name)
    if not handle:
        raise OSError(ctypes.get_last_error(), '无法取得 Mod 发布锁')
    acquired = False
    try:
        result = api.WaitForSingleObject(handle, 0xffffffff)
        if result not in (0, 0x80):
            raise OSError(ctypes.get_last_error(), 'Mod 发布锁等待失败')
        acquired = True
        yield
    finally:
        if acquired:
            api.ReleaseMutex(handle)
        api.CloseHandle(handle)


def published_revision(destination):
    """Read the committed block identity without hashing all source payloads."""
    path = Path(destination).resolve() / 'compiled.bin'
    if not path.is_file():
        return None
    with path.open('rb') as stream:
        status = os.fstat(stream.fileno())
        header = stream.read(192)
        if header[:16] == b'EFF_MOD_BLOCKS/1':
            valid = []
            for number in range(2):
                slot = header[16 + number * 88:16 + (number + 1) * 88]
                if len(slot) != 88 or hashlib.sha256(slot[:56]).digest() != slot[56:]:
                    continue
                generation, offset, size = struct.unpack_from('<QQQ', slot)
                if not generation or offset < 192 or offset > status.st_size or size > status.st_size - offset:
                    continue
                stream.seek(offset)
                digest = hashlib.sha256()
                remaining = size
                while remaining:
                    data = stream.read(min(1024 * 1024, remaining))
                    if not data:
                        raise ValueError('Mod 提交索引被截断，保留上一发布')
                    digest.update(data)
                    remaining -= len(data)
                if digest.digest() == slot[24:56]:
                    valid.append((generation, digest.hexdigest()))
            if not valid:
                raise ValueError('Mod 没有可读的已提交代际，不能丢弃新增命中')
            generation, digest = max(valid)
            return (status.st_dev, status.st_ino, 'blocks', generation, digest)
        stream.seek(0)
        digest = hashlib.sha256()
        for data in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(data)
        return (status.st_dev, status.st_ino, 'packet', digest.hexdigest())


def publish_reload(staging, destination, publisher, context, source_index, compiled):
    """Optimistically merge a concurrent discovery before swapping author files."""
    for attempt in range(3):
        with publication_lock(destination):
            if published_revision(destination) == compiled.get('offlinePreviousRevision'):
                return publisher(), compiled
        if attempt == 2:
            break
        # Compilation must run outside the game writer's mutex.
        compiled = compile_reload(staging, context, previous=destination, source_index=source_index)
    raise ValueError('游戏在导出期间持续补写命中，暂存结果未发布；上一完整 Mod 保持')


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
    view = produced.get('compiledInputView')
    if view is not None and consumer.get('compiledInputView') != view:
        raise ValueError('DLL 无法读取编译器输出的原生输入读取布局，请同步部署 DLL：' + str(view))
    runtime = produced.get('compiledRuntimeIndex')
    if runtime is not None and consumer.get('compiledRuntimeIndex') != runtime:
        raise ValueError('DLL 无法读取编译器输出的运行索引布局，请同步部署 DLL：' + str(runtime))


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


def valid_game_directory(path):
    path = Path(path)
    return ((path / 'Endfield.exe').is_file() and (path / 'GameAssembly.dll').is_file()
            and (path / 'Endfield_Data/StreamingAssets/VFS').is_dir())


def _games_at(path):
    """Inspect the selected root and launcher games folder, never a drive tree."""
    root = Path(os.path.expandvars(str(path))).expanduser().resolve()
    if root.name.lower() == 'endfield.exe' or root.name.lower() == 'endfield_data':
        root = root.parent
    if valid_game_directory(root):
        return [root]
    folders = [root / 'games']
    if root.name.lower() == 'games':
        folders.append(root)
    candidates = [root / 'Endfield Game']
    for folder in folders:
        try:
            candidates.extend(child for child in folder.iterdir() if child.is_dir())
        except OSError:
            continue
    return sorted({candidate.resolve() for candidate in candidates if valid_game_directory(candidate)},
                  key=lambda p: str(p).casefold())


def _registry_game_hints():
    if os.name != 'nt':
        return []
    import winreg
    results = []
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, r'Software\Microsoft\Windows\CurrentVersion\Uninstall',
                                    0, winreg.KEY_READ | view) as uninstall:
                    for index in range(winreg.QueryInfoKey(uninstall)[0]):
                        try:
                            with winreg.OpenKey(uninstall, winreg.EnumKey(uninstall, index)) as entry:
                                name = str(winreg.QueryValueEx(entry, 'DisplayName')[0]).casefold()
                                if any(label in name for label in ('endfield', '终末地', 'hypergryph', '鹰角')):
                                    results.append(winreg.QueryValueEx(entry, 'InstallLocation')[0])
                        except OSError:
                            continue
            except OSError:
                continue
    return results


def _launcher_game_hints():
    if os.name != 'nt':
        return []
    return [Path(drive + ':/' + prefix + 'Hypergryph Launcher/games')
            for drive in 'CDEFGHIJKLMNOPQRSTUVWXYZ'
            for prefix in ('', 'Program Files/', 'Program Files (x86)/')]


def discover_game_directories():
    hints = []
    local = os.environ.get('LOCALAPPDATA')
    if local:
        try:
            saved = json.loads((Path(local) / 'EFF/Repair/settings.json').read_text('utf-8-sig'))
            if isinstance(saved, dict) and isinstance(saved.get('game'), str):
                hints.append(saved['game'])
        except (OSError, ValueError):
            pass
    hints.extend(_registry_game_hints())
    hints.extend(_launcher_game_hints())
    games = {}
    for hint in hints:
        if not hint:
            continue
        try:
            for game in _games_at(hint):
                games[os.path.normcase(str(game))] = game
        except (OSError, ValueError):
            continue
    return sorted(games.values(), key=lambda p: str(p).casefold())


def resolve_game_directory(destination=None, configured=''):
    selected = str(configured or os.environ.get('EFF_RELOAD_GAME', '')).strip()
    if selected:
        games = _games_at(selected)
        if not games:
            raise ValueError('游戏目录无效，请在导出窗口重新选择包含 Endfield.exe 的安装目录：' + selected)
    else:
        if destination is not None:
            for parent in Path(destination).resolve().parents:
                if parent.name.lower() == 'plugin' and valid_game_directory(parent.parent):
                    return parent.parent
        games = discover_game_directories()
    if len(games) == 1:
        return games[0]
    if games:
        raise ValueError('发现多份终末地游戏目录，请在导出窗口选择本次使用的安装：' + '；'.join(map(str, games)))
    raise ValueError('未找到终末地游戏，请在导出窗口选择“游戏目录”；选择一次后会记住，Mod 可导出到任意文件夹')


def reload_context(destination, game_directory='', effect_update=False):
    destination = Path(destination).resolve()
    game = resolve_game_directory(destination, game_directory)
    baseline = destination / 'compiled.bin'
    cache = destination
    protocol = _game_protocol(game)
    if protocol is not None and protocol.get('compiledMod') != COMPILED_MOD:
        raise ValueError('当前 DLL 不支持独立 Mod 编译结构，请安装配套 DLL')
    candidates = [Path(__file__).parent / 'nativepack/eff_resource_pack.exe']
    compiler, declaration = _select_compiler(candidates, protocol)
    if effect_update and declaration.get('effectAuthorUpdate')!='effects-only/validated-character-inputs/1':
        raise ValueError('配套资源编译器不支持向角色包追加特效，请更新完整 Blender 插件')
    if declaration.get('compiledMod') != COMPILED_MOD:
        raise ValueError('插件缺少支持独立 Mod 编译结果的配套工具，请安装完整插件包')
    return compiler, baseline, cache, game


def prepare_static_sources(destination, source_package, context, previous=None):
    """Seed the descriptor and locate the index; the compiler does discovery."""
    # Full exports start with an empty staging directory. Seed the previous
    # descriptor so the compiler can validate its request hash and reuse it;
    # changed declarations still take its normal discovery path.
    descriptor = Path(destination) / 'source-inputs.bin'
    if previous is not None and not descriptor.is_file():
        prior = Path(previous) / 'source-inputs.bin'
        if prior.is_file():
            shutil.copyfile(prior, descriptor)
    configured = os.environ.get('EFF_SOURCE_INDEX')
    index = Path(configured).resolve() if configured else None
    source_package = Path(source_package).resolve()
    if index is None:
        manifest_path = source_package / 'source/manifest.json'
        if manifest_path.is_file():
            current = json.loads(manifest_path.read_text('utf-8-sig')).get('sourceIndex')
            if current:
                index = Path(current).resolve()
                if not index.is_file():
                    raise ValueError('重新解包记录的来源索引已不存在，请在 AnimeStudio 重新打开对应工作区：' + str(index))
    if index is None:
        for parent in (source_package, *source_package.parents):
            candidate = parent / 'index/endfield_assets.eidx'
            if candidate.is_file():
                index = candidate
                break
    # The compiler validates the current layered game identity against the
    # index header before reusing a descriptor; a full index read is needed
    # only when declarations or the installed resource catalog have changed.
    return index or source_package / 'index/endfield_assets.eidx'


def compile_reload(destination, context=None, previous=None, source_index=None):
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
    declarations=(destination/'mod.ini').read_text(encoding='utf-8-sig') if (destination/'mod.ini').is_file() else ''
    for prefix,field,expected in (
            ('MaterialAnimation','materialAnimation','sampled-material-property-clips/renderer-slot-block/le32/1'),
            ('EffectAttachment','effectAttachment','isolated-native-effect/mesh-owner+bone/le32/1'),
            ('EffectAttachment','effectTarget','source-prefab+renderer+mesh+bone-palette/strings/1')):
        if '['+prefix in declarations and (protocol.get('structures',{}).get(field)!=expected or consumer is not None and consumer.get('structures',{}).get(field)!=expected):
            raise ValueError('当前编译器或游戏 DLL 未声明 '+prefix+' 支持，请安装配套测试版本；上一 Mod 保留')
    if (destination/'mod.ini').is_file() and '[ShapeAnimation' in (destination/'mod.ini').read_text(encoding='utf-8-sig'):
        expected='sampled-shape-weight-clips/le32/1'
        if protocol.get('structures',{}).get('shapeAnimation')!=expected or consumer is not None and consumer.get('structures',{}).get('shapeAnimation')!=expected:
            raise ValueError('当前编译器或游戏 DLL 未声明形态动画支持，请安装配套版本；上一 Mod 保留')
        playback='shared-channel-groups/replace-conflicting-playback'
        if protocol.get('structures',{}).get('shapeAnimationPlayback')!=playback or consumer is not None and consumer.get('structures',{}).get('shapeAnimationPlayback')!=playback:
            raise ValueError('当前编译器或游戏 DLL 未声明多组共享形态频道支持，请更新配套文件；上一 Mod 保留')
    if source_index is not None:
        if protocol.get('sourcePreparation') != SOURCE_PREPARATION:
            raise ValueError('插件缺少支持进程内来源查询的编译器，请安装完整插件包')
        command.extend(['--source-index', str(Path(source_index).resolve())])
    revision = None
    if previous is not None:
        with publication_lock(previous):
            revision = published_revision(previous)
    if revision is not None:
        if protocol.get('compiledExportCarryover') != EXPORT_CARRYOVER:
            raise ValueError('编译器不支持继承已提交的 PFB 命中，不能覆盖旧 Mod；请更新 Blender 配套编译器')
        if revision[2] == 'blocks' and consumer is not None and consumer.get('compiledModStore') != 'EFF_MOD_BLOCKS/1':
            raise ValueError('当前 DLL 不支持该 Mod 的来源块存储，保留上一发布；需要配套 reader 迁移')
        command.extend(['--previous-mod', str(Path(previous).resolve())])
    result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace',
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), check=False)
    refreshed = None
    # A changed installation is a request to bring the author index forward,
    # not a reason to discard a valid author stage. Only the compiler's typed
    # stale-index result permits this one bounded retry; other failures remain.
    if (source_index is not None and result.returncode == 3 and
            'OFFLINE-SOURCE-INDEX-STALE' in result.stderr.splitlines()):
        refreshed = refresh_source_index(compiler, game, source_index)
        result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace',
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), check=False)
    if result.returncode:
        raise ValueError('离线资源构建失败，游戏继续保留上一完整候选：' + (result.stderr or result.stdout)[-1600:])
    if any(row.startswith('OFFLINE-SOURCE-LOOKUP-PENDING ') for row in result.stdout.splitlines()):
        raise ValueError('静态来源未准备完整，请重新生成该 Mod 的来源清单；旧候选未发布为新导出结果')
    artifact = destination / 'compiled.bin'
    if not artifact.is_file():
        raise ValueError('资源编译没有生成完整的 compiled.bin')
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
                offlinePreviousRevision=revision,
                offlineSourceIndexRefresh=refreshed,
                offlineCompilerCostsMs=costs)


def refresh_source_index(compiler, game, index):
    """Update only changed Bundles using this add-on's offline parser helper."""
    helper = Path(compiler).with_name('eff_source_index.exe')
    if not helper.is_file():
        raise ValueError('游戏资源已有更新，插件缺少自动刷新索引的配套工具，请安装完整 Blender 插件；上一 Mod 保留')
    options = dict(capture_output=True, text=True, encoding='utf-8', errors='replace',
                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), check=False)
    try:
        reply = subprocess.run([str(helper), '--protocol'], timeout=5, **options)
        declaration = json.loads(reply.stdout) if reply.returncode == 0 else {}
    except (OSError, subprocess.TimeoutExpired, ValueError) as error:
        raise ValueError('无法查询自动来源索引工具：' + str(error)) from error
    if (not isinstance(declaration, dict) or declaration.get('format') != 'EFF_SOURCE_INDEX_UPDATER' or declaration.get('version') != 1 or
            declaration.get('update') != 'bundle-content/incremental' or declaration.get('authorPackages') != 'unchanged'):
        raise ValueError('自动来源索引工具协议不兼容，请安装完整 Blender 插件；上一 Mod 保留')
    print('[EFF] 检测到游戏资源更新，正在增量刷新来源索引，完成后继续本次导出', flush=True)
    result = subprocess.run([str(helper), '--update', str(game), str(Path(index).resolve())], **options)
    if result.returncode:
        raise ValueError('游戏来源索引自动刷新失败，上一 Mod 保留：' + (result.stderr or result.stdout)[-1600:])
    try:
        rows = [line.removeprefix('SOURCE-INDEX-PREPARED ') for line in result.stdout.splitlines()
                if line.startswith('SOURCE-INDEX-PREPARED ')]
        receipt = json.loads(rows[-1])
        if Path(receipt['index']).resolve() != Path(index).resolve():
            raise ValueError('索引输出路径不一致')
        for key in ('parsed', 'reused', 'removed'):
            if type(receipt[key]) is not int or receipt[key] < 0:
                raise ValueError('索引统计无效')
    except (ValueError, KeyError, IndexError, TypeError) as error:
        raise ValueError('自动来源索引工具没有返回当前索引的完整凭据；上一 Mod 保留') from error
    print('[EFF] 来源索引刷新完成：解析 %(parsed)d，复用 %(reused)d，移除 %(removed)d；继续导出' % receipt, flush=True)
    return receipt
