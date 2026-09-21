<#
.SYNOPSIS
  Start Kohya Dataset Tagger.

.DESCRIPTION
  Three things are prepared before starting the service: confirm the venv exists, determine the dataset roots, pick an unoccupied port;
  then wait for the service to be ready and open the browser automatically. The service runs in the foreground; Ctrl+C quits.

  Where the dataset roots come from (in priority order):
    1. the -Roots parameter
    2. the KOHYA_TAGGER_ROOTS environment variable (semicolon-separated)
    3. roots.txt at the repository root (the first run asks once and remembers it)
    4. if none of those, ask once interactively

.PARAMETER DryRun
  Just print the command line that would run (DRY_RUN_PYTHON / DRY_RUN_PORT / DRY_RUN_ARG) and exit 0,
  without starting the service, opening the browser, or asking "initialize the environment first?".
  The format is **exactly the same** as scripts/start.sh --dry-run, so argument assembly can be asserted on Windows too (p0-spec §7 "cross-platform launcher" / A32).

.EXAMPLE
  .\start.bat
  .\scripts\start.ps1 -Roots "D:\datasets\my-lora" -Port 3010
  .\scripts\start.ps1 -DryRun
#>
[CmdletBinding()]
param(
    [string[]]$Roots,
    [int]$Port = 3001,
    [switch]$NoBrowser,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$VenvPy = Join-Path $RepoRoot '.venv\Scripts\python.exe'
$RootsFile = Join-Path $RepoRoot 'roots.txt'
$ModelPathsFile = Join-Path $RepoRoot 'model_paths.txt'
$NL = [Environment]::NewLine

function Say($m)  { Write-Host $m }
function Ok($m)   { Write-Host "    $m" -ForegroundColor Green }
function Warn($m) { Write-Host "    $m" -ForegroundColor Yellow }

# ---------------------------------------------------------------- 1. venv
if (-not (Test-Path $VenvPy)) {
    Warn "No .venv found ($VenvPy)"
    if ($DryRun) { throw "Run setup_env.bat first (-DryRun will not initialize the environment for you)" }
    Say  "    Initialization installs fastapi / pillow / numpy / onnxruntime and so on, about 250 MB, taking a few minutes."
    Say  "    On a China network use setup_env_cn.bat (same implementation + -Index cn, going through domestic mirrors)."
    $answer = Read-Host "    Run scripts\setup_env.ps1 to initialize now? (Y/n)"
    if ($answer -ne 'n') {
        & (Join-Path $PSScriptRoot 'setup_env.ps1')
        if (-not (Test-Path $VenvPy)) { throw "Still no venv after initialization; stopping" }
    } else {
        throw "Run setup_env.bat first"
    }
}

# ---------------------------------------------------------------- 2. dataset roots
if (-not $Roots -or $Roots.Count -eq 0) {
    if ($env:KOHYA_TAGGER_ROOTS) {
        $Roots = $env:KOHYA_TAGGER_ROOTS -split ';' | Where-Object { $_.Trim() } | ForEach-Object { $_.Trim() }
    } elseif ((Test-Path $RootsFile) -and (Get-Content $RootsFile -Raw).Trim()) {
        $Roots = (Get-Content $RootsFile) | Where-Object { $_.Trim() } | ForEach-Object { $_.Trim() }
    } else {
        Warn "No dataset root configured yet"
        Say  "    You can enter several, separated by semicolons or commas. For example:"
        Say  "    D:\datasets\my-lora"
        $line = Read-Host "    Dataset root"
        if (-not $line.Trim()) { throw "Without a dataset root we cannot start" }
        $Roots = $line -split '[;,]' | Where-Object { $_.Trim() } | ForEach-Object { $_.Trim() }
        $Roots | Set-Content -Path $RootsFile -Encoding UTF8
        Ok "Remembered, written to $RootsFile (no need to enter it again next time; edit it to change)"
    }
}

$bad = @($Roots | Where-Object { -not (Test-Path $_ -PathType Container) })
if ($bad.Count -gt 0) {
    throw ("These directories do not exist or are not directories:" + $NL + "    " + ($bad -join ($NL + "    ")))
}

# ---------------------------------------------------------------- 3. port
$chosen = $null
foreach ($p in $Port..($Port + 20)) {
    $busy = $false
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $client.Connect('127.0.0.1', $p)
        $client.Close()
        $busy = $true
    } catch { }
    if (-not $busy) { $chosen = $p; break }
}
if (-not $chosen) { throw "All 20 ports starting at $Port are taken" }
if ($chosen -ne $Port) { Warn "Port $Port is taken, using $chosen instead" }
$url = "http://127.0.0.1:$chosen"

# ---------------------------------------------------------------- 4. command line
$serverArgs = @('-m', 'kohya_dataset_tagger', '--port', "$chosen")
foreach ($r in $Roots) { $serverArgs += @('--roots', $r) }

# -DryRun: print the argv that would be executed, one item per line, and exit 0 without starting the service.
# The format matches scripts/start.sh --dry-run (DRY_RUN_PYTHON / DRY_RUN_PORT / DRY_RUN_ARG).
if ($DryRun) {
    Write-Output ("DRY_RUN_PYTHON=" + $VenvPy)
    Write-Output ("DRY_RUN_PORT=" + $chosen)
    foreach ($a in $serverArgs) { Write-Output ("DRY_RUN_ARG=" + $a) }
    exit 0
}

# ---------------------------------------------------------------- 5. start the service
Say ""
Say "==> Kohya Dataset Tagger"
Say "    Dataset roots:"
$Roots | ForEach-Object { Say "      $_" }
# Extra model scan paths are **not forwarded by this script** (p0-spec §3.7: the server reads model_paths.txt itself).
# Turning it into command-line arguments would mean "deleting a line in the UI" gets overridden by the command line on the next start. Here we only print its location.
if (Test-Path $ModelPathsFile) { Say "    Extra model scan paths: $ModelPathsFile" }
Say "    Address: $url"
Say "    (Ctrl+C to quit)"
Say ""

if (-not $NoBrowser) {
    $null = Start-Job -ScriptBlock {
        param($u)
        for ($i = 0; $i -lt 40; $i++) {
            try {
                Invoke-RestMethod -Uri "$u/api/health" -TimeoutSec 2 | Out-Null
                Start-Process $u
                return
            } catch { Start-Sleep -Milliseconds 500 }
        }
    } -ArgumentList $url
}

& $VenvPy @serverArgs
