"""Windows one-file upgrade handoff. The old process stays alive until GUI readiness."""

import ctypes
from ctypes import wintypes as w
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
from time import monotonic, sleep

from pc_client.online_updates import _is_windows_x64_gui, _windows_file_version, version_key
from pc_client.version import PC_VERSION


FLAG = "--power-module-upgrade"
TIMEOUT = 60
REPARSE = 0x400
INVALID = ctypes.c_void_p(-1).value


def _win(name, result, *types):
    api = ctypes.WinDLL("kernel32.dll", use_last_error=True, winmode=0x00000800)[name]
    api.restype, api.argtypes = result, types
    return api


def _close(handle):
    _win("CloseHandle", w.BOOL, w.HANDLE)(handle)


def _winerror():
    return ctypes.WinError(ctypes.get_last_error())


def _file(path, access, sharing):
    handle = _win("CreateFileW", w.HANDLE, w.LPCWSTR, w.DWORD, w.DWORD,
                  w.LPVOID, w.DWORD, w.DWORD, w.HANDLE)(
        str(path), access, sharing, None, 3, 0x00200000, None,
    )
    if handle == INVALID:
        raise _winerror()
    return handle


class _FileInfo(ctypes.Structure):
    _fields_ = [
        ("attributes", w.DWORD), ("created", w.FILETIME), ("accessed", w.FILETIME),
        ("modified", w.FILETIME), ("volume", w.DWORD), ("size_high", w.DWORD),
        ("size_low", w.DWORD), ("links", w.DWORD), ("id_high", w.DWORD), ("id_low", w.DWORD),
    ]


def _identity(handle):
    info = _FileInfo()
    if not _win("GetFileInformationByHandle", w.BOOL, w.HANDLE, ctypes.POINTER(_FileInfo))(
        handle, ctypes.byref(info),
    ):
        raise _winerror()
    if info.attributes & (REPARSE | 0x10) or info.links != 1 or not (info.id_high or info.id_low):
        raise ValueError("更新只支持普通本地文件。")
    return (info.volume, info.id_high, info.id_low, info.size_high, info.size_low,
            info.modified.dwHighDateTime, info.modified.dwLowDateTime)


def _path_identity(path):
    handle = _file(path, 0x80, 7)
    try:
        return _identity(handle)
    finally:
        _close(handle)


def _process(pid):
    handle = _win("OpenProcess", w.HANDLE, w.DWORD, w.BOOL, w.DWORD)(
        0x00101000, False, pid,
    )
    if not handle:
        raise _winerror()
    return handle


def _image_path(handle):
    buf = ctypes.create_unicode_buffer(32768)
    size = w.DWORD(len(buf))
    if not _win("QueryFullProcessImageNameW", w.BOOL, w.HANDLE, w.DWORD,
                w.LPWSTR, ctypes.POINTER(w.DWORD))(handle, 0, buf, ctypes.byref(size)):
        raise _winerror()
    return Path(buf.value).resolve(strict=True)


def _same_process_path(pid, path):
    handle = _process(pid)
    try:
        if _image_path(handle) != path:
            raise ValueError("无法确认旧客户端进程，保留旧客户端。")
        return handle
    except BaseException:
        _close(handle)
        raise


def _exited(handle):
    result = _win("WaitForSingleObject", w.DWORD, w.HANDLE, w.DWORD)(handle, 0)
    if result not in (0, 258):
        raise _winerror()
    return result == 0


def _remove_exact(path, identity):
    # Open the directory entry itself, disallowing competing rename/write.
    # Mark that handle for deletion, never a second lookup of the pathname.
    try:
        handle = _file(path, 0x00010080, 1)
    except FileNotFoundError:
        return
    try:
        if _identity(handle) != identity:
            raise ValueError("旧文件已变更，未删除。")
        delete = ctypes.c_ubyte(1)
        if not _win("SetFileInformationByHandle", w.BOOL, w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD)(
            handle, 4, ctypes.byref(delete), ctypes.sizeof(delete),
        ):
            raise _winerror()
    finally:
        _close(handle)


