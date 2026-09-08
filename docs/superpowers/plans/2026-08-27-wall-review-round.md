# Wall Review Round Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eight review items from the 2026-08-27 wall walkthrough: keep sketches on done items, collapse every section with a glyph summary, legible theme icons, readable sub-row spacing, retroactive edits, session codes, and a merged per-project view.

**Architecture:** Seven changes, each its own issue + branch + PR + squash-merge, in order. Every one is the smallest diff that holds — no new abstractions, no new dependencies, existing helpers reused (`_prompt`'s toggle machinery, `_art_sub`, `with_extras`, `parse_stem`).

**Tech Stack:** Python stdlib (CLI, model), NiceGUI 3.16 via uv (dashboard), plain CSS.

**Spec:** The user's numbered review list (2026-08-27, third round). Item 1 (sample diagrams) was satisfied live and needs no code.

## Global Constraints

- CLI and model stay stdlib-only; the dashboard's only dependency stays `nicegui>=3.16,<4`.
- Log content is untrusted: props ASSIGNED only, exactly one `ui.html`, no `shell=`.
- `./test.sh` green after every task; every behaviour change pinned by an assertion in the touched file's `selftest()`.
- Ponytail: reuse before adding, no config knob for a constant, no helper with one caller unless it carries a test.
- Git per change: `gh issue create` → branch → TDD commits → `gh pr create` → squash-merge. Trailer `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`; PR bodies end with the Claude Code footer.

---

### Task 1: Done items keep their sketch (review item 2)

**Files:** `bin/table-talk-dash.py` (`_done_row` + pin), `bin/tt_model.py` (`weight` + test)

**Interfaces:** none new.

- [ ] **Step 1: Issue + branch**

```bash
cd <repo> && git checkout main && git pull
gh issue create --title "A resolved item loses its sketch" --body "_done_row renders only the id and headline, so the ASCII sketch recorded on an action or job vanishes when it is marked done — exactly when it becomes reference material worth keeping. Render the sketch on done rows too."
git checkout -b fix/done-keeps-sketch
```

- [ ] **Step 2: Failing pin** — in `bin/table-talk-dash.py` `selftest()`, after the art-last pin:

```python
    assert code.count("_art_sub(ev)") == 3, \
        "actions, jobs AND done rows draw the sketch: a resolved item is when " \
        "the picture becomes reference, so dropping it there is backwards"
```

Run `uv run --script bin/table-talk-dash.py --selftest` — expect 2 != 3.

- [ ] **Step 3: Implement** — replace `_done_row`'s body:

```python
    with ui.element("div").classes("row" + _dim(ev, query)):
        _id_button(ev, "id-ok")
        with ui.element("div"):
            _cell(ev.get("background") or ev.get("what", ""), query, "ttl")
            _art_sub(ev)
```

- [ ] **Step 4: Model** — a done item's sketch is drawn, so it costs height again. In `bin/tt_model.py` `weight()`, move the sketch charge above the done skip by changing the guard to read the field on any action/task. Replace the existing sketch line with one placed BEFORE `if ev.get("status") == "done": continue`:

```python
        if ev.get("type") in ("action", "task") and ev.get("diagram"):
            units += 1 + str(ev["diagram"]).count("\n") // 2
```

and update the existing test:

```python
    assert weight({"a": {"type": "action", "status": "done", "diagram": "x\ny",
                         "ts": 1}}) == 3, \
        "a done item still DRAWS its sketch, so it still costs the packer height"
```

- [ ] **Step 5:** `./test.sh` → `all selftests passed`. Commit `fix(ui): a resolved item keeps its sketch — that is when it becomes reference`, PR, stop.

---

### Task 2: Collapse every section, with a glyph bar when shut (review items 3 + 4)

One task, not two: both edit `_prompt` and the same five call sites.

**Files:** `bin/table-talk-dash.py`, `bin/tt.css`, `docs/config.example.toml`, `README.md`

**Interfaces:** `_prompt(..., bar=None)` where `bar` is `(filled, empty)` glyph strings.

- [ ] **Step 1: Issue + branch**

```bash
git checkout main && git pull
gh issue create --title "Actions and jobs cannot be collapsed, and a collapsed section says nothing" --body "Only glossary and done collapse. Make actions, jobs and diagrams collapse too (config: collapsed_sections gains \"actions\"/\"jobs\"), and give every collapsed header a glyph bar — █ per item still wanting attention, ░ per resolved one — so a shut section still reports its state."
git checkout -b feat/collapsible-sections
```

- [ ] **Step 2: Failing pins** — in `selftest()` css block:

```python
    assert ".pr .bar" in css and ".pr .bar.e" in css, \
        "a collapsed section still has to report itself: █ per item wanting " \
        "attention, ░ per resolved one, in the section's own colour"
```

and in the source-pin block:

```python
    for key in ('"act": "actions" not in collapsed', '"job": "jobs" not in collapsed'):
        assert key in code, \
            f"{key}: every section collapses, and which ones START shut is the " \
            "config's call, named the way the config names them"
```

Run — expect failure.

- [ ] **Step 3: `_prompt` gains the bar.** Signature becomes:

```python
def _prompt(cls, title, count, toggles=None, opened=None, key="", force=False, bar=None):
```

After the `caret = ...` line, inside the same `with` block:

```python
        # Shown only while the section is shut: open, the rows themselves say it.
        bar_el = ui.element("div").classes("bar-box")
        if bar:
            with bar_el:
                ui.label(bar[0]).classes("bar")
                ui.label(bar[1]).classes("bar e")
```

After `toggles.set_visibility(shown)` add `bar_el.set_visibility(bool(bar) and not shown)`, and inside `flip`, after `toggles.set_visibility(now)`:

```python
        bar_el.set_visibility(bool(bar) and not now)
```

- [ ] **Step 4: The glyph helper.** Above `_prompt`:

```python
def bar_for(open_n, done_n):
    """A section's state as glyphs: █ per item still wanting attention, ░ per
    resolved one. Capped like the footer's cells so a long section cannot
    wreck the prompt line."""
    open_n, done_n = max(0, open_n), max(0, done_n)
    if open_n + done_n > MAX_CELLS:               # scale, never overflow
        total = open_n + done_n
        open_n = round(MAX_CELLS * open_n / total)
        done_n = MAX_CELLS - open_n
    return "█" * open_n, "░" * done_n
```

with tests beside the `resolved_cells` ones:

```python
    assert bar_for(3, 2) == ("███", "░░")
    assert bar_for(0, 0) == ("", "")
    f, e = bar_for(30, 30)
    assert len(f) + len(e) == MAX_CELLS, "a long section is scaled, never overflowed"
    assert bar_for(-1, 0) == ("", ""), "a negative count draws nothing"
```

- [ ] **Step 5: Wire the five sections** in `render_window_body`. The `opened` default becomes:

```python
        opened = container.tt_open = {"act": "actions" not in collapsed,
                                      "job": "jobs" not in collapsed,
                                      "dia": "diagrams" not in collapsed,
                                      "gls": "glossary" not in collapsed,
                                      "ok": "done" not in collapsed}
```

Actions and jobs now build into boxes exactly like the others (build the box, then the prompt, then `move(container, -1)`). Replace the actions and jobs blocks with:

```python
        done = done_rows(state)
        done_a = sum(1 for e in done if e.get("type") == "action")

        acts_box = ui.element("div")
        with acts_box:
            if not acts:
                ui.label("nothing needs you").classes("empty")
            for ev in acts:
                _action_row(ev, str(ev["id"]) == newest_action_id, query,
                            str(ev["id"]) in changed)
        _prompt("p-act", "actions --open", len(acts), toggles=acts_box, opened=opened,
                key="act", force=_hits(acts, query), bar=bar_for(len(acts), done_a))
        acts_box.move(container, -1)

        jobs_box = ui.element("div")
        with jobs_box:
            if not jobs:
                ui.label("nothing running").classes("empty")
            for ev in jobs:
                _task_row(ev, query, str(ev["id"]) in changed)
        _prompt("p-job", "jobs", len(jobs), toggles=jobs_box, opened=opened,
                key="job", force=_hits(jobs, query),
                bar=bar_for(len(jobs), len(done) - done_a))
        jobs_box.move(container, -1)
```

(`done` moves up here; delete its later re-computation and keep the existing done block using this `done`.) Give the remaining three prompts their bars: diagrams `bar=bar_for(0, len(dias))`, glossary `bar=bar_for(0, len(terms))`, done `bar=bar_for(0, len(done))`.

- [ ] **Step 6: CSS** — after the `.p-ok` line:

```css
/* a shut section still reports itself; open, the rows say it and this hides */
.bar-box{display:flex;margin-left:2px}
.bar{letter-spacing:-.5px}
.bar.e{color:var(--ink-3);opacity:.55}
```

- [ ] **Step 7: Docs** — `docs/config.example.toml`: the collapsed_sections comment becomes `# window sections that start folded ("actions", "jobs", "diagrams", "glossary", "done")`. README's Dashboard bullet: after the sections sentence add `Every section collapses, and a shut one keeps a █/░ bar reporting what it holds.`

- [ ] **Step 8:** `./test.sh`, commit `feat(ui): every section collapses, and a shut one keeps a glyph bar`, PR, stop.

---

### Task 3: Legible theme toggle (review item 5)

**Files:** `bin/table-talk-dash.py` (`THEME_ICONS`, tooltip, pin), `bin/tt.css` (`.dw-theme`)

Root cause: `☀` (U+2600) and `☾` (U+263E) are absent from JetBrains Mono, so they render from a fallback face at a different weight and size beside `◐` (which Adwaita Mono supplies). Geometric-shape glyphs the primary font actually carries fix it outright — no fallback, one visual family.

- [ ] **Step 1: Issue + branch**

```bash
git checkout main && git pull
gh issue create --title "The theme toggle's glyphs are hard to see" --body "☀ and ☾ are missing from JetBrains Mono, so each renders from a different fallback face beside ◐ — mismatched weights at 12px with --ink-3 contrast. Use the geometric shapes the primary font carries (○ light, ● dark, ◐ system), enlarge, raise the contrast, and add a tooltip naming the mode."
git checkout -b fix/theme-toggle-legibility
```

- [ ] **Step 2: Failing pin** — replace the existing `assert set(THEME_ICONS) == set(THEME_MODES)` with:

```python
    assert set(THEME_ICONS) == set(THEME_MODES)
    assert THEME_ICONS == {"system": "◐", "light": "○", "dark": "●"}, \
        "one geometric family the PRIMARY face carries: ☀ and ☾ are absent " \
        "from JetBrains Mono and each drew from a different fallback"
    assert "font-size:15px" in css.split(".dw-theme{")[1].split("}")[0], \
        "a 12px control at --ink-3 is the complaint; it is a real button"
```

- [ ] **Step 3: Implement** — `THEME_ICONS = {"system": "◐", "light": "○", "dark": "●"}`; in `main()` after `theme_lbl = ui.label(THEME_ICONS[mode])` is created, set the tooltip and keep it current:

```python
                    theme_btn.props["title"] = f"theme: {mode} (click to cycle)"
```

and in `cycle_theme`, after `theme_lbl.set_text(...)`:

```python
        theme_btn.props["title"] = f"theme: {mode} (click to cycle)"
```

- [ ] **Step 4: CSS** — `.dw-theme` becomes:

```css
.dw-theme{border:0;background:transparent;font:inherit;font-size:15px;
  color:var(--ink-2);cursor:pointer;padding:0 4px;line-height:1}
```

- [ ] **Step 5:** `./test.sh`, commit `fix(ui): theme toggle uses glyphs the primary face carries, at a readable size`, PR, stop.

---

### Task 4: Breathing room between int / why / rec (review item 8)

**Files:** `bin/tt.css` only (plus one pin)

The guide is one continuous rule (`.sub::before` spans the row and 2px past it to bridge `margin-top`). Widening the gap means widening BOTH numbers or the rule breaks.

- [ ] **Step 1: Issue + branch**

```bash
git checkout main && git pull
gh issue create --title "int/why/rec sub-rows are too tight to scan" --body "One line of space between an item's description and its int/why/rec rows makes them far easier to read. The tree guide must stay connected: .sub::before bridges the gap with a negative bottom, so the margin and the bridge move together."
git checkout -b fix/sub-row-spacing
```

- [ ] **Step 2: Failing pin** — in the css block, after the existing `.sub::before` assertions:

```python
    gap = sub.split("margin-top:")[1].split("px")[0]
    bridge = css.split(".sub::before{")[1].split("}")[0].split("bottom:-")[1].split("px")[0]
    assert gap == bridge == "7", \
        "the guide bridges the gap with a negative bottom: move the margin " \
        "without moving the bridge and the rule breaks between sub-rows"
```

- [ ] **Step 3: Implement** — in `.sub` change `margin-top:2px` → `margin-top:7px`; in `.sub::before` change `bottom:-2px` → `bottom:-7px`.

- [ ] **Step 4:** `./test.sh`, commit `fix(ui): one line of air between sub-rows, guide still unbroken`, PR, stop.

---

### Task 5: Retroactive intuitive and sketches (review item 9)

**Files:** `bin/table-talk` (`progress` text optional + test), `skill/SKILL.md`, `README.md`

`progress` already routes through `with_extras`, so the only thing blocking a retroactive edit is its required positional `text`.

- [ ] **Step 1: Issue + branch**

```bash
git checkout main && git pull
gh issue create --title "No way to add an intuitive line or sketch to an existing item" --body "progress carries --intuitive/--diagram already, but its positional text is required, so amending an old item forces a meaningless progress note onto it. Make the text optional: 'table-talk progress <id> --intuitive ...' should amend and nothing else."
git checkout -b feat/amend-existing-items
```

- [ ] **Step 2: Failing test** — in `bin/table-talk` `selftest()`, after the `with_extras` asserts:

```python
    i4 = add_event("proj", {"type": "action", "status": "open", "background": "b",
                            "why": "w", "rec": "r"})
    update(i4, with_extras({}, NS(intuitive="plain words", diagram=None)))
    got = fold(session_file("proj"))[i4]
    assert got["intuitive"] == "plain words" and got["background"] == "b", \
        "an amend adds the field and touches nothing else"
    assert "progress" not in got, \
        "amending an ACTION must not invent a progress note it never had"
```

Run `python3 bin/table-talk --selftest` — expect pass already at the function level (the gap is argparse), so ALSO verify the CLI path by hand after Step 3:

```bash
TABLE_TALK_DIR=$(mktemp -d) sh -c 'id=$(table-talk action a --why w --rec r --project t); table-talk progress $id --intuitive "plain"; table-talk show'
```
Expected before the change: argparse error `the following arguments are required: text`.

- [ ] **Step 3: Implement** — `pr.add_argument("text", nargs="?", help="progress note; omit to only amend --intuitive/--diagram")`, and the dispatch branch becomes:

```python
    elif args.cmd == "progress":
        update(args.id, with_extras({"progress": args.text} if args.text else {}, args))
```

- [ ] **Step 4: Docs** — `skill/SKILL.md`, in the record-as-you-go table, after the progress row:

```markdown
| Item needs a plain line or sketch later | `table-talk progress <id> --intuitive "<line>" --diagram "<ascii>"` (no note needed) |
```

README **Use**, after the progress line:

```
    table-talk progress "$id" --intuitive "what this means in plain words"   # amend, no note
```

- [ ] **Step 5:** `./test.sh`, verify the CLI path from Step 2 now succeeds, commit `feat(cli): progress can amend an item without inventing a note`, PR, stop.

---

### Task 6: Session codes on window titles (review item 6)

**Files:** `bin/table-talk` (stamp `sid`), `bin/table-talk-dash.py` (`session_code`, title), `README.md`

One log file is one date + project and may hold several agent sessions, so the code shown is the session that wrote LAST; the merged view (Task 7) tags every row.

- [ ] **Step 1: Issue + branch**

```bash
git checkout main && git pull
gh issue create --title "Window titles number sessions instead of naming them" --body "phephree:0 / phephree:1 says only 'newer' and 'older'. The agent already has a session id (CLAUDE_CODE_SESSION_ID); stamp its first four chars on every event and title the window with the code of whichever session wrote it last. Files with no stamped events keep the index."
git checkout -b feat/session-codes
```

- [ ] **Step 2: Failing CLI test** — in `bin/table-talk` `selftest()`:

```python
    assert session_code({}) == "", "no session id, no stamp"
    assert session_code({"CLAUDE_CODE_SESSION_ID": "9f3c8a1e-dead"}) == "9f3c", \
        "four chars is enough to tell sessions apart and fits the id column"
    os.environ["CLAUDE_CODE_SESSION_ID"] = "abcd1234-x"
    try:
        i5 = add_event("proj", {"type": "task", "status": "open", "what": "s"})
        assert fold(session_file("proj"))[i5]["sid"] == "abcd"
    finally:
        del os.environ["CLAUDE_CODE_SESSION_ID"]
```

- [ ] **Step 3: CLI implement** — above `add_event`:

```python
def session_code(env=None):
    """Four chars of the agent's own session id, or ''. One log file is one
    date+project and can hold several sessions, so identity belongs on the
    EVENT, not the filename."""
    return ((env if env is not None else os.environ).get("CLAUDE_CODE_SESSION_ID") or "")[:4]
```

In `add_event`, `add_term` and `add_diagram`, build the record as:

```python
        ev = {"id": i, "ts": int(time.time()), **fields}
        if (s := session_code()):
            ev.setdefault("sid", s)
        append(f, ev)
```

(`add_term`/`add_diagram` append to `path`, not `f` — keep each function's own target.)

- [ ] **Step 4: Dashboard failing pin** — in `selftest()`:

```python
    st_sid = {"a": {"id": "a", "sid": "beef", "ts": 5},
              "b": {"id": "b", "sid": "cafe", "ts": 9}}
    assert session_label(st_sid, 3) == "cafe", "the session that wrote LAST names the window"
    assert session_label({"a": {"id": "a", "ts": 1}}, 3) == "3", \
        "a file recorded before session stamping keeps its tmux index"
    assert session_label({}, 0) == "0"
```

- [ ] **Step 5: Dashboard implement** — beside `abbrev`:

```python
def session_label(state, index):
    """What follows the colon in a window title: the code of the session that
    wrote this file last, or the tmux index for files recorded before stamping."""
    best, code = -1, ""
    for ev in state.values():
        if ev.get("sid") and ev.get("ts", 0) > best:
            best, code = ev["ts"], str(ev["sid"])
    return code or str(index)
```

In `poll()`, the index line becomes:

```python
                            (win["ix"], f":{session_label(states[k], where[k][1])}")):
```

- [ ] **Step 6: Docs** — README Dashboard bullet: change "A titlebar reads `project:index`" to "A titlebar reads `project:session` — the code of the agent session that wrote the file last, or its index for older files".

- [ ] **Step 7:** `./test.sh`, commit `feat: stamp the agent session on every event and title windows with it`, PR, stop.

---

### Task 7: Merged per-project view (review item 7)

**Files:** `bin/tt_model.py` (`merge_projects`), `bin/table-talk-dash.py` (KEYMAP, poll, `_id_button`), `bin/tt.css`, `README.md`

**Interfaces:** consumes Task 6's `sid`. `M.merge_projects([(stem, state)]) -> {project: state}` with every event carrying `_from`.

- [ ] **Step 1: Issue + branch**

```bash
git checkout main && git pull
gh issue create --title "No way to read a project's whole history as one card" --body "Reviewing an old project means reading a card per day. Add a merge view (key u): one window per project holding every session's actions, jobs, glossary and done, each row tagged with the session code that recorded it (or its date for pre-stamp events)."
git checkout -b feat/merged-project-view
```

- [ ] **Step 2: Model failing test** — in `bin/tt_model.py` `selftest()`, after the group_sessions block:

```python
    m = merge_projects([("2026-08-26-phe", {"a": {"id": "a", "sid": "beef", "ts": 1}}),
                        ("2026-08-25-phe", {"b": {"id": "b", "ts": 2}}),
                        ("2026-08-25-gpn", {"c": {"id": "c", "ts": 3}})])
    assert set(m) == {"phe", "gpn"}, "one card per project, not per file"
    assert set(m["phe"]) == {"a", "b"}, "every session's rows land in one state"
    assert m["phe"]["a"]["_from"] == "beef", "a stamped event is tagged with its session"
    assert m["phe"]["b"]["_from"] == "0825", \
        "an event recorded before stamping falls back to its file's date"
    assert merge_projects([]) == {}
```

- [ ] **Step 3: Model implement** — after `group_sessions`:

```python
def merge_projects(sessions):
    """[(stem, state)] -> {project: one merged state}, every event tagged with
    the session that recorded it. Ids are minted unique across every file, so
    the merge cannot collide. The tag is the event's own session code, or its
    file's date for anything recorded before stamping existed."""
    out = {}
    for stem, state in sessions:
        date, project = parse_stem(stem)
        fallback = date[5:].replace("-", "") or project[:4]
        bucket = out.setdefault(project, {})
        for i, ev in state.items():
            bucket[i] = {**ev, "_from": str(ev.get("sid") or fallback)}
    return out
```

- [ ] **Step 4: Dashboard failing pins**

```python
    assert KEYMAP["u"] == "merge", "the merge view needs a key like every other view"
    assert "M.merge_projects" in code, \
        "the merged wall is the model's merge, not a second grouping in the UI"
    assert '.sid' in css, "a merged row must say which session recorded it"
```

- [ ] **Step 5: Dashboard implement**

`KEYMAP` gains `"u": "merge"` (before `"?"`), and the KEYMAP equality assertion is updated to match.

`_id_button` renders the tag when the merger set one:

```python
    with btn:
        ui.label(str(ev["id"]))
        if ev.get("_from"):
            ui.label(str(ev["_from"])).classes("sid")
```

In `do()`, beside `needs-me`:

```python
        elif what == "merge":
            put("merged", not store("merged", False))
            tick()
```

In `poll()`, replace the `where`/`order` lines with:

```python
        merged = bool(store("merged", False))
        # The DRAWER always lists real session files; only the wall merges.
        if merged:
            wall_states = M.merge_projects(list(states.items()))
            order = [g["project"] for g in ordered]
            where = {g["project"]: (g["project"], len(g["sessions"])) for g in ordered}
        else:
            wall_states = states
            order = [s["key"] for g in ordered for s in g["sessions"]]
            where = {s["key"]: (g["project"], s["index"]) for g in ordered for s in g["sessions"]}
```

`ordered`/`apply_fold_rules`/`render_drawer` must run BEFORE this block (they already do; move the `where`/`opens` lines down past `ordered`). `opens` keeps keying off sessions for the drawer, and gains a merged form for needs-me:

```python
        opens = ({g["project"]: g["open_actions"] for g in ordered} if merged else
                 {s["key"]: s["summary"]["open_actions"] for g in ordered for s in g["sessions"]})
```

Then replace every remaining `states[k]` with `wall_states[k]`, the stale-window sweep's `if key not in states` with `wall_states`, and the weights map with `{k: 1 if k in folds else M.weight(v) for k, v in wall_states.items()}`. The `for k in visible` loops read `wall_states[k]`. `layout_key` needs no new field: merging replaces every key in `visible`, so the layout key already differs.

The chip mirrors its state like needs-me:

```python
        chips["merge"].classes(replace="sl-h on" if merged else "sl-h")
```

- [ ] **Step 6: CSS** — after `.id.copied`:

```css
/* the session that recorded a row, on the merged wall only */
.sid{display:block;font-size:9px;line-height:1.3;color:var(--ink-3);opacity:.85}
```

- [ ] **Step 7: Docs** — README key table gains `| \`u\` | merge: one window per project, every row tagged with its session |`, and the Dashboard wall bullet notes the merged view.

- [ ] **Step 8:** `./test.sh`, commit `feat(ui): merged per-project wall, rows tagged with their session`, PR, stop.

---

## Self-review notes

- Coverage: item 1 satisfied live (samples 57ec/1b02/a63a/fa63); items 2→T1, 3+4→T2, 5→T3, 8→T4, 9→T5 (+ manual backfills after merge), 6→T6, 7→T7.
- Ordering: T7 consumes T6's `sid`; T5's amend command is what the backfills need; the rest are independent. T1's weight edit and T2's render edits touch different regions of the same two files, sequentially merged.
- Type consistency: `bar_for` returns `(str, str)` and `_prompt(bar=…)` destructures exactly that; `session_code` (CLI, env→str) and `session_label` (dash, state+index→str) are deliberately different names for different jobs; `_from` is set only by `merge_projects` and read only by `_id_button`.
- Known ceiling, accepted: terms and diagrams render their own id cell (`.id-gls`/`.id-mag`) and so carry no session tag in the merged view — glossary is cumulative reference, which is what merging it is for.
