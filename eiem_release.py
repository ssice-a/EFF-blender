"""Pure Python release lookup used by the Blender preferences UI."""

import json
import re
import hashlib
import errno
import http.client
import os
import shutil
import socket
import ssl
import stat
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


API_URL = "https://api.github.com/repos/ssice-a/EFF-blender/releases/latest"
RELEASE_PREFIX = "https://github.com/ssice-a/EFF-blender/releases/"
VERSION = re.compile(r"^[vV]?(\d+)\.(\d+)\.(\d+)$")
CONTINUOUS_API = 'https://api.github.com/repos/ssice-a/EFF-blender/releases/tags/continuous'
COMPARE_API = 'https://api.github.com/repos/ssice-a/EFF-blender/compare/'
DOWNLOAD_PREFIX = 'https://github.com/ssice-a/EFF-blender/releases/download/'
BUILD_FILE = 'eff-build.json'
FEED_FILE = 'eff-blender-update.json'


def parse_version(tag):
    match = VERSION.fullmatch(tag or "")
    if not match:
        raise ValueError("Release tag must be vMAJOR.MINOR.PATCH")
    return tuple(map(int, match.groups()))


def check_release(current, ignored_tag="", force=False, opener=urlopen, sleep=time.sleep):
    data = _request_json(API_URL, opener, timeout=5, stage='查询正式 Release', sleep=sleep)
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


def save_install_job(path, job):
    path = Path(path)
    job['updatedAt'] = datetime.now(timezone.utc).isoformat()
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, path)


def read_install_status(directory, job_path=None):
    """Recover one installation's latest task without touching its transaction.

    Called on registration or by the main-thread task monitor, never in draw.
    Other installations, malformed discoveries and linked stages are ignored.
    """
    target = Path(directory).resolve()

    def read(path):
        if (path.name != 'job.json' or path.parent.parent.resolve() != target.parent or
                not path.parent.name.startswith('.eff-update-') or
                linked(path.parent) or linked(path) or path.stat().st_size > 262144):
            raise ValueError('更新任务位置或大小不合法')
        data = json.loads(path.read_text('utf-8-sig'))
        if (not isinstance(data, dict) or data.get('format') != 'EFF_BLENDER_INSTALL/1' or
                not isinstance(data.get('target'), str) or Path(data['target']).resolve() != target or
                not re.fullmatch('[0-9a-f]{40}', str(data.get('revision', ''))) or
                data.get('status') not in ('prepared', 'waitingForBlenderExit', 'installed', 'failed')):
            raise ValueError('更新任务不属于当前安装或记录不完整')
        return data

    if job_path is not None:
        path = Path(job_path).absolute()
        try:
            data = read(path)
        except (OSError, ValueError, TypeError, RuntimeError) as error:
            # Do not expose an arbitrary path supplied by a malformed record.
            return dict(status='unknown', message='无法读取更新任务：' + str(error))
    else:
        candidates = []
        try:
            for path in target.parent.glob('.eff-update-*/job.json'):
                try:
                    data = read(path)
                    candidates.append((path.stat().st_mtime_ns, str(path), path, data))
                except (OSError, ValueError, TypeError, RuntimeError):
                    continue
        except OSError:
            return {}
        if not candidates:
            return {}
        _, _, path, data = max(candidates, key=lambda item: item[:2])
    status = data['status']
    message = {
        'prepared': '更新包已校验，保存工程并关闭全部 Blender 后安装',
        'waitingForBlenderExit': '安装助手正在等待所有 Blender 退出',
        'failed': '安装失败：' + str(data.get('error') or '任务未记录具体原因，请查看日志'),
        'installed': '更新已安装并校验：' + data['revision'][:8],
    }[status]
    if status == 'installed':
        try:
            if read_build(target).get('revision') != data['revision']:
                status, message = 'superseded', '历史更新已完成；当前插件已由后续安装替换'
            else:
                verify_package(target, data['revision'])
        except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
            status, message = 'unknown', '任务记录已完成，但当前插件校验失败：' + str(error)
    pids = data.get('waitingPids')
    updated_at = data.get('updatedAt')
    if not isinstance(updated_at, str) or not updated_at:
        try:
            updated_at = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
        except OSError:
            updated_at = ''
    return dict(status=status, message=message, job=str(path),
                log=str(path.parent / 'install.log'), revision=data['revision'],
                updatedAt=updated_at,
                waitingPids=[pid for pid in (pids if isinstance(pids, list) else [])
                             if type(pid) is int and pid > 0][:64])


