---
name: p0-spec
description: P0 frozen interface contract and acceptance criteria - the implementer's only authority
metadata:
  type: topic
---

# P0 Spec and Acceptance Criteria (Frozen)

> **Status: frozen.** Implementers write code against this file. Changing this file means changing the
> contract, so the acceptance criteria must change at the same time.
> Facts verified on: 2026-09-17. Related: [dataset-contract](dataset-contract.md),
> [encoder-cache-invalidation](encoder-cache-invalidation.md), [thumbnail-pipeline](thumbnail-pipeline.md),
> [wd14-tagger](wd14-tagger.md), [local-model-inventory](local-model-inventory.md).

---

## Contents (in the order they **actually appear** in the document)

> **Section numbers follow the order they were appended, not the document order** - §3.9 sits after §4.8
> because it was added later, and §4.10 sits before §4.9 for the same reason.
> Everywhere these numbers are cited (AGENTS.md, skills, the topic files) goes by number, so **do not
> renumber**; use the table below to locate things.

| Section | Content |
|---|---|
| §0 | Hard boundaries (violating them fails acceptance) |
| §1 / §1.1-§1.3 | Dataset facts (verified), **the trainer does not recurse**, smoke measurements, filenames are not unique across directories |
| §2 | The frozen file tree |
| §3.0 | Module dependency direction (hard requirement) |
| §3.1-§3.7 | Frozen public signatures per module (paths / captions / cache_invalidation / thumbs / vocab / tagger / config) |
| §4 / §4.1-§4.5 | HTTP contract + filled-in value ranges (thresholds, has_caption, SSE, cancel, preview limit, etc.) |
| **§3.9** | **Contract for generating dataset.toml** (one subset per directory; escaping requirements) |
| §4.6-§4.8 | Model download: requirements and endpoints, HTTP surface, implementation contract |
| §4.10 | Autocomplete switches to **substring** matching |
| §4.9 | HTTP surface for exporting dataset.toml |
| **§4.11** | **Runtime-editable path configuration** (roots / model search directories) - requirement added 2026-09-17 |
| §4.12-§4.13 | Graphical directory picker + loopback gate; the folder nine-grid preview |
| §4.14 | Dataset image scaling/export (area normalization into a target directory, background job + SSE) |
| §4.15-§4.16 | Scroll memory for directory navigation; the dataset root's `dataset.toml` as a read-only fact |
| **§4.17** | **Image cropping**: `POST /api/crop/apply` - crop one image, archive it beside the source (`_1`/`_2`…), the per-image confirmation dialog and its inheritance rules - requirement added 2026-09-20 |
| **§5** | **Acceptance criteria A1-A32** (run `acceptance/`) |
| §6 | Division of labor and file ownership (for avoiding conflicts in parallel workflows; skippable when one person edits the code) |
| §7 | Environment, GPU (DirectML by default), benchmark methodology, out-of-band lessons |

---

## 0. Hard Boundaries (violating them fails acceptance)

1. The dataset configured in `local_paths.ini` - the committed sample dataset under `test/fixtures/sample_dataset/` when it is unset - is a **read-only test set**.
   No code path may create, modify, delete, or rename anything in it - **including temporary files,
   cache directories, and the `__pycache__` produced on import**.
2. Tests that need to write **always copy into a temporary directory first** (`tmp_path` or a scratch
   dir inside the repo); the assertion target is the copy.
3. The thumbnail cache directory **must be outside the dataset directory**.
4. The read-only boundary has two criteria, **do not conflate them**:
   - **Relative invariant (automated gate = §5 A1)**: before acceptance starts, take a fingerprint with
     `python tools/dataset_manifest.py --root "<dataset>" --out <scratch>`; at the end of the session,
     `--check` **the same file**. A mismatch = some code path wrote to the read-only test set.
     It holds for any data state; since 2026-09-19 it replaces the old "compare against a snapshot" wording.
   - **Snapshot check (manual)**: `python tools/dataset_manifest.py --root "<dataset>" --check <file>` against a manifest snapshot you
     generate yourself with `tools/dataset_manifest.py --out <file>`.
     The baseline was **re-collected on 2026-09-19 at the user's request** (the previous one was from
     2026-09-17): `file_count` = 4959 (the original 3306 entries + 1653 caption backups `.txt.bak`),
     `total_bytes` = 13382190161, `manifest_sha256` = `fbf67b54f3a5155d…`.
     **The dataset is alive** - the user adding images or fixing captions is legitimate, so a snapshot
     will inevitably go stale, which is why it **is no longer an automated gate**; re-collect with `--out`
     before comparing against it.

   Why it was re-collected twice (both user-approved caption fixes):
   ① removing a stray backslash in the 6 files of `1_sample_alpha` (-1 byte each);
   ② escaping the **closing** parenthesis of the same tag (+1 byte each) - the two cancel out, the total
   byte count returned to its original value, but the sha256 already differs. See
   [changelog/caption-defect-fix](changelog/caption-defect-fix.md).
   **Dataset content changes through legitimate fixes/additions** - so the automated gate asserts only the
   relative invariant "this test did not write to it"; any assertion that tracks a snapshot will
   false-alarm after every legal change (measured 2026-09-19).

---

## 1. Dataset Facts (verified, they drive the design)

| Fact | Value | Source |
|---|---|---|
| Structure | **219 post subdirectories**, each with **1-25** images (originally written 1-20, measured max 25 - a wrong fact table should be corrected) | measured |
| Images | 1629 `.jpeg` + 10 `.jpg` + 14 `.png` = **1653** | measured |
| captions | **1653 `.txt`**, 1:1 with the images | measured |
| Total size | 12.46 GiB | measured |
| caption shape | danbooru tags, comma+space separated, single line | sampling |
| Image dimensions | median 3000×3500, max 6000×7000 | 60-image sample |
| Image size | median 3.64 MB, max 30.92 MB; 21/60 have a long edge > 4096 | 60-image sample |
| Filenames | `1.jpeg` / `2.jpeg`… inside a directory, **not unique across directories** | measured |

### 1.1 The single most important fact: the trainer does not recurse

`glob_images(directory, base)` at `library/train_util.py:3059` uses
`glob.glob(os.path.join(directory, base + ext))` - it **scans a single level only**.
The call sites `:2062` and `:2173` both pass `subset.image_dir`. Measured:

    glob_images(sample_dataset)                 -> 0 images
    glob_images(sample_dataset\1_sample_alpha)      -> 3 images

**So when `image_dir` points at a parent directory the training set is empty.**

**How the generated dataset.toml reaches the trainer** (the call chain was traced on 2026-09-17 and confirmed workable):

    anima_train_network.py:541  train_network.setup_parser()
      -> train_util.add_dataset_arguments()          # the standard kohya argument chain
        -> --dataset_config <path>                   # this very [[datasets]] TOML
          -> config_util.load_user_config() -> BlueprintGenerator

`anima_train_network.py` itself never mentions `dataset_config` - it delegates to
`train_network.setup_parser()`, so the argument **exists all the same**. Do not assume this fork does not
support it just because "grep finds nothing". The other route is what the training UI does:
`[dataset_arguments].dataset_config` inside `--config_file job.toml` points at the same file.

**Conclusion**:

- **Multi-directory support is P0**, not P2.
- The editor must be able to list subdirectories, count the images in each, and generate a
  "**one subset per directory**" `dataset.toml`.
- `glob_images_pathlib(dir_path, recursive)` (`:3071`) exists, but **nothing in the codebase calls it**;
  it is dead code, do not count on it.

### 1.2 Smoke measurements (2026-09-17, by a one-off probe that is not kept in the repository)

Measured on this machine with `wd-swinv2-tagger-v3` + onnxruntime 1.23.2; **the numbers below are the
baseline for the criteria**:

