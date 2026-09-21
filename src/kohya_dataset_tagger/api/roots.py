"""Runtime-editable path configuration (spec §4.11): allowlist roots + tagger model scan directories.

Why a separate module
---------------------
These six endpoints are two halves of one thing - "path configuration"; none of them reads images or writes the dataset,
and what they share is that **changes take effect immediately, no restart**. Stuffing them into `fs.py` or `autotag.py` would make that
module carry two lifecycles at once (read-only browsing / mutable configuration), and their failure modes are completely different.

Three conventions that are easy to get wrong
--------------------------------------------
1. **The allowlist is the one already published in `core.paths`**, not the one in `config`.
   The two agree under normal conditions (`config.load()` publishes it), but the single source of truth for the allowlist is `paths`.
2. **`persisted` is a truthful report, not a promise**: when writing the file fails the state has already taken effect,
   so `changed: true` comes with `persisted: false` + `warning`, not a rollback.
3. **`models` counts only this one root**: what the user wants to see is "what my added directory was recognized as containing",
   and aggregating all roots would count models from other roots against this row.

Security boundary (deliberate, not a hole)
------------------------------------------
`POST` / `DELETE /api/roots` **widen** the allowlist. The service listens only on 127.0.0.1, so the caller and the filesystem user
are the same person; the allowlist stops mistyped paths and out-of-bounds traversal, not another user on the machine.
Change `--host` to an externally listening address and these two endpoints hand over read/write access to any directory.

Since 2026-09-18 this sentence has a matching **gate**: when `--host` is not a loopback address, these four mutating endpoints and
`GET /api/fs/pick` all return 403 `remote_path_config_disabled`, unless `--allow-remote-path-config` is explicitly added
(in which case the startup banner spells out what you opened up).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict

from .. import config
from ..core import paths
from ..autotag import registry
from . import error_response, path_config_denied

logger = logging.getLogger("kohya_dataset_tagger.api.roots")

router = APIRouter(prefix="/api", tags=["roots"])

__all__ = ["router"]


class RootBody(BaseModel):
    """`{path}` - shared by the two POSTs. An empty path is reported by `config` as 400 `bad_request`."""

    model_config = ConfigDict(extra="forbid")

    path: str = ""


# ---------------------------------------------------------------------------
# GET /api/roots - also reports "does this one exist / was it remembered"
# ---------------------------------------------------------------------------


def _root_entries() -> list[dict[str, Any]]:
    """Per-entry state of the allowlist (§4.11). The shape **adds fields** to §4's original table `{path,name}`."""
    current = config.current()
    published = paths.get_roots() or current.roots
    persisted = config.persisted_roots(current)
    entries: list[dict[str, Any]] = []
    for root in published:
        entries.append({
            "path": str(root),
            "name": root.name or str(root),
            "exists": root.exists(),
            "is_dir": root.is_dir(),
            "persisted": config.norm_key(root) in persisted,
        })
    return entries


@router.get("/roots")
def list_roots() -> dict[str, Any]:
    """Allowlist roots + each entry's `exists` / `is_dir` / `persisted` (§4.11)."""
    return {"roots": _root_entries()}


@router.post("/roots")
def create_root(payload: RootBody) -> Any:
    """Add an allowlist root, **effective immediately** (§4.11).

    Idempotent: when already in the allowlist it returns `changed: false` and **writes no file**.
    Missing or not a directory -> 400 `not_a_directory` (an allowlist root may not be a not-yet-created directory).
    Non-loopback address without explicit opt-in -> 403 `remote_path_config_disabled`.
    """
    blocked = path_config_denied()
    if blocked is not None:
        return blocked
    try:
        result = config.add_root(payload.path)
    except config.PathConfigError as exc:
        return error_response(exc.status, exc.code, str(exc), params=exc.params)
    return {"roots": _root_entries(), **result}


@router.delete("/roots")
def delete_root(path: str = Query("", description="Root to remove")) -> Any:
    """Remove an allowlist root (§4.11).

    404 `not_found` = it was not in the allowlist to begin with; 400 `last_root` = it is the last one.
    """
    blocked = path_config_denied()
    if blocked is not None:
        return blocked
    try:
        result = config.remove_root(path)
    except config.PathConfigError as exc:
        return error_response(exc.status, exc.code, str(exc), params=exc.params)
    return {"roots": _root_entries(), **result}


# ---------------------------------------------------------------------------
# GET/POST/DELETE /api/autotag/model-roots - model search roots
# ---------------------------------------------------------------------------


def _models_under(root: Path) -> list[str]:
    """The model ids resolvable **under this one root only** (§4.11).

    All 5 roots measured 12 ms (2026-09-17), so there is no cache and no `?probe=` switch.
    A single unreadable root (permissions, path too long) is not an error: showing 0 models on that row is enough.
    """
    if not root.is_dir():
        return []
    found: list[str] = []
    for model_id in sorted(registry.BUILTIN):
        try:
            registry.resolve(model_id, [root])
        except registry.RegistryError:
            continue
        except OSError as exc:  # noqa: PERF203 - an unreadable directory must not 500 the whole list
            logger.debug("Failed to probe %s under %s: %s", root, model_id, exc)
            continue
        found.append(model_id)
    return found


def _model_root_entries() -> list[dict[str, Any]]:
    """Per-entry state of the search roots; **the order is the priority** and is not reordered (§3.7 / §4.11)."""
    entries: list[dict[str, Any]] = []
    for root, source in config.classify_model_roots(config.current()):
        models = _models_under(root)
        entries.append({
            "path": str(root),
            "source": source,
            "exists": root.exists(),
            "is_dir": root.is_dir(),
            "file_backed": source == "file",
            "models": models,
            "model_count": len(models),
        })
    return entries


@router.get("/autotag/model-roots")
def list_model_roots() -> dict[str, Any]:
    """Model search roots + each entry's source and "what was recognized under this one root" (§4.11)."""
    return {"roots": _model_root_entries()}


@router.post("/autotag/model-roots")
def create_model_root(payload: RootBody) -> Any:
    """Add an **extra** model scan directory, effective immediately (§4.11).

    Appended after the existing `file` entries - still ahead of the auto-discovered positions (explicit > auto-discovered, §3.7).
    """
    blocked = path_config_denied()
    if blocked is not None:
        return blocked
    try:
        result = config.add_model_root(payload.path)
    except config.PathConfigError as exc:
        return error_response(exc.status, exc.code, str(exc), params=exc.params)
    return {"roots": _model_root_entries(), **result}


@router.delete("/autotag/model-roots")
def delete_model_root(path: str = Query("", description="Model search root to remove")) -> Any:
    """Delete a model search root (§4.11).

    For a `file` source, the matching line in the file is deleted too; otherwise it only lasts this run (`removed_scope: session`).
    Deleting them all does not error: the model list becomes empty and tagging falls back to `model_unavailable`, but the UI can still add the directory back.
    """
    blocked = path_config_denied()
    if blocked is not None:
        return blocked
    try:
        result = config.remove_model_root(path)
    except config.PathConfigError as exc:
        return error_response(exc.status, exc.code, str(exc), params=exc.params)
    return {"roots": _model_root_entries(), **result}
