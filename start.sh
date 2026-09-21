#!/usr/bin/env bash
# Start Kohya Dataset Tagger (Linux/macOS). On Windows use start.bat at the repository root.
#
# This layer does only two things: change to the repository root, then hand the arguments verbatim to the real implementation scripts/start.sh.
# --dry-run / --port / --roots / KOHYA_TAGGER_ROOTS are all handled by scripts/start.sh.
set -euo pipefail
cd "$(dirname "$0")"
exec bash scripts/start.sh "$@"
