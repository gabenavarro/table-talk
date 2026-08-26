#!/usr/bin/env python3
"""table-talk data model: everything the dashboard computes, with no UI and no
dependencies outside the standard library, so it can be selftested with plain
python3 and reasoned about without starting a server."""
import argparse
import json
import os
import re
from pathlib import Path

DATA_DIR = Path(os.environ.get("TABLE_TALK_DIR") or str(Path.home() / ".local/share/table-talk"))

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
        key = lambda g: (g["project"].lower(), -g["latest"])          # noqa: E731
    elif mode == "actions":
        key = lambda g: (-g["open_actions"], -g["latest"])            # noqa: E731
    else:
        key = lambda g: (-g["latest"], g["project"].lower())          # noqa: E731
    return sorted(groups, key=key)


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

    Masonry is deliberately NOT used - it reassigns columns on every content
    change, which slides a window out from under the reader every 2 s poll.
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
    print("ok")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.parse_args()
    selftest()
