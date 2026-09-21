"""The caption editor of the active image lives in the gallery, not on a tab.

Why this exists
---------------
Captioning is this tool's main loop: open a directory, walk the grid, fix one
caption after another. That loop used to be interrupted by the panel layout -
the single tag editor sat INSIDE the right column's tab strip, so the moment the
user looked at Filter / Tagger / Cache / Batch / Export / Scale there was no
editor on screen and the only way to one was a double-click into the modal zoom
overlay. The editor is now pinned below the tab panels, outside the strip.

Three things have to hold together, and none is visible from the other two, so
they are pinned separately here:

1. **Position**: the editor's elements are inside #caption-dock, the dock is not
   a .tab-panel, and the tab switcher only ever hides .tab-panel - so no tab can
   take the editor off screen;
2. **One instance per surface**: app.js constructs exactly one editor (the dock)
   and zoom.js exactly one (the modal that covers it). A third would be a second
   live copy of one caption, which is the thing the zoom overlay's flush/disable
   dance exists to prevent;
3. **Behaviour**: nothing active hides the control block, so the dock shows its
   short empty state instead of a disabled empty form, and the status line is
   repainted on a language switch (fixtures/caption_dock_harness.mjs runs the
   real tags.js, gallery.js and api.js).
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
MARKUP = WEB / "index.html"
CSS = WEB / "css" / "app.css"
APP = JS / "app.js"
ZOOM = JS / "zoom.js"
TAGS = JS / "tags.js"
HARNESS = REPO / "test" / "fixtures" / "caption_dock_harness.mjs"

#: The controls of the editor: what has to be on screen next to the grid.
EDITOR_IDS = ("preview-img", "active-meta", "tag-chips", "tag-input", "btn-add-tag",
              "raw-toggle", "btn-save-tags", "raw-text")

STRING_PAIR_RE = re.compile(r'"([^"\\]+)"\s*:\s*"((?:[^"\\]|\\.)*)"')


def text_of(path: Path) -> str:
    assert path.is_file(), "not found: %s" % path
    return path.read_text(encoding="utf-8")


def rule(css: str, selector: str) -> str:
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert match is not None, "CSS has no %s" % selector
    return match.group(1)


def dock_markup() -> str:
    """The markup from #caption-dock to the end of the right column."""
    markup = text_of(MARKUP)
    start = markup.index('id="caption-dock"')
    end = markup.index("</aside>", start)
    return markup[start:end]


def pack_keys(name: str) -> dict:
    return {key: value for key, value in STRING_PAIR_RE.findall(text_of(LOCALES / name))}


def relabel_body() -> str:
    """The body of app.js's relabelAfterLocaleChange(), where render-time copy is re-registered."""
    app = text_of(APP)
    start = app.index("async function relabelAfterLocaleChange()")
    return app[start:app.index("\n}\n", start)]


# ---------------------------------------------------------------------------
# 1. Position: no tab switch can take the editor off screen
# ---------------------------------------------------------------------------


def test_the_editor_sits_in_the_dock_and_not_in_a_tab_panel() -> None:
    markup = text_of(MARKUP)
    dock = dock_markup()
    for element_id in EDITOR_IDS:
        assert 'id="%s"' % element_id in dock, (
            "#%s is not in #caption-dock - it is back inside the tab strip, where a tab switch hides it"
            % element_id
        )
    assert "data-panel=" not in dock, "the dock is a tab panel again; a tab switch will hide the editor"
    opening = markup[markup.index('id="caption-dock"'):markup.index(">", markup.index('id="caption-dock"'))]
    assert "hidden" not in opening, "the dock must not carry hidden: it is on screen for every tab"
    panels = markup.index('class="tab-panels"')
    assert markup.index('id="caption-dock"') > panels, "the dock must come after the tab panels"


