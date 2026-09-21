<h1 align="center">Kohya Dataset Tagger</h1>

<p align="center">
  <a href="README.zh-CN.md">简体中文</a> ·
  <a href="README.md">English</a> ·
  <a href="README.ja.md">日本語</a>
</p>

<p align="center">
  <a href="https://github.com/AmethystLuna/kohya-dataset-tagger/actions/workflows/ci.yml"><img src="https://github.com/AmethystLuna/kohya-dataset-tagger/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License: MIT"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.10%2B-blue.svg" alt="Python 3.10+"></a>
</p>

A standalone dataset tagger for **kohya-style training sets** - LoRA / full finetune / DreamBooth all use the same `image_dir` + `.txt` sidecar format. Do the whole chain in the browser:

**directory browsing → gallery preview → per-image tag editing → tag filtering → batch WD14 captioning → generate a trainable dataset.toml**

It is a **companion tool** to **Anima-Standalone-Trainer** (a sibling project, not published here), not a plugin for it:
its own repository, its own process, its own page, interacting with the trainer only through the filesystem contract (the dataset directory, `dataset.toml`, the training cache).

## Status

**V0.1 · P0 complete**. Backend, frontend, tagger, model download, and dataset.toml generation have all landed and passed the P0 acceptance round.

    pytest test/ -q -n auto -m "not network"   1072 passed, 31 skipped   # a fresh clone: the committed sample dataset, no tagger model
    pytest test/test_docs_index.py -q          9 passed

Every path the tests need comes from `local_paths.ini` (gitignored, with a committed template); the
criteria whose subject cannot exist - a real training set, a downloaded tagger model, the real
vocabulary - **skip themselves and say so**, which is why a fresh clone is green. The acceptance suite is
the independent one and is scored against the trainer's own `config_util`; it stays a local gate rather
than a published number. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Two things it knows that the trainer will not tell you

These two are the reason this tool exists, and what sets it apart from "hand-writing the config":

**① After you change a caption, the trainer does not rebuild the text encoder cache.**

It will **silently keep training with the old tags**, with no error at all.
So this tool's number-one feature is not "editing" but **making the cache right after the edit** -
every write to a `.txt` also deletes the matching `cache_text_encoder/<name>_anima_te.safetensors`,
and if it cannot delete it, it raises an error rather than **swallowing** it.
Evidence and post-mortem: [.github/memory/encoder-cache-invalidation.md](.github/memory/encoder-cache-invalidation.md)

**② The trainer's image enumeration is not recursive.**

`glob_images()` scans only one level, so when `image_dir` points at a parent directory **the training set is empty**.
A training set here is a few hundred subdirectories, so it must be **one subset per directory**.
The generator produces exactly that, and it passes acceptance against **the trainer's own config validator**
([tools/verify_toml_with_trainer.py](tools/verify_toml_with_trainer.py)).

Along the way it reports six classes of problem that the trainer accepts silently: missing captions, a caption containing `\,` (which gets read as two tags),
images larger than `max_bucket_reso`, images with alpha, too few images, and empty directories.

## Quick start

**Two commands.** The first time:

```cmd
setup_env.bat
```

Then every time:

```cmd
start.bat
```

`start.bat` does all of this by itself before opening the browser:

- If there is no `.venv`, it asks whether to initialize first
- The first time it asks once for the **dataset root directory** and stores it in `roots.txt` (gitignored), never asking again
- If the port is taken it moves on automatically (3001 → 3002 → …)
- **It opens the browser only once the service is actually ready**, so you never get a page that cannot connect
- The service runs in the foreground; Ctrl+C ends it

You do not have to use the launcher:

```powershell
.\scripts\start.ps1 -Roots "D:\datasets\my-lora" -Port 3001
.\scripts\start.ps1 -NoBrowser
```

Linux / macOS are **two scripts for the same thing, placed symmetrically at the root**:

```bash
./setup_env.sh       # idempotent; installs the CUDA build of onnxruntime if nvidia-smi exists, otherwise the CPU build
./start.sh           # reads the same roots.txt, picks a free port, opens the browser once the service is ready
```

Both accept `--dry-run`: print the command lines that would be executed and exit without starting anything.

