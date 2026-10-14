"""§4.18 opening a directory in the operating system's file manager.

The whole point of this endpoint is a **side effect outside the process**, so the criteria are built
around one question: what exactly does it start, and does it start anything at all when it should not?

Every test replaces `reveal.open_in_file_manager` - no window may open during a test run - and the
negative cases assert the recorder is **empty**, because "403" alone would also pass for an
implementation that refused the request *after* launching the file manager.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kohya_dataset_tagger import config
from kohya_dataset_tagger.api import reveal
from kohya_dataset_tagger.app import create_app
from kohya_dataset_tagger.core import paths

REVEAL = Path(reveal.__file__)


@pytest.fixture(autouse=True)
def _restore_global_config():
    """`config` is a process-level singleton and other test files depend on it - restore it exactly."""
    before = config.current()
    roots = paths.get_roots()
    yield
    config._CONFIG = before
    paths.configure(roots)


@pytest.fixture()
def launched(monkeypatch) -> list:
    """Replace the launcher and record what it was handed. Nothing is really started."""
    calls: list = []

    def fake(target: Path) -> str:
        calls.append(Path(target))
        return "fake-launcher"

    monkeypatch.setattr(reveal, "open_in_file_manager", fake)
    return calls


def _workspace(tmp_path: Path) -> dict:
    tree = tmp_path / "tree"
    (tree / "alpha").mkdir(parents=True)
    (tree / "note.txt").write_text("x", encoding="utf-8")
    return {
        "tree": tree,
        "outside": tmp_path / "outside",
        "roots_file": tmp_path / "roots.txt",
        "model_paths_file": tmp_path / "model_paths.txt",
    }


def _client(workspace, **kwargs) -> TestClient:
    config.load(
        roots=[workspace["tree"]],
        thumb_cache_dir=workspace["tree"].parent / "thumbs",
        model_search_roots=[],
        roots_file=workspace["roots_file"],
        model_paths_file=workspace["model_paths_file"],
        **kwargs,
    )
    return TestClient(create_app())


def _assert_frozen_error(response, status: int, code: str) -> dict:
    assert response.status_code == status, response.text
    body = response.json()
    assert set(body) == {"error"}, body
    assert set(body["error"]) == {"code", "message", "params"}, body
    assert body["error"]["code"] == code, body
    return body["error"]


# ---------------------------------------------------------------------------
# The normal path
# ---------------------------------------------------------------------------


def test_reveal_opens_the_resolved_directory(tmp_path, launched) -> None:
    ws = _workspace(tmp_path)
    target = ws["tree"] / "alpha"
    with _client(ws) as client:
        response = client.post("/api/fs/reveal", json={"path": str(target)})
        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body) == {"path", "launcher"}, body
        assert Path(body["path"]) == target.resolve()
        assert body["launcher"] == "fake-launcher"
    assert launched == [target.resolve()], "the launcher was handed %s" % launched


def test_reveal_without_a_path_opens_the_first_root(tmp_path, launched) -> None:
    """The empty path is the same fallback every other path endpoint uses (§4), not an error."""
    ws = _workspace(tmp_path)
    with _client(ws) as client:
        response = client.post("/api/fs/reveal", json={})
        assert response.status_code == 200, response.text
    assert launched == [ws["tree"].resolve()]


# ---------------------------------------------------------------------------
# Refusals: nothing is launched
# ---------------------------------------------------------------------------


def test_reveal_refuses_outside_the_allowlist_without_launching(tmp_path, launched) -> None:
    """The allowlist is the door (§4.18). A refused request must not open a window behind the 403."""
    ws = _workspace(tmp_path)
    ws["outside"].mkdir()
    with _client(ws) as client:
        error = _assert_frozen_error(
            client.post("/api/fs/reveal", json={"path": str(ws["outside"])}), 403, "outside_roots"
        )
    assert error["params"].get("path"), "an out-of-bounds refusal carries no path"
    assert launched == [], "a refused path still started the file manager: %s" % launched


@pytest.mark.parametrize("relative", ["../..", "note.txt"])
def test_reveal_refuses_an_escape_and_a_file(tmp_path, relative: str, launched) -> None:
    ws = _workspace(tmp_path)
    with _client(ws) as client:
        response = client.post("/api/fs/reveal", json={"path": str(ws["tree"] / relative)})
        if relative == "note.txt":
            _assert_frozen_error(response, 400, "not_a_directory")
        else:
            _assert_frozen_error(response, 403, "outside_roots")
    assert launched == [], "a refused path still started the file manager: %s" % launched


def test_reveal_reports_a_missing_directory(tmp_path, launched) -> None:
    ws = _workspace(tmp_path)
    with _client(ws) as client:
        _assert_frozen_error(
            client.post("/api/fs/reveal", json={"path": str(ws["tree"] / "nope")}), 404, "not_found"
        )
    assert launched == [], "a missing path still started the file manager: %s" % launched


def test_reveal_reports_a_machine_without_a_launcher(tmp_path, monkeypatch) -> None:
    """A headless server answers with a reason. A silent 200 would be a button that does nothing."""
    ws = _workspace(tmp_path)

    def unavailable(target: Path) -> str:
        raise reveal.FileManagerUnavailable("no file manager launcher on this system (xdg-open)")

    monkeypatch.setattr(reveal, "open_in_file_manager", unavailable)
    with _client(ws) as client:
        error = _assert_frozen_error(
            client.post("/api/fs/reveal", json={"path": str(ws["tree"])}), 500, "no_file_manager"
        )
    assert Path(error["params"]["path"]) == ws["tree"].resolve(), error["params"]
    assert "xdg-open" in error["message"]


# ---------------------------------------------------------------------------
# The launcher itself
# ---------------------------------------------------------------------------


def test_launcher_argv_names_this_platforms_file_manager() -> None:
    target = Path("/tmp/example")
    argv = reveal.launcher_argv(target)
    assert argv[-1] == str(target), "the directory is not the last argument: %s" % argv
    if os.name == "nt":
        assert argv[0] == "explorer.exe", argv
    elif sys.platform == "darwin":
        assert argv[0] == "open", argv
    else:
        assert argv[0] == "xdg-open", argv


def test_the_launcher_is_fire_and_forget_and_never_through_a_shell() -> None:
    """`explorer.exe` exits 1 even when it worked, so a return code is not a success signal.

    Read from the source rather than measured: spawning a real file manager in a test is exactly
    what this suite must never do. What it pins is the three properties that make the call
    fire-and-forget - `Popen` (not `run`/`check_call`), discarded stdio, and no shell.
    """
    body = REVEAL.read_text(encoding="utf-8")
    assert "subprocess.Popen(" in body, "the launcher is no longer fire-and-forget"
    assert not re.search(r"subprocess\.(run|call|check_call|check_output)\(", body), (
        "the launcher waits for the process (or reads its output), which a GUI launcher does not answer"
    )
    assert "DEVNULL" in body, "the launcher's stdio is not discarded: it can block the server or fill its log"
    assert "shell=True" not in body, "the launcher goes through a shell, so the path is re-parsed as a command"
