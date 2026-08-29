# Stale-completion check Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Warn, on every recording command, about any task in this project that
carries an explicit `pct` of 100 while its status is still open.

**Architecture:** One pure function in `bin/table-talk` decides which ids are
stale; `main()` calls it after the command has appended its event and prints the
result to stderr. Nothing new is stored, nothing is auto-closed, and no existing
command changes its behaviour — a warning is the whole feature.

**Tech Stack:** Python 3.10 stdlib only. Behaviour is pinned by `assert`s inside
`selftest()` in the same file, run by `./test.sh`.

**Spec:** none — this is issue #190, small enough that the issue body is the
specification. Its three correctness requirements are reproduced in Global
Constraints below, which is what the tasks are checked against.

## Global Constraints

- `bin/table-talk` is stdlib-only and must import on **Python 3.10** (CI job
  `cli-oldest-python` is a required check). Never import `tt_config` or
  `tomllib` at module level.
- Staleness is decided by the **literal `pct` field**, never by
  `tt_model.progress_pct`, which scrapes prose and has already misread a result
  ("92% of 5039 genes above zero") as progress.
- The id written by the current invocation is **never** reported by that same
  invocation.
- A task **blocked on a still-open action** is not stale, however high its pct.
- Every new behaviour gets an `assert` in `selftest()` whose message states the
  **consequence** of the behaviour being wrong, not what the code does.
- Every pin must be mutation-tested: break the real line, confirm the suite goes
  red, restore. A pin's needle must not match its own assertion text, and must
  fall inside the region the pin searches (`code` = the file minus `selftest()`,
  plus everything from `def main(` onward).
- The warning goes to **stderr** and must never change a command's exit status:
  recording must not fail because a nudge could not be computed.

## File Structure

| File | Responsibility for this change |
|---|---|
| `bin/table-talk` | `stale_hundreds()` (pure, decides the ids) and one call site in `main()`. Both live here because the CLI already owns `fold`, `blocker` and `DATA_DIR`, and the dashboard has no part in this. |
| `skill/SKILL.md` | One line under the 🔵 table telling a session what the warning means and what to do about it. |
| `README.md` | Two sentences under the progress section, since `--pct 100` is documented there. |

`bin/tt_model.py` is deliberately untouched: the dashboard renders the wall for
a human who can see an unclosed job, and the scrape it owns is the thing this
check must not use.

---

### Task 1: `stale_hundreds()` — decide which ids are stale

**Files:**
- Modify: `bin/table-talk` (new function beside `blocker`; pins in `selftest()`)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `stale_hundreds(states, skip_id=None) -> list[str]` — sorted ids of
  tasks that are open, carry a literal `pct` of 100, are not blocked on a
  still-open action, and are not `skip_id`. `states` is a `dict[str, dict]` of
  folded events keyed by id, exactly what `fold()` returns.

- [ ] **Step 1: Write the failing pins**

Add to `selftest()` in `bin/table-talk`, immediately after the existing
`blocker` pins:

```python
    # stale_hundreds: a task that says it is finished but was never closed.
    open_act = {"type": "action", "status": "open"}
    st = {"aaaa": {"id": "aaaa", "type": "task", "status": "open", "pct": 100},
          "bbbb": {"id": "bbbb", "type": "task", "status": "open", "pct": 60},
          "cccc": {"id": "cccc", "type": "task", "status": "done", "pct": 100},
          "dddd": {"id": "dddd", "type": "action", "status": "open", "pct": 100},
          "eeee": open_act}
    assert stale_hundreds(st) == ["aaaa"], \
        "only an OPEN TASK at an explicit 100 is stale: a finished one is " \
        "closed, a partial one is running, and an action has no percentage"
    assert stale_hundreds(st, skip_id="aaaa") == [], \
        "`progress <id> --pct 100` must not warn about the very id it just " \
        "wrote - it would fire on every single completion, and a warning " \
        "that always fires is one nobody reads"
    st["aaaa"]["blocked_on"] = "eeee"
    assert stale_hundreds(st) == [], \
        "a task blocked on a still-open action is legitimately open at 100%: " \
        "this is exactly f389 awaiting a merge decision, and warning about it " \
        "would teach the session to ignore the warning"
    st["eeee"] = {"type": "action", "status": "done"}
    assert stale_hundreds(st) == ["aaaa"], \
        "once the blocking action is answered the task is stale again - a " \
        "stale blocker must not hide it forever"
    st["aaaa"]["pct"] = "100"
    assert stale_hundreds(st) == [], \
        "pct is compared as a number, so a string cannot count as complete"
    st["aaaa"]["pct"] = 100
    st["ffff"] = {"id": "ffff", "type": "task", "status": "open", "pct": 100}
    assert stale_hundreds(st) == ["aaaa", "ffff"], "ids come back sorted"
    assert stale_hundreds({"gggg": {"type": "task", "status": "open",
                                    "progress": "92% of 5039 genes above zero"}}) == [], \
        "staleness reads the literal pct field and NEVER scrapes prose: the " \
        "scrape already turned that exact sentence - a RESULT - into a 92% bar"
```

