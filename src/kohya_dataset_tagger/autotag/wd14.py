"""WD14 v3 ONNX inference.

`preprocess` is aligned with `preprocess_image` in sd-scripts
`finetune/tag_images_by_wd14_tagger.py` down to the letter (see the steps in the
function's comments). **Do not "abstractly unify"**: other models (JoyTag-like)
are CHW + divide by 255 + CLIP normalization, and the two cannot share one code path.

A note on the resize interpolation (the one place that cannot be copied verbatim)
---------------------------------------------------------------------------------
sd-scripts calls `library/utils.py::resize_image(image, h, w, 448, 448)`;
with `resize_interpolation=None` that function does: **both dimensions >= 448 -> "area",
otherwise -> "lanczos"**; the lanczos branch itself also goes through PIL. This venv has no
cv2 (p0-spec §7 pins the dependency list), so the area branch approximates `cv2.INTER_AREA`
with PIL's BOX (area filter), and the lanczos branch matches upstream exactly.

Measured (local wd-swinv2-tagger-v3, first 3 images of the sample dataset):
BICUBIC and this implementation give **exactly the same** top-12 tag set, and both overlap
the existing captions 6/12, so this substitution affects none of the numbers recorded in spec §1.2.
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Sequence

import numpy as np
from PIL import Image

from .base import TagScore, category_name

#: Input side length of WD14 v3 (p0-spec §1.2 measured: input [batch_size, 448, 448, 3] tensor(float)).
WD14_INPUT_SIZE = 448

#: Provider attempt order: CUDA first (the explicit -Gpu cuda route), then **DirectML** (the Windows
#: default the setup scripts install), then CPU. An onnxruntime build only offers what it was compiled
#: with, so the first entry that is actually available is the one that runs. DirectML used to be missing
#: here: this venv is the DirectML build, the tagger asked for CUDA + CPU only, the CUDA entry got
#: filtered out and it silently ran on CPU (0.84 s/image here instead of about 0.19). See the changelog.
DEFAULT_PROVIDERS: tuple[str, ...] = (
    "CUDAExecutionProvider",
    "DmlExecutionProvider",
    "CPUExecutionProvider",
)


def select_providers(requested: Sequence[str], available: Sequence[str]) -> list[str]:
    """The entries of requested that this onnxruntime build really has, order preserved.

    Split out of WD14Tagger.__init__ so the which-provider-wins rule is testable without a model, a GPU
    or a session. A provider that cannot *load* still falls back to CPU silently, so only
    InferenceSession.get_providers() is the truth - this function decides the request, not the result.
    """
    usable = [name for name in requested if name in available]
    if not usable:
        raise RuntimeError(
            "no usable onnxruntime provider: requested %s, available locally %s"
            % (list(requested), list(available))
        )
    return usable

#: How many images to stuff into one session.run at most.
#: This is not an interface, it is a memory guardrail: 448*448*3*4 B = 2.4 MB/image, and
#: without a cap a whole-batch call over 1653 images would need about 4 GB of input tensor.
#: Chunking does not change the result (each image is independent); measured, the difference
#: from a single image at the chunk boundary is 0 (batch<=4) or 4.5e-07 (batch 8/20, floating-point reassociation).
MAX_BATCH_SIZE = 32


def _resize_bgr(arr: np.ndarray, size: int) -> np.ndarray:
    """Replicate sd-scripts `resize_image`'s default interpolation choice: LANCZOS for upscaling, area filter for downscaling."""
    height, width = arr.shape[0], arr.shape[1]
    resample = Image.Resampling.BOX if (height >= size and width >= size) else Image.Resampling.LANCZOS
    return np.asarray(Image.fromarray(arr).resize((size, size), resample))


