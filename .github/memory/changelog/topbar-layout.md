# Two rows, one grammar: where the browse scope, the refresh action and the theme switch belong

**When to read this**: changing the page header or the gallery toolbar, the position of the browse
scope (`#recursive-toggle`), of the refresh action (`#btn-refresh`), of the theme switch or of the
command bar, the census line (`#dir-counts`) and its breadcrumb row, the `.group-sep` separator, or
the top bar geometry recorded in
[readiness-strip-and-layout-probe](readiness-strip-and-layout-probe.md).

**Superseded in part (2026-09-20, the command-bar round).** The command entry became a **bar** and the
census line left this row for the breadcrumb's; every claim below that says otherwise is marked
where it stands, and the round that changed them is
[command-bar-and-census-row](command-bar-and-census-row.md).

## Why (2026-09-20)

The page header and the gallery toolbar each hold two or three *kinds* of control in one row, and
neither row said where one kind ended and the next began. Everything sat at the same weight with the
same 6-10px gap, so 含子目录 read as a sibling of 刷新, and 已选中 1 张 read as the continuation of the
checkbox label. The round gives both rows the same grammar, draws the one boundary that was missing,
and moves the controls that were in the wrong row:

- the **browse scope** (含子目录) left the header for the grid's own toolbar, because it changes what
  the GRID contains;
- the **refresh action** (刷新) followed it there, because the listing it re-fetches *is* the gallery's
  content;
- the **theme switch** moved out of the top-left to the row's right end, and later the **command
  palette's entry** moved past it to become the row's last control;
- the **header's separator was dropped** once the refresh action left: a boundary needs two sides.

## The two rows' grammar

| Row | Left | Then | Right end |
|---|---|---|---|
| Page header (`.topbar`) | identity: brand lockup, connection pill | | the two **preferences** (语言, 深色模式), then the row's last control, the **command bar** |
| Breadcrumb row (`.breadcrumb-row`, the centre panel) | the path (`#breadcrumb`) | | the **census** line (`#dir-counts`, right-aligned) |
| Gallery toolbar (`#gallery-toolbar`) | **scope**: 含子目录 | separator, then the **state** readout 已选中 N 张 | **actions**: 刷新 (icon), 全选, 选中缺失 caption, 取消选择 |

Both rows say the same thing: *what this is* on the left, *what state it is in* next to it, and *what
you can do* at the right end. What differs is which half needs a drawn boundary - the toolbar's left
half is a control and a readout, so 含子目录 / 已选中 1 张 read as one run of text without it; the
header's left half is a lockup and two readouts, and its right half is *all* controls, so a rule in
the middle of them would separate nothing from nothing. That is why exactly one `.group-sep` exists.

Three rules fall out of it:

1. **A control belongs to the row that owns its subject.** The listing's scope and its refresh are
   the gallery's, so they live on the row above the grid - the argument
   `#gallery-filter` already won ([gallery-filter-strip](gallery-filter-strip.md)). The page-wide
   entry (the palette reaches every tab), the language and the theme are the header's.
2. **Preferences sit next to the edge; the row's last control is the one you reach by going to the
   end.** The theme switch is the last *preference* and the palette's entry is the last *control*.
3. **One idea, one class.** The separator between groups is a single class `.group-sep` -
   decoration, so `aria-hidden="true"`, no text, nothing focusable (`flex: 0 0 auto`, 1px wide,
   18px tall, `var(--line)`) - and a criterion checks *every* use of that class, not the two places
   that motivated it.

## Why the browse scope went to the grid's toolbar (and not the left column)

The obvious home was the left column's 子目录 header - "put the toggle next to the numbers it
changes". It is wrong, and the contract says so: **`api/fs.py:5-12`** - "*dirs lists only direct
subdirectories, each with its own image count (non-recursive)*" and "*recursive=true affects images
and the captions statistics that follow; **dirs still stays one level***". A subdirectory's count is
never recursive, so 含子目录 changes none of the numbers in that column, and `browse.leftHint`
("子目录后的数字是它直接包含的图片数") stays true in both states. The hint was **not** reworded and no
copy key was added, because the copy was never wrong.

What the toggle actually changes - read from `app.js`, all five sites by id:

