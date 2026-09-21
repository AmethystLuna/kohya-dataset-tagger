"""HTTP contract tests for the read-only dataset.toml status endpoint (p0-spec §4.16).

Why this file exists
--------------------
The readiness strip's third segment answers "is this dataset trainable?", and the trainer reads
`dataset.toml` next to the **dataset root**. Until §4.16 nothing could tell the app whether that file
exists: `POST /api/dataset/toml` is pure computation (§4.9) and never reads it. So the strip could only
say "not checked yet" - which is honest and useless.

What the criteria have to pin down, in the order the risk is real:

1. **The subject is the dataset root.** A nested subdirectory must not answer for the dataset it
   belongs to, and the file's name is defined in exactly one place (`DATASET_TOML_NAME`).
2. **Read-only.** One `stat`, nothing else: asking about a root that has no dataset.toml does not
   create one, and no byte, mtime or extra file of the directory changes. A criterion that only
   asserted the response would pass just as happily against an implementation that touches the file.
3. **No claim when it cannot answer.** A stat that fails is 500 `stat_failed`, never "no dataset.toml";
   `Path.is_file()` would have swallowed exactly that error into a false "not there".
4. The allowlist still decides: out of bounds is 403, a missing root 404, a file as root 400.
"""
from __future__ import annotations

import os
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kohya_dataset_tagger import config
from kohya_dataset_tagger.core import dataset_toml

ROUTE = "/api/dataset/toml/status"


@pytest.fixture(autouse=True)
def _neutral_state():
    yield
    config.load(env={}, roots=[])


@pytest.fixture
def env(tmp_path: Path):
    """A miniature dataset root (one post directory) plus a sandbox sibling for the subject tests."""
    dataset = tmp_path / "dataset"
    (dataset / "1_post").mkdir(parents=True)
    (dataset / "1_post" / "1.txt").write_text("1girl", encoding="utf-8")
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()

    config.load(
        env={},
        roots=[dataset, sandbox],
        thumb_cache_dir=tmp_path / "thumbs",
        model_search_roots=[],
    )
    from kohya_dataset_tagger import app as app_module

    with TestClient(app_module.create_app()) as client:
        yield types.SimpleNamespace(client=client, dataset=dataset, sandbox=sandbox, tmp=tmp_path)


def _fingerprint(root: Path) -> dict:
    """Every entry under `root`, by relative path: size + mtime_ns.

    The read-only criterion is only meaningful as a before/after comparison of the directory itself -
    the response cannot tell "was the file created by the question?".
    """
    found = {}
    for path in sorted(root.rglob("*")):
        info = path.stat()
        found[path.relative_to(root).as_posix()] = (info.st_size, info.st_mtime_ns)
    return found


def _status(env, root) -> dict:
    resp = env.client.get(ROUTE, params={"root": str(root)})
    assert resp.status_code == 200, resp.text[:300]
    return resp.json()


# ---------------------------------------------------------------------------
# 1. The fact
# ---------------------------------------------------------------------------


def test_a_root_without_the_file_says_so(env) -> None:
    """The state that was unreachable before: "this dataset has no dataset.toml" as a fact, with the
    path that was looked at (so a caller can see where the file is expected)."""
    body = _status(env, env.dataset)
    assert body["exists"] is False
    assert Path(body["path"]) == env.dataset.resolve() / dataset_toml.DATASET_TOML_NAME
    assert Path(body["root"]) == env.dataset.resolve()
    assert body["mtime"] is None and body["size"] is None


def test_the_file_appearing_flips_the_answer(env) -> None:
    """Normal path: write the file, ask again - existence, mtime and size must be the real stat, not
    a copy of the request. mtime/size are what makes this provably a read of the disk."""
    target = env.dataset / dataset_toml.DATASET_TOML_NAME
    target.write_bytes(b"[datasets]\nbatch_size = 8\n")
    info = target.stat()

    body = _status(env, env.dataset)
    assert body["exists"] is True
    assert body["size"] == info.st_size
    assert body["mtime"] == pytest.approx(info.st_mtime, abs=1e-6)


def test_the_subject_is_the_root_it_was_asked_about(env) -> None:
    """"The subject is the dataset root, not the directory on screen" is a property of this endpoint
    too: a subdirectory's answer is about the subdirectory's own file. The frontend is what asks about
    `state.root`; here we pin that the endpoint keeps the two apart instead of searching upwards."""
    (env.dataset / dataset_toml.DATASET_TOML_NAME).write_text("[datasets]\n", encoding="utf-8")
    nested = env.dataset / "1_post"

    nested_body = _status(env, nested)
    assert Path(nested_body["root"]) == nested.resolve()
    assert Path(nested_body["path"]) == nested.resolve() / dataset_toml.DATASET_TOML_NAME
    assert nested_body["exists"] is False, "a subdirectory answered with the dataset's file"

    (nested / dataset_toml.DATASET_TOML_NAME).write_text("[datasets]\n", encoding="utf-8")
    assert _status(env, nested)["exists"] is True
    assert _status(env, env.dataset)["exists"] is True, "the root's answer changed with a subdirectory's file"


def test_an_empty_root_falls_back_to_the_first_allowlist_root(env) -> None:
    """The /api/fs/list convention; without it the endpoint is unusable before a directory is picked."""
    body = env.client.get(ROUTE).json()
    assert Path(body["root"]) == env.dataset.resolve()


def test_a_directory_named_dataset_toml_is_not_a_file(env) -> None:
    """`exists` means "a regular file is there" (stat.S_ISREG), which is what the trainer's open()
    needs. A directory of that name is not a dataset it can read, so the honest answer is no."""
    (env.dataset / dataset_toml.DATASET_TOML_NAME).mkdir()
    assert _status(env, env.dataset)["exists"] is False


