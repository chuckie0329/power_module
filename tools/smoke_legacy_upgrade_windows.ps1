$ErrorActionPreference = 'Stop'
Set-Location (Resolve-Path (Join-Path $PSScriptRoot '..'))
$successor = (Resolve-Path 'pc_client/build/PowerModule-PC.exe').Path
$next = python -c "from pc_client.version import PC_VERSION; print(PC_VERSION)"
if ($LASTEXITCODE -ne 0 -or $next -ne '1.0.4') { throw 'Unexpected successor version for native migration smoke.' }
$root = Join-Path ([System.IO.Path]::GetTempPath()) ("pm-legacy-ci-" + [Guid]::NewGuid())
New-Item -ItemType Directory -Path $root | Out-Null

try {
    git fetch --depth 1 origin refs/tags/pc-v1.0.3
    if ($LASTEXITCODE -ne 0 -or (git rev-parse FETCH_HEAD) -ne '5ec70705de5ed629fa9a12b4960e5bdee4582938') {
        throw 'The exact official v1.0.3 source is unavailable.'
    }
    git archive --format zip --output (Join-Path $root 'old.zip') FETCH_HEAD
    if ($LASTEXITCODE -ne 0) { throw 'Could not extract old tagged source.' }
    $oldSource = Join-Path $root 'old'
    Expand-Archive -LiteralPath (Join-Path $root 'old.zip') -DestinationPath $oldSource

    # Keep old update/download/handoff logic byte-for-byte. Only inject the
    # trusted catalog and offline response at the not-yet-public download boundary.
    @'
from hashlib import file_digest
import os
from pathlib import Path
from PySide6.QtCore import QTimer
import pc_client.app as app
from pc_client import online_updates as online

asset = Path(os.environ["PM_CI_NEXT_EXE"])
with asset.open("rb") as source:
    sha = file_digest(source, "sha256").hexdigest()
version = "1.0.4"
url = online.release_url("pc", version)
update = online.parse_catalog({"schema": 1, "kind": "pc", "stable": {
    "version": version, "target": "windows-x64", "url": url, "sha256": sha,
    "size": asset.stat().st_size}}, "pc")
assert online._windows_file_version(asset) == (1, 0, 4, 0)
def offline_download(requested, hosts, **kwargs):
    assert requested == url and hosts == online.DOWNLOAD_HOSTS
    return asset.open("rb")
online._open = offline_download

class HarnessWindow(app.Window):
    def __init__(self):
        super().__init__()
        QTimer.singleShot(0, self.run_migration)

    def run_migration(self):
        dialog = app.UpdateDialog(self, None)
        self.update_dialog = dialog
        dialog.checked = {"pc": (update, ""), "firmware": (None, "")}
        dialog._render()
        dialog.show()
        dialog.buttons["pc"].click()

app.Window = HarnessWindow
raise SystemExit(app.main())
'@ | Set-Content -Encoding utf8 (Join-Path $oldSource 'pc_client\__main__.py')
    Push-Location $oldSource
    try {
        & .\tools\build_pc_windows.ps1
        $oldBuilt = (Resolve-Path 'pc_client/build/PowerModule-PC.exe').Path
    } finally { Pop-Location }

    $env:PM_CI_NEXT_EXE = $successor
    $newHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $successor).Hash
    foreach ($previousName in @('PowerModule-PC.exe','PowerModule-PC-v1.0.3.exe')) {
        $location = Join-Path $root ("install-" + [guid]::NewGuid())
        New-Item -ItemType Directory -Path $location | Out-Null
        $previous = Join-Path $location $previousName
        $canonical = Join-Path $location 'PowerModule-PC.exe'
        $shell = New-Object -ComObject WScript.Shell
        $shortcut = $shell.CreateShortcut((Join-Path $location 'PowerModule.lnk'))
        $shortcut.TargetPath = $canonical
        $shortcut.Save()
        Copy-Item -LiteralPath $oldBuilt -Destination $previous
        $proc = Start-Process $previous -PassThru
        $deadline = [DateTime]::UtcNow.AddSeconds(110)
        $found = $false
        try {
            while ([DateTime]::UtcNow -lt $deadline) {
                if ((Test-Path -LiteralPath $canonical) -and
                    (Get-FileHash -Algorithm SHA256 -LiteralPath $canonical).Hash -eq $newHash) {
                    $found = $true
                    break
                }
                Start-Sleep -Milliseconds 200
            }
            if (-not $found) { throw "Legacy migration did not restore canonical path from $previousName." }
            if ($shell.CreateShortcut((Join-Path $location 'PowerModule.lnk')).TargetPath -ne $canonical) {
                throw 'Desktop shortcut target changed.'
            }
            $marker = Join-Path $location 'canonical-gui.txt'
            $launched = Start-Process $canonical -ArgumentList ('--release-smoke-test "{0}"' -f $marker) -PassThru
            if (-not $launched.WaitForExit(45000) -or $launched.ExitCode -ne 0 -or
                -not (Test-Path -LiteralPath $marker) -or (Get-Content -Raw $marker) -ne '1.0.4') {
                throw "Restored shortcut target does not launch new GUI for $previousName."
            }
            Write-Output "Canonical shortcut verified for $previousName"
        } finally {
            Get-Process -Name 'PowerModule-PC*' -ErrorAction SilentlyContinue | Where-Object {
                $_.Path -and $_.Path.StartsWith($location + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)
            } | Stop-Process -Force -ErrorAction SilentlyContinue
        }
    }
} finally {
    Remove-Item Env:PM_CI_NEXT_EXE -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
}
