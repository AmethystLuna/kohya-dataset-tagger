#!/usr/bin/env bash
# Start Kohya Dataset Tagger (Linux/macOS). On Windows use start.bat at the repository root.
#
#   ./start.sh                       # read roots.txt at the repository root (asks once the first time and remembers it)
#   ./start.sh --port 3010
#   ./start.sh --roots /data/a --roots /data/b
#   ./start.sh --no-browser
#   ./start.sh --dry-run             # just print the command line that would run and exit 0, without starting the service
#
# Where the dataset roots come from (in priority order):
#   1. command line (--roots or a positional argument)    2. KOHYA_TAGGER_ROOTS (; or , separated)
#   3. $ROOTS_FILE (default <repository>/roots.txt)       4. ask once interactively and write it back
#
# Aligned with scripts/start.ps1 (p0-spec §7 "cross-platform launcher" / acceptance A32):
# it reads the same roots.txt, uses the same "pick an unoccupied port and pass it to --port", and the same --dry-run.
#
# Two deliberate differences, both fixed by p0-spec:
#   * model_paths.txt at the repository root is **not forwarded by this script** (§3.7: the server reads it itself).
#     Turning it into command-line arguments would mean that "deleting a line in the UI" gets overridden by the command line on the next start. Here we only print its location.
#   * in --dry-run, stdout has **only** lines starting with DRY_RUN_ (machine-assertable),
#     and every human-facing hint goes to stderr.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$REPO_ROOT/.venv"
ROOTS_FILE="${KOHYA_TAGGER_ROOTS_FILE:-$REPO_ROOT/roots.txt}"
MODEL_PATHS_FILE="${KOHYA_TAGGER_MODEL_PATHS_FILE:-$REPO_ROOT/model_paths.txt}"
PORT="${PORT:-3001}"
PORT_TRIES=21
DRY_RUN=0
NO_BROWSER=0
ROOTS=()

say() { printf '%s\n' "$1" >&2; }

usage() {
  cat >&2 <<'USAGE'
Usage: ./start.sh [options] [dataset root ...]

  --roots DIR    dataset root, repeatable; can also be given as a positional argument
  --port N       starting port (defaults to $PORT, then to 3001); moves on if it is taken
  --no-browser   do not open the browser automatically
  --dry-run      just print the command line that would run to stdout and exit 0, without starting the service or opening the browser
  -h, --help     show this help

Root priority: command line > KOHYA_TAGGER_ROOTS > roots.txt > ask once interactively.
USAGE
}

# ---------------------------------------------------------------- arguments
while [[ $# -gt 0 ]]; do
  case "$1" in
    --roots)   ROOTS+=("${2:?--roots must be followed by a directory}"); shift 2 ;;
    --roots=*) ROOTS+=("${1#*=}"); shift ;;
    --port)    PORT="${2:?--port must be followed by a port}"; shift 2 ;;
    --port=*)  PORT="${1#*=}"; shift ;;
    --dry-run|-n) DRY_RUN=1; shift ;;
    --no-browser) NO_BROWSER=1; shift ;;
    -h|--help) usage; exit 0 ;;
    --) shift; while [[ $# -gt 0 ]]; do ROOTS+=("$1"); shift; done ;;
    -*) say "Unknown argument: $1"; usage; exit 2 ;;
    *)  ROOTS+=("$1"); shift ;;
  esac
done

# ---------------------------------------------------------------- 0. platform
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

# roots.txt / KOHYA_TAGGER_ROOTS / manual input share one splitting rule: one item per line, and ; and , are also recognized.
# On POSIX, : is recognized too (on Linux the server's --roots is joined with os.pathsep=':');
# on Windows : must not be recognized -- C:/data would be split into C and /data.
root_separators() {
  if is_windows_shell; then printf ';,'; else printf ';,:'; fi
}

trim() {
  local s="$1"
  s="${s#"${s%%[![:space:]]*}"}"
  s="${s%"${s##*[![:space:]]}"}"
  printf '%s' "$s"
}

# Also tolerate the UTF-8 BOM and CRLF that PowerShell 5.1 writes: those bytes turn the first root
# into a "nonexistent path", and the error message looks completely nonsensical.
read_roots_from_stdin() {
  local line part sep
  sep="$(root_separators)"
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    line="${line#$'\xef\xbb\xbf'}"
    [[ -n "$(trim "$line")" ]] || continue
    IFS="$sep" read -r -a _parts <<< "$line"
    for part in ${_parts[@]+"${_parts[@]}"}; do
      part="$(trim "$part")"
      [[ -n "$part" ]] || continue
      case "$part" in
        "~")   part="$HOME" ;;
        "~/"*) part="$HOME/${part#\~/}" ;;
      esac
      printf '%s\n' "$part"
    done
  done
}

load_roots_into_ROOTS() {   # stdin -> ROOTS
  local line
  ROOTS=()
  while IFS= read -r line || [[ -n "$line" ]]; do ROOTS+=("$line"); done < <(read_roots_from_stdin)
}

# ---------------------------------------------------------------- 1. venv
# .venv/bin/python is the Linux/macOS layout; Scripts/python.exe is the fallback that lets the same
# scripts run under Git Bash / MSYS (this machine has no WSL, so they can only be verified on Git Bash).
PY=""
for candidate in "$VENV/bin/python" "$VENV/bin/python3" "$VENV/Scripts/python.exe"; do
  if [[ -x "$candidate" ]]; then PY="$candidate"; break; fi
