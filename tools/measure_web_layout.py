"""Measure the real frontend's layout in headless Chrome - without the app, and without a dataset.

Why this tool exists
--------------------
Three rounds in a row (caption dock, gallery filter strip, shared scope strip) measured the UI with an
ad-hoc headless-Chrome probe, and two of those probes were deleted as scratch afterwards. So every round
re-earned the same thing: "the strip is 60px tall, the panels lost 68px" rested on a script nobody could
re-run. This is that probe, committed, with the canned answers it needs and a fixed output shape.

What it does
------------
    python tools/measure_web_layout.py                 # geometry as JSON on stdout
    python tools/measure_web_layout.py --scenario fail # /api/cache/status answers 500 instead
    python tools/measure_web_layout.py --toml          # the fake root "has" a dataset.toml (§4.16)
    python tools/measure_web_layout.py --theme dark      # only the dark pass (default: both)
    python tools/measure_web_layout.py --lang en         # measure another pack (the app's own ?lang=)
    python tools/measure_web_layout.py --shot shots/     # also write probe-light.png / probe-dark.png
    python tools/measure_web_layout.py --out run.json

It serves the real web/ directory plus a **canned /api/***, loads the real index.html in a same-origin
iframe inside headless Chrome, drives it (click a tile, walk every tab, generate the dataset.toml), and
prints the geometry of the page header, the three columns, the tab panels, the scope strip, the
readiness strip, the caption dock and the gallery - plus which surfaces are on screen on which tab.

The theme passes
----------------
--theme both (the default) runs **three** loads of the same page, and they are three different claims:

- **light** and **dark** seed localStorage["kdt.theme"] in the wrapper page BEFORE the iframe is
  created. index.html's boot script reads it while the document is still parsing, so what is measured
  is the boot path - the state a reloaded page is really in - and never the toggle. Each block carries
  the resolved data-theme attribute, the computed color-scheme, every theme token read back through
  getComputedStyle, and a contrast table.
- **toggled** loads light and then CLICKS THE REAL SWITCH (#theme-dark). Its tokens must equal the
  dark pass's, which is what makes "the switch applies the theme a reload would" a measurement rather
  than a promise; test/test_measure_web_layout.py pins the click and the JSON carries the comparison
  in theme_toggle.tokens_match_dark.

The contrast table is the part that catches a theme nobody can read: for each surface it takes the
computed colour and the **nearest ancestor with a non-transparent background-color**, compositing any
translucent layers in between (a badge is a translucent green over the tile's own background). A class
that only exists in one state - a failed toast, a running job, a present badge - gets a swatch appended
to the host it belongs to, so the cascade and the ancestor chain are the app's own; the probe paints
nothing, every colour comes from app.css.

Safety
------
- It never starts the backend and never needs it: every /api/* answer comes from the constants below.
- It never touches a dataset: the directory it pretends to serve (/probe/dataset) is not a path on any
  machine, and the canned API does no filesystem work at all.
- It writes a Chrome profile inside a scratch directory it prints ("scratch" in the JSON):
  tempfile.mkdtemp() by default, --scratch to put it somewhere else, --keep-scratch to keep it.
  Without that flag the directory is removed again when the run ends. --shot DIR is the only other
  thing it writes, and only when it is asked for: two PNGs in the directory the caller names.

The browser
-----------
--chrome <path>, else $KDT_CHROME / $CHROME_PATH, else chrome / chromium / google-chrome on PATH, else
the usual install locations. A machine with no Chromium browser is a clear message on stderr and exit
code 2 - not a traceback.
"""
from __future__ import annotations

import argparse
import base64
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WEB_ROOT = REPO_ROOT / "src" / "kohya_dataset_tagger" / "web"

#: The dataset the canned API pretends to serve. Deliberately not a real path: nothing in this tool
#: reads or writes a dataset directory, and the frontend only does string work on what it is given.
FAKE_ROOT = "/probe/dataset"
FAKE_DIRS = ("alpha", "beta", "gamma")
FAKE_IMAGES = ("1.png", "2.png", "3.png", "4.png", "5.png")
#: Of FAKE_IMAGES, how many carry a caption - so the readiness strip has a real missing-caption count.
FAKE_CAPTIONS = 3
#: Stale text-encoder caches the canned /api/cache/status reports (scenario "stale").
FAKE_STALE = ("1.png", "2.png")
#: Whether the canned dataset root pretends to have a dataset.toml (§4.16), and the stat it reports
#: when it does. Fixed values: the canned API stays pure (no clock, no filesystem).
FAKE_TOML_MTIME = 1700000123.0
FAKE_TOML_SIZE = 2048

#: Views to measure, in the order app.css lays them out. "#readiness" and "#scope-strip" are the two
#: strips this repository keeps moving controls into, so they are measured like any other panel.
#: The header's own controls and the gallery toolbar are here for the same reason: they are what the
#: 2026-09-20 round rearranged, and "the row wraps at 900px without losing or overlapping a control"
#: is only a measurement if each control has its own box.
SELECTORS = (
    ".topbar",
    ".brand-lockup",
    "#btn-refresh",
    "#btn-palette",
    ".breadcrumb-row",
    "#dir-counts",
    "#lang-select",
    "#theme-dark",
    "#readiness",
    ".layout",
    ".panel-left",
    "#center-panel",
    ".panel-right",
    ".tabs",
    "#scope-strip",
    ".tab-panels",
    "#caption-dock",
    "#gallery-toolbar",
    "#gallery-filter",
    "#gallery",
    "#jobs",
    "#toasts",
    "#palette",
    "#palette-input",
    "#palette-list",
)

#: The scenarios /api/cache/status can be in. "stale" is the interesting default; "fail" is the one
#: the readiness strip must not paint as "0 stale".
SCENARIOS = ("stale", "empty", "fail")

#: Every theme token, read back through getComputedStyle on <html>. The list is the whole palette by
#: name, so two passes can be compared by VALUE: a token that exists in one theme only reads as the
#: empty string here, and a token that drifted reads as a different value.
THEME_TOKENS = (
    "--bg", "--bg-panel", "--bg-elev", "--line", "--text", "--text-dim",
    "--accent", "--accent-dim", "--danger", "--warn", "--ok", "--danger-bg",
    "--tint-accent", "--tint-warn", "--tint-ok", "--tint-danger",
    "--checker-a", "--checker-b", "--skeleton-a", "--skeleton-b", "--skeleton-flat",
    "--shadow-menu", "--shadow-dialog", "--scrim-dialog", "--scrim-zoom",
    "--badge-missing-fg", "--badge-ring", "--badge-unknown-bg", "--badge-unknown-fg",
    "--badge-present-bg", "--badge-present-fg", "--zoom-stage-bg", "--zoom-fg",
    "--zoom-nav-bg", "--zoom-nav-bg-hover", "--zoom-nav-border",
    "--crop-outline", "--crop-shade", "--crop-guide", "--crop-handle-bg", "--crop-handle-border",
)

