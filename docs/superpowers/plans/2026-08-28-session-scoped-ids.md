# Session-scoped IDs and automatic hand-off — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Acting on another session's item transfers ownership to the acting session and says so, and clicking an id copies `SESSION: <sid> - ID: <id>`.

**Architecture:** Two changes, each its own issue + branch + PR. Ownership is one field (`sid`) on one event, so a transfer is one extra key on the append `update()` already performs — no second file, no new status. The dashboard already renders a session tag in the merged view; the flat wall and the clipboard learn the same field.

**Tech Stack:** Python stdlib (CLI, model), NiceGUI 3.16 via uv (dashboard).

**Spec:** `docs/superpowers/specs/2026-08-28-session-scoped-ids-design.md` — read it first, especially the Appendix, which records a rejected design and why. Do not reintroduce a file move or a `moved` status.

## Global Constraints

- `bin/table-talk` and `bin/tt_model.py` are stdlib-only and must run on Python **3.10** — CI enforces it (`cli-oldest-python`). Never import `tt_config` or `tomllib` at module level in `bin/table-talk`.
- Every behaviour change is pinned by an assertion in the touched file's own `selftest()`, with a message stating the **consequence** of the failure.
- Untrusted-input discipline is unchanged: exactly one `ui.html`, props assigned never string-interpolated, no `shell=`, and only a minted-looking value may reach the clipboard.
- `./test.sh` must print `all selftests passed` before any commit.
- **Two pin traps that hit real assertions in this repo on 2026-08-27, both mandatory to check:** a needle must not appear inside its own assertion (it will match itself and pass or fail spuriously), and `code`-based pins must actually cover the region they guard — in `bin/table-talk`, `main()` is defined *after* `selftest()`, so a pin built only from `src.split("def selftest():")[0]` does not see it. Mutation-test every new pin: break the thing, confirm the suite fails, restore.
- Never mask a test's exit status behind a pipe (`./test.sh | tail -1` makes the pipeline's status `tail`'s). Use `if ./test.sh > /tmp/out 2>&1; then …`.
- Git: `gh issue create` → `gh issue develop <n> --name <branch> --base main` → commits → `gh pr create` → STOP. Trailer `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`; PR bodies end with the Claude Code footer. Do not merge; merges are review-gated and need the maintainer's explicit bypass.

---

### Task 1: Acting on another session's item transfers it

**Files:**
- Modify: `bin/table-talk` (new `transfer()`, `update()` uses it, selftest)
- Modify: `skill/SKILL.md` (the reply must report a transfer)

**Interfaces:**
- Produces: `transfer(ev, me) -> (extra_fields, notice)`. Pure; Task 2 does not use it.

- [ ] **Step 1: Issue + branch**

```bash
cd /var/home/dna/GitHub/table-talk && git checkout main && git pull
gh issue create --title "Acting on another session's item leaves it owned by the other session" \
  --body "$(cat <<'EOF'
`update()` (the `progress` and `done` commands) finds an item by id anywhere in the data dir and appends to it without checking who owns it. So session B advances session A's job and the record still says it is A's, while B's reply discusses it as B's work. Several agent sessions share one project's log every day, so this is the normal case, not an edge one.

Take ownership automatically and say so: one extra key (`sid`) on the append `update()` already performs, plus a stderr notice. No second file and no new status — see the spec's Appendix for the design that was rejected and why.

Spec: docs/superpowers/specs/2026-08-28-session-scoped-ids-design.md
EOF
)"
gh issue develop <ISSUE> --name feat/transfer-on-touch --base main --checkout
```

- [ ] **Step 2: Write the failing test**

In `bin/table-talk`'s `selftest()`, immediately before `assert find_file_with_id("a1b2") == f`:

```python
    # Ownership is one field on one event, so a hand-off is one more key on the
    # append update() already makes. A session that forgot whose item it was
    # touching is exactly the case this must catch.
    assert transfer({"id": "4c1a", "sid": "7e2b"}, "9f3c") == (
        {"sid": "9f3c"}, "moved 4c1a from session 7e2b to 9f3c")
    assert transfer({"id": "4c1a", "sid": "7e2b"}, "7e2b") == ({}, None), \
        "the owner touching its own item is not a transfer"
    assert transfer({"id": "4c1a"}, "9f3c") == ({}, None), \
        "an item with no session is adopted silently: there is nobody to take it from"
    assert transfer({"id": "4c1a", "sid": "7e2b"}, "") == ({}, None), \
        "a human terminal has no session id and must not take anything over"
```

