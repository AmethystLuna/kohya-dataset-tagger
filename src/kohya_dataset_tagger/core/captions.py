r"""Reading and writing the caption sidecar - the only module allowed to change dataset text directly.

Contract (.github/memory/dataset-contract.md)
---------------------------------------------
- Path: `<image_dir>/<image stem><extension>`, default `.txt`, UTF-8 without BOM.
- Separator: `", "`. The trainer's caption_separator defaults to `,`, but the shuffle branch **hardcodes**
  `", "` when it recomposes (`library/train_util.py:931`), so this tool reads and writes `", "` uniformly.
- Only the **first line** takes part in training (`library/train_util.py:881`); line 2 onward must warn.
- The split rule is **byte-for-byte identical** to the trainer's: split on **bare commas**, **with no unescaping at all** (p0-spec §3.2 / §5 A6).
  That is how the trainer reads it (`library/train_util.py:900`), and what the editor displays must equal what the trainer will read.
- A tag **must not** contain a comma: a tag containing one gets read as two tags by the trainer once on disk. `write_caption` raises
  `ValueError` outright rather than "fixing" it for the user - escaping (`a\,b`) is read wrong by the trainer anyway, and the user cannot see it.
- Backslashes such as `\(` `\)` are kept **verbatim** (the reference dataset uses them in hundreds of captions). The dirty shapes the
  generator wrote must also be shown as-is: among the 6 .txt files of one subset directory, `fugue \(honkai: star rail)\,`
  has one extra backslash (**open** paren escaped, close paren bare - verified byte by byte), so the trainer reads a tag ending in `\` -
  the editor must display the same thing, otherwise the user cannot see the problem they need to fix.

Invariant number one (p0-spec §3.2)
-----------------------------------
When the content changes and `invalidate=True`, it **must** call
`cache_invalidation.invalidate_text_encoder_cache` and put the result into `WriteResult`.
A removal failure **must not be swallowed** - it goes into `cache_remove_failed` for the caller to report.

All disk writes are **atomic**: a temp file in the same directory -> `os.replace` (atomic only on the same volume), leaving no temp file behind.
When the target file is read-only, `os.replace` raises `PermissionError` (Windows WinError 5); at that point
the original file content **must** stay unchanged - that is also why the temp file must not be written directly to the target.
"""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from . import cache_invalidation

#: Caption separator. Matches the hardcoded value in the trainer's shuffle branch (train_util.py:931).
SEPARATOR = ", "

#: Default caption extension. Same value as the same-named constant in core/paths.py - paths cannot be imported
#: here, see the layering constraint explained in the cache_invalidation module docstring.
CAPTION_EXT_DEFAULT = ".txt"
#: Read: tolerate a BOM (the contract requires none, but a file someone else wrote should not crash the editor).
_READ_ENCODING = "utf-8-sig"

#: Write: always UTF-8 without BOM.
_WRITE_ENCODING = "utf-8"

#: Temp-file prefix/suffix for atomic writes: leading dot (hidden) + non-image extension, so directory scans never mistake it for an image.
_TMP_PREFIX = ".tmp-caption-"
_TMP_SUFFIX = ".tmp"


@dataclass(frozen=True)
class WriteResult:
    """The result of one caption write. When `cache_remove_failed` is non-empty the caller must report an error."""

    caption_path: Path
    changed: bool
    cache_removed: tuple[Path, ...]
    cache_remove_failed: tuple[Path, ...]
    multiline_warning: bool


def caption_path(image: Path, ext: str = CAPTION_EXT_DEFAULT) -> Path:
    """Image -> the caption path with the same stem in the same directory (not guaranteed to exist)."""
    image = Path(image)
    if ext and not ext.startswith("."):
        ext = "." + ext
    return image.with_suffix(ext)


#: Backup suffix: `foo.txt` -> `foo.txt.bak`. It is neither in `IMAGE_EXTS` nor equal to any
#: image's caption sidecar name, so directory scans and `dataset.toml` generation never treat it as data.
BACKUP_SUFFIX = ".bak"


def backup_path(image: Path, ext: str = CAPTION_EXT_DEFAULT) -> Path:
    """Image -> its caption backup path (not guaranteed to exist)."""
    return Path(str(caption_path(image, ext)) + BACKUP_SUFFIX)


def read_caption(image: Path, ext: str = CAPTION_EXT_DEFAULT) -> tuple[str, bool]:
    r"""Read a caption, returning `(text, multiline_warning)`.

    A missing file returns `("", False)` (not an exception) - the HTTP layer derives its `exists` field from this.
    Newlines are normalized to `\n` (universal newlines), so a CRLF file does not pollute the tags.
    """
    path = caption_path(image, ext)
    if not path.is_file():
        return "", False
    text = path.read_text(encoding=_READ_ENCODING)
    return text, _is_multiline(text)


def _is_multiline(text: str) -> bool:
    """The trainer **does not read** line 2 onward, so it must warn (train_util.py:881)."""
    return len(text.splitlines()) > 1


