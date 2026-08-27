# Protocol, Links, Config and Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make table-talk's protocol produce short, decision-first actions; add file/memory links that open in the user's editor; make everything configurable from one TOML file; fix four UI defects; and correct the change-gutter design error.

**Architecture:** Two new stdlib-only modules — `bin/tt_config.py` (TOML load, defaults, validation) and path handling added to `bin/tt_model.py` — keep the security-critical and config logic testable under bare `python3`. `bin/table-talk-dash.py` consumes both. `bin/table-talk` and the JSONL format are **untouched**; document links work by detecting paths already present in free text, so no schema change is needed.

**Tech Stack:** Python 3.12+, stdlib `tomllib`/`subprocess`/`re`/`pathlib`, NiceGUI 3.16. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-27-protocol-and-polish-design.md`

## Global Constraints

- **Never modify `bin/table-talk` or the JSONL event format.** Confirmed by the user this round: the protocol fix is guidance-only.
- **No new dependencies.** TOML via stdlib `tomllib`; YAML is therefore not an option.
- `bin/tt_model.py` and `bin/tt_config.py` import nothing outside the standard library and must run under bare `python3`.
- `bin/table-talk-dash.py` keeps its PEP 723 header and `#!/usr/bin/env -S uv run --script` shebang.
- **Exactly ONE `ui.html(` call** in the dashboard, fed only by `tt_model.marked`. Event data reaches props only via the **dict form** (`el.props["k"] = v`), never an f-string — #63 closed two Critical injection holes here and they stay closed.
- **Never `shell=True`.** Any process launch uses a list argv.
- **The filter DIMS, it never hides.**
- Tests are assert-based selftests, no framework. `test.sh` runs all of them.
- A malformed or missing config file must **never crash the dashboard** — fall back to defaults with one clear stderr warning.
- Commit after every task, using that task's message.

---

### Task 1: `bin/tt_config.py` — TOML config with defaults and validation

**Files:**
- Create: `bin/tt_config.py`
- Modify: `test.sh`

**Interfaces:**
- Produces: `DEFAULTS: dict`, `load(path=None) -> dict`, `valid_colour(s) -> bool`, `selftest()`.

- [ ] **Step 1: Write the failing test**

Create `bin/tt_config.py` with the module docstring, `DEFAULTS`, and this selftest only:

```python
#!/usr/bin/env python3
"""table-talk configuration: one TOML file, stdlib only, and a missing or broken
file must never stop the dashboard from starting."""
import argparse
import os
import tomllib
from pathlib import Path

CONFIG_PATH = Path(os.environ.get("TABLE_TALK_CONFIG")
                   or Path.home() / ".config/table-talk/config.toml")


def selftest():
    import tempfile
    assert valid_colour("#fb4934") and valid_colour("#FFF") and valid_colour("#1d2021ff")
    assert not valid_colour("red"), "only hex is accepted"
    assert not valid_colour("#12"), "too short"
    assert not valid_colour("#12345"), "not a legal hex length"
    assert not valid_colour('#fff;}body{display:none'), "a css injection attempt is not a colour"
    assert not valid_colour(None) and not valid_colour(123)

    with tempfile.TemporaryDirectory() as td:
        missing = Path(td) / "nope.toml"
        assert load(missing) == DEFAULTS, "a missing file yields the defaults verbatim"

        p = Path(td) / "c.toml"
        p.write_text('[server]\nport = 9999\n\n[theme.dark]\nbg = "#000000"\n')
        cfg = load(p)
        assert cfg["server"]["port"] == 9999, "a set key overrides"
        assert cfg["server"]["poll_seconds"] == DEFAULTS["server"]["poll_seconds"], \
            "an unset key keeps its default"
        assert cfg["theme"]["dark"]["bg"] == "#000000"
        assert cfg["theme"]["dark"]["ink"] == DEFAULTS["theme"]["dark"]["ink"], \
            "an unset theme token keeps its default"
        assert load(p) is not DEFAULTS and cfg["server"] is not DEFAULTS["server"], \
            "load must not hand out the shared DEFAULTS dict"

        bad = Path(td) / "bad.toml"
        bad.write_text("[server\nport = 1\n")
        assert load(bad) == DEFAULTS, "a malformed file falls back to defaults"

        evil = Path(td) / "evil.toml"
        evil.write_text('[theme.dark]\nbg = "#fff;}body{display:none}"\nink = "#ebdbb2"\n')
        got = load(evil)
        assert got["theme"]["dark"]["bg"] == DEFAULTS["theme"]["dark"]["bg"], \
            "an invalid colour is dropped, not emitted into the stylesheet"
        assert got["theme"]["dark"]["ink"] == "#ebdbb2", "valid siblings still apply"

        wrong = Path(td) / "wrong.toml"
        wrong.write_text('[server]\nport = "not a number"\n')
        assert load(wrong)["server"]["port"] == DEFAULTS["server"]["port"], \
            "a wrongly-typed value falls back to its default"

        extra = Path(td) / "extra.toml"
        extra.write_text('[nonsense]\nkey = 1\n')
        assert "nonsense" not in load(extra), "unknown sections are ignored, not merged"

        # theme.default is a MODE, not a colour - the colour check must not eat it
        mode = Path(td) / "mode.toml"
        mode.write_text('[theme]\ndefault = "dark"\n')
        assert load(mode)["theme"]["default"] == "dark", \
            "theme.default is a mode name and must survive the colour validator"
    print("ok")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.parse_args()
    selftest()
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python3 bin/tt_config.py --selftest`
Expected: `NameError: name 'valid_colour' is not defined`

