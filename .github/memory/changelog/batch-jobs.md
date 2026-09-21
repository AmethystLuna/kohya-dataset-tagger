---
name: batch-jobs
description: 2026-09-19, the batch caption write and the cache rebuild gained a job form with real progress and a cancel that stops between images - additive, the synchronous endpoints unchanged
metadata:
  type: topic
---

# The batch write and the cache rebuild as jobs (2026-09-19)

Both operations were a single POST over however many images the user had selected: a thousand-image
write looked exactly like a hung one, and a cache rebuild could not be stopped at all. This is the
backend half of the fix; the frontend panels come next.

## Why the job form is additive

The synchronous endpoints **stay exactly as they were**. The dry-run preview is instant and must not
hide behind a stream, and external callers keep working. What is new is a second entrance:

| Method | Path | Answer |
|---|---|---|
| POST | `/captions/batch/run` | 202 `{job_id, total}` (`dry_run:true` is 400 `bad_request`) |
| GET | `/captions/batch/stream?job_id=` | SSE: one `progress` per image, then `done` |
| POST | `/captions/batch/cancel` | 202 `{cancelled: bool}` |
| POST | `/cache/invalidate/run` | 202 `{job_id, total}` |
| GET | `/cache/invalidate/stream?job_id=` | same stream shape |
| POST | `/cache/invalidate/cancel` | 202 `{cancelled: bool}` |

Frozen in [p0-spec](../p0-spec.md) §4.5 supplement, and scored independently by A39 in `acceptance/`.

## Design: one shape, copied from §4.14 on purpose

`_Job` holds the machinery every job needs: an ordered **event log** (one progress per image plus one
terminal done), a `threading.Event` cancel flag, a finished flag and a lock. `BatchJob` and `CacheJob`
add their counters and their payloads. The log is complete, so an EventSource that connects *after* the
POST replays the whole run - the frontend never needs a second request, and a dropped connection
cannot change the numbers, because **counting happens in the worker thread**.

The per-image payload is the same entry the synchronous endpoint returns in `results`, so the frontend
builds its outcome table from the stream alone. The two loops now share `_batch_step()`, which is what
keeps "the preview says it will change" and "the write really changed it" one rule rather than two.

## Cancel semantics (frozen, and the honest half)

The flag is checked **between images**: a write already in progress always finishes. Whatever landed
stays written and is counted, so `cancelled:true` means "stopped early", never "undone". A cancel for
an unknown or finished job answers 202 `{cancelled:false}` instead of pretending. An unknown `job_id`
on a stream is 404 `unknown_job`.

## Criteria and falsification

`test/test_batch_jobs.py` (new, 7 criteria) is split deliberately:

- **API level** (the real routes over a synthetic dataset): one progress per image with the counters
  running 1..N, a terminal done whose `summary` matches the event count, the files really changed,
  `dry_run` refused, an unknown job answering 404, and the cache job removing the caches;
- **worker level** (deterministic, because a cancel races the worker): a cancel already set means
  **not one write**, and a cancel arriving mid-run finishes the image in hand and skips the rest.

The API-level cancel criterion asserts invariants rather than a fixed count - `processed == done`,
`processed == the number of captions that actually changed`, and "unwritten only with `cancelled:true`"
- so it cannot be flaky on any interleaving.

Falsified: removing the between-images check turns the two worker criteria red (2 failed); restoring
it returns 7 passed.

    .venv\Scripts\python.exe -m pytest test/ -q            843 passed, 2 skipped (2026-09-19)
    .venv\Scripts\python.exe -m pytest acceptance/ -q      75 passed (2026-09-19)

## Two self-inflicted incidents worth keeping

1. **The Python source was written through TypeScript strings.** Literal `\n` inside a single-quoted
   TS string became a real newline in `_sse()` and the keepalive frame, which broke two Python string
   literals. The router-discovery error surfaced it immediately (`SyntaxError: unterminated string
   literal`), and the repair was two lines. Lesson: build generated source as line arrays, or escape.
2. **A capped read truncated a 2100-line acceptance file to 936 lines.** Appending "to the end" of a
   file whose read was silently capped wrote the block into the middle and dropped ~1015 lines.
   `git diff --stat` caught it at once, `git checkout --` restored the committed file, and the block
   was then appended **byte-wise** (write the block to scratch, then `read_bytes() + block`). This is
   the second time a length-capped read has bitten this session; the rule is now explicit: never
   round-trip a whole file through a read you have not bounded yourself.

