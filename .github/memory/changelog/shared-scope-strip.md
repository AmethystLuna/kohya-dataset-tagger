# One answer to "what am I about to act on"

**When to read this**: changing the right column's layout or its tab strip, the scope control
(`web/js/scope.js`), either of the two panels that write captions, or the export/scale panel's
statement of what it covers.

## Why (2026-09-20)

The processing scope - selected images / this directory / this directory + subdirectories / filter
result - is one question, and it was being asked twice. `createScopeControl` was constructed in
`batch.js` and again in `autotag.js`, each with its **own** value and its own live counts. A user
who picked "selected: 12 images" in the tagger and then switched to the batch panel and pressed its
button acted on a different set of images. Both panels looked like they were answering the same
question, and the answer on the screen was not the one the button obeyed.

The export/scale panel is the other half of the same defect: it had **no** scope at all, and
`/api/scale/run` covers `paths.iter_images(root, recursive=True)` (`api/scale.py:520`) - the whole
directory, subdirectories included - while saying nothing about it.

**Two premise corrections** while checking the task against the code:

1. `test/test_web_scale.py` + `test/fixtures/scale_harness.mjs` do **not** identify the operations
   select by position (there is no select-by-position in either; the harness finds controls by class,
   placeholder and `type`/`min`). The position-based lookup was in `batch_harness.mjs`, and that one
   was already fixed on 2026-09-19. The scale fixture needed no change for a new select.
2. `test/test_web_autotag.py` does not exist; the tagger's diff-stale and gating criteria live in
   `test/test_web_diff.py` and `test/test_web_contract.py`.

## The decision

- **One control, built once, in the column.** `app.js` builds it over `#scope-select` and hands the
  **instance** to both panels (`scope,` in each factory call). `scope.js` is unchanged as an
  implementation: same four scopes, same labels, same "…" rule, same per-directory cache. What is new
  is `subscribe(handlers)`: the old single `onPaint`/`onChange` options could only ever hold one
  panel's hook, which is the same shape of defect one level down. A panel that builds its own control
  is now visible as a second `createScopeControl(` call site, and
  `test_web_scope.py::test_the_control_is_constructed_once_for_the_whole_frontend` counts them
  frontend-wide (today: 1, in `app.js`).
- **The strip is a sibling of `.tab-panels`, not a child.** That element is the column's only
  scrolling region; a control inside it scrolls out of reach, which is what "shared" would stop
  meaning. It is also not inside any `.tab-panel`, so no tab switch hides it.
- **The hooks survived the hoist.** The batch panel still drops its preview and tells the app to
  probe the new set on `onChange`; the tagger still re-derives its diff-stale message and re-gates
  Preview/Run on `onPaint`; the counts are still repainted from the providers.
- **One hook changed shape deliberately**: the counts are now painted by `refreshScope()` **before**
  its hidden-tab early return. That return is what keeps the per-caption-chunk filter pass cheap, and
  the panel's own work still belongs behind it - but the strip is on screen on *every* tab now, so a
  filter change while the batch tab is closed must still move the number the user is reading.
- **The default is the control's own (`selected`, the picked images).** With one shared value there
  can only be one default, and the batch panel's old one (the filter result) is the dangerous end:
  with no filter on, "filter result" is the whole directory, so the tagger's Run would silently cover
  the library where it used to cover the selection. An untouched scope selects nothing, so both
  panels start with their action buttons disabled and the strip says `当前选中（0 张）`. This is a
  real change of the batch panel's default - see the judgement calls.
- **The export panel states its coverage instead of getting the control.** §4.14 freezes the request
  body as a `root`, so "selected images" and "filter result" are not expressible; a control that
  silently does not apply to one of the four values is the defect this round removes. `scale.coverage`
  now sits under the source line: the export covers the whole source directory, subdirectories
  included, and the Scope control does not apply to it. The strip's own hint (`scope.stripHint`) names the two
  panels that do obey it, because the strip is on screen on the Filter/Cache/Export/Scale tabs too.
