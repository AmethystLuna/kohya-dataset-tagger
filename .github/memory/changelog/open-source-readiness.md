# Publishing the repository: a licence, CI, and not one personal path

**When to read this**: you are wondering why the tests skip on your machine, where their paths come from,
what the GitHub Actions jobs run, or why the repository has a single commit.

## Why (2026-09-21)

The repository was about to be pushed to GitHub for the first time, and three things would have gone out
with it:

1. **No licence, no CI, no community files** - nothing that tells a visitor what they may do with it, and
   nothing that runs the tests anywhere but here.
2. **A test suite that assumed this machine.** Measured on a copy with the private paths pointed at a
   nonexistent drive: **21 failed + 21 errors**. A CI job running `pytest` would have been red from the
   first push and stayed red.
3. **Personal paths and private directory names all over the tree** - in tests, tools, launcher examples
   and the design memory: the author's drive layout, the model cache locations, and the subset directory
   names of a private training set (which are artist names). A public repository, and above all its git
   history, would have carried all of it.

## What was added

| Added | Notes |
|---|---|
| `LICENSE` | MIT; `pyproject.toml` declares it with PEP 639 (`license = "MIT"`, `license-files`, `setuptools>=77`). No `License ::` classifier - PEP 639 deprecates it |
| `.github/workflows/ci.yml` | `docs` (structure gate), `tests` (Ubuntu 3.10/3.13, Windows 3.10), `network` (weekly, see below) |
| `.github/dependabot.yml` | pip + github-actions, grouped, weekly |
| `CONTRIBUTING.md` | setup, the two-tier gate, what CI runs, the five rules a change has to respect |
| `CODE_OF_CONDUCT.md`, `SECURITY.md` | SECURITY.md states the threat model outright: the allowlist is a typo guard, and `--host` on a non-loopback address hands read/write access to the root endpoints |
| `CHANGELOG.md` | Keep a Changelog, `0.1.0` |
| `ISSUE_TEMPLATE/*`, `PULL_REQUEST_TEMPLATE.md` | the PR template asks for the falsification and the topic-file sync this repository already requires |
| `.editorconfig`, `.gitignore` | editor behaviour that mirrors `.gitattributes` and the `.ps1` BOM rule; editor/OS noise ignored |
| `README.zh-CN.md`, `README.ja.md` | the README in three languages, chosen from a centred navigation under the title. All three are checked the same way: every command, path, table row and code block is **byte-identical**, only the prose and the comments inside code blocks are translated. The Japanese one takes its vocabulary from `web/js/locales/ja.js` rather than inventing its own |

## Every path now comes from `local_paths.ini`

The repository holds **no machine path at all**. Anything that needs one reads it from `local_paths.ini`,
which is gitignored:

    [paths]
    dataset = D:\datasets\my-lora     # the kohya-style dataset the criteria read (never written)
    models  = D:\taggers;E:\more      # model search roots, os.pathsep separated
    hf_hub  =                          # a HuggingFace cache, for the real-vocabulary criteria
    trainer =                          # a trainer checkout, for acceptance A23
    scratch =                          # where the destructive benches may write

`local_paths.ini.example` is committed and its defaults point **inside the repository**: the sample
dataset under `test/fixtures/sample_dataset/` and the `models/` directory. `local_paths.py` is the
reader (stdlib `configparser`, no interpolation - a Windows path may contain `%`) and prints what is in
effect. Relative values resolve against the repository root.

`test/fixtures/sample_dataset/` is the reason a fresh clone is not just green but **useful**: 3 subset
directories, 6 images (24×24, ~1.4 KB in total), 5 captions, one image deliberately without one, and a
`cache_text_encoder/` + `latent_cache/` inside a subset plus one at the root, so the cache/hidden filter
in `core/paths.py` is really exercised. The criteria whose subject cannot exist without a downloaded
model or the real vocabulary skip themselves and say so.

## The dataset criteria learned to skip

Four gaps, each found by simulating a clean checkout rather than by reasoning:

1. `test_api_e2e.py`: the `dataset_client` fixture **skips** when no dataset is configured (its subject is
   real content). The criteria that only need "some root" already used `tmp_path` and keep running.
2. The same file's `workspace` fixture no longer requires a real photograph: `_copy_sample_image()`
   **synthesizes** the one captioned sample when the dataset is the committed one, because the ~21 criteria
   built on it are about the write path (caption write -> cache invalidation -> read back, batch operations,
   previews) and none of them looks at the picture.
3. `test_wd14_integration.py` had four tests without a guard, and not all of them the same precondition:
   discovery/resolution needs `wd-swinv2-tagger-v3`, the ComfyUI-copy case needs `wd-vit-tagger-v3` as well
   (hence `requires_comfy_copy` next to `requires_model`).
4. Running against the sample dataset **revealed three criteria that only worked because a model happened to
   be installed**: the two autocomplete ones (no vocabulary -> an empty list, which is correct behaviour) and
   the option-validation one (with no model at all the endpoint answers 409 `model_unavailable` before it
   looks at the thresholds). They now call `_require_vocabulary()` / `_require_local_model()`.

**One product-level fix came out of it too**: `POST /api/autotag/preview` and `/autotag/run` used to
resolve the model *before* validating `thresholds` / `postprocess` / `write_mode`, so on a machine with no
model a malformed request answered 409 `model_unavailable` instead of 400 `bad_request`. The request's shape
is now checked first and the environment second, in both handlers (`api/autotag.py`), and p0-spec §4.7
records the order. The criterion that covers it needs no model any more, so it runs everywhere.

