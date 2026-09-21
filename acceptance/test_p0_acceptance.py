"""P0 acceptance suite -- see .github/memory/p0-spec.md §5 for the standard.

This file belongs to the accepting side (the main agent), not to the implementer. The implementer's
unit tests live under test/; this suite is deliberately kept separate so that running pytest test/
does not drag in acceptance criteria that are not finished yet.

How to run (working directory = repository root):

    .\\.venv\\Scripts\\python.exe -m pytest acceptance/ -q

The criterion numbers map one-to-one onto p0-spec §5. Each one demands **executable evidence**;
"looks right" does not count.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import local_paths  # noqa: E402  (the repository root is not a package; the bootstrap above finds it)

REPO = Path(__file__).resolve().parents[1]
#: Every external path comes from `local_paths.ini` (gitignored). The committed template points at the
#: sample dataset under test/fixtures/sample_dataset/, so acceptance runs on a fresh clone too.
DATA = local_paths.dataset_root()
MODEL_ROOTS = local_paths.model_roots()
VENV_PY = REPO / ".venv" / "Scripts" / "python.exe"


def _require_model(model_id: str = "wd-swinv2-tagger-v3"):
    """Resolve a tagger model, skipping **with the reason** when the configured roots have none."""
    from kohya_dataset_tagger.autotag import registry

    try:
        return registry.resolve(model_id, MODEL_ROOTS)
    except Exception as exc:  # UnknownModel / ModelNotFound: both mean "not on this machine"
        pytest.skip("no %s under the configured model roots (%s): %s" % (model_id, MODEL_ROOTS, exc))

#: A single backslash. chr(92) rather than a literal, so the source does not lose count of the layers.
BS = chr(92)

#: A defect sample from the dataset that produced this repository (one subset directory, all 6 .txt files):
#: the generator wrote one extra backslash between the character tag and the next tag.
#: **Verified byte by byte** (code points taken from the backup file): the real form is
#: `fugue \(honkai: star rail)\,` -- only the **opening** parenthesis is escaped, the closing one
#: is bare and followed by a stray backslash.
#: Code point sequence: ... 108, 41, 92, 44 ... i.e. `l` `)` `\` `,`; there is **no** backslash
#: before the closing parenthesis.
#: The trainer splits on bare commas, so what it reads is a **tag with a trailing backslash**.
#: The editor must show the same thing -- otherwise the user cannot see the problem that needs fixing.
REAL_DEFECT_LINE = (
    "1girl, fugue " + BS + "(honkai: star rail)" + BS + ", hair ornament, tongue out"
)


def trainer_split(text: str) -> list[str]:
    """The trainer's split rule: [t.strip() for t in caption.split(",")] from train_util.py:900."""
    return [t.strip() for t in text.strip().split(",") if t.strip()]


# --------------------------------------------------------------------------
# Global state isolation
# --------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _restore_global_config():
    r"""Restore `config._CONFIG` and the path whitelist around every test case.

    `config.load()` changes two pieces of **global** state at once: `config._CONFIG` and the root
    whitelist in `paths`. If a case does not restore them, later cases see another case's
    leftovers. We were burned by this twice on 2026-09-17:

      1. `api_client` was written with module scope -- the whitelist was published only the first
         time, and once another case overwrote it, it never came back
         (symptom: A12 failed in a full run and passed on its own)
      2. A26 called `config.load(env={KOHYA_TAGGER_MODELS: ...})` without restoring it, so the
         `_download_root()` A25 read was the one A26 left behind -- `-k "A25 or A26"` red, each one
         alone green

    Caught in one place here, so every new test case does not have to remember to restore.
    (Reading and writing the private `_CONFIG` is deliberate: it is the only home of "the current
    configuration" and there is no public read-back API.)
    """
    from kohya_dataset_tagger import config as _config
    from kohya_dataset_tagger.core import paths as _paths

    saved_config = getattr(_config, "_CONFIG", None)
    saved_roots = _paths.get_roots()
    yield
    _config._CONFIG = saved_config
    _paths.configure(saved_roots)


# --------------------------------------------------------------------------
# A1  Read-only boundary
# --------------------------------------------------------------------------