def test_the_tab_switcher_only_hides_tab_panels() -> None:
    app = text_of(APP)
    assert 'document.querySelectorAll(".tab-panel")' in app, (
        "the tab switcher no longer selects .tab-panel; the dock would be hidden with the panels"
    )
    assert "panel.hidden = panel.dataset.panel !== tab.dataset.tab;" in app, (
        "the tab switcher changed shape: re-check that #caption-dock (no data-panel) is never hidden"
    )
    markup = text_of(MARKUP)
    tabs = set(re.findall(r'data-tab="([^"]+)"', markup))
    panels = set(re.findall(r'data-panel="([^"]+)"', markup))
    assert tabs == panels, "tabs and panels do not match: tabs=%s panels=%s" % (sorted(tabs), sorted(panels))
    assert "tags" not in tabs, "the Tags tab is back; its content now lives in the dock"


def test_the_appended_panels_land_inside_the_scrolling_wrapper() -> None:
    app = text_of(APP)
    assert app.count('panelHost: document.querySelector(".tab-panels")') == 4, (
        "the batch/export/scale/crop panels must be appended to .tab-panels, not to .panel-right, "
        "or they render after the pinned dock"
    )
    assert 'panelHost: document.querySelector(".panel-right")' not in app, (
        "a panel is still appended straight into the column, which puts it below the dock"
    )
    assert 'document.querySelector(".panel-right")' in app, "the right drag handle lost its column"


def test_the_dock_is_pinned_with_its_own_scrolling() -> None:
    css = text_of(CSS)
    column = rule(css, ".panel-right")
    assert "display: flex" in column and "flex-direction: column" in column, (
        "the right column is not a flex column: the dock cannot be pinned below the panels"
    )
    assert "var(--right-w)" in column, "the right column width is no longer wired to the variable"
    panels = rule(css, ".tab-panels")
    assert "flex: 1 1 auto" in panels and "min-height: 0" in panels, (
        "the tab panels must be the part that shrinks, or the dock pushes them out"
    )
    assert "overflow-y: auto" in panels, "the tab panels must scroll on their own"
    dock = rule(css, ".caption-dock")
    assert "flex: 0 0 auto" in dock, "the dock must keep its natural height"
    assert "max-height" in dock and "overflow-y: auto" in dock, (
        "the dock needs a bounded height with its own scrolling: a long chip list must not eat the panels"
    )


# ---------------------------------------------------------------------------
# 2. One editor instance per surface
# ---------------------------------------------------------------------------


def test_one_editor_instance_per_surface() -> None:
    # the call sites, not the definition in tags.js itself
    call = "createTagEditor({"
    app = text_of(APP)
    zoom = text_of(ZOOM)
    assert app.count(call) == 1, (
        "app.js must construct exactly one editor (the dock): a second live copy of one caption overwrites the first"
    )
    assert zoom.count(call) == 1, "zoom.js must construct exactly one editor (the modal)"
    total = sum(text_of(path).count(call) for path in JS.rglob("*.js"))
    assert total == 2, "a third tag editor instance appeared (%d): two surfaces, two instances" % total


# ---------------------------------------------------------------------------
# 3. Nothing active: the empty state, not an empty form
# ---------------------------------------------------------------------------


def test_no_active_image_hides_the_form_and_leaves_the_empty_state() -> None:
    dock = dock_markup()
    assert 'id="caption-form"' in dock, "the control block has no wrapper to hide"
    assert 'id="active-meta" class="meta" data-copy="caption.noActiveImage"' in dock, (
        "the dock's status line is not the empty state (data-copy=caption.noActiveImage)"
    )
    app = text_of(APP)
    assert 'form: $("caption-form"),' in app, "app.js does not hand the control block to the editor"
    assert 'el.textContent = t("caption.noActiveImage");' in app, (
        "paintActiveMeta() no longer paints the empty state for 'nothing active'"
    )
    tags = text_of(TAGS)
    assert "if (formEl) formEl.hidden = true;" in tags, "disable() does not hide the control block"
    assert "if (formEl) formEl.hidden = false;" in tags, "enable() does not bring the control block back"


