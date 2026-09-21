"""Scroll memory for directory navigation - "go up one level" should stop on the folder you just
came from.

The user's own words: "the path navigation experience of the various components needs polishing;
when going up one level it does not leave the current list's scroll area at the position of the
folder you are in / selected".

What is judged here is **behavior**, not "the function exists" - `navscroll.js` is really run
(the fake DOM's `getBoundingClientRect` is fed by the fixture), while the four components (the
left directory list, the center folder cards, the breadcrumb dropdown, the directory picker) and
the CSS highlight get wiring criteria only: `app.js` assembles the whole UI at module top level,
and a headless fixture would cost far more than it is worth - the same approach as
`test_web_dirlist.py` / `test_web_breadcrumb.py`.

The module's own invariants (path semantics, centering/clamping, bounds of the memory) are in
`test/fixtures/navscroll_harness.mjs`; the behavior criteria for the picker and the folder cards
are extended inside `picker_harness.mjs` and `folder_grid_harness.mjs` respectively (both are
existing fixtures, no new set is started).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
WEB = REPO / "src" / "kohya_dataset_tagger" / "web"
JS = WEB / "js"
APP = JS / "app.js"
GALLERY = JS / "gallery.js"
PICKER = JS / "picker.js"
NAVSCROLL = JS / "navscroll.js"
CSS = WEB / "css" / "app.css"


def text_of(path: Path) -> str:
    assert path.is_file(), "cannot find %s" % path
    return path.read_text(encoding="utf-8")


def body_of(path: Path, start: str, end: str) -> str:
    """The segment from `start` up to `end` (both markers written literally)."""
    text = text_of(path)
    assert start in text, "%s has no %s" % (path.name, start)
    begin = text.index(start)
    stop = text.index(end, begin)
    return text[begin:stop]


def strip_comments(css: str) -> str:
    """Strip /* ... */ comments (the comments contain braces too)."""
    out = []
    depth = 0
    i = 0
    while i < len(css):
        if depth == 0 and css.startswith("/*", i):
            depth = 1
            i += 2
            continue
        if depth and css.startswith("*/", i):
            depth = 0
            i += 2
            continue
        if depth == 0:
            out.append(css[i])
        i += 1
    return "".join(out)


def rule(css: str, selector: str) -> str:
    """Return one rule's declaration body; selector is written literally."""
    for chunk in strip_comments(css).split("}")[:-1]:
        head, _, body = chunk.rpartition("{")
        if head.strip() == selector:
            return body
    raise AssertionError("CSS has no %s" % selector)


# ---------------------------------------------------------------------------
# 1. The module itself: one implementation, four consumers
# ---------------------------------------------------------------------------


def test_the_scroll_behaviour_lives_in_one_shared_module() -> None:
    body = text_of(NAVSCROLL)
    for name in ("export function childOnPath(", "export function samePath(",
                 "export function createScrollMemory(", "export function revealRow("):
        assert name in body, "navscroll.js is missing %s" % name
    for path in (APP, GALLERY, PICKER):
        assert 'from "./navscroll.js"' in text_of(path), (
            "%s does not use the shared scroll module: separate copies are how four components start drifting apart" % path.name
        )


def test_no_component_scrolls_with_scroll_into_view() -> None:
    """`scrollIntoView` scrolls ancestors along with it (including the page itself); here only the designated scroll container may move."""
    offenders = [
        path.name for path in (APP, GALLERY, PICKER) if ".scrollIntoView(" in text_of(path)
    ]
    assert not offenders, "these files call scrollIntoView (which scrolls ancestor containers too): %s" % offenders


# ---------------------------------------------------------------------------
# 2. Left directory list: return to the folder just left
# ---------------------------------------------------------------------------


def test_open_dir_reveals_the_directory_the_navigation_came_from() -> None:
    body = text_of(APP)
    assert "from = state.dir" in body, "openDir does not remember which directory this navigation started from"
    assert "childOnPath(state.dir, from)" in body, (
        "does not compute \"the child directory just left\" - going up has no reveal target"
    )
    assert "renderDirs(state.dirs, reveal)" in body, "openDir does not hand the target to renderDirs"
    assert "restoreScroll(state.dir, reveal, revealedRow)" in body, (
        "openDir does not restore the scroll position after fetching the data"
    )
    open_body = body_of(APP, "async function openDir(", "/** Section 4.11")
    assert open_body.index("rememberScroll(from)") < open_body.index("api.listDir("), (
        "the old list's position must be remembered before sending the request, otherwise on return you remember the value after it was overwritten"
    )


def test_the_directory_row_that_was_left_is_marked() -> None:
    body = body_of(APP, "function renderDirs(", "/** Repaint the list from state")
    assert "samePath(dir.path, reveal)" in body, "renderDirs does not recognize \"the child directory just left\" by path"
    assert '"is-revealed"' in body or "is-revealed" in body, "recognized but not marked"
    assert "return revealed;" in body, "renderDirs does not hand out that row, so the caller has no way to scroll to it"


