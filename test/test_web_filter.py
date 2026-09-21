"""Filter panel (`web/js/filter.js`): tag search, sorting, selection-set filtering, and
recomputing frequencies from the visible images.

The user asked to "polish the filter logic and UX after stable-diffusion-webui-dataset-tag-editor".
The three corresponding things in the reference implementation: Search Tags (the search box),
Sort by, Filter by Selection (treating the gallery selection as one filter layer), and
"the tag list shrinks with the currently shown images".

The criteria come in three layers; drop one and you get "it looks done but was never wired up":

1. **Wiring**: search / sort / the two checkboxes really are in index.html and really handed to
   the panel, and clear-all really is bound to the button;
2. **Behavior**: really run `filter.js` (fake DOM, no jsdom) - three-state combinations
   (or/and/exclusion precedence), search **before** truncation, the selection set intersected
   with the tag filter rather than replacing it, frequencies shrinking with the visible images,
   clear all;
3. **CSS**: the search box is the only flexible element in its row (the main entry point for
   1500+ tags).
"""
from __future__ import annotations

import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
WEB = REPO / "src" / "kohya_dataset_tagger" / "web"
JS = WEB / "js"
FILTER = JS / "filter.js"
APP = JS / "app.js"
STRINGS = JS / "locales" / "zh-CN.js"
HTML = WEB / "index.html"
CSS = WEB / "css" / "app.css"


def text_of(path: Path) -> str:
    assert path.is_file(), "cannot find %s" % path
    return path.read_text(encoding="utf-8")


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
# 1. Wiring
# ---------------------------------------------------------------------------


def test_filter_controls_exist_in_the_markup() -> None:
    markup = text_of(HTML)
    for element_id in (
        "filter-search",
        "filter-sort",
        "filter-selection",
        "filter-scope-visible",
        "btn-filter-clear-all",
    ):
        assert 'id="%s"' % element_id in markup, "index.html is missing #%s" % element_id


def test_app_hands_every_control_to_the_panel() -> None:
    body = text_of(APP)
    for key in ("search:", "sort:", "selection:", "visibleScope:"):
        assert key in body, "app.js does not hand %s to createFilterPanel" % key
    assert 'strip: $("gallery-filter")' in body, (
        "the panel is not handed the strip's host, so the conditions can never be hidden again"
    )
    assert "isSelected: (path) => gallery.isSelected(path)" in body, (
        "the selection filter is not wired to the gallery selection state (gallery.isSelected is the O(1) entry point)"
    )
    assert '$("btn-filter-clear").addEventListener("click", () => filterPanel.clearTags())' in body, (
        "\"Clear filter\" is not wired"
    )
    assert '$("btn-filter-clear-all").addEventListener("click", () => filterPanel.clearAll())' in body, (
        "\"Clear all\" is not wired"
    )


def test_selection_filter_reapplies_on_selection_change() -> None:
    """When the selection set changes it only needs recomputing if it is one of the filter layers."""
    assert "if (filterPanel.hasSelectionFilter()) applyFilter();" in text_of(APP), (
        "with the selection filter on, changing the selection does not recompute the visible images"
    )
    assert "hasSelectionFilter" in text_of(FILTER), "the panel does not expose hasSelectionFilter"


def test_visible_scoped_frequency_is_a_reason_to_probe_captions() -> None:
    """\"Frequency counts only the currently shown images\" needs each image's tags, so it has to trigger caption probing too."""
    body = text_of(APP)
    assert "filterPanel.hasFilter() || filterPanel.isVisibleScope()" in body, (
        "turning on visible-image frequencies does not trigger caption loading, so the table stays empty"
    )


def test_pure_helpers_are_exported_for_the_harness() -> None:
    body = text_of(FILTER)
    for name in ("export function matchesFilter", "export function countTags", "export function selectTagRows",
                 "export function cycleState", "export const FILTER_SORT"):
        assert name in body, "filter.js is missing %s" % name


def test_result_rows_are_capped_after_the_search() -> None:
    """Search must come before truncation: truncating first makes rare tags permanently unreachable."""
    body = text_of(FILTER)
    assert "rows.slice(0, MAX_FREQUENCY_ROWS)" in body, "the frequency table has no row cap"
    assert "selectTagRows(all, search, sortMode)" in body, "the render path does not go through search/sort first"


