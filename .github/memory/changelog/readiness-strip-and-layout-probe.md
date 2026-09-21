# "Can I train yet?" in the page header - and the layout probe that measured it

**When to read this**: changing the page header or the three-column layout, the readiness strip
(`web/js/readiness.js`), the cache panel's status line, the export panel's report, or
`tools/measure_web_layout.py`.

## Why (2026-09-20)

The tool exists to turn a directory of images plus captions into a trainable dataset, and the question
it answers - *can I train yet?* - had no single answer. Three surfaces each held a third of it, and
each lived behind a different tab:

- **captions missing** - `GET /api/fs/list`'s `counts.missing_captions`, painted as prose in the top
  bar's census line (`renderCounts()`), with the *action* (select the caption-less images) in the
  gallery toolbar;
- **text-encoder cache stale** - `staleCaches` from `GET /api/cache/status`, listed on the Cache tab;
- **`dataset.toml`** - the export panel's checks (`POST /api/dataset/toml`: warnings, skipped
  subsets, `subsetProblems()`), on the Export tab.

So "can I train yet" meant visiting three tabs and remembering three numbers. The approved item (T1.4,
"the readiness strip") had sat unbuilt since the job-shelf round, and was about to go quiet a second
time, so this round built it - together with the missing half of the previous three rounds.

**The premise check** (the task asked for it, and two of the last three rounds were dispatched on
premises that turned out false). All three numbers really are already in the app: `missing_captions`
is in the listing payload and painted by `renderCounts()`; `staleCaches` is the array the cache tab
renders (`app.js`, declared at the old line 1157, filled at 1260); the export report is real
(`subsetProblems()`, `paintReport()`). **No backend change was needed**, and none was made. One
detail the task did not know: the report is closure-local (`lastReport` inside
`createExportPanel`), so "reuse the state the app already holds" meant *exposing* it, not copying it -
`onReport` hands the computed summary object out, and nothing is recomputed.

Second finding, and the reason the second half of this round exists: **the layout numbers in the last
three changelogs were measured with an ad-hoc Chrome probe that was deleted afterwards**
(`.probe/scope_strip_layout.py`, gone before this round). "Never seen in a real browser" (gap 5) was
being re-earned every round instead of being closed. `tools/measure_web_layout.py` is that probe,
committed.

## The decision

### The strip

- **A page-header row, not a panel.** `#readiness` is a child of `<header class="topbar">` in
  `index.html` and `flex: 1 1 100%` puts it on its own line under the controls. That is the only
  place that is on screen on **every** tab without competing for height with the scope strip and the
  caption dock (both in the right column, both already fighting for it) or with the job shelf and
  toasts (fixed, bottom left and right). Measured: 29px tall, inside the 89px header.
- **Every number is another surface's number.** The strip holds no state of its own, fetches nothing,
  and counts nothing:
  - the caption segment is painted from the same `counts` object `renderCounts()` paints the top
    bar with - one call, two surfaces (`readiness.setCounts(counts || null)`);
  - the cache segment is painted by `cacheStatusText(cacheView())` - **the same function the cache
    tab's status line is painted with** - so "2 possibly stale entries" is one definition, not two
    (`readiness.js` is the only file in the frontend that contains `t("cache.statusLine"`);
  - the export segment is painted from `onReport({root, summary, problems})`, where `summary` is
    the very object `paintReport()` paints its own summary line from (`t("export.summary",
    computed)`), and the segment reuses the panel's own headings (`export.warningsTitle` /
    `export.skippedTitle`) rather than paraphrasing the numbers.
  A fourth source of truth is what the design constraint forbids, and it is also what the sharpest
  criterion in `test/test_web_readiness.py` forbids: two wording sites for the stale-cache count is
  the failure mode, in either direction.
- **Each segment is the action that already exists** - not a new one, and not a sentence about one:
  - captions -> `selectMissingCaptions()`, the gallery toolbar's own action (the handler was
    extracted so the button and the strip run one copy of it, including its "mode before filter
    recompute" ordering);
  - cache -> retry `loadCacheStatus()` when the status load failed, otherwise
    `rebuildAllStaleCaches()` - the cache tab's own confirmation and its own job (the confirm used to
    live in the button's handler and now lives in the shared function, counted by a criterion);
  - `dataset.toml` -> the Export tab, where the toml is generated, read and copied. That segment is
    always clickable, because even a clean report is worth opening.
