"""Light mode: the palette architecture, the pre-paint boot, and the switch.

Three things are pinned here, and each one is a way the change could rot silently:

 1. **The arithmetic.** app.css declares every colour twice (light in `:root`, dark in
    `:root[data-theme="dark"]`) and paints with the names. Two names that drifted apart, a name that
    exists in one theme only, a `var(--x)` nobody defines, or a hard-coded colour below the token
    blocks all pass a visual check and fail a user - so they are computed, not eyeballed. Every pair
    the brief names is asserted at WCAG AA (4.5:1) **in both themes**, and the light theme is
    additionally required to have borders at least as visible as the dark ones.
 2. **The dark theme has not moved.** Its values are pinned against the literals this file used to
    carry, one row per literal: dark mode is the theme that already existed, and a light-mode round
    that quietly restyles it is a regression nobody asked for.
 3. **The pre-paint boot.** `js/theme-boot.js` is a classic `<head>` script and the only non-module
    script in the page; a deferred module runs after the first paint, which is exactly the light
    flash the boot exists to remove. Its storage key is written out a second time (it cannot import
    the module - that is what would make it a module), and the two copies are compared here.

The behavioural half is `test/fixtures/theme_harness.mjs`: a storage that throws, a garbage value,
the toggle, the checkbox/attribute agreement, and "the boot script and the module read the same key"
over a fake DOM the harness evaluates both in.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
WEB = REPO / "src" / "kohya_dataset_tagger" / "web"
JS = WEB / "js"
LOCALES = JS / "locales"
CSS = WEB / "css" / "app.css"
HTML = WEB / "index.html"
APP = JS / "app.js"
THEME = JS / "theme.js"
BOOT = JS / "theme-boot.js"
HARNESS = REPO / "test" / "fixtures" / "theme_harness.mjs"

#: The six colours that carry text, and the three page surfaces they are read on.
TEXT_COLOURS = ("text", "text-dim", "accent", "danger", "warn", "ok")
PAGE_SURFACES = ("bg", "bg-panel", "bg-elev")
#: WCAG 2.1 AA for normal-size text. The UI is 11-13px, so nothing here qualifies as "large text".
AA = 4.5

#: The three blocks, by their real selectors - and by SELECTOR, not by a comment, so a criterion
#: cannot be fooled by prose. Block 2 matches in both themes on purpose: an on-image colour exists
#: once, so it cannot drift. (A regex looking for the dark selector alone finds block 2's second
#: selector line first, which is exactly the mistake this table makes impossible.)
LIGHT_SELECTOR = ":root"
IMAGE_SELECTOR = ':root, :root[data-theme="dark"]'
DARK_SELECTOR = ':root[data-theme="dark"]'
#: One top-level rule: an optional multi-line selector (block 2's spans two lines), then a body.
#: [^{}] on both sides means the @media wrapper is skipped and its inner rules are matched
#: individually - which is all this file needs, and it cannot mis-nest.
RULE_RE = re.compile(r"(?m)^[ \t]*([^{}\n]*(?:\n[^{}\n]*)*?)[ \t]*\{([^{}]*)\}")
DECL_RE = re.compile(r"(--[a-z0-9-]+)\s*:\s*([^;]+);")
#: A colour literal: a hex colour, or a functional notation. The negative lookahead keeps `#btn-x`
#: selectors out (a hex colour is never followed by an identifier character).
COLOR_RE = re.compile(r"#[0-9a-fA-F]{3,8}(?![0-9a-zA-Z_-])|rgba?\(|hsla?\(")
HEX_RE = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
FUNC_RE = re.compile(r"^rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)")

#: The five names in the light block that are not colours. They are the layout constants the theme
#: must not swallow, and the criterion below derives this set from the values rather than trusting it.
LAYOUT_TOKENS = {"--tile", "--radius", "--left-w", "--right-w", "--mono"}

#: What the dark theme looked like before light mode existed: name -> the literal app.css used to
#: carry. Every one of these is compared whitespace-insensitively, so reformatting is free and a
#: changed channel is not.
LEGACY_DARK = {
    "--bg": "#14161a",
    "--bg-panel": "#1b1e24",
    "--bg-elev": "#22262e",
    "--line": "#333a45",
    "--text": "#e6e9ef",
    "--text-dim": "#9aa4b2",
    "--accent": "#4c8dff",
    "--accent-dim": "#2b4f8f",
    "--danger": "#ff5c5c",
    "--warn": "#ffb020",
    "--ok": "#46c07a",
    "--danger-bg": "#5a2020",
    "--tint-accent": "rgba(76,141,255,.18)",
    "--tint-warn": "rgba(255,176,32,.12)",
    "--tint-ok": "rgba(70,192,122,.14)",
    "--tint-danger": "rgba(255,92,92,.14)",
    "--checker-a": "#2a2e36",
    "--checker-b": "#23262c",
    "--skeleton-a": "#23262c",
    "--skeleton-b": "#313640",
    "--skeleton-flat": "#262a31",
    "--shadow-menu": "0 8px 24px rgba(0,0,0,.45)",
    "--shadow-dialog": "0 12px 40px rgba(0,0,0,.45)",
    "--scrim-dialog": "rgba(0,0,0,.55)",
    "--scrim-zoom": "rgba(0,0,0,.72)",
}

#: The literals that used to sit on the image, now block 2. Same rule: the pixels may not move.
LEGACY_ON_IMAGE = {
    "--badge-missing-fg": "#fff",
    "--badge-ring": "rgba(0,0,0,.5)",
    "--badge-unknown-bg": "#555b66",
    "--badge-unknown-fg": "#fff",
    "--badge-present-bg": "rgba(70,192,122,.85)",
    "--badge-present-fg": "#10231a",
    "--zoom-stage-bg": "#101216",
    "--zoom-nav-bg": "rgba(20,22,26,.55)",
    "--zoom-nav-bg-hover": "rgba(20,22,26,.85)",
    "--zoom-nav-border": "rgba(255,255,255,.16)",
    "--crop-outline": "rgba(255,255,255,.9)",
    "--crop-shade": "rgba(0,0,0,.55)",
    "--crop-guide": "rgba(255,255,255,.45)",
    "--crop-handle-bg": "#fff",
    "--crop-handle-border": "#111",
}

VOID_TAGS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
})


# ---------------------------------------------------------------------------
# reading app.css
# ---------------------------------------------------------------------------


def text_of(path: Path) -> str:
    assert path.is_file(), "cannot find %s" % path
    return path.read_text(encoding="utf-8")


def without_comments(css: str) -> str:
    """CSS comments removed. app.css has no nesting and no comment opener inside a string."""
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def code_of(path: Path) -> str:
    """A source file with its comments removed.

    A criterion about what the code *does* must not be tripped by prose that explains what it does
    not do: this repository names its rejected alternatives in comments on purpose.
    """
    body = path.read_text(encoding="utf-8", errors="replace")
    suffix = path.suffix.lower()
    if suffix == ".html":
        return re.sub(r"<!--.*?-->", "", body, flags=re.S)
    if suffix == ".css":
        return without_comments(body)
    if suffix == ".js":
        return re.sub(r"(?m)^\s*//.*$", "", without_comments(body))
    return body


def rules() -> list:
    """Every top-level rule of app.css, comments removed: {selector, body, start, end}."""
    css = without_comments(text_of(CSS))
    return [
        {
            "selector": re.sub(r"\s+", " ", match.group(1).strip()),
            "body": match.group(2),
            "start": match.start(),
            "end": match.end(),
        }
        for match in RULE_RE.finditer(css)
        if match.group(1).strip()
    ]


def rule_body(selector: str) -> str:
    found = [item for item in rules() if item["selector"] == selector]
    assert found, "app.css has no %r rule; the first selectors found were %s" % (
        selector, [item["selector"] for item in rules()][:10]
    )
    assert len(found) == 1, "app.css has %d %r rules" % (len(found), selector)
    return found[0]["body"]


def tokens(selector: str) -> dict:
    return {name: value.strip() for name, value in DECL_RE.findall(rule_body(selector))}


def light_tokens() -> dict:
    return tokens(LIGHT_SELECTOR)


def image_tokens() -> dict:
    return tokens(IMAGE_SELECTOR)


def dark_tokens() -> dict:
    return tokens(DARK_SELECTOR)


def normalize(value: str) -> str:
    return re.sub(r"\s+", "", str(value)).lower()


def rgb_of(value: str):
    """(r, g, b) for a hex or rgb()/rgba() colour; raises for anything else."""
    text = value.strip()
    hexed = HEX_RE.match(text)
    if hexed:
        digits = hexed.group(1)
        if len(digits) == 3:
            digits = "".join(ch * 2 for ch in digits)
        return tuple(int(digits[index:index + 2], 16) for index in (0, 2, 4))
    func = FUNC_RE.match(text)
    assert func is not None, "not a colour: %r" % value
    return tuple(float(part) for part in func.groups())


def _linear(channel: float) -> float:
    c = float(channel) / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def luminance(value) -> float:
    r, g, b = rgb_of(value) if isinstance(value, str) else value
    return 0.2126 * _linear(r) + 0.7152 * _linear(g) + 0.0722 * _linear(b)


def contrast(fg, bg) -> float:
    first, second = luminance(fg), luminance(bg)
    high, low = max(first, second), min(first, second)
    return (high + 0.05) / (low + 0.05)


def declarations(css: str) -> dict:
    """Every `--name: value` in the file, as name -> list of raw values."""
    out: dict = {}
    for name, value in DECL_RE.findall(without_comments(css)):
        out.setdefault(name, []).append(value)
    return out


# ---------------------------------------------------------------------------
# reading index.html
# ---------------------------------------------------------------------------


class TopbarScanner(HTMLParser):
    """The direct children of `<header class="topbar">`, in document order.

    Each child carries its own tag/id/classes/attributes plus **the ids of everything nested inside
    it**, so a criterion can say "the label that wraps #theme-dark" without a substring search - the
    difference between reading the nesting and matching text that happens to be somewhere in the file.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.topbar_depth = None
        self.children: list = []
        self.current = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        classes = (attributes.get("class") or "").split()
        if self.topbar_depth is not None and self.depth == self.topbar_depth:
            self.current = {
                "tag": tag, "id": attributes.get("id") or "", "class": classes,
                "attrs": attributes, "ids": [], "text": [],
            }
            self.children.append(self.current)
        elif self.current is not None and attributes.get("id"):
            self.current["ids"].append(attributes["id"])
        if tag in VOID_TAGS:
            return
        self.depth += 1
        if self.topbar_depth is None and "topbar" in classes:
            self.topbar_depth = self.depth

    def handle_data(self, data):
        if self.current is not None:
            self.current["text"].append(data)

    def handle_endtag(self, tag):
        if tag in VOID_TAGS:
            return
        self.depth -= 1
        if self.topbar_depth is not None and self.depth < self.topbar_depth:
            self.topbar_depth = None
            self.current = None
        elif self.current is not None and self.depth == self.topbar_depth:
            self.current = None


