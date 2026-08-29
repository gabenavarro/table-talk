#!/usr/bin/env python3
"""table-talk configuration: one TOML file, stdlib only, and a missing or broken
file must never stop the dashboard from starting."""
import argparse
import json
import os
import tomllib
from pathlib import Path

CONFIG_PATH = Path(os.environ.get("TABLE_TALK_CONFIG")
                   or Path.home() / ".config/table-talk/config.toml")

import re
import sys

_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})\Z")

# The configurable tokens, values copied from bin/tt.css and pinned equal to it by
# selftest(). --hover is deliberately excluded: it is a consequence of --surface,
# not an independent choice (see the note at the top of tt.css).
_DARK = {"bg": "#1d2021", "surface": "#282828", "surface-2": "#32302f",
         "ink": "#ebdbb2", "ink-2": "#a89984", "ink-3": "#928374",
         "sel": "#665c54", "caret": "#8ec07c",
         "act": "#fb4934", "act-2": "#cc241d", "job": "#83a598", "job-2": "#458588",
         "gls": "#fabd2f", "gls-2": "#d79921", "ok": "#b8bb26", "ok-2": "#98971a",
         "mag": "#d3869b"}
_LIGHT = {"bg": "#e8e6dc", "surface": "#faf9f5", "surface-2": "#f2f0e8",
          "ink": "#141413", "ink-2": "#6f6e68", "ink-3": "#727169",
          "sel": "#e8e6dc", "caret": "#c4512c",
          "act": "#a53a2e", "act-2": "#d97757", "job": "#3668a0", "job-2": "#6a9bcc",
          "gls": "#8a6a10", "gls-2": "#b88a28", "ok": "#4a7038", "ok-2": "#788c5d",
          "mag": "#7a4a82"}

DEFAULTS = {
    "server": {"port": 8731, "poll_seconds": 2.0},
    "ui": {"view": "merged", "columns": 0, "drawer_open": True,
           "filter_debounce_ms": 100,
           "collapsed_sections": ["glossary", "done"]},
    # xdg-open does not exist on macOS, and both launchers swallow a missing
    # command into stderr the user is not watching - deliberately, and pinned -
    # so the wrong default there presents as links that are simply decorative.
    "links": {"open_command": "open" if sys.platform == "darwin" else "xdg-open",
              "extra_roots": []},
    # dark_theme / light_theme name a palette from bin/themes.json; "" keeps the
    # built-in one. The token tables below still win, so a user can take a theme
    # and change one colour without restating the other sixteen.
    "theme": {"default": "system", "dark_theme": "", "light_theme": "",
              "dark": _DARK, "light": _LIGHT},
}


def valid_colour(s):
    """Only a hex colour. A config file is a second route into the stylesheet,
    so it gets the same treatment as any other untrusted input."""
    return isinstance(s, str) and bool(_HEX.match(s))


# A value can be the RIGHT TYPE and still break the dashboard: poll_seconds=0
# turns the 2s timer into an unthrottled loop re-globbing the data dir every
# tick, port outside 1-65535 either dies at bind with a raw uvicorn traceback
# (>65535, <0) or silently starts on a random ephemeral port that breaks the
# documented URL (0), and columns outside 0-3 has no packing defined for it
# (0 itself is valid: it means "auto", see cols_for). Checked in _merge next
# to the colour check, not clamped to the boundary - a fallback to the same
# default the type check already uses needs no new machinery.
_RANGES = {
    "server.poll_seconds": (0.2, float("inf")),
    "server.port": (1, 65535),
    "ui.columns": (0, 3),
    "ui.filter_debounce_ms": (0, float("inf")),
}

# Keys whose value must be one OF a set rather than within a range. Same
# treatment as _RANGES: a typo falls back to the default with a warning instead
# of reaching the dashboard, where an unknown view or theme mode is a silent
# fallback the user never learns about.
_CHOICES = {
    "ui.view": ("merged", "flat"),
    "theme.default": ("system", "light", "dark"),
}


