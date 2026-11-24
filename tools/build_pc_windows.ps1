$ErrorActionPreference = 'Stop'
Set-Location (Resolve-Path (Join-Path $PSScriptRoot '..'))
$buildPython = & python -c "import sys; print(sys.executable)"
if ($LASTEXITCODE -ne 0 -or -not $buildPython) { throw 'Could not locate the selected Python.' }
& $buildPython -c "import sys,struct; assert sys.platform == 'win32' and sys.version_info[:2] == (3,12) and struct.calcsize('P') == 8"
if ($LASTEXITCODE -ne 0) { throw 'Windows x64 Python 3.12 is required.' }
New-Item -ItemType Directory -Force '.work' | Out-Null
& $buildPython tools/write_pc_version.py '.work/pc-version-resource.txt'
if ($LASTEXITCODE -ne 0) { throw 'Could not generate the Windows version resource.' }
$originalPath = $env:Path
try {
    $env:Path = "$env:SystemRoot\System32;$env:SystemRoot;$env:SystemRoot\System32\Wbem"
    & $buildPython -m PyInstaller --noconfirm --onefile --windowed --name PowerModule-PC --icon pc_client/assets/power_module.ico --version-file .work/pc-version-resource.txt --paths . --workpath .work/pc-client-pyi-clean --specpath .work --distpath pc_client/build --hidden-import bleak.backends.winrt.client --hidden-import bleak.backends.winrt.scanner pc_client/__main__.py
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed.' }
} finally {
    $env:Path = $originalPath
}
Get-Item 'pc_client/build/PowerModule-PC.exe'
