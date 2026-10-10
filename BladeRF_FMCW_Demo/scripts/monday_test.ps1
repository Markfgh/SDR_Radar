param(
    [Parameter(Mandatory=$true)][string]$Session,
    [string]$Run,
    [ValidateSet("offline","probe","rx","reference_pre","fixed","aba","sweep","reference_post","validate","stack")]
    [string]$Through = "rx",
    [switch]$DryRun,
    [switch]$Resume,
    [switch]$ConfirmTx,
    [string[]]$AuthorizeTx = @(),
    [string]$Permits
)
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
$python = Join-Path $PWD ".venv/Scripts/python.exe"
$benchArgs = @("-m", "sww.bench", "--session", $Session, "--through", $Through)
if ($Run) { $benchArgs += @("--run", $Run) }
if ($DryRun) { $benchArgs += "--dry-run" }
if ($Resume) { $benchArgs += "--resume" }
if ($ConfirmTx) { $benchArgs += "--confirm-tx" }
if ($AuthorizeTx.Count) { $benchArgs += "--authorize-tx"; $benchArgs += $AuthorizeTx }
if ($Permits) { $benchArgs += @("--permits", $Permits) }
& $python @benchArgs
exit $LASTEXITCODE
