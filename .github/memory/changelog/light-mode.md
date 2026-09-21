# Light mode: a theme the user chooses, applied before the first paint

**When to read this**: changing the three token blocks in `web/css/app.css`, `web/js/theme.js`,
`web/js/theme-boot.js`, the header row's arrangement (the switch is its last *preference*, and the
command bar follows it as the row's last control), or the theme passes of
`tools/measure_web_layout.py`.

## Why (2026-09-20)

The frontend had exactly one theme, and it was dark. Its colours lived in a single `:root` block at
the top of the only stylesheet, and twenty-six colour literals sat below it - a danger fill, a
checkerboard, a skeleton sweep, three badge colours, two tints, two scrims, a zoom stage, a crop box.
So "add a light theme" was never a palette edit: it was a question about where a colour is allowed to
live, and about what happens between the browser receiving the HTML and the first pixel.

Three things had to be true when the round ended:

1. **Light is the default, for everyone, on every OS.** Not "follow the system" - the app is not being
   asked to guess about someone else's desktop, and a third state is a third thing to make true.
2. **A user who chose dark never sees light.** Not "usually" - the wrong-theme paint has to be
   unrepresentable, which is a statement about *when* the theme is applied, not about how fast.
3. **The colour architecture cannot rot.** A colour written below the token blocks ignores the theme,
   and looks correct in whichever theme it was typed in. Two themes with a shared vocabulary have to
   be enforced, not remembered.

## The measurement that forced a classic script

A deferred module - which is what `type="module"` means - runs *after* the document is parsed, and
the browser paints as soon as it can. Measured over this page's real module graph (27 modules) with a
paint-timing probe in headless Chrome:

| | cold profile | warm profile |
|---|---|---|
| first paint | 116 ms | 36 ms |
| a deferred module (`js/app.js`) evaluated | 275 ms | 50 ms |
| a synchronous `<head>` script evaluated | 87 ms | 13 ms |

So a theme applied by the module arrives **about 160 ms after the paint** on a cold load (one frame on
a warm one): a user who chose dark gets a white page first, every single time. No amount of ordering
inside the module fixes that, because the browser does not wait for the module to paint.

**The evidence class of those four numbers is "measured by the requester before this round", not
"measured again here".** This round did not re-run the paint-timing probe; what it did instead is make
the *structure* the measurement implies into a criterion (`test_web_theme.py` and
`test_web_contract.py` both require one - and only one - non-module script, in `<head>`, after the
stylesheet), so a later change that moves the theme into the module turns red instead of silently
reintroducing the flash.

That is the whole reason `js/theme-boot.js` exists: a classic script, in `<head>`, after the
stylesheet link, setting `data-theme` on `<html>` before anything is painted. It **cannot import**
`js/theme.js` - importing is what would make it a module, which is the too-late thing - so the
storage key `kdt.theme` is written out twice, and
`test_web_theme.py::test_the_module_and_the_boot_script_agree_on_the_storage_key` is what makes the
two copies impossible to drift (it also counts the occurrences of the literal, so a "fallback key"
cannot hide in either file).

## The decision

### Three token blocks, and nothing else carries a colour

`web/css/app.css` now opens with three blocks, and no colour literal appears below them:

| Block | Selector | Holds |
|---|---|---|
| 1 | `:root` | the light theme (the default), plus the five values that are not colours (`--tile`, `--radius`, `--left-w`, `--right-w`, `--mono`) |
| 2 | `:root, :root[data-theme="dark"]` | the colours that sit **on the image** - identical in both themes, and written once so they cannot drift |
| 3 | `:root[data-theme="dark"]` | the dark theme: every value the UI had before this round |

Block 2's selector is the statement: a caption badge, a crop handle and a zoom arrow are drawn over a
photograph, whose pixels this stylesheet does not control. A badge has to read the same over a white
photo and a black one, so "the theme" is the wrong axis for them - and one definition is the only
version of "identical in both themes" that cannot decay. The same reasoning applies to the zoom stage
(the mat around a picture) and to the crop box (an outline, a shade, four guides and eight handles, all
over the picture). The fills of `.badge-missing` and `.badge-present` **are** themed
(`var(--danger)` / `var(--ok)`), because those two were already tokens before this round; only the
literals moved into block 2.

