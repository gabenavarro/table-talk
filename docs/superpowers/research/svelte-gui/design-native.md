# Design: `native` — gpuix-svelte IS the GUI

One Node process, one real GPUI window, Svelte 5 components, no webview, no HTTP
server, no browser. `table-talk gui` launches it. The JSONL log format, `bin/table-talk`
and `skill/SKILL.md` are untouched; `table-talk serve` (NiceGUI) keeps working, unchanged,
for the whole migration and stays the permanent kill-switch.

---

## Goal

Make table-talk its own application instead of a web page served to a browser.

Concretely, what "native" buys that the current dashboard cannot:

1. **No server.** `server.host`, `server.port`, the port-mismatch chip, the `os.execv`
   restart dance, `serve_refusal`, "is the dashboard up? curl it" — all of that exists
   only because the UI is on the other end of a socket. A native window deletes the
   category.
2. **No poll-and-repaint-from-scratch.** NiceGUI's wall calls `container.clear()` every
   2 s, which is why `REPLY_JS` has to save and restore textarea drafts and carets through
   a `MutationObserver`. Svelte's keyed `{#each}` mutates only what changed; a textarea
   that nobody touched is never re-created, so drafts and carets survive by construction
   and ~120 lines of injected JavaScript stop existing.
3. **No HTML.** `tt_model.marked()` exists solely to HTML-escape after splitting matches,
   and carries a ~48,000-case property test proving highlighting can never leak markup.
   With no DOM there is no markup to leak: highlight becomes `{#each parts(text, q)}`
   over plain text nodes. The whole XSS surface, plus the AST ban on non-constant
   `.props()` arguments, is deleted rather than ported.
