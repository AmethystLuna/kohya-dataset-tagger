"""The frontend landing spot for §4.11: the root and model-search-directory editing panels.

Why a separate file (`test_web_contract.py` already has a pile of static criteria)
----------------------------------------------------------------------------------
That file covers the **generic** contract (endpoints do not drift, copy is
centralized, no build step, ids line up). This one covers whether this one feature
is **wired up** -- its failure mode is peculiar: the backend is all green and the
API is all correct, yet the UI has no entry point at all (the user's own words:
"the model scan directory page **has no findable config entry**"). Static criteria
can catch exactly this class: the endpoint is never called, the panel is not wired
up, the ids do not match on both sides.

The last section is Node's **syntax gate**: `web/` has no build step, so a stray
parenthesis is only discovered when you click it open in the browser. Parsing each
module as an ESM (without executing it) catches that early.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
WEB = REPO / "src" / "kohya_dataset_tagger" / "web"
JS = WEB / "js"
SETTINGS = JS / "settings.js"
APP = JS / "app.js"
API_JS = JS / "api.js"
MARKUP = WEB / "index.html"

#: all six §4.11 endpoints must appear in api.js's ROUTES
FROZEN_ROUTES = (
    "GET /api/roots",
    "POST /api/roots",
    "DELETE /api/roots",
    "GET /api/autotag/model-roots",
    "POST /api/autotag/model-roots",
    "DELETE /api/autotag/model-roots",
)

#: element ids the panel needs: `settings.js` fetches them, `index.html` must declare them
PANEL_IDS = (
    "root-list",
    "btn-add-root",
    "root-add-form",
    "root-path-input",
    "btn-root-add-ok",
    "btn-root-add-cancel",
    "btn-model-roots",
    "model-roots-dialog",
    "model-roots-list",
    "model-root-path-input",
    "btn-model-root-add",
    "btn-model-roots-close",
)

#: §4.11's **response-level** fields (the POST / DELETE payload)
PAYLOAD_FIELDS = ("changed", "persisted", "warning", "removed_scope")

#: §4.11's **per-entry** fields (each item of roots[])
ROOT_FIELDS = ("path", "source", "exists", "is_dir", "file_backed", "models", "model_count")


def text_of(path: Path) -> str:
    assert path.is_file(), "not found: %s" % path
    return path.read_text(encoding="utf-8")


def test_settings_module_exists_and_exports_both_panels() -> None:
    body = text_of(SETTINGS)
    assert "export function createRootsPanel" in body, "missing the roots panel factory"
    assert "export function createModelRootsPanel" in body, "missing the model-search-directory panel factory"


def test_app_js_wires_both_panels_and_owns_the_root_list_once() -> None:
    """Drawing the roots list in both places = double rendering; in neither = the user sees no entry point."""
    body = text_of(APP)
    assert re.search(r'import\s*\{[^}]*createRootsPanel[^}]*\}\s*from\s*"\./settings\.js"', body), (
        "app.js does not import createRootsPanel from settings.js"
    )
    assert "createModelRootsPanel(" in body, "app.js does not wire up the model-search-directory panel"
    assert "createRootsPanel(" in body, "app.js does not wire up the roots panel"
    assert "function renderRoots" not in body, (
        "app.js still draws the roots list itself -- the list can have only one owner, otherwise you get two copies that overwrite each other"
    )
    assert "rootsPanel.reload()" in body or "rootsPanel.reload(" in body, (
        "loadRoots is not delegated to the panel"
    )
    assert "onModelsChanged" in body, (
        "adding/removing a model directory does not refresh the tagger's model dropdown -- the user thinks it did not take effect"
    )


def test_api_js_declares_and_the_panels_call_every_frozen_route() -> None:
    routes = text_of(API_JS)
    for route in FROZEN_ROUTES:
        assert '"%s"' % route in routes, "api.js does not declare %s" % route
    body = text_of(SETTINGS)
    for name in ("api.roots(", "api.addRoot(", "api.removeRoot(",
                 "api.modelRoots(", "api.addModelRoot(", "api.removeModelRoot("):
        assert name in body, "settings.js does not call %s" % name


def test_panel_ids_exist_in_the_markup() -> None:
    markup = text_of(MARKUP)
    declared = set(re.findall(r'\bid="([^"]+)"', markup))
    missing = [item for item in PANEL_IDS if item not in declared]
    assert not missing, "index.html is missing these ids: %s" % missing
    body = text_of(SETTINGS)
    used = set(re.findall(r'getElementById\("([^"]+)"\)', body))
    assert set(PANEL_IDS) <= used, (
        "settings.js does not fetch these elements: %s" % sorted(set(PANEL_IDS) - used)
    )


def test_the_dialog_starts_hidden() -> None:
    """If the dialog is visible by default, a mask is smeared over the page on load -- and that is checkable at static time."""
    markup = text_of(MARKUP)
    for element_id in ("root-add-form", "model-roots-dialog"):
        match = re.search(r'<[^>]*\bid="%s"[^>]*>' % element_id, markup)
        assert match is not None, "cannot find #%s" % element_id
        assert re.search(r'\bhidden\b', match.group(0)), "#%s has no default hidden" % element_id


def test_panels_read_every_frozen_response_field() -> None:
    """Fields the contract hands over must really be used, otherwise they are just documentation.

    A word on `persisted` specifically: `changed:false` + `persisted:false` + no
    warning means 'this entry came from the command line and only applies to this
    run'. If the UI only says 'already in the list' the user thinks it was
    remembered -- which is exactly why this field exists.
    """
    body = text_of(SETTINGS)
    missing = [field for field in PAYLOAD_FIELDS if ("payload.%s" % field) not in body]
    assert not missing, "settings.js does not read these §4.11 response fields: %s" % missing
    missing = [field for field in ROOT_FIELDS if not re.search(r'root\.%s\b' % field, body)]
    assert not missing, "settings.js does not read these §4.11 per-entry fields: %s" % missing
    assert "reportNotPersisted(" in body, (
        "no extra notice when persisted=false: the user thinks this entry was remembered"
    )


def test_the_server_warning_is_shown_and_errors_are_not_swallowed() -> None:
    """A non-empty `warning` = the change took effect but was not remembered. Swallowing it is lying to the user."""
    body = text_of(SETTINGS)
    assert re.search(r'notify\(String\(payload\.warning\), "info"\)', body), (
        "warning is not shown (or was rewritten into something else)"
    )
    assert body.count('notify(describeError(err), "error")') >= 4, (
        "the four failure paths (list/add/remove × two panels) do not all surface the server's own words"
    )


def test_the_last_root_cannot_be_removed_from_the_ui() -> None:
    """The server returns 400 `last_root`; the UI must **say so up front** rather than letting the user find out after clicking."""
    body = text_of(SETTINGS)
    assert "settings.lastRoot" in body, "the remove button has no 'this is the last one' note"
    assert re.search(r'remove\.disabled\s*=', body), "the remove button for the last entry is not disabled"


def test_session_only_removals_are_labelled_as_such() -> None:
    """A root with `removed_scope: session` comes back after a restart; not labelling it is deceptive."""
    body = text_of(SETTINGS)
    assert 'settings.removedSession' in body, "does not distinguish 'only applies to this run'"
    assert 'settings.sessionOnly' in body, "does not mark which roots come back after a restart"


# ---------------------------------------------------------------------------
# Syntax gate: web/ has no build step, so errors only surface in the browser
# ---------------------------------------------------------------------------


def test_every_frontend_module_parses_as_an_es_module() -> None:
    """Parse every `.js` as an ESM (**syntax check only, no execution**).

    `node --check` decides the module type from the file extension; a `.js` in a
    directory without package.json is treated as CJS, so `import` statements error
    out immediately. Hence copying to `.mjs` before checking -- the only shape in
    which `--check` works.
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed here")
    modules = sorted(JS.glob("*.js"))
    assert modules, "not a single module under js/"
    with tempfile.TemporaryDirectory(prefix="kohya-esm-") as tmp:
        for module in modules:
            target = Path(tmp) / (module.stem + ".mjs")
            target.write_bytes(module.read_bytes())
            done = subprocess.run(
                [node, "--check", str(target)],
                capture_output=True, encoding="utf-8", errors="replace",
            )
            assert done.returncode == 0, "%s fails syntax check:\n%s" % (module.name, done.stderr)
