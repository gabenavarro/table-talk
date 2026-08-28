# Session-scoped IDs and automatic hand-off — design

**Status:** approved 2026-08-28 (decisions 4220, 6b72, f88a, dcc5, ede1)
**Scope:** sub-projects C (session-scoped ID copy contract) and D (cross-session
reassignment). Sub-projects A (config-driven defaults + settings GUI, including
making the merged view the default) and B (theme library, ported from
https://ghostty-style.vercel.app/) are NOT in this spec and get their own.

> **Revised after research (2026-08-28).** The first version of this spec moved
> the item between files and introduced a `moved` status. Investigation of that
> design's own risk section found the move was both unnecessary and wrong on its
> happy path. What follows is the approved replacement (ede1). The rejected
> design and why it lost are recorded at the end, because the reasoning is the
> valuable part.

## The two problems

**C. An id does not say whose it is.** Clicking an id copies four hex
characters. One log file is one date+project and several agent sessions share
it, so `4c1a` alone cannot say which session recorded it. The merged view
already shows the session code beside the id — that pairing is what makes it
readable — and the clipboard throws half of it away.

**D. A session can act on another session's item silently.** `update()` (the
`progress` and `done` commands) finds an item by id anywhere in the data dir and
appends to it. Nothing checks who owns it. Session B advances session A's job,
and the record still says it is A's while B's reply talks about it as its own.

PR #149 (issue #133) addressed the reporting half — `show --mine` stops a
session *listing* another session's asks. This spec addresses the writing half,
and takes the opposite policy deliberately: rather than refusing to touch
another session's item, the touching session **takes ownership and says so**.
The item should belong to the conversation actually working on it.

## The mechanism

Ownership is already one field on one event: `sid`. A transfer is therefore one
more key on the append `update()` already performs.

```python
    ev = fold(f).get(id_, {})
    typ = ev.get("type")
    if typ not in ("action", "task"):
        sys.exit(...)                      # unchanged: terms and diagrams never transfer
    owner, me = ev.get("sid"), session_code()
    if owner and me and owner != me:
        fields = {**fields, "sid": me}
        print(f"moved {id_} from session {owner} to {me}", file=sys.stderr)
    append(f, {"id": id_, "ts": int(time.time()), **fields})
```

That is the whole transfer. **The item does not change file.** There is no
second write, no tombstone, no new status, and nothing whose ordering matters.

**Why one file is sufficient — measured, not argued.** A session's reply tables
come from `table-talk show <project> --open --mine`, and that command globs
`*-<project>.jsonl` across every date and filters on `sid`. So session B sees
the item it just took over regardless of which day's file holds it. Verified
with the real CLI: after B transfers, `--mine` as B lists the item and `--mine`
as A does not.

**Counted once, everywhere.** The item exists in exactly one file, so
`summarize()` counts it once and `roll_up` cannot double it. Verified:
`open_actions=1, recorded=1` after a transfer.

**The merged view is already right.** `merge_projects` tags each row with
`ev.get("sid")`, so the row's session code follows ownership with no change at
all. Verified: `_from=9f3c` after the transfer.

## Decisions and their rationale

| # | Decision | Why |
|---|---|---|
| 4220 | A transfer APPENDS; it never rewrites in place | The log is append-only everywhere else, and that is what makes concurrent sessions safe. Still holds: the transfer is an appended event carrying the new `sid`. |
| 6b72 | A transferred item must be counted once, not once per session | `roll_up` sums sessions. Now moot by construction — the item lives in one file, so there is nothing to double-count and no tombstone to exclude. |
| f88a | The transfer fires automatically inside `update()` | A skill instruction can be ignored, and the case that matters is a session that has forgotten whose item it is. `update()` already folds the item to check its type, so the owner is free to read. |
| ede1 | Re-tag in place; accept that the item stays on its original day's card | It deletes the two-file risk rather than managing it. The residual objection is answered by sub-project A making the merged view the default. |

