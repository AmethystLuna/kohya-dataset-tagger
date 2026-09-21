---
name: keyboard-and-dialogs
description: 2026-09-19, the filter table became keyboard operable, chips and tabs got real accessible names and state, and the directory picker took ownership of the keyboard while it is open
metadata:
  type: topic
---

# Keyboard and dialogs (2026-09-19)

Four findings from the 2026-09 accessibility audit: controls that exist but only for a mouse, or
only for people who can see them.

## 1. The frequency table is operable from the keyboard

Rows were `<span class="freq-item">` with one click delegate on the list. The tri-state cycle is the
only way to narrow the image set, so a keyboard user could browse and edit but never filter. Rows
are now real `<button type="button">` elements (the click delegate still works through bubbling),
with the chip look kept by resetting the user-agent chrome and a `:focus-visible` ring added.

The tri-state itself was a colour (and a strikethrough for exclude). Each row now carries a
visually hidden `sr-only` span with its state - `filter.only` / `filter.include` / `filter.exclude`
or the new `filter.stateOff` for an unfiltered tag - so the accessible name reads "1girl 42, only".

## 2. Chips say which tag they remove

The visible glyph is `×`, and **content beats `title` in the accessible-name computation**, so every
chip remove button was announced as "times" in the tag editor, the filter chips and the batch panel.
They now carry `aria-label` = the same string as the tooltip ("remove 1girl").

## 3. The tab strip is a real tablist

`index.html` declared `role="tablist"` / `role="tab"` and stopped there: no `aria-selected`, no
`aria-controls`, no `role="tabpanel"`, no roving tabindex, no arrow keys - and the active tab was a
background colour. `setupTabs()` now wires the whole pattern for the four static tabs *and* the
tabs that `batch`/`export`/`scale` append at construction:

- every tab gets an id and `aria-controls`, every panel an id, `role="tabpanel"` and `aria-labelledby`;
- `aria-selected` and a roving `tabindex` follow the selection;
- Left/Right move between tabs, Home/End jump to the ends, and the newly selected tab takes focus;
- the tab marked active in the markup is painted on boot **without** running the show hooks;
- the active tab gained a weight change and an inset underline, so it is not colour-only.

## 4. The picker owns the keyboard while it is open

`open()` never moved focus, while the picker key handler listens on the **document**: focus stayed on
the page behind an `aria-modal` backdrop, and an Enter aimed at a button back there settled the
picker with the directory on screen - which the caller then adds as a root (`POST /api/roots`, a
persisted write). Now:

- `open()` remembers `document.activeElement` and focuses the path field;
- `settle()` hands focus back to whatever had it;
- the key handler ignores any event whose target is not inside the dialog - a stray key can no longer
  resolve it.

## Criteria and falsification

`test/test_web_a11y.py` (new, 4 criteria) pins the wiring statically - the frequency rows, the chip
labels, the tablist contract including the arrow-key expression, and the picker focus rules.
Behaviour is real: `test/fixtures/filter_harness.mjs` now asserts every row is a `BUTTON` carrying
exactly one spoken state, and `test/fixtures/picker_harness.mjs` asserts focus moves into the dialog
and that an Enter targeted at the page behind it does **not** settle the picker.

Falsified rather than assumed: rows back to spans -> 2 red; `aria-selected` removed -> 1 red; the
stray-key guard removed -> 2 red; all green after restoring. Restores were done with an edit and its
inverse this time - the previous round learned what a whole-file read/write round trip costs
([long-job-visibility](long-job-visibility.md)).

    .venv\Scripts\python.exe -m pytest test/ -q    824 passed, 2 skipped (2026-09-19)

## What is still open from the same audit

The zoom viewer and the model-roots dialog still do not restore focus (the picker is the one that
does), nothing traps focus inside a modal, the diff tables still attribute a row to a file only
through a `title`, `window.confirm` is still used for the cache rebuild and the WD14 write, and seven
copy keys (`app.retry`, `app.dismiss`, `common.confirm`, `common.networkError`, `gallery.activeName`,
`autotag.scopeEmpty`, `autotag.running`) still have no use site. They stay in gap 15 of
[current-state](../current-state.md).

---

Related: [ui-copy](../ui-copy.md), [i18n](../i18n.md)