- [ ] **Step 3: Implement**

Insert after `CONFIG_PATH`. The theme token lists must match the `:root` block in `bin/tt.css` — read that file and copy the token names rather than inventing them.

```python
import re
import sys

_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")

# Every token the stylesheet's :root block defines. Names copied from bin/tt.css.
_DARK = {"bg": "#1d2021", "surface": "#282828", "surface-2": "#32302f",
         "ink": "#ebdbb2", "ink-2": "#a89984", "ink-3": "#928374",
         "sel": "#665c54", "caret": "#8ec07c",
         "act": "#fb4934", "act-2": "#cc241d", "job": "#83a598", "job-2": "#458588",
         "gls": "#fabd2f", "gls-2": "#d79921", "ok": "#b8bb26", "ok-2": "#98971a",
         "mag": "#d3869b"}
_LIGHT = {"bg": "#e8e6dc", "surface": "#faf9f5", "surface-2": "#f2f0e8",
          "ink": "#141413", "ink-2": "#6f6e68", "ink-3": "#93918a",
          "sel": "#e8e6dc", "caret": "#d97757",
          "act": "#a53a2e", "act-2": "#d97757", "job": "#3668a0", "job-2": "#6a9bcc",
          "gls": "#8a6a10", "gls-2": "#b88a28", "ok": "#4a7038", "ok-2": "#788c5d",
          "mag": "#7a4a82"}

DEFAULTS = {
    "server": {"port": 8731, "poll_seconds": 2.0},
    "ui": {"columns": 0, "drawer_open": True, "filter_debounce_ms": 100,
           "collapsed_sections": ["glossary", "done"]},
    "links": {"open_command": "xdg-open", "extra_roots": []},
    "theme": {"default": "system", "dark": _DARK, "light": _LIGHT},
}


def valid_colour(s):
    """Only a hex colour. A config file is a second route into the stylesheet,
    so it gets the same treatment as any other untrusted input."""
    return isinstance(s, str) and bool(_HEX.match(s))


def _merge(default, override, path=""):
    """Deep-merge override onto a COPY of default. A value of the wrong type, or
    an unknown key, is dropped with a warning rather than propagated."""
    out = {}
    for key, dv in default.items():
        ov = override.get(key) if isinstance(override, dict) else None
        where = f"{path}{key}"
        if isinstance(dv, dict):
            out[key] = _merge(dv, ov if isinstance(ov, dict) else {}, where + ".")
        elif ov is None:
            out[key] = list(dv) if isinstance(dv, list) else dv
        elif where.startswith(("theme.dark.", "theme.light.")) and not valid_colour(ov):
            print(f"warning: config {where}: {ov!r} is not a hex colour, using default",
                  file=sys.stderr)
            out[key] = dv
        elif not isinstance(ov, type(dv)) and not (isinstance(dv, float) and isinstance(ov, int)):
            print(f"warning: config {where}: expected {type(dv).__name__}, using default",
                  file=sys.stderr)
            out[key] = dv
        else:
            out[key] = ov
    return out


def load(path=None):
    """The config, with every unset key falling back to its default. A missing or
    malformed file yields the defaults and one warning - never an exception,
    because a typo in a config file must not stop the dashboard starting."""
    p = Path(path) if path is not None else CONFIG_PATH
    try:
        raw = tomllib.loads(p.read_text())
    except FileNotFoundError:
        raw = {}
    except (tomllib.TOMLDecodeError, OSError, UnicodeDecodeError) as e:
        print(f"warning: config {p}: {e}; using defaults", file=sys.stderr)
        raw = {}
    return _merge(DEFAULTS, raw)
```

