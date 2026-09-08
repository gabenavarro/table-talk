# gpuix-svelte — capability read for a dense text dashboard

Clone read: `/tmp/.../scratchpad/gpuix-svelte` @ `b474c83`. Probe run: copied to
`/tmp/.../scratchpad/svelte/probe-gpuix/`, `npm install` (Node 25.8.2, no Bun), on Fedora
Linux x64 (Wayland). No windows were opened (instructed not to); findings below on Linux are
from `npm run typecheck`, running each `test:*` script, and reading `@gpuix/native`'s installed
`index.d.ts`.

## What it is

A Svelte 5 **custom renderer** (`svelte/renderer`'s `createRenderer`, an unreleased API —
[sveltejs/svelte#18511](https://github.com/sveltejs/svelte/pull/18511)) that projects a Svelte
component tree onto **GPUI** (Zed's Rust/GPU UI framework) through the napi addon
`@gpuix/native`, with no DOM and no webview anywhere in the stack. A `.svelte` file compiles at
import time (a Node `module.registerHooks` loader or a `Bun.plugin`, `src/register.ts` /
`src/plugin.ts`) into JS that imports `gpuix-svelte/renderer` and calls Svelte's `mount()` against
a JS shadow tree (`src/renderer.ts`); that tree batches DOM-shaped mutations into one
`applyBatch(json)` FFI call per frame. The same element tree also compiles to WebAssembly/WebGPU
for a browser build (Bun only). One author (Stanislav Khromov), MIT license, npm name reserved
2026-09-01, first real release will be 0.1.0 (README.md:1-4, CLAUDE.md:683-684).

## Maturity and risk

- **Built on an unreleased, actively-rebased Svelte branch.** `dependencies.svelte` is
  `file:vendor/svelte-5.57.0-ff9658a.tgz` — a build of a 4-PR stack
  (#18042→#18405→#18461→#18511) pinned as a vendored tarball and bundled into the npm package
  (CLAUDE.md:304-331). It cannot be a URL pin: pkg.svelte.dev serves a build only for the PR's
  *current* head and 404s once the branch is force-pushed, which CLAUDE.md says happens "about
  weekly" (CLAUDE.md:640-643, 322-325). `npm run vendor` re-fetches it; if upstream ever drops the
  commit entirely, the fallback is building Svelte from a local checkout by hand
  (CLAUDE.md:326-329). This is the single biggest structural risk: the project's entire API
  surface (`bind:` refusal, snippet plumbing, `createSubscriber`) rides on a PR that could change
  shape or stall before merging.
- **`@gpuix/native` is pinned narrow** (`>=0.7.0 <=0.8.0`, installs 0.7.0 — package.json:38) with
  prebuilds for **darwin-arm64 / linux-x64 / win32-x64 only** (HOWTO.txt:75, CLAUDE.md:336) — no
  linux-arm64, no darwin-x64 (Intel Mac), no win32-arm64. Verified installed: `@gpuix/native@0.7.0`,
  Apache-2.0, 20 MB unpacked (includes a 19.9 MB wasm build shipped unconditionally).
- **Bus factor / release cadence**: one GitHub repo, one named author, CI is two macOS jobs
  (Node + Bun) per `docs/comparison-gpuix-solid.md:82` — **Linux is compiled, never tested**, in
  upstream CI. `docs/todo/*.md` (10 files) track a full backlog of unshipped work — auxclick
  events, test helpers, window size/insets, debug overlay, text search, live automation,
  tooltip/popover, style parity fixes, benchmarks, a native-parity table — none merged as of
  `e729a86`/`b474c83`. This is pre-1.0, days-old software, not a project with a track record.
- **Linux status, verified empirically in this sandbox**:
  - The live `GpuixRenderer` (the one that opens a real window) is asserted by CLAUDE.md to work
    on Linux (CLAUDE.md:456-457, "on Windows/Linux... `tick()` only reports whether that thread is
    still alive") — **not independently verified here**, since opening a window was out of scope.
  - The **headless test renderer does not exist on Linux**, confirmed by running it:
    `node --conditions custom-renderer --conditions development -e "require('@gpuix/native').hasTestGpuixRenderer()"`
    → `false`, and instantiating `new TestGpuixRenderer(400,200)` throws
    `"TestGpuixRenderer is macOS and Windows only. Linux builds have no test-support because wgpu
    cannot read a rendered image back yet. GpuixRenderer still works on Linux."` This matches
    HOWTO.txt:94-97 and CLAUDE.md:336-339 exactly.
  - Practical consequence, also verified: **`npm test` cannot run on Linux at all.** Of the 15
    chained `test:*` scripts, only `test:compile` (pure compiler/AST logic, no native calls,
    src/compile.ts) passed clean (13/13 checks). `test:style` ran 22 pure-JS CSS-parsing checks
    (`parse_css_text`, no native) and then crashed the moment it tried
    `new TestGpuixRenderer(400, 200)` at `test/style.ts:107`. Every other script
    (`reorder`, `smoke`, `autocommit`, `teardown`, `lifecycle`, `css`, `module`, `vars`,
    `scroller`, `hitbox`, `auxclick`, `window-keys`, `portal`) threw immediately on
    `mount_headless()` (`src/test.ts:56-58`, the same guarded error). `npm run typecheck`
    (`tsc --noEmit`, strict) passed clean.
  - Consequence for the requested probe: since Linux has no headless renderer and this task
    forbids opening a real window, **there is no way on this machine to render a component to an
    image and check it** — not `mount_headless`+`screenshot()` (headless is unavailable), not
    `GPUIX_SCREENSHOT=... npm run demo:*` (that opens a real GPUI window). The tiny probe
    component (40-row monospace grid, bold coloured id column, block-glyph progress bar,
    clickable row, text input) was written conceptually against the documented API but **could
    not be executed or screenshotted** on this Linux sandbox under the "no windows" constraint —
    it would need either a macOS/Windows machine, or a Linux machine where opening a window is
    permitted.

## The styling model

There is no CSS engine — `style="…"` is CSS *text*, parsed at runtime (`<style>` blocks) or
compile time (class rules) into a plain camelCase object GPUI deserializes
(`src/style.ts:240-259`, `build_style` at `src/style.ts:281-300`). GPUI's layout is flexbox (and a
grid subset) in **logical pixels**; there is no viewport unit and no `rem` scaling (`rem` is a
flat literal 16 px per `docs/todo/8-style-parity.md`, and that fix is not yet landed — currently
`1rem` is dropped with a warning, README.md:240).

- **Exact subset**: layout (`display: flex|grid`, all flex/gap/align/justify props), sizing
  (`width/height/min*/max*` in `px`, `%`, or `auto` — the *only* properties that take `%`/`auto`,
  `src/style.ts:55`), spacing (`padding`/`margin`/`border-width`/`border-radius`/`gap`/`inset`,
  1–4-value shorthands expanded in `src/style.ts:223-238`), position (`relative`/`absolute`, not
  `fixed` — it lays out identically to `absolute`, README.md:288), overflow (`hidden`/`scroll`
  only — `auto` is silently a no-op, README.md:265-266, `docs/todo/8-style-parity.md:25-27`), paint
  (`background-color`/`color`/`border-color`, any CSS colour syntax, `opacity`), text
  (`font-size`/`font-family`/`font-weight`/`text-align`/`line-height`(px only)/`white-space`/
  `text-overflow: ellipsis`), interaction (`cursor` keyword set, `pointer-events`, `user-select:
  none`, `:hover`/`:active` — natively-handled pseudo-styles, not JS-tracked). Unitless numbers are
  pixels (`padding: 12`). Full lists: README.md:255-312.
- **Refused outright at compile time, with a warning naming the file**: descendant combinators,
  `:global`, attribute selectors, `@media`/any at-rule, and nesting in `<style>` blocks
  (`src/compile.ts:77-97` `compile_selector`, refuse path at `src/compile.ts:104-111`). So no CSS
  custom-property cascade beyond `set_css_vars()`, no `@font-face` (custom font *files* cannot be
  loaded through CSS at all — only OS-installed fonts referenced by `font-family` name are
  reachable, README.md:269 confirms `font-family` is a plain string pass-through with no loading
  mechanism documented anywhere in this repo).
- **Accepted but silently dropped/ignored**: `rem`/`em`/`vh`/`vw` units, `%`/`auto` outside
  dimension keys, `flex: 1` (use `flex-grow`), `border: 1px solid …` (split it), `box-shadow`,
  `linear-gradient`, `transform`, `transition`, `z-index`, `text-decoration`, `letter-spacing`,
  `display: none`/`visibility` (use `{#if}`) (README.md:238-250, and `src/style.ts:194-202`
  `accepts()` for exactly what a wrong value does — throws out of `applyBatch` if not caught, so
  every rejected value is dropped with a one-time `console.warn` instead, `src/style.ts:209-221`).
- **Colours/opacity**: any CSS colour syntax (hex incl. 8-digit alpha, `rgb()`, `hsl()`, named) via
  the native `csscolorparser`; `opacity` is a plain float property, not a stacking-context blend.
- **Fonts/monospace**: `font-family` is a normal string; Substrate's worked example uses
  `IBM Plex Sans` for UI and **`Lilex`** (a real monospace font) for code (`examples/second-brain/lib/theme.ts:112`
  `FONT = { sans: 'IBM Plex Sans', mono: 'Lilex' }`), so a monospace grid is a plain
  `font-family: Lilex` (or any installed monospace face) on a `<text>`/`div`; the renderer does not
  special-case monospace layout — column alignment is done with fixed `width` on cells, exactly
  like a flexbox table.
- **Emoji and box-drawing glyphs**: not specially handled or blocked — they are ordinary UTF-8 text
  content, rendered by whatever glyph coverage the resolved `font-family` has (no code in this
  repo tests emoji or box-drawing directly; nothing forbids them, but nothing loads a custom font
  file to guarantee coverage — see the `@font-face` refusal above). Treated as **workaround**
  in the verdict table: it depends on picking a system-installed font with the right glyphs.
- **Animations**: only through the `motion={{ initial, animate, transition }}` prop on
  `left`/`top`/`width`/`height`/`opacity`/`border-radius` (durations in seconds), a native tween —
  CSS `transition`/`transform` are ignored (README.md:309-311, CLAUDE.md:576-577).
- **Theming**: `set_css_vars({ token: value })` restyles exactly the elements whose style read
  `var(--token)`, in one batch (`src/renderer.ts:599-604`); values are substituted at runtime by
  `src/style.ts:143-181` `substitute_vars`. This is a first-class, well-built feature — Substrate's
  `LIGHT`/`DARK` palette objects (`examples/second-brain/lib/theme.ts:7-105`) are the worked
  example, switched with one `$effect` call.

## Text and input

- **Text inputs**: `<input>`/`<textarea>` report through `onchange`/`onsubmit` events carrying the
  value (`e.value`) — `bind:` is refused by the compiler under a custom renderer
  (README.md:441-449). Getting a handle needs `{@attach (node) => …}`, and `node.nativeId` feeds
  `get_native()`'s methods (README.md:451-452).
- **Focus**: `tabindex="0"`/`autofocus` make an element focusable; `keyDown`/`keyUp` need focus.
  Since native 0.7.0, **Tab no longer moves focus** — an app must call `focusNext()`/
  `focusPrevious()` itself (native methods exist, CLAUDE.md:544-545, but gpuix-svelte wraps
  neither yet — `docs/todo/10-native-parity-table.md:39` lists them as unwrapped). `focus_element(node)`
  / `blur()` are the package's window helpers (`src/window.ts:21-23`, `:18`).
- **Keyboard shortcuts**: `on_window_key('keydown'|'keyup', handler)` fires whatever has focus,
  survives remounts, and hands back an unsubscribe (`src/renderer.ts:642-660`); `event.editing`
  tells a shortcut handler a text field is getting the same key, so it can step aside
  (README.md:412-439). This is solid and is exactly the primitive a keyboard-first dashboard wants.
- **Clipboard**: **not exposed by `@gpuix/native` at all** — confirmed by
  `docs/comparison-gpuix-solid.md:291-292`: "Clipboard and cursor APIs do not exist natively;
  Substrate shells out to `pbcopy` and `pbpaste`." Substrate's real implementation
  (`examples/second-brain/lib/clipboard.ts:18-34`) picks a shell command per platform: `pbcopy`/
  `pbpaste` (macOS), PowerShell `Set-Clipboard`/`Get-Clipboard` (Windows), and on Linux **`wl-copy`/
  `wl-paste` if `$WAYLAND_DISPLAY` is set and the binary exists, else `xclip`, else `xsel`**, else
  clipboard is a no-op. On this machine (Fedora, Wayland) that means clipboard text works only if
  `wl-clipboard` (or `xclip`/`xsel`) is installed — not guaranteed, not the gpuix-svelte package's
  job to install. Clipboard **images** are Bun-only (`Bun.Image.fromClipboard`) and explicitly
  disabled on Linux (`clipboard.ts:9`, `process.platform !== 'linux'`). Verdict: workaround, and
  the workaround is itself platform-fragmented.
- **Scrolling**: GPUI paints no scrollbar and does not capture the pointer, so
  `gpuix-svelte/components/Scroller.svelte` measures painted bounds/scroll offset on a timer,
  draws its own thumb, and drags it on a mouse-move overlay (`src/components/Scroller.svelte:44-129`).
  A `virtual` prop swaps in GPUI's native `<virtual-list>` — only rows near the viewport are
  built/painted (`Scroller.svelte:160-172`); Substrate's 50-card timeline went from ~24 ms to
  ~1.4 ms per frame switching to it (CLAUDE.md:369-370). This is the primitive that makes "hundreds
  of rows" viable.
- **Hit testing**: real GPUI hit testing, but **no mouse-event bubbling** and **no pointer
  capture** (CLAUDE.md:560-575) — a painted child occludes a clickable ancestor unless
  `hitbox="self"` is set on the ancestor, which then ships `pointer-events: none` to
  non-interactive, non-scrolling, non-listening descendants (`shielded()`,
  `src/renderer.ts:257-262`, applied in `src/renderer.ts:277-298`). Drags need manual
  `mousemove`/`mouseup` handling on the surfaces the cursor may cross, since GPUI never captures
  the pointer.
- **Hover**: native pseudo-style objects (`hover="…"` attribute or `:hover` in `<style>`), not
  JS-tracked — no hover-state prop, no `mouseenter`/`mouseleave` needed just to restyle
  (README.md:230, :293-296).
- **Context menus**: no OS context menu exists. A right-click (`onauxclick`, `e.isRightClick`) is
  drawn by the app as a `<Portal>` positioned at the click coordinates — Substrate's
  `components/ContextMenu.svelte` is the full worked example (positioning math at
  `ContextMenu.svelte:17-35`, arrow-key navigation at `:50-59`, `Escape`/click-outside close via
  `on_window_key` and a backdrop div). This is a real, working pattern but entirely
  application-code — there is no shipped `ContextMenu` component in the package itself
  (`docs/todo/7-tooltip-popover.md` tracks shipping `Tooltip`/`Popover`, not a context menu).

## Windows

- **Single window only.** `@gpuix/native`'s `GpuixRenderer` constructor takes one
  `eventCallback` and `.init(options?: WindowOptions)` opens the one window the process gets;
  `render.ts` holds it in a single `globalThis` slot (`SLOT = Symbol.for('gpuix.svelte.host')`,
  `src/render.ts:27-43`) precisely so hot remounts reuse it. `docs/comparison-gpuix-solid.md:283-284`
  states it directly: "Native supports one window either way." **There is no API to open a second
  window** — no window array, no `createWindow()`. A tmux-style wall of many session windows, as
  table-talk's dashboard currently does, is **not possible** as multiple native OS windows; it
  would have to be one window with an in-app tiled/tabbed layout instead.
- **Geometry**: `WindowOptions` (verified from the installed `@gpuix/native@0.7.0` `index.d.ts:502-537`)
  has `title`, `appName`, `width`, `height`, `minWidth`, `minHeight`, `resizable`, `fullscreen`,
  `transparent`, `titlebarTransparent`, `windowBackground` (`"opaque"|"transparent"|"blurred"`),
  `trafficLightX/Y` (macOS-only traffic-light positioning), `focus`, `show` (both "ignored on
  Linux" per their own doc comments). **No `x`/`y` position field exists** — a window cannot be
  placed at a specific screen position from the API, only sized. Runtime querying:
  `getWindowSize()` exists on both renderers; `getWindowInsets()` exists on the live renderer only
  (`index.d.ts` around the `WindowInsets`/`getWindowInsets` declarations, task 3 in
  `docs/todo/3-window-geometry.md` — not yet wrapped by gpuix-svelte, `window_size`/`window_insets`
  helpers are still a TODO, not shipped).
- **Always-on-top**: **no such field in `WindowOptions`**, confirmed by reading the installed
  d.ts in full — not possible through this API today.
- **Title**: `title` at init, and `setWindowTitle(title)` at runtime
  (`src/window.ts:12`, `WindowOptions.title` `index.d.ts:503`).
- **Transparency**: `transparent: boolean` (plain alpha) and `windowBackground: "blurred"` (macOS
  vibrancy — CLAUDE.md:680-682 confirms this is macOS-only; elsewhere the liquid-glass demo fakes
  it by darkening its own panel over a plain `"transparent"` window). `titlebarTransparent` lets an
  app draw under the traffic lights (macOS chrome concept; meaningless on Linux/Windows chrome).

## Process model

- **Main thread vs. workers**: the renderer itself is single-process, single-thread JS driving a
  native addon; there is a ~125 fps `setTimeout(8ms)` loop calling `native.tick()`
  (`src/render.ts:25`, `:62-106`) on macOS (where GPUI needs polling), and on Windows/Linux
  (`requiresTick()` false) mutations self-schedule on a microtask instead
  (`set_auto_commit(true)`, `src/render.ts:63-72`, `src/renderer.ts:689-691`). A separate OS
  process for heavy work is entirely the app's own doing via plain `node:child_process` /
  `Bun.spawn` — Substrate's ML models run in a **child process** (`examples/second-brain/ml/worker.ts`)
  talked to over `Bun.spawn` IPC (CLAUDE.md:66-68), which is Bun-only tooling, not a package
  feature. There is no built-in RPC/IPC layer to a non-JS process (e.g. a Python backend) — an
  integration with table-talk's Python CLI/dashboard would need a hand-rolled bridge (spawn +
  stdio JSON lines, or a local HTTP/socket server), the same shape `docs/todo/6-live-automation.md`
  proposes for its own automation protocol (NDJSON over stdio, no new deps).
- **File watching**: `render_hot`'s own `node:fs` `watch(dir, { recursive: true })`
  (`src/hot.ts:31-54`) — `.svelte` file edits hot-reload (remount, debounced 60 ms); `.ts`/`.svelte.ts`
  edits are detected but only warn ("restart to pick it up") since modules are cached per-process
  by design (`src/hot.ts:34-41`), which is also what lets `.svelte.ts` state (route, theme) survive
  a hot reload.
- **Timers**: plain `setTimeout`/`setInterval`, nothing GPUI-specific (Scroller's own thumb-refresh
  throttle, `Scroller.svelte:133-140`, is a normal example).
- **Hot reload**: built in and central to the dev loop (`render_hot`, `src/hot.ts`); `bun --hot`
  must never be used instead, since it swaps Svelte's whole runtime and orphans the old component
  (CLAUDE.md:301-303).
- **Reading files/dirs, spawning processes, opening URLs/files**: none of this goes through
  gpuix-svelte — it's just Node/Bun code inside a `.svelte` `<script>`. `examples/hacker-news/HackerNews.svelte:1-2,41-51`
  is the concrete pattern: `import { spawn } from 'node:child_process'` inside a component,
  picking `open`(macOS)/`start`(Windows)/`xdg-open`(Linux) to open a URL, with `child.on('error', …)`
  guarded so a missing `xdg-open` doesn't crash the window. The same pattern works for "open in
  editor" (spawn `$EDITOR`/`code`) — nothing native-specific stops it, since the whole process is
  a normal OS process with normal Node/Bun capabilities.

## Packaging

- **`npm run compile`** (`scripts/compile.ts`, **Bun-only** — "it refuses to run under Node",
  CLAUDE.md:137-138) does `Bun.build({ compile: true })` over a static-`render()` entry point (not
  `render_hot`, which needs disk access) into a single executable — `dist/tictactoe` /
  `dist\tictactoe.exe` on Windows. Result size **~80 MB**: the Bun runtime + the Svelte runtime +
  the **17 MB GPUI addon** (README.md:63-78). Built for the machine that runs the command — no
  cross-compiling, since npm only installs the host's prebuilt native addon (README.md:73-75);
  producing Linux, macOS and Windows binaries means running the build on all three.
  `npm run compile:app` additionally wraps a macOS `.app` with an icon rasterized from
  `examples/tic-tac-toe/icon.svg`.
- **Signing**: opt-in via env vars so CI stays unsigned — `CODESIGN_IDENTITY` (a Developer ID
  Application cert) signs with the hardened runtime; `NOTARY_PROFILE` (from
  `xcrun notarytool store-credentials`) additionally notarizes and staples the `.app`
  (README.md:104-132, CLAUDE.md:229-233). macOS-specific; nothing analogous is documented for
  Windows (no Authenticode signing step) or Linux (no AppImage/deb packaging at all — just the raw
  binary).
- **The Node vs. Bun split**: everything in the package itself runs on **either** runtime — Node
  ≥24 via `tsx` + `module.registerHooks` (`src/register.ts`), or Bun ≥1.4 via `Bun.plugin`
  (`src/plugin.ts`) — with every script doubled as a `bun:`-prefixed twin. **Bun-only** pieces:
  `compile`/`compile:app` (`Bun.build({compile})`), `demo:web` (Bun's `with { type: 'file' }` wasm
  import attribute), and the entire Substrate example (`bun:sqlite`, `Bun.spawn` IPC, `Bun.Image`,
  `HTMLRewriter`, `bun:ffi` — CLAUDE.md:63-68). Since **this machine has no Bun installed**, none of
  those three are usable here without first installing Bun; the core renderer, hot reload, and
  every non-Substrate demo work on plain Node (confirmed: `npm install`, `npm run typecheck` both
  succeeded with Node 25.8.2 only).

## The web/wasm target

Confirmed from CLAUDE.md:235-278 and README.md:80-102 (not independently run — it is Bun-only and
this machine has no Bun): GPUI itself compiles to `wasm32-unknown-unknown` and paints a
WebGPU/WebGL2 `<canvas>` — there is no DOM renderer, so this is pixel-identical desktop-app output
inside a canvas, not an HTML page. Hard requirements: **Bun only** (the `browser.mjs` entry uses
Bun-only `with { type: 'file' }` wasm import attributes); the page **must** be cross-origin
isolated (`COOP: same-origin`, `COEP: require-corp`, `.wasm` served as `application/wasm` — GPUI's
wasm uses `SharedArrayBuffer`); `file://` cannot work. Window options (`title`/`width`/`height`)
are ignored — the canvas fills the page and is sized with ordinary CSS. It is a **19.9 MB**
uncompressed download. The wasm class differs from the desktop napi class in ways that matter for
reuse: `requiresTick()` is hardcoded false, `captureScreenshot`/`activateWindow`/
`simulateKeystrokes`/the debug-overlay pair do not exist, and there is **no automated test path**
for it at all (`TestGpuixRenderer` is napi-only) — CLAUDE.md recommends manual verification via
headless Chrome over CDP.

## Testing

`gpuix-svelte/test` (`src/test.ts`) wraps `TestGpuixRenderer` (real Metal/DirectX pipeline, no
window) in a mount/settle/hit-test loop: `mount_headless`, `settle()`, `wait(ms)`, `tree()`/
`find_text`/`find_test_id`/`element_of` over `getTreeJson()`, and `click`/`click_text`/
`click_test_id`/`press`/`type` that go through **real GPUI hit testing** (not a bypassed
`dispatch()`, which the repo's own convention warns can pass while a real window fails — CLAUDE.md:156-157).
Plain scripts, no runner: `check(label, actual, expected)` / `finish(name, expectedCount)`, exiting
1 on any failure or on a check-count mismatch (`src/test.ts:223-251`) — the count guard exists
specifically so a check that silently stops executing can't leave the file green.

**This entire mechanism is unavailable on Linux** (verified above: `hasTestGpuixRenderer()` is
`false`, `new TestGpuixRenderer()` throws by design — "wgpu cannot read a rendered image back
yet"). `GPUIX_SCREENSHOT=path npm run demo:*` is the Linux/any-platform fallback for *seeing* a
running app, but it requires actually opening a real window and is not a repeatable, scriptable
test — it is a manual "open it and look" workflow. There is a `getPaintedText()`/`getAllText()`
pair and `captureScreenshot()` on the **live** renderer too, but again gated behind opening a real
window. Net effect for a Linux CI pipeline or a sandboxed agent: **no automated verification of
rendering, layout, or interaction is possible without a macOS or Windows runner** (or a Linux box
where a real GPUI window is allowed to open — untested here, out of scope for this task).

## Licence

- **gpuix-svelte**: MIT (Stanislav Khromov, 2026) — `LICENSE`.
- **@gpuix/native**: Apache-2.0, confirmed from the installed package's `package.json` (`license:
  "Apache-2.0"`), 20 MB unpacked including the native binding and the 19.9 MB wasm build.

## Verdict table — table-talk's UI-inventory needs

| Need | Verdict | Evidence (file:line) |
|---|---|---|
| Dense monospace rows | **Supported** | `font-family` is a plain pass-through property (README.md:269); Substrate sets a real mono face, `examples/second-brain/lib/theme.ts:112` (`FONT.mono = 'Lilex'`). Column alignment is fixed-`width` flex cells, not a terminal-grid primitive — same technique the styling playground and Substrate use throughout. |
| Box-drawing sketches | **Workaround** | Glyphs are ordinary UTF-8 text; rendering depends on the resolved font's glyph coverage. No `@font-face`/custom font-file loading exists — at-rules are refused at compile time, `src/compile.ts:104-111` (`refuse()`). Must rely on an OS-installed font that happens to cover `─│┌┐└┘` etc.; not verified to fail, just not guaranteed. |
| Mermaid diagrams | **Workaround** | No DOM, no canvas-2D drawing API, no bundled SVG renderer beyond a raw `<svg source>` string (CLAUDE.md:237 "no DOM renderer and there never was one"). Mermaid needs its own DOM/canvas to lay out; the realistic path is pre-rendering diagrams out-of-process (mermaid-cli) to PNG/SVG and displaying via `<img src>`, which takes a filesystem path or `data:` URL only, never `http` (README.md:530 area, CLAUDE.md:530-531). |
| Collapsible sections | **Supported** | Plain `{#if}` toggling, no special primitive needed — Substrate's `Modal`/`ContextMenu` already do exactly this (`examples/second-brain/components/ContextMenu.svelte:78-113`). |
| Drawer tree | **Supported** | Recursive components + `{#if}` + indentation via padding; large flattened lists can go through `<Scroller virtual>` (`src/components/Scroller.svelte:160-172`) which renders GPUI's native `<virtual-list>`, building only rows near the viewport. |
| Keyboard-first | **Supported (with a gap)** | `on_window_key('keydown'|'keyup', handler)` fires regardless of focus and reports `e.editing` (`src/renderer.ts:642-660`). Gap: Tab no longer moves focus since native 0.7.0, and `focusNext()`/`focusPrevious()` exist natively but are unwrapped by the package (`docs/todo/10-native-parity-table.md:39`) — full tab-order navigation needs hand-rolled wiring today. |
| Clipboard | **Workaround** | No clipboard API in `@gpuix/native` at all (`docs/comparison-gpuix-solid.md:291-292`). Substrate shells out per-platform: `pbcopy`/`pbpaste` (macOS), PowerShell (Windows), `wl-copy`/`wl-paste` → `xclip` → `xsel` on Linux (`examples/second-brain/lib/clipboard.ts:18-34`) — text-only on Linux, and only if one of those binaries is installed; clipboard images are Bun-only and explicitly disabled on Linux (`clipboard.ts:9`). |
| Open-in-editor | **Supported** | Plain `node:child_process.spawn`/`Bun.spawn` from inside a component — the exact pattern already shipped for "open in browser": `examples/hacker-news/HackerNews.svelte:1-2,41-51` (spawns `open`/`start`/`xdg-open`, with an `error` handler so a missing binary can't crash the window). |
| Themes | **Supported** | First-class: `set_css_vars({...})` restyles every element that reads a changed `var()` in one batch (`src/renderer.ts:599-604`, substitution in `src/style.ts:143-181`); Substrate's `LIGHT`/`DARK` palettes are the complete worked example (`examples/second-brain/lib/theme.ts:7-105`). |
| Per-2s refresh of hundreds of rows | **Supported** | Svelte's fine-grained reactivity means unchanged rows emit no mutations; `<Scroller virtual>` renders GPUI's native `<virtual-list>`, building/painting only rows near the viewport — Substrate's 50-card timeline went from ~24 ms to ~1.4 ms/frame moving to it (CLAUDE.md:369-370, "Measuring frame cost" baselines at CLAUDE.md:210-213). Not independently benchmarked here (no headless renderer on Linux — see Testing). |

**Cross-cutting blocker not in the original inventory but load-bearing for the "wall of session
windows" concept**: gpuix-svelte / `@gpuix/native` 0.7.0 supports **exactly one native window per
process** (`docs/comparison-gpuix-solid.md:283-284`; `WindowOptions` has no window-array or
`createWindow` API, `node_modules/@gpuix/native/index.d.ts:502-537` read in full). Table-talk's
current tmux-shaped wall of separate session windows cannot map onto separate OS windows in this
renderer — it would need to become one window with an in-app tiled/tabbed layout, or the dashboard
would need to run one OS process per session window (each with its own native window), which is a
materially different process model than today's single NiceGUI server.
