"""Tag frequency and autocomplete: GET `/api/tags/frequency`, GET `/api/tags/autocomplete` (§4).

Two clearly separated data sources
----------------------------------
- The source of truth for **frequency** is the dataset itself: read each image's caption -> `captions.split_tags` (the same
  rule as the trainer), so "the tag that appears most in the directory" is a statistic of exactly what training will read.
- The source of truth for **autocomplete** is the WD14 vocabulary: `config.model_search_roots` -> `autotag.registry`
  discovers local models -> their `selected_tags.csv` -> `core.vocab.Vocabulary`.

Autocomplete's match semantics (§4.10 frozen)
---------------------------------------------
q does a **case-insensitive substring match**, ordered: prefix hit first -> count descending -> tag name lexicographic.
Why not prefix match: character tags in the vocabulary look like ganyu_(genshin_impact), and **no** tag starts with
genshin (even genshin_impact is not found, because "(_" still precedes it) - what the user remembers is "this character is
from Genshin", not its exact start in the vocabulary. Measurements in §4.10; the linear scan over 10,861 entries is negligible.

Vocabulary cache
----------------
`Vocabulary.from_csv` reads 10,861 rows, cached by `(path, mtime_ns)`: re-downloading the vocabulary changes the
mtime and the cache expires automatically. When no model can be discovered it returns empty suggestions rather than a 5xx -
autocomplete is an auxiliary feature and must not make the input box pop an error.

The two representations of category (§4.5 ① frozen)
---------------------------------------------------
- `category`     = **the category name** (`"General"` / `"Character"` / `"Rating"` ...), which the frontend displays directly;
- `category_id`  = the raw CSV digit string (`"0"` / `"4"` / `"9"`), for callers that need to align with the vocabulary.

The internal representation (`core.vocab.TagInfo.category`) **stays the digit string** - translating is the API layer's job, not the core layer's.
A field named `category` should be the category name: `tags.js:paintSuggest()` shows it to the user directly, and
returning numeric codes would put rows like `1girl 0 5113288` in the dropdown.
"""
from __future__ import annotations

import logging
import threading
from collections import Counter
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query

from .. import config
from ..core import captions, paths, vocab as vocab_module
from ..autotag import registry
from . import ensure_roots_configured, error_response, resolve_query_path

logger = logging.getLogger("kohya_dataset_tagger.api.tags")

router = APIRouter(prefix="/api", tags=["tags"])

__all__ = ["router", "get_vocabulary", "vocabulary_path"]

#: Upper bound on autocomplete suggestions: nobody would pick the 200th from the dropdown anyway.
MAX_SUGGESTIONS = 200


def _all_tags(vocabulary: vocab_module.Vocabulary) -> list[vocab_module.TagInfo]:
    """The whole vocabulary, by descending popularity and by name within the same popularity (= search's order).

    The Vocabulary frozen in §3.5 has only one lookup entry point, search(prefix, limit), and no iterator,
    while "an empty prefix matches everything" is search's existing semantic - so search("", len(vocabulary)) is the whole vocabulary.
    Use the public API rather than reaching into _by_name: that is core's private structure, and reaching in would silently break when someone refactors.

    When the counts disagree, log a warning and continue (better a few missing suggestions than a 5xx): autocomplete is an auxiliary feature.
    """
    everything = vocabulary.search("", len(vocabulary))
    if len(everything) != len(vocabulary):
        logger.warning(
            "Vocabulary has %d entries but search('') returned only %d, autocomplete may miss suggestions", len(vocabulary), len(everything)
        )
    return everything


def _match_tags(
    vocabulary: vocab_module.Vocabulary, needle: str, limit: int
) -> list[vocab_module.TagInfo]:
    """§4.10's matching and ordering: case-insensitive substring match, prefix hits first.

    Sort key = (whether it is a prefix hit, count descending, name lexicographic). The third term keeps the result deterministic
    (it will not vary with dict order for the same count and group).

    With needle="" every tag "matches" and is a prefix hit, so it degenerates into a whole-vocabulary sort by (**-count, name**) -
    which is q=""'s original semantic (the hottest few), no regression.
    """
    matched = [info for info in _all_tags(vocabulary) if needle in info.name.lower()]
    matched.sort(
        key=lambda info: (0 if info.name.lower().startswith(needle) else 1, -info.count, info.name)
    )
    return matched[:limit]

