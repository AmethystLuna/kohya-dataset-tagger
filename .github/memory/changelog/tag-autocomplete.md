---
name: tag-autocomplete
description: Tag autocomplete is one shared dropdown (web/js/suggest.js) that the caption editor and the batch panel's tag fields both use; the candidates are the scope's own tags first and the WD14 vocabulary as a fallback, and one Enter is claimed by exactly one handler.
metadata:
  type: topic
---

# Tag autocomplete: one dropdown, two hosts, the scope's tags first (2026-10-02)

**The report**: 「批量 tag 编辑这边怎么不支持自动补全」.

**The diagnosis**: it was not a decision and not a regression - the behaviour had never left the per-image
tag editor. `createTagEditor` (web/js/tags.js) built its own debounce, its own keyboard handling, its own row
painter and its own request against §4.10, all over closures that only an editor instance can see; a surface
that does not build an editor could not have it. The batch panel builds plain inputs
(`textInput()` in web/js/batch.js - whose `autocomplete="off"` only turns off the *browser's* autofill) and
never asked for anything else. Checked before changing: the frozen contract does not mention autocomplete for
the batch panel (§4.10 freezes the endpoint's matching semantics only), and `git status` was clean.

## The mechanism

`web/js/suggest.js` owns the dropdown: the debounce, the arrow keys / Tab / Enter / Escape, the active row,
click-to-accept, the `is-active` painting, and a **request-ordering guard** (each refresh takes a token; a reply
whose token is stale is dropped, so a slow query cannot repaint the dropdown for text that has since changed).
The host owns everything else: the input element, the dropdown element (positioned by the host's own
`.suggest-wrap`, which is `position: relative`), the candidate source, the query rule, and what an accepted tag
means. It returns `{ close }`.

| Host | Fields | Candidates | Accepting a tag |
|---|---|---|---|
| web/js/tags.js (caption dock + zoom) | the add-tag input | §4.10 vocabulary; a tag the image already has is excluded (unchanged behaviour) | appends a chip |
| web/js/batch.js | common-tag **add** field, **replace with**, and append/prepend's list | the scope's own tags first, then §4.10's vocabulary | fills the field (the list field completes its trailing fragment) |
| web/js/batch.js | **find**, and remove's list | the scope's own tags only | fills the field (the list field completes its trailing fragment) |

**The order of the two sources is the feature.** `/api/tags/autocomplete` is vocabulary-driven: with no tagger
model installed it legally answers `{"suggestions": []}` (api/tags.py), so it can never be the only source.
The batch panel's own half costs no request and is the answer the ops actually act on - find and remove name a
tag the images already carry. `rankSuggestions()` applies §4.10's ordering (prefix hits first, then
count descending, then the name) to that list, so one dropdown never mixes two orders; the vocabulary is
consulted only when the dataset half does not fill the page, and a failed request there is an addition lost,
not an empty dropdown. A tag both halves carry is offered once, and the badge tells the halves apart: a
dataset row shows how many images in the scope carry the tag, a vocabulary row shows `category · count` (the
vocabulary's own popularity - a different number with a different meaning, deliberately not merged).

**A field that names an existing tag does not offer the vocabulary** (`mode: "dataset"`, one round later, at
the requester's prompting). `find` and `remove` act on tags the images already carry, so a vocabulary tag the
scope does not carry is a candidate that cannot be acted on - replace would match zero images, and the row
would look like a working suggestion. `replace with` and the common-tag add field keep the vocabulary, because
both may name a tag no image has yet (and may want the vocabulary's own spelling). One field serves append,
prepend and remove, so its mode is read **per query** (`() => currentOp() === BATCH_OP.REMOVE ? "dataset" :
"both"`) rather than captured when the panel is built. The one exception: while the scope's captions are still
being read (`scopeTagMissing > 0`) the index is incomplete and even a dataset-only field keeps the vocabulary -
suppressing it would suggest nothing at all.

**Taking a suggestion is an edit like any other.** A programmatic `input.value` fires no `input` event, and the
batch panel's "a stale preview is dropped" wiring hangs off exactly that event (`addEventListener("input",
invalidatePreview)`, which the preview/write contract depends on), so the accept path calls
`invalidatePreview()` itself.

**One Enter, one handler.** Enter means "take the highlighted tag" in the dropdown and "add what I typed" in
both hosts. The dropdown listens for keydown, handles the key and marks the event with `preventDefault()`;
each host's own keydown listener returns early on `event.defaultPrevented`. Two handlers acting on one Enter
would add the accepted tag and then an empty one, which the user sees as the `caption.emptyTag` warning
("tag 不能为空").

## Judgement calls (and what was deliberately not done)

- **The common-tag rows keep plain inputs.** They live inside `.batch-common-list`, which is
  `max-height: 40vh; overflow: auto`; an absolutely positioned dropdown inside it is clipped by that scroll
  box. The **add** field directly below carries the autocomplete for the section instead.
- **The regex fields are not tag fields.** A dropdown over a regular expression would offer tags nobody asked
  for; `patternInput` / `replacementInput` stay `textInput()`.
- **The comma separated list asks about its trailing fragment** (`tagListQuery`) and completes only that
  fragment (`completeTagList`), so accepting `smile` in `1girl, sm` gives `1girl, smile` and never replaces
  the finished tags. A tag the list already carries is not added twice.
- **Tab still accepts the active row.** That is the pre-existing behaviour of this dropdown (and of the Tag
  autocomplete in the wider SD ecosystem). It is a deliberate exception to the "a dropdown is not a modal"
  invariant's Tab rule, which is about the command palette; changing it here would break the muscle memory the
  editor has had since the first release.
- **No copy was added**: the rows show a tag name and an optional badge, and nothing else. No locale key, no
  relabel registration.

## Criteria

- `test/test_web_contract.py::test_autocomplete_renders_the_category_name` now reads **suggest.js** (where the
  row is built) and asserts that both hosts import it. It also had to be **strengthened**: `"item.category" in
  text` is satisfied by the `item.category_id` fallback alone, so it pinned nothing until it asserted the
  pushed lines (`parts.push(String(item.category));`) - found by falsifying it.
- `test/test_web_batch.py` (5 new criteria): the four tag fields go through `tagField` while the two regex
  fields stay plain inputs and the common rows do not change shape; `suggestTags` asks the dataset half first,
  short-circuits the request when it fills the page, and survives a failed vocabulary call;
  `query: tagListQuery` and `export function tagListQuery(` exist; accepting calls `invalidatePreview()` while
  typed edits still go through the input-event wiring; `find` asks for `mode: "dataset"`, remove's list maps to
  it per op, and the incomplete-probe escape hatch is kept in both places the scope is read.
- `test/fixtures/batch_harness.mjs` sections 13-14 drive the real panel (fake DOM + fake fetch, real api.js,
  real suggest.js): the dataset tag leads with its image count, the vocabulary tag follows with its badge, a
  tag both halves carry appears once, Enter takes the active row and closes the dropdown **and drops the
  preview**, the comma field completes its trailing fragment on Tab, ArrowDown + a press takes the second row,
  a tag no image carries is still found through the vocabulary, a **held older reply does not repaint** the
  dropdown, an empty field asks for nothing; and then, over the **real find/replace/list fields**: a
  vocabulary-only query leaves `find` empty while `replace with` shows it, remove's list stays empty while
  append over the same field shows it, and a scope with an unprobed caption re-opens the vocabulary to `find`.
- `test/fixtures/tags_harness.mjs` section 6 covers the half only the editor can prove: typing opens the
  dropdown against a stubbed `api.tagAutocomplete`, and one Enter takes the suggestion **exactly once** with
  no notice (`notices.length === 0`), the input cleared and the dropdown closed. Its fake DOM's `fire()` gained
  a real `preventDefault()`/`defaultPrevented` because that is the handshake under test; `batch_harness.mjs`
  gained the same plus `closest()`/`focus()` and a small `/api/tags/autocomplete` stub (its own request log, so
  the write sections' call counts stay exact).

**Falsified 17 ways**, each red then green again (throw-away runner, real files patched and restored
byte-for-byte): the stale-reply guard removed; the accept's `preventDefault` removed; the host's
`defaultPrevented` check removed; `invalidatePreview()` removed from the accept path; the vocabulary asked
first / the early return dropped; `query: tagListQuery` removed; the cross-source dedupe removed; the category
name pushed as `item.cat`; the dataset count set to 7; `findField` renamed; `from "./suggest.js"` renamed in
tags.js; the CSS rule below removed; the dataset-only mode's early return removed; the per-query mode read
replaced by the raw option (the bug below); the "unfinished probe" condition dropped; `find`'s
`mode: "dataset"` removed; and one of the two `scopeTagMissing` assignments removed. Two of them (the category
criterion and the count) were **green under the first break attempt** and had to be re-broken properly - the
category assertion was vacuous as written, and the count break edited a branch the one-image scope never
reached.

**The harness caught a real bug while this section was written.** `mode` was passed to `suggestTags` as
whatever the field declared - for the append/prepend/remove list that is a *function*, so the comparison
`mode === "dataset"` was never true and remove's list went on offering the vocabulary. `tagField` now reads it
per query (`readMode()`), and section 14 covers remove and append over that one field, which is what failed.

## The one layout fact, measured in a real (headless) browser

The shared `.suggest-wrap` is `flex: 1 1 auto`, which the caption editor's input row wants. In the batch
panel's `.row` (the common-tag add field) that made the wrapper grow past the field it wraps - measured with
the committed probe (`tools/measure_web_layout.py`, imported as a module with extra selectors so the geometry
comes from the same path the repository trusts): the input was **160px** inside a **271px** wrapper, so an open
list would have dropped from a box 111px wider than the input, and the add button had been pushed to the row's
far end. The rule

    .batch-section .suggest-wrap { flex: 0 1 auto; min-width: 0; }

holds the two together (inside a `.field` the wrapper is a block and already fills the label, so the rule only
bites in the flex row); re-measured after it: wrapper **160px** = input **160px**, and the add button back at
6px after the field. `0 page_errors`. What is still **not** measured: an **open** dropdown (the probe reads the
closed state, and its canned data has no captions), and the geometry of the three `.field`-hosted fields (read
from CSS: a block wrapper plus `.field input[type="text"] { width: 100% }`).

**Evidence class**: read from the code, driven through fake DOM/fetch in Node, and one layout fact measured in
headless Chrome. **Not verified**: never clicked in a real browser by hand (the standing gap 5), and the
vocabulary fallback's "no tagger model installed" path is covered by the empty response, not by running
without a model.

---

Related: [ui-copy](../ui-copy.md) (no new string was needed), [p0-spec §4.10](../p0-spec.md),
[changelog/tag-editing-and-filtering](tag-editing-and-filtering.md) (where the editor's autocomplete came from),
[changelog/caption-dock](caption-dock.md) (the dock is the second surface of the same editor)
