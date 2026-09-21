#!/usr/bin/env bash
# Initialize Kohya Dataset Tagger's Python environment (idempotent, safe to re-run).
# On Windows use setup_env.bat at the repository root. **On a China network use ./setup_env_cn.sh** (the same script + --index cn).
#
# This layer does only two things: change to the repository root, then hand the arguments verbatim to the real implementation scripts/setup_env.sh.
# --index / --gpu / --recreate / --dry-run are all handled by scripts/setup_env.sh.
set -euo pipefail
cd "$(dirname "$0")"
exec bash scripts/setup_env.sh "$@"
