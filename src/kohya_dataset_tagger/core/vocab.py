"""WD14 vocabulary - the data source for tag autocomplete and Danbooru-order sorting.

CSV contract (p0-spec §3.5, .github/memory/wd14-tagger.md:57-75)
----------------------------------------------------------------
Column names `tag_id,name,category,count`; `category` is a **numeric string**:
0=General 1=Artist 3=Copyright 4=Character 5=Meta 9=Rating (the first 4 labels are rating).
`count` is the tag's popularity, used to sort autocomplete candidates.

The filename is **not uniform**: in the HF cache it is `selected_tags.csv`, while ComfyUI's copy is `wd-vit-tagger-v3.csv`.
So it is discovered as "any `*.csv` in the same directory" rather than hardcoding the filename. The HF cache's `snapshots/<sha>/` directory
changes on re-download - callers must discover it with a wildcard, not hardcode the sha.

Layering constraint (p0-spec §3.0)
----------------------------------
This module is a leaf of `core/`: it must not import other core modules, nor import FastAPI.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

#: Danbooru category order (rating first, artist last). The index is `order_key`'s category priority.
DANBOORU_ORDER: tuple[str, ...] = (
    "Rating",
    "Meta",
    "Quality",
    "General",
    "Character",
    "Copyright",
    "Artist",
)

#: Category numeric string in the CSV -> category name. `Quality` is a placeholder in DANBOORU_ORDER, but WD14's CSV never produces it.
CATEGORY_NAMES: dict[str, str] = {
    "0": "General",
    "1": "Artist",
    "3": "Copyright",
    "4": "Character",
    "5": "Meta",
    "9": "Rating",
}

#: Required CSV columns (`tag_id` is only an identifier, takes no part in the logic, and can be missing).
_REQUIRED_COLUMNS = ("name", "category", "count")


@dataclass(frozen=True)
class TagInfo:
    """One row of the vocabulary. `category` is the raw numeric string from the CSV (`"0"` = General)."""

    name: str
    category: str
    count: int


class Vocabulary:
    """An immutable vocabulary. Every lookup is **case-insensitive** (WD14's tags are always lowercase)."""

    def __init__(self, tags: Iterable[TagInfo]) -> None:
        self._by_name: dict[str, TagInfo] = {}
        #: The CSV row order = the tagger's label order, which `order_key` uses to order within a category
        #: (the 4 rating rows must keep the label order general, sensitive, questionable, explicit).
        self._row_index: dict[str, int] = {}
        for index, tag in enumerate(tags):
            key = tag.name.strip().lower()
            if key in self._by_name:
                continue
            self._by_name[key] = tag
            self._row_index[key] = index

    # ------------------------------------------------------------------ Construction

    @classmethod
    def from_csv(cls, path: Path) -> "Vocabulary":
        """Read the vocabulary from a CSV. `path` may be a CSV file, a model file, or a **directory**.

        A directory / non-CSV file is discovered as "any `*.csv` in the same directory"; more than one CSV in the directory raises
        `ValueError` (better an error than silently picking the wrong vocabulary - a wrong category poisons sorting and thresholds).
        """
        csv_path = _resolve_csv(Path(path))
        tags: list[TagInfo] = []
        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            columns = set(reader.fieldnames or ())
            missing = [name for name in _REQUIRED_COLUMNS if name not in columns]
            if missing:
                raise ValueError(
                    "vocabulary CSV is missing columns %s: %s" % (missing, csv_path)
                )
            for row in reader:
                name = (row.get("name") or "").strip()
                if not name:
                    continue
                category = (row.get("category") or "").strip()
                tags.append(
                    TagInfo(name=name, category=category, count=_parse_count(row.get("count")))
                )
        return cls(tags)

    # ------------------------------------------------------------------ Query

    def __len__(self) -> int:
        return len(self._by_name)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name.strip().lower() in self._by_name

    def get(self, name: str) -> TagInfo | None:
        if not isinstance(name, str):
            return None
        return self._by_name.get(name.strip().lower())

    def search(self, prefix: str, limit: int = 50) -> list[TagInfo]:
        """Prefix autocomplete candidates: popularity (`count`) descending, ties by name ascending, returning the first `limit`.

        `prefix=""` is "the N hottest tags in the whole vocabulary". Case-insensitive.
        """
        if limit <= 0:
            return []
        needle = prefix.strip().lower()
        matches = [tag for key, tag in self._by_name.items() if key.startswith(needle)]
        matches.sort(key=lambda tag: (-tag.count, tag.name))
        return matches[:limit]

    def order_key(self, name: str) -> tuple:
        """`sorted(tags, key=vocab.order_key)` -> Danbooru order (rating -> … -> artist).

        Category is the primary key, **the vocabulary's own row order** is the secondary key, and the name breaks ties; tags absent from the vocabulary sort last.

        The secondary key is the row order rather than `count`: the CSV row order is precisely the tagger's label order, and the
        order of the 4 rating rows (general -> sensitive -> questionable -> explicit) is meaningful; sorting by popularity would scramble it
        (sensitive is more popular than general). Popularity is used only to sort autocomplete candidates (`search`).
        """
        tag = self.get(name)
        if tag is None:
            return (len(DANBOORU_ORDER), len(self._by_name), str(name).lower())
        return (
            _category_rank(tag.category),
            self._row_index.get(tag.name.strip().lower(), 0),
            tag.name.lower(),
        )


def _parse_count(raw: object) -> int:
    """`count` column tolerance: empty / non-numeric is treated as 0, so one dirty row does not ruin the whole vocabulary."""
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return 0


def _category_rank(category: str) -> int:
    """Category -> DANBOORU_ORDER index; an unknown category sorts last.

    Accepts both a numeric string (`"0"`, as the CSV has it) and a category name (`"General"`), so callers can sort with either representation.
    """
    name = CATEGORY_NAMES.get(category, category)
    try:
        return DANBOORU_ORDER.index(name)
    except ValueError:
        return len(DANBOORU_ORDER)


def _resolve_csv(path: Path) -> Path:
    """`path` -> the real CSV file; discovered as `*.csv` in the same directory, with no hardcoded filename."""
    if path.is_dir():
        candidates = sorted(entry for entry in path.glob("*.csv") if entry.is_file())
        if not candidates:
            raise FileNotFoundError("no *.csv vocabulary in the directory: %s" % path)
        if len(candidates) > 1:
            raise ValueError(
                "multiple *.csv in the directory, cannot tell which vocabulary to use: %s" % [str(entry) for entry in candidates]
            )
        return candidates[0]
    if path.is_file():
        if path.suffix.lower() == ".csv":
            return path
        # Given a same-directory file like model.onnx: discover it as "any *.csv in the same directory".
        return _resolve_csv(path.parent)
    raise FileNotFoundError("vocabulary path does not exist: %s" % path)
