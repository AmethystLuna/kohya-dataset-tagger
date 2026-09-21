"""Unit tests for `core/vocab.py`: CSV discovery, categories, autocomplete ordering, and the A11 real-vocabulary criteria.

The real vocabulary lives in the HF cache (**read-only**): the snapshots/<sha>/ directory changes on re-download, so it is always
**discovered with a wildcard**, never a hardcoded sha (also in `wd14-tagger.md:74` and the skill's high-frequency pitfalls list).
"""
from __future__ import annotations

import csv
import os
from collections import Counter
from pathlib import Path

import pytest

import local_paths
from kohya_dataset_tagger.core.vocab import (
    CATEGORY_NAMES,
    DANBOORU_ORDER,
    TagInfo,
    Vocabulary,
)

#: The HuggingFace cache from `local_paths.ini` (None when the key is unset). Tests **only read** it.
HF_HUB = local_paths.hf_hub()

#: The A11 criterion (p0-spec §1.2 smoke measurement): 10861 total rows = general 8106 + character 2751 + rating 4.
EXPECTED_TOTAL = 10861
EXPECTED_CATEGORIES = {"0": 8106, "4": 2751, "9": 4}
EXPECTED_RATING_TAGS = ["general", "sensitive", "questionable", "explicit"]

HEADER = "tag_id,name,category,count\n"

MINI_ROWS = [
    ("1", "general", "9", "100"),
    ("2", "sensitive", "9", "90"),
    ("3", "1girl", "0", "5000"),
    ("4", "solo", "0", "4000"),
    ("5", "long_hair", "0", "3000"),
    ("6", "hatsune_miku", "4", "800"),
    ("7", "vocaloid", "3", "700"),
    ("8", "artist_name", "1", "600"),
    ("9", "meta_thing", "5", "500"),
]


def write_csv(directory: Path, name: str, rows=MINI_ROWS, header: str = HEADER) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    body = "".join(",".join(row) + "\n" for row in rows)
    path.write_text(header + body, encoding="utf-8")
    return path


@pytest.fixture()
def mini(tmp_path: Path) -> Vocabulary:
    return Vocabulary.from_csv(write_csv(tmp_path, "selected_tags.csv"))


# --------------------------------------------------------------------------
# CSV discovery: filenames are not uniform, so discover any *.csv in the same directory
# --------------------------------------------------------------------------


@pytest.mark.parametrize("filename", ["selected_tags.csv", "wd-vit-tagger-v3.csv", "my-tags.csv"])
def test_from_csv_discovers_any_csv_name_in_directory(tmp_path: Path, filename: str) -> None:
    """The HF cache calls it selected_tags.csv and ComfyUI's copy calls it wd-vit-tagger-v3.csv - neither may be hardcoded."""
    write_csv(tmp_path, filename)

    vocab = Vocabulary.from_csv(tmp_path)

    assert len(vocab) == len(MINI_ROWS)
    assert vocab.get("1girl") is not None


def test_from_csv_accepts_the_csv_file_directly(tmp_path: Path) -> None:
    path = write_csv(tmp_path, "wd-vit-tagger-v3.csv")

    assert len(Vocabulary.from_csv(path)) == len(MINI_ROWS)


def test_from_csv_discovers_csv_next_to_a_model_file(tmp_path: Path) -> None:
    """Given model.onnx, the vocabulary in the same directory is still found."""
    write_csv(tmp_path, "wd-vit-tagger-v3.csv")
    onnx = tmp_path / "wd-vit-tagger-v3.onnx"
    onnx.write_bytes(b"fake onnx")

    assert len(Vocabulary.from_csv(onnx)) == len(MINI_ROWS)


def test_from_csv_prefers_explicit_file_over_directory_scan(tmp_path: Path) -> None:
    write_csv(tmp_path, "other.csv", rows=[("1", "wrong", "0", "1")])
    wanted = write_csv(tmp_path, "selected_tags.csv")

    vocab = Vocabulary.from_csv(wanted)

    assert len(vocab) == len(MINI_ROWS)


def test_from_csv_rejects_ambiguous_directory(tmp_path: Path) -> None:
    """With multiple CSVs, better an error than silently picking the wrong vocabulary (a wrong category poisons sorting and thresholds)."""
    write_csv(tmp_path, "selected_tags.csv")
    write_csv(tmp_path, "another.csv")

    with pytest.raises(ValueError):
        Vocabulary.from_csv(tmp_path)


def test_from_csv_missing_path_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        Vocabulary.from_csv(tmp_path / "nope.csv")
    with pytest.raises(FileNotFoundError):
        Vocabulary.from_csv(tmp_path / "empty-dir")


def test_from_csv_directory_without_csv_raises(tmp_path: Path) -> None:
    (tmp_path / "model.onnx").write_bytes(b"fake")

    with pytest.raises(FileNotFoundError):
        Vocabulary.from_csv(tmp_path)


