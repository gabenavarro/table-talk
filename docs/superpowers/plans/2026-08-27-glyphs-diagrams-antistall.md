# Glyph Rendering, Mermaid Diagrams & Anti-Stall Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix poorly-rendered symbol/emoji glyphs in the dashboard, make the blink cursor self-explanatory, add a mermaid `diagram` event type (CLI + dashboard + skill), and stop Claude sessions stalling after their closing tables.

**Architecture:** Four independent changes, each its own issue + branch + PR + squash-merge into main, executed in order. All changes follow the repo's selftest idiom: every behavioural claim is pinned by an assertion in the touched file's `selftest()`, and source-level invariants are pinned by string/AST checks on the file's own text.

**Tech Stack:** Python stdlib (CLI, model), NiceGUI 3.16 via PEP 723/uv (dashboard), plain CSS.

**Spec:** The user request (2026-08-27) plus the investigation findings below. No separate spec doc — findings are reproduced here in full where a task depends on them.

## Global Constraints

- CLI (`bin/table-talk`) and model (`bin/tt_model.py`) stay stdlib-only; the dashboard's only dependency stays `nicegui>=3.16,<4`.
- Every value that originates in a session log file is untrusted: props are ASSIGNED (never `.props()` strings), `ui.html` appears exactly once (in `_marked`), no `shell=True` anywhere. The dash selftest enforces these on the source; do not break them.
- `./test.sh` must pass after every task (runs all four selftests).
- Git: never commit to main. Per change: `gh issue create` → branch → TDD commits → `gh pr create` → `gh pr merge --squash --delete-branch`. PR bodies end with the Claude Code attribution footer.
- Conventional commit prefixes as in the existing history (`fix:`, `feat:`, `docs:`, with scope where it helps).

## Investigation findings the tasks depend on