done
if [[ -z "$PY" ]]; then
  say "No python found in .venv (looked for $VENV/bin/python and $VENV/Scripts/python.exe)."
  say "Run ./setup_env.sh first to initialize the environment (it installs fastapi / pillow / numpy / onnxruntime, about 250 MB, a few minutes)."
  say "On a China network use ./setup_env_cn.sh (same implementation + --index cn, going through domestic mirrors)."
  exit 1
fi

# ---------------------------------------------------------------- 2. dataset roots
if [[ ${#ROOTS[@]} -eq 0 && -n "${KOHYA_TAGGER_ROOTS:-}" ]]; then
  load_roots_into_ROOTS < <(printf '%s\n' "$KOHYA_TAGGER_ROOTS")
fi
if [[ ${#ROOTS[@]} -eq 0 && -s "$ROOTS_FILE" ]]; then
  load_roots_into_ROOTS < "$ROOTS_FILE"
fi
if [[ ${#ROOTS[@]} -eq 0 ]]; then
  say "No dataset root configured yet"
  say "    You can enter several, separated by semicolons or commas. For example:"
  say "    /mnt/data/my-dataset"
  printf '    Dataset root: ' >&2
  line=""
  read -r line || true
  load_roots_into_ROOTS < <(printf '%s\n' "$line")
  if [[ ${#ROOTS[@]} -eq 0 ]]; then
    say "Without a dataset root we cannot start"
    exit 1
  fi
  printf '%s\n' "${ROOTS[@]}" > "$ROOTS_FILE"
  say "    Remembered, written to $ROOTS_FILE (no need to enter it again next time; edit it to change)"
fi

# A nonexistent root must fail **now**: starting the service with a bad path only yields a screen full of 403s.
BAD_ROOTS=()
for r in "${ROOTS[@]}"; do
  [[ -d "$r" ]] || BAD_ROOTS+=("$r")
done
if [[ ${#BAD_ROOTS[@]} -gt 0 ]]; then
  say "These directories do not exist or are not directories:"
  for bad in "${BAD_ROOTS[@]}"; do say "    $bad"; done
  exit 1
fi

# ---------------------------------------------------------------- 3. port
if [[ ! "$PORT" =~ ^[0-9]+$ ]]; then
  say "The port must be a number: $PORT"
  exit 2
fi
if (( PORT < 1 || PORT > 65535 )); then
  say "Port out of range (1-65535): $PORT"
  exit 2
fi

CHOSEN="$("$PY" - "$PORT" "$PORT_TRIES" <<'PYEOF'
import socket
import sys

start, tries = int(sys.argv[1]), int(sys.argv[2])
for port in range(start, start + tries):
    sock = socket.socket()
    sock.settimeout(0.25)
    try:
        sock.connect(("127.0.0.1", port))
    except OSError:
        print(port)          # cannot connect = nobody is listening = usable (the same criterion as start.ps1's TcpClient)
        raise SystemExit(0)
    finally:
        sock.close()
raise SystemExit(1)
PYEOF
)" || { say "All $((PORT_TRIES - 1)) ports starting at $PORT are taken"; exit 1; }

if [[ "$CHOSEN" != "$PORT" ]]; then
  say "Port $PORT is taken, using $CHOSEN instead"
fi
URL="http://127.0.0.1:$CHOSEN"

ARGS=(-m kohya_dataset_tagger --port "$CHOSEN")
for r in "${ROOTS[@]}"; do
  ARGS+=(--roots "$r")
done

# ---------------------------------------------------------------- 4. --dry-run
if [[ $DRY_RUN -eq 1 ]]; then
  printf 'DRY_RUN_PYTHON=%s\n' "$PY"
  printf 'DRY_RUN_PORT=%s\n' "$CHOSEN"
  printf 'DRY_RUN_ARG=%s\n' "${ARGS[@]}"
  exit 0
fi

# ---------------------------------------------------------------- 5. start the service
say ""
say "==> Kohya Dataset Tagger"
say "    Dataset roots:"
for r in "${ROOTS[@]}"; do say "      $r"; done
if [[ -f "$MODEL_PATHS_FILE" ]]; then
  say "    Extra model scan paths: $MODEL_PATHS_FILE"
  say "      (the server reads it itself, see p0-spec §3.7; restart the service after editing the file)"
fi
say "    Address: $URL"
say "    (Ctrl+C to quit)"
say ""

# Open the browser only once the service is really ready (probe with the venv's own python, not relying on curl).
open_browser_when_ready() {
  local url="$1" opener="" i
  if command -v xdg-open >/dev/null 2>&1; then opener="xdg-open"
  elif command -v open >/dev/null 2>&1; then opener="open"
  elif command -v cmd.exe >/dev/null 2>&1; then opener="cmd.exe"
  else
    say "    (neither xdg-open nor open was found, skipping opening the browser; address: $url)"
    return 0
  fi
  for ((i = 0; i < 40; i++)); do
    if "$PY" -c 'import sys, urllib.request; urllib.request.urlopen(sys.argv[1], timeout=2).read(1)' \
        "$url/api/health" >/dev/null 2>&1; then
      case "$opener" in
        cmd.exe) cmd.exe //c start "" "$url" >/dev/null 2>&1 || true ;;
        *)       "$opener" "$url" >/dev/null 2>&1 || true ;;
      esac
      return 0
    fi
    sleep 0.5
  done
  say "    (timed out waiting for the service to be ready, did not open the browser; address: $url)"
}

if [[ $NO_BROWSER -eq 0 ]]; then
  open_browser_when_ready "$URL" &
fi

exec "$PY" "${ARGS[@]}"