- [ ] **Step 3: Run it and watch it fail**

Run: `python3 bin/table-talk --selftest`
Expected: `NameError: name 'transfer' is not defined`

- [ ] **Step 4: Implement**

Above `def update(id_, fields):`:

```python
def transfer(ev, me):
    """Who an item belongs to after this write, and what to say about it.

    Returns (extra fields, notice-or-None). Ownership is one field on one event,
    so a hand-off is one more key on the append update() already performs: no
    second file, no new status, nothing whose ordering matters, and nothing for
    a crash to land between. The item does not change file - `show --mine`
    globs every dated file of the project and filters on sid, so the new owner
    sees it wherever it lives.

    An item with no sid is adopted silently: there is no session to take it
    from. A caller with no sid takes nothing: that is a human terminal.
    """
    owner = ev.get("sid")
    if not (owner and me and owner != me):
        return {}, None
    return {"sid": me}, f"moved {ev.get('id')} from session {owner} to {me}"
```

In `update()`, replace the body from the type check onward:

```python
    ev = fold(f).get(id_, {})
    typ = ev.get("type")
    if typ not in ("action", "task"):
        sys.exit(f"error: id '{id_}' is a {typ or 'unknown record'}, not an action/task")
    extra, notice = transfer(ev, session_code())
    if notice:
        # stderr, and the skill also requires the reply to say it: the premise
        # is that the session did not know whose item this was.
        print(notice, file=sys.stderr)
    append(f, {"id": id_, "ts": int(time.time()), **fields, **extra})
```

`**extra` comes after `**fields` so a transfer always wins.

- [ ] **Step 5: Run it and watch it pass**

Run: `python3 bin/table-talk --selftest` → `ok`

- [ ] **Step 6: End-to-end test**

Append to the same selftest block, after the pure-function asserts:

```python
        os.environ["CLAUDE_CODE_SESSION_ID"] = "7e2bxxxx"
        owned = add_event("shift", {"type": "action", "status": "open",
                                    "background": "b", "why": "w", "rec": "r"})
        os.environ["CLAUDE_CODE_SESSION_ID"] = "9f3cxxxx"
        update(owned, {"progress": "taken over"})
        assert fold(session_file("shift"))[owned]["sid"] == "9f3c", \
            "touching another session's item hands it over; nothing else does"
        assert fold(session_file("shift"))[owned]["background"] == "b", \
            "and the hand-off must not disturb any other field"
        os.environ["CLAUDE_CODE_SESSION_ID"] = "aaaa-x"
```

(`os.environ` is already manipulated with save/restore in this selftest; keep
that pattern — the ambient variable is set in every agent session.)

- [ ] **Step 7: Full suite, both Pythons**

```bash
if ./test.sh > /tmp/tt.log 2>&1; then tail -1 /tmp/tt.log; else tail -6 /tmp/tt.log; fi
uv run --python 3.10 --no-project python bin/table-talk --selftest
```

- [ ] **Step 8: Skill**

In `skill/SKILL.md`, in the "Responding to the user" section, after the id-reference bullet:

```markdown
- Acting on another session's item takes it over: `progress` and `done` re-tag
  it to you and the CLI prints `moved <id> from session <a> to <b>`. Say so in
  your reply — the user does not see that line, and an item changing hands
  silently is the thing this protocol exists to prevent.
```

- [ ] **Step 9: Commit and PR, then STOP**

```bash
git add bin/table-talk skill/SKILL.md
git commit -m "$(cat <<'EOF'
feat(cli): acting on another session's item hands it over

update() found an item by id anywhere in the data dir and appended to it
without checking who owned it, so one session advanced another's job while
the record still said it was theirs. Several sessions share one project's
log every day, so this was the normal case.

Ownership is one field on one event, so the hand-off is one more key on the
append update() already made: no second file, no new status, nothing for a
crash to land between. The item does not change file because `show --mine`
already spans every dated file and filters on sid.

Closes #<ISSUE>.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
)"
git push -u origin feat/transfer-on-touch
gh pr create --title "feat(cli): acting on another session's item hands it over" --body "…"
```

---

### Task 2: An id copies with its session

**Files:**
- Modify: `bin/table-talk-dash.py` (`_id_button`, `COPY_JS`, pins)
- Modify: `skill/SKILL.md`, `README.md`

**Interfaces:** consumes the `sid` field Task 1 keeps current. Independent of `transfer()`.

- [ ] **Step 1: Issue + branch**

