"""FastAPI assembly layer: dynamically discover the routers in `api/`, mount static assets, unify the error shape.

Three hard conventions (p0-spec §6, the docstring in api/__init__.py)
---------------------------------------------------------------------
1. Every module under `api/` exposes a `router` that **carries its own `prefix="/api"`**. Here we
   only `include_router(router)` - adding the prefix again would produce `/api/api/...`.
2. **Dynamic discovery**: scan the `api/` package for every module exposing a `router` and register
   it, so a new module needs no change to this file. A module that fails to import is recorded in
   `app.state.router_errors` and warned about, rather than silently yielding fewer routes.
3. **The startup path must call `config.load()`**: only it publishes the roots to `core.paths` (the
   single source of truth for the allowlist). Constructing a `Config` without publishing →
   `paths.get_roots()` is empty → every request is 403 (fail closed).

The error shape is unified as `{"error": {"code", "message"}}` (§4): FastAPI's default
`{"detail": ...}` and the validation-failure `{"detail": [...]}` both violate the contract. Here
`PathOutsideRoots` (→403), `FileNotFoundError` (→404), request validation failure (→422), and
`HTTPException` are all converted to the frozen shape. An out-of-bounds request must land on 403,
not 500 - the exception handlers catch it even when an endpoint forgot its own try.
"""
from __future__ import annotations

import dataclasses
import importlib
import logging
import mimetypes
import pkgutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response

from . import __version__, config
from .api import error_response
from .core import paths

logger = logging.getLogger("kohya_dataset_tagger.app")

__all__ = ["app", "create_app", "discover_routers", "API_PACKAGE", "WEB_DIR"]

#: Static asset root (`web/` is build-free and mounted directly at `/`).
WEB_DIR = Path(__file__).resolve().parent / "web"

#: api package name. Derived from the package's own `__name__` to avoid hard-coding the top-level package name.
API_PACKAGE = __name__.rsplit(".", 1)[0] + ".api"

#: Default code for HTTPException (§4 only requires code to be a string, but stable codes make frontend branching easier).
_STATUS_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    422: "validation_error",
    500: "internal_error",
    503: "unavailable",
}

#: MIME types that must be pinned explicitly. The Windows registry often reports `.js` as
#: `text/plain`, and a browser flatly refuses to load `<script type="module">` with a non-JS MIME
#: (§6 hard convention).
_STATIC_TYPES = (
    ("text/javascript", ".js"),
    ("text/javascript", ".mjs"),
    ("text/css", ".css"),
    ("image/webp", ".webp"),
)


def discover_routers() -> tuple[list[tuple[str, APIRouter]], list[dict[str, str]]]:
    """Scan the api package and return `[(module name, router)]` plus the list of modules that failed to import.

    Sorted by module name so route registration order is deterministic (the `/api/...` routes do not
    overlap, so order only affects the OpenAPI display).
    """
    package = importlib.import_module(API_PACKAGE)
    routers: list[tuple[str, APIRouter]] = []
    errors: list[dict[str, str]] = []
    for info in sorted(pkgutil.iter_modules(package.__path__), key=lambda item: item.name):
        if info.name.startswith("_"):
            continue
        try:
            module = importlib.import_module("%s.%s" % (API_PACKAGE, info.name))
        except Exception as exc:  # noqa: BLE001 - one bad module must not keep the whole service from starting
            logger.warning("api module %s failed to import; its routes will not be registered: %s", info.name, exc)
            errors.append({"module": info.name, "message": "%s: %s" % (type(exc).__name__, exc)})
            continue
        router = getattr(module, "router", None)
        if router is None:
            logger.debug("api module %s does not expose a router, skipping", info.name)
            continue
        routers.append((info.name, router))
    return routers, errors


def _format_validation_errors(exc: RequestValidationError, limit: int = 5) -> str:
    """Compress pydantic's validation errors into one readable line (details are still in the server log)."""
    parts: list[str] = []
    for item in list(exc.errors())[:limit]:
        location = ".".join(str(step) for step in item.get("loc", ())) or "(body)"
        parts.append("%s: %s" % (location, item.get("msg", "invalid")))
    more = len(exc.errors()) - limit
    if more > 0:
        parts.append("... and %d more" % more)
    return "; ".join(parts) or "invalid request body"


def _install_error_handlers(application: FastAPI) -> None:
    """Unify the various exceptions into the §4 error shape."""

    @application.exception_handler(paths.PathOutsideRoots)
    async def _outside_roots(request: Request, exc: paths.PathOutsideRoots) -> JSONResponse:
        # The structured payload comes from the exception itself (§4.9 supplement 4): even when an
        # endpoint forgot its try, it must still carry code/params, otherwise the frontend cannot get
        # the placeholders that msg.<code> needs and can only fall back to the backend's own wording.
        return error_response(
            403,
            getattr(exc, "code", "outside_roots"),
            str(exc),
            params=getattr(exc, "params", None),
        )

    @application.exception_handler(FileNotFoundError)
    async def _not_found(request: Request, exc: FileNotFoundError) -> JSONResponse:
        return error_response(404, "not_found", str(exc) or "file does not exist")

    @application.exception_handler(RequestValidationError)
    async def _invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        return error_response(422, "validation_error", _format_validation_errors(exc))

    @application.exception_handler(StarletteHTTPException)
    async def _http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _STATUS_CODES.get(exc.status_code, "http_%d" % exc.status_code)
        return error_response(exc.status_code, code, str(exc.detail))

    @application.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled exception %s %s", request.method, request.url.path)
        return error_response(500, "internal_error", "%s: %s" % (type(exc).__name__, exc))