- **Failure states follow the cache tab's precedent.** A failed `/api/cache/status` paints
  `cache.loadFailed` (never "0 stale"), stays clickable (the click is the retry) and carries
  `is-bad`; a successful retry puts the real count back. A caption count that nobody reported hides
  the segment instead of claiming completeness, and no directory open hides the whole row -
  `host.hidden = counts === null`.
- **The caption segment says nothing when there is nothing to say.** Two cases hide it, and a hidden
  segment is emptied rather than left holding its last claim (otherwise "hidden means no claim" would
  depend on the order the states happened to be painted in):
  - `counts.missing_captions` was not reported (nothing to claim);
  - `counts.images === 0` - **the dataset root**, and any folder-only level: the gallery is drawing
    folder cards, and "captions complete" over them is a comfort nobody earned. The strip itself stays
    on screen there, because the other two segments still have something to say.
- **The missing-caption number is stated once.** It used to be printed twice in one header - `缺失
  2` at the end of the census line in `#dir-counts` and `缺失 caption 2 张` one row below in the
  strip. The two surfaces are still painted from the same `counts` object in the same
  `renderCounts()` call (which is what keeps them from drifting), but the number now appears only
  where it is an action: `{missing}` came out of `browse.counts` / `browse.countsRecursive` in all
  three packs and the `missing:` parameter out of `renderCounts()`.
- **The toml segment states what the app knows, not a disk fact.** It said `dataset.toml 未生成` /
  "not generated yet" / `は未生成` - a claim about the file on disk that this app cannot make: no
  endpoint returns the file, so a `dataset.toml` written by an earlier session (or by hand) would be
  reported as missing. It now says `dataset.toml 未检查` / "dataset.toml not checked yet" /
  `dataset.toml は未確認`, and the key was renamed with it (`readiness.tomlUnchecked`) because the old
  name *was* the claim.
- **Copy**: five keys in all three packs, one of them renamed (`readiness.captionsMissing` /
  `captionsOk` / `tomlUnchecked` / `tomlInconsistent` / `tomlReady`); the cache segment reuses
  `cache.statusLine`, `cache.loadFailed`, `cache.progress` and the export segment reuses the two
  export headings, so the strip and the panels cannot drift in wording either. The three segments are
  state-shaped ("2 images missing a caption" / "2 possibly stale entries" / "dataset.toml ready")
  rather than verb-first - see the judgement calls.

### The layout

The strip is a second header row, and `.layout` was sized by `height: calc(100% - 45px)` - a
hard-coded header height that was **already 3px short** (the header is 48px, and `.layout`'s bottom
edge measured 903 in a 900px viewport, which is where the page scrollbar came from). The page is now a
flex column (`body`), the header is `flex: 0 0 auto`, and `.layout` is `flex: 1 1 auto;
min-height: 0`. Measured afterwards: the layout ends exactly at the viewport at 900 / 720 / 600px, and
the centre column gained the 15px the page scrollbar used to take.

### The probe

`tools/measure_web_layout.py` serves the real `web/` plus a **canned `/api/*`** over an ephemeral
loopback port, loads the real `index.html` in a same-origin iframe in headless Chrome, drives it
(click a tile, walk all six tabs, generate the dataset.toml) and prints the geometry as JSON. It never
starts the backend, never touches a dataset (the directory it pretends to serve, `/probe/dataset`, is
not a path on any machine and a request outside that tree is refused with 403), and writes exactly one
thing - a Chrome profile in a scratch directory it prints and removes again. `--scenario fail` makes
`/api/cache/status` answer 500 and `--scenario empty` makes it answer no stale caches, which is how
the strip's failure and "nothing stale" states were measured in a browser; `--root-images 0` makes
the canned root folder-only (a real dataset root), which is how the hidden caption segment was
measured. A machine with no Chromium gets a sentence and exit code 2, not a traceback.

## Evidence

**A real browser, measured** - `tools/measure_web_layout.py` (headless Chrome 153.0.8010.48, 1280px
wide, one image active with its caption loaded, five canned images of which two have no caption and two
canned stale caches):

| viewport | header | layout (bottom) | centre gallery | tab panels | caption dock | scope strip | readiness strip |
|---|---|---|---|---|---|---|---|
| 900 px | 89 | 811 (900) | 666 | 309.5 | 346 | 60 | 29 |
| 720 px | 89 | 631 (720) | 486 | 181 | 294.5 | 60 | 29 |
| 600 px | 89 | 511 (600) | 366 | 121 | 234.5 | 60 | 29 |

