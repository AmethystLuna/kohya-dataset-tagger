"""api package: each module exposes a `router` (an APIRouter instance) that `app.py` discovers dynamically.

**Assembly convention (important)**: every router in this package carries its own `prefix="/api"`, and `app.py` must
call `app.include_router(mod.router)` **without** adding another `prefix="/api"`,
otherwise the paths become `/api/api/...`.

Only two small cross-module helpers live here (the error response shape and query-parameter path resolution).
FastAPI's default `HTTPException` wraps into `{"detail": ...}` and validation failures into `{"detail": [...]}`,
neither of which is the §4 frozen shape, so the api layer returns `JSONResponse` itself.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.responses import JSONResponse

from ..core import paths

__all__ = [
    "error_response",
    "resolve_query_path",
    "ensure_roots_configured",
    "path_config_denied",
    "ERROR_SHAPE",
]


#: The frozen error shape (spec §4) + the `params` added in §4.
ERROR_SHAPE = {"error": {"code": "", "message": "", "params": {}}}


def error_response(
    status_code: int, code: str, message: str, params: dict[str, Any] | None = None
) -> JSONResponse:
    """`{"error": {"code", "message", "params"}}` + a suitable 4xx/5xx.

    The two legacy fields `code` / `message` are unchanged verbatim; `params` is the added **variable data**
    (an empty object when there is none). The frontend looks up localized copy by `msg.<code>` and falls back
    to the backend's own wording when placeholders cannot all be filled - see p0-spec §4 and §4.9 supplement 4.
    """
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, "params": dict(params or {})}},
    )


def ensure_roots_configured() -> tuple[Path, ...]:
    """Return the allowlist roots; when none are configured, fill in once from the environment variable (KOHYA_TAGGER_ROOTS).

    This is the **fail-closed** backstop: it only lets anything through when that environment variable in `config`
    actually declares a root, and still rejects everything when it does not. It rescues assembly mistakes such as
    "app.py forgot to publish config.roots to paths" - in that case every endpoint 403s and the cause is hard to find.
    """
    roots = paths.get_roots()
    if roots:
        return roots
    from .. import config  # lazy import: the core layer does not depend on it, only the api boundary needs it

    env_roots = config.current().roots
    if env_roots:
        paths.configure(env_roots)
        roots = paths.get_roots()
    return roots


def path_config_denied() -> JSONResponse | None:
    """Reject path-config endpoints entirely on a non-loopback address without explicit opt-in (spec §4.11 / §4.12).

    Returns `None` when allowed (the caller continues), otherwise returns a ready-made 403 response (the caller `return`s it).
    What it blocks is "change `--host` to an external address and these endpoints hand over read/write access to any directory" -
    the rationale is written in the security-boundary section at the top of `roots.py`.
    """
    from .. import config  # lazy import: same reason as ensure_roots_configured, only the api boundary needs it

    if config.path_config_allowed():
        return None
    host = config.current().host
    return error_response(
        403,
        "remote_path_config_disabled",
        "The service is bound to %s (a non-loopback address), so path configuration and the graphical directory "
        "picker are disabled; to enable them, start with --allow-remote-path-config." % host,
        params={"host": host},
    )


def resolve_query_path(raw: str | None) -> Path:
    """Resolve a path from a query parameter; an empty value falls back to the first allowlist root.

    Out of bounds raises `PathOutsideRoots` (the caller turns it into 403), missing raises `FileNotFoundError` (404).
    """
    if raw is None or not str(raw).strip():
        roots = ensure_roots_configured()
        if not roots:
            raise paths.PathOutsideRoots(
                "No root allowlist configured (set KOHYA_TAGGER_ROOTS or call config.load())",
                code="paths_unconfigured",
                params={},
            )
        return roots[0]
    return paths.resolve_under_roots(raw)
