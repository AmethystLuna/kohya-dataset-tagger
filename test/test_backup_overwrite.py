"""`backup_overwrite`: the default write mode = back up first, then overwrite.

Why it is the default (p0-spec §4.5 (4))
----------------------------------------
`overwrite` is the only path that **irreversibly** loses an existing caption. The default must
not land on it: when the user clicks "Run" without looking closely at the dropdown, the worst
outcome should be an extra `.bak`, not permanently losing the old tags.

The criteria come in three layers; missing one gives "it looks wired up but is not connected at all":

1. **Value domain and default**: the backend `WRITE_MODES` / `DEFAULT_WRITE_MODE` and the frontend
   `WRITE_MODE` agree value for value, and the default is `backup_overwrite` on both sides;
2. **Backup semantics**: the four rules of `captions.backup_caption` (no caption -> do not create;
   byte-for-byte; never overwrite an existing backup; no change in content -> do not create);
3. **Wiring**: `_write_tags` really backs up before writing under `backup_overwrite`, while
   `overwrite` still does not back up.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "src" / "kohya_dataset_tagger" / "api" / "autotag.py"
API = REPO / "src" / "kohya_dataset_tagger" / "web" / "js" / "api.js"
AUTOTAG_JS = REPO / "src" / "kohya_dataset_tagger" / "web" / "js" / "autotag.js"

#: The backend is a **single-line** tuple (unlike BATCH_OPS's multi-line format), so it is matched with [^)]*.
BACKEND_MODES_RE = re.compile(r"WRITE_MODES: tuple\[str, \.\.\.\] = \(([^)]*)\)")
FRONTEND_MODES_RE = re.compile(r"WRITE_MODE = Object\.freeze\(\{(.*?)\}\);", re.S)
QUOTED_RE = re.compile(r'"([a-z_]+)"')


def text_of(path: Path) -> str:
    assert path.is_file(), "cannot find %s" % path
    return path.read_text(encoding="utf-8")


def backend_modes() -> list[str]:
    match = BACKEND_MODES_RE.search(text_of(BACKEND))
    assert match is not None, "WRITE_MODES not found in api/autotag.py"
    return QUOTED_RE.findall(match.group(1))


def frontend_modes() -> list[str]:
    match = FRONTEND_MODES_RE.search(text_of(API))
    assert match is not None, "WRITE_MODE not found in api.js"
    return re.findall(r':\s*"([a-z_]+)"', match.group(1))


# ---------------------------------------------------------------------------
# 1. Value domain and default
# ---------------------------------------------------------------------------


def test_the_two_sides_agree_on_the_write_mode_set() -> None:
    """One drifted value = the user gets a 400 (bad_write_mode) only after clicking "Write"."""
    backend = backend_modes()
    frontend = frontend_modes()
    assert len(backend) >= 6, "the backend parsed only %d write_modes; the parsing is probably broken" % len(backend)
    assert backend == frontend, (
        "the two sides' write_mode sets or order disagree: backend=%s frontend=%s" % (backend, frontend)
    )


def test_backup_overwrite_is_the_default_on_both_sides() -> None:
    assert backend_modes()[0] == "backup_overwrite", "the backend's first value-domain entry is not the default"
    assert 'DEFAULT_WRITE_MODE = "backup_overwrite"' in text_of(BACKEND), (
        "the backend does not write the default as a named constant"
    )
    assert re.search(r"payload\.write_mode or DEFAULT_WRITE_MODE", text_of(BACKEND)), (
        "the run endpoint does not fall back to DEFAULT_WRITE_MODE (omitting write_mode goes somewhere else)"
    )
    assert frontend_modes()[0] == "backup_overwrite", "the frontend's first value-domain entry is not the default"
    assert "writeModeSelect.value = WRITE_MODE.BACKUP_OVERWRITE" in text_of(AUTOTAG_JS), (
        "the frontend dropdown does not default to backup_overwrite"
    )


# ---------------------------------------------------------------------------
# 2. Backup semantics (real files, tmp_path)
# ---------------------------------------------------------------------------


def _image(tmp_path: Path, name: str = "a.png") -> Path:
    path = tmp_path / name
    path.write_bytes(b"\x89PNG\r\n\x1a\n")
    return path


def test_no_caption_means_nothing_to_back_up(tmp_path: Path) -> None:
    image = _image(tmp_path)
    assert captions.backup_caption(image) is None
    assert not captions.backup_path(image).exists()


def test_backup_is_byte_identical_and_never_clobbered(tmp_path: Path) -> None:
    image = _image(tmp_path)
    target = captions.caption_path(image)
    original = "旧 tag, another\r\n".encode("utf-8")  # both CRLF + non-ASCII must be preserved byte-for-byte
    target.write_bytes(original)

    backup = captions.backup_caption(image, "new")
    assert backup == captions.backup_path(image)
    assert backup.read_bytes() == original, "the backup and the original are not byte-for-byte identical"

    target.write_bytes("second".encode("utf-8"))
    again = captions.backup_caption(image, "third")
    assert again == backup
    assert backup.read_bytes() == original, "the existing backup was overwritten (the original data from the first time is lost)"


def test_no_backup_when_the_content_would_not_change(tmp_path: Path) -> None:
    image = _image(tmp_path)
    captions.caption_path(image).write_bytes(b"same")
    assert captions.backup_caption(image, "same") is None
    assert not captions.backup_path(image).exists()


def test_backup_file_is_not_mistaken_for_an_image_or_caption(tmp_path: Path) -> None:
    """.bak is neither in IMAGE_EXTS nor equal to any caption sidecar name."""
    image = _image(tmp_path)
    backup = captions.backup_path(image)
    assert backup.name == "a.txt.bak"
    assert backup.suffix.lower() not in {e.lower() for e in paths.IMAGE_EXTS}
    assert backup != captions.caption_path(image)


# ---------------------------------------------------------------------------
# 3. Wiring: _write_tags
# ---------------------------------------------------------------------------


class _Tag:
    def __init__(self, tag: str) -> None:
        self.tag = tag


def test_write_tags_backs_up_then_overwrites(tmp_path: Path) -> None:
    image = _image(tmp_path)
    captions.caption_path(image).write_bytes(b"old")
    _write_tags(image, [_Tag("new1"), _Tag("new2")], "backup_overwrite")
    assert captions.caption_path(image).read_bytes() == b"new1, new2"
    assert captions.backup_path(image).read_bytes() == b"old"


def test_plain_overwrite_still_writes_no_backup(tmp_path: Path) -> None:
    """An old caller that explicitly picks overwrite sees unchanged behaviour."""
    image = _image(tmp_path)
    captions.caption_path(image).write_bytes(b"old")
    _write_tags(image, [_Tag("new")], "overwrite")
    assert captions.caption_path(image).read_bytes() == b"new"
    assert not captions.backup_path(image).exists()


from kohya_dataset_tagger.api.autotag import _write_tags  # noqa: E402
from kohya_dataset_tagger.core import captions, paths  # noqa: E402

# ---------------------------------------------------------------------------
# 4. The batch rewrite uses the same net (2026-09-19)
# ---------------------------------------------------------------------------


def test_the_batch_step_backs_up_before_it_overwrites(tmp_path: Path) -> None:
    """The batch panel rewrites the same .txt files the tagger does. The request's backup flag
    (p0-spec §4.5 ③, default true) is that promise carried over rather than re-implemented - and it
    goes through the one step function the preview, the sync endpoint and the job all share."""
    image = _image(tmp_path)
    original = "1girl, solo".encode("utf-8")
    captions.caption_path(image).write_bytes(original)
    request = CaptionBatchRequest(images=[str(image)], op="append", tags=["new"])
    entry, counted = _batch_step(image, "append", request, False)
    assert counted is True and entry["ok"] is True, entry
    assert captions.caption_path(image).read_bytes() == b"1girl, solo, new"
    assert captions.backup_path(image).read_bytes() == original, "the batch write kept no original"
    assert entry["before"] == "1girl, solo" and entry["after"] == "1girl, solo, new"


def test_a_second_batch_write_never_replaces_the_first_backup(tmp_path: Path) -> None:
    """The first .bak is the only copy of the state before the tool touched anything."""
    image = _image(tmp_path)
    captions.caption_path(image).write_bytes(b"first")
    for tag in ("second", "third"):
        request = CaptionBatchRequest(images=[str(image)], op="append", tags=[tag])
        _batch_step(image, "append", request, False)
    assert captions.backup_path(image).read_bytes() == b"first"
    assert captions.caption_path(image).read_bytes() == b"first, second, third"


def test_a_batch_op_that_changes_nothing_leaves_no_backup(tmp_path: Path) -> None:
    image = _image(tmp_path)
    captions.caption_path(image).write_bytes(b"1girl, solo")
    request = CaptionBatchRequest(images=[str(image)], op="append", tags=["solo"])
    entry, _counted = _batch_step(image, "append", request, False)
    assert entry["before"] == entry["after"], entry
    assert not captions.backup_path(image).exists(), "an unchanged caption left noise in the dataset"


def test_backup_false_is_the_way_out(tmp_path: Path) -> None:
    image = _image(tmp_path)
    captions.caption_path(image).write_bytes(b"old tag")
    request = CaptionBatchRequest(images=[str(image)], op="normalize", backup=False)
    entry, _counted = _batch_step(image, "normalize", request, False)
    assert entry["ok"] is True, entry
    assert not captions.backup_path(image).exists()


def test_a_failed_backup_aborts_the_write(tmp_path: Path, monkeypatch) -> None:
    """Fail closed: a run that cannot keep the safety net must not overwrite anyway."""
    from kohya_dataset_tagger.api import captions as api_captions

    image = _image(tmp_path)
    captions.caption_path(image).write_bytes(b"old")

    def boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(api_captions.captions, "backup_caption", boom)
    request = CaptionBatchRequest(images=[str(image)], op="append", tags=["new"])
    entry, counted = _batch_step(image, "append", request, False)
    assert counted is False
    assert entry["ok"] is False and entry["error"]["code"] == "write_failed", entry
    assert entry["error"]["message"].startswith("backup failed"), entry
    assert captions.caption_path(image).read_bytes() == b"old", "overwritten without a backup"


# ---------------------------------------------------------------------------
# 5. The inverse: putting the backup back (the undo behind "undo > confirm")
# ---------------------------------------------------------------------------


def _te_cache(image: Path) -> Path:
    cache_dir = image.parent / "cache_text_encoder"
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / (image.stem + "_anima_te.safetensors")
    path.write_bytes(b"cache")
    return path


def test_restore_puts_the_bytes_back_and_consumes_the_backup(tmp_path: Path) -> None:
    """The backup holds the bytes from before the tool touched the file, so the undo is byte-level too."""
    image = _image(tmp_path)
    target = captions.caption_path(image)
    original = b"1girl, solo\r\nsecond line"
    target.write_bytes(original)
    request = CaptionBatchRequest(images=[str(image)], op="append", tags=["new"])
    assert _batch_step(image, "append", request, False)[0]["ok"] is True
    assert target.read_bytes() != original, "the write changed nothing, so there is no undo to test"

    result = captions.restore_caption(image)

    assert result.restored is True
    assert result.backup_removed is True
    assert target.read_bytes() == original, "the restore did not put the exact bytes back"
    assert not captions.backup_path(image).exists(), "the backup was not consumed by the restore"


def test_restore_invalidates_the_text_encoder_cache(tmp_path: Path) -> None:
    """Invariant number one: an undo that leaves the cache behind trains on the tags the user took back."""
    image = _image(tmp_path)
    target = captions.caption_path(image)
    target.write_bytes(b"old")
    request = CaptionBatchRequest(images=[str(image)], op="append", tags=["new"])
    assert _batch_step(image, "append", request, False)[0]["ok"] is True
    cache = _te_cache(image)

    result = captions.restore_caption(image)

    assert result.restored is True
    assert not cache.exists(), "restoring a caption left the text-encoder cache in place"
    assert [str(path) for path in result.cache_removed], result


def test_restore_without_a_backup_is_not_an_error(tmp_path: Path) -> None:
    image = _image(tmp_path)
    target = captions.caption_path(image)
    target.write_bytes(b"untouched")

    result = captions.restore_caption(image)

    assert result.restored is False
    assert result.backup_removed is False
    assert target.read_bytes() == b"untouched", "a restore with nothing to restore wrote anyway"


def test_a_write_after_a_restore_backs_up_again(tmp_path: Path) -> None:
    """The consumed backup re-arms the net: keeping it would disarm rule 1 for every later write."""
    image = _image(tmp_path)
    target = captions.caption_path(image)
    target.write_bytes(b"original")
    request = CaptionBatchRequest(images=[str(image)], op="append", tags=["first"])
    assert _batch_step(image, "append", request, False)[0]["ok"] is True
    assert captions.restore_caption(image).restored is True
    # Change the content before the second write: the restored bytes happen to equal the old backup, so
    # asserting on them could not tell "the net was re-armed" from "the stale .bak is still sitting
    # there and rule 1 handed it back".
    target.write_bytes(b"edited by hand")

    second = CaptionBatchRequest(images=[str(image)], op="append", tags=["second"])
    assert _batch_step(image, "append", second, False)[0]["ok"] is True

    assert captions.backup_path(image).read_bytes() == b"edited by hand", (
        "the write after a restore did not back the current caption up"
    )


def test_a_backup_that_cannot_be_deleted_is_reported(tmp_path: Path, monkeypatch) -> None:
    """Windows can refuse the unlink (a locked file); the caption is restored anyway, and the caller is told."""
    image = _image(tmp_path)
    target = captions.caption_path(image)
    target.write_bytes(b"original")
    captions.backup_file(target)
    target.write_bytes(b"changed")
    real_unlink = Path.unlink

    def refuse(self, *args, **kwargs):
        if self.name.endswith(captions.BACKUP_SUFFIX):
            raise PermissionError("locked")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", refuse)
    result = captions.restore_caption(image)

    assert result.restored is True
    assert result.backup_removed is False, "a backup that survived the restore was reported as removed"
    assert target.read_bytes() == b"original"


from kohya_dataset_tagger.api.captions import CaptionBatchRequest, _batch_step  # noqa: E402