`theme.default` is a string, not a colour, so exclude it from the colour check — guard on `where.startswith("theme.dark.") or where.startswith("theme.light.")` instead if the simpler prefix catches it wrongly. Verify against the test.

- [ ] **Step 4: Run it and watch it pass**

Run: `python3 bin/tt_config.py --selftest`
Expected: `ok`

- [ ] **Step 5: Add to `test.sh`**

Insert `python3 "$here/bin/tt_config.py" --selftest` after the `tt_model.py` line.

- [ ] **Step 6: Whole suite**

Run: `./test.sh` — expected: four `ok` lines then `all selftests passed`

- [ ] **Step 7: Commit**

```bash
git add bin/tt_config.py test.sh
git commit -m "feat(config): stdlib TOML config with validated defaults"
```

---

### Task 2: `safe_paths` — detect and confine file paths in free text

Security-critical. This takes a string from a log file and turns it into something the user can click to launch a process, so confinement is the whole feature.

**Files:**
- Modify: `bin/tt_model.py`

**Interfaces:**
- Produces: `path_spans(text: str, roots: list[Path]) -> list[tuple[int, int, str]]` — non-overlapping `(start, end, resolved_path)` for path-shaped substrings that resolve to an existing file **inside** one of `roots`.

- [ ] **Step 1: Write the failing test**

Add to `tt_model.selftest()` before `print("ok")`:

```python
    import tempfile as _tf
    with _tf.TemporaryDirectory() as _td:
        root = Path(_td) / "proj"
        (root / "docs").mkdir(parents=True)
        real = root / "docs" / "notes.md"
        real.write_text("x")
        (root / "CLAUDE.md").write_text("x")
        outside = Path(_td) / "secret.txt"
        outside.write_text("x")
        roots = [root]

        def spans(t):
            return [s[2] for s in path_spans(t, roots)]

        assert spans(f"see {real} for detail") == [str(real)], "an existing file is found"
        assert spans("see docs/notes.md") == [], "a relative path with no root anchor is skipped"
        assert spans(f"{root}/docs/missing.md") == [], "a path that does not exist is not a link"
        assert spans(f"{outside}") == [], "a real file OUTSIDE the roots is never linked"
        assert spans(f"{root}/../{outside.name}") == [], "traversal out of the root is refused"
        assert spans("/etc/passwd") == [], "an absolute path outside the roots is refused"
        assert spans(f"a {real} b {root/'CLAUDE.md'} c") == [str(real), str(root / "CLAUDE.md")], \
            "several paths in one string, in order"
        assert spans(f"({real})") == [str(real)], "surrounding punctuation is trimmed"
        assert spans(f"{real}.") == [str(real)], "a trailing sentence period is not part of the path"
        assert spans("no paths here at all") == []
        assert spans("") == [] and spans(None) == []

        link = root / "link.md"
        link.symlink_to(outside)
        assert spans(str(link)) == [], "a symlink pointing outside the roots is refused"

        weird = root / "has space.md"
        weird.write_text("x")
        assert spans(f"{weird}") == [], "a path containing a space is not detected (accepted limit)"

        sp = path_spans(f"see {real} ok", roots)
        assert len(sp) == 1 and f"see {real} ok"[sp[0][0]:sp[0][1]] == str(real), \
            "the span must index the ORIGINAL string exactly"
```