def _mark(folder, name):
    with (folder / name).open("xb"):
        pass


def _cleanup(folder):
    for name in ("request.json", "ready", "commit", "abort", "rejected"):
        try:
            (folder / name).unlink(missing_ok=True)
        except OSError:
            pass
    try:
        folder.rmdir()
    except OSError:
        pass


def _resume(value):
    if value is None:
        return None
    if (not isinstance(value, dict) or set(value) != {"mode", "target", "device_id"}
            or value["mode"] not in ("usb", "ble")
            or any(not isinstance(value[k], str) or not 1 <= len(value[k]) <= 256
                   or any(ord(c) < 32 for c in value[k]) for k in ("target", "device_id"))):
        raise ValueError("无效的升级重连信息。")
    return value


def _power_module_product(path):
    version = ctypes.WinDLL("version.dll", winmode=0x00000800)

    def fn(name, result, *types):
        function = version[name]
        function.restype, function.argtypes = result, types
        return function

    cookie = w.DWORD()
    size = fn("GetFileVersionInfoSizeW", w.DWORD, w.LPCWSTR, ctypes.POINTER(w.DWORD))(
        str(path), ctypes.byref(cookie),
    )
    if not 0 < size <= 65536:
        return False
    data = ctypes.create_string_buffer(size)
    if not fn("GetFileVersionInfoW", w.BOOL, w.LPCWSTR, w.DWORD, w.DWORD, w.LPVOID)(
        str(path), 0, size, data,
    ):
        return False
    query = fn("VerQueryValueW", w.BOOL, w.LPCVOID, w.LPCWSTR,
               ctypes.POINTER(w.LPVOID), ctypes.POINTER(w.UINT))
    for field, expected in (("ProductName", "PowerModule PC"), ("InternalName", "PowerModule-PC"),
                            ("OriginalFilename", "PowerModule-PC.exe")):
        value, length = w.LPVOID(), w.UINT()
        key = rf"\StringFileInfo\040904B0\{field}"
        if (not query(data, key, ctypes.byref(value), ctypes.byref(length))
                or not value or length.value != len(expected) + 1
                or ctypes.wstring_at(value, len(expected)) != expected):
            return False
    return True


def _predecessor(old, old_version, version):
    old_key, next_key = version_key(old_version), version_key(version)
    fixture = old.name == "PowerModule-PC.exe" and _smoke_mode(old.parent)
    if not fixture and (old_key >= next_key or old.name not in (
        "PowerModule-PC.exe", f"PowerModule-PC-v{old_version}.exe",
    )):
        return False
    return (_windows_file_version(old) == (*old_key, 0)
            and _power_module_product(old))


def _exe():
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        raise ValueError("仅 Windows 打包版支持自动升级。")
    return Path(sys.executable).resolve(strict=True)


@dataclass
class Session:
    folder: Path
    process: subprocess.Popen

    def commit(self):
        if (self.process.poll() is not None or not (self.folder / "helper-ready").is_file()
                or any((self.folder / flag).exists() for flag in ("abort", "rejected"))):
            self.abort()
            raise RuntimeError("新版未就绪，旧客户端保持运行。")
        _mark(self.folder, "commit")

    def abort(self):
        try:
            _mark(self.folder, "abort")
        except OSError:
            pass
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            return
        _clear_tx(self.folder)


CANONICAL = "PowerModule-PC.exe"
TX_DIR = ".PowerModule-update"
WORKER = "--power-module-update-worker"
START = "--power-module-update-start"
ROLLBACK = "--power-module-update-rollback"


def _same_digest(path, digest):
    with path.open("rb") as source:
        h = sha256()
        for data in iter(lambda: source.read(1024 * 1024), b""):
            h.update(data)
    return h.hexdigest() == digest


