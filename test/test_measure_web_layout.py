"""The layout probe is a tool, not a criterion: it drives headless Chrome and stays manual.

What is checked here needs **no browser at all**, and covers the parts that rot silently:

 1. the module imports, and the two placeholders the handler substitutes into the wrapper page are
    really there (a typo would produce a page that loads and measures nothing);
 2. the canned API answers every request the real page makes at boot, and refuses what it does not
    know instead of guessing;
 3. nothing here can reach a real dataset: the directory the probe pretends to serve is not a path on
    any machine, and requests outside that fake tree are refused;
 4. a machine without a Chromium browser gets a sentence on stderr and exit code 2 - not a traceback -
    and a browser that cannot run gets exit code 1 with the same manners;
 5. the run writes nothing outside the scratch directory it prints.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
TOOL = REPO / "tools" / "measure_web_layout.py"
WEB = REPO / "src" / "kohya_dataset_tagger" / "web"


def load_tool():
    """Import tools/measure_web_layout.py by path: tools/ is a script directory, not a package."""
    assert TOOL.is_file(), "missing %s" % TOOL
    spec = importlib.util.spec_from_file_location("measure_web_layout", TOOL)
    module = importlib.util.module_from_spec(spec)
    sys.modules["measure_web_layout"] = module
    spec.loader.exec_module(module)
    return module


def body_of(answer) -> bytes:
    status, content_type, payload = answer
    assert status == 200, "expected 200, got %s" % status
    assert "json" in content_type, "expected a JSON answer, got %s" % content_type
    return payload


# ---------------------------------------------------------------------------
# 1. The module and the wrapper page
# ---------------------------------------------------------------------------


def test_the_tool_imports_and_exposes_its_halves() -> None:
    module = load_tool()
    for name in ("canned_api", "find_chrome", "run_browser", "parse_probe_result", "main", "start_server"):
        assert callable(getattr(module, name, None)), "measure_web_layout.%s is gone" % name


def test_the_wrapper_page_carries_the_placeholders_the_handler_substitutes() -> None:
    """The handler replaces __SELECTORS__ and __SIZE__ textually; a rename on either side would ship
    a page that loads, measures nothing, and still exits 0."""
    module = load_tool()
    assert "__SELECTORS__" in module.PROBE_PAGE
    assert "__SIZE__" in module.PROBE_PAGE
    assert "./index.html" in module.PROBE_PAGE, "the wrapper no longer loads the real frontend"


def test_the_probe_measures_the_surfaces_this_repository_keeps_moving() -> None:
    module = load_tool()
    for selector in ("#readiness", "#scope-strip", "#caption-dock", "#gallery-filter", ".tab-panels",
                     "#palette", "#palette-list", "#gallery-toolbar", "#theme-dark", "#lang-select",
                     ".breadcrumb-row", "#dir-counts"):
        assert selector in module.SELECTORS, "%s is not measured any more" % selector


def test_the_probe_drives_the_command_palette_the_way_a_user_does() -> None:
    """The palette is keyboard-only, so the probe has to press real keys on the real page.

    This is the only place in the repository where the gesture is measured end to end: the fixtures
    run palette.js over a fake DOM and cannot see whether app.js's own wiring would have answered a
    key at all, and the static criteria read source. Here a real Chrome dispatches a real Ctrl+K on
    the real document, and "the row ran the app's action" is read off the page (the active tab),
    never off the dialog.
    """
    module = load_tool()
    page = module.PROBE_PAGE
    assert 'doc.dispatchEvent(new win.KeyboardEvent("keydown", init));' in page, (
        "the probe no longer sends real key events to the iframe's own document"
    )
    for pin in ('press("k", { ctrlKey: true });', 'press("ArrowDown");', 'press("Escape");',
                'paletteInput.value = "zzzz";', 'paletteInput.dispatchEvent(new win.Event("input", { bubbles: true }));',
                'press("Enter");'):
        assert pin in page, "the probe no longer drives the palette with %s" % pin
    for step in ('"palette:opened"', '"palette:arrows"', '"palette:escape"', '"palette:no-match"',
                 '"palette:query"', '"palette:ran"'):
        assert step in page, "the probe no longer records the %s step" % step
    assert 'doc.querySelectorAll("#palette-list [role=option]")' in page, (
        "the probe does not read the palette's rows, so 'it listed the commands' cannot be measured"
    )
    assert 'attrOf(paletteInput, "aria-activedescendant")' in page, (
        "the probe does not read what the active option is, so the arrow keys are unmeasured"
    )
    # The panel's own shell, read off the panel: role, and the aria-modal it must NOT carry now
    # that it is a dropdown. Reading them off "#palette .dialog" would report null for both.
    assert 'role: attrOf(paletteEl, "role"),' in page, "the probe no longer reads the panel's role"
    assert 'modal: attrOf(paletteEl, "aria-modal"),' in page, (
        "the probe no longer records whether the panel claims to be a modal"
    )
    assert '#palette .dialog' not in page, "the probe still addresses the modal shell that is gone"


def test_the_probe_can_measure_another_pack() -> None:
    """--lang reaches the app through the app's OWN resolution order (?lang=), not a second one.

    Without it, every number in the layout tables is whatever locale the browser reports - zh-CN on
    this machine and nothing in particular anywhere else - so "the en/ja labels are longer, and here
    is where the row wraps" stayed a claim no one could re-run from the committed tool.
    """
    module = load_tool()
    page = module.PROBE_PAGE
    handler = TOOL.read_text(encoding="utf-8")
    assert "__LANG__" in page, "the wrapper has no locale placeholder"
    assert handler.count("__LANG__") >= 2, "__LANG__ is never substituted"
    assert '"--lang"' in handler, "the probe has no --lang flag"
    assert '(SEED_LANG ? "?lang=" + encodeURIComponent(SEED_LANG) : "")' in page, (
        "the locale does not travel through the query string index.html's own initLocale() reads"
    )
    assert page.index("const SEED_LANG = __LANG__;") < page.index("frame.src ="), (
        "the locale is read after the app is mounted, so it would not reach the first render"
    )


# ---------------------------------------------------------------------------
# 2. The canned API is pure, complete and honest about what it does not know
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/health"),
    ("GET", "/api/roots"),
    ("GET", "/api/fs/list"),
    ("GET", "/api/tags/frequency"),
    ("GET", "/api/cache/status"),
    ("GET", "/api/autotag/models"),
    ("GET", "/api/captions"),
    ("GET", "/api/dataset/toml/status"),
    ("GET", "/api/images/meta"),
    ("GET", "/api/images/thumb"),
    ("POST", "/api/captions/read"),
    ("POST", "/api/dataset/toml"),
])
def test_the_canned_api_answers_every_request_the_page_boots_with(method, path) -> None:
    module = load_tool()
    query = {"path": [module.FAKE_ROOT]} if path != "/api/captions/read" else {}
    body = json.dumps({"images": [module.FAKE_ROOT + "/1.png"]}).encode("utf-8") if method == "POST" else b""
    answer = module.canned_api(method, path, query, body, "stale")
    assert answer is not None, "%s %s has no canned answer; the page would get a 404" % (method, path)
    assert answer[0] == 200


def test_the_canned_api_is_pure() -> None:
    module = load_tool()
    first = module.canned_api("GET", "/api/fs/list", {"path": [module.FAKE_ROOT]}, b"", "stale")
    second = module.canned_api("GET", "/api/fs/list", {"path": [module.FAKE_ROOT]}, b"", "stale")
    assert first == second, "the same request answered two different ways: the probe is not repeatable"

    listing = json.loads(body_of(first))
    assert listing["counts"]["missing_captions"] == 2, (
        "the canned listing lost its missing-caption count, so the strip cannot be measured"
    )
    assert listing["path"] == module.FAKE_ROOT


def test_the_canned_root_can_be_the_folder_only_level_a_real_dataset_root_is() -> None:
    """--root-images 0 is how the readiness strip's "nothing to say about captions" case is measured
    in a browser rather than only in a fake DOM: a real dataset root holds no images, so the gallery
    draws folder cards and the strip must stay quiet about captions while still reporting the other
    two things."""
    module = load_tool()
    folder_only = module._listing(module.FAKE_ROOT, 0)
    assert folder_only["images"] == [], "a folder-only root still lists images"
    assert folder_only["counts"]["images"] == 0
    assert folder_only["counts"]["captions"] == 0
    assert folder_only["counts"]["missing_captions"] == 0
    assert len(folder_only["dirs"]) == len(module.FAKE_DIRS) > 0, (
        "a folder-only root is what the cards are drawn from; it must keep its directories"
    )

    answered = json.loads(body_of(module.canned_api(
        "GET", "/api/fs/list", {"path": [module.FAKE_ROOT]}, b"", "stale", 0)))
    assert answered["counts"]["images"] == 0, (
        "canned_api ignores root_images, so --root-images 0 would still serve a root full of images"
    )
    full = json.loads(body_of(module.canned_api("GET", "/api/fs/list", {"path": [module.FAKE_ROOT]}, b"", "stale")))
    assert full["counts"]["images"] == len(module.FAKE_IMAGES), (
        "the default root lost its images, so the tile/tab walk has nothing to click"
    )
    assert full["counts"]["missing_captions"] == 2


def test_the_cache_scenario_is_the_only_thing_that_changes() -> None:
    """--scenario fail is what makes the readiness strip's failure state measurable in a browser."""
    module = load_tool()
    stale = json.loads(body_of(module.canned_api("GET", "/api/cache/status", {}, b"", "stale")))["stale"]
    empty = json.loads(body_of(module.canned_api("GET", "/api/cache/status", {}, b"", "empty")))["stale"]
    failed = module.canned_api("GET", "/api/cache/status", {}, b"", "fail")
    assert len(stale) == len(module.FAKE_STALE) > 0
    assert empty == []
    assert failed[0] == 500, "the fail scenario must answer an error, not an empty list"


