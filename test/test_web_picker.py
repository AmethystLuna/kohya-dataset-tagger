"""**Wiring** criteria for the §4.12 graphical directory picker.

Why a separate file: the endpoint was right and `picker.js` was written right, but
**nobody called it**, so the user still saw that line of text that would not
respond to clicks -- exactly the bug this was fixing (the user's own words: "it
says a directory is now open, but clicking it does not let me switch the
selection"). So what is asserted here is "it is wired up", not "the function
exists".

It also pins one more thing: when the server says the picker is unavailable, the
UI must give the **reason** instead of silently disappearing -- silently
disappearing is yet another "cannot click it and does not know why".
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
PICKER = JS / "picker.js"
APP = JS / "app.js"
API_JS = JS / "api.js"
SETTINGS = JS / "settings.js"
STRINGS = JS / "locales" / "zh-CN.js"


def text_of(path: Path) -> str:
    assert path.is_file(), "not found: %s" % path
    return path.read_text(encoding="utf-8")


def test_api_js_declares_the_pick_route_and_exposes_it() -> None:
    body = text_of(API_JS)
    assert '"GET /api/fs/pick"' in body, "api.js does not declare GET /api/fs/pick"
    assert 'pick: (path) => request("fsPick"' in body, "api.js does not provide pick(path)"


def test_api_js_declares_the_mkdir_route_and_exposes_it() -> None:
    body = text_of(API_JS)
    assert '"POST /api/fs/pick/mkdir"' in body, "api.js does not declare POST /api/fs/pick/mkdir"
    assert 'pickMkdir: (parent, name) => request("fsPickMkdir"' in body, (
        "api.js does not provide pickMkdir(parent, name)"
    )


def test_picker_offers_a_manual_path_field_and_a_new_directory_action() -> None:
    """The user's own words: "the export directory picker is not complete enough, you cannot type manually and cannot create a new folder"."""
    body = text_of(PICKER)
    assert "picker-path" in body, "the picker has no editable path field (cannot type manually)"
    assert 't("picker.pathPlaceholder")' in body, "the path field has no placeholder copy"
    assert "picker-newdir" in body and 't("picker.newDir")' in body, (
        "the picker has no 'new directory' control"
    )
    assert 't("picker.newDirPlaceholder")' in body, "the new-directory-name field has no placeholder copy"
    assert "api.pickMkdir(" in body, "'new directory' does not call the mkdir endpoint"


def test_picker_copy_covers_the_new_controls() -> None:
    keys = text_of(STRINGS)
    for key in (
        "picker.pathPlaceholder", "picker.go", "picker.newDir",
        "picker.newDirPlaceholder", "picker.createFailed",
    ):
        assert '"%s"' % key in keys, "strings.js is missing copy %s" % key
    assert "{message}" in keys, "picker.createFailed's copy has no message placeholder"


def test_picker_module_exists_is_reachable_and_is_really_opened() -> None:
    assert PICKER.is_file(), "no web/js/picker.js"
    assert "export function createPicker" in text_of(PICKER), "picker.js does not export createPicker"
    body = text_of(APP)
    assert re.search(r'import\s*\{[^}]*createPicker[^}]*\}\s*from\s*"\./picker\.js"', body), (
        "app.js does not import createPicker from picker.js (a floating module under js/ turns the contract test red)"
    )
    assert "createPicker(" in body, "app.js does not wire up the picker"
    assert "picker.open(" in body, "app.js does not actually open the picker"


def test_all_three_entry_points_are_wired() -> None:
    """The currently highlighted root in the left column / the export directory row of the export panel / the current-location segment of the breadcrumb."""
    body = text_of(APP)
    assert body.count("onPickDataset") >= 4, "the entry point is not wired to both the roots panel and the export panel"
    assert re.search(r"createRootsPanel\(\{.*?onPickDataset", body, re.S), "the roots panel is not wired to the picker"
    assert re.search(r"createExportPanel\(\{.*?onPickDataset", body, re.S), "the export panel is not wired to the picker"
    assert "requestPick()" in body, "no clickable entry point calls requestPick"
    assert "switchDataset(" in body, "selecting does not actually switch the dataset"
    assert "setPickerEnabled" in body, "app.js does not push the server capability to the entry points (the buttons will not grey out)"
    settings = text_of(SETTINGS)
    assert "onPickDataset" in settings, "settings.js does not accept onPickDataset"
    assert "setPickerEnabled" in settings, "settings.js does not expose setPickerEnabled"


def test_selected_directory_is_added_to_the_whitelist_before_opening() -> None:
    """Switching the dataset = extend the allowlist first, then open; without addRoot, openDir is bound to be a 403."""
    body = text_of(APP)
    switch = body.index("async function switchDataset(")
    segment = body[switch:switch + 700]
    assert "api.addRoot(" in segment, "switchDataset does not add the directory to the allowlist first"
    assert "openDir(" in segment, "switchDataset does not open the selected directory"
    assert segment.index("api.addRoot(") < segment.index("openDir("), (
        "must POST /api/roots before openDir, otherwise choosing a directory outside the allowlist is a 403"
    )


def test_unavailable_picker_explains_itself() -> None:
    """When unavailable it must give the reason: silently disappearing is yet another 'cannot click it and does not know why'."""
    body = text_of(APP)
    assert "path_config" in body, "app.js does not read the capability bit from /api/health"
    assert 't("picker.disabled"' in body, "app.js does not explain the reason when unavailable"
    keys = text_of(STRINGS)
    assert '"picker.disabled"' in keys, "strings.js is missing picker.disabled"
    assert "{host}" in keys, "picker.disabled's copy has no host placeholder"


# ---------------------------------------------------------------------------
# Behavior gate: actually run picker.js once (fake DOM, no jsdom)
# ---------------------------------------------------------------------------


def test_picker_behaves_headlessly_against_a_fake_dom() -> None:
    "Actually run picker.js once: clicking a directory enters it, clicking 'use this "
    "directory' resolves it, Esc cancels, 'go up' is disabled at a root; pressing "
    "Enter in the path field navigates, creating a directory calls the mkdir "
    "endpoint and enters the new directory, and an empty name sends no request."
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed here")
    harness = REPO / "test" / "fixtures" / "picker_harness.mjs"
    assert harness.is_file(), "missing headless behavior fixture %s" % harness
    done = subprocess.run(
        [node, str(harness), str(WEB)],
        capture_output=True, encoding="utf-8", errors="replace",
    )
    assert done.returncode == 0, "picker.js behavior criterion failed:\n%s\n%s" % (done.stdout, done.stderr)
    assert "picker harness OK" in done.stdout
