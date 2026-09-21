"""Path allowlist and directory enumeration - the **single source of truth** for every endpoint that accepts a user path.

Why this module exists
----------------------
The trainer's `training-ui/server.js:1800` takes the path the user gave and uses it directly to build file operations, blocking only the literal
`..`, with no "resolve, then assert the prefix" step. So symlinks, junctions, and all sorts of equivalent spellings can bypass it.

This module's approach (spec §3.1):

1. First `Path.resolve()` - expand `..`, symlinks, and junctions into the final real path;
2. Then use `os.path.commonpath` to assert it lands under one of the allowlist roots.

The two steps must come as a pair: a literal check alone cannot stop links, and resolving without comparing has no allowlist.
`commonpath` (rather than `str.startswith`) is used because `...\\sample_dataset_evil` has
`...\\sample_dataset` as a string prefix yet lies outside the allowlist.

Directory enumeration rules (dataset contract)
---------------------------------------------
`cache_text_encoder/`, `latent_cache/`, and hidden directories starting with `.` are always filtered, and the filtering happens
**server-side** - the counting rule must match the UI. Non-image extensions (`.npz`/`.safetensors`) are blocked by the
`IMAGE_EXTS` allowlist, so no extra blocklist is needed.

This module is a leaf: it imports no other core module and does not import FastAPI.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Sequence

__all__ = [
    "IMAGE_EXTS",
    "CAPTION_EXT_DEFAULT",
    "CACHE_DIRNAMES",
    "PathOutsideRoots",
    "configure",
    "get_roots",
    "resolve_under_roots",
    "is_cache_or_hidden",
    "iter_images",
    "list_subdirs",
    "count_images",
]

#: Lowercase baseline; the allowlist set itself holds both cases (spec §3.1).
_IMAGE_EXTS_LOWER = frozenset({".png", ".jpg", ".jpeg", ".webp", ".bmp"})

#: Image extension allowlist, including uppercase variants. Aligned with IMAGE_EXTENSIONS in library/train_util.py:108.
IMAGE_EXTS: frozenset[str] = frozenset(_IMAGE_EXTS_LOWER | {e.upper() for e in _IMAGE_EXTS_LOWER})

#: Default extension for the caption sidecar. The real value comes from the subset's `caption_extension`.
CAPTION_EXT_DEFAULT = ".txt"

#: Trainer-generated cache directory names that must be filtered out.
CACHE_DIRNAMES: frozenset[str] = frozenset({"cache_text_encoder", "latent_cache"})

#: Directory allowlist. Module-level state written by `configure()` - the single source of truth for the whole process.
_roots: tuple[Path, ...] = ()

#: Natural sort: puts `2.jpeg` before `10.jpeg` (plain lexicographic order would reverse them).
_DIGITS_RE = re.compile(r"(\d+)")


class PathOutsideRoots(ValueError):
    """The resolved path is not under any allowlist root (out of bounds, `..` escape, link escape, no root configured).

    `code` / `params` are structured payloads (p0-spec §4.9 supplement 4): the frontend
    takes localized copy by `msg.<code>` + `params`. `str(exc)` (that is, `message`) and the original strings at the three raise
    sites are **byte-for-byte unchanged**, as the fallback when the code is not recognized or the placeholders cannot be filled -
    the structured fields are the source of truth for data, and the fallback strings coexist long-term with no removal plan.

    The three raise sites each get a stable code: allowlist not configured `paths_unconfigured` (params empty),
    resolution failed `bad_path`, out of bounds `outside_roots`. The api layer passes both fields through **verbatim** and no longer
    hardcodes code/message itself.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "outside_roots",
        params: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.params: dict[str, Any] = dict(params or {})


def _sort_key(path: Path) -> tuple:
    """Case-insensitive natural sort key (Windows filenames are case-insensitive, so sorting should match).

    Sorts by **each path segment**: when listing images recursively, directories group first (`1_post/1.jpeg` before
    `2_post/1.jpeg`), and within a group natural numeric order applies (`2.jpeg` before `10.jpeg`).
    Listing a single level makes all entries share a parent, equivalent to sorting by filename.
    """
    key: list[tuple] = []
    for part in path.parts:
        for chunk in _DIGITS_RE.split(part):
            key.append((1, int(chunk)) if chunk.isdigit() else (0, chunk.casefold()))
    return tuple(key)


def configure(roots: Sequence[str | Path]) -> None:
    """Set the allowlist roots (overwrite semantics). An empty sequence = nothing is accessible.

    Each root is first `expanduser()`-ed then `resolve()`-ed, deduplicated by normalized path, keeping the given order.
    Roots are **not required to exist**: acceptance and unit tests both configure before use, and a missing directory is a 404 from
    `resolve_under_roots`.
    """
    if isinstance(roots, (str, Path)):  # Guard against slips: configure("F:\\x") would be treated as a character sequence
        roots = [roots]
    resolved: list[Path] = []
    seen: set[str] = set()
    for raw in roots:
        try:
            path = Path(raw).expanduser().resolve()
        except OSError:  # Path unresolvable (device name, etc.): fall back to an absolute path, still never outside the allowlist
            path = Path(os.path.abspath(str(raw)))
        key = os.path.normcase(str(path))
        if key in seen:
            continue
        seen.add(key)
        resolved.append(path)
    global _roots
    _roots = tuple(resolved)