#: The surfaces the contrast table covers. Every one of them carries text, so the minimum is WCAG AA
#: for normal-size text - nothing in this UI is large text (the badges are 11px bold, not 18.66px).
CONTRAST_SELECTORS = (
    "body",
    ".topbar",
    ".panel",
    ".hint",
    ".btn",
    ".btn-primary",
    ".btn-danger",
    ".readiness-item.is-ok",
    ".readiness-item.is-todo",
    ".readiness-item.is-bad",
    ".chip",
    ".freq-item",
    ".tab.is-active",
    ".toast-error",
    ".job-row",
    ".palette-item.is-active",
    ".palette-group-head",
    ".zoom-nav",
    ".badge-missing",
    ".badge-unknown",
    ".badge-present",
)
CONTRAST_MIN = 4.5

#: (selector, host, tag, class): the classes that only exist in one app state. The probe appends one
#: element carrying the class to the host it belongs to ONLY when no real one is on screen, so the
#: rule, the cascade and the background it composites over are the app's own and the probe paints
#: nothing. Without these, a table built from one page state would silently skip a third of the
#: surfaces this round is about.
CONTRAST_SEEDS = (
    (".badge-missing", "#gallery .tile .tile-frame", "span", "badge badge-missing"),
    (".badge-unknown", "#gallery .tile .tile-frame", "span", "badge badge-unknown"),
    (".badge-present", "#gallery .tile .tile-frame", "span", "badge badge-present"),
    (".readiness-item.is-ok", "#readiness", "button", "btn btn-mini readiness-item is-ok"),
    (".readiness-item.is-bad", "#readiness", "button", "btn btn-mini readiness-item is-bad"),
    (".toast-error", "#toasts", "div", "toast toast-error"),
    (".job-row", "#jobs", "div", "job-row"),
    (".palette-item.is-active", "#palette-list", "div", "palette-item is-active"),
    # The group heads are painted by palette.js and the list is emptied when the panel closes, so the
    # end-of-walk contrast pass always needs the band drawn for it - one swatch, the app's own rule.
    (".palette-group-head", "#palette-list", "div", "palette-group-head"),
)

#: The passes --theme can ask for, in the order they run: (name, seeded theme, click the real switch).
THEME_RUNS = (
    ("light", "light", False),
    ("dark", "dark", False),
    ("toggled", "light", True),
)


def theme_runs(theme: str):
    """Which passes a --theme value asks for. "both" adds the click, which is only comparable to dark."""
    if theme == "light":
        return THEME_RUNS[:1]
    if theme == "dark":
        return THEME_RUNS[1:2]
    return THEME_RUNS

NO_BROWSER_MESSAGE = (
    "measure_web_layout: no Chromium browser found.\n"
    "  Looked at --chrome, $KDT_CHROME, $CHROME_PATH, chrome/chromium/google-chrome on PATH, and the\n"
    "  usual install locations (Program Files/Google/Chrome, Program Files (x86)/Microsoft/Edge,\n"
    "  /usr/bin/google-chrome, /Applications/Google Chrome.app).\n"
    "  Pass one explicitly:  python tools/measure_web_layout.py --chrome \"<path to chrome>\""
)

#: A 2x2 PNG: the gallery only ever asks for grid thumbnails, and decoding a real image is not what is
#: being measured here. Base64 keeps the tool free of an image dependency.
THUMB_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAYAAABytg0kAAAAFElEQVR4nGP8z8DAwMDAwMDAwMAAAAwBAQDJ/pLvAAAAAElFTkSuQmCC"
)


def _base(path: str) -> str:
    return str(path).replace("\\", "/").rsplit("/", 1)[-1]


def _fake_image_path(name: str) -> str:
    return FAKE_ROOT + "/" + name


def _listing(path: str, root_images: int = len(FAKE_IMAGES)) -> dict:
    """The canned GET /api/fs/list body for one directory: images plus three subdirectories.

    The root level carries both, so the gallery renders tiles (clickable, with captions) *and* the
    sidebar has directory rows - the two layouts this probe has to walk. root_images=0 makes it a
    folder-only level, which is what a real dataset root looks like (and the case the readiness
    strip has to stay quiet about captions in).
    """
    name = _base(path)
    if name and name in FAKE_DIRS:
        # A subdirectory: more images, no further nesting. Nothing browses there during a run.
        images = [
            {
                "name": "%s-%02d.png" % (name, index),
                "path": path + "/%s-%02d.png" % (name, index),
                "size": 1024 * (index + 1),
                "mtime": 1700000000 + index,
                "has_caption": index % 2 == 0,
            }
            for index in range(1, 13)
        ]
        return {
            "path": path,
            "parent": FAKE_ROOT,
            "dirs": [],
            "images": images,
            "counts": {
                "images": len(images),
                "dirs": 0,
                "captions": sum(1 for item in images if item["has_caption"]),
                "missing_captions": sum(1 for item in images if not item["has_caption"]),
            },
        }
    names = FAKE_IMAGES[: max(0, min(int(root_images), len(FAKE_IMAGES)))]
    images = [
        {
            "name": filename,
            "path": _fake_image_path(filename),
            "size": 1180 * 1024,
            "mtime": 1700000000 + index,
            "has_caption": index < FAKE_CAPTIONS,
        }
        for index, filename in enumerate(names)
    ]
    captions = sum(1 for index in range(len(names)) if index < FAKE_CAPTIONS)
    dirs = [
        {
            "name": dirname,
            "path": FAKE_ROOT + "/" + dirname,
            "image_count": count,
            "preview_images": [_fake_image_path(item) for item in FAKE_IMAGES[: min(3, count)]],
        }
        for dirname, count in zip(FAKE_DIRS, (12, 7, 0))
    ]
    return {
        "path": FAKE_ROOT,
        "parent": None,
        "dirs": dirs,
        "images": images,
        "counts": {
            "images": len(images),
            "dirs": len(dirs),
            "captions": captions,
            "missing_captions": len(images) - captions,
        },
    }


def _caption_entry(path: str, ok: bool = True) -> dict:
    name = _base(path)
    index = FAKE_IMAGES.index(name) if name in FAKE_IMAGES else 0
    has_caption = index < FAKE_CAPTIONS
    return {
        "image": str(path),
        "ok": ok,
        "caption_path": str(path).rsplit(".", 1)[0] + ".txt",
        "exists": has_caption,
        "text": "1girl, solo" if has_caption else "",
        "tags": ["1girl", "solo"] if has_caption else [],
        "multiline_warning": False,
    }