| Site | What it changes |
|---|---|
| `app.js:1005, 1013` | `openDir`: the **gallery listing** (`api.listDir(path, recursive)`) |
| `app.js:992` | the census line's own copy (`browse.counts` vs `browse.countsRecursive`) |
| `app.js:990` | `readiness.setCounts(counts)` - the same object, so the strip's caption segment moves with it |
| `app.js:1164-1167` | the tag-frequency scope (`api.tagFrequency`, and `filter.scopeDir`/`scopeRecursive`) |
| `app.js:1301-1303` | the cache status/list (`api.cacheStatus`) |

Four of the five are the grid and what is computed about it. The toggle cannot go *inside*
`#breadcrumb` or `#gallery` - both are rebuilt with `replaceChildren()` on every navigation, so a
control inside one is destroyed by the next click - and it must not go back into the action cluster,
where it would be one more peer of 全选/取消选择 rather than the scope those actions act within.

## Why the refresh action moved to the same row

刷新 re-fetches **the listing on screen**: `refreshBrowse()` re-runs the `/api/fs/list` call for the
open directory and repaints the gallery, the census line, the readiness strip's caption segment and
the left column's directory list **from that one answer**. The listing is the gallery's content, so
the button belongs to the gallery's row - and, since the same call already repaints the left column's
list, there is deliberately **no second refresh button** anywhere (written down so a reader does not
go looking for one).

**Icon-only, with the words kept.** Markup is the globe's pattern: a decorative
`<svg aria-hidden="true" focusable="false">` inside `<button id="btn-refresh" class="btn btn-icon"
data-copy-aria="browse.refresh" data-copy-title="browse.refresh">`. A glyph may never *become* the
accessible name (the chip-remove lesson in [keyboard-and-dialogs](keyboard-and-dialogs.md)), so the
control is still named and tooltipped by `browse.refresh` - the copy did not change and no key was
added.

## Why the header's separator was dropped

It was introduced in the same round, between 刷新 and the language, when the header still had an
action group with two members. Once 刷新 moved to the grid's row the header's right end held one
button and two labelled controls: a boundary between *a button* and *a select* separates nothing from
nothing, and the row's remaining controls are all simply "the ones at the right end". It was removed
and the class kept - the toolbar still needs it, and the criterion now checks the class rather than
the two places.

## The row that clips: why the toolbar wraps (found while measuring, fixed here)

Moving two controls into a 578px row has a failure mode that no static criterion sees, and the
measurement found it: **the row squeezed instead of wrapping**. `.toolbar` was a single-line flex
container, and `.panel-center` is `overflow: hidden`, so at a narrow centre panel the items shrank
past their content and then overflowed - and the overflowing buttons were **clipped out of reach**.

