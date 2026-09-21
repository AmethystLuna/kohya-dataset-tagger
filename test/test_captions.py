"""Unit tests for `core/captions.py`: contract §3.2, the A6 round trip, A7 atomic write, invariant number one.

**Everything builds its dataset in `tmp_path`**: the dataset configured in `local_paths.ini` is a
read-only test set, and no test may create/modify/delete anything in it (p0-spec §0).
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from kohya_dataset_tagger.core import cache_invalidation as ci
import local_paths
from kohya_dataset_tagger.core import captions

#: A single backslash. Uses chr(92) rather than a literal so the escaping does not become hard to count in the source.
BS = "\\"

#: The **synthetic** sample of the defect (byte-for-byte identical to REAL_DEFECT_LINE in acceptance/test_p0_acceptance.py):
#: the generator wrote one extra backslash between the character tag and the next tag. The trainer splits on bare commas,
#: so it reads a tag with a **trailing backslash**; the editor must display the same thing and must not "helpfully fix" it.
#: The file that produced all this (one subset directory, 6 .txt files) is byte for byte `fugue \(honkai: star rail)\,` -
#: there is **no** backslash before its `)`, only the immediately following 0x5c 0x2c; the synthetic sample below writes `\)\,`.
#: The defect shape (one extra backslash at the tag's end) matches, but the bytes are not fully identical - the real file is
#: asserted by `test_real_dataset_file_splits_like_the_trainer` against the actual bytes, not by the constant here.
REAL_DEFECT_LINE = (
    "1girl, fugue " + BS + "(honkai: star rail" + BS + ")" + BS + ", hair ornament, tongue out"
)
REAL_DEFECT_TAG = "fugue " + BS + "(honkai: star rail" + BS + ")" + BS

#: One caption of the configured dataset, opened read-only. **Its content does not matter**: the case
#: below asserts only invariants that hold for any caption (see its docstring). What used to stand here
#: was one specific file of the author's own dataset - unnecessary for the criterion, and not publishable.
def _first_caption() -> Path:
    root = local_paths.dataset_root()
    for sub in sorted(root.glob("*")):
        for caption in sorted(sub.glob("*.txt")):
            return caption
    return root / "no-caption.txt"


REAL_CAPTION = _first_caption()


def trainer_split(text: str) -> list[str]:
    """The trainer's split rule: `[t.strip() for t in caption.split(",")]` from train_util.py:900."""
    return [t.strip() for t in text.strip().split(",") if t.strip()]


# --------------------------------------------------------------------------
# Build samples
# --------------------------------------------------------------------------


def make_image(directory: Path, name: str = "1.jpeg") -> Path:
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


def entries(directory: Path) -> set[str]:
    return {path.name for path in directory.iterdir()}


# --------------------------------------------------------------------------
# Paths and reading
# --------------------------------------------------------------------------


def test_caption_path_default_is_sibling_txt(tmp_path: Path) -> None:
    image = make_image(tmp_path, "1.jpeg")

    assert captions.caption_path(image) == tmp_path / "1.txt"
    assert captions.CAPTION_EXT_DEFAULT == ".txt"
    assert captions.SEPARATOR == ", "


def test_caption_path_keeps_dots_in_stem(tmp_path: Path) -> None:
    """`a.b.jpeg` -> `a.b.txt` (matching the trainer's `splitext`)."""
    image = make_image(tmp_path, "a.b.jpeg")

    assert captions.caption_path(image) == tmp_path / "a.b.txt"


def test_caption_path_custom_ext(tmp_path: Path) -> None:
    image = make_image(tmp_path, "1.png")

    assert captions.caption_path(image, ".caption") == tmp_path / "1.caption"
    assert captions.caption_path(image, "caption") == tmp_path / "1.caption"


def test_read_caption_missing_file_is_empty_not_an_error(tmp_path: Path) -> None:
    image = make_image(tmp_path)

    assert captions.read_caption(image) == ("", False)
    assert captions.read_caption(image, ".caption") == ("", False)


def test_read_caption_tolerates_bom_and_crlf(tmp_path: Path) -> None:
    image = make_image(tmp_path)
    (tmp_path / "1.txt").write_bytes("\ufeff1girl, solo\r\n".encode("utf-8"))

    text, multiline = captions.read_caption(image)

    assert text == "1girl, solo\n"
    assert multiline is False
    assert captions.split_tags(text) == ["1girl", "solo"]


