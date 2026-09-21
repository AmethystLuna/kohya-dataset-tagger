"""§4.12 graphical directory picker + §4.11's loopback gate.

Two invariants:

1. The picker **lists directories only** (no files, no reading contents). It exists for "pick a dataset directory",
   and listing one extra file would mix "browsing" and "reading" together.
2. On a non-loopback address, the picker and the four path-config endpoints are **all 403**, unless
   `--allow-remote-path-config` is explicitly turned on. This one is tied most tightly to the security boundary: disabling only the picker
   while leaving roots changes alone is self-deception (both can cross the allowlist via `add_root` and then read/write any directory).

Every "gate is in effect" case comes with a pre/post assertion that "the same request succeeds on loopback" -
a test that only asserts 403 would also pass for an implementation that rejects every request.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kohya_dataset_tagger import config
from kohya_dataset_tagger.app import create_app
from kohya_dataset_tagger.core import paths


@pytest.fixture(autouse=True)
def _restore_global_config():
    """Faithfully restore the process-level configuration (config is a global singleton that other test files also depend on)."""
    before = config.current()
    roots = paths.get_roots()
    yield
    config._CONFIG = before
    paths.configure(roots)


def _workspace(tmp_path: Path) -> dict:
    """`tree/` is the allowlist root, holding a mix of subdirectories, one file, one hidden directory, and two numeric directories."""
    tree = tmp_path / "tree"
    for name in ("alpha", "beta", "1-num", "10-num", ".hidden"):
        (tree / name).mkdir(parents=True)
    (tree / "note.txt").write_text("x", encoding="utf-8")
    return {
        "tree": tree,
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


def _assert_frozen_error(response, status: int, code: str) -> None:
    assert response.status_code == status, response.text
    body = response.json()
    assert set(body) == {"error"}, body
    # 2026-09-19: the error shape gained params (§4 supplement); the legacy fields are unchanged verbatim.
    assert set(body["error"]) == {"code", "message", "params"}, body
    assert isinstance(body["error"]["params"], dict)
    assert body["error"]["code"] == code, body


# ---------------------------------------------------------------------------
# Directories only
# ---------------------------------------------------------------------------


def test_pick_lists_only_directories(tmp_path) -> None:
    ws = _workspace(tmp_path)
    with _client(ws) as client:
        body = client.get("/api/fs/pick", params={"path": str(ws["tree"])}).json()
        assert set(body) == {"path", "parent", "dirs", "roots", "home"}
        names = [item["name"] for item in body["dirs"]]
        assert names == [".hidden", "1-num", "10-num", "alpha", "beta"]
        assert "note.txt" not in names, "the picker listed a file: it may list directories only"
        for item in body["dirs"]:
            assert set(item) == {"name", "path"}, "the picker returned extra fields (image counts / sizes or the like)"


def test_pick_parent_is_none_at_a_filesystem_root(tmp_path) -> None:
    ws = _workspace(tmp_path)
    with _client(ws) as client:
        reported = client.get("/api/fs/pick", params={"path": str(ws["tree"])}).json()["roots"]
        assert reported, "the picker did not provide any navigable filesystem root"
        top = client.get("/api/fs/pick", params={"path": reported[0]}).json()
        assert top["parent"] is None, "reporting a parent at the filesystem root makes the UI click into a 403"


def test_pick_without_path_starts_at_the_first_root(tmp_path) -> None:
    ws = _workspace(tmp_path)
    with _client(ws) as client:
        body = client.get("/api/fs/pick").json()
        assert Path(body["path"]) == ws["tree"].resolve()


def test_pick_rejects_missing_paths_and_files(tmp_path) -> None:
    ws = _workspace(tmp_path)
    with _client(ws) as client:
        _assert_frozen_error(
            client.get("/api/fs/pick", params={"path": str(ws["tree"] / "nope")}), 404, "not_found"
        )
        _assert_frozen_error(
            client.get("/api/fs/pick", params={"path": str(ws["tree"] / "note.txt")}),
            400, "not_a_directory",
        )


def test_pick_can_browse_outside_the_whitelist(tmp_path) -> None:
    """This is the **entire point** of the picker: adding a root is essentially picking a directory that is still outside the allowlist."""
    ws = _workspace(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "inner").mkdir()
    with _client(ws) as client:
        # the allowlist is unchanged: /api/fs/list still 403s for outside it
        _assert_frozen_error(
            client.get("/api/fs/list", params={"path": str(outside)}), 403, "outside_roots"
        )
        # but the picker can list it - otherwise "graphically switch the dataset" is simply impossible
        body = client.get("/api/fs/pick", params={"path": str(outside)}).json()
        assert [item["name"] for item in body["dirs"]] == ["inner"]


# ---------------------------------------------------------------------------
# Creating a directory (POST /api/fs/pick/mkdir)
# ---------------------------------------------------------------------------
# The user's own words: "I can neither type manually nor create a new folder". Manual typing is pure frontend navigation; creating a directory must write to disk,
# so it is a **write** path: only a single path component is allowed, a same-named directory is idempotent, and a same-named file is never overwritten.


def test_mkdir_creates_one_subdirectory_and_is_idempotent(tmp_path) -> None:
    ws = _workspace(tmp_path)
    with _client(ws) as client:
        created = client.post(
            "/api/fs/pick/mkdir", json={"parent": str(ws["tree"]), "name": "new-set"}
        )
        assert created.status_code == 200, created.text
        body = created.json()
        assert body["created"] is True
        assert Path(body["path"]) == (ws["tree"] / "new-set").resolve()
        assert (ws["tree"] / "new-set").is_dir()

        again = client.post(
            "/api/fs/pick/mkdir", json={"parent": str(ws["tree"]), "name": "new-set"}
        )
        assert again.status_code == 200, again.text
        assert again.json() == {"path": str((ws["tree"] / "new-set").resolve()), "created": False}
        # creates only one level: it is not mkdir -p
        assert list((ws["tree"] / "new-set").iterdir()) == []


def test_mkdir_may_create_outside_the_whitelist(tmp_path) -> None:
    """The same level as the picker: the place where a new directory is created is necessarily still outside the allowlist."""
    ws = _workspace(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    with _client(ws) as client:
        _assert_frozen_error(
            client.get("/api/fs/list", params={"path": str(outside)}), 403, "outside_roots"
        )
        response = client.post(
            "/api/fs/pick/mkdir", json={"parent": str(outside), "name": "brand-new"}
        )
        assert response.status_code == 200, response.text
        assert (outside / "brand-new").is_dir()


@pytest.mark.parametrize("name", ["", ".", "..", "a/b", "a\\b", "trailing.", "trailing "])
def test_mkdir_accepts_only_a_single_path_component(tmp_path, name: str) -> None:
    """A name with a separator = a write path that escapes the parent; it must be blocked here, not caught by resolve."""
    ws = _workspace(tmp_path)
    with _client(ws) as client:
        response = client.post(
            "/api/fs/pick/mkdir", json={"parent": str(ws["tree"]), "name": name}
        )
        _assert_frozen_error(response, 400, "bad_name")


def test_mkdir_never_overwrites_an_existing_file(tmp_path) -> None:
    ws = _workspace(tmp_path)
    before = (ws["tree"] / "note.txt").read_bytes()
    with _client(ws) as client:
        response = client.post(
            "/api/fs/pick/mkdir", json={"parent": str(ws["tree"]), "name": "note.txt"}
        )
        _assert_frozen_error(response, 409, "already_exists")
    assert (ws["tree"] / "note.txt").read_bytes() == before


def test_mkdir_reports_a_missing_or_file_parent(tmp_path) -> None:
    ws = _workspace(tmp_path)
    with _client(ws) as client:
        _assert_frozen_error(
            client.post(
                "/api/fs/pick/mkdir", json={"parent": str(ws["tree"] / "nope"), "name": "x"}
            ),
            404, "not_found",
        )
        _assert_frozen_error(
            client.post(
                "/api/fs/pick/mkdir", json={"parent": str(ws["tree"] / "note.txt"), "name": "x"}
            ),
            400, "not_a_directory",
        )
        _assert_frozen_error(
            client.post("/api/fs/pick/mkdir", json={"parent": "", "name": "x"}),
            400, "bad_path",
        )


# ---------------------------------------------------------------------------
# Loopback gate
# ---------------------------------------------------------------------------


def test_non_loopback_disables_picker_and_all_path_config(tmp_path) -> None:
    ws = _workspace(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    with _client(ws, host="0.0.0.0") as client:
        _assert_frozen_error(
            client.get("/api/fs/pick", params={"path": str(ws["tree"])}),
            403, "remote_path_config_disabled",
        )
        # creating a directory is the only **write** step of the same capability and must be disabled too - disabling only the picker leaves a back door.
        _assert_frozen_error(
            client.post(
                "/api/fs/pick/mkdir", json={"parent": str(ws["tree"]), "name": "should-not-exist"}
            ),
            403, "remote_path_config_disabled",
        )
        assert not (ws["tree"] / "should-not-exist").exists(), "a rejected request still created the directory"
        _assert_frozen_error(
            client.post("/api/roots", json={"path": str(other)}),
            403, "remote_path_config_disabled",
        )
        _assert_frozen_error(
            client.delete("/api/roots", params={"path": str(ws["tree"])}),
            403, "remote_path_config_disabled",
        )
        _assert_frozen_error(
            client.post("/api/autotag/model-roots", json={"path": str(other)}),
            403, "remote_path_config_disabled",
        )
        _assert_frozen_error(
            client.delete("/api/autotag/model-roots", params={"path": str(other)}),
            403, "remote_path_config_disabled",
        )
        # the health probe reports truthfully, and the frontend greys out the buttons and explains why based on it
        health = client.get("/api/health").json()
        assert health["path_config"] == {
            "allowed": False, "host": "0.0.0.0", "reason": "host_not_loopback",
        }
        assert len(client.get("/api/roots").json()["roots"]) == 1, "a rejected request must not modify the allowlist"


def test_explicit_flag_reopens_path_config_on_non_loopback(tmp_path) -> None:
    """Backstop: `--allow-remote-path-config` is the only opt-in switch, and it is off by default."""
    ws = _workspace(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    with _client(ws, host="0.0.0.0", allow_remote_path_config=True) as client:
        assert client.get("/api/fs/pick", params={"path": str(ws["tree"])}).status_code == 200
        assert client.post(
            "/api/fs/pick/mkdir", json={"parent": str(ws["tree"]), "name": "allowed"}
        ).status_code == 200
        assert client.post("/api/roots", json={"path": str(other)}).status_code == 200
        assert client.get("/api/health").json()["path_config"]["allowed"] is True


def test_loopback_binding_keeps_path_config_enabled(tmp_path) -> None:
    ws = _workspace(tmp_path)
    with _client(ws, host="127.0.0.1") as client:
        assert client.get("/api/fs/pick", params={"path": str(ws["tree"])}).status_code == 200
        assert client.get("/api/health").json()["path_config"]["allowed"] is True


@pytest.mark.parametrize(
    "host,expected",
    [
        ("127.0.0.1", True),
        ("localhost", True),
        ("LOCALHOST", True),
        ("::1", True),
        ("[::1]", True),
        ("::ffff:127.0.0.1", True),
        ("0.0.0.0", False),
        ("203.0.113.7", False),   # TEST-NET-3 (RFC 5737): any non-loopback address has to be refused
        ("::", False),
        ("", False),
        ("example.com", False),
    ],
)
def test_is_loopback_host(host: str, expected: bool) -> None:
    """Anything unrecognized counts as **non**-local (fail closed): a false allow is far more dangerous than a false reject."""
    assert config.is_loopback_host(host) is expected


def test_env_var_can_enable_remote_path_config(tmp_path) -> None:
    ws = _workspace(tmp_path)
    cfg = config.load(
        env={"KOHYA_TAGGER_ALLOW_REMOTE_PATH_CONFIG": "1"},
        roots=[ws["tree"]],
        thumb_cache_dir=tmp_path / "thumbs",
        model_search_roots=[],
        roots_file=ws["roots_file"],
        model_paths_file=ws["model_paths_file"],
        host="0.0.0.0",
    )
    assert cfg.allow_remote_path_config is True
    assert config.path_config_allowed() is True
