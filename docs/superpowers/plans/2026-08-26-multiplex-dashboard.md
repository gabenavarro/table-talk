# Multiplex Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the table-talk dashboard as the approved Multiplex design — a tiling wall of tmux-flagged session windows, prose `why`/`rec`, and a foldable project-grouped session tree — without touching the CLI or the JSONL format.

**Architecture:** Pure logic (fold, percent, grouping, roll-up, sort, filter, packing) moves into a new stdlib-only sibling module `bin/tt_model.py` so it is testable with plain `python3` and no server. The stylesheet moves to `bin/tt.css`, read at startup. `bin/table-talk-dash.py` keeps its PEP 723 header and becomes rendering only: it builds windows once per file-set change, re-packs them between column containers via `Element.move()` (which preserves element identity, so no flicker), and rebuilds only the body of a window whose data actually changed.

**Tech Stack:** Python 3.12+, NiceGUI 3.16 (Quasar/Vue), `uv run --script` (PEP 723), stdlib `re`/`json`/`pathlib`. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-26-multiplex-dashboard-design.md`

## Global Constraints

- **Never modify `bin/table-talk` or the JSONL event format.** The CLI is stdlib-only by design and recording must never gain a new failure mode.
- **The fold contract is fixed:** current state = shallow-merge by id, in file order — `state[id] = {**state.get(id, {}), **event}`. A status-only append must preserve `background`/`why`/etc.
- **`bin/tt_model.py` imports nothing outside the Python standard library.** It must run under bare `python3`.
- **`bin/table-talk-dash.py` keeps its PEP 723 header** (`requires-python = ">=3.12"`, `dependencies = ["nicegui>=3.16,<4"]`) and its `#!/usr/bin/env -S uv run --script` shebang.
- **No new dependencies.** Not for fonts, not for drag-and-drop, not for testing.
- **Fonts must not require the network.** The dashboard is loopback-only; declare real fallback stacks (`ui-monospace, SF Mono, Menlo, Consolas, monospace` and `system-ui, -apple-system, Segoe UI, sans-serif`).
- **Tests are assert-based selftests, no framework, no fixtures.** `test.sh` is the whole suite.
- **All user text renders through `ui.label`**, which escapes. Never `ui.html` with event content.
- **Exact palette values** are in the spec's palette table and are copied verbatim — dark ground `#1d2021`, dark surface `#282828`, light ground `#e8e6dc`, light surface `#faf9f5`, cursor `#8ec07c` dark / `#d97757` light.
- **Persisted UI state lives in `app.storage.general`** (no `storage_secret` needed): `theme`, `sort`, `cols`, `drawer_open`, `marks`, `folds`, `groups_folded`, `scope`.
- **Commit after every task**, using the message given in that task's final step.

---

### Task 1: `tt_model.py` — module skeleton, fold, and session stems

Creates the stdlib-only model module by moving `fold`/`fold_cached` out of the dashboard, and adds session-stem parsing. This is the foundation every later task imports.

**Files:**
- Create: `bin/tt_model.py`
- Modify: `test.sh`

**Interfaces:**
- Consumes: nothing.
- Produces: `fold(path) -> dict[str, dict]`, `fold_cached(path) -> dict[str, dict]`, `parse_stem(stem: str) -> tuple[str, str]`, `selftest() -> None`, and module constant `DATA_DIR: Path`.

- [ ] **Step 1: Write the failing test**

Create `bin/tt_model.py` containing only the test, so the module exists and its selftest fails for the right reason:

```python
#!/usr/bin/env python3
"""table-talk data model: everything the dashboard computes, with no UI and no
dependencies outside the standard library, so it can be selftested with plain
python3 and reasoned about without starting a server."""
import argparse
import json
import os
import re
import time
from pathlib import Path

DATA_DIR = Path(os.environ.get("TABLE_TALK_DIR") or str(Path.home() / ".local/share/table-talk"))


def selftest():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "2026-08-26-phephree.jsonl"
        p.write_text(
            '{"id":"a1b2","type":"action","status":"open","background":"bg","why":"w","rec":"r","ts":1}\n'
            '{"id":"a1b2","status":"done","ts":2}\n'
            'garbage\n'
            '{"id":"c3d4","type":"task","status":"open","what":"train","ts":3}\n')
        s = fold(p)
        assert s["a1b2"]["why"] == "w", "a partial update must preserve other fields"
        assert s["a1b2"]["status"] == "done", "a partial update must apply"
        assert s["c3d4"]["what"] == "train"
        assert fold(Path(td) / "missing.jsonl") == {}, "a missing file folds to empty"
        with open(p, "ab") as fh:
            fh.write(b"\xff\xfe bad utf8\n")
        assert fold(p)["a1b2"]["why"] == "w", "invalid utf-8 must not break fold"
        assert fold_cached(p) == fold(p), "cached fold matches uncached"
        before = fold_cached(p)
        with open(p, "a") as fh:
            fh.write('{"id":"e5f6","type":"term","term":"Z","ts":9}\n')
        assert "e5f6" in fold_cached(p) and "e5f6" not in before, "cache invalidates on change"
        assert fold_cached(Path(td) / "missing.jsonl") == {}, "cached fold tolerates missing file"
    assert parse_stem("2026-08-26-phephree") == ("2026-08-26", "phephree")
    assert parse_stem("2026-08-26-gcp-aws-xfer") == ("2026-08-26", "gcp-aws-xfer"), \
        "a project name may contain hyphens"
    assert parse_stem("no-date-here") == ("", "no-date-here"), "an undated stem is all project"
    print("ok")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.parse_args()
    selftest()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 bin/tt_model.py --selftest`
Expected: `NameError: name 'fold' is not defined`

- [ ] **Step 3: Write the implementation**

Insert these four definitions immediately after the `DATA_DIR` line:

```python
_STEM = re.compile(r"^(\d{4}-\d{2}-\d{2})-(.+)$")


def fold(path):
    """Current state of one file: shallow-merge events by id, in file order.
    This contract is shared with bin/table-talk; both selftests pin it."""
    state = {}
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return state
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            ev = json.loads(line.decode("utf-8"))
            eid = str(ev["id"])
            state[eid] = {**state.get(eid, {}), **ev}
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, KeyError):
            pass
    return state


_fold_cache = {}  # path -> ((st_mtime, st_size), folded_state)


def fold_cached(path):
    """fold(), but re-parse a file only when its mtime/size changed. Steady state
    collapses the 2 s tick to O(files stat) regardless of history size."""
    try:
        st = path.stat()
    except FileNotFoundError:
        return {}
    key = (st.st_mtime, st.st_size)
    hit = _fold_cache.get(path)
    if hit and hit[0] == key:
        return hit[1]
    state = fold(path)
    _fold_cache[path] = (key, state)
    return state


def parse_stem(stem):
    """'2026-08-26-phephree' -> ('2026-08-26', 'phephree'). The project half is
    greedy so hyphenated project names survive. An undated stem is all project."""
    m = _STEM.match(stem)
    return (m.group(1), m.group(2)) if m else ("", stem)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 bin/tt_model.py --selftest`
Expected: `ok`

- [ ] **Step 5: Wire it into `test.sh`**

Replace the whole file with:

```bash
#!/usr/bin/env bash
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
python3 "$here/bin/table-talk" --selftest
python3 "$here/bin/tt_model.py" --selftest
uv run --script "$here/bin/table-talk-dash.py" --selftest
echo "all selftests passed"
```

- [ ] **Step 6: Run the whole suite**

Run: `./test.sh`
Expected: three `ok` lines then `all selftests passed`

- [ ] **Step 7: Commit**

```bash
git add bin/tt_model.py test.sh
git commit -m "feat(model): stdlib-only tt_model with fold and session-stem parsing"
```

---

### Task 2: `percent()` — derive completion from free-text progress

The progress field stays free text. This reads a percentage out of it, and refuses to invent one.

**Files:**
- Modify: `bin/tt_model.py`

**Interfaces:**
- Consumes: `bin/tt_model.py` module from Task 1.
- Produces: `percent(text: str | None) -> int | None` — 0–100, or `None` meaning indeterminate.

- [ ] **Step 1: Write the failing test**

Add to `selftest()`, immediately before `print("ok")`:

```python
    # percent: an explicit percentage always beats a fraction in the same string.
    # This exact string is real progress from the alpha-lac campaign; reading the
    # trailing '8/8 GPUs busy' would claim a 58%-done run had finished.
    assert percent("401 cells sealed, 1747 passing (58% of 3000). 8/8 GPUs busy") == 58
    assert percent("epoch 3/10") == 30
    assert percent("3 of 10 shards") == 30
    assert percent("100%") == 100
    assert percent("58.6% done") == 59, "percentages round to the nearest integer"
    assert percent("done 12/8") == 100, "over-complete clamps to 100"
    assert percent("0/10") == 0
    assert percent("split 5/0") is None, "a zero denominator is not a percentage"
    assert percent("Last sync 16:01:58Z OK, 399 sealed cells") is None
    assert percent("Blocked on capacity (see 9a70), not on code") is None
    assert percent("Seeds 8042000-8042328 planned") is None, "a numeric range is not a fraction"
    assert percent("") is None and percent(None) is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 bin/tt_model.py --selftest`
Expected: `NameError: name 'percent' is not defined`

- [ ] **Step 3: Write the implementation**

Add after `parse_stem`:

