"""Public types and category constants for autotag.

Dependency direction (p0-spec §3.0, mandatory):

    api/*  ->  autotag/*        (autotag only takes `pathlib.Path` and `PIL.Image`)
    autotag/*  -/->  core/*     (autotag **must not** import core, nor import FastAPI)

So this package holds its own category mapping instead of reading `core/vocab.py`.
Constants that exist on both sides (`CATEGORY_NAMES` / `DANBOORU_ORDER`) must be changed in sync.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence, runtime_checkable

#: The category column of `selected_tags.csv` is a **numeric string**.
#: Per p0-spec §3.5: "0=General 1=Artist 3=Copyright 4=Character 5=Meta 9=Rating".
CATEGORY_NAMES: dict[int, str] = {
    0: "General",
    1: "Artist",
    3: "Copyright",
    4: "Character",
    5: "Meta",
    9: "Rating",
}

#: Name -> numeric code, for reverse lookup.
CATEGORY_CODES: dict[str, int] = {name: code for code, name in CATEGORY_NAMES.items()}

#: Only these three categories actually appear in the local WD14 v3 vocabulary (measured 10861 rows = 4 rating + 8106 general + 2751 character).
RATING_CATEGORY = "Rating"
CHARACTER_CATEGORY = "Character"
GENERAL_CATEGORY = "General"


def category_name(code: str | int) -> str:
    """Turn the numeric category from the CSV into a name.

    Unrecognized codes are returned as their string form (lose no information, pretend no knowledge).
    """
    try:
        return CATEGORY_NAMES[int(code)]
    except (KeyError, TypeError, ValueError):
        return str(code)


@dataclass(frozen=True)
class TagScore:
    """The inference result for one tag.

    `category` is a **name** (`General` / `Character` / `Rating` / ...), not the
    numeric code from the CSV -- the name is what `DANBOORU_ORDER` and
    `character_tags_first` use.
    """

    tag: str
    score: float
    category: str


@runtime_checkable
class Tagger(Protocol):
    """The unified protocol for taggers.

    `runtime_checkable` only lets tests do an `isinstance` check; it changes no semantics.
    """

    name: str

    def predict(self, image: Path) -> list[TagScore]:  # pragma: no cover - protocol declaration
        ...

    def predict_batch(self, images: Sequence[Path]) -> list[list[TagScore]]:  # pragma: no cover
        ...
