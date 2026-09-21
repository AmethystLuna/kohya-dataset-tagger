---
name: ui-copy-audit
description: 2026-09-19, every user-facing string audited twice - once against the writing rules, once against how a person actually reads a sentence - and the "reading logic" half written down as rules with four new criteria
metadata:
  type: topic
---

# The copy audit: what the strings say and the order they say it in (2026-09-19)

The first pass ([ui-copy](../ui-copy.md)) settled the vocabulary and the marks. This pass asked a second
question of all **474 strings x 3 packs**: does the sentence survive being read by a person - is the
result in front of the reason, is the control named the way the screen names it, is there a next step,
does the tense tell the truth.

## How it was done

1. A mechanical sweep of the three packs (spacing, half/full-width punctuation, banned synonyms,
   placeholder parity, length ratios).
2. A read-through of all 474 Chinese strings, then **every finding checked against its call site** -
   `index.html` and `web/js/*.js` - before it was treated as real. Sentences were judged by where they
   render, not in isolation.
3. Two read-only subagent audits, English and Japanese, each told to read the packs and the rendering
   sites and to report only keys that are genuinely wrong.
4. Everything that survived was applied to all three packs in one pass, so key parity and placeholder
   parity never went out of sync (they are also the two things `test_web_contract.py` already guards).

## What was actually wrong (the classes)

| Class | Example | Fix |
|---|---|---|
| The text did not match reality | `autotag.streamDone` said 正在刷新字幕 (subtitles); `scale.progress` said 已完成 while still exporting; the cache stream, rebuild-start and cancel failures all reported 读取缓存状态失败; a literal English `no job_id` was spliced into Chinese toasts; `app.fatal` claimed the front end could not start on any `window.onerror` | each now names the event that really happened |
| The copy named controls that do not exist | 「运行」/「预览」 when the buttons say 写入 caption / 预览 diff（不写盘）; "the button above" where the button is below; 双击 chip; the model directory had **four** names (模型目录… / 模型扫描目录 / 模型搜索目录 / 模型搜索根目录); the filter cycle ended on 关闭 while the row announces 未过滤 | one name per control, taken from the visible label |
| A sentence assembled out of fragments | five sites did `t("...loadFailed") + ": " + detail` - an ASCII colon in every language, and no way to translate the sentence as a whole | a `{message}` placeholder in the pack |
| Dead ends | errors and empty states with no way out; the third filter state's word in the tooltip | state + the control that leads out |
| The dialog title said nothing | `createConfirm()` titled every question 确认操作 while its body asked a concrete question | the title falls back to the action label |
| One concept, several words | 枚/件, 未展开/未列出, 中止/キャンセル, "repeats" vs num_repeats, the English pseudo-plural, British and US spelling inside one pack | one word per concept, per language |
| Labels longer than a label | `autotag.opt.escapeParens` (110 chars, two examples) and `export.alphaMask` (68) carried whole sentences; the examples moved into their `*Hint` | the label carries the name |

## Deliberately not changed

- **The `msg.*` parameter names** (`op`, `tags`, `size`, `write_mode`): these surface when the
  frontend sends a bad request - a bug path - and the parameter name is what makes the report useful.
- **403 in `settings.lastRoot` / `msg.paths_unconfigured`**: the audience runs a local server, and the
  repository's own instruction is that "every request is 403" is the symptom to look for.
- **Punctuation families**: `scale.noSource` lost its full stop to match `scale.sourceNone`, and
  `batch.streamLost` gained one to match its two siblings; the empty states were already internally
  consistent and were left alone.

## The rules are now written down - and four of them can fail

[ui-copy](../ui-copy.md) gained a **Reading logic** section: result before reason, name the on-screen
control, the user's view rather than the system's, state + next step, nothing to remember, honest tense
and quantity, parallel structure, one screen order, no assembled fragments, a dialog titled by its action.

New or extended criteria, each falsified by breaking the rule and watching it go red:

| Criterion | Guards |
|---|---|
| `test_copy_is_never_assembled_out_of_translated_fragments` | no `t("x") + ":"` anywhere in `web/js` |
| `test_zh_copy_keeps_half_width_punctuation_out_of_chinese_text` | no half-width mark glued to a Chinese character |
| `test_progress_ratios_are_written_without_padding` | `{done}/{total}` is written tight, in every pack |
| `test_the_filter_cycle_hint_ends_on_the_state_word` | the tooltip and the row announce the same state word |
| `test_message_placeholders_are_always_filled_in` | a key carrying `{message}` is never called as `t("key")` |
| `test_en_copy_has_no_parenthesised_plural_placeholders` | English has no `image(s)` |
| `test_no_pack_quotes_our_own_spec_sections` | no `§1.1` in user-facing copy |
| `test_the_dialog_names_the_action_and_defaults_to_no` (extended) | the confirmation dialog is titled by its action |

