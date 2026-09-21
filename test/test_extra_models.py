"""Extra model scan paths (§3.7 / §4.7).

The semantic difference from KOHYA_TAGGER_MODELS is the whole point of this feature:
**that one replaces wholesale, this one appends**. Setting KOHYA_TAGGER_MODELS throws away the
repository's models/ and every auto-discovered location, and "add one more directory" is clearly
not what a user wants.

**Every case must point `model_paths_file` at tmp_path.** Its default is the repository root's
`model_paths.txt` -- a **local file on the development machine** (gitignored) whose contents vary
per person. Hit for real on 2026-09-17: after writing that machine's two model paths into
`model_paths.txt`, two cases in this file went red immediately (the extra file root sorted ahead
of the env root).
"""
from __future__ import annotations

import os
from pathlib import Path

from kohya_dataset_tagger import config


def test_extras_are_inserted_right_after_the_repo_models_dir() -> None:
    repo_models = Path(__file__).resolve().parents[1] / "models"
    plain = config.default_model_search_roots(environ={})
    assert plain[0] == repo_models, "the repository's models/ must be position 0 (A25 guards this too)"

    extra = [Path("D:/my-taggers"), Path("E:/more")]
    with_extra = config.default_model_search_roots(environ={}, extra=extra)
    assert with_extra[:3] == (repo_models, *extra), (
        "extras should come right after position 0, measured %r" % (with_extra[:4],))
    assert with_extra[3:] == plain[1:], "extras should not disturb the rest of the order"


def test_env_var_appends_to_the_defaults(tmp_path: Path) -> None:
    # Join with os.pathsep and use tmp_path as the path: a drive-letter form like D:/env1 would be
    # split on `:` on POSIX (os.pathsep is `:`), so the test would no longer be testing "append"
    # -- one of the three reds on real Ubuntu on 2026-09-17.
    one, two = tmp_path / "env1", tmp_path / "env2"
    cfg = config.load(
        env={"KOHYA_TAGGER_EXTRA_MODELS": os.pathsep.join([str(one), str(two)])},
        model_paths_file=tmp_path / "model_paths.txt",
    )
    names = [p.name for p in cfg.model_search_roots]
    assert names[0] == "models", "the repository's models/ should still be position 0"
    assert names[1:3] == ["env1", "env2"], names[:4]
    # None of the auto-discovered locations in the defaults may be missing
    assert "hub" in names or any("huggingface" in str(p) for p in cfg.model_search_roots)


def test_models_env_still_replaces_but_extras_are_appended(tmp_path: Path) -> None:
    """`--models` / KOHYA_TAGGER_MODELS keeps its authority: it is still the complete list."""
    only, extra1 = tmp_path / "only", tmp_path / "extra1"
    cfg = config.load(
        env={
            "KOHYA_TAGGER_MODELS": str(only),
            "KOHYA_TAGGER_EXTRA_MODELS": str(extra1),
        },
        model_paths_file=tmp_path / "model_paths.txt",
    )
    names = [p.name for p in cfg.model_search_roots]
    assert names == ["only", "extra1"], names


def test_duplicates_are_deduped_keeping_first_occurrence(tmp_path: Path) -> None:
    repo_models = Path(__file__).resolve().parents[1] / "models"
    cfg = config.load(
        env={},
        extra_model_roots=[repo_models, Path("F:/x"), Path("F:/x")],
        model_paths_file=tmp_path / "model_paths.txt",
    )
    roots = cfg.model_search_roots
    assert roots[0] == repo_models
    assert len([p for p in roots if p.name == "x"]) == 1, "the duplicate path was not removed"
    assert len([p for p in roots if p == repo_models]) == 1, "the repository's models/ was inserted twice"


def test_nonexistent_extra_paths_are_kept_for_the_registry_to_ignore() -> None:
    """Nonexistent directories are not filtered -- "the directory does not exist yet" and "the directory has no models" are the same thing to the caller."""
    cfg = config.load(env={}, extra_model_roots=[Path("Q:/definitely-not-here")])
    assert any(p.name == "definitely-not-here" for p in cfg.model_search_roots)


def test_default_search_roots_are_machine_independent() -> None:
    """The default search roots **must not** contain a path that only exists on one machine.

    Before 2026-09-17 `config.py` hardcoded two paths that existed only on the development machine
    (a webui and a ComfyUI installation). That tied the product code to one machine: another machine just gets two
    `exists: false` noise entries (measured on real Ubuntu), and seeing two entries that never
    exist a user cannot tell whether that is "normal, only that machine has them" or "I
    misconfigured it". Those two now go through `model_paths.txt`.

    Criterion: every entry in the default list must fall **inside the repository** or **inside the
    current user's own cache area**. Third-party directories (webui / ComfyUI / another drive) can
    only be provided by `model_paths.txt` -- that way changing machines needs no code change and
    no environment variable.
    """
    repo = Path(__file__).resolve().parents[1]
    home = Path.home()
    local = Path(os.environ.get("LOCALAPPDATA") or home)
    allowed = (repo, home, local)
    for environ in ({}, dict(os.environ)):
        for root in config.default_model_search_roots(environ=environ):
            assert any(root.is_relative_to(base) for base in allowed), (
                "a machine-specific path %s appeared in the default search roots; it should be provided by model_paths.txt" % root
            )
    assert config.default_model_search_roots(environ={})[0] == repo / "models"