def get_roots() -> tuple[Path, ...]:
    """Current allowlist roots (resolved, deduplicated absolute paths)."""
    return _roots


def _is_under_any(target: Path, roots: Sequence[Path]) -> bool:
    target_key = os.path.normcase(str(target))
    for root in roots:
        root_key = os.path.normcase(str(root))
        if target_key == root_key:
            return True
        try:
            common = os.path.commonpath([target_key, root_key])
        except ValueError:
            continue  # Different drives
        if common == root_key:
            return True
    return False


def resolve_under_roots(raw: str | Path, *, must_exist: bool = True) -> Path:
    """Resolve the user-given path to an absolute path and assert it lies under an allowlist root.

    The order is deliberate: **resolve first, assert second, check existence last**. That way an out-of-bounds path is
    `PathOutsideRoots` (HTTP 403) whether or not it exists, instead of leaking "does this file exist".

    A relative path resolves against the current working directory - it must pass the prefix assertion too, so `../../etc/passwd` is rejected.
    """
    roots = _roots
    if not roots:
        raise PathOutsideRoots(
            "no root allowlist configured (call paths.configure() or config.load() first)",
            code="paths_unconfigured",
            params={},
        )
    try:
        resolved = Path(raw).expanduser().resolve()
    except OSError as exc:
        raise PathOutsideRoots(
            "cannot resolve path %r: %s" % (raw, exc),
            code="bad_path",
            params={"path": str(raw), "detail": str(exc)},
        ) from exc
    if not _is_under_any(resolved, roots):
        raise PathOutsideRoots(
            "path is not inside any allowed root: %s" % resolved,
            code="outside_roots",
            params={"path": str(resolved)},
        )
    if must_exist and not resolved.exists():
        raise FileNotFoundError("path does not exist: %s" % resolved)
    return resolved


def is_cache_or_hidden(name: str) -> bool:
    """Whether a directory entry name belongs to a "must filter" category: a trainer cache directory, or a hidden entry starting with `.`.

    Decided only by the **entry name**, never touching disk. Loose files like `.npz`/`.safetensors` are not blocked here -
    they are not images (the `IMAGE_EXTS` allowlist) or not directories, so they never enter the results anyway.
    """
    if not name:
        return False
    return name in CACHE_DIRNAMES or name.startswith(".")


def _is_image_name(name: str) -> bool:
    ext = os.path.splitext(name)[1]
    return ext in IMAGE_EXTS or ext.lower() in _IMAGE_EXTS_LOWER


def iter_images(directory: Path, *, recursive: bool = False) -> list[Path]:
    """List the image files in a directory (returns `directory / name`, sorted naturally).

    `recursive=False` scans one level only - aligned with the trainer's `glob_images` (`library/train_util.py:3059`),
    so `iter_images(dataset root)` is the **empty list** under the "one directory per post" layout.
    With `recursive=True`, cache/hidden directories are pruned at every level.

    Raises `FileNotFoundError`/`NotADirectoryError` when the directory does not exist (no silent empty list).
    This function **does not do allowlist validation**: it only enumerates; out-of-bounds is stopped by the caller (the api layer) with `resolve_under_roots`.
    """
    directory = Path(directory)
    found: list[Path] = []
    if recursive:
        for dirpath, dirnames, filenames in os.walk(directory):
            dirnames[:] = [d for d in dirnames if not is_cache_or_hidden(d)]
            base = Path(dirpath)
            found.extend(base / name for name in filenames if _is_image_name(name))
    else:
        with os.scandir(directory) as entries:
            for entry in entries:
                if _is_image_name(entry.name) and entry.is_file():
                    found.append(directory / entry.name)
    found.sort(key=_sort_key)
    return found


def list_subdirs(directory: Path) -> list[Path]:
    """List the **subdirectories** of a directory (one level, sorted naturally), filtering cache and hidden directories."""
    directory = Path(directory)
    found: list[Path] = []
    with os.scandir(directory) as entries:
        for entry in entries:
            if is_cache_or_hidden(entry.name):
                continue
            if entry.is_dir():
                found.append(directory / entry.name)
    found.sort(key=_sort_key)
    return found


def count_images(directory: Path, *, recursive: bool = False) -> int:
    """`len(iter_images(...))`; shares the same filtering logic as enumeration, so the rule never drifts."""
    return len(iter_images(directory, recursive=recursive))
