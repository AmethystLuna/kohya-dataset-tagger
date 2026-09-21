# The command bar and the census row: a dropdown is not a modal

**When to read this**: changing the palette's entry or panel shell (`web/index.html`, `app.css`,
`web/js/palette.js`), the header row's arrangement, the census line (`#dir-counts`) or the
breadcrumb row, or the `--shot` / `--lang` modes of `tools/measure_web_layout.py`.

## Why (2026-09-20)

Three requests, in one round because they are one subject - *where does the command entry live, and
what is the shape of the thing it opens*:

1. **拓展成整行的 toolbar** - the entry was a 48px button reading 命令 ("Commands"), i.e. it named the
   *feature*. A bar that carries the field's own invitation (输入命令名称…) and the gesture (Ctrl+K)
   says what happens instead of what it is called.
2. **点一下出现下拉框** - it opened a modal: a full-screen scrim, `aria-modal="true"`, Tab trapped
   between the input and the close button. That was the shape of a dialog, not of the thing attached
   to a control in the header - and it was the last surface in this app that stole the keyboard.
3. The census line (`#dir-counts`) had to leave the header for the bar to fit, and it belongs next
   to the directory it counts: the breadcrumb's own row.
4. The requester then looked at the open panel and named a fourth thing: **its typography had no
   hierarchy** - the surface's name rendered smaller and quieter than the rows it named. That is the
   last section of "The decision" below.

The browser round also found and fixed a **measurement defect** in `--shot` (below): the pictures it
wrote did not always show the geometry its JSON reported.

## The decision

### The bar: the row's last control, shaped like the field it opens

```html
<div class="palette-bar-wrap">
  <button type="button" id="btn-palette" class="palette-bar" aria-haspopup="dialog"
          aria-expanded="false" data-copy-title="palette.openTitle" data-copy-aria="palette.openTitle">
    <span class="palette-bar-hint" data-copy="palette.placeholder" aria-hidden="true"></span>
    <kbd class="palette-key" data-copy="palette.openKey" aria-hidden="true"></kbd>
  </button>
  <div id="palette" class="palette-pop" role="dialog" aria-labelledby="palette-title" hidden> … </div>
</div>
```

- **The id and the click wiring are unchanged** (`#btn-palette`, `openPalette()`): the command table
  and the probe's steps name that id, so the round renamed the *shape*, not the control.
- **The invitation is `palette.placeholder`** - the same string the input inside shows. One idea,
  one string; a second wording for the same question would have been a second key. While the panel is
  open the bar's copy steps aside (`.palette-bar[aria-expanded="true"] .palette-bar-hint`), because
  the input is carrying it twenty pixels below.
- **The keycap is a key name, not copy.** `palette.openKey = "Ctrl+K"` in all three packs, and the
  three values are byte-identical. A translated keycap would rename a key the keyboard does not have
   - the same call `strings.js` already makes for the language endonyms. `palette.open` ("命令" /
  "Commands") was deleted from all three packs: the bar shows the gesture instead of naming the
  feature, so the key became dead copy.
- **WCAG 2.5.3 (label in name) is a criterion, not a coincidence**: in every pack,
  `palette.openTitle` must CONTAIN `palette.openKey` ("打开命令面板（Ctrl+K）" contains "Ctrl+K").
  Reword the title or translate the key and `test_the_command_bar_shows_the_gesture_and_names_it`
  goes red.
- **Height**: the bar is 28px (`padding: 4px 8px` + a 19px line box + a 1px border), shorter than the
  34px first row of the header, so it cannot make the header grow. Width
  `clamp(150px, 18vw, 240px)`: 230.4px at 1280, 162px at 900.

### The panel: a dropdown anchored to the bar

`#palette` keeps everything inside it (the combobox input, the listbox, `aria-activedescendant`, the
local matcher, the groups, the reasons, the status line, the title, the close button, the keys
footer, the relabel hook, the job-shelf refresh). Only its **shell** changed:

