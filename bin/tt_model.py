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
