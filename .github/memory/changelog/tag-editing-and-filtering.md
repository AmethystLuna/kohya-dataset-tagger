---
name: tag-editing-and-filtering
description: 2026-09-18 filling out batch tag editing and filtering against dataset-tag-editor: batch gains edit_tags/prepend/regex_replace_text and a dry_run preview, a frontend batch panel, and frequency-table search/sort/selection-set/per-visible-image counts; and why there is no "staging + backup"
metadata:
  type: topic
---

# Batch tag editing and filtering enhancements (2026-09-18)

The user's words: "`stable-diffusion-webui-dataset-tag-editor` — use this git repo's tag-editing features as a reference to improve our filtering and editing logic and UX". (The reference repo is the A1111 extension `toshiaki1729/stable-diffusion-webui-dataset-tag-editor`; its feature surface is in its README and `DESCRIPTION_OF_DISPLAY.md`.)

## Inventory: where the gaps were

The reference repo's usage is "filter first, then batch-change": **every batch edit applies only to the currently displayed (filtered) images**.
Against that, our state at the time was:

| Reference repo capability | Our state (before the change) |
|---|---|
| Filter by Tags: search tags, sort by frequency/name, positive/negative filter groups | the three-state chips (only this / includes this / excludes this) were in use, but **there was no search box** — 1538 tags could only be scrolled; no sorting |
| The tag list shrinks with the currently displayed images | frequency was always for the whole directory |
| Filter by Selection: use the gallery selection as a filter layer | the selection only fed the tagger's "process scope" |
| Batch Edit Captions: append/prepend/remove/replace/regex/dedupe/sort | **all 6 backend ops had been implemented long ago, and the frontend had not one call site** (§4.5 ③ even said "no call site yet" at the time) |
| Edit common tags: edit tags shared in place | none |
| Search and Replace in Entire Caption | none (`regex_replace` only replaces within each tag) |

The last cell is the most glaring: `api.js` exports `batchCaptions` and `BATCH_OP`, and `api/captions.py` implements all six ops,
but the UI has no entry point at all — the feature effectively does not exist.

## Decisions

### 1. The backend adds only three ops; the batch protocol is not rewritten

The frozen `POST /api/captions/batch` shape is unchanged, and `BATCH_OPS` gains:

