#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["nicegui>=3.16,<4"]
# ///
"""table-talk dashboard: live NiceGUI view of the table-talk event logs."""
import argparse
import time
from datetime import datetime
from pathlib import Path

import tt_model as M
from tt_model import DATA_DIR, fold_cached

CSS_PATH = Path(__file__).resolve().parent / "tt.css"


def load_css():
    """The stylesheet lives beside the script so it can be edited as CSS.
    Read at startup; a missing file is a broken install, not a runtime path."""
    return CSS_PATH.read_text()


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

THEME_MODES = ("system", "light", "dark")
# Glyphs, not Quasar icon names: the shell is a terminal costume and the only
# button styling left in the sheet is the statusline's.
THEME_ICONS = {"system": "◐", "light": "☀", "dark": "☾"}

MAX_CELLS = 20     # a long session must not wreck the footer line
BAR_CELLS = 14

# The width default_cols() assumes until someone picks a column count. The real
# viewport cannot be read from the server: the only hook that fires per client,
# app.on_connect, runs in a context where this page's elements are unreachable
# (verified against NiceGUI 3.16 - client.elements reads empty there and move()
# raises "the parent slot has been deleted"), and main()'s closure runs twice per
# process with only the second one live, so the connect handler cannot even reach
# the live tick. cols 1|2|3 in the statusline is the deliberate override.
WALL_WIDTH = 1400


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


def _dim(ev, query):
    """The dim suffix for one row's class list, or ''.

    The filter DIMS, it never hides: a filter must never remove an open action
    from the wall. Non-matching rows stay in place at reduced opacity, so the
    layout does not reflow and nothing disappears while you look for something
    else. tt_model.matches answers a whole session; this answers one row."""
    q = (query or "").strip().lower()
    return " tt-dim" if q and q not in M.row_text(ev).lower() else ""


def _id_button(ev, cls):
    """The id IS the command. A nested label rather than a bare button so we stay
    on public API; the delegated listener finds the button via closest()."""
    from nicegui import ui
    with ui.element("button").props(f'data-id={ev["id"]}').classes(f"id {cls}"):
        ui.label(str(ev["id"]))


def _marked(text, query, cls=None):
    """User text with the query highlighted. tt_model.marked escapes every chunk
    itself and is property-tested; it is the only thing allowed to reach ui.html."""
    from nicegui import ui
    el = ui.html(M.marked(text or "", (query or "").strip()))
    return el.classes(cls) if cls else el


def _action_row(ev, blink, query, changed):
    from nicegui import ui
    with ui.element("div").classes(("row changed" if changed else "row") + _dim(ev, query)):
        _id_button(ev, "id-act")
        with ui.element("div"):
            with ui.element("div").classes("ttl"):
                _marked(ev.get("background", ""), query)
                if blink:   # exactly one cursor on the page: the newest thing waiting on you
                    ui.label("▉").classes("cursor")
            for glyph, label, field in (("├─", "why", "why"), ("└─", "rec", "rec")):
                with ui.element("div").classes("sub"):
                    ui.label(glyph).classes("gd")
                    ui.label(label).classes("lb")
                    _marked(ev.get(field, ""), query)


def _task_row(ev, query, changed):
    from nicegui import ui
    with ui.element("div").classes(("row changed-job" if changed else "row") + _dim(ev, query)):
        _id_button(ev, "id-job")
        with ui.element("div"):
            _marked(ev.get("what", ""), query, "ttl")
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
                    _marked(text, query, "raw")


def _term_row(ev, query):
    from nicegui import ui
    with ui.element("div").classes("row" + _dim(ev, query)):
        ui.label(ev.get("term", "")).classes("id id-gls")
        with ui.element("div"):
            _marked(ev.get("intuitive", ""), query, "ttl")
            with ui.element("div").classes("sub"):
                ui.label("└─").classes("gd")
                ui.label("def").classes("lb")
                _marked(ev.get("technical", ""), query)


