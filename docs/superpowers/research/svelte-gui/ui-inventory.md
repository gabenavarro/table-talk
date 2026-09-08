# table-talk dashboard — full UI inventory (for Svelte/GPUI parity judging)

Source read in full: `bin/table-talk-dash.py` (3257 lines incl. selftest),
`bin/tt_model.py` (923 lines incl. selftest), `bin/tt_config.py` (564 lines
incl. selftest), `bin/tt.css` (482 lines), `bin/themes.json` (structure),
`skill/SKILL.md`, `README.md` (Dashboard/Configuration/Themes/hooks
sections), `bin/table-talk` lines 1-120. Line numbers below are `file:line`
in the current repo (`<repo>`, clean, HEAD
`5687092`).

Note: `bin/tt_jobs.py` (the "start work from the wall" job runner, README
§"Starting work from the wall") is imported and exercised heavily by
dash.py (`import tt_jobs` at dash.py:19; `start_job`/`run_job` at
dash.py:715-814; jobs section UI at dash.py:936-1191) but was **not** in
this task's required reading list, so its internal gate/permission logic is
out of scope here — only its wall-visible surface (the "jobs" section, the
`#` activity flag, blocked-task rendering) is inventoried below as part of
the dashboard's UI.

---

## 1. Feature inventory (every user-visible behaviour)

### Wall structure, packing, columns
- One window per session file (flat view) or one per project (merged view,
  default) — dash.py:3046-3053, `tt_model.merge_projects` tt_model.py:244-264.
- Windows greedy-packed into N columns by shortest-current-load; marked
  windows placed first — `tt_model.pack` tt_model.py:324-342, called
  dash.py:3012/3091.
- Column count: 1/2/3, auto-picked from wall width (`default_cols`:
  ≥1800→3, ≥1200→2, else 1 — dash.py:1248-1250) unless user picked one via
  statusline `cols` buttons or `ui.columns` config; stored pref is a
  **maximum**, a wall <900px (`NARROW`) always packs 1 column regardless
  (`cols_for` dash.py:1253-1258, 1372).
- Wall's own pixel width is reported by the **client** via a
  `ResizeObserver` on `.wall` (`WIDTH_JS` dash.py:299-308) because the
  server cannot read the viewport; a tick only re-fires when the
  narrow/wide clamp flips (dash.py:2361-2376).
- Window "weight" (packing height estimate) is derived purely from content
  (not measured pixels, so packing never moves a window mid-read):
  open action ≈3+chars/110 units, open task ≈2(+1 if intuitive), diagram
  +6, attached ASCII diagram +1+lines//2, done items cost only their
  sketch, terms cost 0 — `tt_model.weight` tt_model.py:296-321.
- Re-pack happens ONLY when `layout_key` changes (visible set, cols, marks,
  folds, zoomed, scope, sort, drawer_open) — never on a poll that only
  changed text — dash.py:1261-1265, 3079-3092.
- Zoom forces 1 column and shows only the zoomed window — dash.py:3073-3078.
- Empty wall shows one of three messages depending on why: needs-me active
  ("press ! to show every session"), scope active ("clear the scope..."),
  or truly nothing recorded ("record something with table-talk") —
  dash.py:3014-3022. The filter query can **never** empty the wall (dims
  only).

### Window titlebar flags
Per README §Dashboard and dash.py:2664-2704 (`build_window`):
- `!` bell — session has ≥1 open action; blinks with `steps(2,start)`, never
  an eased tween (selftest pins this at dash.py:1557-1564, css `.bell`).
- `#` activity (`actv` class `.actv`) — session has ≥1 open task.
- `M` marked (held at front of pack).
- `Z` zoomed (fills wall).
- `*` current — the window m/z/f keyboard actions act on; the one last
  clicked if still on wall, else the first window on the wall
  (`target()` dash.py:2642-2647). Titlebar tints toward `--sel` (not `--sel`
  outright — contrast reasons) and keeps a `--caret` underline
  (css `.win.cur>.win-t`).
- `◉` beat — session's `sid` has a live heartbeat file in `.beat/` within
  `BEAT_WINDOW=120s` (`live_sessions` dash.py:574-596); applied per poll,
  outside the paint signature since it changes nothing in any session file
  (dash.py:3146, selftest dash.py:1865-1868).
- Titlebar also shows: project name, session-index button (`ix`, opens the
  Claude Code transcript when resolvable), age (`ago(latest)`), and the
  `wctl` row of M/Z/▾ buttons with tooltips.
- Clicking anywhere in a window's titlebar/body makes it current
  (dash.py:2667).

### Sections and their collapse bars
Per window body (`render_window_body` dash.py:1136-1230), in fixed order:
1. **actions --open** — open (non-done) actions, newest first.
2. **jobs** — open (non-done) tasks, newest first.
3. **diagrams** — only rendered when ≥1 exists (mermaid rows); starts open
   by default unless config folds it (`"dia" not in collapsed`) — it exists
   to be looked at.
4. **glossary** — terms, alphabetical.
5. **done** — resolved actions+tasks, newest first.

Each section header is a clickable shell-prompt-styled `<button>`
(`_prompt` dash.py:1091-1133): `❯ <title> (<count>) ▾/▸`. Clicking toggles
visibility; state persisted per-window on the container object
(`container.tt_open`, survives poll rebuilds) and which sections **start**
shut comes from `ui.collapsed_sections` config (default
`["glossary","done"]`) — dash.py:1155-1161. A shut section shows a
`(filled,empty)` glyph bar (█ open-count / ░ resolved-count, capped/scaled
to `MAX_CELLS=20` — `bar_for` dash.py:1077-1088) so a collapsed section
still reports itself; the bar disappears when open. A **live query that
matches a row inside a collapsed section force-opens it** for that render
only, without touching the user's own toggle (`_hits`/`force` dash.py:
1070-1074, 1177-1220 — issue #57 pinned).

### Row anatomy
- **Action row** (`_action_row` dash.py:912-934): id button, title
  (`background`), optional blinking cursor `▉` on the single newest open
  action across the whole visible wall (tooltip "newest action waiting on
  you"), then guided sub-rows: `int` (intuitive, reads FIRST — pinned
  dash.py:2235-2237), `why`, `rec`, ASCII sketch (`art`), reply box.
- **Task row** (`_task_row` dash.py:936-984): id button, title (`what`),
  a `meter` line: blocked banner (⏸ + "blocked on <id>") OR indeterminate
  scan (▓▓▓▓▓ pulsing) when no % readable OR a glyph progress bar + pct +
  "as of HH:MM" absolute clock; then `int` sub-row, sketch, reply box.
- **Term row** (`_term_row` dash.py:1012-1020): term as the "id" cell,
  intuitive as title, `def` sub-row for technical definition.
- **Diagram row** (`_diagram_row` dash.py:1045-1056): title as id-cell,
  live-rendered mermaid body.
- **Done row** (`_done_row` dash.py:1058-1067): id button (still
  clickable/copyable — "a mistaken done is easy to find again"), title
  (background or what), sketch, reply box.
- **Sub-row tree guide**: drawn as ONE continuous CSS rule (`.sub::before`/
  `::after`) with a corner on the last sub-line, not a per-row ├/└ glyph —
  a glyph-per-row broke when `why` wrapped past one line (css:245-252,
  selftest dash.py:1347-1355, 1839 area).
