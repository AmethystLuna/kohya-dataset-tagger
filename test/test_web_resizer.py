"""Layout controls: both sidebars can be dragged wider (left = directory browsing / right = tag panel).

The user asked: "polish the layout controls so the sidebars can be resized by hand".

The layout before the change was a CSS grid: `grid-template-columns: 260px minmax(0,1fr) 380px`,
with both sidebars' widths hard-coded in the stylesheet. Now `.layout` is a flex row:
`[left] [handle] [center] [handle] [right]`, and the two 10px handles are what used to be the
grid's `gap`; the sidebar widths come from `--left-w` / `--right-w`, which `resizer.js`
rewrites while dragging.

The static criteria can only prove "the wiring is connected and the styles are right"; the drag
direction, clamping, keyboard stepping and persistence behaviors are really exercised in
`test/fixtures/resizer_harness.mjs` with a fake DOM by running `createResizers()` (not by
grepping strings). The real-browser drag was measured separately with CDP and recorded in
`.github/memory/changelog/resizable-panels.md`.
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
RESIZER = JS / "resizer.js"
APP = JS / "app.js"
CSS = WEB / "css" / "app.css"
MARKUP = WEB / "index.html"
STRINGS = JS / "locales" / "zh-CN.js"
HARNESS = REPO / "test" / "fixtures" / "resizer_harness.mjs"

HANDLES = ("resizer-left", "resizer-right")


def text_of(path: Path) -> str:
    assert path.is_file(), "cannot find %s" % path
    return path.read_text(encoding="utf-8")


def rule(css: str, selector: str) -> str:
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert match is not None, "CSS has no %s" % selector
    return match.group(1)


def test_markup_declares_two_focusable_separators() -> None:
    """A handle must be a focusable separator: draggable with the mouse and adjustable with the keyboard."""
    markup = text_of(MARKUP)
    for element_id in HANDLES:
        match = re.search(r'<[^>]*\bid="%s"[^>]*>' % element_id, markup)
        assert match is not None, "index.html has no #%s" % element_id
        tag = match.group(0)
        assert 'role="separator"' in tag, "#%s is not a separator" % element_id
        assert 'aria-orientation="vertical"' in tag, "#%s does not declare its orientation" % element_id
        assert 'tabindex="0"' in tag, "#%s cannot be focused with the keyboard (dragging would become mouse-only)" % element_id
        assert "data-copy-aria=" in tag, "#%s has no accessible name" % element_id


def test_app_wires_the_resizers_to_both_sides() -> None:
    body = text_of(APP)
    assert re.search(r'import\s*\{[^}]*createResizers[^}]*\}\s*from\s*"\./resizer\.js"', body), (
        "app.js does not import resizer.js"
    )
    assert "createResizers(" in body, "app.js does not assemble the drag handles"
    assert 'document.querySelector(".layout")' in body, "the handles are not attached to .layout"
    for element_id in HANDLES:
        assert '$("%s")' % element_id in body, "app.js does not grab #%s" % element_id
    assert 'querySelector(".panel-left")' in body, "the left handle is not matched to the left column"
    assert 'querySelector(".panel-right")' in body, "the right handle is not matched to the right column"


def test_layout_row_is_flex_and_the_sides_use_the_width_variables() -> None:
    """The grid's fixed column widths must become flex + CSS variables, otherwise dragging has nothing to act on."""
    css = text_of(CSS)
    layout = rule(css, ".layout")
    assert "display: flex" in layout, ".layout is not a flex row yet"
    assert "grid-template-columns" not in layout, ".layout still keeps hard-coded grid column widths"
    assert "var(--left-w)" in rule(css, ".panel-left"), "the left column width is not wired to the variable"
    assert "var(--right-w)" in rule(css, ".panel-right"), "the right column width is not wired to the variable"
    center = rule(css, ".panel-center")
    assert "flex: 1 1 auto" in center and "min-width: 0" in center, (
        "the center gallery must take the remaining width and be allowed to shrink to 0 without bursting (min-width: 0)"
    )
    assert "--left-w: 260px" in css and "--right-w: 380px" in css, (
        "the :root default widths must be written out (JS's RESIZER_DEFAULTS is aligned with them)"
    )


def test_the_handle_is_grabbable_and_has_a_focus_ring() -> None:
    body = rule(text_of(CSS), ".resizer")
    assert "cursor: col-resize" in body, "the handle has no column-resize cursor"
    assert "touch-action: none" in body, "the handle is missing touch-action: none, so on touch screens it is treated as scrolling"
    assert ".resizer:focus-visible" in text_of(CSS), "the handle is invisible when focused with the keyboard"


def test_defaults_in_js_and_css_agree() -> None:
    """The two default sets must agree: otherwise the first frame uses CSS and then JS changes it to another width."""
    body = text_of(RESIZER)
    assert "RESIZER_DEFAULTS = Object.freeze({ left: 260, right: 380 })" in body, (
        "RESIZER_DEFAULTS no longer matches CSS's --left-w/--right-w"
    )


def test_copy_keys_exist() -> None:
    keys = text_of(STRINGS)
    for key in ("layout.resizeLeft", "layout.resizeRight", "layout.resizeHint"):
        assert '"%s"' % key in keys, "strings.js is missing %s" % key


def test_the_resizer_behaves_headlessly() -> None:
    """Really run createResizers(): drag direction, clamping, keyboard stepping, persistence, and the bad-saved-state fallback."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("no node on this machine")
    assert HARNESS.is_file(), "missing headless behavior fixture %s" % HARNESS
    done = subprocess.run(
        [node, str(HARNESS), str(WEB)],
        capture_output=True, encoding="utf-8", errors="replace",
    )
    assert done.returncode == 0, "resizer behavior criteria failed:\n%s\n%s" % (done.stdout, done.stderr)
    assert "resizer harness OK" in done.stdout