```python
_PCT = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")
_FRAC = re.compile(r"(\d+)\s*(?:/|\s+of\s+)\s*(\d+)")


def percent(text):
    """Completion read out of free-text progress, or None when it cannot be read.

    An explicit percentage always wins over a fraction, which is what stops
    '(58% of 3000). 8/8 GPUs busy' from reporting a finished run. Anything
    unreadable returns None and is drawn as an indeterminate sweep rather than
    an invented number.

    Known limitation, accepted: a bare '8/8 GPUs busy' with no percentage
    anywhere reads as 100%. Writing an explicit percentage is the reliable form.
    """
    if not text:
        return None
    m = _PCT.search(text)
    if m:
        return max(0, min(100, round(float(m.group(1)))))
    m = _FRAC.search(text)
    if m:
        num, den = int(m.group(1)), int(m.group(2))
        if den > 0:
            return max(0, min(100, round(100 * num / den)))
    return None
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 bin/tt_model.py --selftest`
Expected: `ok`

- [ ] **Step 5: Commit**

```bash
git add bin/tt_model.py
git commit -m "feat(model): derive percent-done from free-text progress"
```

---

### Task 3: `summarize()` and `roll_up()` — the meter arithmetic

Every drawer row shows resolved ÷ recorded. A project sums its sessions rather than averaging their percentages.

**Files:**
- Modify: `bin/tt_model.py`

**Interfaces:**
- Consumes: `fold` from Task 1.
- Produces: `summarize(state: dict) -> dict` and `roll_up(summaries: list[dict]) -> dict`, both returning keys `open_actions`, `open_tasks`, `resolved`, `recorded`, `pct`, `latest` (all `int`).

- [ ] **Step 1: Write the failing test**

Add to `selftest()` before `print("ok")`:

```python
    # summarize: terms are reference material, never an obligation, so they are
    # counted in neither resolved nor recorded.
    st = {"a": {"type": "action", "status": "open", "ts": 10},
          "b": {"type": "action", "status": "done", "ts": 20},
          "c": {"type": "task", "status": "open", "ts": 30},
          "d": {"type": "task", "status": "done", "ts": 40},
          "e": {"type": "term", "term": "x", "ts": 50}}
    s = summarize(st)
    assert s["open_actions"] == 1 and s["open_tasks"] == 1
    assert s["resolved"] == 2 and s["recorded"] == 4, "terms are excluded from the meter"
    assert s["pct"] == 50
    assert s["latest"] == 50, "latest spans every event, terms included"
    assert summarize({})["pct"] == 100, "nothing recorded means nothing outstanding"
    assert summarize({})["latest"] == 0

    # roll_up: sum the sessions, never average their percentages. These are the
    # real phephree numbers: 0/5 on the busy day and 4/7 on the quiet one.
    busy = {"open_actions": 3, "open_tasks": 2, "resolved": 0, "recorded": 5, "pct": 0, "latest": 99}
    quiet = {"open_actions": 1, "open_tasks": 2, "resolved": 4, "recorded": 7, "pct": 57, "latest": 50}
    r = roll_up([busy, quiet])
    assert (r["resolved"], r["recorded"]) == (4, 12)
    assert r["pct"] == 33, "summed, not averaged - averaging would give 29"
    assert r["open_actions"] == 4 and r["open_tasks"] == 4
    assert r["latest"] == 99, "a group is as recent as its newest session"
    assert roll_up([])["pct"] == 100 and roll_up([])["latest"] == 0
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 bin/tt_model.py --selftest`
Expected: `NameError: name 'summarize' is not defined`

- [ ] **Step 3: Write the implementation**

Add after `percent`:

```python
_COUNTED = ("action", "task")


def summarize(state):
    """Counts for one folded session file.

    resolved/recorded covers actions and tasks only: a glossary term is
    reference material, not something you owe anyone. latest spans every event
    including terms, because it answers 'when did this session last do
    anything', not 'when did an obligation move'.
    """
    open_a = open_t = resolved = recorded = latest = 0
    for ev in state.values():
        latest = max(latest, ev.get("ts", 0))
        typ = ev.get("type")
        if typ not in _COUNTED:
            continue
        recorded += 1
        if ev.get("status") == "done":
            resolved += 1
        elif typ == "action":
            open_a += 1
        else:
            open_t += 1
    return {"open_actions": open_a, "open_tasks": open_t,
            "resolved": resolved, "recorded": recorded,
            "pct": 100 if recorded == 0 else round(100 * resolved / recorded),
            "latest": latest}


def roll_up(summaries):
    """A project's numbers are the SUM of its sessions', never the average of
    their percentages: averaging lets a quiet day outvote a busy one."""
    tot = {k: sum(s[k] for s in summaries)
           for k in ("open_actions", "open_tasks", "resolved", "recorded")}
    tot["latest"] = max((s["latest"] for s in summaries), default=0)
    tot["pct"] = 100 if tot["recorded"] == 0 else round(100 * tot["resolved"] / tot["recorded"])
    return tot
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 bin/tt_model.py --selftest`
Expected: `ok`

- [ ] **Step 5: Commit**

```bash
git add bin/tt_model.py
git commit -m "feat(model): session summaries and summed project roll-up"
```

---

### Task 4: `group_sessions()` and `sort_groups()` — the drawer tree

Two `phephree` files become one heading with two children. A project with one session stays a single flat row.

**Files:**
- Modify: `bin/tt_model.py`

**Interfaces:**
- Consumes: `parse_stem`, `summarize`, `roll_up`.
- Produces: `group_sessions(sessions: list[tuple[str, dict]]) -> list[dict]` where each group has `project: str`, `sessions: list[dict]` (each with `key`, `date`, `index`, `summary`), plus the roll-up keys; and `sort_groups(groups: list[dict], mode: str) -> list[dict]` with `SORTS = ("recent", "actions", "project")`.

- [ ] **Step 1: Write the failing test**

Add to `selftest()` before `print("ok")`:

```python
    def _sess(n_open_actions, ts):
        st = {f"o{i}": {"type": "action", "status": "open", "ts": ts}
              for i in range(n_open_actions)}
        st["z"] = {"type": "action", "status": "done", "ts": ts}
        return st

    sessions = [("2026-08-26-phephree", _sess(3, 900)),
                ("2026-08-26-table-talk", _sess(2, 950)),
                ("2026-08-25-phephree", _sess(1, 500)),
                ("2026-08-24-gcp-aws-xfer", _sess(0, 100))]
    groups = group_sessions(sessions)
    assert [g["project"] for g in groups] == ["phephree", "table-talk", "gcp-aws-xfer"]
    phe = groups[0]
    assert len(phe["sessions"]) == 2 and phe["open_actions"] == 4
    assert [s["index"] for s in phe["sessions"]] == [0, 1], "index 0 is the newest file"
    assert [s["date"] for s in phe["sessions"]] == ["2026-08-26", "2026-08-25"]
    assert phe["sessions"][0]["key"] == "2026-08-26-phephree"
    assert len(groups[2]["sessions"]) == 1, "a single-session project is still a group of one"

    by_recent = sort_groups(groups, "recent")
    assert [g["project"] for g in by_recent] == ["table-talk", "phephree", "gcp-aws-xfer"]
    by_actions = sort_groups(groups, "actions")
    assert [g["project"] for g in by_actions] == ["phephree", "table-talk", "gcp-aws-xfer"]
    by_project = sort_groups(groups, "project")
    assert [g["project"] for g in by_project] == ["gcp-aws-xfer", "phephree", "table-talk"]
    assert [s["date"] for s in by_project[1]["sessions"]] == ["2026-08-26", "2026-08-25"], \
        "children always read newest-first whatever the group order"
    assert sort_groups(groups, "nonsense") == by_recent, "an unknown sort falls back to recent"
    assert SORTS == ("recent", "actions", "project")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 bin/tt_model.py --selftest`
Expected: `NameError: name 'group_sessions' is not defined`

- [ ] **Step 3: Write the implementation**

Add after `roll_up`:

```python
SORTS = ("recent", "actions", "project")


def group_sessions(sessions):
    """[(stem, folded_state)] -> one group per project, newest file first.

    index is the tmux window index within the project: phephree:0 is the newest
    phephree file, phephree:1 the one before it. Groups keep first-seen order;
    sort_groups imposes the display order.
    """
    order, by_project = [], {}
    for key, state in sessions:
        date, project = parse_stem(key)
        if project not in by_project:
            by_project[project] = []
            order.append(project)
        by_project[project].append({"key": key, "date": date, "summary": summarize(state)})
    groups = []
    for project in order:
        rows = sorted(by_project[project], key=lambda r: -r["summary"]["latest"])
        for i, row in enumerate(rows):
            row["index"] = i
        group = roll_up([r["summary"] for r in rows])
        group["project"] = project
        group["sessions"] = rows
        groups.append(group)
    return groups


def sort_groups(groups, mode):
    """Order the drawer. Every key falls back to recency so the order is total
    and stable; children always read newest-first regardless of group order."""
    if mode == "project":
        key = lambda g: (g["project"].lower(), -g["latest"])
    elif mode == "actions":
        key = lambda g: (-g["open_actions"], -g["latest"])
    else:
        key = lambda g: (-g["latest"], g["project"].lower())
    return sorted(groups, key=key)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 bin/tt_model.py --selftest`
Expected: `ok`

- [ ] **Step 5: Commit**

```bash
git add bin/tt_model.py
git commit -m "feat(model): group sessions by project with sortable roll-ups"
```

---

### Task 5: `weight()` and `pack()` — the deterministic wall packer

Masonry is forbidden: it reassigns columns on every content change, sliding a window out from under the reader. This packs from an estimated weight instead.

