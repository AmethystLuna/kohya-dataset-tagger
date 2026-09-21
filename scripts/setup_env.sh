#!/usr/bin/env bash
# Initialize Kohya Dataset Tagger's Python environment (idempotent, safe to re-run).
# On Windows use setup_env.bat / setup_env_cn.bat at the repository root (= scripts/setup_env.ps1).
#
#   ./setup_env.sh                  # standard profile: official pypi.org (default)
#   ./setup_env_cn.sh               # China profile: USTC -> Aliyun -> pypi.org, in fallback order
#   ./setup_env.sh --index cn       # equivalent to ./setup_env_cn.sh
#   ./setup_env.sh --gpu cpu        # auto | cuda | directml | cpu
#   ./setup_env.sh --gpu cuda -y    # CUDA build: onnxruntime-gpu + nvidia-* runtime libraries
#   ./setup_env.sh --recreate       # delete .venv and rebuild (prints the absolute path and asks for confirmation first)
#   ./setup_env.sh --dry-run        # install nothing, only print the choices that would be executed
#
# Aligned with scripts/setup_env.ps1 (p0-spec §7); these are the pitfalls the ps1 already hit:
#
#   * onnxruntime / onnxruntime-gpu / onnxruntime-directml are **three mutually exclusive variants**: uninstall
#     all three before installing the chosen one, otherwise two conflicting runtimes are left behind, and no error is reported.
#   * onnxruntime **silently falls back to CPU** when a GPU provider fails to load -- so at the end you must
#     build a real session with tools/check_provider.py and look at the provider **actually** in use.
#   * deleting .venv is irreversible: print the absolute path, assert the path really ends in .venv / venv, then confirm.
#
# Index profiles (--index pypi|cn) -- every pip call passes an explicit --index-url and never falls back to global config:
#   pypi  only the official https://pypi.org/simple.
#   cn    domestic mirrors, tried in order, moving on only when the previous one fails:
#           USTC   https://mirrors.ustc.edu.cn/pypi/simple
#           Aliyun https://mirrors.aliyun.com/pypi/simple
#           official https://pypi.org/simple (last-resort fallback, so the cn profile is never worse than the standard one)
#   The environment variable KOHYA_TAGGER_PIP_INDEX=https://a/simple,https://b/simple replaces the whole profile (comma-separated).
#
#   Why fall back instead of pinning one: on 2026-09-19 a local Range probe (8 MB / 12 s)
#   measured this 245 MB onnxruntime-gpu wheel: USTC 8.4-11 MB/s, Aliyun 0.86-1.25 MB/s,
#   pypi.org / files.pythonhosted 0.55-1.01 MB/s; TUNA's simple index page returns 200,
#   but a direct request for the wheel file it hands out returns 403 (probably hotlink protection) -- so TUNA is not in the profile. Mirror speed varies by machine,
#   which is why we "fall back in order" instead of "pinning one".
#
# --dry-run's stdout has **only** these kinds of lines (machine-assertable); every human-facing hint goes to stderr:
#   DRY_RUN_GPU=<cuda|directml|cpu>
#   DRY_RUN_PIP=<dependency packages that would be pip installed, one per line>
#   DRY_RUN_INDEX=<index-urls that would be tried, one per line, in try order>
# (Upgrading pip and `pip install -e . --no-deps` are not a "package choice" and are only explained on stderr.)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$REPO_ROOT/.venv"
GPU="auto"
INDEX_PROFILE="pypi"
PYTHON="${PYTHON:-python3}"
RECREATE=0
ASSUME_YES=0
DRY_RUN=0

BASE_PKGS=(fastapi uvicorn pillow numpy pytest httpx)
ORT_VARIANTS=(onnxruntime onnxruntime-gpu onnxruntime-directml)

step() { printf '\033[36m==> %s\033[0m\n' "$1" >&2; }
ok()   { printf '    %s\n' "$1" >&2; }
warn() { printf '    %s\n' "$1" >&2; }
say()  { printf '%s\n' "$1" >&2; }

# Index profile -> an ordered list of index-urls, one per line. See "Index profiles" at the top of the file.
index_urls() {
  case "$1" in
    pypi) printf '%s\n' "https://pypi.org/simple" ;;
    cn)   printf '%s\n' \
            "https://mirrors.ustc.edu.cn/pypi/simple" \
            "https://mirrors.aliyun.com/pypi/simple" \
            "https://pypi.org/simple" ;;
  esac
}