| Item | Measured value |
|---|---|
| ONNX input | `input` `['batch_size', 448, 448, 3]` `tensor(float)` - confirms **NHWC + dynamic batch** |
| Session load | 1.5 s |
| Single-image inference | **0.47 s / image (CPU)** → about 13 minutes for 1653 images |
| Available providers | `CPUExecutionProvider` (this machine's venv has **no CUDA**) |
| Vocabulary | **10861** lines total = general 8106 + character 2751 + rating 4 |
| rating rows | first 4 rows: `general, sensitive, questionable, explicit` |
| `preprocess` output | `(448, 448, 3) float32`, max **255.0** |
| Pad value | white 255 ✓ |

> 2026-09-19 correction: this measurement ran against a plain onnxruntime build. The venv now installs
> `onnxruntime-directml` by default, so `DmlExecutionProvider` is available too; `DEFAULT_PROVIDERS` includes it
> (CUDA -> DirectML -> CPU), and the shipped tagger really uses it - see
> [changelog/tagger-gpu-provider](changelog/tagger-gpu-provider.md).

**Two measurement traps that must be written down**:

1. **Do not verify BGR with a padded rectangle.** A 100×300 solid-red image padded to a square is 2/3
   white border, and the B-channel mean measures **170.4**, not 0. To assert channel order you must use a
   **square** image (no padding).
2. **The model outputs underscores, the dataset captions use spaces.** The model measured gives
   `raiden_shogun`, while the caption for that image in the dataset reads `raiden shogun`.
   **`remove_underscore` is not optional, it must default to on** - otherwise the captions the tagger
   writes do not match the existing captions.

(The measured overlap between the top-12 tags and the existing caption is 6/12; the existing caption has
45-49 tags and was hand-refined, so the low overlap is expected and does not mean the model is wrong.)

### 1.3 Filenames are not unique across directories

`1.jpeg` exists in all 219 directories. **Every cache key and every UI identity must use the absolute
path**, never just the filename.

---

## 2. The Frozen File Tree

    pyproject.toml                          # A
    requirements.txt                        # A
    src/kohya_dataset_tagger/
    ├── __init__.py                         # A   __version__
    ├── __main__.py                         # D   CLI entry point
    ├── app.py                              # D   FastAPI assembly + dynamic discovery of api/
    ├── config.py                           # A   runtime configuration
    ├── core/
    │   ├── __init__.py                     # A
    │   ├── paths.py                        # A   ★ single source of truth for the path allowlist
    │   ├── captions.py                     # B   ★ caption read/write
    │   ├── cache_invalidation.py           # B   ★ invariant number one
    │   ├── thumbs.py                       # A   thumbnail pipeline
    │   └── vocab.py                        # B   vocabulary
    ├── autotag/
    │   ├── __init__.py                     # C
    │   ├── base.py                         # C   Tagger protocol
    │   ├── registry.py                     # C   model discovery
    │   ├── postprocess.py                  # C   post-processing (remove underscores / block list / sort / replace)
    │   └── wd14.py                         # C   ★ WD14 v3 ONNX
    ├── console.py                          # D   console color decision (§7 "Console and ANSI")
    ├── api/
    │   ├── __init__.py                     # A
    │   ├── fs.py                           # A   directory browsing + statistics
    │   ├── roots.py                        # D   ★ §4.11 runtime-editable allowlist
    │   ├── pick.py                         # D   §4.12 graphical directory picker (list dirs only + create dir, loopback gate)
    │   ├── images.py                       # A   thumbnails / metadata
    │   ├── captions.py                     # D
    │   ├── tags.py                         # D
    │   ├── autotag.py                       # D
    │   ├── dataset.py                      # D   §4.9 export
    │   └── scale.py                        # D   §4.14 image scale/export (job + SSE)
    ├── core/
    │   ├── dataset_toml.py                 # A   §3.9 generator
    │   └── scaler.py                       # D   §4.14 area-normalized scaling (leaf)
    ├── autotag/
    │   └── download.py                     # C   §4.8 model download
    └── web/                                # E   build-free frontend
        ├── index.html
        ├── css/app.css
        └── js/
            ├── strings.js                  # the only file containing Chinese: all UI copy
            ├── api.js                      # §4 single source of truth for the route table + mapLimit
            ├── gallery.js                  # grid / multi-select / rubber-band / keyboard / bounded thumbnail queue
            ├── tags.js                     # chip editing / autocomplete / save on blur
            ├── filter.js                   # three-state filter + frequency table
            ├── autotag.js                   # model / thresholds / diff preview / SSE
            ├── settings.js                 # §4.11 edit panel for roots and model scan directories
            ├── picker.js                   # §4.12 dataset directory picker (modal)
            ├── zoom.js                     # double-click zoom preview: image + side-by-side tag editing
            ├── scale.js                    # §4.14 image scale/export panel (job + SSE)
            ├── trainparams.js              # §4.14 shared source of truth for resolution and caption extension
            └── app.js                      # assembly
    test/                                   # each writer owns their own
    tools/dataset_manifest.py               # already exists (for acceptance)

★ = core risk points of this spec.

> The tree was refreshed once on 2026-09-17: `console.py` / `api/roots.py` / `api/dataset.py` /
> `core/dataset_toml.py` / `autotag/download.py` / `web/js/settings.js` were all added after wave 1
> under §3.9, §4.6-§4.9, §4.11, so they are not in the §6 division-of-labor table.
> **Added again on 2026-09-18**: `api/pick.py` / `web/js/picker.js` (§4.12 graphical directory picker)
> and `web/js/zoom.js` (double-click zoom preview + side-by-side tag editing, reusing the editor
> instance from `tags.js`).
> **Added again on 2026-09-19**: `api/scale.py` / `core/scaler.py` / `web/js/scale.js` /
> `web/js/trainparams.js` (§4.14 image scale/export, ported from the standalone `image_scaler_gui.py`).

---

## 3. Frozen Public Signatures

### 3.0 Module Dependency Direction (hard requirement)

So that the three parallel workstreams do not block each other, `core/` is constrained internally:

    api/*  ──→  core/*  ──→  (none)
    api/*  ──→  autotag/*

- `core/paths.py`, `core/thumbs.py`, `core/vocab.py`, `core/cache_invalidation.py` are **leaves** and
  **must not import each other**.
- **The only allowed intra-core dependency**: `core/captions.py` → `core/cache_invalidation.py` (same owner).
- `core/*` must not import `autotag/*`, and must not import FastAPI.
- `autotag/*` must not import `core/*` - it only consumes `Path` and `PIL.Image`.

The root `conftest.py` already puts `src/` on `sys.path`, so tests can just
`import kohya_dataset_tagger.core.paths` without `pip install -e .`.
(A missing `__init__.py` does not block it: Python 3.3+ implicit namespace packages import fine.)

### 3.1 `core/paths.py` (A)

    IMAGE_EXTS: frozenset[str]                  # {".png",".jpg",".jpeg",".webp",".bmp"} including uppercase
    CAPTION_EXT_DEFAULT = ".txt"
    CACHE_DIRNAMES: frozenset[str]              # {"cache_text_encoder", "latent_cache"}

    class PathOutsideRoots(ValueError): ...

    def configure(roots: Sequence[str | Path]) -> None
    def get_roots() -> tuple[Path, ...]
    def resolve_under_roots(raw: str | Path, *, must_exist: bool = True) -> Path
    def is_cache_or_hidden(name: str) -> bool
    def iter_images(directory: Path, *, recursive: bool = False) -> list[Path]
    def list_subdirs(directory: Path) -> list[Path]
    def count_images(directory: Path, *, recursive: bool = False) -> int

**`resolve_under_roots` must do a prefix assertion after resolution** (call `Path.resolve()` first, then
compare `os.path.commonpath`), rejecting `..`, symlink escapes, and absolute paths outside the roots.
This is the step missing from the trainer's `server.js:1800`.

### 3.2 `core/captions.py` (B)

    SEPARATOR = ", "

    @dataclass(frozen=True)
    class WriteResult:
        caption_path: Path
        changed: bool
        cache_removed: tuple[Path, ...]
        cache_remove_failed: tuple[Path, ...]
        multiline_warning: bool

    def caption_path(image: Path, ext: str = CAPTION_EXT_DEFAULT) -> Path
    def read_caption(image: Path, ext: str = CAPTION_EXT_DEFAULT) -> tuple[str, bool]
    def split_tags(text: str) -> list[str]
    def join_tags(tags: Sequence[str]) -> str
    def normalize_for_write(tags: Sequence[str]) -> str
    def write_caption(image: Path, tags: Sequence[str] | str, *,
                      ext: str = CAPTION_EXT_DEFAULT,
                      invalidate: bool = True) -> WriteResult

**Splitting rule (frozen, see §5 A6)**: `split_tags(text)` must match **exactly what the trainer reads** -
`[t.strip() for t in text.strip().split(",") if t.strip()]`, with **no unescaping whatsoever**.
`join_tags(tags) = ", ".join(tags)`, and it **raises ValueError** if any tag contains `,`.

Why no comma escaping: the trainer splits on **bare commas** (`train_util.py:900`). If a tag containing a
comma were escaped to `a\,b` on disk, the trainer would read it as the two tags `a\` and `b` - **silent
data corruption**. What the editor displays must equal what the trainer will read, otherwise the user
cannot see the problem they actually need to fix. `\(` `\)` are always preserved as-is (measured:
**1049 files** in the dataset use them, e.g. `hu tao \(genshin impact\)`).

**Invariant**: when `write_caption` sees a content change and `invalidate=True`, it **must** call
`cache_invalidation.invalidate_text_encoder_cache(image)` and put the result into `WriteResult` verbatim.
A failed deletion **must not be swallowed** - record it in `cache_remove_failed` and let the caller raise.

`normalize_for_write(tags)` (added 2026-09-18) = `strip` / drop empties → reject commas → `join_tags`; it
is the **only** implementation of "tag list → writable text" and is shared by `write_caption` and the
batch `dry_run`. Rationale: if the preview re-implemented this rule, it would report a comma-containing
tag as "writable" while the real write rejects it.

Writes use an **atomic write**: a temporary file in the same directory → `os.replace`; the temporary file
must be created in the caption's directory (same volume) and **must not be left behind**.

`split_tags` / `join_tags` must round-trip: for tags containing spaces and parentheses such as
`["a b", "c\(d\)", "1girl"]`, `join→split` yields the same result.

### 3.3 `core/cache_invalidation.py` (B)

    TE_CACHE_DIRNAME = "cache_text_encoder"
    LATENT_CACHE_DIRNAME = "latent_cache"
    TE_CACHE_SUFFIXES = ("_anima_te.safetensors", "_anima_te.npz")

    @dataclass(frozen=True)
    class InvalidationResult:
        removed: tuple[Path, ...]
        failed: tuple[Path, ...]

    @dataclass(frozen=True)
    class StaleEntry:
        image: Path
        caption: Path
        cache: Path

    def text_encoder_cache_paths(image: Path) -> list[Path]
    def invalidate_text_encoder_cache(image: Path) -> InvalidationResult
    def latent_cache_paths(image: Path) -> list[Path]
    def invalidate_latent_cache(image: Path) -> InvalidationResult
    def find_stale_encoder_caches(image_dir: Path, *, recursive: bool = False) -> list[StaleEntry]

The path rules **copy the trainer** (`library/strategy_anima.py:284-293`):
`<image_dir>/cache_text_encoder/<image_stem>_anima_te.safetensors`, plus the same-name `.npz` variant
(`:299-300` reads both paths).

`find_stale_encoder_caches`: an entry counts as stale when the caption's mtime is **strictly later than**
the cache file's mtime.

### 3.4 `core/thumbs.py` (A)

    GRID_THUMB = 320
    PREVIEW_THUMB = 1600
    PIPELINE_VERSION = 1
    DEFAULT_CACHE_DIRNAME = ".kohya-dataset-tagger-thumbs"

    def cache_key(image: Path, target: int) -> str
    def ensure_thumb(image: Path, target: int, cache_root: Path) -> Path
    def clear_cache(cache_root: Path) -> int

- `cache_key = sha1(f"{abs_path}|{mtime_ns}|{size}|{target}|{PIPELINE_VERSION}")[:16]`
- **JPEG must be `draft()`ed first**, then `exif_transpose`, then `thumbnail(..., LANCZOS)`
- Output WebP, **preserving alpha** (when alpha is present it must not be flattened to opaque)
- The cache directory is **never allowed** inside the dataset directory (validated via `paths`)

### 3.5 `core/vocab.py` (B)

    @dataclass(frozen=True)
    class TagInfo:
        name: str
        category: str
        count: int

    DANBOORU_ORDER: tuple[str, ...]   # ("Rating","Meta","Quality","General","Character","Copyright","Artist")

    class Vocabulary:
        @classmethod
        def from_csv(cls, path: Path) -> "Vocabulary"
        def __len__(self) -> int
        def __contains__(self, name: str) -> bool
        def get(self, name: str) -> TagInfo | None
        def search(self, prefix: str, limit: int = 50) -> list[TagInfo]
        def order_key(self, name: str) -> tuple

CSV column names are `tag_id,name,category,count`; category is a **numeric string**
(0=General 1=Artist 3=Copyright 4=Character 5=Meta 9=Rating).
In the HF cache the file is called `selected_tags.csv`, in ComfyUI `wd-vit-tagger-v3.csv` - **discover by
`*.csv` in the same directory, do not hardcode the filename**.

### 3.6 `autotag/` (C)

    # base.py
    @dataclass(frozen=True)
    class TagScore:
        tag: str
        score: float
        category: str

    class Tagger(Protocol):
        name: str
        def predict(self, image: Path) -> list[TagScore]: ...
        def predict_batch(self, images: Sequence[Path]) -> list[list[TagScore]]: ...

    # registry.py
    @dataclass(frozen=True)
    class ModelSpec:
        id: str
        onnx_glob: str        # glob relative to search_root
        vocab_glob: str
        input_size: int
        description: str

    BUILTIN: dict[str, ModelSpec]
    def resolve(model_id: str, search_roots: Sequence[Path]) -> tuple[Path, Path]
    def discover(search_roots: Sequence[Path]) -> list[str]

    # wd14.py
    class WD14Tagger:
        def __init__(self, onnx_path, vocab_path, *, providers=None,
                     input_size=448, intra_op_num_threads=None): ...
        def predict(self, image: Path) -> list[TagScore]: ...
        def predict_batch(self, images) -> list[list[TagScore]]: ...

    def select_providers(requested, available) -> list[str]

    def preprocess(image: "PIL.Image.Image", size: int = 448) -> np.ndarray

**`preprocess` is the easiest piece in this spec to get wrong** and must match sd-scripts character for
character (`finetune/tag_images_by_wd14_tagger.py:37-64`):

1. alpha → composite onto white (`(255,255,255)`)
2. RGB→**BGR** (`arr[:, :, ::-1]`)
3. center-pad to a square with **255**
4. resize to `size`
5. `astype(np.float32)`, **no division by 255**
6. layout **NHWC** = `(H, W, 3)`

**`intra_op_num_threads` (added when thawed on 2026-09-17)**: passed to
`onnxruntime.SessionOptions.intra_op_num_threads`. The default `None` = keep the ORT default.

**Provider order (added 2026-09-19)**: `wd14.DEFAULT_PROVIDERS` is CUDA -> DirectML -> CPU, filtered by
`select_providers(requested, available)` against `ort.get_available_providers()`. DirectML is what the Windows
setup scripts install, so the shipped default now runs on the GPU without anyone passing `providers=`.
**The provider really in effect is `WD14Tagger.providers` (= `InferenceSession.get_providers()`), never the
requested list.** Before this, the list was CUDA + CPU, so on a DirectML install the tagger silently ran on
CPU (0.75 s/image instead of 0.20) - see [changelog/tagger-gpu-provider](changelog/tagger-gpu-provider.md).

Workstream C measured this on a 16-logical-core machine **running training at the same time**:

| Setting | Inference time |
|---|---|
| default (uses every core) | 3.45 s/image |
| `intra_op_num_threads=4` | **1.39 s/image** |
| `intra_op_num_threads=1` | 2.75 s/image |

**A 2.5× difference, and "training is running" is the normal state on this machine**, so this knob must
exist. Batch budget (1653 images): about 22 minutes when quiet; 68-88 minutes under heavy load.
Also note: **on CPU larger batches are slower** (0.45 → 0.92 s/image), so batch jobs should infer one
image at a time rather than relying on a large batch for speed.

### 3.7 `config.py` (A)

    @dataclass
    class Config:
        roots: tuple[Path, ...]          # allowlist of roots that may be browsed
        thumb_cache_dir: Path            # default <user cache>/kohya-dataset-tagger-thumbs
        model_search_roots: tuple[Path, ...]
        host: str = "127.0.0.1"
        port: int = 3001
        default_caption_ext: str = ".txt"

    def load(..., extra_model_roots=None) -> Config
    # environment variables: KOHYA_TAGGER_ROOTS / KOHYA_TAGGER_THUMB_CACHE / KOHYA_TAGGER_MODELS / KOHYA_TAGGER_EXTRA_MODELS

**The order of the model search roots is the priority** (for a duplicate model id the first hit wins),
fixed on 2026-09-17:

| # | Source | Why it sits here |
|---|---|---|
| 0 | repo `models/` | the directory shown to users (it holds placeholder files saying "put models here"), part of the promise |
| 1.. | `extra_model_roots` / `KOHYA_TAGGER_EXTRA_MODELS` | **explicitly specified by the user**, and should override auto-discovered things |
| - | `%LOCALAPPDATA%/kohya-dataset-tagger/models` | our own download directory (the old location, still recognized) |
| last | HF cache | also "happened to be there"; it used to rank 1 and would shadow models the user had placed |

**The default list must not contain paths that "only exist on one machine"** (tightened 2026-09-17).
There used to be a fourth tier here, "known ComfyUI / webui locations on this machine" - two hardcoded
directories outside the repository. It tied product code to one machine: on another machine it only adds two
`exists: false` noise entries, and on Linux those two are not even the name of an existing relative file.
**Deleted**; those two paths are now supplied by the repo-root `model_paths.txt` (a gitignored local file,
editable from the UI too, see §4.11). Criterion: `test/test_extra_models.py` requires that the default
roots may only live **inside the repo** or in the **current user's cache area**; third-party directories
go through that file.

**Semantic difference between `KOHYA_TAGGER_MODELS` and `KOHYA_TAGGER_EXTRA_MODELS`** (this difference is
the reason the latter exists):

- `KOHYA_TAGGER_MODELS` (`--models`) = **replaces the whole** search list;
- `KOHYA_TAGGER_EXTRA_MODELS` (`--extra-models`) = **appends** to the list.

Both can be used at once: the former gives the complete list, the latter adds a few more at the end. The
repo-root `model_paths.txt` (one per line, `#` starts a comment, semicolons are also accepted) follows the
same convention as `roots.txt`.

**Added 2026-09-17: `model_paths.txt` is now read by the server itself, no longer forwarded by the
launcher.** Rationale in §4.11 - it has to be editable at runtime. When both sources are present **the
file wins** (`pinned = what the CLI gave - what is in the file`), otherwise "delete a line from the UI"
gets pushed back by the CLI argument on the next start.

### 3.7.1 Runtime-editable Fields (added 2026-09-17, used by §4.11)

    @dataclass
    class Config:
        # ...the first 6 fields are unchanged; the ones below are **appended** at the end
        # (positional construction stays compatible)
        roots_file: Path | None = None           # default <repo>/roots.txt
        model_paths_file: Path | None = None     # default <repo>/model_paths.txt
        explicit_model_roots: tuple[Path, ...] | None = None   # --models / KOHYA_TAGGER_MODELS
        pinned_model_roots: tuple[Path, ...] = ()              # --extra-models / KOHYA_TAGGER_EXTRA_MODELS
        file_model_roots: tuple[Path, ...] = ()                # read from model_paths.txt
        excluded_model_roots: frozenset[str] = frozenset()     # auto-discovered roots deleted in this run
        environ: Mapping[str, str] | None = None               # used when rebuilding the search list; tests can inject it

    KOHYA_TAGGER_ROOTS_FILE / KOHYA_TAGGER_MODEL_PATHS_FILE        # override the location of the two files above
    --roots-file / --model-paths-file                      # same (CLI)

New fields are **always appended at the end**: `Config` is positionally constructed (the field order in
§3.7 is cited by tests and docs), and inserting in the middle silently changes the meaning of existing
calls. Same for `dataclasses.replace` in `app.py`.

`model_search_roots` remains a **computed result**, it is just recomputable now:

    rebuild_model_roots(cfg) =
        (explicit_model_roots or default_model_search_roots(environ, file + pinned))
        minus excluded_model_roots, then deduplicated

**The five values of `source` are the product of this rewrite** (used by §4.11's
`GET /api/autotag/model-roots`): `repo` = the repo `models/`, `file` = `model_paths.txt` (persistently
deletable), `cli` = `--extra-models` (valid for this run), `explicit` = `--models`, `auto` =
auto-discovery (deletable, but only takes effect for this run).

---

## 4. HTTP Contract

Uniform prefix `/api`. Errors are always `{"error": {"code": str, "message": str, "params": {...}}}` plus
an appropriate 4xx/5xx. `params` is the **variable data** added on 2026-09-19 (may be an empty object,
see §4.9 supplement 4); the two old fields `code` / `message` are unchanged. **Every path parameter goes
through `resolve_under_roots`**, and an out-of-bounds path returns 403.
**The only exception is §4.12's `GET /api/fs/pick`**: by design it must be able to list directories
**outside** the allowlist (the loopback gate is its boundary). Always ask this question first for a new
endpoint.

**How the contract evolves (rule established 2026-09-19)**

The P0 contract is the implementer's only authority, but it is not unchangeable stone. Only three things
are allowed, and **any of them must change `p0-spec.md` and `acceptance/` together** (this repo's hard
rule: changing only one side decouples the criteria from the contract):

1. **Addition (the default)**: add fields only, the old fields stay **byte-for-byte unchanged**. Write it
   into this section and add an acceptance criterion.
2. **Deprecation**: mark the field "transitional only" and also provide two things - a **single source of
   truth** (two places maintaining the same wording is forbidden) and a **removal condition** (what
   evidence permits deletion). New code must not read it during the deprecation.
3. **Removal (breaking)**: allowed only when **all consumers have stopped reading it** and `acceptance/`
   is rewritten in the same batch. The consumers in this repo are the in-repo frontend, and front and
   back end are upgraded in the same change - **"there are no external consumers" is the normal state,
   do not use compatibility as a reason to keep a dead field**.

How to choose: **when a human-facing natural language sentence appears in the contract, an abstraction
layer is missing.** The API returns only "data + a stable code + interpolatable params"; wording belongs
to the frontend (where localization lives: see [i18n](i18n.md)). The 2026-09-19 `warnings` / `skipped`
are an example: they are a **degraded fallback** retained under the addition rule, while the source of
truth is already the structured fields.

| Method | Path | Request | Response |
|---|---|---|---|
| GET | `/health` | — | `{ok, version, path_config:{allowed,host,reason}}` (`path_config` added in §4.12) |
| GET | `/roots` | — | `{roots:[{path,name,exists,is_dir,persisted}]}`, see §4.11 |
| POST | `/roots` | `{path}` | 200 `{roots,changed,persisted,warning}`, see §4.11 |
| DELETE | `/roots` | `?path=` | 200 `{roots,removed,persisted,warning}`, see §4.11 |
| GET | `/fs/list` | `?path=&recursive=` | `{path, parent, dirs:[{name,path,image_count,preview_images}], images:[{name,path,size,mtime}], counts:{images,dirs,captions,missing_captions}}` (`preview_images` added in §4.13) |
| GET | `/fs/pick` | `?path=` | `{path, parent, dirs:[{name,path}], roots:[str], home}`, **lists directories only**, see §4.12 |
| POST | `/fs/pick/mkdir` | `{parent, name}` | 200 `{path, created}`; creates one level only, idempotent for an existing directory (`created:false`), 409 `already_exists` for an existing file, see §4.12 |
| GET | `/images/thumb` | `?path=&size=grid|preview` | WebP bytes |
| GET | `/images/meta` | `?path=` | `{path,width,height,display_width,display_height,bytes,mtime,mode,has_alpha}` (`display_*` added in §4.17: the size **as shown**, EXIF orientation applied) |
| GET | `/captions` | `?path=` | `{image,caption_path,exists,text,tags,multiline_warning}` |
| PUT | `/captions` | `{image, tags?, text?}` | WriteResult expanded |
| POST | `/captions/batch` | `{images, op, …}` | `{applied, failed, results}`; with `dry_run:true` it only previews and writes nothing |
| POST | `/captions/read` | `{images}` | `{results}` - many captions in one round trip (the GET /captions entry plus `ok`), see §4.5 supplement |
| POST | `/captions/batch/run` | `{images, op, …}` | 202 `{job_id,total}` - the job form of the same op, see §4.5 supplement |
| GET | `/captions/batch/stream` | `?job_id=` | SSE: one `progress` per image, a final `done{finished:true}`, see §4.5 supplement |
| POST | `/captions/batch/cancel` | `{job_id}` | 202 `{cancelled: bool}`, see §4.5 supplement |
| POST | `/captions/restore` | `{images}` | `{restored, failed, results}` - put each `<caption>.bak` back and consume it, see §4.5 supplement |
| GET | `/tags/frequency` | `?path=&recursive=` | `{total_images, tags:[{tag,count}]}` |
| GET | `/tags/autocomplete` | `?q=&limit=` | `{suggestions:[{tag,category,count}]}`, **substring matching**, see §4.10 |
| GET | `/autotag/models` | — | `{models:[{id,available,installed,size_bytes,download_size_bytes,downloadable,onnx_path,vocab_path,description}]}` (details in §4.7) |
| GET | `/autotag/model-roots` | — | `{roots:[{path,source,exists,is_dir,file_backed,models,model_count}]}`, see §4.11 |
| POST | `/autotag/model-roots` | `{path}` | 200 `{roots,changed,persisted,warning}`, see §4.11 |
| DELETE | `/autotag/model-roots` | `?path=` | 200 `{roots,removed,removed_scope,persisted,warning}`, see §4.11 |
| POST | `/autotag/preview` | `{images, model, thresholds, postprocess}` | `{items:[{image,before,after,tags:[{tag,score,category}]}]}` |
| POST | `/autotag/run` | as above + `write_mode` | `{job_id}` |
| GET | `/autotag/stream` | `?job_id=` | SSE, events and end signal in §4.3 |
| POST | `/autotag/cancel` | `{job_id}` | 202 `{cancelled: bool}`, see §4.4 |
| GET | `/cache/status` | `?path=&recursive=` | `{stale:[{image,caption,cache}]}` |
| POST | `/cache/invalidate` | `{images}` | `{removed:[],failed:[]}` |
| POST | `/cache/invalidate/run` | `{images}` | 202 `{job_id,total}`, see §4.5 supplement |
| GET | `/cache/invalidate/stream` | `?job_id=` | SSE: one `progress` per image, a final `done{finished:true}`, see §4.5 supplement |
| POST | `/cache/invalidate/cancel` | `{job_id}` | 202 `{cancelled: bool}`, see §4.5 supplement |
| POST | `/scale/run` | `{root,target_dir,target_width,target_height,no_upscale,resample,output_format,quality,optimize,caption_extension,write_dataset_toml,overwrite_dataset_toml}` | 202 `{job_id,total}`, see §4.14 |
| GET | `/scale/stream` | `?job_id=` | SSE: one `progress` per image, a final `done{finished:true}`, see §4.14 |
| POST | `/scale/cancel` | `{job_id}` | 202 `{cancelled: bool}`, see §4.14 |
| POST | `/crop/apply` | `{src,crop:{x,y,width,height},format,scale?}` | 200 `{src,archived,format,crop,crop_requested,orig_width,orig_height,crop_width,crop_height,output_width,output_height,scale,resampled,upscale_skipped,copied,warnings}`, see §4.17 |
| GET | `/dataset/toml/status` | `?root=` | `{root,path,exists,mtime,size}` - whether the **dataset root** has the file, by one `stat`; never creates it, never returns its contents, see §4.16 |

`op` supports: `replace` / `append` / `remove` / `regex_replace` / `normalize` / `sort_danbooru`.
`write_mode` (**defaults to `backup_overwrite` when omitted**): `backup_overwrite` / `overwrite` /
`append` / `prepend` / `only_if_empty` / `merge`.


#### 4.5 supplement: the job form of the batch write and the cache rebuild (2026-09-19)

Both operations are one POST over however many images the user selected. That is right for the dry-run
preview (instant, and the diff must not hide behind a stream) and wrong for a thousand-image write:
no progress, and nothing to stop. The synchronous endpoints above stay **unchanged**; the job form is
**additive** and follows §4.14's shape exactly.

- `POST /captions/batch/run` with `dry_run:true` is **400 `bad_request`**: the job form always writes.
  The preview stays `POST /captions/batch`.
- Every `progress` event carries the same per-image entry the synchronous endpoint returns in
  `results`, in `{phase, done, total, current, entry, errors}` — so the frontend builds the outcome
  table from the stream alone and needs no second request.
- The terminal event is `done{phase, done, total, finished:true, cancelled, errors, summary}`.
  `summary` is `{op, processed, applied, failed}` for a batch write and `{processed, removed, failed}`
  for a cache rebuild. **Counting happens in the worker thread, never in the frontend**: a dropped
  connection must not change the numbers.
- **Cancel semantics (frozen):** the flag is checked **between images**, so a write already in
  progress always finishes. Whatever landed stays written and is counted in `processed`, which means
  `cancelled:true` says "stopped early", never "undone". A cancel for an unknown or finished job
  answers 202 `{cancelled:false}` rather than pretending.
- An unknown `job_id` on either stream is 404 `unknown_job` (§4.14's code).
- The event log is complete, so an EventSource that connects only after the POST replays the whole run.
- Finished jobs are kept under the same `MAX_JOBS` cap as §4.14; the oldest finished job is dropped
  first, and its stream then answers 404.

**The batch caption read (2026-09-19).** `POST /captions/read` answers `{results: [...]}` in input
order, one entry per image. An entry is the `GET /captions` shape plus `ok: true`; a file that
vanished or is not an image is `{image, ok: false, error: {code, message}}` and the other entries
still come back. Out of bounds is a security problem, so it rejects the whole request with 403 like
`/captions/batch` does. It is read-only: no write, no cache invalidation. §4.2 removed one request
per image for the red dot (`has_caption`); the tag filter and the visible-scope frequency table need
the **tag lists**, which the listing deliberately does not carry, so the frontend asks for them in
chunks instead of N times.

**The restore: undoing a write (2026-09-19, the fifth §4.5 item).** `POST /captions/restore` is the
inverse of item (4)'s safety net. For each image, `<caption>.bak` is written back **byte for byte** - the
bytes that were there before this tool touched the file, CRLF / BOM included - and then the backup is
**removed**, because `backup_file` rule 1 ("an existing backup is never overwritten") would otherwise
disarm the net for every later write to that caption. When the content really changes, the text-encoder
cache is invalidated in the same call (§3.2, invariant number one): an undo that left a stale cache behind
would train on the tags the user just took back. Per-entry problems are reported in that entry and the
rest still run - `{image, ok: false, error: {code: "no_backup" | "restore_failed" | ...}}`, where
`no_backup` is what undoing a write that changed nothing looks like, not a request failure. Out of bounds
rejects the whole request with 403, the same rule as `/captions/batch` and `/captions/read`. **This is
what makes a write reversible, and therefore what lets the batch panel drop its confirmation dialog**
(the checkbox decides whether there is a question at all: with the backup on, the toast carries the undo).

**`/autotag/preview` must not write to disk.**

### 4.1 thresholds / postprocess (frozen)

Request body for `POST /api/autotag/preview` and `/run`:

    {
      "images": ["<absolute path>", ...],
      "model": "wd-swinv2-tagger-v3",
      "thresholds": {"general":0.35,"character":0.35,"copyright":0.35,"artist":0.35,"meta":0.35},
      "postprocess": {
        "remove_underscore": true,
        "undesired_tags": [], "always_first_tags": [],
        "character_tags_first": false, "character_tag_expand": false,
        "escape_parens": true,
        "rating": "none"
      },
      "write_mode": "backup_overwrite"
    }

**Threshold defaults follow sd-scripts: all 0.35** (the default of `--thresh`, general and character the
same value). **Do not** give character/copyright higher defaults - that silently drops legitimate
character tags and also makes the output impossible to compare against `tag_images_by_wd14_tagger.py`.
`remove_underscore` **must default to true** (measured: the model gives `raiden_shogun`, the dataset
writes `raiden shogun`). `rating` ∈ {none, first, last}.

**`escape_parens` must default to true**, and this is not a matter of taste - it rests on two pieces of
measured evidence:

1. In the dataset **1049 files** write character tags in danbooru-escaped form:
   `hu tao \(genshin impact\)`, `asuna \(blue archive\)`.
2. The repo's own inference prompt `configs/sample_prompts.txt:4` reads `mika \(blue archive\)` - **the
   prompt convention on the Anima side is escaped parentheses too**.

The name in the WD14 vocabulary is `keqing_(genshin_impact)`, which after `remove_underscore` becomes
`keqing (genshin impact)` - **with no escaping**. Skip this step and the caption the tagger writes matches
neither the existing captions nor the inference prompt.

Order: after `remove_underscore` and before sorting, escape every `(` `)` to `\(` `\)`.
This applies only to tagger output; it does not touch tags the user typed by hand (those may contain
weight syntax such as `(masterpiece:1.2)`).

### 4.2 `/api/fs/list` images[] must carry has_caption (frozen)

    {"name": str, "path": str, "size": int, "mtime": float, "has_caption": bool}

Without it the frontend would have to issue another `GET /api/captions` per image just to learn about
the red dot - 20 images/directory = 20 extra requests.

### 4.3 SSE Protocol (frozen)

`GET /api/autotag/stream?job_id=...`:

| event | data |
|---|---|
| `progress` | `{done:int, total:int, current:str, errors:[{path,message}]}` |
| `done` | `{done:int, total:int, finished:true, errors:[...]}` |

**The end must emit an event with `finished: true`**; do not rely on `done >= total` alone - total can be
inaccurate when a failure occurs midway. Emit one `progress` per processed image.

### 4.4 Cancellation (frozen)

`POST /api/autotag/cancel`, body `{job_id}` → 202 `{cancelled: bool}`.
The backend sets the cancel flag, stops once the current image finishes, and emits a final `done`
(`finished: true`). **The frontend button must actually call it**, not just close the EventSource.

---

### 4.5 Four Filled-in Value Ranges (2026-09-17, surfaced by the wave-2 implementation)

**① The `category` of `/api/tags/autocomplete` is the category name, not the numeric code**

    {"tag": "1girl", "category": "General", "category_id": "0", "count": 5113288}

The internal representation (`core.vocab.TagInfo.category`) keeps the CSV's numeric string unchanged;
**the API layer is responsible for translating**. Rationale: the only consumer of this API is our own
frontend, and `tags.js:paintSuggest()` displays `item.category` directly - returning the numeric code
would produce dropdown rows like `1girl 0 5113288`. A field named `category` should be the category name.

**② `POST /api/autotag/preview` accepts at most 32 images**

Exceeding it → 400 `too_many_images`. Rationale: preview is a **synchronous** request, about 0.8 s/image
on CPU; with no limit, selecting all 1653 images ≈ 13 minutes while the frontend fetch has no timeout, so
the UI looks frozen. 32 images ≈ a 26-second ceiling, and the real dataset has "1-20 images per
directory", so 32 is enough to preview a whole directory.
**To preview a larger range, the right move is to turn preview into a job + SSE too (P1), not to remove
this limit.**

**Validation order (2026-09-21)**: the **shape of the request** is checked before this machine's
environment - the image limit, then path resolution, then `thresholds` / `postprocess` / `write_mode`, and
only then whether a model is installed. A malformed request is therefore 400 whatever happens to be on
disk. Before this, a machine with **no** model answered 409 `model_unavailable` to a request whose
thresholds were out of range, so the status code depended on what was downloaded (found by running the
suite where nothing is).

**③ Parameter names for `/api/captions/batch` ops**

| op | Parameters |
|---|---|
| `replace` | `find`, `replace`, `ignore_case` |
| `regex_replace` | `pattern`, `replacement`, `ignore_case` (replace **within each tag**) |
| `regex_replace_text` | `pattern`, `replacement`, `ignore_case` (replace **across the whole text**, result re-split on bare commas) |
| `append` / `prepend` | `tags` (order-preserving, skips ones already present) |
| `remove` | `tags` |
| `edit_tags` | `pairs:[{find,replace}]` (empty `replace` string = delete) + `tags` (additions) + `position` (`append`/`prepend`) + `ignore_case` |
| `normalize` / `sort_danbooru` | — |

The last three were filled in on 2026-09-18 when wiring up the frontend batch panel (`regex_replace_text`
/ `edit_tags` / `prepend`). `edit_tags` renaming is a **single mapping**: the first matching pair wins,
no chaining (given both `a→b` and `b→c`, the original `a` becomes only `b`). Chaining would let "rename
then rename again" quietly touch a batch of extra images with no visible sign in the UI. Rows are **not
compressed** either: deleting one tag does not shift and rename the tags after it.

**`dry_run: true` (added 2026-09-18, for "take a look before applying")**: writes not a single byte and
touches no cache, returning `changed` / `before` / `after` per image; here `applied` means "how many would
change", the response carries top-level `dry_run: true`, so the frontend cannot mistake a preview for a
completed write. Preview and the real write share the same `_apply_batch_op` and §3.2's
`normalize_for_write`, so "the preview says it will change" and "the write really changed it" are the same
rule - acceptance **A35** asserts directly that `after` is byte-for-byte equal to the file after writing.

**`backup` (added 2026-09-19, default `true`)**: before an image's caption is overwritten it is backed
up verbatim to `<caption>.bak`, with exactly `write_mode`'s `backup_overwrite` semantics (§4.5 ④): an
existing backup is never overwritten, a caption whose content does not change leaves no backup, the copy
is byte-level. The default matches the tagger's, for the same reason - **the batch rewrite is the same
irreversible overwrite**, so the safe path must not be the one you have to ask for; `backup:false`
restores the behaviour from before this date. A backup that cannot be written **aborts that image's
write** (`write_failed`, message `backup failed: …`): a promise of "you can restore it" must not be
broken by the very run that needed it. A `.bak` touches no cache and is not dataset content - it is
neither an image extension nor a caption sidecar name, so directory scans, `dataset.toml` generation and
cache invalidation never see it (§4.5 ④).

The frontend's `BATCH_OP` in `api.js` is the **single frontend source of truth** for this op set, and its
values must **match the backend `BATCH_OPS` value for value** (`test/test_web_batch.py` compares the two
sides; drift one value and the user gets a 400 only after clicking "write").

**④ Semantics of `write_mode`**

**The default is `backup_overwrite` (2026-09-19)**: used when the request omits `write_mode`. Why the
default changed - `overwrite` is the only path that **irreversibly** discards an existing caption, so the
default should not land on it; callers that explicitly pass `overwrite` are unaffected.

| Value | Semantics |
|---|---|
| `backup_overwrite` | **Default**: first back up the existing caption **verbatim** to `<caption>.bak`, then overwrite with only the new tags. An existing backup is **never overwritten** (the first backup = the original state before the tool ever touched it); when the content does not change **no backup is left** (nothing would be overwritten); the backup is a **byte-level** copy, preserving CRLF / BOM / encoding together; if the caption does not exist there is nothing to back up |
| `overwrite` | use only the new tags |
| `append` | old + new (no dedup) |
| `prepend` | new + old |
| `merge` | old + the ones in new that never appeared (**order-preserving dedup**) |
| `only_if_empty` | if the old caption is non-empty **not a single byte moves**, not even the cache is touched |

### 4.6 Model Download (requirement added 2026-09-17)

**Requirement**: when a tagger model is selected and is missing locally, download it from HF
automatically; a mirror must be available in China.

**Endpoints actually probed** (2026-09-17, by one-off probes that are not kept in the repository):

| Endpoint | `/api/models/<repo>` listing | `resolve/main/<file>` download | Notes |
|---|---|---|---|
| `https://huggingface.co` | 200 | 206, correct length | primary |
| `https://hf-mirror.com` | **200, byte-identical to the primary** | 206, same length | mirror, **same URL structure** |
| ModelScope | a different API | third-party re-uploads exist (e.g. `fireicewolf/wd-eva02-large-tagger-v3` has all files) | **not in P0** |

**Why ModelScope is not in P0**: its path structure and repo naming are both different
(`/models/<owner>/<name>/resolve/master/...`), and the existing WD taggers are **third-party re-uploads**
rather than official, so a hardcoded mapping table would rot. **A fallback already exists** - the user can
put the files into the model directory by any means (browser / ModelScope / aria2) and the registry will
recognize them (see the flat layout below).

**A limitation that must be stated**: this machine can reach huggingface.co directly, so during probing
`hf-mirror.com`'s `resolve` merely **redirects** to `us.aws.cdn.hf.co`. **I cannot verify the proxy
behavior on the China side from this machine.** So "the mirror is always faster" does not go into the
contract; what we can do is: configurable endpoints, automatic fallback across multiple endpoints, and
manual placement as an always-available last resort.

**Endpoint order**: `KOHYA_TAGGER_HF_ENDPOINT` (single, highest priority) → `https://huggingface.co` →
`https://hf-mirror.com`. Try in order and remember whichever succeeded.

**Directory layout**: downloaded to `<model_root>/<model_id>/model.onnx` and
`<model_root>/<model_id>/selected_tags.csv` (**flat layout**, without faking the HF cache's snapshot sha).

**The registry must recognize both layouts** (this was the gap found here: files placed by hand could not
be found before):

    <root>/models--<org>--<name>/snapshots/*/model.onnx     # HF cache (the shape hf_hub_download produces)
    <root>/<model_id>/model.onnx                            # flat (what we download / the user places by hand)

The vocabulary = any `*.csv` **in the same directory** as the onnx. Both layouts must be recognized,
otherwise the "download it yourself and drop it in" fallback is a lie.

### 4.7 HTTP Surface for Download

- `GET /api/autotag/models` gains per item: `installed: bool`, `size_bytes: int` (the byte count of the
  **local** model.onnx, 0 when not installed - this endpoint makes no network requests),
  `downloadable: bool`, `download_size_bytes: int` (**static** nominal download size, 0 when unknown).
  **Both sizes must be consumed by the frontend**: a not-installed row needs `download_size_bytes` to say
  "how big is the download"; reading only `size_bytes` makes every row show "unknown size" - this dead
  field was fixed once on 2026-09-19, and the criterion is pinned in
  `test_web_contract.py::test_model_list_shows_installed_downloadable_and_blocked`.
- **`description: str` is a degraded fallback, kept permanently** (not deleted, not renamed): it is the
  human-readable source text produced by the backend (Chinese prose for built-in models). The **source of
  truth** for model descriptions is the localized key addressed by stable id,
  `autotag.model.desc.<model-id>`, the same model as §4 "field division of labor" - structured/stable
  identity as the source of truth, the old string as fallback. The frontend uses the translation when
  `hasKey("autotag.model.desc."+id)` is true, otherwise it **displays `description` verbatim**: a message
  is never dropped and never replaced by a bare key. Both the list row and the dropdown description
  follow this rule.
- `POST /api/autotag/download` body `{model}` → 202 `{job_id}`
- Progress **reuses** `GET /api/autotag/stream?job_id=`, with one added field per event, `phase`:

    progress: {"phase":"download"|"infer", "done":int, "total":int, "current":str, "errors":[...]}
    done:     {"phase":..., "done":int, "total":int, "finished":true, "errors":[...]}

  When `phase=download` the unit of done/total is **bytes** and `current` is the filename; when
  `phase=infer` the unit is **images**. Adding fields is backward compatible (the frontend reads specific
  keys, not the whole object).
- Download failure (including a size mismatch) → goes into the job's `errors` and emits
  `done{finished:true}`, leaving no `.part` garbage

### 4.8 Download Implementation Contract (`autotag/download.py`)

    DEFAULT_ENDPOINTS = ("https://huggingface.co", "https://hf-mirror.com")
    HF_ENDPOINT_ENV = "KOHYA_TAGGER_HF_ENDPOINT"

    @dataclass(frozen=True)
    class DownloadPlan:
        model_id: str; repo_id: str
        files: tuple[tuple[str, int], ...]      # (path within the repo, expected byte count)
        total_bytes: int

    @dataclass(frozen=True)
    class DownloadResult:
        model_id: str; onnx_path: Path; vocab_path: Path
        endpoint: str; bytes_downloaded: int; skipped_existing: bool

    def endpoints() -> tuple[str, ...]
    def plan(model_id: str, *, endpoint: str | None = None) -> DownloadPlan
    def download(model_id: str, dest_root: Path, *, endpoint: str | None = None,
                 progress: Callable[[str, int, int], None] | None = None,
                 cancel: Callable[[], bool] | None = None) -> DownloadResult

- The listing comes from `{endpoint}/api/models/{repo_id}` (**measured: the mirror supports it too**);
  fetch only `model.onnx` and `selected_tags.csv`
- Stream the download (do not read it into memory at once - eva02's onnx is **1.26 GB**)
- Write `<name>.part` first, then `os.replace` on success; every failure/cancel path must delete the `.part`
- **Verify the byte count after download** against the listing; on mismatch → raise and delete, leaving no
  half model behind
- Already present with the correct size → return directly, `skipped_existing=True` (idempotent)
- Endpoint fallback: if one endpoint fails move to the next; when all fail, include every endpoint's error
  in the exception message

### 3.9 `core/dataset_toml.py` - generating the trainer's dataset.toml (A)

This is where the §1.1 fact lands: **the trainer does not recurse**, so when `image_dir` points at a
parent directory the training set is empty; there must be **one subset per directory**. Without this
module the editor would be finished and still unusable.

    @dataclass(frozen=True)
    class DatasetTomlParams:
        resolution: tuple[int, int] = (1536, 1536)
        batch_size: int = 1
        caption_extension: str = ".txt"
        num_repeats: int = 1
        keep_tokens: int = 0
        flip_aug: bool = False
        shuffle_caption: bool = False
        caption_tag_dropout_rate: float = 0.0
        caption_dropout_rate: float = 0.0
        caption_dropout_every_n_epochs: int = 0
        alpha_mask: bool = False
        enable_bucket: bool = True
        bucket_no_upscale: bool = True
        min_bucket_reso: int = 256
        max_bucket_reso: int = 4096
        bucket_reso_steps: int = 64

    @dataclass(frozen=True)
    class WarningItem:
        code: str; params: dict[str, Any]; text: str

    @dataclass(frozen=True)
    class SubsetPlan:
        image_dir: Path; image_count: int; num_repeats: int
        caption_count: int; warnings: tuple[str, ...]
        warning_items: tuple[WarningItem, ...] = ()

    @dataclass(frozen=True)
    class SkippedDir:
        image_dir: Path; reason: str; code: str = ""; params: dict[str, Any] = {}

    @dataclass(frozen=True)
    class TomlPlan:
        root: Path; subsets: tuple[SubsetPlan, ...]; toml_text: str
        image_count: int; warnings: tuple[str, ...]
        skipped: tuple[SkippedDir, ...] = ()
        warning_items: tuple[WarningItem, ...] = ()

    DATASET_TOML_NAME = "dataset.toml"         # added 2026-09-20 (§4.16): the file name the trainer reads, defined once

    def toml_quote(value: str) -> str          # produces a TOML basic string, escaping backslashes and double quotes
    def plan_dataset_toml(root: Path, params: DatasetTomlParams) -> TomlPlan

`DATASET_TOML_NAME` is the **single definition** of the name: §4.14's export writes it at the target
root, §4.9's frontend download saves it under it, and §4.16's status endpoint reports it. Two
spellings would let those surfaces describe two different files without anything noticing.

**The three `caption_dropout_*` defaults are never written into the TOML** (measured 2026-09-17): with
`--support_dreambooth` (and without `--support_dropout`), writing those three keys makes the trainer
reject the file:

    extra keys not allowed @ data['datasets'][0]['subsets'][0]['caption_tag_dropout_rate']

Omitting them exits 0. The trainer dataclass defaults are already 0.0/0.0/0, so the semantics are
identical. **Write only non-default values** - do not casually change it to "always write everything".

**Cold-cache cost**: a full scan has to open 1653 image headers; measured **14.65 s cold / 0.33 s warm**.
The endpoint is a synchronous `def` (FastAPI offloads it to a thread pool, so it does not block the event
loop), so the first export makes the user wait a dozen seconds. P0 accepts that; turning it into job+SSE
is P1 (same reason as the preview item).

**Path escaping is not optional, it is a hard requirement established by measurement**
(a one-off probe + the trainer's own validator):

| TOML form | Result from the trainer's `config_util` |
|---|---|
| `"C:\\datasets\\myset\\..."` (escaped basic string) | **exit 0** ✓ |
| `'C:\datasets\myset\...'` (literal string) | **exit 0** ✓ |
| `"C:\datasets\myset\..."` (unescaped basic string) | **exit 1**: `ValueError: Reserved escape sequence used` ✗ |

→ Use an **escaped basic string**: `\\` becomes `\\\\`, `"` becomes `\"`.
A literal string passes too, but breaks the moment a single quote appears in the path; the user's existing
hand-written kohya config uses an escaped basic string as well.

**Warnings that must be produced** (this is where the editor earns its keep over hand-written config - the
trainer silently accepts all of these):

- a subdirectory with 0 images → **skip that directory** and warn (do not generate an empty subset)
- an image with a missing caption → warn, stating the count (the trainer will happily train on an empty
  caption)
- a caption containing `\,` → warn (the trainer splits on bare commas and would read two tags)
- an image whose long edge > `max_bucket_reso` → warn (it will be resized)
- an image with alpha → hint that the subset can enable `alpha_mask`
- a subdirectory with too few images (suggested threshold 5) → hint, but do not block

### 4.10 Autocomplete Matching Semantics: Substring, Not Prefix

**Surfaced by the end-to-end smoke test** (2026-09-17, `tools/e2e_smoke.py`): the query `genshin` returned
empty.

The cause is not a bug but the semantics: character tags in the WD14 vocabulary look like
`ganyu_(genshin_impact)`, and **no** tag starts with `genshin`; not even `genshin_impact` is findable -
because it is preceded by `(_`. Measured:

    q=1girl            -> 1girl
    q=ganyu            -> ganyu_(genshin_impact)
    q=genshin          -> (empty)
    q=genshin_impact   -> (empty)
    q=hair             -> hair_ornament, hair_between_eyes, hair_ribbon

Prefix matching is **not good enough** for danbooru tags: what a user remembers is usually "this character
is from Genshin", not its exact prefix in the vocabulary. Tag autocomplete in the SD ecosystem (a1111 and
others) has always been substring matching.

**Frozen**: `q` does **case-insensitive substring matching**, ordered by

1. prefix hits first
2. then by `count` (vocabulary popularity), descending
3. then by tag name lexicographically, for determinism

A linear scan over 10,861 entries is a negligible cost.

### 4.9 HTTP Surface for Exporting dataset.toml

`POST /api/dataset/toml`, body `{root, params}` (pure computation, **writes no file**) →

    {"toml": str, "subset_count": int, "image_count": int,
     "subsets": [{"image_dir": str, "image_count": int, "num_repeats": int,
                  "caption_count": int, "warnings": [str]}],
     "warnings": [str], "skipped": [{"image_dir": str, "reason": str}],
     "warning_items": [{"code": str, "params": {...}, "text": str}],
     "skipped_items": [{"image_dir": str, "code": str, "params": {...}, "reason": str}]}

**Three supplements to the wire shape (2026-09-17, raised while implementing the export UI)**:

1. **`params.resolution` is a two-element `[width, height]` array in JSON** (on the Python side a
   `tuple[int, int]`). All other fields are scalars.
2. **Top-level `warnings` is an aggregate**: it includes the entries from each `subsets[].warnings`;
   `subsets[].warnings` holds that subset's own entries. The frontend merges them for display and
   deduplicates (the same text appearing under different subdirectories is **not** deduplicated - those
   are problems in different places).
3. **The `reason` of `skipped` and the `warnings` messages must all start with `[code]`**, see the table
   below. Rationale: the frontend groups them for display by category, and matching keywords against the
   Chinese wording would silently degrade into "other warnings" after any rewrite - which would exactly
   kill the main value of this feature.

| code | Meaning |
|---|---|
| `[empty_dir]` | directory with 0 images (goes into `skipped`) |
| `[unreadable_dir]` | directory cannot be read (goes into `skipped`) |
| `[missing_caption]` | an image is missing its caption |
| `[escaped_comma]` | a caption contains `\,` (the trainer would read two tags) |
| `[oversize_image]` | image long edge exceeds `max_bucket_reso` |
| `[alpha_images]` | images with alpha present (consider enabling `alpha_mask`) |
| `[too_few_images]` | too few images in the subdirectory |
| `[root_images]` | **the root directory's own level has images** - the trainer does not recurse, so none of them get trained; not reporting it silently drops data |
| `[broken_image]` | a single image's dimensions cannot be read (corrupt/unsupported); still counted in `image_count` (the trainer counts it too, then fails to load it) |

The last two codes were added when the generator was implemented and are added to the table. Note that
`[broken_image]` **must not** be called `unreadable_image`: in the frontend's fallback keyword table
`unreadable` belongs to the "directory cannot be read" group and would be misclassified.

The frontend **classifies by `[code]`**; an unknown code lands in the "other" bucket and is **not
discarded**.

**Group headings use the code's label; the backend's original text degrades to small print inside the
group** (decided 2026-09-17). Rationale: the same code may have more than one wording (in the
measurements the two `[empty_dir]` messages differ), and using the original text as the heading would
split one class of problem into two groups. The original text is still shown in full, it just is not the
grouping key.

**Fourth supplement to the wire shape (2026-09-19, localization requirement)**: the two old arrays
`warnings: [str]` / `skipped: [{image_dir, reason}]` are **unchanged**, and two **item-for-item parallel**
optional arrays are added, so the frontend can look up localized text by `code` (`code` / `text` /
`reason` are byte-for-byte identical to the old arrays):

    warning_items: [{"code", "params", "text"}]                # same order and length as warnings
    skipped_items: [{"image_dir", "code", "params", "reason"}]  # same order and length as skipped

`params` is frozen per code (the frontend key and the template `{placeholder}` correspond one to one):

| code | params |
|---|---|
| `missing_caption` | `{"count": int, "extension": str}` |
| `escaped_comma` | `{"count": int}` |
| `oversize_image` | `{"count": int, "max_bucket_reso": int, "max_edge": int}` |
| `alpha_images` | `{"count": int, "alpha_mask": bool}` |
| `too_few_images` | `{"count": int, "minimum": int}` |
| `broken_image` | `{"count": int}` |
| `empty_dir` (warning) | `{"count": int}` (total number of skipped empty directories) |
| `empty_dir` (skipped reason) | `{}` - a fixed short sentence, no variables |
| `unreadable_dir` (warning) | `{"name": str, "detail": str}` |
| `unreadable_dir` (skipped reason) | `{}` |
| `root_images` | `{"count": int}` |

Frontend rendering rules (frozen): `key = "msg." + code`; **the skip reason uses `"msg.skip." + code`** -
the same `empty_dir` / `unreadable_dir` is two different sentences in "warning" and "skipped reason", and
the frozen code cannot change, so the two paths each use their own key. When `hasKey(key)` is false, or
after `t(key, params)` a `{placeholder}` is still left (incomplete params), **fall back to the backend's
original text** - a message is never dropped, and never replaced by a bare key. Errors follow the same
rule: `msg.<code>`, and when it is unrecognized / the placeholders are incomplete, show `message`.
**Code semantics change (2026-09-19)**: an empty allowlist (`paths.configure()` never called) used to share
the generic `outside_roots` with out-of-bounds paths; it now has its own stable code `paths_unconfigured`
(403 unchanged, message string unchanged). This is a **semantic refinement** recorded per §4 "how the
contract evolves": the old `outside_roots` carried three different reasons under one code, so the frontend
could not produce a fitting message - what was missing was not translations, it was codes. The only
consumer (the in-repo frontend) does not branch on this value, so it is non-breaking.

**Error codes that supply `params`** are frozen one by one (frontend key = `msg.<code>`, corresponding one
to one with the template `{placeholder}`):

| code | params |
|---|---|
| `outside_roots` / `not_found` / `not_a_directory` | `{"path": str}` |
| `paths_unconfigured` | `{}` (a fixed short sentence, no variables) |
| `remote_path_config_disabled` | `{"host": str}` |
| `not_a_file` | `{"path": str}` |
| `unsupported_image` | `{"name": str}` |
| `bad_size` | `{"sizes": str}` |
| `bad_write_mode` | `{"modes": str}` |
| `bad_op` | `{"op": str, "available": str}` |
| `unknown_job` | `{"job_id": str}` |
| `no_images` | `{"path": str}` |
| `unreadable` | `{"target": str, "detail": str}` |
| `missing_tag_payload` / `empty_image_list` | `{}` (a fixed short sentence, no variables) |
| `bad_path` | `{"path": str, "detail": str}` |

The remaining errors have an empty `params` object and are **deliberately not translated**, shown as the
original text: `bad_request` (the free-text half), `invalid_tag` / `unknown_model` / `model_unavailable` /
`cannot_plan` (`str(exc)`), plus the diagnostic ones `validation_error` / `internal_error` and 500s (the
list is in [i18n](i18n.md)).

**Splitting `bad_request` (2026-09-19)**: it carried several structurally different messages at once and
one template could not cover them, so the three **fixed-template** cases were split into their own codes
with explicit `params`: `missing_tag_payload` (neither tags nor text given), `empty_image_list` (`images`
empty), `bad_path` (path resolution failed, `detail` is the raw OS exception). `bad_request` itself keeps
carrying free text such as `str(exc)`; its role is the fallback and it is not translated.
The config layer's `PathConfigError` (codes `bad_request` / `not_a_directory` / `last_root` /
`unknown_root`) surfaces through the `/api/roots` endpoint of §4.11 and now carries `params` too: the
variable data for the base class's two fixed templates lives here and is passed through verbatim by the
endpoint - `cannot resolve path %r: %s` fills `{"path": str, "detail": str}`, `path must not be empty` fills `{}`. Their
code is still the frozen `bad_request` (**not renamed**), so they are still shown as the backend's original
text; the subclasses `not_a_directory` / `last_root` / `unknown_root` carry no variables for now. `code`
and `message` are unchanged.

**Field division of labor (2026-09-19, localization)**: the structured fields are the **data source of
truth**, the old string fields are the **degraded fallback**, and the two **coexist long-term** - this is
not a transition, and there is no removal plan.

- Source of truth: `warning_items[].code/params`, `skipped_items[].code/params`, `error.code/params`;
- Fallback: `warning_items[].text` / `skipped_items[].reason` / `error.message`. The frontend uses the
  translation only when `hasKey("msg."+code)` is true and the placeholders are fully filled, otherwise it
  **displays the fallback verbatim**.

**Why the fallback cannot be deleted**: `validation_error`, `internal_error` and the various 500 classes
are **diagnostic information** (pydantic / raw exceptions) and are, per [i18n](i18n.md)'s rules,
**deliberately not translated**; without a fallback they would render as blanks in the English/Japanese UI.
So "the old fields are redundant" does not hold - deleting them is not a simplification, it is a
regression.

**The invariant that must hold**: the fallback string is a **pure projection** of code+params - produced in
one place in core, with no separately written wording, and `test_dataset_toml.py` compares the two
byte-for-byte. When a new code is added, its `msg.<code>` translation must be added at the same time;
"it can be displayed" is not "it is localized".

**P0 does not write to disk server-side**: the frontend offers "copy" and "download .toml". Rationale:
writing files is a new damage surface (paths, overwriting, backups), while a browser download is already
entirely sufficient. Server-side writing, if done at all, would first require freezing the overwrite
policy and the backup location, which is a separate matter.

### 4.11 Runtime-editable Path Configuration (requirement added 2026-09-17)

**Why it must be runtime.** The allowlist roots used to come only from `--roots` / `KOHYA_TAGGER_ROOTS` /
`roots.txt`, and the model search directories only from `--models` / `--extra-models` /
`model_paths.txt`, and both were **read only once at startup**. So switching to a different dataset or
adding a model directory required restarting the service. The user hit both during testing:

> the web ui root directory cannot be modified, it is locked to the test directory I provided;
> the model scan directory page has no configuration entry, it seems to be hardcoded too.

This section makes both editable at runtime, **taking effect immediately** (re-running
`paths.configure()` / recomputing the search result), with no process restart.

**A security note (deliberate, not a flaw):** `POST` / `DELETE /api/roots` **widen** the allowlist. That
is this tool's intended use - the service listens only on `127.0.0.1` (`DEFAULT_HOST`), and the caller is
the same person using the filesystem. The allowlist stops "mistyped paths and out-of-bounds traversal",
not "another user on this machine". **Change `--host` to an externally listening address and those two
endpoints hand over read/write access to any directory.**

Two persistence files (both in the repo root by default, paths overridable, see §3.7.1):

| File | Written by | Content | Rewrite method |
|---|---|---|---|
| `roots.txt` | the launcher and the API | **all** allowlist roots, one per line | **rewritten whole** (a machine-managed file, comments not recognized) |
| `model_paths.txt` | the user and the API | **extra** model scan directories, one per line, `#` comments, semicolons also accepted | **only path lines are added or removed**; comments and layout are preserved verbatim |

The two are written differently on purpose: `roots.txt` is the server's authoritative state (the launcher
reads it line by line, so it cannot contain comments); `model_paths.txt` is a user-authored config file,
and rewriting it whole would eat the explanation block at the top.