| | before | now |
|---|---|---|
| markup | `div#palette.dialog-backdrop` at the end of `<body>`, wrapping `div.dialog.palette-dialog` | `div#palette.palette-pop` inside `.palette-bar-wrap` in the header |
| position | `position: fixed; inset: 0` (a full-screen scrim) | `position: absolute; top: calc(100% + 6px); right: 0` (anchored, right-aligned) |
| roles | `role="dialog"` + `aria-modal="true"` | `role="dialog"` + `aria-labelledby` - **no `aria-modal`** |
| keyboard | Tab trapped between the input and the close button | **Tab leaves the panel and closes it** |
| entry state | none | `aria-haspopup="dialog"` + `aria-expanded`, written by the palette's own `open()`/`close()` |

- **`role="dialog"` stays, `aria-modal` goes.** The panel does own a name and a focusable field, so
  "dialog" is the honest role; `aria-modal` claims the rest of the page is unreachable, and with Tab
  walking out of it that claim would be false.
- **z-index 30.** The panel is inside the header's own stacking context (the sticky `.topbar` is
  `z-index: 20`, above the panels), so it is already above every panel and below the job shelf (40)
  and the toasts (50) whatever number it carries; 30 is the number the breadcrumb's dropdown uses for
  the same kind of surface, and it beats the readiness strip, its only in-flow sibling.
- **Sizes are today's numbers**: `width: min(560px, 94vw)`, `.palette-list { max-height: min(46vh, 420px) }`.

### Dismissal: three ways out, none of them a trap

