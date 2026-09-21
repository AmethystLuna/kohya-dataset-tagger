---
name: caption-restore
description: 2026-09-19, a caption write can be taken back - core restore_caption puts the .bak back byte for byte and consumes it, POST /captions/restore exposes it, and the batch panel drops its confirmation when the backup is on because the toast now carries the undo
metadata:
  type: topic
---

# Undoing a write (2026-09-19)

## The gap

[changelog/batch-backup-parity](batch-backup-parity.md) gave the batch rewrite the tagger's safety net: every
overwritten caption keeps its original bytes in `<caption>.bak`. But nothing in the tool could **put them
back**. The net only paid off if the user went into Explorer and copied files by hand - which means the
panel still had to ask "are you sure?" before every write, and the answer the user needed ("what did I just
lose?") arrived too late.

That is the whole trade in one sentence: **every confirmation dialog is a symptom of missing
reversibility.** The backup existed; the undo did not.

## The change

**`core/captions.restore_caption(image)`** - the inverse of `backup_file`, with three rules:

1. **Byte-level in both directions.** The backup holds the bytes from before this tool touched the file, so
   the restore writes *those bytes* back - CRLF, BOM and a missing trailing newline included - instead of
   re-encoding anything.
2. **Invariant number one still applies.** When the content really changes, the text-encoder cache is
   invalidated in the same call. An undo that left a stale cache behind would train on the tags the user
   just took back.
3. **The backup is consumed.** It is deleted once it has been put back, because rule 1 of `backup_file`
   ("an existing backup is never overwritten") would otherwise **silently disarm the net** for every later
   write to that caption. A backup that cannot be deleted (a locked file) is reported as
   `backup_removed: false` - the caption is restored either way, and the caller is not told a lie.

**`POST /captions/restore {images}`** is the list form: per-entry results, `no_backup` as an entry rather
than a request failure (that is what undoing a write that changed nothing looks like), and out-of-bounds
rejects the whole request with 403 exactly like `/captions/batch` and `/captions/read` - the resolve step is
now one shared helper so the three cannot drift.

**The panel stops asking.** With the backup on, `runApply` no longer opens a dialog: the write is
reversible, and the panel already made the user look at the diff before the apply button went live. The
toast that reports the write now carries **撤销** (`app.undo`), which calls the restore with exactly the
images the job reported as written - including a cancelled job, where that is what the user most likely
wants. Turning the backup **off** makes the write irreversible again, and then the confirmation is back.
`batch.cancelled` lost its "the writes that landed cannot be undone" clause: with a button next to it that
says otherwise, that sentence had become false.

A toast that carries an action no longer retires on its own (`sticky`): an escape hatch that disappears
while the user is reading it is not an escape hatch.

## What the criteria caught

The harness (`test/fixtures/batch_harness.mjs`, sections 9 and 10) drives the real panel: with the backup
on it asserts **zero dialogs** across two writes, that the toast offers an action labelled `app.undo`, and
that clicking it POSTs `/api/captions/restore` with **only the images the job actually wrote** - and then
that turning the backup off brings the dialog back.

That last part found a real bug: the undo list was written as `appliedImages.slice()` **inside the click
handler**, so the next run cleared the live array before the user could click. It is snapshotted when the
toast is built now.

**Two criteria were insensitive and were strengthened after falsification**, both for the same reason: the
restored bytes equal the bytes in the backup, so "the net was re-armed" and "the stale .bak is still
sitting there" are indistinguishable if the test asserts on the restored content. They now edit the caption
in between, so only a re-armed net produces the expected backup.

## Criteria

- `test/test_backup_overwrite.py` section 5 (5 criteria, real files): the exact bytes come back and the
  backup is consumed; the cache is invalidated; nothing to restore is not an error; the net is re-armed for
  the current content; a backup that cannot be deleted is reported.
- `test_web_batch.py`: the checkbox decides whether there is a question at all (this **replaces** the
  earlier criterion "the checkbox decides what the confirmation says", which was right before the undo
  existed), the undo targets the written set, the dead `batch.confirmBackup` key is gone from all three
  packs, and the route is declared *and* called.
- `acceptance/` **A42**: contract -> write -> restore -> cache -> `no_backup` -> 403 -> re-armed net.
- `p0-spec.md` §4.5 (5) (a new item in the supplement) and the route table.

**Falsified six ways**: the write-back disabled, the consumption removed, the cache invalidation removed
(each turning the expected criteria red, including A42), the undo list read at click time, the confirmation
made unconditional, and the toast action removed - the last three each red in **both** layers (static and
the harness).

---

Related: [p0-spec](../p0-spec.md) §4.5 (4)/(5), [changelog/batch-backup-parity](batch-backup-parity.md),
[changelog/failure-states](failure-states.md), [ui-copy](../ui-copy.md)