usage() {
  cat >&2 <<'USAGE'
Usage: ./setup_env.sh [options]

  --index pypi|cn    index profile used for package installs, default pypi
        pypi   official pypi.org (standard profile; the default for setup_env.sh)
        cn     domestic mirrors USTC -> Aliyun -> pypi.org, in fallback order (setup_env_cn.sh uses it)
  --gpu auto|cuda|directml|cpu   default auto
        auto         nvidia-smi exists and works -> cuda; on Windows -> directml; otherwise cpu
        cuda         onnxruntime-gpu + a set of nvidia-* runtime libraries (about 700 MB)
        directml     onnxruntime-directml (about 50 MB, **Windows only**)
        cpu          onnxruntime (about 15 MB)
  --python CMD      python used to create the venv (default $PYTHON, then python3)
  --venv PATH       venv location (default <repository>/.venv)
  --recreate        delete an existing venv and rebuild (prints the absolute path and asks for confirmation first)
  -y, --yes         skip the --recreate confirmation (for automation)
  --dry-run         install nothing, only print the choices that would be executed to stdout (DRY_RUN_* lines)
  -h, --help        show this help

The environment variable KOHYA_TAGGER_PIP_INDEX=<url>[,<url>...] overrides the index profile above (fallback in order).
USAGE
}

# ---------------------------------------------------------------- arguments
while [[ $# -gt 0 ]]; do
  case "$1" in
    --gpu)     GPU="${2:?--gpu must be followed by a variant}"; shift 2 ;;
    --gpu=*)   GPU="${1#*=}"; shift ;;
    --index)   INDEX_PROFILE="${2:?--index must be followed by pypi or cn}"; shift 2 ;;
    --index=*) INDEX_PROFILE="${1#*=}"; shift ;;
    --python)  PYTHON="${2:?--python must be followed by a command}"; shift 2 ;;
    --python=*) PYTHON="${1#*=}"; shift ;;
    --venv)    VENV="${2:?--venv must be followed by a path}"; shift 2 ;;
    --venv=*)  VENV="${1#*=}"; shift ;;
    --recreate) RECREATE=1; shift ;;
    -y|--yes)  ASSUME_YES=1; shift ;;
    --dry-run|-n) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) say "Unknown argument: $1"; usage; exit 2 ;;
  esac
done

case "$GPU" in
  auto|cuda|directml|cpu) ;;
  *) say "Unknown --gpu: $GPU (choose auto | cuda | directml | cpu)"; exit 2 ;;
esac

case "$INDEX_PROFILE" in
  pypi|cn) ;;
  *) say "Unknown --index: $INDEX_PROFILE (choose pypi | cn)"; exit 2 ;;
esac

# Ordered index-url list; KOHYA_TAGGER_PIP_INDEX (comma-separated) replaces the whole profile.
# Deliberately not using mapfile -- the bash 3.2 shipped with macOS does not have it (see gap 6 in current-state).
INDEXES=()
if [[ -n "${KOHYA_TAGGER_PIP_INDEX:-}" ]]; then
  IFS=',' read -r -a INDEXES <<< "$KOHYA_TAGGER_PIP_INDEX"
else
  while IFS= read -r index_line; do
    [[ -n "$index_line" ]] && INDEXES+=("$index_line")
  done < <(index_urls "$INDEX_PROFILE")
fi
if [[ ${#INDEXES[@]} -eq 0 ]]; then
  say "No usable index-url (both --index $INDEX_PROFILE and KOHYA_TAGGER_PIP_INDEX are empty)"
  exit 2
fi

# uname takes priority (a fake uname on PATH can override it, which is how the tests exercise the POSIX branch on Windows),
# and only if that fails do we look at OSTYPE, which bash sets itself.
is_windows_shell() {
  local s
  if s="$(uname -s 2>/dev/null)" && [[ -n "$s" ]]; then
    case "$s" in MINGW*|MSYS*|CYGWIN*|Windows*) return 0 ;; *) return 1 ;; esac
  fi
  case "${OSTYPE:-}" in msys*|cygwin*|win32) return 0 ;; esac
  return 1
}

# ---------------------------------------------------------------- pick the GPU variant
if [[ "$GPU" == "auto" ]]; then
  if is_windows_shell; then
    GPU=directml
  elif command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
    GPU=cuda
  else
    GPU=cpu
  fi
fi

if [[ "$GPU" == "directml" ]] && ! is_windows_shell; then
  say "onnxruntime-directml only has wheels for Windows (it goes through D3D12), and this machine is not Windows."
  say "On Linux / macOS use --gpu cuda (with an NVIDIA card) or --gpu cpu."
  exit 1
fi

case "$GPU" in
  cuda)
    ORT_PKGS=(onnxruntime-gpu nvidia-cudnn-cu12 nvidia-cublas-cu12 nvidia-cuda-runtime-cu12 nvidia-cufft-cu12 nvidia-curand-cu12)
    ;;
  directml) ORT_PKGS=(onnxruntime-directml) ;;
  *)        ORT_PKGS=(onnxruntime) ;;
esac
PLANNED_PIP=("${BASE_PKGS[@]}" "${ORT_PKGS[@]}")

