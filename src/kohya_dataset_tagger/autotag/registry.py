"""Model discovery: model id -> local file path.

Two hard rules (local-model-inventory.md, "notes about paths"):

1. **Do not hardcode the HF cache snapshot hash.** `snapshots/<sha>/` changes when a model is
   re-downloaded, so discover by glob wildcard; when one repo has several snapshots, take the
   one with the newest mtime.
2. **Do not hardcode the CSV file name.** HF calls it `selected_tags.csv`, the ComfyUI copy is
   called `wd-vit-tagger-v3.csv`, so find it by glob (prefer `selected_tags.csv`, otherwise the
   lexicographically first).

Recognizes **two layouts** (p0-spec §4.6; both must be recognized, otherwise the fallback
"if it will not download, download it yourself and drop it in" is a lie):

    <root>/models--<org>--<name>/snapshots/*/model.onnx   # HF cache (the shape hf_hub_download produces)
    <root>/<model_id>/model.onnx                          # flat (what we download / what the user drops in)

A flat-layout vocabulary = any `*.csv` in the **same directory** as the onnx (so a renamed copy
like `wd-vit-tagger-v3.csv` is recognized too). Between search roots the caller's order applies;
inside a root, **the specialised glob wins and the flat layout is the fallback**.

This module does not import onnxruntime (it only discovers paths), so it is safe to call anywhere.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

#: The canonical vocabulary file name in the HF cache; prefer it when present (a snapshot directory should not hold a second vocabulary).
_PREFERRED_VOCAB_NAME = "selected_tags.csv"

#: The generic flat layout (p0-spec §4.6): `<root>/<model_id>/model.onnx` + any `*.csv` in the same directory.
#: This is the shape download.py writes; it is also how a user's manual download is recognized.
FLAT_ONNX_NAME = "model.onnx"
FLAT_VOCAB_GLOB = "*.csv"


class RegistryError(LookupError):
    """Base class for model discovery / resolution failures.

    Inherits `LookupError`: callers can catch just this one type.
    """


class UnknownModel(RegistryError):
    """`model_id` is not in `BUILTIN`."""


class ModelNotFound(RegistryError):
    """`model_id` is valid, but the model file or vocabulary cannot be found under the given `search_roots`."""


@dataclass(frozen=True)
class ModelSpec:
    """A description of one discoverable model. Globs are **relative to a search_root**.

    `onnx_glob` / `vocab_glob` are the model's **specialised** layout (HF cache / ComfyUI plugin
    directory); the generic flat layout is appended by the properties below, so `resolve()` finds
    both layouts.
    """

    id: str
    onnx_glob: str
    vocab_glob: str
    input_size: int
    description: str

    @property
    def onnx_globs(self) -> tuple[str, ...]:
        """The ONNX globs tried in order: specialised layout -> (fallback) generic flat layout `<model_id>/model.onnx`."""
        return (self.onnx_glob, "%s/%s" % (self.id, FLAT_ONNX_NAME))

    @property
    def vocab_globs(self) -> tuple[str, ...]:
        """The vocabulary globs tried in order: specialised layout -> (fallback) any `*.csv` in the flat directory."""
        return (self.vocab_glob, "%s/%s" % (self.id, FLAT_VOCAB_GLOB))


#: Every model mentioned locally and in the spec. The globs cover the search roots given by
#: p0-spec §7 / local-model-inventory.md:
#:   <hf-hub>/models--SmilingWolf--<repo>/snapshots/*/model.onnx        (HF cache layout)
#:   **/<name>.onnx                                                     (ComfyUI plugin's flat layout)
BUILTIN: dict[str, ModelSpec] = {
    "wd-swinv2-tagger-v3": ModelSpec(
        id="wd-swinv2-tagger-v3",
        onnx_glob="models--SmilingWolf--wd-swinv2-tagger-v3/snapshots/*/model.onnx",
        vocab_glob="models--SmilingWolf--wd-swinv2-tagger-v3/snapshots/*/*.csv",
        input_size=448,
        description="WD14 v3 / SwinV2, 445.8 MB, the strongest v3 locally, the default recommendation (HF cache layout)",
    ),
    "wd-vit-tagger-v3": ModelSpec(
        id="wd-vit-tagger-v3",
        onnx_glob="models--SmilingWolf--wd-vit-tagger-v3/snapshots/*/model.onnx",
        vocab_glob="models--SmilingWolf--wd-vit-tagger-v3/snapshots/*/*.csv",
        input_size=448,
        description="WD14 v3 / ViT, 361.0 MB (HF cache layout)",
    ),
    "wd-vit-tagger-v3-comfy": ModelSpec(
        id="wd-vit-tagger-v3-comfy",
        onnx_glob="**/wd-vit-tagger-v3.onnx",
        vocab_glob="**/wd-vit-tagger-v3.csv",
        input_size=448,
        description="WD14 v3 / ViT, a copy inside the ComfyUI WD14-Tagger plugin, with a different CSV file name (flat layout)",
    ),
    "wd-convnext-tagger-v3": ModelSpec(
        id="wd-convnext-tagger-v3",
        onnx_glob="models--SmilingWolf--wd-convnext-tagger-v3/snapshots/*/model.onnx",
        vocab_glob="models--SmilingWolf--wd-convnext-tagger-v3/snapshots/*/*.csv",
        input_size=448,
        description="WD14 v3 / ConvNeXt (not present locally, needs a network download)",
    ),
    "wd-eva02-large-tagger-v3": ModelSpec(
        id="wd-eva02-large-tagger-v3",
        onnx_glob="models--SmilingWolf--wd-eva02-large-tagger-v3/snapshots/*/model.onnx",
        vocab_glob="models--SmilingWolf--wd-eva02-large-tagger-v3/snapshots/*/*.csv",
        input_size=448,
        description="WD14 v3 / EVA02-Large, the best quality among v3 but about 1.2 GB (not present locally, needs a network download)",
    ),
    "wd-v1-4-swinv2-tagger-v2": ModelSpec(
        id="wd-v1-4-swinv2-tagger-v2",
        onnx_glob="models--SmilingWolf--wd-v1-4-swinv2-tagger-v2/snapshots/*/model.onnx",
        vocab_glob="models--SmilingWolf--wd-v1-4-swinv2-tagger-v2/snapshots/*/*.csv",
        input_size=448,
        description="WD14 v2 / SwinV2, weaker than v3, for reference only",
    ),
    "wd-v1-4-vit-tagger-v2": ModelSpec(
        id="wd-v1-4-vit-tagger-v2",
        onnx_glob="models--SmilingWolf--wd-v1-4-vit-tagger-v2/snapshots/*/model.onnx",
        vocab_glob="models--SmilingWolf--wd-v1-4-vit-tagger-v2/snapshots/*/*.csv",
        input_size=448,
        description="WD14 v2 / ViT, weaker than v3, for reference only",
    ),
}


def _newest(paths: Sequence[Path]) -> Path:
    """Pick one among several matches: the newest mtime wins.

    Re-downloading from HF **adds** a snapshot directory without deleting the old one, so
    lexicographic order alone is not enough. On equal mtime take the lexicographically largest
    path, so the result is deterministic within one call.
    """
    return max(paths, key=lambda path: (path.stat().st_mtime_ns, str(path)))


def _pick_vocab(candidates: Sequence[Path]) -> Path | None:
    files = sorted({path for path in candidates if path.is_file()})
    if not files:
        return None
    preferred = [path for path in files if path.name == _PREFERRED_VOCAB_NAME]
    return (preferred or files)[0]


def resolve(model_id: str, search_roots: Sequence[Path]) -> tuple[Path, Path]:
    """Resolve `model_id` into `(onnx_path, vocab_path)`, both resolved absolute paths.

    Search in the order of `search_roots`; inside each root in the order of
    `ModelSpec.onnx_globs` (specialised layout first, generic flat layout as fallback, see §4.6);
    if no ONNX is found, try the next root; if an ONNX is found but there is no vocabulary,
    raise directly (half a model is a configuration error and must not be skipped silently).
    """
    spec = BUILTIN.get(model_id)
    if spec is None:
        raise UnknownModel("unknown model id: %r; available: %s" % (model_id, sorted(BUILTIN)))

    tried: list[str] = []
    for raw_root in search_roots:
        root = Path(raw_root)
        tried.append(str(root))
        if not root.is_dir():
            continue
        for onnx_glob in spec.onnx_globs:
            onnx_matches = [path for path in root.glob(onnx_glob) if path.is_file()]
            if not onnx_matches:
                continue
            onnx_path = _newest(onnx_matches).resolve()
            # The vocabulary is **taken from the same directory as the onnx** first (§4.6: that is
            # how the flat layout is defined, and the HF cache's and ComfyUI's csv are also in the
            # onnx directory). The layout-level glob is only a fallback -- within one layout the onnx
            # and csv are necessarily in the same directory, so checking the same directory first
            # keeps layout A's onnx from being paired with layout B's vocabulary.
            vocab_path = _pick_vocab(list(onnx_path.parent.glob("*.csv")))
            if vocab_path is None:
                vocab_path = _pick_vocab([path for glob in spec.vocab_globs for path in root.glob(glob)])
            if vocab_path is None:
                raise ModelNotFound(
                    "%s: found ONNX %s under %s but no vocabulary CSV (vocab_globs=%r)"
                    % (model_id, root, onnx_path, spec.vocab_globs)
                )
            return onnx_path, vocab_path.resolve()

    raise ModelNotFound(
        "%s: no file matching %r under these search roots: %s" % (model_id, list(spec.onnx_globs), tried)
    )


def discover(search_roots: Sequence[Path]) -> list[str]:
    """Return the list of model ids that **actually exist** under `search_roots` (sorted by id).

    Nonexistent search roots are skipped -- a configured root may not have been created yet, and that is not an error.
    """
    found: list[str] = []
    for model_id in sorted(BUILTIN):
        try:
            resolve(model_id, search_roots)
        except RegistryError:
            continue
        found.append(model_id)
    return found
