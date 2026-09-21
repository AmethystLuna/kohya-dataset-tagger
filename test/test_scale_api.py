"""HTTP contract tests for api/scale.py (§4.14): background task + SSE + target directory validation.

Criterion highlights:
1. actually scale by area normalization and **attach the caption automatically** (byte-for-byte identical);
2. no_upscale is on by default: an image with area below the target keeps its original size; turning it off enlarges;
3. same format and unchanged size -> **copy the bytes directly** (no re-encode);
4. the target directory must fall inside the allowlist; it **must not be inside the source directory** (otherwise it pollutes the dataset);
5. SSE ends with done{finished:true}; cancelling an unknown job is always 202;
6. the target directory keeps no .tmp-scale-* temp files, and the source files are not touched by a single byte.
"""
from __future__ import annotations

import json
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from kohya_dataset_tagger import config

TARGET = (1536, 1536)


def _png(path: Path, size=(100, 100), color=(10, 20, 30), mode="RGB") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new(mode, size, color).save(path, "PNG")
    return path


def _jpeg(path: Path, size=(200, 100), color=(200, 30, 30)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path, "JPEG", quality=90)
    return path


@pytest.fixture(autouse=True)
def _neutral_state():
    yield
    config.load(env={}, roots=[])


@pytest.fixture
def env(tmp_path: Path):
    """A miniature dataset: one large image (will be shrunk) + one small image (area below the target) + captions."""
    dataset = tmp_path / "dataset"
    (dataset / "1_post").mkdir(parents=True)
    _png(dataset / "1_post" / "1.png", size=(3000, 2000))
    (dataset / "1_post" / "1.txt").write_bytes("1girl, solo\n".encode("utf-8"))
    _jpeg(dataset / "1_post" / "2.jpeg", size=(400, 300))
    (dataset / "1_post" / "2.txt").write_text("2girls", encoding="utf-8")
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()

    config.load(
        env={},
        roots=[dataset, sandbox],
        thumb_cache_dir=tmp_path / "thumbs",
        model_search_roots=[],
    )
    from kohya_dataset_tagger import app as app_module

    app_obj = app_module.create_app()
    with TestClient(app_obj) as client:
        yield types.SimpleNamespace(
            client=client, dataset=dataset, sandbox=sandbox, tmp=tmp_path
        )


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for block in text.split("\n\n"):
        name = None
        data = None
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line[len("event: "):]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: "):])
        if name is not None:
            events.append((name, data))
    return events


def _run(client: TestClient, payload: dict) -> tuple[object, list[tuple[str, dict]]]:
    resp = client.post("/api/scale/run", json=payload)
    body = resp.json()
    job_id = body.get("job_id")
    assert job_id, "POST /api/scale/run did not return a job_id: %s" % body
    with client.stream("GET", "/api/scale/stream", params={"job_id": job_id}) as stream:
        assert stream.status_code == 200, stream.read()
        text = "".join(stream.iter_text())
    return resp, _parse_sse(text)


def _summary(events: list[tuple[str, dict]]) -> dict:
    assert events and events[-1][0] == "done", "SSE did not end with done: %s" % [e[0] for e in events]
    done = events[-1][1]
    assert done.get("finished") is True, "the done event has no finished:true"
    return done["summary"]


def _images(env) -> list[str]:
    """The two images of the miniature dataset, as the scope strip would hand them over."""
    return [
        str(env.dataset / "1_post" / "1.png"),
        str(env.dataset / "1_post" / "2.jpeg"),
    ]


def _listed_images(env, directory: Path, *, recursive: bool = True) -> list[str]:
    """Build a list the way the frontend does: ask /api/fs/list and take images[].path.

    The panel's scope strip reads exactly this endpoint (app.js scopeProviders.loadDirRecursive),
    so a criterion that exports *this* list is the real chain rather than a hand-written one - and
    the cache / hidden directory pruning is exercised on the way.
    """
    resp = env.client.get("/api/fs/list", params={"path": str(directory), "recursive": recursive})
    assert resp.status_code == 200, resp.text
    return [entry["path"] for entry in resp.json()["images"]]


