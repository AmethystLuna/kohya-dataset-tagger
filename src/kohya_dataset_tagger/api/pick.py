"""Graphical directory picker: `GET /api/fs/pick` + `POST /api/fs/pick/mkdir` (spec §4.12).

Why a separate module (instead of stuffing it into `fs.py`)
-----------------------------------------------------------
`fs.py`'s docstring promises clearly: sub-entries outside the allowlist are **not listed** ("if it is visible, it must be clickable").
Yet the picker must find directories **outside** the allowlist - adding a directory to the allowlist is essentially picking a
directory that is still outside it. Hiding this capability inside `fs.py` would turn that promise into a lie, so it gets its own layer
and the rules are stated openly:

- list **directories** only: no files, no image counts, no reading file contents, no touching thumbnails;
- what it returns is one level of directory names + the filesystem roots (drive letters), with no recursion and no statistics;
- both endpoints **ignore the allowlist** and check the **loopback gate** instead: a non-loopback address always gets 403 (§4.12);
- `POST /api/fs/pick/mkdir` is the only **write** step of the same capability ("create directory"), so it shares the same gate
  as the picker and is never allowed through on its own.

The security boundary is the same level as §4.11: when the service listens only on 127.0.0.1, the caller and the filesystem user are the same person.
The real risk is binding it to an external address - then `--allow-remote-path-config` must be explicitly turned on.
"""
from __future__ import annotations

import os
import string
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict

from .. import config
from . import error_response, path_config_denied

router = APIRouter(prefix="/api", tags=["pick"])

__all__ = ["router"]

#: Characters always forbidden in a directory name: the two path separators + the Windows reserved characters.
#: POSIX allows some of them, but the picker must produce the same directory names on both platforms, so it is a blanket rule.
_FORBIDDEN_NAME_CHARS = set('/\\<>:"|?*')


class MkdirBody(BaseModel):
    """Request body for `POST /api/fs/pick/mkdir`: create a subdirectory named `name` under `parent`."""

    model_config = ConfigDict(extra="forbid")

    parent: str = ""
    name: str = ""


def _bad_name_reason(name: str) -> str | None:
    """Give a reason when the directory name is invalid, return None when valid.

    Only a **single path component** is accepted: a name with a separator would let "parent + name" escape the parent,
    so `a/b`, `..\\x`, and absolute paths are all blocked here, rather than relying on the resolve further down to catch them.
    """
    if not name:
        return "Directory name cannot be empty"
    if name in (".", ".."):
        return "Directory name cannot be . or .."
    if name != name.strip():
        return "Directory name cannot have leading or trailing whitespace"
    if name.endswith("."):
        return "Directory name cannot end with a dot"
    if any(char in _FORBIDDEN_NAME_CHARS for char in name):
        return "Directory name cannot contain / \\ < > : \" | ? *"
    if any(ord(char) < 32 for char in name):
        return "Directory name cannot contain control characters"
    return None


def _filesystem_roots() -> list[str]:
    """Filesystem roots usable for navigation: existing drive letters on Windows, `/` on POSIX."""
    if os.name != "nt":
        return ["/"]
    found: list[str] = []
    for letter in string.ascii_uppercase:
        candidate = "%s:\\" % letter
        try:
            if os.path.isdir(candidate):
                found.append(candidate)
        except OSError:  # devices like an empty card reader error out here; just skip them
            continue
    return found


def _default_start() -> str:
    """Starting point when `path` is not given: the first allowlist root, then the user home directory."""
    roots = config.current().roots
    if roots:
        return str(roots[0])
    return str(Path.home())


def _list_subdirs(directory: Path) -> list[dict[str, str]]:
    """List direct subdirectories only, sorted case-insensitively. Unreadable entries are skipped - one unreadable entry must not 500 the whole page."""
    found: list[Path] = []
    with os.scandir(directory) as entries:
        for entry in entries:
            try:
                if entry.is_dir():
                    found.append(directory / entry.name)
            except OSError:
                continue
    found.sort(key=lambda item: item.name.casefold())
    return [{"name": item.name, "path": str(item)} for item in found]


