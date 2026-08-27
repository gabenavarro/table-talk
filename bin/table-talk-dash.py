#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["nicegui>=3.16,<4"]
# ///
"""table-talk dashboard: live NiceGUI view of the table-talk event logs."""
import argparse
import json
import logging
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import tt_config
import tt_model as M
from tt_model import DATA_DIR, fold_cached

CSS_PATH = Path(__file__).resolve().parent / "tt.css"

# The loaded config, reachable from a row renderer and from a click handler that
# outlives the render. main() replaces it; threading it through six row
# signatures to reach one <button> buys nothing - the process has one config.
CFG = tt_config.DEFAULTS


def load_css():
    """The stylesheet lives beside the script so it can be edited as CSS.
    Read at startup; a missing file is a broken install, not a runtime path."""
    return CSS_PATH.read_text()


def theme_css(theme):
    """The config's palette as one stylesheet, holding only the tokens that
    DIFFER from tt.css's own. An unset token emits nothing and inherits - a
    restatement of the default is a second place to change it.

    :root carries the light palette and body.body--dark the dark one, exactly as
    tt.css orders them, so this block simply has to come after it.

    Every value is re-checked with valid_colour HERE, at the point of emission:
    load() validates on the way in, this validates on the way out, and a config
    file is a second untrusted route into the stylesheet. The comprehension runs
    over the DEFAULTS keys rather than the file's, so a token NAME out of the
    file is only ever matched, never interpolated.
    """
    def block(sel, mode):
        base = tt_config.DEFAULTS["theme"][mode]
        got = theme.get(mode) or {}
        decls = "".join(f"--{k}:{got[k]};" for k in base
                        if k in got and got[k] != base[k] and tt_config.valid_colour(got[k]))
        return f"{sel}{{{decls}}}" if decls else ""

    return block(":root", "light") + block("body.body--dark", "dark")


# Ids hand you the command: one delegated listener, no server round-trip.
COPY_JS = """<script>
document.addEventListener('click', e => {
  const b = e.target.closest && e.target.closest('[data-id]');
  if (!b) return;
  if (!/^[0-9a-f]{4,}$/.test(b.dataset.id || '')) return;  // it becomes a SHELL command
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


def scroll_js(key):
    """JS that scrolls one window into view.

    The session key crosses into JS as a json.dumps LITERAL and is compared
    there; no selector is ever built from it. Concatenated into a quoted
    selector it was executable - `--project "y'+alert(9)+'z"` ran alert(9) on an
    ordinary drawer-row click - and it broke benignly too: --project defaults to
    the cwd name, so a directory called don't-panic killed click-to-scroll.
    Escaping a selector is the wrong repair; not building one is the fix.
    """
    return (f'requestAnimationFrame(()=>{{const k={json.dumps(key)};'
            '[...document.querySelectorAll("[data-window]")]'
            '.find(e=>e.dataset.window===k)'
            '?.scrollIntoView({behavior:"smooth",block:"start"})})')


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

# A row that flashes while you are looking at your editor is a change you never
# saw, so the seen-watermark is advanced by INTERACTION - a click, a keypress or
# a scroll - and by nothing else.
#
# It used to be advanced by the Page Visibility API, which cannot carry this on
# its own: that API reports tab BACKGROUNDING only. It cannot see a covered
# window and it has no idea which monitor you are looking at. This dashboard's
# deployment is a second monitor that is permanently on screen, where
# document.hidden is therefore never true - measured there, a gutter appeared on
# one poll and was gone on the next (1.0s of an 8s untouched window at a 1s
# cadence), which is exactly the fleeting flash the gutter exists to replace.
# Interaction is the only signal that proves a human looked. Returning to a
# genuinely backgrounded tab stays as a second signal: it is real when it fires,
# it just never fires on the wall.
#
# Capture phase because a scroll inside the wall does not bubble to document.
# The throttle is because a scroll fires per frame and every call is one socket
# message for a mark that is idempotent. The retry is for the Vue root:
# emitEvent needs it mounted, and NiceGUI queues the message itself until the
# socket handshake.
SEEN_JS = """<script>
let ttLast = 0;
const ttSeen = () => {
  if (Date.now() - ttLast < 300) return;
  try { emitEvent('tt-seen', 1); ttLast = Date.now(); }
  catch (e) { setTimeout(ttSeen, 200); }
};
for (const t of ['click', 'keydown', 'scroll']) document.addEventListener(t, ttSeen, true);
document.addEventListener('visibilitychange', () => { if (!document.hidden) ttSeen(); });
</script>"""

# The wall's own width, announced by the page, because the server cannot read the
# viewport (see WALL_WIDTH). A ResizeObserver rather than a resize listener: it
# fires ONCE on observe, so the first width arrives without a resize ever
# happening, and it also catches the drawer collapsing, which changes the wall's
# width without changing the window's. The retry is for the wall element itself -
# NiceGUI has not mounted the Vue root when this script parses.
WIDTH_JS = """<script>
const ttWall = () => {
  const el = document.querySelector('.wall');
  if (!el) return setTimeout(ttWall, 200);
  new ResizeObserver(() => {
    try { emitEvent('tt-width', el.clientWidth); } catch (e) { /* not mounted yet */ }
  }).observe(el);
};
ttWall();
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
THEME_ICONS = {"system": "◐", "light": "○", "dark": "●"}

# tmux choose-tree guides. Kept as literal glyphs so the verticals connect in a
# fixed-width column rather than being faked with borders.
GUIDES = {"open": "▾", "closed": "▸", "mid": "├", "last": "└", "line": "│", "none": " "}

# Mermaid dressed as the app. The SPLIT is the point: colour comes from
# tt.css token overrides (they swap with the palette live - in "system" mode
# the server cannot know the client's prefers-color-scheme, so no baked
# colour can be right), but the FONT goes through the directive, because
# mermaid measures labels with its configured font and a CSS-only font swap
# overflows every box it drew. base is the theme built to be overridden.
# A per-render directive rather than initialize() config: it travels with
# each render, per diagram. securityLevel cannot be relaxed the same way -
# mermaid's directive sanitiser blocks it - so strict stays strict.
# The same sanitiser runs every themeVariables VALUE through
# ^[\d "#%(),.;A-Za-z]+$ - NO hyphen - and one bad character blanks the
# WHOLE value: with ui-monospace in this stack, fontFamily came out "",
# labels were measured in the page font and every box mis-sized. Verified
# against the bundled mermaid 11.16.1; the selftest pins hyphen-freedom.
MERMAID_INIT = ('%%{init: {"theme": "base", "themeVariables": {"fontFamily": '
                '"JetBrains Mono, Adwaita Mono, SF Mono, Menlo, Consolas, monospace", '
                '"fontSize": "12px"}}}%%\n')

MAX_CELLS = 20     # a long session must not wreck the footer line
BAR_CELLS = 14

# The width assumed until the page announces its own. The real viewport cannot be
# read from the server: the only hook that fires per client, app.on_connect, runs
# in a context where this page's elements are unreachable (verified against
# NiceGUI 3.16 - client.elements reads empty there and move() raises "the parent
# slot has been deleted"), and main()'s closure runs twice per process with only
# the second one live, so the connect handler cannot even reach the live tick.
# WIDTH_JS reports it from the client side instead, and cols 1|2|3 in the
# statusline stays the deliberate override.
WALL_WIDTH = 1400
# Below this the wall packs ONE column whatever the preference says: three columns
# of a 600px wall are 190px each, which is two or three words a line.
NARROW = 900


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


