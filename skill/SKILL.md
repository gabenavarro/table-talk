---
name: table-talk
description: Use in EVERY conversation, at the start, before any other work — this is Gabriel's standing reply protocol, not a per-task skill. Record decisions, background work, and jargon via the CLI, and close every reply with ID-keyed tables.
---

# table-talk

State: `~/.local/share/table-talk/*.jsonl` (one file per date+project).
Live view: `table-talk serve` → http://127.0.0.1:8731

## Record as you go

| Moment | Command |
|---|---|
| Decision only Gabriel can make | `table-talk action "<background>" --why "<why it matters>" --rec "<your recommendation>"` → prints ID |
| Background work starts | `table-talk task "<what>"` → prints ID |
| Background work advances | `table-talk progress <id> "<update>"` |
| Item answered/finished | `table-talk done <id>` |
| Jargon first used | `table-talk term "<term>" --intuitive "<plain one-liner>" --technical "<precise definition>"` |

Project defaults to basename of cwd; override with `--project`.
Record BEFORE writing the reply, so the dashboard and the reply never disagree.

## End every reply with these tables (omit one only when truly empty)

**🔴 Actions needed from you**
| ID | Background | Why it matters | Recommendation |

**🔵 Background work**
| ID | What | Progress |

**📖 Terms in this reply**
| Term | Intuitive | Technical |

List only terms new or load-bearing this reply — dashboard holds the
cumulative glossary. Intuitive = a plain-English sentence a newcomer follows;
technical = the precise definition, jargon spelled out.

## Responding to Gabriel

- He references rows by ID ("a3f9: go with 2"). Act on that item, then `table-talk done a3f9`.
- IDs are 4 hex chars, printed by the CLI — never invent one; always use the printed value.
- At session start, mention `table-talk serve` once if the dashboard might not be running.
