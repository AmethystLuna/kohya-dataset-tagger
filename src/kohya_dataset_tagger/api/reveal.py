"""Open a directory the app is browsing in the operating system's own file manager (spec §4.18).

Why this exists
---------------
Everything else here happens inside the page, and a browser cannot open a folder: a user who wants to
see the real directory - drop a file in, compare two folders side by side, look at what the trainer
wrote - had to find that path by hand from the text on screen. Only the process that owns the
filesystem can hand the window over.

Why a separate module
---------------------
`fs.py` is a **read-only** browsing module and says so in its docstring; this endpoint starts a
program on the machine. That is a different promise about the same directory, and `pick.py` is the
wrong home for the opposite reason: it deliberately ignores the allowlist (§4.12), while this endpoint
does not ignore it at all.

The security boundary, stated openly
------------------------------------
The directory must already be one the app is browsing: the path goes through `core/paths.py` like
every other one, so this endpoint grants no authority that reading the listing or writing a caption
beside it does not already grant. It is therefore **not** behind §4.11's loopback gate - that gate
exists for the endpoints that *widen* the allowlist, which this one cannot. The honest consequence,
written down rather than implied: with `--host` on an external address a remote caller can make a file
manager window appear on this machine's desktop. That is a nuisance, not an escalation, and it stays
inside the allowlist.

Where the window comes from: `explorer.exe` on Windows, `open` on macOS, `xdg-open` elsewhere. A
machine with no launcher at all (a headless server) answers 500 `no_file_manager`; nothing else about
the app changes.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from ..core import paths
from . import ensure_roots_configured, error_response, resolve_query_path

router = APIRouter(prefix="/api", tags=["reveal"])

__all__ = ["router", "launcher_argv", "open_in_file_manager", "FileManagerUnavailable"]


class RevealBody(BaseModel):
    """`{path}` - the directory to show. An empty path falls back to the first allowlist root."""

    model_config = ConfigDict(extra="forbid")

    path: str = ""


class FileManagerUnavailable(RuntimeError):
    """No file-manager launcher for this platform is installed, or it refused to start."""


def launcher_argv(target: Path) -> list[str]:
    """The command that opens `target` in this platform's own file manager."""
    text = str(target)
    if os.name == "nt":
        return ["explorer.exe", text]
    if sys.platform == "darwin":
        return ["open", text]
    return ["xdg-open", text]


def open_in_file_manager(target: Path) -> str:
    """Open `target` in the OS file manager; returns the launcher's own name.

    **Fire and forget, and never through a shell**: the launcher is not waited for (`explorer.exe`
    exits 1 even when it worked, so a return code would be a lie), and its stdio is discarded so a
    chatty launcher can neither block the server nor fill its log. No shell means the path cannot be
    re-interpreted as a command.

    This is the one function in the repository that really starts a program, so the tests replace it
    (`monkeypatch.setattr(reveal, "open_in_file_manager", ...)`) and assert two things: the exact
    directory it was handed, and that it was **not** called at all when the path is refused.
    """
    argv = launcher_argv(target)
    try:
        subprocess.Popen(  # noqa: S603 - fixed argv, no shell, path passed as one argument
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError as exc:
        raise FileManagerUnavailable("no file manager launcher on this system (%s): %s" % (argv[0], exc)) from exc
    except OSError as exc:
        raise FileManagerUnavailable("could not start %s: %s" % (argv[0], exc)) from exc
    return argv[0]


@router.post("/fs/reveal")
def reveal_directory(payload: RevealBody) -> Any:
    """Show one directory in the OS file manager (§4.18).

    Read-only with respect to the dataset - the directory is opened, never modified - but it is a
    **POST**, because it starts a process: a GET has to stay safe to retry, to prefetch and to sit in
    a cache.

    Out of bounds 403 / missing 404 / not a directory 400 / no launcher 500 `no_file_manager`.
    """
    ensure_roots_configured()
    try:
        target = resolve_query_path(payload.path)
    except paths.PathOutsideRoots as exc:
        return error_response(403, exc.code, str(exc), params=exc.params)
    except FileNotFoundError as exc:
        return error_response(404, "not_found", str(exc), params={"path": str(payload.path)})
    if not target.is_dir():
        return error_response(
            400, "not_a_directory", "Not a directory: %s" % target, params={"path": str(target)}
        )
    try:
        launcher = open_in_file_manager(target)
    except FileManagerUnavailable as exc:
        return error_response(500, "no_file_manager", str(exc), params={"path": str(target)})
    return {"path": str(target), "launcher": launcher}