**On mainland-China networks** use the China-mirror profile (the same implementation, only the package source differs):

```cmd
setup_env_cn.bat
```

```bash
./setup_env_cn.sh
```

It falls back through the sources in the order `USTC → Aliyun → pypi.org` (moving to the next only when the previous one fails), so it installs even when a mirror is temporarily down.
Measured (2026-09-19, 245 MB onnxruntime-gpu): USTC **8.4–11 MB/s**, Aliyun 0.86–1.25 MB/s,
pypi.org 0.55–1.01 MB/s. To use a different mirror: set `KOHYA_TAGGER_PIP_INDEX=https://your/simple`
(comma-separated for several, falling back in order).

**The only dependency is a dedicated venv** (about 250 MB), **torch is not needed**, and do not install anything into the trainer's venv.

Other uses of `setup_env.bat` (equivalent to `scripts\setup_env.ps1`):

```powershell
.\setup_env.bat -Gpu cuda        # use the CUDA EP (needs torch or the nvidia runtime)
.\setup_env.bat -Gpu cpu
.\setup_env.bat -Index cn        # China-mirror profile (equivalent to setup_env_cn.bat)
.\setup_env.bat -DryRun          # only print the choices that would be executed, install nothing
.\setup_env.bat -Recreate        # rebuild the venv (prints the absolute path and asks for confirmation first)
```

## GPU

Measured on this machine (RTX 4070 Ti SUPER, 16 real images, median after warm-up):

| provider | median | 1653 images |
|---|---|---|
| CPU | 0.721 s/image | 19.9 minutes |
| **DirectML** (default) | **0.192 s/image** | **5.3 minutes** |
| CUDA | 0.168 s/image | 4.6 minutes |

DirectML is the default on Windows: 50 MB, self-contained, vendor-agnostic, no CUDA/cuDNN needed.
CUDA is 14% faster but costs 195 MB more plus a torch dependency.
**Installing onnxruntime must pin `--index-url` explicitly** (both setup scripts already do this):
falling back to the global pip index can be so slow it "looks hung" (0.07 MB/s measured on this machine). The regular profile uses `pypi.org`,
the China profile (`setup_env_cn.*`) falls back through USTC → Aliyun → pypi.org.

Always use it for the post-install self-check, and **do not** trust `get_available_providers()` -
when a provider fails to load, onnxruntime **silently falls back to CPU**, and inference still runs, just 4× slower:

```powershell
.\.venv\Scripts\python.exe tools\check_provider.py    # builds a session with a real model and reports the **actual** provider
```

## Model download

If a tagger model is not available locally when you select it, it is downloaded automatically. Endpoint order
`KOHYA_TAGGER_HF_ENDPOINT` → `huggingface.co` → `hf-mirror.com` (a China mirror; its manifest has been verified to match the main site word for word).

**The easiest way is to change it in the UI** (see the next section): tagger panel → "Model directory…", and additions **take effect immediately**,
no restart needed, and get written to `model_paths.txt`. Three other equivalent ways:

```powershell
# 1. Write it into model_paths.txt (recommended; one per line, # starts a comment, semicolons also work) - the server reads it at startup
# 2. Environment variable (appends, does not replace):
set KOHYA_TAGGER_EXTRA_MODELS=D:\my-taggers;E:\more
# 3. Run the CLI directly (the launcher has **no** -ExtraModels argument, do not copy old docs):
.\.venv\Scripts\python.exe -m kohya_dataset_tagger --roots "<dataset>" --extra-models "D:\my-taggers"
```

> `scripts\start.ps1` / `scripts\start.sh` only "pick a port + read roots.txt + start the service";
> model search directories are **not forwarded by the launcher** (§3.7) - if they were turned into command-line arguments, a line deleted in the UI
> would be pushed back on the next start.

Note the difference from `--models` / `KOHYA_TAGGER_MODELS`: **that replaces the whole search list** (setting it drops
the repository `models/` and all auto-discovered locations), while **this appends** - "add one more directory" wants the latter.

The **order of search roots is the priority**: repository `models/` → the ones you added (`model_paths.txt`) → the ones we downloaded → the HF cache.
Explicitly specified ones always come before auto-discovered ones.
The default list contains **no** paths that exist only on one machine - wherever your webui / ComfyUI models are, write them into `model_paths.txt`.