def _dataset_toml() -> dict:
    """A canned POST /api/dataset/toml answer, so the export panel (and the readiness strip's third
    segment) can be measured with real numbers instead of an empty state."""
    subsets = [
        {
            "image_dir": FAKE_ROOT + "/" + dirname,
            "image_count": count,
            "caption_count": count,
            "num_repeats": 1,
            "warnings": [],
        }
        for dirname, count in zip(FAKE_DIRS, (12, 7, 0))
    ]
    lines = ["[general]", 'caption_extension = ".txt"', "", "[[datasets]]", "batch_size = 1", "num_repeats = 1"]
    for subset in subsets:
        lines += ["", "  [[datasets.subsets]]", '  image_dir = "%s"' % subset["image_dir"]]
    return {
        "toml": "\n".join(lines) + "\n",
        "subset_count": len(subsets),
        "image_count": sum(item["image_count"] for item in subsets),
        "subsets": subsets,
        "warnings": ["[too_few_images] %s has 0 images" % (FAKE_ROOT + "/gamma")],
        "skipped": [],
    }


def _dataset_toml_status(toml_exists: bool) -> dict:
    """The canned GET /api/dataset/toml/status answer (§4.16) for the fake root.

    Both states are measurable: without a file the readiness strip's third segment must say the
    dataset root has no dataset.toml even after the export panel reports a clean plan (the export
    endpoint writes nothing), and --toml is how the "the file is there" state is measured.
    """
    return {
        "root": FAKE_ROOT,
        "path": FAKE_ROOT + "/dataset.toml",
        "exists": bool(toml_exists),
        "mtime": FAKE_TOML_MTIME if toml_exists else None,
        "size": FAKE_TOML_SIZE if toml_exists else None,
    }


def canned_api(method: str, path: str, query: dict, body: bytes, scenario: str = "stale",
               root_images: int = len(FAKE_IMAGES), toml_exists: bool = False):
    """Answer one /api/* request from the constants above.

    Pure by construction: no filesystem, no clock, no randomness, no network - the same request always
    answers the same bytes (that is what makes it assertable without a browser). "scenario" only
    switches the one endpoint the readiness strip has a failure state for.

    Returns (status, content_type, payload_bytes), or None for a request this probe does not know - the
    handler then answers 404, which shows up in the request log rather than looking like a success.
    """
    def payload(data, status: int = 200):
        return status, "application/json; charset=utf-8", json.dumps(data).encode("utf-8")

    def error(status: int, code: str, message: str):
        return payload({"error": {"code": code, "message": message}}, status)

    if method == "GET" and path == "/api/health":
        return payload({
            "ok": True,
            "version": "layout-probe",
            "path_config": {"allowed": True, "host": "127.0.0.1", "reason": None},
        })
    if method == "GET" and path == "/api/roots":
        return payload({"roots": [{"path": FAKE_ROOT, "name": "probe-dataset", "exists": True}]})
    if method == "GET" and path == "/api/fs/list":
        target = (query.get("path") or [FAKE_ROOT])[0]
        # Only the fake tree is answered; anything else is refused, so a wrong path can never look
        # like a real listing.
        if target != FAKE_ROOT and not target.startswith(FAKE_ROOT + "/"):
            return error(403, "path_outside_roots", "Not under the probe root: %s" % target)
        return payload(_listing(target, root_images))
    if method == "GET" and path == "/api/tags/frequency":
        return payload({
            "total_images": len(FAKE_IMAGES),
            "tags": [
                {"tag": "1girl", "count": FAKE_CAPTIONS},
                {"tag": "solo", "count": FAKE_CAPTIONS},
                {"tag": "long_hair", "count": 1},
            ],
        })
    if method == "GET" and path == "/api/cache/status":
        if scenario == "fail":
            return error(500, "probe_failure", "The probe was asked for the failed cache status")
        names = FAKE_STALE if scenario == "stale" else ()
        return payload({"stale": [
            {
                "image": _fake_image_path(name),
                "caption": _fake_image_path(name).rsplit(".", 1)[0] + ".txt",
                "cache": FAKE_ROOT + "/cache_text_encoder/" + name + "_anima_te.safetensors",
            }
            for name in names
        ]})
    if method == "GET" and path == "/api/captions":
        target = (query.get("path") or [""])[0]
        entry = _caption_entry(target)
        entry.pop("ok", None)
        return payload(entry)
    if method == "POST" and path == "/api/captions/read":
        try:
            images = json.loads(body.decode("utf-8") or "{}").get("images", [])
        except (ValueError, UnicodeDecodeError):
            return error(400, "bad_json", "The probe could not read the request body")
        return payload({"results": [_caption_entry(item) for item in images]})
    if method == "GET" and path == "/api/images/meta":
        target = (query.get("path") or [""])[0]
        return payload({
            "path": str(target),
            "width": 1024,
            "height": 768,
            "bytes": 1180 * 1024,
            "mtime": 1700000000,
            "mode": "RGB",
            "has_alpha": False,
        })
    if method == "GET" and path == "/api/autotag/models":
        # No model on purpose: the tagger panel must render its own "no model" state rather than this
        # probe inventing one, and a machine without WD14 still measures the layout.
        return payload({"models": []})
    if method == "GET" and path == "/api/autotag/model-roots":
        return payload({"roots": [], "models": []})
    if method == "GET" and path == "/api/images/thumb":
        return 200, "image/png", THUMB_PNG
    if method == "GET" and path == "/api/dataset/toml/status":
        target = (query.get("root") or [FAKE_ROOT])[0]
        if target != FAKE_ROOT and not target.startswith(FAKE_ROOT + "/"):
            return error(403, "outside_roots", "Not under the probe root: %s" % target)
        return payload(_dataset_toml_status(toml_exists))
    if method == "POST" and path == "/api/dataset/toml":
        return payload(_dataset_toml())
    return None


#: The wrapper page. It lives here rather than under web/ on purpose: web/ is the shipped frontend and
#: must not contain probe code. The app is loaded in a same-origin iframe so this script can read its
#: geometry - the app itself is untouched.
PROBE_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>layout probe</title>
<style>html,body{margin:0}iframe{display:block;width:__SIZE__;border:0}
#result{display:none}</style></head>
<body>
<!-- The app is mounted by the script below rather than by the parser: the dark pass has to write
     localStorage BEFORE index.html exists, otherwise it would measure the toggle path and not the
     boot path - and "applied before the first paint" is the whole claim being measured. -->