def test_repainting_the_list_does_not_lose_its_position() -> None:
    """Switching language is an in-place re-render: repainting once should not bounce the list back to the top."""
    body = text_of(APP)
    assert "function repaintDirs()" in body, "no repaint function that preserves the scroll position"
    repaint = body_of(APP, "function repaintDirs()", "/** Remember where both listings")
    assert "list.scrollTop" in repaint, "repaintDirs does not read/write scrollTop"
    relabel = body_of(APP, "async function relabelAfterLocaleChange()", "// ------")
    assert "repaintDirs()" in relabel, "switching language still goes through the old path that bounces the list back to the top"
    assert "renderDirs(state.dirs)" not in relabel, "switching language still repaints the directory list directly"


def test_the_two_listings_remember_where_they_were_left() -> None:
    body = text_of(APP)
    assert "createScrollMemory()" in body, "app.js has no directory-level scroll memory"
    remember = body_of(APP, "function rememberScroll(dir)", "function setScrollTop(")
    assert "$(\"dir-list\").scrollTop" in remember and '$("gallery").scrollTop' in remember, (
        "only the left column or only the center workspace is remembered: either side could be the column the user is reading"
    )
    restore = body_of(APP, "function restoreScroll(", "// ------")
    assert "revealRow(list, revealedRow)" in restore, "the target row is given but it does not scroll to it"
    assert "gallery.revealDir(reveal)" in restore, "the center workspace's folder cards are not wired"
    assert "dirScroll.recall(dir)" in restore and "gridScroll.recall(dir)" in restore, (
        "does not fall back to \"where this directory last stopped\" - a refresh or a jump back bounces to the top"
    )


def test_restore_prefers_the_folder_we_came_from_over_the_remembered_position() -> None:
    """The order must not be reversed: when going up, the user is looking for the folder just left, not the previous offset."""
    restore = body_of(APP, "function restoreScroll(", "// ------")
    assert restore.index("revealRow(list, revealedRow)") < restore.index("dirScroll.recall(dir)"), (
        "the remembered position is placed before reveal: going up one level stops at the old offset instead of the folder just left"
    )


def test_the_breadcrumb_menu_opens_on_the_current_entry() -> None:
    """With 200+ sibling directories, the dropdown opens parked on the current entry, not at the top of the list."""
    body = body_of(APP, "function paintCrumbMenu(", "async function toggleCrumbMenu(")
    assert "currentItem = item" in body, "no reference to the current entry is kept"
    assert "revealRow(menu, currentItem)" in body, "the dropdown menu does not scroll to the current entry"


# ---------------------------------------------------------------------------
# 3. Center folder cards and the directory picker
# ---------------------------------------------------------------------------


def test_the_gallery_exposes_a_reveal_entry_point() -> None:
    body = text_of(GALLERY)
    assert "function revealDir(path)" in body, "gallery.js has no revealDir"
    assert "revealRow(container, entry.card)" in body, "revealDir does not scroll the center workspace"
    assert 'classList.add("is-revealed")' in body, "the card is not marked \"just came from here\""
    assert "revealDir," in body, "revealDir does not appear in the gallery's public interface"


def test_the_picker_remembers_and_reveals_like_the_main_browser() -> None:
    body = text_of(PICKER)
    assert "createScrollMemory()" in body, "the picker has no directory-level scroll memory"
    assert "scrollMemory.remember(currentPath, list.scrollTop)" in body, (
        "does not remember where a directory stopped before leaving it"
    )
    assert "childOnPath(currentPath, from)" in body, "the picker does not compute \"the child directory just left\""
    assert "navigate(parentPath, { from: currentPath })" in body, (
        "\"up one level\" does not carry the departure directory over, so there is nothing to reveal"
    )


# ---------------------------------------------------------------------------
# 4. CSS: the revealed row is visible
# ---------------------------------------------------------------------------


def test_the_revealed_row_is_highlighted() -> None:
    css = text_of(CSS)
    row = rule(css, ".list button.is-revealed")
    assert "background" in row and "border-color" in row, "the left column row has no visible highlight"
    card = rule(css, ".dir-card.is-revealed .dir-thumbs")
    assert "border-color" in card, "the folder card has no visible highlight"
    picker = rule(css, ".picker-dir.is-revealed")
    assert "background" in picker, "the picker's row has no visible highlight"


# ---------------------------------------------------------------------------
# 5. Behavior: really run navscroll.js (fake DOM, no jsdom)
# ---------------------------------------------------------------------------


def test_navscroll_behaves_headlessly_against_a_fake_dom() -> None:
    "Really run navscroll.js: path semantics (including the segment-prefix trap and Windows separators), memory bounds, centering and clamping."
    node = shutil.which("node")
    if node is None:
        pytest.skip("no node on this machine")
    harness = REPO / "test" / "fixtures" / "navscroll_harness.mjs"
    assert harness.is_file(), "missing headless behavior fixture %s" % harness
    done = subprocess.run(
        [node, str(harness), str(WEB)],
        capture_output=True, encoding="utf-8", errors="replace",
    )
    assert done.returncode == 0, "navscroll.js behavior criteria failed:\n%s\n%s" % (done.stdout, done.stderr)
    assert "navscroll harness OK" in done.stdout
