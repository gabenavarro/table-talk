#!/usr/bin/env bash
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
python3 "$here/bin/table-talk" --selftest
python3 "$here/bin/tt_model.py" --selftest
python3 "$here/bin/tt_config.py" --selftest
python3 "$here/bin/tt_jobs.py" --selftest
uv run --script "$here/bin/table-talk-dash.py" --selftest
echo "all selftests passed"
