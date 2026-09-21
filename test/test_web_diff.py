"""Rendering of the tag diff: additions boxed green, deletions red.

The user asked for "box additions in green, deletions in red". The facts before the change:

- the `before` cell was **one solid block of plain text**, so a deleted tag was invisible in the
  UI - and that is exactly "what you lose when you press the button";
- `.diff .del` in the CSS was a dead rule **no JS ever used**;
- `.diff .add` only changed the text color, it was not a "box".

Static criteria can only prove a class name appears somewhere; here we really run
`renderCaptionDiff` (fake DOM, no jsdom) and assert **which chip gets which class**.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
WEB = REPO / "src" / "kohya_dataset_tagger" / "web"
AUTOTAG = WEB / "js" / "autotag.js"
CSS = WEB / "css" / "app.css"
HARNESS = REPO / "test" / "fixtures" / "diff_harness.mjs"


def text_of(path: Path) -> str:
    assert path.is_file(), "cannot find %s" % path
    return path.read_text(encoding="utf-8")


def test_both_columns_go_through_the_shared_renderer() -> None:
    """Both sides must go through the same function: change only one side and the other falls back to plain text."""
    body = text_of(AUTOTAG)
    assert "export function renderCaptionDiff" in body, "no testable diff render function"
    assert "renderCaptionDiff(cellBefore, cellAfter" in body, "renderDiff does not go through the shared render function"
    assert "appendTagChips(" in body, "chip rendering was not extracted"
    assert "before.textContent" not in body, "the before cell fell back to plain text (deletions become invisible)"


def test_added_is_a_green_box_and_removed_is_red() -> None:
    """`.add` must **have a border** (boxed), `.del` must be red with a strikethrough, and both must really have a producer."""
    css = text_of(CSS)
    add = re.search(r"\.diff \.add\s*\{([^}]*)\}", css)
    dele = re.search(r"\.diff \.del\s*\{([^}]*)\}", css)
    assert add is not None, "CSS has no .diff .add"
    assert dele is not None, "CSS has no .diff .del"
    assert "var(--ok)" in add.group(1) and "border:" in add.group(1), (
        ".add must be a green **box** (border), not just a text color change"
    )
    assert "var(--danger)" in dele.group(1), ".del must be red"
    assert "text-decoration" in dele.group(1), ".del should have a strikethrough"
    body = text_of(AUTOTAG)
    assert '"del"' in body, ".del has become a dead rule nobody uses again"


def test_cleared_caption_is_boxed_red_without_a_strike() -> None:
    """Had a caption, new caption is empty = it will be cleared: mark it "none" and box it red, but no strikethrough."""
    css = text_of(CSS)
    cleared = re.search(r"\.diff \.cleared\s*\{([^}]*)\}", css)
    assert cleared is not None, "CSS has no .diff .cleared"
    body = cleared.group(1)
    assert "var(--danger)" in body, ".cleared must be red"
    assert "border:" in body, ".cleared must be boxed"
    assert "text-decoration" not in body, ".cleared should have no strikethrough (what is deleted is the whole caption, not one item in a list)"
    js = text_of(AUTOTAG)
    assert '"cleared"' in js, ".cleared has no producer"
    assert '"autotag.diffNone"' in js, "the empty cell does not use the \"none\" label"
    assert '"autotag.diffNoChange"' not in js, "still uses the deprecated \"no change\" label"
    assert '"autotag.diffNone": "无"' in text_of(REPO / "src" / "kohya_dataset_tagger" / "web" / "js" / "locales" / "zh-CN.js")


def test_diff_marks_the_right_chip_headlessly() -> None:
    """Really run it: which chip on which side gets add / del."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("no node on this machine")
    assert HARNESS.is_file(), "missing headless behavior fixture %s" % HARNESS
    done = subprocess.run(
        [node, str(HARNESS), str(WEB)],
        capture_output=True, encoding="utf-8", errors="replace",
    )
    assert done.returncode == 0, "diff behavior criteria failed:\n%s\n%s" % (done.stdout, done.stderr)
    assert "diff harness OK" in done.stdout


def test_a_preview_stops_describing_a_scope_it_no_longer_covers() -> None:
    """signature() re-signs the preview's own paths, so by itself it can only catch a settings change."""
    body = text_of(AUTOTAG)
    assert "function previewCoversScope() {" in body, "the diff never checks the scope it is describing"
    assert 'if (!previewCoversScope()) return t("autotag.diffStaleScope");' in body, (
        "a scope change is not distinguished from a settings change, and for a scope change the other copy is wrong"
    )
    assert 'if (previewSignature !== signature(preview.paths)) return t("autotag.diffStale");' in body, (
        "a settings change no longer stales the diff"
    )
    assert "onPaint: () => { refreshDiffStale(); refreshActionButtons(); }," in body, (
        "a directory or selection change never reaches the staleness check (scope.js paints the counts"
        " and the panel's paint hook is what says the diff is now about other images)"
    )
    assert "if (message === diffStaleText) return;" in body, (
        "the table is rebuilt on every caption probe chunk again (the O(N^2) the probe exists to avoid)"
    )
    assert "diffStaleText = \"\";\n    refreshDiffStale();" in body, (
        "repainting the table drops the warning that says the table is stale"
    )
    # and the write mode is NOT one of the things that can stale it - see the next test


def test_the_preview_reviews_the_tagger_not_the_write() -> None:
    """§4.5 (1) lists /preview's request body without a write_mode, and the panel must not invent one.

    The diff answers "what does the tagger produce, against what is on disk"; the write mode only
    decides how /run composes the two. Folding the mode into the preview signature made a mode
    switch claim "the settings changed - preview again" and locked Run behind a re-preview whose
    output cannot differ - a false alarm is the same defect as a missing one.
    """
    body = text_of(AUTOTAG)
    assert "writeMode: writeModeSelect.value," not in body, (
        "the write mode is part of the preview signature, so switching it fakes a stale preview"
    )
    assert "OVERWRITE_WRITE_MODES" not in body, "a mode-dependent note crept back under the diff"
    assert "autotag.diffModeNote" not in body, (
        "the table explains the write mode instead of showing what the tagger produced"
    )
    assert "payload.write_mode = writeModeSelect.value;" in body, (
        "the run stopped sending the mode it composes with"
    )
