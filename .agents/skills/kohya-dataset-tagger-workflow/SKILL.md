---
name: kohya-dataset-tagger-workflow
description: Change workflow for the Kohya Dataset Tagger repository - which memory file to read first, which layer a change lands in, which gates to run, and which docs to sync afterward, plus a list of high-frequency pitfalls. Use when changing the Kohya Dataset Tagger code, for example the FastAPI backend, directory browsing, caption read/write with training-cache invalidation, the thumbnail pipeline, tag editing/filtering, or the WD14 tagger integration.
---

# Kohya Dataset Tagger change workflow

This skill is this repository's working procedure (how to change things, how to verify after changing, how to sync the docs).
**Concrete parameters and historical conclusions are not in this file**: they live in the second-level indexes under `.github/memory/`.

## When to use

- Before changing any code or documentation in this repository.
- When picking up an old session and needing to get back to "knowing which file to read" quickly.

## Step 0: read the map before the code

1. `AGENTS.md` (the resident index): project map, commands, hard constraints - it applies every time and is already in context.
2. `.github/memory/current-state.md` (**first stop when taking over**): what has been verified, what has not, and what the next step is.
   If your context was compacted, or you are coming back after a few days, reading this page first saves a round of blind searching.
3. Pick a category index by task, then read one or two of its topic files:

| Task | Second-level index | Typical topic files |
|---|---|---|
| **Taking over / wanting to know where things stand** | `INDEX-scope.md` | `current-state.md` |
| Generating / modifying dataset.toml | `INDEX-scope.md` | `p0-spec.md` §3.9 §4.9 (acceptance A23 is scored by the trainer) |
| Directory scanning, caption read/write, counting rules | `INDEX-scope.md` | `dataset-contract.md` |
| Adding modules, changing layering, integrating with the trainer | `INDEX-scope.md` | `project-architecture.md` |
| Thumbnails blurry/slow, cache directories | `INDEX-pipeline.md` | `thumbnail-pipeline.md` |
| **Any code that writes captions** | `INDEX-pipeline.md` | `encoder-cache-invalidation.md` (required reading) |
| Wiring up a tagger, tuning thresholds, post-processing | `INDEX-tagging.md` | `wd14-tagger.md` |
| Finding model files, setting the default model | `INDEX-tagging.md` | `local-model-inventory.md` |
| "Why is it like this?" | `INDEX-history.md` | files under `changelog/` |

**Do not read whole directories cover to cover**, and **do not change parameters from memory** - most thresholds and paths have been verified, and re-deriving them is expensive.

## Step 1: make the change in the narrowest layer

Before adding a surface, an action or a number on screen, read
[design-principles](../../../.github/memory/design-principles.md) - the principle family and the invariants
are what the reviewer will check a change against.

When you are the one **delegating** the change, hand the implementer
[BRIEF-TEMPLATE.md](BRIEF-TEMPLATE.md) filled in - the blocks in it are the ones whose absence has
cost this repository a wasted round.

| Change type | Landing spot |
|---|---|
| Path resolution, allowlist validation | `core/paths.py` (single source of truth, do not bypass) |
| Caption read/write and separator rules | `core/captions.py` |
| Cache invalidation | `core/cache_invalidation.py` (read that topic file before changing anything) |
| Thumbnail pipeline | `core/thumbs.py` |
| Vocabulary parsing | `core/vocab.py` |
| WD14 preprocessing and post-processing | `autotag/wd14.py` |
| Model path discovery | `autotag/registry.py` |
| HTTP semantics and parameter validation | `api/` (never touch the filesystem directly) |
| Frontend | `web/` (native ES modules, no build step) |
| UI copy (any string in a locale pack) | `web/js/locales/*.js` - all three packs change together, with identical keys and `{placeholders}`; the wording rules are in `.github/memory/ui-copy.md`, the engine and relabel contract in `.github/memory/i18n.md` |

For new logic, ask first: **is it a "path"?** If so, go through `core/paths.py`; **is it "writing a caption"?**
If so, it must be wired to cache invalidation.

## Step 2: gates (two tiers: the loop you iterate in, and the one you report with)

**While iterating, do not run the whole suite.** It is ~80 s, and about a quarter of that is ten tests
doing real work (ONNX inference, a live HuggingFace endpoint, a real dataset scan). Run only what covers
what you changed:

```bash
python tools/check_relevant.py             # changed files -> the tests/harnesses that name them (+ family nets)
python tools/check_relevant.py --dry-run   # print the plan, run nothing
```

**Before reporting, once:**

```bash
python tools/check_relevant.py --full      # whole suite + documentation gate (+ acceptance if the contract moved)
python -m pytest test/ -q -n auto                                # the same full suite across cores (74.5 s -> 25.2 s, same result)
python -m pytest test/test_docs_index.py -q                      # documentation structure gate only
```

- The selector is evidence-based, not a map: a test is relevant when it names the changed file, and a
  change inside a family also runs that family's net (`test_web_contract.py` for frontend changes,
  `test_api_e2e.py` for `src/` changes). Anything **no** test mentions is printed as **UNCOVERED** -
  when you see that, the full suite is the only cover for it, so run it.

