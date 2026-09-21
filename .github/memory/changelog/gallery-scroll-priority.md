# The gallery loads what is on screen, not what was queued first

**When to read this**: changing the thumbnail loader or the gallery's observers (web/js/gallery.js),
the prefetch margin, or anything that decides the order thumbnails arrive in.

## Why (2026-09-20)

The user's report: 「gallery加载的过程在滚动之后能不能优先加载当前显示区域」 - while a directory was still
loading, scrolling down left the images they were looking at at the back of the line.

The queue was strictly first-in-first-out. `THUMB_CONCURRENCY` is 6 and the loader prefetches 400px
past the viewport, so one screen already queues far more than six tiles; every tile that came into
view afterwards was appended **behind** all of them - including tiles the user had already scrolled
past. The images on screen were the last to arrive.

## The mechanism: two observers, one ranked queue

| Observer | rootMargin | Job |
|---|---|---|
| prefetch | `400px` | `ThumbLoader.request(img, url, onScreen)` - queue a tile once, when it comes near the viewport |
| viewport | `0px` | `ThumbLoader.prioritize(img, onScreen)` - after **every** crossing, say whether the tile is on screen |

`ThumbLoader` now keeps its queue ranked: requests with `onScreen` are served first, each band
in arrival order. `request()` inserts straight into the right band, so the very first callback
cannot put an on-screen tile behind a prefetch; `prioritize()` re-ranks one that is still waiting -
it promotes a tile that scrolls in and sends one that scrolls back out to the back. A request already
in flight cannot be reordered and is a no-op.

Two details that are easy to get wrong:

- **The prefetch margin must not decide priority.** 400px of margin is what makes prefetching work, and
  it is exactly what makes `isIntersecting` the wrong answer to "can the user see it". The second
  observer therefore has `rootMargin: "0px"`.
- **The two observers can deliver in either order**, so the prefetch callback may ask "is this on
  screen?" before the viewport callback has run. A shared `WeakMap` holds the answer; a missing
  entry means prefetch, and the tile is requested with `onScreen: false`. A `WeakSet` records
  which tiles were already requested, because the observers now keep observing (they used to
  `unobserve` after the first crossing) and a tile crossing the margin twice must not be queued
  twice.

## Criteria and falsification

- `test/fixtures/thumb_loader_harness.mjs` (extended, pure): an on-screen request starts before an
  earlier prefetch; `prioritize(img, true)` makes a waiting tile overtake the prefetches queued
  before it; demoting it puts it back in arrival order; an in-flight image is a no-op.
  **Falsified**: making the ranking ignore `onScreen` turns it red (exit 1).
- `test/fixtures/gallery_scroll_harness.mjs` (new; drives the real `createGallery` against a
  fake `IntersectionObserver` and a fake DOM): exactly two observers exist (400px + 0px) and every
  tile is watched by both; after the first screen queues tiles 0..7 and the user scrolls tiles 4..7
  into view, the next free slot goes to tile 4, not to the earlier prefetch tile 2.
  **Falsified**: dropping `viewportObserver.observe(img)` turns it red; so does the ranking above
  (both exit 1).
- `test/test_web_gallery.py::test_scrolled_into_view_tiles_are_served_before_the_backlog` runs the
  new harness in the suite, and `tools/check_relevant.py` picks both harnesses up from gallery.js.

## Known limits

- **A fake DOM and a fake `IntersectionObserver`, not a real browser.** The harnesses prove the
  re-ranking and the wiring; they do not prove the feel of scrolling a 691-image directory. Same
  evidence class as the other gallery criteria (gap 5 in [current-state](../current-state.md)).
- **`root: null` is "inside the window viewport", not "inside the scrolling `.tab-panels`".**
  The grid scrolls inside `.tab-panels`, so a tile clipped by that panel but still inside the
  window (a narrow band under the header) counts as on screen. The prefetch observer already used the
  same root, so this is not a regression - it is why the viewport test is an observer rather than a
  `getBoundingClientRect` measurement, which would have to name the real scroller.
- The folder nine-grid (§4.13) shares the loader; folder_grid_harness.mjs deliberately has no
  `IntersectionObserver`, so those cards still render immediately and did not change.

---

Related: [thumbnail-pipeline](../thumbnail-pipeline.md), [gallery-loading-and-live-regions](gallery-loading-and-live-regions.md)