- **`prepend`** — symmetric with `append` (the reference implementation's "add to the beginning");
- **`edit_tags`** — `pairs:[{find,replace}]` grouped renaming (an empty `replace` = delete) + `tags` additions +
  `position`. One request does "rename + delete + add", so each image is written once and the cache is invalidated once.
  Renaming is a **single mapping** (the first matching pair wins, no chaining): when given both `a→b` and `b→c`,
  the original `a` becomes only `b`. Chaining would make "rename, then rename again" silently change an extra batch of images.
  **Rows are not compacted**: deleting the 2nd common tag does not shift the 3rd into a wrong rename.
- **`regex_replace_text`** — regex replacement across the whole caption (the reference implementation's "Entire Caption" mode).
  Commas in the replacement string **are re-split on bare commas**, i.e. they become new tags — which is exactly the rule the trainer reads.

### 2. Use a `dry_run` preview instead of "staging + Save all changes"

The reference implementation's write model is **staging**: nothing lands until you click "Save all changes", and it can also rename the original files as backups
(`filename.000` …). We **did not copy this**, because this repository's invariant #1 is "writing a caption deletes the TE cache", and all write paths are
**immediate atomic writes**; introducing staging would split that invariant into two states of being, a worse risk than the benefit.

What replaces it is **look before you apply**: when `POST /api/captions/batch` receives `dry_run: true` it writes not one byte and touches no cache,
returning per image `changed` / `before` / `after` with `dry_run: true` at the top level.
The key is **same source**: preview and write share `_apply_batch_op` and the new
`core.captions.normalize_for_write` (strip/drop empty → reject commas → join, the same rule as the write path),
so "the preview says it will change" and "the write really changed it" cannot diverge. Acceptance **A35** directly asserts that
`dry_run`'s `after` is **byte-for-byte equal** to the file read back after the real write.

One hidden risk that had to share a source is fixed along the way: a tag containing a comma is reported `invalid_tag` at preview time,
not failing only at write time. For this, "tag list → writable text" was extracted into `normalize_for_write` (appended to §3.2), and `write_caption` now calls it.

### 3. Frontend: one batch panel, whose scope is always "the currently displayed images"

`web/js/batch.js` (a new module that builds its own tab and panel, the same pattern as `exportPanel`) provides an operation dropdown:
common-tag editing / append / prepend / remove / replace / regex (per tag) / regex (whole caption) / dedupe-normalize / sort by vocab.
The scope is always `gallery.getVisiblePaths()` — **filtering is the way you select images**; there is no second range selector,
or else "what you see in the UI" and "what the batch changes" could disagree.

The write button is bound to "a fresh preview exists": any input/dropdown change goes through `invalidatePreview()`,
disabling write again (the same scheme as autotag's `autotag.diffStale`). The preview table reuses
`autotag.renderCaptionDiff`, so the red/green boxes for before/after have only one implementation.

**A pitfall we hit: the local pseudo-op was missing a mapping.** The dropdown's "common tag editing" value is the local pseudo-op `common`,
and the request body initially sent it straight as `op` — the backend only recognizes `BATCH_OPS`, so clicking preview only got
400 `bad_op`. The fake fetch fixture at the time even "verified" `op === "common"`, because it was written to match the implementation.
The fix is `requestOp()` mapping `common` to `edit_tags`, changing the fixture to assert `op == "edit_tags"`,
and adding a static criterion (falsification: remove the mapping → **2 criteria go red at the same time**).
**Lesson**: if a fixture asserts the implementation's own constant, it cements the implementation's bug too; a criterion should assert the value on **the contract side**
(the op the backend really recognizes, or the `BATCH_OP` exported from `api.js`).

**The common-tag editor is a row list, not a comma-separated text box**: one tag per row; editing a row = renaming that tag;
clearing a row (or clicking ×) = deleting it. The reference implementation gives a textarea — in that shape "delete the 3rd of 5"
shifts the two rows after it up one each, silently renaming tags the user never touched. The row list removes that ambiguity at the root.

### 4. Filtering: search, sort, visible-image frequency, selection set

- **Search box** (case-insensitive substring, the same rule as §4.10's autocomplete). It is implemented in `selectTagRows` as **search first, then truncate**:
  the reverse would make rare tags unreachable forever (the table has a `MAX_FREQUENCY_ROWS = 400` cap).
- **Sort**: by frequency / by name.
- **Frequency counts only the currently displayed images** (checkbox, off by default): when on, the frontend computes it live from the visible images' tags,
  to answer "which other tags co-occur with the batch of images I filtered out". It is a **display choice**; it does not change which images are visible.
  The cost is that it needs every image's tags, so like tag filtering it triggers caption probing.
- **Selection-set filtering** (checkbox): **intersect** (not replace) the gallery selection with the tag filter, recomputed automatically when the selection changes.
  For this, `gallery` gained `isSelected(path)` (O(1), avoiding one `getSelectedPaths().includes` per image).
- **"Clear all"** is separate from the existing "clear filter": the former clears the search text and the selection-set filter too.

## Explicitly not done

- **Staging / original-file backup / undo stack**: see point 2 above. Batch writes are still irreversible, so what is given is a preview + a confirmation dialog.
- **Move or Delete Files** (the reference repo's fifth tab): deleting/moving images belongs to P1+, and this repository's hard constraint is
  "do not modify image files in the dataset"; if it is ever done it must go through "back up + move into `_trash/`".
- **Cramming an interrogator into the batch panel**: tagging has its own tab and SSE stream; do not copy a second one.

## Criteria and falsification

| Criterion | Content |
|---|---|
| `test/test_api_e2e.py` three new cases | `edit_tags` single mapping / deletes do not shift / prepend; `regex_replace_text` over the whole caption; `dry_run` writes nothing, does not touch mtime, does not delete the cache, and reports `invalid_tag` at preview time for a comma |
| `test/test_captions.py::test_normalize_for_write_is_the_same_rule_as_the_write_path` | the write rule and the preview rule are the same function |
| `acceptance` **A35 / A35b** | the preview's `after` is byte-for-byte equal to the file after writing; whole-caption regex splits on bare commas; `prepend` lands at the beginning |
| `test/test_web_batch.py` | the backend `BATCH_OPS` and the frontend `BATCH_OP` **agree value by value** (drift one value = the user gets a 400 only after clicking write); write must be bound to a fresh preview; the common tags are a row list; CSS marks deleted rows |
| `test/test_web_filter.py` | wiring of the search box/sort/the two checkboxes; search before truncation; the search box is stretchable in CSS |
| `test/fixtures/batch_harness.mjs` | fake DOM + **fake fetch** (not stubbing api.js): preview carries `dry_run`, write does not, write is re-disabled after the preview goes stale, common-tag intersection and rename pairs |
| `test/fixtures/filter_harness.mjs` | three-state combination (or / and / exclude-first), search before truncation, selection-set intersection, frequency shrinking with the visible images, clear all |
| **Real uvicorn + real HTTP** (local 3099, urllib, a temporary dataset copy) | after `dry_run` the caption bytes are unchanged and the TE cache is still there; after writing `on_disk == preview_after` (`1girl, azure eyes, new tag`) and the cache is deleted. TestClient is in-process ASGI; this one is the evidence over a real socket |

Falsification (deliberately break → confirm red → restore; all three were measured, and after restoring the SHA256 was byte-for-byte identical to before):

| Break | Criterion that went red |
|---|---|
| `dry_run = bool(payload.dry_run)` changed to `dry_run = False` (the preview writes to disk as usual) | `test_api_e2e -k dry_run` **1 failed**; `acceptance -k A35` **1 failed** (the other one, A35b, is unaffected) |
| Remove `PREPEND: "prepend"` from the frontend `BATCH_OP` | `test_web_batch.py` **1 failed** (the two op sets disagree) |
| Remove the selection-set check from `filter.js`'s `apply()` | `test_web_filter.py` **1 failed** (the fixture asserts selection-only intersection) |

## Not verified (stated as it is)

**It has not been clicked in a browser.** Browser-level verification of the new panel is still gap 5: a fake-DOM fixture can prove event wiring, the request body, and
the state machine, but not the first-screen feel, the actual height of the row list in a narrow side panel, or how the confirmation dialog's wording reads on real data.
Also, `dry_run` goes through a **synchronous** request: a whole-corpus (1653 images) preview reads 1653 `.txt` files sequentially, the same order of magnitude as the
tag-frequency scan (about 1 s warm, longer cold), with no progress feedback in the panel. To change it, make it a job + SSE (the same pattern as gaps 2 and 9).

## Related

- Contract: [p0-spec](../p0-spec.md) §3.2 (`normalize_for_write`), §4 routing table, §4.5 ③, §5's A35/A35b
- Cache invalidation after a write: [encoder-cache-invalidation](../encoder-cache-invalidation.md)
- Dataset contract (split rule): [dataset-contract](../dataset-contract.md)
- Previous UI round: [ui-folder-grid-preview](ui-folder-grid-preview.md)
