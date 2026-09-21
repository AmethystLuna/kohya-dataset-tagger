"""The column's one scope control: `web/js/scope.js` + the `#scope-strip` in the right column.

The processing scope ("selected images / this directory / this directory + subdirectories / filter
result") was the same four-way question in two panels, each with its **own** value: a user who picked
"selected: 12 images" in the tagger and then pressed the batch panel's button acted on a different set
of images. One question, two answers.

The fix is a hoist, the way the caption dock and the gallery filter strip were done: the control moves
to where its effect is (the column, above the tab panels), exactly **one** live copy of the value
exists, and the panels subscribe to it. `scope.js` stays the single implementation - no second scope
model, and nothing about what a scope means changed.

What the criteria have to pin down, in the order the risk is real:

1. **Structure**: the strip is in the right column and outside `.tab-panels` (the only scrolling
   region - a control that scrolls out of reach is not shared), and there is one `#scope-select`.
2. **One value**: exactly one `createScopeControl(` call site in the whole frontend, in `app.js`,
   handed to every panel that acts on images (tagger, batch, crop, scale); no panel builds its own.
3. **Nothing silently lost**: the three hooks that existed before the hoist (the batch preview
   invalidation, the tagger's diff-stale + button gating, the counts repaint) are still attached.
   The behavioural half of each is in `test/fixtures/scope_strip_harness.mjs`, which builds both
   panels over one control.
4. **Blast radius**: every panel that acts on the grid acts on **this** value. The export was the
   last exception - its endpoint took a directory and walked it recursively - and it was resolved by
   changing the endpoint (`ScaleRequest.images`, 2026-09-20), not by excusing the panel: a control
   that silently does not apply is the defect, and a sentence admitting it is not a fix.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
WEB = REPO / "src" / "kohya_dataset_tagger" / "web"
JS = WEB / "js"
LOCALES = JS / "locales"
APP = JS / "app.js"
BATCH = JS / "batch.js"
AUTOTAG = JS / "autotag.js"
SCALE = JS / "scale.js"
SCOPE = JS / "scope.js"
HTML = WEB / "index.html"
CSS = WEB / "css" / "app.css"
HARNESS = REPO / "test" / "fixtures" / "scope_strip_harness.mjs"

#: HTML void elements: they never open a scope the nesting walker has to close.
VOID_TAGS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
})


def text_of(path: Path) -> str:
    assert path.is_file(), "cannot find %s" % path
    return path.read_text(encoding="utf-8")


def ancestry(html: str, element_id: str) -> list:
    """The tag/id/class tokens on the path from the root down to #element_id.

    "the strip is in the right column, not inside the scrolling panels" is a claim about the real
    nesting: a substring test would pass just as happily with the strip inside `.tab-panels`,
    which is the one place it must not be.
    """
    path: list = []

    class Walker(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.stack: list = []
            self.found = False

        def handle_starttag(self, tag, attrs):
            attributes = dict(attrs)
            if attributes.get("id") == element_id:
                path.extend(self.stack)
                self.found = True
            if tag not in VOID_TAGS:
                tokens = [tag]
                if attributes.get("id"):
                    tokens.append(attributes["id"])
                tokens.extend((attributes.get("class") or "").split())
                self.stack.append(tokens)

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


def pack(name: str) -> str:
    return text_of(LOCALES / (name + ".js"))


# ---------------------------------------------------------------------------
# 1. Structure: where the strip is
# ---------------------------------------------------------------------------


def test_the_strip_is_in_the_column_and_outside_the_scrolling_panels() -> None:
    """The tab panels are the column's scroller; a control inside them disappears on a long panel.

    The dock and the filter strip were moved for the same reason: the control belongs where its
    effect is, and "on every tab" is only true while it is not inside the region that scrolls.
    """
    html = text_of(HTML)
    path = ancestry(html, "scope-strip")
    flat = [token for frame in path for token in frame]
    assert "panel-right" in flat, "#scope-strip is not in the right column: %s" % path
    assert "tab-panels" not in flat, "#scope-strip is inside the scrolling tab panels: %s" % path
    assert not any("tab-panel" in frame for frame in path), (
        "#scope-strip sits inside a tab panel, so it disappears on every other tab: %s" % path
    )


def test_there_is_exactly_one_scope_select_in_the_markup() -> None:
    html = text_of(HTML)
    assert html.count('id="scope-select"') == 1, (
        "a second #scope-select is a second value: two live copies of one answer drift"
    )
    assert 'data-copy="scope.label"' in html, "the strip's select has no label"
    autotag_panel = html[html.index('data-panel="autotag"'):html.index('data-panel="cache"')]
    assert "scope-select" not in autotag_panel, (
        "the tagger's panel still owns a scope select; the value belongs to the column"
    )
    assert 'id="autotag-scope"' not in html, "the old per-panel scope select is still in the markup"


# ---------------------------------------------------------------------------
# 2. One value: one construction site
# ---------------------------------------------------------------------------


def test_the_control_is_constructed_once_for_the_whole_frontend() -> None:
    """The count *is* the invariant: one control, one value, one set of live counts.

    Two construction sites are two answers to "which images?" again - even over one <select>,
    because each instance keeps its own listeners and its own cached recursive listing. This is
    what the old `app.count("...scopeProviders,") == 2` criterion was reaching for; that count
    described the two panels each building a control, which is the defect.
    """
    definition = SCOPE
    assert "export function createScopeControl(options)" in text_of(definition), (
        "scope.js no longer defines the shared control"
    )
    sites = {}
    for path in sorted(JS.rglob("*.js")):
        if path == definition or "locales" in path.parts:
            continue
        body = text_of(path)
        if "createScopeControl(" in body:
            sites[path.name] = body.count("createScopeControl(")
    assert sites == {"app.js": 1}, (
        "the frontend must build the scope control exactly once, in app.js - got %s. A panel that "
        "builds its own acts on its own value, which is the defect this round removes." % sites
    )


def test_every_panel_that_acts_on_images_takes_the_same_instance() -> None:
    app = text_of(APP)
    assert "const scope = createScopeControl({" in app, "app.js does not build the control"
    assert "providers: scopeProviders," in app, "the control does not read the shared providers"
    # Every factory gets the same identifier: the panels cannot disagree about the value if there
    # is only one value for them to read.
    for factory in (
        "const batchPanel = createBatchPanel({",
        "const autotagPanel = createAutotagPanel({",
        "const cropPanel = createCropPanel({",
        "const scalePanel = createScalePanel({",
    ):
        start = app.index(factory)
        call = app[start:app.index("\n});", start)]
        assert re.search(r"^  scope,$", call, re.M), (
            "%s does not receive the shared scope control" % factory
        )

    batch = text_of(BATCH)
    assert "const scope = config.scope;" in batch, "the batch panel does not take the shared control"
    assert "scope.subscribe({" in batch, "the batch panel never attaches its reactions"
    assert "const paths = scope.paths();" in batch, "the batch request does not read the shared value"
    assert "const paths = getVisiblePaths();" not in batch, (
        "a request path still reads the visible set directly, so the control can be ignored"
    )

    autotag = text_of(AUTOTAG)
    assert "const scope = options.scope;" in autotag, "the tagger does not take the shared control"
    assert "scope.subscribe({" in autotag, "the tagger never attaches its reactions"
    assert "return scope.paths();" in autotag, "the tagger's scope paths do not come from the control"

def test_a_listing_is_an_answer_about_one_directory_only() -> None:
    """The recursive cache is keyed by directory, and dropped when that directory is re-listed.

    Two defects this pins, both of them "the control describes a tree that is not the one on
    screen": the cache outliving its directory - after navigating, "directory + subdirectories"
    went on handing out the images of the folder the user had just left, which every panel acting
    on the scope (tagger, batch, crop, export) then acted on - and the cache surviving a
    re-listing of the *same* directory, so a crop's archive beside its source stayed invisible to
    the count and to anything exporting that scope until the user navigated away and back. The
    behavioural half is section 6 of `scope_strip_harness.mjs`.
    """
    scope_js = text_of(SCOPE)
    assert "function cachedRecursive()" in scope_js, (
        "the cached listing is used without asking which directory it describes"
    )
    assert "recursive.paths && recursive.dir === getDirKey()" in scope_js, (
        "the cached listing is still trusted whatever directory it came from"
    )
    assert "if (value() === SCOPE.DIR_RECURSIVE) return cachedRecursive() || [];" in scope_js, (
        "the recursive scope can still hand out another directory's images"
    )
    assert "function invalidate()" in scope_js, "the control cannot be told that a listing changed"
    app = text_of(APP)
    body = app[app.index("async function openDir("):]
    body = body[: body.index("\n}\n")]
    assert "scope.invalidate();" in body, (
        "a fresh listing does not drop the cached one, so a write into the open directory stays "
        "invisible until the user navigates away and back"
    )


# ---------------------------------------------------------------------------
# 3. The hooks the hoist had to keep
# ---------------------------------------------------------------------------


def test_the_batch_panel_still_drops_its_preview_when_the_scope_changes() -> None:
    batch = text_of(BATCH)
    subscription = batch[batch.index("scope.subscribe({"):]
    subscription = subscription[: subscription.index("\n  });")]
    assert "onChange: () => {" in subscription, "the panel does not react to a new value at all"
    assert "invalidatePreview();" in subscription, (
        "a scope change keeps a preview that describes the previous set of images"
    )
    assert "onScopeChange();" in subscription, (
        "the app is no longer told, so the captions of the new scope are never probed"
    )


def test_the_counts_are_repainted_even_while_the_batch_tab_is_closed() -> None:
    """The strip is on screen on every tab, so the filter pass has to repaint it from any tab.

    `refreshScope()` deliberately does no work while the panel is hidden (the gallery re-applies
    the filter once per caption chunk); the counts are the part that must not be skipped, or the
    strip keeps showing the count of a filter that has already changed.
    """
    batch = text_of(BATCH)
    body = batch[batch.index("function refreshScope() {"):]
    body = body[: body.index("\n  }\n")]
    paint = body.index("scope.paint();")
    guard = body.index("if (panel.hidden) {")
    assert paint < guard, (
        "the shared counts are only repainted while the batch tab is open; the strip is on screen "
        "on every tab"
    )


def test_the_tagger_keeps_its_diff_stale_check_and_its_gating() -> None:
    autotag = text_of(AUTOTAG)
    subscription = autotag[autotag.index("scope.subscribe({"):]
    subscription = subscription[: subscription.index("\n  });")]
    assert "onPaint: () => { refreshDiffStale(); refreshActionButtons(); }," in subscription, (
        "a directory or selection change never reaches the staleness check (scope.js paints the"
        " counts and the panel's paint hook is what says the diff is now about other images)"
    )
    # The count-shaped half: the button gating reads the control's own count of images, not a
    # remembered list, which is what makes one value drive both panels.
    assert "runButton.disabled = scopePaths().length <= PREVIEW_MAX_IMAGES;" in autotag, (
        "Run is no longer gated by the size of the scope the control holds"
    )


def test_the_strip_is_repainted_on_a_language_switch() -> None:
    """The option labels carry the live counts, so they are render-time copy.

    `applyCopy()` only reaches the static data-copy nodes; the <option> text is written by
    scope.js:paint(). Without the registration the whole strip stays in the previous language -
    and it is now on screen permanently.
    """
    app = text_of(APP)
    body = app[app.index("async function relabelAfterLocaleChange() {"):]
    body = body[: body.index("\n}\n")]
    assert "scope.paint();" in body, (
        "relabelAfterLocaleChange() does not repaint the strip's options"
    )
    for name in ("zh-CN", "en", "ja"):
        text = pack(name)
        for key in ("scope.label", "scope.stripHint"):
            assert '"%s"' % key in text, "%s is missing %s" % (name, key)


def test_the_strip_stays_put_in_the_flex_column() -> None:
    """The declaration that keeps the strip from being squeezed by the pinned dock.

    Asserting the selector alone is the criterion that stayed green last round while the rule it
    named did nothing; this reads the declarations.
    """
    css = text_of(CSS)
    strip = rule(css, ".scope-strip")
    assert "flex: 0 0 auto" in strip, (
        "the strip may shrink: .caption-dock can take half the column, and a squeezed strip is not "
        "a readable answer to 'what am I about to act on'"
    )
    panels = rule(css, ".tab-panels")
    assert "overflow-y: auto" in panels, (
        ".tab-panels is no longer the scrolling region, so 'outside it' no longer means anything"
    )


# ---------------------------------------------------------------------------
# 4. The export obeys it too (2026-09-20: the endpoint takes the list now)
# ---------------------------------------------------------------------------


def test_the_export_panel_obeys_the_shared_scope() -> None:
    """p0-spec section 4.14: POST /api/scale/run takes an image list (ScaleRequest.images).

    Until 2026-09-20 it took a `root` and the endpoint walked it recursively, so the export could
    not obey "selected images" or "the filter result" - and the panel printed a sentence saying the
    control did not apply. The endpoint takes the list now, so the sentence is gone and the control
    is the answer: the panel reads the same instance as everybody else, sends the list it reports,
    and follows it live (the strip sits above the tabs, so it can be changed while this tab is open).
    """
    scale = text_of(SCALE)
    assert "const scope = config.scope || null;" in scale, (
        "the export panel is not given the column's scope control"
    )
    assert "return scope.paths() || [];" in scale, (
        "the export panel does not read the images from the shared control"
    )
    assert "images: Array.isArray(source.images) ? source.images.map" in scale, (
        "the request builder drops the scope's list, so the export has no extent again"
    )
    assert "\n      images,\n      root,\n" in scale, (
        "the run does not hand the scope's snapshot to the request"
    )
    assert "scope.subscribe(" in scale, (
        "the panel reads the count once instead of following the control"
    )
    assert 'paintText(make("p", "hint"), "scale.coverage")' not in scale, (
        "the export still states a coverage line instead of obeying the control"
    )
    assert "\n    sourceLine,\n    scopeLine,\n" in scale, (
        "the scope reason is built but never put on the panel"
    )
    for name in ("zh-CN", "en", "ja"):
        text = pack(name)
        assert '"scale.coverage"' not in text, "%s still ships the coverage sentence" % name
        assert re.search(r'"scale\.scopeNone":\s*"[^"]+"', text), (
            "%s ships no reason for an empty scope" % name
        )
    app = text_of(APP)
    assert "  scope,\n  getRoot: () => state.dir,\n" in app, (
        "app.js does not hand the shared control to the scale panel"
    )
    assert "if (status.scope === 0) return REASON.noScope;" in app, (
        "the palette still blames the target folder when the empty half is the scope"
    )


# ---------------------------------------------------------------------------
# 5. Behavior: really run both panels over one control
# ---------------------------------------------------------------------------


def test_one_scope_change_reaches_both_panels_headlessly() -> None:
    """The criterion the whole round exists for: change the value once, watch both panels obey.

    A static test can only see that both panels mention a scope. This runs the real scope.js, the
    real batch.js and the real autotag.js over one control built the way app.js builds it.
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("no node on this machine")
    assert HARNESS.is_file(), "missing headless behavior fixture %s" % HARNESS
    done = subprocess.run(
        [node, str(HARNESS), str(WEB)],
        capture_output=True, encoding="utf-8", errors="replace",
    )
    assert done.returncode == 0, "scope strip behavior criteria failed:\n%s\n%s" % (done.stdout, done.stderr)
    assert "scope strip harness OK" in done.stdout
