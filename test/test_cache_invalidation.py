"""Unit tests for `core/cache_invalidation.py`.

Covers p0-spec's A5 criteria (the three cache-invalidation paths) + §3.3's path rules.

**Everything builds its dataset in `tmp_path`**: the dataset configured in local_paths.ini is a
read-only test set, and no test may create/modify/delete anything in it (p0-spec §0).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from kohya_dataset_tagger.core import cache_invalidation as ci


# --------------------------------------------------------------------------
# Build samples: one image + the trainer cache beside it
# --------------------------------------------------------------------------


def make_image(directory: Path, name: str = "1.jpeg") -> Path:
    """Create a fake image file (only path rules are tested here, so it need not really decode)."""
    directory.mkdir(parents=True, exist_ok=True)
    image = directory / name
    image.write_bytes(b"\xff\xd8 not a real jpeg")
    return image


def make_te_cache(image: Path, suffix: str = "_anima_te.safetensors") -> Path:
    cache_dir = image.parent / ci.TE_CACHE_DIRNAME
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / (image.stem + suffix)
    path.write_bytes(b"cached-te")
    return path


def make_latent_cache(image: Path, size: str = "1536x1536", suffix: str = ".npz") -> Path:
    cache_dir = image.parent / ci.LATENT_CACHE_DIRNAME
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / ("%s_%s%s" % (image.stem, size, suffix))
    path.write_bytes(b"cached-latent")
    return path


def set_mtime(path: Path, seconds: int) -> None:
    """Set mtime explicitly, avoiding the fragile assumption that 'a file just written is newer than another'."""
    os.utime(path, ns=(seconds * 10**9, seconds * 10**9))


# --------------------------------------------------------------------------
# Path rules (copied from library/strategy_anima.py:284-293, 299-300)
# --------------------------------------------------------------------------


def test_text_encoder_cache_paths_follows_trainer_rule(tmp_path: Path) -> None:
    image = make_image(tmp_path, "1.jpeg")

    paths = ci.text_encoder_cache_paths(image)

    cache_dir = tmp_path / "cache_text_encoder"
    assert paths == [cache_dir / "1_anima_te.safetensors", cache_dir / "1_anima_te.npz"]
    assert ci.TE_CACHE_DIRNAME == "cache_text_encoder"


def test_text_encoder_cache_paths_uses_stem_not_name(tmp_path: Path) -> None:
    """`a.b.jpeg`'s stem is `a.b`, matching the trainer's `splitext` algorithm."""
    image = make_image(tmp_path, "a.b.jpeg")

    assert ci.text_encoder_cache_paths(image)[0].name == "a.b_anima_te.safetensors"


def test_text_encoder_cache_paths_are_per_directory(tmp_path: Path) -> None:
    """`1.jpeg` exists in 219 directories, so the cache must land in each directory (p0-spec §1.3)."""
    first = make_image(tmp_path / "a", "1.jpeg")
    second = make_image(tmp_path / "b", "1.jpeg")

    assert ci.text_encoder_cache_paths(first)[0] != ci.text_encoder_cache_paths(second)[0]
    assert ci.text_encoder_cache_paths(first)[0].parent == tmp_path / "a" / "cache_text_encoder"


# --------------------------------------------------------------------------
# A5-1 success: delete the cache
# --------------------------------------------------------------------------


def test_invalidate_removes_both_variants(tmp_path: Path) -> None:
    image = make_image(tmp_path)
    safetensors = make_te_cache(image, "_anima_te.safetensors")
    npz = make_te_cache(image, "_anima_te.npz")

    result = ci.invalidate_text_encoder_cache(image)

    assert result.removed == (safetensors, npz)
    assert result.failed == ()
    assert not safetensors.exists()
    assert not npz.exists()


def test_invalidate_keeps_other_images_and_dirs(tmp_path: Path) -> None:
    image = make_image(tmp_path, "1.jpeg")
    mine = make_te_cache(image)
    other = make_te_cache(make_image(tmp_path, "2.jpeg"))
    latent = make_latent_cache(image)

    result = ci.invalidate_text_encoder_cache(image)

    assert result.removed == (mine,)
    assert other.is_file(), "another image's cache must not be touched"
    assert latent.is_file(), "the latent cache is unrelated to the caption and must not be touched"


def test_invalidate_without_cache_is_not_a_failure(tmp_path: Path) -> None:
    """No cache file is not a failure - otherwise the first caption write would error."""
    image = make_image(tmp_path)

    result = ci.invalidate_text_encoder_cache(image)

    assert result == ci.InvalidationResult(removed=(), failed=())


def test_invalidate_is_idempotent(tmp_path: Path) -> None:
    image = make_image(tmp_path)
    make_te_cache(image)

    first = ci.invalidate_text_encoder_cache(image)
    second = ci.invalidate_text_encoder_cache(image)

    assert len(first.removed) == 1
    assert second == ci.InvalidationResult(removed=(), failed=())


# --------------------------------------------------------------------------
# A5-2 failure: a lock must not be swallowed
# --------------------------------------------------------------------------


@pytest.mark.skipif(os.name != "nt", reason="POSIX permits deleting an open file, so a handle cannot construct a deletion failure")
def test_invalidate_reports_locked_file_as_failed(tmp_path: Path) -> None:
    image = make_image(tmp_path)
    locked = make_te_cache(image, "_anima_te.safetensors")
    free = make_te_cache(image, "_anima_te.npz")

    with open(locked, "rb") as handle:
        assert handle.read() == b"cached-te"
        result = ci.invalidate_text_encoder_cache(image)

        assert result.failed == (locked,), "a deletion failure must be reported as-is, never swallowed"
        assert result.removed == (free,), "a variant that is not locked must still be deleted"
        assert locked.is_file()

    # Once the handle is closed a retry must delete it - "retry after failure" is a usable recovery path
    retry = ci.invalidate_text_encoder_cache(image)
    assert retry == ci.InvalidationResult(removed=(locked,), failed=())
    assert not locked.exists()


# --------------------------------------------------------------------------
# A5-3 recovery / status panel: find_stale_encoder_caches
# --------------------------------------------------------------------------


def test_find_stale_flags_caption_newer_than_cache(tmp_path: Path) -> None:
    image = make_image(tmp_path)
    caption = tmp_path / "1.txt"
    caption.write_text("1girl", encoding="utf-8")
    cache = make_te_cache(image)
    set_mtime(cache, 1_000)
    set_mtime(caption, 2_000)

    stale = ci.find_stale_encoder_caches(tmp_path)

    assert stale == [ci.StaleEntry(image=image, caption=caption, cache=cache)]


def test_find_stale_ignores_cache_newer_or_equal(tmp_path: Path) -> None:
    """The criterion is **strictly later**: a cache newer than the caption (or with the same timestamp) is not stale."""
    image = make_image(tmp_path)
    caption = tmp_path / "1.txt"
    caption.write_text("1girl", encoding="utf-8")
    cache = make_te_cache(image)
    set_mtime(caption, 1_000)
    set_mtime(cache, 2_000)
    assert ci.find_stale_encoder_caches(tmp_path) == []

    set_mtime(cache, 1_000)
    assert ci.find_stale_encoder_caches(tmp_path) == []


def test_find_stale_is_empty_after_cache_dir_removed(tmp_path: Path) -> None:
    """A5-3 recovery path: after deleting the whole `cache_text_encoder/` nothing is reported stale any more."""
    for name in ("1.jpeg", "2.jpeg", "3.jpeg"):
        image = make_image(tmp_path, name)
        (tmp_path / (Path(name).stem + ".txt")).write_text("1girl", encoding="utf-8")
        set_mtime(make_te_cache(image), 1_000)
        set_mtime(tmp_path / (Path(name).stem + ".txt"), 2_000)
    assert len(ci.find_stale_encoder_caches(tmp_path)) == 3

    import shutil

    shutil.rmtree(tmp_path / ci.TE_CACHE_DIRNAME)

    assert ci.find_stale_encoder_caches(tmp_path) == []
    assert not (tmp_path / ci.TE_CACHE_DIRNAME).exists()


def test_find_stale_ignores_images_without_caption(tmp_path: Path) -> None:
    image = make_image(tmp_path)
    make_te_cache(image)
    assert ci.find_stale_encoder_caches(tmp_path) == []


def test_find_stale_reports_each_variant(tmp_path: Path) -> None:
    image = make_image(tmp_path)
    caption = tmp_path / "1.txt"
    caption.write_text("1girl", encoding="utf-8")
    safetensors = make_te_cache(image, "_anima_te.safetensors")
    npz = make_te_cache(image, "_anima_te.npz")
    for cache in (safetensors, npz):
        set_mtime(cache, 1_000)
    set_mtime(caption, 2_000)

    stale = ci.find_stale_encoder_caches(tmp_path)

    assert [entry.cache for entry in stale] == sorted([safetensors, npz])


def test_find_stale_skips_cache_and_hidden_dirs_when_recursive(tmp_path: Path) -> None:
    """Recursion must not walk into `cache_text_encoder/`, `latent_cache/`, or hidden directories."""
    post = tmp_path / "1_sample_alpha"
    post.mkdir()
    image = make_image(post, "1.jpeg")
    caption = post / "1.txt"
    caption.write_text("1girl", encoding="utf-8")
    cache = make_te_cache(image)
    set_mtime(cache, 1_000)
    set_mtime(caption, 2_000)

    hidden = tmp_path / ".trash"
    hidden.mkdir()
    hidden_image = make_image(hidden, "9.jpeg")
    hidden_caption = hidden / "9.txt"
    hidden_caption.write_text("1girl", encoding="utf-8")
    set_mtime(make_te_cache(hidden_image), 1_000)
    set_mtime(hidden_caption, 2_000)

    assert ci.find_stale_encoder_caches(tmp_path) == [], "subdirectories do not count when not recursing"
    assert [entry.image for entry in ci.find_stale_encoder_caches(tmp_path, recursive=True)] == [image]


def test_find_stale_only_looks_at_images(tmp_path: Path) -> None:
    """When `.npz`/`.safetensors` are scattered in image_dir they must not be treated as images (dataset-contract)."""
    (tmp_path / "stray_1536x1536.npz").write_bytes(b"x")
    (tmp_path / "stray.txt").write_text("1girl", encoding="utf-8")
    assert ci.find_stale_encoder_caches(tmp_path) == []


def test_find_stale_of_missing_dir_is_empty(tmp_path: Path) -> None:
    assert ci.find_stale_encoder_caches(tmp_path / "nope") == []


# --------------------------------------------------------------------------
# latent cache: filenames carry the resolution and must be discovered by wildcard
# --------------------------------------------------------------------------


def test_latent_cache_paths_finds_all_resolutions(tmp_path: Path) -> None:
    image = make_image(tmp_path, "1.jpeg")
    small = make_latent_cache(image, "1024x1024", ".npz")
    large = make_latent_cache(image, "1536x1536", ".npz")
    anima = make_latent_cache(image, "1536x1536", "_anima.safetensors")
    other = make_latent_cache(make_image(tmp_path, "2.jpeg"), "1536x1536", ".npz")
    (tmp_path / ci.LATENT_CACHE_DIRNAME / "1_notes.txt").write_text("x", encoding="utf-8")

    assert ci.latent_cache_paths(image) == sorted([small, large, anima])
    assert other not in ci.latent_cache_paths(image)


def test_latent_cache_paths_without_dir_is_empty(tmp_path: Path) -> None:
    assert ci.latent_cache_paths(make_image(tmp_path)) == []


def test_invalidate_latent_cache_removes_all_resolutions(tmp_path: Path) -> None:
    image = make_image(tmp_path)
    paths = [
        make_latent_cache(image, "1024x1024", ".npz"),
        make_latent_cache(image, "1536x1536", ".npz"),
        make_latent_cache(image, "1536x1536", "_anima.safetensors"),
    ]
    keep = make_latent_cache(make_image(tmp_path, "2.jpeg"))

    result = ci.invalidate_latent_cache(image)

    assert sorted(result.removed) == sorted(paths)
    assert result.failed == ()
    assert keep.is_file()


@pytest.mark.skipif(os.name != "nt", reason="POSIX permits deleting an open file")
def test_invalidate_latent_cache_reports_locked_file(tmp_path: Path) -> None:
    image = make_image(tmp_path)
    locked = make_latent_cache(image)
    with open(locked, "rb"):
        result = ci.invalidate_latent_cache(image)
    assert result.failed == (locked,)
    assert result.removed == ()


# --------------------------------------------------------------------------
# Read-only boundary: this module's write path touches only the cache directory
# --------------------------------------------------------------------------


def test_module_constants_match_dataset_contract() -> None:
    assert ci.TE_CACHE_DIRNAME == "cache_text_encoder"
    assert ci.LATENT_CACHE_DIRNAME == "latent_cache"
    assert ci.TE_CACHE_SUFFIXES == ("_anima_te.safetensors", "_anima_te.npz")


def test_duplicated_constants_stay_in_sync_with_paths() -> None:
    """§3.0 forbids core modules importing each other, and the price is duplicated constants - the duplication must be pinned (the same as acceptance A16).

    The image extension allowlist must **include uppercase** (spec §3.1), so this compares the whole set, not a lowercase subset.
    """
    from kohya_dataset_tagger.core import paths

    assert ci.IMAGE_EXTS == paths.IMAGE_EXTS, "IMAGE_EXTS drifted between the two places"
    assert ci.CAPTION_EXT_DEFAULT == paths.CAPTION_EXT_DEFAULT, "the caption extension drifted"
    # The allowlist has "lowercase + all-uppercase" forms (spec §3.1 "include uppercase"); arbitrary mixed case is covered by lower() at comparison time.
    assert {".PNG", ".JPG", ".JPEG", ".WEBP", ".BMP"} <= ci.IMAGE_EXTS


def test_mixed_case_image_extension_is_found(tmp_path: Path) -> None:
    """A mixed-case image name (`1.JpEg`) must also be scanned - lower() before comparing."""
    image = make_image(tmp_path, "1.JpEg")
    caption = tmp_path / "1.txt"
    caption.write_text("1girl", encoding="utf-8")
    cache = make_te_cache(image)
    set_mtime(cache, 1_000)
    set_mtime(caption, 2_000)

    stale = ci.find_stale_encoder_caches(tmp_path)

    assert [entry.image for entry in stale] == [image]
