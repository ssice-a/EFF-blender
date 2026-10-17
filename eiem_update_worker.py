"""Install a verified staged add-on after all Blender processes have closed.

Run by Blender's bundled standalone Python, never by the game or an admin
service. The existing installation is retained as a complete rollback copy.
"""
import json
import os
from pathlib import Path
import shutil
import sys
import time
import uuid

import eiem_release as updates


def blender_processes():
    if os.name != 'nt':
        raise ValueError('当前自动安装仅支持 Windows')
    import ctypes
    from ctypes import wintypes
    class Entry(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('usage', wintypes.DWORD), ('pid', wintypes.DWORD),
                    ('heap', ctypes.c_size_t), ('module', wintypes.DWORD), ('threads', wintypes.DWORD),
                    ('parent', wintypes.DWORD), ('priority', wintypes.LONG), ('flags', wintypes.DWORD),
                    ('exe', wintypes.WCHAR * 260)]
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    api.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    api.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    api.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = api.CreateToolhelp32Snapshot(2, 0)
    if handle == wintypes.HANDLE(-1).value:
        raise OSError(ctypes.get_last_error(), '无法读取 Blender 进程')
    try:
        item = Entry(); item.size = ctypes.sizeof(item)
        found = []
        valid = api.Process32FirstW(handle, ctypes.byref(item))
        while valid:
            if item.exe.casefold() == 'blender.exe':
                found.append(item.pid)
            valid = api.Process32NextW(handle, ctypes.byref(item))
        return found
    finally:
        api.CloseHandle(handle)


def save_job(path, job):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, path)


def install(job_path):
    job_path = Path(job_path).resolve()
    job = json.loads(job_path.read_text('utf-8-sig'))
    if job.get('format') != 'EFF_BLENDER_INSTALL/1':
        raise ValueError('安装任务格式不匹配')
    target = updates.check_install_target(job['target'])
    candidate = Path(job['candidate']).resolve()
    if job_path.parent.parent != target.parent.resolve() or candidate != job_path.parent / 'unpacked/EFF_Blender':
        raise ValueError('安装暂存目录越界')
    if any(updates.linked(path) for path in (job_path.parent, candidate.parent, candidate)):
        raise ValueError('安装暂存目录不能是链接')
    build = updates.verify_package(candidate, job['revision'])
    old = updates.read_build(target)
    if old.get('revision') != job.get('oldRevision'):
        raise ValueError('插件已被其他更新更改，本次安装停止')
    # Keep unknown author/user files; retire only files listed by a previous
    # build receipt. Native modules are never replaced while Blender is open.
    for path in target.rglob('*'):
        if updates.linked(path):
            raise ValueError('插件内部包含链接，保留原安装')
        if not path.is_file() or '__pycache__' in path.parts:
            continue
        relative = path.relative_to(target)
        if relative.as_posix() in old.get('files', {}) or relative.as_posix() == updates.BUILD_FILE:
            continue
        destination = candidate / relative
        if not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
    backup = target.parent / '.eff-update-backups' / target.name / uuid.uuid4().hex
    backup.parent.mkdir(parents=True, exist_ok=True)
    target.rename(backup)
    try:
        candidate.rename(target)
    except Exception:
        backup.rename(target)
        raise
    job.update(status='installed', backup=str(backup), revision=build['revision'])
    save_job(job_path, job)
    return job


def run(job_path):
    path = Path(job_path).resolve()
    try:
        job = json.loads(path.read_text('utf-8-sig'))
        job['status'] = 'waitingForBlenderExit'; save_job(path, job)
        while blender_processes():
            time.sleep(1)
        # A new process can race the final check; locked files make the rename
        # fail with the old directory intact. Do not force-close anything.
        install(path)
    except Exception as error:
        job = json.loads(path.read_text('utf-8-sig'))
        job.update(status='failed', error=str(error)); save_job(path, job)
        raise


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('Expected a staged EFF update job')
    run(sys.argv[1])
