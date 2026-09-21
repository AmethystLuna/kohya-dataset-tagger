---
name: dir-list-layout
description: 2026-09-18 left-panel directory-browsing layout: the count no longer wraps (nowrap + non-shrinkable), the lists scroll independently while the header and hint stay fixed; and static assets get no-cache (a refresh after a change no longer serves old CSS)
metadata:
  type: topic
---

# Left-panel directory-browsing layout (2026-09-18)

The user's words: **"optimize the left directory-browsing layout; the 'n images' text on the right must not wrap"**.

Measured on real data (the reference dataset, 1653 images, 219 subdirectories, local Chrome 1440×900):
before the change the count on a directory row whose name is long enough to be truncated was split into "6" + "images" on two lines;
after it all 219 rows are single-line.

## 1. Why the count wrapped

`.list .li-count` originally had only `color / font-size / margin-left: auto`. A directory row is flex,
and the name `.li-name` had `overflow: hidden`, but the count opposite it had **no non-shrinkable guarantee**:
a flex item's automatic minimum size is its **min-content width**, and Chinese can wrap **between any two
characters** (a space is a break point too), so the min-content is only as wide as one digit — once the name gets
long, the count is squeezed onto two lines. This is not theoretical, it is the row you can see in the screenshot.

The fix gives both together (either one alone still wraps):

```css
.list .li-count { flex: 0 0 auto; white-space: nowrap; text-align: right; }
.list .li-name  { min-width: 0; flex: 0 1 auto; }   /* the name is the only side that shrinks */
```

`min-width: 0` is the key to whether the ellipsis can appear: besides `overflow: hidden`, flex's automatic
minimum size has to be lifted too, or the name would rather not shrink and push the count onto a new line.
Row alignment also changed from `baseline` to `center` — a block with `overflow: hidden` degrades its baseline to the
bottom margin edge, which is not worth betting on in a single-line row.

## 2. The whole left panel scrolled, and the header went with it

`.panel` is uniformly `overflow: auto`, so with 219 subdirectories **scrolling past row 20 moved the "root
directories" list, the "go up" button, and the bottom hint all out of the viewport**, and going back up
required scrolling to the top first.

Now the left panel is a column-direction flex, with a fixed header + two independently scrolling lists:

```css
.panel-left { display: flex; flex-direction: column; overflow: hidden; }
.panel-left > #root-list { flex: 0 1 auto; max-height: 40%; min-height: 0; overflow-y: auto; }
.panel-left > #dir-list  { flex: 1 1 0; min-height: 0; overflow-y: auto; }
.panel-left > .hint      { flex: 0 0 auto; }
```

**`#dir-list`'s flex-basis must be `0`, not `auto`.** The first version wrote `flex: 1 1 auto`, so the content
height of the 219 rows became a shrink weight (basis × shrink weighting), almost all of the negative free
space was dumped on it, and **the single-row root directory list was squeezed into a 2px sliver** (the thin
blue line in the screenshot). With `flex: 1 1 0` basis no longer takes part, so the root directory keeps its content
height and the subdirectory list eats the remaining height.

## Criteria

`test/test_web_dirlist.py` (statically checks CSS rule bodies, the same approach as the side-by-side
criteria in `test_web_zoom` — Node has no layout engine). Five of them:

| Criterion | Falsification (done, confirmed red) |
|---|---|
| `.li-count` has nowrap + `flex: 0 0 auto` | remove nowrap, `flex: 0 1 auto` → red |
| `.li-name` clips + ellipsis + `min-width: 0` | (same file as the row above) |
| directory row `align-items: center` | |
| `.panel-left` column layout + hidden; the two lists scroll themselves; `#dir-list` is `flex: 1 1 0` | back to `flex: 1 1 auto` → red; `.panel-left` back to `overflow: auto` → red |
| JS delivers one complete string (`browse.dirImageCount` = `{n} images`) | |

Browser-level verification does not go into the unit tests (it needs local Chrome + a live service, not a repeatable gate); this time it was really measured with CDP:

    219 .li-count rows: computed white-space=nowrap, flex=0 0 auto, element height == line-height (18px)
    #dir-list: scrollHeight 6901 / clientHeight 542, setting scrollTop 400 takes effect
    at the same moment .panel-left.scrollTop is still 0, .panel-head's rect.top has not moved (header fixed)

A synthetic dataset then measured "extremely long names" again (43 / 73 / 74 / 137 chars, including space-free ASCII runs):
count width 23px, row height 30px, name scrollWidth > clientWidth (ellipsis works), count does not overflow the right edge.

## 3. "Long names still don't work" — not the layout, the browser simply never fetched the new CSS

After the commit the user reported that "ultra-long folder names are still not fixed". CDP re-check: with the
new CSS a 137-char directory name still renders the count on one line at row height 30px, **the code is right**
(see the previous paragraph). The real problem was that the browser never re-fetched `app.css`:

- `_mount_web` uses plain Starlette `StaticFiles`: it sends only `ETag` / `Last-Modified`, **no `Cache-Control`**;
- with no explicit cache directive, the browser caches by **heuristic freshness** (≈10% of the time elapsed
  since Last-Modified): the older the file, the longer the cache stays valid, and a plain refresh (F5) does not
  go back to the origin at all;
- reproduced for real (swap `app.css` to an old version and backdate mtime by 2 days → first visit → swap the new
  version back → F5):

      PHASE1 (cache holds the old CSS):            white-space=normal, .panel-left overflow=auto
      PHASE2 (after F5, disk already has new CSS): white-space=normal, overflow=auto   ← still old styles
      => STALE CONFIRMED

The fix is to add `Cache-Control: no-cache` to the static mount (`_RevalidatingStaticFiles` in `app.py`):
`no-cache` does not mean "do not cache", it means "must revalidate at the origin before use", and an unchanged
file is a 304. The same experiment rerun:

      PHASE2 (after F5): white-space=nowrap, flex-shrink=0, .panel-left overflow=hidden  ← new styles take effect

Criterion `test_api_e2e.py::test_static_assets_are_revalidated_so_a_reload_picks_up_changes`:
the three responses for `/`, `/css/app.css`, `/js/app.js` must all carry `Cache-Control: no-cache`;
falsification: remove that header line → red.

**Lesson**: this frontend has no build step, so the static files are everything the user sees; if the backend
does not change its cache policy, "changed it but the refresh shows nothing" gets investigated as a frontend
bug for a long time.

## Related

- Frontend contract: [p0-spec](../p0-spec.md) §4; copy-centralization rules in `test_web_contract.py`
- Similar changes: the three WebUI experience changes the same day (graphical directory picker / tag diff colors / double-click zoom preview),
  recorded in `changelog/ui-picker-and-zoom.md`