- [ ] **Step 2: Run the suite to verify it fails**

Run: `./test.sh`
Expected: FAIL with `NameError: name 'stale_hundreds' is not defined`

- [ ] **Step 3: Write the implementation**

Add to `bin/table-talk`, directly after `blocker()`:

```python
def stale_hundreds(states, skip_id=None):
    """Ids of tasks that say they are finished but were never closed.

    `done` is run by the session and by nothing else, so a task at 100% that is
    still open is the one shape of protocol drift the data can see by itself.
    Three exclusions keep it honest rather than noisy:

    - the literal `pct` field only, never tt_model's scrape, which reads any
      percentage in the prose and once turned "92% of 5039 genes above zero"
      into a 92% bar on work that had barely started;
    - never the id this invocation just wrote, or `progress --pct 100` would
      warn about itself every time and the warning would train its own reader
      to skip it;
    - never a task blocked on a still-open action, which is a legitimately
      open task at 100% waiting on the user.
    """
    blocking = {i for i, e in states.items()
                if e.get("type") == "action" and e.get("status") != "done"}
    return sorted(
        i for i, e in states.items()
        if i != skip_id
        and e.get("type") == "task" and e.get("status") != "done"
        and isinstance(e.get("pct"), int) and e.get("pct") == 100
        and e.get("blocked_on") not in blocking)
```

The `isinstance` guard is what rejects a string `"100"`; `== 100` then rejects
every other number, booleans included.

- [ ] **Step 4: Run the suite to verify it passes**

Run: `./test.sh`
Expected: `all selftests passed`

- [ ] **Step 5: Mutation-test every pin**

For each line below: apply the change, run `./test.sh`, confirm it goes RED,
restore.

| Mutate | Pin it must break |
|---|---|
| `if i != skip_id` → `if True` | skip_id |
| `e.get("status") != "done"` → `True` | done tasks excluded |
| `e.get("type") == "task"` → `e.get("type") in ("task", "action")` | actions excluded |
| `e.get("blocked_on") not in blocking` → `True` | blocked tasks excluded |
| `e.get("status") != "done"` in the `blocking` set comprehension → `True` | answered blocker stops hiding it |
| `isinstance(e.get("pct"), int)` → `True` | string pct rejected |
| `sorted(` → `list(` | sorted output (may pass by luck — if it does, say so and drop the sorted pin as unpinnable rather than claiming it holds) |

- [ ] **Step 6: Commit**

```bash
git add bin/table-talk
git commit -m "feat(cli): stale_hundreds - a task at 100% that was never closed"
```

---

### Task 2: Print the warning, and say what it means

**Files:**
- Modify: `bin/table-talk` (`main()`, after command dispatch; pins in `selftest()`)
- Modify: `skill/SKILL.md` (one line under the 🔵 Background work section)
- Modify: `README.md` (under the progress/`--pct` section)

**Interfaces:**
- Consumes: `stale_hundreds(states, skip_id=None) -> list[str]` from Task 1.
- Produces: `warn_stale(project, skip_id=None) -> None`, which folds every file
  of `project` and prints one stderr line naming the ids, or nothing at all.

- [ ] **Step 1: Write the failing pins**

Add to `selftest()`, after the Task 1 pins:

```python
    # warn_stale: the nudge itself, on the real file layout.
    with tempfile.TemporaryDirectory() as td:
        global DATA_DIR
        keep, DATA_DIR = DATA_DIR, Path(td)
        try:
            f = DATA_DIR / "2026-01-01-proj.jsonl"
            f.write_text(json.dumps({"id": "aaaa", "type": "task",
                                     "status": "open", "pct": 100}) + "\n")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                warn_stale("proj")
            assert "aaaa" in err.getvalue(), \
                "the warning must NAME the stale id: a nudge the reader has " \
                "to go hunting for is one they will not act on"
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                warn_stale("proj", skip_id="aaaa")
            assert err.getvalue() == "", "the id just written is never named"
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                warn_stale("nonesuch")
            assert err.getvalue() == "", \
                "a project with nothing stale must print NOTHING - a warning " \
                "that appears on every clean run is noise, not a signal"
            (DATA_DIR / "unreadable.jsonl").write_text("{ not json\n")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                warn_stale("proj")
            assert "aaaa" in err.getvalue(), \
                "one corrupt file must not silence the check for the rest: " \
                "recording is what must never break, and this is only a nudge"
        finally:
            DATA_DIR = keep
```

`io` and `contextlib` are imported at the top of `selftest()` alongside the
existing `tempfile` import.

- [ ] **Step 2: Run the suite to verify it fails**

Run: `./test.sh`
Expected: FAIL with `NameError: name 'warn_stale' is not defined`

- [ ] **Step 3: Write the implementation**

Add to `bin/table-talk`, directly after `stale_hundreds()`:

