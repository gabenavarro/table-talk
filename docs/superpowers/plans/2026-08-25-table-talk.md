# table-talk Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the table-talk protocol: a stdlib CLI that records action/task/term events to JSONL, a NiceGUI live dashboard over those files, a Claude reply-protocol skill, and an installer — published to public repo `gabenavarro/table-talk`.

**Architecture:** Append-only JSONL event logs in `~/.local/share/table-talk/` (one file per date+project); current state = shallow-merge fold by id. A zero-dependency Python CLI appends events; a PEP 723 NiceGUI script renders per-session cards refreshing every 2 s. A skill instructs Claude sessions to record via the CLI and end replies with the three tables.

**Tech Stack:** Python 3.12 stdlib (CLI), NiceGUI `>=3.16,<4` via `uv run --script` (dashboard), bash (install/test), gh CLI (repo).

**Spec:** `docs/superpowers/specs/2026-08-25-table-talk-design.md`

## Global Constraints

- `bin/table-talk` is **stdlib only** — no imports outside the standard library, ever (recording must never depend on network/PyPI).
- Dashboard PEP 723 header pins exactly: `dependencies = ["nicegui>=3.16,<4"]`, `requires-python = ">=3.12"`.
- Fold contract everywhere: `state[id] = {**state.get(id, {}), **event}` — shallow merge, never replace. Both selftests must pin this.
- State dir: `Path(os.environ.get("TABLE_TALK_DIR", str(Path.home() / ".local/share/table-talk")))` — env override is what makes selftests hermetic.
- Dashboard binds `127.0.0.1`, default port `8731`, `show=False`, `reload=False`.
- IDs: 4 lowercase hex via `secrets.token_hex(2)`, re-rolled on collision within the target file's folded state.
- Tests are assert-based selftests only (ponytail) — no pytest, no fixtures. `test.sh` is the whole suite.
- Commits: end messages with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- The fold helper is deliberately duplicated in both bin files (different runtimes; sharing would cost an import shim). Each file's selftest pins the shared contract. Do not "DRY" this into a module.

---

### Task 1: CLI — `bin/table-talk`

**Files:**
- Create: `bin/table-talk` (executable, `#!/usr/bin/env python3`)

**Interfaces:**
- Consumes: nothing (first task).
- Produces: the on-disk event contract every later task relies on — JSONL events `{"id": <4hex>, "type": "action"|"task"|"term", "status": "open"|"done", ..., "ts": <int epoch>}` in `$TABLE_TALK_DIR/<YYYY-MM-DD>-<project>.jsonl`; CLI subcommands `action`, `task`, `progress`, `done`, `term`, `show`, `serve`, flag `--selftest`. Subcommands that create items print the bare id to stdout.
- `serve` execs `uv run --script <dir-of-realpath(__file__)>/table-talk-dash.py` — Task 2 must create that file with that exact name.

- [ ] **Step 1: Write `bin/table-talk` with selftest first, stubs for behavior**

Write the full file below. The selftest encodes the contract; the functions under test are real but minimal. Save exactly:

