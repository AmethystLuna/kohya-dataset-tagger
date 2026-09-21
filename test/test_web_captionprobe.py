"""Batched caption probing (web/js/captionprobe.js and its wiring into app.js).

The probe used to be one GET /api/captions per image and re-ran the whole filter after every
response (O(N^2) over the directory). Two kinds of criterion here: the **wiring** (app.js really
calls the batch endpoint and the module) and the **behaviour** (really run the module headlessly:
691 images -> two requests, order kept, failure classification, malformed body).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
WEB = REPO / "src" / "kohya_dataset_tagger" / "web"
JS = WEB / "js"
APP = JS / "app.js"
API = JS / "api.js"
PROBE = JS / "captionprobe.js"


def text_of(path: Path) -> str:
    assert path.is_file(), "cannot find %s" % path
    return path.read_text(encoding="utf-8")


def test_api_declares_the_batch_read_route() -> None:
    source = text_of(API)
    assert 'captionsRead: "POST /api/captions/read"' in source
    assert "readCaptions:" in source


def test_app_probes_in_batches_not_one_request_per_image() -> None:
    source = text_of(APP)
    assert 'from "./captionprobe.js"' in source
    assert "api.readCaptions(" in source
    assert "captionProbeChunks(" in source
    # the per-image primitive must be gone from the probe path
    assert "mapLimit(" not in source, "the per-image probe loop came back"
    # getCaption stays for the single-image refresh only
    assert "api.getCaption(" in source


def test_captionprobe_module_exists() -> None:
    assert PROBE.is_file(), "the batched probe helper is missing"


def test_captionprobe_behaves_headlessly() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("no node on this machine")
    harness = REPO / "test" / "fixtures" / "captionprobe_harness.mjs"
    assert harness.is_file(), "missing headless behavior fixture %s" % harness
    done = subprocess.run(
        [node, str(harness), str(WEB)],
        capture_output=True, encoding="utf-8", errors="replace",
    )
    assert done.returncode == 0, "caption probe behavior criteria failed:\n%s\n%s" % (done.stdout, done.stderr)
    assert "captionprobe harness OK" in done.stdout