def _done_row(ev, query):
    """A resolved action or task, dimmed. Keeps its id clickable so a mistaken
    'done' is easy to find again."""
    from nicegui import ui
    with ui.element("div").classes("row" + _dim(ev, query)):
        _id_button(ev, "id-ok")
        _marked(ev.get("background") or ev.get("what", ""), query, "ttl")


def _hits(evs, query):
    """True when a query is live and at least one of these rows matches it.
    Drives the #57 behaviour: a match inside a collapsed section is an invisible
    match, so a live query opens the section holding it."""
    return bool((query or "").strip()) and any(_dim(ev, query) == "" for ev in evs)


def _prompt(cls, title, count, toggles=None, opened=None, key="", force=False):
    """A section header as a shell prompt line: '❯ actions --open (3)'.

    When `toggles` is given, clicking the line shows or hides that container —
    this is what keeps the collapsed glossary and done sections. `opened` is a
    dict owned by the caller and outliving this render, so a section the user
    expanded is still expanded after the next poll rebuilds the body. `force`
    opens the section for this render only, without touching what the user chose.
    """
    from nicegui import ui
    shown = force or bool(opened and opened.get(key))
    with ui.element("div").classes(f"pr {cls}") as line:
        ui.label("❯").classes("g")
        ui.label(title)
        tail = "" if toggles is None else (" ▾" if shown else " ▸")
        caret = ui.label(f"({count}){tail}").classes("n")
    if toggles is None:
        return
    toggles.set_visibility(shown)

    def flip(_):
        opened[key] = not (force or opened[key])
        toggles.set_visibility(opened[key])
        caret.set_text(f"({count}) " + ("▾" if opened[key] else "▸"))

    line.on("click", flip)


def render_window_body(container, state, newest_action_id, query="", changed=()):
    """Rebuild one window's body.

    A window holds a handful of rows, so rebuilding it is cheaper and far
    simpler than diffing them - and the caller only calls this when that
    window's data actually changed.

    `query` drives dimming and highlighting only, never visibility: see _dim.
    The one exception is section collapse, which is not filtering: a live query
    opens a collapsed section holding a match, because a match you cannot see is
    the same as no match (#57).

    Which sections the user has expanded is kept on the container, which
    outlives the rebuild, so a poll cannot snap an open panel shut underneath a
    reader. It dies with the container, so nothing has to clean it up.
    """
    from nicegui import ui
    opened = getattr(container, "tt_open", None)
    if opened is None:
        opened = container.tt_open = {"gls": False, "ok": False}
    container.clear()
    with container:
        acts = open_rows(state, "action")
        jobs = open_rows(state, "task")

        _prompt("p-act", "actions --open", len(acts))
        if not acts:
            ui.label("nothing needs you").classes("empty")
        for ev in acts:
            _action_row(ev, str(ev["id"]) == newest_action_id, query, str(ev["id"]) in changed)

        _prompt("p-job", "jobs", len(jobs))
        if not jobs:
            ui.label("nothing running").classes("empty")
        for ev in jobs:
            _task_row(ev, query, str(ev["id"]) in changed)

        # Glossary and done are collapsed until the user opens them or a query
        # finds something inside. Each box is built before the prompt that
        # toggles it, then moved back under it with move(container, -1).
        terms = term_rows(state)
        gls_box = ui.element("div")
        with gls_box:
            for ev in terms:
                _term_row(ev, query)
        _prompt("p-gls", "glossary", len(terms), toggles=gls_box,
                opened=opened, key="gls", force=_hits(terms, query))
        gls_box.move(container, -1)

        done = done_rows(state)
        done_box = ui.element("div")
        with done_box:
            for ev in done:
                _done_row(ev, query)
        _prompt("p-ok", "done", len(done), toggles=done_box,
                opened=opened, key="ok", force=_hits(done, query))
        done_box.move(container, -1)


def default_cols(width):
    """Column count before the user picks one. Three on a wide second monitor."""
    return 3 if width >= 1800 else (2 if width >= 1200 else 1)