def preprocess(image: Image.Image, size: int = WD14_INPUT_SIZE) -> np.ndarray:
    """PIL image -> ONNX input tensor, shape `(H, W, 3)`, dtype `float32`, values 0-255.

    Six steps copied from sd-scripts, none of which can be skipped:
      1. composite alpha onto a white background (255,255,255)
      2. RGB -> **BGR** (`arr[:, :, ::-1]`)
      3. pad to a square, centred, with **255**
      4. resize to `size`
      5. `astype(np.float32)`, **without dividing by 255**
      6. layout **NHWC** = `(H, W, 3)`
    """
    # --- 1. alpha -> white background -------------------------------------
    if image.mode in ("RGBA", "LA") or "transparency" in image.info:
        image = image.convert("RGBA")
    elif image.mode != "RGB":
        image = image.convert("RGB")
    if image.mode == "RGBA":
        background = Image.new("RGB", image.size, (255, 255, 255))
        background.paste(image, mask=image.split()[3])
        image = background

    # --- 2. RGB -> BGR ----------------------------------------------------
    arr = np.array(image)[:, :, ::-1]

    # --- 3. pad to a square, centred, with white (255) --------------------
    side = max(arr.shape[0], arr.shape[1])
    pad_x = side - arr.shape[1]
    pad_y = side - arr.shape[0]
    pad_left = pad_x // 2
    pad_top = pad_y // 2
    arr = np.pad(
        arr,
        ((pad_top, pad_y - pad_top), (pad_left, pad_x - pad_left), (0, 0)),
        mode="constant",
        constant_values=255,
    )

    # --- 4. resize to size ------------------------------------------------
    arr = _resize_bgr(arr, size)

    # --- 5+6. float32 (no /255) / NHWC -----------------------------------
    return arr.astype(np.float32)


