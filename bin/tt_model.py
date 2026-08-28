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
    except OSError:
        # OSError, not FileNotFoundError: the data dir is a directory anyone can
        # drop things in, and a log that is unreadable (chmod, a stale mount) or
        # a DIRECTORY whose name ends .jsonl both raise here. Either one used to
        # take down every session at once, because poll() folds them all before
        # it draws anything and the next poll failed identically - a permanent
        # freeze presented as a stale spinner.
        return state
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            ev = json.loads(line.decode("utf-8"))
            eid = str(ev["id"])
            # Also on the way IN, so a log written before the CLI scrubbed at
            # the write boundary still renders: a lone surrogate reaching the
            # page kills orjson, which NiceGUI requires, and with it the whole
            # render - not one cell. A `\\udcff` escape is plain ASCII in the
            # file, so the decode above cannot catch it.
            clean = {k: (v.encode("utf-8", "backslashreplace").decode("utf-8")
                         if isinstance(v, str) else v) for k, v in ev.items()}
            # A ts that is not a number is corrupt, and every consumer compares
            # it (summarize's max, the sort keys, ago, the change watermark), so
            # one bad line raised TypeError out of poll() before a single window
            # was drawn. Normalised HERE because this is the one boundary they
            # all route through - and only when the key is PRESENT, or a partial
            # update with no ts of its own would erase the real one.
            if "ts" in clean and (isinstance(clean["ts"], bool)
                                  or not isinstance(clean["ts"], (int, float))):
                clean["ts"] = 0
            state[eid] = {**state.get(eid, {}), **clean}
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, KeyError):
            pass
    return state


_fold_cache = {}  # path -> ((st_mtime, st_size), folded_state)


def fold_cached(path):
    """fold(), but re-parse a file only when its mtime/size changed. Steady state
    collapses the 2 s tick to O(files stat) regardless of history size."""
    try:
        st = path.stat()
    except OSError:      # same reasoning as fold(): a bad entry is not a crash
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


def progress_pct(ev):
    """The number the bar draws: the explicit --pct if one was recorded, else
    whatever percent() can scrape out of the prose.

    Explicit wins because scraping is a guess, and a guess that reads a RESULT
    as completion is worse than no bar at all: '92% of 5039 genes above zero'
    drew a 92% bar on a job that was nowhere near finished.

    A bool or a string is not a reading - a hand-edited log must not be able to
    draw a bar - and True is deliberately rejected before the numeric check,
    because in Python it IS an int.
    """
    p = ev.get("pct")
    if isinstance(p, bool) or not isinstance(p, (int, float)):
        return percent(ev.get("progress", ""))
    return max(0, min(100, round(p)))


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


def merge_projects(sessions):
    """[(stem, state)] -> {project: one merged state}, every event tagged with
    the session that recorded it. Ids are minted unique across every file, so
    the merge cannot collide. The tag is the event's own session code, or its
    file's date for anything recorded before stamping existed."""
    out = {}
    for stem, state in sessions:
        date, project = parse_stem(stem)
        fallback = date[5:].replace("-", "") or project[:4]
        bucket = out.setdefault(project, {})
        for i, ev in state.items():
            bucket[i] = {**ev, "_from": str(ev.get("sid") or fallback)}
    return out


def sort_groups(groups, mode):
    """Order the drawer. Every key falls back to recency so the order is total
    and stable.

    Children read newest-first in every mode but `actions`, where they answer
    the same question the mode asks - what needs me most - because a quiet
    newer session otherwise sits above the one holding four open actions purely
    for being newer. The index is NOT touched: it is the file's identity (`:0`
    is the newest file), so a session keeps its name wherever it is drawn.

    Returns new group dicts rather than reordering the caller's lists: the
    tally is summed from the same groups, and a sort that mutated them would
    be a second, invisible caller.
    """
    if mode == "project":
        key = lambda g: (g["project"].lower(), -g["latest"])          # noqa: E731
    elif mode == "actions":
        key = lambda g: (-g["open_actions"], -g["latest"])            # noqa: E731
    else:
        key = lambda g: (-g["latest"], g["project"].lower())          # noqa: E731
    out = sorted(groups, key=key)
    if mode == "actions":
        out = [{**g, "sessions": sorted(
            g["sessions"],
            key=lambda s: (-s["summary"]["open_actions"], -s["summary"]["latest"]))}
            for g in out]
    return out


