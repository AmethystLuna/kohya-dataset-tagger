"""Contract, wiring and behavior criteria for the scale/export panel (web/js/scale.js +
trainparams.js) (§4.14).

Three layers:
1. **Contract**: the frontend's format/resample lists and defaults must equal the backend's value
   for value, otherwise requests hit a wall on 422;
2. **Wiring**: api.js declares three routes, the panel really calls them, the done event must
   honor finished:true, and the target folder goes through the directory picker; captions are
   **attached automatically**, with no "sidecar file" input;
3. **Behavior**: the scale_harness.mjs fixture really runs it through with a fake DOM + fake
   fetch + fake EventSource (defaults, request body, shared resolution, done guard, cancel
   endpoint).
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
SCALE = JS / "scale.js"
CROP = JS / "crop.js"
TRAIN = JS / "trainparams.js"
API = JS / "api.js"
APP = JS / "app.js"
STRINGS = JS / "locales" / "zh-CN.js"
BACKEND = REPO / "src" / "kohya_dataset_tagger" / "api" / "scale.py"
SCALER = REPO / "src" / "kohya_dataset_tagger" / "core" / "scaler.py"
HARNESS = REPO / "test" / "fixtures" / "scale_harness.mjs"


def text_of(path: Path) -> str:
    assert path.is_file(), "cannot find %s" % path
    return path.read_text(encoding="utf-8")


def strings_keys() -> set[str]:
    return set(re.findall(r'"([^"\\]+)"\s*:', text_of(STRINGS)))


# ---------------------------------------------------------------------------
# 1. Contract: the frontend lists and defaults == the backend
# ---------------------------------------------------------------------------


def _tuple_names(path: Path, name: str) -> list[str]:
    match = re.search(r"%s: tuple\[str, \.\.\.\] = \(([^)]*)\)" % name, text_of(path))
    assert match is not None, "%s has no %s" % (path.name, name)
    return re.findall(r'"([a-z]+)"', match.group(1))


def _js_array(name: str, path: Path | None = None) -> list[str]:
    """Read a frozen string array out of the frontend module that defines it.

    The resample/format domains live in the store now (trainparams.js): it validates a *remembered*
    name against them, so the panel and the stored entry cannot disagree about what exists.
    """
    source = path or SCALE
    match = re.search(r"%s = Object\.freeze\(\[([^\]]*)\]" % name, text_of(source), re.S)
    assert match is not None, "%s not found in %s" % (name, source.name)
    return re.findall(r'"([a-z]+)"', match.group(1))


def test_resample_and_format_lists_match_the_backend() -> None:
    assert _js_array("RESAMPLE_NAMES", TRAIN) == _tuple_names(SCALER, "RESAMPLE_NAMES")
    assert _js_array("IMAGE_FORMATS", TRAIN) == _tuple_names(SCALER, "IMAGE_FORMATS")


def test_panel_defaults_match_the_backend_defaults() -> None:
    backend = text_of(BACKEND)
    block = re.search(r"SCALE_DEFAULTS = Object\.freeze\(\{(.*?)\}\)", text_of(SCALE), re.S)
    assert block is not None, "SCALE_DEFAULTS not found in scale.js"
    defaults = block.group(1)

    assert re.search(r"no_upscale: bool = True", backend), "the backend's no_upscale default changed"
    assert re.search(r'noUpscale:\s*true', defaults), "the frontend's \"no upscale\" is not checked by default"
    assert re.search(r'output_format: str = "png"', backend)
    assert re.search(r'format:\s*"png"', defaults)
    assert re.search(r'resample: str = "lanczos"', backend)
    assert re.search(r'resample:\s*"lanczos"', defaults)
    assert re.search(r"quality: int = Field\(95", backend)
    assert re.search(r"quality:\s*95", defaults)
    assert re.search(r"optimize: bool = True", backend)
    assert re.search(r"optimize:\s*true", defaults)
    assert re.search(r'caption_extension: str = "(.+?)"', backend), "the backend's caption_extension default was not found"
    assert re.search(r'captionExtension:\s*"(.+?)"', text_of(TRAIN)), "the shared parameters have no caption extension default"


def test_request_body_shape_is_the_frozen_one() -> None:
    """The request body's key set is exactly §4.14's field set; one extra or one missing is swallowed by extra=forbid / the defaults."""
    backend = set(re.findall(r"^    ([a-z_]+):", text_of(BACKEND), re.M))
    frozen = {
        # images is the list the column's scope strip reports (2026-09-20): the export's whole
        # extent, instead of a root the endpoint used to walk recursively.
        "images", "root", "target_dir", "target_width", "target_height", "no_upscale",
        "resample", "output_format", "quality", "optimize", "caption_extension",
        "write_dataset_toml", "overwrite_dataset_toml",
    }
    assert frozen.issubset(backend), "the backend is missing fields: %s" % sorted(frozen - backend)
    front = text_of(SCALE)
    for key in sorted(frozen - {"root"}):
        assert '"%s"' % key in front or key in front, "the frontend request body has no %s" % key


# ---------------------------------------------------------------------------
# 2. Wiring
# ---------------------------------------------------------------------------


def test_routes_are_declared_and_called() -> None:
    api_text = text_of(API)
    for route in ("POST /api/scale/run", "GET /api/scale/stream", "POST /api/scale/cancel"):
        assert '"%s"' % route in api_text, "api.js does not declare %s" % route
    scale_text = text_of(SCALE)
    assert "api.scaleRun(" in scale_text, "the panel does not call the start-export endpoint"
    assert "api.scaleCancel(" in scale_text, "the panel does not call the cancel endpoint"
    assert "scaleStreamUrl(" in scale_text, "progress does not reuse the SSE endpoint"


def test_done_event_is_guarded_by_the_finished_flag() -> None:
    scale_text = text_of(SCALE)
    assert '"progress"' in scale_text and '"done"' in scale_text, "progress / done are not listened to"
    assert "payload.finished !== true" in scale_text, "the done event does not honor finished:true (§4.3)"


def test_caption_is_attached_automatically_without_a_sidecar_field() -> None:
    """The user explicitly asked: no more custom sidecar files, just attach captions the way this tool defines it."""
    scale_text = text_of(SCALE)
    assert "caption_extension: extension" in scale_text or "caption_extension" in scale_text, (
        "the request body has no caption_extension"
    )
    assert "captionExtension()" in scale_text, "the caption extension does not come from the shared parameters"
    assert "sidecars" not in scale_text, "a customizable sidecar file list appeared in the panel"
    assert "copy_sidecar" not in scale_text, "a customizable sidecar file extension appeared in the panel"
    keys = strings_keys()
    assert not any(key.startswith("scale.sidecar") for key in keys), "the copy still has sidecar file settings"


def test_the_parameters_are_remembered_and_the_permissions_are_not() -> None:
    """The report: the panel came back as the defaults on every page load.

    One owner: the store reads its key when it is built and writes it whenever a setter accepts a
    value, so a panel cannot keep a second copy nobody remembers (no panel touches localStorage).
    The snapshot is an explicit field list, and that list is also where "what is deliberately not
    remembered" is pinned: the target directory (a stored destination is a location nobody chose
    this session) and `overwrite_dataset_toml` (a standing permission to replace a hand-tuned
    file). The behavioural half is sections 2b and 10 of `scale_harness.mjs`.
    """
    train = text_of(TRAIN)
    assert 'TRAIN_PARAMS_STORAGE_KEY = "kdt.trainParams"' in train, "the store has no storage key"
    assert "export function readStoredTrainParams(" in train, "nothing reads what was remembered"
    assert "writeStoredTrainParams(storage" in train, "nothing writes the remembered values"
    create = train[train.index("export function createTrainParams("):]
    assert "readStoredTrainParams(storage)" in create, "the store is built without what was remembered"
    assert "Object.assign({}, remembered, initial || {})" in create, (
        "the precedence between the remembered values and an explicit seed is not stated in one place"
    )
    persist_body = train[train.index("function persist() {"):]
    persist_body = persist_body[: persist_body.index("\n  }")]
    snapshot = re.search(r"Object\.assign\(\{(.*?)\},\s*processing\)", persist_body, re.S)
    assert snapshot is not None, "persist() no longer writes one explicit snapshot"
    assert sorted(re.findall(r"([A-Za-z]+),", snapshot.group(1))) == [
        "captionExtension", "height", "width", "writeToml",
    ], "the remembered field list changed - is the new field a parameter, or a permission?"
    assert train.count("persist();") >= 3, "a setter accepts a value without remembering it"
    scale_text = text_of(SCALE)
    assert "params.setScaleOptions(" in scale_text, "the panel's own switch never reaches the store"
    assert "panelOptions.writeToml" in scale_text, "a remembered switch is not painted on load"
    for path in (SCALE, CROP):
        assert "localStorage" not in text_of(path), (
            "%s reaches for storage itself: the store is the one thing that remembers a parameter"
            % path.name
        )


def test_app_wires_the_panel_and_shares_parameters() -> None:
    app_text = text_of(APP)
    assert 'from "./scale.js"' in app_text or "createScalePanel" in app_text, "app.js does not import the scale panel"
    assert "createTrainParams(" in app_text, "app.js does not create the shared parameters"
    assert "params: trainParams" in app_text, "the export panel did not receive the shared parameters"
    assert "getParams: () => trainParams" in app_text, "the scale panel did not receive the shared parameters"
    assert "  scope,\n  getRoot: () => state.dir,\n" in app_text, (
        "the scale panel does not receive the column's scope control, so it would export a directory"
    )
    assert "scalePanel.setPickerEnabled(enabled, title)" in app_text, (
        "under a non-loopback address the scale panel's directory picker is not disabled along with it"
    )
    assert 'tab.dataset.tab === "scale"' in app_text or "scalePanel.onShow()" in app_text, (
        "the scale panel is not refreshed when switching tabs"
    )
    assert "ensureTargetRoot" in app_text, "the selected target folder is not added to the allowlist first"


def test_strings_cover_every_panel_label() -> None:
    keys = strings_keys()
    required = [
        "tab.scale", "scale.title", "scale.hint", "scale.sourceNone", "scale.sourceValue",
        "scale.target", "scale.targetPlaceholder", "scale.browse", "scale.targetHint",
        "scale.params", "scale.resolution", "scale.resample", "scale.format", "scale.quality",
        "scale.optimize", "scale.noUpscale", "scale.noUpscaleHint", "scale.writeToml",
        "scale.writeTomlHint", "scale.start", "scale.starting", "scale.cancel",
        "scale.cancelling", "scale.cancelRequested", "scale.progress", "scale.current",
        "scale.errorsTitle", "scale.errorsMore", "scale.summary", "scale.summaryCancelled",
        "scale.datasetToml", "scale.datasetTomlNone", "scale.datasetTomlFailed",
        "scale.datasetTomlUnknown", "scale.done", "scale.doneFailed", "scale.doneCancelled",
        "scale.noSource", "scale.noTarget", "scale.pickerDisabled",
        # 2026-09-20: the reason the panel cannot start while the scope strip has no images.
        "scale.scopeNone",
        "scale.runFailed", "scale.streamLost",
        # 2026-09-19: the missing-job-id failure is shared with the batch / autotag / cache
        # panels, so the key moved to the common group.
        "common.noJobId",
    ]
    missing = [key for key in required if key not in keys]
    assert not missing, "strings.js is missing copy: %s" % missing
    # every t("scale....") literal must be defined (test_web_contract catches it once more too).
    used = set(re.findall(r't\("(scale\.[a-zA-Z]+)"', text_of(SCALE)))
    assert used.issubset(keys), "the scale panel uses keys that strings.js does not have: %s" % sorted(used - keys)


def test_export_panel_reads_the_shared_resolution() -> None:
    app_text = text_of(APP)
    assert "sharedResolution" in app_text, "the export panel does not read the resolution from the shared parameters"
    assert "params.setResolution(" in app_text, "the export panel does not write the resolution back to the shared parameters"
    assert "paintSharedParams" in app_text, "the export panel does not follow changes to the shared parameters"


# ---------------------------------------------------------------------------
# 3. Behavior: really run scale.js (fake DOM + fake fetch + fake EventSource)
# ---------------------------------------------------------------------------


def test_an_existing_dataset_toml_is_replaced_only_on_purpose() -> None:
    """The export writes dataset.toml, and a hand-tuned one must not be thrown away by a checkbox nobody read.

    Same rule as the images this job exports (never overwrite on its own), and the explicit way through
    leaves the previous file at dataset.toml.bak - so the panel has to be able to say all three outcomes:
    written, left alone, failed.
    """
    body = text_of(SCALE)
    assert "overwriteToml: false," in body, "the panel replaces an existing dataset.toml by default"
    assert "overwrite_dataset_toml: source.overwriteToml === true," in body, (
        "the request never carries the choice, so the backend default decides for the user"
    )
    assert "overwriteToml.box.disabled = !writeToml.box.checked;" in body, (
        "the replace option is live while dataset.toml is not being generated at all"
    )
    assert 't("scale.datasetTomlSkipped")' in body, "a file that was left alone reads as a failure"
    assert "t(\"scale.datasetTomlBackup\", { path: String(toml), backup: String(tomlBackup) })" in body, (
        "a replaced file never says where the previous one went"
    )

    backend = text_of(BACKEND)
    assert "overwrite_dataset_toml: bool = False" in backend, "the backend default is not 'leave it alone'"
    assert '"dataset_toml_skipped": self._dataset_toml_skipped,' in backend, (
        "the summary cannot tell 'left alone' from 'failed'"
    )
    assert "if target.exists() and not settings.get(\"overwrite_dataset_toml\"):" in backend, (
        "the existing file is not consulted before the write"
    )
    assert "captions.backup_file(target)" in backend, (
        "the overwrite path keeps no copy of what it replaces"
    )


def test_scale_panel_behaves_headlessly_against_a_fake_dom() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("no node on this machine")
    assert HARNESS.is_file(), "missing headless behavior fixture %s" % HARNESS
    done = subprocess.run(
        [node, str(HARNESS), str(WEB)],
        capture_output=True, encoding="utf-8", errors="replace",
    )
    assert done.returncode == 0, "scale panel behavior criteria failed:\n%s\n%s" % (done.stdout, done.stderr)
    assert "scale harness OK" in done.stdout


def test_the_done_report_reads_the_request_not_the_checkbox() -> None:
    """Ticking write_dataset_toml mid-run used to report a finished job as a failed toml write."""
    body = text_of(SCALE)
    assert "let jobWroteToml = false;" in body, "the toml decision is read from the live checkbox"
    assert "jobWroteToml = payload.write_dataset_toml === true;" in body, (
        "the run does not remember what it asked the backend to do"
    )
    assert "} else if (jobWroteToml) {" in body, "the summary still reads the checkbox at paint time"
    assert "writeDatasetToml: writeToml.box.checked," in body, "the request no longer carries the checkbox value"


def test_the_caption_extension_is_editable_where_the_run_starts() -> None:
    """The extension names the sidecars and the caption_extension this run writes; it lived only on the export tab."""
    body = text_of(SCALE)
    assert 'const captionExtensionField = labelled("export.captionExtension", captionExtInput);' in body, (
        "the scale panel does not show the caption extension it is about to use"
    )
    assert 'captionExtension: params ? params.captionExtension() : ".txt",' in body, (
        "the request stopped taking the extension from the shared store"
    )
    assert "params.setCaptionExtension(captionExtInput.value);" in body, (
        "the field is read-only, so the other tab still has to be visited to change it"
    )
    assert (
        "if (document.activeElement !== captionExtInput) captionExtInput.value = String(params.captionExtension());"
        in body
    ), "an edit in the other panel never reaches this field"