- Copy lives in the three packs only (two new keys, `scope.stripHint` and `scale.coverage`);
  `scope.label` and the four scope labels are reused. The strip's `<option>` text is painted at
  render time, so `relabelAfterLocaleChange()` calls `scope.paint()` - a pure redraw from the
  providers, no request.

## Evidence

**One live value** - `test/fixtures/scope_strip_harness.mjs` builds the real `scope.js`, the real
`batch.js` and the real `autotag.js` over **one** control (the way `app.js` wires them) and changes
the value once: the tagger's `scopePaths()` and the batch panel's next `/api/captions/batch` body both
become the 40-image directory, the tagger's Preview goes disabled (over the 32-image cap), its note
appears, and Run is offered. The static half counts construction sites (1) and pins that both
factories receive `scope`.

**Nothing silently lost** - each hook has a criterion that fails when the hook is removed:

| hook | criterion | falsification (break -> red -> restore -> green) |
|---|---|---|
| batch preview invalidation | `batch_harness.mjs` §11 (hidden-tab case) + `test_the_batch_panel_still_drops_its_preview_when_the_scope_changes` | drop `invalidatePreview()` from the subscription: *"a scope change while the batch tab was closed kept a preview of the previous set of images"* |
| tagger diff stale + gating | `scope_strip_harness.mjs` (stale message + cap gating) + `test_the_tagger_keeps_its_diff_stale_check_and_its_gating` + `test_web_diff` | drop the tagger's `onPaint`: *"40 images are over the tagger's 32-image preview cap, so Preview must be disabled by the same change"*; keep `onPaint` but drop `refreshDiffStale()`: *"a scope change must say the diff describes other images, got 共 1 张 · 新增 1 个 tag · 删除 0 个 tag"* |
| counts repaint | `scope_strip_harness.mjs` §3 + `panel_locale_harness.mjs` | empty the label loop in `paint()`: *"the selected count is not live: 当前选中（0 张）"* and *"the shared scope control was not repainted on the language switch, got \"Filter result (0 images)\""* |
| counts repaint while the batch tab is closed | `scope_strip_harness.mjs` §4 + `test_the_counts_are_repainted_even_while_the_batch_tab_is_closed` | move `scope.paint()` below the hidden guard: *"a filter change while the batch tab was closed left the strip stale: 过滤结果（3 张）"* |
| one value | `test_the_control_is_constructed_once_for_the_whole_frontend` | add a second `createScopeControl(` to `batch.js`: red (`{'batch.js': 1, 'app.js': 1}`) |
| structure | `test_the_strip_is_in_the_column_and_outside_the_scrolling_panels` | move the strip inside `.tab-panels`: red |
| relabel | `test_the_strip_is_repainted_on_a_language_switch` | delete `scope.paint()` from `relabelAfterLocaleChange()`: red |
| export coverage | `test_the_export_panel_states_what_it_covers_instead_of_implying_a_scope` | drop `coverageLine` from the panel's children: red |
| strip declaration | `test_the_strip_stays_put_in_the_flex_column` | `.scope-strip { flex: 1 1 auto }`: red (it reads the declaration, not the selector) |

**A pre-existing criterion this round found was already vacuous.** `batch_harness.mjs` §11's
"changing the scope kept a preview of the previous set of images" asserted
`applyButton.disabled === true` while the section-10 write job was still in flight - the button was
disabled by `writing`, not by the scope. Removing the invalidation hook left it **green**. The harness
now finishes that job first, asserts the enabled state, and only then changes the scope, so the
criterion fails for the right reason.

**A real browser, measured.** Ad-hoc probe (`.probe/scope_strip_layout.py`, **not committed**):
in-process `http.server` serving the real `web/` plus canned `/api/*` JSON, the real `index.html` in a
same-origin iframe, headless Chrome 153.0.8010.48 `--dump-dom --virtual-time-budget`, one image active with its
caption loaded.

| viewport | column | strip | panels (dock expanded) | dock expanded | dock folded | panels (folded) | panels before this round |
|---|---|---|---|---|---|---|---|
| 900 px | 835 | 60 | 354 | 346 | 129 | 571 | 422 / 639 |
| 720 px | 655 | 60 | 203 | 317 | 129 | 391 | 271 / 459 |
| 600 px | 535 | 60 | 143 | 257 | 129 | 271 | 211 / 339 |

