"""Dataset image scaling/export: POST /api/scale/run + SSE + cancel (p0-spec §4.14).

What this endpoint does
----------------------
Scale **the images the request lists** to the target size with **area normalization**, write them
into a destination directory preserving their relative structure under `root`, and **automatically
carry along a same-named caption** next to each image (no more "accessory file extension" input).
Optionally generate a dataset.toml at the destination root so the export directory can be used for
training as-is.

`images` is the explicit list, and it is the whole of what the job exports (2026-09-20). The
endpoint used to enumerate `root` recursively instead, which is how an export could cover more than
the control on screen said it would: the column's scope strip answers "which images?" for every
panel that acts on the grid (scope.js), so the request carries that answer. A request without a
list is rejected (`no_images`) rather than widened to the whole root - that widening is exactly the
surprise this contract removes, and leaving it in as a fallback would leave the surprise reachable.

Why a background job
--------------------
Decoding + resampling + encoding 1653 images is minutes of work and must never sit on a request
thread—the same approach as run in api/autotag.py: POST returns 202 {job_id} immediately, progress
goes over SSE, cancel goes through POST /api/scale/cancel. The finish signal must be
done{finished:true}, not just done >= total (total is a snapshot from the start).

Security boundary
-----------------
Every path goes through the allowlist: each image in the list, and both directories. The source
directory must exist and be a directory; the destination directory may not exist yet (it is created
during export), but **after resolution it must fall under some allowlisted root** (out of bounds →
403). The destination directory **must not be inside the source directory**—that would mix the
export results into the dataset, and the trainer's directory scan would treat them as a new subset. This endpoint sits at the same level as
PUT /api/captions: it writes only allowlisted paths, so it does not additionally go through the
loopback gate.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..core import captions, dataset_toml, paths, scaler
from . import ensure_roots_configured, error_response, resolve_query_path

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["scale"])

__all__ = ["router", "ScaleRequest"]

#: Upper bound on simultaneously retained jobs. A snapshot stores one progress event per image, so it must stay bounded (same as autotag).
MAX_JOBS = 8

#: SSE poll interval / idle keepalive interval (seconds).
POLL_SECONDS = 0.05
KEEPALIVE_SECONDS = 15.0

#: The generated file's name comes from `core.dataset_toml.DATASET_TOML_NAME` (defined once there):
#: the name §4.9's frontend download uses, the name §4.16's status endpoint reports, and the name the
#: trainer reads are one fact, not three spellings that can drift apart.

_TMP_PREFIX = ".tmp-scale-"
_TMP_SUFFIX = ".tmp"

#: How many images the render stage processes at once. Decode / resample / encode are Pillow C calls
#: that release the GIL, so this really is parallel: measured on 16 cores for a 1536x1536 PNG export,
#: 1355 ms/image sequential -> 415 ms/image at 4 workers -> 272 ms/image at 8 (5.0x). Capped at 8:
#: past that the curve flattens while peak memory (several decoded images at once) keeps growing.
EXPORT_WORKERS = max(1, min(8, os.cpu_count() or 1))

#: Images planned per render round. A cancel is only noticed between rounds, so this bounds how much
#: work a cancel can still leave in flight; it also bounds how many plans are outstanding at once.
EXPORT_CHUNK_FACTOR = 2

#: The settings that belong to a scaler plan/render call (write_dataset_toml is not one of them).
_PLAN_KEYS = (
    "target_width",
    "target_height",
    "no_upscale",
    "resample",
    "output_format",
    "quality",
    "optimize",
    "caption_extension",
)


class ScaleRequest(BaseModel):
    """Request body for POST /api/scale/run (frozen in §4.14).

    `images` is **the export's whole extent**: exactly these files are written, in this order, and
    every one of them must live under `root` (which is what the destination tree is rebuilt from:
    `root/post/1.png` -> `target_dir/post/1.png`). An empty list is 400 `no_images` rather than
    "then do the whole directory" - a job that silently widens what it writes is the defect this
    list exists to remove, and the frontend always sends the scope strip's answer (scope.js).

    A duplicate entry is dropped instead of exported twice: `unique_destination` would have given
    the second copy a `_1` suffix, i.e. two files on disk for one image the user picked once.

    When root is empty it falls back to the first allowlisted root (the same convention as
    /api/fs/list). target_dir has **no default**: the export destination must be given explicitly,
    otherwise it is the same as writing the results into the source directory.

    `write_dataset_toml` generates the trainer's dataset.toml at the target root. If one is already
    there, **it is not replaced** unless `overwrite_dataset_toml` says so - the rule this job already
    applies to images - and that path backs the previous file up first.
    """

    model_config = ConfigDict(extra="forbid")

    images: list[str] = Field(default_factory=list)
    root: str = ""
    target_dir: str
    target_width: int = Field(1536, ge=8, le=32768)
    target_height: int = Field(1536, ge=8, le=32768)
    no_upscale: bool = True
    resample: str = "lanczos"
    output_format: str = "png"
    quality: int = Field(95, ge=1, le=100)
    optimize: bool = True
    caption_extension: str = ".txt"
    write_dataset_toml: bool = False
    overwrite_dataset_toml: bool = False

    @field_validator("target_dir")
    @classmethod
    def _target_required(cls, value: str) -> str:
        if not str(value).strip():
            raise ValueError("target_dir must not be empty")
        return value

    @field_validator("resample")
    @classmethod
    def _known_resample(cls, value: str) -> str:
        name = str(value).strip().lower()
        if name not in scaler.RESAMPLE_NAMES:
            raise ValueError("resample must be one of %s" % list(scaler.RESAMPLE_NAMES))
        return name

    @field_validator("output_format")
    @classmethod
    def _known_format(cls, value: str) -> str:
        name = str(value).strip().lower()
        if name not in scaler.IMAGE_FORMATS:
            raise ValueError("output_format must be one of %s" % list(scaler.IMAGE_FORMATS))
        return name

    @field_validator("caption_extension")
    @classmethod
    def _non_empty_extension(cls, value: str) -> str:
        """An empty extension would make the sidecar the image name itself (which never exists)—reject it.

        As in §4.9, **no dot is added**: if the user writes txt, we let the trainer see txt too.
        """
        if not str(value).strip():
            raise ValueError("caption_extension must not be empty")
        return value


class ScaleCancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str


@dataclass(frozen=True)
class _Event:
    seq: int
    name: str
    data: dict[str, Any]


class ScaleJob:
    """An export job's progress snapshot + event log (§4.14).

    The event log is **complete** (one progress per image + one done at the end), so an EventSource
    that connects only after the POST can still replay the whole run. Every progress entry carries
    that image's plan/result (original size, new size, factor, whether the caption was copied), so
    the frontend needs no second request.
    """

    def __init__(self, job_id: str, total: int, root: Path, target_dir: Path) -> None:
        self.job_id = job_id
        self.total = total
        self.root = root
        self.target_dir = target_dir
        self.created = time.time()
        self.errors: list[dict[str, str]] = []
        self._done = 0
        self._current = ""
        self._scaled = 0
        self._kept = 0
        self._captions = 0
        self._dataset_toml: str | None = None
        self._dataset_toml_warnings: list[str] = []
        self._dataset_toml_backup: str | None = None
        self._dataset_toml_skipped = False
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
            self.errors.append({"path": str(path), "message": message})

    def advance(self, outcome: scaler.ScaleOutcome) -> None:
        """One image finished successfully. Counting happens here, not by the frontend counting itself."""
        with self._lock:
            self._done += 1
            self._current = str(outcome.src)
            if outcome.resized:
                self._scaled += 1
            if outcome.upscale_skipped:
                self._kept += 1
            if outcome.caption is not None:
                self._captions += 1
            self._events.append(_Event(len(self._events), "progress", self._progress_locked(outcome.as_dict())))

    def advance_failed(self, path: Path | str) -> None:
        with self._lock:
            self._done += 1
            self._current = str(path)
            self._events.append(
                _Event(
                    len(self._events),
                    "progress",
                    self._progress_locked(
                        {"status": "error", "src": str(path), "error": "see errors"}
                    ),
                )
            )

    def set_dataset_toml(
        self,
        path: str | None,
        warnings: list[str],
        *,
        backup: str | None = None,
        skipped: bool = False,
    ) -> None:
        with self._lock:
            self._dataset_toml = path
            self._dataset_toml_warnings = list(warnings)
            self._dataset_toml_backup = backup
            self._dataset_toml_skipped = skipped

    def finish(self) -> None:
        """Emit the terminal done{finished:true}. Idempotent."""
        with self._lock:
            if self._finished:
                return
            self._finished = True
            self._events.append(_Event(len(self._events), "done", self._done_locked()))

    def request_cancel(self) -> bool:
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

    def _progress_locked(self, image: dict[str, Any]) -> dict[str, Any]:
        return {
            "phase": "scale",
            "done": self._done,
            "total": self.total,
            "current": self._current,
            "image": image,
            "errors": [dict(item) for item in self.errors],
        }

    def _summary_locked(self) -> dict[str, Any]:
        return {
            "target_dir": str(self.target_dir),
            "processed": self._done,
            "scaled": self._scaled,
            "kept": self._kept,
            "captions": self._captions,
            "failed": len(self.errors),
            "dataset_toml": self._dataset_toml,
            "dataset_toml_warnings": list(self._dataset_toml_warnings),
            "dataset_toml_backup": self._dataset_toml_backup,
            "dataset_toml_skipped": self._dataset_toml_skipped,
        }

    def _done_locked(self) -> dict[str, Any]:
        return {
            "phase": "scale",
            "done": self._done,
            "total": self.total,
            "finished": True,
            "cancelled": self._cancel.is_set(),
            "errors": [dict(item) for item in self.errors],
            "summary": self._summary_locked(),
        }


_JOBS: dict[str, ScaleJob] = {}
_JOBS_LOCK = threading.Lock()


def _register(job: ScaleJob) -> None:
    with _JOBS_LOCK:
        if len(_JOBS) >= MAX_JOBS:
            finished = sorted(
                (item for item in _JOBS.values() if item.finished), key=lambda item: item.created
            )
            for stale in finished[: max(1, len(_JOBS) - MAX_JOBS + 1)]:
                _JOBS.pop(stale.job_id, None)
        _JOBS[job.job_id] = job


def get_job(job_id: str) -> ScaleJob | None:
    with _JOBS_LOCK:
        return _JOBS.get(job_id)


def _is_inside(child: Path, parent: Path) -> bool:
    """child equals parent or is under it (path comparison uses the platform's case rules)."""
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def _atomic_write_text(target: Path, text: str) -> None:
    """The same atomic write as core/captions.py: temp file → fsync → os.replace."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=_TMP_PREFIX, suffix=_TMP_SUFFIX)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, target)
    except BaseException:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


def _write_dataset_toml(job: ScaleJob, settings: dict[str, Any]) -> None:
    """Optional: generate dataset.toml at the destination root (image_dir points at the new directory, resolution matches the scaling target).

    An existing dataset.toml is **never replaced silently** - the rule this job already applies to the
    images it exports (`scaler.unique_destination`). `overwrite_dataset_toml` is the explicit way
    through, and even that path copies the previous file to `dataset.toml.bak` first.

    The name comes from `dataset_toml.DATASET_TOML_NAME`; §4.16's status endpoint reports the same
    file, and one definition is what keeps "the file this job writes" and "the file the strip reports"
    from becoming two different files.
    """
    target = job.target_dir / dataset_toml.DATASET_TOML_NAME
    if target.exists() and not settings.get("overwrite_dataset_toml"):
        job.set_dataset_toml(
            None,
            [
                "dataset.toml already exists at %s and was left untouched; pass overwrite_dataset_toml "
                "to replace it (the previous file is then copied to %s%s)"
                % (target, target.name, captions.BACKUP_SUFFIX)
            ],
            skipped=True,
        )
        return
    params = dataset_toml.DatasetTomlParams(
        resolution=(settings["target_width"], settings["target_height"]),
        caption_extension=settings["caption_extension"],
    )
    try:
        plan = dataset_toml.plan_dataset_toml(job.target_dir, params)
    except dataset_toml.DatasetTomlError as exc:
        job.set_dataset_toml(None, [str(exc)])
        return
    except OSError as exc:
        job.set_dataset_toml(None, ["failed to scan the destination directory: %s" % exc])
        return
    backup = captions.backup_file(target) if target.is_file() else None
    try:
        _atomic_write_text(target, plan.toml_text)
    except OSError as exc:
        job.set_dataset_toml(None, ["failed to write dataset.toml: %s" % exc])
        return
    job.set_dataset_toml(
        str(target), list(plan.warnings), backup=str(backup) if backup is not None else None
    )


def _plan_kwargs(settings: dict[str, Any]) -> dict[str, Any]:
    return {key: settings[key] for key in _PLAN_KEYS}


def _render_plan_safe(
    plan: scaler.ImagePlan,
) -> tuple[scaler.ImagePlan, scaler.ScaleOutcome | None, str | None]:
    """Pool body: a single failure must not abort the whole batch, so the error is returned, not raised."""
    try:
        return plan, scaler.render_one_image(plan), None
    except Exception as exc:  # noqa: BLE001 - one failure must not abort the whole batch
        return plan, None, "%s: %s" % (type(exc).__name__, exc)


def _export(job: ScaleJob, images: list[Path], settings: dict[str, Any]) -> None:
    """Worker-thread body. One failure does not abort the whole batch (same as autotag).

    Every round has two stages. **Planning** (read the header, allocate the destination name) runs on
    this thread - that is what keeps scaler.unique_destination's never-overwrite rule true without a
    lock. **Rendering** (decode / resample / encode) runs in a pool: Pillow releases the GIL there.
    A cancel is noticed between rounds, so it stops with at most one round in flight rather than
    between two consecutive images.
    """
    try:
        job.target_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        job.fail(job.target_dir, "cannot create the destination directory: %s" % exc)
        return

    plan_kwargs = _plan_kwargs(settings)
    chunk_size = max(1, EXPORT_WORKERS * EXPORT_CHUNK_FACTOR)
    # One reserved-name set for the whole run: a chunk is planned before it is rendered, so a name
    # already promised to an earlier image is not on disk yet and exists() alone would not see it.
    reserved: set[str] = set()
    with ThreadPoolExecutor(max_workers=EXPORT_WORKERS) as pool:
        index = 0
        while index < len(images) and not job.cancelled:
            chunk = images[index : index + chunk_size]
            index += len(chunk)
            plans: list[scaler.ImagePlan] = []
            for src in chunk:
                if job.cancelled:
                    logger.info("job %s received a cancel, planning stops at %s", job.job_id, src)
                    break
                try:
                    dest_dir = scaler.destination_directory(src, job.root, job.target_dir)
                    plans.append(
                        scaler.plan_one_image(src, dest_dir, reserved=reserved, **plan_kwargs)
                    )
                except Exception as exc:  # noqa: BLE001 - one failure must not abort the whole batch
                    job.fail(src, "%s: %s" % (type(exc).__name__, exc))
                    job.advance_failed(src)
            for plan, outcome, error in pool.map(_render_plan_safe, plans):
                if error is not None:
                    job.fail(plan.src, error)
                    job.advance_failed(plan.src)
                else:
                    job.advance(outcome)

    if settings["write_dataset_toml"] and not job.cancelled:
        _write_dataset_toml(job, settings)


def _run_scale_job(job: ScaleJob, images: list[Path], settings: dict[str, Any]) -> None:
    """Background-thread entry point: **always** emits done afterwards, whichever path it takes."""
    try:
        _export(job, images, settings)
    except Exception:  # noqa: BLE001 - no HTTP boundary inside a thread, so it must catch everything itself
        logger.exception("export job %s failed unexpectedly", job.job_id)
    finally:
        job.finish()


def _resolve_source_images(
    raw_images: list[str], root: Path
) -> tuple[list[Path], JSONResponse | None]:
    """Resolve the requested image list. The whole request is rejected on the first bad entry - no partial application.

    Four checks per path, in this order, each with its own answer: the allowlist (403 / 404), it is a
    file (400 `not_a_file`), it is an image (400 `unsupported_image`), and it lies under `root`
    (400 `image_outside_root`). The last one is not pedantry: `scaler.destination_directory` rebuilds
    each destination from the source's path *relative to root*, so an image from outside the source
    directory has no defined place in the target tree.

    Paths are deduplicated case-insensitively (the platform's rule, `os.path.normcase` - the same
    one `unique_destination` reserves names with) while the order the request gave is kept.
    """
    if not raw_images:
        return [], error_response(
            400,
            "no_images",
            "the request carries no images to export",
            params={"path": str(root)},
        )
    resolved: list[Path] = []
    seen: set[str] = set()
    for raw in raw_images:
        try:
            image = paths.resolve_under_roots(str(raw))
        except paths.PathOutsideRoots as exc:
            return [], error_response(403, exc.code, str(exc), params=exc.params)
        except FileNotFoundError as exc:
            return [], error_response(404, "not_found", str(exc), params={"path": str(raw)})
        if not image.is_file():
            return [], error_response(
                400, "not_a_file", "not a file: %s" % image, params={"path": str(image)}
            )
        if image.suffix.lower() not in paths.IMAGE_EXTS:
            return [], error_response(
                400,
                "unsupported_image",
                "unsupported image extension: %s" % image.name,
                params={"name": image.name},
            )
        if not _is_inside(image, root):
            return [], error_response(
                400,
                "image_outside_root",
                "the image is not under the source directory: %s" % image,
                params={"path": str(image)},
            )
        key = os.path.normcase(str(image))
        if key in seen:
            continue
        seen.add(key)
        resolved.append(image)
    return resolved, None


@router.post("/scale/run")
def scale_run(payload: ScaleRequest) -> Any:
    """Start a background export job: 202 {job_id, total}. Disk writes happen only in the worker thread."""
    ensure_roots_configured()
    try:
        root = resolve_query_path(payload.root)
    except paths.PathOutsideRoots as exc:
        return error_response(403, exc.code, str(exc), params=exc.params)
    except FileNotFoundError as exc:
        return error_response(404, "not_found", str(exc), params={"path": str(payload.root)})
    if not root.is_dir():
        return error_response(
            400, "not_a_directory", "not a directory: %s" % root, params={"path": str(root)}
        )

    try:
        # must_exist=False: the export directory may not exist yet (created in the worker thread).
        target_dir = paths.resolve_under_roots(payload.target_dir, must_exist=False)
    except paths.PathOutsideRoots as exc:
        return error_response(403, exc.code, str(exc), params=exc.params)
    except FileNotFoundError as exc:  # in theory must_exist=False means this cannot happen
        return error_response(404, "not_found", str(exc), params={"path": str(payload.target_dir)})
    if target_dir.exists() and not target_dir.is_dir():
        return error_response(
            400,
            "not_a_directory",
            "the target is not a directory: %s" % target_dir,
            params={"path": str(target_dir)},
        )
    if _is_inside(target_dir, root):
        return error_response(
            400,
            "target_inside_root",
            "the target directory must not be the source dataset directory itself or a "
            "subdirectory of it (%s); export results mixed into the dataset would be treated as a "
            "new subset by the trainer's directory scan." % target_dir,
        )

    # The list is resolved after both directories are settled: "the target must not be inside the
    # source" is a fact about the request, and it is cheaper to answer before opening N files.
    images, failure = _resolve_source_images(payload.images, root)
    if failure is not None:
        return failure

    job = ScaleJob(uuid.uuid4().hex, len(images), root, target_dir)
    _register(job)
    settings: dict[str, Any] = {
        "target_width": payload.target_width,
        "target_height": payload.target_height,
        "no_upscale": payload.no_upscale,
        "resample": payload.resample,
        "output_format": payload.output_format,
        "quality": payload.quality,
        "optimize": payload.optimize,
        "caption_extension": payload.caption_extension,
        "write_dataset_toml": payload.write_dataset_toml,
        "overwrite_dataset_toml": payload.overwrite_dataset_toml,
    }
    worker = threading.Thread(
        target=_run_scale_job,
        args=(job, images, settings),
        name="scale-%s" % job.job_id[:8],
        daemon=True,
    )
    worker.start()
    logger.info(
        "export job %s: %d images -> %s (%dx%d, no_upscale=%s, %s)",
        job.job_id,
        len(images),
        target_dir,
        payload.target_width,
        payload.target_height,
        payload.no_upscale,
        payload.output_format,
    )
    return JSONResponse(status_code=202, content={"job_id": job.job_id, "total": len(images)})


def _sse(event: _Event) -> str:
    return "event: %s\ndata: %s\n\n" % (event.name, json.dumps(event.data, ensure_ascii=False))


async def _stream_job(job: ScaleJob) -> AsyncIterator[str]:
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


@router.get("/scale/stream")
async def scale_stream(
    job_id: str = Query(..., description="the job_id returned by POST /api/scale/run"),
) -> Any:
    """SSE progress stream (§4.14): one progress per image and one done{finished:true} at the end."""
    job = get_job(job_id)
    if job is None:
        return error_response(404, "unknown_job", "no such job: %s" % job_id, params={"job_id": str(job_id)})
    return StreamingResponse(
        _stream_job(job),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/scale/cancel")
def scale_cancel(payload: ScaleCancelRequest) -> Any:
    """Request a cancel: set the flag → stop once the current image is written → the worker thread emits done{finished:true} afterwards. Always 202."""
    job = get_job(payload.job_id)
    accepted = bool(job is not None and job.request_cancel())
    if not accepted:
        logger.info("cancel request had no effect (job missing or already finished): %s", payload.job_id)
    return JSONResponse(status_code=202, content={"cancelled": accepted})