```bash
git checkout main && git pull
gh issue create --title "A copied id does not say which session it belongs to" \
  --body "$(cat <<'EOF'
Clicking an id copies four hex characters. One log file is one date+project and several sessions share it, so `4c1a` alone cannot say who recorded it — and the merged view already shows the session code beside the id precisely because the pairing is what makes it readable. The clipboard throws half of it away.

Copy `SESSION: 7e2b - ID: 4c1a`, falling back to the bare id when there is no session to name. The flat wall must show the session too: `_id_button` renders only `_from`, which only the merged view sets, so a re-tagged item is invisible there.

Spec: docs/superpowers/specs/2026-08-28-session-scoped-ids-design.md
EOF
)"
gh issue develop <ISSUE> --name feat/copy-session-and-id --base main --checkout
```

- [ ] **Step 2: Write the failing pins**

In `bin/table-talk-dash.py` `selftest()`, beside the existing COPY_JS assertions:

```python
    assert 'SESSION: ${s} - ID: ${id}' in COPY_JS, \
        "an id alone cannot say whose it is: one log file is one date+project " \
        "and several sessions share it, which is why the merged view shows the " \
        "pair and why the clipboard must carry both"
    assert COPY_JS.index("[0-9a-f]{2,}") < COPY_JS.index("writeText"), \
        "the session reaches the clipboard too, so it is guarded before the " \
        "write like the id is - a stray data-session must not copy arbitrary text"
    assert 'ev.get("_from") or ev.get("sid")' in code, \
        "the FLAT wall must show ownership as well: _from is set only by the " \
        "merged view, so a re-tagged item was invisible on the day cards"
```

- [ ] **Step 3: Run and watch them fail**

Run: `uv run --script bin/table-talk-dash.py --selftest` → AssertionError on the first.

- [ ] **Step 4: Implement `_id_button`**

```python
def _id_button(ev, cls):
    """The id, and the session that owns it. A nested label rather than a bare
    button so we stay on public API; the delegated listener finds the button via
    closest().

    _from is set by the merged view; sid is the field itself, so the flat wall
    shows ownership too. Both props are ASSIGNED, never interpolated into a
    .props() string: see build_window.
    """
    from nicegui import ui
    sid = ev.get("_from") or ev.get("sid")
    btn = ui.element("button").classes(f"id {cls}")
    btn.props["data-id"] = str(ev["id"])
    if sid:
        btn.props["data-session"] = str(sid)
    with btn:
        ui.label(str(ev["id"]))
        if sid:
            ui.label(str(sid)).classes("sid")
```

- [ ] **Step 5: Implement `COPY_JS`**

Replace the guard and payload lines:

```js
  const id = b.dataset.id || '', s = b.dataset.session || '';
  if (!/^[0-9a-f]{4,}$/.test(id)) return;              // never copy arbitrary text
  const cmd = /^[0-9a-f]{2,}$/.test(s) ? `SESSION: ${s} - ID: ${id}` : id;
```

- [ ] **Step 6: Run, then mutation-test both pins**

```bash
if ./test.sh > /tmp/tt.log 2>&1; then tail -1 /tmp/tt.log; else tail -6 /tmp/tt.log; fi
```

Then, in a throwaway python snippet: replace the payload with the bare `id`, confirm the suite fails; restore. Replace `ev.get("_from") or ev.get("sid")` with `ev.get("_from")`, confirm the suite fails; restore.

- [ ] **Step 7: Docs**

`skill/SKILL.md`, in "Responding to the user":

```markdown
- A pasted `SESSION: 7e2b - ID: 4c1a` names both the item and the session that
  owns it. Act on the id; the session tells you whether it is yours, and
  touching it when it is not hands it over (see above).
```

`README.md`, in the Dashboard bullet, change "Click any id to copy it." to
"Click any id to copy `SESSION: <session> - ID: <id>` — or the bare id when the
item predates session stamping."

- [ ] **Step 8: Commit and PR, then STOP**

Message: `feat(ui): an id copies with the session that owns it`, closing the issue, with the trailer.

---

## Self-review notes

- Spec coverage: the mechanism → Task 1; the copy contract and the flat-wall
  `sid` → Task 2; both skill changes are folded into the task whose deliverable
  needs them.
- Deliberately absent, per the spec's Appendix: any file move, any `moved`
  status, any `summarize()` change, any `merge_projects` change.
- Type consistency: `transfer(ev, me)` returns `(dict, str|None)` and `update()`
  destructures exactly that; `data-session` is the DOM name for the same value
  `_id_button` renders.
- Ordering: Task 2 does not depend on Task 1 — the copy contract works on any
  item already carrying `sid`. Either may land first.