def weight(state):
    """A window's estimated height in arbitrary units, derived from CONTENT.

    Never from measured pixels: those change on every re-render, and a weight
    that changes makes the packer move windows while they are being read. Done
    items cost nothing except their sketch, which is still drawn; terms cost
    nothing because they live in collapsed sections. Progress text is
    deliberately not measured - it changes on nearly every poll, and packing
    must not.
    """
    units = 1
    for ev in state.values():
        if ev.get("type") in ("action", "task") and ev.get("diagram"):
            units += 1 + str(ev["diagram"]).count("\n") // 2
        if ev.get("status") == "done":
            continue
        typ = ev.get("type")
        if typ == "action":
            chars = (len(ev.get("background", "")) + len(ev.get("why", ""))
                     + len(ev.get("rec", "")) + len(ev.get("intuitive", "")))
            units += 3 + chars // 110
        elif typ == "task":
            units += 2 + (1 if ev.get("intuitive") else 0)
        elif typ == "diagram":
            units += 6      # rendered SVG: roughly an action's height, plus room
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


_ART_ASCII = set("-|+/\\<>^_=~*.:'`,;()[]{}#")


def _is_structure(ch):
    """A stroke character, as opposed to label text."""
    if ch in _ART_ASCII:
        return True
    o = ord(ch)
    return (0x2190 <= o <= 0x21FF     # arrows
            or 0x2500 <= o <= 0x259F  # box drawing + block elements
            or 0x25A0 <= o <= 0x25FF)  # geometric shapes


def art_spans(text):
    """Split ASCII art into [(chunk, is_structure)] runs.

    Structure is box-drawing, arrows, geometric shapes and ASCII stroke
    characters; everything else (labels) is content. Whitespace is neutral and
    extends whatever run is open, so the chunks reassemble the art losslessly
    - the renderer colours structure with the faint ink and labels with the
    full ink, and a split that dropped a byte would redraw different art.
    """
    if not text:
        return []
    out, cur, cls = [], "", None
    for ch in str(text):
        k = cls if ch in " \t\n" else _is_structure(ch)
        if k != cls and cls is not None and k is not None:
            out.append((cur, cls))
            cur, cls = "", k
        cur += ch
        if cls is None:
            cls = k
    out.append((cur, bool(cls)))
    return out


_TEXT_FIELDS = ("id", "background", "why", "rec", "what", "progress",
                "term", "intuitive", "technical", "title", "mermaid", "diagram")


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


# The lookbehind is load-bearing: unanchored, this matches the TAIL of a token
# ('xhttps://y/z' linked 'https://y/z'), and what it matches becomes a button
# handed to a process launcher.
_URL = re.compile(r"(?<![\w.-])https?://[^\s'\"<>()\[\]\x00-\x1f\x7f]+")


