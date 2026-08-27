#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["nicegui>=3.16,<4"]
# ///
"""table-talk dashboard: live NiceGUI view of the table-talk event logs."""
import argparse
import logging
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

# Both of these key on the statusline's open-action tally (#tt-tally) and read
# the count out of its glyph text: "●6 open  ▶5 running" | "▶2 running" | "all
# clear". ● prefixes actions and ▶ prefixes tasks, so /●(\d+)/ is unambiguous
# and "no ● at all" is honestly zero.
TAB_TITLE_JS = """<script>
document.addEventListener('DOMContentLoaded', () => {
  const sync = () => {
    const el = document.getElementById('tt-tally');
    if (!el) return;
    const m = el.textContent.match(/●(\\d+)/);
    document.title = (m ? `(${m[1]}) ` : '') + 'table-talk';
  };
  const wait = setInterval(() => {
    const el = document.getElementById('tt-tally');
    if (el) {
      clearInterval(wait);
      new MutationObserver(sync).observe(el, {childList: true, characterData: true, subtree: true});
      sync();
    }
  }, 500);
});
</script>"""

# Toast when the open-action count RISES: the baseline is read before observing,
# so a page load or a reconnect never fires a stale burst.
TOAST_JS = """<script>
document.addEventListener('DOMContentLoaded', () => {
  const show = (msg) => {
    const t = document.createElement('div');
    t.className = 'tt-toast';
    t.textContent = msg;
    document.body.appendChild(t);
    requestAnimationFrame(() => t.classList.add('tt-toast-in'));
    setTimeout(() => { t.classList.remove('tt-toast-in'); setTimeout(() => t.remove(), 400); }, 5000);
  };
  const wait = setInterval(() => {
    const el = document.getElementById('tt-tally');
    if (!el) return;
    clearInterval(wait);
    const read = () => { const m = el.textContent.match(/●(\\d+)/); return m ? +m[1] : 0; };
    let prev = read();
    new MutationObserver(() => {
      const n = read();
      if (n > prev) {
        const d = n - prev, s = d > 1 ? 's' : '';
        show(`${d} new action item${s} need${s ? '' : 's'} you`);
      }
      prev = n;
    }).observe(el, {childList: true, characterData: true, subtree: true});
  }, 500);
});
</script>"""

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


# Blur a button the moment it is clicked WITH A MOUSE. ui.keyboard ignores every
# keystroke while document.activeElement is an input/select/button/textarea
# (verified in keyboard.js), and every control here - drawer rows, window M/Z/▾,
# statusline chips - is a <button>. Without this, one click on any of them
# silently kills the whole keyboard layer until you click elsewhere.
#
# e.detail is the click count: 1+ for a real pointer, 0 when Enter or Space on a
# focused button synthesises the click. Blurring on the synthetic one would throw
# a keyboard user's focus ring to <body> after every activation, which is the
# same bug pointed the other way.
BLUR_JS = """<script>
document.addEventListener('click', e => {
  const b = e.target.closest && e.target.closest('button');
  if (b && e.detail) b.blur();
});
</script>"""

# Every key the page binds. The statusline chips are built from this same dict
# and dispatch through the same handler, so a key can never do something no
# click can.
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


THEME_MODES = ("system", "light", "dark")
# Glyphs, not Quasar icon names: the shell is a terminal costume and the only
# button styling left in the sheet is the statusline's.
THEME_ICONS = {"system": "◐", "light": "☀", "dark": "☾"}

# tmux choose-tree guides. Kept as literal glyphs so the verticals connect in a
# fixed-width column rather than being faked with borders.
GUIDES = {"open": "▾", "closed": "▸", "mid": "├", "last": "└", "line": "│", "none": " "}

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


