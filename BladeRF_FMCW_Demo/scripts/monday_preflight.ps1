param([string]$Config = "configs/loopback_safe.yaml", [int]$Blocks = 100, [switch]$Metadata)
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
$python = Join-Path $PWD ".venv/Scripts/python.exe"
& $python scripts/validate_offline.py
if ($LASTEXITCODE -ne 0) { throw "Offline tests FAIL; stop" }
& $python -m sww.cli plan --config $Config --dry-run
if ($LASTEXITCODE -ne 0) { throw "Plan FAIL; stop" }
& $python -m sww.cli probe
if ($LASTEXITCODE -ne 0) { throw "Probe FAIL/BLOCKED; stop" }
$rxArgs = @("-m", "sww.cli", "rx-selftest", "--config", $Config, "--blocks", "$Blocks")
if ($Metadata) { $rxArgs += "--metadata" }
& $python @rxArgs
if ($LASTEXITCODE -ne 0) { throw "RX FAIL/BLOCKED; stop" }
Write-Output "Preflight ended. No TX command was executed. Phase gate remains BLOCKED."
