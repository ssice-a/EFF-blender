"""Pure Python release lookup used by the Blender preferences UI."""

import json
import re
import hashlib
import os
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from urllib.request import Request, urlopen


API_URL = "https://api.github.com/repos/ssice-a/EFF-blender/releases/latest"
RELEASE_PREFIX = "https://github.com/ssice-a/EFF-blender/releases/"
VERSION = re.compile(r"^[vV]?(\d+)\.(\d+)\.(\d+)$")
CONTINUOUS_API = 'https://api.github.com/repos/ssice-a/EFF-blender/releases/tags/continuous'
DOWNLOAD_PREFIX = 'https://github.com/ssice-a/EFF-blender/releases/download/'
BUILD_FILE = 'eff-build.json'
FEED_FILE = 'eff-blender-update.json'


def parse_version(tag):
    match = VERSION.fullmatch(tag or "")
    if not match:
        raise ValueError("Release tag must be vMAJOR.MINOR.PATCH")
    return tuple(map(int, match.groups()))


def check_release(current, ignored_tag="", force=False, opener=urlopen):
    request = Request(API_URL, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "EFF-Blender-UpdateCheck/1.0",
    })
    with opener(request, timeout=5) as response:
        data = json.load(response)
    tag, url = data.get("tag_name"), data.get("html_url")
    latest = parse_version(tag)
    if not isinstance(url, str) or not url.startswith(RELEASE_PREFIX):
        raise ValueError("Invalid release URL")
    if latest <= tuple(current):
        return {"status": "latest", "tag": tag, "url": url}
    if not force and tag == ignored_tag:
        return {"status": "ignored", "tag": tag, "url": url}
    return {"status": "available", "tag": tag, "url": url}


def read_build(directory):
    path = Path(directory) / BUILD_FILE
    if not path.is_file():
        return {}
    data = json.loads(path.read_text('utf-8-sig'))
    if data.get('format') != 'EFF_BLENDER_BUILD/1':
        raise ValueError('不支持的插件构建记录')
    return data


def _request_json(url, opener, timeout=15):
    with opener(Request(url, headers={'User-Agent': 'EFF-Blender-Update/1',
                                      'Accept': 'application/vnd.github+json'}), timeout=timeout) as response:
        return json.load(response)


def validate_feed(data):
    if (data.get('format') != 'EFF_BLENDER_UPDATE/1'
            or not re.fullmatch('[0-9a-f]{40}', str(data.get('revision', '')))
            or not re.fullmatch('[0-9a-f]{64}', str(data.get('sha256', '')))
            or type(data.get('bytes')) is not int or data['bytes'] <= 0
            or data.get('url') != DOWNLOAD_PREFIX + 'continuous/EFF_Blender_main-' + data['revision'] + '.zip'
            or not isinstance(data.get('version'), list) or len(data['version']) != 3
            or any(type(n) is not int or n < 0 for n in data['version'])):
        raise ValueError('仓库更新信息不完整或下载地址不匹配')
    return data


def check_repository(directory, current, ignored='', force=False, opener=urlopen):
    release = _request_json(CONTINUOUS_API, opener)
    assets = [asset for asset in release.get('assets', ()) if asset.get('name') == FEED_FILE]
    if release.get('draft') or len(assets) != 1:
        raise ValueError('仓库更新包尚未准备完成')
    url = assets[0].get('browser_download_url')
    if url != DOWNLOAD_PREFIX + 'continuous/' + FEED_FILE:
        raise ValueError('仓库更新目录地址不匹配')
    feed = validate_feed(_request_json(url, opener))
    installed = read_build(directory)
    revision = feed['revision']
    # No version bump is required for a Python-only push. A lower semantic
    # version never replaces a newer stable install; equal versions use SHA.
    same = installed.get('revision') == revision or tuple(feed['version']) < tuple(current)
    status = 'latest' if same else 'ignored' if not force and ignored == revision else 'available'
    return dict(status=status, tag=revision, revision=revision, feed=feed,
                url='https://github.com/ssice-a/EFF-blender/commit/' + revision,
                label='仓库更新 ' + revision[:8], channel='MAIN')


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def linked(path):
    path = Path(path)
    return path.is_symlink() or bool(path.stat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT) if os.name == 'nt' else path.is_symlink()