def test_the_canned_dataset_toml_status_has_both_states() -> None:
    """§4.16: the readiness strip's third segment needs a canned answer, and both states must be
    reachable - --toml is how "the file is there" is measured in a browser, while the default (no
    file) is what makes the drift measurable: the export panel reports a plan either way, and this
    endpoint is the only one that says whether a file exists."""
    module = load_tool()
    query = {"root": [module.FAKE_ROOT]}
    absent = json.loads(body_of(module.canned_api("GET", "/api/dataset/toml/status", query, b"", "stale")))
    assert absent["exists"] is False, "the default canned root claims a dataset.toml it does not have"
    assert absent["path"] == module.FAKE_ROOT + "/dataset.toml"
    assert absent["mtime"] is None and absent["size"] is None

    present = json.loads(body_of(module.canned_api(
        "GET", "/api/dataset/toml/status", query, b"", "stale", len(module.FAKE_IMAGES), True)))
    assert present["exists"] is True and present["size"] == module.FAKE_TOML_SIZE
    assert present["mtime"] == module.FAKE_TOML_MTIME

    outside = module.canned_api("GET", "/api/dataset/toml/status", {"root": ["C:/data"]}, b"", "stale")
    assert outside is not None and outside[0] == 403, (
        "a real path got a canned dataset.toml status instead of a refusal: %s" % (outside,)
    )
    assert module.canned_api("GET", "/api/dataset/toml/status", query, b"", "stale") == module.canned_api(
        "GET", "/api/dataset/toml/status", query, b"", "stale"
    ), "the canned status answer is not pure"