def topbar_children() -> list:
    scanner = TopbarScanner()
    scanner.feed(text_of(HTML))
    assert scanner.children, "index.html has no topbar header with children"
    return scanner.children


#: The header row's arrangement, as the identity of every child that belongs to the row, in order:
#: identity on the left (the brand lockup and the connection pill), the spacer, then the controls
#: that belong at the right end - the two PREFERENCES (set once and left alone) and, last, the
#: command bar. The census line left this row for the breadcrumb's (it counts the directory the
#: breadcrumb names, and the bar needed the room); there is no separator here, because the only
#: action that had a "here" side (the listing's refresh) lives on the grid's own row now.
#: #readiness is the header's second line, not a member of the row - it is checked separately.
TOPBAR_ROW = [
    "brand-lockup",
    "#server-status",
    ".spacer",
    "language",
    "theme-switch",
    "command-bar",
]


def child_role(child: dict) -> str:
    """What one top-bar child *is*, from its own id and classes and the ids nested inside it."""
    if child["id"]:
        return "#" + child["id"]
    if "brand-lockup" in child["class"]:
        return "brand-lockup"
    if "spacer" in child["class"]:
        return ".spacer"
    if "group-sep" in child["class"]:
        return ".group-sep"
    if "theme-switch" in child["class"]:
        return "theme-switch"
    if "palette-bar-wrap" in child["class"]:
        return "command-bar"
    if "lang-select" in child["ids"]:
        return "language"
    return "<%s class=%r>" % (child["tag"], " ".join(child["class"]))


