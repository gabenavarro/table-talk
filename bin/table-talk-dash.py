#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["nicegui>=3.16,<4"]
# ///
"""table-talk dashboard: live NiceGUI view of the table-talk event logs."""
import argparse
import json
import os
import time
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(os.environ.get("TABLE_TALK_DIR") or str(Path.home() / ".local/share/table-talk"))

# Catppuccin palettes (anuppuccin): Latte on light, Mocha on dark.
# Quasar's dark plugin toggles body--dark, so the variables swap with the theme.
THEME_CSS = """<style>
:root{
  --ctp-base:#eff1f5; --ctp-mantle:#e6e9ef; --ctp-crust:#dce0e8;
  --ctp-surface0:#ccd0da; --ctp-surface1:#bcc0cc;
  --ctp-text:#4c4f69; --ctp-subtext:#6c6f85;
  --ctp-red:#d20f39; --ctp-blue:#1e66f5; --ctp-green:#40a02b;
  --ctp-mauve:#8839ef; --ctp-lavender:#7287fd; --ctp-peach:#fe640b;
  --q-primary:#8839ef;
}
body.body--dark{
  --ctp-base:#1e1e2e; --ctp-mantle:#181825; --ctp-crust:#11111b;
  --ctp-surface0:#313244; --ctp-surface1:#45475a;
  --ctp-text:#cdd6f4; --ctp-subtext:#a6adc8;
  --ctp-red:#f38ba8; --ctp-blue:#89b4fa; --ctp-green:#a6e3a1;
  --ctp-mauve:#cba6f7; --ctp-lavender:#b4befe; --ctp-peach:#fab387;
  --q-primary:#cba6f7;
}
body{ background:var(--ctp-base); color:var(--ctp-text); }
.tt-header{ background:color-mix(in srgb, var(--ctp-mantle) 90%, transparent);
  color:var(--ctp-text); backdrop-filter:blur(8px);
  border-bottom:1px solid var(--ctp-surface0); }
.tt-title{ font-family:ui-monospace,'JetBrains Mono','Fira Code',monospace;
  letter-spacing:.05em; color:var(--ctp-mauve); }
.tt-session{ font-family:ui-monospace,'JetBrains Mono','Fira Code',monospace;
  color:var(--ctp-lavender); }
.tt-sec{ font-size:.72rem; font-weight:700; letter-spacing:.09em;
  text-transform:uppercase; color:var(--ctp-subtext); }
.nicegui-content .q-card{ background:var(--ctp-mantle); color:var(--ctp-text);
  border:1px solid var(--ctp-surface0); border-radius:14px;
  box-shadow:0 1px 3px color-mix(in srgb, var(--ctp-crust) 60%, transparent); }
.q-table{ background:transparent; color:var(--ctp-text); }
.q-table__card{ background:transparent; box-shadow:none; color:var(--ctp-text); }
.q-table th{ color:var(--ctp-subtext); font-weight:600; border-color:var(--ctp-surface0); }
.q-table td{ border-color:var(--ctp-surface0); }
.q-table tbody tr:hover{ background:color-mix(in srgb, var(--ctp-surface0) 45%, transparent); }
.q-table__bottom{ color:var(--ctp-subtext); }
.q-expansion-item .q-item{ color:var(--ctp-subtext); }
.nicegui-content{ max-width:1100px; margin:0 auto; }
.tt td,.tt th{ font-variant-numeric:tabular-nums; }
.tt td{ white-space:normal; overflow-wrap:anywhere; vertical-align:top; }
.tt td:first-child,.tt th:first-child{ font-family:ui-monospace,'JetBrains Mono','Fira Code',monospace; }
.tt td:first-child{ white-space:nowrap; }
.tt-age{ color:var(--ctp-subtext); }
.tt-clear{ color:var(--ctp-green); font-size:.85rem; }
.tt-toast{ position:fixed; top:64px; right:16px; z-index:9999; background:var(--ctp-mantle);
  color:var(--ctp-red); border:1px solid var(--ctp-red); border-radius:10px;
  padding:.5rem .9rem; font-weight:600; opacity:0; transform:translateY(-6px);
  transition:all .3s ease; box-shadow:0 4px 14px color-mix(in srgb, var(--ctp-crust) 60%, transparent); }
.tt-toast-in{ opacity:1; transform:none; }
.tt-search .q-field__native{ color:var(--ctp-text); }
.tt-search .q-field__native::placeholder{ color:var(--ctp-subtext); opacity:.7; }
.tt-rollup{ color:var(--ctp-subtext); }
.tt-rollup-hot{ color:var(--ctp-red); font-weight:600; }
.tt-pulse{ color:var(--ctp-green); transition:opacity .9s ease; }
</style>"""

