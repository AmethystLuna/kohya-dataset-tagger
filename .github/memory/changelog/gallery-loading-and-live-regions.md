---
name: gallery-loading-and-live-regions
description: 2026-09-19, the gallery thumbnail loading state, the frontend first live regions, and empty states that name the next step
metadata:
  type: topic
---

# Thumbnail loading state, live regions and empty-state next steps (2026-09-19)

Three changes that all answer the same question - "can the user tell what the app is doing?" - each
derived from the copy and UX research collected in [ui-copy](../ui-copy.md).

## 1. A tile in flight says so

`.tile-frame` paints a transparency checkerboard so that a PNG alpha is visible. An `img` that had not
decoded yet showed nothing over it, so **loading, failed and a genuinely transparent image were the same
picture**. The `ThumbLoader` in `gallery.js` now marks the image `.is-loading` while the request is in
flight, `.is-loaded` when the bitmap lands and `.is-error` when it fails; `app.css` turns the loading mark
into a moving placeholder surface and fades the bitmap in. Both the transition and the animation are
switched off under `prefers-reduced-motion`.

The locale packs had carried `gallery.loadingThumbs` ("Loading thumbnails...") since the copy pass and
**no code ever read it** - the planned state had never been wired.

## 2. The first live regions in the frontend

A search for `aria-live` over `web/` returned **zero** hits, so a WD14 run over 200 images, a scaling
export, an autosave failure and every toast were silent. Now:

| Element | Semantics |
|---|---|
| `#toasts` | `role="status" aria-live="polite"`; an error toast carries `role="alert"` so it interrupts instead of queueing |
| `#save-state` | `aria-live="polite"` - autosave results are announced |
| `#caption-warning` | `role="alert"` - the multiline guard reaches a screen reader when it appears |
| `#autotag-progress-fill` and the scale bar | `role="progressbar"` plus `aria-valuemin/max/now`, each named through `aria-labelledby` |
| `#autotag-progress-text`, `#autotag-errors` and the scale equivalents | `role="status"` / `role="alert"` |

The progress bars update `aria-valuenow` from the same painter that moves the fill width, so the exposed
value cannot drift from the pixels.

## 3. Empty states name the next step

Seven empty states were dead ends ("No subdirectories here", "No possibly stale cache"). They now carry
the next step in all three packs, following the template in [ui-copy](../ui-copy.md): `browse.noDirs`,
`gallery.empty`, `batch.scopeEmpty`, `autotag.previewEmpty`, `filter.frequencyEmpty`, `export.empty`,
`cache.empty`.

## Criteria and falsification

`test/test_web_gallery.py` (new, 6 criteria) pins the wiring statically and drives the real `ThumbLoader`
through `test/fixtures/thumb_loader_harness.mjs` (fake DOM, no jsdom): the marker appears, clears on load,
becomes an error on failure, the concurrency cap holds, and a stale generation never starts the next
request.

Falsified rather than assumed: replacing `is-loading` with an unknown class turns the harness red,
deleting the toast live region turns `test_toasts_are_a_live_region` red, and both are green after
restoring.

    .venv\Scripts\python.exe -m pytest test/ -q    811 passed, 2 skipped (2026-09-19)

## What this deliberately does not fix

A frontend audit (Nielsen heuristics plus a keyboard and screen-reader pass, both read from source)
found larger items that need their own change: a failed caption save does not block navigation, a sidebar
tag edit does not refresh the caption cache, batch write and "rebuild all" are silent single POSTs, a
failed load leaves the previous directory data on screen, the scale cancel can stick, the dialogs neither
trap nor restore focus, the frequency table is mouse-only, and the tab strip has no
`aria-selected`/`tabpanel` wiring. They are listed as gap 15 in [current-state](../current-state.md) -
**not** claimed as fixed here.

---

Related: [ui-copy](../ui-copy.md), [i18n](../i18n.md)