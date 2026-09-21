---
name: ui-folder-grid-preview
description: 2026-09-18 folder-grid preview in the center gallery (§4.13) — why it appears only on the "folders only" level, why the grid adapts as 1/4/9, how samples are taken, and why the mosaic is not assembled server-side
metadata:
  type: topic
---

# Folder 3×3-grid preview in the center gallery (2026-09-18)

The user's words: "in pure folder-browsing mode the preview UI should show the folders, and the folder preview content should be the 3×3 grid of the image files inside the folder".

## What the problem was

By the trainer's rule the dataset root **has no images of its own** (§1.1: `glob_images` scans one level only),
and the reference dataset's 219 post directories all sit directly under the root. So opening the dataset:

- the center workspace is **empty** (`gallery.empty`: "There are no images in this directory");
- the 219 subdirectories are only a column of text in the left panel, and you have to click into each one to know what is inside.

The previous round ([ui-picker-and-zoom](ui-picker-and-zoom.md)) had just finished "it clicks"; this round the user wants
"it shows": **folders need previews too**.

## Decisions

1. **Shape**: one card = the subdirectory's own **grid of thumbnails** + the directory name + "n images".
   The card is a **real `<button>`**, so Enter/Space work naturally
   (the previous round's lesson: "double-click only" is a dead end for keyboard users). The grid **adapts as 1/4/9 by image count** —
   see "Second round" below, which is what the user raised after seeing the first version.
2. **Data**: hung on the existing `GET /api/fs/list` (`dirs[].preview_images`), **no new endpoint**.
   `dirs[].image_count` was already `len(iter_images(child))`, and the samples are a **slice of the same enumeration** —
   zero extra IO, and it cannot say "the count says there are some but the preview shows none".
3. **It appears only on the "folders only" level** (`state.images.length === 0 ? state.dirs : []`).
   A level with images is for **browsing images**, not folders — that is exactly what the user meant by "pure folder-browsing mode".
   With the recursive switch on, `images` is non-empty, so the cards naturally do not appear, with no extra check.
   And when cards are shown, "There are no images in this directory" is no longer displayed — at that point it would be a lie.
4. **The mosaic is not assembled server-side.** One request fetches at most 9 `size=grid` thumbnails and goes through the gallery's
   existing **bounded concurrency + IntersectionObserver lazy loading**; having the server assemble one 3×3 mosaic would not save the decode cost
   (still those 9) but would introduce a new cache and invalidation logic. Thumbnails still only ever use the grid size and never load the original
   (decision #4).
5. Sampling does **no per-image allowlist check** — both versions were tried and the data rejected both: **per-image `resolve_under_roots`**
   measured **+440 ms** (219 directories became two thousand realpath calls), and **per-image `lstat`** still cost **+48 ms**.
   The case they guard is "a **symlink** inside a subdirectory points outside the allowlist": the directory itself has already passed the
   allowlist, and only a link can escape, and when that path is really taken `/api/images/thumb` returns 403 and the card hides it as
   "failed to load" — invisible and unclickable, with no illusion. The final implementation is just one slice, **96 → 98.5 ms**, essentially free.
   An empty directory gets `[]` rather than an omitted field.
6. Clicking a card goes through the existing `openDir(dir.path)`: allowlist, breadcrumb, and hash all take the same path,
   with no second navigation route.

## The backend must be restarted after a change (the user hit this for real on 2026-09-18)

Frontend static assets are `Cache-Control: no-cache` (see [dir-list-layout](dir-list-layout.md)), so
**refreshing the page only swaps in the JS**; `api/fs.py` is loaded when the process starts. Refresh without restart = **new frontend + old API**,
which shows up exactly as "the folder cards appeared but the grid is empty": `dirs[]` has no `preview_images`,
`Array.isArray(undefined)` is false → the card has a name and a count but not one thumbnail.

Measured evidence (that process at the time): `Get-Process 13108` started at **21:19:35**, `fs.py` was changed at **21:48:44**;
the `gallery.js` fetched from it was already the new one (with `makeDirCard`), while `/api/fs/list`'s
`dirs[0]` keys were only `{name,path,image_count}`. **Restarting the service** fixes it; refreshing the page cannot.

## Second round: the grid should "pick the optimal one" (1/4/9)

The first version **pinned the thumbnail area to 3×3**. So in a directory with only 1 image, that image huddled in the top-left with two empty
rows below; 2–4 images were the same. After looking the user said "the preview grid does not seem to have picked the optimal one" — in his
previous message he had in fact already named the shape: a **1/4/9 grid**.

The change is tiny: `side = samples.length >= 5 ? 3 : (samples.length >= 2 ? 2 : 1)`, the card carries
`dir-thumbs-1|2|3`, and three CSS rules give the column/row counts; `samples.slice(0, side * side)` also trims
extra samples (the backend caps at 9, and the frontend does not depend on that cap). The container still uses `aspect-ratio: 1 / 1`,
so a 1-cell and a 9-cell card are the same height and the grid does not come out ragged.

Criterion: the fixture holds directories with 1 / 3 / 12 images and asserts `dir-thumbs-1` / `-2` / `-3` and the thumbnail count respectively.

## Measurements (real data, 219 post directories / 1653 images)

| Quantity | Value |
|---|---|
| `GET /api/fs/list` (root, real HTTP + urllib) | best **101.6 ms** / median **107.2 ms**, response **156 593 B (≈153 KB)** |
| Same machine in-process: sampling on / off (`DIR_PREVIEW_LIMIT = 0`) | **98.5 ms / 96.0 ms** — sampling itself ≈ free |
| The two rejected approaches | per-image `resolve_under_roots` **+440 ms**; per-image `lstat` **+48 ms** |
| `dirs` / `dirs` with previews / max per directory | 219 / 219 / 9 |
| The root's own `images` | 0 (the trainer's rule, §1.1) |

We also falsified one idea along the way: `Invoke-WebRequest` measured 615 ms, while `urllib` measured 171 ms — **client
overhead had been mixed into the server time**. Do not measure performance with PowerShell's web cmdlet.

## Criteria and falsification

| Criterion | Falsification (confirmed red) |
|---|---|
| `acceptance` A34 (`preview_images` at most 9, one level, natural order, empty directory `[]`) | change `DIR_PREVIEW_LIMIT` to 12 → A34 and `test_fs_api` **both go red** (measured) |
| `test_fs_api`'s two (same source and order / cap and one level) | same as above |
| `test_web_folder_grid` wiring and CSS (1/4/9, equal-height cards, no interference with `.tile`) | remove the `galleryFolders()` gate → the wiring criterion goes red |
| Headless harness `folder_grid_harness.mjs` | switch sampling to 8 (done on a copy) → the harness exits 1: "expected 9, got 8" (measured) |
| Same (adaptive) | pin `side` to 3 (done on a copy) → the harness exits 1: "a one-image folder must use a single cell" (measured) |

## Related

- Contract: [p0-spec](../p0-spec.md) §4.13, §5's A34
- Directory scan rule: [dataset-contract](../dataset-contract.md)
- Thumbnails: [thumbnail-pipeline](../thumbnail-pipeline.md)
- Previous UI round: [ui-picker-and-zoom](ui-picker-and-zoom.md)