"""Training-cache invalidation - the enforcer of this repository's invariant number one.

Why it exists
-------------
The trainer's text encoder cache path is **determined only by the image path** (`library/strategy_anima.py:284-293`),
and the validity check only asks whether 4 keys are present (`:295-349`), with **no hash / mtime / sha1 check anywhere in the file**.
So changing a `.txt` without deleting the cache makes training silently use the old caption's text embedding: no error, no warning.
Evidence in .github/memory/encoder-cache-invalidation.md.

Conversely, the latent cache depends only on image pixels and is unrelated to the caption (`strategy_base.py:431`);
it only needs invalidating when parameters like flip_aug / alpha_mask / random_crop change.

Layering constraint (p0-spec §3.0)
----------------------------------
This module is a **leaf** of `core/`: it must not import other core modules. `core/captions.py` imports this module in turn,
so it **cannot** do `from .captions import CAPTION_EXT_DEFAULT` here - which is exactly why the `CAPTION_EXT_DEFAULT` / `IMAGE_EXTS`
below **intentionally duplicate** the same-named constants in `core/paths.py`, not a typo.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

#: Subdirectory name the trainer writes the TE cache into (strategy_anima.py:287).
TE_CACHE_DIRNAME = "cache_text_encoder"

#: Subdirectory name the trainer writes the VAE latent cache into (strategy_base.py:431).
LATENT_CACHE_DIRNAME = "latent_cache"

#: The two TE cache variants: the trainer writes `.safetensors`, but read time looks at both paths
#: (strategy_anima.py:299-300 swaps extensions with splitext). Deleting only one is not enough.
TE_CACHE_SUFFIXES = ("_anima_te.safetensors", "_anima_te.npz")
#: Same value as `CAPTION_EXT_DEFAULT` in core/paths.py (see the layering constraint in the module docstring).
CAPTION_EXT_DEFAULT = ".txt"

#: **Same value** as `IMAGE_EXTS` in core/paths.py (likewise; acceptance A16 compares these two copies value by value):
#: the lowercase allowlist + their uppercase spellings. Aligned with `IMAGE_EXTENSIONS` in train_util.py:108.
_IMAGE_EXTS_LOWER = frozenset({".png", ".jpg", ".jpeg", ".webp", ".bmp"})
IMAGE_EXTS: frozenset[str] = frozenset(
    _IMAGE_EXTS_LOWER | {ext.upper() for ext in _IMAGE_EXTS_LOWER}
)

#: Trainer cache directories to filter (the "entries that must be filtered" from dataset-contract).
_CACHE_DIRNAMES = frozenset({TE_CACHE_DIRNAME, LATENT_CACHE_DIRNAME})

#: Extensions of the latent cache: kohya's general `.npz`, and the `_anima.safetensors` variant.
_LATENT_SUFFIXES = frozenset({".npz", ".safetensors"})


@dataclass(frozen=True)
class InvalidationResult:
    """The result of one invalidation. Non-empty `failed` = the caller must report an error and not treat it as success."""

    removed: tuple[Path, ...]
    failed: tuple[Path, ...]


@dataclass(frozen=True)
class StaleEntry:
    """A possibly stale cache: the caption is newer than the cache, meaning the cache was built **before** the caption change."""

    image: Path
    caption: Path
    cache: Path


def text_encoder_cache_paths(image: Path) -> list[Path]:
    """Return the TE cache **candidate paths** the trainer would read (not guaranteed to exist).

    Rules copied from `library/strategy_anima.py:284-293, 299-300`:
    `<image_dir>/cache_text_encoder/<image_stem>_anima_te.safetensors` and the same-named `.npz`.
    """
    image = Path(image)
    cache_dir = image.parent / TE_CACHE_DIRNAME
    return [cache_dir / (image.stem + suffix) for suffix in TE_CACHE_SUFFIXES]


def invalidate_text_encoder_cache(image: Path) -> InvalidationResult:
    """Delete every TE cache variant for the image.

    A missing candidate **does not count as a failure** (there was nothing to delete); a failed removal (locked / permissions) goes into
    `failed` and is **never swallowed** - a silent failure equals silently using the old tags.
    """
    removed: list[Path] = []
    failed: list[Path] = []
    for candidate in text_encoder_cache_paths(image):
        try:
            candidate.unlink()
        except FileNotFoundError:
            continue
        except OSError:
            failed.append(candidate)
        else:
            removed.append(candidate)
    return InvalidationResult(removed=tuple(removed), failed=tuple(failed))


def latent_cache_paths(image: Path) -> list[Path]:
    """Return the latent cache files that **already exist** for the image (all resolutions / suffixes).

    The latent filename carries the resolution and cache_suffix (`library/strategy_base.py:437-440`):
    `<stem>_<W:04d>x<H:04d><cache_suffix>`; the `_anima` strategy's suffix is
    `_anima.safetensors` (`strategy_anima.py:424`) and kohya's general one is `.npz`.
    The resolution cannot be derived from the image path, so it is discovered with a `<stem>_*` wildcard - which is why, unlike
    `text_encoder_cache_paths`, this returns only files that really exist.
    """
    image = Path(image)
    cache_dir = image.parent / LATENT_CACHE_DIRNAME
    if not cache_dir.is_dir():
        return []
    prefix = image.stem + "_"
    found = [
        entry
        for entry in cache_dir.iterdir()
        if entry.is_file()
        and entry.name.startswith(prefix)
        and entry.suffix.lower() in _LATENT_SUFFIXES
    ]
    return sorted(found)


def invalidate_latent_cache(image: Path) -> InvalidationResult:
    """Delete every latent cache for the image (all resolutions).

    Called only when the subset's flip_aug / alpha_mask / random_crop change; a caption change
    **does not** need to touch it.
    """
    removed: list[Path] = []
    failed: list[Path] = []
    for candidate in latent_cache_paths(image):
        try:
            candidate.unlink()
        except FileNotFoundError:
            continue
        except OSError:
            failed.append(candidate)
        else:
            removed.append(candidate)
    return InvalidationResult(removed=tuple(removed), failed=tuple(failed))


def _iter_images(image_dir: Path, recursive: bool):
    """Yield the image files under a directory, skipping trainer cache directories and hidden directories."""
    if recursive:
        for dirpath, dirnames, filenames in os.walk(image_dir):
            dirnames[:] = sorted(
                name
                for name in dirnames
                if name not in _CACHE_DIRNAMES and not name.startswith(".")
            )
            for name in sorted(filenames):
                if Path(name).suffix.lower() in IMAGE_EXTS:
                    yield Path(dirpath) / name
    else:
        try:
            entries = sorted(os.listdir(image_dir))
        except OSError:
            return
        for name in entries:
            path = image_dir / name
            if path.suffix.lower() in IMAGE_EXTS and path.is_file():
                yield path


def find_stale_encoder_caches(image_dir: Path, *, recursive: bool = False) -> list[StaleEntry]:
    """List the entries where "the caption is newer than the TE cache" - the data source for the status panel and the pre-training notice.

    Criterion (p0-spec §3.3): only `caption.mtime` **strictly later than** `cache.mtime` counts as stale.
    A cache directory that does not exist at all naturally returns empty (that is the "recovery" of the three paths).
    """
    image_dir = Path(image_dir)
    stale: list[StaleEntry] = []
    for image in _iter_images(image_dir, recursive):
        caption = image.with_suffix(CAPTION_EXT_DEFAULT)
        if not caption.is_file():
            continue
        try:
            caption_mtime = caption.stat().st_mtime_ns
        except OSError:
            continue
        for cache in text_encoder_cache_paths(image):
            if not cache.is_file():
                continue
            try:
                cache_mtime = cache.stat().st_mtime_ns
            except OSError:
                continue
            if caption_mtime > cache_mtime:
                stale.append(StaleEntry(image=image, caption=caption, cache=cache))
    stale.sort(key=lambda entry: (str(entry.image), str(entry.cache)))
    return stale
