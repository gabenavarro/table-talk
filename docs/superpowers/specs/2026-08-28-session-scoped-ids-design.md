# Session-scoped IDs and automatic hand-off — design

**Status:** approved 2026-08-28 (decisions 4220, 6b72, f88a, dcc5)
**Scope:** sub-projects C (session-scoped ID copy contract) and D (cross-session
reassignment). Sub-projects A (config-driven defaults + settings GUI) and B
(theme library, ported from https://ghostty-style.vercel.app/) are deliberately
NOT in this spec and get their own.

## The two problems

**C. An id does not say whose it is.** Clicking an id copies four hex
characters. One log file is one date+project and several agent sessions share
it, so `4c1a` alone cannot tell anyone which session recorded it. The merged
view (`u`) already shows the session code beside the id — that pairing is what
makes the merged view readable, and the clipboard throws half of it away.

**D. A session can act on another session's item silently.** `update()` (the
`progress` and `done` commands) finds an item by id anywhere in the data dir and
appends to it. Nothing checks who owns it. So session B advances session A's job
and the wall still says the job is A's, while B's reply talks about work that,
on the record, is not its own.

PR #149 (issue #133) addressed the reporting half of this — `show --mine` stops
a session *listing* another session's asks. This spec addresses the writing
half, and takes the opposite policy deliberately: rather than refusing to touch
another session's item, the touching session **takes ownership and says so**.
The item should belong to the conversation actually working on it.

## Decisions and their rationale

| # | Decision | Why |
|---|---|---|
| 4220 | A transfer APPENDS an event; it never rewrites `sid` in place | The log is append-only everywhere else, and that is what makes concurrent sessions safe. A rewrite invites the lost-update race `mint_lock` exists to prevent, and destroys the record of where an item went. |
| 6b72 | A transferred-out row counts in NEITHER `resolved` nor `recorded` | `roll_up` sums sessions. Counting it resolved in the old session while it is open in the new makes one logical item appear twice, inflating both halves of the project meter. |
| f88a | The transfer fires automatically inside `update()` | A skill instruction can be ignored, and the case that matters is precisely a session that has forgotten whose item it is. `update()` already locates the item and both session codes are already on hand. |
| dcc5 | Design approved as described | — |

Assumed and unchallenged: terms and diagrams never transfer (`update()` already
refuses them — they are reference material, not obligations); an item carrying
no `sid` is adopted silently, since there is no session to take it from.

## Data model

One new status value on actions and tasks: **`moved`**.

```json
{"id": "4c1a", "status": "moved", "moved_to": "9f3c", "ts": 1787900000}
```

`summarize()` must treat `moved` as neither open nor resolved, and must exclude
it from `recorded` entirely — the same treatment a glossary term already gets.
It is NOT a variant of `done`: an item that was handed off was not finished.

This is the project's first new status. It is load-bearing in three places at
once — `summarize()`, the row renderers, and the skill — and every one of them
must learn it in the same change or the tally, the card and the reply disagree.

## The transfer

Inside `update(id_, fields)`, under the existing `mint_lock`:

1. Locate the item's file (`find_file_with_id`) and fold it. Read `owner = ev.get("sid")`.
2. `me = session_code()`. If `me` is empty, or `owner` is empty, or `owner == me`
   — no transfer; apply the update exactly as today.
3. Otherwise, transfer:
   - **Same file** (the item's file IS today's file for that project): append one
     event `{id, sid: me, ts}`. No tombstone — the window is the same window, so
     there is nothing to leave behind. The sid tag in the merged view changes.
   - **Different file** (the item came from an earlier day): append the item's
     current fields to **today's** file for that project under the same id with
     `sid: me`, then append `{id, status: "moved", moved_to: me, ts}` to the old
     file.
4. Apply the caller's `fields` to the file that now owns the item.
5. Print the notice (below) to stderr.

Steps 3–4 are one sequence inside one lock, so a concurrent session cannot
observe an item owned by both or neither.

**Why a duplicate id across files is safe:** ids are minted unique across the
whole data dir, `find_file_with_id` returns the newest file containing the id,
and `merge_projects` resolves same-id events by timestamp (fixed in #156).
Per-file fold gives the old window the tombstone and today's window the live
item, which is exactly what should be seen from either end.

## The notice

```
moved 4c1a from session 7e2b to 9f3c
```

To stderr, on every transfer. It cannot be silent: the whole premise is that the
session did not know it was touching someone else's item. The skill additionally
requires the session to state the transfer in its reply.

## The copy contract

Clicking an id copies:

```
SESSION: 7e2b - ID: 4c1a
```

Falling back to the bare id when the item carries no `sid` (recorded before
session stamping, or by a human terminal). The existing hex guard stays: only a
minted id may reach the clipboard, so a stray element carrying a `data-id`
cannot copy arbitrary text.

The skill learns to read this back, so pasting it identifies both the item and
its owner.

## Skill changes

- How to read `SESSION: … - ID: …` when the user pastes one.
- That acting on another session's item transfers it, and the transfer MUST be
  reported in the reply — the CLI's stderr notice is not something the user
  necessarily sees.
- That a `moved` row on a card is not a failure and not a completion; it is a
  pointer to where the item went.

## Non-goals

- Reassigning terms or diagrams.
- A manual `claim` command. Automatic is the decision (f88a); a manual override
  can be added later if declining a transfer turns out to matter.
- Any change to `show --mine` / `--open` semantics from #133 — the reporting
  rule and the writing rule are independent and both stand.
- Sub-projects A and B.

## Risks

**`update()` will write to two files in one lock.** Every existing write touches
exactly one. A partial failure — an exception between the two appends — leaves
an item owned by nobody or by both. The implementation must order the appends so
that the failure mode is the harmless one: write the new owner's copy FIRST, so
an interruption leaves the item owned by both (the newest `ts` wins, and the old
row is merely stale) rather than by neither.

**Three layers must learn `moved` together.** A renderer that does not know it
draws a row that looks open; a `summarize` that does not know it counts a
hand-off as an outstanding obligation forever.

## Testing requirements

- `summarize()`: a `moved` action and a `moved` task are absent from
  `open_actions`, `open_tasks`, `resolved` and `recorded`; `pct` is unaffected.
- The transfer logic lives in a helper the CLI selftest can call directly — not
  inside `main()`'s argparse dispatch, which no selftest can reach (the lesson
  from `progress_fields`).
- Same-file transfer: one append, sid changes, no tombstone, no duplicate id.
- Cross-file transfer: today's file holds the live item under the caller's sid;
  the old file holds the tombstone; the id appears in both; `merge_projects`
  yields exactly one row, the live one.
- No transfer when the caller has no session id, when the item has none, or when
  they match.
- A term and a diagram are still refused by `update()`.
- The copy format, and its bare-id fallback, pinned in the dashboard selftest
  and mutation-tested.
- Every pin's needle must not match its own assertion, and must cover the region
  it guards (both traps hit real pins in this repo on 2026-08-27).