4. **GPU-drawn dense text.** A wall of 300+ monospace rows repainting every 2 s is exactly
   what GPUI is for, and `<Scroller virtual>` (GPUI's native `<virtual-list>`) builds only
   rows near the viewport.

What "native" does **not** buy, stated up front so the rest of the document is honest:
it does not make the product better at its job. The current dashboard is good. This is a
platform bet, and §Risks prices it.

---

## Architecture

### Processes

```
Claude session ──> bin/table-talk (python, stdlib)  ──append──> ~/.local/share/table-talk/*.jsonl
bin/tt-beat    ──touch──────────────────────────────────────> ~/.local/share/table-talk/.beat/<sid>

user ──> table-talk gui
             │  (python: which(node), version >= 24, spawn, then exit? no — exec)
             ▼
         node --conditions custom-renderer --conditions development
              --import tsx --import gpuix-svelte/register
              gui/main.ts
             │
             ├─ node:fs  ──reads──> *.jsonl, .beat/, ~/.claude/projects/*/*.jsonl
             ├─ child_process.spawn("python3", ["bin/tt_config.py", "--json"])   (startup + on config mtime change)
             ├─ child_process.spawn("python3", ["bin/tt_config.py", "--set", "ui.view=flat"])  (settings save)
             ├─ child_process.spawn(open_command, [path|url])                    (links)
             └─ child_process.spawn(wl-copy|xclip|xsel|pbcopy, ...)              (clipboard)
             │
             ▼
        @gpuix/native GpuixRenderer  ──> one native GPUI window
```

**One OS process. One native window.** `@gpuix/native@0.7.0` has no `createWindow()` and
no window array (`index.d.ts` `WindowOptions` has `title/appName/width/height/min*/resizable/
fullscreen/transparent/...` and no `x`/`y`); `src/render.ts` holds the single renderer in a
`globalThis` slot. This is not a regression: today's "wall of session windows" is already
one browser window containing tmux-shaped *cards*. The wall stays a tiled in-window layout.

**No Python process stays alive.** The GUI is not a front end to a Python backend. It reads
the log directory itself. Python is invoked only as a short-lived subprocess for the three
things it already owns and does better than a port would (§Config bridge).

### Who reads the JSONL

`gui/data.ts`, in the GUI process, with `node:fs` sync calls on the poll tick:

```ts
readdirSync(DATA_DIR).filter(f => f.endsWith('.jsonl'))      // same glob as poll()
  → fold_cached(path)   // statSync → (mtimeMs,size) cache key → readFileSync → fold()
```

Identical contract to `tt_model.fold_cached`, including the mtime/size cache key, so the
steady state is one `readdir` + N `stat` per 2 s and zero parsing. Measured shape of the
real data dir: tens of files, tens of KB each.

### Who owns state

One module, `gui/state.svelte.ts`, loaded once per process (so it survives `render_hot`
remounts during development — the documented reason `.svelte.ts` state modules exist):

```ts
export const wall = $state({
  files: {} as Record<string, State>,   // stem -> folded state, from the poll
  now: 0, beats: new Set<string>(), transcripts: {} as Record<string,string>,
  cols: 0, sort: 'recent', merged: true, needsMe: false, scope: null as string|null,
  zoomed: null as string|null, marks: new Set<string>(), folds: new Set<string>(),
  groupsFolded: new Set<string>(), seenProjects: {} as Record<string,number>,
  current: null as string|null, drawerOpen: true, query: '', theme: 'system',
  seenAt: {} as Record<string,number>, openedTs: 0, touched: false,
  cfg: null as Config|null, spinner: 0, lastPoll: 0, stale: false,
});
```

Everything the wall draws is `$derived` from `wall.files` + the toggles. There is no manual
repaint, no paint signature, no `container.tt_sig`: Svelte's fine-grained reactivity is the
paint guard, and it is finer than the current one (per-row, not per-window).

Persistence replaces `app.storage.general` with one JSON file at the same place the current
store already lives — `~/.local/share/table-talk/.ui/gui.json` — written debounced 500 ms
via `writeFileSync(tmp)` + `renameSync` (atomic, same discipline as `tt_config.set_keys`).
Keys are exactly the persisted set from the inventory: `theme, marks, folds, groups_folded,
zoomed, scope, needs_me, current, cols, sort, drawer_open, merged, seen`. Deliberately not
persisted, same as today: window size, `seen_at`, `opened_ts`, `touched`.

### How updates flow

```
setInterval(cfg.server.poll_seconds * 1000, tick)
  tick():
    now = Date.now()/1000                    // clock BEFORE the reads, same pin as poll()
    for stem of listFiles(): wall.files[stem] = fold_cached(...)
    wall.beats = live_sessions(now)          // readdir .beat/, mtime within 120 s, not future
    wall.transcripts = transcripts()         // readdir ~/.claude/projects/*/*.jsonl, 4-char prefix map
    if (statSync(cfgPath).mtimeMs !== cfgMtime) wall.cfg = readConfig()   // stat only, not a parse
    wall.spinner++; wall.lastPoll = now; wall.stale = false
  on throw: wall.stale = true                // spinner freezes, cadence chip goes stale — never rethrow
```

Assignment into `wall.files` is what makes every `$derived` downstream recompute; Svelte then
emits mutations only for the elements whose text actually changed, batched into one
`applyBatch()` per frame.

**Polling, not watching.** `fs.watch` on the data dir is available and would cut latency to
~0, but the directory is a handful of append-only files and the existing 2 s cadence is a
tested, documented product behaviour (`server.poll_seconds`, validated 0.2–∞). A watcher adds
platform-specific double-fire/rename quirks and a debounce for a latency nobody asked to
reduce. Skipped — add when someone complains the wall feels slow.

### How the Python CLI launches it

New subcommand in `bin/table-talk`, ~35 lines, stdlib only, no import of `tt_config` at
module scope (the 3.10 promise holds):

```python
def cmd_gui(force=False):
    if (msg := serve_refusal(os.environ, force)):   # reused verbatim: gui never returns either
        sys.exit(msg)
    node = shutil.which("node")
    gui = Path(__file__).resolve().parent.parent / "gui"
    if not node or node_major(node) < 24 or not (gui / "node_modules").is_dir():
        sys.exit(gui_unavailable_message(node, gui))   # names the exact fix, and `table-talk serve`
    os.execv(node, [node, "--conditions", "custom-renderer", "--conditions", "development",
                    "--import", str(gui / "node_modules/tsx/dist/loader.mjs"),
                    "--import", str(gui / "node_modules/gpuix-svelte/src/register.ts"),
                    str(gui / "main.ts")])
```

`os.execv`, not `Popen`: `table-talk gui` *becomes* the GUI, so Ctrl-C, exit codes and
process supervision behave like any other app, exactly as `cmd_serve` already does.
The argv is a list; there is no shell anywhere (the existing AST ban on `shell=` in
dash.py gets a sibling ban in `bin/table-talk`'s selftest).

`table-talk serve` is untouched and stays in `--help` as "the browser dashboard".

---

## Components

Every unit below is one file under `gui/`. Names are final, not placeholders.

### Non-visual

| File | What it does | Used by | Depends on |
|---|---|---|---|
| `gui/model.ts` | TS port of `tt_model.py`: `fold`, `fold_cached`, `parse_stem`, `percent`, `progress_pct`, `blocked_by`, `open_action_ids`, `summarize`, `roll_up`, `group_sessions`, `merge_projects`, `sort_groups`, `weight`, `pack`, `art_spans`, `row_text`, `parts`, `url_spans`, `path_spans`. ~620 lines. | everything | `node:fs`, `node:path` only |
| `gui/model.test.ts` | `node:test` port of `tt_model.selftest()` — every pin in the inventory, including the `art_spans` fuzz and the `parts` property test. ~700 lines. | CI, `./test.sh` | `node:test`, `node:assert` |
| `gui/data.ts` | Directory glob + fold cache, `live_sessions()`, `transcripts()`, `link_roots()`, `link_spans()`, `changed_ids()`. The I/O `model.ts` deliberately does not do. | `state.svelte.ts` | `gui/model.ts` |
| `gui/config.ts` | Spawns `python3 bin/tt_config.py --json` / `--set k=v`; caches the result; watches the file's mtime. Never parses TOML. | `state.svelte.ts`, `Settings.svelte` | `node:child_process` |
| `gui/os.ts` | `copy(text)` (wl-copy → xclip → xsel → pbcopy, resolved once with `which`), `open_path(p)`, `open_url(u)` — argv arrays, `child.on('error')` guarded, both re-deriving root confinement at call time from `ROOTS`. | rows, titlebar, drawer footer | `node:child_process` |
| `gui/theme.ts` | Turns the resolved config's token tables into one `set_css_vars({...})` call; picks dark/light from `theme.default` + the OS. | `state.svelte.ts` | `gpuix-svelte` |
| `gui/state.svelte.ts` | The store above, the poll timer, persistence, `do(action)` (the single `KEYMAP` dispatcher), watermark advance. | every component | all of the above |
| `gui/main.ts` | `render(App, { title: 'table-talk', width: 1400, height: 900, minWidth: 480, minHeight: 320 })`, plus `on_window_key('keydown', …)`. 30 lines. | entry point | `gpuix-svelte` |

### Visual

| Component | What it does | How it is used | Depends on |
|---|---|---|---|
| `App.svelte` | Root: drawer + wall in a flex row, statusline below, `<Toast>`/`<Keys>`/`<Settings>` portals over it. Registers the window-key handler and reads `e.editing` so a keystroke aimed at a textarea never triggers `m`/`z`/`f`. | mounted by `main.ts` | all below |
| `Wall.svelte` | Runs `pack(keys, cols, weights, marked)` on a `$derived` `layout_key`, lays out N flex columns, each a `<Scroller>`. Shows the three empty-wall messages. | `App` | `Window`, `model.pack` |
| `Window.svelte` | One session/project card: `<Titlebar>` + body. Wrapped in `<svelte:boundary>` so one unrenderable row costs one card, never the wall (the direct analogue of `paint_window`'s own try/except). | `Wall` | `Titlebar`, `Section` |
| `Titlebar.svelte` | Project name, `!`/`#`/`M`/`Z`/`*`/`◉` flags, session-index button, `ago(latest)`, M/Z/▾ buttons. Click anywhere sets `current`. | `Window` | `os.open_path` |
| `Section.svelte` | The shell-prompt collapse bar `❯ <title> (<n>) ▾/▸`, the `(█░)` glyph bar when shut, and the force-open-on-query-hit rule. Takes a snippet for its rows. | `Window` ×5 | — |
| `ActionRow.svelte` | id button, title, newest-open cursor `▉`, sub-rows in pinned order `int` → `why` → `rec` → `<Art>` → `<Reply>`. | `Section` | `IdButton`, `Art`, `Reply`, `Linked` |
| `TaskRow.svelte` | id button, `what`, then blocked banner **or** `<Scan>` **or** `<Bar>` + pct + `as of HH:MM`; then `int`, `<Art>`, `<Reply>`. | `Section` | `Bar`, `Scan`, `Art`, `Reply` |
| `TermRow.svelte` / `DiagramRow.svelte` / `DoneRow.svelte` | As inventoried. `DoneRow` keeps its id button, sketch and reply box. | `Section` | `Art`, `Reply` |
| `Art.svelte` | `{#each art_spans(text)}` → two-ink runs, `white-space: pre`, inside a `overflow: scroll` box that never widens the card. Used on action, task **and** done rows (4 call sites, same pin). | rows | `model.art_spans` |
| `Bar.svelte` / `Scan.svelte` | `blocks(pct, 14)` glyph bar with a `motion` opacity pulse whose `initial` is set from the reading's age (the native equivalent of the negative `animation-delay`); 5-cell staggered sweep when no `%` is readable. | `TaskRow` | — |
| `Reply.svelte` | `<textarea value={draft} onchange={e => draft = e.value}>` + a copy button. Draft is component-local `$state`; the component is never destroyed by a poll, so no draft/caret machinery exists at all. | rows | `os.copy` |
| `IdButton.svelte` | 4-hex id + smaller `sid` line; click copies `SESSION: <sid> - ID: <id>` or the bare id, guarded by `/^[0-9a-f]{4,}$/`, flashing "copied" only after `os.copy()` resolves true. | rows | `os.copy` |
| `Linked.svelte` | Splits a text cell on `link_spans()` and renders non-link runs as text, link runs as clickable, underlined runs. | rows | `data.link_spans`, `os` |
| `Drawer.svelte` | 284 px panel / 54 px rail; filter input + hit count + theme button; scrollable project/session tree with fold triangles, meters (`● n`, `▶ n`, `[####    ]NN%`), scope-on-click, auto-fold rules; context footer. | `App` | `Meter`, `config` |
| `Statusline.svelte` | Spinner (advances on successful polls only), cadence, tally, scope chip, `cols 1 2 3`, one chip per `KEYMAP` entry, clock. | `App` | `state.do` |
| `Settings.svelte` | Dialog built from `form_fields` in the config JSON — dropdowns and number fields with the validator's own bounds. Save diffs against the loaded config and sends only changed keys to `tt_config.py --set`. | `App` | `config` |
| `Keys.svelte` / `Toast.svelte` | The `?` key list; the "N new action item(s) need(s) you" toast on a rising open-action tally. | `App` | — |
| `Scroller` / `Portal` | From `gpuix-svelte/components/*` — the package's own, not reimplemented. `virtual` on wall columns. | `Wall`, `Drawer`, dialogs | package |

Total new visual code: ~2,100 lines of Svelte, replacing ~2,300 lines of NiceGUI element
construction plus ~250 lines of injected JS in `dash.py`.

---

## Data flow

```
*.jsonl  ──readdirSync/statSync──>  data.list_files()
   │
   ├─ fold_cached(path)                      (mtime,size) key; unchanged file ⇒ zero parsing
   ▼
wall.files: { stem -> {id: event} }          ← the ONLY mutation the poll performs
   │
   ├─ $derived summaries   = map(summarize)
   ├─ $derived groups      = sort_groups(group_sessions(entries), wall.sort)
   ├─ $derived states      = wall.merged ? merge_projects(entries) : entries
   ├─ $derived openActs    = open_action_ids(states)        // cross-file, drives blocked banners
   ├─ $derived visible     = states filtered by scope + needsMe + zoomed
   ├─ $derived weights     = map(weight)                    // content-derived, never pixels
   └─ $derived columns     = pack(visible, cols_for(width, cols), weights, marks)
   ▼
Wall → Window → Section → Row components; Svelte emits mutations for changed text only
   ▼
one applyBatch(json) per frame → GPUI
```

**Repack discipline is preserved by shape, not by a guard.** `columns` is
`$derived` from `layout_key`-equivalent inputs only (visible set, cols, marks, folds, zoomed,
scope, sort, drawerOpen) — progress *text* is not among them, so a poll that only changes a
progress line cannot move a window under the reader's cursor. `weight()` stays content-derived
for the same reason.

**Cadence.** `setInterval(cfg.server.poll_seconds * 1000)`, default 2 s, same validated range.
The clock is read before the file reads (a write landing between the two must not be
mis-stamped as already-seen). One failing tick sets `wall.stale` and returns; the interval is
never cleared — a frozen spinner beside a stale timestamp, exactly as today.

**Wall width.** No `ResizeObserver` and no client/server split: the process owns the window.
`getWindowSize()` on the native handle, read on the poll tick (gpuix-svelte does not yet wrap
it — `docs/todo/3-window-geometry.md`; we call `get_native().getWindowSize()` directly, ~3
lines). `cols_for()` keeps the same clamp: stored pref is a maximum, `< 900 px` always packs 1.

---

## Feature parity table

Every line of `ui.md`'s inventory. "Same" = the same behaviour, reimplemented in Svelte/TS.

### Wall structure, packing, columns
| Inventory item | This design |
|---|---|
| One window per file (flat) / per project (merged) | Same. `merge_projects` ported. |
| Greedy pack into N columns, marked first | Same. `pack` ported, deterministic, pinned. |
| Column count 1/2/3, auto from width, pref is a max, `<900px` ⇒ 1 | Same. Width from `getWindowSize()` instead of a `ResizeObserver` round-trip — strictly simpler. |
| Wall width reported by the client | **Cut as a mechanism, not a feature.** One process owns the window; no client to ask. |
| Content-derived window weight | Same. `weight` ported with its pins (done items cost only their sketch, diagram costs height even when done, progress text costs nothing). |
| Re-pack only on `layout_key` change | Same, by construction: `columns` is `$derived` and progress text is not an input. |
| Zoom forces 1 column, one window | Same. |
| Three empty-wall messages; query never empties the wall | Same. |

### Window titlebar flags
| Item | This design |
|---|---|
| `!` bell, blinking `steps(2,start)` | **Partial.** GPUI has no CSS animation; `motion` tweens are eased, not stepped. Implemented as a 500 ms `setInterval` toggling the glyph's colour token — a real 2-state blink, which is what `steps(2,start)` was chosen to guarantee. One timer for the whole app, not one per bell. |
| `#` activity, `M`, `Z`, `*`, `◉` beat | Same. `live_sessions()` ported (120 s window, future mtime never live, missing dir ⇒ empty set). |
| Current-window tint toward `--sel` + caret underline | Same tokens; underline drawn as a 1 px bordered child (`text-decoration` is dropped by the style layer). |
| Project name, session-index button, `ago(latest)`, M/Z/▾ with tooltips | Same, except **tooltips**: gpuix-svelte ships no `Tooltip` (`docs/todo/7-tooltip-popover.md`). **Cut for phase 1**; the `?` key dialog and the statusline chips already name every action. Restored when the package ships `Tooltip`. |
| Click anywhere makes the window current | Same — needs `hitbox="self"` on the card (GPUI has no event bubbling), which then shields non-interactive descendants automatically. |

### Sections and collapse bars
| Item | This design |
|---|---|
| Fixed order actions → jobs → diagrams → glossary → done | Same. |
| Diagrams section only when ≥1 exists, starts open unless folded by config | Same. |
| `❯ title (n) ▾/▸` clickable header | Same. |
| Per-window open/shut state surviving rebuilds | Same, and free: component state is not destroyed by a poll. |
| `ui.collapsed_sections` decides which start shut | Same, read from the config JSON. |
| `(█░)` glyph bar when shut, `MAX_CELLS=20` scaling | Same. |
| A query hit force-opens a collapsed section for that render only | Same — `open = userOpen \|\| hasHit`, so the user's own toggle is untouched. |

### Row anatomy
| Item | This design |
|---|---|
| Action row: id, title, `▉` on the single newest open action, `int`→`why`→`rec`→art→reply | Same, order pinned by a render test. |
| Task row: blocked banner **or** indeterminate scan **or** bar + pct + `as of HH:MM` | Same. |
| Term row, diagram row, done row | Same (diagram body: see §Diagrams). |
| Sub-row tree guide as one continuous CSS rule with a corner | **Reimplemented, not ported.** No `::before`/`::after` in GPUI. Drawn as an absolutely-positioned 1 px vertical rule inside the sub-row group plus a 1 px horizontal stub per line, with the last line's stub forming the corner. Same visual contract (survives a wrapped `why`), different mechanism. Pinned by a macOS render test comparing painted bounds. |
| id button with smaller `sid` line, click copies | Same. |

### ASCII sketch
| Item | This design |
|---|---|
| Two-ink structure/label split via `art_spans` | Same, ported with the exact codepoint ranges and the fuzz test. |
| `.art` full-width panel, `.art-in` inline-block centring | Same shape: a `width:100%` box containing an `align-self: center` child with `white-space: pre`. |
| Own `overflow-x: auto`, never widens the card | **Same behaviour, different value**: GPUI treats `overflow: auto` as a no-op, so it is `overflow: scroll` with `<Scroller>`'s drawn thumb (GPUI paints no scrollbars). |
| Rendered as text, never markup | Same — and stronger: there is no markup path in the renderer at all. |
| Drawn on action, task and done rows | Same, 4 call sites. |

### Progress, pulse, read_ts
| Item | This design |
|---|---|
| `progress_pct` explicit-over-scraped, bool rejected | Same, ported with the alpha-lac and gpn-micro cases. |
| `blocks(pct, 14)`, snapping, no tweening | Same. |
| Pulse for `LIVE_WINDOW=300 s`, started partway through by a negative delay, stale/future ⇒ not live | **Reimplemented.** `motion={{ initial:{opacity:o0}, animate:{opacity:1}, transition:{duration: remaining} }}` where `o0` is computed from the reading's age — same "finishes on its own with no repaint" property, native tween instead of CSS. |
| `as of HH:MM` absolute clock | Same. |
| Indeterminate 5-cell staggered sweep | Same, staggered `motion` delays. |

### Blocked-on
| Item | This design |
|---|---|
| `blocked_by` derived, cross-file, only while the action is open | Same, ported. |
| `⏸ blocked on <id>` replaces the bar entirely | Same. |
| Counted separately in the tally; drives repaint | Same — `openActs` is a `$derived` input to every task row, so answering a blocker in another file updates the banner on the next tick. |

### Reply box
| Item | This design |
|---|---|
| Under every action, task and done row | Same, 3 call sites. |
| Draft + caret survive the 2 s rebuild | **Delivered by deletion.** No rebuild happens; `REPLY_JS`, its `Map`, its `MutationObserver` and its mousedown-yield rule are not ported because the problem they solve does not exist. |
| Copy button copies `"<id>: <answer>"`, confirms only on success | Same, via `os.copy()` which resolves false when no clipboard binary exists. |
| Caret restore gives up on a deliberate mousedown elsewhere | N/A — nothing steals the caret. |

### Change gutters and watermarks
| Item | This design |
|---|---|
| `changed_ids` vs a watermark; terms never gutter | Same. |
| Watermark per window, frozen while off-wall, floor at open time | Same. |
| Advanced only by interaction, not by visibility alone | Same: window-level `keydown` + a `mousedown`/`scroll` handler on the wall set `wall.touched`. GPUI has no Page Visibility API, and the measured reason that API was distrusted ("covered but not backgrounded on a second monitor") applies identically here. |
| `int(now)-1` one-second-back advance | Same, same reason (`ts` is whole seconds). |
| Not persisted across restart | Same. |
| `.changed` (`--act`) vs `.changed-job` (`--job`) colours | Same. |

### Drawer
| Item | This design |
|---|---|
| 284 px / 54 px rail, toggled by `\`, persisted | Same. |
| Filter input + hit count + theme toggle | Same. |
| Session tree, header counts, `sort:` row cycling | Same. |
| Fold triangle only when >1 session; `click.stop` semantics | Same — with no event bubbling in GPUI, "stop propagation" is the default and the *ancestor* needs `hitbox="self"`; the inverted default is strictly less error-prone. |
| Meter row: `●`/`▶` badges greyed at zero, `[####    ]NN%` | Same. |
| Click row scopes; click again or ✕ clears | Same. |
| Collapsed rail: `abbrev()` tag, `●n`, thin bar, same scope target | Same. |
| Auto-fold: fold on first-ever sight with 0 open actions; rising edge reopens | Same, ported with `seenProjects` persisted. |
| Context footer: nearest `CLAUDE.md` (never above `$HOME`, symlinks resolved), `~/.claude/CLAUDE.md`, `MEMORY.md`, ⚙ settings, raw settings path, `config.example.toml` reference | Same. `nearest_claude_md` is ~15 lines of TS. The ⚙ glyph is kept (geometric, in the mono fallback); the 📑 emoji on "settings ref" is **replaced with `▤`** — see §Risks/emoji. |
| Filter dims (`opacity .78`), highlights, never hides; `N/M rows match`; scroll first hit into view | Same. `opacity` is a supported style property; scroll-into-view uses `<Scroller>`'s offset. Debounce from `ui.filter_debounce_ms`. |

### Statusline
| Item | This design |
|---|---|
| Spinner advancing on successful polls only | Same. |
| `Every Ns · last HH:MM:SS` | Same. |
| Tally `●N open ▶M running ⏸K blocked`, blocked subtracted from running, counts every session | Same. |
| Port-mismatch segment + restart button | **Cut, argued.** There is no port and no server. `server.host`/`server.port` remain in the config and remain live for `table-talk serve`; the GUI's settings form shows them greyed with the note "used by `table-talk serve`". The `port_free` socket probe, `restart_offer` and `do_restart` are not ported. |
| Scope segment + ✕ | Same. |
| `cols 1 2 3` with the effective one highlighted | Same. |
| One chip per `KEYMAP` entry, built from the same dict as the key handler | Same — `state.do()` is the single dispatcher, and the chips iterate `KEYMAP`, so a key can still never do something no click can. |
| `needs-me`/`merge` chips highlight when active | Same. |
| Live clock | Same. |
| Tab title `(N) ` prefix | **Reimplemented**: `set_window_title(\`(${n}) table-talk\`)` when `n > 0`, on the tally's own change. A native window title is a better home for it than a regex over a DOM node. |
| Toast on a *rising* open-action tally, 5 s auto-dismiss | Same, `<Portal>` + a `$effect` comparing against the previous derived count (baseline read once at mount, so a start-up burst never fires). |

### Keys
| Item | This design |
|---|---|
| `\ m z f s / ! u ? Escape` | All same, one `KEYMAP` + `state.do()`. |
| Keyboard ignores keydown while an input has focus / dialog open / key repeat | Same, and better-supported: `on_window_key` reports `event.editing`, which is exactly this signal, natively. |
| Every control is a real button; `BLUR_JS` un-focuses after a real mouse click | **Cut, argued.** `BLUR_JS` exists because NiceGUI's keyboard layer swallows keystrokes while a button holds focus. `on_window_key` fires regardless of focus, so the bug does not exist and the workaround is not ported. |
| Keyboard-only traversal of controls | **Regression, named.** Since `@gpuix/native` 0.7.0 Tab no longer moves focus, and gpuix-svelte wraps neither `focusNext()` nor `focusPrevious()` (`docs/todo/10-native-parity-table.md:39`). Phase 3 adds a ~40-line focus ring over an ordered list of `nativeId`s bound to Tab/Shift-Tab, calling `focusElement()` directly. This is real work the web version got free, and it is an accessibility basic, so it is not cut. |

### Merged vs flat
| Item | This design |
|---|---|
| `u` toggles, persisted, default from `ui.view` | Same. |
| `_from` tagging, higher-`ts`-wins on id collision | Same, ported with the #140 pin. |
| Drawer always lists real session files | Same. |
| Clicking a session row while merged resolves to its project key | Same. |

### Zoom / fold / mark
All three same, including "scope change clears zoom", "folded window costs weight 1", and
marked-first packing with the caret-coloured border (drawn as `border-color` + a 1 px inset
child, since `box-shadow` is dropped by the style layer).

### Transcript links
| Item | This design |
|---|---|
| Scan every `~/.claude/projects/*/*.jsonl`, 4-char prefix map, ambiguous prefix dropped entirely | Same, ported (~20 lines, measured 0.2 ms for 39 files in Python; a `readdir` in Node is comparable). |
| `ix` button opens it with the transcript's own path added to the roots for that one call | Same. |

### Links and `open_command`
| Item | This design |
|---|---|
| `url_spans` http(s) only, lookbehind, punctuation trim | Same, ported with the scheme allowlist pins. |
| `path_spans` — resolves to an existing **file**, confined under roots, cheap `/` early-out | Same, ported with the symlink-escape and traversal pins. |
| Roots resolved once at start into a module global | Same. |
| Click handler re-derives confinement from scratch; argv list, never a shell | Same. `gui/os.ts` uses `spawn(cmd, [arg])` with no `shell` option; the repo's selftest gains a grep-based ban on `shell:` in `gui/*.ts`, the sibling of dash.py's AST ban. |
| Failed launch warns, never crashes | Same, `child.on('error', …)` — the exact pattern gpuix-svelte's HN demo already ships. |
| `link_spans` merge, URL wins on overlap | Same. |

### Copy-id
| Item | This design |
|---|---|
| `SESSION: <sid> - ID: <id>` or bare id | Same. |
| `^[0-9a-f]{4,}$` guard | Same. |
| "copied" only on success | Same, and the failure is *louder*: with no clipboard binary the flash reads `no clipboard tool` rather than nothing. |

### Mermaid diagrams
| Item | This design |
|---|---|
| Live-rendered mermaid with `securityLevel: strict`, `theme: base`, per-render `%%{init}%%`, no-hyphen sanitiser workaround, themed via `!important` CSS overrides | **Cut, argued, with an upgrade path.** GPUI has no DOM, no canvas-2D and no SVG layout engine; mermaid's layout needs real browser geometry APIs, and jsdom/canvas fakes are documented to fail or produce broken output. The diagram row instead renders the **mermaid source** through the same two-ink `Art` component (structure glyphs faint, labels full ink — it reads well, because mermaid source is mostly `-->` and labels), plus an **`open ▸` button** that writes `~/.local/share/table-talk/.dia/<id>.html` (a 12-line page embedding the source and mermaid from a CDN) and hands it to `open_command`. Rationale: diagrams are recorded once and read many times, and the current dashboard's entire mermaid section is 40 lines of workarounds for a browser we no longer have. Upgrade path if the maintainer wants inline pictures: shell out to `mmdc` when it is on `PATH`, cache `sha1(source).png` under `.dia/`, display with `<img src="file://…">` (GPUI renders raster images), fall back to source text when `mmdc` is absent. Deliberately not built in phase 1 — it adds a Chromium dependency to a repo whose whole pitch is stdlib. |
| Parse errors render mermaid's own graphic | N/A — no parse happens in-process. Bad source shows as bad source, which is more diagnosable. |

### Settings, restart, port check
| Item | This design |
|---|---|
| Form derived entirely from `tt_config.form_fields()` | Same, and *literally the same*: the field list arrives as JSON from `tt_config.py --json`, so it cannot drift from the validator any more than today's can. |
| Colour tokens excluded from the form | Same. |
| Save writes only changed keys, `coerce()`'d, `None` when out of bounds | Same logic, in TS, over the same `form_fields` bounds. |
| Write via `tt_config.set_keys` line surgery, comments preserved | **Same code, not ported.** The GUI spawns `python3 bin/tt_config.py --set ui.view=flat --set server.poll_seconds=3`. Every pin in `tt_config.selftest` (comment preservation, insertion point, the multi-line-string safety net, the atomic `os.replace`) keeps holding, because it is still the code that runs. |
| "which keys need a restart" message | Same — for the GUI, *every* key needs a restart for the same reason (config is loaded at startup), except the ones the poll re-reads on mtime change. The message says so. |
| `ensure_config()` copies `docs/config.example.toml`; refreshes the reference copy each start | Same, moved into `tt_config.py --json` (which already knows both paths) so both front-ends share one implementation. |
| Port-restart flow, `port_free` socket bind, `os.execv` re-exec | **Cut** — no port. See the statusline row. |
| Config mtime polled by `stat`, re-loaded only on change | Same. |

### Themes
| Item | This design |
|---|---|
| Mode toggle `◐/○/●` cycling system→light→dark | Same glyphs (chosen precisely because they are geometric, not emoji — that decision pays off twice here). "system" resolves via `Intl`-free platform probe: `gsettings get org.gnome.desktop.interface color-scheme` on Linux, `defaults read -g AppleInterfaceStyle` on macOS, cached, re-checked on the poll tick. Falls back to dark on failure. |
| 15 bundled themes from `bin/themes.json`, `adapted` metadata | Same file, delivered as JSON by `tt_config.py --json`. Not copied into `gui/`. |
| Config token overrides applied on top of a named theme | Same — `tt_config.load()` already does it; the GUI receives the resolved result. |
| WCAG contrast floors enforced against every bundled theme | **Same test, unmoved.** `tt_config.selftest()` keeps enforcing them; nothing about the floors is reimplemented in TS, so nothing can drift. |
| `--hover` derived from `--surface`, never configurable | Same, computed in `gui/theme.ts` in the same direction. |
| Emit only tokens that differ from the stylesheet default; re-validate hex on the way out | **Cut, argued.** `set_css_vars()` takes a plain object; there is no stylesheet cascade for a restatement to shadow, so "emit only differences" has no meaning. Hex re-validation on the way out is also cut — `set_css_vars` values are parsed by the native colour parser and a bad value is dropped with a warning, never interpolated into a document. The *inbound* validation (`valid_colour` in `tt_config.load`) is untouched and is still the trust boundary. |
| Live light/dark swap | Same, one `set_css_vars()` call in a `$effect` — the feature gpuix-svelte is strongest at. |

### UI-state persistence
| Item | This design |
|---|---|
| Every `tt.*` key in `app.storage.general` | Same keys, in `~/.local/share/table-talk/.ui/gui.json`, atomic write. |
| Stored beside the data, not the launch dir | Same, and by construction: the path is derived from `TABLE_TALK_DIR`, so the `NICEGUI_STORAGE_PATH`-before-import hazard (and the AST test guarding it) has no analogue to guard. |
| `wall_width`, watermarks not persisted | Same. |
| Two tabs share marks but not watermarks | N/A — one process, one window. Two `table-talk gui` processes would fight over `gui.json`; the launcher refuses a second instance with a `.ui/gui.lock` (`open(O_EXCL)` + pid), printing "already running". |

### Poll and paint guard
| Item | This design |
|---|---|
| `ui.timer(poll_seconds)`; one bad poll degrades the statusline, never kills the timer | Same. |
| Re-glob + `fold_cached` every tick, clock read first | Same. |
| Per-window paint guard, signature recorded only after a successful paint | **Reimplemented as `<svelte:boundary>` per window.** One unrenderable row costs its own card and shows an inline `⚠ could not draw this window` with the error, exactly the property the try/except bought; the "record the signature only after success" pin has no analogue because there is no signature. |
| Drawer signature | Same reasoning; `<svelte:boundary>` around the drawer. |

### Heartbeat
`bin/tt-beat` and the `.beat/` contract are untouched. `live_sessions()` is ported with its
three pins (120 s window, future mtime never live, missing dir ⇒ empty set, silently).

### Demo dir
`TABLE_TALK_DIR=docs/demo table-talk gui` works identically — the env var is read by
`gui/data.ts` the same way `tt_model` reads it. Two instances against different dirs are fine
(the instance lock is per data dir). This is *easier* than today: no `--port` juggling.

### Jobs
The jobs section, the `#` flag and the blocked banner are rendered exactly as inventoried.
`tt_jobs.py`'s *runner* (`start_job`/`run_job`, the `claude-agent-sdk` dependency, the
PreToolUse gate) is **not ported** — it is Python-async-specific, the job modal was already
dropped at HEAD, and re-implementing an agent launcher in TS is a second product. `table-talk
serve` keeps whatever job-starting surface it has. Named as a cut, not an oversight.

---

## Error handling

| Failure | Behaviour |
|---|---|
| **Bad JSONL line** | `fold()` skips it and writes `warning: skipped malformed line <file>:<n>` to stderr, once per (file, line) per process — same contract as both existing `fold()`s, including the UTF-8 `backslashreplace` re-scrub that `tt_model`'s side does and the CLI's does not. |
| **Unreadable / vanished file, directory named `*.jsonl`, non-numeric `ts`** | `fold()` returns `{}` on any thrown error; `summarize` coerces a non-numeric `ts` to 0. Ported pins, one for one. |
| **Data dir missing** | `mkdirSync(recursive)` at startup, then an empty wall with the "record something with table-talk" message. |
| **Config file missing or malformed** | Handled where it already is: `tt_config.load()` returns DEFAULTS with a warning. If the `tt_config.py --json` subprocess fails entirely (no python3, 3.10 without `tomllib`), the GUI starts on the built-in Gruvbox-Dark-Hard defaults compiled into `gui/theme.ts` and shows one statusline chip `config unavailable`. It never refuses to start over a config. |
| **Clipboard binary missing** | `os.copy()` resolves false; the button flashes `no clipboard tool`. Never a silent lie, never a crash. |
| **`open_command` missing** | `child.on('error')` logs one line; the link is inert, as today. |
| **A row throws while rendering** | `<svelte:boundary>` per window: that card shows `⚠ could not draw this window` + the message; every other card is unaffected. |
| **Renderer/native throws at startup** (Vulkan `NoSupportedDeviceFound`, Wayland surface failure, missing prebuild for the platform) | `main.ts` wraps `render()` in try/catch, prints the native error plus `the native GUI could not start on this machine — run 'table-talk serve' for the browser dashboard`, exits 3. `table-talk gui` is `execv`'d, so exit 3 is the user's exit code. |
| **Renderer crashes mid-session** | GPUI owns the UI thread; a hard crash takes the process. No supervisor is added (a crash loop that hides the crash is worse than a crash). The log directory is append-only and owned by the CLI, so nothing is lost. |
| **Missing Node / Node < 24 / `gui/node_modules` absent** | `table-talk gui` refuses *before* exec, naming the exact fix (`install Node 24+`, or `npm --prefix <repo>/gui ci`) and pointing at `table-talk serve`. It never half-starts. |
| **Second instance** | `.ui/gui.lock` (`O_CREAT\|O_EXCL`, pid inside, stale pid reclaimed) — prints `table-talk gui is already running (pid N)` and exits 1. |
| **`table-talk gui` run inside a Claude session** | `serve_refusal()` reused verbatim: it never returns, so it would wedge a session until the tool timeout. Same message, same `--force`. |

---

## Testing

`./test.sh` stays the one entry point and stays green on Linux.

```sh
#!/usr/bin/env bash
set -euo pipefail
python3 bin/table-talk --selftest
python3 bin/tt_model.py --selftest
python3 bin/tt_config.py --selftest
python3 bin/tt_jobs.py --selftest
uv run --script bin/table-talk-dash.py --selftest
if command -v node >/dev/null && [ -d gui/node_modules ]; then
    node --test gui/model.test.ts gui/data.test.ts
    if [ "$(uname)" = Darwin ]; then node gui/node_modules/.bin/gpuix-svelte gui/test/render.ts
    else echo "skipped: gui render tests need macOS (no headless GPU renderer on Linux)"; fi
else
    echo "skipped: gui tests (node >= 24 and gui/node_modules required)"
fi
echo "all selftests passed"
```

**`gui/model.test.ts` carries the same pins as `tt_model.selftest`**, one for one, using
`node:test` + `node:assert` (stdlib, no framework, matching the repo's existing no-framework
rule). Specifically it ports: `fold`'s partial-update/garbage-line/missing-file/bad-UTF-8/
lone-surrogate/chmod-000/directory/non-numeric-`ts` cases and the mtime+size cache
invalidation; `parse_stem`; `percent`'s explicit-%-over-fraction (the alpha-lac case) and
zero-denominator rejection; `blocked_by`/`open_action_ids`; `progress_pct`'s bool/string/list
rejection and `0`-is-a-reading; `summarize`/`roll_up` (sum, never average — the phephree case);
`group_sessions`/`sort_groups` including the "returns new objects" rule; `merge_projects`'s
higher-`ts`-wins (#140); `weight`'s "progress text costs nothing"; `pack`'s determinism and
marked-first; `art_spans`' `eval`-is-not-an-arrowhead case and the ~500-case lossless-reassembly
fuzz; `parts`' casing preservation and non-overlap over 2000 random strings × hostile queries
(the escaping half of that property test is dropped with `marked()` — there is no HTML);
`url_spans`/`path_spans`' scheme allowlist, punctuation trim, symlink escape, traversal,
directories-never-linked, and exact span indices against the original string.

**A cross-language pin.** `gui/model.test.ts` also runs `python3 bin/tt_model.py --selftest`'s
fixture corpus: phase 1 adds `tt_model.py --dump-fixtures` (~20 lines) writing
`gui/test/fixtures.json` — `{input jsonl bytes, expected fold, expected summarize, expected
pack, expected art_spans}` for every case the Python selftest already builds. The TS test
asserts against that file, and CI fails if `--dump-fixtures` output differs from the committed
copy. This is what keeps two independently-maintained implementations of the log-format
contract from drifting, and it is the same technique already used to keep `bin/table-talk`'s
`fold()` and `tt_model`'s in agreement.

**`gui/data.test.ts`** covers the I/O layer against a temp dir: `live_sessions` (in-window,
future mtime, missing dir), `transcripts` (ambiguous 4-char prefix dropped entirely),
`link_roots`/confinement, and `os.ts`'s command selection (mocked `which`).

**`gui/test/render.ts`** uses `gpuix-svelte/test` (`mount_headless`, `settle`, `find_text`,
`click_test_id`, `press`) and pins what only a renderer can answer: the `int`-before-`why`
sub-row order, `Art` on exactly 4 row kinds, `Reply` on exactly 3, section force-open on a
query hit, the tree-guide geometry (painted bounds), the collapse-bar glyph counts, and that
every `KEYMAP` entry has a statusline chip. **It runs on macOS only** — verified in this
sandbox: `hasTestGpuixRenderer()` is `false` on Linux and `new TestGpuixRenderer()` throws by
design ("wgpu cannot read a rendered image back yet"). On Linux it prints an explicit skip
line; it is never silently green.

**CI** (`.github/workflows/test.yml`) gains two jobs:

```yaml
gui-model:   { runs-on: ubuntu-latest,  steps: [checkout, setup-node@24, npm --prefix gui ci, node --test gui/*.test.ts] }
gui-render:  { runs-on: macos-14,       steps: [checkout, setup-node@24, npm --prefix gui ci, gpuix-svelte gui/test/render.ts] }
```

The existing `test` and `cli-oldest-python` jobs are unchanged. `gui-render` is the only new
paid runner; it is also the only place the whole rendering half of the product is verified,
which is a standing cost, not a one-off.

---

## Migration path

Each phase ends with a shipped, working product. `table-talk serve` runs untouched throughout
and is not removed by any phase.

**Phase 0 — spike, 2 d.** Vendor `gpuix-svelte` as a tarball (`npm pack` of the pinned clone,
which bundles its private Svelte build) into `vendor/gpuix-svelte-0.1.0-b474c83.tgz`; commit it;
`gui/package.json` depends on `file:../vendor/...tgz` and `"@gpuix/native": "0.7.0"` exact.
Prove on Fedora/Wayland **and** on a macOS machine: a window opens; `Lilex`/the system mono
renders `─│┌┘█░▓❯▾▸◉●▶⏸◐○●▉⚠✕⚙▤` legibly; `wl-copy` round-trips; a 400-row `<Scroller virtual>`
holds frame cost. **Gate: if any of those fail on either platform, stop here and take the
`landscape.md` recommendation instead.** This phase is cheap and its whole purpose is to be
allowed to fail.

**Phase 1 — the model, 6 d.** `gui/model.ts`, `gui/data.ts`, `gui/model.test.ts`,
`gui/data.test.ts`, `tt_model.py --dump-fixtures`, `gui/test/fixtures.json`, CI `gui-model` job.
Nothing renders yet. Ships as: `./test.sh` runs two implementations of the log contract and
proves they agree.

**Phase 2 — the read-only wall, 12 d.** `main.ts`, `state.svelte.ts`, `theme.ts`, `App`,
`Wall`, `Window`, `Titlebar`, `Section`, all row components, `Art`, `Bar`, `Scan`, `IdButton`,
`Linked`, `Statusline`, `Drawer`. `table-talk gui` exists and refuses cleanly when Node is
missing. Ships as: a native window that shows the wall correctly and does nothing else.
README gains a "native GUI (preview)" section that says plainly it is read-only.

**Phase 3 — interaction, 7 d.** `KEYMAP` + `state.do()`, marks/zoom/fold/scope/needs-me/merge/
sort, filter with dimming and scroll-to-hit, `Reply`, clipboard, link clicks, transcript
button, watermarks and change gutters, persistence, the focus ring for Tab traversal, `Keys`,
`Toast`, window-title count. Ships as: the wall is usable for a full working day.

**Phase 4 — config and settings, 4 d.** `tt_config.py --json` / `--set` (the only change to a
Python file besides the new `gui` subcommand), `gui/config.ts`, `Settings.svelte`, theme mode
cycling with OS detection, `ensure_config` moved behind `--json`. Ships as: everything the
settings dialog does today, minus the port/restart flow.

**Phase 5 — parity sweep and distribution, 5 d.** Walk `ui.md` line by line against the running
app; `gui/test/render.ts` and the macOS CI job; diagram source rendering + `open ▸`; the
instance lock; error paths exercised by hand (chmod a log, delete a file mid-poll, break the
TOML, unplug the clipboard binary). `install.sh` gains an optional `npm --prefix gui ci` behind
`--gui`. README documents both front-ends and the kill-switch. Ships as: parity, declared and
tested.

**Phase 6 — decide, 3 d.** Run both for two weeks. Then either (a) mark `table-talk serve`
"legacy, kept for headless/remote use" and keep it — it is 3.2k lines that cost nothing to
leave alone and it is the only way to see the wall over SSH — or (b) delete it and the NiceGUI
dependency. Recommendation: (a). "The dashboard also works in a browser" is a feature, and the
Python selftests that pin the design tokens and theme contrast live in files both front-ends
share.

**Total: 39 engineer-days**, plus a standing maintenance tax (§Effort).

### The repo at the end

```
bin/table-talk              + cmd_gui, + a `shell:`/`shell=` ban in its selftest   (~50 new lines)
bin/table-talk-dash.py      unchanged                                    (the kill-switch)
bin/tt_model.py             + --dump-fixtures                            (~20 new lines)
bin/tt_config.py            + --json, --set, + ensure_config()           (~60 new lines)
bin/tt_jobs.py tt.css themes.json tt-beat tt-ref   unchanged
skill/SKILL.md              unchanged                                    (non-negotiable)
gui/                        ~2,900 lines TS + Svelte, 24 files
vendor/gpuix-svelte-0.1.0-b474c83.tgz    ~1.5 MB, committed
docs/config.example.toml    + a note that server.* applies to `serve` only
test.sh                     + the node block above
.github/workflows/test.yml  + gui-model (ubuntu), gui-render (macos-14)
README.md                   two front-ends, one data dir, one CLI
install.sh                  + --gui  (npm --prefix gui ci)
```

Commands: `table-talk gui` (native), `table-talk serve` (browser), everything else unchanged.
Runtime dependencies: Python 3.10+ for the CLI (unchanged), `uv` + NiceGUI for `serve`
(unchanged), Node ≥ 24 for `gui`.

---

## Distribution

**Tier 1 — from the repo (the default, and the only one phase 5 ships).**
`git clone && ./install.sh --gui` → `npm --prefix gui ci` installs the committed tarball plus
`@gpuix/native@0.7.0` (20 MB unpacked, of which 19.9 MB is a wasm build we never use — it ships
unconditionally). Requires Node ≥ 24 and network once. `table-talk gui` then works. This matches
how the repo is already installed and adds no packaging machinery.

**Tier 2 — compiled binaries (deferred, and priced honestly).**
`bun scripts/compile.ts` produces one ~80 MB executable (Bun runtime + Svelte runtime + the 17 MB
GPUI addon). It is **Bun-only** — this machine has no Bun — and there is **no cross-compiling**,
so shipping Linux + macOS means a GitHub Actions matrix building on `ubuntu-latest` and
`macos-14` per release, producing `table-talk-gui-linux-x64` and `table-talk-gui-darwin-arm64`.
Signing: macOS needs `CODESIGN_IDENTITY` (Developer ID) and `NOTARY_PROFILE` or Gatekeeper
blocks a downloaded copy; Linux has no packaging story in gpuix-svelte at all (no AppImage, no
deb — a raw binary). For a repo whose current install is a `git clone` of ~400 KB of Python,
adding a 160 MB two-platform release is a large change in what the project *is*, so it is a
phase-6-or-later decision, not part of parity.

**Platform coverage — the honest table.**

| Platform | `@gpuix/native@0.7.0` prebuild | Status |
|---|---|---|
| linux-x64 | yes | supported; Linux is compiled but **never tested** in gpuix-svelte's own CI (two macOS jobs only) |
| darwin-arm64 | yes | supported, and the best-tested target |
| win32-x64 | yes | works, not a project goal |
| **darwin-x64 (Intel Mac)** | **no prebuild** per `HOWTO.txt:75` / `CLAUDE.md:336` | **falls back to `table-talk serve`** |
| **linux-arm64** | **no prebuild** per the same source | **falls back to `table-talk serve`** |

`landscape.md` reports upstream `remorses/gpuix` advertising darwin-x64 and linux-arm64-gnu
prebuilds, which contradicts the pin read out of the installed package. **Phase 0 must resolve
this**, because "macOS works" is a non-negotiable and an Intel Mac with no native GUI is a
partial failure of it. If the contradiction resolves badly, the mitigation is exactly the
fallback above, documented in the README rather than discovered by a user.

---

## Risks

| Risk | Severity | Mitigation |
|---|---|---|
| **Svelte's custom-renderer API (#18511) is unmerged, unreleased, force-pushed weekly, and gpuix-svelte vendors a private build of it.** If it changes shape or stalls, the GUI is pinned to a fork forever. | **Highest.** | Commit `vendor/gpuix-svelte-0.1.0-b474c83.tgz` — the packed npm tarball, which *bundles* the private Svelte build, so nothing is fetched from `pkg.svelte.dev` at install time and a force-push upstream cannot break an install. Never add `svelte` to `gui/package.json` (the registry's `svelte` has the same version number and none of the API). Upgrading the tarball is a deliberate, reviewed commit gated on `./test.sh` — including the fixture cross-check and the macOS render suite. Worst case the project sits on b474c83 indefinitely; it works or it does not, and it cannot silently rot. |
| **GPUI is pre-1.0 with routine breaking changes, and its Linux graphics backend is mid-rewrite from Blade to wgpu** (zed#46758). | High. | `@gpuix/native` pinned to exact `0.7.0`, not a range. Upgrades are commits with the same gate. GPUI churn reaches us only through a version we chose. |
| **Linux is compiled but never tested upstream** — gpuix-svelte's CI is two macOS jobs; the headless test renderer does not exist on Linux at all. | High — the maintainer's daily driver is Fedora. | The model half is fully tested on Linux (`gui-model`, no renderer needed). The render half is tested on `macos-14` in our own CI, so *someone* runs it. Linux rendering is verified by hand at each phase boundary, and phase 0 exists to find out early. This is the single ugliest structural fact in the design and it does not have a clean fix. |
| **Colour emoji renders blank on Linux (COLRv1 / cosmic-text)**; Wayland font-size regressions. | Medium. | No emoji anywhere in the GUI. The current dashboard already chose geometric glyphs on purpose (`◐○●`, not `☀☾`); this design extends that to a hard rule and replaces the two remaining emoji (`📑`, and `⚙` if it resolves poorly) with `▤` / `≡`. Box-drawing and block glyphs are BMP monochrome and covered by every mono face phase 0 checks. |
| **No headless render test on Linux ⇒ a Linux-only rendering regression can ship.** | Medium. | `GPUIX_SCREENSHOT=… table-talk gui` at each phase boundary, screenshot committed to `docs/assets/` and eyeballed in review. Manual, and named as manual. |
| **Clipboard is not a native API** — it shells out per platform, and on Linux only if `wl-clipboard`/`xclip`/`xsel` is installed. | Medium — copy-id is a core interaction. | `os.copy()` resolves false and the button says `no clipboard tool`, matching the existing "never lie about a copy" pin. `install.sh --gui` warns when none of the three is on `PATH`. |
| **Tab no longer moves focus** (native 0.7.0), and `focusNext`/`focusPrevious` are unwrapped by gpuix-svelte. | Medium — accessibility basic. | ~40-line focus ring in phase 3 over an ordered `nativeId` list, calling `get_native().focusElement()`. Not cut. |
| **Mermaid loses live rendering.** | Medium — it is a real feature the maintainer uses. | Source rendered legibly two-ink + `open ▸` in a browser; `mmdc`-to-PNG cache documented as the upgrade. Argue it explicitly in the PR rather than hoping it goes unnoticed. |
| **One author, days-old package, ten open TODO files, npm name reserved 2026-09-01, first release will be 0.1.0.** | Medium. | The vendored tarball means an abandoned upstream costs us nothing operationally — but it also means we would own a Svelte custom renderer. Phase 6's "keep `serve`" recommendation is the standing answer: the browser dashboard remains a complete product. |
| **80 MB per-platform binaries, no cross-compile, Bun required, macOS signing.** | Medium. | Tier 1 (`npm ci` from the repo) is the shipped path; binaries are deferred and priced above. |
| **Two implementations of the fold contract drift.** | Medium. | `tt_model.py --dump-fixtures` + a committed `gui/test/fixtures.json` checked in CI. The same technique the repo already uses to keep the CLI's `fold()` and the model's in agreement. |
| **The port loses a pinned edge case nobody notices for months.** | Medium. | Every pin in `tt_model.selftest` is enumerated in §Testing and ported one for one; the fixture file makes the enumeration mechanical rather than a promise. |
| **Effort overrun: 39 days is 8 weeks part-time for a dashboard that already works.** | High, in opportunity cost. | Phases end shippable, and phase 0 is a real gate with a named alternative. If phase 2 slips past ~18 days, stop — `serve` never broke. |

---

## Effort

Honest engineer-days, assuming familiarity with Svelte 5 runes and with this codebase, and
including tests and review for each phase.

| Phase | Days | Notes |
|---|---|---|
| 0 — spike and gate | 2 | Cheap on purpose; needs access to a macOS machine as well as the Fedora box. |
| 1 — `model.ts` + tests + fixtures | 6 | ~620 lines TS, ~700 lines test, plus `--dump-fixtures`. The regex and Unicode-range work (`percent`, `art_spans`, `url_spans`, `path_spans`) is where the day count actually goes. |
| 2 — read-only wall | 12 | The bulk. No CSS engine: every rule in `tt.css`'s 482 lines is re-expressed in the supported subset, and the tree guide, the art panel and the collapse bars each need a new mechanism. |
| 3 — interaction | 7 | Keys, filter, clipboard, links, watermarks, persistence, focus ring. |
| 4 — config and settings | 4 | Small, because `tt_config` stays Python. |
| 5 — parity sweep + render tests + distribution tier 1 | 5 | The macOS CI job and the line-by-line `ui.md` walk. |
| 6 — dual-run and decide | 3 | Mostly waiting and reading. |
| **Total** | **39** | ≈ 8 weeks at 1 day/week; ≈ 2 months of evenings. |

Not in the 39: compiled-binary distribution (+4 d, plus a macOS signing certificate), the
`mmdc` diagram cache (+2 d), and the **standing tax** — each `gpuix-svelte` / `@gpuix/native`
upgrade is a reviewed commit with a full test pass, realistically 0.5–1 d whenever the
maintainer chooses to take one, forever.

For scale: the NiceGUI dashboard being replaced is 3,257 lines including its selftest, and it
exists.

---

## What ponytail cuts

1. **`tt_config.py` is not ported.** TOML parsing, colour validation, 17 tokens × 2 modes, 15
   bundled themes, the WCAG contrast floors, `form_fields`, and above all `set_keys`' line
   surgery with its comment-preservation and multi-line-string safety net — the single most
   dangerous file to reimplement — stay in Python behind `--json` / `--set`. Two spawns of a
   stdlib script cost ~40 ms each and keep `tt_config.selftest()` as the one place the rules
   live. Reimplementing it in TS would be ~500 lines and a second set of pins to keep honest.
2. **`marked()` is deleted, not ported.** No HTML, no escaping, no ~48,000-case escaping
   property test. `parts()` survives; `html.escape` does not.
3. **`BLUR_JS`, `REPLY_JS`, `COPY_JS`, `SEEN_JS`, `WIDTH_JS`, `TAB_TITLE_JS`, `TOAST_JS`,
   `scroll_js` are deleted, not ported.** Six of the eight are workarounds for NiceGUI's
   clear-and-rebuild render loop, its keyboard-vs-focus layer, and the server's inability to
   see the viewport. Two (`COPY_JS`'s format, `TOAST_JS`'s rising-edge rule) are real product
   behaviour and survive as ~15 lines of component code each.
4. **The port/restart flow is cut whole**: `port_free`'s socket bind, `restart_offer`,
   `do_restart`, `os.execv`-onto-a-new-port, the `.sl-port` statusline segment. There is no
   server.
5. **The paint-signature machinery is cut**: `layout_key` comparison, `win["sig"]`,
   `container.tt_sig`, the "record after success" ordering pin. Svelte's reactivity is a finer
   guard, and `<svelte:boundary>` is a stricter blast radius.
6. **The theme-CSS emitter is cut**: "emit only tokens that differ" and the outbound hex
   re-validation have no meaning against `set_css_vars()`. Inbound validation is untouched.
7. **Live mermaid is cut** to source-plus-`open ▸`, with `mmdc` named as the upgrade and not
   built. A headless Chromium is not a dependency this repo should acquire to draw a picture
   of a graph that was recorded once.
8. **Titlebar tooltips are cut** until the package ships `Tooltip`. The `?` dialog names every
   action.
9. **`tt_jobs`' runner is not ported.** The jobs *section* is; the agent launcher is a second
   product.
10. **No file watcher.** The 2 s poll is already the product's documented cadence and the
    directory is tiny. `fs.watch` is added when someone says the wall feels slow, not before.
11. **No abstraction layer between the model and the view.** No repository interface, no event
    bus, no store framework. One `$state` object, `$derived` everywhere else.
12. **`table-talk serve` is not deleted.** It is 3.2k lines that already work, it is the only
    way to see the wall from another machine, and it is the kill-switch that makes every phase
    above reversible. Deleting it would be the one piece of work in this document with no
    upside at all.

### The cut ponytail would make if the brief allowed it

The brief mandates that the model be ported to TypeScript. It is worth naming what that costs:
`gui/model.ts` + its tests + the fixture cross-check is **~9 of the 39 days and a permanent
second implementation of the log-format contract**. The lazier design that keeps every
behaviour is a long-lived `python3 bin/tt_model.py --serve-state` emitting one NDJSON line per
poll on stdout, with the Svelte process rendering it — one process boundary, zero ported
functions, zero drift, and `tt_model.selftest()` remains the only test of the model that ever
needs to exist. It is worse for the "native" thesis (Python is back in the runtime) and better
for everything else. If the maintainer is choosing between designs rather than between
front-ends, that swap is the highest-value line item here.
