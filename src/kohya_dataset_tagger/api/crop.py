"""Dataset image cropping: POST /api/crop/apply (p0-spec §4.17).

What this endpoint does
-----------------------
One image in, one new file out: cut the confirmed rectangle out of the source image, **archive the
result next to the source image** under `<stem>_<n><ext>` (the first free n, never an overwrite),
optionally area-normalizing it to the training resolution on the way. The source image itself is
never touched, and **no caption is inherited**: §4.17's archive starts with no sidecar, so the
optional tagger step (the frontend calls the existing /api/autotag/run with the new files) is what
produces one.

Why one image per request
-------------------------
The crop is confirmed image by image in a dialog, so the unit of work is one image and the unit of
truth is one response: the frontend learns the archived path, the applied rectangle and the real
output size from the same call that wrote the file. It is also fast enough to sit on a request
thread (decode + crop + encode of a 1536x1536 PNG measured at 1.35 s worst case, a JPEG is an order
of magnitude less), and a `def` endpoint is what keeps that work off the event loop.

Contract notes
--------------
- **The rectangle is clamped, not rejected** (§4.17): the confirmation dialog works in display
  pixels of a downscaled preview, so a rectangle that is a pixel or two outside the frame is a
  rounding artefact, not a client bug. width/height < 1 is still a 400 - that is a bug.
- **"keep" is a format, not an extension**: with `format="keep"` the archive keeps the source's own
  format (and its own suffix spelling, so `.jpeg` stays `.jpeg`). A source format Pillow can read
  but we do not write (BMP) falls back to PNG.
- **The archive name is always `<stem>_<n><ext>`** (`scaler.numbered_destination`), starting at 1:
  the bare name is never used, because the archive sits in the same directory as its own source and
  a bare name would read as that source.
- **Both paths go through the allowlist.** The source must exist and be an image; the destination is
  derived from the source (its own directory), so there is no second user-supplied path to validate
  and nothing here can write outside the dataset.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..core import paths, scaler
from . import ensure_roots_configured, error_response, resolve_query_path

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["crop"])

__all__ = ["router", "CropRequest"]

#: `format` accepts the three writable formats plus "keep" (the source's own, §4.17).
KEEP_FORMAT = "keep"
CROP_FORMATS: tuple[str, ...] = (KEEP_FORMAT,) + scaler.IMAGE_FORMATS

#: One archive allocation at a time. `unique_destination` asks the filesystem what exists, so two
#: requests running at once could both be handed `foo_1.png` and `os.replace` would then drop one of
#: them silently. The dialog is sequential, so this lock is never contended in practice - it is here
#: because "never overwrite" has to be true, not usually true.
_ARCHIVE_LOCK = threading.Lock()

#: Failures that mean "this file is not an image we can decode" (the same set api/images.py reports:
#: UnidentifiedImageError is an OSError subclass, but DecompressionBombError only inherits Exception).
_DECODE_ERRORS = (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError)


class CropScaleBody(BaseModel):
    """The optional area-normalization half, with the same defaults as §4.14's export.

    `enabled` does not exist: the presence of this object *is* the switch, so a request can never
    carry "scale" with everything switched off and leave the reader guessing.
    """

    model_config = ConfigDict(extra="forbid")

    target_width: int = Field(1536, ge=8, le=32768)
    target_height: int = Field(1536, ge=8, le=32768)
    no_upscale: bool = True
    resample: str = "lanczos"
    quality: int = Field(95, ge=1, le=100)
    optimize: bool = True

    @field_validator("resample")
    @classmethod
    def _known_resample(cls, value: str) -> str:
        name = str(value).strip().lower()
        if name not in scaler.RESAMPLE_NAMES:
            raise ValueError("resample must be one of %s" % list(scaler.RESAMPLE_NAMES))
        return name


class CropBody(BaseModel):
    """The rectangle, in **display coordinates** of the source image (§4.17).

    x/y may be negative and the box may be larger than the image: it is clamped (see the module
    docstring). width/height must be >= 1 - a zero-sized crop box is a client bug, and silently
    turning it into a 1-pixel image would hide it.
    """

    model_config = ConfigDict(extra="forbid")

    x: int = 0
    y: int = 0
    width: int = Field(..., ge=1)
    height: int = Field(..., ge=1)


class CropRequest(BaseModel):
    """Request body for POST /api/crop/apply (§4.17)."""

    model_config = ConfigDict(extra="forbid")

    src: str
    crop: CropBody
    format: str = KEEP_FORMAT
    scale: CropScaleBody | None = None

    @field_validator("src")
    @classmethod
    def _src_required(cls, value: str) -> str:
        if not str(value).strip():
            raise ValueError("src must not be empty")
        return value

    @field_validator("format")
    @classmethod
    def _known_format(cls, value: str) -> str:
        name = str(value).strip().lower()
        if name not in CROP_FORMATS:
            raise ValueError("format must be one of %s" % list(CROP_FORMATS))
        return name


def _archive_ext(src: Path, output_format: str) -> str:
    """The suffix the archive gets: the source's own spelling when the format is kept."""
    if output_format == scaler.source_format(src):
        return src.suffix
    return scaler.FORMAT_EXT[output_format]