def _verify_binary(path, digest, version):
    if (not _is_windows_x64_gui(path) or not _same_digest(path, digest)
            or _windows_file_version(path) != (*version_key(version), 0)
            or not _power_module_product(path)):
        raise ValueError("新版客户端校验失败。")


def _plain_directory(path):
    return (path == path.resolve(strict=True) and path.is_dir()
            and not path.lstat().st_file_attributes & REPARSE)


def _valid_tx(folder, directory):
    if folder != directory / TX_DIR or not _plain_directory(folder):
        raise ValueError("升级事务位置无法确认。")
    request = folder / "request.json"
    _path_identity(request)
    with request.open(encoding="utf-8") as source:
        payload = json.loads(source.read(8193))
    if set(payload) != {"pid", "identity", "version", "old_version", "digest", "resume"}:
        raise ValueError("升级事务数据无效。")
    version_key(payload["version"])
    version_key(payload["old_version"])
    _resume(payload["resume"])
    return payload


def _spawn(path, flag, folder):
    env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")
    return subprocess.Popen([str(path), flag, str(folder)], cwd=folder.parent, env=env)


def _clear_tx(folder, worker=True):
    # No recursive deletion. Never remove previous.exe here: an unsuccessful
    # rollback can leave it as the only intact executable.
    files = ["request.json", "candidate.exe", "worker.json", "helper-ready", "commit",
             "ready", "rejected", "abort", "success", "recovered", "recovery-attached"]
    if worker:
        files.append("helper.exe")
    for name in files:
        try:
            (folder / name).unlink(missing_ok=True)
        except OSError:
            pass
    try:
        folder.rmdir()
    except OSError:
        pass


def verify_predecessor(expected_version):
    old = _exe()
    if (old.name != CANONICAL or not _predecessor(old, PC_VERSION, expected_version)):
        raise ValueError("请先手动安装并以 PowerModule-PC.exe 打开客户端。")
    if (old.parent / TX_DIR).exists():
        raise ValueError("检测到未完成的客户端升级，请先检查旧版备份。")


def launch(new_path, expected_sha256, expected_version, resume=None):
    """Stage the verified candidate and wait for the independent helper, not the GUI."""
    old = _exe()
    verify_predecessor(expected_version)
    resume = _resume(resume)
    new = Path(new_path)
    if (new != new.resolve(strict=True) or new.parent.parent != old.parent
            or not new.parent.name.startswith(".PowerModule-download-")
            or not _plain_directory(new.parent)
            or new.name != f"PowerModule-PC-v{expected_version}.exe"):
        raise ValueError("新版客户端不在预期的下载目录。")
    lock = _file(new, 0x80000000, 1)
    folder = old.parent / TX_DIR
    session = None
    created = False
    try:
        _identity(lock)
        _verify_binary(new, expected_sha256, expected_version)
        folder.mkdir()
        created = True
        shutil.copyfile(new, folder / "candidate.exe")
        shutil.copyfile(new, folder / "helper.exe")
        _verify_binary(folder / "candidate.exe", expected_sha256, expected_version)
        helper = folder / "helper.exe"
        helper_lock = _file(helper, 0x80000000, 1)
        try:
            _verify_binary(helper, expected_sha256, expected_version)
            (folder / "request.json").write_text(json.dumps({
                "pid": os.getpid(), "identity": _path_identity(old), "digest": expected_sha256,
                "version": expected_version, "old_version": PC_VERSION, "resume": resume,
            }), encoding="utf-8")
            session = Session(folder, _spawn(helper, WORKER, folder))
            deadline = monotonic() + TIMEOUT
            while monotonic() < deadline:
                if session.process.poll() is not None:
                    raise RuntimeError("升级程序无法启动，旧客户端保持运行。")
                if (folder / "helper-ready").is_file():
                    return session
                sleep(0.05)
            raise TimeoutError("升级程序未就绪，旧客户端保持运行。")
        finally:
            _close(helper_lock)
    except BaseException:
        if session is not None:
            session.abort()
            try:
                session.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        if created and (session is None or session.process.poll() is not None):
            _clear_tx(folder)
        raise
    finally:
        _close(lock)


