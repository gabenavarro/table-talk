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


# Mirrors the header rollup into the tab title: "(N) table-talk" while actions are open.
# Client-side observer, so every tab stays correct with zero extra server traffic.
TAB_TITLE_JS = """<script>
document.addEventListener('DOMContentLoaded', () => {
  const sync = () => {
    const el = document.getElementById('tt-rollup');
    if (!el) return;
    const m = el.textContent.match(/^(\\d+) action/);
    document.title = (m ? `(${m[1]}) ` : '') + 'table-talk';
  };
  const wait = setInterval(() => {
    const el = document.getElementById('tt-rollup');
    if (el) {
      clearInterval(wait);
      new MutationObserver(sync).observe(el, {childList: true, characterData: true, subtree: true});
      sync();
    }
  }, 500);
});
</script>"""

# Toast when the open-action count rises: baseline is read before observing,
# so page loads and reconnects never fire a stale burst.
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
    const el = document.getElementById('tt-rollup');
    if (!el) return;
    clearInterval(wait);
    const read = () => { const m = el.textContent.match(/^(\\d+)/); return m ? +m[1] : 0; };
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

# '/' focuses the filter box unless the user is already typing somewhere.
HOTKEY_JS = """<script>
document.addEventListener('keydown', e => {
  if (e.key === '/' && !['INPUT', 'TEXTAREA'].includes(document.activeElement.tagName)) {
    e.preventDefault();
    const el = document.querySelector('.tt-search input');
    if (el) el.focus();
  }
});
</script>"""

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
THEME_ICONS = {"system": "brightness_auto", "light": "light_mode", "dark": "dark_mode"}

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


# ---- LEGACY -----------------------------------------------------------------
# Feeds the card/table DOM main() still builds. Task 9 replaces that DOM with the
# wall and deletes this block; it is kept only so the app runs at every commit.
_COLS = {"action": ["id", "background", "why", "rec"], "task": ["id", "what", "progress"],
         "term": ["term", "intuitive", "technical"], "done": ["id", "type", "summary"]}
# Header labels that differ from a bare capitalize — match the reply tables in SKILL.md.
_COL_LABELS = {"why": "Why it matters", "rec": "Recommendation"}


def _columns(typ):
    return [{"name": c, "label": _COL_LABELS.get(c, c.capitalize()), "field": c,
             "align": "left", "sortable": True} for c in _COLS[typ]]


def _card_data(state):
    """Everything one card renders; pure and comparable, so ticks can no-op on unchanged data."""
    return {"action": open_rows(state, "action"), "task": open_rows(state, "task"),
            "term": term_rows(state),
            "done": [{"id": e["id"], "type": e["type"],
                      "summary": e.get("background") or e.get("what", "")}
                     for e in done_rows(state)]}
# ---- end legacy -------------------------------------------------------------


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
    assert "tt-rollup" in TAB_TITLE_JS and "document.title" in TAB_TITLE_JS
    assert section_label("action", 3) == "🔴 Actions needed · 3"
    assert set(SECTION_TEXT) == {"action", "task", "term"}
    assert accent({"action": [1], "task": []}) == "act"
    assert accent({"action": [], "task": [1]}) == "job"
    assert accent({"action": [], "task": []}) == "ok"
    assert ago(0, now=30) == "just now" and ago(0, now=90) == "1m ago"
    assert ago(0, now=7200) == "2h ago" and ago(0, now=200000) == "2d ago"
    assert latest_ts({"a": {"ts": 5}, "b": {"ts": 9}, "c": {}}) == 9 and latest_ts({}) == 0
    assert "tt-search" in HOTKEY_JS and "preventDefault" in HOTKEY_JS
    assert "tt-toast" in TOAST_JS and "tt-toast" in css, "toast script and styles must pair"
    assert set(EMPTY_STATES) == {"action", "task", "term"}, "every section needs an empty state"
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
    print("ok")


# LEGACY, with _COLS/_card_data above: the card sections main() still builds.
_SECTIONS = (("🔴 Actions needed", "action", "id"),
             ("🔵 Background work", "task", "id"),
             ("📖 Glossary (cumulative)", "term", "term"))
SECTION_TEXT = {key: label for label, key, _ in _SECTIONS}
# (icon, message) shown in place of an empty table — calm, not "No data available".
EMPTY_STATES = {"action": ("check_circle", "no open actions"),
                "task": ("done_all", "no background work"),
                "term": ("menu_book", "no terms yet")}


def section_label(key, count):
    return f"{SECTION_TEXT[key]} · {count}"


def accent(data):
    """Card state colour as a token name: needs the user > working > settled."""
    return "act" if data["action"] else ("job" if data["task"] else "ok")


