"""Caption write safety (web/js/tags.js + the activateImage path in app.js).

Three ways an edit could disappear, all found by the 2026-09 UX and accessibility audits:

1. **A failed PUT did not stop navigation.** `activateImage` awaited `tagEditor.flush()` and then
   loaded the next image regardless, so a read-only file or a restarted server silently ate
   everything typed. `flush()` now answers "clean" / "saved" / "failed" and the switch stops on
   "failed" - the user is asked, and declining puts the gallery marker back on the image still
   being edited.
2. **The failure itself was erased one line later.** The catch painted `is-error` and the trailing
   `paintState()` immediately repainted the dirty state over it. The error is now held until the
   next successful write.
3. **Leaving raw text mode silently truncated a multiline caption** and took the warning with it.
   The editor now asks first, and declining puts the toggle back.

Layer 1 is static wiring (the guard could exist and never be called); layer 2 really runs the
editor against a stub API through `test/fixtures/tags_harness.mjs`.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
WEB = REPO / "src" / "kohya_dataset_tagger" / "web"
JS = WEB / "js"
LOCALES = JS / "locales"


def source(name: str) -> str:
    return (JS / name).read_text(encoding="utf-8")


def test_flush_reports_a_failed_write_and_the_switch_listens() -> None:
    tags = source("tags.js")
    assert 'flush: async () => {' in tags, "flush is still a bare save() wrapper"
    assert 'return lastSaveFailed ? "failed" : "clean";' in tags, "flush cannot report a failure"
    app = source("app.js")
    assert 'const flushed = await tagEditor.flush();' in app, "activateImage ignores the flush result"
    assert 'if (flushed === "failed")' in app, "activateImage navigates away from a failed write"
    assert 'gallery.setActive(current, false)' in app, (
        "declining must put the gallery marker back without re-announcing the change"
    )


def test_a_sidebar_save_invalidates_what_the_app_cached() -> None:
    app = source("app.js")
    assert "const path = tagEditor.getPath();" in app, "the sidebar save path does not know which caption it wrote"
    assert "void refreshCaption(path);" in app, "the caption cache (filter, badge) is not refreshed"
    assert "void loadFrequency();" in app, "the frequency table keeps counting the pre-edit tags"


def test_leaving_raw_mode_asks_before_dropping_lines() -> None:
    tags = source("tags.js")
    assert "export function wouldDropLines(" in tags, "the truncation decision is not testable on its own"
    assert "const drop = wouldDropLines(rawTextEl.value)" in tags, (
        "leaving raw mode still truncates without asking"
    )
    assert 'confirmLabel: t("caption.dropLinesOk")' in tags, (
        "the dialog must name the action, not just ask a question"
    )
    assert "if (drop) {" in tags and "rawToggle.checked = true;" in tags, (
        "declining must restore the raw-mode toggle"
    )


def test_the_new_confirmations_are_translated_in_every_pack() -> None:
    for name in ("zh-CN.js", "en.js", "ja.js"):
        text = (LOCALES / name).read_text(encoding="utf-8")
        for key in ("caption.saveBlocked", "caption.dropLinesConfirm"):
            match = re.search(r'"' + re.escape(key) + r'":\s*"([^"]*)"', text)
            assert match is not None, "%s has no %s" % (name, key)
            assert match.group(1).strip(), "%s %s is empty" % (name, key)


def test_the_editor_behaves_headlessly_against_a_stub_api() -> None:
    """A failed write survives, a retry clears it, and the raw-mode exit asks before truncating."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("no node on this machine")
    harness = REPO / "test" / "fixtures" / "tags_harness.mjs"
    assert harness.is_file(), "missing headless behavior fixture %s" % harness
    done = subprocess.run(
        [node, str(harness), str(WEB)],
        capture_output=True, encoding="utf-8", errors="replace",
    )
    assert done.returncode == 0, "tag editor behavior criteria failed:\n%s\n%s" % (done.stdout, done.stderr)
    assert "tags harness OK" in done.stdout