def test_an_unknown_route_is_refused_rather_than_guessed() -> None:
    module = load_tool()
    assert module.canned_api("GET", "/api/does/not/exist", {}, b"", "stale") is None
    assert module.canned_api("DELETE", "/api/roots", {}, b"", "stale") is None


# ---------------------------------------------------------------------------
# 3. A real dataset directory is not reachable from here
# ---------------------------------------------------------------------------


def test_the_fake_dataset_is_not_a_path_on_any_machine() -> None:
    module = load_tool()
    assert module.FAKE_ROOT.startswith("/") and not Path(module.FAKE_ROOT).exists(), (
        "%s exists on this machine; the probe must pretend to serve a directory that cannot be real"
        % module.FAKE_ROOT
    )
    listing = module._listing(module.FAKE_ROOT)
    for item in listing["images"] + listing["dirs"]:
        assert str(item["path"]).startswith(module.FAKE_ROOT), (
            "the canned listing advertises a path outside the fake root: %s" % item["path"]
        )


def test_a_listing_outside_the_fake_root_is_refused() -> None:
    """The frontend only ever sees the fake root; a real path must not be answered with a fake listing."""
    module = load_tool()
    answer = module.canned_api("GET", "/api/fs/list", {"path": ["C:/Users/someone/dataset"]}, b"", "stale")
    assert answer is not None and answer[0] == 403, (
        "a real path got a canned listing instead of a refusal: %s" % (answer,)
    )


# ---------------------------------------------------------------------------
# 4/5. No browser: a sentence, an exit code, and no stray writes
# ---------------------------------------------------------------------------