def diagram_rows(state):
    return sorted((e for e in state.values() if e.get("type") == "diagram"),
                  key=lambda e: e.get("ts", 0), reverse=True)


def changed_ids(state, since):
    """Ids of actions and tasks that moved after `since`. Terms are reference
    material - a glossary definition is not a change that needs your attention."""
    return {str(e["id"]) for e in state.values()
            if e.get("type") in ("action", "task") and e.get("ts", 0) > since}


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
    on public API; the delegated listener finds the button via closest().

    The prop is assigned, never .props(f'data-id={...}'): see build_window."""
    from nicegui import ui
    btn = ui.element("button").classes(f"id {cls}")
    btn.props["data-id"] = str(ev["id"])
    with btn:
        ui.label(str(ev["id"]))


def _marked(text, query, cls=None):
    """User text with the query highlighted. tt_model.marked escapes every chunk
    itself and is property-tested; it is the only thing allowed to reach ui.html."""
    from nicegui import ui
    el = ui.html(M.marked(text or "", (query or "").strip()))
    return el.classes(cls) if cls else el


def link_roots(cfg):
    """Where a path found in a log line is allowed to point: the data dir, the
    project dir the server was started in, and whatever links.extra_roots adds.

    Absolute, because path_spans resolves a relative token against the process
    CWD - the same CWD this list names, so the two agree by construction.
    Non-string entries are dropped rather than handed to Path(): extra_roots
    comes out of a TOML file and _merge only checks that the LIST is a list.
    """
    return [DATA_DIR.resolve(), Path.cwd(),
            *(Path(r).expanduser() for r in cfg["links"]["extra_roots"] if isinstance(r, str))]


def open_path(path, cfg, run=subprocess.Popen, extra_roots=()):
    """Open one path in the configured command, re-deriving confinement HERE.

    The string arrives from a click on markup built out of a log file, so it is
    put back through path_spans - the same gate the link was rendered through -
    and only a string that is exactly its own resolved, in-root self survives.
    Nothing about the click is trusted, so a payload that was tampered with is
    no different from a log line that never should have linked.

    extra_roots widens the check for ONE call without touching link_roots (and
    so without widening every other link on the wall): the drawer footer hands
    back the exact path it resolved for itself, never one out of a log line, so
    re-deriving confinement against that single path grants exactly that file
    and nothing beside it.

    The command is an argv LIST and never shell=True: that is the whole reason a
    file called 'a;b.md' stays a filename. A click that fails the check does
    nothing at all, and a command that is not installed is not a crash.
    """
    spans = M.path_spans(path, [*link_roots(cfg), *extra_roots])
    if len(spans) != 1 or spans[0][2] != path:
        return
    try:
        run([cfg["links"]["open_command"], path])
    except OSError as e:
        print(f"table-talk: could not open {path!r}: {e}", file=sys.stderr)


def nearest_claude_md(start, home):
    """The closest CLAUDE.md found walking up from `start`, or None.

    Never inspects anything above `home`: a dashboard started outside the
    user's own tree must not surface someone else's CLAUDE.md living further
    up the disk. Both paths are resolved before comparison, so a symlinked
    `start` that resolves outside home's tree is refused outright rather than
    walked - the same "resolve, then confine" rule path_spans uses.
    """
    cur = Path(start).resolve()
    home = Path(home).resolve()
    try:
        cur.relative_to(home)
    except ValueError:
        return None
    while True:
        hit = cur / "CLAUDE.md"
        if hit.is_file():
            return hit
        if cur == home:
            return None
        cur = cur.parent


def _cell(text, query, cls=None):
    """One cell of user text: the query highlighted, and any path resolving to a
    real file inside link_roots rendered as a button that opens it.

    One walk over both span lists rather than a second renderer - the text
    between paths goes through _marked exactly as before, so a cell carries a
    highlight and a link at once and every run is still escaped AFTER the split.
    The path reaches the DOM through the props dict, never a .props() string.

    The runs share one box because this cell's slot is a flex item in .ttl and a
    grid cell in .sub: loose siblings there become layout items and the sentence
    comes apart.
    """
    from nicegui import ui
    text = text or ""
    spans = M.path_spans(text, link_roots(CFG))
    if not spans:
        return _marked(text, query, cls)
    box = ui.element("span").classes(f"lk-p {cls}" if cls else "lk-p")
    with box:
        i = 0
        for start, end, resolved in spans:
            if start > i:
                _marked(text[i:start], query)
            btn = ui.element("button").classes("lk")
            btn.props["data-path"] = resolved
            btn.props["title"] = f"open {resolved}"
            with btn:
                _marked(text[start:end], query)
            btn.on("click", lambda _, p=resolved: open_path(p, CFG))
            i = end
        if i < len(text):
            _marked(text[i:], query)
    return box


def _action_row(ev, blink, query, changed):
    from nicegui import ui
    with ui.element("div").classes(("row changed" if changed else "row") + _dim(ev, query)):
        _id_button(ev, "id-act")
        with ui.element("div"):
            with ui.element("div").classes("ttl"):
                _cell(ev.get("background", ""), query)
                if blink:   # exactly one cursor on the page: the newest thing waiting on you
                    cur = ui.label("▉").classes("cursor")
                    cur.props["title"] = "newest action waiting on you"
            # no guide glyph: .sub draws the whole tree guide as one rule, because
            # a per-row ├/└ came apart the moment `why` wrapped past one line
            if ev.get("intuitive"):
                with ui.element("div").classes("sub"):
                    ui.label("int").classes("lb")
                    _cell(ev["intuitive"], query)
            for field in ("why", "rec"):
                with ui.element("div").classes("sub"):
                    ui.label(field).classes("lb")
                    _cell(ev.get(field, ""), query)
            _art_sub(ev)


def _task_row(ev, query, changed):
    from nicegui import ui
    with ui.element("div").classes(("row changed-job" if changed else "row") + _dim(ev, query)):
        _id_button(ev, "id-job")
        with ui.element("div"):
            _cell(ev.get("what", ""), query, "ttl")
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
                    _cell(text, query, "raw")
            if ev.get("intuitive"):
                with ui.element("div").classes("sub"):
                    ui.label("int").classes("lb")
                    _cell(ev["intuitive"], query)
            _art_sub(ev)


def _term_row(ev, query):
    from nicegui import ui
    with ui.element("div").classes("row" + _dim(ev, query)):
        ui.label(ev.get("term", "")).classes("id id-gls")
        with ui.element("div"):
            _cell(ev.get("intuitive", ""), query, "ttl")
            with ui.element("div").classes("sub"):
                ui.label("def").classes("lb")
                _cell(ev.get("technical", ""), query)


def _art_sub(ev):
    """The item's ASCII sketch, hung off the same tree guide as why/rec and
    emitted LAST so .sub:last-child's corner lands on it. Rendered as
    ui.label runs split by tt_model.art_spans - structure strokes in the
    faint ink, labels in the full ink - never as markup: the art comes out
    of a LOG FILE, and a label's text binding cannot become HTML."""
    art = ev.get("diagram")
    if not art:
        return
    from nicegui import ui
    with ui.element("div").classes("sub"):
        ui.label("art").classes("lb")
        with ui.element("div").classes("art"):
            for chunk, structure in M.art_spans(art):
                lbl = ui.label(chunk)
                if structure:
                    lbl.classes("st")


def _diagram_row(ev, query):
    """A recorded mermaid diagram. The source comes out of a LOG FILE, so it is
    rendered at securityLevel strict - the bundled default, restated here so a
    future config knob cannot silently relax it. A parse error draws mermaid's
    own error graphic client-side; the server never sees it."""
    from nicegui import ui
    with ui.element("div").classes("row" + _dim(ev, query)):
        ui.label(ev.get("title", "")).classes("id id-mag")
        with ui.element("div"):
            ui.mermaid(MERMAID_INIT + str(ev.get("mermaid", "")),
                       config={"securityLevel": "strict"}).classes("mmd")