def test_read_caption_flags_multiline(tmp_path: Path) -> None:
    """Only the first line takes part in training (train_util.py:881); line 2 onward must warn."""
    image = make_image(tmp_path)
    (tmp_path / "1.txt").write_text("1girl, solo\nsecond line\n", encoding="utf-8")

    text, multiline = captions.read_caption(image)

    assert multiline is True
    assert text == "1girl, solo\nsecond line\n"


def test_single_line_with_trailing_newline_is_not_multiline(tmp_path: Path) -> None:
    image = make_image(tmp_path)
    (tmp_path / "1.txt").write_text("1girl, solo\n", encoding="utf-8")

    assert captions.read_caption(image) == ("1girl, solo\n", False)


# --------------------------------------------------------------------------
# A6 round trip: split_tags / join_tags and write->read
# --------------------------------------------------------------------------

#: A tag set with spaces, escaped parens, Japanese/Chinese, and a colon (no commas - a comma-containing tag takes the rejection path).
#: All are "already normalized" forms (no leading/trailing whitespace, non-empty), so write->read must be the identity.
ROUND_TRIP_TAGS = [
    "1girl",
    "a b",
    "c\\(d\\)",
    "hatsune_miku_(cosplay)",
    "long_hair",
    "rating:general",
    "0.5",
    "日本語タグ",
    "初音ミク",
    "a b c d",
    "()",
]


def test_join_split_round_trip() -> None:
    text = captions.join_tags(ROUND_TRIP_TAGS)

    assert text == ", ".join(ROUND_TRIP_TAGS)
    assert captions.split_tags(text) == ROUND_TRIP_TAGS


@pytest.mark.parametrize("tags", [
    ROUND_TRIP_TAGS,
    ["a b", "c\\(d\\)", "1girl"],
    ["1girl"],
    [],
    # The acceptance suite A6's original set (`\(` `\)` and weight syntax must be restored byte for byte; a comma-containing tag takes the rejection path)
    ["1girl", "solo"],
    ["long hair", "blue eyes"],
    ["hatsune miku", "vocaloid"],
    ["kagura " + BS + "(gintama" + BS + ")", "silver hair"],
    ["初音ミク", "ツインテール"],
    ["(masterpiece:1.2)", "best quality"],
    ["hu tao " + BS + "(genshin impact" + BS + ")", "1girl"],
    # A tag ending in a backslash: it still round-trips byte for byte without escaping (the old implementation's escaping ate the separator here)
    ["a" + BS, "b"],
    ["1_2024-12-31 " + BS + "(dirty" + BS + ")" + BS, "hair ornament"],
])
def test_write_read_round_trip(tmp_path: Path, tags: list[str]) -> None:
    image = make_image(tmp_path)

    result = captions.write_caption(image, tags)
    text, _ = captions.read_caption(image)

    assert result.caption_path.read_text(encoding="utf-8") == ", ".join(tags)
    assert captions.split_tags(text) == tags


def test_written_file_is_utf8_without_bom_and_single_line(tmp_path: Path) -> None:
    image = make_image(tmp_path)

    captions.write_caption(image, ROUND_TRIP_TAGS)

    raw = (tmp_path / "1.txt").read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert raw == ", ".join(ROUND_TRIP_TAGS).encode("utf-8")
    assert b"\r\n" not in raw
    assert not raw.endswith(b"\n"), "captions in the dataset have no trailing newline (measured)"


def test_split_tags_tolerates_extra_whitespace(tmp_path: Path) -> None:
    """A file with odd leading/trailing whitespace still reads out the correct tag list (dataset-contract 'tolerate extra whitespace')."""
    image = make_image(tmp_path)
    (tmp_path / "1.txt").write_text("  1girl ,  solo ,,\tlong hair  \n\n", encoding="utf-8")

    text, multiline = captions.read_caption(image)

    assert multiline is True
    assert captions.split_tags(text) == ["1girl", "solo", "long hair"]