<div id="slot"></div>
<pre id="result">pending</pre>
<script>
const SELECTORS = __SELECTORS__;
const CONTRAST_SELECTORS = __CONTRAST__;
const THEME_TOKENS = __TOKENS__;
const CONTRAST_SEEDS = __SEEDS__;
const CONTRAST_MIN = __CONTRAST_MIN__;
const SEED_THEME = __SEED__;
const SEED_LANG = __LANG__;
const TOGGLE_THEME = __TOGGLE__;
const SHOT_MODE = __SHOT__;
const STEPS = [];
const THEMES = [];
const SEEDED = [];
const ERRORS = [];
const result = document.getElementById("result");

// The stored choice, written before the iframe is inserted. index.html's classic <head> script reads
// it while the document is still parsing, so the dark pass measures what a reloaded dark page is.
try {
  window.localStorage.setItem("kdt.theme", SEED_THEME);
} catch (err) {
  ERRORS.push("the probe could not seed localStorage: " + String(err));
}
// The frame's size comes from the stylesheet (__SIZE__), which is the probe's own viewport - so it
// is fixed before index.html starts loading and never restyled afterwards. Sizing it from the
// window (100vw/100vh) or restyling it after the app exists would let the app boot against a
// layout that is not final: app.js clamps the two side panels against the layout it sees, in one
// direction only, and a run whose frame was still small recorded 200/280 - exactly
// RESIZER_LIMITS.left.min and .right.min - while the JSON measured 260/380.
const frame = document.createElement("iframe");
frame.id = "app";
// The locale is the app's own resolution order, not a second one: ?lang= is what initLocale() reads
// first, and it is appended here so a scratch patch is not needed to measure another pack.
frame.src = "./index.html" + (SEED_LANG ? "?lang=" + encodeURIComponent(SEED_LANG) : "");
document.getElementById("slot").append(frame);
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const round = (value) => Math.round(value * 10) / 10;

function boxOf(win, el) {
  if (!el) return null;
  const rect = el.getBoundingClientRect();
  const style = win.getComputedStyle(el);
  return {
    top: round(rect.top), bottom: round(rect.bottom), left: round(rect.left), right: round(rect.right),
    width: round(rect.width), height: round(rect.height),
    clientHeight: el.clientHeight, scrollHeight: el.scrollHeight,
    hidden: el.hidden === true,
    display: style.display,
    visible: style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0,
  };
}

function attrOf(el, name) {
  return el && el.getAttribute ? el.getAttribute(name) : null;
}

function measure(doc, win, name) {
  const boxes = {};
  for (const selector of SELECTORS) boxes[selector] = boxOf(win, doc.querySelector(selector));
  const activeTab = doc.querySelector(".tab.is-active");
  const activePanel = doc.querySelector(".tab-panel:not([hidden])");
  const strip = doc.getElementById("readiness");
  const paletteEl = doc.getElementById("palette");
  const paletteInput = doc.getElementById("palette-input");
  return {
    name: name,
    tab: activeTab ? activeTab.dataset.tab : null,
    panel: activePanel ? activePanel.dataset.panel : null,
    viewport: { width: win.innerWidth, height: win.innerHeight },
    boxes: boxes,
    activePanel: boxOf(win, activePanel),
    readiness: strip ? {
      hidden: strip.hidden === true,
      box: boxOf(win, strip),
      segments: Array.from(strip.querySelectorAll(".readiness-item")).map((el) => ({
        segment: el.dataset.segment,
        text: el.textContent,
        hidden: el.hidden === true,
        disabled: el.disabled === true,
        state: Array.from(el.classList).filter((name) => name.indexOf("is-") === 0).join(" "),
      })),
    } : null,
    // The command palette: hidden until the gesture opens it, so every field is read defensively.
    palette: paletteEl ? {
      hidden: paletteEl.hidden === true,
      box: boxOf(win, paletteEl),
      // The panel IS #palette now - a dropdown anchored under the command bar - so its role and
      // its (absent) aria-modal are read off it directly, and its box is a comparison against the
      // box of #btn-palette above: same right edge, top = the bar's bottom + 6.
      role: attrOf(paletteEl, "role"),
      modal: attrOf(paletteEl, "aria-modal"),
      input: boxOf(win, paletteInput),
      expanded: attrOf(paletteInput, "aria-expanded"),
      activeDescendant: attrOf(paletteInput, "aria-activedescendant"),
      status: (doc.getElementById("palette-status") || {}).textContent || "",
      rows: Array.from(doc.querySelectorAll("#palette-list [role=option]")).map((el) => ({
        id: el.id,
        label: (el.querySelector(".palette-label") || {}).textContent || "",
        why: (el.querySelector(".palette-why") || {}).textContent || "",
        disabled: el.getAttribute("aria-disabled"),
        selected: el.getAttribute("aria-selected"),
      })),
      groups: Array.from(doc.querySelectorAll("#palette-list [role=group]")).map(
        (el) => el.getAttribute("aria-label")),
      focusedId: (doc.activeElement || {}).id || (doc.activeElement || {}).tagName || "",
    } : null,
    countsLine: (doc.getElementById("dir-counts") || {}).textContent || "",
    cacheLine: (doc.getElementById("cache-status-line") || {}).textContent || "",
    frequencyScope: (doc.getElementById("frequency-scope") || {}).textContent || "",
    toasts: (doc.getElementById("toasts") || {}).textContent || "",
    tiles: doc.querySelectorAll("#gallery .tile").length,
    dirCards: doc.querySelectorAll("#gallery .dir-card").length,
  };
}

// --- the contrast table -----------------------------------------------------
// Everything below reads the page; nothing here writes a colour. The arithmetic is the WCAG 2.1
// relative-luminance formula, spelled out so the JSON can be checked by hand.

function parseColour(value) {
  const found = /^rgba?\\(([^)]+)\\)$/.exec(String(value || "").trim());
  if (!found) return null;
  const parts = found[1].split(/[,\\s\\/]+/).filter((part) => part.length > 0).map(Number);
  if (parts.length < 3 || parts.some((part) => Number.isNaN(part))) return null;
  return { r: parts[0], g: parts[1], b: parts[2], a: parts.length > 3 ? parts[3] : 1 };
}

function formatColour(colour) {
  const body = Math.round(colour.r) + ", " + Math.round(colour.g) + ", " + Math.round(colour.b);
  return colour.a >= 0.999 ? "rgb(" + body + ")" : "rgb(" + body + " / " + Math.round(colour.a * 100) / 100 + ")";
}