```python
def warn_stale(project, skip_id=None):
    """Nudge about tasks left open at 100%, on stderr, never fatally.

    Runs after every recording command because those are what a session
    actually runs; a check that only fires inside `show --open` would depend on
    the same sweep that already failed. Costs one fold of the project's files -
    1.0 ms across five of them - and swallows everything, because a warning
    that could break `table-talk done` would be worse than no warning at all.
    """
    try:
        states = {}
        for path in sorted(DATA_DIR.glob(f"*-{glob.escape(project)}.jsonl")):
            states.update(fold(path))
        ids = stale_hundreds(states, skip_id)
    except Exception:               # a nudge must never break a recording
        return
    if ids:
        print(f"note: {', '.join(ids)} at 100% and still open — `table-talk "
              f"done <id>`, or `progress <id> --blocked-on <action-id>` if it "
              f"is waiting on the user", file=sys.stderr)
```

- [ ] **Step 4: Call it from `main()`**

In `bin/table-talk`, at the very end of `main()`, after the `if/elif` dispatch
chain, add:

```python
    if args.cmd in ("action", "task", "progress", "done", "term", "diagram"):
        warn_stale(getattr(args, "project", None) or Path.cwd().name,
                   getattr(args, "id", None))
```

- [ ] **Step 5: Run the suite to verify it passes**

Run: `./test.sh`
Expected: `all selftests passed`

- [ ] **Step 6: Verify by hand, end to end**

```bash
export TABLE_TALK_DIR=$(mktemp -d)
id=$(./bin/table-talk task "demo job")
./bin/table-talk progress "$id" --pct 100      # must print NO warning
./bin/table-talk term "demo" --intuitive a --technical b   # must warn about $id
./bin/table-talk done "$id"
./bin/table-talk term "demo2" --intuitive a --technical b  # must print NO warning
rm -rf "$TABLE_TALK_DIR"; unset TABLE_TALK_DIR
```

Expected: exactly one warning, on the third command, naming `$id`.

- [ ] **Step 7: Mutation-test the new pins**

| Mutate | Pin it must break |
|---|---|
| `if ids:` → `if True:` | clean run prints nothing |
| `states.update(fold(path))` → `states.update({})` | the warning names the id |
| the `main()` call site → deleted | (pin below) |

Add a pin that the call site exists, searching `code` (the file minus
`selftest()`, plus everything from `def main(`), and matching the real call
rather than the function's own definition:

```python
    assert "warn_stale(getattr(args, \"project\", None)" in code, \
        "the nudge must fire from main() after a recording command: defined " \
        "and never called, it catches exactly nothing"
```

- [ ] **Step 8: Document it**

In `skill/SKILL.md`, in the 🔵 Background work section, after the paragraph
ending "A sample or demo item is a task like any other — close it once it has
served its purpose.", add:

```markdown
The CLI helps with exactly one half of this: after any recording command it
prints `note: <id> at 100% and still open` for a task that reports itself
finished but was never closed. Act on it in that same turn — either `done` it,
or, if it is genuinely waiting on the user, record the action and
`progress <id> --blocked-on <action-id>` so the wall shows why. Nothing warns
about an ANSWERED ACTION left open; no log can know your reply answered it.
That half is still yours.
```

In `README.md`, in the section documenting `--pct`, after the paragraph
explaining that the bar is only as fresh as the last `progress` call, add:

```markdown
A task that reaches 100% and is never closed is the one kind of drift the log
can see by itself, so every recording command checks for it and prints a note.
It stays quiet for a task that is `done`, for the id you just wrote, and for
one blocked on a still-open action.
```

- [ ] **Step 9: Commit**

```bash
git add bin/table-talk skill/SKILL.md README.md
git commit -m "feat(cli): warn when a task sits at 100% but was never closed"
```

---

## Self-Review

**1. Requirement coverage.** Issue #190's three correctness requirements map to
Task 1: literal `pct` only (pin: the "92% of 5039 genes" case), never the id
just written (pin: `skip_id`), never a blocked task (pin: `blocked_on`). The
"every recording command" requirement maps to Task 2 Step 4, pinned in Step 7.
The stderr and never-fatal requirements map to Task 2 Step 3, pinned by the
corrupt-file case.

**2. Placeholder scan.** No TBDs; every code step carries the literal code, and
every mutation step names the exact edit and the pin it must break.

**3. Type consistency.** `stale_hundreds(states, skip_id=None) -> list[str]` is
defined in Task 1 and consumed in Task 2 under the same name and signature.
`warn_stale(project, skip_id=None)` is defined and called under one name.
`states` is `dict[str, dict]` in both, matching what `fold()` returns.

**One defect found and fixed during review.** The draft of Task 1 Step 3 carried
a clause reading `e.get("pct") is True is not True`, which is a chained
comparison that evaluates to `False` for every input — it would have made
`stale_hundreds` return `[]` always, and the "only an open task is stale" pin
would have caught it only because that pin expects a non-empty list. It is
replaced by `isinstance(e.get("pct"), int) and e.get("pct") == 100`, which is
what the accompanying pin actually tests.