- The documentation structure gate uses only the standard library + pytest, so **the system `python` can run it** (the trainer's venv has no pytest).
- **Verify all three paths**: normal, failure, recovery. Verifying only "it runs" does not count.
- Destructive disk operations (cache invalidation, batch caption writes) **must** be round-trip tested in a temporary directory:
  write → read back → delete the cache → confirm it is rebuilt. Unit stubs alone are not allowed.

## Step 3: write conclusions back into the docs (a hard requirement of this repository)

- Behavior/parameter changes → update the **matching memory topic file**, changing its `description` too (the index line reads exactly that).
- New topic file → add a line to `INDEX-<category>.md`, otherwise `test/test_docs_index.py` turns red.
- **Do not write details back into AGENTS.md** (the budget is 28 KB and the gate enforces it); keep only pointers there.
- This repository has **no** Claude bridge file and does not need a new one - AGENTS.md is the single source of truth for resident instructions.

## High-frequency pitfalls

- **Do not swap packages in the venv while another agent is running tests.** On 2026-09-17 I ran an install that replaced onnxruntime with the GPU build in the background,
  three agents hit `ModuleNotFoundError` at the same time, one of them "helpfully" reinstalled the CPU build, fighting with my install,
  and in the end the environment had neither. Before touching a shared environment, confirm nobody is running tests; after an incident, **fix the environment before explaining**.
- **A `.ps1` containing Chinese must be saved as UTF-8 with a BOM.** Windows PowerShell 5.1 decodes as ANSI(GBK) when there is no BOM,
  turning all Chinese into mojibake and failing to parse. `pwsh`(7+) defaults to UTF-8 so it never exposes the problem - running it once with 5.1 is what counts as verified.
  **Two additional traps** (both stepped on): (a) **edit-style operations strip the BOM** - once it is written, any further edit removes it;
  (b) checking syntax **must not use `Get-Content -Raw | PSParser::Tokenize`** - `Get-Content` decodes first,
  which is a different path from the engine's byte-level decoding, so you get "the check passes but the script will not run".
  Use `[System.Management.Automation.Language.Parser]::ParseFile()` instead (reads bytes, same path as execution),
  or simply run it. `test/test_ps1_encoding.py` guards both the BOM and parsability.
- **A module-scoped fixture + global state = an execution-order bomb.** `core.paths`'s allowlist is global,
  and other cases in the acceptance suite call `paths.configure(...)`; once `api_client` was made module-scoped,
  it published the allowlist only the first time and never restored it after being overwritten - the full run failed while an isolated run passed.
  Always use function scope for fixtures that share global state.
- **`subprocess` must always pass explicit `encoding="utf-8", errors="replace"`.** The default follows the locale (GBK on this machine);
  when a subprocess's output contains Chinese, the reader thread raises `UnicodeDecodeError`, `proc.stdout` becomes `None`,
  and the criterion becomes a **random false red** - depending on whether the random bytes in the path happen to be valid GBK.
  On 2026-09-17 my A4b went red on and off for half a day this way, while **the allowlist was fine the whole time**.
  Worse, E had reported this pitfall earlier; I fixed the files for D and A and **missed my own acceptance suite**:
  when fixing a problem of the same kind, grep the whole repository for `text=True` first instead of fixing only the files that were named.
- **Tests must not assert the current contents of user data.** The real dataset is **mutable** - the whole point of the editor is to change it.
  A case that says "the defect must still be in the file" is equivalent to forbidding the editor from working, and it goes red every time the user edits a caption.
  Cases against real data should assert only **invariants that hold for any contents** (such as "the split rule equals the trainer's"),
  and defect behavior should be verified with synthetic constants or a `tmp_path` copy. This really happened on 2026-09-17: after fixing stray backslashes in 6 captions,
  a case asserting "the defect bytes are still there" went red immediately.
- **A criterion only counts if it can fail.** After adding or changing a criterion, do one **falsification**: deliberately break the behavior under test → confirm it turns red → restore.
  This repository has seen several rounds of "the criterion is green but it is not actually testing anything": counting stdout when the target writes to stderr (counts 0),
  matching a number against a pprint log that wraps lines (219 counted as 218), treating `Get-Content | Tokenize` as a syntax check,
  and **random false reds** caused by subprocess encoding (the allowlist was clearly fine). When adding a criterion, spend the extra minute on a falsification.
- **Changing a caption without deleting the TE cache = silently using the old tag.** The trainer will not discover this for you (`library/strategy_anima.py:295-349`
  only checks whether the key exists). This is this repository's number-one invariant.
- **WD14 preprocessing must not be "abstractly unified".** BGR + white-border square padding + raw 0-255 + NHWC; get any one of the four wrong and you get garbage tags,
  with no error. Other models (the JoyTag family) are CHW + /255 + CLIP normalization, and the two cannot share one code path.
- **Do not write the thumbnail cache into the dataset directory.** It pollutes `image_dir` and gets treated as junk by its own directory scan.
- **Do not convert every PNG to an opaque JPEG.** Users need to see from the thumbnail which images have alpha (this matters for `alpha_mask`).
- **Do not hardcode the snapshot hash of the HF cache.** That `<sha>` directory changes when something is re-downloaded; discover it by wildcard or store the directory.
- **Do not make the thumbnail size a request parameter.** It would fragment the cache by arbitrary sizes; the size is a constant.
- **Do not follow the trainer's path concatenation.** `training-ui/server.js:1800` does not validate `..` and is a counter-example.
- **CSV filenames in the HF cache are not uniform** (`selected_tags.csv` vs `wd-vit-tagger-v3.csv`), so discover them as `*.csv` in the same directory.
