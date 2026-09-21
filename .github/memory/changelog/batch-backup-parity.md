---
name: batch-backup-parity
description: 2026-09-19, the batch caption rewrite backs the existing caption up before overwriting it (backup, default true) - the same net the tagger's default write mode already threw, wired through the one shared step function
metadata:
  type: topic
---

# The batch rewrite gets the tagger's safety net (2026-09-19)

## The gap

`api/autotag.py` has defaulted to `backup_overwrite` since 2026-09-19 for one reason, written in that
file: **the default path must not be the only one that irreversibly throws away an existing caption.**
The batch panel (`POST /captions/batch` and its job form) rewrites the **same** `.txt` files - regex
replacements, dedupe, vocabulary sorting - and had no backup at all: its confirmation could only say
"cannot be undone". One tool, two entrances, one of them without a net.

That is the "undo > confirm" trade-off: every confirmation dialog is a symptom of missing reversibility,
and the cheap cure is not a better sentence but a restorable state.

## The change

`CaptionBatchRequest.backup` (default **true**) - the same default as the tagger, for the same reason.
`_batch_step`, the one function the preview, the sync endpoint and the job all share, backs the caption
up **before** it writes:

    if payload.backup:
        captions.backup_caption(image, new_text)

- `backup_caption` is the primitive that already existed (`core/captions.py`, used by the tagger): it
  is byte-level, it never overwrites an existing backup (the first backup is the state before the tool
  ever touched the file) and it produces nothing when the content does not change - so an op that changes
  nothing leaves no noise behind.
- Passing `new_text` is what keeps "is there anything to back up" and "does the content really change"
  one rule: both go through the write's own normalization.
- **Fail closed**: if the backup cannot be written (read-only directory, full disk), that image's write is
  aborted with `write_failed` / `backup failed: ...`. A promise of "you can restore it" must not be
  broken by the very run that needed it.
- The preview is untouched: `dry_run` still writes not a single byte, backup included.
- `.bak` stays invisible to everything else: it is neither an image extension nor a caption sidecar
  name, so directory scans, `dataset.toml` generation and cache invalidation never see it (§4.5 ④).

Frontend: a 写入前备份（.bak） checkbox in the batch panel, checked by default and carried in the request;
the confirmation question follows it (`batch.confirmBackup` when on, `batch.confirm` - the one that says
"cannot be undone" - when off). `batch.applyHint` dropped its "cannot be undone" clause: with the net on
that is no longer the whole truth, and the dialog is where the irreversible decision is made.

## Criteria

- `test/test_backup_overwrite.py` section 4 (5 criteria, deterministic, real files through
  `_batch_step`): the original bytes land in the `.bak`; a second run never replaces the first backup;
  an op that changes nothing leaves no backup; `backup:false` is the way out; a failing backup aborts the
  write.
- `acceptance/` **A40** scores the chain independently: the contract states the default and the failure
  rule, a request that never mentions the field keeps the original bytes, the preview leaves nothing, a
  second run does not replace the backup, `backup:false` is honoured.
- `test/fixtures/batch_harness.mjs` drives the real panel and asserts the write request carries
  `backup: true`; `test/test_web_batch.py` pins the checkbox default, the payload field and the
  confirmation switching.

Falsified three ways, all red, all restored green: the backend default flipped to false, the backup call
removed from `_batch_step`, and the checkbox default flipped to off.

---

Related: [p0-spec](../p0-spec.md) §4.5 ③, [ui-copy](../ui-copy.md), [changelog/batch-jobs](batch-jobs.md)
