# Design "web" — Svelte 5 SPA + Python stdlib SSE, native-ready

Status: proposal. Written under ponytail (full). Target repo:
`<repo>` at `5687092`.

---

## Goal

Replace `bin/table-talk-dash.py` (3257 lines of NiceGUI) with a Svelte 5
single-page app served by a stdlib-only Python HTTP server that pushes folded
model snapshots over Server-Sent Events.

Three things this buys, in order of how much they matter:

1. **The model is not ported.** `bin/tt_model.py` and `bin/tt_config.py` are
   imported unchanged. Every one of the twenty pure functions in ui.md's
   table, and every pin in `tt_model.selftest()` / `tt_config.selftest()`,
   keeps working on the day of the switch with zero re-derivation. No
   TypeScript `fold()`. No second `art_spans()` whose Unicode ranges drift.
   No second `path_spans()` whose symlink check is subtly weaker than the one
   a real bug taught us to write.
2. **Runtime dependencies go to zero.** Today: `uv` + `nicegui>=3.16` +
   `claude-agent-sdk>=0.2`. After: `python3` (≥3.11 for `tomllib`) and a
   browser. No Node, no Bun, no Rust, no uv, no wheel download, no lockfile,
   no per-platform binary, no code signing. Linux and macOS are identical
   because nothing platform-specific is shipped.
3. **The native phase gets cheap and honest.** A GPUI window built on
   gpuix-svelte becomes *another client of `/events`* — it needs the view
   layer and nothing else. It does not need a model port either. That is the
   only version of "native later" that is not a fantasy, and this design is
   the one that produces it as a by-product rather than as a second project.

Non-goals, stated once: this is not a native window in phase 1–5, it opens in
the user's browser. That is what `table-talk serve` already does today.

---

## Architecture

### Processes

```
  Claude session                     the user
       │ writes                          │ reads/clicks
       ▼                                 ▼
 ~/.local/share/table-talk/       browser tab (any browser)
   2026-09-08-proj.jsonl                 │  http://127.0.0.1:8731/
   .beat/<sid>                           │
   .ui/ui.json                           │
       ▲                                 │
       │ reads (poll, 2 s)               │ SSE snapshot ← / POST intents →
       └──────────── python3 bin/tt_web.py ─────────────┘
                     (one process, stdlib only,
                      imports tt_model + tt_config)
```

One process. `http.server.ThreadingHTTPServer` bound to
`cfg["server"]["host"]:cfg["server"]["port"]`. One daemon thread runs the
poll loop; request handler threads read the snapshot it publishes.

### Who reads the JSONL

Python, and only Python. `tt_model.fold_cached(path)` for every `*.jsonl` in
`DATA_DIR`, exactly as `poll()` does today (dash.py:3030-3206), including the
`(mtime,size)` cache so a steady-state tick re-parses nothing. `TABLE_TALK_DIR`
keeps working for `docs/demo` and for per-client work logs, because
`tt_model.DATA_DIR` is the same module-level value it always was.

The browser never sees a file path it can act on and never reads a file. It
receives a JSON object.

### Who owns state

Three tiers, matching the split the current dashboard already arrived at:

| Tier | Lives in | Contents |
|---|---|---|
| Model | `tt_model`, recomputed each poll | folded states, groups, summaries, pack |
| Shared UI prefs | `~/.local/share/table-talk/.ui/ui.json` | `marks`, `folds`, `groups_folded`, `zoomed`, `scope`, `needs_me`, `current`, `cols`, `sort`, `drawer_open`, `merged`, `seen`, `theme` |
| Per-client | the browser tab, never persisted | wall width, `seen_at`/`opened_ts`/`touched` watermarks, reply drafts, filter query, section open/shut toggles |

The shared tier is the twelve-plus-one keys `app.storage.general` holds today
(dash.py:2315-2357), moved to a plain JSON file written with
`os.replace` (atomic; same discipline as `tt_config.set_keys`). It replaces
NiceGUI's storage and with it the whole `NICEGUI_STORAGE_PATH`-before-import
hazard (dash.py:22-36 and the AST-order pin at 1849-1853): a JSON file has no
import-time class attribute to lose a race with. Two tabs share prefs; two
tabs get independent watermarks and widths. Same semantics as today, one file
instead of a framework.

Section open/shut moves from `container.tt_open` (a Python attribute stashed on
a NiceGUI element to survive `container.clear()`) to ordinary component state,
because nothing clears.

### How updates flow

```
poll thread, every cfg.server.poll_seconds (2.0):
    now = time.time()                     # clock BEFORE the file reads, as today
    states  = {stem: fold_cached(p) for p in DATA_DIR.glob("*.jsonl")}
    beat    = live_sessions(now, DATA_DIR / ".beat", 120)
    prefs   = ui_store.read()             # cheap: mtime-gated, like the config
    snap    = build_snapshot(states, beat, prefs, cfg)   # a plain dict
    body    = json.dumps(snap, separators=(",", ":"), sort_keys=True)
    if sha1(body) != last_hash:           # the paint guard, in two lines
        last_hash = sha1(body); publish(body)
```

`publish` writes `data: <body>\n\n` to every open `/events` response and
flushes. Clients that raise on write are dropped. No delta protocol: a full
snapshot for a heavy real wall (40 sessions × 30 rows) is ~250 KB, pushed at
most every 2 s, over loopback. Deltas are the speculative abstraction; add
them when a profiler says so.

**The snapshot carries absolute timestamps only** — `ts`, `latest`, `read_ts`,
`now` — never a rendered `ago()` or `hm()` string. Two reasons, both
load-bearing:

- A rendered relative string changes every second, so the hash would fire a
  push every 2 s forever and the guard would be worthless.
