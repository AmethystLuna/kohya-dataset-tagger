"""Dataset image pipeline: optional crop, then area normalization (p0-spec §4.14, §4.17).

Origin and rules
----------------
Ported from the core scaling logic of the author's original standalone scaling tool, rewritten under
this repository's layering and hard constraints.

The pipeline is **crop → area normalization → encode**, in that order, and either half can be left out:

- **Crop (optional, §4.17)**: an explicit `CropRect` in **display coordinates** (the image as the eye sees it, i.e.
  after EXIF orientation is honoured) is cut out first. Without a `crop` argument nothing is cut and the behaviour is
  exactly the old one - the aspect ratio is untouched.
- **Area normalization**: scale = sqrt(target width × target height / (original width × original height)); after scaling,
  new width × new height ≈ the target area (rounding differs by a few pixels, which the bucket absorbs during training).
  With a crop present the *cropped* size is what gets normalized, so "crop box area == target area" means "the scale
  factor is 1.0" and the pixels are not resampled at all.
- **no_upscale=True (new option)**: when the original area is smaller than the target area, **do not enlarge**, keeping the original size
  (the scale factor is clamped to 1.0). The original script always allowed enlargement.
- **Caption copied alongside automatically**: there is no "attachment file extension" input anymore; each exported image gets a same-named
  sidecar beside it (named per caption_extension) whose content is **byte-for-byte identical** to the source caption.
  A source image without a caption is not a failure; it just has nothing attached. **`attach_caption=False`** turns this off:
  the archive written by §4.17 deliberately starts with no caption, so nothing on disk is duplicated by accident and the
  optional tagger step is what produces one.
- **All disk writes are atomic**: same-directory temp file -> os.replace (the same hard constraint as core/captions.py).
  Any failure leaves no temp file.
- **The source file is never changed**: this module only reads source images. What it writes is a *new* file whose name
  no existing file holds (`unique_destination`: _1 / _2 … suffix, never an overwrite) - either in a target directory
  (§4.14) or next to the source image (§4.17's archive).

Layering
--------
This module is a **leaf** of core: at module level it imports only the standard library and Pillow. When captions are needed it imports
lazily **inside the function**, per §3.0 (core/dataset_toml.py does the same with paths).

Concurrency
-----------
Deciding a target filename and rendering it are **two steps**, because they have different concurrency needs:

- `plan_one_image` reads the header and allocates the destination name. `unique_destination` deduplicates per
  directory, so this step runs on **one thread** - that is what keeps the "never overwrite" rule true without a lock.
- `render_one_image` decodes / resamples / encodes. Those are Pillow C calls that release the GIL, so this step
  is what a caller may run in a thread pool. Measured on 16 cores for a 1536x1536 PNG export: 1355 ms/image
  sequential, 415 ms/image at 4 workers, 272 ms/image at 8 (5.0x).

`process_one_image` is the sequential composition of the two and keeps its original behaviour.
"""
from __future__ import annotations

import math
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, NamedTuple

from PIL import Image, ImageOps

__all__ = [
    "FORMAT_EXT",
    "IMAGE_FORMATS",
    "CropRect",
    "ImagePlan",
    "NewDimensions",
    "RESAMPLE_NAMES",
    "ScaleError",
    "ScaleOutcome",
    "calculate_new_dimensions",
    "destination_directory",
    "display_size",
    "numbered_destination",
    "plan_one_image",
    "process_one_image",
    "render_one_image",
    "source_format",
    "unique_destination",
]


class ScaleError(ValueError):
    """Invalid arguments, a source image outside the dataset root, or an unrecognized extension."""