def test_split_tags_keeps_newline_inside_tag() -> None:
    """A newline is **not** a separator (the rule is bare commas): line 2 simply does not take part in training, flagged by the multiline warning.

    Treating it as a separator would conjure up tags the trainer cannot read - again "displayed != what the trainer reads".
    """
    assert captions.split_tags("1girl, solo\nsecond line") == ["1girl", "solo\nsecond line"]


def test_real_dataset_defect_is_visible() -> None:
    """A real defect must be displayed faithfully as "a tag with a trailing backslash" and must not be reverse-escaped "fixed".

    Without unescaping there is no such problem; this case pins it down: any "casual cleanup" turns it red.
    """
    got = captions.split_tags(REAL_DEFECT_LINE)

    assert got == trainer_split(REAL_DEFECT_LINE)
    assert got[1] == REAL_DEFECT_TAG
    assert got[1].endswith(BS), "the defect tag must keep that extra backslash"


@pytest.mark.skipif(not REAL_CAPTION.is_file(), reason="no captioned file in the configured dataset (read-only case)")
def test_real_dataset_caption_splits_like_the_trainer() -> None:
    """Validate the rule against a real file (**read-only**): assert only invariants that hold for **any content**.

    It does **not** assert specific tags, specific bytes, or whether a defect is still there - the whole point of a dataset editor is to
    fix such defects, and "the defect must still be there" would forbid the editor from working and turn the case red every time the user fixes a caption.
    The defect shape itself is the responsibility of the synthetic constant `REAL_DEFECT_LINE` (independent of the file's content).
    """
    before = REAL_CAPTION.stat()
    raw_text = REAL_CAPTION.read_text(encoding="utf-8")

    assert captions.split_tags(raw_text) == trainer_split(raw_text)
    assert captions.split_tags(raw_text) == [
        t.strip() for t in raw_text.strip().split(",") if t.strip()
    ]
    assert all(tag == tag.strip() and tag for tag in captions.split_tags(raw_text))

    after = REAL_CAPTION.stat()
    assert (after.st_size, after.st_mtime_ns) == (before.st_size, before.st_mtime_ns), (
        "the read-only case touched the dataset"
    )


def test_editor_can_repair_the_defect_in_a_copy(tmp_path: Path) -> None:
    """Run the repair flow on the **synthetic** sample against a tmp_path copy: delete the extra backslash -> write -> read back clean.

    The real dataset takes no part (the asserted object is always the copy, p0-spec §0); this case is also evidence that "the editor can work",
    sharing the division of labor with the read-only invariant case above: one validates the rule, the other validates the capability.
    """
    image = make_image(tmp_path)
    (tmp_path / "1.txt").write_text(REAL_DEFECT_LINE, encoding="utf-8")
    cache = make_te_cache(image)

    tags = captions.split_tags(captions.read_caption(image)[0])
    dirty = [tag for tag in tags if tag.endswith(BS)]
    assert dirty == [REAL_DEFECT_TAG], "the synthetic sample should contain one tag with a trailing backslash: %r" % (tags,)

    fixed = [tag[:-1] if tag.endswith(BS) else tag for tag in tags]
    result = captions.write_caption(image, fixed)

    assert result.changed is True
    assert not cache.exists(), "fixing a caption must likewise invalidate the TE cache (invariant number one)"
    assert result.cache_remove_failed == ()
    assert captions.split_tags(captions.read_caption(image)[0]) == fixed
    assert not [tag for tag in fixed if tag.endswith(BS)]


def test_write_canonicalizes_tags(tmp_path: Path) -> None:
    """strip + drop empty tags on write: only then is "write->read" the identity for a normalized list."""
    image = make_image(tmp_path)

    captions.write_caption(image, ["  1girl ", "", "  ", "solo"])

    assert (tmp_path / "1.txt").read_text(encoding="utf-8") == "1girl, solo"
    assert captions.split_tags(captions.read_caption(image)[0]) == ["1girl", "solo"]


def test_normalize_for_write_is_the_same_rule_as_the_write_path(tmp_path: Path) -> None:
    """`normalize_for_write` must use the same rule as `write_caption`.

    `POST /api/captions/batch`'s `dry_run` uses it to compute "what would be written", so if the two diverge,
    the preview reports a comma-containing tag as "writable" while the real write is rejected.
    """
    image = make_image(tmp_path)
    tags = ["  1girl ", "", "  ", "solo"]

    captions.write_caption(image, tags)

    assert captions.normalize_for_write(tags) == (tmp_path / "1.txt").read_text(encoding="utf-8")
    assert captions.normalize_for_write([]) == ""
    with pytest.raises(ValueError):
        captions.normalize_for_write(["ok", " a,b "])


