"""The readiness strip (T1.4): the one place that answers "can I train yet?".

Three surfaces already knew one third of the answer each - the top bar's census line (missing
captions), the cache tab's status line (stale text-encoder caches) and the export panel's checks
(dataset.toml). The strip puts all three on screen on every tab, and each segment is the action that
fixes it.

What these criteria have to pin down, in the order the risk is real:

1. **Agreement, not display.** Every number on the strip is the number the owning surface already
   shows, read from the same value: the strip must not fetch, count or recompute. The sharpest case is
   the cache: one function (cacheStatusText) is the only place "how many are stale" is worded, and
   both the tab and the strip paint through it. Making either of them count for itself must go red.
2. **Where it is.** A page-header row: outside the three columns, outside the tab panels, outside the
   right column's strips - that is what "on every tab" and "does not fight the dock" mean.
3. **Failure states.** No directory, an unknown caption count, a failed cache status, a failed
   dataset.toml status - the strip may report nothing, but never a zero nobody measured and never a
   file state nobody read.
4. **The licence to be there.** The header row costs the columns height; the layout had to stop being
   sized by a hard-coded "100% - 45px" for that to be safe.
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
READINESS = JS / "readiness.js"
HTML = WEB / "index.html"
CSS = WEB / "css" / "app.css"
HARNESS = REPO / "test" / "fixtures" / "readiness_harness.mjs"

#: The strip's own copy, one key per segment state.
SEGMENT_KEYS = (
    "readiness.captionsMissing",
    "readiness.captionsOk",
    "readiness.tomlUnchecked",
    "readiness.tomlMissing",
    "readiness.tomlUnread",
    "readiness.tomlInconsistent",
    "readiness.tomlReady",
)

#: The three panels' keys the strip reuses rather than restating (the same words, the same numbers).
REUSED_KEYS = ("cache.statusLine", "cache.loadFailed", "cache.progress", "export.warningsTitle", "export.skippedTitle")

VOID_TAGS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
})


def text_of(path: Path) -> str:
    assert path.is_file(), "cannot find %s" % path
    return path.read_text(encoding="utf-8")


def ancestry(html: str, element_id: str) -> list:
    """The tag/id/class tokens on the path from the root down to #element_id.

    "in the page header, not in a column" is a claim about the real nesting; a substring test would
    pass just as happily with the strip inside .panel-right.
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


def function_body(source: str, signature: str) -> str:
    """One top-level function declaration's text, from its signature to its closing brace."""
    assert signature in source, "app.js has no %s" % signature
    start = source.index(signature)
    end = source.index("\n}\n", start)
    return source[start:end]


def nested_body(source: str, signature: str) -> str:
    """One function declaration **inside** createReadinessStrip, from its signature to its closing brace.

    Its painters are indented by two spaces, so the top-level helper above (which closes on "\n}\n")
    would swallow the rest of the factory - and a criterion that reads half the module proves nothing.
    """
    assert signature in source, "readiness.js has no %s" % signature
    start = source.index(signature)
    end = source.index("\n  }\n", start)
    return source[start:end]


def pack(name: str) -> str:
    return text_of(LOCALES / (name + ".js"))


# ---------------------------------------------------------------------------
# 1. Where the strip is
# ---------------------------------------------------------------------------


def test_the_strip_is_a_page_header_row_outside_every_panel() -> None:
    """On every tab, and out of the way of the columns.

    Inside .panel-right it would fight the scope strip and the caption dock for height and could be
    scrolled away; inside a .tab-panel it would appear on one tab only, which is the defect the three
    previous rounds each removed somewhere else.
    """
    path = ancestry(text_of(HTML), "readiness")
    flat = [token for frame in path for token in frame]
    assert "topbar" in flat, "#readiness is not in the page header: %s" % path
    for forbidden in ("layout", "panel-left", "panel-right", "center-panel", "scope-strip",
                      "caption-dock", "tab-panels", "tabs"):
        assert forbidden not in flat, "#readiness sits inside .%s: %s" % (forbidden, path)
    assert not any("tab-panel" in frame for frame in path), (
        "#readiness sits inside a tab panel, so it disappears on every other tab: %s" % path
    )
    assert 'id="readiness"' in text_of(HTML)
    assert re.search(r'<div id="readiness" class="readiness" hidden>', text_of(HTML)), (
        "the strip must be built empty and hidden in the markup: it is painted from state, and "
        "without a directory there is no answer to give"
    )