@dataclass(frozen=True)
class CropRect:
    """A crop box in **display coordinates** of one image (origin top-left, unit = source pixel).

    Display coordinates, not raw-file coordinates: an EXIF orientation of 5-8 rotates the image by
    90/270 degrees, so what the user drags over the preview is only the same rectangle as the raw
    pixels after `ImageOps.exif_transpose`. Both the planner and the renderer therefore work in
    display space (see `display_size`), and the frontend never sees a raw-file rectangle.

    The rectangle is a **value**, not a policy: it may stick out of the image, and `clamped` is the
    only thing that makes it legal. The API layer validates the obvious client errors (width/height
    < 1 → 400) and clamps the rest, so a rectangle that is one pixel off still does what the user
    meant instead of failing the whole request.
    """

    x: int
    y: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.x + self.width

    @property
    def bottom(self) -> int:
        return self.y + self.height

    @property
    def area(self) -> int:
        return self.width * self.height

    def as_dict(self) -> dict[str, int]:
        return {"x": self.x, "y": self.y, "width": self.width, "height": self.height}

    def clamped(self, frame_width: int, frame_height: int) -> "CropRect":
        """The same rectangle, moved inside the frame and shrunk to fit it when it is too large.

        The size is clamped first and the position second, which is what makes an oversized
        rectangle end up anchored at the top-left corner instead of hanging off the image: a box
        larger than the frame is always reduced to the whole frame.
        """
        frame_width = max(1, int(frame_width))
        frame_height = max(1, int(frame_height))
        width = min(max(1, int(self.width)), frame_width)
        height = min(max(1, int(self.height)), frame_height)
        x = min(max(0, int(self.x)), frame_width - width)
        y = min(max(0, int(self.y)), frame_height - height)
        return CropRect(x, y, width, height)

    def covers(self, frame_width: int, frame_height: int) -> bool:
        """Whether the rectangle is the whole frame - the case where no pixel is actually dropped."""
        return (
            self.x <= 0
            and self.y <= 0
            and self.right >= int(frame_width)
            and self.bottom >= int(frame_height)
        )


def display_size(image: Image.Image) -> tuple[int, int]:
    """The image's size **as displayed**: EXIF orientations 5-8 rotate it by 90/270 degrees.

    `ImageOps.exif_transpose` swaps width and height for exactly these four values (2/3/4 only
    mirror or rotate by 180 degrees and keep the dimensions), so the planner can predict the size
    the renderer will actually work on without decoding any pixels. Reading EXIF is best-effort: a
    broken EXIF block must not fail an export, it just means "orientation 1".
    """
    width, height = image.size
    try:
        orientation = image.getexif().get(0x0112, 1)
    except Exception:  # noqa: BLE001 - a corrupt EXIF block is not a reason to refuse the image
        orientation = 1
    if orientation in (5, 6, 7, 8):
        return int(height), int(width)
    return int(width), int(height)


#: Output format -> file extension (jpeg uses .jpg, consistent with paths.IMAGE_EXTS).
IMAGE_FORMATS: tuple[str, ...] = ("png", "jpeg", "webp")
FORMAT_EXT: dict[str, str] = {"png": ".png", "jpeg": ".jpg", "webp": ".webp"}
#: Format name passed explicitly to Pillow when saving: the temp file extension is .tmp, so guessing would get it wrong.
_PIL_FORMAT: dict[str, str] = {"png": "PNG", "jpeg": "JPEG", "webp": "WEBP"}

#: Resample name -> Pillow constant. The HTTP layer uses these lowercase names (§4.14 frozen).
RESAMPLE_NAMES: tuple[str, ...] = ("lanczos", "bilinear", "bicubic", "nearest", "box", "hamming")
_RESAMPLE: dict[str, int] = {
    "lanczos": Image.LANCZOS,
    "bilinear": Image.BILINEAR,
    "bicubic": Image.BICUBIC,
    "nearest": Image.NEAREST,
    "box": Image.BOX,
    "hamming": Image.HAMMING,
}

#: Source extension -> output format name. Both .jpeg and .jpg count as jpeg (whether it can be copied directly is decided by format, not extension).
_SOURCE_FORMAT: dict[str, str] = {".png": "png", ".jpg": "jpeg", ".jpeg": "jpeg", ".webp": "webp"}

#: Temp-file prefix/suffix for atomic writes: leading dot (hidden) + .tmp, so directory scans never mistake it for an image or caption.
_TMP_PREFIX = ".tmp-scale-"
_TMP_SUFFIX = ".tmp"


class NewDimensions(NamedTuple):
    """The result of one area normalization.

    scale is the factor **actually used** (after clamping), and upscale_skipped says whether no_upscale clamped it
    from >1 to 1.0 - only together can the two distinguish "was exactly right" from "wanted to enlarge and was blocked".
    """

    width: int
    height: int
    scale: float
    upscale_skipped: bool


