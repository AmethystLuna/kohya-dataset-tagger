"""§4.11 runtime-editable path configuration: allowlist roots + tagger model scan directories.

Why this set of criteria exists (user-reported in practice)
-----------------------------------------------------------
Roots used to come only from `--roots` / `KOHYA_TAGGER_ROOTS` / `roots.txt`, and model search directories only from
`--models` / `--extra-models` / `model_paths.txt`, and they were **read only once at startup**.
So "switch datasets" and "add a model directory" both required restarting the service.

What is asserted here is that **changes take effect at runtime**, not that "the endpoint returns 200" - every mutation comes with a
pre-condition assertion of "what it was before". A test that only asserts 200 would also pass for an implementation that "only takes effect after a restart".

Both persistent files are pointed at tmp_path: the default location is the repository root, and changing the user's `roots.txt`
by running the tests once is the easiest trap in this design.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kohya_dataset_tagger import config
from kohya_dataset_tagger.app import create_app
from kohya_dataset_tagger.core import paths

MODEL_ID = "wd-vit-tagger-v3"


@pytest.fixture(autouse=True)
def _restore_global_config():
    """Faithfully restore the process-level configuration.

    It **puts back the same Config object** directly rather than rebuilding via `config.load()`: rebuilding would
    fold the explicit / pinned / file search-root groups into one, and later cases would no longer see the original configuration.
    """
    before = config.current()
    roots = paths.get_roots()
    yield
    config._CONFIG = before
    paths.configure(roots)


def _make_model_dir(root: Path, model_id: str = MODEL_ID) -> Path:
    """A fake model in the flat layout: `<root>/<model_id>/model.onnx` + a `*.csv` in the same directory."""
    target = root / model_id
    target.mkdir(parents=True, exist_ok=True)
    (target / "model.onnx").write_bytes(b"not really an onnx")
    (target / "selected_tags.csv").write_text("tag_id,name,category\n", encoding="utf-8")
    return root


@pytest.fixture
def workspace(tmp_path: Path):
    """A is the only root and B is the directory to add; each gets one image so `/api/fs/list` is directly judgeable."""
    a = tmp_path / "A"
    b = tmp_path / "B"
    for directory in (a, b):
        directory.mkdir()
        (directory / "1.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    files = {
        "roots_file": tmp_path / "roots.txt",
        "model_paths_file": tmp_path / "model_paths.txt",
    }
    return {"a": a, "b": b, "tmp": tmp_path, **files}


def _client(workspace, *, model_search_roots=(), extra_model_roots=None) -> TestClient:
    config.load(
        roots=[workspace["a"]],
        thumb_cache_dir=workspace["tmp"] / "thumbs",
        model_search_roots=list(model_search_roots),
        extra_model_roots=extra_model_roots,
        roots_file=workspace["roots_file"],
        model_paths_file=workspace["model_paths_file"],
    )
    return TestClient(create_app())


def _assert_frozen_error(response, status: int, code: str) -> None:
    assert response.status_code == status, response.text
    error = response.json()["error"]
    assert set(error) == {"code", "message", "params"}
    assert error["code"] == code
    assert isinstance(error["message"], str)
    assert isinstance(error["params"], dict)
    assert response.json()["error"]["code"] == code


# ---------------------------------------------------------------------------
# GET /api/roots - shape and the three marker flags
# ---------------------------------------------------------------------------


def test_get_roots_reports_exists_and_persisted(workspace) -> None:
    with _client(workspace) as client:
        body = client.get("/api/roots").json()
        assert set(body) == {"roots"}
        assert len(body["roots"]) == 1
        entry = body["roots"][0]
        # the shape **adds fields** to §4's original table {path,name} (the old frontend reads only the first two)
        assert set(entry) == {"path", "name", "exists", "is_dir", "persisted"}
        assert Path(entry["path"]) == workspace["a"].resolve()
        assert entry["name"] == "A"
        assert entry["exists"] is True and entry["is_dir"] is True
        assert entry["persisted"] is False, "roots.txt has not been written yet, so it must not claim to be remembered"


def test_get_roots_marks_missing_directories(workspace) -> None:
    """The service is still alive after a root directory is deleted, and the UI must be able to see that this entry is gone."""
    with _client(workspace) as client:
        (workspace["a"] / "1.png").unlink()
        workspace["a"].rmdir()
        entry = client.get("/api/roots").json()["roots"][0]
        assert entry["exists"] is False and entry["is_dir"] is False


# ---------------------------------------------------------------------------
# POST / DELETE /api/roots - effective at runtime
# ---------------------------------------------------------------------------


def test_post_root_takes_effect_without_restart(workspace) -> None:
    with _client(workspace) as client:
        before = client.get("/api/fs/list", params={"path": str(workspace["b"])})
        _assert_frozen_error(before, 403, "outside_roots")

        response = client.post("/api/roots", json={"path": str(workspace["b"])})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["changed"] is True and body["persisted"] is True
        assert [Path(item["path"]) for item in body["roots"]] == [
            workspace["a"].resolve(), workspace["b"].resolve(),
        ]

        # the same process, no restart: this is the whole point of this criterion
        after = client.get("/api/fs/list", params={"path": str(workspace["b"])})
        assert after.status_code == 200, after.text
        assert after.json()["counts"]["images"] == 1
        assert workspace["roots_file"].read_text(encoding="utf-8").splitlines() == [
            str(workspace["a"].resolve()), str(workspace["b"].resolve()),
        ]


def test_post_root_is_idempotent_and_does_not_rewrite_the_file(workspace) -> None:
    with _client(workspace) as client:
        client.post("/api/roots", json={"path": str(workspace["b"])})
        stamp = workspace["roots_file"].stat().st_mtime_ns
        again = client.post("/api/roots", json={"path": str(workspace["b"])})
        assert again.status_code == 200
        assert again.json()["changed"] is False
        assert workspace["roots_file"].stat().st_mtime_ns == stamp


def test_post_root_normalizes_equivalent_paths(workspace) -> None:
    """`A/../B` and `B` are the same path and must not become two roots."""
    with _client(workspace) as client:
        odd = workspace["tmp"] / "A" / ".." / "B"
        assert client.post("/api/roots", json={"path": str(odd)}).status_code == 200
        assert client.post("/api/roots", json={"path": str(workspace["b"])}).json()["changed"] is False
        assert len(client.get("/api/roots").json()["roots"]) == 2


def test_post_root_rejects_missing_directory_and_files(workspace) -> None:
    with _client(workspace) as client:
        missing = workspace["tmp"] / "does-not-exist"
        _assert_frozen_error(client.post("/api/roots", json={"path": str(missing)}), 400, "not_a_directory")
        _assert_frozen_error(client.post("/api/roots", json={"path": ""}), 400, "bad_request")
        _assert_frozen_error(
            client.post("/api/roots", json={"path": str(workspace["a"] / "1.png")}), 400, "not_a_directory"
        )
        assert len(client.get("/api/roots").json()["roots"]) == 1, "a rejected request must not modify the allowlist"


def test_delete_root_takes_effect_and_refuses_to_empty_the_list(workspace) -> None:
    with _client(workspace) as client:
        client.post("/api/roots", json={"path": str(workspace["b"])})
        response = client.delete("/api/roots", params={"path": str(workspace["b"])})
        assert response.status_code == 200, response.text
        assert response.json()["persisted"] is True
        _assert_frozen_error(
            client.get("/api/fs/list", params={"path": str(workspace["b"])}), 403, "outside_roots"
        )
        assert workspace["roots_file"].read_text(encoding="utf-8").splitlines() == [str(workspace["a"].resolve())]

        # the last one may not be deleted: emptying it makes every /api/fs/* 403, the hardest symptom to diagnose
        last = client.delete("/api/roots", params={"path": str(workspace["a"])})
        _assert_frozen_error(last, 400, "last_root")
        assert len(client.get("/api/roots").json()["roots"]) == 1
        assert client.get("/api/fs/list").status_code == 200


def test_delete_unknown_root_is_404(workspace) -> None:
    with _client(workspace) as client:
        _assert_frozen_error(
            client.delete("/api/roots", params={"path": str(workspace["b"])}), 404, "not_found"
        )


def test_roots_file_is_rewritten_wholesale(workspace) -> None:
    """`roots.txt` is a machine-managed state file: its content must **equal** the allowlist, with no stale lines."""
    workspace["roots_file"].write_text("D:\\stale\\path\n", encoding="utf-8")
    with _client(workspace) as client:
        client.post("/api/roots", json={"path": str(workspace["b"])})
        lines = workspace["roots_file"].read_text(encoding="utf-8").splitlines()
        assert lines == [str(workspace["a"].resolve()), str(workspace["b"].resolve())]
        assert not any("stale" in line for line in lines)
        entries = client.get("/api/roots").json()["roots"]
        assert [item["persisted"] for item in entries] == [True, True]


def test_persist_failure_is_reported_not_swallowed(workspace) -> None:
    """When the file cannot be written the feature still works, but it must **say so** - quietly lasting only until the next restart is the worst."""
    workspace["roots_file"] = workspace["tmp"] / "no-such-dir" / "roots.txt"
    with _client(workspace) as client:
        body = client.post("/api/roots", json={"path": str(workspace["b"])}).json()
        assert body["changed"] is True
        assert body["persisted"] is False
        assert body["warning"] and "roots.txt" in body["warning"]
        assert client.get("/api/fs/list", params={"path": str(workspace["b"])}).status_code == 200


# ---------------------------------------------------------------------------
# /api/autotag/model-roots
# ---------------------------------------------------------------------------


def test_model_roots_report_source_and_what_they_contain(workspace) -> None:
    file_root = workspace["tmp"] / "from-file"
    cli_root = workspace["tmp"] / "from-cli"
    file_root.mkdir()
    cli_root.mkdir()
    workspace["model_paths_file"].write_text(
        "# note\n%s\n" % file_root, encoding="utf-8"
    )
    with _client(workspace, extra_model_roots=[str(cli_root)]) as client:
        roots = client.get("/api/autotag/model-roots").json()["roots"]
        assert [(Path(item["path"]), item["source"]) for item in roots] == [
            (file_root, "file"),
            (cli_root, "cli"),
        ]
        for item in roots:
            assert set(item) == {
                "path", "source", "exists", "is_dir", "file_backed", "models", "model_count",
            }
        assert roots[0]["file_backed"] is True and roots[1]["file_backed"] is False


def test_adding_a_model_root_makes_the_model_visible_without_restart(workspace) -> None:
    models_root = _make_model_dir(workspace["tmp"] / "models-extra")
    with _client(workspace) as client:
        before = {item["id"]: item for item in client.get("/api/autotag/models").json()["models"]}
        assert before[MODEL_ID]["installed"] is False

        response = client.post("/api/autotag/model-roots", json={"path": str(models_root)})
        assert response.status_code == 200, response.text
        assert response.json()["changed"] is True

        after = {item["id"]: item for item in client.get("/api/autotag/models").json()["models"]}
        assert after[MODEL_ID]["installed"] is True, "the newly added directory was not used immediately"
        assert Path(after[MODEL_ID]["onnx_path"]).parent.parent == models_root.resolve()

        entry = client.get("/api/autotag/model-roots").json()["roots"][0]
        assert entry["models"] == [MODEL_ID] and entry["model_count"] == 1


def test_removing_a_file_backed_model_root_persists(workspace) -> None:
    models_root = _make_model_dir(workspace["tmp"] / "models-extra")
    workspace["model_paths_file"].write_text(
        "# the top comment must not be eaten\n\n%s\n" % models_root, encoding="utf-8"
    )
    with _client(workspace) as client:
        response = client.delete("/api/autotag/model-roots", params={"path": str(models_root)})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["removed_scope"] == "file" and body["persisted"] is True
        assert body["roots"] == []
        assert client.get("/api/autotag/models").json()["models"][3]["installed"] is False
        assert workspace["model_paths_file"].read_text(encoding="utf-8") == (
            "# the top comment must not be eaten\n\n"
        ), "Comments must be preserved verbatim (a whole-file rewrite would eat it)"


def test_removing_a_non_file_model_root_only_lasts_this_run(workspace) -> None:
    """A non-`file` root can be deleted, but it **only lasts this run**, and that must be stated truthfully."""
    auto = workspace["tmp"] / "auto-root"
    auto.mkdir()
    with _client(workspace, extra_model_roots=[str(auto)]) as client:
        root = client.get("/api/autotag/model-roots").json()["roots"][0]
        assert root["source"] == "cli" and root["file_backed"] is False
        body = client.delete("/api/autotag/model-roots", params={"path": str(auto)}).json()
        assert body["removed_scope"] == "session" and body["persisted"] is False
        assert body["warning"]
        assert client.get("/api/autotag/model-roots").json()["roots"] == []


def test_readding_a_removed_model_root_clears_the_exclusion(workspace) -> None:
    """Deleting and adding back verbatim must really bring it back.

    Invariant: deleting records the path in `excluded`, so adding back must first **remove** that -
    otherwise the "deleted" marker would filter the just-added root again while the endpoint reports success.
    """
    auto = workspace["tmp"] / "auto-root"
    auto.mkdir()
    with _client(workspace, extra_model_roots=[str(auto)]) as client:
        assert client.delete("/api/autotag/model-roots", params={"path": str(auto)}).status_code == 200
        assert client.get("/api/autotag/model-roots").json()["roots"] == []
        assert client.post("/api/autotag/model-roots", json={"path": str(auto)}).status_code == 200
        # after adding back it is a file source (it went into model_paths.txt), no longer cli
        assert [item["source"] for item in client.get("/api/autotag/model-roots").json()["roots"]] == ["file"]


def test_model_root_add_is_idempotent_and_rejects_missing(workspace) -> None:
    models_root = _make_model_dir(workspace["tmp"] / "models-extra")
    with _client(workspace) as client:
        assert client.post("/api/autotag/model-roots", json={"path": str(models_root)}).json()["changed"] is True
        again = client.post("/api/autotag/model-roots", json={"path": str(models_root)}).json()
        assert again["changed"] is False and again["persisted"] is True
        _assert_frozen_error(
            client.post("/api/autotag/model-roots", json={"path": str(workspace["tmp"] / "nope")}),
            400,
            "not_a_directory",
        )
        _assert_frozen_error(
            client.delete("/api/autotag/model-roots", params={"path": str(workspace["tmp"] / "nope")}),
            404,
            "not_found",
        )


def test_model_paths_comment_block_survives_add_and_remove(workspace) -> None:
    """The core of A29: that is a user-authored file, and the comment and semicolon lines are theirs."""
    keep = workspace["tmp"] / "keep-me"
    keep.mkdir()
    added = workspace["tmp"] / "added"
    added.mkdir()
    # Must write bytes: in text mode write_text translates "\n" to os.linesep,
    # so "\r\n" becomes "\r\r\n" - and then it is not testing CRLF preservation anymore (this file fell into this trap once)
    workspace["model_paths_file"].write_bytes(
        ("# a note line\r\n#\r\n%s;%s\r\n" % (keep, added)).encode("utf-8")
    )
    with _client(workspace) as client:
        bodies = [item["path"] for item in client.get("/api/autotag/model-roots").json()["roots"]]
        assert bodies == [str(keep), str(added)]
        client.delete("/api/autotag/model-roots", params={"path": str(added)})
    text = workspace["model_paths_file"].read_bytes().decode("utf-8")
    assert text == "# a note line\r\n#\r\n%s\r\n" % keep, (
        "Only the matching segment should be removed: comments, blank lines, the other segment on the line, and CRLF must all be preserved verbatim"
    )


# ---------------------------------------------------------------------------
# Configuration-layer invariants
# ---------------------------------------------------------------------------


def test_rebuild_is_idempotent_and_matches_load(workspace) -> None:
    """The startup path and runtime add/remove must go through the same code, otherwise the two algorithms drift."""
    extra = workspace["tmp"] / "extra"
    extra.mkdir()
    config.load(
        roots=[workspace["a"]],
        thumb_cache_dir=workspace["tmp"] / "thumbs",
        extra_model_roots=[str(extra)],
        roots_file=workspace["roots_file"],
        model_paths_file=workspace["model_paths_file"],
    )
    current = config.current()
    assert config.rebuild_model_roots(current) == current.model_search_roots
    assert config.rebuild_model_roots(current) == config.rebuild_model_roots(current)


def test_norm_key_follows_the_platform_case_rules(workspace) -> None:
    """`norm_key` uses `os.path.normcase`: it folds case on Windows and does **not** on POSIX.

    On POSIX `/data/A` and `/data/a` are two genuinely different directories - treating them as one is the mistake.
    (A difference observed on real Ubuntu on 2026-09-17.)
    """
    same = config.norm_key(str(workspace["a"]))
    assert same == config.norm_key(str(workspace["a"]))
    upper = config.norm_key(str(workspace["a"]).upper())
    if os.name == "nt":
        assert upper == same, "Case must be folded on Windows, otherwise the same directory is added twice"
    else:
        assert upper != same, "POSIX is case-sensitive; folding would treat two different directories as one"


def test_roots_file_with_a_bom_is_still_read(workspace) -> None:
    """**This is the real shape on this machine**, not a corner case.

    `start.bat` calls Windows PowerShell 5.1, whose `Set-Content -Encoding UTF8` **writes a BOM**.
    Read as `utf-8`, the first line becomes `\\ufeffF:\\...`, so "remembered" never shows as remembered.
    """
    workspace["roots_file"].write_bytes(
        b"\xef\xbb\xbf" + (str(workspace["a"].resolve()) + "\n").encode("utf-8")
    )
    with _client(workspace) as client:
        entry = client.get("/api/roots").json()["roots"][0]
        assert entry["persisted"] is True, "A roots.txt with a BOM was not recognized (which is exactly what PS 5.1 writes)"
        # after the server rewrites it, it is always UTF-8 without a BOM (and is still recognized on the next read)
        client.post("/api/roots", json={"path": str(workspace["b"])})
        assert workspace["roots_file"].read_bytes()[:3] != b"\xef\xbb\xbf"
        assert all(item["persisted"] for item in client.get("/api/roots").json()["roots"])
