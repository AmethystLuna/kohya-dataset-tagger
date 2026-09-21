"""A10 end to end: inference over **20 real images** with the local `wd-swinv2-tagger-v3`.

Discipline (p0-spec §0 hard boundary 1):
the dataset configured in `local_paths.ini` is a **read-only** test set. This file only `glob`s and
`Image.open`s it, and never writes once. A module-level autouse fixture asserts at teardown that
the dataset root's entry set has not changed (which catches any `__pycache__` / cache directory
sneaked in).

Timing: model load about 1.5 s, each image about 0.65 s (preprocess 0.17 s + inference 0.47 s),
20 images about 15 s. Run with `-s` to see the measured per-part timing table
(`pytest test/test_wd14_integration.py -q -s`).
"""
from __future__ import annotations

import ast
import inspect
import os
import time
from pathlib import Path

import pytest

import local_paths
from kohya_dataset_tagger.autotag import postprocess as pp
from kohya_dataset_tagger.autotag import registry, wd14
from kohya_dataset_tagger.autotag.base import TagScore

pytest.importorskip("onnxruntime")

#: Where the models and the dataset are: `local_paths.ini` (gitignored; the template points at the
#: sample dataset and the repository's own models/ directory). Set the keys there to run these.
SEARCH_ROOTS = local_paths.model_roots()
DATASET = local_paths.dataset_root()
SAMPLE_SIZE = 20
MODEL_ID = "wd-swinv2-tagger-v3"

AVAILABLE_MODELS = registry.discover(SEARCH_ROOTS)
HAS_MODEL = MODEL_ID in AVAILABLE_MODELS
#: The ComfyUI copy carries its own CSV file name and is a precondition of its own.
HAS_COMFY_COPY = "wd-vit-tagger-v3" in AVAILABLE_MODELS
HAS_DATASET = DATASET.is_dir()

requires_model = pytest.mark.skipif(not HAS_MODEL, reason="%s is not present locally" % MODEL_ID)
requires_comfy_copy = pytest.mark.skipif(
    not HAS_COMFY_COPY,
    reason="the ComfyUI copy of wd-vit-tagger-v3 is not present locally (see 'models' in local_paths.ini)",
)
requires_dataset = pytest.mark.skipif(
    not HAS_DATASET,
    reason="no dataset configured in local_paths.ini: %s" % DATASET,
)

#: Per-category thresholds. Spec §4 puts them in a separate `thresholds` field.
THRESHOLDS = {"general": 0.35, "character": 0.35, "copyright": 0.35, "artist": 0.35, "meta": 0.35}


def _dataset_entries() -> list[str]:
    return sorted(entry.name for entry in DATASET.iterdir())


@pytest.fixture(scope="module", autouse=True)
def dataset_root_is_untouched():
    """After A10 runs, assert the dataset root's entry set has not changed."""
    if not HAS_DATASET:
        yield
        return
    before = _dataset_entries()
    yield
    after = _dataset_entries()
    assert before == after, "the configured dataset was written to: %s" % sorted(set(after) ^ set(before))


@pytest.fixture(scope="module")
def real_images() -> list[Path]:
    paths = sorted(DATASET.glob("*/*.jpeg"))[:SAMPLE_SIZE]
    if len(paths) < SAMPLE_SIZE:
        pytest.skip("fewer than %d jpegs in the dataset" % SAMPLE_SIZE)
    return paths


@pytest.fixture(scope="module")
def tagger() -> "wd14.WD14Tagger":
    onnx_path, vocab_path = registry.resolve(MODEL_ID, SEARCH_ROOTS)
    started = time.perf_counter()
    instance = wd14.WD14Tagger(onnx_path, vocab_path)
    print("\n[wd14] model load %.2f s | providers=%s" % (time.perf_counter() - started, instance.providers))
    return instance


# --------------------------------------------------------------------------
# registry: model discovery (A10's precondition)
# --------------------------------------------------------------------------


