"""caption read/write and cache invalidation: GET/PUT `/api/captions`, POST `/api/captions/batch`,
GET `/api/cache/status`, POST `/api/cache/invalidate` (p0-spec §4).

Why the cache routes live in this module
----------------------------------------
TE cache invalidation is the other half of the "write caption" path (invariant number one): PUT changes the content ->
`write_caption` -> `invalidate_text_encoder_cache`. Only together do they make sense,
so `/api/cache/*` belongs to this module.

The two meanings of a write failure (the frontend branches on them; never merge them)
-------------------------------------------------------------------------------------
- `write_caption` **raises** = the write did not succeed -> this module returns 4xx/5xx;
- it returns but `cache_remove_failed` is non-empty = **the write succeeded, the cache is still there** -> 200 with the values
  filled back verbatim, and the frontend pops a red toast. The backend must not swallow this entry or "turn it into a failure".

Failure granularity of batch
----------------------------
Out of bounds (`PathOutsideRoots`) rejects the whole request with 403 - out of bounds is a security problem, not something to "partially apply";
a single image missing / an unsupported extension / a write failure is recorded in that entry's `results`, and the rest are processed as usual.

Batch preview (dry_run)
-----------------------
With `dry_run=true` it computes per image **what would be written** without touching disk, so the frontend can "take a look before applying":
preview and disk write share `_apply_batch_op` (the single source of truth for op semantics) and `captions.normalize_for_write`
(the single source of truth for normalization + comma rejection). A tag containing a comma is reported as `invalid_tag` already in the preview, not only when it fails on write.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Sequence

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from ..core import cache_invalidation, captions, paths
from . import ensure_roots_configured, error_response, resolve_query_path
from .tags import get_vocabulary

router = APIRouter(prefix="/api", tags=["captions"])

__all__ = ["router"]

logger = logging.getLogger(__name__)

#: Finished jobs kept for replay; the oldest finished ones are dropped first (same cap as scale).
MAX_JOBS = 8

#: How often the SSE generator looks for new events, and how often it emits a keepalive.
POLL_SECONDS = 0.05
KEEPALIVE_SECONDS = 15.0

#: The ops `/api/captions/batch` supports (§4, matching the frontend api.js BATCH_OP).
#: `append`/`prepend` versus `replace`/`edit_tags` is the "single tag / group rename" split;
#: `regex_replace`/`regex_replace_text` is the "per tag / whole text" split -
#: the latter re-splits the replacement result on bare commas, a different semantic from the former, so they cannot be merged into one path.
BATCH_OPS: tuple[str, ...] = (
    "replace",
    "append",
    "prepend",
    "remove",
    "regex_replace",
    "regex_replace_text",
    "edit_tags",
    "normalize",
    "sort_danbooru",
)

#: The positions `edit_tags` allows: whether new tags are appended at the end or the start.
BATCH_POSITIONS: tuple[str, ...] = ("append", "prepend")

_WHITESPACE_RE = re.compile(r"\s+")


class CaptionWriteRequest(BaseModel):
    """Request body for PUT `/api/captions` (§4: `{image, tags?, text?}`).

    When both `tags` and `text` are given, `tags` wins (tag mode), because the two frontend modes are
    mutually exclusive and only one can be sent; and `tags` goes through normalization and the comma check, a stronger semantic.
    """

    image: str
    tags: list[str] | None = None
    text: str | None = None


class TagEditPair(BaseModel):
    """One rename rule for `edit_tags`; an empty `replace` = delete this tag."""

    find: str
    replace: str = ""


class CaptionBatchRequest(BaseModel):
    """POST `/api/captions/batch`: the same op applied to multiple images.

    Parameters are used per op (§4.5 ③ frozen):
    `replace` uses `find` / `replace` / `ignore_case` (compared by casefold when case-insensitive);
    `regex_replace` / `regex_replace_text` use `pattern` / `replacement`
    (falling back to `find` / `replace` when absent);
    `append` / `prepend` / `remove` use `tags`;
    `edit_tags` uses `pairs` (group rename, empty string deletes) + `tags` (additions) +
    `position` (`append` / `prepend`, appending at the end by default);
    `normalize` / `sort_danbooru` need no extra parameters.

    With `dry_run=true` it writes **not a single byte**: it returns `changed` and `before` / `after` per image,
    letting the frontend show the user the changes before actually writing (the same principle as `/api/autotag/preview`).
    Preview and disk write go through the same `_apply_batch_op`, so whatever the preview says "will change" really does change on write.

    `backup` (default **true**, matching the tagger's default `backup_overwrite`): before a caption is
    overwritten it is backed up verbatim to `<caption>.bak`, with exactly the semantics of §4.5 (4) -
    an existing backup is never overwritten, an unchanged caption leaves no backup, the copy is
    byte-level. This is the same irreversible overwrite the write modes describe, so the safety net is
    what you get without asking; `backup=false` is the way out for callers that manage their own copies.
    """

    images: list[str]
    op: str
    find: str | None = None
    replace: str | None = None
    pattern: str | None = None
    replacement: str | None = None
    tags: list[str] | None = None
    pairs: list[TagEditPair] | None = None
    position: str | None = None
    ignore_case: bool = False
    dry_run: bool = False
    backup: bool = True


class CacheInvalidateRequest(BaseModel):
    """Request body for POST `/api/cache/invalidate` (§4)."""

    images: list[str]


class CaptionReadRequest(BaseModel):
    """Request body for POST `/api/captions/read`: many captions in one round trip.

    The tag filter and the visible-scope frequency table need **every image's tag list**, and the
    per-image `GET /api/captions` is one request per image (691 on the recursive scope of the
    reference dataset). `has_caption` in the listing (§4.2) covers only the red dot, not the tags.
    """

    images: list[str]


class CaptionRestoreRequest(BaseModel):
    """Request body for POST `/api/captions/restore`: put the backups back, i.e. undo writes.

    The inverse of the write path's safety net (`backup`, §4.5 (4)). Each image's `<caption>.bak` is
    written back **byte for byte** and then removed, and the text-encoder cache is invalidated in the same
    step (§3.2, invariant number one). An image with no backup is reported in its own entry - that is what
    an undo of a write that changed nothing looks like, not a failure of the request.
    """

    images: list[str]


class BatchOpError(ValueError):
    """Invalid op argument (->400 `bad_request` / `bad_op`)."""


def _write_payload(result: captions.WriteResult) -> dict[str, Any]:
    """Expand the five WriteResult fields (paths to str). The frontend branches on these five keys."""
    return {
        "caption_path": str(result.caption_path),
        "changed": bool(result.changed),
        "cache_removed": [str(path) for path in result.cache_removed],
        "cache_remove_failed": [str(path) for path in result.cache_remove_failed],
        "multiline_warning": bool(result.multiline_warning),
    }


def _resolve_image(raw: Any) -> Path:
    """Resolve an image path: out of bounds raises `PathOutsideRoots`, missing raises `FileNotFoundError`."""
    return paths.resolve_under_roots(str(raw))


def _check_image(image: Path) -> tuple[str, str, dict[str, Any]] | None:
    """Image writability check; returns `(code, message, params)` (None = passed)."""
    if not image.is_file():
        return "not_a_file", "Not a file: %s" % image, {"path": str(image)}
    if image.suffix.lower() not in paths.IMAGE_EXTS:
        return "unsupported_image", "Unsupported image extension: %s" % image.name, {"name": image.name}
    return None


# ---------------------------------------------------------------------------
# GET / PUT /api/captions
# ---------------------------------------------------------------------------


@router.get("/captions")
def read_caption(
    path: str = Query(..., description="Absolute image path"),
) -> Any:
    """Read one image's caption: `{image, caption_path, exists, text, tags, multiline_warning}`."""
    try:
        image = _resolve_image(path)
    except paths.PathOutsideRoots as exc:
        return error_response(403, exc.code, str(exc), params=exc.params)
    except FileNotFoundError as exc:
        return error_response(404, "not_found", str(exc), params={"path": str(path)})
    problem = _check_image(image)
    if problem is not None:
        return error_response(400, problem[0], problem[1], params=problem[2])

    target = captions.caption_path(image)
    text, multiline = captions.read_caption(image)
    return {
        "image": str(image),
        "caption_path": str(target),
        "exists": target.is_file(),
        "text": text,
        "tags": captions.split_tags(text),
        "multiline_warning": bool(multiline),
    }


@router.post("/captions/read")
def read_captions(payload: CaptionReadRequest) -> Any:
    """Read many captions at once: `{results: [...]}` in input order, one entry per image.

    Same entry shape as `GET /api/captions`, plus `ok`. Out of bounds rejects the whole request with
    403 (a security problem is not a per-entry failure, the same rule as `/captions/batch`); a file
    that disappeared or is not an image is reported in **that entry** and the rest still come back.
    Read-only: it writes nothing and touches no cache.
    """
    results: list[dict[str, Any]] = []
    for raw in payload.images:
        try:
            image = _resolve_image(raw)
        except paths.PathOutsideRoots as exc:
            return error_response(403, exc.code, str(exc), params=exc.params)
        except FileNotFoundError:
            results.append({
                "image": str(raw),
                "ok": False,
                "error": {"code": "not_found", "message": "Not found: %s" % raw},
            })
            continue
        problem = _check_image(image)
        if problem is not None:
            results.append({
                "image": str(image),
                "ok": False,
                "error": {"code": problem[0], "message": problem[1]},
            })
            continue
        target = captions.caption_path(image)
        text, multiline = captions.read_caption(image)
        results.append({
            "image": str(image),
            "ok": True,
            "caption_path": str(target),
            "exists": target.is_file(),
            "text": text,
            "tags": captions.split_tags(text),
            "multiline_warning": bool(multiline),
        })
    return {"results": results}


@router.put("/captions")
def write_caption(payload: CaptionWriteRequest) -> Any:
    """Write one image's caption. An exception = the write did not succeed; a non-empty `cache_remove_failed` = written but the cache is still there."""
    try:
        image = _resolve_image(payload.image)
    except paths.PathOutsideRoots as exc:
        return error_response(403, exc.code, str(exc), params=exc.params)
    except FileNotFoundError as exc:
        return error_response(404, "not_found", str(exc), params={"path": str(payload.image)})
    problem = _check_image(image)
    if problem is not None:
        return error_response(400, problem[0], problem[1], params=problem[2])

    if payload.tags is not None:
        value: Sequence[str] | str = list(payload.tags)
    elif payload.text is not None:
        value = payload.text
    else:
        return error_response(400, "missing_tag_payload", "At least one of tags or text must be given", params={})

    try:
        result = captions.write_caption(image, value)
    except ValueError as exc:  # e.g. a tag containing a comma: rejected before writing, nothing changed
        return error_response(400, "invalid_tag", str(exc))
    except OSError as exc:  # read-only file / in use / disk full
        return error_response(500, "write_failed", "Write failed: %s" % exc)
    return _write_payload(result)


# ---------------------------------------------------------------------------
# POST /api/captions/batch
# ---------------------------------------------------------------------------


def _compile_pattern(payload: CaptionBatchRequest, op: str) -> "re.Pattern[str]":
    """Compile batch's regex parameter; a missing pattern or an invalid one always raises `BatchOpError` (->400)."""
    pattern = payload.pattern or payload.find
    if not pattern:
        raise BatchOpError("op=%s needs pattern" % op)
    flags = re.IGNORECASE if payload.ignore_case else 0
    try:
        return re.compile(pattern, flags)
    except re.error as exc:
        raise BatchOpError("Invalid regular expression: %s" % exc) from None


def _with_added_tags(
    tags: list[str], payload: CaptionBatchRequest, *, at_front: bool
) -> list[str]:
    """`append` / `prepend`: add the not-yet-present `tags` at the end or the start (order preserved)."""
    wanted = [str(tag) for tag in (payload.tags or []) if str(tag)]
    present = set(tags)
    fresh = [tag for tag in wanted if tag not in present]
    return fresh + tags if at_front else tags + fresh


def _apply_edit_tags(tags: list[str], payload: CaptionBatchRequest) -> list[str]:
    """`edit_tags`: rename/delete by `pairs`, then add `tags` at the end or the start.

    Renaming is a **single mapping**: the first matching pair wins, no chaining (when a->b and b->c are both given,
    a tag that was originally a only becomes b and is not further changed to c). Chaining would make a combination like
    "rename then rename again" silently change extra images, and the user could not see it in the UI.

    An empty or all-whitespace `replace` = delete this tag (consistent with dataset-tag-editor's "change to blank means delete").
    Slots are not compacted: deleting the 3rd common tag does not affect the 4th, so there is no "misaligned rename".
    """
    pairs = payload.pairs or []
    added = [str(tag) for tag in (payload.tags or []) if str(tag)]
    if not pairs and not added:
        raise BatchOpError("op=edit_tags needs pairs or tags")
    position = (payload.position or "append").strip().lower()
    if position not in BATCH_POSITIONS:
        raise BatchOpError("op=edit_tags position must be one of %s" % list(BATCH_POSITIONS))
    ignore_case = bool(payload.ignore_case)
    out: list[str] = []
    for tag in tags:
        replacement: str | None = None
        for pair in pairs:
            find = str(pair.find or "")
            if not find:
                raise BatchOpError("op=edit_tags pairs[].find cannot be empty")
            hit = tag.casefold() == find.casefold() if ignore_case else tag == find
            if hit:
                replacement = str(pair.replace or "")
                break
        if replacement is None:
            out.append(tag)
            continue
        cleaned = replacement.strip()
        if cleaned:
            out.append(cleaned)
    present = set(out)
    fresh = [tag for tag in added if tag not in present]
    return fresh + out if position == "prepend" else out + fresh


def _apply_batch_op(
    op: str, tags: list[str], payload: CaptionBatchRequest, text: str = ""
) -> list[str] | str:
    """Apply one op to a tag list (or a whole text) (a pure function, touches no files).

    Returning `list[str]` = a tag-level op, which goes through `captions.normalize_for_write` before writing;
    returning `str` = a whole-text op (`regex_replace_text`), written back verbatim as a whole.
    Both paths are handled uniformly by the caller, and **disk write and dry_run share this function** - only what the preview says will change does change.
    """
    if op == "replace":
        find = payload.find
        if not find:
            raise BatchOpError("op=replace needs a non-empty find")
        target = payload.replace if payload.replace is not None else ""
        if payload.ignore_case:  # §4.5 ③: replace's parameters are find / replace / ignore_case
            needle = find.casefold()
            return [target if tag.casefold() == needle else tag for tag in tags]
        return [target if tag == find else tag for tag in tags]
    if op == "regex_replace":
        compiled = _compile_pattern(payload, op)
        replacement = payload.replacement if payload.replacement is not None else (payload.replace or "")
        out = [compiled.sub(replacement, tag).strip() for tag in tags]
        return [tag for tag in out if tag]
    if op == "regex_replace_text":
        compiled = _compile_pattern(payload, op)
        replacement = payload.replacement if payload.replacement is not None else (payload.replace or "")
        return compiled.sub(replacement, text)
    if op == "append":
        return _with_added_tags(tags, payload, at_front=False)
    if op == "prepend":
        return _with_added_tags(tags, payload, at_front=True)
    if op == "remove":
        drop = set(payload.tags or [])
        return [tag for tag in tags if tag not in drop]
    if op == "edit_tags":
        return _apply_edit_tags(tags, payload)
    if op == "normalize":
        out = []
        seen: set[str] = set()
        for tag in tags:
            cleaned = _WHITESPACE_RE.sub(" ", str(tag)).strip()
            if cleaned and cleaned not in seen:
                seen.add(cleaned)
                out.append(cleaned)
        return out
    if op == "sort_danbooru":
        vocab = get_vocabulary()
        if vocab is None:
            return sorted(tags, key=lambda tag: tag.lower())
        return sorted(tags, key=vocab.order_key)
    raise BatchOpError("Unknown op %r; available: %s" % (op, list(BATCH_OPS)))


def _batch_step(
    image: Path, op: str, payload: CaptionBatchRequest, dry_run: bool
) -> tuple[dict[str, Any], bool]:
    """One image's entry, plus whether it counts as applied.

    Shared by the synchronous endpoint and the background job, so "the preview says it will change"
    and "the write really changed it" stay two entrances to one rule. Raises `BatchOpError` for a bad
    argument: the caller decides whether that is a 400 (a request) or an error entry plus a terminal
    done (a job).
    """
    problem = _check_image(image)
    if problem is not None:
        return (
            {"image": str(image), "ok": False, "error": {"code": problem[0], "message": problem[1]}},
            False,
        )
    try:
        current, _ = captions.read_caption(image)
        outcome = _apply_batch_op(op, captions.split_tags(current), payload, current)
        # tag-level results go through normalize_for_write: preview and actual writing use the same
        # normalization + comma-rejection rule, otherwise the preview would say "writable" while the
        # write reports invalid_tag.
        new_text = outcome if isinstance(outcome, str) else captions.normalize_for_write(outcome)
    except BatchOpError:
        # BatchOpError IS a ValueError; a bad argument must not be reported as one bad image.
        raise
    except ValueError as exc:
        return (
            {"image": str(image), "ok": False, "error": {"code": "invalid_tag", "message": str(exc)}},
            False,
        )
    except OSError as exc:
        return (
            {"image": str(image), "ok": False, "error": {"code": "write_failed", "message": str(exc)}},
            False,
        )

    if dry_run:
        # write not a single byte: just report before / after back for the user to see
        changed = new_text != current
        return (
            {
                "image": str(image),
                "ok": True,
                "changed": changed,
                "before": current,
                "after": new_text,
            },
            changed,
        )

    if payload.backup:
        try:
            # Same normalization as the write (new_text), so "is there anything to back up"
            # and "does the content really change" stay one rule - an unchanged caption must
            # not leave a .bak behind.
            captions.backup_caption(image, new_text)
        except OSError as exc:
            # Fail closed: when the safety net cannot be written the caption must not be
            # overwritten anyway - that would break the promise exactly when it was needed.
            return (
                {
                    "image": str(image),
                    "ok": False,
                    "error": {"code": "write_failed", "message": "backup failed: %s" % exc},
                },
                False,
            )

    try:
        written = captions.write_caption(image, outcome)
    except ValueError as exc:
        return (
            {"image": str(image), "ok": False, "error": {"code": "invalid_tag", "message": str(exc)}},
            False,
        )
    except OSError as exc:
        return (
            {"image": str(image), "ok": False, "error": {"code": "write_failed", "message": str(exc)}},
            False,
        )
    entry: dict[str, Any] = {"image": str(image), "ok": True}
    entry.update(_write_payload(written))
    # The preview and the write must render the same table: without before/after a
    # successful write showed an empty diff ("none -> none") even though the tags
    # did change. Both values are already in hand here.
    entry["before"] = current
    entry["after"] = new_text
    return entry, True


@router.post("/captions/batch")
def batch_captions(payload: CaptionBatchRequest) -> Any:
    """Run the same op on multiple images, returning `{applied, failed, results}`.

    With `dry_run=true` it **does not write to disk or touch the cache**: `applied` means "how many would change",
    each result carries `changed` / `before` / `after`, and the top level additionally has `dry_run: true`.
    Preview and disk write share `_apply_batch_op` and `captions.normalize_for_write`, so
    "the preview says it will change" and "the write really changed it" are two entrances to the same rule.
    """
    op = (payload.op or "").strip().lower()
    if op not in BATCH_OPS:
        return error_response(
            400,
            "bad_op",
            "Unknown op %r; available: %s" % (payload.op, list(BATCH_OPS)),
            params={"op": str(payload.op), "available": str(list(BATCH_OPS))},
        )

    # Resolve everything first: out of bounds always rejects the whole request with 403, no "partial application".
    resolved, failure = _resolve_batch_images(payload)
    if failure is not None:
        return failure
    images = resolved or []

    dry_run = bool(payload.dry_run)
    applied = 0
    failed = 0
    results: list[dict[str, Any]] = []
    for image in images:
        try:
            entry, counted = _batch_step(image, op, payload, dry_run)
        except BatchOpError as exc:
            return error_response(400, "bad_request", str(exc))
        if counted:
            applied += 1
        if entry.get("ok") is False:
            failed += 1
        results.append(entry)

    response: dict[str, Any] = {"applied": applied, "failed": failed, "results": results}
    if dry_run:
        # explicitly flagged, so the frontend cannot mistake the preview for "already written"
        response["dry_run"] = True
    return response


@router.post("/captions/restore")
def restore_captions(payload: CaptionRestoreRequest) -> Any:
    """Undo caption writes by restoring each image's backup: `{restored, failed, results}`.

    The counterweight to `backup` on the write side: a batch rewrite is reversible, so the panel can
    offer an undo instead of a dialog. Per-entry problems (no backup, not an image, an unreadable file)
    are reported in **that entry** and the rest still run; an out-of-bounds path rejects the whole request
    with 403, the same rule as `/captions/batch` and `/captions/read`.

    A restore that changes the file invalidates the text-encoder cache in the same call - undoing a write
    must never leave training reading the tags the user just took back.
    """
    resolved, failure = _resolve_many(payload.images)
    if failure is not None:
        return failure
    images = resolved or []

    restored = 0
    failed = 0
    results: list[dict[str, Any]] = []
    for image in images:
        problem = _check_image(image)
        if problem is not None:
            results.append({
                "image": str(image),
                "ok": False,
                "error": {"code": problem[0], "message": problem[1]},
            })
            failed += 1
            continue
        try:
            result = captions.restore_caption(image)
        except OSError as exc:
            results.append({
                "image": str(image),
                "ok": False,
                "error": {"code": "restore_failed", "message": "Restore failed: %s" % exc},
            })
            failed += 1
            continue
        if not result.restored:
            results.append({
                "image": str(image),
                "ok": False,
                "restored": False,
                "error": {
                    "code": "no_backup",
                    "message": "No backup to restore: %s" % captions.backup_path(image),
                },
            })
            failed += 1
            continue
        restored += 1
        results.append({
            "image": str(image),
            "ok": True,
            "restored": True,
            "caption_path": str(result.caption_path),
            "backup_removed": bool(result.backup_removed),
            "cache_removed": [str(path) for path in result.cache_removed],
            "cache_remove_failed": [str(path) for path in result.cache_remove_failed],
        })
    return {"restored": restored, "failed": failed, "results": results}


# ---------------------------------------------------------------------------
# GET /api/cache/status, POST /api/cache/invalidate
# ---------------------------------------------------------------------------


@router.get("/cache/status")
def cache_status(
    path: str = Query("", description="Directory to check; empty = the first root in the allowlist"),
    recursive: bool = Query(False, description="Collect images recursively"),
) -> Any:
    """List entries where the caption is newer than the TE cache (stale cache)."""
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

    stale = cache_invalidation.find_stale_encoder_caches(target, recursive=recursive)
    return {
        "stale": [
            {"image": str(entry.image), "caption": str(entry.caption), "cache": str(entry.cache)}
            for entry in stale
        ]
    }


@router.post("/cache/invalidate")
def cache_invalidate(payload: CacheInvalidateRequest) -> Any:
    """Delete the TE cache for the given images (a manual fallback for when a caption change did not clean up fully)."""
    resolved, failure = _resolve_cache_images(payload)
    if failure is not None:
        return failure

    removed: list[str] = []
    failed: list[str] = []
    for image in resolved or []:
        result = cache_invalidation.invalidate_text_encoder_cache(image)
        removed.extend(str(path) for path in result.removed)
        failed.extend(str(path) for path in result.failed)
    return {"removed": removed, "failed": failed}


# ---------------------------------------------------------------------------
# Background jobs (§4.5): the batch write and the cache rebuild are long operations
# over hundreds of images, so both have a job form with real progress and a cancel.
#
# The synchronous endpoints stay exactly as they were: the diff preview is instant and
# must not become a job, and external callers keep working. The job form is additive.
# ---------------------------------------------------------------------------


class JobCancelRequest(BaseModel):
    """Request body for the cancel endpoints (§4.5)."""

    job_id: str


@dataclass(frozen=True)
class _Event:
    seq: int
    name: str
    data: dict[str, Any]


class _Job:
    """Shared machinery: an ordered event log, a cancel flag and a finished flag.

    The log is **complete** (one progress per image + one terminal done), so an EventSource that
    connects only after the POST can still replay the whole run. Counting happens here, in the
    worker thread, never in the frontend: a dropped connection must not change the numbers.
    """

    phase = "job"

    def __init__(self, job_id: str, total: int) -> None:
        self.job_id = job_id
        self.total = total
        self.created = time.time()
        self.errors: list[dict[str, str]] = []
        self._done = 0
        self._current = ""
        self._finished = False
        self._cancel = threading.Event()
        self._lock = threading.Lock()
        self._events: list[_Event] = []

    # ---- state (read-only, for endpoints and tests) --------------------

    @property
    def done(self) -> int:
        with self._lock:
            return self._done

    @property
    def finished(self) -> bool:
        with self._lock:
            return self._finished

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    # ---- called by the worker thread -----------------------------------

    def fail(self, path: Path | str, message: str) -> None:
        with self._lock:
            self.errors.append({"path": str(path), "message": str(message)})

    def advance(self, item: dict[str, Any]) -> None:
        """One image finished (successfully or not); `item` is what the frontend renders."""
        raise NotImplementedError

    def finish(self) -> None:
        """Emit the terminal done{finished:true}. Idempotent."""
        with self._lock:
            if self._finished:
                return
            self._finished = True
            self._events.append(_Event(len(self._events), "done", self._done_locked()))

    def request_cancel(self) -> bool:
        """Set the flag; the worker stops before its next write. False = nothing to cancel."""
        with self._lock:
            if self._finished:
                return False
            self._cancel.set()
            return True

    # ---- called by the SSE endpoint ------------------------------------

    def events_since(self, cursor: int) -> tuple[list[_Event], bool]:
        with self._lock:
            events = [event for event in self._events if event.seq >= cursor]
            return events, self._finished

    # ---- internal ------------------------------------------------------

    def _append_locked(self, name: str, data: dict[str, Any]) -> None:
        self._events.append(_Event(len(self._events), name, data))

    def _progress_locked(self, item: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def _summary_locked(self) -> dict[str, Any]:
        raise NotImplementedError

    def _done_locked(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "done": self._done,
            "total": self.total,
            "finished": True,
            "cancelled": self._cancel.is_set(),
            "errors": [dict(entry) for entry in self.errors],
            "summary": self._summary_locked(),
        }


class BatchJob(_Job):
    """A batch caption write over N images: one progress event per image, then done."""

    phase = "captions"

    def __init__(self, job_id: str, total: int, op: str) -> None:
        super().__init__(job_id, total)
        self.op = op
        self._applied = 0
        self._failed = 0

    def advance(self, item: dict[str, Any]) -> None:
        """`item` is one entry of the synchronous endpoint's `results`, verbatim."""
        with self._lock:
            self._done += 1
            self._current = str(item.get("image", ""))
            if item.get("ok") is False:
                self._failed += 1
                error = item.get("error") or {}
                self.errors.append({"path": self._current, "message": str(error.get("message", ""))})
            elif item.get("changed", True):
                self._applied += 1
            self._append_locked("progress", self._progress_locked(item))

    def abort(self, message: str) -> None:
        """A bad argument: not one image's failure but the whole job's, recorded once."""
        with self._lock:
            self.errors.append({"path": "", "message": str(message)})
            self._append_locked("progress", self._progress_locked({"ok": False, "error": {"message": str(message)}}))

    def _progress_locked(self, item: dict[str, Any]) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "done": self._done,
            "total": self.total,
            "current": self._current,
            "entry": item,
            "errors": [dict(entry) for entry in self.errors],
        }

    def _summary_locked(self) -> dict[str, Any]:
        return {
            "op": self.op,
            "processed": self._done,
            "applied": self._applied,
            "failed": self._failed,
        }


class CacheJob(_Job):
    """A cache rebuild over N images: one progress event per image, then done."""

    phase = "cache"

    def __init__(self, job_id: str, total: int) -> None:
        super().__init__(job_id, total)
        self._removed = 0
        self._failed = 0

    def advance(self, item: dict[str, Any]) -> None:
        with self._lock:
            self._done += 1
            self._current = str(item.get("image", ""))
            removed = int(item.get("removed", 0) or 0)
            failed = int(item.get("failed", 0) or 0)
            self._removed += removed
            self._failed += failed
            if failed:
                self.errors.append({"path": self._current, "message": "cache could not be removed"})
            self._append_locked("progress", self._progress_locked(item))

    def _progress_locked(self, item: dict[str, Any]) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "done": self._done,
            "total": self.total,
            "current": self._current,
            "entry": item,
            "errors": [dict(entry) for entry in self.errors],
        }

    def _summary_locked(self) -> dict[str, Any]:
        return {
            "processed": self._done,
            "removed": self._removed,
            "failed": self._failed,
        }


