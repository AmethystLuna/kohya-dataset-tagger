"""Thumbnail pipeline: draft fast decode -> LANCZOS resize -> WebP on-disk cache.

Design (see `.github/memory/thumbnail-pipeline.md`)
---------------------------------------------------
- **JPEG goes through `draft()` first**: let libjpeg emit 1/2, 1/4, 1/8 sizes via DCT scaling instead of decoding every pixel;
- Then `exif_transpose()`: without it, portrait shots from phones/cameras stay lying on their side in the thumbnail;
- Then `thumbnail(LANCZOS)`: the regular resampler with the best sharpness;
- Output **WebP**: much smaller than PNG and **supports alpha** - the `alpha_mask` information must be visible from the thumbnail,
  so this does not do the trainer's "flatten every PNG into an opaque JPEG" treatment;
- The cache key includes mtime/size/target size/pipeline version, so any change invalidates it automatically;
- The cache directory is **never allowed inside a dataset root** (polluting `image_dir` violates the dataset contract).

The hit path does only one `os.utime` (refresh atime for a future LRU, **keeping mtime unchanged**),
without touching Pillow - that is the watershed for "refresh the page" speed, and the criterion for acceptance A9.

This module is a leaf: at module level it imports no other core module (spec §3.0). The only exception is the cache-directory check,
which imports `paths` **inside the function** (spec §3.4 explicitly requires "validate with paths"),
so that even a missing `paths` does not stop this module from being imported.
"""
from __future__ import annotations

import hashlib
import io
import os
import re
import threading
import time
from pathlib import Path

from PIL import Image, ImageOps

__all__ = [
    "GRID_THUMB",
    "PREVIEW_THUMB",
    "PIPELINE_VERSION",
    "DEFAULT_CACHE_DIRNAME",
    "CacheDirInsideRoots",
    "cache_key",
    "ensure_thumb",
    "clear_cache",
]

#: Grid thumbnail edge length (CSS 160px × DPR 2).
GRID_THUMB = 320
#: Preview image edge length.
PREVIEW_THUMB = 1600
#: Pipeline version, written into the cache key: changing the algorithm/sizes invalidates everything, with no manual cache clear.
PIPELINE_VERSION = 1
#: Default name for the in-repo/temp cache directory (a hidden directory, which dataset enumeration filters automatically).
DEFAULT_CACHE_DIRNAME = ".kohya-dataset-tagger-thumbs"

#: Cache key truncation length (first 16 hex characters of the sha1).
KEY_LENGTH = 16
_WEBP_QUALITY = 85
_WEBP_METHOD = 4

#: Delete only files this module wrote: `<16 hex chars>.webp` and an in-progress temp file.
_OWN_FILE_RE = re.compile(r"^[0-9a-f]{%d}\.webp$" % KEY_LENGTH)
_OWN_TEMP_RE = re.compile(r"^\.[0-9a-f]{%d}\..*\.tmp$" % KEY_LENGTH)

#: Concurrent requests for the same key coalesce onto one lock, so first paint does not decode the same 30 MB image multiple times.
_locks_guard = threading.Lock()
_key_locks: dict[str, threading.Lock] = {}


class CacheDirInsideRoots(ValueError):
    """The cache directory lies inside a dataset allowlist root - it would pollute `image_dir`, so it is rejected outright."""


def _assert_cache_outside_roots(cache_root: Path) -> None:
    """The cache directory must not lie inside any allowlist root (spec §0.3 / §3.4).

    Reuses `paths.resolve_under_roots`'s decision, so "root" has only one definition.
    When no roots are configured (say, in pure unit tests) it does not block.
    """
    from . import paths  # Local import, see the module docstring

    try:
        paths.resolve_under_roots(cache_root, must_exist=False)
    except paths.PathOutsideRoots:
        return
    raise CacheDirInsideRoots(
        "the thumbnail cache directory cannot lie inside a dataset root: %s" % cache_root
    )


def cache_key(image: Path, target: int) -> str:
    """`sha1(f"{abs_path}|{mtime_ns}|{size}|{target}|{PIPELINE_VERSION}")[:16]`.

    Uses the **absolute path**: in the dataset, `1.jpeg` exists in 219 directories, so a filename-only key would overwrite itself.
    """
    path = Path(image)
    st = path.stat()
    absolute = str(path.resolve())
    raw = "%s|%d|%d|%d|%d" % (absolute, st.st_mtime_ns, st.st_size, int(target), PIPELINE_VERSION)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:KEY_LENGTH]


