# Inline ASCII Sketches & Intuitive Descriptions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Actions and jobs can carry a small ASCII diagram drawn centered under their description in the theme's colours, and a plain-English `intuitive` sub-row — both recorded via optional CLI flags.

**Architecture:** Two changes, each its own issue + branch + PR + squash-merge, in order. Task 1 adds the `--diagram` flag end to end (CLI → model classifier → dashboard sub-row renderer → CSS → skill). Task 2 adds `--intuitive` the same way. Both follow the repo's selftest idiom: behaviour pinned in the touched file's `selftest()`, source invariants pinned by string/AST checks.

**Tech Stack:** Python stdlib (CLI, model), NiceGUI 3.16 via uv (dashboard), plain CSS.

**Spec:** The user request (2026-08-27, second round): "Right below each description, make an ASCII diagram… use the color scheme of our theme… centered and no larger than the minimum size of a single card." And: actions gain an INTUITIVE sub-row beside WHY/REC ("a concise plain english simple description and what is needed"); jobs gain an intuitive sub-row too, with an ASCII diagram to reference. The standalone mermaid `diagram` feature stays (ruled: inline ASCII for small sketches, mermaid for big pictures — user asked via action a180).

## Global Constraints

- CLI and model stay stdlib-only; the dashboard's only dependency stays `nicegui>=3.16,<4`.
- Everything from a log file is untrusted: art renders as `ui.label` text runs, never markup; `ui.html` count stays exactly one; props only ASSIGNED; no `shell=`.
- `./test.sh` must pass after every task.
- Git per change: `gh issue create` → branch → TDD commits → `gh pr create` → squash-merge. Commits end with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`; PR bodies end with the Claude Code footer.
- The theme constraint is load-bearing: the art uses ONLY existing theme tokens (`--ink`, `--ink-3`, `--mono`), so a config palette override recolours it too.
- Centering and size are load-bearing: the art sits centered in its card and can never widen it (scrolls inside its own box when oversized).

## Design facts the tasks depend on

- An action row renders `.ttl` then guided `.sub` rows (why, rec) on one continuous rule; **`.sub:last-child::before` draws the corner on the LAST sub**, so the art must be emitted as a `.sub` row itself, LAST — a bare div after the subs would strand the corner.
- `.sub` switches to the prose face (`font-family:var(--prose)`), so `.art` must restate `var(--mono)`.
- `ui.label` emits a **div**; inline art runs need `.art>*{display:inline}` (the `.blocks`/`.cells` trap).
- `.sub` is a `28px 1fr` grid; `.art` is a grid item, so `justify-self:center` centers it and `min-width:0` + `max-width:100%` + `overflow-x:auto` keep oversized art scrolling inside its box instead of widening the card. `white-space:pre` preserves the art's geometry against the row's inherited `overflow-wrap:anywhere`.
- The dash selftest's `code` variable (`src.split("def selftest():")[0] + src.split("\ndef ago(", 1)[1]`) covers module code before `selftest()` AND `main()` after it — pins against `code` are mutation-safe for both regions.
- CLI selftests test functions, not argparse — so the flags→fields logic lives in a pure helper `with_extras(fields, args)` testable with `types.SimpleNamespace`.

---

### Task 1: Inline ASCII sketches (`--diagram`)

**Files:**
- Modify: `bin/tt_model.py` (add `_is_structure`/`art_spans`, extend `weight` and `_TEXT_FIELDS`, selftests)
- Modify: `bin/table-talk` (add `with_extras`, `--diagram` on action/task/progress, selftests)
- Modify: `bin/table-talk-dash.py` (add `_art_sub`, call it from `_action_row`/`_task_row`, selftests)
- Modify: `bin/tt.css` (`.art` rules)
- Modify: `skill/SKILL.md` (rewrite the Diagrams section to cover sketches)
- Modify: `README.md` (one example line + one Dashboard mention)

**Interfaces:**
- Produces: event field `diagram` (str, ASCII art) on `action`/`task` events; CLI helper `with_extras(fields, args)` copying non-empty `intuitive`/`diagram` attrs (Task 2 reuses it verbatim); `tt_model.art_spans(text) -> [(chunk, is_structure)]`; renderer `_art_sub(ev)`.

- [ ] **Step 1: Issue + branch**

```bash
cd <repo> && git checkout main && git pull
gh issue create --title "Inline ASCII sketches under actions and jobs" \
  --body "$(cat <<'EOF'