@requires_model
@requires_comfy_copy
def test_discover_finds_the_local_models() -> None:
    assert MODEL_ID in AVAILABLE_MODELS
    # The ComfyUI copy with a different CSV file name must be discovered too (discovery by glob, no hardcoded file name)
    assert "wd-vit-tagger-v3" in AVAILABLE_MODELS
    assert AVAILABLE_MODELS == sorted(AVAILABLE_MODELS)


@requires_model
def test_resolve_returns_existing_absolute_paths() -> None:
    onnx_path, vocab_path = registry.resolve(MODEL_ID, SEARCH_ROOTS)
    assert onnx_path.is_absolute() and onnx_path.is_file()
    assert vocab_path.is_absolute() and vocab_path.is_file()
    assert onnx_path.suffix == ".onnx"
    assert vocab_path.suffix == ".csv"
    assert vocab_path.parent == onnx_path.parent
    # The snapshot hash must not be written into the code: the sha should appear only in the resolution result
    assert "snapshots" in str(onnx_path)


@requires_comfy_copy
def test_resolve_finds_the_comfyui_copy_with_its_own_csv_name() -> None:
    onnx_path, vocab_path = registry.resolve("wd-vit-tagger-v3-comfy", SEARCH_ROOTS)
    assert onnx_path.name == "wd-vit-tagger-v3.onnx"
    assert vocab_path.name == "wd-vit-tagger-v3.csv"


@requires_model
def test_resolve_is_stable_across_calls() -> None:
    assert registry.resolve(MODEL_ID, SEARCH_ROOTS) == registry.resolve(MODEL_ID, SEARCH_ROOTS)


def test_unknown_model_raises_unknown_model() -> None:
    with pytest.raises(registry.UnknownModel):
        registry.resolve("no-such-tagger", SEARCH_ROOTS)
    assert issubclass(registry.UnknownModel, LookupError)


def test_missing_model_raises_model_not_found(tmp_path: Path) -> None:
    with pytest.raises(registry.ModelNotFound):
        registry.resolve(MODEL_ID, [tmp_path])
    assert issubclass(registry.ModelNotFound, registry.RegistryError)


def test_nonexistent_search_root_is_skipped(tmp_path: Path) -> None:
    assert registry.discover([tmp_path / "does-not-exist"]) == []


def test_glob_patterns_do_not_hardcode_snapshot_hashes() -> None:
    for spec in registry.BUILTIN.values():
        assert "snapshots/*/" in spec.onnx_glob or spec.onnx_glob.startswith("**/")
        assert spec.input_size == wd14.WD14_INPUT_SIZE
        # A21: every model's onnx_globs must **append** the generic flat layout (specialised glob first, flat as fallback)
        assert spec.onnx_globs == (spec.onnx_glob, "%s/model.onnx" % spec.id)


# --------------------------------------------------------------------------
# A21: the registry must recognize both the HF cache layout and the **generic flat layout** (p0-spec §4.6)
#
# The gap used to be here: only the HF cache and the ComfyUI flat special case were recognized,
# so the fallback "if it will not download, download it yourself into <root>/<model_id>/" was a
# lie. Both layouts have their cases below.
# --------------------------------------------------------------------------


def _fake_model_files(
    directory: Path, *, vocab_name: str = "selected_tags.csv", size: int = 32
) -> tuple[Path, Path]:
    """Create a fake pair of onnx / csv (content is irrelevant, only the discovery logic is verified here)."""
    directory.mkdir(parents=True, exist_ok=True)
    onnx = directory / "model.onnx"
    vocab = directory / vocab_name
    onnx.write_bytes(b"\x00" * size)
    vocab.write_bytes(b"tag_id,name,category,count\n")
    return onnx, vocab


def test_a21_flat_layout_is_discovered(tmp_path: Path) -> None:
    """The generic flat layout: <root>/<model_id>/model.onnx (what we download / what the user drops in)."""
    onnx, vocab = _fake_model_files(tmp_path / MODEL_ID)

    assert registry.resolve(MODEL_ID, [tmp_path]) == (onnx.resolve(), vocab.resolve())
    assert registry.discover([tmp_path]) == [MODEL_ID]


