---
name: design-principles
description: The design doctrine - the principle family this tool is built on, the invariants those principles turned into, and the loop (mechanism -> criterion -> falsification -> changelog) that keeps a later change honest.
metadata:
  type: topic
---

# Design doctrine (constraints, paradigm, principles)

**What it is**: the design layer of this project - how the product is meant to behave, and how a change to
that behaviour is proved. **Read it before** adding a surface, an action, a number on screen, or a rule.

**What it is not** (link these, do not restate them):

| Layer | File | The question it answers |
|---|---|---|
| Technical hard constraints | `AGENTS.md` | what must never be true of the code |
| The words the user reads | [ui-copy](ui-copy.md) | how a sentence is built |
| Where copy lives, how relabel works | [i18n](i18n.md) | the mechanism behind a string |
| How an agent reports | [agent-reporting](agent-reporting.md) | conclusion first, evidence levels |
| How maintainer docs are written | [documentation-style](documentation-style.md) | indexes route, topics carry the body |
| The frozen HTTP contract | [p0-spec](p0-spec.md) | what the endpoints promise |
| Why a value is what it is | `changelog/` via [INDEX-history](INDEX-history.md) | provenance |

## The paradigm in one paragraph

The reader trains LoRA / finetune / DreamBooth models and already knows what a caption, a subset, a bucket,
a text-encoder cache and a sidecar `.txt` are. They are in the middle of preparing a dataset, not learning a
tool. So the product's whole value is that **at the moment of action the screen is true**: what it says is
what is; what it does is reversible or prevented; long work is visible and stoppable; nothing expensive
happens without a way back. Every screen has to answer three questions without being asked - *what am I
looking at*, *what will this act on*, *what happens if I press it*.

Two consequences run through everything below. **The shortest true form wins**: an expert pays for every
word and every repeated number. And **the mechanism beats the message**: if something needs a paragraph of
explanation, the layout is usually wrong.

## The loop: how a principle becomes part of the product

    principle -> mechanism -> criterion -> falsification -> changelog -> the topic file that routes to it

- A **mechanism** is the smallest structural thing that makes the principle true without being remembered.
  `selected is a subset of visible` is a mechanism; "be careful what you select" is not.
- A **criterion** is a test that can fail. If you cannot make it fail, you have not written evidence. This
  repository has caught several criteria that were green for the wrong reason - a paint-order accident, a
  token that also appears in another module, an assertion that held while the button was already disabled -
  every one of them by deliberately breaking the code.
- A **falsification** is the proof: break it, watch the criterion go red, restore, watch it go green. Report
  the observation, not the intention.
- A **changelog** carries why, the evidence, the falsifications and the known limits; the **topic file**
  carries the rule that a later change has to respect.

A change that cannot name its mechanism is a copy edit. Say so, and keep it in the packs.

## The principle family

Thirteen rules, agreed on 2026-09-20, each with what it actually produced here.

