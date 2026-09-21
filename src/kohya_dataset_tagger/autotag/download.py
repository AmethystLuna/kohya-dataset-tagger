"""Automatic model download (p0-spec §4.6 / §4.8).

Contract (point by point against §4.8)
--------------------------------------
* **Endpoint order**: KOHYA_TAGGER_HF_ENDPOINT (a single one, highest priority) -> https://huggingface.co
  -> https://hf-mirror.com. Tried in order, and whichever succeeds is remembered (DownloadResult.endpoint).
  ModelScope is **not** supported (§4.6: both the path structure and the repo naming differ, and
  the existing ones are all third-party mirrors).
* The **manifest** goes through {endpoint}/api/models/{repo_id}?blobs=true (measured: the main site
  and the mirror are word-for-word identical); only model.onnx and selected_tags.csv are taken; the
  byte count comes from siblings[].size, and when the manifest gives no size it degrades to one
  HEAD / Range probe.
* **Streaming download**: httpx's iter_bytes writes to disk chunk by chunk at CHUNK_SIZE, and the
  1.26 GB eva02 onnx is never read into memory at once.
* Write <name>.part first, then os.replace after verification; **every failure / cancellation path
  deletes the .part**.
* **Verify the byte count after download** against the manifest; a mismatch -> error and delete,
  never leave half a model behind (other files already written successfully in this call are rolled
  back too).
* Already present with the right size -> return directly, skipped_existing=True (idempotent).

Two levels of idempotency
-------------------------
1. **Local manifest (sidecar, zero requests)**: after a successful download, write
   .kohya-dataset-tagger-download.json in the same directory (schema version + each file's byte
   count + the endpoint that succeeded). On the next call, if the sidecar and the files on disk
   **match size for size**, return skipped_existing=True directly -- without sending a single HTTP request.
2. **Remote manifest (one manifest request)**: when the sidecar is absent / does not match (for
   example the user followed §4.6 and put the files into <root>/<model_id>/ themselves), fetch one
   manifest; if the local file sizes match it, skip downloading those two files
   (skipped_existing=True, without sending any resolve/main request).

Level 2 is the source of correctness for the "if it will not download, download it yourself and
drop it in" fallback (together with the registry's flat-layout discovery); level 1 just saves it
from needing the network in normal use.

Concurrency and cancellation
----------------------------
progress / cancel are both synchronous callbacks; the **caller** (the worker thread in
api/autotag.py) is responsible for thread safety. The progress callback guarantees **done is
monotonically non-decreasing**: an endpoint fallback restarts from 0 (the .part was deleted), so
_ProgressSink clamps done and keeps the progress bar from moving backwards. When cancel() returns
True it immediately raises DownloadCancelled and cleans up, **without triggering an endpoint
fallback** (a user cancellation is not an endpoint failure).
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import httpx

from .registry import BUILTIN, UnknownModel

__all__ = [
    "CHUNK_SIZE",
    "DEFAULT_ENDPOINTS",
    "HF_ENDPOINT_ENV",
    "PART_SUFFIX",
    "REPO_OVERRIDES",
    "REQUIRED_FILES",
    "SIDECAR_NAME",
    "DownloadCancelled",
    "DownloadError",
    "DownloadPlan",
    "DownloadResult",
    "download",
    "endpoints",
    "installed_size",
    "is_downloadable",
    "local_paths",
    "manifest_url",
    "plan",
    "repo_id",
    "resolve_url",
]

#: Default endpoints (the order frozen in §4.8).
DEFAULT_ENDPOINTS: tuple[str, ...] = ("https://huggingface.co", "https://hf-mirror.com")

#: The environment variable for endpoint override (a single endpoint, highest priority).
HF_ENDPOINT_ENV = "KOHYA_TAGGER_HF_ENDPOINT"

#: Only these two files are taken (§4.8). The order = download order = DownloadPlan.files order.
REQUIRED_FILES: tuple[str, ...] = ("model.onnx", "selected_tags.csv")

#: Temporary file suffix: write it first, os.replace on success, always delete on failure/cancellation.
PART_SUFFIX = ".part"

#: Streaming write chunk size. 1 MiB x 1200 chunks ≈ eva02's 1.26 GB; smaller only adds progress events for nothing.
CHUNK_SIZE = 1024 * 1024

#: The local manifest (sidecar) file name. Starts with a dot, so it will not collide with the *.csv / model.onnx discovery globs.
SIDECAR_NAME = ".kohya-dataset-tagger-download.json"
SIDECAR_SCHEMA = 1

#: Request timeout: connect 15 s; read 120 s is the limit for **each read operation**, not for the
#: whole file (a slow mirror must not time out just because "1.26 GB cannot be read in time").
TIMEOUT = httpx.Timeout(120.0, connect=15.0)

USER_AGENT = "kohya-dataset-tagger"

#: Parse the repo id from ModelSpec.onnx_glob's HF cache prefix: models--<org>--<name>/...
_HF_CACHE_RE = re.compile(r"^models--(?P<org>[^/]+?)--(?P<name>[^/]+?)/")

#: Model ids without an HF cache glob (flat-layout aliases / ComfyUI copies) get an explicit upstream repo.
REPO_OVERRIDES: dict[str, str] = {
    "wd-vit-tagger-v3-comfy": "SmilingWolf/wd-vit-tagger-v3",
}


class DownloadError(RuntimeError):
    """Download failed: manifest unavailable, transfer interrupted, byte count mismatch."""


class DownloadCancelled(DownloadError):
    """cancel() returned True: the user aborted. **Does not trigger an endpoint fallback**."""


@dataclass(frozen=True)
class DownloadPlan:
    """The list of files to download for one model: ((in-repo path, expected byte count), ...)."""

    model_id: str
    repo_id: str
    files: tuple[tuple[str, int], ...]
    total_bytes: int

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(name for name, _size in self.files)

    def sizes(self) -> dict[str, int]:
        return dict(self.files)


@dataclass(frozen=True)
class DownloadResult:
    """The result of one successful download (or idempotent skip)."""

    model_id: str
    onnx_path: Path
    vocab_path: Path
    endpoint: str
    bytes_downloaded: int
    skipped_existing: bool


# ---------------------------------------------------------------------------
# Endpoints and repositories
# ---------------------------------------------------------------------------


def _normalize_endpoint(value: str) -> str:
    return value.strip().rstrip("/")


def endpoints() -> tuple[str, ...]:
    """Endpoint priority: KOHYA_TAGGER_HF_ENDPOINT -> huggingface.co -> hf-mirror.com.

    The environment variable only **prepends** one endpoint; the two defaults always come after --
    a misconfiguration cannot cut off the fallback. Deduplicated, trailing slashes removed, and
    every returned item can be concatenated with /api/models/... directly.
    """
    candidates: list[str] = []
    override = _normalize_endpoint(os.environ.get(HF_ENDPOINT_ENV, ""))
    if override:
        candidates.append(override)
    candidates.extend(DEFAULT_ENDPOINTS)
    unique: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        if item and item not in seen:
            seen.add(item)
            unique.append(item)
    return tuple(unique)


def repo_id(model_id: str) -> str:
    """model_id -> HF repo id (<org>/<name>).

    Prefer parsing ModelSpec.onnx_glob's HF cache prefix (models--<org>--<name>/...); when that
    fails, consult REPO_OVERRIDES. Neither -> UnknownModel (the model is not downloadable).
    """
    spec = BUILTIN.get(model_id)
    if spec is None:
        raise UnknownModel("unknown model id: %r; available: %s" % (model_id, sorted(BUILTIN)))
    match = _HF_CACHE_RE.match(spec.onnx_glob)
    if match is not None:
        return "%s/%s" % (match.group("org"), match.group("name"))
    override = REPO_OVERRIDES.get(model_id)
    if override:
        return override
    raise UnknownModel(
        "model %r has no corresponding HF repo (glob %r is not an HF cache layout and is not in REPO_OVERRIDES), cannot download"
        % (model_id, spec.onnx_glob)
    )


def is_downloadable(model_id: str) -> bool:
    """Whether this model id can be downloaded automatically (§4.7's downloadable)."""
    try:
        repo_id(model_id)
    except UnknownModel:
        return False
    return True


def manifest_url(endpoint: str, repo: str) -> str:
    """{endpoint}/api/models/{repo_id}?blobs=true (only blobs=true carries each file's size)."""
    return "%s/api/models/%s?blobs=true" % (endpoint.rstrip("/"), repo)


def resolve_url(endpoint: str, repo: str, name: str) -> str:
    """{endpoint}/{repo_id}/resolve/main/{in-repo path} (both endpoints share one structure, §4.6)."""
    return "%s/%s/resolve/main/%s" % (endpoint.rstrip("/"), repo, name)


def local_paths(model_id: str, dest_root: Path | str) -> tuple[Path, Path]:
    """Download destinations: <dest_root>/<model_id>/model.onnx and .../selected_tags.csv (flat layout).

    dest_root must be one of the registry's search roots, otherwise the download is not discoverable (§4.6).
    """
    directory = Path(dest_root) / model_id
    return directory / REQUIRED_FILES[0], directory / REQUIRED_FILES[1]


def installed_size(model_id: str, dest_root: Path | str) -> int:
    """The byte count of the local model.onnx; 0 when absent (§4.7's size_bytes)."""
    onnx_path, _vocab = local_paths(model_id, dest_root)
    try:
        return onnx_path.stat().st_size if onnx_path.is_file() else 0
    except OSError:
        return 0


def _client() -> httpx.Client:
    return httpx.Client(follow_redirects=True, timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


def _fetch_manifest(client: httpx.Client, endpoint: str, repo: str) -> Mapping[str, Any]:
    url = manifest_url(endpoint, repo)
    response = client.get(url, headers={"Accept": "application/json"})
    if response.status_code != 200:
        raise DownloadError(
            "manifest request failed: HTTP %d %s (%s)" % (response.status_code, response.reason_phrase, url)
        )
    try:
        data = response.json()
    except ValueError as exc:
        raise DownloadError("manifest is not valid JSON (%s): %s" % (url, exc)) from exc
    if not isinstance(data, Mapping):
        raise DownloadError("manifest is not a JSON object (%s): %s" % (url, type(data).__name__))
    return data


def _manifest_entries(data: Mapping[str, Any]) -> dict[str, int | None]:
    """siblings -> {rfilename: size or None} (?blobs=true carries size only then)."""
    entries: dict[str, int | None] = {}
    siblings = data.get("siblings")
    if not isinstance(siblings, Sequence):
        return entries
    for item in siblings:
        if not isinstance(item, Mapping):
            continue
        name = item.get("rfilename")
        if not isinstance(name, str) or not name:
            continue
        size: Any = item.get("size")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            lfs = item.get("lfs")
            size = lfs.get("size") if isinstance(lfs, Mapping) else None
        entries[name] = size if isinstance(size, int) and not isinstance(size, bool) and size >= 0 else None
    return entries


def _probe_size(client: httpx.Client, endpoint: str, repo: str, name: str) -> int:
    """Fallback when the manifest gives no size: HEAD for content-length, otherwise a 1-byte Range read of content-range.

    Only 1 byte is fetched, **without** downloading the model.
    """
    url = resolve_url(endpoint, repo, name)
    response = client.head(url)
    if response.status_code in (200, 206):
        length = response.headers.get("content-length")
        if length is not None and length.isdigit():
            return int(length)
    probe = client.get(url, headers={"Range": "bytes=0-0"})
    if probe.status_code in (200, 206):
        content_range = probe.headers.get("content-range", "")
        total = content_range.rsplit("/", 1)[-1] if "/" in content_range else probe.headers.get("content-length", "")
        if total.isdigit():
            return int(total)
    raise DownloadError(
        "cannot get the byte count of %s: HEAD %d / Range %d (%s)" % (name, response.status_code, probe.status_code, url)
    )


def _plan_from_manifest(
    client: httpx.Client,
    model_id: str,
    repo: str,
    endpoint: str,
    data: Mapping[str, Any],
) -> DownloadPlan:
    entries = _manifest_entries(data)
    files: list[tuple[str, int]] = []
    for name in REQUIRED_FILES:
        if name not in entries:
            raise DownloadError(
                "the manifest of %s@%s has no %s (only %s)" % (repo, endpoint, name, sorted(entries))
            )
        size = entries[name]
        if size is None:
            size = _probe_size(client, endpoint, repo, name)
        files.append((name, size))
    return DownloadPlan(
        model_id=model_id,
        repo_id=repo,
        files=tuple(files),
        total_bytes=sum(size for _name, size in files),
    )


def _plan_at(endpoint: str, model_id: str, repo: str) -> DownloadPlan:
    """The manifest from a single endpoint. Transport errors (httpx.HTTPError) propagate as-is, and the caller decides whether to fall back."""
    with _client() as client:
        data = _fetch_manifest(client, endpoint, repo)
        return _plan_from_manifest(client, model_id, repo, endpoint, data)


def plan(model_id: str, *, endpoint: str | None = None) -> DownloadPlan:
    """Fetch model_id's download manifest (read-only, downloads no files).

    When endpoint is given, use only it; otherwise fall back in the order of endpoints(), and when
    all fail the exception message **carries each endpoint's own error**.
    """
    repo = repo_id(model_id)
    if endpoint is not None:
        return _plan_at(endpoint, model_id, repo)
    failures: list[tuple[str, BaseException]] = []
    for base in endpoints():
        try:
            return _plan_at(base, model_id, repo)
        except DownloadCancelled:
            raise
        except (DownloadError, httpx.HTTPError) as exc:
            failures.append((base, exc))
    raise _all_endpoints_failed(model_id, failures)


def _all_endpoints_failed(model_id: str, failures: Sequence[tuple[str, BaseException]]) -> DownloadError:
    lines = ["%s: all download endpoints failed (tried %d)" % (model_id, len(failures))]
    for endpoint, exc in failures:
        lines.append("  - %s: %s: %s" % (endpoint, type(exc).__name__, exc))
    return DownloadError("\n".join(lines))


# ---------------------------------------------------------------------------
# Local manifest (sidecar)
# ---------------------------------------------------------------------------


def _same_size(path: Path, expected: int) -> bool:
    try:
        return path.is_file() and path.stat().st_size == expected
    except OSError:
        return False


def _read_sidecar(dest_dir: Path) -> Mapping[str, Any] | None:
    try:
        raw = json.loads((dest_dir / SIDECAR_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, Mapping) or raw.get("schema") != SIDECAR_SCHEMA:
        return None
    return raw


def _sidecar_matches(dest_dir: Path, data: Mapping[str, Any]) -> bool:
    """A hit only when the sidecar and the files on disk **match size for size** (no network request at all)."""
    endpoint = data.get("endpoint")
    if not isinstance(endpoint, str) or not endpoint:
        return False
    files = data.get("files")
    if not isinstance(files, Mapping) or any(name not in files for name in REQUIRED_FILES):
        return False
    for name, size in files.items():
        if not isinstance(name, str) or Path(name).name != name:
            return False
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            return False
        if not _same_size(dest_dir / name, size):
            return False
    return True


def _write_sidecar(dest_dir: Path, payload: Mapping[str, Any]) -> None:
    """Write the sidecar atomically. It is only the "avoid the network" fast path; failing to write it must not turn a successful download into a failure."""
    path = dest_dir / SIDECAR_NAME
    temporary = dest_dir / (SIDECAR_NAME + ".tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        _remove_quietly(temporary)


# ---------------------------------------------------------------------------
# Transfer
# ---------------------------------------------------------------------------


def _remove_quietly(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def _remove_dir_if_empty(path: Path) -> None:
    try:
        path.rmdir()
    except OSError:
        pass


class _ProgressSink:
    """Forward (name, done) as progress(name, done, total), keeping **done monotonically non-decreasing**.

    The whole download() call shares one sink: an endpoint fallback restarts from 0 (the .part was
    deleted), and clamping done is what keeps the progress bar from moving backwards. total is set
    only after the manifest for that attempt has been fetched.
    """

    def __init__(self, callback: Callable[[str, int, int], None] | None) -> None:
        self._callback = callback
        self._total = 0
        self._last = 0

    def set_total(self, total: int) -> None:
        self._total = total

    def __call__(self, name: str, done: int) -> None:
        if done > self._last:
            self._last = done
        if self._callback is None:
            return
        self._callback(name, self._last, self._total)


def _transfer(
    client: httpx.Client,
    url: str,
    target: Path,
    expected: int,
    *,
    name: str,
    sink: _ProgressSink,
    state: dict[str, int],
    cancel: Callable[[], bool] | None,
) -> int:
    """Stream one file down: <name>.part -> verify the byte count -> os.replace.

    Returns the number of bytes actually transferred. **Every failure/cancellation path deletes the .part** (finally).
    """
    part = target.with_name(target.name + PART_SUFFIX)
    if cancel is not None and cancel():
        raise DownloadCancelled("download of %s was cancelled" % name)
    try:
        with client.stream("GET", url) as response:
            if response.status_code not in (200, 206):
                raise DownloadError(
                    "download of %s failed: HTTP %d %s (%s)"
                    % (name, response.status_code, response.reason_phrase, url)
                )
            transferred = 0
            with open(part, "wb") as handle:
                for chunk in response.iter_bytes(CHUNK_SIZE):
                    if cancel is not None and cancel():
                        raise DownloadCancelled("download of %s was cancelled (%d bytes received)" % (name, transferred))
                    if not chunk:
                        continue
                    handle.write(chunk)
                    handle.flush()  # keep the on-disk .part in sync with done in the callback (also evidence of streaming)
                    transferred += len(chunk)
                    state["done"] += len(chunk)
                    sink(name, state["done"])
                handle.flush()
        actual = part.stat().st_size
        if actual != expected:
            raise DownloadError(
                "byte count mismatch: %s expected %d bytes, actually %d bytes (%s)" % (name, expected, actual, url)
            )
        os.replace(part, target)
        return transferred
    finally:
        _remove_quietly(part)


def _download_at(
    endpoint: str,
    model_id: str,
    repo: str,
    dest_dir: Path,
    *,
    sink: _ProgressSink,
    cancel: Callable[[], bool] | None,
) -> DownloadResult:
    """The complete download from a single endpoint. Any failure **leaves no half a model**: it removes the files this call wrote and deletes an empty directory."""
    if cancel is not None and cancel():
        raise DownloadCancelled("download of %s was cancelled (before switching endpoints)" % model_id)
    created_dir = not dest_dir.is_dir()
    created: list[Path] = []
    state = {"done": 0, "bytes": 0}
    with _client() as client:
        data = _fetch_manifest(client, endpoint, repo)
        model_plan = _plan_from_manifest(client, model_id, repo, endpoint, data)
        sink.set_total(model_plan.total_bytes)
        dest_dir.mkdir(parents=True, exist_ok=True)
        try:
            for name, expected in model_plan.files:
                if cancel is not None and cancel():
                    raise DownloadCancelled("download of %s was cancelled" % model_id)
                target = dest_dir / name
                if _same_size(target, expected):
                    state["done"] += expected
                    sink(name, state["done"])
                    continue
                state["bytes"] += _transfer(
                    client,
                    resolve_url(endpoint, repo, name),
                    target,
                    expected,
                    name=name,
                    sink=sink,
                    state=state,
                    cancel=cancel,
                )
                created.append(target)
        except BaseException:
            for path in created:
                _remove_quietly(path)
            if created_dir:
                _remove_dir_if_empty(dest_dir)
            raise

    onnx_path, vocab_path = local_paths(model_id, dest_dir.parent)
    _write_sidecar(
        dest_dir,
        {
            "schema": SIDECAR_SCHEMA,
            "model_id": model_id,
            "repo_id": repo,
            "endpoint": endpoint,
            "files": model_plan.sizes(),
        },
    )
    return DownloadResult(
        model_id=model_id,
        onnx_path=onnx_path,
        vocab_path=vocab_path,
        endpoint=endpoint,
        bytes_downloaded=state["bytes"],
        skipped_existing=not created,
    )


def download(
    model_id: str,
    dest_root: Path,
    *,
    endpoint: str | None = None,
    progress: Callable[[str, int, int], None] | None = None,
    cancel: Callable[[], bool] | None = None,
) -> DownloadResult:
    """download model_id into <dest_root>/<model_id>/ (flat layout, §4.6).

    * progress(name, done, total): done / total are in **bytes** (§4.7's phase=download),
      name is the file name, accumulated across files, **monotonically non-decreasing**.
    * cancel() -> bool: when it returns True, raise DownloadCancelled and clean up, **without
      falling back to another endpoint**.
    * When endpoint is given, use only it; otherwise fall back in the order of endpoints(), and when
      all fail the exception message **carries each endpoint's own error**.
    * Already present (and matching in size) -> return directly, skipped_existing=True.
    """
    repo = repo_id(model_id)  # UnknownModel propagates straight to the caller (§4.7 -> 400 unknown_model)
    dest_dir = (Path(dest_root) / model_id).resolve()

    cached = _read_sidecar(dest_dir)
    if cached is not None and _sidecar_matches(dest_dir, cached):
        onnx_path, vocab_path = local_paths(model_id, dest_dir.parent)
        return DownloadResult(
            model_id=model_id,
            onnx_path=onnx_path,
            vocab_path=vocab_path,
            endpoint=str(cached["endpoint"]),
            bytes_downloaded=0,
            skipped_existing=True,
        )

    if cancel is not None and cancel():
        raise DownloadCancelled("download of %s was cancelled (before starting)" % model_id)

    candidates: tuple[str, ...] = (endpoint,) if endpoint is not None else endpoints()
    failures: list[tuple[str, BaseException]] = []
    sink = _ProgressSink(progress)  # one shared for the whole call: done must not move backwards on an endpoint fallback either
    for base in candidates:
        try:
            return _download_at(base, model_id, repo, dest_dir, sink=sink, cancel=cancel)
        except DownloadCancelled:
            raise  # a user cancellation is not an endpoint failure: never retry on another endpoint
        except (DownloadError, httpx.HTTPError) as exc:
            failures.append((base, exc))
    raise _all_endpoints_failed(model_id, failures)
