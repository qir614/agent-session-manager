#!/usr/bin/env bash
set -euo pipefail
if [[ "$(uname -s)" != "Linux" ]]; then
    echo "This test runner supports Linux only." >&2
    exit 1
fi
PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd -- "$PROJECT_ROOT"
mkdir -p -- "$PROJECT_ROOT/.review-tmp"
export TMPDIR="$PROJECT_ROOT/.review-tmp"
export PYTHONPATH="$PROJECT_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
exec python -B -m unittest discover -s tests -t . "$@"