def check_install_target(directory):
    directory = Path(directory).absolute()
    if (not directory.is_dir() or directory.parent == directory
            or not (directory / '__init__.py').is_file()
            or not (directory / 'eiem_blender_addon.py').is_file()):
        raise ValueError('不是可更新的 EFF 插件安装目录')
    if linked(directory) or (directory / '.git').exists():
        raise ValueError('当前是源码链接／Git 开发安装，请通过 Git 更新；普通 ZIP 安装支持一键更新')
    return directory


def verify_package(directory, revision=None):
    directory = Path(directory)
    build = read_build(directory)
    if (not re.fullmatch('[0-9a-f]{40}', str(build.get('revision', '')))
            or not isinstance(build.get('files'), dict) or not build['files']
            or revision is not None and build['revision'] != revision):
        raise ValueError('更新包构建身份不匹配')
    for name in ('__init__.py', 'eiem_blender_addon.py', 'eiem_release.py', 'eiem_update_worker.py',
                 'nativepack/eff_author_core.dll', 'nativepack/eff_resource_pack.exe',
                 'nativepack/eff_source_index.exe', 'nativepack/package-protocol.json',
                 'nativepack/compiled_store.py', 'vendor/PIL/__init__.py',
                 'physics_presets/chen_soft_ribbon.json'):
        if name not in build['files']:
            raise ValueError('更新包缺少配套文件：' + name)
    for name, expected in build['files'].items():
        relative = PurePosixPath(name)
        if relative.is_absolute() or '..' in relative.parts or ':' in name or '\\' in name:
            raise ValueError('更新包文件越界')
        path = directory.joinpath(*relative.parts)
        if not path.is_file() or digest(path) != expected:
            raise ValueError('更新包文件校验失败：' + name)
    return build


def extract_package(archive, destination):
    destination = Path(destination)
    with zipfile.ZipFile(archive) as source:
        names = set()
        for member in source.infolist():
            name = member.orig_filename
            parts = PurePosixPath(name).parts
            if (not parts or parts[0] != 'EFF_Blender' or '..' in parts or ':' in name or '\\' in name
                    or any(part.endswith((' ', '.')) for part in parts)
                    or PurePosixPath(name).is_absolute() or name.casefold() in names
                    or stat.S_ISLNK(member.external_attr >> 16)):
                raise ValueError('更新包包含非法文件路径')
            names.add(name.casefold())
            source.extract(member, destination)
    return destination / 'EFF_Blender'


def prepare_update(directory, feed, opener=urlopen, progress=None):
    target = check_install_target(directory)
    validate_feed(feed)
    stage = Path(tempfile.mkdtemp(prefix='.eff-update-', dir=target.parent))
    try:
        archive = stage / 'download.zip'
        with opener(Request(feed['url'], headers={'User-Agent': 'EFF-Blender-Update/1'}), timeout=60) as response, archive.open('wb') as output:
            size = 0
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                size += len(block)
                if size > feed['bytes']:
                    raise ValueError('更新包大小超过发布记录')
                output.write(block)
                if progress:
                    progress(size, feed['bytes'])
        if size != feed['bytes'] or digest(archive) != feed['sha256']:
            raise ValueError('更新包大小或 SHA256 校验失败')
        candidate = extract_package(archive, stage / 'unpacked')
        verify_package(candidate, feed['revision'])
        job = dict(format='EFF_BLENDER_INSTALL/1', target=str(target), candidate=str(candidate),
                   revision=feed['revision'], oldRevision=read_build(target).get('revision'),
                   status='prepared')
        job_path = stage / 'job.json'
        job_path.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding='utf-8')
        for name in ('eiem_release.py', 'eiem_update_worker.py'):
            shutil.copy2(target / name, stage / name)
        return job_path
    except Exception:
        shutil.rmtree(stage)
        raise