def test_from_csv_requires_the_four_columns(tmp_path: Path) -> None:
    path = tmp_path / "selected_tags.csv"
    path.write_text("name,count\n1girl,10\n", encoding="utf-8")

    with pytest.raises(ValueError):
        Vocabulary.from_csv(path)


# --------------------------------------------------------------------------
# Parsing details
# --------------------------------------------------------------------------


def test_category_is_the_raw_digit_string(mini: Vocabulary) -> None:
    """p0-spec §3.5: category is a **numeric string** - kept as-is, not converted to int or to a category name."""
    assert mini.get("1girl") == TagInfo(name="1girl", category="0", count=5000)
    assert mini.get("hatsune_miku").category == "4"
    assert mini.get("general").category == "9"


def test_from_csv_tolerates_bom_crlf_and_blank_lines(tmp_path: Path) -> None:
    path = tmp_path / "selected_tags.csv"
    path.write_bytes(
        ("\ufeff" + HEADER + "1,1girl,0,10\r\n\r\n2,solo,0,5\r\n").encode("utf-8")
    )

    vocab = Vocabulary.from_csv(path)

    assert len(vocab) == 2
    assert vocab.get("1girl").count == 10


def test_from_csv_skips_blank_names_and_survives_bad_count(tmp_path: Path) -> None:
    rows = [("1", "", "0", "10"), ("2", "1girl", "0", ""), ("3", "solo", "0", "abc")]
    vocab = Vocabulary.from_csv(write_csv(tmp_path, "selected_tags.csv", rows=rows))

    assert len(vocab) == 2
    assert vocab.get("1girl").count == 0
    assert vocab.get("solo").count == 0


def test_from_csv_keeps_first_of_duplicate_names(tmp_path: Path) -> None:
    rows = [("1", "1girl", "0", "10"), ("2", "1girl", "4", "20")]
    vocab = Vocabulary.from_csv(write_csv(tmp_path, "selected_tags.csv", rows=rows))

    assert len(vocab) == 1
    assert vocab.get("1girl").count == 10


# --------------------------------------------------------------------------
# Query and autocomplete
# --------------------------------------------------------------------------


def test_len_and_contains(mini: Vocabulary) -> None:
    assert len(mini) == len(MINI_ROWS)
    assert "1girl" in mini
    assert "nope" not in mini
    assert 123 not in mini


def test_lookup_is_case_insensitive(mini: Vocabulary) -> None:
    assert "1GIRL" in mini
    assert mini.get("1GIRL") == mini.get("1girl")
    assert mini.get("  1girl  ") == mini.get("1girl")
    assert mini.get("nope") is None


def test_search_is_prefix_and_case_insensitive(mini: Vocabulary) -> None:
    assert [tag.name for tag in mini.search("1g")] == ["1girl"]
    assert [tag.name for tag in mini.search("LONG")] == ["long_hair"]
    assert mini.search("zzz") == []


def test_search_orders_by_count_desc(mini: Vocabulary) -> None:
    """Autocomplete candidates are ordered by popularity (wd14-tagger.md:71, 'the count column gives tag popularity')."""
    assert [tag.name for tag in mini.search("s")] == ["solo", "sensitive"]
    assert [tag.name for tag in mini.search("")] == [
        "1girl", "solo", "long_hair", "hatsune_miku", "vocaloid", "artist_name", "meta_thing",
        "general", "sensitive",
    ]


def test_search_respects_limit(mini: Vocabulary) -> None:
    assert len(mini.search("", limit=2)) == 2
    assert mini.search("", limit=0) == []
    assert mini.search("", limit=-1) == []


def test_order_key_follows_danbooru_order(mini: Vocabulary) -> None:
    tags = ["artist_name", "vocaloid", "hatsune_miku", "1girl", "meta_thing", "general"]

    assert sorted(tags, key=mini.order_key) == [
        "general",       # Rating
        "meta_thing",    # Meta
        "1girl",         # General
        "hatsune_miku",  # Character
        "vocaloid",      # Copyright
        "artist_name",   # Artist
    ]


def test_order_key_puts_unknown_tags_last(mini: Vocabulary) -> None:
    assert mini.order_key("no_such_tag") > mini.order_key("artist_name")
    assert sorted(["no_such_tag", "1girl"], key=mini.order_key) == ["1girl", "no_such_tag"]


def test_order_key_uses_vocabulary_row_order_inside_a_category(tmp_path: Path) -> None:
    """Within a category the vocabulary row order (= the tagger's label order) applies, **not** popularity.

    rating is the clearest example: sensitive is more popular than general, but the label order must be
    general -> sensitive -> questionable -> explicit.
    """
    rows = [("1", "general", "9", "100"), ("2", "sensitive", "9", "999"), ("3", "1girl", "0", "1")]
    vocab = Vocabulary.from_csv(write_csv(tmp_path, "selected_tags.csv", rows=rows))

    assert sorted(["sensitive", "general"], key=vocab.order_key) == ["general", "sensitive"]
    assert sorted(["sensitive", "general", "1girl"], key=vocab.order_key) == [
        "general", "sensitive", "1girl",
    ]


