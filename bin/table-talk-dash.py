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


def card_data(state):
    """Everything one card renders; pure and comparable, so ticks can no-op on unchanged data."""
    return {"action": rows(state, "action", done=False),
            "task": rows(state, "task", done=False),
            "term": rows(state, "term"),
            "done": done_rows(state)}


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
        assert card_data(s) == card_data(fold(p)), "card_data must be stable for change-gating"
        with open(p, "a") as fh:
            fh.write('{"id":"c3d4","progress":"epoch 4","ts":6}\n')
        after = card_data(fold(p))
        assert after != card_data(s) and after["task"][0]["progress"] == "epoch 4", \
            "card_data must change when an event lands"
    print("ok")


SECTIONS = (("🔴 Actions needed", "action", "id"),
            ("🔵 Background work", "task", "id"),
            ("📖 Glossary (cumulative)", "term", "term"))


def main(port):
    from nicegui import ui

    cards = {}    # path -> {"tables": {key: ui.table}, "expansion": ui.expansion, "data": card_data}
    built = None  # file list the DOM was last built for; None = never built

    def build_card(path, data):
        with ui.card().classes("w-full"):
            ui.label(path.stem).classes("text-lg font-bold")
            tables = {}
            for label, key, row_key in SECTIONS:
                ui.label(label)
                tables[key] = ui.table(columns=columns(key), rows=data[key],
                                       row_key=row_key).classes("w-full")
            exp = ui.expansion(f"{len(data['done'])} done").classes("w-full opacity-50")
            with exp:
                tables["done"] = ui.table(columns=columns("done"), rows=data["done"],
                                          row_key="id").classes("w-full")
            exp.set_visibility(bool(data["done"]))
        return {"tables": tables, "expansion": exp, "data": data}

    container = ui.column().classes("w-full")

    def tick():
        nonlocal built
        files = sorted(DATA_DIR.glob("*.jsonl"), reverse=True)
        if files != built:  # session file appeared/disappeared: rebuild structure (only case that resets scroll)
            container.clear()
            cards.clear()
            with container:
                if not files:
                    ui.label("no sessions yet - record something with the table-talk CLI").classes("m-8 text-gray-500")
                for p in files:
                    cards[p] = build_card(p, card_data(fold(p)))
            built = files
            return
        for p, card in cards.items():  # data-only change: mutate rows in place so scroll and sort survive
            data = card_data(fold(p))
            if data == card["data"]:
                continue
            for key, table in card["tables"].items():
                if data[key] != card["data"][key]:
                    table.rows[:] = data[key]
                    table.update()
            card["expansion"].set_text(f"{len(data['done'])} done")
            card["expansion"].set_visibility(bool(data["done"]))
            card["data"] = data

    tick()
    # ponytail: full re-glob+refold every tick; mtime-gate if files reach hundreds
    ui.timer(2.0, tick)
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
