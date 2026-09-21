---
name: batch-performance
description: 2026-09-19, batch operations measured first and optimized by benefit: the export render stage is parallel (5.0x), the caption probe is one batched request per chunk, and the write/cache paths were deliberately left alone as already seconds
metadata:
  type: topic
---

# Batch operations: measure, then optimize by benefit (2026-09-19)

The user asked for performance work on "the features that involve batch operations", chose **total
wall-clock time** as the pain, and named the scale/export, batch-edit, cache-rebuild and
batch-caption-loading paths. Autotag parallelism was explicitly scoped to "only where there is a GPU;
the CPU probably cannot take it", so it was left alone.

## Method: a non-destructive harness, then order by measured benefit

`tools/bench_batch_ops.py` (new) runs every path directly and also drives the real
`api/scale.py` job body. It is **non-destructive by construction**: it writes only under a
`--scratch` directory outside the repository, reads the real dataset read-only
(it samples images for the scale bench), and never touches a real training cache. The scratch guard
asserts the prefix before anything is created.

Baseline medians on this machine (16 cores; caption/cache on synthetic images, scale on real ones):

| Operation | Sequential | 4 workers | 8 workers |
|---|---|---|---|
| Export 1536x1536 PNG, `optimize=True` (the UI default) | 1355 ms/image | 415 | 272 (5.0x) |
| Export, `optimize=False` | 502 ms/image | - | - |
| Batch caption write (job body) | 3.46 ms/image | 1.27 | 0.85 (4.1x) |
| Cache invalidate (job body) | 0.30 ms/image | - | - |
| Caption read, one image per request | 2.56 ms/request | - | - |
| Caption read, batched via `/captions/read` | 0.34 ms/image | - | - |

At library scale (1653 images): export **~36 min -> ~7.5 min**; batch caption write 5.7 s; cache
rebuild 0.5 s; the caption probe went from 691 requests to 2 (chunked at 500). That is the whole
ordering argument.

## What changed

**1. The export renders in parallel** (`core/scaler.py`, `api/scale.py`).

`process_one_image` was split into `plan_one_image` (read the header, allocate the
destination) and `render_one_image` (decode / resample / encode / byte-copy), with
`process_one_image` kept as their sequential composition so every existing caller and test is
unchanged.

Allocation stays on one thread: `unique_destination` decides by asking what already exists, so
that is what keeps "never overwrite" true without a lock. Rendering runs in a
`ThreadPoolExecutor` (`EXPORT_WORKERS = max(1, min(8, cpu_count))`, `workers x 2`
images planned per round) because Pillow's decode/resample/encode release the GIL. The measured
5.0x is real, not scheduling noise.

**The first version of this was wrong, and the new criterion caught it.** Planning a whole round
before rendering any of it means a destination planned for an earlier image is not on disk yet, so
`exists()` alone handed the same name to two images: a same-stem pair (`a.png` +
`a.jpeg`) silently overwrote one output. The fix is one `reserved` name set for the
whole run, threaded through `unique_destination` / `plan_one_image` and compared with
`os.path.normcase` (so it follows the platform's case rules exactly like `Path.exists`).

**2. The caption probe is batched** (`POST /api/captions/read`, `web/js/captionprobe.js`, `web/js/app.js`).

The tag filter and the visible-scope frequency table need every image's tag list. The probe used to
issue one `GET /api/captions` per image (691 on the recursive scope) and - worse - re-ran the
whole filter after **every single response**: `applyFilter()` re-filters the whole listing,
hides/shows every gallery tile and recomputes the batch panel's scope. That is O(N^2) over the
directory.

`/captions/read` returns the `GET /api/captions` entry for many images in one request
(plus `ok`); out of bounds still rejects the whole request with 403, a vanished file is that
entry's error. `captionprobe.js` owns the chunk size (500) and the folding rule, and
`app.js` now refreshes **once per chunk** instead of once per image. `mapLimit` lost its
only caller and was deleted.

**3. Deliberately not changed.**

- **Batch caption write**: 3.46 ms/image is 5.7 s for the whole library. Parallelising buys ~4 s, and
  it would change the frozen "cancel is checked between images" contract (p0-spec §4.5 supplement) to
  "between chunks". Not worth it.
- **Cache rebuild**: 0.30 ms/image, 0.5 s for the library. Nothing to win.
- **WD14 autotag**: left alone per the user's "GPU only" scope.
- **PNG `optimize=True`**: 2.7x the cost of `optimize=False`, but it is the source
  script's default and a user-visible option, so the default is not changed quietly (the doc says so
  in [image-scaling](../image-scaling.md)).

## Criteria and falsification

- `test/test_scale_api.py::test_parallel_render_matches_sequential_byte_for_byte` (new): drives
  the real `_export` body at 1 and 4 workers over a fixture that contains a same-stem
  `.png`/`.jpeg` pair, and compares the two target trees by SHA-256. **Falsified**:
  dropping the `reserved` set turns it red (the `_1` output disappears - the collision
  is really exercised).
- `test/test_api_e2e.py` (new cases): the batched entry equals the single `GET` entry
  key for key, reading touches no mtime, one missing file is that entry's `not_found` while the
  rest still come back, and out of bounds is 403. The route is added to `EXPECTED_ROUTES`.
- `test/test_web_captionprobe.py` + `test/fixtures/captionprobe_harness.mjs` (new):
  static wiring plus a real headless run - 691 images become 2 requests with the order kept, an
  explicit size is honoured, `ok:false` / missing / malformed entries are classified.
  **Falsified**: putting `api.getCaption` back into the probe turns the wiring case red;
  changing the chunk size to 1000 turns the harness red.
- The new frontend route is checked against the frozen §4 route table by
  `test_web_contract.py`; `POST /captions/read` was added to p0-spec §4 first.

## How to reproduce

```
.venv/Scripts/python.exe tools/bench_batch_ops.py --count 200 --real-sample 24 --repeats 3 --export-job
.venv/Scripts/python.exe tools/bench_batch_ops.py --count 200 --repeats 1 --skip-scale
.venv/Scripts/python.exe -m pytest test/ -q
```

---

Related: [image-scaling](../image-scaling.md), [batch-jobs](batch-jobs.md), [p0-spec](../p0-spec.md)
