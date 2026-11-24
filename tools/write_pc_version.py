"""Generate Windows version resource from the desktop version constant."""

from pathlib import Path
import runpy
import sys

version = runpy.run_path(Path(__file__).resolve().parents[1] / "pc_client/version.py")["PC_VERSION"]
parts = tuple(map(int, version.split("."))) + (0,)
assert len(parts) == 4 and all(0 <= part <= 65535 for part in parts)
Path(sys.argv[1]).write_text(
    f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={parts!r}, prodvers={parts!r}, mask=0x3f, flags=0,
                    OS=0x40004, fileType=1, subtype=0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('FileDescription', 'PowerModule Windows Client'),
      StringStruct('FileVersion', {version!r}),
      StringStruct('InternalName', 'PowerModule-PC'),
      StringStruct('OriginalFilename', 'PowerModule-PC.exe'),
      StringStruct('ProductName', 'PowerModule PC'),
      StringStruct('ProductVersion', {version!r})
    ])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""", encoding="utf-8",
)
