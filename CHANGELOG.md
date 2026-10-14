# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The reasoning behind a change (why a threshold is 0.35, why the thumbnail cache lives outside the
dataset) is not repeated here: it lives next to the code, in
[`.github/memory/`](.github/memory/MEMORY.md).

## [Unreleased]

## [0.2.0] - 2026-10-14

### Added

- **Tag autocomplete wherever a tag is typed.** The dropdown used to live inside the per-image editor,
  where the batch panel could not reach it; it is one shared module now (`web/js/suggest.js`) that both
  surfaces use. The candidates are the scope's **own tags first** — no request, each showing how many
  images carry it — with the WD14 vocabulary as a fallback, which matters because that vocabulary is
  empty until a tagger model is installed. `find` suggests only tags the images already carry (a tag they
  do not carry is a suggestion that cannot be acted on), `replace` may name a new one, taking a
  suggestion drops a stale preview, and one `Enter` is claimed by exactly one handler.
- **Open the directory on screen in your file manager.** A gallery toolbar button calls the new
  `POST /api/fs/reveal`, which starts `explorer.exe` / `open` / `xdg-open` on an allowlisted path —
  fire-and-forget, because the launcher can exit non-zero even when it worked.
- **The directory picker shows where you are.** A breadcrumb of the path's ancestors sits above the
  listing (each ancestor a button, the directory itself plain text), because the editable path box hides
  the end of a long path — and picking a directory now happens while the dialog is relabelled on a
  language switch.
- **Hand-reorder a scope's tags in the batch panel.** The common-tag list *is* the order editor: each row
  has a drag handle, one container holds a single image's own order, and a multi-image scope gets the
  front / keep / end containers — the middle is never sent, because a batch has no single order to
  impose on captions that differ. The same editor adds a tag to either end (its own permanent `+` row
  appends an editable element), renames by editing a row, deletes by clearing one, and the keyboard
  (`↑`/`↓`, `Home`, `End`) reaches the same three containers. It goes through the same
  preview → backup → write → undo path as every other batch edit.

### Changed

- The batch panel offers **six operations** instead of nine. `append` / `prepend` / `remove` are gone
  from the panel because its common-tag editor covers them (add at either end, clear a row, or replace
  with an empty string for a tag that is not common); the endpoint keeps the three as its primitive set.
- The two order containers are named **前置 / 维持 / 后置** — `Prefix` / `Keep` / `Suffix` in English,
  プレフィックス / 維持 / サフィックス in Japanese.
- `edit_tags` gained `order_first` / `order_last` (move those tags to the front / to the end, everything
  else keeps its own relative order) and `tags_first` / `tags_last` (additions that belong to one of the
  two blocks).

### Fixed

- **The pinned container row could vanish.** A flex item with `overflow: auto` loses its automatic
  minimum size, so once the long middle list overflowed, `flex-shrink: 1` squashed the front/end row to
  zero height — measured in real Chrome: 0 px, against 97 px with the fix. Both rows and lists keep their
  content height now and the group scrolls instead.
- **A list could be squashed to its own `min-height` floor** with its rows overflowing it invisibly (an
  explicit `min-height` is also the floor a flex item may shrink to).
- **`aria-label` was never actually set** in the batch panel's JavaScript-built controls (`aria-label` is
  not a property of an element), so those accessible names did not exist; and an element appended after
  construction had no placeholder until the next relabel, which also rebuilt the row and threw away a
  half-typed tag.
- The spacing around the add row and the empty-list gap (30 px where every other gap is 4 px) are one
  declared value now, and the hint that still pointed at the removed add field was rewritten in all
  three languages.

## [0.1.1] - 2026-10-01

### Fixed

- **`start.bat` refused to start once a dataset root had been added from the UI.** The Windows
  launcher read `roots.txt` with PowerShell's default encoding — the ANSI code page, 936 on a Chinese
  Windows — while the server writes that file as UTF-8 **without a BOM**. A root containing non-ASCII
  characters therefore came back as mojibake and the launcher stopped with "These directories do not
  exist or are not directories". It now reads the file as UTF-8, which keeps the BOM that PowerShell
  itself writes on the first interactive run working too. The same line also crashed on an empty
  `roots.txt` (`Get-Content -Raw` is `$null` there) before it could ask; it asks now.

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

[Unreleased]: https://github.com/AmethystLuna/kohya-dataset-tagger/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/AmethystLuna/kohya-dataset-tagger/releases/tag/v0.2.0
[0.1.1]: https://github.com/AmethystLuna/kohya-dataset-tagger/releases/tag/v0.1.1
[0.1.0]: https://github.com/AmethystLuna/kohya-dataset-tagger/releases/tag/v0.1.0