- **id button**: shows the 4-hex id, plus a smaller `sid` line (session
  code) when the row carries `_from` (merged view) or `sid` (flat view) —
  `_id_button` dash.py:434-452. Clicking copies via delegated listener
  (`COPY_JS`) — see "copy-id format" below.

### ASCII sketch ("art") two-ink rendering
- Recorded via `--diagram` on action/task/progress (skill §Sketches).
- Rendered as the LAST guided sub-row (`_art_sub` dash.py:1023-1042), inside
  a `.art`/`.art-in` panel: `.art` spans the row width (guide arm lands on
  it), `.art-in` is `inline-block` so a full-width panel can still center a
  multi-line drawing without shearing lines apart (selftest dash.py:
  2203-2209).
- Split into structure-vs-label runs by `tt_model.art_spans`
  (tt_model.py:345-379): ASCII stroke chars (`-|+/\<>^_=~*.:'`,;()[]{}#`)
  plus Unicode arrows (U+2190-21FF), box-drawing/blocks (U+2500-259F),
  geometric shapes (U+25A0-25FF) are "structure" (faint ink, `.art .st`);
  everything else is "label" text (full ink). Whitespace is neutral,
  extends the open run. Rendered as `ui.label` runs, **never markup** —
  comes out of a log file (dash.py comment 1023-1028).
- Sketch keeps its own geometry: `white-space:pre`, never folded by the
  row's `overflow-wrap:anywhere`; wider-than-card art scrolls in its own
  `overflow-x:auto` box, never widens the card (css `.art`).
- Drawn on actions, tasks, AND done rows (4 call sites pinned,
  dash.py:2225-2227) — a resolved item is exactly when the picture becomes
  reference material.

### Progress bar, pulse rule, read_ts
- `tt_model.progress_pct` (tt_model.py:135-150): explicit `--pct` always
  wins over text-scraped `tt_model.percent` (tt_model.py:89-110, explicit %
  beats a fraction in the same string, e.g. avoids reading "8/8 GPUs busy"
  as 100% when "(58% of 3000)" is also present). Bool `pct` values are
  rejected (`True` is an int in Python but not a reading).
- Bar glyphs: `blocks(pct, cells=14)` → (`█`×filled, `░`×empty) — snaps
  cell-by-cell, deliberately no tweening (dash.py:375-381).
- **Pulse**: a bar "lives" (CSS animation) for `LIVE_WINDOW=300s` after its
  reading (`read_ts` or fallback `ts`) was taken, started PARTWAY THROUGH
  via a **negative** `animation-delay` equal to the reading's age
  (`live_delay` dash.py:605-621) — so it finishes on its own with no repaint
  needed; a stale/future-stamped reading pulses `None` (not live).
- **`as of HH:MM`** label beside the bar is the wall-clock of the reading
  (`hm()` dash.py:2277-2279), ABSOLUTE not relative — a row only repaints on
  data change so a relative "12m ago" would freeze and lie.
- Indeterminate state (no % readable): 5-cell scanning sweep (`.scan`,
  staggered animation-delays) instead of an invented number.

### Blocked-on
- A task's `blocked_on` field names an action id; `tt_model.blocked_by`
  (tt_model.py:113-126) returns it only while that action is still open —
  derived, never stored — so answering the action unblocks every task
  pointing at it with no second write. Checked **across every session
  file** in the data dir, not just the file/window (`open_action_ids`
  tt_model.py:129-132), because a blocker can live in another day's file.
- Rendered as a `⏸ blocked on <id>` banner replacing the progress bar
  entirely (dash.py:949-951) — a blocked task is explicitly NOT drawn as a
  running one.
- Counted separately in the statusline tally (see below) and drives window
  repaint even though answering the blocker changes nothing in the task's
  own file (`open_acts` is in the paint signature, dash.py:3097, 3127).

### Reply box (draft/focus restore)
- Under EVERY action, task, AND done row (3 call sites pinned dash.py:
  1992-1996) — a finished item is still something worth following up on.
- A raw `<textarea data-reply="<id>">` + a `copy` button
  (`data-reply-copy="<id>"`) — `_reply_sub` dash.py:987-1010.
- Server-side widget is impossible: the wall calls `container.clear()`
  every poll (≈2s), which would wipe a server-bound input's text AND caret.
  `REPLY_JS` (dash.py:96-152) instead keeps drafts in a client-side `Map`,
  restores value+selection after each poll-triggered DOM mutation via one
  `requestAnimationFrame`-throttled `MutationObserver` callback (not once
  per mutation), and gives up caret restore on a deliberate `mousedown`
  elsewhere so it never yanks focus from something else the reader clicked.
- Copy button copies `"<id>: <trimmed answer>"` (skill's documented reply
  format) to the clipboard, confirmed only on a resolved `writeText`
  promise (never fires "copied" on a denied/insecure-context failure) —
  id is guarded (`[0-9a-f]{4,}`) before it ever reaches the clipboard call.

### Change gutters and seen-watermarks
- `changed_ids(state, since)` (dash.py:416-420): ids of actions/tasks whose
  `ts` is newer than a watermark; **terms never carry a gutter** (reference
  material, not a change needing attention).
- Watermark (`seen_at`) is **per window**, not per page: a window off the
  wall (zoomed away, scoped out, needs-me-hidden) freezes its watermark
  until it's back on the wall AND the user has touched the page since
  (dash.py:2344-2351). Floor is the page-open time so a freshly opened
  dashboard is quiet.
- Advanced **only by interaction** (click/keydown/scroll — captured via
  `SEEN_JS` dash.py:282-291) — NOT by the Page Visibility API alone, which
  cannot detect "covered but not backgrounded" on a permanently-visible
  second monitor (measured case in the code comment). `visibilitychange`
  (return from background) is kept as a secondary signal.
- Watermark advance uses `int(now)-1`, one second back, because
  `bin/table-talk` stamps `ts` in whole seconds and a float watermark could
  swallow an event written in the same second (dash.py:3161-3170).
- Not persisted across reload/tabs — each NiceGUI client run gets its own
  closure-local `seen_at`/`opened_ts` (dash.py:2352-2357).
- Rendered as `.row.changed` (action, `--act` colored) vs
  `.row.changed-job` (task, `--job` colored) — distinct colors per kind.

### Drawer tree + meters + filter that dims
- Left panel (`.dw`), 284px expanded / 54px collapsed rail
  (`.tt-main`/`.tt-collapsed` css:75-76), toggled by `\` — persists as
  `drawer_open` in storage.
- **Expanded**: filter input + hit-count + theme toggle (`.dw-find`) above
  a scrollable session tree (`.dw-tree`, `render_drawer` dash.py:2873-2962).
  Header shows "sessions" + `<n> · <k> projects`. A `sort:` row (bold marks
  current mode) cycles recent→actions→project on click.
  Per project: fold triangle (▾/▸, only when >1 session — a group of one is
  noise), project name, `<n> sessions` or the single date, a `meter_row`
  (● open-actions badge, ▶ open-tasks badge — greyed at zero rather than
  disappearing so columns stay aligned — plus an `[####    ]NN%` htop-style
  meter). Click the row scopes the wall to that project; click again (or
  the ✕ chip in the statusline) clears scope. Click the triangle
  (`click.stop`) folds/unfolds children without scoping.
  Sessions nested under an unfolded project: date, age (`ago(latest)`),
  same meter_row; click jumps/scrolls the wall to that window (`on_focus`).
- **Collapsed rail** (54px): one button per project — 3-letter `abbrev()`
  tag, `●<n>` badge, a thin htop bar (`rail-t`) — same click-to-scope
  target as the full row, so scope state survives width collapse.
- **Auto-fold rule** (`apply_fold_rules` dash.py:2825-2851): a project with
  zero open actions folds the FIRST time ever seen (persisted in
  `seen_projects`, so a reload never re-triggers it); a **rising edge** in
  open-action count (not just "any open") force-reopens a manually-folded
  project — a level test would spring a manually-folded group back open
  every 2s poll, so only the *increase* does it.
- **Context footer** (`ctx`, built once, dash.py:2410-2453): links to the
  nearest `CLAUDE.md` walking up from cwd (never above `$HOME` — symlinks
  resolved and refused if they escape home, `nearest_claude_md`
  dash.py:851-872), `~/.claude/CLAUDE.md`, the session-memory `MEMORY.md`
  file, a "⚙ settings" button that opens the settings FORM (not the raw
  file), the raw settings file path, and the current `config.example.toml`
  reference copy (refreshed on every start so it never goes stale
  independent of the user's own file) — entirely absent (no empty footer)
  when nothing exists.
- **Filter** (`dw-find` input, `search`): debounced via `filter_debounce_ms`
  config key (props dict, never a `.props()` string — injection-sensitive,
  see below). Dims non-matching rows (`.tt-dim{opacity:.78}` — chosen to
  clear a 3:1 contrast floor even under sunlight/laptop glare) and
  highlights matches (`.tt-hit`) — **never hides** an open action. Reports
  `N/M rows match` beside the box. On value change: re-ticks and either
  scrolls the first `.tt-hit` into center view, or (empty query) scrolls
  the wall back to top.

### Statusline
- Sits below `.tt-main` (drawer+wall), spans full width. Chips left to
  right (`dash.py:2459-2518`):
  1. Spinner (`SPINNER` braille frames, advances one frame per
     **successful** poll only — freezes on failure, so it reads as
     liveness you can trust rather than an abstract pulse) + "table-talk".
  2. Poll cadence: "Every Ns · last HH:MM:SS".
  3. **Tally** (`id=tt-tally`, read by two client-side scripts): `tally_text`
     (dash.py:228-242) → `"●N open  ▶M running  ⏸K blocked"` pieces, only
     non-zero ones shown, else "all clear". Blocked count is subtracted
     from running (a task waiting on a decision doesn't read as "in
     flight"). Counts EVERY session, not just what's on the wall (scope/
     zoom must not shrink the number and lie).
  4. **Port-mismatch segment** (`.sl-port`, hidden by default): shown when
     the on-disk config's `server.port` disagrees with the running port and
     `--port` wasn't explicit; message + "restart" button
     (`restart_offer`/`do_restart`, see Settings below).
  5. **Scope segment** (hidden by default): "showing <project> only" + ✕
     clear button.
  6. **Column buttons** "cols 1 2 3", highlighted button = effective count
     (zoom always shows as 1 effectively).
  7. **Key chips**: one per `KEYMAP` entry except `filter`/`unzoom` (those
     have their own affordance — the search box, and Esc). Each chip is a
     real `<button>` built from the same `KEYMAP` dict the keyboard handler
     uses, so click and key can never diverge. `needs-me` and `merge` chips
     get an `.on` highlight when active — the only two with visible state.
  8. Live clock, right-aligned.
- Tab title live-updates via `TAB_TITLE_JS` (dash.py:158-175): reads the
  `●N` count out of the tally text via regex and prefixes `document.title`
  with `(N) ` — a MutationObserver on `#tt-tally`.
- **Toast** (`TOAST_JS` dash.py:179-205): fires only when the open-action
  tally RISES versus its own baseline (read once before observing, so a
  page load/reconnect never fires a stale burst) — "N new action item(s)
  need(s) you", auto-dismiss after 5s.

### Every key (and its click equivalent)
`KEYMAP` (dash.py:313-315), dispatched through one `do()` function
(dash.py:2756-2793) shared by keyboard and every statusline chip / titlebar
button, so a key can never do something no click can:
| key | action | effect |
|---|---|---|
| `\` | drawer | toggle drawer open/collapsed |
| `m` | mark | toggle mark on current (`target()`) window — marks pack first |
| `z` | zoom | toggle zoom on current window (fills wall, 1 col) |
| `f` | fold | toggle fold on current window (collapses to titlebar-only) |
| `s` | sort | cycle drawer sort recent→actions→project→recent (`next_sort`) |
| `/` | filter | opens drawer if collapsed, then focuses the filter input (rAF) |
| `!` | needs-me | toggle: drop windows with 0 open actions (a running job alone does NOT keep a window) |
| `u` | merge | toggle merged/flat wall view |
| `?` | keys | open/close the key-list dialog |
| `Escape` | unzoom | if zoomed, un-zoom |
- Keyboard ignores keydown while an input/select/button/textarea has focus
  or the keys dialog is open, or on key-repeat (`on_key` dash.py:2795-2805).
- Every clickable control (drawer rows, window M/Z/▾, statusline chips) is
  a real `<button>`; NiceGUI's keyboard layer ignores ALL keystrokes while
  any button holds focus, so `BLUR_JS` (dash.py:255-260) blurs a button the
  instant it's clicked with a **real mouse** (`e.detail>=1`, never on a
  synthetic Enter/Space click which has `detail===0`, or a keyboard user's
  focus ring would be thrown to `<body>` after every activation).

### Merged vs flat view
- `u` toggles `ui.merged` (persisted, default from `cfg.ui.view=="merged"`).
- Merged: one window per **project**, holding every session's rows folded
  together with a `_from` tag (session code, or the file's date fallback
  for events recorded before session-stamping existed) —
  `tt_model.merge_projects` tt_model.py:244-264. On an id collision across
  files (legitimate: term/diagram "dedupe" can leave the same id in two
  dated files), the row with the **higher `ts` wins**, regardless of which
  file was processed last (pinned against a last-write-wins regression,
  tt_model.py comment 244-254 and selftest 706-717).
  `_id_button` shows both id and `sid`/`_from` in merged view.
- Flat: one window per session **file**; drawer always lists real session
  files regardless of wall mode (dash.py:3045).
- Clicking a drawer session row while merged resolves to its **project**
  key on the wall (`M.parse_stem(key)[1]`) before scrolling — pinned
  dash.py:2733-2735/1840-1843 (a merged wall never has the raw session key
  on it).

### Zoom / fold / mark
- All three are per-window boolean-ish state toggled via `on_window_action`
  (dash.py:2619-2628), stored as sets (`marks`, `folds`) or a single value
  (`zoomed`), all persisted (`app.storage.general`).
- Zoom: only one window zoomed at a time (toggling a new one doesn't clear
  the old one implicitly — `zoomed = None if zoomed==key else key`); scope
  change clears zoom (a zoom pointed at a window no longer on the wall is
  meaningless, dash.py:2717-2721).
- Fold: a folded window renders as titlebar-only; the packer costs it
  weight=1 (not its real content weight) since nothing is drawn
  (dash.py:3089-3092).
- Mark: packed first (`tt_model.pack` marked-first ordering), visually
  distinguished by a caret-colored border+inset shadow (css `.win.marked`).

### Transcript link resolution
- `transcripts()` (dash.py:541-565): scans **every** `~/.claude/projects/*/*.jsonl`
  (not just the dashboard's own project — a session's cwd need not match
  where the dashboard was started), builds `{4-char sid prefix: path}`.
  An ambiguous prefix (two files sharing 4 chars) is **dropped entirely** —
  linking to the wrong conversation is worse than no link. Derived fresh
  every poll (~0.2ms for 39 files measured), never cached/stored.
- Session-index button (`ix`) in the titlebar opens the resolved transcript
  via `open_path` with the transcript's own path added to `extra_roots` for
  that one call (widens confinement for exactly that file, dash.py:2678-2680,
  1862-1864) — it lives outside the data dir and the cwd.

### Path/URL link spans and open_command
- `tt_model.url_spans` (tt_model.py:428-446): only `http(s)://` schemes
  matched (never `file://`/`javascript:` — the match becomes an argv
  element handed to a launcher); lookbehind prevents matching mid-token
  tails; trailing sentence punctuation trimmed; control chars end the
  match.
- `tt_model.path_spans` (tt_model.py:452-497): a path-shaped substring is
  linked only if it (a) resolves (symlinks included) to an existing
  **file** (never a directory) (b) lands inside one of the allowed roots
  after resolution — existence+confinement is the entire filter, so prose
  full of slashes with no real backing file never becomes a button. Cheap
  early-out: a cell with no literal `/` never touches the filesystem at
  all (perf-motivated, comment tt_model.py:467-472). Paths with spaces are
  not detected (accepted limitation); a trailing `:LINE` suffix (e.g.
  `file.py:42`) is swallowed into the token and so fails to resolve too
  (also accepted).
- Roots = data dir + CWD + `config.links.extra_roots` (`link_roots`
  dash.py:463-473), resolved **once** at process start into module-global
  `ROOTS`, never re-resolved per rendered cell (perf).
- Click handler (`open_path`/`open_url` dash.py:476-520) **re-derives**
  confinement from scratch at click time (never trusts the rendered
  target) — the payload is data that came out of a log file, "no different
  from a log line that never should have linked." Launch is always an argv
  **list**, never `shell=True` (AST-scanned by selftest to guarantee no
  `shell=` keyword anywhere in the file, dash.py:1820-1825), so shell
  metacharacters in a filename stay inert. A failed launch (`open_command`
  not installed) prints a warning, never crashes.
- `link_spans` (dash.py:523-538) merges URL and path spans, URL wins on
  overlap (both can match the same slashy substring).
- `links.open_command` config key: `open` on macOS, `xdg-open` elsewhere.

### Copy-id format
- Clicking any `[data-id]` element (delegated single document listener,
  `COPY_JS` dash.py:84-94) copies to clipboard:
  - `SESSION: <sid> - ID: <id>` when the row carries a session code
    (merged view, or flat view rows that carry `sid`).
  - the bare `<id>` otherwise.
- Guarded: only fires for ids matching `^[0-9a-f]{4,}$` (never copies
  arbitrary text off a stray `data-id`); "copied" flash only added inside
  the `writeText().then()` success callback (never fire-and-forget) so a
  denied-permission/insecure-context failure never lies about a successful
  copy.
- Deliberately changed from copying a runnable `table-talk done <id>`
  command — an identifier that pastes as a command can't be used to refer
  to the item, grep for it, or build any other command from it (dash.py
  comment 80-83).

### Mermaid diagrams
- `securityLevel: "strict"` (bundled default, explicitly restated even
  though it's already the default — "a future config knob cannot silently
  relax it", dash.py:1046-1049) — diagram source comes from a log file.
- `theme: "base"` (a theme built to be overridden) + a per-render
  `%%{init:...}%%` directive (not `mermaid.initialize()` — travels with
  each render) setting `fontFamily` to the app's own mono stack and
  `fontSize:"12px"` (`MERMAID_INIT` dash.py:354-356) — mermaid measures
  labels using its OWN configured font, so a CSS-only font override
  overflows every box it already sized.
- **No hyphen** allowed anywhere in `MERMAID_INIT`: mermaid's directive
  sanitiser regex for `themeVariables` values is `^[\d "#%(),.;A-Za-z]+$`
  (no `-`) and ONE bad char blanks the ENTIRE value silently — verified
  against bundled mermaid 11.16.1 and pinned by selftest.
- Colors come from `tt.css` `!important` overrides keyed on the app's own
  theme tokens (`.mmd .node rect`, `.mmd .edgeLabel`, `.mmd .marker`,
  `.mmd rect.actor`, `.mmd text.actor>tspan`, `.mmd .noteText>tspan`, `.mmd
  .note`, `.mmd .labelBox`, etc.) rather than mermaid's own theming, because
  colors must swap live with light/dark/system mode and mermaid renders a
  static inline SVG stylesheet per diagram that a baked color can't follow
  (server can't know client's `prefers-color-scheme` in "system" mode).
  No blanket `.mmd text{}`/`.mmd p{}` rule (would collide with untouched
  diagram types whose base fills are cream, producing ink-on-cream in dark
  mode) — every themed selector is deliberately scoped.
- A parse error renders mermaid's own error graphic client-side; server
  never sees it.

### Settings dialog + restart offer + port_free check
- **Settings form** (`open_settings` dash.py:2547-2594): dialog whose
  fields are derived entirely from `tt_config.form_fields()`
  (tt_config.py:163-183) — never a second hardcoded field list that could
  drift from the validator. Fields: `ui.view` / `theme.default` /
  `server.host` (choice dropdowns), `theme.dark_theme` / `theme.light_theme`
  (choice, filtered to actually-dark/-light bundled themes + `""` for
  built-in), `server.port` / `ui.columns` / `server.poll_seconds` /
  `ui.filter_debounce_ms` (number, with validator-sourced min/max bounds).
  Color tokens are deliberately excluded from the form — "seventeen tokens
  across two modes is a colour-picker project", the raw file stays one
  click away for that.
- Save writes only the **changed** keys (`form_updates` dash.py:665-680) —
  the loaded config already has every default present, so writing back
  everything would materialize every default as an explicit line on first
  save. Uses `coerce()` (dash.py:694-712) to convert the number widget's
  always-float value back to int/float matching the *current* value's
  type, and to `None` (meaning "leave alone") when out of validator bounds
  — so a save never writes a value the loader will then reject with a
  warning nobody reads.
- Write goes through `tt_config.set_keys` — LINE SURGERY on the TOML text
  (see §Config below), never a re-serialize/dump — so comments and
  untouched keys survive.
- After save: message shows which keys need a restart (`needs_restart` —
  literally every key, since `main()` loads config once at startup);
  if any of `RESTART_KEYS=("server.host","server.port")` changed, the
  statusline port-restart button is also surfaced.
- **`ensure_config()`** (dash.py:817-848): a missing config file is created
  by COPYING `docs/config.example.toml` (which is fully commented — a bare
  defaults dump "teaches nobody anything") rather than dumping defaults;
  the user's OWN file is never rewritten once it exists; a
  `config.example.toml` reference copy beside it is refreshed on every
  start so it never goes stale relative to options added since the user's
  file was created (this is also linked in the drawer footer as "📑
  settings ref").
- **Port-restart flow**: `restart_offer(running_port, config_port,
  explicit)` (dash.py:643-655) is silent when they already agree or when
  `--port` was passed explicitly (a CLI flag always beats the file, forever
  — even across a later config edit). Otherwise it does a **real socket
  bind** (`port_free` dash.py:624-640, `SO_REUSEADDR`) to check the new
  port is actually free before ever offering — an exec onto a taken port
  doesn't move the dashboard, it **ends** it (process replaces itself,
  fails to bind, nothing left running); if taken, message says so and the
  restart button is hidden; if free, "restart to move there?" with a
  button. `do_restart()` (dash.py:2531-2545) re-checks free-ness at click
  time too, then `os.execv` re-execs the same interpreter/script (config
  re-read fully at startup, nothing threaded through manually).
- Config file's on-disk mtime is polled cheaply (stat only, not a full TOML
  parse) each tick and only re-`load()`ed when it actually changes
  (dash.py:3179-3190) — avoids parsing TOML every 2s for an answer that
  almost never differs.

### Theme mode toggle + 15 bundled themes + token overrides + contrast floors
- Mode toggle button in the drawer's filter bar (`dw-theme`, glyph `◐`
  system / `○` light / `●` dark — deliberately geometric glyphs the primary
  mono font actually has, not ☀/☾ which fall to inconsistent system
  fallback fonts) cycles `system→light→dark→system`
  (`cycle_theme` dash.py:2596-2604), persisted, applied via NiceGUI's
  `ui.dark_mode()` (`.auto()`/`.disable()`/`.enable()`).
- **15 bundled palettes** in `bin/themes.json` (Ayu Light, Catppuccin
  Latte/Mocha, Dracula, Everforest Dark/Light Hard, Gruvbox Dark/Light Hard,
  Kanagawa Wave, Nord/Nord Light, Rose Pine/Rose Pine Dawn, TokyoNight
  Day/Night), converted mechanically from iTerm2-Color-Schemes/Ghostty
  themes (README §Themes) via `tools/build-themes.py` (not read — out of
  scope). Each theme record: `{"tokens": {17 hex values}, "adapted":
  [token names moved off-palette], "dark": bool}`. `adapted` names exactly
  which of the 17 tokens were shifted from the literal terminal ANSI color
  because terminal accents assume large glyphs on their own background and
  here they're 9-12px UI text (a faithful yellow read ~1.8:1). Selected via
  config `theme.dark_theme`/`theme.light_theme` (name string, `""` = keep
  built-in Gruvbox-Dark-Hard/claude-code-light default).
- Config-file token overrides (`[theme.dark]`/`[theme.light]` tables) are
  re-applied ON TOP of a named theme, so changing one color doesn't require
  restating the other sixteen (`tt_config.load` tt_config.py:143-157).
- Contrast floors enforced by `tt_config.selftest` against EVERY bundled
  theme (tt_config.py:303-323): `ink`/`ink-2`≥4.5:1, `ink-3`≥3.5:1,
  `act`/`job`/`ok`/`gls`/`mag`/`caret`≥3.0:1, each measured against that
  theme's own `surface` token using real WCAG relative-luminance math
  (not a heuristic) — "a theme nobody can read is not a theme."
- `--hover` is deliberately NOT a configurable token — it's a *consequence*
  of `--surface` (computed direction, lightens on dark bg / darkens on
  light bg — opposite of `--sel`), never an independent choice, to avoid a
  second knob drifting out of sync (tt.css top-of-file comment, dash.py
  selftest 1331-1338).
- Theme CSS emission (`theme_css` dash.py:56-77): only tokens that DIFFER
  from `tt.css`'s own stylesheet defaults are emitted (`inherit` is the
  correct behavior for a match — "a restatement of the default is a second
  place to change it"); every value is re-validated as a hex color on the
  way OUT (a config file is a second untrusted route INTO the stylesheet,
  same treatment as on the way in) — comprehension iterates the DEFAULTS
  key set, never the file's own keys, so an attacker-controlled key *name*
  is only ever matched, never interpolated into the emitted CSS.

### UI-state persistence
- Everything lives in `app.storage.general` (NiceGUI's server-side JSON
  store), keyed `tt.<name>` — theme mode, `marks`, `folds`,
  `groups_folded`, `zoomed`, `scope`, `needs_me`, `current` (the m/z/f
  target), `cols`, `sort`, `drawer_open`, `merged`, `seen` (per-project
  "first ever seen open-action count" for the auto-fold rule).
  (dash.py:2315-2357 `store`/`put` helpers, and every read site.)
- Physically written to `~/.local/share/table-talk/.ui/` — **beside the
  data**, not beside the launch directory — because NiceGUI resolves its
  storage path once, at import time, onto a class attribute
  (`NICEGUI_STORAGE_PATH` env var set via `os.environ.setdefault` BEFORE
  any `nicegui` import anywhere in the process, dash.py:22-36, pinned by
  AST-order check dash.py:1849-1853) — assigning `app.storage.path` later
  does nothing. Pre-v5 this landed in a `.nicegui/` folder beside whatever
  CWD `serve` ran from (README §Configuration notes these are now safe to
  delete).
- Explicitly **NOT** persisted: `wall_width` (a property of the current
  browser window, not a preference — two tabs at different widths must not
  fight over column count), `seen_at`/`opened_ts`/`touched` (per-process
  watermark state — "since you last looked", and a reload IS you looking).
- Two tabs open simultaneously get **independent** watermarks/wall_width
  (each NiceGUI client run is its own closure) but **shared** marks/folds/
  scope/etc. via the server-side store (loopback single-user assumption,
  documented "no storage_secret needed").

### The 2s poll and per-window paint guard
- `ui.timer(poll_seconds, tick)` (default `server.poll_seconds=2.0`,
  validated range 0.2–∞ — 0 or negative pegs a CPU core re-globbing the
  data dir every event-loop tick, tt_config.py selftest 509-518).
- `tick()` (dash.py:3209-3228) wraps `poll()` in try/except: **one bad
  poll degrades the statusline** (spinner freezes, cadence chip turns
  `.sl-stale`) rather than killing the recurring timer — "a frozen spinner
  beside a stale timestamp is the whole watch(1) idiom."
- `poll()` (dash.py:3030-3206): re-globs+`fold_cached`s every `*.jsonl` in
  the data dir every tick (cheap in steady state — `fold_cached` re-parses
  only on mtime/size change, `tt_model.fold_cached` tt_model.py:62-75), then
  groups/sorts/packs/paints. Clock is read **before** file reads each tick
  (a write landing between the two must not be mis-stamped as already-seen).
- **Per-window paint guard**: each window has its own comparison "paint
  signature" `(query, newest, wall_states[k], changed, open_acts)`
  (dash.py:3127); `paint_window` is called, and wrapped in its OWN
  try/except (`logging.exception("could not paint window %s", k)`,
  dash.py:3133-3138) so **one unrenderable row costs only its own card**,
  never the whole wall — a `tick()`-level guard is all-or-nothing and the
  *next* poll would fail identically at the same spot, presenting as a
  permanent freeze that only looks like a stale spinner. The signature is
  recorded **after** a successful paint, never before (an exception
  mid-build must not mark a half-drawn window as up-to-date forever, pinned
  dash.py:1837-1839, 3139-3143).
- Drawer has an analogous signature (`drawer_sig`/`container.tt_sig`,
  dash.py:2859-2876/2962) recorded only after a clean build for the same
  reason.

### Live-session heartbeat (tt-beat markers)
- `bin/tt-beat` (not read in full — out of scope per task list, but its
  contract is exercised by dash.py/README): a `PostToolUse` hook that
  touches an empty file `~/.local/share/table-talk/.beat/<4-char-sid>` on
  every tool call.
- Dashboard reads it via `live_sessions(now, beat_dir, window=120)`
  (dash.py:574-596): a session counts as "live" if its beat file's mtime is
  within the last 120s AND not in the future (a future mtime = clock jump,
  never treated as live). No hook installed / no directory / unreadable
  file → empty set, silently — "nobody is known to be working" rather than
  an error, so an install with no hook behaves exactly as before.
- This is the ONLY signal in the whole dashboard that can say "an agent is
  working *right now*" — everything else in the log records what
  *happened*. Rendered as the titlebar `◉` beat flag.

### The demo dir
- `TABLE_TALK_DIR=docs/demo ./bin/table-talk serve` runs the dashboard
  against a frozen, seeded snapshot directory (README §"See it first") —
  two projects, three sessions, live actions and jobs, one mermaid diagram,
  a glossary — without touching the user's real data dir, and can run
  alongside a real instance on a different `--port`. `TABLE_TALK_DIR` env
  var is the mechanism (read at dash.py:20 import of `tt_model.DATA_DIR`,
  and identically in `bin/table-talk`:18) — points BOTH the CLI and the
  dashboard at an arbitrary data directory; this is also the documented way
  to keep separate work logs (e.g. per client/employer).

---

## 2. Data flow

```
*.jsonl (append-only log files, one per date+project)
   │  bin/table-talk appends events with `fold()`'s shallow-merge-by-id contract
   ▼
tt_model.fold_cached(path)            # per-file, mtime/size-cached
   │  -> {id: {...event fields, later keys override earlier}}
   ▼
tt_model.summarize(state)             # per-file counts
tt_model.group_sessions([(stem,state)])   # -> per-project groups, sessions newest-file-first
tt_model.merge_projects([(stem,state)])   # -> {project: one folded state}, _from-tagged (only for merged wall view)
tt_model.sort_groups(groups, mode)        # -> display order (recent/actions/project)
tt_model.roll_up(summaries)               # project totals = SUM of sessions', never averaged
   │
   ▼ (dash.py poll())
tt_model.pack(keys, ncols, weights, marked)   # -> [[keys...] per column]
   │
   ▼
render_window_body() / render_drawer() / statusline updates   # NiceGUI element tree mutation
```

### Pure functions a port must reproduce or call (tt_model.py — stdlib
only, zero deps, this whole module is designed to be portable):

| Function | Signature (py) | One-line semantics |
|---|---|---|
| `fold(path)` | `Path -> {id: dict}` | Shallow-merge JSONL events by id in file order; tolerates OSError/bad-utf8/bad-json/missing-ts/non-numeric-ts per line without raising. |
| `fold_cached(path)` | `Path -> {id: dict}` | `fold()` gated by an `(mtime,size)` cache key; module-level dict cache. |
| `parse_stem(stem)` | `str -> (date, project)` | Regex-split `YYYY-MM-DD-project`; undated stems return `("", stem)`. |
| `percent(text)` | `str -> int\|None` | Scrape a %, else a fraction, from free text; % always wins over a fraction found in the same string. |
| `blocked_by(ev, open_actions)` | `(dict, set[str]) -> str\|None` | `ev["blocked_on"]` iff it's a member of the open-actions set. |
| `open_action_ids(states)` | `Iterable[dict] -> set[str]` | ids of every open (non-done) action across given states. |
| `progress_pct(ev)` | `dict -> int\|None` | Explicit numeric `pct` (bool rejected) wins, else `percent(progress text)`. |
| `summarize(state)` | `dict -> dict` | Per-file counts: open_actions, open_tasks, resolved, recorded, pct, latest. Terms excluded from resolved/recorded; latest spans everything. |
| `roll_up(summaries)` | `list[dict] -> dict` | Sum counts across sessions (never average pct). |
| `group_sessions(sessions)` | `list[(stem,state)] -> list[dict]` | One group per project (first-seen order), sessions sorted newest-first inside, `index` = position within project. |
| `merge_projects(sessions)` | `list[(stem,state)] -> {project: state}` | Per-id merge across a project's files tagging `_from`; ties resolved by higher `ts`, not last-processed. |
| `sort_groups(groups, mode)` | `(list[dict], str) -> list[dict]` | Reorder for display; `"actions"` mode also reorders each group's `sessions`; returns NEW dicts, never mutates input. |
| `weight(state)` | `dict -> int` | Content-derived packing-height estimate; never from measured pixels. |
| `pack(keys, ncols, weights, marked)` | `(...) -> list[list[str]]` | Deterministic greedy shortest-column bin-pack; marked keys placed first. |
| `art_spans(text)` | `str -> [(chunk, is_structure:bool)]` | Split ASCII/Unicode art into structure-vs-label runs; lossless reassembly. |
| `row_text(ev)` | `dict -> str` | Concatenate every user-visible text field for substring search. |
| `parts(text, q)` | `(str,str) -> [(chunk, is_match:bool)]` | Case-insensitive non-overlapping match split, original casing preserved. |
| `marked(text, q)` | `(str,str) -> str` | HTML-escaped, matches wrapped in `<span class="tt-hit">`; escape happens AFTER split (order matters for correctness, see tt_model.py:407-419). |
| `url_spans(text)` | `str -> [(start,end,url)]` | http(s)-only URL spans, lookbehind-anchored, trailing punctuation trimmed. |
| `path_spans(text, roots)` | `(str, list[Path]) -> [(start,end,resolved_str)]` | Path-shaped substrings that resolve (symlinks followed) to an existing FILE confined under one of `roots`. |
| `project_roots(states_by_file)` | `dict[str,dict] -> {project: root_path}` | Newest-`ts` `root` field per project (only used by the job runner, not the read-only dashboard render path). |

`bin/table-talk-dash.py` itself has a smaller set of pure(ish) helpers a
port would also want (all trivially portable, no NiceGUI dependency):
`tally_text`, `blocks`, `resolved_cells`, `bar_for`, `_dim`/`_hits` (query
matching), `next_sort`, `toggle`, `abbrev`, `default_cols`, `cols_for`,
`layout_key`, `ago`/`stamp`/`hm` (time formatting), `live_delay`,
`restart_offer`, `form_updates`, `needs_restart`, `coerce`, `open_rows`/
`done_rows`/`term_rows`/`diagram_rows`/`changed_ids`, `link_roots`,
`link_spans` (composes `tt_model.url_spans`+`path_spans`), `theme_css`.
Everything else in dash.py (`build_window`, `render_window_body`,
`render_drawer`, `poll`, `main`, `_action_row` etc.) is NiceGUI-element
construction — the part a Svelte port fully replaces rather than calls.

---

## 3. Python-only parts vs portable parts

**Portable logic (pure functions, stdlib-only, no UI dependency)** — the
entirety of `tt_model.py` and `tt_config.py`'s `_merge`/`load`/`valid_colour`
/`themes`/`set_keys`/`form_fields` core. This is the layer a TypeScript port
of `tt_model` must reimplement completely, since GPUI/Svelte cannot import
Python. Every function in the table above needs a TS equivalent with
matching behavior — most are small (a page or less) and their exact
edge-case handling is what's pinned by selftest, listed below.

**Python-only / must-be-replaced-or-bridged**:
- **NiceGUI element tree + reactive DOM patching** (`ui.element`, `.move()`,
  `.classes(replace=...)`, `container.clear()`) — the entire render layer.
  A Svelte port replaces this with real Svelte components/reactivity; no
  equivalent needed, just parity of *output*.
- **The client-side JS snippets** (`COPY_JS`, `REPLY_JS`, `TAB_TITLE_JS`,
  `TOAST_JS`, `BLUR_JS`, `SEEN_JS`, `WIDTH_JS`, `scroll_js`) — these encode
  real UX contracts (draft/caret persistence across rebuilds, clipboard
  format, seen-watermark triggers, click-to-scroll) that a GPUI-native app
  must reproduce with native event handling instead of injected `<script>`
  tags — GPUI has no DOM/webview to inject into, so these become ordinary
  native event handlers/state.
- **`app.storage.general`** (NiceGUI's server-side JSON KV store) — a
  native app needs its own persistence (a JSON file, sqlite, or platform
  prefs) for the same keys listed under "UI-state persistence" above.
- **NiceGUI-specific plumbing**: `NICEGUI_STORAGE_PATH` env-var timing
  hack, `ui.mermaid()` (wraps mermaid.js — a native GPUI renderer has no
  browser mermaid; diagrams need either a native mermaid-to-SVG/native-shape
  pipeline or an embedded webview just for that one panel), `ui.dark_mode()`,
  `ui.keyboard()`, `ui.timer()`, `ui.run()` (uvicorn/websocket server —
  irrelevant to a native single-process GPUI app; poll loop becomes a
  native timer/file-watcher instead).
- **`tt_jobs.py`-backed job runner** (`start_job`/`run_job`, the
  `claude-agent-sdk` dependency, `ClaudeSDKClient`/hooks/gate) — a
  Python-async-specific integration; out of this task's reading scope but
  its wall-visible surface (jobs section, `#`/`◉` flags, blocked banner)
  is a UI feature a port needs, even if the job-*starting* mechanism is
  reimplemented differently (or deferred, per the task's non-negotiable
  "every feature preserved or explicitly argued away").
- **`subprocess.Popen`/`os.execv`** (open_path/open_url/do_restart) — OS
  process-launch primitives; any language has an equivalent, just not the
  exact Python API.
- **`fcntl.flock`** (CLI's `mint_lock` in `bin/table-talk`, not the
  dashboard) — POSIX-only advisory locking for id-minting; a cross-platform
  Rust/TS CLI replacement would need `flock`-equivalent (e.g. a lockfile
  crate) since the non-negotiable requires macOS too.

**What a TS port of tt_model must reimplement** (already itemized in the
function table above) — restated as the priority list:
1. `fold`/`fold_cached` (JSONL shallow-merge-by-id + mtime/size cache) —
   this IS the log format contract; must byte-for-byte match `bin/table-talk`'s
   own `fold()` (they're independently maintained but pinned to agree,
   `bin/table-talk`:21-42 vs `tt_model.py`:17-56 — note the CLI's version
   does NOT do the ts-normalization or the utf-8 backslashreplace re-scrub
   that `tt_model`'s does; the CLI scrubs at WRITE time via `scrub()`
   instead, `bin/table-talk`:60-75, so the two fold()s are deliberately
   asymmetric: dash-side fold defends against logs written before the CLI
   added scrubbing).
2. `percent`/`progress_pct` (regex-based progress scraping with explicit-%
   priority).
3. `summarize`/`roll_up`/`group_sessions`/`merge_projects`/`sort_groups`
   (the whole grouping/rollup pipeline).
4. `weight`/`pack` (content-based height estimate + deterministic greedy
   packer — must be pixel-independent to avoid layout thrash).
5. `art_spans` (ASCII-art structure/label classifier — Unicode codepoint
   ranges must match exactly).
6. `url_spans`/`path_spans` (security-sensitive link detection — every
   "must never" in the selftest is a real historical bug: shell injection
   via unescaped filenames, XSS via unescaped ui.html, symlink escape,
   `javascript:`/`file://` schemes reaching a launcher).
7. `blocked_by`/`open_action_ids` (cross-file blocking derivation).

### Behaviours pinned by which selftest (so a port can carry the same pins)

**`tt_model.selftest()`** (tt_model.py:500-916) pins:
- `fold`: partial-update field preservation, done-status overwrite, garbage
  line skip, missing-file→`{}`, invalid-utf8 tolerance, cache invalidation
  on mtime/size change, lone-surrogate escaping (`\udcff`), unreadable file
  (chmod 000)→`{}` not raise, directory-named-`*.jsonl`→`{}` not raise,
  non-numeric `ts`→coerced to 0 (not raise on `summarize`), partial update
  with no `ts` must not erase the real one.
- `parse_stem`: hyphenated project names, undated stems.
- `percent`: explicit-%-over-fraction (the real alpha-lac 58%/8-8-GPUs
  case), rounding, over-100 clamp, zero-denominator rejection, numeric
  ranges/timestamps not mistaken for fractions.
- `blocked_by`/`open_action_ids`: only OPEN actions block, a task never
  blocks a task, a vanished blocker doesn't hold hostage.
- `progress_pct`: explicit `pct` beats scrape (real gpn-micro 92%-of-genes
  case), `0` is a real reading not a missing one, bool/string/list/dict
  `pct` values rejected and fall back to scrape.
- `summarize`: terms excluded from resolved/recorded but counted in
  `latest`; empty state → pct 100, latest 0.
- `roll_up`: sum not average (documented real phephree 33% vs naive-29%
  case); empty list → pct 100.
- `group_sessions`/`sort_groups`: project first-seen order, session
  newest-file-first, `index` stability, `actions` mode reorders sessions
  WITHIN a project by need not just projects, recency tie-break so order
  never flaps, sorting returns new dicts (no caller mutation).
- `merge_projects`: one state per project, `_from` tagging (sid or file's
  date fallback), ties resolved by higher `ts` regardless of file-processing
  order (#140 regression pin).
- `weight`: empty state still costs 1 unit (titlebar), done items cost
  nothing but a diagram still costs height even when done, terms cost
  nothing, long prose costs more, progress TEXT must not affect weight
  (changes every poll — would thrash the pack).
- `pack`: deterministic, marked-first, columns never exceed key count,
  missing weight defaults to 1.
- `art_spans`: word `eval`'s embedded `v` is not mis-read as an arrowhead
  (mid-word split is never allowed), box-drawing is structure, whitespace
  extends the neutral run, lossless reassembly (~500 randomized fuzz cases).
- `row_text`: non-string id is safe (coerced to str).
- `parts`/`marked`: original-casing preservation, non-overlapping matches,
  a ~48,000-case property test (2000 random strings × ~10 hostile-query
  probes) asserting `strip(marked(t,q)) == html.escape(t)` AND
  chunk-reassembly losslessness for every combination — this is the load-
  bearing proof that highlighting can never leak unescaped HTML regardless
  of adversarial input.
- `url_spans`/`path_spans`: scheme allowlist (http/https only), trailing
  punctuation trimming, no bare `#ref` linking, symlink-escape refusal,
  space-containing paths not detected (documented limit), traversal (`..`)
  refused, directories never linked, span indices exact against the
  ORIGINAL string (not the trimmed token).
- `project_roots`: newest-`ts`-wins per project, malformed root/ts values
  skipped not crashed on (data dir is hand-editable text).

**`tt_config.selftest()`** (tt_config.py:272-557) pins:
- `DEFAULTS["dark"/"light"]` token values are kept byte-identical to
  `tt.css`'s own `:root`/`body.body--dark` blocks (cross-file consistency
  guard) — a port copying tt_config's DEFAULTS also inherits this
  cross-check obligation against whatever stylesheet/theme file it uses.
- Every one of the 15 bundled themes: exact token-set match against
  DEFAULTS's key set, every value passes `valid_colour`, every WCAG
  contrast floor (see §1 Theme section) against `surface`, `Gruvbox Dark
  Hard` reproduces the original literal defaults exactly (0 adapted
  tokens).
- `valid_colour`: hex-only (3/4/6/8 digit), rejects CSS-injection strings,
  rejects trailing newline (documented Python `$`-vs-`\Z` regex gotcha).
- `load()`: missing file → DEFAULTS exactly; malformed TOML → DEFAULTS +
  warning, no exception; unset keys keep defaults; set keys override;
  `load()` never returns a dict that IS (identity) the shared DEFAULTS
  object (mutation-safety); invalid-colour dropped independent of valid
  siblings; wrong-typed/bool-for-number values dropped with warning;
  unknown sections ignored; unknown key WARNS (the one mistake that must
  never be silent, unlike other validation failures); named-theme
  replaces base palette wholesale, file's own token overrides still win on
  top of a named theme; `host`/`view`/`theme.default` choice validation
  (typo never silently widens network exposure — this is a security-
  relevant pin, not just cosmetics); `poll_seconds` 0/negative rejected
  (CPU-pegging); `port` <1/>65535/0 all rejected (bind failure / random
  ephemeral port); `columns` >3 rejected, 0 (auto) explicitly allowed;
  negative debounce rejected.
- `set_keys()`: every comment preserved, untouched keys preserved, new key
  inserted after the section's last REAL (non-blank) line not past the
  blank separator (would misattribute to the next section), a section-
  absent key appends the section without disturbing existing ones, a
  write that would corrupt semantics inside a TOML multi-line string is
  caught by the read-back-and-compare safety net and the file is left
  byte-identical to before, a hostile value that would break the file
  syntactically is refused and the temp file cleaned up (no half-written
  file survives a refusal), atomic write via `os.replace` (never a
  half-written config on crash).
- `form_fields()`: keys are a superset of `_CHOICES`, `server.port` present,
  number fields carry the VALIDATOR's own bounds (never invented bounds),
  dark/light theme dropdowns are actually filtered by polarity and are NOT
  identical to each other.

**`bin/table-talk-dash.py selftest()`** (dash.py:1268-2259) additionally
pins (beyond what's already folded into §1 above): the exact CSS token
values for both palettes (design-token regression guard), font-fallback
stack ORDER (`--mono`: primary → Adwaita Mono → Noto Symbols — coverage
order, not arbitrary), hover-vs-selection color independence, tree-guide
CSS geometry (the `margin-top`/`bottom:-Npx` bridge must match exactly or
the guide visually disconnects), an AST walk over the WHOLE source file
banning (a) any non-constant argument to `.props()` (the injection vector
that let a crafted `--project` value execute `onmouseover=` JS) and (b) any
`shell=` keyword anywhere (the injection vector for filenames with shell
metacharacters), exact call-count pins (`_reply_sub` called exactly 3×,
`_art_sub` exactly 4×, `theme_btn.props["title"]` set exactly 2×) that
exist specifically because a prior mutation-testing pass found these
call-sites could be silently dropped without any other test catching it,
and ordering pins (`paint_window` before `win["sig"]=sig`, `int` sub-row
before `why`/`rec`, `os.environ.setdefault(NICEGUI_STORAGE_PATH)` before
`from nicegui import`) that encode "this line must run before that line or
a subtle bug reappears" contracts a port must independently guarantee via
its own architecture (e.g. by construction, since a native app doesn't
share NiceGUI's late-binding-storage-path hazard).

---

## 4. Config surface (every key) and hooks the GUI depends on

### Config keys (`tt_config.DEFAULTS`, tt_config.py:34-52; every key, its
default, type, and validation)
| Key | Default | Validation |
|---|---|---|
| `server.host` | `"127.0.0.1"` | choice: `"127.0.0.1"` \| `"0.0.0.0"` |
| `server.port` | `8731` | int range 1–65535 |
| `server.poll_seconds` | `2.0` | float range 0.2–∞ |
| `ui.view` | `"merged"` | choice: `"merged"` \| `"flat"` |
| `ui.columns` | `0` (auto) | int range 0–3 |
| `ui.drawer_open` | `true` | bool |
| `ui.filter_debounce_ms` | `100` | int range 0–∞ |
| `ui.collapsed_sections` | `["glossary","done"]` | list (section names: act/job/dia/gls/ok internally) |
| `links.open_command` | `"open"` (macOS) / `"xdg-open"` (else) | string, no format check (launched as argv[0]) |
| `links.extra_roots` | `[]` | list; non-string entries silently dropped |
| `theme.default` | `"system"` | choice: `system`\|`light`\|`dark` |
| `theme.dark_theme` | `""` | choice: `""` or a bundled dark theme name |
| `theme.light_theme` | `""` | choice: `""` or a bundled light theme name |
| `theme.dark.*` (16 tokens) | Gruvbox-Dark-Hard values | must be valid hex colour, else dropped w/ warning |
| `theme.light.*` (16 tokens) | claude-code-light values | same |

Environment overrides: `TABLE_TALK_DIR` (data dir, shared by CLI+dash+
tt_model), `TABLE_TALK_CONFIG` (config file path, tt_config.py:10-11).
File location: `~/.config/table-talk/config.toml` (TOML), unknown top-level
sections/keys are ignored with a stderr warning (never silently merged or
silently dropped).

### Hooks the GUI depends on
| Hook | Event | Purpose | GUI dependency |
|---|---|---|---|
| `bin/tt-beat` | `PostToolUse` | Touches `~/.local/share/table-talk/.beat/<4-char-sid>` | Powers the `◉` "working right now" titlebar flag (`live_sessions`, dash.py:574-596). Absent → flag simply never shows, no crash/degradation elsewhere. |
| `bin/tt-ref` | `UserPromptSubmit` | Records every standalone 4-hex word typed by the user | CLI-side only (drives the "named by the user and still open" nudge); not read by the dashboard's render path directly — a CLI/skill-loop concern, not a rendering one. |

Both hooks are installed via `table-talk install-hook` / `./install.sh`
(merges into `~/.claude/settings.json`, idempotent re-run, `--remove` to
undo); both need `jq`; both fail silently/exit-0 on any error so a missing
hook degrades to "the feature it powers just doesn't show anything" rather
than breaking the dashboard. A GPUI/Svelte port that keeps the same hook
contract (same file-touch mechanism, same `.beat/` directory, same
`Post/UserPromptSubmit` shell-hook shape) needs no changes here at all —
these are Claude-Code-side hooks writing to a filesystem location the new
GUI can poll exactly the same way `live_sessions()` does.
