---
name: open-in-file-manager
description: 2026-10-13 the gallery gains "open in the OS file manager" (§4.18 adds POST /api/fs/reveal) - an allowlisted path, no loopback gate, and an icon-only control because the words cost a toolbar row
metadata:
  type: topic
---

# Opening the directory on screen in the OS file manager (2026-10-13)

The user's words: "add a button in the gallery area that opens the folder in Explorer".

Everything this app does happens inside the page, and a browser cannot open a folder. The next thing
after reading a caption is often on the desktop - drop a file in, compare two folders, look at what the
trainer wrote - and the only route was to read the path off the screen and walk there by hand. So
this is the second thing in the app that has an effect **outside** the process (the first is the
launcher scripts), and it is the first endpoint that starts a program.

## 1. `POST /api/fs/reveal` (`api/reveal.py`, §4.18)

    {"path": str}   (empty = the first allowlist root)
    → 200 {"path": str, "launcher": str}

- **A separate module, for the opposite reason `api/pick.py` is one.** `fs.py` promises read-only
  browsing; this starts a program. `pick.py` is the other extreme: it ignores the allowlist by design
  (picking a directory that is still outside it is its whole purpose), while this endpoint goes
  through `core/paths.py` like every ordinary one - 403 `outside_roots` / 404 `not_found` /
  400 `not_a_directory`;
- **POST, not GET**: it starts a process, so a GET would have to be safe to retry, to prefetch and to
  sit in a cache;
- the launcher is `explorer.exe` / `open` / `xdg-open`, started with `subprocess.Popen`, stdio
  discarded, **no shell** and **no waiting**: `explorer.exe` exits 1 even when it worked, so waiting
  for a return code would turn a success into a reportable failure. A machine with no launcher at all
  (a headless server) answers 500 `no_file_manager` with the path in `params` rather than a silent 200.

### The judgement call: allowlist yes, loopback gate no

§4.11's gate exists for the endpoints that **widen** the allowlist (`POST /api/roots`,
`GET /api/fs/pick`, `mkdir`). This one cannot widen anything: it opens a directory the app is already
browsing, which the same caller can already list, read captions from and write captions into. So it is
**not** behind the gate - and the honest consequence is written into the spec instead of being implied:
with `--host` on an external address, a remote caller can make a file manager window appear on this
machine's desktop. That is a nuisance, not an escalation. (The alternative - hanging it off
`path_config.allowed` - was rejected because the flag is named for *path configuration*, and nothing
here changes what is reachable.)

## 2. The control: `#btn-open-folder`, icon only

- On the grid's own toolbar, **next to refresh**: both act on the listing itself (one re-reads it, the
  other opens it) and neither acts on the selection, which is the contiguous trio after them
  (`test_web_filter.py` pins the exact order);
- **greyed out while no directory is open**, painted in `renderBreadcrumb()` (the one function that
  runs on every navigation and already owns `#btn-parent`'s state);
- a failure is **reported, never swallowed** (`gallery.openFolderFailed` + `{message}`): a headless
  server has no launcher, and a button that appears to do nothing is worse than a sentence;
- it is also a command-palette entry (`browse.openFolder`) calling the same `openInFileManager()` the
  button's listener calls - the "no privileged path" rule.

**Why an icon and not the words** - this was measured, not assumed. With the zh label
(在资源管理器中打开, 139 px wide; ja is longer) the toolbar wraps its three selection actions onto a
second line at a **1280 px window**: the row measures 65 px tall instead of 29.5, and it costs the grid
that 30 px, at exactly the width where the previous round had recorded a one-line row
(`tools/measure_web_layout.py`, `--width 1280`). A 28 px glyph like the refresh action beside it keeps
the row at 29.5 px at 1280 and at 29.5 px from 1600 up. The words survive as the accessible name and
the tooltip (`data-copy-aria` / `data-copy-title`, the globe's pattern), and the palette names it in
words - so nothing is lost except the pixels.

`tools/measure_web_layout.py` gained `#btn-open-folder` in its `SELECTORS` (the toolbar's controls each
get their own box, which is what made the measurement above possible), and
`test_the_probe_measures_the_surfaces_this_repository_keeps_moving` was extended with it.

## Criteria and falsification

| Criterion | Location |
|---|---|
| Allowlisted path opens; the resolved directory (not the sent string) reaches the launcher; the empty path falls back to the first root | `test/test_reveal_api.py` |
| Refusals - outside the allowlist / `..` escape / a file / a missing path - and **nothing is launched** | `test/test_reveal_api.py`, acceptance **A45** |
| Headless machine: 500 `no_file_manager` + the path in `params`; recovery once a launcher exists again | `test/test_reveal_api.py`, acceptance **A45** |
| Fire and forget read from the source: `Popen` (not `run`/`check_call`), `DEVNULL`, no `shell=True` | `test/test_reveal_api.py` |
| Route declared in `api.js`; the button is on the grid's row, icon-only, named through `data-copy-aria`/`-title`; the handler is a named function wired to the button; the disabled state is painted from `state.dir` | `test/test_web_reveal.py` |
| The palette's `browse.openFolder` runs that same function | `test/test_web_palette.py` |
| The toolbar's exact children and their order | `test/test_web_filter.py` |

Falsifications (each really went red, then was restored; the observation, not the intention):

- **Calling the launcher before the path is resolved** → 7 criteria red at once, including all four
  "nothing was launched" assertions - which is what makes the negative half worth having: "403" alone
  would also pass for an implementation that opened a window and then refused.
- `$("btn-open-folder").disabled = false` in `renderBreadcrumb()` → the "greyed out while no directory
  is open" criterion red.
- Removing `relabelComponent(picker)` from `app.js` → two criteria red (see
  [picker-current-location](picker-current-location.md)).

## Known limits

- **Not clicked in a real browser.** The endpoint is exercised through TestClient with the launcher
  replaced (a criterion may not open a window on the machine running it), so "Explorer really appears"
  is reasoned from `launcher_argv` and the platform check, not observed. The layout half *was* measured
  in real headless Chrome.
- Only the directory is opened, not the selected image inside it. That is what was asked for; picking
  one file out of a grid of thumbnails would need a per-image control and a `select` variant.
- The window appears on the machine running the **server**, which is the same machine as the browser
  only because the service listens on loopback by default.

## Related

- Contract: [p0-spec](../p0-spec.md) §2 tree, §4 route table, §4.18, §5 A45
- The other OS-level surface, for contrast: the picker ([ui-picker-and-zoom](ui-picker-and-zoom.md),
  [picker-manual-input-and-mkdir](picker-manual-input-and-mkdir.md))
- The button's own placement rules: [design-principles](../design-principles.md) ("the control lives
  where its effect is visible", "unavailable is shown, with its reason")