def load_vocab(path: Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Read `selected_tags.csv` and return (names, categories) **aligned with the model output column order**.

    categories have already been converted to names by `category_name` (the `CATEGORY_NAMES` mapping).
    """
    names: list[str] = []
    categories: list[str] = []
    path = Path(path)
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = set(reader.fieldnames or ())
        missing = {"name", "category"} - columns
        if missing:
            raise ValueError(
                "vocabulary %s is missing columns %s; expected header tag_id,name,category,count" % (path, sorted(missing))
            )
        for row in reader:
            name = (row.get("name") or "").strip()
            if not name:
                continue
            names.append(name)
            categories.append(category_name((row.get("category") or "").strip()))
    if not names:
        raise ValueError("vocabulary %s is empty" % path)
    return tuple(names), tuple(categories)


def _check_input(input_meta: object, input_size: int, onnx_path: Path) -> None:
    """Confirm the model really has WD14's NHWC input.

    This step is worth doing: p0-spec / AGENTS.md both spell out that "getting it wrong
    raises no error, it just emits garbage tags", so failing loudly at load time beats
    quietly producing garbage.
    """
    shape = list(getattr(input_meta, "shape", []))
    if len(shape) != 4:
        raise ValueError("WD14 expects a 4-D input (N,H,W,C); %s gives %r" % (onnx_path, shape))
    if isinstance(shape[3], int) and shape[3] != 3:
        raise ValueError("WD14 requires NHWC (last axis = 3); %s gives %r" % (onnx_path, shape))
    for axis in (1, 2):
        value = shape[axis]
        if isinstance(value, int) and value != input_size:
            raise ValueError(
                "WD14 input side %s does not match input_size=%d: %s gives %r"
                % ("HW"[axis - 1], input_size, onnx_path, shape)
            )


def _resolve_intra_op_threads(value: int | None) -> int | None:
    """Normalize `intra_op_num_threads` into "the value to set" or None (= do not set, keep the ORT default).

    ORT's own convention is that 0 means "let ORT decide", so 0 also normalizes to None;
    do not stuff 0 into SessionOptions.
    """
    if value is None:
        return None
    threads = int(value)
    if threads < 0:
        raise ValueError("intra_op_num_threads must be an integer >= 0 or None, got %r" % (value,))
    return threads or None


class WD14Tagger:
    """WD14 v3 ONNX tagger. The model and session are kept resident, loaded once.

    `intra_op_num_threads` (spec §3.6, added when unfrozen on 2026-09-17): passed straight to
    `onnxruntime.SessionOptions.intra_op_num_threads`, default None = keep the ORT default.
    Measured locally (16 logical cores, training running at the same time): default **3.45 s/image**,
    `intra_op_num_threads=4` **1.39 s/image** (2.5x). The thread count does not affect the result --
    measured, default / 4 / 1 all give bit-identical output.
    

    `predict` returns **scores over the full vocabulary** (by descending score), not a filtered
    result -- the threshold is an adjustable parameter (HTTP's `thresholds` field), and re-running
    inference costs 0.47 s/image, so "scoring" and "filtering" are separated: filtering is the job
    of the two pure functions `postprocess.apply_thresholds` /
    `postprocess.postprocess_tags`.
    """

    name = "wd14"

    def __init__(
        self,
        onnx_path: str | Path,
        vocab_path: str | Path,
        *,
        providers: Sequence[str] | None = None,
        input_size: int = WD14_INPUT_SIZE,
        intra_op_num_threads: int | None = None,
    ) -> None:
        # Import onnxruntime lazily: preprocess/vocabulary parsing do not need it, so tests and the registry need not pay that cost.
        import onnxruntime as ort

        self.onnx_path = Path(onnx_path)
        self.vocab_path = Path(vocab_path)
        self.input_size = int(input_size)

        if not self.onnx_path.is_file():
            raise FileNotFoundError("ONNX model does not exist: %s" % self.onnx_path)
        if not self.vocab_path.is_file():
            raise FileNotFoundError("vocabulary does not exist: %s" % self.vocab_path)

        self.tag_names, self.tag_categories = load_vocab(self.vocab_path)

        requested = list(providers) if providers is not None else list(DEFAULT_PROVIDERS)
        usable = select_providers(requested, ort.get_available_providers())
        #: Normalized thread count: None = not set (keep the ORT default).
        self.intra_op_num_threads = _resolve_intra_op_threads(intra_op_num_threads)
        session_options = ort.SessionOptions()
        if self.intra_op_num_threads is not None:
            session_options.intra_op_num_threads = self.intra_op_num_threads
        #: The session options object itself is kept too, so callers/acceptance can check directly that it really was passed down.
        self.session_options = session_options
        self.session = ort.InferenceSession(
            str(self.onnx_path), sess_options=session_options, providers=usable
        )
        #: The provider actually in effect (the UI must show this, not the "requested" one).
        self.providers: tuple[str, ...] = tuple(self.session.get_providers())

        input_meta = self.session.get_inputs()[0]
        #: ONNX input name (measured locally it is just "input").
        self.input_name: str = input_meta.name
        _check_input(input_meta, self.input_size, self.onnx_path)

    def __len__(self) -> int:
        """Vocabulary size (10861 for the local wd-swinv2-tagger-v3)."""
        return len(self.tag_names)

    def _load(self, image: str | Path) -> np.ndarray:
        with Image.open(image) as handle:
            handle.load()
            return preprocess(handle, self.input_size)

    def _scores(self, probabilities: np.ndarray) -> list[TagScore]:
        values = np.asarray(probabilities, dtype=np.float64).reshape(-1)
        if values.shape[0] != len(self.tag_names):
            raise ValueError(
                "model output has %d dimensions, does not match the %d vocabulary rows (%s)"
                % (values.shape[0], len(self.tag_names), self.vocab_path)
            )
        # stable: on equal scores keep the vocabulary's original order, so a given image's output is fully reproducible.
        order = np.argsort(-values, kind="stable")
        return [
            TagScore(
                tag=self.tag_names[int(index)],
                score=float(values[int(index)]),
                category=self.tag_categories[int(index)],
            )
            for index in order
        ]

    def predict(self, image: Path) -> list[TagScore]:
        """Single-image inference, returns scores over the full vocabulary (descending).

        `image` is a `Path` per the protocol; the implementation normalizes with
        `Path(image)`, so passing a str works too.
        """
        batch = self._load(image)[np.newaxis, ...]
        probabilities = self.session.run(None, {self.input_name: batch})[0]
        return self._scores(probabilities[0])

    def predict_batch(self, images: Sequence[Path]) -> list[list[TagScore]]:
        """Batch inference, returns a result list of the same length and order as `images`.

        More than `MAX_BATCH_SIZE` images are chunked automatically (same result as without
        chunking, only the peak memory is capped).

        Measured caveat: **on CPU a bigger batch is slower** -- for the local
        wd-swinv2-tagger-v3 a single image is 0.45 s, batch 4 is 0.67 s/image, batch 8 is
        0.82 s/image, batch 20 is 0.92 s/image (memory bandwidth saturated). Go image by image
        with `predict` for throughput; the value of `predict_batch` is sparing the caller a loop.
        """
        paths = [Path(image) for image in images]
        if not paths:
            return []
        results: list[list[TagScore]] = []
        for start in range(0, len(paths), MAX_BATCH_SIZE):
            chunk = paths[start : start + MAX_BATCH_SIZE]
            batch = np.stack([self._load(path) for path in chunk])
            probabilities = self.session.run(None, {self.input_name: batch})[0]
            results.extend(self._scores(row) for row in probabilities[: len(chunk)])
        return results
