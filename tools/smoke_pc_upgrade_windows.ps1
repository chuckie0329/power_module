$ErrorActionPreference = 'Stop'
Set-Location (Resolve-Path (Join-Path $PSScriptRoot '..'))
$exe = (Resolve-Path 'pc_client/build/PowerModule-PC.exe').Path
$version = python -c "from pc_client.version import PC_VERSION; print(PC_VERSION)"
if ($LASTEXITCODE -ne 0) { throw 'Cannot read client version.' }

foreach ($mode in @('success', 'failure')) {
    $folder = Join-Path ([System.IO.Path]::GetTempPath()) ("pm-ci-handoff-" + [Guid]::NewGuid())
    New-Item -ItemType Directory -Path $folder | Out-Null
    $old = Join-Path $folder 'PowerModule-PC.exe'
    $staging = Join-Path $folder ".PowerModule-download-ci"
    New-Item -ItemType Directory -Path $staging | Out-Null
    $new = Join-Path $staging "PowerModule-PC-v$version.exe"
    Copy-Item -LiteralPath $exe -Destination $old
    Copy-Item -LiteralPath $exe -Destination $new
    New-Item -ItemType File -Path (Join-Path $folder 'smoke-fixture') | Out-Null
    New-Item -ItemType File -Path (Join-Path $folder 'unrelated.exe') | Out-Null
    $env:POWER_MODULE_UPGRADE_SMOKE = $mode
    $process = Start-Process $old -ArgumentList '--release-upgrade-smoke' -PassThru
    try {
        if (-not $process.WaitForExit(80000) -or $process.ExitCode -ne 0) {
            throw "Predecessor did not finish its $mode upgrade smoke."
        }
        $marker = Join-Path $folder 'result.txt'
        $deadline = [DateTime]::UtcNow.AddSeconds(95)
        while (-not (Test-Path -LiteralPath $marker) -and [DateTime]::UtcNow -lt $deadline) {
            Start-Sleep -Milliseconds 100
        }
        $result = Get-Content -LiteralPath $marker -Raw
        $expected = if ($mode -eq 'success') { 'success-replaced-old' } else { 'failure-restored-old' }
        if ($result -ne $expected -or -not (Test-Path -LiteralPath $old) -or
            -not (Test-Path -LiteralPath $new) -or -not (Test-Path -LiteralPath (Join-Path $folder 'unrelated.exe'))) {
            throw "Unexpected $mode upgrade smoke result: $result"
        }
    } finally {
        if (-not $process.HasExited) { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue }
        Remove-Item Env:POWER_MODULE_UPGRADE_SMOKE -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 800
        Remove-Item -LiteralPath $folder -Recurse -Force -ErrorAction SilentlyContinue
    }
}