- **The strip is painted once and stays put**: on all six tabs (filter, autotag, cache, batch, export,
  scale) its box is `top 107, bottom 167` at every viewport - inside the column, **never** inside
  `.tab-panels` - and the same DOM node (and the same `<select>`) survives every tab switch and a
  gallery re-render, still carrying exactly four options.
- **One live value, seen in pixels**: the labels read `当前选中（1 张）`, `当前目录全部（40 张）`,
  `当前目录及子目录（… 张）` (unknown, never a fabricated 0) and `过滤结果（40 张）`.
- **It repaints in place on a language switch**: `过滤结果（40 张）` -> `Filter result (40 images)`,
  height unchanged at 60 px.
- **The cost**: the strip takes **68 px** of panel height at all three viewports (422 -> 354 at
  900 px, 271 -> 203 at 720 px, 211 -> 143 at 600 px). The dock's own numbers are unchanged
  (346/317/257 expanded, 129 folded). At 600 px the panels keep 143 px and scroll, so nothing is
  unreachable - but on a short window this is a visible price.

Gate lines: `.venv\Scripts\python.exe -m pytest test/ -q` -> **929 passed, 2 skipped** (was
918/2; +11 criteria in the new `test/test_web_scope.py`), `python -m pytest test/test_docs_index.py -q`
-> **9 passed**.

## Judgement calls the parent should confirm

1. **The shared default is the picked images, so the batch panel's default changed** from "what the
   filter left visible" to "the current selection". The 2026-09-19 decision to keep the batch default
   was about not changing what gets written *silently*; one shared value cannot have two defaults, and
   the other end makes the tagger's Run cover the whole directory by default. If the parent prefers
   the old batch behaviour, it is one option (`defaultScope: SCOPE.FILTERED` on the `createScopeControl`
   call) plus inverting the default assertions in `batch_harness.mjs`, `scope_strip_harness.mjs`,
   `test_web_scope.py` and `test_web_batch.py`.
2. **The export panel still has no scope control.** §4.14's body is a `root`, so two of the four
   scopes cannot be expressed without a backend change (out of scope for this round). The alternative
   - showing the control there and mapping it - would have been a control that lies for half its
   values.
3. **The strip is always visible**, including on the Filter/Cache/Export/Scale tabs where nothing
   obeys it. Hiding it on those tabs keeps the column taller but makes the value look like it
   disappears; the hint (`scope.stripHint`, naming 「批量」 and 「Tagger」) is what pays for always-visible.
4. **`#scope-strip` is a two-line block (60 px)** rather than a one-line row. The hint is on its own
   line because the label + select already fill the 380 px column; folding the hint into a title
   attribute would save ~22 px and cost the statement.

## Known limits

- **The browser probe is not committed** and was run once on this machine at a 1280 px wide viewport
  with the right column at its default width. The canned `/api/*` answers mean no real caption was
  written through the UI there; the write paths are covered headlessly and by the backend's criteria.
- **The export panel's coverage statement is copy, not a count.** It names the rule (whole source
  directory, subdirectories included) but not "1 653 images". A live count is available through the
  control's own recursive listing if that is wanted later; it brings an async load and its failure
  state with it.
- **`scope.stripHint` names two tab labels in prose** (`「批量」` / `「Tagger」`, `「一括」` / `「タガー」`).
  Renaming a tab silently makes the hint wrong; no criterion links the two, and there is no
  placeholder mechanism for "the tab's own label" today.
- **Tab order and focus were not driven with the keyboard** in the browser. The strip sits between the
  tab strip and the tab panels, so it is reached after the tab strip and before the open panel.
- The strip was measured with the zh-CN and en packs only; a longer ja hint was not measured for
  wrapping (ja's is shorter than zh's).

---

Related: [shared-scope-control](shared-scope-control.md), [caption-dock](caption-dock.md),
[gallery-filter-strip](gallery-filter-strip.md), [ui-copy](../ui-copy.md), [i18n](../i18n.md)