SWITCH_MARKUP_RE = re.compile(
    r'<label class="field-inline theme-switch">\s*'
    r'<input type="checkbox" id="theme-dark" role="switch" />\s*'
    r'<span data-copy="theme\.dark"></span>\s*'
    r"</label>"
)
SCRIPT_TAG_RE = re.compile(r"<script\b([^>]*)>", re.I)
SCRIPT_SRC_RE = re.compile(r"""src\s*=\s*["']([^"']+)["']""", re.I)
MODULE_ATTR_RE = re.compile(r"""type\s*=\s*["']module["']""")


# ---------------------------------------------------------------------------
# 1. The arithmetic
# ---------------------------------------------------------------------------


def test_the_palettes_parse_into_a_non_trivial_set() -> None:
    """Guards the criteria below: a broken parse must not turn them into empty assertions.

    The three blocks are found by their real selectors, so this also pins that they exist at all -
    and that the dark selector did not swallow block 2, which is a mistake that made an earlier
    version of the set comparison pass for the wrong reason.
    """
    light, dark, image = light_tokens(), dark_tokens(), image_tokens()
    assert len(light) >= 25, "only %d tokens parsed out of the light block" % len(light)
    assert len(dark) >= 20, "only %d tokens parsed out of the dark block" % len(dark)
    assert len(image) >= 14, "only %d on-image tokens parsed" % len(image)
    assert "--badge-ring" in image and "--badge-ring" not in dark, (
        "the on-image block and the dark block are not told apart by their selectors"
    )
    assert "--line" in dark and "--line" not in image, (
        "the dark block did not parse; the dark selector matched another block"
    )
    for name, theme in (("light", light), ("dark", dark)):
        for token in TEXT_COLOURS + PAGE_SURFACES + ("accent-dim",):
            assert "--" + token in theme, "the %s theme has no --%s" % (name, token)
            rgb_of(theme["--" + token])  # raises if it is not a colour