@dataclass(frozen=True)
class ScaleOutcome:
    """The result of processing one image. When status is error, error is non-empty and dest is None."""

    status: str
    src: Path
    dest: Path | None
    orig_width: int
    orig_height: int
    new_width: int
    new_height: int
    scale: float
    upscale_skipped: bool
    resized: bool
    copied: bool
    caption: Path | None
    #: The rectangle that was really cut out of the **display-space** frame, or None when nothing was cut.
    crop: CropRect | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    @property
    def crop_width(self) -> int:
        """The width the resize stage started from: the crop box width, or the whole frame."""
        return self.crop.width if self.crop is not None else self.orig_width

    @property
    def crop_height(self) -> int:
        return self.crop.height if self.crop is not None else self.orig_height

    def as_dict(self) -> dict[str, object]:
        """The pure-data form for SSE / tests (paths become str)."""
        return {
            "status": self.status,
            "src": str(self.src),
            "dest": None if self.dest is None else str(self.dest),
            "orig_width": self.orig_width,
            "orig_height": self.orig_height,
            "crop": None if self.crop is None else self.crop.as_dict(),
            "crop_width": self.crop_width,
            "crop_height": self.crop_height,
            "new_width": self.new_width,
            "new_height": self.new_height,
            "scale": self.scale,
            "upscale_skipped": self.upscale_skipped,
            "resized": self.resized,
            "copied": self.copied,
            "caption": None if self.caption is None else str(self.caption),
            "error": self.error,
        }


@dataclass(frozen=True)
class ImagePlan:
    """One image's destination decision, taken before any pixel work happens.

    `plan_one_image` produces it (header read + name allocation); `render_one_image` executes it.
    Carrying the options in the plan is deliberate: a render thread never has to re-derive what the
    allocating thread decided.
    """

    src: Path
    dest: Path
    output_format: str
    resample: str
    quality: int
    optimize: bool
    caption_extension: str
    #: Cut this rectangle out before resizing (display coordinates, already clamped), or None.
    crop: CropRect | None
    #: False = write the image and nothing else (§4.17's archive): the source caption is not copied.
    attach_caption: bool
    orig_width: int
    orig_height: int
    new_width: int
    new_height: int
    scale: float
    upscale_skipped: bool
    resized: bool
    copied: bool


# ---------------------------------------------------------------------------
# Pure computation
# ---------------------------------------------------------------------------


def calculate_new_dimensions(
    width: int,
    height: int,
    target_width: int,
    target_height: int,
    *,
    no_upscale: bool = True,
) -> NewDimensions:
    """Area normalization: return (new width, new height, factor used, whether the original size was kept because enlarging is off).

    When no_upscale=True and the original area is smaller than the target area, the factor is clamped to 1.0 and the size stays as-is.
    round() uses Python's banker's rounding (matching the source script); max(1, …) keeps an extreme
    sliver image from computing a 0 width/0 height.
    """
    width = int(width)
    height = int(height)
    target_width = int(target_width)
    target_height = int(target_height)
    if width <= 0 or height <= 0:
        raise ScaleError("image dimensions must be positive integers, got %dx%d" % (width, height))
    if target_width <= 0 or target_height <= 0:
        raise ScaleError("target dimensions must be positive integers, got %dx%d" % (target_width, target_height))

    raw_scale = math.sqrt((target_width * target_height) / (width * height))
    upscale_skipped = bool(no_upscale and raw_scale > 1.0)
    scale = 1.0 if upscale_skipped else raw_scale
    return NewDimensions(
        max(1, int(round(width * scale))),
        max(1, int(round(height * scale))),
        scale,
        upscale_skipped,
    )


def source_format(path: str | Path) -> str | None:
    """The output format name of the source file (png / jpeg / webp); None when unrecognized."""
    return _SOURCE_FORMAT.get(Path(path).suffix.lower())


