---
name: scale-toml-safety
description: 2026-09-19, the export no longer replaces an existing dataset.toml on its own: the file is left alone unless overwrite_dataset_toml says otherwise, and that path backs the previous file up first - the same rule, and the same primitive, the caption writes already use
metadata:
  type: topic
---

# The export stops throwing away a dataset.toml (2026-09-19)

## The gap

`api/scale.py` exports images and, when asked, generates a `dataset.toml` at the destination. That last
step was one line:

    target = job.target_dir / DATASET_TOML_NAME
    _atomic_write_text(target, plan.toml_text)

No existence check, no backup, and no question asked in the UI (`web/js/scale.js` had zero confirmation
sites - it still has none, deliberately). The same job, ten lines earlier, refuses to overwrite an *image*
(`scaler.unique_destination`): one run, two opposite rules about the destination directory.

`dataset.toml` is not a scratch file. It is the file that says which directories are subsets, what the
resolution is and how the batches are built - the one file in this workflow a user is most likely to have
hand-tuned. And the destination may well be an existing dataset directory.

## The change

**The same rule as the images, and the same primitive as the captions.**

- `core/captions.backup_file(target)` is `backup_caption`'s generic half, extracted so that "byte-level,
  and never replace an existing backup" has one implementation instead of two: the caption write paths call
  it, and so does the export. `backup_caption` keeps its third rule (no backup when the content does not
  change), which only a caption write can answer. The extraction is behaviour-preserving, guarded by the
  five `test_backup_overwrite.py` criteria plus A38/A40.
- `overwrite_dataset_toml` (default **false**) is the explicit way through:
  - file already there, flag off -> **nothing is written**, `summary.dataset_toml = null`,
    `dataset_toml_skipped = true`, and the reason (with the path) lands in `dataset_toml_warnings`;
  - flag on -> the previous file is copied to `dataset.toml.bak` **before** the write, and
    `summary.dataset_toml_backup` says where it went.
- `dataset_toml_skipped` is a separate field rather than a second warning string because the panel has to
  paint three different statements - written, left alone, failed - and two of them used to share the line
  `dataset.toml 未生成：{reason}`.

Frontend (`scale.js`): a second checkbox, "replace an existing dataset.toml", disabled and hidden while
generation is off (there is nothing to replace), and the result line follows the summary. **The panel grew
no confirmation dialog** - that is the point of the "undo > confirm" trade: the destructive operation
became a non-destructive one, so there is nothing to ask about.

## Criteria

- `test/test_scale_api.py` (real endpoint, real files): an existing `dataset.toml` keeps its bytes **and
  its mtime**, the summary says skipped, no `.bak` appears (nothing was replaced); with the flag on, the new
  file lands and the original bytes are in `dataset.toml.bak`; a second replacement does not touch that
  backup.
- `test/fixtures/scale_harness.mjs` section 8 drives the real panel: a `dataset_toml_skipped` summary
  paints the "left alone" line, and a summary carrying `dataset_toml_backup` names where the previous file
  went. The frozen request-key list grew the new field and asserts it defaults to false.
- `test/test_web_scale.py`: the panel default, the disabled-until-generation rule, the request field, and
  the backend's existence check and backup call.
- `acceptance/` **A41** scores the whole chain independently (skip -> replace -> replace again).
- `p0-spec.md` §4.14: the request body, the summary shape, the rule paragraph and the frontend note.

**Falsified five ways**, each turning exactly the expected criteria red: the existence check disabled
(3 red), the backup call removed (3 red), the never-replace rule removed from `backup_file` (5 red - the
two caption criteria catch it too, which is what a shared primitive should look like), the panel default
flipped to true, and the "left alone" branch deleted from the painter (the last two are caught statically
*and* by the harness).

---

Related: [p0-spec](../p0-spec.md) §4.14, [changelog/batch-backup-parity](batch-backup-parity.md),
[changelog/long-job-visibility](long-job-visibility.md), [ui-copy](../ui-copy.md)
