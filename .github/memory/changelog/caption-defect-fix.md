---
name: caption-defect-fix
description: Removing the stray backslashes from 6 captions in one subset directory (an approved dataset change)
metadata:
  type: topic
---

# 2026-09-17 Removing the stray backslashes from 6 captions

## What it is

In the reference dataset, under the
`1_sample_alpha` directory, 6 captions (1.txt–6.txt) had an extra backslash
the generator wrote between a character tag and the next tag:

    ... solo focus, fugue \(honkai: star rail)\, brown hair, nipples, ...

**Byte-by-byte verification** (codepoints against the backup): only the **open**
paren was escaped; the close paren was bare, followed immediately by that extra
backslash. The codepoint sequence `... 108, 41, 92, 44 ...` is `l` `)` `\` `,` —
there is **no** backslash before the close paren. This disagrees with the
double-paren escaping used everywhere else in the repository (1043 files, and the
inference prompt).

The trainer splits on **bare commas**, so what it read was a dirty tag with a
trailing backslash: `fugue \(honkai: star rail\)\`. **This directory's training
data had been dirty the whole time**, and backslashes are invisible, so nobody
would notice.

## How it was found

While researching the tagger output format, workflow C reported "existing captions
escape their parens, the tagger output does not". To settle whether to add
escaping, I counted `\(` usage in the dataset (1049 files) and grepped `\,` along
the way — 6 hits, all in this one directory.

**Side effect**: this directly overturned the "comma escaping" design the spec had
originally allowed. Since what the editor displays must equal what the trainer will
read, `split_tags` cannot do any unescaping.
See [p0-spec](../p0-spec.md) §3.2 and A6 in §5.

## How it was fixed

After the user approved (this changes user data, and by the rules that needs a nod):

1. **Back up first**, to a place **outside** the dataset — anything added inside the
   dataset breaks the read-only fingerprint:
   a timestamped `_caption_fix_backup_2026-09-17_1917/` directory outside the dataset (and outside the repository)
2. **Go through the editor's own `core.captions.write_caption`** rather than
   hand-writing files — that way the atomic write and the cache invalidation take the
   same real path, which also counts as a dogfood run.
3. Assert item by item: `\,` count zero, `\(` `\)` counts unchanged, the split result equals the
   trainer's rule, and no tag ends with a backslash.

Measured: each of the 6 files lost **1 byte**, and the `\(` count stayed 1→1.
Script: a one-off fixer, not kept in the repository.

## Consequences

- **The read-only fingerprint baseline was re-sampled**: `.txt` bytes `925026 → 925020`, file count still 3306.
  The snapshot used for that comparison is no longer committed; generate your own with `tools/dataset_manifest.py --out <file>`.
- **One test went red**: `test_captions.py::test_real_dataset_file_splits_like_the_trainer`
  asserted that a real file "contains the defective byte", so it necessarily fails after
  the fix. That case was **designed wrong** (it asserts the current contents of user data)
  and has been rewritten to assert only the invariant. The lesson went into the workflow
  skill's high-frequency pitfalls.
## Second site: a close paren missing its escape (same day, user approved fixing it too)

After deleting the stray backslashes I did a survey and found those 6 files were the
**only** samples in the whole corpus with inconsistent paren escaping:

| Shape | Files |
|---|---|
| both parens escaped `\(...\)` | 1043 |
| **only the open paren escaped** `\(...)` | **6** ← these 6 |
| no escapable paren | 604 |

Output of the same generator, but the fix goes the opposite way — not removing a
character, but **adding** one backslash before the close paren. I raised it separately
with the user, who replied "fix them all together" and added one key constraint:

> **Some kaomoji carry a lone close paren; do not mishandle those.**

The constraint is right, and **it really is in the data**:

- In the dataset, `3_sample_gamma` has 5 occurrences of `;\)` — lone bare close
  parens (already escaped), with no matching `\(`;
- The vocab has 4 such tags: `;)` `>:)` `;(` `>:(`.

So the fix script applies **no** crude rule like "escape every bare )" or "delete the
backslash before )"; instead it scans **pair-aware**: it adds an escape only when a bare
`)` actually closes an `\(` that is still open, and leaves any unpaired bare `)` exactly as it
was. Script: a second one-off fixer, likewise not kept.

Tag-level re-check (1538 distinct tags):

| Shape | Count |
|---|---|
| no parens | 1466 |
| both parens escaped | 70 |
| only the open paren escaped (defect) | 1 ← fixed |
| only a bare close paren | **0** |

Measured fix: each of the 6 files changed 1 tag (positions 20/29/29/31/30/24); every other
tag was **asserted unchanged item by item**; the 5 occurrences of `;\)` were left exactly
as they were.

## The same risk on the vocab side (pinned down)

Workflow C's `escape_parens` is a **literal rule** (add a backslash to every paren without
checking whether it is already escaped). For tags that come from the vocab that is correct:
the 5 tags in the vocab containing a backslash are all kaomoji and **none contains parens**;
the 1598 tags containing parens **none contains a backslash**, so the intersection is 0.

- `;)` → `;\)`, `>:(` → `>:\ (` — **escape** rather than drop or miss it, matching the `;\)` shape
  already in the dataset.
- One side effect to remember: this rule is **not idempotent** (`;\)` would become `;\\)` on a second pass),
  so it **may only be applied to raw vocab tags**, never to text that is already escaped.
  C pins this down with a test.

Acceptance criteria A19 / A19b guard these two points.

- The dataset has **no training cache at all** (`cache_text_encoder` / `latent_cache` each 0), so nothing needed
  invalidating this time — which also says invariant #1's end-to-end path has not yet been run
  on real data.

---

Related: [p0-spec](../p0-spec.md), [dataset-contract](../dataset-contract.md), [encoder-cache-invalidation](../encoder-cache-invalidation.md)
