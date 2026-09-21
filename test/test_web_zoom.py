"""**Wiring** criteria for the double-click zoom preview (image + side-by-side tags + editing).

The user asked: "the image in the center workspace can be double-clicked to zoom preview +
show tags side by side + edit".
What is asserted here is that it is **assembled**, not that "the function exists" - the gallery
already had a `dblclick` listener, but it previously only called `preventDefault()`, so a
double click did nothing.

"Side by side" is not an adjective: `.zoom-body` must be a horizontal flex row, with the image
and the tag column each taking one side.
The full run (including the real tags.js editor) lives in `test/fixtures/zoom_harness.mjs`.
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
ZOOM = JS / "zoom.js"
APP = JS / "app.js"
GALLERY = JS / "gallery.js"
STRINGS = JS / "locales" / "zh-CN.js"
CSS = WEB / "css" / "app.css"
HARNESS = REPO / "test" / "fixtures" / "zoom_harness.mjs"


def text_of(path: Path) -> str:
    assert path.is_file(), "cannot find %s" % path
    return path.read_text(encoding="utf-8")


def test_gallery_double_click_really_announces_the_image() -> None:
    """A double click must hand out the path: that old listener only called preventDefault()."""
    body = text_of(GALLERY)
    assert "onOpen" in body, "gallery.js has no onOpen option"
    open_at = body.index('addEventListener("dblclick"')
    call_at = body.index("onOpen(figure.dataset.path")
    assert open_at < call_at, "the dblclick listener does not call onOpen (double-clicking is still a no-op)"


def test_gallery_also_has_a_keyboard_route() -> None:
    """Double-click only = keyboard users cannot open the zoom view; Z is that accessibility entry point, and it must be written in the hint."""
    body = text_of(GALLERY)
    assert 'key === "z"' in body, "the gallery has no keyboard entry point for the zoom view"
    start = body.index('key === "z"')
    segment = body[start:start + 320]
    assert "onOpen(" in segment, "the keyboard entry point does not call onOpen"
    assert "双击（或 Z）放大预览" in text_of(STRINGS), "the hint copy does not tell the user they can press Z"


def test_zoom_module_is_reachable_and_reuses_the_editor() -> None:
    """Reuse tags.js's editor instead of writing a second set of edit logic."""
    assert ZOOM.is_file(), "no web/js/zoom.js"
    body = text_of(ZOOM)
    assert "export function createZoom" in body, "zoom.js does not export createZoom"
    assert "createTagEditor(" in body, "the zoom view does not reuse the tag editor (it will drift into a second set of edit logic)"
    assert "THUMB_SIZE.PREVIEW" in body, "the zoom view does not use the preview thumbnail (the original image must not be loaded)"
    app = text_of(APP)
    assert re.search(r'import\s*\{[^}]*createZoom[^}]*\}\s*from\s*"\./zoom\.js"', app), (
        "app.js does not import zoom.js (an orphan module turns the contract test red)"
    )


def test_app_wires_open_close_and_directory_switch() -> None:
    """All three paths - open, close, switch directory - must be wired."""
    app = text_of(APP)
    assert "zoom.open(path, paths)" in app, "the gallery does not wire the double click to the zoom view"
    assert "onClose:" in app, "after closing, the sidebar editor will not follow (the two images would overwrite each other)"
    assert "zoom.dismiss()" in app, "the zoom view is not dismissed when switching directories (the path list is already stale)"
    assert "tagEditor.flush()" in app and "tagEditor.disable()" in app, (
        "the sidebar editor is not flushed to disk and disabled before opening the zoom view (two live copies would overwrite each other)"
    )


def test_layout_is_side_by_side() -> None:
    """\"Side by side\" must be horizontal: one column of images, one column of tags, not stacked vertically."""
    css = text_of(CSS)
    body = re.search(r"\.zoom-body\s*\{([^}]*)\}", css)
    assert body is not None, "CSS has no .zoom-body"
    assert "flex" in body.group(1), ".zoom-body is not a flex layout, so it is not side by side"
    stage = re.search(r"\.zoom-stage\s*\{([^}]*)\}", css)
    tags = re.search(r"\.zoom-tags\s*\{([^}]*)\}", css)
    assert stage is not None, "CSS has no .zoom-stage"
    assert tags is not None, "CSS has no .zoom-tags"
    assert "flex" in tags.group(1), "the tag column does not take part in the flex layout"
    assert "1 1 auto" in stage.group(1), "the image area does not fill the remaining width"
    assert "0 0" in tags.group(1), "the tag column is not a fixed sidebar and would be squeezed away by the image"