def _install_core_routes(application: FastAPI) -> None:
    """`/api/health`: process-level information, not belonging to any business module.

    `/api/roots` used to be here too; on 2026-09-17 it moved to `api/roots.py` (§4.11) - it is no
    longer read-only, and "a read-only process probe" and "a config endpoint that can change the
    allowlist" should not live in the same file; more importantly, registering the same path in two
    places makes whichever wins depend on registration order, which is a time bomb.
    """

    @application.get("/api/health", tags=["app"])
    def health() -> dict[str, Any]:
        """Liveness probe + path configuration capability (§4.12).

        `path_config.allowed` decides whether the frontend shows the graphical directory picker or
        greys it out with an explanation: it is False when bound to a non-loopback address without
        `--allow-remote-path-config`.
        """
        allowed = config.path_config_allowed()
        return {
            "ok": True,
            "version": __version__,
            "path_config": {
                "allowed": allowed,
                "host": config.current().host,
                "reason": None if allowed else "host_not_loopback",
            },
        }


class _RevalidatingStaticFiles(StaticFiles):
    """Static assets must be revalidated against the origin every time (`Cache-Control: no-cache`).

    Why it must be given explicitly: Starlette's `StaticFiles` sends only `ETag`/`Last-Modified`,
    not `Cache-Control`, so the browser caches by **heuristic freshness** (roughly 10% of
    "Last-Modified until now", the older the file the longer it is cached). `web/` is a frontend
    with no build step, so after changing CSS/JS the user still gets the old file on F5 - on
    2026-09-18 someone concluded from this that the left-column layout was not fixed (measured via
    CDP: after a normal refresh the computed style was still the old one).

    `no-cache` does not mean "do not cache", it means "must revalidate against the origin before
    use": if the file is unchanged, 304. On a local service that cost is negligible, and what it
    buys is "after a change, a refresh always takes effect".
    """

    async def get_response(self, path: str, scope: Any) -> Response:
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


def _mount_web(application: FastAPI) -> None:
    """Mount `web/` at `/` (`html=True` makes `/` serve index.html directly).

    Must be mounted **last**: the mount point `/` swallows every request not matched by an earlier route.
    """
    for media_type, suffix in _STATIC_TYPES:
        mimetypes.add_type(media_type, suffix, strict=True)
    if not WEB_DIR.is_dir():
        logger.warning("web/ directory does not exist; static assets will not be mounted: %s", WEB_DIR)
        return
    application.mount(
        "/", _RevalidatingStaticFiles(directory=str(WEB_DIR), html=True), name="web"
    )


def create_app(cfg: config.Config | None = None) -> FastAPI:
    """Assemble the FastAPI application.

    With `cfg=None`: if the allowlist has already been published, reuse it (tests often call
    `config.load(roots=[...])` first), otherwise call `config.load()` to read the environment
    variables and publish the allowlist.
    """
    if cfg is None:
        published = paths.get_roots()
        if published:
            cfg = dataclasses.replace(config.current(), roots=tuple(published))
        else:
            cfg = config.load()
    elif cfg.roots:
        paths.configure(cfg.roots)

    application = FastAPI(
        title="Kohya Dataset Tagger",
        version=__version__,
        description="A local dataset tagger for kohya-style training sets.",
    )

    routers, errors = discover_routers()
    for _name, router in routers:
        application.include_router(router)

    _install_core_routes(application)
    _install_error_handlers(application)
    _mount_web(application)

    application.state.config = cfg
    application.state.router_modules = [name for name, _router in routers]
    application.state.router_errors = errors

    logger.info(
        "assembled %d api modules %s; %d roots in the allowlist",
        len(routers),
        [name for name, _router in routers],
        len(paths.get_roots()),
    )
    if not paths.get_roots():
        logger.warning(
            "allowlist is empty: all /api requests will be 403. Specify roots with KOHYA_TAGGER_ROOTS or config.load(roots=[...])."
        )
    return application


#: `app` for `uvicorn kohya_dataset_tagger.app:app`. **Lazily constructed**: importing this module
#: must have no side effects (no reading environment variables, no publishing the allowlist),
#: otherwise one import in a test would overwrite someone else's allowlist.
_APP: FastAPI | None = None


def __getattr__(name: str) -> Any:
    global _APP
    if name == "app":
        if _APP is None:
            _APP = create_app()
        return _APP
    raise AttributeError("module %r has no attribute %r" % (__name__, name))


if __name__ == "__main__":  # pragma: no cover - convenience entry point (equivalent to python -m kohya_dataset_tagger)
    import uvicorn

    _instance = create_app()
    uvicorn.run(_instance, host=_instance.state.config.host, port=_instance.state.config.port)
