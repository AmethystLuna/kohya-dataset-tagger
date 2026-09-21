"""Unit tests for `autotag/postprocess.py`.

Covers every post-processing step listed in spec §4 / wd14-tagger.md, and per p0-spec's
"verify all three paths" discipline additionally verifies: defaults, the ordering contract,
and purity (does not mutate its arguments, does not touch the filesystem).
"""
from __future__ import annotations

import ast
import dataclasses
import inspect
from pathlib import Path

import pytest

from kohya_dataset_tagger.autotag import postprocess as pp
from kohya_dataset_tagger.autotag.base import TagScore


def S(tag: str, score: float, category: str) -> TagScore:
    return TagScore(tag=tag, score=score, category=category)


def tags_of(items: list[TagScore]) -> list[str]:
    return [item.tag for item in items]


# --------------------------------------------------------------------------
# Defaults: the two named in the spec
# --------------------------------------------------------------------------


def test_remove_underscore_is_on_by_default() -> None:
    """§1.2 measured pitfall 2: the model gives `raiden_shogun`, the dataset caption writes `raiden shogun`."""
    assert pp.PostprocessOptions().remove_underscore is True
    assert tags_of(pp.postprocess_tags([S("raiden_shogun", 0.9, "Character")])) == ["raiden shogun"]


def test_rating_is_off_by_default() -> None:
    """Sample of 400 real captions, 0 carry a rating tag; sd-scripts also emits none by default."""
    assert pp.PostprocessOptions().rating_mode == "none"
    assert tags_of(pp.postprocess_tags([S("explicit", 0.99, "Rating"), S("1girl", 0.9, "General")])) == ["1girl"]


# --------------------------------------------------------------------------
# remove_underscore
# --------------------------------------------------------------------------


def test_remove_underscore_replaces_all_underscores() -> None:
    out = pp.postprocess_tags([S("large_breasts", 0.9, "General")])
    assert tags_of(out) == ["large breasts"]


def test_remove_underscore_keeps_short_kaomoji_tags() -> None:
    """sd-scripts only replaces tags with len(tag) > 3; the local vocabulary has 12 `^_^`-like tags (measured)."""
    kaomoji = [S("^_^", 0.5, "General"), S(">_<", 0.5, "General"), S("a_b", 0.5, "General")]
    options = pp.PostprocessOptions()
    assert tags_of(pp.postprocess_tags(kaomoji, options)) == ["^_^", ">_<", "a_b"]
    assert pp.UNDERSCORE_MIN_LENGTH == 4


def test_remove_underscore_can_be_disabled() -> None:
    options = pp.PostprocessOptions(remove_underscore=False)
    assert tags_of(pp.postprocess_tags([S("raiden_shogun", 0.9, "Character")], options)) == ["raiden_shogun"]


# --------------------------------------------------------------------------
# undesired_tags: the comparison happens **after** remove_underscore
# --------------------------------------------------------------------------


def test_undesired_tags_compare_after_underscore_replacement() -> None:
    options = pp.PostprocessOptions(undesired_tags=("raiden shogun",))
    items = [S("raiden_shogun", 0.9, "Character"), S("1girl", 0.8, "General")]
    assert tags_of(pp.postprocess_tags(items, options)) == ["1girl"]


def test_undesired_tags_in_underscore_form_do_not_match() -> None:
    """Counter-case: writing `raiden_shogun` in the block list is useless -- because the comparison happens after the replacement."""
    options = pp.PostprocessOptions(undesired_tags=("raiden_shogun",))
    items = [S("raiden_shogun", 0.9, "Character")]
    assert tags_of(pp.postprocess_tags(items, options)) == ["raiden shogun"]


def test_undesired_tags_can_block_a_rating_tag() -> None:
    options = pp.PostprocessOptions(rating_mode="last", undesired_tags=("explicit",))
    items = [S("explicit", 0.9, "Rating"), S("1girl", 0.8, "General")]
    assert tags_of(pp.postprocess_tags(items, options)) == ["1girl"]