Unchallenged assumptions: terms and diagrams never transfer (`update()` already
refuses them — reference material, not obligations); an item carrying no `sid`
is adopted silently, since there is no session to take it from.

## The notice

```
moved 4c1a from session 7e2b to 9f3c
```

To stderr, on every transfer. It cannot be silent: the premise is that the
session did not know it was touching someone else's item. The skill
additionally requires the session to state the transfer in its reply, because
stderr is not something the user necessarily sees.

## The copy contract

Clicking an id copies:

```
SESSION: 7e2b - ID: 4c1a
```

falling back to the bare id when the item carries no `sid` (recorded before
session stamping, or by a human terminal). The existing hex guard stays: only a
minted id may reach the clipboard, so a stray element carrying a `data-id`
cannot copy arbitrary text.

The flat wall must also show ownership, or a re-tag is invisible there:
`_id_button` currently renders only `_from`, which only `merge_projects` sets.
It becomes `ev.get("_from") or ev.get("sid")`. `.sid` in the stylesheet is not
scoped to the merged wall, so it already styles correctly.

## Skill changes

- How to read `SESSION: … - ID: …` when the user pastes one.
- That acting on another session's item transfers it, and the transfer MUST be
  reported in the reply.
- That `--mine` is how a session finds what it owns, including items it has
  taken over that live in an older file.

## Known consequence, accepted

A window is titled by the session that most recently wrote to it
(`session_label`). After a transfer, the ORIGINAL day's window title changes
from `proj:7e2b` to `proj:9f3c` — verified. That is literally true and the
function already behaves this way whenever two sessions share today's file, but
it reads as though B were working inside A's session.

Accepted because sub-project A makes the merged view the default, and the merged
view has no per-day windows: one card per project, every row tagged with its own
session. The objection is then structurally absent rather than tolerated. **This
spec therefore depends on A landing;** if A is abandoned, revisit this.

## Non-goals

- Moving an item between files. This is the rejected design.
- A `moved` status. Nothing needs one.
- A manual `claim` command. Automatic is the decision (f88a).
- Any change to `show --mine` / `--open` semantics from #133 — the reporting
  rule and the writing rule are independent and both stand.
- Sub-projects A and B.

## Testing requirements

- The transfer logic must be reachable from the CLI selftest — not buried in
  `main()`'s argparse dispatch, which no selftest can reach (the lesson from
  `progress_fields`).
- No transfer when the caller has no session id, when the item has none, or when
  they match; a transfer when they differ.
- After a transfer: the item's file is the only file changed; `--mine` lists it
  for the new owner and not the old; `summarize` counts it once;
  `merge_projects` tags it with the new session.
- A term and a diagram are still refused by `update()`.
- The copy format and its bare-id fallback, pinned in the dashboard selftest and
  mutation-tested.
- Every pin's needle must not match its own assertion, and must cover the region
  it guards (both traps hit real pins in this repo on 2026-08-27).

## Appendix: the rejected design, and why

The first version moved the item: append its fields to today's file under the
same id with the new `sid`, then append `{"status": "moved", "moved_to": …}` to
the old file, and teach `summarize()` to exclude `moved` from every count.

It was rejected for three reasons, in increasing order of severity:

1. **Two writes for one logical operation.** Nothing else in this codebase
   writes two files. A crash between them leaves an item owned by both or by
   neither — and `flock` does not help, because the lock is released when a
   process is killed. (`update()` does not even take the lock today; the first
   version of this spec wrongly claimed it did.)
2. **It was wrong before crash-safety even entered.** The copy and the tombstone
   would both be candidates in `merge_projects`' recency tie-break, so the
   merged view could show the tombstone as the winner. Making it work required
   deliberately backdating the tombstone — a timestamp that lies.
3. **It bought nothing the one-append version does not already deliver.** The
   goal was that the new owner sees the item; `show --open --mine` already
   spans files and filters on `sid`, which is where a session's reply tables
   come from.

The general lesson is worth keeping: the safest way to make a risky operation
survivable is usually to find the version of the feature that does not need it.
