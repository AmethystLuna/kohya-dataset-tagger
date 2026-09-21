"""Criteria for following a dataset that changes outside the app (2026-09-20).

The gallery polls /api/fs/list instead of watching the filesystem: the backend has no change
notification, and inventing one would be a second source of truth for \"what changed\". What matters
here is not that a timer exists but that the reload can never fire when it would do damage.
"""

from __future__ import annotations

from pathlib import Path


def _web(*parts):
    return Path(__file__).resolve().parents[1].joinpath("src", "kohya_dataset_tagger", "web", *parts)


def _app() -> str:
    return _web("js", "app.js").read_text(encoding="utf-8")


def test_the_open_directory_is_re_checked_without_being_watched():
    app = _app()
    assert "const CHANGE_POLL_MS = " in app
    assert app.count("void pollForExternalChanges();") == 1, "the poll must be started exactly once"
    #: The stamp is count + newest mtime; anything else would need a new endpoint.
    assert "function listingStamp(payload)" in app
    assert "Number(item.mtime)" in app
    assert "await api.listDir(state.dir)" in app


def test_the_reload_never_fires_when_it_would_do_damage():
    app = _app()
    #: Our own writes move mtimes every second; reloading mid-job would fight the job.
    assert "jobShelf.active().length > 0" in app
    #: A background tab must not be reloaded.
    assert 'document.visibilityState !== "visible"' in app
    #: The focus guard, spelled out: "document.activeElement" on its own also appears in the
    #: scale panel, so a looser assertion cannot fail when the guard is removed.
    assert "/^(INPUT|TEXTAREA|SELECT)$/.test(focused.tagName)" in app


def test_a_change_is_re_lised_through_the_ordinary_path_and_announced():
    app = _app()
    assert "await openDir(state.dir, state.rootName);" in app
    assert 'notify(t("gallery.datasetChanged")' in app
    #: The first sighting of a directory is the baseline, not a change.
    assert "changeStamp.dir !== state.dir || changeStamp.stamp" in app


def test_every_pack_can_say_the_directory_changed():
    for name in ("zh-CN", "en", "ja"):
        text = _web("js", "locales", name + ".js").read_text(encoding="utf-8")
        assert '"gallery.datasetChanged"' in text, name