# --------------------------------------------------------------------------
# always_first_tags
# --------------------------------------------------------------------------


def test_always_first_tags_are_moved_to_the_front() -> None:
    options = pp.PostprocessOptions(always_first_tags=("1girl",))
    items = [S("solo", 0.9, "General"), S("1girl", 0.8, "General")]
    assert tags_of(pp.postprocess_tags(items, options)) == ["1girl", "solo"]


def test_always_first_tags_keep_the_given_order() -> None:
    """sd-scripts' item-by-item insert(0) reverses the order; here the given order is kept, that quirk is not copied."""
    options = pp.PostprocessOptions(always_first_tags=("1girl", "1boy"))
    items = [S("solo", 0.9, "General"), S("1boy", 0.8, "General"), S("1girl", 0.7, "General")]
    assert tags_of(pp.postprocess_tags(items, options)) == ["1girl", "1boy", "solo"]


def test_always_first_tags_ignore_absent_tags() -> None:
    options = pp.PostprocessOptions(always_first_tags=("nobody_here", "1girl"))
    items = [S("solo", 0.9, "General"), S("1girl", 0.8, "General")]
    assert tags_of(pp.postprocess_tags(items, options)) == ["1girl", "solo"]


def test_always_first_tags_win_over_danbooru_sort_and_rating() -> None:
    options = pp.PostprocessOptions(
        always_first_tags=("1girl",),
        sort_danbooru=True,
        character_tags_first=True,
        rating_mode="first",
    )
    items = [
        S("keqing", 0.9, "Character"),
        S("1girl", 0.5, "General"),
        S("explicit", 0.99, "Rating"),
    ]
    assert tags_of(pp.postprocess_tags(items, options)) == ["1girl", "explicit", "keqing"]


# --------------------------------------------------------------------------
# character_tags_first
# --------------------------------------------------------------------------


def test_character_tags_first_moves_characters_ahead_of_general() -> None:
    options = pp.PostprocessOptions(character_tags_first=True)
    items = [
        S("1girl", 0.9, "General"),
        S("keqing", 0.8, "Character"),
        S("solo", 0.7, "General"),
        S("raiden shogun", 0.6, "Character"),
    ]
    assert tags_of(pp.postprocess_tags(items, options)) == [
        "keqing",
        "raiden shogun",
        "1girl",
        "solo",
    ]


def test_character_tags_first_off_keeps_input_order() -> None:
    items = [S("1girl", 0.9, "General"), S("keqing", 0.8, "Character")]
    assert tags_of(pp.postprocess_tags(items, pp.PostprocessOptions())) == ["1girl", "keqing"]


# --------------------------------------------------------------------------
# character_tag_expand
# --------------------------------------------------------------------------


def test_character_tag_expand_splits_the_series_parenthesis() -> None:
    options = pp.PostprocessOptions(character_tag_expand=True)
    out = pp.postprocess_tags([S("raiden_shogun_(genshin_impact)", 0.95, "Character")], options)
    assert tags_of(out) == ["raiden shogun", "genshin impact"]
    assert all(item.category == "Character" for item in out)


def test_character_tag_expand_handles_costume_then_series() -> None:
    """Copied from sd-scripts: `chara_(costume)_(series)` -> `chara_(costume)` + `series`."""
    options = pp.PostprocessOptions(character_tag_expand=True)
    out = pp.postprocess_tags([S("chara_name_(costume)_(series)", 0.9, "Character")], options)
    assert tags_of(out) == ["chara name \\(costume\\)", "series"]


def test_character_tag_expand_only_touches_character_category() -> None:
    options = pp.PostprocessOptions(character_tag_expand=True)
    out = pp.postprocess_tags([S("touhou_(series)", 0.9, "Copyright")], options)
    assert tags_of(out) == ["touhou \\(series\\)"]