def test_a_failed_load_stays_on_the_dock_status_line() -> None:
    """The status line sits outside the form that gets hidden, so a failure cannot be hidden with it."""
    app = text_of(APP)
    assert 'activeMeta = { kind: "failed", message: describeError(err) };' in app, (
        "a failed load no longer records itself on the dock status line"
    )
    dock = dock_markup()
    assert dock.index('id="active-meta"') < dock.index('id="caption-form"'), (
        "the status line must be outside (and before) the form that gets hidden"
    )


def test_the_status_line_follows_a_language_switch() -> None:
    body = relabel_body()
    assert "paintActiveMeta();" in body, (
        "relabelAfterLocaleChange() does not repaint the dock's status line: it is painted at render time, "
        "so a language switch would leave the size line and the empty state in the old language"
    )
    assert "relabelComponent(tagEditor);" in body, "the editor's own copy is no longer relabelled"


def test_the_empty_state_copy_lives_in_the_packs_only() -> None:
    packs = {name: pack_keys(name) for name in ("zh-CN.js", "en.js", "ja.js")}
    for name, keys in packs.items():
        assert keys.get("caption.noActiveImage"), "%s has no caption.noActiveImage" % name
        assert "{" not in keys["caption.noActiveImage"], (
            "%s: the empty state carries a placeholder, so it has to be repainted on a switch" % name
        )
    for key in ("caption.collapse", "caption.expand"):
        for name, keys in packs.items():
            assert keys.get(key), "%s has no %s" % (name, key)
            assert "{" not in keys[key], "%s: %s carries a placeholder and must be repainted on a switch" % (name, key)
    for dead in ("gallery.noActive", "tab.tags"):
        for name, keys in packs.items():
            assert dead not in keys, (
                "%s still carries %s, which nothing renders any more (the dock reuses caption.noActiveImage)"
                % (name, dead)
            )


def test_the_dock_can_be_folded_away_without_breaking_the_empty_state() -> None:
    """A permanent dock takes column height from every panel; on a short window the
    user has to be able to give it back (measured in a real browser: a 720px viewport
    left the panels 242px of 655)."""
    dock = dock_markup()
    assert 'id="btn-caption-toggle"' in dock, "the dock has no collapse control"
    assert 'aria-controls="caption-form"' in dock, "the toggle does not name the region it folds"
    css = text_of(CSS)
    folded = rule(css, ".caption-dock.is-collapsed #caption-form")
    assert "display: none" in folded, (
        "folding must work through the dock's own class and really hide the block: [hidden] belongs to "
        "enable()/disable(), so a fold expressed that way would pop back open on the next image"
    )
    app = text_of(APP)
    assert '$("btn-caption-toggle").addEventListener("click"' in app, "the toggle is not wired"
    assert "setCaptionCollapsed(readCaptionCollapsed());" in app, "the remembered fold is never applied"
    assert 't("caption.collapse")' in app and 't("caption.expand")' in app, (
        "the toggle's label is not painted from the folded state"
    )
    assert "paintCaptionToggle();" in relabel_body(), (
        "a language switch would leave the toggle in the previous language"
    )


def test_the_editor_behaves_headlessly() -> None:
    """Actually run tags.js + gallery.js + api.js: the empty state, following the
    active image (tile click and arrow keys), and the one write path with its cache answer."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed here")
    assert HARNESS.is_file(), "missing headless behavior fixture %s" % HARNESS
    done = subprocess.run(
        [node, str(HARNESS), str(WEB)],
        capture_output=True, encoding="utf-8", errors="replace",
    )
    assert done.returncode == 0, "caption dock behavior criterion failed:\n%s\n%s" % (done.stdout, done.stderr)
    assert "caption dock harness OK" in done.stdout