Measured, en, 900px viewport (centre panel 278px), before the fix: `#btn-select-missing`'s right
edge **75.4px** past the row's and `#btn-clear-selection`'s **158.7px** past it. At 2471b31 the same
row fitted (its last button ended exactly on the row's edge, 107.5px tall with its text wrapped), so
this was a regression the move introduced - in en/ja only, which is exactly the case the committed
probe cannot see (see the known limits).

The fix is one declaration - `.toolbar { flex-wrap: wrap }` - and it is now a criterion
(`test_web_filter.py::test_the_toolbar_wraps_instead_of_clipping_its_controls`, which also pins the
`overflow: hidden` that makes wrapping necessary; removing the wrap turns it red). What it costs,
measured, no clipping anywhere afterwards:

| viewport / locale | `.toolbar` before wrap | `.toolbar` after wrap | `#gallery` before | `#gallery` after |
|---|---|---|---|---|
| 1280 zh-CN | 29.5 | **29.5** (one line, unchanged) | 666 | **666** |
| 1280 en / ja | 49 | **65** | 646.5 | **630.5** |
| 900 zh-CN | 88 | **65** | 589.5 | **612.5** |
| 900 en | 107.5 (overflowing) | **130.5** | 512.5 | **489.5** |

Two rows of it are worth reading twice: in zh-CN at 900px wrapping makes the row *shorter* (65 against
88 - two clean lines beat three squeezed ones) and the gallery **gains** 23px; and at 1280 in en/ja the
cost is 16px of gallery height, paid to stop the buttons' own labels wrapping mid-word.

**The census line was considered for this row and rejected** - and the reason was the row, not the
census: `#dir-counts` did not fit next to 含子目录 (about 150px in zh-CN against roughly 120px of
slack, and more in en/ja).

> **Superseded 2026-09-20.** The census did move - not into the toolbar, but into the **breadcrumb
> row** of the centre panel, as a sibling of `#breadcrumb`: it counts the directory the breadcrumb
> names, and the header needed the room for the command bar. The half of the sentence that survives
> is the one about the readiness strip: the caption segment is still painted from the same `counts`
> object `renderCounts()` writes into `#dir-counts`, wherever that line lives
> ([command-bar-and-census-row](command-bar-and-census-row.md)).

## The honest record: the first arrangement was top-left because the requester said so

The light-mode round shipped the switch as the **first child of `.topbar`**, before the brand - not
because it was designed that way, but because the brief asked for "the literal top-left". The
requester withdrew that placement the same day, together with the criterion that pinned it
(`test_the_switch_is_the_top_bars_first_child`), and the row was re-arranged three times in one day:
the switch to the right end, the browse scope and the refresh action to the grid's row, the palette's
entry past the preferences to the row's last control. (A keycap shape for that entry was proposed and
withdrawn *before it shipped that day*; the next round shipped one - the entry is a 230.4px command
bar at 1280 whose keycap shows Ctrl+K, and `palette.open` was deleted from the packs with it. The 48px
measurement below is the *button's*, which is what this round's claim rested on; the bar's numbers are
in [command-bar-and-census-row](command-bar-and-census-row.md).)
**The lesson, which is why this file exists**: a placement handed to an implementer is still a design
claim, and it has to be checked against **what the control actually changes** before it ships. The
same test was run on the browse scope and it *failed* the obvious answer (the left column): the
toggle changes the grid and its derived numbers, not the subdirectory counts. A placement that is
handed to you and a placement you invent are the same kind of claim; only the second one usually gets
checked.

## Evidence

**A real browser, measured** - `tools/measure_web_layout.py`, headless Chrome, canned `/api/*`,
zh-CN profile. The round also added the header's controls, `#btn-refresh` and `#gallery-toolbar` to
the probe's `SELECTORS`, because "the row wraps without losing a control" is only a measurement if
each control has its own box.

`--theme both --shot DIR`, 1280x900 - **every number the theme round recorded is unchanged**:

| | 2471b31 (before) | this round |
|---|---|---|
| top bar | 89 | **89** |
| `.layout` (ends at the 900px viewport) | 811 | **811** |
| `.panel-left` / `.panel-right` | 260 / 380 | **260 / 380** |
| `.tabs` | 63 | **63** |
| `#caption-dock` | 346 | **346** |
| `#gallery` | 666 | **666** |
| `#gallery-toolbar` (the row that gained two controls) | 29.5 (measured as `.toolbar` in a scratch run) | **29.5** |
| readiness strip | 29 at top 51 | **29 at top 51** |
| page errors | `[]` | **`[]`**, 0 unhandled rejections |