- [ ] **Step 2: Run it and watch it fail**

Run: `python3 bin/tt_model.py --selftest`
Expected: `NameError: name 'path_spans' is not defined`

- [ ] **Step 3: Implement**

Add after `marked`:

```python
_PATHISH = re.compile(r"[^\s'\"<>()\[\]]*/[^\s'\"<>()\[\]]*")


def path_spans(text, roots):
    """Non-overlapping (start, end, resolved) for path-shaped substrings that
    resolve to an existing FILE inside one of `roots`.

    Existence plus confinement is the whole filter: prose is full of slashes, and
    a token that does not resolve to a real file under a root we already trust is
    not rendered as a link at all. Resolution happens BEFORE the containment
    check so a symlink cannot escape, and traversal (`..`) collapses first.

    Paths containing spaces are not detected. That is an accepted limit - the
    alternative is guessing where a filename ends inside a sentence.
    """
    if not text:
        return []
    resolved_roots = []
    for r in roots or ():
        try:
            resolved_roots.append(Path(r).resolve(strict=True))
        except (OSError, RuntimeError):
            pass
    if not resolved_roots:
        return []
    out = []
    for m in _PATHISH.finditer(str(text)):
        raw = m.group(0).rstrip(".,;:!?")
        if not raw:
            continue
        try:
            p = Path(raw).resolve(strict=True)
        except (OSError, RuntimeError, ValueError):
            continue
        if not p.is_file():
            continue
        if not any(p == root or root in p.parents for root in resolved_roots):
            continue
        out.append((m.start(), m.start() + len(raw), str(p)))
    return out
```

- [ ] **Step 4: Run it and watch it pass**

Run: `python3 bin/tt_model.py --selftest && ./test.sh`
Expected: `ok`, then `all selftests passed`

- [ ] **Step 5: Commit**

```bash
git add bin/tt_model.py
git commit -m "feat(model): detect and confine file paths found in free text"
```

---

### Task 3: Wire the config into the dashboard

**Files:** Modify `bin/table-talk-dash.py`, `bin/table-talk`

- [ ] **Step 1** Add a selftest asserting the emitted theme override block contains only validated hex values and that a config with no `[theme]` section emits no override block at all.
- [ ] **Step 2** Watch it fail.
- [ ] **Step 3** Implement: `import tt_config`; call `tt_config.load()` once in `main()`. Apply `server.port` (as the default when `--port` is not given — an explicit flag still wins), `server.poll_seconds` to the `ui.timer`, `ui.columns` as the stored-cols default, `ui.drawer_open`, `ui.filter_debounce_ms` to the filter input, and `theme.default` as the initial mode. Emit theme overrides as a second `<style>` block after `tt.css`, containing only `:root{--k:v;…}` and `body.body--dark{…}` for keys that differ from the default and pass `valid_colour`. **Only `bin/table-talk`'s `cmd_serve` may need the port default; do not change any other CLI behaviour.**
- [ ] **Step 4** Verify: run with a temp config setting a wild `bg`, screenshot, assert the computed body background matches. Run with a malformed config and assert the dashboard still starts.
- [ ] **Step 5** Commit: `feat(ui): drive the dashboard from the config file`

---

### Task 4: Render path links and open them in the user's editor

**Files:** Modify `bin/table-talk-dash.py`

