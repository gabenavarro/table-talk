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
At most two more sentences may follow, after an em dash in the same cell;
anything beyond that goes in the reply body, where prose belongs. The ask is the
thing only the user can settle — not what you did, not how you got there.

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
| "It's all relevant" | Relevant to you. The ask is what is relevant to them. |
| "The ask is obvious from the detail" | If it were, you could write it in one sentence. Write that sentence instead. |
| "I'll state the finding, then ask" | The finding is evidence, not the ask. Lead with the question. |
| "Trimming loses information" | The cell is not the archive — the reply body, the log and the PR all still exist. |

### Self-check before recording

Read your first sentence on its own. If it does not end in a question mark or
name a choice between options, rewrite it before you run the command. An ask
that is a request rather than a choice is still a question: "Can you upload
docs/assets/hero.png as the repo's social preview?"

## Record as you go

| Moment | Command |
|---|---|
| Decision only the user can make | `table-talk action "<the ask>" --why "<why it matters>" --rec "<your recommendation>"` → prints ID |
| Background work starts | `table-talk task "<what>"` → prints ID |
| Background work advances | `table-talk progress <id> "<update>"` |
| Item answered/finished | `table-talk done <id>` |
| Jargon first used | `table-talk term "<term>" --intuitive "<plain one-liner>" --technical "<precise definition>"` |

Project defaults to basename of cwd; override with `--project`. `--help` has the rest.
Record BEFORE writing the reply, so the dashboard and the reply never disagree.

## End every reply with these tables (omit one only when truly empty)

**🔴 Actions needed from you**
| ID | What I need | Why it matters | Recommendation |

**🔵 Background work**
| ID | What | Progress |

**📖 Terms in this reply**
| Term | Intuitive | Technical |

List only terms new or load-bearing this reply — the dashboard holds the
cumulative glossary. Intuitive = a plain-English sentence a newcomer follows;
technical = the precise definition, jargon spelled out.

## Responding to the user

- They reference rows by ID ("a3f9: go with 2"). Act on that item, then `table-talk done a3f9`.
- IDs are 4 hex chars, printed by the CLI — never invent one; always use the printed value.
- At session start, mention `table-talk serve` once if the dashboard might not be running.

## The dashboard

A tmux-style wall: one window per session file, carrying tmux flags — `!` bell
(a new action), `#` activity, `M` marked, `Z` zoomed, `*` current. The left
drawer groups sessions under their project with count badges and htop-style
meters; clicking a project scopes the wall to it, a session scrolls to its window.

Keys, mirrored as statusline chips (`?` lists them): `\` drawer, `m` mark,
`z` zoom, `f` fold, `s` sort (recent → actions → project), `/` filter,
`!` needs-me (drop windows with nothing open), `Esc` unzoom.

Filtering **dims** non-matching rows rather than hiding them, and highlights the
match. Rows that moved since you last interacted with the page keep a coloured
change gutter. Clicking an id copies `table-talk done <id>`; paths in the text
that resolve to a real file are clickable and open in the configured command.

Config: `~/.config/table-talk/config.toml`, overridable with `TABLE_TALK_CONFIG`
— port, poll interval, columns, drawer state, `links.open_command`, theme mode
and every colour token. Missing or malformed file → defaults, never a crash.
