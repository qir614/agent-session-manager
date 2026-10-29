#!/usr/bin/env bash
set -euo pipefail
if [[ "$(uname -s)" != "Linux" ]]; then
    echo "This launcher supports Linux only." >&2
    exit 1
fi
PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
exec python -B "$PROJECT_ROOT/run_gui.py" "$@"
