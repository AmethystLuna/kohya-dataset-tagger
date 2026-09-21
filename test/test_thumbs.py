"""Unit tests for core/thumbs.py: cache key, JPEG draft order, alpha preservation, cache hit, cleanup.

All writes happen in pytest's tmp_path; in this file the real dataset appears only as the unmodified
control group (A9's 'the cache directory is not inside the dataset directory'), and its contents are not read.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from PIL import Image, ImageOps

from kohya_dataset_tagger.core import paths, thumbs


@pytest.fixture(autouse=True)
def _neutral_roots():
    paths.configure([])
    yield
    paths.configure([])


def _jpeg(path: Path, size=(64, 48), color=(200, 30, 30), quality=70) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path, "JPEG", quality=quality)
    return path


def _png_rgba(path: Path, size=(32, 32)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    im = Image.new("RGBA", size, (255, 0, 0, 0))
    im.putpixel((0, 0), (0, 255, 0, 255))
    im.save(path, "PNG")
    return path


def _png_rgb(path: Path, size=(32, 32)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (10, 120, 240)).save(path, "PNG")
    return path


def _exif_orientation(value: int) -> Image.Exif:
    exif = Image.Exif()
    exif[274] = value
    return exif


def _snapshot(root: Path) -> list:
    """A sorted list of (relative path, size, mtime_ns): used to prove a tree was not touched."""
    out = []
    for path in sorted(root.rglob("*")):
        st = path.stat()
        out.append((path.relative_to(root).as_posix(), st.st_size, st.st_mtime_ns))
    return out


def _is_under(candidate: Path, root: Path) -> bool:
    target = os.path.normcase(str(candidate.resolve()))
    base = os.path.normcase(str(root.resolve()))
    try:
        return os.path.commonpath([target, base]) == base
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# cache_key (spec §3.4)
# ---------------------------------------------------------------------------


def test_cache_key_is_16_hex_and_deterministic(tmp_path: Path) -> None:
    src = _jpeg(tmp_path / "1.jpeg")
    key = thumbs.cache_key(src, thumbs.GRID_THUMB)
    assert key == thumbs.cache_key(src, thumbs.GRID_THUMB)
    assert len(key) == 16
    assert all(c in "0123456789abcdef" for c in key)


def test_cache_key_changes_with_target(tmp_path: Path) -> None:
    src = _jpeg(tmp_path / "1.jpeg")
    assert thumbs.cache_key(src, thumbs.GRID_THUMB) != thumbs.cache_key(src, thumbs.PREVIEW_THUMB)


def test_cache_key_changes_with_mtime_and_size(tmp_path: Path) -> None:
    src = _jpeg(tmp_path / "1.jpeg")
    original = thumbs.cache_key(src, thumbs.GRID_THUMB)
    st = src.stat()
    os.utime(src, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))
    assert thumbs.cache_key(src, thumbs.GRID_THUMB) != original
    _jpeg(src, size=(128, 96))
    assert thumbs.cache_key(src, thumbs.GRID_THUMB) != original


def test_cache_key_uses_absolute_path_not_just_the_filename(tmp_path: Path) -> None:
    """In the dataset 1.jpeg exists in 219 directories, so the cache key must tell them apart (spec §1.3)."""
    first = _jpeg(tmp_path / "1_post" / "1.jpeg")
    second = _jpeg(tmp_path / "2_post" / "1.jpeg")
    assert first.name == second.name
    assert thumbs.cache_key(first, thumbs.GRID_THUMB) != thumbs.cache_key(second, thumbs.GRID_THUMB)


def test_cache_key_requires_the_file_to_exist(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        thumbs.cache_key(tmp_path / "nope.jpeg", thumbs.GRID_THUMB)


# ---------------------------------------------------------------------------
# ensure_thumb
# ---------------------------------------------------------------------------


def test_ensure_thumb_writes_a_webp_that_fits_the_target(tmp_path: Path) -> None:
    src = _jpeg(tmp_path / "1.jpeg", size=(2000, 1500))
    cache = tmp_path / "cache"
    out = thumbs.ensure_thumb(src, thumbs.GRID_THUMB, cache)
    assert out.is_file()
    assert out.parent == cache
    assert out.suffix == ".webp"
    assert out.read_bytes()[:4] == b"RIFF" and out.read_bytes()[8:12] == b"WEBP"
    with Image.open(out) as im:
        assert max(im.size) == thumbs.GRID_THUMB
        assert im.size == (320, 240)
        assert im.format == "WEBP"


def test_second_call_hits_the_cache_and_keeps_mtime(tmp_path: Path, monkeypatch) -> None:
    """The A9 criterion: the second call hits the cache - evidenced by the cache file's mtime staying unchanged and no further decode."""
    src = _jpeg(tmp_path / "1.jpeg", size=(800, 600))
    cache = tmp_path / "cache"
    calls = {"n": 0}
    real_render = thumbs._render_webp

    def counting_render(image, target):
        calls["n"] += 1
        return real_render(image, target)

    monkeypatch.setattr(thumbs, "_render_webp", counting_render)

    first = thumbs.ensure_thumb(src, thumbs.GRID_THUMB, cache)
    mtime_before = first.stat().st_mtime_ns
    second = thumbs.ensure_thumb(src, thumbs.GRID_THUMB, cache)
    assert second == first
    assert second.stat().st_mtime_ns == mtime_before
    assert calls["n"] == 1


