# shell — native shell, Python brain

A Svelte 5 front end that owns **no model at all**. `bin/table-talk` keeps
folding the JSONL, keeps every `tt_model`/`tt_config` selftest pin byte-for-byte
(neither file is edited), and emits a fully-resolved **frame** — rows already
split into spans, flags already computed, columns already packed. The shell maps
that frame onto elements and sends back **intents**. No fold, no `pack`, no
`art_spans`, no `path_spans` in TypeScript. Ever.

---

## Goal

1. table-talk becomes its own GUI: one command (`table-talk gui`) opens a window,
   no browser tab, no `uv`, no NiceGUI, no uvicorn, no websocket stack.
2. The JSONL format, `bin/table-talk`'s CLI surface and `skill/SKILL.md` are
   untouched contracts. `table-talk url` still prints a URL and
   `curl -sf "$(table-talk url)"` still answers 200 — SKILL.md:222 hard-codes
   that liveness check, so **the GUI must keep an HTTP face on `server.port`**.
   That single constraint decides most of this design.
3. Every dashboard feature in `reads/ui.md` survives, or is cut with an argument.
4. `bin/table-talk-dash.py` keeps working, unmodified, until the day it is deleted.
5. Linux and macOS both work, with automated render tests on both.

Non-goal: sharing components with a web app, a plugin API, multi-user, remote access.

---

## Architecture

### Two processes, one URL

