---
name: ui-copy
description: User-facing UI copy rules - the one-word-per-concept table, per-language punctuation and spacing, the length budget for localizable strings, and templates for buttons, errors, empty states and status text
metadata:
  type: topic
---

# UI copy (the words the user reads)

**What it is**: the wording rules for text that ends up on screen. **Read it before adding or changing any string** in
`web/js/locales/*.js` (`src/kohya_dataset_tagger/web/js/locales/`).

**Who reads it**: someone who trains LoRA / finetune / DreamBooth models with kohya's scripts. They know
what a caption, a subset, a bucket, a text encoder cache, a sidecar `.txt` and a `.bak` file are, and they
are looking at the panel the message appears in. So: never explain the domain, never justify a rule they
already follow, and never name a control that is already visible next to the message. The shortest form
that is still true wins; the ones below that read like a manual are the exception, not the template.

**Boundaries.** The *mechanism* - where copy lives, key and placeholder parity, the relabel contract, backend message
localization - is [i18n](i18n.md). Text written for **maintainers** (AGENTS.md, memory files, docstrings) is
[documentation-style](documentation-style.md). Those two files derive their general writing rules from the same sources
as this one, so don't restate them here: documentation is read by whoever maintains the repo, this file is read by
whoever uses the app.
The design layer above the wording - what the product must *do*, not what it says - is
[design-principles](design-principles.md).

## Sources (cited, not measured here)