A separate CI requirement came out of the same simulation: `start.sh --dry-run` criteria need the
repository's own `.venv` to exist, so the workflow **builds a real one** instead of installing into the
runner's interpreter - which also exercises `requirements.txt` the way a user does.

## The golden counts are gone

The reference dataset had 219 subdirectories / 1653 images recorded in 2026-09-17, and five criteria pinned
those numbers. The dataset then grew (the crop tool archived two images; a subset directory was added), and
all five went red while **A1, the relative read-only guard, stayed green in the same run** - which is what
proves no *test* wrote them.

They were **deleted, not re-sampled**: A2 and A23 now assert that the plan agrees with the **directory
listing** (the per-subdirectory counts sum to the recursive count; one subset per populated directory), and
A3 asserts the exclusion of cache/hidden entries against a listing that is non-empty. An exact count is a
fact about *data*, not about *code*, and a criterion that pins one turns red on a legitimate edit. p0-spec §5
was updated with the criteria.

The stored fingerprint (`test/fixtures/*.baseline.json`) is deleted for the same reason and one more: it
was a picture of a private dataset. A1 has always used a **relative** fingerprint (sample `--out` before the
run, `--check` the same file at the end); a stored one is a manual tool, and you generate your own.

## What replaced the literal paths

| Was | Is |
|---|---|
| `F:\...\data sets\<private dataset>` | `local_paths.dataset_root()` |
| `F:\...\huggingface\hub` | `local_paths.hf_hub()` (None-safe: the script says which key to set) |
| the ComfyUI model directory | `local_paths.model_roots()` |
| the trainer checkout | `local_paths.trainer_root()` |
| a scratch directory under the author's drive | `local_paths.scratch_root()` |
| `1_<date> <artist name>` subset directories | `1_sample_alpha` / `2_sample_beta` / `3_sample_gamma` |
| a LAN address of a borrowed machine | a documentation address (RFC 5737) |

In the design memory the same literals became descriptions ("the reference dataset", "the HuggingFace
cache", "a scratch directory outside the repository"), with every measurement, count, quotation and
falsification note left byte-identical.

## The network criterion is scheduled, not per-push

`test_real_endpoints_list_the_same_eva02_onnx` fetches two manifests from huggingface.co and hf-mirror.com.
It is marked `network` (registered in `pyproject.toml`); the per-push job runs `-m "not network"` and the
scheduled job runs `-m network`. A third party must not be able to redden someone's pull request, and
`1/1103 tests collected (1102 deselected)` is the check that the marker selects exactly that one case.

## The history was re-created

The old history contained all of the above, so the repository was **re-initialised with a single commit**:
the old `.git` was copied out of the tree first (it is not recoverable from the new repository), then
removed and `git init` run again. Nothing else about the working tree changed. The user's e-mail is not in
the commit either - it uses the GitHub noreply address (`git config user.email`, repo-local).

## Evidence

- **Clean checkout, no private configuration** (simulated: a copy of the tree with `.git`, a freshly created
  `.venv`, and **no** `local_paths.ini`): `pytest test/ -q -n auto -m "not network"` -> **1072 passed,
  31 skipped, 0 failed**, with the committed sample dataset as the only data.
- With this machine's private `local_paths.ini` (the real dataset and its models): **1101 passed, 2 skipped**,
  and `acceptance/` **81 passed** - it was 79 passed + 2 failed before the golden counts were deleted.
- The round's first simulation, before the dataset criteria learned to skip: **21 failed, 1035 passed,
  26 skipped, 21 errors**. After: **0 failed**.
- **The CI job rehearsed end to end** in a sandbox: a fresh `.venv`, `pip install -r requirements.txt`
  (all 34 distributions), the import check, then the suite - exit 0.
- Local suite **with** the private `local_paths.ini` (the real dataset and models): green, and the three
  pre-existing reds are gone because the golden counts were deleted.
- `pip install --dry-run --no-deps .` with build isolation: metadata builds, "Would install
  kohya-dataset-tagger-0.1.0".
- Every new YAML file parsed with PyYAML; the workflow's three jobs and its matrix read back.
- Leak grep over the whole tree (code, docs, skills, launchers) for drive-letter paths, the cache and
  trainer directory names, the LAN address, the artist names and the backup directory name: **no hits**.
- `test/test_docs_index.py`: 9 passed after all the new files.

## Known limits

- **Only the Windows job is measured.** There is no Linux and no Docker on this machine, so the Ubuntu jobs
  are reasoned from the suite's own platform guards (`os.name == "nt"` skips, `/etc` fallbacks) - not from a
  run. The Windows job mirrors the simulation.
- The sample dataset satisfies the `dataset_client` criteria, not the **vocabulary** ones: with no model
  downloaded those skip, and that is the intended answer rather than a gap.
- `tools/` was cut down to the ten scripts that are still load-bearing (the gate, the provider check, the
  manifest tool, the smoke test, the layout probe, the trainer validator, three benchmarks and
  `probe_whitelist.py`, which acceptance A4b executes). The twenty one-off probes were **deleted** - with
  them went the last dataset-*content* literals they carried (an image count, the tag `fugue`). The design
  memory still names them where a measurement came from, marked "a one-off probe, not kept in the
  repository".