def _merge(default, override, path=""):
    """Deep-merge override onto a COPY of default. A value of the wrong type or
    out of range, or an unknown key, is dropped with a warning rather than
    propagated."""
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
        elif (isinstance(ov, bool) and not isinstance(dv, bool)) or (
                not isinstance(ov, type(dv)) and not (isinstance(dv, float) and isinstance(ov, int))):
            print(f"warning: config {where}: expected {type(dv).__name__}, using default",
                  file=sys.stderr)
            out[key] = dv
        elif where in _CHOICES and ov not in _CHOICES[where]:
            print(f"warning: config {where}: {ov!r} is not one of "
                  f"{', '.join(_CHOICES[where])}, using default", file=sys.stderr)
            out[key] = dv
        elif where in _RANGES and not (_RANGES[where][0] <= ov <= _RANGES[where][1]):
            lo, hi = _RANGES[where]
            print(f"warning: config {where}: {ov!r} is out of range ({lo}-{hi}), using default",
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
    cfg = _merge(DEFAULTS, raw)
    # A named theme replaces the BASE palette, then the file's own tokens are
    # re-applied on top - so naming a theme and overriding one colour does what
    # it looks like. An unknown name warns and keeps the built-in palette, the
    # way every other unrecognised value here does, rather than reaching the
    # dashboard as a silent fallback nobody learns about.
    bundle = themes()
    for mode in ("dark", "light"):
        name = cfg["theme"].get(f"{mode}_theme")
        if not name:
            continue
        if name not in bundle:
            print(f"warning: config theme.{mode}_theme: {name!r} is not a bundled "
                  f"theme, using the built-in palette", file=sys.stderr)
            cfg["theme"][f"{mode}_theme"] = ""
            continue
        picked = dict(bundle[name]["tokens"])
        picked.update({k: v for k, v in (raw.get("theme", {}).get(mode) or {}).items()
                       if k in picked and valid_colour(v)})
        cfg["theme"][mode] = picked
    return cfg


THEMES_PATH = Path(__file__).with_name("themes.json")


def themes():
    """The bundled palettes: {name: {"tokens": {...}, "adapted": [...], "dark": bool}}.

    Generated by tools/build-themes.py from Ghostty theme files; see
    THEMES-LICENCE for the MIT notice the sources carry. A missing or malformed
    file yields nothing at all rather than an exception - the same contract the
    config itself has, because a broken bundle must not stop the dashboard.
    """
    try:
        return json.loads(THEMES_PATH.read_text())["themes"]
    except (OSError, ValueError, KeyError):
        return {}


def selftest():
    import tempfile
    # The defaults are a SECOND copy of the stylesheet's values; if they drift, a
    # user with no config file silently gets the old colour back.
    css = Path(__file__).with_name("tt.css").read_text()
    for block, tokens in ((":root{", _LIGHT), ("body.body--dark{", _DARK)):
        decls = css.split(block, 1)[1].split("}", 1)[0]
        for name, value in tokens.items():
            assert f"--{name}:{value};" in decls, \
                f"tt.css {block[:-1]} --{name} does not match tt_config's {value}"
    assert DEFAULTS["links"]["open_command"] == ("open" if sys.platform == "darwin"
                                                 else "xdg-open"), \
        "the opener is per platform: xdg-open is absent on macOS, and a missing " \
        "command is swallowed into stderr nobody is watching, so the links just " \
        "look decorative there"
    # The bundled themes are DATA, so the thing worth pinning is that every one
    # of them is still readable. Terminal accents are picked for large glyphs on
    # their own ground; here they are 9-12px UI text, and a naive port of the
    # light ones ran as low as 1.7:1.
    def _lum(h):
        h = h.lstrip("#")
        c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]

    def _ratio(a, b):
        la, lb = _lum(a), _lum(b)
        return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)

    _floors = {"ink": 4.5, "ink-2": 4.5, "ink-3": 3.5, "act": 3.0, "job": 3.0,
               "ok": 3.0, "gls": 3.0, "mag": 3.0, "caret": 3.0}
    _bundled = themes()
    assert len(_bundled) >= 10, "the bundle is missing; ship themes.json beside tt_config"
    for _name, _t in _bundled.items():
        assert set(_t["tokens"]) == set(DEFAULTS["theme"]["dark"]), \
            f"{_name} does not define exactly the tokens the stylesheet reads"
        for _tok, _hex in _t["tokens"].items():
            assert valid_colour(_hex), f"{_name}.{_tok} = {_hex!r} is not a colour"
        for _tok, _floor in _floors.items():
            assert _ratio(_t["tokens"][_tok], _t["tokens"]["surface"]) >= _floor, \
                f"{_name}: {_tok} is unreadable on its own surface - a theme " \
                "nobody can read is not a theme"
        assert isinstance(_t["adapted"], list) and isinstance(_t["dark"], bool)
        assert all(a in _t["tokens"] for a in _t["adapted"]), \
            f"{_name} claims to have adapted a token it does not define"
    assert not _bundled["Gruvbox Dark Hard"]["adapted"], \
        "Gruvbox Dark Hard is the palette this app already shipped; if it needs " \
        "adapting, the mapping has drifted from what the stylesheet does"
    assert _bundled["Gruvbox Dark Hard"]["tokens"]["act"] == DEFAULTS["theme"]["dark"]["act"], \
        "and it must still reproduce that palette exactly"

    assert valid_colour("#fb4934") and valid_colour("#FFF") and valid_colour("#1d2021ff")
    assert not valid_colour("red"), "only hex is accepted"
    assert not valid_colour("#12"), "too short"
    assert not valid_colour("#12345"), "not a legal hex length"
    assert not valid_colour('#fff;}body{display:none'), "a css injection attempt is not a colour"
    assert not valid_colour(None) and not valid_colour(123)
    assert not valid_colour("#fff\n"), "Python's $ matches before a trailing newline; \\Z must not"

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

        boolean = Path(td) / "bool.toml"
        boolean.write_text('[server]\nport = true\n')
        assert load(boolean)["server"]["port"] == DEFAULTS["server"]["port"], \
            "a bool must not silently satisfy an int/float default"

        extra = Path(td) / "extra.toml"
        extra.write_text('[nonsense]\nkey = 1\n')
        assert "nonsense" not in load(extra), "unknown sections are ignored, not merged"

        # Naming a bundled theme replaces the base palette; the file's own
        # tokens still win, so one colour can be changed without restating
        # the other sixteen.
        pick = Path(td) / "pick.toml"
        pick.write_text('[theme]\ndark_theme = "Dracula"\n')
        got = load(pick)["theme"]["dark"]
        assert got == _bundled["Dracula"]["tokens"], "a named theme replaces the base"
        assert got != DEFAULTS["theme"]["dark"], "and it is really different from built-in"
        pick.write_text('[theme]\ndark_theme = "Dracula"\n\n[theme.dark]\ncaret = "#ff00ff"\n')
        got = load(pick)["theme"]["dark"]
        assert got["caret"] == "#ff00ff", "an explicit token still wins over the theme"
        assert got["act"] == _bundled["Dracula"]["tokens"]["act"], \
            "and overriding one colour must not discard the other sixteen"
        pick.write_text('[theme]\ndark_theme = "Nonesuch"\n')
        assert load(pick)["theme"]["dark"] == DEFAULTS["theme"]["dark"], \
            "an unknown theme name warns and keeps the built-in palette, rather " \
            "than reaching the dashboard as a silent fallback"
        pick.write_text('[theme]\nlight_theme = "Catppuccin Latte"\n')
        both = load(pick)["theme"]
        assert both["light"] == _bundled["Catppuccin Latte"]["tokens"]
        assert both["dark"] == DEFAULTS["theme"]["dark"], \
            "naming one mode's theme must not disturb the other"
        pick.write_text('[theme]\ndark_theme = "Dracula"\n\n[theme.dark]\ncaret = "red"\n')
        assert load(pick)["theme"]["dark"]["caret"] == _bundled["Dracula"]["tokens"]["caret"], \
            "a non-hex override is dropped here too; this is a second route " \
            "into the stylesheet and gets the same gate"

        # theme.default is a MODE, not a colour - the colour check must not eat it
        # a value that is not one of the allowed ones falls back and WARNS,
        # rather than reaching the dashboard as a silent fallback nobody sees
        choice = Path(td) / "choice.toml"
        choice.write_text('[ui]\nview = "mrged"\n')
        assert load(choice)["ui"]["view"] == "merged", \
            "a typo'd view must not reach the wall; the default is merged"
        choice.write_text('[ui]\nview = "flat"\n')
        assert load(choice)["ui"]["view"] == "flat", "a legal choice still applies"
        choice.write_text('[theme]\ndefault = "nonsense"\n')
        assert load(choice)["theme"]["default"] == "system", \
            "the same check covers the theme mode, which had none"

        mode = Path(td) / "mode.toml"
        mode.write_text('[theme]\ndefault = "dark"\n')
        assert load(mode)["theme"]["default"] == "dark", \
            "theme.default is a mode name and must survive the colour validator"

        zero_poll = Path(td) / "zero_poll.toml"
        zero_poll.write_text('[server]\npoll_seconds = 0\n')
        assert load(zero_poll)["server"]["poll_seconds"] == DEFAULTS["server"]["poll_seconds"], \
            "a poll_seconds of 0 pegs a core: the timer stops throttling and " \
            "re-globs the whole data dir every event-loop tick"

        neg_poll = Path(td) / "neg_poll.toml"
        neg_poll.write_text('[server]\npoll_seconds = -5\n')
        assert load(neg_poll)["server"]["poll_seconds"] == DEFAULTS["server"]["poll_seconds"], \
            "a negative poll_seconds pegs a core the same as zero does"

        big_port = Path(td) / "big_port.toml"
        big_port.write_text('[server]\nport = 99999\n')
        assert load(big_port)["server"]["port"] == DEFAULTS["server"]["port"], \
            "a port above 65535 dies at bind() with a raw uvicorn OverflowError " \
            "instead of starting the dashboard"

        neg_port = Path(td) / "neg_port.toml"
        neg_port.write_text('[server]\nport = -1\n')
        assert load(neg_port)["server"]["port"] == DEFAULTS["server"]["port"], \
            "a negative port dies at bind() the same as one above 65535"

        zero_port = Path(td) / "zero_port.toml"
        zero_port.write_text('[server]\nport = 0\n')
        assert load(zero_port)["server"]["port"] == DEFAULTS["server"]["port"], \
            "port 0 starts on a random ephemeral port: the documented URL and " \
            "the liveness check serve_refusal hands every session both go stale"

        edge_port = Path(td) / "edge_port.toml"
        edge_port.write_text('[server]\nport = 65535\n')
        assert load(edge_port)["server"]["port"] == 65535, \
            "65535 is the highest legal port and must not be rejected as out of range"

        big_cols = Path(td) / "big_cols.toml"
        big_cols.write_text('[ui]\ncolumns = 99\n')
        assert load(big_cols)["ui"]["columns"] == DEFAULTS["ui"]["columns"], \
            "cols_for() has no packing defined past 3 columns"

        zero_cols = Path(td) / "zero_cols.toml"
        zero_cols.write_text('[ui]\ncolumns = 0\n')
        assert load(zero_cols)["ui"]["columns"] == 0, \
            "0 means auto-pack (see cols_for) and must not be rejected as out of range"

        neg_debounce = Path(td) / "neg_debounce.toml"
        neg_debounce.write_text('[ui]\nfilter_debounce_ms = -5\n')
        assert load(neg_debounce)["ui"]["filter_debounce_ms"] == DEFAULTS["ui"]["filter_debounce_ms"], \
            "a negative debounce is meaningless as a JS setTimeout delay and " \
            "must not reach the search box's debounce prop"
    print("ok")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.parse_args()
    selftest()
