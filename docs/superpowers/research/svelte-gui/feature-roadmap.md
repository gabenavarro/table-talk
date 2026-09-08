# Feature roadmap (after parity)

Merged from the three brainstorms (`workflow.md`, `presentation.md`,
`integration:.md`) against the chosen design (`shell`: Python brain + Svelte 5
view + a ~120-line Tauri 2 window). Nineteen + eighteen + seventeen ideas, minus
duplicates, minus everything that only made sense on the rejected gpuix-svelte
renderer, minus everything a real user would not reach for in a normal week,
leaves **seven**: two already scheduled inside parity, three worth building after
it, two worth building only if someone asks.

None of these touch the JSONL format, `bin/table-talk`'s CLI surface, or
`skill/SKILL.md`. One (#4) adds a new *value* to an existing config key and is
flagged where it lands.

## Ranked

| # | feature | value | cost | depends on | when |
|---|---|---|---|---|---|
| 1 | Native window identity — own icon, title, `(N)` open-count prefix, remembered size and position | high | S (already in phase 5) | Tauri `WebviewWindowBuilder` + `tauri-plugin-window-state` | **v1 parity** |
| 2 | In-app toast + title count on a **rising** open-action tally | high | S (already in phase 4) | `frame.status.tally`, baseline-then-rise discipline | **v1 parity** |
| 3 | Real OS notification on a rise while the window is unfocused; clicking it focuses the newest open action | high | S (~1 d) | #1, #2; `tauri-plugin-notification`, else spawn `notify-send`/`osascript` from the brain | **v1.1** |
| 4 | Open a linked path at its exact **line** in `$EDITOR` | high | M (~2 d) | `tt_model.path_spans` (unchanged); `links.open_command` gains an `"editor"` choice + a four-entry argv table | **v1.1** |
| 5 | Resident presence — tray/menu-bar icon carrying the tally, window close hides instead of quits, optional autostart | med-high | M (~3 d) | #3; `tauri-plugin-tray-icon`; one `systemd --user` unit + one `launchd` plist | later |
| 6 | Global hotkey that summons the wall from any app | med-high | M–L (~3 d, Wayland unknown) | `tauri-plugin-global-shortcut`; on Wayland the XDG desktop portal, verified before it is promised | later |
| 7 | Inline transcript tail under the `ix` button, opt-in and click-to-reveal | med | M (~3 d) | `transcripts()` (unchanged) + a seek-from-end line reader on the existing poll tick | later |

`S` ≤ 1 day, `M` 2–4 days, `L` 5+ — one engineer who knows the codebase,
tests and docs included, on top of the 28–31 days in §3.12.

## The features

**1. Native window identity.** This is not new work, it is the reason phase 5
exists, and it is listed so the parity sweep actually checks it rather than
shipping Tauri's defaults. The window carries table-talk's own icon and title,
holds its own Alt-Tab and Mission Control slot, and remembers its size and
position through `tauri-plugin-window-state`. The `(N) table-talk` prefix that
`TAB_TITLE_JS` maintains today by regexing rendered DOM text becomes a `$effect`
on `tally.open` that sets both the document title and the window title. Every
brainstorm ranked this "ship v1" independently and all three were right for the
same unexciting reason: it falls out of having a window at all. Worth naming
once so nobody counts it twice as a roadmap item.

**2. Rising-tally toast.** Also already parity, restated here because it is the
foundation #3 extends rather than replaces. The rule is the one `TOAST_JS`
learned: fire only when the open-action count rises against a baseline read at
the *first* frame, so a reconnect or a browser refresh never bursts a stack of
toasts for actions that were already there. The Svelte version is a `$effect`
with the same baseline discipline and a 5 s dismiss. If this rule regresses,
#3 becomes an alarm clock that goes off every time the SSE connection blinks.

**3. Real OS notification.** The in-app toast only exists while the window is
visible and rendering, which is precisely the case where nobody needs telling.
The whole product is "an action is waiting on you", so the one attention channel
worth adding is the one that works when table-talk is minimised, behind an
editor, or on another workspace: a genuine OS notification through the
notification centre, so it is still in the history after the coffee break.
Tauri's notification plugin is a one-line call and needs no permission dialog for
a locally-installed app; the fallback is the same guarded `spawn` idiom the
codebase already uses for `open_command` (`notify-send` on Linux,
`osascript -e 'display notification'` on macOS, silent if neither exists).
The click handler is what makes it a loop rather than an interruption: activating
the notification focuses the window and issues the `{do:'focus', key}` intent the
drawer already sends, landing the caret on the newest open action. Everything
here rides on plumbing #2 already built — one number, one rising edge, now three
surfaces.

**4. Open at the exact line in `$EDITOR`.** Today a linked path goes to
`xdg-open`/`open`, which routes through the OS file-association table and has no
concept of a line number — so `path_spans` drops a trailing `:LINE` entirely, a
limitation the current dashboard documents rather than fixes. For a person whose
day is reading a `why` field that names `bin/tt_wall.py:412` and then going
there, this is the single most-used link in the app and it currently lands at the
top of the file. The fix is small and stays inside the existing security
boundary: the brain still re-derives confinement from scratch at click time,
still launches an argv **list**, still never uses `shell=True`; only the command
built changes, from `[open_command, path]` to `["code", "--goto", f"{path}:{n}"]`
or `[$EDITOR, f"+{n}", path]`, with a fallback to today's whole-file open when
the editor is not in the four-entry table (`code`, `zed`, `vim`/`nvim`,
`emacs`). The line number is computed at click time by scanning the file, never
stored, exactly as confinement is — which is also why this needs no log-format
field. It is the one item on this roadmap that touches config: `links.open_command`
gains `"editor"` as a validated choice. §3.10's "no config key is added,
removed or re-typed" is a promise about the *parity migration*; this is a
post-parity feature adding one enum value to one key's existing validator, and it
should be argued in its own PR rather than smuggled in under that sentence.

**5. Resident presence.** The brain already survives the window closing — it is a
server. What dies is every trace of table-talk on screen, which is the real gap
between "a dashboard" and "a thing that lives on your desktop": with no window
open there is nothing showing that three actions are waiting. A tray/menu-bar
icon carrying the same tally (`●N`, tinted for blocked) closes that, and it is
also what makes #3 honest — a notification you can act on when nothing is open.
The shape is ordinary: the Tauri shell keeps running with its window hidden
rather than exiting, the tray icon owns show/hide and quit, and an optional
`install.sh --autostart` writes one `systemd --user` unit or one `launchd`
plist. Two deliberate limits. It ships opt-in, because a process that keeps
running after you close its window is exactly the kind of thing that should
require an explicit yes. And Linux tray support is `StatusNotifierItem`, which
GNOME still needs an extension for — so this lands as "works on KDE and macOS,
degrades to nothing visible elsewhere", verified per desktop rather than
asserted, or it does not land. That caveat is why it is `later` and not `v1.1`
despite being the highest-value item on the list after #3.

**6. Global summon hotkey.** One key combination from inside any application
brings the wall to the front — the Spotlight/Raycast move, aimed at the moment
someone finishes a thought in their editor and wants to know what is waiting
without hunting a taskbar. All three brainstorms reached for it and one called it
the best idea on its list. It is `later` for one honest reason: on the
maintainer's own desktop (Fedora, Wayland) a global shortcut is not a hotkey
registration but an XDG desktop portal request, whose behaviour varies by
compositor and which may show the user a permission prompt or silently do
nothing. macOS is straightforward (and prompts for Accessibility permission the
first time). So this is a two-day feature with a one-day spike in front of it,
and the spike has to come first: build it only after `tauri-plugin-global-shortcut`
is confirmed working on Fedora/Wayland, and drop it rather than shipping a key
combination that works on one of the two required platforms. The
quick-capture-popup variant several brainstorms proposed on top of it is cut
below — summoning the wall and typing into the reply box that is already there
does the same job with no second window.

**7. Inline transcript tail.** The `ix` button hands the whole Claude Code
transcript to an external viewer, which is a context switch for the common
question, "what is this session actually doing right now?" The brain already
resolves the transcript path every poll (`transcripts()`, including its rule that
an ambiguous 4-char prefix is dropped entirely) and already runs a 2 s tick, so
the last few KB of that file can be read on the same tick with no new polling
loop and no new dependency — a seek-from-end-and-count-newlines reader is about
fifteen lines. It ranks below the rest because it is the only feature here that
*widens what is shown* rather than changing how: a transcript carries full tool
output, meaning file contents and command output for anything the session
touched. So it ships collapsed and opt-in behind a reveal click, never open by
default, unlike every section beside it. Reason to build it at all: for someone
watching several agents at once, "is it stuck or is it working" is asked far more
often than any other question the wall cannot currently answer.

## Cut, and why

Merged into the seven above: dock/taskbar badge count (#5's tally, a third
surface for a number already on two); quick-capture popup (#6 plus the reply box
that already exists); "zero-hop spawn for open-in-editor" (that is #4, and the
latency claim was marginal — loopback is ~5 ms); "window is its own Alt-Tab
citizen" (#1).

Cut on evidence, from the brainstorms' own reading: system tray via GPUI,
multi-window wall, always-on-top HUD, background survival, OS-idle-aware
batching, `WindowOptions.fullscreen`, GPU tweens, `set_css_vars` theming, native
cursor shaping, GPU virtual-list scrolling — every one of these is scoped to the
gpuix-svelte renderer the spec rejected in §2.1, and either does not exist in its
API or is delivered for free by a webview.

Cut as YAGNI: **full-history search index** — `poll()` already folds *every*
`*.jsonl` in `DATA_DIR` each tick, so the drawer's filter already searches all
history; a SQLite FTS5 index would index data that is already in memory.
**Deep-link `table-talk://focus/<id>`** — nothing anywhere emits such a link, and
inventing an emitter means touching the CLI. **Timeline scrubber** — change
gutters and watermarks already answer "what moved since I looked", which is the
weekly version of the question. **Live theme-token editor** — theming is a
once-a-year task; editing the TOML is fine. **Drag-and-drop file attach**, **macOS
Services menu**, **Spotlight importer**, **remote tray-only companion**,
**resizable split panes**, **macOS vibrancy** — real ideas, none of them reached
in a normal week; the split panes would also turn `pack()`'s pinned determinism
into user-perturbable state for a cosmetic win. **Activate the live terminal from
the `◉` beat flag** — Wayland deliberately gives unprivileged clients no
raise-that-window API, so it would no-op on the maintainer's own desktop.

Not a feature, kept as a contingency: if a real wall ever scrolls badly, the next
move is the one §3.11 already names — drop `done` rows and glossary bodies from
the frame until their section is open — not a virtualisation library.
