# AGENTS.md

This file is this repository's **resident index**: it holds only the project map, commands, and hard constraints that every change needs.
Subsystem details, historical decisions, and pitfall write-ups live in the **second-level indexes** under `.github/memory/`; read them when needed:

    category table .github/memory/MEMORY.md  →  topic lists .github/memory/INDEX-<category>.md  →  topic files

This file is the **single source of truth** for resident instructions. There is no need to hang a bridge file for other clients too - injecting the same body twice
just wastes context.

**Skills live in `.agents/skills/`**: one directory per `SKILL.md` plus `name`/`description` frontmatter;
frontmatter holds only spec fields, client extensions go under `metadata`.

The budget gate `test/test_docs_index.py` watches six things: this file ≤ 28 KB, every link in the index points at an existing file,
no orphan topic files, every `INDEX-<category>.md` is linked from both `MEMORY.md` and the routing table here,
every skill's frontmatter is spec-compliant, and the three READMEs stay in step (one navigation, one skeleton, one set of commands and link targets). **Do not write details back into this file** - new content goes into a topic file, with one line added to the index.

---

## Project overview

**Kohya Dataset Tagger** - a **standalone dataset tagger** for **kohya-style training sets** (works for LoRA / finetune / DreamBooth).
Do the whole chain in the browser:

**directory browsing → gallery preview → per-image tag editing → tag filtering → batch WD14 captioning → generate a trainable dataset.toml**

It is a **companion tool** to **Anima-Standalone-Trainer** (a sibling project, not published here), but **not a plugin for it**: its own repository, its own process,
its own page, interacting with the trainer only through the **filesystem contract** (the dataset directory, `dataset.toml`, the training cache).

Stack: a Python backend (FastAPI + uvicorn) + a build-free native ES modules frontend.
The Python environment is **the repository's own `.venv`** (about 250 MB, **torch is not needed**).

**Current status: P0 complete** (the "Current status" section below). **Before taking over, read
[.github/memory/current-state.md](.github/memory/current-state.md)** - it has the verified/unverified checklist and the next step.

## Run and development commands

```powershell
# End-user entry points (two sets at the root, symmetric across Windows / Linux)
.\setup_env.bat      # Windows: one-shot setup (idempotent; DirectML onnxruntime by default)
.\setup_env_cn.bat   # Windows: same, but package installs use the China-mirror profile (USTC→Aliyun→pypi.org fallback)
.\start.bat          # Windows: one-shot start + opens the browser (asks once for the dataset root, stores it in roots.txt)
./setup_env.sh       # Linux/macOS: same (CUDA if nvidia-smi exists, otherwise CPU)
./setup_env_cn.sh    # Linux/macOS: same, but package installs use the China-mirror profile
./start.sh           # Linux/macOS: same
                      #   if the port is taken it moves on; opens the browser only once the service is ready
                      #   both sets accept --dry-run: print the command lines that would run, start nothing (for acceptance)

# Direct development run
.\.venv\Scripts\python.exe -m kohya_dataset_tagger --roots "<dataset>" --port 3001
.\.venv\Scripts\python.exe -m kohya_dataset_tagger --dump-config   # print the config only; first place to look when debugging a 403
.\.venv\Scripts\python.exe -m kohya_dataset_tagger --color never    # no colored logs (CI / piping to a file)

# Gates (**all need the .venv python**, the system python has no fastapi/pillow)
.\.venv\Scripts\python.exe tools\check_relevant.py          # inner loop: only the tests that cover the change (seconds, not minutes)
.\.venv\Scripts\python.exe tools\check_relevant.py --full   # the gate before reporting: full suite + docs (+ acceptance if the contract moved)
.\.venv\Scripts\python.exe -m pytest test/ -q -n auto   # full unit suite across cores (74.5 s -> 25.2 s, same result)
.\.venv\Scripts\python.exe -m pytest acceptance/ -q      # acceptance criteria (written independently against p0-spec §5)
.\.venv\Scripts\python.exe tools\check_provider.py       # the provider onnxruntime **actually** uses
.\.venv\Scripts\python.exe tools\e2e_smoke.py            # real HTTP end to end (requires a running service)
.\.venv\Scripts\python.exe tools\measure_web_layout.py    # real layout geometry in headless Chrome (canned /api/*; needs no service and no dataset)
python -m pytest test/test_docs_index.py -q                # documentation gate (standard library only, system python works)
```

