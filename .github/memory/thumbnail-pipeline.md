---
name: thumbnail-pipeline
description: Performance-first thumbnail pipeline: PIL draft fast decode + LANCZOS scaling + disk cache + 2x DPR
metadata:
  type: topic
---

# Thumbnail Pipeline

## What it is

Decision #4 is performance first, while keeping visuals as sharp as possible. The two do not conflict here —
the fast part comes from **decoding less**, and the sharp part from **correct resampling and pixel density**.

## The four design points

### 1. Decode less: JPEG uses `draft()`, PNG has no choice

```python
im = Image.open(path)
im.draft("RGB", (target, target))   # JPEG only: let libjpeg emit 1/2, 1/4, 1/8 dimensions directly via DCT scaling
im = ImageOps.exif_transpose(im)    # portrait shots from phones/cameras
im.thumbnail((target, target), Image.LANCZOS)
```

`draft()` is an order-of-magnitude speedup for JPEG: a 4000×3000 JPEG producing a 320px thumbnail
does not need to decode all 12 million pixels. **PNG has no equivalent mechanism**, so it must be fully decoded before scaling —
when a dataset has many PNGs this is the bottleneck, and it is also why point 3 below has to exist.

### 2. Sharp: LANCZOS + 2x pixel density

- Resampling is always `Image.LANCZOS` (the sharpest general choice; `BILINEAR`/`NEAREST` are visibly blurry)
- Thumbnails are generated at **2x DPR**: a cell displayed at 160px in CSS gets a 320px image.
  This is the main place where keeping visuals as sharp as possible lands — a 1x thumbnail is guaranteed to look soft on a high-DPI screen.
- No sharpening (`UnsharpMask`): downscaling is already a low-pass filter, so extra sharpening only rings at edges,
  and in the tag-editing view the user needs to judge detail, not look at something pretty

### 3. Disk cache: decode once, reuse for a long time

    <cache_root>/<key>.webp
    key = sha1(absolute path | mtime_ns | file size | target size | pipeline version)[:16]

- A hit goes straight to `send_file` and **never touches Pillow** — this is the dividing line for how fast refreshing the page feels
- The pipeline version is written into the key: change the scaling algorithm or size and everything is invalidated, with no manual cache clearing
- Response headers `ETag: <key>` + `Cache-Control: public, max-age=31536000, immutable`,
  saving the browser another round trip
- On a hit, `os.utime` touches the file for the LRU eviction below to use

**Eviction policy**: cap by total size (2 GB by default), and when over the cap delete from oldest atime to newest.
Do not evict by file count — thumbnail sizes vary widely with the source image size.

### 4. Preserve the alpha channel

A dataset may contain images with alpha, and the trainer's subsets have an `alpha_mask` option —
users need to **see which images have alpha from the thumbnail alone**.

- Has alpha: keep alpha, the frontend draws a checkerboard behind it; cache format is WebP (supports alpha, much smaller than PNG)
- No alpha: WebP as well
- Do **not** convert every PNG to an opaque JPEG the way the trainer does (`training-ui/server.js:1815`) —
  that hides alpha from the user, and alpha is exactly the information `alpha_mask` needs

## How to use it

- Thumbnail sizes are **constants**; do not make them request parameters (otherwise arbitrary sizes shatter the cache):
  `GRID_THUMB = 320`, `PREVIEW_THUMB = 1600`
- Generation goes through a `ThreadPoolExecutor`: Pillow **releases the GIL** during decode and resampling,
  so threads really run in parallel and no multiprocessing is needed
- Concurrent requests for the same key must be coalesced (per-key lock or a future table), otherwise the first screen decodes the same image repeatedly
- The cache directory defaults to the system temp area or a configurable path outside the repo; do **not** put it inside a dataset directory
  (it would pollute `image_dir` and violate [dataset-contract](dataset-contract.md))

## Frontend: the queue serves what is on screen first

The gallery loader (`web/js/gallery.js`) caps concurrent thumbnail requests at `THUMB_CONCURRENCY = 6`
and prefetches 400px past the viewport. One screen queues far more than six tiles, so **the order the
queue drains in is what the user experiences**.

Two `IntersectionObserver`s watch the same tiles: the 400px one queues a tile (`request`) when it comes
near the viewport, and a zero-margin one re-ranks it (`prioritize`) after every crossing. The loader
keeps its queue ordered by `onScreen` (each band in arrival order), so a tile that scrolls into view
overtakes the prefetches, and one that scrolls back out falls behind them. The prefetch margin decides
only *whether* a tile is queued, never its priority - a request already in flight cannot be reordered.

Do not make the queue strictly first-in-first-out again, and do not drop the zero-margin observer: the
criteria are `test/fixtures/thumb_loader_harness.mjs` and `test/fixtures/gallery_scroll_harness.mjs`,
run from `test/test_web_gallery.py`. Why (2026-09-20), the falsifications and the known limits:
[changelog/gallery-scroll-priority](../changelog/gallery-scroll-priority.md).

---

Related: [dataset-contract](dataset-contract.md), [scope-and-decisions](scope-and-decisions.md)
