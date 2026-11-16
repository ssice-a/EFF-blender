"""Validated, content-addressed effect builds shared by both export routes."""
import hashlib
import json
import copy
from pathlib import Path
import subprocess
import tempfile

if __package__:
    from .nativepack.author_source import source_read_lock
    from .nativepack.hashing import sha256_bytes
    from .eiem_effect_document import dumps, require, safe_path
else:
    from nativepack.author_source import source_read_lock
    from nativepack.hashing import sha256_bytes
    from eiem_effect_document import dumps, require, safe_path


def digest(data):
    return sha256_bytes(data).hex().upper()


def compiler_path(explicit='', required=True):
    """One tool selection for creation, effects and author mount animations."""
    if __package__:from . import eiem_shared_resources as shared
    else:import eiem_shared_resources as shared
    selected = Path(explicit).expanduser().resolve() if explicit else shared.anime_studio()
    if selected is None:
        # Source checkouts may run without a local installation. Installed
        # add-ons never search the user's disks for development executables.
        project = Path(__file__).resolve().parent.parent/'AnimeStudio/AnimeStudio.GUI/bin'
        selected = next((project/config/'net9.0-windows/AnimeStudio.GUI.exe'
                         for config in ('Release','Debug')
                         if (project/config/'net9.0-windows/AnimeStudio.GUI.exe').is_file()), None)
    if required:require(selected is not None and selected.is_file(), '请指定已部署的配套 AnimeStudio')
    return selected


def inputs_signature(payload, compiler):
    source = Path(payload['source']).resolve()
    with source_read_lock(source):
        manifest = safe_path(source, 'source/manifest.json').read_bytes()
        effect = safe_path(source, 'effect.json').read_bytes()
        require(digest(manifest) == payload['sourceManifestSha256'].upper() and
                digest(effect) == payload['sourceEffectSha256'].upper(),
                '来源已改变，请重新导入特效；上一 Mod 保留')
        baseline = json.loads(manifest.decode('utf-8-sig'))
        # Check actual bytes, including external texture/mesh streams. A cache
        # hit must not hide corruption or replacements preserving timestamps.
        def snapshots(node):
            if isinstance(node, dict):
                if isinstance(node.get('snapshot'), dict):
                    yield node['snapshot']
                for key, value in node.items():
                    if key != 'snapshot':
                        yield from snapshots(value)
            elif isinstance(node, list):
                for value in node:
                    yield from snapshots(value)
        for row in snapshots(baseline):
            data = safe_path(source, row['file']).read_bytes()
            require(len(data) == row['size'] and digest(data) == row['sha256'].upper(),
                    '特效来源快照损坏：' + row['file'])
    compiler = Path(compiler).resolve()
    require(compiler.is_file(), '请指定配套 AnimeStudio')
    binaries = {compiler}
    # The portable launchers execute bin/AnimeStudio.GUI.dll. Updating that
    # implementation must invalidate builds even when the launcher is identical.
    for folder in (compiler.parent, compiler.parent / 'bin'):
        for pattern in ('*.dll', '*.deps.json', '*.runtimeconfig.json'):
            binaries.update(folder.glob(pattern))
    inputs = [(str(path), digest(path.read_bytes())) for path in sorted(binaries)]
    return digest(dumps(inputs).encode('utf-8'))


class Build:
    def __init__(self, payload, compiler, cache, identity, read_native, *, compiled_payload=None, resources=None):
        self.payload = compiled_payload if compiled_payload is not None else payload
        self.resources = resources or dict(materials={}, textures=[], meshes=[])
        self.author = dumps(payload).encode('utf-8')
        self.compiler = Path(compiler).resolve()
        self.inputs = inputs_signature(payload, self.compiler)
        self.portable_payload = copy.deepcopy(payload)
        self.key = digest(self.author + dumps(self.payload).encode('utf-8') + self.inputs.encode('ascii'))
        slot = hashlib.sha256((payload['source'] + '\0' + identity).encode('utf-8')).hexdigest()
        self.output = Path(cache).resolve() / slot
        self.read_native = read_native
        self.process = self.job = self.log = None
        self.reused = False

    def cached(self):
        try:
            receipt = json.loads((self.output/'eiem-request.json').read_text('utf-8'))
            if receipt.get('key') != self.key or receipt.get('runtime') != digest((self.output/'runtime.bin').read_bytes()):
                return False
            self.read_native(self.output)
            self.reused = True
            return True
        except (OSError, ValueError, KeyError):
            return False

    def start(self):
        self.output.parent.mkdir(parents=True, exist_ok=True)
        self.job = tempfile.TemporaryDirectory(prefix='EFF-effect-export-')
        folder = Path(self.job.name)
        compiled = dict(self.payload)
        (folder/'edits.json').write_text(dumps(compiled), encoding='utf-8')
        self.log = (folder/'build.log').open('w', encoding='utf-8')
        self.process = subprocess.Popen([str(self.compiler), '--build-eff-effects',
            self.payload['source'], str(folder/'edits.json'), str(self.output)],
            stdout=self.log, stderr=self.log,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))

    def finish(self, validate_inputs=True):
        require(self.process and self.process.poll() is not None, '特效构建尚未完成')
        self.log.flush()
        require(self.process.returncode == 0,
                (Path(self.job.name)/'build.log').read_text('utf-8', errors='replace')[-1600:] or '原生特效构建失败')
        if validate_inputs:
            require(inputs_signature(self.payload, self.compiler) == self.inputs,
                    '构建期间来源或编译器改变，请重试；上一 Mod 保留')
        self.read_native(self.output)
        receipt = dict(key=self.key, runtime=digest((self.output/'runtime.bin').read_bytes()))
        temporary = self.output/'eiem-request.json.tmp'
        temporary.write_text(dumps(receipt), encoding='utf-8')
        temporary.replace(self.output/'eiem-request.json')

    def run(self):
        try:
            if not self.cached():
                self.start()
                self.process.wait()
                self.finish()
            return self.output
        finally:
            self.close()

    def close(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            self.process.wait()
        if self.log:
            self.log.close()
            self.log = None
        if self.job:
            self.job.cleanup()
            self.job = None