Actions and jobs should carry an optional small ASCII diagram, drawn right below the item's description on the dashboard — centered, themed via the existing ink tokens (structure strokes recede to the faint ink, labels read in the full ink), and never wider than the card (oversized art scrolls inside its own box). Recorded via --diagram on `table-talk action|task|progress`. Follow-up to the mermaid diagrams of #77: inline ASCII is the simpler form the reply protocol actually needs — it renders in the terminal reply AND on the wall.
EOF
)"
git checkout -b feat/inline-ascii-sketches
```

- [ ] **Step 2: Model — failing selftests first**

In `bin/tt_model.py` `selftest()`, after the `weight(job) == weight(job2)` assertion, add:

```python
    # art_spans: strokes recede, labels read - and the chunks must reassemble.
    assert art_spans("A->B") == [("A", False), ("->", True), ("B", False)]
    assert art_spans("┌─┐") == [("┌─┐", True)], "box drawing is structure"
    assert [c for c, s in art_spans("│ logs │") if not s] == ["logs "], \
        "words inside a box are content; strokes and their padding recede"
    assert art_spans("") == [] and art_spans(None) == []
    assert art_spans("   ") == [("   ", False)], "all-neutral art is one content run"
    _arng = _random.Random(20260827)
    _art_alphabet = "ab XY01−│┌┘├→▲▶█░ -|+/\\<>^v_=~*.:\n\t"
    for _ in range(500):
        t = "".join(_arng.choice(_art_alphabet) for _ in range(_arng.randint(0, 60)))
        sp = art_spans(t)
        assert "".join(c for c, _ in sp) == t, f"chunks must reassemble {t!r}"
        assert all(c for c, _ in sp), "no empty chunks"
        assert all(isinstance(s, bool) for _, s in sp)

    art_task = {"a": {"type": "task", "status": "open", "what": "x",
                      "diagram": "a\nb\nc\nd", "ts": 1}}
    plain_task = {"a": {"type": "task", "status": "open", "what": "x", "ts": 1}}
    assert weight(art_task) == weight(plain_task) + 2, \
        "a 4-line sketch costs the packer 1 + lines//2 units - it is real height"
    assert weight({"a": {"type": "action", "status": "done", "diagram": "x\ny",
                         "ts": 1}}) == 1, "a done item's sketch is collapsed with it"
    assert "flow" in row_text({"id": "x", "type": "task", "diagram": "a->flow"}), \
        "the filter must see a sketch's labels"
```

(Note: `_random` is already imported inside `selftest()` as `import random as _random` — the new code reuses it, so place these lines AFTER that import, which sits mid-function; putting them right after the `weight(job) == weight(job2)` line is BEFORE the import, so instead place the whole block after the existing `marked`/`parts` property-test loop that uses `_random`, i.e. directly after the `"chunks must reassemble losslessly"` loop.)

Run: `python3 bin/tt_model.py --selftest` — expect NameError `art_spans`.

- [ ] **Step 3: Model — implement**

After `pack()` and before `_TEXT_FIELDS`, add:

```python
_ART_ASCII = set("-|+/\\<>^v_=~*.:'`,;()[]{}#")


def _is_structure(ch):
    """A stroke character, as opposed to label text."""
    if ch in _ART_ASCII:
        return True
    o = ord(ch)
    return (0x2190 <= o <= 0x21FF     # arrows
            or 0x2500 <= o <= 0x259F  # box drawing + block elements
            or 0x25A0 <= o <= 0x25FF)  # geometric shapes


