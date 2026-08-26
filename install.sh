#!/usr/bin/env bash
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
mkdir -p ~/.local/bin ~/.local/share/table-talk ~/.claude/skills
chmod +x "$here/bin/table-talk" "$here/bin/table-talk-dash.py"
ln -sf "$here/bin/table-talk" ~/.local/bin/table-talk
ln -sfn "$here/skill" ~/.claude/skills/table-talk
echo "installed: ~/.local/bin/table-talk, skill -> ~/.claude/skills/table-talk, data dir ready"
