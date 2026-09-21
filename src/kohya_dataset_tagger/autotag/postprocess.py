"""tag post-processing: **pure functions**, no filesystem access.

Semantics aligned with sd-scripts `finetune/tag_images_by_wd14_tagger.py` (step-by-step
comparison in the comments), plus the "sort by DANBOORU order" required by spec §4 and the
`escape_parens` frozen in §4.1.

The fixed order inside `postprocess_tags` (this order is itself a contract, do not casually change it):

    1. character_tag_expand   split the trailing parenthesis of a character tag into two tags
                              (needs the **not-yet-underscore-removed** original form)
    2. remove_underscore      underscore -> space (only for len(tag) > 3, aligned with sd-scripts)
    3. escape_parens          backslash-escape left/right parentheses (aligned with the danbooru caption convention, §4.1)
    4. undesired_tags         block list (the comparison happens **after** 1-3, see wd14-tagger.md)
    5. tag_replacement        replacement table (after character_tag_expand, see wd14-tagger.md)
    6. dedupe                 order-preserving; the input is by descending score, so the highest-scoring one survives
    7. sort_danbooru          optional; only establishes the **base** order, yields to the explicit instructions in 8-10
    8. character_tags_first   move character tags to the front as a block (order-preserving)
    9. rating                 none / first / last; rating goes through argmax, only one survives
    10. always_first_tags     force to the front, runs last, highest priority

Step 3's position is deliberate: it runs **before** undesired_tags / tag_replacement /
always_first_tags, so all three user-filled tables are written in "the form visible in the
caption" (parentheses already escaped), consistent across the three; it also lands where the
spec requires, "after remove_underscore, before sorting".

Mapping from field names to the §4.1 frozen request body (the HTTP key names are frozen, this
module's field names need not match):

    remove_underscore / undesired_tags / always_first_tags / character_tags_first /
    character_tag_expand / escape_parens   -- same name
    "rating"                               -- this module calls it rating_mode
    (tag_replacement / sort_danbooru / danbooru_order are local extensions, not in the frozen request body)

Threshold filtering is not in `postprocess_tags` but in `apply_thresholds`:
the HTTP contract splits `thresholds` and `postprocess` into two fields (p0-spec §4),
so this splits into two functions too; the caller runs `apply_thresholds` then `postprocess_tags`.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from .base import CHARACTER_CATEGORY, RATING_CATEGORY, TagScore, category_name

#: The caption sidecar separator. Per dataset-contract: the trainer hardcodes ", " when reassembling.
TAG_SEPARATOR = ", "

#: Danbooru category order (p0-spec §3.5).
#: **Must match core/vocab.py::DANBOORU_ORDER** -- autotag cannot import core (§3.0),
#: so each holds a copy; change one and you must change the other (acceptance criterion A16 watches exactly this duplication).
DANBOORU_ORDER: tuple[str, ...] = (
    "Rating",
    "Meta",
    "Quality",
    "General",
    "Character",
    "Copyright",
    "Artist",
)

#: Default category threshold. sd-scripts' `--thresh` defaults to 0.35, and omitted categories follow `--thresh`.
#: Spec §4.1 explicitly requires not giving character/copyright a higher default value.
DEFAULT_THRESHOLD = 0.35

#: The length threshold for `remove_underscore`, copied from sd-scripts:
#: `tag.replace("_", " ") if len(tag) > 3 else tag`
#: The point is to protect kaomoji tags -- the local 10861-row vocabulary has 12 tags
#: (`^_^`, `>_<`, etc.) that are at most 3 characters long and contain an underscore
#: (measured); without a threshold they would be destroyed into `^ ^`.
UNDERSCORE_MIN_LENGTH = 4

#: The three rating strategies.
RATING_MODES: tuple[str, ...] = ("none", "first", "last")

#: The backslash used to escape danbooru parentheses. Named separately so the replaces below are obvious at a glance.
ESCAPE_PREFIX = "\\"


@dataclass(frozen=True)
class PostprocessOptions:
    """Post-processing options. Field names correspond one-to-one with the "post-processing options" table in wd14-tagger.md."""

    #: Underscore -> space. **Default True**: the model gives `raiden_shogun`, the dataset caption writes `raiden shogun`.
    remove_underscore: bool = True
    #: Global block list (written in the form visible in the caption, i.e. parentheses already escaped).
    undesired_tags: tuple[str, ...] = ()
    #: If present in the result, force to the front (e.g. `1girl`; also in the escaped form).
    always_first_tags: tuple[str, ...] = ()
    #: Move character tags to the front as a block.
    character_tags_first: bool = False
    #: `chara_(series)` -> `chara` + `series`.
    character_tag_expand: bool = False
    #: Replacement table; keys are **underscore-removed, escaped** tag names.
    tag_replacement: Mapping[str, str] = field(default_factory=dict)
    #: Backslash-escape left/right parentheses. **Default True** (spec §4.1):
    #: in the dataset, 1049 files write the character tag as `hu tao \(genshin impact\)`,
    #: and the repository's own inference prompt configs/sample_prompts.txt:4 also writes `mika \(blue archive\)`.
    #: **Applies only to tagger output**: user-typed tags may carry weight syntax like `(masterpiece:1.2)`;
    #: that path must not go through this function (or pass escape_parens=False explicitly).
    escape_parens: bool = True
    #: Sort by DANBOORU_ORDER (the option required by spec §4).
    sort_danbooru: bool = False
    #: rating strategy: none / first / last (corresponds to `rating` in the §4.1 request body).
    #: Default none -- consistent with sd-scripts (`--use_rating_tags` is off by default),
    #: and consistent with the local dataset: sampling 400 captions, **0** carry a rating tag (measured).
    rating_mode: str = "none"
    #: The category order used for sorting, default DANBOORU_ORDER; core/vocab.py may pass in its own copy.
    danbooru_order: tuple[str, ...] = DANBOORU_ORDER


def _normalise_thresholds(thresholds: Mapping[str, float] | None) -> dict[str, float]:
    """Normalize the threshold table's keys into lowercase category names.

    Tolerates the three spellings `{"general": ..}` / `{"General": ..}` / `{"0": ..}` (numeric
    code) -- the HTTP request body is JSON, so key casing cannot rely on a convention.
    """
    table: dict[str, float] = {}
    for key, value in (thresholds or {}).items():
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise ValueError("threshold must be a number: %r -> %r" % (key, value)) from None
        table[category_name(key).lower()] = number
    return table


def apply_thresholds(
    tags: Sequence[TagScore],
    thresholds: Mapping[str, float] | None = None,
) -> list[TagScore]:
    """Filter tags by category threshold (`score >= threshold`).

    **rating does not go through the threshold**: wd14-tagger.md specifies that rating uses
    argmax to pick one. Here all rating candidates pass through untouched, and
    `postprocess_tags`'s `rating_mode` decides which to keep.
    """
    table = _normalise_thresholds(thresholds)
    kept: list[TagScore] = []
    for item in tags:
        if item.category == RATING_CATEGORY:
            kept.append(item)
            continue
        if item.score >= table.get(item.category.lower(), DEFAULT_THRESHOLD):
            kept.append(item)
    return kept


def _remove_underscore(tag: str) -> str:
    return tag.replace("_", " ") if len(tag) >= UNDERSCORE_MIN_LENGTH else tag


def _escape_parens(tag: str) -> str:
    """Backslash-escape left/right parentheses to get the danbooru caption form.

    Following spec §4.1's literal rule: **all** left/right parentheses are escaped, with no
    "skip if already escaped" check. This is safe on the real vocabulary: only 5 tags contain
    a backslash, all kaomoji (backslash + letter + slash), and none of them contain parentheses;
    the 1598 tags containing parentheses contain no backslash -- the two sets are disjoint (measured).
    """
    return tag.replace("(", ESCAPE_PREFIX + "(").replace(")", ESCAPE_PREFIX + ")")


def _with_tag(item: TagScore, tag: str) -> TagScore:
    return TagScore(tag=tag, score=item.score, category=item.category)


def _expand_character_tag(item: TagScore) -> list[TagScore]:
    """`chara_name_(series)` -> `chara_name` + `series`.

    Copied from sd-scripts `expand_character_tags`:
    `chara_name_(costume)_(series)` -> `chara_name_(costume)` + `series`.

    Difference: sd-scripts stuffs the expansion result into **one** string element containing the
    separator; here it returns **two** TagScores. The reassembled caption text is identical, but
    the editor can see the two tags' scores separately.
    """
    tag = item.tag
    if item.category != CHARACTER_CATEGORY or not tag.endswith(")"):
        return [item]
    parts = tag.split("(")
    if len(parts) < 2:
        return [item]
    character = "(".join(parts[:-1])
    if character.endswith("_"):
        character = character[:-1]
    series = parts[-1].replace(")", "")
    if not character or not series:
        return [item]
    return [_with_tag(item, character), _with_tag(item, series)]


def _dedupe(tags: Sequence[TagScore]) -> list[TagScore]:
    """Dedupe, order-preserving: keep only the **first** occurrence of a tag (input is by descending score, so the highest score stays)."""
    seen: set[str] = set()
    out: list[TagScore] = []
    for item in tags:
        if item.tag in seen:
            continue
        seen.add(item.tag)
        out.append(item)
    return out


def _sort_danbooru(tags: Sequence[TagScore], order: Sequence[str]) -> list[TagScore]:
    """Sort by danbooru category order; within a category, by descending score.

    Note the difference from core/vocab.py: `Vocabulary.order_key` uses (category rank, -count),
    while here there is no CSV count to read (the tagger does not import core), so it uses
    (category rank, -score).
    """
    ranks = {name: index for index, name in enumerate(order)}
    fallback = len(ranks)
    indexed = list(enumerate(tags))
    indexed.sort(key=lambda pair: (ranks.get(pair[1].category, fallback), -pair[1].score, pair[0]))
    return [item for _, item in indexed]


def _place_rating(tags: Sequence[TagScore], mode: str) -> list[TagScore]:
    """rating goes through argmax to pick one, then mode puts it first / last / drops it."""
    ratings = [item for item in tags if item.category == RATING_CATEGORY]
    others = [item for item in tags if item.category != RATING_CATEGORY]
    if mode == "none" or not ratings:
        return others
    best = max(ratings, key=lambda item: item.score)
    return [best] + others if mode == "first" else others + [best]


def postprocess_tags(
    tags: Sequence[TagScore],
    options: PostprocessOptions | None = None,
) -> list[TagScore]:
    """Process the tag list per `PostprocessOptions` and return a new list (does not mutate the argument)."""
    opts = options if options is not None else PostprocessOptions()
    if opts.rating_mode not in RATING_MODES:
        raise ValueError("rating_mode must be one of %s, got %r" % (list(RATING_MODES), opts.rating_mode))

    items: list[TagScore] = list(tags)

    # 1. character_tag_expand (must come before underscore removal: the split relies on the original (_...) form)
    if opts.character_tag_expand:
        expanded: list[TagScore] = []
        for item in items:
            expanded.extend(_expand_character_tag(item))
        items = expanded

    # 2. remove_underscore
    if opts.remove_underscore:
        items = [_with_tag(item, _remove_underscore(item.tag)) for item in items]

    # 3. escape_parens (before 4-5 and 10, so the user-filled tables all use the form visible in the caption)
    if opts.escape_parens:
        items = [_with_tag(item, _escape_parens(item.tag)) for item in items]

    # 4. undesired_tags
    if opts.undesired_tags:
        blocked = set(opts.undesired_tags)
        items = [item for item in items if item.tag not in blocked]

    # 5. tag_replacement (after character_tag_expand)
    if opts.tag_replacement:
        items = [_with_tag(item, opts.tag_replacement.get(item.tag, item.tag)) for item in items]

    # 6. dedupe
    items = _dedupe(items)

    # 7. sorting (optional, establishes only the base order)
    if opts.sort_danbooru:
        items = _sort_danbooru(items, opts.danbooru_order)

    # 8. move character tags to the front as a block (order-preserving)
    if opts.character_tags_first:
        characters = [item for item in items if item.category == CHARACTER_CATEGORY]
        others = [item for item in items if item.category != CHARACTER_CATEGORY]
        items = characters + others

    # 9. rating first / last / not emitted
    items = _place_rating(items, opts.rating_mode)

    # 10. always_first_tags runs last, so it always wins
    if opts.always_first_tags:
        front: list[TagScore] = []
        rest = list(items)
        for wanted in opts.always_first_tags:
            for index, item in enumerate(rest):
                if item.tag == wanted:
                    front.append(item)
                    del rest[index]
                    break
        items = front + rest

    return items


def format_tags(tags: Sequence[TagScore]) -> str:
    """Assemble the caption sidecar's text form: separated by comma + space, with no trailing newline."""
    return TAG_SEPARATOR.join(item.tag for item in tags)
