#!/usr/bin/env python3
"""Turn Ghostty theme files into bin/themes.json.

Build-time only, stdlib only, no network. Fetch the sources first:

    gh api repos/mbadolato/iTerm2-Color-Schemes/contents/ghostty/<NAME> \
       --jq .content | base64 -d > themes-src/<NAME>

then run: python3 tools/build-themes.py themes-src bin/themes.json

WHY A CONVERTER AND NOT HAND-PICKED HEX: 14 of this app's 17 tokens are exactly
a terminal theme's ANSI palette - Gruvbox Dark Hard reproduces the shipped dark
palette to the byte, selection colour included. The other three are derived,
because a terminal has one background where this app has three depths.
"""
import json
import re
import sys
from pathlib import Path

# Every pair the stylesheet documents a ratio for, at floors derived from what
# the two shipped palettes already achieve. A bundled theme may not be worse
# than what ships.
FLOORS = {"ink": 4.5, "ink-2": 4.5, "ink-3": 3.5, "act": 3.0, "job": 3.0,
          "ok": 3.0, "gls": 3.0, "mag": 3.0, "caret": 3.0}


def lum(h):
    h = h.lstrip("#")
    c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def ratio(a, b):
    """UNROUNDED. Rounding to 2dp here let a theme at 2.996 pass a floor of 3.0
    and then fail the stricter check in tt_config's selftest - the generator
    must not be more forgiving than the thing that validates its output."""
    la, lb = lum(a), lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def mix(a, b, t):
    a, b = a.lstrip("#"), b.lstrip("#")
    return "#" + "".join(f"{round(int(a[i:i+2],16)*(1-t)+int(b[i:i+2],16)*t):02x}"
                         for i in (0, 2, 4))


def dim_to(fg, bg, target):
    """fg faded toward bg only as far as `target` contrast allows.

    A fixed blend works on one polarity and fails on the other; the token means
    "dimmer ink that still reads", so it is derived to land on its ratio.
    """
    best = fg
    for i in range(101):
        c = mix(fg, bg, i / 100)
        if ratio(c, bg) < target + 0.01:
            break
        best = c
    return best


def comply(colour, surface, ink, floor):
    """`colour` moved toward `ink` only as far as `floor` requires.

    Terminal accents are chosen for large glyphs on their own ground; this app
    uses them for 9-12px UI text. Yellow on a light background is ~1.8:1, which
    is fine in a terminal and unreadable here. Moving toward the ink darkens on
    a light theme and lightens on a dark one, automatically, because the ink is
    whatever contrasts with the surface.
    """
    if ratio(colour, surface) >= floor:
        return colour, False
    for i in range(1, 101):
        c = mix(colour, ink, i / 100)
        if ratio(c, surface) >= floor + 0.01:
            return c, True
    return ink, True


def convert(text):
    """One Ghostty theme file -> the 17 tokens, or None if it is not one."""
    pal = {int(n): c.lower() for _, n, c in
           re.findall(r"(palette)\s*=\s*(\d+)=(#[0-9a-fA-F]{6})", text)}
    kv = {k: ("#" + v.lstrip("#")).lower() for k, v in re.findall(
        r"^\s*(background|foreground|cursor-color|selection-background)"
        r"\s*=\s*(#?[0-9a-fA-F]{6})", text, re.M)}
    if len(pal) != 16 or "background" not in kv or "foreground" not in kv:
        return None
    bg, fg = kv["background"], kv["foreground"]
    # POLARITY DECIDES WHICH HALF OF THE PALETTE READS. On a dark ground the
    # bright variants carry; on a light ground they are washed out and the
    # normal ones are what stay legible. Blind to this, 13 of 15 themes failed.
    hi, lo = (9, 1) if lum(bg) < lum(fg) else (1, 9)
    surface = mix(bg, fg, 0.06)
    # The FOREGROUND is adapted first and differently: comply() moves a colour
    # toward the ink, which does nothing for the ink itself. A theme whose own
    # text does not reach 4.5:1 on its own surface is pushed AWAY from it -
    # toward black on a light theme, white on a dark one - and the dim inks are
    # derived afterwards, from whatever the ink ended up being.
    ink, ink_adapted = fg, False
    if ratio(ink, surface) < FLOORS["ink"]:
        pole = "#000000" if lum(surface) > lum(ink) else "#ffffff"
        ink, ink_adapted = comply(ink, surface, pole, FLOORS["ink"])
    t = {"bg": bg, "surface": surface, "surface-2": mix(bg, fg, 0.11),
         "ink": ink, "ink-2": dim_to(ink, surface, 4.6), "ink-3": dim_to(ink, surface, 3.6),
         "sel": kv.get("selection-background", mix(bg, fg, 0.25)),
         "caret": kv.get("cursor-color", pal[14]),
         "act": pal[hi], "act-2": pal[lo], "ok": pal[hi + 1], "ok-2": pal[lo + 1],
         "gls": pal[hi + 2], "gls-2": pal[lo + 2], "job": pal[hi + 3], "job-2": pal[lo + 3],
         "mag": pal[hi + 4]}
    adapted = ["ink"] if ink_adapted else []
    for token, floor in FLOORS.items():
        if token == "ink":
            continue
        t[token], changed = comply(t[token], t["surface"], t["ink"], floor)
        if changed:
            adapted.append(token)
    return t, sorted(adapted)


def main(src, out):
    themes = {}
    for f in sorted(Path(src).iterdir()):
        got = convert(f.read_text())
        if not got:
            print(f"skipped (not a ghostty theme): {f.name}", file=sys.stderr)
            continue
        tokens, adapted = got
        name = f.name.replace("_", " ")
        themes[name] = {"tokens": tokens, "adapted": adapted,
                        "dark": lum(tokens["bg"]) < lum(tokens["ink"])}
    Path(out).write_text(json.dumps({
        "_source": "https://github.com/mbadolato/iTerm2-Color-Schemes (MIT), the "
                   "same collection Ghostty's built-in themes come from",
        "_licence": "The colour schemes are MIT licensed; see THEMES-LICENCE. "
                    "Every theme credits its original by name.",
        "_adapted": "An 'adapted' list names tokens moved toward the ink to clear "
                    "this app's contrast floors. Terminal accents are chosen for "
                    "large glyphs on their own ground; here they are 9-12px UI text.",
        "themes": themes}, indent=1, sort_keys=True) + "\n")
    faithful = sum(1 for t in themes.values() if not t["adapted"])
    print(f"{len(themes)} themes -> {out} ({faithful} faithful, "
          f"{len(themes) - faithful} adapted)")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
