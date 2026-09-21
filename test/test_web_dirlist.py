"""Layout criteria for the left-column directory browser -- "the n images on the
right must not wrap" and "the list scrolls itself".

The user's own words: "improve the layout of the directory browser on the left;
the 'n images' text on the right must not wrap".

Measured before the change (a real dataset, 219 subdirectories, local Chrome
1440×900 headless screenshot + CDP measurement; the full record is in
`.github/memory/changelog/dir-list-layout.md`):

1. With a long directory name the count on the right broke between "6" and the
   unit character -- `.li-count` had neither `white-space: nowrap` nor
   protection against being squashed by a long name (flex shrink left its
   min-content only one digit wide); CJK can wrap between any two characters, so
   this was not a theoretical problem.
2. The whole left column was one scroll region (`.panel`'s `overflow: auto`).
   After scrolling to the 20th subdirectory, the "roots" list, the "go up" button
   and the bottom hint had all left the viewport.

Both are pure CSS layout, and Node has no layout engine; the criteria therefore
inspect the CSS rule bodies directly, the same approach as
`test_web_zoom.py::test_layout_is_side_by_side`.
The browser-level measurement (CDP measuring `getClientRects().length`) is
recorded in the changelog and does not enter the unit tests: it needs a local
Chrome and a live service, so it is not a repeatable gate.
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WEB = REPO / "src" / "kohya_dataset_tagger" / "web"
CSS = WEB / "css" / "app.css"
APP = WEB / "js" / "app.js"
STRINGS = WEB / "js" / "locales" / "zh-CN.js"


def text_of(path: Path) -> str:
    assert path.is_file(), "not found: %s" % path
    return path.read_text(encoding="utf-8")


def rule(css: str, selector: str) -> str:
    """Take the declaration body of one rule; selector is matched literally.

    Splits on braces by hand instead of a regex -- avoids escaping braces in the
    pattern and skips comments along the way.
    """
    without_comments = strip_comments(css)
    for chunk in without_comments.split("}")[:-1]:
        head, _, body = chunk.rpartition("{")
        if head.strip() == selector:
            return body
    raise AssertionError("no such selector in the CSS: %s" % selector)


def strip_comments(css: str) -> str:
    """Strip /* ... */ comments (comments contain CJK/braces too)."""
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


def test_the_count_column_never_wraps() -> None:
    """`.li-count` must not wrap and must not shrink: this is the one the user called out."""
    body = rule(text_of(CSS), ".list .li-count")
    assert "white-space: nowrap" in body, (
        "the count field has no nowrap: CJK breaks between '6' and the unit character (exactly what the user reported)"
    )
    assert "flex: 0 0 auto" in body, (
        "the count field can be squashed: a long directory name squeezes it to min-content (only one digit wide) and it wraps anyway"
    )
    assert "margin-left: auto" in body, "the count field is not pushed right"
    assert "text-align: right" in body, "multi-digit counts and the unit are not right-aligned"


def test_the_name_is_the_part_that_shrinks_and_ellipsizes() -> None:
    """The name is the only part allowed to shrink, and it must really ellipsize rather than overflow."""
    body = rule(text_of(CSS), ".list .li-name")
    assert "overflow: hidden" in body, "the name is not clipped and pushes out of the panel"
    assert "text-overflow: ellipsis" in body, "no ellipsis when the name is too long"
    assert "white-space: nowrap" in body, "the name must not wrap (wrapping pushes the count to a second line)"
    assert "min-width: 0" in body, (
        "without min-width: 0 flex's automatic minimum size blocks shrinking, so the ellipsis never appears"
    )


def test_the_row_aligns_its_two_cells() -> None:
    """A single-line row aligns with center; baseline plus an overflow:hidden name misaligns."""
    body = rule(text_of(CSS), ".list button")
    assert "display: flex" in body, "the directory row is not flex, so the two cells cannot be pinned to the two sides"
    assert "align-items: center" in body, "the directory row is not vertically centered"


def test_the_left_panel_scrolls_its_lists_not_the_panel() -> None:
    """The left column itself must not scroll: the two lists must scroll, and the header and hint stay put."""
    panel = rule(text_of(CSS), ".panel-left")
    assert "flex-direction: column" in panel, "the left column is not a column layout, so a fixed header + independent scrolling is out of the question"
    assert "overflow: hidden" in panel, (
        "the left column still scrolls itself (.panel's overflow: auto): with 200+ directories the roots and 'go up' scroll out of the viewport"
    )

    roots = rule(text_of(CSS), ".panel-left > #root-list")
    dirs = rule(text_of(CSS), ".panel-left > #dir-list")
    assert "overflow-y: auto" in roots, "the roots list has no scrolling of its own"
    assert "overflow-y: auto" in dirs, "the subdirectory list has no scrolling of its own"
    assert "min-height: 0" in roots and "min-height: 0" in dirs, (
        "a flex child without min-height: 0 does not shrink, so the scrollbar never appears"
    )
    assert "flex: 1 1 0" in dirs, (
        "the subdirectory list's flex-basis must be 0, not auto: "
        "the content height of 219 rows becomes the shrink weight and squeezes the "
        "single-row roots list down to a slit"
    )

    hint = rule(text_of(CSS), ".panel-left > .hint")
    assert "flex: 0 0 auto" in hint, "the bottom hint gets squeezed away by the lists; it should be fixed"


def test_the_count_is_one_string_from_strings_js() -> None:
    """Wrapping can only come from CSS: JS must hand over one complete phrase ("{n} 张")."""
    body = text_of(APP)
    assert 't("browse.dirImageCount", { n: dir.image_count })' in body, (
        "the directory row's count is not rendered from the shared copy key (it may have been split into two nodes)"
    )
    assert 'count.className = "li-count"' in body, "the count no longer takes the .li-count class"

    line = next(
        (ln for ln in text_of(STRINGS).splitlines() if '"browse.dirImageCount"' in ln),
        None,
    )
    assert line is not None, "browse.dirImageCount is not in strings.js"
    value = line.split(":", 1)[1].strip().rstrip(",").strip().strip('"')
    assert value == "{n} 张", (
        "the copy changed to %r: the number and the unit must be separated by a plain space and the whole must not wrap" % value
    )
