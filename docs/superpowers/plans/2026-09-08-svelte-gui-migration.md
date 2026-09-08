# Svelte GUI Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `bin/table-talk-dash.py` (3257 lines of NiceGUI) with a Svelte 5 front end served by a stdlib-Python brain, and give table-talk its own native window, without editing `bin/tt_model.py`, `bin/tt_config.py` or `skill/SKILL.md`.

**Architecture:** Two processes, one URL. Python keeps the model: `tt_model.fold_cached` folds the JSONL exactly as today, `bin/tt_wall.py` turns that into a fully-resolved JSON **frame** (rows already split into spans, flags computed, columns already packed), and `bin/tt_serve.py` pushes frames over stdlib SSE and takes **intents** back over `POST /do`. A Svelte 5 view maps frames onto elements; a ~120-line Tauri 2 webview points at the same `http://127.0.0.1:8731/`, and with no shell binary installed the same URL opens in the default browser. Nothing about the log format, the CLI or the skill changes.

**Tech Stack:** Python 3.11 stdlib (`http.server`, `json`, `secrets`, `hashlib`), Svelte 5 + Vite (build time only, output committed to `bin/web/`), mermaid 11.16.1, `node --test`, Playwright (CI only), Tauri 2 + Rust (optional binary only).

**Spec:** `docs/superpowers/specs/2026-09-08-svelte-gui-design.md`

## Global Constraints

Every task's requirements implicitly include this section.

- **Not in scope, not negotiable:** the JSONL log format, `bin/table-talk`'s CLI surface, and `skill/SKILL.md`. Claude sessions keep writing through the CLI exactly as they do today.
- `curl -sf "$(table-talk url)"` keeps answering 200 — `skill/SKILL.md:222` hard-codes that liveness check. `GET /` is token-free forever.
- `bin/tt_model.py` and `bin/tt_config.py` are **not edited by any task before phase 6**. Their selftests keep running unchanged; that is how the model's pins are carried. Phase 6 removes exactly one thing from `tt_model.py` — `marked()`, the HTML wrapper, and its property test — because Svelte escapes text nodes structurally and the bug it defends against cannot occur. `parts()` stays and keeps every pin. `tt_config.py` is never edited.
- **No config key is added, removed or re-typed.** Every key in `tt_config.DEFAULTS` keeps its default, its type and its validation.
- Runtime dependencies of the whole product at the end: **`python3 >= 3.11` and a webview or a browser.** No uv, no NiceGUI, no Node, no Bun, no Rust at runtime.
- `bin/table-talk` keeps its 3.10 promise: **no `tt_config` import at module scope**, still covered by the `cli-oldest-python` CI job.
- `bin/tt_wall.py` and `bin/tt_serve.py` each carry a PEP 723 header declaring `requires-python = ">=3.11"` and **no** dependencies, so `uv run --script` still works on a system-Python-3.10 box.
- Every new Python file has a `--selftest`. `./test.sh` stays a dependency-free bash script that is green on Linux with nothing installed.
- Launching a process is always an argv **list**, never `shell=True`; an AST scan bans any `shell=` keyword anywhere in `bin/tt_serve.py`.
- **Absolute timestamps only in the frame.** A selftest asserts the serialised frame carries no key matching `ago|since|_ago` and no value matching `\d+[smhd] ago`. `ago`/`hm`/the clock/`live_delay` render client-side.
- `{@html}` appears in exactly one place in `ui/src` — `Diagram.svelte`. CI greps for exactly one occurrence.
- Linux **and** macOS both work. Windows is out of scope (WSL only), as today.
- The frame carries `"v":1`; the view refuses a version it does not know and shows "update table-talk".
- `/do` is gated by a token (32 bytes from `secrets.token_urlsafe`, written to `DATA_DIR/.ui/token` mode 0600, inlined into `index.html`) **and** an `Origin` check.
- `bin/web/` is committed build output; CI runs `git diff --exit-code -- bin/web`. `tests/frames.json` is committed; CI runs the dump and `git diff --exit-code -- tests/frames.json`.
- Node ≥ 20 is needed only by `ui/build.sh`; Rust only by `shell/build.sh`. Neither is a runtime dependency.
- **Every phase ends with a working product.** `bin/table-talk-dash.py` is not edited by any task before phase 6; it is the kill-switch, reachable as `table-talk serve --legacy` from Task 30 onward.

## Deviations from the spec, and why

Five places where this plan is more specific than the spec, each a refinement rather than a change of design. An executor should follow the plan.

1. **Two SSE event types.** `event: frame` carries a full frame; `event: tick` carries `{"t","polls_ok","spin","last_ok","stale"}` (~60 bytes) on a **skipped** tick. The spec puts `t`/`polls_ok`/`spin`/`last_ok` inside the frame *and* skips unchanged frames; those cannot both hold, because the spinner advances every successful poll. Splitting them keeps the spinner honest, keeps `Wall.frame()` a pure function of the files (which is what makes skip-stability testable), and keeps the on-the-wire payload identical to spec §3.3.
2. **`tt.css` is served from `bin/tt.css`, not copied into `bin/web/`.** The static whitelist is a five-name **map** to paths, so `tt.css` has exactly one copy in the repo and `tt_config.selftest`'s cross-check keeps pointing at the file the browser actually loads.
3. **The lock helpers live in `bin/tt_serve.py`** (`claim`/`release`), imported lazily by `bin/table-talk`'s `cmd_gui` — the same lazy-import idiom `dashboard_url()` already uses to keep the CLI 3.10-clean. One implementation, owned by the process that must release it.
4. **`serve` flips to the brain in phase 4**, with `serve --legacy` as the kill-switch that still execs `table-talk-dash.py` under `uv`. That is what spec §3.7 phase 4 ships ("two dashboards at parity, the new one on the configured port").
5. **An infinite validator bound serialises as `null`.** `_RANGES["server.poll_seconds"]` is `(0.2, float("inf"))`, and `json.dumps(float("inf"))` emits `Infinity`, which `JSON.parse` rejects. `Settings.svelte` reads a `null` bound as "no bound".

---

## File Structure

**Created**

| file | responsibility |
|---|---|
| `bin/tt_wall.py` | The brain's view layer: `Store` (persisted shared UI state), `Wall` (per-connection view state), `KEYMAP`, `frame()`, every portable helper lifted out of `dash.py`, `--selftest`, `--once`, `--dump-fixtures`. |
| `bin/tt_serve.py` | The boundary: `ThreadingHTTPServer`, five-name static whitelist, `/state` SSE, `/do` intents, `/themes.css`, the token, the `Origin` check, `.ui/gui.lock`, in-place port rebind, `--selftest`. |
| `bin/web/index.html`, `app.js`, `app.css`, `mermaid.min.js` | Committed build output. Never hand-edited. |
| `ui/package.json`, `ui/vite.config.js`, `ui/build.sh` | Svelte 5 + Vite build, output pinned to unhashed names. |
| `ui/src/main.js`, `frame.svelte.js`, `fmt.js`, `App.svelte`, `Wall.svelte`, `Window.svelte`, `Section.svelte`, `Row.svelte`, `Spans.svelte`, `Meter.svelte`, `Reply.svelte`, `Diagram.svelte`, `Drawer.svelte`, `Statusline.svelte`, `Settings.svelte`, `Keys.svelte` | The view. One responsibility each, exactly as spec §3.2 lists them. |
| `ui/test/fmt.test.mjs` | `node --test` over `fmt.js` — the only client-side logic that can break. |
| `ui/e2e.mjs` | One Playwright script, no test framework: spawns the brain against `docs/demo` and drives the page. Grown task by task through phases 3–4. |
| `ui/gate/gate.html` | The phase-0 webview gate: `bin/tt.css`, the glyph set, a mermaid diagram, a `steps(2,start)` blink, a negative-`animation-delay` bar and a clipboard button, in one static page. |
| `docs/webview-gate.md` | What phase 0 actually saw, per browser and version. Evidence, not assertion. |
| `tests/frames.json` | Golden frames from `docs/demo` at a fixed clock. |
| `shell/src-tauri/src/main.rs`, `Cargo.toml`, `tauri.conf.json`, `icons/` | The window. ~120 lines of Rust, no IPC, no commands. |
| `shell/build.sh` | `cargo tauri build`. |
| `.github/workflows/release.yml` | Two-runner matrix building `table-talk-gui`. |

**Modified**

| file | change |
|---|---|
| `bin/table-talk` | `+ state` (SUPPRESSed), `+ gui`, `serve --legacy`, then `serve` repointed at the brain; `shell=` AST ban added to its selftest. |
| `bin/tt.css` | Phase 6 only: NiceGUI-specific rules trimmed. |
| `test.sh` | `+ tt_wall`, `+ tt_serve`, optional `node --test`; `uv` line removed in phase 6. |
| `.github/workflows/test.yml` | `+ ui`, `+ ui-macos` jobs; `setup-uv` removed in phase 6. |
| `install.sh` | A note about the optional binary (Task 33); the `chmod` list loses `table-talk-dash.py` in Task 34, **in the same commit as the deletion** — the script runs under `set -e`, so a `chmod` on a missing path aborts the install before the CLI is even symlinked. |
| `README.md`, `docs/config.example.toml` | Dashboard/Install/Themes sections; the restart note removed. |
| `.gitignore` | `+ ui/node_modules/`, `+ shell/src-tauri/target/`. |

**Deleted (phase 6 only)**

`bin/table-talk-dash.py` (3257 lines).

---

## Phase 0 — the webview gate

### Task 1: The webview gate

**Files:**
- Create: `ui/gate/gate.html`
- Create: `docs/webview-gate.md`

**Interfaces:**
- Consumes: `bin/tt.css` (unchanged, loaded by `<link>`), `bin/themes.json` (unchanged).
- Produces: a go/no-go answer recorded in `docs/webview-gate.md`. **If WebKitGTK mangles `tt.css`, Tasks 31–33 (phase 5) are dropped** and the browser product is the answer; every other task is unaffected.

- [ ] **Step 1: Write the gate page**

Create `ui/gate/gate.html`. It links the real stylesheet — no copy — so what renders is what ships.

```html
<!doctype html>
<meta charset="utf-8">
<title>table-talk webview gate</title>
<link rel="stylesheet" href="../../bin/tt.css">
<style>
  body { padding: 16px; font-family: var(--mono); background: var(--bg); color: var(--ink); }
  .gate-h { color: var(--ink-3); margin: 18px 0 6px; }
  .gate-glyphs { font-size: 14px; letter-spacing: 2px; }
</style>
<h3 class="gate-h">1. glyph coverage — every glyph below must render, none as tofu</h3>
<div class="gate-glyphs">─│┌┐└┘├┤┬┴┼█░▓▉▰▱❯▾▸◉●▶⏸◐○⚠✕⚙⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏</div>
<h3 class="gate-h">2. bell blink — steps(2,start), a hard on/off, never a fade</h3>
<div class="win cur"><div class="win-t"><span class="bell">!</span><span>project</span></div></div>
<h3 class="gate-h">3. progress bar pulse — starts partway through, finishes on its own</h3>
<div class="meter"><span class="bar live" style="animation-delay:-120s">████████░░░░░░</span>
  <span class="pct">57%</span><span class="as-of">as of 14:03</span></div>
<h3 class="gate-h">4. indeterminate sweep — five cells, staggered</h3>
<div class="meter"><span class="scan"><i>▓</i><i>▓</i><i>▓</i><i>▓</i><i>▓</i></span></div>
<h3 class="gate-h">5. sub-row tree guide — one continuous arm with a corner, no gaps</h3>
<div class="row"><div class="sub"><span class="k">int</span><span>reads first</span></div>
  <div class="sub"><span class="k">why</span><span>a why long enough to wrap onto a second line so the guide has to bridge it</span></div>
  <div class="sub last"><span class="k">rec</span><span>the corner lands here</span></div></div>
<h3 class="gate-h">6. mermaid — must draw, and must pick up tt.css token colours</h3>
<div class="mmd" id="mmd"></div>
<h3 class="gate-h">7. clipboard — must resolve, not reject</h3>
<button id="clip">copy "gate"</button> <span id="clipout"></span>
<script src="../../bin/web/mermaid.min.js"></script>
<script>
  const SRC = '%%{init: {"theme": "base", "themeVariables": {"fontFamily": '
    + '"JetBrains Mono, Adwaita Mono, SF Mono, Menlo, Consolas, monospace", '
    + '"fontSize": "12px"}}}%%\n'
    + 'flowchart TD\n  A[fold] --> B{sig changed?}\n  B -->|no| C[emit nothing]\n  B -->|yes| D[push frame]\n';
  mermaid.initialize({ startOnLoad: false, securityLevel: "strict" });
  mermaid.render("g1", SRC).then(r => document.getElementById("mmd").innerHTML = r.svg);
  document.getElementById("clip").onclick = () =>
    navigator.clipboard.writeText("gate")
      .then(() => document.getElementById("clipout").textContent = "resolved ✓")
      .catch(e => document.getElementById("clipout").textContent = "REJECTED: " + e);
</script>
```

- [ ] **Step 2: Get a mermaid build to point at**

Run: `mkdir -p bin/web && curl -sSfL https://cdn.jsdelivr.net/npm/mermaid@11.16.1/dist/mermaid.min.js -o bin/web/mermaid.min.js`
Expected: a ~1.1 MB file. (Task 17 replaces this with the copy `ui/build.sh` takes out of `node_modules`; the version is the same 11.16.1 the current dashboard pins.)

- [ ] **Step 3: Run the gate on Linux**

Serve it over loopback HTTP — **never `file://`**. Check 7 is `navigator.clipboard`,
which is gated on secure-context status: `http://127.0.0.1` is a secure context by
spec, `file://` is implementation-defined and historically inconsistent in WebKit. A
FAIL here drops a whole phase, so the origin the gate is tested under has to be the
origin the shipped app runs under (Task 31 loads `http://127.0.0.1:<port>/`).

Run, from the repo root, in one terminal:

```bash
python3 -m http.server --bind 127.0.0.1 8765
```