**Both files are read once, at `config.load()`.** At runtime **in-memory state is authoritative**: the
endpoints change memory and write the file to match; hand-editing a file requires a restart (or going
through the API) to take effect. Doing the reverse - re-reading the files on every request - would let the
state "the API said the change succeeded but the file was not written" silently roll back on the next
refresh, which is far worse than one extra restart.

#### GET /api/roots

    {"roots": [{"path": str, "name": str, "exists": bool, "is_dir": bool, "persisted": bool}]}

`name` = the directory name (for the roots column display); `persisted` = whether this entry appears in
`roots.txt`. The shape **adds fields** on top of the §4 table's `{path,name}`: an old frontend reading only
`path` / `name` still works.

#### POST /api/roots

Request `{"path": str}`. **There is no `persist` switch**: if it can be added it must be remembered; when
the file cannot be written, report it honestly (`persisted: false` + `warning`) instead of silently living
only until the next restart.

    {"roots": [...], "changed": bool, "persisted": bool, "warning": str | null}

- The path is `expanduser()`d + `resolve()`d before comparison, so `F:\data\..\data` and `F:\data` are the
  same entry;
- already in the allowlist → `changed: false`, **no file write**, still 200 (idempotent, not an error);
- does not exist, or exists but is not a directory → **400 `not_a_directory`**. This differs from other
  endpoints: an allowlist root is **not allowed** to be a nonexistent directory - configure one and every
  request 403s/404s while the user believes it is set up;