def _resolve_format(src: Path, requested: str) -> tuple[str, list[str]]:
    """The format to write, plus the warnings that explain a fallback.

    "keep" means the source's own format; a source we can read but not write (BMP) lands on PNG -
    saying so is cheaper than a mystery format change in the dataset.
    """
    if requested != KEEP_FORMAT:
        return requested, []
    kept = scaler.source_format(src)
    if kept is None:
        return "png", [
            "the source format (%s) is not one this tool writes; the archive was written as PNG"
            % (src.suffix or "unknown")
        ]
    return kept, []


@router.post("/crop/apply")
def crop_apply(payload: CropRequest) -> Any:
    """Crop one image and archive it beside the source: 200 with the applied rectangle and the real output size."""
    ensure_roots_configured()
    try:
        src = resolve_query_path(payload.src)
    except paths.PathOutsideRoots as exc:
        return error_response(403, exc.code, str(exc), params=exc.params)
    except FileNotFoundError as exc:
        return error_response(404, "not_found", str(exc), params={"path": str(payload.src)})
    if not src.is_file():
        return error_response(400, "not_a_file", "not a file: %s" % src, params={"path": str(src)})
    if src.suffix.lower() not in paths.IMAGE_EXTS:
        return error_response(
            400,
            "unsupported_image",
            "unsupported image extension: %s" % src.name,
            params={"name": src.name},
        )

    output_format, warnings = _resolve_format(src, payload.format)
    requested = scaler.CropRect(payload.crop.x, payload.crop.y, payload.crop.width, payload.crop.height)
    scale = payload.scale
    dest_ext = _archive_ext(src, output_format)

    try:
        with _ARCHIVE_LOCK:
            plan = scaler.plan_one_image(
                src,
                src.parent,
                # The target size is unused when there is no scaling half; 1 keeps it a positive
                # integer so an accidental use cannot divide by zero.
                target_width=scale.target_width if scale else 1,
                target_height=scale.target_height if scale else 1,
                no_upscale=scale.no_upscale if scale else True,
                resample=scale.resample if scale else "lanczos",
                output_format=output_format,
                quality=scale.quality if scale else 95,
                optimize=scale.optimize if scale else True,
                attach_caption=False,
                crop=requested,
                resize=scale is not None,
                dest_ext=dest_ext,
                # §4.17's archive is always numbered; the allocation happens under the lock below.
                numbered=True,
            )
            outcome = scaler.render_one_image(plan)
    except paths.PathOutsideRoots as exc:  # pragma: no cover - resolve_query_path already passed
        return error_response(403, exc.code, str(exc), params=exc.params)
    except _DECODE_ERRORS as exc:
        return error_response(400, "decode_failed", "Failed to crop image: %s" % exc, params={"path": str(src)})

    applied = outcome.crop or requested
    if (applied.x, applied.y, applied.width, applied.height) != (
        requested.x,
        requested.y,
        requested.width,
        requested.height,
    ):
        warnings.append("the crop box was clamped to the image: %dx%d+%d+%d"
                        % (applied.width, applied.height, applied.x, applied.y))
    logger.info(
        "crop %s -> %s (%dx%d+%d+%d -> %dx%d, format=%s, scale=%.4f)",
        src.name,
        outcome.dest.name if outcome.dest else "?",
        applied.width,
        applied.height,
        applied.x,
        applied.y,
        outcome.new_width,
        outcome.new_height,
        output_format,
        outcome.scale,
    )
    return {
        "src": str(src),
        "archived": None if outcome.dest is None else str(outcome.dest),
        "format": output_format,
        "crop": applied.as_dict(),
        "crop_requested": requested.as_dict(),
        "orig_width": outcome.orig_width,
        "orig_height": outcome.orig_height,
        "crop_width": outcome.crop_width,
        "crop_height": outcome.crop_height,
        "output_width": outcome.new_width,
        "output_height": outcome.new_height,
        "scale": outcome.scale,
        "resampled": outcome.resized,
        "upscale_skipped": outcome.upscale_skipped,
        "copied": outcome.copied,
        "warnings": warnings,
    }
