---
name: image-scaling
description: Dataset image scaling/export (§4.14): the request's image list is the whole export (the scope strip's answer, no whole-directory fallback), area normalization, no-upscale option, automatic caption attachment, destination-directory allowlist, never writing into the source directory
metadata:
  type: topic
---

# Dataset Image Scaling/Export (§4.14)

**What it is**: scale a dataset's images by **area normalization** into a destination directory, rebuilding subdirectories in the original relative structure,
with a same-named caption **automatically attached** next to each image; optionally generate a `dataset.toml` at the destination root so the export directory is directly trainable.

**Why it exists**: the author's standalone scaling tool is where it comes from,
and they asked to integrate it plus add a 'do not upscale images smaller than the target' option; at the same time they explicitly said **sidecar files are no longer customizable**
and captions are simply attached per this tool's rules. The contract is in [p0-spec](p0-spec.md) §4.14, and the acceptance criterion is A36.

## Rule: area normalization, not cropping

    scale = sqrt(target_width * target_height / (width * height))
    new_width  = max(1, round(width  * scale))
    new_height = max(1, round(height * scale))

- The aspect ratio is unchanged, and **this half has no crop box**. It is often called cropping in Chinese;
  the implementation is a uniform scale. (The real crop is [image-cropping](image-cropping.md), §4.17: a
  rectangle confirmed per image, applied **before** this normalization, which is what makes "the crop box
  area equals the target area" come out at scale 1.0 with no resampling at all.)
- With `no_upscale=True` (checked by default in the frontend), if the original **area** is smaller than the target area, `scale` is clamped to `1.0` and
  the size stays as-is. The test uses **area**, not a single side: the area of 2000×500 is 1.0e6 < 1536²'s 2.36e6,
  so it is not upscaled either — this is exactly the self-consistent rule of area normalization;
- `round` is Python's banker's rounding (matching the source script), and `max(1, …)` covers extreme strip images.

## Layers

| Layer | File | Responsibility |
|---|---|---|
| core | `core/scaler.py` | Pure scaling math + the plan/render split (`plan_one_image` allocates the destination, `render_one_image` does the pixels, `process_one_image` is the sequential composition) + atomic write. **Leaf**: module level imports only the standard library and Pillow, and the caption path does `from . import captions` inside the function |
| api | `api/scale.py` | `POST /api/scale/run` (202 + job_id; resolves and validates the `images` list) / `GET /api/scale/stream` (SSE) / `POST /api/scale/cancel` |
| web | `web/js/scale.js` | The 'scale' tab; behavior fixture `test/fixtures/scale_harness.mjs` |
| web | `web/js/trainparams.js` | The **shared source of truth** for resolution and caption extension, used by both the §4.9 export panel and §4.14 - and the one thing that **remembers** the parameters across a reload (one `kdt.trainParams` entry; the resample/format domains live here too, so a remembered name is validated against the list the backend accepts) |

## The image list is the whole export (changed 2026-09-20)

`POST /api/scale/run` writes **exactly the images the request lists**, in the order they are listed.
`root` stays the base the target tree is rebuilt from - `destination_directory()` uses
`src.parent.relative_to(root)`, so `root/post/1.png` → `target_dir/post/1.png`, and one task still
covers 219 subdirectories in a single stream. The list is the answer the column's shared scope strip
gives (`web/js/scope.js`), snapshotted when the run starts.

Until 2026-09-20 the request had no list: the endpoint enumerated `root` itself
(`paths.iter_images(root, recursive=True)`), so an export could cover the whole folder while the
control on screen said "selected: 3". The panel printed a sentence admitting the scope did not apply -
and a control that silently does not apply is the defect the strip exists to remove, while a sentence
admitting it is not a fix. There is **no fallback**: an empty or absent list is 400 `no_images`, because
"then do the whole directory" is precisely the surprise being removed (a fallback reachable from a bug
is the bug). Every entry passes `paths.resolve_under_roots`, must be a file with an image extension and
must lie under `root`; the first bad entry rejects the whole request, and a duplicate is dropped -
`unique_destination` would otherwise write a `_1` copy of an image the user picked once.

Criteria: `test/test_scale_api.py::test_the_export_covers_exactly_the_listed_images` (falsification:
enumerate `root` again → two files where the user listed one → red),
`::test_a_body_without_a_list_is_refused`, `::test_an_empty_image_list_is_400`,
`::test_a_duplicate_entry_is_exported_once`, `::test_an_image_outside_the_source_root_is_400`, and
`::test_a_recursive_scope_list_is_rebuilt_by_structure`, which builds its list from
`GET /api/fs/list?recursive=true` - the endpoint the strip itself reads - so the cache/hidden pruning is
covered on the way and the target tree must still correspond directory by directory to the source.
Frontend: `test/fixtures/scale_harness.mjs` builds a real `scope.js` control and asserts the request
carries that control's list, that an empty scope keeps Start disabled, and that a scope change between
two runs reaches the second request.

Three boundaries to know:

1. **`dataset.toml` under the destination root covers only one level**: the trainer's `glob_images` is not recursive (§1.1),
   so the generator creates subsets only for the destination's **direct subdirectories**. When the source is nested more than one level, deeper images are **still exported**,
   but they do not appear in a subset (they could not have been trained on from there anyway); images at the root level itself trigger
   the `[root_images]` warning.
2. **Empty directories are not rebuilt**: only directories that actually receive images are created.
3. **Repeated exports are neither skipped nor overwritten**: name collisions get `_1` / `_2` suffixes, and exporting to the same destination directory again
   just adds a copy; there is no resumability.

## Key decisions (read before changing)

1. **Captions are attached automatically; there is no 'sidecar extension' parameter.** This is a deliberate divergence from the source script:
   in this tool's dataset contract, the sidecar is the caption, and one more free-form input would only let people write a config the trainer cannot read.
   To change the extension, change `captionExtension` in the shared parameters (default `.txt`).
2. **The destination directory must not be inside the source directory (equality included)**, otherwise the export result would be seen by the trainer's directory scan as a new
   subset — that is writing into the dataset. The criterion is 400 `target_inside_root`. The test uses
   `Path.relative_to` (on Windows it compares case-insensitively), so equality matches too.
3. **Do not modify source files**: the source images are read-only; name collisions in the destination directory get `_1` / `_2` suffixes and are never overwritten.
   One criterion compares a `(size, mtime_ns)` snapshot of the source files before and after export.
4. **Atomic write**: a same-directory `.tmp-scale-*` file → `os.replace`; no temp file is left behind on success or failure
   (the same hard constraint as `core/captions.py`).
5. **When the size is unchanged and the output format equals the source format, copy bytes directly** (no re-encode). The quality loss of re-encoding JPEG
   is real, and 'no upscale' is exactly the most common case.
6. **Resolution and caption_extension are shared with §4.9** (`trainparams.js`): the resolution written into the toml
   and the size actually used for scaling must be the same value, otherwise the exported directory and the config disagree.
7. **Parallel render, single-threaded allocation** (changed 2026-09-19 from "single-threaded sequential processing"): `process_one_image` is split into `plan_one_image` (read the header + allocate the destination) and `render_one_image` (decode / resample / encode). Allocation stays on **one thread** - that is what keeps `unique_destination`'s never-overwrite rule true without a lock - and **one `reserved` set spans the whole run**, because a destination planned for an earlier image is not on disk yet, so `exists()` alone would hand the same name to two images (the parallel-equivalence criterion caught exactly that). The render stage is a `ThreadPoolExecutor` (`api/scale.py: EXPORT_WORKERS` = `max(1, min(8, cpu_count))`, chunk = workers x 2); Pillow releases the GIL, so it really scales: measured on 16 cores for a 1536x1536 PNG export, 1355 ms/image sequential -> 415 at 4 workers -> 272 at 8 (5.0x), and the target tree is **byte-for-byte identical** to the sequential one (`test_parallel_render_matches_sequential_byte_for_byte`). A cancel is noticed **between rounds** rather than between two consecutive images. Why not faster: `optimize=True` PNG is 2.7x the cost of `optimize=False` (1355 vs 502 ms/image), but it is the source script's default and a user-visible option, so the default is not changed quietly.
10. **The panel's parameters are remembered, the permissions are not** (2026-09-21). The user's report:
   "缩放参数需要持久化，不能每次打开网页都恢复成默认值". The store reads its key when it is built and writes it
   whenever a setter accepts a value, so all three panels that share it are seeded from the user's last
   session, and no panel keeps a second, unremembered copy (a criterion forbids `localStorage` in
   `scale.js` / `crop.js`). The remembered field list is explicit and pinned by a criterion, because the
   interesting part is what is **not** in it: the target directory (a stored destination is a location nobody
   chose this session) and `overwrite_dataset_toml` (a standing permission to replace a hand-tuned
   `dataset.toml` is exactly what "never replaced silently" is about - the panel offers it per run and the
   run that skips the file says so). A remembered resample/format name is validated against
   `RESAMPLE_NAMES` / `IMAGE_FORMATS` (defined here, pinned to the backend's tuples), so an entry written by
   an older build falls back to the default instead of landing in an empty `<select>` and a 422.
8. **Re-encoding must call `ImageOps.exif_transpose`**: we do not carry EXIF into the output, and if the orientation is not baked into pixels
   a portrait shot comes out rotated 90°. The direct byte-copy path does not touch it (EXIF is still there and both the browser and the trainer read it correctly).
9. **The export obeys the column's scope strip, and the endpoint takes the list** (2026-09-20). The panel
   sends `images` (the control's `paths()` at the moment Start is pressed, read again for every run) and its
   Start button is disabled while the scope is empty, so "what will this act on" is answered by the same
   control as in every other panel. The backend is the authority for the list's validity: allowlist → file →
   image extension → under `root`, first bad entry rejects the request, duplicates dropped. What was
   rejected: keeping the whole-root walk as a fallback for an empty list (that fallback *is* the reported
   bug), and deriving `root` from the list's common parent (it would move the target tree's shape: the
   structure must mirror the open directory, not the selection's deepest common folder). The control's
   own listing was fixed the same day so the list cannot be a stale tree - it used to outlive its
   directory and to survive a re-listing of the same one:
   [changelog/scope-recursive-cache](changelog/scope-recursive-cache.md).

## How to verify

    .\.venv\Scripts\python.exe -m pytest test/test_scaler.py test/test_scale_api.py test/test_web_scale.py -q
    .\.venv\Scripts\python.exe tools\bench_batch_ops.py --count 200 --real-sample 24 --export-job
    .\.venv\Scripts\python.exe -m pytest acceptance/ -q -k "A36"
    node test/fixtures/scale_harness.mjs <repo>/src/kohya_dataset_tagger/web

- `test/test_scaler.py`: math, no_upscale, byte copy, caption byte-for-byte, name-collision suffixes, atomic write;
- `test/test_scale_api.py`: 202/SSE/cancel, the four destination-directory boundaries, source files untouched, no temp files, parallel render == sequential render byte for byte, and the image-list contract (exactly the listed images, order, duplicates, empty/absent list, and the four list-entry rejection paths);
- `test/test_web_scale.py` + `scale_harness.mjs`: frontend list and defaults == backend, request body key set,
  done must recognize `finished:true`, cancel really hits the endpoint (fake DOM + fake fetch + fake EventSource),
  and the parameters survive a reload (harness sections 2b and 10: build the store twice over one fake
  `localStorage`; a corrupt/foreign/out-of-domain entry reads as "nothing stored"; a hostile storage does not
  break the store; and a seeded entry really lands on the panel's controls and in the request body);
- `acceptance` A36/A36b: real HTTP through the whole pipeline.

## Common pitfalls

- **The temp file extension is `.tmp`, so `format=` must be passed explicitly** to Pillow; otherwise it guesses from the extension and throws.
- **`max(1, …)` cannot be omitted**: an extreme strip image (10000×1 scaled to 8×8) would compute a height of 0.
- **JPEG does not support alpha**: `RGBA / P / LA` all need `convert("RGB")` first; saving directly throws.
- **PNG's `optimize` is slow for large images**, but that is the source script's default and a user-visible option, so do not quietly change the default.
- **`paths.resolve_under_roots(target, must_exist=False)`**: allows the directory not to exist yet, so the user
  does not have to create it in the file manager first; but the allowlist check cannot be skipped.
- **This module does not disable `Image.MAX_IMAGE_PIXELS`**: the source script did, this one does not. Images above roughly 89 M pixels
  raise a `DecompressionBombWarning` (not an error). Add handling only if such images really need to be processed, and write it into this section.
- **The SSE end signal is `done{finished:true}`**, not `done >= total`: `total` is a snapshot taken at the start,
  so on cancel the two can never catch up.