**Fonts (verified on this machine with fontTools cmaps + fc-match):** JetBrains Mono lacks the braille spinner (U+28xx), ▰▱ (U+25B0/1), ◐ (U+25D0), ☀ (U+2600), ☾ (U+263E). Unlisted glyphs fall to system fallback: braille/▰▱/◐ land in Cascadia Code (wrong metrics), ☾ lands in Jomolhari (a Tibetan script font), and ☀ — which carries the Unicode Emoji property — is grabbed by Noto Color Emoji in Chrome. A Nerd Font would NOT help: patched fonts add PUA icons, not these codepoints, and no color emoji (upstream: nerd-fonts discussion #1076). Fix: plug the holes inside the CSS stack with fonts already installed on Fedora — "Adwaita Mono" (covers everything except ☾, mono metrics) then "Noto Sans Symbols 2"/"Noto Sans Symbols" (covers ☾ and the rest, proportional) — after the real text faces, before the generic. Real color emoji (🔴 ✅ 📖) are in none of the added fonts and still reach Noto Color Emoji.

**NiceGUI mermaid (verified in installed nicegui 3.16.0 source):** `ui.mermaid(content: str, config: dict | None = None, *, on_node_click=None)` — bundled mermaid 11.16.1 served locally (no CDN). Default `securityLevel:"strict"` sanitizes labels via DOMPurify, so untrusted log content cannot inject script; pass `config={"securityLevel": "strict"}` explicitly anyway (belt and suspenders — `"loose"` would skip sanitization). Parse errors render mermaid's error graphic client-side; the server never crashes. `useMaxWidth` defaults true → the SVG gets `max-width:<natural>px;width:100%` and scales down inside a narrow card. Theme is fixed at client mount; a per-diagram `%%{init: ...}%%` directive applies per render — but the server cannot know the client's prefers-color-scheme in "system" mode, so one fixed theme (`neutral`, greyscale, readable on both grounds) is used.

**Wedge (verified in transcripts + env):** `cmd_serve` does `os.execv` into `uv run --script table-talk-dash.py` and never returns — a foreground Bash call hangs until the tool timeout, a background task never completes. `pgrep -f "table-talk serve"` is ALWAYS a false negative (exec renames the process), which funnels sessions into launching a second serve. `CLAUDECODE=1` is set in Claude Code Bash sessions and absent from the user's own terminals (which carry other `CLAUDE_CODE_*` vars — match `CLAUDECODE` exactly). No table-talk systemd unit exists. SKILL.md never tells sessions to end the turn after the tables.

---

### Task 1: Symbol-font fallbacks in the CSS stacks

**Files:**
- Modify: `bin/tt.css:33-34` (the `--mono`/`--prose` tokens) plus a comment block
- Modify: `bin/table-talk-dash.py` (selftest assertions near the existing font asserts, ~line 657)

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: nothing later tasks rely on.

- [ ] **Step 1: Issue + branch**

```bash
cd <repo> && git checkout main && git pull
gh issue create --title "Dashboard symbol glyphs fall into scattershot font fallback (☀ renders as color emoji, ☾ as Tibetan-font glyph)" \
  --body "$(cat <<'EOF'
JetBrains Mono lacks the braille spinner, ▰▱, ◐, ☀ and ☾. Unlisted, those fall into system fallback where each lands in a different font: braille/▰▱/◐ in Cascadia Code (wrong metrics), ☾ in Jomolhari (a Tibetan script font), and ☀ — Unicode Emoji property — gets grabbed by Noto Color Emoji, so the theme toggle shows a color-emoji sun beside monochrome ◐/☾.

A Nerd Font does not fix this (verified: patched fonts add PUA icons, not these codepoints, and no color emoji). Fix is CSS-only: list "Adwaita Mono" (mono metrics, covers all but ☾) and "Noto Sans Symbols 2"/"Noto Sans Symbols" (covers ☾) inside the stacks — after the real text faces, before the generic — all already installed on Fedora. Real color emoji in user text still reach Noto Color Emoji.
EOF
)"
git checkout -b fix/symbol-font-fallbacks
```

- [ ] **Step 2: Write the failing selftest**

In `bin/table-talk-dash.py`'s `selftest()`, directly after the line
`assert "ui-monospace" in css and "system-ui" in css, "both faces need a real fallback stack"`, add:

```python
    # The symbol fallbacks: JetBrains Mono lacks braille/▰▱/◐/☀/☾, and unlisted
    # those fall into SYSTEM fallback - ☾ drew a Tibetan script font, and ☀
    # (Unicode Emoji property) drew the color emoji font. An author-listed
    # family beats the browser's implicit emoji fallback, so the stack itself
    # must carry the coverage.
    for face in ("--mono", "--prose"):
        decl = css.split(face + ":")[1].split(";")[0]
        assert '"Noto Sans Symbols 2"' in decl and '"Noto Sans Symbols"' in decl, \
            f"{face} must list the symbol fallbacks, or ☀ falls to the color " \
            "emoji font and ☾ to whatever fontconfig finds first"
    mono_decl = css.split("--mono:")[1].split(";")[0]
    assert mono_decl.index('"JetBrains Mono"') < mono_decl.index('"Adwaita Mono"') \
        < mono_decl.index('"Noto Sans Symbols 2"'), \
        "fallback order is coverage order: the primary face first, then the " \
        "MONO-metric symbol source (spinner and bars sit in mono columns), " \
        "then the proportional Noto pair for what is still missing (☾)"
```

- [ ] **Step 3: Run to verify it fails**

Run: `uv run --script bin/table-talk-dash.py --selftest`
Expected: AssertionError "--mono must list the symbol fallbacks…"

- [ ] **Step 4: The CSS change**

In `bin/tt.css`, replace:

```css
  --mono:"JetBrains Mono",ui-monospace,"SF Mono",Menlo,Consolas,monospace;
  --prose:"Fira Sans",system-ui,-apple-system,"Segoe UI",sans-serif;
```

with:

```css
  /* Symbol fallbacks, both faces: JetBrains Mono has no braille (the spinner),
     ▰▱, ◐, ☀ or ☾. Unlisted, those fall into SYSTEM fallback where each lands
     in a different font - ☾ drew Jomolhari (a Tibetan script font) and ☀,
     which carries the Unicode Emoji property, drew the COLOR emoji font: a
     color sun in the theme toggle beside monochrome ◐/☾. An author-listed
     family beats the browser's implicit emoji fallback, so the stack plugs its
     own holes: Adwaita Mono first (mono metrics - the spinner and bars sit in
     mono columns), the Noto symbol pair for what is still missing (☾). After
     the real text faces, before the generic, so they only ever serve glyphs
     the primaries lack. Real emoji (🔴 ✅) are in none of them and still reach
     the color emoji font. A Nerd Font would not help here: patching adds PUA
     icons, not these codepoints, and no color emoji. */
  --mono:"JetBrains Mono","Adwaita Mono",ui-monospace,"SF Mono",Menlo,Consolas,
    "Noto Sans Symbols 2","Noto Sans Symbols",monospace;
  --prose:"Fira Sans",system-ui,-apple-system,"Segoe UI",
    "Noto Sans Symbols 2","Noto Sans Symbols",sans-serif;
```

- [ ] **Step 5: Run the whole suite**

Run: `./test.sh`
Expected: `all selftests passed`

- [ ] **Step 6: Commit, PR, merge**

```bash
git add bin/tt.css bin/table-talk-dash.py
git commit -m "fix(ui): plug symbol-glyph coverage holes in the font stacks

Closes #<issue>.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
git push -u origin fix/symbol-font-fallbacks
gh pr create --fill-first   # body must state the ☾/☀ mechanism and end with the attribution footer
gh pr merge --squash --delete-branch
```

---

### Task 2: The blink cursor explains itself

**Files:**
- Modify: `bin/table-talk-dash.py` `_action_row` (~line 448) + selftest pin

**Interfaces:** none.

Context: `_action_row(ev, blink, …)` renders `ui.label("▉").classes("cursor")` on exactly one action — the newest open action on the wall — and `.cursor` blinks via `animation:blink 1.1s steps(2,start) infinite` in caret color (green in dark mode). It was circled in a bug report as a mystery artifact; it needs a tooltip.

- [ ] **Step 1: Issue + branch**

```bash
git checkout main && git pull
gh issue create --title "The blink cursor on the newest action reads as a rendering artifact" \
  --body "The ▉ terminal-cursor glyph marks the newest open action, but nothing on the page says so — it was circled in a bug report as a mysterious 'green box that sometimes blinks'. Give it a title tooltip so hovering answers the question in place."
git checkout -b fix/cursor-tooltip
```

- [ ] **Step 2: Failing selftest**

In `selftest()`, after the `assert 'btn.props["data-id"]' …` block (source-pin section at the bottom), add:

```python
    assert 'cur.props["title"]' in code and "newest action waiting on you" in code, \
        "the cursor glyph must explain itself: a green blinking box with no " \
        "tooltip reads as a rendering artifact (it was circled in a bug report)"
```

- [ ] **Step 3: Run to verify it fails**

Run: `uv run --script bin/table-talk-dash.py --selftest` — expect the new assertion to fail.

- [ ] **Step 4: The change**

In `_action_row`, replace:

```python
                if blink:   # exactly one cursor on the page: the newest thing waiting on you
                    ui.label("▉").classes("cursor")
```

with:

```python
                if blink:   # exactly one cursor on the page: the newest thing waiting on you
                    cur = ui.label("▉").classes("cursor")
                    cur.props["title"] = "newest action waiting on you"
```

- [ ] **Step 5: Run `./test.sh`** — expect `all selftests passed`

- [ ] **Step 6: Commit, PR, merge** (same flow as Task 1; branch `fix/cursor-tooltip`, commit `fix(ui): tooltip on the blink cursor — it read as a rendering artifact`)

---

### Task 3: Mermaid diagram events — CLI, model, dashboard, skill

**Files:**
- Modify: `bin/table-talk` (add `add_diagram`, `diagram` subcommand, selftests)
- Modify: `bin/tt_model.py` (`_TEXT_FIELDS`, `weight`, selftests)
- Modify: `bin/table-talk-dash.py` (`diagram_rows`, `_diagram_row`, `MERMAID_INIT`, section in `render_window_body`, selftests)
- Modify: `bin/tt.css` (`.p-mag`, `.id-mag`, `.mmd` rules)
- Modify: `docs/config.example.toml` (document "diagrams" as a collapsed_sections value)
- Modify: `skill/SKILL.md` (record-table row + Diagrams section)
- Modify: `README.md` (Use + Dashboard mentions)

**Interfaces:**
- Produces: event type `"diagram"` with fields `title` (str) and `mermaid` (str), deduped case-insensitively by title within a session file (same contract as `term`). Task 4's SKILL.md text lists `diagram` among the recording commands.

- [ ] **Step 1: Issue + branch**

```bash
git checkout main && git pull
gh issue create --title "Record mermaid diagrams and render them on the dashboard" \
  --body "Claude sessions cannot render mermaid in the terminal, but the dashboard can. Add 'table-talk diagram \"<mermaid source>\" --title <name>' (dedupe by title, like terms, so a diagram is iterated on rather than accumulated), render it in a collapsible 'diagrams' window section via NiceGUI's bundled ui.mermaid (mermaid 11.16.1, offline, securityLevel strict so log content stays sanitized), and teach the skill to record diagrams for concepts a picture explains faster — pointing the user at the dashboard to view them."
git checkout -b feat/diagram-events
```

- [ ] **Step 2: CLI — failing selftest first**

In `bin/table-talk` `selftest()`, after the GUARD term-rejection block, add:

```python
        d1 = add_diagram(f, "Pipeline", "flowchart LR\n  a --> b")
        d2 = add_diagram(f, "pipeline", "flowchart LR\n  a --> c")
        assert d1 == d2, "diagram dedupe is case-insensitive on the title"
        assert fold(f)[d1]["mermaid"].endswith("a --> c"), \
            "re-recording a title REPLACES the source: a diagram is iterated on"
        try:
            update(d1, {"status": "done"})
            raise AssertionError("done on a diagram must be rejected")
        except SystemExit:
            pass
```

Run `python3 bin/table-talk --selftest` — expect NameError `add_diagram`.

- [ ] **Step 3: CLI — implement**

After `add_term`, add (mirror of `add_term` — same lock, same dedupe shape):

```python
def add_diagram(path, title, mermaid):
    """Same title (case-insensitive) in this file -> reuse id, replace the
    source: a diagram is iterated on, never accumulated."""
    with mint_lock():
        state = fold(path)
        existing = next((i for i, e in state.items() if e.get("type") == "diagram"
                         and e.get("title", "").lower() == title.lower()), None)
        i = existing or new_id()
        append(path, {"id": i, "type": "diagram", "title": title,
                      "mermaid": mermaid, "ts": int(time.time())})
    return i
```

In `main()`, after the `term` subparser:

```python
    dg = sub.add_parser("diagram", help="record a mermaid diagram (the dashboard renders it)")
    dg.add_argument("mermaid", help="mermaid source, e.g. $'flowchart LR\\n  a --> b'")
    dg.add_argument("--title", required=True)
    dg.add_argument("--project")
```

and in the dispatch chain, after the `term` branch:

```python
    elif args.cmd == "diagram":
        print(add_diagram(session_file(args.project), args.title, args.mermaid))
```

Run `python3 bin/table-talk --selftest` — expect `ok`. Commit: `feat(cli): diagram events — mermaid source deduped by title`.

- [ ] **Step 4: Model — failing selftest first**

In `bin/tt_model.py` `selftest()`, after the `weight(job) == weight(job2)` assertion, add:

```python
    dia = {"a": {"type": "diagram", "title": "t", "mermaid": "flowchart LR", "ts": 1}}
    assert weight(dia) == 7, \
        "a diagram renders open and tall; the packer must budget for it"
    assert "flowchart" in row_text({"id": "x", "type": "diagram", "title": "T",
                                    "mermaid": "flowchart LR"}), \
        "the filter must see a diagram's source and title"
```

Run `python3 bin/tt_model.py --selftest` — expect the weight assertion to fail (reads 1).

- [ ] **Step 5: Model — implement**

In `_TEXT_FIELDS`, append the two fields:

```python
_TEXT_FIELDS = ("id", "background", "why", "rec", "what", "progress",
                "term", "intuitive", "technical", "title", "mermaid")
```

In `weight()`, after the `elif typ == "task":` branch:

```python
        elif typ == "diagram":
            units += 6      # rendered SVG: roughly an action's height, plus room
```

(Diagrams carry no `status`, so the `done` skip above never eats them; `summarize` counts only actions/tasks, so a diagram — reference material like a term — adds no obligation.)

Run `python3 bin/tt_model.py --selftest` — expect `ok`. Commit: `feat(model): diagrams are searchable and cost packing weight`.

- [ ] **Step 6: Dashboard — failing selftests first**

In `bin/table-talk-dash.py` `selftest()`:

After the `term_rows` assertion, add:

```python
    dst = dict(st, f={"id": "f", "type": "diagram", "title": "Arch",
                      "mermaid": "flowchart LR", "ts": 6},
               g={"id": "g", "type": "diagram", "title": "Flow",
                  "mermaid": "sequenceDiagram", "ts": 7})
    assert [r["id"] for r in diagram_rows(dst)] == ["g", "f"], "diagrams read newest first"
    assert diagram_rows(st) == [], "no diagram events, no rows"
    assert [r["id"] for r in done_rows(dst)] == ["b"], \
        "a diagram is never an obligation: done still spans actions and tasks only"
```

In the source-pin section at the bottom, add:

```python
    assert '"securityLevel": "strict"' in code, \
        "mermaid source comes out of a LOG FILE; strict is what keeps its " \
        "labels sanitized - loose would execute whatever the log carries"
    assert '%%{init:' in code and '"theme": "neutral"' in code, \
        "one fixed mermaid theme, readable on both grounds: in system mode the " \
        "server cannot know the client's prefers-color-scheme, so a per-theme " \
        "render would be a guess"
    assert '"dia": "diagrams" not in collapsed' in code, \
        "diagrams exist to be LOOKED at - the reply points the user here - so " \
        "unlike glossary/done they start open unless the config folds them"
    assert ".p-mag" in css and ".id-mag" in css and ".mmd" in css, \
        "the diagrams section needs its prompt, title-cell and body styles"
    assert ".win-b .row:has(.id-mag)" in css, \
        "a diagram title shares the row grid with 4-hex ids: without its own " \
        "wider column it overflows the 42px id track (same bug .id-gls had)"
```

Run `uv run --script bin/table-talk-dash.py --selftest` — expect NameError `diagram_rows`.

- [ ] **Step 7: Dashboard — implement**

After `term_rows`, add:

```python
def diagram_rows(state):
    return sorted((e for e in state.values() if e.get("type") == "diagram"),
                  key=lambda e: e.get("ts", 0), reverse=True)
```

Near `GUIDES` (module constants), add:

```python
# One fixed mermaid theme: in "system" mode the server cannot know the client's
# prefers-color-scheme, so a per-theme render would be a guess. neutral is
# greyscale and reads on both grounds. Applied as a per-render directive, not
# initialize() config - initialize runs once per client and config after that
# is silently ignored (verified against the bundled mermaid 11.16.1).
MERMAID_INIT = '%%{init: {"theme": "neutral"}}%%\n'
```

After `_term_row`, add:

```python
def _diagram_row(ev, query):
    """A recorded mermaid diagram. The source comes out of a LOG FILE, so it is
    rendered at securityLevel strict - the bundled default, restated here so a
    future config knob cannot silently relax it. A parse error draws mermaid's
    own error graphic client-side; the server never sees it."""
    from nicegui import ui
    with ui.element("div").classes("row" + _dim(ev, query)):
        ui.label(ev.get("title", "")).classes("id id-mag")
        with ui.element("div"):
            ui.mermaid(MERMAID_INIT + str(ev.get("mermaid", "")),
                       config={"securityLevel": "strict"}).classes("mmd")
```

In `render_window_body`: extend the `opened` initialisation to

```python
        opened = container.tt_open = {"gls": "glossary" not in collapsed,
                                      "ok": "done" not in collapsed,
                                      "dia": "diagrams" not in collapsed}
```

and between the jobs loop and the glossary block insert:

```python
        # Diagrams are reference material like the glossary, but they exist to
        # be LOOKED at - the terminal cannot render mermaid, the reply points
        # the user here - so they start open unless the config folds them, and
        # the header only exists when a diagram does: an empty always-there
        # section is noise (same rule as the drawer's context footer).
        dias = diagram_rows(state)
        if dias:
            dia_box = ui.element("div")
            with dia_box:
                for ev in dias:
                    _diagram_row(ev, query)
            _prompt("p-mag", "diagrams", len(dias), toggles=dia_box,
                    opened=opened, key="dia", force=_hits(dias, query))
            dia_box.move(container, -1)
```

- [ ] **Step 8: CSS**

In `bin/tt.css`, after the `.id-gls{min-width:0;overflow-wrap:break-word}` rule, add:

```css
/* the diagrams section: same wider title column as glossary terms, same reason */
.p-mag{color:var(--mag)}
.win-b .row:has(.id-mag){grid-template-columns:96px 1fr}
.id-mag{color:var(--mag);min-width:0;overflow-wrap:break-word}
/* mermaid's own useMaxWidth already caps the SVG at 100% of this box; min-width:0
   is the usual grid-item floor so the box can shrink to the column */
.mmd{min-width:0}
.mmd svg{display:block}
```

- [ ] **Step 9: Run the whole suite**

Run: `./test.sh` — expect `all selftests passed`. Commit: `feat(ui): render diagram events via ui.mermaid in a collapsible section`.

- [ ] **Step 10: Docs + skill**

`docs/config.example.toml`: change the collapsed_sections line to

```toml
collapsed_sections = ["glossary", "done"]  # window sections that start folded ("glossary", "done", "diagrams")
```

`skill/SKILL.md` — in the **Record as you go** table, after the term row, add:

```markdown
| Concept a picture explains faster | `table-talk diagram "<mermaid source>" --title "<short name>"` → prints ID |
```

and after the table's trailing paragraph, add a section:

```markdown
## Diagrams

When a flow, architecture, or dependency graph would land faster as a picture,
record one — mermaid source, e.g.
`table-talk diagram $'flowchart LR\n  logs --> fold --> wall' --title "data flow"`.
You cannot render mermaid in the terminal; the dashboard renders it live, so
point the user there in the reply body: *"diagram 'data flow' is on the
dashboard (http://127.0.0.1:8731)"*. Re-recording the same title replaces the
diagram, so iterate freely. Keep it simple and concise — the handful of nodes
that explain the concept, not a mural.
```

`README.md` — in **Use**, after the `term` line add:

```
    table-talk diagram $'flowchart LR\n  a --> b' --title "data flow"   # dashboard renders it
```

and in the **Dashboard** wall bullet, mention the section: change "over sections for actions, jobs, glossary and done" to "over sections for actions, jobs, diagrams, glossary and done".

Commit: `docs(skill): record diagrams for concepts a picture explains faster`.

- [ ] **Step 11: PR + merge** (branch `feat/diagram-events`; PR title `feat: mermaid diagram events — CLI, dashboard section, skill guidance`)

---

### Task 4: Anti-stall — serve guard + end-the-turn skill text

**Files:**
- Modify: `bin/table-talk` (`serve_refusal`, `--force`, selftests)
- Modify: `skill/SKILL.md` (server section replacing the line-98 bullet)
- Modify: `README.md` (one line in Use)

**Interfaces:**
- Consumes: Task 3's `diagram` command exists (the skill text lists it among recording commands).

- [ ] **Step 1: Issue + branch**

```bash
git checkout main && git pull
gh issue create --title "Claude sessions stall after their closing tables — serve never returns and the skill never says to end the turn" \
  --body "$(cat <<'EOF'
Two mechanisms, both verified:

1. `table-talk serve` os.execv's into `uv run --script table-talk-dash.py` and never returns. A session that runs it in a foreground Bash call hangs until the tool timeout; run_in_background never completes. The natural liveness check `pgrep -f "table-talk serve"` is ALWAYS a false negative (exec renames the process), which is exactly what funnels a helpful session into launching serve. Fix: serve refuses under CLAUDECODE (exactly that var — the user's own terminals carry other CLAUDE_CODE_* vars) unless --force, and hands back the curl liveness check that works.

2. SKILL.md tells sessions to record and print the closing tables but never says the turn ENDS there — an autonomous session can plausibly wait/poll for the answer to the action it just recorded. Fix: explicit "end the turn" text.
EOF
)"
git checkout -b fix/serve-guard-end-turn
```

- [ ] **Step 2: CLI — failing selftest first**

In `bin/table-talk` `selftest()` (before the final `print("ok")`), add:

```python
    # serve_refusal: pure, so both directions are testable without exec'ing uv
    assert serve_refusal({}, False) is None, "a human terminal is never refused"
    assert serve_refusal({"CLAUDE_CODE_SSE_PORT": "1"}, False) is None, \
        "the user's own VS Code terminal carries CLAUDE_CODE_* vars; only " \
        "CLAUDECODE itself marks a Claude session"
    msg = serve_refusal({"CLAUDECODE": "1"}, False)
    assert msg and "curl" in msg and "--force" in msg, \
        "the refusal must hand back the liveness check that actually works " \
        "(pgrep -f 'table-talk serve' never matches: execv renames the process)"
    assert serve_refusal({"CLAUDECODE": "1"}, True) is None, "--force overrides"
```

Run `python3 bin/table-talk --selftest` — expect NameError `serve_refusal`.

- [ ] **Step 3: CLI — implement**

Above `cmd_serve`, add:

```python
def serve_refusal(env, force):
    """Why serve must not start here, or None.

    serve never returns - it BECOMES the dashboard - so a Claude session that
    runs it wedges until its tool timeout. And the obvious liveness check lies:
    pgrep -f "table-talk serve" matches nothing once execv renames the process,
    so a session that checked first still concludes 'not running'. CLAUDECODE
    exactly, never CLAUDE_CODE_*: the user's own VS Code terminal carries
    CLAUDE_CODE_SSE_PORT, and a broad match would lock the human out too.
    """
    if env.get("CLAUDECODE") and not force:
        return ("error: table-talk serve never exits, and this is a Claude Code "
                "session - it would hang until the tool timeout.\n"
                "check instead:  curl -sf -o /dev/null --max-time 2 "
                "http://127.0.0.1:8731/ && echo up || echo down\n"
                "If it is down, ask the user to run `table-talk serve` in their "
                "own terminal (or pass --force to insist).")
    return None
```

Change `cmd_serve` to take and check it:

```python
def cmd_serve(port, force=False):
    if (msg := serve_refusal(os.environ, force)):
        sys.exit(msg)
    dash = Path(__file__).resolve().parent / "table-talk-dash.py"
    ...
```

Add the flag to the subparser and dispatch:

```python
    sv.add_argument("--force", action="store_true",
                    help="start even inside a Claude Code session")
```

```python
    elif args.cmd == "serve":
        cmd_serve(args.port, args.force)
```

Run `python3 bin/table-talk --selftest` — expect `ok`. Commit: `fix(cli): serve refuses inside a Claude session — it never returns`.

- [ ] **Step 4: SKILL.md**

Replace the bullet
`- At session start, mention \`table-talk serve\` once if the dashboard might not be running.`
with
`- At session start, if the dashboard is down (check below), mention \`table-talk serve\` once — never run it yourself.`

and add, after the **Responding to the user** section:

```markdown
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
```

- [ ] **Step 5: README**

In **Use**, change the `table-talk serve           # dashboard` line to
`table-talk serve           # dashboard (run it yourself — it refuses inside a Claude session)`.

- [ ] **Step 6: `./test.sh`** — expect `all selftests passed`

- [ ] **Step 7: Commit docs, PR, merge** (commit `docs(skill): never run serve, end the turn after the tables`; PR title `fix: anti-stall — serve guard and end-the-turn skill contract`)

---

## Self-review notes

- Spec coverage: request item 1 → Task 1 (determination: Nerd Font does not help; findings recorded in issue + CSS comment). Item 2 → Task 2 (the "why" is answered in the reply; the change makes the glyph self-answering). Item 3 → Task 3. Item 4 → Task 4.
- Type consistency: `add_diagram(path, title, mermaid)` matches the CLI dispatch; `diagram_rows`/`_diagram_row` names match between steps 6–7; `"dia"` key matches the source-pin assertion; `.id-mag`/`.p-mag`/`.mmd` match between CSS and selftest pins.
- The dash selftest's existing invariants (one `ui.html`, assigned props, no `shell=`) are untouched by every task: `ui.mermaid` is not `ui.html`, and no new `.props()` strings or subprocess keywords are introduced.
