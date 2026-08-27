#!/usr/bin/env python3
"""table-talk configuration: one TOML file, stdlib only, and a missing or broken
file must never stop the dashboard from starting."""
import argparse
import os
import tomllib
from pathlib import Path

CONFIG_PATH = Path(os.environ.get("TABLE_TALK_CONFIG")
                   or Path.home() / ".config/table-talk/config.toml")

import re
import sys

_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})\Z")

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
        elif (isinstance(ov, bool) and not isinstance(dv, bool)) or (
                not isinstance(ov, type(dv)) and not (isinstance(dv, float) and isinstance(ov, int))):
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


def selftest():
    import tempfile
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