def _replace_with_backup(old, candidate, backup):
    if backup.exists():
        raise ValueError("升级目录中已有旧版备份。")
    if not _win("ReplaceFileW", w.BOOL, w.LPCWSTR, w.LPCWSTR, w.LPCWSTR,
                w.DWORD, w.LPVOID, w.LPVOID)(
                    str(old), str(candidate), str(backup), 0, None, None,
                ):
        raise _winerror()


def _retry_locked(action):
    deadline = monotonic() + TIMEOUT
    while True:
        try:
            return action()
        except OSError as error:
            if getattr(error, "winerror", None) not in (5, 32, 33) or monotonic() >= deadline:
                raise
            sleep(0.1)


def _restore(old, backup, old_identity, new_identity):
    if _path_identity(backup) != old_identity:
        raise ValueError("无法确认旧版备份，未覆盖现有文件。")
    if old.exists() and _path_identity(old) != new_identity:
        raise ValueError("客户端文件已被修改，未覆盖。")
    os.replace(backup, old)


def _notice(message):
    user32 = ctypes.WinDLL("user32.dll", winmode=0x00000800)
    call = user32.MessageBoxW
    call.argtypes = [w.HWND, w.LPCWSTR, w.LPCWSTR, w.UINT]
    call.restype = ctypes.c_int
    call(None, message, "PowerModule 客户端升级", 0x30)


def run_upgrade_worker():
    if len(sys.argv) < 2 or sys.argv[1] != WORKER:
        return None
    helper = _exe()
    if len(sys.argv) != 3 or helper.name != "helper.exe":
        return 1
    folder = Path(sys.argv[2])
    old, backup, candidate = folder.parent / CANONICAL, folder / "previous.exe", folder / "candidate.exe"
    predecessor = None
    old_released = False
    try:
        payload = _valid_tx(folder, helper.parent.parent)
        old_id = tuple(payload["identity"])
        if (helper.parent != folder or payload["version"] != PC_VERSION
                or not _predecessor(old, payload["old_version"], PC_VERSION)
                or _path_identity(old) != old_id):
            raise ValueError("无法确认升级事务。")
        new_id = _path_identity(candidate)
        _verify_binary(candidate, payload["digest"], PC_VERSION)
        predecessor = _same_process_path(payload["pid"], old)
        (folder / "worker.json").write_text(str(os.getpid()), encoding="ascii")
        _mark(folder, "helper-ready")
        deadline = monotonic() + TIMEOUT * 2
        while monotonic() < deadline and not (folder / "abort").exists():
            if _exited(predecessor):
                if not (folder / "commit").is_file():
                    return 1
                old_released = True
                break
            sleep(0.05)
        if not old_released:
            return 1
        _close(predecessor)
        predecessor = None

        def swap():
            if _path_identity(old) != old_id or _path_identity(candidate) != new_id:
                raise ValueError("升级文件已变更。")
            _replace_with_backup(old, candidate, backup)

        _retry_locked(swap)
        if _path_identity(old) != new_id or _path_identity(backup) != old_id:
            raise ValueError("升级文件替换结果校验失败。")
        image_lock = _file(old, 0x80000000, 1)
        try:
            _verify_binary(old, payload["digest"], PC_VERSION)
            process = _spawn(old, START, folder)
        finally:
            _close(image_lock)
        deadline = monotonic() + TIMEOUT
        while monotonic() < deadline:
            if process.poll() is not None or (folder / "rejected").exists():
                break
            if (folder / "ready").is_file():
                # Acknowledge startup before dropping the rollback file.
                _mark(folder, "success")
                try:
                    _retry_locked(lambda: _remove_exact(backup, old_id))
                except (ValueError, OSError):
                    pass  # UI reports retained backup once helper exits.
                return 0
            sleep(0.05)
        _mark(folder, "abort")
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired as error:
            raise RuntimeError("新版尚未退出，无法自动回退。") from error
        raise RuntimeError("新版未能启动或恢复原设备连接。")
    except Exception:
        if old_released:
            try:
                if backup.exists():
                    _retry_locked(lambda: _restore(old, backup, old_id, new_id))
                if _path_identity(old) != old_id:
                    raise ValueError("无法验证已恢复的旧版。")
                _mark(folder, "recovered")
                recovery = _spawn(old, ROLLBACK, folder)
                deadline = monotonic() + TIMEOUT
                while (monotonic() < deadline and recovery.poll() is None
                       and not (folder / "recovery-attached").is_file()):
                    sleep(0.05)
            except (ValueError, OSError):
                _notice(f"客户端未能自动恢复。旧版备份如果存在，保留在：\n{backup}")
        return 1
    finally:
        if predecessor is not None:
            _close(predecessor)


