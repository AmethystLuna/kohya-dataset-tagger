"""A20: the whole automatic model download chain -- verified against a **local fake endpoint**, never really downloading 1.26 GB.

Criteria (p0-spec §5 A20, point by point)
-----------------------------------------
* manifest parsing (§4.8: take only model.onnx and selected_tags.csv + byte counts)
* streaming write with the content **byte-for-byte** correct (not read into memory at once)
* the progress callback is **monotonically non-decreasing** and the last value == total bytes
* byte count mismatch -> error, and no .part / half-product left behind
* already present with the right size -> skipped_existing=True and **no request sent**
* the first endpoint is broken -> automatically falls back to the second and succeeds
* cancel() -> abort and clean up (and do not retry the cancellation as an endpoint failure)
* all fail -> the exception message carries **every** endpoint's error

This file connects only to a fake HF endpoint (FakeHub) on 127.0.0.1. The one real network access
is the last **read-only** probe: fetch a few KB of manifest JSON to check eva02's onnx byte count,
**without downloading the model**; when the network is unreachable it skips rather than fails.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Mapping

import httpx
import pytest

from kohya_dataset_tagger.autotag import download as dl
from kohya_dataset_tagger.autotag import registry

MODEL_ID = "wd-eva02-large-tagger-v3"
REPO = "SmilingWolf/wd-eva02-large-tagger-v3"

MANIFEST_PATH = "/api/models/%s?blobs=true" % REPO


def _pattern(size: int) -> bytes:
    """Deterministic pseudo-random bytes: random enough to catch "downloaded wrong / files crossed", without depending on random's implementation."""
    block = bytes(range(256))
    return (block * (size // 256 + 1))[:size]


#: 2 whole chunks + a 4-byte tail: verifies both chunked streaming and a final chunk that is not full.
ONNX = _pattern(2 * dl.CHUNK_SIZE + 4)
CSV = b"tag_id,name,category,count\n0,general,9,3\n1,1girl,0,2\n"
TOTAL = len(ONNX) + len(CSV)
PAYLOAD: dict[str, bytes] = {"model.onnx": ONNX, "selected_tags.csv": CSV}


# ---------------------------------------------------------------------------
# Fake endpoint
# ---------------------------------------------------------------------------


class FakeHub:
    """An in-process fake HF endpoint; the URL structure is copied from §4.6's measured results.

    GET  /api/models/<org>/<name>?blobs=true    -> {"siblings":[{"rfilename","size"}...]}
    GET  /<org>/<name>/resolve/main/<file>      -> the file bytes
    HEAD /<org>/<name>/resolve/main/<file>      -> content-length only (fallback when the manifest has no size)
    """

    def __init__(
        self,
        files: Mapping[str, bytes] | None = None,
        *,
        sizes: Mapping[str, int] | None = None,
        include_sizes: bool = True,
        manifest_status: int = 200,
        file_status: int = 200,
    ) -> None:
        self.files = dict(PAYLOAD if files is None else files)
        self.sizes = (
            dict(sizes) if sizes is not None else {name: len(data) for name, data in self.files.items()}
        )
        self.include_sizes = include_sizes
        self.manifest_status = manifest_status
        self.file_status = file_status
        self.requests: list[tuple[str, str]] = []
        self._server = _Server(("127.0.0.1", 0), _Handler)
        setattr(self._server, "hub", self)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self.url = "http://127.0.0.1:%d" % self._server.server_address[1]

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    # -- Assertion helpers -----------------------------------------------

    @property
    def paths(self) -> list[str]:
        return [path for _method, path in self.requests]

    def manifest_requests(self) -> list[str]:
        return [path for path in self.paths if path.startswith("/api/models/")]

    def resolve_requests(self) -> list[str]:
        """All resolve requests (including HEAD probes)."""
        return [path for path in self.paths if "/resolve/main/" in path]

    def file_gets(self) -> list[str]:
        """The requests that **really download the file body** (GET resolve); HEAD probes do not count."""
        return [path for method, path in self.requests if method == "GET" and "/resolve/main/" in path]


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request: Any, client_address: Any) -> None:  # noqa: ANN401
        """The cancellation test cuts the connection on purpose; BrokenPipe is expected noise, do not flood the log."""
        import sys

        exc = sys.exc_info()[1]
        if isinstance(exc, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
            return
        super().handle_error(request, client_address)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "FakeHF/1.0"

    def log_message(self, *args: Any) -> None:  # noqa: ANN401 - silence
        pass

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's naming
        hub: FakeHub = self.server.hub  # type: ignore[attr-defined]
        path, _, query = self.path.partition("?")
        hub.requests.append(("GET", self.path))
        if path.startswith("/api/models/"):
            self._manifest(hub, path[len("/api/models/") :], query)
        elif "/resolve/main/" in path:
            self._file(hub, path)
        else:
            self._reply(404, b"not found", "text/plain")

    def do_HEAD(self) -> None:  # noqa: N802
        hub: FakeHub = self.server.hub  # type: ignore[attr-defined]
        path = self.path.partition("?")[0]
        hub.requests.append(("HEAD", self.path))
        data = hub.files.get(path.partition("/resolve/main/")[2]) if "/resolve/main/" in path else None
        if data is None:
            self._reply(404, b"", "text/plain", send_body=False)
        else:
            self._reply(200, b"", "application/octet-stream", declared=len(data), send_body=False)

    # -- Internals -------------------------------------------------------

    def _manifest(self, hub: FakeHub, repo: str, query: str) -> None:
        if hub.manifest_status != 200:
            self._reply(hub.manifest_status, b"boom", "text/plain")
            return
        siblings: list[dict[str, Any]] = []
        for name in sorted(hub.files):
            entry: dict[str, Any] = {"rfilename": name}
            size = hub.sizes.get(name)
            if hub.include_sizes and "blobs=true" in query and size is not None:
                entry["size"] = size
            siblings.append(entry)
        body = json.dumps({"id": repo, "siblings": siblings}).encode("utf-8")
        self._reply(200, body, "application/json")

    def _file(self, hub: FakeHub, path: str) -> None:
        if hub.file_status != 200:
            self._reply(hub.file_status, b"boom", "text/plain")
            return
        data = hub.files.get(path.partition("/resolve/main/")[2])
        if data is None:
            self._reply(404, b"missing", "text/plain")
            return
        self._reply(200, data, "application/octet-stream")

    def _reply(
        self,
        status: int,
        body: bytes,
        content_type: str,
        *,
        declared: int | None = None,
        send_body: bool = True,
    ) -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body) if declared is None else declared))
            self.end_headers()
            if send_body and body:
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass


@pytest.fixture
def hubs():
    """FakeHub factory: closes them all when the test ends, leaving no background thread."""
    started: list[FakeHub] = []

    def make(files: Mapping[str, bytes] | None = None, **kwargs: Any) -> FakeHub:
        hub = FakeHub(files, **kwargs)
        started.append(hub)
        return hub

    yield make
    for hub in started:
        hub.close()


@pytest.fixture
def fake_endpoints(monkeypatch: pytest.MonkeyPatch) -> Callable[..., None]:
    """Point download's endpoints entirely at the local fake service (and clear any environment-variable override that might exist).

    monkeypatch DEFAULT_ENDPOINTS rather than setting KOHYA_TAGGER_HF_ENDPOINT: the environment
    variable only prepends **one** endpoint, and the rest still falls back to the real
    huggingface.co -- tests must never touch the real network.
    """

    def apply(*hubs: FakeHub) -> None:
        monkeypatch.delenv(dl.HF_ENDPOINT_ENV, raising=False)
        monkeypatch.setattr(dl, "DEFAULT_ENDPOINTS", tuple(hub.url for hub in hubs), raising=True)

    return apply


def _leftovers(root: Path) -> list[str]:
    """Anything still left under root (relative paths) -- the failure paths must leave nothing."""
    if not root.is_dir():
        return []
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))


# ---------------------------------------------------------------------------
# Endpoint order (§4.6 / §4.8)
# ---------------------------------------------------------------------------


def test_default_endpoints_are_the_frozen_pair() -> None:
    assert dl.DEFAULT_ENDPOINTS == ("https://huggingface.co", "https://hf-mirror.com")
    assert dl.HF_ENDPOINT_ENV == "KOHYA_TAGGER_HF_ENDPOINT"