def art_spans(text):
    """Split ASCII art into [(chunk, is_structure)] runs.

    Structure is box-drawing, arrows, geometric shapes and ASCII stroke
    characters; everything else (labels) is content. Whitespace is neutral and
    extends whatever run is open, so the chunks reassemble the art losslessly
    - the renderer colours structure with the faint ink and labels with the
    full ink, and a split that dropped a byte would redraw different art.
    """
    if not text:
        return []
    out, cur, cls = [], "", None
    for ch in str(text):
        k = cls if ch in " \t\n" else _is_structure(ch)
        if k != cls and cls is not None and k is not None:
            out.append((cur, cls))
            cur, cls = "", k
        cur += ch
        if cls is None:
            cls = k
    out.append((cur, bool(cls)))
    return out
```

In `weight()`, after the `elif typ == "diagram":` branch (keep it), add at the same level as the type branches, just before the function's `return`:

```python
        if typ in ("action", "task") and ev.get("diagram"):
            units += 1 + str(ev["diagram"]).count("\n") // 2
```

Extend `_TEXT_FIELDS` with the new field:

```python
_TEXT_FIELDS = ("id", "background", "why", "rec", "what", "progress",
                "term", "intuitive", "technical", "title", "mermaid", "diagram")
```

Run: `python3 bin/tt_model.py --selftest` — expect `ok`. Commit: `feat(model): art_spans classifier + sketches carry packing weight`.

- [ ] **Step 4: CLI — failing selftest first**

In `bin/table-talk` `selftest()`, before the `serve_refusal` block, add:

```python
    from types import SimpleNamespace as NS
    assert with_extras({"a": 1}, NS(diagram="x", intuitive=None)) == {"a": 1, "diagram": "x"}
    assert with_extras({}, NS()) == {}, "absent flags write nothing"
    assert with_extras({}, NS(diagram="", intuitive="i")) == {"intuitive": "i"}, \
        "an empty string is not an extra - it must not land in the event"
```

Run: `python3 bin/table-talk --selftest` — expect NameError `with_extras`.

- [ ] **Step 5: CLI — implement**

After `add_diagram`, add:

```python
def with_extras(fields, args):
    """The optional extras action/task/progress share. Only a flag that was
    actually given (and non-empty) lands in the event: fold() shallow-merges
    by id, so a null written here would CLEAR the field on every later
    progress update that did not repeat it."""
    for k in ("intuitive", "diagram"):
        v = getattr(args, k, None)
        if v:
            fields[k] = v
    return fields
```

Add the flag to the three subparsers (`a`, `t`, `pr`):

```python
    a.add_argument("--diagram", help="small ASCII sketch drawn under the item (≤40 cols)")
```
```python
    t.add_argument("--diagram", help="small ASCII sketch drawn under the item (≤40 cols)")
```
```python
    pr.add_argument("--diagram", help="add or replace the item's ASCII sketch")
```

Change the three dispatch branches to route through the helper:

```python
    elif args.cmd == "action":
        print(add_event(args.project, with_extras(
            {"type": "action", "status": "open", "background": args.background,
             "why": args.why, "rec": args.rec}, args)))
    elif args.cmd == "task":
        print(add_event(args.project, with_extras(
            {"type": "task", "status": "open", "what": args.what}, args)))
    elif args.cmd == "progress":
        update(args.id, with_extras({"progress": args.text}, args))
```

Run: `python3 bin/table-talk --selftest` — expect `ok`. Commit: `feat(cli): --diagram sketches on action, task and progress`.

- [ ] **Step 6: Dashboard — failing selftests first**

In `bin/table-talk-dash.py` `selftest()`, in the css section (after the `.win-b .row:has(.id-mag)` assertion), add:

```python
    art = css.split(".art{")[1].split("}")[0]
    assert "white-space:pre" in art and "justify-self:center" in art, \
        "a sketch keeps its own geometry (the row's overflow-wrap:anywhere " \
        "must not fold a box border) and sits CENTERED in the card - the spec"
    assert "max-width:100%" in art and "overflow-x:auto" in art and "min-width:0" in art, \
        "art wider than the narrowest card scrolls inside its own box - it " \
        "must never widen the card"
    assert "var(--mono)" in art, \
        ".sub switched to the prose face; art must restate mono or misalign"
    assert ".art>*{display:inline}" in css, \
        "every glyph the renderer emits is a div - the .blocks trap again"
    assert ".art .st" in css, \
        "structure strokes recede to the faint ink so the labels read first"