`color-scheme` flips with the theme (`light` in `:root`, `dark` in block 3). Without it the
native scrollbars and form controls stay dark on a light page - the one part of "a light theme" that no
screenshot of our own controls would show.

**The four tints are tokens.** The hard-coded `rgba()`s that were a token's own value spelled out
(`.warning`'s amber, `.diff .add`'s green, `.diff .del`/`.cleared`'s red, `.marquee`'s blue)
became `--tint-warn` / `--tint-ok` / `--tint-danger` / `--tint-accent`, per theme. A tint that
copies a token's value silently stops following it the moment the token moves - which is exactly what
this round would otherwise have done to all four.

### The light palette, and the one value that had to move

| token | light | dark (unchanged) |
|---|---|---|
| `--bg` | `#f7f8fa` | `#14161a` |
| `--bg-panel` | `#ffffff` | `#1b1e24` |
| `--bg-elev` | `#eef1f5` | `#22262e` |
| `--line` | `#c6cdd7` | `#333a45` |
| `--text` | `#1b1f27` | `#e6e9ef` |
| `--text-dim` | `#5b6472` | `#9aa4b2` |
| `--accent` | `#2563eb` | `#4c8dff` |
| `--accent-dim` | `#dbe6ff` | `#2b4f8f` |
| `--danger` | `#c02626` | `#ff5c5c` |
| `--warn` | `#9a5b00` | `#ffb020` |
| `--ok` | `#0f7a45` | `#46c07a` |
| `--danger-bg` | `#f7dbdb` | `#5a2020` |

Contrast (WCAG 2.1 relative luminance, computed from the file by `test/test_web_theme.py` - the same
six text colours against the three page surfaces):

| | on `--bg` | on `--bg-panel` | on `--bg-elev` |
|---|---|---|---|
| light `--text` | 15.54 | 16.51 | 14.57 |
| light `--text-dim` | 5.63 | 5.98 | 5.28 |
| light `--accent` | 4.86 | 5.17 | 4.56 |
| light `--danger` | 5.57 | 5.92 | 5.23 |
| light `--warn` | 5.11 | 5.43 | 4.79 |
| light `--ok` | 5.08 | 5.40 | 4.76 |
| dark `--text` | 14.89 | 13.73 | 12.47 |
| dark `--text-dim` | 7.18 | 6.62 | 6.01 |
| dark `--accent` | 5.66 | 5.22 | 4.74 |
| dark `--danger` | 5.98 | 5.52 | 5.01 |
| dark `--warn` | 9.90 | 9.13 | 8.29 |
| dark `--ok` | 7.83 | 7.22 | 6.56 |
| light text on `--accent-dim` | 13.19 | | |
| dark text on `--accent-dim` | 6.60 | | |

Minimum 4.5:1 (AA, normal text - nothing in this UI is large text). These numbers were recomputed by
the test, not copied from the brief; they agree with the brief's own measurements to the second
decimal.

**`--line` had to move: the brief's `#d3d9e0` fails the parity criterion.** WCAG 1.4.11 (3:1 for a
control boundary) is met by neither theme, and this round did not chase it - but *parity* is a
criterion, because a light theme whose borders are less visible than the dark theme's is a light theme
with invisible borders:

| | `--line` on `--bg-panel` |
|---|---|
| the brief's light `#d3d9e0` | **1.422:1** |
| dark `#333a45` (frozen) | 1.456:1 |
| shipped light `#c6cdd7` | 1.601:1 |

`#c6cdd7` is the same hue family, one step darker, and it clears the dark theme by 10%. This is the
only value this round changed from the brief's starting palette.

### The switch

The header row's **last preference** - the two preferences sit at the row's right end and the
command palette's entry follows them as the row's last control. This round shipped the switch at the
literal top-left because the requester asked for that placement; the requester withdrew it the same
day, and the row was re-arranged (the switch to the right end, the browse scope and the refresh
action to the grid's own row) - the arrangement, why preferences sit outermost, and the falsifications
that pin the order are in [topbar-layout](topbar-layout.md). What follows is the switch's own markup
and behaviour, which the move did not change:

    <label class="field-inline theme-switch">
      <input type="checkbox" id="theme-dark" role="switch" />
      <span data-copy="theme.dark"></span>
    </label>

- **A checkbox, because that is this repository's pattern** (`#recursive-toggle`, the filter
  checkboxes), and `role="switch"` is how ARIA says a checkbox is a switch. The accessible name comes
  from the wrapping label - so there is no tooltip and no `aria-label` to keep in step with the
  visible words.