/** The top layer painted over the bottom one - what a translucent background really looks like. */
function composite(top, bottom) {
  const alpha = top.a + bottom.a * (1 - top.a);
  const mix = (upper, lower) => (upper * top.a + lower * bottom.a * (1 - top.a)) / (alpha || 1);
  return { r: mix(top.r, bottom.r), g: mix(top.g, bottom.g), b: mix(top.b, bottom.b), a: alpha };
}

function relativeLuminance(colour) {
  const channel = (value) => {
    const c = value / 255;
    return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  };
  return 0.2126 * channel(colour.r) + 0.7152 * channel(colour.g) + 0.0722 * channel(colour.b);
}

function contrastRatio(foreground, background) {
  const first = relativeLuminance(foreground);
  const second = relativeLuminance(background);
  return (Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05);
}

/**
 * What a colour is seen against: the nearest ancestor (self included) with an opaque
 * background-color, with every translucent layer between it and the element composited on top.
 * A transparent background (the default on most elements) is skipped, which is why walking up is the
 * only honest way to answer this.
 */
function effectiveBackground(win, el) {
  const layers = [];
  let base = null;
  let node = el;
  while (node) {
    const parsed = parseColour(win.getComputedStyle(node).backgroundColor);
    if (parsed) {
      if (parsed.a >= 0.999) { base = parsed; break; }
      if (parsed.a > 0) layers.push(parsed);
    }
    node = node.parentElement;
  }
  if (!base) base = { r: 255, g: 255, b: 255, a: 1 };
  let out = base;
  for (let index = layers.length - 1; index >= 0; index -= 1) out = composite(layers[index], out);
  return out;
}

function seedSwatches(doc) {
  for (const seed of CONTRAST_SEEDS) {
    if (doc.querySelector(seed[0])) continue; // the real element is on screen: measure that one
    const host = doc.querySelector(seed[1]);
    if (!host) {
      ERRORS.push("no " + seed[1] + " to put a " + seed[0] + " contrast swatch in");
      continue;
    }
    const node = doc.createElement(seed[2]);
    node.className = seed[3];
    node.textContent = "Aa";
    host.append(node);
    SEEDED.push(seed[0]);
  }
}

function contrastTable(doc, win) {
  return CONTRAST_SELECTORS.map((selector) => {
    const el = doc.querySelector(selector);
    const fg = el ? parseColour(win.getComputedStyle(el).color) : null;
    const bg = el ? effectiveBackground(win, el) : null;
    const value = fg && bg ? Math.round(contrastRatio(fg, bg) * 100) / 100 : null;
    return {
      selector: selector,
      found: Boolean(el),
      seeded: SEEDED.indexOf(selector) >= 0,
      fg: fg ? formatColour(fg) : null,
      bg: bg ? formatColour(bg) : null,
      ratio: value,
      min: CONTRAST_MIN,
      ok: value !== null && value >= CONTRAST_MIN,
    };
  });
}

function themePass(doc, win, name) {
  const style = win.getComputedStyle(doc.documentElement);
  const tokens = {};
  for (const token of THEME_TOKENS) tokens[token] = String(style.getPropertyValue(token)).trim();
  const box = doc.getElementById("theme-dark");
  return {
    name: name,
    seed: SEED_THEME,
    attribute: doc.documentElement.getAttribute("data-theme"),
    colorScheme: style.colorScheme,
    // The other half of "one module writes both": the switch itself, read off the page. A toggle
    // pass whose attribute moved while the box did not would otherwise look like a success.
    switch: box ? { checked: box.checked === true, role: box.getAttribute("role") } : null,
    tokens: tokens,
    contrast: contrastTable(doc, win),
  };
}

async function until(predicate, tries) {
  for (let index = 0; index < (tries || 60); index += 1) {
    if (predicate()) return true;
    await wait(50);
  }
  return false;
}

async function main() {
  // The iframe's own document is only readable once index.html has replaced about:blank, so both
  // handles are taken inside the poll rather than from the (initially blank) frame - and the wait is
  // bounded, so a page that never boots is a recorded failure rather than a hang.
  let win = null;
  let doc = null;
  // The app boots asynchronously (health, roots, then the first listing); tiles or folder cards in
  // the gallery mean it is up (a root with no images renders cards, not tiles).
  await until(() => {
    doc = frame.contentDocument;
    win = frame.contentWindow;
    return Boolean(doc && doc.getElementById("gallery")
      && doc.querySelectorAll("#gallery .tile, #gallery .dir-card").length > 0);
  }, 200);
  win.addEventListener("error", (event) => ERRORS.push("error: " + (event.message || "?")));
  win.addEventListener("unhandledrejection", (event) => ERRORS.push("rejection: " + String(event.reason)));
  // One image active, so the caption dock is in its real state (not the empty one) - that is what the
  // previous rounds measured, and a comparison needs the same state.
  const tile = doc.querySelector("#gallery .tile");
  if (tile) {
    tile.click();
    const form = doc.getElementById("caption-form");
    await until(() => form && form.hidden === false, 100);
  }
  await wait(200);
  STEPS.push(measure(doc, win, "boot"));
  // --shot wants the picture the numbers describe, not the end of the walk: the boot state is where
  // "a stored theme is applied before the first paint" is visible.
  if (SHOT_MODE) {
    result.textContent = btoa(unescape(encodeURIComponent(JSON.stringify({ steps: STEPS, errors: ERRORS, themes: THEMES }))));
    document.title = "probe-done";
    return;
  }
  for (const tab of Array.from(doc.querySelectorAll(".tab"))) {
    const name = tab.dataset.tab;
    tab.click();
    await wait(120);
    STEPS.push(measure(doc, win, "tab:" + name));
  }
  // The export panel's generate button is the only .btn-primary inside its own panel; clicking it
  // gives the readiness strip's third segment a real report to report. The export tab is selected
  // first so the measurement below is taken with that panel - and the strip - on screen.
  const exportTab = doc.querySelector('.tab[data-tab="export"]');
  if (exportTab) exportTab.click();
  await wait(120);
  const generate = doc.querySelector('[data-panel="export"] .btn-primary');
  if (generate) {
    generate.click();
    const summary = doc.querySelector('[data-panel="export"] .mono');
    await until(() => summary && summary.textContent.length > 0, 100);
    await wait(120);
    STEPS.push(measure(doc, win, "export:generated"));
  } else {
    ERRORS.push("no generate button found in the export panel");
  }
  // The command palette (T3). Driven the way a user drives it: real keydown events on the iframe's
  // own document (the gesture lives there), then a typed query, then Enter - whose effect is read
  // off the page (the active tab) rather than off the palette, because "it ran the action" is a
  // claim about the app, not about the dialog.
  function press(key, extra) {
    const init = Object.assign({ key: key, bubbles: true, cancelable: true }, extra || {});
    doc.dispatchEvent(new win.KeyboardEvent("keydown", init));
  }
  // Ctrl+K is the opening gesture; focus starts on the top bar's own button so the return trip is
  // measurable as "focus is back on the button that was focused before".
  const paletteButton = doc.getElementById("btn-palette");
  if (paletteButton) paletteButton.focus();
  press("k", { ctrlKey: true });
  await wait(150);
  STEPS.push(measure(doc, win, "palette:opened"));
  press("ArrowDown");
  await wait(80);
  press("ArrowDown");
  await wait(80);
  STEPS.push(measure(doc, win, "palette:arrows"));
  press("Escape");
  await wait(150);
  STEPS.push(measure(doc, win, "palette:escape"));
  // Reopen, search in English while the profile is in Chinese (the matcher reads every pack), then
  // run the best match - the tab it opens is the proof that the row called the real action.
  press("k", { ctrlKey: true });
  await wait(150);
  const paletteInput = doc.getElementById("palette-input");
  if (paletteInput) {
    paletteInput.value = "zzzz";
    paletteInput.dispatchEvent(new win.Event("input", { bubbles: true }));
    await wait(80);
    STEPS.push(measure(doc, win, "palette:no-match"));
    paletteInput.value = "cache";
    paletteInput.dispatchEvent(new win.Event("input", { bubbles: true }));
    await wait(80);
    STEPS.push(measure(doc, win, "palette:query"));
    press("Enter");
    await wait(200);
    STEPS.push(measure(doc, win, "palette:ran"));
  } else {
    ERRORS.push("no #palette-input: the palette is not in the page");
  }
  // The theme pass. The seeded key was already read by index.html's boot script, so this measures
  // the state a reload produces; the toggle pass then clicks the real switch and measures again, and
  // the comparison with the dark pass is made on the Python side (theme_toggle.tokens_match_dark).
  seedSwatches(doc);
  await wait(120);
  if (TOGGLE_THEME) {
    THEMES.push(themePass(doc, win, "before"));
    const themeBox = doc.getElementById("theme-dark");
    if (themeBox) {
      themeBox.click();
      await wait(150);
    } else {
      ERRORS.push("no #theme-dark in the page: the theme switch is not wired");
    }
    THEMES.push(themePass(doc, win, "after"));
  } else {
    THEMES.push(themePass(doc, win, "theme"));
  }
  result.textContent = btoa(unescape(encodeURIComponent(JSON.stringify({ steps: STEPS, errors: ERRORS, themes: THEMES }))));
  document.title = "probe-done";
}