- **Before this round** (same probe, same canned page, and matching the numbers the previous three
  changelogs report): header 48, layout 855 ending at **903** in a 900px viewport (a 3px page
  overflow), tab panels 354. So the strip costs the column **44px** of panel height at 900px
  (354 -> 309.5) and 22px at 720/600px - on the short viewports the dock's `max-height: 50%` cap
  absorbs the rest (317 -> 294.5, 257 -> 234.5). The scope strip and the dock are unchanged.
- **The three segments, on screen on every tab**: `缺失 caption 2 张` | `疑似过期 2 条` |
  `dataset.toml 未检查` on all six tabs, at the same box (top 51, bottom 80) before and after every
  tab switch.
- **Agreement, read off the page**: the census line says `本目录：图片 5 · caption 3` and the caption
  segment says `缺失 caption 2 张` - the missing count is stated **once** now, and it is the strip
  that states it; the cache tab's status line and the cache segment are the **same string**,
  `疑似过期 2 条`; after clicking the export panel's own Generate button the toml segment becomes
  `警告 1 条（训练器会静默接受）`, the panel's own warning heading.
- **The failure path, in the browser**: with `--scenario fail` the status line and the segment both
  read `读取缓存状态失败` (segment `is-bad`, still clickable); with `--scenario empty` both read
  `疑似过期 0 条` and the segment is disabled and `is-ok`.
- **A folder-only root, in the browser** (`--root-images 0`, i.e. what a real dataset root looks
  like): the census line reads `本目录：图片 0 · caption 0`, the strip is still on screen at 29px with
  the cache (`疑似过期 2 条`) and toml (`dataset.toml 未检查`) segments, and the **caption segment is
  hidden and empty** - no "captions complete" over a level of folder cards.
- **No page errors**: 0 uncaught errors, 0 unhandled rejections, 0 error toasts across the whole run;
  every `/api/*` request the page made was answered by the canned API (the run's request log is part
  of the JSON output).

**Headless behaviour** - `test/fixtures/readiness_harness.mjs` imports the real `readiness.js` over a
fake DOM: three segments; the caption segment follows `missing_captions` through missing -> complete ->
missing again, **hides (and empties itself) both on an unknown count and on a directory with no
images** - the strip row staying visible in the second case - and closes with the directory; the cache
segment equals `cacheStatusText(view)` for the stale, failed, recovered and rebuilding views, **says
"could not read" instead of "0 stale"** when the load failed, and stays clickable there; the toml
segment walks missing -> warning -> skipped -> inconsistent -> ready with the export panel's own
numbers and headings; each segment's click reaches the callback it was given; and a locale switch
repaints all three from held state, with `setLocale()` alone deliberately changing nothing.

**Static** - `test/test_web_readiness.py` (11 criteria): `#readiness` is in the top bar and in none
of the three columns, the tab panels or the right column's strips; the page is a flex column, `.layout`
takes the remaining height and no longer contains a `calc(100%`; `renderCounts()` feeds the strip from
the same counts object the census line is painted with, and **no longer paints the missing count**
(no pack may keep a `{missing}` placeholder nothing fills - it would render literally); the
toml segment **states what the app knows** (each pack's `readiness.tomlUnchecked` must contain its
language's "checked" word and not its "generated" word, and the old key name may not survive anywhere
in `readiness.js`); the stale-cache sentence exists **exactly once in the whole frontend**, the cache
tab paints through `cacheStatusText(view)` in both branches, and `readiness.setCache(view)` is handed
that same view; `paintReport()` paints its summary line and hands the strip the same object, and
`clearReport()` tells the strip `null`; the strip contains no `api.`, no `fetch(` and no app state;
the three segments call the three existing actions and the rebuild confirmation exists once;
`relabelAfterLocaleChange()` repaints the strip; every state has non-empty copy in all three packs.

**Falsified 33 ways** (break -> red -> restore -> green, each observed; script-driven, byte-exact
restore verified - the whole set was re-run after the three follow-up changes). The ones that matter
most:

