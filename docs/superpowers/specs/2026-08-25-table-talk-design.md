# table-talk — Design Spec (2026-08-25)

## Purpose

A communication protocol + tracker for collaboration between Gabriel (gabenavarro) and Claude sessions on one Linux box. Claude structures every reply around three tables (actions needed from the user, background work in flight, technical terms), persists them as events, and a local NiceGUI dashboard shows every session's tables live, with the full cumulative glossary.

Approved by user 2026-08-25 (decision `d41e`, round 2): NiceGUI dashboard, chosen over the research judge's stdlib recommendation for its more powerful UI (sortable/filterable Quasar tables). Research archive: 5-agent workflow compared FastHTML (6/10), NiceGUI (5.5/10 on minimalism criteria), Flask (7.5), stdlib (9); user explicitly weighted UI power higher than minimalism.

## Components

```
table-talk/                     (public repo: gabenavarro/table-talk)
├── README.md                   what it is, install, protocol example
├── install.sh                  symlinks CLI + skill, creates data dir
├── bin/table-talk              CLI — Python STDLIB ONLY (appender + serve launcher)
├── bin/table-talk-dash.py      NiceGUI dashboard — PEP 723, run via uv
├── skill/SKILL.md              the reply-protocol skill (→ ~/.claude/skills/table-talk/)
└── test.sh                     runs both selftests
```

- `install.sh`: `ln -sf` `bin/table-talk` → `~/.local/bin/table-talk`; `ln -sfn` `skill/` → `~/.claude/skills/table-talk`; `mkdir -p ~/.local/share/table-talk`. Idempotent.
- CLI is stdlib-only so **recording an event can never fail on network/deps**. The dashboard is the only thing that needs `uv`/PyPI.
- `table-talk serve` re-execs `uv run --script <repo>/bin/table-talk-dash.py` (path resolved via `os.path.realpath(__file__)` so the symlink works).

## Data model

State dir: `~/.local/share/table-talk/` (override: `TABLE_TALK_DIR`).
One JSONL file per (date, project): `<YYYY-MM-DD>-<project>.jsonl`. Project = `--project` flag or basename of `$PWD`. Two sessions, same project, same day → same file (intended: one merged view).

Append-only events, one JSON object per line:

| type | fields |
|---|---|
| action | `id, type:"action", status:"open", background, why, rec, ts` |
| task | `id, type:"task", status:"open", what, ts` |
| term | `id, type:"term", term, intuitive, technical, ts` |
| (update) | partial: `id` + any changed fields, e.g. `{"id":"b210","progress":"epoch 3/10","ts":…}` or `{"id":"a3f9","status":"done","ts":…}` |

**Fold semantics (the contract both CLI and dashboard must share): current state = shallow-merge by id, in file order — `state[id] = {**state.get(id, {}), **event}`.** Never replace: a status-only append must preserve `background`/`why`/etc. Both selftests pin this.

IDs: 4 lowercase hex chars from `secrets.token_hex(2)`, re-rolled on collision within the file's folded state. `ts`: unix epoch int.

## CLI (`bin/table-talk`, stdlib argparse)

```
table-talk action "<background>" --why "…" --rec "…" [--project P]   → prints id
table-talk task "<what>" [--project P]                               → prints id
table-talk progress <id> "<update>"        # partial update
table-talk done <id>                       # status → done
table-talk term "<term>" --intuitive "…" --technical "…"   # dedupe: same term (case-insensitive) in folded state → reuse id, update
table-talk show [project]                  # plain-text dump of folded state (debugging)
table-talk serve [--port 8731]             # exec uv run …/table-talk-dash.py
table-talk --selftest                      # tempdir: add → update → fold → assert merge semantics + term dedupe
```

`progress`/`done` locate the id by folding each state file (newest first) and **append the update to the file containing that id** — folding is per-file, so an update written elsewhere would be invisible. Error clearly if the id is not found anywhere.

## Dashboard (`bin/table-talk-dash.py`)

- PEP 723 header: `dependencies = ["nicegui>=3.16,<4"]`, `requires-python = ">=3.12"`. Shebang `#!/usr/bin/env -S uv run --script`.
- `ui.run(host="127.0.0.1", port=8731, show=False, title="table-talk")` — loopback only, no auth. `__mp_main__` guard as NiceGUI requires.
- `@ui.refreshable` render function + `ui.timer(2.0, view.refresh)`. Re-globs and re-folds every `*.jsonl` per tick — fine at hundreds of lines. `# ponytail: full reparse per tick; mtime-gate if file count reaches hundreds`
- Per session file (newest first), one `ui.card`:
  1. **Actions needed** — open actions in `ui.table` (id, background, why, rec), sortable.
  2. **Background work** — open tasks (id, what, progress).
  3. **Glossary** — ALL terms, cumulative (term, intuitive, technical).
  4. Done actions/tasks inside a collapsed `ui.expansion("N done")`, dimmed.
- `--selftest`: imports fold + row-building only (no server start), asserts merge semantics, done-split, term cumulativeness against a tempfile. Must run without network once nicegui is cached.

## The skill (`skill/SKILL.md`)

Written per superpowers:writing-skills. Core protocol Claude follows in every session where the skill is active:

1. **Record as you go** via the CLI: user-blocking decisions → `action`; background work → `task` + `progress` updates; jargon introduced → `term`. Mark `done` when resolved.
2. **Every reply ends with up to three tables** (omit genuinely empty ones):
   - 🔴 Actions needed from you: `ID · Background · Why it matters · Recommendation`
   - 🔵 Background work: `ID · What · Progress`
   - 📖 Terms: `Term · Intuitive · Technical` — only terms new or load-bearing **for this reply**; the dashboard carries the cumulative glossary.
3. Users reference rows by ID ("a3f9: option 2"); Claude resolves the referenced item and marks it done.
4. Session start: derive project from cwd; mention `table-talk serve` if the dashboard isn't running.

## Error handling

- CLI: missing state dir → create it; malformed JSONL line → skip with warning to stderr, never crash a fold; unknown id → clear error, exit 1.
- Dashboard: malformed line → skip (same fold helper); empty dir → friendly "no sessions yet" page.
- `serve` with no `uv` on PATH → clear error naming the install URL.

## Testing

`test.sh`: `bin/table-talk --selftest` + `uv run bin/table-talk-dash.py --selftest`. Assert-based, no framework. That is the whole suite (ponytail).

## Deliberately out of scope (add when felt)

Cross-machine sync (explicitly declined by user), dashboard auth, editing state from the browser, systemd unit for auto-serve, SSE/websockets/inotify (2 s timer suffices), uv lockfile, stale-task cron nagging. Escalation path if browser-side interactivity is ever wanted: judge's runner-up notes (Flask 3.1.3 pattern) in the research archive.
