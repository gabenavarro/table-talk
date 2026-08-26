#!/usr/bin/env bash
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
python3 "$here/bin/table-talk" --selftest
uv run --script "$here/bin/table-talk-dash.py" --selftest
echo "all selftests passed"