def test_a21_flat_layout_vocab_is_any_csv_in_the_same_directory(tmp_path: Path) -> None:
    """§3.5 / §4.6: the vocabulary = any *.csv in the **same directory** as the onnx, no hardcoded file name."""
    onnx, vocab = _fake_model_files(tmp_path / MODEL_ID, vocab_name="wd-eva02-large-tagger-v3.csv")

    resolved_onnx, resolved_vocab = registry.resolve(MODEL_ID, [tmp_path])

    assert resolved_onnx == onnx.resolve()
    assert resolved_vocab == vocab.resolve()
    assert resolved_vocab.name == "wd-eva02-large-tagger-v3.csv"


def test_a21_hf_cache_layout_is_still_discovered(tmp_path: Path) -> None:
    """The other layout still works: models--<org>--<name>/snapshots/*/model.onnx (the shape hf_hub_download produces).

    This also proves there is **no** hardcoded snapshot hash: the sha used here is made up.
    """
    snapshot = tmp_path / ("models--SmilingWolf--" + MODEL_ID) / "snapshots" / ("0" * 40)
    onnx, vocab = _fake_model_files(snapshot)

    assert registry.resolve(MODEL_ID, [tmp_path]) == (onnx.resolve(), vocab.resolve())
    assert registry.discover([tmp_path]) == [MODEL_ID]


def test_a21_both_layouts_in_one_root_prefer_the_specialised_one(tmp_path: Path) -> None:
    """Both layouts under one root: the specialised (HF cache) glob wins, the flat layout is the fallback.

    The order is pinned here so that "switching layouts" is always intentional, never luck.
    """
    flat_onnx, _flat_vocab = _fake_model_files(tmp_path / MODEL_ID)
    snapshot = tmp_path / ("models--SmilingWolf--" + MODEL_ID) / "snapshots" / ("a" * 40)
    cache_onnx, _cache_vocab = _fake_model_files(snapshot)

    resolved_onnx, _resolved_vocab = registry.resolve(MODEL_ID, [tmp_path])

    assert resolved_onnx == cache_onnx.resolve()
    assert resolved_onnx != flat_onnx.resolve()


def test_a21_vocab_is_taken_from_the_onnx_directory(tmp_path: Path) -> None:
    """The vocabulary must be in the **same directory** as the onnx (§4.6): a csv from another layout must not be paired in (versions would get crossed)."""
    flat_onnx, flat_vocab = _fake_model_files(tmp_path / MODEL_ID)
    cache_dir = tmp_path / ("models--SmilingWolf--" + MODEL_ID) / "snapshots" / ("c" * 40)
    cache_dir.mkdir(parents=True)
    (cache_dir / "selected_tags.csv").write_bytes(b"another-vocab")

    resolved_onnx, resolved_vocab = registry.resolve(MODEL_ID, [tmp_path])

    assert resolved_onnx == flat_onnx.resolve()
    assert resolved_vocab == flat_vocab.resolve()


def test_a21_flat_layout_without_a_vocab_is_an_error(tmp_path: Path) -> None:
    """Half a model (only the onnx): raise, do not skip silently -- otherwise inference fails inexplicably."""
    (tmp_path / MODEL_ID).mkdir(parents=True)
    (tmp_path / MODEL_ID / "model.onnx").write_bytes(b"\x00" * 32)

    with pytest.raises(registry.ModelNotFound):
        registry.resolve(MODEL_ID, [tmp_path])
    assert registry.discover([tmp_path]) == []


def test_a21_every_builtin_model_accepts_the_flat_layout(tmp_path: Path) -> None:
    """Every BUILTIN model needs a flat glob: only then does the "drop it in by hand" fallback really exist for downloadable models."""
    for model_id, spec in registry.BUILTIN.items():
        _fake_model_files(tmp_path / spec.id)

    assert registry.discover([tmp_path]) == sorted(registry.BUILTIN)
    for model_id, spec in registry.BUILTIN.items():
        onnx_path, vocab_path = registry.resolve(model_id, [tmp_path])
        assert onnx_path.parent == (tmp_path / spec.id).resolve()
        assert vocab_path.parent == onnx_path.parent


