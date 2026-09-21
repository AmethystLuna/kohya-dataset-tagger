"""The command palette (T3): one keyboard route to the app's own actions.

The palette is the only one of the four redesign items that **adds a surface**, so it is the only one
that can quietly become a second, invisible interface to the dataset. The criteria below are ordered
by how much damage the thing they pin would do:

1. **No privileged path.** Every entry's `run` is a single call to the function the button it names
   already calls - pinned per command against that button's own wiring, in the module that owns the
   button. An entry that grows a body, calls the API, or points at something no button reaches must
   go red. This is the property the whole feature stands on, and the reason the table is data.
2. **Unavailable is visible.** Every command is gated by its own control (which has the last word),
   the reason is painted on the row rather than the row vanishing, and Enter on it says why.
3. **The keyboard is honest.** Ctrl+K on the document (and never a toggle), arrows/Enter/Escape,
   **Tab leaves the panel and closes it** - a dropdown is not a modal - focus returned to the bar,
   a click outside closing it, and a refusal to open over a dialog that owns the keyboard.
4. **Copy and the relabel contract.** Every key the table names exists in all three packs (the
   contract test can only see literal `t("...")` call sites, and the table's keys are data), and the
   rows are repainted on a language switch.
"""
from __future__ import annotations

import base64
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
WEB = REPO / "src" / "kohya_dataset_tagger" / "web"
JS = WEB / "js"
LOCALES = JS / "locales"
APP = JS / "app.js"
PALETTE = JS / "palette.js"
HTML = WEB / "index.html"
CSS = WEB / "css" / "app.css"
JOBS = JS / "jobs.js"
AUTOTAG = JS / "autotag.js"
BATCH = JS / "batch.js"
SCALE = JS / "scale.js"
CROP = JS / "crop.js"
SETTINGS = JS / "settings.js"
TAGS = JS / "tags.js"
HARNESS = REPO / "test" / "fixtures" / "palette_harness.mjs"
#: The layout probe: it owns the canned /api/*, the loopback server and the headless browser, so the
#: one criterion here that needs a real page drives the app through it instead of growing a second one.
PROBE = REPO / "tools" / "measure_web_layout.py"

#: The commands, and the evidence that each one calls what its button calls. Every value is
#: (callee, [(module, the wiring that proves the button runs the same function), ...]).
#: A command that starts calling something else changes `callee` here and turns this criterion red -
#: which is the point: the palette must not be able to reach anything the UI cannot.
EXPECTED = {
    # The six tabs go through the app's own openTab(kind) route - the same one the job shelf's row
    # uses - and that route is what calls the tab strip's own selectTab(tab).
    "view.filter": ("openTab", [(APP, "openTab = (kind) => {"), (APP, "tab.addEventListener(\"click\", () => selectTab(tab));")]),
    "view.autotag": ("openTab", [(APP, "openTab = (kind) => {"), (APP, "onOpen: (kind) => { openTab(kind); },")]),
    "view.cache": ("openTab", [(APP, "openTab = (kind) => {"), (APP, "onOpen: (kind) => { openTab(kind); },")]),
    "view.batch": ("openTab", [(APP, "openTab = (kind) => {"), (APP, "onOpen: (kind) => { openTab(kind); },")]),
    "view.export": ("openTab", [(APP, "openTab = (kind) => {"), (APP, "onOpen: (kind) => { openTab(kind); },")]),
    "view.scale": ("openTab", [(APP, "openTab = (kind) => {"), (APP, "onOpen: (kind) => { openTab(kind); },")]),
    "view.crop": ("openTab", [(APP, "openTab = (kind) => {"), (APP, "onOpen: (kind) => { openTab(kind); },")]),
    "browse.refresh": ("refreshBrowse", [(APP, '$("btn-refresh").addEventListener("click", refreshBrowse);')]),
    "browse.parent": ("goToParent", [(APP, '$("btn-parent").addEventListener("click", goToParent);')]),
    "browse.pickDataset": ("requestPick", [
        (APP, "onPickDataset: () => { void requestPick(); },"),
        (SETTINGS, "if (onPickDataset) pickButton.addEventListener(\"click\", () => { onPickDataset(); });"),
    ]),
    "settings.modelRoots": ("modelRootsPanel.open", [
        (SETTINGS, "if (openButton) openButton.addEventListener(\"click\", open);"),
        (SETTINGS, "return { open, close, reload, relabel };"),
    ]),
    "gallery.selectAll": ("gallery.selectAll", [(APP, '$("btn-select-all").addEventListener("click", () => gallery.selectAll());')]),
    "gallery.selectMissing": ("selectMissingCaptions", [(APP, '$("btn-select-missing").addEventListener("click", () => { selectMissingCaptions(); });')]),
    "gallery.clearSelection": ("gallery.clearSelection", [(APP, '$("btn-clear-selection").addEventListener("click", () => gallery.clearSelection());')]),
    "filter.clearAll": ("filterPanel.clearAll", [(APP, '$("btn-filter-clear-all").addEventListener("click", () => filterPanel.clearAll());')]),
    "caption.save": ("tagEditor.save", [
        (TAGS, 'saveButton.addEventListener("click", () => { void save(); });'),
        (TAGS, "  return {\n    load,\n    save,"),
    ]),
    "autotag.preview": ("autotagPanel.preview", [
        (AUTOTAG, 'previewButton.addEventListener("click", () => { void runPreview(); });'),
        (AUTOTAG, "    preview: runPreview,"),
    ]),
    "autotag.run": ("autotagPanel.run", [
        (AUTOTAG, 'runButton.addEventListener("click", () => { void runAutotag(); });'),
        (AUTOTAG, "    run: runAutotag,"),
    ]),
    "batch.preview": ("batchPanel.preview", [
        (BATCH, 'previewButton.addEventListener("click", () => { void runPreview(); });'),
        (BATCH, "    preview: runPreview,"),
    ]),
    "batch.apply": ("batchPanel.apply", [
        (BATCH, 'applyButton.addEventListener("click", () => { void runApply(); });'),
        (BATCH, "    apply: runApply,"),
    ]),
    "cache.refresh": ("loadCacheStatus", [(APP, '$("btn-cache-refresh").addEventListener("click", () => { void loadCacheStatus(); });')]),
    "cache.rebuildAll": ("rebuildAllStaleCaches", [(APP, '$("btn-cache-rebuild-all").addEventListener("click", () => { void rebuildAllStaleCaches(); });')]),
    "export.generate": ("exportPanel.generate", [
        (APP, 'generateButton.addEventListener("click", () => { void runExport(); });'),
        (APP, "    generate: runExport,"),
    ]),
    "scale.start": ("scalePanel.start", [
        (SCALE, 'startButton.addEventListener("click", () => { void start(); });'),
        (SCALE, "    start,\n    cancel,"),
    ]),
    "crop.start": ("cropPanel.start", [
        (CROP, 'startButton.addEventListener("click", () => { void startRun(); });'),
        (CROP, "    start: startRun,"),
    ]),
}

