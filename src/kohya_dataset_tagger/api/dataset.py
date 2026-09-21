"""Export dataset.toml: POST `/api/dataset/toml` (p0-spec §4.9), and report whether the file is
already there: GET `/api/dataset/toml/status` (§4.16).

The status endpoint (§4.16, added 2026-09-20)
---------------------------------------------
"Pure computation" above is about the *export*: it never writes, and it never reads the file either, so
it cannot say whether the dataset root already has a `dataset.toml`. That is the one readiness question
the frontend could not answer (its strip could only say "not checked yet"), and it is the fact this
endpoint adds: **one `stat`, on the dataset root's own file name**, and nothing else. It does not open
the file, does not return its contents, and never creates, touches or backs anything up.

Pure computation, **writes no file**
------------------------------------
The P0 decision of §4.9: the server does not write to disk; the frontend offers "copy" and "download .toml". Writing files is a new
destructive surface (paths, overwrite, and backup strategy are all unfrozen), while downloading from the browser is already enough. So this module
only calls `core.dataset_toml.plan_dataset_toml`, and that path only scandir / stat / reads files.

Request and response
--------------------
`{root, params}` -> `{toml, subset_count, image_count, subsets[], warnings, skipped[]}`
(field names and order frozen by §4.9). `root` goes through `resolve_under_roots`: out of bounds 403,
missing 404, not a directory 400 - the same semantics as `/api/fs/list`.

Three wire-shape supplements to §4.9 (2026-09-17, raised while implementing the export UI):

1. `params.resolution` is a two-element `[width, height]` array in JSON (on the Python side it is still
   `tuple[int, int]`) - so the model accepts `list[int]` and converts to a tuple in `to_params()`;
2. the top-level `warnings` is the **aggregate** of each `subsets[].warnings` (core does the aggregation; this module passes it through verbatim);
3. the messages in `warnings` and `skipped[].reason` all start with `[code]` (core generates them).

In `params` the frontend currently sends resolution / batch_size / num_repeats / caption_extension
plus the optional `alpha_mask` (a checkbox, sent only when ticked), and the rest keep §3.9's defaults, so in the model
**every field is optional** and a missing one falls back. `extra="forbid"` for the same reason as `api/autotag.py`:
the request body is a frozen contract, so unknown keys 422 outright rather than being silently dropped (`escape_parens` fell into this trap).

Synchronous endpoint
--------------------
`def` (not `async def`): scanning the image headers of 1653 images is **blocking** work
(measured 0.33 s warm cache, 15 s cold cache), and FastAPI throws synchronous endpoints into a thread pool, so the event loop is not stuck.

Error codes
-----------
`outside_roots` 403 / `not_found` 404 / `not_a_directory` 400 /
`cannot_plan` 400 (no subdirectory under the directory contains images, a parameter is not a finite float) /
`validation_error` 422 (params out of range or an unknown key, shaped uniformly by app.py's exception handler) /
`scan_failed` 500.
"""
from __future__ import annotations

import stat as stat_module
from dataclasses import replace
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..core import dataset_toml, paths
from . import ensure_roots_configured, error_response, resolve_query_path

router = APIRouter(prefix="/api", tags=["dataset"])

__all__ = ["router", "DatasetExportParams", "DatasetExportRequest"]


