# The filter conditions next to the grid they narrow

**When to read this**: changing the gallery toolbar or the center panel's layout, the
filter panel's chips and count, or anything that decides what the grid shows and why.

## Why (2026-09-20)

The tag filter's *controls* and its *state* were on different screens. The frequency
table, the search box, the two layer checkboxes, the active-condition chips
(`#filter-active`) and the count line (`#filter-count`) all lived on the right column's
Filter tab, while the grid they narrow lives in the center panel. So when the gallery
showed 3 of 691 images, the two things a user needs - **why** and **the way out** - were
both behind a tab switch; the center panel offered only a "clear all" button, and only
once the grid was already empty.

Two conditions were worse than that, because no chip named them at all:

- **"Show selected images only"** narrows the grid exactly like a tag does
  (`apply()` skips unselected paths) and had no chip and no count wording anywhere.
- **Missing-caption mode** narrowed the grid and was named only as a prose prefix on the
  count line (`"<mode> / 3 of 691"`) - the same fact told twice in two places once a chip
  exists for it.

## The decision

- **Relocated, not mirrored.** `#filter-active` and `#filter-count` moved out of the
  Filter tab into a new `#gallery-filter` strip in the center panel, directly above
  `#gallery`. A second, mirrored copy was rejected for the reason this repository keeps
  hitting: two live DOM copies of one state drift, and the drift is invisible. The Filter
  tab keeps the controls that *choose* conditions; the strip is the only rendering of the
  conditions *in force*.
- **The strip is a sibling of `#gallery`, never a child.** The grid is rebuilt with
  `replaceChildren()` on every navigation, so a strip inside it would be wiped by the
  next directory change.
- **One painter, three kinds of condition.** `paintActive()` (`filter.js`) builds one
  chip per condition: the tag tri-state, missing mode, and selection-only. Each chip
  carries `data-condition`, and the strip's one click delegate removes exactly that
  condition (`removeCondition`): removing the missing chip leaves the mode, removing the
  selection chip clears the checkbox it stands for, and neither touches the others.
- **The strip is on screen exactly while something narrows the grid**
  (`stripEl.hidden = conditions.length === 0`). It therefore never shows an idle "no
  filter applied", and never a meaningless "0 of 0 images shown" at a folder-only level -
  and no layout is spent on a row that has nothing to say. `filter.none` became dead copy
  and was deleted from all three packs.
- **The count line stopped naming the mode.** `paintCount()` lost its `modePrefix()`:
  with the mode one row away as a removable chip, prefixing the count with the same words
  is the same fact stated twice in adjacent lines, and the copy rules say to drop it when
  the control is already beside the message.
- **`#btn-gallery-clear-filter` stays (judgement call, see below).**

## Evidence

