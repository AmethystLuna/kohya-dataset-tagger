"""Frontend i18n: locale-pack contract + engine behavior.

Why this exists
---------------
Copy is centralized in `web/js/locales/*.js` and parsed by the `web/js/strings.js`
engine. A static-text test can show "every pack has the same keys", but it cannot
show the `?lang= > localStorage > navigator` precedence, whether `setLocale()`
really persists and re-renders the static DOM, or whether interpolation still
holds across languages -- only actually running the engine reveals those. The
three layers here:

1. **Wiring**: the top bar has `#lang-select`, boot has `initLocale()`, the switch
   handler is wired to `setLocale`, and every dynamically rendered panel is
   re-labelled in `relabelAfterLocaleChange()`;
2. **Contract**: every `data-copy*` key in `index.html` exists in **every** pack;
3. **Behavior**: a fake DOM + fake localStorage actually runs `strings.js` (see
   fixtures/i18n_harness.mjs).
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
ENGINE = JS / "strings.js"
HTML = WEB / "index.html"
APP = JS / "app.js"
BATCH = JS / "batch.js"
SCALE = JS / "scale.js"
PANEL_HARNESS = REPO / "test" / "fixtures" / "panel_locale_harness.mjs"

DATA_COPY_RE = re.compile(r'''data-copy[a-z-]*\s*=\s*"([^"]+)"''')
STRING_PAIR_RE = re.compile(r'"([^"\\]+)"\s*:\s*"((?:[^"\\]|\\.)*)"')


def text_of(path: Path) -> str:
    assert path.is_file(), "not found: %s" % path
    return path.read_text(encoding="utf-8")


def pack_paths() -> list[Path]:
    return sorted(LOCALES.glob("*.js"))


# ---------------------------------------------------------------------------
# 1. Wiring
# ---------------------------------------------------------------------------


def test_the_language_switcher_exists_and_is_wired() -> None:
    markup = text_of(HTML)
    assert 'id="lang-select"' in markup, "index.html has no language switcher #lang-select"
    assert 'data-copy-aria="lang.label"' in markup, "the switcher has no accessible name (data-copy-aria)"

    app = text_of(APP)
    assert "initLocale()" in app, "boot() does not call initLocale(), so the locale is never resolved"
    assert "setLocale(" in app, "the switch handler does not call setLocale()"
    assert "setupLanguageSelect()" in app, "the language switcher is not wired up"


#: any create...() factory call (capitalized, to avoid colliding with createElement / createDocumentFragment)
FACTORY_CALL_RE = re.compile(r"\bcreate[A-Z]\w*\s*\(")
INIT_LOCALE_CALL_RE = re.compile(r"\binitLocale\s*\(\s*\)")


def _strip_line_comments(source: str) -> str:
    """Comments mention initLocale() / createXxx() too; the ordering criterion looks only at real code."""
    return re.sub(r"//[^\n]*", "", source)


def test_locale_is_resolved_before_any_panel_is_constructed() -> None:
    """Panels are built at module scope and call t() as they are built; the locale must
    be resolved first.

    Measured in a real browser (127.0.0.1:3001/?lang=en): boot() called
    initLocale() only after all panels had been built, leaving 103 unique CJK runs
    in the right-hand panel; applyCopy() only patches data-copy* nodes and
    relabel() only runs on a **switch**, so neither can rescue copy already
    hard-coded at construction time. Asserting that initLocale() exists is not
    enough (it was in boot all along) -- the **ordering** must be pinned down.
    """
    app = _strip_line_comments(text_of(APP))
    calls = list(INIT_LOCALE_CALL_RE.finditer(app))
    assert calls, "app.js does not call initLocale(), so the locale is never resolved"

    factory = FACTORY_CALL_RE.search(app)
    assert factory is not None, "no create...() factory call found in app.js, so this criterion degenerates into an empty assertion"
    assert calls[0].start() < factory.start(), (
        "initLocale() appears after the first panel factory (line %d vs line %d): "
        "when a panel is built at module scope t() takes the default zh-CN, so "
        "construction-time copy stays in Chinese."
        % (app.count("\n", 0, calls[0].start()) + 1, app.count("\n", 0, factory.start()) + 1)
    )
    assert len(calls) == 1, (
        "initLocale() is called %d times: the locale gets resolved twice and the two may disagree" % len(calls)
    )


def test_every_dynamic_panel_is_relabelled_on_a_switch() -> None:
    """Forgetting to register one panel = that block of copy stays in the old language after a switch, silently."""
    app = text_of(APP)
    match = re.search(r"async function relabelAfterLocaleChange\(\)\s*\{(.*?)\n\}", app, re.S)
    assert match is not None, "relabelAfterLocaleChange() not found in app.js"
    body = match.group(1)
    components = (
        "gallery", "tagEditor", "filterPanel", "autotagPanel", "rootsPanel", "modelRootsPanel",
        # These three panels' tab buttons are hard-coded with t() at **construction
        # time**; running onShow() only while the tab is active cannot rescue them,
        # so they must be registered unconditionally (a bug measured 2026-09-19).
        "batchPanel", "exportPanel", "scalePanel",
    )
    for component in components:
        assert "relabelComponent(%s)" % component in body, (
            "on a language switch %s is not re-labelled -- its dynamic copy stays in the old language" % component
        )
    # Unconditional registration: it must not appear only inside the tab === "..." branch.
    unconditional = body.split('const active = document.querySelector')[0]
    for component in ("batchPanel", "exportPanel", "scalePanel"):
        assert "relabelComponent(%s)" % component in unconditional, (
            "%s is only re-labelled while its tab is active; during a language switch it usually is not the active tab" % component
        )


def test_panels_built_at_module_scope_expose_relabel() -> None:
    """For construction-time copy to be re-labelled, the panel must expose relabel(); otherwise it just console.warns."""
    for path, name in ((BATCH, "batchPanel"), (SCALE, "scalePanel")):
        body = text_of(path)
        assert "function relabel(" in body, "%s has no relabel()" % path.name
        assert re.search(r"return\s*\{[^}]*relabel", body, re.S), (
            "%s defines relabel() but does not expose it in its return value (%s falls back to console.warn)" % (path.name, name)
        )
    app = text_of(APP)
    assert "function relabel(" in app, "app.js's export panel has no relabel()"
    # Same shape as the two panels above: the return object is a list of fields, and it grew one
    # (the command palette's own entry point into this panel) - the criterion follows the code
    # rather than pinning one exact formatting of it.
    assert re.search(r"return\s*\{[^}]*relabel", app, re.S), (
        "app.js's export panel does not expose relabel()"
    )


def test_the_engine_resolves_locales_from_the_environment() -> None:
    engine = text_of(ENGINE)
    assert "URLSearchParams" in engine and '"lang"' in engine, "the engine does not read ?lang="
    assert "localStorage" in engine, "the engine does not read localStorage, so the choice cannot persist"
    assert "navigator" in engine and "languages" in engine, "the engine does not read navigator.languages"
    assert "localechange" in engine, "the engine does not dispatch a locale-change event, so dynamic panels are never notified"


# ---------------------------------------------------------------------------
# 2. Contract: every key in index.html must exist in every pack
# ---------------------------------------------------------------------------


def test_every_markup_copy_key_exists_in_every_pack() -> None:
    keys = set(DATA_COPY_RE.findall(text_of(HTML)))
    assert len(keys) >= 30, "only %d data-copy keys parsed out of index.html" % len(keys)
    problems = []
    for path in pack_paths():
        have = set(STRING_PAIR_RE.findall(text_of(path)))
        missing = sorted(keys - {key for key, _ in have})
        if missing:
            problems.append("%s is missing %s" % (path.name, missing))
    assert not problems, "These packs are missing copy used by index.html:\n" + "\n".join(problems)


MEANINGFUL_RE = re.compile(r"[A-Za-z0-9\u4e00-\u9fff\u3040-\u30ff]")


def test_glyph_only_labels_carry_an_accessible_name() -> None:
    """When a button is only a symbol (such as "+"), its accessible name is that symbol -- it must have an aria-label."""
    zh = {key: value for key, value in STRING_PAIR_RE.findall(text_of(LOCALES / "zh-CN.js"))}
    assert zh, "no copy parsed out of the zh-CN pack"
    problems = []
    for tag in re.split(r"(?=<)", text_of(HTML)):
        if not tag.startswith("<"):
            continue
        match = re.search(r'data-copy="([^"]+)"', tag)
        if match is None:
            continue
        value = zh.get(match.group(1))
        if value is None or MEANINGFUL_RE.search(value):
            continue
        if "data-copy-aria=" not in tag:
            element_id = (re.search(r'id="([^"]+)"', tag) or [None, "(no id)"])[1]
            problems.append("%s (%s = %r) has no data-copy-aria" % (element_id, match.group(1), value))
    assert not problems, "Symbol-only buttons are missing an accessible name:\n" + "\n".join(problems)


#: Only one word per concept. Synonyms make people think they are two different things ("target folder" vs the site-wide "directory").
BANNED_SYNONYMS = {
    "zh-CN.js": "文件夹",
    "en.js": "folder",
    "ja.js": "フォルダ",
}


def test_one_word_per_concept() -> None:
    problems = []
    for path in pack_paths():
        banned = BANNED_SYNONYMS.get(path.name)
        if banned is None:
            continue
        for key, value in STRING_PAIR_RE.findall(text_of(path)):
            if banned.lower() in value.lower():
                problems.append("%s %s uses the synonym %r" % (path.name, key, banned))
    assert not problems, "One concept has two words:\n" + "\n".join(problems)


#: A sentence assembled from a fragment plus a hard-coded separator cannot be translated:
#: the separator and the word order belong to the pack, not to the caller. Five call sites
#: did `t("...loadFailed") + ": " + detail`, which renders an ASCII colon in every language
#: (2026-09-19: they were converted to a {message} placeholder).
FRAGMENT_CALL_RE = re.compile(r"""t\(\s*"[^"]+"\s*\)\s*\+\s*["'][:：]""")


