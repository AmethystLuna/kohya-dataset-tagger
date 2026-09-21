---
name: export-scope-list
description: 2026-09-20, the scaling export stopped covering the whole folder - POST /api/scale/run takes an explicit `images` list (the column's scope strip), an empty or absent list is 400 `no_images` instead of "then do the whole directory", every entry is validated (allowlist / file / image / under root) and duplicates dropped; the crop panel got the live strip reaction it was missing, and the scope strip's copy stopped naming only two of the four panels that obey it
metadata:
  type: topic
---

# The export acts on the scope strip's list (2026-09-20)

## The report

The user's words:

> 缩放功能没有按我当前选中的图片区导出到目标，而是选择了整个文件夹，你检查一下，顺便看看其他地方有没有犯同样的错误

The code agreed with the report, in writing. `api/scale.py` had `images = paths.iter_images(root,
recursive=True)`; the request body had no image list; the panel carried a sentence saying so
(`scale.coverage`: "The export covers every image under the source directory, subdirectories
included - the Scope control does not apply."), and the comment above `#scope-strip` in index.html
repeated it. **A documented deviation is still a deviation**: the control answers "which images?" for
the whole column, and a panel that answers it differently is precisely the defect the strip was built
to remove. The sentence was a receipt, not a fix - and the fix was on the endpoint's side, because
"the request has no image list" is an implementation fact, never a design reason.

## The audit (the second half of the request)

Every surface that acts on images was checked for the same mistake - acting on a wider set than the
control on screen says it will:

| Surface | What it acts on | Same defect? |
|---|---|---|
| Tagger (`autotag.js`) | `scope.paths()`, subscribes to it | no |
| Batch (`batch.js`) | `scope.paths()`, subscribes to it | no |
| Crop (`crop.js`) | `scope.paths()`, snapshotted per run | **half**: it read the strip only in `onShow()`, so with the tab open the line and the Start gate went stale - picking images in the gallery left Start disabled until the tab was left and re-entered. Fixed here. |
| Export/scale (`scale.js`) | the open directory, recursively | **yes** - the reported defect |
| Cache panel (`app.js`) | the stale list it is showing (`staleCaches[].image`) | no: it rebuilds exactly what it lists |
| Tag frequency (`filter.js`) | its own `#frequency-scope` | no: a different question (whose tags are counted), and the count is painted from what it counted |
| dataset.toml export (§4.9) | the open directory, one level | no: it generates a config file, it does not touch images |

Two statements were stale in the other direction and are corrected with the same change:
`scope.stripHint` said "The Batch and Tagger panels act on this scope" (the crop panel had obeyed the
strip since 2026-09-20, and the scale panel does now), and the `index.html` comment said the export
panel does not obey it.

## The change

- **`api/scale.py`**: `ScaleRequest.images: list[str]` is the export's whole extent.
  `_resolve_source_images` validates every entry (allowlist → is a file → image extension → under
  `root`), rejects the whole request on the first bad entry (no partial application), and drops
  duplicates. `root` keeps its job: the base the target tree is mirrored from
  (`destination_directory`, `src.parent.relative_to(root)`).
- **`web/js/scale.js`**: reads the shared control, snapshots it when Start is pressed, subscribes to
  it (so the gate moves while the tab is open), and disables Start while the scope is empty with one
  line saying why (`scale.scopeNone` - also the toast if a run is attempted anyway).
  `scale.coverage` is gone from the panel and from all three packs.
- **`web/js/crop.js`**: subscribes to the same control, like the tagger and the batch panel.
- **Copy**: `scope.stripHint` now names the four panels that obey the strip.

## The decision that mattered: no fallback

The tempting version kept the recursive walk as the behaviour for an empty list. It was rejected:
"the list is empty, so export everything" **is** the reported bug, and a fallback is reachable from a
bug - a UI regression would silently restore the whole-folder export. An empty or absent list is 400
`no_images`, and the panel cannot even get there (Start is disabled). The other rejected alternative
was deriving `root` from the list's common parent: it would move the target tree's shape, because the
structure must mirror the open directory, not the selection's deepest common folder.

## Evidence

- `test/test_scale_api.py` (40 criteria): exactly the listed images (one listed while `root` holds
  two → one image and its caption in the target), the list's order is the processing order, a
  duplicate is one file, an empty list and a body with no list are 400 `no_images` (and create no
  target directory), and the four rejection paths (403 outside the roots / 404 missing / 400
  `not_a_file` / 400 `unsupported_image` / 400 `image_outside_root`). The recursive criterion builds
  its list from `GET /api/fs/list?recursive=true` - the endpoint the strip itself reads - so the
  cache/hidden pruning and the directory-by-directory rebuild are covered on the way.
- `test/fixtures/scale_harness.mjs`: a real `scope.js` control over a fake DOM; the request carries
  that control's list, an empty scope keeps Start disabled and shows the reason, a change of the
  control reaches the panel while the tab is open, and a scope change between two runs reaches the
  second request (the snapshot is per run).
- `test/test_web_scope.py::test_the_export_panel_obeys_the_shared_scope` (replaces the criterion that
  pinned the old deviation), `test_web_crop.py` and `test/fixtures/crop_harness.mjs` for the crop
  panel's live reaction, `test_web_scale.py` for the frozen key set (`images` included).
- `acceptance/test_p0_acceptance.py::test_A36_image_scaling_export`: the list extent, the empty-list
  refusal and the boundary errors scored end to end against real files, with the list read from
  `/api/fs/list` rather than written by hand.

## Falsification (what was deliberately broken, and what turned red)

1. **The empty list falls back to `paths.iter_images(root, recursive=True)`**:
   `test_an_empty_image_list_is_400` and `test_a_body_without_a_list_is_refused` turn red - the
   fallback is exactly the behaviour the user reported.
2. **`images, failure = paths.iter_images(root, recursive=True), None`** (ignore the list):
   `test_the_export_covers_exactly_the_listed_images`, `test_the_list_order_is_the_processing_order`
   and `test_a_duplicate_entry_is_exported_once` turn red (2 exported where 1 was listed).
3. **The duplicate check removed**: `test_a_duplicate_entry_is_exported_once` turns red
   (`1.png` + `1_1.png`).
4. **The "under `root`" check removed**: `test_an_image_outside_the_source_root_is_400` turns red.
5. **`buildScaleRequest` returning `images: []`**: the harness fails on "the builder must carry the
   scope's list through untouched".
6. **The scale panel's `scope.subscribe` removed**: the harness fails on "the reason must go away
   once there is something to export" - the panel stopped following the control.
7. **The crop panel's `scope.subscribe` removed**: the crop harness fails on
   `scopeListeners.length === 0` - the subscription is what makes the reaction live.

Each break was restored exactly and the affected criteria re-run green (backend 40 passed; both
harnesses OK).

## Known limits

- **The list travels in the request body.** The reference dataset's "directory + subdirectories" scope
  is one array of 1653 absolute paths (~150 KB of JSON). Fine over loopback; the request is no longer
  O(1) in the size of the dataset.
- **The list is a client-side snapshot.** A file deleted between the listing and Start makes the whole
  request 404 rather than exporting the rest - the same "no partial application" rule autotag and the
  caption batch already follow.
- **Anything the strip itself gets wrong, the export inherits.** In particular the strip's
  "directory + subdirectories" entry was cached per directory in `scope.js:ensureRecursive` without
  being invalidated when the same directory's contents change: measured with a scratch probe against
  the real `scope.js` (create control → `ensureRecursive` → 2 paths; grow the listing to 3;
  `refresh()` → still 2, still 1 fetch; switch the scope away and back → still 2, still 1 fetch).
  So after a crop archived new files into the open directory, that scope kept reporting - and, once
  this round made the export take the list, exporting - the pre-crop tree until the user navigated
  elsewhere. **Fixed as its own item** in the round that produced this review, so the list an export
  carries is never a stale tree: see [scope-recursive-cache](scope-recursive-cache.md).
- **Not measured in a real browser.** The panel's behaviour is the fake-DOM harness; the strip's
  geometry is unchanged by this round.