```

and in the source-pin section (after the tally-tuple pin), add:

```python
    assert "M.art_spans" in code, \
        "art is split by the property-tested model classifier and rendered as " \
        "label runs - never raw, never markup: it comes out of a LOG FILE"
    assert code.index('for field in ("why", "rec")') < code.index("_art_sub(ev)"), \
        "the sketch is the LAST guided sub-row: .sub:last-child draws the " \
        "tree corner, and a bare div after the subs would strand it mid-air"
```

Run: `uv run --script bin/table-talk-dash.py --selftest` — expect the `.art{` split to fail (IndexError or AssertionError).

- [ ] **Step 7: Dashboard — implement**

After `_term_row`, add:

```python
def _art_sub(ev):
    """The item's ASCII sketch, hung off the same tree guide as why/rec and
    emitted LAST so .sub:last-child's corner lands on it. Rendered as
    ui.label runs split by tt_model.art_spans - structure strokes in the
    faint ink, labels in the full ink - never as markup: the art comes out
    of a LOG FILE, and a label's text binding cannot become HTML."""
    art = ev.get("diagram")
    if not art:
        return
    from nicegui import ui
    with ui.element("div").classes("sub"):
        ui.label("art").classes("lb")
        with ui.element("div").classes("art"):
            for chunk, structure in M.art_spans(art):
                lbl = ui.label(chunk)
                if structure:
                    lbl.classes("st")
```

In `_action_row`, after the `for field in ("why", "rec"):` loop (outside it, same indent as the `for`), add:

```python
            _art_sub(ev)
```

In `_task_row`, after the meter block (after the `if text:` line's `_cell(...)`, at the same indent as `with ui.element("div").classes("meter"):`), add:

```python
            _art_sub(ev)
```

- [ ] **Step 8: CSS**

In `bin/tt.css`, after the `.mmd svg{display:block}` rule, add:

```css
/* the item's ASCII sketch: one guided sub-row like why/rec, centered in the
   card per the spec. white-space:pre keeps the art's own geometry - the row's
   inherited overflow-wrap:anywhere must not fold a box border - and the mono
   face is restated because .sub switched to prose. justify-self centers the
   shrink-to-fit box in the 1fr track; min-width:0 + max-width + overflow-x
   mean art wider than the card scrolls inside its own box rather than
   widening the card. Only the theme's own inks: structure strokes recede to
   --ink-3 so the labels read first, and a config palette override recolours
   the art with everything else. */
.art{justify-self:center;min-width:0;max-width:100%;overflow-x:auto;
  font-family:var(--mono);font-size:11px;line-height:1.3;white-space:pre;
  color:var(--ink)}
.art>*{display:inline}
.art .st{color:var(--ink-3)}
```

- [ ] **Step 9: Run the whole suite**

Run: `./test.sh` — expect `all selftests passed`. Commit: `feat(ui): centered, theme-inked ASCII sketch sub-row on actions and jobs`.

- [ ] **Step 10: Skill + README**

In `skill/SKILL.md`, replace the entire `## Diagrams` section (heading and its one paragraph) with:

```markdown
## Sketches and diagrams

An action or task takes an optional ASCII sketch:
`table-talk action "..." --why ... --rec ... --diagram $'┌ logs ┐\n│ fold │\n└ wall ┘'`
— drawn centered under the item on the dashboard in the theme's colours, and
`table-talk progress <id> "..." --diagram "..."` adds or replaces one on an
existing item. Keep a sketch at most 40 columns wide and ~12 lines: it must
fit the narrowest card. Box-drawing and arrows read best (┌─┐ │ → ▲). ASCII
renders in the terminal too, so the same sketch can appear in the reply body.

For a picture too big for a card, record a standalone mermaid diagram —
`table-talk diagram "<mermaid source>" --title "<short name>"` — and point
the user at the dashboard (http://127.0.0.1:8731), which renders it live;
the terminal cannot. Re-recording the same title replaces that diagram.
```

In `README.md` **Use** section, after the `table-talk term ...` line, add:

```
    table-talk task "GPN training" --diagram $'data → train → eval'   # ASCII sketch under the item
```

and in the **Dashboard** wall bullet, after "Progress text is read for a percentage and drawn as a bar;" insert "an attached `--diagram` sketch draws centered under its item in the theme's inks;".

Commit: `docs(skill): ASCII sketches on items; mermaid stays for big pictures`.

- [ ] **Step 11: PR** (title `feat: inline ASCII sketches under actions and jobs`; body summarizes: --diagram flag on action/task/progress, art_spans two-ink theming, centered + card-capped rendering, skill guidance; footer line). STOP after `gh pr create` — the controller merges after review.

---

### Task 2: Intuitive descriptions (`--intuitive`)

**Files:**
- Modify: `bin/table-talk` (`--intuitive` on action/task/progress — `with_extras` already handles it)
- Modify: `bin/tt_model.py` (`weight` counts intuitive text, selftests)
- Modify: `bin/table-talk-dash.py` (int sub-row in `_action_row` and `_task_row`, selftests)
- Modify: `skill/SKILL.md` (extend the Sketches section + the action rule's word counts)
- Modify: `README.md` (extend the example line)

**Interfaces:**
- Consumes: `with_extras` from Task 1 (already copies a non-empty `intuitive` attr — only the argparse flags are missing).
- Produces: event field `intuitive` (str) on `action`/`task` events, rendered as the FIRST guided sub-row labeled `int`.

- [ ] **Step 1: Issue + branch**

```bash
git checkout main && git pull
gh issue create --title "Actions and jobs need a plain-English intuitive line" \
  --body "Actions carry why/rec and jobs carry progress, but neither says plainly what the thing IS. Add --intuitive to action, task and progress: a concise plain-English line — what this is and what is needed — rendered as the first guided sub-row (labeled 'int') above why/rec on actions, and above the sketch on jobs. Same two-register idea as the glossary's intuitive/technical split."
git checkout -b feat/intuitive-descriptions
```

- [ ] **Step 2: Model — failing selftest first**

In `bin/tt_model.py` `selftest()`, after the `weight(art_task)`/done-sketch assertions from Task 1, add:

```python
    int_action = {"a": dict(one_action["a"], intuitive="i" * 220)}
    assert weight(int_action) > weight(one_action), \
        "a long intuitive line is real height, like long why/rec prose"
    int_task = {"a": {"type": "task", "status": "open", "what": "x",
                      "intuitive": "plain", "ts": 1}}
    assert weight(int_task) == 4, "a task's intuitive sub-row costs one unit"
```

(`one_action` is defined earlier in the selftest, before the pack() block — these lines must come after the Task 1 art additions, which follow it.)

Run: `python3 bin/tt_model.py --selftest` — expect the `int_task` assertion to fail (reads 3).

- [ ] **Step 3: Model — implement**

In `weight()`, change the action branch's `chars` line to include intuitive, and the task branch to charge for it:

```python
        if typ == "action":
            chars = (len(ev.get("background", "")) + len(ev.get("why", ""))
                     + len(ev.get("rec", "")) + len(ev.get("intuitive", "")))
            units += 3 + chars // 110
        elif typ == "task":
            units += 2 + (1 if ev.get("intuitive") else 0)
```

(`"intuitive"` is already in `_TEXT_FIELDS`, so the filter sees it with no change.)

Run: `python3 bin/tt_model.py --selftest` — expect `ok`. Commit: `feat(model): intuitive text is real height`.

- [ ] **Step 4: CLI — flags**

Add to the three subparsers (`a`, `t`, `pr`), beside their `--diagram` lines:

```python
    a.add_argument("--intuitive", help="plain-English line: what this is and what is needed")
```
```python
    t.add_argument("--intuitive", help="plain-English line: what this job is doing")
```
```python
    pr.add_argument("--intuitive", help="add or replace the item's plain-English line")
```

No dispatch change — `with_extras` already copies it; the Task 1 selftest already pins that behaviour. Run: `python3 bin/table-talk --selftest` — expect `ok`. Commit: `feat(cli): --intuitive on action, task and progress`.

- [ ] **Step 5: Dashboard — failing selftest first**

In the source-pin section of `selftest()` (after Task 1's art-last pin), add:

```python
    assert code.count('ui.label("int").classes("lb")') == 2, \
        "both actions and jobs hang the plain-English line off the guide, " \
        "labeled the way the --intuitive flag is spelled"
    assert code.index('ui.label("int")') < code.index('for field in ("why", "rec")'), \
        "int reads FIRST: it is the line for someone with no context, and " \
        "why/rec argue a decision that line has to set up"
```

Run: `uv run --script bin/table-talk-dash.py --selftest` — expect the count to read 0.

- [ ] **Step 6: Dashboard — implement**

In `_action_row`, directly BEFORE the `for field in ("why", "rec"):` loop, add:

```python
            if ev.get("intuitive"):
                with ui.element("div").classes("sub"):
                    ui.label("int").classes("lb")
                    _cell(ev["intuitive"], query)
```

In `_task_row`, directly BEFORE the `_art_sub(ev)` call, add:

```python
            if ev.get("intuitive"):
                with ui.element("div").classes("sub"):
                    ui.label("int").classes("lb")
                    _cell(ev["intuitive"], query)
```

(Sub order ends up: actions int → why → rec → art; jobs int → art. `.sub:last-child`'s corner still lands on the last row either way.)

Run: `uv run --script bin/table-talk-dash.py --selftest` — expect `ok`, then `./test.sh` — expect `all selftests passed`. Commit: `feat(ui): plain-English int sub-row on actions and jobs`.

- [ ] **Step 7: Skill + README**

In `skill/SKILL.md`, in the `## Sketches and diagrams` section from Task 1, insert after the first sentence's closing "…existing item.":

```markdown
Both also take `--intuitive "<one plain-English sentence>"` — what this is
and what is needed, for a reader with no context, ≤ 25 words. It renders as
the first sub-row (`int`) above why/rec. Record it whenever the ask or the
job is jargon-heavy; skip it when the headline is already plain.
```

In `README.md`, change the Task 1 example line to:

```
    table-talk task "GPN training" --intuitive "teaching a model to read DNA" --diagram $'data → train → eval'
```

Commit: `docs(skill): when to record --intuitive`.

- [ ] **Step 8: PR** (title `feat: plain-English --intuitive line on actions and jobs`; body: the two-register idea, sub-row placement, weight accounting; footer line). STOP after `gh pr create`.

---

## Self-review notes

- Spec coverage: ASCII below each description → Task 1 (`_art_sub` emitted after why/rec on actions, after meter on jobs). Theme colours → `.art` uses only `--ink`/`--ink-3`/`--mono` tokens. Centered → `justify-self:center`, pinned. No larger than a card → `min-width:0`/`max-width:100%`/`overflow-x:auto` pinned + the skill's ≤40-column rule. Actions get INTUITIVE beside WHY/REC → Task 2 `_action_row`. Jobs get an intuitive sub-row with a sketch to reference → Task 2 `_task_row` int + Task 1 `_art_sub`.
- Type consistency: `with_extras(fields, args)` written in Task 1 with both keys, consumed unchanged in Task 2; `art_spans` returns `[(str, bool)]` and the renderer destructures exactly that; the `diagram` FIELD on action/task events is distinct from the `diagram` event TYPE (standalone mermaid), and `weight`'s new charge is guarded by `typ in ("action", "task")` so the two never collide.
- Selftest ordering: Task 1's model tests sit after the `_random` import inside `selftest()`; Task 2's model tests reference `one_action` (defined earlier) and Task 1's names — both placements stated inline.
