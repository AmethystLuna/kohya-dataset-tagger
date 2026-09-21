<#
.SYNOPSIS
  Initialize Kohya Dataset Tagger's Python environment (idempotent, safe to re-run).

.DESCRIPTION
  Does four things: create/reuse the venv -> install base dependencies -> install the onnxruntime variant chosen for the GPU -> editable-install this package.
  Finally it self-checks and prints a one-line conclusion.

  onnxruntime's three variants (onnxruntime / onnxruntime-gpu / onnxruntime-directml) are **mutually exclusive**;
  the script uninstalls all three before installing the chosen one, so re-running is safe and never leaves two conflicting runtimes behind.

  Index profiles (-Index) -- every pip call passes an explicit --index-url and never falls back to global pip config:
    pypi  standard profile: only the official https://pypi.org/simple (the default for setup_env.bat)
    cn    China profile: USTC -> Aliyun -> pypi.org, in fallback order (setup_env_cn.bat uses it)
  The environment variable KOHYA_TAGGER_PIP_INDEX=https://a/simple,https://b/simple replaces the whole profile (comma-separated).

.PARAMETER Gpu
  auto     (default) install the CUDA build if there is an NVIDIA card; otherwise the DirectML build on Windows; otherwise the CPU build
  cuda      onnxruntime-gpu + a set of nvidia-* runtime libraries (about 700 MB; no system CUDA Toolkit needed)
  directml  onnxruntime-directml (about 50 MB, Windows only, works with any GPU vendor, no CUDA/cuDNN needed)
  cpu       onnxruntime (about 15 MB)

.PARAMETER Index
  pypi  official pypi.org (default, standard profile).
  cn    domestic mirrors: USTC -> Aliyun -> official pypi.org, moving on only when the previous one fails.

.PARAMETER DryRun
  Install nothing, only print the choices that would be executed to stdout (DRY_RUN_GPU / DRY_RUN_PIP / DRY_RUN_INDEX),
  then exit 0. It will **not** create a venv, nor perform -Recreate's deletion (the same contract as setup_env.sh --dry-run).

.PARAMETER Recreate
  Delete an existing .venv and rebuild. **This is a deletion** -- the script prints the absolute path and asks for confirmation first.

.EXAMPLE
  .\scripts\setup_env.ps1
  .\scripts\setup_env.ps1 -Gpu directml
  .\scripts\setup_env.ps1 -Index cn -Gpu cuda -Recreate
  .\scripts\setup_env.ps1 -Index cn -Gpu cpu -DryRun