def test_draft_shrinks_the_jpeg_before_exif_transpose(tmp_path: Path, monkeypatch) -> None:
    """The order must be draft -> exif_transpose -> thumbnail.

    A proxy spy records the size exif_transpose receives: if draft did not take effect, what shows up here is 2000x1500.
    """
    src = _jpeg(tmp_path / "big.jpg", size=(2000, 1500))
    seen: dict = {}
    real_transpose = ImageOps.exif_transpose

    def spy(im, *args, **kwargs):
        seen["size"] = im.size
        return real_transpose(im, *args, **kwargs)

    monkeypatch.setattr(thumbs.ImageOps, "exif_transpose", spy)
    thumbs.ensure_thumb(src, thumbs.GRID_THUMB, tmp_path / "cache")
    assert seen["size"] == (500, 375)  # libjpeg picks the 1/4 DCT scale (1/8 would be smaller than the target)


def test_exif_orientation_is_applied(tmp_path: Path) -> None:
    src = tmp_path / "oriented.jpg"
    Image.new("RGB", (400, 200), (10, 200, 10)).save(src, "JPEG", exif=_exif_orientation(6))
    out = thumbs.ensure_thumb(src, thumbs.GRID_THUMB, tmp_path / "cache")
    with Image.open(out) as im:
        assert im.size == (160, 320)  # without the orientation fix it would be (320, 160)


def test_alpha_is_preserved(tmp_path: Path) -> None:
    src = _png_rgba(tmp_path / "1.png", size=(64, 64))
    out = thumbs.ensure_thumb(src, thumbs.GRID_THUMB, tmp_path / "cache")
    with Image.open(out) as im:
        assert im.mode == "RGBA"
        assert im.getchannel("A").getextrema() == (0, 255)
        assert im.getpixel((1, 1))[3] == 0  # a transparent pixel is still transparent
        assert im.getpixel((0, 0))[3] == 255  # an opaque pixel was not wiped out either


def test_palette_png_with_transparency_keeps_alpha(tmp_path: Path) -> None:
    """PNG-8 expresses transparency through palette indices: it must really be converted to RGBA, not handled as opaque."""
    src = tmp_path / "pal.png"
    im = Image.new("P", (16, 16), 1)  # 1 = opaque red
    im.putpalette([0, 0, 0, 255, 0, 0] + [0, 0, 0] * 254)
    im.putpixel((3, 3), 0)  # 0 = the transparent index, and at least one pixel must really use it
    im.save(src, "PNG", transparency=0)
    out = thumbs.ensure_thumb(src, thumbs.GRID_THUMB, tmp_path / "cache")
    with Image.open(out) as back:
        assert back.mode == "RGBA"
        assert back.getchannel("A").getextrema()[0] == 0


def test_opaque_jpeg_stays_opaque(tmp_path: Path) -> None:
    src = _jpeg(tmp_path / "1.jpeg")
    out = thumbs.ensure_thumb(src, thumbs.GRID_THUMB, tmp_path / "cache")
    with Image.open(out) as im:
        assert im.mode == "RGB"


def test_preview_and_grid_use_separate_cache_entries(tmp_path: Path) -> None:
    src = _jpeg(tmp_path / "1.jpeg", size=(640, 480))
    cache = tmp_path / "cache"
    grid = thumbs.ensure_thumb(src, thumbs.GRID_THUMB, cache)
    preview = thumbs.ensure_thumb(src, thumbs.PREVIEW_THUMB, cache)
    assert grid != preview
    assert grid.is_file() and preview.is_file()


