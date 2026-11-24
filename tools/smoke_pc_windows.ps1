$ErrorActionPreference = 'Stop'
Set-Location (Resolve-Path (Join-Path $PSScriptRoot '..'))
$exe = (Resolve-Path 'pc_client/build/PowerModule-PC.exe').Path
$version = python -c "from pc_client.version import PC_VERSION; print(PC_VERSION)"
if ($LASTEXITCODE -ne 0) { throw 'Cannot identify the client version.' }
$versionInfo = (Get-Item $exe).VersionInfo
if ($versionInfo.FileVersion -ne $version -or $versionInfo.ProductVersion -ne $version) {
    throw 'Wrong Windows version metadata.'
}
python -c "import pefile,sys; p=pefile.PE(sys.argv[1]); assert p.FILE_HEADER.Machine == 0x8664; assert p.OPTIONAL_HEADER.Subsystem == 2; assert {3,14,16} <= {r.id for r in p.DIRECTORY_ENTRY_RESOURCE.entries}" $exe
if ($LASTEXITCODE -ne 0) { throw 'Wrong binary architecture, subsystem, icon or version resource.' }
$marker = (Join-Path (Get-Location).Path '.work/frozen-smoke.txt')
Remove-Item -ErrorAction SilentlyContinue $marker
$process = Start-Process $exe -ArgumentList ('--release-smoke-test "{0}"' -f $marker) -PassThru
if (-not $process.WaitForExit(45000)) {
    Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
    throw 'Frozen Windows client did not finish the GUI smoke test.'
}
if ($process.ExitCode -ne 0 -or -not (Test-Path $marker) -or (Get-Content -Raw $marker) -ne $version) {
    if (Test-Path $marker) { Get-Content -Raw $marker }
    throw 'Frozen Windows client failed GUI startup, compact geometry, or BLE imports.'
}

$catalogMarker = (Join-Path (Get-Location).Path '.work/catalog-smoke.txt')
Remove-Item -ErrorAction SilentlyContinue $catalogMarker
$catalog = Start-Process $exe -ArgumentList ('--release-catalog-smoke "{0}"' -f $catalogMarker) -PassThru
if (-not $catalog.WaitForExit(120000)) {
    Stop-Process -Id $catalog.Id -Force -ErrorAction SilentlyContinue
    throw 'Frozen Windows client timed out reading online update catalogs.'
}
if ($catalog.ExitCode -ne 0 -or -not (Test-Path $catalogMarker) -or (Get-Content -Raw $catalogMarker) -notmatch '^\d+\.\d+\.\d+ \d+\.\d+\.\d+$') {
    if (Test-Path $catalogMarker) { Get-Content -Raw $catalogMarker }
    throw 'Frozen Windows client failed to resolve both live catalogs or check the public firmware asset.'
}
Get-Content -Raw $catalogMarker
