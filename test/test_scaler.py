"""Unit tests for core/scaler.py: area-normalization math + single-image processing + atomic write (§4.14).

Criteria focus (only if they can fail do they count as criteria):
1. Scaling math: area normalization, unchanged aspect ratio, no_upscale clamping an enlargement back to the original size;
2. When no_upscale hits, **no re-encoding** (same format copies bytes directly) - JPEG is not degraded twice;
3. The caption is attached automatically and is **byte-for-byte identical**; a source image without a caption is not a failure;
4. Name collisions get a suffix and never overwrite; the relative directory structure is rebuilt;
5. Atomic write: neither the success nor the failure path leaves a .tmp-scale-* temp file.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from kohya_dataset_tagger.core import captions, scaler


def _png(path: Path, size=(100, 100), color=(10, 20, 30), mode="RGB") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new(mode, size, color).save(path, "PNG")
    return path


def _jpeg(path: Path, size=(200, 100), color=(200, 30, 30)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path, "JPEG", quality=90)
    return path


# ---------------------------------------------------------------------------
# 1. Scaling math
# ---------------------------------------------------------------------------


def test_downscale_preserves_aspect_ratio_and_area() -> None:
    dims = scaler.calculate_new_dimensions(3000, 2000, 1536, 1536)
    assert (dims.width, dims.height) == (1881, 1254)
    assert dims.upscale_skipped is False
    # Area normalization: the error between the new area and the target area comes only from rounding (< 0.1%).
    assert abs(dims.width * dims.height - 1536 * 1536) / (1536 * 1536) < 0.001
    # Aspect ratio preserved (within rounding error)
    assert abs((dims.width / dims.height) - 1.5) < 0.002


def test_exact_ratio_case_lands_on_target_area() -> None:
    dims = scaler.calculate_new_dimensions(4000, 1000, 1536, 1536)
    assert (dims.width, dims.height) == (3072, 768)
    assert dims.width * dims.height == 1536 * 1536


def test_upscale_is_allowed_when_no_upscale_is_off() -> None:
    dims = scaler.calculate_new_dimensions(1000, 1000, 1536, 1536, no_upscale=False)
    assert (dims.width, dims.height) == (1536, 1536)
    assert dims.scale > 1.0
    assert dims.upscale_skipped is False


def test_upscale_is_blocked_when_no_upscale_is_on() -> None:
    dims = scaler.calculate_new_dimensions(1000, 1000, 1536, 1536, no_upscale=True)
    assert (dims.width, dims.height) == (1000, 1000)
    assert dims.scale == 1.0
    assert dims.upscale_skipped is True


def test_landscape_smaller_by_area_is_not_upscaled_either() -> None:
    # A 2000x500 sliver has area (1e6) smaller than the target (2.36e6): by the area rule it is likewise not enlarged.
    dims = scaler.calculate_new_dimensions(2000, 500, 1536, 1536, no_upscale=True)
    assert (dims.width, dims.height) == (2000, 500)
    assert dims.upscale_skipped is True


@pytest.mark.parametrize("bad", [(0, 100), (100, 0), (-5, 10)])
def test_invalid_source_dimensions_raise(bad) -> None:
    with pytest.raises(scaler.ScaleError):
        scaler.calculate_new_dimensions(bad[0], bad[1], 1536, 1536)


def test_invalid_target_dimensions_raise() -> None:
    with pytest.raises(scaler.ScaleError):
        scaler.calculate_new_dimensions(100, 100, 0, 1536)


def test_two_pixel_separator_dimension_never_rounds_to_zero() -> None:
    dims = scaler.calculate_new_dimensions(10000, 1, 8, 8)
    assert dims.height == 1 and dims.width >= 1


# ---------------------------------------------------------------------------
# 2. Single-image processing
# ---------------------------------------------------------------------------


def test_no_upscale_copies_bytes_without_reencoding(tmp_path: Path) -> None:
    src = _jpeg(tmp_path / "src" / "small.jpg", size=(320, 240))
    before = src.read_bytes()
    outcome = scaler.process_one_image(
        src, tmp_path / "out", target_width=1536, target_height=1536, output_format="jpeg"
    )
    assert outcome.ok and outcome.dest is not None
    assert (outcome.new_width, outcome.new_height) == (320, 240)
    assert outcome.upscale_skipped is True and outcome.resized is False
    assert outcome.copied is True
    assert outcome.dest.read_bytes() == before  # byte-for-byte identical = no second encoding


def test_downscale_writes_expected_size_and_keeps_alpha_capable_format(tmp_path: Path) -> None:
    # Area 6.0e6 > target 2.36e6, so it definitely goes through resampling (no_upscale will not keep it).
    src = _png(tmp_path / "src" / "big.png", size=(3000, 2000))
    outcome = scaler.process_one_image(
        src, tmp_path / "out", target_width=1536, target_height=1536, output_format="png"
    )
    assert outcome.copied is False and outcome.resized is True
    assert outcome.upscale_skipped is False
    with Image.open(outcome.dest) as opened:
        assert opened.size == (outcome.new_width, outcome.new_height)
    assert (outcome.new_width, outcome.new_height) == (1881, 1254)
    assert abs(outcome.new_width * outcome.new_height - 1536 * 1536) / (1536 * 1536) < 0.001


def test_format_change_reencodes_even_without_resizing(tmp_path: Path) -> None:
    src = _png(tmp_path / "src" / "small.png", size=(64, 48))
    outcome = scaler.process_one_image(
        src, tmp_path / "out", target_width=1536, target_height=1536, output_format="jpeg"
    )
    assert outcome.copied is False
    assert outcome.dest.suffix == ".jpg"
    with Image.open(outcome.dest) as opened:
        assert opened.format == "JPEG" and opened.size == (64, 48)


def test_jpeg_output_drops_alpha(tmp_path: Path) -> None:
    src = _png(tmp_path / "src" / "alpha.png", size=(500, 500), color=(0, 255, 0, 128), mode="RGBA")
    outcome = scaler.process_one_image(
        src, tmp_path / "out", target_width=256, target_height=256, output_format="jpeg"
    )
    with Image.open(outcome.dest) as opened:
        assert opened.mode == "RGB"


def test_webp_output(tmp_path: Path) -> None:
    src = _png(tmp_path / "src" / "big.png", size=(1000, 1000))
    outcome = scaler.process_one_image(
        src, tmp_path / "out", target_width=512, target_height=512, output_format="webp"
    )
    assert outcome.dest.suffix == ".webp"
    with Image.open(outcome.dest) as opened:
        assert opened.format == "WEBP"


def test_caption_is_copied_byte_for_byte(tmp_path: Path) -> None:
    src = _png(tmp_path / "src" / "1.png")
    payload = "1girl, solo, 中文 tag\r\nsecond line\n"
    (tmp_path / "src" / "1.txt").write_bytes(payload.encode("utf-8"))
    outcome = scaler.process_one_image(
        src, tmp_path / "out", target_width=64, target_height=64, output_format="png"
    )
    assert outcome.caption is not None
    assert outcome.caption.read_bytes() == payload.encode("utf-8")
    assert outcome.caption.name == outcome.dest.stem + ".txt"


def test_missing_caption_is_not_a_failure(tmp_path: Path) -> None:
    src = _png(tmp_path / "src" / "naked.png")
    outcome = scaler.process_one_image(
        src, tmp_path / "out", target_width=64, target_height=64, output_format="png"
    )
    assert outcome.ok and outcome.caption is None


def test_caption_extension_matches_core_captions(tmp_path: Path) -> None:
    """The extension rule must match core/captions.py (otherwise the trainer cannot read the exported sidecar)."""
    image = tmp_path / "post" / "1.png"
    for ext in (".txt", "txt", ".caption"):
        assert scaler._caption_path(image, ext) == captions.caption_path(image, ext)
    src = _png(image)
    (tmp_path / "post" / "1.caption").write_text("tag", encoding="utf-8")
    outcome = scaler.process_one_image(
        src, tmp_path / "out", target_width=64, target_height=64, caption_extension="caption"
    )
    assert outcome.caption is not None and outcome.caption.name == "1.caption"


def test_collision_never_overwrites(tmp_path: Path) -> None:
    src = _png(tmp_path / "src" / "1.png")
    first = scaler.process_one_image(
        src, tmp_path / "out", target_width=64, target_height=64
    )
    second = scaler.process_one_image(
        src, tmp_path / "out", target_width=64, target_height=64
    )
    assert first.dest.name == "1.png"
    assert second.dest.name == "1_1.png"
    assert first.dest.read_bytes() == second.dest.read_bytes()


def test_destination_directory_rebuilds_relative_structure(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    assert scaler.destination_directory(root / "a" / "b" / "1.png", root, tmp_path / "out") == (
        tmp_path / "out" / "a" / "b"
    )
    assert scaler.destination_directory(root / "1.png", root, tmp_path / "out") == tmp_path / "out"


def test_source_outside_root_raises(tmp_path: Path) -> None:
    with pytest.raises(scaler.ScaleError):
        scaler.destination_directory(tmp_path / "elsewhere" / "1.png", tmp_path / "dataset", tmp_path / "out")


@pytest.mark.parametrize("kwargs", [
    {"output_format": "tiff"},
    {"resample": "magic"},
    {"quality": 0},
    {"quality": 101},
])
def test_invalid_options_raise(tmp_path: Path, kwargs: dict) -> None:
    src = _png(tmp_path / "src" / "1.png")
    base = {"target_width": 64, "target_height": 64}
    base.update(kwargs)
    with pytest.raises(scaler.ScaleError):
        scaler.process_one_image(src, tmp_path / "out", **base)


# ---------------------------------------------------------------------------
# 3. Atomic write: no temp files left behind
# ---------------------------------------------------------------------------


def _leftover_temps(directory: Path) -> list[str]:
    return [p.name for p in directory.rglob(".tmp-scale-*")]


def test_success_leaves_no_temp_files(tmp_path: Path) -> None:
    src = _png(tmp_path / "src" / "1.png", size=(300, 300))
    (tmp_path / "src" / "1.txt").write_text("tag", encoding="utf-8")
    scaler.process_one_image(src, tmp_path / "out", target_width=128, target_height=128)
    assert _leftover_temps(tmp_path) == []


def test_failure_leaves_no_temp_files_and_no_output(tmp_path: Path) -> None:
    bogus = tmp_path / "src" / "broken.png"
    bogus.parent.mkdir(parents=True, exist_ok=True)
    bogus.write_bytes(b"this is not an image")
    with pytest.raises(Exception):
        scaler.process_one_image(bogus, tmp_path / "out", target_width=64, target_height=64)
    assert _leftover_temps(tmp_path) == []
    assert list((tmp_path / "out").glob("*")) == []


# ---------------------------------------------------------------------------
# 6. Cropping (section 4.17)
# ---------------------------------------------------------------------------


def test_crop_rect_clamps_like_the_frontend_does() -> None:
    """The dialog draws the box over a 1600 px preview, so the server clamps instead of rejecting.

    The same two rows are asserted in test/fixtures/crop_harness.mjs: the frontend
    (web/js/cropbox.clampRect) and the backend must agree, or the preview and the applied crop
    would be different rectangles of one confirmation.
    """
    frame = (3000, 2000)
    assert scaler.CropRect(100, 50, 800, 600).clamped(*frame) == scaler.CropRect(100, 50, 800, 600)
    assert scaler.CropRect(2900, 1900, 800, 600).clamped(*frame) == scaler.CropRect(2200, 1400, 800, 600)
    assert scaler.CropRect(-10, -10, 5000, 4000).clamped(400, 300) == scaler.CropRect(0, 0, 400, 300)


def test_crop_rect_reports_what_it_covers() -> None:
    whole = scaler.CropRect(0, 0, 300, 200)
    assert whole.covers(300, 200) is True
    assert scaler.CropRect(0, 0, 300, 199).covers(300, 200) is False
    assert scaler.CropRect(-5, -5, 400, 300).covers(300, 200) is True
    assert whole.area == 60000


def test_display_size_honours_exif_orientation(tmp_path: Path) -> None:
    """A photo with orientation 6 is 100x200 as displayed, and that is the frame a crop box means."""
    rotated = tmp_path / "r.jpg"
    image = Image.new("RGB", (200, 100), (5, 5, 5))
    exif = image.getexif()
    exif[0x0112] = 6
    image.save(rotated, "JPEG", quality=90, exif=exif)

    with Image.open(rotated) as opened:
        assert opened.size == (200, 100), "the raw header is landscape"
        assert scaler.display_size(opened) == (100, 200), "the displayed size is portrait"

    flat = _png(tmp_path / "flat.png", size=(200, 100))
    with Image.open(flat) as opened:
        assert scaler.display_size(opened) == (200, 100)


def test_the_archive_is_always_numbered_and_never_reuses_the_bare_name(tmp_path: Path) -> None:
    """Section 4.17's naming rule, deliberately not the export's: the bare name is never tried."""
    _png(tmp_path / "a.png", size=(10, 10))
    assert scaler.numbered_destination(tmp_path, "a", ".png").name == "a_1.png"
    # The export rule still prefers the bare name when it is free.
    assert scaler.unique_destination(tmp_path, "b", ".png").name == "b.png"

    _png(tmp_path / "a_1.png", size=(10, 10))
    _png(tmp_path / "a_2.png", size=(10, 10))
    assert scaler.numbered_destination(tmp_path, "a", ".png").name == "a_3.png"
    # Another extension does not collide with the source, and is numbered all the same.
    assert scaler.numbered_destination(tmp_path, "a", ".webp").name == "a_1.webp"
    # Two allocations in one batch must not hand out one name twice.
    reserved: set[str] = set()
    first = scaler.numbered_destination(tmp_path, "c", ".png", reserved)
    second = scaler.numbered_destination(tmp_path, "c", ".png", reserved)
    assert (first.name, second.name) == ("c_1.png", "c_2.png")


def test_a_crop_is_written_at_its_own_size_and_gets_no_caption(tmp_path: Path) -> None:
    """attach_caption=False (the archive) + resize=False (crop only): the pixels are the crop's."""
    source = _png(tmp_path / "src" / "1.png", size=(1000, 800))
    (source.parent / "1.txt").write_bytes("1girl".encode("utf-8"))

    outcome = scaler.process_one_image(
        source,
        source.parent,
        target_width=1,
        target_height=1,
        crop=scaler.CropRect(100, 100, 300, 200),
        resize=False,
        attach_caption=False,
        numbered=True,
    )

    assert outcome.dest.name == "1_1.png"
    assert (outcome.new_width, outcome.new_height) == (300, 200)
    assert outcome.scale == 1.0 and outcome.resized is False and outcome.copied is False
    assert outcome.caption is None and not (source.parent / "1_1.txt").exists()
    with Image.open(outcome.dest) as produced, Image.open(source) as opened:
        assert produced.tobytes() == opened.crop((100, 100, 400, 300)).tobytes()
    # The source is untouched, and the atomic write left nothing behind.
    with Image.open(source) as opened:
        assert opened.size == (1000, 800)
    assert list(source.parent.glob(".tmp-scale-*")) == []


def test_a_crop_covering_the_whole_frame_is_still_a_plain_copy(tmp_path: Path) -> None:
    """No pixel is dropped, so the no-re-encode shortcut still holds - which is what makes
    "confirm without changing anything" cheap instead of a JPEG generation."""
    source = _jpeg(tmp_path / "2.jpeg", size=(400, 300))

    outcome = scaler.process_one_image(
        source,
        source.parent,
        target_width=1,
        target_height=1,
        output_format="jpeg",
        crop=scaler.CropRect(0, 0, 400, 300),
        resize=False,
        attach_caption=False,
        numbered=True,
    )

    assert outcome.copied is True
    assert outcome.dest.read_bytes() == source.read_bytes()

