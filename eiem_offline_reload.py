"""Publish game-ready reload inputs with the same native compiler as the DLL.

No scene/selection access, no second geometry implementation. Native candidates
live outside the author directory; failed compilation leaves current untouched.
"""
from pathlib import Path
import json
import os
import subprocess


def reload_context(destination):
    destination = Path(destination).resolve()
    configured = os.environ.get('EFF_RELOAD_GAME')
    target = Path(__file__).parent / 'nativepack/offline-target.json'
    if not configured and target.is_file():
        data = json.loads(target.read_text('utf-8-sig'))
        if data.get('format') != 'EFF_OFFLINE_EXPORT_TARGET' or data.get('version') != 1:
            raise ValueError('离线导出目标配置版本不兼容')
        configured = data['game']
    game = Path(configured).resolve() if configured else None
    # A normal deployment export to <game>/plugin/mods/<Mod> needs no setup.
    if game is None:
        for parent in destination.parents:
            if parent.name.lower() == 'plugin' and (parent / 'resource-reload-baseline/baseline.tsv').is_file():
                game = parent.parent
                break
    if game is None:
        return None
    baseline = game / 'plugin/resource-reload-baseline'
    assembly = game / 'GameAssembly.dll'
    mods = (game / 'plugin/mods').resolve()
    cache = game / 'plugin/resource-reload-cache' if destination.parent == mods else destination.with_name(destination.name + '.reload')
    candidates = [game / 'plugin/tools/eff_resource_pack.exe',
                  Path(__file__).parent / 'nativepack/eff_resource_pack.exe',
                  Path(__file__).resolve().parents[2] / 'bin/resource-pack/eff_resource_pack.exe']
    compiler = next((p for p in candidates if p.is_file()), None)
    if compiler is None or not assembly.is_file() or not (baseline / 'baseline.tsv').is_file():
        raise ValueError('完整离线热重载需要同版本 eff_resource_pack.exe、游戏来源基线和 GameAssembly.dll')
    return compiler, baseline, cache, assembly


def compile_reload(destination, context=None):
    context = context if context is not None else reload_context(destination)
    if context is None:
        return dict(offlineReloadPrepared=False, offlineReloadReason='workspace-author-export')
    compiler, baseline, cache, assembly = context
    command = [str(compiler), str(Path(destination).resolve()), str(baseline), str(cache), str(assembly)]
    result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace',
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), check=False)
    if result.returncode:
        raise ValueError('离线资源构建失败，游戏继续保留上一完整候选：' + (result.stderr or result.stdout)[-1600:])
    rows = (cache / 'current.tsv').read_text('utf-8-sig').splitlines()
    if len(rows) != 2 or rows[0] != 'EFF_RESOURCE_RELOAD_CURRENT\t1':
        raise ValueError('离线构建未发布完整版本清单')
    key, digest = rows[1].split('\t')
    return dict(offlineReloadPrepared=True, offlineReloadKey=key, offlineReloadManifestHash=digest,
                offlineReloadCache=str(cache), offlineCompiler='EFF_NATIVE_PACK_V35_1')
