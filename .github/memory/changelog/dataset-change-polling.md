# Following a dataset that changes outside the app

**When to read this**: touching the gallery's reload path, the boot timers in `app.js`, or anything that
assumes `state.images` only changes when the user navigates.

## Why (2026-09-20)

In-app writes were already live (`refreshCaption` / `reloadCaptions` end in `applyFilter()`), external ones
were not: there was no dataset watcher, so a dataset edited by the trainer or by hand stayed invisible until
the user hit refresh - and images added outside the app were not even in `state.images`.

## What it does

Boot starts a **poll**, not a watcher: the backend has no change notification, and inventing one would be a
second source of truth for "what changed". Every 15 s, with a directory open, the frontend asks
`/api/fs/list` and reduces the answer to a stamp - **image count + newest mtime**; an add, a removal, an
image edit and a caption write all move it, so the check costs one listing and **no new endpoint**. On a
change it re-lists through `openDir()` (the ordinary navigation path, so captions are re-probed and the
filter result follows) and says so (`gallery.datasetChanged`). The first sighting of a directory is the
baseline, never a change.

A reload is the thing that can do damage, so it is skipped when (1) **a job is running**
(`jobShelf.active()`: our own writes move mtimes every second), (2) **the tab is in the background**, or
(3) **a field has focus** (never reload under somebody's hands).

## Evidence

- `test/test_web_contract.py`: the poll exists, is started exactly once, all three guards and the
  announcement are present, and every pack can say the directory changed.
- **Falsified**: the job guard removed -> red; the focus guard removed -> red; restored -> green.
- Gates: unit suite green, docs gate green.

## Known limits

- The wait is up to 15 s, and a directory that is not open is not watched at all.
- The stamp cannot tell **who** changed the dataset: a change made while the user is mid-edit in the zoom
  view still triggers the re-list (the focus guard only covers text fields).

## Two things this round taught the tests (both were my mistakes)

1. **Never rebuild a file from `read` output.** The criteria were first added by reading
   `test/test_web_contract.py` and writing it back; the read output truncates very long lines, so 109
   lines and **5 tests** disappeared. Nothing failed: the damage showed up only as the suite total
   dropping. It was restored from `6a7c340` and the new criteria moved to their own file
   (`test/test_web_datasetchange.py`). Append with `edit`, or put them in a new file.
2. **A criterion that names a token occurring elsewhere cannot fail.** `assert "document.activeElement"
   in app` stayed green with the whole guard deleted, because the scale panel contains the same string.
   It now asserts the guard's own regex (`/^(INPUT|TEXTAREA|SELECT)$/.test(focused.tagName)`) and was
   re-falsified: removing the guard turns it red.