- **The label names the thing toggled** (深色模式 / "Dark mode" / ダークモード) and does not change with
  the state. A control whose words change under the pointer is the ambiguity this repository keeps
  paying for - the same call the tab strip made when it added an underline ("colour alone does not say
  selected").
- **Styled as a switch, from tokens only**: `appearance: none`, a track, a `::before` knob, a
  `:focus-visible` ring, and the knob's transition switched off inside the *existing*
  `prefers-reduced-motion` block (a second media query would have been a second place to forget).
- **One writer.** `theme.js`'s `paint(value)` writes the `data-theme` attribute and the checkbox's
  `checked` from one value, so "the switch says dark while the page is light" is not a state the
  module can be in. The harness checks the pair at every step, and the probe reads both off a real page.
- **No `prefers-color-scheme`, anywhere** - a criterion scans every file under `web/` for it
  (comments stripped, since the token is named in prose there to explain why it is absent). **No
  `?theme=` parameter either**: it would be a second way in, and it could not be applied before the
  first paint without a second copy of the resolution order.

### What the boot does not do

The boot script does not choose, toggle or remember anything. It reads one key and writes one
attribute. Everything else - the switch, the writing, the relabelling - is `js/theme.js`, which
`app.js` constructs at module scope next to `initLocale()` and registers in
`relabelAfterLocaleChange()` like every other component that carries copy.

## Evidence

**A real browser, measured** - `.\.venv\Scripts\python.exe tools\measure_web_layout.py --theme both
--shot <dir>`, headless Chrome, 1280x900, canned `/api/*`. The probe now runs **three** loads, and
each one is a different claim:

- `themes.light`: seeded `localStorage["kdt.theme"] = "light"` **before the iframe is created**, so
  the pass measures the boot path. `attribute: "light"`, `colorScheme: "light"`, **41 tokens
  non-empty**, 20 contrast rows, **0 with `ok: false`**. (The table gained a 21st row on
  2026-09-20 - the command panel's group-head band - and nothing else about these numbers moved.)
- `themes.dark`: the same page, seeded dark. `attribute: "dark"`, `colorScheme: "dark"`, 41
  tokens, 20 rows (**21** since 2026-09-20), **1 with `ok: false`** - `.badge-missing`, white on `#ff5c5c` = **3.03:1**.
  That is a pre-existing value this round deliberately did not touch (dark mode may not change), and the
  instrument is supposed to show it rather than round it away. See the known limits.
- `themes.toggled`: loaded light, then **clicked the real switch** (`#theme-dark`).
  `theme_toggle = {"clicked": true, "before": "light", "after": "dark", "switch_before": {"checked": false,
  "role": "switch"}, "switch_after": {"checked": true, "role": "switch"}, "tokens_match_dark": true}` -
  so "the switch applies the theme a reload would" is a measurement, not a promise, and the switch's own
  state moved with it.
- The geometry did not move: the top bar is **89px** with the new control in it (the switch is 16px
  tall; the row is sized by the brand lockup and the buttons), the readiness strip is still 29px at top
  51, and `.layout` still ends exactly at the 900px viewport. `test_web_readiness.py`'s recorded
  89px therefore stays correct - re-measured, not assumed.
- **0 page errors, 0 unhandled rejections** in all three passes.
- `--shot <dir>` writes `probe-light.png` and `probe-dark.png` at the probe's viewport (the shot
  page stretches the iframe to `100vw/100vh` and the window *is* the viewport, so the PNG is the page
  at the size the JSON was measured at). Both pictures were read: the switch is unchecked with the knob
  on the left in light, checked on a blue track with the knob on the right in dark - the pair the
  harness asserts, seen on a real page.

**Headless behaviour** - `test/fixtures/theme_harness.mjs`, the real `theme.js` plus the real
`theme-boot.js` evaluated as a classic script over a fake DOM:

- seven stored states (dark, light, missing, `Dark`, a JSON blob, an empty string, and a storage that
  throws on read): the boot script and `readTheme()` **agree on every one**, which is the property the
  two key copies exist for;
- a toggle writes the key and moves both the attribute and the checkbox; toggling back does the same;
- a storage that throws on **write** still themes the session and never raises (private mode);
- `checked === (data-theme === "dark")` is asserted after construction, after each toggle, and with a
  hostile storage;
- with no boot script at all the module still themes the document (only the first paint is the
  browser's own);
- `relabel()` repaints the label from the packs.

**Static** - `test/test_web_theme.py` (15 criteria) and the amended
`test/test_web_contract.py::test_index_html_uses_module_scripts_only`:

- the 19 contrast pairs above, in both themes, plus the parity criterion;
- the two theme blocks carry **exactly the same colour-token names**, and the exception (five layout
  constants) is *derived from the values* rather than declared - moving a colour into it turns red;
- every `var(--x)` used in app.css resolves to a definition (a `var(--typo)` paints as nothing,
  silently);
- no colour literal below the three blocks;
- **every legacy value is pinned by name**: 25 rows for the dark block and 15 for the on-image block,
  compared whitespace-insensitively, so reformatting is free and a changed channel is not;
- no `prefers-color-scheme` in any file under `web/`, and no `URLSearchParams` in `theme.js`;
- the switch's place in the header row is pinned as an ordered list (so a swap goes red), its
  markup is the agreed one, and its copy is one translated key in all three packs;
- the boot script is in `<head>` after the stylesheet, is not a module, and is the **only** non-module
  script in the page - with the body-move, the rename and a second classic script each turning red;
- `app.js` hands the real control to `createThemeSwitch`, and registers the switch for relabel.

**Falsified 24 ways**, each: break -> red -> restore -> green, with the restore verified byte-identical
by sha256. The full list is in `falsification.json` next to the round's scratch; the ones that matter
most:

| break | red |
|---|---|
| light `--text-dim` set to `#cdd2d9` (a near-invisible grey) | the 19-pair AA criterion |
| `--line` deleted from the dark block | the token-set criterion **and** the legacy pin |
| a colour token added to the light block only | the token-set criterion |
| light `--line` set back to the brief's `#d3d9e0` | the parity criterion |
| dark `--bg` moved by one step (`#15171b`) | the legacy pin |
| a colour literal added below the blocks | the literal criterion |
| a `var(--text-dim2)` use with no definition | the resolution criterion |
| a `prefers-color-scheme` block added | the OS criterion |
| a second classic `<script>` | the contract criterion **and** the boot criterion |
| the boot script moved into `<body>` / renamed | the contract criterion |
| the boot's key changed to `kdt.theme2` | the agreement criterion |
| the boot's `try/catch` removed | the harness (a hostile storage throws) |
| two header-row controls swapped (the language label and the switch) | the row-arrangement criterion |
| `createThemeSwitch` removed / dropped from relabel | the wiring criterion |
| `input.checked` written outside `paint()` | the one-writer criterion |
| the knob's reduced-motion rule removed | the same criterion |
| the probe stops clicking the switch | the probe criterion |
| the probe seeds the theme after mounting the app | the probe criterion |
| the screenshot renamed `shot-*.png` | the `--shot` criterion |
| a placeholder left unsubstituted | the placeholder criterion |
| the toggle comparison loses its emptiness guard | the synthetic toggle criterion |
| a contrast surface dropped from the table | the coverage criterion |

**Two criteria were green for the wrong reason and were fixed while falsifying** (the part of the round
worth repeating):

1. The toggle comparison reported `tokens_match_dark: true` on the **first live run of the contrast
   table**, in which the page had died on a `ReferenceError` and every token dict was empty. Two empty
   dicts are equal. The summary now requires a non-empty read, and the criterion has a **both-empty**
   case - the first version of that criterion only compared one empty side against a full one, which
   compares unequal on the values alone and stayed green with the guard removed.
2. The falsification harness itself reported a *restored* run as red for a break whose replacement has
   the **same length** as the original: restored within the same second, `(mtime_int, size)` is
   unchanged, and importlib then reuses the bytecode it cached from the broken source. The restore was
   byte-perfect both times (sha256-asserted). The harness now runs with `PYTHONDONTWRITEBYTECODE=1`.

## Judgement calls the parent should confirm

1. **Block 2's selector is `:root, :root[data-theme="dark"]`** rather than a second plain `:root`.
   It reads as what it is ("these are the same in both themes"), and it gives the parser three
   distinguishable selectors - a regex looking for the dark selector alone finds block 2's second
   selector line first, which made an early version of the set comparison pass for the wrong reason.
2. **The zoom arrows' text colour is a new block-2 token (`--zoom-fg`, `#e6e9ef`).** `.zoom-nav`
   used `var(--text)`, which in light mode is near-black on the `rgba(20,22,26,.55)` arrow over the
   `#101216` stage - an invisible paging arrow. Same value as dark's `--text`, so dark is unchanged.
3. **`--danger-bg` in light is `#f7dbdb`**, a quiet danger surface with the button's inherited text
   at 12.67:1 - the same shape as dark's `#5a2020` with light text at 10.39:1.
4. **The light tints use different alphas from the dark ones** (`--tint-ok` is `.10` in light and
   `.14` in dark). The tinted `.diff .add` text is `--ok` on its own tint: at `.14` light would
   sit at 4.44:1, just under AA; `.12` would be 4.57:1; `.10` is 4.70:1.
5. **The switch's placement was the requester's, and it was wrong - it was corrected the same day.**
   "The literal top-left" was what this round was asked for, so the switch shipped there; the
   requester withdrew it once the row was on screen, and the switch is now the header row's last
   *preference*, with the brand lockup the row's first item again (so the 2px accent rule on
   `.brand-lockup` is the row's left edge again, and its comment says so). The lesson is in
   [topbar-layout](topbar-layout.md): a placement handed to an implementer is still a design claim,
   and it has to be checked against what the control actually changes before it ships.
6. **`theme.dark` is one key with no hint and no tooltip.** ui-copy's rule is 引导 beats 说明, and a
   label plus a switch needs no sentence; the longest of the three packs (深色模式 / Dark mode /
   ダークモード) fits beside the switch at 1280px (the shot shows it).

## Known limits

- **WCAG 1.4.11 is not met by either theme, and this round did not chase it.** A control boundary needs
  3:1; light `--line` is 1.601:1 and dark `--line` is 1.456:1 (unchanged). The parity criterion keeps
  light from being *worse* than dark; meeting 3:1 would mean repainting every border, panel edge and
  separator in both themes, which is its own round.
- **`.badge-missing` is 3.03:1 in both themes.** White on `#ff5c5c`, which is dark's old
  `--danger` and the value the badge keeps in light (the badge is an on-image colour). Pre-existing
  and unchanged; the probe's `ok: false` row is the instrument reporting it, not a regression.
- **The paint-timing numbers are the requester's, not this round's.** The millisecond figures above were
  measured before the round; what this round added is the structural criterion that keeps the boot a
  classic `<head>` script. Re-measuring them was not part of the brief.
- **The theme was not exercised on a real dataset or a real backend.** The probe serves the real
  `web/` over a canned `/api/*`; nothing here has been looked at with 1653 real thumbnails, and no
  real root was touched.
- **Only zh-CN was looked at in the screenshots.** The switch's label was not measured with the en/ja
  packs (the probe's Chrome profile is zh-CN), so "Dark mode" and "ダークモード" beside the switch rest
  on the packs' own length, not on a picture.
- **Hover, focus and disabled states are not in the contrast table.** It covers the resting state of
  twenty surfaces. The switch's focus ring is pinned statically (`:focus-visible` + `outline`), not
  photographed.
- **`.btn-danger`'s hover is still the accent colour.** `.btn:hover:not(:disabled)` sets
  `border-color: var(--accent)` for every button - a pre-existing oddity that a red button hovering to
  blue makes more visible; it was left alone as out of scope.
- **The probe now costs three browser loads** with `--theme both` (the default). `--theme light` or
  `--theme dark` runs one.

---

Related: [ui-copy](../ui-copy.md), [design-principles](../design-principles.md),
[topbar-layout](topbar-layout.md), [readiness-strip-and-layout-probe](readiness-strip-and-layout-probe.md),
[command-palette](command-palette.md), [i18n](../i18n.md)