**Files:**
- Modify: `bin/tt_model.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `weight(state: dict) -> int` and `pack(keys: list[str], ncols: int, weights: dict[str, int], marked=()) -> list[list[str]]`.

- [ ] **Step 1: Write the failing test**

Add to `selftest()` before `print("ok")`:

```python
    # weight: derived from content, never from measured pixels. A done item costs
    # nothing because it lives in a collapsed section.
    assert weight({}) == 1, "an empty window still occupies a titlebar"
    one_action = {"a": {"type": "action", "status": "open",
                        "background": "b", "why": "w", "rec": "r", "ts": 1}}
    assert weight(one_action) == 4
    assert weight({"a": {"type": "task", "status": "open", "what": "x", "ts": 1}}) == 3
    assert weight({"a": {"type": "action", "status": "done",
                         "background": "b" * 500, "ts": 1}}) == 1, "done items are collapsed"
    assert weight({"a": {"type": "term", "term": "x", "ts": 1}}) == 1, "terms are collapsed"
    longer = {"a": dict(one_action["a"], why="w" * 220)}
    assert weight(longer) > weight(one_action), "long prose costs extra lines"
    # progress text must NOT change the weight - it changes on nearly every poll
    job = {"a": {"type": "task", "status": "open", "what": "x", "progress": "1/10", "ts": 1}}
    job2 = {"a": {"type": "task", "status": "open", "what": "x",
                  "progress": "epoch 9/10, still running, no interruption in 45 h", "ts": 1}}
    assert weight(job) == weight(job2), "progress text must not affect packing"

    # pack: greedy shortest column, deterministic, marked first.
    w = {"a": 14, "b": 9, "c": 8, "d": 2}
    # 2 cols: a->0(14) b->1(9) c->1(17) d->0(16)
    assert pack(["a", "b", "c", "d"], 2, w) == [["a", "d"], ["b", "c"]]
    # 3 cols: a->0(14) b->1(9) c->2(8) d->2(10) - d joins the lightest column
    assert pack(["a", "b", "c", "d"], 3, w) == [["a"], ["b"], ["c", "d"]]
    assert pack(["a", "b", "c", "d"], 1, w) == [["a", "b", "c", "d"]]
    assert pack(["a", "b", "c", "d"], 2, w) == pack(["a", "b", "c", "d"], 2, w), \
        "identical inputs must produce an identical layout"
    assert pack(["a", "b", "c", "d"], 2, w, marked={"d"})[0][0] == "d", \
        "a marked window lands at the top of the leftmost column"
    assert pack([], 2, {}) == [[]], "no windows still yields one column"
    assert pack(["a"], 3, w) == [["a"]], "columns never outnumber windows"
    assert pack(["a", "b"], 2, {}) == [["a"], ["b"]], "a missing weight defaults to 1"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 bin/tt_model.py --selftest`
Expected: `NameError: name 'weight' is not defined`

- [ ] **Step 3: Write the implementation**

Add after `sort_groups`:

```python
def weight(state):
    """A window's estimated height in arbitrary units, derived from CONTENT.

    Never from measured pixels: those change on every re-render, and a weight
    that changes makes the packer move windows while they are being read. Done
    items and terms cost nothing because they live in collapsed sections.
    Progress text is deliberately not measured - it changes on nearly every
    poll, and packing must not.
    """
    units = 1
    for ev in state.values():
        if ev.get("status") == "done":
            continue
        typ = ev.get("type")
        if typ == "action":
            chars = len(ev.get("background", "")) + len(ev.get("why", "")) + len(ev.get("rec", ""))
            units += 3 + chars // 110
        elif typ == "task":
            units += 2
    return units


def pack(keys, ncols, weights, marked=()):
    """Greedy shortest-column packing into fixed buckets.

    Marked keys are placed first, which lands them at the top of the leftmost
    column. Pure and deterministic: the same inputs always produce the same
    layout, which is what lets the caller skip a re-pack when nothing changed.
    """
    ncols = max(1, min(ncols, len(keys))) if keys else 1
    cols = [[] for _ in range(ncols)]
    load = [0] * ncols
    rank = {k: i for i, k in enumerate(keys)}
    for key in sorted(keys, key=lambda k: (k not in marked, rank[k])):
        i = load.index(min(load))
        cols[i].append(key)
        load[i] += weights.get(key, 1)
    return cols
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 bin/tt_model.py --selftest`
Expected: `ok`

- [ ] **Step 5: Commit**

```bash
git add bin/tt_model.py
git commit -m "feat(model): content-weighted deterministic wall packer"
```

---

### Task 6: `matches()` — server-side filtering

Quasar's table `filter` prop disappears with the tables, so filtering becomes ours.

**Files:**
- Modify: `bin/tt_model.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `row_text(ev: dict) -> str` and `matches(state: dict, name: str, query: str | None) -> bool`.

- [ ] **Step 1: Write the failing test**

Add to `selftest()` before `print("ok")`:

```python
    st = {"a1b2": {"id": "a1b2", "type": "action", "background": "retrain the model",
                   "why": "GPU cost", "rec": "let it finish", "ts": 1},
          "c3d4": {"id": "c3d4", "type": "term", "term": "FBA",
                   "intuitive": "flux balance", "technical": "linear program", "ts": 2}}
    assert matches(st, "2026-08-26-phephree", "") is True, "an empty query matches everything"
    assert matches(st, "2026-08-26-phephree", None) is True
    assert matches(st, "2026-08-26-phephree", "   ") is True
    assert matches(st, "2026-08-26-phephree", "phephree") is True, "the session name matches"
    assert matches(st, "2026-08-26-phephree", "PHEPHREE") is True, "matching is case-insensitive"
    assert matches(st, "2026-08-26-phephree", "retrain") is True, "background matches"
    assert matches(st, "2026-08-26-phephree", "gpu cost") is True, "why matches"
    assert matches(st, "2026-08-26-phephree", "flux") is True, "a glossary term matches"
    assert matches(st, "2026-08-26-phephree", "a1b2") is True, "an id matches"
    assert matches(st, "2026-08-26-phephree", "kubernetes") is False
    assert matches({}, "2026-08-26-empty", "anything") is False
    assert "retrain the model" in row_text(st["a1b2"])
    assert row_text({"id": 77, "type": "task"}).startswith("77"), "a non-string id is safe"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 bin/tt_model.py --selftest`
Expected: `NameError: name 'matches' is not defined`

- [ ] **Step 3: Write the implementation**

Add after `pack`:

```python
_TEXT_FIELDS = ("id", "background", "why", "rec", "what", "progress",
                "term", "intuitive", "technical")


def row_text(ev):
    """Every user-visible string on one event, joined for substring matching."""
    return " ".join(str(ev.get(f, "")) for f in _TEXT_FIELDS)


def matches(state, name, query):
    """A session survives the filter if the query hits its name or any of its rows.
    Replaces Quasar's built-in table filter, which goes away with the tables."""
    q = (query or "").strip().lower()
    if not q:
        return True
    if q in name.lower():
        return True
    return any(q in row_text(ev).lower() for ev in state.values())
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 bin/tt_model.py --selftest && ./test.sh`
Expected: `ok`, then `all selftests passed`

- [ ] **Step 5: Commit**

```bash
git add bin/tt_model.py
git commit -m "feat(model): server-side session and row filtering"
```

---

### Task 7: `bin/tt.css` — the gruvbox / claude-code-light stylesheet

The stylesheet leaves the Python string and becomes a real file, so it is editable and the exact palette values can be asserted.

**Files:**
- Create: `bin/tt.css`
- Modify: `bin/table-talk-dash.py` (remove `THEME_CSS`, load the file, update selftest)

**Interfaces:**
- Consumes: nothing.
- Produces: `CSS_PATH: Path` and `load_css() -> str` in `bin/table-talk-dash.py`.

- [ ] **Step 1: Write the failing test**

In `bin/table-talk-dash.py`, replace the three `THEME_CSS` assertions in `selftest()` with:

```python
    css = load_css()
    # both palettes, keyed the way the artifact pins them
    assert "--bg:#1d2021" in css and "--surface:#282828" in css, "gruvbox-dark ground and surface"
    assert "--bg:#e8e6dc" in css and "--surface:#faf9f5" in css, "claude-code-light ground and surface"
    assert "--caret:#8ec07c" in css and "--caret:#d97757" in css, "cursor colour per theme"
    assert "--act:#fb4934" in css and "--act:#a53a2e" in css
    assert 'body.body--dark' in css, "dark palette must key off Quasar's body--dark"
    assert "tabular-nums" in css, "digit columns must align"
    assert "prefers-reduced-motion" in css, "motion must be defeatable"
    assert "ui-monospace" in css and "system-ui" in css, "both faces need a real fallback stack"
    assert "ctp-" not in css, "the Catppuccin palette is gone"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run --script bin/table-talk-dash.py --selftest`
Expected: `NameError: name 'load_css' is not defined`

- [ ] **Step 3: Create `bin/tt.css`**

