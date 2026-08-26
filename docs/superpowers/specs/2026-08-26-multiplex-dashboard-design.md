# Multiplex Dashboard — Design Spec (2026-08-26)

Supersedes the dashboard section of `2026-08-25-table-talk-design.md`. The CLI,
the JSONL event format, and the fold contract are **unchanged**.

## Purpose

Rebuild `bin/table-talk-dash.py` so it reads as a tool someone who lives in a
terminal built, not a weekend project. Approved by Gabriel 2026-08-26 after three
rounds of rendered mockups (decision `d679`).

Design record: <https://claude.ai/code/artifact/6a1e0d83-9ee5-4607-b956-71abe16e503b>
(the merged direction — the wall, the prose, the session tree, the packer).
The three original directions: <https://claude.ai/code/artifact/4d302861-1cd0-4fc4-a0c4-851ff759c5bb>

## The direction

Three layouts were proposed and one merged direction chosen:

- **Wall (from Multiplex).** Every session file is a window on a tiling wall,
  wearing tmux window flags. Carries the identity.
- **Prose (from Ledger).** `why` and `rec` are set in a proportional face on
  box-drawing tree guides, not in table cells. Carries the reading.
- **Tree (from Supervisor).** A foldable left drawer grouping sessions by
  project, each row with an `htop` meter. Carries navigation at scale.

## Palettes (locked, exact)

Dark is Ghostty **gruvbox-dark**; light is Ghostty **claude-code-light**. One
control drives both; three theme states (system / light / dark) as today.

| role | dark | light |
|---|---|---|
| ground (page) | `#1d2021` | `#e8e6dc` |
| surface (window) | `#282828` | `#faf9f5` |
| surface-2 (titlebar, statusline, track) | `#32302f` | `#f2f0e8` |
| ink | `#ebdbb2` | `#141413` |
| ink-2 (secondary) | `#a89984` | `#6f6e68` |
| ink-3 (faint) | `#928374` | `#93918a` |
| selection / hover | `#665c54` | `#e8e6dc` |
| cursor / focus / mark | `#8ec07c` | `#d97757` |
| action open | `#fb4934` / `#cc241d` | `#a53a2e` / `#d97757` |
| work running | `#83a598` / `#458588` | `#3668a0` / `#6a9bcc` |
| glossary | `#fabd2f` / `#d79921` | `#8a6a10` / `#b88a28` |
| done / progress | `#b8bb26` / `#98971a` | `#4a7038` / `#788c5d` |
| key hints | `#d3869b` | `#7a4a82` |

Two deliberate reads of the source themes, both approved:

