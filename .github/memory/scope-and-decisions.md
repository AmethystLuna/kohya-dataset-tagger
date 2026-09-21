---
name: scope-and-decisions
description: V0.1's seven settled scope decisions and their rationale (including explicit non-goals)
metadata:
  type: topic
---

# V0.1 scope and settled decisions

Settled on 2026-09-17 (process in [changelog/bootstrap](changelog/bootstrap.md)).
**This is the currently effective version**; changing it means changing scope, so sync AGENTS.md.

## The seven decisions

| # | Decision | Rationale / impact |
|---|---|---|
| 1 | **Output form = a `.txt` caption sidecar per image** | kohya standard. No `sample_prompts.txt`-style inference prompt list - that's the trainer's Prompts tab |
| 2 | **The tagger defaults to WD14 v3 ONNX (`wd-swinv2-tagger-v3`), accepting the new `onnxruntime`** | The v3 model file is already on this machine (see [local-model-inventory](local-model-inventory.md)), so no network is needed. **Revised after a 2026-09-17 review**: the WD family has been unmaintained for over two years, and stronger options exist (including the Anima-specific `sorryhyun/anima-tagger`). v3 stays the **baseline default** - not because it's the best, but because any replacement needs it as a control; the upgrade path is in [tagger-model-landscape](tagger-model-landscape.md) |
| 3 | **Danbooru tags only, no VLM natural-language captioning** | The dependency scale is completely different (a VLM needs several more GB of models plus an inference stack). Anima trains on both tags and natural language; do the former first |
| 4 | **Thumbnails: performance first + maximally sharp** | In implementation this means: a resident Python process + `draft()` fast decode + LANCZOS + disk cache + DPR 2x. See [thumbnail-pipeline](thumbnail-pipeline.md) |
| 5 | **Designed for the "a few hundred images" scale** | No extreme virtual scrolling is needed; but the thumbnail disk cache and pagination are still required, otherwise re-decoding 4K PNGs on every refresh stays slow |
| 6 | **No compatibility with non-kohya layouts** | It doesn't handle `metadata.json` / `metadata.jsonl` / the DreamBooth directory-name `N_classname` prefix / multi-line caption semantics. Tolerated on read, but **not** promised as a feature |
| 7 | **A standalone page** | Its own process, its own URL, its own repository. Not a tab of the trainer. See [project-architecture](project-architecture.md) |

## Explicit non-goals (V0.1)

- VLM / natural-language captioner
- **In-place** modification of images (rename, move, delete, scale) - only reads and writes `.txt`.
  **The exception is the §4.14 image export**: it writes scaled **copies** to another directory (together with captions),
  and doesn't touch a single byte of the images in the source dataset (see [image-scaling](image-scaling.md))
- Perceptual-hash deduplication, clustering, aesthetic scoring
- Multi-user / auth / remote access (listens on `127.0.0.1` only)
- A frontend build step (bundler, TS, framework)
- **Semantic** compatibility with non-kohya layouts (reads them fine and edits them correctly, but doesn't promise to understand their special rules)

## Settled (formerly "open" items, closed on 2026-09-17)

| Item | Decision | Notes |
|---|---|---|
| Backend framework | **FastAPI + uvicorn** | SSE and type validation come cheap; already running |
| Port | default **3001**, shifts forward 20 if taken | The trainer uses 3000, the DSH GUI 3080 |
| Python environment | **the repository's own `.venv`** | ⚠️ **Overturns** the earlier idea of "reusing the trainer's venv" |

**Why reusing the trainer's venv was overturned**: P0 of this tool doesn't need torch at all (only onnxruntime + pillow + fastapi,
about 250 MB), and installing packages into a training environment that is in use risks breaking it - **and in fact it did break once**.
One `.\setup_env.bat` builds it, far cheaper than the cost of coupling.

## Settled after the seven (all frozen in [p0-spec](p0-spec.md))

| Matter | Decision | Basis |
|---|---|---|
| Generating dataset.toml | **Yes, and it counts as P0** | The trainer doesn't recurse, so pointing `image_dir` at a parent directory yields **0 images** → one subset per directory is required |
| Model download | yes; HF + **hf-mirror.com** automatic fallback | Usable from China; ModelScope **no** (different paths and naming, and the existing ones are third-party mirrors) |
| GPU | Windows defaults to **DirectML** | Measured 0.192 vs CPU 0.721 s/image; CUDA is only 14% faster yet adds 245 MB + a torch dependency |
| Model location | repository `models/`` (position 0 in the search roots **and** the download destination) | There's a placeholder file there saying "put models here"; that sentence has to be true |
| Extra scan paths | `KOHYA_TAGGER_EXTRA_MODELS` (**appends**) | Distinct from `KOHYA_TAGGER_MODELS` (replaces wholesale) |
| Autocomplete | **substring** matching, prefix hits sorted first | No tag in the vocabulary starts with `genshin`, so prefix matching is unusable |
| Tagger model | keep the WD14 v3 baseline for now | See [tagger-model-landscape](tagger-model-landscape.md) |
| Runtime path changes | yes: roots and model scan directories can be added/removed in the UI, **effective immediately** | §4.11; user feedback was "the web UI won't let me change the root directory" and "I can't find the config entry on the model-scan-directories page" |
| The security boundary of those two endpoints | when listening on `127.0.0.1` only, widening the allowlist is **allowed**; once `--host` faces outward it amounts to handing over directory read/write | Stated at the top of §4.11, and nailed into the hard constraints in AGENTS.md |
| How the two persistent files are written | `roots.txt` is rewritten whole; `model_paths.txt` only has path lines added/removed (the comments are a user asset) | §4.11; test A29 falsifies with "a whole-file rewrite eats comments" |
| Colored logs | the program opens the Windows console VT itself, and `use_colors` gets a boolean instead of `None` | §7; in cmd.exe `isatty()` is true while VT is off → a literal `←[32m` lands on screen |
| Linux launcher | repository-root `start.sh` / `setup_env.sh`, symmetric with the `.bat` files; both support `--dry-run` | §7; this machine has no WSL distro, and `--dry-run` lets the argument assembly be asserted from Git Bash too |

---

Related: [project-architecture](project-architecture.md), [dataset-contract](dataset-contract.md)
