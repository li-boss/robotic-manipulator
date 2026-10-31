# Dot-source this file in PowerShell: . .\tools\use-g-training.ps1
# The settings apply only to the current shell, not to Windows globally.
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
if (-not ($projectRoot -like 'G:\*')) {
    throw "Expected the training project on G:, got $projectRoot"
}

$workRoot = Join-Path $projectRoot 'data\a_blocks'
$cacheRoot = Join-Path $workRoot 'cache'
$tempRoot = Join-Path $workRoot 'tmp'
foreach ($path in @($cacheRoot, $tempRoot)) {
    New-Item -ItemType Directory -Path $path -Force | Out-Null
}

$env:TMP = $tempRoot
$env:TEMP = $tempRoot
$env:TMPDIR = $tempRoot
$env:PIP_CACHE_DIR = Join-Path $cacheRoot 'pip'
$env:TORCH_HOME = Join-Path $cacheRoot 'torch'
$env:YOLO_CONFIG_DIR = Join-Path $cacheRoot 'ultralytics'
$env:MPLCONFIGDIR = Join-Path $cacheRoot 'matplotlib'
$env:XDG_CACHE_HOME = $cacheRoot
$env:HF_HOME = Join-Path $cacheRoot 'huggingface'
$env:PIP_DISABLE_PIP_VERSION_CHECK = '1'
foreach ($path in @($env:PIP_CACHE_DIR, $env:TORCH_HOME, $env:YOLO_CONFIG_DIR, $env:MPLCONFIGDIR, $env:HF_HOME)) {
    New-Item -ItemType Directory -Path $path -Force | Out-Null
}

Set-Location -LiteralPath $projectRoot
Write-Host "Project: $projectRoot"
Write-Host "Temporary files: $env:TEMP"
Write-Host "Package/model cache: $cacheRoot"
Write-Host "Training Python: $(Join-Path $workRoot '.venv-train\Scripts\python.exe')"
