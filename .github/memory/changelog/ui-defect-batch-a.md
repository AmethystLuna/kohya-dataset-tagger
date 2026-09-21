---
name: ui-defect-batch-a
description: 2026-09-19, the panel-level defect batch (no contract change) - a stale diff, buttons that can only fail, a cancel that cannot be retried, and two failures that looked like empty success; and the one audit finding that was withdrawn after the preview's semantics were questioned
metadata:
  type: topic
---

# Panel defects that made "broken" look like "fine" (2026-09-19)

## Where this came from

After the copy audit ([ui-copy-audit](ui-copy-audit.md)) the wording was in order, so two structural audits
were run over the frontend (the tagger/export/cache panels; the gallery/selection/navigation surfaces).
They returned 24 findings. **None was taken on trust**: each was re-read in the source before it was
accepted, one had to be downgraded (a failed cache cancel locks the button until the job ends, not
forever - `finishCacheJob` does clear the flag), the rest held, and **one was withdrawn after it had
already been implemented** - the note below. Reading the source is not the same as knowing what a surface
is *for*.

This batch is the subset that **changes no contract**: no endpoint, no request or response shape, no
written byte. It is the "visible status / prevent > repair / user control" family in its cheapest form.

## What was wrong, by principle

**Visible status - the screen stated something nobody had checked**

- **Withdrawn: "the preview's after column is wrong for four of the six write modes."** The audit read
  the diff as a simulation of the write; it is a review of the tagging. `/preview` does ignore
  `write_mode`, and that is the design: §4.5 ① lists the preview's request body *without* the field,
  `AutotagRequest` is shared with `/run` (which is the reader that needs it), and the mode only decides
  how the run later composes the tagger's output with what is on disk. The change that came out of this
  finding made things worse, not better: with `writeMode` in `signature()`, switching the mode claimed
  设置已改动，请重新预览 and (at a scope of 32 images or fewer) disabled Run behind a re-preview whose
  output cannot differ - **a false alarm is the same defect as a missing one.** The signature member, the
  mode note and its key are gone. What survives is the explanation, in `signature()` and in
  `AutotagRequest`, so the next reader does not re-find it as a defect.
- `refreshDiffStale()` compared `previewSignature !== signature(preview.paths)` - **re-signing the
  preview's own paths**, which a directory or selection change never disturbs. Change the directory and
  the old diff stayed on screen describing other images. -> `previewCoversScope()` compares the real
  scope, the two causes get their own copy (`autotag.diffStale` / `autotag.diffStaleScope`), and the
  warning is re-derived rather than trusted whenever the table is reprinted.
- A lost cache stream repainted the pre-rebuild rows as if they were current (`app.js`), while an unknown
  number of caches had already been deleted. -> the list is dropped, `cacheFailed` marks it unverified and
  the status is re-read.
- A failed model listing left the dropdown and list **blank** - indistinguishable from "nothing is
  installed" once the toast retired. -> `modelsFailed` (the same device as `cacheFailed`) keeps a line
  and a retry in the list.
- The scale summary read `writeToml.box.checked` **at paint time**: ticking the box mid-run reported a
  finished job as a failed `dataset.toml` write. -> the run remembers what it asked for
  (`jobWroteToml`).

**Prevent > repair - controls that could only fail**

- An over-cap scope left the preview button enabled; the click could only produce a 400-toast. -> the
  button is disabled and the reason is on screen (`autotag.previewTooManyNote`), because `/run` is the
  path for a whole directory.
- A preview that found nothing re-armed Run in the same tick (`refreshActionButtons()` in `finally`),
  so Run was lit and answered only `autotag.previewEmpty`. -> the empty rule lives inside
  `refreshRunButton()`.

**User control - a cancel you cannot take back**

- The cache panel set `cacheCancelling` and, when the cancel request itself failed, never handed the
  button back. The other two panels arm a 30 s watchdog; this one had none. -> `CACHE_CANCEL_WATCHDOG_MS`,
  the failure branch resets the state, and `closeCacheStream()` disarms it.
- An accepted cancel looked exactly like a running job for up to 30 s while the backend finished the
  current image. -> `paintCancelButton()`: "cancelling", disabled, until a terminal path clears it - and
  `relabel()` repaints it so a language switch cannot leave the old state behind.

**Recognition > recall**

- The scale panel takes its `caption_extension` from the shared store (`trainparams.js`), which was only
  editable on the export tab - the sidecar names of a run were decided on another screen. -> the field is
  now editable in the panel, through the same store (and therefore shows up on both).

## Criteria

- `test/test_web_jobs.py`: the empty preview does not re-arm Run; an over-cap scope disables Preview and
  says why; a cancel in flight says so and keeps its lock; the cache cancel can be retried and is armed.
- `test/test_web_failure_states.py`: a failed model list is not an empty model list; a lost cache stream
  is not reported as a fact.
- `test/test_web_diff.py`: the diff stops describing a scope it no longer covers (and is not rebuilt per
  probe); and the write mode is **not** allowed back into the preview signature - a negative criterion,
  which is what this finding deserved.
- `test/test_web_scale.py`: the done report reads the request, not the checkbox; the caption extension is
  editable where the run starts.
- Five new keys (`autotag.diffStaleScope`, `autotag.previewTooManyNote`, `autotag.cancelling`,
  `autotag.modelsLoadFailed`, `cache.cancelTimeout`) exist non-empty in all three packs.

**Falsification found a criterion that tested nothing**: breaking `previewTooLarge()` to `return false`
left its test green, because the test pinned the *call shape* and not the cap. It now pins the function
body, and goes red when the cap stops being read. Four more breaks (modelsFailed flag, stream re-read,
the two staleness causes, the cache handback) each turned exactly the expected test red; all were
restored green - and the withdrawn change above is the reminder that a criterion can be green, falsifiable
and still be pinning the wrong thing.

## Deliberately not in this batch

- Nothing of the withdrawn finding: `/preview` keeping its own semantics is not a gap to close.
- The export path: `api/scale.py` writes `<target>/dataset.toml` with an atomic overwrite and **no
  existence check and no backup**, while the same job refuses to overwrite an image
  (`scaler.unique_destination`) - and the panel never asks. That is a contract change and it is the
  largest remaining "irreversible without a net" gap.
- The gallery findings (a missing-caption selection that spans the whole directory while "select all"
  spans the filtered view; filtering silently deleting the selection) need a decision about what a
  selection means, not a silent fix.

---

Related: [p0-spec](../p0-spec.md), [ui-copy](../ui-copy.md), [i18n](../i18n.md),
[changelog/failure-states](failure-states.md), [changelog/long-job-visibility](long-job-visibility.md)