def abbrev(project):
    """Three-letter tag for the collapsed rail."""
    return project[:3] if project else "?"


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
    assert abbrev("phephree") == "phe"
    assert abbrev("table-talk") == "tab"
    assert abbrev("ab") == "ab", "a short name is not padded"
    assert abbrev("") == "?"
    assert GUIDES == {"open": "▾", "closed": "▸", "mid": "├", "last": "└", "line": "│", "none": " "}
    assert ".dw-row" in css and ".trk i" in css and ".rail-t" in css, \
        "the tree, its meters and the collapsed rail all need styles to mean anything"
    assert len(SPINNER) == 10 and SPINNER[0] == "⠋"
    assert tally_text(6, 5) == "●6 open  ▶5 running"
    assert tally_text(0, 0) == "all clear"
    assert tally_text(1, 0) == "●1 open"
    assert tally_text(0, 2) == "▶2 running"
    assert "tt-tally" in TAB_TITLE_JS and "document.title" in TAB_TITLE_JS
    assert "tt-tally" in TOAST_JS and "tt-toast" in TOAST_JS
    assert ".tt-toast" in css and ".tt-toast-in" in css, "toast script and styles must pair"
    assert ".sl-s.hidden" in css, \
        "set_visibility appends the CLASS 'hidden'; a [hidden] attribute rule would never match"
    assert "width:100%" in css.split(".tt-app{")[1].split("}")[0], \
        "the shell must be pinned to the viewport, or nowrap statusline segments widen the page"
    assert KEYMAP == {"\\": "drawer", "m": "mark", "z": "zoom", "f": "fold",
                      "s": "sort", "/": "filter", "!": "needs-me", "?": "keys",
                      "Escape": "unzoom"}
    assert next_sort("recent") == "actions"
    assert next_sort("actions") == "project"
    assert next_sort("project") == "recent"
    assert next_sort("nonsense") == "recent", "an unknown sort cycles back to the default"
    assert toggle({"a"}, "a") == set() and toggle(set(), "a") == {"a"}
    assert toggle({"a"}, "b") == {"a", "b"}
    assert toggle({"a"}, "a") is not None and "a" in {"a"}, "toggle returns a NEW set"
    src = {"a"}
    toggle(src, "a")
    assert src == {"a"}, "toggle must not mutate its argument"
    assert ".sl-h" in css and ".tt-keys" in css, \
        "the statusline key chips and the ? overlay both need styles"
    assert ".win.cur" in css and ".fl-c" in css, \
        "the window m/z/f act on carries the spec's * flag, not CSS alone"
    assert ".dw-fold" in css, "the drawer triangle is a control and must look like one"
    assert "closest('button')" in BLUR_JS, \
        "keyboard.js ignores keys while a button holds focus; clicks must blur"
    assert "e.detail" in BLUR_JS, \
        "Enter/Space on a button synthesise a click with detail 0; blurring that " \
        "throws a keyboard user's focus ring to <body> after every activation"
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
    ui.add_head_html(TAB_TITLE_JS)
    ui.add_head_html(TOAST_JS)
    ui.add_head_html(BLUR_JS)
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

    # Layout state. marks/folds/zoomed/scope/needs_me/current are read once and
    # then owned by the handlers below; cols/sort/drawer_open are read per tick
    # because a handler only has to write them for the next tick to pick them up.
    marks = set(store("marks", []))
    folds = set(store("folds", []))
    groups_folded = set(store("groups_folded", []))
    zoomed = store("zoomed", None)
    scope = store("scope", None)
    needs_me = bool(store("needs_me", False))
    current = store("current", None)   # the window m/z/f act on
    on_wall = []                       # what poll() last put on the wall, in order

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
        # The statusline: a sibling BELOW .tt-main, not inside it, so the drawer
        # and the wall both stop 28 px short of the floor and it spans the lot.
        # It carries no filter readout - the drawer's N/M one already exists and
        # lives beside the box you type into, which is where it belongs.
        with ui.element("div").classes("sl"):
            with ui.element("div").classes("sl-s on"):
                spin = ui.label(SPINNER[0]).classes("spin")
                ui.label("table-talk")
            cadence = ui.element("div").classes("sl-s")
            with cadence:
                ui.label("Every 2.0s · last")
                last_stamp = ui.label("--:--:--")
            # id, not a class: TAB_TITLE_JS and TOAST_JS both getElementById this
            # and hang a MutationObserver on it. Keep the id if you move it.
            tally = ui.label("").classes("sl-s").props("id=tt-tally")
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
            chips = {}
            with ui.element("div").classes("sl-s sl-k"):
                # Built from KEYMAP itself, so the hints cannot drift from the
                # bindings, and each is a BUTTON: every key needs a click
                # equivalent. filter and unzoom are left out - the box you type
                # in and Esc are their own affordance, and a "click to unzoom"
                # chip would sit dead on a wall that is not zoomed.
                # <b> with a nested label, never ui.html: exactly one ui.html is
                # allowed on this page and _marked owns it.
                for hotkey, what in KEYMAP.items():
                    if what in ("filter", "unzoom"):
                        continue
                    # no title= tooltip: the chip already reads '\ drawer', and a
                    # backslash inside a props string trips NiceGUI's parser into
                    # compiling it as an escape ("invalid escape sequence '\)'")
                    chips[what] = b = ui.element("button").classes("sl-h")
                    with b:
                        with ui.element("b"):
                            ui.label(hotkey)
                        ui.label(what)
                    b.on("click", lambda _, w=what: do(w), [])
            clock = ui.label("").classes("sl-clock")
    with ui.dialog() as keys_dlg, ui.element("div").classes("tt-keys"):
        ui.label("keys").classes("ttl")
        for hotkey, what in KEYMAP.items():
            with ui.element("div").classes("k-row"):
                with ui.element("b"):
                    ui.label(hotkey)
                ui.label(what)
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

    def _replace(target, new, store_key):
        """Mutate the live set in place (the handlers close over it) and persist.
        `new` is computed by the caller BEFORE this runs: clearing first would
        make toggle() operate on an already-empty set and every mark would read
        as 'not marked'."""
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

    def on_pick(key):
        """Make one window current. Re-dresses the two windows that changed
        rather than ticking: nothing moved, so a re-pack would be a lie."""
        nonlocal current
        if key == current:
            return
        old, current = target(), key    # target() first: it still sees the old one
        put("current", key)
        for k in (old, key):
            if k in windows:
                dress(k, windows[k])

    def target():
        """The window m/z/f act on, and the one carrying the * flag: the one you
        last touched if it is still on the wall, else the first window on it.
        Never something you cannot see, and never nothing while a wall exists -
        so the indicator is present from the first paint, with no prior click."""
        return current if current in on_wall else (on_wall[0] if on_wall else None)

    def build_window(key, project):
        """Everything about a window that never changes: its project is its file's
        name. The tmux index is NOT one of those - it is position by recency
        within the project, so every older sibling shifts down when a newer
        session file lands. It is set from `where` on every tick instead."""
        el = ui.element("div").classes("win").props(f'data-window="{key}"')
        # clicking anywhere in a window makes it current, M/Z/▾ included
        el.on("click", lambda _, k=key: on_pick(k), [])
        with el:
            with ui.element("div").classes("win-t"):
                ui.label(project).classes("nm")
                ix = ui.label("").classes("ix")
                bell = ui.label("!").classes("bell")
                actv = ui.label("#").classes("actv")
                mark = ui.label("M").classes("fl-m")
                zoom = ui.label("Z").classes("fl-z")
                star = ui.label("*").classes("fl-c")   # current, per the design spec
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
        return {"el": el, "body": body, "ix": ix, "bell": bell, "actv": actv, "mark": mark,
                "zoom": zoom, "star": star, "when": when, "cells": cells, "tally": tally,
                "hot": False, "latest": 0, "sig": None}

    # project -> its open-action count when we last looked. Persisted, because
    # "the first time we see it" has to mean the first time EVER: kept in the
    # process only, a reload re-runs this closure with an empty dict, every
    # quiet project reads as new, and the auto-fold silently undoes whatever the
    # user chose with the triangle.
    seen_projects = dict(store("seen", {}))

    def on_scope(project):
        """Clicking a project scopes the wall to it; clicking it again, or the ✕
        in the statusline, clears the scope. Zoom goes with it - a zoom held
        across a scope change points at a window that is no longer on the wall."""
        nonlocal scope, zoomed
        scope = None if scope == project else project
        zoomed = None
        put("scope", scope)
        put("zoomed", None)
        tick()

    def on_focus(key):
        """Clicking a session in the drawer scrolls its window into view. The
        drawer lists every session, scoped or not, so anything that could be
        hiding this one is cleared first - a click that visibly does nothing is
        worse than a click that changes the view."""
        nonlocal zoomed, scope, needs_me
        on_pick(key)
        if key not in on_wall:
            zoomed, scope, needs_me = None, None, False
            put("zoomed", None)
            put("scope", None)
            put("needs_me", False)
            tick()
        # rAF: the window may have only just been moved back onto the wall
        ui.run_javascript(
            'requestAnimationFrame(() => document.querySelector('
            f'\'[data-window="{key}"]\')'
            '?.scrollIntoView({behavior:"smooth",block:"start"}))')

    def on_cols(n):
        """cols is read from storage per tick, so writing it and re-ticking is the
        whole handler: layout_key changes and repack() moves the windows."""
        put("cols", n)
        tick()

    def cycle_sort():
        put("sort", next_sort(store("sort", "recent")))
        tick()

    def do(what):
        """One dispatcher for the whole interaction layer. The statusline chips
        and the keyboard both come through here, so a key can never do something
        no click can, and the two can never drift apart."""
        nonlocal needs_me
        if what in ("mark", "zoom", "fold"):
            if (key := target()):
                on_window_action(key, what)
        elif what == "drawer":
            put("drawer_open", not store("drawer_open", True))
            tick()
        elif what == "sort":
            cycle_sort()
        elif what == "needs-me":
            # not a filter: it drops windows with NOTHING open, so it cannot
            # hide an open action however hard you squint at it
            needs_me = not needs_me
            put("needs_me", needs_me)
            tick()
        elif what == "unzoom":
            if zoomed:
                on_window_action(zoomed, "zoom")
        elif what == "filter":
            ui.run_javascript('document.querySelector(".dw-find input")?.focus()')
        elif what == "keys":
            keys_dlg.close() if keys_dlg.value else keys_dlg.open()

    def on_key(e):
        # ignore defaults to ['input','select','button','textarea'] and is checked
        # against document.activeElement, so keys do not fire while the filter box
        # has focus. Verified in both directions; BLUR_JS is what keeps a clicked
        # button from holding that focus forever.
        if not e.action.keydown or e.action.repeat or keys_dlg.value:
            return
        if (what := KEYMAP.get(e.key.name)):
            do(what)

    ui.keyboard(on_key=on_key)

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
                with ui.element("div").classes("trk"):
                    fill = ui.element("i").style(f"width:{pct}%")
                    if pct >= 100:
                        fill.classes("full")
                ui.label("]")
            ui.label(f"{pct}%").classes("pc")

    def apply_fold_rules(groups):
        """A project with nothing open folds the first time we see it, so the
        drawer opens showing only what is live. A poll that RAISES the
        open-action count forces the group back open: a fold must never hide
        something that just started needing you.

        Rising edge, not level: comparing against the previous count is what the
        docstring always promised, and it is what makes the triangle a real
        control. A level test ('any open action forces it open') springs a
        manually folded group back open two seconds later, every time, so a busy
        project could never be collapsed at all."""
        changed, before = False, dict(seen_projects)
        for g in groups:
            project, n = g["project"], g["open_actions"]
            was = seen_projects.get(project)
            if was is None:
                if n == 0:
                    groups_folded.add(project)
                    changed = True
            elif n > was and project in groups_folded:      # an action just LANDED
                groups_folded.discard(project)
                changed = True
            seen_projects[project] = n
        if changed:
            put("groups_folded", sorted(groups_folded))
        if seen_projects != before:
            put("seen", seen_projects)

    def on_group_fold(project):
        """The drawer's ▾/▸ triangle. Folds the group and persists it; the rest
        of the row still scopes the wall."""
        _replace(groups_folded, toggle(groups_folded, project), "groups_folded")
        tick()

    def drawer_sig(groups, collapsed):
        """Everything the tree draws, in one comparable value. The poll runs every
        2 s and the drawer usually has nothing new to say; rebuilding it anyway
        would drop hover and keyboard focus off a row somebody is reading."""
        # `scope` is in here because the scoped row carries .dw-on: leave it out
        # and the highlight never repaints when the scope changes, which is
        # exactly the bug this signature is otherwise there to cause.
        return (collapsed, scope, store("sort", "recent"), tuple(sorted(groups_folded)),
                tuple((g["project"], g["open_actions"], g["open_tasks"], g["pct"],
                       tuple((s["key"], s["date"], s["summary"]["open_actions"],
                              s["summary"]["open_tasks"], s["summary"]["pct"],
                              ago(s["summary"]["latest"])) for s in g["sessions"]))
                      for g in groups))

    def render_drawer(container, groups):
        collapsed = not store("drawer_open", True)
        sig = drawer_sig(groups, collapsed)
        if getattr(container, "tt_sig", None) == sig:
            return
        container.tt_sig = sig
        container.clear()
        with container:
            if collapsed:
                # 54 px of drawer: the three-letter tag, what is waiting, and the
                # meter. Same click target as the full row, so scope survives the
                # collapse rather than being a different feature at another width.
                for g in groups:
                    rail = ui.element("button").classes(
                        "rail-item dw-on" if g["project"] == scope else "rail-item")
                    rail.props(f'data-project="{g["project"]}" title="{g["project"]}"')
                    with rail:
                        ui.label(abbrev(g["project"])).classes("rail-ab")
                        ui.label(f'●{g["open_actions"]}').classes(
                            "b-act" if g["open_actions"] else "b-off")
                        with ui.element("div").classes("rail-t"):
                            ui.element("i").style(f'width:{g["pct"]}%')
                    rail.on("click", lambda _, p=g["project"]: on_scope(p))
                return
            with ui.element("div").classes("dw-top"):
                ui.label("sessions").classes("ttl")
                n = sum(len(g["sessions"]) for g in groups)
                ui.label(f"{n} · {len(groups)} projects")
            sort_row = ui.element("div").classes("dw-sort")
            with sort_row:
                ui.label("sort:")
                for mode in M.SORTS:
                    # <b>, not a class: .dw-sort b is what the stylesheet marks up
                    with ui.element("b") if mode == store("sort", "recent") else ui.element("span"):
                        ui.label(mode)
            sort_row.on("click", lambda _: cycle_sort())
            for g in groups:
                # a project with one session is one flat row: a group of one is noise
                single = len(g["sessions"]) == 1
                folded = g["project"] in groups_folded and not single
                row = ui.element("button").classes(
                    "dw-row dw-proj dw-on" if g["project"] == scope else "dw-row dw-proj")
                row.props(f'data-project="{g["project"]}"')
                with row:
                    # The triangle is a control, not decoration: it folds the
                    # group, and click.stop keeps that click off the row, which
                    # scopes. Without the stop, the only affordance promising a
                    # way back into a folded group scopes the wall instead and
                    # the tree stays truncated with no way to reopen it.
                    tri = ui.label(GUIDES["none"] if single else
                                   (GUIDES["closed"] if folded else GUIDES["open"]))
                    tri.classes("dw-g" if single else "dw-g dw-fold")
                    if not single:
                        tri.props(f'title="fold {g["project"]}"')
                        tri.on("click.stop", lambda _, p=g["project"]: on_group_fold(p), [])
                    with ui.element("div").classes("dw-l1"):
                        ui.label(g["project"]).classes("dw-nm")
                        ui.label(g["sessions"][0]["date"] if single
                                 else f'{len(g["sessions"])} sessions').classes("dw-meta")
                    # the │ under an open group is what its ├ children hang from
                    ui.label(GUIDES["none"] if single or folded
                             else GUIDES["line"]).classes("dw-g")
                    meter_row(g)
                row.on("click", lambda _, p=g["project"]: on_scope(p))
                if single or folded:
                    continue
                for i, sess in enumerate(g["sessions"]):
                    last = i == len(g["sessions"]) - 1
                    srow = ui.element("button").classes("dw-row dw-sess")
                    srow.props(f'data-session="{sess["key"]}"')
                    with srow:
                        ui.label(GUIDES["last"] if last else GUIDES["mid"]).classes("dw-g")
                        with ui.element("div").classes("dw-l1"):
                            ui.label(sess["date"]).classes("dw-nm")
                            ui.label(ago(sess["summary"]["latest"])).classes("dw-meta")
                        ui.label(GUIDES["none"] if last else GUIDES["line"]).classes("dw-g")
                        meter_row(sess["summary"])
                    srow.on("click", lambda _, k=sess["key"]: on_focus(k))

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
        cur = key == target()
        if cur:
            cls += " cur"
        win["el"].classes(replace=cls)
        win["mark"].set_visibility(key in marks)
        win["zoom"].set_visibility(key == zoomed)
        win["star"].set_visibility(cur)

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
                # A query can never empty the wall - it dims, it never hides - so
                # only scope and needs-me can, and saying "no sessions yet"
                # while sessions plainly exist would be a lie.
                ui.label("nothing needs you right now — press ! to show every session"
                         if needs_me else
                         f"nothing under {scope} — clear the scope to see every session"
                         if scope else
                         "no sessions yet — record something with table-talk").classes("tt-none")
            for _ in buckets:
                columns.append(ui.element("div").classes("col"))
        for bucket, container in zip(buckets, columns):
            for key in bucket:
                windows[key]["el"].move(container, -1)
                dress(key, windows[key])

    def poll():
        nonlocal layout, on_wall
        states = {p.stem: fold_cached(p) for p in sorted(DATA_DIR.glob("*.jsonl"), reverse=True)}
        groups = M.group_sessions(list(states.items()))
        where = {s["key"]: (g["project"], s["index"]) for g in groups for s in g["sessions"]}
        opens = {s["key"]: s["summary"]["open_actions"] for g in groups for s in g["sessions"]}
        sort = store("sort", "recent")
        # the wall has no sort of its own: it reads in the drawer's order
        ordered = M.sort_groups(groups, sort)
        order = [s["key"] for g in ordered for s in g["sessions"]]
        apply_fold_rules(ordered)     # before the render, or a forced-open group draws folded
        render_drawer(drawer, ordered)

        for key in list(windows):              # a session file went away
            if key not in states:
                windows.pop(key)["el"].delete()
        for key in order:
            if key not in windows:
                with attic:
                    windows[key] = build_window(key, where[key][0])

        # Scope, needs-me and zoom choose what is on the wall - all three are
        # explicit, user-initiated choices about the view. The query never does:
        # the filter dims, it never hides, so an open action cannot leave the
        # wall. needs-me only ever drops windows with nothing open, so it cannot
        # remove an open action either.
        visible = [k for k in order if scope in (None, where[k][0])]
        if needs_me:
            visible = [k for k in visible if opens[k]]
        if zoomed in windows:
            visible = [zoomed]
        on_wall = visible
        drawer_open = store("drawer_open", True)
        cols = 1 if zoomed in windows else store("cols", 0) or default_cols(WALL_WIDTH)
        key = layout_key(visible, cols, marks, folds, zoomed, scope, sort, drawer_open)
        if key != layout:
            layout = key
            shell.classes(replace="tt-main" if drawer_open else "tt-main tt-collapsed")
            # a folded window is a titlebar: costing it its full content weight
            # would leave the packer balancing around height that is not drawn
            repack(visible, cols,
                   {k: 1 if k in folds else M.weight(v) for k, v in states.items()})

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
            # both advance without the window's own data changing: age with the
            # clock, the tmux index whenever a newer sibling session appears
            for el, txt in ((win["when"], ago(win["latest"]) if win["latest"] else ""),
                            (win["ix"], f":{where[k][1]}")):
                if el.text != txt:
                    el.set_text(txt)

        # The tally counts EVERY session, not just what is on the wall: scope and
        # zoom are choices about the view, and the tab title and the toast hang
        # off this element to tell you from another monitor that something needs
        # you. A number that shrinks because you zoomed would be a lie.
        tally.set_text(tally_text(sum(g["open_actions"] for g in groups),
                                  sum(g["open_tasks"] for g in groups)))
        scope_seg.set_visibility(scope is not None)
        if scope:
            scope_label.set_text(f"showing {scope} only")
        for n, b in col_buttons.items():     # the EFFECTIVE count: zoom forces 1
            b.classes(replace="sl-c on" if n == cols else "sl-c")
        # the only chip with a state worth showing: the others are momentary
        chips["needs-me"].classes(replace="sl-h on" if needs_me else "sl-h")

    spin_i = 0

    def tick():
        """One bad poll degrades the statusline instead of killing the timer.
        A frozen spinner beside a stale timestamp is the whole watch(1) idiom:
        liveness you read at a glance rather than an abstract pulse."""
        nonlocal spin_i
        try:
            poll()
        except Exception:
            # Catching before NiceGUI's timer does removes the only traceback
            # anyone gets. The frozen bar tells whoever is watching it; the log
            # is for whoever is not.
            logging.exception("poll failed")
            cadence.classes(replace="sl-s sl-stale")
            return
        spin_i = (spin_i + 1) % len(SPINNER)
        spin.set_text(SPINNER[spin_i])
        now = time.strftime("%H:%M:%S")
        last_stamp.set_text(now)
        clock.set_text(now)
        cadence.classes(replace="sl-s")

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
