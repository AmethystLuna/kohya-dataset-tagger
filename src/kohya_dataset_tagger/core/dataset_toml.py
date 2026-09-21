r"""Generate the trainer's dataset.toml - 'one subset per directory' (p0-spec §3.9, §4.9).

Why this module must exist (§1.1)
---------------------------------
The trainer's `glob_images()` (`library/train_util.py:3059`) is **non-recursive**::

    glob.glob(os.path.join(glob.escape(directory), base + ext))

Measured (2026-09-17, read-only)::

    glob_images(<dataset root>)                        -> 0 images
    glob_images(<dataset root>\<subset dir>)  -> 3 images

So when `image_dir` points at the parent directory the training set is **empty**, while a dataset of a few hundred subdirectories
must have one subset per subdirectory. Without this module, even a finished edit would be unusable.

Path escaping is a hard requirement, not a matter of taste (§3.9 measurement table)
-----------------------------------------------------------------------------------
Using the trainer's **own** validator (`python -m library.config_util --support_dreambooth`),
three spellings were measured:

==============================  ==================================================
TOML spelling                    Result from the trainer's `config_util`
==============================  ==================================================
Escaped basic string `"D:\\datasets\\my-lora"`    **exit 0** ✓
Literal string `'D:\datasets\my-lora'`              **exit 0** ✓
Unescaped basic string `"D:\datasets\my-lora"`    **exit 1**
                                `toml.decoder.TomlDecodeError: Reserved escape
                                sequence used`
==============================  ==================================================

This module always emits an **escaped basic string** (:func:`toml_quote`): backslashes doubled and double quotes written as
`\"`. A literal string would also pass, but it breaks as soon as a single quote appears in the path - and the user's existing
the trainer's own generated `dataset.toml` uses escaped basic strings too.

Why `caption_dropout_*` is written conditionally (likewise measured)
--------------------------------------------------------------------
`ConfigSanitizer` only merges these keys into the schema with `--support_dropout`,
and voluptuous's `Schema` defaults to `PREVENT_EXTRA`. Measured, writing the three default values
(`0.0 / 0.0 / 0`) into a subset gives::

    voluptuous.error.MultipleInvalid:
        extra keys not allowed @ data['datasets'][0]['subsets'][0]['caption_tag_dropout_rate']

-> a default value is **not written** (the trainer's dataclass defaults are 0.0/0.0/0 anyway, semantically identical),
and a non-default value is written as-is. Only then can A23's `--support_dreambooth` criterion be exit 0.

:data:`DatasetTomlParams.alpha_mask` (added frozen on 2026-09-17) follows the same rule: **written only when true**;
the default false is not written. It is a subset-level key (the trainer's `DB_SUBSET_DISTINCT_SCHEMA`) and can only go in
`[[datasets.subsets]]` - when adding it, this was confirmed with the trainer's validator: inside a subset it is exit 0, while
putting it in `[[datasets]]` is rejected with `extra keys not allowed`.

Warning: the `[code]` prefix is frozen (§4.9)
--------------------------------------------
Every warning and every `skipped[].reason` must start with `[code]`, where the code comes from §4.9's
frozen table (the :data:`WARN_*` constants). The reason: the frontend groups them by category, and doing keyword
matching against the Chinese wording would silently degrade to 'other warnings' after any rewrite - which would gut the feature's main value.
**An unknown code lands in the 'other' bucket and is not dropped**, so this module adds only two codes outside the table (see below) and uses table codes for everything else.

| code | meaning | this module's constant |
|---|---|---|
| `[empty_dir]` | directory has 0 images (goes into `skipped`) | :data:`WARN_EMPTY_DIR` |
| `[unreadable_dir]` | directory cannot be read (goes into `skipped`) | :data:`WARN_UNREADABLE_DIR` |
| `[missing_caption]` | images are missing captions | :data:`WARN_MISSING_CAPTION` |
| `[escaped_comma]` | a caption contains `\,` | :data:`WARN_ESCAPED_COMMA` |
| `[oversize_image]` | the long edge exceeds `max_bucket_reso` | :data:`WARN_OVERSIZE_IMAGE` |
| `[alpha_images]` | some images have alpha | :data:`WARN_ALPHA_IMAGES` |
| `[too_few_images]` | too few images in a subdirectory | :data:`WARN_TOO_FEW_IMAGES` |

The two codes below were added while implementing this module and were added to §4.9's table on 2026-09-17:

- :data:`WARN_ROOT_IMAGES` - the root directory itself contains images; the trainer does not recurse, so not one of them gets trained.
  Not reporting it is **silent data loss**, which is exactly the failure mode §1.1 exists to prevent.
- :data:`WARN_BROKEN_IMAGE` - a single image whose dimensions cannot be read (corrupt/unsupported). It still counts toward
  image_count (the trainer counts it too, then fails to load it), so it must be called out.

  Why the code is `broken_image` rather than `unreadable_image`: the frontend's classification is
  'code known -> use the code, unknown -> fall back to the keyword table', and the keyword list for the `unreadable_dir`
  group contains `unreadable` - so `[unreadable_image]` would be **misclassified** under the 'directory unreadable' heading.
  Renamed to `broken_image`, it lands cleanly in 'other warnings' and the whole message (code included) is shown verbatim.
  The frontend's `WARNING_GROUPS` had groups for only seven codes at the time, and these two were not in it yet (they temporarily
  fell to 'other' via the keyword fallback); **this was completed on 2026-09-17**, with each of the nine codes having its own bucket.
  This module's bucketing criterion is **adaptive** (the frontend must put a code it recognizes into its own bucket, and only an
  unrecognized code may fall into other), so the two sides never fight each other.

The message body satisfies both bucketing schemes at once: `[code]` is the frontend's primary criterion
(`app.js:warningCategory()` looks at the code first and uses it if recognized), while the keyword table in `strings.js:WARNING_GROUPS`
is only the fallback for 'no code / unrecognized code'. So the body still deliberately keeps words like
`.txt`, `max_bucket_reso`, and `only` - when changing the copy, both sides must be checked.

Structured companion arrays (added 2026-09-19, backward compatible)
-----------------------------------------------------------------
The top-level `warnings: [str]` / `skipped: [{image_dir, reason}]` are **unchanged to the letter**; two **parallel**
arrays are added for the frontend to pick localized copy by `[code]`:

- `warning_items: [{"code", "params", "text"}]` - **same order and length, item by item** as `warnings`,
  with `text` **byte-for-byte equal** to the matching `warnings[i]` (including the `[code] ` prefix), and `code` the code in that prefix.
- `skipped_items: [{"image_dir", "code", "params", "reason"}]` - same order as `skipped`,
  with `reason` byte-for-byte equal to the matching `skipped[i]["reason"]`.

`params` is the **variable data** for that code (the values for the `{name}` placeholders in the frontend templates):

| code | params |
|---|---|
| `missing_caption` | `{"count": int, "extension": str}` |
| `escaped_comma` | `{"count": int}` |
| `oversize_image` | `{"count": int, "max_bucket_reso": int, "max_edge": int}` |
| `alpha_images` | `{"count": int, "alpha_mask": bool}` |
| `too_few_images` | `{"count": int, "minimum": int}` |
| `broken_image` | `{"count": int}` |
| `empty_dir` (warning) | `{"count": int}` (total empty directories skipped) |
| `empty_dir` (skip reason) | `{}` - a fixed short sentence, no variables |
| `unreadable_dir` (warning) | `{"name": str, "detail": str}` |
| `unreadable_dir` (skip reason) | `{}` |
| `root_images` | `{"count": int}` |

The same code is **two different sentences** in 'warning' and 'skip reason' (`empty_dir` really is),
so the frontend takes warnings by `msg.<code>` and skip reasons by `msg.skip.<code>`; **an unrecognized code, a missing translation,
or an unfilled placeholder always falls back to the backend's own text** - never dropped, and never replaced by a bare key.

Read-only
---------
This module is **pure computation**: it only scandirs / stats / reads captions / reads image headers, and never creates, modifies, or deletes
any entry. The §4.9 P0 decision is likewise that the server writes nothing to disk (writing files is a new destruction surface, and a browser download is enough).

Layering (p0-spec §3.0)
-----------------------
Inside `core/` only the single module-level dependency `captions -> cache_invalidation` is allowed, so here
**`core.paths` is not imported at module level**, the same approach as `core/thumbs.py`: import it lazily inside the function.
Enumeration and filtering rules must match `paths` exactly (cache directories, hidden directories, the extension
allowlist, natural sorting); rewriting a copy is bound to drift - which is what the 'single source of truth' principle avoids.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from PIL import Image

__all__ = [
    "CAPTION_EXT_DEFAULT",
    "DATASET_TOML_NAME",
    "MIN_IMAGES_PER_SUBSET",
    "SKIP_REASON_EMPTY",
    "SKIP_REASON_UNREADABLE",
    "WARN_ALPHA_IMAGES",
    "WARN_BROKEN_IMAGE",
    "WARN_EMPTY_DIR",
    "WARN_ESCAPED_COMMA",
    "WARN_MISSING_CAPTION",
    "WARN_OVERSIZE_IMAGE",
    "WARN_ROOT_IMAGES",
    "WARN_TOO_FEW_IMAGES",
    "WARN_UNREADABLE_DIR",
    "WarningItem",
    "DatasetTomlError",
    "DatasetTomlParams",
    "SkippedDir",
    "SubsetPlan",
    "TomlPlan",
    "plan_dataset_toml",
    "toml_quote",
]

#: Default caption extension. Same value as the same-named constants in `core/paths.py` / `core/captions.py` -
#: here too paths cannot be imported (see the layering constraint in the module docstring), and the duplication is **intentional**.
CAPTION_EXT_DEFAULT = ".txt"

#: The file name the trainer reads next to a dataset directory. **Defined once**, here, because
#: three places name the same file: this module generates the text, `api/scale.py` writes it at the
#: export target root, and `api/dataset.py` reports whether it is there (§4.16). Two spellings of
#: the name would let those surfaces describe two different files without anything noticing.
DATASET_TOML_NAME = "dataset.toml"

#: Suggested threshold for 'too few images in a subdirectory' (§3.9). A hint only - it does not block or change num_repeats.
MIN_IMAGES_PER_SUBSET = 5

#: `skipped[].reason`. The frontend uses it as a **group heading** (`groupExportSkipped`; the old one grouped by the whole
#: string, the new one by `[code]`), so each code gets exactly **one** fixed short sentence:
#: putting a directory name or a number in it would split the group apart.
SKIP_REASON_EMPTY = "[empty_dir] the directory contains no images"
SKIP_REASON_UNREADABLE = "[unreadable_dir] the directory cannot be read"

#: Warning category codes (§4.9's frozen table). A message looks like `"[code] <description>"`.
WARN_EMPTY_DIR = "empty_dir"
WARN_UNREADABLE_DIR = "unreadable_dir"
WARN_MISSING_CAPTION = "missing_caption"
WARN_ESCAPED_COMMA = "escaped_comma"
WARN_OVERSIZE_IMAGE = "oversize_image"
WARN_ALPHA_IMAGES = "alpha_images"
WARN_TOO_FEW_IMAGES = "too_few_images"

#: The two codes outside the table (§4.9: an unknown code lands in the 'other' bucket and is not dropped). See the module docstring for why.
#: `broken_image` rather than `unreadable_image`: the latter would be misclassified by the frontend's keyword table
#: under the `unreadable_dir` heading (that keyword is `unreadable`).
WARN_ROOT_IMAGES = "root_images"
WARN_BROKEN_IMAGE = "broken_image"

#: Backslash + comma (two characters). The trainer splits on **bare commas** (`train_util.py:900`),
#: so `a\,b` is read as the two tags `a\` and `b`. Six real occurrences once existed in the dataset
#: (one subset directory, see changelog/caption-defect-fix), all fixed with the user's approval.
_ESCAPED_COMMA = "\\,"

#: Reading a caption tolerates a BOM (same rule as core/captions.py); bad bytes should not fail the whole scan.
_READ_ENCODING = "utf-8-sig"


class DatasetTomlError(ValueError):
    """Cannot produce a usable plan (no subdirectory under the directory contains images, or a parameter is not a finite float)."""


@dataclass(frozen=True)
class DatasetTomlParams:
    """Training parameters (p0-spec §3.9 frozen). Field order and default values are unchanged to the letter.

    The five items starting at `enable_bucket` go into `[general]`; `resolution` / `batch_size` /
    `caption_extension` go into `[[datasets]]` (subsets inherit them); the rest go into
    `[[datasets.subsets]]`.
    """

    resolution: tuple[int, int] = (1536, 1536)
    batch_size: int = 1
    caption_extension: str = CAPTION_EXT_DEFAULT
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
    """The **structured** form of one warning (§4.9 supplement 4, the backward-compatible companion array).

    `text` is the old `warnings[i]` string (including the `[code] ` prefix), byte-for-byte identical;
    `params` is the variable data for that code. See the params table in the module docstring.
    """

    code: str
    params: dict[str, Any]
    text: str


@dataclass(frozen=True)
class SubsetPlan:
    """The plan for one subdirectory -> one subset (p0-spec §3.9 frozen)."""

    image_dir: Path
    image_count: int
    num_repeats: int
    caption_count: int
    warnings: tuple[str, ...]
    #: A structured companion with the same order and length as `warnings` (§4.9 supplement 4); empty by default to stay compatible with old callers.
    warning_items: tuple[WarningItem, ...] = ()


@dataclass(frozen=True)
class SkippedDir:
    """A skipped subdirectory and the reason. §4.9's `skipped[]` is this structure flattened.

    The P0 spec's `TomlPlan` has no such field, while §4.9's response **requires** `skipped`:
    rather than reverse-engineering `warnings` from string prefixes in the API layer (fragile), core hands out the structure directly.
    """

    image_dir: Path
    reason: str
    #: The frozen `[code]` (the same code as in the `reason` prefix) and its variable data (§4.9 supplement 4).
    code: str = ""
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TomlPlan:
    """Everything from one generation run (p0-spec §3.9's five fields + the added `skipped`).

    `warnings` is a **summary** (§4.9 supplement 2): the dataset-level entries first, then each
    `SubsetPlan.warnings`'s entries in subset order. Having them in both places is what the contract requires; the frontend merges and deduplicates.

    `skipped` has a default and comes last: callers constructing by §3.9's five field positions are unaffected.
    """

    root: Path
    subsets: tuple[SubsetPlan, ...]
    toml_text: str
    image_count: int
    warnings: tuple[str, ...]
    skipped: tuple[SkippedDir, ...] = ()
    #: A structured companion with the same order and length as the top-level `warnings` (§4.9 supplement 4).
    warning_items: tuple[WarningItem, ...] = ()


@dataclass(frozen=True)
class _Scan:
    """One subdirectory's scan result (internal, not part of the public contract)."""

    images: int = 0
    captions: int = 0
    missing_captions: int = 0
    escaped_commas: int = 0
    oversize: int = 0
    alpha: int = 0
    unreadable: int = 0
    max_edge: int = 0


def toml_quote(value: str) -> str:
    """Write a string as a TOML **basic string** (including the surrounding double quotes).

    Backslashes and double quotes must be escaped - an unescaped basic string is rejected outright by the trainer's TOML parser
    (`Reserved escape sequence used`, see the measurement table in the module docstring).
    Control characters are escaped per TOML 1.0 (they almost never appear in paths, but the output must be valid TOML).
    """
    out = ['"']
    for char in str(value):
        if char == "\\":
            out.append("\\\\")
        elif char == '"':
            out.append('\\"')
        elif char == "\n":
            out.append("\\n")
        elif char == "\r":
            out.append("\\r")
        elif char == "\t":
            out.append("\\t")
        elif char == "\b":
            out.append("\\b")
        elif char == "\f":
            out.append("\\f")
        elif ord(char) < 0x20 or ord(char) == 0x7F:
            out.append("\\u%04X" % ord(char))
        else:
            out.append(char)
    out.append('"')
    return "".join(out)


def plan_dataset_toml(root: Path, params: DatasetTomlParams) -> TomlPlan:
    """Generate one subset for **each subdirectory** of `root` and produce the full dataset.toml text.

    Pure computation: it writes no files (the §4.9 P0 decision). A missing directory raises `FileNotFoundError`,
    and passing a file raises `NotADirectoryError` (both are turned into HTTP status codes by the caller).

    Raises :class:`DatasetTomlError` when no subdirectory contains images - emitting a 0-subset config
    would write the fault 'the training set is empty' into the file, and it is better to say so here.
    """
    from . import paths  # Local import, see the layering constraint in the module docstring

    directory = _absolute(root)
    subsets: list[SubsetPlan] = []
    skipped: list[SkippedDir] = []
    unreadable: list[tuple[Path, str]] = []
    for child in paths.list_subdirs(directory):
        try:
            scan = _scan_directory(child, params)
        except OSError as exc:
            # Insufficient permissions / the directory vanished mid-scan / a bad link: skip it, but the whole generation
            # **must not** fail, and it must not pass silently either (§4.9's [unreadable_dir]).
            skipped.append(
                SkippedDir(
                    image_dir=child,
                    reason=SKIP_REASON_UNREADABLE,
                    code=WARN_UNREADABLE_DIR,
                    params={},
                )
            )
            unreadable.append((child, "%s: %s" % (type(exc).__name__, exc)))
            continue
        if scan.images == 0:
            # §3.9: a subdirectory with 0 images is **skipped**; an empty subset is never emitted.
            skipped.append(
                SkippedDir(
                    image_dir=child,
                    reason=SKIP_REASON_EMPTY,
                    code=WARN_EMPTY_DIR,
                    params={},
                )
            )
            continue
        subset_warnings = _subset_warnings(scan, params)
        subsets.append(
            SubsetPlan(
                image_dir=child,
                image_count=scan.images,
                num_repeats=params.num_repeats,
                caption_count=scan.captions,
                # The legacy field is a projection of the structured items, byte-for-byte identical (§4.9 supplement 4).
                warnings=tuple(item.text for item in subset_warnings),
                warning_items=subset_warnings,
            )
        )

    loose = paths.count_images(directory)
    if not subsets:
        raise DatasetTomlError(_empty_plan_message(directory, loose, len(skipped)))

    summary_warnings = _summary_warnings(loose, skipped, unreadable, subsets)
    return TomlPlan(
        root=directory,
        subsets=tuple(subsets),
        toml_text=_render_toml(params, subsets, skipped),
        image_count=sum(subset.image_count for subset in subsets),
        warnings=tuple(item.text for item in summary_warnings),
        skipped=tuple(skipped),
        warning_items=summary_warnings,
    )


# ---------------------------------------------------------------------------
# Internals: paths / scanning
# ---------------------------------------------------------------------------


def _absolute(root: Path) -> Path:
    """Absolutize + normalize (`..`, symlinks).

    This is a must: `image_dir` is **for the trainer**, and a relative path would resolve against the trainer's cwd -
    a different directory in a different process. A resolution failure (device name, etc.) falls back to `abspath`,
    the same handling as `core/paths.configure`.
    """
    path = Path(root)
    try:
        return path.resolve()
    except OSError:
        return Path(os.path.abspath(str(path)))


def _scan_directory(directory: Path, params: DatasetTomlParams) -> _Scan:
    """Scan one subdirectory: image count, caption count, and the four criteria that require opening images.

    `OSError` is caught by the caller (a directory that cannot be read is not '0 images'; the two are handled differently).
    """
    from . import paths  # Local import, see the layering constraint in the module docstring

    images = paths.iter_images(directory)
    captions = missing = commas = oversize = alpha = unreadable = 0
    max_edge = 0
    for image in images:
        # The caption path rule matches the trainer: <same dir>/<image stem><caption_extension>.
        # caption_extension is concatenated as-is, **no dot added** - the trainer also concatenates it as-is, and when
        # the user writes "txt" we must report the same 'missing caption' the trainer would, not guess for them.
        caption = image.with_name(image.stem + params.caption_extension)
        if caption.is_file():
            captions += 1
            if _has_escaped_comma(caption):
                commas += 1
        else:
            missing += 1

        measured = _measure(image)
        if measured is None:
            unreadable += 1
            continue
        edge, has_alpha = measured
        max_edge = max(max_edge, edge)
        if edge > params.max_bucket_reso:
            oversize += 1
        if has_alpha:
            alpha += 1

    return _Scan(
        images=len(images),
        captions=captions,
        missing_captions=missing,
        escaped_commas=commas,
        oversize=oversize,
        alpha=alpha,
        unreadable=unreadable,
        max_edge=max_edge,
    )


def _has_escaped_comma(caption: Path) -> bool:
    """Whether `\\,` appears in the caption.

    Reads text only, with no unescaping or splitting - the criterion is simply what the trainer will read (§3.2's split rule).
    """
    try:
        text = caption.read_text(encoding=_READ_ENCODING, errors="replace")
    except OSError:
        return False
    return _ESCAPED_COMMA in text


def _measure(image: Path) -> tuple[int, bool] | None:
    """`(long edge, has alpha)`; returns `None` when the image header cannot be read.

    `Image.open` reads only the file header (JPEG up to SOF, PNG up to IHDR) and does not decode pixels -
    measured at about 0.33 s for 1653 images with a warm cache. A single bad file must never fail the whole generation,
    so the exception is swallowed here and the caller records a warning.
    """
    try:
        with Image.open(image) as opened:
            width, height = opened.size
            bands = opened.getbands()
            transparent = "transparency" in opened.info
    except Exception:  # noqa: BLE001 - bad file / unsupported format, see the docstring
        return None
    return (max(int(width), int(height)), "A" in bands or transparent)


# ---------------------------------------------------------------------------
# Internals: warnings (the `[code]` prefix is §4.9's frozen contract)
# ---------------------------------------------------------------------------


def _message(code: str, params: dict[str, Any], body: str) -> WarningItem:
    """`"[code] description"` + structured params (§4.9 supplement 4).

    The prefix shape is frozen: the frontend recognizes only the code inside the brackets. `text` and the legacy `warnings` entry are
    **byte-for-byte identical**, so old and new clients see the same message.
    """
    return WarningItem(code=code, params=params, text="[%s] %s" % (code, body))


def _subset_warnings(scan: _Scan, params: DatasetTomlParams) -> tuple[WarningItem, ...]:
    """Subset-level warnings. The order is fixed (matching §3.9's entry order), guaranteeing the same input produces the same file."""
    items: list[WarningItem] = []
    if scan.missing_captions:
        items.append(
            _message(
                WARN_MISSING_CAPTION,
                {"count": scan.missing_captions, "extension": params.caption_extension},
                "%d image(s) have no caption (the sidecar <image name>%s does not exist); the trainer will train on the empty caption anyway"
                % (scan.missing_captions, params.caption_extension),
            )
        )
    if scan.escaped_commas:
        items.append(
            _message(
                WARN_ESCAPED_COMMA,
                {"count": scan.escaped_commas},
                "%d caption(s) contain \\, (backslash + comma); the trainer splits on bare commas and will read them as two tags"
                % scan.escaped_commas,
            )
        )
    if scan.oversize:
        items.append(
            _message(
                WARN_OVERSIZE_IMAGE,
                {"count": scan.oversize, "max_bucket_reso": params.max_bucket_reso, "max_edge": scan.max_edge},
                "%d image(s) have a long edge over max_bucket_reso=%d (largest %d) and will be resized during training"
                % (scan.oversize, params.max_bucket_reso, scan.max_edge),
            )
        )
    if scan.alpha:
        # The switch already exists, so the copy has two forms: when off, say how to turn it on; when on, confirm it was written.
        items.append(
            _message(
                WARN_ALPHA_IMAGES,
                {"count": scan.alpha, "alpha_mask": bool(params.alpha_mask)},
                "%d image(s) carry an alpha channel; %s"
                % (
                    scan.alpha,
                    "alpha_mask = true is already written (the trainer will cut out by alpha)"
                    if params.alpha_mask
                    else "turn on alpha_mask to train with alpha-based cutout",
                ),
            )
        )
    if scan.images < MIN_IMAGES_PER_SUBSET:
        items.append(
            _message(
                WARN_TOO_FEW_IMAGES,
                {"count": scan.images, "minimum": MIN_IMAGES_PER_SUBSET},
                "only %d image(s) (fewer than the suggested %d); training may be unstable"
                % (scan.images, MIN_IMAGES_PER_SUBSET),
            )
        )
    if scan.unreadable:
        items.append(
            _message(
                WARN_BROKEN_IMAGE,
                {"count": scan.unreadable},
                "%d image(s) have unreadable dimensions (corrupt file or unsupported format); the size check was skipped" % scan.unreadable,
            )
        )
    return tuple(items)


def _dataset_warnings(
    loose_images: int, skipped: Sequence[SkippedDir], unreadable: Sequence[tuple[Path, str]]
) -> tuple[WarningItem, ...]:
    """Dataset-level warnings: skipped directories, and images in the root directory itself.

    The root layer is §1.1's other form: the trainer does not recurse, so images scattered in the parent directory are
    **not trained at all**, while subsets cover only subdirectories. Not reporting it is silent data loss.

    Empty directories get one **summary** entry (there may be dozens, and reporting each would drown out the other warnings; the directory list is in `skipped`),
    while unreadable directories are reported one by one (rare, and the system error message is useful).
    """
    items: list[WarningItem] = []
    empty_dirs = sum(1 for entry in skipped if entry.reason == SKIP_REASON_EMPTY)
    if empty_dirs:
        items.append(
            _message(
                WARN_EMPTY_DIR,
                {"count": empty_dirs},
                "%d subdirectory(ies) contain no images and were skipped (a 0-image subset would leave the training set empty)" % empty_dirs,
            )
        )
    for directory, detail in unreadable:
        items.append(
            _message(
                WARN_UNREADABLE_DIR,
                {"name": directory.name, "detail": detail},
                "%s cannot be read (%s); skipped" % (directory.name, detail),
            )
        )
    if loose_images:
        items.append(
            _message(
                WARN_ROOT_IMAGES,
                {"count": loose_images},
                "the root directory has %d image(s) not assigned to any subset - the trainer's glob_images does not recurse, "
                "so these images will not be trained" % loose_images,
            )
        )
    return tuple(items)


def _summary_warnings(
    loose_images: int,
    skipped: Sequence[SkippedDir],
    unreadable: Sequence[tuple[Path, str]],
    subsets: Sequence[SubsetPlan],
) -> tuple[WarningItem, ...]:
    """§4.9 supplement 2: the top-level `warnings` is a summary that includes each `subsets[].warnings`'s entries."""
    items = list(_dataset_warnings(loose_images, skipped, unreadable))
    for subset in subsets:
        items.extend(subset.warning_items)
    return tuple(items)


def _empty_plan_message(root: Path, loose_images: int, skipped_dirs: int) -> str:
    """The explanation for a 0-subset result. The two causes are handled completely differently, so they are written separately."""
    if loose_images:
        return (
            "%s has no subdirectory containing images, but the root directory itself has %d image(s). The trainer's "
            "glob_images() does not recurse: for a flat directory point image_dir straight at it, whereas this tool's P0 only emits"
            "'one subset per directory'." % (root, loose_images)
        )
    return (
        "%s has no subdirectory containing images (%d directories skipped), so no subset can be produced. The trainer's "
        "glob_images() does not recurse, so please make sure the right dataset directory was chosen." % (root, skipped_dirs)
    )


# ---------------------------------------------------------------------------
# Internals: render TOML
# ---------------------------------------------------------------------------


def _toml_bool(value: bool) -> str:
    return "true" if value else "false"


def _toml_float(value: float) -> str:
    """A float literal. `inf`/`nan` are not valid TOML, so they are rejected outright rather than writing a bad file."""
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise DatasetTomlError("not a finite float, cannot write it into TOML: %r" % (value,))
    return repr(number)


def _render_toml(
    params: DatasetTomlParams, subsets: Sequence[SubsetPlan], skipped: Sequence[SkippedDir]
) -> str:
    """Render the full dataset.toml (LF newlines, one trailing newline).

    The structure follows `training-ui/templates/dataset_template.toml`: `[general]` holds the dataset parameters
    that are inherited downward, `[[datasets]]` holds resolution/batch_size/caption_extension,
    and each subdirectory gets one `[[datasets.subsets]]`.
    """
    total_images = sum(subset.image_count for subset in subsets)
    total_captions = sum(subset.caption_count for subset in subsets)

    lines = [
        "# Generated by Kohya Dataset Tagger - can be handed straight to the trainer (library/config_util.py).",
        "# The trainer's glob_images() scans one level only (library/train_util.py:3059), so here one subdirectory"
        " is one subset:",
        "# pointing image_dir at the parent directory yields 0 images.",
        "# %d subset(s) / %d image(s) / %d caption(s)." % (len(subsets), total_images, total_captions),
    ]
    if skipped:
        lines.append("# Another %d subdirectory(ies) contain no images and were skipped." % len(skipped))
    lines.extend(
        [
            "# Paths are escaped basic strings (backslashes doubled): an unescaped basic string is rejected by the trainer's TOML parser.",
            "# caption_dropout_* and alpha_mask are written only when non-default"
            " (the trainer accepts the former only with --support_dropout).",
            "",
            "[general]",
            "enable_bucket = %s" % _toml_bool(params.enable_bucket),
            "bucket_no_upscale = %s" % _toml_bool(params.bucket_no_upscale),
            "min_bucket_reso = %d" % params.min_bucket_reso,
            "max_bucket_reso = %d" % params.max_bucket_reso,
            "bucket_reso_steps = %d" % params.bucket_reso_steps,
            "",
            "[[datasets]]",
            "resolution = [%d, %d]" % (params.resolution[0], params.resolution[1]),
            "batch_size = %d" % params.batch_size,
            "caption_extension = %s" % toml_quote(params.caption_extension),
        ]
    )

    for subset in subsets:
        lines.extend(
            [
                "",
                "  [[datasets.subsets]]",
                "  # %d image(s) / %d caption(s)" % (subset.image_count, subset.caption_count),
                "  image_dir = %s" % toml_quote(str(subset.image_dir)),
                "  num_repeats = %d" % subset.num_repeats,
                "  keep_tokens = %d" % params.keep_tokens,
                "  flip_aug = %s" % _toml_bool(params.flip_aug),
                "  shuffle_caption = %s" % _toml_bool(params.shuffle_caption),
            ]
        )
        if params.caption_tag_dropout_rate:
            lines.append("  caption_tag_dropout_rate = %s" % _toml_float(params.caption_tag_dropout_rate))
        if params.caption_dropout_rate:
            lines.append("  caption_dropout_rate = %s" % _toml_float(params.caption_dropout_rate))
        if params.caption_dropout_every_n_epochs:
            lines.append("  caption_dropout_every_n_epochs = %d" % params.caption_dropout_every_n_epochs)
        if params.alpha_mask:
            lines.append("  alpha_mask = %s" % _toml_bool(params.alpha_mask))

    lines.append("")
    return "\n".join(lines)
