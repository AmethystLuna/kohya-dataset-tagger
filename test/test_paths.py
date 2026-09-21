"""Unit tests for core/paths.py - including read-only criteria against the real dataset (acceptance A2 / A3 / A4).

Hard boundary (spec §0): the dataset configured in local_paths.ini - the committed sample dataset
under test/fixtures/sample_dataset/ when it is unset - is a read-only test set.
Every assertion this file makes about it uses only listdir / stat: it creates, modifies, deletes, and renames nothing,
and produces no __pycache__ inside it (that directory is never imported). Cases that need to write use pytest's
tmp_path.

config.py has no dedicated test file (the frozen file tree did not allocate one for it), and what it manages is the allowlist roots,
so its cases also live at the end of this file, under the section "config.py: the allowlist roots configuration entry point".
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from kohya_dataset_tagger import config
from kohya_dataset_tagger.core import paths

import local_paths

#: The dataset the A2 / A3 criteria read, from `local_paths.ini` (the committed sample dataset when unset).
REAL_ROOT = local_paths.dataset_root()

requires_real_dataset = pytest.mark.skipif(
    not REAL_ROOT.is_dir(), reason="no dataset configured in local_paths.ini: %s" % REAL_ROOT
)


@pytest.fixture(autouse=True)
def _neutral_state():
    """Restore the allowlist to the neutral 'nothing configured' state before and after every case, so cases do not leak into each other."""
    config.load(env={}, roots=[])
    yield
    config.load(env={}, roots=[])


def _write(path: Path, data: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _make_reparse_link(link: Path, target: Path) -> bool:
    """Create a directory reparse point at link pointing at target (a symlink, degrading to a junction).

    A normal user on this machine lacks SeCreateSymbolicLinkPrivilege (WinError 1314), but a junction does not need
    that privilege, and Path.resolve() expands it to the real path just the same - for the 'resolve first, then assert the prefix'
    line of defense, the two are the same class of escape.
    """
    try:
        os.symlink(str(target), str(link), target_is_directory=True)
        return True
    except OSError:
        pass
    if os.name != "nt":
        return False
    proc = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
        # Explicit UTF-8: the default follows the locale (GBK here), and mklink's output contains 0x80 bytes that fail to decode,
        # turning into a real failure instead of a warning on machines with a different locale.
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode == 0


def _remove_reparse_link(link: Path) -> None:
    """Remove the link itself first, so tmp_path cleanup does not recurse into the target directory through it."""
    try:
        os.rmdir(link)
    except OSError:
        try:
            os.unlink(link)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def test_image_exts_contains_both_cases() -> None:
    for ext in (".png", ".jpg", ".jpeg", ".webp", ".bmp"):
        assert ext in paths.IMAGE_EXTS
        assert ext.upper() in paths.IMAGE_EXTS
    # Formats the trainer supports only with optional dependencies are not in the allowlist
    assert ".avif" not in paths.IMAGE_EXTS
    assert ".jxl" not in paths.IMAGE_EXTS
    assert ".txt" not in paths.IMAGE_EXTS
    assert ".npz" not in paths.IMAGE_EXTS
    assert ".safetensors" not in paths.IMAGE_EXTS


def test_caption_ext_and_cache_dirnames() -> None:
    assert paths.CAPTION_EXT_DEFAULT == ".txt"
    assert paths.CACHE_DIRNAMES == frozenset({"cache_text_encoder", "latent_cache"})


# ---------------------------------------------------------------------------
# configure / get_roots
# ---------------------------------------------------------------------------


def test_configure_and_get_roots_keep_order_and_dedupe(tmp_path: Path) -> None:
    first = tmp_path / "a"
    second = tmp_path / "b"
    first.mkdir()
    second.mkdir()
    paths.configure([first, second, first, str(first) + os.sep])
    assert paths.get_roots() == (first.resolve(), second.resolve())


def test_get_roots_is_empty_by_default() -> None:
    paths.configure([])
    assert paths.get_roots() == ()


def test_configure_accepts_a_single_path_not_only_a_sequence(tmp_path: Path) -> None:
    """Guard against slips: configure("F:/data") must not be treated as a character sequence."""
    paths.configure(str(tmp_path))
    assert paths.get_roots() == (tmp_path.resolve(),)
    paths.configure(tmp_path)
    assert paths.get_roots() == (tmp_path.resolve(),)


# ---------------------------------------------------------------------------
# resolve_under_roots (acceptance A4)
# ---------------------------------------------------------------------------


def test_resolve_returns_absolute_resolved_path_inside_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    image = _write(root / "sub" / "1.jpeg")
    paths.configure([root])
    resolved = paths.resolve_under_roots(image)
    assert resolved == image.resolve()
    assert resolved.is_absolute()
    # An equivalent spelling with .. is allowed as long as it ends up inside the root
    assert paths.resolve_under_roots(root / "sub" / ".." / "sub" / "1.jpeg") == image.resolve()
    # The root itself is also inside the allowlist
    assert paths.resolve_under_roots(root) == root.resolve()


def test_resolve_rejects_parent_traversal(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    secret = _write(tmp_path / "secret" / "x.txt")
    paths.configure([root])
    with pytest.raises(paths.PathOutsideRoots):
        paths.resolve_under_roots(root / ".." / "secret" / "x.txt")
    with pytest.raises(paths.PathOutsideRoots):
        paths.resolve_under_roots(secret)
    with pytest.raises(paths.PathOutsideRoots):
        paths.resolve_under_roots(r"..\..\etc\passwd")


def test_resolve_rejects_absolute_path_outside_root(tmp_path: Path) -> None:
    paths.configure([tmp_path])
    with pytest.raises(paths.PathOutsideRoots):
        paths.resolve_under_roots(Path("C:\\Windows"))


def test_resolve_rejects_sibling_that_only_shares_a_string_prefix(tmp_path: Path) -> None:
    """The watershed between commonpath and str.startswith: sample_dataset_evil is not sample_dataset."""
    root = tmp_path / "root"
    root.mkdir()
    evil = _write(tmp_path / "root_evil" / "1.jpeg")
    paths.configure([root])
    assert str(evil.resolve()).startswith(str(root.resolve()))  # the string prefix really is the same
    with pytest.raises(paths.PathOutsideRoots):
        paths.resolve_under_roots(evil)


def test_resolve_rejects_reparse_point_escape(tmp_path: Path) -> None:
    """Symlink / junction escape: after resolve() expands it, it must land back inside the allowlist."""
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    secret = _write(outside / "secret.jpeg")
    link = root / "linkdir"
    if not _make_reparse_link(link, outside):
        pytest.skip("this machine can create neither a symlink nor a junction")
    try:
        # Precondition: this link really does reach outside the root, so the test does not pass by accident
        assert (link / "secret.jpeg").resolve() == secret.resolve()
        paths.configure([root])
        with pytest.raises(paths.PathOutsideRoots):
            paths.resolve_under_roots(link / "secret.jpeg")
        with pytest.raises(paths.PathOutsideRoots):
            paths.resolve_under_roots(link)
    finally:
        _remove_reparse_link(link)


def test_resolve_checks_existence_after_the_whitelist(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    paths.configure([root])
    missing = root / "nope.jpeg"
    with pytest.raises(FileNotFoundError):
        paths.resolve_under_roots(missing)
    assert paths.resolve_under_roots(missing, must_exist=False) == missing.resolve()
    # An out-of-bounds path is PathOutsideRoots whether or not it exists, without leaking 'does the file exist'
    with pytest.raises(paths.PathOutsideRoots):
        paths.resolve_under_roots(tmp_path / "nope.jpeg", must_exist=False)


def test_resolve_without_configured_roots_rejects_everything(tmp_path: Path) -> None:
    paths.configure([])
    with pytest.raises(paths.PathOutsideRoots):
        paths.resolve_under_roots(tmp_path)


def test_path_outside_roots_is_a_value_error() -> None:
    assert issubclass(paths.PathOutsideRoots, ValueError)


def test_each_raise_site_carries_its_own_code_and_params(tmp_path: Path) -> None:
    """The structured payload at each of the three raise sites (p0-spec §4.9 supplement 4), with the message string unchanged to the letter.

    This is the root-cause criterion for this group of bugs: when the exception carries no `code` / `params`, the api layer can only hardcode
    `("outside_roots", str(exc))`, and the frontend's `msg.outside_roots` `{path}` can never be filled.
    """
    paths.configure([])
    with pytest.raises(paths.PathOutsideRoots) as unconfigured:
        paths.resolve_under_roots(tmp_path)
    assert unconfigured.value.code == "paths_unconfigured"
    assert unconfigured.value.params == {}
    assert str(unconfigured.value) == "no root allowlist configured (call paths.configure() or config.load() first)"

    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    paths.configure([root])
    with pytest.raises(paths.PathOutsideRoots) as escaped:
        paths.resolve_under_roots(outside)
    assert escaped.value.code == "outside_roots"
    assert escaped.value.params == {"path": str(outside.resolve())}
    assert str(escaped.value) == "path is not inside any allowed root: %s" % outside.resolve()


def test_unresolvable_path_carries_bad_path_payload(tmp_path: Path, monkeypatch) -> None:
    """When `resolve()` raises OSError it is `bad_path` + `{path, detail}`, not a generic outside_roots."""
    paths.configure([tmp_path])
    real_resolve = Path.resolve

    def boom(self: Path, *args, **kwargs):
        if self.name == "boom":
            raise OSError("device unavailable")
        return real_resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", boom)
    with pytest.raises(paths.PathOutsideRoots) as info:
        paths.resolve_under_roots("boom")
    assert info.value.code == "bad_path"
    assert info.value.params == {"path": "boom", "detail": "device unavailable"}
    assert str(info.value) == "cannot resolve path 'boom': device unavailable"


# ---------------------------------------------------------------------------
# is_cache_or_hidden
# ---------------------------------------------------------------------------


def test_is_cache_or_hidden_matrix() -> None:
    assert paths.is_cache_or_hidden("cache_text_encoder")
    assert paths.is_cache_or_hidden("latent_cache")
    assert paths.is_cache_or_hidden(".git")
    assert paths.is_cache_or_hidden(".kohya-dataset-tagger-thumbs")
    assert not paths.is_cache_or_hidden("1_sample_alpha")
    assert not paths.is_cache_or_hidden("1.jpeg")
    assert not paths.is_cache_or_hidden("")
    # .npz / .safetensors are blocked by the extension allowlist, not by this
    assert not paths.is_cache_or_hidden("1_anima_te.safetensors")


# ---------------------------------------------------------------------------
# iter_images / list_subdirs / count_images
# ---------------------------------------------------------------------------


def test_iter_images_filters_extensions_and_hidden_dirs(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    _write(root / "1.jpeg")
    _write(root / "2.JPEG")
    _write(root / "3.PnG")
    _write(root / "4.webp")
    _write(root / "5.bmp")
    _write(root / "6.txt")
    _write(root / "7.npz")
    _write(root / "8.safetensors")
    _write(root / "cache_text_encoder" / "9.jpeg")
    _write(root / "latent_cache" / "10.jpeg")
    _write(root / ".hidden" / "11.jpeg")
    (root / "sub").mkdir()

    found = [p.name for p in paths.iter_images(root)]
    assert found == ["1.jpeg", "2.JPEG", "3.PnG", "4.webp", "5.bmp"]
    assert paths.count_images(root) == 5


def test_iter_images_is_natural_sorted(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    for name in ("10.jpeg", "2.jpeg", "1.jpeg", "B.jpeg", "a.jpeg"):
        _write(root / name)
    assert [p.name for p in paths.iter_images(root)] == [
        "1.jpeg", "2.jpeg", "10.jpeg", "a.jpeg", "B.jpeg",
    ]


def test_iter_images_recursive_prunes_cache_and_hidden_at_every_level(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    _write(root / "sub" / "1.jpeg")
    _write(root / "sub" / "cache_text_encoder" / "2.jpeg")
    _write(root / "sub" / "latent_cache" / "3.jpeg")
    _write(root / "sub" / ".hidden" / "4.jpeg")
    _write(root / "sub" / "deep" / "5.png")
    _write(root / "sub" / "deep" / ".cache" / "6.png")

    assert [p.name for p in paths.iter_images(root)] == []
    assert sorted(p.name for p in paths.iter_images(root, recursive=True)) == ["1.jpeg", "5.png"]
    assert paths.count_images(root, recursive=True) == 2


def test_iter_images_missing_directory_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        paths.iter_images(tmp_path / "nope")
    with pytest.raises(NotADirectoryError):
        paths.iter_images(_write(tmp_path / "a.txt"))


def test_list_subdirs_filters_and_sorts(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    (root / "2_post").mkdir(parents=True)
    (root / "10_post").mkdir()
    (root / "cache_text_encoder").mkdir()
    (root / "latent_cache").mkdir()
    (root / ".hidden").mkdir()
    _write(root / "1.jpeg")

    assert [p.name for p in paths.list_subdirs(root)] == ["2_post", "10_post"]
    assert paths.list_subdirs(root)[0] == root / "2_post"


def test_count_images_matches_iter_images(tmp_path: Path) -> None:
    root = tmp_path / "dataset"
    _write(root / "1.jpeg")
    _write(root / "2.png")
    _write(root / "sub" / "3.png")
    assert paths.count_images(root) == len(paths.iter_images(root)) == 2
    assert paths.count_images(root, recursive=True) == len(paths.iter_images(root, recursive=True)) == 3


# ---------------------------------------------------------------------------
# Real dataset: acceptance A2 / A3 (read-only)
# ---------------------------------------------------------------------------


@requires_real_dataset
def test_real_dataset_iter_images_on_root_is_zero() -> None:
    """First half of A2: the trainer's glob_images does not recurse, so the parent directory shows not one image."""
    assert paths.iter_images(REAL_ROOT) == []