# ---------------------------------------------------------------- --dry-run
if [[ $DRY_RUN -eq 1 ]]; then
  printf 'DRY_RUN_GPU=%s\n' "$GPU"
  printf 'DRY_RUN_PIP=%s\n' "${PLANNED_PIP[@]}"
  for index_url in "${INDEXES[@]}"; do
    printf 'DRY_RUN_INDEX=%s\n' "$index_url"
  done
  exit 0
fi

# ---------------------------------------------------------------- venv
step "Python environment: $VENV"
if [[ $RECREATE -eq 1 && -d "$VENV" ]]; then
  # Directory deletion is irreversible: print the absolute path, assert it really is a venv, then ask for confirmation (AGENTS.md's destructive-operation discipline).
  FULL="$(cd -- "$VENV" && pwd -P)" || { say "Cannot find $VENV"; exit 1; }
  say "    About to delete: $FULL"
  case "$FULL" in
    */.venv|*/venv) ;;
    *) say "Refusing to delete: the target is not a venv directory ($FULL)"; exit 1 ;;
  esac
  if [[ $ASSUME_YES -eq 0 ]]; then
    printf '    Delete it? (y/N) ' >&2
    answer=""
    read -r answer || true
    [[ "$answer" == "y" ]] || { say "Cancelled"; exit 1; }
  fi
  rm -rf -- "$FULL"
  ok "Deleted the old venv"
elif [[ -d "$VENV" ]]; then
  ok "Already exists; reusing it (add --recreate to rebuild)"
fi

if [[ ! -x "$VENV/bin/python" && ! -x "$VENV/Scripts/python.exe" ]]; then
  step "Creating the venv"
  "$PYTHON" -m venv "$VENV" || { say "Failed to create the venv (cannot find $PYTHON?)"; exit 1; }
fi

PY=""
for candidate in "$VENV/bin/python" "$VENV/bin/python3" "$VENV/Scripts/python.exe"; do
  if [[ -x "$candidate" ]]; then PY="$candidate"; break; fi
done
if [[ -z "$PY" ]]; then
  say "The venv was created but no python can be found in it ($VENV)"
  exit 1
fi
ok "$("$PY" --version 2>&1)"

# Try each index in order; move to the next only when the previous one fails (cannot connect / 404 / connection cut).
# An explicit --index-url throughout: **never** fall back to the mirror in global config (that is exactly what "looks like a hang" comes from).
pip_install() {
  local index_url
  for index_url in "${INDEXES[@]}"; do
    if "$PY" -m pip install --index-url "$index_url" "$@"; then
      INDEX_USED="$index_url"
      return 0
    fi
    warn "Install from index $index_url failed, trying the next one"
  done
  return 1
}

# ---------------------------------------------------------------- dependencies
step "Upgrading pip"
pip_install -q --upgrade pip   || { say "Failed to upgrade pip (tried: ${INDEXES[*]})"; exit 1; }

step "Installing base dependencies"
pip_install -q "${BASE_PKGS[@]}"   || { say "Failed to install base dependencies (tried: ${INDEXES[*]})"; exit 1; }

# ---------------------------------------------------------------- onnxruntime
step "Configuring onnxruntime (chosen: $GPU)"
# The three variants are mutually exclusive: uninstall all three first so re-running never leaves two conflicting runtimes behind.
"$PY" -m pip uninstall -y "${ORT_VARIANTS[@]}" >/dev/null 2>&1 || true
if [[ "$GPU" == "cuda" ]]; then
  warn "CUDA build: onnxruntime-gpu is about 245 MB, plus a set of nvidia-* runtime libraries (about 450 MB)."
fi
pip_install "${ORT_PKGS[@]}"   || { say "Failed to install onnxruntime ($GPU) (tried: ${INDEXES[*]})"; exit 1; }
ok "Actually used: $INDEX_USED"

# ---------------------------------------------------------------- this package
step "Editable install of this package (--no-deps, leaving the pinned versions above untouched)"
(cd "$REPO_ROOT" && pip_install -e . --no-deps -q)   || { say "Editable install failed (check pyproject.toml first)"; exit 1; }
ok "kohya_dataset_tagger is importable"

# ---------------------------------------------------------------- self-check
step "Self-check (build a session with a real model and look at the provider **actually** in use)"
if [[ -f "$REPO_ROOT/tools/check_provider.py" ]]; then
  if ! "$PY" "$REPO_ROOT/tools/check_provider.py"; then
    warn "The requested provider is not really in effect. onnxruntime **silently falls back to CPU**"
    warn "when a GPU provider fails to load -- inference still runs, just about 5x slower; you will not notice without actively checking."
  fi
else
  warn "(no tools/check_provider.py; skipping the real-session check)"
fi

"$PY" - <<'PYEOF' || { say "Self-check failed"; exit 1; }
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
PYEOF

step "Done"
say "    Start the service: $PY -m kohya_dataset_tagger --port 3001"
say "    Run the tests:     $PY -m pytest test/ -q"
say "    Acceptance:        $PY -m pytest acceptance/ -q"