- The page ground sits one step under the window surface (`#1d2021` is canonical
  gruvbox hard; `#e8e6dc` is the light theme's own selection colour), so windows
  separate **without any drop shadow**.
- Terracotta `#d97757` is claude-code-light's declared *cursor* colour, so it
  appears only where a cursor, focus ring, or mark would — never as a wash.

`ink-2` and `ink-3` in light are derived neutrals (the palette's `#b0aea5` is too
faint for body meta on cream). Every other value is verbatim from the theme.

## Typography

- Structure, ids, counts, meters, statusline: **JetBrains Mono** (400/500/700/800),
  falling back to `ui-monospace, SF Mono, Menlo, Consolas, monospace`.
- `why` / `rec` prose only: **Fira Sans** (400/500), falling back to
  `system-ui, -apple-system, Segoe UI, sans-serif`.
- Fonts are bundled or fall back silently; the dashboard is loopback-only and
  **must not depend on a network font fetch**.
- `font-variant-numeric: tabular-nums` on every column of digits.

## Layout

```
┌────────────────┬─────────────────────────────────────────────┐
│ DRAWER 284px   │  WALL — N columns of windows                │
│  session tree  │                                             │
├────────────────┴─────────────────────────────────────────────┤
│ statusline (28px, fixed at the bottom, tmux-style)           │
└──────────────────────────────────────────────────────────────┘
```

### Drawer (284 px expanded / 54 px collapsed)

A tmux `choose-tree`: projects with their session files folded underneath.

- **Group by** the project component of `<YYYY-MM-DD>-<project>.jsonl`.
- **A project with one session renders as one flat row** — no disclosure
  triangle, no child. A group of one is noise.
- Guides are literal box-drawing glyphs (`▾ ▸ ├ └ │`) in a fixed 15 px column so
  the verticals connect; only the row *content* is indented.
- Every row (project and session) carries `●open ▶running` badges and a bracketed
  `htop` meter with a right-aligned percentage.
- **Fold state** per project in `app.storage.general`. A project with zero open
  actions is folded on load. A poll that adds an open action **forces its group
  open** — a fold must never hide something that just started needing you.
- **Sort**: `recent` (default) | `actions` | `project`. Sorts groups and their
  children together. This is the **only** ordering control; the wall follows it.
- **Clicking a project row scopes the wall** to that project's windows and the
  statusline shows `showing <project> only` with an ✕ to clear. Clicking again
  clears. Clicking a session row scrolls the wall to that window.
- **Collapsed** to 54 px: one row per project — three-letter tag, open-action
  count, meter. The "is anything waiting on me?" answer must survive the collapse.

### Wall

- Windows are titled `project:index` (tmux session:window notation), index 0 =
  newest file for that project.
- **tmux window flags** in the titlebar: `!` bell (open actions, gently pulsing),
  `#` activity (work running), `M` marked, `Z` zoomed, `*` current.
- Sections are prompt lines: `❯ actions --open (3)`, `❯ jobs (2)`,
  `❯ glossary (5) ▸`, `❯ done (4) ▸`. Glossary and done collapsed by default.
- An action row is `id` in a gutter, the background as the title line, then
  `├─ why` and `└─ rec` as prose sub-lines on tree guides.
- A job row is `id`, what, then a block-glyph meter plus the raw progress text
  kept verbatim.
- Footer: `▰▰▰▱▱ 4/7 resolved` cells, all-green when clear.
- Empty sections say `nothing needs you` / `nothing running`, never "No data".

### Statusline (bottom, tmux-style)

`⠹ table-talk │ Every 2.0s · last HH:MM:SS │ ●N open ▶N running │ [showing X only ✕] │ cols 1│2│3 │ \ drawer m mark z zoom f fold ? keys │ HH:MM:SS`

- The braille spinner advances **one frame per successful poll** and freezes red
  when a poll fails. `watch(1)` honesty, not an abstract pulse dot.

## The packer

A window's height depends on its item count, and that changes under the user
every 2 s. Masonry is therefore **forbidden**: it reassigns columns on every
content change, which slides a window out from under the cursor mid-read.

1. **Estimate from content, never from measured pixels.** Weight is derived from
   item counts plus a rough line estimate from action text length.
2. **Order by the drawer's sort.** The wall has no sort of its own.
3. **Marked windows first** — which puts them at the top of the leftmost column.
4. **Greedy shortest column**: walk the ordered list, drop each window into
   whichever column is currently lightest.
5. **Re-pack only on a set change** — a file appearing or disappearing, a sort
   change, a mark, a fold, a scope change, a column-count change. **Never** on a
   poll that only changed text inside an existing window.

Columns drift out of balance as items accumulate. That is the accepted trade:
drift is quiet, hopping is loud. Folding a window is a set change and re-packs.

## Rearranging (tmux's own alphabet)

| control | key | behaviour |
|---|---|---|
| Mark | `m` | Pins a window to the front of the wall whatever the sort says, with a cursor-coloured rail down its left edge. Ids persisted. |
| Zoom | `z` | One window fills the wall; the drawer stays. `Esc` or `z` restores. |
| Fold | `f` | Collapses to titlebar + resolved meter. Weight drops to 1; the wall re-packs. |
| Columns | — | `1│2│3` in the statusline. Defaults by width (3 ≥ 1800 px, 2 ≥ 1200 px, else 1); an explicit choice is persisted. |
| Scope | — | Drawer project row. Never a silent filter. |

**Drag and drop is deliberately out of scope.** It needs a sortable library,
fights a DOM that re-packs underneath it, and is ambiguous the moment the sort
changes. Mark + fold + zoom covers the need. Add dragging only if an order that
*isn't* "marked, then sorted" is actually wanted.

## Percent-done

Percent is **derived from the existing free-text `progress` field**. No CLI
change, no schema change.

Rule, in order:
1. The first explicit percentage (`58%`, `58.5 %`) wins.
2. Otherwise the first `n/m` or `n of m` fraction, requiring `m > 0`.
3. Otherwise **indeterminate** — an animated sweep, never an invented number.

Percent-before-fraction is what stops `8/8 GPUs busy` from claiming a campaign
is finished when the same string says `(58% of 3000)`. Values clamp to 0–100.

Known limitation, accepted: a bare `8/8 GPUs busy` with no percentage reads as
100%. Writing an explicit percentage is the reliable form; the fraction is a
convenience. Documented, not heuristically patched.

## The meter

Every row — session or project — shows **resolved ÷ recorded**: actions answered
plus background work finished, over everything that session ever recorded.

A project row **sums its sessions rather than averaging their percentages**, so a
busy day cannot be outvoted by a quiet one:

```
phephree = (0 + 4) / (5 + 7) = 4/12 = 33%     not (0% + 57%)/2 = 29%
```

## Shared foundations

1. **Ids hand you the command.** Clicking any id copies `table-talk done <id>`.
   A delegated JS listener reading `data-id`; no server round-trip.
2. **Box-drawing, not indentation** for `why`/`rec`.
3. **One cursor, placed with meaning** — exactly one blinking block cursor on the
   page, at the end of the newest unanswered action.
4. **Change bars beat flashes.** A row that changed while the tab was hidden
   keeps a coloured left gutter until the tab is actually visible again
   (Page Visibility API), instead of a flash nobody sees on a second monitor.
5. **`watch(1)` honesty** — the literal cadence header above.
6. **Reverse-video focus** — selection swaps foreground and background.
7. **A tape, not a screenshot** — a `vhs` tape for the README. Separate task.

## Architecture

Pure logic moves to a new stdlib-only sibling module so it is testable without
starting a server; rendering stays in the PEP 723 script.

```
bin/tt_model.py         NEW — stdlib only. fold, percent, grouping, roll-up,
                        sort, filter, packing. Own --selftest.
bin/table-talk-dash.py  MODIFIED — PEP 723 + NiceGUI. Imports tt_model.
                        Theme CSS, renderer, drawer, wall, statusline, keyboard.
bin/table-talk          UNTOUCHED.
test.sh                 MODIFIED — runs the third selftest.
```

`uv run --script` puts the script's directory on `sys.path`, so the sibling
import resolves. `tt_model.py` imports nothing outside the stdlib, so it can be
selftested with plain `python3`.

### Verified NiceGUI 3.16 facts the implementation relies on

Confirmed against a running server, not assumed:

- `.props('data-id=x')` renders as a real HTML attribute on any element.
- `ui.label` escapes its text — user content is safe through it.
- `Element.move(target_container, target_index)` relocates an element **without
  rebuilding it**, which is the packer's primitive: element identity survives, so
  no flicker and no lost scroll.
- `ui.keyboard(on_key=…)` defaults to `ignore=['input','select','button','textarea']`,
  so keys do not fire while the filter box has focus. Verified both directions.
- `app.storage.general` needs no `storage_secret` and persists to
  `.nicegui/storage-general.json`. Loopback, one user — server-wide storage is
  correct here and keeps two tabs in agreement.

### State

All in `app.storage.general`: `theme`, `sort`, `cols`, `drawer_open`,
`marks` (list of window keys), `folds`, `groups_folded`, `scope`.

## Error handling

Unchanged where it exists: a malformed JSONL line is skipped, a missing file
folds to empty, an empty data dir shows a friendly empty page. New: a failed poll
freezes the statusline spinner and turns the cadence red rather than throwing.

## Testing

`test.sh` runs three assert-based selftests, no framework:

```
bin/table-talk --selftest                        (unchanged)
python3 bin/tt_model.py --selftest               (new — all pure logic)
uv run --script bin/table-talk-dash.py --selftest (render helpers + CSS tokens)
```

Every pure function gets its assertions in `tt_model.selftest()`. The dashboard
selftest keeps its existing discipline: import and exercise helpers, never start
a server.

## Out of scope

Cross-machine sync, auth, editing state from the browser, drag-and-drop,
SSE/websocket push (the 2 s timer stands), any CLI or schema change.