def destination_directory(src: str | Path, root: str | Path, target_dir: str | Path) -> Path:
    """The directory under the target root that corresponds to the source image: **rebuilt from the relative structure**.

    root/post/1.png -> target_dir/post; images in the root itself land directly in target_dir.
    A source image outside root raises ScaleError (the caller should guarantee enumeration from root).
    """
    src = Path(src)
    root = Path(root)
    target_dir = Path(target_dir)
    try:
        relative_parent = src.parent.relative_to(root)
    except ValueError as exc:
        raise ScaleError("source image is not under the dataset root: %s" % src) from exc
    return target_dir / relative_parent


def _first_free_named(candidate_for: "Callable[[int], Path]", start: int, reserved: set[str] | None) -> Path:
    """The first candidate whose path no file holds and no earlier allocation reserved.

    `reserved` is for a batch that allocates several destinations before rendering any of them: a name
    planned but not yet on disk does not exist, so `exists()` alone would hand the same name to two
    images. Pass one set for the whole run; the chosen path is added to it. Keys are
    `os.path.normcase`-normalized so the comparison follows the platform's case rules (Windows is
    case-insensitive, POSIX is not) exactly like `Path.exists` does.
    """
    suffix = int(start)
    candidate = candidate_for(suffix)
    while candidate.exists() or (
        reserved is not None and os.path.normcase(str(candidate)) in reserved
    ):
        suffix += 1
        candidate = candidate_for(suffix)
    if reserved is not None:
        reserved.add(os.path.normcase(str(candidate)))
    return candidate


def unique_destination(
    directory: str | Path, stem: str, ext: str, reserved: set[str] | None = None
) -> Path:
    """The export's target filename (§4.14): the bare `<stem><ext>` when it is free, else _1 / _2…

    **Never overwrites** an existing file. The bare name first is right for an export because the
    destination directory is a new tree that merely mirrors the source's names.
    """
    directory = Path(directory)

    def candidate_for(suffix: int) -> Path:
        return directory / (stem + ext if suffix == 0 else "%s_%d%s" % (stem, suffix, ext))

    return _first_free_named(candidate_for, 0, reserved)


def numbered_destination(
    directory: str | Path, stem: str, ext: str, reserved: set[str] | None = None
) -> Path:
    """The archive's filename (§4.17): always `<stem>_<n><ext>`, the first free n >= 1.

    The bare name is deliberately **not** tried, which is the one difference from the export's rule
    and it is what the user asked for ("原文件名 + _数字"). Two reasons it is not just cosmetic:
    the archive lives in the same directory as its own source, so a bare name would sit next to the
    file it came from and read as if it were the original; and because "keep" may change the
    extension (.bmp → .png, .jpeg → .jpeg), a bare candidate can be free while still being the
    source's own stem. Always numbering makes "the file this dialog just wrote" unambiguous.
    """
    directory = Path(directory)

    def candidate_for(suffix: int) -> Path:
        return directory / ("%s_%d%s" % (stem, suffix, ext))

    return _first_free_named(candidate_for, 1, reserved)


# ---------------------------------------------------------------------------
# Process one image
# ---------------------------------------------------------------------------


def _validate_options(output_format: str, resample: str, quality: int) -> int:
    """Shared argument checks for the plan/render pair; returns the normalized quality."""
    if output_format not in FORMAT_EXT:
        raise ScaleError("unknown output format: %r (choose from %s)" % (output_format, list(IMAGE_FORMATS)))
    if resample not in _RESAMPLE:
        raise ScaleError("unknown resample: %r (choose from %s)" % (resample, list(RESAMPLE_NAMES)))
    quality = int(quality)
    if not 1 <= quality <= 100:
        raise ScaleError("quality must be in 1..100, got %d" % quality)
    return quality


