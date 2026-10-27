"""Build an immutable Blender add-on with Python sources only."""
import argparse
import ast
import json
import os
from pathlib import Path
import shutil
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import eiem_release as updates


def build(output, revision, name=None, framework_root=None):
    if len(revision) != 40 or any(c not in '0123456789abcdef' for c in revision):
        raise ValueError('Expected exact source commit')
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    package = output / 'package' / 'EFF_Blender'
    if package.exists():
        shutil.rmtree(package)
    package.mkdir(parents=True)
    framework = Path(framework_root).resolve() if framework_root else ROOT / 'framework'
    nativepack = framework / 'tools' / 'nativepack'
    if not (nativepack / 'compiled_store.py').is_file():
        raise FileNotFoundError('Missing pinned EFF nativepack Python sources')
    shutil.copytree(nativepack, package / 'nativepack',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc', 'test_*.py', '*_experiment.py',
                                                  'verify_*.py', '*.dll', '*.exe', '*.pyd'))
    for path in ROOT.glob('*.py'):
        if path.name == '__init__.py' or path.name.startswith('eiem_'):
            shutil.copy2(path, package / path.name)
    shutil.copytree(ROOT / 'physics_presets', package / 'physics_presets', dirs_exist_ok=True)
    for name_ in ('README.md', 'LICENSE', 'THIRD_PARTY_NOTICES'):
        shutil.copy2(ROOT / name_, package / name_)
    source = ast.parse((ROOT / '__init__.py').read_text('utf-8'))
    info = next(ast.literal_eval(node.value) for node in source.body if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == 'bl_info' for target in node.targets))
    manifest = dict(format='EFF_BLENDER_BUILD/1', revision=revision, version=list(info['version']),
                    files={p.relative_to(package).as_posix(): updates.digest(p)
                    for p in sorted(package.rglob('*')) if p.is_file() and p.name != updates.BUILD_FILE})
    (package / updates.BUILD_FILE).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    updates.verify_package(package, revision)
    destination = output / (name or 'EFF_Blender_main-' + revision + '.zip')
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as target:
        for path in sorted(package.rglob('*')):
            if path.is_file():
                target.write(path, 'EFF_Blender/' + path.relative_to(package).as_posix())
    feed = dict(format='EFF_BLENDER_UPDATE/1', revision=revision, version=list(info['version']),
                bytes=destination.stat().st_size, sha256=updates.digest(destination),
                url=updates.DOWNLOAD_PREFIX + 'continuous/EFF_Blender_main-' + revision + '.zip')
    (output / updates.FEED_FILE).write_text(json.dumps(feed, indent=2), encoding='utf-8')
    print('PASS Python add-on:', destination, 'files:', len(manifest['files']))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--name')
    parser.add_argument('--framework-root', default=os.environ.get('EFF_FRAMEWORK_ROOT'))
    args = parser.parse_args()
    build(args.output, args.revision, args.name, args.framework_root)
