# A caption editor that does not require leaving the gallery

**When to read this**: changing the right column's layout or its tab strip, the caption dock, the
editor's empty state in `web/js/tags.js`, or anything that decides which panel is on screen.

## Why (2026-09-20)

The task said "one image's tags can only be edited inside the zoom overlay". Checked against the code,
that is not what shipped: `app.js` wires the gallery's `onActiveChange` to `activateImage()`,
which loads the single tag editor of the right column's **Tags tab**. No modal was ever required.

The real gap was the *tab*: the editor lived inside the tab strip, so the moment the user was on Filter,
Tagger, Cache, Batch, Export or Scale there was no editor on screen - and the only way back to one was a
double-click into the modal zoom view, which covers the grid. Captioning is this tool's main loop, so the
editor had to stop being a place you visit.

## The decision

- **The editor is pinned, not tabbed.** `#caption-dock` sits at the bottom of the right column,
  outside a new `.tab-panels` wrapper; the batch/export/scale panels are appended to that wrapper (not
  to `.panel-right`) so the dock stays the last thing in the column. The **Tags tab is gone** - its
  whole content moved into the dock, so keeping the button would have left an empty panel, and
  `test_web_contract` requires `tabs == panels`. The Filter tab becomes the default.
- **One live editor per surface.** `createTagEditor` is parameterised by DOM elements, so the dock is
  the *same instance* `app.js` already built - the only new option is `form: $("caption-form")`.
  The frontend holds exactly two instances: this one, and the zoom overlay's (`zoom.js`), which flushes
  and disables this one while it is open. No third surface: two live copies of one caption overwrite each
  other.
- **Nothing active shows a short empty state, not an empty form.** `disable()` hides the control block;
  the dock's status line paints `caption.noActiveImage` ("Click an image in the gallery first") - the
  guidance string the disabled form already carried, so no new copy. The status line lives *outside* the
  block that gets hidden, which is what keeps a failed load visible.
- **The status line is held as data.** `activeMeta` + `paintActiveMeta()` replaced three direct
  `textContent =` writes, and `relabelAfterLocaleChange()` repaints it. It is painted at render
  time, so without that registration a language switch left it (and the empty state it doubles as) in the
  old language - a pre-existing staleness that a permanent dock would have put on screen for good.
- **The dock can be folded away** (`#btn-caption-toggle`, remembered in `localStorage`). A permanent
  dock takes column height from every panel, and the measurement below says how much. The fold is a class
  on the dock, deliberately not `[hidden]`: that attribute belongs to `enable()`/`disable()`, so
  a fold expressed with it would pop back open on the next image.
- **Copy**: two new labels (`caption.collapse` / `caption.expand`, painted from the folded state and
  therefore also registered for relabel); the now-dead `gallery.noActive` and `tab.tags` were
  deleted from all three packs.

## Evidence

**A real browser, measured** (ad-hoc probe, not committed: an in-process `http.server` serving `web/`
plus canned `/api/*` JSON, the real `index.html` loaded in a same-origin iframe, driven by headless
Chrome 153.0.8010.48 and dumped with `--dump-dom`). Column height, the panel region and the dock, at
three viewport heights, with one image active and its caption loaded:

| viewport | column | tab panels | dock (expanded) | dock (folded) | panels (folded) |
|---|---|---|---|---|---|
| 900 px | 835 | 422 | 346 | 129 | 639 |
| 720 px | 655 | 271 | 317 | 129 | 459 |
| 600 px | 535 | 211 | 257 | 129 | 339 |

- **The editor is on screen on every tab**: switching through filter / autotag / cache / batch / export /
  scale, the dock's height stayed > 0 and `#caption-form` stayed visible - six tabs, not one.
- **Nothing active**: `#caption-form` hidden and the status line reading the empty state, before any
  tile was clicked.
- **It follows the active image**: after one tile click the block is visible, the status line reads
  `1024x768 / 1.18 MB / RGB` and the chips are that caption's two tags.