def plan_one_image(
    src: str | Path,
    dest_dir: str | Path,
    *,
    target_width: int,
    target_height: int,
    no_upscale: bool = True,
    resample: str = "lanczos",
    output_format: str = "png",
    quality: int = 95,
    optimize: bool = True,
    caption_extension: str = ".txt",
    copy_unchanged: bool = True,
    reserved: set[str] | None = None,
    crop: CropRect | None = None,
    attach_caption: bool = True,
    resize: bool = True,
    dest_ext: str | None = None,
    numbered: bool = False,
) -> ImagePlan:
    """Read one image's header and **allocate its destination name**, touching no pixels and writing nothing.

    Runs on the calling thread on purpose: `unique_destination` decides by asking what already exists,
    so the allocation step of a whole batch must stay on one thread for "never overwrite" to hold
    without a lock. The heavy half is [`render_one_image`][kohya_dataset_tagger.core.scaler.render_one_image].

    `reserved` must be the same set for every image of a batch, otherwise two plans can claim one name
    (see `unique_destination`).

    `crop` (§4.17) cuts a rectangle out **before** the area normalization, so the factor is computed
    from the cropped size: a crop box whose area already equals the target area comes out at scale 1.0.
    The rectangle is clamped into the display-space frame here - the one place that decides what is
    legal - and the clamped value travels in the plan, so the renderer and the caller's report agree.

    `attach_caption=False` writes the image alone (§4.17's archive). `resize=False` skips the
    normalization entirely and the crop's own pixels are the output (§4.17 without the scaling half);
    `target_width` / `target_height` are then unused. `dest_ext` overrides the allocated
    extension (the archive keeps the source's own spelling, .jpeg stays .jpeg) and `numbered=True`
    switches the allocator to the archive rule, `<stem>_<n><ext>` (§4.17) - `dest_ext` exists
    precisely so that `numbered` can be combined with a kept extension.
    """
    src = Path(src)
    dest_dir = Path(dest_dir)
    quality = _validate_options(output_format, resample, quality)

    with Image.open(src) as opened:
        orig_width, orig_height = display_size(opened)
        applied = None if crop is None else crop.clamped(orig_width, orig_height)
        crop_width = orig_width if applied is None else applied.width
        crop_height = orig_height if applied is None else applied.height
        if resize:
            dims = calculate_new_dimensions(
                crop_width, crop_height, target_width, target_height, no_upscale=no_upscale
            )
            resized = (dims.width, dims.height) != (crop_width, crop_height)
        else:
            # The crop's own pixels are the output: scale 1.0 by construction, and "resized" is
            # false even when the crop is smaller than the target - nothing was resampled.
            dims = NewDimensions(crop_width, crop_height, 1.0, False)
            resized = False
        same_format = source_format(src) == output_format
        # A crop that keeps the whole frame drops no pixel, so the no-re-encode shortcut still holds.
        cropped = applied is not None and not applied.covers(orig_width, orig_height)
        allocate = numbered_destination if numbered else unique_destination
        dest = allocate(
            dest_dir,
            src.stem,
            FORMAT_EXT[output_format] if dest_ext is None else dest_ext,
            reserved,
        )

    return ImagePlan(
        src=src,
        dest=dest,
        output_format=output_format,
        resample=resample,
        quality=quality,
        optimize=bool(optimize),
        caption_extension=caption_extension,
        crop=applied,
        attach_caption=bool(attach_caption),
        orig_width=orig_width,
        orig_height=orig_height,
        new_width=dims.width,
        new_height=dims.height,
        scale=dims.scale,
        upscale_skipped=dims.upscale_skipped,
        resized=resized,
        copied=bool(copy_unchanged and not resized and same_format and not cropped),
    )


def render_one_image(plan: ImagePlan) -> ScaleOutcome:
    """Execute a plan: byte-copy or decode -> resample -> encode, then carry the caption along.

    **Thread-safe for plans produced by one plan_one_image loop**, because every plan owns a
    destination name no other plan can have picked. `copy_unchanged` (part of the plan) is what
    keeps an unchanged JPEG from losing quality to a re-encode.
    """
    with Image.open(plan.src) as opened:
        if plan.copied:
            _atomic_copy_file(plan.src, plan.dest)
        else:
            # Re-encoding loses EXIF, so the orientation must be baked into the pixels, otherwise a portrait shot ends up rotated 90 degrees.
            work = ImageOps.exif_transpose(opened)
            if plan.crop is not None:
                # The rectangle is in display coordinates, i.e. after the transpose above - the same
                # frame the planner measured with display_size().
                work = work.crop((plan.crop.x, plan.crop.y, plan.crop.right, plan.crop.bottom))
            if plan.resized:
                work = work.resize((plan.new_width, plan.new_height), _RESAMPLE[plan.resample])
            _atomic_save_image(
                work, plan.dest, plan.output_format, quality=plan.quality, optimize=plan.optimize
            )
    caption = copy_caption(plan.src, plan.dest, plan.caption_extension) if plan.attach_caption else None

    return ScaleOutcome(
        status="ok",
        src=plan.src,
        dest=plan.dest,
        orig_width=plan.orig_width,
        orig_height=plan.orig_height,
        new_width=plan.new_width,
        new_height=plan.new_height,
        scale=plan.scale,
        upscale_skipped=plan.upscale_skipped,
        resized=plan.resized,
        copied=plan.copied,
        caption=caption,
        crop=plan.crop,
    )