The header's right end at 1280: the language select at 1026-1112, the theme switch at 1122-1152, the
command entry at **1220-1268** (the last control, flush with the header's content edge); the refresh
icon sits in the toolbar at (576.6, 142.3), 28x24.

> **Re-measured 2026-09-20, after the entry became a bar and the census left the header**: every
> number in the table above still holds (top bar 89, `.layout` 811 ending at 900, panels 260/380,
> toolbar 29.5, `#gallery` 666, readiness 29 at top 51, 0 page errors). What moved is the header's
> right end: the language select at 843.6, the theme switch at 939.6, and the **command bar at
> 1037.6-1268** (230.4 x 28), with the census now in the breadcrumb row (578 x 23.5, `#dir-counts`
> 146.2 wide ending at 869). The theme passes are unchanged: 41 tokens, 0 / 1 failing,
> `tokens_match_dark: true` - the contrast table had 20 rows that day and 21 after the group-head band
> joined it.

The theme passes are untouched by the move: 41 tokens non-empty and 20 contrast rows in each of
light and dark (21 since the group-head band joined the table), 0 / 1 failing (the pre-existing
`.badge-missing` 3.03:1), and the toggle pass still
reports `tokens_match_dark: true` with `checked` moving false -> true.

**--width 900** (one load, `--theme light`): the header stays **89px** - it no longer wraps at this
width, because 刷新 left it - and nothing is lost or overlapped: the header's right end reads
语言 646-732, 深色模式 742-772, 命令 840-888, all on one line, while `.layout` still ends exactly at
900. The centre panel is 278px at this width, so the toolbar row wraps to **65px** (two clean
lines, `#gallery` 612.5px) and the refresh icon sits at (427, 142.3) - on the first line, no control
clipped.

> **Re-measured 2026-09-20 with the bar in the row** (`--width 900 --lang zh-CN`): the header is
> still **89px**, the bar at 726-888 (162px), 语言 at 532, 深色模式 at 628, toolbar 65 and `#gallery`
> 612.5 - the same numbers as above. In the en pack the same row is still one line at 900 (bar
> 726-888) and starts wrapping **below ~885px**, where the bar drops to a second line beside the
> brand (header 127); zh-CN is still one line at 800.

**Per locale at 1280** (scratch runs that add `?lang=` to the iframe - the committed probe could not
select a locale that day, so these numbers come from the probe's own machinery with that one patch;
**since 2026-09-20 it can**: `--lang zh-CN|en|ja` reaches the app through its own `?lang=`
resolution, so the rows below are reproducible - see the known limits):

| | recursive label + checkbox | `#gallery-toolbar` | `#gallery` | header | command entry (button) |
|---|---|---|---|---|
| zh-CN | 77px (13px checkbox + 52px label, natural width) | 29.5px (one line) | 666px | 89px | 48px |
| en | 126px (squeezed, two lines) | 65px (two lines) | 630.5px | 89px | 92.9px |
| ja | 144.8px (squeezed, two lines) | 65px (two lines) | 630.5px | 89px | 74px |

The refresh icon is 28x24px in all three packs, and the command entry measured **48px in zh-CN -
exactly what it measured at 2471b31**, which is the measurement behind "the entry did not change".

> **The same three packs on 2026-09-20, with the command bar and the census row in place**
> (`--lang zh-CN|en|ja` - the flag the round above could not commit):
>
> | pack | header | bar | `#lang-select` left | theme switch left | breadcrumb row | census (`#dir-counts`) | toolbar | `#gallery` |
> |---|---|---|---|---|---|---|---|---|
> | zh-CN | 89 | 230.4 | 843.6 | 939.6 | 23.5 | 146.2 | 29.5 | 666 |
> | en | 89 | 230.4 | 826.5 | 922.5 | 23.5 | 205.4 | 65 | 630.5 |
> | ja | 89 | 230.4 | 817.6 | 913.6 | 23.5 | 235.2 | 65 | 630.5 |
>
> The bar is 230.4px at 1280 (162px at 900, 150px at 800 - `clamp(150px, 18vw, 240px)`),
> the header stays 89px in all three packs, and the census keeps its own width and its own line in
> each of them - the full width sweep is in
> [command-bar-and-census-row](command-bar-and-census-row.md).

## Falsified

Break -> red -> restore -> green, **every one re-run against the shipped bytes** and every restore
verified by `sha256` against the pre-break file:

| break | red |
|---|---|
| two header controls swapped (**the theme switch and the command entry**) | the header-arrangement criterion |
| the browse scope moved back into the header | 4 criteria: the arrangement one plus the toolbar's first-child, the toolbar order and "not in the header" |
| the refresh action moved back into the header | the refresh criterion, the toolbar order criterion, the header arrangement |
| the refresh control named by its glyph (no `data-copy-aria` / `data-copy-title`) | the refresh criterion |
| the icon's svg without `aria-hidden`/`focusable="false"` | the refresh criterion |
| the toolbar separator given the text `|` | the separator-class criterion |
| the toolbar separator given `tabindex="-1"` | the separator-class criterion |
| a second, **announced** `.group-sep` added to the header | the separator-class criterion **and** the header arrangement |
| the toolbar separator renamed `.group-divider` | the toolbar order criterion + the separator-class criterion |
| `#btn-select-all` pushed out of the toolbar row | the toolbar order criterion |
| `background: var(--line)` -> `#c6cdd7` on `.group-sep` | the separator-class criterion **and** "nothing below the token blocks carries a colour literal" |
| `.group-sep` given `flex: 1 1 auto` | the separator-class criterion |
| the readiness strip lifted above the state readouts | the header arrangement criterion |
| `flex-wrap: wrap` removed from `.toolbar` | the "wraps instead of clipping" criterion |
| `#gallery-toolbar` dropped from the probe's `SELECTORS` | the probe's "surfaces this repository keeps moving" criterion |

One break was **vacuous and stayed green**: moving the switch label to sit immediately before
`#readiness` changes nothing (it was already there), so the criterion was right to stay green. The
readiness assertion was falsified separately by lifting the strip above the state readouts.

## Judgement calls

1. **Does the one remaining separator earn its keep?** Yes. In the toolbar the boundary is
   load-bearing: without it "含子目录 已选中 1 张" is one run of text with two different subjects (a
   scope and a count), and the refresh icon would read as a fourth selection action. The header's was
   removed rather than kept for symmetry - it separated a button from a labelled select, which is not
   a boundary; keeping it "because the grammar has one" would have been decoration with no idea
   behind it.
2. **The refresh action is an icon, and the words did not vanish.** `browse.refresh` is still the
   accessible name and the tooltip; the glyph is decoration. It is also why the toolbar gains a
   control without gaining a width problem in zh-CN (28px against a 48px word button).
3. **`.group-sep`'s height is a fixed 18px**, not `align-self: stretch`: 18px is shorter than the
   row's tallest control, so it cannot change the row's height, which is the property this round had
   to protect.
4. **`browse.recursive`'s wording is unchanged.** "含子目录" beside "已选中 1 张" reads as two
   fragments of one line in zh-CN, and the separator is what fixes it - rewording a key three packs
   share would have been the wrong fix (and the hint in the left column stays true either way).
5. **The probe learned the header's controls, not a new probe.** `SELECTORS` gained
   `.brand-lockup`, `#btn-refresh`, `#btn-palette`, `#lang-select`, `#theme-dark` and
   `#gallery-toolbar`.

## Known limits

- **The probe could not choose a locale when this round ran** - the wrapper hard-coded
  `frame.src = "./index.html"`, so `?lang=` never reached the app, and the en/ja numbers above were taken
  with the same probe and one patch to that line (appending the query the app itself resolves),
  which was **not** reproducible from the committed tool. **Fixed 2026-09-20**: the probe has
  `--lang zh-CN|en|ja`, which travels through that same `?lang=`, so the en/ja rows are one flag away.
- **In en and ja the toolbar row is 65px, not 29.5px** (two flex lines; `#gallery` 630.5 against
  zh-CN's 666). With the recursive label and the icon button in it the row is longer than the 578px
  centre panel in those packs. This is a *move*, not a regression, and it is measured both ways: at
  2471b31 the same labels made the **header** wrap instead (89 -> 128.5px in en and ja), and the
  gallery was 646.5px there because the row squeezed its buttons' labels onto two lines each. zh-CN,
  the width the theme round's numbers are pinned at, is unchanged at 29.5px.
- **The command entry's shape was not settled by this round** - it stayed the row's last control with
  the markup it had before (the requester was deciding its treatment separately), so its 48px width
  was a measurement of the old shape, not a design decision. **Settled 2026-09-20**: it is now a
  command bar (230.4px at 1280) with a Ctrl+K keycap, and the panel it opens is a dropdown rather
  than a modal - [command-bar-and-census-row](command-bar-and-census-row.md).
- **The icon has no hover/focus contrast row.** The contrast table covers the resting state of twenty
  surfaces; `.btn-icon` inherits `.btn`'s colours, so its swatch is the `.btn` row. The glyph
  itself was checked in the light screenshot (`--shot`), not measured.
- **The 900px run is one pass** (`--theme light`); the header geometry is identical in all three
  theme passes, and the theme behaviour at 900 was not re-measured.

---

Related: [command-palette](command-palette.md), [light-mode](light-mode.md),
[gallery-filter-strip](gallery-filter-strip.md),
[readiness-strip-and-layout-probe](readiness-strip-and-layout-probe.md), [ui-copy](../ui-copy.md)