| break | red |
|---|---|
| the strip counts the stale caches itself (`t("cache.statusLine", {n: 0})` in the strip) | `test_the_cache_segment_and_the_cache_tab_share_one_number` **and** the harness |
| the cache tab words the count itself again | `test_the_cache_segment_and_the_cache_tab_share_one_number` |
| the strip is handed `{subsets: 0, images: 0, ...}` instead of the panel's `computed` | `test_the_export_segment_reports_the_panel_s_own_summary` |
| a new directory keeps the previous report (`onReport(null)` dropped) | same criterion |
| `renderCounts()` stops feeding the strip | `test_the_census_line_and_the_strip_are_painted_from_one_counts_object` |
| the census line prints the missing count again | same criterion |
| a pack keeps a `{missing}` placeholder nothing fills | that criterion **and** `test_placeholders_match_across_locale_packs` |
| a directory with no images claims complete captions | the harness |
| a hidden caption segment keeps its last claim | the harness |
| the zh toml segment claims the file is not generated | `test_the_toml_segment_states_what_the_app_knows_instead_of_a_disk_fact` |
| `readiness.js` references the old key name | that criterion **and** the harness |
| `--root-images` is ignored by the canned listing | `test_the_canned_root_can_be_the_folder_only_level_a_real_dataset_root_is` |
| `readiness.setCache(...)` is handed a fresh object instead of `view` | the agreement criterion |
| the failed status is painted as `0 stale` | the harness ("a failed /api/cache/status was painted as 'no stale cache'") |
| an unknown caption count is painted as complete | the harness |
| `relabel()` becomes a no-op | the harness |
| `readiness.relabel()` dropped from `relabelAfterLocaleChange()` | `test_the_strip_is_repainted_on_a_language_switch` |
| the rebuild confirmation put back into the button handler | `test_the_three_segments_run_the_actions_the_panels_already_have` |
| `#readiness` moved into `.panel-right` | `test_the_strip_is_a_page_header_row_outside_every_panel` |
| `.layout` back to `calc(100% - 45px)` | `test_the_header_row_costs_the_columns_height_instead_of_overflowing` |
| the ja pack loses one segment key | that criterion **and** `test_web_contract` |
| the probe answers a listing outside the fake root | `test_a_listing_outside_the_fake_root_is_refused` |
| the canned listing is not pure (a random mtime) | `test_the_canned_api_is_pure` |
| the probe writes a dump next to the scratch directory | `test_a_browser_that_cannot_run_fails_cleanly_and_writes_only_the_scratch` |
| "no browser" exits 0 | `test_no_browser_is_a_message_and_not_a_traceback` |
| the server starts before the browser is found | `test_the_tool_starts_no_server_when_it_cannot_run_a_browser` |
| the wrapper placeholder renamed on one side only | `test_the_wrapper_page_carries_the_placeholders_the_handler_substitutes` |