def test_a21_flat_layout_is_not_confused_with_another_model(tmp_path: Path) -> None:
    """The directory name must match the model id: someone else's flat directory must not be taken for this model."""
    other = "wd-vit-tagger-v3"
    assert other != MODEL_ID
    _fake_model_files(tmp_path / other)

    with pytest.raises(registry.ModelNotFound):
        registry.resolve(MODEL_ID, [tmp_path])
    assert registry.discover([tmp_path]) == [other]


# --------------------------------------------------------------------------
# Contracts after the model is loaded (A8's measured baseline + A11's vocabulary facts)
# --------------------------------------------------------------------------


def test_provider_order_prefers_a_gpu_and_falls_back_to_cpu() -> None:
    """The default order really is CUDA -> DirectML -> CPU, filtered by what the build offers.

    This is what makes the tagger pick the GPU without anyone passing providers=. Before 2026-09-19 the
    list was CUDA + CPU, so a DirectML install (the Windows default the setup scripts produce) was
    filtered down to CPU and inference ran ~4x slower with no error.
    """
    dml_build = ["DmlExecutionProvider", "CPUExecutionProvider"]
    assert wd14.select_providers(wd14.DEFAULT_PROVIDERS, dml_build) == dml_build
    cuda_build = ["CUDAExecutionProvider", "CPUExecutionProvider"]
    assert wd14.select_providers(wd14.DEFAULT_PROVIDERS, cuda_build)[0] == "CUDAExecutionProvider"
    assert wd14.select_providers(wd14.DEFAULT_PROVIDERS, ["CPUExecutionProvider"]) == [
        "CPUExecutionProvider"
    ]
    # an explicit request keeps its own order and only loses the unavailable entries
    assert wd14.select_providers(["CPUExecutionProvider"], dml_build) == ["CPUExecutionProvider"]
    assert wd14.select_providers(
        ["CUDAExecutionProvider", "CPUExecutionProvider"], dml_build
    ) == ["CPUExecutionProvider"]
    with pytest.raises(RuntimeError):
        wd14.select_providers(["CUDAExecutionProvider"], dml_build)


@requires_model
def test_input_contract_matches_the_spec_baseline(tagger: "wd14.WD14Tagger") -> None:
    """§1.2: input ['batch_size', 448, 448, 3] tensor(float).

    The provider is deliberately **not pinned by name**: p0-spec §7 says a hardcoded
    (CPUExecutionProvider,) turns red the moment the venv's onnxruntime package changes. What must hold
    is that the session took the best provider the installed build offers and that the tuple the UI
    shows is the session's real one.
    """
    import onnxruntime as ort

    meta = tagger.session.get_inputs()[0]
    assert meta.name == tagger.input_name
    assert len(meta.shape) == 4
    assert meta.shape[1:] == [448, 448, 3]
    assert tagger.input_size == 448
    assert list(tagger.providers) == tagger.session.get_providers(), (
        "the reported provider is not what the session really uses"
    )
    wanted = wd14.select_providers(wd14.DEFAULT_PROVIDERS, ort.get_available_providers())
    assert list(tagger.providers)[0] == wanted[0], (
        "the default order did not pick the best available provider: %s" % list(tagger.providers)
    )
    assert tagger.providers[-1] == "CPUExecutionProvider"
    assert tagger.name == "wd14"


@requires_model
def test_vocab_matches_the_spec_baseline(tagger: "wd14.WD14Tagger") -> None:
    """A11: 10861 rows in total = general 8106 + character 2751 + rating 4, and the first 4 rows are rating."""
    names = list(tagger.tag_names)
    categories = list(tagger.tag_categories)
    assert len(names) == 10861
    assert categories.count("General") == 8106
    assert categories.count("Character") == 2751
    assert categories.count("Rating") == 4
    assert names[:4] == ["general", "sensitive", "questionable", "explicit"]
    assert categories[:4] == ["Rating"] * 4
    assert len(tagger) == 10861