#>
[CmdletBinding()]
param(
    [ValidateSet('auto', 'cuda', 'directml', 'cpu')]
    [string]$Gpu = 'auto',
    [ValidateSet('pypi', 'cn')]
    [string]$Index = 'pypi',
    [string]$Python = 'python',
    [string]$VenvPath,
    [switch]$Recreate,
    [switch]$Yes,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
if (-not $VenvPath) { $VenvPath = Join-Path $RepoRoot '.venv' }

function Step($msg)  { Write-Host "==> $msg" -ForegroundColor Cyan }
function Ok($msg)    { Write-Host "    $msg" -ForegroundColor Green }
function Warn($msg)  { Write-Host "    $msg" -ForegroundColor Yellow }

# ---------------------------------------------------------------- resolve the GPU
function Resolve-GpuChoice([string]$Requested) {
    if ($Requested -ne 'auto') { return $Requested }
    # DirectML by default on Windows: about 50 MB, goes through D3D12, works with any GPU vendor, no CUDA/cuDNN version matching.
    # The CUDA build pulls an extra ~700 MB of nvidia-* runtime libraries, and **on this machine that download stalls** (25 minutes, zero bytes),
    # so it is an explicit option (-Gpu cuda), not the default.
    # On this machine DirectML measured 5.4x faster than CPU (0.275 vs 1.492 s/image), so defaulting to it pays off.
    if ($IsWindows -or $env:OS -eq 'Windows_NT') { return 'directml' }
    $smi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
    if ($smi) {
        try {
            $null = & nvidia-smi -L 2>$null
            if ($LASTEXITCODE -eq 0) { return 'cuda' }
        } catch { }
    }
    return 'cpu'
}

$choice = Resolve-GpuChoice $Gpu

$BasePackages = @('fastapi', 'uvicorn', 'pillow', 'numpy', 'pytest', 'httpx')
switch ($choice) {
    'cuda'     { $PlannedOrt = @('onnxruntime-gpu') }
    'directml' { $PlannedOrt = @('onnxruntime-directml') }
    default    { $PlannedOrt = @('onnxruntime') }
}

# ---------------------------------------------------------------- index profiles
# Index profile -> an ordered list of index-urls, see .DESCRIPTION.
# On 2026-09-19 a local Range probe measured onnxruntime-gpu (245 MB): USTC 8.4-11 MB/s,
# Aliyun 0.86-1.25 MB/s, pypi.org 0.55-1.01 MB/s; TUNA's simple index page returns 200,
# but a direct request for the wheel file returns 403 (probably hotlink protection) -- so the cn profile leads with USTC and falls back to the official source.
function Get-IndexUrls([string]$Profile) {
    switch ($Profile) {
        'cn' {
            @(
                'https://mirrors.ustc.edu.cn/pypi/simple',
                'https://mirrors.aliyun.com/pypi/simple',
                'https://pypi.org/simple'
            )
        }
        default { @('https://pypi.org/simple') }
    }
}
$IndexUrls = @(Get-IndexUrls $Index)
if ($env:KOHYA_TAGGER_PIP_INDEX) {
    $IndexUrls = @($env:KOHYA_TAGGER_PIP_INDEX -split ',' | Where-Object { $_ })
}
if ($IndexUrls.Count -eq 0) {
    throw "No usable index-url (both -Index $Index and KOHYA_TAGGER_PIP_INDEX are empty)"
}

# ---------------------------------------------------------------- -DryRun
# Only print the choices that would be executed to stdout (DRY_RUN_* lines, the same contract as scripts/setup_env.sh --dry-run);
# not a single byte is written: no venv is created, let alone -Recreate's deletion.
if ($DryRun) {
    Write-Output ("DRY_RUN_GPU=" + $choice)
    foreach ($pkg in @($BasePackages + $PlannedOrt)) {
        Write-Output ("DRY_RUN_PIP=" + $pkg)
    }
    foreach ($url in $IndexUrls) {
        Write-Output ("DRY_RUN_INDEX=" + $url)
    }
    exit 0
}

# ---------------------------------------------------------------- venv
Step "Python environment: $VenvPath"
if (Test-Path $VenvPath) {
    if ($Recreate) {
        # Directory deletion is irreversible: print the absolute path first, then ask for confirmation
        $full = (Resolve-Path $VenvPath).Path
        Write-Host "    About to delete: $full" -ForegroundColor Yellow
        if (-not $full.EndsWith('.venv') -and -not $full.EndsWith('venv')) {
            throw "Refusing to delete: the target is not a venv directory ($full)"
        }
        if (-not $Yes) {
            $answer = Read-Host "    Delete it? (y/N)"
            if ($answer -ne 'y') { throw "Cancelled" }
        }
        Remove-Item -Recurse -Force $full
        Ok "Deleted the old venv"
    } else {
        Ok "Already exists; reusing it (add -Recreate to rebuild)"
    }
}
if (-not (Test-Path (Join-Path $VenvPath 'Scripts\python.exe'))) {
    Step "Creating the venv"
    & $Python -m venv $VenvPath
    if ($LASTEXITCODE -ne 0) { throw "Failed to create the venv (cannot find $Python?)" }
}
$py = Join-Path $VenvPath 'Scripts\python.exe'
Ok (& $py --version)

# Try each index in order; move to the next only when the previous one fails (cannot connect / 404 / connection cut).
# An explicit --index-url throughout: **never** fall back to global pip config (that is exactly what "looks like a hang" comes from).
function Invoke-PipInstall {
    param([Parameter(Mandatory)][string[]]$PipArgs)
    foreach ($url in $IndexUrls) {
        & $py -m pip install --index-url $url @PipArgs
        if ($LASTEXITCODE -eq 0) { return }
        Warn "Install from index $url failed, trying the next one"
    }
    throw "All indexes failed (tried: $($IndexUrls -join ' -> '))"
}

# ---------------------------------------------------------------- dependencies
Step "Upgrading pip"
Invoke-PipInstall @('-q', '--upgrade', 'pip')

Step "Installing base dependencies"
Invoke-PipInstall (@('-q') + $BasePackages)

# ---------------------------------------------------------------- onnxruntime
$ortPackages = @('onnxruntime', 'onnxruntime-gpu', 'onnxruntime-directml')
Step "Configuring onnxruntime (chosen: $choice)"
& $py -m pip uninstall -y @ortPackages 2>&1 | Out-Null

switch ($choice) {
    'cuda' {
        Warn "CUDA build: onnxruntime-gpu is about 245 MB; if this machine has no torch(cu12x), an extra set of nvidia-* runtime libraries is pulled too."
        Invoke-PipInstall @('onnxruntime-gpu')
        # The CUDA EP needs the cuDNN 9.* / CUDA 12.* runtime DLLs. The approach **that actually works** is to put an existing torch's
        # lib directory on PATH -- note that os.add_dll_directory **does not work**, because ORT's provider DLL
        # is statically imported and goes through the legacy search path.
        # Any torch that lives in another environment (the trainer's venv, a ComfyUI or webui install) is
        # pointed at with KOHYA_TAGGER_TORCH_LIB=<...>\Lib\site-packages\torch\lib, several separated by ';'.
        # Nothing machine-specific is hardcoded here: what used to be two literal paths is now one variable.
        $torchLibs = @((Join-Path $VenvPath 'Lib\site-packages\torch\lib'))
        if ($env:KOHYA_TAGGER_TORCH_LIB) { $torchLibs += $env:KOHYA_TAGGER_TORCH_LIB -split ';' }
        $torchLibs = $torchLibs | Where-Object { Test-Path (Join-Path $_ 'cublasLt64_12.dll') }
        if ($torchLibs.Count -gt 0) {
            Ok ('Found a usable CUDA runtime; just add its lib directory to PATH: ' + $torchLibs[0])
            $env:PATH = $torchLibs[0] + ';' + $env:PATH
            Ok 'Set for this session (a new terminal must set it itself, or write that line into your start script)'
        } else {
            Warn "No torch with cublasLt64_12.dll was found (set KOHYA_TAGGER_TORCH_LIB to point at one); installing the nvidia-* runtime libraries instead."
            Invoke-PipInstall @('nvidia-cudnn-cu12', 'nvidia-cublas-cu12', 'nvidia-cuda-runtime-cu12', 'nvidia-cufft-cu12', 'nvidia-curand-cu12')
        }
    }
    'directml' {
        Warn "DirectML build: about 50 MB, goes through D3D12, no CUDA/cuDNN needed."
        Invoke-PipInstall @('onnxruntime-directml')
    }
    default {
        Invoke-PipInstall @('onnxruntime')
    }
}

# ---------------------------------------------------------------- this package
Step "Editable install of this package (--no-deps, leaving the pinned versions above untouched)"
Push-Location $RepoRoot
try {
    Invoke-PipInstall @('-e', '.', '--no-deps', '-q')
} finally { Pop-Location }
Ok "kohya_dataset_tagger is importable"

# ---------------------------------------------------------------- self-check
Step "Self-check (build a session with a real model and look at the provider **actually** in use)"
if (Test-Path (Join-Path $RepoRoot 'tools\check_provider.py')) {
    & $py (Join-Path $RepoRoot 'tools\check_provider.py')
    if ($LASTEXITCODE -ne 0) {
        Warn "The requested provider is not really in effect. onnxruntime **silently falls back to CPU**"
        Warn "when a GPU provider fails to load -- inference still runs, just about 5x slower; you will not notice without actively checking."
    }
} else {
    Warn "(no tools/check_provider.py; skipping the real-session check)"
}

& $py -c @"
import sys
import onnxruntime as ort
import kohya_dataset_tagger as pkg
providers = ort.get_available_providers()
print('    python      :', sys.version.split()[0])
print('    package     :', pkg.__version__)
print('    onnxruntime :', ort.__version__)
print('    providers   :', providers)
gpu = [p for p in providers if p in ('CUDAExecutionProvider', 'DmlExecutionProvider')]
if gpu:
    print('    GPU         : available ->', gpu[0])
else:
    print('    GPU         : unavailable, will use CPU')
"@
if ($LASTEXITCODE -ne 0) { throw "Self-check failed" }

Step "Done"
Write-Host "    Start service: $py -m kohya_dataset_tagger --port 3001" -ForegroundColor Green
Write-Host "    Run tests:     $py -m pytest test/ -q" -ForegroundColor Green
Write-Host "    Acceptance:    $py -m pytest acceptance/ -q" -ForegroundColor Green
