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