@requires_model
def test_preprocess_output_reaches_the_session(tagger: "wd14.WD14Tagger", real_images: list[Path]) -> None:
    """The tensor fed in must be exactly `preprocess`'s shape / value range."""
    array = tagger._load(real_images[0])
    assert array.shape == (448, 448, 3)
    assert array.dtype.name == "float32"
    assert float(array.max()) <= 255.0
    assert float(array.max()) > 1.0, "must not divide by 255"


# --------------------------------------------------------------------------
# A10 body
# --------------------------------------------------------------------------


@requires_model
@requires_dataset
def test_a10_twenty_real_images_all_return_non_empty_tags(
    tagger: "wd14.WD14Tagger", real_images: list[Path]
) -> None:
    """20 real images: each returns non-empty tags; two inferences on the same image are **exactly identical**."""
    raw_counts: list[int] = []
    kept_counts: list[int] = []
    preprocess_seconds: list[float] = []
    infer_seconds: list[float] = []
    first_result: list[TagScore] | None = None
    raiden_raw = False
    raiden_postprocessed = False
    raiden_underscore_survived = False
    escaped_paren_tags = 0

    load_started = time.perf_counter()
    for index, path in enumerate(real_images):
        started = time.perf_counter()
        tagger._load(path)
        preprocess_seconds.append(time.perf_counter() - started)

        started = time.perf_counter()
        scores = tagger.predict(path)
        infer_seconds.append(time.perf_counter() - started)

        assert scores, "image %d returned an empty tag list: %s" % (index, path)
        raw_counts.append(len(scores))
        assert [item.score for item in scores] == sorted((item.score for item in scores), reverse=True)

        kept = pp.apply_thresholds(scores, THRESHOLDS)
        assert kept, "image %d has no tags under the default thresholds: %s" % (index, path)
        kept_counts.append(len(kept))

        final = pp.postprocess_tags(kept, pp.PostprocessOptions(always_first_tags=("1girl",)))
        postprocessed = [item.tag for item in final]
        assert postprocessed, "image %d has no tags after post-processing" % index
        assert all("_" not in tag or len(tag) < 4 for tag in postprocessed), "underscore replacement was missed"
        assert len(postprocessed) == len(set(postprocessed)), "post-processing did not dedupe"

        # A17: no unescaped parentheses may appear in the final text (both the dataset and the inference prompt use the escaped form)
        text = pp.format_tags(final)
        bare = text.replace(pp.ESCAPE_PREFIX + "(", "").replace(pp.ESCAPE_PREFIX + ")", "")
        assert "(" not in bare and ")" not in bare, "image %d has unescaped parentheses: %s" % (index, text)
        escaped_paren_tags += sum(1 for tag in postprocessed if pp.ESCAPE_PREFIX + "(" in tag)

        if "raiden_shogun" in {item.tag for item in kept}:
            raiden_raw = True
            raiden_postprocessed = "raiden shogun" in postprocessed
            raiden_underscore_survived = "raiden_shogun" in postprocessed

        if first_result is None:
            first_result = scores

    total = time.perf_counter() - load_started
    per_image = total / len(real_images)
    print("\n[wd14] measured on 20 real images:")
    print("        preprocess %.3f s/image | inference %.3f s/image | total %.2f s (%.3f s/image)" % (
        sum(preprocess_seconds) / len(preprocess_seconds),
        sum(infer_seconds) / len(infer_seconds),
        total,
        per_image,
    ))
    print("        extrapolated to 1653 images: %.1f minutes (spec §1.2's 0.47 s/image estimate = 13 minutes, excluding preprocessing)" % (
        per_image * 1653 / 60.0,
    ))
    print("        vocabulary dimension %d | after default thresholds %d-%d tags per image" % (raw_counts[0], min(kept_counts), max(kept_counts)))
    print("        machine logical cores %s; onnxruntime uses all cores by default, and with another heavy load on the machine it degrades noticeably" % (os.cpu_count(),))
    print("        A17 parenthesis escaping: %d tags escaped across 20 images (e.g. asuna \\(blue archive\\))" % escaped_paren_tags)

    assert len(raw_counts) == SAMPLE_SIZE
    assert set(raw_counts) == {len(tagger)}

    # --- A10's determinism criterion: two inferences on the same image are exactly identical ---
    again = tagger.predict(real_images[0])
    assert again == first_result, "two inferences on the same image disagree (should be bit-identical)"
    assert [item.score for item in again] == [item.score for item in first_result]

    # --- §1.2 measured pitfall 2: the model gives underscores, the dataset caption gives spaces ---
    assert raiden_raw, "at least one of the 20 should be recognized as raiden_shogun (verified with A10's sample)"
    assert raiden_postprocessed, "remove_underscore must be on by default: raiden_shogun -> raiden shogun"
    assert not raiden_underscore_survived

    # --- A17: a tag with parentheses must really occur on real data, otherwise this assertion is empty ---
    assert escaped_paren_tags > 0, "at least one tag with parentheses should occur across the 20 images"

    caption = real_images[0].with_suffix(".txt")
    if caption.is_file():
        text = caption.read_text(encoding="utf-8")
        assert "1girl" in text
        assert "_" not in text.split(",")[0]