def test_both_themes_reach_wcag_aa_on_every_page_surface() -> None:
    """6 text colours x 3 surfaces, plus text on --accent-dim, in BOTH themes.

    This is the criterion that makes "a light theme" a claim rather than a look: every pair the brief
    measured is recomputed here from the file, so a later palette edit that darkens a surface under a
    dim caption turns red instead of shipping.
    """
    for name, theme in (("light", light_tokens()), ("dark", dark_tokens())):
        failures = []
        for fg in TEXT_COLOURS:
            for bg in PAGE_SURFACES:
                ratio = contrast(theme["--" + fg], theme["--" + bg])
                if ratio < AA:
                    failures.append("%s on %s = %.2f:1" % (fg, bg, ratio))
        on_accent_dim = contrast(theme["--text"], theme["--accent-dim"])
        if on_accent_dim < AA:
            failures.append("text on accent-dim = %.2f:1" % on_accent_dim)
        assert not failures, "the %s theme is below %.1f:1:\n  %s" % (name, AA, "\n  ".join(failures))


def test_the_light_theme_has_borders_at_least_as_visible_as_the_dark_ones() -> None:
    """--line against the panel it draws on, compared between the themes.

    WCAG 1.4.11 (3:1 for a control boundary) is met by neither theme and is not chased here - but the
    *parity* is: a light theme whose borders are less visible than the dark theme's is a light theme
    with invisible borders, which is the failure this criterion exists for. It has teeth: the light
    --line given in the brief (#d3d9e0, 1.42:1) is below the dark one (1.46:1) and fails it.
    """
    light, dark = light_tokens(), dark_tokens()
    light_ratio = contrast(light["--line"], light["--bg-panel"])
    dark_ratio = contrast(dark["--line"], dark["--bg-panel"])
    assert light_ratio >= dark_ratio, (
        "the light theme's borders are less visible than the dark theme's: %.2f:1 vs %.2f:1"
        % (light_ratio, dark_ratio)
    )