Three **pre-existing** criteria were pinned to the exact lines this round moved and were rewritten to
follow the code, not weakened: `test_web_filter`'s missing-mode ordering (now read in
`selectMissingCaptions()`, plus the new wiring assertion), `test_web_failure_states`'s "a failed load
never reports a count" (now read in `cacheStatusText()`), and `test_web_jobs`'s rebuild progress line
(now read in `cacheStatusText()`, plus the tab's call site).

Gates: `.venv\Scripts\python.exe -m pytest test/ -q` -> **963 passed, 2 skipped** (was 929/2 before this
round; +11 readiness criteria in `test/test_web_readiness.py`, +23 in
`test/test_measure_web_layout.py`), `python -m pytest test/test_docs_index.py -q` -> **9 passed**,
all **20** `test/fixtures/*_harness.mjs` green (exit 0).

## Resolved after review (2026-09-20)

The review accepted the round and asked for three changes; all three are in, each with its own
criterion and falsification:

1. **The missing count is stated once.** `{missing}` is gone from `browse.counts` /
   `browse.countsRecursive` in all three packs and `missing:` is gone from `renderCounts()`. The
   criterion that used to pin "the census line paints the count" was **rewritten, not deleted**: it now
   asserts what is true - the strip is fed the same `counts` object the census line is painted from,
   the census line does not paint the count, and no pack keeps a `{missing}` placeholder that nothing
   would fill (which would render literally, and which the cross-pack parity test cannot see).
2. **The caption segment is hidden on a folder-only level.** `counts.images === 0` hides it, and a
   hidden segment is now **emptied** - without that, "hidden means no claim" depended on the order the
   states happened to be painted in, which the harness caught the moment the new case was added after
   the ok state. Harness criteria for the hidden state and its recovery; measured in the browser with
   the new `--root-images 0`.
3. **The toml segment was reworded to a "not checked yet" framing** in all three packs, and the key was
   renamed `readiness.tomlMissing` -> `readiness.tomlUnchecked` because the old name *was* the claim.
   A criterion reads each pack's value and requires the "checked" word, forbidding the "generated" one.
   **The backend could answer honestly** - a new endpoint that stats `<root>/dataset.toml`, or an
   additive field on `/api/fs/list` - but that is a §4 contract change (p0-spec plus acceptance), and
   this round was told not to add a request, so it did not.

## Judgement calls the parent should confirm

1. **The strip's segments are state-shaped, not verb-first** - accepted in review. All three *do* the
   action, but their labels state the fact (`缺失 caption 2 张`, `疑似过期 2 条`, `dataset.toml 就绪`)
   because two of the three labels are the owning panel's own words, and reusing them is what makes the
   agreement criterion airtight. Verb-first labels would need new copy for the cache and toml segments
   and would lose the "same string as the panel" property.
2. **`未检查` / "not checked yet" / `未確認` introduces a word the rest of the UI does not use** (the
   export panel says 生成 / "Generate"). The alternative that stays strictly inside the panel's own
   vocabulary is reusing `export.empty` ("还没有生成结果" / "No result yet") for the segment, at the cost
   of not naming `dataset.toml` in a row whose other two segments do name their subject. The review
   asked for the "not checked yet" framing, so that is what shipped; say the word if the other trade is
   preferred.
3. **The tool's scratch directory is a `tempfile.mkdtemp()` under the system temp** - accepted in
   review. Chrome needs a profile directory, and a repo-local one would show up in `git status`.
   `--scratch` puts it elsewhere, `--keep-scratch` keeps it.
4. **Edge/Chromium count as "a browser"** in `find_chrome()` (last resort, after Chrome on PATH and in
   the install locations) - accepted in review. The probe only needs `--headless --dump-dom`, and
   failing on a machine that has a working Chromium would be the wrong kind of strict. The chosen
   binary is in the JSON output.

## Known limits

- **The browser run is one page state, not every state.** It measures the strip with a stale cache
  count, a missing-caption count and (after Generate) a warning; the failed and empty cache states were
  measured with `--scenario fail` / `empty`, and the folder-only root with `--root-images 0`; but the
  *rebuilding* state, a running job on the shelf, and the filter strip's visible state are not driven
  by the probe.
- **The strip says nothing about captions at a folder-only level.** Deliberate (see above), but it
  means "is anything missing a caption?" cannot be answered from the dataset root - the answer needs a
  directory that actually holds images. The gallery's own empty state is what tells the user to go
  deeper.
- **Only zh-CN and 1280px wide by default.** `--width`/`--height` change the viewport, but the locale
  comes from the browser profile; a long English or Japanese strip was not measured for wrapping.
- **The probe clicks, it does not interact**: no hover states, no keyboard, no dialogs, no scrolling.
  Gap 5 ("never seen in a real browser") is now closable for *layout* questions and is still open for
  interaction ones - the batch panel's first-screen feel, focus traps, and disabled-button tooltips
  still rest on fixtures and reading.
- **The probe needs Chrome**, so it stays manual: `test/test_measure_web_layout.py` only checks the
  parts that can rot without a browser (import, the canned API's completeness and purity, the fake root,
  the clear failure paths, and that nothing is written outside the scratch directory).
> **Superseded 2026-09-20** by [readiness-toml-status](readiness-toml-status.md): the app now reads the
> file's state (§4.16, one read-only stat of the dataset root), so the bullet below about the segment
> never reading the file no longer describes the code - it is kept as the state this round started from.
- **The export report can be older than the directory.** The strip reuses the panel's report, including
  its staleness: changing the dataset outside the app re-lists the gallery (the 15s poll) but does not
  clear the export panel's report, so a `dataset.toml 就绪` segment can describe a listing that has
  since changed. `未检查` is honest about a different limit in the same family: the app never reads the
  file, so the segment reports the state of *its own checks*, and a dataset.toml that exists but was
  never re-generated in this session stays "not checked". Being as fresh as the panel it copies is the point - a second, fresher count is the
  drift this design forbids - but it means the strip is not a live claim.
- **The strip is not a live region.** A screen reader learns about the numbers when the user navigates
  to the buttons, not when they change (the same call the gallery filter strip made).
- **`cache.progress` on the strip has no cancel.** While a rebuild runs the segment is a disabled
  status line; cancelling is on the cache tab and the job shelf.

---

Related: [i18n](../i18n.md), [ui-copy](../ui-copy.md), [changelog/caption-dock](caption-dock.md),
[changelog/gallery-filter-strip](gallery-filter-strip.md),
[changelog/shared-scope-strip](shared-scope-strip.md), [changelog/job-shelf](job-shelf.md)
