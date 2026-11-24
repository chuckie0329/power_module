"""Read separate stable catalogs and save verified downloads. Never execute or flash."""

from dataclasses import dataclass
from hashlib import sha256
import json
import os
import sys
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from time import monotonic
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from pc_client.firmware_update import APP_SLOT_SIZE, FirmwareImage

REPOSITORY = "chuckie0329/power_module"
CATALOG = f"https://raw.githubusercontent.com/{REPOSITORY}/main/updates"
FIRMWARE_TARGET = "PowerModule-ESP32"
PC_ASSET = "PowerModule-PC.exe"
FIRMWARE_ASSET = "PowerModule-ESP32.bin"
DOWNLOAD_HOSTS = {"github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"}
MAX_CATALOG_BYTES = 8192
LIMITS = {"pc": 150 * 1024**2, "firmware": APP_SLOT_SIZE}
TIMEOUT = 12
DOWNLOAD_SECONDS = 180


def version_key(value):
    if not isinstance(value, str) or not re.fullmatch(r"(0|[1-9][0-9]{0,4})(\.(0|[1-9][0-9]{0,4})){2}", value):
        raise ValueError("无效版本号。")
    parts = tuple(map(int, value.split(".")))
    if any(part > 65535 for part in parts):
        raise ValueError("无效版本号。")
    return parts


def release_url(kind, version):
    prefix = "pc-v" if kind == "pc" else "v" if version == "1.2.1" else "fw-v"
    tag = prefix + version
    asset = PC_ASSET if kind == "pc" else FIRMWARE_ASSET
    return f"https://github.com/{REPOSITORY}/releases/download/{tag}/{asset}"


@dataclass(frozen=True)
class Update:
    kind: str
    version: str
    url: str
    sha256: str
    size: int
    notes: str = ""

    @property
    def filename(self):
        asset = Path(PC_ASSET if self.kind == "pc" else FIRMWARE_ASSET)
        return f"{asset.stem}-v{self.version}{asset.suffix}"


def parse_catalog(doc, kind):
    if (kind not in LIMITS or not isinstance(doc, dict)
            or type(doc.get("schema")) is not int or doc["schema"] != 1
            or doc.get("kind") != kind
            or "stable" not in doc):
        raise ValueError("不支持的更新清单。")
    item = doc["stable"]
    if item is None:
        return None
    if not isinstance(item, dict):
        raise ValueError("无效的稳定版本记录。")
    version = item.get("version")
    version_key(version)
    url, checksum, size = (item.get(x) for x in ("url", "sha256", "size"))
    if (url != release_url(kind, version) or not isinstance(checksum, str)
            or not re.fullmatch(r"[0-9a-f]{64}", checksum)
            or type(size) is not int or not 0 < size <= LIMITS[kind]):
        raise ValueError("更新链接、校验和或文件大小无效。")
    if kind == "firmware" and (item.get("target") != FIRMWARE_TARGET
                               or type(item.get("protocol")) is not int or item["protocol"] != 1):
        raise ValueError("固件目标或协议不兼容。")
    if kind == "pc" and item.get("target") != "windows-x64":
        raise ValueError("客户端目标不兼容。")
    notes = item.get("notes", "")
    if not isinstance(notes, str) or len(notes) > 360 or any(ord(c) < 32 and c not in "\t\n" for c in notes):
        raise ValueError("更新说明无效或过长。")
    return Update(kind, version, url, checksum, size, notes)


def _allowed(url, hosts):
    try:
        parsed = urlsplit(url)
        return (parsed.scheme == "https" and parsed.hostname in hosts and parsed.port in (None, 443)
                and parsed.username is None and parsed.password is None)
    except ValueError:
        return False


class _Redirects(HTTPRedirectHandler):
    def __init__(self, hosts):
        self.hosts = hosts

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _allowed(newurl, self.hosts):
            raise ValueError("更新服务器跳转至不受支持的地址。")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open(url, hosts):
    if not _allowed(url, hosts):
        raise ValueError("更新地址不受支持。")
    return build_opener(_Redirects(hosts)).open(
        Request(url, headers={"User-Agent": "PowerModule-PC"}), timeout=TIMEOUT,
    )


def check_catalog(kind):
    with _open(f"{CATALOG}/{kind}-stable.json", {"raw.githubusercontent.com"}) as source:
        content = source.read(MAX_CATALOG_BYTES + 1)
    if len(content) > MAX_CATALOG_BYTES:
        raise ValueError("更新清单过大。")
    return parse_catalog(json.loads(content), kind)


def _is_windows_x64_gui(path):
    with path.open("rb") as source:
        dos = source.read(64)
        if len(dos) != 64 or dos[:2] != b"MZ":
            return False
        offset = int.from_bytes(dos[60:64], "little")
        if offset < 64 or offset > 65536:
            return False
        source.seek(offset)
        head = source.read(96)
    return (len(head) == 96 and head[:6] == b"PE\x00\x00\x64\x86"
            and head[24:26] == b"\x0b\x02" and head[92:94] == b"\x02\x00")


def download(update, destination):
    """Save to an adjacent temporary file; never overwrite an existing destination."""
    destination = Path(destination)
    if destination.exists():
        raise FileExistsError("文件已经存在。请选择新文件名，以便保留旧版本。")
    tmp_path = None
    digest, size = sha256(), 0
    deadline = monotonic() + DOWNLOAD_SECONDS
    try:
        with NamedTemporaryFile(dir=destination.parent, delete=False, suffix=".download") as temp:
            tmp_path = Path(temp.name)
            with _open(update.url, DOWNLOAD_HOSTS) as source:
                while chunk := source.read(64 * 1024):
                    if monotonic() > deadline:
                        raise ValueError("下载超时。")
                    size += len(chunk)
                    if size > update.size:
                        raise ValueError("文件超出清单中的大小。")
                    temp.write(chunk)
                    digest.update(chunk)
        if size != update.size or digest.hexdigest() != update.sha256:
            raise ValueError("下载校验失败，未保存文件。")
        if update.kind == "firmware" and FirmwareImage.load(tmp_path).version != update.version:
            raise ValueError("固件内嵌版本与清单不符。")
        if update.kind == "pc" and not _is_windows_x64_gui(tmp_path):
            raise ValueError("文件不是 Windows x64 客户端。")
        try:
            # Windows rename refuses to replace an existing target. On POSIX
            # use a same-directory hard link for that same atomic no-clobber rule.
            if sys.platform == "win32":
                tmp_path.rename(destination)
            else:
                os.link(tmp_path, destination)
        except FileExistsError as error:
            raise FileExistsError("文件已经存在。请选择新文件名，以便保留旧版本。") from error
    finally:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