def test_character_tag_expand_off_by_default() -> None:
    """With expand off the parenthesis stays, and escape_parens turns it into the danbooru escaped form."""
    out = pp.postprocess_tags([S("raiden_shogun_(genshin_impact)", 0.9, "Character")])
    assert tags_of(out) == ["raiden shogun \\(genshin impact\\)"]


def test_character_tag_expand_leaves_nothing_to_escape() -> None:
    """With expand on the parenthesis has already been split off, so escape_parens has nothing to escape."""
    options = pp.PostprocessOptions(character_tag_expand=True, escape_parens=True)
    out = pp.postprocess_tags([S("raiden_shogun_(genshin_impact)", 0.95, "Character")], options)
    assert tags_of(out) == ["raiden shogun", "genshin impact"]
    assert not any("\\" in item.tag for item in out)


# --------------------------------------------------------------------------
# escape_parens (spec §4.1 / acceptance criterion A17)
# --------------------------------------------------------------------------


def test_escape_parens_is_on_by_default() -> None:
    """Frozen in §4.1: default true. The reason is two pieces of measured evidence, not taste."""
    assert pp.PostprocessOptions().escape_parens is True


def test_a17_keqing_becomes_exactly_the_danbooru_escaped_form() -> None:
    """Criterion A17: after remove_underscore + escape_parens, the **final text** for the
    vocabulary's `keqing_(genshin_impact)` must be exactly `keqing \\(genshin impact\\)`."""
    out = pp.postprocess_tags([S("keqing_(genshin_impact)", 0.9, "Character")])
    assert pp.format_tags(out) == "keqing \\(genshin impact\\)"


def test_a17_escape_parens_false_keeps_the_bare_form() -> None:
    options = pp.PostprocessOptions(escape_parens=False)
    out = pp.postprocess_tags([S("keqing_(genshin_impact)", 0.9, "Character")], options)
    assert pp.format_tags(out) == "keqing (genshin impact)"


def test_escape_prefix_is_a_single_backslash() -> None:
    assert pp.ESCAPE_PREFIX == "\\"


def test_escape_parens_touches_only_parentheses() -> None:
    """A tag containing a backslash but no parenthesis is unaffected -- the vocabulary happens to have 5 such kaomoji tags."""
    out = pp.postprocess_tags([S("double_\\m/", 0.5, "General")])
    assert tags_of(out) == ["double \\m/"]


def test_escape_parens_is_literal_not_idempotent() -> None:
    """The spec says "escape all ( )", so already-escaped input gets another backslash.

    This cannot happen on the real vocabulary: the 1598 tags containing parentheses contain no
    backslash, and the two sets are disjoint. This test pins the "literal rule" down so nobody
    casually turns it into an idempotent implementation and changes the contract.
    """
    out = pp.postprocess_tags([S("a \\(b\\)", 0.5, "General")])
    assert tags_of(out) == ["a \\\\(b\\\\)"]


def test_undesired_tags_use_the_escaped_form() -> None:
    """escape_parens runs **before** the block list, so the block list is written in the form visible in the caption."""
    items = [S("hu_tao_(genshin_impact)", 0.9, "Character")]

    blocked = pp.PostprocessOptions(undesired_tags=("hu tao \\(genshin impact\\)",))
    assert tags_of(pp.postprocess_tags(items, blocked)) == []

    stale = pp.PostprocessOptions(undesired_tags=("hu tao (genshin impact)",))
    assert tags_of(pp.postprocess_tags(items, stale)) == ["hu tao \\(genshin impact\\)"]


def test_always_first_tags_use_the_escaped_form() -> None:
    options = pp.PostprocessOptions(always_first_tags=("hu tao \\(genshin impact\\)",))
    items = [S("1girl", 0.99, "General"), S("hu_tao_(genshin_impact)", 0.5, "Character")]
    assert tags_of(pp.postprocess_tags(items, options)) == [
        "hu tao \\(genshin impact\\)",
        "1girl",
    ]