def test_the_two_themes_define_exactly_the_same_colour_tokens() -> None:
    """A name that exists in one theme only is a `var(--x)` that resolves to nothing in the other,
    which paints a transparent background or an inherited colour - a silent, theme-dependent bug.

    The five layout constants are the one exception, and it is derived rather than declared: a token
    whose value is not a colour is not a theme token. Moving a colour in there (or leaving one in the
    dark block) turns this red.
    """
    light, dark = light_tokens(), dark_tokens()
    non_colour = {name for name, value in light.items() if not COLOR_RE.search(value)}
    assert non_colour == LAYOUT_TOKENS, (
        "the light block's non-colour tokens are %s; the layout constants are %s"
        % (sorted(non_colour), sorted(LAYOUT_TOKENS))
    )
    light_colours = set(light) - non_colour
    assert light_colours == set(dark), (
        "the themes define different colour tokens: light-only %s, dark-only %s"
        % (sorted(light_colours - set(dark)), sorted(set(dark) - light_colours))
    )


def test_every_variable_used_resolves_to_a_definition() -> None:
    """`var(--typo)` is not an error anywhere: it paints as nothing. The whole file is checked."""
    css = without_comments(text_of(CSS))
    defined = set(declarations(css))
    used = set(re.findall(r"var\(\s*(--[a-z0-9-]+)", css))
    assert len(used) >= 40, "only %d var() uses found; the scan is probably broken" % len(used)
    undefined = sorted(used - defined)
    assert not undefined, "these variables are used but never defined: %s" % undefined


def test_nothing_below_the_token_blocks_carries_a_colour_literal() -> None:
    """The architecture, enforced: three blocks at the top, and `var(--x)` everywhere under them.

    A colour written below the blocks is a colour that ignores the theme - it looks right in whichever
    theme it was typed in, which is how a light mode ends up with four dark rectangles in it.
    """
    css = without_comments(text_of(CSS))
    dark = [item for item in rules() if item["selector"] == DARK_SELECTOR]
    assert len(dark) == 1, "app.css does not have exactly one %r block" % DARK_SELECTOR
    below = css[dark[0]["end"]:]
    assert len(below) > 4000, "the scan starts too late (%d chars below the blocks)" % len(below)
    offenders = [line.strip() for line in below.splitlines() if COLOR_RE.search(line)]
    assert not offenders, "colour literals below the token blocks:\n  " + "\n  ".join(offenders)


def test_the_dark_theme_still_carries_every_legacy_colour() -> None:
    """Dark mode must not change at all: one row per literal app.css used to carry.

    Whitespace is ignored, so reformatting a value is free; changing a channel is not.
    """
    dark, on_image = dark_tokens(), image_tokens()
    for table, found, where in (
        (LEGACY_DARK, dark, "the dark block"),
        (LEGACY_ON_IMAGE, on_image, "the on-image block"),
    ):
        for name, legacy in table.items():
            assert name in found, "%s lost %s" % (where, name)
            assert normalize(found[name]) == normalize(legacy), (
                "%s changed %s: was %r, now %r" % (where, name, legacy, found[name])
            )


def test_the_theme_never_follows_the_operating_system() -> None:
    """Light is the default for everyone and dark is a stored choice; there is no third state.

    A prefers-color-scheme branch would make the pre-paint answer depend on the OS, which is what
    js/theme-boot.js cannot know before the first paint without a second resolution order - and the
    app is not being asked to guess. A ?theme= parameter is the same trap from the other side.
    """
    for path in sorted(WEB.rglob("*")):
        if not path.is_file():
            continue
        # Comments are stripped: this is a criterion about code, and the token is named in prose in
        # three of these files precisely to explain why it is absent.
        body = code_of(path)
        assert "prefers-color-scheme" not in body, (
            "%s follows the operating system; the default is light for everyone and dark is a stored "
            "choice" % path.relative_to(REPO).as_posix()
        )
    assert "URLSearchParams" not in text_of(THEME), (
        "theme.js reads a query parameter: that is a second way in, and it cannot be applied before "
        "the first paint without a second copy of the resolution order"
    )


