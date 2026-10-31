"""Shared EFF resource locations and native-tool cache."""

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import tempfile
import time
from contextlib import contextmanager
from urllib.parse import urlparse
from urllib.request import Request, urlopen
import zipfile


SETTINGS_FORMAT = 'EFF_SHARED_RESOURCES/1'
TOOLS_FORMAT = 'EFF_SHARED_NATIVE_TOOLS/2'
TOOLS_API = 'https://api.github.com/repos/ssice-a/EFF/releases/latest'
TOOLS_ASSET = 'eff-native-tools.json'
TOOLS_INSTALL_FORMAT = 'EFF_SHARED_NATIVE_TOOLS_INSTALL/2'
MAX_TOOLS_BYTES = 512 * 1024 * 1024
PIL_REQUIRED_FILES = (
    'PIL/__init__.py',
    'PIL/_imaging.cp311-win_amd64.pyd',
    'PIL/_imaging.cp313-win_amd64.pyd',
)


def metadata_root():
    local = os.environ.get('LOCALAPPDATA')
    if not local:
        home = os.environ.get('USERPROFILE') or os.environ.get('HOME')
        local = str(Path(home) / 'AppData/Local') if home else str(Path(tempfile.gettempdir()) / 'EFF' / 'LocalAppData')
    return Path(local) / 'EFF' / 'Resources'


def settings_path():
    return metadata_root() / 'settings.json'


def _read_settings():
    try:
        value = json.loads(settings_path().read_text(encoding='utf-8-sig'))
        return value if isinstance(value, dict) and value.get('format') == SETTINGS_FORMAT else {}
    except (OSError, ValueError, TypeError):
        return {}


def _write_settings(value):
    target = settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=target.name + '.', suffix='.tmp', dir=target.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def resource_root():
    configured = os.environ.get('EFF_RESOURCE_ROOT')
    if configured:
        return Path(configured).expanduser().resolve()
    value = _read_settings().get('resourceRoot')
    return Path(value).expanduser().resolve() if isinstance(value, str) and value else None


def set_resource_root(path):
    path = Path(path).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    value = _read_settings()
    value.update(format=SETTINGS_FORMAT, resourceRoot=str(path))
    _write_settings(value)
    return path


def _read_json(url):
    request = Request(url, headers={
        'Accept': 'application/vnd.github+json',
        'User-Agent': 'EFF-Shared-Tools/1',
    })
    with urlopen(request, timeout=20) as response:
        return json.loads(response.read(2 * 1024 * 1024 + 1).decode('utf-8-sig'))


def _validate_tools_manifest(value):
    if not isinstance(value, dict) or value.get('format') != TOOLS_FORMAT:
        raise ValueError('EFF Release 缺少受支持的共享工具清单')
    url = value.get('url')
    parsed = urlparse(url or '')
    if parsed.scheme != 'https' or parsed.hostname != 'github.com':
        raise ValueError('共享工具包必须来自 EFF GitHub Release')
    parts = parsed.path.strip('/').split('/')
    if len(parts) != 6 or parts[:4] != ['ssice-a', 'EFF', 'releases', 'download']:
        raise ValueError('EFF 共享工具包地址无效')
    if not isinstance(value.get('bytes'), int) or not 0 < value['bytes'] <= MAX_TOOLS_BYTES:
        raise ValueError('EFF 共享工具包大小无效')
    digest = str(value.get('sha256', '')).lower()
    if len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
        raise ValueError('EFF 共享工具包 SHA256 无效')
    expected = {
        'authorAssets': 'nativepack/eff_author_assets.dll',
        'authorCore': 'nativepack/eff_author_core.dll',
        'textureCompressor': 'nativepack/texconv.exe',
        'archiveRoot': 'EFF_Native_Tools',
    }
    if any(value.get(key) != item for key, item in expected.items()):
        raise ValueError('EFF 共享工具包清单中的文件布局不受支持')
    return value


