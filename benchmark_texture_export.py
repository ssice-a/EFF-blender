"""Isolated Blender texture-export benchmark; never saves the input .blend.

Run with factory-startup Blender, then -- ADDON_DIR BLEND MOD SOURCE GAME.
All author outputs and compiler caches are owned temporary directories.
"""
import cProfile
import functools
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import pstats
import shutil
import sys
import tempfile
import time

import bpy


def hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def run(addon_dir, blend, production, source, game, compile_candidate=False,
        changed_pixels=False, compiler=None, report_path=None):
    addon_dir, blend, production, source, game = map(
        lambda p: Path(p).resolve(), (addon_dir, blend, production, source, game))
    sys.path.insert(0, str(addon_dir.parent))
    package = importlib.import_module(addon_dir.name)
    package.register()
    addon = package.eiem_blender_addon
    exporter = addon.native_export
    offline = importlib.import_module(addon_dir.name + '.eiem_offline_reload')
    bpy.ops.wm.open_mainfile(filepath=str(blend), load_ui=False, use_scripts=False)
    body = bpy.data.objects['MeshS_actor_lizhiyan_body_01_lod0_0']
    os.environ['EFF_RELOAD_GAME'] = str(game)
    modules, _ = exporter.pack_modules()
    sys.path.insert(0, str(addon_dir / 'vendor'))
    from PIL import Image
    real_clone = exporter._clone_staging_tree
    real_collect = exporter.collect_material
    real_compile = offline.compile_reload
    real_context = offline.reload_context
    real_run = offline.subprocess.run
    if compiler:
        compiler = Path(compiler).resolve()
        def context_with_compiler(destination):
            context = real_context(destination)
            return (compiler, *context[1:])
        offline.reload_context = context_with_compiler
    def logged_run(command, *args, **kwargs):
        result = real_run(command, *args, **kwargs)
        if len(command) > 2:
            for line in (result.stdout + result.stderr).splitlines():
                if line.startswith('OFFLINE-'):
                    print('EFF_EXPORT_BENCH_COMPILER ' + line, flush=True)
        return result
    offline.subprocess.run = logged_run
    reports = []
    with tempfile.TemporaryDirectory(prefix='eff-export-bench-', dir=production.parents[3].parent) as folder:
        root = Path(folder)
        # Freeze the author inputs once. A user may legitimately export into
        # production while this background benchmark is running.
        snapshot = root / 'source-author'
        shutil.copytree(production, snapshot)
        before = hashes(snapshot)
        assert hashes(production) == before, 'production changed while taking the input snapshot'
        destination = root / 'author' / 'LZY'
        png = root / 'body.png'
        with Image.open(bpy.path.abspath(body.data.materials[0]['eiem_texture._BaseMap'])) as img:
            image_size = img.size
        body.data.materials[0]['eiem_texture._BaseMap'] = str(png)
        cases = [('legacy', 0), ('optimized', 0), ('optimized', 0), ('legacy', 0)]
        if changed_pixels:
            cases = [('optimized', 0), ('optimized', 1), ('optimized', 2), ('optimized', 0)]
        expected_by_pixels = {}
        colors = [(255, 255, 255, 255), (0, 200, 200, 255), (60, 180, 30, 255)]
        for mode, pixels in cases:
            Image.new('RGBA', image_size, colors[pixels]).save(png)
            if destination.exists():
                shutil.rmtree(destination)
            shutil.copytree(snapshot, destination)
            times, restore = {}, []
            def instrument(owner, name):
                fn = getattr(owner, name)
                @functools.wraps(fn)
                def measured(*args, **kwargs):
                    start = time.perf_counter()
                    try:
                        return fn(*args, **kwargs)
                    finally:
                        times[name] = times.get(name, 0) + time.perf_counter() - start
                restore.append((owner, name, fn))
                setattr(owner, name, measured)
            if mode == 'legacy':
                exporter._clone_staging_tree = shutil.copytree
                def uncached(*args, **kwargs):
                    args = list(args)
                    if len(args) > 10:
                        args[10] = {}
                    else:
                        kwargs['texture_cache'] = {}
                    return real_collect(*args, **kwargs)
                exporter.collect_material = uncached
            if not compile_candidate:
                offline.compile_reload = lambda *args: {'offlineReloadPrepared': False}
            for owner, name in ((exporter, '_clone_staging_tree'),
                                (exporter, '_detach_staging_files'),
                                (exporter, 'collect_material'), (exporter, 'publish'),
                                (modules['ini_reader'], 'read_package'),
                                (modules['ini_package'], 'update_directory'),
                                (offline, 'compile_reload')):
                instrument(owner, name)
            profiler = cProfile.Profile()
            start = time.perf_counter()
            try:
                profiler.enable()
                stats = addon.export_package(destination, [body], [], [],
                    source_baseline=str(source), resource_scope='TEXTURES')
                profiler.disable()
                elapsed = time.perf_counter() - start
            finally:
                profiler.disable()
                for owner, name, fn in reversed(restore):
                    setattr(owner, name, fn)
                exporter._clone_staging_tree = real_clone
                exporter.collect_material = real_collect
                offline.compile_reload = real_compile
            current = hashes(destination)
            expected = expected_by_pixels.setdefault(pixels, current)
            assert current == expected, 'optimized author output differs'
            untouched = {name: digest for name, digest in before.items()
                         if not name.startswith('textures/')}
            assert all(current.get(name) == digest for name, digest in untouched.items()), 'non-texture author file changed'
            stream = io.StringIO()
            pstats.Stats(profiler, stream=stream).sort_stats('cumulative').print_stats(16)
            report = dict(mode=mode, pixels=pixels, compiler=str(compiler or 'installed'), seconds=round(elapsed, 4),
                          stages={k: round(v, 4) for k, v in times.items()}, stats=stats)
            if stats.get('offlineReloadPrepared'):
                startup = Path(stats['offlineReloadCache']) / 'generations' / stats['offlineReloadKey'] / 'startup'
                reused = [p.stat() for p in startup.iterdir() if p.is_file() and p.stat().st_nlink > 1]
                report['startupReusedFiles'] = len(reused)
                report['startupReusedBytes'] = sum(row.st_size for row in reused)
            reports.append(report)
            print('EFF_EXPORT_BENCH ' + json.dumps(report, ensure_ascii=False), flush=True)
            print(stream.getvalue(), flush=True)
    if hashes(production) != before:
        print('EFF_EXPORT_BENCH productionChangedDuringRun=true frozenSnapshotUsed=true', flush=True)
    offline.reload_context = real_context
    offline.subprocess.run = real_run
    if report_path:
        Path(report_path).write_text(json.dumps(reports, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print('EFF_EXPORT_BENCH_OK ' + json.dumps(reports, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    args = sys.argv[sys.argv.index('--') + 1:]
    compile_candidate = '--compile' in args
    changed_pixels = '--changed-pixels' in args
    args = [arg for arg in args if arg not in ('--compile', '--changed-pixels')]
    compiler = None
    if '--compiler' in args:
        index = args.index('--compiler')
        compiler = args[index + 1]
        del args[index:index + 2]
    report_path = None
    if '--report' in args:
        index = args.index('--report')
        report_path = args[index + 1]
        del args[index:index + 2]
    run(*args, compile_candidate=compile_candidate, changed_pixels=changed_pixels,
        compiler=compiler, report_path=report_path)
