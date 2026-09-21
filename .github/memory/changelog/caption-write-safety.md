---
name: caption-write-safety
description: 2026-09-19, a failed caption write no longer loses the typing: flush reports failure, activateImage refuses to navigate, the error stays on screen, and leaving raw mode asks before truncating
metadata:
  type: topic
---

# Caption write safety (2026-09-19)

Three ways an edit could disappear without the user agreeing to it, all three found by the 2026-09
UX and accessibility audits (see gap 15 in [current-state](../current-state.md)) and fixed together
because they share one rule: **the editor is the only copy of what the user typed, so nothing may
silently reload it.**

## 1. A failed write no longer lets the next image load over it

`activateImage` awaited `tagEditor.flush()` and then loaded the next image regardless. When the PUT
failed - a read-only `.txt`, a permission problem, a restarted server, a full disk - the editor was
reloaded from disk and everything typed was gone; the only trace was a toast that disappears and an
error line the reload wiped.

`flush()` now answers `"clean"` (nothing to write), `"saved"` or `"failed"`, and `activateImage`
stops on `"failed"`: it asks whether to leave, and declining puts the gallery marker back on the
image still being edited (`gallery.setActive(current, false)` - without re-announcing, or the guard
would re-enter itself). Staying keeps the text **and** the error state, so the user can fix the
cause and press Save again.

The open preview keeps the same guarantee by construction: it flushes and disables the sidebar
editor, and closing it calls `activateImage` again, which hits the same guard.

## 2. The failure stayed on screen for one frame

The catch branch painted `is-error` with the message, and the trailing `paintState()` - added so a
change typed during the request is not lost - immediately repainted the dirty state over it. The
message was therefore never readable in the editor. `saveError` now holds the failure until the next
successful write (or the next `load()`), and `paintState()` renders it before the dirty branch.

**This one was found by the harness, not by reading**: the first version of the test asserted the
error class after a failed flush and went red.

## 3. Leaving raw text mode asks before deleting lines

The chip editor can only represent one line, so leaving raw mode re-parsed the buffer from its first
line - dropping everything below, and `paintWarning()` then saw no newline and hid the multiline
warning in the same tick. The autosave wrote the truncation shortly after. The trainer ignores those
lines, but they are still the user text.

`wouldDropLines(text)` (exported, pure) decides whether anything of value would be lost: trailing
blank lines are not loss. When it returns true the editor asks first, and declining puts the toggle
back and leaves both buffers untouched.

## Criteria and falsification

`test/test_web_tags.py` (new, 5 criteria) pins the wiring statically and drives the real editor
through `test/fixtures/tags_harness.mjs` (fake DOM, stub API, no jsdom): a failed write is reported
and the text survives, a retry clears the error, a clean editor reports `clean`, declining the
raw-mode exit changes nothing, accepting keeps the first line, and trailing blank lines never ask.

Falsified rather than assumed: making `flush()` always answer `clean` turns the static criterion and
the harness red; deleting the raw-mode guard turns both red as well. The file is byte-identical after
restoring, and both runs are green again.

    .venv\Scripts\python.exe -m pytest test/ -q    816 passed, 2 skipped (2026-09-19)

## What is still open from the same audit

Batch write and "rebuild all caches" are still silent single POSTs, a failed **load** still leaves
the previous listing on screen, the scale cancel can still stick, dialogs still neither trap nor
restore focus, the frequency table is still mouse-only, and the tab strip still has no
`aria-selected`. They stay in gap 15 of [current-state](../current-state.md).

---

Related: [ui-copy](../ui-copy.md), [encoder-cache-invalidation](encoder-cache-invalidation.md)