def test_tag_replacement_keys_use_the_escaped_form() -> None:
    options = pp.PostprocessOptions(
        tag_replacement={"keqing \\(genshin impact\\)": "keqing"}
    )
    items = [S("keqing_(genshin_impact)", 0.9, "Character")]
    assert tags_of(pp.postprocess_tags(items, options)) == ["keqing"]


def test_user_weight_syntax_must_not_be_escaped() -> None:
    """The warning in spec §4.1: user-typed tags may carry `(masterpiece:1.2)` weight syntax.

    The default escape_parens=True escapes the parentheses, so **the path that writes user tags
    must pass False**. This test pins both behaviours down, so nobody pipes the tagger's output
    path straight onto user input.
    """
    items = [S("(masterpiece:1.2)", 1.0, "General")]
    assert tags_of(pp.postprocess_tags(items)) == ["\\(masterpiece:1.2\\)"]

    keep = pp.PostprocessOptions(remove_underscore=False, escape_parens=False)
    assert tags_of(pp.postprocess_tags(items, keep)) == ["(masterpiece:1.2)"]


def test_escape_parens_does_not_affect_danbooru_sorting() -> None:
    """escape runs before sorting, but sorting only looks at category / score, so the result is unaffected by it."""
    options = pp.PostprocessOptions(sort_danbooru=True)
    items = [
        S("genshin_impact", 0.7, "Copyright"),
        S("keqing_(genshin_impact)", 0.9, "Character"),
        S("1girl", 0.8, "General"),
    ]
    assert tags_of(pp.postprocess_tags(items, options)) == [
        "1girl",
        "keqing \\(genshin impact\\)",
        "genshin impact",
    ]


def test_option_fields_cover_the_frozen_request_body() -> None:
    """Every key of the §4.1 frozen request body must map onto a field (key-name drift makes D silently drop the parameter)."""
    frozen_body = {
        "remove_underscore": True,
        "undesired_tags": [],
        "always_first_tags": [],
        "character_tags_first": False,
        "character_tag_expand": False,
        "escape_parens": True,
        "rating": "none",
    }
    local_names = {"rating": "rating_mode"}
    fields = {item.name for item in dataclasses.fields(pp.PostprocessOptions)}
    for key in frozen_body:
        assert local_names.get(key, key) in fields, "frozen key %r has no corresponding field" % key


# --------------------------------------------------------------------------
# tag_replacement: must come **after** character_tag_expand / remove_underscore
# --------------------------------------------------------------------------


def test_tag_replacement_sees_the_expanded_tag() -> None:
    options = pp.PostprocessOptions(
        character_tag_expand=True,
        tag_replacement={"genshin impact": "genshin_impact"},
    )
    out = pp.postprocess_tags([S("raiden_shogun_(genshin_impact)", 0.9, "Character")], options)
    assert tags_of(out) == ["raiden shogun", "genshin_impact"]


def test_tag_replacement_keys_use_the_underscore_free_form() -> None:
    options = pp.PostprocessOptions(tag_replacement={"raiden shogun": "raiden mei"})
    out = pp.postprocess_tags([S("raiden_shogun", 0.9, "Character")], options)
    assert tags_of(out) == ["raiden mei"]

    stale = pp.PostprocessOptions(tag_replacement={"raiden_shogun": "noop"})
    assert tags_of(pp.postprocess_tags([S("raiden_shogun", 0.9, "Character")], stale)) == ["raiden shogun"]


def test_tag_replacement_keeps_score_and_category() -> None:
    options = pp.PostprocessOptions(tag_replacement={"1girl": "1girls"})
    out = pp.postprocess_tags([S("1girl", 0.87, "General")], options)
    assert out == [S("1girls", 0.87, "General")]


# --------------------------------------------------------------------------
# dedupe
# --------------------------------------------------------------------------