def _latest_tools_manifest():
    explicit = os.environ.get('EFF_NATIVE_TOOLS_MANIFEST')
    if explicit:
        parsed = urlparse(explicit)
        if parsed.scheme in ('http', 'https'):
            return _validate_tools_manifest(_read_json(explicit))
        return _validate_tools_manifest(json.loads(Path(explicit).read_text(encoding='utf-8-sig')))
    release = _read_json(TOOLS_API)
    assets = [item for item in release.get('assets', ()) if item.get('name') == TOOLS_ASSET]
    if release.get('draft') or len(assets) != 1:
        raise ValueError('EFF 最新 Release 尚未提供共享工具清单')
    url = assets[0].get('browser_download_url', '')
    if urlparse(url).hostname != 'github.com' or not url.endswith('/' + TOOLS_ASSET):
        raise ValueError('EFF 共享工具清单地址无效')
    manifest = _validate_tools_manifest(_read_json(url))
    if manifest.get('framework') != release.get('tag_name'):
        raise ValueError('EFF 共享工具清单与最新 Release 标签不一致')
    return manifest


def _digest(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _extract_tools(archive, destination):
    required = {
        'nativepack/eff_author_assets.dll',
        'nativepack/eff_author_core.dll',
        'nativepack/texconv.exe',
        'nativepack/THIRD_PARTY_NOTICES',
        *('vendor/' + name for name in PIL_REQUIRED_FILES),
    }
    found = set()
    with zipfile.ZipFile(archive) as package:
        damaged = package.testzip()
        if damaged:
            raise ValueError('EFF 共享工具包 ZIP 损坏：' + damaged)
        for member in package.infolist():
            raw_name = member.filename
            relative = PurePosixPath(raw_name)
            if (relative.is_absolute() or '..' in relative.parts or ':' in raw_name or '\\' in raw_name
                    or not relative.parts or relative.parts[0] != 'EFF_Native_Tools'):
                raise ValueError('EFF 共享工具包包含非法路径')
            parts = relative.parts[1:]
            if not parts or parts[0] not in ('nativepack', 'vendor'):
                continue
            if member.is_dir():
                continue
            relative_name = '/'.join(parts)
            if relative_name not in required:
                continue
            target = destination.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with package.open(member) as source, target.open('wb') as output:
                shutil.copyfileobj(source, output, 1024 * 1024)
            found.add(relative_name)
    missing = required - found
    if missing:
        raise ValueError('EFF 共享工具包缺少文件：' + ', '.join(sorted(missing)))


def _installed_tools(root):
    try:
        receipt = json.loads((root / 'tools' / 'current.json').read_text(encoding='utf-8-sig'))
        if receipt.get('format') != TOOLS_INSTALL_FORMAT:
            return None
        directory = (root / 'tools' / receipt['sha256']).resolve()
        if root.resolve() not in directory.parents:
            return None
        required = ('eff_author_assets.dll', 'eff_author_core.dll', 'texconv.exe')
        nativepack = directory / 'nativepack'
        vendor = directory / 'vendor'
        if (all((nativepack / name).is_file() for name in required)
                and all((vendor / name).is_file() for name in PIL_REQUIRED_FILES)):
            return directory
    except (OSError, KeyError, ValueError, TypeError):
        return None
    return None


def find_native_tools():
    """Find the selected shared cache without downloading or scanning disks."""
    override = os.environ.get('EFF_NATIVE_TOOLS_DIR')
    if override:
        path = Path(override).expanduser().resolve()
        if all((path / 'nativepack' / name).is_file()
               for name in ('eff_author_assets.dll', 'eff_author_core.dll', 'texconv.exe')):
            return path
        return None
    root = resource_root()
    return _installed_tools(root) if root is not None else None


@contextmanager
def _native_tools_install_lock(path):
    """Use the same exclusive Windows file-share lock as the .NET clients."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name != 'nt':
        import fcntl
        with path.open('a+b') as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        return

    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    create_file = kernel.CreateFileW
    create_file.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                            wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create_file.restype = wintypes.HANDLE
    close_handle = kernel.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    invalid = ctypes.c_void_p(-1).value
    deadline = time.monotonic() + 900
    while True:
        handle = create_file(str(path), 0xC0000000, 0, None, 4, 0x80, None)
        if handle != invalid:
            break
        error = ctypes.get_last_error()
        if error not in (32, 33) or time.monotonic() >= deadline:
            raise OSError(error, 'Unable to acquire EFF shared-tool install lock', str(path))
        time.sleep(0.1)
    try:
        yield
    finally:
        close_handle(handle)


def ensure_native_tools():
    override = os.environ.get('EFF_NATIVE_TOOLS_DIR')
    if override:
        path = Path(override).expanduser().resolve()
        if all((path / 'nativepack' / name).is_file()
               for name in ('eff_author_assets.dll', 'eff_author_core.dll', 'texconv.exe')):
            return path
        raise ValueError('EFF_NATIVE_TOOLS_DIR lacks the Format 3 author tools')
    root = resource_root()
    if root is None:
        raise ValueError('请先在 EFF 三端中的任一应用设置工具存储目录。')
    tools = root / 'tools'
    tools.mkdir(parents=True, exist_ok=True)
    with _native_tools_install_lock(tools / 'install.lock'):
        return _install_native_tools(root)


def _install_native_tools(root):
    installed = _installed_tools(root)
    tools = root / 'tools'
    try:
        manifest = _latest_tools_manifest()
    except (OSError, ValueError):
        if installed is not None:
            return installed
        raise

    if installed is not None:
        try:
            receipt = json.loads((tools / 'current.json').read_text(encoding='utf-8-sig'))
            if str(receipt.get('sha256', '')).casefold() == str(manifest['sha256']).casefold():
                return installed
        except (OSError, KeyError, ValueError, TypeError):
            pass

    staging = Path(tempfile.mkdtemp(prefix='.eff-native-tools-', dir=tools))
    archive = staging / 'native-tools.zip'
    destination = tools / manifest['sha256']
    try:
        request = Request(manifest['url'], headers={'User-Agent': 'EFF-Shared-Tools/1'})
        size = 0
        digest = hashlib.sha256()
        with urlopen(request, timeout=120) as response, archive.open('wb') as output:
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                size += len(block)
                if size > manifest['bytes']:
                    raise ValueError('EFF 共享工具包超过发布清单大小')
                digest.update(block)
                output.write(block)
        if size != manifest['bytes'] or digest.hexdigest() != manifest['sha256'].lower():
            raise ValueError('EFF 共享工具包大小或 SHA256 校验失败')
        payload = staging / 'payload'
        payload.mkdir()
        try:
            _extract_tools(archive, payload)
        except ValueError as error:
            if (installed is not None
                    and any(name in str(error) for name in PIL_REQUIRED_FILES[1:])):
                return installed
            raise
        if destination.exists():
            required = ('eff_author_assets.dll', 'eff_author_core.dll', 'texconv.exe', 'THIRD_PARTY_NOTICES')
            nativepack = destination / 'nativepack'
            vendor = destination / 'vendor'
            if (not all((nativepack / name).is_file() for name in required)
                    or not all((vendor / name).is_file() for name in PIL_REQUIRED_FILES)):
                shutil.rmtree(destination)
                os.replace(payload, destination)
        else:
            os.replace(payload, destination)
        receipt = {
            'format': TOOLS_INSTALL_FORMAT,
            'sha256': manifest['sha256'].lower(),
            'release': manifest.get('release', ''),
        }
        temporary = tools / ('current.' + next(tempfile._get_candidate_names()) + '.tmp')
        temporary.write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
        os.replace(temporary, tools / 'current.json')
        return destination
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def native_tool_file(name):
    if name not in {'eff_author_assets.dll', 'eff_author_core.dll', 'texconv.exe'}:
        raise ValueError('Blender requested an unsupported native tool: ' + str(name))
    tools = find_native_tools()
    if tools is None:
        root = resource_root()
        if root is None:
            raise ValueError('请在 EFF 面板选择共享工具存储目录。')
        raise ValueError('EFF 共享作者工具尚未下载，请在 EFF 面板点击“检查并准备工具”。')
    path = tools / 'nativepack' / name
    if not path.is_file():
        raise ValueError('共享工具包缺少 ' + name)
    add_vendor_path(tools)
    return path


def add_vendor_path(tools=None):
    try:
        tools = tools or (_installed_tools(resource_root()) if resource_root() is not None else None)
        vendor = tools / 'vendor' if tools is not None else None
        if vendor is not None and vendor.is_dir() and str(vendor) not in os.sys.path:
            os.sys.path.insert(0, str(vendor))
    except (OSError, ValueError, TypeError):
        pass