def _payload(env, images=None, **overrides) -> dict:
    payload = {
        # The list is the export's whole extent (2026-09-20). Every test here states it explicitly.
        "images": _images(env) if images is None else images,
        "root": str(env.dataset),
        "target_dir": str(env.sandbox / "out"),
        "target_width": TARGET[0],
        "target_height": TARGET[1],
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# 1. Normal path
# ---------------------------------------------------------------------------


def test_run_resizes_and_attaches_captions(env) -> None:
    resp, events = _run(env.client, _payload(env))
    assert resp.status_code == 202, resp.text
    assert resp.json()["total"] == 2

    out = env.sandbox / "out"
    big = out / "1_post" / "1.png"
    small = out / "1_post" / "2.png"  # source is jpeg, output png -> format change, re-encode
    with Image.open(big) as opened:
        assert opened.size == (1881, 1254)
    with Image.open(small) as opened:
        assert opened.size == (400, 300)  # area below target + no_upscale on by default -> keep original size
    assert (out / "1_post" / "1.txt").read_bytes() == (env.dataset / "1_post" / "1.txt").read_bytes()
    assert (out / "1_post" / "2.txt").read_text(encoding="utf-8") == "2girls"

    summary = _summary(events)
    assert summary["processed"] == 2
    assert summary["scaled"] == 1
    assert summary["kept"] == 1
    assert summary["captions"] == 2
    assert summary["failed"] == 0
    assert summary["target_dir"] == str(out)


def test_progress_events_carry_per_image_dims(env) -> None:
    _, events = _run(env.client, _payload(env))
    progress = [data for name, data in events if name == "progress"]
    assert len(progress) == 2
    first = progress[0]["image"]
    assert first["orig_width"] == 3000 and first["orig_height"] == 2000
    assert first["new_width"] == 1881 and first["new_height"] == 1254
    assert progress[0]["done"] == 1 and progress[0]["total"] == 2
    assert progress[0]["phase"] == "scale"


def test_no_upscale_off_enlarges_the_small_image(env) -> None:
    _, events = _run(env.client, _payload(env, no_upscale=False, output_format="png"))
    with Image.open(env.sandbox / "out" / "1_post" / "2.png") as opened:
        assert opened.size == (1774, 1330)
    summary = _summary(events)
    assert summary["kept"] == 0
    assert summary["scaled"] == 2


def test_unchanged_same_format_is_a_byte_copy(env) -> None:
    source = env.dataset / "1_post" / "2.jpeg"
    before = source.read_bytes()
    _, events = _run(env.client, _payload(env, output_format="jpeg"))
    dest = env.sandbox / "out" / "1_post" / "2.jpg"
    assert dest.read_bytes() == before
    assert _summary(events)["captions"] == 2


def test_write_dataset_toml_option(env) -> None:
    _, events = _run(env.client, _payload(env, write_dataset_toml=True))
    summary = _summary(events)
    toml_path = env.sandbox / "out" / "dataset.toml"
    assert summary["dataset_toml"] == str(toml_path)
    text = toml_path.read_text(encoding="utf-8")
    assert "1_post" in text
    assert "1536" in text
    # caption_extension must match the one attached during export, otherwise the trainer cannot read the sidecar
    assert ".txt" in text


def test_dataset_toml_is_not_written_by_default(env) -> None:
    _, events = _run(env.client, _payload(env))
    assert _summary(events)["dataset_toml"] is None
    assert not (env.sandbox / "out" / "dataset.toml").exists()


def test_an_existing_dataset_toml_is_not_replaced(env) -> None:
    """The export writes dataset.toml, and that file may be hand-tuned.

    The rule this job already applies to the images it exports (never overwrite, its
    unique_destination) has to hold for the generated file too: a run that finds one there writes
    nothing at all.
    """
    out = env.sandbox / "out"
    out.mkdir(parents=True)
    existing = out / "dataset.toml"
    original = b"# hand tuned\n[datasets]\nbatch_size = 8\n"
    existing.write_bytes(original)
    before = existing.stat().st_mtime_ns

    _, events = _run(env.client, _payload(env, write_dataset_toml=True))
    summary = _summary(events)

    assert existing.read_bytes() == original, "the export replaced a dataset.toml that was already there"
    assert existing.stat().st_mtime_ns == before, "the existing dataset.toml was rewritten byte-identically"
    assert summary["dataset_toml"] is None
    assert summary["dataset_toml_skipped"] is True, "the run never said it left the file alone"
    assert any("already exists" in line for line in summary["dataset_toml_warnings"]), summary
    assert not (out / "dataset.toml.bak").exists(), "nothing was replaced, so nothing should be backed up"


def test_overwriting_a_dataset_toml_backs_it_up(env) -> None:
    out = env.sandbox / "out"
    out.mkdir(parents=True)
    existing = out / "dataset.toml"
    original = b"# hand tuned\n[datasets]\nbatch_size = 8\n"
    existing.write_bytes(original)

    _, events = _run(
        env.client, _payload(env, write_dataset_toml=True, overwrite_dataset_toml=True)
    )
    summary = _summary(events)

    assert summary["dataset_toml"] == str(existing)
    assert summary["dataset_toml_skipped"] is False
    assert existing.read_bytes() != original, "the overwrite was asked for and did not happen"
    assert "1_post" in existing.read_text(encoding="utf-8")
    backup = out / "dataset.toml.bak"
    assert summary["dataset_toml_backup"] == str(backup)
    assert backup.read_bytes() == original, "the replaced file was not preserved byte for byte"


def test_a_second_overwrite_keeps_the_first_backup(env) -> None:
    """The backup is the state before this tool touched the file, so it is never replaced either."""
    out = env.sandbox / "out"
    out.mkdir(parents=True)
    existing = out / "dataset.toml"
    original = b"# the original\n"
    existing.write_bytes(original)

    _, first_events = _run(env.client, _payload(env, write_dataset_toml=True, overwrite_dataset_toml=True))
    backup = out / "dataset.toml.bak"
    assert _summary(first_events)["dataset_toml_backup"] == str(backup)

    _, second_events = _run(env.client, _payload(env, write_dataset_toml=True, overwrite_dataset_toml=True))

    assert _summary(second_events)["dataset_toml"] == str(existing), "the second run did not write"
    # The generated text is deliberately not compared between the two runs: the second export lands
    # next to the first one (unique_destination appends _1), so it legitimately differs. The backup is
    # what must not move.
    assert backup.read_bytes() == original, (
        "the second overwrite replaced the one copy of the pre-existing file"
    )


def test_target_directory_is_created_recursively(env) -> None:
    target = env.sandbox / "nested" / "deep"
    resp, events = _run(env.client, _payload(env, target_dir=str(target)))
    assert resp.status_code == 202
    assert (target / "1_post" / "1.png").is_file()
    assert _summary(events)["failed"] == 0


def test_source_files_are_untouched(env) -> None:
    before = {
        path: (path.stat().st_size, path.stat().st_mtime_ns)
        for path in sorted(env.dataset.rglob("*"))
        if path.is_file()
    }
    _run(env.client, _payload(env))
    after = {
        path: (path.stat().st_size, path.stat().st_mtime_ns)
        for path in sorted(env.dataset.rglob("*"))
        if path.is_file()
    }
    assert before == after


def test_no_temp_files_left_in_target(env) -> None:
    _run(env.client, _payload(env))
    leftovers = [p.name for p in (env.sandbox / "out").rglob(".tmp-scale-*")]
    assert leftovers == []


def test_missing_caption_is_not_counted_but_does_not_fail(env) -> None:
    (env.dataset / "1_post" / "1.txt").unlink()
    _, events = _run(env.client, _payload(env))
    summary = _summary(events)
    assert summary["failed"] == 0
    assert summary["captions"] == 1
    assert not (env.sandbox / "out" / "1_post" / "1.txt").exists()


# ---------------------------------------------------------------------------
# 2. Target directory and error paths
# ---------------------------------------------------------------------------


def test_target_outside_roots_is_rejected(env) -> None:
    resp = env.client.post("/api/scale/run", json=_payload(env, target_dir=str(env.tmp / "outside")))
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "outside_roots"


def test_target_inside_source_is_rejected(env) -> None:
    resp = env.client.post(
        "/api/scale/run", json=_payload(env, target_dir=str(env.dataset / "export"))
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["code"] == "target_inside_root"
    assert not (env.dataset / "export").exists()


def test_target_equal_to_source_is_rejected(env) -> None:
    resp = env.client.post("/api/scale/run", json=_payload(env, target_dir=str(env.dataset)))
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["code"] == "target_inside_root"


def test_target_that_is_a_file_is_rejected(env) -> None:
    note = env.sandbox / "note.txt"
    note.write_text("x", encoding="utf-8")
    resp = env.client.post("/api/scale/run", json=_payload(env, target_dir=str(note)))
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["code"] == "not_a_directory"


def test_missing_root_is_404(env) -> None:
    resp = env.client.post("/api/scale/run", json=_payload(env, root=str(env.dataset / "missing")))
    assert resp.status_code == 404, resp.text
    assert resp.json()["error"]["code"] == "not_found"


def test_an_empty_image_list_is_400(env) -> None:
    """An empty list is refused, not widened to the whole directory.

    \"Then export everything under the root\" is the exact surprise this contract removes: a user
    who picked nothing must not get a copy of the dataset. (Before 2026-09-20 the endpoint had no
    list at all and always did that.)
    """
    resp = env.client.post("/api/scale/run", json=_payload(env, images=[]))
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["code"] == "no_images"
    assert resp.json()["error"]["params"] == {"path": str(env.dataset)}
    assert not (env.sandbox / "out").exists(), "a refused request created the target directory"


def test_a_body_without_a_list_is_refused(env) -> None:
    """The old whole-root body (`root` + `target_dir`, no images) is not accepted any more.

    Leaving it working as a fallback would leave the widened export reachable, so the field is
    absent-tolerant in pydantic and answered by an explicit 400 instead.
    """
    body = _payload(env)
    body.pop("images")
    resp = env.client.post("/api/scale/run", json=body)
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["code"] == "no_images"


def test_empty_target_dir_is_rejected(env) -> None:
    resp = env.client.post("/api/scale/run", json=_payload(env, target_dir="   "))
    assert resp.status_code == 422, resp.text


@pytest.mark.parametrize("override", [
    {"output_format": "tiff"},
    {"resample": "magic"},
    {"target_width": 0},
    {"quality": 0},
    {"caption_extension": "  "},
])
def test_invalid_params_are_422(env, override: dict) -> None:
    resp = env.client.post("/api/scale/run", json=_payload(env, **override))
    assert resp.status_code == 422, resp.text


def test_unknown_key_is_422(env) -> None:
    resp = env.client.post("/api/scale/run", json=_payload(env, sidecars="txt"))
    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# 3. Cancel and SSE
# ---------------------------------------------------------------------------


def test_cancel_unknown_job_is_202_and_not_cancelled(env) -> None:
    resp = env.client.post("/api/scale/cancel", json={"job_id": "nope"})
    assert resp.status_code == 202, resp.text
    assert resp.json() == {"cancelled": False}


def test_stream_unknown_job_is_404(env) -> None:
    resp = env.client.get("/api/scale/stream", params={"job_id": "nope"})
    assert resp.status_code == 404, resp.text
    assert resp.json()["error"]["code"] == "unknown_job"
    assert resp.json()["error"]["params"] == {"job_id": "nope"}


def test_cancel_stops_the_run(env) -> None:
    # Start the job, then cancel immediately: whether or not it finished, done must carry finished:true.
    dataset = env.dataset
    for index in range(12):
        _png(dataset / "1_post" / ("bulk_%02d.png" % index), size=(2000, 2000))
    resp = env.client.post("/api/scale/run", json=_payload(env))
    assert resp.status_code == 202, resp.text
    job_id = resp.json()["job_id"]
    env.client.post("/api/scale/cancel", json={"job_id": job_id})
    with env.client.stream("GET", "/api/scale/stream", params={"job_id": job_id}) as stream:
        events = _parse_sse("".join(stream.iter_text()))
    done = events[-1][1]
    assert done["finished"] is True
    assert isinstance(done["cancelled"], bool)

# ---------------------------------------------------------------------------
# 4. The image list is the export's whole extent (2026-09-20)
# ---------------------------------------------------------------------------


def test_the_export_covers_exactly_the_listed_images(env) -> None:
    """The defect this contract removes: the export acted on the whole directory, not on the list.

    One image of two is listed. A run that still walks `root` - or that keeps the old whole-root
    enumeration as a fallback - writes two files (and two captions) where the user picked one.
    """
    resp, events = _run(env.client, _payload(env, images=_images(env)[:1]))
    assert resp.status_code == 202, resp.text
    assert resp.json()["total"] == 1, "the job's total is not the list's length"

    out = env.sandbox / "out"
    exported = sorted(
        path.relative_to(out).as_posix() for path in out.rglob("*") if path.is_file()
    )
    assert exported == ["1_post/1.png", "1_post/1.txt"], exported
    assert _summary(events)["processed"] == 1


def test_the_list_order_is_the_processing_order(env) -> None:
    """The list arrives in the scope's order and the job keeps it: the progress stream is what the user watches."""
    listed = list(reversed(_images(env)))
    _, events = _run(env.client, _payload(env, images=listed))
    processed = [data["image"]["src"] for name, data in events if name == "progress"]
    assert processed == listed, processed


def test_a_duplicate_entry_is_exported_once(env) -> None:
    """A path listed twice is one image to the user, so it is one file.

    `unique_destination` would have given the second copy a `_1` suffix: two files on disk, and
    the caption written twice, for an image that was picked once.
    """
    first = _images(env)[0]
    resp, _ = _run(env.client, _payload(env, images=[first, first]))
    assert resp.status_code == 202, resp.text
    assert resp.json()["total"] == 1
    names = sorted(path.name for path in (env.sandbox / "out" / "1_post").iterdir())
    assert names == ["1.png", "1.txt"], names


def test_a_recursive_scope_list_is_rebuilt_by_structure(env) -> None:
    """The "directory + subdirectories" scope: a flat list of paths, exported as the tree it came from.

    The list is built by asking /api/fs/list the way the panel does, so this is the real chain -
    including the pruning: cache_text_encoder/, latent_cache/ and dot-directories are never
    listed, so they can never be exported. The target tree has to **correspond directory by
    directory** to the source instead of flattening the images into one level.
    """
    root = env.sandbox / "whole"
    _png(root / "a" / "1.png", size=(3000, 2000))
    (root / "a" / "1.txt").write_text("1girl", encoding="utf-8")
    _png(root / "a" / "b" / "2.png", size=(3000, 2000))
    (root / "a" / "b" / "2.txt").write_text("2girls", encoding="utf-8")
    _png(root / "c" / "3.png", size=(3000, 2000))
    _png(root / "top.png", size=(3000, 2000))
    _png(root / "cache_text_encoder" / "ignored.png")
    _png(root / "latent_cache" / "ignored.png")
    _png(root / ".hidden" / "ignored.png")

    images = _listed_images(env, root)
    assert len(images) == 4, "the listing did not prune the cache/hidden directories: %s" % images

    target = env.sandbox / "whole_out"
    resp, events = _run(
        env.client, _payload(env, images=images, root=str(root), target_dir=str(target))
    )
    assert resp.status_code == 202, resp.text
    assert resp.json()["total"] == 4

    exported = sorted(
        path.relative_to(target).as_posix()
        for path in target.rglob("*")
        if path.is_file()
    )
    assert exported == [
        "a/1.png", "a/1.txt", "a/b/2.png", "a/b/2.txt", "c/3.png", "top.png",
    ], exported
    # not a single image from the cache or hidden directories may come along
    assert not (target / "cache_text_encoder").exists()
    assert not (target / "latent_cache").exists()
    assert not (target / ".hidden").exists()
    # captions from the nested level come along too
    assert (target / "a" / "b" / "2.txt").read_text(encoding="utf-8") == "2girls"

    summary = _summary(events)
    assert summary["processed"] == 4
    assert summary["captions"] == 2
    assert summary["failed"] == 0


def test_an_image_outside_the_source_root_is_400(env) -> None:
    """`destination_directory` rebuilds the target from the source's path relative to root, so an image from outside it has no place in the tree."""
    other = env.sandbox / "elsewhere"
    _png(other / "x.png", size=(64, 64))
    resp = env.client.post("/api/scale/run", json=_payload(env, images=[str(other / "x.png")]))
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["code"] == "image_outside_root"


def test_an_image_outside_the_roots_is_403(env) -> None:
    """The allowlist decides first: a list entry is a user path like any other."""
    outside = env.tmp / "not_a_root"
    _png(outside / "x.png", size=(64, 64))
    resp = env.client.post("/api/scale/run", json=_payload(env, images=[str(outside / "x.png")]))
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "outside_roots"


def test_a_missing_listed_image_is_404(env) -> None:
    ghost = env.dataset / "1_post" / "gone.png"
    resp = env.client.post("/api/scale/run", json=_payload(env, images=[str(ghost)]))
    assert resp.status_code == 404, resp.text
    assert resp.json()["error"]["code"] == "not_found"
    assert resp.json()["error"]["params"] == {"path": str(ghost)}


def test_a_non_image_entry_is_400(env) -> None:
    note = env.dataset / "1_post" / "notes.txt"
    note.write_text("x", encoding="utf-8")
    resp = env.client.post("/api/scale/run", json=_payload(env, images=[str(note)]))
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["code"] == "unsupported_image"


def test_a_directory_entry_is_400(env) -> None:
    directory = env.dataset / "1_post" / "sub"
    directory.mkdir()
    resp = env.client.post("/api/scale/run", json=_payload(env, images=[str(directory)]))
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["code"] == "not_a_file"
# ---------------------------------------------------------------------------
# 5. Parallel render: EXPORT_WORKERS>1 must produce the sequential bytes exactly
# ---------------------------------------------------------------------------


def _export_once(dataset: Path, target: Path, workers: int, images: list):
    """Run the real api/scale.py _export worker body at a given worker count."""
    from kohya_dataset_tagger.api import scale as scale_api

    settings = {
        "target_width": 768,
        "target_height": 768,
        "no_upscale": True,
        "resample": "lanczos",
        "output_format": "png",
        "quality": 95,
        "optimize": True,
        "caption_extension": ".txt",
        "write_dataset_toml": False,
    }
    job = scale_api.ScaleJob("test-%d" % workers, len(images), dataset, target)
    previous = scale_api.EXPORT_WORKERS
    scale_api.EXPORT_WORKERS = workers
    try:
        scale_api._export(job, images, settings)
    finally:
        scale_api.EXPORT_WORKERS = previous
    return job


def _tree_digest(root: Path) -> dict:
    import hashlib

    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_parallel_render_matches_sequential_byte_for_byte(tmp_path: Path) -> None:
    """The render stage is parallel but destinations are allocated on one thread, so the output
    must be byte-for-byte what the sequential loop produced - including the never-overwrite
    numbered suffix on a same-stem collision (.png + .jpeg in one directory make it observable).
    """
    dataset = tmp_path / "dataset"
    (dataset / "a").mkdir(parents=True)
    (dataset / "b").mkdir(parents=True)
    _png(dataset / "a" / "same.png", size=(1600, 1200))
    (dataset / "a" / "same.txt").write_text("png sibling", encoding="utf-8")
    _jpeg(dataset / "a" / "same.jpeg", size=(800, 600))
    for index in range(6):
        _png(dataset / "b" / ("img%d.png" % index), size=(1200, 900))
    images = sorted(
        path
        for path in dataset.rglob("*")
        if path.is_file() and path.suffix.lower() in (".png", ".jpeg", ".jpg")
    )

    sequential_job = _export_once(dataset, tmp_path / "out_seq", 1, images)
    parallel_job = _export_once(dataset, tmp_path / "out_par", 4, images)

    assert sequential_job.done == len(images)
    assert parallel_job.done == len(images)
    assert sequential_job.errors == []
    assert parallel_job.errors == []
    assert _tree_digest(tmp_path / "out_par") == _tree_digest(tmp_path / "out_seq")
    names = sorted(path.name for path in (tmp_path / "out_par" / "a").iterdir())
    assert names == ["same.png", "same.txt", "same_1.png", "same_1.txt"], names