def test_copy_is_never_assembled_out_of_translated_fragments() -> None:
    problems = []
    for path in sorted(JS.glob("*.js")):
        for number, line in enumerate(text_of(path).splitlines(), start=1):
            if FRAGMENT_CALL_RE.search(line):
                problems.append("%s:%d %s" % (path.name, number, line.strip()))
    assert not problems, (
        "These lines build one sentence out of a translated fragment plus a fixed separator "
        "(put a {message} placeholder in the pack instead):\n" + "\n".join(problems)
    )


#: Chinese takes full-width punctuation. A half-width mark glued to a Chinese character is a
#: typographic defect - and a half-width colon after 可用 reads like a code comment. Code
#: identifiers, paths and regular expressions are the only places half-width punctuation belongs.
ZH_HALFWIDTH_GLUED_RE = re.compile(r"[\u4e00-\u9fff][,;:!?()]|[,;:!?()][\u4e00-\u9fff]")


def test_zh_copy_keeps_half_width_punctuation_out_of_chinese_text() -> None:
    problems = [
        "%s = %s" % (key, value)
        for key, value in STRING_PAIR_RE.findall(text_of(LOCALES / "zh-CN.js"))
        if ZH_HALFWIDTH_GLUED_RE.search(value)
    ]
    assert not problems, (
        "Half-width punctuation is glued to Chinese text:\n" + "\n".join(problems)
    )


