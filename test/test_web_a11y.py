"""Keyboard and screen-reader reachability of the controls a user has to operate.

Four findings from the 2026-09 accessibility audit, all "the control exists but only for a mouse
or only for people who can see it":

1. The tag frequency table - the only way to narrow the image set - was a list of `<span>`s with a
   click delegate, so the tri-state filter was unreachable from the keyboard.
2. The active tab was signalled by background colour alone, and the strip carried `role="tab"` with
   no `aria-selected`, no `aria-controls` and no panel role, so nothing told a screen reader which
   panel was showing.
3. Every chip remove button announced itself as "times": the visible glyph is the accessible name,
   and the `title` that says which tag is removed loses to it.
4. The directory picker never took focus, while its key handler listened on the document - so an
   Enter aimed at the page behind it resolved the picker and silently "used" the directory on
   screen (that path adds a root).

The behavioural half lives in `test/fixtures/filter_harness.mjs` (rows are real buttons carrying
their state) and `test/fixtures/picker_harness.mjs` (focus moves in, a stray key is ignored); this
file pins the rest statically, because `app.js` has no headless harness.
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WEB = REPO / "src" / "kohya_dataset_tagger" / "web"
JS = WEB / "js"
CSS = WEB / "css" / "app.css"


def source(name: str) -> str:
    return (JS / name).read_text(encoding="utf-8")


def test_frequency_rows_are_buttons_with_a_spoken_state() -> None:
    body = source("filter.js")
    assert 'const row = document.createElement("button");' in body, (
        "frequency rows are not buttons, so the tri-state filter stays mouse-only"
    )
    assert 'spoken.className = "sr-only";' in body, "the state is a colour only"
    assert 't("filter.stateOff")' in body, "the neutral state has no accessible name"
    css = CSS.read_text(encoding="utf-8")
    assert ".sr-only {" in css, "the class that hides the spoken state has no styling"
    assert ".freq-item:focus-visible" in css, "a focused frequency row shows no focus ring"


def test_chip_remove_buttons_name_their_tag() -> None:
    for name in ("tags.js", "filter.js", "batch.js"):
        body = source(name)
        assert 'remove.setAttribute("aria-label"' in body, (
            "%s: a chip remove button still announces itself as the times glyph" % name
        )


def test_the_tab_strip_is_a_real_tablist() -> None:
    app = source("app.js")
    assert 'tab.setAttribute("aria-controls", "panel-" + name);' in app, "tabs do not say what they control"
    assert 'panel.setAttribute("role", "tabpanel");' in app, "the panels are not panels"
    assert 'panel.setAttribute("aria-labelledby", tab.id);' in app, "panels are not labelled by their tab"
    assert 'other.setAttribute("aria-selected", active ? "true" : "false");' in app, "the selection is invisible"
    assert "other.tabIndex = active ? 0 : -1;" in app, "no roving tabindex, so Left/Right has no anchor"
    assert 'event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0' in app, "no arrow-key navigation"
    assert 'selectTab(initial, { runHooks: false })' in app, (
        "the initially active tab never gets its aria state, or boot runs panel hooks early"
    )
    css = CSS.read_text(encoding="utf-8")
    assert "box-shadow: inset 0 -2px 0 var(--accent);" in css, "the active tab is still colour-only"


def test_the_picker_owns_the_keyboard_while_it_is_open() -> None:
    body = source("picker.js")
    assert "if (!insideDialog(event.target)) return;" in body, (
        "a key aimed at the page behind the dialog can still settle the picker"
    )
    assert "restoreFocusTo = document.activeElement || null;" in body, "focus is never remembered"
    assert "pathInput.focus();" in body, "the dialog never takes focus"
    assert "if (restoreFocusTo && typeof restoreFocusTo.focus === \"function\") restoreFocusTo.focus();" in body, (
        "focus is never handed back"
    )


def test_every_dialog_hands_focus_back() -> None:
    """A modal that swallows focus leaves the user at the top of the page."""
    for name in ("picker.js", "zoom.js", "settings.js", "crop.js"):
        body = source(name)
        assert "restoreFocusTo = document.activeElement || null;" in body, (
            "%s: does not remember where focus was" % name
        )
        assert "restoreFocusTo.focus()" in body, "%s: never gives focus back" % name


def test_an_image_is_named_once() -> None:
    """A tile announced twice, or a diff row with no name at all, both cost the reader."""
    gallery = source("gallery.js")
    # pinned together with the tile's other attribute: gallery.js sets alt="" on
    # the folder-card thumbnails too, and a bare alt check passed while the tile
    # still repeated its figcaption (found by falsifying this very criterion)
    assert 'img.alt = "";\n  img.title = item.name;' in gallery, (
        "the tile image repeats the file name its figcaption already carries"
    )
    zoom = source("zoom.js")
    assert "image.alt = String(path).split(" in zoom, "the enlargement announces a whole absolute path"
    for name, field in (("batch.js", "entry.image"), ("autotag.js", "item.image")):
        body = source(name)
        marker = "sr-only\", String(" + field + ")"
        assert marker in body, (
            "%s: a diff row is attributed to a file only through a title attribute" % name
        )


def test_the_language_switcher_shows_a_decorative_icon() -> None:
    """A bare <select> says nothing about what it switches; a globe does, without being read aloud.

    Decorative on purpose: the control already carries `lang.label` as its accessible name and
    `lang.hint` as its tooltip, so the icon must not add a second, glyph-shaped name.
    """
    markup = (WEB / "index.html").read_text(encoding="utf-8")
    start = markup.index('id="lang-select"')
    label = markup[:start]
    icon = label.rindex("<svg")
    svg = label[icon:]
    assert 'aria-hidden="true"' in svg, "the language icon is not decorative: it would name the control"
    assert 'focusable="false"' in svg, "an aria-hidden svg is still focusable in some browsers"
    styles = CSS.read_text(encoding="utf-8")
    assert ".field-icon" in styles, "the icon has no size rule: the flex label would size it instead"
    assert (WEB / "js" / "locales" / "zh-CN.js").read_text(encoding="utf-8").count('"lang.label"') == 1