#: The keys the table names as data. The contract test sees literal t("...") call sites only, so a
#: key that exists solely in this table would silently render as itself.
REASON_KEYS = (
    "palette.needDir", "palette.atRoot", "palette.needImages", "palette.needSelection",
    "palette.noFilter", "palette.needActiveImage", "palette.needScope", "palette.needModel",
    "palette.needPreview", "palette.nothingToWrite", "palette.needCaptions", "palette.nothingStale",
    "palette.needTarget", "palette.busy", "palette.notReady",
)
GROUP_KEYS = (
    "palette.group.view", "palette.group.browse", "palette.group.selection",
    "palette.group.caption", "palette.group.jobs", "palette.group.settings",
)
CHROME_KEYS = (
    "palette.title", "palette.openKey", "palette.openTitle", "palette.placeholder", "palette.keys",
    "palette.status", "palette.noMatch", "palette.close", "palette.unavailable",
)

#: The reasons a command can carry that are another surface's own wording (one claim, one string).
REUSED_KEYS = ("autotag.previewTooManyNote", "cache.loadFailed", "picker.disabled")

VOID_TAGS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
})


def text_of(path: Path) -> str:
    assert path.is_file(), "cannot find %s" % path
    return path.read_text(encoding="utf-8")


def source(name: str) -> str:
    return text_of(JS / name)


def pack(name: str) -> str:
    return text_of(LOCALES / (name + ".js"))


def strip_comments(css: str) -> str:
    """A stylesheet without its block comments, so a criterion reads declarations, not prose."""
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
    """One rule's declaration body; the selector is written literally."""
    for chunk in strip_comments(css).split("}")[:-1]:
        head, _, body = chunk.rpartition("{")
        if head.strip() == selector:
            return body
    raise AssertionError("CSS has no %s" % selector)


def tag_of(html: str, marker: str) -> str:
    """The whole start tag carrying `marker` (an id or an attribute), from < to >."""
    at = html.index(marker)
    start = html.rindex("<", 0, at)
    return html[start:html.index(">", at)]