@requires_real_dataset
def test_real_dataset_subdir_image_total_is_the_sum_of_its_subdirectories() -> None:
    """Second half of A2: the per-subdirectory counts and the recursive count agree.

    Deliberately not a golden number: the dataset is alive, and pinning "1653" made every legitimate
    addition (the crop tool added two images on 2026-09-20) turn the criterion red while the code was
    fine. What A2 is really about - the listing agrees with itself - holds for any content.
    """
    subdirs = paths.list_subdirs(REAL_ROOT)
    assert subdirs, "the configured dataset has no subdirectory at all: %s" % REAL_ROOT
    assert sum(paths.count_images(d) for d in subdirs) == paths.count_images(REAL_ROOT, recursive=True)


@requires_real_dataset
def test_real_dataset_subdir_list_has_no_cache_or_hidden_entries() -> None:
    """A3: no cache_text_encoder / latent_cache / hidden directory shows up in the listing.

    This one **can** fail: the filter lives in `list_subdirs`, and the committed sample dataset
    carries a `cache_text_encoder/` and a `latent_cache/` on purpose, so the criterion exercises the
    filter instead of trusting it.
    """
    subdirs = paths.list_subdirs(REAL_ROOT)
    assert subdirs, "the configured dataset has no subdirectory at all: %s" % REAL_ROOT
    assert [p for p in subdirs if paths.is_cache_or_hidden(p.name)] == []


