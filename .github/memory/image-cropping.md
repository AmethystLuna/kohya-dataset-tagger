---
name: image-cropping
description: Dataset image cropping (section 4.17) - confirm a rectangle per image, archive it beside the source as <stem>_N, the display-coordinate rule (EXIF), keep-format and no-caption-inherited semantics, the box inheritance rule across resolutions, the aspect lock that binds the numeric W/H fields as well as the handle drags, the output-scaling switch that lives in both the panel and the dialog, the zoom/pan canvas view that never moves the crop box, the original-resolution canvas with its crop preview, and how the optional tagger step reuses /api/autotag/run
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
12. **A locked ratio binds the numeric W/H fields, not just the mouse.** `cropbox.applyNumeric` is the single
    rule: the edited box drives and its partner is derived (`height = width / ratio`, or the reverse),
    `input` updates the partner live and `change`/Enter writes the applied numbers back. A size the frame
    cannot hold is capped by the **largest rectangle of that ratio that fits** - the same "stop at the frame"
    rule as a handle drag - because clamping each axis on its own is exactly how the ratio would be lost
    silently. `x`/`y` only move the box. With no preset (`free`) the two fields stay independent, and
    `source` locks to the image's own ratio like any other preset.
13. **"Was scaling asked for" has one answer, kept in two places.** The panel's `crop.scaleAfter` switch is the
    run default, and the confirmation dialog carries the same switch on its readout line; changing either
    writes both (`dialog.scaleBox` -> `scaled` -> `scaleField.box` -> `paintOptions`), so the choice made
    for one image carries to the next image and becomes the panel's state for the next run (the rule the
    aspect preset already follows). Off sends `scale: null` (§4.17 rule 6) and writes the box's own pixels.
    The switch is disabled while a write is in flight, because it decides the body that is already sent.
14. **The canvas view is separate from the crop box.** The stage is a fixed viewport; the image and the box live
    in one `.crop-canvas` that zoom (wheel, 0.25x-8x, around the pointer) and pan (a drag on the background, or
    Space/middle-drag anywhere) move together. The two gesture families are separated by *where the drag starts*,
    and the wheel is bound to the stage because the box is the top layer: for a full-frame crop there is no
    background left to grab, and a gesture the box swallowed would make that crop unzoomable. The view decides
    only the display - `dialog.rect`, the numeric fields and the request body are source pixels, so zooming and
    panning can never change the crop. `0` and every new image return to the fitted, centred view.
15. **The dialog draws the original, not the thumbnail cache.** `GET /api/images/raw` streams the source file
    (ETag = size+mtime, `private, no-cache`) so zooming shows the pixels the crop keeps; the gallery and the zoom
    surface stay on the grid/preview thumbnails. `image-orientation: from-image` keeps the browser's EXIF
    handling in step with `display_width/height`. A source the browser cannot decode falls back to the preview
    thumbnail **once** (proportional, so the box still means the same rectangle); a second failure is the
    "broken preview" that disables Confirm.
16. **The crop preview is a view, not a crop.** The toolbar switch fits the rectangle to the stage and clips the
    rest of the image away, so the viewport shows exactly the confirmed result; every option (aspect, X/Y/W/H,
    normalize, reset, output scaling) and every canvas gesture is frozen meanwhile, while the switch itself and
    Enter/Esc stay live. Leaving it (or confirming, or moving to the next image) restores the editable view the
    user had; the rectangle is never touched.

## Known limits

- Re-running the crop over the same directory treats the archives as sources (`foo_1.png` -> `foo_1_1.png`).
  There is no resume and no "`_N` looks like an archive, skip it" heuristic: guessing would sooner or later
  exclude a real image. Documented in section 4.17 rather than papered over.
- The gallery and the zoom surface draw the 1600 px preview thumbnail; the crop dialog draws the original, so
  its zoom is real detail - decoded one image at a time.
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
- `test_web_crop.py` + `crop_harness.mjs`: the panel and dialog (request body, box inheritance, the ratio lock
  for both a handle drag and a typed width/height, the zoom/pan canvas view, the crop preview that freezes the
  options, the original image with its thumbnail fallback, failed write, tagger reuse, single write path).
