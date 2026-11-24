"""Windows one-file upgrade handoff. The old process stays alive until GUI readiness."""

import ctypes
from ctypes import wintypes as w
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
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
    fixture = old.name == "PowerModule-PC-old.exe" and _smoke_mode(old.parent)
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
        if (self.process.poll() is not None or not (self.folder / "ready").is_file()
                or any((self.folder / flag).exists() for flag in ("abort", "rejected"))):
            self.abort()
            raise RuntimeError("新版未就绪，旧客户端保持运行。")
        _mark(self.folder, "commit")

    def abort(self):
        try:
            _mark(self.folder, "abort")
        except OSError:
            pass


def launch(new_path, expected_sha256, expected_version, resume=None):
    """Verify again under a deny-write lock, then launch without shell indirection."""
    old = _exe()
    resume = _resume(resume)
    new = Path(new_path)
    if (new != new.resolve(strict=True) or new.parent != old.parent or new == old
            or new.name != f"PowerModule-PC-v{expected_version}.exe"):
        raise ValueError("新版客户端不在预期位置。")
    lock = _file(new, 0x80000000, 1)
    folder = None
    session = None
    try:
        _identity(lock)
        with new.open("rb") as exe:
            digest = sha256()
            for block in iter(lambda: exe.read(1024 * 1024), b""):
                digest.update(block)
        if (digest.hexdigest() != expected_sha256 or not _is_windows_x64_gui(new)
                or _windows_file_version(new) != (*version_key(expected_version), 0)):
            raise ValueError("新版客户端已变更，无法启动。")
        if not _predecessor(old, PC_VERSION, expected_version):
            raise ValueError("旧客户端文件名或版本无法确认；改名的客户端请手动更新。")
        folder = Path(tempfile.mkdtemp(prefix="PowerModule-upgrade-")).resolve(strict=True)
        (folder / "request.json").write_text(json.dumps({
            "pid": os.getpid(), "old": str(old), "identity": _path_identity(old),
            "new": str(new), "version": expected_version, "old_version": PC_VERSION, "resume": resume,
        }), encoding="utf-8")
        env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")
        process = subprocess.Popen([str(new), FLAG, str(folder)], cwd=new.parent, env=env)
        session = Session(folder, process)
        deadline = monotonic() + TIMEOUT
        while monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"新版未能启动（退出码 {process.returncode}），已保留旧客户端。")
            if (folder / "rejected").is_file():
                raise RuntimeError("新版无法安全地恢复原有设备连接，已保留旧客户端。")
            if (folder / "ready").is_file():
                return session
            sleep(0.05)
        raise TimeoutError("新版启动超时，已保留旧客户端。")
    except BaseException:
        if session is not None:
            session.abort()
            try:
                session.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            else:
                _cleanup(folder)
        elif folder is not None:
            _cleanup(folder)
        raise
    finally:
        _close(lock)


@dataclass
class Handoff:
    folder: Path
    old: Path
    identity: tuple
    handle: int
    deadline: float
    resume: dict | None

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
            _remove_exact(self.old, self.identity)
        except (OSError, ValueError) as error:
            if getattr(error, "winerror", None) in (5, 32, 33) and monotonic() < self.deadline:
                return "pending"
            return self._finish("cleanup-failed")
        return self._finish("complete")


def pending_handoff():
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
    return Handoff(folder, old, identity, _same_process_path(payload["pid"], old),
                   monotonic() + TIMEOUT * 2, _resume(payload.get("resume")))


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
    if _exe().name != "PowerModule-PC-old.exe" or mode is None:
        raise ValueError("无效的 CI 升级测试目录。")
    successor = folder / f"PowerModule-PC-v{PC_VERSION}.exe"

    def run():
        result = 1
        try:
            if not window.isVisible():
                raise AssertionError("predecessor GUI not visible")
            try:
                session = launch(successor, sha256(successor.read_bytes()).hexdigest(), PC_VERSION)
            except (RuntimeError, TimeoutError) as error:
                if mode != "failure" or not _exe().is_file() or not window.isVisible():
                    raise
                if "无法安全地恢复" not in str(error):
                    raise
                (folder / "result.txt").write_text("failure-preserved-old", encoding="ascii")
            else:
                if mode != "success" or not _exe().is_file():
                    session.abort()
                    raise AssertionError("old EXE must still exist before commit")
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
    if mode is None or handoff.old.name != "PowerModule-PC-old.exe":
        return False
    from PySide6.QtCore import QTimer

    timer = QTimer(app)
    timer.setInterval(50)

    def poll():
        result = handoff.poll()
        if result == "pending":
            return
        timer.stop()
        if mode == "success":
            marker = "success-removed-old" if result == "complete" and not handoff.old.exists() else result
            (folder / "result.txt").write_text(marker, encoding="ascii")
            app.exit(0 if marker == "success-removed-old" else 1)
        else:
            app.exit(0 if result == "abort" else 1)

    def visible():
        if not any(widget.isVisible() and PC_VERSION in widget.windowTitle()
                   for widget in app.topLevelWidgets()):
            app.exit(1)
            return
        if mode == "failure":
            handoff.reject()
        else:
            handoff.acknowledge()
        timer.timeout.connect(poll)
        timer.start()

    QTimer.singleShot(0, visible)
    return True