def test_order_key_ranks_by_danbooru_order_position(mini: Vocabulary) -> None:
    assert [mini.order_key(name)[0] for name in ("general", "meta_thing", "1girl")] == [0, 1, 3]
    assert DANBOORU_ORDER == (
        "Rating", "Meta", "Quality", "General", "Character", "Copyright", "Artist",
    )


def test_order_key_accepts_category_names_too() -> None:
    """`_category_rank` accepts both a numeric string (as the CSV has it) and a category name (a caller-built TagInfo)."""
    vocab = Vocabulary([TagInfo("x", "General", 1), TagInfo("y", "Rating", 1)])

    assert vocab.order_key("x")[0] == DANBOORU_ORDER.index("General")
    assert vocab.order_key("y")[0] == DANBOORU_ORDER.index("Rating")
    assert sorted(["x", "y"], key=vocab.order_key) == ["y", "x"]


def test_category_names_map_is_the_documented_one() -> None:
    assert CATEGORY_NAMES == {
        "0": "General", "1": "Artist", "3": "Copyright",
        "4": "Character", "5": "Meta", "9": "Rating",
    }


def test_duplicated_constants_stay_in_sync_with_autotag() -> None:
    """autotag cannot import core (§3.0), so CATEGORY_NAMES / DANBOORU_ORDER each keep a copy.

    Acceptance A16 compares these two copies value by value; this pins it down early, so drift turns red in my case first.
    """
    from kohya_dataset_tagger.autotag import base as autotag_base
    from kohya_dataset_tagger.autotag import postprocess as autotag_pp

    assert {str(code): name for code, name in autotag_base.CATEGORY_NAMES.items()} == CATEGORY_NAMES
    assert autotag_pp.DANBOORU_ORDER == DANBOORU_ORDER


def test_empty_vocabulary() -> None:
    vocab = Vocabulary([])

    assert len(vocab) == 0
    assert "1girl" not in vocab
    assert vocab.get("1girl") is None
    assert vocab.search("1g") == []
    assert vocab.order_key("1girl") == (len(DANBOORU_ORDER), 0, "1girl")


# --------------------------------------------------------------------------
# A11: this machine's real vocabulary (read-only)
# --------------------------------------------------------------------------


def _snapshot_dirs() -> list[Path]:
    """Discover the wd-swinv2-tagger-v3 snapshot directory with a wildcard - **no hardcoded sha**."""
    if HF_HUB is None:
        return []
    return sorted(
        path
        for path in HF_HUB.glob("models--SmilingWolf--wd-swinv2-tagger-v3/snapshots/*")
        if path.is_dir() and list(path.glob("*.csv"))
    )


def _require_snapshot() -> Path:
    dirs = _snapshot_dirs()
    if not dirs:
        pytest.skip(
            "no wd-swinv2-tagger-v3 CSV in the configured HuggingFace cache (%s)"
            % (HF_HUB if HF_HUB is not None else "hf_hub is unset in local_paths.ini")
        )
    return dirs[-1]


def test_real_csv_matches_smoke_measurements() -> None:
    """A11 criterion: 10861 rows = general 8106 + character 2751 + rating 4, with the first 4 rows being rating."""
    snapshot = _require_snapshot()
    csv_path = next(snapshot.glob("*.csv"))

    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert len(rows) == EXPECTED_TOTAL
    assert dict(Counter(row["category"] for row in rows)) == EXPECTED_CATEGORIES
    assert [row["name"] for row in rows[:4]] == EXPECTED_RATING_TAGS
    assert {row["category"] for row in rows[:4]} == {"9"}
    assert list(rows[0].keys()) == ["tag_id", "name", "category", "count"]


def test_real_vocabulary_loads_from_snapshot_directory() -> None:
    """Read the real vocabulary through the 'any *.csv in the same directory' discovery path (the directory also has model.onnx)."""
    snapshot = _require_snapshot()

    vocab = Vocabulary.from_csv(snapshot)

    assert len(vocab) == EXPECTED_TOTAL
    assert [vocab.get(name).category for name in EXPECTED_RATING_TAGS] == ["9"] * 4
    assert vocab.get("1girl").category == "0"
    assert vocab.get("1girl").count > 0


def test_real_vocabulary_rating_tags_sort_first() -> None:
    vocab = Vocabulary.from_csv(_require_snapshot())

    ranked = sorted(EXPECTED_RATING_TAGS + ["1girl"], key=vocab.order_key)

    assert ranked[:4] == EXPECTED_RATING_TAGS
    assert ranked[-1] == "1girl"


def test_real_vocabulary_autocomplete() -> None:
    vocab = Vocabulary.from_csv(_require_snapshot())

    suggestions = vocab.search("raiden")

    assert suggestions, "the vocabulary should have tags starting with raiden"
    assert all(tag.name.startswith("raiden") for tag in suggestions)
    assert [tag.count for tag in suggestions] == sorted(
        (tag.count for tag in suggestions), reverse=True
    )
