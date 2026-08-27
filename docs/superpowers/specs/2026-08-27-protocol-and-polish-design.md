# Protocol tightening, links, config, and UI polish — Design Spec (2026-08-27)

Follows the Multiplex dashboard rewrite (#63, merged). Six changes in one round.

Approved by the user 2026-08-27 in four answers, recorded inline below.

## The problem that matters most

table-talk exists so a Claude session and its user can talk **concisely**. The
dashboard now looks the part, but the content it carries has drifted the other
way. A real action recorded by this project, at full length:

> Implementation plan for the Multiplex dashboard is written, verified and up as
> PR #59 (closes #58). 14 TDD tasks: Tasks 1-6 build a stdlib-only
> bin/tt_model.py (fold, percent, summaries, grouping, packer, filter), Tasks
> 7-13 rebuild bin/table-talk-dash.py (tt.css, custom row renderer, wall,
> drawer, statusline, keyboard, change gutters), Task 14 is README + vhs tape.
> Every code block in Tasks 1-6 was assembled and run green before commit. Two
> execution styles are available.

Eighty words, and the actual question — *which execution style?* — never appears.
It is inferable only from the Recommendation column. Three failures:

1. **No bottom line up front.** The reader must finish the paragraph to learn
   what is being asked, and here even that is not enough.
2. **Background is a status report.** It narrates what was done, not what is
   needed. The field name invited it.
3. **Why and Recommendation read as separate documents.** Because Background
   never states an ask, the other two columns have nothing to attach to.

### Decision: fix this in the skill, not the CLI

**User's answer: "Skill guidance only".** The CLI and JSONL format stay untouched
— consistent with the invariant this project has held throughout, and it keeps
every existing log readable.

That places the entire burden on `skill/SKILL.md`, so the guidance must be
*forcing* rather than advisory. Prose that says "be concise" has already failed;
this session generated the example above while the skill said exactly that. What
goes in instead:

- **A hard shape for the `Background` cell**, renamed in the reply tables to
  **what I need** — one sentence, phrased as a question or a choice, ≤ 25 words.
  The field name is half the fix: "Background" invites background.
- **A worked before/after pair** using the real failure above. Skills in this
  ecosystem work best when they show the failure, not just the rule.
- **A red-flag table** in the house style, listing the rationalisations that
  produce a bad action ("the context is genuinely complicated", "they'll want to
  know how I got here") next to what to do instead.
- **A self-check with a concrete test**: *if the first sentence does not end in a
  question mark or contain a choice between named options, rewrite it.* Detail
  that survives the cut goes at the end of the same cell after a `—`, or into the
  reply body where prose belongs.
- **Why and Rec must attach to the ask**, not to the situation: Why states the
  consequence of getting *this decision* wrong; Rec names one option and commits.

The CLI's `--why`/`--rec` flags already have the right names. Only the first
positional argument's *usage* changes, which is a documentation change.

## Feature 1 — Rename Gabriel to User

`skill/SKILL.md` is written for one named person. Rename throughout so the skill
is adoptable by anyone. This is a precondition for the repo's stated goal.

## Feature 2 — Bring the skill up to date

The skill documents a dashboard that no longer exists. It must describe the
current one: the drawer and its project grouping, marks/zoom/fold/scope, the
keyboard layer, dim-not-hide filtering, change gutters, and the config file.
Keep it short — the skill is a protocol, not a manual.

## Feature 3 & 4 — Links to documents, memory, and CLAUDE.md

**User's answer: "Open in your editor".** The dashboard runs on loopback as the
user, so a click runs a configured command server-side.

**No CLI change is needed, and none is made.** Paths are already present in the
free text of `background`/`why`/`rec`/`what`/`progress` — this project's own logs
are full of them. The dashboard therefore:

- **Detects path-shaped substrings** when rendering a cell and turns them into
  clickable affordances. Detection is deliberately conservative: a token
  containing `/` or ending in a known extension, that **resolves to a file that
  exists**. Existence is the filter that keeps prose from lighting up.
- **Runs the configured command** on click (`open_command` in the config,
  default `xdg-open`). The path is passed as an argv element, never through a
  shell, and must resolve inside an allowed root.
- **Links memory and CLAUDE.md from fixed locations** — the drawer footer gets
  entries for `CLAUDE.md` (walking up from the project dir) and the session
  memory directory, when they exist.

### Security constraints, non-negotiable

This feature takes a string from a log file and hands it to a process launcher.
The threat model is the same one that produced two Critical findings in #63: log
content is agent-supplied and may summarise untrusted material.

- **Never `shell=True`.** `subprocess.run([cmd, path])` with a list argv.
- **Resolve and confine.** `Path(p).resolve()` must be under one of the allowed
  roots (the data dir, the cwd project dir, the home config dir). Anything else
  is not rendered as a link at all.
- **No symlink escape**: resolve first, check second.
- **The rendered link is a `ui.label` inside a button**, never markup, and the
  path reaches props via the dict form — the sinks #63 closed stay closed.
- **Selftest pins the confinement** with hostile inputs: `../../../etc/passwd`,
  an absolute path outside the roots, a symlink pointing outside, a path with a
  newline, and a path with a shell metacharacter.

## Feature 5 — Config file

**User's answer: "Everything".** Format is **TOML**, read with stdlib `tomllib`
(Python 3.11+). YAML would need PyYAML — a new dependency, which this project
does not take.

Location: `~/.config/table-talk/config.toml`, overridable with
`TABLE_TALK_CONFIG`. Absent or unreadable file → built-in defaults, never a crash.
A malformed file → defaults plus one clear stderr warning naming the line.

```toml
[server]
port = 8731
poll_seconds = 2.0

[ui]
columns = 0          # 0 = auto
drawer_open = true
collapsed_sections = ["glossary", "done"]
filter_debounce_ms = 100

[links]
open_command = "xdg-open"    # or "code", "$EDITOR"
extra_roots = []

[theme]
default = "system"           # system | light | dark

[theme.dark]                 # any subset; unlisted keys keep their defaults
bg = "#1d2021"
surface = "#282828"
ink = "#ebdbb2"
act = "#fb4934"
# ... every token from the stylesheet's :root block

[theme.light]
bg = "#e8e6dc"
# ...
```

The stylesheet keeps its current values as the defaults; config values are
emitted as a `:root` override block after `tt.css` loads, so an unset key simply
inherits. Colour values are **validated against `^#[0-9a-fA-F]{3,8}$`** before
emission — a config file is a second route into the stylesheet and gets the same
treatment as any other untrusted input.

## UI fixes from the user's review of #63

1. **Hover in dark mode lightens the row**, reducing contrast against light text.
   Invert it: hover should *darken* in dark mode and lighten in light mode.
   Measure text contrast against the hover background, not the resting one.
2. **Text overflows horizontally at narrow widths.** Every cell must wrap at the
   card edge. No horizontal overflow at any viewport width.
3. **Multiple columns persist when the window is too narrow.** Force a single
   column below a breakpoint regardless of the stored `cols` preference — the
   preference is a maximum, not a mandate.
4. **The `├─`/`└─` tree guide breaks when `why` wraps.** The guides are drawn as
   isolated glyphs per row, so a three-line `why` leaves a gap with no connecting
   `│`. Replace the glyph column with a continuous vertical rule that spans the
   full height of the sub-line block, keeping the corner glyph at the last row.

## Change gutters — correcting a design error

The spec for #63 said a change gutter persists "until the tab is genuinely
visible again", cleared via the Page Visibility API. **That does not do what it
was meant to do.** The API only reports tab backgrounding; a window that is
simply covered, or on a monitor you are not looking at, still reports `visible`.
For the dashboard's actual deployment — permanently visible on a second monitor —
`document.hidden` is never true and the gutter clears after ~2 polls.

**Corrected intent: the gutter clears on user *interaction*, not on visibility.**
Any click, keypress, or scroll on the page marks everything currently shown as
seen. Visibility remains a secondary signal (returning to a backgrounded tab
still counts), but interaction is the primary one, because it is the only thing
that actually proves a human looked.

## Out of scope

Editing state from the browser, cross-machine sync, a packaged binary, and the
follow-ups already filed as #64.