#: One shape for "how far along": the job strings all show {done}/{total}. One of them padded
#: the slash, which makes the same ratio look like a different kind of number.
PROGRESS_PAIR_RE = re.compile(r"\{done\}\s*/\s*\{total\}")


def test_progress_ratios_are_written_without_padding() -> None:
    problems = []
    for path in pack_paths():
        for key, value in STRING_PAIR_RE.findall(text_of(path)):
            match = PROGRESS_PAIR_RE.search(value)
            if match and match.group(0) != "{done}/{total}":
                problems.append("%s %s = %s" % (path.name, key, value))
    assert not problems, (
        "A progress ratio is padded; every {done}/{total} is written tight:\n" + "\n".join(problems)
    )


def test_the_filter_cycle_hint_ends_on_the_state_word() -> None:
    """The tooltip names the state the row label announces. "off" in the hint and "not
    filtered" on the row is two words for one state."""
    problems = []
    for path in pack_paths():
        pairs = {key: value for key, value in STRING_PAIR_RE.findall(text_of(path))}
        state = (pairs.get("filter.stateOff") or "").strip().strip(".。")
        hint = pairs.get("filter.cycleHint") or ""
        if not state or state not in hint:
            problems.append(
                "%s: filter.stateOff = %r is not named by filter.cycleHint = %r" % (path.name, state, hint)
            )
    assert not problems, "The filter cycle hint and the state label disagree:\n" + "\n".join(problems)