def process_one_image(
    src: str | Path,
    dest_dir: str | Path,
    *,
    target_width: int,
    target_height: int,
    no_upscale: bool = True,
    resample: str = "lanczos",
    output_format: str = "png",
    quality: int = 95,
    optimize: bool = True,
    caption_extension: str = ".txt",
    copy_unchanged: bool = True,
    crop: CropRect | None = None,
    attach_caption: bool = True,
    resize: bool = True,
    dest_ext: str | None = None,
    numbered: bool = False,
) -> ScaleOutcome:
    """Scale one image into dest_dir, with a same-named caption alongside.

    With copy_unchanged=True, when the **size is unchanged and the output format matches the source format**, the source file's
    bytes are copied directly: no re-encoding (so JPEG quality is not lost twice), and faster. Only a size change or a format change
    triggers decode -> resample -> encode.

    caption_extension follows the same rule as core/captions.caption_path (lazy import).
    This is the sequential `plan_one_image` -> `render_one_image` composition; a batch caller can call
    the two halves itself to render in parallel (see api/scale.py).
    """
    plan = plan_one_image(
        src,
        dest_dir,
        target_width=target_width,
        target_height=target_height,
        no_upscale=no_upscale,
        resample=resample,
        output_format=output_format,
        quality=quality,
        optimize=optimize,
        caption_extension=caption_extension,
        copy_unchanged=copy_unchanged,
        crop=crop,
        attach_caption=attach_caption,
        resize=resize,
        dest_ext=dest_ext,
        numbered=numbered,
    )
    return render_one_image(plan)


def copy_caption(
    src_image: str | Path, dest_image: str | Path, caption_extension: str = ".txt"
) -> Path | None:
    """Copy the source image's caption **byte for byte** beside the target image; returns None when the source has no caption."""
    caption_path = _caption_path(src_image, caption_extension)
    if not caption_path.is_file():
        return None
    target = _caption_path(dest_image, caption_extension)
    _atomic_copy_file(caption_path, target)
    return target


def _caption_path(image: str | Path, ext: str) -> Path:
    from . import captions  # Lazy import: stay a leaf at module level (§3.0)

    return captions.caption_path(Path(image), ext)


# ---------------------------------------------------------------------------
# Atomic writes
# ---------------------------------------------------------------------------


def _make_temp(directory: Path) -> Path:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=str(directory), prefix=_TMP_PREFIX, suffix=_TMP_SUFFIX)
    os.close(fd)
    return Path(name)


def _unlink_quietly(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def _atomic_copy_file(source: str | Path, target: str | Path) -> None:
    target = Path(target)
    tmp = _make_temp(target.parent)
    try:
        shutil.copyfile(source, tmp)
        os.replace(tmp, target)
    except BaseException:
        _unlink_quietly(tmp)
        raise


def _atomic_save_image(
    image: Image.Image, target: str | Path, output_format: str, *, quality: int, optimize: bool
) -> None:
    """Same-directory temp file -> os.replace. Explicit format=: the temp file extension is .tmp."""
    target = Path(target)
    tmp = _make_temp(target.parent)
    try:
        if output_format == "png":
            image.save(tmp, format=_PIL_FORMAT["png"], optimize=optimize)
        elif output_format == "jpeg":
            if image.mode in ("RGBA", "P", "LA"):
                image = image.convert("RGB")  # JPEG does not support alpha
            image.save(tmp, format=_PIL_FORMAT["jpeg"], quality=quality, optimize=optimize)
        else:  # webp
            image.save(tmp, format=_PIL_FORMAT["webp"], quality=quality, lossless=quality >= 100)
        os.replace(tmp, target)
    except BaseException:
        _unlink_quietly(tmp)
        raise