```css
/* table-talk — dark is ghostty gruvbox-dark, light is ghostty claude-code-light.
   Quasar's dark plugin toggles body--dark, so the tokens swap with the theme. */
:root{
  --bg:#e8e6dc; --surface:#faf9f5; --surface-2:#f2f0e8;
  --ink:#141413; --ink-2:#6f6e68; --ink-3:#93918a;
  --rule:color-mix(in srgb,#b0aea5 62%,transparent);
  --sel:#e8e6dc; --caret:#d97757;
  --act:#a53a2e; --act-2:#d97757;
  --job:#3668a0; --job-2:#6a9bcc;
  --gls:#8a6a10; --gls-2:#b88a28;
  --ok:#4a7038;  --ok-2:#788c5d;
  --mag:#7a4a82;
  --mono:"JetBrains Mono",ui-monospace,"SF Mono",Menlo,Consolas,monospace;
  --prose:"Fira Sans",system-ui,-apple-system,"Segoe UI",sans-serif;
}
body.body--dark{
  --bg:#1d2021; --surface:#282828; --surface-2:#32302f;
  --ink:#ebdbb2; --ink-2:#a89984; --ink-3:#928374;
  --rule:color-mix(in srgb,#928374 34%,transparent);
  --sel:#665c54; --caret:#8ec07c;
  --act:#fb4934; --act-2:#cc241d;
  --job:#83a598; --job-2:#458588;
  --gls:#fabd2f; --gls-2:#d79921;
  --ok:#b8bb26;  --ok-2:#98971a;
  --mag:#d3869b;
}

html,body{height:100%}
body{background:var(--bg);color:var(--ink);font-family:var(--mono);font-size:12px;line-height:1.55;margin:0}
.nicegui-content{padding:0;max-width:none;gap:0}
.tt-app{display:flex;flex-direction:column;height:100vh;overflow:hidden}
.tt-main{display:grid;grid-template-columns:284px 1fr;flex:1;min-height:0}
.tt-main.tt-collapsed{grid-template-columns:54px 1fr}
.tt td,.tt th,.pc,.dw-meta,.when,.sl-clock{font-variant-numeric:tabular-nums}

/* ---- drawer ---- */
.dw{border-right:1px solid var(--rule);background:var(--surface);overflow-y:auto;min-height:0}
.dw-top{display:flex;align-items:center;gap:7px;padding:7px 10px;border-bottom:1px solid var(--rule);
  font-size:11px;color:var(--ink-3)}
.dw-top .ttl{font-weight:700;color:var(--ink);letter-spacing:.05em;text-transform:uppercase;font-size:10.5px}
.dw-sort{padding:5px 10px;border-bottom:1px solid var(--rule);color:var(--ink-3);font-size:11px;cursor:pointer}
.dw-sort b{color:var(--gls);font-weight:700}
.dw-row{display:grid;grid-template-columns:15px 1fr;gap:1px 4px;width:100%;text-align:left;
  border:0;background:transparent;font:inherit;color:inherit;cursor:pointer;padding:6px 10px 7px}
.dw-row:hover{background:var(--sel)}
.dw-row:focus-visible{outline:2px solid var(--caret);outline-offset:-2px}
.dw-g{color:var(--ink-3);opacity:.88;font-size:11.5px}
.dw-l1{display:flex;align-items:baseline;gap:7px;min-width:0}
.dw-nm{font-weight:700;color:var(--ink);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.dw-sess .dw-nm{font-weight:400;color:var(--ink-2);font-size:11.5px}
.dw-sess .dw-l1,.dw-sess .dw-l2{padding-left:9px}
.dw-meta{margin-left:auto;color:var(--ink-3);font-size:10.5px;white-space:nowrap}
.dw-l2{display:flex;align-items:center;gap:6px;font-size:10.5px;margin-top:1px}
.dw-proj{border-bottom:1px solid color-mix(in srgb,var(--rule) 55%,transparent)}
.dw-on{background:var(--sel)}
.dw-on .dw-g{color:var(--caret);opacity:1}
.b-act{color:var(--act)} .b-job{color:var(--job)} .b-off{color:var(--ink-3);opacity:.5}
.mtr{margin-left:auto;color:var(--ink-3);display:flex;align-items:center;gap:1px}
.trk{display:inline-block;vertical-align:middle;width:56px;height:7px;overflow:hidden;
  background:color-mix(in srgb,var(--ink-3) 32%,transparent)}
.trk i{display:block;height:100%;background:var(--ok-2);transition:width .45s cubic-bezier(.2,0,0,1)}
.trk i.full{background:var(--ok)}
.pc{width:30px;text-align:right;color:var(--ink-2);font-size:10.5px}
.rail-item{border:0;background:transparent;font:inherit;color:inherit;cursor:pointer;width:100%;
  padding:7px 0 8px;display:flex;flex-direction:column;align-items:center;gap:3px;
  border-bottom:1px solid color-mix(in srgb,var(--rule) 45%,transparent)}
.rail-item:hover{background:var(--sel)}
.rail-ab{font-size:11px;font-weight:700;color:var(--ink)}
.rail-t{width:34px;height:5px;background:color-mix(in srgb,var(--ink-3) 32%,transparent);overflow:hidden}
.rail-t i{display:block;height:100%;background:var(--ok-2)}

/* ---- wall ---- */
.wall{display:flex;align-items:flex-start;gap:9px;padding:9px;overflow-y:auto;min-height:0}
.wall .col{flex:1;display:flex;flex-direction:column;gap:9px;min-width:0}
.win{background:var(--surface);border:1px solid var(--rule);border-radius:3px;
  display:flex;flex-direction:column;min-width:0}
.win-hot{border-color:color-mix(in srgb,var(--act) 55%,transparent)}
.win.marked{border-color:var(--caret);box-shadow:inset 2px 0 0 var(--caret)}
.win.folded .win-b{display:none}
.win-t{display:flex;align-items:center;gap:6px;padding:5px 9px;background:var(--surface-2);
  border-bottom:1px solid var(--rule);font-size:11.5px}
.win-t .nm{color:var(--ink);font-weight:700}
.win-t .ix{color:var(--ink-3)}
.bell{color:var(--act);font-weight:800;animation:bell 2.2s ease-in-out infinite}
@keyframes bell{0%,100%{opacity:1}50%{opacity:.45}}
.actv{color:var(--job);font-weight:800}
.fl-m{color:var(--caret);font-weight:800}
.fl-z{color:var(--gls);font-weight:800}
.when{margin-left:auto;color:var(--ink-3);font-size:10.5px}
.wctl{display:flex;gap:2px;margin-left:8px}
.wb{border:1px solid transparent;background:transparent;color:var(--ink-3);font:inherit;
  font-size:10px;font-weight:700;line-height:1;padding:2px 5px;border-radius:2px;cursor:pointer}
.wb:hover{color:var(--caret);border-color:var(--caret)}
.wb:focus-visible{outline:2px solid var(--caret);outline-offset:1px}
.pr{padding:7px 9px 3px;font-size:11.5px;font-weight:700;display:flex;gap:6px;align-items:baseline;cursor:pointer}
.pr .n{color:var(--ink-3);font-weight:400}
.p-act{color:var(--act)} .p-job{color:var(--job)} .p-gls{color:var(--gls)} .p-ok{color:var(--ok)}
.row{display:grid;grid-template-columns:42px 1fr;gap:8px;padding:5px 9px 8px;align-items:start;
  border-left:3px solid transparent}
.row:hover{background:var(--sel)}
.row.changed{border-left-color:var(--act)}
.row.changed-job{border-left-color:var(--job)}
.row .ttl{color:var(--ink);line-height:1.5}
/* Ledger's contribution: why/rec are prose, hung off the action on tree guides */
.sub{display:grid;grid-template-columns:20px 28px 1fr;gap:6px;margin-top:2px;
  font-family:var(--prose);font-size:12.5px;line-height:1.5;color:var(--ink-2)}
.sub .gd{font-family:var(--mono);color:var(--ink-3);font-size:11.5px;line-height:1.62}
.sub .lb{font-family:var(--mono);font-size:9.5px;font-weight:700;letter-spacing:.11em;
  text-transform:uppercase;color:var(--ink-3);line-height:2.1}
.meter{margin-top:4px;display:flex;gap:8px;align-items:baseline;flex-wrap:wrap}
.blocks{color:var(--ok);letter-spacing:-.5px}
.blocks .e{color:var(--ink-3);opacity:.45}
.pct{color:var(--ink-2)}
.raw{color:var(--ink-3);font-size:11px}
.scan span{color:var(--job);animation:scan 1.9s linear infinite}
.scan span:nth-child(2){animation-delay:.16s} .scan span:nth-child(3){animation-delay:.32s}
.scan span:nth-child(4){animation-delay:.48s} .scan span:nth-child(5){animation-delay:.64s}
@keyframes scan{0%,45%,100%{opacity:.2}20%{opacity:1}}
.empty{padding:2px 9px 8px;color:var(--ink-3);font-size:11.5px}
.win-f{margin-top:auto;padding:6px 9px;border-top:1px solid var(--rule);display:flex;gap:9px;
  align-items:center;font-size:11px;color:var(--ink-2)}
.cells .on{color:var(--ok)} .cells .off{color:var(--ink-3);opacity:.5}
.cursor{color:var(--caret);animation:blink 1.1s steps(2,start) infinite}
@keyframes blink{to{visibility:hidden}}
.id{font-family:var(--mono);font-size:11.5px;font-weight:500;border:0;background:transparent;
  padding:0;cursor:copy;color:var(--ink-3);text-align:left}
.id:hover,.id:focus-visible{color:var(--caret);outline:none}
.id-act{color:var(--act)} .id-job{color:var(--job)}

/* ---- statusline ---- */
.sl{display:flex;align-items:center;background:var(--surface-2);border-top:1px solid var(--rule);
  font-size:11.5px;color:var(--ink-2);height:28px;padding:0 3px;flex:0 0 28px}
.sl-s{padding:0 10px;display:flex;align-items:center;gap:6px;white-space:nowrap}
.sl-s[hidden]{display:none}   /* a class-set display would beat the UA [hidden] rule */
.sl-s+.sl-s{border-left:1px solid var(--rule)}
.sl-s.on{background:var(--ink);color:var(--surface);font-weight:700;height:100%}
.sl-s.on .spin{color:var(--ok)}
.sl-stale{color:var(--act)}
.sl-scope{color:var(--caret)}
.sl-k b{color:var(--mag);font-weight:700}
.sl-c{border:0;background:transparent;font:inherit;cursor:pointer;color:var(--ink-3);padding:0 4px}
.sl-c.on{color:var(--gls);font-weight:700}
.sl-clock{margin-left:auto;padding:0 10px;color:var(--ink-3)}
.spin{display:inline-block;width:9px;color:var(--ok)}

/* ---- empty page ---- */
.tt-none{margin:auto;text-align:center;color:var(--ink-3);padding:48px}

@media (prefers-reduced-motion: reduce){
  *,*::before,*::after{animation-duration:.001ms !important;animation-iteration-count:1 !important;
    transition-duration:.001ms !important}
  .cursor{visibility:visible}
}
```

