param(
    [string]$Python = ''
)

$ErrorActionPreference = 'Stop'
$project = (Resolve-Path -LiteralPath $PSScriptRoot).Path
if (-not $Python) {
    $localVenv = Join-Path $project '.venv\Scripts\python.exe'
    $Python = if (Test-Path -LiteralPath $localVenv) { $localVenv } else { 'python' }
}
& $Python -c "import sys; assert sys.version_info[:2] == (3, 11), 'Python 3.11 is required by the legacy bytecode modules'"
if ($LASTEXITCODE -ne 0) { throw 'Unsupported Python; install requirements-build.lock in Python 3.11.' }

$source = Join-Path $project 'src'
$versionFile = Join-Path $source 'imprint_decompose\__init__.py'
$match = [regex]::Match((Get-Content -LiteralPath $versionFile -Raw), '__version__\s*=\s*["'']([^"'']+)["'']')
if (-not $match.Success) { throw 'Version number was not found.' }
$version = $match.Groups[1].Value
$release = Join-Path $project 'release'
$work = Join-Path $project 'build\work'
$stage = Join-Path $project 'build\stage'
# Keep the artifact filename ASCII; the application title remains Chinese.
$name = 'yxmys-imprint-assistant'
$icon = Join-Path $project 'assets\app_icon.ico'
$coffeeQr = Join-Path $project 'assets\wechat_pay.jpg'
$hooks = Join-Path $project 'packaging_hooks'
New-Item -ItemType Directory -Force $release | Out-Null
New-Item -ItemType Directory -Force $work,$stage | Out-Null
$previousPythonPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = $source
    Push-Location $project
    try { & $Python -B -m unittest discover -s tests -v } finally { Pop-Location }
    if ($LASTEXITCODE -ne 0) { throw 'Regression tests failed; release unchanged.' }
} finally { $env:PYTHONPATH = $previousPythonPath }

# Conda's _ctypes.pyd loads ffi.dll at runtime. PyInstaller does not always
# discover it, especially for one-file builds, so include it when present.
$pythonBase = (& $Python -c 'import sys; print(sys.base_prefix)').Trim()
$extraBinaries = @()
$ffiCandidates = @(
    (Join-Path $pythonBase 'Library\bin\ffi.dll'),
    (Join-Path $pythonBase 'DLLs\libffi-8.dll'),
    (Join-Path $pythonBase 'DLLs\libffi-7.dll')
)
foreach ($candidate in $ffiCandidates) {
    if (Test-Path -LiteralPath $candidate) {
        $extraBinaries += @('--add-binary', "$candidate;.")
        break
    }
}
foreach ($dllName in @('tcl86t.dll', 'tk86t.dll')) {
    $candidate = Join-Path $pythonBase "Library\bin\$dllName"
    if (Test-Path -LiteralPath $candidate) {
        $extraBinaries += @('--add-binary', "$candidate;.")
    }
}

& $Python -m PyInstaller @extraBinaries --noconfirm --onefile --windowed `
    --name $name --icon $icon --paths $source --distpath $stage `
    --workpath $work --specpath $work `
    --additional-hooks-dir $hooks `
    --exclude-module PIL.AvifImagePlugin --exclude-module PIL._avif `
    --exclude-module PIL.WebPImagePlugin --exclude-module PIL._webp `
    --add-data "$(Join-Path $project 'config\default.yaml');config" `
    --add-data "$(Join-Path $source 'imprint_decompose\default.yaml');imprint_decompose" `
    --add-data "$(Join-Path $source 'imprint_decompose\game_digit_templates.npz');imprint_decompose" `
    --add-data "$(Join-Path $source 'imprint_decompose\element_icons');imprint_decompose\element_icons" `
    --add-data "$(Join-Path $source 'imprint_decompose\models');imprint_decompose\models" `
    --add-data "$icon;assets" `
    --add-data "$coffeeQr;assets" `
    (Join-Path $source 'imprint_decompose_entry.py')
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }

$artifact = Join-Path $stage "$name.exe"
$check = Join-Path $stage 'self-test.json'
if (Test-Path -LiteralPath $check) { Remove-Item -LiteralPath $check }
$process = Start-Process -FilePath $artifact -ArgumentList @('--self-test', "`"$check`"") -WindowStyle Hidden -PassThru
if (-not $process.WaitForExit(45000)) {
    Stop-Process -InputObject $process -Force
    throw 'Packaged self-test startup timed out; release unchanged. Check execution restrictions before retrying.'
}
$process.Refresh()
if ($process.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $check)) {
    if (Test-Path -LiteralPath $check) { Get-Content -LiteralPath $check -Raw | Write-Output }
    throw 'Packaged self-test failed; release unchanged.'
}
$result = Get-Content -LiteralPath $check -Raw | ConvertFrom-Json
if (-not $result.success) { throw "Packaged self-test failed: $($result.error)" }
$published = Join-Path $release "$name-v$version.exe"
Copy-Item -LiteralPath $artifact -Destination "$published.pending" -Force
Move-Item -LiteralPath "$published.pending" -Destination $published -Force
$manifest = [ordered]@{version=$version; commit=(& git -C $project rev-parse HEAD); sourceDirty=[bool](& git -C $project status --porcelain); sha256=(Get-FileHash -LiteralPath $published -Algorithm SHA256).Hash; bytes=(Get-Item -LiteralPath $published).Length; selfTest=$result; builtAt=(Get-Date).ToUniversalTime().ToString('o')}
$manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $release 'release-info.json') -Encoding utf8
Get-Item -LiteralPath $published | Select-Object FullName,Length
