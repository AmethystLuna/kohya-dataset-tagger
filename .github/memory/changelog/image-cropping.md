---
name: image-cropping
description: 2026-09-20, the crop tool: POST /api/crop/apply confirms a rectangle per image and archives it beside the source as <stem>_N (always numbered, never overwriting, source untouched), the rectangle is in display coordinates so EXIF-rotated photos crop the way they are shown, no caption is inherited, and the box is carried to the next image as the same relative window
metadata:
  type: topic
---

# The crop tool (2026-09-20)

## The gap

Section 4.14 says it in one line: "the aspect ratio is unchanged, and **there is no crop box**". Area
normalization is the right default for a dataset and it cannot remove a border, a watermark strip or an
off-centre subject. The user asked for the other half - a Photoshop-style crop tool - and named the pipeline
in one sentence:

    裁切 -> 在当前目录留档（原文件名+_数字，冲突时自增）-> tagger(可选)

The archive being **in the image's own directory** is the load-bearing part of that sentence: it makes the
cropped result a dataset image in its own right, so the existing tagger, the readiness count and the trainer
all see it without a second mechanism.

## The three decisions the user made

1. **Standalone panel, and no export step in it.** The first answer was conditional - "if crop is separate,
   then drop the export-to-target part; the whole filter/crop/scale/tag/export order needs designing first".
   So this pipeline stops at the dataset and the scaling panel keeps its own export. Adding a copy step would
   freeze an order nobody has chosen yet.
2. **Keep the source format, inherit no caption.** The archive is a new image: duplicating the source's
   caption would make "these tags describe this image" quietly false after a crop removed half of what the
   tags described. It also makes the optional tagger the only writer of that sidecar.
3. **Reuse the tagger's settings** rather than putting a second model picker on the crop panel. The panel asks
   `autotag.js` for the settings the Tagger tab is showing, and the tag job is the existing
   `POST /api/autotag/run`.

## The change

- **`POST /api/crop/apply`** (section 4.17): one image, synchronous, one response carrying the archived path,
  the rectangle that was really applied and the real output size. The destination is derived from the source,
  so there is no second user-supplied path and no loopback gate.
- **`core/scaler.py`** grew `CropRect`, `display_size`, `numbered_destination` and four plan options
  (`crop`, `resize`, `attach_caption`, `numbered`), so the export, the crop and any future step share one
  image pipeline instead of three.
- **`web/js/cropbox.js`** holds every piece of geometry with no DOM in it, and `web/js/crop.js` is the panel
  plus the per-image dialog.
- **The tagger step** runs once, after the dialog closes, on the images that were archived.

## Why the box is inherited the way it is (the research)

The user asked what happens when the next image has a **different resolution**. What batch tools do:

| Rule | Where it is used | Effect |
|---|---|---|
| the same **pixel** rectangle | ImageMagick / IrfanView style command-line crops | on a smaller image it either fails or is clamped to nonsense |
| the same **relative** window (fraction of the frame) | Lightroom's copy-settings and Capture One's apply-crop, XnConvert's percentage crop | keeps the composition, which is what cropping a dataset means |
| a fixed output size, ratio only | the "bucket" way of thinking this tool already uses | needs the pipeline to resample, which is what the scaling half does |

This tool takes the middle one and adds the rule that a **locked ratio survives**: the same relative window
and centre, with the height re-derived from the width fraction when a preset is locked. Identical resolutions
short-circuit to the identical rectangle - the common case for one generator's output.

## Evidence

- `test/test_crop_api.py` (21 criteria, real HTTP through TestClient): naming and stepping over a user's
  file, no caption inheritance, keep-format for .jpeg, the BMP fallback warning, clamping, EXIF orientation,
  scale-from-crop (1774x1330 for a 4:3 crop of a 3000x2000 source), the whole-frame byte copy, 403/404/400/422.
- `test/test_scaler.py` section 6: the clamp table (**the same two rows the frontend harness asserts**, so the
  preview and the applied crop cannot drift), `display_size`, the numbering rule, crop-only rendering.
- `test/fixtures/crop_harness.mjs` (fake DOM + fake fetch + fake EventSource) really runs both frontend
  modules: the confirmed rectangle reaches the request, the shared store's resolution moves the normalize
  button, the same-resolution case reuses the box (732/232/1536/1536), a 1000x1000 frame inherits it as
  244/244/512/512, a failed write keeps the dialog on the same image and the retry is not counted as a
  failure, and the tagger step posts the archived paths with the Tagger tab's settings.
- `acceptance/ A44`: the same promises scored end to end against real files, including a (size, mtime, bytes)
  fingerprint of the whole source directory before and after.
- Falsifications run by hand: see below.

## Falsification (what was deliberately broken, and what turned red)

1. **`numbered=True` -> `numbered=False` in `api/crop.py`** (the bare name tried first again):
   `test_a_format_we_cannot_write_falls_back_to_png_and_says_so` and
   `test_an_explicit_format_wins_over_the_source` turn red - the archive was named `4.png` next to
   `4.bmp`. The same-extension rows stay green, which is the honest reading: with `1.png` as the source
   the bare candidate collides anyway, so the numbering assertions there are only half the cover.
2. **`display_size(opened)` -> `opened.size`** in `plan_one_image`:
   `test_the_crop_box_is_applied_to_the_rotated_image` and
   `test_resizing_a_rotated_image_keeps_the_displayed_aspect_ratio` turn red (the output came out
   2172x1086 instead of 1086x2172 - the transposed image squeezed into a landscape box). `display_size`'s
   own unit criterion stays green, because it calls the function directly.
3. **The last image closing the dialog without `endRun()`**: the harness fails on "two archives -> one
   refresh of the directory on screen".
4. **A second `api.cropApply(` call site** hidden in the dialog builder:
   `test_the_panel_never_writes_without_a_confirmation` turns red on the call count.

Each break was restored byte-for-byte (compared against the file read before the break) and the affected
criteria were re-run green.

## Known limits

- **No resume and no archive detection.** A second run over the same directory crops the archives again
  (`foo_1.png` -> `foo_1_1.png`). Any heuristic ("`_N` with a matching bare name is an archive") would sooner
  or later exclude a real image, so the limit is documented instead of guessed at.
- The dialog draws the 1600 px preview thumbnail. Precision comes from the numeric fields and the snapping.
- The archive name adds one underscore to a stem, and two underscore-sensitive places downstream were checked
  while answering exactly that question: the latent-cache wildcard (`invalidating 1.png also deletes
  `1_1.png`'s latent cache - safe, it is rebuilt) and kohya's face-crop convention (>=5 underscore tokens,
  only with `--face_crop_aug_range`, which this tool never writes). The repeat convention itself is
  **directory-only** in the trainer, so a purely numeric file name is never read as a repeat count. Details
  and line references in [image-cropping](../image-cropping.md).
- Not measured in a real browser: the drag geometry is covered by the pure-function harness (the same
  functions the pointer handlers call), and the CSS is static. The layout probe
  (`tools/measure_web_layout.py`) does not cover the crop dialog yet.