def test_duplicates_are_removed_keeping_the_first_occurrence() -> None:
    # replacement table keys are in the **underscore-removed** form (the replacement happens after remove_underscore)
    options = pp.PostprocessOptions(tag_replacement={"a tag": "1girl"})
    items = [S("1girl", 0.9, "General"), S("a_tag", 0.5, "General")]
    out = pp.postprocess_tags(items, options)
    assert tags_of(out) == ["1girl"]
    assert out[0].score == 0.9


def test_expand_can_collide_and_is_deduped() -> None:
    options = pp.PostprocessOptions(character_tag_expand=True)
    items = [S("keqing_(genshin_impact)", 0.9, "Character"), S("genshin impact", 0.8, "Copyright")]
    assert tags_of(pp.postprocess_tags(items, options)) == ["keqing", "genshin impact"]


# --------------------------------------------------------------------------
# sort_danbooru
# --------------------------------------------------------------------------


def test_sort_danbooru_orders_by_category_then_score() -> None:
    options = pp.PostprocessOptions(sort_danbooru=True)
    items = [
        S("genshin impact", 0.7, "Copyright"),
        S("keqing", 0.9, "Character"),
        S("solo", 0.6, "General"),
        S("1girl", 0.8, "General"),
    ]
    assert tags_of(pp.postprocess_tags(items, options)) == [
        "1girl",
        "solo",
        "keqing",
        "genshin impact",
    ]


def test_sort_danbooru_is_off_by_default() -> None:
    items = [S("genshin impact", 0.7, "Copyright"), S("1girl", 0.8, "General")]
    assert tags_of(pp.postprocess_tags(items)) == ["genshin impact", "1girl"]


def test_sort_danbooru_accepts_a_custom_order() -> None:
    options = pp.PostprocessOptions(sort_danbooru=True, danbooru_order=("Character", "General"))
    items = [S("1girl", 0.9, "General"), S("keqing", 0.1, "Character")]
    assert tags_of(pp.postprocess_tags(items, options)) == ["keqing", "1girl"]


def test_danbooru_order_matches_the_frozen_tuple() -> None:
    assert pp.DANBOORU_ORDER == (
        "Rating",
        "Meta",
        "Quality",
        "General",
        "Character",
        "Copyright",
        "Artist",
    )


# --------------------------------------------------------------------------
# rating strategies
# --------------------------------------------------------------------------


RATINGS = [S("general", 0.10, "Rating"), S("explicit", 0.90, "Rating"), S("questionable", 0.20, "Rating")]
BODY = [S("1girl", 0.80, "General")]


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("none", ["1girl"]),
        ("first", ["explicit", "1girl"]),
        ("last", ["1girl", "explicit"]),
    ],
)
def test_rating_modes(mode: str, expected: list[str]) -> None:
    options = pp.PostprocessOptions(rating_mode=mode)
    assert tags_of(pp.postprocess_tags(BODY + RATINGS, options)) == expected


def test_rating_uses_argmax_not_threshold() -> None:
    """rating does not go through the threshold: a 0.10 rating is considered too, but only argmax survives."""
    options = pp.PostprocessOptions(rating_mode="last")
    out = pp.postprocess_tags([S("general", 0.01, "Rating"), S("1girl", 0.8, "General")], options)
    assert tags_of(out) == ["1girl", "general"]


def test_rating_first_beats_character_tags_first() -> None:
    options = pp.PostprocessOptions(rating_mode="first", character_tags_first=True)
    items = [S("keqing", 0.9, "Character"), S("explicit", 0.5, "Rating")]
    assert tags_of(pp.postprocess_tags(items, options)) == ["explicit", "keqing"]


def test_unknown_rating_mode_is_rejected() -> None:
    with pytest.raises(ValueError):
        pp.postprocess_tags(BODY, pp.PostprocessOptions(rating_mode="middle"))


