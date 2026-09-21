---
name: zoom-image-viewer
description: 2026-09-18 zoom-preview images gained wheel zoom and drag pan, making it a complete image viewer control
metadata:
  type: topic
---

# A complete image viewer for the zoom preview (2026-09-18)

The user asked: **"the image itself needs wheel zoom and a grab handle; then it is basically a complete image-viewer control"**.

## Why it was not enough before

`.zoom-img` was just a `<img>` with `max-width/height: 100%`, always shrunk to fit; and the zoom preview uses the
1600px preview thumbnail (it does not load the original), so there was no way to see detail short of switching images.
It was missing exactly the two most basic things a viewer has: zoom and pan.

## The approach: move the bitmap, not the layout

There are only three numbers of state — `scale` / `offsetX` / `offsetY`; `paintView()` writes
`translate(...) scale(...)` onto the bitmap (an empty string when `scale === 1 && offset === 0`, i.e. no transform).
Layout and paging are unaffected; paging is still just a `src` swap.

- **Wheel zoom around the cursor**: `zoomAt()` uses `t' = d - (d - t) * (s'/s)` (`d` = the cursor's offset from the stage center),
  so the pixel under the cursor does not move when zooming. The `wheel` listener must be `{ passive: false }` and call
  `preventDefault()` in the handler — otherwise the browser ignores the cancellation and the page scrolls along.
- **Drag pan**: `pointerdown/move/up` + `setPointerCapture`, with the `is-panning` class switching the cursor to `grabbing`;
  when `scale <= 1` (the image still fits) there is no drag, because there is nothing to reveal. `clampOffset()` clamps the
  offset by "scaled size vs stage size", so the image cannot be dragged completely out of sight. Ending a drag briefly
  suppresses the backdrop's click-to-close — one drag should not close the viewer.
- **Reset**: double-click toggles between "fit the window ↔ 2×"; keyboard `+` / `-` / `0`; **every image switch and close calls
  `resetView()`** — a new image should not inherit the previous one's zoom position.
- The limits are `MIN_SCALE = 1` / `MAX_SCALE = 8`; `WHEEL_ZOOM = 0.0015` with `Math.exp` makes each notch
  geometric (zoom in and out are symmetric).
- The bitmap gets `draggable = false`, and CSS gets `-webkit-user-drag: none` + `user-select: none`:
  otherwise press-and-drag triggers the browser's native "drag image", fighting the grab handle.
- `.zoom-stage` changed from `overflow: auto` to `overflow: hidden` + `touch-action: none`: the bitmap moves by
  transform, the stage should not be a scrollable container, and a touch drag should not turn into page scrolling.

## Criteria

| Criterion | What it checks |
|---|---|
| `test/test_web_zoom.py::test_image_is_a_viewer_with_wheel_zoom_and_drag_pan` | the wheel listener carries `passive: false`; all three pointer events are present; the transform has translate/scale; MIN/MAX exist; `show()` calls `resetView()`; CSS has `touch-action`, `overflow: hidden`, and `grab`/`grabbing` cursors |
| `test/fixtures/zoom_harness.mjs` | really runs zoom.js: a wheel zoom at the stage center **produces no pan**, the `is-zoomed` class; dragging 50/20 gives `translate(50px, 20px)` and `is-panning` disappears after pointerup; double-click resets; `+`/`0` keyboard zoom; **after paging the transform must be an empty string** |

**Falsification** (all actually done):

- `{ passive: false }` changed to `true` → the static criterion goes red (the harness stays green — it calls the handler directly, bypassing passive);
- delete `resetView()` from `show()` → the static criterion and the harness go red **at the same time** ("the new image inherited the old zoom").

## Fix: rapid clicks on the nav arrows were misread as double-click zoom (same day)

What the user reported: "clicking the navigation arrows quickly triggers double-click zoom".

The cause is direct: the arrows are **children** of `.zoom-stage`, so when the arrows are clicked twice in quick
succession, the `dblclick` after the second click bubbles from the button to the stage and is caught by "double-click
toggles between fit-the-window / 2×" — so "next, next" becomes a zoom.

The fix: add the same exclusion in `onDoubleClick` as in `onPointerDown`; when `event.target.closest("button")`
hits, return immediately; only the image and the blank background toggle the zoom.

The harness's fake DOM gained the matching `closest("button")` (walking up the parent chain, returning only when it hits a
`<button>`). It **recognizes buttons only**; other selectors still return null all the way, so the `.chip` / `.suggest-item`
paths in `tags.js` behave unchanged. New criterion: when the `dblclick`'s `target` is an arrow, `transform` must stay an empty string.
**Falsification**: delete that guard → the static criterion and the harness go red at the same time ("a double-click on a paging arrow zoomed the picture").

## Not verified

**The feel in a real browser has not been tried**: whether `MAX_SCALE = 8` is enough, whether one wheel notch of `0.0015` is the
right speed, the resistance when clamping at the edge, and how `touch-action: none` actually behaves on touch devices.
(The Chrome MCP manual pass was not rerun this round.)

## Related

- Paging arrows: [zoom-nav-arrows](zoom-nav-arrows.md)
- The original zoom-preview implementation and real-browser verification: [ui-picker-and-zoom](ui-picker-and-zoom.md)