- It removes the constraint that forced today's `as of HH:MM` to be absolute
  ("a row only repaints on data change so a relative *12m ago* would freeze
  and lie", ui.md §Progress bar). The client re-renders `ago()` on its own
  1 s `setInterval` against `Date.now()`, so relative times stay honest with
  no repaint. `hm()` stays absolute because it is the right label, not
  because it is forced.

The `?w=` question — the wall's pixel width, which the server cannot see and
which drives `cols_for`/`default_cols`/`pack` — is answered by making width a
**connection parameter**: `GET /events?w=1680`. The handler holds it in a
local variable, so there is no per-client server state to bookkeep, and
`pack`/`weight`/`cols_for` stay in Python, unmodified and untested-twice. The
client reconnects the `EventSource` only when the clamp flips, using three
`matchMedia` listeners (`(min-width:900px)`, `1200px`, `1800px`) instead of a
`ResizeObserver` — the same "only re-fire on a clamp flip" rule as
dash.py:2361-2376, with less machinery.

### How the Python CLI launches it

`bin/table-talk` gains one subcommand, and later `serve` is repointed:

```python
def cmd_web(port, force=False, no_browser=False):
    if (msg := serve_refusal(os.environ, force)):    # reused verbatim
        sys.exit(msg)
    web = Path(__file__).resolve().parent / "tt_web.py"
    argv = [sys.executable, str(web)] + (["--port", str(port)] if port is not None else [])
    if no_browser: argv.append("--no-browser")
    os.execv(sys.executable, argv)
```

No `uv`, no `shutil.which`, no PEP 723 header, no wheel resolution on first
run. `serve_refusal` (bin/table-talk:580-596) is reused as-is, so a Claude
session still cannot wedge itself launching a server that never returns.
`table-talk url` already reads `server.port` from the same config and needs no
change.

`bin/tt_web.py` is a plain `#!/usr/bin/env python3` script — importable,
selftestable with `python3 bin/tt_web.py --selftest`, and runnable directly.
On start it calls `ensure_config()` (moved verbatim from dash.py:817-848),
binds, prints the URL, and `webbrowser.open(url)` unless `--no-browser`. That
is the one command: **`table-talk web`** during migration, **`table-talk
serve`** after the flip.

### HTTP surface (seven routes, four of them writes)

| Route | Purpose |
|---|---|
| `GET /` | `web/dist/index.html` |
| `GET /assets/*` | static from `web/dist/assets/`, path-confined, `Cache-Control: no-cache` |
| `GET /events?w=<px>` | SSE; first frame is a full snapshot, then on change |
| `POST /ui` | `{"marks": [...]}` etc. — merged into `ui.json`, allowlisted keys, triggers an immediate republish |
| `POST /open` | `{"path": ...}` \| `{"url": ...}` \| `{"transcript": "<4-hex sid>"}` |
| `POST /settings` | `{"server.port": 8732, ...}` → `form_updates` → `coerce` → `tt_config.set_keys` |
| `POST /restart` | re-check `port_free`, then `os.execv` |

Trust boundaries, none of which get lazy:

- **Origin guard.** These POSTs launch processes and rewrite a config file, so
  any page in any browser tab could otherwise POST to `127.0.0.1:8731`. The
  handler rejects (403) a request whose `Origin` header is present and is not
  `http://127.0.0.1:<port>` / `http://localhost:<port>` / the configured host.
  Four lines. This boundary is *new* — NiceGUI's websocket carried its own
  same-origin behaviour — so it is added deliberately, not inherited.
- **`/open` re-derives confinement from scratch**, exactly as
  `open_path`/`open_url` do today (dash.py:476-520). The client's payload is
  data that came out of a log file; it is treated as such. Scheme allowlist
  (`http`/`https` only), root confinement after symlink resolution, launch as
  an argv **list**, never `shell=True` — and the AST scan that bans any
  `shell=` keyword anywhere in the file moves to `tt_web.py`'s selftest.
- **The transcript link never sends a path.** The `ix` button POSTs
  `{"transcript": "a1b2"}`; the server runs `transcripts()` (dash.py:541-565,
  moved verbatim, ambiguous 4-char prefixes still dropped entirely) and opens
  the resolved file with that one path added to `extra_roots` for that one
  call. This is strictly better than today, where the resolved path is
  rendered into the page first.
- **`/ui` allowlists key names** against the thirteen known keys and
  type-checks each value; an unknown key is a 400, not a silent merge (the
  same "the one mistake that must never be silent" rule `tt_config.load`
  applies to unknown config keys).
- `server.host != "127.0.0.1"` prints the same plain warning at the moment it
  takes effect (dash.py:2294-2300).

---

## Components

Thirteen files under `web/src/`. Each is one screen of code. Every one is
written to the DOM/GPUI-portable subset **where that is free** — flat class
names, `px` units, explicit event handlers, no `bind:`, no `@html` — and
deliberately not where it is expensive (see §"What the portable subset costs").

### `lib/stream.svelte.js` — the data spine
Opens `new EventSource("/events?w="+wallWidth)`, parses each frame into a
`$state` object `snap`. Holds the three `matchMedia` listeners and reconnects
on a clamp flip. Exposes `snap` and `post(route, body)`. Depends on: nothing.
Used by: everything. ~70 lines. This is the only place in the client that
knows a network exists.

### `lib/fmt.js` — the pure client helpers
The handful of dash.py helpers that must move client-side because they are
time-derived: `ago(ts, now)`, `hm(ts)`, `stamp()`, `blocks(pct, cells)`,
`bar_for(open, done)`, `live_delay(read_ts, now)`, `cols_for(width, pref)`,
`default_cols(width)`, `abbrev(name)`, `parts(text, q)`. Pure, no imports,
unit-tested by `node --test`. ~90 lines. Everything else stays in Python.

`parts()` is the one duplicated model function. It is duplicated rather than
served because highlighting must re-run on every keystroke of the filter box,
and a round trip per keystroke is absurd. Its pins (original-casing
preservation, non-overlapping matches) are re-asserted in
`web/test/fmt.test.mjs`. Note what it does **not** carry: `marked()` and its
48,000-case HTML-escaping property test do not exist here, because the client
renders `{#each parts(t, q) as [chunk, hit]}<span class:tt-hit={hit}>{chunk}</span>{/each}`
— escaping is structural in Svelte, so the bug that test guards cannot occur.

### `lib/keymap.js` — one dict, two consumers
`KEYMAP = {"\\":"drawer", m:"mark", z:"zoom", f:"fold", s:"sort", "/":"filter",
"!":"needs-me", u:"merge", "?":"keys", Escape:"unzoom"}` and a single `do(name)`
that mutates prefs and POSTs them. The statusline builds its chips by
iterating the same dict, so a key and a click can never diverge — the
invariant dash.py:313-315/2756-2793 already enforces, kept by construction.
The `keydown` handler ignores repeats and any event whose
`target.closest("input,textarea,select,button")` is non-null.

`BLUR_JS` (dash.py:255-260) is deleted. It existed only because NiceGUI's
keyboard layer swallows every keystroke while a button holds focus; with an
ordinary `window.addEventListener("keydown")` there is nothing to work around,
and the `e.detail>=1` synthetic-click subtlety goes with it.

### `lib/seen.svelte.js` — watermarks
Per-window `seen_at`, floored at page-open. Advanced only on real interaction
(`click`, `keydown`, `scroll` — a capture-phase listener on `document`, plus
`visibilitychange` as the secondary signal, exactly the reasoning in
dash.py:282-291 about a covered-but-not-backgrounded second monitor). Advance
uses `Math.floor(now) - 1` for the same whole-second `ts` reason. A window off
the wall freezes its watermark. `changed_ids` is computed client-side against
the snapshot's absolute `ts` values; terms never get a gutter.

### `Wall.svelte` — the tmux-shaped wall
Renders `snap.columns` (a `[[key,...], ...]` list, already packed by
`tt_model.pack` on the server) as N flex columns of `<Window>`. Keyed
`{#each cols as col}{#each col as key (key)}` — Svelte's keyed each does the
whole job of the per-window paint signature (dash.py:3097-3143) and of
`layout_key`'s "re-pack only when the layout actually changed" rule: a poll
that only changed text moves no node. The empty-wall message picks one of
three by `snap.empty_reason` (`"needs_me" | "scope" | "nothing"`). Depends on:
`stream`, `Window`.

### `Window.svelte` — one session/project card
Titlebar (project name, `ix` button, `ago(latest)`, `!`/`#`/`M`/`Z`/`*`/`◉`
flags, the `wctl` M/Z/▾ row) plus five `<Section>`s in fixed order: actions,
jobs, diagrams, glossary, done. Click anywhere makes it current. Wrapped in
`<svelte:boundary>` with a `failed` snippet reading "could not render this
window — <key>" — this is the parity for dash.py:3133-3138's per-window
try/except, and it holds the same property: one unrenderable row costs its own
card and nothing else. Depends on: `Section`, `Row`, `fmt`, `seen`.

### `Section.svelte` — the shell-prompt collapse bar
`❯ <title> (<count>) ▾/▸` as a real `<button>`. Open/shut is component state
seeded from `snap.collapsed_sections`. A filter hit inside a shut section
force-opens it **for that render only** without touching the user's own toggle
(`open || force`) — the #57 pin, expressed as one boolean or. When shut it
draws the `(█ open, ░ resolved)` glyph bar from `fmt.bar_for`; the bar
disappears when open.

### `Row.svelte` — every row kind
One component, `{#if}` on `ev.type` and `ev.status`, because the five row
kinds already share the id-button / title / guided-sub-row skeleton that
`_id_button`/`_art_sub`/`_reply_sub` share in dash.py. Branches:

- **action**: id button, `background`, the `▉` cursor when
  `ev.id === snap.newest_open_action`, then `int` (**first**, before `why`),
  `why`, `rec`, `<Art>`, `<Reply>`.
- **task**: id button, `what`, then the meter line — `⏸ blocked on <id>` when
  `ev.blocked_on_open` (the server sends the resolved boolean from
  `tt_model.blocked_by`, computed across every session file), else the
  `blocks()` bar + pct + `as of hm(read_ts)`, else the 5-cell `.scan` sweep —
  then `int`, `<Art>`, `<Reply>`.
- **term**: term as the id cell, `int` as title, `def` sub-row. No gutter, no
  reply box.
- **diagram**: title as id cell, `<Diagram>` body.
- **done**: id button (still clickable and copyable), title, `<Art>`,
  `<Reply>`.

The sub-row tree guide stays exactly what it is today: one continuous CSS rule
on `.sub::before`/`::after` with a corner on the last sub-line, ported verbatim
from `tt.css:245-252`. Not a per-row glyph — that broke when `why` wrapped.

### `Art.svelte` — two-ink ASCII sketches
Takes `ev.art_spans` — the server already ran `tt_model.art_spans(text)` and
sends `[[chunk, is_structure], ...]`, so the Unicode range table exists once,
in Python, with its ~500-case fuzz test intact. Renders
`{#each spans as [c, st]}<span class:st>{c}</span>{/each}` inside
`.art`/`.art-in` (`white-space:pre`, own `overflow-x:auto`, `inline-block`
inner so a full-width panel centres multi-line art without shearing it).
Drawn on actions, tasks **and** done rows.

### `Reply.svelte` — the answer box
A plain `<textarea>` and a `copy` button. **The entire `REPLY_JS` machinery is
deleted** — the client-side draft `Map`, the `requestAnimationFrame`-throttled
`MutationObserver`, the value+selection restore, the "give up caret restore on
a deliberate mousedown elsewhere" escape hatch (dash.py:96-152, ~60 lines).
All of it exists because the wall calls `container.clear()` every 2 s. Svelte
never clears the node, so the textarea keeps its own value and its own caret
the way a textarea does. Copy writes `"<id>: <trimmed answer>"`, id guarded by
`/^[0-9a-f]{4,}$/`, and the "copied" flash fires only inside
`writeText().then()` so a denied clipboard never lies.

### `Drawer.svelte` — tree, rail, meters, filter, footer
284 px expanded / 54 px collapsed rail, toggled by `\`. Expanded: filter input
(debounced by `cfg.ui.filter_debounce_ms`), `N/M rows match`, the theme-mode
button (`◐`/`○`/`●`), the `sort:` cycler, then the project/session tree with
fold triangles (only when a project has >1 session), `● n` / `▶ n` badges
greyed at zero, and the `[####    ]NN%` htop meter. Collapsed: one button per
project with `abbrev()`, `●n`, and a thin bar — same click-to-scope target.
Auto-fold rules (`fold on first-ever sight with zero open actions`, persisted
in `seen`; **rising edge** in open-action count force-reopens) run server-side
in `build_snapshot` from `tt_model.roll_up` counts, since they read the
persisted `seen` map. Footer built once from `snap.ctx`: nearest `CLAUDE.md`
(walked up from cwd, never above `$HOME`, symlinks resolved), `~/.claude/CLAUDE.md`,
`MEMORY.md`, `⚙ settings` (opens the form), the raw settings path, and the
refreshed `config.example.toml`; absent entirely when nothing exists.

Filter **dims** (`.tt-dim{opacity:.78}`) and highlights, never hides — the
query can never empty the wall. On change it scrolls the first `.tt-hit` into
centre view, or the wall back to top on an empty query.

### `Statusline.svelte`
Spinner (braille frames, advanced one frame per **successful** snapshot only —
the server sends a monotonically incrementing `snap.polls_ok`, so a failed
poll freezes it), `Every Ns · last HH:MM:SS`, the tally
(`●N open  ▶M running  ⏸K blocked`, blocked subtracted from running, counted
across every session regardless of scope or zoom, `all clear` when empty), the
port-mismatch segment with its restart button, the scope segment with its ✕,
`cols 1 2 3`, one chip per `KEYMAP` entry except `filter`/`unzoom`, and a live
clock. `Toast.svelte` and the tab-title prefix read `snap.tally.open` directly
as a number — no `MutationObserver` reading a rendered string out of the DOM
with a regex (`TAB_TITLE_JS`/`TOAST_JS`, dash.py:158-205, deleted). The toast
still fires only on a **rise** against a baseline captured at first snapshot,
so a reconnect never fires a stale burst.

### `Settings.svelte` — the form, still derived from the validator
Fields come from `snap.form_fields` — which is `tt_config.form_fields()`
(tt_config.py:163-183) serialized as-is, so there is still no second hardcoded
field list that can drift from the validator, and number fields still carry the
validator's own bounds. Save POSTs the whole form to `/settings`; the server
runs `form_updates` (writes only *changed* keys) and `coerce` (float widget →
the current value's type; out-of-bounds → `None`, meaning leave alone) and then
`tt_config.set_keys` — TOML line surgery, comments preserved, atomic
`os.replace`, read-back-and-compare safety net. All four functions move
verbatim. Colour tokens stay out of the form for the same reason as today.

### `Diagram.svelte` — mermaid
`await import("./mermaid.min.js")` on first diagram, then `mermaid.render()`
per source with `securityLevel:"strict"` (restated explicitly, so a future
config knob cannot silently relax it), `theme:"base"`, and the per-render
`%%{init:...}%%` directive carrying `fontFamily` and `fontSize:"12px"` —
**with no hyphen anywhere in the directive**, because mermaid's
`themeVariables` sanitiser regex is `^[\d "#%(),.;A-Za-z]+$` and one bad char
blanks the whole value silently. Colours keep coming from `!important` rules in
`app.css` keyed on the app's own theme tokens (`.mmd .node rect` and the rest
of the deliberately scoped list), never from mermaid's theming, because they
must swap live with `prefers-color-scheme`. A parse error draws mermaid's own
error graphic; the server never sees it. Mermaid is lazy-imported so the 2.8 MB
file is fetched only by a wall that actually has a diagram on it.

### Heartbeat
Not a component. `bin/tt-beat` is untouched — same `PostToolUse` hook, same
`~/.local/share/table-talk/.beat/<4-char-sid>` file-touch contract. The server
calls `live_sessions(now, beat_dir, 120)` (dash.py:574-596, moved verbatim: a
future mtime is a clock jump and never live; no hook, no dir, unreadable file →
empty set, silently) and puts the sid set in the snapshot. The client renders
`◉`. Because the beat set is inside the hashed snapshot, a session going live
or dark pushes a frame on its own — no separate out-of-band path, unlike
today's "applied per poll, outside the paint signature" special case.

### Themes
`app.css` is `bin/tt.css` moved, with its `:root` / dark-mode token blocks
intact. Mode: `system` uses `@media (prefers-color-scheme: dark)`; explicit
light/dark set `data-theme` on `<html>`, which the stylesheet honours. That
replaces `ui.dark_mode()` and is one CSS rule rather than a framework call.
The fifteen bundled palettes stay in `bin/themes.json`, read by
`tt_config.themes()`, unchanged, with their WCAG contrast floors still enforced
by `tt_config.selftest()`. `theme_css(cfg["theme"])` (dash.py:56-77 — emits only
tokens that *differ* from the stylesheet default, re-validates every value as
hex on the way out, iterates the DEFAULTS key set so a hostile key name is
matched and never interpolated) **moves from dash.py into tt_config.py**, which
is where it belonged: `tt_config.selftest()` already cross-checks `DEFAULTS`
byte-for-byte against the stylesheet, and after the move that check points at
`web/src/app.css` — a one-path edit, not a new obligation. Its output rides in
`snap.theme_css` and is applied to a single `<style id="tt-theme">`.

---

## Data flow

```
*.jsonl  ──fold_cached──▶  {id: event}  per file
                              │
     ┌────────────────────────┼──────────────────────────────┐
     ▼                        ▼                              ▼
 summarize            group_sessions / merge_projects   open_action_ids
     │                        │                              │
  roll_up               sort_groups(mode)               blocked_by(ev, ·)
     │                        │                              │
     └────────────┬───────────┘                              │
                  ▼                                          │
        weight(state) per key                                │
                  ▼                                          │
        pack(keys, cols_for(w, pref), weights, marks)        │
                  ▼                                          │
         build_snapshot(...) ◀────────────────────────────────┘
                  │  + art_spans per sketch, + live_sessions, + tally,
                  │  + form_fields, + theme_css, + ctx, + prefs
                  ▼
        json.dumps(sort_keys=True) ──sha1 changed?──▶ SSE push
                  │
                  ▼
   Svelte $state snap ──keyed {#each}──▶ Wall / Drawer / Statusline
                  │
   client-only: ago()/clock (1 s interval), parts(text,q) highlighting,
                seen watermarks, reply drafts, section toggles
```

### Polling vs watching, and the 2 s cadence

Poll, not watch. `cfg.server.poll_seconds` (default 2.0, validated ≥0.2 —
0 or negative pegs a core, the pin at tt_config.py:509-518) drives one
`time.sleep` loop in a daemon thread. `fold_cached`'s `(mtime,size)` gate means
a steady-state tick re-`stat`s a handful of files and parses nothing. The
config file's own mtime is `stat`ed on the same tick and re-`load()`ed only
when it changes — never a TOML parse every 2 s for an answer that almost never
differs (dash.py:3179-3190).

`inotify`/`kqueue` is the rung above and it is not needed: the directory holds a
handful of append-only files, the cadence is already the product's documented
behaviour ("Every 2s" is on the statusline), and a watcher would need a
debounce that reintroduces the same 2 s anyway. Add a watcher when someone
measures the poll costing something.

The loop wraps `build_snapshot` in `try/except`: a failure increments nothing,
sets `snap.stale = True`, logs once, and keeps the timer alive — the frozen
spinner beside a stale timestamp, which is the whole `watch(1)` idiom and the
exact behaviour of `tick()` today (dash.py:3209-3228). A stale snapshot is
still pushed, because the client must be told it is stale.

---

## Feature parity table

Every line of ui.md §1, in order.

| ui.md feature | How "web" delivers it |
|---|---|
| One window per session (flat) / per project (merged) | `tt_model.merge_projects` / per-file states, server-side; `snap.windows` keyed the same way |
| Greedy pack into N columns, marked first | `tt_model.pack` unchanged, server-side; `snap.columns` |
| Column count 1/2/3 auto from width; pref is a **maximum**; <900 px always 1 | `cols_for`/`default_cols` unchanged in Python, fed by `?w=` |
| Wall width reported by the client | `?w=<px>` on the EventSource URL; three `matchMedia` listeners reconnect on a clamp flip (replaces `ResizeObserver` + `WIDTH_JS`) |
| Content-derived window weight (never measured pixels) | `tt_model.weight` unchanged |
| Re-pack only when `layout_key` changes | Free: the server re-packs each tick but the snapshot hash suppresses the push, and Svelte's keyed `{#each}` moves no node when the column lists are equal |
| Zoom forces 1 column, shows one window | Server honours `prefs.zoomed` in `build_snapshot` |
| Three distinct empty-wall messages | `snap.empty_reason` ∈ `needs_me`/`scope`/`nothing` |
| Filter can never empty the wall | Filter is client-side and only dims; the server never filters |
| `!` bell, `steps(2,start)` blink | `.bell` CSS ported verbatim from `tt.css` |
| `#` activity, `M`, `Z`, `*` current + `--sel` tint + `--caret` underline | Ported CSS; `*` from `prefs.current`, resolved with the same `target()` fallback (last clicked if still on wall, else first) |
| `◉` beat | `snap.beat` from `live_sessions`, unchanged |
| Titlebar: project, `ix` button, `ago(latest)`, `wctl` M/Z/▾ with tooltips | `Window.svelte`; `ago` client-side |
| Click anywhere makes current | `on:click` on the card |
| Five sections in fixed order + shell-prompt collapse bars | `Section.svelte` |
| Section start-state from `ui.collapsed_sections` | `snap.collapsed_sections` |
| Diagrams section starts open unless folded by config | Same rule, `"dia" not in collapsed` |
| Shut section shows the `(█,░)` glyph bar, capped at 20 cells | `fmt.bar_for` |
| A query hit force-opens a shut section, for that render only | `open \|\| force` in `Section.svelte` |
| Action row: id, background, `▉` on the single newest open action wall-wide, `int` **before** `why`, `rec`, art, reply | `Row.svelte`; `snap.newest_open_action` computed server-side |
| Task row: id, what, blocked banner OR scan OR bar+pct+`as of HH:MM`, `int`, art, reply | `Row.svelte`; `blocked_on_open` from `tt_model.blocked_by` |
| Term row, diagram row, done row (id still clickable) | `Row.svelte` branches |
| Sub-row tree guide as ONE continuous CSS rule with a corner | `tt.css:245-252` ported verbatim |
| id button shows id + `sid`/`_from` line | `Row.svelte`, from the snapshot's `_from`/`sid` |
| ASCII art two-ink split | `tt_model.art_spans` server-side; `Art.svelte` renders the spans |
| Art geometry: `white-space:pre`, own `overflow-x:auto`, `inline-block` inner | CSS ported verbatim |
| Art on actions, tasks **and** done | Three `Row.svelte` branches call `<Art>` |
| `progress_pct` explicit-`pct`-beats-scrape, bool rejected | `tt_model.progress_pct` unchanged |
| `blocks(pct, 14)` snapping, no tween | `fmt.blocks` (10 lines, unit-tested) |
| Pulse via **negative** `animation-delay` = the reading's age; stale/future → not live | `fmt.live_delay` + a `style="animation-delay:{d}s"`; CSS animation ported verbatim |
| `as of HH:MM` absolute | `fmt.hm` |
| Indeterminate 5-cell `.scan` with staggered delays | CSS ported verbatim |
| Blocked-on derived, cross-file, never stored | `tt_model.blocked_by` + `open_action_ids` over every state |
| Blocked replaces the bar entirely; counted separately in the tally | Same |
| Answering a blocker repaints the blocked task | Free: `open_action_ids` is inside the hashed snapshot |
| Reply box under action, task **and** done | Three branches |
| Draft + caret survive the 2 s refresh | **Free** — nothing clears the node; `REPLY_JS` deleted |
| Copy `"<id>: <answer>"`, id guarded, flash only on resolved promise | `Reply.svelte`, same guard, same `.then()` |
| Change gutters `.changed` / `.changed-job`; terms never gutter | `lib/seen.svelte.js` + ported CSS |
| Per-window watermark, frozen while off the wall, floored at page open | Same, client-side |
| Advanced only by interaction, `visibilitychange` secondary | Capture-phase `click`/`keydown`/`scroll` listeners |
| `int(now)-1` advance | `Math.floor(now)-1` |
| Not persisted; two tabs independent | Per-tab state, never POSTed |
| Drawer 284/54 px, `\` toggle, `drawer_open` persisted | `Drawer.svelte` + `/ui` |
| Filter input + hit count + theme toggle + sort cycler + tree | `Drawer.svelte` |
| Per-project meter row (● ▶ badges greyed at zero, `[####  ]NN%`) | From `tt_model.roll_up` counts in the snapshot |
| Click row scopes; click again clears; ✕ chip clears; triangle folds without scoping | `/ui` writes `scope` / `groups_folded` |
| Fold triangle only when >1 session | Same rule |
| Collapsed rail: `abbrev()` tag, `●n`, thin bar, same scope target | `Drawer.svelte` |
| Auto-fold on first-ever sight with zero open actions, persisted in `seen` | `build_snapshot` reads/writes `prefs.seen` |
| **Rising edge** in open-action count force-reopens a manually-folded project | Same comparison, server-side against `prefs.seen` |
| Context footer links, absent when nothing exists | `snap.ctx`; `nearest_claude_md` moved verbatim (never above `$HOME`, symlinks refused) |
| Filter debounced by `ui.filter_debounce_ms` | Client-side `setTimeout`. The `.props()`-injection hazard that made this config key dangerous does not exist without NiceGUI |
| Filter dims (`.78`), highlights, never hides; `N/M rows match`; scroll-to-first-hit / scroll-to-top | `Drawer.svelte` + `fmt.parts` |
| Spinner advances on **successful** poll only | `snap.polls_ok` counter |
| `Every Ns · last HH:MM:SS` | `snap.poll_seconds`, `snap.last_ok` |
| Tally: `●N ▶M ⏸K`, blocked subtracted, every session counted, `all clear` | `tally_text`'s logic moves into `build_snapshot`, sent as three numbers |
| Port-mismatch segment + restart button | `restart_offer` moved verbatim (silent when they agree or `--port` was explicit; real `port_free` socket bind before offering); `POST /restart` re-checks then `os.execv` |
| Scope segment + ✕ | `snap.scope` |
| `cols 1 2 3` with the effective one highlighted (zoom shows 1) | `snap.effective_cols` |
| Key chips from the same `KEYMAP` as the handler; `.on` for needs-me/merge | `lib/keymap.js`, one dict |
| Live clock | 1 s interval, client-side |
| Tab title `(N) ` prefix | Reads `snap.tally.open` as a number, not a regex over rendered text |
| Toast on a **rise** only, baseline read before observing, 5 s dismiss | Same rule against `snap.tally.open` |
| Every key, and its click equivalent | `lib/keymap.js` |
| Keyboard ignores keys while an input/button is focused, and on repeat | `target.closest(...)` + `e.repeat` |
| NiceGUI-button-focus workaround (`BLUR_JS`) | **Cut.** The problem does not exist without NiceGUI's keyboard layer |
| Merged/flat `u` toggle, `_from` tagging, higher-`ts`-wins on id collision | `tt_model.merge_projects` unchanged |
| Drawer always lists real session files regardless of wall mode | Server sends both `snap.sessions` and `snap.windows` |
| Clicking a drawer session while merged resolves to its project key | `M.parse_stem(key)[1]` server-side in the scroll target |
| Zoom / fold / mark toggles, persisted; one zoom at a time; scope change clears zoom; folded window costs weight 1 | `/ui` + `build_snapshot`; the weight-1 rule is in the server's `weights` dict |
| Transcript resolution over **every** `~/.claude/projects/*/*.jsonl`, ambiguous prefix dropped, derived fresh each poll | `transcripts()` moved verbatim; resolved server-side on `POST /open` so the path never reaches the page |
| `url_spans` http(s)-only, punctuation trimmed | `tt_model.url_spans` unchanged; spans sent with the row |
| `path_spans` must resolve to an existing **file** inside a root | `tt_model.path_spans` unchanged; roots resolved once at start |
| Click re-derives confinement from scratch; argv list, never `shell=True` | `POST /open`; the `shell=` AST ban moves to `tt_web.py`'s selftest |
| `links.open_command` (`open`/`xdg-open`) | Unchanged |
| Failed launch warns, never crashes | Unchanged |
| Copy-id: `SESSION: <sid> - ID: <id>` or bare id, `^[0-9a-f]{4,}$` guard, flash only on success | `Row.svelte` |
| Mermaid `securityLevel:"strict"`, `theme:"base"`, per-render `%%{init}%%`, no hyphen, CSS-token colours, client-side parse errors | `Diagram.svelte`, lazy-imported `mermaid.min.js` committed under `web/dist/` |
| Settings form derived from `form_fields()`; colour tokens excluded | `snap.form_fields` |
| Save writes only changed keys, `coerce`, `set_keys` line surgery | Moved verbatim |
| `needs_restart` message; `RESTART_KEYS` surface the restart button | Moved verbatim |
| `ensure_config()` copies the commented example; never rewrites the user's file; refreshes the reference copy each start | Moved verbatim into `tt_web.py` |
| Config mtime polled cheaply, re-loaded only on change | Same tick |
| Theme mode cycle `system→light→dark`, persisted, geometric glyphs | `@media` + `data-theme`; replaces `ui.dark_mode()` |
| 15 bundled themes, `adapted` tokens, `theme.dark_theme`/`light_theme` | `tt_config.themes()` unchanged |
| Config token overrides layered on top of a named theme | `tt_config.load` unchanged |
| WCAG contrast floors enforced against every bundled theme | `tt_config.selftest()` unchanged |
| `--hover` derived from `--surface`, never configurable | CSS ported verbatim |
| `theme_css` emits only differing tokens, re-validates hex on the way out | Moved to `tt_config.py`; its DEFAULTS-vs-stylesheet cross-check now points at `web/src/app.css` |
| UI-state persistence of the 13 keys | `~/.local/share/table-talk/.ui/ui.json`, atomic write |
| Storage lands beside the **data**, not the launch dir | Path is `DATA_DIR / ".ui"`. The `NICEGUI_STORAGE_PATH`-before-import hazard and its AST-order pin are **cut** — a JSON file has no import-time race |
| `wall_width`, watermarks explicitly NOT persisted | Per-tab, never POSTed |
| Two tabs: shared prefs, independent watermarks/width | Same |
| 2 s timer; one bad poll degrades the statusline, never kills the timer | Poll thread `try/except` + `snap.stale` |
| Clock read **before** file reads each tick | Same line order in the poll loop |
| Per-window paint guard so one bad row costs one card | `<svelte:boundary>` per `Window` |
| Signature recorded only **after** a successful paint | N/A by construction: there is no signature, and a boundary that catches renders the failure state rather than marking anything clean |
| Drawer signature | Same — cut, Svelte diffs the drawer |
| `tt-beat` hook contract, `BEAT_WINDOW=120`, future mtime never live, missing hook silent | `live_sessions` unchanged |
| `TABLE_TALK_DIR=docs/demo ./bin/table-talk web` | Unchanged mechanism (`tt_model.DATA_DIR`); runs alongside a real instance on another `--port` |
| `tt_jobs` job-*starting* (`start_job`/`run_job`) | **Argued cut.** #201 removed the job modal; grep shows no wall control calls `start_job` today — it is reachable only from `dash.py`'s own selftest. It dies with `dash.py` in phase 5, along with the `claude-agent-sdk` dependency. Its wall-*visible* surface (the "jobs" section = open tasks, the `#` flag, the blocked banner) is fully preserved above, because that surface was never the runner |

---

## Error handling

| Failure | Behaviour |
|---|---|
| Bad JSONL line (garbage, bad UTF-8, missing/non-numeric `ts`, lone surrogate) | `tt_model.fold` already tolerates each per-line without raising. **Unchanged, and its pins still run.** |
| Vanished / unreadable / directory-named `*.jsonl` | `fold` returns `{}`; the glob stops listing it; the key leaves `snap.windows`; Svelte's keyed each removes the card. No error path needed |
| A row the client cannot render | `<svelte:boundary>` on `Window.svelte` renders "could not render this window — `<key>`". One card lost, wall intact. Same guarantee as dash.py:3133-3138, and unlike a `tick()`-level guard it does not present as a permanent freeze |
| A poll raises | Caught in the poll thread; `snap.stale = True`, `polls_ok` not incremented, spinner freezes, cadence chip goes `.sl-stale`, timer survives. Logged once via `logging.exception` |
| SSE connection drops (laptop sleep, server restart) | `EventSource` auto-reconnects with its own backoff. The server's first frame on any connection is a **full** snapshot, so a reconnect needs no resync protocol. Until it arrives the client shows the last snapshot with the stale chip |
| Server not running when the browser opens | Static page loads from cache or fails outright; no half-state, because the page renders nothing before its first snapshot |
| Port already in use | `port_free` check before binding; the message names the port and suggests `--port`. `restart_offer` already refuses to offer an exec onto a taken port — an exec that fails to bind does not move the dashboard, it **ends** it |
| Config file is malformed TOML | `tt_config.load` returns DEFAULTS plus a stderr warning. Unchanged |
| Config write would corrupt the file | `tt_config.set_keys`'s read-back-and-compare net leaves it byte-identical and cleans up the temp file. Unchanged |
| Clipboard denied / insecure context (`server.host = 0.0.0.0` reached over a LAN IP) | The "copied" flash never fires, because it is inside `writeText().then()`. Same limitation as today (NiceGUI used the same API); `http://127.0.0.1` and `http://localhost` are secure contexts by spec, so the default path is unaffected |
| `open_command` not installed | `subprocess.Popen` raises `FileNotFoundError`, caught, warning printed, no crash. Unchanged |
| Hostile payload to `/open` (`javascript:`, `..`, a symlink escaping a root, a filename full of shell metacharacters) | Confinement re-derived server-side from scratch; scheme allowlist; argv list; the AST scan bans `shell=` anywhere in the file. Unchanged logic, moved file |
| Cross-origin POST from another browser tab | 403 on a foreign `Origin`. New guard, added because the POST endpoints are a new trust boundary |
| `web/dist/index.html` missing (fresh checkout mid-rebase, someone cleaned it) | `tt_web.py` exits before binding: `error: web/dist not built - run tools/build-web.sh (needs Node >=20), or use table-talk serve --nicegui` |
| **Missing Node** | Cannot happen at runtime. Node is a maintainer/CI build tool only; the shipped artefact is static files. This is the design's headline property, not a mitigation |
| Missing `uv` | Cannot happen after phase 5 — nothing in the runtime path uses it |
| No browser (headless box, SSH) | `--no-browser` prints the URL and serves anyway; `server.host = 0.0.0.0` plus the existing "no password" warning already covers the remote case |

---

## Testing

Nothing new to install. Same shape as the four selftests the repo already has.

**Python, unchanged and still authoritative:**
- `bin/table-talk --selftest` — untouched.
- `bin/tt_model.py --selftest` — **untouched**. Every pin in ui.md §"Behaviours
  pinned by which selftest" for `fold`, `parse_stem`, `percent`, `blocked_by`,
  `progress_pct`, `summarize`, `roll_up`, `group_sessions`, `sort_groups`,
  `merge_projects`, `weight`, `pack`, `art_spans`, `row_text`, `parts`,
  `marked`, `url_spans`, `path_spans`, `project_roots` keeps running against
  the same code the GUI uses. This is the entire argument for this design in
  one line.
- `bin/tt_config.py --selftest` — one path edit (the DEFAULTS-vs-stylesheet
  cross-check now reads `web/src/app.css`), plus the `theme_css` tests that
  move in with the function. All fifteen themes, all WCAG floors, all
  `set_keys` line-surgery pins, all `form_fields()` pins: unchanged.

**New: `bin/tt_web.py --selftest`** (~200 lines, stdlib `unittest`-free,
`assert`-based like its siblings). Starts a real server on port 0 in a thread
and drives it with `http.client`. Pins:
- `build_snapshot` over a seeded temp `DATA_DIR`: window keys, column lists,
  tally numbers, `empty_reason`, `newest_open_action`, `blocked_on_open` across
  two files.
- **Hash stability**: two `build_snapshot` calls over unchanged files produce
  byte-identical JSON — the guard that the push suppression actually works.
- **No relative time leaks**: assert the serialized snapshot contains no key
  matching `ago|since|_ago` and no value matching `\d+[smhd] ago`. This is the
  pin that keeps the hash meaningful.
- `/ui` rejects an unknown key with 400 and a wrong-typed value with 400; a
  known key round-trips through `ui.json` and appears in the next snapshot.
- `/open` refuses `javascript:alert(1)`, refuses `file:///etc/passwd`, refuses
  a path outside `ROOTS`, refuses a symlink resolving outside, and accepts a
  real file inside `DATA_DIR` (with a fake launcher).
- `/open {"transcript": "..."}` refuses an ambiguous 4-char prefix.
- **Origin guard**: `Origin: https://evil.example` → 403 on every POST;
  absent `Origin` → allowed; own origin → allowed.
- **AST scan**: no `shell=` keyword anywhere in `tt_web.py` (the same
  mutation-proof guard dash.py carries, kept because the bug it guards is real).
- `restart_offer` silent when ports agree / when `--port` was explicit;
  refuses to offer onto a taken port.
- `ensure_config` creates from the commented example and never rewrites an
  existing file.
- A missing `web/dist/index.html` exits with the named message.

**JS, only where logic can break:** `node --test web/test/*.test.mjs` over
`lib/fmt.js` and `lib/keymap.js` — `ago` boundaries, `hm` padding, `blocks`
cell snapping, `bar_for` scaling at `MAX_CELLS`, `live_delay` returning null
for a stale or future reading, `cols_for`'s narrow clamp beating the stored
preference, `parts` casing/overlap, `abbrev`. ~60 assertions. Run only when
Node is present, which is true in CI and on a maintainer's box; `./test.sh`
skips them with a printed note otherwise, because the shipped product does not
need Node.

**No component-render test framework.** No vitest, no jsdom, no
`@testing-library/svelte`. The components are declarative; the logic that can
break lives in Python (tested) and in `lib/*.js` (tested). Add a component test
the first time a component-level regression actually ships — not before.

**`./test.sh`** during migration:
```sh
python3 "$here/bin/table-talk"   --selftest
python3 "$here/bin/tt_model.py"  --selftest
python3 "$here/bin/tt_config.py" --selftest
python3 "$here/bin/tt_web.py"    --selftest
command -v node >/dev/null && node --test "$here/web/test/" || echo "node absent - skipped web/test"
uv run --script "$here/bin/table-talk-dash.py" --selftest   # deleted in phase 5
```
After phase 5 it loses the last two lines' `uv` and the `tt_jobs` line, and the
script no longer needs `uv` at all.

**CI** (`.github/workflows/test.yml`): the existing `test` job keeps running
`./test.sh` (it needs `uv` only until phase 5). A new `web-build` job pins the
committed artefact against its source:
```yaml
  web-build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: '22', cache: npm, cache-dependency-path: web/package-lock.json }
      - run: npm ci --prefix web
      - run: npm run --prefix web build
      - run: git diff --exit-code -- web/dist   # committed dist must match its source
```
The `cli-oldest-python` job is unchanged. `tt_web.py` needs 3.11 (`tomllib`,
via `tt_config`) — the same floor `tt_config` already has, so no promise moves.

---

## Migration path

Each phase ends with a working product. The NiceGUI dashboard runs untouched
through phase 4 and is deleted only in phase 5.

**Phase 0 — scaffold (0.5 d).** `web/package.json` (three dev deps: `vite`,
`svelte`, `@sveltejs/vite-plugin-svelte`), `web/vite.config.js`
(`build.outDir: "dist"`, no hashed filenames so the diff-check is readable),
`web/index.html`, `web/src/main.js`, `tools/build-web.sh`. Nothing ships.
Repo still works exactly as before.

**Phase 1 — read-only wall (3 d).** `bin/tt_web.py` with `GET /`,
`GET /assets/*`, `GET /events`, the poll thread, `build_snapshot`, and
`ensure_config`. Svelte: `stream`, `fmt`, `Wall`, `Window`, `Section`, `Row`,
`Art`, `Statusline`, `app.css` (copied from `bin/tt.css`). `table-talk web`
launches it. No writes, no `/ui`, no drawer. **Product: a second, read-only
view of the same data, side by side with `table-talk serve` on another port.**

**Phase 2 — interaction (3.5 d).** `POST /ui` + `ui.json`; `keymap`, `seen`,
`Drawer`, `Reply`, `Diagram`; marks/folds/zoom/scope/sort/needs-me/merge;
clipboard; change gutters; `POST /open` with links and the transcript button;
themes (all 15, three modes). **Product: feature-comparable; a user could
switch and only miss the settings dialog.**

**Phase 3 — settings and the parity sweep (2 d).** `Settings.svelte`,
`POST /settings`, `POST /restart`, the port-mismatch segment, config mtime
reload, `Toast`, tab title, keys dialog, `docs/demo` verification. Then walk
this document's parity table row by row with both dashboards open side by side
on two ports. **Product: parity claimed and checked.**

**Phase 4 — flip (1 d).** `table-talk serve` launches `tt_web.py`;
`table-talk serve --nicegui` keeps the old one for exactly one release.
`install.sh` chmods `bin/tt_web.py`. README rewritten (Dashboard, Themes,
Configuration sections; the "needs uv" sentence deleted). CI gains `web-build`.
`./test.sh` runs both selftests. **Product: the new GUI is the default; the old
one is one flag away.**

**Phase 5 — delete (0.5 d, one release later).** Remove
`bin/table-talk-dash.py` (3257 lines), `bin/tt.css` (now `web/src/app.css`),
`bin/tt_jobs.py` (33 KB, its only caller gone), `serve --nicegui`, and the
`nicegui` + `claude-agent-sdk` dependencies with the PEP 723 header that
carried them. `test.sh` and the runtime path no longer mention `uv`.

**The repo at the end:**
```
bin/table-talk        CLI (unchanged contract, +cmd_web, serve repointed)
bin/tt_web.py         ~450 lines, stdlib only, --selftest
bin/tt_model.py       unchanged
bin/tt_config.py      +theme_css, one path edit in selftest
bin/themes.json       unchanged
bin/tt-beat, tt-ref   unchanged
web/                  package.json, vite.config.js, index.html,
                      src/{main.js, app.css, lib/*.js, *.svelte} ≈ 1400 lines,
                      test/*.test.mjs, dist/ (committed)
tools/build-web.sh    npm ci && npm run build
install.sh            unchanged except one chmod target
.github/workflows/    test.yml + the web-build job
```
Commands: `table-talk serve` (the GUI), `table-talk web` (alias, or dropped),
`table-talk url`, everything else unchanged. Runtime dependencies:
`python3 >= 3.11` and a browser. Build dependencies (maintainers and CI only):
`node >= 20`.

Net: **−3257 lines of NiceGUI Python, −33 KB of dead job runner, −2 pip
dependencies, −uv from the runtime path; +450 lines of stdlib Python, +~1400
lines of Svelte/JS, + a committed build artefact.**

**Phase N — the native experiment (2 d, gated, not scheduled).** Under
`web/native/`: `render()` from `gpuix-svelte` mounting a `<Wall>` variant
against `new EventSource("http://127.0.0.1:8731/events?w=1400")` from Node 22+.
It needs no model port and no second server — the native window is just another
client of `/events`, and `bin/tt_web.py --no-browser` is its backend. Gate: all
three of (a) sveltejs/svelte#18511 merged and in a released Svelte, (b) GPUI's
Linux Blade→wgpu rewrite landed, (c) a headless test renderer on Linux so CI
can execute it. Until all three hold this stays a spike on a branch, and
nothing in phases 0–5 depends on it.

---

## Distribution

There is nothing to distribute. That is the section.

- **What a user installs:** `git clone && ./install.sh` — unchanged. The script
  symlinks `bin/table-talk` into `~/.local/bin`, symlinks `skill/`, makes the
  data dir, and installs the heartbeat hook. It gains nothing and loses the
  `uv`-bootstrap surprise on first `serve`.
- **Runtime requirements:** `python3 >= 3.11` and a browser. Both platforms
  ship the first; the second is the user's own. `jq` is still needed by the
  hooks, as today.
- **Linux and macOS:** identical, because nothing platform-specific is shipped.
  No prebuilt native addon with a per-arch matrix (gpuix-svelte has no
  linux-arm64 and no darwin-x64), no Vulkan/wgpu dependency, no Wayland/X11
  divergence, no font-file loading question. `links.open_command` already
  defaults per platform and is the only OS branch in the whole product.
- **Size:** the committed `web/dist/` is ~120 KB of app bundle plus
  `mermaid.min.js` at ~2.8 MB. `git clone` grows by ~3 MB, once. Mermaid is
  lazy-imported, so a wall with no diagram never fetches it. The honest
  alternative — not committing `dist/` and requiring Node at install — costs
  every user a Node toolchain to read a work log; not worth it. Compare:
  gpuix-svelte's answer is an ~80 MB binary per platform, built on the host,
  with no cross-compilation.
- **Signing / notarization:** none, because there is no binary and no bundle.
  No Developer ID, no `notarytool`, no Gatekeeper prompt, no Authenticode, no
  AppImage or `.deb`.
- **Updates:** `git pull`. The committed `dist/` comes with the source that
  produced it, and CI proves they agree.

---

## What the portable subset costs (the honest part)

The brief asks for components written to a DOM/GPUI-portable subset. Here is
what that actually buys and costs, measured against gpuix.md's constraints.

**Free, and better DOM code anyway — adopted:**
- No `bind:` — explicit `on:input` handlers. Two extra lines per input.
- No `@html` — `{#each parts(t,q)}` instead of `marked()`. This *deletes* the
  48,000-case escaping property test, because the escape happens structurally.
- `px` units and flat class names throughout. Trivial in a 9–12 px monospace UI
  that never used `rem` for anything meaningful.
- Explicit `{#if}` instead of `display:none`. Already how the code reads.

**Expensive, and therefore not adopted:**
- **The sub-row tree guide.** It is one continuous `::before`/`::after` rule
  with a corner on the last line, and it is that way because a per-row `├`/`└`
  glyph broke when `why` wrapped. GPUI has no pseudo-elements: the portable
  version is real positioned `<div>`s per sub-row, which is more markup and
  reintroduces exactly the wrapping fragility the current rule was written to
  kill. Kept as CSS.
- **Every animation.** The bell's `steps(2,start)` blink, the `.scan` sweep's
  staggered delays, and above all the progress pulse's **negative
  `animation-delay`** — a bar that starts partway through its own lifetime and
  finishes without any repaint. GPUI has no CSS animations at all, only
  `motion={{}}` tweens on six properties; the negative-delay trick is not
  expressible, and the portable version is a `setInterval` per visible bar.
  That is worse code to buy a phase that may never happen. Kept as CSS.
- **Theming.** `prefers-color-scheme` is an `@media` rule, refused at compile
  time by gpuix-svelte. The portable version is JS reading the OS theme and
  calling `set_css_vars()`. Kept as CSS; the native build would need its own
  theme bridge, which is ~20 lines when and if it exists.
- **Mermaid's `!important` token overrides** on `.mmd .node rect` and friends.
  Descendant combinators, refused. And there is no SVG DOM in GPUI to style
  regardless — the native answer is pre-rendering to PNG out of process, which
  is a different feature, not a portable component. Kept as CSS.
- **Scrolling.** `overflow: auto` is a silent no-op in GPUI; three places
  (wall, drawer tree, art panel) would need `<Scroller>` wrappers, one of which
  (`virtual`) changes the component tree's shape. Kept as CSS.

**So: about 30% of `app.css` — roughly 150 of `tt.css`'s 482 lines — is not
portable and is deliberately not made portable.** The portable-subset
discipline is worth following for the *component structure* (props, events,
`{#each}` shapes, no `bind:`), which is the part a native build would actually
reuse; it is not worth following for the styling, which a native build must
rewrite anyway.

**Is the native phase real?** Not today, and the design says so rather than
pretending. Five specific reasons, all from gpuix.md and landscape.md:
1. gpuix-svelte cannot use published Svelte at all. It vendors
   `svelte-5.57.0-ff9658a.tgz`, a build of an **unmerged, weekly force-pushed**
   4-PR stack (#18511). If that PR changes shape or stalls, table-talk's GUI
   is pinned to a private fork of Svelte forever.
2. GPUI is pre-1.0 with documented routine breaking changes, and its **Linux
   renderer is mid-rewrite** from Blade to wgpu (zed#46758).
3. **CI cannot test it on Linux.** `hasTestGpuixRenderer()` is `false`; `new
   TestGpuixRenderer()` throws by design. Fourteen of gpuix-svelte's fifteen
   own test scripts do not run on this machine. table-talk's entire quality
   model is "every file pins its behaviour in a selftest and CI runs them" — a
   GUI CI cannot execute is a GUI whose regressions ship.
4. **This UI is made of glyphs** (`● ▶ ⏸ ◉ ❯ ▾ ▸ █ ░ ▓ ▉ ✕ ⚙ ◐`, plus
   box-drawing in every sketch). `@font-face` is refused at compile time — only
   OS-installed fonts reachable by name — and there is no documented fallback
   *stack*, while the current `--mono` stack exists precisely because coverage
   order matters. Add cosmic-text's open COLRv1 bug on Linux.
5. Clipboard does not exist in `@gpuix/native`; the workaround shells out to
   `wl-copy`/`xclip`/`xsel`, none of which is guaranteed installed.

The gate above is the honest form of "later". The web design does not depend on
any of it clearing — and if it does clear, the native window is two days of
view code, because this design already put the model on the other side of a
socket.

---

## Risks, with mitigations

| Risk | Mitigation |
|---|---|
| **Committed `dist/` drifts from its source** — the classic vendored-artefact rot | The `web-build` CI job rebuilds and `git diff --exit-code -- web/dist`. A drifted commit fails the PR |
| **`git clone` grows ~3 MB from `mermaid.min.js`** | Accepted, once, and it is lazy-imported so it costs no runtime for a diagram-free wall. If it ever grates, the cut is rendering diagrams as monospace source (they are logged once and read many times, per landscape.md §5) |
| **Full-snapshot pushes get large** on a very heavy wall | Measured, not assumed: ~250 KB at 40×30 rows over loopback every 2 s. If a real user's wall triples that, the first fix is dropping `done` rows and glossary bodies from the snapshot until their section is open (the client already knows), not a delta protocol |
| **The snapshot hash fires on something time-derived and pushes every tick** | Pinned by a selftest that asserts the serialized payload contains no relative-time key or value. Absolute-timestamps-only is a rule with a test, not a convention |
| **A rewrite loses a subtlety** the current dashboard learned the hard way | This document's parity table is the checklist, and phase 3 is a dedicated sweep with both dashboards open side by side. The costliest subtleties (`fold` edge cases, `pack` determinism, `art_spans` ranges, `path_spans` confinement, `set_keys` line surgery, the WCAG floors) are **not rewritten at all** — that is the point of the design |
| **New trust boundary**: POST endpoints that launch processes and rewrite config | Origin guard + key allowlist + server-side re-derivation of every confinement decision + the `shell=` AST ban, all selftested |
| **`http.server` is not a production server** | It does not need to be. Loopback, one user, `ThreadingHTTPServer`, a handful of connections. Same posture as the current NiceGUI/uvicorn setup, which also documents "no `storage_secret` needed" for the same reason. `server.host = 0.0.0.0` still prints the same "no password" warning |
| **SSE through a corporate proxy / an odd browser** | Loopback, so no proxy. `EventSource` is supported in every browser shipped since 2011 except IE |
| **Svelte 5 itself is a dependency at build time** | Published, GA since 2024-10, currently 5.45. Unlike the gpuix path, this is the *registry* Svelte with no vendored fork and no export-condition dance. If Svelte 6 breaks the build, the committed `dist/` keeps shipping while the source is fixed |
| **Node 20+ needed to rebuild** | Maintainers and CI only. A user who never touches `web/` never sees Node |
| **Nobody ever builds the native phase** | Then nothing is lost: phases 0–5 are a complete product on their own terms. This is the design's main defence against the gpuix risks — it does not spend anything on them upfront |

---

## Effort

Honest engineer-days, for someone who already knows this codebase.

| Phase | Days | What dominates |
|---|---|---|
| 0 — scaffold | 0.5 | vite config, build script, first mount |
| 1 — read-only wall | 3.0 | `build_snapshot` (~200 lines, the whole model surface), SSE plumbing, `Wall`/`Window`/`Section`/`Row`/`Art`, porting `tt.css` |
| 2 — interaction | 3.5 | `Drawer` (the densest component: tree, rail, meters, auto-fold rules), keymap, watermarks, links, themes, mermaid |
| 3 — settings + parity sweep | 2.0 | Settings form, restart flow, then the row-by-row sweep — this is where a rewrite actually costs |
| 4 — flip | 1.0 | README, CI job, `test.sh`, `install.sh`, `serve` repoint |
| 5 — delete | 0.5 | Removing 3257 lines and two dependencies |
| **Total** | **10.5** | |
| Contingency | +2.0 | Parity items the sweep finds that this table did not |
| **Honest range** | **10–14** | |
| Native spike (gated, not scheduled) | +2.0 | View layer only — no model, no second server |

For comparison, a native-first design pays for a full TypeScript port of
`tt_model` (twenty functions plus every pin listed in ui.md §3, realistically
4–6 days on its own) **before** writing any UI, and then cannot run its own
tests in CI on Linux.

---

## What ponytail cuts

Deletions this design makes, each because the thing it solved stopped existing:

1. **The TypeScript model port.** Twenty functions and their pins. Python stays
   the model; the GUI is a client. This is the single largest cut and the
   reason the effort estimate is ten days rather than twenty.
2. **`REPLY_JS`** (dash.py:96-152, ~60 lines): the draft `Map`, the
   rAF-throttled `MutationObserver`, the value+selection restore, the
   mousedown escape hatch. All of it existed because the wall calls
   `container.clear()` every 2 s. Nothing clears now.
3. **`BLUR_JS`** (dash.py:255-260) and its `e.detail>=1` synthetic-click
   subtlety. NiceGUI's keyboard layer is gone, so the workaround is gone.
4. **Per-window paint signatures and the drawer signature**
   (dash.py:3097-3143, 2859-2962), plus `layout_key`'s repack guard and the
   "record the signature only after a successful paint" ordering pin. Svelte's
   keyed `{#each}` and `<svelte:boundary>` provide both properties by
   construction.
5. **`marked()`'s HTML escaping and its 48,000-case property test.** Replaced
   by `{#each parts(...)}`, where the bug cannot occur. `parts()`'s own pins
   stay.
6. **`TAB_TITLE_JS` / `TOAST_JS`'s regex-over-rendered-DOM `MutationObserver`s**
   (dash.py:158-205). The tally arrives as three integers.
7. **`WIDTH_JS`'s `ResizeObserver` and clamp-flip tick** (dash.py:299-308,
   2361-2376). Three `matchMedia` listeners and a query parameter.
8. **The `.props()` AST injection scan.** No NiceGUI props exist. The `shell=`
   scan is kept, because that vector does still exist.
9. **The `NICEGUI_STORAGE_PATH`-before-import hazard** and its AST-order pin
   (dash.py:22-36, 1849-1853). A JSON file has no import-time class attribute
   to race.
10. **`uv`, `nicegui`, `claude-agent-sdk`** from the runtime path, and
    `bin/tt_jobs.py` with them — its only caller is the dashboard's own
    selftest since #201 removed the job modal.
11. **A delta protocol for SSE.** Full snapshot, hashed, pushed on change.
12. **A component test framework** (vitest / jsdom / testing-library). The
    logic that can break is in Python or in `lib/*.js`; both are tested with
    what is already installed.
13. **Tauri, Electron, and a Bun-compiled binary.** All three ship a shell
    around a page the user's own browser already renders — 5–200 MB, a Rust or
    Bun toolchain, and a signing story, to avoid typing a URL that
    `webbrowser.open()` types for you.
14. **A file watcher** (`inotify`, chokidar, `@parcel/watcher`). A handful of
    append-only files and a 2 s cadence that is already the product's
    documented behaviour.
15. **An `--app` browser-window flag** (`chromium --app=…`). One line, and
    nobody has asked for it. Add it when someone does.

Kept deliberately, and not simplified: `open_path`'s from-scratch confinement,
the `shell=` ban, the new Origin guard, `port_free` before offering a restart,
`set_keys`'s read-back safety net, the WCAG contrast floors, `fold`'s per-line
tolerance, and the clock-before-file-reads ordering in the poll loop.