def ancestry(html: str, element_id: str) -> list:
    """The tag/id/class tokens from the root down to #element_id (real nesting, not a substring)."""
    path: list = []

    class Walker(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.stack: list = []
            self.found = False

        def handle_starttag(self, tag, attrs):
            attributes = dict(attrs)
            if attributes.get("id") == element_id:
                path.extend(self.stack)
                self.found = True
            if tag not in VOID_TAGS:
                tokens = [tag]
                if attributes.get("id"):
                    tokens.append(attributes["id"])
                tokens.extend((attributes.get("class") or "").split())
                self.stack.append(tokens)

        def handle_startendtag(self, tag, attrs):
            self.handle_starttag(tag, attrs)
            if tag not in VOID_TAGS:
                self.stack.pop()

        def handle_endtag(self, tag):
            if tag in VOID_TAGS:
                return
            while self.stack:
                if self.stack.pop()[0] == tag:
                    break

    walker = Walker()
    walker.feed(html)
    assert walker.found, "index.html has no element with id %r" % element_id
    return path


#: The table is read as data: one field per line at four spaces. The parser is deliberately strict
#: about that shape so a reformat fails loudly here instead of silently testing nothing.
REGISTRY_OPEN = "\nconst commands = [\n"
REGISTRY_CLOSE = "\n];\n"
FIELD_RE = re.compile(r"^    (\w+): (.+),$", re.M)
RUN_RE = re.compile(r"^\(\) => ([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)?)\(([^()]*)\)$")
GATE_RE = re.compile(r'^gate\("([^"]*)"')


def registry_text(app: str) -> str:
    assert REGISTRY_OPEN in app, "app.js no longer has a command table"
    start = app.index(REGISTRY_OPEN) + len(REGISTRY_OPEN)
    end = app.index(REGISTRY_CLOSE, start)
    return app[start:end]


def unquote(value: str) -> str:
    return value[1:-1] if len(value) >= 2 and value.startswith('"') and value.endswith('"') else value


def command_table(app: str) -> list:
    entries = []
    for chunk in registry_text(app).split("\n  {\n")[1:]:
        block = chunk.split("\n  },\n")[0]
        fields = {name: unquote(value) for name, value in FIELD_RE.findall(block)}
        assert set(fields) == {"id", "group", "labelKey", "control", "run", "available"}, (
            "a command entry does not carry exactly the six documented fields on their own lines: %r"
            % (fields,)
        )
        entries.append(fields)
    assert len(entries) >= 20, "the command table shrank to %d entries" % len(entries)
    return entries


# ---------------------------------------------------------------------------
# 1. No privileged path: every entry calls what its button calls
# ---------------------------------------------------------------------------


def test_every_command_runs_the_function_its_own_button_runs() -> None:
    """THE criterion of this feature.

    A palette that can do something the UI cannot is a second, invisible interface to the dataset.
    So each entry's `run` has to be a single call to the function **the button named by the same
    entry** already calls, and that wiring is asserted in the module that owns the button. Changing
    an entry to call anything else - another function, an inline body, the API - turns this red.
    """
    app = text_of(APP)
    entries = command_table(app)
    ids = [entry["id"] for entry in entries]
    assert len(ids) == len(set(ids)), "two commands share an id: %s" % ids
    assert sorted(ids) == sorted(EXPECTED), (
        "the command table and this criterion disagree about which commands exist: "
        "missing %s, unexpected %s" % (sorted(set(EXPECTED) - set(ids)), sorted(set(ids) - set(EXPECTED)))
    )

    for entry in entries:
        callee, evidence = EXPECTED[entry["id"]]
        run = entry["run"]
        match = RUN_RE.match(run)
        assert match, (
            "%s: run must be one call expression of the form '() => fn(args)', got %r - an entry "
            "carries no implementation of its own" % (entry["id"], run)
        )
        assert match.group(1) == callee, (
            "%s calls %s(); the button it stands for calls %s()" % (entry["id"], match.group(1), callee)
        )
        for module, wiring in evidence:
            assert wiring in text_of(module), (
                "%s: %s no longer wires %s the way the palette expects: %s"
                % (entry["id"], module.name, callee, wiring)
            )


def test_the_controls_the_commands_name_are_the_controls_that_exist() -> None:
    """Every command names a control, and that control really is on the page (or is built by a panel).

    A typo in a control id is not a broken entry - it is an entry with no gate at all, which is the
    privileged path this design forbids.
    """
    html = text_of(HTML)
    markup_ids = set(re.findall(r'\bid="([^"]+)"', html))
    assigned = set()
    tabs = set()
    for path in sorted(JS.glob("*.js")):
        body = text_of(path)
        assigned.update(re.findall(r'\bid = "([^"]+)"', body))
        # setupTabs() names every tab button "tab-<data-tab>"; the palette names them that way too.
        tabs.update(re.findall(r'dataset\.tab = "([^"]+)"', body))
        tabs.update(re.findall(r'data-tab="([^"]+)"', html))
    controls = [entry["control"] for entry in command_table(text_of(APP))]
    assert controls == sorted(set(controls), key=controls.index), "two commands share one control: %s" % controls
    for control in controls:
        assert control, "a command names no control, so its button has no say"
        known = control in markup_ids or control in assigned
        if control.startswith("tab-"):
            known = known or control[len("tab-"):] in tabs
        assert known, "no element or panel assigns the id %r the command table gates on" % control


def test_every_command_is_gated_by_its_own_control() -> None:
    """The reasons are what the reader gets; the control is the authority.

    Every entry's `available` starts with the gate over the control it named, and that gate refuses
    a disabled or hidden button whatever the reasons believe - so the palette can never offer more
    than the UI does, and a command whose reason list is incomplete still cannot become a way around
    a greyed-out button.
    """
    app = text_of(APP)
    for entry in command_table(app):
        match = GATE_RE.match(entry["available"])
        assert match, "%s: available must be gate(...), got %r" % (entry["id"], entry["available"])
        assert match.group(1) == entry["control"], (
            "%s is gated by %r but names %r as its control" % (entry["id"], match.group(1), entry["control"])
        )
    gate = app[app.index("function gate(control, ...reasons) {"):]
    gate = gate[:gate.index("\n}\n")]
    assert "node.disabled || node.hidden" in gate, (
        "the gate no longer reads the control's own disabled/hidden state, so the button lost the last word"
    )
    assert "REASON.notReady" in gate, "the gate has no fallback reason for a control that refuses the action"


def test_the_command_table_carries_no_implementation() -> None:
    """The table is data: no bodies, no requests, no second way to the dataset."""
    registry = registry_text(text_of(APP))
    for forbidden in ("function", "=> {", "api.", "fetch(", "/api/", "await "):
        assert forbidden not in registry, (
            "the command table contains %r; an entry must be a reference to an existing action" % forbidden
        )


def test_the_palette_module_owns_no_action_of_its_own() -> None:
    """palette.js renders a table it is handed. It must not be able to act on its own."""
    body = source("palette.js")
    for forbidden in ("api.", "fetch(", "/api/", "XMLHttpRequest", "openDir(", "state."):
        assert forbidden not in body, "palette.js reaches into the app through %r" % forbidden
    imports = re.findall(r'import\s+[^;]*?from\s+"([^"]+)"', body, re.S)
    assert sorted(imports) == sorted(["./strings.js", "./navscroll.js"]), (
        "palette.js imports %s; it may only take copy and the scroll helper - every action arrives "
        "through the table it is handed" % imports
    )
    for exported in ("export function createCommandPalette(", "export function matchScore(",
                     "export function rankCommands("):
        assert exported in body, "palette.js does not export %s" % exported


# ---------------------------------------------------------------------------
# 2. Unavailable commands are shown, with their reason
# ---------------------------------------------------------------------------


def test_unavailable_commands_are_painted_with_their_reason() -> None:
    """Shown, not hidden - and Enter on one says why instead of doing nothing.

    The two failure modes this pins are opposite and both are lies: a row that vanishes (the user
    cannot find out that the action exists) and a row that looks runnable and silently does nothing.
    """
    body = source("palette.js")
    assert 'item.setAttribute("aria-disabled", "true");' in body, (
        "an unavailable option does not say so to assistive tech"
    )
    assert 'why.className = "palette-why";' in body and "why.textContent = reasonText(row.why);" in body, (
        "the reason is not painted on the row"
    )
    assert 't("palette.unavailable")' in body, "the row does not announce that it is unavailable"
    # The row is built and appended whatever the reason says: nothing filters the list by availability.
    assert "if (row.why) {" in body and "list.append(box);" in body, (
        "the unavailable branch skips painting the row"
    )
    run = body[body.index("  function run() {"):]
    run = run[:run.index("\n  }\n")]
    assert "if (row.why) {" in run and "status.textContent = reasonText(row.why);" in run, (
        "Enter on an unavailable row does nothing, which reads as a broken key"
    )
    assert run.index("close();") > run.index("if (row.why) {"), (
        "the palette closes before checking availability, so a refusal would look like a success"
    )
    assert "row.command.run();" in run, "the palette does not run the command it was handed"


# ---------------------------------------------------------------------------
# 3. The keyboard model, the modal guard and the roles
# ---------------------------------------------------------------------------


def test_the_opening_gesture_is_registered_on_the_document_and_justified() -> None:
    """Ctrl+K, on the document, in the module that owns the palette - and it is never a bare key.

    The gallery owns arrows, Space, z and Ctrl+A while it has focus; a bare letter would fight the
    caption editor's search fields. Ctrl+K is the one combination every editor and code host uses
    for this, and nothing in this app had claimed it.
    """
    body = source("palette.js")
    assert '(key === "k" || key === "K")' in body, "the gesture is not Ctrl+K"
    assert "event.ctrlKey || event.metaKey" in body, "the gesture does not accept Cmd+K on macOS"
    assert "document.addEventListener(\"keydown\", onDocumentKeyDown);" in body, (
        "the gesture must be on the document: the palette has to open from anywhere on the page"
    )
    assert body.index("document.addEventListener(\"keydown\", onDocumentKeyDown);") < body.index("  return {\n    open,"), (
        "the gesture is registered inside open(), so it only exists while the palette is open"
    )


def test_the_palette_refuses_to_open_over_another_dialog() -> None:
    """A modal owns the keyboard while it is open. Opening over it would steal focus out of a
    dialog that claims aria-modal="true", and the two key handlers would fight over Escape.

    The palette itself is not one of those any more, so the guard no longer excepts its own node:
    a branch that can never be true is exactly the kind of criterion this repository throws away.
    If `el !== host` comes back, the shell went back to being a backdrop.
    """
    body = source("palette.js")
    guard = body[body.index("  function anotherDialogOpen() {"):]
    guard = guard[:guard.index("\n  }\n")]
    assert '".dialog-backdrop, .zoom-backdrop"' in guard, "the guard does not look at the dialogs this app builds"
    assert "el.hidden !== true" in guard, "the guard counts a hidden dialog as one that is on screen"
    assert "el !== host" not in guard, (
        "the guard excepts the palette's own node again - the palette is a dropdown with no backdrop "
        "class, so that exception can never fire"
    )
    opened = body[body.index("  function open() {"):]
    opened = opened[:opened.index("\n  }\n")]
    assert "if (anotherDialogOpen()) return false;" in opened, "open() does not consult the guard"


def test_every_modal_in_the_frontend_is_covered_by_the_guard() -> None:
    """The guard is a list of class names, so a modal with a new class would slip past it.

    The palette takes the keyboard away from the page; opening it over a dialog that claims
    aria-modal="true" would steal focus out of that dialog and leave two Escape handlers fighting.
    Every element that *is* a backdrop therefore has to carry one of the two base classes the guard
    knows - picker-backdrop and confirm-backdrop are extra classes on a .dialog-backdrop, which is
    why the check is "contains", not "equals".
    """
    guard = source("palette.js")
    guard = guard[guard.index("  function anotherDialogOpen() {"):]
    guard = guard[:guard.index("\n  }\n")]
    assert '".dialog-backdrop, .zoom-backdrop"' in guard, (
        "the guard's selector changed; this criterion checks the class names it can see"
    )
    bases = {"dialog-backdrop", "zoom-backdrop"}
    #: Where a class actually lands on an element: the markup, the two JS helpers that take a class
    #: string, and a plain className assignment (confirm.js builds its backdrop by hand).
    class_sources = (
        r'class="([^"]*)"',
        r'el\(\s*"[a-z]+"\s*,\s*"([^"]*)"',
        r'className\s*=\s*"([^"]*)"',
        r'createElement\("[a-z]+"\)\s*;\s*\n\s*[\w.]*className\s*=\s*"([^"]*)"',
    )
    offenders = set()
    seen = set()
    for path in sorted(JS.glob("*.js")) + [HTML]:
        body = text_of(path)
        for pattern in class_sources:
            for value in re.findall(pattern, body):
                tokens = [token for token in str(value).split() if "backdrop" in token]
                if not tokens:
                    continue
                # A backdrop may carry extra classes (picker-backdrop, confirm-backdrop); what
                # matters is that the element also carries one the guard knows.
                seen.update(tokens)
                if not any(base in tokens for base in bases):
                    offenders.add("%s: %r" % (path.name, value))
    assert seen, "the scan found no backdrop class at all; it is looking in the wrong place"
    assert not offenders, (
        "these backdrop classes are not covered by the palette's modal guard, so the palette could "
        "open on top of them: %s" % sorted(offenders)
    )


def test_the_panel_is_a_dropdown_and_tab_leaves_it() -> None:
    """A dropdown is not a modal: it does not trap Tab, and it closes when focus leaves.

    Escape closes and puts the keyboard back on the bar - the control the panel belongs to. Tab
    does the same and does NOT swallow the key: the browser then moves focus on from the bar,
    which is what "Tab leaves the panel" looks like from the reader's side. The options are
    never focusable, so there was nothing inside the panel for Tab to reach anyway.
    """
    body = source("palette.js")
    for pin in (
        'if (key === "Escape") {',
        'if (key === "Tab") {',
        'if (key === "ArrowDown") {',
        'if (key === "ArrowUp") {',
        'if (key === "Enter") {',
    ):
        assert pin in body, "the palette's key handling lost %r" % pin
    tab = body[body.index('if (key === "Tab") {'):]
    tab = tab[:tab.index("return;")]
    assert "close();" in tab, "Tab no longer leaves the panel"
    assert "event.preventDefault();" not in tab, (
        "Tab is still swallowed: after the panel closes the browser has to move focus on from the bar"
    )
    assert "cycleFocus" not in body, "the focus trap is back; a dropdown does not own the keyboard"
    assert "restoreFocusTo" not in body, (
        "closing remembers where focus was instead of returning it to the bar it dropped from"
    )
    close_fn = body[body.index("  function close() {"):]
    close_fn = close_fn[:close_fn.index("\n  }\n")]
    assert 'if (trigger && typeof trigger.focus === "function") trigger.focus();' in close_fn, (
        "closing does not put the keyboard back on the command bar"
    )
    outside = body[body.index("  function onDocumentClick(event) {"):]
    outside = outside[:outside.index("\n  }\n")]
    assert "host.contains(target)" in outside and "trigger.contains(target)" in outside, (
        "the outside-click rule does not know what \"inside\" is"
    )
    assert 'document.addEventListener("click", onDocumentClick);' in body, (
        "nothing listens for a click outside the panel"
    )
    # Ctrl+K opens; it never toggles (a gesture that closes would throw away a typed query).
    keys = body[body.index("  function onDocumentKeyDown(event) {"):]
    keys = keys[:keys.index("\n  }\n")]
    gesture = keys[keys.index('(key === "k" || key === "K")'):]
    gesture = gesture[:gesture.index("return;")]
    assert "open();" in gesture and "close();" not in gesture, "Ctrl+K toggles the panel again"
    assert 'input.setAttribute("aria-activedescendant", node.id);' in body, (
        "the active option is not announced"
    )
    assert 'input.removeAttribute("aria-activedescendant");' in body, (
        "a closed or empty panel keeps pointing at an option that is gone"
    )



def test_the_panel_carries_the_roles_a_screen_reader_needs() -> None:
    """The panel is a named region with a combobox over a listbox - and it is NOT a modal.

    `role="dialog"` stays: the panel owns an accessible name and a focusable field, and a screen
    reader should announce that it appeared. `aria-modal` does not: the page behind it is still
    reachable (Tab walks out of the panel), and claiming otherwise is the lie the old shell told.
    """
    html = text_of(HTML)
    assert 'role="dialog"' in html, "the panel is not a named region any more"
    assert 'aria-modal="true"' not in html, (
        "the panel claims to be a modal; it is a dropdown and the page behind it stays reachable"
    )
    assert 'aria-labelledby="palette-title"' in html, "the panel has no accessible name"
    assert 'role="combobox"' in html and 'aria-controls="palette-list"' in html, (
        "the input is not a combobox over the list"
    )
    assert 'role="listbox"' in html, "the list is not a listbox"
    assert 'id="palette-status" class="hint" role="status" aria-live="polite"' in html, (
        "the count / no-match / refusal line is not announced"
    )
    body = source("palette.js")
    assert 'item.setAttribute("role", "option");' in body, "the rows are not options"
    assert 'box.setAttribute("role", "group");' in body, (
        "the rows are not grouped, so a listbox would own non-option children"
    )


def test_the_panel_is_a_dropdown_anchored_inside_the_command_bar() -> None:
    """The shell: no backdrop, no fixed scrim, and the panel lives in the header's own wrapper.

    It has to be anchored to the control it belongs to - a full-screen scrim centred on the page
    was the modal's shape, not this one's - and the wrapper is what lets the panel be an absolute
    child of the header: it paints over the page without taking a row's worth of height, which is
    how the header keeps its 89px with the panel open.
    """
    html = text_of(HTML)
    path = ancestry(html, "palette")
    flat = [token for frame in path for token in frame]
    assert "topbar" in flat, "#palette is not inside the page header: %s" % path
    assert "palette-bar-wrap" in flat, (
        "#palette is not anchored to the command bar's own wrapper: %s" % path
    )
    for forbidden in ("layout", "panel-left", "panel-right", "center-panel", "tab-panels",
                      "breadcrumb", "gallery"):
        assert forbidden not in flat, "#palette sits inside .%s: %s" % (forbidden, path)
    tag = tag_of(html, 'id="palette"')
    assert "dialog-backdrop" not in tag, "the panel is a full-screen backdrop again"
    assert 'class="palette-pop"' in tag, "the panel is not the dropdown shell: %s" % tag
    css = text_of(CSS)
    popover = rule(css, ".palette-pop")
    assert "position: absolute" in popover, "the panel is not positioned against its wrapper"
    assert "position: fixed" not in popover, "the panel is pinned to the viewport, not to the bar"
    assert "right: 0" in popover, "the panel is not right-aligned to the bar"
    assert "z-index: 30" in popover, (
        "the panel lost its place in the stacking order: 30 is the breadcrumb dropdown's number, "
        "inside a header that is already a stacking context at 20 and outside the shelf/toasts at 40/50"
    )
    assert "width: min(560px, 94vw)" in popover, "the panel lost the width the round reused"
    assert "max-height: min(46vh, 420px)" in css, "the list lost its height budget"
    wrapper = rule(css, ".palette-bar-wrap")
    assert "position: relative" in wrapper, "the wrapper cannot anchor an absolute child"



def test_the_command_bar_shows_the_gesture_and_names_it() -> None:
    """A gesture nobody can see is not discoverable - so the bar SHOWS Ctrl+K instead of naming
    the feature, and the name a screen reader announces contains that visible text.

    `palette.openKey` is a KEY NAME, not copy: one string in all three packs, deliberately
    untranslated (the same call strings.js makes for the language endonyms - a translated keycap
    would rename a key the keyboard does not have). WCAG 2.5.3 (label in name) is pinned as the
    pairing it is: `palette.openTitle` must CONTAIN that keycap in every pack, so rewording the
    title or translating the key turns this red instead of quietly breaking the rule.
    """
    html = text_of(HTML)
    path = ancestry(html, "btn-palette")
    flat = [token for frame in path for token in frame]
    assert "topbar" in flat, "#btn-palette is not in the page header: %s" % path
    assert "palette-bar-wrap" in flat, "the bar is not inside its own wrapper: %s" % path
    tag = tag_of(html, 'id="btn-palette"')
    assert 'class="palette-bar"' in tag, "the entry is not bar-shaped: %s" % tag
    assert 'aria-haspopup="dialog"' in tag, "the bar does not say it opens a popup"
    assert 'aria-expanded="false"' in tag, "the bar does not carry the popup state"
    assert 'data-copy-aria="palette.openTitle"' in tag, "the bar's accessible name is not its title"
    assert 'data-copy-title="palette.openTitle"' in tag, "the bar has no tooltip naming the gesture"
    assert 'data-copy="palette.open"' not in html, (
        "the bar still names the feature (palette.open): one idea, one string - it shows the gesture"
    )
    assert '<span class="palette-bar-hint" data-copy="palette.placeholder"' in html, (
        "the bar does not carry the palette's own invitation copy"
    )
    assert '<kbd class="palette-key" data-copy="palette.openKey"' in html, (
        "the bar has no keycap showing the gesture"
    )
    keys = {}
    titles = {}
    for name in ("zh-CN", "en", "ja"):
        body = pack(name)
        key = re.search(r'"palette\.openKey":\s*"([^"]*)"', body)
        title = re.search(r'"palette\.openTitle":\s*"([^"]*)"', body)
        assert key is not None, "%s has no palette.openKey" % name
        assert title is not None, "%s has no palette.openTitle" % name
        keys[name] = key.group(1)
        titles[name] = title.group(1)
        assert key.group(1) in title.group(1), (
            "%s: the accessible name %r does not contain the visible keycap %r (WCAG 2.5.3)"
            % (name, title.group(1), key.group(1))
        )
    assert len(set(keys.values())) == 1, (
        "palette.openKey differs per pack: it is a key name, and a keyboard has one Ctrl+K"
    )
    assert keys["zh-CN"] == "Ctrl+K", "the keycap does not show the gesture the module listens for"
    assert '(key === "k" || key === "K")' in source("palette.js"), (
        "the keycap says Ctrl+K but the module listens for something else"
    )
    assert '$("btn-palette").addEventListener("click", () => { openPalette(); });' in text_of(APP), (
        "the command bar does not open the palette"
    )
    open_fn = text_of(APP)
    open_fn = open_fn[open_fn.index("function openPalette() {"):]
    open_fn = open_fn[:open_fn.index("\n}\n")]
    assert "closeCrumbMenu();" in open_fn, (
        "the breadcrumb's dropdown stays open behind the panel: a click elsewhere closes it, Ctrl+K does not"
    )



# ---------------------------------------------------------------------------
# 5. The panel's typography: a name is a title, and the scale has an order
# ---------------------------------------------------------------------------


def declared_px(body: str, prop: str) -> float:
    """One `prop: Npx` declaration's number, or a failure naming what is missing."""
    found = re.search(r"(?:^|;)\s*%s:\s*(\d+(?:\.\d+)?)px" % re.escape(prop), body)
    assert found is not None, "no %s in px in %r" % (prop, body)
    return float(found.group(1))


def base_font_px(css: str) -> float:
    """The page's base font size, from `html, body { font: 13px/1.5 ... }`."""
    body = rule(css, "html, body")
    found = re.search(r"font:\s*(\d+(?:\.\d+)?)px", body)
    assert found is not None, "the page's base font is not set in px: %r" % body
    return float(found.group(1))


def test_a_dialog_name_is_larger_than_the_text_it_names() -> None:
    """The name of a surface has to outrank its content.

    `.panel-title` is the small label that heads a panel's block - 12px, dim, upper-cased,
    letter-spaced - which is right above a block of fields and wrong for a dialog's name: the rows
    and fields it names are 13px in `var(--text)`, so the title rendered SMALLER and QUIETER than
    the thing it named, and the close button beside it was the loudest thing in the head. One rule
    scoped to `.dialog-head` states the scale for every dialog this app builds (the picker, the
    model directories, the crop dialog, the confirm dialog and this panel).
    """
    css = text_of(CSS)
    title = declared_px(rule(css, ".dialog-head .panel-title"), "font-size")
    base = base_font_px(css)
    assert title > base, (
        "the dialog's name is %gpx against the %gpx body text it names: a title may not be smaller "
        "than its content" % (title, base)
    )


def test_a_dialog_name_is_not_painted_like_a_caption() -> None:
    """Foreground colour, sentence case, no letter-spacing - and the head keeps its margin."""
    head = rule(text_of(CSS), ".dialog-head .panel-title")
    assert "color: var(--text)" in head, "the title is not the foreground colour"
    assert "var(--text-dim)" not in head, "the title is painted dimmer than the rows it names"
    assert "text-transform: none" in head, "the title is still upper-cased like a small label"
    assert "letter-spacing: 0" in head, "the title still carries the caption's letter-spacing"
    assert "margin: 0" in head, "the head's own margin is gone, so every dialog head grew"


def test_the_palette_head_is_a_chrome_row_above_its_own_control() -> None:
    """A rule under the name, tokens only - and only here: every other dialog head is followed by a
    hint paragraph, this one by the field the surface is for."""
    head = rule(text_of(CSS), ".palette-pop .dialog-head")
    assert "border-bottom: 1px solid var(--line)" in head, (
        "the name is not separated from the input and the list under it"
    )
    assert "padding-bottom" in head, "the rule sits on the input"
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(", head), (
        "the head's rule carries a colour literal instead of a token"
    )


def test_the_group_heads_stay_below_the_rows_they_head() -> None:
    """The scale cannot be flattened by inflating the quiet label: 15 > 13 > 12 > 11.

    The group heads are a label, not a heading - the fix for "no hierarchy" is a louder title, not
    a louder list of group names.
    """
    css = text_of(CSS)
    groups = declared_px(rule(css, ".palette-group-head"), "font-size")
    base = base_font_px(css)
    title = declared_px(rule(css, ".dialog-head .panel-title"), "font-size")
    assert groups < base, (
        "the group heads (%gpx) are no longer below the rows (%gpx): inflating them flattens the "
        "scale instead of fixing it" % (groups, base)
    )
    assert groups < title, "the group heads are not below the dialog's name"

# ---------------------------------------------------------------------------


def css_rules(css: str) -> list:
    """Every (selector, body) pair in the stylesheet, comments stripped.

    Split on the closing brace, so a rule written on one line, spread over five, or nested in a media
    query all come back as pairs - the point of reading selectors rather than grepping declarations.
    """
    pairs = []
    for chunk in strip_comments(css).split("}"):
        head, opened, body = chunk.rpartition("{")
        if not opened:
            continue
        pairs.append((head.strip(), body))
    return pairs


def declared_margins(body: str) -> tuple:
    """A rule's declared vertical margins (top, bottom) in px, from the margin shorthand."""
    found = re.search(r"(?:^|;)\s*margin:\s*([^;]+);", body)
    assert found is not None, "no margin shorthand in %r" % body
    values = found.group(1).split()
    top = values[0]
    bottom = values[2] if len(values) > 2 else values[0]
    assert top.endswith("px") and bottom.endswith("px"), "not px margins: %r" % found.group(1)
    return float(top[:-2]), float(bottom[:-2])


def horizontal_padding(body: str) -> float:
    """A rule's left/right padding in px, from the `padding` shorthand."""
    found = re.search(r"(?:^|;)\s*padding:\s*([^;]+);", body)
    assert found is not None, "no padding shorthand in %r" % body
    values = found.group(1).split()
    # 1 value = all sides, 2 = vertical/horizontal, 3 = top/horizontal/bottom, 4 = top/right/bottom/left
    horizontal = values[1] if len(values) > 1 else values[0]
    assert horizontal.endswith("px"), "the horizontal padding is not a px value: %r" % horizontal
    return float(horizontal[:-2])


def test_the_group_head_is_a_band_that_lines_up_with_its_rows() -> None:
    """A rounded strip of `--bg-elev` behind the quiet label - and its text starts where the rows'
    text starts.

    The surface is what makes a group boundary scannable next to 13px rows, and `--bg-elev` is the
    only token it may use: `--text-dim` on `--bg-elev` is one of the pairs `test_web_theme.py`
    already computes and falsifies, while a tint would be an unmeasured claim. The alignment is the
    falsifiable half: the band's horizontal padding IS the row's, so changing either one alone
    turns this red.
    """
    css = text_of(CSS)
    band = rule(css, ".palette-group-head")
    assert "background: var(--bg-elev)" in band, (
        "the band does not carry a token background (a literal would stop following the theme)"
    )
    assert "border-radius: 4px" in band, "the band is not rounded like this app's row controls"
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(", band), (
        "the band carries a colour literal instead of a token"
    )
    declared_top, declared_bottom = declared_margins(band)
    assert declared_top > 0 and declared_bottom > 0, (
        "the band touches the rows above or below it (margin %gpx / %gpx)" % (declared_top, declared_bottom)
    )
    # ONE element, ONE rule - and the SELECTOR is what is counted, not a declaration inside a body.
    # This is the lesson, paid for by hand: an earlier version of this guard looked for a second rule
    # whose BODY carried a "margin" shorthand, and three shapes of the very defect it exists for went
    # straight through it - ".palette-group-head:first-child { margin-top: 0; }" on one line, the same
    # long-hand spread over three lines, and ".palette-group-head:last-child { margin-bottom: 0; }" -
    # because it matched text, and the defect was written as long-hand. Counting the rules that NAME
    # the class is formatting-independent, and it is what this criterion actually claims.
    named = [selector for selector, _body in css_rules(css) if ".palette-group-head" in selector]
    assert named == [".palette-group-head"], (
        "the band is named by %d rule(s): %s. One element, one rule - any second selector (a "
        "pseudo-class, a long-hand declaration, a media query) can silently undo what the rule says, "
        "and the cascade is not something this half can see" % (len(named), named)
    )
    assert horizontal_padding(band) == horizontal_padding(rule(css, ".palette-item")), (
        "the band's text does not start where the rows' text starts (%gpx against %gpx)"
        % (horizontal_padding(band), horizontal_padding(rule(css, ".palette-item")))
    )
    assert horizontal_padding(band) == 8, "the shared horizontal padding moved off 8px"

#: The page the browser half drives. It is written into a scratch COPY of web/ (the frontend must not
#: carry probe code), loads the real index.html in a same-origin iframe, clicks the command bar open
#: and reads every group-head band's COMPUTED margins back out.
BAND_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<style>html,body{margin:0}iframe{display:block;width:1280px;height:900px;border:0}</style></head>
<body><iframe id="app" src="./index.html"></iframe><pre id="result">pending</pre>
<script type="module">
const out = { bands: [], open: false, errors: [] };
const frame = document.getElementById("app");
try {
  await new Promise((resolve) => frame.addEventListener("load", resolve, { once: true }));
  await new Promise((resolve) => setTimeout(resolve, 900));
  const doc = frame.contentDocument;
  const win = frame.contentWindow;
  doc.getElementById("btn-palette").click();
  await new Promise((resolve) => setTimeout(resolve, 300));
  out.open = doc.getElementById("palette").hidden === false;
  out.bands = Array.from(doc.querySelectorAll("#palette-list .palette-group-head")).map((el) => {
    const style = win.getComputedStyle(el);
    return {
      text: el.textContent,
      top: parseFloat(style.marginTop),
      bottom: parseFloat(style.marginBottom),
      height: Math.round(el.getBoundingClientRect().height * 10) / 10,
    };
  });
} catch (err) {
  out.errors.push(String(err));
}
document.getElementById("result").textContent = btoa(unescape(encodeURIComponent(JSON.stringify(out))));
document.title = "probe-done";
</script></body></html>"""

def test_every_band_renders_the_margin_the_stylesheet_declares(tmp_path) -> None:
    """The half a static check cannot do: the CASCADE.

    Counting the rules that name the band (above) is formatting-independent, but it is still a reading
    of the stylesheet - a rule that reaches the element from a selector that never names it (a parent,
    a sibling, a universal child) is invisible to it, and a long-hand declaration added by a second
    rule is exactly how this band's top margin was silently zeroed once (the declared 4px never
    rendered; the measurement is what found it). So this one measures the real page: the app is served
    from a scratch copy of web/, the command bar is clicked, and every band's computed margin-top and
    margin-bottom must equal what app.css declares - and be greater than zero, which is the reason the
    margin exists at all.

    Skipped where no Chromium is installed, like the WD14 integration criteria are skipped where no
    model is: the static half above still runs everywhere.
    """
    band = rule(text_of(CSS), ".palette-group-head")
    declared_top, declared_bottom = declared_margins(band)
    module = probe_tool()
    chrome = module.find_chrome(None)
    if chrome is None:
        pytest.skip("no Chromium on this machine: the static half still pins the single rule")
    web = tmp_path / "web"
    shutil.copytree(WEB, web)
    (web / "band.html").write_text(BAND_PAGE, encoding="utf-8")
    server = module.start_server(web, "stale", (1280, 900), 5, False)
    try:
        dom = module.run_browser(chrome, "http://127.0.0.1:%d/band.html" % server.server_address[1],
                                 tmp_path / "profile", 1280, 900, 20.0)
    finally:
        server.shutdown()
        server.server_close()
    found = re.search(r'<pre id="result">(.*?)</pre>', dom, re.S)
    assert found is not None, "the band page never rendered its result element"
    measured = json.loads(base64.b64decode(found.group(1).strip()).decode("utf-8"))
    assert measured["errors"] == [], "the band page failed: %s" % measured["errors"]
    assert measured["open"] is True, "the command bar did not open the panel"
    bands = measured["bands"]
    assert len(bands) >= 6, "the open panel rendered %d bands, not one per group" % len(bands)
    for index, one in enumerate(bands):
        assert one["top"] == declared_top, (
            "band %d (%r) renders margin-top %gpx while app.css declares %gpx - something in the "
            "cascade is reaching an element that no rule of its own names"
            % (index, one["text"], one["top"], declared_top)
        )
        assert one["bottom"] == declared_bottom, (
            "band %d (%r) renders margin-bottom %gpx while app.css declares %gpx"
            % (index, one["text"], one["bottom"], declared_bottom)
        )
        assert one["top"] > 0 and one["bottom"] > 0, (
            "band %d (%r) touches the rows above or below it (%gpx / %gpx)"
            % (index, one["text"], one["top"], one["bottom"])
        )



def probe_tool():
    """tools/measure_web_layout.py by path: tools/ is a script directory, not a package."""
    assert PROBE.is_file(), "missing %s" % PROBE
    spec = importlib.util.spec_from_file_location("measure_web_layout", PROBE)
    module = importlib.util.module_from_spec(spec)
    sys.modules["measure_web_layout"] = module
    spec.loader.exec_module(module)
    return module

    # The static half cannot see the cascade, and this repository has been burned by exactly that
    # here: the first version of this rule was paired with `.palette-group-head:first-child
    # { margin-top: 0 }`, which matched EVERY band - each one is the first child of its own
    # `.palette-group` box - and flattened the top margin to 0 on all six (the browser measurement
    # caught it; the declared margin looked right). Any other rule that reaches this element is
    # therefore a failure, not a detail.
    for chunk in strip_comments(css).split("}")[:-1]:
        head, _, body = chunk.rpartition("{")
        selector = head.strip()
        if ".palette-group-head" not in selector or selector == ".palette-group-head":
            continue
        assert not re.search(r"(?:^|;)\s*(margin|padding|background|border-radius|font-size)\s*:", body), (
            "another rule (%r) changes how the band is laid out or painted; the cascade can undo "
            "the margin or the alignment this criterion pins" % selector
        )
# 4. The matcher, the copy and the relabel contract
# ---------------------------------------------------------------------------


def test_the_matcher_is_local_and_an_empty_query_lists_everything() -> None:
    """No dependency, and the empty state is the whole vocabulary of the app.

    Split on whitespace, every term must land on the same command, a contiguous hit beats a
    scattered one, and the query is matched against **every** pack's wording for the key - a Chinese
    interface with an English keyboard still finds the command.
    """
    body = source("palette.js")
    assert "hay.indexOf(needle)" in body and "1000 - at" in body, (
        "the contiguous (substring) rule is gone or no longer outranks a scattered match"
    )
    assert "return Math.max(1, 400 - spread);" in body, "the subsequence rule is gone"
    assert 'raw.normalize("NFKC").toLowerCase().trim()' in body, (
        "matching is not folded: full-width Latin and case would both miss"
    )
    rank = body[body.index("export function rankCommands(commands, query) {"):]
    rank = rank[:rank.index("\n}\n")]
    assert 'normalizeQuery(query).split(/\\s+/).filter((term) => term.length > 0)' in rank, (
        "the query is not split into terms (an empty query would match nothing instead of everything)"
    )
    assert "if (best < 0) return;" in rank, "a term that matches nothing does not reject the command"
    assert "for (const locale of LOCALES)" in body, (
        "the matcher reads one locale, so a user typing another language's word finds nothing"
    )
    assert "scored.sort((a, b) => (b.score - a.score) || (a.index - b.index));" in rank, (
        "ties are not broken by the table's own order, so an empty query would not be stable"
    )


def test_every_command_key_exists_in_all_three_packs() -> None:
    """The table names its copy as data, which the contract test cannot see."""
    app = text_of(APP)
    entries = command_table(app)
    keys = [entry["labelKey"] for entry in entries]
    keys += list(GROUP_KEYS) + list(REASON_KEYS) + list(CHROME_KEYS) + list(REUSED_KEYS)
    # The reasons the table builds inline, plus the ones it borrows from a panel.
    keys += re.findall(r'key: "([^"]+)"', registry_text(app) + app[app.index("const REASON = Object.freeze({"):][:2000])
    for name in ("zh-CN", "en", "ja"):
        text = pack(name)
        for key in sorted(set(keys)):
            assert re.search(r'"%s":\s*"[^"]+"' % re.escape(key), text), "%s has no %s" % (name, key)
        for group in set(entry["group"] for entry in entries):
            assert re.search(r'"palette\.group\.%s":\s*"[^"]+"' % group, text), (
                "%s has no label for the %r group" % (name, group)
            )


def test_the_palette_is_repainted_on_a_language_switch() -> None:
    """The rows are render-time copy: without this they stay in the previous language."""
    app = text_of(APP)
    body = app[app.index("async function relabelAfterLocaleChange() {"):]
    body = body[:body.index("\n}\n")]
    assert "palette.relabel();" in body, "relabelAfterLocaleChange() does not repaint the palette"


def test_the_palette_is_told_when_the_set_of_running_jobs_changes() -> None:
    """"A job is already running" is a reason on screen, and a stale reason is a lie.

    The shelf reports a job starting or ending - never a progress tick, which would rebuild the list
    under the reader's cursor once a second.
    """
    jobs = text_of(JOBS)
    assert "const onChange = typeof config.onChange === \"function\" ? config.onChange : null;" in jobs
    assert jobs.count("reportChange();") == 2, (
        "the shelf must report a changed job set from exactly start() and finish() (progress ticks "
        "go through paint() and must not rebuild the palette's list)"
    )
    assert "function reportChange() {" in jobs, "the shelf has no change hook of its own"
    app = text_of(APP)
    assert "onChange: () => { paletteRefresh(); }," in app, "the shelf's change hook is not wired"
    assert "paletteRefresh = () => { palette.refresh(); };" in app, (
        "the palette is never refreshed when a job ends, so its reasons go stale"
    )
    palette = source("palette.js")
    assert "refresh: () => { if (opened) paint(true); }," in palette, (
        "refresh() must repaint from held state and only while the palette is open"
    )


def test_the_palette_behaves_headlessly() -> None:
    """Everything above is static; the keyboard, the reasons and the focus handover really run here."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("no node on this machine")
    assert HARNESS.is_file(), "missing headless behaviour fixture %s" % HARNESS
    done = subprocess.run(
        [node, str(HARNESS), str(WEB)],
        capture_output=True, encoding="utf-8", errors="replace",
    )
    assert done.returncode == 0, "palette behavior criteria failed:\n%s\n%s" % (done.stdout, done.stderr)
    assert "palette harness OK" in done.stdout