- [ ] **Step 4: Load it from the dashboard**

In `bin/table-talk-dash.py`, delete the entire `THEME_CSS = """<style>…</style>"""` block and add near the top, after `DATA_DIR`:

```python
CSS_PATH = Path(__file__).resolve().parent / "tt.css"


def load_css():
    """The stylesheet lives beside the script so it can be edited as CSS.
    Read once at startup; a missing file is a broken install, not a runtime path."""
    return CSS_PATH.read_text()
```

Then in `main()`, replace `ui.add_head_html(THEME_CSS)` with:

```python
    ui.add_head_html(f"<style>{load_css()}</style>")
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run --script bin/table-talk-dash.py --selftest`
Expected: `ok`

- [ ] **Step 6: Commit**

```bash
git add bin/tt.css bin/table-talk-dash.py
git commit -m "feat(ui): gruvbox-dark and claude-code-light stylesheet in its own file"
```

---

### Task 8: The window renderer — custom rows replace the Quasar tables

This is the largest change: `ui.table` goes away, and with it Quasar's built-in filtering (Task 6 already replaced it).

**Files:**
- Modify: `bin/table-talk-dash.py`

**Interfaces:**
- Consumes: `tt_model.percent`, `tt_model.summarize`, `tt_model.matches`.
- Produces: `blocks(pct: int, cells: int = 14) -> tuple[str, str]`, `resolved_cells(resolved: int, recorded: int) -> tuple[str, str]`, `render_window_body(container, state, newest_action_id, changed=()) -> None`, and `COPY_JS: str`. The `changed` set stays empty until Task 13 fills it.

- [ ] **Step 1: Write the failing test**

Add to `selftest()` in `bin/table-talk-dash.py`, before `print("ok")`:

```python
    assert blocks(0, 10) == ("", "░░░░░░░░░░")
    assert blocks(100, 10) == ("██████████", "")
    assert blocks(50, 10) == ("█████", "█████".replace("█", "░"))
    filled, empty = blocks(58, 14)
    assert len(filled) + len(empty) == 14, "a bar is always the same width"
    assert blocks(-5, 10) == blocks(0, 10) and blocks(150, 10) == blocks(100, 10)
    assert resolved_cells(0, 5) == ("", "▱▱▱▱▱")
    assert resolved_cells(4, 7) == ("▰▰▰▰", "▱▱▱")
    assert resolved_cells(1, 1) == ("▰", "")
    assert resolved_cells(0, 0) == ("", ""), "a session with nothing recorded shows no cells"
    on, off = resolved_cells(30, 60)
    assert len(on) + len(off) == 20, "cells cap at 20 so a long session cannot wreck the footer"
    assert "data-id" in COPY_JS and "clipboard" in COPY_JS
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run --script bin/table-talk-dash.py --selftest`
Expected: `NameError: name 'blocks' is not defined`

- [ ] **Step 3: Write the implementation**

Replace the `COLS`, `columns`, `COL_LABELS`, `card_data`, `done_rows`, and `build_card` definitions with the following. Also replace the module's `fold`/`fold_cached`/`rows` copies with an import at the top of the file, immediately after the stdlib imports:

```python
import tt_model as M
from tt_model import DATA_DIR, fold, fold_cached
```

Then add:

```python
MAX_CELLS = 20     # a long session must not wreck the footer line
BAR_CELLS = 14


def blocks(pct, cells=BAR_CELLS):
    """A glyph progress bar as (filled, empty). Deliberately snaps cell by cell:
    text cannot tween, and pretending otherwise is where terminal costume
    becomes terminal cosplay."""
    pct = max(0, min(100, pct))
    filled = round(cells * pct / 100)
    return "█" * filled, "░" * (cells - filled)


def resolved_cells(resolved, recorded):
    """The window footer's obligation tally as (done, outstanding) glyphs."""
    if recorded <= 0:
        return "", ""
    if recorded > MAX_CELLS:                      # scale down rather than overflow
        done = round(MAX_CELLS * resolved / recorded)
        return "▰" * done, "▱" * (MAX_CELLS - done)
    return "▰" * resolved, "▱" * (recorded - resolved)


# Ids hand you the command: one delegated listener, no server round-trip.
COPY_JS = """<script>
document.addEventListener('click', e => {
  const b = e.target.closest && e.target.closest('[data-id]');
  if (!b) return;
  const cmd = 'table-talk done ' + b.dataset.id;
  if (navigator.clipboard) navigator.clipboard.writeText(cmd).catch(() => {});
  b.classList.add('copied');
  setTimeout(() => b.classList.remove('copied'), 900);
});
</script>"""

SECTIONS = (("actions --open", "action", "p-act", "id-act", "nothing needs you"),
            ("jobs", "task", "p-job", "id-job", "nothing running"),
            ("glossary", "term", "p-gls", "", ""),
            ("done", "done", "p-ok", "", ""))


def open_rows(state, typ):
    return sorted((e for e in state.values()
                   if e.get("type") == typ and e.get("status") != "done"),
                  key=lambda e: e.get("ts", 0), reverse=True)


def done_rows(state):
    return sorted((e for e in state.values()
                   if e.get("type") in ("action", "task") and e.get("status") == "done"),
                  key=lambda e: e.get("ts", 0), reverse=True)


def term_rows(state):
    return sorted((e for e in state.values() if e.get("type") == "term"),
                  key=lambda e: e.get("term", "").lower())


def _id_button(ev, cls):
    """The id IS the command. A nested label rather than a bare button so we stay
    on public API; the delegated listener finds the button via closest()."""
    from nicegui import ui
    with ui.element("button").props(f'data-id={ev["id"]}').classes(f"id {cls}"):
        ui.label(str(ev["id"]))


def _action_row(ev, blink, changed):
    from nicegui import ui
    with ui.element("div").classes("row changed" if changed else "row"):
        _id_button(ev, "id-act")
        with ui.element("div"):
            with ui.element("div").classes("ttl"):
                ui.label(ev.get("background", ""))
                if blink:   # exactly one cursor on the page: the newest thing waiting on you
                    ui.label("▉").classes("cursor")
            for glyph, label, field in (("├─", "why", "why"), ("└─", "rec", "rec")):
                with ui.element("div").classes("sub"):
                    ui.label(glyph).classes("gd")
                    ui.label(label).classes("lb")
                    ui.label(ev.get(field, ""))


def _task_row(ev, changed):
    from nicegui import ui
    with ui.element("div").classes("row changed-job" if changed else "row"):
        _id_button(ev, "id-job")
        with ui.element("div"):
            ui.label(ev.get("what", "")).classes("ttl")
            text = ev.get("progress", "")
            pct = M.percent(text)
            with ui.element("div").classes("meter"):
                if pct is None:
                    with ui.element("div").classes("scan"):
                        for _ in range(5):
                            ui.label("▓")
                else:
                    filled, empty = blocks(pct)
                    with ui.element("div").classes("blocks"):
                        ui.label(filled)
                        ui.label(empty).classes("e")
                    ui.label(f"{pct}%").classes("pct")
                if text:
                    ui.label(text).classes("raw")


def _term_row(ev):
    from nicegui import ui
    with ui.element("div").classes("row"):
        ui.label(ev.get("term", "")).classes("id id-gls")
        with ui.element("div"):
            ui.label(ev.get("intuitive", "")).classes("ttl")
            with ui.element("div").classes("sub"):
                ui.label("└─").classes("gd")
                ui.label("def").classes("lb")
                ui.label(ev.get("technical", ""))


def _prompt(cls, title, count, toggles=None):
    """A section header as a shell prompt line: '❯ actions --open (3)'.
    When `toggles` is given, clicking the line shows or hides that container —
    this is what keeps the existing collapsed glossary and done sections."""
    from nicegui import ui
    with ui.element("div").classes(f"pr {cls}") as line:
        ui.label("❯").classes("g")
        ui.label(title)
        caret = ui.label(f"({count})" + (" ▸" if toggles is not None else "")).classes("n")
    if toggles is not None:
        def flip(_, box=toggles, lbl=caret, n=count):
            box.set_visibility(not box.visible)
            lbl.set_text(f"({n}) " + ("▾" if box.visible else "▸"))
        line.on("click", flip)


def render_window_body(container, state, newest_action_id, changed=()):
    """Rebuild one window's body.

    A window holds a handful of rows, so rebuilding it is cheaper and far
    simpler than diffing them - and the caller only calls this when that
    window's data actually changed. Filtering is by session, not by row: a
    session survives the filter whole (tt_model.matches), so a window's body
    always shows everything that session holds.
    """
    from nicegui import ui
    container.clear()
    with container:
        acts = open_rows(state, "action")
        jobs = open_rows(state, "task")

        _prompt("p-act", "actions --open", len(acts))
        if not acts:
            ui.label("nothing needs you").classes("empty")
        for ev in acts:
            _action_row(ev, str(ev["id"]) == newest_action_id, str(ev["id"]) in changed)

        _prompt("p-job", "jobs", len(jobs))
        if not jobs:
            ui.label("nothing running").classes("empty")
        for ev in jobs:
            _task_row(ev, str(ev["id"]) in changed)

        # Glossary and done stay collapsed by default, as they are today.
        terms = term_rows(state)
        gls_box = ui.element("div")
        with gls_box:
            for ev in terms:
                _term_row(ev)
        gls_box.set_visibility(False)
        _prompt("p-gls", "glossary", len(terms), toggles=gls_box)
        gls_box.move(container, -1)

        done = done_rows(state)
        done_box = ui.element("div")
        with done_box:
            for ev in done:
                _done_row(ev)
        done_box.set_visibility(False)
        _prompt("p-ok", "done", len(done), toggles=done_box)
        done_box.move(container, -1)
```