def test_no_browser_is_a_message_and_not_a_traceback(monkeypatch, capsys) -> None:
    module = load_tool()
    monkeypatch.setattr(module, "find_chrome", lambda explicit=None: None)
    code = module.main(["--web", str(WEB)])
    captured = capsys.readouterr()
    assert code == 2, "a machine without a browser must not look like a successful run"
    assert "no Chromium browser" in captured.err
    assert "--chrome" in captured.err, "the message does not say how to fix it"
    assert "Traceback" not in captured.err
    assert captured.out.strip() == "", "a failed run printed output a caller could mistake for geometry"


def test_a_browser_that_cannot_run_fails_cleanly_and_writes_only_the_scratch(tmp_path) -> None:
    """sys.executable stands in for a browser that is present but cannot do the job."""
    module = load_tool()
    scratch = tmp_path / "scratch"
    code = module.main([
        "--web", str(WEB),
        "--chrome", sys.executable,
        "--scratch", str(scratch),
        "--timeout", "1",
    ])
    assert code == 1, "a browser that produced no DOM must not exit 0"
    assert scratch.is_dir(), "the scratch directory the run was given was not used"
    outside = sorted(path.name for path in tmp_path.iterdir() if path != scratch)
    assert outside == [], "the run wrote outside its scratch directory: %s" % outside


def test_the_tool_starts_no_server_when_it_cannot_run_a_browser(monkeypatch, capsys) -> None:
    """The probe must not need the app or the backend; without a browser it should not bind anything."""
    module = load_tool()
    started = []
    monkeypatch.setattr(module, "find_chrome", lambda explicit=None: None)
    monkeypatch.setattr(module, "start_server", lambda *args, **kwargs: started.append(args))
    module.main(["--web", str(WEB)])
    capsys.readouterr()
    assert started == [], "the probe started an HTTP server before it knew it could measure anything"


# ---------------------------------------------------------------------------
# 6. The theme passes: the boot path, the toggle, the contrast table and the shots
# ---------------------------------------------------------------------------


def test_the_probe_covers_every_surface_the_theme_round_is_about() -> None:
    """The contrast table is only worth having if it covers the surfaces a theme can break.

    A table that quietly skipped the badges ("they only exist once a caption has loaded") or the
    readiness states ("they depend on the scenario") would report a clean run over half the UI.
    """
    module = load_tool()
    for selector in ("body", ".topbar", ".panel", ".hint", ".btn", ".btn-primary", ".btn-danger",
                     ".readiness-item.is-ok", ".readiness-item.is-todo", ".readiness-item.is-bad",
                     ".chip", ".freq-item", ".tab.is-active", ".toast-error", ".job-row",
                     ".palette-item.is-active", ".zoom-nav",
                     ".badge-missing", ".badge-unknown", ".badge-present"):
        assert selector in module.CONTRAST_SELECTORS, "%s is not in the contrast table any more" % selector
    assert len(module.CONTRAST_SELECTORS) >= 20, "the contrast table shrank to %d rows" % len(module.CONTRAST_SELECTORS)
    assert module.CONTRAST_MIN == 4.5, "the minimum is no longer WCAG AA for normal text"
    # A class that only exists in one app state has to be seeded somewhere real, or the row is blank.
    for selector, host, _tag, _css in module.CONTRAST_SEEDS:
        assert selector in module.CONTRAST_SELECTORS, "%s is seeded but never measured" % selector
        assert host.startswith("#") or host.startswith("."), "a swatch has no real host: %s" % host
    seeded = {seed[0] for seed in module.CONTRAST_SEEDS}
    assert {".badge-unknown", ".toast-error", ".job-row", ".palette-item.is-active",
            ".palette-group-head"} <= seeded, (
        "a surface that is absent from the default page state is no longer seeded: %s" % sorted(seeded)
    )
    # The palette, not just the geometry the other rounds measured.
    for token in ("--bg", "--bg-panel", "--line", "--text", "--accent", "--zoom-fg", "--badge-ring"):
        assert token in module.THEME_TOKENS, "%s is not read back from the page" % token