- once added it **immediately** takes effect for `paths.configure()`.

#### DELETE /api/roots?path=

    {"roots": [...], "removed": str, "persisted": bool, "warning": str | null}

- not in the allowlist → **404 `not_found`** (the same `resolve()` comparison);
- **the last one cannot be deleted** → **400 `last_root`**. Empty out the allowlist and every `/api/fs/*`
  call 403s, which is exactly the hardest class of symptom to diagnose ("why does nothing open"). To switch
  datasets, **add first, then delete**;
- deletion also takes effect immediately.

#### GET /api/autotag/model-roots

    {"roots": [{"path": str, "source": str, "exists": bool, "is_dir": bool,
                 "file_backed": bool, "models": [str], "model_count": int}]}

- `source` ∈ `repo` / `file` / `cli` / `explicit` / `auto` (meanings in §3.7.1); `file_backed` is
  equivalent to `source == "file"`, given a separate field only because the frontend needs just that one
  test;
- `models` = the model ids resolvable from **this root alone** (`registry.resolve(id, [root])` succeeds).
  This is the main reason the endpoint exists: the user has to be able to see "what did the directory I
  added actually get recognized as";
- measured cost **12 ms** (all 5 roots, 2026-09-17), so **no caching and no `?probe=` switch**;
- the order **is** the priority (§3.7's table), not reordered, not sorted.

#### POST /api/autotag/model-roots

Request `{"path": str}`. The new root is **appended after the existing `file` entries** - that is, still
before `%LOCALAPPDATA%/kohya-dataset-tagger/models` / the HF cache (explicit > auto-discovery, §3.7).

    {"roots": [...], "changed": bool, "persisted": bool, "warning": str | null}

Nonexistent or not a directory → 400 `not_a_directory`; already in the list → `changed: false`, idempotent.

#### DELETE /api/autotag/model-roots?path=

    {"roots": [...], "removed": str, "removed_scope": "file" | "session",
     "persisted": bool, "warning": str | null}

- `file_backed: true` → remove **that one line** from `model_paths.txt` (other lines and comments untouched),
  `removed_scope: "file"`, and it does not come back after a restart;
- `cli` / `auto` roots → only effective for this run: `removed_scope: "session"`, `persisted: false`, and
  the `warning` says plainly that a restart brings it back. **There is no exclusion table**: one more
  persistent file is one more place that can fall out of sync;
- deleting everything does **not** error (the model list becomes empty and tagging falls back to
  `model_unavailable`, but the UI can still add directories back).

#### Frontend landing spots (`web/js/settings.js`)

- In the left column, a plus button next to the "Roots" heading and a delete button after each entry
  (disabled with a hint when only one is left);
- in the tagger panel a "Model directories…" button whose dialog lists every search root, each row showing
  `model_count` and a source badge, with add and delete;
- both inputs allow **pasting an absolute path** - it remains the **fallback under any binding** (the picker
  may be disabled by the loopback gate). A browser cannot provide a native directory picker: the File System
  Access API gives only a handle, drag and drop gives only relative entries, and neither yields an absolute
  path. So "graphical" can only be implemented by the server listing directories, see §4.12;
- the server's `warning` is **displayed as-is**, not swallowed (the same principle as §4.9's `warnings`).