@dataclass
class Handoff:
    folder: Path
    old: Path
    handle: int
    resume: dict | None
    recovery: bool = False
    helper_id: tuple | None = None
    cleanup_until: float | None = None
    legacy = False

    def acknowledge(self):
        if not self.recovery:
            try:
                _mark(self.folder, "ready")
            except FileExistsError:
                pass

    def reject(self, reason=None):
        if not self.recovery:
            try:
                _mark(self.folder, "rejected")
            except FileExistsError:
                pass

    def poll(self):
        if not self.recovery and ((self.folder / "abort").exists() or
                (_exited(self.handle) and not (self.folder / "success").is_file())):
            _close(self.handle)
            return "abort"
        if not _exited(self.handle):
            return "pending"
        keep_backup = (self.folder / "previous.exe").exists()
        helper_locked = False
        if self.helper_id is not None:
            try:
                _remove_exact(self.folder / "helper.exe", self.helper_id)
            except (OSError, ValueError) as error:
                if self.cleanup_until is None:
                    self.cleanup_until = monotonic() + 15
                if (getattr(error, "winerror", None) in (5, 32, 33)
                        and monotonic() < self.cleanup_until):
                    return "pending"
                helper_locked = True
        _close(self.handle)
        _clear_tx(self.folder, worker=self.helper_id is None)
        return "cleanup-failed" if keep_backup or helper_locked else "complete"


def complete_legacy_install(old, old_id):
    """Install the already-ready legacy successor; never overwrite unknown files."""
    running = _exe()
    if running.name != f"PowerModule-PC-v{PC_VERSION}.exe":
        raise ValueError("无法确认旧版升级程序启动的客户端身份。")
    canonical = running.parent / CANONICAL
    if old.parent != running.parent:
        raise ValueError("旧客户端路径不在预期位置。")
    if old == canonical:
        # Wait for PyInstaller's outer image holder before copying the candidate.
        eligible = _file(old, 0x00010080, 1)
        try:
            if _identity(eligible) != old_id:
                raise ValueError("旧客户端已被替换，未覆盖。")
        finally:
            _close(eligible)
    lock = _file(running, 0x80000000, 1)
    staging = None
    try:
        _identity(lock)
        with running.open("rb") as source:
            digest = sha256()
            for data in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(data)
        _verify_binary(running, digest.hexdigest(), PC_VERSION)
        staging = Path(tempfile.mkdtemp(prefix=".PowerModule-migrate-", dir=running.parent))
        candidate = staging / CANONICAL
        shutil.copyfile(running, candidate)
        _verify_binary(candidate, digest.hexdigest(), PC_VERSION)
        if old == canonical:
            backup = staging / "previous.exe"
            new_id = _path_identity(candidate)
            try:
                if _path_identity(old) != old_id:
                    raise ValueError("旧客户端已被替换，未覆盖。")
                _replace_with_backup(old, candidate, backup)
                if _path_identity(canonical) != new_id or _path_identity(backup) != old_id:
                    raise ValueError("标准客户端安装结果异常。")
            except (ValueError, OSError):
                if backup.exists():
                    _restore(canonical, backup, old_id, new_id)
                raise
            _remove_exact(backup, old_id)
        else:
            # Windows rename is atomic and does not overwrite an existing file.
            os.rename(candidate, canonical)
            _remove_exact(old, old_id)
    finally:
        _close(lock)
        if staging is not None:
            (staging / CANONICAL).unlink(missing_ok=True)
            try:
                staging.rmdir()
            except OSError:
                pass  # Preserve the backup if cleanup/rollback is blocked.