# ---------------------------------------------------------------------------
# config.py: the allowlist roots configuration entry point
# ---------------------------------------------------------------------------


def test_config_defaults() -> None:
    cfg = config.load(env={}, roots=[], model_search_roots=[])
    assert cfg.host == "127.0.0.1"
    assert cfg.port == 3001
    assert cfg.default_caption_ext == ".txt"
    assert cfg.roots == ()
    assert isinstance(cfg.thumb_cache_dir, Path)
    assert isinstance(cfg.model_search_roots, tuple)


def test_config_reads_roots_from_env_and_splits_on_pathsep(tmp_path: Path) -> None:
    one = tmp_path / "one"
    two = tmp_path / "two with space"
    one.mkdir()
    two.mkdir()
    raw = os.pathsep.join([str(one), str(two)])
    cfg = config.load(env={"KOHYA_TAGGER_ROOTS": raw})
    assert cfg.roots == (one, two)
    # Quoted, newline-containing, and extra-whitespace spellings must all be accepted
    quoted = '"%s"%s%s' % (one, os.pathsep, two)
    assert config.load(env={"KOHYA_TAGGER_ROOTS": quoted}).roots == (one, two)
    assert config.load(env={"KOHYA_TAGGER_ROOTS": "%s\n%s" % (one, two)}).roots == (one, two)
    assert config.load(env={"KOHYA_TAGGER_ROOTS": ""}).roots == ()