main().catch((err) => {
  result.textContent = btoa(unescape(encodeURIComponent(JSON.stringify({
    steps: STEPS, errors: ERRORS.concat(["fatal: " + String(err)]), themes: THEMES,
  }))));
  document.title = "probe-done";
});
</script>
</body></html>
"""


class ProbeServer(ThreadingHTTPServer):
    """A localhost server for the probe: the real web/ tree, one wrapper page, and the canned API."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, web_root: Path, scenario: str, viewport, root_images=len(FAKE_IMAGES),
                 toml_exists: bool = False, lang: str = ""):
        super().__init__(address, ProbeHandler)
        self.web_root = web_root
        self.scenario = scenario
        #: The locale the app is asked for (?lang=), or "" for the browser's own.
        self.lang = lang
        #: The iframe's size in CSS pixels - the viewport every measurement is relative to.
        self.viewport = viewport
        #: How many images the canned root lists; 0 makes it the folder-only level a dataset root is.
        self.root_images = root_images
        #: Whether the canned dataset root has a dataset.toml (§4.16) - the strip's third segment.
        self.toml_exists = toml_exists
        #: Every /api/* request the page made, in order - the probe's own proof of what it answered.
        self.requests: list = []


class ProbeHandler(BaseHTTPRequestHandler):
    server_version = "kdt-layout-probe"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        """Silence the default stderr access log; the request log travels in the JSON output."""

    def _send(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _dispatch(self, method: str) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""

        if path == "/__probe__.html":
            # Every knob the page has is in the query string, because one server serves all the theme
            # passes: ?seed=dark measures the boot path, ?toggle=1 clicks the switch, ?shot=1 stops
            # after the boot state so the screenshot is the state the numbers describe.
            seed = (query.get("seed") or ["light"])[0]
            if seed not in ("light", "dark"):
                seed = "light"
            toggle = (query.get("toggle") or ["0"])[0] == "1"
            shot = (query.get("shot") or ["0"])[0] == "1"
            #: Empty means "whatever the browser reports": the probe does not carry a locale of its own.
            lang = self.server.lang or ""
            page = PROBE_PAGE.replace("__SELECTORS__", json.dumps(list(SELECTORS)))
            page = page.replace("__CONTRAST__", json.dumps(list(CONTRAST_SELECTORS)))
            page = page.replace("__TOKENS__", json.dumps(list(THEME_TOKENS)))
            page = page.replace("__SEEDS__", json.dumps([list(seed_spec) for seed_spec in CONTRAST_SEEDS]))
            page = page.replace("__CONTRAST_MIN__", repr(float(CONTRAST_MIN)))
            page = page.replace("__SEED__", json.dumps(seed))
            page = page.replace("__LANG__", json.dumps(lang))
            page = page.replace("__TOGGLE__", "true" if toggle else "false")
            page = page.replace("__SHOT__", "true" if shot else "false")
            # The viewport is the iframe's own size, not the browser window's: --window-size is not
            # honoured by every headless build, and a measurement that silently ran at another size
            # would be worse than no measurement. The wrapper is only a frame around it - and a shot
            # run's window IS this size (run_browser_shot passes the same numbers to --window-size),
            # so the frame fills the window either way. Sizing it from the window instead (100vw /
            # 100vh) made the app boot against a layout that was not final yet, which is how a shot
            # once recorded 200/280 against the JSON's 260/380.
            page = page.replace(
                "__SIZE__",
                "%dpx;height:%dpx" % (self.server.viewport[0], self.server.viewport[1]),
            )
            self._send(200, "text/html; charset=utf-8", page.encode("utf-8"))
            return

        if path.startswith("/api/"):
            self.server.requests.append({"method": method, "path": path, "query": parsed.query})
            answer = canned_api(method, path, query, body, self.server.scenario,
                                self.server.root_images, self.server.toml_exists)
            if answer is None:
                self._send(404, "application/json; charset=utf-8",
                           json.dumps({"error": {"code": "probe_unknown_route", "message": path}}).encode("utf-8"))
                return
            self._send(*answer)
            return

        target = (self.server.web_root / path.lstrip("/")).resolve()
        root = self.server.web_root.resolve()
        if root not in target.parents and target != root:
            self._send(403, "text/plain; charset=utf-8", b"outside the probe root")
            return
        if target.is_dir():
            target = target / "index.html"
        if not target.is_file():
            self._send(404, "text/plain; charset=utf-8", b"not found")
            return
        content_type = {
            ".html": "text/html; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".svg": "image/svg+xml",
            ".png": "image/png",
            ".ico": "image/x-icon",
        }.get(target.suffix.lower(), "application/octet-stream")
        self._send(200, content_type, target.read_bytes())

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")


def start_server(web_root: Path, scenario: str, viewport=(1280, 900), root_images=len(FAKE_IMAGES),
                 toml_exists: bool = False, lang: str = "") -> ProbeServer:
    """Bind a probe server on an ephemeral loopback port. Returns it already serving."""
    server = ProbeServer(("127.0.0.1", 0), web_root, scenario, viewport, root_images, toml_exists, lang)
    thread = threading.Thread(target=server.serve_forever, name="probe-http", daemon=True)
    thread.start()
    return server


def find_chrome(explicit=None):
    """The first usable Chromium browser, or None. Explicit path > environment > PATH > install dirs."""
    candidates = []
    if explicit:
        candidates.append(explicit)
    for name in ("KDT_CHROME", "CHROME_PATH", "CHROME_BIN"):
        value = os.environ.get(name)
        if value:
            candidates.append(value)
    for name in ("chrome", "chrome.exe", "google-chrome", "google-chrome-stable",
                 "chromium", "chromium-browser", "msedge", "msedge.exe"):
        found = shutil.which(name)
        if found:
            candidates.append(found)
    for base in (os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)"),
                 os.environ.get("LOCALAPPDATA")):
        if not base:
            continue
        candidates.append(os.path.join(base, "Google", "Chrome", "Application", "chrome.exe"))
        candidates.append(os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"))
    candidates.extend([
        "/usr/bin/google-chrome",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/snap/bin/chromium",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
    ])
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate)
        if path.is_file():
            return str(path)
    return None