_JOBS: dict[str, _Job] = {}
_JOBS_LOCK = threading.Lock()


def _register(job: _Job) -> None:
    with _JOBS_LOCK:
        if len(_JOBS) >= MAX_JOBS:
            finished = sorted(
                (item for item in _JOBS.values() if item.finished), key=lambda item: item.created
            )
            for stale in finished[: max(1, len(_JOBS) - MAX_JOBS + 1)]:
                _JOBS.pop(stale.job_id, None)
        _JOBS[job.job_id] = job


def get_job(job_id: str) -> _Job | None:
    with _JOBS_LOCK:
        return _JOBS.get(job_id)


def _sse(event: _Event) -> str:
    return "event: %s\ndata: %s\n\n" % (event.name, json.dumps(event.data, ensure_ascii=False))



async def _stream_job(job: _Job) -> AsyncIterator[str]:
    cursor = 0
    idle = 0.0
    while True:
        events, finished = job.events_since(cursor)
        for event in events:
            cursor = event.seq + 1
            yield _sse(event)
            if event.name == "done":
                return
        if finished:
            return
        await asyncio.sleep(POLL_SECONDS)
        idle += POLL_SECONDS
        if idle >= KEEPALIVE_SECONDS:
            idle = 0.0
            yield ": keepalive\n\n"