def test_config_load_publishes_roots_to_paths(tmp_path: Path) -> None:
    """The roots in the config and the allowlist in paths must be the same one, otherwise the endpoints recognize a different one."""
    cfg = config.load(env={}, roots=[tmp_path])
    assert cfg.roots == (tmp_path,)
    assert paths.get_roots() == (tmp_path.resolve(),)
    assert paths.resolve_under_roots(tmp_path) == tmp_path.resolve()


def test_config_thumb_cache_dir_from_env_and_default(tmp_path: Path) -> None:
    from_env = config.load(env={"KOHYA_TAGGER_THUMB_CACHE": str(tmp_path / "c")}, roots=[])
    assert from_env.thumb_cache_dir == tmp_path / "c"
    defaulted = config.load(env={"LOCALAPPDATA": str(tmp_path)}, roots=[])
    assert defaulted.thumb_cache_dir == tmp_path / "kohya-dataset-tagger-thumbs"
    assert config.THUMB_CACHE_DIRNAME == "kohya-dataset-tagger-thumbs"


def test_config_model_search_roots_from_env(tmp_path: Path) -> None:
    one = tmp_path / "models"
    two = tmp_path / "hub"
    cfg = config.load(env={"KOHYA_TAGGER_MODELS": os.pathsep.join([str(one), str(two)])}, roots=[])
    assert cfg.model_search_roots == (one, two)
    # The defaults include at least the HF cache and the in-repo models/ (nonexistent directories are left for the registry to ignore)
    defaulted = config.load(env={}, roots=[])
    assert any(p.name == "hub" for p in defaulted.model_search_roots)
    # **Order is priority** (adjusted 2026-09-17, do not change it back):
    # the repo models/ moved from 'last position' to 'position 0' - it is the directory shown to users (it contains a placeholder file
    # saying 'put tagger models in this directory'), while the HF cache moved to last.
    # Reason: a model the user explicitly placed should not be shadowed by a same-named copy in the cache.
    assert defaulted.model_search_roots[0] == Path(__file__).resolve().parents[1] / "models"
    assert defaulted.model_search_roots[-1] != defaulted.model_search_roots[0]


def test_config_kwargs_override_env(tmp_path: Path) -> None:
    cfg = config.load(
        env={"KOHYA_TAGGER_ROOTS": str(tmp_path / "env")},
        roots=[tmp_path / "kw"],
        host="0.0.0.0",
        port=3999,
        default_caption_ext=".caption",
    )
    assert cfg.roots == (tmp_path / "kw",)
    assert (cfg.host, cfg.port, cfg.default_caption_ext) == ("0.0.0.0", 3999, ".caption")


def test_config_current_does_not_reset_the_whitelist(tmp_path: Path) -> None:
    """current() is a read-only accessor: a request thread reading the config must not clear the allowlist."""
    paths.configure([tmp_path])
    config.load(env={}, roots=[])
    paths.configure([tmp_path])
    config.current()
    assert paths.get_roots() == (tmp_path.resolve(),)
