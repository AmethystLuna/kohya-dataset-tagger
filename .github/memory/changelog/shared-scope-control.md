---
name: shared-scope-control
description: 2026-09-19, the two panels that write captions now answer "which images?" with one shared control (web/js/scope.js): four scopes with live counts, the same providers from app.js, the batch panel's default kept, and its scope finally visible on the panel that obeys it
metadata:
  type: topic
---

# One question, one control (2026-09-19)

## The gap

Two panels write captions - the tagger and the batch editor - and both have to answer the same question
first: **which images?** They answered it differently:

- the tagger offered a choice of four scopes with live counts (current selection / the directory / that
  directory recursively / the filter result), and its own copy of that logic;
- the batch panel acted on `getVisiblePaths()` and printed a bare count somewhere below its fields
  (`batch.scope`, "作用范围：N 张"). Its copy even said so - `batch.hint` = "only applies to the images
  currently shown in the gallery" - which was a rule with no control on the panel that obeyed it.

So the way to know what a batch write would touch was to remember what the **filter tab** had been left
doing. That is the recognition-over-recall failure in its purest form: the answer existed, on a different
screen, as a consequence rather than a choice.

## The change

**`web/js/scope.js`** owns the question now: the four scopes, the labels that carry the counts, and the
two rules the counts must keep -

1. a count is **live** (the gallery probes captions in chunks; the filter changes what is visible), and
2. the recursive scope shows **"…"** until its listing has loaded, never a fabricated 0, because 0 reads
   as "there is nothing below this directory".

The recursive listing is still cached per directory, so switching back and forth does not refetch. Panels
attach their own reactions through two hooks: `onPaint` (the tagger re-evaluates Run/Preview and the diff
warning there) and `onChange` (the batch panel drops its preview, re-reads the scope, and tells the app
to probe captions for the new set).

**Both panels read the same providers.** The four closures moved into one `scopeProviders` object in
app.js and are spread into both panels, because a second copy is exactly how the two answers drifted in the
first place. The criterion says so literally: `app.count("...scopeProviders,") == 2`.

**Nothing changed for the tagger**: same four scopes, same labels, same gating, same "…" rule - the code
moved, it did not change meaning. The batch panel **keeps its default** (what the filter left visible):
silently changing what gets written would be worse than the old hidden rule. What is new is that the
default is now visible on the panel, can be changed there, and shows its count.

Copy: four shared keys (`scope.label`, `scope.selected`, `scope.dir`, `scope.dirRecursive`,
`scope.filtered`) replace the four `autotag.scope*` ones; `batch.scope` and `batch.scopeEmpty` are gone
(the label carries the count), and `batch.hint` no longer claims a scope the panel does not own.

## Criteria

- `test_web_contract.py::test_scope_list_has_a_subdirectory_option` moved to the shared control. Its
  original lesson is kept verbatim in spirit: asserting that the literal `dir_recursive` appears is an
  empty criterion (it also appears in the label table and the option loop), so the criterion pins the line
  that really *returns* the recursive list, the cache check, and the "…" rule.
- `test_web_batch.py`: the panel uses the control, defaults to the filter result, has it on screen
  labelled, takes both request paths' images from it, and no request path reads the visible set directly.
- `test/fixtures/batch_harness.mjs` section 11 (behavioral, real panel): the chosen scope reaches
  `/captions/batch` (selected -> one image, recursive -> four, the recursive count actually arriving), a
  scope change drops the preview of the previous set, and the app is told (so captions get probed).
- The fixture gained an `options` getter on its fake node - the same one `filter_harness.mjs` already had,
  for the same reason (a standard DOM API the panel really uses).

**Falsified four ways**, each red in the layer that should catch it: `paths()` ignoring the selected scope
(the harness), the batch default flipped (static criterion + harness), the preview not invalidated on a scope
change (harness), and the recursive load disabled (harness - verified on its own, where it fails with the
honest `（… 张）` on screen).

Two smaller things the change forced, both worth knowing: the harness had to stop identifying the op select
**by position** (the panel has two selects now, and the position broke the day the control arrived), and the
first draft of a comment in `batch.js` quoted the old Chinese label - caught by
`test_no_cjk_literals_outside_the_locale_packs`, which is the rule that copy lives in exactly three files.

---

Related: [p0-spec](../p0-spec.md), [ui-copy](../ui-copy.md), [i18n](../i18n.md),
[changelog/tag-editing-and-filtering](tag-editing-and-filtering.md)