class UpdateNetworkError(RuntimeError):
    """A failed update request, with its stage and safe retry outcome."""


def _network_failure(error):
    if isinstance(error, HTTPError):
        if error.code == 403:
            limited = (error.headers or {}).get('X-RateLimit-Remaining') == '0'
            return ('GitHub API 额度已用完，请等待额度恢复后重试' if limited else
                    'GitHub 拒绝访问（HTTP 403），请检查网络或稍后重试'), False
        if error.code == 429:
            return 'GitHub 请求过于频繁（HTTP 429），请稍后重试', True
        return ('服务器返回 HTTP ' + str(error.code), 500 <= error.code < 600)
    reason = error.reason if isinstance(error, URLError) else error
    if isinstance(reason, ssl.SSLError):
        return 'TLS／证书验证失败，请检查系统时间、证书或代理', False
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return '连接或读取超时，请检查网络或代理', True
    if isinstance(reason, socket.gaierror):
        return 'DNS 解析失败，请检查网络或代理', reason.errno in (socket.EAI_AGAIN, 11002)
    if isinstance(reason, (ConnectionError, http.client.IncompleteRead, http.client.RemoteDisconnected)):
        return '连接中断，未能完整读取更新数据', True
    if isinstance(reason, OSError):
        retryable = reason.errno in (errno.ECONNRESET, errno.ECONNABORTED, errno.ETIMEDOUT,
                                    errno.ENETUNREACH, errno.EHOSTUNREACH, 10054, 10060)
        return '网络连接失败：' + str(reason)[:100], retryable
    text = str(reason)
    # Some platform openers expose a transient resolver failure as text only.
    return '网络连接失败：' + text[:100], 'temporary' in text.lower() and 'dns' in text.lower()


def _network_request(action, stage, sleep):
    for attempt in range(3):
        try:
            return action()
        except (HTTPError, URLError, TimeoutError, ConnectionError, socket.gaierror,
                ssl.SSLError, http.client.IncompleteRead, http.client.RemoteDisconnected) as error:
            message, retryable = _network_failure(error)
            delay = attempt + 1
            if isinstance(error, HTTPError):
                try:
                    delay = max(delay, float((error.headers or {}).get('Retry-After', '0')))
                except ValueError:
                    # HTTP-date Retry-After values also require a later manual retry.
                    retryable = False
                if delay > 5:
                    retryable = False
            if not retryable or attempt == 2:
                raise UpdateNetworkError(stage + '失败：' + message +
                                         '（已尝试 %d 次）' % (attempt + 1)) from error
            sleep(delay)


def _request_json(url, opener, timeout=15, stage='读取更新信息', sleep=time.sleep):
    def request():
        with opener(Request(url, headers={'User-Agent': 'EFF-Blender-Update/1',
                                          'Accept': 'application/vnd.github+json'}), timeout=timeout) as response:
            return json.load(response)
    return _network_request(request, stage, sleep)


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