```
┌─────────────────────────────── brain (python3, stdlib only) ────────────────┐
│ bin/table-talk state                                                        │
│   tt_model.fold_cached ──► tt_wall.Wall  ──► frame dict ──► json.dumps      │
│   ~/.local/share/table-talk/*.jsonl        (per connection)                 │
│   .beat/, ~/.claude/projects/*.jsonl                                        │
│   tt_serve.py: http.server.ThreadingHTTPServer on 127.0.0.1:8731            │
│      GET  /            → bin/web/index.html   (also the liveness 200)       │
│      GET  /state?t=…   → text/event-stream, one frame per event             │
│      POST /do          → one intent, X-TT-Token required                    │
│      GET  /app.js /app.css /tt.css /mermaid.min.js /themes.css              │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │ loopback HTTP
┌──────────────────────────────────────┴──────────────────────────────────────┐
│ shell (Tauri 2, ~120 lines of Rust, no JS of its own)                       │
│   WebviewWindow → http://127.0.0.1:8731/                                    │
│   the Svelte 5 bundle runs inside; EventSource in, fetch out                │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Who reads the JSONL:** only Python. `tt_model.fold_cached` on every `*.jsonl`
in `DATA_DIR`, exactly as `poll()` does today (dash.py:3036), with the same
`(mtime,size)` cache. The shell never opens a file in the data dir.

**Who owns state:** Python owns both halves.

| state | owner | lifetime |
|---|---|---|
| folded model (`states`, `groups`, `wall_states`) | brain, per tick | process |
| shared view state — `marks`, `folds`, `groups_folded`, `zoomed`, `scope`, `needs_me`, `current`, `cols`, `sort`, `drawer_open`, `merged`, `seen`, theme mode | brain, one `Store` | persisted `~/.local/share/table-talk/.ui/wall.json` |
| per-viewer state — `seen_at`, `opened_ts`, `touched`, `wall_width`, `query`, per-window section toggles | brain, one `Wall` **per SSE connection** | connection |
| textarea drafts, scroll offsets | shell | tab/window |

The per-connection `Wall` is the exact analogue of today's NiceGUI
closure-per-client (dash.py:2352-2357): two open windows get independent
watermarks and independent wall widths, shared marks/folds/scope. Nothing about
that behaviour changes.

**How updates flow:**

```
timer (server.poll_seconds, default 2.0)  ─┐
POST /do  → Wall.apply(intent)            ─┴─► Wall.frame(now) ─► SSE "data: {…}\n\n"
```
A frame is pushed on the tick **and immediately after any intent**, so a keypress
repaints in ~5 ms instead of waiting up to 2 s as it does today. That is free
(the fold is already cached) and is the one user-visible improvement in the whole
migration.

**How the Python CLI launches it** — `bin/table-talk` gains two subcommands,
both reusing existing helpers:

```python
gu = sub.add_parser("gui", help="open the table-talk window")
gu.add_argument("--port", type=int)
gu.add_argument("--force", action="store_true")
st = sub.add_parser("state", help=argparse.SUPPRESS)   # the brain; gui/serve exec it
st.add_argument("--port", type=int)
```

* `table-talk gui` → `serve_refusal(os.environ, force)` first (unchanged
  reasoning: it never returns, so a Claude session must not run it), then forks
  the shell and `execv`s the brain:

  ```python
  def cmd_gui(port, force=False):
      if (msg := serve_refusal(os.environ, force)): sys.exit(msg)
      url = dashboard_url()
      shell = os.environ.get("TABLE_TALK_SHELL") or shutil.which("table-talk-gui")
      if shell:
          subprocess.Popen([shell, url], start_new_session=False)
      else:
          import webbrowser; webbrowser.open(url)   # no shell installed: still works
      cmd_state(port)          # execs nothing; becomes the server, like serve does
  ```
  The shell is spawned *first* and retries the URL until the brain binds — it
  polls `GET /` for up to 10 s. Simpler than a handshake and it also covers
  "the brain was already running": a second `table-talk gui` finds the port
  taken, skips starting a brain, and just opens a window on the running one.

* `table-talk serve` keeps its name and its meaning. During phases 0–4 it still
  execs `table-talk-dash.py` under `uv`. In phase 5 its body becomes `cmd_state`
  and the `uv` requirement disappears from the repo.

* `table-talk url` unchanged (`tt_config.load()["server"]["port"]`).

**Why HTTP rather than the stdio/NDJSON bridge the brief suggested.** SKILL.md
already requires a socket on `server.port` answering 200. Given that socket must
exist, a second stdio protocol is a second thing to write, test and debug for
zero capability. The ladder stops at rung 3: `http.server` + a text/event-stream
generator is ~180 lines of stdlib, and it makes the shell replaceable by any
browser — which is what makes the migration reversible and the CI render test
possible. Stdio would also have forced Tauri to own a child process (spawn,
reap, restart, zombie on SIGKILL); pointing a webview at a URL owns nothing.

---

## Components

Python side (new files, stdlib only, each with a `--selftest`):

**`bin/tt_wall.py`** (~650 lines) — the brain's view layer, lifted out of
dash.py's `poll()`/`do()`/row builders with every `ui.*` call removed.
* `Store` — the `app.storage.general` replacement. `dict` + atomic
  `os.replace` write of `.ui/wall.json`, debounced to at most one write/second.
  Same keys, same `tt.` prefix dropped (the prefix existed to share a namespace
  with NiceGUI).
* `Wall(store, cfg)` — per connection. Holds `seen_at`, `opened_ts`, `touched`,
  `wall_width`, `query`, `sections`. Methods: `apply(intent)`, `frame(now, states)`.
* `frame()` — does exactly what `poll()` does today up to the point where it
  would touch an element: group → sort → `apply_fold_rules` → merge/flat →
  visible → `cols_for` → `layout_key` → `pack`, then serialises. Every helper
  ui.md lists as "portable" (`tally_text`, `blocks`, `bar_for`, `_dim`/`_hits`,
  `next_sort`, `abbrev`, `default_cols`, `cols_for`, `layout_key`, `ago`/`stamp`/
  `hm`, `live_delay`, `changed_ids`, `link_roots`, `link_spans`, `open_rows`/
  `done_rows`/`term_rows`/`diagram_rows`, `form_updates`, `needs_restart`,
  `coerce`, `theme_css`) moves here verbatim. Depends on: `tt_model`,
  `tt_config`, `tt_jobs`.
* `KEYMAP` — the same dict, still the single source for keys *and* chips.

**`bin/tt_serve.py`** (~250 lines) — `ThreadingHTTPServer` +
`BaseHTTPRequestHandler`. Static files out of `bin/web/` (whitelist of five
filenames, never a path join on the request), `/state` SSE, `/do` POST.
Owns the token, the `Origin` check, the shutdown on SIGINT, and the live rebind
when `server.port` changes on disk. Depends on: `tt_wall`, `tt_config`.

**`bin/web/`** — committed build output: `index.html`, `app.js`, `app.css`,
`tt.css` (a symlink-free copy step in the build), `mermaid.min.js`.
~1.5 MB, of which mermaid is 1.1 MB.

Svelte side (`ui/`, Svelte 5 + Vite, **build-time only**):

| unit | what it does | used by | depends on |
|---|---|---|---|
| `src/frame.svelte.js` | `EventSource('/state?t=…')` into `$state frame`; `send(intent)` = `fetch('/do',{headers:{'X-TT-Token'}})`; reconnect backoff 0.5→8 s; exposes `stale` | everything | nothing |
| `src/App.svelte` | `.tt-main` grid (drawer + wall) + statusline; `on:keydown` → `{do:'key',k}` unless `event.target` is input/textarea/button or the keys dialog is open; window/scroll/click listeners → `{do:'touch'}`; `ResizeObserver` on `.wall` → `{do:'width'}` when the narrow clamp flips | root | frame |
| `src/Wall.svelte` | renders `frame.columns` (`string[][]`, already packed) as N `.col` divs of `<Window>`; empty-wall message from `frame.wall.empty` | App | Window |
| `src/Window.svelte` | `.win` card: titlebar (project, `:sid` button, age, `! # M Z * ◉` flags, M/Z/▾ buttons), body of `<Section>`; click anywhere → `{do:'current'}` | Wall | Section |
| `src/Section.svelte` | `❯ title (n) ▾/▸` button; open → rows, shut → the `█░` glyph bar; `sec.forced` overrides the user toggle for one render | Window | Row |
| `src/Row.svelte` | one component, `{#if row.kind}` over action/task/term/diagram/done; renders `row.cells` in order — the frame decides that `int` precedes `why`/`rec` | Section | Spans, Meter, Reply, Diagram |
| `src/Spans.svelte` | `{#each spans as [text, kind]}` → `<span class={kind}>`; kinds: `''`, `st` (art structure), `tt-hit` (query match), `lnk` (clickable, carries `target`). **Never `{@html}`** — Svelte escapes text nodes, so `tt_model.marked`'s escaping proof is replaced by construction | Row | frame |
| `src/Meter.svelte` | blocked banner ⏸ / `.scan` sweep / `blocks()` glyph bar + pct + `as of HH:MM`, with `animation-delay: -Ns` straight from `row.meter.delay` | Row | tt.css |
| `src/Reply.svelte` | `<textarea>` bound to a module-level `Map<id,string>` + copy button (`navigator.clipboard.writeText(row.copy).then(flash)`) | Row | — |
| `src/Diagram.svelte` | `mermaid.render(id, src)` → `{@html svg}`. The **only** `@html` in the app; CI greps for exactly one | Row | mermaid.min.js |
| `src/Drawer.svelte` | expanded tree (filter input, hit count, theme toggle, `sort:` row, projects/sessions with `meter_row`) and the 54 px rail; footer links from `frame.drawer.ctx` | App | frame |
| `src/Statusline.svelte` | spinner frame (`frame.status.spin`, advanced by the brain only on a successful tick), cadence, tally, scope chip, `cols 1 2 3`, one chip per `frame.status.keymap` entry, clock; toast `$effect` on a rising `open` count; `document.title` = `(N) table-talk` | App | frame |
| `src/Settings.svelte` | dialog built from `frame.settings.fields` (= `tt_config.form_fields()`); save → `{do:'config', set:{…}}` | Drawer | frame |
| `src/Keys.svelte` | `?` dialog from `frame.status.keymap` | App | frame |

Shell side:

**`shell/src-tauri/src/main.rs`** (~120 lines) — `WebviewWindowBuilder` at the
URL from `argv[1]`, title `table-talk`, min 900×600, size/position remembered in
`tauri-plugin-window-state`. Two extra behaviours: retry the URL until the brain
answers (10 s), and a `page_load` handler that re-navigates if the webview lands
on an error page. No IPC, no commands, no sidecar, no JS injected.
Builds to `table-talk-gui`.

---

## Data flow

```
*.jsonl  ──tt_model.fold_cached──►  states{stem: {id: ev}}
                                     │
   group_sessions ─ sort_groups ─ apply_fold_rules ─ merge_projects
                                     │
   weight ─ pack(visible, cols, weights, marks)  ──► columns[[key]]
                                     │
   per row: art_spans, link_spans, parts(q)  ──► spans[[text,kind]]
   per row: progress_pct, blocked_by, blocks() ──► meter
   per window: changed_ids, live_sessions, transcripts ──► flags
                                     │
                       json.dumps(frame)  ──SSE──►  Svelte $state ──► elements
```

