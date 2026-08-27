---
name: table-talk
description: Use in EVERY conversation, at the start, before any other work — this is the user's standing reply protocol, not a per-task skill. Record decisions, background work, and jargon via the CLI, and close every reply with ID-keyed tables.
---

# table-talk

State: `~/.local/share/table-talk/*.jsonl` (one file per date+project).
Live view: `table-talk serve` → http://127.0.0.1:8731

## The action rule

**The first sentence of an action is the ask, and nothing else.** One sentence,
≤ 25 words, ending in a question mark or naming a choice between options.
Detail may follow after an em dash in the same cell: ≤ 2 sentences **and ≤ 50
words**. `--why` and `--rec` are **≤ 40 words each**. Sentences, commas and
semicolons are free units — the word counts are what bind, and anything past
them goes in the reply body, where prose belongs. The ask is the thing only the
user can settle — not what you did, not how you got there.

### Before / after — this project's own failure

**Before** — 80 words, recorded by this project. The question never appears:

> Implementation plan for the Multiplex dashboard is written, verified and up as
> PR #59 (closes #58). 14 TDD tasks: Tasks 1-6 build a stdlib-only
> bin/tt_model.py (fold, percent, summaries, grouping, packer, filter), Tasks
> 7-13 rebuild bin/table-talk-dash.py (tt.css, custom row renderer, wall,
> drawer, statusline, keyboard, change gutters), Task 14 is README + vhs tape.
> Every code block in Tasks 1-6 was assembled and run green before commit. Two
> execution styles are available.

**After** — the ask, then the detail that survived the cut:

> Subagent-driven or inline execution for the 14-task Multiplex plan? — plan is
> up as PR #59 (closes #58); Tasks 8-12 are the risky ones.
> **Why:** reviewing 14 finished commits costs far more than reviewing the plan
> now, and the choice decides how much you see between tasks.
> **Rec:** subagent-driven — a fresh agent and a review gate per task. Say "go"
> and I start at Task 1.

### Why and Rec attach to the ask

- **Why it matters** — the consequence of getting *this decision* wrong. Not
  background, not a recap of the situation.
- **Recommendation** — name one option and commit to it. "Two styles are
  available" is not a recommendation.

### Red flags — you are about to file a status report

| What you are telling yourself | Reality |
|---|---|
| "The context is genuinely complicated" | The decision is still one sentence. Complicated context goes after the em dash. |
| "They need to know how I got here" | They need to know what to decide. How you got there is the reply body. |
| "The recommendation explains it anyway" | The reader reads left to right. A column three cells away is not the ask. |
| "Trimming loses information" | The cell is not the archive — the reply body, the log and the PR all still exist. |

### Self-check before recording

Read your first sentence on its own. If it does not end in a question mark or
name a choice between options, rewrite it before you run the command. Then count
words: ask ≤ 25, detail after the dash ≤ 50 (and ≤ 2 sentences), `--why` ≤ 40,
`--rec` ≤ 40. An ask that is a request rather than a choice is still a question:
"Can you upload docs/assets/hero.png as the repo's social preview?"

## Record as you go

| Moment | Command |
|---|---|
| Decision only the user can make | `table-talk action "<the ask>" --why "<why it matters>" --rec "<your recommendation>"` → prints ID |
| Background work starts | `table-talk task "<what>"` → prints ID |
| Background work advances | `table-talk progress <id> "<update>" --pct <0-100>` |
| Item needs a plain line or sketch later | `table-talk progress <id> --intuitive "<line>" --diagram "<ascii>"` (no note needed) |
| Item answered/finished | `table-talk done <id>` |
| Jargon first used | `table-talk term "<term>" --intuitive "<plain one-liner>" --technical "<precise definition>"` |
| Sketch that explains one item | add `--diagram "<ascii>"` to the `action`/`task`/`progress` command |
| Concept a picture explains faster | `table-talk diagram "<mermaid source>" --title "<short name>"` → prints ID |

Project defaults to basename of cwd; override with `--project`. `--help` has the rest.
Record BEFORE writing the reply, so the dashboard and the reply never disagree.

## Progress that means something

Pass `--pct` whenever you know the number. Without it the bar is scraped out of
your prose, and a scrape reads any percentage it finds: a real job reported
"92% of 5039 genes above zero" — a RESULT — and drew a 92%-complete bar on work
that had barely started. `--pct` always wins over the text, so the sentence
stays prose and the bar stays honest.

The bar is only ever as fresh as your last `progress` call, and the card stamps
it with the wall-clock time it was recorded so a frozen reading cannot pass for
a live one. Nothing polls on your behalf: for work that runs longer than a
reply, re-record on a cadence — a loop or a wrapper around the job that calls
`table-talk progress <id> "..." --pct N` as it goes — or say plainly in the
task text that the number is a checkpoint, not a live feed.