Five keys that nothing referenced were dealt with instead of translated again:
`settings.modelRootRemoveOk` / `modelRootRemoveSessionOk` are now the confirm button of the destructive
dialog (they existed for exactly that), and `gallery.loadingThumbs`, `cache.rebuilding` and
`autotag.opt.ratingFirst` were deleted from all three packs.

## Two self-inflicted pitfalls (both caught by verifying, not by luck)

- A `String.raw` template still interpolates: a character class that listed the regex metacharacters,
  including the dollar-brace pair, ended the template early ("Expression expected"). Build the class from
  a plain string constant instead.
- **Generating a string literal is not the same as writing text.** The escaped-parenthesis example
  (`keqing \(genshin impact\)`) went in with **four** backslashes because the value was escaped twice - once
  by hand, once by `JSON.stringify`. Both escaped examples were re-checked by parsing the finished literal
  the way the browser does (`JSON.parse` of the quoted source) instead of eyeballing the file.

## Follow-up the same day: the reader is not a beginner

The first pass over-corrected. The audience for this tool trains LoRA / finetune / DreamBooth models with
kohya's scripts: they know what a caption, a subset, a bucket, a text encoder cache and a `.bak` are, and
they are standing in the panel the message appears in. Writing for someone who knows nothing produced
"读取缓存状态失败，点「刷新」重试" next to a Refresh button, and explanations of things the reader does
every day.

What changed in the second pass (62 keys x 3 packs, 186 values):

- **The obvious next step is gone.** An empty state or an error names a control only when that control is
  not already beside it: "点「刷新」重试", "用上面的按钮", "到「过滤」里点「清空过滤」" and "可以输入绝对路径或新建目录"
  were all removed.
- **The domain explanation is gone.** `cache.hint` no longer explains why a stale cache is dangerous
  (that is the reader's daily work), `caption.dropLinesConfirm` dropped "（训练器本来也不读）", `msg.empty_dir`
  dropped "（0 张图的 subset 会让训练集为空）".
- **The manual voice is gone.** `filter.semantics`, `batch.commonHint`, `settings.modelRootsHint`,
  `scale.summary` and the four confirmation bodies were cut down to the facts.

The zh pack went from 8761 to 8053 characters over the same 477 keys.

One trim was wrong, and a criterion caught it: shortening 双击（或 Z）放大预览 to 双击（或 Z）放大 dropped the
feature's own name (@@zoom.title@@ = 放大预览) from the only place the keyboard route is written down.
@@test/test_web_zoom.py@@ pins that phrase, so it went back in all three packs - the legend now names the
panel the way the panel names itself.

The rules were corrected, not just the strings ([ui-copy](../ui-copy.md)): a **"Who reads it"** paragraph at
the top states the audience, the empty-state template now reads "the next step **only when that control is
not on screen next to it**", and reading-logic rule 4 says the reason inside a message is a fact about this
run, never about the domain.

One criterion was added and one loosened. Added: `test_no_string_grows_into_a_manual` - a per-language
ceiling (zh 160 / en 250 / ja 200), a runaway guard rather than a target, falsified by padding one string to
170 characters. Loosened: `test_backup_write_modes_get_backup_wording_never_the_old_denial` pinned the
plain over-cap confirmation to the phrase 不会自动备份; it now pins the fact (自动备份), so the shorter
无自动备份 passes while the contradiction is still caught.

## Then: guidance beats instruction

The trim pass went one step too far in exactly one place, and the fix is a principle rather than more
words. `gallery.emptyFiltered` had lost "到「过滤」里点「清空过滤」" - and the Filter tab is **not on
screen** in the middle column, so that empty state was a real dead end. Every other empty state names a
control that sits beside it, or says nothing more; this one pointed at another tab.

The answer is not a longer sentence but a control. `#gallery-empty` gained a sibling
`#btn-gallery-clear-filter` (`data-copy="filter.clearAll"`, revealed only in the filtered branch),
wired to `filterPanel.clearAll()` - the same action as the Filter tab's 清空全部, offered where the dead
end is discovered. The copy stayed short: 没有符合过滤条件的图片 plus the button. The action is the
clear-**every**-filter one on purpose: a tag-only clear leaves the search box and "selection only" in
place and the grid stays empty.

Guarded by `test_the_filtered_empty_state_offers_the_way_out` - the button exists, it carries the
clear-every-filter action, it is wired, and it appears in exactly one of `paintEmptyState()`'s three
branches - falsified three ways: removing the reveal, removing the wiring and swapping the action all go
red. [ui-copy](../ui-copy.md) now states the rule twice: reading-logic rule 4 is "guide, do not instruct",
and the empty-state template says the way out is preferably the control itself.

---

Related: [ui-copy](../ui-copy.md), [i18n](../i18n.md)
