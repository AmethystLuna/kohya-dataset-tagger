---
name: job-shelf
description: 2026-09-19, one shelf for every long job (web/js/jobs.js): the panels register the jobs they already own, so a running job is visible and cancellable from every tab - and the batch cancel gained the watchdog it was missing
metadata:
  type: topic
---

# One shelf for every long job (2026-09-19)

## The gap

Four panels start long jobs, and each of them painted progress **inside its own tab**:

| panel | state it kept | what was on screen elsewhere |
|---|---|---|
| tagger | `running`, `currentJobId`, `taskKind`, its own stream + watchdog | nothing |
| batch write | `writing`, `jobId`, `cancelling`, its own stream | nothing |
| cache rebuild | `cacheBusy`, `cacheJobId`, its own stream + watchdog | nothing |
| image export | `running`, `jobId`, its own stream + watchdog | nothing |

Nothing serialises them either - two jobs can run at once - so "which tab is busy" was not even a single
answer. Switching tabs hid a job that was still writing captions, and the only way to stop it was to
remember which panel had started it. The top bar reported server health and directory counts; the tab
strip had no busy marker.

## The change

**`web/js/jobs.js`** is a shelf, not a second state machine. A panel **registers the job it already
owns** and pushes the numbers it already paints:

    const row = shelf.start({ kind, label, cancelLabel, total, cancel });
    row.update({ done, total, current });
    row.finish();

One source of truth per job (the panel), one place that shows them all. A shelf that kept its own copy
would be a second thing to keep in sync, and it would be the wrong one.

- It sits **outside every panel** (a sibling of the layout, bottom left so it can never cover the toasts),
  which is the whole reason a tab switch cannot hide a job. The criterion asserts the position, because
  the position *is* the feature.
- A row names the workspace by its **existing tab label** (`tab.autotag`, `tab.batch`, `tab.cache`,
  `tab.scale`) and its own cancel label - so the shelf added **no copy at all**, and the words match the tab
  the user already knows.
- The label is a button that opens the owning tab (`openTab`, set by `setupTabs()`): "where do I look" has an
  answer that does not depend on remembering.
- Every panel closes its row in its **single job-end function** (`closeStream` / `closeWriteStream`), which
  every terminal path already goes through - done, a lost stream, a refused cancel, the watchdog. Closing
  the row anywhere else would leave a ghost exactly on the paths that skip it.

**A hole this exposed**: the batch panel had `cancelling` but **no cancel watchdog** (the tagger, the scale
panel and the cache panel all arm 30 s). A cancel the server accepted and never answered would have left a
shelf row forever - the shelf made a pre-existing gap visible, so it was fixed here: same constant, same
shape, plus `batch.cancelTimeout` in the three packs.

## Criteria

- `test/fixtures/jobs_harness.mjs` drives the shelf: an empty shelf is hidden, one job is one row, **two jobs
  are two rows** (a single "busy" flag would be a lie), `update()` moves the count and names the file, an
  unknown total reads as `?` rather than 0, cancel calls the panel's path **once**, the label opens the
  owning tab, and `active()` hands out copies rather than the live rows.
- Static criteria in `test_web_jobs.py`: the host is outside `<main>` and fixed; `createJobShelf` is
  created with it; `jobs: jobShelf,` appears **exactly three times** (one panel silently missing the shelf is
  the failure mode); the cache panel registers and repaints its row; and each panel closes its row inside
  its choke point.
- `test/fixtures/batch_harness.mjs` section 12: a running write has a row naming its workspace, the row
  follows the job's progress, the row's cancel reaches `/api/captions/batch/cancel`, and **both** endings -
  a normal done and a lost stream - remove it.

**Falsified five ways**, each red in the layer that should catch it: the row never closed (static choke
point + harness), the row never registered (batch harness), the cancel button not disabled after the
request (jobs harness), the host moved inside a panel (static), and one panel not handed the shelf
(static).

The harness caught a real defect in the shelf on its first run, and the fix went both ways: my assertion
was clicking a button that had already been **replaced by a repaint**, and the shelf's cancel was not
idempotent. The fixture now re-queries the button and asserts its disabled state, and `cancel()` is guarded
so a second call cannot fire a second request by any route.

---

Related: [p0-spec](../p0-spec.md), [changelog/long-job-visibility](long-job-visibility.md),
[changelog/caption-restore](caption-restore.md), [changelog/shared-scope-control](shared-scope-control.md)