def _done_row(ev, query):
    """A resolved action or task, dimmed. Keeps its id clickable so a mistaken
    'done' is easy to find again."""
    from nicegui import ui
    with ui.element("div").classes("row" + _dim(ev, query)):
        _id_button(ev, "id-ok")
        with ui.element("div"):
            _cell(ev.get("background") or ev.get("what", ""), query, "ttl")
            _art_sub(ev)


def _hits(evs, query):
    """True when a query is live and at least one of these rows matches it.
    Drives the #57 behaviour: a match inside a collapsed section is an invisible
    match, so a live query opens the section holding it."""
    return bool((query or "").strip()) and any(_dim(ev, query) == "" for ev in evs)


def bar_for(open_n, done_n):
    """A section's state as glyphs: █ per item still wanting attention, ░ per
    resolved one. Capped like the footer's cells so a long section cannot
    wreck the prompt line."""
    open_n, done_n = max(0, open_n), max(0, done_n)
    if open_n + done_n > MAX_CELLS:               # scale, never overflow
        total = open_n + done_n
        # never round an open item AWAY: a shut section reporting "nothing
        # wants you" while something does is the one lie this bar must not tell
        open_n = max(1, round(MAX_CELLS * open_n / total)) if open_n else 0
        done_n = MAX_CELLS - open_n
    return "█" * open_n, "░" * done_n


def _prompt(cls, title, count, toggles=None, opened=None, key="", force=False, bar=None):
    """A section header as a shell prompt line: '❯ actions --open (3)'.

    When `toggles` is given, clicking the line shows or hides that container —
    this is what makes every section collapsible. `opened` is a
    dict owned by the caller and outliving this render, so a section the user
    expanded is still expanded after the next poll rebuilds the body. `force`
    opens the section for this render only, without touching what the user chose.
    `bar` is a (filled, empty) glyph pair shown only while the section is shut.
    """
    from nicegui import ui
    shown = force or bool(opened and opened.get(key))
    with ui.element("div").classes(f"pr {cls}") as line:
        ui.label("❯").classes("g")
        ui.label(title)
        tail = "" if toggles is None else (" ▾" if shown else " ▸")
        caret = ui.label(f"({count}){tail}").classes("n")
        # Shown only while the section is shut: open, the rows themselves say it.
        bar_el = ui.element("div").classes("bar-box")
        if bar:
            with bar_el:
                ui.label(bar[0]).classes("bar")
                ui.label(bar[1]).classes("bar e")
    if toggles is None:
        return
    toggles.set_visibility(shown)
    bar_el.set_visibility(bool(bar) and not shown)

    def flip(_):
        # Toggle against the LIVE visibility. `force` and `shown` are constants
        # captured for this render and nothing rebuilds the body on a click, so
        # `not (force or opened[key])` evaluated `not True` on every click of a
        # filter-forced section: the first collapsed it and every one after was
        # dead. Reading the element is the only source of truth a click has.
        now = not toggles.visible
        opened[key] = now
        toggles.set_visibility(now)
        bar_el.set_visibility(bool(bar) and not now)
        caret.set_text(f"({count}) " + ("▾" if now else "▸"))

    line.on("click", flip)


