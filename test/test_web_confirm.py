"""The in-app confirmation dialog (`web/js/confirm.js`) and its call sites.

`window.confirm` was doing this job in four places. Its buttons come from the browser UI language
(a Chinese interface got "OK / Cancel"), the body cannot name the action, and a destructive question
looked exactly like a harmless one - the audit put the two ends of that together: the app confirmed
the reversible cache rebuild while deleting a root directory (`roots.txt`, a user-authored file)
without asking at all.

The dialog is one module with one behaviour, so the behaviour is tested for real through
`test/fixtures/confirm_harness.mjs` (fake DOM): the action button names the action, a dangerous
question focuses Cancel, and the three ways of saying no - Escape, the cancel button and a click on
the backdrop - all resolve false while a click inside the dialog does not.
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
LOCALES = JS / "locales"


def source(name: str) -> str:
    return (JS / name).read_text(encoding="utf-8")


def test_the_browser_dialog_is_gone_from_the_app() -> None:
    app = source("app.js")
    assert "window.confirm(" not in app, "a call site still uses the browser dialog"
    assert 'import { createConfirm } from "./confirm.js";' in app, "the dialog is not imported"
    assert "const askConfirm = (spec) => confirmDialog.ask(spec);" in app, "no shared dialog instance"
    for seam in ("confirm: askConfirm,",):
        assert app.count(seam) >= 5, (
            "every panel that asks a question must get the dialog, found %d" % app.count(seam)
        )


def test_the_dialog_names_the_action_and_defaults_to_no() -> None:
    body = source("confirm.js")
    assert 'confirm.textContent = options.confirmLabel || t("common.confirm");' in body, (
        "the action button falls back to a word that names nothing"
    )
    assert '(options.danger ? cancel : confirm).focus();' in body, (
        "a destructive question must focus Cancel, so a stray Enter cannot delete"
    )
    assert "if (event.target === backdrop) close(false);" in body, "a click outside does not cancel"
    assert 'document.addEventListener("keydown", onKeyDown);' in body and "document.removeEventListener" in body, (
        "the Escape listener is not cleaned up"
    )
    assert "if (restoreFocusTo && typeof restoreFocusTo.focus === \"function\") restoreFocusTo.focus();" in body, (
        "focus is not handed back"
    )
    assert 'title.textContent = options.title || options.confirmLabel || t("confirm.title");' in body, (
        "the dialog is titled with a generic word instead of the action it is asking about, "
        "so a screen reader announces nothing useful"
    )


def test_removing_a_root_or_a_model_directory_asks_first() -> None:
    body = source("settings.js")
    assert body.count("const confirmFn = config.confirm || (() => true);") == 2, (
        "one of the two settings panels has no way to ask"
    )
    assert 't("settings.rootRemoveConfirm", { path: root.path })' in body, "removing a root asks nothing"
    assert 't("settings.modelRootRemoveConfirm", { path: root.path })' in body, (
        "deleting a line from model_paths.txt asks nothing"
    )
    assert "if (!ok) return;" in body, "the answer is collected but not acted on"


def test_every_question_has_copy_in_every_pack() -> None:
    keys = (
        "confirm.title",
        "settings.rootRemoveConfirm",
        "settings.rootRemoveOk",
        "settings.modelRootRemoveConfirm",
        "settings.modelRootRemoveSessionConfirm",
        "settings.modelRootRemoveOk",
        "settings.modelRootRemoveSessionOk",
        "caption.dropLinesOk",
        "caption.saveBlockedLeave",
    )
    for name in ("zh-CN.js", "en.js", "ja.js"):
        text = (LOCALES / name).read_text(encoding="utf-8")
        for key in keys:
            match = re.search(r'"' + re.escape(key) + r'":\s*"([^"]*)"', text)
            assert match is not None, "%s has no %s" % (name, key)
            assert match.group(1).strip(), "%s %s is empty" % (name, key)


def test_the_dialog_behaves_headlessly_against_a_fake_dom() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("no node on this machine")
    harness = REPO / "test" / "fixtures" / "confirm_harness.mjs"
    assert harness.is_file(), "missing headless behavior fixture %s" % harness
    done = subprocess.run(
        [node, str(harness), str(WEB)],
        capture_output=True, encoding="utf-8", errors="replace",
    )
    assert done.returncode == 0, "confirm dialog behavior criteria failed:\n%s\n%s" % (done.stdout, done.stderr)
    assert "confirm harness OK" in done.stdout
