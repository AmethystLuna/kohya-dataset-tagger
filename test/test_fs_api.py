"""HTTP contract tests for api/fs.py and api/images.py (spec §4).

By the parallel-workflow convention, this file assembles its own minimal FastAPI app mounting only these two routers,
without depending on app.py (that is a wave-2 file). All disk writes go to tmp_path; the real dataset is never touched.
"""
from __future__ import annotations

import types
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from kohya_dataset_tagger import config
from kohya_dataset_tagger.api import fs as fs_api
from kohya_dataset_tagger.api import images as images_api
from kohya_dataset_tagger.core import paths


@pytest.fixture(autouse=True)
def _neutral_state():
    yield
    config.load(env={}, roots=[])


def _jpeg(path: Path, size=(40, 30), color=(200, 30, 30)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path, "JPEG")
    return path


def _png_rgba(path: Path, size=(24, 18)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    im = Image.new("RGBA", size, (0, 0, 255, 128))
    im.putpixel((0, 0), (0, 255, 0, 0))
    im.save(path, "PNG")
    return path


def _snapshot(root: Path) -> list:
    out = []
    for path in sorted(root.rglob("*")):
        st = path.stat()
        out.append((path.relative_to(root).as_posix(), st.st_size, st.st_mtime_ns))
    return out


@pytest.fixture
def env(tmp_path: Path):
    """A mini dataset: normal post directories + cache/hidden directories that must be filtered out + an image missing its caption."""
    root = tmp_path / "dataset"
    (root / "1_post").mkdir(parents=True)
    (root / "2_post").mkdir()
    (root / "cache_text_encoder").mkdir()
    (root / "latent_cache").mkdir()
    (root / ".hidden").mkdir()
    _jpeg(root / "1_post" / "1.jpeg")
    _jpeg(root / "1_post" / "2.jpeg", size=(50, 50))
    (root / "1_post" / "1.txt").write_text("1girl, solo", encoding="utf-8")
    _png_rgba(root / "2_post" / "1.png")
    (root / "2_post" / "orphan.txt").write_text("1girl", encoding="utf-8")
    _jpeg(root / "cache_text_encoder" / "9.jpeg")
    _jpeg(root / "latent_cache" / "8.jpeg")
    _jpeg(root / ".hidden" / "7.jpeg")
    (root / "notes.txt").write_text("not an image", encoding="utf-8")

    cache = tmp_path / "thumbs"
    paths.configure([root])
    config.load(env={"KOHYA_TAGGER_ROOTS": str(root), "KOHYA_TAGGER_THUMB_CACHE": str(cache)})

    app = FastAPI()
    app.include_router(fs_api.router)
    app.include_router(images_api.router)
    with TestClient(app) as client:
        yield types.SimpleNamespace(client=client, root=root, cache=cache)


# ---------------------------------------------------------------------------
# GET /api/fs/list
# ---------------------------------------------------------------------------


def test_fs_list_root_shape_and_filters(env) -> None:
    response = env.client.get("/api/fs/list", params={"path": str(env.root)})
    assert response.status_code == 200
    body = response.json()
    assert body["path"] == str(env.root)
    assert body["parent"] is None  # allowlist boundary, no parent given
    assert [d["name"] for d in body["dirs"]] == ["1_post", "2_post"]
    assert [d["image_count"] for d in body["dirs"]] == [2, 1]
    assert [d["path"] for d in body["dirs"]] == [str(env.root / "1_post"), str(env.root / "2_post")]
    assert body["images"] == []  # not recursive: the trainer's glob_images also scans only one level
    assert body["counts"] == {"images": 0, "dirs": 2, "captions": 0, "missing_captions": 0}


def test_fs_list_dirs_carry_preview_images(env) -> None:
    """§4.13: every subdirectory carries at most 9 sampled images, for the center gallery's nine-grid."""
    body = env.client.get("/api/fs/list", params={"path": str(env.root)}).json()
    first = body["dirs"][0]
    assert set(first) == {"name", "path", "image_count", "preview_images"}
    assert first["preview_images"] == [
        str(env.root / "1_post" / "1.jpeg"),
        str(env.root / "1_post" / "2.jpeg"),
    ], "sampling must share the source and order (natural order) with image_count"
    assert body["dirs"][1]["preview_images"] == [str(env.root / "2_post" / "1.png")]
    assert all(Path(item).is_file() for entry in body["dirs"] for item in entry["preview_images"])


def test_fs_list_preview_images_are_capped_at_nine_and_one_level(tmp_path: Path) -> None:
    """§4.13: capped at 9 and one level only - otherwise the nine-grid would pass off images from a subtree as this directory's."""
    root = tmp_path / "dataset"
    post = root / "1_post"
    post.mkdir(parents=True)
    for index in range(1, 13):
        _jpeg(post / ("%d.jpeg" % index))
    nested = post / "nested"
    nested.mkdir()
    _jpeg(nested / "99.jpeg")

    paths.configure([root])
    config.load(env={"KOHYA_TAGGER_ROOTS": str(root), "KOHYA_TAGGER_THUMB_CACHE": str(tmp_path / "thumbs")})
    app = FastAPI()
    app.include_router(fs_api.router)
    with TestClient(app) as client:
        body = client.get("/api/fs/list", params={"path": str(root)}).json()

    entry = body["dirs"][0]
    assert entry["image_count"] == 12, "image_count must not have its semantics changed by preview sampling"
    assert len(entry["preview_images"]) == 9, "the nine-grid should take only 9"
    assert [Path(item).name for item in entry["preview_images"]] == [
        "%d.jpeg" % index for index in range(1, 10)
    ], "sampling is the first 9 in natural order"
    assert all(Path(item).parent == post for item in entry["preview_images"]), "preview may only come from this level"


def test_fs_list_counts_captions_and_missing_captions(env) -> None:
    response = env.client.get("/api/fs/list", params={"path": str(env.root / "1_post")})
    body = response.json()
    assert body["counts"]["images"] == 2
    assert body["counts"]["captions"] == 1
    assert body["counts"]["missing_captions"] == 1
    assert [i["name"] for i in body["images"]] == ["1.jpeg", "2.jpeg"]
    # §4.2: each image carries has_caption, consistent with counts
    assert [i["has_caption"] for i in body["images"]] == [True, False]
    entry = body["images"][0]
    assert set(entry) == {"name", "path", "size", "mtime", "has_caption"}
    assert Path(entry["path"]).is_file()
    assert entry["size"] == (env.root / "1_post" / "1.jpeg").stat().st_size
    assert entry["mtime"] == pytest.approx((env.root / "1_post" / "1.jpeg").stat().st_mtime)
    assert body["parent"] == str(env.root)


def test_fs_list_recursive_collects_images_but_dirs_stay_one_level(env) -> None:
    response = env.client.get("/api/fs/list", params={"path": str(env.root), "recursive": "true"})
    body = response.json()
    assert [i["name"] for i in body["images"]] == ["1.jpeg", "2.jpeg", "1.png"]
    assert [i["has_caption"] for i in body["images"]] == [True, False, False]
    assert body["counts"] == {"images": 3, "dirs": 2, "captions": 1, "missing_captions": 2}


def test_fs_list_images_carry_has_caption_boolean(env) -> None:
    """§4.2: every images[] entry must carry has_caption, so the frontend need not send another /api/captions per image."""
    body = env.client.get("/api/fs/list", params={"path": str(env.root), "recursive": "true"}).json()
    entries = {item["name"]: item for item in body["images"]}
    assert len(entries) == len(body["images"]) == 3

    for item in body["images"]:
        assert set(item) == {"name", "path", "size", "mtime", "has_caption"}
        assert isinstance(item["has_caption"], bool)
        # the field must match the disk truth, not be always true/false
        assert Path(item["path"]).with_suffix(".txt").is_file() is item["has_caption"]

    # 1_post/1.jpeg has 1.txt; 1_post/2.jpeg does not; orphan.txt next to 2_post/1.png is not its sidecar
    assert entries["1.jpeg"]["has_caption"] is True
    assert entries["2.jpeg"]["has_caption"] is False
    assert entries["1.png"]["has_caption"] is False
    # the same semantics as counts
    assert sum(item["has_caption"] for item in body["images"]) == body["counts"]["captions"] == 1
    assert body["counts"]["missing_captions"] == 2


def test_fs_list_without_path_uses_the_first_root(env) -> None:
    response = env.client.get("/api/fs/list")
    assert response.status_code == 200
    assert response.json()["path"] == str(env.root)


def test_fs_list_outside_absolute_path_is_403(env) -> None:
    response = env.client.get("/api/fs/list", params={"path": r"C:\Windows"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "outside_roots"


def test_fs_list_traversal_is_403(env) -> None:
    response = env.client.get("/api/fs/list", params={"path": "../../etc/passwd"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "outside_roots"


def test_fs_list_missing_path_is_404(env) -> None:
    response = env.client.get("/api/fs/list", params={"path": str(env.root / "nope")})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_fs_list_on_a_file_is_400(env) -> None:
    response = env.client.get("/api/fs/list", params={"path": str(env.root / "notes.txt")})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "not_a_directory"


# ---------------------------------------------------------------------------
# GET /api/images/thumb
# ---------------------------------------------------------------------------


def test_thumb_returns_webp_bytes_with_immutable_headers(env) -> None:
    image = env.root / "1_post" / "1.jpeg"
    response = env.client.get("/api/images/thumb", params={"path": str(image)})
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/webp"
    assert response.content[:4] == b"RIFF"
    assert response.content[8:12] == b"WEBP"
    etag = response.headers["etag"]
    assert etag.startswith('"') and etag.endswith('"')
    key = etag.strip('"')
    assert len(key) == 16 and all(c in "0123456789abcdef" for c in key)
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_thumb_size_grid_and_preview_are_different_entries(env) -> None:
    # the source image must be larger than grid, otherwise preview is not scaled and the two images have identical bytes
    image = env.root / "1_post" / "2.jpeg"
    _jpeg(image, size=(400, 300))
    grid = env.client.get("/api/images/thumb", params={"path": str(image), "size": "grid"})
    preview = env.client.get("/api/images/thumb", params={"path": str(image), "size": "preview"})
    assert grid.status_code == preview.status_code == 200
    assert grid.headers["etag"] != preview.headers["etag"]
    assert grid.content != preview.content


def test_thumb_second_request_hits_the_cache(env) -> None:
    image = env.root / "1_post" / "1.jpeg"
    first = env.client.get("/api/images/thumb", params={"path": str(image)})
    second = env.client.get("/api/images/thumb", params={"path": str(image)})
    assert first.headers["etag"] == second.headers["etag"]
    assert first.content == second.content
    assert len(list(env.cache.glob("*.webp"))) == 1


def test_thumb_never_writes_into_the_dataset_dir(env) -> None:
    before = _snapshot(env.root)
    response = env.client.get("/api/images/thumb", params={"path": str(env.root / "2_post" / "1.png")})
    assert response.status_code == 200
    assert _snapshot(env.root) == before
    assert env.cache.is_dir()


def test_thumb_bad_size_is_400(env) -> None:
    response = env.client.get(
        "/api/images/thumb", params={"path": str(env.root / "1_post" / "1.jpeg"), "size": "huge"}
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "bad_size"


def test_thumb_traversal_is_403(env) -> None:
    response = env.client.get("/api/images/thumb", params={"path": "../../etc/passwd"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "outside_roots"


def test_thumb_outside_absolute_path_is_403(env) -> None:
    response = env.client.get("/api/images/thumb", params={"path": r"C:\Windows\win.ini"})
    assert response.status_code == 403


def test_thumb_missing_file_is_404(env) -> None:
    response = env.client.get("/api/images/thumb", params={"path": str(env.root / "1_post" / "9.jpeg")})
    assert response.status_code == 404


def test_thumb_non_image_is_400(env) -> None:
    response = env.client.get("/api/images/thumb", params={"path": str(env.root / "notes.txt")})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "unsupported_image"


# ---------------------------------------------------------------------------
# GET /api/images/meta
# ---------------------------------------------------------------------------


def test_meta_reports_alpha_for_rgba_png(env) -> None:
    image = env.root / "2_post" / "1.png"
    response = env.client.get("/api/images/meta", params={"path": str(image)})
    assert response.status_code == 200
    body = response.json()
    assert body["path"] == str(image)
    assert (body["width"], body["height"]) == (24, 18)
    assert body["bytes"] == image.stat().st_size
    assert body["mtime"] == pytest.approx(image.stat().st_mtime)
    assert body["mode"] == "RGBA"
    assert body["has_alpha"] is True


def test_meta_reports_no_alpha_for_jpeg(env) -> None:
    body = env.client.get("/api/images/meta", params={"path": str(env.root / "1_post" / "1.jpeg")}).json()
    assert (body["width"], body["height"]) == (40, 30)
    assert body["mode"] == "RGB"
    assert body["has_alpha"] is False


def test_meta_traversal_is_403(env) -> None:
    response = env.client.get("/api/images/meta", params={"path": "../../etc/passwd"})
    assert response.status_code == 403


def test_meta_missing_file_is_404(env) -> None:
    response = env.client.get("/api/images/meta", params={"path": str(env.root / "ghost.jpeg")})
    assert response.status_code == 404


def test_meta_non_image_is_400(env) -> None:
    response = env.client.get("/api/images/meta", params={"path": str(env.root / "notes.txt")})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "unsupported_image"


# ---------------------------------------------------------------------------
# error shape (spec §4: always {"error": {"code", "message"}})
# ---------------------------------------------------------------------------


def test_every_error_uses_the_frozen_shape(env) -> None:
    for response in (
        env.client.get("/api/fs/list", params={"path": r"C:\Windows"}),
        env.client.get("/api/fs/list", params={"path": str(env.root / "nope")}),
        env.client.get("/api/fs/list", params={"path": str(env.root / "notes.txt")}),
        env.client.get("/api/images/thumb", params={"path": "../../etc/passwd"}),
        env.client.get("/api/images/thumb", params={"path": str(env.root / "1_post" / "1.jpeg"), "size": "x"}),
    ):
        assert response.status_code >= 400
        body = response.json()
        assert set(body) == {"error"}
        # 2026-09-19: the error shape gained params (§4 supplement); the other two legacy fields are unchanged verbatim.
        assert set(body["error"]) == {"code", "message", "params"}
        assert isinstance(body["error"]["code"], str) and body["error"]["code"]
        assert isinstance(body["error"]["message"], str) and body["error"]["message"]
        assert isinstance(body["error"]["params"], dict)
