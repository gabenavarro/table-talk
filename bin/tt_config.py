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
    # host is a CHOICE, not free text: the dashboard has no authentication and
    # renders work logs, project names and file paths, so the only two values
    # worth offering are "keep it on this machine" and "put it on the network".
    "server": {"host": "127.0.0.1", "port": 8731, "poll_seconds": 2.0},
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
    "server.host": ("127.0.0.1", "0.0.0.0"),
    "ui.view": ("merged", "flat"),
    "theme.default": ("system", "light", "dark"),
}


def _merge(default, override, path=""):
    """Deep-merge override onto a COPY of default. A value of the wrong type or
    out of range, or an unknown key, is dropped with a warning rather than
    propagated."""
    out = {}
    if isinstance(override, dict):
        for key in override.keys() - default.keys():
            print(f"warning: config {path}{key}: unknown key, ignored", file=sys.stderr)
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


def form_fields():
    """What a settings form may offer, derived from the validators themselves.

    Never a second hardcoded list: a form that disagrees with the validator is
    worse than no form, because it puts values into the file that then fall
    back to defaults with a warning nobody sees. Colour tokens are deliberately
    absent - seventeen of them across two modes is a colour-picker project, and
    the file already does that well.
    """
    bundle = themes()
    dark = [n for n, v in bundle.items() if v["dark"]]
    light = [n for n, v in bundle.items() if not v["dark"]]
    out = []
    for key in ("ui.view", "theme.default", "server.host"):
        out.append((key, "choice", list(_CHOICES[key])))
    out.append(("theme.dark_theme", "choice", [""] + sorted(dark)))
    out.append(("theme.light_theme", "choice", [""] + sorted(light)))
    for key in ("server.port", "ui.columns", "server.poll_seconds",
                "ui.filter_debounce_ms"):
        out.append((key, "number", _RANGES[key]))
    return out