def test_the_header_row_costs_the_columns_height_instead_of_overflowing() -> None:
    """The layout used to be sized by "100% - 45px" - a hard-coded header height that was already
    3px short of the real top bar and would have been wrong the moment the header gained a row.

    Measured in a real browser (tools/measure_web_layout.py): the top bar is 89px with the strip,
    48px without, and the layout ends exactly at the viewport in both cases.
    """
    css = text_of(CSS)
    body = rule(css, "body")
    assert "display: flex" in body and "flex-direction: column" in body, (
        "the page is not a column, so nothing tells the layout to take the height the header leaves"
    )
    layout = rule(css, ".layout")
    assert "flex: 1 1 auto" in layout, ".layout does not take the remaining height"
    assert "calc(100%" not in layout, (
        ".layout is sized by a hard-coded header height again; the readiness strip is a second "
        "header row and the number would be wrong"
    )
    topbar = rule(css, ".topbar")
    assert "flex: 0 0 auto" in topbar, "the header may be squeezed by the layout"
    assert "flex-wrap: wrap" in topbar, "the header cannot wrap, so the strip has nowhere to go"
    strip = rule(css, ".readiness")
    assert "flex: 1 1 100%" in strip, (
        ".readiness is not a full-width row of the header; it would be squeezed into the controls row"
    )


# ---------------------------------------------------------------------------
# 2. Agreement: every number is another surface's number
# ---------------------------------------------------------------------------


def test_the_census_line_and_the_strip_are_painted_from_one_counts_object() -> None:
    """One call, one value - and one statement of the missing-caption count.

    The count used to be printed twice in one header: "缺失 2" at the end of the census line and
    "缺失 caption 2 张" one row below it in the strip. Both surfaces are still painted from the same
    counts object in the same call - which is what keeps them from drifting - but the number itself
    is now stated once, as the action.
    """
    body = function_body(text_of(APP), "function renderCounts(counts, recursive) {")
    assert "readiness.setCounts(counts || null);" in body, (
        "the strip is not fed from renderCounts(), so its caption count comes from somewhere else"
    )
    assert "missing: counts.missing_captions || 0," not in body, (
        "the census line prints the missing-caption count again: one header, two rows, one fact"
    )
    for name in ("zh-CN", "en", "ja"):
        assert "{missing}" not in pack(name), (
            "%s still carries a {missing} placeholder that nothing fills - it would render literally"
            % name
        )


