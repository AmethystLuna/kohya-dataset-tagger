---
name: failure-states
description: 2026-09-19, failures stop looking like success: a failed load clears the previous directory subject, errors are sticky with retry and dismiss, and the connection pill re-checks itself
metadata:
  type: topic
---

# Failure states (2026-09-19)

Three findings from the 2026-09 UX audit, all the same shape: **a failure was indistinguishable from
success**, so the user acted on data that no longer described what was on screen.

## 1. A failed load clears what it was going to replace

`loadCacheStatus()` and `loadFrequency()` only notified on failure. The cache tab kept listing the
previous directory caches (with its old status line), and the frequency table kept the old rows and
the old counts - "could not ask" looked exactly like "nothing to report".

- the cache panel gained a `cacheFailed` flag: the list is cleared, the status line says the load
  failed instead of printing a count, the "no stale cache" note is suppressed (it is a different
  statement), rebuild-all is disabled, and a successful load clears the flag;
- the filter panel gained `setFrequencyFailed(message)`: rows, scope counts and visible-scope data are
  dropped and the note carries the reason. A later `setFrequency()` clears it. The panel keeps that
  state internally so a search keystroke cannot repaint "no frequency data yet" over it;
- both notify calls now offer a **Retry** that repeats the load.

## 2. Errors are sticky, and they carry the actions that already existed

`notify()` removed every toast on a timer (12 s for errors) and offered nothing to press. Now an
error stays until dismissed - the Dismiss button uses `app.dismiss`, and a caller that can repeat the
action passes a retry callback that becomes the `app.retry` button. Both keys had been sitting unused
in all three packs since the copy pass. Non-error kinds still retire on their own after 6 s.

Because errors accumulate, `.toasts` gained a ceiling (`max-height: 60vh; overflow: auto`) - the
same audit had flagged that a directory full of broken thumbnails could stack hundreds of boxes over
the page.

## 3. The connection pill re-checks itself

`checkHealth()` ran at boot and on a language switch, and never again: the pill kept claiming
"Service connected (version …)" after the backend died, while every action failed with a toast that
vanished. It now polls every 10 s (`HEALTH_POLL_MS`), which also refreshes the loopback capability
that gates the picker.

## Criteria and falsification

`test/test_web_failure_states.py` (new, 4 criteria) pins the wiring statically - sticky errors with
both buttons, the toast ceiling, the health interval, the cache failure state, and the frequency
failure reaching the panel. The behavioural half is in `test/fixtures/filter_harness.mjs`: a failed
load empties the table, shows the reason and clears the scope counts, and a successful load clears
that state.

Falsified rather than assumed: making errors auto-dismiss again -> red; dropping the cache list reset
-> red; removing the `setFrequencyFailed` call -> red; all green after restoring (each with an edit
and its inverse).

    .venv\Scripts\python.exe -m pytest test/ -q    828 passed, 2 skipped (2026-09-19)

## 4. A directory scan says it is running (same day, follow-up)

`openDir()` awaited `api.listDir` with nothing on screen changing: on a large tree the click looked
ignored, and a slow answer looked like an empty directory. The directory list, the gallery and the
breadcrumb now dim while the request is in flight and carry `aria-busy="true"`, released in a
`finally` so an early return cannot leave them stuck. That closes the last frontend half of item 4.

## What is still open from the same audit

Batch write and the cache rebuild were later given a visible, single-flight in-flight state, but they
still have **no server-side progress and no cancel**: that needs a job id plus SSE, the treatment
`scale` and `autotag` already have, and it is backend work rather than a frontend fix. It is the one
item left in gap 15 of [current-state](../current-state.md). Everything else this changelog listed as
open (the browser dialog, focus, alt text, the dead copy keys) was closed in
[changelog/confirm-dialog-and-focus](confirm-dialog-and-focus.md).

---

Related: [ui-copy](../ui-copy.md), [changelog/long-job-visibility](long-job-visibility.md)