def _render(value):
    """A TOML scalar. Managed values are enums and numbers, nothing exotic."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


_LINE = re.compile(r"^(\s*)([A-Za-z0-9_-]+)(\s*=\s*)(.*?)(\s*#.*)?$")


def set_keys(path, updates):
    """Set `{"section.key": value}` in a TOML file, keeping everything else.

    LINE SURGERY, not a dump. Re-serialising the parsed config would flatten a
    heavily commented file into bare values and silently materialise every
    default the user never chose - the file is documentation as much as data.
    Only the one line per key is touched; its trailing comment is kept.

    The safety net is that the result is PARSED and the value read back before
    it replaces anything. Text editing that produced a file which does not load,
    or does not mean what was asked, is discarded rather than saved.

    Returns (ok, message).
    """
    path = Path(path)
    try:
        lines = path.read_text().splitlines() if path.exists() else []
    except OSError as e:
        return False, f"cannot read {path}: {e}"
    for dotted, value in updates.items():
        section, _, key = dotted.rpartition(".")
        head = [i for i, l in enumerate(lines) if l.strip() == f"[{section}]"]
        if not head:
            lines += ["", f"[{section}]", f"{key} = {_render(value)}"]
            continue
        start = head[0] + 1
        end = next((i for i in range(start, len(lines))
                    if lines[i].lstrip().startswith("[")), len(lines))
        for i in range(start, end):
            m = _LINE.match(lines[i])
            if m and m.group(2) == key:
                lines[i] = (f"{m.group(1)}{key}{m.group(3)}{_render(value)}"
                            f"{m.group(5) or ''}")
                break
        else:
            # After the section's last NON-BLANK line, not at its boundary:
            # inserting at `end` puts the key past the blank separator and
            # jams it against the next [header], where it reads as though it
            # belonged to that section even though TOML says otherwise.
            while end > start and not lines[end - 1].strip():
                end -= 1
            lines.insert(end, f"{key} = {_render(value)}")
    text = "\n".join(lines) + "\n"
    try:
        got = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        return False, f"refusing to save: the result would not parse ({e})"
    for dotted, value in updates.items():
        section, _, key = dotted.rpartition(".")
        if got.get(section, {}).get(key) != value:
            return False, f"refusing to save: {dotted} did not round-trip"
    try:
        tmp = path.with_suffix(".toml.tt-tmp")
        tmp.write_text(text)
        os.replace(tmp, path)       # never a half-written config
    except OSError as e:
        return False, f"cannot write {path}: {e}"
    return True, "saved"


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
    import contextlib
    import io
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

        typo = Path(td) / "typo.toml"
        typo.write_text('[server]\npol_seconds = 5\n')
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            got = load(typo)
        assert got["server"]["poll_seconds"] == DEFAULTS["server"]["poll_seconds"], \
            "a typo'd key must not silently take effect - it is not a known key"
        assert "pol_seconds" in buf.getvalue() and "unknown key" in buf.getvalue(), \
            "and it must warn: every OTHER way to get a value wrong in this " \
            "file warns, and a typo that is dropped with no signal at all is " \
            "the one mistake the user never learns they made"

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
        # server.host: the default must keep the dashboard OFF the network, and
        # a typo must not silently bind it there either.
        assert DEFAULTS["server"]["host"] == "127.0.0.1", \
            "the dashboard has no password, so reaching the network must be a " \
            "deliberate edit and never what an untouched install does"
        # set_keys: the file is DOCUMENTATION as much as data. A dump of the
        # parsed config would flatten it and materialise every default.
        w = Path(td) / "w.toml"
        w.write_text('# mine\n\n[server]\nport = 8731   # the port\n'
                     '# keep me\npoll_seconds = 2.0\n\n[ui]\nview = "merged"\n')
        ok, msg = set_keys(w, {"server.port": 9000, "server.host": "0.0.0.0"})
        got = w.read_text()
        assert ok, msg
        assert "# the port" in got and "# keep me" in got and "# mine" in got, \
            "every comment must survive: this file explains itself, and a " \
            "settings form that silently strips the explanations makes the " \
            "config worse than it was before the form existed"
        assert "poll_seconds = 2.0" in got, "untouched keys keep their value"
        assert load(w)["server"]["port"] == 9000 and \
               load(w)["server"]["host"] == "0.0.0.0", "and the write took"
        lines = got.splitlines()
        assert lines[lines.index('host = "0.0.0.0"') - 1].startswith("poll_seconds"), \
            "a NEW key goes after the section's last real line, not past the " \
            "blank separator where it reads as part of the next section"
        w.write_text("[ui]\nview = \"merged\"\n")
        ok, _ = set_keys(w, {"theme.default": "dark"})
        assert ok and load(w)["theme"]["default"] == "dark" and \
               load(w)["ui"]["view"] == "merged", \
            "a key whose section is absent appends the section, and must not " \
            "disturb the sections already there"
        w.write_text('[server]\nnote = """\nport = 1\n"""\n')
        ok, msg = set_keys(w, {"server.port": 9000})
        assert not ok and "round-trip" in msg, \
            "line surgery can match inside a MULTI-LINE STRING: the result " \
            "still parses but means something else entirely, so the value is " \
            "read back before anything is replaced"
        assert w.read_text() == '[server]\nnote = """\nport = 1\n"""\n', \
            "and the file is left exactly as it was"
        w.write_text("[server]\nport = 8731\n")
        ok, msg = set_keys(w, {"server.host": 'x"\nport = 1'})
        assert w.read_text() == "[server]\nport = 8731\n", \
            "a value that would break the file must leave it EXACTLY as it " \
            "was: the config is read on every start, and a corrupt one is a " \
            "dashboard that will not come up"
        assert not list(Path(td).glob("*tt-tmp")), \
            "and no half-written temp file may survive a refusal"
        # form_fields is derived, never a second list that can disagree.
        keys = {k for k, _, _ in form_fields()}
        assert keys >= set(_CHOICES) and "server.port" in keys, \
            "the form must offer what the VALIDATOR knows: a form built from " \
            "its own hardcoded list drifts, and then writes values that fall " \
            "back to defaults with a warning the user never sees"
        assert all(o in dict((k, v) for k, _, v in form_fields())["ui.view"]
                   for o in _CHOICES["ui.view"]), "choices come from _CHOICES"
        kinds = {k: (kind, v) for k, kind, v in form_fields()}
        assert kinds["server.port"] == ("number", _RANGES["server.port"]) and \
               kinds["ui.columns"] == ("number", _RANGES["ui.columns"]), \
            "a number field must carry the VALIDATOR's own bounds: a form that " \
            "invents its own lets a value through that load() then rejects, " \
            "so the setting silently does not take"
        names = dict((k, v) for k, _, v in form_fields())["theme.dark_theme"]
        assert "" in names and len(names) > 1 and "Dracula" in names, \
            "theme names come from the bundle, with \"\" for the built-in one"
        fields = dict((k, v) for k, _, v in form_fields())
        assert "Ayu Light" not in fields["theme.dark_theme"], \
            "dark_theme must filter to actually-dark themes, not offer every " \
            "bundled name under both dropdowns"
        assert set(fields["theme.dark_theme"]) != set(fields["theme.light_theme"]), \
            "the two dropdowns must not be identical: themes.json's polarity " \
            "field is 'dark', not 'mode'"

        pick.write_text('[server]\nhost = "0.0.0.0"\n')
        assert load(pick)["server"]["host"] == "0.0.0.0", \
            "and asking for it must actually work, or the option is a lie"
        pick.write_text('[server]\nhost = "0.0.0.0 "\n')
        assert load(pick)["server"]["host"] == "127.0.0.1", \
            "anything that is not one of the two known values falls back to " \
            "localhost with a warning: a typo must never widen exposure"
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
