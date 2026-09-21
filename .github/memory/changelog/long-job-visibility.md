---
name: long-job-visibility
description: 2026-09-19, long operations say they are running: the cache rebuild is single-flight, a batch write locks the panel and keeps its outcome table, and the scale cancel reads the answer and arms a watchdog
metadata:
  type: topic
---

# Long jobs say what they are doing (2026-09-19)

Three findings from the 2026-09 UX audit, all the same shape: the app started something slow and
then looked exactly like an app that had done nothing. The autotag panel is the reference
implementation (phase switch, per-item progress, error list, cancel with a watchdog); these are the
places that never got it.

## 1. "Rebuild all caches" is single-flight and says so

`rebuildCaches()` fired one POST covering every stale entry with **nothing disabled**: a second click
started a second rebuild, and the panel gave no sign that anything was happening. It now holds a
`cacheBusy` flag, paints `cache.rebuilding` in the status line, disables the rebuild-all button and
every per-row button, and refuses a re-entrant call - released in a `finally` that also re-reads the
status, so a failed rebuild cannot leave the panel looking busy forever.

The rebuild is still one POST with no server-side progress or cancel (that needs the job + SSE
treatment `scale` and `autotag` already have); what changed is that the user can see it, cannot
double-fire it, and is told when it finished.

## 2. A batch write locks the panel and keeps its outcome table

A batch write is also a single POST. The apply button now doubles as the status line - its label is
painted from the `writing` state through the panel painters, so a language switch during a write
repaints it correctly instead of freezing it - and `refreshButtons()` locks both the apply and the
preview button while it runs. `runApply()` also returns early when `writing` is set: a disabled
button already blocks a human, but not a second programmatic click.

The write result used to be followed by `invalidatePreview()`, which cleared the diff table. The
preview is indeed stale after a write, but **the write own result is the only place a failed image
can be located**, so the table is now repainted from the write response (falling back to clearing it
when the response carries no `results`).

## 3. A refused cancel no longer locks the export panel

`cancel()` discarded the answer from `/api/scale/cancel`. When the server said `cancelled: false`
(the job was already gone) no terminal event would ever arrive, so the panel stayed locked with
Start and Cancel both disabled and no way out except a page reload. Now:

- `cancelled: false` closes the stream, unlocks the panel and says the job had already finished;
- an accepted cancel arms the same 30 s watchdog the autotag panel uses, which unlocks the panel and
  says the job may still be running rather than waiting forever;
- a cancelled job no longer paints a 100% progress bar - it keeps the last value the user was
  watching, because a full bar claims the work was completed.

## Criteria and falsification

`test/fixtures/batch_harness.mjs` and `test/fixtures/scale_harness.mjs` grew the behavioural cases
(a held response shows the writing label and the lock, a second click fires no second POST, the
outcome table survives; a cancelled job keeps its real progress, a refused cancel unlocks the
panel). `test/test_web_jobs.py` (new, 4 criteria) pins the wiring the harnesses cannot reach,
because the cache panel lives in `app.js`, which touches the DOM at module scope and has no
headless harness.

Falsified rather than assumed: forcing `accepted = true` turns the scale harness red, removing the
lock from `setWriting()` turns the batch harness red, and dropping the `cacheBusy` guard turns the
job criterion red; all three are green again after restoring.

    .venv\Scripts\python.exe -m pytest test/ -q    820 passed, 2 skipped (2026-09-19)

## A process note worth keeping

The first attempt at falsifying the `cacheBusy` guard round-tripped the whole file through the
read tool, whose default cap silently returned the first ~1260 lines; writing it back truncated
`app.js` by 500 lines. `git diff --stat` caught it immediately and `git checkout --` restored the
committed version (the only uncommitted work in that file was the guard itself, re-applied by hand).
**Falsify with an edit and its inverse, never with a whole-file read/write round trip** - the
repository already warns that reads can be lossy; this is what that costs.

## What is still open from the same audit

The batch write and the cache rebuild still have no server-side progress and no cancel (they need a
job id + SSE, like `scale`/`autotag`); a failed **load** still leaves the previous listing on screen;
the dialogs still neither trap nor restore focus; the frequency table is still mouse-only; and the
tab strip still has no `aria-selected`. They stay in gap 15 of [current-state](../current-state.md).

---

Related: [ui-copy](../ui-copy.md), [changelog/caption-write-safety](caption-write-safety.md)