def test_en_copy_has_no_parenthesised_plural_placeholders() -> None:
    """English has no "{count} image(s)": write the noun first ("Images with no caption:
    {count}") so one wording is true for every count."""
    problems = [
        "%s = %s" % (key, value)
        for key, value in STRING_PAIR_RE.findall(text_of(LOCALES / "en.js"))
        if "(s)" in value or "(ies)" in value
    ]
    assert not problems, (
        "These English strings use a parenthesised plural:\n" + "\n".join(problems)
    )


#: Our own spec section numbers ("§1.1") are for maintainers: a user reading the Export
#: panel cannot look them up, and the number reads as internal jargon.
SPEC_REF_RE = re.compile(r"§\s*\d")


def test_no_pack_quotes_our_own_spec_sections() -> None:
    problems = []
    for path in pack_paths():
        for key, value in STRING_PAIR_RE.findall(text_of(path)):
            if SPEC_REF_RE.search(value):
                problems.append("%s %s = %s" % (path.name, key, value))
    assert not problems, (
        "User-facing copy cites a spec section (it belongs in the memory files, not on screen):\n"
        + "\n".join(problems)
    )


#: The reader trains LoRA / finetune / DreamBooth models with kohya's scripts every day
#: (ui-copy.md, "Who reads it"). A string past this length is a manual: it explains the domain
#: instead of this run. The ceiling is generous on purpose - it is a runaway guard, not a target,
#: and the longest strings today are the ones carrying a tool-specific constraint
#: (export.alphaMaskHint: zh 152 / en 236 / ja 187).
MAX_COPY_CHARS = {"zh-CN.js": 160, "en.js": 250, "ja.js": 200}


def test_no_string_grows_into_a_manual() -> None:
    problems = []
    for path in pack_paths():
        limit = MAX_COPY_CHARS.get(path.name)
        if limit is None:
            continue
        for key, value in STRING_PAIR_RE.findall(text_of(path)):
            if len(value) > limit:
                problems.append("%s %s = %d chars (limit %d)" % (path.name, key, len(value), limit))
    assert not problems, (
        "These strings explain more than the reader needs:\n" + "\n".join(problems)
    )


def test_message_placeholders_are_always_filled_in() -> None:
    """A key carrying {message} that is called as t("key") renders the literal text
    "{message}" on screen. The scan is over the panel sources, not over the packs."""
    zh = {key: value for key, value in STRING_PAIR_RE.findall(text_of(LOCALES / "zh-CN.js"))}
    needs_message = sorted(key for key, value in zh.items() if "{message}" in value)
    assert len(needs_message) >= 5, "no pack key carries {message}; this criterion is vacuous"
    sources = "\n".join(
        text_of(path) for path in sorted(JS.glob("*.js")) if path.parent.name != "locales"
    )
    problems = [
        key for key in needs_message if re.search(r't\(\s*"%s"\s*\)' % re.escape(key), sources)
    ]
    assert not problems, (
        "These keys take a {message} but are called without one (the placeholder shows verbatim):\n"
        + "\n".join(problems)
    )


#: Message codes already localized; they must not regress to backend Chinese. Add new codes here.
TRANSLATED_MESSAGE_CODES = {
    # export warnings (the frozen codes of §4.9)
    "missing_caption", "escaped_comma", "oversize_image", "alpha_images",
    "too_few_images", "broken_image", "empty_dir", "unreadable_dir", "root_images",
    # skipped[].reason
    "skip.empty_dir", "skip.unreadable_dir",
    # API errors
    "outside_roots", "not_found", "not_a_directory", "paths_unconfigured",
    "remote_path_config_disabled",
    # 2026-09-19: remaining actionable 4xx filled in (fixed template + params)
    "not_a_file", "unsupported_image", "bad_size", "bad_write_mode", "bad_op",
    "unknown_job", "no_images", "unreadable",
    # split of bad_request's fixed template (the free-text half stays as fallback)
    "missing_tag_payload", "empty_image_list", "bad_path",
}


