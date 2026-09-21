"""Contract tests for core/dataset_toml.py and api/dataset.py (p0-spec §3.9, §4.9).

Covers
----
1. **Escaping**: `toml_quote`'s backslashes / double quotes / control characters, verified by round trip through a **real TOML parser**
   (tomli - it comes with pytest on Python 3.10; 3.11+ uses the stdlib `tomllib`).
   §3.9's measurement table shows an unescaped basic string is rejected outright by the trainer, so this is not decoration.
2. **One subset per directory**: a subdirectory with 0 images is skipped and recorded in `skipped`, and an empty subset is never emitted;
   ordering is stable (two generations are byte-for-byte identical).
3. **One criterion per warning class of §3.9's six**; each asserts "the target subset shows only that class", and
   **a clean subset has not a single warning** - otherwise "there is a warning" could be stacked up from false positives. §4.9's frozen
   `[code]` table has a product for every code (including `[unreadable_dir]`, simulated with monkeypatch).
4. **§4.9's HTTP shape**: `subsets[]`'s five keys, the top-level `warnings` being a summary,
   the code of `skipped[].reason`, and 403 / 404 / 400 / 422.
5. **Read-only**: both the synthetic and the real dataset are compared before and after by a `(relative path, size, mtime_ns)` snapshot.
6. **Frontend bucketing**: `web/js/locales/zh-CN.js`'s `FALLBACK_GROUPS` is the grouping table,
   and `app.js:warningCategory()` groups **by `[code]` first**, falling back to the keyword table only when unrecognized.
   This file loads that table and verifies, rule by rule, that our copy lands in the correct bucket.
7. **Structured companion arrays (§4.9 supplement 4)**: `warning_items` / `skipped_items` and the legacy
   `warnings` / `skipped` are **item-by-item same order and length, with byte-for-byte identical text**; params are checked per code;
   a message is still complete when params are empty (the frontend falls back on that, never dropping a message).

What it does not do
--------
**It does not call the trainer**: §5 A23 (`python -m library.config_util --support_dreambooth <toml>`
exit code 0) is accepted by the spec owner. Here only the TOML parser is used to hold up the 'the output is valid TOML' layer.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from kohya_dataset_tagger import config
from kohya_dataset_tagger.api import dataset as dataset_api
from kohya_dataset_tagger.app import create_app
from kohya_dataset_tagger.core import dataset_toml, paths

try:  # Python 3.11+
    import tomllib as toml_reader
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 goes here
    import tomli as toml_reader  # pip show tomli -> Required-by: pytest

import local_paths

REPO = Path(__file__).resolve().parents[1]

#: The dataset from `local_paths.ini` (the committed sample dataset when it is unset). This file only
#: reads it: it creates, modifies and deletes nothing, and it pins **no count** - the dataset is alive,
#: so the criteria below assert how the plan relates to the directory listing, not how large the
#: directory happens to be today.
REAL_ROOT = local_paths.dataset_root()

#: §4.9's code table (frozen) and the current frontend's grouping table (keywords) - two cross-module contracts.
SPEC_PATH = REPO / ".github" / "memory" / "p0-spec.md"
SPEC_CODE_RE = re.compile(r"^\|\s*`\[(\w+)\]`\s*\|", re.M)
STRINGS_ZH = REPO / "src" / "kohya_dataset_tagger" / "web" / "js" / "locales" / "zh-CN.js"
FALLBACK_GROUPS_BLOCK_RE = re.compile(r"FALLBACK_GROUPS = Object\.freeze\(\[(.*?)\n\]\);", re.S)
FALLBACK_GROUPS_RE = re.compile(r'\{\s*id:\s*"(\w+)",\s*match:\s*\[(.*?)\]\s*\}', re.S)
JS_STRING_RE = re.compile(r'"((?:[^"\\]|\\.)*)"')

#: Message shape: `"[code] description"` (§4.9 supplement 3).
CODE_RE = re.compile(r"^\[([a-z_]+)\] .+$", re.S)

#: Synthetic dataset directory names (natural order = lexicographic, single digits).
CLEAN = "1_clean"          # 5 images / 5 captions
MISSING = "2_missing"      # 5 images / 4 captions  -> missing_caption
COMMA = "3_comma"          # 5 images, one caption contains \,  -> escaped_comma
OVERSIZE = "4_oversize"    # 5 images, one 6000 wide -> oversize (default max_bucket_reso=4096)
ALPHA = "5_alpha"          # 5 images, one RGBA -> alpha_images
FEW = "6_few"              # 3 images -> too_few_images
EMPTY = "7_empty"          # no images -> skipped, never in subsets
BROKEN = "8_broken"        # 5 images, one is not an image -> unreadable_image
CACHES = ("cache_text_encoder", "latent_cache")
HIDDEN = ".hidden"


# ---------------------------------------------------------------------------
# Synthetic dataset
# ---------------------------------------------------------------------------


def _jpeg(path: Path, size: tuple[int, int] = (24, 18)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (200, 30, 30)).save(path, "JPEG")
    return path


def _png_rgba(path: Path, size: tuple[int, int] = (24, 18)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", size, (0, 0, 255, 128)).save(path, "PNG")
    return path


def _caption(image: Path, text: str = "1girl, solo") -> Path:
    path = image.with_name(image.stem + ".txt")
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture()
def dataset_root(tmp_path: Path) -> Path:
    """A synthetic dataset with "one spot per warning class", plus a clean sample and directories that must be filtered."""
    root = tmp_path / "dataset"
    for index in range(1, 6):
        _caption(_jpeg(root / CLEAN / ("%d.jpeg" % index)))
    for index in range(1, 6):
        image = _jpeg(root / MISSING / ("%d.jpeg" % index))
        if index <= 4:
            _caption(image)
    for index in range(1, 6):
        image = _jpeg(root / COMMA / ("%d.jpeg" % index))
        # Byte-for-byte identical to the real defect (changelog/caption-defect-fix): the open paren is escaped and the close paren is bare,
        # then **one extra backslash** sits in front of the comma - so the tag the trainer reads ends with a \.
        _caption(
            image,
            r"fugue \(honkai: star rail\), brown hair" if index != 5 else r"fugue \(honkai: star rail\)\, brown hair",
        )
    for index in range(1, 6):
        size = (6000, 12) if index == 5 else (24, 18)
        _caption(_jpeg(root / OVERSIZE / ("%d.jpeg" % index), size=size))
    for index in range(1, 6):
        if index == 5:
            _caption(_png_rgba(root / ALPHA / "5.png"))
        else:
            _caption(_jpeg(root / ALPHA / ("%d.jpeg" % index)))
    for index in range(1, 4):
        _caption(_jpeg(root / FEW / ("%d.jpeg" % index)))
    (root / EMPTY).mkdir(parents=True)
    (root / EMPTY / "note.txt").write_text("not an image, should not be counted as one\n", encoding="utf-8")
    for index in range(1, 6):
        _caption(_jpeg(root / BROKEN / ("%d.jpeg" % index)))
    (root / BROKEN / "5.jpeg").write_bytes(b"this is not a jpeg")

    # Directories that must be filtered: images inside them can neither become a subset nor be counted.
    for name in CACHES + (HIDDEN,):
        _jpeg(root / name / "cached.jpeg")
    # Images in the root directory itself: the trainer does not recurse, so none can enter a subset -> it must warn.
    _caption(_jpeg(root / "loose.jpeg"))
    return root


def _by_name(plan: dataset_toml.TomlPlan) -> dict[str, dataset_toml.SubsetPlan]:
    return {subset.image_dir.name: subset for subset in plan.subsets}


def _code(message: str) -> str:
    """`"[code] description"` -> `code`; a wrong shape fails outright."""
    match = CODE_RE.match(message)
    assert match is not None, "the message does not start with [code]: %r" % message
    return match.group(1)


def _codes(messages) -> list[str]:
    return [_code(message) for message in messages]


def _planned(root: Path, params: dataset_toml.DatasetTomlParams | None = None) -> dataset_toml.TomlPlan:
    return dataset_toml.plan_dataset_toml(root, params or dataset_toml.DatasetTomlParams())


def _all_messages(plan: dataset_toml.TomlPlan) -> list[str]:
    messages = list(plan.warnings)
    for subset in plan.subsets:
        messages.extend(subset.warnings)
    messages.extend(entry.reason for entry in plan.skipped)
    return messages


def _all_codes(plan: dataset_toml.TomlPlan) -> set[str]:
    return {_code(message) for message in _all_messages(plan)}


def _toml_key_lines(text: str, key: str) -> list[str]:
    """The lines in TOML that **really are written** as `key = ...` - a mention in a comment does not count."""
    return re.findall(r"^\s*%s\s*=" % re.escape(key), text, re.M)


def _snapshot(root: Path) -> list[tuple[str, int, int]]:
    """A full snapshot of (relative path, size, mtime_ns) - the read-only criterion."""
    entries: list[tuple[str, int, int]] = []
    for path in sorted(root.rglob("*")):
        stat = path.stat()
        entries.append((path.relative_to(root).as_posix(), stat.st_size, stat.st_mtime_ns))
    return entries


def _simulate_unreadable(monkeypatch, name: str, root: Path) -> dataset_toml.TomlPlan:
    """Make one subdirectory's enumeration raise `PermissionError` (really changing ACLs is both unreliable and needs admin)."""
    real = paths.iter_images

    def fake(directory, *, recursive: bool = False):
        if Path(directory).name == name:
            raise PermissionError("access denied")
        return real(directory, recursive=recursive)

    monkeypatch.setattr(paths, "iter_images", fake)
    return _planned(root)