def test_filter_strings_exist() -> None:
    keys = text_of(STRINGS)
    for key in (
        "filter.searchPlaceholder",
        "filter.searchEmpty",
        "filter.sortCount",
        "filter.sortName",
        "filter.onlySelected",
        "filter.scopeVisible",
        "filter.clearAll",
    ):
        assert '"%s"' % key in keys, "strings.js is missing %s" % key


# ---------------------------------------------------------------------------
# 2. CSS
# ---------------------------------------------------------------------------


def test_search_box_is_the_flexible_part_of_its_row() -> None:
    css = text_of(CSS)
    search = rule(css, "#filter-search")
    assert "flex: 1 1" in search, "the search box is not the flexible element in its row"
    assert "min-width: 0" in search, "the search box has no min-width: 0, so a long placeholder bursts it"
    select = rule(css, "#filter-sort")
    assert "flex: 0 0 auto" in select, "the sort dropdown should not stretch along"


# ---------------------------------------------------------------------------
# 3. Behavior: really run filter.js (fake DOM, no jsdom)
# ---------------------------------------------------------------------------


def test_filter_behaves_headlessly_against_a_fake_dom() -> None:
    """Three-state combinations, search before truncation, selection-set intersection, visible-image frequencies, clear all."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("no node on this machine")
    harness = REPO / "test" / "fixtures" / "filter_harness.mjs"
    assert harness.is_file(), "missing headless behavior fixture %s" % harness
    done = subprocess.run(
        [node, str(harness), str(WEB)],
        capture_output=True, encoding="utf-8", errors="replace",
    )
    assert done.returncode == 0, "filter panel behavior criteria failed:\n%s\n%s" % (done.stdout, done.stderr)
    assert "filter harness OK" in done.stdout

# --- Missing-caption mode (2026-09-20) --------------------------------------
# Tag filtering and "select images missing a caption" govern disjoint sets of
# images: one talks about images that have captions, the other about the ones
# that do not. They must not be able to disagree about what is selected, so
# entering the mode is the mutual exclusion, and the selection stays a subset
# of what is on screen.


def _web(*parts):
    return Path(__file__).resolve().parents[1].joinpath("src", "kohya_dataset_tagger", "web", *parts)


def test_the_button_enters_missing_mode_instead_of_selecting_over_the_filter():
    app = _web("js", "app.js").read_text(encoding="utf-8")
    # The action moved into its own function on 2026-09-20 (the readiness strip's caption segment runs
    # it too), so the order is read where the action now lives - and the wiring is pinned separately,
    # or the criterion would pass on an ordering nothing calls.
    assert '$("btn-select-missing").addEventListener("click", () => { selectMissingCaptions(); });' in app, (
        "the toolbar button no longer runs the shared select-missing action"
    )
    handler = app[app.index("function selectMissingCaptions() {"):]
    handler = handler[: handler.index("\n}\n")]
    assert "filterPanel.setMissingOnly(true)" in handler
    assert handler.index("setMissingOnly(true)") < handler.index("applyFilter()"), (
        "the mode must be set before the filter recomputes what is visible"
    )
    assert handler.index("applyFilter()") < handler.index("gallery.selectAll()"), (
        "selecting must follow the recomputed visible set, never precede it"
    )
    assert "selectMissing()" not in app, "the old select-across-the-directory call is still wired"
    assert "isMissing: (path) => missingPaths.has(path)" in app


def test_the_panel_is_told_which_images_have_no_caption():
    source = _web("js", "filter.js").read_text(encoding="utf-8")
    assert "options.isMissing" in source
    assert "setMissingOnly" in source and "hasMissingFilter" in source
    assert "missingOnly" in source


def test_a_caption_less_image_is_not_an_image_that_lacks_a_tag():
    source = _web("js", "filter.js").read_text(encoding="utf-8")
    assert "hasTagConditions(conditions)" in source


def test_every_pack_can_name_the_mode():
    for name in ("zh-CN", "en", "ja"):
        text = _web("js", "locales", name + ".js").read_text(encoding="utf-8")
        assert '"filter.modeMissing"' in text, name


# ---------------------------------------------------------------------------
# 4. The gallery filter strip (2026-09-20): the conditions belong where their
#    effect is. The chips and the count moved out of the Filter tab's panel into
#    the center panel, one row above the grid they narrow, and the missing-caption
#    mode and "show selected images only" became conditions of the same strip.
# ---------------------------------------------------------------------------

#: HTML void elements: they never open a scope the nesting walker has to close.
VOID_TAGS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
})


def ancestors_of(html: str, element_id: str) -> list:
    """The ids on the path from the root down to #element_id.

    A claim like "the chips are on the grid's panel" is only worth asserting against
    the real nesting: `'id="x"' in html` passes just as happily when the element sits
    inside the very thing it must not sit inside - the same class of mistake as
    asserting a CSS selector without its declaration.
    """
    path: list = []

    class Walker(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.stack: list = []
            self.found = False

        def handle_starttag(self, tag, attrs):
            attributes = dict(attrs)
            identifier = attributes.get("id")
            if identifier == element_id:
                path.extend(name for _, name in self.stack if name)
                self.found = True
            if tag not in VOID_TAGS:
                self.stack.append((tag, identifier))

        def handle_startendtag(self, tag, attrs):
            self.handle_starttag(tag, attrs)
            if tag not in VOID_TAGS:
                self.stack.pop()

        def handle_endtag(self, tag):
            if tag in VOID_TAGS:
                return
            while self.stack:
                if self.stack.pop()[0] == tag:
                    break

    walker = Walker()
    walker.feed(html)
    assert walker.found, "index.html has no element with id %r" % element_id
    return path


def test_the_conditions_are_next_to_the_grid_they_narrow() -> None:
    """The strip lives on the grid's panel, and a gallery re-render cannot eat it.

    The second half is the reason it is a *sibling* of #gallery: gallery.js rebuilds
    that container with replaceChildren() on every navigation, so a strip inside it
    would be wiped by the next directory change.
    """
    html = text_of(HTML)
    strip_path = ancestors_of(html, "gallery-filter")
    assert "center-panel" in strip_path, "#gallery-filter is not on the grid's panel: %s" % strip_path
    assert "gallery" not in strip_path, "#gallery-filter is inside #gallery: %s" % strip_path
    for element_id in ("filter-active", "filter-count"):
        path = ancestors_of(html, element_id)
        assert "gallery-filter" in path, "#%s is not in the strip: %s" % (element_id, path)
        assert html.count('id="%s"' % element_id) == 1, (
            "#%s has more than one host - two live copies of one filter state drift" % element_id
        )
    # The Filter tab keeps the controls that CHOOSE conditions, not the conditions in force.
    tab = html[html.index('<div class="tab-panel" data-panel="filter"'):html.index('data-panel="autotag"')]
    assert "filter-active" not in tab, "the Filter tab still renders the strip's chips"
    assert "filter-count" not in tab, "the Filter tab still renders the strip's count"


def test_the_strip_can_actually_be_hidden() -> None:
    """The strip is hidden by the [hidden] property, and a rule that sets `display` on
    #gallery-filter outranks the user agent's own [hidden] rule. The declaration that
    makes the hiding work is the !important on this file's [hidden] rule - so that is
    what this criterion reads. Asserting the selector alone is exactly the kind of
    criterion that stayed green last round while the thing it named did nothing.
    """
    css = text_of(CSS)
    strip = rule(css, "#gallery-filter")
    assert "display:" in strip, "#gallery-filter sets no display, so its hiding proves nothing"
    hidden = rule(css, "[hidden]")
    assert "display: none" in hidden, "[hidden] no longer hides anything"
    assert "!important" in hidden, (
        "without !important the UA's [hidden] rule loses to #gallery-filter's display, "
        "and the strip can never be hidden"
    )


def test_one_painter_owns_the_strip() -> None:
    """Every condition the grid is under is painted by one function into one host.

    A second painter - or a second host - is how the strip and the grid would start
    disagreeing about what is in force, which is the invariant this round is about.
    """
    body = text_of(FILTER)
    assert body.count("activeEl.replaceChildren()") == 1, (
        "the chip host is emptied in more than one place: the strip has more than one painter"
    )
    for marker in ('{ kind: "tag"', '{ kind: "missing"', '{ kind: "selection"'):
        assert marker in body, "paintActive() does not carry the %s condition" % marker
    assert "stripEl.hidden = conditions.length === 0" in body, (
        "the strip's visibility is not the same statement as 'there is a condition'"
    )


def test_no_pack_keeps_the_wording_of_an_empty_strip() -> None:
    """The strip is on screen exactly while a condition is, so nothing ever paints "no
    conditions": filter.none was deleted from all three packs instead of staying as dead
    copy that looks alive."""
    for name in ("zh-CN", "en", "ja"):
        text = _web("js", "locales", name + ".js").read_text(encoding="utf-8")
        assert '"filter.none"' not in text, "%s still carries the dead empty-strip key" % name


# ---------------------------------------------------------------------------
# 5. The gallery toolbar (2026-09-20): two rows, one grammar. The browse scope
#    left the page header for the row above the grid, because it changes what
#    the GRID contains - and this repository's own precedent says the grid's
#    controls live on the grid's panel, one row above it (#gallery-filter, and
#    before that the strip's whole argument). The left column was the tempting
#    wrong answer and is refuted by the contract: api/fs.py:5-12 keeps `dirs`
#    one level deep and each child's count non-recursive, so the scope never
#    changes those numbers and browse.leftHint stays true in both states.
# ---------------------------------------------------------------------------


def subtree_of(html: str, match) -> dict:
    """One element of index.html and what is inside it: its own attributes and text, its direct
    children, and every id below it.

    A claim like "the browse scope is the toolbar's first child" needs the real nesting - a
    substring search cannot tell a child from a grandchild, and `'id="recursive-toggle"' in header`
    is true wherever the label sits, including in the row it must not go back to.
    """
    children: list = []
    ids: list = []
    root_attrs: dict = {}
    root_text: list = []

    class Scanner(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.depth = 0
            self.found = False
            self.in_root = False
            self.root_depth = None
            self.current = None

        def handle_starttag(self, tag, attrs):
            attributes = dict(attrs)
            classes = (attributes.get("class") or "").split()
            if not self.found and match(tag, attributes, classes):
                self.found = True
                self.root_depth = self.depth
                root_attrs.update(attributes)
                if tag in VOID_TAGS:
                    return
                self.in_root = True
                self.depth += 1
                return
            if self.in_root:
                if self.depth == self.root_depth + 1:
                    self.current = {"tag": tag, "id": attributes.get("id") or "",
                                    "class": classes, "attrs": attributes, "text": [], "ids": []}
                    children.append(self.current)
                if attributes.get("id"):
                    ids.append(attributes["id"])
                    if self.current is not None:
                        self.current["ids"].append(attributes["id"])
            if tag in VOID_TAGS:
                return
            self.depth += 1

        def handle_data(self, data):
            if self.in_root:
                root_text.append(data)
                if self.current is not None:
                    self.current["text"].append(data)

        def handle_endtag(self, tag):
            if tag in VOID_TAGS:
                return
            self.depth -= 1
            if self.in_root and self.depth == self.root_depth:
                self.in_root = False
                self.current = None

    scanner = Scanner()
    scanner.feed(html)
    assert scanner.found, "index.html has no element matching %r" % (match,)
    return {"children": children, "ids": ids, "attrs": root_attrs, "text": root_text}


def elements_with_class(html: str, css_class: str) -> list:
    """Every element carrying one class, with its own attributes and its own text.

    A claim about a *class* ("every use of .group-sep is decoration") has to be checked over the
    whole document: one announced or focusable use is exactly how a decorative convention decays,
    and the second use is the one nobody looks at.
    """
    found: list = []
    stack: list = []

    class Scanner(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)

        def handle_starttag(self, tag, attrs):
            attributes = dict(attrs)
            classes = (attributes.get("class") or "").split()
            record = None
            if css_class in classes:
                record = {"tag": tag, "attrs": attributes, "text": []}
                found.append(record)
            if tag not in VOID_TAGS:
                stack.append((tag, record))

        def handle_startendtag(self, tag, attrs):
            self.handle_starttag(tag, attrs)
            if tag not in VOID_TAGS:
                stack.pop()

        def handle_data(self, data):
            for _tag, record in reversed(stack):
                if record is not None:
                    record["text"].append(data)
                    break

        def handle_endtag(self, tag):
            if tag in VOID_TAGS:
                return
            while stack:
                if stack.pop()[0] == tag:
                    break

    Scanner().feed(html)
    return found


def toolbar_subtree() -> dict:
    return subtree_of(text_of(HTML), lambda tag, attrs, classes: attrs.get("id") == "gallery-toolbar")


def toolbar_child(child: dict) -> str:
    """What one toolbar child *is*, from its own id/classes and the ids nested inside it."""
    if child["id"]:
        return "#" + child["id"]
    if "group-sep" in child["class"]:
        return ".group-sep"
    if "spacer" in child["class"]:
        return ".spacer"
    if "recursive-toggle" in child["ids"]:
        return "recursive-toggle"
    return "<%s class=%r>" % (child["tag"], " ".join(child["class"]))


def test_the_browse_scope_is_the_gallery_toolbars_first_child() -> None:
    """The row is "scope | state ....... actions", and the scope leads it.

    Read as nesting: the first child must be the label that *wraps* #recursive-toggle, not a node
    that merely mentions it - and #recursive-toggle must exist exactly once in the whole page,
    because two live copies of one scope would drift.
    """
    html = text_of(HTML)
    assert html.count('id="recursive-toggle"') == 1, (
        "the browse scope exists in %d places; one live copy is the invariant"
        % html.count('id="recursive-toggle"')
    )
    children = toolbar_subtree()["children"]
    first = children[0]
    assert first["tag"] == "label" and "field-inline" in first["class"] and "recursive-toggle" in first["ids"], (
        "the gallery toolbar's first child is <%s class=%r ids=%s>; the browse scope must lead the "
        "row and wrap its own checkbox" % (first["tag"], " ".join(first["class"]), first["ids"])
    )


def test_the_toolbar_reads_scope_separator_state_then_its_actions() -> None:
    """The row's order, exactly: scope | state ....... actions.

    The separator is between the scope and the state readout, and the action group sits behind the
    spacer, pushed to the row's right end - refresh first (it re-fetches the listing the grid is
    showing), then the three selection actions as one contiguous set.

    The separator is not decoration for its own sake: 6px of gap between a checkbox label and a
    dim 12px readout reads as one run of text ("include subdirectories 1 selected"), and the two
    are different claims about different things.
    """
    children = toolbar_subtree()["children"]
    identities = [toolbar_child(child) for child in children]
    assert identities == [
        "recursive-toggle", ".group-sep", "#selection-info", ".spacer",
        "#btn-refresh", "#btn-select-all", "#btn-select-missing", "#btn-clear-selection",
    ], "the gallery toolbar reads %s" % identities


def test_the_refresh_action_sits_on_the_row_it_refreshes() -> None:
    """刷新 re-fetches the listing the gallery is displaying, so it belongs to the grid's row.

    (The left column's directory list is repainted by the very same call, which is why there is no
    second refresh button anywhere.) The page header keeps identity, state and the two preferences;
    a control that acts on the listing sits where the listing is - the same rule that moved the
    browse scope here. The ancestry is read from the real nesting, so moving it back to the header
    turns this red.

    Icon only, and the words survive as the accessible name and the tooltip - the globe's pattern.
    A glyph that *becomes* the accessible name tells a screen reader nothing (the chip-remove
    lesson), so the svg is aria-hidden and unfocusable and the control is still named by
    browse.refresh.
    """
    html = text_of(HTML)
    path = ancestors_of(html, "btn-refresh")
    assert "gallery-toolbar" in path, "#btn-refresh is not on the grid's own row: %s" % path
    header = subtree_of(html, lambda tag, attrs, classes: "topbar" in classes)
    assert "btn-refresh" not in header["ids"], "the refresh action is back in the page header"

    button = subtree_of(html, lambda tag, attrs, classes: attrs.get("id") == "btn-refresh")
    attrs = button["attrs"]
    assert attrs.get("data-copy-aria") == "browse.refresh", (
        "the icon button has no accessible name: a glyph-shaped name is worse than none"
    )
    assert attrs.get("data-copy-title") == "browse.refresh", "the icon button has no tooltip"
    assert "data-copy" not in attrs, (
        "the button carries visible words again (data-copy): the icon is named through data-copy-aria"
    )
    assert "".join(button["text"]).strip() == "", "the icon button carries text of its own"
    glyph = button["children"][0]
    assert glyph["tag"] == "svg", "the refresh action is not an icon: <%s>" % glyph["tag"]
    assert glyph["attrs"].get("aria-hidden") == "true", (
        "the glyph is announced, so the control has two names"
    )
    assert glyph["attrs"].get("focusable") == "false", "the svg is a focus stop in some engines"
    assert "field-icon" in glyph["class"], "the glyph does not use the header globe's icon sizing"


def test_the_toolbar_wraps_instead_of_clipping_its_controls() -> None:
    """The row must never lose a control, and the panel it lives in does not scroll sideways.

    Measured with the width the wide packs need: at a 900px viewport the centre panel is 278px, and
    in en/ja the scope label, the state readout, the refresh icon and the three selection buttons do
    not fit on one line. Without wrapping the flex container squeezes and then overflows, and
    `.panel-center`'s overflow: hidden clips the last buttons out of reach (measured before the fix:
    the last button's right edge 158.7px past the row's). Wrapping moves them onto the next line
    instead - it costs the gallery height, which is the trade this criterion records.
    """
    toolbar = rule(text_of(CSS), ".toolbar")
    assert "flex-wrap: wrap" in toolbar, (
        "the toolbar cannot wrap, so a narrow centre panel clips whatever does not fit"
    )
    assert "overflow: hidden" in rule(text_of(CSS), ".panel-center"), (
        "the panel no longer clips, so this criterion's reason moved - re-derive it before relaxing it"
    )


def test_the_browse_scope_is_not_in_the_page_header() -> None:
    """The scope acts on the grid, and the header is not the grid's row: moving it back - or
    leaving a second copy there - turns this red. "含子目录 belongs next to the numbers it
    changes" was checked against the contract and is false: api/fs.py:5-12 lists only direct
    subdirectories, each with its own non-recursive image count, so the toggle changes no number
    in the left column and browse.leftHint keeps saying exactly that.
    """
    header = subtree_of(text_of(HTML), lambda tag, attrs, classes: "topbar" in classes)
    assert "recursive-toggle" not in header["ids"], (
        "the browse scope is back inside the page header: %s" % header["ids"]
    )


def test_the_group_separator_is_one_decorative_class() -> None:
    """One class draws the boundary between a row's groups, and **every** use of it is decoration.

    Today the only user is the gallery toolbar (scope | state ....... actions). The page header has
    none - once the listing's refresh moved to the grid's own row, its remaining controls are all
    "the controls at the right end", and a boundary needs two sides - and this criterion must not
    require one there. What it does require is the property that makes the class reusable: a
    separator is not a control (aria-hidden, no text, nothing focusable), and it is painted from the
    theme's own line token, so a colour literal cannot smuggle a second theme-dependent value in
    below the token blocks.
    """
    html = text_of(HTML)
    separators = elements_with_class(html, "group-sep")
    assert separators, "the .group-sep class is not used anywhere"
    for separator in separators:
        assert separator["tag"] == "span", "a separator is a <%s>" % separator["tag"]
        assert separator["attrs"].get("aria-hidden") == "true", (
            "a .group-sep is announced: a boundary is not a control (%s)" % separator["attrs"]
        )
        assert "tabindex" not in separator["attrs"] and "role" not in separator["attrs"], (
            "a .group-sep is focusable or carries a role: %s" % separator["attrs"]
        )
        assert "".join(separator["text"]).strip() == "", "a .group-sep carries text"

    toolbar = toolbar_subtree()
    in_toolbar = [child for child in toolbar["children"] if "group-sep" in child["class"]]
    assert len(in_toolbar) == 1, "the gallery toolbar has %d separators" % len(in_toolbar)

    css = rule(text_of(CSS), ".group-sep")
    assert "flex: 0 0 auto" in css, "the separator can absorb the row's slack, so the spacer stops working"
    assert "background: var(--line)" in css, "the separator is not painted from the theme's line token"
    assert "width: 1px" in css, "the separator is not a 1px rule"