def check_repository(directory, current, ignored='', force=False, opener=urlopen, sleep=time.sleep):
    release = _request_json(CONTINUOUS_API, opener, stage='读取仓库发布目录', sleep=sleep)
    assets = [asset for asset in release.get('assets', ()) if asset.get('name') == FEED_FILE]
    if release.get('draft') or len(assets) != 1:
        raise ValueError('仓库更新包尚未准备完成')
    url = assets[0].get('browser_download_url')
    if url != DOWNLOAD_PREFIX + 'continuous/' + FEED_FILE:
        raise ValueError('仓库更新目录地址不匹配')
    feed = validate_feed(_request_json(url, opener, stage='读取仓库更新清单', sleep=sleep))
    installed = read_build(directory)
    revision = feed['revision']
    installed_revision = installed.get('revision')
    status, message = 'available', ''
    if installed_revision == revision:
        status = 'latest'
    elif tuple(feed['version']) < tuple(current):
        status, message = 'latest', '本机版本高于已发布的仓库更新，无需安装旧包'
    elif re.fullmatch('[0-9a-f]{40}', str(installed_revision or '')):
        # Different hashes do not imply a newer commit. In particular, a local
        # verified deployment may already be ahead of the continuous release.
        comparison = _request_json(COMPARE_API + installed_revision + '...' + revision,
                                   opener, stage='确认版本先后', sleep=sleep)
        if not isinstance(comparison, dict):
            comparison = {}
        relation = comparison.get('status')
        base = comparison.get('base_commit')
        if not isinstance(base, dict) or base.get('sha') != installed_revision:
            status, message = 'unknown', '无法确认更新包与本机的版本先后，已停止自动更新'
        elif relation == 'behind':
            status, message = 'latest', '本机已领先已发布的仓库更新，无需安装旧包'
        elif relation != 'ahead':
            status, message = 'unknown', '更新包与本机提交已分叉或来源不明，已停止自动更新'
    elif tuple(feed['version']) == tuple(current):
        status, message = 'unknown', '本机缺少构建来源，无法确认同版号更新的先后；请手动核对安装包'
    if status == 'available' and not force and ignored == revision:
        status = 'ignored'
    return dict(status=status, tag=revision, revision=revision, feed=feed,
                url='https://github.com/ssice-a/EFF-blender/commit/' + revision,
                label='仓库更新 ' + revision[:8], channel='MAIN', message=message)


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
                 'eiem_shared_resources.py', 'nativepack/author_source.py',
                 'nativepack/author_format3.py',
                 'nativepack/effect_native.py', 'nativepack/mesh.py',
                 'eiem_texture_compression.py',
                 'physics_presets/chen_soft_ribbon.json'):
        if name not in build['files']:
            raise ValueError('更新包缺少配套文件：' + name)
    obsolete = {'nativepack/compiled_store.py', 'nativepack/ini_package.py',
                'nativepack/ini_reader.py', 'nativepack/payload.py',
                'nativepack/resource_file.py', 'nativepack/runtime_controls.py',
                'nativepack/shape_animation.py', 'nativepack/texture_fields.py'}
    if obsolete.intersection(build['files']):
        raise ValueError('Blender Format 3 update package contains legacy Mod readers/writers')
    if any(name.endswith(('.exe', '.dll', '.pyd')) for name in build['files']):
        raise ValueError('Blender 更新包不能重复分发 EFF 共享原生工具')
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


def prepare_update(directory, feed, opener=urlopen, progress=None, sleep=time.sleep):
    target = check_install_target(directory)
    validate_feed(feed)
    stage = Path(tempfile.mkdtemp(prefix='.eff-update-', dir=target.parent))
    try:
        archive = stage / 'download.zip'
        def download():
            # A retry starts a fresh file; never append to a partial ZIP.
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
                if size != feed['bytes']:
                    raise http.client.IncompleteRead(b'', feed['bytes'] - size)
        _network_request(download, '下载更新包', sleep)
        if digest(archive) != feed['sha256']:
            raise ValueError('更新包大小或 SHA256 校验失败')
        candidate = extract_package(archive, stage / 'unpacked')
        verify_package(candidate, feed['revision'])
        job = dict(format='EFF_BLENDER_INSTALL/1', target=str(target), candidate=str(candidate),
                   revision=feed['revision'], oldRevision=read_build(target).get('revision'),
                   status='prepared')
        job_path = stage / 'job.json'
        save_install_job(job_path, job)
        for name in ('eiem_release.py', 'eiem_update_worker.py'):
            shutil.copy2(target / name, stage / name)
        return job_path
    except Exception:
        shutil.rmtree(stage)
        raise