### 4.12 Graphical Directory Picker + Loopback Gate (requirement added 2026-09-18)

**The problem to solve**: the UI displays "the currently open dataset directory", but it is a line of
**unclickable text** - switching datasets means pasting an absolute path. §4.11 had rejected this earlier
on the grounds that "a browser cannot provide a native directory picker, and adding a 'list any directory'
endpoint would void the allowlist". On 2026-09-18 the user explicitly asked for graphical selection
**only in this one place**, accepting the loopback gate as the boundary.

#### GET /api/fs/pick

    ?path=     (empty = the first allowlist root; with no allowlist root = the user's home directory)

    {"path": str, "parent": str | null, "dirs": [{"name": str, "path": str}],
     "roots": [str], "home": str}

- **lists directories only**: no files, no image counts, no file contents, no thumbnails. It picks a
  directory, it is not a second browsing endpoint;
- `parent` is `null` at a filesystem root (Windows' `C:\`, POSIX's `/`), and the UI disables "up" on that
  basis;
- `roots` are the **jumpable filesystem roots**: on Windows the existing drive letters, on POSIX `/`.
  Without them there is no way to move between directory trees;
- 404 `not_found` / 400 `not_a_directory` / 403 `unreadable`;
- **the allowlist does not apply** - that is precisely why it exists (adding a directory to the allowlist
  is, by nature, picking a directory that is still outside it). It is **one of the exceptions** to §4's
  opening rule "every path parameter goes through `resolve_under_roots`" (the other is the mkdir in the
  same module), which is why it lives in its own layer (`api/pick.py`) rather than being mixed into the
  semantics of `/api/fs/*`.

#### POST /api/fs/pick/mkdir (added 2026-09-19)

    {"parent": str, "name": str}
    → 200 {"path": str, "created": bool}

- The user's words: "the export directory picker is not complete enough, you cannot type a path and you
  cannot create a folder". **Manual entry** is pure frontend navigation (a path box → `GET /api/fs/pick`);
  **creating a directory** has to touch disk, so this is the only write endpoint here;
- `name` allows **a single path component** only: empty, `.`, `..`, containing `/ \ < > : " | ? *`, leading
  or trailing whitespace, or ending with a dot all get 400 `bad_name` - creating a directory must not become
  a write path that escapes the parent;
- **creates one level only** (not `mkdir -p`); when a **directory** of the same name exists it returns
  idempotently with `created:false` (what the user wants is "go to that directory", and an existing one
  should not be an error), while a **file** of the same name gets 409 `already_exists` and is never
  overwritten;
- `parent` nonexistent 404 `not_found`, not a directory 400 `not_a_directory`, unwritable 403 `unwritable`;
- **same level, same gate** as `GET /api/fs/pick`: the allowlist does not apply, the loopback gate does.

#### Loopback gate: `remote_path_config_disabled`

    {"error": {"code": "remote_path_config_disabled", "message": str}}

When `--host` is **not a loopback address**, the following six all return 403:

    GET    /api/fs/pick              POST /api/fs/pick/mkdir
    POST   /api/roots                DELETE /api/roots
    POST   /api/autotag/model-roots  DELETE /api/autotag/model-roots

- loopback = `127.0.0.1` / `::1` / `localhost` / `::ffff:127.0.0.1` and the like
  (`config.is_loopback_host`); **anything unrecognized is treated as non-loopback** (fail closed);
- escape hatch: `--allow-remote-path-config`, or `KOHYA_TAGGER_ALLOW_REMOTE_PATH_CONFIG=1`. With it on,
  those six behave normally, and the startup banner states in one conspicuous line what has been opened up;
- **gating only the picker and not roots changes is self-deception**: `POST /api/roots` can already add any
  **existing** directory to the allowlist, after which `/api/fs/list` + `PUT /api/captions` can read and
  write it. The real door is those four mutation endpoints, so the gate must cover them together;
  `POST /api/fs/pick/mkdir` is **a write outside the allowlist**, so gating only the picker and leaving it
  alone is a backdoor, which is why it is in the same gate;
- the frontend decides from `GET /api/health`'s `path_config.allowed`: show the picker when available,
  otherwise **gray it out + explain why**. Silently disappearing = another "unclickable and no idea why",
  which is exactly the experience this change is meant to fix.

#### Frontend landing spots ((web/js/picker.js))

- New module: a modal = **an editable current-path box** + quick jumps (drives / home) + **a create-directory
  row** + a one-level directory list + "up / use this directory / cancel"; Esc, the overlay and Cancel all
  close it;
- **Manual entry** (2026-09-19): type an absolute path in the box and press Enter to go there
  (`GET /api/fs/pick`); "use this directory" also respects the box's value - typing something and not
  pressing Enter should not be silently ignored. Enter inside the input belongs to the input and does not
  close the dialog along the way;
- **Create directory** (2026-09-19): an inline directory-name input + a "Create directory" button
  (**not `window.prompt`**: it cannot be translated, cannot be tested headless, and blocks the modal). On
  success, clear the name and **enter the new directory**, from which the user can then click "use this
  directory";
- three entry points feed the same picker: **the currently highlighted root in the left column**, **the
  "Export directory" row of the export panel**, and **the current-position segment of the breadcrumb**.
  Once chosen = `POST /api/roots` (idempotent) → reload the roots list → open it;
- the paste box is **kept**: it is the fallback under any binding, just no longer the only road.


### 4.13 Folder Nine-Grid Preview in the Center Gallery (requirement added 2026-09-18)

**The problem to solve**: by the trainer's rules a dataset root **has no images of its own** (§1.1:
`glob_images` scans one level only), so opening a dataset leaves the center workspace **empty** - the 219
subdirectories are just a column of text in the left pane. The user asked for: "the preview interface
should show folders in plain folder-browsing mode, and the folder preview content should be a nine-grid of
the image files inside the folder".

#### Data: `dirs[].preview_images` of `GET /api/fs/list`

    {"name": str, "path": str, "image_count": int, "preview_images": [str]}

- at most **9** per item (`api/fs.py:DIR_PREVIEW_LIMIT`) - 9 is a **cap**, and the frontend lays out
  **1 / 4 / 9** grids by the actual count (1 image fills the card, 2-4 use 2×2, 5 or more use 3×3);
- sampling **scans only this one level of that subdirectory**, taking the first few in `iter_images`'s
  **natural order** - it is **the same enumeration** as `image_count` (`count_images` is just
  `len(iter_images(...))`), so no extra images are opened and there cannot be a "the count says it exists
  but the preview does not" mismatch;
- sampling does **not** validate each image against the allowlist: the directory itself already passed the
  allowlist, and only a **symlink** pointing outside could escape - and if that path is ever taken,
  `/api/images/thumb` returns 403 and the card hides that image as a load failure, so it does not look
  clickable. Per-image `resolve_under_roots` measured **+440 ms** and per-image `lstat` **+48 ms**, both
  paying for a boundary that is already blocked downstream; the final implementation slices once, with the
  root listing measuring **96 → 98.5 ms** (219 directories), essentially free;
- an empty directory yields `[]`, and the field is **not omitted** - the frontend should not have to test
  whether the key exists.

#### Frontend: the folder card in `web/js/gallery.js`

- each card is a real `<button>`: **an adaptive grid** (see above, the count decides 1/4/9) + the directory
  name + "n images" (reusing `browse.dirImageCount`). A fixed 3×3 would put a tiny square huddled in a
  corner of a directory with only 1-4 images, and the user pointed out at first glance that "the optimal
  one was not chosen";
  Enter/Space are naturally reachable, no separate key handling is needed; the gallery's own click handler
  only recognizes `.tile`, so the two kinds of cards do not interfere;
- it appears **only when "this level has folders and no images"** (`app.js:galleryFolders()`:
  `state.images.length === 0 ? state.dirs : []`). A level with images **stays as it was** - that is
  browsing images, not browsing folders; with the recursive switch on, `images` is non-empty and the cards
  simply do not appear;
- thumbnails reuse `/api/images/thumb?size=grid` and the gallery's **bounded concurrency +
  IntersectionObserver lazy loading**, so 219 directories do not open hundreds of connections at once.
  Since 2026-09-20 the same loader also **serves the tiles on screen first**: the 400px prefetch margin
  decides *whether* a tile is queued, and a second zero-margin observer decides its priority, so scrolling
  never puts the visible images behind the first screen's backlog
  ([changelog/gallery-scroll-priority](changelog/gallery-scroll-priority.md));
  changing directories releases the bitmap with `removeAttribute("src")` together with the image tiles;
- clicking a card goes straight through the existing `openDir(dir.path)`: allowlist, breadcrumb and hash all
  follow the same path, with no second navigation route;
- when the cards are shown, "this directory has no images" is **no longer displayed** - at that point the
  sentence is false.

**Deliberately not done**: no new endpoint, no server-side compositing (one request returning a single
nine-grid image). Sizing and colors belong to CSS; thumbnails still only use the grid size and never load
the original image (decision #4, the image constraints outside A33 are unchanged).
### 4.14 Dataset Image Scaling/Export (requirement added 2026-09-19)

**The problem to solve**: the dataset's image sizes are all over the place (among 1653 images there are
both 3000×2000 and 400×300), while the trainer buckets by resolution. The user asked to integrate the
**area-normalized scaling** from the author's original standalone scaling tool and to add a "do not enlarge
images smaller than the target" option; on export it **attaches the caption automatically**, and the
original script's "extra file extension" input is no longer offered.

#### Math: area normalization, not cropping

    scale = sqrt(target_width * target_height / (width * height))
    new_width  = max(1, round(width  * scale))
    new_height = max(1, round(height * scale))

- the aspect ratio is unchanged, **there is no crop box** (in Chinese this is often called "cropping", but
  the implementation is scaling);
- with `no_upscale=True` (checked by default in the frontend), if the original image's **area** is smaller
  than the target area, `scale` is clamped to `1.0` and the dimensions stay as they are. The test uses
  **area**, not a single edge: a 2000×500 banner image has area 1.0e6 < the target's 2.36e6, so it is not
  enlarged either;
- rounding uses Python's `round` (banker's rounding), matching the source script; `max(1, …)` keeps an
  extreme banner image from ending up 0 wide / 0 high.

#### Request: `POST /api/scale/run`

    {images, root, target_dir, target_width, target_height, no_upscale, resample,
     output_format, quality, optimize, caption_extension,
     write_dataset_toml, overwrite_dataset_toml}

- **`images` is the export's whole extent** (2026-09-20): exactly those files are written, in the
  order they are listed. It is the answer the column's shared scope strip gives (`web/js/scope.js`),
  snapshotted when the run starts, so the panel acts on what the control on screen says - the same
  rule §4.17's crop panel follows. Before this the endpoint took only a directory and walked it
  recursively, which is how an export could cover the whole folder when the user had picked a few
  images: **an empty or absent list is 400 `no_images`, never "then do the whole directory"**, so the
  widened export is not reachable at all.
