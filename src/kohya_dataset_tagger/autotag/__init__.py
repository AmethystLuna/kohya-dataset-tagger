"""The autotag package: model discovery + WD14 v3 ONNX inference + post-processing.

Only these three things are needed externally:

    from kohya_dataset_tagger.autotag import WD14Tagger, resolve, discover
    from kohya_dataset_tagger.autotag.postprocess import (
        PostprocessOptions, apply_thresholds, postprocess_tags, format_tags,
    )

Automatic model download (p0-spec §4.6 / §4.8) lives in a submodule, imported by module to avoid
clashing with the function of the same name:

    from kohya_dataset_tagger.autotag import download as download_module
    download_module.download("wd-eva02-large-tagger-v3", dest_root)   # or download_model(...)

Boundary (p0-spec §3.0): this package only takes `pathlib.Path` and `PIL.Image`,
**and does not import core/*** (so the category table and DANBOORU order are held once per package; changes must be synced on both sides).
"""
from __future__ import annotations

from .base import (
    CATEGORY_CODES,
    CATEGORY_NAMES,
    CHARACTER_CATEGORY,
    GENERAL_CATEGORY,
    RATING_CATEGORY,
    Tagger,
    TagScore,
    category_name,
)
from . import download
from .download import (
    DEFAULT_ENDPOINTS,
    HF_ENDPOINT_ENV,
    DownloadCancelled,
    DownloadError,
    DownloadPlan,
    DownloadResult,
    endpoints,
    installed_size,
    is_downloadable,
    local_paths,
    repo_id,
)
from .download import download as download_model
from .postprocess import (
    DANBOORU_ORDER,
    DEFAULT_THRESHOLD,
    RATING_MODES,
    TAG_SEPARATOR,
    PostprocessOptions,
    apply_thresholds,
    format_tags,
    postprocess_tags,
)
from .registry import (
    BUILTIN,
    ModelNotFound,
    ModelSpec,
    RegistryError,
    UnknownModel,
    discover,
    resolve,
)
from .wd14 import DEFAULT_PROVIDERS, MAX_BATCH_SIZE, WD14_INPUT_SIZE, WD14Tagger, load_vocab, preprocess

__all__ = [
    "BUILTIN",
    "CATEGORY_CODES",
    "CATEGORY_NAMES",
    "CHARACTER_CATEGORY",
    "DANBOORU_ORDER",
    "DEFAULT_ENDPOINTS",
    "DEFAULT_PROVIDERS",
    "DEFAULT_THRESHOLD",
    "DownloadCancelled",
    "DownloadError",
    "DownloadPlan",
    "DownloadResult",
    "GENERAL_CATEGORY",
    "HF_ENDPOINT_ENV",
    "MAX_BATCH_SIZE",
    "ModelNotFound",
    "ModelSpec",
    "PostprocessOptions",
    "RATING_CATEGORY",
    "RATING_MODES",
    "RegistryError",
    "TAG_SEPARATOR",
    "Tagger",
    "TagScore",
    "UnknownModel",
    "WD14_INPUT_SIZE",
    "WD14Tagger",
    "apply_thresholds",
    "category_name",
    "discover",
    "download",
    "download_model",
    "endpoints",
    "format_tags",
    "installed_size",
    "is_downloadable",
    "load_vocab",
    "local_paths",
    "postprocess_tags",
    "preprocess",
    "repo_id",
    "resolve",
]