| Principle | What it means in this product | What it produced |
|---|---|---|
| **Guidance over explanation** (引导 > 说明) | Where the way out is a control, carry the control; a sentence pointing at another screen is the fallback | the filtered empty state carries 清空全部 itself; every readiness segment *is* the action that fixes it; reading-logic rule 4 in [ui-copy](ui-copy.md) |
| **Recognition over recall** (识别 > 回忆) | The state that decides an action sits on screen next to it | the conditions narrowing the grid are removable chips above it (`#gallery-filter`); the processing scope is one strip visible on every tab (`#scope-strip`) |
| **Prevention over repair** (预防 > 补救) | Make the dangerous thing impossible or opt-in instead of warning about it | `dataset.toml` is never silently overwritten (skip + backup, `overwrite_dataset_toml`); the caption write invalidates the cache inside the same call |
| **Undo over confirm** (撤销 > 确认) | Ask only when the action cannot be taken back; otherwise keep the way back | a batch write keeps a `.bak` and the panel offers one-click restore; the backup checkbox is what decides whether a dialog exists at all |
| **State visible** (状态可见) | Every long thing happening is on screen, on every tab, and says what it is doing | the job shelf and the job store (a job survives a reload); the readiness strip; a stale diff says so; "could not ask" is never painted as "none" |
| **User control and freedom** (用户控制与自由) | Anything long-running can be stopped, and stopping cannot hang | every job has a cancel with a watchdog; the shelf's cancel is idempotent |
| **Consistency over cleverness** (一致 > 聪明) | One value, one painter, one control; the convenient variant loses | one scope for the whole column; one live editor per surface; one painter owns the chips; a job ends in exactly one function |
| **Direct manipulation over dialogs** (直接操作 > 对话框) | If a control can do it in place, there is no modal | the caption editor is a pinned dock, not a tab you visit; the command palette reaches verbs without walking the tabs |
| **Progressive disclosure** (渐进披露) | Show what is needed now, keep the rest one step away | the dock folds and remembers; a count that is not known yet renders as `…`, never as 0 |
| **The user's language** (用户语言 > 系统语言) | The UI speaks the trainer's vocabulary; an internal name appears only where it is actionable | the one-word-per-concept table in [ui-copy](ui-copy.md); no spec section numbers on screen |
| **Tolerance over strictness** (宽容 > 严格) | Accept what the user means; do not punish a near miss | substring matching in autocomplete and filter search; a corrupt stored job entry reads as "none" instead of throwing; a tile whose caption probe is still in flight stays visible |
| **Minimalism / signal-to-noise** (极简 / 信噪比) | The shortest true form, and never the same fact twice | the census line lost its `{missing}` count the moment the readiness strip printed it; a hidden element also drops its claim (the emptied status line) |
| **Help should not be needed** (帮助最好不存在) | A control that needs a paragraph is the wrong control | empty states carry the action; a hint states a condition, not a tutorial |

## The invariants (a principle that became structural)

These have teeth. Each is enforced somewhere - do not break one without changing its criterion in the same
commit.

| Invariant | Statement | Enforced by |
|---|---|---|
| Selection is a subset of the visible | `selected ⊆ visible = filter result`; entering missing-caption mode is the mutual exclusion, not a second layer | [test_web_filter.py](../../test/test_web_filter.py), [changelog/missing-caption-mode](changelog/missing-caption-mode.md) |
| One live copy | A value, a control or an editor exists once; two live copies of one caption overwrite each other | `test_web_caption_editor.py` (instance count), `test_web_scope.py` (one construction site), `test_web_filter.py` (one painter) |
| The control lives where its effect is visible | A control that acts on the grid sits next to the grid, and a host that is re-rendered needs a **sibling**, never a child | `test_web_filter.py`, `test_web_scope.py`, [changelog/caption-dock](changelog/caption-dock.md), [changelog/topbar-layout](changelog/topbar-layout.md) (2026-09-20: the browse scope left the page header for the grid's own toolbar; the fact that killed the left-column option is `api/fs.py:5-12` - a subdirectory's count is never recursive) |
| One number, one source | A fact shown in two places is painted from one value, and "could not ask" is not "none" | the readiness/cache agreement criteria in `test_web_readiness.py` |
| Unavailable is shown, with its reason | Never silently absent, never runnable anyway | the palette's unavailable rows (`test_web_palette.py`), disabled controls that say why |
| No privileged paths | A new surface calls the function the existing control calls; it owns no action | the palette's command table, `test_web_palette.py` |
| Copy lives in the packs | The engine and the panels hold no user-visible string, and render-time copy is registered for relabel | `test_web_contract.py`, `test_web_i18n.py`, [i18n](i18n.md) |
| A job ends in one place | Whatever ends it - done, lost stream, cancel, watchdog - closes the shelf row *and* the bookmark there | `test_web_jobs.py`, [changelog/job-shelf](changelog/job-shelf.md), [changelog/job-resume](changelog/job-resume.md) |
| The allowlist is the only door | Every endpoint that takes a path goes through `core/paths.py` | `AGENTS.md`, `test_paths.py`, `test_fs_api.py` |
| A caption write invalidates the cache | Otherwise training silently uses the old tags | invariant one in `AGENTS.md`, `test_backup_overwrite.py`, [encoder-cache-invalidation](encoder-cache-invalidation.md) |
| A stored UI preference is applied before the first paint | A choice read from storage (the theme, and the same is true of a locale or a layout) has to be on the document before the browser paints, and a deferred module is too late: over this page's real 27-module graph a `type="module"` evaluated at **275 ms cold / 50 ms warm** while first paint happened at **116 ms / 36 ms**, so a user who chose dark got a ~160 ms light flash on every load. The pre-paint half is therefore a **classic `<head>` script** (87 ms / 13 ms, always before the paint), and it cannot import the module that owns the rest - importing is what would make it a module. The storage key therefore exists twice **on purpose**, and a criterion compares the two copies rather than trusting them | `test/test_web_theme.py` (the key agreement, the only-one-non-module-script-in-`<head>` criteria), `test/test_web_contract.py` (the narrow exception with its reason), `test/fixtures/theme_harness.mjs`, [changelog/light-mode](changelog/light-mode.md) |
| A dropdown is not a modal | A surface that drops down from a control does not take the keyboard: Tab **leaves** it (and closes it), a click outside closes it, Escape closes it and hands focus back to the control it came from, and the page behind stays reachable. Only a dialog that covers the page may trap Tab or claim `aria-modal`, and only while it is the only such surface on screen | `test_web_palette.py` (the Tab/role/anchor criteria, and the flipped "not inside `.topbar`" one), `test/fixtures/palette_harness.mjs`, [changelog/command-bar-and-census-row](changelog/command-bar-and-census-row.md) |

