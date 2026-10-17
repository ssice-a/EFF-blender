"""Build an immutable complete add-on from pushed Python + a pinned native base."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.request import Request, urlopen
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import eiem_release as updates
import eiem_offline_reload as offline


def build(output, revision, base_archive=None, name=None):
    if len(revision) != 40 or any(c not in '0123456789abcdef' for c in revision):
        raise ValueError('Expected exact source commit')
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    base = json.loads((ROOT / 'native-base.json').read_text())
    archive = Path(base_archive) if base_archive else output / 'native-base.zip'
    if not archive.exists():
        with urlopen(Request(base['url'], headers={'User-Agent': 'EFF-Blender-Build/1'}), timeout=60) as source, archive.open('wb') as target:
            shutil.copyfileobj(source, target)
    if archive.stat().st_size != base['bytes'] or updates.digest(archive) != base['sha256']:
        raise ValueError('Pinned native base digest differs')
    package = updates.extract_package(archive, output / 'package')
    # Delete only old packaged top-level Python and docs in this new staging
    # directory. The current author's installed add-on is never a build input.
    for path in package.glob('*.py'):
        if path.name == '__init__.py' or path.name.startswith('eiem_'):
            path.unlink()
    if (package / 'docs').exists():
        shutil.rmtree(package / 'docs')
    for path in ROOT.glob('*.py'):
        if path.name == '__init__.py' or path.name.startswith('eiem_'):
            shutil.copy2(path, package / path.name)
    shutil.copytree(ROOT / 'docs', package / 'docs')
    for name_ in ('README.md', 'LICENSE', 'THIRD_PARTY_NOTICES'):
        shutil.copy2(ROOT / name_, package / name_)
    protocol = offline._compiler_protocol(package / 'nativepack/eff_resource_pack.exe')
    declared = json.loads((package / 'nativepack/package-protocol.json').read_text('utf-8-sig'))
    if protocol != declared or protocol['abi'] != 35:
        raise ValueError('Native base compiler protocol differs')
    source = ast.parse((ROOT / '__init__.py').read_text('utf-8'))
    info = next(ast.literal_eval(node.value) for node in source.body if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == 'bl_info' for target in node.targets))
    manifest = dict(format='EFF_BLENDER_BUILD/1', revision=revision, version=list(info['version']),
                    nativeBase=base, files={p.relative_to(package).as_posix(): updates.digest(p)
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
    print('PASS complete add-on:', destination, 'native base:', base['sha256'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--base-archive')
    parser.add_argument('--name')
    args = parser.parse_args()
    build(args.output, args.revision, args.base_archive, args.name)