# ---------------------------------------------------------------------------
# 2. The switch, and where it sits
# ---------------------------------------------------------------------------


def test_the_header_row_reads_identity_then_preferences_then_the_command_bar() -> None:
    """The row's order, read from the real nesting (TopbarScanner), not from substrings.

    Identity on the left; then, at the right end, the two PREFERENCES (the language and the theme,
    which are set once and left alone) and - the row's **last** control - the command bar. The
    brand lockup is the row's first item, so the accent rule is the row's left edge. The census
    readout is no longer a member of this row: it moved to the breadcrumb's, next to the
    directory it counts (test_web_breadcrumb.py pins the new row).

    This replaces the 2026-09-20 *first-child* criterion: the placement that round was handed
    ("the literal top-left") was withdrawn by the requester the same day, and the row was
    re-arranged three times (the refresh action left for the grid's row, the entry moved past the
    preferences, and the entry became the bar). The arrangement, the reasoning and the
    falsifications are in .github/memory/changelog/topbar-layout.md.
    """
    children = topbar_children()
    roles = [child_role(child) for child in children]
    row = [role for role in roles if role != "#readiness"]
    assert row == TOPBAR_ROW, (
        "the header row reads %s; the arrangement is %s (identity + state, then the right end)"
        % (row, TOPBAR_ROW)
    )
    assert roles[-1] == "#readiness", (
        "the readiness strip is not the header's last child, so something was added below the row: %s"
        % roles
    )
    assert row[-1] == "command-bar", (
        "the command bar is not the row's last control: %s" % row
    )
    assert SWITCH_MARKUP_RE.search(text_of(HTML)) is not None, (
        "the switch markup is not the agreed one (a wrapping label, a role=\"switch\" checkbox named "
        "#theme-dark, and a data-copy span)"
    )


def test_the_switch_copy_is_one_key_in_all_three_packs() -> None:
    """The label names the thing toggled and does not change with the state, so it is one key."""
    values = {}
    for name in ("zh-CN", "en", "ja"):
        pack = text_of(LOCALES / (name + ".js"))
        found = re.search(r'"theme\.dark"\s*:\s*"([^"]+)"', pack)
        assert found is not None, "the %s pack has no theme.dark" % name
        assert found.group(1).strip(), "the %s pack's theme.dark is empty" % name
        values[name] = found.group(1)
    assert len(set(values.values())) == 3, "theme.dark is not translated: %s" % values


def test_the_boot_script_is_in_head_after_the_stylesheet_and_is_not_a_module() -> None:
    """The whole point of the classic script: it runs before the first paint.

    A `type="module"` is deferred, so it runs after the paint - measured at 275 ms cold against a
    first paint at 116 ms, which is a visible light flash for a user who chose dark.
    """
    markup = text_of(HTML)
    head = markup[: markup.index("</head>")]
    stylesheet = head.index('href="./css/app.css"')
    boot = head.index('src="./js/theme-boot.js"')
    assert stylesheet < boot, "the boot script runs before the stylesheet it feeds"
    assert 'type="module"' not in code_of(HTML)[: code_of(HTML).index("</head>")], (
        "the boot script is a module, so it runs after the first paint"
    )
    assert BOOT.is_file(), "missing %s" % BOOT

    tags = SCRIPT_TAG_RE.findall(markup)
    assert tags, "no script tags in index.html"
    classic = [attrs.strip() for attrs in tags if not MODULE_ATTR_RE.search(attrs)]
    assert len(classic) == 1, "index.html has %d non-module scripts: %s" % (len(classic), classic)
    assert SCRIPT_SRC_RE.search(classic[0]).group(1) == "./js/theme-boot.js", (
        "the one non-module script is not the theme boot: <%s>" % classic[0]
    )
    for attrs in tags:
        assert SCRIPT_SRC_RE.search(attrs), "index.html has an inline script: <%s>" % attrs.strip()


# ---------------------------------------------------------------------------
# 3. One writer, one key
# ---------------------------------------------------------------------------