## The frontend drives the job (same day, follow-up)

**Batch panel.** The apply button now starts the job (`api.batchCaptionsRun`), the panel opens the
stream and paints from it: a progress bar plus `batch.running` with the file in hand, a Cancel button
that calls the cancel endpoint and relabels to `batch.cancelling`, and an outcome table **built from
the per-image events** instead of from one response. The table shares its row builder with the preview
(`buildDiffRow`), so the preview and the write cannot drift apart, and it stops at `PREVIEW_ROWS` with
the same "and N more" note. A lost stream (three failures, the limit `scale.js` uses) unlocks the
panel and says so rather than spinning forever.

**Cache panel.** "Rebuild all" and the per-row rebuild go through `api.cacheInvalidateRun`; the status
line becomes `cache.progress {done}/{total}`, a new Cancel button appears next to Refresh, and the
terminal copy says how far it got. `paintCacheStatus()` is the single place that paints that line, so
the stream (per image), the failed-load state and the language switch all agree.

**The terminal copy never says undo.** `batch.cancelled` reads "已写入 {applied}/{total} 张后取消；已经落盘
的改动无法撤销" and `cache.cancelled` "已处理 {done}/{total} 个后取消": a cancel stops what has not been
written yet, and pretending otherwise would be a lie about the user's files.

Nine new strings in all three packs (`batch.running`, `batch.current`, `batch.cancel`,
`batch.cancelling`, `batch.cancelled`, `batch.streamLost`, `cache.progress`, `cache.cancel`,
`cache.cancelling`, `cache.cancelled`) - and the placeholder-parity criterion caught the first draft,
which had `{done}` in zh but not in en/ja.

Criteria: the batch harness now drives the job end to end (run request carries no `dry_run`, the
stream opens, progress paints 1/2 and the file name, the table grows, `done` closes the stream and
releases the panel, cancel posts `{job_id}` and relabels, a second click never starts a second job);
three older criteria that described the single-POST behaviour were **updated, not deleted**
(`test_web_jobs.py` cache + batch wiring, `test_web_failure_states.py`'s status-line location).
Falsified: removing the stream opening turns the static criterion and the batch harness red (2 failed),
and restoring it returns 12 passed.
## Verified in a real browser (2026-09-19)

Chrome DevTools MCP over the local bridge, the app started on a **copy** of the dataset
(120 images staged in a scratch directory, so a write could not touch the real one):

| Step | What the browser showed | What the server logged |
|---|---|---|
| Preview | the diff table filled | `POST /api/captions/batch` 200 (the synchronous preview, unchanged) |
| Apply + confirm dialog | 确认操作 / "确认把改动写入这 120 张图？…" / 确认并写入 | `POST /api/captions/batch/run` **202 Accepted** |
| Running | 正在写入 120/120 · 当前：…\img119.jpeg, the bar at 100% | `GET /api/captions/batch/stream?job_id=…` **200** |
| Done | toast "已写入 120 张，失败 0 张"; the outcome table built from the stream | - |
| Disk | all 120 captions are `a` (normalized) | - |

The cancel path was not exercised there: 120 tiny images finish in well under a second, so by the
time the cancel was clicked the job had already emitted `done` and hidden the button - the server log
confirms **no `/cancel` request** was sent. Cancel is covered deterministically at the worker level
(`test_batch_jobs.py`) and at the API level with invariants (A39).

**One wart the browser run exposed:** a successful write's table row showed "无 → 无", because the
write entry carried only `_write_payload` while the preview carries `before`/`after`. Fixed in
`_batch_step`: both values are already in hand there, so the write table now renders the same diff the
preview promised.
## What is left

The frontend still uses the synchronous endpoints: the batch panel needs the apply button to start the
job and paint `done/total` + the current file from the stream with a Cancel button, the cache panel the
same for "rebuild all", and both need the terminal copy that says how far it got ("已写入 N 张，取消于
第 M 张" rather than an undo that does not exist). All three packs need the new strings.

---

Related: [changelog/long-job-visibility](long-job-visibility.md), [p0-spec](../p0-spec.md)