```python
#!/usr/bin/env python3
"""table-talk: record actions, background tasks, and technical terms for the
table-talk reply protocol. Stdlib only — recording must never need the network.
View: `table-talk serve` -> http://127.0.0.1:8731"""
import argparse
import json
import os
import secrets
import shutil
import sys
import time
from pathlib import Path

DATA_DIR = Path(os.environ.get("TABLE_TALK_DIR", str(Path.home() / ".local/share/table-talk")))


def fold(path):
    """Current state of one file: shallow-merge events by id, in file order.
    Contract shared with table-talk-dash.py; both selftests pin it."""
    state = {}
    for n, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            ev = json.loads(line)
            state[ev["id"]] = {**state.get(ev["id"], {}), **ev}
        except (json.JSONDecodeError, TypeError, KeyError):
            print(f"warning: skipped malformed line {path.name}:{n}", file=sys.stderr)
    return state


def session_file(project=None):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    p = DATA_DIR / f"{time.strftime('%Y-%m-%d')}-{project or Path.cwd().name}.jsonl"
    p.touch()
    return p


def append(path, ev):
    with open(path, "a") as f:
        f.write(json.dumps(ev) + "\n")


def new_id(state):
    while (i := secrets.token_hex(2)) in state:
        pass
    return i


def find_file_with_id(id_):
    for path in sorted(DATA_DIR.glob("*.jsonl"), reverse=True):
        if id_ in fold(path):
            return path
    return None


def add_event(project, fields):
    f = session_file(project)
    i = new_id(fold(f))
    append(f, {"id": i, "ts": int(time.time()), **fields})
    return i


def add_term(path, term, intuitive, technical):
    """Same term (case-insensitive) in this file -> reuse id, update definitions."""
    state = fold(path)
    existing = next((i for i, e in state.items() if e.get("type") == "term"
                     and e.get("term", "").lower() == term.lower()), None)
    i = existing or new_id(state)
    append(path, {"id": i, "type": "term", "term": term, "intuitive": intuitive,
                  "technical": technical, "ts": int(time.time())})
    return i


def update(id_, fields):
    """Append a partial update to the file that contains id_ (folding is per-file)."""
    f = find_file_with_id(id_)
    if not f:
        sys.exit(f"error: id '{id_}' not found in {DATA_DIR}")
    append(f, {"id": id_, "ts": int(time.time()), **fields})


def cmd_show(project):
    pattern = f"*-{project}.jsonl" if project else "*.jsonl"
    for path in sorted(DATA_DIR.glob(pattern)):
        print(f"== {path.stem}")
        for e in fold(path).values():
            print(json.dumps(e))


def cmd_serve(port):
    dash = Path(__file__).resolve().parent / "table-talk-dash.py"
    uv = shutil.which("uv")
    if not uv:
        sys.exit("error: uv not found on PATH - install from https://docs.astral.sh/uv/")
    os.execv(uv, [uv, "run", "--script", str(dash), "--port", str(port)])


def selftest():
    import tempfile
    global DATA_DIR
    with tempfile.TemporaryDirectory() as td:
        DATA_DIR = Path(td)
        f = session_file("proj")
        append(f, {"id": "a1b2", "type": "action", "status": "open",
                   "background": "bg", "why": "w", "rec": "r", "ts": 1})
        append(f, {"id": "a1b2", "status": "done", "ts": 2})
        s = fold(f)
        assert s["a1b2"]["status"] == "done", "update must apply"
        assert s["a1b2"]["why"] == "w", "partial update must preserve other fields"
        with open(f, "a") as fh:
            fh.write("not json\n")
        assert fold(f)["a1b2"]["background"] == "bg", "malformed line must not break fold"
        i1 = add_term(f, "FVA", "v1", "t1")
        i2 = add_term(f, "fva", "v2", "t2")
        assert i1 == i2, "term dedupe is case-insensitive"
        assert fold(f)[i1]["intuitive"] == "v2", "term re-add updates definitions"
        assert find_file_with_id("a1b2") == f
        assert find_file_with_id("ffff") is None
        i3 = add_event("proj", {"type": "task", "status": "open", "what": "x"})
        update(i3, {"progress": "50%"})
        assert fold(f)[i3]["progress"] == "50%" and fold(f)[i3]["what"] == "x"
        assert len(i3) == 4
    print("ok")


def main():
    p = argparse.ArgumentParser(prog="table-talk", description=__doc__)
    p.add_argument("--selftest", action="store_true", help=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="cmd")

    a = sub.add_parser("action", help="record a decision needed from the user")
    a.add_argument("background")
    a.add_argument("--why", required=True)
    a.add_argument("--rec", required=True)
    a.add_argument("--project")

    t = sub.add_parser("task", help="record background work")
    t.add_argument("what")
    t.add_argument("--project")

    pr = sub.add_parser("progress", help="update progress on an item")
    pr.add_argument("id")
    pr.add_argument("text")

    d = sub.add_parser("done", help="mark an item done")
    d.add_argument("id")

    tm = sub.add_parser("term", help="record a technical term")
    tm.add_argument("term")
    tm.add_argument("--intuitive", required=True)
    tm.add_argument("--technical", required=True)
    tm.add_argument("--project")

    sh = sub.add_parser("show", help="dump folded state")
    sh.add_argument("project", nargs="?")

    sv = sub.add_parser("serve", help="launch the dashboard")
    sv.add_argument("--port", type=int, default=8731)

    args = p.parse_args()
    if args.selftest:
        selftest()
    elif args.cmd == "action":
        print(add_event(args.project, {"type": "action", "status": "open",
              "background": args.background, "why": args.why, "rec": args.rec}))
    elif args.cmd == "task":
        print(add_event(args.project, {"type": "task", "status": "open", "what": args.what}))
    elif args.cmd == "progress":
        update(args.id, {"progress": args.text})
    elif args.cmd == "done":
        update(args.id, {"status": "done"})
    elif args.cmd == "term":
        print(add_term(session_file(args.project), args.term, args.intuitive, args.technical))
    elif args.cmd == "show":
        cmd_show(args.project)
    elif args.cmd == "serve":
        cmd_serve(args.port)
    else:
        p.print_help()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Make executable, run selftest**

Run: `chmod +x bin/table-talk && python3 bin/table-talk --selftest`
Expected: `ok` (exit 0). If any assert fires, fix the implementation — the asserts are the spec.

- [ ] **Step 3: Smoke-test real subcommands against a temp dir**

Run:
```bash
export TABLE_TALK_DIR=$(mktemp -d)
id=$(bin/table-talk action "pick genome" --why "blocks run" --rec "R64" --project demo)
bin/table-talk done "$id"
bin/table-talk term "FBA" --intuitive "best-case flow" --technical "LP over stoichiometry" --project demo
bin/table-talk show demo
unset TABLE_TALK_DIR
```
Expected: `show` prints one `== <date>-demo` header, the action folded with `"status": "done"` and all original fields, and the term.

- [ ] **Step 4: Commit**

```bash
git add bin/table-talk
git commit -m "feat: stdlib CLI - JSONL event log with shallow-merge fold

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Dashboard — `bin/table-talk-dash.py`

