---
name: scope-recursive-cache
description: 2026-09-20, the scope strip's "directory + subdirectories" listing stopped describing trees that are not on screen - the cache is only an answer for the directory it was loaded from (after navigating it used to hand the previous directory's images to every panel acting on the scope, the two that write included), and scope.invalidate(), called by app.js wherever a fresh listing is installed, drops it so a crop's archive beside its source reaches the count instead of staying invisible until the user navigates away and back
metadata:
  type: topic
---

# The recursive listing is an answer about one directory (2026-09-20)

## Two ways it described the wrong tree

`web/js/scope.js` answers "which images?" for every panel that acts on the grid, and one of its four
scopes - "directory + subdirectories" - needs a **second listing request** (`GET
/api/fs/list?recursive=true`). That listing was cached in `{dir, paths}`, and the cache was trusted
whenever `paths` was non-empty, **whatever directory it had come from**:

1. **The cache outlived its directory.** Navigating from `A` to `B` with that scope selected never
   re-listed `B` (the only listing request `openDir()` makes is the non-recursive one), so
   `scope.paths()` kept returning `A`'s images while the screen showed `B` - and every panel acting
   on the scope acted on the tree the user had just left, including the two that **write** (the batch
   caption write and the crop).
2. **The cache survived a re-listing of the same directory.** The crop archives its result *beside its
   source* (`<stem>_<n><ext>`, §4.17), so a run changes the tree under the open directory while the
   directory itself does not change at all. Nothing invalidated the cache - not `refresh()`, not even
   switching the scope away and back - so the count, and (once the export began taking the scope's
   list) the export itself, described the pre-crop tree until the user happened to navigate elsewhere
   and back.

Found by the review that followed the export-scope-list round: the same "acts on a different set than
the control says" family, one layer down - there the panel was wrong, here the control itself was.
Measured with a scratch probe against the real module before anything was changed:

    after first load: 2                        fetches: 1
    after refresh(): 2                         fetches: 1     <- the tree had grown to 3 in between
    after switching the scope away and back: 2 fetches: 1

## The change

- **The directory is part of the question.** `cachedRecursive()` returns the cached listing only when
  `recursive.dir === getDirKey()`. Otherwise it is **unknown**: `counts()` reports `null` (the strip
  renders "…", never a fabricated 0) and `paths()` returns nothing rather than another directory's
  images - so an action that writes can never run over the tree the user has just left.
- **`invalidate()`** drops the cache and, when that scope is the selected one, repaints ("…") and
  reloads immediately.
- **`app.js` calls it in `openDir()`**, right where a fresh listing is installed. The control cannot
  see the filesystem; "a fresh listing arrived" is the one signal it gets, and `openDir()` is the only
  place one is installed - navigation, the refresh action, the external-change poll and the crop
  panel's own post-run refresh all land there, so one call covers every writer.

## What is deliberately *not* changed

- **A plain repaint still does not refetch.** The cache exists because the strip is repainted on every
  selection change, every filter pass (once per caption chunk) and every directory change; re-listing
  on each of those would turn one request into a loop. Freshness comes from the one event that
  actually means "the tree may have changed": a new listing.
- **Nothing new polls.** The 15 s external-change poll already re-lists only when its fingerprint
  moved, and its reload goes through `openDir()`, so there is no second watcher and no request at
  rest.

## Evidence

- `test/fixtures/scope_strip_harness.mjs` section 6 - the real `scope.js`, `batch.js` and
  `autotag.js` over one control: a plain repaint costs no fetch; a dropped cache reads "…" and then
  the **new** number (42) with the panels' `scopePaths()` following it; another directory reads "…"
  and hands out **nothing** until its own listing arrives; navigating back reloads instead of reusing;
  the fetch counter is asserted at every step.
- `test/test_web_scope.py::test_a_listing_is_an_answer_about_one_directory_only`: the static half (the
  keyed cache, the `paths()` guard, `invalidate()` existing, and `openDir()` calling it).

## Falsification (what was deliberately broken, and what turned red)

1. **`cachedRecursive()` back to `return recursive.paths || null`** (the old rule): "the control
   reported the PREVIOUS directory's count: 42".
2. **`invalidate()` without dropping the cache**: "a dropped listing must read as unknown, not as the
   number from before: 当前目录及子目录（41 张）".
3. **`scope.invalidate()` removed from `openDir()`**: the static criterion red.
4. **The first version of the criterion was caught by its own falsification**: with the (a) block
   written *before* the (b) block, break 2 left the harness **green** - the stale listing read as
   unknown for the other reason. The order is now part of the criterion, and the comment in the
   harness says why.

Each break was restored and the affected criteria re-run green (harness OK; 12 passed).

## Known limits

- The cache holds **one** directory's listing, so A → B → A costs a reload each way. That is the
  behaviour the control already had (the slot was always single); only switching the *scope* away and
  back is free.
- A listing that was already in flight when the directory changed is stored against the directory it
  was requested for, so it can never be handed out for the new one - it is simply never used. A race
  inside one directory (the tree changing between request and response) is not detected: nothing in
  the protocol carries a listing generation, and the next fresh listing corrects it.
