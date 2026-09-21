"""Image thumbnails and metadata: GET /api/images/thumb, GET /api/images/meta (spec §4).

- thumb: the size is a **constant** (grid / preview), not an arbitrary pixel value - otherwise the cache would be
  scattered across arbitrary sizes. The response carries an ETag (the cache key) and Cache-Control: immutable; a
  cache hit does not touch Pillow.
- meta: reads only the file header (Image.open is lazy) and does not decode the whole image. has_alpha is the key
  information for alpha_mask and must be reported truthfully - the user must be able to see from the thumbnail/metadata
  which images carry a transparency channel.

The cache directory comes from config.current().thumb_cache_dir; when it falls inside the dataset root, core/thumbs.py
rejects it outright (500 cache_dir_inside_roots), because that would pollute image_dir.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query, Response
from PIL import Image, UnidentifiedImageError

from .. import config
from ..core import paths, scaler, thumbs
from . import error_response, resolve_query_path

router = APIRouter(prefix="/api", tags=["images"])

__all__ = ["router"]

#: Size name -> edge length. Deliberately not an arbitrary pixel parameter (spec §3.4).
_THUMB_SIZES = {"grid": thumbs.GRID_THUMB, "preview": thumbs.PREVIEW_THUMB}

_IMMUTABLE = "public, max-age=31536000, immutable"

#: Fallback for decode failures: PIL's UnidentifiedImageError is an OSError subclass, but
#: DecompressionBombError only inherits from Exception - not listing it explicitly would turn into a 500.
_DECODE_ERRORS = (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError)


def _has_alpha(im: Image.Image) -> bool:
    if im.mode in ("RGBA", "LA", "PA", "RGBa"):
        return True
    # Palette images (PNG-8) express the transparent index via info["transparency"].
    return im.mode == "P" and "transparency" in im.info


def _is_supported_image(name: str) -> bool:
    """Extension allowlist: IMAGE_EXTS contains both upper- and lowercase spellings, so comparing after lowercasing is enough."""
    return Path(name).suffix.lower() in paths.IMAGE_EXTS


@router.get("/images/thumb")
def image_thumb(
    path: str = Query(..., description="Absolute image path"),
    size: str = Query("grid", description="grid=320 / preview=1600"),
) -> Any:
    """Return WebP bytes. Out of bounds 403 / missing 404 / not an image 400."""
    target_px = _THUMB_SIZES.get((size or "grid").lower())
    if target_px is None:
        sizes = "|".join(sorted(_THUMB_SIZES))
        return error_response(400, "bad_size", "size must be one of %s" % sizes, params={"sizes": sizes})
    try:
        image = resolve_query_path(path)
    except paths.PathOutsideRoots as exc:
        return error_response(403, exc.code, str(exc), params=exc.params)
    except FileNotFoundError as exc:
        return error_response(404, "not_found", str(exc), params={"path": str(path)})
    if not image.is_file():
        return error_response(400, "not_a_file", "Not a file: %s" % image, params={"path": str(image)})
    if not _is_supported_image(image.name):
        return error_response(400, "unsupported_image", "Unsupported image extension: %s" % image.name, params={"name": image.name})

    cache_root = config.current().thumb_cache_dir
    try:
        thumb = thumbs.ensure_thumb(image, target_px, cache_root)
    except thumbs.CacheDirInsideRoots as exc:
        return error_response(500, "cache_dir_inside_roots", str(exc))
    except _DECODE_ERRORS as exc:
        return error_response(400, "decode_failed", "Failed to decode image: %s" % exc)

    return Response(
        content=thumb.read_bytes(),
        media_type="image/webp",
        headers={
            "ETag": '"%s"' % thumb.stem,
            "Cache-Control": _IMMUTABLE,
        },
    )


@router.get("/images/meta")
def image_meta(path: str = Query(..., description="Absolute image path")) -> Any:
    """Return width/height / byte size / mtime / color mode / whether it has alpha."""
    try:
        image = resolve_query_path(path)
    except paths.PathOutsideRoots as exc:
        return error_response(403, exc.code, str(exc), params=exc.params)
    except FileNotFoundError as exc:
        return error_response(404, "not_found", str(exc), params={"path": str(path)})
    if not image.is_file():
        return error_response(400, "not_a_file", "Not a file: %s" % image, params={"path": str(image)})
    if not _is_supported_image(image.name):
        return error_response(400, "unsupported_image", "Unsupported image extension: %s" % image.name, params={"name": image.name})

    try:
        with Image.open(image) as im:  # lazy: reads only the file header, does not decode pixels
            width, height = im.size
            display_width, display_height = scaler.display_size(im)
            mode = im.mode
            has_alpha = _has_alpha(im)
    except _DECODE_ERRORS as exc:
        return error_response(400, "decode_failed", "Failed to decode image: %s" % exc)

    stat = image.stat()
    return {
        "path": str(image),
        "width": width,
        "height": height,
        # Section 4.17: width/height are the file's own pixels, display_* is the size **as shown**
        # (EXIF orientation 5-8 swap the two). The crop box is confirmed against what the eye sees,
        # so the dialog measures in display space - the same space core/scaler.py crops in. Both
        # are reported because they answer different questions and neither is derivable from the
        # other on the client side.
        "display_width": display_width,
        "display_height": display_height,
        "bytes": stat.st_size,
        "mtime": stat.st_mtime,
        "mode": mode,
        "has_alpha": has_alpha,
    }