Frame shape (abridged; `v` is bumped on any incompatible change and the shell
refuses a version it does not know, showing "update table-talk"):

```json
{"v":1,"t":1757337600.4,"sig":"…",
 "wall":{"cols":2,"columns":[["gpn-yeast"],["table-talk"]],"empty":null},
 "windows":{"gpn-yeast":{
   "sig":"9f3c…","project":"gpn-yeast","sid":"4f2a","tx":"/home/…/4f2a….jsonl",
   "age":"3m","flags":{"bell":true,"actv":false,"mark":false,"zoom":false,
                       "cur":true,"beat":true},
   "sections":[{"id":"act","title":"actions --open","n":2,"open":true,
                "forced":false,"bar":null,"rows":[
     {"kind":"action","id":"a1b2","copy":"SESSION: 4f2a - ID: a1b2",
      "changed":"act","cursor":true,
      "cells":[{"c":"title","spans":[["Pick a fold cadence",false]]},
               {"c":"int","spans":[…]},{"c":"why","spans":[…]},{"c":"rec","spans":[…]},
               {"c":"art","lines":[[["┌──",true],[" alpha ",false],["──┐",true]]]}],
      "reply":true}]}]}},
 "drawer":{"open":true,"sort":"recent","hits":"4/61 rows match","projects":[…],"ctx":[…]},
 "status":{"spin":3,"cadence":"Every 2s · last 14:22:07","clock":"14:22:07",
           "tally":{"open":3,"running":1,"blocked":1,"text":"●3 open  ▶1 running  ⏸1 blocked"},
           "scope":null,"cols":2,"keymap":{"m":{"label":"mark","on":false},…},"stale":false},
 "theme":{"mode":"dark","vars":{"--act":"#fb4934",…}},
 "settings":{"fields":[…]}}
```

**Polling vs watching, and the 2 s cadence.** Keep the poll. The brain re-globs
`DATA_DIR` and `fold_cached`s every tick; `fold_cached` re-parses only on
mtime/size change, which is why today's 2 s loop is free and why it stays free.
`server.poll_seconds` keeps its 0.2–∞ validation and its meaning. No `inotify`,
no watchdog dependency; `reads/landscape.md` §6 reaches the same conclusion and
a watcher would have to be debounced back to ~2 s anyway to avoid repainting
mid-write. The one addition — pushing a frame right after an intent — is what
makes the UI feel immediate without touching the cadence.

**Frame skipping.** Python already computes a per-window paint signature
(dash.py:3127). That value ships as `windows[k].sig`, and the connection keeps
the last frame's signatures: if every window sig, the drawer sig and the status
text are unchanged, **the tick emits nothing at all** (SSE goes quiet). An idle
wall costs one `stat()` per file per 2 s and zero bytes on the wire. When a frame
is emitted it is always complete — no deltas, no patch protocol.

---

## Feature parity

Every line of `reads/ui.md` §1, in its order.

### Wall structure, packing, columns
| feature | delivered by |
|---|---|
| window per session / per project | brain, `merge_projects` unchanged; `{do:'key',k:'u'}` toggles |
| greedy pack, marked first | brain, `tt_model.pack` unchanged; frame ships `columns` |
| cols 1/2/3, auto from width, stored pref is a maximum, NARROW forces 1 | `cols_for` in `tt_wall`; shell reports px via `{do:'width'}` from a `ResizeObserver`, same as `WIDTH_JS`, same flip-only firing |
| content-derived weight | `tt_model.weight` unchanged |
| re-pack only on `layout_key` change | `tt_wall` keeps `layout`; unchanged `columns` in the frame means Svelte's keyed `{#each}` moves nothing |
| zoom forces 1 column | brain |
| three empty-wall messages; filter never empties | `frame.wall.empty`; `_dim` still dims |

### Window titlebar flags
| feature | delivered by |
|---|---|
| `!` bell blinking `steps(2,start)` | `flags.bell` + `.bell` in tt.css, **unchanged CSS** |
| `#` activity, `M`, `Z`, `*` current | flags; `.win.cur>.win-t` tint + caret underline unchanged |
| `◉` beat, outside the paint signature | `live_sessions` in `tt_wall`, written to `flags.beat` after the sig is computed |
| project, `ix` transcript button, age, M/Z/▾ with tooltips | frame fields; `ix` click → `{do:'open', target: win.tx}` |
| click anywhere makes current | `{do:'current', key}` |