`move(container, -1)` puts each collapsed box back *after* its prompt line, since
the box has to exist before the prompt that toggles it.

Add the done-row renderer alongside `_term_row`:

```python
def _done_row(ev):
    """A resolved action or task, dimmed. Keeps its id clickable so a mistaken
    'done' is easy to find again."""
    from nicegui import ui
    with ui.element("div").classes("row"):
        _id_button(ev, "id-ok")
        ui.label(ev.get("background") or ev.get("what", "")).classes("ttl")
```

`newest_action_id` is the id of the single newest open action **across the whole
wall**, computed once per tick — exactly one blinking cursor exists on the page.

Also register the copy script in `main()` beside the other head HTML:

```python
    ui.add_head_html(COPY_JS)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run --script bin/table-talk-dash.py --selftest`
Expected: `ok`

- [ ] **Step 5: Commit**

```bash
git add bin/table-talk-dash.py
git commit -m "feat(ui): custom window renderer with prose why/rec, replacing Quasar tables"
```

---

### Task 9: The wall — build windows once, re-pack by moving them

`Element.move()` relocates a window without rebuilding it, so element identity survives a re-pack: no flicker, no lost scroll.

**Files:**
- Modify: `bin/table-talk-dash.py`

**Interfaces:**
- Consumes: `tt_model.pack`, `tt_model.weight`, `render_window_body`, `resolved_cells`.
- Produces: `layout_key(...) -> tuple`, `default_cols(width: int) -> int`, `build_window(key, project, index, state) -> dict`, and the persistence pair `store(key, default)` / `put(key, value)` that Tasks 10 and 12 both rely on.

- [ ] **Step 1: Write the failing test**

Add to `selftest()` before `print("ok")`:

```python
    assert default_cols(2000) == 3 and default_cols(1400) == 2 and default_cols(800) == 1
    a = layout_key(["x", "y"], 2, {"x"}, set(), None, None, "recent", True)
    b = layout_key(["x", "y"], 2, {"x"}, set(), None, None, "recent", True)
    assert a == b, "identical state must produce an identical key, so no needless re-pack"
    assert a != layout_key(["x", "y"], 3, {"x"}, set(), None, None, "recent", True), "cols"
    assert a != layout_key(["x", "y"], 2, set(), set(), None, None, "recent", True), "marks"
    assert a != layout_key(["y", "x"], 2, {"x"}, set(), None, None, "recent", True), "order"
    assert a != layout_key(["x", "y"], 2, {"x"}, {"y"}, None, None, "recent", True), "folds"
    assert a != layout_key(["x", "y"], 2, {"x"}, set(), "x", None, "recent", True), "zoom"
    assert a != layout_key(["x", "y"], 2, {"x"}, set(), None, "phe", "recent", True), "scope"
    assert a != layout_key(["x", "y"], 2, {"x"}, set(), None, None, "actions", True), "sort"
    assert a != layout_key(["x", "y"], 2, {"x"}, set(), None, None, "recent", False), "drawer"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run --script bin/table-talk-dash.py --selftest`
Expected: `NameError: name 'default_cols' is not defined`

- [ ] **Step 3: Write the implementation**

Add after `render_window_body`:

```python
def default_cols(width):
    """Column count before the user picks one. Three on a wide second monitor."""
    return 3 if width >= 1800 else (2 if width >= 1200 else 1)


def layout_key(visible, cols, marks, folds, zoomed, scope, sort, drawer_open):
    """Everything that changes WHERE a window sits. The wall re-packs when this
    changes and at no other time - never on a poll that only changed text."""
    return (tuple(visible), cols, tuple(sorted(marks)), tuple(sorted(folds)),
            zoomed, scope, sort, drawer_open)
```

Then replace the `build_card`/`tick` pair in `main()` with the wall builder. Inside `main()`, after the head HTML and before the timer. The persistence pair comes first because Tasks 10 and 12 both read it:

```python
    def store(key, default):
        """Persisted UI state. Loopback, one user, so server-wide storage is
        correct here and keeps two tabs in agreement. No storage_secret needed."""
        return app.storage.general.get(f"tt.{key}", default)

    def put(key, value):
        app.storage.general[f"tt.{key}"] = value

    wall = ui.element("div").classes("wall")
    columns = []          # the current column containers
    windows = {}          # key -> {"el", "body", "flags", "foot", "state", "data_sig"}

    def build_window(key, project, index, state):
        summary = M.summarize(state)
        el = ui.element("div").classes("win")
        with el:
            with ui.element("div").classes("win-t"):
                ui.label(project).classes("nm")
                ui.label(f":{index}").classes("ix")
                bell = ui.label("!").classes("bell")
                actv = ui.label("#").classes("actv")
                mark = ui.label("M").classes("fl-m")
                zoom = ui.label("Z").classes("fl-z")
                when = ui.label("").classes("when")
                with ui.element("div").classes("wctl"):
                    for act, glyph, tip in (("mark", "M", "Mark (m) — hold at the front"),
                                            ("zoom", "Z", "Zoom (z) — fill the wall"),
                                            ("fold", "▾", "Fold (f) — collapse to the titlebar")):
                        btn = ui.element("button").classes("wb").props(f'title="{tip}"')
                        with btn:
                            ui.label(glyph)
                        btn.on("click", lambda _, k=key, a=act: on_window_action(k, a))
            body = ui.element("div").classes("win-b")
            with ui.element("div").classes("win-f"):
                cells = ui.element("div").classes("cells")
                tally = ui.label("")
        return {"el": el, "body": body, "bell": bell, "actv": actv, "mark": mark,
                "zoom": zoom, "when": when, "cells": cells, "tally": tally,
                "summary": summary, "sig": None}

    def paint_window(win, state, newest, changed=()):
        """Update everything about one window that depends on its data.
        Called only when that window's data actually changed."""
        summary = M.summarize(state)
        win["bell"].set_visibility(summary["open_actions"] > 0)
        win["actv"].set_visibility(summary["open_tasks"] > 0)
        on, off = resolved_cells(summary["resolved"], summary["recorded"])
        win["cells"].clear()
        with win["cells"]:
            ui.label(on).classes("on")
            ui.label(off).classes("off")
        win["tally"].set_text(
            f'{summary["resolved"]}/{summary["recorded"]} resolved'
            + (" · all clear" if summary["open_actions"] == 0 and summary["open_tasks"] == 0 else ""))
        render_window_body(win["body"], state, newest, changed)
        win["summary"] = summary

    def repack(visible, cols):
        """Move existing windows into freshly sized columns. move() preserves
        element identity, so nothing is rebuilt and nothing flickers."""
        buckets = M.pack(visible, cols, weights, marks)
        wall.clear()
        columns.clear()
        with wall:
            for _ in buckets:
                columns.append(ui.element("div").classes("col"))
        for bucket, container in zip(buckets, columns):
            for key in bucket:
                windows[key]["el"].move(container, -1)
```

Wire `weights`, `marks`, `visible` and the tick loop as described in Task 12; for this task, drive `repack` from the existing `tick()` with `marks = set()` and `cols = default_cols(1400)` so the wall renders.

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run --script bin/table-talk-dash.py --selftest && ./test.sh`
Expected: `ok`, then `all selftests passed`

- [ ] **Step 5: Launch it and look at it**

Run: `TABLE_TALK_DIR=~/.local/share/table-talk uv run --script bin/table-talk-dash.py --port 8732`
Open <http://127.0.0.1:8732>. Expected: a wall of windows in two columns, each with a titlebar, prose why/rec, and a resolved footer. Stop it with Ctrl-C.

- [ ] **Step 6: Commit**

```bash
git add bin/table-talk-dash.py
git commit -m "feat(ui): tiling wall with deterministic re-packing via Element.move"
```

---

### Task 10: The drawer — grouped session tree with meters

**Files:**
- Modify: `bin/table-talk-dash.py`

**Interfaces:**
- Consumes: `tt_model.group_sessions`, `tt_model.sort_groups`, `tt_model.SORTS`, and `store`/`put` from Task 9.
- Produces: `render_drawer(container, groups) -> None`, `abbrev(project: str) -> str`, `GUIDES: dict[str, str]`, and the module-level set `groups_folded`.
- **Stubs:** this task also lands no-op `on_scope(project)`, `on_focus(key)`, and `cycle_sort()` so the drawer is clickable without erroring. Task 12 replaces all three with the real handlers.

- [ ] **Step 1: Write the failing test**

Add to `selftest()` before `print("ok")`:

```python
    assert abbrev("phephree") == "phe"
    assert abbrev("table-talk") == "tab"
    assert abbrev("ab") == "ab", "a short name is not padded"
    assert abbrev("") == "?"
    assert GUIDES == {"open": "▾", "closed": "▸", "mid": "├", "last": "└", "line": "│", "none": " "}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run --script bin/table-talk-dash.py --selftest`
Expected: `NameError: name 'abbrev' is not defined`

- [ ] **Step 3: Write the implementation**

Add near the other module constants:

```python
# tmux choose-tree guides. Kept as literal glyphs so the verticals connect in a
# fixed-width column rather than being faked with borders.
GUIDES = {"open": "▾", "closed": "▸", "mid": "├", "last": "└", "line": "│", "none": " "}