- [ ] **Step 1** Selftest: assert the open handler builds a **list** argv (never a string), refuses a path outside the roots, and that the roots list is the data dir plus the cwd project dir plus any `links.extra_roots`.
- [ ] **Step 2** Watch it fail.
- [ ] **Step 3** Implement. Render a cell by walking `M.path_spans` and emitting alternating `ui.label` text runs and clickable `ui.element("button")` link runs — the same escape-after-split shape `marked` uses, so text is never markup. The path reaches the button via `btn.props["data-path"] = p` (**dict form**). On click, `subprocess.run([cfg["links"]["open_command"], path], check=False)` — **never `shell=True`**, and re-validate confinement server-side on click rather than trusting the click payload.
- [ ] **Step 4** Verify in a browser: a real path renders as a link and a hostile one does not; clicking runs the command with the right argv (stub the command to a script that logs its argv); a path with a shell metacharacter reaches the argv intact and unexecuted.
- [ ] **Step 5** Commit: `feat(ui): open file paths from the dashboard in the configured editor`

---

### Task 5: Link CLAUDE.md and session memory from the drawer

**Files:** Modify `bin/table-talk-dash.py`

- [ ] **Step 1** Selftest for the discovery helper: walking up from a directory finds the nearest `CLAUDE.md`; returns `None` when there is none; never walks above the home directory.
- [ ] **Step 2** Watch it fail.
- [ ] **Step 3** Implement a drawer footer section listing whichever of these exist: the nearest `CLAUDE.md`, `~/.claude/CLAUDE.md`, and the session memory directory for the project. Reuse Task 4's link button.
- [ ] **Step 4** Verify: with and without each file present; the footer must not appear empty-but-present when nothing exists.
- [ ] **Step 5** Commit: `feat(ui): link CLAUDE.md and session memory from the drawer`

---

### Task 6: The four UI fixes

**Files:** Modify `bin/tt.css`, `bin/table-talk-dash.py`

- [ ] **Step 1** Add selftests pinning what CSS can pin: the hover rule exists for both themes, a single-column media query exists, and no cell rule sets `white-space: nowrap` on a prose cell.
- [ ] **Step 2** Watch them fail.
- [ ] **Step 3** Implement all four:
  1. **Hover direction.** Dark mode must *darken* on hover, light mode lighten. Today both use `--sel`. Introduce a `--hover` token per theme.
  2. **Wrapping.** Every prose cell wraps at the card edge: `overflow-wrap:anywhere` and `min-width:0` on the grid children that need it. No horizontal overflow at any width.
  3. **Single column when narrow.** The stored `cols` preference is a **maximum**, not a mandate — clamp to 1 below roughly 900px of wall width. Do this where the packer picks its column count, so the layout key changes and the wall re-packs.
  4. **Continuous tree guide.** Replace the per-row `├─`/`└─` glyph column with a continuous vertical rule spanning the whole sub-line block, keeping a corner glyph at the last line. The current break appears whenever `why` wraps past one line.
- [ ] **Step 4** Verify by measuring at 700, 900, 1280 and 1920: no horizontal page scroll at any width; one column below the breakpoint and the stored preference honoured above it; text contrast against the **hover** background ≥ 4.5:1 in both themes; and with a deliberately long three-line `why`, assert the guide is continuous — no vertical gap between the `why` and `rec` rows.
- [ ] **Step 5** Commit: `fix(ui): hover contrast, wrapping, narrow-width columns, unbroken tree guides`

---

### Task 7: Change gutters clear on interaction, not visibility

**Files:** Modify `bin/table-talk-dash.py`

- [ ] **Step 1** Selftest: the injected JS registers click, keydown and scroll listeners, not only `visibilitychange`.
- [ ] **Step 2** Watch it fail.
- [ ] **Step 3** Implement. Any click, keypress or scroll marks what is currently shown as seen; returning to a backgrounded tab still counts as a secondary signal. Keep the per-window `seen_at` watermark. Update the comment explaining *why* visibility alone was insufficient, so nobody restores it.
- [ ] **Step 4** Verify: with the tab visible and untouched throughout, append an event and assert the gutter **persists** across several polls; then click anywhere and assert it clears. This is the case the previous implementation failed.
- [ ] **Step 5** Commit: `fix(ui): clear change gutters on interaction, not tab visibility`