@pytest.mark.parametrize("raw", [
    "1girl, solo, long hair",
    "  spaced ,  out  ",
    "",
    "   ",
    ",",
    ",,,",
    "a" + BS + ",b",
    "x" + BS + "," + BS + ",y",
    "kagura " + BS + "(gintama" + BS + "), silver hair",
    REAL_DEFECT_LINE,
    "1girl, solo\nsecond line",
    "a" + BS,
])
def test_split_tags_equals_trainer_formula(raw: str) -> None:
    """The split rule = the trainer's rule, byte for byte (p0-spec §3.2 / §5 A6): split on bare commas, strip, drop empty pieces, **no unescaping**."""
    assert captions.split_tags(raw) == trainer_split(raw)


def test_split_tags_on_empty_text() -> None:
    assert captions.split_tags("") == []
    assert captions.split_tags("   ") == []
    assert captions.split_tags("\n\n") == []


def test_write_caption_rejects_comma_inside_tag(tmp_path: Path) -> None:
    """A comma-containing tag must be rejected on write: on disk the trainer would read it as two tags (silent data corruption)."""
    image = make_image(tmp_path)

    with pytest.raises(ValueError):
        captions.write_caption(image, ["a,b", "c"], invalidate=False)

    with pytest.raises(ValueError):
        captions.write_caption(image, ["ok", " b,c "])

    assert not (tmp_path / "1.txt").exists(), "nothing should be left behind after a rejection"


def test_rejected_write_touches_neither_caption_nor_cache(tmp_path: Path) -> None:
    """The rejection happens **before** the write and the cache deletion: the original caption and the cache stay untouched."""
    image = make_image(tmp_path)
    target = tmp_path / "1.txt"
    target.write_text("1girl, solo", encoding="utf-8")
    cache = make_te_cache(image)
    before = entries(tmp_path)

    with pytest.raises(ValueError):
        captions.write_caption(image, ["1girl", "a,b"])

    assert target.read_text(encoding="utf-8") == "1girl, solo"
    assert cache.is_file(), "rejecting a write but deleting the cache = making the next training rebuild for nothing"
    assert entries(tmp_path) == before


def test_join_tags_is_a_plain_join() -> None:
    """`join_tags` is exactly `", ".join(tags)`: no escaping, no deduplication, no sorting, no stripping.

    Comma interception happens on the write path (`write_caption`), not in this pure string function.
    """
    assert captions.join_tags(["a b", "c" + BS + "(d" + BS + ")"]) == "a b, c" + BS + "(d" + BS + ")"
    assert captions.join_tags([]) == ""
    assert captions.join_tags(["x,y"]) == "x,y"


def test_text_path_writes_verbatim_and_allows_commas(tmp_path: Path) -> None:
    """A `str` is written as a whole block of text (the HTTP `text` field): no normalization, no comma interception."""
    image = make_image(tmp_path)

    result = captions.write_caption(image, "a,b, c\n", invalidate=False)

    assert result.changed is True
    assert (tmp_path / "1.txt").read_text(encoding="utf-8") == "a,b, c\n"


# --------------------------------------------------------------------------
# A5-1 success: change a caption -> the cache disappears
# --------------------------------------------------------------------------


def test_write_caption_removes_text_encoder_cache(tmp_path: Path) -> None:
    image = make_image(tmp_path)
    safetensors = make_te_cache(image, "_anima_te.safetensors")
    npz = make_te_cache(image, "_anima_te.npz")

    result = captions.write_caption(image, ["1girl", "solo"])

    assert result.changed is True
    assert result.caption_path == tmp_path / "1.txt"
    assert result.multiline_warning is False
    assert result.cache_remove_failed == ()
    assert result.cache_removed == (safetensors, npz)
    assert not safetensors.exists(), "changing a caption must delete the TE cache, otherwise training silently uses the old tags"
    assert not npz.exists()
    assert (tmp_path / "1.txt").read_text(encoding="utf-8") == "1girl, solo"