def test_app_wires_the_switch_and_the_module_owns_it() -> None:
    """The module is reachable from the page (an orphan module turns the contract test red), app.js
    hands it the real control, and the switch is registered for relabel like every other component."""
    app = text_of(APP)
    assert re.search(r'import\s*\{[^}]*createThemeSwitch[^}]*\}\s*from\s*"\./theme\.js"', app), (
        "app.js does not import theme.js"
    )
    assert 'createThemeSwitch({ input: $("theme-dark") })' in app, (
        "app.js does not hand the switch to the module, so nothing owns both the attribute and the box"
    )
    assert "relabelComponent(themeSwitch)" in app, (
        "the switch's label is not registered for relabel: a language switch would leave it behind"
    )


def test_the_module_and_the_boot_script_agree_on_the_storage_key() -> None:
    """The boot cannot import the module - importing is what would make it a module, which is the too
    late thing. So the key exists twice, and this is the criterion that makes drift impossible.

    Both sides are read as literals: the module's exported constant and the boot's getItem argument.
    A second copy of the key in either file is caught too, so "add a fallback key" cannot hide here.
    """
    theme, boot = text_of(THEME), text_of(BOOT)
    key_line = re.search(r'export const THEME_KEY = "([^"]+)"', theme)
    assert key_line is not None, "theme.js does not export THEME_KEY as a literal"
    assert key_line.group(1) == "kdt.theme"
    assert theme.count('"kdt.theme"') == 1, "theme.js carries the storage key more than once"
    assert boot.count('"kdt.theme"') == 1, "theme-boot.js carries the storage key more than once"
    assert 'getItem("kdt.theme")' in boot, (
        "the boot script does not read the key the module writes; a user who chose dark would get a "
        "light first paint"
    )
    assert "export " not in boot, (
        "theme-boot.js became a module: it would be deferred, which is the whole defect it exists for"
    )
    assert 'setAttribute("data-theme"' in boot, "the boot script does not set data-theme"


def test_the_switch_is_wired_in_one_writer_and_the_css_styles_it() -> None:
    """checked and the applied theme come out of one function, and the control is a switch.

    A checkbox painted from one value and an attribute written from another is the disagreement this
    shape forbids; the CSS half (appearance:none, a track, a knob, a focus ring, reduced motion) is
    read here because a criterion that only reads JS would pass on a native checkbox.
    """
    theme = text_of(THEME)
    paint = re.search(r"function paint\(value\)\s*\{(.*?)\n  \}", theme, re.S)
    assert paint is not None, "theme.js has no single paint() writer"
    body = paint.group(1)
    assert "applyTheme(" in body and "input.checked" in body, (
        "the attribute and the checkbox are not written together, so they can disagree"
    )
    assert 'setAttribute("data-theme"' in theme, "theme.js never writes the attribute"
    assert "readTheme(storage)" in theme, "theme.js does not read the stored theme at construction"

    css = text_of(CSS)
    assert '.theme-switch input[type="checkbox"] {' in css, "the switch has no rule of its own"
    assert "appearance: none" in css, "the switch is still the browser's own checkbox"
    assert '.theme-switch input[type="checkbox"]::before' in css, "the switch has no knob"
    assert '.theme-switch input[type="checkbox"]:checked' in css, "the switch has no checked state"
    assert '.theme-switch input[type="checkbox"]:focus-visible' in css, (
        "the switch has no visible focus ring"
    )
    reduced = re.search(r"@media \(prefers-reduced-motion: reduce\)\s*\{(.*?)\n\}", css, re.S)
    assert reduced is not None, "the reduced-motion block is gone"
    assert '.theme-switch input[type="checkbox"]::before' in reduced.group(1), (
        "the knob slides under reduced motion"
    )


def test_the_theme_behaves_headlessly() -> None:
    """Really run it: private-mode storage, a garbage value, the toggle, and the two key readers."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("no node on this machine")
    assert HARNESS.is_file(), "missing headless behavior fixture %s" % HARNESS
    done = subprocess.run(
        [node, str(HARNESS), str(WEB)],
        capture_output=True, encoding="utf-8", errors="replace",
    )
    assert done.returncode == 0, "theme behavior criteria failed:\n%s\n%s" % (done.stdout, done.stderr)
    assert "theme harness OK" in done.stdout