def layout_key(visible, cols, marks, folds, zoomed, scope, sort, drawer_open):
    """Everything that changes WHERE a window sits. The wall re-packs when this
    changes and at no other time - never on a poll that only changed text."""
    return (tuple(visible), cols, tuple(sorted(marks)), tuple(sorted(folds)),
            zoomed, scope, sort, drawer_open)


def selftest():
    # fold/fold_cached now live in tt_model and are pinned by its own selftest.
    st = {"a": {"id": "a", "type": "action", "status": "open", "background": "bg", "ts": 1},
          "b": {"id": "b", "type": "action", "status": "done", "background": "old", "ts": 2},
          "c": {"id": "c", "type": "task", "status": "open", "what": "train", "ts": 3},
          "d": {"id": "d", "type": "term", "term": "FBA", "intuitive": "i", "technical": "t", "ts": 4},
          "e": {"id": "e", "type": "action", "status": "open", "background": "new", "ts": 9}}
    assert [r["id"] for r in open_rows(st, "action")] == ["e", "a"], "open actions read newest first"
    assert [r["id"] for r in open_rows(st, "task")] == ["c"]
    assert [r["id"] for r in done_rows(st)] == ["b"], "done spans actions and tasks, never terms"
    assert [r["term"] for r in term_rows(st)] == ["FBA"], "terms are cumulative"
    assert _dim(st["a"], "") == "" and _dim(st["a"], "  ") == "", "an empty query dims nothing"
    assert _dim(st["a"], "BG") == "", "a matching row is not dimmed, and matching ignores case"
    assert _dim(st["a"], "kubernetes") == " tt-dim", "a non-matching row dims — it never hides"
    assert _hits([st["d"]], "flux") is False and _hits([st["d"]], "FBA") is True
    assert _hits([st["d"]], "") is False, "no query opens nothing"
    assert _hits([], "FBA") is False
    css = load_css()
    # both palettes, keyed the way the design spec pins them
    assert "--bg:#1d2021" in css and "--surface:#282828" in css, "gruvbox-dark ground and surface"
    assert "--bg:#e8e6dc" in css and "--surface:#faf9f5" in css, "claude-code-light ground and surface"
    assert "--caret:#8ec07c" in css and "--caret:#d97757" in css, "cursor colour per theme"
    assert "--act:#fb4934" in css and "--act:#a53a2e" in css
    assert "body.body--dark" in css, "dark palette must key off Quasar's body--dark"
    assert "tabular-nums" in css, "digit columns must align"
    assert "prefers-reduced-motion" in css, "motion must be defeatable"
    assert "ui-monospace" in css and "system-ui" in css, "both faces need a real fallback stack"
    assert "--ctp-" not in css, "the Catppuccin palette is gone"
    assert set(THEME_ICONS) == set(THEME_MODES)
    assert ago(0, now=30) == "just now" and ago(0, now=90) == "1m ago"
    assert ago(0, now=7200) == "2h ago" and ago(0, now=200000) == "2d ago"
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
    assert ".tt-dim" in css and ".tt-hit" in css, "dim and highlight need styles to mean anything"
    assert ".dw-find" in css and ".tt-none" in css, "the filter bar and empty wall need styles"
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
    print("ok")


def ago(ts, now=None):
    d = (now if now is not None else time.time()) - ts
    if d < 60:
        return "just now"
    if d < 3600:
        return f"{int(d // 60)}m ago"
    if d < 86400:
        return f"{int(d // 3600)}h ago"
    return f"{int(d // 86400)}d ago"