- every entry must be a file with an image extension (400 `not_a_file` / `unsupported_image`) and
  must lie under `root` (400 `image_outside_root`) - the target tree is rebuilt from each source's
  path **relative to `root`**, so an image from outside it has no place in that tree. All entries
  pass through the allowlist like any other user path (403 `outside_roots` / 404 `not_found`), the
  first bad entry rejects the whole request (no partial application), and a duplicate is dropped
  rather than exported twice.
- an empty `root` falls back to the first allowlist root; it must exist and be a directory.
- `target_dir` is required and **is allowed not to exist** (created recursively on export), but after
  `resolve_under_roots` it must land inside the allowlist, otherwise 403 `outside_roots`.
- **The target directory must not be inside the source directory** (or equal to it): otherwise the exported
  result would be seen by the trainer's directory scan as a new subset, which amounts to writing into the
  dataset. 400 `target_inside_root`.
- `resample` takes `lanczos / bilinear / bicubic / nearest / box / hamming`; `output_format` takes
  `png / jpeg / webp`; `quality` 1..100; unknown keys 422 (`extra=forbid`).
- The response is **202** `{job_id, total}`.

#### caption: attached automatically, not configurable

Next to every exported image a sidecar with the same stem is written (extension = `caption_extension`,
default `.txt`), byte-for-byte identical to the source caption. A source image without a caption is not a
failure, it just does not get one attached.
There is **no** "extra file extension" parameter - this is a deliberate divergence from the source script:
in this tool's dataset contract the sidecar *is* the caption, and one more free-form input would only let
people write configurations the trainer cannot read. `caption_extension` is **shared** between the frontend
and the §4.9 export panel (`web/js/trainparams.js`), so the generated dataset.toml and the sidecars actually
written cannot drift apart.

#### Progress: `GET /api/scale/stream?job_id=`

The same convention as §4.3: `event: progress` accompanies each image's result, and the end **must** be
`event: done` with `data.finished === true` (do not rely on `done >= total` alone, `total` is a snapshot
from the start).

    progress: {phase:"scale", done, total, current,
               image:{status,src,dest,orig_width,orig_height,new_width,new_height,
                      scale,upscale_skipped,resized,copied,caption,error},
               errors:[{path,message}]}
    done:     {phase:"scale", done, total, finished:true, cancelled:bool, errors:[…],
               summary:{target_dir,processed,scaled,kept,captions,failed,
                        dataset_toml,dataset_toml_warnings,dataset_toml_backup,dataset_toml_skipped}}

Cancellation: `POST /api/scale/cancel {job_id}` → **always 202** `{cancelled: bool}`. The render
stage is parallel, so it stops **before the next round is planned**: the images already in flight
finish, and the worker then emits a final `done{finished:true}`.

#### Writes and concurrency

- always an **atomic write** (`.tmp-scale-*` in the same directory → `os.replace`), leaving no temp file on
  success or failure;
- **the source files are not modified**: source images are read-only; a name collision in the target
  directory gets a `_1` / `_2` suffix and is never overwritten;
- the render stage is **parallel** (decode / resample / encode are Pillow C calls that release the GIL),
  while destination names stay deduplicated per directory by allocating them on **one** thread
  (`scaler.plan_one_image`, with one reserved-name set for the whole run) - so the never-overwrite rule
  needs no lock. Measured on 16 cores for a 1536x1536 PNG export: 1355 ms/image sequential -> 415 at 4
  workers -> 272 at 8 (5.0x). A cancel is checked between rounds;
- when the size is unchanged and the output format matches the source format, **copy the bytes directly**
  (no re-encoding), so JPEG does not lose quality a second time.

#### Optional: generate a dataset.toml for the target root

With `write_dataset_toml=true`, once the export finishes the §3.9 generator produces a `dataset.toml` for
the **target directory** (`resolution` = the target size, `caption_extension` = the one used when
attaching), written at the target root. A failure is only explained in `summary.dataset_toml_warnings` and
does not affect the images already exported.

**An existing `dataset.toml` is not replaced** (2026-09-19). It may be hand-tuned, so the rule this job
already applies to the images it exports (`unique_destination`) applies to the generated file too: the run
leaves the file untouched, writes nothing, reports `summary.dataset_toml_skipped=true` and puts the reason
(with the path) in `dataset_toml_warnings`. `overwrite_dataset_toml=true` is the explicit way through;
that path copies the previous file to `<name>.bak` **first** - byte-level, and never replacing an existing
backup, the same `core/captions.backup_file` the caption write paths use - and reports where it went in
`summary.dataset_toml_backup`. `dataset_toml_skipped` exists so the panel can tell "left alone" from
"failed" instead of painting one line for both.

#### Error codes

`outside_roots` 403 / `not_found` 404 / `not_a_directory` 400 / `target_inside_root` 400 / `no_images` 400 /
`not_a_file` 400 / `unsupported_image` 400 / `image_outside_root` 400 / `unknown_job` 404 /
`validation_error` 422 (out-of-range arguments or unknown keys, shaped uniformly by app.py).
`no_images` now means "the request listed no images" (2026-09-20); the image-list checks are answered
**after** the two directory checks, so a bad target is still reported as a target problem.

#### Frontend

A new "Scale" tab (`web/js/scale.js`): the images come from **the column's shared scope strip**
(`web/js/scope.js`: picked / directory / directory+subdirectories / filter result), the same instance the
tagger, the batch panel and the crop panel read, and it is snapshotted when the run starts; the panel
subscribes to it, so changing the scope while this tab is open moves the Start gate immediately. Start is
**disabled while the scope is empty**, and the panel says why instead of quietly widening to the whole
directory. The source directory the target tree mirrors is the open one; the target folder reuses §4.12's
directory picker (picking a directory outside the allowlist adds it as a root, the same flow as switching
datasets); the target resolution is **shared** with the §4.9 export panel; "do not enlarge images smaller
than the target" is checked by default; format PNG / JPEG / WebP + quality + optimized compression;
optionally generate dataset.toml, with a second, dependent checkbox for replacing one that is already at
the destination (disabled while generation is off). A progress bar + current file + failure list + result
summary. **There is no extra-file input box.** Behavioral fixture: `test/fixtures/scale_harness.mjs` (a fake
DOM + fake control + fake fetch + fake EventSource, really running `scale.js`).

**The parameters are remembered** (2026-09-21). The user's report: they came back as the defaults on every
page load. The shared store (`web/js/trainparams.js`) is what remembers them - one localStorage key
(`kdt.trainParams`), read when the store is built and written whenever a setter accepts a value - so the
scaling panel, the §4.9 export panel and the crop panel (which inherits the same pipeline settings) are all
seeded from it, and there is no second copy for a panel to keep. Remembered: target resolution, caption
extension, resample / format / quality / optimize / no-upscale, and the scaling panel's own
"generate dataset.toml". Deliberately **not** remembered: the target directory (a stored destination is a
location nobody chose in this session) and `overwrite_dataset_toml` (a standing permission to replace a
hand-tuned file - it is offered per run, and the run that skips the file says so). A stored name the current
build does not offer (an older key, a hand-edited entry) reads as the default rather than reaching a
`<select>` with no such option and a 422; a corrupt, absent or unreadable entry reads as "nothing stored".

### 4.15 Scroll Memory for Directory Navigation (requirement added 2026-09-19)

**The problem to solve**: the user's words, "the path navigation experience of the various components
needs work; when going back up it does not keep the current list's scroll area at the position of the
folder you are in / selected". Every list is rebuilt wholesale on navigation (`replaceChildren`), and the
scroll container goes back to the top with it - the left column's list has **219** entries, so after going
back up the folder just entered is not in the viewport.

**The only implementation: `web/js/navscroll.js`** (a leaf module, it does not touch the DOM, makes no
requests, contains no copy). Four consumers (the left-column directory list `app.js` / the center folder
cards `gallery.js` / the breadcrumb dropdown `app.js` / the directory picker `picker.js`) share the one
copy; each writing its own is not allowed:

| Export | Semantics |
|---|---|
| `childOnPath(parent, child)` | the **first segment** of `child` under `parent` ("the subdirectory just came out of"); returns `null` when not a strict descendant. Rebuilt with `parent`'s own separators and drive-letter casing, so it matches the backend's `dirs[].path` byte for byte |
| `samePath(a, b)` | path equality ignoring separators and case; two empty strings are not the same directory |
| `createScrollMemory(limit=200)` | directory → the last `scrollTop`. For an unknown directory `recall()` returns **`null`** (`0` is a legitimate position); beyond the cap the oldest entry is dropped |
| `revealRow(scroller, row, {align})` | scrolls a row into the **specified** scroll container. **Must not use `scrollIntoView`** (it scrolls ancestors along with it, including the page itself); `center` (the default) centers it to give context, `nearest` is for the keyboard cursor (minimal movement); returns `false` and **does not touch** the container when geometry cannot be measured |

**Rule** (`reveal = childOnPath(new directory, departure directory)`): only **upward** navigation ("up",
a breadcrumb jump to an ancestor) is non-null; downward / refresh / same-level jumps are all `null`.

- if `reveal` is non-null and that row exists → scroll to it and mark it with the `is-revealed` highlight
  (the position has to explain itself);
- otherwise fall back to **where that directory last stopped** (refresh and jumping back to the original
  directory also do not bounce to the top);
- **reveal takes priority over memory** (getting the order backwards is the same as not doing this
  requirement).

**Landing spots and highlight**: `.list button.is-revealed` / `.dir-card.is-revealed .dir-thumbs` /
`.picker-dir.is-revealed`; the breadcrumb dropdown reuses the existing `.crumb-item.is-current` and scrolls
to the current entry **when it opens** (otherwise it sits at the top with 200+ sibling directories).

**Along the way**: in-place re-rendering on a language switch goes through `repaintDirs()` (repainting no
longer bounces the list back to the top); `gallery.js`'s keyboard cursor used
`scrollIntoView({block:"nearest"})` and now uses `revealRow(..., {align:"nearest"})` - the same minimal
movement, without scrolling the page along with it.

**Deliberately not done**: no reveal for the **roots list** in the left column. It repaints on every
`openDir` (changing only the current highlight), and scrolling to the current root along the way would yank
away a user browsing a different root.

**Criteria**: `test/test_web_navscroll.py` (wiring + CSS + really running the module),
`test/fixtures/navscroll_harness.mjs`; the behavioral criteria are extended inside the existing fixtures
`picker_harness.mjs` / `folder_grid_harness.mjs` rather than starting a new set.

### 4.16 The Dataset Root's `dataset.toml`, Read-Only (requirement added 2026-09-20)

**The problem to solve**: the readiness strip's third segment could only say `dataset.toml 未检查`. It
answers the most important of the three readiness questions - *is this dataset trainable?* - and the
app could not answer it, because **no endpoint told it whether the file exists**:
`POST /api/dataset/toml` is pure computation (§4.9) and never reads the file, and the export panel's
`dataset_toml_skipped` describes a run that already happened. A `dataset.toml` written by an earlier
session, by hand, or by the trainer was invisible: the segment reported the state of *the app's own
checks*, and a shrug was the honest maximum.

#### Request: `GET /api/dataset/toml/status?root=`

- `root` is the **dataset root**, resolved through `resolve_under_roots` like every path parameter:
  out of bounds (or no allowlist configured) 403, missing 404, not a directory 400. An empty `root`
  falls back to the first allowlist root (the `/api/fs/list` convention).
- **The subject is the root, not the directory on screen**: the trainer reads `dataset.toml` next to
  the dataset directory, so the answer is about `<root>/dataset.toml`, and a nested subdirectory
  must not answer for the dataset it belongs to.
- The name comes from `core.dataset_toml.DATASET_TOML_NAME` (§3.9) - **defined once** - so the file
  reported here, the file §4.14's export writes at its target root and the name §4.9's frontend
  download uses cannot drift apart.

#### Response: `{root, path, exists, mtime, size}`

- `exists` is true iff a **regular file** is there (`stat.S_ISREG`), which is what the trainer's
  `open()` needs; anything else at that path (a directory of that name, a dangling link) answers
  false, because "a dataset.toml I can read" is the question. `mtime` / `size` are the provenance of
  the fact and are `null` when `exists` is false.
- `path` is reported even when the file is absent, so a caller can see where it looked.
- **The file's contents are never returned**: this is a fact about a file, not a reader of it. There
  is no request body and no write of any kind.

#### Read-only, provably

One `os.stat` on `root / DATASET_TOML_NAME` is the whole interaction with the filesystem. The file is
never opened; a missing one is **not created**; nothing is backed up, touched or renamed; no `.bak`
appears. Writing anything here would answer a different question - saying what is already there is the
endpoint's only job. An explicit `stat` rather than `Path.is_file()` is deliberate: `is_file()`
swallows `OSError`, which would silently turn "the root cannot be read" into "no dataset.toml" - the
one claim this endpoint must not make - so a stat failure is 500 `stat_failed` instead.
`test/test_dataset_status_api.py` fingerprints the directory around every call, and **A43** scores it
end to end.

#### Error codes

`outside_roots` / `paths_unconfigured` 403, `not_found` 404, `not_a_directory` 400,
`stat_failed` 500.

#### Frontend: what the third segment says