def split_tags(text: str) -> list[str]:
    r"""Caption text -> tag list, with a rule **byte-for-byte identical** to the trainer's.

    `[t.strip() for t in text.strip().split(",") if t.strip()]` - split on bare commas, strip each piece,
    drop empty pieces. **No unescaping at all**: that is how the trainer reads it (`library/train_util.py:900`),
    and what the editor displays must equal what the trainer will read (the same principle as invariant number one).

    So the backslash in `\,` stays inside the tag (that is the case for the 6 .txt files of one subset directory,
    where that tag ends with an extra backslash) - that is a defect **meant to be shown to the user to fix**, not something to smooth over for them.
    A newline is **not** a separator: line 2 simply does not take part in training, and `read_caption`'s multiline warning covers it.
    """
    return [tag.strip() for tag in text.strip().split(",") if tag.strip()]


def join_tags(tags: Sequence[str]) -> str:
    r"""Tag list -> caption text, which is exactly `", ".join(tags)`.

    No escaping, no deduplication, no sorting, no stripping - a pure join. A tag containing a comma does **not**
    raise here (a pure string function); it is stopped at the write step (`write_caption` raises `ValueError`).
    """
    return SEPARATOR.join(tags)


def _canonical_tags(tags: Sequence[str]) -> list[str]:
    """Normalization before writing: strip + drop empty tags.

    Normalization happens only on the write path, so "write -> read" is the identity for any already-normalized tag list
    (`split_tags` also strips and drops empty pieces).
    """
    out: list[str] = []
    for tag in tags:
        text = str(tag).strip()
        if text:
            out.append(text)
    return out


def _reject_commas(tags: Sequence[str]) -> None:
    r"""Tag contains a comma -> `ValueError`.

    The trainer splits on bare commas (`library/train_util.py:900`), so `"a,b"` read back becomes
    the two tags `a` and `b` the moment it lands on disk - **silent data corruption**. Escaping it for the user to `a\,b` is likewise read wrong
    (as `a\` and `b`), and it displays as clean, so the user cannot see the problem. So it is rejected outright.
    """
    for tag in tags:
        if "," in tag:
            raise ValueError(
                "a tag cannot contain a comma (the trainer splits on bare commas, so on disk it would be read as two tags): %r" % (tag,)
            )


def normalize_for_write(tags: Sequence[str]) -> str:
    r"""Tag list -> text fit for disk: `_canonical_tags` -> reject commas -> `join_tags`.

    The write path (`write_caption`) and the **read-only preview** (`POST /api/captions/batch`'s
    `dry_run`) share this one rule; otherwise the preview reports a comma-containing tag as "writable"
    while the real write is rejected by `ValueError` - the preview and the result must say the same thing.
    """
    cleaned = _canonical_tags(tags)
    _reject_commas(cleaned)
    return join_tags(cleaned)


def write_caption(
    image: Path,
    tags: Sequence[str] | str,
    *,
    ext: str = CAPTION_EXT_DEFAULT,
    invalidate: bool = True,
) -> WriteResult:
    """Atomically write a caption; when the content changes and `invalidate=True`, remove the TE cache in step.

    When `tags` is a `str` it is treated as **a whole block of text** (the caller writes whatever it wants, with no normalization or checking -
    this maps to the HTTP `text` field, and a normal caption of course contains commas); when it is a sequence it first goes through `_canonical_tags`
    (strip / drop empties) and then the comma check: any tag containing `,` raises `ValueError` **before the write to disk**,
    so no half-written file is left and the cache is untouched.

    When the content has not changed nothing is written (mtime stays put) and the cache is **not** invalidated - otherwise every save would wastefully delete the cache.
    A failed write (read-only / locked / disk full) raises directly, so the caller gets "nothing was changed" rather than half a file.
    """
    image = Path(image)
    target = caption_path(image, ext)
    if isinstance(tags, str):
        new_text = tags
    else:
        new_text = normalize_for_write(tags)

    old_text, _ = read_caption(image, ext)
    changed = (not target.is_file()) or old_text != new_text
    if not changed:
        return WriteResult(
            caption_path=target,
            changed=False,
            cache_removed=(),
            cache_remove_failed=(),
            multiline_warning=_is_multiline(new_text),
        )

    _atomic_write_text(target, new_text)

    removed: tuple[Path, ...] = ()
    failed: tuple[Path, ...] = ()
    if invalidate:
        result = cache_invalidation.invalidate_text_encoder_cache(image)
        removed = result.removed
        failed = result.failed

    return WriteResult(
        caption_path=target,
        changed=True,
        cache_removed=removed,
        cache_remove_failed=failed,
        multiline_warning=_is_multiline(new_text),
    )


def backup_file(target: Path, *, suffix: str = BACKUP_SUFFIX) -> Path | None:
    r"""Copy one file **verbatim** to `<name><suffix>` and return the backup path; None when there is nothing to back up.

    The generic half of the safety net, shared by every caller that is about to replace a file the user
    may care about (the caption write paths, and the export's `dataset.toml`):

    1. **An existing backup is never overwritten** - the first backup is the state before the tool touched
       anything; overwriting it means swapping "the result of the last run" for the one and only copy of
       the original data.
    2. Nothing to back up (the file does not exist) returns None rather than creating an empty file.
    3. The copy is **byte-level** (atomic write): CRLF / BOM / encoding are never touched, and restoration
       must be trustworthy byte for byte.
    """
    target = Path(target)
    if not target.is_file():
        return None
    backup = target.with_name(target.name + suffix)
    if backup.exists():
        return backup
    _atomic_write_bytes(backup, target.read_bytes())
    return backup


