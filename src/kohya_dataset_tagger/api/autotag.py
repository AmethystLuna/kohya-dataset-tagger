"""WD14 tagging: GET `/api/autotag/models`, POST `/api/autotag/preview`,
POST `/api/autotag/run`, GET `/api/autotag/stream`, POST `/api/autotag/cancel`
(§4, §4.1, §4.3, §4.4), plus model download POST `/api/autotag/download` (§4.6-§4.8).

Four frozen constraints
-----------------------
1. **preview never writes to disk**: it reads the caption (`read_caption`) to compute `before`,
   inference + postprocessing to compute `after`, and touches no write endpoint. End to end it is
   backstopped by the fingerprint from `tools/dataset_manifest.py`.
2. **SSE termination must emit `finished: true`** (§4.3): you cannot rely on `done >= total` alone,
   because `total` can be wrong after a mid-run failure. One `progress` per processed image.
3. **Cancel is a real cancel** (§4.4): `POST /api/autotag/cancel` sets the flag, the current image
   finishes and then it stops, followed by a `done{finished:true}`. The frontend does not stop the
   backend by closing the EventSource.
4. **No network requests in a GET** (§4.7): `/api/autotag/models` takes `size_bytes` from the local
   file size only, rather than fetching the HF manifest for one byte count—that would hang the
   model list on the availability of an external endpoint.

Concurrency model
-----------------
Inference is CPU-bound (0.47 s/image on this machine), so:
- `preview` / `models` are `def` endpoints — FastAPI puts them in a thread pool itself, without
  blocking the event loop;
- `run` starts a **background thread** to run the whole batch and the HTTP call returns `job_id`
  immediately;
- `download` is the same (§4.7): 202 + `job_id`, the 1.26 GB eva02 must never sit on a request thread;
- `stream` is `async def` + `asyncio.sleep` polling a task snapshot — it takes no thread-pool thread
  and does not block the event loop;
- ONNX sessions are resident per model id (1.5 s / 445 MB to load) and loaded once per process;
- `preview` is a synchronous request, so it has a **hard limit of 32 images** (§4.5 ②): over that it
  returns 400 `too_many_images` outright, instead of making the browser wait ten-odd minutes. To
  preview a larger range, the right move is to turn preview into a job+SSE too (P1).

Two task kinds, one SSE
-----------------------
Tagging and download share one `AutotagJob` + `GET /api/autotag/stream` and are told apart by the
`phase` in the event (§4.7): with `"infer"`, `done/total` is in **images**; with `"download"` it is
in **bytes** and `current` is a file name. Adding fields is backward compatible—the frontend only
reads specific keys.

The download implementation (`autotag/download.py`, §4.8) is **imported lazily**: that workflow
arriving late must not 404 models/preview/run/stream/cancel along with it; when the module is absent
only `/api/autotag/download` returns 503.

The domains of `rating` and `write_mode` live in `postprocess.RATING_MODES` and `WRITE_MODES`
respectively, and anything out of range is a 400 — a silent fallback would make the user think their
own settings had run.

`write_mode` semantics (§4 froze only the names, not the meanings; here they are nailed down and
written into the code)
------------------------------------------------------------------------------
**The default is `backup_overwrite` (since 2026-09-19)**: the default path must not be the only one
that **irreversibly** throws away an existing caption.

| mode | what is written |
|---|---|
| `backup_overwrite` | **default**: first back up the existing caption **verbatim** as `<caption>.bak`, then overwrite with the new tags only (an existing backup is not overwritten; no backup is left behind when the content is unchanged) |
| `overwrite` | the new tags only |
| `append` | existing tags + new tags (concatenated verbatim, not deduplicated) |
| `prepend` | new tags + existing tags |
| `merge` | existing tags + those of the new tags **not already present** (stable dedup) |
| `only_if_empty` | writes only if the existing caption is empty (or the file does not exist); otherwise **not a single byte is touched** (nor the cache deleted) |

Existing tags are parsed with `captions.split_tags` (the same rule as the trainer), so the caption
recomposed by write_mode is exactly what the trainer will read.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from .. import config
from ..core import captions, paths
from ..autotag import WD14Tagger, registry
from ..autotag import postprocess as postprocess_module
from . import error_response

logger = logging.getLogger("kohya_dataset_tagger.api.autotag")

router = APIRouter(prefix="/api", tags=["autotag"])

__all__ = ["router", "AutotagJob", "WRITE_MODES", "DOWNLOAD_MODULE_NAME"]

#: The domain of `write_mode` (§4).
WRITE_MODES: tuple[str, ...] = ("backup_overwrite", "overwrite", "append", "prepend", "only_if_empty", "merge")

#: The default write mode. `overwrite` irreversibly throws away an existing caption, so the default
#: became back-up-then-overwrite (a user request on 2026-09-19); old callers that pass `overwrite`
#: explicitly see no change.
DEFAULT_WRITE_MODE = "backup_overwrite"

#: The default model: the strongest v3 on this machine (`local-model-inventory.md`). Only if it cannot be found does it fall back to the first available model in lexicographic order.
PREFERRED_MODEL = "wd-swinv2-tagger-v3"

#: Upper bound on simultaneously retained jobs. A job snapshot stores all progress events, so it must stay bounded.
MAX_JOBS = 32

#: SSE poll interval / idle keepalive interval (seconds). Polling takes no thread-pool thread, and a 50 ms progress delay is invisible to the eye.
POLL_SECONDS = 0.05
KEEPALIVE_SECONDS = 15.0

#: Hard limit per preview (§4.5 ②). preview is a synchronous request, about 0.8 s/image on CPU:
#: 32 images ≈ 26 seconds is an acceptable synchronous ceiling and enough to cover the real-dataset
#: usage of "1–20 images per directory".
MAX_PREVIEW_IMAGES = 32

#: The module holding the download implementation (§4.8). **Imported lazily**: when it is absent only
#: `/api/autotag/download` returns 503 while the other autotag routes keep working—a parallel
#: workflow arriving late must not 404 the whole route group.
DOWNLOAD_MODULE_NAME = __package__.rsplit(".", 1)[0] + ".autotag.download"

#: Minimum byte interval between download progress events. The download implementation calls back
#: every 1 MiB (download.CHUNK_SIZE) and the event log is replayed from memory—the 1.26 GB eva02
#: yields thousands of callbacks.
DOWNLOAD_EVENT_BYTES = 512 * 1024

#: Module-level names the download implementation might use to declare "which models have a known
#: repository". §4.8 does **not freeze** its name, so they are probed in order; when none is found it
#: falls back to "the module exists + the id is in BUILTIN" (see `_downloadable`).
_DOWNLOAD_PREDICATE_NAMES = ("is_downloadable", "can_download", "has_repo", "repo_id_for")
_DOWNLOAD_CATALOG_NAMES = ("MODEL_REPOS", "REPOS", "REPO_IDS", "CATALOG", "REPOSITORIES")

#: Our own model directories (relative to %LOCALAPPDATA%). Same source as the one in
#: config.default_model_search_roots; downloads land here **preferentially** and never in HF's hub
#: cache directory (see _download_root).
OWN_MODEL_DIRNAME = ("kohya-dataset-tagger", "models")

#: Markers that say "this looks like an HF cache directory". HF does not recognize the flat
#: <root>/<model_id>/model.onnx shape, and stuffing 1.26 GB in there only soils someone else's cache
#: directory, so download destinations avoid them.
_HF_CACHE_MARKERS = ("huggingface", "models--", "hf-mirror")

#: Static download sizes: the byte count of model.onnx, **unknown is 0** (§4.7's download_size_bytes).
#:
#: Why it is needed: size_bytes is the size of what is **installed locally** (not installed = 0,
#: because a GET must not make network requests), so the UI cannot show "how big is the download"
#: when the model is not installed. This table hard-codes the measured values and sends no request.
#:
#: **Only measured values go in; never fill in from memory** (frozen by the parent agent on
#: 2026-09-17). Source: the byte count of the same-named model.onnx fetched via hf_hub_download in
#: the local HF cache (measured 2026-09-17); wd-eva02-large-tagger-v3 was measured by the parent
#: agent. It is a UI hint, not a criterion—the real byte count is given by the manifest at download
#: time and verified one by one (§4.8). The table goes stale when the repository changes revision;
#: re-measure then.
DOWNLOAD_SIZE_BYTES: dict[str, int] = {
    "wd-eva02-large-tagger-v3": 1260435999,   # measured by the parent agent (1.26 GB)
    "wd-swinv2-tagger-v3": 467460978,         # measured from the local HF cache (= 445.8 MiB)
    "wd-vit-tagger-v3": 378536310,            # measured from the local HF cache
    "wd-vit-tagger-v3-comfy": 378536310,      # the same onnx as wd-vit-tagger-v3 (measured same size)
    "wd-v1-4-swinv2-tagger-v2": 455481275,    # measured from the local HF cache
    "wd-v1-4-vit-tagger-v2": 373287959,       # measured from the local HF cache
    # wd-convnext-tagger-v3: not present locally and never measured → 0 (do not guess)
}


class PostprocessBody(BaseModel):
    """The sub-shape of `postprocess` (frozen in §4.1). Defaults are aligned with sd-scripts
    `--thresh` / observed traps.

    `escape_parens` **must default to true** (§4.1): the vocabulary yields
    `keqing_(genshin_impact)`, underscore removal makes it `keqing (genshin impact)`, and the 1049
    files in the dataset plus this repository's own inference prompts all use the escaped form. Here
    we only **pass it through**—the translation happens in the postprocessing layer, so the API layer
    must not drop it.

    `extra="forbid"`: an unknown key is a straight 422, **not silently dropped**. The reason is the
    pitfall `escape_parens` itself hit—pydantic ignores extra keys by default, so the frontend sends
    something, the backend does not pick it up, the user's checkbox becomes decorative, and there is
    no error to look up. Under a frozen shape, one extra key should be loud.
    """

    model_config = ConfigDict(extra="forbid")

    remove_underscore: bool = True
    escape_parens: bool = True
    undesired_tags: list[str] = Field(default_factory=list)
    always_first_tags: list[str] = Field(default_factory=list)
    character_tags_first: bool = False
    character_tag_expand: bool = False
    rating: str = "none"
    tag_replacement: dict[str, str] = Field(default_factory=dict)
    sort_danbooru: bool = False

    def to_options(self) -> postprocess_module.PostprocessOptions:
        """Convert to autotag-layer options; an out-of-range `rating` raises ValueError (the caller turns it into a 400)."""
        rating = (self.rating or "none").strip().lower()
        if rating not in postprocess_module.RATING_MODES:
            raise ValueError(
                "rating must be one of %s, got %r" % (list(postprocess_module.RATING_MODES), self.rating)
            )
        return postprocess_module.PostprocessOptions(
            remove_underscore=self.remove_underscore,
            escape_parens=self.escape_parens,
            undesired_tags=tuple(tag for tag in self.undesired_tags if tag),
            always_first_tags=tuple(tag for tag in self.always_first_tags if tag),
            character_tags_first=self.character_tags_first,
            character_tag_expand=self.character_tag_expand,
            tag_replacement=dict(self.tag_replacement),
            sort_danbooru=self.sort_danbooru,
            rating_mode=rating,
        )


class AutotagRequest(BaseModel):
    """Request body for `/preview` and `/run` (§4.1). `thresholds` defaults to 0.35 across the board.

    `write_mode` is read by `/run` only. `/preview` deliberately ignores it: the preview answers
    "what does the tagger produce" (§4.5 (1) lists its request body without the field), and the mode
    decides how the run later composes that with what is on disk - so its `after` is the same for
    every mode, and a client must not read it as the post-run caption.
    """

    images: list[str]
    model: str | None = None
    thresholds: dict[str, float] | None = None
    postprocess: PostprocessBody | None = None
    write_mode: str | None = None


class CancelRequest(BaseModel):
    """Request body for POST `/api/autotag/cancel` (§4.4)."""

    job_id: str


class DownloadRequest(BaseModel):
    """Request body for POST `/api/autotag/download` (§4.7): just a `model`.

    `extra="forbid"` for the same reason as `PostprocessBody`: the request body is a **frozen
    contract**, so an unknown key is a straight 422. Neither the download destination nor the
    endpoint comes from here (`KOHYA_TAGGER_MODELS` / `KOHYA_TAGGER_HF_ENDPOINT`, see §4.6), so an
    extra key always means the caller got something wrong.
    """

    model_config = ConfigDict(extra="forbid")

    model: str


class ModelUnavailable(LookupError):
    """No usable model at all (none specified and none discovered locally)."""


# ---------------------------------------------------------------------------
# Model resolution and session cache
# ---------------------------------------------------------------------------


def _search_roots() -> tuple[Path, ...]:
    return tuple(config.current().model_search_roots)


def _pick_model(requested: str | None) -> str:
    """The model from the request; when absent, pick the default `PREFERRED_MODEL`, else the first available model in lexicographic order."""
    if requested:
        return requested
    found = registry.discover(_search_roots())
    if not found:
        raise ModelUnavailable("no usable model found locally; point KOHYA_TAGGER_MODELS at a search root")
    return PREFERRED_MODEL if PREFERRED_MODEL in found else found[0]


def _model_error(exc: Exception) -> JSONResponse:
    """Map the two kinds of `registry` failures to 400 / 409."""
    if isinstance(exc, registry.UnknownModel):
        return error_response(400, "unknown_model", str(exc))
    if isinstance(exc, ModelUnavailable):
        return error_response(409, "model_unavailable", str(exc))
    return error_response(409, "model_unavailable", str(exc))


_SESSIONS: dict[str, WD14Tagger] = {}
_SESSION_LOCK = threading.Lock()


def _session(model_id: str) -> WD14Tagger:
    """Get (loading if needed) the resident ONNX session. A load failure propagates as-is; the caller maps the status code."""
    with _SESSION_LOCK:
        cached = _SESSIONS.get(model_id)
    if cached is not None:
        return cached

    onnx_path, vocab_path = registry.resolve(model_id, _search_roots())
    logger.info("loading tagger model %s: %s", model_id, onnx_path)
    instance = WD14Tagger(onnx_path, vocab_path)
    with _SESSION_LOCK:
        return _SESSIONS.setdefault(model_id, instance)


# ---------------------------------------------------------------------------
# Request body validation
# ---------------------------------------------------------------------------


def _thresholds(payload: AutotagRequest) -> dict[str, float]:
    """Threshold table validation: must be numbers in 0..1 (pydantic already guarantees a float)."""
    table: dict[str, float] = {}
    for key, value in (payload.thresholds or {}).items():
        number = float(value)
        if not 0.0 <= number <= 1.0:
            raise ValueError("threshold must be between 0..1: %s=%r" % (key, value))
        table[key] = number
    return table


def _options(payload: AutotagRequest) -> postprocess_module.PostprocessOptions:
    body = payload.postprocess or PostprocessBody()
    return body.to_options()


def _resolve_images(raw_images: list[str]) -> tuple[list[Path], JSONResponse | None]:
    """Resolve each image path in turn. Out of bounds → 403, missing → 404 (the whole request is rejected; no partial application)."""
    resolved: list[Path] = []
    for raw in raw_images:
        try:
            image = paths.resolve_under_roots(str(raw))
        except paths.PathOutsideRoots as exc:
            return [], error_response(403, exc.code, str(exc), params=exc.params)
        except FileNotFoundError as exc:
            return [], error_response(404, "not_found", str(exc), params={"path": str(raw)})
        if not image.is_file():
            return [], error_response(400, "not_a_file", "not a file: %s" % image, params={"path": str(image)})
        if image.suffix.lower() not in paths.IMAGE_EXTS:
            return [], error_response(400, "unsupported_image", "unsupported image extension: %s" % image.name, params={"name": image.name})
        resolved.append(image)
    return resolved, None


def _tag_scores(
    tagger: WD14Tagger,
    image: Path,
    thresholds: dict[str, float],
    options: postprocess_module.PostprocessOptions,
) -> list[Any]:
    """Inference → threshold filtering → postprocessing (matching sd-scripts' two-stage flow)."""
    scores = tagger.predict(image)
    kept = postprocess_module.apply_thresholds(scores, thresholds)
    return postprocess_module.postprocess_tags(kept, options)


def _tag_payload(item: Any) -> dict[str, Any]:
    return {"tag": item.tag, "score": float(item.score), "category": item.category}


# ---------------------------------------------------------------------------
# Lazy wiring for the download implementation (§4.8)
# ---------------------------------------------------------------------------


def _download_module() -> Any | None:
    """Get `autotag/download.py`; return None when the module is absent (or the import fails).

    **No caching**: caching would nail the "module has not landed yet" state into the process and
    would stop tests from swapping it out. `importlib.import_module` only consults `sys.modules`
    anyway, so the cost is negligible.
    """
    try:
        return importlib.import_module(DOWNLOAD_MODULE_NAME)
    except ImportError as exc:
        logger.debug("download implementation unavailable (%s): %s", DOWNLOAD_MODULE_NAME, exc)
        return None


def _looks_like_hf_cache(path: Path) -> bool:
    text = path.as_posix().lower()
    return any(marker in text for marker in _HF_CACHE_MARKERS)


def _own_model_dirs() -> tuple[Path, ...]:
    """Our own model directories. **The in-repo models/ comes first**, then
    %LOCALAPPDATA%/kohya-dataset-tagger/models.

    Order reversed on 2026-09-17: the in-repo models/ is the directory shown to the user—it holds a
    placeholder file whose **name literally says "put the tagger models in this directory"**. It must
    come first, otherwise a user follows that and puts models there while downloads land in
    %LOCALAPPDATA%, making the sentence a lie.
    """
    own: list[Path] = [Path(__file__).resolve().parents[3] / "models"]
    local = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if local:
        own.append(Path(local).joinpath(*OWN_MODEL_DIRNAME))
    return tuple(own)


def _same_dir(left: Path, right: Path) -> bool:
    return os.path.normcase(os.path.abspath(str(left))) == os.path.normcase(os.path.abspath(str(right)))


def _download_root() -> Path | None:
    """Which model root a download lands in (order decided by the parent agent on 2026-09-17).

    1. the root **explicitly declared by KOHYA_TAGGER_MODELS** (the user decides);
    2. the **in-repo models/** (the directory the placeholder file points at);
    3. %LOCALAPPDATA%/kohya-dataset-tagger/models;
    4. the first of the remaining search roots that **does not look like an HF cache directory**;
    5. only HF cache roots left → None (the caller returns 409 no_model_root).

    Two hard constraints:

    - **Never land in HF's hub cache directory**. That is huggingface_hub's home, and one eva02
      download stuffs 1.26 GB of the **flat** <model_id>/model.onnx into it—HF does not recognize
      that shape at all and it only soils someone else's cache directory. Better not to download
      (409 + send the user to set KOHYA_TAGGER_MODELS).
    - A candidate must be **among config's search roots**: downloading into a directory the registry
      does not search is the same as not downloading (the "still installed:false after downloading"
      symptom is the flip side of this constraint).

    A candidate directory need not exist—the download implementation mkdir -p's it; whether it is
    "writable" is only known by actually writing, so this does not pretend to check with os.access
    (which ignores ACLs on Windows).
    """
    roots = _search_roots()
    if not roots:
        return None
    if os.environ.get(config.ENV_MODELS, "").strip():
        return roots[0]  # explicit config: config already orders them as the user gave
    for own_dir in _own_model_dirs():
        for root in roots:
            if _same_dir(root, own_dir):
                return root
    for root in roots:
        if not _looks_like_hf_cache(root):
            return root
    return None


def _downloadable(model_id: str, module: Any | None) -> bool:
    """Whether this model can be installed locally via `POST /api/autotag/download`.

    **Purely local, no network requests** (the same reason as §4.7: hanging the model list on the
    availability of an external endpoint is unacceptable). Decision order:

    1. the download implementation is absent / no model search root is configured → False (nowhere
       to put it even if downloaded);
    2. the download implementation declares a predicate or a repository catalog → follow it (§4.8
       does not freeze those names, so probe the candidate names);
    3. neither → "module present + id in `BUILTIN`" counts as downloadable. The reason:
       `plan(model_id)` is the authority and it needs the network; always returning False would
       leave all 7 models without a download button, i.e. the feature would effectively not be
       wired up. A mistake is not silent either—a failed download job goes into `errors`.
    """
    if module is None or not _search_roots() or model_id not in registry.BUILTIN:
        return False
    for attr in _DOWNLOAD_PREDICATE_NAMES:
        predicate = getattr(module, attr, None)
        if callable(predicate):
            try:
                return bool(predicate(model_id))
            except Exception:  # noqa: BLE001 - a faulty predicate must not 500 the models list
                logger.warning("the download implementation's %s(%r) raised; treating as not downloadable", attr, model_id, exc_info=True)
                return False
    for attr in _DOWNLOAD_CATALOG_NAMES:
        table = getattr(module, attr, None)
        if isinstance(table, dict):
            return model_id in table
    return True


def _local_size(onnx_path: Path) -> int:
    """Byte count of the local ONNX; 0 when unavailable (§4.7). **This only stats, it makes no network request.**"""
    try:
        return int(onnx_path.stat().st_size)
    except OSError as exc:
        logger.debug("cannot get model size %s: %s", onnx_path, exc)
        return 0


# ---------------------------------------------------------------------------
# GET /api/autotag/models
# ---------------------------------------------------------------------------


@router.get("/autotag/models")
def autotag_models() -> Any:
    """List the built-in models and their local state (§4, §4.7):

    `{models:[{id,available,installed,size_bytes,download_size_bytes,downloadable,
    onnx_path,vocab_path,description}]}`

    - `installed` and `available` share one source (`registry.resolve` succeeding or not);
      `available` is the old §4 field, kept for an already-wired frontend;
    - `size_bytes` = the byte count of the **local** ONNX, 0 when not installed or unavailable.
      It makes **no network request** to ask the HF manifest—that would hang the model list on the
      availability of an external endpoint (§4.7);
    - `download_size_bytes` = the **static** download size (`DOWNLOAD_SIZE_BYTES`, 0 when
      unknown), also with no network request. It fills exactly the gap in `size_bytes`: the UI can
      show "how big is the download" even when the model is not installed;
    - `downloadable` see `_downloadable`.
    """
    roots = _search_roots()
    module = _download_module()
    models: list[dict[str, Any]] = []
    for model_id in sorted(registry.BUILTIN):
        spec = registry.BUILTIN[model_id]
        entry: dict[str, Any] = {
            "id": model_id,
            "available": False,
            "installed": False,
            "size_bytes": 0,
            "download_size_bytes": int(DOWNLOAD_SIZE_BYTES.get(model_id, 0)),
            "downloadable": _downloadable(model_id, module),
            "onnx_path": None,
            "vocab_path": None,
            "description": spec.description,
        }
        try:
            onnx_path, vocab_path = registry.resolve(model_id, roots)
        except registry.RegistryError as exc:
            logger.debug("model %s unavailable: %s", model_id, exc)
        else:
            entry["available"] = True
            entry["installed"] = True
            entry["size_bytes"] = _local_size(onnx_path)
            entry["onnx_path"] = str(onnx_path)
            entry["vocab_path"] = str(vocab_path)
        models.append(entry)
    return {"models": models}


# ---------------------------------------------------------------------------
# POST /api/autotag/preview — read-only
# ---------------------------------------------------------------------------


@router.post("/autotag/preview")
def autotag_preview(payload: AutotagRequest) -> Any:
    """Tagging preview: `{items:[{image,before,after,tags:[{tag,score,category}]}]}`. **Writes nothing.**

    The limit check comes **before** path resolution: an over-limit request is 400ed without even
    doing a `stat` (§4.5 ②).
    """
    if len(payload.images) > MAX_PREVIEW_IMAGES:
        return error_response(
            400,
            "too_many_images",
            "preview accepts at most %d images at a time (synchronous request, about 0.8 s/image on "
            "CPU), got %d; preview in batches (run has no such limit—it is a background job)"
            % (MAX_PREVIEW_IMAGES, len(payload.images)),
        )
    images, failure = _resolve_images(payload.images)
    if failure is not None:
        return failure
    if not images:
        return {"items": []}

    # The shape of the request first, this machine's environment second: a malformed threshold or
    # postprocess option is the caller's mistake whatever is installed, and answering "no model" to a
    # bad request made the status code depend on what happens to be downloaded (found by running the
    # suite on a machine with no model at all).
    try:
        thresholds = _thresholds(payload)
        options = _options(payload)
    except ValueError as exc:
        return error_response(400, "bad_request", str(exc))
    try:
        model_id = _pick_model(payload.model)
    except ModelUnavailable as exc:
        return _model_error(exc)
    try:
        tagger = _session(model_id)
    except registry.RegistryError as exc:
        return _model_error(exc)
    except Exception as exc:  # noqa: BLE001 - a missing onnxruntime / a corrupt model both count as a load failure
        logger.exception("model load failed: %s", model_id)
        return error_response(500, "model_load_failed", "model load failed: %s: %s" % (type(exc).__name__, exc))

    items: list[dict[str, Any]] = []
    for image in images:
        before, _multiline = captions.read_caption(image)  # read-only
        tags = _tag_scores(tagger, image, thresholds, options)
        items.append({
            "image": str(image),
            "before": before,
            "after": postprocess_module.format_tags(tags),
            "tags": [_tag_payload(item) for item in tags],
        })
    return {"items": items}


# ---------------------------------------------------------------------------
# Jobs: POST /api/autotag/run, GET /api/autotag/stream, POST /api/autotag/cancel
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Event:
    seq: int
    name: str
    data: dict[str, Any]


class AutotagJob:
    """A background job's progress snapshot + event log (shared by tagging and download, §4.3/§4.7).

    The event log is **complete** (one `progress` per image + one `done` at the end), so an
    `EventSource` that connects later can still replay the whole run—the frontend opens the stream
    only after POST /run, by which time the job may already have processed several images.

    `phase` tells the two job kinds apart: with `"infer"`, `done/total` is in **images**; with
    `"download"` it is in **bytes** and `current` is a file name (§4.7).
    """

    def __init__(
        self,
        job_id: str,
        total: int,
        model_id: str,
        write_mode: str,
        *,
        phase: str = "infer",
    ) -> None:
        self.job_id = job_id
        self.total = total
        self.model_id = model_id
        self.write_mode = write_mode
        self.phase = phase
        self.created = time.time()
        self.errors: list[dict[str, str]] = []
        self._done = 0
        self._current = ""
        self._last_emit: int | None = None
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

    def fail(self, image: Path | str, message: str) -> None:
        """Record one error; every subsequent event carries it out."""
        with self._lock:
            self.errors.append({"path": str(image), "message": message})

    def advance(self, image: Path | str) -> None:
        """One image finished: `done += 1` and emit one `progress`."""
        with self._lock:
            self._done += 1
            self._current = str(image)
            self._events.append(_Event(len(self._events), "progress", self._progress_locked()))

    def report_download(self, name: str, done: int, total: int) -> None:
        """The download implementation reports progress once (the §4.8 `progress(name, done, total)`
        callback).

        `current` = a **file name**, `done`/`total` = **bytes** (§4.7). The numbers are **copied
        verbatim** from the callback, neither converted nor accumulated here: §4.8 froze only the
        callback shape `(name, done, total)` and never said whether done means "this file done" or
        "the whole job done"—copying works under either convention (both are in bytes), while
        accumulating would double-count the cumulative one. The measured implementation's
        convention is **whole-job cumulative** (`download._ProgressSink` accumulates done across
        files and total is the manifest's total bytes), so the frontend gets one global progress
        bar; `total` therefore comes from the callback, and passing 0 when constructing the job is
        fine.

        **Throttling**: within one file, emit at least every `DOWNLOAD_EVENT_BYTES` (or 1/200 of
        the total, whichever is larger); always emit on a file change or when the download is
        complete. The callback itself fires every `download.CHUNK_SIZE` (1 MiB), so the 1.26 GB
        eva02 produces thousands of callbacks, and storing each one would pile up a long event
        list.
        """
        with self._lock:
            name = str(name)
            done = max(0, int(done))
            total = max(0, int(total))
            if total:
                self.total = total
            step = max(DOWNLOAD_EVENT_BYTES, self.total // 200 if self.total else 0)
            emit = (
                name != self._current
                or self._last_emit is None
                or done >= self.total
                or abs(done - self._last_emit) >= step
            )
            self._current = name
            self._done = done
            if not emit:
                return
            self._last_emit = done
            self._events.append(_Event(len(self._events), "progress", self._progress_locked()))

    def finish(self) -> None:
        """Emit the terminal `done{finished:true}`. Idempotent—a repeated call emits nothing more."""
        with self._lock:
            if self._finished:
                return
            self._finished = True
            self._events.append(_Event(len(self._events), "done", self._done_locked()))

    def request_cancel(self) -> bool:
        """Set the cancel flag. A job that already finished returns False (the frontend uses that to tell "there is nothing to cancel")."""
        with self._lock:
            if self._finished:
                return False
            self._cancel.set()
            return True

    # ---- called by the SSE endpoint ------------------------------------

    def events_since(self, cursor: int) -> tuple[list[_Event], bool]:
        """Return the events with `seq >= cursor` and whether the job has finished."""
        with self._lock:
            events = [event for event in self._events if event.seq >= cursor]
            return events, self._finished

    # ---- internal ------------------------------------------------------

    def _progress_locked(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "done": self._done,
            "total": self.total,
            "current": self._current,
            "errors": [dict(item) for item in self.errors],
        }

    def _done_locked(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "done": self._done,
            "total": self.total,
            "finished": True,
            "errors": [dict(item) for item in self.errors],
        }


_JOBS: dict[str, AutotagJob] = {}
_JOBS_LOCK = threading.Lock()


def _register(job: AutotagJob) -> None:
    with _JOBS_LOCK:
        if len(_JOBS) >= MAX_JOBS:
            finished = sorted(
                (item for item in _JOBS.values() if item.finished), key=lambda item: item.created
            )
            for stale in finished[: max(1, len(_JOBS) - MAX_JOBS + 1)]:
                _JOBS.pop(stale.job_id, None)
        _JOBS[job.job_id] = job


def get_job(job_id: str) -> AutotagJob | None:
    with _JOBS_LOCK:
        return _JOBS.get(job_id)


def _compose_tags(existing: str, new_tags: list[str], mode: str) -> list[str] | None:
    """Combine the new tags with the existing caption per `write_mode`; None = write nothing this time (only_if_empty)."""
    current = captions.split_tags(existing)
    if mode == "overwrite":
        return new_tags
    if mode == "only_if_empty":
        return None if current else new_tags
    if mode == "append":
        return current + new_tags
    if mode == "prepend":
        return new_tags + current
    if mode == "merge":
        seen = set(current)
        return current + [tag for tag in new_tags if tag not in seen]
    raise ValueError("unknown write_mode: %r" % mode)


def _write_tags(image: Path, tags: list[Any], mode: str) -> None:
    """Write one image to disk. With `only_if_empty` and existing content it **writes nothing**
    (not even the cache is touched).

    `backup_overwrite`: before overwriting, back up the existing caption **byte for byte** as
    `<caption>.bak`. The backup trade-offs (do not overwrite an existing backup, leave no backup
    when the content did not change) live in `captions.backup_caption`.
    """
    names = [item.tag for item in tags]
    if mode == "overwrite":
        captions.write_caption(image, names)
        return
    if mode == "backup_overwrite":
        # The same normalization as write_caption, so "whether to back up" and "whether the content really changes" share one rule
        captions.backup_caption(image, captions.normalize_for_write(names))
        captions.write_caption(image, names)
        return
    existing, _multiline = captions.read_caption(image)
    composed = _compose_tags(existing, names, mode)
    if composed is None:
        return
    captions.write_caption(image, composed)


def _run_job(
    job: AutotagJob,
    images: list[Path],
    thresholds: dict[str, float],
    options: postprocess_module.PostprocessOptions,
) -> None:
    """Background-thread body: infer + write each image, and **always** emit `done` at the end (the exception path too)."""
    try:
        try:
            tagger = _session(job.model_id)
        except Exception as exc:  # noqa: BLE001 - no HTTP boundary inside a thread, so it must catch everything itself
            job.fail("", "model load failed: %s: %s" % (type(exc).__name__, exc))
            return
        for image in images:
            if job.cancelled:
                logger.info("job %s received a cancel, stopping at %s", job.job_id, image)
                break
            try:
                tags = _tag_scores(tagger, image, thresholds, options)
                _write_tags(image, tags, job.write_mode)
            except Exception as exc:  # noqa: BLE001 - one failure must not abort the whole batch
                job.fail(image, "%s: %s" % (type(exc).__name__, exc))
            job.advance(image)
    finally:
        job.finish()


@router.post("/autotag/run")
def autotag_run(payload: AutotagRequest) -> Any:
    """Start a background tagging job: `{job_id}`. Disk writes happen only in the worker thread."""
    if not payload.images:
        return error_response(400, "empty_image_list", "images must not be empty", params={})
    images, failure = _resolve_images(payload.images)
    if failure is not None:
        return failure

    try:
        thresholds = _thresholds(payload)
        options = _options(payload)
    except ValueError as exc:
        return error_response(400, "bad_request", str(exc))

    write_mode = (payload.write_mode or DEFAULT_WRITE_MODE).strip().lower()
    if write_mode not in WRITE_MODES:
        modes = str(list(WRITE_MODES))
        return error_response(400, "bad_write_mode", "write_mode must be one of %s" % list(WRITE_MODES), params={"modes": modes})

    # The environment comes second, exactly as in `autotag_preview`: everything above is a property of
    # the request, everything below is a property of this machine.
    try:
        model_id = _pick_model(payload.model)
    except ModelUnavailable as exc:
        return _model_error(exc)

    # Do only the cheap path resolution (glob); the real ONNX session load waits for the worker
    # thread: that way a missing model file surfaces right away at the HTTP layer, while the 445 MB
    # load does not occupy a request thread.
    try:
        registry.resolve(model_id, _search_roots())
    except registry.RegistryError as exc:
        return _model_error(exc)

    job = AutotagJob(uuid.uuid4().hex, len(images), model_id, write_mode)
    _register(job)
    worker = threading.Thread(
        target=_run_job,
        args=(job, images, thresholds, options),
        name="autotag-%s" % job.job_id[:8],
        daemon=True,
    )
    worker.start()
    logger.info("job %s: %d images, model %s, write_mode=%s", job.job_id, len(images), model_id, write_mode)
    return {"job_id": job.job_id}


def _sse(event: _Event) -> str:
    """One SSE message: `event: <name>` + `data: <json>`. The JSON contains no bare newline."""
    return "event: %s\ndata: %s\n\n" % (event.name, json.dumps(event.data, ensure_ascii=False))


async def _stream_job(job: AutotagJob) -> AsyncIterator[str]:
    """Replay the job events + follow new ones, and close the stream right after `done`.

    Polling rather than `Condition.wait`: this is an `async def` generator, so waiting must yield
    to the event loop, whereas a `threading.Condition` wait blocks the thread-pool thread.
    """
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


@router.get("/autotag/stream")
async def autotag_stream(
    job_id: str = Query(..., description="the job_id returned by POST /api/autotag/run"),
) -> Any:
    """SSE progress stream (§4.3), shared by tagging and download (§4.7):

    Each `progress` = one image (`phase="infer"`) or a chunk of downloaded bytes (`phase="download"`),
    with one `done{finished:true}` at the end. The finish signal cannot rely on `done >= total` alone.
    """
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


@router.post("/autotag/cancel")
def autotag_cancel(payload: CancelRequest) -> Any:
    """Request a cancel (§4.4): set the flag → stop once the current image finishes → the worker
    thread then emits `done{finished:true}`.

    Always 202: `cancelled=false` means "there is no running job" (unknown id or already finished),
    and the frontend uses that to tidy up the UI instead of treating it as an error.
    """
    job = get_job(payload.job_id)
    accepted = bool(job is not None and job.request_cancel())
    if not accepted:
        logger.info("cancel request had no effect (job missing or already finished): %s", payload.job_id)
    return JSONResponse(status_code=202, content={"cancelled": accepted})


# ---------------------------------------------------------------------------
# POST /api/autotag/download — automatic model download (§4.6-§4.8)
# ---------------------------------------------------------------------------


#: In-flight download jobs: model_id -> job_id. Clicking download again for the same model **reuses
#: it** rather than writing the same <name>.part in parallel (two interleaved writers would fail
#: verification on both sides).
_INFLIGHT_DOWNLOADS: dict[str, str] = {}
_INFLIGHT_LOCK = threading.Lock()


def _claim_download(model_id: str, job: AutotagJob) -> str:
    """Claim the download slot for this model and return the job_id the caller should hand to the
    frontend.

    If an **unfinished** download job already exists, return its id (the frontend then streams the
    same SSE); completed/vanished bookkeeping is cleared along the way, so "click again after a
    failure" still starts a new job. Registration and claiming happen under one lock, so two
    concurrent POSTs cannot both claim successfully.
    """
    with _INFLIGHT_LOCK:
        current = _INFLIGHT_DOWNLOADS.get(model_id)
        if current is not None:
            existing = get_job(current)
            if existing is not None and not existing.finished:
                return current
        _register(job)
        _INFLIGHT_DOWNLOADS[model_id] = job.job_id
        return job.job_id


def _release_download(model_id: str, job_id: str) -> None:
    with _INFLIGHT_LOCK:
        if _INFLIGHT_DOWNLOADS.get(model_id) == job_id:
            _INFLIGHT_DOWNLOADS.pop(model_id, None)


def _run_download_job(job: AutotagJob, module: Any, model_id: str, dest_root: Path) -> None:
    """Background-thread body: call §4.8's `download()` and **always** emit `done` at the end.

    A failure (including a byte-count mismatch) goes into the job's `errors`, and **no progress is
    emitted to indicate success**—`done{finished:true}` is emitted either way, so the frontend never
    gets stuck "waiting for the end". A cancel does not count as a failure (same semantics as
    tagging, §4.4).
    """
    try:
        result = module.download(
            model_id,
            dest_root,
            progress=job.report_download,
            cancel=lambda: job.cancelled,
        )
    except Exception as exc:  # noqa: BLE001 - no HTTP boundary inside a thread, so it must catch everything itself
        if job.cancelled:
            logger.info("download job %s was cancelled: %s: %s", job.job_id, type(exc).__name__, exc)
        else:
            logger.exception("download job %s failed", job.job_id)
            job.fail(model_id, "download failed: %s: %s" % (type(exc).__name__, exc))
    else:
        if job.cancelled:
            logger.info("download job %s was cancelled (the download implementation returned normally)", job.job_id)
        else:
            logger.info(
                "download job %s finished: %s endpoint=%s bytes=%s skipped_existing=%s",
                job.job_id,
                getattr(result, "onnx_path", "?"),
                getattr(result, "endpoint", "?"),
                getattr(result, "bytes_downloaded", "?"),
                getattr(result, "skipped_existing", "?"),
            )
            # Downloaded but still unresolvable = the on-disk layout / search roots do not match
            # (the two layouts of §4.6). **Log only**: this is usually a search root not fully
            # configured, and it must not turn a successful download into a failure in the events.
            try:
                registry.resolve(model_id, _search_roots())
            except registry.RegistryError as exc:
                logger.warning("download finished but the registry still cannot resolve %s (layout/search roots): %s", model_id, exc)
    finally:
        job.finish()
        _release_download(model_id, job.job_id)


@router.post("/autotag/download")
def autotag_download(payload: DownloadRequest) -> Any:
    """Download a built-in model automatically: **202** `{job_id}`, with progress reusing `GET /api/autotag/stream` (§4.7).

    The request thread does only three cheap things (validate the id, find the download implementation,
    pick the destination root); the real download runs in a worker thread: eva02's onnx is 1.26 GB,
    and hanging that on the request thread would freeze the service.

    - 400 `unknown_model`: the id is not in `registry.BUILTIN` (the frontend should only send ids
      from the list);
    - 503 `download_unavailable`: `autotag/download.py` is absent or failed to import (§4.8);
    - 409 `no_model_root`: no model search root is configured, so there is nowhere to put it.

    An already-installed model **still starts a job as usual**: §4.8 requires idempotency
    (`skipped_existing=True`), so "click download again" must not become an error.

    While the same model is **already downloading** (a double-click / two tabs), the same job_id is
    returned: otherwise two threads would write the same `<name>.part` in parallel and neither would
    pass the byte-count verification.
    """
    requested = (payload.model or "").strip()
    if requested not in registry.BUILTIN:
        return error_response(
            400,
            "unknown_model",
            "unknown model id: %r; available: %s" % (payload.model, sorted(registry.BUILTIN)),
        )
    module = _download_module()
    if module is None:
        return error_response(
            503,
            "download_unavailable",
            "the download implementation is unavailable (%s is missing or failed to import). You can "
            "manually place model.onnx and the vocabulary CSV from the same directory into "
            "<model root>/%s/ (the flat layout of §4.6) and the registry will recognize it"
            % (DOWNLOAD_MODULE_NAME, requested),
        )
    dest_root = _download_root()
    if dest_root is None:
        return error_response(
            409, "no_model_root", "no model search root is configured (KOHYA_TAGGER_MODELS), so there is nowhere to put the downloaded model"
        )

    job = AutotagJob(uuid.uuid4().hex, 0, requested, "", phase="download")
    claimed = _claim_download(requested, job)
    if claimed != job.job_id:
        logger.info("model %s already has download job %s, reusing it", requested, claimed)
        return JSONResponse(status_code=202, content={"job_id": claimed})
    worker = threading.Thread(
        target=_run_download_job,
        args=(job, module, requested, dest_root),
        name="autotag-dl-%s" % job.job_id[:8],
        daemon=True,
    )
    worker.start()
    logger.info("download job %s: %s -> %s", job.job_id, requested, dest_root)
    return JSONResponse(status_code=202, content={"job_id": job.job_id})