`web/js/readiness.js` holds **the fact** (this endpoint, for `app.js`'s `state.root`) apart from
**the export panel's report** (`onReport`, for the directory that panel is pointed at) and paints in
this order:

| state | copy | what it means |
|---|---|---|
| no answer yet | `readiness.tomlUnchecked` | nothing was asked, or the dataset changed under the held answer - the strip's own state, deliberately not a claim about the file |
| the request failed | `readiness.tomlUnread` | "could not ask" is never "not there" (the cache segment's rule); the segment stays clickable and the click retries |
| `exists: false` | `readiness.tomlMissing` | the dataset root has no `dataset.toml` - a fact now, not a guess |
| `exists: true` | `readiness.tomlReady`, or the export panel's own `export.warningsTitle` / `export.skippedTitle` / `readiness.tomlInconsistent` | the file is there; the panel's report can only make the segment **worse**, never better |

- The report counts only while `samePath(report.root, fact.root)`: the panel's subject is the
  directory on screen, the fact's subject is the dataset root, and a report for another directory
  describes another file.
- **A clean report can never produce `dataset.toml 就绪` on its own.** The export endpoint writes
  nothing, so "the checks passed and there is no file" is reported as 未生成 - that disagreement is the
  point, and A43 scores it. (The two surfaces describe the same file; the fact is the one that knows
  about the disk.)
- `app.js` re-asks - a stat, never an optimistic set - when a directory or root is opened, when the
  15 s external-change poll ticks (a `dataset.toml` is invisible to that poll's listing fingerprint,
  which only sees images), when an export that may have written one finishes (`onFinished` from
  `web/js/scale.js`), and when the segment itself is clicked. A fact belonging to another dataset is
  dropped before the new answer arrives - the same rule that empties the export panel's report.
- `readiness.js` itself still fetches nothing: the fact is handed in (`setTomlFile`), so the strip
  remains a painter of values other surfaces own.


### 4.17 Dataset Image Cropping (requirement added 2026-09-20)

**What it is.** A crop tool in the Photoshop sense - a rectangle with eight handles, guide lines, aspect-ratio
presets and a one-click "make the box area the target size" - applied to dataset images **one at a time, with the
user confirming each rectangle**, after which the result is archived **beside its source**.

**Why it exists.** §4.14 scales by area and says so: "the aspect ratio is unchanged, and there is no crop box".
That is the right default for a dataset, and it is not enough for one: a generator's output often has a border,
a watermark strip or a subject that is off-centre, and no uniform scale removes those. The user asked for the crop
half explicitly, with the archive placed **in the image's own directory** so the cropped file is itself a dataset
image (and can therefore be tagged, counted and trained on like any other).

**The pipeline this section freezes** (the user's order, with one deliberate omission):

    crop -> archive beside the source (<stem>_<n><ext>) -> [tagger] -> (nothing else)

Export to a target directory is **not** part of it. The whole filter/crop/scale/tag/export chain is going to be
designed as a workflow later; until then the scaling panel keeps its own export, and this one stops at the
dataset. Adding a copy step here would freeze the wrong order.

#### Request: `POST /api/crop/apply`

    {
      "src": "F:\\datasets\\myset\\1_post\\1.png",
      "crop": {"x": 100, "y": 50, "width": 800, "height": 600},
      "format": "keep",                       // keep | png | jpeg | webp
      "scale": null                           // or {target_width, target_height, no_upscale, resample, quality, optimize}
    }

    200 {
      "src": "...", "archived": "...1_post\\1_1.png", "format": "png",
      "crop": {"x":100,"y":50,"width":800,"height":600},      // what was really applied
      "crop_requested": {"x":100,"y":50,"width":800,"height":600},
      "orig_width": 3000, "orig_height": 2000,                // the display-space frame
      "crop_width": 800, "crop_height": 600,                  // what the resize stage started from
      "output_width": 800, "output_height": 600,
      "scale": 1.0, "resampled": false, "upscale_skipped": false, "copied": false,
      "warnings": []
    }

Errors: 403 `outside_roots` / 404 `not_found` / 400 `not_a_file` / 400 `unsupported_image` /
400 `decode_failed` / 422 for a malformed body (unknown `format`, an extra key, `width`/`height` < 1).
The endpoint is **synchronous** - the unit of work is one image and the unit of truth is one response, so the
dialog learns the archived path, the applied rectangle and the real output size from the call that wrote the file.
It sits at the same trust level as `PUT /api/captions`: the destination is derived from the source (its own
directory), so there is no second user-supplied path and no loopback gate.

**Six rules, each with a reason**

1. **The rectangle is in display coordinates**, i.e. the image as the eye sees it: EXIF orientations 5-8 rotate
   the image by 90/270 degrees, and the confirmation dialog draws the *thumbnail*, which is already transposed
   (`core/thumbs.py`). `GET /api/images/meta` therefore gained `display_width` / `display_height`
   (additive §4 evolution) computed by `core/scaler.display_size` - the same function the planner uses, so the
   dialog and the crop cannot disagree about which way up the image is. This is also why the planner measures the
   **display** size: the old code computed the target size from the raw header and then resized the transposed
   image, which squashed any EXIF-rotated photo that was re-encoded.
2. **The rectangle is clamped, not rejected.** The box is dragged over a preview that is at most 1600 px wide, so
   "one or two pixels outside the frame" is a rounding artefact of the interaction, not a client bug. The applied
   rectangle comes back in `crop` and a `warnings` entry says it was clamped. A box whose `width` or `height`
   is below 1 is a bug and is a 422 instead - silently turning it into a one-pixel image would hide it.
3. **The archive is always numbered**: `<stem>_1<ext>`, then `_2`, … (`core/scaler.numbered_destination`).
   The bare name is deliberately **not** tried first (which is the one difference from §4.14's export rule):
   the archive lives in the same directory as its own source, and with `format="keep"` a source of another
   format would otherwise produce a bare `4.png` next to `4.bmp` and read as that file. Existing files are
   never overwritten, and **the source image is never modified** - not its bytes, not its mtime.
4. **`format: "keep"` keeps the source's format *and* its suffix spelling** (.jpeg stays .jpeg, .JPG stays .JPG).
   A format Pillow reads but this tool does not write (BMP) falls back to PNG **and says so in `warnings`** -
   a silent format change in a dataset is worse than a sentence.
5. **No caption is inherited.** The source's sidecar is not copied: the archive is a new image, and duplicating a
   caption would make "the tags describe this image" quietly false (the crop may have removed half of what the
   tags describe). This also makes the optional tagger step the single writer of that sidecar, so §4.4's
   `write_mode` and its backup net apply to it unchanged.
6. **`scale` is present or absent, never a flag**: `null` writes the crop's own pixels (`resampled:false`,
   `scale:1.0`), an object area-normalizes **from the cropped size** exactly as §4.14 defines it. Absent means the
   resize stage is skipped entirely rather than "normalized to itself", so no float rounding can touch the pixels.
   Consequently a crop box whose area equals the target area (`target_width x target_height`) comes out at
   `scale == 1.0` with `resampled == false` - which is what makes the dialog's "match the target area" button
   the no-loss way to reach the training resolution.

**The frontend contract (frozen because the interaction is the feature)**

- **The image list comes from the column's shared scope strip** (`web/js/scope.js`: picked / directory /
  directory+subdirectories / filter result), snapshotted when the run starts. New files created by the run are
  therefore never picked up mid-run.
- **One dialog per image, and the box is carried over.** Same display resolution: the identical rectangle.
  Different resolution: the same **relative** window, same centre, and - when a ratio is locked - the same ratio
  (the width fraction drives, the height follows). This is the rule the batch-crop world settled on (Lightroom's
  copy-settings and Capture One's apply-crop behave this way): a pixel rectangle copied verbatim lands somewhere
  else entirely on another resolution, while "the same fraction of the frame" is what cropping a dataset means.
- **Aspect presets**: free / source / 1:1, 3:2, 2:3, 4:3, 3:4, 5:4, 4:5, 16:9, 9:16. Picking one rebuilds the box
  to the largest rectangle with that ratio that fits; a locked ratio survives handle drags (edges grow the other
  axis symmetrically around the centre, corners drive whichever axis moved further).
- **"Match the target area"**: the box area becomes `target_width x target_height` with the ratio kept and the
  centre kept; if the frame cannot hold it the box is reduced, keeping the ratio, and the dialog **says so**
  instead of leaving the user to notice later.
- **Guide lines are drawn and snapped to**: the frame edges, the centre and the two thirds, within 6 **display**
  pixels (converted to source pixels against the drawn size), so snapping feels the same on a 512 px and a
  6000 px image. The numeric X/Y/W/H fields are the exact path, and the keyboard path: arrows move by 1
  (Shift: 10), Alt+arrows resize from the bottom-right corner, Enter confirms, Esc ends the run.
- **Each confirmed image is written immediately.** The dialog is the progress display; closing it early (Esc or
  the end button) keeps everything already archived and the summary says how many. There is no "apply" at the
  end and no rollback: nothing was overwritten, so there is nothing to roll back.
- **The tagger step is a reuse**: after the dialog closes, `POST /api/autotag/run` is called with the archived
  paths and **the Tagger panel's current settings** (model, thresholds, postprocess, write_mode) - one job for the
  whole batch, one shelf row, §4.4's cancel and stream. No second tagging implementation, and no second model
  picker to drift away from the first.

**Known limits (deliberate, do not "fix" quietly)**

- Re-running the crop over the same directory treats the archives as sources (`foo_1.png` becomes
  `foo_1_1.png`). There is no resume, no "already archived" detection and no heuristic to exclude
  `_N` names: the naming rule is the user's, and guessing which `_N` files are archives would sooner or later
  exclude a real image. A run that was interrupted is best finished by looking at the names.
- The preview is the 1600 px thumbnail (`THUMB_SIZE.PREVIEW`), so precision comes from the numeric fields and
  the snapping rather than from the pixels on screen. A full-resolution viewer is what the zoom surface is for,
  and it is not wired into the dialog.

## 5. Acceptance Criteria

Each one must be an **executable criterion**, not "looks right". The spec gives the criterion, the
measurement gives the result.

| # | Criterion | Who verifies |
|---|---|---|
| **A1** | **Read-only boundary (relative fingerprint)**: run the full read-only flow against the reference dataset (list directories / thumbnails / metadata / tag frequency / tagger preview), **sample once with `--out` before starting and `--check` the same file after everything finishes**; the sha256 must be byte-for-byte identical (the `_a1_dataset_untouched` session fixture in `acceptance/`; that the guard is really armed is proven by `test_A1_dataset_read_only_guard_is_armed`). **It does not compare against the 2026-09 snapshot in the repo** - the dataset is alive, and a red snapshot criterion means the data changed, not the code (§0.4) | me |
| **A2** | `iter_images(<dataset root>)` returns **0** (the trainer's glob does not recurse); the per-subdirectory counts **sum to the recursive count**. **No golden number**: the dataset is alive, and a stored count (1653) turned this red the moment the crop tool added an image - see §0.4 | me |
| **A3** | `list_subdirs(<dataset root>)` **excludes** `cache_text_encoder` / `latent_cache` / hidden directories, and is non-empty. Not a length either: the committed sample dataset carries a `cache_text_encoder/` and a `latent_cache/` on purpose, so the exclusion is really exercised | me |
| **A4** | `resolve_under_roots` **raises `PathOutsideRoots` for all** of `../..`, absolute paths outside the roots, and symlink escapes; a normal path inside a root gets the resolved absolute path back | me |
| **A5** | Cache invalidation along three paths: **normal** - after changing a caption the `_anima_te.safetensors` disappears and `WriteResult.cache_removed` is non-empty; **failure** - when the cache file is locked `cache_remove_failed` is non-empty and the caption content is still written correctly; **recovery** - after deleting the whole `cache_text_encoder/`, `find_stale_encoder_caches` returns empty | me |
| **A6** | Split rule and round-trip: `split_tags(t)` must equal the trainer's rule `[x.strip() for x in t.split(",") if x.strip()]`; for an ordinary tag set (with spaces / `\(` `\)` / Chinese and Japanese / weight syntax) `write→read` is item-by-item equal; **writing a tag containing a comma must raise ValueError** (on disk the trainer would read it as two tags) | me |
| **A7** | Atomic write: no residual temp files during writing (after writing, the number of extra files in the directory = 0); when writing to a read-only caption file fails, **the original file content is unchanged** | me |
| **A8** | `preprocess` shape/value assertions: shape `(448,448,3)`, dtype `float32`, max **255.0** (not divided by 255); **use a 64×64 square solid-red image** to assert **ch0≈0 and ch2≈255** (BGR, with no padding interference); use a 100×300 rectangular solid-red image to assert the pad pixels **= 255** | me |
| **A9** | Thumbnails: a 6000×7000 image yields a 320px thumbnail; a second call hits the cache (no re-decoding, evidenced by the cache file's mtime not changing); **the cache directory is not inside the dataset directory** | me |
| **A10** | WD14 end to end: infer 20 real images with the local `wd-swinv2-tagger-v3`, all returning a non-empty tag list; two inferences of the same image are **completely identical** (determinism) | me |
| **A11** | Vocabulary: the local swinv2-v3 CSV has **10861 lines total** (general 8106 / character 2751 / rating 4); the first 4 lines are rating, named `general, sensitive, questionable, explicit` | me |
| **A12** | HTTP end to end: after starting the service, `/api/health`, `/api/roots`, `/api/fs/list`, `/api/images/thumb`, `/api/captions`, `/api/tags/autocomplete` are all 2xx and their JSON shapes match §4 | me |
| **A13** | Out-of-bounds requests: `/api/fs/list?path=C:\Windows` → **403**; `/api/images/thumb?path=../../etc/passwd` → **403** | me |
| **A14** | Documentation gate `pytest test/test_docs_index.py -q` is still green | me |
| **A15** | `pytest test/ -q` is all green; and **no test writes to the reference dataset** (A1 is its backstop) | me |
| **A23** | **The generated dataset.toml must be accepted by the trainer's own validator**: run `<trainer venv>python -m library.config_util --support_dreambooth <generated toml>`, **exit 0**, and the blueprint's subset count == 219 with every image_dir matching. This is the only criterion in this repo "scored by the real consumer" | me |
| **A23b** | The same content, if the backslashes are written into a basic string **without escaping**, must make the validator **exit≠0** - proving our escaping is not redundant (a criterion is only meaningful if it can fail) | me |
| **A22** | Preview limit: 33 images → **400 `too_many_images`**, rejected **before path resolution** (33 nonexistent paths should also yield 400, not 404) - proving an over-limit request does not even stat | me |
| **A20** | The whole download chain is verified with a **local fake endpoint** (not really downloading 1.26 GB): a temporary HTTP service serves `/api/models/<repo>` and `resolve/main/<file>`; assert listing parsing, streaming to disk, monotonic progress callbacks, byte-count verification, idempotent skipping when already present, the `.part` being cleaned up on failure, and **automatic fallback to the second endpoint when the first one breaks** | me |
| **A21** | The registry recognizes both the HF cache layout and the **flat layout** (`<root>/<model_id>/model.onnx`) - a model placed by hand must be discoverable, otherwise the "if the download fails, download it yourself and drop it in" fallback is a lie | me |
| **A25** | The promise of the `models/` directory must be true: it is search root **position 0**, it is the **download destination**, and it contains **empty** placeholder files (git does not track empty directories, so the explanation is carried by the filenames) | me |
| **A24** | Autocomplete must support **substring** matching (prefix hits sort first, case-insensitive, deterministic results): `genshin` must find `ganyu_(genshin_impact)` - no tag in the vocabulary starts with genshin. This is a contract gap found by the **end-to-end smoke test** rather than a unit test | me |
| **A17** | Parenthesis escaping matches the repo's convention: `keqing_(genshin_impact)` from the vocabulary, after `remove_underscore` + `escape_parens`, must end up exactly `keqing \(genshin impact\)`; and with `escape_parens=False` it stays as it was | me |
| **A16** | Constant consistency: `cache_invalidation.IMAGE_EXTS == paths.IMAGE_EXTS`; `CAPTION_EXT_DEFAULT` is equal in all three modules; `captions.SEPARATOR == ", "`. §3.0 forbids core modules importing each other, which duplicates constants, and the duplication must be checked or it will drift | me |
| **A27** | **Runtime-editable allowlist**: at startup the allowlist holds only the dataset root; after `POST /api/roots` adds a second directory, **within the same process** (no restart) `GET /api/fs/list?path=<new root>` goes from **403 to 200**; after `DELETE /api/roots?path=<new root>` it returns to 403. **The criterion must include the "it was 403 before adding" step** - asserting only 200 would let an implementation that "only takes effect after a restart" pass | me |
| **A28** | The allowlist's two rejection paths: adding a nonexistent path / a file → **400 `not_a_directory`** with the allowlist unchanged; deleting the last entry → **400 `last_root`** with both the allowlist and `roots.txt` unchanged | me |
| **A29** | The two persistence files are written differently: `roots.txt` is **rewritten whole** to match the allowlist (deleted path lines disappear); when `model_paths.txt` is added to or removed from, **the comment block and other path lines are untouched** (assert on a sample with `#` comments and semicolon lines). Falsification: change `model_paths.txt` to a whole-file rewrite and this must turn red | me |
| **A30** | **Model scan directories are runtime-editable**: put a fake model in the flat layout (`<root>/<model_id>/model.onnx` + a `*.csv` in the same directory) in a temp directory; after `POST /api/autotag/model-roots`, **without restarting**, that model's `installed` in `GET /api/autotag/models` goes from false to true, and its row's `models` in `GET /api/autotag/model-roots` contains it; after `DELETE` it goes back to false and the line disappears from the file | me |
| **A31** | **The color decision must not be `None`**: `resolve_use_colors()` yields True / False / an `isatty`-based decision for the three states `--color always/never/auto` respectively; and in the `log_config` passed to uvicorn, the `use_colors` of both the `default` and `access` formatters must be **booleans**. `None` makes uvicorn guess from `sys.stdout.isatty()` itself - while in cmd.exe `isatty()` is true but VT is off (measured: `GetConsoleMode=0x3` without `0x4`, and simultaneously `DefaultFormatter(use_colors=None).use_colors=True`), so the terminal shows the literal `←[32mINFO←[0m` | me |
| **A32** | **Cross-platform script set**: the root `start.sh` / `setup_env.sh` / `setup_env_cn.sh` and their `.bat` counterparts are symmetric; `scripts/start.sh` and `scripts/start.ps1` behave the same (both read `roots.txt`, both pick an unused port, both support `--dry-run` to print the command lines that would run without starting); `scripts/setup_env.ps1` / `.sh` both support `-Index/--index pypi|cn` and `-DryRun/--dry-run`, and dry-run prints **all** the `DRY_RUN_INDEX` positions (regular profile = the official source; China profile = the China mirrors first, `pypi.org` as the fallback); `bash -n` passes for every `.sh` (Git Bash 5.2 on this machine); **all `.sh` are LF and all `.bat` are CRLF** (with LF in a `.bat`, cmd.exe mis-parses parenthesized blocks) | me |
| **A33** | **Graphical directory picker** (§4.12): `GET /api/fs/pick` can list directories **outside** the allowlist, while `/api/fs/list` in the same directory still returns 403 (the allowlist was not quietly opened); **directories only** - `note.txt` is not in `dirs`, and each item has only `{name,path}`; at a filesystem root `parent == null`; a non-empty `roots` is provided alongside `dirs` | me |
| **A34** | **Folder nine-grid preview** (§4.13): each `dirs[]` item carries `preview_images` - **at most 9**, **scanning only this level**, in natural order; an empty directory yields `[]`; `image_count`'s old meaning is unchanged. The frontend `gallery.js` renders cards as 1/4/9 grids by count, and only when `state.images.length === 0`; the fixture `test/fixtures/folder_grid_harness.mjs` really runs `gallery.js` (9 thumbnails, directory name and count, click callback, releasing bitmaps on directory change) | me |
| **A33b** | **Loopback gate**: with `--host 0.0.0.0`, `GET /api/fs/pick`, `POST /api/fs/pick/mkdir` and the four path-config mutation endpoints (`POST`/`DELETE /api/roots`, `POST`/`DELETE /api/autotag/model-roots`) **all** return 403 `remote_path_config_disabled`, and `/api/health`'s `path_config.allowed` is false. **Gating only the picker and not roots changes (or not directory creation) is no gating at all**, so all six must be covered by the same criterion | me |
| **A33c** | **Escape hatch**: `--allow-remote-path-config` (or `KOHYA_TAGGER_ALLOW_REMOTE_PATH_CONFIG=1`) is the only way to open it up, and must be off by default; with it on the six above work again and `path_config.allowed` is true | me |
| **A33d** | **Creating a directory in the picker** (§4.12, 2026-09-19): `POST /api/fs/pick/mkdir` really touches disk; creates **one level** only; an existing directory of the same name is idempotent with `created:false`; an existing **file** of the same name gets 409 `already_exists` with its bytes unchanged; a name with a path component such as `../escaped` gets 400 `bad_name` and produces **nothing** outside the parent. The frontend fixture `picker_harness.mjs` really runs `picker.js`: Enter in the path box navigates, Enter in the input does not close the dialog, "Create directory" calls the endpoint and enters the new directory, and an empty name sends no request | me |
| **A35** | **The batch-edit preview must equal the write** (§4.5 ③ extension): with `dry_run:true` the caption bytes are unchanged, the mtime is unchanged and the TE cache is still there, and the response's `results[0].after` is **byte-for-byte equal** to the caption read back after a real write - if the preview used its own logic, the diff the user sees and what lands on disk could disagree, and the feature would be worthless. The same criterion also requires `edit_tags` deletions to be **positionally correct** (deleting a tag in the middle does not change the meaning of the tags after it) | me |
| **A35b** | `regex_replace_text`'s whole-segment replacement: commas in the replacement string **are split into new tags by the trainer's rule**; `prepend` puts the new tags at the front | me |
| **A36** | **Image scaling/export** (§4.14): an image with more area than the target is scaled to `new width×new height ≈ the target area` (aspect ratio unchanged, no cropping); an image with less area keeps **the same dimensions** under `no_upscale=true` (the default) and is only enlarged with it off; the caption is copied **byte-for-byte** next to the target image; when the dimensions are unchanged and the format is the same the output is **byte-identical** to the source (no re-encoding); **the export covers exactly the listed images** - one image listed while `root` holds two writes one image and its caption, in the list's order, a duplicate entry is one file, and an empty or absent list is 400 `no_images` with no target directory created (the widened export is unreachable); an image outside `root` is 400 `image_outside_root`; a target directory outside the allowlist gets 403, one inside the source directory gets 400 `target_inside_root`; the target directory is left with no `.tmp-scale-*` and the source mtime is unchanged; with `write_dataset_toml=true` a dataset.toml appears in the target root; the frontend fixture `scale_harness.mjs` really runs `scale.js` over the shared scope control and the request really carries that control's list, **and the panel's parameters survive a reload while the permissions do not** (the fixture seeds nothing but a storage entry and reads the controls and the request back; see §4.14's "The parameters are remembered") | me |
| **A44** | **Image cropping (§4.17)**: `POST /api/crop/apply` writes the rectangle's window **beside the source** as `<stem>_<n><ext>` - `_1`, then `_2`, stepping over a file the user made and **never overwriting it**, leaving the source's bytes, size and mtime untouched and no `.tmp-scale-*` behind; a box that sticks out of the frame is **clamped and reported** (`crop` vs `crop_requested` + a `warnings` entry), not rejected; `format:"keep"` keeps a `.jpeg` source a `.jpeg` archive; **no caption is inherited**; the rectangle is in **display coordinates** (an EXIF orientation-6 photo is cropped the way it is shown); with the scaling half, a box whose area already equals `target_width x target_height` comes out at `scale == 1.0` with `resampled == false`; a source outside the allowlist is 403 and creates nothing. The frontend fixture `crop_harness.mjs` really runs `crop.js` + `cropbox.js` (the confirmed rectangle reaches the request, the box is carried over between images, a locked ratio survives a drag, a failed write stays on the same image, and the tagger step calls the existing `/api/autotag/run` with the Tagger tab's settings) | me |
| **A43** | **The dataset root's `dataset.toml` is a read-only fact (§4.16)**: `GET /api/dataset/toml/status?root=` answers `exists:false` before the file is there and `exists:true` **with that file's real mtime and size** afterwards, at `<root>/dataset.toml`; a nested subdirectory answers about its own file, not the dataset's; an out-of-bounds root is 403. **Read-only, scored by fingerprinting the whole directory around every call**: no file is created (asking about a root without a toml does not create one), no mtime moves, no `.bak` appears, and no byte of the directory changes - the endpoint writes nothing. **The strip does not guess**: an independent node probe over the real `readiness.js` shows a failed request painting `readiness.tomlUnread` (never the "not generated" claim), and a **clean export report with no file on disk painting 未生成, not 就绪** - the report describes a plan, the endpoint describes the file, and the plan may not stand in for it | me |
| **A37** | **Backend messages are localizable (§4.9 supplement 4)**: the `warnings` / `skipped` of `POST /api/dataset/toml` are unchanged, while `warning_items` / `skipped_items` are supplied item-for-item in the same order and length with `text` / `reason` **byte-for-byte identical** to the old arrays; each code's `params` match the frozen table; the error shape gains `params`, and `outside_roots` / `not_found` / `not_a_directory` carry `{"path": ...}`; the frontend uses the translation when `hasKey("msg."+code)` hits and the placeholders are fully filled, otherwise it **displays the backend's original text verbatim** (falsification: change `text` or delete a `msg.*` key and it turns red) | me |

---

## 6. Division of Labor and File Ownership (avoiding parallel conflicts)

At any moment **a file has exactly one owner**. Do not change files owned by someone else; if a change is
needed, raise it in your conclusion.

| Wave | Agent | Owns |
|---|---|---|
| 1 | A | `pyproject.toml` `requirements.txt` `src/kohya_dataset_tagger/__init__.py` `config.py` `core/__init__.py` `core/paths.py` `core/thumbs.py` `api/__init__.py` `api/fs.py` `api/images.py` `test/test_paths.py` `test/test_thumbs.py` |
| 1 | B | `core/captions.py` `core/cache_invalidation.py` `core/vocab.py` `test/test_captions.py` `test/test_cache_invalidation.py` `test/test_vocab.py` |
| 1 | C | `autotag/__init__.py` `autotag/base.py` `autotag/registry.py` `autotag/postprocess.py` `autotag/wd14.py` `test/test_wd14_preprocess.py` `test/test_wd14_postprocess.py` `test/test_wd14_integration.py` |
| 2 | D | `__main__.py` `app.py` `api/captions.py` `api/tags.py` `api/autotag.py` `test/test_api_e2e.py` |
| 2 | E | `web/**` |

`app.py` registers routes by **dynamic discovery** (scanning the modules under `api/` that expose a
`router`), so wave 2 does not have to wait for all of wave 1 to land.

---

## 7. Environment (settled, do not change)

- **Dedicated venv**: the `.venv` at the repository root (Python 3.10.11).
  It is **not** the trainer's venv - this tool's P0 does not need torch, and installing packages into a
  training environment in active use risks breaking training. This corrects the "reuse the venv" in
  [scope-and-decisions](scope-and-decisions.md).
- Dependencies: `fastapi uvicorn pillow numpy onnxruntime pytest httpx`
- **An editable install must be done once**: `.venv\Scripts\python.exe -m pip install -e . --no-deps`.
  The repo uses a `src/` layout, and without the install the
  `python -m kohya_dataset_tagger` written in AGENTS.md and the README reports
  `No module named kohya_dataset_tagger` - **commands in the docs must really run**, which wave 2 found and
  fixed on 2026-09-17. `--no-deps` is deliberate: it does not touch the versions already installed in the
  venv. `*.egg-info/` is already in .gitignore.
- **One-shot setup**: `scripts/setup_env.ps1` (Windows) / `scripts/setup_env.sh` (Linux).
  Idempotent, safe to run repeatedly. `-Gpu auto|cuda|directml|cpu`, default `auto`.
- **Two index profiles (added 2026-09-19)**: the root `setup_env.bat` / `setup_env.sh` are the **regular
  profile** (official pypi.org); `setup_env_cn.bat` / `setup_env_cn.sh` are the **China profile** (the same
  implementation + `-Index cn` / `--index cn`). The China profile falls back in the order
  `USTC -> Aliyun -> pypi.org`, so it can never be worse than the regular one; the environment variable
  `KOHYA_TAGGER_PIP_INDEX=a,b` replaces the whole profile. Every pip call passes an explicit
  `--index-url` and does **not** fall back to the global pip config - which is exactly where "looks hung"
  comes from.

### GPU (measured on this machine, 2026-09-17)

This machine: **RTX 4070 Ti SUPER 16 GB**, driver 610.88, torch 2.7.0+cu128. **No** system CUDA Toolkit,
**no** system cuDNN.

**Clean re-measurement (2026-09-17, second time, after the methodology fix)**:

| Setup | Size | Median | p10 / p90 | 1653 images |
|---|---|---|---|---|
| CPU (ORT default threads) | 15 MB | 0.721 s/image | 0.692 / 0.785 | 19.9 minutes |
| CPU (`intra_op_num_threads=4`) | — | 0.834 s/image | 0.807 / 0.909 | 23.0 minutes |
| `onnxruntime-directml` | 50 MB | 0.192 s/image | 0.179 / 0.208 | 5.3 minutes |
| `onnxruntime-gpu` (CUDA EP) | 245 MB | **0.168 s/image** | 0.159 / 0.179 | **4.6 minutes** |

- **CPU → either GPU is about 3.8-4.3× faster** (not the 5.4× written earlier, which came from dirty data).
- **CUDA is another 14% faster than DirectML** (saving about 42 seconds across the whole library), but it
  costs 195 MB more, adds a torch dependency and needs PATH set. → **Keep DirectML as the Windows default**;
  CUDA is the explicit `-Gpu cuda` option. In other words: **DirectML buys 86% of the benefit for 1/5 of the
cost.** The tagger's own default provider order matches this choice as of 2026-09-19 (CUDA -> DirectML ->
CPU); before that the tagger only ever asked for CUDA + CPU and silently ran on CPU under a DirectML
install - see [changelog/tagger-gpu-provider](changelog/tagger-gpu-provider.md).
  size.**

### Benchmark methodology (`tools/bench_providers.py`, both times we tripped over this)

- **One process per provider**, 16 real images, 4-6 warm-up images **discarded**, 2 rounds of 32 total runs,
  reporting the **median** and p10/p90 (per-image jitter 0.24-0.42, and looking only at the mean hides the
  tail).
- **Warm-up is mandatory**: without it 0.30 is reported as 1.10 s/image, leading straight to the opposite
  conclusion "CUDA is slower".
- **Measure on a quiet machine**: on the same machine with the same code, CPU measured 1.492 s/image with
  the test suite running alongside and 0.721 when quiet - **the 2× difference comes purely from load**. The
  whole earlier "5.4×" came from comparing "a loaded CPU" against "a less loaded GPU".
- Numbers measured while something else runs on the GPU (this machine was gaming at the time, 13-54%
  utilization) are **unusable**; the p10/p90 this time are tight (0.179/0.208), so interference was low, but
  measurements should still be taken while idle where possible.

### The thread knob (correcting workstream C's conclusion)

C measured `intra_op_num_threads=4` as **2.5× faster** than the default when the 16 logical cores were
saturated by other load (3.45 → 1.39 s/image). Re-measured on a relatively idle machine this time, **the
default was faster instead** (0.721 vs 0.834).

→ This knob is a **"machine is busy" mitigation, not a default win**. Do not make it the default; its
reason to exist is so that ORT does not eat every core while you tag and train at the same time.

### Three hard lessons (all paid for in time)

**① pip's global source can throttle a large wheel into unusability, but "which mirror is fast" varies by
machine.**

On 2026-09-17 this machine's `pip config` pointed at `mirrors.aliyun.com`, and measured, it gave
`onnxruntime-gpu` (245 MB) only **0.07 MB/s** (58 minutes, which looks exactly like a hang), while
`files.pythonhosted.org` / `pypi.org` gave **1.45-5.8 MB/s** (about 45 seconds).

On 2026-09-19 the same method (Range 8 MB / 12 s, by a one-off probe) gave different numbers:

| Source | `onnxruntime-gpu` (245 MB) |
|---|---|
| USTC `mirrors.ustc.edu.cn/pypi` | **8.4-11 MB/s** |
| Aliyun `mirrors.aliyun.com/pypi` | 0.86-1.25 MB/s |
| `pypi.org` / `files.pythonhosted.org` | 0.55-1.01 MB/s |
| TUNA `pypi.tuna.tsinghua.edu.cn` | the simple index page is 200, but a direct wheel file is **403** (suspected hotlink protection) |

The conclusion is not "pin one source forever" but **pin explicitly + fall back in order**: the regular
profile has only `pypi.org`, the China profile `USTC -> Aliyun -> pypi.org`. **Without an explicit
`--index-url` it falls back to the global config** - which is where 0.07 MB/s comes from. The setup scripts
(both `.ps1` and `.sh`) now route all four pip call sites through this chain.

**② The CUDA EP wants the traditional search path, not `add_dll_directory`.**
On this machine the cuDNN 9 / CUDA 12 DLLs (`cublasLt64_12.dll` and friends) exist only in **torch's `lib`
directory** (there is no system CUDA Toolkit and no cuDNN). Measured:

| Approach | Result |
|---|---|
| do nothing | load fails → falls back to CPU |
| `os.add_dll_directory(torch/lib)` | **still fails** (ORT's provider DLL uses static imports) |
| **put `torch/lib` on `PATH`** | **`CUDAExecutionProvider` takes effect** |

This is why ComfyUI can run it (it installed torch 2.7.0+cu128, with no `nvidia-*` package in
site-packages).

**③ onnxruntime falls back to CPU silently.** When a GPU provider fails to load it **does not error**, and
`get_available_providers()` still lists CUDA/DirectML (that means "compiled in", not "loadable"); only
`InferenceSession.get_providers()` tells the truth. Inference still runs, just 5× slower.

→ **`tools/check_provider.py` exists for exactly this**: build one session with the real model, report the
**actual** provider, and return 0 only when it is the requested provider. The setup scripts call it for
self-checks. **Any claim that "the provider is enabled" must be based on it.**

### Other measurements

- DirectML compiles operators on first use, so **a benchmark must warm up** (insufficient warm-up reports
  0.30 as 1.10 s/image and yields the opposite conclusion).
- **The CPU and GPU tag sets agree at threshold 0.35**; the **ordering** of the scores across all 10861
  entries differs where they are nearly tied (floating-point differences), which does not affect what lands
  in the captions.
- Once the provider in `.venv` changes, an assertion hardcoding `('CPUExecutionProvider',)` turns red -
  integration tests should assert "at least one provider and inference runs", not pin the name.
- Every command uses `.venv\Scripts\python.exe`. **Do not** install anything into the trainer's venv.

### Console and ANSI (measured 2026-09-17, the first problem the user reported)

Symptom: after double-clicking `start.bat` to start the service, the logs in the console look like they do
not support ANSI. The measured root cause is **two things stacking**:

| Fact | Measured value |
|---|---|
| Does uvicorn emit ANSI | `DefaultFormatter(use_colors=None).use_colors = True` (it only asks `sys.stdout.isatty()`) |
| `isatty()` in a real console | **True** (measured with a real console opened by `Start-Process`) |
| Is VT enabled on the console | `GetConsoleMode(stdout) = 0x3`, **without** `0x4` (`ENABLE_VIRTUAL_TERMINAL_PROCESSING`) |
| Can we enable it ourselves | `SetConsoleMode(h, mode \| 0x4)` → **succeeds** (becomes `0x7`) |

That is: uvicorn emitted color and conhost did not interpret it, so the literal `←[32m` appeared on screen.
**The fix is for the program to enable VT itself, not to turn color off** (the only place outside §4.11 that
touches `__main__.py`, see A31):

- `console.enable_ansi_console()` tries `SetConsoleMode(… | 0x4)` on stdout on Windows and returns "will ANSI
  be interpreted"; failure counts as False (not an error - e.g. when output is redirected there is no
  console anyway).
- The result is passed explicitly to uvicorn's `log_config` (both the `default` and `access` formatters), and
  **is not left to `None`**.
- `--color auto|always|never` lets people force it; `never` is also for CI / log files.

Another easy thing to get wrong: the Chinese in a `.bat` is **UTF-8 without a BOM**, while cmd.exe decodes
it by the console code page (**936** on this machine), so the `echo` lines in `start.bat` come out as
mojibake (Python itself is unaffected - it goes through `WriteConsoleW`, and `sys.stdout.encoding` measures
as `utf-8`). The fix is to **leave only ASCII in a `.bat`** and let PowerShell speak the Chinese.

### Cross-platform launchers (added 2026-09-17)

One set each for Windows and Linux, **symmetric at the root**: `start.bat` / `setup_env.bat` ↔ `start.sh` /
`setup_env.sh`, with the real implementations under `scripts/`. `--dry-run` was **added for acceptance**:
print the command lines that would run and exit, so that Git Bash on Windows can also assert how
`scripts/start.sh` assembles its arguments (it cannot be tested on real Linux, and this machine has no WSL
distribution).

The three things the two must agree on (A32): read the same `roots.txt`; pick a free port and pass it to
`--port`; pass `--dry-run` through to each other. **Model directories are no longer forwarded by the
launcher** (§3.7: the server reads `model_paths.txt` itself).