def abbrev(project):
    """Three-letter tag for the collapsed rail."""
    return project[:3] if project else "?"
```

And the drawer renderer, inside `main()` after `build_window`. The three stubs go first so the click handlers resolve; Task 12 replaces them:

```python
    groups_folded = set(store("groups_folded", []))
    seen_projects = set()

    def on_scope(project):   # replaced in Task 12
        pass

    def on_focus(key):       # replaced in Task 12
        pass

    def cycle_sort():        # replaced in Task 12
        pass

    def meter_row(summary):
        """The badge pair and htop meter carried by every drawer row, project and
        session alike. A zero badge goes grey rather than disappearing, so the
        columns stay aligned down the tree."""
        with ui.element("div").classes("dw-l2"):
            for count, glyph, cls in ((summary["open_actions"], "●", "b-act"),
                                      (summary["open_tasks"], "▶", "b-job")):
                ui.label(f"{glyph}{count}").classes(cls if count else "b-off")
            pct = summary["pct"]
            with ui.element("div").classes("mtr"):
                ui.label("[")
                trk = ui.element("div").classes("trk")
                with trk:
                    fill = ui.element("i")
                    fill.style(f"width:{pct}%")
                    if pct >= 100:
                        fill.classes("full")
                ui.label("]")
            ui.label(f"{pct}%").classes("pc")

    def render_drawer(container, groups):
        container.clear()
        with container:
            with ui.element("div").classes("dw-top"):
                ui.label("sessions").classes("ttl")
                n = sum(len(g["sessions"]) for g in groups)
                ui.label(f"{n} · {len(groups)} projects")
            sort_row = ui.element("div").classes("dw-sort")
            with sort_row:
                ui.label("sort:")
                for mode in M.SORTS:
                    lbl = ui.label(mode)
                    if mode == store("sort", "recent"):
                        lbl.classes("b-on")
            sort_row.on("click", lambda _: cycle_sort())
            for g in groups:
                single = len(g["sessions"]) == 1
                folded = g["project"] in groups_folded and not single
                row = ui.element("button").classes("dw-row dw-proj")
                row.props(f'data-project={g["project"]}')
                with row:
                    ui.label(GUIDES["none"] if single else
                             (GUIDES["closed"] if folded else GUIDES["open"])).classes("dw-g")
                    with ui.element("div").classes("dw-l1"):
                        ui.label(g["project"]).classes("dw-nm")
                        ui.label(g["sessions"][0]["date"] if single
                                 else f'{len(g["sessions"])} sessions').classes("dw-meta")
                    ui.label(GUIDES["line"] if not (single or folded) else GUIDES["none"]).classes("dw-g")
                    meter_row(g)
                row.on("click", lambda _, p=g["project"]: on_scope(p))
                if single or folded:
                    continue
                for i, sess in enumerate(g["sessions"]):
                    last = i == len(g["sessions"]) - 1
                    srow = ui.element("button").classes("dw-row dw-sess")
                    with srow:
                        ui.label(GUIDES["last"] if last else GUIDES["mid"]).classes("dw-g")
                        with ui.element("div").classes("dw-l1"):
                            ui.label(sess["date"]).classes("dw-nm")
                            ui.label(ago(sess["summary"]["latest"])).classes("dw-meta")
                        ui.label(GUIDES["none"] if last else GUIDES["line"]).classes("dw-g")
                        meter_row(sess["summary"])
                    srow.on("click", lambda _, k=sess["key"]: on_focus(k))
```

And the fold-on-load rule from the spec, called once per tick **before** `render_drawer`:

```python
    def apply_fold_rules(groups):
        """A project with nothing open folds the first time we see it, so the
        drawer opens showing only what is live. A poll that raises the open-action
        count forces the group back open: a fold must never hide something that
        just started needing you."""
        changed = False
        for g in groups:
            project = g["project"]
            if project not in seen_projects:
                seen_projects.add(project)
                if g["open_actions"] == 0:
                    groups_folded.add(project)
                    changed = True
            elif g["open_actions"] > 0 and project in groups_folded:
                groups_folded.discard(project)
                changed = True
        if changed:
            put("groups_folded", sorted(groups_folded))
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run --script bin/table-talk-dash.py --selftest`
Expected: `ok`

- [ ] **Step 5: Launch it and look at it**

Run: `uv run --script bin/table-talk-dash.py --port 8732`
Expected: the drawer shows `phephree` with two children under connected `├`/`└` guides, `gcp-aws-xfer` as one flat row with no triangle, and every row carries badges and a meter. Stop with Ctrl-C.

- [ ] **Step 6: Commit**

```bash
git add bin/table-talk-dash.py
git commit -m "feat(ui): drawer session tree grouped by project with htop meters"
```

---

### Task 11: The statusline — watch(1) cadence, tallies, scope, columns

**Files:**
- Modify: `bin/table-talk-dash.py`

**Interfaces:**
- Consumes: `tt_model.summarize`.
- Produces: `SPINNER: tuple[str, ...]`, `tally_text(open_actions: int, open_tasks: int) -> str`.

- [ ] **Step 1: Write the failing test**

Add to `selftest()` before `print("ok")`:

```python
    assert len(SPINNER) == 10 and SPINNER[0] == "⠋"
    assert tally_text(6, 5) == "●6 open  ▶5 running"
    assert tally_text(0, 0) == "all clear"
    assert tally_text(1, 0) == "●1 open"
    assert tally_text(0, 2) == "▶2 running"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run --script bin/table-talk-dash.py --selftest`
Expected: `NameError: name 'SPINNER' is not defined`

- [ ] **Step 3: Write the implementation**

```python
# Advances one frame per SUCCESSFUL poll and freezes when a poll fails: liveness
# you can trust, rather than an abstract pulse dot you have to interpret.
SPINNER = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")


def tally_text(open_actions, open_tasks):
    parts = []
    if open_actions:
        parts.append(f"●{open_actions} open")
    if open_tasks:
        parts.append(f"▶{open_tasks} running")
    return "  ".join(parts) if parts else "all clear"
```

Build the statusline in `main()` after the wall, as the last child of `.tt-app`:

```python
    with ui.element("div").classes("sl"):
        with ui.element("div").classes("sl-s on"):
            spin = ui.label(SPINNER[0]).classes("spin")
            ui.label("table-talk")
        cadence = ui.element("div").classes("sl-s")
        with cadence:
            ui.label("Every 2.0s · last")
            last_stamp = ui.label("--:--:--")
        tally = ui.label("").classes("sl-s")
        scope_seg = ui.element("div").classes("sl-s sl-scope")
        with scope_seg:
            scope_label = ui.label("")
            clear_btn = ui.element("button").classes("sl-c")
            with clear_btn:
                ui.label("✕")
            clear_btn.on("click", lambda _: on_scope(None))
        scope_seg.set_visibility(False)
        col_buttons = {}
        with ui.element("div").classes("sl-s"):
            ui.label("cols")
            for n in (1, 2, 3):
                b = ui.element("button").classes("sl-c")
                with b:
                    ui.label(str(n))
                b.on("click", lambda _, n=n: on_cols(n))
                col_buttons[n] = b
        with ui.element("div").classes("sl-s sl-k"):
            for key, what in (("\\", "drawer"), ("m", "mark"), ("z", "zoom"),
                              ("f", "fold"), ("?", "keys")):
                ui.html(f"<b>{key}</b>")
                ui.label(what)
        clock = ui.label("").classes("sl-clock")
```

Then, on each tick:

- Advance `spin` one frame and set `last_stamp`/`clock` from `time.strftime("%H:%M:%S")` **only when the poll succeeded**. On an exception, add `sl-stale` to `cadence` and leave the spinner frozen — a stopped spinner and a stale timestamp are the whole point of the `watch(1)` idiom.
- `tally.set_text(tally_text(total_open_actions, total_open_tasks))`.
- Show `scope_seg` when `scope` is set, and `scope_label.set_text(f"showing {scope} only")`.
- Mark the active column button: `col_buttons[n].classes(replace="sl-c on" if n == cols else "sl-c")`.

Wrap the whole tick body in `try/except Exception` so one bad poll degrades the statusline instead of killing the timer.

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run --script bin/table-talk-dash.py --selftest`
Expected: `ok`

- [ ] **Step 5: Commit**

```bash
git add bin/table-talk-dash.py
git commit -m "feat(ui): tmux-style statusline with watch(1) cadence and column control"
```

---

### Task 12: Marks, folds, zoom, scope, columns, and the keyboard layer

Ties the controls together and persists them. `ui.keyboard` already ignores keypresses while an input has focus — verified, not assumed.

**Files:**
- Modify: `bin/table-talk-dash.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `store(key, default)`, `put(key, value)`, `on_window_action(key, action)`, `on_scope(project)`, `on_cols(n)`, `on_focus(key)`, `cycle_sort()`, `KEYMAP: dict[str, str]`.

- [ ] **Step 1: Write the failing test**

Add to `selftest()` before `print("ok")`:

```python
    assert KEYMAP == {"\\": "drawer", "m": "mark", "z": "zoom", "f": "fold",
                      "s": "sort", "/": "filter", "!": "needs-me", "?": "keys",
                      "Escape": "unzoom"}
    assert next_sort("recent") == "actions"
    assert next_sort("actions") == "project"
    assert next_sort("project") == "recent"
    assert next_sort("nonsense") == "recent", "an unknown sort cycles back to the default"
    assert toggle({"a"}, "a") == set() and toggle(set(), "a") == {"a"}
    assert toggle({"a"}, "b") == {"a", "b"}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run --script bin/table-talk-dash.py --selftest`
Expected: `NameError: name 'KEYMAP' is not defined`

- [ ] **Step 3: Write the implementation**

```python
KEYMAP = {"\\": "drawer", "m": "mark", "z": "zoom", "f": "fold",
          "s": "sort", "/": "filter", "!": "needs-me", "?": "keys",
          "Escape": "unzoom"}