# ---------------------------------------------------------------------------
# 1. Escaping (§3.9's hard requirement)
# ---------------------------------------------------------------------------


def test_toml_quote_doubles_backslashes_and_escapes_quotes() -> None:
    r"""Measured criterion: the backslashes in `"F:\\a\\b"` must double, otherwise the trainer exits 1."""
    assert dataset_toml.toml_quote(r"F:\a\b") == '"F:\\\\a\\\\b"'
    assert dataset_toml.toml_quote('say "hi"') == '"say \\"hi\\""'
    assert dataset_toml.toml_quote("") == '""'


def test_toml_quote_escapes_control_characters() -> None:
    assert dataset_toml.toml_quote("a\nb\tc") == '"a\\nb\\tc"'
    assert dataset_toml.toml_quote("bell\x07") == '"bell\\u0007"'


def test_quoted_values_round_trip_through_a_toml_parser() -> None:
    """The real parser has the final say: what comes back must be the original string."""
    values = [
        r"D:\datasets\my-lora\1_sample_alpha",
        'quote " inside',
        "back\\slash and \\\\ double",
        "tab\there",
        "中文 tag（含全角括号）",
        "it's",
        "trailing backslash \\",
        "\x01\x1f\x7f",
    ]
    for value in values:
        parsed = toml_reader.loads("x = " + dataset_toml.toml_quote(value))
        assert parsed["x"] == value, value


