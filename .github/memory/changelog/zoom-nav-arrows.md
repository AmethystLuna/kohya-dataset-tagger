---
name: zoom-nav-arrows
description: 2026-09-18 zoom-preview paging changed from title-bar buttons to gallery-style navigation arrows on either side of the image
metadata:
  type: topic
---

# Zoom-preview paging arrows (2026-09-18)

The user asked: **"change the zoom-preview UI's previous/next controls into navigation arrows on either side of the image area, like a gallery"**.

## Before

`zoom.js` put three `.btn-mini`s — "Previous / Next / Close" — together into the title bar `.zoom-head`:

    Zoom  path…   3 / 1653   [Prev][Next][Close]

The problem was not only aesthetics:

- the controls were far from the thing they change (the image), so the eye had to run back and forth between the title bar and the image;
- the title bar was already occupied by the path (`.zoom-path` capped at 42% width), so in a narrow window the buttons got squeezed easily;
- the standard gallery / lightbox interaction is **left and right arrows floating on either side of the image**, which is what users expect anyway.

## Now

- The two arrows are produced by `navButton()`, classed `zoom-nav zoom-nav-prev` / `zoom-nav zoom-nav-next`,
  and **attached to `.zoom-stage` (the image area)** rather than going into `.zoom-head`; the close button stays in the title bar.
- The arrow itself is a **purely decorative arrow glyph** (`‹` / `›`, U+2039 / U+203A); the accessible name comes from
  `aria-label` + `title`, and the copy is still `strings.js`'s `zoom.prev` / `zoom.next` ("Previous" / "Next").
  That way `test/test_web_contract.py`'s "CJK may only appear in strings.js" is not broken by the glyph,
  and screen readers do not lose information.
- CSS: `.zoom-stage` gains `position: relative` as the positioning context; `.zoom-nav` is absolutely positioned,
  `top: 50%` + `translateY(-50%)`, translucent dark background, brightened on `hover` / `:focus-visible`;
  `.zoom-nav-prev { left: 10px }` / `.zoom-nav-next { right: 10px }` hug either side.
- With only one image the arrows are **hidden entirely** (`hidden = !many` in `paintHeader()`) rather than left disabled —
  with nothing to page to they are just noise (the global `[hidden] { display: none !important }` applies).
- `zoom.hint` is now "← → or the arrows on either side switch images…"; the keyboard `←` / `→` path is unchanged.

## Criteria

| Criterion | What it checks |
|---|---|
| `test/test_web_zoom.py::test_paging_controls_are_arrows_on_the_image_stage` | prev/next are in `stage.append(...)` and not in `head.append(...)`; `navButton` reuses `zoom.prev` / `zoom.next`; in CSS the stage is `position: relative`, `.zoom-nav` is `absolute`, and one hugs each side |
| `test/fixtures/zoom_harness.mjs` | the arrows are found with `byClass(stage, ...)`, not found in `.zoom-head`, `aria-label` is correct, clicking really pages, and `hidden` with a single image |

**Falsification** (all actually done): remove `prevButton` / `nextButton` from `stage.append(...)` →
the static criterion goes red, and putting them back goes green; the harness also went red once this round —
the class name was once written `zoom-prev` instead of `zoom-nav-prev`, so `byClass(stage, "zoom-nav-prev")`
found nothing, which is exactly how it caught the real bug "the CSS selector never matches".

## Not verified

This round only has the **headless harness + static CSS criteria**. How the arrows look in a real browser —
the 10px inset, how much they overlap the image edge, whether they block the image in a narrow window — **has not been looked at in a browser**;
the Chrome MCP manual pass in [ui-picker-and-zoom](ui-picker-and-zoom.md) was not rerun.

## Related

- Follow-up (same day): the bitmap itself gained wheel zoom and drag pan → [zoom-image-viewer](zoom-image-viewer.md)
- The original zoom-preview implementation and real-browser verification: [ui-picker-and-zoom](ui-picker-and-zoom.md)
- Copy centralization / build-free contract: `test/test_web_contract.py`
