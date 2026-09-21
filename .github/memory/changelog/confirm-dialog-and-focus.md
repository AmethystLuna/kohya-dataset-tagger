---
name: confirm-dialog-and-focus
description: 2026-09-19, an in-app confirmation dialog replaced window.confirm everywhere (naming the action and defaulting to no), root and model-directory removal finally ask, every dialog hands focus back, and images are named once
metadata:
  type: topic
---

# The confirmation dialog, focus, and naming images once (2026-09-19)

The last of the 2026-09 audit findings: the app confirmed the reversible and stayed silent about the
irreversible, and the browser was doing the asking.

## 1. `window.confirm` is gone

Four call sites used the browser dialog, and this repository had already written the copy for a real
one. Why it had to go: its buttons come from the **browser** UI language (a Chinese interface showed
"OK / Cancel"), the body cannot name the action, and a destructive question looked exactly like a
harmless one. At the same time the single genuinely irreversible edit in the app - removing a root
directory from `roots.txt`, a user-authored file - asked nothing at all.

`web/js/confirm.js` builds the dialog per call: a title, a body and **the label of the action button**
(`Rebuild`, `Write captions`, `Delete root`). Because it is built per call it reads the current locale
and needs no relabel registration. It resolves `true` only for the action button - Escape, the cancel
button and a click on the backdrop all mean no, and a click inside the dialog does nothing. A
dangerous question focuses **Cancel**, so an accidental Enter cannot delete anything, and a second
question supersedes the first (which resolves false). Focus is remembered and handed back.

Converted: cache rebuild (one and all), batch apply, the WD14 run, the raw-text-mode truncation, the
"could not save, leave anyway?" guard in `activateImage`, and two new ones - removing a root and
removing a model directory. The panels keep their `confirm` seam; it is simply asynchronous now, and
each call site says what the action is.

## 2. Every dialog hands focus back

The picker already did (previous batch). The zoom viewer captured nothing, so closing it dropped
focus on `<body>` - in a grid of hundreds of tiles that is where you were. The model-roots dialog had
the same hole. Both now remember `document.activeElement` on open and restore it on close.

## 3. An image is named once

- tiles set `alt` to the file name **and** render a `figcaption` with the same name, so every tile was
  announced twice: the image is decorative now and the caption carries the name;
- the enlargement announced the whole absolute path while the path is already on screen: it announces
  the base name instead;
- the diff tables attributed a row to a file only through a `title` on the `<tr>`, which a screen
  reader never reads: each image cell now carries a visually hidden file name.

## 4. Dead copy removed

`app.retry`, `app.dismiss` and `common.confirm` had been sitting unused since the copy pass; the first
two are now the error toast buttons and the third is the dialog default. The four that are still
unused were deleted from all three packs together (`common.networkError`, `gallery.activeName`,
`autotag.scopeEmpty`, `autotag.running`) - the contract test keeps the packs in step.

## Criteria and falsification

`test/test_web_confirm.py` (new, 5 criteria) pins the wiring and runs the dialog for real through
`test/fixtures/confirm_harness.mjs`: the action button names the action, a dangerous question focuses
Cancel, the three ways of saying no all resolve false, a click inside does not, the listener and the
nodes are removed, focus goes back, and a second question supersedes the first.
`test/test_web_a11y.py` grew two criteria (every dialog restores focus; an image is named once).

Falsified rather than assumed - and one of them earned its keep: the first "tile is announced twice"
criterion asserted that `gallery.js` contains `img.alt = "";`, which is **also** true of the folder
card thumbnails, so it stayed green while the tile still repeated its caption. Falsifying it is what
exposed that, and the criterion now pins the tile line together with its neighbouring attribute.

    .venv\Scripts\python.exe -m pytest test/ -q    835 passed, 2 skipped (2026-09-19)

## What is left from the audit

Only the two items that need backend work: the batch write and the cache rebuild are still single
POSTs without server-side progress or a cancel (they want the job + SSE treatment `scale`/`autotag`
have), and directory scans and `dataset.toml` generation still have no busy state. Everything else in
gap 15 of [current-state](../current-state.md) is closed.

---

Related: [ui-copy](../ui-copy.md), [changelog/keyboard-and-dialogs](keyboard-and-dialogs.md)