@requires_model
@requires_dataset
def test_a10_is_reproducible_at_the_score_level(tagger: "wd14.WD14Tagger", real_images: list[Path]) -> None:
    """Run the whole chain a second time: the tag sequence and the scores are bit-identical."""
    path = real_images[1]
    first = tagger.predict(path)
    second = tagger.predict(path)
    assert first == second
    assert len(first) == len(second) == len(tagger)


@requires_model
@requires_dataset
def test_predict_batch_matches_predict(tagger: "wd14.WD14Tagger", real_images: list[Path]) -> None:
    paths = real_images[:3]
    batched = tagger.predict_batch(paths)
    assert len(batched) == len(paths)

    # A single-element batch must be exactly identical to predict
    single = tagger.predict_batch(paths[:1])[0]
    assert single == tagger.predict(paths[0])

    # A multi-sample batch: floating-point reassociation brings tiny differences; the tag sequence must match
    for path, batch_scores in zip(paths, batched):
        per_image = tagger.predict(path)
        assert [item.tag for item in batch_scores[:12]] == [item.tag for item in per_image[:12]]
        for left, right in zip(batch_scores, per_image):
            assert abs(left.score - right.score) < 1e-5


@requires_model
def test_predict_batch_of_nothing_is_empty(tagger: "wd14.WD14Tagger") -> None:
    assert tagger.predict_batch([]) == []


@requires_model
def test_chunking_constant_bounds_memory() -> None:
    assert 0 < wd14.MAX_BATCH_SIZE <= 64


# --------------------------------------------------------------------------
# A17: the escaped-parenthesis convention of existing captions (an on-site recheck of spec §4.1 evidence 1)
# --------------------------------------------------------------------------


@requires_dataset
def test_dataset_captions_use_the_escaped_parenthesis_convention(real_images: list[Path]) -> None:
    """§4.1 says 1049 files in the dataset are written in the escaped form -- this rechecks on the
    20 sampled, and asserts that **none** uses unescaped parentheses. The tagger's output must be
    consistent with this convention."""
    escaped = 0
    checked = 0
    for image in real_images:
        caption = image.with_suffix(".txt")
        if not caption.is_file():
            continue
        checked += 1
        text = caption.read_text(encoding="utf-8")
        bare = text.replace(pp.ESCAPE_PREFIX + "(", "").replace(pp.ESCAPE_PREFIX + ")", "")
        assert "(" not in bare and ")" not in bare, "existing caption has unescaped parentheses: %s" % caption
        if pp.ESCAPE_PREFIX + "(" in text:
            escaped += 1
    assert checked == len(real_images), "every sampled image should have a caption"
    assert escaped > 0, "a caption with escaped parentheses should occur in the sample"
    print(
        "\n[wd14] existing captions: %d/%d contain escaped parentheses, 0 contain unescaped parentheses"
        % (escaped, checked)
    )