def backup_caption(
    image: Path,
    new_text: str | None = None,
    *,
    ext: str = CAPTION_EXT_DEFAULT,
) -> Path | None:
    r"""Back up the existing caption **verbatim** to `<caption>.bak` and return the backup path; return None when there is nothing to back up.

    Three rules, all so that "the backup is a safety net" rather than "a by-product of every write":

    1. **An existing backup is never overwritten** - the first backup is the original state before the tool touched anything; overwriting it means swapping
       "the result of the last run" for the one and only copy of the original data.
    2. When `new_text` is passed and it is **exactly the same** as the current content, no backup is produced: nothing would be overwritten,
       so leaving a `.bak` around would only fill the dataset directory with noise.
    3. The backup is a **byte-level** copy (atomic write): CRLF / BOM / encoding are never touched, and restoration must be trustworthy byte for byte.

    Rules 1 and 3 are `backup_file`'s; this function adds rule 2, which only a caption write can answer.
    """
    target = caption_path(image, ext)
    if not target.is_file():
        return None
    if new_text is not None:
        try:
            old_text = target.read_text(encoding=_READ_ENCODING)
        except (OSError, UnicodeDecodeError):
            old_text = None  # If it cannot be read, back it up anyway - the safe direction is "keep a backup", not "skip"
        if old_text == new_text:
            return None
    return backup_file(target)


@dataclass(frozen=True)
class RestoreResult:
    """One restore's outcome. `restored` is False when there was no backup to put back (not an error)."""

    caption_path: Path
    restored: bool
    backup_removed: bool
    cache_removed: tuple[Path, ...]
    cache_remove_failed: tuple[Path, ...]


def restore_caption(image: Path, *, ext: str = CAPTION_EXT_DEFAULT) -> RestoreResult:
    r"""Put the backup back: `<caption>.bak` -> `<caption>`, then **remove the backup**.

    The inverse of `backup_file`, for undoing a write instead of asking the user to be sure. Three rules:

    1. **Byte-level in both directions.** The backup holds the bytes that were there before this tool
       touched the file, so the restore writes *those bytes* back (CRLF / BOM / a missing trailing
       newline included) rather than re-encoding anything.
    2. **Invariant number one still applies**: when the content really changes, the text-encoder cache is
       invalidated in the same call. An undo that left a stale cache behind would train on the tags the
       user just undid.
    3. **The backup is consumed**: it is deleted once it has been put back. `backup_file` rule 1 ("an
       existing backup is never overwritten") would otherwise silently disarm the safety net for every
       later write to that caption - and the restored file *is* what the backup held.

    A caption with no backup returns `restored=False` rather than raising: a retried undo, or an image
    whose write changed nothing in the first place, has nothing to put back. `backup_removed` is False
    when the backup could not be deleted (a locked file on Windows); the caption is restored either way,
    and the caller decides whether to say so.
    """
    target = caption_path(image, ext)
    backup = backup_path(image, ext)
    if not backup.is_file():
        return RestoreResult(
            caption_path=target, restored=False, backup_removed=False,
            cache_removed=(), cache_remove_failed=(),
        )
    data = backup.read_bytes()
    try:
        before = target.read_bytes()
    except OSError:
        before = None  # an unreadable caption is not "already correct": write the backup over it
    if before != data:
        _atomic_write_bytes(target, data)
    backup_removed = True
    try:
        backup.unlink()
    except OSError:
        backup_removed = False
    removed: tuple[Path, ...] = ()
    failed: tuple[Path, ...] = ()
    if before != data:
        result = cache_invalidation.invalidate_text_encoder_cache(image)
        removed = result.removed
        failed = result.failed
    return RestoreResult(
        caption_path=target, restored=True, backup_removed=backup_removed,
        cache_removed=removed, cache_remove_failed=failed,
    )


def _atomic_write_bytes(target: Path, data: bytes) -> None:
    """The byte version of `_atomic_write_text`: the backup must be byte-for-byte identical to the original file."""
    fd, tmp_name = tempfile.mkstemp(
        dir=str(target.parent), prefix=_TMP_PREFIX, suffix=_TMP_SUFFIX
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, target)
    except BaseException:
        _unlink_quietly(tmp_path)
        raise


def _atomic_write_text(target: Path, text: str) -> None:
    """Same-directory temp file -> fsync -> `os.replace`; any failure leaves no temp file behind."""
    fd, tmp_name = tempfile.mkstemp(
        dir=str(target.parent), prefix=_TMP_PREFIX, suffix=_TMP_SUFFIX
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding=_WRITE_ENCODING, newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, target)
    except BaseException:
        _unlink_quietly(tmp_path)
        raise


def _unlink_quietly(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass
