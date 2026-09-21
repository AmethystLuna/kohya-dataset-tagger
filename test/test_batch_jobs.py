"""Batch caption write and cache rebuild as background jobs: real progress, and a cancel that
stops before the next image.

Both operations used to be a single POST over however many images the user selected - a thousand
image write looked exactly like a hung one, and a cache rebuild could not be stopped at all. The
job form is **additive**: the synchronous endpoints stay, because the dry-run preview is instant
and must not be hidden behind a stream, and external callers keep working.

The criteria here are deliberately **invariant-based**. A cancel races the worker, so "exactly three
images were written" would be flaky; what has to hold on every interleaving is that the terminal
counters agree with what is actually on disk, and that nothing is written without being counted.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from kohya_dataset_tagger import config
from kohya_dataset_tagger.app import create_app

COUNT = 6
#: Big enough that a cancel posted right after the run request usually lands mid-job.
CANCEL_COUNT = 60
TE_CACHE_DIRNAME = "cache_text_encoder"


def _build_dataset(root: Path, count: int) -> list[Path]:
    """One image per subdirectory: the trainer treats every subdirectory as its own subset."""
    images: list[Path] = []
    for index in range(count):
        sub = root / ("sub%02d" % index)
        sub.mkdir(parents=True, exist_ok=True)
        image = sub / "img.png"
        Image.new("RGB", (8, 8), (10, 10, 10)).save(image)
        # "a,  a" normalizes to "a", so op=normalize changes every image (and only once).
        image.with_suffix(".txt").write_text("a,  a", encoding="utf-8")
        images.append(image)
    return images


def _client_for(tmp_path: Path, root: Path) -> TestClient:
    config.load(roots=[root], thumb_cache_dir=tmp_path / "thumbs", model_search_roots=[])
    application = create_app()
    assert application.state.router_errors == [], application.state.router_errors
    return TestClient(application)


@pytest.fixture()
def job_env(tmp_path):
    root = tmp_path / "ds"
    root.mkdir()
    images = _build_dataset(root, COUNT)
    with _client_for(tmp_path, root) as client:
        yield client, images


@pytest.fixture()
def cancel_env(tmp_path):
    root = tmp_path / "ds"
    root.mkdir()
    images = _build_dataset(root, CANCEL_COUNT)
    with _client_for(tmp_path, root) as client:
        yield client, images


def _read_stream(client: TestClient, route: str, job_id: str) -> list[tuple[str, dict]]:
    """Read the SSE stream to the end (the server closes it itself once done is sent)."""
    events: list[tuple[str, dict]] = []
    name = ""
    with client.stream("GET", route, params={"job_id": job_id}) as response:
        assert response.status_code == 200, response.read()[:300]
        assert response.headers["content-type"].startswith("text/event-stream")
        for line in response.iter_lines():
            if line.startswith("event: "):
                name = line[len("event: "):].strip()
            elif line.startswith("data: "):
                events.append((name, json.loads(line[len("data: "):])))
    return events


def _caption(image: Path) -> str:
    return image.with_suffix(".txt").read_text(encoding="utf-8")


def _terminal(events: list[tuple[str, dict]]) -> dict:
    done = [data for name, data in events if name == "done"]
    assert len(done) == 1, "exactly one terminal done event, got %d" % len(done)
    return done[0]


def test_a_batch_job_streams_one_progress_per_image_then_done(job_env) -> None:
    client, images = job_env
    payload = {"images": [str(image) for image in images], "op": "normalize"}
    started = client.post("/api/captions/batch/run", json=payload)
    assert started.status_code == 202, started.text[:300]
    body = started.json()
    assert set(body) == {"job_id", "total"}, body
    assert body["total"] == len(images)

    events = _read_stream(client, "/api/captions/batch/stream", body["job_id"])
    progress = [data for name, data in events if name == "progress"]
    assert len(progress) == len(images), "one progress per image, got %d" % len(progress)
    assert [data["done"] for data in progress] == list(range(1, len(images) + 1))
    # the per-image payload is the synchronous endpoint entry, verbatim
    assert [data["entry"]["image"] for data in progress] == [str(image) for image in images]
    assert all(data["total"] == len(images) for data in progress)

    terminal = _terminal(events)
    assert terminal["finished"] is True
    assert terminal["cancelled"] is False
    assert terminal["errors"] == []
    assert terminal["summary"] == {"op": "normalize", "processed": len(images), "applied": len(images), "failed": 0}
    assert all(_caption(image) == "a" for image in images), "the job did not really write"


def test_the_job_rejects_a_dry_run_and_a_bad_op(job_env) -> None:
    client, images = job_env
    payload = {"images": [str(images[0])], "op": "normalize", "dry_run": True}
    refused = client.post("/api/captions/batch/run", json=payload)
    assert refused.status_code == 400, refused.text[:200]
    assert refused.json()["error"]["code"] == "bad_request"

    bad = client.post("/api/captions/batch/run", json={"images": [str(images[0])], "op": "nope"})
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "bad_op"

    # the synchronous preview is untouched by any of this
    preview = client.post("/api/captions/batch", json={"images": [str(images[0])], "op": "normalize", "dry_run": True})
    assert preview.status_code == 200
    assert preview.json()["dry_run"] is True
    assert _caption(images[0]) == "a,  a", "a dry run must not write"


def test_an_unknown_job_streams_a_404_not_a_hang(job_env) -> None:
    client, _images = job_env
    for route in ("/api/captions/batch/stream", "/api/cache/invalidate/stream"):
        response = client.get(route, params={"job_id": "nope"})
        assert response.status_code == 404, route
        assert response.json()["error"]["code"] == "unknown_job"


def test_a_cancelled_job_reports_how_far_it_got_and_counts_what_it_wrote(cancel_env) -> None:
    client, images = cancel_env
    payload = {"images": [str(image) for image in images], "op": "normalize"}
    started = client.post("/api/captions/batch/run", json=payload)
    assert started.status_code == 202, started.text[:300]
    job_id = started.json()["job_id"]

    asked = client.post("/api/captions/batch/cancel", json={"job_id": job_id})
    assert asked.status_code == 202
    assert isinstance(asked.json()["cancelled"], bool)

    events = _read_stream(client, "/api/captions/batch/stream", job_id)
    terminal = _terminal(events)
    processed = terminal["summary"]["processed"]
    assert processed == terminal["done"], "the terminal counters must agree with the event count"
    written = sum(1 for image in images if _caption(image) == "a")
    assert written == processed, (
        "counted %d but %d images are on disk: a cancel must never lose or invent a write"
        % (processed, written),
    )
    if processed < len(images):
        assert terminal["cancelled"] is True, (
            "images left unwritten without a cancel recorded: %r" % terminal["summary"]
        )

    # a second cancel has nothing left to cancel, and must say so rather than pretend
    again = client.post("/api/captions/batch/cancel", json={"job_id": job_id})
    assert again.status_code == 202
    assert again.json()["cancelled"] is False


def test_a_cache_job_removes_the_caches_and_reports_per_image(job_env) -> None:
    client, images = job_env
    targets = []
    for image in images:
        cache_dir = image.parent / TE_CACHE_DIRNAME
        cache_dir.mkdir(exist_ok=True)
        # the trainer's own name (library/strategy_anima.py): <stem>_anima_te.safetensors
        cache = cache_dir / (image.stem + "_anima_te.safetensors")
        cache.write_bytes(b"cache")
        targets.append(cache)

    started = client.post("/api/cache/invalidate/run", json={"images": [str(image) for image in images]})
    assert started.status_code == 202, started.text[:300]
    assert started.json()["total"] == len(images)

    events = _read_stream(client, "/api/cache/invalidate/stream", started.json()["job_id"])
    progress = [data for name, data in events if name == "progress"]
    assert len(progress) == len(images)
    terminal = _terminal(events)
    assert terminal["summary"]["processed"] == len(images)
    assert terminal["summary"]["removed"] == len(images)
    assert terminal["summary"]["failed"] == 0
    assert all(not cache.exists() for cache in targets), "the caches are still on disk"


def test_the_worker_stops_before_the_next_image_when_already_cancelled(tmp_path) -> None:
    """Deterministic counterpart of the API-level cancel test.

    The API test can only assert invariants, because a cancel races the worker. Driving the worker
    itself pins the contract exactly: a cancel that is already set means **not one write**.
    """
    from kohya_dataset_tagger.api import captions as captions_api

    root = tmp_path / "ds"
    root.mkdir()
    images = _build_dataset(root, 4)
    payload = captions_api.CaptionBatchRequest(images=[str(image) for image in images], op="normalize")
    job = captions_api.BatchJob("unit-cancel", len(images), "normalize")
    job.request_cancel()

    captions_api._run_batch_job(job, images, payload)

    events, finished = job.events_since(0)
    assert finished is True, "the worker must always end with a terminal event"
    assert job.done == 0, "a cancelled-before-start job processed %d image(s)" % job.done
    assert all(_caption(image) == "a,  a" for image in images), "a cancelled job wrote a caption"
    terminal = [event for event in events if event.name == "done"][0].data
    assert terminal["cancelled"] is True
    assert terminal["summary"]["processed"] == 0


def test_a_cancel_mid_run_finishes_the_image_in_hand_and_skips_the_rest(tmp_path, monkeypatch) -> None:
    """The honest half of "cancellable": the write in progress is never interrupted, and the
    terminal event says how many landed. Everything after that is untouched."""
    from kohya_dataset_tagger.api import captions as captions_api

    root = tmp_path / "ds"
    root.mkdir()
    images = _build_dataset(root, 4)
    payload = captions_api.CaptionBatchRequest(images=[str(image) for image in images], op="normalize")
    job = captions_api.BatchJob("unit-mid", len(images), "normalize")
    real_step = captions_api._batch_step
    seen: list[Path] = []

    def step_with_cancel(image, op, request, dry_run):
        seen.append(image)
        job.request_cancel()  # the cancel arrives while this image is being written
        return real_step(image, op, request, dry_run)

    monkeypatch.setattr(captions_api, "_batch_step", step_with_cancel)
    captions_api._run_batch_job(job, images, payload)

    assert len(seen) == 1, "the worker started %d images after the cancel" % len(seen)
    assert job.done == 1
    assert _caption(images[0]) == "a", "the image in hand must finish"
    assert all(_caption(image) == "a,  a" for image in images[1:]), "the rest must be untouched"
    terminal = [event for event in job.events_since(0)[0] if event.name == "done"][0].data
    assert terminal["cancelled"] is True
    assert terminal["summary"] == {"op": "normalize", "processed": 1, "applied": 1, "failed": 0}