def test_rating_modes_constant() -> None:
    assert pp.RATING_MODES == ("none", "first", "last")


# --------------------------------------------------------------------------
# apply_thresholds
# --------------------------------------------------------------------------


def test_apply_thresholds_uses_035_by_default() -> None:
    assert pp.DEFAULT_THRESHOLD == 0.35
    items = [S("keep", 0.35, "General"), S("drop", 0.3499, "General")]
    assert tags_of(pp.apply_thresholds(items)) == ["keep"]


def test_apply_thresholds_is_per_category() -> None:
    items = [S("1girl", 0.40, "General"), S("keqing", 0.40, "Character")]
    kept = pp.apply_thresholds(items, {"general": 0.30, "character": 0.60})
    assert tags_of(kept) == ["1girl"]


def test_apply_thresholds_accepts_capitalised_and_numeric_keys() -> None:
    items = [S("x", 0.5, "General")]
    assert tags_of(pp.apply_thresholds(items, {"GENERAL": 0.9})) == []
    assert tags_of(pp.apply_thresholds(items, {"0": 0.9})) == []
    assert tags_of(pp.apply_thresholds(items, {"General": 0.1})) == ["x"]


def test_apply_thresholds_passes_all_rating_candidates_through() -> None:
    """Which rating to keep is decided by postprocess_tags's rating_mode, not by the threshold."""
    assert len(pp.apply_thresholds(RATINGS)) == 3


def test_apply_thresholds_rejects_non_numeric_values() -> None:
    with pytest.raises(ValueError):
        pp.apply_thresholds([S("x", 0.5, "General")], {"general": "high"})


def test_apply_thresholds_preserves_input_order() -> None:
    items = [S("b", 0.9, "General"), S("a", 0.8, "General")]
    assert tags_of(pp.apply_thresholds(items)) == ["b", "a"]


def test_threshold_then_postprocess_pipeline() -> None:
    """The spec §4 preview pipeline: thresholds and postprocess are two fields."""
    raw = [
        S("1girl", 0.99, "General"),
        S("raiden_shogun", 0.93, "Character"),
        S("explicit", 0.90, "Rating"),
        S("noise", 0.05, "General"),
    ]
    kept = pp.apply_thresholds(raw, {"general": 0.35, "character": 0.35})
    final = pp.postprocess_tags(kept, pp.PostprocessOptions(always_first_tags=("1girl",), rating_mode="last"))
    assert tags_of(final) == ["1girl", "raiden shogun", "explicit"]
    assert pp.format_tags(final) == "1girl, raiden shogun, explicit"


# --------------------------------------------------------------------------
# format_tags
# --------------------------------------------------------------------------


def test_format_tags_uses_the_sidecar_separator() -> None:
    assert pp.TAG_SEPARATOR == ", "
    assert pp.format_tags([S("a", 1.0, "General"), S("b", 0.5, "General")]) == "a, b"


def test_format_tags_of_nothing_is_empty() -> None:
    assert pp.format_tags([]) == ""


# --------------------------------------------------------------------------
# Purity
# --------------------------------------------------------------------------


def test_postprocess_does_not_mutate_its_input() -> None:
    items = [S("raiden_shogun", 0.9, "Character"), S("1girl", 0.8, "General")]
    snapshot = list(items)
    pp.postprocess_tags(items, pp.PostprocessOptions(always_first_tags=("1girl",), rating_mode="last"))
    pp.apply_thresholds(items)
    assert items == snapshot


def test_postprocess_has_no_filesystem_imports() -> None:
    """Post-processing is a pure function: it must not import any module that touches the filesystem / subprocesses."""
    source = Path(inspect.getsourcefile(pp)).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    forbidden = {"os", "io", "pathlib", "shutil", "tempfile", "subprocess", "glob", "core"}
    assert not (imported & forbidden), "postprocess must be a pure function; extra imports: %s" % sorted(imported & forbidden)
