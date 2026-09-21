"""§4.13 folder mosaic preview in the center gallery.

The user's own words: "in plain folder-browsing mode the preview UI should show
the folders, and the folder preview content should be a mosaic of the image files
inside the folder".

The criteria come in three layers; missing one produces "looks done, but not
actually wired up":

1. **Wiring**: the data really comes from `/api/fs/list`'s
   `dirs[].preview_images`, renders only at the level with "only folders, no
   images", and clicking really enters;
2. **CSS**: the 3×3 grid and uniform card size (Node has no layout engine, so only
   the rule bodies can be inspected, the same approach as `test_web_dirlist.py`);
3. **Behavior**: actually run `gallery.js` once (fake DOM, no jsdom) -- 9
   thumbnails, directory name and image count, click callback, and bitmaps
   released when switching directories.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
WEB = REPO / "src" / "kohya_dataset_tagger" / "web"
JS = WEB / "js"
GALLERY = JS / "gallery.js"
APP = JS / "app.js"
STRINGS = JS / "locales" / "zh-CN.js"
CSS = WEB / "css" / "app.css"


def text_of(path: Path) -> str:
    assert path.is_file(), "not found: %s" % path
    return path.read_text(encoding="utf-8")


def strip_comments(css: str) -> str:
    """Strip /* ... */ comments (comments contain braces too)."""
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
    """Take the declaration body of one rule; selector is matched literally."""
    for chunk in strip_comments(css).split("}")[:-1]:
        head, _, body = chunk.rpartition("{")
        if head.strip() == selector:
            return body
    raise AssertionError("no such selector in the CSS: %s" % selector)


# ---------------------------------------------------------------------------
# 1. Wiring
# ---------------------------------------------------------------------------


def test_gallery_renders_one_card_per_directory() -> None:
    body = text_of(GALLERY)
    assert "function makeDirCard(" in body, "gallery.js has no folder card"
    assert '"dir-card"' in body and '"dir-thumbs dir-thumbs-"' in body and '"dir-thumb"' in body, (
        "the card's three class names are incomplete: card / mosaic (with a capacity suffix) / single thumbnail"
    )
    # The mosaic follows the sample count: 1 fills the card, 2-4 use 2×2, 5+ use 3×3.
    # A fixed 3×3 leaves a tiny square tucked in the corner for a directory with
    # only 1 image -- the user pointed this out at first glance as "not the optimal one".
    assert "samples.length >= 5 ? 3 : (samples.length >= 2 ? 2 : 1)" in body, (
        "the mosaic does not adapt to the sample count (1/4/9)"
    )
    assert '"dir-thumbs dir-thumbs-" + side' in body, "the adaptive value is computed but never applied to the class name"
    assert "samples.slice(0, side * side)" in body, "extra samples are not trimmed to the mosaic capacity"
    assert "dir.preview_images" in body, "the card does not take its sample images from dirs[].preview_images"
    assert 't("browse.dirImageCount", { n: dir.image_count })' in body, (
        "the count does not reuse the shared copy (it would disagree with the left-column directory row)"
    )
    assert 't("folder.openTitle"' in body, "the card has no readable title"
    assert "card.addEventListener(\"click\"" in body, "the card has no click handler"
    assert "onOpenDir(" in body, "the card click is not surfaced through a callback"
    assert 'loading = "lazy"' in body, "mosaic thumbnails must lazy-load"


def test_folder_cards_only_appear_at_a_folder_only_level() -> None:
    """The level that has images stays as it was: the gallery shows images, not folders."""
    body = text_of(APP)
    assert "function galleryFolders()" in body, "app.js does not consolidate 'when to show folders' into one decision"
    assert "state.images.length === 0 ? state.dirs : []" in body, "folder cards must not be inserted when there are images"
    assert "gallery.setImages(state.images, galleryFolders())" in body, (
        "openDir does not hand the subdirectories to the gallery"
    )
    assert "preview_images: Array.isArray(item.preview_images)" in body, (
        "app.js does not pass preview_images down (without it the mosaic is empty)"
    )
    assert "onOpenDir: (path) => { void openDir(path, state.rootName); }" in body, (
        "clicking a folder card does not actually open that directory"
    )
    assert "el.hidden = gallery.getFolderCount() > 0" in body, (
        "with folder cards present it still shows 'no images in this directory' = self-contradictory"
    )


def test_strings_js_has_the_folder_open_title() -> None:
    line = next((ln for ln in text_of(STRINGS).splitlines() if '"folder.openTitle"' in ln), None)
    assert line is not None, "strings.js is missing folder.openTitle"
    assert "{name}" in line, "folder.openTitle has no directory-name placeholder"


# ---------------------------------------------------------------------------
# 2. CSS
# ---------------------------------------------------------------------------


def test_folder_mosaic_css_adapts_to_one_four_or_nine() -> None:
    """The old fixed 3×3 produced a tiny square tucked in the corner for a directory with 1 image."""
    css = text_of(CSS)
    thumbs = rule(css, ".dir-card .dir-thumbs")
    assert "aspect-ratio: 1 / 1" in thumbs, "card heights are not uniform"
    assert "overflow: hidden" in thumbs, "thumbnails leak outside the rounded corners"
    # columns/rows for each of the three capacities; missing either half distorts the mosaic or leaves blank space
    expected = {"1": 1, "2": 2, "3": 3}
    for suffix, cells in expected.items():
        body = rule(css, ".dir-thumbs-" + suffix)
        assert "grid-template-columns: repeat(%d, 1fr)" % cells in body, (
            ".dir-thumbs-%s has no %d columns" % (suffix, cells)
        )
        assert "grid-template-rows: repeat(%d, 1fr)" % cells in body, (
            ".dir-thumbs-%s has no %d rows: with fewer than %d images the card gets "
            "shorter and the grid is ragged"
            % (suffix, cells, cells * cells)
        )

    card = rule(css, ".gallery .dir-card")
    assert "flex-direction: column" in card, "the card is not the vertical 'thumbnail + name + count' layout"
    assert "cursor: pointer" in card, "the card looks unclickable"

    thumb = rule(css, ".dir-thumb")
    assert "object-fit: cover" in thumb, "the mosaic leaves blank space or distorts"
    assert "width: 100%" in thumb and "height: 100%" in thumb, "the thumbnail does not fill its cell"


# ---------------------------------------------------------------------------
# 3. Behavior: actually run gallery.js once (fake DOM, no jsdom)
# ---------------------------------------------------------------------------


def test_folder_cards_behave_headlessly_against_a_fake_dom() -> None:
    "Actually run gallery.js: a 9-cell thumbnail grid, directory name and count, clicking enters the directory, and bitmaps are released when switching directories."
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed here")
    harness = REPO / "test" / "fixtures" / "folder_grid_harness.mjs"
    assert harness.is_file(), "missing headless behavior fixture %s" % harness
    done = subprocess.run(
        [node, str(harness), str(WEB)],
        capture_output=True, encoding="utf-8", errors="replace",
    )
    assert done.returncode == 0, "folder card behavior criterion failed:\n%s\n%s" % (done.stdout, done.stderr)
    assert "folder grid harness OK" in done.stdout