**Files:**
- Create: `bin/table-talk-dash.py` (executable)

**Interfaces:**
- Consumes: the JSONL event contract from Task 1 (same `$TABLE_TALK_DIR`, same fold semantics — reimplemented here, pinned by this file's own selftest).
- Produces: `uv run --script bin/table-talk-dash.py [--port N] [--selftest]`; HTTP UI on `127.0.0.1:<port>`.

- [ ] **Step 1: Write the file**

```python
#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["nicegui>=3.16,<4"]
# ///
"""table-talk dashboard: live NiceGUI view of the table-talk event logs."""
import argparse
import json
import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("TABLE_TALK_DIR", str(Path.home() / ".local/share/table-talk")))

COLS = {
    "action": ["id", "background", "why", "rec"],
    "task": ["id", "what", "progress"],
    "term": ["term", "intuitive", "technical"],
    "done": ["id", "type", "summary"],
}


def fold(path):
    """Shallow-merge events by id, in file order. Same contract as bin/table-talk."""
    state = {}
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            ev = json.loads(line)
            state[ev["id"]] = {**state.get(ev["id"], {}), **ev}
        except (json.JSONDecodeError, TypeError, KeyError):
            pass
    return state


def rows(state, typ, done=None):
    out = [e for e in state.values() if e.get("type") == typ
           and (done is None or (e.get("status") == "done") == done)]
    return sorted(out, key=lambda e: e.get("ts", 0), reverse=True)


def done_rows(state):
    return [{"id": e["id"], "type": e["type"],
             "summary": e.get("background") or e.get("what", "")}
            for e in rows(state, "action", done=True) + rows(state, "task", done=True)]


def columns(typ):
    return [{"name": c, "label": c.capitalize(), "field": c, "align": "left",
             "sortable": True} for c in COLS[typ]]


def selftest():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "x.jsonl"
        p.write_text(
            '{"id":"a1b2","type":"action","status":"open","background":"bg","why":"w","rec":"r","ts":1}\n'
            '{"id":"a1b2","status":"done","ts":2}\n'
            'garbage\n'
            '{"id":"c3d4","type":"task","status":"open","what":"train","ts":3}\n'
            '{"id":"c3d4","progress":"epoch 3","ts":4}\n'
            '{"id":"e5f6","type":"term","term":"FBA","intuitive":"i","technical":"t","ts":5}\n')
        s = fold(p)
        assert s["a1b2"]["why"] == "w" and s["a1b2"]["status"] == "done", "merge preserves fields"
        assert rows(s, "action", done=False) == []
        assert rows(s, "task", done=False)[0]["progress"] == "epoch 3"
        assert [r["term"] for r in rows(s, "term")] == ["FBA"], "terms are cumulative"
        assert done_rows(s) == [{"id": "a1b2", "type": "action", "summary": "bg"}]
        assert [c["name"] for c in columns("action")] == ["id", "background", "why", "rec"]
    print("ok")


def main(port):
    from nicegui import ui

    @ui.refreshable
    def view():
        files = sorted(DATA_DIR.glob("*.jsonl"), reverse=True)
        if not files:
            ui.label("no sessions yet - record something with the table-talk CLI").classes("m-8 text-gray-500")
            return
        for path in files:
            state = fold(path)
            with ui.card().classes("w-full"):
                ui.label(path.stem).classes("text-lg font-bold")
                ui.label("🔴 Actions needed")
                ui.table(columns=columns("action"), rows=rows(state, "action", done=False), row_key="id").classes("w-full")
                ui.label("🔵 Background work")
                ui.table(columns=columns("task"), rows=rows(state, "task", done=False), row_key="id").classes("w-full")
                ui.label("📖 Glossary (cumulative)")
                ui.table(columns=columns("term"), rows=rows(state, "term"), row_key="term").classes("w-full")
                if (dr := done_rows(state)):
                    with ui.expansion(f"{len(dr)} done").classes("w-full opacity-50"):
                        ui.table(columns=columns("done"), rows=dr, row_key="id").classes("w-full")

    view()
    # ponytail: full re-glob+refold every tick per tab; mtime-gate if files reach hundreds
    ui.timer(2.0, view.refresh)
    ui.run(host="127.0.0.1", port=port, show=False, reload=False, title="table-talk")


if __name__ in {"__main__", "__mp_main__"}:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8731)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        selftest()
    else:
        main(a.port)
```

- [ ] **Step 2: Make executable, run selftest (this also exercises the uv/PEP 723 path)**

Run: `chmod +x bin/table-talk-dash.py && uv run --script bin/table-talk-dash.py --selftest`
Expected: uv resolves nicegui (first run: a few seconds), prints `ok`.

- [ ] **Step 3: Live smoke test**

Run:
```bash
export TABLE_TALK_DIR=$(mktemp -d)
python3 bin/table-talk action "smoke" --why "w" --rec "r" --project smoke
(uv run --script bin/table-talk-dash.py --port 8799 &) ; sleep 6
curl -s http://127.0.0.1:8799/ | grep -o "table-talk" | head -1
pkill -f table-talk-dash || true
unset TABLE_TALK_DIR
```
Expected: `table-talk` printed (page title served). NiceGUI renders rows via websocket so curl only proves the server is up — that is enough for smoke.

- [ ] **Step 4: Commit**

```bash
git add bin/table-talk-dash.py
git commit -m "feat: NiceGUI live dashboard (PEP 723, uv run)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: The skill — `skill/SKILL.md`

**Files:**
- Create: `skill/SKILL.md`

**Interfaces:**
- Consumes: CLI subcommand surface from Task 1 (exact flags: `action <bg> --why --rec`, `task <what>`, `progress <id> <text>`, `done <id>`, `term <term> --intuitive --technical`, `serve`).
- Produces: the skill installed later at `~/.claude/skills/table-talk/SKILL.md` (via symlinked dir).

- [ ] **Step 1: REQUIRED — invoke superpowers:writing-skills, then write SKILL.md following it**

Invoke the `superpowers:writing-skills` skill and apply its conventions (frontmatter format, trigger-rich description, token economy) to this draft — keep the protocol content, improve the form if the skill says so:

```markdown
---
name: table-talk
description: Use in EVERY conversation, at the start, before any other work - structured collaboration protocol with Gabriel. Record decisions you need from him, background work, and technical jargon via the table-talk CLI, and end every reply with action/background/term tables using 4-hex IDs he references back ("a3f9: option 2").
---

# table-talk

Structured communication protocol. State: `~/.local/share/table-talk/*.jsonl`
(one file per date+project). Live view: `table-talk serve` → http://127.0.0.1:8731

## Record as you go

| Moment | Command |
|---|---|
| A decision only Gabriel can make | `table-talk action "<background>" --why "<why it matters>" --rec "<your recommendation>"` → prints ID |
| Background work starts | `table-talk task "<what>"` → prints ID |
| Background work advances | `table-talk progress <id> "<update>"` |
| Item answered/finished | `table-talk done <id>` |
| Jargon first used | `table-talk term "<term>" --intuitive "<plain one-liner>" --technical "<precise definition>"` |

Project defaults to basename of cwd; override with `--project`.
Record BEFORE writing the reply so the dashboard and the reply never disagree.

## Every reply ends with these tables (omit a table only when truly empty)

**🔴 Actions needed from you**
| ID | Background | Why it matters | Recommendation |

**🔵 Background work**
| ID | What | Progress |

**📖 Terms in this reply**
| Term | Intuitive | Technical |

Term table lists only terms new or load-bearing for THIS reply — the dashboard
holds the cumulative glossary. Intuitive = one plain-English sentence a
newcomer follows; technical = the precise definition with jargon spelled out.

## Responding to Gabriel

- He references rows by ID ("a3f9: go with 2"). Act on that item, then `table-talk done a3f9`.
- IDs are 4 hex chars, printed by the CLI — never invent one; always use the printed value.
- At session start, mention `table-talk serve` once if the dashboard may not be running.
```

- [ ] **Step 2: Verify frontmatter parses and CLI references match Task 1**

Run: `head -5 skill/SKILL.md` and `grep -o 'table-talk [a-z]*' skill/SKILL.md | sort -u`
Expected: frontmatter has exactly `name` and `description`; subcommands mentioned ⊆ {action, task, progress, done, term, serve, show}.

- [ ] **Step 3: Commit**

```bash
git add skill/SKILL.md
git commit -m "feat: table-talk reply-protocol skill

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Installer, test suite, README

**Files:**
- Create: `install.sh`, `test.sh`, `README.md` (both .sh executable)

**Interfaces:**
- Consumes: `bin/table-talk`, `bin/table-talk-dash.py`, `skill/` from Tasks 1–3.
- Produces: `~/.local/bin/table-talk` symlink, `~/.claude/skills/table-talk` dir symlink, `~/.local/share/table-talk/` data dir; `./test.sh` exits 0.

- [ ] **Step 1: Write `install.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
mkdir -p ~/.local/bin ~/.local/share/table-talk ~/.claude/skills
chmod +x "$here/bin/table-talk" "$here/bin/table-talk-dash.py"
ln -sf "$here/bin/table-talk" ~/.local/bin/table-talk
ln -sfn "$here/skill" ~/.claude/skills/table-talk
echo "installed: ~/.local/bin/table-talk, skill -> ~/.claude/skills/table-talk, data dir ready"
```

- [ ] **Step 2: Write `test.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
python3 "$here/bin/table-talk" --selftest
uv run --script "$here/bin/table-talk-dash.py" --selftest
echo "all selftests passed"
```

- [ ] **Step 3: Write `README.md`**

```markdown
# table-talk

Structured collaboration between you and Claude: every Claude reply ends with
three tables — decisions it needs from you, background work in flight, and
technical terms explained twice (intuitive + precise). Everything is also an
event in a local log, and a live dashboard shows every session's tables with
the full cumulative glossary.

## How it works

- **CLI** (`bin/table-talk`, Python stdlib, zero deps): appends events to
  `~/.local/share/table-talk/<date>-<project>.jsonl`. Current state is a
  shallow-merge fold by 4-hex id — append-only, safe for concurrent sessions.
- **Dashboard** (`bin/table-talk-dash.py`, [NiceGUI](https://nicegui.io) via
  `uv run`, PEP 723): `table-talk serve` → http://127.0.0.1:8731 — one card
  per session, sortable tables, refreshes every 2 s, done items collapsed.
- **Skill** (`skill/SKILL.md`): tells Claude to record as it works and to end
  every reply with the tables, using ids you can reference back ("a3f9: option 2").

## Install

Requires Python ≥3.12, [uv](https://docs.astral.sh/uv/) (dashboard only), and
Claude Code (skill).

    git clone git@github.com:gabenavarro/table-talk.git
    cd table-talk && ./install.sh

## Use

    table-talk action "Choose ref genome" --why "blocks training" --rec "R64-1-1"
    table-talk task "Training GPN model"
    table-talk progress b210 "epoch 3/10"
    table-talk done a3f9
    table-talk term "FVA" --intuitive "range of possible flux" --technical "LP min/max per reaction at fixed optimum"
    table-talk show            # plain-text dump
    table-talk serve           # dashboard

## Test

    ./test.sh
```

- [ ] **Step 4: Run installer and suite**

Run: `chmod +x install.sh test.sh && ./install.sh && ./test.sh && ls -la ~/.local/bin/table-talk ~/.claude/skills/table-talk`
Expected: both selftests print `ok`, `all selftests passed`, both symlinks point into this repo.

- [ ] **Step 5: Commit**

```bash
git add install.sh test.sh README.md
git commit -m "feat: installer, test suite, README

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Publish and verify end-to-end

**Files:**
- None new — repo operations + verification.

**Interfaces:**
- Consumes: everything above; `gh` authenticated as gabenavarro (ssh).
- Produces: public https://github.com/gabenavarro/table-talk with all commits pushed; working installed tool.

- [ ] **Step 1: Create the public repo and push**

Run:
```bash
gh repo create gabenavarro/table-talk --public --source . --push \
  --description "Structured Claude collaboration: action/background/term tables with a live NiceGUI dashboard"
```
Expected: repo URL printed; `git push` succeeds on `main`.

- [ ] **Step 2: End-to-end check through the installed symlink**

Run:
```bash
cd /tmp && export TABLE_TALK_DIR=$(mktemp -d)
id=$(table-talk action "e2e check" --why "verify install" --rec "n/a" --project e2e)
table-talk done "$id" && table-talk show e2e
(table-talk serve --port 8798 &) ; sleep 6
curl -sf http://127.0.0.1:8798/ >/dev/null && echo SERVE_OK
pkill -f table-talk-dash || true; unset TABLE_TALK_DIR
```
Expected: folded action shows `"status": "done"` with fields intact; `SERVE_OK`.

- [ ] **Step 3: Verify remote state**

Run: `gh repo view gabenavarro/table-talk --json url,visibility -q '.url + " " + .visibility'` and `git status --short`
Expected: URL + `PUBLIC`; clean working tree.