def test_the_probe_seeds_the_stored_theme_before_the_app_exists() -> None:
    """The dark pass has to measure the BOOT path.

    If the key were written after the iframe was inserted, index.html's boot script would already have
    painted light and the pass would measure the toggle path instead - which is the one thing a
    "no flash" claim must not be based on.
    """
    module = load_tool()
    page = module.PROBE_PAGE
    seed = page.index('window.localStorage.setItem("kdt.theme", SEED_THEME)')
    mount = page.index('document.getElementById("slot").append(frame)')
    assert seed < mount, "the theme is seeded after the app is mounted, so the boot path is not measured"
    assert 'frame.src = "./index.html"' in page, "the wrapper no longer loads the real frontend"
    assert page.index('frame.src = "./index.html"') < mount, "the iframe is appended before it is pointed at the app"
    assert "<iframe" not in page, "the iframe is parsed by the markup again, so the seed comes too late"


def test_the_probe_really_clicks_the_switch() -> None:
    """The toggle pass must click the app's own control, not call the module.

    "The switch applies the theme a reload would" is a claim about the wiring - app.js handing the real
    checkbox to the module - so the probe has to click the element the user clicks, and the claim is
    read off the page afterwards (the attribute and the tokens), never off the click.
    """
    module = load_tool()
    page = module.PROBE_PAGE
    assert 'const themeBox = doc.getElementById("theme-dark");' in page, (
        "the probe does not look up the real switch"
    )
    assert "themeBox.click();" in page, "the probe does not click the switch"
    assert 'THEMES.push(themePass(doc, win, "before"))' in page and 'THEMES.push(themePass(doc, win, "after"))' in page, (
        "the toggle pass does not measure both ends of the click"
    )
    assert 'doc.documentElement.getAttribute("data-theme")' in page, "the probe does not read the attribute"
    assert "style.getPropertyValue(token)" in page, "the probe does not read the computed tokens"
    assert "box.checked === true" in page, "the probe does not read the switch's own state"


def test_the_probe_runs_light_dark_and_the_toggle_only_when_both_are_asked_for() -> None:
    """--theme is what decides how many page loads the run costs, and the comparison needs all three."""
    module = load_tool()
    assert [run[0] for run in module.theme_runs("both")] == ["light", "dark", "toggled"]
    assert [run[0] for run in module.theme_runs("light")] == ["light"]
    assert [run[0] for run in module.theme_runs("dark")] == ["dark"]
    seeds = {run[0]: run[1] for run in module.theme_runs("both")}
    assert seeds == {"light": "light", "dark": "dark", "toggled": "light"}, (
        "the toggle pass must start from light, or 'the click moved it' is not what was measured"
    )
    assert [run[2] for run in module.theme_runs("both")] == [False, False, True], (
        "only the third pass clicks the switch"
    )


def test_the_toggle_claim_cannot_be_true_by_accident() -> None:
    """The comparison between the toggled pass and the dark pass, on synthetic input.

    This is the criterion for a defect this round produced: the first live run reported
    tokens_match_dark=true while every token set was EMPTY, because a page that had died on a
    ReferenceError still produced a well-formed report. Two empty sets are equal, so emptiness has to
    be a failure of its own.
    """
    module = load_tool()
    dark = {"attribute": "dark", "tokens": {"--bg": "#14161a"}}
    toggled = {"clicked": True, "attribute": "dark", "before": {"attribute": "light"}, "tokens": {"--bg": "#14161a"}}
    summary = module.toggle_summary({"dark": dark, "toggled": toggled})
    assert summary["tokens_match_dark"] is True
    assert summary["before"] == "light" and summary["after"] == "dark"

    empty = module.toggle_summary({"dark": dark, "toggled": {"clicked": True, "attribute": "dark", "tokens": {}}})
    assert empty["tokens_match_dark"] is False, "an empty token read counted as a match"

    # The case the live defect actually produced: BOTH sides empty. One empty side against a full one
    # compares unequal on the values alone, so a criterion that stops there is green while the bug is
    # present - which is what the first version of this criterion did.
    both_empty = module.toggle_summary({
        "dark": {"attribute": "dark", "tokens": {}},
        "toggled": {"clicked": True, "attribute": "dark", "tokens": {}},
    })
    assert both_empty["tokens_match_dark"] is False, "two empty token reads counted as a match"

    missing_dark = module.toggle_summary({"toggled": toggled})
    assert missing_dark["tokens_match_dark"] is False, "the toggle matched a dark pass that never ran"

    drifted = module.toggle_summary({
        "dark": dark,
        "toggled": {"clicked": True, "attribute": "dark", "tokens": {"--bg": "#ffffff"}},
    })
    assert drifted["tokens_match_dark"] is False, "a different token value counted as a match"

    assert module.toggle_summary({"light": {"tokens": {}}}) is None, "a run without a toggle makes no claim"


