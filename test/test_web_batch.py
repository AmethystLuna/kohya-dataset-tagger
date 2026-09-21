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


def test_backend_and_frontend_op_sets_are_identical() -> None:
    """One missing value = the op the frontend sends gets a 400 (bad_op) from the backend, and only after the user has clicked write."""
    backend = backend_ops()
    frontend = frontend_ops()
    assert len(backend) >= 8, "only %d ops parsed out of the backend; the parsing is probably broken" % len(backend)
    assert backend == frontend, (
        "the batch op sets on the two sides differ: backend-only=%s, frontend-only=%s"
        % (sorted(backend - frontend), sorted(frontend - backend))
    )
    for op in ("prepend", "edit_tags", "regex_replace_text"):
        assert op in backend, "BATCH_OPS is missing %s" % op


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
    ):
        assert '"%s"' % key in keys, "strings.js is missing %s" % key


def test_batch_css_marks_a_row_that_is_being_removed() -> None:
    css = text_of(CSS)
    assert ".batch-common-row" in css, "the row list has no layout rule"
    assert ".batch-common-input.is-removed" in css, "a cleared row has no visual marker (the user cannot tell it will be deleted)"
    assert "line-through" in css, "a cleared row has no strikethrough"


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