#: `(path, mtime_ns)` -> Vocabulary. An in-process cache.
_VOCAB_CACHE: dict[str, tuple[int, vocab_module.Vocabulary]] = {}
_VOCAB_LOCK = threading.Lock()


def _under_roots(candidate: Path) -> bool:
    """Whether a child entry is still inside the allowlist (the same semantics as api/fs.py)."""
    try:
        paths.resolve_under_roots(candidate, must_exist=False)
    except paths.PathOutsideRoots:
        return False
    return True


def vocabulary_path() -> Path | None:
    """Path to the local vocabulary CSV; returns None when no usable model exists."""
    roots = config.current().model_search_roots
    for model_id in registry.discover(roots):
        try:
            _onnx, vocab_path = registry.resolve(model_id, roots)
        except registry.RegistryError as exc:
            logger.debug("Failed to resolve model %s: %s", model_id, exc)
            continue
        return Path(vocab_path)
    return None


def get_vocabulary() -> vocab_module.Vocabulary | None:
    """The current vocabulary (with mtime cache); returns None and warns only once when unavailable."""
    path = vocabulary_path()
    if path is None:
        return None
    key = str(path)
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        return None

    with _VOCAB_LOCK:
        cached = _VOCAB_CACHE.get(key)
        if cached is not None and cached[0] == mtime:
            return cached[1]
        try:
            vocabulary = vocab_module.Vocabulary.from_csv(path)
        except (OSError, ValueError) as exc:
            logger.warning("Failed to read vocabulary %s: %s", path, exc)
            return None
        _VOCAB_CACHE[key] = (mtime, vocabulary)
        return vocabulary


@router.get("/tags/frequency")
def tag_frequency(
    path: str = Query("", description="Directory to tally; empty = the first root in the allowlist"),
    recursive: bool = Query(False, description="Collect images recursively"),
) -> Any:
    """Tag frequency within a directory: `{total_images, tags:[{tag,count}]}` (by count descending, name within the same count)."""
    ensure_roots_configured()
    try:
        target = resolve_query_path(path)
    except paths.PathOutsideRoots as exc:
        return error_response(403, exc.code, str(exc), params=exc.params)
    except FileNotFoundError as exc:
        return error_response(404, "not_found", str(exc), params={"path": str(path)})
    if not target.is_dir():
        return error_response(
            400, "not_a_directory", "Not a directory: %s" % target, params={"path": str(target)}
        )

    counter: Counter[str] = Counter()
    total_images = 0
    for image in paths.iter_images(target, recursive=recursive):
        if not _under_roots(image):
            continue
        total_images += 1
        try:
            text, _multiline = captions.read_caption(image)
        except OSError as exc:  # a single bad file must not fail the whole table
            logger.warning("Failed to read caption %s: %s", image, exc)
            continue
        counter.update(captions.split_tags(text))

    ordered = sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    return {
        "total_images": total_images,
        "tags": [{"tag": tag, "count": count} for tag, count in ordered],
    }


@router.get("/tags/autocomplete")
def tag_autocomplete(
    q: str = Query("", description="Substring (case-insensitive); empty = the hottest few in the vocabulary"),
    limit: int = Query(50, description="Suggestion cap (1-%d)" % MAX_SUGGESTIONS),
) -> Any:
    """Vocabulary autocomplete (§4.10): `{suggestions:[{tag,category,category_id,count}]}`.

    `q` does a **case-insensitive substring match** (not a prefix, §4.10 frozen), ordered
    **prefix hits first -> count descending -> tag name lexicographic**; `q=""` is still "the hottest few in the vocabulary".
    Measured motivation: character tags look like `ganyu_(genshin_impact)`, and no tag starts with `genshin`.

    `category` is the category **name** (`"General"`), while `category_id` is the CSV digit string - the frontend displays the former directly.
    """
    vocabulary = get_vocabulary()
    if vocabulary is None:
        return {"suggestions": []}
    bounded = max(1, min(int(limit), MAX_SUGGESTIONS))
    needle = (q or "").strip().lower()
    return {
        "suggestions": [
            {
                "tag": info.name,
                "category": vocab_module.CATEGORY_NAMES.get(info.category, info.category),
                "category_id": info.category,
                "count": info.count,
            }
            for info in _match_tags(vocabulary, needle, bounded)
        ]
    }
