#!/usr/bin/env python3
"""table-talk data model: everything the dashboard computes, with no UI and no
dependencies outside the standard library, so it can be selftested with plain
python3 and reasoned about without starting a server."""
import argparse
import html
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


def parts(text, q):
    """Split text into [(chunk, is_match)], case-insensitive, preserving the
    original casing and spacing. Matches are non-overlapping, left to right."""
    if not q:
        return [(text, False)]
    low, ql, out, i = text.lower(), q.lower(), [], 0
    while (j := low.find(ql, i)) != -1:
        if j > i:
            out.append((text[i:j], False))
        out.append((text[j:j + len(ql)], True))
        i = j + len(ql)
    if i < len(text):
        out.append((text[i:], False))
    return out or [("", False)]


def marked(text, q):
    """Escaped HTML with matched substrings wrapped in a highlight span.

    Escaping happens AFTER the split, never before. Escaping first is broken in
    both directions: a query of 'a&b' would stop matching 'a&amp;b', and a query
    of '<span class=' would match the markup just inserted. Splitting on the raw
    text means source '<', '&' and quotes can never become markup, stay
    searchable, and our own markup is not in the search space.

    This is the one place ui.html is permitted; the property test pins it.
    """
    return "".join(f'<span class="tt-hit">{html.escape(c)}</span>' if hit else html.escape(c)
                   for c, hit in parts(text, q))


_PATHISH = re.compile(r"[^\s'\"<>()\[\]]*/[^\s'\"<>()\[\]]*")


def path_spans(text, roots):
    """Non-overlapping (start, end, resolved) for path-shaped substrings that
    resolve to an existing FILE inside one of `roots`.

    Existence plus confinement is the whole filter: prose is full of slashes, and
    a token that does not resolve to a real file under a root we already trust is
    not rendered as a link at all. Resolution happens BEFORE the containment
    check so a symlink cannot escape, and traversal (`..`) collapses first.

    Paths containing spaces are not detected. That is an accepted limit - the
    alternative is guessing where a filename ends inside a sentence. A trailing
    ':LINE' as in 'path/to/file.py:42' is swallowed into the token and so fails
    to resolve too - also accepted, since stripping it would break absolute
    Windows-style paths.
    """
    if not text:
        return []
    resolved_roots = []
    for r in roots or ():
        try:
            resolved_roots.append(Path(r).resolve(strict=True))
        except (OSError, RuntimeError):
            pass
    if not resolved_roots:
        return []
    out = []
    for m in _PATHISH.finditer(str(text)):
        raw = m.group(0).rstrip(".,;:!?")
        if not raw:
            continue
        try:
            p = Path(raw).resolve(strict=True)
        except (OSError, RuntimeError, ValueError):
            continue
        if not p.is_file():
            continue
        if not any(p == root or root in p.parents for root in resolved_roots):
            continue
        out.append((m.start(), m.start() + len(raw), str(p)))
    return out


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

    import html as _html
    import random as _random
    assert parts("SEssion", "se") == [("SE", True), ("ssion", False)], "original casing survives"
    assert parts("abc", "") == [("abc", False)] and parts("", "x") == [("", False)]
    assert parts("aaa", "aa") == [("aa", True), ("a", False)], "matches are non-overlapping"
    assert marked("a & b", "&amp;").count("tt-hit") == 0, "an escaped entity must not self-match"
    assert marked("x", "</span>").count("tt-hit") == 0, "our own markup is not in the search space"
    assert marked('<script>alert(1)</script>', "script").count("tt-hit") == 2
    assert "<script>" not in marked('<script>alert(1)</script>', "script"), "hostile text stays text"

    # Property test: stripping our spans must return exactly html.escape(original),
    # and the chunks must reassemble the source losslessly. ~48k pairs.
    _strip = re.compile(r'</?span[^>]*>')
    _rng = _random.Random(20260826)
    _hostile = ['<script>alert("xss")</script>', 'a & b', '5 < 6 && 7', '"quoted"', "it's",
                '</span><img src=x onerror=alert(1)>', '&amp;', '<span class="tt-hit">', '']
    _texts = _hostile + ["".join(_rng.choice('<>&"\'/ abSE&;') for _ in range(_rng.randint(0, 40)))
                         for _ in range(2000)]
    for _t in _texts:
        for _q in ("", "a", "se", "SE", "&", "<", '"', "'", "</span>", "&amp;", "<script>", "ab"):
            assert _strip.sub("", marked(_t, _q)) == _html.escape(_t), \
                f"round-trip must equal html.escape for {_t!r} / {_q!r}"
            assert "".join(c for c, _ in parts(_t, _q)) == _t, "chunks must reassemble losslessly"

    import tempfile as _tf
    with _tf.TemporaryDirectory() as _td:
        root = Path(_td) / "proj"
        (root / "docs").mkdir(parents=True)
        real = root / "docs" / "notes.md"
        real.write_text("x")
        (root / "CLAUDE.md").write_text("x")
        outside = Path(_td) / "secret.txt"
        outside.write_text("x")
        roots = [root]

        def spans(t):
            return [s[2] for s in path_spans(t, roots)]

        assert spans(f"see {real} for detail") == [str(real)], "an existing file is found"
        assert spans("see docs/notes.md") == [], "a relative path with no root anchor is skipped"
        assert spans(f"{root}/docs/missing.md") == [], "a path that does not exist is not a link"
        assert spans(f"{outside}") == [], "a real file OUTSIDE the roots is never linked"
        assert spans(f"{root}/../{outside.name}") == [], "traversal out of the root is refused"
        assert spans("/etc/passwd") == [], "an absolute path outside the roots is refused"
        assert spans(f"a {real} b {root/'CLAUDE.md'} c") == [str(real), str(root / "CLAUDE.md")], \
            "several paths in one string, in order"
        assert spans(f"({real})") == [str(real)], "surrounding punctuation is trimmed"
        assert spans(f"{real}.") == [str(real)], "a trailing sentence period is not part of the path"
        assert spans("no paths here at all") == []
        assert spans("") == [] and spans(None) == []
        assert spans(str(root / "docs")) == [], "a directory must not be returned as linkable"

        link = root / "link.md"
        link.symlink_to(outside)
        assert spans(str(link)) == [], "a symlink pointing outside the roots is refused"

        weird = root / "has space.md"
        weird.write_text("x")
        assert spans(f"{weird}") == [], "a path containing a space is not detected (accepted limit)"

        sp = path_spans(f"see {real} ok", roots)
        assert len(sp) == 1 and f"see {real} ok"[sp[0][0]:sp[0][1]] == str(real), \
            "the span must index the ORIGINAL string exactly"
    print("ok")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.parse_args()
    selftest()