def _job_stream(job_id: str) -> Any:
    """Both job families share one stream shape: progress events, then done{finished:true}."""
    job = get_job(job_id)
    if job is None:
        return error_response(
            404, "unknown_job", "no such job: %s" % job_id, params={"job_id": str(job_id)}
        )
    return StreamingResponse(
        _stream_job(job),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _resolve_many(raw_images: list[str]) -> tuple[list[Path] | None, Any | None]:
    """Resolve every image first: out of bounds always rejects the whole request with 403.

    Shared by the batch write and the restore, so the two list endpoints cannot drift apart on what a
    bad path means.
    """
    resolved: list[Path] = []
    for raw in raw_images:
        try:
            resolved.append(_resolve_image(raw))
        except paths.PathOutsideRoots as exc:
            return None, error_response(403, exc.code, str(exc), params=exc.params)
        except FileNotFoundError as exc:
            return None, error_response(404, "not_found", str(exc), params={"path": str(raw)})
    return resolved, None


def _resolve_batch_images(payload: CaptionBatchRequest) -> tuple[list[Path] | None, Any | None]:
    return _resolve_many(payload.images)


def _resolve_cache_images(payload: CacheInvalidateRequest) -> tuple[list[Path] | None, Any | None]:
    resolved: list[Path] = []
    for raw in payload.images:
        try:
            image = _resolve_image(raw)
        except paths.PathOutsideRoots as exc:
            return None, error_response(403, exc.code, str(exc), params=exc.params)
        except FileNotFoundError as exc:
            return None, error_response(404, "not_found", str(exc), params={"path": str(raw)})
        problem = _check_image(image)
        if problem is not None:
            return None, error_response(400, problem[0], problem[1], params=problem[2])
        resolved.append(image)
    return resolved, None


def _run_batch_job(job: BatchJob, images: list[Path], payload: CaptionBatchRequest) -> None:
    """Background-thread entry point: **always** emits done afterwards, whichever path it takes."""
    try:
        for image in images:
            # The cancel check sits between images, so a cancel never interrupts a write in
            # progress: whatever was already written stays written, and the done event says so.
            if job.cancelled:
                break
            try:
                entry, _counted = _batch_step(image, job.op, payload, False)
            except BatchOpError as exc:
                job.abort(str(exc))
                break
            job.advance(entry)
    except Exception:  # noqa: BLE001 - no HTTP boundary inside a thread, so it catches everything itself
        logger.exception("batch job %s failed unexpectedly", job.job_id)
    finally:
        job.finish()


def _run_cache_job(job: CacheJob, images: list[Path]) -> None:
    """Background-thread entry point: **always** emits done afterwards, whichever path it takes."""
    try:
        for image in images:
            if job.cancelled:
                break
            result = cache_invalidation.invalidate_text_encoder_cache(image)
            job.advance({
                "image": str(image),
                "removed": len(result.removed),
                "failed": len(result.failed),
            })
    except Exception:  # noqa: BLE001
        logger.exception("cache job %s failed unexpectedly", job.job_id)
    finally:
        job.finish()


@router.post("/captions/batch/run")
def batch_captions_run(payload: CaptionBatchRequest) -> Any:
    """Start a background batch write: 202 {job_id, total}. Progress via GET .../batch/stream."""
    op = (payload.op or "").strip().lower()
    if op not in BATCH_OPS:
        return error_response(
            400,
            "bad_op",
            "Unknown op %r; available: %s" % (payload.op, list(BATCH_OPS)),
            params={"op": str(payload.op), "available": str(list(BATCH_OPS))},
        )
    if payload.dry_run:
        # A preview is instant; turning it into a job would only hide the diff behind a stream.
        return error_response(
            400,
            "bad_request",
            "the job form always writes; use POST /api/captions/batch for a dry-run preview",
        )
    resolved, failure = _resolve_batch_images(payload)
    if failure is not None:
        return failure
    images = resolved or []
    job = BatchJob(uuid.uuid4().hex, len(images), op)
    _register(job)
    worker = threading.Thread(
        target=_run_batch_job,
        args=(job, images, payload),
        name="batch-%s" % job.job_id[:8],
        daemon=True,
    )
    worker.start()
    logger.info("batch job %s: %s over %d image(s)", job.job_id, op, len(images))
    return JSONResponse(status_code=202, content={"job_id": job.job_id, "total": len(images)})


@router.get("/captions/batch/stream")
async def batch_captions_stream(
    job_id: str = Query(..., description="the job_id returned by POST /api/captions/batch/run"),
) -> Any:
    """SSE progress for a batch write: one progress per image, one done{finished:true} at the end."""
    return _job_stream(job_id)


@router.post("/captions/batch/cancel")
def batch_captions_cancel(payload: JobCancelRequest) -> Any:
    """Request a cancel: set the flag -> stop before the next image -> done{finished:true} says how far it got."""
    job = get_job(payload.job_id)
    accepted = bool(job is not None and job.request_cancel())
    if not accepted:
        logger.info("cancel request had no effect (job missing or already finished): %s", payload.job_id)
    return JSONResponse(status_code=202, content={"cancelled": accepted})


@router.post("/cache/invalidate/run")
def cache_invalidate_run(payload: CacheInvalidateRequest) -> Any:
    """Start a background cache rebuild: 202 {job_id, total}. Progress via GET .../invalidate/stream."""
    resolved, failure = _resolve_cache_images(payload)
    if failure is not None:
        return failure
    images = resolved or []
    job = CacheJob(uuid.uuid4().hex, len(images))
    _register(job)
    worker = threading.Thread(
        target=_run_cache_job,
        args=(job, images),
        name="cache-%s" % job.job_id[:8],
        daemon=True,
    )
    worker.start()
    logger.info("cache job %s over %d image(s)", job.job_id, len(images))
    return JSONResponse(status_code=202, content={"job_id": job.job_id, "total": len(images)})


@router.get("/cache/invalidate/stream")
async def cache_invalidate_stream(
    job_id: str = Query(..., description="the job_id returned by POST /api/cache/invalidate/run"),
) -> Any:
    """SSE progress for a cache rebuild."""
    return _job_stream(job_id)


@router.post("/cache/invalidate/cancel")
def cache_invalidate_cancel(payload: JobCancelRequest) -> Any:
    """Request a cancel for a cache rebuild (same contract as the batch cancel)."""
    job = get_job(payload.job_id)
    accepted = bool(job is not None and job.request_cancel())
    if not accepted:
        logger.info("cancel request had no effect (job missing or already finished): %s", payload.job_id)
    return JSONResponse(status_code=202, content={"cancelled": accepted})