# Mirrors the header rollup into the tab title: "(N) table-talk" while actions are open.
# Client-side observer, so every tab stays correct with zero extra server traffic.
TAB_TITLE_JS = """<script>
document.addEventListener('DOMContentLoaded', () => {
  const sync = () => {
    const el = document.getElementById('tt-rollup');
    if (!el) return;
    const m = el.textContent.match(/^(\\d+) action/);
    document.title = (m ? `(${m[1]}) ` : '') + 'table-talk';
  };
  const wait = setInterval(() => {
    const el = document.getElementById('tt-rollup');
    if (el) {
      clearInterval(wait);
      new MutationObserver(sync).observe(el, {childList: true, characterData: true, subtree: true});
      sync();
    }
  }, 500);
});
</script>"""

# Toast when the open-action count rises: baseline is read before observing,
# so page loads and reconnects never fire a stale burst.
TOAST_JS = """<script>
document.addEventListener('DOMContentLoaded', () => {
  const show = (msg) => {
    const t = document.createElement('div');
    t.className = 'tt-toast';
    t.textContent = msg;
    document.body.appendChild(t);
    requestAnimationFrame(() => t.classList.add('tt-toast-in'));
    setTimeout(() => { t.classList.remove('tt-toast-in'); setTimeout(() => t.remove(), 400); }, 5000);
  };
  const wait = setInterval(() => {
    const el = document.getElementById('tt-rollup');
    if (!el) return;
    clearInterval(wait);
    const read = () => { const m = el.textContent.match(/^(\\d+)/); return m ? +m[1] : 0; };
    let prev = read();
    new MutationObserver(() => {
      const n = read();
      if (n > prev) {
        const d = n - prev, s = d > 1 ? 's' : '';
        show(`${d} new action item${s} need${s ? '' : 's'} you`);
      }
      prev = n;
    }).observe(el, {childList: true, characterData: true, subtree: true});
  }, 500);
});
</script>"""

# '/' focuses the filter box unless the user is already typing somewhere.
HOTKEY_JS = """<script>
document.addEventListener('keydown', e => {
  if (e.key === '/' && !['INPUT', 'TEXTAREA'].includes(document.activeElement.tagName)) {
    e.preventDefault();
    const el = document.querySelector('.tt-search input');
    if (el) el.focus();
  }
});
</script>"""

THEME_MODES = ("system", "light", "dark")
THEME_ICONS = {"system": "brightness_auto", "light": "light_mode", "dark": "dark_mode"}

COLS = {
    "action": ["id", "background", "why", "rec"],
    "task": ["id", "what", "progress"],
    "term": ["term", "intuitive", "technical"],
    "done": ["id", "type", "summary"],
}


def fold(path):
    """Shallow-merge events by id, in file order. Same contract as bin/table-talk."""
    state = {}
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return state
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            ev = json.loads(line.decode("utf-8"))
            eid = str(ev["id"])
            state[eid] = {**state.get(eid, {}), **ev}
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, KeyError):
            pass
    return state


_fold_cache = {}  # path -> ((st_mtime, st_size), folded_state)


def fold_cached(path):
    """fold(), but re-parse a file only when its mtime/size changed since last poll.
    Steady state collapses the 2s tick to O(files stat) regardless of history size."""
    try:
        st = path.stat()
    except FileNotFoundError:
        return {}
    key = (st.st_mtime, st.st_size)
    hit = _fold_cache.get(path)
    if hit and hit[0] == key:
        return hit[1]
    state = fold(path)
    _fold_cache[path] = (key, state)
    return state


def rows(state, typ, done=None):
    out = [e for e in state.values() if e.get("type") == typ
           and (done is None or (e.get("status") == "done") == done)]
    return sorted(out, key=lambda e: e.get("ts", 0), reverse=True)


def done_rows(state):
    return [{"id": e["id"], "type": e["type"],
             "summary": e.get("background") or e.get("what", "")}
            for e in rows(state, "action", done=True) + rows(state, "task", done=True)]


# Header labels that differ from a bare capitalize — match the reply-table headers in SKILL.md.
COL_LABELS = {"why": "Why it matters", "rec": "Recommendation"}


def columns(typ):
    return [{"name": c, "label": COL_LABELS.get(c, c.capitalize()), "field": c,
             "align": "left", "sortable": True} for c in COLS[typ]]


def card_data(state):
    """Everything one card renders; pure and comparable, so ticks can no-op on unchanged data."""
    return {"action": rows(state, "action", done=False),
            "task": rows(state, "task", done=False),
            "term": rows(state, "term"),
            "done": done_rows(state)}


def selftest():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "x.jsonl"
        p.write_text(
            '{"id":"a1b2","type":"action","status":"open","background":"bg","why":"w","rec":"r","ts":1}\n'
            '{"id":"a1b2","status":"done","ts":2}\n'
            'garbage\n'
            '{"id":"c3d4","type":"task","status":"open","what":"train","ts":3}\n'
            '{"id":"c3d4","progress":"epoch 3","ts":4}\n'
            '{"id":"e5f6","type":"term","term":"FBA","intuitive":"i","technical":"t","ts":5}\n')
        s = fold(p)
        assert s["a1b2"]["why"] == "w" and s["a1b2"]["status"] == "done", "merge preserves fields"
        assert rows(s, "action", done=False) == []
        assert rows(s, "task", done=False)[0]["progress"] == "epoch 3"
        assert [r["term"] for r in rows(s, "term")] == ["FBA"], "terms are cumulative"
        assert done_rows(s) == [{"id": "a1b2", "type": "action", "summary": "bg"}]
        assert [c["name"] for c in columns("action")] == ["id", "background", "why", "rec"]
        assert [c["label"] for c in columns("action")] == ["Id", "Background", "Why it matters", "Recommendation"]
        assert card_data(s) == card_data(fold(p)), "card_data must be stable for change-gating"
        with open(p, "a") as fh:
            fh.write('{"id":"c3d4","progress":"epoch 4","ts":6}\n')
        after = card_data(fold(p))
        assert after != card_data(s) and after["task"][0]["progress"] == "epoch 4", \
            "card_data must change when an event lands"
        with open(p, "ab") as fh:
            fh.write(b"\xff\xfe bad utf8\n")
        assert fold(p)["a1b2"]["why"] == "w", "invalid utf-8 line must not break fold"
        assert fold(Path(td) / "missing.jsonl") == {}, "missing file folds to empty"
        # fold_cached: same result as fold, and re-reads when the file changes
        assert fold_cached(p) == fold(p), "cached fold matches uncached"
        before = fold_cached(p)
        with open(p, "a") as fh:
            fh.write('{"id":"z9z9","type":"term","term":"Z","intuitive":"i","technical":"t","ts":9}\n')
        assert "z9z9" in fold_cached(p) and "z9z9" not in before, "cache invalidates on file change"
        assert fold_cached(Path(td) / "missing.jsonl") == {}, "cached fold tolerates missing file"
    assert "--ctp-base:#eff1f5" in THEME_CSS and "--ctp-base:#1e1e2e" in THEME_CSS, \
        "both Latte and Mocha palettes must be defined"
    assert "body.body--dark" in THEME_CSS, "dark palette must key off Quasar's body--dark"
    assert set(THEME_ICONS) == set(THEME_MODES)
    assert "tt-rollup" in TAB_TITLE_JS and "document.title" in TAB_TITLE_JS
    assert section_label("action", 3) == "🔴 Actions needed · 3"
    assert set(SECTION_TEXT) == {"action", "task", "term"}
    assert accent({"action": [1], "task": []}) == "red"
    assert accent({"action": [], "task": [1]}) == "blue"
    assert accent({"action": [], "task": []}) == "green"
    assert "font-variant-numeric:tabular-nums" in THEME_CSS and "max-width:1100px" in THEME_CSS
    assert ago(0, now=30) == "just now" and ago(0, now=90) == "1m ago"
    assert ago(0, now=7200) == "2h ago" and ago(0, now=200000) == "2d ago"
    assert latest_ts({"a": {"ts": 5}, "b": {"ts": 9}, "c": {}}) == 9 and latest_ts({}) == 0
    assert "tt-search" in HOTKEY_JS and "preventDefault" in HOTKEY_JS
    assert "tt-toast" in TOAST_JS and "tt-toast" in THEME_CSS, "toast script and styles must pair"
    print("ok")