**It does not matter if the download will not work**: put `model.onnx` and the `*.csv` from the same directory into the repository's

    models/<model-id>/

(that directory has a placeholder file whose filename is exactly this sentence). It is **position 0 of the default search root**
and also **the download destination** - what you place by hand and what is downloaded automatically live in the same place, with no second copy.

## Switching datasets / adding model directories: no restart

Both kinds of path used to be configurable only through command-line arguments or a config file, **and were read once at startup**. Now both can be changed in the UI, taking effect immediately:

| What you want to do | Where | Effect |
|---|---|---|
| Switch / add a dataset directory | the **+** next to the "Roots" title in the left column | browsable immediately; written back to `roots.txt` |
| Remove a dataset directory | the **×** after each root | the last one cannot be deleted (with none left, nothing opens) |
| Add a model scan directory | tagger panel → "Model directory…" | appears in the model dropdown immediately; written back to `model_paths.txt` |
| Remove a model scan directory | "Remove" on each entry in the same dialog | the auto-discovered ones apply only to this run; the UI says so |

Browsers cannot give you a native directory picker, so **paste an absolute path** (opening a "list any directory" endpoint just for this one control would void the allowlist). The directory must already exist - configuring a nonexistent root only makes every request 403/404.

When writing the file fails the feature **still works**, but the UI says outright "this entry will be lost after a restart" rather than pretending it succeeded.

## Acceptance

`pytest acceptance/` holds criteria **written independently against the spec**, not by the implementer. A23 among them is scored by **the real consumer**:
it feeds the generated `dataset.toml` to the trainer's own `config_util` and requires it to build one subset per directory.

## Development

Read [AGENTS.md](AGENTS.md) first - the resident index pointing into the second-level indexes under `.github/memory/`.
The working procedure is in [.agents/skills/kohya-dataset-tagger-workflow/SKILL.md](.agents/skills/kohya-dataset-tagger-workflow/SKILL.md).

```powershell
.\.venv\Scripts\python.exe tools\check_relevant.py          # inner loop: only the tests that cover the change
.\.venv\Scripts\python.exe tools\check_relevant.py --full   # the gate before reporting: full suite + documentation gate
.\.venv\Scripts\python.exe -m pytest test/ -q -n auto       # the full unit suite, across cores
.\.venv\Scripts\python.exe -m pytest acceptance/ -q         # acceptance criteria (needs a real dataset)
.\.venv\Scripts\python.exe tools\e2e_smoke.py               # real HTTP end to end (requires a running service)
.\.venv\Scripts\python.exe tools\dataset_manifest.py --root "<dataset>" --out snapshot.json   # store your own fingerprint
```

`local_paths.ini` is where a checkout finds its own training set, models and trainer; the committed
template points at the sample dataset under `test/fixtures/sample_dataset/`, so the suite runs as it is.
[CONTRIBUTING.md](CONTRIBUTING.md) explains the keys and lists what the CI jobs run:
[.github/workflows/ci.yml](.github/workflows/ci.yml) covers Linux and Windows, and leaves the criteria
that talk to huggingface.co to a weekly scheduled run.

## Documentation layout

    AGENTS.md                          resident index (≤ 28 KB, gate-enforced)
    .github/memory/MEMORY.md           category table
    .github/memory/INDEX-<category>.md topic lists
    .github/memory/<topic>.md          bodies
    .github/memory/p0-spec.md          **P0 frozen contract and acceptance criteria** (must read before changing code)
    .agents/skills/<name>/SKILL.md     working procedure

`test/test_docs_index.py` keeps this structure from rotting: budget, reachable links, no orphan topics, skill compliance.

## Contributing

Issues and pull requests are welcome, in English or Chinese. [CONTRIBUTING.md](CONTRIBUTING.md) has the
setup, the two-tier gate, the rules a change has to respect, and what CI runs. Security problems go
through [SECURITY.md](SECURITY.md); the project follows the [Contributor Covenant](CODE_OF_CONDUCT.md).

## License

[MIT](LICENSE) © 2026 AmethystLuna.