def _has_alpha(im: Image.Image) -> bool:
    if im.mode in ("RGBA", "LA", "PA", "RGBa"):
        return True
    # Palette images (PNG-8) express a transparent index via info["transparency"].
    return im.mode == "P" and "transparency" in im.info


def _render_webp(image: Path, target: int) -> bytes:
    """Decode -> orient -> resize -> encode into WebP bytes. Nothing is written to disk (so unit tests can assert pixels directly)."""
    with Image.open(image) as im:
        im.draft("RGB", (target, target))  # Only effective for JPEG; a no-op for other formats
        # Normalize the color mode before exif_transpose: a palette image's transparent index is not guaranteed to
        # still be in info after the transpose, and WebP cannot store CMYK/YCCK.
        if _has_alpha(im):
            if im.mode != "RGBA":
                im = im.convert("RGBA")
        elif im.mode != "RGB":
            im = im.convert("RGB")
        im = ImageOps.exif_transpose(im)
        im.thumbnail((target, target), Image.LANCZOS)
        buffer = io.BytesIO()
        im.save(buffer, "WEBP", quality=_WEBP_QUALITY, method=_WEBP_METHOD)
    return buffer.getvalue()


def _key_lock(key: str) -> threading.Lock:
    with _locks_guard:
        lock = _key_locks.get(key)
        if lock is None:
            lock = _key_locks[key] = threading.Lock()
        return lock


def _touch(path: Path) -> None:
    """Refresh atime only, keep mtime (A9 uses an unchanged mtime as the evidence of a "cache hit")."""
    try:
        st = path.stat()
        os.utime(path, ns=(time.time_ns(), st.st_mtime_ns))
    except OSError:
        pass  # A failed touch does not affect correctness


def _atomic_write(dest: Path, data: bytes) -> None:
    """Same-directory temp file + `os.replace`: concurrent readers never see half a WebP."""
    tmp = dest.with_name(".%s.%d.%d.tmp" % (dest.stem, os.getpid(), threading.get_ident()))
    try:
        with open(tmp, "wb") as handle:
            handle.write(data)
        os.replace(tmp, dest)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def ensure_thumb(image: Path, target: int, cache_root: Path) -> Path:
    """Return the WebP thumbnail path for `image` at edge length `target`, generating and caching it when necessary."""
    if int(target) <= 0:
        raise ValueError("target must be a positive integer: %r" % (target,))
    image = Path(image)
    cache_root = Path(cache_root)
    _assert_cache_outside_roots(cache_root)
    cache_root.mkdir(parents=True, exist_ok=True)
    key = cache_key(image, target)
    dest = cache_root / ("%s.webp" % key)
    if dest.is_file():
        _touch(dest)
        return dest
    with _key_lock(key):
        if dest.is_file():  # Another thread already produced it while we waited for the lock
            _touch(dest)
            return dest
        data = _render_webp(image, int(target))
        _atomic_write(dest, data)
    return dest


def clear_cache(cache_root: Path) -> int:
    """Empty the cache directory and return the **number of files successfully deleted**.

    Only deletes files this module wrote (`<key>.webp` and an in-progress temp file); every other file is left alone -
    this function walks the filesystem, and pointing it at a dataset directory by mistake would be catastrophic, so:

    1. It is rejected outright when inside a dataset root (`CacheDirInsideRoots`);
    2. The directory itself is kept, only files are deleted;
    3. Unrecognized entries are skipped.
    """
    cache_root = Path(cache_root)
    _assert_cache_outside_roots(cache_root)
    if not cache_root.is_dir():
        return 0
    removed = 0
    with os.scandir(cache_root) as entries:
        names = [entry.name for entry in entries if entry.is_file()]
    for name in names:
        if not (_OWN_FILE_RE.match(name) or _OWN_TEMP_RE.match(name)):
            continue
        try:
            (cache_root / name).unlink()
        except OSError:
            continue  # Skip locked/permission problems; the return value counts only what was really deleted
        removed += 1
    return removed
