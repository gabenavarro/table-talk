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
    print("ok")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.parse_args()
    selftest()
