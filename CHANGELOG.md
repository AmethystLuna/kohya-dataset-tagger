# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The reasoning behind a change (why a threshold is 0.35, why the thumbnail cache lives outside the
dataset) is not repeated here: it lives next to the code, in
[`.github/memory/`](.github/memory/MEMORY.md).

## [Unreleased]

## [0.1.0] - 2026-09-21

The first public release. The `dataset.toml` it generates is scored in acceptance by the trainer's
own `config_util`, not by a fixture of our own.

### Added

- **Directory browsing and gallery** — browse the dataset one level at a time with non-recursive
  counts, preview a folder as a grid of sampled images, and walk the images of a folder with
  thumbnails served from an on-disk cache that lives outside the dataset.
- **Caption editing** — read and write `.txt` sidecars with the trainer's own split rule, tag
  chips with autocomplete and a tag-frequency table, filtering by tag, and a zoom view for looking
  at an image closely.
- **The text-encoder cache is invalidated on every caption write**, and a write that cannot delete
  the matching `cache_text_encoder/<name>_anima_te.safetensors` fails loudly instead of leaving the
  trainer to read stale tags.
- **Batch captioning with WD14** — ONNX inference (CUDA, DirectML or CPU), with preprocessing
  aligned to `sd-scripts` down to the tensor layout, per-category thresholds, tag post-processing
  (underscore removal, bracket escaping, block lists, ordering), dry-run previews, and an
  auto-downloading model registry that recognizes both the HuggingFace cache layout and a flat
  `<root>/<model_id>/` directory.
- **`dataset.toml` generation** — one subset per directory, written only when you ask for it, with
  the previous file backed up first.
- **Dataset checks** — reports the problem classes the trainer accepts silently: missing captions,
  a caption containing `\,` (which the trainer reads as two tags), images larger than
  `max_bucket_reso`, images with an alpha channel, too few images, and empty directories.
- **Image scaling and cropping** (opt-in) — area-normalized export to a target resolution, and a
  crop tool that archives the result beside its source as `<stem>_<N><ext>`, never overwriting and
  never touching the original.
- **Runtime path configuration** — add or remove dataset roots and model search directories from
  the UI; they take effect immediately and are written back to `roots.txt` / `model_paths.txt`.
- **Three languages** — Simplified Chinese, English and Japanese, switchable without a reload.
- **Light and dark themes**, and a command palette (`Ctrl+K`).

### Notes

- Windows is the primary platform (DirectML is the default inference provider, and the setup and
  start scripts come in `.bat` and `.ps1` form). Linux and macOS are supported through the
  equivalent `.sh` scripts.
- The service listens on `127.0.0.1` only. See [SECURITY.md](SECURITY.md) before changing that.
- Requires Python 3.10 or newer. Torch is not needed.

[Unreleased]: https://github.com/AmethystLuna/kohya-dataset-tagger/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/AmethystLuna/kohya-dataset-tagger/releases/tag/v0.1.0
