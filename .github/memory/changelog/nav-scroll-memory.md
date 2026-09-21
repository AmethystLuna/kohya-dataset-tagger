---
name: nav-scroll-memory
description: 2026-09-19 scroll memory for directory navigation (§4.15): after a list is rebuilt it either stays on "the folder that just appeared" or stays where it was last left — four components share one implementation, and no one gets to write a second copy
metadata:
  type: topic
---

# Scroll memory for directory navigation (2026-09-19)

The user's words: **"the path-navigation experience of the various components needs optimizing; going back up does not leave the current list's scroll area on the folder you are in / have chosen"**.

## 1. Why it bounces back to the top

Every list in this frontend is **rebuilt wholesale** on navigation (`replaceChildren`), and the scroll container goes back to the top with it.
The left panel's list has **219** entries (measured on the reference dataset), so after "go up" the folder you just entered is not in the viewport and
you have to scroll to find it again. The center workspace's folder cards (§4.13) and the §4.12 picker are the same story.

## 2. The rule: reveal takes priority over "where we last stopped"

New module `web/js/navscroll.js` (leaf layer: touches no DOM, makes no requests, contains no copy), shared by four consumers:

| Export | Semantics |
|---|---|
| `childOnPath(parent, child)` | the **first segment** of `child` under `parent` ("the subdirectory that just appeared"). Returns `null` when not a strict descendant (going down, refreshing, and same-level jumps all take that branch). The result is rebuilt with `parent`'s own separator and drive-letter case so it matches the backend's `dirs[].path` **character for character** (on Windows `F:\a` and `F:/a` must be the same row) |
| `samePath(a, b)` | equality ignoring separator and case; **two empty strings are not the same directory** |
| `createScrollMemory(limit=200)` | directory → last `scrollTop`. For an unknown directory `recall()` returns **`null`** (`0` is a legal position, so a falsy test cannot be used); above the limit the oldest entry is dropped |
| `revealRow(scroller, row, {align})` | scroll a row into **the specified** scroll container. `center` (default) centers it — the neighboring rows give context; `nearest` moves the least (for the keyboard cursor). When geometry cannot be measured it returns `false` **and does not move the container**, and the caller falls back to the remembered position accordingly |

The rule at navigation time in one sentence: `reveal = childOnPath(new dir, origin dir)`.

- `reveal` is non-empty and that row is in the list → scroll to it and mark it with the `is-revealed` highlight;
  the position has to **explain itself**, or "stopping in the middle" looks like a random jump.
- Otherwise → restore **where that directory was last stopped**: a refresh or a jump back to the original directory also no longer bounces to the top.
- **reveal must take priority over memory** (the order inside `restoreScroll` is this requirement itself).

### Why not `scrollIntoView`

It scrolls **ancestor containers** along with it (including the page itself). Here only the specified scroll container may move: `revealRow` computes
the target `scrollTop` from `getBoundingClientRect()` itself and clamps it by `scrollHeight - clientHeight`.
`gallery.js`'s old `scrollIntoView({block:"nearest"})` for the keyboard cursor was changed too —
`{align:"nearest"}` keeps the "move the least" feel but does not scroll the page along with it.

## 3. The four attachment points

| Component | Attachment point | Highlight |
|---|---|---|
| Left-panel directory list | `app.js:openDir` → `renderDirs(state.dirs, reveal)` + `restoreScroll()` | `.list button.is-revealed` |
| Center folder cards | `gallery.revealDir(path)` → `revealRow(container, card)` (returns `false` when it cannot measure) | `.dir-card.is-revealed .dir-thumbs` |
| Breadcrumb dropdown | `paintCrumbMenu` scrolls to the current entry **when it opens** | reuses `.crumb-item.is-current` |
| Directory picker | `picker.js:navigate(path, {from})` + its own `createScrollMemory()` | `.picker-dir.is-revealed` |

Two side notes:

- The in-place re-render on language switch goes through `repaintDirs()` instead: redrawing no longer bounces the list back to the top;
- The left panel and the center each keep their own position (`dirScroll` / `gridScroll`): **either side may be the column the user is reading**
  (the root level has only folder cards, other levels only image tiles).

**Deliberately not done**: the left panel's **root-directory list** gets no reveal. It repaints on every `openDir` (swapping only the current
highlight), and scrolling to the current root while the user is browsing another root would drag them away.

## 4. Criteria and falsification

`test/test_web_navscroll.py` (12 checks): module uniqueness, **`.scrollIntoView(` must not appear**, the order in
`openDir` (record the old position before making the request), `renderDirs` marking and handing over that row, `repaintDirs`,
**reveal taking priority over memory**, the breadcrumb dropdown, `gallery.revealDir`, picker wiring, CSS highlight,
plus `test/fixtures/navscroll_harness.mjs` which really runs the module (path rule including the segment-prefix trap
`/data` vs `/datax` and Windows separators / memory bounds / centering and clamping at both ends / returning `false` when nothing can be measured).

The behavior criteria were extended inside the **existing** harnesses rather than starting a new set: `picker_harness.mjs` ("go up" reveals the origin
directory, a non-upward jump restores the position it left from, entering a directory and coming back from above centers it), `folder_grid_harness.mjs`
(`revealDir` marks and centers, returns `false` and does not move the container when it cannot measure).

Falsification (all really done, confirmed red, then restored):

| Break | Criterion that went red |
|---|---|
| `childOnPath` returns the **last segment** of the path instead of the first | `navscroll_harness` |
| In `restoreScroll`, let memory override reveal | `test_web_navscroll.py` two checks |
| `revealDir` does not mark `is-revealed` | `folder_grid_harness` + the wiring criterion |
| Picker "go up" without `{from}` | `picker_harness` + the wiring criterion |

## 5. Not verified

**How it looks in a real browser has not been measured** (the same class as gap 5 in current-state): the actual landing point after centering with 219
entries, and whether the `is-revealed` highlight is conspicuous enough on a dark theme, are held up by nothing but the harness and CSS rules.
The harness feeds it **fake geometry** (`getBoundingClientRect` computed by the harness from content offsets), which can prove the algorithm and the
wiring but not the final picture in the browser.

## Related

- Contract: §4.15 ([p0-spec](../p0-spec.md))
- Scrolling in the same left panel: [changelog/dir-list-layout](dir-list-layout.md) (that one solved "the list scrolls itself")
- The four consumers: [changelog/ui-folder-grid-preview](ui-folder-grid-preview.md) (§4.13),
  [changelog/picker-manual-input-and-mkdir](picker-manual-input-and-mkdir.md) (§4.12)