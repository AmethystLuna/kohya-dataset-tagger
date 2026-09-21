---
name: image-cropping
description: Dataset image cropping (section 4.17) - confirm a rectangle per image, archive it beside the source as <stem>_N, the display-coordinate rule (EXIF), keep-format and no-caption-inherited semantics, the box inheritance rule across resolutions, and how the optional tagger step reuses /api/autotag/run
metadata:
  type: topic
---

# Dataset Image Cropping (section 4.17)

**What it is**: a Photoshop-style crop tool for a dataset directory - a rectangle with eight handles,
guide lines, aspect-ratio presets and a one-click "make the box area the target size" - confirmed
**image by image**, whose result is **archived beside its source** (`<stem>_1<ext>`, `_2`, …) and can then
be captioned by the existing tagger in the same run.

**Why it exists**: [image-scaling](image-scaling.md) normalizes area and keeps the aspect ratio; it cannot
remove a border, a watermark strip or an off-centre subject. The crop is the other half of "prepare the
image", and the user asked for it explicitly (2026-09-20). The contract is [p0-spec](p0-spec.md) section 4.17
and the acceptance criterion is A44.

## The pipeline, and what is deliberately not in it

    crop -> archive beside the source -> [tagger] -> (stop)

The user's naming: 裁切 → 在当前目录留档 → tagger(可选). **The export-to-target-directory step is not part of
it.** The whole filter/crop/scale/tag/export chain is going to be designed as a workflow later; until then the
scaling panel keeps its own export and this one stops at the dataset. Do not "helpfully" add a copy step here:
that would freeze an order the user has not chosen yet.

## Layers

| Layer | File | Responsibility |
|---|---|---|
| core | `core/scaler.py` | `CropRect` (+ `clamped`/`covers`), `display_size`, `numbered_destination`, and `plan_one_image`/`render_one_image` with `crop`/`resize`/`attach_caption`/`numbered` |
| api | `api/crop.py` | `POST /api/crop/apply` - one image per request, synchronous, destination derived from the source |
| web | `web/js/cropbox.js` | pure geometry: handle drags, ratio lock, area normalization, cross-resolution inheritance, snapping (no DOM) |
| web | `web/js/crop.js` | the panel, the per-image confirmation dialog, the run loop and the tagger step |
| web | `web/js/trainparams.js` | the shared resolution **and** the pipeline settings the crop panel inherits (resample/quality/optimize/no-upscale) |

## Rules a later change has to respect

1. **Display coordinates, always.** The rectangle means the image as the eye sees it, i.e. after EXIF
   orientation. `core/scaler.display_size` is the single source for that frame (it swaps width/height for
   orientations 5-8), `GET /api/images/meta` reports it as `display_width`/`display_height`, and the
   renderer applies `ImageOps.exif_transpose` **before** the crop. Cropping the raw header instead is how a
   portrait phone photo comes out sideways - and the same mistake used to distort any EXIF-rotated photo the
   export re-encoded (the planner measured the raw size and the renderer resized the transposed image).
2. **The archive is always numbered**: `scaler.numbered_destination`, first free `n >= 1`, the bare name
   never tried. That is deliberately **not** the export's rule (`unique_destination` prefers the bare name):
   the archive sits in the same directory as its own source, so a bare `4.png` next to `4.bmp` would read as
   that file. Both rules share the collision loop, so "is this name taken?" is answered once.
3. **Nothing is overwritten and the source is never touched** - not its bytes, not its mtime. The endpoint's
   criteria compare a full (size, mtime, bytes) fingerprint of the source directory before and after.
4. **No caption is inherited.** The archive starts with no sidecar; duplicating the source's tags would make
   "these tags describe this image" quietly false after a crop removed half of what they describe. The
   optional tagger step is therefore the only writer of that sidecar, with section 4.4's write modes and
   backup net unchanged.
5. **`format: "keep"`** keeps the source's format *and its suffix spelling* (.jpeg stays .jpeg, .JPG stays
   .JPG). A format Pillow reads but this tool does not write (BMP) falls back to PNG **and says so in
   `warnings`**.
6. **`scale` is present or absent, never a flag.** Absent (null) writes the crop's own pixels; an object
   area-normalizes **from the cropped size** exactly as section 4.14 defines. So a box whose area equals
   `target_width x target_height` comes out at `scale == 1.0` and `resampled == false` - which is what makes
   the dialog's normalize button the no-loss way to reach the training resolution.
7. **The rectangle is clamped, not rejected** (a 1-2 px overshoot is a rounding artefact of dragging over a
   1600 px preview); `crop` reports what was applied, `crop_requested` what arrived, and a `warnings` entry
   says it was clamped. `width`/`height` < 1 is a 422 instead, because that is a client bug.