# ---------------------------------------------------------------------------
# 2. One subset per directory
# ---------------------------------------------------------------------------


def test_one_subset_per_subdirectory(dataset_root: Path) -> None:
    plan = _planned(dataset_root)
    assert [subset.image_dir.name for subset in plan.subsets] == [
        CLEAN, MISSING, COMMA, OVERSIZE, ALPHA, FEW, BROKEN,
    ]
    for subset in plan.subsets:
        assert subset.image_dir.parent == plan.root, "image_dir must be a direct subdirectory of the root"
    assert len({str(subset.image_dir) for subset in plan.subsets}) == len(plan.subsets)
    assert str(plan.root) not in {str(subset.image_dir) for subset in plan.subsets}
    assert plan.image_count == 5 * 6 + 3


def test_cache_and_hidden_directories_are_neither_subsets_nor_skipped(dataset_root: Path) -> None:
    plan = _planned(dataset_root)
    names = {subset.image_dir.name for subset in plan.subsets}
    skipped = {entry.image_dir.name for entry in plan.skipped}
    for name in CACHES + (HIDDEN,):
        assert name not in names
        assert name not in skipped
    assert plan.image_count == 33, "images in the cache directory were counted into image_count"


def test_empty_directory_is_skipped_not_emitted(dataset_root: Path) -> None:
    """§3.9: a subdirectory with 0 images is skipped and warned about; an empty subset is never emitted."""
    plan = _planned(dataset_root)
    assert EMPTY not in _by_name(plan)
    assert [entry.image_dir.name for entry in plan.skipped] == [EMPTY]
    assert [entry.reason for entry in plan.skipped] == [dataset_toml.SKIP_REASON_EMPTY]
    assert plan.warnings[0].startswith("[%s] " % dataset_toml.WARN_EMPTY_DIR)
    assert plan.toml_text.count("[[datasets.subsets]]") == len(plan.subsets)
    assert EMPTY not in plan.toml_text


def test_unreadable_directory_is_skipped_and_warned(monkeypatch, dataset_root: Path) -> None:
    """§4.9's `[unreadable_dir]`: an unreadable directory goes into skipped, and must not bring down the whole generation."""
    plan = _simulate_unreadable(monkeypatch, CLEAN, dataset_root)
    assert CLEAN not in _by_name(plan)
    assert [entry.reason for entry in plan.skipped if entry.image_dir.name == CLEAN] == [
        dataset_toml.SKIP_REASON_UNREADABLE
    ]
    assert dataset_toml.WARN_UNREADABLE_DIR in _codes(plan.warnings)
    assert len(plan.subsets) == 6, "the other directories are generated as usual"