class DatasetExportParams(BaseModel):
    """Optional overrides for §3.9's `DatasetTomlParams` (all optional = a missing item falls back to §3.9's default).

    Domains are tightened only where **the trainer would clearly reject them or silently train wrong**: `batch_size` / `num_repeats`
    at least 1 (0 would train no image in this subset), and the resolution and bucket sizes are positive integers.
    """

    model_config = ConfigDict(extra="forbid")

    resolution: list[int] | None = None
    batch_size: int | None = Field(None, ge=1)
    caption_extension: str | None = None
    num_repeats: int | None = Field(None, ge=1)
    keep_tokens: int | None = Field(None, ge=0)
    flip_aug: bool | None = None
    shuffle_caption: bool | None = None
    caption_tag_dropout_rate: float | None = None
    caption_dropout_rate: float | None = None
    caption_dropout_every_n_epochs: int | None = None
    alpha_mask: bool | None = None
    enable_bucket: bool | None = None
    bucket_no_upscale: bool | None = None
    min_bucket_reso: int | None = Field(None, ge=1)
    max_bucket_reso: int | None = Field(None, ge=1)
    bucket_reso_steps: int | None = Field(None, ge=1)

    @field_validator("resolution")
    @classmethod
    def _resolution_shape(cls, value: list[int] | None) -> list[int] | None:
        """§4.9 supplement 1: the wire shape is a two-element `[width, height]` array."""
        if value is None:
            return None
        if len(value) != 2:
            raise ValueError("resolution must be two integers [width, height]")
        if any(side <= 0 for side in value):
            raise ValueError("resolution must be two positive integers")
        return value

    @field_validator("caption_extension")
    @classmethod
    def _non_empty_extension(cls, value: str | None) -> str | None:
        """An empty string would make the trainer look for a sidecar ending in an empty string (which never exists) - reject outright.

        Note it does **not** add a dot: when the user writes `txt` we report the same "missing caption" as the trainer,
        rather than guessing `.txt` for them (core's docstring says the same).
        """
        if value is not None and not value.strip():
            raise ValueError("caption_extension cannot be empty")
        return value

    def to_params(self) -> dataset_toml.DatasetTomlParams:
        """Merge with §3.9's defaults: only the explicitly given keys are overridden."""
        provided = self.model_dump(exclude_none=True)
        if "resolution" in provided:
            # §3.9's Python side is tuple[int, int]; only the JSON side is a two-element array.
            provided["resolution"] = tuple(provided["resolution"])
        return replace(dataset_toml.DatasetTomlParams(), **provided)


class DatasetExportRequest(BaseModel):
    """Request body for `POST /api/dataset/toml` (§4.9: `{root, params}`).

    An empty `root` falls back to the first root in the allowlist - the same convention as `/api/fs/list`'s query parameter.
    When `params` is omitted entirely, all of §3.9's defaults are used.
    """

    model_config = ConfigDict(extra="forbid")

    root: str = ""
    params: DatasetExportParams | None = None


def _subset_payload(subset: dataset_toml.SubsetPlan) -> dict[str, Any]:
    """§4.9's `subsets[]` entry: five keys, paths to str."""
    return {
        "image_dir": str(subset.image_dir),
        "image_count": subset.image_count,
        "num_repeats": subset.num_repeats,
        "caption_count": subset.caption_count,
        "warnings": list(subset.warnings),
    }


def _warning_item_payload(item: dataset_toml.WarningItem) -> dict[str, Any]:
    """§4.9 supplement 4's `warning_items[]`: code + params + the same text as the old `warnings[i]`."""
    return {"code": item.code, "params": dict(item.params), "text": item.text}


def _skipped_item_payload(entry: dataset_toml.SkippedDir) -> dict[str, Any]:
    """§4.9 supplement 4's `skipped_items[]`: the old fields + code + params (same order as `skipped[]`)."""
    return {
        "image_dir": str(entry.image_dir),
        "code": entry.code,
        "params": dict(entry.params),
        "reason": entry.reason,
    }