def url_spans(text):
    """Non-overlapping (start, end, url) for http(s) URLs in `text`.

    Deliberately only those two schemes: the span becomes a button that hands
    the string to a process launcher, and file:// or javascript: reaching that
    launcher is the whole reason to name the schemes rather than match any
    'x://'. Trailing sentence punctuation is trimmed the way path_spans trims
    it, so 'see https://x/y.' links y and not 'y.'.

    A bare '#117' is NOT matched: which repository a session means is not
    knowable from its log, so the protocol records whole URLs instead of
    guessing one.
    """
    out = []
    for m in _URL.finditer(str(text or "")):
        raw = m.group(0).rstrip(".,;:!?")
        if "://" in raw and not raw.endswith("://"):
            out.append((m.start(), m.start() + len(raw), raw))
    return out


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

        # A lone surrogate (argv decodes undecodable bytes with surrogateescape)
        # is plain ASCII once json.dumps escapes it, so the decode above cannot
        # catch it - and orjson, which NiceGUI requires, refuses the payload and
        # stops the whole page rendering rather than spoiling one cell.
        with open(p, "a") as fh:
            fh.write(json.dumps({"id": "9f9f", "type": "task", "what": "bad \udcff"}) + "\n")
        bad = fold(p)["9f9f"]["what"]
        assert bad == "bad \\udcff", "a lone surrogate is escaped on the way in"

        # One bad entry in the data dir must cost itself, never the whole wall:
        # poll() folds every file before drawing anything, and the next poll
        # failed identically, so a freeze was permanent and looked like a stale
        # spinner.
        unread = Path(td) / "2026-08-27-locked.jsonl"
        unread.write_text('{"id":"u1","type":"task","ts":1}\n')
        unread.chmod(0o000)
        try:
            assert fold(unread) == {} and fold_cached(unread) == {}, \
                "an unreadable log folds to empty; it must not raise"
        finally:
            unread.chmod(0o644)
        adir = Path(td) / "2026-08-27-oops.jsonl"
        adir.mkdir()
        assert fold(adir) == {} and fold_cached(adir) == {}, \
            "a DIRECTORY named *.jsonl matches the glob and must not raise either"

        tsbad = Path(td) / "2026-08-25-ts.jsonl"
        tsbad.write_text(json.dumps({"id": "t1", "type": "task", "ts": "nope"}) + "\n")
        assert fold(tsbad)["t1"]["ts"] == 0, \
            "a ts that is not a number is corrupt: every consumer COMPARES it, " \
            "so one bad line raised TypeError before a single window was drawn"
        summarize(fold(tsbad))    # raises TypeError if the normalisation slipped
        keep = Path(td) / "2026-08-24-keep.jsonl"
        keep.write_text(json.dumps({"id": "k1", "type": "task", "ts": 99}) + "\n"
                        + json.dumps({"id": "k1", "status": "done"}) + "\n")
        assert fold(keep)["k1"]["ts"] == 99, \
            "a partial update carrying no ts must not erase the real one"
        bad.encode("utf-8")   # raises UnicodeEncodeError if one ever survives
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

    # progress_pct: an explicit reading beats one scraped out of prose. This
    # exact sentence is real gpn-micro progress; scraping drew a 92% bar from
    # a RESULT on a job that had barely started.
    _res = "median per-gene R2 0.210, r 0.453, 92% of 5039 genes above zero"
    assert progress_pct({"progress": _res}) == 92, "the scrape still reads it"
    assert progress_pct({"pct": 40, "progress": _res}) == 40, \
        "and an explicit --pct overrules it, which is the whole point"
    assert progress_pct({"pct": 0}) == 0, "0 is a reading, not a missing one"
    assert progress_pct({"progress": "epoch 3/10"}) == 30, "no --pct still scrapes"
    assert progress_pct({}) is None and progress_pct({"progress": ""}) is None
    assert progress_pct({"pct": 140}) == 100 and progress_pct({"pct": -5}) == 0
    assert progress_pct({"pct": 39.6}) == 40, "a float reading rounds"
    for bad in (True, False, "40", None, [40], {"a": 1}):
        assert progress_pct({"pct": bad, "progress": "epoch 1/4"}) == 25, \
            f"pct={bad!r} is not a reading; a hand-edited log falls back to the scrape"

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
        "project mode reorders the groups and leaves the children newest-first"
    assert sort_groups(groups, "nonsense") == by_recent, "an unknown sort falls back to recent"

    # In actions mode the sort reaches INSIDE a project: a quiet newer session
    # must not outrank the older one holding every open action (phephree:1 had
    # four and sat under an empty phephree:0).
    hot = [("2026-08-27-x", _sess(0, 999)), ("2026-08-26-x", _sess(4, 100))]
    by_act = sort_groups(group_sessions(hot), "actions")[0]
    assert [s["date"] for s in by_act["sessions"]] == ["2026-08-26", "2026-08-27"], \
        "actions mode ranks the sessions inside a project by what needs you"
    assert [s["index"] for s in by_act["sessions"]] == [1, 0], \
        "the index is the FILE's identity - :0 is the newest - and never renumbers"
    for quiet in ("recent", "project", "nonsense"):
        assert [s["date"] for s in sort_groups(group_sessions(hot), quiet)[0]["sessions"]] \
            == ["2026-08-27", "2026-08-26"], f"{quiet} still reads newest-first"
    # Equal open actions must fall back to RECENCY, not to the date string:
    # group_sessions already emits recency order, so date and recency have to
    # disagree here or a date tie-break is invisible.
    tie = [("2026-08-27-y", _sess(2, 100)), ("2026-08-26-y", _sess(2, 900)),
           ("2026-08-25-y", _sess(2, 500))]
    assert [s["date"] for s in sort_groups(group_sessions(tie), "actions")[0]["sessions"]] \
        == ["2026-08-26", "2026-08-25", "2026-08-27"], \
        "equal open actions fall back to recency, so the order is total and " \
        "cannot flap between polls"

    fresh = group_sessions(hot)
    sort_groups(fresh, "actions")
    assert [s["date"] for s in fresh[0]["sessions"]] == ["2026-08-27", "2026-08-26"], \
        "sorting must not mutate the caller's groups: the tally sums the same list"
    assert SORTS == ("recent", "actions", "project")

    m = merge_projects([("2026-08-26-phe", {"a": {"id": "a", "sid": "beef", "ts": 1}}),
                        ("2026-08-25-phe", {"b": {"id": "b", "ts": 2}}),
                        ("2026-08-25-gpn", {"c": {"id": "c", "ts": 3}})])
    assert set(m) == {"phe", "gpn"}, "one card per project, not per file"
    assert set(m["phe"]) == {"a", "b"}, "every session's rows land in one state"
    assert m["phe"]["a"]["_from"] == "beef", "a stamped event is tagged with its session"
    assert m["phe"]["b"]["_from"] == "0825", \
        "an event recorded before stamping falls back to its file's date"
    assert merge_projects([]) == {}

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
    dia = {"a": {"type": "diagram", "title": "t", "mermaid": "flowchart LR", "ts": 1}}
    assert weight(dia) == 7, \
        "a diagram renders open and tall; the packer must budget for it"
    assert "flowchart" in row_text({"id": "x", "type": "diagram", "title": "T",
                                    "mermaid": "flowchart LR"}), \
        "the filter must see a diagram's source and title"

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

    # art_spans: strokes recede, labels read - and the chunks must reassemble.
    assert art_spans("A->B") == [("A", False), ("->", True), ("B", False)]
    assert art_spans("┌─┐") == [("┌─┐", True)], "box drawing is structure"
    assert art_spans("eval") == [("eval", False)], \
        "the letter v is not an arrowhead: a label must never split mid-word"
    assert [c for c, s in art_spans("│ logs │") if not s] == ["logs "], \
        "words inside a box are content; strokes and their padding recede"
    assert art_spans("") == [] and art_spans(None) == []
    assert art_spans("   ") == [("   ", False)], "all-neutral art is one content run"

    _arng = _random.Random(20260827)
    _art_alphabet = "ab XY01−│┌┘├→▲▶█░ -|+/\\<>^v_=~*.:\n\t"
    for _ in range(500):
        t = "".join(_arng.choice(_art_alphabet) for _ in range(_arng.randint(0, 60)))
        sp = art_spans(t)
        assert "".join(c for c, _ in sp) == t, f"chunks must reassemble {t!r}"
        assert all(c for c, _ in sp), "no empty chunks"
        assert all(isinstance(s, bool) for _, s in sp)

    art_task = {"a": {"type": "task", "status": "open", "what": "x",
                      "diagram": "a\nb\nc\nd", "ts": 1}}
    plain_task = {"a": {"type": "task", "status": "open", "what": "x", "ts": 1}}
    assert weight(art_task) == weight(plain_task) + 2, \
        "a 4-line sketch costs the packer 1 + lines//2 units - it is real height"
    assert weight({"a": {"type": "action", "status": "done", "diagram": "x\ny",
                         "ts": 1}}) == 2, \
        "a done item still DRAWS its sketch, so it still costs the packer height"
    assert "flow" in row_text({"id": "x", "type": "task", "diagram": "a->flow"}), \
        "the filter must see a sketch's labels"

    int_action = {"a": dict(one_action["a"], intuitive="i" * 220)}
    assert weight(int_action) > weight(one_action), \
        "a long intuitive line is real height, like long why/rec prose"
    int_task = {"a": {"type": "task", "status": "open", "what": "x",
                      "intuitive": "plain", "ts": 1}}
    assert weight(int_task) == 4, "a task's intuitive sub-row costs one unit"

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

    # url_spans: the span becomes a button that hands its string to a process
    # launcher, so the scheme is named, never guessed.
    u = "https://github.com/gabenavarro/table-talk/pull/117"
    assert url_spans(f"see {u} now") == [(4, 4 + len(u), u)], \
        "the span must index the ORIGINAL string exactly"
    assert url_spans(f"{u}.") == [(0, len(u), u)], \
        "a trailing sentence period is not part of the URL"
    assert url_spans("http://127.0.0.1:8731/") == [(0, 22, "http://127.0.0.1:8731/")]
    assert url_spans(f"a {u} b {u} c")[1][2] == u, "several URLs in one string"
    assert url_spans("") == [] and url_spans(None) == []
    assert url_spans("PR #117") == [], \
        "a bare #ref is not a URL: which repo a session means is not knowable"
    assert url_spans("no links here") == []
    for hostile in ("file:///etc/passwd", "javascript:alert(1)", "ftp://x/y",
                    "data:text/html,x", "HTTPS://X/Y", "xhttps://y/z"):
        assert url_spans(hostile) == [], \
            f"{hostile!r} must never become a button: the string reaches a launcher"
    assert url_spans("https://") == [] and url_spans("https://x")[0][2] == "https://x"
    assert url_spans("https://x/y\x00z")[0][2] == "https://x/y", \
        "a control character ends the URL: a real one has none, and the match " \
        "becomes an argv element"
    assert url_spans("xhttps://y/z") == [], \
        "the scheme must start the token, or the TAIL of a word becomes a link"
    assert url_spans("(https://x/y)") == [(1, 12, "https://x/y")], \
        "surrounding brackets are not part of the URL"
    print("ok")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.parse_args()
    selftest()