# ---------------------------------------------------------------------------
# 2. Read-only
# ---------------------------------------------------------------------------


def test_asking_never_writes_creates_or_touches_anything(env) -> None:
    """THE criterion of this round: the answer may not cost the dataset a single byte.

    Both states are asked (no file yet, then the file present), plus a subdirectory, and the whole
    tree is compared by (size, mtime_ns) before and after. Creating the file it was asked about is the
    specific trap: it would make the endpoint report exists:true from then on.
    """
    before = _fingerprint(env.dataset)
    assert _status(env, env.dataset)["exists"] is False
    assert _status(env, env.dataset / "1_post")["exists"] is False
    assert _fingerprint(env.dataset) == before, "asking about a missing dataset.toml changed the directory"

    target = env.dataset / dataset_toml.DATASET_TOML_NAME
    target.write_bytes(b"# hand tuned\n")
    present = _fingerprint(env.dataset)
    for _ in range(3):
        assert _status(env, env.dataset)["exists"] is True
    assert _fingerprint(env.dataset) == present, "the status endpoint touched the file it reported"


def test_no_backup_or_temp_file_is_left_behind(env) -> None:
    """A backup would be a write with side effects; a .bak next to the file is exactly what a "safe"
    implementation might add, and it would be wrong here - nothing is being replaced."""
    (env.dataset / dataset_toml.DATASET_TOML_NAME).write_text("[datasets]\n", encoding="utf-8")
    _status(env, env.dataset)
    names = {path.name for path in env.dataset.iterdir()}
    assert names == {"1_post", dataset_toml.DATASET_TOML_NAME}, names


def test_a_failed_stat_is_not_reported_as_a_missing_file(env, monkeypatch) -> None:
    """Failure path: Path.is_file() returns False on OSError, which would turn "the root cannot be
    read" into "no dataset.toml". The explicit stat makes it a 500 instead - the endpoint must not
    claim a file state it could not observe. (Recovery is the next call: no state is kept.)
    """
    target = env.dataset / dataset_toml.DATASET_TOML_NAME
    target.write_text("[datasets]\n", encoding="utf-8")
    real_stat = Path.stat

    def deny(self, *args, **kwargs):
        if self.name == dataset_toml.DATASET_TOML_NAME:
            raise PermissionError(13, "Permission denied")
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", deny)
    resp = env.client.get(ROUTE, params={"root": str(env.dataset)})
    assert resp.status_code == 500, resp.text[:200]
    assert resp.json()["error"]["code"] == "stat_failed"

    monkeypatch.undo()
    assert _status(env, env.dataset)["exists"] is True, "the failed request left the endpoint stuck"


# ---------------------------------------------------------------------------
# 3. The allowlist and the error codes
# ---------------------------------------------------------------------------


def test_out_of_bounds_roots_are_403(env) -> None:
    """Every path parameter goes through resolve_under_roots (§4). The probe uses the platform's own
    idea of "outside" so it cannot pass by accident on a machine without a C: drive."""
    outside = "C:" + os.sep + "Windows" if os.name == "nt" else "/etc"
    for bad in (outside, str(env.dataset / ".." / "..")):
        resp = env.client.get(ROUTE, params={"root": bad})
        assert resp.status_code == 403, "%s -> %s %s" % (bad, resp.status_code, resp.text[:200])
        assert resp.json()["error"]["code"] in ("outside_roots", "bad_path"), resp.text[:200]


def test_missing_root_is_404_and_a_file_root_is_400(env) -> None:
    loose = env.sandbox / "loose.txt"
    loose.write_text("x", encoding="utf-8")
    missing = env.client.get(ROUTE, params={"root": str(env.sandbox / "nope")})
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "not_found"
    not_dir = env.client.get(ROUTE, params={"root": str(loose)})
    assert not_dir.status_code == 400 and not_dir.json()["error"]["code"] == "not_a_directory"


def test_an_unconfigured_allowlist_is_403_and_not_a_500() -> None:
    """The fail-closed backstop: with no roots at all the endpoint denies, and it must not invent one."""
    from kohya_dataset_tagger.core import paths

    saved = paths.get_roots()
    try:
        paths.configure([])
        config.load(env={}, roots=[])
        from kohya_dataset_tagger import app as app_module

        with TestClient(app_module.create_app()) as client:
            resp = client.get(ROUTE)
        assert resp.status_code == 403, resp.text[:200]
        assert resp.json()["error"]["code"] in ("paths_unconfigured", "outside_roots")
    finally:
        paths.configure(saved)


# ---------------------------------------------------------------------------
# 4. One definition of the file's name
# ---------------------------------------------------------------------------


def test_the_file_name_is_defined_once_in_the_whole_backend() -> None:
    """The export writes the file, the status endpoint reports it and the frontend downloads it under
    the same name. Two definitions would let them describe two different files without anything
    noticing, so the literal may only appear as the constant's own value."""
    src = Path(__file__).resolve().parents[1] / "src" / "kohya_dataset_tagger"
    definitions = []
    literals = []
    for path in sorted(src.rglob("*.py")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if "DATASET_TOML_NAME = " in line:
                definitions.append("%s:%d" % (path.name, number))
            if '"dataset.toml"' in line or "'dataset.toml'" in line:
                literals.append("%s:%d: %s" % (path.name, number, line.strip()))
    assert len(definitions) == 1, "the file's name is defined %d times: %s" % (len(definitions), definitions)
    assert len(literals) == 1, (
        "dataset.toml is spelled out as a literal somewhere other than the constant: %s" % literals
    )
    scale = (src / "api" / "scale.py").read_text(encoding="utf-8")
    assert "dataset_toml.DATASET_TOML_NAME" in scale, "the export does not write the constant's file name"