def test_endpoints_without_override_is_the_frozen_order(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(dl.HF_ENDPOINT_ENV, raising=False)
    assert dl.endpoints() == dl.DEFAULT_ENDPOINTS


def test_endpoints_puts_the_override_first(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(dl.HF_ENDPOINT_ENV, "  https://example.invalid/hf/  ")
    assert dl.endpoints() == ("https://example.invalid/hf",) + dl.DEFAULT_ENDPOINTS


def test_endpoints_does_not_duplicate_a_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """The override is already one of the defaults (differing only by a slash) -> dedupe, but it **still goes first** (highest priority)."""
    monkeypatch.setenv(dl.HF_ENDPOINT_ENV, "https://hf-mirror.com/")
    assert dl.endpoints() == ("https://hf-mirror.com", "https://huggingface.co")


def test_endpoints_ignores_a_blank_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(dl.HF_ENDPOINT_ENV, "   ")
    assert dl.endpoints() == dl.DEFAULT_ENDPOINTS


def test_url_shapes_match_the_measured_endpoints() -> None:
    """§4.6 measured: the main site and the mirror share **one URL structure**."""
    assert dl.manifest_url("https://huggingface.co/", REPO) == "https://huggingface.co" + MANIFEST_PATH
    assert (
        dl.resolve_url("https://hf-mirror.com", REPO, "model.onnx")
        == "https://hf-mirror.com/%s/resolve/main/model.onnx" % REPO
    )


def test_every_builtin_model_maps_to_a_repo() -> None:
    for model_id in registry.BUILTIN:
        assert dl.is_downloadable(model_id), model_id
        assert dl.repo_id(model_id).count("/") == 1, dl.repo_id(model_id)
    assert dl.repo_id(MODEL_ID) == REPO
    # The ComfyUI flat copy has no HF cache glob, so it goes through the explicit mapping
    assert dl.repo_id("wd-vit-tagger-v3-comfy") == "SmilingWolf/wd-vit-tagger-v3"


def test_unknown_model_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(registry.UnknownModel):
        dl.plan("no-such-model")
    with pytest.raises(registry.UnknownModel):
        dl.download("no-such-model", tmp_path)
    assert dl.is_downloadable("no-such-model") is False
    assert _leftovers(tmp_path) == []


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


def test_plan_parses_the_manifest(hubs: Callable[..., FakeHub], fake_endpoints: Callable[..., None]) -> None:
    hub = hubs()
    fake_endpoints(hub)

    model_plan = dl.plan(MODEL_ID, endpoint=hub.url)

    assert (model_plan.model_id, model_plan.repo_id) == (MODEL_ID, REPO)
    assert model_plan.files == (("model.onnx", len(ONNX)), ("selected_tags.csv", len(CSV)))
    assert model_plan.names == ("model.onnx", "selected_tags.csv")
    assert model_plan.total_bytes == TOTAL
    assert hub.paths == [MANIFEST_PATH]


def test_plan_falls_back_to_the_next_endpoint(
    hubs: Callable[..., FakeHub], fake_endpoints: Callable[..., None]
) -> None:
    broken = hubs(manifest_status=500)
    good = hubs()
    fake_endpoints(broken, good)

    assert dl.plan(MODEL_ID).total_bytes == TOTAL

    assert broken.manifest_requests() == [MANIFEST_PATH], "the first must be tried first"
    assert good.manifest_requests() == [MANIFEST_PATH], "once the first is broken it should switch to the second"


def test_plan_uses_a_head_probe_when_the_manifest_has_no_sizes(
    hubs: Callable[..., FakeHub], fake_endpoints: Callable[..., None]
) -> None:
    """When ?blobs=true is ignored (the manifest has only file names), it degrades to one HEAD for content-length -- fetching headers only."""
    hub = hubs(include_sizes=False)
    fake_endpoints(hub)

    model_plan = dl.plan(MODEL_ID, endpoint=hub.url)

    assert dict(model_plan.files) == {"model.onnx": len(ONNX), "selected_tags.csv": len(CSV)}
    assert [method for method, _path in hub.requests] == ["GET", "HEAD", "HEAD"]
    assert hub.file_gets() == [], "a probe must not GET the file body"


def test_plan_rejects_a_manifest_without_the_onnx(
    hubs: Callable[..., FakeHub], fake_endpoints: Callable[..., None]
) -> None:
    hub = hubs(files={"README.md": b"hello"})
    fake_endpoints(hub)

    with pytest.raises(dl.DownloadError) as info:
        dl.plan(MODEL_ID, endpoint=hub.url)

    assert "model.onnx" in str(info.value)
    assert "README.md" in str(info.value)


def test_plan_reports_every_endpoint_when_all_fail(
    hubs: Callable[..., FakeHub], fake_endpoints: Callable[..., None]
) -> None:
    first = hubs(manifest_status=500)
    second = hubs(manifest_status=404)
    fake_endpoints(first, second)

    with pytest.raises(dl.DownloadError) as info:
        dl.plan(MODEL_ID)

    message = str(info.value)
    assert first.url in message and second.url in message
    assert "500" in message and "404" in message


# ---------------------------------------------------------------------------
# Download: content / streaming / progress
# ---------------------------------------------------------------------------


def test_download_writes_the_exact_bytes_from_the_endpoint(
    hubs: Callable[..., FakeHub], fake_endpoints: Callable[..., None], tmp_path: Path
) -> None:
    hub = hubs()
    fake_endpoints(hub)
    dest = tmp_path / "models"

    result = dl.download(MODEL_ID, dest)

    assert result.model_id == MODEL_ID
    assert result.endpoint == hub.url
    assert result.skipped_existing is False
    assert result.bytes_downloaded == TOTAL
    assert result.onnx_path == (dest / MODEL_ID / "model.onnx").resolve()
    assert result.vocab_path == (dest / MODEL_ID / "selected_tags.csv").resolve()
    assert result.onnx_path.read_bytes() == ONNX
    assert result.vocab_path.read_bytes() == CSV
    assert hub.resolve_requests() == [
        "/%s/resolve/main/model.onnx" % REPO,
        "/%s/resolve/main/selected_tags.csv" % REPO,
    ]
    # Only three files remain: the two products + the local manifest (sidecar), and no .part at all
    assert sorted(path.name for path in result.onnx_path.parent.iterdir()) == [
        dl.SIDECAR_NAME,
        "model.onnx",
        "selected_tags.csv",
    ]


def test_download_accepts_a_string_dest_root(
    hubs: Callable[..., FakeHub], fake_endpoints: Callable[..., None], tmp_path: Path
) -> None:
    fake_endpoints(hubs())
    result = dl.download(MODEL_ID, str(tmp_path / "models"))
    assert result.onnx_path.is_file()


def test_progress_is_monotonic_and_ends_at_the_total(
    hubs: Callable[..., FakeHub], fake_endpoints: Callable[..., None], tmp_path: Path
) -> None:
    fake_endpoints(hubs())
    calls: list[tuple[str, int, int]] = []

    dl.download(
        MODEL_ID,
        tmp_path / "models",
        progress=lambda name, done, total: calls.append((name, done, total)),
    )

    assert calls, "the progress callback was never called once"
    assert calls[0][0] == "model.onnx", "the ONNX comes first (manifest order)"
    assert {name for name, _done, _total in calls} == {"model.onnx", "selected_tags.csv"}
    assert {total for _name, _done, total in calls} == {TOTAL}, "total is always the whole manifest's byte count"
    done_values = [done for _name, done, _total in calls]
    assert done_values == sorted(done_values), "progress must be monotonically non-decreasing: %s" % done_values
    assert done_values[-1] == TOTAL, "the last value must equal the total bytes"
    assert len([1 for name, _done, _total in calls if name == "model.onnx"]) >= 3


def test_download_streams_chunk_by_chunk_instead_of_buffering(
    hubs: Callable[..., FakeHub], fake_endpoints: Callable[..., None], tmp_path: Path
) -> None:
    """When the callback fires, the .part **is already on disk** and its length equals done -- that is direct evidence of streaming to disk.

    If the implementation read the whole file into memory and wrote it in one go, the .part would
    either not exist or have length 0 at the first callback.
    """
    fake_endpoints(hubs())
    dest = tmp_path / "models"
    seen: list[tuple[str, int, int]] = []

    def progress(name: str, done: int, total: int) -> None:
        part = dest / MODEL_ID / (name + dl.PART_SUFFIX)
        seen.append((name, done, part.stat().st_size if part.is_file() else -1))

    dl.download(MODEL_ID, dest, progress=progress)

    onnx_seen = [item for item in seen if item[0] == "model.onnx"]
    assert len(onnx_seen) >= 3, onnx_seen
    assert all(done == size for _name, done, size in onnx_seen), "done must equal the .part's current length"
    assert onnx_seen[0][1] == dl.CHUNK_SIZE, "the first chunk should be CHUNK_SIZE"
    assert onnx_seen[-1][1] == len(ONNX), "a final partial chunk must be reported truthfully too"
    assert dl.CHUNK_SIZE <= 4 * 1024 * 1024, "the chunk must not be so big that a 1.26 GB model becomes a memory problem again"


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_second_download_sends_no_request_at_all(
    hubs: Callable[..., FakeHub], fake_endpoints: Callable[..., None], tmp_path: Path
) -> None:
    hub = hubs()
    fake_endpoints(hub)
    dest = tmp_path / "models"
    first = dl.download(MODEL_ID, dest)
    requests_after_first = list(hub.requests)

    second = dl.download(MODEL_ID, dest)

    assert second.skipped_existing is True
    assert second.bytes_downloaded == 0
    assert second.endpoint == first.endpoint == hub.url
    assert second.onnx_path == first.onnx_path
    assert second.vocab_path == first.vocab_path
    assert hub.requests == requests_after_first, "an idempotent skip should send no request at all (not even the manifest)"


def test_hand_placed_files_skip_the_download_but_verify_against_the_manifest(
    hubs: Callable[..., FakeHub], fake_endpoints: Callable[..., None], tmp_path: Path
) -> None:
    """The user followed §4.6 and put the files into <root>/<model_id>/ themselves (no sidecar): one manifest request only."""
    hub = hubs()
    fake_endpoints(hub)
    dest = tmp_path / "models"
    model_dir = dest / MODEL_ID
    model_dir.mkdir(parents=True)
    (model_dir / "model.onnx").write_bytes(ONNX)
    (model_dir / "selected_tags.csv").write_bytes(CSV)

    result = dl.download(MODEL_ID, dest)

    assert result.skipped_existing is True
    assert result.bytes_downloaded == 0
    assert result.endpoint == hub.url
    assert hub.file_gets() == [], "the size is already right, not a single byte should be downloaded again"
    assert hub.manifest_requests() == [MANIFEST_PATH]


def test_a_wrong_sized_local_file_is_re_downloaded(
    hubs: Callable[..., FakeHub], fake_endpoints: Callable[..., None], tmp_path: Path
) -> None:
    """The local file size is wrong (a failed download / truncated) -> re-download rather than treating it as installed."""
    hub = hubs()
    fake_endpoints(hub)
    dest = tmp_path / "models"
    model_dir = dest / MODEL_ID
    model_dir.mkdir(parents=True)
    (model_dir / "model.onnx").write_bytes(b"truncated")
    (model_dir / "selected_tags.csv").write_bytes(CSV)

    result = dl.download(MODEL_ID, dest)

    assert result.skipped_existing is False
    assert result.onnx_path.read_bytes() == ONNX
    assert hub.file_gets() == ["/%s/resolve/main/model.onnx" % REPO], "re-download only the missing one"


def test_a_stale_sidecar_is_not_trusted(
    hubs: Callable[..., FakeHub], fake_endpoints: Callable[..., None], tmp_path: Path
) -> None:
    """The sidecar's recorded byte counts do not match the disk (the file was swapped) -> go back to manifest verification, do not trust blindly."""
    hub = hubs()
    fake_endpoints(hub)
    dest = tmp_path / "models"
    result = dl.download(MODEL_ID, dest)
    (dest / MODEL_ID / "model.onnx").write_bytes(b"tampered")

    again = dl.download(MODEL_ID, dest)

    assert again.skipped_existing is False
    assert again.onnx_path.read_bytes() == ONNX
    assert hub.resolve_requests().count("/%s/resolve/main/model.onnx" % REPO) == 2
    assert result.onnx_path == again.onnx_path


# ---------------------------------------------------------------------------
# Failure paths: byte verification / cleanup
# ---------------------------------------------------------------------------


def test_size_mismatch_on_the_first_file_leaves_nothing(
    hubs: Callable[..., FakeHub], fake_endpoints: Callable[..., None], tmp_path: Path
) -> None:
    hub = hubs(
        files={"model.onnx": b"truncated", "selected_tags.csv": CSV},
        sizes={"model.onnx": 4096, "selected_tags.csv": len(CSV)},
    )
    fake_endpoints(hub)
    dest = tmp_path / "models"

    with pytest.raises(dl.DownloadError) as info:
        dl.download(MODEL_ID, dest)

    message = str(info.value)
    assert "expected 4096 bytes" in message and "actually 9 bytes" in message
    assert _leftovers(dest) == [], "must not leave .part / half-products / empty directories"
    assert dl.installed_size(MODEL_ID, dest) == 0


def test_size_mismatch_on_the_second_file_rolls_back_the_first(
    hubs: Callable[..., FakeHub], fake_endpoints: Callable[..., None], tmp_path: Path
) -> None:
    """Never leave half a model: the ONNX downloaded successfully but the CSV failed verification -> the ONNX already on disk must be removed too."""
    hub = hubs(
        files={"model.onnx": ONNX, "selected_tags.csv": b"short"},
        sizes={"model.onnx": len(ONNX), "selected_tags.csv": 999999},
    )
    fake_endpoints(hub)
    dest = tmp_path / "models"

    with pytest.raises(dl.DownloadError) as info:
        dl.download(MODEL_ID, dest)

    assert "selected_tags.csv" in str(info.value)
    assert _leftovers(dest) == [], "half a model must be cleaned up too: %s" % _leftovers(dest)


def test_download_reports_every_endpoint_when_all_fail(
    hubs: Callable[..., FakeHub], fake_endpoints: Callable[..., None], tmp_path: Path
) -> None:
    first = hubs(manifest_status=500)
    second = hubs(manifest_status=404)
    fake_endpoints(first, second)
    dest = tmp_path / "models"

    with pytest.raises(dl.DownloadError) as info:
        dl.download(MODEL_ID, dest)

    message = str(info.value)
    assert MODEL_ID in message, "the exception message must carry the model id"
    assert first.url in message and second.url in message, "every endpoint's error must be included"
    assert "500" in message and "404" in message
    assert _leftovers(dest) == []


def test_an_explicit_endpoint_disables_the_fallback(
    hubs: Callable[..., FakeHub], fake_endpoints: Callable[..., None], tmp_path: Path
) -> None:
    broken = hubs(manifest_status=500)
    good = hubs()
    fake_endpoints(broken, good)

    with pytest.raises(dl.DownloadError):
        dl.download(MODEL_ID, tmp_path / "models", endpoint=broken.url)

    assert good.requests == [], "with an explicit endpoint it must not secretly switch"


def test_http_error_on_the_file_transfer_falls_back_to_the_next_endpoint(
    hubs: Callable[..., FakeHub], fake_endpoints: Callable[..., None], tmp_path: Path
) -> None:
    """The manifest works but the file returns 500 -- this also counts as the endpoint being broken; switch to the next."""
    bad = hubs(file_status=500)
    good = hubs()
    fake_endpoints(bad, good)

    result = dl.download(MODEL_ID, tmp_path / "models")

    assert result.endpoint == good.url
    assert result.onnx_path.read_bytes() == ONNX


def test_progress_stays_monotonic_across_an_endpoint_fallback(
    hubs: Callable[..., FakeHub], fake_endpoints: Callable[..., None], tmp_path: Path
) -> None:
    """The first endpoint fails halfway (byte count mismatch) and then we switch: progress **must not go backwards**.

    Endpoint 1 transfers 1.5 chunks before failing; endpoint 2 re-downloads from 0, and its first
    1 MiB chunk is clamped to 1.5 chunks, so the value 1.5 chunks appears twice -- that is exactly
    the evidence of clamping.
    """
    truncated = 3 * dl.CHUNK_SIZE // 2
    bad = hubs(
        files={"model.onnx": ONNX[:truncated], "selected_tags.csv": CSV},
        sizes={"model.onnx": len(ONNX), "selected_tags.csv": len(CSV)},
    )
    good = hubs()
    fake_endpoints(bad, good)
    calls: list[tuple[str, int, int]] = []

    result = dl.download(
        MODEL_ID,
        tmp_path / "models",
        progress=lambda name, done, total: calls.append((name, done, total)),
    )

    assert result.endpoint == good.url
    assert result.onnx_path.read_bytes() == ONNX
    done_values = [done for _name, done, _total in calls]
    assert done_values == sorted(done_values), "progress went backwards: %s" % done_values
    assert done_values[-1] == TOTAL
    assert done_values.count(truncated) >= 2, "endpoint 2's first chunk should be clamped to the position endpoint 1 already reported"


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------


def test_cancel_aborts_cleans_up_and_does_not_fall_back(
    hubs: Callable[..., FakeHub], fake_endpoints: Callable[..., None], tmp_path: Path
) -> None:
    hub = hubs()
    other = hubs()
    fake_endpoints(hub, other)
    dest = tmp_path / "models"
    state = {"cancelled": False}

    def progress(name: str, done: int, total: int) -> None:
        if done >= dl.CHUNK_SIZE:
            state["cancelled"] = True

    with pytest.raises(dl.DownloadCancelled) as info:
        dl.download(MODEL_ID, dest, progress=progress, cancel=lambda: state["cancelled"])

    assert "cancelled" in str(info.value)
    assert issubclass(dl.DownloadCancelled, dl.DownloadError)
    assert _leftovers(dest) == [], "cancellation must delete the .part too: %s" % _leftovers(dest)
    assert other.requests == [], "a user cancellation is not an endpoint failure; never retry on another endpoint"
    assert hub.resolve_requests(), "the cancellation happened mid-transfer (the first endpoint really did start)"


def test_cancel_before_starting_makes_no_request_at_all(
    hubs: Callable[..., FakeHub], fake_endpoints: Callable[..., None], tmp_path: Path
) -> None:
    hub = hubs()
    fake_endpoints(hub)
    dest = tmp_path / "models"

    with pytest.raises(dl.DownloadCancelled):
        dl.download(MODEL_ID, dest, cancel=lambda: True)

    assert hub.requests == []
    assert _leftovers(dest) == []


# ---------------------------------------------------------------------------
# The helpers §4.7 needs: installed / size_bytes / downloadable
# ---------------------------------------------------------------------------


def test_local_helpers_feed_the_models_listing(
    hubs: Callable[..., FakeHub], fake_endpoints: Callable[..., None], tmp_path: Path
) -> None:
    dest = tmp_path / "models"
    assert dl.installed_size(MODEL_ID, dest) == 0
    assert dl.local_paths(MODEL_ID, dest) == (
        dest / MODEL_ID / "model.onnx",
        dest / MODEL_ID / "selected_tags.csv",
    )

    fake_endpoints(hubs())
    dl.download(MODEL_ID, dest)

    assert dl.installed_size(MODEL_ID, dest) == len(ONNX)
    assert dl.is_downloadable(MODEL_ID) is True


# ---------------------------------------------------------------------------
# A21's closing loop: the downloaded flat layout must be discoverable by the registry
# ---------------------------------------------------------------------------


def test_downloaded_model_is_discovered_by_the_registry(
    hubs: Callable[..., FakeHub], fake_endpoints: Callable[..., None], tmp_path: Path
) -> None:
    """The download lands in the <root>/<model_id>/ flat layout (without faking an HF cache
    snapshot sha), and the registry must recognize it -- otherwise it stays "unavailable" after
    the download."""
    fake_endpoints(hubs())
    dest = tmp_path / "models"

    result = dl.download(MODEL_ID, dest)

    assert registry.discover([dest]) == [MODEL_ID]
    onnx_path, vocab_path = registry.resolve(MODEL_ID, [dest])
    assert onnx_path == result.onnx_path.resolve()
    assert vocab_path == result.vocab_path.resolve()
    assert "snapshots" not in str(onnx_path)
    assert onnx_path.parent.name == MODEL_ID


def test_registry_still_discovers_the_hf_cache_layout(tmp_path: Path) -> None:
    """The other layout is still recognized: models--<org>--<name>/snapshots/*/model.onnx (the shape hf_hub_download produces).

    The A21 cases in test_wd14_integration.py are in a file that is skipped entirely when
    onnxruntime is missing, so both layouts are pinned again here (where ORT is not needed).
    """
    snapshot = tmp_path / ("models--SmilingWolf--" + MODEL_ID) / "snapshots" / ("b" * 40)
    snapshot.mkdir(parents=True)
    (snapshot / "model.onnx").write_bytes(ONNX)
    (snapshot / "selected_tags.csv").write_bytes(CSV)

    onnx_path, vocab_path = registry.resolve(MODEL_ID, [tmp_path])

    assert onnx_path == (snapshot / "model.onnx").resolve()
    assert vocab_path == (snapshot / "selected_tags.csv").resolve()
    assert onnx_path.parent.name == "b" * 40


def test_every_builtin_model_has_a_flat_glob(tmp_path: Path) -> None:
    """A21: every BUILTIN model must recognize <root>/<model_id>/model.onnx, otherwise that fallback is a lie for it."""
    for spec in registry.BUILTIN.values():
        assert spec.onnx_globs == (spec.onnx_glob, "%s/model.onnx" % spec.id)
        assert spec.vocab_globs == (spec.vocab_glob, "%s/*.csv" % spec.id)
        directory = tmp_path / spec.id
        directory.mkdir(parents=True)
        (directory / "model.onnx").write_bytes(ONNX)
        (directory / "selected_tags.csv").write_bytes(CSV)

    assert registry.discover([tmp_path]) == sorted(registry.BUILTIN)


def test_registry_pairs_the_vocab_from_the_onnx_directory(tmp_path: Path) -> None:
    """The vocabulary must be in the **same directory** as the onnx (§4.6): a csv from another layout under the same root must not be paired in."""
    flat = tmp_path / MODEL_ID
    flat.mkdir(parents=True)
    (flat / "model.onnx").write_bytes(ONNX)
    (flat / "selected_tags.csv").write_bytes(b"flat-vocab")
    other = tmp_path / ("models--SmilingWolf--" + MODEL_ID) / "snapshots" / ("c" * 40)
    other.mkdir(parents=True)
    (other / "selected_tags.csv").write_bytes(b"cache-vocab")

    _onnx_path, vocab_path = registry.resolve(MODEL_ID, [tmp_path])

    assert vocab_path == (flat / "selected_tags.csv").resolve()
    assert vocab_path.read_bytes() == b"flat-vocab"


def test_registry_flat_layout_wins_over_nothing_else_in_the_same_root(tmp_path: Path) -> None:
    """Both layouts under one root -> the specialised (HF cache) glob wins, flat as fallback (the order is pinned to prevent drift)."""
    flat = tmp_path / MODEL_ID
    flat.mkdir(parents=True)
    (flat / "model.onnx").write_bytes(ONNX)
    (flat / "selected_tags.csv").write_bytes(CSV)
    snapshot = tmp_path / ("models--SmilingWolf--" + MODEL_ID) / "snapshots" / ("a" * 40)
    snapshot.mkdir(parents=True)
    hub_onnx = snapshot / "model.onnx"
    hub_onnx.write_bytes(b"hf-cache-copy")
    (snapshot / "selected_tags.csv").write_bytes(CSV)

    onnx_path, _vocab_path = registry.resolve(MODEL_ID, [tmp_path])

    assert onnx_path == hub_onnx.resolve()


# ---------------------------------------------------------------------------
# Real endpoints: read-only probe (no download)
# ---------------------------------------------------------------------------


@pytest.mark.network
def test_real_endpoints_list_the_same_eva02_onnx() -> None:
    """An on-site recheck of §4.6: both endpoints' /api/models/<repo> list model.onnx, with matching byte counts.

    **Fetches only the manifest JSON (a few KB), does not download the model.** When the network is
    unreachable it skips, not fails.
    """
    plans: dict[str, dl.DownloadPlan] = {}
    for endpoint in dl.DEFAULT_ENDPOINTS:
        try:
            plans[endpoint] = dl.plan("wd-eva02-large-tagger-v3", endpoint=endpoint)
        except httpx.TransportError as exc:
            pytest.skip("network unreachable (%s): %s: %s" % (endpoint, type(exc).__name__, exc))

    sizes: dict[str, int] = {}
    for endpoint, model_plan in plans.items():
        assert model_plan.names == ("model.onnx", "selected_tags.csv"), (endpoint, model_plan.names)
        sizes[endpoint] = dict(model_plan.files)["model.onnx"]
        assert model_plan.total_bytes > sizes[endpoint]

    assert len(set(sizes.values())) == 1, "both endpoints' model.onnx byte counts must match: %s" % sizes
    size = next(iter(sizes.values()))
    assert size > 100 * 1024 * 1024, "this is not a 1.26 GB-class model file (an LFS pointer?): %d bytes" % size
    print(
        "\n[eva02] both endpoint manifests agree: model.onnx = %d bytes (%.2f GB), selected_tags.csv = %d bytes"
        % (size, size / 1024 ** 3, dict(plans[dl.DEFAULT_ENDPOINTS[0]].files)["selected_tags.csv"])
    )
