# table-talk

[![License: MIT](https://img.shields.io/github/license/gabenavarro/table-talk)](LICENSE)
[![Python ≥3.10](https://img.shields.io/badge/python-%E2%89%A53.10-blue)](https://www.python.org/)
[![PEP 723](https://img.shields.io/badge/PEP%20723-uv%20run-261230)](https://docs.astral.sh/uv/guides/scripts/)
[![NiceGUI](https://img.shields.io/badge/NiceGUI-3.16-informational)](https://nicegui.io/)

Structured collaboration between you and Claude: every Claude reply ends with
three tables — decisions it needs from you, background work in flight, and
technical terms explained twice (intuitive + precise). Everything is also an
event in a local log, and a live dashboard shows every session's tables with
the full cumulative glossary.

![The table-talk dashboard: a drawer of sessions grouped by project beside a tiled wall of session windows](docs/assets/hero.png)

<!-- The terminal demo is scripted in docs/assets/demo.tape; `vhs docs/assets/demo.tape`
     re-renders docs/assets/demo.gif, which still shows the pre-wall dashboard. -->

## How it works

- **CLI** (`bin/table-talk`, Python stdlib, zero deps): appends events to
  `~/.local/share/table-talk/<date>-<project>.jsonl`. Current state is a
  shallow-merge fold by 4-hex id — append-only, safe for concurrent sessions.
- **Dashboard** (`bin/table-talk-dash.py`, [NiceGUI](https://nicegui.io) via
  `uv run`, PEP 723): `table-talk serve` → http://127.0.0.1:8731 — a tiled wall
  of sessions beside a project drawer, refreshed every 2 s. See
  [Dashboard](#dashboard).
- **Skill** (`skill/SKILL.md`): tells Claude to record as it works and to end
  every reply with the tables, using ids you can reference back ("a3f9: option 2").

## See it first

The dashboard runs against any directory of logs, so you can look at a seeded
one before installing anything but [uv](https://docs.astral.sh/uv/):

    git clone https://github.com/gabenavarro/table-talk.git
    cd table-talk
    TABLE_TALK_DIR=docs/demo ./bin/table-talk serve      # http://127.0.0.1:8731

Two projects, four sessions, live actions and jobs, a mermaid diagram and a
glossary — enough to press `\`, `s`, `!` and `u` and see what they do.
`TABLE_TALK_DIR` points the CLI and the dashboard at any data directory, which
is also how you keep work logs separate:

    TABLE_TALK_DIR=docs/demo ./bin/table-talk show --open

(The demo is a frozen snapshot, so its ages count up from 27 Aug 2026.)

## Install

The CLI needs Python ≥3.10 and nothing else — it is stdlib-only, so whatever
your distro ships almost certainly works (Ubuntu 22.04 and Debian 12 included).

The dashboard needs [uv](https://docs.astral.sh/uv/), which fetches its own
interpreter from the script's PEP 723 header — your system Python is not
involved. The skill needs Claude Code.

Linux and macOS; on Windows use WSL (the CLI locks its log with `fcntl`).

    git clone https://github.com/gabenavarro/table-talk.git
    cd table-talk && ./install.sh

install.sh symlinks the CLI into ~/.local/bin — make sure that's on your PATH.

## Use

    # each recording command prints a 4-hex id; capture it to update or close the item
    id=$(table-talk action "Choose ref genome" --why "blocks training" --rec "R64-1-1")
    tid=$(table-talk task "Training GPN model")
    table-talk progress "$tid" "epoch 3/10" --pct 30   # --pct beats any number in the text
    table-talk progress "$id" --intuitive "what this means in plain words"   # amend, no note
    table-talk done "$id"
    table-talk term "FVA" --intuitive "range of possible flux" --technical "LP min/max per reaction at fixed optimum"
    table-talk task "GPN training" --intuitive "teaching a model to read DNA" --diagram $'data\n  ▼\ntrain\n  ▼\neval'
    table-talk diagram $'flowchart TD\n  a --> b' --title "data flow"   # dashboard renders it
    table-talk show --open     # only what is still outstanding (--mine for this session only)
    table-talk serve           # dashboard (run it yourself — it refuses inside a Claude session)

## Dashboard

`table-talk serve` opens a tmux-shaped view of every session log, polling every
two seconds.

- **The wall** tiles one window per session, packed into columns by how much
  each has to say. A titlebar reads `project:session` — the code of the agent
  session that wrote the file last, or its index for older files — and
  carries tmux's flags — `!` open actions, `#` jobs running, `M` marked,
  `Z` zoomed, `*` current — over sections for actions, jobs, diagrams,
  glossary and done, and a footer tallying what is resolved. Every section
  collapses, and a shut one keeps a █/░ bar reporting what it holds. Progress
  text is read for a percentage and drawn as a bar; an attached `--diagram`
  sketch draws centered under its item in the theme's inks;
  a coloured gutter marks whatever moved since you last looked. Click any id
  to copy it. Press `u` to merge: one window per project
  holding every session's actions, jobs, glossary and done, each row tagged
  with the session code that recorded it (or its date for events recorded
  before session stamping existed) — the drawer still lists every real
  session underneath.
- **The drawer** is a session tree grouped by project, every row carrying open
  counts and an htop-style meter (a project's numbers are the sum of its
  sessions', never the average of their percentages). Click a project to scope
  the wall to it, a session to jump to its window. The filter box dims
  non-matching rows instead of hiding them — a filter must never make an open
  action disappear — and reports `N/M rows match`.
- **The statusline** carries the poll cadence, the open tally, the column
  count, and one chip per key.

| key | does |
| --- | --- |
| `\` | show or hide the drawer |
| `m` | mark the current window — marked windows pack first |
| `z` | zoom it to fill the wall |
| `f` | fold it down to its titlebar |
| `s` | cycle the drawer sort: recent → actions → project — `actions` ranks sessions inside a project too |
| `/` | jump to the filter box |
| `!` | needs-me: drop windows with nothing open |
| `u` | merge: one window per project, every row tagged with its session |
| `?` | the key list |
| `Esc` | leave zoom |

Every key has a click equivalent — the statusline chips, the `M` `Z` `▾`
buttons on each titlebar, and the filter box itself — so nothing on this page
is keyboard-only.

## Configuration

UI state — theme, marks, folds, scope, sort and what you have already seen —
lives beside the logs in `~/.local/share/table-talk/.ui/`, so it follows the
data rather than the directory you launched from. (Before v5 it landed in a
`.nicegui/` folder beside the launch directory; those are safe to delete.)

`~/.config/table-talk/config.toml` (or point `TABLE_TALK_CONFIG` at another
file) sets the port, poll interval, columns, drawer state, theme, and
`links.open_command` — the command used to open a clicked file path in your
editor (`open` on macOS, `xdg-open` elsewhere). Every key, its default, and what it does is in
[`docs/config.example.toml`](docs/config.example.toml); copy it to get
started. A missing or broken file just falls back to the defaults.

## Test

    ./test.sh

Every change runs all four selftests — the CLI, the model, the config loader
and the dashboard. There is no separate test framework: each file pins its own
behaviour in a `selftest()`, and a change that breaks one fails loudly.

## Contributing

Issues and pull requests are welcome from anyone — open an issue to report a
bug or suggest something, or fork the repo and send a PR.

`main` is protected by two rulesets. The first takes no direct pushes, no
force-pushes and no deletion, and requires the `test` check — `./test.sh` run
by [the workflow](.github/workflows/test.yml) — to pass. Nobody bypasses it:
a red build cannot reach `main`. The second requires one approving review,
and only a repository admin can bypass it, and only through a pull request.

So: branch from `main`, keep `./test.sh` green, open a PR, and get a review.
