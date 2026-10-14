"""Wiring criteria for §4.18 (open the directory on screen in the OS file manager) and for §4.12's
current-location breadcrumb in the picker.

Why here and not in `test_web_picker.py`: that file owns the picker's own behaviour (manual entry,
create-directory, the keyboard) and `test_web_filter.py` owns the gallery toolbar's exact shape. What
this file adds is the *wiring between them* - a new endpoint that nothing calls is exactly the defect
`test_web_picker.py` was written for ("the endpoint was right, the module was right, nobody called it"),
so each piece is asserted together with the thing that uses it.

The behaviour of the breadcrumb is not asserted here: `test/fixtures/picker_harness.mjs` really runs
`picker.js` over a fake DOM and drives the crumbs (an empty directory still says where it is, an
ancestor segment navigates, the current segment is not a button) - `test_web_picker.py` runs it.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WEB = REPO / "src" / "kohya_dataset_tagger" / "web"
JS = WEB / "js"
HTML = WEB / "index.html"
APP = JS / "app.js"
API_JS = JS / "api.js"
PICKER = JS / "picker.js"
STRINGS = JS / "locales" / "zh-CN.js"


def text_of(path: Path) -> str:
    assert path.is_file(), "not found: %s" % path
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# The route, the button, the handler
# ---------------------------------------------------------------------------


def test_api_js_declares_the_reveal_route_and_exposes_it() -> None:
    body = text_of(API_JS)
    assert '"POST /api/fs/reveal"' in body, "api.js does not declare POST /api/fs/reveal"
    assert 'revealDir: (path) => request("fsReveal", { body: { path } })' in body, (
        "api.js does not provide revealDir(path), or does not send it as a POST body"
    )


def test_the_gallery_toolbar_carries_the_open_folder_button() -> None:
    """The control is an icon button, and that is a measured decision (see the markup's own comment):
    with words it pushed the row's selection actions onto a second line at a 1280px window."""
    html = text_of(HTML)
    assert html.count('id="btn-open-folder"') == 1, (
        "the button exists %d times; one live copy is the invariant" % html.count('id="btn-open-folder"')
    )
    at = html.index('id="btn-open-folder"')
    tag = html[html.rindex("<button", 0, at): html.index(">", at) + 1]
    assert 'data-copy-aria="gallery.openFolder"' in tag, (
        "the icon button has no accessible name: a glyph-shaped name is worse than none"
    )
    assert 'data-copy-title="gallery.openFolder"' in tag, "the icon button has no tooltip"
    assert "data-copy=" not in tag, (
        "the button carries visible words (data-copy): the icon is named through data-copy-aria"
    )
    assert 'class="btn btn-icon"' in tag, "the button is not the icon-size control the row uses"
    assert "disabled" in tag, (
        "the button does not start disabled: before the first listing there is no directory to open"
    )
    end = html.index("</button>", at)
    glyph = html[html.index("<svg", at): end]
    assert 'aria-hidden="true"' in glyph, "the glyph is announced, so the control has two names"
    assert 'focusable="false"' in glyph, "the svg is a focus stop in some engines"
    # Its position on the grid's own row is pinned by test_web_filter.py (the toolbar's exact
    # children); this is the other half - the row it sits on is the one the grid lives in.
    assert "gallery-toolbar" in html[:at], "the button is not in the toolbar"


def test_the_button_calls_the_named_handler_that_the_palette_also_calls() -> None:
    body = text_of(APP)
    assert "async function openInFileManager()" in body, "app.js has no named openInFileManager()"
    assert '$("btn-open-folder").addEventListener("click", openInFileManager);' in body, (
        "the button is not wired to openInFileManager (a second closure would be a second behaviour)"
    )
    start = body.index("async function openInFileManager()")
    segment = body[start: start + 600]
    assert "api.revealDir(state.dir)" in segment, "the handler does not ask the endpoint to open the directory"
    assert 'notify(t("gallery.openFolderFailed"' in segment, (
        "a failed open is swallowed: a headless server has no launcher, and a button that does nothing "
        "silently is worse than a sentence"
    )
    assert 't("gallery.openFolderFailed"' in body, "the failure message key is not used"


def test_the_button_is_greyed_out_while_no_directory_is_open() -> None:
    """Unavailable is shown, with its reason - the app's own invariant; here the state is "nothing open"."""
    body = text_of(APP)
    start = body.index("function renderBreadcrumb()")
    segment = body[start: start + 2000]
    assert '$("btn-open-folder").disabled = !state.dir;' in segment, (
        "nothing paints the button's disabled state from the open directory"
    )


def test_the_frontend_uses_the_real_launcher_name_in_the_copy() -> None:
    """The button's words name what happens, and the zh label calls a directory 目录 (the one-word-per-
    concept table in ui-copy.md; the value is read here, not the file - the pack's own comments about
    §4.13's 文件夹 cards are not user-facing text)."""
    keys = text_of(STRINGS)
    for key in ("gallery.openFolder", "gallery.openFolderFailed"):
        assert '"%s"' % key in keys, "the zh pack is missing %s" % key
    value = re.search(r'"gallery\.openFolder":\s*"([^"]+)"', keys)
    assert value, "gallery.openFolder has no value"
    assert "文件夹" not in value.group(1), "the button calls a 目录 a 文件夹"
    assert "{message}" in keys, "gallery.openFolderFailed has no message placeholder"


# ---------------------------------------------------------------------------
# The picker's current location (§4.12)
# ---------------------------------------------------------------------------


def test_the_picker_shows_the_current_location_as_a_breadcrumb() -> None:
    body = text_of(PICKER)
    assert "picker-crumbs" in body and "picker-crumb" in body, "the picker has no location row"
    assert 't("picker.location")' in body, "the location row has no label copy"
    assert "function pathCrumbs(" in body, "nothing splits the path into its ancestors"
    assert 'setAttribute("aria-current", "true")' in body, (
        "the current directory is not marked for assistive tech (it is the only segment that is not a button)"
    )


def test_the_picker_is_relabelled_on_a_language_switch() -> None:
    """Its copy is all construction-time, so without relabel() the dialog stays in the old language."""
    body = text_of(PICKER)
    assert "function relabel(" in body, "picker.js has no relabel()"
    assert re.search(r"^\s*relabel,\s*$", body, re.M), (
        "picker.js defines relabel() but does not expose it (app.js falls back to console.warn)"
    )
    app = text_of(APP)
    assert "relabelComponent(picker)" in app, "app.js does not relabel the picker on a language switch"