8. **The dialog has exactly one write path** (`confirmCurrent` -> `api.cropApply`) and **exactly one
   run-ending function** (`endRun`), called by the last image, the end button and Esc. All three owe the user
   the same three things: the summary, a refreshed listing, and the optional tag job.
9. **The box is inherited across images by *relative* window**: same display resolution gives the identical
   rectangle; a different one keeps the same fraction of the frame and the same centre, and a locked ratio is
   re-derived from the width fraction. A verbatim pixel rectangle would land somewhere else entirely.
10. **The tagger step is a reuse**: after the dialog closes, the panel calls the existing
    `POST /api/autotag/run` with the archived paths and the settings the Tagger tab is showing (`getTaggerParams`
    from `autotag.js`). No second model picker, no second thresholds table, no per-image tagging job.
11. **Snapping is measured in display pixels** (6 px, converted against the drawn image), so it feels the same
    on a 512 px and a 6000 px image; the guides are the frame edges, the centre and the two thirds.

## Known limits

- Re-running the crop over the same directory treats the archives as sources (`foo_1.png` -> `foo_1_1.png`).
  There is no resume and no "`_N` looks like an archive, skip it" heuristic: guessing would sooner or later
  exclude a real image. Documented in section 4.17 rather than papered over.
- The dialog draws the 1600 px preview thumbnail; precision comes from the numeric X/Y/W/H fields and the
  snapping, not from the pixels on screen.
- **The archive name adds one `_`-separated token**, and two places further down the pipeline are
  underscore-sensitive. Both are **pre-existing** (they bite `foo.png` next to `foo_bar.png` just as well)
  and neither makes a crop wrong - but the crop makes the `<stem>_<n>` shape systematic, so they are
  written down here rather than discovered later:
  - `core/cache_invalidation.latent_cache_paths` finds an image's latent caches with the `<stem>_*`
    wildcard (the resolution cannot be derived from the image path), so **invalidating `1.png` also
    deletes the latent cache of `1_1.png`**. Deleting a latent cache is always safe - the trainer rebuilds
    it - so the cost is recomputation, never a wrong cache. The TE cache is *not* affected: it is looked up
    by exact name (`<stem>_anima_te.safetensors`), and invariant number one keeps working per file.
  - kohya's **face-crop naming convention** (`library/train_util.py:1536-1548`) reads the last four
    `_`-separated tokens of an image stem as `face_cx/cy/w/h` **when there are at least five**, and only
    while `--face_crop_aug_range` (or that subset key in a toml) is set. This tool never writes that key,
    so it takes a hand-edited config: an image already named `a_b_c_d.png` becomes `a_b_c_d_1.png` and
    would then be read as a face box.

  **What is *not* affected, checked in both codebases**: the kohya repeat convention (`<N>_<class>`) is
  parsed from **directory** names only (`library/config_util.py:627-631`, guarded by
  `if not subdir.is_dir(): continue`) and only on the legacy `--train_data_dir` path - the dataset.toml
  this tool generates carries an explicit `num_repeats` (the number typed on the export panel), so a
  purely numeric file name such as `1.png`, or its archive `1_1.png`, is never read as a repeat count.
  Nothing in this tool or in the trainer maps `_N` back to a base name either: the allocator only asks
  whether a candidate name is taken, and every cache path is built by appending to the stem.

  **Measured, not reasoned** (the trainer's own `generate_dreambooth_subsets_config_by_subdirs` run
  against a synthetic tree, 2026-09-20): `1_post/` with `2.png` **and** `2_1.png` inside gives
  `num_repeats: 1, class_tokens: "post"` - the archive file is invisible to the parser; a *directory*
  named `2_1` gives `num_repeats: 2, class_tokens: "1"`, and a purely numeric directory `10` gives
  `num_repeats: 10, class_tokens: ""`. Two traps of that legacy path, both directory-level and both
  pre-existing: a directory whose name has no numeric prefix is **dropped with a warning**
  (`plain/` disappeared from the result), and a flat `--train_data_dir` with no subdirectories yields
  **zero subsets**. The crop can never introduce either: it writes files into an existing directory and
  never creates one.

## How to verify

    .\.venv\Scripts\python.exe -m pytest test/test_scaler.py test/test_crop_api.py test/test_web_crop.py -q
    node test/fixtures/crop_harness.mjs <repo>/src/kohya_dataset_tagger/web
    .\.venv\Scripts\python.exe -m pytest acceptance/ -q -k A44

- `test/test_scaler.py` section 6: the clamp table the frontend shares, `display_size`, the numbering rule,
  crop-only rendering, the full-frame byte copy;
- `test/test_crop_api.py`: the endpoint end to end (naming, no caption, keep-format, clamping, EXIF,
  scale-from-crop, 403/404/400/422, no temp files);
- `test_web_crop.py` + `crop_harness.mjs`: the panel and dialog (request body, box inheritance, ratio lock,
  failed write, tagger reuse, single write path).