@dataclass
class LegacyHandoff:
    folder: Path
    old: Path
    identity: tuple
    handle: int
    deadline: float
    resume: dict | None

    legacy = True
    recovery = False

    def acknowledge(self):
        _mark(self.folder, "ready")

    def reject(self, reason=None):
        # No cross-process user strings; the old app gives a bounded error.
        try:
            _mark(self.folder, "rejected")
        except FileExistsError:
            pass

    def _finish(self, status):
        _close(self.handle)
        _cleanup(self.folder)
        return status

    def poll(self):
        committed = (self.folder / "commit").is_file()
        if (self.folder / "abort").exists():
            return self._finish("abort")
        if (self.folder / "rejected").exists() and committed:
            return self._finish("cleanup-failed" if _exited(self.handle) else "abort")
        if not committed:
            if _exited(self.handle) or monotonic() > self.deadline:
                return self._finish("abort")
            return "pending"
        # PyInstaller's outer process can retain the image after this GUI exits.
        # Retry sharing/lock errors only; never delete a replacement at the path.
        if not _exited(self.handle):
            return self._finish("cleanup-failed") if monotonic() > self.deadline else "pending"
        try:
            complete_legacy_install(self.old, self.identity)
        except (OSError, ValueError) as error:
            if getattr(error, "winerror", None) in (5, 32, 33) and monotonic() < self.deadline:
                return "pending"
            return self._finish("cleanup-failed")
        return self._finish("complete")


def _legacy_handoff():
    if len(sys.argv) < 2 or sys.argv[1] != FLAG:
        return None
    new = _exe()
    if len(sys.argv) != 3:
        raise ValueError("无效的升级启动参数。")
    folder = Path(sys.argv[2])
    if (folder != folder.resolve(strict=True) or folder.parent != Path(tempfile.gettempdir()).resolve(strict=True)
            or not folder.name.startswith("PowerModule-upgrade-")
            or folder.lstat().st_file_attributes & REPARSE):
        raise ValueError("无效的升级握手目录。")
    with (folder / "request.json").open(encoding="utf-8") as request:
        payload = json.loads(request.read(8193))
    old = Path(payload["old"])
    identity = tuple(payload["identity"])
    if (Path(payload["new"]) != new or payload["version"] != PC_VERSION
            or old.parent != new.parent or old == new or old.suffix.lower() != ".exe"
            or old.resolve(strict=True) != old or _path_identity(old) != identity
            or not _predecessor(old, payload["old_version"], PC_VERSION)):
        raise ValueError("无法确认升级设备身份，保留旧客户端。")
    return LegacyHandoff(folder, old, identity, _same_process_path(payload["pid"], old),
                   monotonic() + TIMEOUT * 2, _resume(payload.get("resume")))