- **The fold survives an image change**: after folding, clicking another tile leaves `display: none` on
  the block while `enable()` has already cleared `hidden` - the two mechanisms are independent, as
  intended.
- **Language switch**: the toggle label goes from the Chinese one to `Expand editor`, and the status
  line is repainted rather than reset by `applyCopy()`.
- **Remembered**: `localStorage["kdt.captionCollapsed"] = "1"`, and after an iframe reload the dock
  starts folded.

**Headless behaviour** - `test/fixtures/caption_dock_harness.mjs` runs the real `tags.js`,
`gallery.js` and `api.js` over a fake DOM: a freshly built editor hides its control block; loading
the active image shows it; `disable()` hides it again; the gallery drives the editor through a **tile
click** and through **arrow keys** (the grid cursor walks image to image and the caption under the editor
changes with it); navigating away returns to the empty state; and the write goes out as `{image, tags}`
through `api.putCaption`, with a `cache_remove_failed` answer surfaced to `onError` instead of
swallowed (invariant number one from the frontend side - the endpoint's own cache deletion is covered by
`test_captions.py` / `test_backup_overwrite.py`).

**Static** - `test/test_web_caption_editor.py` (11 criteria): the editor's elements are inside the dock
and the dock is not a `.tab-panel` (and carries no `hidden`); the tab switcher only hides
`.tab-panel`, while `tabs == panels` and there is no Tags tab; the three appended panels go to
`.tab-panels`; the column is a flex column with a bounded, self-scrolling dock; `createTagEditor({`
appears exactly once in `app.js`, once in `zoom.js` and twice in the whole frontend; the empty state
hides the block and keeps the status line outside it; a failed load records itself on that line;
`relabelAfterLocaleChange()` repaints both the status line and the toggle label; the empty state and the
two toggle labels exist in all three packs and the two dead keys do not.

**Falsified 17 ways** (break -> red -> restore -> green, each observed). What turned red: the dock marked
`data-panel` (2 criteria), the tab switcher widened to `#caption-dock` (1), one appended panel sent
back to `.panel-right` (1), the dock's `max-height` removed (1), a third `createTagEditor` call
(1), `disable()` no longer hiding the block (**static + harness**), `enable()` no longer showing it,
the failed-load record replaced (1), `paintActiveMeta()` dropped from relabel (1), `gallery.noActive`
put back into the en pack (1), `setActive` no longer announcing the active image (**harness**),
`cache_remove_failed` swallowed (**harness**), `paintCaptionToggle()` dropped from relabel (1), the
folded rule changed to `display: block` (1), the toggle's click wiring removed (1), the remembered fold
no longer applied (1), and one pack renaming a new label (1).

**One criterion this caught**: the fold criterion originally asserted only that the CSS *selector* existed.
Changing its declaration to `display: block` stayed **green** - a criterion that tested nothing. It now
reads the declaration through the same `rule()` helper the resizer criteria use, and was re-falsified.

## Known limits

- The browser probe answered `/api/*` from canned JSON (thumbnails were a 2x2 PNG), so no real caption
  was written over HTTP in a browser. The write path is covered headlessly and by the backend's criteria.
- The probe is not committed and was run once on this machine; the numbers are that run, at a 1280 px wide
  viewport where the right column keeps its default 380 px.
- A folded dock starts **expanded** for a fresh browser profile, and the fold is per browser
  (`localStorage`), not per dataset.
- Tab order and focus were not driven with the keyboard in the browser. The tab strip's roving tabindex is
  unchanged, and the dock is a sibling of the panels, so a keyboard user reaches it after the open panel.

---

Related: [i18n](../i18n.md), [ui-copy](../ui-copy.md), [changelog/zoom-image-viewer](zoom-image-viewer.md),
[changelog/caption-write-safety](caption-write-safety.md), [changelog/shared-scope-control](shared-scope-control.md)