The dataset read-only boundary is guarded by a **relative** fingerprint criterion: sample once with `tools\dataset_manifest.py --out <temp file>` before
acceptance starts, and `--check` **the same file** at the end (the `_a1_dataset_untouched` session fixture in `acceptance/`).
There is deliberately **no stored snapshot** in the repository: one would expire the moment the dataset changes,
and it would be a picture of someone's private dataset. Store your own (`--out <file>`) if you want one to compare by hand.

## Architecture

```
setup_env.bat / start.bat              # user entry points (thin wrappers)
scripts/setup_env.ps1 | .sh            # environment setup (-Gpu auto|cuda|directml|cpu)
scripts/start.ps1 | .sh                # launcher (roots.txt / model_paths.txt)
models/                                # ★ models go here (a placeholder file lives inside; search root position 0 + download destination)
roots.txt                              # remembered dataset roots (machine file, not committed; editable in the UI)
model_paths.txt                        # extra model scan paths (appends; template in .example; editable in the UI)
src/kohya_dataset_tagger/
├── __main__.py        # CLI: argument parsing + config.load() + color decision + uvicorn
├── app.py             # FastAPI assembly: discover api/ routers dynamically, mount web/ at /, exceptions → frozen error shape
├── config.py          # runtime config: root allowlist / thumbnail cache / **model search root order** / runtime-editable (§4.11)
├── console.py         # log coloring: open the console VT on Windows itself, actively disable color if it cannot (§7)
├── api/               # every module carries prefix="/api"
│   ├── fs.py          # directory browsing (filters cache directories + path allowlist)
│   ├── roots.py       # ★ runtime-editable roots and model search directories (§4.11, writes roots.txt / model_paths.txt)
│   ├── images.py      # images / thumbnails / metadata
│   ├── captions.py    # caption read/write (atomic write + cache invalidation) + cache/status|invalidate + batch
│   ├── tags.py        # tag frequency, autocomplete (**substring** match)
│   ├── autotag.py     # WD14 models/preview/run/stream/cancel + model download
│   ├── dataset.py     # dataset.toml generation (pure computation, nothing written to disk)
│   ├── scale.py       # §4.14 image scaling/export (background task + SSE + cancel)
│   └── crop.py        # §4.17 image cropping (one image per request: crop -> archive beside the source)
├── core/              # leaf layer, mutually independent (sole exception below)
│   ├── paths.py       # ★ single source of truth for the path allowlist
│   ├── captions.py    # sidecar read/write, split rule (= the trainer's rule)
│   ├── cache_invalidation.py  # ★ implementation of invariant number one
│   ├── thumbs.py      # thumbnail pipeline + on-disk cache
│   ├── vocab.py       # selected_tags.csv parsing
│   ├── dataset_toml.py# generate a kohya-compatible dataset.toml (one subset per directory)
│   └── scaler.py      # §4.14/§4.17 image pipeline: optional crop -> area-normalized scaling -> atomic write (leaf)
├── autotag/
│   ├── base.py        # Tagger abstraction
│   ├── wd14.py        # WD14 v3 ONNX (preprocessing aligned with sd-scripts down to the letter)
│   ├── postprocess.py # underscore removal / bracket escaping / block list / sorting
│   ├── registry.py    # model id → path (recognizes the HF cache layout **and the flat layout**)
│   └── download.py    # download: HF + hf-mirror fallback, streaming, byte verification
└── web/               # build-free frontend (native ES modules; copy lives only in js/locales/*.js)
    ├── js/theme-boot.js # ★ the ONLY classic script: applies the stored theme before the first paint
    ├── js/theme.js    # ★ the theme: kdt.theme, readTheme/applyTheme, the switch (one writer for both)
    ├── js/strings.js  # i18n engine: locale resolution/switching, t(), applyCopy (contains no copy itself)
    ├── js/locales/    # one bundle per language (zh-CN / en / ja); key sets and {placeholders} must match exactly
    ├── js/palette.js  # T3: the command palette (Ctrl+K) - renders app.js's command table and owns no action itself
    ├── js/settings.js # §4.11's two panels: root add/remove in the left column + the tagger's "Model directory…" dialog
    ├── js/scale.js    # §4.14 image scaling/export panel (resolution + pipeline settings are shared via trainparams.js)
    ├── js/cropbox.js  # §4.17 pure crop-box geometry (handles, ratio lock, area normalization, box inheritance)
    └── js/crop.js     # §4.17 crop panel + the per-image confirmation dialog + the tagger step
```