def test_the_toml_segment_claims_only_what_the_app_has_read() -> None:
    """The segment may say "no dataset.toml" **only** when a read-only stat said so (§4.16).

    This criterion used to forbid the "not generated" wording outright, because no endpoint could tell
    the app whether the file exists and the key *was* an unbacked claim. §4.16 made the claim
    answerable, so the criterion now pins the opposite property instead: the wording exists, it is
    painted from the read fact and from nowhere else, and "could not ask" is its own third state
    rather than either of the two claims. The "not checked yet" wording is still checked: it must
    keep saying *checked* and must not smuggle a file claim back in.
    """
    for name, check_word, forbidden in (
        ("zh-CN", "检查", "生成"),
        ("en", "checked", "generated"),
        ("ja", "確認", "生成"),
    ):
        found = re.search(r'"readiness\.tomlUnchecked":\s*"([^"]+)"', pack(name))
        assert found, "%s has no readiness.tomlUnchecked" % name
        value = found.group(1)
        assert check_word in value, (
            "%s: the segment does not say the check has not run: %r" % (name, value)
        )
        assert forbidden not in value, (
            "%s: %r claims something about the file on disk, but this is the 'no answer yet' state"
            % (name, value)
        )

    readiness = text_of(READINESS)
    paint = nested_body(readiness, "function paintToml() {")
    assert paint.count('t("readiness.tomlMissing")') == 1, (
        "the missing-file claim is painted from more than one place (or not at all): it belongs to the "
        "one branch where the fact says the file is not there"
    )
    not_exists = paint.index("if (!fileFact.exists)")
    missing_at = paint.index('t("readiness.tomlMissing")')
    gate_at = paint.index("samePath(")
    ready_at = paint.index('t("readiness.tomlReady")')
    assert not_exists < missing_at < gate_at < ready_at, (
        "the order of the segment's states changed: the fact must decide before the export panel's "
        'report is even looked at, and "ready" must be unreachable while the file is not there'
    )
    assert "return;" in paint[not_exists:gate_at], (
        "the not-exists branch does not return, so painting continues into the report/ready states"
    )
    failure = nested_body(readiness, "function paintCache() {")  # sanity: the helper really nests
    assert failure.startswith("function paintCache()"), failure[:40]

    # The failure state is its own wording: "could not ask" is never "not there" (the cache segment's
    # rule). Two different strings in every pack, or the states are indistinguishable on screen.
    for name in ("zh-CN", "en", "ja"):
        text = pack(name)
        missing = re.search(r'"readiness\.tomlMissing":\s*"([^"]+)"', text)
        unread = re.search(r'"readiness\.tomlUnread":\s*"([^"]+)"', text)
        assert missing and unread, "%s is missing one of the two file states" % name
        assert missing.group(1) != unread.group(1), (
            "%s paints the same sentence for 'the file is not there' and 'the check failed'" % name
        )


def test_the_cache_segment_and_the_cache_tab_share_one_number() -> None:
    """THE agreement criterion, statically.

    "3 possibly stale entries" is one claim about one number, so it is worded in one place
    (readiness.js:cacheStatusText) and painted from one view (app.js:cacheView). A second wording
    site - in either direction - is how the strip ends up disagreeing with the tab it sits above.
    """
    app = text_of(APP)
    readiness = text_of(READINESS)

    sites = []
    for path in sorted(JS.rglob("*.js")):
        if "locales" in path.parts:
            continue
        count = text_of(path).count('t("cache.statusLine"')
        if count:
            sites.append((path.name, count))
    assert sites == [("readiness.js", 1)], (
        "the stale-cache sentence must be defined exactly once, in readiness.js - found %s" % sites
    )
    assert 't("cache.loadFailed")' in readiness, (
        "the failure wording is not shared either: the strip would say something the tab does not"
    )

    view = function_body(app, "function cacheView() {")
    assert "stale: staleCaches.length," in view, (
        "the shared cache view no longer carries the count the cache tab lists"
    )

    paint = function_body(app, "function paintCacheStatus() {")
    # Two call sites - the busy branch and the resting branch - and both of them pass the one local
    # view; a third argument, or a call that builds its own object, is the drift this pins.
    assert re.findall(r"cacheStatusText\(([^)]*)\)", paint) == ["view", "view"], (
        "the cache tab must paint its status line from the shared view, once per branch"
    )
    assert "readiness.setCache(view);" in paint, (
        "the strip is fed something other than the view the status line is painted from"
    )
    calls = "\n".join(
        line for line in app.splitlines()
        if not line.startswith("import ") and not line.lstrip().startswith(("*", "//", "/*"))
    )
    assert calls.count("cacheStatusText(") == paint.count("cacheStatusText("), (
        "app.js words the stale-cache count outside paintCacheStatus(): the two surfaces are no "
        "longer guaranteed to agree"
    )