def test_write_caption_without_cache_is_fine(tmp_path: Path) -> None:
    image = make_image(tmp_path)

    result = captions.write_caption(image, ["1girl"])

    assert result.changed is True
    assert result.cache_removed == ()
    assert result.cache_remove_failed == ()


def test_unchanged_write_does_not_invalidate(tmp_path: Path) -> None:
    """When the content is unchanged, do not wastefully delete the cache (otherwise every save makes the next training rebuild everything)."""
    image = make_image(tmp_path)
    captions.write_caption(image, ["1girl", "solo"])
    cache = make_te_cache(image)
    caption_stat = (tmp_path / "1.txt").stat().st_mtime_ns

    result = captions.write_caption(image, ["1girl", "solo"])

    assert result.changed is False
    assert result.cache_removed == ()
    assert result.cache_remove_failed == ()
    assert cache.is_file()
    assert (tmp_path / "1.txt").stat().st_mtime_ns == caption_stat, "an unchanged save should not rewrite the file"


def test_unchanged_write_still_reports_multiline_only_when_content_is_multiline(tmp_path: Path) -> None:
    image = make_image(tmp_path)
    captions.write_caption(image, "1girl\nsecond")

    result = captions.write_caption(image, "1girl\nsecond")

    assert result.changed is False
    assert result.multiline_warning is True


def test_invalidate_false_keeps_cache(tmp_path: Path) -> None:
    image = make_image(tmp_path)
    cache = make_te_cache(image)

    result = captions.write_caption(image, ["1girl"], invalidate=False)

    assert result.changed is True
    assert result.cache_removed == ()
    assert cache.is_file()


def test_write_caption_custom_ext(tmp_path: Path) -> None:
    image = make_image(tmp_path)
    cache = make_te_cache(image)

    result = captions.write_caption(image, ["1girl"], ext=".caption")

    assert result.caption_path == tmp_path / "1.caption"
    assert (tmp_path / "1.caption").read_text(encoding="utf-8") == "1girl"
    assert not (tmp_path / "1.txt").exists()
    assert not cache.exists()


def test_write_caption_accepts_raw_text(tmp_path: Path) -> None:
    """Given a `str`, write it as a whole block of text (the HTTP `text` field is this path)."""
    image = make_image(tmp_path)

    result = captions.write_caption(image, "1girl, solo\n")

    assert result.changed is True
    assert result.multiline_warning is False
    assert (tmp_path / "1.txt").read_text(encoding="utf-8") == "1girl, solo\n"


def test_write_caption_multiline_text_warns(tmp_path: Path) -> None:
    image = make_image(tmp_path)

    result = captions.write_caption(image, "1girl\nsolo\n")

    assert result.multiline_warning is True
    assert captions.read_caption(image)[1] is True


def test_write_caption_empty_tag_list_writes_empty_file(tmp_path: Path) -> None:
    image = make_image(tmp_path)
    cache = make_te_cache(image)

    result = captions.write_caption(image, [])

    assert result.changed is True
    assert (tmp_path / "1.txt").read_bytes() == b""
    assert not cache.exists(), "emptying a caption is a content change too"


# --------------------------------------------------------------------------
# A5-2 failure: the cache is locked -> cache_remove_failed non-empty, the caption is still written correctly
# --------------------------------------------------------------------------


@pytest.mark.skipif(os.name != "nt", reason="POSIX permits deleting an open file, so a handle cannot construct a deletion failure")
def test_write_caption_reports_locked_cache_and_still_writes(tmp_path: Path) -> None:
    image = make_image(tmp_path)
    locked = make_te_cache(image, "_anima_te.safetensors")
    free = make_te_cache(image, "_anima_te.npz")

    with open(locked, "rb"):
        result = captions.write_caption(image, ["1girl", "solo"])

        assert result.cache_remove_failed == (locked,), "a deletion failure must be reported to the caller, never swallowed"
        assert result.cache_removed == (free,)
        assert locked.is_file()

    assert (tmp_path / "1.txt").read_text(encoding="utf-8") == "1girl, solo"
    assert captions.split_tags(captions.read_caption(image)[0]) == ["1girl", "solo"]