## Sketches and diagrams

An action or task takes an optional ASCII sketch:
`table-talk action "..." --why ... --rec ... --diagram $'┌ logs ┐\n│ fold │\n└ wall ┘'`
— drawn centered under the item on the dashboard in the theme's colours, and
`table-talk progress <id> "..." --diagram "..."` adds or replaces one on an
existing item. Keep a sketch at most 40 columns wide and ~12 lines: it must
fit the narrowest card. Box-drawing and arrows read best (┌─┐ │ ▼ ▲). Lay it
out PORTRAIT — steps stacked top-to-bottom with ▼ between them, never a wide
left-to-right chain: cards are narrow, and landscape either wraps or shrinks.
ASCII renders in the terminal too, so the same sketch can appear in the reply
body.

Both also take `--intuitive "<one plain-English sentence>"` — what this is
and what is needed, for a reader with no context, ≤ 25 words. It renders as
the first sub-row (`int`) above why/rec. Record it whenever the ask or the
job is jargon-heavy; skip it when the headline is already plain.

For a picture too big for a card, record a standalone mermaid diagram —
`table-talk diagram "<mermaid source>" --title "<short name>"` — and point
the user at the dashboard (http://127.0.0.1:8731), which renders it live;
the terminal cannot. Re-recording the same title replaces that diagram.
Portrait here too: `flowchart TD`, never `LR` — the dashboard renders it in
the app's own font and colours inside a narrow card, and a landscape chain
scales down until its labels are unreadable.

## End every reply with these tables (omit one only when truly empty)

**🔴 Actions needed from you**
| ID | What I need | Why it matters | Recommendation |

Every action still open **in this project**, not just the ones recorded this
reply. An action stays in this table until it is `done`, however many replies
that takes — the dashboard keeps showing it, and a table that quietly drops it
is the one place the two disagree. Another project's actions belong to its own
card, never to this reply. Check before writing the tables rather than trusting
memory:

    table-talk show "$(basename "$PWD")"

**🔵 Background work**
| ID | What | Progress |

**📖 Terms in this reply**
| Term | Intuitive | Technical |

List only terms new or load-bearing this reply — the dashboard holds the
cumulative glossary. Intuitive = a plain-English sentence a newcomer follows;
technical = the precise definition, jargon spelled out.

## Responding to the user

- They reference rows by ID ("a3f9: go with 2"). Act on that item, then `table-talk done a3f9`.
- Record whole URLs, never a bare `#117`: the dashboard makes an http(s) URL a
  button and cannot guess which repository a `#ref` belongs to.
- IDs are 4 hex chars, printed by the CLI — never invent one; always use the printed value.
- At session start, if the dashboard is down (check below), mention `table-talk serve` once — never run it yourself.

## The server — never run it, and end the turn

- `table-talk serve` never exits (it becomes the dashboard process). Never run
  it from a session: a foreground call hangs until the tool timeout, a
  background task never completes. The CLI refuses under `CLAUDECODE`; do not
  work around it with `--force` or by launching the dash script directly — if
  the dashboard is down, say so once in the reply body and move on.
- Liveness: `curl -sf -o /dev/null --max-time 2 http://127.0.0.1:8731/ && echo up || echo down`.
  Never `pgrep -f "table-talk serve"` — serve execs into table-talk-dash.py,
  so that pattern matches nothing even while the dashboard runs.
- Recording commands (`action`, `task`, `progress`, `done`, `term`, `diagram`)
  all exit immediately. After printing the closing tables, END THE TURN: never
  sleep, loop, or poll `table-talk show` or the log files waiting for an
  answer — the answer arrives as the user's next message, referencing the ID.

## The dashboard

A tmux-style wall: one window per session file, carrying tmux flags — `!` bell
(a new action), `#` activity, `M` marked, `Z` zoomed, `*` current; the left
drawer groups sessions under their project.

Keys, most of them mirrored as statusline chips (`?` lists them): `\` drawer,
`m` mark, `z` zoom, `f` fold, `s` sort (recent → actions → project), `/` filter,
`!` needs-me (drop windows with nothing open), `Esc` unzoom.

Filtering **dims** non-matching rows rather than hiding them, and highlights the
match. Clicking an id copies `table-talk done <id>`, real paths open in the
configured command, and rows that moved since you last interacted keep a gutter.

Config: `~/.config/table-talk/config.toml`, overridable with `TABLE_TALK_CONFIG`
— port, poll interval, columns, drawer state, `links.open_command`, theme mode
and colour tokens. Missing or malformed file → defaults, never a crash.