def test_the_export_segment_reports_the_panel_s_own_summary() -> None:
    """One object, used twice: the panel's summary line and the strip's segment read the same
    numbers, and the strip reuses the panel's own two headings rather than paraphrasing them."""
    report = function_body(text_of(APP), "function paintReport(payload, root) {")
    assert "t(\"export.summary\", computed)" in report, (
        "the panel's summary line is no longer painted from the computed object"
    )
    assert "onReport({ root: root, summary: computed, problems: problems.length });" in report, (
        "the strip is handed something other than the object the summary line was painted from"
    )
    clear = function_body(text_of(APP), "function clearReport() {")
    assert "if (onReport) onReport(null);" in clear, (
        "a different directory keeps the previous report, so the strip reports another dataset's plan"
    )
    readiness = text_of(READINESS)
    for key in ('t("export.warningsTitle"', 't("export.skippedTitle"'):
        assert key in readiness, (
            "%s is not reused by the strip: it paraphrases a number the export panel already prints" % key
        )
    assert "checked.summary" in readiness, "the strip no longer reads the panel's own summary object"
    assert "samePath(report.root, fileFact.root)" in readiness, (
        "the panel's report is used without checking that it describes the same file as the read fact: "
        "the panel's subject is the directory on screen, the fact's subject is the dataset root"
    )


def test_the_strip_computes_nothing_of_its_own() -> None:
    """The strip has no request and no counting: the whole point is that it cannot be a fourth
    source of truth. Two numbers it could plausibly have counted for itself are pinned here."""
    readiness = text_of(READINESS)
    assert "api." not in readiness and "fetch(" not in readiness, (
        "the strip fetches something; a second request for a number three panels already hold is "
        "exactly the drift the design constraint forbids"
    )
    assert "staleCaches" not in readiness and "state.counts" not in readiness, (
        "the strip reaches into app state instead of being handed the value the owner painted from"
    )


# ---------------------------------------------------------------------------
# 3. Each segment is an action that already exists
# ---------------------------------------------------------------------------


def test_the_three_segments_run_the_actions_the_panels_already_have() -> None:
    """Guidance over explanation: the strip does not describe the fix, it performs the one that
    exists - the gallery's own select-missing action, the cache tab's own rebuild, the export tab."""
    app = text_of(APP)
    call = app[app.index("const readiness = createReadinessStrip({"):]
    call = call[: call.index("\n});")]
    assert "onSelectMissingCaptions: () => { selectMissingCaptions(); }," in call
    assert "onCacheAction: () => { fixCaches(); }," in call
    assert 'onOpenExport: () => { void refreshTomlStatus(); openTab("export"); },' in call, (
        "the toml segment must open the export tab (where the toml is generated, read and copied) and "
        "re-ask for the file's state, which is what makes a failed status recoverable from the segment"
    )
    assert app.count("async function refreshTomlStatus(") == 1, (
        "the retry must run the one loader, not a second copy of it"
    )
    assert len(re.findall(r"function refreshTomlStatus\(", app)) == 1, (
        "there is more than one refreshTomlStatus definition"
    )

    # One action, two entry points - not a copy of the action.
    assert app.count("function selectMissingCaptions()") == 1
    assert '$("btn-select-missing").addEventListener("click", () => { selectMissingCaptions(); });' in app
    assert app.count("function rebuildAllStaleCaches()") == 1
    assert '$("btn-cache-rebuild-all").addEventListener("click", () => { void rebuildAllStaleCaches(); });' in app
    assert app.count('t("cache.confirmAll"') == 1, (
        "the rebuild confirmation is built twice: the strip and the tab would ask different questions"
    )
    fix = function_body(app, "function fixCaches() {")
    assert "loadCacheStatus()" in fix and "rebuildAllStaleCaches()" in fix, (
        "the failed state must retry the read, and the healthy state must rebuild"
    )


