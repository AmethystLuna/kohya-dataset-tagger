"""Breadcrumb: following the VS Code approach, each segment drops down its
**sibling** directories.

Two invariants:

1. The root segment's name is **derived from the path** (`baseName`), not taken
   from `state.rootName` -- after a deep link/refresh the latter holds the whole
   path, which is exactly where "same directory, two displays" comes from;
2. Every segment must be able to list **sibling** directories (not just jump up):
   the root segment lists the allowlisted roots + "choose another dataset
   directory…", and the rest list that directory's sibling directories.

Only static criteria here: the breadcrumb logic lives in `app.js` (the module top
level wires up the whole UI), so a headless fixture would cost far more than it is
worth; behavior in a real browser was checked by hand with Chrome MCP (see
changelog/ui-picker-and-zoom).
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WEB = REPO / "src" / "kohya_dataset_tagger" / "web"
APP = WEB / "js" / "app.js"
CSS = WEB / "css" / "app.css"
HTML = WEB / "index.html"
STRINGS = WEB / "js" / "locales" / "zh-CN.js"


def text_of(path: Path) -> str:
    assert path.is_file(), "not found: %s" % path
    return path.read_text(encoding="utf-8")


def breadcrumb_body() -> str:
    """The stretch from `renderBreadcrumb` up to, but not including, `renderCounts`."""
    body = text_of(APP)
    start = body.index("function renderBreadcrumb(")
    end = body.index("function renderCounts(")
    return body[start:end]


def test_root_label_comes_from_the_path_not_state_rootname() -> None:
    """After a deep link/refresh state.rootName is the whole path -- it cannot serve as the breadcrumb's display name."""
    segment = breadcrumb_body()
    assert "label: baseName(state.root)" in segment, "the root segment does not derive its name with baseName"
    assert "state.rootName || state.root" not in text_of(APP), (
        "still using state.rootName as the display name (the same directory shows as either a path or a name)"
    )


def test_every_segment_lists_its_siblings() -> None:
    """Core capability: sibling navigation. A segment must carry its parent, and the menu must really list it."""
    body = text_of(APP)
    assert "async function crumbEntries(" in body, "there is no function to fetch sibling entries"
    assert "api.listDir(segment.parent" in body, "sibling entries are not obtained by listing the parent directory"
    assert "parent," in body, "the segment does not carry its parent, so the sibling menu has nothing to list"
    assert "current: dir.path === segment.path" in body, "the sibling menu does not mark the current entry"


def test_dataset_segment_lists_roots_and_offers_the_picker() -> None:
    """The root segment's dropdown = the other datasets in the allowlist + choosing another directory."""
    body = text_of(APP)
    assert "segment.kind === \"root\"" in body, "the root segment is not distinguished"
    assert "state.roots" in body, "the root segment does not use the allowlisted roots as sibling entries"
    assert "crumb-choose" in body and "requestPick()" in body, (
        "the root segment's dropdown has no 'choose another dataset directory…'"
    )


def test_menu_is_dismissed_by_outside_click_escape_and_navigation() -> None:
    """The overlay must be closable, otherwise it sits on top of the directory list."""
    body = text_of(APP)
    assert "function closeCrumbMenu(" in body, "there is no function to close the breadcrumb menu"
    assert "closeCrumbMenu();" in breadcrumb_body(), "re-rendering the breadcrumb does not close the old menu"
    assert 'addEventListener("click", (event) => {' in body, "no click-outside to close"
    assert re.search(r'key === "Escape".*?closeCrumbMenu\(\)', body, re.S), "Escape does not close the menu"


def test_breadcrumb_copy_keys_exist() -> None:
    keys = text_of(STRINGS)
    for key in ("breadcrumb.jump", "breadcrumb.current", "breadcrumb.choose", "breadcrumb.loadFailed"):
        assert '"%s"' % key in keys, "strings.js is missing %s" % key


# ---------------------------------------------------------------------------
# The census row: the count belongs NEXT TO the path it counts, and outside the nav
# ---------------------------------------------------------------------------


def strip_comments(css: str) -> str:
    """The stylesheet without its block comments, so a criterion reads declarations, not prose."""
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
    """One rule's declaration body; the selector is written literally."""
    for chunk in strip_comments(css).split("}")[:-1]:
        head, _, body = chunk.rpartition("{")
        if head.strip() == selector:
            return body
    raise AssertionError("app.css has no %s rule" % selector)


def test_the_census_is_a_sibling_of_the_nav_inside_its_own_row() -> None:
    """#dir-counts cannot be a child of #breadcrumb: the nav is rebuilt with replaceChildren() on
    every navigation (this module's own breadcrumb_body() - app.js's renderBreadcrumb), so an
    element inside it is destroyed by the next click. The census sits NEXT TO the nav in
    .breadcrumb-row, and it left the page header for that row, next to the directory it counts.
    """
    html = text_of(HTML)
    row = html[html.index('<div class="breadcrumb-row">'):]
    row = row[:row.index("</div>")]
    assert '<nav id="breadcrumb" class="breadcrumb"></nav>' in row, (
        "the breadcrumb nav is not the row's first item: %r" % row
    )
    assert '<span id="dir-counts" class="counts"></span>' in row, (
        "the census is not in the breadcrumb row: %r" % row
    )
    assert row.index('id="breadcrumb"') < row.index('id="dir-counts"'), (
        "the census comes before the path it counts"
    )
    header = html[html.index('<header class="topbar">'):]
    header = header[:header.index("</header>")]
    assert 'id="dir-counts"' not in header, (
        "the census is still a child of the page header: the command bar needed that room"
    )
    assert text_of(APP).count('$("dir-counts")') == 2, (
        "renderCounts must stay the only painter and the only reader of the census element"
    )


def test_the_census_keeps_its_width_and_its_line() -> None:
    """The .li-count lesson, with a longer subject: the count is a right-hand column sized by its
    own content. It must not be squeezed by a long path (flex: 0 0 auto) and must not break between
    the number and its unit (white-space: nowrap) - CJK breaks between any two characters, so
    without it the census wrapped onto a second line. The other half is the nav's: the path is the
    part that shrinks (min-width lifts the flex automatic minimum), and the row's 6px gap moved
    from the nav to the row, so the centre column's stack did not get taller.
    """
    counts = rule(text_of(CSS), ".breadcrumb-row .counts")
    assert "flex: 0 0 auto" in counts, "the census can be squeezed by a long path"
    assert "white-space: nowrap" in counts, "the census can break between the number and its unit"
    assert "margin-left: auto" in counts, "the census is not pushed to the row's right edge"
    nav = rule(text_of(CSS), ".breadcrumb")
    assert "flex: 0 1 auto" in nav, "the path is not the part that shrinks"
    assert "min-width: 0" in nav, (
        "the nav keeps its automatic minimum, so a long path pushes the census out of the row"
    )
    assert "flex-wrap: wrap" in nav, "a long path cannot wrap; it would overflow the row instead"
    assert "margin-bottom" not in nav, (
        "the 6px gap under the row is on the nav again: with the row's own margin the stack grows"
    )
    row = rule(text_of(CSS), ".breadcrumb-row")
    assert "margin-bottom: 6px" in row, "the row lost the 6px the nav used to carry"
    assert "display: flex" in row and "align-items: center" in row, (
        "the path and the census are not laid out on one line"
    )

