"""Gallery thumbnails and asynchronous status: the tile loading state, and the live regions a
screen reader needs for work that happens off-screen.

Two things were true before this file existed, and neither was visible to any test:

1. **A thumbnail in flight looked like an image with no pixels.** The tile frame paints a
   transparency checkerboard, and an image that had not decoded yet showed nothing over it, so
   "loading", "failed" and "a genuinely transparent PNG" were the same picture. The loader now
   marks the image with .is-loading while the request is in flight and .is-loaded once it lands.
2. **Every asynchronous result was silent.** A search for aria-live over the whole frontend
   returned nothing: a WD14 run over 200 images, a scaling export, an autosave failure and every
   toast happened without a word to a screen reader.

The criteria come in two layers, because "the attribute is in the HTML" and "the loader really
marks the tile" are different claims:

1. **Wiring (static)**: the live regions exist and point at real elements, the progress bars are
   progress bars, and the loader source keeps the two markers;
2. **Behavior**: really run gallery.js ThumbLoader against a fake image element (no jsdom) - the
   marker appears, clears on load, becomes an error on failure, and the concurrency cap holds.
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
HTML = WEB / "index.html"
CSS = WEB / "css" / "app.css"
GALLERY = JS / "gallery.js"
SCALE = JS / "scale.js"


def element(html: str, element_id: str) -> str:
    match = re.search('<[^>]*id="' + re.escape(element_id) + '"[^>]*>', html)
    assert match is not None, "index.html has no element with id %r" % element_id
    return match.group(0)


# ---------------------------------------------------------------------------
# 1. Wiring: the live regions a screen reader listens to
# ---------------------------------------------------------------------------


def test_toasts_are_a_live_region() -> None:
    """A toast that is never announced is invisible to anyone not looking at that corner."""
    tag = element(HTML.read_text(encoding="utf-8"), "toasts")
    assert 'aria-live="polite"' in tag, "the toast host is not a live region"
    assert 'role="status"' in tag, "the toast host has no role"
    source = (JS / "app.js").read_text(encoding="utf-8")
    assert 'box.setAttribute("role", kind === "error" ? "alert" : "status")' in source, (
        "an error toast must interrupt (role=alert); a status toast must not"
    )


def test_autosave_state_and_warnings_are_announced() -> None:
    html = HTML.read_text(encoding="utf-8")
    assert "aria-live" in element(html, "save-state"), "the autosave state is silent"
    assert 'role="alert"' in element(html, "caption-warning"), (
        "the multiline caption warning must reach a screen reader when it appears"
    )


def test_progress_bars_are_progress_bars() -> None:
    """The autotag bar is in index.html; the scale bar is built by scale.js."""
    html = HTML.read_text(encoding="utf-8")
    fill = element(html, "autotag-progress-fill")
    assert 'role="progressbar"' in fill, "the autotag progress fill is a bare div"
    assert "aria-valuenow" in fill and "aria-valuemax" in fill, "no value is exposed"
    labelled_by = re.search('aria-labelledby="([^"]+)"', fill)
    assert labelled_by is not None, "the progress bar has no accessible name"
    element(html, labelled_by.group(1))  # the label target must exist
    assert "aria-live" in element(html, "autotag-progress-text"), "the progress text is silent"
    assert 'role="alert"' in element(html, "autotag-errors"), "streamed errors are silent"

    autotag = (JS / "autotag.js").read_text(encoding="utf-8")
    assert 'progressFill.setAttribute("aria-valuenow", String(pct))' in autotag, (
        "the autotag progress bar never updates its value"
    )


def test_scale_panel_exposes_progress_and_errors() -> None:
    source = SCALE.read_text(encoding="utf-8")
    assert 'progressFill.setAttribute("role", "progressbar")' in source, "the scale bar is not a progress bar"
    assert '"aria-valuenow", String(pct)' in source, "the scale progress bar never updates its value"
    assert 'progressText.setAttribute("aria-live", "polite")' in source, "the scale progress text is silent"
    assert 'errorsBox.setAttribute("role", "alert")' in source, "scale export failures are silent"


# ---------------------------------------------------------------------------
# 3. Guidance: the empty state that knows why it is empty
# ---------------------------------------------------------------------------


def test_the_filtered_empty_state_offers_the_way_out() -> None:
    """Guidance beats instruction.

    When a filter hides every image, the empty state carries an action instead of a
    sentence pointing at another screen, and it carries it **only** in that branch (a
    folder-only level must not offer to clear a filter). Since 2026-09-20 the conditions
    themselves are on screen too - one chip each, one row above, in #gallery-filter (see
    test_web_filter.py) - but this button is the single click that clears *every*
    condition, which is why it stays wired to clearAll() and not to clearTags().
    """
    html = HTML.read_text(encoding="utf-8")
    button = element(html, "btn-gallery-clear-filter")
    assert 'data-copy="filter.clearAll"' in button, (
        "the empty state's action is not the 'clear every filter' one (a tag-only clear "
        "leaves the search box and 'selection only' in place, and the grid stays empty)"
    )

    source = (JS / "app.js").read_text(encoding="utf-8")
    assert 'el.textContent = t("gallery.emptyFiltered")' in source, "the filtered empty state is gone"
    assert re.search(
        r'\$\("btn-gallery-clear-filter"\)\.addEventListener\("click", \(\) => filterPanel\.clearAll\(\)\)',
        source,
    ), "the button in the empty state is not wired to anything"

    body = re.search(r"function paintEmptyState\(\)\s*\{(.*?)\n\}", source, re.S)
    assert body is not None, "paintEmptyState() not found"
    branch = body.group(1)
    assert branch.count("clear.hidden = false") == 1, (
        "the action must be shown in exactly one branch (the filtered one)"
    )
    assert branch.count("clear.hidden = true") == 2, (
        "the other two branches must hide it, or it survives a directory change"
    )


# ---------------------------------------------------------------------------
# 2. The tile loading state
# ---------------------------------------------------------------------------


def test_loader_marks_in_flight_tiles() -> None:
    source = GALLERY.read_text(encoding="utf-8")
    assert 'img.classList.add("is-loading")' in source, "an in-flight thumbnail is not marked"
    assert 'img.classList.remove("is-loading")' in source, "the loading mark is never cleared"
    css = CSS.read_text(encoding="utf-8")
    assert ".tile-img.is-loading" in css, "the loading mark has no styling, so nothing changes on screen"
    assert "prefers-reduced-motion" in css, "the loading animation ignores reduced-motion"


def test_thumbnail_loader_behaves_headlessly_against_a_fake_image() -> None:
    """The marker appears, clears on load, becomes an error on failure, and the cap still holds."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("no node on this machine")
    harness = REPO / "test" / "fixtures" / "thumb_loader_harness.mjs"
    assert harness.is_file(), "missing headless behavior fixture %s" % harness
    done = subprocess.run(
        [node, str(harness), str(WEB)],
        capture_output=True, encoding="utf-8", errors="replace",
    )
    assert done.returncode == 0, "thumbnail loader behavior criteria failed:\n%s\n%s" % (done.stdout, done.stderr)
    assert "thumb loader harness OK" in done.stdout


def test_scrolled_into_view_tiles_are_served_before_the_backlog() -> None:
    """The queue is bounded, so its order is what the user feels.

    One directory queues far more thumbnails than THUMB_CONCURRENCY, so a tile
    that comes into view on scroll used to wait behind every prefetch the first
    screen had already queued - the images the user was looking at arrived last.
    The mechanism is two observers (a 400px margin to prefetch, no margin to know
    what is on screen), and the second one has to re-rank the loader's queue;
    only running the real gallery shows that.
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("no node on this machine")
    harness = REPO / "test" / "fixtures" / "gallery_scroll_harness.mjs"
    assert harness.is_file(), "missing headless behavior fixture %s" % harness
    done = subprocess.run(
        [node, str(harness), str(WEB)],
        capture_output=True, encoding="utf-8", errors="replace",
    )
    assert done.returncode == 0, "gallery scroll priority criteria failed:\n%s\n%s" % (done.stdout, done.stderr)
    assert "gallery scroll harness OK" in done.stdout