| gesture | what happens |
|---|---|
| Escape | closes, focus back on the bar (the standard popover behaviour: "put me back") |
| click outside | closes (a document listener; the panel and the bar are the two "inside" exceptions, so the bar's own click does not read as "outside") |
| Tab | **closes and does not swallow the key** - the browser moves focus on from the bar, which is what "Tab leaves the panel" means |
| Ctrl+K | opens and focuses the input, and **never closes it**: a gesture that toggles throws away a half-typed query |
| the close button | closes (kept: it is the panel's own control; see the judgement calls) |

The refusal guard is unchanged in meaning: another `.dialog-backdrop`/`.zoom-backdrop` on screen owns
the keyboard, and `open()` returns false. It no longer excepts the palette's own node - the palette
is not a backdrop any more, so that branch could never fire, and a criterion now forbids it coming
back.

### The census moves into the breadcrumb row

```html
<div class="breadcrumb-row">
  <nav id="breadcrumb" class="breadcrumb"></nav>
  <span id="dir-counts" class="counts"></span>
</div>
```

- **A sibling of the nav, never a child**: `#breadcrumb` is rebuilt with `replaceChildren()` on every
  navigation (`app.js:951-954`), so an element inside it is destroyed by the next click. The census is
  painted by `renderCounts()` from the listing's own `counts` object, which is also what feeds the
  readiness strip's caption segment - one value, two surfaces, unchanged.
- **Right-aligned by the `.li-count` technique**, with a longer subject: `margin-left: auto` +
  `flex: 0 0 auto` + `white-space: nowrap`, and the nav takes `flex: 0 1 auto; min-width: 0` so the
  **path** is what shrinks and wraps (`flex-wrap: wrap`). CJK breaks between any two characters, so
  without the nowrap the census wrapped onto a second line under a long path.
- **The row's 6px gap moved from the nav to the row** (`.breadcrumb-row { margin-bottom: 6px }`), so
  the centre column's stack is exactly as tall as it was: measured, the row is **23.5px** and
  `#gallery` is **666px** at 1280 in zh-CN - the same numbers as before the move.
- **No copy changed.** `browse.counts` / `browse.countsRecursive` already say what they count, and
  the recursive variant is what keeps the scope stated in words.

### The panel's typography: a name is a title

The requester's second look at the open panel: the NAME (命令面板) rendered at
`.panel-title`'s 12px, in `var(--text-dim)`, upper-cased and letter-spaced - smaller AND quieter
than the 13px `var(--text)` rows it named - while the 关闭 button beside it was the loudest thing in
the head, and nothing separated the head from the input under it. Three levels of small dim text, and
no title.

**One rule, scoped to `.dialog-head` so every dialog gets it** - the picker, the model
directories, the crop dialog, the confirm dialog and this panel all carry a `.panel-title` inside
one, and a name is a title on every surface:

    .dialog-head .panel-title {
      margin: 0;
      font-size: 15px;
      color: var(--text);
      font-weight: 600;
      text-transform: none;
      letter-spacing: 0;
    }

The scale it states - every number read from the real stylesheet in a real browser: **title 15 /
`var(--text)` / 600  >  input and rows 13  >  reasons, status line and footer 12  >  group heads 11**.
The group heads stay the quiet label they are: inflating them would flatten the scale instead of
fixing it, and a criterion now says so.

**One more rule, and deliberately only here**:
`.palette-pop .dialog-head { border-bottom: 1px solid var(--line); padding-bottom: 6px; }` -
every other dialog's head is followed by a hint paragraph that is part of its body, this one by the
control the surface is FOR (the input). Tokens only; the close button stays a `.btn-mini`.

**A/B, measured** (the same tree with only the title rule reverted; each dialog built by its own
module over the real stylesheet, heads read through `getComputedStyle`):

| head | before | after | delta | dialog before -> after | overflow |
|---|---|---|---|---|---|
| command panel | 29 | 29.5 | +0.5 | 355.5 -> 356 | none |
| model directories | 22 | 22.5 | +0.5 | 424 -> 424 | none |
| picker | 22 | 22.5 | +0.5 | 210.5 -> 211 | none |
| confirm | 18 | 22.5 | +4.5 | 111.5 -> 116 | none |

The heads that carry a `.btn-mini` close button (22px) move by half a pixel: the button sets
their height, not the title. The confirm dialog's head is title-only, so it grows by the title's own
line box. No head overflows, and no copy changed.

**And the group heads are BANDS** (the requester's third look): a rounded strip of `var(--bg-elev)` behind the
quiet 11px label - `padding: 3px 8px`, `margin: 4px 0 3px`, `border-radius: 4px`. The 8px is the
**rows'** own horizontal padding, so the band's text starts where the text it heads starts; and the
token is not decoration either - `--text-dim` on `--bg-elev` is a pair `test_web_theme.py` already
computes and falsifies, so the band's readability is a covered fact (**measured in the probe: 5.28:1
light, 6.01:1 dark**, now one of the table's 21 rows). A tint (`--tint-accent`) would have been an
unmeasured claim.

The first version of that rule shipped with a sibling - `.palette-group-head:first-child { margin-top: 0 }`,
meant to keep the list's own top spacing - and the browser measurement caught it: **every band is the
first child of its own .palette-group box**, so it matched all six and silently flattened the top
margin to 0 (the declared margin looked right; a criterion that reads the stylesheet cannot see the
cascade). The rule is gone, the margin is uniform, and the criterion now refuses any second rule that
touches the band's layout.

The band costs the list 30px of scroll (6 groups x 5px: the head's own box shrinks by 2px and gains 7px
of margin). **The panel's measured height does not move** - 560x575 before and after - because the list
is already at its `min(46vh, 420px)` cap, so the band eats the fold, not the panel: the list's
scrollHeight goes 885 -> 915 while its clientHeight stays 414 (~14 rows on screen). The list's max-height
was deliberately not touched.

**The shot**: `%TEMP%\kdt-bar-round\band2-panel-open.png` (1280x900, the panel held open) shows the scale and
the bands on screen: the 15/600 name, the head rule, the 13px rows, the 11px bands with their text on
the rows' left edge, and the first band 10px under the input (its 6px margin + the band's 4px).
### The `--shot` defect, found while measuring

Two independent shot runs put **200px** in `probe-light.png` for `#panel-left` and **280px** for
`#panel-right` while the same run's JSON said **260/380**. 200 and 280 are exactly
`RESIZER_LIMITS.left.min` and `.right.min` (`resizer.js:25-28`): the app had booted against a layout
that was not final yet, and `createResizers` clamps **down only** (on construction and again on
`window resize`, lines 247-262), so the clamped minimums stuck.

The cause was the shot page's own sizing: `__SIZE__` was `100vw;height:100vh` for a shot run **and**
the wrapper restyled the frame after creating it (`frame.style.cssText = …`). Sizing from the window
means sizing from whatever the window happens to be while the app is still starting - and a race is
exactly why one picture had it and the other did not (the first, cold run lost it). A normal probe run
was immune because its frame is `1280px` wide no matter what the window is doing.

**The fix**: the frame is sized once, in the stylesheet, from the probe's own viewport
(`"%dpx;height:%dpx" % viewport`) - in a shot run too, since a shot run's window IS that viewport
(`--window-size` gets the same numbers) - and the post-load restyle is gone. Both PNGs now measure
260/380, the numbers the JSON reports.

## Evidence

**A real browser, measured** - `tools/measure_web_layout.py --theme both --shot DIR`, 1280x900,
canned `/api/*`, zh-CN:

| surface | value |
|---|---|
| top bar | 89 (unchanged) |
| `.layout` | 89 -> 900 (unchanged) |
| `.panel-left` / `.panel-right` | 260 / 380 (unchanged) |
| `#gallery-toolbar` | 29.5 (unchanged) |
| `#gallery` | 666 (unchanged) |
| readiness strip | 29 at top 51 (unchanged) |
| `.breadcrumb-row` | 578 x 23.5 at top 110 |
| `#dir-counts` | 146.2 x 18, right edge 869 = the centre panel's content edge |
| `#btn-palette` (the bar) | 230.4 x 28, left 1037.6, right 1268 (the header's content edge), top 9.5, bottom 37.5 |
| `#palette` open | 560 x 575 (567.5 before the head became a chrome row), top **43.5 = the bar's bottom + 6**, right **1268 = the bar's right** |
| the panel's shell | `role=dialog`, `aria-modal=null` |
| `palette:opened` | `#palette-input` focused, `aria-expanded=true`, 25 rows, 6 groups, status `共 25 项` |
| `palette:escape` | hidden, `document.activeElement` = `#btn-palette` |
| `palette:ran` | the page's active tab became `cache`, focus back on `#btn-palette` |
| page errors | **0** in all three passes, 0 unhandled rejections |

The theme passes did not move: 41 tokens non-empty each, **21** contrast rows each (the group-head band
joined the table), light **0** failing /
dark 1 (the pre-existing `.badge-missing` 3.03:1), and the toggle pass still reports
`tokens_match_dark: true` with `checked` moving false -> true.

**The two PNGs** (`--shot`), measured pixel by pixel at y=300 and hashed:

| | `probe-light.png` | `probe-dark.png` |
|---|---|---|
| `.panel-left` borders | x=10 and x=269 -> **260px** | x=10 and x=269 -> **260px** |
| `.panel-right` borders | x=890 and x=1269 -> **380px** | x=890 and x=1269 -> **380px** |
| sha256 | `e5b42695…` | `f4e32c59…` |

Before the fix, the same measurement on `probe-light.png` gave borders at x=10/x=209 (200px) and
x=990/x=1269 (280px) while `probe-dark.png` already read 260/380 - the two pictures of one run
disagreeing with each other and with the JSON.

**Where the header wraps** (one load per row, `--width`, `--lang`; `#lang-select` and the theme
switch keep their own boxes, so the bar is the only thing that moved):

| viewport / pack | top bar | bar | bar left | `#lang-select` left | theme switch left | breadcrumb row | census | toolbar | `#gallery` |
|---|---|---|---|---|---|---|---|---|---|
| 1280 zh-CN | 89 | 230.4 | 1037.6 | 843.6 | 939.6 | 23.5 | 146.2 | 29.5 | 666 |
| 1280 en | 89 | 230.4 | 1037.6 | 826.5 | 922.5 | 23.5 | 205.4 | 65 | 630.5 |
| 1280 ja | 89 | 230.4 | 1037.6 | 817.6 | 913.6 | 23.5 | 235.2 | 65 | 630.5 |
| 1100 zh-CN | 89 | 198 | 890 | 696 | 792 | 23.5 | 146.2 | 65 | 630.5 |
| 900 zh-CN | 89 | 162 | 726 | 532 | 628 | 23.5 | 146.2 | 65 | 612.5 |
| 900 en | 89 | 162 | 726 | 514.9 | 610.9 | 23.5 | 205.4 | 130.5 | 529 |
| 890 en | 89 | 160.2 | 717.8 | 505.9 | 601.9 | 23.5 | 205.4 | 130.5 | 529 |
| 880 en | **127** | 158.4 | **12** (second line) | 495.9 | 591.9 | 23.5 | 205.4 | 130.5 | 491 |
| 840 en | **127** | 151.2 | 12 | 626.9 | 722.9 | 23.5 | 205.4 | 126 | 491 |
| 800 en | **127** | 150 | 12 | 586.9 | 682.9 | 23.5 | 205.4 | 126 | 477.5 |
| 840 zh-CN | 89 | 151.2 | 676.8 | 482.8 | 578.8 | 23.5 | 146.2 | 65 | 612.5 |
| 800 zh-CN | 89 | 150 | 638 | 444 | 540 | 23.5 | 146.2 | 65 | 612.5 |

**At 1280 the header is on one line and the bar ends exactly on its content edge** (1268 = 1280 - 12),
which is what "the header does not grow" means here: 89px, driven by the 34px first row, and the bar
is 28px of it. The en pack is the wide one and it starts wrapping **below ~885px** (89px at 890, 127px
at 880, where the bar drops to a second line beside the brand); **zh-CN is still one line at 800px**,
the narrowest run. The census keeps its own width everywhere (146.2 / 205.4 / 235.2), and so does the
breadcrumb row (23.5).

**A long path**, measured in a real browser with a scratch wrapper over the same canned API (the
committed probe's fake root is one level deep, so no navigation can produce a long path - see the
known limits). Eight segments of a long name, 1280px:

| segments | nav | row | census |
|---|---|---|---|
| 1 | 224.6 x 23.5 | 23.5 | 146.2 x 18, right 869 |
| 4 | 423.8 x 106 | 106 | 146.2 x 18, right 869 |
| 8 | 423.8 x 216 | 216 | 146.2 x 18, right 869 |
| 14 | 423.8 x 381 | 381 | 146.2 x 18, right 869 |

The path wraps **inside the nav** (its height is what grows); the census keeps its width, its line
and its right edge in every case.

**Headless behaviour** - `test/fixtures/palette_harness.mjs` (the real `palette.js` over a fake DOM)
now drives the dropdown: Ctrl+K opens and never toggles (a second Ctrl+K keeps the typed query and
puts the caret back), **Tab closes the panel and leaves the keyboard on the bar**, Shift+Tab the same,
Escape closes with focus on the bar, a click outside closes it while a click on the bar, the panel or
a row does not, the bar's own click reopens it from a closed panel, the panel's close button closes
it, the bar's `aria-expanded` follows every one of those, 0 rows for a query that matches nothing,
and the palette still refuses to open while another backdrop is on screen.

**Static** - `test/test_web_palette.py` (24 criteria, one of which runs the real page and skips where no
Chromium is installed), `test/test_web_breadcrumb.py` (2 new),
`test/test_web_theme.py` (the row list), `test/test_measure_web_layout.py` (2 new / amended).

## Falsified

Break -> red -> restore -> green, script-driven, **every restore verified byte-exact by sha256**:

| break | red |
|---|---|
| the Tab trap put back (`event.preventDefault()` in the Tab branch) | the dropdown criterion |
| the trap put back for real (the key swallowed, the panel left open) | the dropdown criterion **and** the harness |
| Ctrl+K toggles the panel shut again | the dropdown criterion **and** the harness |
| the click-outside listener removed | the dropdown criterion **and** the harness |
| the bar stops handing the keyboard back | the harness |
| the guard excepts the palette's own node again | the refusal criterion |
| the backdrop class put back on the panel | the anchor criterion |
| `aria-modal="true"` put back | the roles criterion |
| the panel moved inside `#breadcrumb` | the anchor criterion |
| the bar's wrapper renamed (the panel is anchored to nothing) | the anchor criterion and the bar criterion |
| the keycap badge removed | the bar criterion |
| `aria-haspopup` removed from the bar | the bar criterion |
| the accessible name no longer contains the visible key | the bar criterion (Label in Name) |
| the keycap translated in one pack | the bar criterion |
| an element inserted after the bar (not the row's last control) | the header-arrangement criterion |
| the census made a child of the nav | the census-sibling criterion |
| the census put back in the page header | the census-sibling criterion |
| `white-space: nowrap` removed from the census | the census-CSS criterion |
| the census made shrinkable (`flex: 0 0 auto` -> `1 1 auto`) | the census-CSS criterion |
| `min-width: 0` removed from the nav | the census-CSS criterion |
| `flex-wrap: wrap` removed from the nav | the census-CSS criterion |
| the shot frame restyled after the app may have loaded | the `--shot` criterion |
| the shot frame sized from the window again | the `--shot` criterion |
| the probe addresses the modal shell that is gone | the probe's palette criterion |
| `--lang` no longer reaches the app | the probe's locale criterion |
| the dialog title put back to the caption size (12px) | the title-is-larger criterion |
| the dialog title painted dim and upper-cased again | the title-is-not-a-caption criterion |
| the palette head rule removed | the chrome-row criterion |
| that rule given a colour literal (`#c6cdd7`) | the chrome-row criterion |
| the group heads inflated to 13px (the scale flattened) | the group-heads-stay-below criterion |
| the band's background made a literal (`#eef1f5`) | the band criterion |
| the band squared off (no radius) | the band criterion |
| the band's text pulled off the rows' left edge (`padding: 3px 4px`) | the band criterion |
| the ROWS' padding moved instead (5px 8px -> 5px 10px) | the band criterion (the alignment is a pair) |
| the band glued to its rows (margin removed) | the band criterion |
| the `:first-child` sibling rule put back - one line, and again as the same long-hand over three lines | the band criterion **and** the browser criterion |
| `:first-child { margin: 0 0 3px; }` (the shorthand shape) | the band criterion **and** the browser criterion |
| `:last-child { margin-bottom: 0; }` (a second selector naming the band that matches nothing today) | the band criterion |
| `.palette-group > div:first-child { margin-top: 0; }` (a selector that never names the band) | the browser criterion - the static half cannot see it |
| the band's own selector re-declared later with a different margin | the band criterion **and** the browser criterion |
| the band added to the contrast table but not to the probe's seeds | the probe's coverage criterion |

**The band's first version had a real defect that only the browser measurement caught**: the rule was
paired with `.palette-group-head:first-child { margin-top: 0 }`, and every band is the first child of its
own `.palette-group` box - so all six lost their top margin and the declared `4px` never rendered. The
static criterion read the declaration and passed; the rendered measurement (`margin: 4px 0 3px` on all
six, `scrollHeight` +6 instead of +30) is what showed it.

**The first fix was still a hole, and it was found by hand.** The guard that replaced it looked for a
second rule whose BODY carried a `margin` shorthand - so the defect's own shape went through it three
times: `:first-child { margin-top: 0; }` on one line, the same long-hand over three lines, and
`:last-child { margin-bottom: 0; }` all kept the suite green, and only the shorthand variant went red. A
criterion that claims more than it checks is worse than none, so the guard was rebuilt in two halves:

* **static, selector-based**: the stylesheet is split into (selector, body) pairs and **exactly one rule
  may name `.palette-group-head`** - formatting-independent, so one line, five lines, long-hand,
  shorthand, a pseudo-class or a re-declaration all count as a second rule;
* **browser, because the cascade is not something a stylesheet reading can see**: a criterion that runs
  the real page (the app served from a scratch copy of web/, the command bar clicked open) and asserts
  that **every** band's computed `margin-top`/`margin-bottom` equals the declared value and is
  greater than zero. It skips where no Chromium is installed, like the WD14 integration criteria skip
  where no model is.

The six variants, run against the shipped bytes (each appended to the stylesheet, each restore
sha256-verified):

| variant | static | browser |
|---|---|---|
| v1 `:first-child { margin-top: 0; }` one line | **RED** | **RED** |
| v2 the same shape over three lines | **RED** | **RED** |
| v3 `:first-child { margin: 0 0 3px; }` (the shorthand) | **RED** | **RED** |
| v4 `:last-child { margin-bottom: 0; }` | **RED** | green |
| v5 `.palette-group > div:first-child { margin-top: 0; }` (never names the band) | green | **RED** |
| v6 the band's own selector re-declared later | **RED** | **RED** |

v4 is green in the browser because that selector matches nothing on the real page (the band is the
first child of its group box, never the last) - the static half is deliberately conservative and
refuses the second rule anyway. v5 is the case the static half cannot see at all, which is why the
browser half exists.

**One break was green for the wrong reason and was redone.** The first "[harness] the Tab trap put
back" case *added* `event.preventDefault()` to the Tab branch and the harness stayed green: the fake
event's `preventDefault` is a no-op in a fake DOM, so the panel still closed and the harness saw
nothing. The static criterion owns that pin; the harness case was rewritten to swallow the key
instead (a trap that changes what the harness can see), and both then went red.

## Judgement calls

1. **The title and the close button stay inside the dropdown.** The header ("命令面板" + 关闭) is what
   gives the panel a name and a visible way out for the mouse, and the round's subject was *where the
   panel lives*, not what it contains. The cost is honest and small: Tab now leaves the panel, so the
   close button is a mouse-only affordance and Escape is the keyboard's way out - reachable and
   equivalent, which is what 2.1.1 asks for, but a keyboard user never lands on it. Dropping it would
   have been the other defensible answer (Escape is a complete dismissal); keeping it changes less.
2. **The bar's width is a clamp rather than a fixed number.** `clamp(150px, 18vw, 240px)` reads
   230.4px at 1280 - wide enough to show the invitation without truncating it - and shrinks with the
   window instead of pushing the row over. It is 162px at 900, where the header still does not wrap.
3. **`palette.open` was deleted from the packs** rather than kept unused. It named the feature, and
   the bar shows the gesture now; a key nothing renders is the dead copy this repository deletes.
4. **The invitation hides while the panel is open.** It is the input's placeholder at the same time,
   and the same sentence twice, twenty pixels apart, is what "never the same fact twice" forbids. The
   keycap stays: it is the bar's visible label and the reason the accessible name must contain it.
5. **`el !== host` was removed from the modal guard**, not kept "just in case": the palette carries no
   backdrop class any more, so the exception can never be true, and a criterion now forbids it coming
   back (it would mean the shell went back to being a modal).
6. **The probe learned a `--lang`.** Every en/ja number in the layout tables used to come from a
   scratch patch of `frame.src`, which is not reproducible from the committed tool (the previous
   round wrote that down as a known limit). The locale now travels through the app's own `?lang=`
   resolution, seeded before the frame loads - so "the ja labels are wider" is one flag away.

## Known limits

- **The header below 800px was not measured.** 800 (zh-CN) and 800 (en, wrapped) are the narrowest
  runs; the bar reaches its 150px floor at ~834px and the en row is already on two lines there.
  Nothing was measured on a phone-sized viewport, where the layout's fixed 260/380 columns are the
  real constraint, not the header.
- **The long-path measurement is a scratch wrapper**, not the committed probe: the canned root has one
  level, so the app cannot navigate into a long path. What it measured is the row's CSS with the
  markup `renderBreadcrumb()` draws; the app's own renderer was not driven.
- **The crop dialog's head was read, not opened.** The A/B above opened four of the five heads (the
  command panel, the model directories, the picker, the confirm dialog). The crop dialog builds its
  head per run inside `buildDialog()` (`crop.js:336-340`: `div.dialog-head` + `h2.panel-title` + a
  `.hint` counter), so the shared rule reaches it exactly as it reaches the confirm dialog's
  title-only head - the same markup, measured there - but its own head was not photographed.
- **The dropdown was not exercised by a screen reader.** Roles, names, `aria-expanded`,
  `aria-haspopup` and `aria-activedescendant` are pinned statically and in the fake DOM; what a
  screen reader announces for a non-modal `role="dialog"` popup was not observed.
- **Only one panel position was photographed.** `--shot` stops at the boot state, so the PNGs show
  the bar and the census but not the open dropdown; the dropdown's geometry is the JSON's
  `palette:opened` box (top 43.5, right 1268) - measured, not photographed.
- **The 900px and 1100px runs are one pass each** (`--theme light`); the theme behaviour at those
  widths was not re-measured.
- **The command count in the older docs was stale by two.** The palette renders **25** commands (the
  crop round added `view.crop` and `crop.start`); `command-palette.md` and `current-state.md` still
  said 23, and both were corrected in this round.

---

Related: [command-palette](command-palette.md), [topbar-layout](topbar-layout.md),
[keyboard-and-dialogs](keyboard-and-dialogs.md), [light-mode](light-mode.md),
[readiness-strip-and-layout-probe](readiness-strip-and-layout-probe.md), [ui-copy](../ui-copy.md)