@router.get("/fs/pick")
def pick_directory(
    path: str = Query("", description="Directory to list; empty = the first allowlist root / the user home directory"),
) -> Any:
    """List the subdirectories of any directory (§4.12). Non-loopback 403 / missing 404 / not a directory 400."""
    blocked = path_config_denied()
    if blocked is not None:
        return blocked

    raw = path.strip() or _default_start()
    try:
        target = Path(raw).expanduser().resolve()
    except OSError as exc:
        return error_response(400, "bad_path", "Cannot resolve path %s: %s" % (raw, exc), params={"path": raw, "detail": str(exc)})
    if not target.exists():
        return error_response(404, "not_found", "Path does not exist: %s" % target, params={"path": str(raw)})
    if not target.is_dir():
        return error_response(400, "not_a_directory", "Not a directory: %s" % target, params={"path": str(raw)})
    try:
        dirs = _list_subdirs(target)
    except PermissionError as exc:
        return error_response(403, "unreadable", "Cannot read this directory: %s (%s)" % (target, exc), params={"target": str(target), "detail": str(exc)})
    except OSError as exc:
        return error_response(400, "bad_request", "Cannot read %s: %s" % (target, exc))

    parent = target.parent
    return {
        "path": str(target),
        "parent": None if parent == target else str(parent),
        "dirs": dirs,
        "roots": _filesystem_roots(),
        "home": str(Path.home()),
    }


@router.post("/fs/pick/mkdir")
def make_directory(payload: MkdirBody) -> Any:
    """Create a subdirectory under any directory (§4.12). The user's own words: "I can neither type manually nor create a new folder".

    The same level as `GET /api/fs/pick`, for the same reason: **the allowlist does not apply** (a new dataset directory is created outside it),
    so it checks the **loopback gate**. The name allows only one path component and never `..` or a separator - creating a directory must not become
    a write path that escapes the parent.

    When a directory of the same name already exists, it **idempotently** returns `created: false`: what the user wants is "go to that directory", so an existing one should not error.
    When a path of the same name is a **file**, 409 `already_exists` - it is never overwritten.
    """
    blocked = path_config_denied()
    if blocked is not None:
        return blocked

    parent_raw = payload.parent.strip()
    if not parent_raw:
        return error_response(
            400, "bad_path", "No parent directory given", params={"parent": payload.parent}
        )
    reason = _bad_name_reason(payload.name)
    if reason is not None:
        return error_response(
            400, "bad_name", "%s: %s" % (reason, payload.name), params={"name": payload.name}
        )
    try:
        parent = Path(parent_raw).expanduser().resolve()
    except OSError as exc:
        return error_response(
            400, "bad_path", "Cannot resolve path %s: %s" % (parent_raw, exc),
            params={"path": parent_raw, "detail": str(exc)},
        )
    if not parent.exists():
        return error_response(404, "not_found", "Path does not exist: %s" % parent, params={"path": parent_raw})
    if not parent.is_dir():
        return error_response(
            400, "not_a_directory", "Not a directory: %s" % parent, params={"path": parent_raw}
        )

    target = parent / payload.name
    try:
        target.mkdir()
    except FileExistsError:
        if target.is_dir():
            return {"path": str(target), "created": False}
        return error_response(
            409, "already_exists", "A file with this name already exists and cannot be used as a directory: %s" % target,
            params={"path": str(target)},
        )
    except PermissionError as exc:
        return error_response(
            403, "unwritable", "Cannot write to this directory: %s (%s)" % (parent, exc),
            params={"path": str(parent), "detail": str(exc)},
        )
    except OSError as exc:
        return error_response(
            400, "bad_name", "Cannot create directory %s: %s" % (target, exc),
            params={"path": str(target), "detail": str(exc)},
        )
    return {"path": str(target), "created": True}