@router.post("/dataset/toml")
def export_dataset_toml(payload: DatasetExportRequest) -> Any:
    """Plan a subset for **every subdirectory** under `root` and render dataset.toml (without writing to disk)."""
    ensure_roots_configured()
    # Errors carry params too: the frontend localizes by msg.<code> + params and falls back to message when incomplete.
    requested = str(payload.root) if payload.root else ""
    try:
        root = resolve_query_path(payload.root)
    except paths.PathOutsideRoots as exc:
        return error_response(403, exc.code, str(exc), params=exc.params)
    except FileNotFoundError as exc:
        return error_response(
            404, "not_found", str(exc), params={"path": requested} if requested else {}
        )
    if not root.is_dir():
        return error_response(400, "not_a_directory", "Not a directory: %s" % root, params={"path": str(root)})

    params = payload.params.to_params() if payload.params is not None else dataset_toml.DatasetTomlParams()
    try:
        plan = dataset_toml.plan_dataset_toml(root, params)
    except dataset_toml.DatasetTomlError as exc:
        return error_response(400, "cannot_plan", str(exc))
    except OSError as exc:  # the directory vanished mid-scan, insufficient permissions, etc.
        return error_response(500, "scan_failed", "Failed to scan dataset: %s" % exc)

    return {
        "toml": plan.toml_text,
        "subset_count": len(plan.subsets),
        "image_count": plan.image_count,
        "subsets": [_subset_payload(subset) for subset in plan.subsets],
        # The legacy fields are unchanged verbatim (§4.9); the next two are **item-parallel** structured companion arrays.
        "warnings": list(plan.warnings),
        "skipped": [
            {"image_dir": str(entry.image_dir), "reason": entry.reason} for entry in plan.skipped
        ],
        "warning_items": [_warning_item_payload(item) for item in plan.warning_items],
        "skipped_items": [_skipped_item_payload(entry) for entry in plan.skipped],
    }


def _toml_absent(root: Path, target: Path) -> dict[str, Any]:
    """§4.16's answer for "nothing there": the path is still reported, so a caller can see where it looked."""
    return {"root": str(root), "path": str(target), "exists": False, "mtime": None, "size": None}


@router.get("/dataset/toml/status")
def dataset_toml_status(
    root: str = Query("", description="Dataset root; empty = the first root in the allowlist"),
) -> Any:
    """Report whether the **dataset root's** `dataset.toml` is there (p0-spec §4.16).

    The subject is the root, not the directory being browsed: the trainer reads `dataset.toml` next to
    the dataset directory, so a nested subdirectory must not answer for the dataset it belongs to.

    Read-only, provably: one `os.stat` on `root / DATASET_TOML_NAME` is the whole interaction with the
    filesystem. The file is never opened (its contents are not part of the answer), a missing one is
    **not created**, and nothing is backed up, touched or renamed - saying what is already there is the
    endpoint's only job, so writing anything would answer a different question.

    `exists` means "a regular file is there" (`stat.S_ISREG`), which is what the trainer's `open()`
    needs. The explicit `stat` (rather than `Path.is_file()`) is deliberate: `is_file()` swallows
    `OSError` and would turn "the root cannot be read" into "no dataset.toml", which is exactly the
    claim this endpoint must not make - a stat failure is 500 `stat_failed` instead.

    `mtime` / `size` are the provenance of the fact (it is a real stat, not a guess); the contents are
    never returned.
    """
    ensure_roots_configured()
    requested = str(root) if root else ""
    try:
        target_root = resolve_query_path(root)
    except paths.PathOutsideRoots as exc:
        return error_response(403, exc.code, str(exc), params=exc.params)
    except FileNotFoundError as exc:
        return error_response(
            404, "not_found", str(exc), params={"path": requested} if requested else {}
        )
    if not target_root.is_dir():
        return error_response(
            400, "not_a_directory", "Not a directory: %s" % target_root, params={"path": str(target_root)}
        )

    target = target_root / dataset_toml.DATASET_TOML_NAME
    try:
        info = target.stat()
    except FileNotFoundError:
        return _toml_absent(target_root, target)
    except OSError as exc:  # permissions, a path component that is not a directory, ...
        return error_response(
            500, "stat_failed", "Failed to read dataset.toml: %s" % exc, params={"path": str(target)}
        )
    if not stat_module.S_ISREG(info.st_mode):
        # A directory (or anything else) with that name is not a dataset.toml the trainer can read:
        # the honest answer to "is the file there?" is no, whatever else sits at that path.
        return _toml_absent(target_root, target)
    return {
        "root": str(target_root),
        "path": str(target),
        "exists": True,
        "mtime": info.st_mtime,
        "size": info.st_size,
    }