@requires_model
def test_vocab_has_no_name_with_both_a_backslash_and_a_parenthesis(
    tagger: "wd14.WD14Tagger",
) -> None:
    """escape_parens is a **literal** rule (every parenthesis gets a backslash, with no check for
    already being escaped).

    This is safe on the real vocabulary: the tags containing a backslash (5 kaomoji, shaped like
    backslash+m+slash) and the tags containing parentheses are disjoint, with a measured
    intersection of 0, so the literal rule never turns an already-escaped tag into a double backslash.
    """
    both = [name for name in tagger.tag_names if "\\" in name and ("(" in name or ")" in name)]
    assert both == []


# --------------------------------------------------------------------------
# intra_op_num_threads (spec §3.6, added when unfrozen on 2026-09-17)
# --------------------------------------------------------------------------


def test_intra_op_threads_normalisation() -> None:
    """None / 0 both mean "do not set" (ORT itself uses 0 for the default), a positive number passes straight through, and a negative number errors."""
    assert wd14._resolve_intra_op_threads(None) is None
    assert wd14._resolve_intra_op_threads(0) is None
    assert wd14._resolve_intra_op_threads(4) == 4
    assert wd14._resolve_intra_op_threads("8") == 8
    with pytest.raises(ValueError):
        wd14._resolve_intra_op_threads(-1)


@requires_model
def test_default_leaves_intra_op_threads_alone(tagger: "wd14.WD14Tagger") -> None:
    assert tagger.intra_op_num_threads is None
    assert tagger.session_options.intra_op_num_threads == 0
    assert tagger.session.get_session_options().intra_op_num_threads == 0


@requires_model
@requires_dataset
def test_explicit_intra_op_threads_reach_the_session(
    tagger: "wd14.WD14Tagger", real_images: list[Path]
) -> None:
    """The acceptance requirement: assert this parameter really reaches SessionOptions and is
    really used to build the session.

    This also asserts the thread count **does not affect the result**: measured locally, default /
    4 / 1 all give bit-identical output.
    """
    onnx_path, vocab_path = registry.resolve(MODEL_ID, SEARCH_ROOTS)
    threaded = wd14.WD14Tagger(onnx_path, vocab_path, intra_op_num_threads=4)

    assert threaded.intra_op_num_threads == 4
    assert threaded.session_options.intra_op_num_threads == 4
    assert threaded.session.get_session_options().intra_op_num_threads == 4
    assert threaded.predict(real_images[0]) == tagger.predict(real_images[0])


# --------------------------------------------------------------------------
# Boundary guards
# --------------------------------------------------------------------------


def _imported_modules(module: object) -> set[str]:
    source = Path(inspect.getsourcefile(module)).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def test_autotag_never_imports_core_or_fastapi() -> None:
    """p0-spec §3.0's mandatory dependency direction: autotag/* must not import core/*, nor import FastAPI."""
    from kohya_dataset_tagger import autotag as package

    for module in (package, wd14, registry, pp):
        imported = _imported_modules(module)
        offender = sorted(
            name for name in imported if "core" in name.split(".") or name.split(".")[0] == "fastapi"
        )
        assert not offender, "%s violates the dependency direction: %s" % (module.__name__, offender)


def test_wd14_does_not_import_onnxruntime_at_module_scope() -> None:
    """onnxruntime must be imported lazily, so that `preprocess` and vocabulary parsing work without it."""
    source = Path(inspect.getsourcefile(wd14)).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.Import):
            assert all(alias.name != "onnxruntime" for alias in node.names), "onnxruntime must not be at module top level"
