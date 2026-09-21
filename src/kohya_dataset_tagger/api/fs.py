"""Directory browsing and statistics: GET /api/fs/list (spec §4).

Semantics (shares the same filtering logic with core/paths.py; filtering happens server-side, not by hiding things in the frontend)
-----------------------------------------------------------------------------------------------------------------------------------
- dirs lists only **direct** subdirectories, each with its own image count (non-recursive) - the trainer's glob_images
  also scans only one level, so this number is "how many images can be trained once it is configured as a subset";
- each dirs entry also carries preview_images (§4.13): the paths of up to 9 images in that subdirectory, for the center
  gallery's nine-grid preview. It takes the first few in natural order, scans only one level, and **shares the same
  enumeration as image_count** - so it opens no extra images and there is never a "the count says yes but the preview does not";
- recursive=true affects images (recursive collection) and the captions statistics that follow; dirs still stays one
  level - it describes the directory tree, not the image set;
- counts.captions counts the images for which "same-name <image stem> + CAPTION_EXT_DEFAULT exists", and
  missing_captions is the difference; the per-image has_caption in images[] (§4.2) is the same test, so the frontend
  need not send another GET /api/captions for every image. It builds the extension into <stem>.txt here to test existence,
  without reusing core/captions.py (that module belongs to another parallel workflow, and the statistics do not need to read file contents).

Sub-entries outside the allowlist (for example a junction pointing at C:\\Windows) are **not listed**: listing one would
carry an image count while clicking into it would inevitably 403. If it is visible, it must be clickable.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query

from ..core import paths
from . import ensure_roots_configured, error_response, resolve_query_path

router = APIRouter(prefix="/api", tags=["fs"])

__all__ = ["router"]

#: How many images a subdirectory's nine-grid preview takes at most (§4.13). The frontend lays it out 3x3, hence 9.
DIR_PREVIEW_LIMIT = 9


def _under_roots(candidate: Path) -> bool:
    """Whether a child entry is still inside the allowlist (a missing path counts as "in" - it just has not been created yet)."""
    try:
        paths.resolve_under_roots(candidate, must_exist=False)
    except paths.PathOutsideRoots:
        return False
    return True


def _caption_path(image: Path) -> Path:
    """<image stem><caption_extension>: same directory and same stem as the image."""
    return image.with_name(image.stem + paths.CAPTION_EXT_DEFAULT)


def _parent_under_roots(target: Path) -> str | None:
    """Parent directory; returns None once at the allowlist boundary (or when the parent is out of bounds), so the UI does not click into a 403."""
    parent = target.parent
    if parent == target:
        return None
    return str(parent) if _under_roots(parent) else None


@router.get("/fs/list")
def list_directory(
    path: str = Query("", description="Directory to list; empty = the first root in the allowlist"),
    recursive: bool = Query(False, description="Collect images recursively (the directory list still stays one level)"),
) -> Any:
    """List directory contents and statistics. Out of bounds 403 / missing 404 / not a directory 400."""
    # each images[] entry: {name, path, size, mtime, has_caption} (§4.2)
    ensure_roots_configured()
    try:
        target = resolve_query_path(path)
    except paths.PathOutsideRoots as exc:
        return error_response(403, exc.code, str(exc), params=exc.params)
    except FileNotFoundError as exc:
        return error_response(404, "not_found", str(exc), params={"path": str(path)})
    if not target.is_dir():
        return error_response(
            400, "not_a_directory", "Not a directory: %s" % target, params={"path": str(target)}
        )

    dirs: list[dict[str, Any]] = []
    for child in paths.list_subdirs(target):
        if not _under_roots(child):
            continue
        # §4.13: image_count and preview_images come from the same enumeration (count_images is just
        # len(iter_images(...))), so sampling is a single slice - no extra images opened, and never a
        # "the count says yes but the preview does not".
        #
        # Sampling does **not** check the allowlist image by image: the directory itself already passed the
        # allowlist (the _under_roots call above); only a symlink pointing outside could escape, and when that
        # path is actually taken /api/images/thumb 403s and the card hides it as a "thumbnail failed to load"
        # (.is-error) - no clickable illusion. Per-image resolve measured +440 ms (219 directories -> two thousand
        # realpath calls) and per-image lstat +48 ms more, all paying for a case the downstream endpoint already blocks.
        found = paths.iter_images(child)
        preview = [str(image) for image in found[:DIR_PREVIEW_LIMIT]]
        dirs.append({
            "name": child.name,
            "path": str(child),
            "image_count": len(found),
            "preview_images": preview,
        })

    images: list[dict[str, Any]] = []
    captions = 0
    for image in paths.iter_images(target, recursive=recursive):
        if not _under_roots(image):
            continue
        stat = image.stat()
        # The same loop both counts and reports the per-image has_caption (§4.2) - otherwise, to learn which
        # image lacks a caption, the frontend would have to send another GET /api/captions per image (20 per directory = 20 requests).
        has_caption = _caption_path(image).is_file()
        if has_caption:
            captions += 1
        images.append({
            "name": image.name,
            "path": str(image),
            "size": stat.st_size,
            "mtime": stat.st_mtime,
            "has_caption": has_caption,
        })

    return {
        "path": str(target),
        "parent": _parent_under_roots(target),
        "dirs": dirs,
        "images": images,
        "counts": {
            "images": len(images),
            "dirs": len(dirs),
            "captions": captions,
            "missing_captions": len(images) - captions,
        },
    }
