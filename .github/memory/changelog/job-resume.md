---
name: job-resume
description: 2026-09-19, a running job survives a page reload (web/js/jobstore.js): the panels bookmark their job id in sessionStorage and pick the job back up at boot, because every job stream replays its complete event log - and the reload revealed that the tagger panel could not be constructed at all
metadata:
  type: topic
---

# A running job survives a page reload (2026-09-19)

## The gap

The shelf ([changelog/job-shelf](job-shelf.md)) made a running job visible from every tab. A **reload** still
wiped it - four panels, four in-memory job ids - while the backend kept writing captions. Refreshing the page
was the same thing as losing the job, and the only place that still knew about it was the server.

Nothing about the job needs reconstructing, though: **every job stream replays its complete event log from
the start** (the same property that lets an EventSource connect after the POST that started the job). The id
was the only thing missing.

## The change

**`web/js/jobstore.js`** bookmarks the id. sessionStorage rather than localStorage - the entry describes what
*this tab* is watching, and a fresh tab must not inherit somebody else's job. One entry per panel (each panel
is single-flight, so a new job replaces that panel's old entry). Every operation tolerates a storage that is
absent (a headless fixture), hostile (private mode) or full: remembering is a convenience, and a job must not
fail because its bookmark could not be written.

Each panel remembers at start and forgets in its **single job-end function** - the same choke point the shelf
row is closed in, so a bookmark cannot outlive the job it describes. Each panel gained a `resume(jobId, total,
task)`, and `boot()` hands the bookmarks to the panels that own them. A job that finished while the page was
reloading needs no special case: the stream replays its terminal event immediately, and the panel finishes
normally.

## What the reload exposed

While wiring this, the tagger panel turned out to be **unconstructible**: `autotag.js` called
`createScopeControl` without importing it (introduced in [changelog/shared-scope-control](shared-scope-control.md),
the commit before). The module still *parsed*, so every gate stayed green - `node`-level validation, the diff
harness, and the whole suite - while the app threw a `ReferenceError` the moment it built the panel, i.e. it
could not start at all.

Two criteria now close that hole:

1. **`test/fixtures/panel_locale_harness.mjs` constructs the tagger panel** (stub elements, the same fake DOM
   the batch and scale panels already used there) and asserts its shared scope control carries the resolved
   locale. Removing the import again fails it with `ReferenceError: createScopeControl is not defined` -
   verified. This is the criterion that would have caught it.
2. **`test_every_module_imports_the_helpers_it_calls`**: for every module in `web/js/`, a call to a name
   another module exports must appear in an import statement of that file. It found its own two flaws while
   being written - multi-line imports (six false offenders in app.js) and local helpers that share a name with
   an export (`tags.js` has its own `normalizeTag`, `strings.js` exports one) - and both are handled.

## Criteria

- `test/fixtures/jobstore_harness.mjs`: no storage, two panels (two entries), a panel's new job replacing its
  old entry, forgetting with and without an id, a corrupt value, a non-array value, entries missing a field,
  and a storage that throws on every operation.
- `test_web_jobs.py`: the store's exports, every panel bookmarking/forgetting/resuming, `resumeRunningJobs()`
  being called while booting and handing each entry to its own panel, the jobstore harness, the import rule,
  and the tagger-panel construction (in the locale harness, run by `test_web_contract.py`).
- No new copy: a resumed job shows the row and the numbers it would have shown anyway. There is nothing to
  explain - the panel is simply busy again.

**Falsified**: removing the tagger's scope import reddens the construction criterion with the exact
`ReferenceError` that shipped; removing a jobstore import reddens the import rule; the harness itself was
verified against the no-storage path the headless fixtures actually run in.

---

Related: [changelog/job-shelf](job-shelf.md), [changelog/shared-scope-control](shared-scope-control.md),
[changelog/batch-jobs](batch-jobs.md), [p0-spec](../p0-spec.md)
