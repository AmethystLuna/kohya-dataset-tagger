---
name: read-only-guard-vs-live-dataset
description: 2026-09-19 the read-only fingerprint criterion changed from "compare to a 2026-09 snapshot" to "compare to before the run" — the dataset snapshot assertion was taken out of the automatic gate
metadata:
  type: topic
---

# Read-only guard: a relative fingerprint replaces snapshot comparison (2026-09-19)

Origin: the user asked "why does that test carry a fingerprint of the real dataset". Digging in showed the problem
was not "should there be a fingerprint" but **what the fingerprint is compared against** — the automatic gate was
pinned to a **dataset snapshot that goes stale**.

## Facts

- The dataset configured in `local_paths.ini` is the read-only test set (§0.1), and e2e and acceptance run read-only
  flows like "preview / thumbnails / tag frequency" against it. **"We do not write it" has to be executable**, hence
  `tools/dataset_manifest.py`: it hashes the sorted `(relative path, size, mtime_ns)`.
- But this fingerprint was compared against a manifest snapshot you generate yourself with `tools/dataset_manifest.py --out <file>` - a **snapshot taken on 2026-09-17/19**:
  `file_count` = 3306, 1653 `.txt` files. By 2026-09-19 the live state was already **4959 entries** — the user added images,
  a completely legitimate action.
- So three criteria went red **before any code under test ran**:
  - `acceptance::test_A1_dataset_untouched_at_suite_start` (which additionally hard-coded `file_count == 3306`,
    `.txt == 1653` — while its own comment a paragraph earlier had just written "do not hard-code total_bytes, the dataset is alive");
  - `test/test_api_e2e.py::test_preview_is_read_only_and_shape_is_frozen` (the `first.returncode == 0` on line 870
    is redundant with the read-only proof: the real proof is lines 902–905);
  - `acceptance::test_A19b_dataset_lone_paren_kaomoji_untouched` - it asserted that **the `;\)` defect was still in the dataset**,
    and the defect had long since been fixed, so it went red too. This is exactly the skill's high-frequency pitfall: **a test must not assert the current contents of user data**.

## The two claims a criterion can make; do not mix them

| Claim | Nature | When it holds |
|---|---|---|
| "this test did not write the dataset" | a **relative** invariant: before vs after within one run | **in any data state** |
| "the dataset equals a snapshot" | an **absolute** snapshot | only while the snapshot is fresh |

The read-only boundary wants the first. The second goes red because of the **data**, not the **code** — it misreports
"the user added images today" as "some code path wrote the read-only test set", and the noise is loud enough that
people start ignoring the criterion, which is the real loss.

## What changed

1. **A1 became a relative invariant**: `acceptance/` gained a session-scoped autouse fixture
   `_a1_dataset_untouched` — sample once with `dataset_manifest.py --out <scratch>` before the run, then `--check` **the same file**
   at the end of the session. It covers the **whole acceptance session** (not just one case), which is exactly the
   radius "some code path wrote it" deserves. `test_A1_dataset_read_only_guard_is_armed` only proves the guard really got set up
   (the dataset exists, the pre-run sample was taken), so the closing assertion cannot silently become an empty criterion.
2. **The preview read-only criterion was relativized the same way**: `test/test_api_e2e.py` samples before with `--out` and compares after
   with `--check`, and no longer touches `BASELINE`; `_manifest_check()` (against the snapshot) became `_manifest_run(*args)`.
3. **A19b became a synthetic round-trip**: "the dataset must contain `;\)`" became "an escaped lone-close-paren kaomoji,
   through `write_caption` → `read_caption` → `split_tags`, is still the same tag" — it asserts **tool behavior**, no longer
   depending on the current state of user data.
4. **The snapshot was demoted to a manual step**: you can still generate a manifest snapshot yourself with `tools/dataset_manifest.py --out <file>`, but it is no longer an automatic gate;
   §0.4 and the AGENTS.md note were synced to "two criteria, do not mix them".

## Same-day wrap-up: baseline re-sampled (as the user asked)

The old snapshot was stuck at 3306 entries, and the manual `--check` in AGENTS would report FAIL forever. As the user asked, we ran
`tools/dataset_manifest.py --root "<dataset>" --out <file>`:
`file_count` 3306 → **4959** (the extra 1653 entries are caption backups `*.txt.bak`),
`total_bytes` 13381018394 → **13382190161**, `manifest_sha256` → `fbf67b54f3a5155d…`;
two `--check` runs in a row both OK (the fingerprint is stable on the spot).

The re-sample **only reads the dataset and only writes the fixture in the repository**, and does not change the conclusion above:
a snapshot necessarily goes stale as the dataset changes, and the automatic gate is still A1's relative invariant. One
incidental piece of evidence: the actual sha before the re-sample (`b3c4e5eb…`) differed from the actual value in the acceptance report
a few tens of minutes earlier, while `file_count` / `total_bytes` matched exactly — meaning some file's mtime changed during that
window (size and path unchanged), with a source outside the test window; A1's relative guard was green for every session at the time.

## Criteria and falsification

| Criterion | Falsification (all really went red, then were restored) |
|---|---|
| A1 closing comparison (`_a1_dataset_untouched`) | clear the pre-run snapshot contents to `{}` → the closing `--check` comparison fails and pytest exits 1 |
| preview read-only (relative) | same, clear `manifest-before.json` to `{}` → the case fails at the after-check |
| A19b synthetic round-trip | change `core/captions.py`'s `SEPARATOR` from `", "` to `","` → the byte-by-byte assertion fails |

All three are "deliberately break → go red → restore → all green"; none is "passes but is not actually testing anything".

## Related

- Contract: [p0-spec](../p0-spec.md) §0.4, §5's A1
- The snapshot's backstory: [caption-defect-fix](caption-defect-fix.md) (each of the two approved caption fixes re-sampled the baseline once)
- Another change the same day: [picker-manual-input-and-mkdir](picker-manual-input-and-mkdir.md)