---

### Task 8: Rewrite `skill/SKILL.md`

The most important task in this plan. The CLI does not change, so the guidance carries the whole burden.

**Files:** Modify `skill/SKILL.md`

- [ ] **Step 1: Rename** every occurrence of `Gabriel` to `the user` / `you` as the grammar requires, including the frontmatter `description`. No named person may remain.

- [ ] **Step 2: Rewrite the action guidance so it forces brevity.** It must contain, in this order:
  - The rule: **the first sentence of an action is the ask, and nothing else.** One sentence, a question or a choice between named options, ≤ 25 words. Detail follows after an em dash in the same cell, or goes in the reply body.
  - The reply table's first column is headed **"What I need"**, not "Background".
  - **A real before/after**, using this project's own failure verbatim from the spec (`docs/superpowers/specs/2026-08-27-protocol-and-polish-design.md`) — the 80-word action whose ask never appears. Show the rewrite beside it.
  - **A red-flag table** in the house style — the rationalisations that produce a bad action ("the context is genuinely complicated", "they need to know how I got here", "the recommendation explains it") next to what to do instead.
  - **Why and Rec attach to the ask:** Why states the consequence of getting *this decision* wrong; Rec names one option and commits to it.
  - **A self-check:** if the first sentence does not end in a question mark or name a choice, rewrite it before recording.

- [ ] **Step 3: Bring the dashboard description up to date** — the drawer and project grouping, marks/zoom/fold/scope, the keyboard layer, dim-not-hide filtering with highlighting, change gutters, clickable ids and paths, and the config file location. Keep it to a short section; the skill is a protocol, not a manual.

- [ ] **Step 4: Verify by using it.** Take three of the long actions this project actually recorded (`table-talk show table-talk`), rewrite each under the new rules, and check every rewrite passes the self-check. Put the three rewrites in the commit message body as evidence.

- [ ] **Step 5: Commit**

```bash
git add skill/SKILL.md
git commit -m "docs(skill): decision-first actions, no named user, current dashboard"
```

---

### Task 9: README and config example

**Files:** Modify `README.md`; create `docs/config.example.toml`

- [ ] **Step 1** Write `docs/config.example.toml` — every key with its default and a one-line comment. Verify it loads: `TABLE_TALK_CONFIG=docs/config.example.toml python3 -c "import sys; sys.path.insert(0,'bin'); import tt_config; assert tt_config.load() == tt_config.DEFAULTS"` — the example must be exactly the defaults, or the comment lies.
- [ ] **Step 2** README: a short Configuration section pointing at the example, and a line about clickable paths opening in your editor.
- [ ] **Step 3** Run `./test.sh`.
- [ ] **Step 4** Commit: `docs: configuration example and README section`

---

## Self-review notes

- **Spec coverage.** Protocol rewrite → Task 8. Rename → Task 8 step 1. Skill freshness → Task 8 step 3. Doc links → Tasks 2, 4. Memory/CLAUDE.md links → Task 5. Config → Tasks 1, 3, 9. UI fixes → Task 6. Gutter correction → Task 7.
- **Ordering is a dependency chain**: Task 3 needs Task 1; Task 4 needs Task 2; Task 5 needs Task 4. Tasks 6, 7 and 8 are independent and may be reordered.
- **Deliberately not done:** no CLI change (user's decision), no YAML (needs a dependency), no editing state from the browser.
- **Security surface this round:** Task 2 confines paths, Task 4 launches a process, Task 1 validates colours into a stylesheet. All three get hostile-input assertions in their selftests, and Task 4's confinement is re-checked server-side on click rather than trusted from the payload.