**Dependency direction**: `api → core / autotag`. Inside `core` **only** one edge is allowed, `captions → cache_invalidation`
(§3.0); when cross-use is needed (e.g. `thumbs` validating the cache directory), **import lazily inside the function** and keep modules as leaves.
`autotag/*` must not import `core/*`.

## Hard constraints

- **A caption write must invalidate the text encoder cache at the same time.** Change a `.txt` without deleting the cache and training **silently uses the old tags**
  with no error, and **will not** rebuild automatically. Rules and executable evidence:
  [.github/memory/encoder-cache-invalidation.md](.github/memory/encoder-cache-invalidation.md). **Invariant number one.**
- **The split rule = the trainer's rule.** `split_tags` is just `[t.strip() for t in text.split(",") if t.strip()]`,
  **with no unescaping at all**; a tag containing a comma is rejected on write. What the editor displays must equal what the trainer will read.
- **`image_dir` is mixed with `cache_text_encoder/` and `latent_cache/`**, so browsing and counting must hard-filter them.
  See [.github/memory/dataset-contract.md](.github/memory/dataset-contract.md).
- **Paths must go through the allowlist.** Every endpoint that accepts a user path goes through `core/paths.py`; direct concatenation is not allowed
  (the trainer's `training-ui/server.js:1800` is the counter-example: it does not validate `..`).
  **The allowlist can change at runtime** (`POST/DELETE /api/roots`, §4.11) - deliberately: the service listens only on
  `127.0.0.1`, so it stops mistyped paths and out-of-bounds traversal, not another user on the machine. **Change `--host` to an external address,
  and those two endpoints hand over read/write access to any directory.** The last root cannot be deleted (400 `last_root`).
- **The two persistent files are written differently; do not unify them on a whim**: `roots.txt` is rewritten whole (machine-managed, comments not recognized);
  `model_paths.txt` **only has path lines added or removed**, with comments and CRLF preserved verbatim (that is a user-authored file).
  Both are **read once, at `config.load()`**; at runtime memory is authoritative.
- **Do not modify image files in the dataset.** Only read and write `.txt` captions; renaming/moving/deleting belongs to later versions
  and must go through "back up + move into `_trash/`" rather than `unlink`. **Adding one is the single exception, and it is §4.17's crop
  archive**: a confirmed crop is written *beside* its source as `<stem>_<N><ext>` - always numbered, never replacing an existing file, and the
  source's bytes and mtime untouched ([.github/memory/image-cropping.md](.github/memory/image-cropping.md)).
- **All disk writes are atomic**: temp file + `os.replace`, keeping the original extension and encoding (UTF-8).
- **WD14 preprocessing must not be "abstractly unified".** WD14 is NHWC + raw 0–255 + BGR; other models are CHW + /255 + CLIP normalization.
  Getting it wrong **raises no error**, it just emits garbage tags. See [.github/memory/wd14-tagger.md](.github/memory/wd14-tagger.md).
- **onnxruntime falls back to CPU silently.** What `get_available_providers()` says does not count; only
  `InferenceSession.get_providers()` is real. To determine the provider, use `tools/check_provider.py`.
- **A `.ps1` containing Chinese must be saved as UTF-8 with a BOM** (PowerShell 5.1 decodes as GBK without a BOM and the script fails to parse).
  Note that **edit-style operations strip the BOM**; `test/test_ps1_encoding.py` guards this.
- **`subprocess` always passes explicit `encoding="utf-8", errors="replace"`** (the default follows the locale, and Chinese output makes criteria randomly false-red).
- **The colored-log switch is a boolean, not `None`.** With `use_colors=None`, uvicorn only asks `sys.stdout.isatty()`,
  while in cmd.exe isatty is true but console VT is off → a literal `←[32m` shows up on screen. `console.py`
  opens VT itself and actively disables color if it cannot; `--color always|never` forces it.
- **Read bytes to determine line endings before changing a text file**: `read_text()` in text mode turns `\r\n` into `\n`,
  so "CRLF detected" is always false - that is exactly how `model_paths.txt` was silently changed from CRLF to LF in practice.
  Likewise, **write test fixtures with `write_bytes`**: `write_text("...\r\n")` becomes `\r\r\n`.
- **No non-ASCII bytes in `.bat` files.** The file is UTF-8 without a BOM, while cmd.exe decodes by the console code page (936 on this machine),
  so `echo Chinese` is guaranteed mojibake; let PowerShell speak the Chinese. `.sh` must be LF, `.bat` must be CRLF.
- **No frontend build step.** The frontend is native ES modules; a new dependency requires a written justification in a topic file.
  **One exception, and only one**: `web/js/theme-boot.js` is a classic `<head>` script, because a deferred module runs *after* the first paint
  (measured: 275 ms cold against a first paint at 116 ms) and a stored dark theme would flash light on every load. It cannot import the module that owns
  the theme - importing is what would make it a module - so the storage key `kdt.theme` exists twice and a criterion compares the two copies.
  `test_web_contract.py` allows exactly one non-module script, named, in `<head>`; see [.github/memory/changelog/light-mode.md](.github/memory/changelog/light-mode.md).
- **Copy lives only in `web/js/locales/*.js`.** The engine, panels, and templates must contain no CJK literals, and the key set and `{placeholders}` of every
  bundle must match exactly (`test/test_web_contract.py` guards this). Switching language is an **in-place re-render**:
  new render-time copy must be registered in `app.js`'s `relabelAfterLocaleChange()`, otherwise it stays in the old language.
  See [.github/memory/i18n.md](.github/memory/i18n.md).

## Tests and gates

Division of responsibility (this division is deliberate):

- `test/` - the implementer's own tests. Fast, close to the module.
- `acceptance/` - criteria **written independently** by the accepting side against `p0-spec.md §5`, not a restatement of the implementer's tests.
  **A23 among them is scored by the real consumer**: the generated `dataset.toml` is fed to **the trainer's own `config_util`**.

Rules:

- **Behavior changes** → `pytest test/ -q` all green + sync the matching topic file.
- **Contract changes** → change both `.github/memory/p0-spec.md` (the frozen contract) and `acceptance/` (the criteria).
  Changing only one side decouples the criteria from the contract.
- **Documentation changes** → `pytest test/test_docs_index.py -q`.
- **A criterion only counts if it can fail.** When adding one, do a falsification (deliberately break → confirm red → restore).
  This repository has several lessons of "the criterion passes but it is not testing anything"; they are all recorded in the skill's high-frequency pitfalls.
- **Verify all three paths**: normal, failure, recovery. Verifying only "it runs" does not count.
- **Where the tests find their data**: `local_paths.ini` (gitignored; the committed template `local_paths.ini.example` defaults to the sample dataset inside this repository).
  The criteria skip when it points at nothing - **never hardcode a machine path**, and never commit the private file.
- **CI** (`.github/workflows/ci.yml`): the documentation gate, the unit suite on Ubuntu (3.10/3.13) and Windows (3.10), and the `network`-marked criteria weekly.
  A fresh clone is green because the criteria that need this machine's dataset or models skip themselves; `CONTRIBUTING.md` says how to point them at your own.

## Deep-dive map (second-level indexes)

Details are not written in this file. Enter `.github/memory/` by this table; each category has its own topic list:

| Category | Second-level index | Covers | When to enter |
|---|---|---|---|
| scope | [INDEX-scope.md](.github/memory/INDEX-scope.md) | current state, scope, architecture, data contract, frozen contract | **first stop when taking over**; deciding "is this in scope", what is in the dataset directory |
| pipeline | [INDEX-pipeline.md](.github/memory/INDEX-pipeline.md) | thumbnail/export pipeline, cache invalidation | changing image reads/thumbnails/image scaling export/cache consistency |
| tagging | [INDEX-tagging.md](.github/memory/INDEX-tagging.md) | WD14 inference, vocabulary, model download, local model inventory | wiring up a tagger, tuning thresholds, finding where model files are |
| history | [INDEX-history.md](.github/memory/INDEX-history.md) | changelog and historical decisions | wanting to know "why is it like this", the provenance of a value |
| docs | [INDEX-docs.md](.github/memory/INDEX-docs.md) | documentation writing conventions, agent conclusion reporting conventions | writing/changing a memory file, AGENTS.md, or SKILL.md, and before reporting conclusions |

Maintenance rules (the first two are enforced by the gate):

1. This file holds only what is "useful every time", with a 28 KB budget; new details go into topic files.
2. A new topic file must appear as a row in the matching `INDEX-<category>.md` (an orphan file turns the gate red).
3. A new category requires changing `MEMORY.md`, this table, and the maintenance rules in `MEMORY.md`.
4. **A wrong fact is worse than no fact.** A table entry in this file marked "verified" must really have been verified -
   we have already been burned twice (vocabulary row count, the cap on images per subdirectory), both found by subagent review.