## The process paradigm (how a change happens here)

1. **One item at a time, approved before it starts.** A batch of "while we are here" edits is how a diff
   grows a second subject and a reviewer loses the thread.
2. **Dispatch and acceptance.** An implementer's report is a *claim*. The accepting side re-runs the gates,
   re-does at least one falsification by hand, reads the diff, and checks the copy in all three packs.
   Several rounds here were dispatched on a premise of the orchestrator's that the code contradicted - so
   every brief asks for a premise check first, and "you are wrong, here is why" is a good answer.
3. **Name the evidence class.** *Measured in a real browser*, *read from CSS and a fake DOM*, and
   *remembered* are three different things; a report that blurs them is worse than one that admits a gap.
   [tools/measure_web_layout.py](../../tools/measure_web_layout.py) exists so the first class is reproducible
   instead of re-improvised each round.
4. **Two test tiers.** `tools/check_relevant.py` runs only what names the change (seconds); `--full` runs the
   whole gate once before reporting. See [changelog/test-loop-tiers](changelog/test-loop-tiers.md).
5. **Docs move with the change.** A changelog and its `INDEX-history` row are created together (an orphan
   file turns the gate red), the matching topic file is updated, and `current-state.md` gets the honest row -
   including what was *not* verified.
6. **Commits are minimal and single-subject**, and the message carries the *why*: a reviewer should be able
   to reject the change from the message alone.
7. **A delegated brief carries** what to read first, the premise check, the invariants above, the two
   recorded tool traps (never rebuild a file from `read` output - it truncates long lines and once silently
   deleted five tests; a restore anchor must be unique in the file - two rounds lost time to anchors that
   matched several times), the evidence classes to report, and "do not commit".

## Checklist before calling a change done

1. The mechanism is named, and it is structural - not a sentence.
2. Every new criterion was falsified: break -> red -> restore -> green, with the observation written down.
3. `tools/check_relevant.py --full` is green (unit + docs, and `acceptance/` when the frozen contract moved).
4. The three paths are covered: normal, failure, recovery.
5. Copy: three packs, same keys and `{placeholders}`, render-time strings registered for relabel.
6. The numbers on screen were checked against their source - none printed twice, none printed as 0 when it
   is unknown.
7. Changelog + index row + topic file + `current-state.md` updated in the same commit.
8. The known limits and the unverified parts are written down, not implied away.

---

Related: [scope-and-decisions](scope-and-decisions.md), [ui-copy](ui-copy.md), [i18n](i18n.md),
[agent-reporting](agent-reporting.md), [documentation-style](documentation-style.md)