def render_window_body(container, state, newest_action_id, query="", changed=(), collapsed=()):
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
    reader. It dies with the container, so nothing has to clean it up. Which
    ones START shut is ui.collapsed_sections, named the way the config names
    them - the user's choice from then on outranks the file.
    """
    from nicegui import ui
    opened = getattr(container, "tt_open", None)
    if opened is None:
        opened = container.tt_open = {"act": "actions" not in collapsed,
                                      "job": "jobs" not in collapsed,
                                      "dia": "diagrams" not in collapsed,
                                      "gls": "glossary" not in collapsed,
                                      "ok": "done" not in collapsed}
    container.clear()
    with container:
        acts = open_rows(state, "action")
        jobs = open_rows(state, "task")

        done = done_rows(state)
        done_a = sum(1 for e in done if e.get("type") == "action")

        acts_box = ui.element("div")
        with acts_box:
            if not acts:
                ui.label("nothing needs you").classes("empty")
            for ev in acts:
                _action_row(ev, str(ev["id"]) == newest_action_id, query,
                            str(ev["id"]) in changed)
        _prompt("p-act", "actions --open", len(acts), toggles=acts_box, opened=opened,
                key="act", force=_hits(acts, query), bar=bar_for(len(acts), done_a))
        acts_box.move(container, -1)

        jobs_box = ui.element("div")
        with jobs_box:
            if not jobs:
                ui.label("nothing running").classes("empty")
            for ev in jobs:
                _task_row(ev, query, str(ev["id"]) in changed)
        _prompt("p-job", "jobs", len(jobs), toggles=jobs_box, opened=opened,
                key="job", force=_hits(jobs, query),
                bar=bar_for(len(jobs), len(done) - done_a))
        jobs_box.move(container, -1)

        # Diagrams are reference material like the glossary, but they exist to
        # be LOOKED at - the terminal cannot render mermaid, the reply points
        # the user here - so they start open unless the config folds them, and
        # the header only exists when a diagram does: an empty always-there
        # section is noise (same rule as the drawer's context footer).
        dias = diagram_rows(state)
        if dias:
            dia_box = ui.element("div")
            with dia_box:
                for ev in dias:
                    _diagram_row(ev, query)
            _prompt("p-mag", "diagrams", len(dias), toggles=dia_box,
                    opened=opened, key="dia", force=_hits(dias, query),
                    bar=bar_for(0, len(dias)))
            dia_box.move(container, -1)

        # Every section collapses; ui.collapsed_sections decides which start
        # shut, and a live query force-opens one holding a match (#57). Each
        # box is built before the prompt that toggles it, then moved back
        # under it with move(container, -1).
        terms = term_rows(state)
        gls_box = ui.element("div")
        with gls_box:
            for ev in terms:
                _term_row(ev, query)
        _prompt("p-gls", "glossary", len(terms), toggles=gls_box,
                opened=opened, key="gls", force=_hits(terms, query),
                bar=bar_for(0, len(terms)))
        gls_box.move(container, -1)

        done_box = ui.element("div")
        with done_box:
            for ev in done:
                _done_row(ev, query)
        _prompt("p-ok", "done", len(done), toggles=done_box,
                opened=opened, key="ok", force=_hits(done, query),
                bar=bar_for(0, len(done)))
        done_box.move(container, -1)


def abbrev(project):
    """Three-letter tag for the collapsed rail."""
    return project[:3] if project else "?"


def session_label(state, index):
    """What follows the colon in a window title: the code of the session that
    wrote this file last, or the tmux index for files recorded before stamping."""
    best, code = -1, ""
    for ev in state.values():
        if ev.get("sid") and ev.get("ts", 0) > best:
            best, code = ev["ts"], str(ev["sid"])
    return code or str(index)


def default_cols(width):
    """Column count before the user picks one. Three on a wide second monitor."""
    return 3 if width >= 1800 else (2 if width >= 1200 else 1)


def cols_for(width, pref):
    """How many columns a wall this wide gets. The stored preference is a MAXIMUM,
    never a mandate: a window narrowed to a laptop half-screen packs one column
    even with 3 chosen, and gets its 3 back when the window grows. pref 0 is
    'auto', which is what `or` reads it as."""
    return 1 if width < NARROW else (pref or default_cols(width))


def layout_key(visible, cols, marks, folds, zoomed, scope, sort, drawer_open):
    """Everything that changes WHERE a window sits. The wall re-packs when this
    changes and at no other time - never on a poll that only changed text."""
    return (tuple(visible), cols, tuple(sorted(marks)), tuple(sorted(folds)),
            zoomed, scope, sort, drawer_open)


def selftest():
    import tempfile
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
    dst = dict(st, f={"id": "f", "type": "diagram", "title": "Arch",
                      "mermaid": "flowchart LR", "ts": 6},
               g={"id": "g", "type": "diagram", "title": "Flow",
                  "mermaid": "sequenceDiagram", "ts": 7})
    assert [r["id"] for r in diagram_rows(dst)] == ["g", "f"], "diagrams read newest first"
    assert diagram_rows(st) == [], "no diagram events, no rows"
    assert [r["id"] for r in done_rows(dst)] == ["b"], \
        "a diagram is never an obligation: done still spans actions and tasks only"
    st_sid = {"a": {"id": "a", "sid": "beef", "ts": 5},
              "b": {"id": "b", "sid": "cafe", "ts": 9}}
    assert session_label(st_sid, 3) == "cafe", "the session that wrote LAST names the window"
    assert session_label({"a": {"id": "a", "ts": 1}}, 3) == "3", \
        "a file recorded before session stamping keeps its tmux index"
    assert session_label({}, 0) == "0"
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
    # light's caret is claude-code-light's #d97757 darkened to clear 4.5:1 on --hover
    assert "--caret:#8ec07c" in css and "--caret:#c4512c" in css, "cursor colour per theme"
    assert "--act:#fb4934" in css and "--act:#a53a2e" in css
    assert "body.body--dark" in css, "dark palette must key off Quasar's body--dark"
    assert "tabular-nums" in css, "digit columns must align"
    assert "prefers-reduced-motion" in css, "motion must be defeatable"
    assert "ui-monospace" in css and "system-ui" in css, "both faces need a real fallback stack"
    # The symbol fallbacks: JetBrains Mono lacks braille/▰▱/◐/☀/☾, and unlisted
    # those fall into SYSTEM fallback - ☾ drew a Tibetan script font, and ☀
    # (Unicode Emoji property) drew the color emoji font. An author-listed
    # family beats the browser's implicit emoji fallback, so the stack itself
    # must carry the coverage.
    for face in ("--mono", "--prose"):
        decl = css.split(face + ":")[1].split(";")[0]
        assert '"Noto Sans Symbols 2"' in decl and '"Noto Sans Symbols"' in decl, \
            f"{face} must list the symbol fallbacks, or ☀ falls to the color " \
            "emoji font and ☾ to whatever fontconfig finds first"
    mono_decl = css.split("--mono:")[1].split(";")[0]
    assert mono_decl.index('"JetBrains Mono"') < mono_decl.index('"Adwaita Mono"') \
        < mono_decl.index('"Noto Sans Symbols 2"'), \
        "fallback order is coverage order: the primary face first, then the " \
        "MONO-metric symbol source (spinner and bars sit in mono columns), " \
        "then the proportional Noto pair for what is still missing (☾)"
    assert "--ctp-" not in css, "the Catppuccin palette is gone"
    # hover moves AWAY from the text, which is a different direction per theme
    assert "--hover:#ffffff" in css and "--hover:#191b1c" in css, \
        "both palettes need a --hover, and light lightens where dark darkens"
    for rule in (".dw-row:hover", ".rail-item:hover", ".win-b .row:hover", ".dw-fold:hover"):
        decl = css.split(rule + "{")[1].split("}")[0]
        assert "var(--sel)" not in decl and "var(--hover)" in decl, \
            f"{rule} must use --hover: --sel is the SELECTED colour and in dark it " \
            "LIGHTENS the row you are reading, which is the whole bug"
    sub = css.split(".sub{")[1].split("}")[0]
    row = css.split(".win-b .row{")[1].split("}")[0]
    assert "overflow-wrap:anywhere" in row, \
        "a prose cell must break a long path rather than push the card wider than the wall"
    assert "nowrap" not in row and "nowrap" not in sub, \
        "nothing in a prose cell may refuse to wrap"
    assert ".win-b .row>*{min-width:0}" in css, \
        "a grid item's automatic minimum is min-content, which overflows the track"
    assert ".sub::before" in css and ".sub:last-child::before" in css, \
        "the tree guide is one continuous RULE with a corner on the last sub-line: " \
        "drawn as a ├/└ glyph per row it broke open the moment `why` wrapped"
    assert "position:relative" in sub, "the guide is absolutely positioned against .sub"
    gap = sub.split("margin-top:")[1].split("px")[0]
    bridge = css.split(".sub::before{")[1].split("}")[0].split("bottom:-")[1].split("px")[0]
    assert gap == bridge == "7", \
        "the guide bridges the gap with a negative bottom: move the margin " \
        "without moving the bridge and the rule breaks between sub-rows"
    assert set(THEME_ICONS) == set(THEME_MODES)
    assert THEME_ICONS == {"system": "◐", "light": "○", "dark": "●"}, \
        "one geometric family the PRIMARY face carries: ☀ and ☾ are absent " \
        "from JetBrains Mono and each drew from a different fallback"
    assert "font-size:15px" in css.split(".dw-theme{")[1].split("}")[0], \
        "a 12px control at --ink-3 is the complaint; it is a real button"

    # theme_css: the config is a second untrusted route into the stylesheet, and
    # the only pure function on that route. Every value below is one a TOML file
    # can carry - the emitter is not allowed to trust that load() cleaned it.
    assert theme_css(tt_config.DEFAULTS["theme"]) == "", \
        "a palette equal to the stylesheet's emits NOTHING: inherit, never restate"
    assert theme_css({}) == "" and theme_css({"dark": {}, "light": {}}) == ""
    assert theme_css({"dark": {"bg": "#ff00ff"}}) == "body.body--dark{--bg:#ff00ff;}", \
        "body.body--dark carries the dark palette, exactly as tt.css orders it"
    assert theme_css({"light": {"bg": "#ff00ff"}}) == ":root{--bg:#ff00ff;}", \
        ":root carries the LIGHT palette - tt.css puts light in :root, not dark"
    assert theme_css({"light": {"bg": "#ff00ff"}, "dark": {"ink": "#010203"}}) == \
        ":root{--bg:#ff00ff;}body.body--dark{--ink:#010203;}", "light first, then dark"
    assert theme_css({"dark": {"bg": tt_config.DEFAULTS["theme"]["dark"]["bg"],
                               "ink": "#010203"}}) == "body.body--dark{--ink:#010203;}", \
        "only the tokens that DIFFER are emitted; a matching sibling emits nothing"
    for hostile in ("#fff;}body{display:none", "red", "", "url(x)", None, 0, ["#fff"]):
        assert theme_css({"dark": {"bg": hostile}}) == "", \
            f"{hostile!r} is not a hex colour and must never reach the stylesheet"
    assert theme_css({"dark": {"x;}body{display:none": "#ff00ff"}}) == "", \
        "a token NAME out of the file is matched against DEFAULTS, never interpolated"
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "c.toml"
        p.write_text("[server]\nport = 9\n")
        assert theme_css(tt_config.load(p)["theme"]) == "", \
            "a config with no [theme] section emits no override block at all"
        p.write_text('[theme.dark]\nbg = "#ff00ff"\n')
        assert theme_css(tt_config.load(p)["theme"]) == "body.body--dark{--bg:#ff00ff;}"
        p.write_text('[theme.dark]\nbg = "#fff;}body{display:none"\n')
        assert theme_css(tt_config.load(p)["theme"]) == "", \
            "an invalid colour is dropped on the way in AND on the way out"
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
    assert bar_for(3, 2) == ("███", "░░")
    assert bar_for(0, 0) == ("", "")
    f, e = bar_for(30, 30)
    assert len(f) + len(e) == MAX_CELLS, "a long section is scaled, never overflowed"
    assert bar_for(-1, 0) == ("", ""), "a negative count draws nothing"
    assert bar_for(1, 100) == ("█", "░" * (MAX_CELLS - 1)), \
        "one open item among a hundred resolved still shows: rounding it away " \
        "makes the bar say nothing needs you while something does"
    assert "data-id" in COPY_JS and "clipboard" in COPY_JS
    assert "[0-9a-f]{4,}" in COPY_JS, \
        "what COPY_JS builds is a SHELL COMMAND the user pastes, so the id it " \
        "splices must look like a minted id (secrets.token_hex) and nothing else"
    assert ".tt-dim" in css and ".tt-hit" in css, "dim and highlight need styles to mean anything"
    assert ".dw-find" in css and ".tt-none" in css, "the filter bar and empty wall need styles"
    assert default_cols(2000) == 3 and default_cols(1400) == 2 and default_cols(800) == 1
    assert cols_for(700, 3) == 1 and cols_for(899, 3) == 1, \
        "the stored cols is a MAXIMUM: a narrow wall packs one column whatever it says"
    assert cols_for(1280, 3) == 3 and cols_for(1920, 3) == 3, \
        "and the preference comes straight back when the window is wide again"
    assert cols_for(2000, 0) == 3 and cols_for(1300, 0) == 2 and cols_for(700, 0) == 1, \
        "0 is auto and still clamps"
    assert "ResizeObserver" in WIDTH_JS and "emitEvent('tt-width'" in WIDTH_JS, \
        "the wall's width can only come from the client; the server cannot read it"
    assert "clientWidth" in WIDTH_JS, \
        "the WALL's width, not the window's: collapsing the drawer changes one and not the other"
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
    assert ".win-b .row:has(.id-gls)" in css and ".id-gls{min-width:0" in css, \
        "a glossary term shares the row grid with 4-hex ids: without its own wider " \
        "column AND min-width:0, a real term ('reverse complement') spills out of " \
        "the 42px id track and overprints the definition beside it"
    assert "closest('button')" in BLUR_JS, \
        "keyboard.js ignores keys while a button holds focus; clicks must blur"
    assert "e.detail" in BLUR_JS, \
        "Enter/Space on a button synthesise a click with detail 0; blurring that " \
        "throws a keyboard user's focus ring to <body> after every activation"
    ch = {"a": {"id": "a", "type": "action", "ts": 100},
          "b": {"id": "b", "type": "task", "ts": 200},
          "c": {"id": "c", "type": "term", "ts": 300}}
    assert changed_ids(ch, 150) == {"b"}, "terms never carry a change gutter"
    assert changed_ids(ch, 0) == {"a", "b"}
    assert changed_ids(ch, 999) == set(), "a fresh watermark marks nothing"
    assert changed_ids({}, 0) == set()
    assert changed_ids({"x": {"id": "x", "type": "action"}}, 0) == set(), \
        "a row with no ts predates every watermark; it must not mark itself"
    # scroll_js: the key is DATA. Balance is asserted because an unbalanced
    # payload is a silent no-op - the browser throws a SyntaxError nobody sees
    # and click-to-scroll simply stops working (measured: one stray brace).
    js = scroll_js("y'+alert(9)+'z")
    assert js.count("{") == js.count("}") and js.count("(") == js.count(")"), \
        f"unbalanced JS payload: {js}"
    assert '[data-window="' not in js, "a selector must never be built from the key"
    assert json.loads(js.split("const k=")[1].split(";")[0]) == "y'+alert(9)+'z", \
        "the key round-trips as a JS string literal, quotes and all"
    for hostile in ('a"b', "it's", "a\"'b", "back\\slash", "</script>"):
        assert json.loads(scroll_js(hostile).split("const k=")[1].split(";")[0]) == hostile
    assert "emitEvent" in SEEN_JS
    for ev in ("click", "keydown", "scroll"):
        assert f"'{ev}'" in SEEN_JS, \
            f"the gutter clears on INTERACTION: without a {ev} listener this " \
            "dashboard - permanently visible on a second monitor, where " \
            "document.hidden is never true - marks rows seen that nobody looked at"
    assert "visibilitychange" in SEEN_JS, \
        "returning to a backgrounded tab stays a second signal, not the only one"
    assert ".win-b .row.changed{" in css and ".win-b .row.changed-job{" in css, \
        "gutter colours must differ by kind, and stay scoped off Quasar's .row"
    cur = css.split(".win.cur>.win-t{")[1].split("}")[0]
    assert "color-mix" in cur and "var(--sel)" in cur and "inset 0 -2px 0 var(--caret)" in cur, \
        "the current titlebar TINTS toward --sel and keeps the caret underline: " \
        "--sel outright is ~2.9x the luminance the glyphs were chosen against " \
        "and drops the ! bell to 1.89:1, and target() marks a window .cur on the " \
        "very first paint, so nobody has to click anything to hit it"

    # links: the click handler hands a string that came out of a LOG FILE to a
    # process launcher, so both halves of that are pinned here.
    assert link_roots({"links": {"extra_roots": []}}) == [DATA_DIR.resolve(), Path.cwd()], \
        "the roots are the data dir and the cwd project dir, in that order"
    assert link_roots({"links": {"extra_roots": ["/srv/x"]}})[2:] == [Path("/srv/x")], \
        "links.extra_roots is appended to the two implicit roots"
    assert link_roots({"links": {"extra_roots": [1, None, {"a": 1}, "/srv/x"]}})[2:] == \
        [Path("/srv/x")], "a non-string extra root is dropped, never handed to Path()"
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "proj"
        root.mkdir()
        real = root / "notes.md"
        real.write_text("x")
        evil = root / "a;b&`id`.md"       # a filename, and it must stay one
        evil.write_text("x")
        outside = Path(td) / "secret.md"
        outside.write_text("x")
        cfg = {"links": {"open_command": "ed", "extra_roots": [str(root)]}}
        argv = []

        def run(cmd, **kw):
            assert isinstance(cmd, list), f"the command must be an argv LIST, got {type(cmd)}"
            assert kw == {}, f"no shell=True, ever: {kw}"
            argv.append(cmd)

        open_path(str(real), cfg, run)
        assert argv == [["ed", str(real)]], "an in-root file opens with a two-element argv"
        argv.clear()
        open_path(str(evil), cfg, run)
        assert argv == [["ed", str(evil)]] and argv[0][1] == str(evil), \
            "a shell metacharacter in a FILENAME reaches argv as one intact element"
        argv.clear()
        for hostile in (str(outside), str(root / "missing.md"), str(root), "/etc/passwd",
                        f"{real} ; rm -rf /", f"x{real}", f"{real}\x00", "", None,
                        str(root / ".." / outside.name)):
            open_path(hostile, cfg, run)
            assert argv == [], f"{hostile!r} must never reach the launcher"
        open_path(str(real), {"links": {"open_command": "ed", "extra_roots": []}}, run)
        assert argv == [], "with the root gone from the config, the same path is refused"

        def boom(cmd, **kw):
            raise FileNotFoundError(cmd[0])

        open_path(str(real), cfg, boom)   # an open_command that is not installed is not a crash
    assert ".lk-p" in css and ".lk{" in css, \
        "a link run needs its styles, and the inline rule is what keeps a cell one sentence"

    # nearest_claude_md: the drawer-footer discovery helper
    with tempfile.TemporaryDirectory() as td:
        above = Path(td)
        home = above / "home"
        proj = home / "a" / "proj"
        sub = proj / "sub"
        sub.mkdir(parents=True)
        (above / "CLAUDE.md").write_text("above home")
        assert nearest_claude_md(sub, home) is None, \
            "nothing under home has one yet; the one ABOVE home must not be found"
        (home / "CLAUDE.md").write_text("home")
        assert nearest_claude_md(sub, home) == home / "CLAUDE.md", \
            "walks all the way up to home when nothing closer exists"
        (proj / "CLAUDE.md").write_text("proj")
        assert nearest_claude_md(sub, home) == proj / "CLAUDE.md", \
            "the nearest one wins once something closer than home exists"
        assert nearest_claude_md(proj, home) == proj / "CLAUDE.md", \
            "the starting directory itself containing one is found immediately"
        outside = above / "outside"
        outside.mkdir()
        (outside / "CLAUDE.md").write_text("outside")
        link = proj / "link"
        link.symlink_to(outside)
        assert nearest_claude_md(link, home) is None, \
            "a symlink whose target resolves outside home's tree is not followed"

    # Everything below reads this file as text: these are properties of the
    # SOURCE, and every one of them is a bug that shipped once already.
    #
    # `code` is this file MINUS this function, and the string checks read it
    # rather than src. Every needle below also appears here in the assertion
    # that carries it, so searching the whole file makes each one satisfy
    # itself: mutation-tested, all five stayed green with their fixes reverted -
    # including on_focus rebuilt as a raw [data-window="{key}"] selector, which
    # is the B2 exploit. An assertion that cannot fail is not a test.
    import ast
    src = Path(__file__).read_text()
    code = src.split("def selftest():")[0] + src.split("\ndef ago(", 1)[1]
    dyn = [n.lineno for n in ast.walk(ast.parse(src))
           if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
           and n.func.attr == "props"
           and any(not isinstance(a, ast.Constant)
                   for a in [*n.args, *(k.value for k in n.keywords)])]
    assert not dyn, (
        f"a computed value reaches the .props() STRING parser at line(s) {dyn}. "
        "That string is parsed (PROPS_PATTERN splits on whitespace outside "
        "quotes), so a value carrying a double quote closes the attribute and "
        "opens new ones: --project 'p\" onmouseover=\"alert(1)' put a real "
        "handler on the window. Assign it instead: el.props['data-window'] = key")
    assert 'el.props["data-window"] = key' in code, \
        "the window's data-window prop must be ASSIGNED - without it the key " \
        "never reaches the DOM and click-to-scroll has nothing to find"
    assert 'btn.props["data-id"] = str(ev["id"])' in code, \
        "the id button's data-id prop must be ASSIGNED; COPY_JS reads it"
    assert 'btn.props["data-path"] = resolved' in code, \
        "the link button's path must be ASSIGNED too - a .props() string is parsed"
    assert 'cur.props["title"]' in code and "newest action waiting on you" in code, \
        "the cursor glyph must explain itself: a green blinking box with no " \
        "tooltip reads as a rendering artifact (it was circled in a bug report)"
    sh = [n.lineno for n in ast.walk(ast.parse(src))
          if isinstance(n, ast.Call) and any(k.arg == "shell" for k in n.keywords)]
    assert not sh, (
        f"a shell= keyword at line(s) {sh}. The open command is handed a path "
        "that came out of a log file: the argv list is the whole defence, and "
        "prose in a docstring cannot satisfy this check")
    assert code.count("ui.html(") == 1, \
        "exactly one ui.html call, fed only by tt_model.marked: a link is built out " \
        "of ui.label runs and a real <button>, never out of markup"
    assert "ui.run_javascript(scroll_js(key))" in code, \
        "on_focus must go through scroll_js, which hands the key to JS as a " \
        "json.dumps LITERAL. Spliced into a selector, --project \"y'+alert(9)+'z\" " \
        "ran on an ordinary drawer click - and don't-panic broke the scroll"
    assert code.index("paint_window(win, k, states[k]") < code.index('win["sig"] = sig'), \
        "the paint signature is recorded AFTER the paint: assigned first, one " \
        "exception mid-build marks a half-drawn window current forever"
    assert code.index('srow.on("click"') < code.index("container.tt_sig = sig"), \
        "same for the drawer: its signature is recorded after the tree is built"
    assert code.index("<style>{load_css()}</style>") < code.index('theme_css(cfg["theme"])'), \
        "the config's palette must be added AFTER tt.css: same specificity, so " \
        "the later block is the only reason it wins"
    assert 'port = cfg["server"]["port"] if port is None else port' in code, \
        "the config is the DEFAULT port; an explicit --port still wins"
    assert '"gls": "glossary" not in collapsed' in code and '"ok": "done" not in collapsed' in code, \
        "ui.collapsed_sections names sections the way the config does; the two " \
        "internal keys are gls and ok"
    assert "not toggles.visible" in code, \
        "_prompt's flip must toggle against the LIVE visibility - `force` and " \
        "`shown` are constants captured for the render, so a filter-forced " \
        "section evaluated `not True` on every click and went dead after one"
    assert code.count("bar_el.set_visibility") == 2, \
        "the bar tracks the section: set at render AND flipped on click, or a " \
        "section you open keeps a bar that contradicts the rows below it"
    assert '"securityLevel": "strict"' in code, \
        "mermaid source comes out of a LOG FILE; strict is what keeps its " \
        "labels sanitized - loose would execute whatever the log carries"
    assert '%%{init:' in code and '"theme": "base"' in code and '"fontFamily"' in code, \
        "mermaid dresses as the app: base is the theme built to be overridden, " \
        "and the FONT must go through the directive because mermaid measures " \
        "labels with its configured font - a CSS-only font swap overflows boxes"
    assert '"theme": "neutral"' not in code and "background:#fff" not in css, \
        "the white neutral card is gone: the sheet's token overrides now theme " \
        "the diagram, and they swap with the palette where a baked ground cannot"
    assert ".mmd .node rect" in css and "var(--surface-2)!important" in css, \
        "mermaid inlines its own stylesheet per SVG; only author !important " \
        "rules keyed on the THEME TOKENS re-dress it and follow light/dark live"
    assert ".mmd .edgeLabel" in css and ".mmd .marker" in css and ".mmd rect.actor" in css, \
        "edges, edge labels, arrowheads and sequence actors are the pieces that " \
        "stayed default-grey when only the nodes were themed - and .actor must " \
        "be RECT-scoped: the class also sits on the name text"
    assert ".mmd text.actor>tspan" in css and ".mmd .noteText>tspan" in css, \
        "mermaid fills these tspans DIRECTLY (#333); only a direct rule beats " \
        "a direct rule - near-black names on a dark actor box otherwise"
    # '.mmd p' alone would also match '.mmd path', a legitimate future selector
    for blanket in (".mmd text{", ".mmd text,", ".mmd span", ".mmd p{", ".mmd p,"):
        assert blanket not in css, \
            f"no blanket text rule ({blanket}): base's fills are cream, and " \
            "forcing --ink onto an untouched diagram type is ink on cream in dark"
    assert ".mmd .note," in css and ".mmd .labelBox" in css, \
        "every ground a tspan rule paints on must be themed too - base's note " \
        "is #fff5ad and its labelBox #fff4dd, which is cream on cream in dark"
    assert "-" not in MERMAID_INIT, \
        "mermaid's directive sanitiser allows ^[\\d \"#%(),.;A-Za-z]+$ per " \
        "themeVariables value - NO hyphen. ui-monospace blanked the whole " \
        "fontFamily: measured in the page font, drawn in mono, every box wrong"
    assert '"dia": "diagrams" not in collapsed' in code, \
        "diagrams exist to be LOOKED at - the reply points the user here - so " \
        "unlike glossary/done they start open unless the config folds them"
    for key in ('"act": "actions" not in collapsed', '"job": "jobs" not in collapsed'):
        assert key in code, \
            f"{key}: every section collapses, and which ones START shut is the " \
            "config's call, named the way the config names them"
    assert '("action", "task", "term", "diagram")' in code, \
        "the statusline tally must count diagram rows: the filter highlights " \
        "them, and '0/N rows match' beside a visibly matching row is a lie"
    assert "M.art_spans" in code, \
        "art is split by the property-tested model classifier and rendered as " \
        "label runs - never raw, never markup: it comes out of a LOG FILE"
    assert code.index('for field in ("why", "rec")') < code.index("_art_sub(ev)"), \
        "the sketch is the LAST guided sub-row: .sub:last-child draws the " \
        "tree corner, and a bare div after the subs would strand it mid-air"
    assert code.count("_art_sub(ev)") == 4, \
        "actions, jobs AND done rows draw the sketch: a resolved item is when " \
        "the picture becomes reference, so dropping it there is backwards"
    assert code.count('ui.label("int").classes("lb")') == 2, \
        "both actions and jobs hang the plain-English line off the guide, " \
        "labeled the way the --intuitive flag is spelled"
    assert code.count('theme_btn.props["title"]') == 2, \
        "the theme toggle names its mode in a tooltip, and CYCLING must " \
        "refresh it: set once at build and once in cycle_theme, or the " \
        "tooltip lies about the mode after the first click"
    assert code.index('ui.label("int")') < code.index('for field in ("why", "rec")'), \
        "int reads FIRST: it is the line for someone with no context, and " \
        "why/rec argue a decision that line has to set up"
    assert ".pr .bar" in css and ".pr .bar.e" in css, \
        "a collapsed section still has to report itself: █ per item wanting " \
        "attention, ░ per resolved one, in the section's own colour"
    assert ".p-mag" in css and ".id-mag" in css and ".mmd" in css, \
        "the diagrams section needs its prompt, title-cell and body styles"
    assert ".win-b .row:has(.id-mag)" in css, \
        "a diagram title shares the row grid with 4-hex ids: without its own " \
        "wider column it overflows the 42px id track (same bug .id-gls had)"
    art = css.split(".art{")[1].split("}")[0]
    assert "white-space:pre" in art and "justify-self:center" in art, \
        "a sketch keeps its own geometry (the row's overflow-wrap:anywhere " \
        "must not fold a box border) and sits CENTERED in the card - the spec"
    assert "max-width:100%" in art and "overflow-x:auto" in art and "min-width:0" in art, \
        "art wider than the narrowest card scrolls inside its own box - it " \
        "must never widen the card"
    assert "var(--mono)" in art, \
        ".sub switched to the prose face; art must restate mono or misalign"
    assert ".art>*{display:inline}" in css, \
        "every glyph the renderer emits is a div - the .blocks trap again"
    assert ".art .st" in css, \
        "structure strokes recede to the faint ink so the labels read first"
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


def main(port=None):
    from nicegui import app, ui

    # One dict, read where it is needed. A missing or malformed file yields the
    # defaults, so nothing below has to think about that.
    global CFG
    cfg = CFG = tt_config.load()
    port = cfg["server"]["port"] if port is None else port   # an explicit --port wins
    poll_seconds = cfg["server"]["poll_seconds"]

    ui.add_head_html(f"<style>{load_css()}</style>")
    if (over := theme_css(cfg["theme"])):     # AFTER tt.css, or it overrides nothing
        ui.add_head_html(f"<style>{over}</style>")
    ui.add_head_html(COPY_JS)
    ui.add_head_html(TAB_TITLE_JS)
    ui.add_head_html(TOAST_JS)
    ui.add_head_html(BLUR_JS)
    ui.add_head_html(SEEN_JS)
    ui.add_head_html(WIDTH_JS)
    # the app shell owns the viewport; NiceGUI's page wrapper must not pad it
    ui.query(".nicegui-content").style("padding:0;gap:0;max-width:none")

    def store(key, default):
        """Persisted UI state. Loopback, one user, so server-wide storage is
        correct here and keeps two tabs in agreement. No storage_secret needed."""
        return app.storage.general.get(f"tt.{key}", default)

    def put(key, value):
        app.storage.general[f"tt.{key}"] = value

    dark = ui.dark_mode()
    mode = store("theme", cfg["theme"]["default"])
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
    # Everything newer than a window's watermark carries a gutter. Per WINDOW,
    # not one for the page: a window off the wall (zoom, scope, needs-me) is one
    # you could not have seen, so its watermark stands still until it is back on
    # the wall AND you have touched the page since. The floor is NOW, which is what makes a
    # freshly opened dashboard quiet - a change you were never here for is not a
    # change you missed. Deliberately not persisted: the watermark means "since
    # you last looked", and a reload is you looking.
    opened_ts = time.time()
    seen_at = {}                       # session key -> when its rows were last on screen
    # Not shared between tabs: NiceGUI gives each client its own run of this
    # function, so two dashboards keep independent watermarks (measured - of two
    # tabs open at once, the one that was clicked cleared its own gutter and the
    # untouched one kept its).
    touched = False                    # SEEN_JS saw a click/keypress/scroll; poll() consumes it
    wall_width = WALL_WIDTH            # until WIDTH_JS says otherwise

    def on_width(e):
        """The wall's width, straight off the client. Deliberately NOT persisted:
        it is a property of this window, not a preference, and two tabs at
        different widths must not overwrite each other's column count.
        A tick only when the CLAMP flips - a ResizeObserver fires on every frame
        of a drag, and re-polling every file for a width that changes nothing
        about the layout is the one thing this must not do."""
        nonlocal wall_width
        try:
            w = int(float(str(e.args).strip("[]")))
        except (TypeError, ValueError):        # a hand-crafted event, not the page
            return
        was, wall_width = wall_width, w
        if (w < NARROW) != (was < NARROW):
            tick()

    ui.on("tt-width", on_width)

    def on_seen(e):
        # A latch, not a state: SEEN_JS only ever fires this when a human touched
        # the page, so the argument carries nothing and is deliberately ignored.
        nonlocal touched
        touched = True

    ui.on("tt-seen", on_seen)

    with ui.element("div").classes("tt-app"):
        shell = ui.element("div").classes("tt-main")
        with shell:
            with ui.element("div").classes("dw"):
                # The filter bar sits above the session tree, outside the container
                # render_drawer() clears, and carries the N/M readout: under dim,
                # "nothing matched" and "your match is 600 px below" look identical.
                with ui.element("div").classes("dw-find"):
                    search = ui.input(placeholder="filter").props(
                        "clearable dense borderless").classes("dw-in")
                    # dict form, never the props STRING: see build_window
                    search.props["debounce"] = cfg["ui"]["filter_debounce_ms"]
                    hits = ui.label("").classes("dw-count")
                    theme_btn = ui.element("button").classes("dw-theme")
                    with theme_btn:
                        theme_lbl = ui.label(THEME_ICONS[mode])
                    theme_btn.props["title"] = f"theme: {mode} (click to cycle)"
                drawer = ui.element("div").classes("dw-tree")   # Task 10 renders here
                # Context files at known locations, not ones found in log text -
                # built once, like .dw-find above, never rebuilt by a poll. Absent
                # entirely when nothing exists: an always-there-but-empty footer
                # is worse than no footer.
                ctx = []
                nearest = nearest_claude_md(Path.cwd(), Path.home())
                if nearest:
                    ctx.append(("CLAUDE.md", nearest))
                home_claude = Path.home() / ".claude" / "CLAUDE.md"
                if home_claude.is_file():
                    ctx.append(("~/.claude/CLAUDE.md", home_claude))
                # The session-memory directory is a DIRECTORY; path_spans only
                # ever returns files. Rather than teach it (or open_path)
                # directories, link the representative MEMORY.md inside it - the
                # memory tool that populates the directory always writes one
                # alongside the rest, so this is the file a user actually wants.
                mem_file = (Path.home() / ".claude" / "projects" /
                            str(Path.cwd()).replace("/", "-") / "memory" / "MEMORY.md")
                if mem_file.is_file():
                    ctx.append(("memory", mem_file))
                if ctx:
                    with ui.element("div").classes("dw-ctx"):
                        for label, p in ctx:
                            p = str(p.resolve())
                            btn = ui.element("button").classes("lk dw-ctx-i")
                            btn.props["data-path"] = p
                            btn.props["title"] = f"open {p}"
                            with btn:
                                ui.label(label)
                            btn.on("click", lambda _, pp=p: open_path(pp, cfg, extra_roots=(pp,)))
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
                ui.label(f"Every {poll_seconds}s · last")
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
        theme_btn.props["title"] = f"theme: {mode} (click to cycle)"

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
        session file lands. It is set from `where` on every tick instead.

        Every prop whose value comes from a session file is ASSIGNED, never
        interpolated into a .props() string. The string form is parsed
        (nicegui/props.py PROPS_PATTERN splits on whitespace outside quotes), so
        a value carrying a double quote closes the attribute and opens new ones:
        `table-talk action pwn --why w --rec r --project 'p" onmouseover="..."'`
        put a REAL onmouseover handler on this element - safe_project() strips
        only / \\ NUL, so the CLI happily accepts it. props is an ObservableDict
        whose on_change updates the element, and the dict form is never parsed.
        """
        el = ui.element("div").classes("win")
        el.props["data-window"] = key
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
                        btn = ui.element("button").classes("wb")
                        btn.props["title"] = tip
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
        # rAF: the window may have only just been moved back onto the wall.
        # The key is data, never code: see scroll_js.
        ui.run_javascript(scroll_js(key))

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
            put("drawer_open", not store("drawer_open", cfg["ui"]["drawer_open"]))
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
        collapsed = not store("drawer_open", cfg["ui"]["drawer_open"])
        sig = drawer_sig(groups, collapsed)
        if getattr(container, "tt_sig", None) == sig:
            return
        container.clear()
        with container:
            if collapsed:
                # 54 px of drawer: the three-letter tag, what is waiting, and the
                # meter. Same click target as the full row, so scope survives the
                # collapse rather than being a different feature at another width.
                for g in groups:
                    rail = ui.element("button").classes(
                        "rail-item dw-on" if g["project"] == scope else "rail-item")
                    rail.props["data-project"] = g["project"]
                    rail.props["title"] = g["project"]
                    with rail:
                        ui.label(abbrev(g["project"])).classes("rail-ab")
                        ui.label(f'●{g["open_actions"]}').classes(
                            "b-act" if g["open_actions"] else "b-off")
                        with ui.element("div").classes("rail-t"):
                            ui.element("i").style(f'width:{g["pct"]}%')
                    rail.on("click", lambda _, p=g["project"]: on_scope(p))
            else:
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
                    row.props["data-project"] = g["project"]
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
                            tri.props["title"] = f'fold {g["project"]}'
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
                        srow.props["data-session"] = sess["key"]
                        with srow:
                            ui.label(GUIDES["last"] if last else GUIDES["mid"]).classes("dw-g")
                            with ui.element("div").classes("dw-l1"):
                                ui.label(sess["date"]).classes("dw-nm")
                                ui.label(ago(sess["summary"]["latest"])).classes("dw-meta")
                            ui.label(GUIDES["none"] if last else GUIDES["line"]).classes("dw-g")
                            meter_row(sess["summary"])
                        srow.on("click", lambda _, k=sess["key"]: on_focus(k))
        # Recorded only after a CLEAN build, never before it: an exception
        # mid-tree would otherwise leave a half-drawn drawer marked up to
        # date, and no later poll would ever repaint it.
        container.tt_sig = sig

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
        win["when"].props.set_optional(
            "title", stamp(summary["latest"]) if summary["latest"] else None)
        render_window_body(win["body"], state, newest, query, changed,
                           cfg["ui"]["collapsed_sections"])

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
        nonlocal layout, on_wall, touched
        # read the clock BEFORE the files: a write that lands between the two
        # would otherwise be stamped as already seen and never get its gutter
        now = time.time()
        was_on_wall = on_wall          # the wall the interaction actually landed on
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
        drawer_open = store("drawer_open", cfg["ui"]["drawer_open"])
        cols = (1 if zoomed in windows
                else cols_for(wall_width, store("cols", cfg["ui"]["columns"])))
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
                if typ not in ("action", "task", "term", "diagram"):
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
            # the gutter set is part of the signature, or the window that must
            # DROP its gutters never repaints: its data did not change, that is
            # the whole point of it
            changed = changed_ids(states[k], seen_at.get(k, opened_ts))
            sig = (query, newest, states[k], changed)
            if win["sig"] != sig:
                paint_window(win, k, states[k], newest, query, changed)
                # AFTER the paint, never before: an exception mid-build (one
                # non-string field is enough) would otherwise leave the window
                # permanently marked up to date and truncated where it threw.
                # Recorded last, the next poll simply paints it again.
                win["sig"] = sig
            # both advance without the window's own data changing: age with the
            # clock, the tmux index whenever a newer sibling session appears
            for el, txt in ((win["when"], ago(win["latest"]) if win["latest"] else ""),
                            (win["ix"], f":{session_label(states[k], where[k][1])}")):
                if el.text != txt:
                    el.set_text(txt)
        # Only now, only if you touched the page since the last poll, and only
        # for the windows we just drew, does anything count as seen. Untouched,
        # this never runs and the gutters pile up until you do. A window that was
        # NOT on the previous wall is skipped: the interaction that brought it
        # back (Escape out of a zoom) happened while it was still off screen, so
        # it keeps its gutter until the next one - which is the whole point of a
        # watermark per window rather than one for the page.
        # int(now) - 1, not now: bin/table-talk stamps ts in WHOLE seconds, so an
        # event written at 100.9 carries ts=100 and a float watermark of 100.3
        # would mark it already-seen. One second back is the newest watermark
        # that cannot swallow a change - it costs a redundant gutter for one poll
        # and never a missed one.
        if touched:
            touched = False
            for k in visible:
                if k in was_on_wall:
                    seen_at[k] = int(now) - 1

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
    ui.timer(poll_seconds, tick)
    ui.run(host="127.0.0.1", port=port, show=False, reload=False, title="table-talk", favicon="🗣")


if __name__ in {"__main__", "__mp_main__"}:
    ap = argparse.ArgumentParser()
    # no default: unset means "whatever the config says", and main() resolves it
    ap.add_argument("--port", type=int)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        selftest()
    else:
        main(a.port)
