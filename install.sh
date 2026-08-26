#!/usr/bin/env bash
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
mkdir -p ~/.local/bin ~/.local/share/table-talk ~/.claude/skills
chmod +x "$here/bin/table-talk" "$here/bin/table-talk-dash.py"

skill_target=~/.claude/skills/table-talk
# ln -sfn only replaces a symlink; over a real dir it would nest the link inside it
# and silently break the skill. Fail loudly instead of guessing.
if [ -e "$skill_target" ] && [ ! -L "$skill_target" ]; then
    echo "error: $skill_target exists and is not a symlink — remove it and re-run" >&2
    exit 1
fi

ln -sf "$here/bin/table-talk" ~/.local/bin/table-talk
ln -sfn "$here/skill" "$skill_target"
echo "installed: ~/.local/bin/table-talk, skill -> $skill_target, data dir ready"

case ":$PATH:" in
    *":$HOME/.local/bin:"*) ;;
    *) echo "warning: ~/.local/bin is not on your PATH — add it:" >&2
       echo '  export PATH="$HOME/.local/bin:$PATH"' >&2 ;;
esac
