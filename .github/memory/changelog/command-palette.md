# The command palette (T3): one keyboard route to the app's own actions

**When to read this**: changing `web/js/palette.js`, `app.js`'s command table or
`relabelAfterLocaleChange()`, any control id the table names, the job shelf's change hook, or the
palette steps in `tools/measure_web_layout.py`.

## Why (2026-09-20)

The last of the four redesign items, and the only one that **adds a surface** instead of
reorganising one. The app answers "which action do I want" with six tabs, and half of what a user
does on a dataset - preview the tagger, write a batch, rebuild the caches, generate the
`dataset.toml`, start the export - is behind one of them. The palette puts every one of them in one
list on one keystroke.

**Three premise corrections** while checking the task against the code (the task asked for them,
and two of the last five rounds were dispatched on premises that were false):

1. **The caption dock has no "discard".** Its only action button is `#btn-save-tags`; the raw-mode
   toggle and the fold are the other two controls, and leaving raw mode *asks* before truncating
   rather than discarding. There was nothing to mirror.
2. **The roots are not in the top bar.** `#btn-add-root`, the inline add form and the root list live
   in the **left column** (`settings.js`); the top bar carries the language, "include
   subdirectories", refresh and the readiness strip. "The top bar's root controls" named controls in
   another column.
3. **Six tabs is right** (filter / autotag / cache plus the batch / export / scale panels, which
   append themselves), but the gallery toolbar is *not* behind a tab either - so "reaches what is
   behind a tab" is the wrong ownership rule on its own. It became one of two.

## The decision

### What the palette owns, and what it deliberately does not

Derived from the source, every action a user can perform today, and the split:

