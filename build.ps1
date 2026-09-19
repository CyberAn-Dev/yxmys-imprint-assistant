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
New-Item -ItemType Directory -Force $release | Out-Null

& $Python -m PyInstaller --noconfirm --onefile --windowed `
    --name $name --paths $source --distpath $release `
    --workpath $work --specpath $work `
    --add-data "$(Join-Path $project 'config\default.yaml');config" `
    --add-data "$(Join-Path $source 'imprint_decompose\default.yaml');imprint_decompose" `
    --add-data "$(Join-Path $source 'imprint_decompose\element_icons');imprint_decompose\element_icons" `
    (Join-Path $source 'imprint_decompose_entry.py')
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }

Write-Output (Join-Path $release "$name.exe")

