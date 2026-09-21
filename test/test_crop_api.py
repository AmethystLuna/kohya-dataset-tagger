"""HTTP contract tests for api/crop.py (§4.17): crop one image, archive it beside the source.

Criterion highlights:
1. the archive is `<stem>_<n><ext>` in the **source's own directory**, the source file itself keeps
   its bytes and mtime, and a second run gets the next number (never an overwrite);
2. **no caption is inherited** - the archive starts without a sidecar, so the optional tagger step
   is what produces one;
3. the crop box is **clamped, not rejected** (a rectangle a few pixels outside the frame is a
   rounding artefact of the confirmation dialog), and the applied rectangle is reported back;
4. "keep" really keeps: a .jpeg source produces a .jpeg archive, and a format we can read but not
   write (BMP) falls back to PNG **and says so**;
5. the rectangle is in **display coordinates**: with EXIF orientation 6 the crop is applied to the
   rotated image, which is the frame the confirmation dialog showed;
6. with the scaling half, the cropped size is what area normalization works from - so a crop box
   whose area already equals the target area comes out at scale 1.0, unresampled.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageOps

from kohya_dataset_tagger import config

TARGET = (1536, 1536)


def _png(path: Path, size=(100, 100), color=(10, 20, 30)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path, "PNG")
    return path


def _jpeg(path: Path, size=(200, 100), color=(200, 30, 30), orientation: int | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", size, color)
    if orientation is None:
        image.save(path, "JPEG", quality=90)
    else:
        exif = image.getexif()
        exif[0x0112] = orientation
        image.save(path, "JPEG", quality=90, exif=exif)
    return path


def _snapshot(path: Path) -> tuple[int, int, bytes]:
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns, path.read_bytes()


@pytest.fixture(autouse=True)
def _neutral_state():
    yield
    config.load(env={}, roots=[])


@pytest.fixture
def env(tmp_path: Path):
    dataset = tmp_path / "dataset"
    _png(dataset / "1_post" / "1.png", size=(3000, 2000))
    (dataset / "1_post" / "1.txt").write_bytes("1girl, solo\n".encode("utf-8"))
    _jpeg(dataset / "1_post" / "2.jpeg", size=(400, 300))
    _jpeg(dataset / "1_post" / "3.jpg", size=(200, 100), orientation=6)
    Image.new("RGB", (120, 80), (5, 5, 5)).save(dataset / "1_post" / "4.bmp", "BMP")
    (dataset / "1_post" / "5.png").write_bytes(b"not an image at all")
    outside = tmp_path / "outside"
    outside.mkdir()
    _png(outside / "6.png", size=(50, 50))

    config.load(
        env={},
        roots=[dataset],
        thumb_cache_dir=tmp_path / "thumbs",
        model_search_roots=[],
    )
    from kohya_dataset_tagger import app as app_module

    app_obj = app_module.create_app()
    with TestClient(app_obj) as client:
        yield types_ns(client=client, dataset=dataset, outside=outside, tmp=tmp_path)


def types_ns(**kwargs):
    import types

    return types.SimpleNamespace(**kwargs)


def _apply(client: TestClient, **payload):
    return client.post("/api/crop/apply", json=payload)


# ---------------------------------------------------------------------------
# 1. one archive per confirmed crop, beside the source
# ---------------------------------------------------------------------------


def test_crop_archives_next_to_the_source_and_never_touches_it(env) -> None:
    src = env.dataset / "1_post" / "1.png"
    before = _snapshot(src)

    response = _apply(env.client, src=str(src), crop={"x": 100, "y": 50, "width": 800, "height": 600})

    assert response.status_code == 200, response.text
    body = response.json()
    archived = Path(body["archived"])
    assert archived == env.dataset / "1_post" / "1_1.png"
    assert body["format"] == "png"
    assert body["crop"] == {"x": 100, "y": 50, "width": 800, "height": 600}
    assert (body["orig_width"], body["orig_height"]) == (3000, 2000)
    assert (body["crop_width"], body["crop_height"]) == (800, 600)
    assert (body["output_width"], body["output_height"]) == (800, 600)
    assert body["resampled"] is False
    assert body["scale"] == 1.0
    assert body["warnings"] == []

    with Image.open(archived) as produced:
        assert produced.size == (800, 600)
        # the pixels really are the requested window of the source
        with Image.open(src) as source:
            assert produced.convert("RGB").tobytes() == source.convert("RGB").crop((100, 50, 900, 650)).tobytes()

    assert _snapshot(src) == before
    assert list(archived.parent.glob(".tmp-scale-*")) == []


def test_a_second_crop_of_the_same_image_takes_the_next_number(env) -> None:
    src = env.dataset / "1_post" / "1.png"
    first = _apply(env.client, src=str(src), crop={"x": 0, "y": 0, "width": 10, "height": 10}).json()
    second = _apply(env.client, src=str(src), crop={"x": 0, "y": 0, "width": 20, "height": 20}).json()

    assert Path(first["archived"]).name == "1_1.png"
    assert Path(second["archived"]).name == "1_2.png"
    assert Path(first["archived"]).exists() and Path(second["archived"]).exists()


def test_an_archive_never_overwrites_a_file_the_user_made(env) -> None:
    src = env.dataset / "1_post" / "1.png"
    mine = env.dataset / "1_post" / "1_1.png"
    _png(mine, size=(7, 7), color=(1, 2, 3))

    body = _apply(env.client, src=str(src), crop={"x": 0, "y": 0, "width": 10, "height": 10}).json()

    assert Path(body["archived"]).name == "1_2.png"
    with Image.open(mine) as untouched:
        assert untouched.size == (7, 7)


def test_no_caption_is_inherited(env) -> None:
    src = env.dataset / "1_post" / "1.png"
    assert (src.with_suffix(".txt")).exists()

    body = _apply(env.client, src=str(src), crop={"x": 0, "y": 0, "width": 100, "height": 100}).json()

    # The archive starts with no sidecar, and the response does not pretend to report one.
    assert "caption" not in body
    assert not Path(body["archived"]).with_suffix(".txt").exists()


# ---------------------------------------------------------------------------
# 2. clamping, not rejecting
# ---------------------------------------------------------------------------


def test_a_crop_box_hanging_over_the_edge_is_clamped_and_reported(env) -> None:
    src = env.dataset / "1_post" / "1.png"

    body = _apply(env.client, src=str(src), crop={"x": 2900, "y": 1900, "width": 800, "height": 600}).json()

    assert body["crop"] == {"x": 2200, "y": 1400, "width": 800, "height": 600}
    assert body["crop_requested"] == {"x": 2900, "y": 1900, "width": 800, "height": 600}
    assert (body["output_width"], body["output_height"]) == (800, 600)
    assert body["warnings"] and "clamped" in body["warnings"][0]


def test_a_crop_box_larger_than_the_image_becomes_the_whole_image(env) -> None:
    src = env.dataset / "1_post" / "2.jpeg"

    body = _apply(env.client, src=str(src), crop={"x": 10, "y": 10, "width": 5000, "height": 4000}).json()

    assert body["crop"] == {"x": 0, "y": 0, "width": 400, "height": 300}
    assert (body["output_width"], body["output_height"]) == (400, 300)


def test_a_zero_sized_crop_box_is_rejected(env) -> None:
    src = env.dataset / "1_post" / "1.png"
    assert _apply(env.client, src=str(src), crop={"width": 0, "height": 10}).status_code == 422


# ---------------------------------------------------------------------------
# 3. format: keep really keeps
# ---------------------------------------------------------------------------


def test_keep_keeps_the_sources_own_format_and_suffix(env) -> None:
    src = env.dataset / "1_post" / "2.jpeg"

    body = _apply(env.client, src=str(src), crop={"x": 0, "y": 0, "width": 100, "height": 100}).json()

    assert body["format"] == "jpeg"
    assert Path(body["archived"]).name == "2_1.jpeg"
    with Image.open(body["archived"]) as produced:
        assert produced.format == "JPEG"


def test_a_format_we_cannot_write_falls_back_to_png_and_says_so(env) -> None:
    src = env.dataset / "1_post" / "4.bmp"

    body = _apply(env.client, src=str(src), crop={"x": 0, "y": 0, "width": 60, "height": 40}).json()

    assert body["format"] == "png"
    assert Path(body["archived"]).name == "4_1.png"
    assert body["warnings"] and "PNG" in body["warnings"][0]


def test_an_explicit_format_wins_over_the_source(env) -> None:
    src = env.dataset / "1_post" / "1.png"

    body = _apply(
        env.client,
        src=str(src),
        crop={"x": 0, "y": 0, "width": 100, "height": 100},
        format="webp",
    ).json()

    assert body["format"] == "webp"
    # Always numbered, even though "1.webp" was free: the archive never takes the source's own stem.
    assert Path(body["archived"]).name == "1_1.webp"


def test_a_whole_frame_crop_in_the_source_format_is_a_byte_copy(env) -> None:
    src = env.dataset / "1_post" / "2.jpeg"

    body = _apply(env.client, src=str(src), crop={"x": 0, "y": 0, "width": 400, "height": 300}).json()

    assert body["copied"] is True
    assert Path(body["archived"]).read_bytes() == src.read_bytes()


# ---------------------------------------------------------------------------
# 4. display coordinates (EXIF orientation)
# ---------------------------------------------------------------------------


def test_image_meta_reports_the_display_size_the_dialog_measures_against(env) -> None:
    rotated = env.client.get(
        "/api/images/meta", params={"path": str(env.dataset / "1_post" / "3.jpg")}
    ).json()
    assert (rotated["width"], rotated["height"]) == (200, 100)
    assert (rotated["display_width"], rotated["display_height"]) == (100, 200)

    flat = env.client.get(
        "/api/images/meta", params={"path": str(env.dataset / "1_post" / "1.png")}
    ).json()
    assert (flat["display_width"], flat["display_height"]) == (flat["width"], flat["height"])


def test_the_crop_box_is_applied_to_the_rotated_image(env) -> None:
    src = env.dataset / "1_post" / "3.jpg"

    # 200x100 raw with orientation 6 is 100x200 as displayed; the crop is the bottom half of that.
    body = _apply(env.client, src=str(src), crop={"x": 0, "y": 100, "width": 100, "height": 100}).json()

    assert (body["orig_width"], body["orig_height"]) == (100, 200)
    with Image.open(src) as opened:
        reference = ImageOps.exif_transpose(opened).crop((0, 100, 100, 200))
    with Image.open(body["archived"]) as produced:
        assert produced.size == (100, 100)
        assert produced.convert("RGB").tobytes() == reference.convert("RGB").tobytes()


def test_resizing_a_rotated_image_keeps_the_displayed_aspect_ratio(env) -> None:
    src = env.dataset / "1_post" / "3.jpg"

    body = _apply(
        env.client,
        src=str(src),
        crop={"x": 0, "y": 0, "width": 100, "height": 200},
        scale={"target_width": TARGET[0], "target_height": TARGET[1], "no_upscale": False},
    ).json()

    width, height = body["output_width"], body["output_height"]
    # Portal crop of a landscape frame: the output must stay portrait, and its area must land on the target.
    assert height > width
    assert abs(width / height - 0.5) < 0.01
    assert abs(width * height - TARGET[0] * TARGET[1]) / (TARGET[0] * TARGET[1]) < 0.01


# ---------------------------------------------------------------------------
# 5. the scaling half works from the cropped size
# ---------------------------------------------------------------------------


def test_area_normalization_starts_from_the_cropped_size(env) -> None:
    src = env.dataset / "1_post" / "1.png"

    # 2000x1500 is 4:3 and its area is above the target, so the default "do not enlarge" does not fire.
    body = _apply(
        env.client,
        src=str(src),
        crop={"x": 0, "y": 0, "width": 2000, "height": 1500},
        scale={"target_width": 1536, "target_height": 1536},
    ).json()

    assert body["resampled"] is True
    assert body["upscale_skipped"] is False
    assert (body["output_width"], body["output_height"]) == (1774, 1330)
    assert abs(body["output_width"] * body["output_height"] - 1536 * 1536) / (1536 * 1536) < 0.01
    # The factor is the one area normalization gives for the *crop*, not for the 3000x2000 frame.
    assert body["scale"] == pytest.approx(0.8868, abs=1e-3)


def test_a_crop_box_whose_area_is_the_target_area_is_not_resampled(env) -> None:
    src = env.dataset / "1_post" / "1.png"

    body = _apply(
        env.client,
        src=str(src),
        crop={"x": 0, "y": 0, "width": 1881, "height": 1254},
        scale={"target_width": 1536, "target_height": 1536},
    ).json()

    assert body["scale"] == pytest.approx(1.0, abs=1e-6)
    assert body["resampled"] is False
    assert (body["output_width"], body["output_height"]) == (1881, 1254)


def test_no_upscale_keeps_a_small_crop_at_its_own_size(env) -> None:
    src = env.dataset / "1_post" / "1.png"

    body = _apply(
        env.client,
        src=str(src),
        crop={"x": 0, "y": 0, "width": 200, "height": 200},
        scale={"target_width": 1536, "target_height": 1536, "no_upscale": True},
    ).json()

    assert body["upscale_skipped"] is True
    assert (body["output_width"], body["output_height"]) == (200, 200)


# ---------------------------------------------------------------------------
# 6. the boundary
# ---------------------------------------------------------------------------


def test_a_source_outside_the_allowlist_is_403(env) -> None:
    src = env.outside / "6.png"
    response = _apply(env.client, src=str(src), crop={"width": 10, "height": 10})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "outside_roots"


def test_a_missing_source_is_404(env) -> None:
    response = _apply(
        env.client, src=str(env.dataset / "1_post" / "nope.png"), crop={"width": 10, "height": 10}
    )
    assert response.status_code == 404


def test_a_non_image_source_is_400(env) -> None:
    response = _apply(
        env.client, src=str(env.dataset / "1_post" / "1.txt"), crop={"width": 10, "height": 10}
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "unsupported_image"


def test_an_undecodable_image_is_400_and_writes_nothing(env) -> None:
    src = env.dataset / "1_post" / "5.png"
    response = _apply(env.client, src=str(src), crop={"width": 10, "height": 10})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "decode_failed"
    assert list(src.parent.glob("5_*")) == []


def test_an_unknown_format_or_extra_key_is_422(env) -> None:
    src = env.dataset / "1_post" / "1.png"
    assert _apply(
        env.client, src=str(src), crop={"width": 10, "height": 10}, format="tiff"
    ).status_code == 422
    assert _apply(
        env.client, src=str(src), crop={"width": 10, "height": 10, "nope": 1}
    ).status_code == 422