- `test/fixtures/filter_harness.mjs` really runs the panel over its fake DOM (which
  gained DOM-style event bubbling and `closest()` for this: the chips are painted by a
  *delegate*, so a click on a chip's remove button only arrives if the event bubbles).
  New criteria: no condition hides the strip; a tag condition shows it and clicking the
  chip's remove button clears the filter, hides the strip and gives every image back; the
  missing-mode chip carries `filter.modeMissing` and the count line is only the count;
  two conditions are two chips and removing one leaves the other (and unchecks the
  checkbox the selection chip stands for); entering a tag filter still leaves missing
  mode; a failed frequency load leaves the conditions (and the strip) exactly as they were -
  the table failing is not the filter failing, and a strip that emptied itself there would
  quietly widen the grid; `relabel()` repaints the chip through a locale switch and back.
- `test/test_web_filter.py` (4 new criteria, 1 extended): the strip is on the grid's
  panel and **not** inside `#gallery` (read through an `html.parser` nesting walk, not a
  substring), each host exists exactly once and not on the Filter tab; the CSS declaration
  that can actually hide the strip (the file's own `[hidden] { display: none !important }`,
  which is what outranks `#gallery-filter`'s own `display`) is asserted as a
  *declaration*; `activeEl.replaceChildren()` appears exactly once and all three kinds of
  condition are built there; `app.js` hands `strip: $("gallery-filter")` over; no pack
  keeps `filter.none`.
- **Falsified 15 ways** (break -> red -> restore -> green, each observed):

  | break | red |
  |---|---|
  | `stripEl.hidden = false` | harness: "a panel with no condition must not show the strip" |
  | the missing-mode condition is dropped | harness: "missing mode must show the strip" |
  | the missing-mode chip carries `filter.onlySelected` | harness: "the mode chip must carry the mode's own label, got 只显示选中的图" |
  | the selection condition is dropped | harness: "two conditions must be two chips, got 1" |
  | `removeCondition` stops unchecking the box | harness: "the chip and the checkbox it stands for must not disagree" |
  | `paintCount` prefixes the mode again | harness: "the count line must not restate the mode: 只看缺失 caption / 当前 1 张 / 共 3 张" |
  | `paintActive()` dropped from `relabel()` | harness: "relabel() must repaint the mode chip, got 只看缺失 caption" |
  | removal ignores `data-condition` (the pre-round handler) | harness: "removing the mode chip must leave the mode" |
  | `app.js` hands `strip: $("filter-active")` | `test_app_hands_every_control_to_the_panel` |
  | `[hidden]` loses `!important` | `test_the_strip_can_actually_be_hidden` |
  | a second `activeEl.replaceChildren()` in `paintFrequency()` | `test_one_painter_owns_the_strip` |
  | a pack gets `filter.none` back | `test_no_pack_keeps_the_wording_of_an_empty_strip` |
  | the strip nested inside `#gallery` | `...is inside #gallery: ['center-panel', 'gallery']` |
  | the whole strip moved back onto the Filter tab | `...#gallery-filter is not on the grid's panel: []` |
  | a failed frequency load also clears the conditions | harness: "a failed frequency load must not drop the conditions the grid is under" |

  Two findings came out of writing these criteria, and both were about the fixture rather
  than the code: the fake DOM had no event **bubbling**, so a click on a chip's remove
  button never reached the delegate (the first red pointed at the fixture, and every
  removal criterion depended on it), and `cycle()` on a tag leaves missing mode *by
  design*, so the "two conditions are two chips" pair has to be the two modes
  (`missing` + `selection`), which do stack in `apply()`.

## Judgement calls the parent should confirm

1. **`#btn-gallery-clear-filter` was kept.** It is not a second rendering of the strip -
   it holds no state, so it cannot drift - and it does something the strip does not: one
   click clears *every* condition, where the strip removes one per click. It sits at the
   dead end where the user discovers the empty grid, which is the "carry the control"
   rule from [ui-copy](../ui-copy.md). If the parent disagrees, removing it means: delete
   the button from `index.html`, the `clear.hidden` lines from `paintEmptyState()`, the
   `addEventListener` line, and rewrite `test_web_gallery.py::test_the_filtered_empty_state_offers_the_way_out`
   into a criterion that the strip is the way out.
2. **"Show selected images only" was made a condition** although the task named only
   missing mode. Without it the strip makes the claim "this is what is filtering the
   grid" while one of the three conditions is missing from it - the same defect one size
   down. It reuses the checkbox's own words (`filter.onlySelected`), so no new copy.
3. **The strip is hidden while nothing filters**, rather than always visible. The
   alternative was tried on paper and rejected: at a folder-only level (the dataset root,
   219 folder cards) an always-visible strip would read "0 of 0 images shown" above cards,
   which is the same lie `paintEmptyState()` already avoids. The cost is a ~28 px layout
   shift the moment the first condition is applied - at which point the grid is being
   re-rendered anyway.

## Known limits

- **Never seen in a real browser** (the same class as gap 5 in
  [current-state](../current-state.md)). The strip's height, its wrapping with several
  long tag chips, and the "0 of N" line on a short window rest on CSS and a fake DOM.
  The hiding itself is verified in two layers (the property in the harness, the
  `!important` declaration statically) but not as pixels.
- The count line is **not** a live region: a screen reader still learns about a filter
  change from the frequency row it just pressed, not from the strip.
- The strip shows the selection chip only while "show selected images only" is on; the
  selection itself stays in the toolbar's own count.
- A filter survives a directory change, so the strip (and its chips) stay on screen at a
  folder-only level. That is deliberate - the filter really is still in force for the
  next directory - but it does mean a "0 of 0" count can be on screen next to folder
  cards.

---

Related: [missing-caption-mode](missing-caption-mode.md), [caption-dock](caption-dock.md),
[ui-copy](../ui-copy.md), [i18n](../i18n.md)
