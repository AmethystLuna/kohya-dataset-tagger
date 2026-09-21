---
name: resizable-panels
description: 2026-09-18 layout controls: both sidebars are drag-resizable (flex + CSS variables, localStorage memory, keyboard accessible)
metadata:
  type: topic
---

# Sidebars drag-resizable (2026-09-18)

The user asked: **"optimize the layout controls; the sidebars should be manually resizable"**.

## The constraint before

`.layout` was a CSS grid, with both sidebar widths hard-coded in the stylesheet:

    grid-template-columns: 260px minmax(0, 1fr) 380px;

When a directory name was long the left column was not enough to read it, and wanting to give the
right tag panel more room was just something to live with — both sides were fixed.

## Now: one flex row + two separators

    [left] [handle] [center] [handle] [right]

- `.layout` became `display: flex; gap: 0`; the two 10px `.resizer`s exactly fill the gap the old grid had;
- sidebar widths come from the CSS variables `--left-w` / `--right-w` (`:root` gives the defaults 260 / 380, and while dragging JS writes
  them on `.layout`). `.panel-left { flex: 0 0 var(--left-w); width: var(--left-w) }`,
  the middle `.panel-center` is `flex: 1 1 auto; min-width: 0` — **dragging the left sidebar yields only the gallery**, the right sidebar does not move;
- handles: `cursor: col-resize`, `touch-action: none` (so a touchscreen does not treat it as scrolling),
  `role="separator"` + `aria-orientation="vertical"` + `tabindex="0"`; once focused `←/→` step 16px,
  `Home` or double-click resets, and `aria-valuenow/min/max` follow along.

`web/js/resizer.js` separates the pure computations (`clampWidth` / `dragWidth` / `keyboardWidth` /
`parseStoredWidths`) from the factory `createResizers`: drag direction, clamping, and keyboard stepping can all be
judged in an environment without a layout engine. Widths are stored in
`localStorage["kohya-dataset-tagger.layout"]`, and a bad save, no save, or an unavailable `localStorage`
(private mode) all fall back to the defaults.

## Clamping: beyond the hard limit there is also an "effective max"

Each side has a hard limit in `RESIZER_LIMITS` (left 200–560, right 280–680), but how far it can really be
dragged also subtracts the other side, the two handles, and the gallery's 320px minimum width:

    effectiveMax(side) = layout.clientWidth - otherWidth - handleWidth - CENTER_MIN_WIDTH

Loading a save and a window `resize` both re-clamp, rather than squeezing the gallery to 0.
(We hit this once during development: a flex child like `#dir-list` with `flex: 1 1 auto` turns its content height
into a shrink weight; the counterpart of the same pitfall here is — clamping must include the width of both
handles, or 20px always ends up squeezed into the gallery.)

## Criteria

| Criterion | Falsification (done/doable) |
|---|---|
| `test_web_resizer.py` (7 checks: separator/tabindex, app.js wiring, flex+variables, `.resizer` styles, JS/CSS defaults agree, copy key, headless behavior) | break a selector / drop `tabindex` → red |
| `test/fixtures/resizer_harness.mjs` (fake DOM really runs `createResizers()`: drag direction, effective max, keyboard stepping, persistence, bad-save fallback) | drag direction reversed → red; drop clamping → red |

A real browser (CDP, `Input.dispatchMouseEvent` through the real input pipeline) measured at 1440×900:

    BEFORE                left 260  right 380  center 727
    drag left handle +80  left 340  right 380  center 647   stored {"left":340,"right":380}
    right panel ArrowLeft left 340  right 396  center 631
    refresh               left 340  right 396 (restored from localStorage)
    double-click left     left 260  right 396 (only the double-clicked side resets)

## Related

- Wrapping/scrolling fix for the same left column: [dir-list-layout](dir-list-layout.md)
- Static assets `no-cache`: section 3 of the same changelog (when the backend does not change its cache policy, a refresh after a change still shows the old UI)