def test_the_status_fact_is_asked_for_the_dataset_root_and_re_asked_when_it_can_change() -> None:
    """§4.16: the fact is a stat of `state.root`, asked again wherever it can have changed, and never
    guessed.

    Four refresh sites, one per way the file can appear or vanish under the app - a directory is
    opened, the 15 s external-change poll ticks (a dataset.toml is invisible to that poll's image
    fingerprint), an export that may have written one finishes, and the segment itself is clicked.
    The other half is "never guessed": after a write the app re-reads rather than assuming the file
    now exists, so no code path may claim a file state it has not been told.
    """
    app = text_of(APP)
    body = function_body(app, "async function refreshTomlStatus() {")
    assert "const root = state.root;" in body, (
        "the fact is not asked for the dataset root - state.dir is the directory on screen"
    )
    assert "api.datasetTomlStatus(root)" in body, "the strip's fact does not come from §4.16's endpoint"
    assert "tomlFactRoot" in body, "the fact is not tagged with the dataset root it belongs to"
    assert "samePath(tomlFactRoot, root)" in body, (
        "a fact about another dataset survives a root switch, so one dataset's file is reported for another"
    )
    assert "tomlStatusToken" in body, "an older answer can paint over a newer one"
    assert "exists: true" not in body and "exists: true" not in app, (
        "some code path assumes the file now exists instead of asking - a write must be followed by a stat"
    )

    open_dir = function_body(app, "async function openDir(path, rootName) {")
    assert "void refreshTomlStatus();" in open_dir, "opening a directory does not re-ask for the file"
    poll = function_body(app, "async function pollForExternalChanges() {")
    assert "void refreshTomlStatus();" in poll, (
        "the external-change poll never re-asks: a dataset.toml is invisible to it"
    )
    assert "onFinished: () => { void refreshTomlStatus(); }," in app, (
        "a finished export - the one job here that writes a dataset.toml - does not re-ask"
    )
    scale = text_of(JS / "scale.js")
    assert "if (onFinished) onFinished(payload || {});" in scale, (
        "the scale panel's single job-end function does not tell anyone the job ended"
    )


def test_the_file_name_the_strip_reports_is_the_name_the_download_saves() -> None:
    """One file, one name - across both languages.

    The backend defines the name once (`core/dataset_toml.DATASET_TOML_NAME`, §3.9); the browser
    download saves the toml under the frontend's own `EXPORT_FILE_NAME`, and §4.16's endpoint reports
    the backend's. Renaming one side is how "the file the trainer reads" and "the file this app talks
    about" quietly become two files, and nothing else would notice.
    """
    backend = (REPO / "src" / "kohya_dataset_tagger" / "core" / "dataset_toml.py").read_text(encoding="utf-8")
    defined = re.search(r'DATASET_TOML_NAME = "([^"]+)"', backend)
    assert defined, "the backend no longer defines the file's name in one place"
    downloaded = re.search(r'EXPORT_FILE_NAME = "([^"]+)"', text_of(APP))
    assert downloaded, "the export panel no longer names the file it downloads"
    assert downloaded.group(1) == defined.group(1), (
        "the frontend downloads %r while the trainer reads %r" % (downloaded.group(1), defined.group(1))
    )


def test_the_strip_is_repainted_on_a_language_switch() -> None:
    """All three segments are render-time copy. applyCopy() only reaches data-copy nodes, so without
    this registration the strip stays in the previous language - and it is on screen permanently."""
    body = function_body(text_of(APP), "async function relabelAfterLocaleChange() {")
    assert "readiness.relabel();" in body, (
        "relabelAfterLocaleChange() does not repaint the readiness strip"
    )


def test_every_segment_state_has_copy_in_all_three_packs() -> None:
    for name in ("zh-CN", "en", "ja"):
        text = pack(name)
        for key in SEGMENT_KEYS:
            assert re.search(r'"%s":\s*"[^"]+"' % re.escape(key), text), (
                "%s is missing a non-empty %s" % (name, key)
            )
        for key in REUSED_KEYS:
            assert '"%s"' % key in text, "%s lost %s, which the strip reuses" % (name, key)


# ---------------------------------------------------------------------------
# 4. Behaviour: really run the strip
# ---------------------------------------------------------------------------


def test_the_strip_behaves_headlessly() -> None:
    """Every criterion about the painted text, the failure states and the relabel contract lives in
    the harness, which imports the real module over a fake DOM."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("no node on this machine")
    assert HARNESS.is_file(), "missing headless behaviour fixture %s" % HARNESS
    done = subprocess.run(
        [node, str(HARNESS), str(WEB)],
        capture_output=True, encoding="utf-8", errors="replace",
    )
    assert done.returncode == 0, "readiness strip behavior criteria failed:\n%s\n%s" % (done.stdout, done.stderr)
    assert "readiness harness OK" in done.stdout
