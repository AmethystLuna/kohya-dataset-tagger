---
name: picker-current-location
description: 2026-10-13 the directory picker shows where you are - a breadcrumb of clickable ancestors (§4.12) - and the dialog is relabelled on a language switch after all
metadata:
  type: topic
---

# The picker's current location, as a breadcrumb (2026-10-13)

The user's words: "show the current location in the modal directory picker - while jumping around,
an empty directory leaves you with no idea where you are".

The picker has had a **current-path box** since 2026-09-19, and that box is why the request read as
strange at first. But the box is a *control*, not a readout: it is where a path gets typed, and its
value is what "use this directory" resolves. A one-line input also shows the **beginning** of a long
path and hides the end - and the end is the part that says which directory this is. In an empty
directory the listing has nothing to say either, so what was left was `F:\ai-alchemy\data sets\...`
with the interesting half cut off.

## What changed

- A **location row** directly above the listing it names: the label `picker.location` (当前位置 /
  Current location / 現在の場所) and then one segment per ancestor, root first, cut by a local
  `pathCrumbs()` that handles `C:\a\b`, `C:/a/b`, `\\server\share\a` and `/a/b` (anything else comes
  back as one segment rather than inventing a root). It **wraps** instead of scrolling, which is the
  whole point, and it is local to `picker.js`: `navscroll.js` owns scroll semantics, not path spelling;
- **every segment above the last is a button**, so three levels up is one click instead of three
  clicks on 上一级. The **directory itself is plain text** marked `aria-current="true"` - clicking
  where you already are would be a no-op that looks like an action;
- the row is painted by `renderLocation()` from `render()`, i.e. on every navigation, so it cannot
  drift from the listing below it.

## The correction: the picker *is* relabelled

[picker-manual-input-and-mkdir](picker-manual-input-and-mkdir.md) recorded that the modal was
"deliberately not in the relabel list, because it covers the top bar and the user cannot reach the
language switch". That reasoning only covers switching **while the dialog is open**. What it misses is
the ordinary case: the user switches to English, then opens the picker - and every string in it is
still the language it was **constructed** in, because the picker is built once at module scope in
`app.js` and none of its copy is a `data-copy*` node. The command palette carries the same note and
solved it the same way ("a switch can still happen between opening and painting").

So `picker.relabel()` now re-paints the whole dialog - title, buttons, placeholders, hint, empty note,
the location label and its `aria-label` - and re-renders the listing, because each row's tooltip is
render-time copy too. The scroll position is saved and handed back, since re-labelling must not move
where the user is in the list. `app.js` registers it with `relabelComponent(picker)`, and
`test_web_i18n.py`'s list of components that must be registered unconditionally now includes `picker`
so the next construction-time string cannot quietly be left out. The rule in
[i18n](../i18n.md) names the picker for the same reason.

## Criteria and falsification

| Criterion | Location |
|---|---|
| The row exists, is labelled, splits paths, and marks the current segment | `test/test_web_reveal.py` |
| `picker.relabel()` exists, is exposed, and is registered by `app.js` | `test/test_web_reveal.py`, `test/test_web_i18n.py` |
| Behavior in a fake DOM: the segments read root → … → the directory; an **empty** directory still says where it is; an ancestor navigates; the current segment is not a button | `test/fixtures/picker_harness.mjs` (run by `test/test_web_picker.py`) |
| Contract | [p0-spec](../p0-spec.md) §4.12 "Frontend landing spots" |

Falsifications (each really went red, then was restored):

- Rendering the current segment as a button (dropping `aria-current`) → the harness exits 1 at the
  aria-current check **and** the static criterion turned red. This also caught a real defect in the
  new harness code: the failure message used Python's `%` formatting in JavaScript, so the assertion
  that was supposed to explain itself threw `NaN` instead. Fixed to string concatenation.
- Making an ancestor a plain `<span>` → the harness exits 1 at "an ancestor in the location row is not
  clickable".
- Removing `relabelComponent(picker)` from `app.js` → `test_web_reveal.py` and
  `test_web_i18n.py::test_every_dynamic_panel_is_relabelled_on_a_switch` both red.

## Known limits

- **Not clicked in a real browser.** The row's behaviour is asserted by the fake-DOM harness; the
  layout claim ("it wraps instead of scrolling") comes from the CSS rule, not from a measurement. The
  picker is not covered by `tools/measure_web_layout.py` - the same gap the picker has had since
  2026-09-18.
- A path with more segments than fit wraps onto a second line and the row grows; there is no
  "collapse the middle" behaviour (the classic `C:\ … \deep\path` form). The segments are ellipsised
  individually at 16em, so a single absurdly long directory name cannot push the row off the dialog.

## Related

- Contract: [p0-spec](../p0-spec.md) §4.12
- The dialog's other halves: [ui-picker-and-zoom](ui-picker-and-zoom.md),
  [picker-manual-input-and-mkdir](picker-manual-input-and-mkdir.md)
- The sibling action this round added: [open-in-file-manager](open-in-file-manager.md)
