# One-time setup on Windows: venv, packages, MAVERIC data, twin training.
#   powershell -ExecutionPolicy Bypass -File scripts\setup_env.ps1 [-Python <path to python 3.10-3.12>] [-SkipRl]
param(
  [string]$Python = "",
  [switch]$SkipRl
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$maveric = Join-Path (Split-Path -Parent $root) "maveric-1.0.0\maveric-1.0.0"
if (-not (Test-Path $maveric)) { throw "MAVERIC not found at $maveric (set MAVERIC_ROOT or unzip maveric-1.0.0.zip there)" }

if (-not (Test-Path .venv)) {
  if ($Python) { uv venv .venv --python $Python } else { uv venv .venv --python 3.12 }
}
uv pip install --python .venv\Scripts\python.exe --index-url https://download.pytorch.org/whl/cpu `
  --extra-index-url https://pypi.org/simple --index-strategy unsafe-best-match -r requirements.txt

if (-not (Test-Path twin_data\energy_saving_data)) {
  New-Item -ItemType Directory -Force twin_data | Out-Null
  tar -xf (Join-Path $maveric "notebooks\data\energy_saving_data.zip") -C twin_data --exclude "__MACOSX*"
}
New-Item -ItemType Directory -Force twin_data\models | Out-Null
.venv\Scripts\python.exe -m twin.train_twin --preprocess --bdt
if (-not $SkipRl) { .venv\Scripts\python.exe -W ignore -m twin.train_twin --rl --timesteps 25000 }
Write-Host "Done. Start with: .venv\Scripts\python.exe run_demo.py"
