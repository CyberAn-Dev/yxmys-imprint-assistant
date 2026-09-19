param(
    [string]$Python = ''
)

$ErrorActionPreference = 'Stop'
$project = (Resolve-Path -LiteralPath $PSScriptRoot).Path
if (-not $Python) {
    $nearbyVenv = Join-Path $project '..\yxmys\.venv\Scripts\python.exe'
    $Python = if (Test-Path -LiteralPath $nearbyVenv) { $nearbyVenv } else { 'python' }
}

$source = Join-Path $project 'src'
$versionFile = Join-Path $source 'imprint_decompose\__init__.py'
$match = [regex]::Match((Get-Content -LiteralPath $versionFile -Raw), '__version__\s*=\s*["'']([^"'']+)["'']')
if (-not $match.Success) { throw 'Version number was not found.' }
$version = $match.Groups[1].Value
$release = Join-Path $project 'release'
$work = Join-Path $project 'build'
$name = "yxmys-刻印快速筛选分解小助手-v$version"
$icon = Join-Path $project 'assets\app_icon.ico'
$hooks = Join-Path $project 'packaging_hooks'
New-Item -ItemType Directory -Force $release | Out-Null

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
    --name $name --icon $icon --paths $source --distpath $release `
    --workpath $work --specpath $work `
    --additional-hooks-dir $hooks `
    --add-data "$(Join-Path $project 'config\default.yaml');config" `
    --add-data "$(Join-Path $source 'imprint_decompose\default.yaml');imprint_decompose" `
    --add-data "$(Join-Path $source 'imprint_decompose\element_icons');imprint_decompose\element_icons" `
    --add-data "$icon;assets" `
    (Join-Path $source 'imprint_decompose_entry.py')
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }

Write-Output (Join-Path $release "$name.exe")
