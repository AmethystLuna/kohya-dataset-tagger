"""Batch edit panel (`web/js/batch.js`): common tag editing, grouped rename, dry_run preview.

The user asked to "polish the edit logic and UX after stable-diffusion-webui-dataset-tag-editor".
The corresponding capabilities in the reference implementation: Edit common tags (edit the
common tags in place), Search and Replace (regex / whole caption), Remove (dedupe, remove the
selected tags), batch append/prepend.

This repo replaces "edit first, then save it all at once" with "dry_run preview first, then on
confirm write atomically at once + invalidate the cache": the write path is unchanged (invariant
number one), but there is an extra "take a look before applying" layer. What the criteria have to
pin down is **the preview and the disk write use the same op, and the write request must not
sneak in dry_run**.

The criteria come in three layers:
1. **Contract**: the value sets of the backend `BATCH_OPS` and the frontend `BATCH_OP` must match
   value for value (drift is a silent 400);
2. **Wiring**: the panel really uses `api.batchCaptions`, really previews before writing, and its
   scope is the shared control's (scope.js) rather than "whatever the filter left on screen";
3. **Behavior**: really run `batch.js` (fake DOM + fake fetch) - common tag intersection, row
   edit -> rename pair, preview carries dry_run, write does not, and the write is re-disabled once
   the preview goes stale.
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
BATCH = JS / "batch.js"
SCOPE_JS = JS / "scope.js"
API = JS / "api.js"
APP = JS / "app.js"
STRINGS = JS / "locales" / "zh-CN.js"
HTML = WEB / "index.html"
CSS = WEB / "css" / "app.css"
BACKEND = REPO / "src" / "kohya_dataset_tagger" / "api" / "captions.py"

BACKEND_OPS_RE = re.compile(r"BATCH_OPS: tuple\[str, \.\.\.\] = \((.*?)\n\)", re.S)
FRONTEND_OPS_RE = re.compile(r"BATCH_OP = Object\.freeze\(\{(.*?)\}\);", re.S)
QUOTED_RE = re.compile(r'"([a-z_]+)"')


def text_of(path: Path) -> str:
    assert path.is_file(), "cannot find %s" % path
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. Contract: the op sets on the two sides must be identical
# ---------------------------------------------------------------------------


def backend_ops() -> set[str]:
    match = BACKEND_OPS_RE.search(text_of(BACKEND))
    assert match is not None, "BATCH_OPS not found in api/captions.py"
    return set(QUOTED_RE.findall(match.group(1)))


def frontend_ops() -> set[str]:
    match = FRONTEND_OPS_RE.search(text_of(API))
    assert match is not None, "BATCH_OP not found in api.js"
    return set(re.findall(r':\s*"([a-z_]+)"', match.group(1)))


def test_every_frontend_op_is_one_the_backend_knows() -> None:
    """One missing value = the op the frontend sends gets a 400 (bad_op) from the backend, and only
    after the user has clicked write.

    The check is **one-directional since 2026-10-13**: the panel dropped `append` / `prepend` /
    `remove` (its common-tag editor covers them - two container add rows, clearing a row, and the
    replace op with an empty replacement), while the endpoint keeps them as its primitive set. A
    frontend op the backend does not know is the failure this criterion exists for; a backend op no
    screen offers is reported by the route/paragraph tests, not failed here.
    """
    backend = backend_ops()
    frontend = frontend_ops()
    assert len(backend) >= 8, "only %d ops parsed out of the backend; the parsing is probably broken" % len(backend)
    unknown = sorted(frontend - backend)
    assert not unknown, "api.js names ops the backend does not accept: %s" % unknown
    assert frontend, "no ops parsed out of api.js"
    for op in ("edit_tags", "replace", "regex_replace_text", "normalize", "sort_danbooru"):
        assert op in frontend, "BATCH_OP is missing %s" % op
    for op in ("prepend", "append", "remove"):
        assert op in backend, "the endpoint's primitive set lost %s" % op
        assert op not in frontend, (
            "%s is offered by the panel again, though the common-tag editor covers it (see the "
            "docstring of BATCH_OP in api.js)" % op
        )


# ---------------------------------------------------------------------------
# 2. Wiring
# ---------------------------------------------------------------------------


def test_panel_is_built_here_and_wired_into_the_app() -> None:
    assert 'import { createBatchPanel } from "./batch.js";' in text_of(APP), "app.js does not import the panel"
    body = text_of(APP)
    assert "const batchPanel = createBatchPanel({" in body, "app.js does not instantiate the batch panel"
    # The four scope providers moved into one shared object on 2026-09-19 (scope.js), and into one
    # shared *control* on 2026-09-20: both write panels read the same closures through the same
    # instance, which is what stops the two answers from drifting apart again. test_web_scope.py
    # owns the one-value invariant (one construction site, both panels handed it).
    assert "const scopeProviders = {" in body, "app.js has no shared scope providers"
    assert "  scope,\n" in body, "the batch panel is not handed the column's scope control"
    assert "getVisiblePaths: () => gallery.getVisiblePaths()" in body, (
        "the shared providers no longer describe what the gallery is showing"
    )
    assert "onAfterWrite: () => { void reloadCaptions(); void loadFrequency(); }" in body, (
        "captions and frequencies are not refreshed after a write"
    )
    assert "if (tab.dataset.tab === \"batch\") batchPanel.onShow();" in body, (
        "the scope is not recomputed when switching to the batch tab"
    )
    assert "batchPanel.refreshScope();" in text_of(APP), "the batch panel scope does not follow when the filter changes"


def test_the_scope_control_decides_what_the_panel_acts_on() -> None:
    """The batch panel used to act on "whatever the filter left on screen" - a rule with no control on
    the panel that obeyed it, so the only way to know the target was to remember the other tab.

    It now reads the same control the tagger does (scope.js, the same four scopes and live counts),
    defaults to the old behaviour, and that scope is what the request carries. The behavior half lives in
    `test/fixtures/batch_harness.mjs` section 11 (the chosen scope reaches /captions/batch).
    """
    batch = text_of(BATCH)
    assert "const scope = config.scope;" in batch, (
        "the batch panel does not read the column's shared scope control"
    )
    assert "createScopeControl(" not in batch, (
        "the batch panel builds a scope control of its own again - two answers to 'which images?'"
    )
    assert "defaultScope" not in batch, (
        "the panel overrides the shared default, so the strip would change value when the user "
        "switches tabs"
    )
    assert "scopeSelect" not in batch, "the panel renders a second scope select"
    assert "const paths = scope.paths();" in batch, "the request does not take its images from the control"
    assert "const paths = getVisiblePaths();" not in batch, (
        "a request path still reads the visible set directly, so the control can be ignored"
    )
    assert "invalidatePreview();" in batch and "onChange: () => {" in batch, (
        "changing the scope keeps a preview that describes the previous set of images"
    )

    scope_js = text_of(SCOPE_JS)
    for token in (
        'selected: "scope.selected"',
        'dir: "scope.dir"',
        'dir_recursive: "scope.dirRecursive"',
        'filtered: "scope.filtered"',
    ):
        assert token in scope_js, "the shared control has no label for %s" % token
    assert "export function createScopeControl(options)" in scope_js, "the control is not exported"
    # One control, two panels: a single onPaint/onChange option could only ever hold one of them.
    assert "function subscribe(handlers)" in scope_js, "the control cannot serve more than one panel"
    assert 'notify("onPaint")' in scope_js, "the panels have no way to react to a repaint"
    assert 'notify("onChange")' in scope_js, "the panels have no way to react to a new value"

    keys = text_of(STRINGS)
    for key in ("scope.label", "scope.selected", "scope.dir", "scope.dirRecursive", "scope.filtered"):
        assert '"%s"' % key in keys, "strings.js is missing %s" % key


def test_write_requires_a_fresh_preview() -> None:
    body = text_of(BATCH)
    assert "api.batchCaptions(" in body, "the panel does not call /api/captions/batch"
    assert "dry_run: true" in body, "the preview does not carry dry_run"
    assert "applyButton.disabled = writing || !lastPreview" in body, (
        "the write button is not tied to \"a fresh preview exists\" (and to no write being in flight)"
    )
    assert "invalidatePreview" in body and "addEventListener(\"input\", invalidatePreview)" in body, (
        "editing the inputs does not invalidate the preview - the user could still commit the previewed changes"
    )
    assert "renderCaptionDiff" in body, "the preview does not reuse autotag's diff rendering (before/after would each get their own copy)"

def test_the_common_editor_maps_to_the_real_backend_op() -> None:
    """\"Common tag edit\" is a **mode name** in the UI, not a backend op.

    This one was caught by actually running it: the panel originally sent the dropdown value
    straight through as the op, so after picking "Common tag edit" the preview only ever got a
    400 `bad_op` - while both the static criteria and the behavior fixture at the time believed
    `op: "common"` was correct. The fixture now asserts `op == "edit_tags"` directly.
    """
    body = text_of(BATCH)
    assert "op === BATCH_COMMON ? BATCH_OP.EDIT_TAGS : op" in body, (
        "the local pseudo-op is not mapped to a backend op: clicking preview would only get a 400 bad_op"
    )
    assert "op: requestOp(op)" in body, "the request body's op is not the mapped value"
    assert "export const BATCH_COMMON" in body, "the local pseudo-op constant is gone"


def test_the_common_editor_is_also_the_manual_order_editor() -> None:
    """The manual reorder ("drag the common tags into the order the trainer should read").

    One image in scope is **one** container - that caption's own order is the answer, so the panel
    shows the order on disk rather than the alphabetical intersection - and several images are the
    front / middle / back containers of §4.5 ③'s two order blocks. The rows are the very rows the
    rename/delete editor uses: a separate list of the same tags would be two live copies.
    """
    body = text_of(BATCH)
    assert 'export const ORDER_BUCKETS = ["front", "middle", "back"];' in body, "the three containers are gone"
    assert 'make("section", "batch-bucket")' in body, "the containers are not built at all"
    assert "bucket.dataset.bucket = name;" in body, "a container carries no name for its own drop target"
    assert 'bucketRows[name].replaceChildren(...orderBuckets[name]' in body, (
        "the containers are not painted from the order state"
    )
    assert 'orderHost.classList.toggle("is-single", reorderSingle);' in body, (
        "nothing switches between the one-container and the three-container layout"
    )
    assert "info.lists[0]" in body, (
        "a single-image scope does not use that caption's own order (the alphabetical intersection "
        "is the wrong list to reorder)"
    )
    assert "orderEdit(orderBuckets, values, reorderSingle)" in body, (
        "the arranged order never reaches the request"
    )
    assert "Object.assign(base, { pairs: edit.pairs }, added, order)" in body, (
        "the order blocks and the container additions are not merged into the edit_tags body"
    )
    assert "const nothing = edit.pairs.length === 0" in body, (
        "a moved row or an added tag is not part of the 'nothing changed' check: pressing preview "
        "after a pure drag would report 'no changes' instead of showing the new order"
    )
    assert "&& !added.tags_first && !added.tags_last" in body, (
        "an empty addition block counts as a change: every preview of an untouched editor would go out"
    )
    assert "&& !order.order_first && !order.order_last;" in body, (
        "an empty order block counts as a change: every preview of an untouched editor would go out"
    )


def test_the_order_editor_is_drag_and_keyboard_reachable() -> None:
    """A drag-only control is a dead end for everyone who does not use a mouse."""
    body = text_of(BATCH)
    assert "grip.draggable = true;" in body, "the rows cannot be dragged"
    assert 'grip.addEventListener("dragstart"' in body, "a drag never starts"
    assert 'bucket.addEventListener("drop"' in body, "the containers are not drop targets"
    assert "rowIndexUnder(event.target, bucket)" in body, (
        "a drop does not ask which row it landed on, so every drop would append"
    )
    assert 'grip.addEventListener("keydown"' in body, "the keyboard cannot reach the order"
    for key in ('key === "ArrowUp" || key === "ArrowDown"', 'key === "Home"', 'key === "End"'):
        assert key in body, "the keyboard path lost %s" % key
    assert 'reorderSingle ? "front" : "back"' in body, (
        "End sends a row into the hidden end container of the single-image layout"
    )
    assert 't("batch.dragHandle", { tag })' in body, "the grip has no accessible name"


def test_the_two_order_containers_are_named_prefix_and_suffix() -> None:
    """The user (2026-10-13): 前置和后置's English "不太通行，我见的比较多的写法的 prefix 和 suffix".

    The pair is the domain's own wording, and it has to be the *pair* in every pack that names it -
    including the two hints and the section hint, which used to spell the containers out again. The
    tagger's own write modes keep Append / Prepend: those are verbs about inserting tags, not the two
    places a caption can hold them.
    """
    names = {}
    for pack in ("zh-CN", "en", "ja"):
        text = text_of(JS / "locales" / (pack + ".js"))
        found = {}
        for key in ("batch.orderFront", "batch.orderMiddle", "batch.orderBack",
                    "batch.orderHintSingle", "batch.orderHintMulti", "batch.commonHint"):
            match = re.search(r'"%s":\s*"([^"]+)"' % re.escape(key), text)
            assert match, "%s is missing from %s.js" % (key, pack)
            found[key] = match.group(1)
        names[pack] = found
    assert names["zh-CN"]["batch.orderFront"] == "前置", "the Chinese container name changed"
    assert names["zh-CN"]["batch.orderBack"] == "后置", "the Chinese container name changed"
    assert names["en"]["batch.orderFront"] == "Prefix", (
        "the English front container is not Prefix (the wording the user actually sees in this domain)"
    )
    assert names["en"]["batch.orderBack"] == "Suffix", "the English end container is not Suffix"
    assert names["ja"]["batch.orderFront"] == "プレフィックス", "the Japanese pack does not follow the pair"
    assert names["ja"]["batch.orderBack"] == "サフィックス", "the Japanese pack does not follow the pair"
    for pack, keys in names.items():
        for key in ("batch.orderHintSingle", "batch.orderHintMulti", "batch.commonHint"):
            for stale in ("Prepend", "Append", "先頭へ", "末尾へ"):
                assert stale not in keys[key], (
                    "%s's %s still calls the containers %r while the titles say Prefix / Suffix"
                    % (pack, key, stale)
                )


def test_the_order_containers_state_their_own_meaning() -> None:
    """Copy and CSS for the containers: the middle is inert, and the single layout closes the rest."""
    keys = text_of(STRINGS)
    for key in ("batch.orderSingle", "batch.orderFront", "batch.orderMiddle", "batch.orderBack",
                "batch.orderHintSingle", "batch.orderHintMulti", "batch.dragHandle"):
        assert '"%s"' % key in keys, "strings.js is missing %s" % key
    assert "{tag}" in keys, "the grip's accessible name has no {tag} placeholder"
    # The hint above the section is the only copy that is on screen whatever the scope holds, so the
    # handle has to be named there: the report that produced this criterion was "where is the entrance?".
    hint = re.search(r'"batch\.commonHint":\s*"([^"]+)"', keys)
    assert hint and "手柄" in hint.group(1), (
        "batch.commonHint does not tell the user that the row has a drag handle"
    )
    # The single-image layout closes two of the three containers, so its hint has to say where they
    # went - otherwise their absence reads as a bug (the report it came from: "前置和后置不见了").
    single = re.search(r'"batch\.orderHintSingle":\s*"([^"]+)"', keys)
    assert single and "前置" in single.group(1) and "后置" in single.group(1), (
        "the single-image hint does not say that the other two containers appear with several images"
    )
    css = text_of(CSS)
    assert '.batch-order.is-single .batch-bucket[data-bucket="middle"]' in css, (
        "the single-image layout does not close the middle container"
    )
    assert '.batch-order.is-single .batch-bucket[data-bucket="back"]' in css, (
        "the single-image layout does not close the end container"
    )
    assert ".batch-bucket.is-drop-target" in css, "the container under the pointer has no marker"
    assert ".batch-grip" in css, "the drag handle has no style of its own"
    # The two drop targets share the top row and 维持 gets its own row below (the harness pins the DOM
    # order; this pins the CSS that makes it a row, and the stickiness that keeps the targets reachable
    # while the long 维持 list is scrolled - the user's own reason: "otherwise dragging is torture").
    assert 'if (name === "middle") orderHost.append(bucket);' in text_of(BATCH), (
        "the containers are appended in one flat list again"
    )
    assert ".batch-order-top {" in css and "position: sticky;" in css, (
        "the row with the two drop targets is not pinned, so scrolling 维持 takes them off screen"
    )
    assert ".batch-order-top .batch-bucket { flex: 1 1 0; min-width: 0; }" in css, (
        "前置 and 后置 do not share the top row"
    )
    bucket_rule = re.search(r"^\.batch-bucket \{(.*?)\}", css, re.S | re.M)
    assert bucket_rule and "border: 1px solid var(--line);" in bucket_rule.group(1), (
        "the containers have no visible border: three unoutlined groups of rows read as one flat list, "
        "and the point of the containers is that their contents mean different things"
    )
    # The user: "这个新增的按钮和列表之间的间距要和列表元素间的间距一致". One value, declared once, used by
    # the container (which spaces its blocks) and by the two lists (which space their rows) - measured in
    # real Chrome over the panel's own DOM shape: row/row, addition/row, list/+ are all 4px.
    assert "--batch-row-gap: 4px" in css, "there is no single spacing value for the editor"
    assert css.count("var(--batch-row-gap)") >= 3, (
        "the + is not spaced by the same value the rows use (a margin of its own drifts apart from it)"
    )
    assert "margin-top: 3px" not in css, (
        "the add row carries a margin again instead of being spaced by its container"
    )
    assert ".batch-bucket-rows, .batch-bucket-added { flex: 0 0 auto; }" in css, (
        "a list can be squashed to its 22px floor again, with its rows overflowing it invisibly"
    )
    assert ".batch-bucket-rows:empty { display: none; }" in css, (
        "an empty list occupies space again, so the gap above the + is not the row gap (it was 30px)"
    )
    assert css.count("max-height: 20vh;") == 1 and "max-height: 40vh;" in css, (
        "the top row is not capped at half the editor's own height (40vh): a 前置 filled with tags "
        "would leave no room for the list you are dragging out of"
    )
    # The report this came from: "前置和后置的容器丢了" - a screenshot with 维持 full of rows and no top
    # row at all. A flex item with `overflow: auto` loses its automatic minimum size, so the default
    # `flex-shrink: 1` let the overflowing middle squash the pinned row flat. Measured in real Chrome
    # over the panel's own DOM shape (48 rows, 1280x900): `flex: 1 1 auto` gives the top row **0 px**
    # and the two containers 6 px; `flex: 0 0 auto` gives it 97 px. The committed layout probe cannot
    # reach this state - its canned captions share two tags, so the editor never overflows there.
    top_rule = re.search(r"\.batch-order-top \{(.*?)\}", css, re.S)
    assert top_rule and "flex: 0 0 auto;" in top_rule.group(1), (
        "the pinned row is shrinkable again: the overflowing middle list squashes it to zero height"
    )
    assert ".batch-order > .batch-bucket { flex: 0 0 auto; }" in css, (
        "the middle container is shrinkable: the long list would be squeezed instead of scrolling"
    )


def test_the_panel_asks_for_the_captions_its_scope_needs() -> None:
    """A scope can reach outside the directory on screen ("current directory + subdirectories", a
    selection), and the gallery only ever probes the listing on screen - so the panel has to ask for
    the captions it cannot compute without, or the common-tag containers stay empty **forever** on
    their "reading caption…" note. That is the report this criterion came from: "the containers start
    out without the common tags".

    The behaviour half (the panel really asks, and only once per scope while the answer is in flight)
    lives in `test/fixtures/batch_harness.mjs`.
    """
    batch = text_of(BATCH)
    assert "config.requestCaptions" in batch, "the panel ignores the app's caption reader"
    assert "if (info.missing > 0) requestCaptions(info.paths);" in batch, (
        "the panel never asks for the captions it cannot compute without"
    )
    app = text_of(APP)
    assert "async function probeScopeCaptions(paths)" in app, "app.js has no scope probe"
    assert "requestCaptions: (paths) => { void probeScopeCaptions(paths); }," in app, (
        "app.js does not hand the batch panel a way to read its scope's captions"
    )
    assert "scopeProbing" in app and "captionInfo.has(path)" in app, (
        "the scope probe does not skip the captions already known (or already in flight), so it would "
        "re-request them on every repaint"
    )
    probe = app[app.index("async function probeScopeCaptions(paths)"):][:1600]
    assert "applyFilter();" in probe, (
        "the scope probe never repaints the panels that were waiting for it"
    )


def test_the_panel_follows_the_gallery_selection() -> None:
    """The scope select does not change when the user picks images, so `onChange` never fires.

    A selection change is a change of "which images" for this panel without any visible control
    moving, and the readout in the toolbar was the only thing that followed it. The batch panel kept
    describing the selection it was last built for - at boot, no images at all - which is what the
    screenshot showed as six selected images above three empty containers and a "no images" note.

    Two halves, because either alone is not enough: app.js has to tell the panel (this criterion),
    and the panel has to notice a provider change that arrives through a repaint (the harness'
    section 15.8, which calls `scope.paint()` the way the selection handler does).
    """
    app = text_of(APP)
    assert "autotagPanel.refreshScopes();" in app and "batchPanel.refreshScope();" in app, (
        "the selection handler still refreshes only the tagger: the batch panel keeps the old selection"
    )
    assert app.index("autotagPanel.refreshScopes();") < app.index("batchPanel.refreshScope();") < app.index(
        "if (filterPanel.hasSelectionFilter()) applyFilter();"
    ), "the batch panel is not refreshed inside onSelectionChange"
    batch = text_of(BATCH)
    assert "function onScopePaint()" in batch, "the panel does not react to a repaint of the control"
    assert 'if (scope.paths().join("\\n") === lastSig) return;' in batch, (
        "a repaint recomputes the scope even when the paths did not change (or never recomputes it)"
    )
    assert "refreshingScope" in batch, (
        "no re-entrancy guard: refreshScope repaints the control, which notifies onPaint again"
    )


def test_the_empty_common_editor_says_which_nothing_it_is() -> None:
    """"no images in the scope" and "these images share no tag" are different facts with different next
    steps; one key for both made the second read like a broken feature (and the first one unanswerable)."""
    batch = text_of(BATCH)
    assert 't(scopeImageCount === 0 ? "batch.commonNoneImages" : "batch.commonNone")' in batch, (
        "the empty state does not distinguish an empty scope from a scope whose tags all differ"
    )
    keys = text_of(STRINGS)
    for key in ("batch.commonNone", "batch.commonNoneImages"):
        assert '"%s"' % key in keys, "strings.js is missing %s" % key


def test_common_tag_editor_is_a_row_list_not_a_text_box() -> None:
    """A row list is what stops "delete row 3 -> the next two rows each lose a tag" from happening."""
    body = text_of(BATCH)
    assert "batch-common-row" in body, "the common tags are not rendered row by row"
    assert "batch-common-input" in body, "the common tags have no editable input box"
    assert "commonEdit(" in body, "row edits are not converted into rename pairs"


def test_strings_js_has_the_batch_copy() -> None:
    keys = text_of(STRINGS)
    for key in (
        "tab.batch",
        "batch.title",
        "batch.opCommon",
        "batch.opRegexText",
        "batch.apply",
        "batch.applyHint",
        "batch.backup",
        "batch.confirm",
        "batch.diffSummary",
        "batch.doneWithCache",
        "batch.commonPending",
        "batch.undoing",
        "batch.undone",
        "batch.undoPartial",
        "batch.undoFailed",
        "batch.orderSingle",
        "batch.orderFront",
        "batch.orderMiddle",
        "batch.orderBack",
        "batch.orderHintSingle",
        "batch.orderHintMulti",
    ):
        assert '"%s"' % key in keys, "strings.js is missing %s" % key


def test_batch_css_marks_a_row_that_is_being_removed() -> None:
    css = text_of(CSS)
    assert ".batch-common-row" in css, "the row list has no layout rule"
    assert ".batch-common-input.is-removed" in css, "a cleared row has no visual marker (the user cannot tell it will be deleted)"
    assert "line-through" in css, "a cleared row has no strikethrough"
    # The shared `.suggest-wrap` grows (`flex: 1 1 auto`, which the caption editor's input row
    # wants); in the batch panel's flex row that made the wrapper wider than the field it drops
    # from (measured in headless Chrome: a 160px input inside a 271px wrapper). The rule that
    # holds the two together is worth pinning, because its absence is invisible in a fake DOM.
    assert ".batch-section .suggest-wrap { flex: 0 1 auto; min-width: 0; }" in css, (
        "the batch tag field's dropdown is not tied to the width of the field it drops from"
    )


# ---------------------------------------------------------------------------
# 2b. Autocomplete: one shared dropdown, the scope's tags first
# ---------------------------------------------------------------------------

SUGGEST = JS / "suggest.js"
#: the tag fields this panel builds; the regex fields are deliberately not among them
TAG_FIELDS = (
    'const findField = tagField(null, { mode: "dataset" });',
    "const replaceField = tagField(null);",
)
SUGGEST_TAGS_RE = re.compile(r"async function suggestTags\([^)]*\)\s*\{(.*?)\n  \}", re.S)
TAG_FIELD_RE = re.compile(r"const tagField = \([^)]*\)\s*=>\s*\{(.*?)\n  \};", re.S)


def test_tag_fields_use_the_shared_autocomplete() -> None:
    """The report: "批量 tag 编辑这边怎么不支持自动补全".

    The behaviour was there all along, inside the per-image editor, where this panel could not
    reach it. It is one module now (`suggest.js`), used by both surfaces, and this panel's tag fields
    go through `tagField`. The two regex fields stay plain inputs: a regular expression is not a tag,
    and a dropdown over one would offer tags nobody asked for.
    """
    batch = text_of(BATCH)
    assert 'from "./suggest.js"' in batch, "the batch panel does not use the shared autocomplete"
    for site in TAG_FIELDS:
        assert site in batch, "a tag field lost its autocomplete: %s is gone" % site
    assert "const patternInput = textInput();" in batch, "the regex pattern field grew a tag dropdown"
    assert "const replacementInput = textInput();" in batch, "the regex replacement field grew a tag dropdown"
    assert "const patternInput = tagField" not in batch and "const replacementInput = tagField" not in batch, (
        "a regex field is offered tags"
    )
    # The common-tag ROWS and the two container add rows stay plain inputs on purpose: they live in a
    # scrolling box (`overflow: auto`, and the pinned top row needs it too), whose scroll clip cuts an
    # absolutely positioned dropdown. This is the reason the old single add field sat *outside* it.
    assert 'textInput("batch-common-input")' in batch, (
        "the common rows changed shape; if they grew a dropdown, it is clipped by .batch-order"
    )
    assert 'textInput("batch-common-input batch-added-input")' in batch, (
        "the container add rows changed shape; a dropdown inside a scrolling container is clipped"
    )

def test_suggestions_come_from_the_scope_first_and_the_vocabulary_as_fallback() -> None:
    """The order is the point: a dataset tag is what replace/find act on, and it costs no request;
    the WD14 vocabulary is what finds a tag no image carries yet (§4.10, and it answers an empty list
    when no tagger model is installed - so it can never be the only source)."""
    match = SUGGEST_TAGS_RE.search(text_of(BATCH))
    assert match is not None, "the batch panel has no suggestTags source"
    body = match.group(1)
    assert "rankSuggestions(" in body, "the panel does not rank the scope's own tags"
    assert "api.tagAutocomplete(" in body, "the vocabulary fallback is gone"
    assert body.index("rankSuggestions(") < body.index("api.tagAutocomplete("), (
        "the vocabulary is consulted before the dataset's own tags"
    )
    assert "if (local.length >= limit) return local;" in body, (
        "a full page of dataset tags still pays for a vocabulary request"
    )
    assert "catch (err)" in body and "vocabulary = [];" in body, (
        "a failed vocabulary request must not empty the dropdown - the dataset half is still an answer"
    )
    assert "scopeTagIndex = buildScopeTagIndex(info.lists);" in text_of(BATCH), (
        "the index is not rebuilt where the scope is re-read"
    )


def test_accepting_a_suggestion_is_an_edit_like_any_other() -> None:
    """A programmatic value change fires no `input` event, and the wiring that drops a stale
    preview hangs off exactly that event - so the accept path has to drop it itself."""
    match = TAG_FIELD_RE.search(text_of(BATCH))
    assert match is not None, "the panel has no tagField helper"
    assert "invalidatePreview();" in match.group(1), (
        "accepting a suggestion leaves a preview of the previous field values armed"
    )
    assert 'addEventListener("input", invalidatePreview)' in text_of(BATCH), (
        "typed edits no longer drop the preview"
    )


def test_find_suggests_from_the_scope_only() -> None:
    """`find` names a tag the images already carry, so a vocabulary tag they do not carry is a
    suggestion that cannot be acted on (replace matches zero images). "replace with" may name a tag
    no image has yet, so it keeps the vocabulary.

    The one exception is a scope whose captions are still being read: the index is incomplete then,
    and suppressing the vocabulary would suggest nothing at all."""
    batch = text_of(BATCH)
    assert 'const findField = tagField(null, { mode: "dataset" });' in batch, (
        "the find field offers tags nothing in the scope carries"
    )
    assert "const replaceField = tagField(null);" in batch, (
        "the replace-with field is no longer the open one (it may name a new tag)"
    )
    match = SUGGEST_TAGS_RE.search(batch)
    assert match is not None, "the batch panel has no suggestTags source"
    assert 'if (mode === "dataset" && scopeTagMissing === 0) return local;' in match.group(1), (
        "the dataset-only mode is not enforced where the candidates are merged"
    )
    assert batch.count("scopeTagMissing = info.missing;") == 2, (
        "the incomplete-probe escape hatch is not kept up to date in both places the scope is read"
    )


def test_the_two_containers_carry_their_own_add_row() -> None:
    """The panel's add mechanism belongs to the containers (2026-10-13): a new tag is added **into**
    one of the two blocks, and the request says which. That replaced the single "new tag" field plus
    the "put new tags at the beginning" checkbox - the container already says where.

    The control is a **permanent, full-width dashed `+`**, and clicking it **appends an element** (the
    user: "这个虚线框可以常驻，点击的时候以新增一个元素作为实现方法，留空时失焦自动删除"): the element is a
    row with a grip, a box and a ×, exactly the shape of the tag rows above it, and an element left
    empty and blurred removes itself. The behaviour half is in `batch_harness.mjs`.
    """
    batch = text_of(BATCH)
    assert 'textInput("batch-common-input batch-added-input")' in batch, (
        "an addition is not a tag row's box any more"
    )
    assert 'make("button", "batch-bucket-add")' in batch, "the containers have no add button"
    assert 'toggle.textContent' not in batch and 'addButton.textContent = "+";' in batch, (
        "the closed state is not a + (the user asked for a +, not a labelled button)"
    )
    assert "addButton.addEventListener(\"click\", () => { addBucketRow(name); });" in batch, (
        "clicking the + does not append an element"
    )
    assert "if (!tag) {" in batch and "dropAdded(item);" in batch, (
        "an element left empty is not taken away again"
    )
    assert "input.addEventListener(\"blur\", () => { commitAdded(item); });" in batch, (
        "losing focus is not what settles the new element"
    )
    assert "if (name !== \"middle\") {" in batch, (
        "the middle container grew an add row: an addition has no middle to land in"
    )
    assert "blockAdditions(addedTags, commonOriginal)" in batch, (
        "the container additions are not turned into the request's fields"
    )
    assert "commonPrepend" not in batch, "the old shared prepend checkbox is still there"
    assert "batch-tags-input" not in batch, "the removed three-op tag list field is back"
    keys = text_of(STRINGS)
    assert '"batch.addIsCommon"' in keys, "nothing tells the user that a tag is already a common row"
    assert '"batch.addIsCommon"' in text_of(JS / "locales" / "en.js"), "the warning is not translated"


# ---------------------------------------------------------------------------
def test_the_backup_checkbox_decides_whether_there_is_a_question_at_all() -> None:
    """p0-spec §4.5 (3) @@backup@@ (default true) + (5) the restore.

    This criterion replaces "the checkbox decides what the confirmation says" (2026-09-19): once the
    restore endpoint exists, a write that kept a backup is **undoable**, and a dialog in front of an
    undoable action is the thing the undo was supposed to remove. The checkbox now decides whether the
    write asks at all - and with it off the confirmation is back, because then it really is irreversible.
    """
    batch = text_of(BATCH)
    assert "backupCheck.checked = true" in batch, "the backup is off by default"
    assert 'inline(backupCheck, "batch.backup")' in batch, (
        "the checkbox is not labelled through the copy engine (a language switch would leave it behind)"
    )
    assert "backup: backupCheck.checked" in batch, "the request does not carry what the checkbox shows"
    assert "if (!backupCheck.checked) {" in batch, (
        "the confirmation is either always there or never: it has to follow the backup"
    )
    assert 'body: t("batch.confirm", { n: paths.length }),' in batch, (
        "an irreversible write must still carry the plain question"
    )
    assert 't("batch.confirmBackup"' not in batch, (
        "the old always-ask wording is back; with a backup the write does not ask"
    )
    keys = text_of(STRINGS)
    assert '"batch.backup"' in keys, "strings.js is missing batch.backup"
    assert '"batch.confirmBackup"' not in keys, "a key nothing renders is still shipped in three packs"


def test_the_undo_is_offered_from_the_toast_and_restores_what_was_written() -> None:
    """The escape hatch after the fact: the toast carries it, it targets this run's images, and it goes
    through the endpoint that puts the backups back (p0-spec §4.5 (5))."""
    batch = text_of(BATCH)
    assert "const written = appliedImages.slice();" in batch, (
        "the undo list is read when the toast is clicked, not when it is built - the next run clears it"
    )
    assert 'run: () => { void undoWrite(written); }' in batch, "the action does not call the undo"
    assert "api.restoreCaptions(paths)" in batch, "the undo does not use the restore endpoint"
    assert "backupCheck.checked && written.length > 0" in batch, (
        "an undo is offered for a write that kept no backup - there is nothing to restore"
    )
    assert 'payload.entry.ok === true && payload.entry.image' in batch, (
        "the undo list is not limited to the images the job actually wrote"
    )

    api_js = text_of(API)
    assert 'captionsRestore: "POST /api/captions/restore",' in api_js, "api.js does not know the route"
    assert 'restoreCaptions: (images) => request("captionsRestore", { body: { images } }),' in api_js, (
        "the route is declared but never called"
    )


# 3. Behavior: really run batch.js (fake DOM + fake fetch)
# ---------------------------------------------------------------------------


def test_batch_panel_behaves_headlessly_against_a_fake_dom() -> None:
    """Common tag intersection, rename pair, dry_run preview, disk write without dry_run, and disabling once the preview is stale."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("no node on this machine")
    harness = REPO / "test" / "fixtures" / "batch_harness.mjs"
    assert harness.is_file(), "missing headless behavior fixture %s" % harness
    done = subprocess.run(
        [node, str(harness), str(WEB)],
        capture_output=True, encoding="utf-8", errors="replace",
    )
    assert done.returncode == 0, "batch panel behavior criteria failed:\n%s\n%s" % (done.stdout, done.stderr)
    assert "batch harness OK" in done.stdout