def next_sort(mode):
    """Cycle recent -> actions -> project -> recent."""
    order = M.SORTS
    return order[(order.index(mode) + 1) % len(order)] if mode in order else order[0]


def toggle(s, item):
    """Set membership toggle, returning a new set so callers can compare."""
    out = set(s)
    out.discard(item) if item in out else out.add(item)
    return out
```

Inside `main()`, replace the four no-op stubs from Task 10 with the real handlers (`store`/`put` already exist from Task 9):

```python
    marks = set(store("marks", []))
    folds = set(store("folds", []))
    groups_folded = set(store("groups_folded", []))
    zoomed = store("zoomed", None)
    scope = store("scope", None)

    def _replace(target, new, store_key):
        """Mutate the live set in place (handlers close over it) and persist.
        Compute `new` BEFORE clearing: clearing first would make toggle() operate
        on an already-empty set and every mark would read as 'not marked'."""
        target.clear()
        target.update(new)
        put(store_key, sorted(target))

    def on_window_action(key, action):
        nonlocal zoomed
        if action == "mark":
            _replace(marks, toggle(marks, key), "marks")
        elif action == "fold":
            _replace(folds, toggle(folds, key), "folds")
        elif action == "zoom":
            zoomed = None if zoomed == key else key
            put("zoomed", zoomed)
        tick()

    def on_scope(project):
        nonlocal scope, zoomed
        scope = None if scope == project else project
        zoomed = None
        put("scope", scope); put("zoomed", None)
        tick()

    def on_cols(n):
        put("cols", n)
        tick()

    def on_focus(key):
        ui.run_javascript(
            f'document.querySelector(\'[data-window="{key}"]\')'
            f'?.scrollIntoView({{behavior:"smooth",block:"start"}})')

    def cycle_sort():
        put("sort", next_sort(store("sort", "recent")))
        tick()

    def on_key(e):
        if not e.action.keydown or e.action.repeat:
            return
        what = KEYMAP.get(e.key.name)
        if what == "drawer":
            put("drawer_open", not store("drawer_open", True)); tick()
        elif what == "sort":
            cycle_sort()
        elif what == "unzoom" and zoomed:
            on_window_action(zoomed, "zoom")
        elif what == "filter":
            ui.run_javascript('document.querySelector(".dw-find input")?.focus()')

    # ignore defaults to ['input','select','button','textarea'], so keys do not
    # fire while the filter box has focus. Verified in both directions.
    ui.keyboard(on_key=on_key)
```

Give each window `data-window={key}` in `build_window` so `on_focus` can find it, and apply `marked`/`folded` classes plus the `M`/`Z` flag visibility from `marks`/`folds`/`zoomed` on each repack.

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run --script bin/table-talk-dash.py --selftest && ./test.sh`
Expected: `ok`, then `all selftests passed`

- [ ] **Step 5: Drive it in a browser**

Run: `uv run --script bin/table-talk-dash.py --port 8732`, then check each by hand:
- Clicking `M` on a window pins it to the top-left and shows the `M` flag.
- Clicking `Z` fills the wall with that window; `Esc` restores.
- Clicking `▾` collapses a window to its titlebar and footer, and the wall re-packs.
- `cols 1│2│3` changes the column count.
- Clicking a project row scopes the wall and shows `showing <project> only` with an ✕.
- Typing in the filter box does **not** trigger `m`/`z`/`f`.
- Reloading the page preserves marks, folds, columns, sort, and scope.

- [ ] **Step 6: Commit**

```bash
git add bin/table-talk-dash.py
git commit -m "feat(ui): marks, folds, zoom, scope and keyboard layer with persistence"
```

---

### Task 13: Change gutters — mark what moved while you were away

The last of the seven shared foundations. A 900 ms flash on a second monitor is a flash nobody sees.

**Files:**
- Modify: `bin/table-talk-dash.py`

**Interfaces:**
- Consumes: `render_window_body`.
- Produces: `changed_ids(state: dict, since: int) -> set[str]`, `VISIBILITY_JS: str`.

- [ ] **Step 1: Write the failing test**

Add to `selftest()` before `print("ok")`:

```python
    st = {"a": {"id": "a", "type": "action", "ts": 100},
          "b": {"id": "b", "type": "task", "ts": 200},
          "c": {"id": "c", "type": "term", "ts": 300}}
    assert changed_ids(st, 150) == {"b"}, "terms never carry a change gutter"
    assert changed_ids(st, 0) == {"a", "b"}
    assert changed_ids(st, 999) == set()
    assert changed_ids({}, 0) == set()
    assert "visibilitychange" in VISIBILITY_JS
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run --script bin/table-talk-dash.py --selftest`
Expected: `NameError: name 'changed_ids' is not defined`

- [ ] **Step 3: Write the implementation**

```python
def changed_ids(state, since):
    """Ids of actions and tasks that moved after `since`. Terms are reference
    material and never carry a gutter."""
    return {str(e["id"]) for e in state.values()
            if e.get("type") in ("action", "task") and e.get("ts", 0) > since}


# The tab going visible is what clears the gutters - not a timer. A change that
# landed while you were in your editor is still marked when you come back.
VISIBILITY_JS = """<script>
document.addEventListener('visibilitychange', () => {
  if (!document.hidden) window.dispatchEvent(new CustomEvent('tt-seen'));
});
</script>"""
```

In `render_window_body`, add `changed` / `changed-job` to a row's classes when its id is in the `changed` set passed down from `tick()`. Track a `seen_ts` in `main()`, updated to `time.time()` when the `tt-seen` event arrives, and pass `changed_ids(state, seen_ts)` into each window's paint.

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run --script bin/table-talk-dash.py --selftest && ./test.sh`
Expected: `ok`, then `all selftests passed`

- [ ] **Step 5: Verify by hand**

Run the dashboard, switch to another tab, and from a terminal run
`table-talk task "gutter check"`. Switch back: the new row carries a coloured left gutter,
which clears on the next poll after the tab became visible.

- [ ] **Step 6: Commit**

```bash
git add bin/table-talk-dash.py
git commit -m "feat(ui): persistent change gutters cleared by tab visibility"
```

---

### Task 14: README, screenshots, and the demo tape

**Files:**
- Modify: `README.md`
- Create: `docs/assets/hero.png` (replace), `docs/assets/demo.tape`

- [ ] **Step 1: Capture the hero screenshot**

Run the dashboard against the real data dir at 1440×900, in dark mode, with the drawer open and at least two projects visible. Save as `docs/assets/hero.png`, cropped to 1280×640 so it doubles as the GitHub social preview (`59d5`).

- [ ] **Step 2: Write the vhs tape**

Create `docs/assets/demo.tape`:

```
Output docs/assets/demo.gif
Set FontSize 16
Set Width 1200
Set Height 700
Set Theme "Gruvbox Dark"

Type "table-talk task 'train the reranker'" Enter
Sleep 1s
Type "table-talk progress 3f21 'epoch 3/10'" Enter
Sleep 1s
Type "table-talk action 'pick a checkpoint' --why 'affects eval' --rec 'take epoch 8'" Enter
Sleep 2s
Type "table-talk done 3f21" Enter
Sleep 2s
```

Run: `vhs docs/assets/demo.tape`

- [ ] **Step 3: Update the README**

Replace the screenshot reference with the new hero, and add a short "Dashboard" section describing the wall, the drawer, and the key bindings from `KEYMAP`. Keep the existing runnable Use block unchanged.

- [ ] **Step 4: Run the whole suite one last time**

Run: `./test.sh`
Expected: `all selftests passed`

- [ ] **Step 5: Commit**

```bash
git add README.md docs/assets/
git commit -m "docs: new dashboard hero, demo tape, and key bindings"
```

---

## Self-review notes

- **Spec coverage.** Palettes → Task 7. Typography → Task 7. Drawer, grouping, group-of-one, fold-on-load, sort, scope, collapsed rail → Tasks 4, 10, 12. Wall, tmux flags, `project:index`, sections, prose why/rec, empty states, footer → Tasks 8, 9. Statusline and `watch(1)` cadence → Task 11. Packer (all five rules) → Tasks 5, 9. Mark/zoom/fold/columns → Task 12. Percent → Task 2. Meter and summed roll-up → Task 3. Filtering → Task 6. Shared foundations: ids-copy → Task 8, box-drawing → Task 8, one cursor → Task 8, change bars → Task 13, watch(1) → Task 11, reverse-video → Task 7 CSS, tape → Task 14.
- **Deliberately deferred:** drag-and-drop (spec: out of scope), and the `?` key overlay, which is listed in `KEYMAP` but renders nothing until someone wants it.
- **Naming is consistent across tasks:** `summarize`/`roll_up`/`group_sessions`/`sort_groups`/`weight`/`pack`/`matches`/`percent` in `tt_model`; `blocks`/`resolved_cells`/`render_window_body`/`layout_key`/`default_cols`/`abbrev`/`tally_text`/`changed_ids` in the dashboard. Every name used in a later task is defined in an earlier one.