def run_browser(chrome: str, url: str, scratch: Path, width: int, height: int, timeout: float) -> str:
    """Load the probe page once and return Chrome's DOM dump; RuntimeError carries a readable message."""
    command = [
        chrome,
        "--headless",
        "--disable-gpu",
        "--disable-extensions",
        "--no-first-run",
        "--no-default-browser-check",
        "--user-data-dir=" + str(scratch / "chrome-profile"),
        # Room around the iframe; the measurements come from the iframe's own viewport.
        "--window-size=%dx%d" % (width + 40, height + 120),
        "--virtual-time-budget=%d" % int(timeout * 1000),
        "--dump-dom",
        url,
    ]
    try:
        done = subprocess.run(
            command,
            capture_output=True, encoding="utf-8", errors="replace",
            timeout=timeout + 30,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("the browser did not finish within %.0f s" % (timeout + 30))
    except OSError as exc:
        raise RuntimeError("could not run the browser (%s): %s" % (chrome, exc))
    if done.returncode != 0 and not done.stdout:
        detail = (done.stderr or "").strip().splitlines()
        raise RuntimeError(
            "the browser exited with %d and printed no DOM%s"
            % (done.returncode, (": " + detail[-1]) if detail else "")
        )
    if not done.stdout:
        raise RuntimeError("the browser printed no DOM")
    return done.stdout


def run_browser_shot(chrome: str, url: str, scratch: Path, width: int, height: int, timeout: float,
                     target: Path) -> str:
    """Load the probe page once more and capture it as a PNG; returns the path that was written.

    A separate invocation on purpose: --screenshot and --dump-dom are two different browser actions
    and asking for both gets one of them. The window is the probe's own viewport and the shot page
    sizes the frame with the same numbers in its stylesheet - before the app inside it loads - so
    the PNG is the page at the size the JSON was measured at, and the app never sees another size.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    command = [
        chrome,
        "--headless",
        "--disable-gpu",
        "--disable-extensions",
        "--no-first-run",
        "--no-default-browser-check",
        "--user-data-dir=" + str(scratch / "chrome-profile"),
        "--window-size=%dx%d" % (width, height),
        "--virtual-time-budget=%d" % int(timeout * 1000),
        "--screenshot=" + str(target),
        url,
    ]
    try:
        done = subprocess.run(
            command,
            capture_output=True, encoding="utf-8", errors="replace",
            timeout=timeout + 30,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("the screenshot run did not finish within %.0f s" % (timeout + 30))
    except OSError as exc:
        raise RuntimeError("could not run the browser (%s): %s" % (chrome, exc))
    if not target.is_file() or target.stat().st_size == 0:
        detail = (done.stderr or "").strip().splitlines()
        raise RuntimeError(
            "the browser wrote no screenshot at %s (exit %d)%s"
            % (target, done.returncode, (": " + detail[-1]) if detail else "")
        )
    return str(target)


def theme_block(name: str, seed: str, toggle: bool, measured: dict) -> dict:
    """One theme pass as the JSON reports it: the state, every token, and the contrast table.

    For a toggle run the block is the state AFTER the real switch was clicked, and the state before it
    is kept next to it - "the click moved it" is only a claim if both ends are on the page.
    """
    passes = measured.get("themes") or []
    chosen = passes[-1] if passes else {}
    block = {
        "seed": seed,
        "clicked": bool(toggle),
        "attribute": chosen.get("attribute"),
        "colorScheme": chosen.get("colorScheme"),
        "switch": chosen.get("switch"),
        "tokens": chosen.get("tokens") or {},
        "contrast": chosen.get("contrast") or [],
        "steps": measured.get("steps", []),
        "page_errors": measured.get("errors", []),
    }
    if toggle and len(passes) > 1:
        block["before"] = {
            "attribute": passes[0].get("attribute"),
            "colorScheme": passes[0].get("colorScheme"),
            "switch": passes[0].get("switch"),
            "tokens": passes[0].get("tokens") or {},
        }
    return block


def toggle_summary(themes: dict):
    """The toggle claim, stated where it can be checked: did the click reach the dark pass's tokens?"""
    toggled = themes.get("toggled")
    if not toggled:
        return None
    dark = themes.get("dark")
    tokens = toggled.get("tokens") or {}
    return {
        "clicked": bool(toggled.get("clicked")),
        "before": (toggled.get("before") or {}).get("attribute"),
        "after": toggled.get("attribute"),
        "switch_before": (toggled.get("before") or {}).get("switch"),
        "switch_after": toggled.get("switch"),
        # Two empty token sets are equal, so an empty read must not count as a match: the first run of
        # this pass reported tokens_match_dark=true from a page that had died on a ReferenceError.
        "tokens_match_dark": bool(dark) and bool(tokens) and tokens == (dark.get("tokens") or {}),
    }


RESULT_RE = re.compile(r'<pre id="result">(.*?)</pre>', re.S)


def parse_probe_result(dom: str) -> dict:
    """Pull the base64 payload out of the wrapper page's DOM dump."""
    found = RESULT_RE.search(dom)
    if not found:
        raise RuntimeError("the probe page did not render its result element")
    raw = html.unescape(found.group(1)).strip()
    if not raw or raw == "pending":
        raise RuntimeError("the probe page never finished its measurement (still 'pending')")
    try:
        decoded = base64.b64decode(raw).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise RuntimeError("the probe page's result was not readable base64 JSON: %s" % exc)
    try:
        return json.loads(decoded)
    except ValueError as exc:
        raise RuntimeError("the probe page's result was not JSON: %s" % exc)


def parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="measure_web_layout.py",
        description="Measure the real frontend's layout in headless Chrome, with a canned /api/*.",
    )
    parser.add_argument("--web", default=str(DEFAULT_WEB_ROOT),
                        help="the frontend directory to serve (default: the repository's web/)")
    parser.add_argument("--chrome", default=None, help="path to a Chromium browser")
    parser.add_argument("--width", type=int, default=1280, help="viewport width (default 1280)")
    parser.add_argument("--height", type=int, default=900, help="viewport height (default 900)")
    parser.add_argument("--scenario", choices=SCENARIOS, default="stale",
                        help="what canned /api/cache/status answers (default: stale)")
    parser.add_argument("--root-images", type=int, default=len(FAKE_IMAGES),
                        help="images the canned dataset root lists; 0 = a folder-only root, like a "
                             "real dataset root (default: %d)" % len(FAKE_IMAGES))
    parser.add_argument("--lang", default=None,
                        help="the interface language to measure (zh-CN / en / ja); it reaches the "
                             "app through its own ?lang= resolution. Default: the browser's locale")
    parser.add_argument("--toml", action="store_true",
                        help="let the canned dataset root pretend it has a dataset.toml (§4.16); "
                             "without it the strip's third segment must say the file is not there")
    parser.add_argument("--scratch", default=None,
                        help="where the browser profile goes (default: a fresh temp directory)")
    parser.add_argument("--keep-scratch", action="store_true",
                        help="keep the scratch directory instead of removing it at the end")
    parser.add_argument("--timeout", type=float, default=20.0,
                        help="virtual time budget in seconds (default 20)")
    parser.add_argument("--theme", choices=("light", "dark", "both"), default="both",
                        help="which theme passes to run (default: both, plus the toggle pass)")
    parser.add_argument("--shot", default=None,
                        help="also write probe-light.png and probe-dark.png into this directory")
    parser.add_argument("--out", default=None, help="also write the JSON here")
    return parser.parse_args(argv)


