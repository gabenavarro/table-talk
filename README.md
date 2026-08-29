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
  `uv run`, PEP 723): `table-talk serve` → http://127.0.0.1:8731 by default,
  and `table-talk url` prints it when you have configured another port — a tiled wall
  of sessions beside a project drawer, refreshed every 2 s. See
  [Dashboard](#dashboard).
- **Skill** (`skill/SKILL.md`): tells Claude to record as it works and to end
  every reply with the tables, using ids you can reference back ("a3f9: option 2").

## See it first

The dashboard runs against any directory of logs, so you can look at a seeded
one before installing anything but [uv](https://docs.astral.sh/uv/):

    git clone https://github.com/gabenavarro/table-talk.git
    cd table-talk
    TABLE_TALK_DIR=docs/demo ./bin/table-talk serve                # http://127.0.0.1:8731
    TABLE_TALK_DIR=docs/demo ./bin/table-talk serve --port 8732    # alongside your own

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
    table-talk progress "$tid" --blocked-on "$id"   # not running: waiting on that decision
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
  `Z` zoomed, `*` current, `◉` its session is working right now. The session
  code opens that session's own Claude Code transcript when one can be
  resolved. Over sections for actions, jobs, diagrams,
  glossary and done, and a footer tallying what is resolved. Every section
  collapses, and a shut one keeps a █/░ bar reporting what it holds. Progress
  text is read for a percentage and drawn as a bar, which pulses for five
  minutes after a reading was taken and is still after that; an attached `--diagram`
  sketch draws centered under its item in the theme's inks;
  a coloured gutter marks whatever moved since you last looked. Click any id
  to copy `SESSION: <session> - ID: <id>` — or the bare id when the
  item predates session stamping. Press `u` to merge: one window per project
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
| `!` | needs-me: drop windows with no open **actions** (a running job is not something that needs you) |
| `u` | merge: one window per project, every row tagged with its session (the default; `ui.view` sets it) |
| `?` | the key list |
| `Esc` | leave zoom |

Every key has a click equivalent — the statusline chips, the `M` `Z` `▾`
buttons on each titlebar, and the filter box itself — and every click has a
keyboard equivalent: section headers and the drawer's fold triangle take Tab
and Enter, so nothing on this page needs a pointer or a key alone.

## Is anything actually happening?

A progress bar pulses for five minutes after a reading was taken, then goes
still. That says *recent*, not *working* — a log records what happened, never
what is happening.

A task that reaches 100% and is never closed is the one kind of drift the log
can see by itself, so every recording command checks for it and prints a note.
It stays quiet for a task that is `done`, for the id you just wrote, and for
one blocked on a still-open action.

For anything beyond that the log is not enough, and two hooks supply what it
cannot see:

| Hook | Event | What it records |
|---|---|---|
| `bin/tt-beat` | `PostToolUse` | The first four characters of the session id — the same `sid` the CLI stamps on every event. A window whose session called a tool in the last two minutes shows a `◉` in its titlebar. |
| `bin/tt-ref` | `UserPromptSubmit` | Every standalone 4-hex word in your message. An action you answered by id that the session never closed then gets named on the next recording command. |

Both do the same small thing: read the payload on stdin and touch an empty
file. Neither reads or writes the event log — a hook appending JSONL on every
prompt could corrupt a file the dashboard folds, and touching an empty file
cannot fail halfway. `tt-ref` records *every* 4-hex word rather than deciding
what is an id, because deciding means folding the log; the CLI does the join at
warn time, and a marker matching no open action is ignored.

`./install.sh` sets both up. It merges them into `~/.claude/settings.json`,
keeping everything already there and backing the file up first, and re-running
it changes nothing — so an existing install picks up a newly added hook simply
by running it again. To skip them — they fire in *every* project, not only this
one — install with `./install.sh --no-hook`.

```sh
table-talk install-hook            # add them later, or after --no-hook
table-talk install-hook --remove   # take them out again
```

Both need `jq`, and the installer warns if it is missing rather than leaving
them to no-op in silence. Without them nothing changes: no directory, no
heartbeats, no markers. Every failure path exits 0, so neither can break the
session it reports on.

Two things `tt-ref` cannot do. It sees a *reference*, not an answer — asking
"what is `a6d9` about?" flags it too, which is still a moment worth acting on.
And it sees nothing when you answer with no id at all ("yes, do it").

## Reaching it from another device

The dashboard listens on `127.0.0.1` — this machine only. To open it from a
phone or another computer, set the bind address in the config:

```toml
[server]
host = "0.0.0.0"
```

**There is no password on this page.** Anyone who can reach the port reads your
work log, project names and file paths, and "your network" includes shared
wifi. The dashboard says so on startup when it binds beyond localhost. Only two
values are accepted; anything else falls back to `127.0.0.1` with a warning,
so a typo can never widen exposure.

## Themes

Fifteen terminal palettes ship in `bin/themes.json`, converted from
[iTerm2-Color-Schemes](https://github.com/mbadolato/iTerm2-Color-Schemes) — the
same collection [Ghostty's own themes](https://ghostty.org/docs/features/theme)
come from — under its MIT licence (see [THEMES-LICENCE](THEMES-LICENCE)).

The conversion is mechanical: 14 of this app's 17 colour tokens *are* a terminal
theme's ANSI palette, and Gruvbox Dark Hard reproduces the palette this app
already shipped, to the byte. `bg` is the theme's background; `surface` and
`surface-2` are derived, because a terminal has one background where this wall
has three depths.

Pick one in the config:

```toml
[theme]
dark_theme = "Rose Pine"
light_theme = "Gruvbox Light Hard"
```

A named theme replaces the base palette and your own `[theme.dark]` tokens
still win on top, so changing one colour does not mean restating the other
sixteen. An unknown name warns and keeps the built-in palette.

Every bundled theme is checked against the contrast pairs the stylesheet
documents. Six pass untouched. The rest are marked `adapted`, naming the exact
tokens moved: terminal accents are chosen for large glyphs on their own ground,
and here they are 9–12px UI text — a faithful yellow on a light background is
about 1.8:1. Regenerate with `tools/build-themes.py`.

## Configuration

UI state — theme, marks, folds, scope, sort and what you have already seen —
lives beside the logs in `~/.local/share/table-talk/.ui/`, so it follows the
data rather than the directory you launched from. (Before v5 it landed in a
`.nicegui/` folder beside the launch directory; those are safe to delete.)

The drawer's footer has a **settings** entry that opens this file in your
`links.open_command`, creating it from the documented example the first time —
a fresh install has no config at all and runs on the defaults below. Change `server.port`
there and the statusline offers to restart onto it — after checking the port is
actually free, so it can never exec into a port something else already holds.
Starting with `--port` opts out: a flag you typed beats the file.

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