@pytest.mark.skipif(os.name != "nt", reason="POSIX permits deleting an open file")
def test_locked_cache_is_removed_on_the_next_write(tmp_path: Path) -> None:
    """The failure is recoverable: once the user closes the process holding it, saving again is clean."""
    image = make_image(tmp_path)
    locked = make_te_cache(image)

    with open(locked, "rb"):
        first = captions.write_caption(image, ["1girl"])
    assert first.cache_remove_failed == (locked,)

    second = captions.write_caption(image, ["1girl", "solo"])

    assert second.cache_removed == (locked,)
    assert second.cache_remove_failed == ()
    assert not locked.exists()


# --------------------------------------------------------------------------
# A7 atomic write
# --------------------------------------------------------------------------


def test_write_leaves_no_temp_file(tmp_path: Path) -> None:
    image = make_image(tmp_path)
    before = entries(tmp_path)

    captions.write_caption(image, ["1girl", "solo"])

    after = entries(tmp_path)
    assert after - before == {"1.txt"}, "the directory must gain nothing else after a write"
    assert [name for name in after if name.startswith(".tmp")] == []


def test_overwrite_leaves_no_temp_file(tmp_path: Path) -> None:
    image = make_image(tmp_path)
    (tmp_path / "1.txt").write_text("old", encoding="utf-8")
    before = entries(tmp_path)

    captions.write_caption(image, ["new"])

    assert entries(tmp_path) == before
    assert (tmp_path / "1.txt").read_text(encoding="utf-8") == "new"


@pytest.mark.skipif(os.name != "nt", reason="on POSIX replacing a read-only file is unrestricted (permissions are decided by the directory)")
def test_write_failure_keeps_original_and_leaves_no_temp(tmp_path: Path) -> None:
    """A7: when writing to a read-only caption fails, the **original file content is unchanged** and no temp file is left."""
    image = make_image(tmp_path)
    target = tmp_path / "1.txt"
    target.write_text("OLD", encoding="utf-8")
    cache = make_te_cache(image)
    os.chmod(target, stat.S_IREAD)

    try:
        with pytest.raises(OSError):
            captions.write_caption(image, ["NEW"])

        assert target.read_text(encoding="utf-8") == "OLD", "a failed write must not corrupt the original file"
        assert [name for name in entries(tmp_path) if name.startswith(".tmp")] == []
        assert cache.is_file(), "the write did not even succeed, so the cache should not be deleted along the way"
    finally:
        os.chmod(target, stat.S_IWRITE)

    result = captions.write_caption(image, ["NEW"])

    assert result.changed is True
    assert target.read_text(encoding="utf-8") == "NEW"
    assert not cache.exists()


@pytest.mark.skipif(os.name == "nt", reason="on Windows a read-only directory does not stop file creation, so use the read-only file case")
def test_write_failure_in_readonly_dir_keeps_original(tmp_path: Path) -> None:
    image = make_image(tmp_path)
    target = tmp_path / "1.txt"
    target.write_text("OLD", encoding="utf-8")
    os.chmod(tmp_path, stat.S_IREAD | stat.S_IEXEC)
    try:
        with pytest.raises(OSError):
            captions.write_caption(image, ["NEW"])
        assert target.read_text(encoding="utf-8") == "OLD"
    finally:
        os.chmod(tmp_path, stat.S_IWRITE | stat.S_IEXEC)


def test_write_does_not_create_directories(tmp_path: Path) -> None:
    """When the image does not exist, do not mkdir for the caller - the dataset directory must not quietly grow subdirectories."""
    ghost = tmp_path / "nope" / "1.jpeg"

    with pytest.raises(OSError):
        captions.write_caption(ghost, ["1girl"])

    assert not (tmp_path / "nope").exists()


# --------------------------------------------------------------------------
# Wiring with cache_invalidation: this module only depends on it, never assembles paths itself
# --------------------------------------------------------------------------


def test_write_uses_cache_invalidation_paths(tmp_path: Path) -> None:
    """The cache paths must be decided by `cache_invalidation`, so the rule does not drift between two places."""
    image = make_image(tmp_path, "a.b.jpeg")
    expected = ci.text_encoder_cache_paths(image)[0]
    expected.parent.mkdir(parents=True, exist_ok=True)
    expected.write_bytes(b"x")

    result = captions.write_caption(image, ["1girl"])

    assert result.cache_removed == (expected,)