def test_output_is_deterministic_and_sorted_by_directory_name(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    for name in ("10_ten", "2_two", "1_one"):
        _caption(_jpeg(root / name / "1.jpeg", size=(8, 8)))

    first = _planned(root)
    second = _planned(root)
    assert [subset.image_dir.name for subset in first.subsets] == ["1_one", "2_two", "10_ten"]
    assert [subset.image_dir.name for subset in second.subsets] == ["1_one", "2_two", "10_ten"]
    assert first.toml_text == second.toml_text


def test_generated_toml_parses_back_to_the_same_subset_directories(dataset_root: Path) -> None:
    plan = _planned(dataset_root, dataset_toml.DatasetTomlParams(num_repeats=3, batch_size=2))
    document = toml_reader.loads(plan.toml_text)

    assert document["general"]["enable_bucket"] is True
    assert document["general"]["max_bucket_reso"] == 4096
    dataset = document["datasets"][0]
    assert dataset["resolution"] == [1536, 1536]
    assert dataset["batch_size"] == 2
    assert dataset["caption_extension"] == ".txt"

    dirs = [subset["image_dir"] for subset in dataset["subsets"]]
    assert dirs == [str(subset.image_dir) for subset in plan.subsets]
    assert all(subset["num_repeats"] == 3 for subset in dataset["subsets"])
    assert all(Path(directory).is_dir() for directory in dirs)


def test_dropout_keys_are_only_written_when_not_default(dataset_root: Path) -> None:
    """A23's precondition: a default value is not written, otherwise --support_dreambooth becomes exit 1 on an extra key."""
    default_text = _planned(dataset_root).toml_text
    assert "caption_dropout_rate" not in default_text
    assert "caption_tag_dropout_rate" not in default_text
    assert "caption_dropout_every_n_epochs" not in default_text

    custom = _planned(
        dataset_root,
        dataset_toml.DatasetTomlParams(
            caption_dropout_rate=0.05,
            caption_tag_dropout_rate=0.1,
            caption_dropout_every_n_epochs=2,
        ),
    )
    document = toml_reader.loads(custom.toml_text)
    subset = document["datasets"][0]["subsets"][0]
    assert subset["caption_dropout_rate"] == 0.05
    assert subset["caption_tag_dropout_rate"] == 0.1
    assert subset["caption_dropout_every_n_epochs"] == 2


def test_plan_raises_when_no_subdirectory_has_images(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    (empty / "1_post").mkdir(parents=True)
    with pytest.raises(dataset_toml.DatasetTomlError):
        _planned(empty)

    flat = tmp_path / "flat"
    _caption(_jpeg(flat / "1.jpeg", size=(8, 8)))
    with pytest.raises(dataset_toml.DatasetTomlError) as excinfo:
        _planned(flat)
    assert "image_dir" in str(excinfo.value), "the flat-directory hint does not say clearly what to do"


# ---------------------------------------------------------------------------
# 3. §3.9's six warning classes + §4.9's code table
# ---------------------------------------------------------------------------


def test_clean_subset_has_no_warning_at_all(dataset_root: Path) -> None:
    """A false positive would make every criterion above meaningless, so this one is pinned first."""
    assert _by_name(_planned(dataset_root))[CLEAN].warnings == ()


def test_missing_caption_warning(dataset_root: Path) -> None:
    subset = _by_name(_planned(dataset_root))[MISSING]
    assert subset.caption_count == 4
    assert _codes(subset.warnings) == [dataset_toml.WARN_MISSING_CAPTION]
    assert ".txt" in subset.warnings[0]


def test_escaped_comma_warning(dataset_root: Path) -> None:
    subset = _by_name(_planned(dataset_root))[COMMA]
    assert _codes(subset.warnings) == [dataset_toml.WARN_ESCAPED_COMMA]
    assert "\\," in subset.warnings[0]


def test_oversize_image_warning(dataset_root: Path) -> None:
    subset = _by_name(_planned(dataset_root))[OVERSIZE]
    assert _codes(subset.warnings) == [dataset_toml.WARN_OVERSIZE_IMAGE]
    assert "4096" in subset.warnings[0] and "6000" in subset.warnings[0]

    raised = _by_name(_planned(dataset_root, dataset_toml.DatasetTomlParams(max_bucket_reso=8192)))
    assert raised[OVERSIZE].warnings == (), "raising the threshold should stop the report"


def test_alpha_image_warning(dataset_root: Path) -> None:
    """When the switch is off it says "how to turn it on"; when on it confirms "it was written" - both wordings must carry alpha_mask."""
    subset = _by_name(_planned(dataset_root))[ALPHA]
    assert _codes(subset.warnings) == [dataset_toml.WARN_ALPHA_IMAGES]
    assert "alpha_mask" in subset.warnings[0]
    assert "already written" not in subset.warnings[0], "the default (off) case must not say it was already written"

    enabled = _by_name(
        _planned(dataset_root, dataset_toml.DatasetTomlParams(alpha_mask=True))
    )[ALPHA]
    assert _codes(enabled.warnings) == [dataset_toml.WARN_ALPHA_IMAGES]
    assert "alpha_mask = true" in enabled.warnings[0]


def test_alpha_mask_is_only_written_when_enabled(dataset_root: Path) -> None:
    """§3.9: alpha_mask defaults to false - the same "write only when non-default" as the three caption_dropout_* keys.

    It is a subset-level key (the trainer's DB_SUBSET_DISTINCT_SCHEMA) and can only go in
    [[datasets.subsets]]; putting it in [[datasets]] is rejected with extra keys not allowed.
    """
    default_text = _planned(dataset_root).toml_text
    assert _toml_key_lines(default_text, "alpha_mask") == []
    assert toml_reader.loads(default_text)["datasets"][0]["subsets"][0].get("alpha_mask") is None

    enabled = _planned(dataset_root, dataset_toml.DatasetTomlParams(alpha_mask=True))
    document = toml_reader.loads(enabled.toml_text)
    subsets = document["datasets"][0]["subsets"]
    assert all(subset["alpha_mask"] is True for subset in subsets)
    assert len(_toml_key_lines(enabled.toml_text, "alpha_mask")) == len(enabled.subsets)
    assert "alpha_mask" not in document["general"]
    assert "alpha_mask" not in document["datasets"][0]
    # All default parameters at once: the value ranges match §3.9's defaults (so nobody changes them in the wrong direction)
    assert dataset_toml.DatasetTomlParams().alpha_mask is False


def test_too_few_images_warning_does_not_block(dataset_root: Path) -> None:
    plan = _planned(dataset_root)
    subset = _by_name(plan)[FEW]
    assert _codes(subset.warnings) == [dataset_toml.WARN_TOO_FEW_IMAGES]
    assert subset.image_count == 3, "a hint must not block, nor change the image count"
    assert dataset_toml.MIN_IMAGES_PER_SUBSET == 5


def test_broken_image_warning(dataset_root: Path) -> None:
    subset = _by_name(_planned(dataset_root))[BROKEN]
    assert _codes(subset.warnings) == [dataset_toml.WARN_BROKEN_IMAGE]
    assert subset.image_count == 5, "a broken file still counts toward the image count (the trainer counts it too)"


def test_images_in_the_root_itself_are_reported(dataset_root: Path) -> None:
    """§1.1's other form: images scattered in the parent directory are never trained, and it must be said."""
    plan = _planned(dataset_root)
    codes = _codes(plan.warnings)
    assert dataset_toml.WARN_EMPTY_DIR in codes
    assert dataset_toml.WARN_ROOT_IMAGES in codes
    assert plan.image_count == 33, "images at the root layer belong to no subset and must not count into image_count"


def test_top_level_warnings_are_a_summary_of_the_subset_warnings(dataset_root: Path) -> None:
    """§4.9 supplement 2: the top-level warnings include each subsets[].warnings entry, so both places carry them."""
    plan = _planned(dataset_root)
    flattened = [warning for subset in plan.subsets for warning in subset.warnings]
    assert flattened, "the synthetic dataset has not a single subset warning, so this criterion would spin empty"

    head = plan.warnings[: len(plan.warnings) - len(flattened)]
    assert _codes(head) == [dataset_toml.WARN_EMPTY_DIR, dataset_toml.WARN_ROOT_IMAGES]
    assert list(plan.warnings[len(head):]) == flattened

    api_shape = [warning for subset in plan.subsets for warning in subset.warnings]
    assert all(warning in plan.warnings for warning in api_shape)


def test_every_message_carries_a_bracketed_code(dataset_root: Path) -> None:
    plan = _planned(dataset_root)
    messages = _all_messages(plan)
    assert messages
    for message in messages:
        assert CODE_RE.match(message), message


def _frozen_codes() -> list[str]:
    """The codes in §4.9's code table (return an empty list when they cannot be parsed, and the caller skips)."""
    if not SPEC_PATH.is_file():
        return []
    text = SPEC_PATH.read_text(encoding="utf-8")
    return sorted(set(SPEC_CODE_RE.findall(text)))


def test_every_frozen_code_is_produced_by_the_implementation(monkeypatch, dataset_root: Path) -> None:
    """All seven classes in the table must really be producible - otherwise the frontend's "group by code" table is a blank check."""
    frozen = _frozen_codes()
    if len(frozen) < 7:
        pytest.skip("p0-spec §4.9's code table cannot be parsed (the spec format changed, so this criterion is void)")

    produced = _all_codes(_planned(dataset_root))
    produced |= _all_codes(_simulate_unreadable(monkeypatch, CLEAN, dataset_root))
    assert set(frozen) <= produced, "these frozen codes were never produced: %s" % sorted(
        set(frozen) - produced
    )


def test_plan_never_writes_to_the_dataset(dataset_root: Path) -> None:
    before = _snapshot(dataset_root)
    _planned(dataset_root)
    assert _snapshot(dataset_root) == before


# ---------------------------------------------------------------------------
# 4. Frontend bucketing (the wording is a cross-module contract)
# ---------------------------------------------------------------------------


def _frontend_groups() -> list[tuple[str, list[str]]] | None:
    """Read FALLBACK_GROUPS from `web/js/locales/zh-CN.js`; return None when the format changed (that criterion is void)."""
    if not STRINGS_ZH.is_file():
        return None
    block = FALLBACK_GROUPS_BLOCK_RE.search(STRINGS_ZH.read_text(encoding="utf-8"))
    if block is None:
        return None
    groups: list[tuple[str, list[str]]] = []
    for group_id, body in FALLBACK_GROUPS_RE.findall(block.group(1)):
        keywords = [
            keyword.replace("\\\\", "\\").replace('\\"', '"')
            for keyword in JS_STRING_RE.findall(body)
        ]
        groups.append((group_id, keywords))
    return groups or None


def _category(message: str, groups: list[tuple[str, list[str]]]) -> str:
    """The same rule as `app.js:warningCategory()`: a recognizable `[code]` has the final say,
    otherwise fall back to the keyword table (the first matching group wins), then to other."""
    known = {group_id for group_id, _ in groups}
    match = CODE_RE.match(message)
    if match is not None and match.group(1) in known:
        return match.group(1)
    text = message.lower()
    for group_id, keywords in groups:
        for keyword in keywords:
            if keyword.lower() in text:
                return group_id
    return "other"


def test_warning_messages_land_in_the_right_frontend_bucket(dataset_root: Path) -> None:
    """The purpose of §4.9 supplement 3: a recognizable code is grouped by code, so rewriting the copy never silently degrades grouping."""
    groups = _frontend_groups()
    if groups is None:
        pytest.skip("zh-CN.js's FALLBACK_GROUPS cannot be parsed (the frontend format changed, so this criterion is void)")

    plan = _planned(dataset_root)
    actual: dict[str, set[str]] = {}
    for message in _all_messages(plan) + [
        dataset_toml.SKIP_REASON_EMPTY,
        dataset_toml.SKIP_REASON_UNREADABLE,
    ]:
        actual.setdefault(_code(message), set()).add(_category(message, groups))

    produced = set(actual)
    expected_codes = {
        dataset_toml.WARN_ESCAPED_COMMA,
        dataset_toml.WARN_MISSING_CAPTION,
        dataset_toml.WARN_OVERSIZE_IMAGE,
        dataset_toml.WARN_ALPHA_IMAGES,
        dataset_toml.WARN_TOO_FEW_IMAGES,
        dataset_toml.WARN_EMPTY_DIR,
        dataset_toml.WARN_UNREADABLE_DIR,
        dataset_toml.WARN_ROOT_IMAGES,
        dataset_toml.WARN_BROKEN_IMAGE,
    }
    assert produced == expected_codes, "some class was not covered: %s" % sorted(expected_codes ^ produced)

    # The frontend recognizes this code (the group id is the code) -> it must land in its own bucket;
    # not recognized yet (the frontend lists only seven codes) -> it may only fall to 'other', and **must not** be grabbed by another code bucket via the keyword table.
    known = {group_id for group_id, _ in groups}
    for code, categories in actual.items():
        if code in known:
            assert categories == {code}, (code, categories)
        else:
            assert categories <= {"other"}, (code, categories)


# ---------------------------------------------------------------------------
# 5. The HTTP surface (§4.9)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _neutral_state():
    """Do not leave the allowlist behind for later tests (the same approach as test_fs_api.py)."""
    yield
    config.load(env={}, roots=[])


def _client_for(roots: list[Path]) -> TestClient:
    """Go through the **real** app.py assembly: §4's error shape (`{"error": {...}}`) is only formed at that layer.

    It also pins down "`api/dataset.py` is discovered dynamically" along the way - a minimal FastAPI app cannot test this.
    """
    config.load(env={}, roots=list(roots))
    return TestClient(create_app())


@pytest.fixture()
def api_client(dataset_root: Path):
    with _client_for([dataset_root]) as client:
        yield client


def _assert_error(response, status: int, code: str) -> None:
    assert response.status_code == status, response.text[:300]
    body = response.json()
    assert set(body) == {"error"}, body
    # 2026-09-19: the error shape gained params (§4 supplement); the other two legacy fields are unchanged to the letter.
    assert set(body["error"]) == {"code", "message", "params"}, body
    assert isinstance(body["error"]["params"], dict), body
    assert body["error"]["code"] == code, body


def test_router_is_prefixed_once_with_api() -> None:
    assert dataset_api.router.prefix == "/api"
    assert "/api/dataset/toml" in {route.path for route in dataset_api.router.routes}


def test_api_returns_the_frozen_response_shape(api_client, dataset_root: Path) -> None:
    response = api_client.post("/api/dataset/toml", json={"root": str(dataset_root)})
    assert response.status_code == 200, response.text[:500]
    body = response.json()

    assert set(body) == {
        "toml", "subset_count", "image_count", "subsets", "warnings", "skipped",
        # §4.9 supplement 4: the two added **optional** structured companion arrays.
        "warning_items", "skipped_items",
    }
    assert _toml_key_lines(body["toml"], "alpha_mask") == [], "alpha_mask must not be written under default parameters"
    assert body["subset_count"] == len(body["subsets"]) == 7
    assert body["image_count"] == 33
    for subset in body["subsets"]:
        assert set(subset) == {"image_dir", "image_count", "num_repeats", "caption_count", "warnings"}
        assert Path(subset["image_dir"]).parent == dataset_root
        assert isinstance(subset["warnings"], list)
    for entry in body["skipped"]:
        assert set(entry) == {"image_dir", "reason"}, "the legacy skipped entry shape is unchanged to the letter"
        assert entry["reason"] == dataset_toml.SKIP_REASON_EMPTY
        assert _code(entry["reason"]) == dataset_toml.WARN_EMPTY_DIR

    plan = _planned(dataset_root)
    assert body["warnings"] == list(plan.warnings), "the top-level warnings must be the summary of the subset warnings"
    assert body["warnings"][0].startswith("[%s] " % dataset_toml.WARN_EMPTY_DIR)
    assert body["toml"] == plan.toml_text
    assert "image_dir = " in body["toml"]

    # §4.9 supplement 4: the two new arrays have the same order and length as the legacy arrays, with byte-identical text/reason.
    assert [item["text"] for item in body["warning_items"]] == body["warnings"]
    assert [item["code"] for item in body["warning_items"]] == _codes(body["warnings"])
    assert [item["image_dir"] for item in body["skipped_items"]] == [
        entry["image_dir"] for entry in body["skipped"]
    ]
    assert [item["reason"] for item in body["skipped_items"]] == [
        entry["reason"] for entry in body["skipped"]
    ]
    assert [item["code"] for item in body["skipped_items"]] == _codes(
        [entry["reason"] for entry in body["skipped"]]
    )


def test_api_accepts_exactly_what_the_export_panel_sends(api_client, dataset_root: Path) -> None:
    """`app.js:collectExportParams()` sends exactly these four keys (§4.9's form is exactly these four knobs).

    `resolution`'s wire shape is a two-element `[width, height]` array (§4.9 supplement 1).
    """
    response = api_client.post(
        "/api/dataset/toml",
        json={
            "root": str(dataset_root),
            "params": {
                "resolution": [1024, 768],
                "batch_size": 2,
                "num_repeats": 4,
                "caption_extension": ".txt",
            },
        },
    )
    assert response.status_code == 200, response.text[:500]
    body = response.json()
    document = toml_reader.loads(body["toml"])
    assert document["datasets"][0]["resolution"] == [1024, 768]
    assert document["datasets"][0]["batch_size"] == 2
    assert [subset["num_repeats"] for subset in body["subsets"]] == [4] * 7


def test_api_defaults_root_to_the_first_whitelisted_root(api_client, dataset_root: Path) -> None:
    response = api_client.post("/api/dataset/toml", json={})
    assert response.status_code == 200, response.text[:500]
    assert response.json()["subset_count"] == 7


def test_api_accepts_the_alpha_mask_switch(api_client, dataset_root: Path) -> None:
    """§3.9's alpha_mask: the backend accepts it, and the frontend already has the checkbox (added 2026-09-17)."""
    response = api_client.post(
        "/api/dataset/toml", json={"root": str(dataset_root), "params": {"alpha_mask": True}}
    )
    assert response.status_code == 200, response.text[:500]
    document = toml_reader.loads(response.json()["toml"])
    assert all(subset["alpha_mask"] is True for subset in document["datasets"][0]["subsets"])


def test_api_keeps_section_3_9_defaults_for_omitted_params(api_client, dataset_root: Path) -> None:
    body = api_client.post("/api/dataset/toml", json={"root": str(dataset_root), "params": {}}).json()
    document = toml_reader.loads(body["toml"])
    assert document["datasets"][0]["resolution"] == [1536, 1536]
    assert document["datasets"][0]["batch_size"] == 1
    assert [subset["num_repeats"] for subset in body["subsets"]] == [1] * 7


def test_api_rejects_paths_outside_the_roots(api_client, tmp_path: Path) -> None:
    _assert_error(
        api_client.post("/api/dataset/toml", json={"root": str(tmp_path / "elsewhere")}),
        403,
        "outside_roots",
    )


def test_api_reports_missing_and_non_directory_roots(api_client, dataset_root: Path) -> None:
    _assert_error(
        api_client.post("/api/dataset/toml", json={"root": str(dataset_root / "nope")}),
        404,
        "not_found",
    )
    _assert_error(
        api_client.post("/api/dataset/toml", json={"root": str(dataset_root / "loose.jpeg")}),
        400,
        "not_a_directory",
    )


def test_api_returns_400_when_no_subset_can_be_planned(tmp_path: Path) -> None:
    """The allowlist must point at this empty dataset, otherwise the request becomes a 403 for being out of bounds (that is another criterion)."""
    empty = tmp_path / "empty_dataset"
    (empty / "only_empty").mkdir(parents=True)
    with _client_for([empty]) as client:
        _assert_error(client.post("/api/dataset/toml", json={}), 400, "cannot_plan")


def test_api_rejects_unknown_or_out_of_range_params(api_client, dataset_root: Path) -> None:
    for params in (
        {"num_repeat": 2},           # unknown key
        {"num_repeats": 0},          # 0 images
        {"resolution": [0, 0]},      # non-positive
        {"resolution": [1536]},      # not a two-element array (§4.9 supplement 1)
        {"resolution": [1536, 1536, 1536]},
        {"caption_extension": "  "},
    ):
        response = api_client.post(
            "/api/dataset/toml", json={"root": str(dataset_root), "params": params}
        )
        assert response.status_code == 422, (params, response.text[:200])
        assert set(response.json()) == {"error"}


def test_api_never_writes_to_the_dataset(api_client, dataset_root: Path) -> None:
    before = _snapshot(dataset_root)
    assert api_client.post("/api/dataset/toml", json={"root": str(dataset_root)}).status_code == 200
    assert _snapshot(dataset_root) == before


# ---------------------------------------------------------------------------
# 6. Real dataset (read-only)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not REAL_ROOT.is_dir(), reason="no dataset configured in local_paths.ini: %s" % REAL_ROOT)
def test_real_dataset_plan_matches_the_directory_and_stays_read_only() -> None:
    before = _snapshot(REAL_ROOT)
    plan = _planned(REAL_ROOT)
    after = _snapshot(REAL_ROOT)

    assert after == before, "generating dataset.toml touched the read-only test set"
    # One subset per directory **that has images** - derived from the listing, not from a golden number.
    populated = [d for d in paths.list_subdirs(REAL_ROOT) if paths.count_images(d)]
    assert populated, "the configured dataset has no populated subdirectory: %s" % REAL_ROOT
    assert [item.image_dir.name for item in plan.subsets] == [d.name for d in populated]
    assert plan.image_count == sum(paths.count_images(d) for d in populated)
    assert paths.count_images(REAL_ROOT) == 0, "§1.1: the trainer's glob_images does not recurse, so the root itself is 0 images"

    document = toml_reader.loads(plan.toml_text)
    subsets = document["datasets"][0]["subsets"]
    assert len(subsets) == len(plan.subsets)
    assert [subset["image_dir"] for subset in subsets] == [str(item.image_dir) for item in plan.subsets]
    assert all(Path(subset["image_dir"]).parent == plan.root for subset in subsets)

    # The top level is a summary (§4.9 supplement 2) - the real data cannot be an exception either.
    flattened = [warning for item in plan.subsets for warning in item.warnings]
    assert list(plan.warnings[-len(flattened):]) == flattened


# ---------------------------------------------------------------------------
# 7. Structured companion arrays (§4.9 supplement 4)
# ---------------------------------------------------------------------------

#: The frozen params table (the core docstring / p0-spec §4.9 must match this).
WARNING_PARAMS: dict[str, set[str]] = {
    dataset_toml.WARN_MISSING_CAPTION: {"count", "extension"},
    dataset_toml.WARN_ESCAPED_COMMA: {"count"},
    dataset_toml.WARN_OVERSIZE_IMAGE: {"count", "max_bucket_reso", "max_edge"},
    dataset_toml.WARN_ALPHA_IMAGES: {"count", "alpha_mask"},
    dataset_toml.WARN_TOO_FEW_IMAGES: {"count", "minimum"},
    dataset_toml.WARN_BROKEN_IMAGE: {"count"},
    dataset_toml.WARN_EMPTY_DIR: {"count"},
    dataset_toml.WARN_UNREADABLE_DIR: {"name", "detail"},
    dataset_toml.WARN_ROOT_IMAGES: {"count"},
}


def test_warning_items_mirror_the_legacy_warnings(dataset_root: Path) -> None:
    """Every code appears in both forms, and text is byte-for-byte identical to the legacy warnings entry."""
    plan = _planned(dataset_root)
    assert plan.warning_items, "the synthetic dataset has no structured warning, so this criterion would spin empty"
    assert len(plan.warning_items) == len(plan.warnings)
    for item, message in zip(plan.warning_items, plan.warnings):
        assert item.text == message, (item.code, item.text, message)
        assert item.code == _code(message)
    for subset in plan.subsets:
        assert len(subset.warning_items) == len(subset.warnings)
        for item, message in zip(subset.warning_items, subset.warnings):
            assert item.text == message
            assert item.code == _code(message)


def test_skipped_items_mirror_the_legacy_skipped(dataset_root: Path) -> None:
    plan = _planned(dataset_root)
    assert plan.skipped, "no directory was skipped, so this criterion would spin empty"
    for entry in plan.skipped:
        assert entry.code == _code(entry.reason)
        assert isinstance(entry.params, dict)


def test_every_produced_warning_code_carries_its_documented_params(
    monkeypatch, dataset_root: Path
) -> None:
    """Check the params table per code: the produced code set must be **exactly equal** to the table's set."""
    plan = _planned(dataset_root)
    items = list(plan.warning_items)
    items += list(_simulate_unreadable(monkeypatch, CLEAN, dataset_root).warning_items)
    produced = {item.code for item in items}
    assert produced == set(WARNING_PARAMS), "coverage is incomplete: %s" % sorted(produced ^ set(WARNING_PARAMS))
    for item in items:
        assert set(item.params) == WARNING_PARAMS[item.code], (item.code, item.params)


def test_skip_reason_params_are_empty_by_design(monkeypatch, dataset_root: Path) -> None:
    """A skipped reason is a fixed short sentence with no variables - params must be an empty object, not a missing field."""
    plan = _planned(dataset_root)
    assert [entry.params for entry in plan.skipped] == [{}]
    unreadable_plan = _simulate_unreadable(monkeypatch, CLEAN, dataset_root)
    assert unreadable_plan.skipped
    assert all(entry.params == {} for entry in unreadable_plan.skipped)


def test_no_message_is_lost_when_params_are_empty(monkeypatch, dataset_root: Path) -> None:
    """When params are empty / missing, text and reason are still a whole displayable message."""
    plan = _simulate_unreadable(monkeypatch, CLEAN, dataset_root)
    for entry in plan.skipped:
        assert entry.params == {}
        assert CODE_RE.match(entry.reason), entry.reason
        assert entry.code == _code(entry.reason)
    for item in plan.warning_items:
        assert item.text.strip(), item.code
        assert item.text.startswith("[%s] " % item.code)


def test_api_error_params_carry_the_requested_path(api_client, dataset_root: Path, tmp_path: Path) -> None:
    """§4 supplement: the error's params carry the variable data, so the frontend can localize by msg.<code>."""
    outside = tmp_path / "elsewhere"
    body = api_client.post("/api/dataset/toml", json={"root": str(outside)}).json()
    assert body["error"]["code"] == "outside_roots"
    assert body["error"]["params"] == {"path": str(outside)}

    missing = dataset_root / "nope"
    body = api_client.post("/api/dataset/toml", json={"root": str(missing)}).json()
    assert body["error"]["code"] == "not_found"
    assert body["error"]["params"] == {"path": str(missing)}

    loose = dataset_root / "loose.jpeg"
    body = api_client.post("/api/dataset/toml", json={"root": str(loose)}).json()
    assert body["error"]["code"] == "not_a_directory"
    assert body["error"]["params"] == {"path": str(loose)}


def test_frontend_reads_the_structured_arrays_and_repaints_on_locale_change() -> None:
    """The frontend must really read §4.9 supplement 4's two arrays and repaint on a locale change (otherwise it stays in the old language)."""
    app = (REPO / "src" / "kohya_dataset_tagger" / "web" / "js" / "app.js").read_text(encoding="utf-8")
    assert "payload.warning_items" in app, "the render path does not read warning_items"
    assert "payload.skipped_items" in app, "the render path does not read skipped_items"
    assert 'localizeBackendMessage("msg."' in app, "warnings / errors do not go through msg.<code> localization"
    assert 'localizeBackendMessage("msg.skip."' in app, "skip reasons do not go through msg.skip.<code> localization"
    assert "hasKey(key)" in app, "localization does not use hasKey to test whether a translation exists"
    assert "return null" in app, "there is no fallback signal when placeholders cannot be filled"