def stamp(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def main(port):
    from nicegui import app, ui

    ui.add_head_html(f"<style>{load_css()}</style>")
    ui.add_head_html(COPY_JS)
    # the app shell owns the viewport; NiceGUI's page wrapper must not pad it
    ui.query(".nicegui-content").style("padding:0;gap:0;max-width:none")

    def store(key, default):
        """Persisted UI state. Loopback, one user, so server-wide storage is
        correct here and keeps two tabs in agreement. No storage_secret needed."""
        return app.storage.general.get(f"tt.{key}", default)

    def put(key, value):
        app.storage.general[f"tt.{key}"] = value

    dark = ui.dark_mode()
    mode = store("theme", "system")
    if mode not in THEME_MODES:  # a hand-edited/stale storage value must not crash startup
        mode = "system"

    def apply_theme(m):
        {"system": dark.auto, "light": dark.disable, "dark": dark.enable}[m]()

    apply_theme(mode)

    # Layout state. marks/folds/zoomed/scope are read once and then owned by the
    # handlers in Task 12; cols/sort/drawer_open are read per tick because a
    # handler only has to write them for the next tick to pick them up.
    marks = set(store("marks", []))
    folds = set(store("folds", []))
    zoomed = store("zoomed", None)
    scope = store("scope", None)

    with ui.element("div").classes("tt-app"):
        shell = ui.element("div").classes("tt-main")
        with shell:
            with ui.element("div").classes("dw"):
                # The filter bar sits above the session tree, outside the container
                # render_drawer() clears, and carries the N/M readout: under dim,
                # "nothing matched" and "your match is 600 px below" look identical.
                with ui.element("div").classes("dw-find"):
                    search = ui.input(placeholder="filter").props(
                        "clearable dense borderless debounce=100").classes("dw-in")
                    hits = ui.label("").classes("dw-count")
                    theme_btn = ui.element("button").classes("dw-theme")
                    with theme_btn:
                        theme_lbl = ui.label(THEME_ICONS[mode])
                drawer = ui.element("div").classes("dw-tree")   # Task 10 renders here
            wall = ui.element("div").classes("wall")
    # Windows live here whenever they are off the wall. Parking them rather than
    # deleting them is what lets scope and zoom be reversible for free, and it is
    # what makes wall.clear() safe: clearing a column deletes what is inside it.
    attic = ui.element("div").style("display:none")

    def cycle_theme():
        nonlocal mode
        mode = THEME_MODES[(THEME_MODES.index(mode) + 1) % len(THEME_MODES)]
        put("theme", mode)
        apply_theme(mode)
        theme_lbl.set_text(THEME_ICONS[mode])

    theme_btn.on("click", lambda _: cycle_theme())

    windows = {}     # session stem -> window parts, built once and moved, never rebuilt
    columns = []     # the current column containers
    layout = None    # last layout_key; the wall re-packs only when this changes

    def on_window_action(key, action):   # replaced in Task 12
        pass

    def build_window(key, project, index, state):
        el = ui.element("div").classes("win").props(f'data-window="{key}"')
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
                "hot": False, "latest": 0, "sig": None}

    def dress(key, win):
        """Every class on a window: `hot` comes from its data, marked/folded/zoomed
        from the layout. One place, or the two callers clobber each other's
        classes(replace=...)."""
        cls = "win"
        if win["hot"]:
            cls += " win-hot"
        if key in marks:
            cls += " marked"
        if key in folds:
            cls += " folded"
        win["el"].classes(replace=cls)
        win["mark"].set_visibility(key in marks)
        win["zoom"].set_visibility(key == zoomed)

    def paint_window(win, key, state, newest, query="", changed=()):
        """Update everything about one window that depends on its data.
        Called only when that window's data actually changed."""
        summary = M.summarize(state)
        win["bell"].set_visibility(summary["open_actions"] > 0)
        win["actv"].set_visibility(summary["open_tasks"] > 0)
        win["hot"] = summary["open_actions"] > 0
        dress(key, win)
        on, off = resolved_cells(summary["resolved"], summary["recorded"])
        win["cells"].clear()
        with win["cells"]:
            ui.label(on).classes("on")
            ui.label(off).classes("off")
        win["tally"].set_text(
            f'{summary["resolved"]}/{summary["recorded"]} resolved'
            + (" · all clear" if summary["open_actions"] == 0 and summary["open_tasks"] == 0 else ""))
        win["latest"] = summary["latest"]
        win["when"].props(f'title="{stamp(summary["latest"])}"' if summary["latest"] else "")
        render_window_body(win["body"], state, newest, query, changed)

    def repack(visible, cols, weights):
        """Move existing windows into freshly sized columns. move() preserves
        element identity, so nothing is rebuilt and nothing flickers."""
        for win in windows.values():
            win["el"].move(attic, -1)      # park first: clearing a column deletes its children
        wall.clear()
        columns.clear()
        buckets = M.pack(visible, cols, weights, marks) if visible else []
        with wall:
            if not visible:
                ui.label("no sessions yet — record something with table-talk").classes("tt-none")
            for _ in buckets:
                columns.append(ui.element("div").classes("col"))
        for bucket, container in zip(buckets, columns):
            for key in bucket:
                windows[key]["el"].move(container, -1)
                dress(key, windows[key])

    def tick():
        nonlocal layout
        states = {p.stem: fold_cached(p) for p in sorted(DATA_DIR.glob("*.jsonl"), reverse=True)}
        groups = M.group_sessions(list(states.items()))
        where = {s["key"]: (g["project"], s["index"]) for g in groups for s in g["sessions"]}
        sort = store("sort", "recent")
        # the wall has no sort of its own: it reads in the drawer's order
        order = [s["key"] for g in M.sort_groups(groups, sort) for s in g["sessions"]]

        for key in list(windows):              # a session file went away
            if key not in states:
                windows.pop(key)["el"].delete()
        for key in order:
            if key not in windows:
                with attic:
                    windows[key] = build_window(key, *where[key], states[key])

        # Scope and zoom choose what is on the wall. The query never does: the
        # filter dims, it never hides, so an open action cannot leave the wall.
        visible = [k for k in order if scope in (None, where[k][0])]
        if zoomed in windows:
            visible = [zoomed]
        drawer_open = store("drawer_open", True)
        cols = 1 if zoomed in windows else store("cols", 0) or default_cols(WALL_WIDTH)
        key = layout_key(visible, cols, marks, folds, zoomed, scope, sort, drawer_open)
        if key != layout:
            layout = key
            shell.classes(replace="tt-main" if drawer_open else "tt-main tt-collapsed")
            repack(visible, cols, {k: M.weight(v) for k, v in states.items()})

        query = (search.value or "").strip()
        # exactly one cursor on the page: the newest open action anywhere on the wall
        newest, newest_ts = None, -1
        rows = matched = 0
        for k in visible:
            for ev in states[k].values():
                typ = ev.get("type")
                if typ not in ("action", "task", "term"):
                    continue
                rows += 1
                matched += not _dim(ev, query)
                if typ == "action" and ev.get("status") != "done" and ev.get("ts", 0) > newest_ts:
                    newest, newest_ts = str(ev["id"]), ev.get("ts", 0)
        txt = f"{matched}/{rows} rows match" if query else ""
        if hits.text != txt:
            hits.set_text(txt)

        for k in visible:
            win = windows[k]
            sig = (query, newest, states[k])
            if win["sig"] != sig:
                win["sig"] = sig
                paint_window(win, k, states[k], newest, query)
            age = ago(win["latest"]) if win["latest"] else ""   # advances with no new data
            if win["when"].text != age:
                win["when"].set_text(age)

    def on_query():
        """Dim alone leaves the match off-screen on half of realistic queries with
        no hint which way to scroll; clearing is how you return to glance mode.
        rAF because the rows carrying .tt-hit are still being patched in."""
        tick()
        target = ("document.querySelector('.tt-hit')?.scrollIntoView({block:'center'})"
                  if (search.value or "").strip() else
                  "document.querySelector('.wall')?.scrollTo({top:0})")
        ui.run_javascript(f"requestAnimationFrame(() => {target})")

    search.on_value_change(lambda _: on_query())

    tick()
    # fold_cached re-parses only changed files; steady-state tick is O(files stat)
    ui.timer(2.0, tick)
    ui.run(host="127.0.0.1", port=port, show=False, reload=False, title="table-talk", favicon="🗣")


if __name__ in {"__main__", "__mp_main__"}:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8731)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        selftest()
    else:
        main(a.port)
