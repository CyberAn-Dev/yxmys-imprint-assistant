param(
    [string]$Python = (Join-Path $PSScriptRoot '..\.venv\Scripts\python.exe')
)

$ErrorActionPreference = 'Stop'
$project = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$source = Join-Path $project 'src'
$dist = Join-Path $project 'dist'
$work = Join-Path $project 'build'

& $Python -m PyInstaller --noconfirm --onedir --windowed `
    --name imprint_assistant --paths $source --distpath $dist `
    --workpath $work --specpath $work `
    --add-data "$(Join-Path $project 'default.yaml');config" `
    --add-data "$(Join-Path $source 'imprint_decompose\default.yaml');imprint_decompose" `
    --add-data "$(Join-Path $source 'imprint_decompose\element_icons');imprint_decompose\element_icons" `
    (Join-Path $source 'imprint_decompose_entry.py')
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }

$pythonBase = (& $Python -c 'import sys; print(sys.base_prefix)').Trim()
$libraries = Join-Path $pythonBase 'Library\bin'
$internal = Join-Path $dist 'imprint_assistant\_internal'
foreach ($name in 'ffi.dll', 'tcl86t.dll', 'tk86t.dll') {
    Copy-Item -LiteralPath (Join-Path $libraries $name) -Destination $internal -Force
}

Write-Output (Join-Path $dist 'imprint_assistant\imprint_assistant.exe')