def test_paging_controls_are_arrows_on_the_image_stage() -> None:
    """Previous/next changed from title-bar buttons to floating arrows on either side of the image
    (user request, like an image gallery).

    This is not purely aesthetic: the title-bar buttons sat far from what they changed, and in a
    narrow window the path would squeeze them out. The criteria pin two things - the arrows hang
    on `.zoom-stage`, not inside `.zoom-head`, and the CSS makes the stage a positioning context
    with the two arrows pinned to the left and right sides.
    """
    body = text_of(ZOOM)
    stage_append = re.search(r"stage\.append\((.*?)\);", body)
    assert stage_append is not None, "stage.append(...) not found in zoom.js"
    assert "prevButton" in stage_append.group(1) and "nextButton" in stage_append.group(1), (
        "previous/next is not attached to the image area (.zoom-stage)"
    )
    head_append = re.search(r"head\.append\((.*?)\);", body)
    assert head_append is not None, "head.append(...) not found in zoom.js"
    assert "prevButton" not in head_append.group(1) and "nextButton" not in head_append.group(1), (
        "the paging buttons are still left in the title bar"
    )
    assert re.search(r'navButton\("zoom-nav-prev", t\("zoom\.prev"\)', body), (
        "previous does not reuse strings.js copy (the accessible name would be missing)"
    )
    assert re.search(r'navButton\("zoom-nav-next", t\("zoom\.next"\)', body), (
        "next does not reuse strings.js copy (the accessible name would be missing)"
    )

    css = text_of(CSS)
    stage = re.search(r"\.zoom-stage\s*\{([^}]*)\}", css)
    assert stage is not None, "CSS has no .zoom-stage"
    assert "position" in stage.group(1) and "relative" in stage.group(1), (
        ".zoom-stage is not a positioning context, so the arrows drift elsewhere"
    )
    nav = re.search(r"\.zoom-nav\s*\{([^}]*)\}", css)
    assert nav is not None, "CSS has no .zoom-nav"
    assert "absolute" in nav.group(1), "the navigation arrows do not float over the image"
    left = re.search(r"\.zoom-nav-prev\s*\{([^}]*)\}", css)
    right = re.search(r"\.zoom-nav-next\s*\{([^}]*)\}", css)
    assert left is not None and "left" in left.group(1), "previous is not pinned to the left side"
    assert right is not None and "right" in right.group(1), "next is not pinned to the right side"


def test_image_is_a_viewer_with_wheel_zoom_and_drag_pan() -> None:
    """The zoom preview is also an image viewer control: the wheel zooms around the cursor and
    holding the button drags to pan.

    The criteria pin "it is really wired", not "a function exists": wheel must be passive:false
    (otherwise preventDefault is ignored and the page scrolls along), all three pointer events
    must be present, the transform must be written as translate + scale, and switching images
    must reset - otherwise paging to the next image stays at the previous zoom position.
    """
    body = text_of(ZOOM)
    assert re.search(r'stage\.addEventListener\("wheel",[^)]*passive:\s*false', body), (
        "wheel is not listened to with passive:false: preventDefault is ignored and the page scrolls along"
    )
    for event in ("pointerdown", "pointermove", "pointerup"):
        assert '"%s"' % event in body, "does not listen to %s (cannot drag)" % event
    assert "translate(" in body and "scale(" in body, "pan/zoom is not written into the transform"
    assert "MIN_SCALE" in body and "MAX_SCALE" in body, "zoom has no lower/upper bound and can shrink away or grow without limit"
    show = re.search(r"async function show\(nextIndex\)\s*\{(.*?)\n  \}", body, re.S)
    assert show is not None, "show() not found in zoom.js"
    assert "resetView()" in show.group(1), "switching images does not reset zoom/pan"
    dbl = re.search(r"function onDoubleClick\([^)]*\)\s*\{(.*?)\n  \}", body, re.S)
    assert dbl is not None, "onDoubleClick() not found in zoom.js"
    assert 'closest("button")' in dbl.group(1), (
        "the double-click handler does not exclude the navigation arrows: the arrows are inside the stage, so double-clicking one bubbles into a dblclick and zooms in"
    )

    css = text_of(CSS)
    stage = re.search(r"\.zoom-stage\s*\{([^}]*)\}", css)
    assert stage is not None, "CSS has no .zoom-stage"
    assert "touch-action" in stage.group(1), "the stage does not turn off touch scrolling, so a touch drag becomes a page scroll"
    assert "hidden" in stage.group(1), "the stage should not be a scrollable container (the bitmap moves via transform)"
    assert re.search(r"\.zoom-stage\.is-zoomed\s*\{[^}]*grab", css), "no grab cursor once zoomed in"
    assert re.search(r"\.zoom-stage\.is-panning\s*\{[^}]*grabbing", css), "no grabbing cursor while dragging"


def test_zoom_copy_keys_exist() -> None:
    keys = text_of(STRINGS)
    for key in (
        "zoom.title", "zoom.close", "zoom.prev", "zoom.next",
        "zoom.position", "zoom.hint", "zoom.empty", "zoom.loadFailed",
    ):
        assert '"%s"' % key in keys, "strings.js is missing %s" % key
    assert "双击（或 Z）放大预览" in keys, "the on-canvas hint does not tell the user the two ways into the zoom preview"


def test_opening_focuses_the_dialog_not_the_tag_input() -> None:
    """Focus landing in the tag input makes "← → to page" fake: the input box keeps the arrow keys for itself."""
    body = text_of(ZOOM)
    start = body.index("async function open(")
    segment = body[start:start + 700]
    assert "dialog.focus()" in segment, "after opening, focus is not handed to the dialog"
    assert "input.focus()" not in segment, "after opening, focus was placed in the tag input (arrow-key paging stops working)"


def test_zoom_behaves_headlessly() -> None:
    """Really run it: opening loads the image and tags, paging switches images, Esc closes and releases the bitmap."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("no node on this machine")
    assert HARNESS.is_file(), "missing headless behavior fixture %s" % HARNESS
    done = subprocess.run(
        [node, str(HARNESS), str(WEB)],
        capture_output=True, encoding="utf-8", errors="replace",
    )
    assert done.returncode == 0, "zoom view behavior criteria failed:\n%s\n%s" % (done.stdout, done.stderr)
    assert "zoom harness OK" in done.stdout