def take_shots(chrome: str, port: int, scratch: Path, args) -> dict:
    """The two PNGs --shot asks for: one per theme, at the probe's own viewport, in the named directory.

    The shot pages are the same wrapper with shot=1, which stops after the boot state - so the picture
    is what a reloaded page looks like, including the theme the boot script applied before painting.
    """
    out = Path(args.shot).expanduser().resolve()
    shots = {}
    for name in ("light", "dark"):
        url = "http://127.0.0.1:%d/__probe__.html?seed=%s&shot=1" % (port, name)
        shots[name] = run_browser_shot(
            chrome, url, scratch, args.width, args.height, args.timeout,
            out / ("probe-%s.png" % name),
        )
    return shots


def main(argv=None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    web_root = Path(args.web).resolve()
    if not (web_root / "index.html").is_file():
        print("measure_web_layout: no index.html under %s" % web_root, file=sys.stderr)
        return 2
    chrome = find_chrome(args.chrome)
    if chrome is None:
        print(NO_BROWSER_MESSAGE, file=sys.stderr)
        return 2

    created_scratch = args.scratch is None
    scratch = Path(tempfile.mkdtemp(prefix="kdt-layout-probe-")) if created_scratch else Path(args.scratch).resolve()
    scratch.mkdir(parents=True, exist_ok=True)
    server = start_server(web_root, args.scenario, (args.width, args.height), max(0, args.root_images),
                          args.toml, args.lang or "")
    port = server.server_address[1]
    report = {
        "chrome": chrome,
        "web": str(web_root),
        "scenario": args.scenario,
        "root_images": max(0, args.root_images),
        "toml_exists": bool(args.toml),
        "theme": args.theme,
        "lang": args.lang or "",
        "scratch": str(scratch),
        "scratch_removed": False,
        "requested": {"width": args.width, "height": args.height},
    }
    status = 0
    failure = None
    try:
        themes = {}
        for name, seed, toggle in theme_runs(args.theme):
            url = "http://127.0.0.1:%d/__probe__.html?seed=%s&toggle=%d" % (port, seed, 1 if toggle else 0)
            before = len(server.requests)
            dom = run_browser(chrome, url, scratch, args.width, args.height, args.timeout)
            measured = parse_probe_result(dom)
            block = theme_block(name, seed, toggle, measured)
            block["api_requests"] = server.requests[before:]
            block["url"] = url
            themes[name] = block
            if "steps" not in report:
                # The top-level fields keep their old meaning: the first pass of the run. The per-theme
                # blocks carry their own copies, so a --theme dark run is still fully readable.
                steps = measured.get("steps", [])
                report["url"] = url
                report["viewport"] = steps[0]["viewport"] if steps else None
                report["steps"] = steps
                report["page_errors"] = measured.get("errors", [])
                report["api_requests"] = block["api_requests"]
        report["themes"] = themes
        report["theme_toggle"] = toggle_summary(themes)
        report["shots"] = (
            take_shots(chrome, port, scratch, args)
            if args.shot else None
        )
    except RuntimeError as exc:
        failure = str(exc)
        status = 1
    finally:
        # The scratch directory is gone before the report is printed, so "scratch_removed" in the
        # output is the truth about the run rather than a plan.
        server.shutdown()
        server.server_close()
        if created_scratch and not args.keep_scratch:
            shutil.rmtree(scratch, ignore_errors=True)
            report["scratch_removed"] = True
    if failure is not None:
        print("measure_web_layout: %s" % failure, file=sys.stderr)
        return status
    text = json.dumps(report, indent=2, ensure_ascii=False)
    print(text)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