def test_6000x7000_image_gets_a_320px_grid_thumb(tmp_path: Path) -> None:
    """First half of A9: the dataset's largest image (6000x7000, about 31 MB) yields a 320px thumbnail."""
    src = _jpeg(tmp_path / "big.jpg", size=(6000, 7000), quality=40)
    out = thumbs.ensure_thumb(src, thumbs.GRID_THUMB, tmp_path / "cache")
    with Image.open(out) as im:
        assert max(im.size) == thumbs.GRID_THUMB
        assert im.size == (274, 320)
    assert out.stat().st_size < src.stat().st_size


def test_target_must_be_positive(tmp_path: Path) -> None:
    src = _jpeg(tmp_path / "1.jpeg")
    with pytest.raises(ValueError):
        thumbs.ensure_thumb(src, 0, tmp_path / "cache")


def test_corrupt_image_raises(tmp_path: Path) -> None:
    broken = tmp_path / "broken.jpeg"
    broken.write_bytes(b"not an image at all")
    with pytest.raises(OSError):
        thumbs.ensure_thumb(broken, thumbs.GRID_THUMB, tmp_path / "cache")


# ---------------------------------------------------------------------------
# The cache directory must be outside the dataset directory (spec §0.3 / §3.4)
# ---------------------------------------------------------------------------


def test_cache_dir_inside_the_dataset_root_is_rejected(tmp_path: Path) -> None:
    ds = tmp_path / "dataset"
    src = _jpeg(ds / "1_post" / "1.jpeg")
    paths.configure([ds])
    with pytest.raises(thumbs.CacheDirInsideRoots):
        thumbs.ensure_thumb(src, thumbs.GRID_THUMB, ds / ".kohya-dataset-tagger-thumbs")
    with pytest.raises(thumbs.CacheDirInsideRoots):
        thumbs.ensure_thumb(src, thumbs.GRID_THUMB, ds)


def test_thumbnailing_never_writes_into_the_dataset_dir(tmp_path: Path) -> None:
    ds = tmp_path / "dataset"
    _jpeg(ds / "1_post" / "1.jpeg")
    _png_rgba(ds / "1_post" / "2.png")
    paths.configure([ds])
    cache = tmp_path / "thumbs"
    before = _snapshot(ds)
    for name in ("1.jpeg", "2.png"):
        thumbs.ensure_thumb(ds / "1_post" / name, thumbs.GRID_THUMB, cache)
    assert _snapshot(ds) == before
    assert cache.is_dir()
    assert not _is_under(cache, ds)


# ---------------------------------------------------------------------------
# clear_cache
# ---------------------------------------------------------------------------


def test_clear_cache_removes_its_own_files_only(tmp_path: Path) -> None:
    src = _jpeg(tmp_path / "1.jpeg")
    cache = tmp_path / "cache"
    thumbs.ensure_thumb(src, thumbs.GRID_THUMB, cache)
    thumbs.ensure_thumb(src, thumbs.PREVIEW_THUMB, cache)
    foreign = cache / "keep-me.webp"
    foreign.write_bytes(b"x")
    stale_tmp = cache / (".%s.1.2.tmp" % ("a" * 16))
    stale_tmp.write_bytes(b"x")

    removed = thumbs.clear_cache(cache)
    assert removed == 3  # two thumbnails + one leftover temp file
    assert foreign.is_file()
    assert cache.is_dir()
    assert thumbs.clear_cache(cache) == 0


def test_clear_cache_on_missing_dir_returns_zero(tmp_path: Path) -> None:
    assert thumbs.clear_cache(tmp_path / "nope") == 0


def test_clear_cache_refuses_to_run_inside_the_dataset_root(tmp_path: Path) -> None:
    """Pointing it at the dataset directory must be a refusal, not a wipe - that is this function's safety catch."""
    ds = tmp_path / "dataset"
    _jpeg(ds / "1_post" / "1.jpeg")
    paths.configure([ds])
    before = _snapshot(ds)
    with pytest.raises(thumbs.CacheDirInsideRoots):
        thumbs.clear_cache(ds)
    with pytest.raises(thumbs.CacheDirInsideRoots):
        thumbs.clear_cache(ds / "1_post")
    assert _snapshot(ds) == before