SECTIONS = (("🔴 Actions needed", "action", "id"),
            ("🔵 Background work", "task", "id"),
            ("📖 Glossary (cumulative)", "term", "term"))
SECTION_TEXT = {key: label for label, key, _ in SECTIONS}


def section_label(key, count):
    return f"{SECTION_TEXT[key]} · {count}"


def accent(data):
    """Card state color: needs the user > working > settled."""
    return "red" if data["action"] else ("blue" if data["task"] else "green")


def latest_ts(state):
    return max((e.get("ts", 0) for e in state.values()), default=0)


def ago(ts, now=None):
    d = (now if now is not None else time.time()) - ts
    if d < 60:
        return "just now"
    if d < 3600:
        return f"{int(d // 60)}m ago"
    if d < 86400:
        return f"{int(d // 3600)}h ago"
    return f"{int(d // 86400)}d ago"


def stamp(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def main(port):
    from nicegui import app, ui

    ui.add_head_html(THEME_CSS)
    ui.add_head_html(TAB_TITLE_JS)
    ui.add_head_html(HOTKEY_JS)
    ui.add_head_html(TOAST_JS)
    dark = ui.dark_mode()
    mode = app.storage.general.get("theme", "system")
    if mode not in THEME_MODES:  # a hand-edited/stale storage value must not crash startup
        mode = "system"

    def apply_theme(m):
        {"system": dark.auto, "light": dark.disable, "dark": dark.enable}[m]()

    apply_theme(mode)

    with ui.header().classes("tt-header items-center"):
        ui.label("table-talk").classes("text-lg font-bold tt-title")
        ui.space()
        search = ui.input(placeholder="filter — press /").props(
            "clearable dense borderless debounce=200").classes("w-56 tt-search")
        pulse = ui.icon("circle", size="10px").classes("tt-pulse").style("opacity:0.35")
        rollup = ui.label("").props("id=tt-rollup aria-live=polite").classes("text-sm tt-rollup")

        def cycle():
            nonlocal mode
            mode = THEME_MODES[(THEME_MODES.index(mode) + 1) % len(THEME_MODES)]
            app.storage.general["theme"] = mode
            apply_theme(mode)
            btn.props(f'icon={THEME_ICONS[mode]}')

        btn = ui.button(on_click=cycle).props(f'flat round icon={THEME_ICONS[mode]}')
        with btn:
            ui.tooltip("theme: system → light → dark")

    cards = {}    # path -> {"tables": {key: ui.table}, "expansion": ui.expansion, "data": card_data}
    built = None  # file list the DOM was last built for; None = never built

    def build_card(path, data, latest):
        with ui.card().classes("w-full").style(
                f"border-left:4px solid var(--ctp-{accent(data)})") as card_el:
            with ui.row().classes("items-baseline gap-2 w-full"):
                ui.label(path.stem).classes("text-base font-bold tt-session")
                meta = ui.label(ago(latest) if latest else "").classes("text-xs tt-age")
                with meta:
                    tip = ui.tooltip(stamp(latest) if latest else "")
            tables = {}
            labels = {}
            for _, key, row_key in SECTIONS:
                labels[key] = ui.label(section_label(key, len(data[key]))).classes("tt-sec")
                tables[key] = ui.table(columns=columns(key), rows=data[key],
                                       row_key=row_key).props("dense flat").classes("w-full tt")
                search.bind_value_to(tables[key], "filter")
                if key == "action":
                    with ui.row().classes("items-center gap-1 tt-clear") as allclear:
                        ui.icon("check_circle", size="18px")
                        ui.label("no open actions")
                    tables[key].set_visibility(bool(data[key]))
                    allclear.set_visibility(not data[key])
            exp = ui.expansion(f"{len(data['done'])} done").classes("w-full opacity-50")
            with exp:
                tables["done"] = ui.table(columns=columns("done"), rows=data["done"],
                                          row_key="id").props("dense flat").classes("w-full tt")
                search.bind_value_to(tables["done"], "filter")
            exp.set_visibility(bool(data["done"]))
        return {"el": card_el, "tables": tables, "labels": labels, "expansion": exp,
                "allclear": allclear, "data": data, "meta": meta, "tip": tip,
                "latest_ts": latest, "age_txt": None}

    container = ui.column().classes("w-full")

    flip = False

    def tick():
        nonlocal built, flip
        files = sorted(DATA_DIR.glob("*.jsonl"), reverse=True)
        if files != built:  # session file appeared/disappeared: rebuild structure (only case that resets scroll)
            container.clear()
            cards.clear()
            with container:
                if not files:
                    with ui.column().classes("items-center w-full m-8 opacity-60"):
                        ui.icon("inbox", size="3rem")
                        ui.label("no sessions yet").classes("text-lg")
                        ui.label("record something: table-talk action \"…\" --why \"…\" --rec \"…\"") \
                            .classes("text-sm tt-session")
                for p in files:
                    state = fold_cached(p)
                    cards[p] = build_card(p, card_data(state), latest_ts(state))
            built = files
        else:
            for p, card in cards.items():  # data-only change: mutate rows in place so scroll and sort survive
                state = fold_cached(p)
                data = card_data(state)
                if data == card["data"]:
                    continue
                for key, table in card["tables"].items():
                    if data[key] != card["data"][key]:
                        table.rows[:] = data[key]
                        table.update()
                        if key in card["labels"]:
                            card["labels"][key].set_text(section_label(key, len(data[key])))
                card["expansion"].set_text(f"{len(data['done'])} done")
                card["expansion"].set_visibility(bool(data["done"]))
                card["el"].style(f"border-left:4px solid var(--ctp-{accent(data)})")
                card["tables"]["action"].set_visibility(bool(data["action"]))
                card["allclear"].set_visibility(not data["action"])
                if (lt := latest_ts(state)) != card["latest_ts"]:
                    card["latest_ts"] = lt
                    card["tip"].set_text(stamp(lt))
                card["data"] = data
        for card in cards.values():  # age advances even when data does not; re-render only on text change
            txt = ago(card["latest_ts"]) if card["latest_ts"] else ""
            if card["age_txt"] != txt:
                card["age_txt"] = txt
                card["meta"].set_text(txt)
        n = sum(len(c["data"]["action"]) for c in cards.values())
        txt = (f"{n} action{'s' if n != 1 else ''} needed across "
               f"{len(cards)} session{'s' if len(cards) != 1 else ''}") if n else "all clear"
        if rollup.text != txt:
            rollup.set_text(txt)
            rollup.classes(replace="text-sm tt-rollup" + (" tt-rollup-hot" if n else ""))
        flip = not flip  # heartbeat: proves the poll is alive without re-rendering anything
        pulse.style(f"opacity:{0.9 if flip else 0.35}")

    tick()
    # fold_cached re-parses only changed files; steady-state tick is O(files stat)
    ui.timer(2.0, tick)
    ui.run(host="127.0.0.1", port=port, show=False, reload=False, title="table-talk", favicon="🗣")


if __name__ in {"__main__", "__mp_main__"}:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8731)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        selftest()
    else:
        main(a.port)