| Where it lives | Actions | In the palette? |
|---|---|---|
| Tab strip | filter / autotag / cache / batch / export / scale | **Yes** - six entries, the reason the surface exists |
| Top bar | language select, the command bar (the row's last control) | the language is a value control (**no**); the command bar is not a command in its own list (**no**) |
| Left column | root list (open a root), add root, remove root, the picker entry, parent directory | parent and "choose dataset directory…" **yes**; opening a root **no** (that is the palette's own "where am I" question, and the rows are one click in a column that is always on screen); add/remove a root **no** (two halves of one inline form) |
| Gallery toolbar (+ the empty-state 清空全部 beside the grid) | refresh, select all, select missing caption, clear selection, clear filter, "include subdirectories" (refresh and the browse scope moved here 2026-09-20) | refresh and the four actions **yes**; the browse scope is a mode control (**no**) |
| Gallery | tile click, Ctrl+A, space, z (zoom), arrows, marquee, double-click | **No** - keys the gallery owns while it has focus, and a cursor the palette does not hold |
| Scope strip | the four scopes | **No** - a value control (`#scope-select`) |
| Caption dock | save now, raw mode, fold | save **yes**; the other two are mode toggles (**no**) |
| Filter tab | clear tags, clear all, search, sort, the two layer checkboxes, the frequency table's tri-state | clear all **yes**; `clearTags` **no** (one condition of the same question, and the two would need indistinguishable reasons); the rest are fields |
| Tagger tab | model, thresholds, options, write mode, preview, run, cancel | preview and run **yes**; cancel **no** (the job shelf already carries every running job's cancel, on screen on every tab, and calls the panel's own path); the rest are fields |
| Cache tab | refresh, rebuild all, cancel, one row per stale cache | refresh and rebuild-all **yes**; cancel **no** (the shelf); a single cache **no** (per-row, and it is a confirmation dialog's subject) |
| Batch tab | preview, write, cancel, the op fields, the common-tag rows | preview and write **yes**; cancel **no** (the shelf); the rest are fields |
| Export tab | generate, copy, download, the parameter fields | generate **yes**; copy/download **no** (they act on the textarea the reader is looking at) |
| Scale tab | start, cancel, target directory, the parameter fields | start **yes**; cancel **no** (the shelf); the rest are fields |
| Zoom overlay | close, prev/next, zoom, reset, its own tag editor | **No** - a modal owns the keyboard while it is open, and it already has its own keys |
| Dialogs (picker / confirm / model roots) | their own buttons and answers | **No** as *entries*; the two entry points that open them ("choose dataset directory…", "model search directories…") **are** entries |

The rule, stated once: **the palette owns verbs that are worth reaching from anywhere; it is not a
settings editor, not a second cancel button, and not a way into a modal.** Value and mode controls
are excluded because their meaning *is* their current value and one list cannot show four selects
honestly; the cancels are excluded because the job shelf already carries them on every tab; the
dialogs' own actions are excluded because they belong to the dialog's keyboard while it is open.

25 commands: 7 view, 3 browse, 1 settings, 4 selection, 1 caption, 9 jobs. (This line said 23
until 2026-09-20: the crop round added `view.crop` and `crop.start` and the count was never
re-measured. The probe reads 25 rows on an empty query.)

### No privileged paths - the property the feature stands on

The command table in `app.js` is **data**. Each entry is
`{id, group, labelKey, control, run, available}`, where:

- `run` is **a single call to the function the named control's own listener calls**. Where a button
  was wired to an inline closure, the closure's body was extracted into a named function
  (`refreshBrowse`, `goToParent`, `cancelCacheRebuild`) and **both** the button and the table now
  reference it; where the action belongs to a panel, the panel exposes the very function its button
  calls (`autotag.js`: `preview: runPreview` sits next to `previewButton.addEventListener("click",
  () => { void runPreview(); })`).
- `available` is `gate(control, ...reasons)`: the reasons are the wording the reader gets, and the
  **control has the last word** - a disabled or hidden button is not a route to its action, whatever
  the reasons believe. If the two ever disagree the entry goes unavailable instead of becoming the
  privileged path the design forbids.
- `control` names the element the entry stands for (a button id, or `tab-<name>`, which
  `setupTabs()` assigns). A control id that does not exist is not a broken entry, it is an entry
  with no gate - so a typo is a criterion failure.

`palette.js` renders that table and nothing else: it imports `strings.js` (copy) and
`navscroll.js` (scrolling one row into view) and no other module, contains no `api.`, `fetch(`,
`state.` or action name, and its only effect is calling the `run` it was handed. Three panels
gained an id on a button they build in JS (`#btn-batch-preview`, `#btn-batch-apply`,
`#btn-scale-start`, `#btn-export-generate`) and the roots panel's picker button became
`#btn-pick-dataset` - because a control the palette cannot name is a control whose disabled state
the palette cannot obey.

**The criterion** (`test_web_palette.py::test_every_command_runs_the_function_its_own_button_runs`)
parses the table, requires `run` to match `() => fn(args)` (no body of any kind), compares `fn`
with a hand-written expectation per command, and asserts the wiring that proves the button calls the
same function - in the module that owns the button. It is complemented by the panels' own exposure
pins (`preview: runPreview,` next to the button's listener) and by "the table carries no
implementation" and "palette.js owns no action of its own".

### The keyboard model, and the conflicts it had to resolve

**Ctrl+K (Cmd+K on macOS)** - the combination every editor and code host uses for exactly this, and
the app had claimed none of it. Registered on the **document** (not on a node that has to be focused
first), together with a **visible top-bar entry** (`#btn-palette`): a gesture nobody can see is not
discoverable. Since 2026-09-20 that entry is a **command bar** and shows the gesture itself as a
keycap (`palette.openKey`, a key name and deliberately untranslated) rather than naming the feature -
see [command-bar-and-census-row](command-bar-and-census-row.md).

**Confirmed 2026-09-20 (the topbar-layout round): the entry's place changed, not its shape.** The id,
the tooltip, the accessible name (`palette.openTitle`), the click handler and the criterion that pins
them are as above; it measured 48px wide at 1280. What changed was the **row**: 刷新 left the header
for the gallery toolbar (it re-fetches the listing, and the listing is the grid's), the header's
decorative separator went with it (a boundary needs two sides), and the entry moved past the two
preferences (语言, 深色模式) to become the row's **last** control. The reasoning and the measurements
are in [topbar-layout](topbar-layout.md).

**Then superseded the same day (the command-bar round): the shape was decided after all.** The entry
is a 230.4px bar at 1280 that carries the palette's own invitation copy and a Ctrl+K keycap - and the
panel it opens is a **dropdown anchored under it, not a modal**. `palette.open` ("命令"/"Commands")
was deleted from the three packs (one idea, one string: the bar shows the gesture, so the key that
named the feature is dead copy), and `palette.openKey = "Ctrl+K"` took its place next to
`palette.openTitle`, which must contain it (WCAG 2.5.3). What the shell change did to this file's
claims is listed under "The modal shell, replaced" below; the full round is
[command-bar-and-census-row](command-bar-and-census-row.md).

The conflicts found by reading every key handler in the frontend, and how each was resolved:

| Owner | Its keys | Resolution |
|---|---|---|
| `gallery.js` (`#gallery`, tabindex 0) | Ctrl+A, z, Escape, Space/Enter, arrows, Home/End, Shift+arrow | No collision: the palette opens on a modifier combination, and with the palette open the gallery is behind a modal and does not have focus. Nothing in the gallery changed. |
| `zoom.js` (document listener while it is open) | Escape, +/=/-/0, arrows | The palette **refuses to open** while the zoom overlay is on screen (`.zoom-backdrop:not([hidden])`), so the two never hold the keyboard at once. |
| picker / confirm / model-roots dialogs (document listeners) | Escape, Enter, their own buttons | The same guard, over `.dialog-backdrop`: opening the palette over a dialog that claims `aria-modal="true"` would steal focus out of it and the two Escape handlers would fight. |
| breadcrumb dropdown (document click + Escape) | Escape closes it | A mouse click anywhere else already closes it; Ctrl+K does not, so `openPalette()` calls `closeCrumbMenu()` first - the keyboard's equivalent of that click. |
| tab strip | Left/Right/Home/End **on the tabs themselves** | Untouched, and the palette deliberately does **not** hijack Home/End (in a search field they move the caret). |
| caption editor, batch common-tag input | typing, Enter, Escape in a field | The palette's own input is where typing goes while it is open; the page behind stays reachable, so the editor keeps its keys whenever focus is in it. |

### The modal shell, replaced (2026-09-20)

While open: ArrowDown/ArrowUp move the active option (wrapping), Enter runs it, Escape closes and
**hands focus back to the bar**, Tab **leaves the panel and closes it** (the options are never
focusable, which is what makes `aria-activedescendant` the announcement), a click on a row chooses
and runs it, and a click anywhere outside closes it. Closing empties the list and the status line, so
a closed panel holds no last claim.

What changed, and what replaced each criterion:

| was (a modal) | is (a dropdown) | criterion |
|---|---|---|
| `#palette` = `div.dialog-backdrop`, `position: fixed; inset: 0` | `#palette.palette-pop`, `position: absolute` inside `.palette-bar-wrap` in the header | `test_the_panel_is_a_dropdown_anchored_inside_the_command_bar` (the old "not inside .topbar" criterion was **flipped**, not deleted) |
| `aria-modal="true"` on the dialog | no `aria-modal`; `role="dialog"` + `aria-labelledby` stay | `test_the_panel_carries_the_roles_a_screen_reader_needs` (rewritten to assert the absence) |
| Tab trapped between the input and the close button | Tab closes the panel and does not swallow the key | `test_the_panel_is_a_dropdown_and_tab_leaves_it` + the harness |
| a click on the backdrop closes | a click outside the panel (and outside the bar) closes | the same criterion + the harness |
| focus restored to "whatever had focus" | focus returns to the bar it dropped from | the same criterion + the harness |
| Ctrl+K toggled | Ctrl+K only opens, and puts the caret back in the field | the same criterion + the harness |
| the entry was a 48px button named by `palette.open` | a bar carrying `palette.placeholder` + a `palette.openKey` keycap, `aria-haspopup`/`aria-expanded` | `test_the_command_bar_shows_the_gesture_and_names_it` |

**The head's typography (2026-09-20).** The panel's name was rendered with `.panel-title`, the small
dim upper-cased label that heads a panel's block - smaller AND quieter than the 13px rows it named,
with the 关闭 button as the loudest thing in the head. One rule scoped to `.dialog-head` now states
the scale for every dialog this app builds (**15 / `var(--text)` / 600  >  13 input and rows  >  12
reasons, status and footer  >  11 group heads**), and this panel's head alone carries a bottom rule in
`var(--line)`, because it is the only head followed by the control the surface is for rather than
by a hint. The A/B measurements (head heights in four real dialogs) are in
[command-bar-and-census-row](command-bar-and-census-row.md).

**Roles**: `role="dialog"` + `aria-labelledby` on the panel, the input is a `combobox` with
`aria-controls` / `aria-expanded` / `aria-activedescendant`, the list is a `listbox` whose
children are `role="group"` boxes (labelled with the same words as the visible heading, which is
`aria-hidden`) each holding `role="option"` rows carrying `aria-selected` and `aria-disabled`.
An unavailable row also carries a visually hidden "unavailable" plus the **reason**, so the
announcement is "Write changes, unavailable, the scope is empty" rather than a grey row.

### Unavailable means shown, with the reason

Nothing is filtered out by availability. An empty query lists **every** command, in the table's own
order, under its group heading - the palette opened on nothing is the app's whole vocabulary, which
is how a user finds out what it can do. A command the app would refuse is painted dim with its
reason on the row ("no directory is open", "the scope is empty", "a job is already running", "no
WD14 model is available", "preview first", "no cache needs rebuilding"), and **Enter on it says the
reason in the live status line** instead of doing nothing - silence reads as a broken key. Fifteen
reason keys, three of which are another surface's own wording reused (`autotag.previewTooManyNote`,
`cache.loadFailed`, `picker.disabled`), so one claim has one string.

The reasons are re-read at every paint, and the **job shelf reports when the set of running jobs
changes** (`onChange` from `start()` / `finish()` only - never a progress tick, which would
rebuild the list under the reader's cursor once a second): a row disabled with "a job is already
running" stops saying so the moment the job ends.

### The matcher (no dependency, no build step)

`palette.js` exports `normalizeQuery`, `matchScore` and `rankCommands`:

- **normalize**: NFKC then lower-case, so full-width Latin and case both fold.
- **one term**: a **contiguous substring** wins and the earlier it starts the better
  (`1000 - at`); only if there is none may the term match as a **subsequence** (characters in
  order, gaps allowed), scored `max(1, 400 - spread)`. Every contiguous hit therefore outranks every
  scattered one.
- **the query** is split on whitespace and **every term must match the same command**; the scores add
  up and ties break on the table's own order.
- **the haystack is the command's wording in every pack** (the key exists in all three, so this is
  the same copy read three ways, not a synonym list to maintain): typing `refresh` in a Chinese
  interface finds the command the Chinese pack calls 刷新, and typing 刷新 in an English one finds it
  too. Measured in the browser, not only in the fixture.
- **an empty query** matches everything (the term list is empty), in the table's order.

## Evidence

**A real browser, measured** - `tools/measure_web_layout.py`, extended with a palette section and
six steps that drive it the way a user does (real `KeyboardEvent`s on the iframe's document, a typed
query, Enter). Headless Chrome 153.0.8010.48, 1280px wide, canned `/api/*`:

| viewport | header | layout bottom | tab panels | dialog | list | rows |
|---|---|---|---|---|---|---|
| 900 px | 89 | 900 | 309.5 | 560 x 563.5 at top 168.3 | 29.5 input + scrollable list | 23 |
| 720 px | 89 | 720 | 181 | 560 x 480.7 at top 119.7 | list 331.2 | 23 |
| 600 px | 89 | 600 | 121 | 560 x 425.5 at top 87.3 | list 276 | 23 |

> These three rows are the **modal** shell's numbers (2026-09-20) and the dialog is centred in the
> viewport, which is why its top moves with the height. The dropdown's numbers are different by
> construction and were re-measured on 2026-09-20 at 1280x900: 560 x 567.5, top **43.5** = the bar's
> bottom + 6, right **1268** = the bar's right edge, 25 rows / 6 groups.

- The header stays 89px and the layout still ends exactly at the viewport at all three heights -
  **the new top-bar button costs the columns nothing** (tab panels 309.5 / 181 / 121 are the numbers
  the readiness round measured), and the dialog fits inside every viewport.
- `palette:opened` (after a real Ctrl+K): `#palette` visible, `#palette-input` focused,
  `aria-expanded=true`, `aria-activedescendant=palette-option-view.filter`, six group headings,
  `role=dialog` and **`aria-modal=null`** (re-measured 2026-09-20; the row count is **25** and the
  status `共 25 项` - see the note on the command count above).
- `palette:arrows`: two ArrowDowns moved the active option filter -> autotag -> **cache**.
- `palette:escape`: hidden, all rows gone, and `document.activeElement` is **`#btn-palette`** -
  the button that had focus before the gesture.
- `palette:no-match`: query `zzzz` -> 0 rows, status `没有匹配的命令`.
- `palette:query`: query **`cache`** (English) in a zh-CN profile -> exactly **one** row,
  `palette-option-view.cache` - the cross-locale matcher, in the browser.
- `palette:ran`: Enter -> the palette closed, focus returned to `#btn-palette`, and the page's
  **active tab became `cache`**. The row really called the app's own action; the claim is read off
  the page, not off the dialog.
- **0 page errors, 0 unhandled rejections** across the whole run (the wrapper collects both).

**Headless behaviour** - `test/fixtures/palette_harness.mjs` imports the real `palette.js` over a
fake DOM: the gesture is on the document and a bare `k` does nothing; an empty query lists every
command in table order under its groups, with the count in the status line; the matcher's rules
(substring, case- and pack-independent, subsequence, every-term-same-command, no match -> the
no-match line); an unavailable row is painted with its reason, carries the spoken "unavailable", and
Enter on it calls **nothing** and says why; the arrows move and wrap; Enter runs the injected function
**once** and closes the palette with the keyboard back on the bar; Escape does the same; **Tab closes
the panel** instead of cycling inside it; a click on a row runs it, a click outside closes it and a
click inside does not; `refresh()` re-reads the state (a row that became available loses its
reason) and `close()` leaves no rows and no claim; Ctrl+K opens and **never toggles**; and **the
palette refuses to open while another backdrop is on screen**. A language switch repaints the rows
and the reasons.

**Static** - `test/test_web_palette.py` (18 criteria since 2026-09-20, one of which runs the
harness) and the criteria in `test/test_measure_web_layout.py` (the probe really presses the keys,
reads the rows, and can pick a pack with `--lang`).

**Falsified 28 ways** (break -> red -> restore -> green, each observed, each restore verified by the
inverse edit). The ones that matter most:

| break | red |
|---|---|
| an entry calls another function (`goToParent` for refresh) | the "same function" criterion |
| an entry grows a body (`() => { refreshBrowse(); }`) | same |
| the button stops calling the shared function | same |
| a panel method is renamed (`scalePanel.begin`) | same |
| the panel stops exposing the function its button calls (`startExport: start`) | same |
| a control id gains a typo | "the controls the commands name exist" |
| the gate names another control / loses `disabled or hidden` | "gated by its own control" |
| an entry calls the API directly | "the table carries no implementation" |
| `palette.js` imports `api.js` | "the palette owns no action of its own" |
| the reason is not painted / the row class renamed | the unavailable-row criterion + the harness |
| Enter runs an unavailable command | same criterion |
| the gesture becomes Ctrl+O | the gesture criterion |
| the modal guard is removed | the refusal criterion |
| Tab is no longer trapped / rows become `menuitem` | the keyboard and role criteria |
| the top-bar button calls the palette directly | the entry-point criterion |
| a term that matches nothing no longer rejects | the matcher criterion |
| a table key that does not exist | the pack criterion |
| `palette.relabel()` dropped from `relabelAfterLocaleChange()` | the relabel criterion |
| the shelf stops reporting a job ending | the shelf-hook criterion |
| the matcher is bypassed / `close()` keeps its status line | the harness |
| `#palette-list` dropped from the probe's SELECTORS | the probe's "surfaces" criterion |
| the probe stops pressing Enter | the probe's palette criterion |
| a dialog grows a backdrop class the guard does not know | "every modal is covered by the guard" |
| the guard forgets the zoom overlay | same criterion + the refusal criterion |

**One criterion that was green for the wrong reason** (caught while falsifying): the first attempt at
breaking the new probe criterion (removing `#palette-list` from `SELECTORS`) left it **green**,
because that break is the *other* probe criterion's responsibility - the palette criterion only pins
the page's own key presses. The falsification was redone against a pin that criterion owns (dropping
`press("Enter")`), and the SELECTORS break was re-run over the whole file to confirm that the
criterion which does own it turns red.

## Judgement calls the parent should confirm

1. **Ctrl+K rather than `/` or Ctrl+Shift+P.** GitHub/Slack/Linear use Ctrl+K; `/` is a bare key
   that would fight the caption editor's fields, and Ctrl+Shift+P is VS Code's alone. The top-bar
   button exists because the gesture alone is invisible.
2. **The cancels are not entries.** The job shelf is on screen on every tab exactly while a job runs,
   carries the panel's own cancel, and that is one click. Four cancel commands would be a third route
   to the same function without answering a question the shelf does not.
3. **The palette is not a settings editor.** The language, "include subdirectories", the scope, the
   tagger's thresholds, the export parameters and the batch op are excluded. Commands that flip a
   value would need the palette to render the *current* value to stay honest, which is a different
   surface (a menu, not a command list).
4. **Reason wrapping.** A reason is one short sentence in the row's right-hand column; a long one
   wraps. Not measured in a browser with the ja pack (the probe can pick a pack since 2026-09-20,
   `--lang ja`, but the reason widths were not re-measured) - see below.
5. **`palette.notReady`** is the gate's fallback wording for "the control refuses and my reasons do
   not know why". It should be unreachable for the shipped table; it exists so that a gap in a reason
   list can never turn into a privileged path.

## Known limits

- **One browser profile, one locale for the palette's own rows.** The numbers above are zh-CN at
  1280px wide. The panel is `min(560px, 94vw)` wide with a `min(46vh, 420px)` list; the en/ja row and
  reason widths are still unmeasured (the layout probe can choose a pack since 2026-09-20, the palette
  steps were not re-run in one).
- **The probe drives the keyboard, not a screen reader.** Roles, names and `aria-activedescendant`
  are pinned statically and asserted in the fixture; what a screen reader actually announces was not
  observed.
- **No ranking beyond the two rules.** No transposition handling, no word-boundary bonus, no
  recent-use ordering: `rfrsh` finds the command, `rfersh` does not.
- **The palette is not a live view of the app.** A job *starting* while the palette is open is
  reported by the shelf and repaints it; a caption finishing a probe, a directory listed by the 15 s
  poll, or the frequency table reloading are not - none of those is wired to it.
- **Opening a root is not a command.** With 200+ roots that would be a sub-list, not a command list;
  the left column remains the way to switch datasets.
- **The command bar costs the header nothing at 1280px** (the row stays 89px, the bar is 28px of the
  34px first row) and the header was measured down to **800px** on 2026-09-20: zh-CN is still one line
  there, en wraps below ~885px (127px, the bar on a second line beside the brand, every control still
  visible and none overlapping). An earlier version of this line claimed the 900px header wraps to
  118.5px - it does not, and the numbers are in
  [command-bar-and-census-row](command-bar-and-census-row.md).

---

Related: [keyboard-and-dialogs](keyboard-and-dialogs.md), [readiness-strip-and-layout-probe](readiness-strip-and-layout-probe.md),
[job-shelf](job-shelf.md), [shared-scope-strip](shared-scope-strip.md), [ui-copy](../ui-copy.md), [i18n](../i18n.md)