### Sections and collapse bars
| feature | delivered by |
|---|---|
| five sections in fixed order, diagrams only when ≥1 | `frame.windows[k].sections` — the brain decides the order |
| `❯ title (n) ▾/▸` buttons | `Section.svelte`; `{do:'section', key, sec, open}` |
| per-window persistence across rebuilds | now server-side per connection — strictly stronger than `container.tt_open` (survives a reload of the same connection's tab? no — same lifetime as today) |
| `ui.collapsed_sections` default `["glossary","done"]` | `tt_config` unchanged |
| `█░` glyph bar when shut, gone when open | `sec.bar` from `bar_for`, `MAX_CELLS=20` |
| query force-opens a section containing a hit, without touching the toggle | `sec.forced` — the brain sets it, the toggle is untouched (issue #57 pin moves to `tt_wall.selftest`) |

### Row anatomy
| feature | delivered by |
|---|---|
| action row: id, title, cursor `▉` on the single newest open action wall-wide, `int` **first**, `why`, `rec`, art, reply | `cells` array order is computed by the brain and pinned by selftest — a stronger pin than dash.py's AST call-count check |
| task row: id, what, meter, `int`, art, reply | same |
| term row: term as id-cell, intuitive as title, `def` | same |
| diagram row: title as id-cell, live mermaid | `Diagram.svelte` |
| done row: id still clickable, title, art, reply | same |
| one continuous CSS tree guide (`.sub::before/::after`), not per-row glyphs | **tt.css unchanged**, including the `margin-top`/`bottom:-Npx` bridge the dash selftest pins |
| id button with `sid` sub-line | frame `id`+`sid` |

### ASCII sketch two-ink rendering
`tt_model.art_spans` runs in Python; the frame carries `cells[].lines` as
`[[text, is_structure], …]` per line. `Spans.svelte` emits `<span>` /
`<span class="st">`. `.art`/`.art-in` CSS (`white-space:pre`, `inline-block`
centring, own `overflow-x`) is unchanged. Drawn on action, task and done rows —
enforced by a frame-content assertion instead of a call-count assertion.

### Progress bar, pulse, read_ts
`progress_pct`, `percent`, `blocks(pct,14)` all unchanged in Python. The frame
carries `meter:{pct, cells:[8,6], delay:-137.2, asof:"14:03", kind:"bar|scan|blocked"}`.
The negative `animation-delay` trick survives verbatim (it is CSS, and tt.css is
kept), so a bar still finishes on its own with no repaint. `as of HH:MM` stays
absolute for exactly the stated reason — with frame skipping, a row genuinely can
sit unrepainted for hours.

### Blocked-on
`blocked_by` / `open_action_ids` unchanged, still computed across every file.
`open_acts` stays inside the per-window signature so answering a blocker in
another project still repaints the task's window.

### Reply box
Kept on action, task and done rows. **The entire `REPLY_JS` mechanism is
deleted, not ported** — `MutationObserver`, the `requestAnimationFrame` throttle,
the caret save/restore, the `mousedown`-elsewhere give-up. All of it existed
because `container.clear()` destroyed the textarea every 2 s (dash.py comment
at 96-152). Svelte updates text in place inside a `{#each rows as row (row.id)}`
keyed block, so the DOM node, its value, its caret and its focus are never
touched by a frame. Drafts live in a `Map` for the case where a row leaves the
wall (scope/needs-me) and comes back. This is the largest single deletion in the
migration: ~60 lines of subtle JS replaced by a keyed each-block.

Copy button: `navigator.clipboard.writeText(row.copy)`, flash only inside
`.then()` — same promise-only guarantee. `row.copy` is minted in Python and is
only present when the id matches `^[0-9a-f]{4,}$`, so the guard moves from the
client to the producer.

### Change gutters and seen-watermarks
`changed_ids` per window against a per-connection `seen_at`, floor `opened_ts`,
`int(now)-1` advance, advance only when `touched`, only for windows that were on
the previous wall. `SEEN_JS`'s trigger set (click/keydown/scroll +
`visibilitychange` as a secondary) becomes four `addEventListener`s in `App.svelte`
sending `{do:'touch'}` at most once per second. Not persisted across reload —
the connection dies, the `Wall` dies. `.row.changed` / `.row.changed-job` colours
unchanged.

### Drawer tree, meters, filter that dims
Whole thing survives: 284/54 px widths, filter + hit count + theme toggle,
`sessions n · k projects`, the `sort:` cycle row, per-project fold triangle only
when >1 session, `meter_row` with greyed-at-zero badges and the `[#### ]NN%`
meter, click-to-scope, click-triangle-to-fold, the 54 px rail with `abbrev()`
tags, `apply_fold_rules` (first-ever-seen fold persisted in `seen`, rising-edge
force-open), the context footer (`nearest_claude_md` with its
never-above-`$HOME` symlink refusal, `~/.claude/CLAUDE.md`, `MEMORY.md`,
⚙ settings, raw settings path, refreshed `config.example.toml`), and the
dimming filter (`.tt-dim{opacity:.78}`, `.tt-hit`, `N/M rows match`,
scroll-first-hit-into-view / scroll-to-top on clear).

`filter_debounce_ms` moves to a plain `setTimeout` in `Drawer.svelte`. The
`.props()` injection hazard it was defending against **does not exist** here —
there is no props string to interpolate into. The dash selftest's AST ban on
non-constant `.props()` arguments therefore has no successor and needs none;
its sibling ban on `shell=` moves to `tt_serve.selftest`.

### Statusline
Spinner advancing only on a successful poll, cadence text, tally
(`●N ▶M ⏸K`, blocked subtracted from running, counted across every session),
scope chip + ✕, `cols 1 2 3` with the effective count highlighted, one chip per
`KEYMAP` entry except `filter`/`unzoom`, `.on` for `needs-me`/`merge`, live clock.
Tab title: `document.title = "(3) table-talk"` from `frame.status.tally.open` —
a `$effect`, no `MutationObserver` on `#tt-tally`. In the Tauri shell that string
is also the window title. Toast: a `$effect` that fires when `tally.open` rises
above the value it saw on its first frame — same baseline discipline, so a
reconnect never bursts.

**Cut: the port-mismatch segment and the restart offer.** `restart_offer`,
`port_free`, `do_restart`, `RESTART_KEYS`, `needs_restart` and the `os.execv`
all exist because `main()` reads config once at startup and moving ports means
replacing the process (dash.py:643-655, 2531-2545). The brain re-stats the
config every tick already; when `server.port` changes it closes the listener and
binds the new one in place, and the shell re-navigates. If the new port is taken
the bind fails, the old listener is kept, and the statusline says so — the same
information the offer was carrying, without a button, a socket probe or a
process replacement. **Argued cut, replaced by a better behaviour.**

### Keys
`KEYMAP` stays one dict in `tt_wall`, shipped in the frame and used to build both
the chips and the `?` dialog, so key and click still cannot diverge. `\ m z f s
/ ! u ? Escape` all unchanged. Key-repeat ignored (`event.repeat`), keys ignored
while an editable element or a dialog has focus.

**Cut: `BLUR_JS`.** It existed solely because NiceGUI's keyboard layer swallows
keystrokes while a `<button>` holds focus, and it had to distinguish a real
mouse click (`e.detail>=1`) from a synthetic Enter/Space one to avoid stealing a
keyboard user's focus ring. With an ordinary `window` keydown listener there is
nothing to swallow: a focused button and a working `m` key coexist. The
accessibility property it was protecting is preserved by *not needing it*.

### Merged vs flat, zoom/fold/mark, transcripts, links, copy format
All unchanged Python. `merge_projects` ties still resolve by higher `ts`
(#140 pin, in `tt_model`, untouched). Clicking a drawer session row while merged
still resolves to the project key before scrolling — `{do:'focus', key}` and the
brain answers with `frame.focus = "<project>"`, which `Wall.svelte`
`scrollIntoView`s once.

`transcripts()` still scans every `~/.claude/projects/*/*.jsonl` per poll and
still drops ambiguous 4-char prefixes. `url_spans`/`path_spans` still run in
Python; the frame ships resolved targets, and `{do:'open', target}` **re-derives
confinement from scratch at click time** exactly as `open_path` does today —
that is the whole reason opening stays a Python intent instead of a Rust or JS
`spawn`. `links.open_command` unchanged; launch is still an argv list, never
`shell=True`, and the AST ban moves with it.

Copy format (`SESSION: <sid> - ID: <id>` or the bare id) is produced in Python
and copied verbatim.

### Mermaid
The single biggest reason this design uses a webview. Everything survives
unchanged: bundled `mermaid.min.js` 11.16.1, `securityLevel:"strict"` restated,
`theme:"base"`, the per-render `%%{init:…}%%` directive with the app's mono stack
and `fontSize:"12px"`, **the no-hyphen rule** in `MERMAID_INIT`, the `.mmd .node
rect` / `.edgeLabel` / `.marker` / `rect.actor` / `text.actor>tspan` /
`.noteText>tspan` / `.note` / `.labelBox` `!important` overrides keyed on theme
tokens, the deliberate absence of a blanket `.mmd text{}` rule, and mermaid's own
client-side error graphic on a parse failure. Not one line of that has to be
re-derived, because tt.css and mermaid both come along.

### Settings dialog
Fields from `tt_config.form_fields()` — still no second hardcoded list. Save
sends only changed keys (`form_updates`), through `coerce` and
`tt_config.set_keys` line surgery, so comments survive. `ensure_config()`
unchanged, including the `config.example.toml` copy and the refreshed reference
beside it. `needs_restart` returns `[]` now (see the cut above) and the message
becomes "saved" — except for `server.port`, where it says which port it moved to.

### Themes
Mode toggle `◐/○/●` cycling system→light→dark, persisted. 15 bundled palettes in
`themes.json`, `adapted` records, `[theme.dark]`/`[theme.light]` overrides on top
of a named theme, `theme_css`'s emit-only-what-differs rule and its
re-validation of every value on the way out, the WCAG contrast floors in
`tt_config.selftest`, `--hover` deliberately not configurable. `ui.dark_mode()`
is replaced by `document.documentElement.classList.toggle('body--dark')` driven
by `frame.theme.mode` plus a `matchMedia('(prefers-color-scheme: dark)')`
listener for `system` — the browser answers the question the server could not.
`theme_css` output is served as `/themes.css` and re-fetched when
`frame.theme.vars` changes.

### UI-state persistence
`.ui/wall.json` (atomic write) replaces `app.storage.general`. Same key set.
`wall_width`, `seen_at`, `opened_ts`, `touched` stay unpersisted for the same
reasons. The `NICEGUI_STORAGE_PATH`-before-import ordering hazard **disappears by
construction** — there is no import-time path binding to get wrong. The AST
ordering pin that guarded it retires with dash.py.

### The 2 s poll and per-window paint guard
`tick()`'s try/except → a frozen spinner and `.sl-stale` cadence chip, kept
verbatim. The per-window try/except with `logging.exception("could not paint
window %s")` becomes a per-window try/except around *frame building* for that
window; a window that throws is emitted as
`{"error":"could not build window"}` and rendered as a card with that one line,
so one unrenderable row still costs only its own card. The signature is still
recorded only after a successful build.

### Heartbeat
`bin/tt-beat` untouched, `.beat/<sid>` untouched, `live_sessions(now, dir, 120)`
untouched including the future-mtime rejection and the silent empty-set on a
missing directory. `install-hook` untouched.

### The demo dir
`TABLE_TALK_DIR=docs/demo table-talk gui --port 8899` — same env var, same
mechanism, same ability to run beside a real instance on another port. It also
becomes the fixture for the CI render test.

### Jobs
`tt_jobs` is not touched. The jobs section, the `#` flag and the blocked banner
render from the frame like any other section. "Start work from the wall" becomes
`{do:'job', ...}` → `tt_jobs.start_job`, running in a thread in the brain exactly
as it runs in the NiceGUI event loop today. The PreToolUse gate is `tt_jobs`'
business and is unchanged. If the maintainer decides to drop the runner from the
public repo (as he did once before), deleting the intent and the section is a
20-line diff — the frame protocol makes that a subtraction, not a refactor.

---

## Which shell: Tauri 2, not gpuix-svelte

With the Python brain fixed and speaking HTTP, both shells are thin. The choice
is decided by what each one must additionally *re-implement* and by what can be
tested.

First, a correction to the framing: **gpuix's single-window limit is not a
blocker here.** table-talk's "wall of session windows" is a grid of CSS cards in
one document, not OS windows (dash.py `build_window` makes a `<div class="win">`).
One window is all this app ever wanted.

What gpuix would still cost, from `reads/gpuix.md`:

* **Mermaid.** No DOM, no canvas 2D, no SVG layout. The only path is
  pre-rendering out of process (mmdc + headless Chromium, or an unproven pure
  reimplementation) to PNG. That trades a working 1.1 MB bundled library for a
  Chromium dependency, a cache directory, an invalidation rule and a class of
  "diagram didn't update" bugs.
* **tt.css.** 482 lines, and the parts that matter most are the ones gpuix
  refuses: no descendant combinators, no `@media`, no at-rules, no cascade, no
  `steps(2,start)` animation, no negative `animation-delay`, no `::before`/
  `::after` (which is how the tree guide is drawn as one continuous rule).
  The bell blink, the scan sweep, the pulse-partway-through trick and the tree
  guide would each need a hand-written native equivalent. The
  `tt_config.DEFAULTS` ↔ `tt.css` byte-identical cross-check would have to be
  re-pointed at a new style file.
* **Clipboard.** Not in `@gpuix/native` at all; the workaround is shelling out to
  `wl-copy`/`xclip`/`xsel`, none of which are guaranteed installed. Copying an id
  is a core interaction, not a nicety.
* **Tests.** `TestGpuixRenderer` throws on Linux by design — no headless render,
  no screenshot, no hit-test. CI is `ubuntu-latest`. A GPUI shell would ship
  with **zero** automated verification of anything visual, on the platform the
  maintainer develops on.
* **Foundations.** Svelte's custom-renderer PR #18511 is unmerged and
  force-pushed weekly, vendored as a private tarball; GPUI is pre-1.0 with its
  Linux backend mid-rewrite from Blade to wgpu; `@gpuix/native` is a third-party
  sub-1.0 binding that already ignores `focus`/`show` on Linux; COLRv1 emoji
  renders blank on Linux via cosmic-text, and this UI leans on `● ▶ ⏸ ◉ ▉ █ ░ ▓ ❯`.
* **Distribution.** ~80 MB per platform, built with Bun (not installed here), no
  cross-compile, no Linux packaging story.

Tauri 2 costs a Rust toolchain **at build time only** and delivers a ~8 MB
binary, tt.css verbatim, mermaid verbatim, `navigator.clipboard` verbatim, and a
front end that a headless browser can drive in CI. Every "workaround" row in
gpuix.md's verdict table becomes "already works".

The honest remainder: with the brain on `http://127.0.0.1:8731`, Tauri buys an
app icon, a window title, no URL bar and no stray browser tab — and nothing else.
That is a real but small benefit, which is why it is **phase 4, not phase 1**:
the Svelte app reaches full parity in the browser first, and the shell is 120
lines of Rust bolted on afterwards. If Tauri ever becomes a burden, deleting
`shell/` leaves a working product.

Revisit gpuix when #18511 has merged into a released Svelte, GPUI's wgpu Linux
backend has landed, and `TestGpuixRenderer` works on Linux. Until all three are
true it is a prototype, not a shell.

---

## Error handling

| failure | behaviour |
|---|---|
| bad JSONL line | `tt_model.fold` skips it (unchanged); the brain logs nothing extra. Unchanged from today. |
| unreadable file / directory named `*.jsonl` / bad utf-8 / non-numeric `ts` | `fold` returns `{}` or coerces, per its existing pins. Unchanged. |
| a file vanishes between glob and read | `fold_cached` catches `OSError` → `{}`; the window disappears from `frame.windows` on the next tick and Svelte's keyed each removes the card. If it was zoomed, `zoomed not in windows` clears the zoom (existing rule). |
| one row cannot be serialised | per-window try/except → that card renders one error line; the rest of the wall is unaffected; the sig is not recorded so the next tick retries. |
| one whole tick raises | caught in the SSE loop; spinner freezes, cadence chip goes `.sl-stale`, the SSE connection stays open. Same degraded-not-dead contract as `tick()` today. |
| brain dies (crash, SIGKILL, port stolen) | `EventSource.onerror` → `frame.stale` banner "brain not responding — reconnecting"; the shell keeps retrying with 0.5→8 s backoff, forever. The last frame stays on screen; nothing blanks. |
| shell dies | the brain keeps running (it is a server). Re-running `table-talk gui` finds the port taken and opens a new window on the same brain. |
| brain already running when `gui` starts | `GET /` answers → skip starting a second brain, open the window. No PID files, no locks. |
| no shell binary installed | `webbrowser.open(url)`. The GUI is never unavailable. |
| Node missing | irrelevant at runtime — `bin/web/` is committed. Only `./ui/build.sh` needs Node ≥20, and it says so and exits 2. |
| Rust missing | only `shell/build.sh` needs it. `table-talk gui` without a shell binary uses the browser. |
| `open_command` missing | `Popen` raises `FileNotFoundError` → the brain returns 200 with `{"warn":"xdg-open not found"}` and the shell shows it in the statusline. Never crashes. Unchanged intent. |
| POST /do from a hostile page | rejected: `X-TT-Token` missing (a cross-origin page cannot read `index.html` to learn it) and `Origin` is not `http://127.0.0.1:<port>`. |
| port in use at startup | `bind` fails → `sys.exit("error: port 8731 is in use — another table-talk is running: <url>")`. |
| config becomes malformed while running | `tt_config.load()` returns DEFAULTS + a warning (existing behaviour); the statusline shows "config not readable, using defaults". |

Security stance, stated plainly: the brain listens on `server.host` (validated
choice, `127.0.0.1` default — the typo-never-widens-exposure pin in
`tt_config.selftest` becomes more load-bearing, not less). `/do` can launch
processes, so it is token- and Origin-gated; the token is 32 bytes from
`secrets.token_urlsafe`, minted per process, written to `.ui/token` mode 0600 and
inlined into `index.html`. `GET /` stays open so SKILL.md's `curl` liveness check
keeps working; it leaks only the fact that table-talk is running.

---

## Testing

`./test.sh` stays a dependency-free bash script running Python selftests, and
stays green on Linux.

```bash
python3 bin/table-talk --selftest      # unchanged
python3 bin/tt_model.py --selftest     # unchanged — file not edited
python3 bin/tt_config.py --selftest    # unchanged — file not edited
python3 bin/tt_jobs.py --selftest      # unchanged
python3 bin/tt_wall.py --selftest      # NEW
python3 bin/tt_serve.py --selftest     # NEW
uv run --script bin/table-talk-dash.py --selftest   # until phase 5 deletes it
```

**The tt_model pins are carried by not moving them.** There is no model port, so
`fold`, `percent`, `progress_pct`, `blocked_by`, `open_action_ids`, `summarize`,
`roll_up`, `group_sessions`, `merge_projects`, `sort_groups`, `weight`, `pack`,
`art_spans`, `row_text`, `parts`, `url_spans`, `path_spans`, `project_roots` and
every one of their ~48,000 property-test cases keep running exactly as they do
today, against exactly the same code the GUI uses. This is the single strongest
argument for this design over any port.

`tt_wall.selftest()` carries the pins that leave dash.py, restated as
**frame-content assertions** — which is strictly better than dash.py's AST
call-count pins, because they assert the output rather than the shape of the
code that produces it:

* `int` cell precedes `why` and `rec` in every action row's `cells`.
* an `art` cell appears on action, task **and** done rows whenever `diagram` is set
  (the `_art_sub`-called-4× pin, as data).
* `reply:true` on action, task and done rows (the `_reply_sub`-called-3× pin).
* every `row.copy` matches `SESSION: [0-9a-f]{4,} - ID: [0-9a-f]{4,}` or `^[0-9a-f]{4,}$`.
* no frame value anywhere contains `<` unescaped in a field the shell renders as
  markup — trivially true, because the shell renders exactly one `@html` and it
  is mermaid's own output.
* `tally_text` pieces, `bar_for` scaling to `MAX_CELLS=20`, `blocks(pct,14)`,
  `live_delay` negative-and-`None`-when-stale, `cols_for` NARROW clamp,
  `default_cols` thresholds, `layout_key` membership, `next_sort` cycle,
  `changed_ids` excluding terms, `abbrev`, `ago`/`hm`.
* the `_hits`-force-opens-a-collapsed-section rule without mutating the toggle
  (issue #57).
* `apply_fold_rules`: first-ever-seen folds once, only a *rising edge* reopens.
* watermark advance uses `int(now)-1`, only when `touched`, only for windows on
  the previous wall.
* `open_acts` inside the window signature; `beat` outside it.
* `form_updates` writes only changed keys; `coerce` returns `None` out of bounds.

`tt_serve.selftest()` pins the boundary: `/` returns 200 with no token, `/state`
without a valid token returns 403, `/do` with a wrong `Origin` returns 403,
`/do` with a path outside `link_roots` refuses and does not spawn, a static-file
request for `../../etc/passwd` returns 404 (whitelist, not path join), the SSE
framing is `data: <json>\n\n`, and the AST scan for any `shell=` keyword anywhere
in the file (moved from dash.py).

Renderer tests, headless, in CI only (never in `./test.sh`):

`.github/workflows/test.yml` gains a job:

```yaml
  ui:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: "22" }
      - run: cd ui && npm ci && npm run build
      - run: git diff --exit-code bin/web   # the committed bundle must match its source
      - run: npx playwright install --with-deps chromium
      - run: node ui/e2e.mjs                # boots the demo brain, drives the page
```

`ui/e2e.mjs` (~120 lines, Playwright, no test framework): spawns
`TABLE_TALK_DIR=docs/demo python3 bin/table-talk state --port 8899`, opens the
page, then asserts against the frozen demo dir — the tally text, that the wall
has two projects, that `!` hides the window with no open actions and the tally
does *not* change, that `u` flips merged/flat, that `\` collapses the drawer to
54 px, that typing in the filter dims rather than hides, that a mermaid `<svg>`
appears, and that typing into a reply textarea survives three poll cycles with
value and caret intact (the REPLY_JS contract, now tested rather than
hand-defended). Screenshots on failure. Same script runs on `macos-14`.

The Tauri shell gets no tests: it is a `WebviewWindowBuilder` and a URL. A
smoke build on both platforms in the release workflow is the check.

---

## Migration path

Each phase ends with a working product. The NiceGUI dashboard is untouched and
launchable throughout phases 0–4.

**Phase 0 — the frame (4 d).** Add `bin/tt_wall.py` with `Store`, `Wall`,
`KEYMAP` and `frame()`, copying the portable helpers out of dash.py. Add
`bin/table-talk state --once` which prints one frame to stdout. `dash.py` is not
edited. Ship: a documented JSON state dump, useful on its own
(`table-talk state --once | jq .status.tally`).

**Phase 1 — the server (2 d).** Add `bin/tt_serve.py`; `table-talk state` becomes
a long-running server with `/`, `/state`, `/do` and a bare `index.html` that just
dumps the frame as text. Ship: a second, ugly, fully-live view on port 8899,
with the real dashboard still on 8731.

**Phase 2 — the Svelte app (12–15 d).** `ui/` with Vite, the components above,
built into `bin/web/`. Work section by section against the demo dir: wall+windows
→ rows+spans+art → drawer → statusline+keys → mermaid → settings+themes →
watermarks+drafts. Ship at every step: `TABLE_TALK_DIR=docs/demo table-talk state
--port 8899` is a real, improving dashboard the whole time.

**Phase 3 — parity and CI (3 d).** Walk `reads/ui.md` line by line against the
running app. Write `ui/e2e.mjs`, add the `ui` CI job and the committed-bundle
diff check. Flip `server.port`'s default owner: `table-talk serve` grows a
`--legacy` flag that still starts NiceGUI. Ship: two dashboards at parity, the
new one on the configured port.

**Phase 4 — the shell (4 d).** `shell/src-tauri` + `table-talk gui` + a release
workflow building `table-talk-gui` for `x86_64-unknown-linux-gnu` and
`universal-apple-darwin`. Ship: one command opens a native window.

**Phase 5 — deletion (2 d).** Remove `bin/table-talk-dash.py` (3257 lines),
`bin/tt.css`'s NiceGUI-specific rules, `tt_model.marked` (the HTML wrapper;
`parts` stays), the `uv` line from `test.sh` and the `astral-sh/setup-uv` step
from CI, and the NiceGUI paragraphs from README. `table-talk serve` becomes an
alias for `table-talk state`. Net line count across the repo goes **down**.

**Repo at the end:**

```
bin/table-talk            CLI, stdlib, py3.10 — + `gui`, `state`
bin/tt_model.py           unchanged
bin/tt_config.py          unchanged
bin/tt_jobs.py            unchanged
bin/tt_wall.py            NEW ~650 lines, stdlib, selftest
bin/tt_serve.py           NEW ~250 lines, stdlib, selftest
bin/tt.css                kept (~450 lines after the NiceGUI-specific trim)
bin/themes.json           unchanged
bin/web/                  committed build: index.html app.js app.css tt.css mermaid.min.js
bin/tt-beat, bin/tt-ref   unchanged
ui/                       Svelte 5 + Vite source, build.sh, e2e.mjs  (build-time only)
shell/src-tauri/          ~120 lines Rust  (build-time only)
skill/SKILL.md            unchanged
install.sh                unchanged
test.sh                   six python3 selftests, no uv
docs/config.example.toml  minus the restart note
```

Runtime dependencies of the whole product: **python3 ≥3.11 and a webview**.
No uv, no NiceGUI, no Node, no Bun.

---

## Distribution

**What a user installs.** `./install.sh` — unchanged: symlinks
`~/.local/bin/table-talk`, links the skill, makes the data dir, offers the
heartbeat hook. That alone gives a working GUI (`table-talk gui` → default
browser). The native window is an optional extra: download `table-talk-gui`
from Releases into `~/.local/bin/`, or `cd shell && ./build.sh`.

**Linux.** `cargo tauri build --target x86_64-unknown-linux-gnu` →
`table-talk-gui` (~8 MB), plus `.deb` and `.AppImage` from the same run. Runtime
needs `libwebkit2gtk-4.1` (present on Fedora/Ubuntu desktops; the AppImage
bundles it). Unsigned — Linux has no signing expectation.

**macOS.** `cargo tauri build --target universal-apple-darwin` →
`table-talk.app` (~10 MB), zipped in a `.dmg`. Signed and notarized when the
release workflow has `APPLE_CERTIFICATE` / `APPLE_ID` / `APPLE_TEAM_ID` secrets,
unsigned otherwise (documented right-click-Open path, same opt-in-by-env shape
gpuix uses). WKWebView is part of the OS; nothing else ships.

No cross-compilation: the release workflow is a two-runner matrix,
`ubuntu-22.04` (older glibc for wider compatibility) and `macos-14`. Both artifacts
attach to the GitHub release.

**Size, honestly:** 8–10 MB shell + 1.5 MB committed web bundle (mermaid is 1.1 MB
of that) + the existing ~250 KB of Python. Against gpuix's ~80 MB per platform,
and against Electron's 120–200 MB.

---

## Risks

| risk | mitigation |
|---|---|
| **WebKitGTK renders tt.css differently from Chromium/Firefox** — the real Linux webview risk (nested `:has()`, newer CSS) | tt.css is 2020-era CSS (flex, grid, custom properties, `::before`); phase 2 runs the app in Epiphany (WebKitGTK) weekly, not only Chromium. Fallback costs nothing: the browser path is always available. |
| **Committed `bin/web/` drifts from `ui/`** | CI rebuilds and `git diff --exit-code bin/web`. A PR that edits `ui/` without rebuilding fails. |
| **Frame size on a large data dir** | Frames are skipped entirely when no signature changed; only *visible* windows carry `sections` (off-wall windows ship flags only). Measured budget: 400 rows ≈ 350 KB, 0.5 Hz worst case. If it ever bites, ship `sections` only for windows whose sig changed — a 15-line change the protocol already anticipates via per-window sigs. Not built now. |
| **Localhost CSRF / a page in the user's browser POSTing `/do`** | token + Origin check, `.ui/token` 0600, `server.host` validated choice. Strictly better than today, where NiceGUI accepts websocket events on 8731 with no token at all. |
| **Two brains on one data dir** (demo + real, or a stale process) | Different ports by construction; `.ui/wall.json` is written atomically and last-writer-wins on shared view state, which is exactly today's `app.storage.general` behaviour. Same-port collision fails loudly at bind. |
| **Tauri's Rust toolchain becomes a maintenance tax** | It is 120 lines behind a build script, in its own directory, needed only for the optional binary. Deleting `shell/` at any time leaves a working product. |
| **The reply-draft contract regresses** | It is now an e2e assertion (type, wait three polls, check value+caret), which it never was before. |
| **`tt_jobs` async work inside a threaded HTTP server** | `start_job` moves to a `threading.Thread` with the same queue discipline it has under NiceGUI's loop; `ThreadingHTTPServer` is already threaded. If `claude-agent-sdk` insists on a running loop, the thread owns its own `asyncio.run`. |
| **Frame protocol churn during phase 2** | `v` field + the shell refusing unknown versions; both sides move in the same commit until phase 3 freezes `v:1`. |

---

## Effort

Honest, one engineer, including tests and docs.

| phase | days |
|---|---|
| 0 — `tt_wall.py`, frame, `state --once` | 4 |
| 1 — `tt_serve.py`, SSE, token, static | 2 |
| 2 — the Svelte app to parity | 12–15 |
| 3 — parity audit, `e2e.mjs`, CI | 3 |
| 4 — Tauri shell, `table-talk gui`, release workflow | 4 |
| 5 — delete dash.py, docs, dependency removal | 2 |
| **total** | **27–30** |

Phase 2 is the estimate that can move. Its floor assumes tt.css and mermaid come
along unchanged; if they had to be re-derived (the gpuix path) that phase alone
would roughly double, and phase 3 would have no automated verification to audit
against on Linux. The same design on gpuix-svelte: **45–60 days, untestable in
CI, on two unreleased foundations.**

---

## What ponytail cuts

* **The whole model port.** No `fold`, `pack`, `weight`, `art_spans`,
  `path_spans` in TypeScript. Python already has them, already tested, already
  the CLI's own contract. This is the single decision the rest of the design
  hangs off.
* **gpuix-svelte / GPUI.** Two pre-release foundations, a vendored Svelte fork,
  no clipboard, no mermaid, no CSS engine, no Linux tests, 80 MB — to render text
  and rectangles. Prototype it for fun; do not ship the dashboard on it.
* **The stdio/NDJSON bridge.** SKILL.md forces an HTTP face to exist; a second
  transport buys nothing. One socket, two verbs.
* **Websockets, uvicorn, FastAPI, NiceGUI, `uv`.** `http.server` + SSE is
  stdlib and one-directional, which is the shape of the data.
* **A bundled Python sidecar.** `table-talk` is already installed on PATH.
* **Node and Bun at runtime.** Commit the bundle.
* **Delta/patch protocol.** Full frames, plus skip-when-unchanged. Add deltas
  when a profiler says so.
* **SQLite / a state DB.** One atomic `wall.json`.
* **`restart_offer`, `port_free`, `do_restart`, `os.execv`, `RESTART_KEYS`, the
  `.sl-port` segment.** Rebind the listener instead.
* **`REPLY_JS`, `BLUR_JS`, `TAB_TITLE_JS`, `COPY_JS`, `SEEN_JS`, `TOAST_JS`.**
  ~200 lines of injected JS defending against NiceGUI's rebuild-everything
  render. A keyed `{#each}` and four `addEventListener`s replace all of it.
* **The `.props()` AST ban.** No props strings exist to inject into.
* **The `NICEGUI_STORAGE_PATH` import-order pin.** No import-time path binding.
* **`tt_model.marked`.** The HTML-wrapping half dies with dash.py; `parts` lives.
* **A test framework.** Python selftests as today, one Playwright script, no
  vitest, no jsdom, no runner.
* **Rust beyond a window.** No commands, no IPC, no plugins except
  `window-state`.

`[frame protocol + 900 lines of stdlib Python + a Svelte view] → skipped: a TS
model port, deltas, gpuix, a sidecar, a state DB; add when Python measurably
falls short, which for folding a directory of small append-only text files it
will not.`
