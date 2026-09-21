---
name: ui-picker-and-zoom
description: three WebUI experience changes on 2026-09-18: the graphical directory picker (§4.12), tag diff colors, and the double-click zoom preview
metadata:
  type: topic
---

# WebUI experience changes (2026-09-18)

The user raised three rounds in one day, all of the "it is visible but not clickable" kind:

1. "It shows that a directory is currently open, but I cannot click it to switch the selection; that is very counter-intuitive and very annoying"
2. "Also improve the tag diff display: additions boxed in green, deletions in red"
3. "Images in the center workspace should be double-clickable to zoom preview + show tags side by side + edit"

## 1. Graphical directory picker (§4.12)

Before the change, **three places that showed the current dataset directory were not clickable**: the highlighted root row in
the left panel (clicking only reopened the same directory), the export panel's "export directory" row (read-only text), and the
breadcrumb's current-position segment (a read-only `<span>`). Switching datasets meant pasting an absolute path.

Why it was a paste box at first: `p0-spec` §4.11 had explicitly rejected an endpoint to "list any directory", on the grounds that it
would make the allowlist void. The user then asked "what is the fallback for a non-loopback address", and this set of boundaries was settled:

- New `GET /api/fs/pick`: **lists directories only** (no files, no counting, no reading contents), and **may go outside the allowlist** —
  because "add a root" is itself choosing a directory outside the allowlist;
- **Loopback gate**: when `--host` is not a loopback address, the picker and the four path-change endpoints (`POST/DELETE /api/roots`,
  `POST/DELETE /api/autotag/model-roots`) return 403 **together**. Gating only the picker and not roots is self-deception:
  `add_root` can already add any existing directory to the allowlist and then read and write it;
- Fallback switch `--allow-remote-path-config` (or `KOHYA_TAGGER_ALLOW_REMOTE_PATH_CONFIG=1`), off by default;
- **The paste box stays**, as a fallback under any binding; when unavailable the button is **greyed out with the reason stated**, not silently removed.

A browser cannot give a native directory picker (the File System Access API only hands out handles, and drag-and-drop only gives relative entries),
so "graphical" can only be implemented by listing directories server-side — that is a design constraint, not laziness.

## 2. Marking both sides of a tag diff

Before, the `before` cell was **one whole block of plain text**: a deleted tag was simply not visible in the UI, and it is exactly
"what you lose when you press the button"; the CSS rule `.diff .del` was even a dead rule with **no JS ever calling it**.

Now both sides share one `renderCaptionDiff` (exported from `autotag.js`): additions boxed green, deletions boxed red + struck through;
an empty cell is marked "none", and **only when there originally was a caption and it really does get cleared** does that side box red
(both sides empty and still red = a false positive).

## 3. Double-click zoom preview (`web/js/zoom.js`)

The gallery's `dblclick` listener used to only `preventDefault()`. Now double-clicking a tile opens a full-screen modal:
the left is the `THUMB_SIZE.PREVIEW` (1600px) large image, the right is a second instance of **the same `tags.js` editor**.

- Reuse `tags.js` rather than writing another one: the editor is fully parameterized by DOM element, so the second instance automatically inherits
  autocomplete, save-on-blur, and **deleting the text encoder cache on every caption write** (invariant #1);
- On open it first `flush()` + `disable()`s the sidebar editor, and on close it `load()`s again for the last image seen —
  the same caption is not allowed to have two active copies (they would overwrite each other);
- `← →` page within the **currently visible list**, Esc/the backdrop/the close button exit; while typing in the input the arrow keys belong to autocomplete;
- Both closing and switching directories call `removeAttribute("src")` to release the bitmap; when switching directories, `dismiss()` closes it outright
  (the path list is already invalid, so it must not call back into the sidebar).

> Follow-up (same day): the "previous/next" in the title bar became gallery-style navigation arrows on either side of the image,
> see [zoom-nav-arrows](zoom-nav-arrows.md).

## Criteria and falsification

| Criterion | Falsification (confirmed red) |
|---|---|
| `acceptance` A33/A33b/A33c (picker and gate) | short-circuit the gate to always true → A33b red |
| The picker lists directories only | make it list files too → A33 red |
| The three frontend entry points are wired | remove the left-panel entry → the wiring criterion red |
| Headless harness `picker_harness.mjs` | clicking a directory does not navigate → red |
| Marking both sides of the diff | remove the deletion marker / remove the green box border → red |
| Headless harness `zoom_harness.mjs` | (really runs zoom.js + tags.js: opening loads the image and tags, paging swaps the image, Esc closes and releases the bitmap) |

## Real-browser verification (2026-09-18, local Chrome + chrome MCP plugin)

The user pointed out that the local Chrome has an MCP plugin and can be reached locally, so this layer was added. Measured (real data
the reference dataset, 1653 images, 219 subdirectories):

- **Zoom preview**: opened with `Z` (see below), 1600px large image on the left; on the right 45 tag chips + an input +
  the "45 tags total" count + caption path + bottom hint, with the `1 / 3` count and "previous/next/close" all present;
- **Directory picker**: clicking "Choose…" pops it up, showing the current position, quick jumps to `C:\ D:\ E:\ F:\ Y:\ Z:\` and the home directory,
  and "Up one level / Use this directory / Cancel";
- **Diff colors**: in the `1.jpeg` row of one subset directory, `bangs` in the original caption column is **boxed red + struck through**,
  and `pelvic curtain lift` in the new caption column is **boxed green** — both sides confirmed;
- Console **0 exceptions** (only a `favicon.ico` 404, which had been there all along).

Also found and fixed along the way: **"double-click only" is a dead end for keyboard users**, so the gallery gained a `Z` key entry and the hint copy was
synced to "double-click (or Z) to zoom preview"; the criterion is `test/test_web_zoom.py::test_gallery_also_has_a_keyboard_route`.

The tool's boundaries (written down so we do not trip on them again): this MCP **has no double-click primitive** (two CDP clicks do not synthesize a `dblclick`),
and `chrome_inject_script` cannot reach the target tab either (it injects by "the active tab of the focused window", and this machine has two windows);
moreover `chrome_navigate` **opens a new tab** every time, so "the current tab" drifts between tools — when debugging this kind of problem,
first confirm you are clicking/screenshotting the same tab. **Starting the service on a different port bypasses Chrome's HTTP cache completely** (a different origin).

**Closing a tab must use an exact id; do not infer a window id from the tab list with a regex** (really hit on 2026-09-18):
in `get_windows_and_tabs`'s output both windows contain the keyword, and a lazy match like `/Window (\d+):[\s\S]*?keyword/`
lands on the **first** window; the result was that it should have closed its own new window but closed the user's main window (15 tabs,
including this session's WebUI), and Chrome then exited entirely. Use the Tab ID returned by `chrome_navigate`, or the windowId you noted when opening the window
yourself. This has the same root as global repository rule #1: for a destructive operation, confirm the **exact** target first, do not guess.

## Related

- Contract: [p0-spec](p0-spec.md) §4.12, §5's A33
- Tags and caches: [wd14-tagger](wd14-tagger.md), [encoder-cache-invalidation](encoder-cache-invalidation.md)
