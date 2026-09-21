"""Contract, wiring and behavior criteria for the crop panel (web/js/crop.js + cropbox.js) (§4.17).

Three layers, the same division the other panel tests use:

1. **Contract**: the frontend's request body and its defaults must equal the backend's value for
   value - a drift is a 422 on a body the user already confirmed, which is the worst moment to
   discover it. The response's key set is pinned against the spec.
2. **Wiring**: api.js declares the route, the panel calls it, the images come from the shared scope
   strip, the pipeline settings from the shared store, the tagger settings from the tagger panel -
   and the run ends in exactly one function, because three endings (the last image, the end button,
   Esc) each owe the user the same summary, refresh and optional tag job.
3. **Behavior**: crop_harness.mjs really runs the module against a fake DOM, a fake fetch and a
   fake EventSource: the confirmed rectangle reaches the request, the box is carried over between
   images, a locked ratio survives a drag, a failed write keeps the dialog on the same image, and
   the tagger step calls the existing /api/autotag/run.
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
CROP = JS / "crop.js"
CROPBOX = JS / "cropbox.js"
SCALE = JS / "scale.js"
TRAIN = JS / "trainparams.js"
AUTOTAG = JS / "autotag.js"
API = JS / "api.js"
APP = JS / "app.js"
STRINGS = JS / "locales" / "zh-CN.js"
BACKEND = REPO / "src" / "kohya_dataset_tagger" / "api" / "crop.py"
SPEC = REPO / ".github" / "memory" / "p0-spec.md"
HARNESS = REPO / "test" / "fixtures" / "crop_harness.mjs"


def text_of(path: Path) -> str:
    assert path.is_file(), "cannot find %s" % path
    return path.read_text(encoding="utf-8")


def strings_keys() -> set[str]:
    return set(re.findall(r'"([^"\\]+)"\s*:', text_of(STRINGS)))


# ---------------------------------------------------------------------------
# 1. Contract: the frontend body == the backend model
# ---------------------------------------------------------------------------


def test_the_archive_format_is_keep_on_both_sides() -> None:
    assert 'KEEP_FORMAT = "keep"' in text_of(BACKEND), "the backend's keep sentinel changed"
    assert 'CROP_ARCHIVE_FORMAT = "keep"' in text_of(CROP), (
        "the panel stopped asking for 'keep' - the archive would silently change format"
    )
    assert 'format: CROP_ARCHIVE_FORMAT,' in text_of(CROP), "the request body does not carry the format"


def test_the_crop_formats_match_the_backend() -> None:
    """The four writable format names are the scaler's three plus "keep"; api.js and crop.js never
    spell them out again, so the only thing to check is that the backend still derives them."""
    backend = text_of(BACKEND)
    assert 'CROP_FORMATS: tuple[str, ...] = (KEEP_FORMAT,) + scaler.IMAGE_FORMATS' in backend, (
        "the format list stopped being derived from the scaler's, so the two can drift"
    )
    scaler = text_of(REPO / "src" / "kohya_dataset_tagger" / "core" / "scaler.py")
    formats = re.search(r'IMAGE_FORMATS: tuple\[str, \.\.\.\] = \(([^)]*)\)', scaler)
    assert formats is not None, "core/scaler.py has no IMAGE_FORMATS"
    assert re.findall(r'"([a-z]+)"', formats.group(1)) == ["png", "jpeg", "webp"]


def test_the_request_body_keys_are_the_frozen_ones() -> None:
    backend = text_of(BACKEND)
    # CropRequest's own fields (the models are declared with extra="forbid", so an extra key is a 422).
    for field in ("src", "crop", "format", "scale"):
        assert re.search(r"^    %s:" % field, backend, re.M), "CropRequest lost %s" % field
    for field in ("x", "y", "width", "height"):
        assert re.search(r"^    %s:" % field, backend, re.M), "CropBody lost %s" % field
    for field in ("target_width", "target_height", "no_upscale", "resample", "quality", "optimize"):
        assert re.search(r"^    %s:" % field, backend, re.M), "CropScaleBody lost %s" % field

    body = text_of(CROP)
    block = re.search(r"export function buildCropRequest\(input\) \{(.*?)\n\}", body, re.S)
    assert block is not None, "buildCropRequest not found"
    emitted = block.group(1)
    for key in ("src", "crop", "format", "scale"):
        assert key + ":" in emitted, "the request never carries %s" % key
    for key in ("x", "y", "width", "height"):
        assert key + ":" in emitted, "the crop box never carries %s" % key
    for key in ("target_width", "target_height", "no_upscale", "resample", "quality", "optimize"):
        assert key + ":" in emitted, "the scaling half never carries %s" % key
    assert "scale: scale" in emitted and ": null" in emitted, (
        "with scaling off the body must carry an explicit null, not an object with a flag"
    )
    assert "caption_extension" not in body, "the crop panel must not attach a caption (§4.17 rule 5)"


#: §4.17's response keys, written out here so a field cannot be added on one side only.
CROP_RESPONSE_KEYS = {
    "src", "archived", "format", "crop", "crop_requested",
    "orig_width", "orig_height", "crop_width", "crop_height",
    "output_width", "output_height", "scale", "resampled", "upscale_skipped", "copied", "warnings",
}


def test_the_response_shape_is_the_documented_one() -> None:
    returned = set(re.findall(r'^        "([a-z_]+)":', text_of(BACKEND), re.M))
    assert returned == CROP_RESPONSE_KEYS, (
        "the response is not the frozen one: missing=%s extra=%s"
        % (sorted(CROP_RESPONSE_KEYS - returned), sorted(returned - CROP_RESPONSE_KEYS))
    )
    spec = text_of(SPEC)
    documented = re.search(r"#### Request: `POST /api/crop/apply`(.*?)Errors:", spec, re.S)
    assert documented is not None, "§4.17 no longer documents the crop endpoint"
    for key in sorted(CROP_RESPONSE_KEYS):
        assert '"%s"' % key in documented.group(1), "§4.17 does not document %s" % key


def test_panel_defaults_and_the_two_switches() -> None:
    body = text_of(CROP)
    block = re.search(r"CROP_DEFAULTS = Object\.freeze\(\{(.*?)\}\)", body, re.S)
    assert block is not None, "CROP_DEFAULTS not found"
    defaults = block.group(1)
    # Scaling after the crop is the "combined with scaling" case and it is a no-op on the pixels
    # once the box is normalized to the target; tagging is opt-in.
    assert re.search(r"scale:\s*true", defaults), "scaling after the crop is off by default"
    assert re.search(r"tag:\s*false", defaults), "tagging is on by default"
    assert re.search(r'aspect:\s*"free"', defaults), "the default aspect ratio is not free"
    assert "CROP_ARCHIVE_FORMAT" in body and "CROP_MIN_SIZE" in body


def test_the_aspect_presets_are_the_frozen_list() -> None:
    presets = re.findall(r'\{ id: "([^"]+)",', text_of(CROPBOX))
    assert presets == [
        "free", "source", "1:1", "3:2", "2:3", "4:3", "3:4", "5:4", "4:5", "16:9", "9:16",
    ], "the aspect presets changed: %s" % presets
    # The two named entries carry copy; the numerals are numerals in every language.
    assert "crop.aspectFree" in text_of(CROPBOX) and "crop.aspectSource" in text_of(CROPBOX)


# ---------------------------------------------------------------------------
# 2. Wiring
# ---------------------------------------------------------------------------


def test_the_route_is_declared_and_called() -> None:
    assert '"POST /api/crop/apply"' in text_of(API), "api.js does not declare the crop route"
    assert "api.cropApply(" in text_of(CROP), "the panel does not call the crop endpoint"
    assert "cropApply: (payload) => request(" in text_of(API), "the route is declared but not wired"


def test_the_tagger_step_reuses_the_existing_job_endpoint() -> None:
    body = text_of(CROP)
    assert "api.autotagRun(" in body, "the tagger step does not reuse /api/autotag/run"
    assert "autotagStreamUrl(" in body, "the tagger step does not reuse the tagger's SSE stream"
    assert "api.autotagCancel(" in body, "the tagger step has no cancel"
    assert "payload.finished !== true" in body, "the done event does not honor finished:true (§4.3)"
    # One tagging implementation, not two: no thresholds table, no model picker, no write-mode
    # combobox on this panel - the settings are handed over by the tagger panel.
    assert "autotag-model" not in body and "autotag-thresholds" not in body, (
        "the crop panel grew its own tagger settings instead of reusing the Tagger tab's"
    )
    # crop.js consumes the getter app.js hands it; autotag.js exposes it from its own controls.
    assert "getTaggerParams" in body and "getTaggerParams()" in body, (
        "the crop panel does not consume the tagger panel's settings"
    )
    assert "taggerParams:" in text_of(AUTOTAG), "the tagger panel does not hand its settings over"


def test_the_panel_reads_the_shared_store_and_the_scope_strip() -> None:
    body = text_of(CROP)
    assert "params.resolution()" in body, "the resolution does not come from the shared store"
    assert "params.processing()" in body, (
        "the pipeline settings (resample/quality/optimize/no-upscale) do not come from the shared store"
    )
    assert "scope.paths()" in body, "the images do not come from the column's scope strip"
    assert "scope.subscribe(" in body, (
        "the panel reads the strip only when its tab is shown; the strip changes while it is open"
    )
    assert "scopeValues" not in body and "state.images" not in body, (
        "the panel grew a second answer to 'which images?'"
    )
    # The store itself gained the pipeline settings for exactly this reason.
    train = text_of(TRAIN)
    for key in ("noUpscale", "resample", "quality", "optimize"):
        assert key + ":" in train, "the shared store lost %s" % key
    assert "processing()" in train and "setProcessing(" in train


def test_a_run_ends_in_exactly_one_function() -> None:
    """Three endings - the last image confirmed, the end button, Esc - owe the user the same
    summary, the same directory refresh and the same optional tag job. Closing the dialog alone
    looked identical and did none of the three."""
    body = text_of(CROP)
    assert "function endRun() {" in body, "there is no single run-ending function"
    # Two statements and one arrow callback: the last image, Esc, and the end button. The
    # definition itself is not a call site, so it does not count.
    calls = body.count("endRun()") - body.count("function endRun()")
    assert calls == 3, (
        "expected exactly three endings to call endRun() (last image, end button, Esc), got %d" % calls
    )
    assert "dialog.finishButton.addEventListener(\"click\", () => endRun());" in body
    tail = body.split("function endRun() {")[1][:400]
    assert "closeDialog();" in tail and "finishRun()" in tail, "endRun does not close and finish"
    assert "if (onArchived) onArchived();" in body, "the directory on screen is never refreshed"
    assert "if (archived > 0 && tagging) await startTagJob(finished.archived);" in body, (
        "the optional tagger step is not wired to the end of the run"
    )


def test_app_wires_the_panel_to_its_three_owners() -> None:
    app = text_of(APP)
    assert 'from "./crop.js"' in app and "createCropPanel(" in app, "app.js does not build the crop panel"
    assert "getParams: () => trainParams," in app, "the crop panel did not receive the shared store"
    assert "getTaggerParams: () => autotagPanel.taggerParams()," in app, (
        "the crop panel did not receive the tagger panel's settings"
    )
    assert "scope," in app.split("createCropPanel(")[1][:400], "the crop panel did not receive the scope strip"
    assert "onArchived: () => { void refreshAfterCrop(); }," in app, (
        "the app does not refresh the listing after an archive run"
    )
    assert 'tab.dataset.tab === "crop"' in app, "the crop panel is not refreshed when its tab is shown"
    assert "relabelComponent(cropPanel);" in app, "the crop panel is not relabelled on a language switch"
    assert 'entry.panel === "crop"' in app, "a tag job started by the crop panel cannot be resumed after a reload"


def test_every_crop_key_used_by_the_panel_exists() -> None:
    keys = strings_keys()
    used = set(re.findall(r't\("(crop\.[a-zA-Z]+)"', text_of(CROP)))
    used |= set(re.findall(r't\("(crop\.[a-zA-Z]+)"', text_of(CROPBOX)))
    assert used, "no crop copy is used at all"
    missing = sorted(key for key in used if key not in keys)
    assert not missing, "the crop panel uses keys the packs do not have: %s" % missing
    for key in ("tab.crop", "crop.title", "crop.hint", "crop.start", "crop.dialogTitle",
                "crop.confirm", "crop.skip", "crop.finish", "crop.normalize", "crop.reset",
                "crop.aspect", "crop.readout", "crop.keys", "crop.summary"):
        assert key in keys, "missing copy: %s" % key


def test_the_panel_never_writes_without_a_confirmation() -> None:
    """The whole feature is 'the user confirms each rectangle'. A write that happens anywhere but
    in confirmCurrent() would be a crop nobody approved."""
    body = text_of(CROP)
    assert body.count("api.cropApply(") == 1, "there is a second write path in the panel"
    tail = body.split("api.cropApply(")[1][:120]
    assert "request" in tail, "the write no longer goes through the confirmed rectangle"


# ---------------------------------------------------------------------------
# 3. Behavior: really run crop.js (fake DOM + fake fetch + fake EventSource)
# ---------------------------------------------------------------------------


def test_the_crop_panel_behaves_headlessly_against_a_fake_dom() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("no node on this machine")
    assert HARNESS.is_file(), "missing headless behavior fixture %s" % HARNESS
    done = subprocess.run(
        [node, str(HARNESS), str(WEB)],
        capture_output=True, encoding="utf-8", errors="replace",
    )
    assert done.returncode == 0, "crop panel behavior criteria failed:\n%s\n%s" % (done.stdout, done.stderr)
    assert "crop harness: all assertions passed" in done.stdout