def latest_ts(state):
    return max((e.get("ts", 0) for e in state.values()), default=0)


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
    ui.add_head_html(TAB_TITLE_JS)
    ui.add_head_html(HOTKEY_JS)
    ui.add_head_html(TOAST_JS)
    ui.add_head_html(COPY_JS)
    dark = ui.dark_mode()
    mode = app.storage.general.get("theme", "system")
    if mode not in THEME_MODES:  # a hand-edited/stale storage value must not crash startup
        mode = "system"

    def apply_theme(m):
        {"system": dark.auto, "light": dark.disable, "dark": dark.enable}[m]()

    apply_theme(mode)

    with ui.header().classes("tt-header items-center"):
        ui.label("table-talk").classes("text-lg font-bold tt-title")
        ui.space()
        search = ui.input(placeholder="filter — press /").props(
            "clearable dense borderless debounce=100").classes("w-56 tt-search")
        pulse = ui.icon("circle", size="10px").classes("tt-pulse").style("opacity:0.35")
        rollup = ui.label("").props("id=tt-rollup aria-live=polite").classes("text-sm tt-rollup")

        def cycle():
            nonlocal mode
            mode = THEME_MODES[(THEME_MODES.index(mode) + 1) % len(THEME_MODES)]
            app.storage.general["theme"] = mode
            apply_theme(mode)
            btn.props(f'icon={THEME_ICONS[mode]}')

        btn = ui.button(on_click=cycle).props(f'flat round icon={THEME_ICONS[mode]}')
        with btn:
            ui.tooltip("theme: system → light → dark")

    cards = {}    # path -> {"tables": {key: ui.table}, "expansion": ui.expansion, "data": _card_data}
    built = None  # file list the DOM was last built for; None = never built

    def build_card(path, data, latest):
        with ui.card().classes("w-full").style(
                f"border-left:4px solid var(--{accent(data)})") as card_el:
            with ui.row().classes("items-baseline gap-2 w-full"):
                ui.label(path.stem).classes("text-base font-bold tt-session")
                meta = ui.label(ago(latest) if latest else "").classes("text-xs tt-age")
                with meta:
                    tip = ui.tooltip(stamp(latest) if latest else "")
            tables = {}
            labels = {}
            empties = {}
            for _, key, row_key in _SECTIONS:
                labels[key] = ui.label(section_label(key, len(data[key]))).classes("tt-sec")
                tables[key] = ui.table(columns=_columns(key), rows=data[key],
                                       row_key=row_key).props("dense flat").classes("w-full tt")
                search.bind_value_to(tables[key], "filter")
                with ui.row().classes("items-center gap-1 tt-clear") as empty:
                    ui.icon(EMPTY_STATES[key][0], size="18px")
                    ui.label(EMPTY_STATES[key][1])
                empties[key] = empty
                tables[key].set_visibility(bool(data[key]))
                empty.set_visibility(not data[key])
            exp = ui.expansion(f"{len(data['done'])} done").classes("w-full opacity-50")
            with exp:
                tables["done"] = ui.table(columns=_columns("done"), rows=data["done"],
                                          row_key="id").props("dense flat").classes("w-full tt")
                search.bind_value_to(tables["done"], "filter")
            exp.set_visibility(bool(data["done"]))
            # a filter match hidden in the collapsed done panel is invisible — open it while filtering
            search.bind_value_to(exp, "value", forward=bool)
        return {"el": card_el, "tables": tables, "labels": labels, "expansion": exp,
                "empties": empties, "data": data, "meta": meta, "tip": tip,
                "latest_ts": latest, "age_txt": None}

    container = ui.column().classes("w-full")

    flip = False

    def tick():
        nonlocal built, flip
        files = sorted(DATA_DIR.glob("*.jsonl"), reverse=True)
        if files != built:  # session file appeared/disappeared: rebuild structure (only case that resets scroll)
            container.clear()
            cards.clear()
            with container:
                if not files:
                    with ui.column().classes("items-center w-full m-8 opacity-60"):
                        ui.icon("inbox", size="3rem")
                        ui.label("no sessions yet").classes("text-lg")
                        ui.label("record something: table-talk action \"…\" --why \"…\" --rec \"…\"") \
                            .classes("text-sm tt-session")
                for p in files:
                    state = fold_cached(p)
                    cards[p] = build_card(p, _card_data(state), latest_ts(state))
            built = files
        else:
            for p, card in cards.items():  # data-only change: mutate rows in place so scroll and sort survive
                state = fold_cached(p)
                data = _card_data(state)
                if data == card["data"]:
                    continue
                for key, table in card["tables"].items():
                    if data[key] != card["data"][key]:
                        table.rows[:] = data[key]
                        table.update()
                        if key in card["labels"]:
                            card["labels"][key].set_text(section_label(key, len(data[key])))
                card["expansion"].set_text(f"{len(data['done'])} done")
                card["expansion"].set_visibility(bool(data["done"]))
                card["el"].style(f"border-left:4px solid var(--{accent(data)})")
                for key, empty in card["empties"].items():
                    card["tables"][key].set_visibility(bool(data[key]))
                    empty.set_visibility(not data[key])
                if (lt := latest_ts(state)) != card["latest_ts"]:
                    card["latest_ts"] = lt
                    card["tip"].set_text(stamp(lt))
                card["data"] = data
        for card in cards.values():  # age advances even when data does not; re-render only on text change
            txt = ago(card["latest_ts"]) if card["latest_ts"] else ""
            if card["age_txt"] != txt:
                card["age_txt"] = txt
                card["meta"].set_text(txt)
        n = sum(len(c["data"]["action"]) for c in cards.values())
        txt = (f"{n} action{'s' if n != 1 else ''} needed across "
               f"{len(cards)} session{'s' if len(cards) != 1 else ''}") if n else "all clear"
        if rollup.text != txt:
            rollup.set_text(txt)
            rollup.classes(replace="text-sm tt-rollup" + (" tt-rollup-hot" if n else ""))
        flip = not flip  # heartbeat: proves the poll is alive without re-rendering anything
        pulse.style(f"opacity:{0.9 if flip else 0.35}")

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