def _manifest_run(*args: str) -> subprocess.CompletedProcess:
    """Run the fingerprint tool once (§0.4).

    Explicit UTF-8: with the locale default (GBK on this machine) the subprocess reader thread
    raises UnicodeDecodeError on output containing Chinese, stdout becomes None, and the criterion
    turns into a "random false red".
    """
    return subprocess.run(
        [sys.executable, str(REPO / "tools" / "dataset_manifest.py"), "--root", str(DATA), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


#: A1's "before the run" fingerprint. **Deliberately not a stored snapshot**: the dataset is alive --
#: adding images and fixing captions are legitimate user actions, so a stored snapshot is bound to
#: expire (measured 2026-09-19: the stored one held 3306 entries / 1653 .txt files while the live
#: dataset already had 4959).
#: Pinning the automatic gate to a snapshot only turns every legitimate change red, and what turns
#: red is the **data**, not the **code**.
_A1_BEFORE = Path(tempfile.mkdtemp(prefix="kdt-a1-manifest-")) / "before.json"


@pytest.fixture(scope="session", autouse=True)
def _a1_dataset_untouched():
    """A1 (relative version): after the whole acceptance run, the read-only test set's fingerprint
    must be byte-for-byte identical to **before the run**.

    Drop a fingerprint with --out before the run, then compare against **the same file** with
    --check at the end of the session: a mismatch = some code path wrote to the read-only test set.
    That is the executable criterion for "do not write the dataset".

    A fingerprint you stored yourself can still be checked by hand with
    tools/dataset_manifest.py --root <dataset> --check <your snapshot file>,
    but that is a **snapshot** criterion and not an automatic gate -- it expires as soon as the
    dataset changes.
    """
    if not DATA.is_dir():
        yield
        return
    made = _manifest_run("--out", str(_A1_BEFORE))
    if made.returncode != 0:
        yield
        return
    yield
    proc = _manifest_run("--check", str(_A1_BEFORE))
    assert proc.returncode == 0, (
        "the read-only test set's fingerprint changed after the acceptance run -- some code path wrote it (A1):\n%s\n%s"
        % (proc.stdout, proc.stderr)
    )


def test_A1_dataset_read_only_guard_is_armed() -> None:
    """A1: the real closing assertion lives in the session fixture _a1_dataset_untouched.

    This one only proves the guard is **actually armed** (the dataset exists, the before-the-run
    fingerprint was taken), so that a missing dataset does not let the closing assertion silently
    test nothing -- "the criterion passes but is not testing anything" is a pit this repository has
    stepped into repeatedly.
    """
    assert DATA.is_dir(), "no dataset configured in local_paths.ini: %s" % DATA
    assert _A1_BEFORE.is_file(), "no fingerprint was taken before the run; the read-only guard is empty"
    snapshot = json.loads(_A1_BEFORE.read_text(encoding="utf-8"))
    assert snapshot["manifest_version"] == 1
    assert snapshot["file_count"] > 0, snapshot


# --------------------------------------------------------------------------
# A2 / A3  Dataset facts: non-recursive + subdirectories
# --------------------------------------------------------------------------


def test_A2_loader_is_non_recursive_and_the_listing_agrees_with_itself() -> None:
    """A2 without a golden number: the per-subdirectory counts and the recursive count must agree.

    The number itself (1653) is deliberately gone: the dataset is alive, and pinning a count turned
    this criterion red the moment a legitimate edit added an image. The invariant A2 is about holds
    for any content.
    """
    from kohya_dataset_tagger.core import paths

    assert paths.iter_images(DATA) == [], "the parent directory must not contain images directly (the trainer is non-recursive)"
    subs = paths.list_subdirs(DATA)
    assert subs, "the configured dataset has no subdirectory at all: %s" % DATA
    total = sum(paths.count_images(d) for d in subs)
    assert total == paths.count_images(DATA, recursive=True), (
        "the per-subdirectory counts and the recursive count disagree")


def test_A3_subdir_listing_excludes_cache_and_hidden() -> None:
    from kohya_dataset_tagger.core import paths

    subs = paths.list_subdirs(DATA)
    names = {p.name for p in subs}
    assert not (names & set(paths.CACHE_DIRNAMES)), "cache directories must not show up in the listing"
    assert not [n for n in names if n.startswith(".")], "hidden directories must not show up in the listing"


# --------------------------------------------------------------------------
# A4  Path whitelist
# --------------------------------------------------------------------------


def test_A4_path_whitelist_rejects_escapes() -> None:
    from kohya_dataset_tagger.core import paths

    paths.configure([DATA])
    # Escapes and absolute paths outside the roots raise PathOutsideRoots; inputs that do not
    # resolve to a real file raise OSError.
    # Both count as failure -- the criterion must not hold only when "the path was concatenated right".
    for bad in ["../..", str(DATA / ".." / ".."), r"C:\\Windows\\win.ini",
                "..%2f..", "", "no-such-entry"]:
        with pytest.raises((paths.PathOutsideRoots, ValueError, OSError)):
            paths.resolve_under_roots(bad)

    ok = paths.resolve_under_roots(str(DATA))
    assert ok == DATA.resolve()


def test_A4b_junction_and_prefix_escapes_are_blocked() -> None:
    """An escape probe independent of the implementer: build our own junction and a same-prefix
    sibling directory, then ask core.paths directly.

    The probe first asserts the junction really does lead outside the root, then demands that it be
    blocked -- so it cannot pass out of thin air.
    """
    proc = subprocess.run([str(VENV_PY), str(REPO / "tools" / "probe_whitelist.py")],
                          cwd=REPO, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    assert proc.returncode == 0, "the path whitelist has an escape:\n%s\n%s" % (proc.stdout, proc.stderr)
    assert "ALL BLOCKED" in proc.stdout, proc.stdout


# --------------------------------------------------------------------------
# A5  Invariant number one: the three cache-invalidation paths
# --------------------------------------------------------------------------


@pytest.fixture()
def sandbox(tmp_path: Path):
    """A copy of real images + captions. Never writes the original directory."""
    from kohya_dataset_tagger.core import paths

    src_dir = paths.list_subdirs(DATA)[0]
    imgs = paths.iter_images(src_dir)
    assert imgs, "the sampled directory has no images"
    work = tmp_path / "subset"
    work.mkdir()
    for img in imgs[:2]:
        shutil.copy2(img, work / img.name)
        cap = img.with_suffix(".txt")
        if cap.exists():
            shutil.copy2(cap, work / cap.name)
    return work


def _first_image(sandbox: Path) -> Path:
    return sorted(p for p in sandbox.iterdir() if p.suffix.lower() != ".txt")[0]


def test_A5_cache_invalidation_normal_path(sandbox: Path) -> None:
    """Normal: after a caption change the TE cache must be gone, and WriteResult must record it."""
    from kohya_dataset_tagger.core import cache_invalidation as ci
    from kohya_dataset_tagger.core import captions

    img = _first_image(sandbox)
    cache_dir = sandbox / ci.TE_CACHE_DIRNAME
    cache_dir.mkdir(exist_ok=True)
    fake = cache_dir / (img.stem + "_anima_te.safetensors")
    fake.write_bytes(b"stale")

    res = captions.write_caption(img, ["1girl", "solo"])
    assert not fake.exists(), "a caption was written but the TE cache was not deleted -- invariant number one is violated"
    assert str(fake) in {str(p) for p in res.cache_removed}


def test_A5_cache_invalidation_failure_path(sandbox: Path) -> None:
    """Failure: when the cache cannot be deleted it must be reported, **and the caption must still
    be written correctly**.

    This is the second half of invariant number one -- a failed delete may not be swallowed.
    On Windows, holding one read handle is enough to make os.remove raise PermissionError(winerror 32).
    """
    from kohya_dataset_tagger.core import cache_invalidation as ci
    from kohya_dataset_tagger.core import captions

    img = _first_image(sandbox)
    cache_dir = sandbox / ci.TE_CACHE_DIRNAME
    cache_dir.mkdir(exist_ok=True)
    locked = cache_dir / (img.stem + "_anima_te.safetensors")
    locked.write_bytes(b"locked")

    with open(locked, "rb"):
        res = captions.write_caption(img, ["1girl", "locked"])
        if not res.cache_remove_failed:
            pytest.skip("this platform allows deleting a locked file; the failure path cannot be constructed")
        assert locked.exists(), "it reported a failed delete yet the file is gone -- the report is fake"
        # the caption itself is still written correctly
        assert captions.split_tags(captions.read_caption(img)[0]) == ["1girl", "locked"]

    # after the handle is released, a retry deletes it (recovery path)
    ci.invalidate_text_encoder_cache(img)
    assert not locked.exists(), "still cannot be deleted after the handle was released"


def test_A5_cache_invalidation_recovery_path(sandbox: Path) -> None:
    """Recovery: after the cache directory is cleared there are no stale entries left."""
    from kohya_dataset_tagger.core import cache_invalidation as ci

    ci.find_stale_encoder_caches(sandbox)
    cache_dir = sandbox / ci.TE_CACHE_DIRNAME
    if cache_dir.exists():
        shutil.rmtree(cache_dir)
    assert ci.find_stale_encoder_caches(sandbox) == [], "an emptied cache directory must not report stale entries"


# --------------------------------------------------------------------------
# A6  Split rule and round trip
# --------------------------------------------------------------------------


@pytest.mark.parametrize("tags", [
    ["1girl", "solo"],
    ["long hair", "blue eyes"],
    ["hatsune miku", "vocaloid"],
    ["kagura " + chr(92) + "(gintama" + chr(92) + ")", "silver hair"],
    ["初音ミク", "ツインテール"],
    ["(masterpiece:1.2)", "best quality"],
    ["hu tao " + chr(92) + "(genshin impact" + chr(92) + ")", "1girl"],
])
def test_A6_caption_roundtrip(tmp_path: Path, tags: list[str]) -> None:
    from kohya_dataset_tagger.core import captions

    img = tmp_path / "x.png"
    img.write_bytes(b"")
    captions.write_caption(img, tags, invalidate=False)
    got = captions.split_tags(captions.read_caption(img)[0])
    assert got == tags, "round trip mismatch: %r -> %r" % (tags, got)


@pytest.mark.parametrize("raw", [
    "1girl, solo, long hair",
    "  spaced ,  out  ",
    "",
    REAL_DEFECT_LINE,
    "a" + BS + ",b",
    "kagura " + BS + "(gintama" + BS + "), silver hair",
])
def test_A6_split_matches_trainer_exactly(raw: str) -> None:
    """The editor's view must equal what the trainer will read.

    This is the same principle as this repository's invariant number one: the user must not be kept
    from seeing a problem that the training run will really read.
    """
    from kohya_dataset_tagger.core import captions

    assert captions.split_tags(raw) == trainer_split(raw), (
        "the split rule does not match the trainer: %r" % raw
    )


def test_A6_real_dataset_defect_is_visible() -> None:
    r"""The defective sample (one subset directory, all 6 of its .txt files) must be shown as a tag with a
    trailing backslash.

    The generator wrote one extra backslash. If the implementation unescaped commas, this tag would
    be "fixed" into `fugue \(honkai: star rail\),` -- looking clean, while the trainer still reads
    it dirty."""
    from kohya_dataset_tagger.core import captions

    got = captions.split_tags(REAL_DEFECT_LINE)
    assert got == trainer_split(REAL_DEFECT_LINE)
    assert got[1] == "fugue " + BS + "(honkai: star rail)" + BS, (
        "the defect is not shown faithfully: %r" % (got[1],)
    )


def test_A6_comma_inside_tag_is_rejected(tmp_path: Path) -> None:
    """A tag containing a comma must be rejected on write.

    The trainer splits on bare commas, so once a tag like "a,b" hits disk it is read as two tags.
    Writing it out silently means silently producing wrong training data."""
    from kohya_dataset_tagger.core import captions

    img = tmp_path / "y.png"
    img.write_bytes(b"")
    with pytest.raises(ValueError):
        captions.write_caption(img, ["a,b", "c"], invalidate=False)


# --------------------------------------------------------------------------
# A7  Atomic write
# --------------------------------------------------------------------------


def test_A7_atomic_write_leaves_no_residue(tmp_path: Path) -> None:
    from kohya_dataset_tagger.core import captions

    img = tmp_path / "y.png"
    img.write_bytes(b"")
    captions.write_caption(img, ["1girl"], invalidate=False)
    before = {p.name for p in tmp_path.iterdir()}
    captions.write_caption(img, ["1girl", "solo"], invalidate=False)
    after = {p.name for p in tmp_path.iterdir()}
    assert after == before, "writing left residue files: %r" % (after - before)


# --------------------------------------------------------------------------
# A8  WD14 preprocessing
# --------------------------------------------------------------------------


def test_A8_preprocess_contract() -> None:
    import numpy as np
    from PIL import Image
    from kohya_dataset_tagger.autotag.wd14 import preprocess

    out = preprocess(Image.new("RGB", (64, 64), (255, 0, 0)))
    assert out.shape == (448, 448, 3)
    assert out.dtype == np.float32
    assert out.max() == 255.0, "the maximum should be 255 (no division by 255), actual %r" % out.max()
    ch = out.mean(axis=(0, 1))
    assert ch[2] > 200 and ch[0] < 20, "channel order is not BGR: means %r (a square pure-red image should be ch2≈255, ch0≈0)" % (ch,)

    padded = preprocess(Image.new("RGB", (100, 300), (255, 0, 0)))
    assert tuple(padded[0, 0]) == (255.0, 255.0, 255.0), "padding should be white 255, actual %r" % (padded[0, 0],)


# --------------------------------------------------------------------------
# A9  Thumbnails
# --------------------------------------------------------------------------


def test_A9_thumbnail_pipeline(tmp_path: Path) -> None:
    from PIL import Image
    from kohya_dataset_tagger.core import thumbs

    big = tmp_path / "big.jpg"
    Image.new("RGB", (6000, 7000), (10, 120, 200)).save(big, quality=80)

    cache_root = tmp_path / "outside" / "thumbs"
    out = thumbs.ensure_thumb(big, thumbs.GRID_THUMB, cache_root)
    assert out.exists() and out.stat().st_size > 0

    with Image.open(out) as im:
        assert max(im.size) == thumbs.GRID_THUMB, "the longest edge should be %d, actual %r" % (thumbs.GRID_THUMB, im.size)

    mtime = out.stat().st_mtime_ns
    out2 = thumbs.ensure_thumb(big, thumbs.GRID_THUMB, cache_root)
    assert out2 == out
    assert out2.stat().st_mtime_ns == mtime, "the second call did not hit the cache (it regenerated)"


def test_A9b_thumbnail_cache_is_refused_inside_dataset(tmp_path: Path) -> None:
    """A thumbnail cache directory inside the dataset must be refused -- otherwise it pollutes image_dir."""
    from PIL import Image
    from kohya_dataset_tagger.core import paths, thumbs

    dataset = tmp_path / "dataset"
    dataset.mkdir()
    img = dataset / "a.png"
    Image.new("RGB", (64, 64), (1, 2, 3)).save(img)

    paths.configure([dataset])
    with pytest.raises(Exception):
        thumbs.ensure_thumb(img, thumbs.GRID_THUMB, dataset / "thumbs")

    ok = thumbs.ensure_thumb(img, thumbs.GRID_THUMB, tmp_path / "thumbs")
    assert ok.exists()


# --------------------------------------------------------------------------
# A10 / A11  Tagger and vocabulary
# --------------------------------------------------------------------------


def test_A11_vocabulary_from_local_csv() -> None:
    from kohya_dataset_tagger.core.vocab import Vocabulary
    from kohya_dataset_tagger.autotag import registry

    onnx_path, vocab_path = _require_model()
    vocab = Vocabulary.from_csv(Path(vocab_path))
    assert len(vocab) == 10861, "swinv2-v3's vocabulary should be 10861 rows on this machine, actual %d" % len(vocab)


def test_A10_wd14_real_inference_is_nonempty_and_deterministic() -> None:
    from kohya_dataset_tagger.core import paths
    from kohya_dataset_tagger.autotag import registry
    from kohya_dataset_tagger.autotag.wd14 import WD14Tagger

    onnx_path, vocab_path = _require_model()
    tagger = WD14Tagger(onnx_path, vocab_path)

    sample: list[Path] = []
    for d in paths.list_subdirs(DATA):
        sample.extend(paths.iter_images(d))
        if len(sample) >= 20:
            break
    sample = sample[:20]
    assert len(sample) == 20

    first = tagger.predict_batch(sample)
    assert all(tags for tags in first), "some image returned an empty tag list"
    again = tagger.predict_batch(sample)
    assert [[t.tag for t in r] for r in first] == [[t.tag for t in r] for r in again], "inference is not deterministic"


# --------------------------------------------------------------------------
# A26  Extra model scan paths "append", they do not "replace"
# --------------------------------------------------------------------------


def test_A26_extra_model_roots_append_and_do_not_replace() -> None:
    """The extra scan paths of §3.7 / §4.9.

    The distinction is practical: KOHYA_TAGGER_MODELS is a **whole-list replacement** -- setting it
    throws away the repository's models/ and every auto-discovered location; a user who "adds one
    more directory" wants an **append**. Both semantics must exist at once and not disturb each
    other.
    """
    from kohya_dataset_tagger import config

    repo_models = REPO / "models"
    plain = config.default_model_search_roots(environ={})
    assert plain[0] == repo_models, "the repository's models/ must be position 0 (A25 guards this too)"

    extra = [Path("D:/my-taggers"), Path("E:/more")]
    with_extra = config.default_model_search_roots(environ={}, extra=extra)
    assert with_extra[:3] == (repo_models, *extra), (
        "extras should come right after position 0: %r" % (with_extra[:4],))
    assert with_extra[3:] == plain[1:], "extras must not disturb the rest of the order"

    # The environment-variable form
    cfg = config.load(env={"KOHYA_TAGGER_EXTRA_MODELS": "D:/env1;E:/env2"})
    assert [p.name for p in cfg.model_search_roots[1:3]] == ["env1", "env2"]

    # KOHYA_TAGGER_MODELS keeps the final say: still the whole list, with extras appended after it
    cfg2 = config.load(env={"KOHYA_TAGGER_MODELS": "Z:/only", "KOHYA_TAGGER_EXTRA_MODELS": "D:/extra1"})
    assert [p.name for p in cfg2.model_search_roots] == ["only", "extra1"]


# --------------------------------------------------------------------------
# A25  The promise of the models/ directory must be true
# --------------------------------------------------------------------------


def test_A25_the_models_directory_is_search_root_zero_and_download_target() -> None:
    r"""The placeholder file in `models/` says "put the tagger models in this directory" -- that
    sentence must be true.

    Three things must hold at once:

      1. It is **position 0** of the default search roots (a model dropped in by hand must be picked
         over any cached copy)
      2. **It is where downloads land** (where the user follows the instructions to put a model is
         the same place automatic download goes)
      3. The placeholder is there and empty (git does not track empty directories, which is why it
         is needed; the explanation is carried by the file name)

    This criterion guards **documentation/placeholder and code behavior agreeing**.
    It did not hold when first changed on 2026-09-17: position 0 of the search roots was still the
    HF cache while my comment said "the repository's models/ comes first" -- the comment and the
    code disagreed, and only running it showed that.
    """
    import os

    from kohya_dataset_tagger import config
    from kohya_dataset_tagger.api import autotag

    # Establish a known state first; do not rely on "the _CONFIG left by the previous case happens to be clean"
    config.load(env={})
    roots = config.default_model_search_roots()
    repo_models = REPO / "models"
    assert os.path.normcase(str(roots[0])) == os.path.normcase(str(repo_models)), (
        "the repository's models/ is not position 0 of the search roots; measured position 0 is %r" % (roots[0],))

    # _download_root is a private function, but it is the single source of truth for "where downloads land", so ask it directly
    assert os.path.normcase(str(autotag._download_root())) == os.path.normcase(str(roots[0])), (
        "the download target and search root position 0 disagree: %r vs %r" % (autotag._download_root(), roots[0]))

    files = [p for p in repo_models.iterdir() if p.is_file()]
    assert files, ("there is no placeholder file in models/ -- git does not track an empty directory, "
                   "so anyone cloning the repo would not have this directory at all and the hint would never be seen")
    assert any(p.stat().st_size == 0 for p in files), (
        "the placeholder file is not empty (the explanation is supposed to be carried by the file name): %r"
        % [(p.name, p.stat().st_size) for p in files])


# --------------------------------------------------------------------------
# A24  Autocomplete must support substring matching
# --------------------------------------------------------------------------


def test_A24_autocomplete_matches_substrings_and_ranks_prefixes_first(api_client) -> None:
    """§4.10: prefix matching is not enough for danbooru tags.

    A character tag in the vocabulary looks like `ganyu_(genshin_impact)`; **no** tag starts with
    genshin, and even genshin_impact cannot be found (there is still "(_" in front). Yet what users
    remember is usually "this character is from Genshin".

    This was the first contract gap in this repository caught by an **end-to-end smoke** run
    (tools/e2e_smoke.py) rather than by a unit test.
    """
    resp = api_client.get("/api/tags/autocomplete", params={"q": "genshin", "limit": 5})
    assert resp.status_code == 200, resp.text[:200]
    tags = [s["tag"] for s in resp.json()["suggestions"]]
    assert tags, "genshin finds nothing -- substring matching is not in effect"
    assert any("genshin" in t for t in tags), tags

    # Prefix hits must rank before substring hits (keep the good property of prefix matching)
    resp2 = api_client.get("/api/tags/autocomplete", params={"q": "hair", "limit": 10})
    tags2 = [s["tag"] for s in resp2.json()["suggestions"]]
    prefix_hits = [t for t in tags2 if t.startswith("hair")]
    substring_only = [t for t in tags2 if not t.startswith("hair")]
    if prefix_hits and substring_only:
        assert (max(tags2.index(t) for t in prefix_hits)
                < min(tags2.index(t) for t in substring_only)), (
            "prefix hits are not ranked before substring hits: %r" % tags2)

    # Case-insensitive + deterministic result
    upper = [s["tag"] for s in api_client.get(
        "/api/tags/autocomplete", params={"q": "GENSHIN", "limit": 5}).json()["suggestions"]]
    assert upper == tags, "case-sensitive or non-deterministic result: %r vs %r" % (upper, tags)


# --------------------------------------------------------------------------
# A23  The generated dataset.toml must be accepted by the trainer's own validator
# --------------------------------------------------------------------------

#: A checkout of Anima-Standalone-Trainer, from `local_paths.ini` (unset by default; A23 then skips).
TRAINER_ROOT = local_paths.trainer_root()
TRAINER_PY = (TRAINER_ROOT / "venv" / "Scripts" / "python.exe") if TRAINER_ROOT else None


def _trainer_verdict(toml_path: Path) -> dict:
    """Let the trainer's own config validator score it, and get back a **structured** verdict.

    This is the only criterion in this repository scored by the **real consumer**: it is not us
    saying "this toml is right", but the trainer reading it in, validating it, building the
    blueprint, and the subset count matching.

    **Do not parse human-readable logs**: `python -m library.config_util` prints pprint, which
    **wraps lines**, and the 219 `image_dir=` entries can only be counted as 218 in the log. I fell
    into this pit twice on 2026-09-17 (first counting stdout gave 0, then counting stderr gave 218)
    -- so this was changed to have tools/verify_toml_with_trainer.py emit one line of JSON.
    """
    proc = subprocess.run(
        [str(TRAINER_PY), str(REPO / "tools" / "verify_toml_with_trainer.py"), str(toml_path)],
        cwd=str(REPO), capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    lines = [l for l in (proc.stdout or "").splitlines() if l.strip().startswith("{")]
    assert lines, ("the validator did not output JSON:\nstdout=%s\nstderr=%s"
                   % (proc.stdout[-600:], proc.stderr[-600:]))
    return json.loads(lines[-1])


@pytest.mark.skipif(not TRAINER_PY.is_file(), reason="no trainer checkout configured (set 'trainer' in local_paths.ini)")
def test_A23_generated_toml_is_accepted_by_the_trainer(tmp_path) -> None:
    from kohya_dataset_tagger.core import paths
    from kohya_dataset_tagger.core.dataset_toml import DatasetTomlParams, plan_dataset_toml

    paths.configure([DATA])
    plan = plan_dataset_toml(DATA, DatasetTomlParams())

    populated = [d for d in paths.list_subdirs(DATA) if paths.count_images(d)]
    assert len(plan.subsets) == len(populated), (
        "subset count %d != the number of populated subdirectories %d" % (len(plan.subsets), len(populated)))
    assert plan.image_count == sum(paths.count_images(d) for d in populated)

    out = tmp_path / "dataset.toml"
    out.write_text(plan.toml_text, encoding="utf-8")
    verdict = _trainer_verdict(out)

    assert verdict.get("ok") is True, (
        "the trainer's own validator rejected our output (this is the criterion): %s" % verdict.get("error"))
    assert verdict["subsets"] == len(plan.subsets), (
        "the validator built %d subsets, the plan had %d" % (verdict["subsets"], len(plan.subsets)))
    assert verdict["image_dirs"] == [str(s.image_dir) for s in plan.subsets], (
        "the image_dir values the validator read do not match the plan")


@pytest.mark.skipif(not TRAINER_PY.is_file(), reason="no trainer checkout configured (set 'trainer' in local_paths.ini)")
def test_A23b_unescaped_backslashes_must_be_rejected(tmp_path) -> None:
    r"""The reverse criterion: write backslashes into a basic string unescaped and the validator
    **must** fail.

    A criterion only means something when it can fail. This also proves A23's escaping is not
    gratuitous (measured 2026-09-17: unescaped -> ValueError: Reserved escape sequence used).
    """
    from kohya_dataset_tagger.core import paths
    from kohya_dataset_tagger.core.dataset_toml import DatasetTomlParams, plan_dataset_toml

    paths.configure([DATA])
    plan = plan_dataset_toml(DATA, DatasetTomlParams())
    broken = plan.toml_text.replace(chr(92) * 2, chr(92))       # \ -> 
    assert broken != plan.toml_text, "the generated toml has no double backslashes, so this criterion is not testing anything"

    out = tmp_path / "broken.toml"
    out.write_text(broken, encoding="utf-8")
    verdict = _trainer_verdict(out)

    assert verdict.get("ok") is False, (
        "a toml with unescaped backslashes was accepted -- either the escaping is gratuitous or the criterion is broken: %r" % verdict)
    assert "escape" in str(verdict.get("error", "")).lower(), (
        "it failed, but not because of escaping -- so this criterion is not testing what it thinks it is: %r"
        % verdict.get("error"))


# --------------------------------------------------------------------------
# A20 / A21  Model download and layout discovery
# --------------------------------------------------------------------------


class _FakeEndpoint:
    """A minimal fake HF endpoint: /api/models/<repo> manifest + /<repo>/resolve/main/<file>.

    Written by the accepting side, not reusing the implementer's test fixtures -- A20 has to hold
    independently.
    """

    def __init__(self, files: dict, *, truncate: str | None = None, port: int = 0):
        import http.server
        import json as _json
        import threading

        outer = self
        self.files = files
        self.truncate = truncate
        self.hits: list[str] = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):        # silence
                pass

            def do_GET(self):
                outer.hits.append(self.path)
                if self.path.startswith("/api/models/"):
                    body = _json.dumps({"siblings": [
                        {"rfilename": n, "size": len(b)} for n, b in outer.files.items()
                    ]}).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                name = self.path.rsplit("/", 1)[-1].split("?")[0]
                if name in outer.files:
                    data = outer.files[name]
                    if outer.truncate == name:
                        data = data[: max(1, len(data) - 3)]     # deliberately a few bytes short
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                self.send_error(404)

        self.server = http.server.HTTPServer(("127.0.0.1", port), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return "http://127.0.0.1:%d" % self.server.server_address[1]

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def test_A20_download_writes_exact_bytes_and_reports_progress(monkeypatch, tmp_path) -> None:
    """The whole download chain: manifest -> streaming to disk -> byte-count verification -> progress monotonically up to the total -> idempotent skip."""
    from kohya_dataset_tagger.autotag import download as dl

    payload = {"model.onnx": b"ONNX" * 5000, "selected_tags.csv": b"tag_id,name\n"}
    ep = _FakeEndpoint(payload)
    monkeypatch.setenv(dl.HF_ENDPOINT_ENV, ep.url)
    try:
        seen: list[tuple[str, int, int]] = []
        res = dl.download("wd-swinv2-tagger-v3", tmp_path,
                          progress=lambda name, done, total: seen.append((name, done, total)))

        assert res.onnx_path.read_bytes() == payload["model.onnx"], "what landed on disk does not match the endpoint"
        assert res.vocab_path.read_bytes() == payload["selected_tags.csv"]
        assert res.endpoint == ep.url, "the successful endpoint was not remembered: %r" % res.endpoint
        assert not res.skipped_existing

        total = len(payload["model.onnx"]) + len(payload["selected_tags.csv"])
        dones = [d for _, d, _ in seen]
        assert dones == sorted(dones), "the progress callbacks are not monotonically non-decreasing: %r" % dones
        assert dones and dones[-1] == total, "final progress %r != total bytes %d" % (dones[-1:], total)

        # Idempotent: the second call must not issue any download request
        ep.hits.clear()
        again = dl.download("wd-swinv2-tagger-v3", tmp_path)
        assert again.skipped_existing, "when it exists with the right size it must be skipped"
        assert not [h for h in ep.hits if "resolve/main" in h], (
            "the idempotent path still issued a download request: %r" % ep.hits)
    finally:
        ep.close()


def test_A20b_download_cleans_up_on_size_mismatch(monkeypatch, tmp_path) -> None:
    """A byte-count mismatch must raise, and leave **not a single residue file** (no .part, no half a model set)."""
    from kohya_dataset_tagger.autotag import download as dl

    payload = {"model.onnx": b"X" * 4096, "selected_tags.csv": b"tag_id,name\n"}
    ep = _FakeEndpoint(payload, truncate="model.onnx")
    monkeypatch.setenv(dl.HF_ENDPOINT_ENV, ep.url)
    try:
        with pytest.raises(Exception) as excinfo:
            dl.download("wd-swinv2-tagger-v3", tmp_path, endpoint=ep.url)
        leftovers = sorted(p.name for p in tmp_path.rglob("*") if p.is_file())
        assert leftovers == [], "a byte-count mismatch left residue: %r (error message: %s)" % (
            leftovers, str(excinfo.value)[:120])
    finally:
        ep.close()


def test_A20c_plan_falls_back_when_the_first_endpoint_is_dead(monkeypatch) -> None:
    """A dead first endpoint must fall back automatically.

    The approach: **set** the configurable endpoint to a dead port so the fallback chain lands on a
    real endpoint. Only the manifest JSON is fetched (a few KB); **no model file is downloaded**.
    """
    from kohya_dataset_tagger.autotag import download as dl

    dead = "http://127.0.0.1:9"          # discard port, the connection is bound to fail
    monkeypatch.setenv(dl.HF_ENDPOINT_ENV, dead)
    endpoints = dl.endpoints()
    assert endpoints[0] == dead, "the environment-variable endpoint must come first: %r" % (endpoints,)
    assert len(endpoints) >= 2, "the fallback chain should still have real endpoints: %r" % (endpoints,)

    try:
        result = dl.plan("wd-eva02-large-tagger-v3")
    except Exception as exc:              # skip when no real endpoint is reachable here; not a failure
        pytest.skip("none of the real endpoints in the fallback chain are reachable: %s" % str(exc)[:120])
    assert result.total_bytes > 0


def test_A21_registry_finds_the_flat_layout(tmp_path) -> None:
    """Files dropped into the model directory by hand must be discoverable.

    Otherwise the escape hatch of "if it will not download, grab it yourself and drop it in" is a
    lie -- and that is the path most likely to be taken on a China-side network.
    """
    from kohya_dataset_tagger.autotag import registry

    root = tmp_path / "models"
    d = root / "wd-swinv2-tagger-v3"
    d.mkdir(parents=True)
    (d / "model.onnx").write_bytes(b"x" * 16)
    (d / "selected_tags.csv").write_text("tag_id,name,category,count\n", encoding="utf-8")

    onnx_path, vocab_path = registry.resolve("wd-swinv2-tagger-v3", [root])
    assert Path(onnx_path) == (d / "model.onnx"), "the flat layout was not discovered: %r" % (onnx_path,)
    assert Path(vocab_path) == (d / "selected_tags.csv"), "the vocabulary was not taken from the same directory: %r" % (vocab_path,)


# --------------------------------------------------------------------------
# A22  preview cap
# --------------------------------------------------------------------------


def test_A22_preview_cap_rejects_before_touching_paths(api_client) -> None:
    """33 images -> 400 too_many_images, and the rejection happens **before path resolution**.

    33 paths that do not exist at all are used: if the cap check happened after path resolution it
    would hit 404 instead of 400.
    """
    ghosts = [str(DATA / "no-such-dir" / ("g%02d.png" % i)) for i in range(33)]
    resp = api_client.post("/api/autotag/preview",
                           json={"images": ghosts, "model": "wd-swinv2-tagger-v3"})
    assert resp.status_code == 400, "%s %s" % (resp.status_code, resp.text[:200])
    body = resp.json()
    assert body.get("error", {}).get("code") == "too_many_images", body
    message = body["error"]["message"]
    assert "32" in message and "33" in message, "the error message does not state the cap and the actual image count: %r" % message


# --------------------------------------------------------------------------
# A19  Lone-paren kaomoji must not be mishandled
# --------------------------------------------------------------------------


def test_A19_escape_parens_handles_lone_paren_kaomoji() -> None:
    r"""A lone-paren kaomoji must be **escaped** correctly, not deleted or missed.

    The vocabulary has exactly 4 tags of this kind: ;)   >:)   ;(   >:(
    The user explicitly warned that this family must not be fiddled with. The expected form is
    escaping -- the dataset already uses ;), and it is one of two spellings of the same tag as the
    ;) in the vocabulary; the tagger must produce the one the dataset uses.
    """
    from kohya_dataset_tagger.autotag.base import TagScore
    from kohya_dataset_tagger.autotag.postprocess import (
        PostprocessOptions, format_tags, postprocess_tags,
    )

    def run(tag: str) -> str:
        return format_tags(postprocess_tags(
            [TagScore(tag=tag, score=0.9, category="General")], PostprocessOptions()))

    assert run(";)") == ";" + BS + ")", "a lone closing-paren kaomoji was mishandled"
    assert run(">:)") == ">:" + BS + ")"
    assert run(";(") == ";" + BS + "("
    assert run(">:(") == ">:" + BS + "("


def test_A19b_lone_paren_kaomoji_survives_a_caption_round_trip(tmp_path) -> None:
    r"""An escaped lone-paren kaomoji must still be the same tag after a caption write/read round
    trip.

    **Deliberately does not assert content against the real dataset**: the dataset is alive, users
    editing captions is legitimate, and a case saying "that tag must still be in the file" amounts
    to forbidding the user from editing -- it had already gone red by 2026-09-19 while the data
    itself was fine (see the skill's high-frequency pitfalls: tests must not assert the current
    content of user data).
    What really needs guarding is **tool behavior**: since the dataset uses this lone-paren family,
    neither writing nor splitting may drop it, break it apart, or pair it with another parenthesis.
    """
    from kohya_dataset_tagger.core import captions

    tags = ["1girl", ";" + BS + ")", "smile", ">:" + BS + "(", "hair ornament"]
    image = tmp_path / "sample.png"  # only a stem is needed to form <stem>.txt; the image itself need not exist
    result = captions.write_caption(image, tags, invalidate=False)
    assert result.changed is True
    assert result.caption_path.read_text(encoding="utf-8") == ", ".join(tags)

    text, multiline = captions.read_caption(image)
    assert multiline is False
    assert captions.split_tags(text) == tags, "the lone-paren kaomoji was altered/lost across the round trip"


# --------------------------------------------------------------------------
# A18  The ORT thread knob really reaches the session
# --------------------------------------------------------------------------


def test_A18_ort_thread_knob_reaches_the_session() -> None:
    """The intra_op_num_threads of §3.6 must really reach the ONNX session, without changing the
    result.

    This knob was earned by a measured 2.5x gap (3.45 -> 1.39 s/image), and the normal state of
    this machine is "training is running". "The parameter was added but has no effect" is the only
    failure mode that is unacceptable here -- it raises nothing, it is just 2.5x slower.
    """
    from kohya_dataset_tagger.core import paths
    from kohya_dataset_tagger.autotag import registry
    from kohya_dataset_tagger.autotag.wd14 import WD14Tagger

    paths.configure([DATA])
    onnx_path, vocab_path = _require_model()

    default = WD14Tagger(onnx_path, vocab_path)
    assert default.intra_op_num_threads in (0, None), (
        "the default must not set the thread count explicitly, actual %r" % (default.intra_op_num_threads,))

    limited = WD14Tagger(onnx_path, vocab_path, intra_op_num_threads=4)
    assert limited.intra_op_num_threads == 4
    assert limited.session.get_session_options().intra_op_num_threads == 4, (
        "the thread count did not reach the ONNX session -- the knob is fake")

    sample = None
    for d in paths.list_subdirs(DATA):
        imgs = paths.iter_images(d)
        if imgs:
            sample = imgs[0]
            break
    assert sample is not None
    assert [t.tag for t in default.predict(sample)] == [t.tag for t in limited.predict(sample)], (
        "changing the thread count changed the inference result")


# --------------------------------------------------------------------------
# A14 / A16  Gates and constant consistency
# --------------------------------------------------------------------------


def test_A14_docs_gate_still_green() -> None:
    proc = subprocess.run([str(VENV_PY), "-m", "pytest", "test/test_docs_index.py", "-q"],
                          cwd=REPO, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_A16_duplicated_constants_stay_in_sync() -> None:
    """§3.0 forbids core modules importing each other, and the price is duplicated constants. The duplication must be checked, otherwise it drifts."""
    from kohya_dataset_tagger.core import cache_invalidation, captions, paths

    assert cache_invalidation.IMAGE_EXTS == paths.IMAGE_EXTS, "the image extension whitelist differs between the two places"
    assert (cache_invalidation.CAPTION_EXT_DEFAULT
            == paths.CAPTION_EXT_DEFAULT
            == captions.CAPTION_EXT_DEFAULT), "the caption extension default differs between the places"
    assert captions.SEPARATOR == ", ", "the separator must be ', ' (hardcoded where the trainer reassembles)"

    # §3.0 also forbids autotag->core, so these two constant groups have a second copy in the
    # tagger. The duplication itself is acceptable; the drift is not -- so it is pinned here.
    # (Measured shape: vocab has string keys, autotag/base has integer keys, the rest is the same.)
    from kohya_dataset_tagger.core import vocab
    from kohya_dataset_tagger.autotag import base as autotag_base
    from kohya_dataset_tagger.autotag import postprocess as autotag_pp

    assert {str(k): v for k, v in autotag_base.CATEGORY_NAMES.items()} == vocab.CATEGORY_NAMES, (
        "CATEGORY_NAMES drifted between core/vocab.py and autotag/base.py"
    )
    assert autotag_pp.DANBOORU_ORDER == vocab.DANBOORU_ORDER, (
        "DANBOORU_ORDER drifted between core/vocab.py and autotag/postprocess.py"
    )


# --------------------------------------------------------------------------
# A17  Paren escaping matches the repository convention
# --------------------------------------------------------------------------


def test_A17_autotag_output_uses_the_repo_paren_convention() -> None:
    r"""The character tags the tagger outputs must match how the dataset and the inference prompts
    spell them.

    Evidence one: 1049 files in the dataset write `hu tao \(genshin impact\)`;
    evidence two: the repository's own inference prompt `configs/sample_prompts.txt:4` writes
    `mika \(blue archive\)`.

    The WD14 vocabulary gives `keqing_(genshin_impact)`, which after underscore removal carries
    **no escaping** -- without this extra step the captions the tagger writes match neither the
    existing captions nor the inference prompts.
    """
    from kohya_dataset_tagger.autotag.base import TagScore
    from kohya_dataset_tagger.autotag.postprocess import (
        PostprocessOptions, format_tags, postprocess_tags,
    )

    scores = [TagScore(tag="keqing_(genshin_impact)", score=0.99, category="Character")]

    escaped = postprocess_tags(
        scores, PostprocessOptions(remove_underscore=True, escape_parens=True))
    assert format_tags(escaped) == "keqing " + BS + "(genshin impact" + BS + ")", (
        "parentheses were not escaped per the repository convention: %r" % format_tags(escaped)
    )

    plain = postprocess_tags(
        scores, PostprocessOptions(remove_underscore=True, escape_parens=False))
    assert format_tags(plain) == "keqing (genshin impact)", (
        "with escape_parens=False the text should be left as-is: %r" % format_tags(plain)
    )


# --------------------------------------------------------------------------
# A12 / A13  HTTP end to end and out-of-bounds
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def api_sandbox(tmp_path_factory):
    """A writable sandbox inside the whitelist.

    The dataset root itself is **read-only**, so any case that writes to disk must land elsewhere --
    and that "elsewhere" must in turn be inside the whitelist, otherwise it is correctly 403'd.
    Stepped in it on 2026-09-17: the PUT case wrote to tmp_path while the whitelist held only the
    dataset root, so it got 403 -- **the 403 was right, the test was wrong**.
    """
    return tmp_path_factory.mktemp("sandbox")


@pytest.fixture()
def api_client(tmp_path_factory, api_sandbox):
    """Start the real app (not a minimal assembly of the routers). The thumbnail cache lands in tmp
    and never enters the dataset.

    **Function scope, not module**: the whitelist is **global** state inside `core.paths`, and other
    cases in this file (A9b / A19b and so on) call `paths.configure(...)` and overwrite it. With
    module scope the fixture publishes only once, and after being overwritten it never comes back --
    really stepped in it on 2026-09-17: in a full run A12's PUT landed outside the sandbox and got
    the correct 403, while running it alone passed. Publishing the whitelist fresh for every test
    removes the dependence on execution order.
    """
    from fastapi.testclient import TestClient

    from kohya_dataset_tagger import app as app_module
    from kohya_dataset_tagger.config import load

    cache_dir = tmp_path_factory.mktemp("thumbs")
    load(roots=[DATA, api_sandbox], thumb_cache_dir=cache_dir, model_search_roots=list(MODEL_ROOTS))

    # app.py does not freeze "factory or singleton"; accept both.
    factory = getattr(app_module, "create_app", None)
    app_obj = factory() if callable(factory) else getattr(app_module, "app")
    with TestClient(app_obj) as client:
        yield client


def _sample_image():
    from kohya_dataset_tagger.core import paths

    for d in paths.list_subdirs(DATA):
        imgs = paths.iter_images(d)
        if imgs:
            return imgs[0]
    raise AssertionError("no image found in the dataset")


@pytest.mark.parametrize("route,params", [
    ("/api/health", {}),
    ("/api/roots", {}),
    ("/api/fs/list", {"path": str(DATA)}),
    ("/api/tags/frequency", {"path": str(DATA)}),
    ("/api/tags/autocomplete", {"q": "1girl", "limit": 5}),
    ("/api/autotag/models", {}),
    ("/api/cache/status", {"path": str(DATA)}),
])
def test_A12_read_endpoints_answer_2xx(api_client, route: str, params: dict) -> None:
    resp = api_client.get(route, params=params)
    assert resp.status_code == 200, "%s -> %s %s" % (route, resp.status_code, resp.text[:300])
    if route != "/api/health":
        assert isinstance(resp.json(), dict)


def test_A12_images_meta_and_thumb(api_client) -> None:
    img = _sample_image()
    meta = api_client.get("/api/images/meta", params={"path": str(img)})
    assert meta.status_code == 200, meta.text[:300]
    body = meta.json()
    for key in ("width", "height", "bytes", "mtime", "mode", "has_alpha"):
        assert key in body, "images/meta is missing field %s: %r" % (key, body)

    thumb = api_client.get("/api/images/thumb", params={"path": str(img), "size": "grid"})
    assert thumb.status_code == 200, thumb.text[:300]
    assert thumb.content[:4] == b"RIFF", "the thumbnail is not WebP (RIFF container)"


def test_A12_caption_roundtrip_over_http(api_client, api_sandbox) -> None:
    """The end-to-end version of invariant number one: after the PUT the cache must be gone, and the response must faithfully fill in WriteResult."""
    import shutil

    from kohya_dataset_tagger.core import cache_invalidation as ci

    src = _sample_image()
    work = api_sandbox / "subset"
    work.mkdir()
    img = work / src.name
    shutil.copy2(src, img)
    if src.with_suffix(".txt").exists():
        shutil.copy2(src.with_suffix(".txt"), work / (src.stem + ".txt"))

    cache_dir = work / ci.TE_CACHE_DIRNAME
    cache_dir.mkdir(exist_ok=True)
    stale = cache_dir / (src.stem + "_anima_te.safetensors")
    stale.write_bytes(b"stale")

    resp = api_client.put("/api/captions", json={"image": str(img), "tags": ["1girl", "solo"]})
    assert resp.status_code == 200, resp.text[:400]
    body = resp.json()
    for key in ("caption_path", "changed", "cache_removed", "cache_remove_failed", "multiline_warning"):
        assert key in body, "PUT /api/captions is missing WriteResult field %s: %r" % (key, body)
    assert not stale.exists(), "the HTTP path did not invalidate the TE cache -- invariant number one is bypassed at the integration layer"
    assert body["cache_remove_failed"] == []


def test_A13_out_of_root_requests_are_403(api_client) -> None:
    cases = [
        ("/api/fs/list", {"path": r"C:\Windows"}),
        ("/api/images/thumb", {"path": "../../etc/passwd"}),
        ("/api/images/meta", {"path": r"C:\Windows\win.ini"}),
        ("/api/captions", {"path": r"C:\Windows\win.ini"}),
        ("/api/cache/status", {"path": r"C:\Windows"}),
    ]
    for route, params in cases:
        resp = api_client.get(route, params=params)
        assert resp.status_code == 403, "%s %r -> %s (expected 403)%s" % (
            route, params, resp.status_code, resp.text[:200])


# --------------------------------------------------------------------------
# A27-A30  §4.11 Runtime-editable path configuration
# --------------------------------------------------------------------------
# Why this group has to exist: the user reported from real use "the web UI root directory cannot be
# changed, it is locked to the test directory I provided" and "the model scan directory page has no
# configuration entry". What the two originally had in common was **reading only once, at startup**.
# So every criterion comes with a "before the change" precondition assertion -- asserting only 200
# would let an implementation that "takes effect after a restart" pass, and that is exactly what the
# user complained about.


@pytest.fixture()
def path_config(tmp_path: Path):
    """The A27-A30 workspace: both persistent files live in tmp_path.

    **Never use `api_client`**: it does not pass `roots_file` / `model_paths_file`, and those two
    default to the **repository root** -- one POST on that fixture and the dataset roots the user
    remembered are changed.
    """
    from fastapi.testclient import TestClient

    from kohya_dataset_tagger import config
    from kohya_dataset_tagger.app import create_app

    first = tmp_path / "dataset-one"
    second = tmp_path / "dataset-two"
    for directory in (first, second):
        directory.mkdir()
        (directory / "1.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    files = {
        "roots_file": tmp_path / "roots.txt",
        "model_paths_file": tmp_path / "model_paths.txt",
    }
    kwargs = {
        "roots": [first],
        "thumb_cache_dir": tmp_path / "thumbs",
        "model_search_roots": [],  # empty list = explicitly empty; the test does not depend on which models are installed here
        **files,
    }
    config.load(**kwargs)
    with TestClient(create_app()) as client:
        yield {"client": client, "first": first, "second": second,
               "tmp": tmp_path, **files,
               # Both persistent files are read **at load time** (§4.11), so "write the file, then
               # ask the endpoint" requires a fresh load first. Endpoints read config.current() on
               # every request, so the app need not be rebuilt.
               "reload": lambda: config.load(**kwargs)}


def test_A27_root_can_be_added_and_removed_without_restart(path_config) -> None:
    """A27: after adding a root it is browsable **in the same process** immediately, and after removal it is 403 again."""
    client = path_config["client"]
    target = path_config["second"]

    # Precondition: it must be 403 before the change. Without this step an implementation that
    # "takes effect after a restart" would fool the criterion, and that is precisely the symptom the
    # user reported.
    before = client.get("/api/fs/list", params={"path": str(target)})
    assert before.status_code == 403, "it should already be 403 before the change, actual %s" % before.status_code
    assert before.json()["error"]["code"] == "outside_roots"

    created = client.post("/api/roots", json={"path": str(target)})
    assert created.status_code == 200, created.text
    assert created.json()["changed"] is True
    assert created.json()["persisted"] is True, "it was not written to roots.txt: %r" % created.json()

    listed = client.get("/api/fs/list", params={"path": str(target)})
    assert listed.status_code == 200, "added, yet it still cannot be opened: %s" % listed.text
    assert listed.json()["counts"]["images"] == 1
    assert path_config["roots_file"].read_text(encoding="utf-8").splitlines() == [
        str(path_config["first"].resolve()), str(target.resolve()),
    ]

    removed = client.delete("/api/roots", params={"path": str(target)})
    assert removed.status_code == 200, removed.text
    after = client.get("/api/fs/list", params={"path": str(target)})
    assert after.status_code == 403, "removed, yet it still opens: %s" % after.status_code
    assert path_config["roots_file"].read_text(encoding="utf-8").splitlines() == [
        str(path_config["first"].resolve()),
    ]


def test_A28_root_edits_refuse_the_two_dead_ends(path_config) -> None:
    """A28: a non-existent path may not be added; the last root may not be deleted. Both turn the UI into a dead end."""
    client = path_config["client"]
    missing = path_config["tmp"] / "not-created-yet"

    bad = client.post("/api/roots", json={"path": str(missing)})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "not_a_directory", bad.text
    assert len(client.get("/api/roots").json()["roots"]) == 1, "a rejected request changed the whitelist"

    last = client.delete("/api/roots", params={"path": str(path_config["first"])})
    assert last.status_code == 400 and last.json()["error"]["code"] == "last_root", (
        "after emptying the whitelist every /api/fs/* is 403, and that is the hardest symptom to track down; it must be refused. %s" % last.text
    )
    assert client.get("/api/fs/list").status_code == 200, "the whitelist was broken by the deletion"
    assert not path_config["roots_file"].exists() or path_config["roots_file"].read_text(
        encoding="utf-8"
    ).splitlines() == [str(path_config["first"].resolve())], "a rejected delete changed the file"


def test_A29_the_two_persistence_files_are_written_differently(path_config) -> None:
    """A29: `roots.txt` is rewritten whole; `model_paths.txt` only has path lines added or removed. **Comments are user assets.**"""
    from kohya_dataset_tagger import config

    client = path_config["client"]
    models_file = path_config["model_paths_file"]
    keep = path_config["tmp"] / "keep-me"
    added = path_config["tmp"] / "added"
    keep.mkdir()
    added.mkdir()
    # Write bytes: write_text turns \n into os.linesep, so CRLF becomes CRCRLF and this is no
    # longer what is being tested
    original = "# top note: this line must survive\r\n#\r\n%s;%s\r\n" % (keep, added)
    models_file.write_bytes(original.encode("utf-8"))
    path_config["reload"]()

    assert [item["path"] for item in client.get("/api/autotag/model-roots").json()["roots"]] == [
        str(keep), str(added),
    ], "a semicolon-separated line was not read as two search roots"

    response = client.delete("/api/autotag/model-roots", params={"path": str(added)})
    assert response.status_code == 200 and response.json()["removed_scope"] == "file", response.text
    assert models_file.read_bytes().decode("utf-8") == "# top note: this line must survive\r\n#\r\n%s\r\n" % keep, (
        "only the matching segment should be removed: the comment, the blank line, the other "
        "segment on the line and the CRLF must all survive verbatim"
    )

    # Falsification: prove that "rewrite the whole file" really does destroy this file --
    # otherwise the assertion above cannot tell the two write styles apart
    config.write_roots_file(models_file, [keep])
    assert "#" not in models_file.read_bytes().decode("utf-8"), (
        "a whole-file rewrite left the comment behind? Then the two paths are not different and the criterion above is spinning in place"
    )


def test_A30_model_search_root_takes_effect_without_restart(path_config) -> None:
    """A30: after adding a model directory, `/api/autotag/models` recognises it **in the same process**."""
    client = path_config["client"]
    model_id = "wd-vit-tagger-v3"
    root = path_config["tmp"] / "extra-models"
    (root / model_id).mkdir(parents=True)
    (root / model_id / "model.onnx").write_bytes(b"fake onnx")
    (root / model_id / "selected_tags.csv").write_text("tag_id,name,category\n", encoding="utf-8")

    def installed() -> bool:
        models = {item["id"]: item for item in client.get("/api/autotag/models").json()["models"]}
        return bool(models[model_id]["installed"])

    assert installed() is False, "precondition does not hold: this machine already has this model in another search root"
    created = client.post("/api/autotag/model-roots", json={"path": str(root)})
    assert created.status_code == 200, created.text
    assert installed() is True, "the added model directory is not used (it would take effect only after a restart)"

    entry = client.get("/api/autotag/model-roots").json()["roots"][0]
    assert entry["models"] == [model_id], "this line does not report 'what it recognised on its own': %r" % entry
    assert entry["source"] == "file" and entry["file_backed"] is True
    assert path_config["model_paths_file"].read_text(encoding="utf-8").splitlines() == [str(root)], (
        "the newly added directory was not written to model_paths.txt; it would be lost on restart"
    )

    deleted = client.delete("/api/autotag/model-roots", params={"path": str(root)})
    assert deleted.status_code == 200 and deleted.json()["persisted"] is True, deleted.text
    assert installed() is False, "removed, yet still recognised"


# --------------------------------------------------------------------------
# A33  §4.12 Graphical directory picker + loopback gate
# --------------------------------------------------------------------------
# The user's own words: "it shows a directory is currently open, but clicking it does not let me
# switch the selection, which is counter-intuitive and infuriating".
# This criterion has to prove two things:
#   1. The picker can list directories **outside** the whitelist -- otherwise "change the dataset
#      graphically" is simply impossible;
#   2. On a non-loopback address it is refused **together with** the four mutating endpoints --
#      disabling only the picker while leaving roots open is self-deception.


def _picker_client(tmp_path: Path, **kwargs):
    """Build a service where only `root` is in the whitelist; `outside` is a directory tree outside it."""
    from fastapi.testclient import TestClient

    from kohya_dataset_tagger import config
    from kohya_dataset_tagger.app import create_app

    root = tmp_path / "dataset"
    outside = tmp_path / "outside"
    for directory in (root, outside):
        directory.mkdir()
    (outside / "inner").mkdir()
    (outside / "note.txt").write_text("x", encoding="utf-8")
    config.load(
        roots=[root],
        thumb_cache_dir=tmp_path / "thumbs",
        model_search_roots=[],
        roots_file=tmp_path / "roots.txt",
        model_paths_file=tmp_path / "model_paths.txt",
        **kwargs,
    )
    return TestClient(create_app()), root, outside


def test_A33_picker_lists_directories_outside_the_whitelist(tmp_path) -> None:
    """A33: the picker must be able to list directories outside the whitelist -- that is its entire reason to exist."""
    client, root, outside = _picker_client(tmp_path)
    with client:
        blocked = client.get("/api/fs/list", params={"path": str(outside)})
        assert blocked.status_code == 403, "the whitelist must not be opened up as a side effect of the picker: %s" % blocked.text

        picked = client.get("/api/fs/pick", params={"path": str(outside)})
        assert picked.status_code == 200, picked.text
        body = picked.json()
        assert [item["name"] for item in body["dirs"]] == ["inner"]
        assert "note.txt" not in [item["name"] for item in body["dirs"]], "the picker listed files: it may list directories only"
        assert set(body["dirs"][0]) == {"name", "path"}, "the picker returned extra fields (image counts and the like)"
        assert body["roots"], "no jumpable filesystem roots were given (cross-drive navigation would be a dead end)"
        assert Path(body["parent"]) == tmp_path.resolve()


def test_A33b_non_loopback_disables_picker_and_every_path_mutation(tmp_path) -> None:
    """A33b: on a non-loopback address all six are 403 together -- disabling only the picker and not roots (or not directory creation) is no disabling at all."""
    client, root, outside = _picker_client(tmp_path, host="0.0.0.0")
    with client:
        cases = (
            ("get", "/api/fs/pick", {"path": str(root)}, None),
            ("post", "/api/fs/pick/mkdir", None, {"parent": str(root), "name": "denied"}),
            ("post", "/api/roots", None, {"path": str(outside)}),
            ("delete", "/api/roots", {"path": str(root)}, None),
            ("post", "/api/autotag/model-roots", None, {"path": str(outside)}),
            ("delete", "/api/autotag/model-roots", {"path": str(outside)}, None),
        )
        for method, url, params, body in cases:
            kwargs = {}
            if params is not None:
                kwargs["params"] = params
            if body is not None:
                kwargs["json"] = body
            response = getattr(client, method)(url, **kwargs)
            assert response.status_code == 403, "%s %s -> %s" % (method, url, response.status_code)
            assert response.json()["error"]["code"] == "remote_path_config_disabled"
        assert client.get("/api/health").json()["path_config"]["allowed"] is False


def test_A33c_explicit_flag_is_the_only_way_to_reopen_it(tmp_path) -> None:
    """A33c: with the fallback switch on it comes back; and it **must be off by default** (A33b already proved that half)."""
    client, root, outside = _picker_client(tmp_path, host="0.0.0.0", allow_remote_path_config=True)
    with client:
        assert client.get("/api/fs/pick", params={"path": str(outside)}).status_code == 200
        assert client.post("/api/roots", json={"path": str(outside)}).status_code == 200
        assert client.get("/api/health").json()["path_config"]["allowed"] is True


def test_A33d_picker_can_create_a_new_directory(tmp_path) -> None:
    """A33d: the user's own words, "there is no way to type a path by hand and no way to create a
    folder". Creating a directory must really hit disk, must create only **one level**, be
    idempotent for an existing directory, never overwrite a file of the same name, and reject names
    carrying a path component."""
    client, root, outside = _picker_client(tmp_path)
    with client:
        created = client.post("/api/fs/pick/mkdir", json={"parent": str(outside), "name": "fresh"})
        assert created.status_code == 200, created.text
        assert created.json()["created"] is True
        assert (outside / "fresh").is_dir() and list((outside / "fresh").iterdir()) == []

        again = client.post("/api/fs/pick/mkdir", json={"parent": str(outside), "name": "fresh"})
        assert again.status_code == 200 and again.json()["created"] is False

        blocked = client.post(
            "/api/fs/pick/mkdir", json={"parent": str(outside), "name": "note.txt"}
        )
        assert blocked.status_code == 409, blocked.text
        assert blocked.json()["error"]["code"] == "already_exists"
        assert (outside / "note.txt").read_text(encoding="utf-8") == "x", "a file of the same name was treated as a directory"

        escaped = client.post(
            "/api/fs/pick/mkdir", json={"parent": str(outside), "name": "../escaped"}
        )
        assert escaped.status_code == 400 and escaped.json()["error"]["code"] == "bad_name"
        assert not (tmp_path / "escaped").exists(), "a name with .. escaped the parent directory"


# --------------------------------------------------------------------------
# A34  §4.13 Folder nine-grid preview in the centre gallery
# --------------------------------------------------------------------------
# The user's own words: "in plain folder-browsing mode the preview pane should show the folder,
# and the folder preview should be a nine-grid of the image files inside it". The data-side
# executable criterion: each subdirectory yields at most 9 sample images, scanned one level deep and
# in natural order (the frontend lays them out 3x3), and the meaning of image_count is not changed
# by this.


def _list_client(tmp_path: Path, root: Path):
    from fastapi.testclient import TestClient

    from kohya_dataset_tagger import config
    from kohya_dataset_tagger.app import create_app

    config.load(
        roots=[root],
        thumb_cache_dir=tmp_path / "thumbs",
        model_search_roots=[],
        roots_file=tmp_path / "roots.txt",
        model_paths_file=tmp_path / "model_paths.txt",
    )
    return TestClient(create_app())


def test_A34_dir_entries_carry_a_nine_image_preview(tmp_path) -> None:
    """A34: dirs[].preview_images holds at most 9 images, scans one level, is in natural order, and an empty directory gives an empty list."""
    root = tmp_path / "dataset"
    post = root / "1_post"
    post.mkdir(parents=True)
    # Only extensions are counted, contents are never decoded; a few bytes are enough
    # (this endpoint never opens the image).
    for index in range(1, 13):
        (post / ("%d.jpeg" % index)).write_bytes(b"x")
    nested = post / "nested"
    nested.mkdir()
    (nested / "99.jpeg").write_bytes(b"x")
    (root / "2_empty").mkdir()

    with _list_client(tmp_path, root) as client:
        body = client.get("/api/fs/list", params={"path": str(root)}).json()

    dirs = {entry["name"]: entry for entry in body["dirs"]}
    first = dirs["1_post"]
    assert set(first) == {"name", "path", "image_count", "preview_images"}
    assert first["image_count"] == 12, "image_count must still be the old one-level meaning"
    assert [Path(item).name for item in first["preview_images"]] == [
        "%d.jpeg" % index for index in range(1, 10)
    ], "the nine-grid must be the first 9 in natural order"
    assert all(Path(item).parent == post for item in first["preview_images"]), (
        "preview images may only come from this subdirectory itself -- scanning the subtree would pass off images from elsewhere as its own"
    )
    assert dirs["2_empty"]["preview_images"] == [], "an empty directory must give an empty list, not a missing field"
    assert all(Path(item).is_file() for item in first["preview_images"])


# --------------------------------------------------------------------------
# A31  Colored logs: do not hand the decision back to isatty()
# --------------------------------------------------------------------------


def _free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _server_log_bytes(extra_args: list[str], timeout: float = 30.0) -> bytes:
    """Start a real service once, collect the **raw bytes** it emits on stdout+stderr, then stop it.

    Only raw bytes can answer "does the log really carry escape sequences" -- `isatty()` is a lie on
    this path, because the subprocess's stdout is a file.
    """
    import tempfile
    import time
    import urllib.error
    import urllib.request

    port = _free_port()
    with tempfile.TemporaryDirectory(prefix="kohya-log-") as tmp:
        log = Path(tmp) / "server.log"
        with open(log, "wb") as handle:
            proc = subprocess.Popen(
                [str(VENV_PY), "-m", "kohya_dataset_tagger", "--port", str(port),
                 "--roots", str(REPO), *extra_args],
                cwd=str(REPO), stdout=handle, stderr=subprocess.STDOUT,
            )
            try:
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    try:
                        urllib.request.urlopen("http://127.0.0.1:%d/api/health" % port, timeout=2)
                        break
                    except (urllib.error.URLError, OSError):
                        time.sleep(0.25)
                else:
                    raise AssertionError("the service did not come up:\n" + log.read_bytes().decode("utf-8", "replace")[-2000:])
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:  # pragma: no cover
                    proc.kill()
                    proc.wait(timeout=10)
        return log.read_bytes()


def test_A31_color_decision_is_never_left_to_isatty() -> None:
    """A31: `--color` has three states, and the `use_colors` handed to uvicorn must be a **boolean**.

    Evidence from a real machine (2026-09-17, measured with `Start-Process` opening a real console):
      `DefaultFormatter(use_colors=None).use_colors = True` (uvicorn only asks isatty)
      `GetConsoleMode(stdout) = 0x3` (the VT bit is off) -> a literal `←[32m` appears on screen
      `SetConsoleMode(h, mode | 0x4)` succeeds -> so the fix is to turn VT on ourselves

    `use_colors=None` hands the decision back to that default which has already proven it guesses
    wrong, so this criterion watches "**always give a boolean**", not "is there color".
    """
    import io

    from kohya_dataset_tagger import console
    from uvicorn.config import LOGGING_CONFIG

    assert console.resolve_use_colors("always", io.StringIO()) is True
    assert console.resolve_use_colors("never") is False
    assert console.resolve_use_colors("auto", io.StringIO()) is False, "no color when redirected"
    # The hardest branch to construct on a real machine: isatty is true yet VT cannot be enabled
    class _Tty(io.StringIO):
        def isatty(self) -> bool:
            return True

    assert console.resolve_use_colors("auto", _Tty(), console=False) is False
    assert console.resolve_use_colors("auto", _Tty(), console=True) is True

    merged = console.colored_log_config(LOGGING_CONFIG, False)
    for name in ("default", "access"):
        assert merged["formatters"][name]["use_colors"] is False, (
            "the %s formatter's use_colors is not a boolean -- the decision went back to isatty()" % name
        )
    assert LOGGING_CONFIG["formatters"]["default"]["use_colors"] is not False, (
        "uvicorn's global LOGGING_CONFIG was modified in place"
    )


def test_A31b_redirected_logs_carry_no_escape_sequences() -> None:
    """The end-to-end half of A31: really start a service and look at the **bytes** it writes to the
    file.

    With the default (auto) stdout is a file -> no color; with `--color always` there must be color.
    Both sides are needed, otherwise "an implementation that never colors" would pass.
    """
    plain = _server_log_bytes([])
    assert b"\x1b[" not in plain, (
        "escape sequences appeared in the log while output was redirected (in CI/log files that is just garbage): %r" % plain[:400]
    )

    colored = _server_log_bytes(["--color", "always"])
    assert b"\x1b[" in colored, "--color always produced no color: %r" % colored[:400]


# --------------------------------------------------------------------------
# A32  Cross-platform script set
# --------------------------------------------------------------------------


def _bash() -> str | None:
    return shutil.which("bash")


def test_A32_shell_and_batch_scripts_are_symmetric_and_clean() -> None:
    """A32: the two launcher sets at the root are symmetric; every `.sh` is LF; every `.bat` is CRLF
    and **pure ASCII**.

    The pure ASCII for `.bat` is not fastidiousness: the file is UTF-8 without a BOM while cmd.exe
    decodes by console code page (936 on this machine), so `echo <Chinese>` is guaranteed mojibake --
    Python itself is unaffected (it goes through WriteConsoleW).
    """
    for name in ("start.sh", "setup_env.sh", "setup_env_cn.sh"):
        wrapper = REPO / name
        assert wrapper.is_file(), "the root is missing %s (Windows has %s.bat, Linux must be symmetric)" % (name, name[:-3])
        text = wrapper.read_text(encoding="utf-8")
        assert "scripts/" in text, "%s does not delegate to the implementation under scripts/" % name
        assert text.startswith("#!"), "%s has no shebang" % name

    for script in sorted(REPO.glob("scripts/*.sh")) + sorted(REPO.glob("*.sh")):
        raw = script.read_bytes()
        assert b"\r\n" not in raw, "%s contains CRLF; on Linux `#!/usr/bin/env bash\r` fails to find the interpreter" % script.name

    for batch in sorted(REPO.glob("*.bat")) + sorted(REPO.glob("scripts/*.bat")):
        raw = batch.read_bytes()
        assert b"\r\n" in raw, "%s must be CRLF (cmd.exe misparses parenthesized blocks with LF)" % batch.name
        non_ascii = sorted({byte for byte in raw if byte > 0x7F})
        assert not non_ascii, (
            "%s has non-ASCII bytes %s: cmd.exe decodes UTF-8 by code page 936, so printing them is mojibake"
            % (batch.name, non_ascii)
        )


def test_A32b_linux_launcher_dry_run_builds_the_right_command_line() -> None:
    """A32: `--dry-run` was added for acceptance -- this machine has **no WSL distribution**, so it
    can only run on Git Bash, which is the same bash semantics (5.2). Execution on real Linux has
    not been verified; that must be stated in the conclusion."""
    bash = _bash()
    if bash is None:
        pytest.skip("this machine has no bash")

    for script in sorted(REPO.glob("scripts/*.sh")) + sorted(REPO.glob("*.sh")):
        done = subprocess.run([bash, "-n", str(script)], capture_output=True, encoding="utf-8")
        assert done.returncode == 0, "%s fails the syntax check: %s" % (script.name, done.stderr)

    roots = REPO / "roots.txt"
    had_roots = roots.exists()
    saved = roots.read_bytes() if had_roots else b""
    try:
        roots.write_text(str(DATA) + "\n", encoding="utf-8")
        done = subprocess.run(
            [bash, "scripts/start.sh", "--dry-run"],
            cwd=str(REPO), capture_output=True, encoding="utf-8",
            env={**os.environ, "MSYS_NO_PATHCONV": "1", "PORT": "3010"},
        )
        assert done.returncode == 0, done.stdout + done.stderr
        lines = done.stdout.splitlines()
        argv = [line[len("DRY_RUN_ARG="):] for line in lines if line.startswith("DRY_RUN_ARG=")]
        ports = [line[len("DRY_RUN_PORT="):] for line in lines if line.startswith("DRY_RUN_PORT=")]
        assert "-m" in argv and "kohya_dataset_tagger" in argv, done.stdout
        assert ports and ports[0].isdigit(), "the final port was not reported: %s" % done.stdout
        # The port is **chosen**: PORT=3010 is the starting point and it moves on when taken.
        # So assert "the same value", not 3010.
        assert "--port" in argv and ports[0] in argv, "the chosen port was not passed to the service: %s" % done.stdout
        assert "--roots" in argv and str(DATA) in argv, "roots.txt was not read in: %s" % done.stdout
        assert "--extra-models" not in argv, (
            "the launcher still forwards model_paths.txt: §3.7 fixed that the server reads it "
            "itself, and forwarding would let a line deleted in the UI be pushed back by the "
            "command line on the next start"
        )
        gpu = subprocess.run(
            [bash, "scripts/setup_env.sh", "--dry-run", "--gpu", "cuda"],
            cwd=str(REPO), capture_output=True, encoding="utf-8",
            env={**os.environ, "MSYS_NO_PATHCONV": "1"},
        )
        assert gpu.returncode == 0, gpu.stdout + gpu.stderr
        assert "DRY_RUN_GPU=cuda" in gpu.stdout, gpu.stdout
        assert "onnxruntime-gpu" in gpu.stdout, "the cuda branch does not install onnxruntime-gpu: %s" % gpu.stdout
        assert "pypi.org" in gpu.stdout, (
            "the regular profile does not pin index-url: the global pip source can be so slow it "
            "looks frozen"
        )
        # China profile: the first index must be a China mirror and the last must be the
        # official source as a fallback (otherwise a dead mirror means nothing installs).
        cn = subprocess.run(
            [bash, "scripts/setup_env.sh", "--dry-run", "--gpu", "cpu", "--index", "cn"],
            cwd=str(REPO), capture_output=True, encoding="utf-8",
            env={**os.environ, "MSYS_NO_PATHCONV": "1"},
        )
        assert cn.returncode == 0, cn.stdout + cn.stderr
        cn_indexes = [line[len("DRY_RUN_INDEX="):] for line in cn.stdout.splitlines()
                      if line.startswith("DRY_RUN_INDEX=")]
        assert cn_indexes and "mirrors.ustc.edu.cn" in cn_indexes[0], (
            "the China profile does not put the China mirror first: %s" % cn.stdout)
        assert cn_indexes[-1] == "https://pypi.org/simple", (
            "the China profile has no official-source fallback: %s" % cn.stdout)
        cn_wrapper = subprocess.run(
            [bash, "setup_env_cn.sh", "--dry-run", "--gpu", "cpu"],
            cwd=str(REPO), capture_output=True, encoding="utf-8",
            env={**os.environ, "MSYS_NO_PATHCONV": "1"},
        )
        assert cn_wrapper.returncode == 0, cn_wrapper.stdout + cn_wrapper.stderr
        assert "DRY_RUN_INDEX=https://mirrors.ustc.edu.cn/pypi/simple" in cn_wrapper.stdout, (
            "setup_env_cn.sh did not use the China profile: %s" % cn_wrapper.stdout)
    finally:
        if had_roots:
            roots.write_bytes(saved)
        elif roots.exists():
            roots.unlink()


# --------------------------------------------------------------------------
# A35  Batch tag editing (extension of §4.5 (3) + dry_run)
# --------------------------------------------------------------------------


def _batch_workdir(api_sandbox: Path, name: str) -> Path:
    work = api_sandbox / name
    work.mkdir(parents=True, exist_ok=True)
    return work


def test_A35_batch_edit_preview_equals_write(api_client, api_sandbox) -> None:
    """§4.5 (3): `edit_tags` renames/deletes/adds in groups, and **the preview's after must equal the
    file after it is written**.

    A "preview" only means something when it shares its source with the write: if dry_run
    reimplements the op semantics, the diff the user sees may differ from what actually lands, and
    the entire reason this feature exists is "take a look before applying". So the criterion is not
    "the preview returned fields" but "dry_run's after" being byte-for-byte equal to "the caption
    read back after the real write".
    """
    import shutil

    from kohya_dataset_tagger.core import cache_invalidation as ci

    src = _sample_image()
    work = _batch_workdir(api_sandbox, "A35")
    img = work / src.name
    shutil.copy2(src, img)
    caption = img.with_suffix(".txt")
    api_client.put("/api/captions", json={"image": str(img), "text": "1girl, solo, blue eyes"})

    cache_dir = work / ci.TE_CACHE_DIRNAME
    cache_dir.mkdir(exist_ok=True)
    cache = cache_dir / (img.stem + "_anima_te.safetensors")
    cache.write_bytes(b"stale")
    before = caption.read_bytes()

    payload = {
        "images": [str(img)],
        "op": "edit_tags",
        "pairs": [{"find": "blue eyes", "replace": "azure eyes"}, {"find": "solo", "replace": ""}],
        "tags": ["new tag"],
        "position": "append",
    }
    preview = api_client.post("/api/captions/batch", json={**payload, "dry_run": True})
    assert preview.status_code == 200, preview.text[:400]
    body = preview.json()
    assert body.get("dry_run") is True, "the dry_run response is not tagged; the frontend might take it for a real write"
    assert body["applied"] == 1 and body["failed"] == 0, body
    entry = body["results"][0]
    assert entry["changed"] is True
    assert caption.read_bytes() == before, "dry_run changed the caption"
    assert cache.is_file(), "dry_run deleted the TE cache"

    written = api_client.post("/api/captions/batch", json=payload)
    assert written.status_code == 200, written.text[:400]
    assert "dry_run" not in written.json(), "the real-write response carries a dry_run flag"
    on_disk = caption.read_text(encoding="utf-8")
    assert on_disk == entry["after"], (
        "the after the preview reported disagrees with what really landed: %r != %r" % (entry["after"], on_disk)
    )
    assert not cache.is_file(), "the write did not invalidate the TE cache (invariant number one)"
    # Deletion does not shift things: after removing solo in the middle, blue eyes is still
    # blue eyes (it was renamed)
    assert on_disk == "1girl, azure eyes, new tag", on_disk


def test_A35b_batch_regex_and_prepend(api_client, api_sandbox) -> None:
    """`regex_replace_text`'s whole-span replacement is re-split on bare commas; `prepend` goes to the front."""
    import shutil

    src = _sample_image()
    work = _batch_workdir(api_sandbox, "A35b")
    img = work / src.name
    shutil.copy2(src, img)
    caption = img.with_suffix(".txt")
    api_client.put("/api/captions", json={"image": str(img), "text": "1boy, solo, smile"})

    resp = api_client.post("/api/captions/batch", json={
        "images": [str(img)], "op": "regex_replace_text",
        "pattern": "solo, smile", "replacement": "solo, grin, closed mouth",
    })
    assert resp.status_code == 200, resp.text[:400]
    assert caption.read_text(encoding="utf-8") == "1boy, solo, grin, closed mouth"

    resp = api_client.post("/api/captions/batch", json={
        "images": [str(img)], "op": "prepend", "tags": ["masterpiece"],
    })
    assert resp.status_code == 200, resp.text[:400]
    assert caption.read_text(encoding="utf-8") == "masterpiece, 1boy, solo, grin, closed mouth"

    # A comma is the separator: a comma inserted by the whole-span replacement becomes a new
    # tag (consistent with the trainer)
    resp = api_client.post("/api/captions/batch", json={
        "images": [str(img)], "op": "regex_replace_text",
        "pattern": "1boy", "replacement": "1girl, red eyes",
    })
    assert resp.status_code == 200, resp.text[:400]
    assert caption.read_text(encoding="utf-8") == "masterpiece, 1girl, red eyes, solo, grin, closed mouth"

# --------------------------------------------------------------------------
# A36  Image scaling/export (§4.14)
# --------------------------------------------------------------------------


def _a36_sse(text: str) -> list[tuple[str, dict]]:
    """Split the SSE body into a list of (event, data)."""
    events: list[tuple[str, dict]] = []
    for block in text.split("\n\n"):
        name = None
        data = None
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line[len("event: "):]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: "):])
        if name is not None:
            events.append((name, data))
    return events


def _a36_run(api_client, payload: dict) -> list[tuple[str, dict]]:
    resp = api_client.post("/api/scale/run", json=payload)
    assert resp.status_code == 202, resp.text[:400]
    job_id = resp.json()["job_id"]
    with api_client.stream("GET", "/api/scale/stream", params={"job_id": job_id}) as stream:
        events = _a36_sse("".join(stream.iter_text()))
    assert events and events[-1][0] == "done", [name for name, _ in events]
    assert events[-1][1]["finished"] is True, "the end signal is not finished:true (§4.3)"
    return events


def _a36_scope_images(api_client, root: Path) -> list[str]:
    """The list the "directory + subdirectories" scope reports, read from the endpoint the strip reads.

    The frontend's scope strip answers "which images?" from /api/fs/list (app.js
    scopeProviders.loadDirRecursive), so the criterion takes the list the same way instead of
    writing one by hand - the cache/hidden pruning is part of what it is verifying.
    """
    resp = api_client.get("/api/fs/list", params={"path": str(root), "recursive": True})
    assert resp.status_code == 200, resp.text[:300]
    return [entry["path"] for entry in resp.json()["images"]]


def _a36_snapshot(root: Path) -> dict:
    return {
        path: (path.stat().st_size, path.stat().st_mtime_ns)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_A36_image_scaling_export(api_client, api_sandbox) -> None:
    """A36: area normalization + automatically carrying captions along + no upscaling + target directory boundary."""
    from PIL import Image

    work = api_sandbox / "a36"
    root = work / "dataset"
    (root / "post").mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (3000, 2000), (10, 20, 30)).save(root / "post" / "big.png", "PNG")
    Image.new("RGB", (400, 300), (10, 20, 30)).save(root / "post" / "small.png", "PNG")
    caption_bytes = "1girl, solo\r\n".encode("utf-8")
    (root / "post" / "big.txt").write_bytes(caption_bytes)
    (root / "post" / "small.txt").write_text("2girls", encoding="utf-8")
    before_source = _a36_snapshot(root)

    images = _a36_scope_images(api_client, root)
    assert len(images) == 2, images

    target = work / "out"
    events = _a36_run(api_client, {
        "images": images,
        "root": str(root),
        "target_dir": str(target),
        "target_width": 1536,
        "target_height": 1536,
    })
    summary = events[-1][1]["summary"]
    assert summary["failed"] == 0, events[-1][1]["errors"]
    assert summary["scaled"] == 1 and summary["kept"] == 1, summary

    with Image.open(target / "post" / "big.png") as opened:
        big_size = opened.size
    assert big_size == (1881, 1254), big_size
    area = big_size[0] * big_size[1]
    assert abs(area - 1536 * 1536) / (1536 * 1536) < 0.001, "the area was not normalized"
    with Image.open(target / "post" / "small.png") as opened:
        assert opened.size == (400, 300), "an image smaller than the target was upscaled (no_upscale should default to true)"

    # The caption is copied byte by byte (including CRLF), not re-assembled
    assert (target / "post" / "big.txt").read_bytes() == caption_bytes
    assert (target / "post" / "small.txt").read_text(encoding="utf-8") == "2girls"

    # The atomic write leaves no temp file; not one byte or mtime of the source changes
    assert [path.name for path in target.rglob(".tmp-scale-*")] == []
    assert _a36_snapshot(root) == before_source, "the export touched the source files"

    # Only turning no_upscale off will enlarge a small image
    target2 = work / "out_enlarged"
    _a36_run(api_client, {
        "images": images,
        "root": str(root),
        "target_dir": str(target2),
        "target_width": 1536,
        "target_height": 1536,
        "no_upscale": False,
    })
    with Image.open(target2 / "post" / "small.png") as opened:
        assert opened.size == (1774, 1330), opened.size

    # Target directory boundary: 403 outside the whitelist, 400 inside the source directory
    # (and no directory is created)
    outside = api_sandbox.parent / "a36_outside"
    resp = api_client.post(
        "/api/scale/run",
        json={"images": images, "root": str(root), "target_dir": str(outside)},
    )
    assert resp.status_code == 403, resp.text[:300]
    assert resp.json()["error"]["code"] == "outside_roots"

    resp = api_client.post(
        "/api/scale/run",
        json={"images": images, "root": str(root), "target_dir": str(root / "exported")},
    )
    assert resp.status_code == 400, resp.text[:300]
    assert resp.json()["error"]["code"] == "target_inside_root"
    assert not (root / "exported").exists(), "a rejected export still created the directory"

    # The export covers the list and nothing else (2026-09-20). One image of the two is listed:
    # a run that still walks `root` writes a second image (and a second caption), which is exactly
    # the defect the list removes - the user picked one image and the whole folder came along.
    listed_only = work / "listed_only"
    listed_events = _a36_run(api_client, {
        "images": [str(root / "post" / "big.png")],
        "root": str(root),
        "target_dir": str(listed_only),
        "target_width": 1536,
        "target_height": 1536,
    })
    exported = sorted(
        path.relative_to(listed_only).as_posix()
        for path in listed_only.rglob("*")
        if path.is_file()
    )
    assert exported == ["post/big.png", "post/big.txt"], exported
    assert listed_events[-1][1]["summary"]["processed"] == 1, listed_events[-1][1]["summary"]

    # An empty list is refused rather than widened back into the whole directory.
    resp = api_client.post("/api/scale/run", json={
        "images": [], "root": str(root), "target_dir": str(work / "refused"),
    })
    assert resp.status_code == 400, resp.text[:300]
    assert resp.json()["error"]["code"] == "no_images", resp.text[:300]
    assert not (work / "refused").exists(), "a refused request created the target directory"


def test_A36b_dataset_toml_for_the_export(api_client, api_sandbox) -> None:
    """The second half of A36: the target root gets a dataset.toml, with the resolution matching the scaling target."""
    from PIL import Image

    work = api_sandbox / "a36b"
    root = work / "dataset"
    (root / "post").mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (3000, 2000), (10, 20, 30)).save(root / "post" / "1.png", "PNG")
    (root / "post" / "1.txt").write_text("1girl", encoding="utf-8")
    target = work / "out"

    events = _a36_run(api_client, {
        "images": [str(root / "post" / "1.png")],
        "root": str(root),
        "target_dir": str(target),
        "target_width": 1024,
        "target_height": 1024,
        "write_dataset_toml": True,
    })
    summary = events[-1][1]["summary"]
    assert summary["dataset_toml"] is not None, summary["dataset_toml_warnings"]
    toml_path = target / "dataset.toml"
    assert Path(summary["dataset_toml"]).resolve() == toml_path.resolve()
    text = toml_path.read_text(encoding="utf-8")
    assert "1024" in text, "the generated dataset.toml resolution does not match the scaling target"
    assert "post" in text, "image_dir does not point at the exported subdirectory"
    assert ".txt" in text, "caption_extension does not match the one carried along automatically"


def test_A41_an_existing_dataset_toml_is_replaced_only_on_purpose(api_client, api_sandbox) -> None:
    """A41 (§4.14, 2026-09-19): the export writes dataset.toml, and a hand-tuned one has to survive a run.

    Scored end to end on the real endpoint. The file already at the destination keeps its bytes and the
    run reports it as skipped rather than failed; asking for the replacement puts the new file there and
    keeps the previous one at dataset.toml.bak; and a second replacement does not touch that backup.
    """
    from PIL import Image

    work = api_sandbox / "a41"
    root = work / "dataset"
    (root / "post").mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (3000, 2000), (10, 20, 30)).save(root / "post" / "1.png", "PNG")
    (root / "post" / "1.txt").write_text("1girl", encoding="utf-8")
    target = work / "out"
    target.mkdir(parents=True, exist_ok=True)

    original = b"# hand tuned by the user\n[datasets]\nbatch_size = 8\n"
    toml_path = target / "dataset.toml"
    toml_path.write_bytes(original)
    before = toml_path.stat().st_mtime_ns

    base = {
        "images": [str(root / "post" / "1.png")],
        "root": str(root),
        "target_dir": str(target),
        "target_width": 1024,
        "target_height": 1024,
    }

    skipped = _a36_run(api_client, dict(base, write_dataset_toml=True))[-1][1]["summary"]
    assert toml_path.read_bytes() == original, "the export replaced a dataset.toml that was already there"
    assert toml_path.stat().st_mtime_ns == before, "the existing dataset.toml was rewritten byte-identically"
    assert skipped["dataset_toml"] is None, skipped
    assert skipped["dataset_toml_skipped"] is True, "the run never reported that it left the file alone"
    assert any("already exists" in line for line in skipped["dataset_toml_warnings"]), skipped

    replaced = _a36_run(
        api_client, dict(base, write_dataset_toml=True, overwrite_dataset_toml=True)
    )[-1][1]["summary"]
    assert Path(replaced["dataset_toml"]).resolve() == toml_path.resolve(), replaced
    assert toml_path.read_bytes() != original, "the replacement was asked for and did not happen"
    assert "post" in toml_path.read_text(encoding="utf-8")
    backup = target / "dataset.toml.bak"
    assert Path(replaced["dataset_toml_backup"]).resolve() == backup.resolve(), replaced
    assert backup.read_bytes() == original, "the replaced file is gone: nothing kept its bytes"

    _a36_run(api_client, dict(base, write_dataset_toml=True, overwrite_dataset_toml=True))
    assert backup.read_bytes() == original, (
        "the second replacement overwrote the only copy of the file that was there before the tool ran"
    )


# --------------------------------------------------------------------------
# A37  Backend messages are localizable: structured companion arrays + error params (§4.9 addendum 4)
# --------------------------------------------------------------------------


def _a37_dataset(root: Path) -> Path:
    """Three subdirectories: one with too few images, one missing captions, one empty -- covering both the warning and the skipped paths."""
    from PIL import Image

    root.mkdir(parents=True, exist_ok=True)
    clean = root / "1_clean"
    clean.mkdir()
    for index in range(1, 3):
        Image.new("RGB", (32, 24), (10, 20, 30)).save(clean / ("%d.png" % index), "PNG")
        (clean / ("%d.txt" % index)).write_text("1girl", encoding="utf-8")
    missing = root / "2_missing"
    missing.mkdir()
    for index in range(1, 3):
        Image.new("RGB", (32, 24), (10, 20, 30)).save(missing / ("%d.png" % index), "PNG")
    (missing / "1.txt").write_text("1girl", encoding="utf-8")
    (root / "3_empty").mkdir()
    return root


def test_A37_backend_messages_are_translatable_and_lossless(api_client, api_sandbox, tmp_path) -> None:
    """§4.9 addendum 4: the old arrays unchanged to the letter + the new arrays parallel item for
    item + errors carrying params + the frontend able to fall back.

    The criterion must be able to fail: mistyping any text, or deleting one msg.* key, turns what
    follows red.
    """
    from kohya_dataset_tagger.core import dataset_toml

    root = _a37_dataset(api_sandbox / "a37")
    body = api_client.post("/api/dataset/toml", json={"root": str(root)}).json()

    # (1) The shape and content of the two old arrays are not rewritten by the new fields
    assert body["warnings"], "the fixture produced no warning; this criterion would spin in place"
    assert body["skipped"], "the fixture skipped no directory; this criterion would spin in place"
    for entry in body["skipped"]:
        assert set(entry) == {"image_dir", "reason"}, entry

    # (2) The structured arrays match the old arrays item for item in order and length, and
    # text / reason are byte for byte identical
    assert [item["text"] for item in body["warning_items"]] == body["warnings"]
    assert [item["image_dir"] for item in body["skipped_items"]] == [
        entry["image_dir"] for entry in body["skipped"]
    ]
    assert [item["reason"] for item in body["skipped_items"]] == [
        entry["reason"] for entry in body["skipped"]
    ]
    for item in body["warning_items"]:
        assert set(item) == {"code", "params", "text"}, item
        assert item["code"] == item["text"][1:item["text"].index("]")], item
        assert isinstance(item["params"], dict)
    for item in body["skipped_items"]:
        assert set(item) == {"image_dir", "code", "params", "reason"}, item
        assert item["code"] == item["reason"][1:item["reason"].index("]")], item

    # (3) params per code match the frozen table; the skipped reasons are fixed short
    # sentences with empty params
    schema = {
        dataset_toml.WARN_MISSING_CAPTION: {"count", "extension"},
        dataset_toml.WARN_TOO_FEW_IMAGES: {"count", "minimum"},
        dataset_toml.WARN_EMPTY_DIR: {"count"},
    }
    produced = {item["code"] for item in body["warning_items"]}
    assert produced == set(schema), "coverage is incomplete: %s" % sorted(produced ^ set(schema))
    for item in body["warning_items"]:
        assert set(item["params"]) == schema[item["code"]], item
    assert all(item["params"] == {} for item in body["skipped_items"])

    # (4) error params carry variable data; the two old fields code / message are unchanged
    # to the letter
    requested = tmp_path / "a37-outside"
    error = api_client.post("/api/dataset/toml", json={"root": str(requested)}).json()["error"]
    assert set(error) == {"code", "message", "params"}
    assert error["code"] == "outside_roots"
    assert isinstance(error["message"], str) and error["message"]
    assert error["params"] == {"path": str(requested)}

    missing = api_sandbox / "a37-does-not-exist"
    error = api_client.post("/api/dataset/toml", json={"root": str(missing)}).json()["error"]
    assert error["code"] == "not_found"
    assert error["params"] == {"path": str(missing)}

    # (5) Frontend: the render path reads the structured arrays and uses hasKey to decide
    # between the translation and falling back to err.message
    app = (REPO / "src" / "kohya_dataset_tagger" / "web" / "js" / "app.js").read_text(
        encoding="utf-8"
    )
    assert "payload.warning_items" in app and "payload.skipped_items" in app
    assert "hasKey(" in app and "localizeBackendMessage(" in app
    assert "err.message" in app, "there is no fallback to the backend's own wording when there is no translation"

    # (6) All three language packs provide msg.* for every code (the key set is mechanically
    # checked by test_web_contract)
    locales = REPO / "src" / "kohya_dataset_tagger" / "web" / "js" / "locales"
    for pack in ("zh-CN.js", "en.js", "ja.js"):
        text = (locales / pack).read_text(encoding="utf-8")
        for code in schema:
            assert ('"msg.%s"' % code) in text, "%s is missing msg.%s" % (pack, code)
        assert '"msg.skip.empty_dir"' in text, "%s is missing msg.skip.empty_dir" % pack


def test_A38_backup_overwrite_is_the_default_and_never_loses_data(tmp_path) -> None:
    """A38 `backup_overwrite` (§4.5 (4)): the default write mode is "back up, then overwrite", and
    the backup is trustworthy.

    Why it deserves an acceptance criterion: `overwrite` is the only path that **irreversibly** loses
    existing captions. Moving it off the default position turns the cost of "not looking at the
    dropdown" from "tags lost forever" into "one extra .bak".
    The criterion chain: the contract states the default and the semantics -> both ends agree on the
    value set and the default -> the backup semantics on real files -> the wiring really backs up
    first.
    """
    from kohya_dataset_tagger.api import autotag as api_autotag
    from kohya_dataset_tagger.core import captions

    # (1) Contract: the default and the three backup rules are all written into p0-spec
    spec = (REPO / ".github" / "memory" / "p0-spec.md").read_text(encoding="utf-8")
    assert "**defaults to `backup_overwrite` when omitted**" in spec, (
        "the §4 route section does not state the default"
    )
    assert "**The default is `backup_overwrite`" in spec, "§4.5 (4) does not state the default"
    assert "An existing backup is **never overwritten**" in spec, (
        "§4.5 (4) does not state 'an existing backup is never overwritten'"
    )

    # (2) Backend: the default = the first item of the value set, and the run endpoint falls
    # back to the constant (omitting write_mode goes through it)
    assert api_autotag.WRITE_MODES[0] == "backup_overwrite", "the first item of the value set is not the default"
    assert api_autotag.DEFAULT_WRITE_MODE == "backup_overwrite"
    run_src = (REPO / "src" / "kohya_dataset_tagger" / "api" / "autotag.py").read_text(encoding="utf-8")
    assert "payload.write_mode or DEFAULT_WRITE_MODE" in run_src, (
        "omitting write_mode does not go through DEFAULT_WRITE_MODE (the default may exist only in the docs)"
    )

    # (3) Frontend: the value set contains it and the dropdown selects it by default
    js_api = (REPO / "src" / "kohya_dataset_tagger" / "web" / "js" / "api.js").read_text(encoding="utf-8")
    js_autotag = (REPO / "src" / "kohya_dataset_tagger" / "web" / "js" / "autotag.js").read_text(encoding="utf-8")
    assert 'BACKUP_OVERWRITE: "backup_overwrite"' in js_api
    assert "writeModeSelect.value = WRITE_MODE.BACKUP_OVERWRITE" in js_autotag, "the dropdown does not select it by default"

    # (4) Backup semantics (real files): byte for byte, and an existing backup is never
    # overwritten
    image = tmp_path / "a38.png"
    image.write_bytes(b"PNG")
    target = captions.caption_path(image)
    original = "old, another".encode("utf-8") + b"\r\n"
    target.write_bytes(original)

    backup = captions.backup_caption(image, "new")
    assert backup is not None and backup == captions.backup_path(image)
    assert backup.read_bytes() == original, "the backup is not byte-for-byte identical to the original (CRLF was changed)"

    target.write_bytes(b"second")
    again = captions.backup_caption(image, "third")
    assert again is not None and again.read_bytes() == original, "an existing backup was overwritten (the original data is lost)"

    # (5) Wiring: with the default mode it really backs up before overwriting
    class _Tag:
        def __init__(self, tag):
            self.tag = tag

    backup.unlink()
    target.write_bytes(b"old")
    api_autotag._write_tags(image, [_Tag("x")], api_autotag.DEFAULT_WRITE_MODE)
    assert target.read_bytes() == b"x", "the default mode did not write the new tags in"
    assert captions.backup_path(image).read_bytes() == b"old", "the default mode did not back up first"

    # (6) No backup is left when the content did not change (otherwise one whole-dataset run
    # floods it with .bak noise)
    captions.backup_path(image).unlink()
    api_autotag._write_tags(image, [_Tag("x")], api_autotag.DEFAULT_WRITE_MODE)
    assert not captions.backup_path(image).exists(), "a backup was produced even though the content did not change"




# A39  The batch write and the cache rebuild have a job form (§4.5 supplement, 2026-09-19)
def test_A39_a_long_write_streams_progress_and_a_cancel_stops_between_images(
    api_client, api_sandbox
) -> None:
    """§4.5 supplement: the job form streams one progress per image and a terminal done, and a
    cancel stops **between** images.

    A35 already pins "the preview equals the write" for one image. What can only go wrong in the
    long-running shape is different: an event that never arrives, a cancel that either interrupts a
    write in flight or fails to stop the rest, and counters that disagree with the files on disk.
    The criteria are therefore "one progress per image", "counted == written", and "images left
    unwritten only when the terminal event says cancelled".
    """
    import json as _json
    import shutil

    src = _sample_image()
    work = _batch_workdir(api_sandbox, "A39")
    count = 40
    images: list[Path] = []
    for index in range(count):
        image = work / ("img%02d%s" % (index, src.suffix))
        shutil.copy2(src, image)
        image.with_suffix(".txt").write_text("a,  a", encoding="utf-8")
        images.append(image)

    def read_stream(route: str, job_id: str) -> list[tuple[str, dict]]:
        events: list[tuple[str, dict]] = []
        name = ""
        with api_client.stream("GET", route, params={"job_id": job_id}) as response:
            assert response.status_code == 200, response.read()[:300]
            for line in response.iter_lines():
                if line.startswith("event: "):
                    name = line[len("event: "):].strip()
                elif line.startswith("data: "):
                    events.append((name, _json.loads(line[len("data: "):])))
        return events

    payload = {"images": [str(image) for image in images], "op": "normalize"}
    started = api_client.post("/api/captions/batch/run", json=payload)
    assert started.status_code == 202, started.text[:300]
    body = started.json()
    assert set(body) == {"job_id", "total"} and body["total"] == count, body

    events = read_stream("/api/captions/batch/stream", body["job_id"])
    progress = [data for name, data in events if name == "progress"]
    done = [data for name, data in events if name == "done"]
    assert len(progress) == count, "one progress per image, got %d" % len(progress)
    assert len(done) == 1, "the stream must end with exactly one terminal event"
    assert [data["done"] for data in progress] == list(range(1, count + 1)), "the counters skipped"
    assert done[0]["finished"] is True and done[0]["cancelled"] is False, done[0]
    assert done[0]["summary"] == {"op": "normalize", "processed": count, "applied": count, "failed": 0}
    assert all(image.with_suffix(".txt").read_text(encoding="utf-8") == "a" for image in images), (
        "the stream claimed a write that is not on disk"
    )

    # A dry run has no job form: the preview stays synchronous and instant.
    refused = api_client.post("/api/captions/batch/run", json={**payload, "dry_run": True})
    assert refused.status_code == 400, refused.text[:200]
    assert refused.json()["error"]["code"] == "bad_request"

    # Cancel: the invariant is what survives every interleaving, not a fixed count.
    for image in images:
        image.with_suffix(".txt").write_text("a,  a", encoding="utf-8")
    second = api_client.post("/api/captions/batch/run", json=payload)
    assert second.status_code == 202, second.text[:300]
    job_id = second.json()["job_id"]
    asked = api_client.post("/api/captions/batch/cancel", json={"job_id": job_id})
    assert asked.status_code == 202

    terminal = [data for name, data in read_stream("/api/captions/batch/stream", job_id) if name == "done"][0]
    processed = terminal["summary"]["processed"]
    written = sum(1 for image in images if image.with_suffix(".txt").read_text(encoding="utf-8") == "a")
    assert processed == terminal["done"], "the terminal counters disagree with the event count"
    assert processed == written, (
        "counted %d but %d captions are on disk: a cancel lost or invented a write" % (processed, written)
    )
    if processed < count:
        assert terminal["cancelled"] is True, (
            "images were left unwritten without a cancel being recorded: %r" % terminal["summary"]
        )

    # A cancel for a job that is over must say so rather than pretend.
    again = api_client.post("/api/captions/batch/cancel", json={"job_id": job_id})
    assert again.status_code == 202 and again.json()["cancelled"] is False

    # The cache rebuild has the same job form; over four images it is enough to see it work.
    from kohya_dataset_tagger.core import cache_invalidation as ci

    caches = []
    for image in images[:4]:
        cache_dir = image.parent / ci.TE_CACHE_DIRNAME
        cache_dir.mkdir(exist_ok=True)
        cache = cache_dir / (image.stem + "_anima_te.safetensors")
        cache.write_bytes(b"stale")
        caches.append(cache)
    rebuild = api_client.post("/api/cache/invalidate/run", json={"images": [str(image) for image in images[:4]]})
    assert rebuild.status_code == 202, rebuild.text[:300]
    cache_events = read_stream("/api/cache/invalidate/stream", rebuild.json()["job_id"])
    cache_done = [data for name, data in cache_events if name == "done"][0]
    assert cache_done["summary"]["removed"] == 4, cache_done["summary"]
    assert all(not cache.is_file() for cache in caches), "the caches are still on disk"


def test_A40_a_batch_write_keeps_the_original_caption(api_client, api_sandbox) -> None:
    """§4.5 ③ `backup` (added 2026-09-19, default true): the batch rewrite is the same irreversible
    overwrite that the tagger's default write mode already protects, so it must not be the one
    entrance that throws existing captions away.

    Independently scored chain: the contract states the default and the failure rule -> a request that
    never mentions the field keeps the original bytes -> the preview leaves nothing behind (it writes
    nothing) -> a second run never replaces the first backup -> `backup:false` is the documented way out.
    """
    import shutil

    from kohya_dataset_tagger.core import captions

    # (1) Contract
    spec = (REPO / ".github" / "memory" / "p0-spec.md").read_text(encoding="utf-8")
    assert "`backup` (added 2026-09-19, default `true`)" in spec, "§4.5 ③ does not state the default"
    assert "aborts that image's" in spec, "§4.5 ③ does not state what a failed backup does"

    src = _sample_image()
    work = _batch_workdir(api_sandbox, "A40")
    first = work / src.name
    second = work / ("other" + src.suffix)
    shutil.copy2(src, first)
    shutil.copy2(src, second)
    for image in (first, second):
        seeded = api_client.put("/api/captions", json={"image": str(image), "text": "1girl, solo"})
        assert seeded.status_code == 200, seeded.text[:300]
    original = captions.caption_path(first).read_bytes()

    # (2) The preview must not leave a backup behind
    preview = api_client.post(
        "/api/captions/batch",
        json={"images": [str(first)], "op": "append", "tags": ["new"], "dry_run": True},
    )
    assert preview.status_code == 200, preview.text[:300]
    assert not captions.backup_path(first).exists(), "the preview wrote a backup"

    # (3) A real write that never mentions the field keeps the original bytes
    written = api_client.post(
        "/api/captions/batch", json={"images": [str(first)], "op": "append", "tags": ["new"]}
    )
    assert written.status_code == 200, written.text[:300]
    assert captions.caption_path(first).read_text(encoding="utf-8") == "1girl, solo, new"
    assert captions.backup_path(first).read_bytes() == original, "the batch write did not keep the original"

    # (4) A second run never replaces the first backup: it is the only copy of the original state
    again = api_client.post(
        "/api/captions/batch", json={"images": [str(first)], "op": "append", "tags": ["third"]}
    )
    assert again.status_code == 200, again.text[:300]
    assert captions.caption_path(first).read_text(encoding="utf-8") == "1girl, solo, new, third"
    assert captions.backup_path(first).read_bytes() == original, "the second run replaced the original backup"

    # (5) backup:false is the documented way out
    off = api_client.post(
        "/api/captions/batch",
        json={"images": [str(second)], "op": "append", "tags": ["new"], "backup": False},
    )
    assert off.status_code == 200, off.text[:300]
    assert captions.caption_path(second).read_text(encoding="utf-8") == "1girl, solo, new"
    assert not captions.backup_path(second).exists(), "backup:false still wrote a backup"


def test_A42_a_write_can_be_taken_back(api_client, api_sandbox) -> None:
    """§4.5 (5), added 2026-09-19: the restore is what makes a batch rewrite reversible instead of merely
    confirmed.

    Independently scored chain: the contract states the rule -> a real write keeps the original in the
    .bak sidecar -> the restore puts those exact bytes back and consumes the backup -> it invalidates the
    text-encoder cache in the same call (an undo that trains on the tags the user took back is not an
    undo) -> nothing to restore is a per-entry answer rather than a request failure -> the net is re-armed
    for the next write.
    """
    import shutil

    from kohya_dataset_tagger.core import cache_invalidation as ci
    from kohya_dataset_tagger.core import captions

    # (1) Contract
    spec = (REPO / ".github" / "memory" / "p0-spec.md").read_text(encoding="utf-8")
    assert "The restore: undoing a write (2026-09-19" in spec, "§4.5 (5) is not in the contract"
    assert "/captions/restore" in spec, "the contract never names the endpoint"

    src = _sample_image()
    work = _batch_workdir(api_sandbox, "A42")
    first = work / src.name
    shutil.copy2(src, first)
    seeded = api_client.put("/api/captions", json={"image": str(first), "text": "1girl, solo"})
    assert seeded.status_code == 200, seeded.text[:300]
    original = captions.caption_path(first).read_bytes()

    # (2) A write, then the undo: the exact bytes come back and the backup is consumed
    written = api_client.post(
        "/api/captions/batch", json={"images": [str(first)], "op": "append", "tags": ["new"]}
    )
    assert written.status_code == 200, written.text[:300]
    assert captions.backup_path(first).is_file(), "the write kept no backup, so there is nothing to undo"
    cache_dir = first.parent / ci.TE_CACHE_DIRNAME
    cache_dir.mkdir(exist_ok=True)
    cache = cache_dir / (first.stem + "_anima_te.safetensors")
    cache.write_bytes(b"stale")

    undone = api_client.post("/api/captions/restore", json={"images": [str(first)]})
    assert undone.status_code == 200, undone.text[:300]
    body = undone.json()
    assert body["restored"] == 1 and body["failed"] == 0, body
    assert body["results"][0]["ok"] is True, body
    assert body["results"][0]["backup_removed"] is True, body
    assert captions.caption_path(first).read_bytes() == original, "the restore did not put the bytes back"
    assert not captions.backup_path(first).exists(), "the restore left the consumed backup behind"
    assert not cache.is_file(), "the undo left the text-encoder cache in place (invariant number one)"

    # (3) Nothing to restore is a per-entry answer; out of bounds is still the whole-request 403
    again = api_client.post("/api/captions/restore", json={"images": [str(first)]})
    assert again.status_code == 200, again.text[:300]
    assert again.json()["restored"] == 0 and again.json()["failed"] == 1, again.json()
    assert again.json()["results"][0]["error"]["code"] == "no_backup", again.json()
    assert captions.caption_path(first).read_bytes() == original, "a restore with nothing to do wrote anyway"
    outside = api_client.post("/api/captions/restore", json={"images": [r"C:\\Windows\\win.ini"]})
    assert outside.status_code == 403, outside.text[:300]

    # (4) The net is re-armed for the *current* content: the restored bytes equal the old backup, so
    # editing the caption first is what makes this stage able to tell the two apart.
    reseeded = api_client.put("/api/captions", json={"image": str(first), "text": "hand edited"})
    assert reseeded.status_code == 200, reseeded.text[:300]
    second = api_client.post(
        "/api/captions/batch", json={"images": [str(first)], "op": "append", "tags": ["third"]}
    )
    assert second.status_code == 200, second.text[:300]
    assert captions.backup_path(first).read_text(encoding="utf-8") == "hand edited", (
        "the write after a restore did not back the current caption up"
    )

# --------------------------------------------------------------------------
# A43  §4.16 The dataset root's dataset.toml, read-only
# --------------------------------------------------------------------------
# The readiness strip's third segment used to be a shrug ("dataset.toml 未检查"), because no endpoint
# could tell the app whether the trainer's file exists: POST /api/dataset/toml is pure computation.
# §4.16 adds one stat. Scored here independently of the implementer's unit tests: the fact matches the
# disk in both directions, the endpoint cannot write (the directory is fingerprinted around every
# call), the allowlist still decides, and the strip may not turn a plan into a file.

#: A43b's probe. Written by the accepting side on purpose: the repository's own fixture belongs to the
#: implementer, so it would drift with the implementer's code instead of catching it.
_A43_PROBE = r'''/**
 * A43's independent probe: run the real readiness strip over a fake DOM and ask what its third segment
 * says. Deliberately *not* the repository's own fixture - the criterion has to be able to fail when the
 * implementation drifts, and a copy of the implementer's fixture would drift along with it.
 *
 * Usage: node a43_probe.mjs <absolute path to web/>
 */
import { pathToFileURL } from "node:url";

const WEB = process.argv[2];
if (!WEB) throw new Error("usage: node a43_probe.mjs <web dir>");
const url = (rel) => pathToFileURL(WEB + "/" + rel).href;

class FakeClassList {
  constructor() { this.items = new Set(); }
  add(name) { this.items.add(name); }
  remove(name) { this.items.delete(name); }
  toggle(name, on) { if (on) this.items.add(name); else this.items.delete(name); }
}

class FakeNode {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase();
    this.children = [];
    this.classList = new FakeClassList();
    this.dataset = {};
    this.attributes = {};
    this.listeners = {};
    this.style = {};
    this.textContent = "";
    this.className = "";
    this.hidden = false;
    this.disabled = false;
    this.type = "";
  }
  append(...nodes) { for (const node of nodes) this.children.push(node); }
  replaceChildren(...nodes) { this.children = []; this.append(...nodes); }
  setAttribute(name, value) { this.attributes[name] = value; }
  getAttribute(name) { return this.attributes[name]; }
  addEventListener(type, handler) { (this.listeners[type] ||= []).push(handler); }
}

globalThis.window = {
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  addEventListener() {},
  removeEventListener() {},
};
globalThis.CustomEvent = class CustomEvent {
  constructor(type, init) { this.type = type; this.detail = init && init.detail; }
};
globalThis.document = {
  documentElement: { lang: "" },
  title: "",
  body: new FakeNode("body"),
  createElement: (tag) => new FakeNode(tag),
  querySelectorAll: () => [],
  addEventListener() {},
  removeEventListener() {},
  dispatchEvent() { return true; },
};

function expect(condition, message) {
  if (!condition) throw new Error("A43 probe: " + message);
}

const strings = await import(url("js/strings.js"));
const { createReadinessStrip } = await import(url("js/readiness.js"));
// The English pack: the claim has to hold in every language, and this is the one the Chinese default
// would hide from a probe that reads whatever the implementer reads all day.
expect(strings.setLocale("en"), "could not switch the probe to English");
const { t } = strings;

const host = new FakeNode("div");
const strip = createReadinessStrip({
  host,
  onSelectMissingCaptions() {},
  onCacheAction() {},
  onOpenExport() {},
});
const segment = host.children.find((node) => node.dataset.segment === "toml");
expect(segment, "the strip has no dataset.toml segment at all");
const text = () => segment.textContent;
const ROOT = "F:/dataset";

// (1) Nothing has been asked: the strip's own state, not a claim about the file.
strip.setTomlFile(null);
expect(text() === t("readiness.tomlUnchecked"), "no answer must read as 'not checked', got " + JSON.stringify(text()));

// (2) The request failed. "Could not ask" is never "not there" - and never "there" either.
strip.setTomlFile({ root: ROOT, failed: true });
expect(text() === t("readiness.tomlUnread"), "a failed request must say so, got " + JSON.stringify(text()));
expect(text() !== t("readiness.tomlMissing"), "a failed request was painted as 'no dataset.toml'");
expect(text() !== t("readiness.tomlReady"), "a failed request was painted as ready");

// (3) THE drift this criterion exists for: the export endpoint writes nothing, so a clean report is
// not a file on disk.
const clean = { root: ROOT, summary: { subsets: 219, images: 1653, skipped: 0, warnings: 0 }, problems: 0 };
strip.setTomlFile({ root: ROOT, exists: false });
strip.setToml(clean);
expect(text() === t("readiness.tomlMissing"), "a clean export report read as a file on disk: " + JSON.stringify(text()));
expect(text() !== t("readiness.tomlReady"), "the export panel's plan produced 'ready' with no file on disk");

// (4) The file is there: the read-only fact - not the report - is what decides.
strip.setTomlFile({ root: ROOT, exists: true });
expect(text() === t("readiness.tomlReady"), "an existing dataset.toml must read as ready, got " + JSON.stringify(text()));

// (5) A report belongs to the directory the panel was pointed at: another directory's plan must never
// attach its numbers to this dataset's file.
strip.setToml({ root: "F:/other-dataset", summary: { subsets: 1, images: 4, skipped: 1, warnings: 9 }, problems: 4 });
expect(text() === t("readiness.tomlReady"), "another directory's report was attached to this file: " + JSON.stringify(text()));

// (6) With the file there, the panel's own report is what is left to be wrong - in the panel's words.
strip.setToml({ root: ROOT, summary: { subsets: 219, images: 1653, skipped: 0, warnings: 2 }, problems: 0 });
expect(
  text() === t("export.warningsTitle", { n: 2 }),
  "the export panel's own heading and number must be reused, got " + JSON.stringify(text()),
);

// (7) And the file vanishing goes straight back to the fact.
strip.setTomlFile({ root: ROOT, exists: false });
expect(text() === t("readiness.tomlMissing"), "a deleted dataset.toml must go back to missing, got " + JSON.stringify(text()));

console.log("A43 probe OK");
'''


def _a43_fingerprint(root: Path) -> dict:
    """Every entry under `root` by relative path: (size, mtime_ns).

    The read-only claim is about the directory, not about the response - a before/after comparison is
    the only form of it that can fail when an implementation quietly creates, backs up or touches the
    file it was asked about.
    """
    entries = {}
    for path in sorted(root.rglob("*")):
        info = path.stat()
        entries[path.relative_to(root).as_posix()] = (info.st_size, info.st_mtime_ns)
    return entries


def test_A43_the_dataset_root_toml_is_a_read_only_fact(api_client, api_sandbox) -> None:
    """A43 (§4.16): one stat of <root>/dataset.toml, and nothing else happens.

    The subject is the dataset root, so a nested directory answers about its own file; the answer is
    the real stat (mtime and size included, which a canned guess cannot produce); asking about a root
    that has no file must not create one - that would make every later answer a lie.
    """
    work = api_sandbox / "a43"
    root = work / "dataset"
    (root / "post").mkdir(parents=True, exist_ok=True)
    (root / "post" / "1.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (root / "post" / "1.txt").write_text("1girl", encoding="utf-8")

    def ask(path):
        resp = api_client.get("/api/dataset/toml/status", params={"root": str(path)})
        assert resp.status_code == 200, resp.text[:300]
        return resp.json()

    before = _a43_fingerprint(work)
    absent = ask(root)
    assert absent["exists"] is False, absent
    assert Path(absent["path"]) == root.resolve() / "dataset.toml", absent
    assert Path(absent["root"]) == root.resolve(), absent
    assert absent["mtime"] is None and absent["size"] is None, absent

    nested = ask(root / "post")
    assert Path(nested["root"]) == (root / "post").resolve(), nested
    assert nested["exists"] is False, "a subdirectory answered with the dataset root's file"
    assert _a43_fingerprint(work) == before, "asking about a missing dataset.toml created or touched something"

    payload = b"# hand tuned by the user\n[datasets]\nbatch_size = 8\n"
    target = root / "dataset.toml"
    target.write_bytes(payload)
    info = target.stat()
    present = ask(root)
    assert present["exists"] is True, present
    assert present["size"] == info.st_size, "the reported size is not the file's"
    assert abs(present["mtime"] - info.st_mtime) < 1e-6, "the reported mtime is not the file's"

    after = _a43_fingerprint(work)
    for _ in range(3):
        ask(root)
    assert _a43_fingerprint(work) == after, "the status endpoint wrote to the directory it only reports on"
    assert target.read_bytes() == payload, "the status endpoint rewrote the file it was asked about"
    assert not (root / "dataset.toml.bak").exists(), "the status endpoint made a backup nobody asked for"

    # The order of the two facts is the whole point: present, then gone again.
    target.unlink()
    assert ask(root)["exists"] is False, "a deleted dataset.toml was still reported as present"

    outside = "C:" + os.sep + "Windows" if os.name == "nt" else "/etc"
    refused = api_client.get("/api/dataset/toml/status", params={"root": outside})
    assert refused.status_code == 403, refused.text[:200]
    assert refused.json()["error"]["code"] in ("outside_roots", "bad_path"), refused.text[:200]


def test_A43b_the_strip_cannot_turn_an_export_plan_into_a_file(tmp_path) -> None:
    """A43, the half a backend test cannot see: an independent node probe over the real readiness strip.

    The export endpoint is pure computation, so "the panel's checks passed" says nothing about the
    disk. With no dataset.toml the segment must say the file is missing even while the panel hands it a
    clean report, and a failed request is its own third state rather than either claim.
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("no node on this machine")
    web = REPO / "src" / "kohya_dataset_tagger" / "web"
    assert (web / "js" / "readiness.js").is_file(), "the readiness strip module is gone"
    probe = tmp_path / "a43_probe.mjs"
    probe.write_text(_A43_PROBE, encoding="utf-8")
    done = subprocess.run(
        [node, str(probe), str(web)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert done.returncode == 0, "the strip probe failed:\n%s\n%s" % (done.stdout, done.stderr)
    assert "A43 probe OK" in done.stdout, done.stdout


# --------------------------------------------------------------------------
# A44  image cropping (section 4.17)
# --------------------------------------------------------------------------


def _a44_fingerprint(root: Path) -> dict:
    """Every file's (size, mtime_ns, bytes) - the source image may not change in any of the three."""
    out = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            stat = path.stat()
            out[path.name] = (stat.st_size, stat.st_mtime_ns, path.read_bytes())
    return out


def test_A44_cropping_archives_beside_the_source(api_client, api_sandbox) -> None:
    """A44 (section 4.17): the rectangle is applied in display coordinates, the archive is always
    stem plus _N beside its source, nothing overwrites anything, no caption is inherited, and the
    scaling half works from the cropped size (so a box whose area is the target area is not
    resampled at all)."""
    from PIL import Image, ImageOps

    work = api_sandbox / "a44"
    root = work / "dataset"
    root.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (3000, 2000), (10, 20, 30)).save(root / "1.png", "PNG")
    (root / "1.txt").write_bytes("1girl, solo".encode("utf-8"))
    Image.new("RGB", (400, 300), (200, 30, 30)).save(root / "2.jpeg", "JPEG", quality=90)
    rotated = Image.new("RGB", (200, 100), (5, 5, 5))
    exif = rotated.getexif()
    exif[0x0112] = 6
    rotated.save(root / "3.jpg", "JPEG", quality=90, exif=exif)
    outside = api_sandbox.parent / "a44_outside"
    outside.mkdir(exist_ok=True)
    Image.new("RGB", (50, 50), (1, 1, 1)).save(outside / "4.png", "PNG")
    #: A file the user made themselves, sitting exactly on the name the second archive would
    #: otherwise take: it must survive the run byte for byte, and the crop must step over it.
    mine = root / "1_3.png"
    Image.new("RGB", (7, 7), (1, 2, 3)).save(mine, "PNG")

    before = _a44_fingerprint(root)

    def apply(**payload):
        payload.setdefault("format", "keep")
        resp = api_client.post("/api/crop/apply", json=payload)
        assert resp.status_code == 200, resp.text[:400]
        return resp.json()

    # 1. the archive is numbered, beside the source, and the crop really is that window
    first = apply(src=str(root / "1.png"), crop={"x": 100, "y": 50, "width": 800, "height": 600})
    assert Path(first["archived"]) == root / "1_1.png", first["archived"]
    assert first["format"] == "png"
    assert (first["output_width"], first["output_height"]) == (800, 600)
    assert first["resampled"] is False and first["scale"] == 1.0
    with Image.open(first["archived"]) as produced, Image.open(root / "1.png") as source:
        window = source.convert("RGB").crop((100, 50, 900, 650))
        assert produced.convert("RGB").tobytes() == window.tobytes()

    # 2. a second crop takes the next number; an existing file is never replaced
    second = apply(src=str(root / "1.png"), crop={"x": 0, "y": 0, "width": 10, "height": 10})
    assert Path(second["archived"]) == root / "1_2.png", second["archived"]
    third = apply(src=str(root / "1.png"), crop={"x": 0, "y": 0, "width": 10, "height": 10})
    assert Path(third["archived"]) == root / "1_4.png", third["archived"]
    with Image.open(mine) as untouched:
        assert untouched.size == (7, 7), "an archive overwrote a file the user made"

    # 3. no caption is inherited - the source has one, the archive does not
    assert not (root / "1_1.txt").exists(), "the archive inherited the source's caption"

    # 4. "keep" keeps the format and the suffix spelling
    kept = apply(src=str(root / "2.jpeg"), crop={"x": 0, "y": 0, "width": 100, "height": 100})
    assert kept["format"] == "jpeg" and Path(kept["archived"]) == root / "2_1.jpeg"
    with Image.open(kept["archived"]) as produced:
        assert produced.format == "JPEG"

    # 5. display coordinates: orientation 6 means the crop box is applied to the rotated image
    turned = apply(src=str(root / "3.jpg"), crop={"x": 0, "y": 100, "width": 100, "height": 100})
    assert (turned["orig_width"], turned["orig_height"]) == (100, 200)
    with Image.open(root / "3.jpg") as opened:
        reference = ImageOps.exif_transpose(opened).crop((0, 100, 100, 200))
    with Image.open(turned["archived"]) as produced:
        assert produced.convert("RGB").tobytes() == reference.convert("RGB").tobytes()

    # 6. the scaling half starts from the cropped size: an 1881x1254 box already has the target area
    exact = apply(
        src=str(root / "1.png"),
        crop={"x": 0, "y": 0, "width": 1881, "height": 1254},
        scale={"target_width": 1536, "target_height": 1536},
    )
    assert exact["scale"] == pytest.approx(1.0, abs=1e-6), exact["scale"]
    assert exact["resampled"] is False, "a box whose area is the target area was resampled anyway"
    assert (exact["output_width"], exact["output_height"]) == (1881, 1254)

    # 7. clamping is not rejection, and it is reported
    clamped_box = apply(src=str(root / "1.png"), crop={"x": 2900, "y": 1900, "width": 800, "height": 600})
    assert clamped_box["crop"] == {"x": 2200, "y": 1400, "width": 800, "height": 600}
    assert clamped_box["crop_requested"] == {"x": 2900, "y": 1900, "width": 800, "height": 600}
    assert any("clamped" in item for item in clamped_box["warnings"]), clamped_box["warnings"]

    # 8. the boundary: outside the whitelist is 403 and writes nothing
    resp = api_client.post(
        "/api/crop/apply",
        json={"src": str(outside / "4.png"), "crop": {"width": 10, "height": 10}, "format": "keep"},
    )
    assert resp.status_code == 403, resp.text[:300]
    assert list(outside.glob("4_*")) == []

    # 9. nothing else on disk moved, and no temp file was left behind
    expected_new = {"1_1.png", "1_2.png", "1_4.png", "1_5.png", "1_6.png", "2_1.jpeg", "3_1.jpg"}
    after = _a44_fingerprint(root)
    created = sorted(set(after) - set(before) - expected_new)
    assert created == [], "the crop created unexpected files: %s" % created
    assert expected_new.issubset(set(after)), "an archive is missing: %s" % sorted(expected_new - set(after))
    for name, snapshot in before.items():
        assert after[name] == snapshot, "%s changed (bytes, size or mtime)" % name
    assert [path.name for path in root.rglob(".tmp-scale-*")] == []