and in another: `epiphany "http://127.0.0.1:8765/ui/gate/gate.html"` (Fedora/Wayland,
the maintainer's box). The page's `../../bin/tt.css` and `../../bin/web/mermaid.min.js`
resolve to `/bin/…` because the server's root is the repo root.
Expected: all seven sections behave. Note the Epiphany and WebKitGTK versions from `epiphany --version` and `rpm -q webkit2gtk4.1`.

- [ ] **Step 4: Run the gate on macOS**

Run, on a macOS 14+ machine, the same `python3 -m http.server --bind 127.0.0.1 8765`
from the repo root, then: `open -a Safari "http://127.0.0.1:8765/ui/gate/gate.html"`
Expected: all seven sections behave. Note the macOS and Safari versions.

- [ ] **Step 5: Record what was actually seen**

Create `docs/webview-gate.md` with one row per check per browser, filled in from Steps 3–4 — the exact versions, and PASS/FAIL with a one-line note for each of the seven sections. A FAIL on §1, §5 or §6 in Epiphany means **phase 5 is dropped**: say so in the file and in the PR, and stop after Task 30.

- [ ] **Step 6: Commit**

```bash
git add ui/gate/gate.html docs/webview-gate.md
git commit -m "test(ui): a webview gate for tt.css, the glyph set and mermaid"
```

---

## Phase 1 — the frame

Eleven tasks build `bin/tt_wall.py`. `dash.py` is not touched. The phase ships `table-talk state --once`, a documented JSON state dump useful on its own.

### Task 2: `tt_wall.Store` — persisted shared UI state

**Files:**
- Create: `bin/tt_wall.py`
- Test: `bin/tt_wall.py` (`selftest()`, run with `--selftest` — the repo's own pattern; there is no test framework)

**Interfaces:**
- Consumes: `tt_model.DATA_DIR` (unchanged).
- Produces: `Store(path=None, clock=time.time)` with `.get(key, default)`, `.put(key, value)`, `.flush()`, and the class attribute `Store.KEYS: tuple[str, ...]` naming the thirteen persisted keys. Written atomically to `DATA_DIR/.ui/wall.json`, debounced to at most one write a second.

- [ ] **Step 1: Write the failing test**

Create `bin/tt_wall.py` with the header, imports and a `selftest()` holding only this:

```python
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""The wall's view layer: the frame the GUI renders, with no UI toolkit in it.

Everything here used to live inside bin/table-talk-dash.py's poll() and row
builders. Lifted out with every ui.* call removed, it is a pure function from
(clock, folded states) to one JSON document - which is what makes it testable
without a browser and skippable when nothing changed.
"""
import argparse, json, os, re, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tt_model as M
import tt_config


def selftest():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / ".ui" / "wall.json"
        clock = [1000.0]
        s = Store(p, clock=lambda: clock[0])
        assert s.get("marks", []) == [], "an absent file reads as defaults"
        s.put("marks", ["a"])
        assert json.loads(p.read_text())["marks"] == ["a"], "the first put writes through"
        s.put("marks", ["a", "b"])
        assert json.loads(p.read_text())["marks"] == ["a"], "a second put inside a second is debounced"
        clock[0] += 1.5
        s.put("folds", ["c"])
        got = json.loads(p.read_text())
        assert got["marks"] == ["a", "b"] and got["folds"] == ["c"], "the next write flushes everything"
        s.put("folds", ["c"])
        assert s._dirty is False, "putting an unchanged value is not a write"
        p.write_text("{ not json")
        assert Store(p).get("marks", []) == [], "a corrupt store reads as defaults, never raises"
        assert len(Store.KEYS) == 13 and "seen" in Store.KEYS, "the thirteen persisted keys"
    print("tt_wall selftest passed")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        selftest()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 bin/tt_wall.py --selftest`
Expected: FAIL with `NameError: name 'Store' is not defined`

- [ ] **Step 3: Write the implementation**

Insert above `selftest()`:

```python
class Store:
    """The shared, persisted UI state - app.storage.general's replacement.

    One JSON file beside the DATA (never beside the launch directory), written
    with os.replace so a crash cannot leave half a file, and debounced to at
    most one write a second: every intent writes, and a wall dragged through
    marks and folds must not become a syscall per keystroke. The `tt.` key
    prefix is dropped - it existed only to share a namespace with NiceGUI.
    """
    KEYS = ("theme", "marks", "folds", "groups_folded", "zoomed", "scope",
            "needs_me", "current", "cols", "sort", "drawer_open", "merged", "seen")

    def __init__(self, path=None, clock=time.time):
        self.path = Path(path) if path else M.DATA_DIR / ".ui" / "wall.json"
        self._clock, self._dirty, self._last = clock, False, 0.0
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            data = {}
        self._data = data if isinstance(data, dict) else {}

    def get(self, key, default):
        return self._data.get(key, default)

    def put(self, key, value):
        if self._data.get(key) == value:
            return                      # an unchanged value is not a write
        self._data[key] = value
        self._dirty = True
        if self._clock() - self._last >= 1.0:
            self.flush()

    def flush(self):
        if not self._dirty:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self._data, sort_keys=True))
        os.replace(tmp, self.path)
        self._dirty, self._last = False, self._clock()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 bin/tt_wall.py --selftest`
Expected: PASS, prints `tt_wall selftest passed`

- [ ] **Step 5: Commit**

```bash
git add bin/tt_wall.py
git commit -m "feat(wall): a persisted store for the wall's shared UI state"
```

### Task 3: The portable row/section/layout helpers

**Files:**
- Modify: `bin/tt_wall.py`
- Test: `bin/tt_wall.py` (`selftest()`)

**Interfaces:**
- Consumes: `Store` (Task 2); `tt_model` (`row_text`, `SORTS`).
- Produces: `tally_text(open_actions, open_tasks, blocked=0) -> str`; `blocks(pct, cells=14) -> tuple[str, str]`; `bar_for(open_n, done_n) -> tuple[str, str]`; `resolved_cells(resolved, recorded) -> tuple[str, str]`; `open_rows(state, typ) -> list[dict]`; `done_rows(state) -> list[dict]`; `term_rows(state) -> list[dict]`; `diagram_rows(state) -> list[dict]`; `changed_ids(state, since) -> set[str]`; `dim(ev, query) -> bool`; `hits(evs, query) -> bool`; `next_sort(mode) -> str`; `toggle(s, item) -> set`; `abbrev(project) -> str`; `session_label(state, index) -> str`; `default_cols(width) -> int`; `cols_for(width, pref) -> int`; `layout_key(visible, cols, marks, folds, zoomed, scope, sort, drawer_open) -> tuple`; and the constants `MAX_CELLS = 20`, `BAR_CELLS = 14`, `WALL_WIDTH = 1400`, `NARROW = 900`, `SPINNER`.

Note the one rename: `dash.py`'s `_dim` returns the CSS suffix `" tt-dim"`; here `dim(ev, query)` returns a **bool** (True = dimmed), because the frame ships data and the class is the view's business. `hits` is `dash.py`'s `_hits` with the underscore dropped for the same reason.

- [ ] **Step 1: Write the failing test**

Append to `selftest()` (before the `print`):

```python
    assert tally_text(3, 2, 1) == "●3 open  ▶2 running  ⏸1 blocked", "every piece, two spaces apart"
    assert tally_text(0, 0, 0) == "all clear", "nothing open reads as all clear"
    assert tally_text(0, 2) == "▶2 running", "a zero piece is dropped, not printed"
    assert blocks(50, 14) == ("█" * 7, "░" * 7), "a bar snaps cell by cell"
    assert blocks(-5) == ("", "░" * 14) and blocks(140) == ("█" * 14, ""), "pct is clamped"
    assert bar_for(3, 2) == ("███", "░░"), "a shut section reports itself"
    assert bar_for(1, 100) == ("█", "░" * 19), "an open item is never rounded away"
    assert sum(len(x) for x in bar_for(50, 50)) == MAX_CELLS, "the bar never overflows MAX_CELLS"
    assert resolved_cells(0, 5) == ("", "▱▱▱▱▱") and resolved_cells(4, 7) == ("▰▰▰▰", "▱▱▱"), \
        "the window footer's obligation tally"
    assert resolved_cells(0, 0) == ("", ""), "a session with nothing recorded shows no cells"
    assert sum(len(x) for x in resolved_cells(30, 60)) == MAX_CELLS, "and it scales rather than overflows"
    st = {"a": {"id": "a", "type": "action", "status": "open", "ts": 9, "background": "Fold cadence"},
          "b": {"id": "b", "type": "action", "status": "done", "ts": 8, "background": "old"},
          "t": {"id": "t", "type": "task", "status": "open", "ts": 7, "what": "run"},
          "g": {"id": "g", "type": "term", "ts": 99, "term": "fold", "intuitive": "merge"}}
    assert [e["id"] for e in open_rows(st, "action")] == ["a"], "open rows exclude done"
    assert [e["id"] for e in done_rows(st)] == ["b"], "done rows are actions and tasks only"
    assert [e["id"] for e in term_rows(st)] == ["g"], "terms sort alphabetically"
    assert changed_ids(st, 6) == {"a", "b", "t"}, "actions and tasks gutter"
    assert changed_ids(st, 98) == set(), "a term never gutters, however new"
    assert dim({"id": "a", "background": "Fold cadence"}, "fold") is False, "a match is not dimmed"
    assert dim({"id": "a", "background": "Fold cadence"}, "zzz") is True, "a miss is dimmed"
    assert dim({"id": "a", "background": "x"}, "  ") is False, "a blank query dims nothing"
    assert hits([st["a"]], "fold") is True and hits([st["a"]], "zzz") is False, "#57: a hit forces a section open"
    assert next_sort("recent") == "actions" and next_sort("project") == "recent", "the sort cycle"
    assert next_sort("nonsense") == "recent", "an unknown sort falls to the first"
    assert toggle({"a"}, "a") == set() and toggle(set(), "a") == {"a"}, "membership toggles both ways"
    assert abbrev("gpn-yeast") == "gpn" and abbrev("") == "?", "the rail tag"
    assert session_label({"x": {"sid": "4f2a", "ts": 2}, "y": {"sid": "9b1c", "ts": 1}}, 7) == "4f2a", \
        "the newest sid names the window"
    assert session_label({"x": {"ts": 2}}, 7) == "7", "an unstamped file falls back to the index"
    assert (default_cols(1800), default_cols(1200), default_cols(900)) == (3, 2, 1), "the width thresholds"
    assert cols_for(600, 3) == 1, "a narrow wall packs one column whatever the preference says"
    assert cols_for(1900, 0) == 3 and cols_for(1900, 2) == 2, "the stored preference is a maximum"
    k1 = layout_key(["a"], 2, {"a"}, set(), None, None, "recent", True)
    assert k1 != layout_key(["a"], 2, set(), set(), None, None, "recent", True), "a mark moves a window"
    assert k1 == layout_key(["a"], 2, {"a"}, set(), None, None, "recent", True), "layout_key is stable"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 bin/tt_wall.py --selftest`
Expected: FAIL with `NameError: name 'tally_text' is not defined`

- [ ] **Step 3: Write the implementation**

Insert above `selftest()`. Every function below is `dash.py`'s, verbatim except `dim`/`hits` returning bools:

```python
SPINNER = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
MAX_CELLS = 20     # a long session must not wreck the footer line
BAR_CELLS = 14
WALL_WIDTH = 1400  # assumed until the client announces its own
NARROW = 900       # below this the wall packs ONE column whatever the pref says


def tally_text(open_actions, open_tasks, blocked=0):
    """The statusline's answer to 'is anything happening?'. Blocked is its own
    number and is subtracted from running by the caller: a task waiting on a
    decision is not running, and counting it as such is what let three finished
    PRs read as work in flight."""
    parts = []
    if open_actions:
        parts.append(f"●{open_actions} open")
    if open_tasks:
        parts.append(f"▶{open_tasks} running")
    if blocked:
        parts.append(f"⏸{blocked} blocked")
    return "  ".join(parts) if parts else "all clear"


def blocks(pct, cells=BAR_CELLS):
    """A glyph progress bar as (filled, empty). Deliberately snaps cell by cell:
    text cannot tween, and pretending otherwise is where terminal costume
    becomes terminal cosplay."""
    pct = max(0, min(100, pct))
    filled = round(cells * pct / 100)
    return "█" * filled, "░" * (cells - filled)


def bar_for(open_n, done_n):
    """A section's state as glyphs when it is shut: █ per item still wanting
    attention, ░ per resolved one."""
    open_n, done_n = max(0, open_n), max(0, done_n)
    if open_n + done_n > MAX_CELLS:               # scale, never overflow
        total = open_n + done_n
        # never round an open item AWAY: a shut section reporting "nothing
        # wants you" while something does is the one lie this bar must not tell
        open_n = max(1, round(MAX_CELLS * open_n / total)) if open_n else 0
        done_n = MAX_CELLS - open_n
    return "█" * open_n, "░" * done_n


def resolved_cells(resolved, recorded):
    """The window footer's obligation tally as (done, outstanding) glyphs.
    Verbatim from dash.py: every card carries this line under its body, and a
    folded card keeps it - tt.css hides only .win-b."""
    if recorded <= 0:
        return "", ""
    if recorded > MAX_CELLS:                      # scale down rather than overflow
        done = round(MAX_CELLS * resolved / recorded)
        return "▰" * done, "▱" * (MAX_CELLS - done)
    return "▰" * resolved, "▱" * (recorded - resolved)


def open_rows(state, typ):
    return sorted((e for e in state.values()
                   if e.get("type") == typ and e.get("status") != "done"),
                  key=lambda e: e.get("ts", 0), reverse=True)


def done_rows(state):
    return sorted((e for e in state.values()
                   if e.get("type") in ("action", "task") and e.get("status") == "done"),
                  key=lambda e: e.get("ts", 0), reverse=True)


def term_rows(state):
    return sorted((e for e in state.values() if e.get("type") == "term"),
                  key=lambda e: e.get("term", "").lower())


def diagram_rows(state):
    return sorted((e for e in state.values() if e.get("type") == "diagram"),
                  key=lambda e: e.get("ts", 0), reverse=True)


def changed_ids(state, since):
    """Ids of actions and tasks that moved after `since`. Terms are reference
    material - a glossary definition is not a change that needs your attention."""
    return {str(e["id"]) for e in state.values()
            if e.get("type") in ("action", "task") and e.get("ts", 0) > since}


def dim(ev, query):
    """Whether the filter dims this row. The filter DIMS, it never hides: a
    filter must never remove an open action from the wall."""
    q = (query or "").strip().lower()
    return bool(q) and q not in M.row_text(ev).lower()


def hits(evs, query):
    """True when a query is live and at least one of these rows matches it.
    Drives #57: a match inside a collapsed section is an invisible match, so a
    live query opens the section holding it."""
    return bool((query or "").strip()) and any(not dim(ev, query) for ev in evs)


def next_sort(mode):
    """Cycle recent -> actions -> project -> recent."""
    order = M.SORTS
    return order[(order.index(mode) + 1) % len(order)] if mode in order else order[0]


def toggle(s, item):
    """Set membership toggle, returning a new set so callers can compare."""
    out = set(s)
    out.discard(item) if item in out else out.add(item)
    return out


def abbrev(project):
    """Three-letter tag for the collapsed rail."""
    return project[:3] if project else "?"


def session_label(state, index):
    """What follows the colon in a window title: the code of the session that
    wrote this file last, or the tmux index for files recorded before stamping."""
    best, code = -1, ""
    for ev in state.values():
        if ev.get("sid") and ev.get("ts", 0) > best:
            best, code = ev.get("ts", 0), str(ev["sid"])
    return code or str(index)


def default_cols(width):
    """Column count before the user picks one. Three on a wide second monitor."""
    return 3 if width >= 1800 else (2 if width >= 1200 else 1)


def cols_for(width, pref):
    """How many columns a wall this wide gets. The stored preference is a
    MAXIMUM, never a mandate. pref 0 is 'auto', which is what `or` reads it as."""
    return 1 if width < NARROW else (pref or default_cols(width))


def layout_key(visible, cols, marks, folds, zoomed, scope, sort, drawer_open):
    """Everything that changes WHERE a window sits. The wall re-packs when this
    changes and at no other time - never on a poll that only changed text."""
    return (tuple(visible), cols, tuple(sorted(marks)), tuple(sorted(folds)),
            zoomed, scope, sort, drawer_open)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 bin/tt_wall.py --selftest`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bin/tt_wall.py
git commit -m "feat(wall): the portable row, section and layout helpers"
```

### Task 4: Links, transcripts, heartbeat and the context footer's paths

*(The footer **builder**, `context_footer()`, is Task 10; this task adds only the
path helper it stands on, which is why the commit message says "the context
footer's paths".)*

**Files:**
- Modify: `bin/tt_wall.py`
- Test: `bin/tt_wall.py` (`selftest()`)

**Interfaces:**
- Consumes: `tt_model.url_spans`, `tt_model.path_spans` (both unchanged).
- Produces: `link_roots(cfg) -> list[Path]`; `set_roots(cfg) -> None` (fills the module global `ROOTS`); `link_spans(text) -> list[tuple[int, int, str, bool]]` as `(start, end, target, is_url)`; `transcripts(root=None) -> dict[str, Path]`; `live_sessions(now, beat_dir=None, window=120) -> set[str]`; `nearest_claude_md(start, home) -> Path | None`; constants `BEAT_WINDOW = 120`, `LIVE_WINDOW = 300`.

`live_delay` does **not** move here — it is client-side in `ui/src/fmt.js` (Task 17), computed from the absolute `read_ts` in the frame.

- [ ] **Step 1: Write the failing test**

Append to `selftest()`:

```python
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "real.md").write_text("x")
        global ROOTS
        ROOTS = [root.resolve()]
        spans = link_spans(f"see {root}/real.md and https://example.com/a.")
        assert len(spans) == 2, "one path and one url"
        assert spans[0][2] == str((root / "real.md").resolve()) and spans[0][3] is False, "the path resolves"
        assert spans[1][2] == "https://example.com/a" and spans[1][3] is True, "trailing punctuation is trimmed"
        assert link_spans(f"{root}/missing.md") == [], "a path with no file behind it is not a link"
        assert [s[3] for s in link_spans("https://example.com/a/b/c")] == [True], "a url wins over the path in it"

        tx = root / "projects"
        (tx / "p1").mkdir(parents=True)
        (tx / "p2").mkdir(parents=True)
        (tx / "p1" / "4f2ab000-1111.jsonl").write_text("")
        (tx / "p2" / "9b1c0000-2222.jsonl").write_text("")
        (tx / "p2" / "9b1c9999-3333.jsonl").write_text("")
        got = transcripts(tx)
        assert set(got) == {"4f2a"}, "an ambiguous 4-char prefix is dropped entirely, never guessed"
        assert transcripts(root / "nope") == {}, "a missing projects dir is empty, not an error"

        beat = root / ".beat"
        beat.mkdir()
        (beat / "4f2a").write_text("")
        os.utime(beat / "4f2a", (1000.0, 1000.0))
        (beat / "old1").write_text("")
        os.utime(beat / "old1", (500.0, 500.0))
        (beat / "future").write_text("")
        os.utime(beat / "future", (2000.0, 2000.0))
        assert live_sessions(1050.0, beat) == {"4f2a"}, "inside the window is live"
        assert live_sessions(1050.0, root / "nope") == set(), "no hook installed is silence, not an error"

        home = root / "home"
        (home / "a" / "b").mkdir(parents=True)
        (home / "a" / "CLAUDE.md").write_text("x")
        assert nearest_claude_md(home / "a" / "b", home) == home / "a" / "CLAUDE.md", "walks up to the nearest"
        assert nearest_claude_md(root, home) is None, "never inspects anything above home"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 bin/tt_wall.py --selftest`
Expected: FAIL with `NameError: name 'link_spans' is not defined`

- [ ] **Step 3: Write the implementation**

```python
# The link roots, resolved once by set_roots(). The data dir, the CWD and the
# config's extra_roots are all fixed for the life of the process, so resolving
# them per rendered CELL was pure syscall overhead on the poll loop.
ROOTS = None
BEAT_WINDOW = 120   # how long a heartbeat counts as current
LIVE_WINDOW = 300   # how long after a reading a bar keeps pulsing (used client-side)


def link_roots(cfg):
    """Where a path found in a log line is allowed to point: the data dir, the
    project dir the brain was started in, and whatever links.extra_roots adds.
    Non-string entries are dropped rather than handed to Path(): extra_roots
    comes out of a TOML file and _merge only checks that the LIST is a list."""
    return [M.DATA_DIR.resolve(), Path.cwd(),
            *(Path(r).expanduser() for r in cfg["links"]["extra_roots"] if isinstance(r, str))]


def set_roots(cfg):
    global ROOTS
    ROOTS = link_roots(cfg)


def link_spans(text):
    """Every clickable run in one cell as (start, end, target, is_url), in order.
    URLs win where the two kinds overlap: a URL is full of slashes, so the
    path matcher matches it too, and only the URL half knows what to do with it."""
    urls = [(s, e, t, True) for s, e, t in M.url_spans(text)]
    taken = [(s, e) for s, e, _, _ in urls]
    paths = [(s, e, t, False) for s, e, t in M.path_spans(text, ROOTS or [])
             if not any(s < ue and us < e for us, ue in taken)]
    return sorted(urls + paths)


def transcripts(root=None):
    """{4-char session prefix: transcript path}, for prefixes that are UNIQUE.

    An ambiguous prefix is DROPPED rather than guessed: linking to the wrong
    conversation is worse than linking to none. Every project directory is
    searched, because a session's cwd need not be the one the brain started in."""
    root = Path(root) if root else Path.home() / ".claude" / "projects"
    seen = {}
    try:
        found = list(root.glob("*/*.jsonl"))
    except OSError:
        return {}
    for f in found:
        key = f.stem[:4]
        seen[key] = None if key in seen else f      # None marks it ambiguous
    return {k: v for k, v in seen.items() if v is not None}


def live_sessions(now, beat_dir=None, window=BEAT_WINDOW):
    """The session codes whose heartbeat is current. Absent hook, absent
    directory, unreadable file: all mean 'nobody is known to be working', which
    is honest rather than a failure."""
    beat_dir = Path(beat_dir) if beat_dir else M.DATA_DIR / ".beat"
    live = set()
    try:
        entries = list(beat_dir.iterdir())
    except OSError:
        return live
    for f in entries:
        try:
            if 0 <= now - f.stat().st_mtime < window:
                live.add(f.name)
        except OSError:
            continue
    return live


def nearest_claude_md(start, home):
    """The closest CLAUDE.md found walking up from `start`, or None. Never
    inspects anything above `home`; both paths are resolved before comparison,
    so a symlinked start that resolves outside home's tree is refused outright."""
    cur = Path(start).resolve()
    home = Path(home).resolve()
    try:
        cur.relative_to(home)
    except ValueError:
        return None
    while True:
        hit = cur / "CLAUDE.md"
        if hit.is_file():
            return hit
        if cur == home:
            return None
        cur = cur.parent
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 bin/tt_wall.py --selftest`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bin/tt_wall.py
git commit -m "feat(wall): link spans, transcripts, heartbeat and the context footer's paths"
```

### Task 5: `Wall` — per-connection state, `KEYMAP`, intents and the fold rules

**Files:**
- Modify: `bin/tt_wall.py`
- Test: `bin/tt_wall.py` (`selftest()`)

**Interfaces:**
- Consumes: `Store` (Task 2), `toggle`, `next_sort`, `cols_for` (Task 3).
- Produces:
  - `KEYMAP: dict[str, str]` — `{"\\": "drawer", "m": "mark", "z": "zoom", "f": "fold", "s": "sort", "/": "filter", "!": "needs-me", "u": "merge", "?": "keys", "Escape": "unzoom"}`.
  - `Wall(store, cfg)` with attributes `store`, `cfg`, `opened_ts: float`, `seen_at: dict[str, float]`, `touched: bool`, `wall_width: float`, `query: str`, `sections: dict[str, dict[str, bool]]`, `on_wall: list[str]`, `layout: tuple | None`, `focus: str | None`, `warn: str | None`.
  - `Wall.apply(intent: dict) -> None` — every view intent except `open` and `config`, which `tt_serve` owns because they leave the process.
  - `Wall.target() -> str | None` — the window `m`/`z`/`f` act on.
  - `Wall.apply_fold_rules(groups: list[dict]) -> None`.

- [ ] **Step 1: Write the failing test**

Append to `selftest()`:

```python
    with tempfile.TemporaryDirectory() as td:
        cfg = tt_config.DEFAULTS
        w = Wall(Store(Path(td) / "wall.json"), cfg)
        w.on_wall = ["k1", "k2"]
        assert w.target() == "k1", "with nothing clicked, the first window on the wall is the target"
        w.apply({"do": "current", "key": "k2"})
        assert w.target() == "k2", "a click makes a window current"
        w.apply({"do": "key", "k": "m"})
        assert w.store.get("marks", []) == ["k2"], "m marks the current window"
        w.apply({"do": "key", "k": "m"})
        assert w.store.get("marks", []) == [], "m toggles"
        w.apply({"do": "key", "k": "z"})
        assert w.store.get("zoomed", None) == "k2", "z zooms the current window"
        w.apply({"do": "key", "k": "Escape"})
        assert w.store.get("zoomed", None) is None, "Escape un-zooms"
        w.apply({"do": "key", "k": "u"})
        assert w.store.get("merged", None) is False, "u flips merged off, from the config default"
        w.apply({"do": "key", "k": "s"})
        assert w.store.get("sort", "recent") == "actions", "s cycles the drawer sort"
        w.apply({"do": "key", "k": "!"})
        assert w.store.get("needs_me", False) is True, "! toggles needs-me"
        w.apply({"do": "key", "k": "\\"})
        assert w.store.get("drawer_open", True) is False, "\\ toggles the drawer"
        w.apply({"do": "key", "k": "/"})
        assert w.query == "", "/ is a client-side affordance and never reaches the brain's state"
        w.apply({"do": "scope", "project": "p1"})
        w.apply({"do": "key", "k": "z"})
        w.apply({"do": "scope", "project": None})
        assert w.store.get("zoomed", None) is None, "a scope change clears the zoom"
        w.apply({"do": "width", "px": 640})
        assert w.wall_width == 640, "the client reports the wall's own width"
        w.apply({"do": "touch"})
        assert w.touched is True, "a touch is remembered until the next frame consumes it"
        w.apply({"do": "query", "q": " fold "})
        assert w.query == "fold", "the query is stripped once, here"
        w.apply({"do": "section", "key": "k2", "sec": "gls", "open": True})
        assert w.sections["k2"]["gls"] is True, "a section toggle is per window and per connection"
        w.apply({"do": "focus", "key": "k1"})
        assert w.focus == "k1", "a drawer click asks the wall to scroll once"
        w.apply({"do": "cols", "n": 3})
        assert w.store.get("cols", 0) == 3, "the statusline picks a column count"

        w2 = Wall(Store(Path(td) / "w2.json"), cfg)
        w2.apply_fold_rules([{"project": "quiet", "open_actions": 0},
                             {"project": "busy", "open_actions": 2}])
        assert w2.store.get("groups_folded", []) == ["quiet"], "a project first seen with nothing open folds"
        assert w2.store.get("seen", {}) == {"quiet": 0, "busy": 2}, "what was seen is persisted"
        w2.store.put("groups_folded", ["quiet", "busy"])
        w2.apply_fold_rules([{"project": "quiet", "open_actions": 0},
                             {"project": "busy", "open_actions": 2}])
        assert set(w2.store.get("groups_folded", [])) == {"quiet", "busy"}, \
            "a LEVEL never reopens a manually folded project - it would spring open every 2s"
        w2.apply_fold_rules([{"project": "quiet", "open_actions": 0},
                             {"project": "busy", "open_actions": 3}])
        assert w2.store.get("groups_folded", []) == ["quiet"], "a RISING edge reopens it"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 bin/tt_wall.py --selftest`
Expected: FAIL with `NameError: name 'Wall' is not defined`

- [ ] **Step 3: Write the implementation**

```python
# The single source for keys AND chips, so a key can never do something no
# click can. "/" and "?" are dispatched by the view: focusing an input and
# opening a dialog are the client's business and must not cost a round trip.
KEYMAP = {"\\": "drawer", "m": "mark", "z": "zoom", "f": "fold",
          "s": "sort", "/": "filter", "!": "needs-me", "u": "merge",
          "?": "keys", "Escape": "unzoom"}


class Wall:
    """One per SSE connection - the exact analogue of NiceGUI's closure per
    client. Two open windows get independent watermarks and independent wall
    widths, and share marks, folds and scope through the one Store."""

    def __init__(self, store, cfg):
        self.store, self.cfg = store, cfg
        # The floor is NOW, which is what makes a freshly opened wall quiet: a
        # change you were never here for is not a change you missed.
        self.opened_ts = time.time()
        self.seen_at = {}          # window key -> when its rows were last on screen
        self.touched = False       # the client saw a click/keypress/scroll
        self.wall_width = WALL_WIDTH
        self.query = ""
        self.sections = {}         # window key -> {section id: open?}
        self.on_wall = []          # what the last frame put on the wall, in order
        self.layout = None         # (layout_key, columns) - re-packed only on a change
        self.focus = None          # one-shot: scroll this window into view
        # A message for the statusline, owned by tt_serve's reload_config: it is
        # CLEARED and re-set on every config reload (Task 16), so it says what is
        # true now. Unlike self.focus it is NOT consumed by frame() - a port that
        # is still taken is still taken on the next frame, and dash.py's
        # restart_offer recomputed exactly that message every poll.
        self.warn = None

    def target(self):
        """The window m/z/f act on: the one last clicked if it is still on the
        wall, else the first window on the wall."""
        cur = self.store.get("current", None)
        if cur in self.on_wall:
            return cur
        return self.on_wall[0] if self.on_wall else None

    def _toggle_key(self, key, item):
        self.store.put(key, sorted(toggle(set(self.store.get(key, [])), item)))

    def apply(self, intent):
        """One intent. Unknown intents are ignored rather than raising: the view
        and the brain move in the same commit, but a stale tab must not be able
        to kill a connection."""
        do, s = intent.get("do"), self.store
        if do == "key":
            act = KEYMAP.get(intent.get("k"))
            if act == "drawer":
                s.put("drawer_open", not s.get("drawer_open", self.cfg["ui"]["drawer_open"]))
            elif act in ("mark", "zoom", "fold") and (k := self.target()):
                self.apply({"do": "window", "key": k, "act": act})
            elif act == "sort":
                s.put("sort", next_sort(s.get("sort", "recent")))
            elif act == "needs-me":
                s.put("needs_me", not s.get("needs_me", False))
            elif act == "merge":
                s.put("merged", not s.get("merged", self.cfg["ui"]["view"] == "merged"))
            elif act == "unzoom":
                s.put("zoomed", None)
        elif do == "window":
            key, act = intent.get("key"), intent.get("act")
            if act == "mark":
                self._toggle_key("marks", key)
            elif act == "fold":
                self._toggle_key("folds", key)
            elif act == "zoom":
                s.put("zoomed", None if s.get("zoomed", None) == key else key)
        elif do == "current":
            s.put("current", intent.get("key"))
        elif do == "scope":
            s.put("scope", intent.get("project"))
            # a zoom pointed at a window no longer on the wall is meaningless
            s.put("zoomed", None)
        elif do == "group_fold":
            self._toggle_key("groups_folded", intent.get("project"))
        elif do == "focus":
            self.focus = intent.get("key")
        elif do == "section":
            self.sections.setdefault(intent.get("key"), {})[intent.get("sec")] = bool(intent.get("open"))
        elif do == "query":
            self.query = (intent.get("q") or "").strip()
        elif do == "width":
            try:
                self.wall_width = float(intent.get("px"))
            except (TypeError, ValueError):
                pass
        elif do == "cols":
            n = intent.get("n")
            if n in (0, 1, 2, 3):
                s.put("cols", n)
        elif do == "theme":
            modes = ("system", "light", "dark")
            cur = s.get("theme", self.cfg["theme"]["default"])
            s.put("theme", modes[(modes.index(cur) + 1) % 3] if cur in modes else "system")
        elif do == "touch":
            self.touched = True

    def apply_fold_rules(self, groups):
        """A project with nothing open folds the first time it is ever seen, so
        the drawer opens showing only what is live. A poll that RAISES the
        open-action count forces the group back open.

        Rising edge, not level: a level test ('any open action forces it open')
        springs a manually folded group back open two seconds later, every time,
        so a busy project could never be collapsed at all."""
        seen = dict(self.store.get("seen", {}))
        folded = set(self.store.get("groups_folded", []))
        before_folded, before_seen = set(folded), dict(seen)
        for g in groups:
            project, n = g["project"], g["open_actions"]
            was = seen.get(project)
            if was is None:
                if n == 0:
                    folded.add(project)
            elif n > was and project in folded:      # an action just LANDED
                folded.discard(project)
            seen[project] = n
        if folded != before_folded:
            self.store.put("groups_folded", sorted(folded))
        if seen != before_seen:
            self.store.put("seen", seen)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 bin/tt_wall.py --selftest`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bin/tt_wall.py
git commit -m "feat(wall): per-connection view state, the keymap and the intent set"
```

### Task 6: Rows — cells, spans, meters and the copy string

**Files:**
- Modify: `bin/tt_wall.py`
- Test: `bin/tt_wall.py` (`selftest()`)

**Interfaces:**
- Consumes: `link_spans` (Task 4), `blocks`, `dim` (Task 3); `tt_model.art_spans`, `tt_model.parts`, `tt_model.progress_pct`, `tt_model.blocked_by` (all unchanged).
- Produces:
  - `cell_spans(text, query) -> list[list]` — each span is `[text, kind]`, or `[text, kind, target]` when the kind contains `lnk`. `kind` is a **CSS class string**: `""`, `"tt-hit"`, `"lnk"`, `"lnk tt-hit"`.
  - `art_lines(text) -> list[list[list]]` — one list of spans per line, kinds `""` and `"st"`.
  - `copy_for(ev) -> str | None` — `"SESSION: <sid> - ID: <id>"`, or the bare id, or `None` when the id does not match `^[0-9a-f]{4,}$`. `row_for` calls it **only for action, task and done rows**: `dash.py` renders a term's and a diagram's id cell as a plain `ui.label`, never through `_id_button`, so neither is clickable or copyable today. The hex guard cannot carry that rule — `new_id()` is `secrets.token_hex(2)` (`bin/table-talk:91-98`), so every real id, on every kind, matches it.
  - `meter_for(ev, open_acts) -> dict` — `{"kind": "blocked", "on": id}` | `{"kind": "scan"}` | `{"kind": "bar", "pct": int, "cells": [filled, empty], "read_ts": float | None}`.
  - `row_for(ev, kind, query, changed, cursor, open_acts) -> dict` — one row of the frame.

Row shape, fixed here and relied on by Tasks 8, 12, 21, 22, 23 and 28:

```json
{"kind":"action","id":"a1b2","sid":"4f2a","copy":"SESSION: 4f2a - ID: a1b2",
 "ts":1757337421,"changed":"act","dim":false,"cursor":true,"reply":true,
 "cells":[{"c":"title","spans":[["Pick a fold cadence",""]]},
          {"c":"int","spans":[…]},{"c":"why","spans":[…]},{"c":"rec","spans":[…]},
          {"c":"art","lines":[[["┌──","st"],[" alpha ",""]]]}]}
```

`changed` is `""`, `"act"` or `"job"`. `meter` rides as `{"c":"meter","meter":{…}}`; a diagram body as `{"c":"mermaid","src":"…"}`.

- [ ] **Step 1: Write the failing test**

Append to `selftest()`:

```python
    ROOTS = []
    assert cell_spans("plain", "") == [["plain", ""]], "no query, no link: one plain span"
    assert cell_spans("a Fold b", "fold") == [["a ", ""], ["Fold", "tt-hit"], [" b", ""]], \
        "a hit keeps its original casing"
    got = cell_spans("see https://example.com/a now", "")
    assert got[1] == ["https://example.com/a", "lnk", "https://example.com/a"], "a link carries its target"
    got = cell_spans("see https://example.com/a now", "example")
    assert ["example", "lnk tt-hit", "https://example.com/a"] in got, "a hit inside a link keeps both classes"
    lines = art_lines("┌─┐\nab┘")
    assert lines == [[["┌─┐", "st"]], [["ab", ""], ["┘", "st"]]], "structure and label split, per line"
    assert copy_for({"id": "a1b2", "sid": "4f2a"}) == "SESSION: 4f2a - ID: a1b2", "the documented copy format"
    assert copy_for({"id": "a1b2"}) == "a1b2", "no session code, the bare id"
    assert copy_for({"id": "a1b2", "_from": "9b1c"}) == "SESSION: 9b1c - ID: a1b2", "merged rows carry _from"
    assert copy_for({"id": "../etc"}) is None, "a non-hex id never reaches a clipboard"
    assert copy_for({"id": "a1b2", "sid": "zz;rm"}) == "a1b2", "a non-hex session code is dropped, not copied"
    assert meter_for({"id": "t1", "blocked_on": "a1b2"}, {"a1b2"}) == {"kind": "blocked", "on": "a1b2"}, \
        "a blocked task is not drawn as a running one"
    assert meter_for({"id": "t1", "blocked_on": "a1b2"}, set())["kind"] != "blocked", \
        "an answered blocker releases the task with no second write"
    assert meter_for({"id": "t1"}, set()) == {"kind": "scan"}, "no reading is a sweep, never an invented number"
    m = meter_for({"id": "t1", "pct": 50, "read_ts": 1757337421}, set())
    assert m == {"kind": "bar", "pct": 50, "cells": ["█" * 7, "░" * 7], "read_ts": 1757337421}, \
        "an explicit reading is a bar, with the absolute read_ts the view pulses from"
    assert meter_for({"id": "t1", "pct": 50}, set())["read_ts"] is None, "no read_ts is honest about it"
    ev = {"id": "a1b2", "type": "action", "status": "open", "ts": 5, "sid": "4f2a",
          "background": "Pick a cadence", "intuitive": "how often", "why": "w", "rec": "r",
          "diagram": "┌─┐"}
    r = row_for(ev, "action", "", {"a1b2"}, True, set())
    assert [c["c"] for c in r["cells"]] == ["title", "int", "why", "rec", "art"], \
        "int reads FIRST, before why and rec"
    assert r["reply"] is True and r["cursor"] is True and r["changed"] == "act", "an action row's flags"
    t = row_for({"id": "t1", "type": "task", "what": "run", "intuitive": "i", "diagram": "x", "ts": 1},
                "task", "", set(), False, set())
    assert [c["c"] for c in t["cells"]] == ["title", "meter", "int", "art"], "a task row's order"
    assert t["reply"] is True and t["changed"] == "", "a task replies too, and did not move"
    d = row_for({"id": "d1", "type": "action", "status": "done", "background": "b", "diagram": "x", "ts": 1},
                "done", "", set(), False, set())
    assert [c["c"] for c in d["cells"]] == ["title", "art"] and d["reply"] is True, \
        "a done row keeps its sketch and its reply box"
    # HEX ids on purpose: every id the CLI mints is secrets.token_hex(2), so a
    # synthetic "g1" would make copy None through the hex guard and never
    # exercise the rule that terms and diagrams are not copyable AT ALL.
    g = row_for({"id": "a1b2", "sid": "4f2a", "type": "term", "term": "fold",
                 "intuitive": "merge", "technical": "t"},
                "term", "", set(), False, set())
    assert [c["c"] for c in g["cells"]] == ["title", "def"] and g["reply"] is False, \
        "a term is reference material: no gutter, no reply"
    assert g["label"] == "fold" and g["copy"] is None, \
        "a term's id cell is the TERM, and dash.py never made it a button - not copyable"
    assert copy_for({"id": "a1b2", "sid": "4f2a"}) is not None, \
        "and that is a rule about the KIND, not about the id: the same id copies on an action"
    dg = row_for({"id": "c3d4", "sid": "4f2a", "type": "diagram", "title": "flow",
                  "mermaid": "flowchart TD\n A-->B"},
                 "diagram", "", set(), False, set())
    assert dg["label"] == "flow" and dg["copy"] is None, \
        "a diagram's id cell is its title, and it is not copyable either"
    assert dg["cells"][0]["c"] == "mermaid" and dg["cells"][0]["src"].startswith("%%{init"), \
        "the init directive travels with every diagram"
    assert "-" not in MERMAID_INIT, "one hyphen blanks the WHOLE themeVariables value, silently"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 bin/tt_wall.py --selftest`
Expected: FAIL with `NameError: name 'cell_spans' is not defined`

- [ ] **Step 3: Write the implementation**

```python
_HEXID = re.compile(r"^[0-9a-f]{4,}$")

# Mermaid dressed as the app. The SPLIT is the point: colour comes from tt.css
# token overrides (they swap with the palette live - in "system" mode the
# server cannot know the client's prefers-color-scheme, so no baked colour can
# be right), but the FONT goes through the directive, because mermaid measures
# labels with its configured font and a CSS-only font swap overflows every box
# it drew. A per-render directive rather than initialize() config: it travels
# with each render. The sanitiser runs every themeVariables VALUE through
# ^[\d "#%(),.;A-Za-z]+$ - NO hyphen - and one bad character blanks the WHOLE
# value. Verified against the bundled mermaid 11.16.1; the selftest pins it.
MERMAID_INIT = ('%%{init: {"theme": "base", "themeVariables": {"fontFamily": '
                '"JetBrains Mono, Adwaita Mono, SF Mono, Menlo, Consolas, monospace", '
                '"fontSize": "12px"}}}%%\n')


def cell_spans(text, query):
    """One cell as [[text, kind], ...], kind being a CSS class string, plus a
    third element carrying the target on a link.

    Rendered as TEXT by the view - never markup, because this came out of a log
    file. tt_model.marked's HTML escaping is not needed and not used: Svelte
    escapes a text node structurally, so the bug cannot occur."""
    text = "" if text is None else str(text)
    if not text:
        return []
    links = link_spans(text)
    out, pos = [], 0
    for start, end, target, _is_url in links + [(len(text), len(text), "", False)]:
        for chunk, hit in M.parts(text[pos:start], query or ""):
            if chunk:
                out.append([chunk, "tt-hit" if hit else ""])
        if start < end:
            for chunk, hit in M.parts(text[start:end], query or ""):
                if chunk:
                    out.append([chunk, "lnk tt-hit" if hit else "lnk", target])
        pos = end
    return out


def art_lines(text):
    """The ASCII sketch as [[ [text, kind], ... ] per line]. Two inks: structure
    (faint) and label (full). Split per line so the view can keep white-space:pre
    without one span crossing a newline."""
    out = []
    for line in str(text or "").split("\n"):
        spans = [[chunk, "st" if is_st else ""] for chunk, is_st in M.art_spans(line) if chunk]
        out.append(spans)
    return out


def copy_for(ev):
    """What clicking the id copies, or None when the id is not an id.

    Guarded HERE, in the producer: a string that never leaves Python cannot be
    tampered with in the view, so the ^[0-9a-f]{4,}$ rule is enforced once
    rather than in every consumer."""
    ident = str(ev.get("id", ""))
    if not _HEXID.match(ident):
        return None
    sid = str(ev.get("sid") or ev.get("_from") or "")
    return f"SESSION: {sid} - ID: {ident}" if _HEXID.match(sid) else ident


def meter_for(ev, open_acts):
    """A task's progress line: a blocked banner, an indeterminate sweep, or a
    bar. Blocked replaces the bar entirely - a blocked task is explicitly NOT
    drawn as a running one."""
    if (on := M.blocked_by(ev, open_acts)):
        return {"kind": "blocked", "on": on}
    pct = M.progress_pct(ev)
    if pct is None:
        return {"kind": "scan"}
    read_ts = ev.get("read_ts", ev.get("ts"))
    if not isinstance(read_ts, (int, float)) or isinstance(read_ts, bool):
        read_ts = None
    return {"kind": "bar", "pct": pct, "cells": list(blocks(pct)), "read_ts": read_ts}


def row_for(ev, kind, query, changed, cursor, open_acts):
    """One row of the frame. The CELL ORDER is decided here and asserted by the
    selftest - a stronger pin than dash.py's AST call-count check, because it
    asserts the output rather than the shape of the code that produced it."""
    ident = str(ev.get("id", ""))
    cells = []
    if kind == "term":
        cells.append({"c": "title", "spans": cell_spans(ev.get("intuitive"), query)})
        cells.append({"c": "def", "spans": cell_spans(ev.get("technical"), query)})
    elif kind == "diagram":
        # The title IS the id cell (see `label` below), so a diagram row is a
        # header and a picture - there is no second title line.
        cells.append({"c": "mermaid", "src": MERMAID_INIT + str(ev.get("mermaid") or "")})
    else:
        cells.append({"c": "title", "spans": cell_spans(
            ev.get("background") if kind == "action" else ev.get("what") or ev.get("background"), query)})
        if kind == "task":
            cells.append({"c": "meter", "meter": meter_for(ev, open_acts)})
        if kind == "action":
            # int reads FIRST: the intuitive line is the one a tired reader
            # needs before the argument for it.
            for c, field in (("int", "intuitive"), ("why", "why"), ("rec", "rec")):
                if ev.get(field):
                    cells.append({"c": c, "spans": cell_spans(ev[field], query)})
        elif kind == "task" and ev.get("intuitive"):
            cells.append({"c": "int", "spans": cell_spans(ev["intuitive"], query)})
        if ev.get("diagram"):
            cells.append({"c": "art", "lines": art_lines(ev["diagram"])})
    gutter = ""
    if kind in ("action", "done") and ident in changed:
        gutter = "act"
    elif kind == "task" and ident in changed:
        gutter = "job"
    return {"kind": kind, "id": ident,
            # A term's id cell is the TERM, and a diagram's is its title:
            # neither is a hex id, so neither is copyable, and `label` is what
            # the view puts in the id button for them.
            "label": (str(ev.get("term", "")) if kind == "term"
                      else str(ev.get("title", "")) if kind == "diagram" else ident),
            "sid": str(ev.get("sid") or ev.get("_from") or ""),
            # Gated by KIND, not by the id: dash.py's _id_button is used on
            # action, task and done rows only (table-talk-dash.py:915,939,1063),
            # while _term_row and _diagram_row render a plain label. Every real
            # id is token_hex(2), so copy_for's hex guard would happily hand a
            # term a clipboard string it has never had.
            "copy": copy_for(ev) if kind in ("action", "task", "done") else None,
            "ts": ev.get("ts", 0),
            "changed": gutter, "dim": dim(ev, query),
            "cursor": bool(cursor), "reply": kind in ("action", "task", "done"),
            "cells": cells}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 bin/tt_wall.py --selftest`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bin/tt_wall.py
git commit -m "feat(wall): rows as cells and spans, with int before why and rec"
```

### Task 7: Sections — order, counts, collapse bars and the #57 force-open

**Files:**
- Modify: `bin/tt_wall.py`
- Test: `bin/tt_wall.py` (`selftest()`)

**Interfaces:**
- Consumes: `open_rows`, `done_rows`, `term_rows`, `diagram_rows`, `bar_for`, `hits` (Task 3); `row_for` (Task 6).
- Produces: `SECTIONS: tuple[tuple[str, str, str], ...]` — `(id, title, config_name)` triples: `(("act", "actions --open", "actions"), ("job", "jobs", "jobs"), ("dia", "diagrams", "diagrams"), ("gls", "glossary", "glossary"), ("ok", "done", "done"))`; and `sections_for(state, key, wall, query, changed, newest, open_acts) -> list[dict]`.

The third element matters: `ui.collapsed_sections` names sections the way the **config** names them (`["glossary","done"]` by default), not by the internal id — `dash.py:1157-1161` checks `"glossary" not in collapsed`, and getting this wrong silently un-collapses the two sections that should start shut.

Section shape: `{"id":"act","title":"actions --open","n":2,"open":true,"forced":false,"bar":["██","░"]|null,"empty":"nothing needs you"|null,"rows":[…]}`.

- [ ] **Step 1: Write the failing test**

Append to `selftest()`:

```python
    with tempfile.TemporaryDirectory() as td:
        w = Wall(Store(Path(td) / "w.json"), tt_config.DEFAULTS)
        st = {"a1b2": {"id": "a1b2", "type": "action", "status": "open", "ts": 9, "background": "Pick a cadence"},
              "a9c0": {"id": "a9c0", "type": "action", "status": "done", "ts": 8, "background": "Old choice"},
              "t111": {"id": "t111", "type": "task", "status": "open", "ts": 7, "what": "run the fold"},
              "g222": {"id": "g222", "type": "term", "ts": 6, "term": "fold", "intuitive": "merge", "technical": "t"}}
        secs = sections_for(st, "k1", w, "", set(), None, set())
        assert [s["id"] for s in secs] == ["act", "job", "gls", "ok"], \
            "five sections in a fixed order, and diagrams only when one exists"
        by = {s["id"]: s for s in secs}
        assert by["act"]["title"] == "actions --open" and by["act"]["n"] == 1, "the header carries its count"
        assert by["act"]["open"] is True and by["act"]["bar"] is None, "an open section shows rows, not a bar"
        assert by["act"]["empty"] is None and by["job"]["empty"] is None, "these two have rows"
        assert by["gls"]["open"] is False and by["gls"]["rows"] == [], \
            "ui.collapsed_sections names sections as the CONFIG does, and a shut section ships no rows"
        assert by["ok"]["bar"] == ["", "░"], "a shut section still reports itself in glyphs"
        assert by["act"]["bar"] is None, "an open section has no bar at all"
        empty = {s["id"]: s for s in sections_for({}, "k1", w, "", set(), None, set())}
        assert empty["act"]["empty"] == "nothing needs you" and empty["job"]["empty"] == "nothing running", \
            "an open but empty section says so"
        w.apply({"do": "section", "key": "k1", "sec": "gls", "open": True})
        assert {s["id"]: s["open"] for s in sections_for(st, "k1", w, "", set(), None, set())}["gls"] is True, \
            "the user's own toggle survives the rebuild"
        w.apply({"do": "section", "key": "k1", "sec": "gls", "open": False})
        forced = {s["id"]: s for s in sections_for(st, "k1", w, "fold", set(), None, set())}["gls"]
        assert forced["open"] is True and forced["forced"] is True, \
            "#57: a query hit force-opens a shut section for that render only"
        assert w.sections["k1"]["gls"] is False, "and never touches the user's toggle"
        with_dia = dict(st, d1={"id": "d1", "type": "diagram", "ts": 5, "title": "flow", "mermaid": "flowchart TD\n A-->B"})
        assert [s["id"] for s in sections_for(with_dia, "k1", w, "", set(), None, set())] == \
            ["act", "job", "dia", "gls", "ok"], "diagrams appear when there is one to look at"
        cur = sections_for(st, "k1", w, "", set(), "a1b2", set())
        assert cur[0]["rows"][0]["cursor"] is True, "the newest open action wall-wide carries the cursor"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 bin/tt_wall.py --selftest`
Expected: FAIL with `NameError: name 'sections_for' is not defined`

- [ ] **Step 3: Write the implementation**

```python
# Fixed order, and the name each section carries in ui.collapsed_sections.
# diagrams sits third because it exists to be looked at, and it is the only
# section that does not appear when it is empty.
SECTIONS = (("act", "actions --open", "actions"), ("job", "jobs", "jobs"),
            ("dia", "diagrams", "diagrams"), ("gls", "glossary", "glossary"),
            ("ok", "done", "done"))
_EMPTY = {"act": "nothing needs you", "job": "nothing running"}


def sections_for(state, key, wall, query, changed, newest, open_acts):
    """A window's body: five sections, each already carrying its rows or its
    collapse bar. A shut section ships NO rows - the glyph bar is what a shut
    section says, and sending rows nobody can see is the frame's largest
    avoidable cost."""
    collapsed = wall.cfg["ui"]["collapsed_sections"]
    toggles = wall.sections.setdefault(key, {})
    done = done_rows(state)
    done_a = sum(1 for e in done if e.get("type") == "action")
    rows_by = {"act": open_rows(state, "action"), "job": open_rows(state, "task"),
               "dia": diagram_rows(state), "gls": term_rows(state), "ok": done}
    kinds = {"act": "action", "job": "task", "dia": "diagram", "gls": "term", "ok": "done"}
    out = []
    for sid, title, cfg_name in SECTIONS:
        evs = rows_by[sid]
        if sid == "dia" and not evs:
            continue
        opened = toggles.get(sid, cfg_name not in collapsed)
        forced = not opened and hits(evs, query)
        show = opened or forced
        built = [row_for(ev, kinds[sid], query, changed,
                         kinds[sid] == "action" and str(ev.get("id")) == newest, open_acts)
                 for ev in evs] if show else []
        # The pairing dash.py uses: an open section's bar counts the resolved
        # items of its OWN kind, so a shut "actions" bar reads "n waiting, m
        # already answered" rather than repeating the done section's total.
        counts = {"act": (len(rows_by["act"]), done_a),
                  "job": (len(rows_by["job"]), len(done) - done_a),
                  "dia": (0, len(rows_by["dia"])),
                  "gls": (0, len(rows_by["gls"])),
                  "ok": (0, len(done))}[sid]
        out.append({"id": sid, "title": title, "n": len(evs), "open": show,
                    "forced": forced, "bar": None if show else list(bar_for(*counts)),
                    "empty": _EMPTY.get(sid) if show and not evs else None,
                    "rows": built})
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 bin/tt_wall.py --selftest`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bin/tt_wall.py
git commit -m "feat(wall): sections, their collapse bars and the #57 force-open"
```

### Task 8: Windows — titlebar flags, the paint signature and the per-window guard

**Files:**
- Modify: `bin/tt_wall.py`
- Test: `bin/tt_wall.py` (`selftest()`)

**Interfaces:**
- Consumes: `sections_for` (Task 7), `session_label`, `changed_ids`, `resolved_cells` (Task 3), `transcripts`, `live_sessions` (Task 4); `tt_model.summarize` (unchanged).
- Produces: `window_for(key, state, wall, ctx) -> dict` where `ctx` is `{"query": str, "changed": set[str], "newest": str | None, "open_acts": frozenset[str], "tx": dict[str, Path], "on_wall": bool, "where": tuple[str, int]}`; and `win_sig(win) -> str` (16 hex chars from `hashlib.blake2s`).

Window shape:

```json
{"sig":"9f3c…","project":"gpn-yeast","sid":"4f2a","tx":"/home/…/4f2a….jsonl","latest":1757337421,
 "flags":{"bell":true,"actv":false,"mark":false,"zoom":false,"cur":true,"fold":false,"beat":true},
 "footer":{"cells":["▰▰▰","▱▱"],"text":"3/5 resolved"},
 "sections":[…]}
```

The footer is `dash.py`'s `.win-f` line (`table-talk-dash.py:2698`, populated at
`:2991-2998`): the `▰▱` obligation tally and `N/M resolved`, with `· all clear`
appended when nothing is open. It rides on **every** window on the wall, folded ones
included — `tt.css:150` hides only `.win-b`, so a folded card is a titlebar and this
footer.

- [ ] **Step 1: Write the failing test**

Append to `selftest()`:

```python
    with tempfile.TemporaryDirectory() as td:
        w = Wall(Store(Path(td) / "w.json"), tt_config.DEFAULTS)
        w.on_wall = ["2026-08-27-proj"]
        st = {"a1b2": {"id": "a1b2", "type": "action", "status": "open", "ts": 9, "sid": "4f2a",
                       "background": "Pick a cadence"},
              "t111": {"id": "t111", "type": "task", "status": "open", "ts": 7, "sid": "4f2a",
                       "what": "run", "blocked_on": "zzzz"}}
        ctx = {"query": "", "changed": set(), "newest": "a1b2", "open_acts": frozenset({"a1b2"}),
               "tx": {}, "on_wall": True, "where": ("proj", 0)}
        win = window_for("2026-08-27-proj", st, w, ctx)
        assert win["project"] == "proj" and win["sid"] == "4f2a", "the titlebar names the project and the session"
        assert win["latest"] == 9 and isinstance(win["latest"], (int, float)), \
            "the age is an ABSOLUTE stamp; the view renders 'ago' from it"
        assert win["flags"]["bell"] is True and win["flags"]["actv"] is True, "an open action bells, an open task hums"
        assert win["flags"]["cur"] is True, "the first window on the wall is current with nothing clicked"
        assert win["tx"] is None, "an unresolvable transcript is a missing link, never a guess"
        assert [s["id"] for s in win["sections"]] == ["act", "job", "gls", "ok"], "the body is sections"
        assert win["footer"]["text"] == "0/2 resolved", \
            "every card carries dash.py's obligation footer under its body"
        assert win["footer"]["cells"] == ["", "▱▱"], "as ▰ done / ▱ outstanding glyphs"
        w.store.put("folds", ["2026-08-27-proj"])
        folded = window_for("2026-08-27-proj", st, w, ctx)
        assert folded["sections"] == [] and folded["footer"]["text"] == "0/2 resolved", \
            "a folded card is a titlebar and its footer: tt.css hides .win-b, never .win-f"
        w.store.put("folds", [])
        off = window_for("2026-08-27-proj", st, w, dict(ctx, on_wall=False))
        assert "sections" not in off and "footer" not in off and off["flags"]["bell"] is True, \
            "an off-wall window ships flags only - forty sessions must not cost forty bodies"
        s1 = win_sig(win)
        assert s1 == win_sig(window_for("2026-08-27-proj", st, w, ctx)), "an unchanged window has an unchanged sig"
        answered = window_for("2026-08-27-proj", st, w, dict(ctx, open_acts=frozenset({"a1b2", "zzzz"})))
        assert win_sig(answered) != s1, \
            "open_acts is INSIDE the signature: answering a blocker changes nothing in the task's own file"
        beaten = dict(win)
        beaten["flags"] = dict(win["flags"], beat=True)
        assert win_sig(beaten) == s1, \
            "beat is OUTSIDE the signature: a heartbeat changes nothing in any session file"
        broken = window_for("2026-08-27-proj", {"x": {"id": "x", "type": "action", "ts": object()}}, w, ctx)
        assert broken.get("error"), "one unrenderable row costs one card, never the wall"
        assert "sig" not in broken, "a failed build records no signature, so the next tick retries"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 bin/tt_wall.py --selftest`
Expected: FAIL with `NameError: name 'window_for' is not defined`

- [ ] **Step 3: Write the implementation**

Add `import hashlib` to the imports, then:

```python
def win_sig(win):
    """A window's paint signature: a hash of everything the card draws EXCEPT
    the heartbeat.

    Hashing the built payload rather than a hand-kept tuple is what makes the
    old (query, newest, state, changed, open_acts) tuple unnecessary: each of
    those five reaches the payload (as a tt-hit span, a cursor flag, a row, a
    gutter, a blocked banner), so a change in any of them changes the hash by
    construction and a change in none of them cannot."""
    body = dict(win)
    body.pop("sig", None)
    body["flags"] = {k: v for k, v in win["flags"].items() if k != "beat"}
    blob = json.dumps(body, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.blake2s(blob.encode("utf-8"), digest_size=8).hexdigest()


def window_for(key, state, wall, ctx):
    """One window of the frame, or a one-line error card.

    The try/except is per WINDOW because tick()'s guard is all-or-nothing: the
    poll after a bad row fails at exactly the same place, so the freeze is
    permanent and shows only as a stale spinner. The signature is attached
    AFTER a successful build, never before - a half-built window must not be
    marked up to date forever."""
    project, index = ctx["where"]
    try:
        sid = session_label(state, index)
        latest = max((e.get("ts", 0) for e in state.values()), default=0)
        tx = ctx["tx"].get(sid)
        win = {"project": project, "sid": sid, "tx": str(tx) if tx else None,
               "latest": latest,
               "flags": {
                   "bell": any(e.get("type") == "action" and e.get("status") != "done"
                               for e in state.values()),
                   "actv": any(e.get("type") == "task" and e.get("status") != "done"
                               for e in state.values()),
                   "mark": key in set(wall.store.get("marks", [])),
                   "zoom": wall.store.get("zoomed", None) == key,
                   "fold": key in set(wall.store.get("folds", [])),
                   "cur": wall.target() == key,
                   "beat": False,
               }}
        if ctx["on_wall"]:
            # The footer is drawn on a folded card too - tt.css hides .win-b and
            # nothing else - so it is built before the fold branch, not inside it.
            s = M.summarize(state)
            win["footer"] = {
                "cells": list(resolved_cells(s["resolved"], s["recorded"])),
                "text": f'{s["resolved"]}/{s["recorded"]} resolved'
                        + (" · all clear" if not s["open_actions"] and not s["open_tasks"] else "")}
        if ctx["on_wall"] and key not in set(wall.store.get("folds", [])):
            win["sections"] = sections_for(state, key, wall, ctx["query"], ctx["changed"],
                                           ctx["newest"], ctx["open_acts"])
        elif ctx["on_wall"]:
            win["sections"] = []          # folded: titlebar only
        win["sig"] = win_sig(win)
        return win
    except Exception as e:                # noqa: BLE001 - one card, not the wall
        return {"project": project, "sid": "", "tx": None, "latest": 0,
                "flags": {"bell": False, "actv": False, "mark": False, "zoom": False,
                          "fold": False, "cur": False, "beat": False},
                "sections": [], "error": f"could not build window: {type(e).__name__}"}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 bin/tt_wall.py --selftest`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bin/tt_wall.py
git commit -m "feat(wall): windows, their flags, the obligation footer and a signature that excludes the heartbeat"
```

### Task 9: The wall — visibility, packing, columns and the watermark

**Files:**
- Modify: `bin/tt_wall.py`
- Test: `bin/tt_wall.py` (`selftest()`)

**Interfaces:**
- Consumes: `window_for` (Task 8), `cols_for`, `layout_key` (Task 3); `tt_model.group_sessions`, `sort_groups`, `merge_projects`, `weight`, `pack`, `open_action_ids` (all unchanged).
- Produces:
  - `read_states() -> dict[str, dict]` — `{stem: folded}` for every `*.jsonl` in `DATA_DIR`, newest file first, `OSError` tolerated.
  - `Wall.wall_view(now, states, beating, tx) -> tuple[dict, dict]` — `(wall, windows)`, where `wall` is `{"cols": int, "columns": [[key]], "empty": null | {"kind": str, "text": str}}` and `windows` is `{key: window}`. Advances the watermarks as its last act.

- [ ] **Step 1: Write the failing test**

Append to `selftest()`:

```python
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "2026-08-27-alpha.jsonl").write_text(
            '{"id":"a1b2","type":"action","status":"open","background":"Pick","ts":9,"sid":"4f2a"}\n')
        (d / "2026-08-27-beta.jsonl").write_text(
            '{"id":"b3c4","type":"task","status":"done","what":"ran","ts":8,"sid":"9b1c"}\n')
        M.DATA_DIR = d
        M._fold_cache.clear()      # fold_cached is keyed on (mtime, size) per path
        states = read_states()
        assert set(states) == {"2026-08-27-alpha", "2026-08-27-beta"}, "every jsonl in the data dir"

        w = Wall(Store(d / ".ui" / "wall.json"), tt_config.DEFAULTS)
        w.wall_width = 1400
        wall, wins = w.wall_view(100.0, states, set(), {})
        assert wall["cols"] == 2 and sorted(sum(wall["columns"], [])) == ["alpha", "beta"], \
            "merged is the default view: one window per project, packed into two columns"
        assert wall["empty"] is None, "a wall with windows on it says nothing"
        assert "sections" in wins["alpha"], "a window on the wall carries its body"

        w.apply({"do": "key", "k": "!"})
        wall, wins = w.wall_view(100.0, states, set(), {})
        assert sum(wall["columns"], []) == ["alpha"], "needs-me drops a window with nothing open"
        assert "sections" not in wins["beta"] and wins["beta"]["flags"]["bell"] is False, \
            "an off-wall window still ships its flags, and nothing else"
        w.apply({"do": "key", "k": "!"})

        w.apply({"do": "scope", "project": "nope"})
        wall, _ = w.wall_view(100.0, states, set(), {})
        assert wall["columns"] == [] and wall["empty"]["kind"] == "scope", "an empty wall says why"
        assert wall["empty"]["text"] == "nothing under nope — clear the scope to see every session", \
            "the exact message dash.py shows"
        w.apply({"do": "scope", "project": None})

        w.apply({"do": "window", "key": "alpha", "act": "zoom"})
        wall, _ = w.wall_view(100.0, states, set(), {})
        assert wall["cols"] == 1 and wall["columns"] == [["alpha"]], "zoom forces one column and one window"
        w.apply({"do": "window", "key": "alpha", "act": "zoom"})

        w.wall_width = 600
        wall, _ = w.wall_view(100.0, states, set(), {})
        assert wall["cols"] == 1, "a wall under 900px packs one column whatever the preference says"
        w.wall_width = 1400

        before = [list(c) for c in w.wall_view(100.0, states, set(), {})[0]["columns"]]
        assert [list(c) for c in w.wall_view(101.0, states, set(), {})[0]["columns"]] == before, \
            "an unchanged layout_key re-uses the pack: a window never moves under a reader"

        # watermarks
        w2 = Wall(Store(d / ".ui" / "w2.json"), tt_config.DEFAULTS)
        w2.opened_ts = 0.0
        w2.wall_view(100.0, states, set(), {})
        assert w2.seen_at == {}, "an untouched page never advances a watermark"
        _, wins = w2.wall_view(100.0, states, set(), {})
        assert wins["alpha"]["sections"][0]["rows"][0]["changed"] == "act", "so the gutter stays"
        w2.apply({"do": "touch"})
        w2.wall_view(100.9, states, set(), {})
        assert w2.seen_at["alpha"] == 99, "the watermark advances by int(now)-1, never by now"
        assert w2.touched is False, "the touch is consumed"
        _, wins = w2.wall_view(101.0, states, set(), {})
        assert wins["alpha"]["sections"][0]["rows"][0]["changed"] == "", "and the gutter clears"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 bin/tt_wall.py --selftest`
Expected: FAIL with `NameError: name 'read_states' is not defined`

- [ ] **Step 3: Write the implementation**

```python
def read_states():
    """Every session file in the data dir, folded. Newest file first, exactly
    as poll() globs today, and with the same (mtime, size) cache underneath -
    which is why a 2 s tick over a directory of append-only files is free."""
    try:
        paths = sorted(M.DATA_DIR.glob("*.jsonl"), reverse=True)
    except OSError:
        return {}
    out = {}
    for p in paths:
        out[p.stem] = M.fold_cached(p)
    return out
```

and, as methods on `Wall`:

```python
    def wall_view(self, now, states, beating, tx):
        """The wall and every window, and the watermark advance that must
        happen after both."""
        s = self.store
        groups = M.group_sessions(list(states.items()))
        sort = s.get("sort", "recent")
        ordered = M.sort_groups(groups, sort)
        self.apply_fold_rules(ordered)     # before the frame, or a forced-open group ships folded
        merged = bool(s.get("merged", self.cfg["ui"]["view"] == "merged"))
        if merged:
            wall_states = M.merge_projects(list(states.items()))
            order = [g["project"] for g in ordered]
            where = {g["project"]: (g["project"], len(g["sessions"])) for g in ordered}
            opens = {g["project"]: g["open_actions"] for g in ordered}
        else:
            wall_states = states
            order = [x["key"] for g in ordered for x in g["sessions"]]
            where = {x["key"]: (g["project"], x["index"]) for g in ordered for x in g["sessions"]}
            opens = {x["key"]: x["summary"]["open_actions"] for g in ordered for x in g["sessions"]}

        scope, needs_me = s.get("scope", None), bool(s.get("needs_me", False))
        zoomed = s.get("zoomed", None)
        # Scope, needs-me and zoom choose what is on the wall - all three are
        # explicit choices about the view. The query never does: the filter
        # dims, it never hides, so an open action cannot leave the wall.
        visible = [k for k in order if scope in (None, where[k][0])]
        if needs_me:
            visible = [k for k in visible if opens.get(k)]
        if zoomed in wall_states:
            visible = [zoomed]
        empty = None
        if not visible:
            empty = ({"kind": "needs_me",
                      "text": "nothing needs you right now — press ! to show every session"} if needs_me
                     else {"kind": "scope",
                           "text": f"nothing under {scope} — clear the scope to see every session"} if scope
                     else {"kind": "nothing", "text": "no sessions yet — record something with table-talk"})
        drawer_open = bool(s.get("drawer_open", self.cfg["ui"]["drawer_open"]))
        cols = (1 if zoomed in wall_states
                else cols_for(self.wall_width, s.get("cols", self.cfg["ui"]["columns"])))
        marks, folds = set(s.get("marks", [])), set(s.get("folds", []))
        lk = layout_key(visible, cols, marks, folds, zoomed, scope, sort, drawer_open)
        if self.layout is None or self.layout[0] != lk:
            # a folded window is a titlebar: costing it its full content weight
            # would leave the packer balancing around height that is not drawn
            weights = {k: 1 if k in folds else M.weight(v) for k, v in wall_states.items()}
            self.layout = (lk, M.pack(visible, cols, weights, marks) if visible else [])
        columns = [list(c) for c in self.layout[1]]

        # Across EVERY file, not just what is on the wall: a task's blocker can
        # be an action recorded on another day.
        open_acts = frozenset(M.open_action_ids(states.values()))
        newest, newest_ts = None, -1
        for k in visible:
            for ev in wall_states[k].values():
                if (ev.get("type") == "action" and ev.get("status") != "done"
                        and ev.get("ts", 0) > newest_ts):
                    newest, newest_ts = str(ev["id"]), ev.get("ts", 0)

        was_on_wall = self.on_wall
        windows = {}
        for k in order:
            on = k in visible
            windows[k] = window_for(k, wall_states[k], self, {
                "query": self.query,
                "changed": changed_ids(wall_states[k], self.seen_at.get(k, self.opened_ts)) if on else set(),
                "newest": newest, "open_acts": open_acts, "tx": tx,
                "on_wall": on, "where": where[k]})
            # Outside the signature deliberately: a heartbeat changes nothing in
            # any session file, so a window would never repaint for it.
            windows[k]["flags"]["beat"] = windows[k]["sid"] in beating
        self.on_wall = visible

        # Only now, only if the page was touched since the last frame, and only
        # for windows that were ALSO on the previous wall: the interaction that
        # brought a window back (Escape out of a zoom) happened while it was
        # still off screen. int(now) - 1, not now: bin/table-talk stamps ts in
        # WHOLE seconds, so a float watermark could swallow an event written in
        # the same second. One second back costs a redundant gutter for one
        # frame and never a missed one.
        if self.touched:
            self.touched = False
            for k in visible:
                if k in was_on_wall:
                    self.seen_at[k] = int(now) - 1
        return {"cols": cols, "columns": columns, "empty": empty}, windows
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 bin/tt_wall.py --selftest`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bin/tt_wall.py
git commit -m "feat(wall): visibility, packing, the column clamp and the seen watermark"
```

### Task 10: The drawer — tree, meters, rail and the context footer

**Files:**
- Modify: `bin/tt_wall.py`
- Test: `bin/tt_wall.py` (`selftest()`)

**Interfaces:**
- Consumes: `abbrev`, `dim` (Task 3), `nearest_claude_md` (Task 4); `tt_model.roll_up` (unchanged), `tt_config.CONFIG_PATH` (unchanged).
- Produces: `Wall.drawer_view(ordered, states, wall_states, visible) -> dict` and `context_footer(cfg_path) -> list[dict]`.

Drawer shape:

```json
{"open":true,"sig":"41ba…","sort":"recent","hits":"4/61 rows match","sessions":7,"projects":2,
 "projects_list":[{"project":"gpn-yeast","abbrev":"gpn","folded":false,"scoped":false,"multi":true,
                   "meter":{"open":3,"tasks":1,"pct":62},
                   "sessions":[{"key":"2026-09-08-gpn-yeast","date":"2026-09-08","latest":1757337421,
                                "meter":{…}}]}],
 "ctx":[{"label":"📘 CLAUDE.md","target":"/home/u/p/CLAUDE.md"},{"label":"⚙ settings","act":"settings"}]}
```

The htop meter is `[####    ]NN%`, and it is the one bar in this app that was never
text: `dash.py`'s `meter_row` (`table-talk-dash.py:2807-2820`) draws literal `[` and
`]` labels around a `.trk` div whose `<i>` is sized in **percent by CSS**
(`bin/tt.css:127-133`). So the frame ships `open`, `tasks` and `pct` and nothing
else — a glyph `cells` tuple here would be a field the view never reads, on every
drawer row, on every frame.

- [ ] **Step 1: Write the failing test**

Append to `selftest()`:

```python
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "2026-08-26-alpha.jsonl").write_text(
            '{"id":"a1b2","type":"action","status":"open","background":"Pick a cadence","ts":9,"sid":"4f2a"}\n')
        (d / "2026-08-27-alpha.jsonl").write_text(
            '{"id":"c3d4","type":"task","status":"done","what":"ran","ts":8,"sid":"9b1c"}\n')
        (d / "2026-08-27-beta.jsonl").write_text(
            '{"id":"e5f6","type":"term","term":"fold","intuitive":"merge","technical":"t","ts":7}\n')
        M.DATA_DIR = d
        w = Wall(Store(d / ".ui" / "wall.json"), tt_config.DEFAULTS)
        states = read_states()
        ordered = M.sort_groups(M.group_sessions(list(states.items())), "recent")
        dw = w.drawer_view(ordered, states, states, ["2026-08-27-alpha"])
        assert dw["sessions"] == 3 and dw["projects"] == 2, "the header counts files and projects"
        alpha = [p for p in dw["projects_list"] if p["project"] == "alpha"][0]
        beta = [p for p in dw["projects_list"] if p["project"] == "beta"][0]
        assert alpha["multi"] is True and beta["multi"] is False, \
            "the fold triangle exists only when a project has more than one session"
        assert alpha["abbrev"] == "alp", "the collapsed rail's tag"
        assert alpha["meter"]["open"] == 1 and alpha["meter"]["tasks"] == 0, "the ● and ▶ badges"
        assert set(alpha["meter"]) == {"open", "tasks", "pct"}, \
            "the drawer's htop bar is CSS-sized from pct: no glyph cells nobody reads"
        assert [s["key"] for s in alpha["sessions"]] == ["2026-08-27-alpha", "2026-08-26-alpha"], \
            "sessions are newest file first"
        assert alpha["sessions"][0]["date"] == "2026-08-27" and "latest" in alpha["sessions"][0], \
            "a session row is a date and an ABSOLUTE stamp"
        assert dw["hits"] == "", "no query, no hit count"
        w.apply({"do": "query", "q": "cadence"})
        dw = w.drawer_view(ordered, states, states, ["2026-08-27-alpha"])
        assert dw["hits"].endswith(" rows match") and dw["hits"].startswith("0/"), \
            "the hit count counts rows on the WALL, and only alpha's newer file is on it"
        sig = dw["sig"]
        assert w.drawer_view(ordered, states, states, ["2026-08-27-alpha"])["sig"] == sig, \
            "an unchanged tree has an unchanged signature"
        w.apply({"do": "group_fold", "project": "alpha"})
        assert w.drawer_view(ordered, states, states, ["2026-08-27-alpha"])["sig"] != sig, \
            "folding a group changes it"
        ctx = context_footer(None)
        assert all(("target" in c) != ("act" in c) for c in ctx), \
            "a footer entry either opens a path or fires an action, never both"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 bin/tt_wall.py --selftest`
Expected: FAIL with `AttributeError: 'Wall' object has no attribute 'drawer_view'`

- [ ] **Step 3: Write the implementation**

```python
def context_footer(cfg_path):
    """The drawer's footer links, built fresh each frame because a CLAUDE.md
    can appear while the brain runs. Entirely absent - not an empty box - when
    nothing exists; an empty footer is noise."""
    out = []
    if (nearest := nearest_claude_md(Path.cwd(), Path.home())):
        out.append({"label": "📘 CLAUDE.md", "target": str(nearest)})
    home_claude = Path.home() / ".claude" / "CLAUDE.md"
    if home_claude.is_file():
        out.append({"label": "📗 ~/.claude/CLAUDE.md", "target": str(home_claude)})
    # The session-memory directory is a DIRECTORY and path_spans only ever
    # returns files, so link the representative MEMORY.md inside it.
    mem = (Path.home() / ".claude" / "projects" /
           str(Path.cwd()).replace("/", "-") / "memory" / "MEMORY.md")
    if mem.is_file():
        out.append({"label": "🧠 memory", "target": str(mem)})
    if cfg_path:
        # A FORM for the settings a form can validate; the file itself stays one
        # click away, because colour tokens and extra_roots belong there.
        out.append({"label": "⚙ settings", "act": "settings"})
        out.append({"label": "📄 settings file", "target": str(cfg_path)})
        if (ref := Path(cfg_path).parent / "config.example.toml").is_file():
            out.append({"label": "📑 settings ref", "target": str(ref)})
    return out
```

and, as a method on `Wall`:

```python
    def drawer_view(self, ordered, states, wall_states, visible):
        """The session tree, its meters and the context footer."""
        s = self.store
        folded = set(s.get("groups_folded", []))
        scope = s.get("scope", None)

        def meter(summary):
            # pct only: .trk's <i> is sized with a CSS percentage, exactly as
            # dash.py's meter_row does it, so the brackets and the bar are the
            # view's business and tt.css needs no change.
            return {"open": summary.get("open_actions", 0), "tasks": summary.get("open_tasks", 0),
                    "pct": summary.get("pct", 100)}

        projects = []
        for g in ordered:
            sessions = [{"key": x["key"], "date": M.parse_stem(x["key"])[0],
                         "latest": x["summary"]["latest"], "meter": meter(x["summary"])}
                        for x in g["sessions"]]
            projects.append({
                "project": g["project"], "abbrev": abbrev(g["project"]),
                "folded": g["project"] in folded, "scoped": scope == g["project"],
                # a group of one is noise: no triangle
                "multi": len(g["sessions"]) > 1,
                "meter": meter(M.roll_up([x["summary"] for x in g["sessions"]])),
                "sessions": sessions})

        rows = matched = 0
        for k in visible:
            for ev in wall_states.get(k, {}).values():
                if ev.get("type") in ("action", "task", "term", "diagram"):
                    rows += 1
                    matched += not dim(ev, self.query)
        cfg_path = tt_config.CONFIG_PATH if tt_config.CONFIG_PATH.is_file() else None
        out = {"open": bool(s.get("drawer_open", self.cfg["ui"]["drawer_open"])),
               "sort": s.get("sort", "recent"),
               "hits": f"{matched}/{rows} rows match" if self.query else "",
               "sessions": sum(len(g["sessions"]) for g in ordered), "projects": len(ordered),
               "projects_list": projects, "ctx": context_footer(cfg_path)}
        out["sig"] = hashlib.blake2s(
            json.dumps(out, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8"),
            digest_size=8).hexdigest()
        return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 bin/tt_wall.py --selftest`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bin/tt_wall.py
git commit -m "feat(wall): the drawer tree, its meters and the context footer"
```

### Task 11: Statusline, theme and the settings form

**Files:**
- Modify: `bin/tt_wall.py`
- Test: `bin/tt_wall.py` (`selftest()`)

**Interfaces:**
- Consumes: `tally_text` (Task 3), `KEYMAP` (Task 5); `tt_config.form_fields()`, `tt_config.DEFAULTS`, `tt_config.valid_colour` (all unchanged).
- Produces:
  - `Wall.status_view(states, open_acts, wall) -> dict`.
  - `Wall.settings_view() -> dict` — `{"fields": [{"key","kind","choices"|"bounds","value"}]}`; an infinite bound serialises as `null`.
  - `form_updates(current, submitted) -> dict[str, object]` and `coerce(kind, bounds, value, like) -> object | None`, both verbatim from `dash.py:665-712`.
  - `theme_css(theme) -> str`, verbatim from `dash.py:56-77`.

Status shape: `{"tally":{"open":3,"running":1,"blocked":1},"text":"●3 open  ▶1 running  ⏸1 blocked","poll_seconds":2.0,"scope":null,"cols":2,"keymap":{"m":{"label":"mark","on":false},…},"port_warn":null}`.

`spin` and `last_ok` are **not** here — they are the server's tick fields (see "Deviations", item 1).

- [ ] **Step 1: Write the failing test**

Append to `selftest()`:

```python
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "2026-08-27-alpha.jsonl").write_text(
            '{"id":"a1b2","type":"action","status":"open","background":"Pick","ts":9}\n'
            '{"id":"t111","type":"task","status":"open","what":"run","ts":8}\n'
            '{"id":"t222","type":"task","status":"open","what":"wait","blocked_on":"a1b2","ts":7}\n')
        M.DATA_DIR = d
        w = Wall(Store(d / ".ui" / "wall.json"), tt_config.DEFAULTS)
        states = read_states()
        open_acts = frozenset(M.open_action_ids(states.values()))
        st = w.status_view(states, open_acts, {"cols": 2})
        assert st["tally"] == {"open": 1, "running": 1, "blocked": 1}, \
            "blocked is subtracted from running: a task waiting on a decision is not in flight"
        assert st["text"] == "●1 open  ▶1 running  ⏸1 blocked", "the rendered tally"
        assert st["cols"] == 2 and st["scope"] is None, "the effective column count and the scope chip"
        assert set(st["keymap"]) == set(KEYMAP) - {"/", "Escape"}, \
            "a chip per key except filter and unzoom, which have their own affordance"
        assert st["keymap"]["m"] == {"label": "mark", "on": False}, "a chip carries its label and its state"
        w.apply({"do": "key", "k": "!"})
        assert w.status_view(states, open_acts, {"cols": 2})["keymap"]["!"]["on"] is True, \
            "needs-me and merge are the only two chips with visible state"

        f = w.settings_view()["fields"]
        keys = {x["key"] for x in f}
        assert keys == {k for k, _, _ in tt_config.form_fields()}, "the form is the validator's own list"
        port = [x for x in f if x["key"] == "server.port"][0]
        assert port["kind"] == "number" and port["bounds"] == [1, 65535] and port["value"] == 8731, \
            "a number field carries the VALIDATOR's bounds and the current value"
        poll = [x for x in f if x["key"] == "server.poll_seconds"][0]
        assert poll["bounds"][1] is None, "an infinite bound is null: JSON has no Infinity and JSON.parse rejects it"
        assert json.loads(json.dumps(w.settings_view())), "the settings block round-trips through JSON"

    assert form_updates({"server": {"port": 8731}}, {"server.port": 8731}) == {}, "an unchanged key is not written"
    assert form_updates({"server": {"port": 8731}}, {"server.port": 8899}) == {"server.port": 8899}, "a changed key is"
    assert form_updates({"server": {"port": 8731}}, {"server.port": None}) == {}, "an out-of-bounds value is skipped"
    assert coerce("number", (1, 65535), 8899.0, 8731) == 8899 and isinstance(coerce("number", (1, 65535), 8899.0, 8731), int), \
        "a number widget's float comes back in the type the config expects"
    assert coerce("number", (0.2, float("inf")), 2.0, 2.0) == 2.0, "a float stays a float"
    assert coerce("number", (1, 65535), 0, 8731) is None, "out of bounds means leave it alone"
    assert coerce("choice", None, "flat", "merged") == "flat", "a choice passes straight through"
    assert theme_css({"dark": dict(tt_config.DEFAULTS["theme"]["dark"])}) == "", \
        "a restatement of the default emits nothing - inherit is the correct behaviour"
    assert "--ink:#abcdef" in theme_css({"dark": {"ink": "#abcdef"}}), "a differing token is emitted"
    assert theme_css({"dark": {"ink": "red;}body{display:none"}}) == "", \
        "a value that is not a hex colour is refused on the way OUT as well as on the way in"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 bin/tt_wall.py --selftest`
Expected: FAIL with `AttributeError: 'Wall' object has no attribute 'status_view'`

- [ ] **Step 3: Write the implementation**

```python
# The two chips that have their own affordance elsewhere: the search box, and Esc.
_NO_CHIP = ("/", "Escape")


def form_updates(current, submitted):
    """Only the settings that actually CHANGED, as {"section.key": value}.

    `current` is the LOADED config, so every default is present in it whether or
    not the file mentions it. Writing back everything the form showed would
    materialise every default as an explicit line the user never chose."""
    out = {}
    for dotted, value in submitted.items():
        section, _, key = dotted.rpartition(".")
        if value is None:
            continue
        if current.get(section, {}).get(key) != value:
            out[dotted] = value
    return out


def coerce(kind, bounds, value, like):
    """A form value in the type the config expects, or None to leave it alone.

    Typed from `like` - the value currently loaded - and NOT from the bounds. A
    number widget hands back a float for everything, and the loader drops a
    value whose type does not match its default, so the setting would silently
    not take."""
    if kind != "number":
        return value
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    lo, hi = bounds
    if not (lo <= num <= hi):
        return None
    return float(num) if isinstance(like, float) else int(num)


def theme_css(theme):
    """The config's palette as one stylesheet, holding only the tokens that
    DIFFER from tt.css's own. Every value is re-checked with valid_colour HERE,
    at the point of emission: load() validates on the way in, this validates on
    the way out, and a config file is a second untrusted route into the
    stylesheet. The comprehension runs over the DEFAULTS keys rather than the
    file's, so a token NAME out of the file is only ever matched, never
    interpolated."""
    def block(sel, mode):
        base = tt_config.DEFAULTS["theme"][mode]
        got = theme.get(mode) or {}
        decls = "".join(f"--{k}:{got[k]};" for k in base
                        if k in got and got[k] != base[k] and tt_config.valid_colour(got[k]))
        return f"{sel}{{{decls}}}" if decls else ""

    return block(":root", "light") + block("body.body--dark", "dark")
```

and, as methods on `Wall`:

```python
    def status_view(self, states, open_acts, wall):
        """The statusline. Counts EVERY session, not just what is on the wall:
        scope and zoom are choices about the view, and a number that shrinks
        because you zoomed would be a lie."""
        groups = M.group_sessions(list(states.items()))
        blocked = sum(1 for st in states.values() for e in st.values()
                      if e.get("type") == "task" and e.get("status") != "done"
                      and M.blocked_by(e, open_acts))
        opens = sum(g["open_actions"] for g in groups)
        running = sum(g["open_tasks"] for g in groups) - blocked
        s = self.store
        on = {"needs-me": bool(s.get("needs_me", False)),
              "merge": bool(s.get("merged", self.cfg["ui"]["view"] == "merged"))}
        return {"tally": {"open": opens, "running": running, "blocked": blocked},
                "text": tally_text(opens, running, blocked),
                "poll_seconds": self.cfg["server"]["poll_seconds"],
                "scope": s.get("scope", None), "cols": wall["cols"],
                "keymap": {k: {"label": v, "on": on.get(v, False)}
                           for k, v in KEYMAP.items() if k not in _NO_CHIP},
                "port_warn": self.warn}

    def settings_view(self):
        """The settings form, derived entirely from tt_config.form_fields() -
        never a second hardcoded list that could drift from the validator."""
        loaded = self.cfg
        out = []
        for key, kind, arg in tt_config.form_fields():
            section, _, name = key.rpartition(".")
            field = {"key": key, "kind": kind, "value": loaded.get(section, {}).get(name)}
            if kind == "choice":
                field["choices"] = list(arg)
            else:
                # JSON has no Infinity, and JSON.parse rejects the token
                # json.dumps would emit. null is "no bound" to the view.
                field["bounds"] = [None if x == float("inf") else x for x in arg]
            out.append(field)
        return {"fields": out}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 bin/tt_wall.py --selftest`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bin/tt_wall.py
git commit -m "feat(wall): the statusline, the theme mode and the settings form"
```

### Task 12: `frame()`, `--once`, and `table-talk state --once`

**Files:**
- Modify: `bin/tt_wall.py`
- Modify: `bin/table-talk` (a `state` subparser and `cmd_state`)
- Test: `bin/tt_wall.py` (`selftest()`), `bin/table-talk` (`selftest()`)

**Interfaces:**
- Consumes: `Wall.wall_view` (Task 9), `Wall.drawer_view` (Task 10), `Wall.status_view`, `Wall.settings_view` (Task 11).
- Produces:
  - `FRAME_V = 1`.
  - `Wall.frame(now, states, tx=None, beating=None) -> dict` — the whole document, minus the four clock fields the server adds (`t`, `polls_ok`, `status.spin`, `status.last_ok`).
  - `bin/table-talk`: `table-talk state --once` prints one frame to stdout and exits 0.

- [ ] **Step 1: Write the failing test**

Append to `tt_wall.selftest()`:

```python
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "2026-08-27-alpha.jsonl").write_text(
            '{"id":"a1b2","type":"action","status":"open","background":"Pick a cadence",'
            '"intuitive":"how often","why":"w","rec":"r","ts":9,"sid":"4f2a"}\n'
            '{"id":"t111","type":"task","status":"open","what":"run","pct":50,"read_ts":8,"ts":8,"sid":"4f2a"}\n')
        M.DATA_DIR = d
        M._fold_cache.clear()
        w = Wall(Store(d / ".ui" / "wall.json"), tt_config.DEFAULTS)
        w.wall_width = 1400
        states = read_states()
        f = w.frame(100.0, states, tx={}, beating=set())
        assert f["v"] == 1, "the frame carries its version, and the view refuses one it does not know"
        assert set(f) == {"v", "wall", "windows", "drawer", "status", "theme", "settings", "focus", "warn"}, \
            "the whole document, and nothing the server has to add"
        assert f["theme"]["mode"] == "system", "the theme mode comes from the config's default"
        w.store.put("theme", "nonsense")
        assert w.frame(100.0, states, tx={}, beating=set())["theme"]["mode"] == "system", \
            "a hand-edited store value must not crash startup"
        w.store.put("theme", "dark")

        blob = json.dumps(w.frame(100.0, states, tx={}, beating=set()), ensure_ascii=False)
        assert re.search(r'"[a-z_]*(ago|since)[a-z_]*"\s*:', blob) is None, \
            "NO relative-time KEY leaks into the frame"
        assert re.search(r"\d+[smhd] ago", blob) is None, \
            "NO rendered relative time leaks into the frame - it would change the payload every second"
        assert "just now" not in blob, "nor the zero case of one"

        w.apply({"do": "focus", "key": "alpha"})
        assert w.frame(100.0, states, tx={}, beating=set())["focus"] == "alpha", "a focus request rides once"
        assert w.frame(100.0, states, tx={}, beating=set())["focus"] is None, "and only once"

        a = w.frame(100.0, states, tx={}, beating=set())
        b = w.frame(200.0, states, tx={}, beating=set())
        assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True), \
            "SKIP STABILITY: two frames over unchanged files are byte-identical, whatever the clock says"
        c = w.frame(300.0, states, tx={}, beating={"4f2a"})
        assert c["windows"]["alpha"]["sig"] == a["windows"]["alpha"]["sig"], \
            "a heartbeat does not change a window signature"
        assert c["windows"]["alpha"]["flags"]["beat"] is True, "but it does light the ◉ flag"
```

Add to `bin/table-talk`'s `selftest()`, inside the `with tempfile.TemporaryDirectory() as td:` block:

```python
    # Guarded by the version, deliberately: the CLI's 3.10 promise is about
    # RECORDING, and the cli-oldest-python CI job runs this whole selftest on
    # 3.10, where the brain cannot run at all - it needs tomllib through
    # tt_config. Recording must never depend on being able to read the config.
    if sys.version_info >= (3, 11):
        out = subprocess.run([sys.executable, str(Path(__file__).resolve()), "state", "--once"],
                             capture_output=True, text=True,
                             env={**os.environ, "TABLE_TALK_DIR": td})
        assert out.returncode == 0, f"state --once must exit 0, got {out.returncode}: {out.stderr}"
        assert json.loads(out.stdout)["v"] == 1, "state --once prints one frame of the wall's state"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 bin/tt_wall.py --selftest && python3 bin/table-talk --selftest`
Expected: FAIL — first `AttributeError: 'Wall' object has no attribute 'frame'`

- [ ] **Step 3: Write the implementation**

In `bin/tt_wall.py`, add `FRAME_V = 1` beside the other constants, this method on `Wall`:

```python
    def frame(self, now, states, tx=None, beating=None):
        """One frame: everything the view draws, with no rendered relative time
        anywhere in it.

        Absolute stamps only. Two reasons, both load-bearing: a rendered "3m"
        or a live clock would change the payload every tick and make frame
        skipping worthless, and a client-rendered relative time stays honest on
        a card that has not repainted for an hour."""
        wall, windows = self.wall_view(now, states, beating or set(), tx or {})
        ordered = M.sort_groups(M.group_sessions(list(states.items())),
                                self.store.get("sort", "recent"))
        merged = bool(self.store.get("merged", self.cfg["ui"]["view"] == "merged"))
        wall_states = M.merge_projects(list(states.items())) if merged else states
        open_acts = frozenset(M.open_action_ids(states.values()))
        mode = self.store.get("theme", self.cfg["theme"]["default"])
        out = {"v": FRAME_V, "wall": wall, "windows": windows,
               "drawer": self.drawer_view(ordered, states, wall_states, self.on_wall),
               "status": self.status_view(states, open_acts, wall),
               "theme": {"mode": mode if mode in ("system", "light", "dark") else "system"},
               "settings": self.settings_view(),
               "focus": self.focus, "warn": self.warn}
        self.focus = None      # one-shot: a scroll happens once, not every tick
        return out
```

and replace the whole `__main__` block with:

```python
if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--once", action="store_true", help="print one frame and exit")
    a = ap.parse_args()
    if a.selftest:
        selftest()
    elif a.once:
        cfg = tt_config.load()
        set_roots(cfg)
        now = time.time()          # the clock BEFORE the files, here as everywhere
        w = Wall(Store(), cfg)
        print(json.dumps(w.frame(now, read_states(), tx=transcripts(), beating=live_sessions(now)),
                         ensure_ascii=False))
    else:
        ap.print_help()
```

In `bin/table-talk`, add the subparser inside `main()` after the `url` one:

```python
    stt = sub.add_parser("state", help=argparse.SUPPRESS)
    stt.add_argument("--once", action="store_true",
                     help="print one frame of the wall's state as JSON and exit")
```

the dispatch beside the other `elif`s:

```python
    elif args.cmd == "state":
        if not args.once:
            sys.exit("usage: table-talk state --once")
        cmd_state(once=True)
```

and the command itself, beside `cmd_serve`:

```python
def cmd_state(once=False):
    """The wall's state, as JSON. Useful on its own:
    `table-talk state --once | jq .status.tally`.

    exec rather than import: tt_wall needs tomllib through tt_config, which is
    3.11+, and this CLI keeps its 3.10 promise by never importing either at
    module scope."""
    wall = Path(__file__).resolve().parent / "tt_wall.py"
    os.execv(sys.executable, [sys.executable, str(wall), "--once"])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 bin/tt_wall.py --selftest && python3 bin/table-talk --selftest`
Expected: PASS

- [ ] **Step 5: Prove the phase ships something**

Run: `TABLE_TALK_DIR=docs/demo python3 bin/table-talk state --once | python3 -m json.tool | head -30`
Expected: a frame with `"v": 1` and two projects under `windows`.

- [ ] **Step 6: Commit**

```bash
git add bin/tt_wall.py bin/table-talk
git commit -m "feat(wall): one JSON frame, and table-talk state --once to print it"
```

### Task 13: Golden fixtures

**Files:**
- Modify: `bin/tt_wall.py`
- Create: `tests/frames.json`
- Modify: `test.sh`
- Test: `bin/tt_wall.py` (`selftest()`)

**Interfaces:**
- Consumes: `Wall.frame` (Task 12).
- Produces: `FIXTURE_NOW = 1800000000.0`; `dump_fixtures(out_path, demo_dir) -> str` (the exact text written); `python3 bin/tt_wall.py --dump-fixtures` writing `tests/frames.json`. Task 30's CI job runs the dump and `git diff --exit-code -- tests/frames.json`.

- [ ] **Step 1: Write the failing test**

Append to `selftest()`:

```python
    repo = Path(__file__).resolve().parent.parent
    demo, golden = repo / "docs" / "demo", repo / "tests" / "frames.json"
    if demo.is_dir():
        text = dump_fixtures(None, demo)
        assert "<DEMO>" in text and str(demo.resolve()) not in text, \
            "no machine-dependent path survives into a committed fixture"
        assert '"ctx"' not in text and '"settings"' not in text, \
            "the footer and the settings form read $HOME and the user's own config: neither is golden"
        assert text == dump_fixtures(None, demo), "the dump is deterministic"
        assert golden.read_text() == text, \
            "the committed frames.json matches this build - re-run --dump-fixtures and review the diff"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 bin/tt_wall.py --selftest`
Expected: FAIL with `NameError: name 'dump_fixtures' is not defined`

- [ ] **Step 3: Write the implementation**

```python
# A clock far enough in the future that every demo row is old news, so nothing
# in a fixture depends on when it was generated.
FIXTURE_NOW = 1800000000.0


def dump_fixtures(out_path, demo_dir):
    """Golden frames from docs/demo at a fixed clock. Returns the text, and
    writes it when out_path is given.

    Everything machine-dependent is neutralised, or the file could never be
    identical on two boxes: no transcripts, link roots holding only the demo
    dir, no context footer (it names paths under $HOME), no settings block (it
    reads the user's own config), no drawer signature (it hashes the footer),
    and the demo dir's absolute path replaced by <DEMO>."""
    import tempfile
    global ROOTS
    M.DATA_DIR = Path(demo_dir).resolve()
    M._fold_cache.clear()
    ROOTS = [M.DATA_DIR]
    out = {}
    with tempfile.TemporaryDirectory() as td:
        for view in ("merged", "flat"):
            w = Wall(Store(Path(td) / f"{view}.json"), tt_config.DEFAULTS)
            w.store.put("merged", view == "merged")
            w.opened_ts = 0.0            # a fixture shows the gutters, not a quiet first look
            w.wall_width = 1400
            f = w.frame(FIXTURE_NOW, read_states(), tx={}, beating=set())
            f["drawer"].pop("ctx"), f["drawer"].pop("sig"), f.pop("settings")
            out[view] = f
    text = json.dumps(out, indent=1, sort_keys=True, ensure_ascii=False)
    text = text.replace(str(M.DATA_DIR), "<DEMO>") + "\n"
    if out_path:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_text(text)
    return text
```

and the `__main__` block becomes:

```python
if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--once", action="store_true", help="print one frame and exit")
    ap.add_argument("--dump-fixtures", action="store_true",
                    help="rewrite tests/frames.json from docs/demo at a fixed clock")
    a = ap.parse_args()
    if a.selftest:
        selftest()
    elif a.dump_fixtures:
        repo = Path(__file__).resolve().parent.parent
        dump_fixtures(repo / "tests" / "frames.json", repo / "docs" / "demo")
        print("wrote tests/frames.json")
    elif a.once:
        cfg = tt_config.load()
        set_roots(cfg)
        now = time.time()
        w = Wall(Store(), cfg)
        print(json.dumps(w.frame(now, read_states(), tx=transcripts(), beating=live_sessions(now)),
                         ensure_ascii=False))
    else:
        ap.print_help()
```

- [ ] **Step 4: Generate the fixture and run the test**

Run: `python3 bin/tt_wall.py --dump-fixtures && python3 bin/tt_wall.py --selftest`
Expected: `wrote tests/frames.json`, then PASS

- [ ] **Step 5: Wire the new selftests into test.sh**

Add to `test.sh`, after the `tt_config.py` selftest line and before the
`uv run --script ... table-talk-dash.py --selftest` line:

```sh
python3 "$here/bin/tt_wall.py" --selftest
```

Run: `./test.sh`
Expected: `all selftests passed`

- [ ] **Step 6: Commit**

```bash
git add bin/tt_wall.py tests/frames.json test.sh
git commit -m "test(wall): golden frames from docs/demo at a fixed clock"
```

---

## Phase 2 — the server

Four tasks. The phase ships a second, ugly, fully live view on port 8899 with the real dashboard still on 8731.

### Task 14: `tt_serve` — static files, `GET /` and the 404 rule

**Files:**
- Create: `bin/tt_serve.py`
- Create: `bin/web/index.html`
- Test: `bin/tt_serve.py` (`selftest()`)

**Interfaces:**
- Consumes: `tt_wall.theme_css` (Task 11), `tt_config.load` (unchanged).
- Produces:
  - `STATIC: dict[str, tuple[Path, str]]` — the five-name whitelist, name → `(path, content_type)`. `tt.css` maps to `bin/tt.css`, the others to `bin/web/`.
  - `Handler(BaseHTTPRequestHandler)` with `do_GET`.
  - `make_server(port, host="127.0.0.1") -> ThreadingHTTPServer`.
  - `ensure_config(path=None, example=None) -> Path | None`, verbatim from `dash.py:817-848`.

- [ ] **Step 1: Write the failing test**

Create `bin/tt_serve.py` with the PEP 723 header, imports and:

```python
def selftest():
    import http.client, threading, tempfile
    srv = make_server(0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = srv.server_address[1]

    def get(path, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        c.request("GET", path, headers=headers or {})
        r = c.getresponse()
        return r.status, r.read()

    code, body = get("/")
    assert code == 200, "SKILL.md:222 hard-codes a curl liveness check against GET / - it answers 200"
    assert b"<!doctype html" in body.lower(), "and it answers with the app"
    assert get("/", {"Origin": "http://evil.example"})[0] == 200, \
        "GET / carries no token and takes no Origin: it leaks only that table-talk is running"
    assert get("/tt.css")[0] == 200, "the stylesheet is served from bin/, where tt_config.selftest checks it"
    assert "app.js" in STATIC, "the bundle has a name reserved on the whitelist"
    assert get("/app.js")[0] == (200 if (WEB / "app.js").is_file() else 404), \
        "a whitelisted name with no file behind it is a 404, never a traceback (it lands in Task 18)"
    assert get("/themes.css")[0] == 200, "and the palette, rendered from the config"
    assert get("/../../etc/passwd")[0] == 404, "the handler never joins a request path onto a directory"
    assert get("/%2e%2e%2f%2e%2e%2fetc%2fpasswd")[0] == 404, "nor an escaped one"
    assert get("/nope.js")[0] == 404, "a name not on the whitelist does not exist"
    assert len(STATIC) == 5, "five names, and adding a sixth is a deliberate act"
    assert b"__TT_TOKEN__" not in get("/")[1], \
        "the per-process token is substituted into index.html on the way out, never left in the file"
    srv.shutdown()
    print("tt_serve selftest passed")
```

Create `bin/web/index.html` as the phase-2 placeholder — a real, useful page that dumps the frame as text:

```html
<!doctype html>
<meta charset="utf-8">
<title>table-talk</title>
<link rel="stylesheet" href="/tt.css">
<link rel="stylesheet" href="/themes.css">
<pre id="out" style="white-space:pre-wrap;font:12px var(--mono);padding:12px">connecting…</pre>
<script>
  // EventSource cannot set a header, so the token rides in the query string.
  // The page learns it because the server substitutes it on the way out.
  window.TT_TOKEN = "__TT_TOKEN__";
  const es = new EventSource("/state?t=" + encodeURIComponent(window.TT_TOKEN));
  es.addEventListener("frame", e =>
    document.getElementById("out").textContent = JSON.stringify(JSON.parse(e.data), null, 1));
</script>
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 bin/tt_serve.py --selftest`
Expected: FAIL with `NameError: name 'make_server' is not defined`

- [ ] **Step 3: Write the implementation**

```python
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""The brain's boundary: static files, one SSE stream and one intent endpoint.

http.server is not a production server and does not need to be: loopback, one
user, a handful of connections - the same posture as the uvicorn it replaces.
"""
import argparse, json, os, secrets, sys, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tt_model as M
import tt_config
import tt_wall as W

HERE = Path(__file__).resolve().parent
WEB = HERE / "web"

# A whitelist of NAMES mapped to paths - never a join of a request path onto a
# directory, which is the only way this handler can be made to traverse. tt.css
# is served from bin/, where tt_config.selftest cross-checks it: one copy of the
# stylesheet in the repo, and the browser loads the file the test reads.
STATIC = {
    "index.html": (WEB / "index.html", "text/html; charset=utf-8"),
    "app.js": (WEB / "app.js", "text/javascript; charset=utf-8"),
    "app.css": (WEB / "app.css", "text/css; charset=utf-8"),
    "mermaid.min.js": (WEB / "mermaid.min.js", "text/javascript; charset=utf-8"),
    "tt.css": (HERE / "tt.css", "text/css; charset=utf-8"),
}


def ensure_config(path=None, example=None):
    """Create a missing config by COPYING the fully commented example (a bare
    defaults dump teaches nobody anything), and refresh the reference copy
    beside it on every start so it never goes stale. The user's OWN file is
    never rewritten once it exists."""
    path = Path(path) if path else tt_config.CONFIG_PATH
    example = Path(example) if example else HERE.parent / "docs" / "config.example.toml"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if example.is_file():
            ref = path.parent / "config.example.toml"
            if not ref.is_file() or ref.read_text() != example.read_text():
                ref.write_text(example.read_text())
            if not path.is_file():
                path.write_text(example.read_text())
    except OSError as e:
        print(f"table-talk: could not prepare {path}: {e}", file=sys.stderr)
        return None
    return path if path.is_file() else None


class Handler(BaseHTTPRequestHandler):
    server_version = "table-talk"
    brain = None                     # set by serve(); the one Brain this server talks to

    def log_message(self, fmt, *args):
        pass                         # a request log on a 2 s poll is noise

    def _send(self, code, body=b"", ctype="text/plain; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass                     # a closed tab is not an error

    def do_GET(self):
        name, _, self.qs = self.path.lstrip("/").partition("?")
        name = name or "index.html"
        if name == "themes.css":
            css = W.theme_css(tt_config.load().get("theme", {}))
            return self._send(200, css.encode("utf-8"), "text/css; charset=utf-8")
        entry = STATIC.get(name)
        if not entry:
            return self._send(404, b"not found")
        path, ctype = entry
        try:
            body = path.read_bytes()
        except OSError:
            return self._send(404, b"not found")
        if name == "index.html":
            # The token is minted per process and inlined HERE, so it never
            # lives in a committed file and a cross-origin page cannot read it.
            tok = getattr(self.brain, "token", "") or ""
            body = body.replace(b"__TT_TOKEN__", tok.encode("utf-8"))
        self._send(200, body, ctype)


def make_server(port, host="127.0.0.1"):
    return ThreadingHTTPServer((host, port), Handler)
```

and the `__main__` block:

```python
if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--port", type=int)
    a = ap.parse_args()
    if a.selftest:
        selftest()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 bin/tt_serve.py --selftest`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bin/tt_serve.py bin/web/index.html
git commit -m "feat(serve): static files by a five-name whitelist, and a token-free GET /"
```

### Task 15: The token, the `Origin` check, `POST /do` and the launcher

**Files:**
- Modify: `bin/tt_serve.py`
- Test: `bin/tt_serve.py` (`selftest()`)

**Interfaces:**
- Consumes: `Wall.apply` (Task 5), `form_updates`, `coerce` (Task 11); `tt_model.path_spans`, `tt_model.url_spans`, `tt_config.set_keys` (all unchanged).
- Produces:
  - `mint_token() -> str` — 32 bytes from `secrets.token_urlsafe`, written to `DATA_DIR/.ui/token` mode 0600.
  - `Brain(port, cfg)` with `.cfg`, `.port`, `.store`, `.token`, `.walls: dict[str, Wall]`, `.states`, `.tx`, `.beating`, `.polls_ok`, `.spin`, `.last_ok`, `.stale`, and `.wall(conn: str) -> Wall`.
  - `open_target(target, cfg, extra_roots=(), run=subprocess.Popen) -> str | None` — a warning string, or `None` on success **or** on a refusal (a refusal does nothing at all, exactly as today).
  - `apply_config(cfg, submitted) -> str` — the message the statusline shows.
  - `Handler.do_POST` serving `/do`.

- [ ] **Step 1: Write the failing test**

Append to `tt_serve.selftest()` (before the `srv.shutdown()`):

```python
    import ast, subprocess, tempfile
    # A Brain mints a token and opens a Store, both under DATA_DIR: point it at
    # a temp dir first, or ./test.sh would rewrite the user's own .ui/token.
    _td = tempfile.TemporaryDirectory()
    M.DATA_DIR = W.M.DATA_DIR = Path(_td.name)
    brain = Brain(port, tt_config.DEFAULTS)
    Handler.brain = brain

    def post(body, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        c.request("POST", "/do", json.dumps(body),
                  {"Content-Type": "application/json", **(headers or {})})
        r = c.getresponse()
        return r.status, r.read()

    tok = {"X-TT-Token": brain.token}
    assert post({"do": "touch"})[0] == 403, "no token, no intent"
    assert post({"do": "touch"}, {"X-TT-Token": "wrong"})[0] == 403, "and not the wrong one"
    assert post({"do": "touch"}, tok)[0] == 200, "the right token works"
    assert post({"do": "touch"}, {**tok, "Origin": "http://evil.example"})[0] == 403, \
        "a cross-origin POST is refused even with a token"
    assert post({"do": "touch"}, {**tok, "Origin": f"http://127.0.0.1:{port}"})[0] == 200, \
        "the app's own origin is fine"
    assert json.loads(post({"do": "key", "k": "m"}, tok)[1])["ok"] is True, "an intent answers ok"
    assert os.stat(W.M.DATA_DIR / ".ui" / "token").st_mode & 0o077 == 0, "the token file is 0600"

    launched = []
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "real.md").write_text("x")
        (root / "sub").mkdir()
        (root / "out.md").write_text("x")
        cfg = json.loads(json.dumps(tt_config.DEFAULTS))
        cfg["links"]["extra_roots"] = [str(root / "sub")]
        W.set_roots(cfg)
        run = lambda argv: launched.append(argv)
        assert open_target(str(root / "real.md"), cfg, run=run) is None and launched == [], \
            "a path outside every root does not launch, and does not explain itself to the caller"
        cfg["links"]["extra_roots"] = [str(root)]
        W.set_roots(cfg)
        open_target(str(root / "real.md"), cfg, run=run)
        assert launched == [[cfg["links"]["open_command"], str(root / "real.md")]], \
            "the launch is an argv LIST, so a file called 'a;b.md' stays a filename"
        launched.clear()
        open_target("javascript:alert(1)", cfg, run=run)
        open_target("file:///etc/passwd", cfg, run=run)
        open_target(str(root / "nope.md"), cfg, run=run)
        assert launched == [], "javascript:, file:// and a path with no file behind it all refuse"
        open_target("https://example.com/a", cfg, run=run)
        assert launched == [[cfg["links"]["open_command"], "https://example.com/a"]], "http(s) is allowed"
        launched.clear()
        def boom(argv):
            raise FileNotFoundError("xdg-open")
        warn = open_target("https://example.com/a", cfg, run=boom)
        assert warn and "xdg-open" in warn, "a missing opener warns and never crashes"

    src = ast.parse(Path(__file__).resolve().read_text())
    assert not [n for n in ast.walk(src) if isinstance(n, ast.keyword) and n.arg == "shell"], \
        "no shell= keyword anywhere in this file: a filename with metacharacters must stay inert"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 bin/tt_serve.py --selftest`
Expected: FAIL with `NameError: name 'Brain' is not defined`

- [ ] **Step 3: Write the implementation**

Add `import re, subprocess, threading` to the imports, then:

```python
def mint_token():
    """A fresh secret per process, on disk only so index.html can be built with
    it inlined. /do can launch processes, so it is token- AND Origin-gated:
    strictly better than today, where NiceGUI accepts websocket events on 8731
    with no token at all."""
    path = M.DATA_DIR / ".ui" / "token"
    path.parent.mkdir(parents=True, exist_ok=True)
    tok = secrets.token_urlsafe(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(tok)
    return tok


def open_target(target, cfg, extra_roots=(), run=subprocess.Popen):
    """Open one path or http(s) URL, re-deriving confinement HERE.

    The string arrives from a click on a frame built out of a log file, so it
    goes back through the same gate it was rendered through, and only a string
    that is exactly its own whole match survives. Nothing about the click is
    trusted. Returns a warning to show, or None - a refusal does nothing at
    all, which is the point."""
    if not isinstance(target, str):
        return None
    if target.startswith(("http://", "https://")):
        if M.url_spans(target) != [(0, len(target), target)]:
            return None
        argv = [cfg["links"]["open_command"], target]
    else:
        spans = M.path_spans(target, [*W.link_roots(cfg), *(Path(r) for r in extra_roots)])
        if len(spans) != 1 or spans[0][2] != target:
            return None
        argv = [cfg["links"]["open_command"], target]
    try:
        run(argv)
    except OSError as e:
        return f"could not open {target!r}: {e}"
    return None


def apply_config(cfg, submitted):
    """Write only the changed keys, through coerce and set_keys' line surgery,
    so comments and untouched keys survive."""
    kinds = {k: (kind, arg) for k, kind, arg in tt_config.form_fields()}
    typed = {}
    for key, value in (submitted or {}).items():
        if key not in kinds:
            continue
        section, _, name = key.rpartition(".")
        kind, arg = kinds[key]
        bounds = arg if kind == "number" else None
        typed[key] = W.coerce(kind, bounds, value, cfg.get(section, {}).get(name))
    changed = W.form_updates(cfg, typed)
    if not changed:
        return "saved"
    tt_config.set_keys(tt_config.CONFIG_PATH, changed)
    return "saved"


class Brain:
    """Everything one running server owns. One Store shared by every viewer,
    one Wall per SSE connection - the exact analogue of NiceGUI's closure per
    client."""

    def __init__(self, port, cfg):
        self.cfg, self.port = cfg, port
        self.store = W.Store()
        self.token = mint_token()
        self.walls = {}
        self.states, self.tx, self.beating = {}, {}, set()
        self.polls_ok, self.spin, self.last_ok, self.stale = 0, 0, 0.0, False
        self.lock = threading.Lock()

    def wall(self, conn):
        """The Wall for one connection, made on demand. An unknown id (a stale
        tab, a curl) gets its own throwaway rather than borrowing someone
        else's watermarks."""
        if conn not in self.walls:
            self.walls[conn] = W.Wall(self.store, self.cfg)
        return self.walls[conn]
```

and on `Handler`:

```python
    def _authorised(self):
        """Token AND Origin. A cross-origin page cannot read index.html to
        learn the token, and cannot forge an Origin; a missing Origin is a
        non-browser caller (curl), which the token already gates."""
        if self.headers.get("X-TT-Token") != self.brain.token:
            return False
        origin = self.headers.get("Origin")
        ok = {f"http://127.0.0.1:{self.brain.port}", f"http://localhost:{self.brain.port}"}
        return origin is None or origin in ok

    def do_POST(self):
        if self.path.split("?", 1)[0] != "/do":
            return self._send(404, b"not found")
        if not self._authorised():
            return self._send(403, b"forbidden")
        try:
            n = int(self.headers.get("Content-Length") or 0)
            intent = json.loads(self.rfile.read(min(n, 65536)) or b"{}")
        except (ValueError, OSError):
            return self._send(400, b"bad request")
        if not isinstance(intent, dict):
            return self._send(400, b"bad request")
        brain, warn = self.brain, None
        conn = self.headers.get("X-TT-Conn") or ""
        with brain.lock:
            do = intent.get("do")
            if do == "open":
                target = intent.get("target")
                # extra_roots is re-derived from the brain's OWN transcript map,
                # never from the client's claim: that widens confinement for
                # exactly the file the brain resolved, and nothing beside it.
                extra = [str(p) for p in brain.tx.values() if str(p) == target]
                warn = open_target(target, brain.cfg, extra_roots=extra)
            elif do == "config":
                warn = apply_config(brain.cfg, intent.get("set"))
            else:
                brain.wall(conn).apply(intent)
            brain.store.flush()
        body = json.dumps({"ok": True, "warn": warn}).encode("utf-8")
        self._send(200, body, "application/json")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 bin/tt_serve.py --selftest`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bin/tt_serve.py
git commit -m "feat(serve): a token, an Origin check, intents and an argv-list launcher"
```

### Task 16: The SSE stream, the poll tick, frame skipping and the in-place rebind

**Files:**
- Modify: `bin/tt_serve.py`
- Test: `bin/tt_serve.py` (`selftest()`)

**Interfaces:**
- Consumes: `Brain` (Task 15), `Wall.frame`, `read_states`, `transcripts`, `live_sessions` (Tasks 4, 9, 12).
- Produces:
  - `Brain.tick(now) -> None` — re-globs, folds, re-stats the config, advances the spinner on a clean tick, and rebinds the listener in place when `server.port` changes on disk. The swap is only half the mechanism: Task 17's `serve()` loop is the other half, and neither works alone.
  - `envelope(frame, brain) -> dict` — the frame plus `t`, `polls_ok`, `stale`, `status.spin` (the **glyph**, not an index — the sequence then lives in exactly one place) and `status.last_ok`.
  - `Handler.do_GET` gains `/state`: `event: hello` (the connection id), then `event: frame` / `event: tick` forever.
  - `serve(port=None) -> NoReturn`.

Wire format, fixed here and consumed by Task 19:

```
event: hello
data: {"conn":"7f3a…","v":1}

event: frame
data: {"v":1,"t":1757337600.4,…}

event: tick
data: {"t":1757337602.4,"polls_ok":413,"stale":false,"spin":"⠼","last_ok":1757337602.4}
```

`spin` is the spinner **glyph**, not an index into a sequence the view would have to
carry a copy of. `SPINNER` stays a Python tuple in `tt_wall`, and nothing has to keep
a JavaScript copy of it in step.

- [ ] **Step 1: Write the failing test**

Append to `tt_serve.selftest()`:

```python
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "2026-08-27-alpha.jsonl").write_text(
            '{"id":"a1b2","type":"action","status":"open","background":"Pick","ts":9}\n')
        M.DATA_DIR = W.M.DATA_DIR = d
        M._fold_cache.clear()
        b = Brain(port, tt_config.DEFAULTS)
        b.tick(100.0)
        assert b.polls_ok == 1 and b.spin == 1 and b.last_ok == 100.0, "a clean tick advances the spinner"
        assert set(b.states) == {"2026-08-27-alpha"}, "and re-reads the data dir"
        wall = b.wall("c1")
        f1 = wall.frame(100.0, b.states, tx=b.tx, beating=b.beating)
        env = envelope(f1, b)
        assert env["t"] == b.last_ok and env["polls_ok"] == 1, \
            "the four clock fields are the server's, added on the way out"
        assert env["status"]["spin"] == W.SPINNER[1], \
            "and the spinner goes out as a GLYPH: one sequence, in Python, never a JS copy of it"
        assert "spin" not in f1["status"], "and never inside a frame, or skipping could never work"
        f2 = wall.frame(200.0, b.states, tx=b.tx, beating=b.beating)
        assert sse_skip(f1, f2) is True, "nothing changed: emit nothing"
        (d / "2026-08-27-alpha.jsonl").write_text(
            '{"id":"a1b2","type":"action","status":"open","background":"Pick","ts":9}\n'
            '{"id":"c3d4","type":"action","status":"open","background":"And another","ts":10}\n')
        M._fold_cache.clear()
        b.tick(300.0)
        f3 = wall.frame(300.0, b.states, tx=b.tx, beating=b.beating)
        assert sse_skip(f2, f3) is False, "a new action is a new frame"

        def boom(now):
            raise OSError("disk fell over")
        real, b.fold = b.fold, boom
        b.tick(400.0)
        assert b.stale is True and b.spin == 1 and b.polls_ok == 1, \
            "one bad tick freezes the spinner and marks the cadence stale - it never kills the loop"
        b.fold = real
        b.tick(500.0)
        assert b.stale is False, "and the next good tick clears it"
```

Add the framing check to the live-server part of the selftest:

```python
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request("GET", f"/state?t={brain.token}")
    r = c.getresponse()
    assert r.status == 200 and r.getheader("Content-Type").startswith("text/event-stream"), \
        "/state is an event stream"
    first = r.fp.readline() + r.fp.readline() + r.fp.readline()
    assert first.startswith(b"event: hello\ndata: {"), "the stream opens by naming the connection"
    assert b'"conn"' in first, "so an intent can reach the Wall that sent it"
    c.close()
    c2 = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c2.request("GET", "/state")
    assert c2.getresponse().status == 403, "/state needs the token; only GET / is free"
    c2.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 bin/tt_serve.py --selftest`
Expected: FAIL with `AttributeError: 'Brain' object has no attribute 'tick'`

- [ ] **Step 3: Write the implementation**

Add to `Brain`:

```python
    def fold(self, now):
        """Everything the wall reads, per tick. Named so a test can break it."""
        self.states = W.read_states()
        self.tx = W.transcripts()
        self.beating = W.live_sessions(now)

    def tick(self, now):
        """One poll. A bad poll degrades the statusline, never kills the loop -
        a frozen spinner beside a stale timestamp is the whole watch(1) idiom.

        The clock is read BEFORE the files by the caller, for the same reason
        poll() does: a write landing between the two must not be stamped as
        already seen."""
        try:
            self.reload_config()
            self.fold(now)
        except Exception:                     # noqa: BLE001 - degraded, not dead
            self.stale = True
            return
        self.stale = False
        self.polls_ok += 1
        self.spin = (self.spin + 1) % len(W.SPINNER)
        self.last_ok = now

    def reload_config(self):
        """The config is re-read only when it CHANGES on disk: parsing TOML
        every two seconds for an answer that almost never differs is exactly
        the work this design took off the render.

        When server.port moves, the listener is closed and rebound IN PLACE.
        No restart offer, no socket probe, no os.execv onto a port that might
        be taken: if the bind fails the old listener is kept and the statusline
        says so, which is the same information the offer carried.

        The swap does NOT start a thread for the new listener. serve()'s loop
        re-reads brain.httpd after every serve_forever() return (Task 17), so
        old.shutdown() is exactly what hands the main thread over to the new
        server. Running the new one in a daemon thread instead would end the
        process: serve_forever() returns, serve() falls out of its finally
        block, __main__ ends, and the interpreter kills every daemon thread on
        the way down - including the replacement listener.

        The warning is CLEARED here and re-set below, never latched. dash.py's
        restart_offer recomputed the port-mismatch message on every poll and
        vanished when the mismatch was resolved; a sticky flag would leave
        "still on 8731" on the statusline forever after the admin reverted the
        edit, because reverting it takes the branch that says nothing."""
        try:
            stamp = tt_config.CONFIG_PATH.stat().st_mtime
        except OSError:
            stamp = None
        if stamp == getattr(self, "_cfg_seen", "unset"):
            return
        self._cfg_seen = stamp
        self.cfg = tt_config.load()
        W.set_roots(self.cfg)
        for w in self.walls.values():
            w.cfg, w.warn = self.cfg, None
        want = self.cfg["server"]["port"]
        if self.httpd is not None and want != self.port and not self.port_pinned:
            try:
                new = make_server(want, self.cfg["server"]["host"])
            except OSError:
                for w in self.walls.values():
                    w.warn = f"server.port is now {want}, but that port is taken — still on {self.port}"
                return
            old, self.httpd, self.port = self.httpd, new, want
            new.RequestHandlerClass.brain = self
            old.shutdown()          # unblocks serve()'s loop, which picks up self.httpd
            old.server_close()      # and frees the port the old listener was holding
            for w in self.walls.values():
                w.warn = f"moved to port {want} — reopen {url_for(want)}"
```

Two notes on the swap, both load-bearing:

* `old.shutdown()` is called from an SSE connection's `_stream` thread, never from
  the thread running `serve_forever()`, so it cannot deadlock. It returns once the
  main thread has left the old loop.
* Between the assignment and the main thread re-entering `serve_forever()` the new
  socket is already bound and listening (`ThreadingHTTPServer.__init__` binds), so
  connections arriving in that window queue in the backlog rather than being refused.

`Brain.__init__` gains `self.httpd = None` and `self.port_pinned = False` (set True when `--port` was passed: a CLI flag always beats the file, forever).

Then, module level:

```python
def url_for(port):
    return f"http://127.0.0.1:{port}/"


def envelope(frame, brain):
    """The frame as it goes on the wire: the four clock fields added here, so
    Wall.frame stays a pure function of the files and skip-stability is a
    property a selftest can assert.

    The spinner goes out as its GLYPH rather than its index: the sequence stays
    a Python tuple nobody has a second copy of, and a view that indexed into its
    own copy would silently desync the day the glyph set changed."""
    out = dict(frame)
    out["t"] = brain.last_ok
    out["polls_ok"] = brain.polls_ok
    out["stale"] = brain.stale
    out["status"] = dict(frame["status"], spin=W.SPINNER[brain.spin], last_ok=brain.last_ok)
    return out


def sse_skip(prev, cur):
    """True when nothing a viewer can see has changed. Compares every window
    signature, the drawer signature and the status numbers - which is every
    part of the frame that is not already covered by one of them."""
    if prev is None:
        return False
    if {k: v.get("sig") for k, v in prev["windows"].items()} != \
       {k: v.get("sig") for k, v in cur["windows"].items()}:
        return False
    return (prev["drawer"]["sig"] == cur["drawer"]["sig"]
            and prev["wall"] == cur["wall"]
            and prev["status"] == cur["status"]
            and prev["theme"] == cur["theme"]
            and cur["focus"] is None and cur["warn"] == prev["warn"])


def sse_write(wfile, event, payload):
    """One SSE message. json.dumps escapes every newline inside a string, so a
    frame can never break the framing."""
    wfile.write(f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8"))
    wfile.flush()
```

and in `Handler.do_GET`, before the static lookup:

```python
        if name == "state":
            # EventSource cannot set a header, so /state (and only /state)
            # accepts the token in the query string as well. It is a read-only
            # stream on loopback; /do, which can launch a process, stays
            # header-and-Origin gated.
            from urllib.parse import parse_qs
            given = (self.headers.get("X-TT-Token")
                     or parse_qs(self.qs).get("t", [""])[0])
            if given != self.brain.token:
                return self._send(403, b"forbidden")
            return self._stream()
```

with:

```python
    def _stream(self):
        brain = self.brain
        conn = secrets.token_urlsafe(8)
        # Registered before the first frame: an intent that lands while this
        # stream is mid-tick must still find an Event to set.
        waiter = brain.waiters[conn] = threading.Event()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        prev = None
        try:
            sse_write(self.wfile, "hello", {"conn": conn, "v": W.FRAME_V})
            while True:
                now = time.time()          # the clock BEFORE the files, every tick
                with brain.lock:
                    brain.tick(now)
                    frame = brain.wall(conn).frame(now, brain.states, tx=brain.tx,
                                                   beating=brain.beating)
                    skip = sse_skip(prev, frame)
                    payload = envelope(frame, brain)
                if skip:
                    sse_write(self.wfile, "tick", {k: payload[k] for k in ("t", "polls_ok", "stale")}
                              | {"spin": W.SPINNER[brain.spin], "last_ok": brain.last_ok})
                else:
                    sse_write(self.wfile, "frame", payload)
                    prev = frame
                # An intent wakes every stream immediately, so a keypress
                # repaints in about five milliseconds instead of waiting up to
                # two seconds for the next poll. It is free - the fold is
                # already cached - and it is the one user-visible improvement
                # in the whole migration.
                waiter.wait(timeout=max(0.2, float(brain.cfg["server"]["poll_seconds"])))
                waiter.clear()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            brain.walls.pop(conn, None)     # the connection dies, the Wall dies
            brain.waiters.pop(conn, None)
```

`Brain.__init__` gains `self.waiters = {}` and:

```python
    def wake(self):
        """Push a frame to every open stream now, not on the next tick."""
        for ev in list(self.waiters.values()):
            ev.set()
```

and `Handler.do_POST` (Task 15) gains one line between the `with brain.lock:` block
and the answer. In full, so the edit is unambiguous — its last four lines become:

```python
            brain.store.flush()
        brain.wake()               # every open stream repaints now, not in 2 s
        body = json.dumps({"ok": True, "warn": warn}).encode("utf-8")
        self._send(200, body, "application/json")
```

`wake()` is deliberately outside the lock: the streams it wakes take that same lock
on their next tick.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 bin/tt_serve.py --selftest`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add bin/tt_serve.py
git commit -m "feat(serve): the SSE stream, the poll tick and a frame that is skipped when nothing moved"
```

### Task 17: The instance lock, `table-talk state`, and the missing-bundle refusal

**Files:**
- Modify: `bin/tt_serve.py`
- Modify: `bin/table-talk`
- Modify: `test.sh`
- Test: `bin/tt_serve.py` (`selftest()`), `bin/table-talk` (`selftest()`)

**Interfaces:**
- Consumes: `Brain`, `make_server` (Tasks 14–16).
- Produces:
  - `lock_path() -> Path` (`DATA_DIR/.ui/gui.lock`), `claim(port) -> tuple[str, bool]` (`(url, owner)`), `relock(port) -> None`, `release() -> None`.
  - `serve(port=None) -> NoReturn` — refuses to start with no `bin/web/index.html`, claims the lock, binds, serves **in a loop that re-reads `brain.httpd`**, releases the lock on the way out.
  - `bin/table-talk`: `table-talk state [--port N] [--force]` execs `bin/tt_serve.py`, behind the same `serve_refusal` guard `serve` and `gui` use.

- [ ] **Step 1: Write the failing test**

Append to `tt_serve.selftest()`:

```python
    with tempfile.TemporaryDirectory() as td:
        M.DATA_DIR = W.M.DATA_DIR = Path(td)
        url, owner = claim(8899)
        assert owner is True and url == "http://127.0.0.1:8899/", "the first claim owns the brain"
        assert lock_path().read_text() == f"{os.getpid()}\n8899\n", "pid and port, in that order"
        url2, owner2 = claim(8731)
        assert owner2 is False and url2 == "http://127.0.0.1:8899/", \
            "a live lock means: do not start a second brain, open a window on the one that is running"
        lock_path().write_text("999999999\n8899\n")
        url3, owner3 = claim(8899)
        assert owner3 is True, "a stale pid is reclaimed once"
        release()
        assert not lock_path().exists(), "and released on the way out"
        lock_path().write_text("not a pid\n")
        assert claim(8899)[1] is True, "a corrupt lock is stale, not fatal"
        relock(9001)
        assert lock_path().read_text() == f"{os.getpid()}\n9001\n", \
            "a rebind rewrites the port in the lock, or gui would open a window on a dead socket"
        release()
```

And the rebind, end to end, against a **live** `serve_forever()` — the only shape of
test that can catch it. Calling `reload_config()` in-process passes whether or not
`serve()` mistakes the swap for a shutdown; what actually breaks is the serving loop,
so the test has to run one:

```python
    # A port change must MOVE the listener, not end the brain. The failure this
    # pins is silent: shutdown() on the old server is exactly what makes the
    # main thread's serve_forever() return, and a serve() that treats that
    # return as "we are done" exits the process and drops the lock.
    import socket, subprocess, urllib.request

    def _free_port():
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        p = s.getsockname()[1]
        s.close()
        return p

    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        p1, p2 = _free_port(), _free_port()
        cfg_file = d / "config.toml"
        cfg_file.write_text(f"[server]\nport = {p1}\npoll_seconds = 0.2\n")
        env = {**os.environ, "TABLE_TALK_DIR": str(d), "TABLE_TALK_CONFIG": str(cfg_file)}
        proc = subprocess.Popen([sys.executable, str(Path(__file__).resolve())], env=env)

        def _up(port):
            for _ in range(100):
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1) as r:
                        return r.status == 200
                except OSError:
                    time.sleep(0.1)
            return False

        try:
            assert _up(p1), "the brain answers on the port its config named"
            token = (d / ".ui" / "token").read_text()
            # A tick only runs while somebody is watching, so hold one stream
            # open - this is also exactly the situation the rebind happens in.
            stream = http.client.HTTPConnection("127.0.0.1", p1, timeout=10)
            stream.request("GET", f"/state?t={token}")
            stream.getresponse().fp.readline()          # "event: hello"
            later = time.time() + 2                     # a coarse mtime must still register
            cfg_file.write_text(f"[server]\nport = {p2}\npoll_seconds = 0.2\n")
            os.utime(cfg_file, (later, later))
            assert _up(p2), "a port change moves the listener in place"
            assert proc.poll() is None, \
                "and the brain is still the SAME process - a swap is not a shutdown"
            pid_s, port_s = (d / ".ui" / "gui.lock").read_text().split("\n")[:2]
            assert int(pid_s) == proc.pid and int(port_s) == p2, \
                "the lock still names a live pid, and now names the port it moved to"
            stream.close()
        finally:
            proc.terminate()
            proc.wait(timeout=10)
```

In `bin/table-talk`'s `selftest()`, extend its local import line to `import tempfile, io, contextlib, subprocess, ast` and append:

```python
    src = Path(__file__).resolve().read_text()
    assert not [n for n in ast.walk(ast.parse(src))
                if isinstance(n, ast.keyword) and n.arg == "shell"], \
        "no shell= keyword anywhere in the CLI either"
    assert "state" in src and "cmd_state" in src, "the brain has a door"
    fn = [n for n in ast.walk(ast.parse(src))
          if isinstance(n, ast.FunctionDef) and n.name == "cmd_state"][0]
    assert "serve_refusal" in ast.get_source_segment(src, fn), \
        "state without --once never returns either, so it goes through the same guard as serve"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 bin/tt_serve.py --selftest`
Expected: FAIL with `NameError: name 'claim' is not defined`

- [ ] **Step 3: Write the implementation**

In `bin/tt_serve.py`:

```python
def lock_path():
    return M.DATA_DIR / ".ui" / "gui.lock"


def claim(port):
    """Claim the right to BE the brain, or find the one already running.

    Returns (url, owner). O_CREAT|O_EXCL so two `table-talk gui` launches
    cannot both see the port unbound and both try to bind it. The lock lives
    beside the data, so TABLE_TALK_DIR=docs/demo gets its own lock and runs
    beside a real instance exactly as today."""
    path = lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "w") as fh:
                fh.write(f"{os.getpid()}\n{port}\n")
            return url_for(port), True
        except FileExistsError:
            try:
                pid_s, port_s = path.read_text().split("\n")[:2]
                pid, held = int(pid_s), int(port_s)
                os.kill(pid, 0)            # alive?
                return url_for(held), False
            except (ValueError, ProcessLookupError, OSError):
                try:
                    path.unlink()          # stale: reclaim it, once
                except OSError:
                    return url_for(port), False
    return url_for(port), False


def relock(port):
    """Rewrite our own lock with the port we just moved to.

    The lock is how a second `table-talk gui` finds a running brain; leaving the
    old port in it after an in-place rebind would open a window on a socket
    nobody is listening to."""
    try:
        if lock_path().read_text().split("\n")[0] == str(os.getpid()):
            lock_path().write_text(f"{os.getpid()}\n{port}\n")
    except (OSError, ValueError):
        pass


def release():
    """Drop the lock, but only if it is ours."""
    try:
        if lock_path().read_text().split("\n")[0] == str(os.getpid()):
            lock_path().unlink()
    except (OSError, ValueError):
        pass


def serve(port=None):
    """Become the brain. Never returns."""
    if not (WEB / "index.html").is_file():
        sys.exit("error: bin/web not built — run ui/build.sh (needs node >= 20), "
                 "or use table-talk serve --legacy")
    cfg = tt_config.load()
    ensure_config()
    W.set_roots(cfg)
    want = port if port is not None else cfg["server"]["port"]
    url, owner = claim(want)
    if not owner:
        sys.exit(f"error: table-talk is already running: {url}")
    brain = Brain(want, cfg)
    brain.port_pinned = port is not None    # a CLI flag beats the file, forever
    try:
        brain.httpd = make_server(want, cfg["server"]["host"])
    except OSError:
        release()
        sys.exit(f"error: port {want} is in use — another table-talk is running: {url}")
    Handler.brain = brain
    print(f"table-talk: {url}")
    try:
        while True:
            # serve_forever() is bound to whatever object brain.httpd names RIGHT
            # NOW, so the local has to be re-read every time round: reload_config
            # swaps brain.httpd and then calls shutdown() on the OLD server, and
            # that shutdown is precisely what makes this call return. Treating
            # that return as "we are finished" ends the process on a port change
            # - drops the lock, kills the brain, and looks like a crash.
            current = brain.httpd
            try:
                current.serve_forever()
            except KeyboardInterrupt:
                break
            if current is brain.httpd:
                break              # a real shutdown, not a rebind
    finally:
        brain.store.flush()
        release()
```

and one line in `Brain.reload_config` (Task 16), immediately after `old.server_close()`:

```python
            relock(want)           # the lock names where the brain actually is
```

and in `__main__`, after the selftest branch: `else: serve(a.port)`.

In `bin/table-talk`, replace `cmd_state` with:

```python
def cmd_state(port=None, once=False, force=False):
    """The brain: one frame with --once, a long-running server without it.

    exec rather than import: the brain needs tomllib through tt_config, which
    is 3.11+, and this CLI keeps its 3.10 promise by never importing either at
    module scope.

    The long-running form goes through serve_refusal for the same reason serve
    and gui do: `state` is SUPPRESSed from --help but fully invocable, it never
    returns, and a Claude session that runs it wedges until its tool timeout.
    --once needs no guard - it prints one frame and exits, which is the
    documented use (`table-talk state --once | jq .status.tally`)."""
    here = Path(__file__).resolve().parent
    if once:
        os.execv(sys.executable, [sys.executable, str(here / "tt_wall.py"), "--once"])
    if (msg := serve_refusal(os.environ, force)):
        sys.exit(msg)
    argv = [sys.executable, str(here / "tt_serve.py")]
    if port is not None:
        argv += ["--port", str(port)]
    os.execv(sys.executable, argv)
```

with the subparser gaining `--port` and a SUPPRESSed `--force`:

```python
    stt.add_argument("--port", type=int, help=argparse.SUPPRESS)
    stt.add_argument("--force", action="store_true", help=argparse.SUPPRESS)
```

and the dispatch becoming `cmd_state(args.port, args.once, args.force)`. Task 30's
`cmd_serve` passes its own `force` straight through (`cmd_state(port, force=force)`),
so `--force` is not asked for twice and does not lose its meaning on the way down.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 bin/tt_serve.py --selftest && python3 bin/table-talk --selftest`
Expected: PASS

- [ ] **Step 5: See the phase's product**

Run: `TABLE_TALK_DIR=docs/demo python3 bin/table-talk state --port 8899` then open `http://127.0.0.1:8899/` in a browser.
Expected: a live JSON dump that updates as `docs/demo` changes, with the real dashboard still available on 8731 via `table-talk serve`.

- [ ] **Step 6: Add the selftest to test.sh and commit**

Add after the `tt_wall.py` line in `test.sh`:

```sh
python3 "$here/bin/tt_serve.py" --selftest
```

```bash
git add bin/tt_serve.py bin/table-talk test.sh
git commit -m "feat(serve): an instance lock, table-talk state, and a refusal to serve an unbuilt bundle"
```

---

## Phase 3 — the Svelte app

Eleven tasks. `TABLE_TALK_DIR=docs/demo python3 bin/table-talk state --port 8899` is a real, improving dashboard the whole time, and `table-talk serve` still starts NiceGUI on 8731.

### Task 18: The `ui/` build, `fmt.js` and the frame store

**Files:**
- Create: `ui/package.json`, `ui/package-lock.json`, `ui/vite.config.js`, `ui/build.sh`, `ui/index.html`, `ui/src/main.js`, `ui/src/fmt.js`, `ui/src/frame.svelte.js`, `ui/src/App.svelte`
- Create: `ui/test/fmt.test.mjs`
- Modify: `.gitignore`, `test.sh`
- Replace (build output): `bin/web/index.html`, `bin/web/app.js`, `bin/web/app.css`, `bin/web/mermaid.min.js`

**Interfaces:**
- Consumes: the SSE wire format from Task 16 (`hello` / `frame` / `tick`), `window.TT_TOKEN` from Task 14's substitution.
- Produces:
  - `fmt.js`: `ago(ts, now) -> string`, `hm(ts) -> string`, `live_delay(read_ts, now, w = 300) -> number | null` (a **negative** number of seconds, or null), `LIVE_WINDOW = 300`.
  - `frame.svelte.js`: `app` (a `$state` object with `frame`, `stale`, `conn`, `bad_version`, `now`), `connect()`, `send(intent) -> Promise<{ok, warn}>`.
  - `ui/build.sh` → `bin/web/{index.html,app.js,app.css,mermaid.min.js}`.

- [ ] **Step 1: Write the failing test**

Create `ui/test/fmt.test.mjs`:

```js
import { test } from "node:test";
import assert from "node:assert/strict";
import { ago, hm, live_delay, LIVE_WINDOW } from "../src/fmt.js";

test("ago", () => {
  assert.equal(ago(100, 100), "just now");
  assert.equal(ago(100, 159), "just now");
  assert.equal(ago(100, 160), "1m ago");
  assert.equal(ago(100, 3699), "59m ago");
  assert.equal(ago(100, 3700), "1h ago");
  assert.equal(ago(100, 86699), "23h ago");
  assert.equal(ago(100, 86700), "1d ago");
  assert.equal(ago(100, 90), "just now", "a clock that ran backwards still reads");
});

test("hm pads both halves", () => {
  const d = new Date(2026, 8, 8, 4, 3, 0);
  assert.equal(hm(d.getTime() / 1000), "04:03");
});

test("live_delay", () => {
  assert.equal(live_delay(100, 100), -0, "a reading taken now starts at the beginning");
  assert.equal(live_delay(100, 220), -120, "and one two minutes old starts partway through");
  assert.equal(live_delay(100, 100 + LIVE_WINDOW), null, "a stale reading is not live");
  assert.equal(live_delay(100, 99), null, "and neither is a future one - that is a clock jump");
  assert.equal(live_delay(null, 100), null);
  assert.equal(live_delay("100", 100), null, "a string is not a reading");
  assert.equal(live_delay(true, 100), null, "and neither is a bool");
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test ui/test/`
Expected: FAIL with `Cannot find module '../src/fmt.js'`

- [ ] **Step 3: Write the implementation**

`ui/src/fmt.js` — the only client-side logic in the app, and the reason it is the only file with unit tests:

```js
// Pure, no imports. Everything here exists because the frame carries ABSOLUTE
// timestamps only: a rendered "3m ago" in the payload would change it every
// second and make frame skipping worthless, and it would freeze and lie on a
// card that has not repainted for an hour.
export const LIVE_WINDOW = 300;

export function ago(ts, now) {
  const d = (now === undefined ? Date.now() / 1000 : now) - ts;
  if (d < 60) return "just now";
  if (d < 3600) return `${Math.floor(d / 60)}m ago`;
  if (d < 86400) return `${Math.floor(d / 3600)}h ago`;
  return `${Math.floor(d / 86400)}d ago`;
}

export function hm(ts) {
  const d = new Date(ts * 1000);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

// A NEGATIVE animation-delay in seconds, or null when the reading is stale.
// The animation runs for exactly LIVE_WINDOW seconds and is started PARTWAY
// THROUGH, so it finishes on its own at the right moment and needs nobody to
// come back and stop it.
export function live_delay(read_ts, now, w = LIVE_WINDOW) {
  if (typeof read_ts !== "number" || !Number.isFinite(read_ts)) return null;
  const age = now - read_ts;
  return age >= 0 && age < w ? -Math.round(age * 10) / 10 : null;
}
```

`ui/src/frame.svelte.js`:

```js
// One EventSource, one $state object, one POST helper. The 1 s clock lives
// here rather than in fmt.js because $state is a Svelte rune and fmt.js is
// deliberately importable by node --test with no toolchain.
export const app = $state({
  frame: null,      // the last full frame
  stale: false,     // the brain is not answering, or its last poll failed
  conn: "",         // this connection's id, so /do reaches the right Wall
  bad_version: false,
  now: Date.now() / 1000,
});

const V = 1;
let es = null;
let backoff = 500;

export function connect() {
  es = new EventSource(`/state?t=${encodeURIComponent(window.TT_TOKEN)}`);
  es.addEventListener("hello", (e) => {
    const d = JSON.parse(e.data);
    app.conn = d.conn;
    app.bad_version = d.v !== V;
    app.stale = false;
    backoff = 500;
  });
  es.addEventListener("frame", (e) => {
    const f = JSON.parse(e.data);
    if (f.v !== V) { app.bad_version = true; return; }
    app.frame = f;
    app.stale = !!f.stale;
  });
  es.addEventListener("tick", (e) => {
    // A skipped frame still has to move the spinner and the cadence, or the
    // one honest liveness signal on the page would freeze.
    const t = JSON.parse(e.data);
    if (!app.frame) return;
    app.frame.t = t.t;
    app.frame.polls_ok = t.polls_ok;
    app.frame.status.spin = t.spin;
    app.frame.status.last_ok = t.last_ok;
    app.stale = !!t.stale;
  });
  es.onerror = () => {
    es.close();
    app.stale = true;              // the last frame stays on screen; nothing blanks
    setTimeout(connect, backoff);
    backoff = Math.min(backoff * 2, 8000);
  };
}

export async function send(intent) {
  try {
    const r = await fetch("/do", {
      method: "POST",
      headers: { "Content-Type": "application/json",
                 "X-TT-Token": window.TT_TOKEN, "X-TT-Conn": app.conn },
      body: JSON.stringify(intent),
    });
    return r.ok ? await r.json() : { ok: false, warn: null };
  } catch {
    return { ok: false, warn: null };
  }
}

setInterval(() => { app.now = Date.now() / 1000; }, 1000);
```

`ui/src/App.svelte` (this task's version — Task 19 replaces the body with the real shell):

```svelte
<script>
  import { app, connect } from "./frame.svelte.js";
  connect();
</script>

{#if app.bad_version}
  <div class="tt-none">update table-talk — this page does not know this frame version</div>
{:else if !app.frame}
  <div class="tt-none">connecting…</div>
{:else}
  <div class="sl"><span class="sl-i">table-talk</span><span class="sl-i">{app.frame.status.text}</span></div>
{/if}
```

`ui/src/main.js`:

```js
import { mount } from "svelte";
import App from "./App.svelte";
mount(App, { target: document.getElementById("app") });
```

`ui/index.html` — hand-written, copied verbatim by `build.sh`; `__TT_TOKEN__` is substituted by the server on every `GET /`:

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>table-talk</title>
<link rel="stylesheet" href="/tt.css">
<link rel="stylesheet" href="/themes.css">
<link rel="stylesheet" href="/app.css">
<script>window.TT_TOKEN = "__TT_TOKEN__";</script>
</head>
<body>
<div id="app"></div>
<script src="/mermaid.min.js"></script>
<script type="module" src="/app.js"></script>
</body>
</html>
```

`ui/package.json`:

```json
{
  "name": "table-talk-ui",
  "private": true,
  "type": "module",
  "scripts": { "build": "vite build", "test": "node --test test/" },
  "dependencies": { "mermaid": "11.16.1" },
  "devDependencies": {
    "@sveltejs/vite-plugin-svelte": "^5.0.3",
    "svelte": "^5.20.0",
    "vite": "^6.0.7"
  }
}
```

`ui/vite.config.js` — unhashed names, one CSS file, output straight into the committed bundle:

```js
import { defineConfig } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";

// Fixed output names and no hashes: bin/web is committed, and CI runs
// `git diff --exit-code -- bin/web`, so a rebuild of unchanged source has to
// produce byte-identical files.
export default defineConfig({
  plugins: [svelte()],
  build: {
    outDir: "../bin/web",
    emptyOutDir: false,          // index.html and mermaid.min.js are copied, not built
    cssCodeSplit: false,
    minify: "esbuild",
    rollupOptions: {
      input: "src/main.js",
      output: {
        entryFileNames: "app.js",
        chunkFileNames: "app.js",
        assetFileNames: "app.css",
      },
    },
  },
});
```

`ui/build.sh`:

```sh
#!/usr/bin/env bash
# Build the committed bundle. Node is a BUILD dependency only: bin/web is in
# the repo, so nobody running table-talk ever needs it.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
if ! command -v node >/dev/null; then
    echo "error: ui/build.sh needs node >= 20 — the built bundle is committed in bin/web," >&2
    echo "so you only need this to CHANGE the UI." >&2
    exit 2
fi
cd "$here"
npm ci
npm run build
cp index.html ../bin/web/index.html
cp node_modules/mermaid/dist/mermaid.min.js ../bin/web/mermaid.min.js
echo "built bin/web"
```

Add to `.gitignore`:

```
ui/node_modules/
shell/src-tauri/target/
```

- [ ] **Step 4: Build and run the tests**

Run: `chmod +x ui/build.sh && ./ui/build.sh && node --test ui/test/`
Expected: `built bin/web`, then all `fmt` tests pass.

- [ ] **Step 5: See it live**

Run: `TABLE_TALK_DIR=docs/demo python3 bin/table-talk state --port 8899` and open `http://127.0.0.1:8899/`
Expected: a statusline reading `table-talk ●N open ▶M running`, updating every two seconds.

- [ ] **Step 6: Add the node tests to test.sh and commit**

Add to `test.sh` before the `echo`:

```sh
command -v node >/dev/null && node --test "$here/ui/test/" || echo "node absent - skipped ui/test"
```

```bash
git add ui .gitignore test.sh bin/web
git commit -m "feat(ui): a Svelte 5 build, the frame store and the only client-side logic"
```

### Task 19: The app shell — grid, keys, touch and width

**Files:**
- Modify: `ui/src/App.svelte`
- Create: `ui/e2e.mjs`
- Modify: `bin/web/app.js`, `bin/web/app.css` (rebuild)

**Interfaces:**
- Consumes: `app`, `send` (Task 18); `KEYMAP` through `frame.status.keymap` (Task 11).
- Produces: `ui/e2e.mjs` — a Playwright script with `const CHECKS = []` and `add(name, fn)`, spawning the brain against `docs/demo` on port 8899 and handing each check to stdlib `node:test`; later tasks append checks. `App.svelte` exporting nothing, but owning the four `addEventListener`s, the `ResizeObserver` and the key dispatch that every later component relies on.

`node:test` is not a dependency — it is the runner `ui/test/fmt.test.mjs` already uses two tasks earlier, and it brings test registration, per-check reporting and the right exit code for free. "No test framework" in the spec means no vitest and no jsdom; hand-rolling a pass counter and a `process.exit` to avoid a stdlib module is not laziness, it is a second implementation of one.

- [ ] **Step 1: Write the failing test**

Create `ui/e2e.mjs`:

```js
// One Playwright script driving the real brain against the frozen demo dir,
// which is what makes it a parity test rather than a mock: every number on the
// page came out of docs/demo through tt_wall.
//
// Registration, reporting and the exit code come from node:test - stdlib, and
// the same runner ui/test/ already uses. The checks share one page and one
// brain and run in order, which node:test does by default within a file.
import { test, after } from "node:test";
import { chromium, webkit } from "playwright";
import { spawn } from "node:child_process";
import { setTimeout as sleep } from "node:timers/promises";

const BROWSER = (process.argv.find(a => a.startsWith("--browser=")) || "--browser=chromium").split("=")[1];
const PORT = 8899;
const URL = `http://127.0.0.1:${PORT}/`;
const CHECKS = [];
export function add(name, fn) { CHECKS.push([name, fn]); }

add("the statusline reports the demo dir's tally", async (page) => {
  const text = await page.textContent(".sl");
  if (!/●\d+ open/.test(text)) throw new Error(`no tally in statusline: ${text}`);
});

add("a keystroke reaches the brain and comes back as a frame", async (page) => {
  const before = await page.getAttribute(".tt-main", "class");
  await page.keyboard.press("\\");
  await page.waitForFunction(
    (b) => document.querySelector(".tt-main").className !== b, before, { timeout: 3000 });
  await page.keyboard.press("\\");
});

add("a keystroke typed into a text field is not a command", async (page) => {
  await page.click(".dw-find input");
  const before = await page.getAttribute(".tt-main", "class");
  await page.keyboard.type("m");
  await sleep(500);
  if (await page.getAttribute(".tt-main", "class") !== before)
    throw new Error("typing m in the filter marked a window");
  await page.fill(".dw-find input", "");
});

// Setup runs BEFORE any test is registered, deliberately: node:test starts a
// test as soon as it is defined, so registering first would race the browser.
const brain = spawn("python3", ["bin/table-talk", "state", "--port", String(PORT)],
                    { env: { ...process.env, TABLE_TALK_DIR: "docs/demo" }, stdio: "inherit" });
process.on("exit", () => brain.kill());
for (let i = 0; i < 100; i++) {
  try { await fetch(URL); break; } catch { await sleep(100); }
}
const browser = await (BROWSER === "webkit" ? webkit : chromium).launch();
const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
await page.goto(URL);
await page.waitForSelector(".sl", { timeout: 10000 });

let shots = 0;
for (const [name, fn] of CHECKS) {
  test(name, async () => {
    try {
      await fn(page);
    } catch (e) {
      await page.screenshot({ path: `e2e-fail-${++shots}.png` });
      throw e;                       // node:test owns the reporting and the exit code
    }
  });
}
after(async () => { await browser.close(); brain.kill(); });
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npx playwright install --with-deps chromium && node ui/e2e.mjs --browser=chromium`
Expected: FAIL — `waitForSelector(".sl")` times out, or the first two checks fail: there is no `.tt-main`, no drawer and no key handling yet.

- [ ] **Step 3: Write the implementation**

`ui/src/App.svelte`:

```svelte
<script>
  import { app, send, connect } from "./frame.svelte.js";

  connect();

  let dialog = $state("");            // "" | "keys" | "settings"
  let touched_at = 0;
  let wall_el = $state(null);
  let narrow = null;                  // the last clamp state we told the brain about

  // SEEN_JS's trigger set, unchanged: a watermark advances on INTERACTION, not
  // on the Page Visibility API alone - which cannot detect "covered but not
  // backgrounded" on a permanently visible second monitor. visibilitychange is
  // kept as a secondary signal.
  function touch() {
    const t = Date.now();
    if (t - touched_at < 1000) return;
    touched_at = t;
    send({ do: "touch" });
  }

  function onkey(e) {
    if (e.repeat) return;
    if (e.target?.closest?.("input,textarea,select,button")) return;
    touch();
    if (e.key === "?") { dialog = dialog === "keys" ? "" : "keys"; return; }
    if (dialog) { if (e.key === "Escape") dialog = ""; return; }
    if (e.key === "/") {
      e.preventDefault();
      if (!app.frame.drawer.open) send({ do: "key", k: "\\" });
      requestAnimationFrame(() => document.querySelector(".dw-find input")?.focus());
      return;
    }
    if (app.frame?.status.keymap[e.key] || e.key === "Escape") {
      e.preventDefault();
      send({ do: "key", k: e.key });
    }
  }

  // The wall's OWN width, not the viewport's: collapsing the drawer changes the
  // wall's width with no viewport change, and matchMedia would miss the clamp
  // flip entirely. Reported only when the narrow/wide clamp actually flips.
  $effect(() => {
    if (!wall_el) return;
    const ro = new ResizeObserver(([entry]) => {
      const px = entry.contentRect.width;
      const now_narrow = px < 900;
      if (now_narrow !== narrow) { narrow = now_narrow; send({ do: "width", px }); }
    });
    ro.observe(wall_el);
    return () => ro.disconnect();
  });
</script>

<svelte:window onkeydown={onkey} onclick={touch} onscroll={touch}
               onvisibilitychange={() => document.visibilityState === "visible" && touch()} />

{#if app.bad_version}
  <div class="tt-none">update table-talk — this page does not know this frame version</div>
{:else if !app.frame}
  <div class="tt-none">connecting…</div>
{:else}
  <!-- .tt-app is the full-height flex column tt.css already defines; it used
       to sit on NiceGUI's own .nicegui-content wrapper, which is why that rule
       can go in phase 6. -->
  <div class="tt-app">
    <div class="tt-main {app.frame.drawer.open ? '' : 'tt-collapsed'}">
      <div class="dw"><div class="dw-find"><input class="dw-in" placeholder="filter" /></div></div>
      <div class="wall" bind:this={wall_el}></div>
    </div>
    <div class="sl"><span class="sl-s">table-talk</span><span class="sl-s">{app.frame.status.text}</span></div>
  </div>
{/if}
```

- [ ] **Step 4: Rebuild and run the test**

Run: `./ui/build.sh && node ui/e2e.mjs --browser=chromium`
Expected: node:test's summary, `# pass 3` / `# fail 0`, and exit 0

- [ ] **Step 5: Commit**

```bash
git add ui bin/web
git commit -m "feat(ui): the app shell, its key dispatch and a width report that only fires on a clamp flip"
```

### Task 20: The wall and its windows

**Files:**
- Create: `ui/src/Wall.svelte`, `ui/src/Window.svelte`
- Modify: `ui/src/App.svelte`, `ui/e2e.mjs`
- Modify: `bin/web/app.js`, `bin/web/app.css` (rebuild)

**Interfaces:**
- Consumes: `frame.wall` (`{cols, columns, empty}`) and `frame.windows` (Tasks 8–9); `fmt.ago`.
- Produces: `Wall.svelte` (props: none — it reads `app`), `Window.svelte` (props: `key: string`, `win: object`).

- [ ] **Step 1: Write the failing test**

Append to `ui/e2e.mjs`, above the runner:

```js
add("the demo dir's two projects are on the wall", async (page) => {
  const n = await page.locator(".win").count();
  if (n !== 2) throw new Error(`expected 2 windows, got ${n}`);
});

add("a wide wall packs two columns", async (page) => {
  const n = await page.locator(".wall .col").count();
  if (n !== 2) throw new Error(`expected 2 columns at 1400px, got ${n}`);
});

add("a titlebar carries the project, the age and the flags", async (page) => {
  const t = await page.textContent(".win .win-t");
  if (!/ago|just now/.test(t)) throw new Error(`no age in titlebar: ${t}`);
  if (!(await page.locator(".win .bell").count())) throw new Error("no bell on a session with open actions");
  if (!(await page.locator(".win.win-hot").count()))
    throw new Error("a window with open actions carries the win-hot border tint");
});

add("every card carries its obligation footer", async (page) => {
  const wins = await page.locator(".win").count();
  const feet = await page.locator(".win .win-f").count();
  if (feet !== wins) throw new Error(`${wins} windows but ${feet} footers`);
  const t = await page.textContent(".win .win-f");
  if (!/\d+\/\d+ resolved/.test(t)) throw new Error(`no resolved tally in the footer: ${t}`);
});

add("clicking a window makes it current", async (page) => {
  const wins = page.locator(".win");
  await wins.nth(1).click();
  await page.waitForFunction(
    () => document.querySelectorAll(".win")[1].classList.contains("cur"), null, { timeout: 3000 });
});

add("z zooms to one window and Escape comes back", async (page) => {
  await page.keyboard.press("z");
  await page.waitForFunction(() => document.querySelectorAll(".win").length === 1, null, { timeout: 3000 });
  await page.keyboard.press("Escape");
  await page.waitForFunction(() => document.querySelectorAll(".win").length === 2, null, { timeout: 3000 });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node ui/e2e.mjs --browser=chromium`
Expected: FAIL — `expected 2 windows, got 0`

- [ ] **Step 3: Write the implementation**

`ui/src/Wall.svelte`:

```svelte
<script>
  import { app } from "./frame.svelte.js";
  import Window from "./Window.svelte";

  // The brain already packed these. A keyed {#each} over an unchanged columns
  // list moves no node, which is why re-packing only on a layout_key change
  // means a window never jumps under a reader.
  let wall = $derived(app.frame.wall);

  $effect(() => {
    const key = app.frame.focus;
    if (!key) return;
    document.querySelector(`[data-window="${CSS.escape(key)}"]`)
      ?.scrollIntoView({ behavior: "smooth", block: "start" });
  });
</script>

{#if wall.empty}
  <div class="tt-none">{wall.empty.text}</div>
{/if}
{#each wall.columns as column, i (i)}
  <div class="col">
    {#each column as key (key)}
      <Window {key} win={app.frame.windows[key]} />
    {/each}
  </div>
{/each}
```

`ui/src/Window.svelte`:

```svelte
<script>
  import { app, send } from "./frame.svelte.js";
  import { ago } from "./fmt.js";

  let { key, win } = $props();
  let f = $derived(win.flags);
</script>

<!-- win-hot is dress()'s own rule: a card with an open action gets a tinted
     border. It rides on the same flag as the bell, because that is what `hot`
     means - summary["open_actions"] > 0. -->
<div class="win {f.bell ? 'win-hot' : ''} {f.cur ? 'cur' : ''} {f.mark ? 'marked' : ''} {f.fold ? 'folded' : ''}"
     data-window={key} onclick={() => send({ do: "current", key })}>
  <div class="win-t">
    {#if f.bell}<span class="bell" title="an action is waiting on you">!</span>{/if}
    {#if f.actv}<span class="actv" title="work is running">#</span>{/if}
    {#if f.mark}<span class="fl-m" title="marked: packed first">M</span>{/if}
    {#if f.zoom}<span class="fl-z" title="zoomed">Z</span>{/if}
    {#if f.cur}<span class="fl-c" title="the window m, z and f act on">*</span>{/if}
    {#if f.beat}<span class="beat" title="a session is working right now">◉</span>{/if}
    <span class="nm">{win.project}</span>
    <button class="ix" title={win.tx ? `open ${win.tx}` : "no transcript found"}
            disabled={!win.tx}
            onclick={(e) => { e.stopPropagation(); send({ do: "open", target: win.tx }); }}
    >ix<span>:{win.sid}</span></button>
    <span class="when">{win.latest ? ago(win.latest, app.now) : ""}</span>
    <span class="wctl">
      <button class="wb" title="mark (m)" onclick={(e) => { e.stopPropagation(); send({ do: "window", key, act: "mark" }); }}>M</button>
      <button class="wb" title="zoom (z)" onclick={(e) => { e.stopPropagation(); send({ do: "window", key, act: "zoom" }); }}>Z</button>
      <button class="wb" title="fold (f)" onclick={(e) => { e.stopPropagation(); send({ do: "window", key, act: "fold" }); }}>▾</button>
    </span>
  </div>
  {#if win.error}
    <div class="win-b"><div class="empty">{win.error}</div></div>
  {:else if win.sections}
    <!-- Task 21 replaces this list with <Section>, which adds the rows. Until
         then the body is the five section headers, which is already the shape
         of the card. -->
    <div class="win-b">
      {#each win.sections as sec (sec.id)}
        <div class="p-{sec.id}">❯ {sec.title} ({sec.n})</div>
      {/each}
    </div>
  {/if}
  <!-- The obligation footer: ▰ resolved, ▱ still owed, and the count. It sits
       OUTSIDE the win-b guard because tt.css hides .win-b on a folded card and
       leaves .win-f alone - a folded window is a titlebar and this line. -->
  {#if win.footer}
    <div class="win-f">
      <span class="cells"><span class="on">{win.footer.cells[0]}</span><span class="off">{win.footer.cells[1]}</span></span>
      <span>{win.footer.text}</span>
    </div>
  {/if}
</div>
```

In `App.svelte`, replace the empty `<div class="wall">` with:

```svelte
    <div class="wall" bind:this={wall_el}><Wall /></div>
```

and import it: `import Wall from "./Wall.svelte";`

- [ ] **Step 4: Rebuild and run the test**

Run: `./ui/build.sh && node ui/e2e.mjs --browser=chromium`
Expected: `# pass 9` / `# fail 0`

- [ ] **Step 5: Commit**

```bash
git add ui bin/web
git commit -m "feat(ui): the packed wall, its window titlebars and the obligation footer"
```

### Task 21: Sections, rows and spans

**Files:**
- Create: `ui/src/Section.svelte`, `ui/src/Row.svelte`, `ui/src/Spans.svelte`
- Modify: `ui/src/Window.svelte`, `ui/e2e.mjs`
- Modify: `bin/web/app.js`, `bin/web/app.css` (rebuild)

**Interfaces:**
- Consumes: `frame.windows[k].sections` (Task 7), the row shape (Task 6).
- Produces: `Section.svelte` (props `key: string`, `sec: object`), `Row.svelte` (props `row: object`), `Spans.svelte` (props `spans: array`) — the component every piece of log text goes through, and the one that must **never** use `{@html}`.

- [ ] **Step 1: Write the failing test**

Append to `ui/e2e.mjs`:

```js
add("an action row shows int before why and rec", async (page) => {
  const labels = await page.locator(".win-b .row .sub .lb").allTextContents();
  const i = labels.indexOf("int"), w = labels.indexOf("why");
  if (i === -1 || w === -1 || i > w) throw new Error(`int must read first: ${labels.join(",")}`);
});

add("an action id is a copy button; a term's id is not", async (page) => {
  const act = page.locator(".win-b .row .id-act").first();
  if (!(await act.count())) throw new Error("no action id button");
  if (await act.isDisabled()) throw new Error("an action id must be copyable");
  // glossary starts shut (ui.collapsed_sections), so open every one to reach a term
  const heads = page.locator(".win-b .pr.p-gls");
  for (let i = 0; i < (await heads.count()); i++) await heads.nth(i).click();
  await page.waitForSelector(".win-b .row .id-gls", { timeout: 3000 });
  if (!(await page.locator(".win-b .row .id-gls").first().isDisabled()))
    throw new Error("dash.py renders a term's id cell as a label: it must not be copyable here");
  for (let i = 0; i < (await heads.count()); i++) await heads.nth(i).click();
});

add("a section header toggles its rows", async (page) => {
  const head = page.locator(".win-b .pr").first();
  const before = await page.locator(".win-b .row").count();
  await head.click();
  await page.waitForFunction((b) => document.querySelectorAll(".win-b .row").length !== b,
                             before, { timeout: 3000 });
  await head.click();
});

add("a shut section still reports itself in glyphs", async (page) => {
  if (!(await page.locator(".win-b .bar-box").count()))
    throw new Error("glossary and done start shut, and a shut section shows its bar");
});

add("log text is rendered as text, never as markup", async (page) => {
  const html = await page.innerHTML(".wall");
  if (/<script|onerror=/.test(html)) throw new Error("markup from a log file reached the DOM");
});

add("the ASCII sketch keeps its own geometry", async (page) => {
  const art = page.locator(".art .art-in").first();
  if (!(await art.count())) throw new Error("the demo dir has a sketch and it must draw");
  const ws = await art.evaluate((el) => getComputedStyle(el.closest(".art")).whiteSpace);
  if (ws !== "pre") throw new Error(`art must be white-space:pre, got ${ws}`);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node ui/e2e.mjs --browser=chromium`
Expected: FAIL — `int must read first: ` (there are no rows on the page yet)

- [ ] **Step 3: Write the implementation**

`ui/src/Spans.svelte` — the whole reason no HTML escaping helper is needed anywhere in this app:

```svelte
<script>
  import { send } from "./frame.svelte.js";
  let { spans } = $props();
</script>

<!--
  A span is [text, kind] or [text, kind, target]. Svelte renders {text} as a
  TEXT NODE, so markup in a log file can never become markup on the page - the
  bug tt_model.marked's HTML-escaping property test exists to prevent cannot
  occur here. There is NO {@html} in this file, and CI greps for that.
-->
{#each spans as [text, kind, target] (text + kind)}
  {#if target}
    <button class="lk lk-p {kind}" title={`open ${target}`}
            onclick={(e) => { e.stopPropagation(); send({ do: "open", target }); }}>{text}</button>
  {:else}
    <span class={kind}>{text}</span>
  {/if}
{/each}
```

`ui/src/Row.svelte`:

```svelte
<script>
  import Spans from "./Spans.svelte";

  let { row } = $props();
  const ID_CLASS = { action: "id-act", task: "id-job", done: "id-ok",
                     term: "id-gls", diagram: "id-mag" };
  const LB = { int: "int", why: "why", rec: "rec", def: "def", art: "art" };
  let copied = $state(false);

  // row.copy is minted in Python and is null on terms and diagrams, whose id
  // cell is a plain label in dash.py and stays one here. The flash lives inside
  // .then(): a denied clipboard or an insecure context must never claim a copy
  // that did not happen - COPY_JS's one hard-won rule.
  function copyId(e) {
    e.stopPropagation();
    if (!row.copy) return;
    navigator.clipboard.writeText(row.copy).then(() => {
      copied = true;
      setTimeout(() => (copied = false), 900);
    }).catch(() => {});
  }
</script>

<div class="row {row.changed === 'act' ? 'changed' : ''} {row.changed === 'job' ? 'changed-job' : ''} {row.dim ? 'tt-dim' : ''}">
  <button class="id {ID_CLASS[row.kind]} {copied ? 'copied' : ''}" data-id={row.id}
          disabled={!row.copy} title={row.copy ? `copy "${row.copy}"` : ""} onclick={copyId}>
    {row.label}{#if row.sid}<span class="sid">{row.sid}</span>{/if}
  </button>
  <div>
    {#each row.cells as cell (cell.c)}
      {#if cell.c === "title"}
        <div class="ttl">
          {#if row.cursor}<span class="cursor" title="newest action waiting on you">▉</span>{/if}
          <Spans spans={cell.spans} />
        </div>
      {:else if cell.c === "meter"}
        <!-- Task 22 replaces this line with <Meter meter={cell.meter} /> -->
        <div class="meter"><span class="pct">{cell.meter.pct ?? ""}</span></div>
      {:else if cell.c === "mermaid"}
        <!-- Task 23 replaces this line with <Diagram src={cell.src} id={row.id} /> -->
        <pre class="mmd empty">{cell.src}</pre>
      {:else if cell.c === "art"}
        <div class="sub"><span class="lb">art</span>
          <div class="art"><div class="art-in">{#each cell.lines as line, i (i)}{#if i}{"\n"}{/if}<Spans spans={line} />{/each}</div></div>
        </div>
      {:else}
        <div class="sub"><span class="lb">{LB[cell.c]}</span><div><Spans spans={cell.spans} /></div></div>
      {/if}
    {/each}
    <!-- Task 22 adds: {#if row.reply}<Reply id={row.id} copy={row.copy} />{/if} -->
  </div>
</div>
```

`ui/src/Section.svelte`:

```svelte
<script>
  import { send } from "./frame.svelte.js";
  import Row from "./Row.svelte";
  let { key, sec } = $props();
</script>

<button class="pr p-{sec.id}"
        onclick={(e) => { e.stopPropagation(); send({ do: "section", key, sec: sec.id, open: !sec.open }); }}>
  ❯ {sec.title} <span class="n">({sec.n})</span> {sec.open ? "▾" : "▸"}
  <!-- A shut section still reports itself: █ per item wanting attention,
       ░ per resolved one. The bar disappears when the section is open. -->
  {#if sec.bar}<span class="bar-box"><span class="bar">{sec.bar[0]}</span><span class="bar e">{sec.bar[1]}</span></span>{/if}
</button>
{#if sec.open}
  {#if sec.empty}<div class="empty">{sec.empty}</div>{/if}
  {#each sec.rows as row (row.id)}
    <Row {row} />
  {/each}
{/if}
```

In `Window.svelte`, import `Section` and replace the placeholder list with:

```svelte
      {#each win.sections as sec (sec.id)}
        <Section {key} {sec} />
      {/each}
```

- [ ] **Step 4: Rebuild and run the test**

Run: `./ui/build.sh && node ui/e2e.mjs --browser=chromium`
Expected: PASS — every check green. The meter, the reply box and mermaid are the three inline placeholders named above, and Tasks 22–23 replace exactly those lines.

- [ ] **Step 5: Commit**

```bash
git add ui bin/web
git commit -m "feat(ui): sections, rows and spans - text nodes, never markup"
```

### Task 22: The meter and the reply box

**Files:**
- Create: `ui/src/Meter.svelte`, `ui/src/Reply.svelte`
- Modify: `ui/e2e.mjs`
- Modify: `bin/web/app.js`, `bin/web/app.css` (rebuild)

**Interfaces:**
- Consumes: `meter_for`'s output (Task 6), `fmt.live_delay`, `fmt.hm` (Task 18).
- Produces: `Meter.svelte` (props `meter: {kind, pct?, cells?, read_ts?, on?}`), `Reply.svelte` (props `id: string`, `copy: string | null`), and the `drafts: Map<string,string>` declared in `Reply.svelte`'s **`<script module>`** block — module scope, so one Map is shared by every instance and outlives any row. A plain `<script>` would give each instance its own empty Map and the parity rule would silently not hold.

- [ ] **Step 1: Write the failing test**

Append to `ui/e2e.mjs`:

```js
add("a progress bar snaps in cells and labels its reading absolutely", async (page) => {
  const bar = page.locator(".win-b .blocks").first();
  if (!(await bar.count())) throw new Error("the demo dir has a running job with a reading");
  const t = await bar.textContent();
  if (!/[█░]/.test(t)) throw new Error(`a bar is glyphs, got ${t}`);
  const asof = await page.locator(".asof").first().textContent();
  if (!/^as of \d\d:\d\d$/.test(asof)) throw new Error(`as-of must be absolute, got ${asof}`);
});

add("a blocked task shows a banner instead of a bar", async (page) => {
  // docs/demo carries one task blocked on an open action
  if (!(await page.locator(".blk").count())) throw new Error("no blocked banner");
});

add("a draft and its caret survive three polls", async (page) => {
  const ta = page.locator("textarea.reply-in").first();
  await ta.click();
  await ta.type("keep me");
  await ta.evaluate((el) => el.setSelectionRange(4, 4));
  await new Promise((r) => setTimeout(r, 6500));       // three 2 s polls
  const [value, caret] = await ta.evaluate((el) => [el.value, el.selectionStart]);
  if (value !== "keep me") throw new Error(`draft lost: ${value}`);
  if (caret !== 4) throw new Error(`caret lost: ${caret}`);
  await ta.fill("");
});

add("a draft survives its row being destroyed and rebuilt", async (page) => {
  // The check above passes whether the draft Map is module-scope or per-instance:
  // the keyed {#each} keeps the SAME component alive across a poll. Only an
  // unmount tells the two apart, so shut the section the row lives in and
  // reopen it - the brain ships no rows for a shut section, so the textarea is
  // really gone.
  const win = page.locator(".win", { has: page.locator("textarea.reply-in") }).first();
  const ta = win.locator("textarea.reply-in").first();
  await ta.fill("still here");
  // The placeholder carries the row id, so this names ONE textarea - the other
  // windows keep theirs, and "gone" has to mean gone for this row.
  const sel = `textarea.reply-in[placeholder="${await ta.getAttribute("placeholder")}"]`;
  const head = win.locator(".pr.p-act").first();
  await head.click();
  await page.waitForFunction((s) => !document.querySelector(s), sel, { timeout: 3000 });
  await head.click();
  await page.waitForSelector(sel, { timeout: 3000 });
  const back = await page.locator(sel).inputValue();
  if (back !== "still here")
    throw new Error(`a remounted row lost its draft (${JSON.stringify(back)}) - the drafts Map is per-instance`);
  await page.locator(sel).fill("");
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node ui/e2e.mjs --browser=chromium`
Expected: FAIL — `the demo dir has a running job with a reading`

- [ ] **Step 3: Write the implementation**

`ui/src/Meter.svelte`:

```svelte
<script>
  import { app } from "./frame.svelte.js";
  import { hm, live_delay, LIVE_WINDOW } from "./fmt.js";
  let { meter } = $props();
  // A NEGATIVE delay equal to the reading's age: the animation runs for
  // LIVE_WINDOW seconds and starts partway through, so it finishes on its own
  // with no repaint. Recomputed as the clock ticks, from the ABSOLUTE read_ts.
  let delay = $derived(meter.kind === "bar" ? live_delay(meter.read_ts, app.now) : null);
</script>

{#if meter.kind === "blocked"}
  <div class="meter"><span class="blk">⏸ blocked on {meter.on}</span></div>
{:else if meter.kind === "scan"}
  <div class="meter"><span class="scan"><i>▓</i><i>▓</i><i>▓</i><i>▓</i><i>▓</i></span></div>
{:else}
  <div class="meter">
    <span class="blocks {delay === null ? '' : 'live'}"
          style={delay === null ? "" : `animation-delay:${delay}s;animation-duration:${LIVE_WINDOW}s`}
    >{meter.cells[0]}<span class="e">{meter.cells[1]}</span></span>
    <span class="pct">{meter.pct}%</span>
    {#if meter.read_ts}<span class="asof">as of {hm(meter.read_ts)}</span>{/if}
  </div>
{/if}
```

`ui/src/Reply.svelte`:

```svelte
<script module>
  // MODULE scope, not instance scope. An ordinary <script> in a .svelte file
  // runs once per COMPONENT INSTANCE, so a Map declared there would be a fresh
  // empty Map every time a row is rebuilt - which is exactly the case this
  // exists for: a window leaving and re-entering the wall (zoom, scope,
  // needs-me, a section shut and reopened) destroys the textarea. A keyed
  // {#each} already carries a draft across a poll for free; this carries it
  // across a mount. REPLY_JS's MutationObserver, its rAF throttle, its caret
  // save/restore and its mousedown escape hatch are DELETED, not ported: there
  // is no clear-and-rebuild to defend against.
  const drafts = new Map();
</script>

<script>
  let { id, copy } = $props();

  let value = $state(drafts.get(id) ?? "");
  let flashed = $state(false);

  function onInput(e) { value = e.target.value; drafts.set(id, value); }

  function copyIt() {
    if (!copy) return;
    // The flash lives INSIDE .then(), so a denied clipboard or an insecure
    // context never lies about a successful copy.
    navigator.clipboard.writeText(`${id}: ${value.trim()}`).then(() => {
      flashed = true;
      setTimeout(() => (flashed = false), 1200);
    }).catch(() => {});
  }
</script>

<div class="sub reply">
  <span class="lb">you</span>
  <div class="reply-box">
    <textarea class="reply-in" rows="1" value={value} oninput={onInput}
              placeholder={`answer ${id} - copies as '${id}: ...'`}></textarea>
    <button class="lk reply-copy {flashed ? 'copied' : ''}" disabled={!copy}
            title={`copy '${id}: your answer' to paste in your session`}
            onclick={copyIt}>{flashed ? "copied" : "copy"}</button>
  </div>
</div>
```

and in `Row.svelte`: add `import Meter from "./Meter.svelte";` and `import Reply from "./Reply.svelte";`, replace the placeholder meter line with `<Meter meter={cell.meter} />`, and replace the reply comment with `{#if row.reply}<Reply id={row.id} copy={row.copy} />{/if}`.

- [ ] **Step 4: Rebuild and run the test**

Run: `./ui/build.sh && node ui/e2e.mjs --browser=chromium`
Expected: all checks pass, including the draft-and-caret one — the contract `REPLY_JS` hand-defended, now tested.

- [ ] **Step 5: Commit**

```bash
git add ui bin/web
git commit -m "feat(ui): the progress meter and a reply box whose draft survives by construction"
```

### Task 23: Mermaid

**Files:**
- Create: `ui/src/Diagram.svelte`
- Modify: `ui/e2e.mjs`
- Modify: `bin/web/app.js` (rebuild)

**Interfaces:**
- Consumes: `cells[].src` — the `%%{init}%%` directive already prepended by `row_for` (Task 6); `window.mermaid` from `bin/web/mermaid.min.js`.
- Produces: `Diagram.svelte` (props `src: string`, `id: string`) — the **only** `{@html}` in `ui/src`.

- [ ] **Step 1: Write the failing test**

Append to `ui/e2e.mjs`:

```js
add("a mermaid diagram draws as SVG", async (page) => {
  await page.waitForSelector(".mmd svg", { timeout: 8000 });
  const fill = await page.locator(".mmd .node rect").first()
    .evaluate((el) => getComputedStyle(el).fill);
  if (!fill || fill === "none") throw new Error("the diagram must take tt.css's token colours");
});

add("a diagram that fails to parse cannot put markup in the DOM", async (page) => {
  // The {@html} sink's error path, tested from the outside: a log line is
  // anything a tool call, a hook or a crafted file can write, and mermaid
  // quotes the offending source back inside its parse-error message. If that
  // message ever reached {@html}, a log entry would be running script on the
  // page that holds window.TT_TOKEN.
  const { writeFileSync, unlinkSync } = await import("node:fs");
  const probe = "docs/demo/2026-08-28-xss-probe.jsonl";
  const ts = Math.floor(Date.now() / 1000);
  writeFileSync(probe, JSON.stringify({
    id: "ff01", type: "diagram", title: "xss probe", ts,
    mermaid: "flowchart TD\n  A[<img src=x onerror=alert(1)>] --> ((((",
  }) + "\n");
  try {
    await page.waitForSelector(".mmd .empty", { timeout: 8000 });   // the error branch ran
    if (await page.locator(".mmd img, .mmd script").count())
      throw new Error("markup from a failed diagram reached the DOM as elements");
    if (await page.locator("[onerror]").count())
      throw new Error("an onerror attribute reached the DOM");
  } finally {
    unlinkSync(probe);
    await page.waitForFunction(() => document.querySelectorAll(".win").length === 2,
                               null, { timeout: 8000 });
  }
});

add("exactly one @html in the whole app", async () => {
  const { readdirSync, readFileSync } = await import("node:fs");
  const hits = readdirSync("ui/src")
    .filter((f) => f.endsWith(".svelte"))
    .flatMap((f) => (readFileSync(`ui/src/${f}`, "utf8").match(/@html/g) || []).map(() => f));
  if (hits.length !== 1 || hits[0] !== "Diagram.svelte")
    throw new Error(`@html must appear exactly once, in Diagram.svelte: ${hits.join(",")}`);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node ui/e2e.mjs --browser=chromium`
Expected: FAIL — `waitForSelector(".mmd svg")` times out

- [ ] **Step 3: Write the implementation**

`ui/src/Diagram.svelte`:

```svelte
<script>
  let { src, id } = $props();
  // TWO states, deliberately. `svg` is only ever written from a RESOLVED
  // mermaid.render(), and it is the only thing {@html} sees. A parse error goes
  // in `error` and is rendered as {error}, which Svelte escapes: mermaid quotes
  // the offending source back inside its message, and that source came out of a
  // log file - anything a tool call, a hook or a crafted log line can write.
  // Writing the message into `svg` would turn this one sanctioned {@html} into
  // a way to run script on the page that holds window.TT_TOKEN.
  let svg = $state("");
  let error = $state("");

  // securityLevel strict is mermaid's default and is restated anyway: a future
  // config knob must not be able to silently relax it, because the diagram
  // source comes out of a log file. The FONT rides in the per-render %%{init}%%
  // directive that tt_wall prepends (mermaid measures labels with its own
  // configured font, so a CSS-only swap overflows every box it already sized);
  // the COLOURS come from tt.css's token overrides, because they must follow a
  // live light/dark switch that no baked colour can.
  window.mermaid?.initialize({ startOnLoad: false, securityLevel: "strict", theme: "base" });

  $effect(() => {
    let alive = true;
    window.mermaid?.render(`mmd-${id}-${Math.random().toString(36).slice(2)}`, src)
      .then((r) => { if (alive) { error = ""; svg = r.svg; } })
      .catch((e) => { if (alive) { svg = ""; error = String(e?.message ?? e); } });
    return () => (alive = false);
  });
</script>

<!-- The only {@html} in this app, and it renders mermaid's OWN output, never
     anything out of a log file: the log's text went in as `src`, and mermaid's
     strict mode is what decides what comes back. The error branch is a text
     node - {error}, not {@html error} - because the message is part log line. -->
<div class="mmd">
  {#if error}<pre class="empty">{error}</pre>{:else}{@html svg}{/if}
</div>
```

and in `Row.svelte`: add `import Diagram from "./Diagram.svelte";` and replace the placeholder `<pre class="mmd empty">{cell.src}</pre>` line with `<Diagram src={cell.src} id={row.id} />`.

- [ ] **Step 4: Rebuild and run the test**

Run: `./ui/build.sh && node ui/e2e.mjs --browser=chromium`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ui bin/web
git commit -m "feat(ui): mermaid, with the init directive it already travels with"
```

### Task 24: The drawer

**Files:**
- Create: `ui/src/Drawer.svelte`
- Modify: `ui/src/App.svelte`, `ui/e2e.mjs`
- Modify: `bin/web/app.js`, `bin/web/app.css` (rebuild)

**Interfaces:**
- Consumes: `frame.drawer` (Task 10), `cfg.ui.filter_debounce_ms` through `frame.settings.fields` (Task 11), `fmt.ago`.
- Produces: `Drawer.svelte` — no props; it reads `app`. Emits the intents `query`, `scope`, `group_fold`, `focus`, `key` (for `s`), `theme`, `open`, and `settings` (which `App.svelte` turns into the dialog).

- [ ] **Step 1: Write the failing test**

Append to `ui/e2e.mjs`:

```js
add("the drawer lists both demo projects with their meters", async (page) => {
  const n = await page.locator(".dw-proj").count();
  if (n !== 2) throw new Error(`expected 2 projects in the drawer, got ${n}`);
  if (!(await page.locator(".dw-proj .trk").count())) throw new Error("no htop meter");
});

add("\\ collapses the drawer to a 54px rail", async (page) => {
  await page.keyboard.press("\\");
  await page.waitForFunction(
    () => Math.round(document.querySelector(".dw").getBoundingClientRect().width) === 54,
    null, { timeout: 3000 });
  if (!(await page.locator(".rail-item").count())) throw new Error("the rail has no project buttons");
  await page.keyboard.press("\\");
  await page.waitForFunction(
    () => Math.round(document.querySelector(".dw").getBoundingClientRect().width) === 284,
    null, { timeout: 3000 });
});

add("the filter dims, never hides, and counts what it matched", async (page) => {
  const before = await page.locator(".win-b .row").count();
  await page.fill(".dw-find input", "zzzznotathing");
  await page.waitForSelector(".dw-count:not(:empty)", { timeout: 3000 });
  const after = await page.locator(".win-b .row").count();
  if (after !== before) throw new Error(`the filter must dim, not hide: ${before} -> ${after}`);
  if (!(await page.locator(".win-b .row.tt-dim").count())) throw new Error("nothing was dimmed");
  const count = await page.textContent(".dw-count");
  if (!/^0\/\d+ rows match$/.test(count)) throw new Error(`bad hit count: ${count}`);
  await page.fill(".dw-find input", "");
});

add("clicking a project scopes the wall, and clicking again clears it", async (page) => {
  await page.locator(".dw-proj .dw-row").first().click();
  await page.waitForFunction(() => document.querySelectorAll(".win").length === 1,
                             null, { timeout: 3000 });
  await page.locator(".dw-proj .dw-row").first().click();
  await page.waitForFunction(() => document.querySelectorAll(".win").length === 2,
                             null, { timeout: 3000 });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node ui/e2e.mjs --browser=chromium`
Expected: FAIL — `expected 2 projects in the drawer, got 0`

- [ ] **Step 3: Write the implementation**

`ui/src/Drawer.svelte`:

```svelte
<script>
  import { app, send } from "./frame.svelte.js";
  import { ago } from "./fmt.js";

  let { onsettings } = $props();
  let dw = $derived(app.frame.drawer);
  let q = $state("");
  let timer = null;
  const ICON = { system: "◐", light: "○", dark: "●" };

  function debounce_ms() {
    return app.frame.settings.fields.find((f) => f.key === "ui.filter_debounce_ms")?.value ?? 100;
  }

  // Highlighting is server-side: tt_model.parts runs in Python and ships
  // tt-hit spans, which costs one loopback round trip per debounce interval
  // (~5 ms, since an intent pushes a frame immediately) and buys the deletion
  // of a second parts() in JavaScript with its own pins.
  function onFind(e) {
    q = e.target.value;
    clearTimeout(timer);
    timer = setTimeout(() => {
      send({ do: "query", q });
      requestAnimationFrame(() => {
        const hit = document.querySelector(".tt-hit");
        if (q.trim() && hit) hit.scrollIntoView({ behavior: "smooth", block: "center" });
        else if (!q.trim()) document.querySelector(".wall")?.scrollTo({ top: 0, behavior: "smooth" });
      });
    }, debounce_ms());
  }

</script>

<!--
  dash.py's meter_row, once, for both project and session rows: the two badges,
  then the htop bar as literal [ ] labels around the .trk div whose <i> is sized
  by a CSS percentage (tt.css:127-133). The bar is CSS, not glyphs - it is the
  one meter in this app that never was text - so the frame ships only pct.
-->
{#snippet meter(m)}
  <span class={m.open ? "b-act" : "b-off"}>●{m.open}</span>
  <span class={m.tasks ? "b-job" : "b-off"}>▶{m.tasks}</span>
  <span class="mtr">[<span class="trk"><i class={m.pct === 100 ? "full" : ""} style="width:{m.pct}%"></i></span>]</span>
  <span class="pc">{m.pct}%</span>
{/snippet}

{#if dw.open}
  <div class="dw">
    <div class="dw-find">
      <input class="dw-in" placeholder="filter" value={q} oninput={onFind} />
      <span class="dw-count">{dw.hits}</span>
      <button class="dw-theme" title="theme: {app.frame.theme.mode}"
              onclick={() => send({ do: "theme" })}>{ICON[app.frame.theme.mode]}</button>
    </div>
    <div class="dw-top">
      <span class="ttl">sessions</span>
      <span class="dw-meta">{dw.sessions} · {dw.projects} projects</span>
    </div>
    <button class="dw-sort" onclick={() => send({ do: "key", k: "s" })}>
      sort: <b>{dw.sort}</b>
    </button>
    <div class="dw-tree">
      {#each dw.projects_list as p (p.project)}
        <div class="dw-proj {p.scoped ? 'dw-on' : ''}">
          <button class="dw-row" onclick={() => send({ do: "scope", project: p.scoped ? null : p.project })}>
            <span class="dw-g {p.multi ? 'dw-fold' : ''}"
                  onclick={(e) => { if (!p.multi) return; e.stopPropagation();
                                    send({ do: "group_fold", project: p.project }); }}
            >{p.multi ? (p.folded ? "▸" : "▾") : ""}</span>
            <span class="dw-l1"><span class="dw-nm">{p.project}</span>
              <span class="dw-meta">{p.sessions.length > 1 ? `${p.sessions.length} sessions` : p.sessions[0].date}</span></span>
            <span></span>
            <span class="dw-l2">{@render meter(p.meter)}</span>
          </button>
          {#if !p.folded}
            {#each p.sessions as s (s.key)}
              <button class="dw-row dw-sess" onclick={() => send({ do: "focus", key: s.key })}>
                <span class="dw-g"></span>
                <span class="dw-l1"><span class="dw-nm">{s.date}</span>
                  <span class="dw-meta">{s.latest ? ago(s.latest, app.now) : ""}</span></span>
                <span></span>
                <span class="dw-l2">{@render meter(s.meter)}</span>
              </button>
            {/each}
          {/if}
        </div>
      {/each}
    </div>
    {#if dw.ctx.length}
      <div class="dw-ctx">
        {#each dw.ctx as c (c.label)}
          <button class="lk dw-ctx-i" title={c.target ? `open ${c.target}` : "edit settings"}
                  onclick={() => c.act === "settings" ? onsettings() : send({ do: "open", target: c.target })}
          >{c.label}</button>
        {/each}
      </div>
    {/if}
  </div>
{:else}
  <div class="dw">
    {#each dw.projects_list as p (p.project)}
      <button class="rail-item {p.scoped ? 'dw-on' : ''}" title={p.project}
              onclick={() => send({ do: "scope", project: p.scoped ? null : p.project })}>
        <span class="rail-ab">{p.abbrev}</span>
        <span class={p.meter.open ? "b-act" : "b-off"}>●{p.meter.open}</span>
        <span class="rail-t"><i style="width:{p.meter.pct}%"></i></span>
      </button>
    {/each}
  </div>
{/if}
```

In `App.svelte`: import `Drawer`, replace the placeholder `<div class="dw">…</div>` with `<Drawer onsettings={() => (dialog = "settings")} />`, and keep the `.tt-main` / `.tt-collapsed` class switch driven by `app.frame.drawer.open`.

- [ ] **Step 4: Rebuild and run the test**

Run: `./ui/build.sh && node ui/e2e.mjs --browser=chromium`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ui bin/web
git commit -m "feat(ui): the drawer tree, its rail and a filter that dims"
```

### Task 25: The statusline, the tab title, the toast and the `?` dialog

**Files:**
- Create: `ui/src/Statusline.svelte`, `ui/src/Keys.svelte`
- Modify: `ui/src/App.svelte`, `ui/e2e.mjs`
- Modify: `bin/web/app.js`, `bin/web/app.css` (rebuild)

**Interfaces:**
- Consumes: `frame.status` (Task 11), `fmt.hm`.
- Produces: `Statusline.svelte` (**no props** — it reads `app` and sends its own intents), `Keys.svelte` (props `onclose: () => void`).

- [ ] **Step 1: Write the failing test**

Append to `ui/e2e.mjs`:

```js
add("the statusline carries the spinner, the cadence, the tally and a chip per key", async (page) => {
  if (!(await page.locator(".sl .spin").count())) throw new Error("no spinner");
  const cadence = await page.textContent(".sl-s.cadence");
  if (!/Every [\d.]+s · last \d\d:\d\d:\d\d/.test(cadence)) throw new Error(`bad cadence: ${cadence}`);
  const chips = await page.locator(".sl-h").count();
  if (chips !== 8) throw new Error(`expected 8 chips (10 keys less filter and unzoom), got ${chips}`);
});

add("a chip does what its key does", async (page) => {
  const chip = page.locator(".sl-h", { hasText: "needs-me" });
  await chip.click();
  await page.waitForFunction(() => document.querySelector(".sl-h.on") !== null, null, { timeout: 3000 });
  await chip.click();
});

add("the tab title carries the open count", async (page) => {
  await page.waitForFunction(() => /^\(\d+\) table-talk$/.test(document.title), null, { timeout: 3000 });
});

add("? opens the key list and Escape closes it", async (page) => {
  await page.keyboard.press("?");
  await page.waitForSelector(".tt-keys", { timeout: 3000 });
  await page.keyboard.press("Escape");
  await page.waitForSelector(".tt-keys", { state: "detached", timeout: 3000 });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node ui/e2e.mjs --browser=chromium`
Expected: FAIL — `no spinner`

- [ ] **Step 3: Write the implementation**

`ui/src/Statusline.svelte`:

```svelte
<script>
  import { app, send } from "./frame.svelte.js";
  import { hm } from "./fmt.js";

  // No SPINNER list here: the brain sends the glyph, not an index. A second
  // copy of the sequence would desync silently the day either side changed it.
  let st = $derived(app.frame.status);
  let toast = $state("");
  let baseline = null;      // read at the FIRST frame, so a reconnect never bursts

  // The spinner advances one frame per SUCCESSFUL poll only - it freezes on
  // failure, so it reads as liveness you can trust rather than an abstract pulse.
  $effect(() => { document.title = `(${st.tally.open}) table-talk`; });

  $effect(() => {
    const n = st.tally.open;
    if (baseline === null) { baseline = n; return; }
    if (n > baseline) {
      const d = n - baseline;
      toast = `${d} new action item${d === 1 ? "" : "s"} need${d === 1 ? "s" : ""} you`;
      setTimeout(() => (toast = ""), 5000);
    }
    baseline = n;
  });
</script>

<div class="sl">
  <span class="sl-s"><span class="spin">{st.spin}</span> table-talk</span>
  <span class="sl-s cadence {app.stale ? 'sl-stale' : ''}">
    Every {st.poll_seconds}s · last {st.last_ok ? hm(st.last_ok) + ":" + String(new Date(st.last_ok * 1000).getSeconds()).padStart(2, "0") : "--:--:--"}
  </span>
  <span class="sl-s">{st.text}</span>
  {#if st.port_warn}<span class="sl-s sl-port">{st.port_warn}</span>{/if}
  {#if st.scope}
    <span class="sl-s sl-scope">showing {st.scope} only
      <button class="sl-c" title="clear the scope" onclick={() => send({ do: "scope", project: null })}>✕</button>
    </span>
  {/if}
  <span class="sl-s">cols
    {#each [1, 2, 3] as n (n)}
      <button class="sl-c {st.cols === n ? 'on' : ''}" onclick={() => send({ do: "cols", n })}>{n}</button>
    {/each}
  </span>
  <span class="sl-s sl-k">
    {#each Object.entries(st.keymap) as [k, v] (k)}
      <button class="sl-h {v.on ? 'on' : ''}" title={v.label}
              onclick={() => send({ do: "key", k })}><b>{k}</b> {v.label}</button>
    {/each}
  </span>
  <span class="sl-clock">{hm(app.now)}</span>
</div>
{#if toast}<div class="tt-toast"><div class="tt-toast-in">{toast}</div></div>{/if}
```

`ui/src/Keys.svelte`:

```svelte
<script>
  import { app } from "./frame.svelte.js";
  let { onclose } = $props();
  // Built from the same KEYMAP the brain dispatches, so a key and its
  // description cannot drift apart.
  let keys = $derived(Object.entries(app.frame.status.keymap));
</script>

<div class="tt-keys" onclick={onclose}>
  <div class="ttl">keys</div>
  {#each keys as [k, v] (k)}
    <div class="k-row"><b>{k}</b> {v.label}</div>
  {/each}
  <div class="k-row"><b>/</b> filter</div>
  <div class="k-row"><b>Escape</b> unzoom</div>
</div>
```

In `App.svelte`: import both, render `<Statusline />` in place of the placeholder `.sl` block, and render `{#if dialog === "keys"}<Keys onclose={() => (dialog = "")} />{/if}`.

- [ ] **Step 4: Rebuild and run the test**

Run: `./ui/build.sh && node ui/e2e.mjs --browser=chromium`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ui bin/web
git commit -m "feat(ui): the statusline, the tab title, the rising-tally toast and the key list"
```

### Task 26: The settings dialog

**Files:**
- Create: `ui/src/Settings.svelte`
- Modify: `ui/src/App.svelte`, `ui/e2e.mjs`
- Modify: `bin/web/app.js`, `bin/web/app.css` (rebuild)

**Interfaces:**
- Consumes: `frame.settings.fields` (Task 11), the `config` intent (Task 15).
- Produces: `Settings.svelte` (props `onclose: () => void`).

- [ ] **Step 1: Write the failing test**

Append to `ui/e2e.mjs`:

```js
add("the settings form is built from the validator's own field list", async (page) => {
  await page.click(".dw-ctx-i:has-text('settings')");
  await page.waitForSelector(".cfg", { timeout: 3000 });
  const rows = await page.locator(".cfg-row").count();
  if (rows !== 9) throw new Error(`form_fields() yields 9 fields, the form shows ${rows}`);
  const port = page.locator(".cfg-row:has-text('server.port') input");
  if (await port.getAttribute("min") !== "1" || await port.getAttribute("max") !== "65535")
    throw new Error("a number field must carry the validator's own bounds");
  const poll = page.locator(".cfg-row:has-text('server.poll_seconds') input");
  if (await poll.getAttribute("max") !== null)
    throw new Error("an infinite bound is no bound, not a made-up one");
  if (await page.locator(".cfg-row:has-text('theme.dark.')").count())
    throw new Error("colour tokens are deliberately not in the form");
  await page.click(".cfg-act button:has-text('cancel')");
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node ui/e2e.mjs --browser=chromium`
Expected: FAIL — `waitForSelector(".cfg")` times out

- [ ] **Step 3: Write the implementation**

`ui/src/Settings.svelte`:

```svelte
<script>
  import { app, send } from "./frame.svelte.js";
  let { onclose } = $props();

  // Never a second hardcoded field list: a form that disagrees with the
  // validator puts values into the file that then fall back to defaults with a
  // warning nobody sees. Colour tokens are deliberately absent - seventeen of
  // them across two modes is a colour-picker project, and the file does it well.
  let fields = $derived(app.frame.settings.fields);
  let edited = $state({});
  let msg = $state("");

  function value(f) { return edited[f.key] ?? f.value; }

  async function save() {
    // Only what changed goes over the wire; the brain runs it through coerce
    // and tt_config.set_keys' line surgery, so comments survive.
    const r = await send({ do: "config", set: edited });
    msg = r.warn || "saved";
    edited = {};
  }
</script>

<div class="cfg">
  <div class="cfg-h">settings</div>
  {#each fields as f (f.key)}
    <div class="cfg-row">
      <span class="cfg-k">{f.key}</span>
      {#if f.kind === "choice"}
        <select value={value(f)} onchange={(e) => (edited[f.key] = e.target.value)}>
          {#each f.choices as c (c)}<option value={c}>{c === "" ? "(built in)" : c}</option>{/each}
        </select>
      {:else}
        <input type="number" value={value(f)}
               min={f.bounds[0] ?? undefined} max={f.bounds[1] ?? undefined}
               step={String(f.value).includes(".") ? "0.1" : "1"}
               oninput={(e) => (edited[f.key] = Number(e.target.value))} />
      {/if}
    </div>
  {/each}
  {#if msg}<div class="cfg-msg">{msg}</div>{/if}
  <div class="cfg-act">
    <button class="wb" onclick={onclose}>cancel</button>
    <button class="wb" onclick={save}>save</button>
  </div>
</div>
```

In `App.svelte`: import it and render `{#if dialog === "settings"}<Settings onclose={() => (dialog = "")} />{/if}`.

- [ ] **Step 4: Rebuild and run the test**

Run: `./ui/build.sh && node ui/e2e.mjs --browser=chromium`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ui bin/web
git commit -m "feat(ui): a settings dialog with no second field list"
```

### Task 27: Themes — mode, tokens and the stylesheet the window depends on

**Files:**
- Modify: `ui/src/App.svelte`, `ui/index.html`, `ui/e2e.mjs`
- Modify: `bin/tt_serve.py` (`/themes.css` cache header)
- Modify: `bin/web/*` (rebuild)

**Interfaces:**
- Consumes: `frame.theme.mode` (Task 12), `/themes.css` (Task 14), `bin/tt.css` (unchanged).
- Produces: the `body--dark` class rule on `<body>` — the same class `tt.css` already keys its dark palette on, so **not one line of `tt.css` changes**.

- [ ] **Step 1: Write the failing test**

Append to `ui/e2e.mjs`:

```js
add("the theme toggle cycles system → light → dark and the palette follows", async (page) => {
  const bg = () => page.evaluate(() => getComputedStyle(document.body).backgroundColor);
  const start = await bg();
  await page.click(".dw-theme");                        // → light
  await page.waitForFunction((b) => getComputedStyle(document.body).backgroundColor !== b,
                             start, { timeout: 3000 });
  const light = await bg();
  await page.click(".dw-theme");                        // → dark
  await page.waitForFunction((b) => getComputedStyle(document.body).backgroundColor !== b,
                             light, { timeout: 3000 });
  const dark = await page.evaluate(() => document.body.classList.contains("body--dark"));
  if (!dark) throw new Error("dark mode must set the class tt.css already keys its palette on");
  await page.click(".dw-theme");                        // → system
});

add("system mode follows the browser, not the server", async (page) => {
  await page.emulateMedia({ colorScheme: "dark" });
  await page.waitForFunction(() => document.body.classList.contains("body--dark"),
                             null, { timeout: 3000 });
  await page.emulateMedia({ colorScheme: "light" });
  await page.waitForFunction(() => !document.body.classList.contains("body--dark"),
                             null, { timeout: 3000 });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node ui/e2e.mjs --browser=chromium`
Expected: FAIL — the background never changes; nothing sets `body--dark`

- [ ] **Step 3: Write the implementation**

In `App.svelte`, inside `<script>`:

```svelte
  // "system" is the one question the server cannot answer: it has no way to
  // know the client's prefers-color-scheme. The browser answers it, and the
  // class it sets is the one tt.css already keys the dark palette on, so no
  // stylesheet rule changes.
  const media = window.matchMedia("(prefers-color-scheme: dark)");
  let sys_dark = $state(media.matches);
  media.addEventListener("change", (e) => (sys_dark = e.matches));

  $effect(() => {
    const mode = app.frame?.theme.mode ?? "system";
    const dark = mode === "dark" || (mode === "system" && sys_dark);
    document.body.classList.toggle("body--dark", dark);
  });
```

In `bin/tt_serve.py`'s `/themes.css` branch, add a no-store header so a palette edit is never served from cache:

```python
            self.send_response(200)
            self.send_header("Content-Type", "text/css; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(css.encode("utf-8"))))
            self.end_headers()
            self.wfile.write(css.encode("utf-8"))
            return
```

- [ ] **Step 4: Rebuild and run the test**

Run: `./ui/build.sh && node ui/e2e.mjs --browser=chromium && python3 bin/tt_serve.py --selftest`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ui bin/web bin/tt_serve.py
git commit -m "feat(ui): the theme mode, and a dark class tt.css already knows"
```

---

## Phase 4 — parity and CI

Three tasks. The phase ships two dashboards at parity, the new one on the configured port and NiceGUI one flag away.

### Task 28: The rest of the end-to-end script

**Files:**
- Modify: `ui/e2e.mjs`

**Interfaces:**
- Consumes: everything Tasks 19–27 built.
- Produces: the finished `ui/e2e.mjs`, runnable as `node ui/e2e.mjs --browser=chromium|webkit`, exiting non-zero with a screenshot per failure.

- [ ] **Step 1: Write the failing test**

Append to `ui/e2e.mjs`:

```js
add("! hides a session with nothing open, and the tally does NOT change", async (page) => {
  const tally = await page.textContent(".sl .sl-s:nth-child(3)");
  const before = await page.locator(".win").count();
  await page.keyboard.press("!");
  await page.waitForFunction((b) => document.querySelectorAll(".win").length < b,
                             before, { timeout: 3000 });
  const after = await page.textContent(".sl .sl-s:nth-child(3)");
  if (after !== tally)
    throw new Error(`the tally counts EVERY session: ${tally} -> ${after}`);
  await page.keyboard.press("!");
});

add("u flips merged and flat", async (page) => {
  const before = await page.locator(".win").count();
  await page.keyboard.press("u");
  await page.waitForFunction((b) => document.querySelectorAll(".win").length !== b,
                             before, { timeout: 3000 });
  if (await page.locator(".win").count() !== 3)
    throw new Error("docs/demo holds three session files, so a flat wall has three windows");
  await page.keyboard.press("u");
});

add("the change gutter clears once the page has been touched", async (page) => {
  await page.click(".wall");
  await new Promise((r) => setTimeout(r, 5000));       // two polls plus slack
  await page.click(".wall");
  await new Promise((r) => setTimeout(r, 3000));
  if (await page.locator(".win-b .row.changed").count())
    throw new Error("a touched, on-wall window must drop its gutters");
});

add("losing the brain shows a stale statusline and keeps the last frame", async (page) => {
  await page.evaluate(() => window.stop());
  // the reconnect banner is the cadence chip going stale; the wall must not blank
  if (!(await page.locator(".win").count()))
    throw new Error("the last frame stays on screen; nothing blanks");
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node ui/e2e.mjs --browser=chromium`
Expected: FAIL on whichever of the four is not yet satisfied (they exercise Tasks 9, 12 and 25 end to end).

- [ ] **Step 3: Fix what the checks catch**

Work through each failure in `ui/src` or `bin/tt_wall.py` — these are parity assertions, not new features, so a failure means one of the earlier tasks got a rule wrong. Re-run after each fix.

- [ ] **Step 4: Run against the engine the Linux window actually uses**

Run: `npx playwright install --with-deps webkit && node ui/e2e.mjs --browser=webkit`
Expected: PASS. WebKit here is Playwright's build, not Fedora's WebKitGTK — Task 1 checked the real one, and this is the automated half.

- [ ] **Step 5: Commit**

```bash
git add ui/e2e.mjs
git commit -m "test(ui): the end-to-end parity script, on chromium and webkit"
```

### Task 29: CI

**Files:**
- Modify: `.github/workflows/test.yml`

**Interfaces:**
- Consumes: `ui/build.sh` (Task 18), `bin/tt_wall.py --dump-fixtures` (Task 13), `ui/e2e.mjs` (Task 28).
- Produces: two new jobs, `ui` and `ui-macos`. The existing `test` and `cli-oldest-python` jobs are unchanged.

- [ ] **Step 1: Write the failing test**

Add to `.github/workflows/test.yml`:

```yaml
  # The UI is a build artifact committed to bin/web, so CI's job is to prove
  # that the committed bundle IS what ui/ builds, and that the golden frames
  # are what tt_wall produces. Both are `git diff --exit-code` guards: a PR
  # that edits ui/ without rebuilding, or changes a frame without reviewing the
  # diff, fails here rather than shipping a silent divergence.
  ui:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: "22" }
      - run: cd ui && npm ci && npm run build
      - run: cp ui/index.html bin/web/index.html
      - run: cp ui/node_modules/mermaid/dist/mermaid.min.js bin/web/mermaid.min.js
      - run: git diff --exit-code -- bin/web
      - run: python3 bin/tt_wall.py --dump-fixtures
      - run: git diff --exit-code -- tests/frames.json
      - run: npx playwright install --with-deps chromium webkit
      - run: node ui/e2e.mjs --browser=chromium
      - run: node ui/e2e.mjs --browser=webkit
      - uses: actions/upload-artifact@v4
        if: failure()
        with: { name: e2e-screenshots, path: "e2e-fail-*.png" }

  # macOS is a non-negotiable, so it is tested rather than asserted. WKWebView
  # is what the mac window renders with, and Playwright's webkit is the closest
  # automatable stand-in.
  ui-macos:
    runs-on: macos-14
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: "22" }
      - run: cd ui && npm ci && npm run build
      - run: npx playwright install webkit
      - run: node ui/e2e.mjs --browser=webkit
```

- [ ] **Step 2: Run it to verify it fails**

Run: `git commit --allow-empty -m "ci: check" && git push` (on a branch with a PR open), or locally: `cd ui && npm ci && npm run build && git diff --exit-code -- bin/web`
Expected: FAIL if the committed bundle was built from different source — which is exactly the guard's job. It passes only when `bin/web` is current.

- [ ] **Step 3: Make it pass**

Run: `./ui/build.sh && git add bin/web && git commit -m "build(ui): rebuild the committed bundle"`
Expected: `git diff --exit-code -- bin/web` is clean.

- [ ] **Step 4: Verify the whole suite locally**

Run: `./test.sh && node ui/e2e.mjs --browser=chromium`
Expected: `all selftests passed`, then the e2e script passes.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/test.yml bin/web
git commit -m "ci: build the UI, diff the committed bundle and the golden frames, run e2e on two engines"
```

### Task 30: `serve` becomes the brain, `--legacy` keeps NiceGUI

**Files:**
- Modify: `bin/table-talk` (`cmd_serve`, the `serve` subparser, `selftest`)
- Create: `docs/superpowers/notes/2026-09-08-parity-sweep.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: `cmd_state` (Task 17).
- Produces: `cmd_serve(port=None, force=False, legacy=False)` — `legacy=True` execs `table-talk-dash.py` under `uv` exactly as today; otherwise it becomes the brain. `table-talk url` is unchanged.

- [ ] **Step 1: Write the failing test**

Add to `bin/table-talk`'s `selftest()`:

```python
    src = Path(__file__).resolve().read_text()
    assert "--legacy" in src, "the kill-switch has a name, and it is documented"
    tree = ast.parse(src)
    fn = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "cmd_serve"][0]
    args = [a.arg for a in fn.args.args]
    assert args == ["port", "force", "legacy"], f"cmd_serve's signature is pinned: {args}"
    body = ast.get_source_segment(src, fn)
    assert "table-talk-dash.py" in body and "uv" in body, \
        "legacy still execs the NiceGUI dashboard under uv - that is the whole point of it"
    assert "tt_serve.py" in body or "cmd_state" in body, "and the default is the new brain"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 bin/table-talk --selftest`
Expected: FAIL with `AssertionError: the kill-switch has a name, and it is documented`

- [ ] **Step 3: Write the implementation**

Replace `cmd_serve` in `bin/table-talk`:

```python
def cmd_serve(port=None, force=False, legacy=False):
    """Launch the dashboard.

    Same name, same meaning, same URL: `serve` is the brain now. --legacy still
    execs the NiceGUI dashboard under uv, and stays until one release after
    parity - the kill-switch is a flag, not a git checkout.
    """
    if (msg := serve_refusal(os.environ, force)):
        sys.exit(msg)
    if not legacy:
        # force rides along: cmd_state guards the same way, and a --force the
        # user already gave must not be asked for again one call down.
        return cmd_state(port, force=force)
    dash = Path(__file__).resolve().parent / "table-talk-dash.py"
    uv = shutil.which("uv")
    if not uv:
        sys.exit("error: uv not found on PATH - install from https://docs.astral.sh/uv/")
    argv = [uv, "run", "--script", str(dash)] + (["--port", str(port)] if port is not None else [])
    os.execv(uv, argv)
```

and add to the `serve` subparser:

```python
    sv.add_argument("--legacy", action="store_true",
                    help="start the old NiceGUI dashboard instead (needs uv)")
```

with the dispatch becoming `cmd_serve(args.port, args.force, args.legacy)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 bin/table-talk --selftest && ./test.sh`
Expected: PASS

- [ ] **Step 5: Walk §3.4 of the spec line by line**

Run both dashboards side by side against the same data:

```bash
table-talk serve --legacy --port 8731 &
table-talk serve --port 8732 &
```

Create `docs/superpowers/notes/2026-09-08-parity-sweep.md` with one row per line of
the spec's §3.4 table: the feature, PASS/FAIL, and the note. The table is the
authority *and* the limit — a sweep with one row per §3.4 line can only find what
§3.4 lists, so anything the running `dash.py` shows that has no row there is a gap in
the **spec**, not a FAIL: add the row to §3.4 in this task's commit and then sweep it.
(The window footer and the `win-hot` border tint were found exactly this way and are
now rows in §3.4, built in Tasks 3, 8 and 20.) Every FAIL becomes a fix in `bin/tt_wall.py` or `ui/src` before this task is done. Then write the PR's **"diff of zero" audit**: the list of files this migration did not touch — `bin/tt_model.py`, `bin/tt_config.py`, `bin/themes.json`, `bin/tt-beat`, `bin/tt-ref`, `skill/SKILL.md`, and (through phase 5) `bin/tt.css` and `bin/table-talk-dash.py` — each with its `git log --oneline -1 <file>` line, so the claim is checkable rather than asserted.

- [ ] **Step 6: Update the README's Dashboard section**

Change the Dashboard section to say `table-talk serve` opens the new dashboard on the same URL, and that `table-talk serve --legacy` starts the NiceGUI one for one more release. Keep the `TABLE_TALK_DIR=docs/demo` line exactly as it is — it still works, on either.

- [ ] **Step 7: Commit**

```bash
git add bin/table-talk README.md docs/superpowers/notes/2026-09-08-parity-sweep.md
git commit -m "feat(cli): serve becomes the brain, with --legacy as the kill-switch"
```

---

## Phase 5 — the window

Three tasks, gated by Task 1. **If Task 1 recorded a FAIL on the glyph set, the tree guide or mermaid in Epiphany, skip Tasks 31–33 entirely**, say so in the PR, and ship the browser product — the plan is finished at Task 30 plus the docs in Task 35.

### Task 31: The Tauri shell

**Files:**
- Create: `shell/src-tauri/Cargo.toml`, `shell/src-tauri/tauri.conf.json`, `shell/src-tauri/src/main.rs`, `shell/src-tauri/icons/icon.png`, `shell/build.sh`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: a URL as `argv[1]`.
- Produces: the binary `table-talk-gui` — a window at that URL, titled `table-talk`, minimum 900×600, size and position remembered. No IPC, no commands, no sidecar, no injected JavaScript.

- [ ] **Step 1: Write the failing test**

The shell is a `WebviewWindowBuilder` and a URL, so its test is a build and a launch, not a unit test. Write the check as a script step first — create `shell/build.sh`:

```sh
#!/usr/bin/env bash
# The optional native window. Everything table-talk does works without it:
# with no binary installed, `table-talk gui` opens the same URL in a browser.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
if ! command -v cargo >/dev/null; then
    echo "error: shell/build.sh needs a Rust toolchain (https://rustup.rs)." >&2
    echo "The window is optional - table-talk gui falls back to your browser." >&2
    exit 2
fi
cd "$here/src-tauri"
cargo build --release
install -m 755 target/release/table-talk-gui "$here/table-talk-gui"
echo "built shell/table-talk-gui — copy it to ~/.local/bin to use it"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `chmod +x shell/build.sh && ./shell/build.sh`
Expected: FAIL — `could not find Cargo.toml`

- [ ] **Step 3: Write the implementation**

`shell/src-tauri/Cargo.toml`:

```toml
[package]
name = "table-talk-gui"
version = "0.1.0"
edition = "2021"
description = "table-talk's window"
license = "MIT"

[build-dependencies]
tauri-build = { version = "2", features = [] }

[dependencies]
tauri = { version = "2", features = [] }
tauri-plugin-window-state = "2"

[[bin]]
name = "table-talk-gui"
path = "src/main.rs"

[profile.release]
strip = true
lto = true
```

`shell/src-tauri/tauri.conf.json`:

```json
{
  "$schema": "https://schema.tauri.app/config/2",
  "productName": "table-talk",
  "version": "0.1.0",
  "identifier": "dev.tabletalk.gui",
  "build": { "frontendDist": "http://127.0.0.1:8731" },
  "app": {
    "withGlobalTauri": false,
    "security": { "csp": null },
    "windows": []
  },
  "bundle": {
    "active": true,
    "targets": ["deb", "appimage", "app", "dmg"],
    "icon": ["icons/icon.png"]
  }
}
```

`frontendDist` is a **URL, not a path**, and that is not cosmetic: this shell bundles
no frontend at all — every window is a `WebviewUrl::External` built from `argv[1]` —
and nothing in this plan ever creates a `shell/dist/`. `tauri_build::build()` (from
`build.rs`, on every `cargo build`) and `tauri::generate_context!()` resolve and
validate a *local* `frontendDist` at compile time, so `"../dist"` would fail the
build with "Unable to find your web assets" before `main()` ever ran. Tauri v2
accepts a URL there for exactly this case. The value is the default URL only because
it has to be something; the window navigates to whatever `table-talk gui` hands it.

`shell/src-tauri/src/main.rs`:

```rust
// table-talk's window: a URL, a title, and nothing else.
//
// No IPC, no commands, no sidecar, no injected JavaScript. Everything the app
// does it does over HTTP to the brain, which is also why the browser is a
// complete fallback rather than a degraded one.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::net::TcpStream;
use std::time::{Duration, Instant};
use tauri::{WebviewUrl, WebviewWindowBuilder};

const DEFAULT_URL: &str = "http://127.0.0.1:8731/";

fn host_port(url: &str) -> Option<String> {
    // Deliberately not a URL crate: the only URLs this binary is ever given
    // are http://host:port/ from `table-talk gui`.
    let rest = url.split("://").nth(1)?;
    let authority = rest.split('/').next()?;
    if authority.contains(':') { Some(authority.to_string()) } else { None }
}

fn wait_for(url: &str, budget: Duration) -> bool {
    // `table-talk gui` starts the brain and the window together, and the
    // window usually wins the race. Retry rather than fail into a blank page.
    let Some(addr) = host_port(url) else { return false };
    let deadline = Instant::now() + budget;
    while Instant::now() < deadline {
        if TcpStream::connect(&addr).is_ok() {
            return true;
        }
        std::thread::sleep(Duration::from_millis(200));
    }
    false
}

fn main() {
    let url = std::env::args().nth(1).unwrap_or_else(|| DEFAULT_URL.to_string());
    wait_for(&url, Duration::from_secs(10));
    let parsed: tauri::Url = url.parse().expect("argv[1] must be a URL");

    tauri::Builder::default()
        .plugin(tauri_plugin_window_state::Builder::default().build())
        .setup(move |app| {
            WebviewWindowBuilder::new(app, "main", WebviewUrl::External(parsed))
                .title("table-talk")
                .inner_size(1280.0, 860.0)
                .min_inner_size(900.0, 600.0)
                .build()?;
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("table-talk-gui failed to start");
}
```

`shell/src-tauri/build.rs`:

```rust
fn main() {
    tauri_build::build()
}
```

Create `shell/src-tauri/icons/icon.png` — a 512×512 PNG of the `❯` prompt glyph on the theme's surface colour:

Run: `python3 -c "import subprocess; subprocess.run(['convert','-size','512x512','xc:#1d2021','-fill','#83a598','-pointsize','360','-gravity','center','-annotate','0','>','shell/src-tauri/icons/icon.png'])"` — or draw one by hand; any 512×512 PNG works.

Add `shell/table-talk-gui` to `.gitignore` (the built binary is a release artifact, not a committed file).

- [ ] **Step 4: Run the build to verify it passes**

Run: `./shell/build.sh && ./shell/table-talk-gui http://127.0.0.1:8899/`
Expected: a native window titled `table-talk` showing the wall served by the brain from Task 17, with no URL bar and no browser tab. Close it and re-open: the size and position are remembered.

If `cargo build` fails with **"Unable to find your web assets"** or anything else
naming `frontendDist`, this toolchain's `tauri-build` wants a local directory: create
`shell/dist/index.html` holding one HTML comment (`<!-- never served: every window is
WebviewUrl::External -->`), point `frontendDist` at `"../dist"`, add the file to this
task's commit, and note in the commit message which `tauri-cli` version required it.
Do not chase the error anywhere else — this is the only thing in the shell that reads
a path off disk at build time.

- [ ] **Step 5: Commit**

```bash
git add shell .gitignore
git commit -m "feat(shell): a Tauri window, a URL, and nothing else"
```

### Task 32: `table-talk gui`

**Files:**
- Modify: `bin/table-talk` (`cmd_gui`, `open_window`, the `gui` subparser, `selftest`)
- Modify: `bin/tt_serve.py` (`serve(port=None, claimed=False)`)

**Interfaces:**
- Consumes: `tt_serve.claim` / `tt_serve.release` / `tt_serve.serve` (Task 17), `serve_refusal` (unchanged).
- Produces:
  - `open_window(url, run=subprocess.Popen, browser=None) -> str` — returns `"shell"` or `"browser"`, naming what it opened.
  - `cmd_gui(port=None, force=False)` — claims the lock, opens a window, and becomes the brain if it won the claim.

- [ ] **Step 1: Write the failing test**

Add to `bin/table-talk`'s `selftest()`:

```python
    seen = []
    fake_browser = type("B", (), {"open": staticmethod(lambda u: seen.append(("browser", u)))})
    os.environ.pop("TABLE_TALK_SHELL", None)
    assert open_window("http://127.0.0.1:8731/", run=lambda a: seen.append(("run", a)),
                       browser=fake_browser) == "browser", \
        "with no binary installed the GUI is never unavailable - it opens a browser"
    assert seen == [("browser", "http://127.0.0.1:8731/")], "and opens exactly the URL it was given"
    seen.clear()
    os.environ["TABLE_TALK_SHELL"] = "/bin/true"
    assert open_window("http://127.0.0.1:8899/", run=lambda a: seen.append(("run", a)),
                       browser=fake_browser) == "shell", "TABLE_TALK_SHELL wins when it is set"
    assert seen == [("run", ["/bin/true", "http://127.0.0.1:8899/"])], "argv list, never a shell string"
    seen.clear()
    def boom(argv):
        raise OSError("no such binary")
    assert open_window("http://127.0.0.1:8899/", run=boom, browser=fake_browser) == "browser", \
        "a broken binary falls back rather than leaving the user with nothing"
    os.environ.pop("TABLE_TALK_SHELL", None)
    assert "gui" in Path(__file__).resolve().read_text(), "the command exists"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 bin/table-talk --selftest`
Expected: FAIL with `NameError: name 'open_window' is not defined`

- [ ] **Step 3: Write the implementation**

In `bin/table-talk`:

```python
def open_window(url, run=subprocess.Popen, browser=None):
    """The native window if one is installed, the default browser otherwise.

    The GUI is never unavailable: the window is an optional 8 MB download and
    the same URL works in any browser, over SSH included."""
    exe = os.environ.get("TABLE_TALK_SHELL") or shutil.which("table-talk-gui")
    if exe:
        try:
            run([exe, url])
            return "shell"
        except OSError:
            pass
    (browser or __import__("webbrowser")).open(url)
    return "browser"


def cmd_gui(port=None, force=False):
    """Open table-talk's window, starting the brain if nobody else has.

    Exactly one process holds .ui/gui.lock and binds the port; every other
    invocation just opens a window on it. That is the whole reason for the
    lock: two launches both seeing the port unbound is a real race, and
    "poll GET / for ten seconds then decide" is how you lose it.
    """
    if (msg := serve_refusal(os.environ, force)):
        sys.exit(msg)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import tt_serve                    # lazy: needs 3.11 through tt_config
    want = port if port is not None else tt_serve.tt_config.load()["server"]["port"]
    url, owner = tt_serve.claim(want)
    open_window(url)
    if owner:
        tt_serve.serve(want, claimed=True)      # never returns
```

with the subparser:

```python
    g = sub.add_parser("gui", help="open table-talk's window (starts it if needed)")
    g.add_argument("--port", type=int, help="default: server.port from the config (8731)")
    g.add_argument("--force", action="store_true", help="start even inside a Claude Code session")
```

and the dispatch `elif args.cmd == "gui": cmd_gui(args.port, args.force)`.

In `bin/tt_serve.py`, change `serve`'s signature to `def serve(port=None, claimed=False):` and its claim block to:

```python
    if claimed:
        url, owner = url_for(want), True     # cmd_gui already holds the lock
    else:
        url, owner = claim(want)
    if not owner:
        sys.exit(f"error: table-talk is already running: {url}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 bin/table-talk --selftest && python3 bin/tt_serve.py --selftest`
Expected: PASS

- [ ] **Step 5: Prove the phase's product**

Run: `table-talk gui` in one terminal, then `table-talk gui` again in a second.
Expected: the first opens a window **and** becomes the brain; the second opens a second window on the same brain and exits, with no second bind and no error.

- [ ] **Step 6: Commit**

```bash
git add bin/table-talk bin/tt_serve.py
git commit -m "feat(cli): table-talk gui - one command, one lock, one window"
```

### Task 33: Releases, the platform coverage table, and install

**Files:**
- Create: `.github/workflows/release.yml`
- Modify: `install.sh`, `README.md`
- Modify: `docs/webview-gate.md` (the coverage table, filled in by checking)

**Interfaces:**
- Consumes: `shell/build.sh` (Task 31).
- Produces: a two-runner release matrix, and a filled-in platform coverage table.

- [ ] **Step 1: Write the failing test**

Create `.github/workflows/release.yml`:

```yaml
name: release

# Two runners, no cross-compilation: npm installs the host's prebuilt toolchain
# and cargo builds for the host. ubuntu-22.04 deliberately, for an older glibc.
on:
  push:
    tags: ["v*"]
  workflow_dispatch:

# Read is all a build needs: these jobs upload artifacts, they do not publish a
# release. Widen it in the commit that adds a publishing step, not before.
permissions:
  contents: read

jobs:
  shell:
    strategy:
      matrix:
        include:
          - runner: ubuntu-22.04
            target: x86_64-unknown-linux-gnu
          - runner: macos-14
            target: universal-apple-darwin
    runs-on: ${{ matrix.runner }}
    steps:
      - uses: actions/checkout@v4
      - uses: dtolnay/rust-toolchain@stable
        with: { targets: "${{ matrix.target }}" }
      - name: linux webview deps
        if: runner.os == 'Linux'
        run: sudo apt-get update && sudo apt-get install -y libwebkit2gtk-4.1-dev libayatana-appindicator3-dev librsvg2-dev
      - run: cargo install tauri-cli --version "^2" --locked
      - run: cd shell/src-tauri && cargo tauri build --target ${{ matrix.target }}
      # A tagged release must not silently diverge from its config.
      - name: metadata matches tauri.conf.json
        run: |
          python3 - <<'PY'
          import json, pathlib, sys
          cfg = json.loads(pathlib.Path("shell/src-tauri/tauri.conf.json").read_text())
          toml = pathlib.Path("shell/src-tauri/Cargo.toml").read_text()
          assert f'version = "{cfg["version"]}"' in toml, "Cargo.toml and tauri.conf.json disagree on version"
          assert cfg["identifier"] == "dev.tabletalk.gui", "bundle identifier changed"
          assert cfg["productName"] == "table-talk", "product name changed"
          print("metadata ok")
          PY
      - uses: actions/upload-artifact@v4
        with:
          name: table-talk-gui-${{ matrix.target }}
          path: |
            shell/src-tauri/target/${{ matrix.target }}/release/bundle/**/*.deb
            shell/src-tauri/target/${{ matrix.target }}/release/bundle/**/*.AppImage
            shell/src-tauri/target/${{ matrix.target }}/release/bundle/**/*.dmg
```

- [ ] **Step 2: Run it to verify it fails**

Trigger it from **this task's branch**, through the `workflow_dispatch` the `on:`
block already declares:

```bash
git push -u origin HEAD                       # the branch this task is being done on
gh workflow run release.yml --ref "$(git rev-parse --abbrev-ref HEAD)"
gh run watch
```

Expected: FAIL on the first run if the metadata check disagrees, or if the Linux webview dependencies are missing — which is what the step is for.

**Do not push a tag to verify this.** A tag on the shared remote is a public,
repo-wide event with an Actions run and artifacts behind it, and this repo's rule is
issue → branch → PR for anything that touches it. If the workflow is ever cut off
from `workflow_dispatch` and only a tag can exercise it, that is a question for the
maintainer, not a plan step — ask, and do not push the tag on your own.

- [ ] **Step 3: Make it pass and fill in the coverage table**

Fix whatever the run reported, then **verify each row by running it** and record the result in `docs/webview-gate.md` under a `## Platform coverage` heading. Until a row says "verified", it is a claim:

| target | artifact | what to verify | result |
|---|---|---|---|
| Fedora 42+ / Wayland | `table-talk-gui` | window opens, `tt.css` renders, clipboard round-trips, mermaid draws | *fill in* |
| Ubuntu 24.04 / Debian 13 x86_64 | `.deb` | installs, `libwebkit2gtk-4.1` resolves from the distro | *fill in* |
| older/other Linux x86_64 | `.AppImage` | runs on a glibc at least as old as the `ubuntu-22.04` runner's — name the exact version | *fill in* |
| linux-arm64 | none built | falls back to the browser | *fill in* |
| macOS 14+, Apple Silicon | `.app` | opens, and is signed or documents the right-click-Open path | *fill in* |
| macOS 13 / Intel | `.app` (universal) | that the universal build actually launches | *fill in* |
| Windows | none | out of scope, WSL only | unchanged |

- [ ] **Step 4: Update install.sh**

`install.sh` installs the CLI and the skill and nothing else; the window is an optional download. Add, after the `installed:` echo:

```sh
# The window is optional and separate on purpose: the CLI plus a browser is a
# complete product, and an 8 MB binary is not something an install script
# should fetch behind your back.
if ! command -v table-talk-gui >/dev/null; then
    echo "note: 'table-talk gui' will open your browser."
    echo "      for a native window, download table-talk-gui from Releases into ~/.local/bin,"
    echo "      or build it: cd shell && ./build.sh"
fi
```

- [ ] **Step 5: Verify install.sh still works**

Run: `./install.sh --no-hook`
Expected: `installed: ~/.local/bin/table-talk, skill -> …`, then the note.

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/release.yml install.sh README.md docs/webview-gate.md
git commit -m "ci: build the window on two runners, and record what was actually verified"
```

---

## Phase 6 — deletion

Two tasks, **one release after parity**. From here the kill-switch is the previous release tag; it can be retired because the new GUI answers on the same URL in any browser, so the only failure mode phase 6 could introduce is "the window binary is broken", and the browser path covers that without NiceGUI.

### Task 34: Remove NiceGUI and `uv`

**Files:**
- Delete: `bin/table-talk-dash.py` (3257 lines)
- Modify: `bin/table-talk` (drop `--legacy` and the `uv` lookup), `bin/tt_model.py` (drop `marked` and its property test), `bin/tt.css` (drop the NiceGUI-specific rules), `install.sh` (the `chmod` list), `test.sh`, `.github/workflows/test.yml`
- Test: `bin/table-talk` (`selftest()`), `bin/tt_model.py` (`selftest()`), `bin/tt_serve.py` (`selftest()`)

**Interfaces:**
- Consumes: nothing new.
- Produces: a repo whose runtime dependency is `python3 >= 3.11` and a browser. `cmd_serve(port=None, force=False)` — the `legacy` parameter is gone; `serve` is an alias for `state`.

- [ ] **Step 1: Write the failing test**

Add to `bin/table-talk`'s `selftest()`:

```python
    src = Path(__file__).resolve().read_text()
    assert 'shutil.which("uv")' not in src, "the runtime has no uv: the brain is stdlib python3"
    assert "table-talk-dash" not in src, "and no NiceGUI dashboard to exec into"
    assert "--legacy" not in src, "the kill-switch is the previous release tag now"
```

and to `bin/tt_serve.py`'s `selftest()` — the same "does the repo still name a file
that is gone?" check, pointed at the one script that would otherwise break on a fresh
clone. `install.sh` runs under `set -euo pipefail`, so a `chmod +x` on a path that no
longer exists exits non-zero and aborts the whole install: no `~/.local/bin/table-talk`
symlink, no skill link, no data dir. That is `git clone && ./install.sh` broken for
every new user, from the deletion commit onward:

```python
    install = (HERE.parent / "install.sh").read_text()
    for word in install.split():
        if word.startswith('"$here/bin/'):
            named = word.strip('"').replace("$here", str(HERE.parent))
            assert Path(named).exists(), \
                f"install.sh chmods {named}, which is not in the repo - set -e aborts the install"
```

and to `bin/tt_model.py`'s `selftest()`, replacing the `marked` assertions at lines 770-785:

```python
    assert "marked" not in globals(), \
        "marked() is gone: Svelte renders a text node, so highlighting cannot leak markup. parts() stays."
    assert parts("a & b", "&amp;") == [("a & b", False)], "parts still never self-matches an entity"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 bin/table-talk --selftest`
Expected: FAIL with `AssertionError: the runtime has no uv: the brain is stdlib python3`

- [ ] **Step 3: Do the deletion**

```bash
git rm bin/table-talk-dash.py
```

In `install.sh`, **in this same commit**, drop the deleted file from the `chmod`
line — the commit that removes the file is the commit that breaks the install, so
they cannot be separated:

```sh
chmod +x "$here/bin/table-talk" "$here/bin/tt-beat"
```

In `bin/table-talk`, replace `cmd_serve` with:

```python
def cmd_serve(port=None, force=False):
    """The dashboard. Same name, same meaning, same URL - the brain is what it
    starts now, and `gui` is the same thing with a window in front of it."""
    if (msg := serve_refusal(os.environ, force)):
        sys.exit(msg)
    cmd_state(port, force=force)
```

drop the `--legacy` argument from the `serve` subparser and the `legacy` argument at the call site, and drop the now-unused `import shutil` if nothing else uses it (`open_window` does, so keep it).

In `bin/tt_model.py`: delete `marked()` (lines 407-425), its selftest block, and the now-unused `import html`, keeping `parts()` and every `parts` assertion. In `bin/tt.css`: delete the three NiceGUI-specific rule blocks — `.dw-find .q-field__native` and its `::placeholder` sibling, `.dw-find .q-icon`, and `.cfg-row .q-field` — and reword the `.nicegui-content` comment above `.tt-app` to name `#app` instead. Leave `body.body--dark` exactly as it is: `App.svelte` sets that class now, and `tt_config.selftest`'s cross-check reads it.

In `test.sh`, the whole file becomes:

```sh
#!/usr/bin/env bash
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
python3 "$here/bin/table-talk"  --selftest
python3 "$here/bin/tt_model.py"  --selftest
python3 "$here/bin/tt_config.py" --selftest
python3 "$here/bin/tt_wall.py"   --selftest
python3 "$here/bin/tt_serve.py"  --selftest
command -v node >/dev/null && node --test "$here/ui/test/" || echo "node absent - skipped ui/test"
echo "all selftests passed"
```

In `.github/workflows/test.yml`, the `test` job loses `astral-sh/setup-uv` and gains `actions/setup-python@v5` with `python-version: "3.12"`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `./test.sh && node ui/e2e.mjs --browser=chromium`
Expected: `all selftests passed`, then the e2e script passes — against the only dashboard left.

Then prove the install still works on the tree that no longer has the dashboard:

Run: `./install.sh --no-hook`
Expected: `installed: ~/.local/bin/table-talk, skill -> …`, and exit 0. A non-zero
exit here means the `chmod` line still names a deleted file.

- [ ] **Step 5: Count what left**

Run: `git diff --stat HEAD~1 -- bin/ test.sh`
Expected: roughly 3300 deletions against a few dozen insertions. Put the number in the PR body.

- [ ] **Step 6: Commit**

```bash
git add -A bin install.sh test.sh .github/workflows/test.yml
git commit -m "refactor: delete the NiceGUI dashboard and uv from the runtime"
```

### Task 35: The documentation

**Files:**
- Modify: `README.md`, `docs/config.example.toml`, `install.sh`

**Interfaces:**
- Consumes: everything above.
- Produces: documentation that matches the shipped product, including the honest Python floor.

- [ ] **Step 1: Write the failing test**

Add to `bin/tt_config.py`… no — the config file is not edited, so the check belongs where the claim lives. Add to `bin/tt_serve.py`'s `selftest()`:

```python
    readme = (HERE.parent / "README.md").read_text()
    assert "nicegui" not in readme.lower(), "the README must not promise a dependency the product does not have"
    assert "uv" not in readme.split("## Test")[0].replace("uv run", ""), \
        "nor uv in the install path"
    assert "python3 >= 3.11" in readme or "Python 3.11" in readme, \
        "the brain needs tomllib, so the floor moved from 3.10 to 3.11 for the GUI - say so"
    assert "table-talk gui" in readme, "one command opens the window"
    example = (HERE.parent / "docs" / "config.example.toml").read_text()
    assert "restart" not in example.lower(), \
        "the restart note is stale: the brain rebinds the listener in place"
    for key in ("server.host", "server.port", "server.poll_seconds", "ui.view", "ui.columns",
                "ui.drawer_open", "ui.filter_debounce_ms", "ui.collapsed_sections",
                "links.open_command", "links.extra_roots", "theme.default",
                "theme.dark_theme", "theme.light_theme"):
        section, _, name = key.rpartition(".")
        assert name in example, f"{key} is still a real config key and still belongs in the example"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 bin/tt_serve.py --selftest`
Expected: FAIL with `AssertionError: the README must not promise a dependency the product does not have`

- [ ] **Step 3: Rewrite the documentation**

`README.md`:

- **Install:** `git clone && ./install.sh` — unchanged. Add: that alone gives a working GUI, because `table-talk gui` opens the default browser; the native window is an optional `table-talk-gui` download from Releases into `~/.local/bin/`, or `cd shell && ./build.sh`.
- **Dashboard:** `table-talk gui` opens the window; `table-talk serve` runs the brain without one; `table-talk url` is unchanged; the URL works in any browser, over SSH included. Delete every mention of NiceGUI and `uv`.
- **A new "Requirements" paragraph:** the runtime is `python3 >= 3.11` and a webview or a browser. `bin/table-talk` itself still runs on 3.10, so recording never needs 3.11 — only the dashboard does, because it reads TOML through `tomllib`. On a box with system Python 3.10 (Ubuntu 22.04, Debian 12), `uv run --script bin/tt_serve.py` still works: the file carries a PEP 723 header declaring `requires-python = ">=3.11"` and no dependencies.
- **Themes:** unchanged except that the palette is served from `/themes.css` rather than injected by NiceGUI.
- **Starting work from the wall:** already gone — #226 deleted that section, along with `start_job`/`run_job` and `claude-agent-sdk`, ahead of this migration. Nothing to do here; the jobs *section* (task rows) is still on the wall, unaffected.
- **Test:** `./test.sh` runs five Python selftests and, when Node is present, the `fmt` unit tests. Add: `node ui/e2e.mjs --browser=chromium` needs Playwright and is what CI runs.
- **Contributing:** add that `bin/web/` is committed build output — change `ui/`, run `./ui/build.sh`, and commit both, because CI diffs them.

`docs/config.example.toml`: delete the note that says a change needs a restart, and replace it with one line: a change takes effect on the next poll; `server.port` moves the listener in place and the page says where it went.

- [ ] **Step 4: Run test to verify it passes**

Run: `./test.sh`
Expected: PASS

- [ ] **Step 5: Read the README as a stranger**

Run, in a scratch clone of the post-deletion tree so nothing is borrowed from an
install that already exists:

```bash
git clone . /tmp/tt-fresh && cd /tmp/tt-fresh && ./install.sh --no-hook
TABLE_TALK_DIR=docs/demo table-talk gui --port 8899
```

Expected: `install.sh` exits 0 and prints `installed: …` (it is the only thing a new
user runs, and phase 6 deleted a file its `chmod` line used to name), then the
command in the README's first Dashboard paragraph works exactly as written.

- [ ] **Step 6: Commit**

```bash
git add README.md docs/config.example.toml install.sh bin/tt_serve.py
git commit -m "docs: the product as it now is - no NiceGUI, no uv, a window and a browser"
```

---

## Self-review

Run against the spec after the plan was written, per the writing-plans skill.

**1. Spec coverage.** Every section of `docs/superpowers/specs/2026-09-08-svelte-gui-design.md`, and the task that implements it:

| spec section | tasks |
|---|---|
| §3.1 architecture, who owns what, HTTP not stdio | 14–17 |
| §3.1 startup and the lock | 17, 32 |
| §3.2 `bin/tt_wall.py` — `Store`, `Wall`, `KEYMAP`, `frame`, `--dump-fixtures` | 2–13 |
| §3.2 `bin/tt_serve.py` — static, SSE, `/do`, token, `Origin`, lock, rebind | 14–17 |
| §3.2 every Svelte unit in the table | 18–27 (`frame.svelte.js` 18, `fmt.js` 18, `App` 19, `Wall`/`Window` 20, `Section`/`Row`/`Spans` 21, `Meter`/`Reply` 22, `Diagram` 23, `Drawer` 24, `Statusline`/`Keys` 25, `Settings` 26) |
| §3.2 the Tauri shell | 31 |
| §3.3 data flow, frame shape, no relative time, polling, frame skipping | 6–13, 16 |
| §3.4 feature parity, every row | 6–13 (brain) and 20–27 (view), swept line by line in 30. Three rows were added to §3.4 while reviewing — the window footer, the `win-hot` tint and the copy-id kind rule — because the sweep can only find what the table lists |
| §3.5 error handling, every row | 8 (per-window guard), 16 (bad tick, SSE), 17 (lock, port, missing bundle), 15 (hostile `/do`), 18 (reconnect, unknown version), 22 (clipboard denied) |
| §3.5 security stance | 15 (token, `Origin`, argv list, `shell=` ban), 14 (token-free `GET /`) |
| §3.6 testing — `test.sh`, `tt_wall.selftest`, `tt_serve.selftest`, fixtures, `node --test`, e2e, CI | 13, 17, 18, 28, 29, and the selftest step of every task |
| §3.7 migration path, phases 0–6, kill-switch | 1, 12, 17, 27, 30, 32, 34 |
| §3.8 repo layout at the end | 34, 35 |
| §3.9 distribution, install, the 3.11 floor, coverage table | 33, 35 |
| §3.10 config and UI-state, `.ui/` files | 2 (`wall.json`), 15 (`token`), 17 (`gui.lock`), 35 |
| §3.11 risks — bundle drift, relative-time leak, frame size, CSRF, two brains, 3.10 | 29, 12, 8, 15, 17, 35 |
| §4 cuts (no TS port, no gpuix, no stdio bridge, no injected JS, no `marked`, no restart offer, no delta protocol, no watcher, no state DB, no component framework) | none of them appear in any task; the restart offer's replacement is Task 16's `reload_config`, and `marked` is deleted in Task 34 |
| §5 Q1–Q6 | Q1 built and gated (1, 31–33), Q2 deleted (34), Q3 already resolved by #226 before this plan (no task here), Q4 server-side (24), Q5 unsigned releases (33), Q6 not revisited — nothing here depends on gpuix |
| Feature roadmap #1–#7 | out of scope by design; #1 and #2 land as parity in 31 and 25 |

**Gaps found and closed while reviewing:**
- The spec's frame carries `spin`/`last_ok` *and* skips unchanged frames. Split into `event: frame` and `event: tick` (Deviation 1, Task 16), or the spinner would freeze on an idle wall.
- `EventSource` cannot set a header, so `/state` accepts the token in the query string (Task 16); `/do` stays header-gated.
- `_RANGES` holds `float("inf")`, which `json.dumps` emits as the invalid token `Infinity`. Task 11 maps it to `null` and pins it.
- `ui.collapsed_sections` names sections as the config does (`"glossary"`), not by internal id (`"gls"`). Pinned in Task 7.
- Fixtures must not carry `$HOME` paths or the user's own config: Task 13 drops `drawer.ctx`, `drawer.sig` and `settings`, and rewrites the demo dir path to `<DEMO>`.
- The spec says `tt_model.py` is never edited, and also that phase 6 removes `tt_model.marked`. The Global Constraints now say "not before phase 6", and Task 34 does exactly that one removal.
- `bin/table-talk` is 3.10-clean, but `table-talk state --once` is not: it spawns the brain, which needs `tomllib`. Task 12's CLI assertion is guarded by `sys.version_info >= (3, 11)` so the `cli-oldest-python` job keeps passing — the 3.10 promise is about **recording**, and it stays exactly as strong.
- **The window footer** (`resolved_cells`, `.win-f`, `.cells`) — the `▰▱` obligation tally and `N/M resolved · all clear` at the bottom of every card (`table-talk-dash.py:2698`, `:2991-2998`) — was in neither `reads/ui.md` §1 nor spec §3.4, so no task built it, and deferring it to Task 30's sweep would not have worked: that sweep is specified as one row per §3.4 line, and §3.4 had no row for it. It is a real, always-visible element and the brief says every feature is preserved or explicitly argued away, so it is **built**: `resolved_cells` in Task 3, `window.footer` in Task 8 (on folded cards too — `tt.css:150` hides only `.win-b`), `.win-f` in Task 20, a row in spec §3.4, and an e2e check that counts one footer per window. Task 30 now says in so many words that a missing feature with no §3.4 row is a spec gap to close, not a FAIL to file.
- Found beside it, and fixed the same way: `Window.svelte` did not set **`win-hot`** (`dress()` adds it whenever a window has open actions — `tt.css:148` tints the border), and `Row.svelte`'s `ID_CLASS` mapped `done` to `id-act`, which would have coloured a resolved id as an open one; `dash.py` uses `id-ok` (`:1063`). Both are one token in Task 20/21, plus a §3.4 row for `win-hot`.
- **The id button had no click handler at all.** `row.copy` was minted, shipped, and used only to disable the button — clicking an id copied nothing, which is `COPY_JS`'s whole job. Task 21 now carries `copyId`, with the flash inside `.then()` (spec §3.4's rule: a denied clipboard must never claim success).
- **`row_for` handed every kind a `copy` string.** `copy_for`'s only refusal is a non-hex id, and every id the CLI mints is `secrets.token_hex(2)` — so in production terms and diagrams would have become clickable, copyable ids they have never been (`_term_row`/`_diagram_row` render a plain label). The gate is by **kind** now, and Task 6's selftest uses a real hex id for those two cases; the old synthetic `"g1"` passed through the hex guard and proved nothing.
- **`Wall.warn` was set but never cleared.** Task 16 now clears every wall's warning on each config reload before deciding what to say, so the statusline stops carrying "still on 8731" after the admin reverts the edit. `dash.py`'s `restart_offer` recomputed that message every poll; a latched flag is not the same contract.
- **The in-place port rebind ended the process.** `serve()` called `brain.httpd.serve_forever()` once, and `reload_config`'s `old.shutdown()` is exactly what makes that call return — so control fell into `finally`, `serve()` returned, `__main__` ended, and CPython killed the daemon thread carrying the replacement listener. Task 17's `serve()` now loops and re-reads `brain.httpd`, Task 16 no longer starts a thread at all (it does not need one), the lock is rewritten with the new port, and Task 17's selftest drives a real subprocess through a config edit — the only shape of test that can fail on this.
- **`Diagram.svelte`'s catch path wrote into the `{@html}` variable.** Mermaid quotes the offending source back in its parse-error message, and that source is a log line, so a crafted diagram could have put live markup on the page holding `window.TT_TOKEN`. `svg` and `error` are separate states now, `{error}` is a text node, and Task 23's e2e check feeds a diagram built to fail with `<img onerror>` in it.
- **`Reply.svelte`'s drafts Map was per-instance.** A `const` in a `.svelte` file's ordinary `<script>` runs once per component, not once per module, so the one parity rule the Map exists for — a draft surviving a row leaving and re-entering the wall — silently did not hold, and the existing e2e check could not see it because the keyed `{#each}` keeps the same instance across a poll. It is a `<script module>` block now, with an e2e check that shuts and reopens the section.
- **`table-talk state` (without `--once`) had no `serve_refusal`.** It is SUPPRESSed from `--help` but fully invocable, and it never returns — the exact hazard the guard exists for. Task 17 routes it through the same check as `serve` and `gui`, and `cmd_serve` passes `force` down so `--force` is not swallowed.
- **`install.sh` would have aborted from the phase-6 commit onward.** Its `chmod +x` names `bin/table-talk-dash.py`, and under `set -euo pipefail` a chmod on a deleted path exits non-zero before the CLI symlink, the skill link or the hook offer. Task 34 edits the chmod line in the deletion commit, adds a selftest that no `"$here/bin/…"` path in `install.sh` is missing from the repo, and Task 35 runs `./install.sh --no-hook` in a fresh clone.
- **Two small duplications deleted rather than documented:** the spinner sequence is Python's alone (the wire carries the glyph, not an index), and `ui/e2e.mjs` registers its checks with stdlib `node:test` instead of hand-rolling registration, reporting and `process.exit`. The drawer's `meter.cells` went too — `dash.py`'s htop bar is a CSS-sized `.trk`, so the glyph tuple was a field on every drawer row that no view would ever read.
- **Task 33 verified the release workflow by pushing a tag to `origin`.** It uses the `workflow_dispatch` already declared in the workflow, from the task's own branch, and says explicitly that pushing a tag is the maintainer's call; `permissions` drops to `contents: read`, which is all an artifact-uploading build needs.
- **`tauri.conf.json`'s `frontendDist` pointed at a `shell/dist/` no task creates.** `tauri-build` validates a local `frontendDist` at compile time, so the first `cargo build` would have failed. It names the loopback URL instead — this shell bundles no assets — with the placeholder-directory fallback written into Task 31 Step 4 rather than left for an executor to debug.
- **The phase-0 gate was opened over `file://`.** Clipboard access is gated on secure-context status, which loopback HTTP has by spec and `file://` does not reliably have in WebKit; a false FAIL there would have dropped phase 5 for the wrong reason. Task 1 serves it with `python3 -m http.server` on `127.0.0.1`.

**2. Placeholder scan.** No `TBD`, no "add validation", no "handle edge cases", no "similar to Task N". Three tasks hand a later task a named replacement (Task 21's inline meter/mermaid/reply lines, replaced in Tasks 22–23 with the exact code given there); each names the exact line and the exact replacement. Two tasks are manual by nature — Task 1 (serve the gate and look at it in two browsers) and Task 33's coverage table (run each artifact) — and both give exact commands and an exact file to record the result in. One task carries a named contingency rather than a placeholder: Task 31 Step 4 says what to do if this `tauri-build` version refuses a URL `frontendDist`, with the exact file, the exact contents and the exact config value, so an executor is never left interpreting an unattributed build error.

**3. Type consistency.** Checked across tasks: `Store.get/put/flush`, `resolved_cells`, `Wall.apply/target/frame/wall_view/drawer_view/status_view/settings_view/apply_fold_rules`, `dim`→bool (renamed from `dash.py`'s `_dim`→str, noted in Task 3), `hits` (from `_hits`), `cell_spans`/`art_lines`/`copy_for`/`meter_for`/`row_for`, `sections_for`, `window_for`/`win_sig`, `read_states`, `link_roots`/`set_roots`/`link_spans`, `transcripts`/`live_sessions`/`nearest_claude_md`, `theme_css`/`form_updates`/`coerce`, `dump_fixtures`, `FRAME_V`; `Brain.wall/tick/fold/reload_config/wake`, `envelope`, `sse_skip`, `sse_write`, `open_target`, `apply_config`, `mint_token`, `claim`/`relock`/`release`/`lock_path`/`url_for`, `serve(port, claimed)`; `cmd_state(port, once, force)`, `cmd_serve(port, force, legacy)` → `cmd_serve(port, force)` in Task 34 (the signature change is the pinned assertion there), `cmd_gui(port, force)`, `open_window(url, run, browser)`; `ago`/`hm`/`live_delay`/`LIVE_WINDOW`; `app`/`connect`/`send`. The frame's key names (`wall.cols/columns/empty`, `windows[k].sig/project/sid/tx/latest/flags/footer/sections/error`, `sections[].id/title/n/open/forced/bar/empty/rows`, `rows[].kind/id/label/sid/copy/ts/changed/dim/cursor/reply/cells`, `cells[].c/spans/lines/meter/src`, `meter.kind/pct/cells/read_ts/on`, `footer.cells/text`, `drawer.open/sig/sort/hits/sessions/projects/projects_list/ctx`, `projects_list[].meter.open/tasks/pct` (no `cells`), `status.tally/text/poll_seconds/scope/cols/keymap/port_warn` plus the server's `spin` (a glyph) and `last_ok`, `theme.mode`, `settings.fields[].key/kind/value/choices/bounds`, `focus`, `warn`) are defined once in Tasks 6–13 and used with the same names in Tasks 20–27 and in `ui/e2e.mjs`.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-09-08-svelte-gui-migration.md`. Two execution options:

**1. Subagent-Driven (recommended)** — a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — execute tasks in this session using `superpowers:executing-plans`, batch execution with checkpoints.

Which approach?