def test_translated_backend_message_codes_do_not_regress() -> None:
    """Message-code translations may grow, but must not be silently dropped in a refactor -- dropping one only falls back to Chinese, silently."""
    zh = {key for key, _ in STRING_PAIR_RE.findall(text_of(LOCALES / "zh-CN.js"))}
    assert zh, "no copy parsed out of the zh-CN pack"
    missing = sorted(code for code in TRANSLATED_MESSAGE_CODES if ("msg." + code) not in zh)
    assert not missing, "These backend message codes lost their translation (the en/ja UI falls back to Chinese): %s" % missing


# ---------------------------------------------------------------------------
# 2b. Built-in model descriptions: the stable key is the model id (p0-spec §4.7)
# ---------------------------------------------------------------------------


def test_every_builtin_model_description_is_localised_in_every_pack() -> None:
    """The model id is a stable key: every built-in model needs a non-empty
    translation in **every** pack.

    Defect example: `registry.BUILTIN`'s `description` was hard-coded Chinese and
    the frontend rendered it verbatim, so the model list and model descriptions in
    the en/ja UI stayed Chinese. The fix is the id key `autotag.model.desc.<id>`,
    with the original `description` kept forever as a fallback. The model list is
    derived from registry to avoid copying the ids here again.
    """
    from kohya_dataset_tagger.autotag import registry

    model_ids = sorted(registry.BUILTIN)
    assert model_ids, "registry.BUILTIN is empty, so this criterion degenerates into an empty assertion"
    problems = []
    for path in pack_paths():
        pairs = {key: value for key, value in STRING_PAIR_RE.findall(text_of(path))}
        missing = [
            model_id for model_id in model_ids
            if not pairs.get("autotag.model.desc.%s" % model_id, "").strip()
        ]
        if missing:
            problems.append("%s missing or empty: %s" % (path.name, missing))
    assert not problems, (
        "These built-in models fall back to a Chinese description (or show empty) in the en/ja UI:\n" + "\n".join(problems)
    )


def test_no_pack_is_scanned_as_empty() -> None:
    """Guards against regex drift turning the test above into an always-true empty assertion."""
    for path in pack_paths():
        pairs = STRING_PAIR_RE.findall(text_of(path))
        assert len(pairs) >= 80, "%s parsed into only %d copy entries" % (path.name, len(pairs))


# ---------------------------------------------------------------------------
# 3. Behavior: actually run the engine (fake DOM, no jsdom)
# ---------------------------------------------------------------------------


def test_locale_resolution_and_switching_behave_headlessly() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed here")
    harness = REPO / "test" / "fixtures" / "i18n_harness.mjs"
    assert harness.is_file(), "missing headless behavior fixture %s" % harness
    done = subprocess.run(
        [node, str(harness), str(WEB)],
        capture_output=True, encoding="utf-8", errors="replace",
    )
    assert done.returncode == 0, "locale-switch behavior criterion failed:\n%s\n%s" % (done.stdout, done.stderr)
    assert "i18n harness OK" in done.stdout


def test_module_scope_panels_are_built_in_the_resolved_locale_headlessly() -> None:
    """Actually run batch.js / scale.js: they are constructed after the locale resolves
    to en, so construction-time copy must be English; after switching to zh-CN,
    relabel() must re-label the tab buttons and section labels back to Chinese, and
    no request may be sent along the way.

    This is the behavior-side criterion for the "construction order" defect: the
    static one pins the position of initLocale() in app.js, and this one pins that
    a panel's construction-time copy really comes from the current locale and can
    be re-labelled.
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed here")
    assert PANEL_HARNESS.is_file(), "missing headless behavior fixture %s" % PANEL_HARNESS
    done = subprocess.run(
        [node, str(PANEL_HARNESS), str(WEB)],
        capture_output=True, encoding="utf-8", errors="replace",
    )
    assert done.returncode == 0, "panel construction-time locale behavior criterion failed:\n%s\n%s" % (done.stdout, done.stderr)
    assert "panel locale harness OK" in done.stdout