def pending_handoff():
    if len(sys.argv) < 2 or sys.argv[1] not in (FLAG, START, ROLLBACK):
        return None
    if sys.argv[1] == FLAG:
        return _legacy_handoff()
    old = _exe()
    if len(sys.argv) != 3:
        raise ValueError("无效的启动参数。")
    folder = Path(sys.argv[2])
    payload = _valid_tx(folder, old.parent)
    rollback = sys.argv[1] == ROLLBACK
    if old.name != CANONICAL:
        raise ValueError("无效的启动文件名。")
    if rollback:
        if (payload["old_version"] != PC_VERSION or not (folder / "recovered").is_file()
                or _path_identity(old) != tuple(payload["identity"])):
            raise ValueError("无法确认恢复后的旧客户端。")
    else:
        if payload["version"] != PC_VERSION or not (folder / "commit").is_file():
            raise ValueError("无法确认替换后的新版客户端。")
        _verify_binary(old, payload["digest"], PC_VERSION)
    pid = int((folder / "worker.json").read_text(encoding="ascii"))
    helper = folder / "helper.exe"
    helper_id = _path_identity(helper)
    handle = _same_process_path(pid, helper)
    if rollback:
        try:
            _mark(folder, "recovery-attached")
        except BaseException:
            _close(handle)
            raise
    return Handoff(folder, old, handle, _resume(payload["resume"]), rollback, helper_id)


def _smoke_mode(directory):
    # Reserved for isolated CI copies, not an installed executable.
    mode = os.environ.get("POWER_MODULE_UPGRADE_SMOKE")
    if (mode in ("success", "failure") and directory.name.startswith("pm-ci-handoff-")
            and directory.parent == Path(tempfile.gettempdir()).resolve(strict=True)
            and (directory / "smoke-fixture").is_file()):
        return mode
    return None


def smoke_startup(window, app):
    if sys.argv[1:] != ["--release-upgrade-smoke"]:
        return False
    from PySide6.QtCore import QTimer

    folder = _exe().parent
    mode = _smoke_mode(folder)
    if _exe().name != CANONICAL or mode is None:
        raise ValueError("无效的 CI 升级测试目录。")
    successor = folder / ".PowerModule-download-ci" / f"PowerModule-PC-v{PC_VERSION}.exe"

    def run():
        result = 1
        try:
            if not window.isVisible():
                raise AssertionError("predecessor GUI not visible")
            original_popen = subprocess.Popen

            def never_start(*_args, **_kwargs):
                raise AssertionError("wrong hash attempted to launch an executable")

            try:
                subprocess.Popen = never_start
                try:
                    launch(successor, "0" * 64, PC_VERSION)
                except ValueError:
                    pass
                else:
                    raise AssertionError("invalid hash was accepted")
            finally:
                subprocess.Popen = original_popen
            session = launch(successor, sha256(successor.read_bytes()).hexdigest(), PC_VERSION)
            if not _exe().is_file() or (folder / TX_DIR / "previous.exe").exists():
                session.abort()
                raise AssertionError("canonical old EXE must still exist before commit")
            session.commit()
            result = 0
        except Exception as error:
            (folder / "result.txt").write_text(repr(error), encoding="utf-8")
        finally:
            window.close()
            app.exit(result)

    QTimer.singleShot(0, run)
    return True


def smoke_handoff(handoff, app):
    folder = handoff.old.parent
    mode = _smoke_mode(folder)
    if mode is None or handoff.old.name != CANONICAL:
        return False
    from PySide6.QtCore import QTimer

    timer = QTimer(app)
    timer.setInterval(50)

    def poll():
        result = handoff.poll()
        if result == "pending":
            return
        timer.stop()
        if mode == "failure" and not handoff.recovery:
            app.exit(0 if result == "abort" else 1)
            return
        expected = "failure-restored-old" if handoff.recovery else "success-replaced-old"
        marker = expected if result == "complete" and handoff.old.exists() else result
        (folder / "result.txt").write_text(marker, encoding="ascii")
        app.exit(0 if marker == expected else 1)

    def visible():
        if not any(widget.isVisible() and PC_VERSION in widget.windowTitle()
                   for widget in app.topLevelWidgets()):
            app.exit(1)
            return
        if mode == "failure" and not handoff.recovery:
            handoff.reject()
        else:
            handoff.acknowledge()
        timer.timeout.connect(poll)
        timer.start()

    QTimer.singleShot(0, visible)
    return True