def test_the_probe_blocks_carry_the_state_the_theme_is_read_from() -> None:
    """A theme block without the attribute, the colour-scheme, the tokens and the table is a picture."""
    module = load_tool()
    measured = {"steps": [{"name": "boot"}], "errors": [], "themes": [{
        "name": "theme", "attribute": "dark", "colorScheme": "dark",
        "switch": {"checked": True, "role": "switch"},
        "tokens": {"--bg": "#14161a"}, "contrast": [{"selector": "body", "ratio": 14.9, "ok": True}],
    }]}
    block = module.theme_block("dark", "dark", False, measured)
    for field in ("seed", "clicked", "attribute", "colorScheme", "switch", "tokens", "contrast", "steps"):
        assert field in block, "the theme block has no %s" % field
    assert block["attribute"] == "dark" and block["colorScheme"] == "dark"
    assert block["tokens"]["--bg"] == "#14161a"
    assert block["contrast"] and block["steps"] == [{"name": "boot"}]

    before = {"name": "before", "attribute": "light", "colorScheme": "light", "tokens": {"--bg": "#f7f8fa"}}
    after = {"name": "after", "attribute": "dark", "colorScheme": "dark", "tokens": {"--bg": "#14161a"}}
    toggled = module.theme_block("toggled", "light", True, {"steps": [], "errors": [], "themes": [before, after]})
    assert toggled["before"]["attribute"] == "light", "the state before the click is not kept"
    assert toggled["attribute"] == "dark", "the block does not report the state after the click"


def test_shot_names_both_themes_and_writes_them_where_it_was_asked() -> None:
    """--shot is the picture the numbers describe, one per theme, at the probe's own viewport."""
    module = load_tool()
    source = TOOL.read_text(encoding="utf-8")
    assert "probe-%s.png" in source and '("light", "dark")' in source, (
        "--shot no longer writes probe-light.png and probe-dark.png"
    )
    assert "--screenshot=" in source, "the screenshot run does not ask the browser for a PNG"
    assert 'url = "http://127.0.0.1:%d/__probe__.html?seed=%s&shot=1"' in source, (
        "the screenshot run is not a shot-mode load, so it would capture the end of the walk"
    )
    # The frame is sized ONCE, in the stylesheet, from the probe's own viewport - and by the same
    # numbers in every pass. A shot run used to fill the window instead (100vw/100vh) and restyle the
    # frame after the app existed, so the app could boot against a layout that was not final:
    # app.js's resizer clamps the side panels against the layout it sees and only ever clamps DOWN, so
    # a shot recorded #panel-left 200 / #panel-right 280 - exactly RESIZER_LIMITS.left.min and
    # .right.min - while the JSON of the same run measured 260/380.
    assert '"100vw;height:100vh"' not in source, (
        "the shot page sizes the frame from the window again: the app can boot before the frame is final"
    )
    assert "frame.style" not in module.PROBE_PAGE, (
        "the wrapper restyles the iframe after the app may already have loaded"
    )
    assert '__SIZE__",\n                "%dpx;height:%dpx" % (self.server.viewport[0], self.server.viewport[1]),' in source, (
        "the frame is not sized from the probe's own viewport before index.html loads"
    )
    assert "SHOT_MODE) {" in module.PROBE_PAGE, "the page has no shot mode"
    assert module.run_browser_shot is not None and callable(module.run_browser_shot)


def test_every_placeholder_the_page_carries_is_substituted_by_the_handler() -> None:
    """A placeholder with no substitution ships the literal token into the browser, which then dies on
    a syntax error - exactly how the first live run of the contrast table reported nothing at all."""
    module = load_tool()
    page = module.PROBE_PAGE
    handler = TOOL.read_text(encoding="utf-8")
    for name in ("__SELECTORS__", "__SIZE__", "__CONTRAST__", "__TOKENS__", "__SEEDS__",
                 "__SEED__", "__TOGGLE__", "__SHOT__", "__CONTRAST_MIN__"):
        assert name in page, "%s is not in the wrapper page" % name
        # Once in PROBE_PAGE and at least once in the handler that replaces it: a token with a single
        # occurrence is shipped to the browser verbatim.
        assert handler.count(name) >= 2, "%s is never substituted" % name