| Source | What was taken from it |
|---|---|
| [Ant Design 文案](https://ant.design/docs/spec/copywriting-cn) | Chinese wording, use of 你 not 您, no end punctuation on labels, spacing between CJK and Latin, wording pairs (其他/其它, 登录/登陆, 此/该) |
| [中文文案排版指北](https://github.com/sparanoid/chinese-copywriting-guidelines) | Spacing between CJK and Latin/digits, full-width vs half-width punctuation, no repeated punctuation |
| [GOV.UK writing guidelines](https://guidance.publishing.service.gov.uk/writing-to-gov-uk-standards/writing-guidelines/) · [A to Z style guide](https://guidance.publishing.service.gov.uk/writing-to-gov-uk-standards/style-guides/a-to-z-style-guide/) | Plain language, tone (specific, concise, not terse), active voice, usually no "please", no block capitals |
| [Microsoft style guide: Top 10 tips](https://learn.microsoft.com/en-us/style-guide/top-10-tips-style-voice) · [UI text checklist](https://learn.microsoft.com/en-us/previous-versions/aa454308(v=msdn.10)) · [localizing the UI](https://learn.microsoft.com/en-us/previous-versions/aa690572(v=vs.71)) | Sentence case, no end punctuation in titles, result before action, 30% expansion allowance, labels must wrap |
| [Google developer documentation style guide](https://developers.google.com/style/highlights) | Second person, active voice, conditions before instructions, UI element naming, placeholder formatting |
| [NN/g error-message guidelines](https://www.nngroup.com/articles/error-message-guidelines/) · [10 heuristics](https://www.nngroup.com/articles/ten-usability-heuristics/) · [3 I's of microcopy](https://www.nngroup.com/articles/3-is-of-microcopy/) | Errors visible/constructive/respectful of effort; speak the user's language; keep system status visible; decide whether a string informs, influences or supports interaction |
| [Mozilla Acorn: UX writing heuristics](https://acorn.firefox.com/latest/content/ux-writing-heuristics-B3ETT3hC) · [writing for global audiences](https://acorn.firefox.com/latest/content/inclusive-writing/writing-for-global-audiences-YOfzwma7) · [empty states](https://acorn.firefox.com/latest/content/patterns/writing-for-empty-states-NQLxhgJJ) (linked, not read while writing this) | Value first, every word earns its place, scannable, strings can double in length, humour and cultural references do not travel |

**Honesty note**: none of these external rules has been measured inside this repository - they are cited, not validated here.
Rules marked **guarded** below are the ones a test actually enforces; everything else is convention.

## One word per concept

A synonym makes people think two things are two different things.

| Concept | zh-CN | en | ja | Never |
|---|---|---|---|---|
| directory | 目录 | directory | ディレクトリ | 文件夹 / folder / フォルダ (**guarded**) |
| tag | 标签 | tag | タグ | - |
| caption | caption (kept as-is) | caption | キャプション | - |
| gallery | 画廊 | gallery | ギャラリー | - |

- **Guarded**: `BANNED_SYNONYMS` in `test/test_web_i18n.py`, checked by `test_one_word_per_concept`, which reads all
  three packs and fails on the banned word. Add a row there when a new concept gets a second name.
- **Code identifiers and kohya's own terms stay untranslated** - `caption`, `subset`, `sidecar`, `alpha_mask`,
  `max_bucket_reso`, `glob_images`, `paths.configure()` appear verbatim in the zh pack (verified by reading the packs).
  A translated identifier sends the user looking for a setting that does not exist.

## Copy and accessibility

- A label that is a single glyph ("＋" for adding a root directory) gives a screen reader nothing to announce: it needs a
  `data-copy-aria`. Guarded by `test_glyph_only_labels_carry_an_accessible_name` in `test/test_web_i18n.py`.
- An accessible name must contain the visible label (WCAG label-in-name), so never set an aria label that contradicts the
  button it sits on.
- Never put meaning in colour or position alone ("click the red button") - name the control instead.

## Punctuation and spacing

**zh-CN**

- Full-width punctuation: ：（）、and · as the count separator; the ellipsis is one `…` character (there is no `...` anywhere
  in any pack, verified by grep).
- A space between CJK and Latin/digits is required (Ant Design, 中文文案排版指北). When a placeholder sits against Chinese,
  the space belongs **in the string**: `"{count} 张图片没有 caption"`.
- Labels, tabs, tooltips and table cells take **no** end punctuation; sentences and multi-line hints take 。
- No repeated punctuation (！！, ？！) and no exclamation marks at all - the zh pack currently contains none (verified by grep).
- The zh pack uses **no second-person pronoun at all** (neither 你 nor 您 appears anywhere in it, verified by grep); the phrasing is imperative.
  If one is ever needed, Ant Design's rule is 你, never 您.

**en**

- Sentence case for labels, tabs and headings ("Root directories", not "Root Directories").
- No period on labels and tabs; hint text that is a full sentence does take one.
- Second person, active voice, conditions before the instruction ("To export, pick a directory first").

**ja**

- Full-width ：；、。 with no spaces around them; `…` for the ellipsis.
- Katakana for UI concepts (ディレクトリ, キャプション, タガー); Latin product names stay Latin.

## Length budget and localizable phrasing

- Assume **30% expansion** when a string is translated (Microsoft), and up to **2x** for some languages (Acorn). A button,
  tab or label must fit the longest of the three packs, not the one you happen to be editing.
- **Never build a sentence out of translated fragments.** One key carries the whole sentence with `{placeholders}`;
  word order differs between the three languages. Placeholder names are pinned across packs by
  `test/test_web_contract.py` (invariant 3 in [i18n](i18n.md)).
- **Runtime values travel as `params`, not as baked-in prose.** Counts, paths and model names already arrive as
  `msg.<code>` + `params` (see [i18n](i18n.md)); keep new messages on that route rather than interpolating a path into
  a sentence at render time.

## Templates

| Kind of string | Shape | Example / note |
|---|---|---|
| Button, menu item | verb + object, no bare "OK"/"Submit" | The label must match the title of what it opens |
| Error | what happened + why it matters + what to do | `msg.missing_caption` = count + cause + consequence ("the trainer will train on an empty caption") |
| Empty state | the state, and then the way out - **preferably the control itself** | Not just "No data", and not a manual. If the action is on another screen, put the action in the empty state (2026-09-19: the filtered gallery offers 清空全部 instead of naming the Filter tab; 2026-09-20 moved the conditions themselves into a strip above the grid - [changelog/gallery-filter-strip](changelog/gallery-filter-strip.md) - so the per-condition undo is on screen as well and the button is the one click that clears all of them). If the control is already beside the message, the state says nothing more (`cache.empty`). The seven dead-end states found on 2026-09-19 (`browse.noDirs`, `gallery.empty`, `batch.scopeEmpty`, `autotag.previewEmpty`, `filter.frequencyEmpty`, `export.empty`, `cache.empty`) name the next step; the ones whose control was already on screen got the sentence removed again the same day |
| Progress / status | what is running, how far, can it be cancelled | Long jobs here stream over SSE (autotag batch, scale export); see the relabel boundary in [i18n](i18n.md) |
| Destructive confirmation | name the consequence and whether it can be undone | - |
| Tooltip, help text | only when it is actually needed | Don't write instructional prose up front; add it when research shows it is missing |

An error must never blame the user, and an error code is never the whole body.

## Reading logic (how the sentence is put together)

The rules above are about words and marks; these are about the order the reader meets them in. They are
what the second pass checks. Only the ones marked **guarded** have a test - the rest are convention.

1. **Result first, reason second.** A status says what happened before why; an error says what broke
   before the mechanism; a hint gives the condition before the instruction.
2. **Name the control that is on the screen.** Never "the button above" (the button moves), never a word
   the UI does not use (「运行」 when the button says 写入 caption), never an internal name (`chip`,
   `sidecar`). A quoted name matches the visible label exactly.
3. **The user's view, not the system's.** 没有需要处理的 beats "no stale entries were found"; a caption is
   a caption, not 字幕; a raw `no job_id` is not something a user can act on. An internal mechanism
   (progress stream, backend event, HTTP 403) may be *named* where the user can act on it, never used as
   the explanation.
4. **Guide, do not instruct.** Where the way out is a control, carry the control: the filtered gallery
   empty state offers 清空全部 itself instead of naming the Filter tab (guarded by
   `test_the_filtered_empty_state_offers_the_way_out`), and since 2026-09-20 the conditions that
   emptied the grid are one removable chip each in the strip above it. A sentence pointing at another screen is the
   fallback, and when the control is already beside the message, say nothing at all - `autotag.modelNone`
   keeps 「模型目录…」, a message next to a Refresh button does not say 点「刷新」. The reason inside a
   message is a fact about **this run** ("{count} 张图片没有 caption"), never a fact about the domain the
   reader works in every day.
5. **Nothing to remember.** Do not write "it", "this operation" or "that file" when the object is not in
   the same sentence - write the path, the file name or the count again.
6. **Honest tense and quantity.** 正在 = running, 已 = finished (`scale.progress` said 已完成 while it was
   still exporting); a number says what it counts (已重建 3/10 个缓存, not 已处理 3/10 个).
7. **Parallel structure inside a group.** The buttons of one row, the clauses of one summary line and the
   four states of one cycle all use one shape; the fourth word of `filter.cycleHint` is the word the row
   announces (`filter.stateOff`) - **guarded** by `test_the_filter_cycle_hint_ends_on_the_state_word`.
8. **One screen order.** A hint that mentions several controls mentions them in the order they appear.
9. **A fragment is not a sentence.** Runtime values arrive as `{placeholders}`; `t("x") + ": " + detail`
   is banned - **guarded** by `test_copy_is_never_assembled_out_of_translated_fragments`.
10. **A dialog is titled by its action.** `createConfirm()` falls back to the action label, not to a
    generic "Confirm" - **guarded** by `test_the_dialog_names_the_action_and_defaults_to_no`.

---

## Checklist before merging a copy change

1. All three packs carry the same key and the same `{placeholders}` - `python -m pytest test/test_web_contract.py -q`.
2. Render-time copy is registered for relabel (contract in [i18n](i18n.md)) - otherwise it stays in the old language.
3. Terminology checked against the table above (`test_one_word_per_concept`).
4. Punctuation and spacing checked for the language you edited.
5. The longest of the three packs fits the button, tab or label.
6. Reading logic above: result first, the control named, a next step, nothing to remember, honest tense,
   parallel shapes.
7. A button or checkbox label carries the **name**, not the explanation - the example or the second clause
   belongs in the paired `*Hint` key (2026-09-19: `export.alphaMask` and `autotag.opt.escapeParens` were
   cut back to the name).
8. Nothing on screen cites our own spec (`(spec §1.1)`) or an internal field name; the English pack has no
   `image(s)`-style pseudo-plural - guarded by `test_no_pack_quotes_our_own_spec_sections` and
   `test_en_copy_has_no_parenthesised_plural_placeholders`.
9. `python -m pytest test/test_web_contract.py test/test_web_i18n.py test/test_web_confirm.py -q`.

---

Related: [i18n](i18n.md), [documentation-style](documentation-style.md)