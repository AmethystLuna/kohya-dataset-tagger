"""Frontend static contract tests -- without starting a browser, directly assert
that web/ has not drifted from p0-spec §4.

Why static
----------
The frontend and backend are implemented by two parallel workstreams that share no
code. Three things really need to be guaranteed mechanically:

 1. **Endpoints must not drift**: every `METHOD /api/...` appearing in api.js must
    exist verbatim in the frozen route table of p0-spec.md §4. The reverse
    direction (routes the frontend does not use) is reported only, not failed.
 2. **Copy must be centralized**: no CJK literal is allowed outside strings.js,
    otherwise the claim "copy is centralized" cannot be verified, and the Chinese
    in templates and the Chinese in JS would drift apart.
 3. **No build, no external dependencies**: every import under web/ must be a
    relative path, and every script in index.html is `type="module"` - with exactly
    one deliberate exception, the pre-paint theme boot (see CLASSIC_SCRIPT below).

A few more are executable criteria derived directly from the hard constraints
(the performance requirement comes from decision #4):

 4. Only api.js may contain the `/api/` literal (the single source of truth for
    endpoint definitions).
 5. The gallery uses only `THUMB_SIZE.GRID`, thumbnail concurrency is capped at
    ≤ 8, and img carries the lazy marker -- making "the gallery must never load
    original images under any circumstance" an assertable rule.
 6. data-copy attributes and `t("...")` literals must both be findable in
    strings.js; a dynamically concatenated key prefix
    (`t("autotag.category." + key)`) must have at least one matching key too.

These tests all read files only; they do not touch the dataset and do not write
to disk.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WEB_ROOT = REPO_ROOT / "src" / "kohya_dataset_tagger" / "web"
JS_ROOT = WEB_ROOT / "js"
SPEC_PATH = REPO_ROOT / ".github" / "memory" / "p0-spec.md"
API_PATH = JS_ROOT / "api.js"

#: The i18n engine. It must stay copy-free: locale resolution and lookup only.
ENGINE_PATH = JS_ROOT / "strings.js"
#: One copy pack per language. zh-CN is the source of truth for every assertion
#: about the Chinese wording, so "strings.js" below always means that pack.
LOCALES_DIR = JS_ROOT / "locales"
STRINGS_PATH = LOCALES_DIR / "zh-CN.js"

#: one row of the §4 route table: | GET | `/health` | ... |
SPEC_ROUTE_RE = re.compile(r"^\|\s*(GET|PUT|POST|DELETE|PATCH)\s*\|\s*`([^`]+)`\s*\|", re.M)
#: the §4 body range (subsections from §4.1 on also freeze endpoints, e.g. §4.4's POST /api/autotag/cancel)
SPEC_SECTION_RE = re.compile(r"^## 4\..*?(?=^## 5\.)", re.M | re.S)
#: an endpoint written in backticks in the body: `POST /api/autotag/cancel`
SPEC_INLINE_ROUTE_RE = re.compile(r"`(GET|PUT|POST|DELETE|PATCH)\s+(/api/[A-Za-z0-9_\-/]*)`")
#: the "METHOD /api/path" literal in api.js
API_ROUTE_RE = re.compile(r"""["'](GET|PUT|POST|DELETE|PATCH)\s+(/api[^"']*)["']""")
#: CJK and full-width punctuation; any occurrence outside strings.js fails
CJK_RE = re.compile("[\u2e80-\u9fff\uf900-\ufaff\ufe30-\ufe4f\uff00-\uffef]")
IMPORT_RE = re.compile(r"""import\s+(?:[^;'"]*?\s+from\s+)?["']([^"']+)["']""")
SCRIPT_TAG_RE = re.compile(r"<script\b([^>]*)>", re.I)
SCRIPT_SRC_RE = re.compile(r"""src\s*=\s*["']([^"']+)["']""", re.I)
DATA_COPY_RE = re.compile(r'''data-copy[a-z-]*\s*=\s*"([^"]+)"''')
T_LITERAL_RE = re.compile(r'''\bt\(\s*"([^"]+)"\s*[),]''')
T_PREFIX_RE = re.compile(r"""\bt\(\s*"([a-z][a-z0-9_.]*\.)"\s*\+""")
STRING_KEY_RE = re.compile(r"""^\s*"([^"\\]+)"\s*:""", re.M)
#: one "key": "value" entry in a locale pack (value may contain escaped quotes)
STRING_PAIR_RE = re.compile(r'"([^"\\]+)"\s*:\s*"((?:[^"\\]|\\.)*)"')
#: {placeholder} names, which must match verbatim across languages
PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")
EXPORT_CONST_RE = re.compile(r"export\s+const\s+(\w+)\s*=\s*(?:\(\s*)?(\d+)")

#: traces of forbidden external dependencies
FORBIDDEN_TOKENS = ("node_modules", "unpkg.com", "jsdelivr", "cdnjs", "cdn.jsdelivr")


def web_files(suffixes: tuple[str, ...] | None = None) -> list[Path]:
    assert WEB_ROOT.is_dir(), "frontend directory does not exist: %s" % WEB_ROOT
    files = [path for path in sorted(WEB_ROOT.rglob("*")) if path.is_file()]
    if suffixes is not None:
        files = [path for path in files if path.suffix.lower() in suffixes]
    return files


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def spec_routes() -> set[tuple[str, str]]:
    """The endpoints frozen by §4 -> {(METHOD, /api/path)}.

    Two sources: §4's route table, and the endpoints written in backticks in the
    body of each subsection from §4.1 on (§4.4's `POST /api/autotag/cancel`
    appears only in the body, not in the table). The body scan is limited to
    between §4 and §5, so that text elsewhere is not mistaken for the contract.
    """
    assert SPEC_PATH.is_file(), "spec file not found: %s" % SPEC_PATH
    text = read(SPEC_PATH)
    routes = set()
    for method, path in SPEC_ROUTE_RE.findall(text):
        routes.add((method.upper(), "/api" + path))
    section = SPEC_SECTION_RE.search(text)
    if section is not None:
        for method, path in SPEC_INLINE_ROUTE_RE.findall(section.group(0)):
            routes.add((method.upper(), path))
    return routes


def api_routes() -> set[tuple[str, str]]:
    """What api.js declares -> {(METHOD, /api/path)}."""
    return {(method.upper(), path.rstrip("?")) for method, path in API_ROUTE_RE.findall(read(API_PATH))}


def strings_keys() -> set[str]:
    return set(STRING_KEY_RE.findall(read(STRINGS_PATH)))


# ---------------------------------------------------------------------------
# 1. Endpoints: the forward direction must be a strict subset; the reverse only reports
# ---------------------------------------------------------------------------


def test_spec_route_table_parses_into_a_non_trivial_set() -> None:
    """Prevents a failed route-table parse from turning the subset assertions below into always-true empty assertions."""
    routes = spec_routes()
    assert len(routes) >= 17, "only %d endpoints parsed out of §4; the parse is probably broken" % len(routes)
    assert ("GET", "/api/health") in routes
    assert ("PUT", "/api/captions") in routes
    assert ("GET", "/api/autotag/stream") in routes
    # §4.4: the cancel endpoint is only written in the body; it must be parsed in too, otherwise the frontend calling it is judged as drift
    assert ("POST", "/api/autotag/cancel") in routes


def test_every_frontend_route_exists_in_the_frozen_contract() -> None:
    front = api_routes()
    spec = spec_routes()
    unknown = sorted(front - spec)
    assert not unknown, (
        "api.js uses endpoints absent from the §4 route table (contract drift): %s\n"
        "If this is intentional, p0-spec.md §4 must be changed first and the acceptance criteria synced." % unknown
    )
    assert front, "no routes parsed out of api.js"


def test_route_table_entries_unused_by_the_frontend(capsys) -> None:
    """Reverse check: report endpoints the frontend does not use. **Not a failure**, just visibility."""
    unused = sorted(spec_routes() - api_routes())
    with capsys.disabled():
        print("[web-contract] §4 route-table endpoints unused by the frontend: %s" % (unused or "none"))
    assert True


# ---------------------------------------------------------------------------
# 2. Copy is centralized in strings.js
# ---------------------------------------------------------------------------


def test_no_cjk_literals_outside_the_locale_packs() -> None:
    """Copy may live only in web/js/locales/*.js -- engine, panels, and templates must not smuggle any."""
    offenders: list[str] = []
    for path in web_files():
        if LOCALES_DIR in path.parents:
            continue
        for number, line in enumerate(read(path).splitlines(), start=1):
            found = CJK_RE.search(line)
            if found:
                offenders.append("%s:%d: %r" % (rel(path), number, found.group(0)))
    assert not offenders, (
        "CJK literals appear outside web/js/locales/; UI copy must be centralized in the locale packs:\n" + "\n".join(offenders)
    )


def test_the_engine_carries_no_copy() -> None:
    """The engine has neither copy nor Chinese: a missing locale pack must be loud, not a silent fallback to some language."""
    assert ENGINE_PATH.is_file(), "i18n engine not found: %s" % ENGINE_PATH
    assert not CJK_RE.search(read(ENGINE_PATH)), "Chinese copy appears in strings.js (the engine)"


def test_locale_packs_are_non_trivial() -> None:
    """The locales directory really must have multiple packs, and the source pack must not be empty."""
    packs = sorted(LOCALES_DIR.glob("*.js"))
    assert len(packs) >= 3, "only %d locale packs: %s" % (len(packs), [p.name for p in packs])
    assert CJK_RE.search(read(STRINGS_PATH)), "the zh-CN pack has no Chinese; the copy was probably moved elsewhere"
    assert len(strings_keys()) >= 80, "the zh-CN pack has only %d keys" % len(strings_keys())


def _pack_pairs(path: Path) -> dict[str, str]:
    return {key: value for key, value in STRING_PAIR_RE.findall(read(path))}


def test_every_locale_pack_has_the_same_keys() -> None:
    """One missing key means that piece of UI copy vanishes in some language; one extra is dead copy nobody uses."""
    reference = set(_pack_pairs(STRINGS_PATH))
    assert reference, "no keys parsed out of the zh-CN pack; the regex or the format drifted"
    for path in sorted(LOCALES_DIR.glob("*.js")):
        keys = set(_pack_pairs(path))
        missing = sorted(reference - keys)
        extra = sorted(keys - reference)
        assert not missing and not extra, (
            "%s's key set differs from zh-CN: missing %s, extra %s" % (rel(path), missing, extra)
        )


def test_placeholders_match_across_locale_packs() -> None:
    """{n} translated into {count}, or lost entirely, leaves a literal in the interpolation silently -- it must be caught mechanically."""
    reference = {key: sorted(PLACEHOLDER_RE.findall(value))
                 for key, value in _pack_pairs(STRINGS_PATH).items()}
    problems = []
    for path in sorted(LOCALES_DIR.glob("*.js")):
        for key, value in _pack_pairs(path).items():
            got = sorted(PLACEHOLDER_RE.findall(value))
            if got != reference.get(key):
                problems.append("%s %s: expected %s, got %s" % (rel(path), key, reference.get(key), got))
    assert not problems, "Placeholders differ across languages:\n" + "\n".join(problems)


def test_no_locale_value_is_empty() -> None:
    problems = []
    for path in sorted(LOCALES_DIR.glob("*.js")):
        for key, value in _pack_pairs(path).items():
            if not value.strip():
                problems.append("%s: %s" % (rel(path), key))
    assert not problems, "Empty copy entries (they render as blank controls):\n" + "\n".join(problems)


def test_copy_keys_used_by_markup_and_code_exist() -> None:
    keys = strings_keys()
    missing: list[str] = []

    for path in web_files():
        text = read(path)
        if path.name == "index.html":
            used = DATA_COPY_RE.findall(text)
        else:
            # The engine and the packs hold no t("...") call sites: the engine is
            # comment-only in that respect, and the packs are pure data.
            if path.resolve() in (STRINGS_PATH.resolve(), ENGINE_PATH.resolve()):
                continue
            used = T_LITERAL_RE.findall(text)
        for key in used:
            if key not in keys:
                missing.append("%s -> %s" % (rel(path), key))

        for prefix in T_PREFIX_RE.findall(text):
            if not any(key.startswith(prefix) for key in keys):
                missing.append("%s -> prefix %s*" % (rel(path), prefix))

    assert not missing, "These UI copy keys do not exist in strings.js:\n" + "\n".join(sorted(set(missing)))


# ---------------------------------------------------------------------------
# 3. No build, no third-party dependencies
# ---------------------------------------------------------------------------


def test_no_external_or_bare_module_imports() -> None:
    problems: list[str] = []
    for path in web_files():
        text = read(path)
        for specifier in IMPORT_RE.findall(text):
            if not (specifier.startswith("./") or specifier.startswith("../")):
                problems.append("%s imports %r (bare specifier = npm package)" % (rel(path), specifier))
            if "://" in specifier or specifier.startswith("//"):
                problems.append("%s imports %r (remote module)" % (rel(path), specifier))
        if path.name == "index.html":
            for tag in SCRIPT_TAG_RE.findall(text):
                for specifier in SCRIPT_SRC_RE.findall(tag):
                    if "://" in specifier or specifier.startswith("//"):
                        problems.append("%s loads remote script %r" % (rel(path), specifier))
        for token in FORBIDDEN_TOKENS:
            if token in text:
                problems.append("%s mentions forbidden token %r" % (rel(path), token))
    assert not problems, "The frontend pulls in external dependencies:\n" + "\n".join(problems)


#: The one deliberate exception to "every script is type=module": the theme boot.
#:
#: It has to run BEFORE the first paint, and a deferred module - which is what type="module" means -
#: runs after it: measured in headless Chrome over this page's real module graph (27 modules), a
#: deferred module evaluated at 275 ms cold / 50 ms warm while first paint happened at 116 ms cold /
#: 36 ms warm, and a synchronous <head> script ran at 87 ms / 13 ms, always before the paint. So a
#: module cannot apply a stored theme before the page is painted, and a user who chose dark would get
#: a light flash on every load. It cannot import js/theme.js either - importing is what would make it
#: a module, which is the too-late thing - so it carries a second copy of the storage key, and
#: test_web_theme.py has the criterion that makes the two copies impossible to drift.
#:
#: Everything else stays a module: a second classic script, a different file name, or a move into
#: <body> all turn this criterion red.
#: See .github/memory/changelog/light-mode.md.
CLASSIC_SCRIPT = "./js/theme-boot.js"


def test_index_html_uses_module_scripts_only() -> None:
    """Every script is a module, except the one pre-paint theme boot - which must be the boot.

    The exception is narrow on purpose. The boot's whole value is paint ORDER, so "in <head>" is part
    of what is asserted, not a detail of the markup: moved into <body> it would be just another
    deferred script that happens to be named like a boot.
    """
    text = read(WEB_ROOT / "index.html")
    tags = SCRIPT_TAG_RE.findall(text)
    assert tags, "no script tags in index.html"
    classic = []
    for attrs in tags:
        assert SCRIPT_SRC_RE.search(attrs), "index.html should use external modules only, no inline scripts: <%s>" % attrs.strip()
        if not re.search(r"""type\s*=\s*["']module["']""", attrs):
            classic.append(attrs)
    assert len(classic) == 1, (
        "index.html has %d scripts that are not type=\"module\"; the pre-paint theme boot is the only "
        "one allowed: %s" % (len(classic), [attrs.strip() for attrs in classic])
    )
    source = SCRIPT_SRC_RE.search(classic[0]).group(1)
    assert source == CLASSIC_SCRIPT, (
        "the one non-module script is not the theme boot: <%s>. If a classic script is wanted for "
        "another reason, the reason goes in the changelog and this constant changes with it."
        % classic[0].strip()
    )
    tag = "<script" + classic[0] + ">"
    assert tag in text, "the theme boot's tag was parsed but not found verbatim: <%s>" % tag
    assert text.index(tag) < text.index("</head>"), (
        "the theme boot is not in <head>: out of it the script is deferred, which is the light flash "
        "on every load that it exists to remove"
    )


def test_import_graph_is_closed_and_reachable_from_index_html() -> None:
    """index.html -> app.js -> ... must cover every module under js/ (no floating modules)."""
    entry_srcs = SCRIPT_SRC_RE.findall(
        " ".join(SCRIPT_TAG_RE.findall(read(WEB_ROOT / "index.html")))
    )
    assert entry_srcs, "index.html references no script"
    reachable: set[Path] = set()
    pending = [(WEB_ROOT / "index.html").parent / src for src in entry_srcs]
    while pending:
        path = pending.pop().resolve()
        assert path.is_file(), "references a module that does not exist: %s" % path
        if path in reachable:
            continue
        reachable.add(path)
        if path.suffix.lower() != ".js":
            continue
        for specifier in IMPORT_RE.findall(read(path)):
            pending.append(path.parent / specifier)
    all_js = {path.resolve() for path in web_files((".js",))}
    orphans = sorted(rel(path) for path in all_js - reachable)
    assert not orphans, "modules under js/ are not referenced: %s" % orphans


# ---------------------------------------------------------------------------
# 4. Performance hard constraints: endpoints centralized + gallery consumes only grid thumbnails
# ---------------------------------------------------------------------------


def test_api_prefix_lives_only_in_api_js() -> None:
    """Endpoints must be assembled only in api.js -- writing a path in a comment does not count; assembling it in a string literal does."""
    offenders = []
    for path in web_files():
        if path.resolve() == API_PATH.resolve():
            continue
        if re.search(r'''["'`]/api/''', read(path)):
            offenders.append(rel(path))
    assert not offenders, "Only api.js may construct the /api/ literal; these files assemble endpoints too: %s" % offenders


def test_gallery_only_uses_grid_thumbnails_with_bounded_concurrency() -> None:
    text = read(JS_ROOT / "gallery.js")
    assert "THUMB_SIZE.GRID" in text, "the gallery must take thumbnails with THUMB_SIZE.GRID"
    assert "THUMB_SIZE.PREVIEW" not in text, "the gallery must not request the preview size"
    assert ".jpeg" not in text and ".png" not in text, "original-image extensions must not appear in the gallery (= would load originals)"
    assert re.search(r'loading\s*=\s*"lazy"', text), "thumbnail img must carry loading=\"lazy\""
    match = EXPORT_CONST_RE.search(text)
    assert match, "gallery.js does not export a concurrency-cap constant"
    name, value = match.group(1), int(match.group(2))
    assert name == "THUMB_CONCURRENCY"
    assert 1 <= value <= 8, "thumbnail concurrency cap %d is out of range (avoid opening hundreds of connections)" % value


def test_gallery_clears_src_when_switching_directories() -> None:
    """Decision #4: switching directories must clear the old img src, avoiding a pile-up of decoded bitmaps."""
    text = read(JS_ROOT / "gallery.js")
    assert 'removeAttribute("src")' in text, "the old img src must be cleared when switching directories"


def test_only_api_js_performs_network_calls() -> None:
    offenders = []
    for path in web_files((".js",)):
        if path.resolve() == API_PATH.resolve():
            continue
        text = read(path)
        if "fetch(" in text or "XMLHttpRequest" in text:
            offenders.append(rel(path))
    assert not offenders, "Network calls must be centralized in api.js: %s" % offenders
    assert "fetch(" in read(API_PATH), "there is no fetch in api.js; the test probably scanned the wrong file"

# ---------------------------------------------------------------------------
# 5. Wiring completeness: ids looked up in JS must exist in index.html
# ---------------------------------------------------------------------------

ID_ATTR_RE = re.compile(r'''\bid\s*=\s*"([^"]+)"''')
ID_LOOKUP_RE = re.compile(r"""\$\(\s*"([^"]+)"\s*\)|getElementById\(\s*"([^"]+)"\s*\)""")
DATA_TAB_RE = re.compile(r'''data-tab="([^"]+)"''')
DATA_PANEL_RE = re.compile(r'''data-panel="([^"]+)"''')


def test_every_element_id_used_by_js_exists_in_index_html() -> None:
    """A typo in $("foo") is a silent runtime failure; the static stage must block it."""
    markup = read(WEB_ROOT / "index.html")
    declared = set(ID_ATTR_RE.findall(markup))
    missing: list[str] = []
    for path in web_files((".js",)):
        for match in ID_LOOKUP_RE.findall(read(path)):
            element_id = match[0] or match[1]
            if element_id not in declared:
                missing.append("%s -> #%s" % (rel(path), element_id))
    assert not missing, "JS references element ids that do not exist in index.html:\n" + "\n".join(sorted(set(missing)))


def test_every_tab_button_has_a_panel() -> None:
    markup = read(WEB_ROOT / "index.html")
    tabs = set(DATA_TAB_RE.findall(markup))
    panels = set(DATA_PANEL_RE.findall(markup))
    assert tabs, "no tab buttons in index.html"
    assert tabs == panels, "tabs and panels do not match: tabs=%s panels=%s" % (sorted(tabs), sorted(panels))

# ---------------------------------------------------------------------------
# 6. Frozen §4.1 / §4.3 / §4.4 items: threshold defaults, end signal, real cancel
# ---------------------------------------------------------------------------

#: `{ key: "general", default: 0.35 }` (OPTIONS carries stringKey too, so it does not false-match)
CATEGORY_DEFAULT_RE = re.compile(r'\{\s*key:\s*"(\w+)",\s*default:\s*([0-9.]+)\s*\}')
REMOVE_UNDERSCORE_RE = re.compile(r'key:\s*"remove_underscore".*?default:\s*true', re.S)


def test_threshold_defaults_match_sd_scripts() -> None:
    """§4.1: all five category thresholds default to 0.35 (= sd-scripts --thresh)."""
    text = read(JS_ROOT / "autotag.js")
    defaults = dict(CATEGORY_DEFAULT_RE.findall(text))
    for key in ("general", "character", "copyright", "artist", "meta"):
        assert key in defaults, "autotag.js is missing the default threshold for %s" % key
        assert defaults[key] == "0.35", "%s's default threshold is %s; §4.1 freezes it at 0.35" % (key, defaults[key])
    assert REMOVE_UNDERSCORE_RE.search(text), "remove_underscore must default to true (§4.1)"


def test_sse_finished_flag_and_stream_endpoint_are_handled() -> None:
    """§4.3: completion must rely on the finished:true event, not merely done >= total."""
    text = read(JS_ROOT / "autotag.js")
    assert "payload.finished === true" in text, "the finished:true end event is not recognized"
    assert '"progress"' in text and '"done"' in text, "the progress / done event names are not listened for"


def test_cancel_button_really_calls_the_frozen_endpoint() -> None:
    """§4.4: the button must really POST /api/autotag/cancel, not merely close the EventSource."""
    autotag_text = read(JS_ROOT / "autotag.js")
    assert ("POST", "/api/autotag/cancel") in api_routes(), "api.js does not declare the cancel endpoint"
    assert "api.autotagCancel(" in autotag_text, "autotag.js does not call the cancel endpoint"
    assert 'cancelButton.addEventListener("click", cancelRun)' in autotag_text
    assert "async function cancelRun(" in autotag_text
    # The old copy ("the server task may still be running") must have been deleted, otherwise the UI is lying
    assert "autotag.cancelNote" not in strings_keys()
    assert "autotag.cancelRequested" in strings_keys()
# ---------------------------------------------------------------------------
# 7. The postprocess body frozen by §4.1: every key must be producible by the panel
# ---------------------------------------------------------------------------

POSTPROCESS_BLOCK_RE = re.compile(r'"postprocess":\s*\{(.*?)\}', re.S)
JSON_KEY_RE = re.compile(r'"(\w+)":')
OPTIONS_BLOCK_RE = re.compile(r"const OPTIONS = \[(.*?)\n\];", re.S)
OPTION_OBJECT_RE = re.compile(r"\{[^{}]*\}", re.S)
STRING_KEY_REF_RE = re.compile(r'(?:stringKey|hintKey):\s*"([\w.]+)"')


def frozen_postprocess_keys() -> set[str]:
    """The set of postprocess keys in the frozen §4.1 body."""
    match = POSTPROCESS_BLOCK_RE.search(read(SPEC_PATH))
    assert match is not None, "no frozen postprocess body found in §4.1"
    return set(JSON_KEY_RE.findall(match.group(1)))


def option_entries() -> dict[str, dict[str, str]]:
    """autotag.js's OPTIONS array -> {key: {stringKey, hintKey, default}}."""
    block = OPTIONS_BLOCK_RE.search(read(JS_ROOT / "autotag.js"))
    assert block is not None, "no OPTIONS array found in autotag.js"
    entries: dict[str, dict[str, str]] = {}
    for chunk in OPTION_OBJECT_RE.findall(block.group(1)):
        key = re.search(r'key:\s*"(\w+)"', chunk)
        if key is None:
            continue
        default = re.search(r"default:\s*(true|false)", chunk)
        string_key = re.search(r'stringKey:\s*"([\w.]+)"', chunk)
        hint_key = re.search(r'hintKey:\s*"([\w.]+)"', chunk)
        entries[key.group(1)] = {
            "default": default.group(1) if default else "",
            "stringKey": string_key.group(1) if string_key else "",
            "hintKey": hint_key.group(1) if hint_key else "",
        }
    return entries


def test_frozen_postprocess_keys_are_exposed_with_frozen_defaults() -> None:
    """§4.1: the panel must be able to produce every postprocess key in the frozen body, and the defaults must agree."""
    frozen = frozen_postprocess_keys()
    assert "escape_parens" in frozen, "the frozen §4.1 body has no escape_parens; the spec was probably changed again"
    options = option_entries()
    text = read(JS_ROOT / "autotag.js")
    for key in sorted(frozen):
        if key in options:
            continue
        # Non-checkbox keys (undesired_tags / always_first_tags / rating) are
        # produced in collectPostprocess() as out.<key> = ..., not as a literal
        pattern = r'(?:"%s"|\'%s\'|\.%s\b)' % (key, key, key)
        assert re.search(pattern, text), (
            "postprocess.%s cannot be produced by the frontend (the panel has no such key)" % key
        )

    assert options["remove_underscore"]["default"] == "true", "remove_underscore must default to true (§4.1)"
    assert options["escape_parens"]["default"] == "true", "escape_parens must default to true (§4.1)"
    assert options["character_tags_first"]["default"] == "false"
    assert options["character_tag_expand"]["default"] == "false"

    order = list(options)
    assert order.index("escape_parens") == order.index("remove_underscore") + 1, (
        "escape_parens must sit next to remove_underscore -- both rewrite the tag text written into .txt"
    )
    assert options["escape_parens"]["hintKey"], "escape_parens needs a hint explaining its cost"


def test_dynamic_string_keys_referenced_by_the_panels_exist() -> None:
    """A **dynamic** lookup like t(option.stringKey) cannot be caught by scanning literals, so this table is the only thing pinning it down."""
    keys = strings_keys()
    refs = set(STRING_KEY_REF_RE.findall(read(JS_ROOT / "autotag.js")))
    assert refs, "no stringKey / hintKey references parsed out of autotag.js"
    missing = sorted(ref for ref in refs if ref not in keys)
    assert not missing, "copy keys dynamically referenced by autotag.js do not exist in strings.js: %s" % missing
    for key in ("filter.only", "filter.include", "filter.exclude"):
        assert key in keys, "filter.js's three-state labels are missing %s" % key
# ---------------------------------------------------------------------------
# 8. §4.5: autocomplete's category name, preview's hard cap of 32 images
# ---------------------------------------------------------------------------

PREVIEW_CAP_RE = re.compile(r"const PREVIEW_MAX_IMAGES = (\d+);")


def test_preview_cap_is_blocked_client_side() -> None:
    """§4.5: more than 32 images must be blocked **before the request is sent**; the backend 400 is only a fallback."""
    text = read(JS_ROOT / "autotag.js")
    match = PREVIEW_CAP_RE.search(text)
    assert match is not None, "autotag.js has no preview-cap constant"
    cap = int(match.group(1))
    assert cap <= 32, "the preview cap %d exceeds the backend's frozen 32" % cap

    keys = strings_keys()
    assert "autotag.previewTooMany" in keys, "missing the 'preview at most N images' notice copy"
    assert "autotag.runConfirmNoPreview" in keys, "exceeding the cap and going through /run must have its own confirmation copy"

    guard = text.index("autotag.previewTooMany")
    call = text.index("api.autotagPreview(")
    assert guard < call, "the block must be written before the call to /api/autotag/preview, otherwise the user sees the backend 400"
    assert 'err.code === "too_many_images"' in text, "the backend 400 fallback is not recognized"


def test_autocomplete_renders_the_category_name() -> None:
    """§4.5: category is the category name ("General"), category_id is the numeric code ("0")."""
    text = read(JS_ROOT / "tags.js")
    assert "item.category" in text, "autocomplete does not read category"
    assert "item.category_id" in text, "autocomplete has no category_id fallback (an old backend would show an empty badge)"


# ---------------------------------------------------------------------------
# 9. §4.7: model download (endpoint, phase unit switching, size formatting)
# ---------------------------------------------------------------------------

#: function-body capture: from the signature to the closing brace indented by 2 spaces (blocks inside the function are indented 4 spaces)
PAINT_PROGRESS_RE = re.compile(r"function paintProgress\([^)]*\)\s*\{(.*?)\n  \}", re.S)
PHASE_OF_RE = re.compile(r"function phaseOf\([^)]*\)\s*\{(.*?)\n  \}", re.S)
FINISH_JOB_RE = re.compile(r"function finishJob\([^)]*\)\s*\{(.*?)\n  \}", re.S)
DOWNLOAD_MODEL_RE = re.compile(r"async function downloadModel\([^)]*\)\s*\{(.*?)\n  \}", re.S)
MODEL_LIST_RE = re.compile(r"function paintModelList\([^)]*\)\s*\{(.*?)\n  \}", re.S)
FORMAT_SIZE_RE = re.compile(r"export function formatSize\([^)]*\)\s*\{(.*?)\n\}", re.S)
#: unit table and radix: the single source of truth for size conversion
SIZE_UNITS_RE = re.compile(r"const SIZE_UNITS = \[([^\]]*)\]")
#: the executable criterion for "size formatting is called by the **model list**"
SIZE_CALL_RE = re.compile(r"formatSize\(\s*model\.size_bytes\s*\)")


def autotag_text() -> str:
    return read(JS_ROOT / "autotag.js")


def test_download_endpoint_is_declared_and_called() -> None:
    """§4.7: POST /api/autotag/download must be declared in api.js and really called."""
    assert ("POST", "/api/autotag/download") in spec_routes(), (
        "POST /api/autotag/download is not in p0-spec §4.7; without it, the frontend calling this endpoint is judged as contract drift"
    )
    assert ("POST", "/api/autotag/download") in api_routes(), "api.js does not declare the download endpoint"
    text = autotag_text()
    assert "api.autotagDownload(" in text, "autotag.js does not call the download endpoint"
    assert "autotagStreamUrl(" in text, "download progress must reuse /api/autotag/stream and must not open a separate stream"


def test_download_progress_reads_phase_and_switches_units() -> None:
    """§4.7: for phase=download done/total are bytes; for phase=infer they are images.

    Both unit paths must exist: showing a byte count as an image count is exactly
    what this field is meant to prevent.
    """
    text = autotag_text()
    match = PAINT_PROGRESS_RE.search(text)
    assert match is not None, "no paintProgress function body found in autotag.js"
    body = match.group(1)
    assert "phaseOf(payload)" in body, "paintProgress does not pick its unit by phase"
    assert "PHASE_DOWNLOAD" in body, "paintProgress has no download branch"
    # Byte path: the count must go through size formatting
    assert "formatSize(done)" in body and "formatSize(total)" in body, (
        "download progress does not format done/total as bytes"
    )
    assert "autotag.downloadProgress" in body
    # The image-count path must still be there
    assert "autotag.progress" in body, "the inference progress (images) got broken"

    phase_body = PHASE_OF_RE.search(text)
    assert phase_body is not None, "no phaseOf function body found in autotag.js"
    assert "payload.phase" in phase_body.group(1), "phaseOf does not read payload.phase (the §4.7 field)"
    assert "PHASE_DOWNLOAD" in phase_body.group(1) and "PHASE_INFER" in phase_body.group(1), (
        "phaseOf does not know both download and infer"
    )

    # Both streams' opening move must hard-code the unit instead of guessing
    assert "phase: PHASE_DOWNLOAD" in text, "the download task does not declare phase=download at the start"
    assert "phase: PHASE_INFER" in text, "the inference task does not declare phase=infer at the start"

    keys = strings_keys()
    for key in ("autotag.downloadProgress", "autotag.downloadCurrent", "autotag.progress", "autotag.progressCurrent"):
        assert key in keys, "missing copy %s" % key


def test_size_formatter_exists_and_is_used_by_the_model_list() -> None:
    """The size formatter exists, there is only one set of units, and it is called by the **model list**."""
    text = autotag_text()
    match = FORMAT_SIZE_RE.search(text)
    assert match is not None, "autotag.js does not export formatSize(bytes)"
    body = match.group(1)
    assert "SIZE_STEP" in body, "formatSize does not convert with a shared radix constant"

    units = SIZE_UNITS_RE.search(text)
    assert units is not None, "autotag.js has no SIZE_UNITS unit table"
    names = re.findall(r'"([^"]+)"', units.group(1))
    binary = [name for name in names if name.endswith("iB")]
    si = [name for name in names if name != "B" and name.endswith("B") and not name.endswith("iB")]
    assert names and names[0] == "B", "the unit table must start at B: %s" % names
    assert bool(binary) != bool(si), "size units mix two systems (only one of the KiB and KB families may remain): %s" % names
    assert "SIZE_STEP = 1024" in text, "the radix must be hard-coded as 1024 instead of scattered around"
    assert re.search(r"//:.*1024", text), "the unit convention must state its reason in a comment (why radix 1024)"

    assert SIZE_CALL_RE.search(text), "the model list does not render model.size_bytes with formatSize"


def test_backup_write_modes_get_backup_wording_never_the_old_denial() -> None:
    """The default write mode keeps a backup before overwriting, so the confirmation
    copy must say backup.

    A real defect on 2026-09-19: after the default changed to backup_overwrite, the
    confirmation shown when the preview cap is exceeded still said "irreversible,
    no automatic backup" -- the **exact opposite** of the default behavior. Three
    things are pinned here: the code branches on the current mode, the backup
    branch has its own copy, and the old wording remains only in the non-backup
    branch.
    """
    text = autotag_text()
    assert "BACKUP_WRITE_MODES" in text, "the 'write modes that back up' are not distinguished"
    assert re.search(r"BACKUP_WRITE_MODES\.has\(writeModeSelect\.value\)", text), (
        "the confirmation/hint does not branch on the current write mode"
    )
    assert 'backsUp ? "autotag.runConfirmBackup" : "autotag.runConfirm"' in text, (
        "the confirmation with a preview does not choose its copy by write mode"
    )
    assert '"autotag.runConfirmNoPreviewBackup" : "autotag.runConfirmNoPreview"' in text, (
        "the confirmation when over the cap does not choose its copy by write mode"
    )
    assert '? "autotag.writeHintBackup" : ""' in text, "there is no hint under the write mode that changes with the mode"
    relabel_body = re.search(r"function relabel\(\)\s*\{(.*?)\n  \}", text, re.S)
    assert relabel_body is not None, "no relabel() function body found"
    assert "paintWriteModeHint()" in relabel_body.group(1), "on a language switch the write-mode hint is not re-labelled"

    zh = read(STRINGS_PATH)
    plain = re.search(r'"autotag\.runConfirmNoPreview":\s*"([^"]*)"', zh)
    assert plain is not None, "missing the over-cap confirmation copy"
    assert "自动备份" in plain.group(1), "the non-backup note was deleted by mistake (it is still correct)"
    backup = re.search(r'"autotag\.runConfirmNoPreviewBackup":\s*"([^"]*)"', zh)
    assert backup is not None, "missing the 'backs up' version of the over-cap confirmation copy"
    assert "备份" in backup.group(1), "the backup version's confirmation copy does not mention backup"
    assert "自动备份" not in backup.group(1), "the backup version reuses the contradictory 'no automatic backup' line"


def test_scope_list_has_a_subdirectory_option() -> None:
    """A scope missing "include subdirectories" = the only way to tag the whole library is directory by directory.

    The four scopes moved into the shared control (web/js/scope.js, 2026-09-19): both write panels now read
    the same list instead of the batch panel inheriting one of them invisibly. The structural lesson from
    this criterion's first version still holds - dir_recursive also appears in the label table and in the
    option loop, so deleting the line that really *returns* the recursive list still has to fail it.
    """
    scope_js = read(JS_ROOT / "scope.js")
    domain = re.search(r"SCOPE = Object\.freeze\(\{(.*?)\}\);", scope_js, re.S)
    assert domain is not None, "the scope value domain is missing"
    assert 'DIR_RECURSIVE: "dir_recursive"' in domain.group(1), (
        "the value domain has no 'include subdirectories' entry"
    )
    labels = re.search(r"SCOPE_LABEL_KEYS = Object\.freeze\(\{(.*?)\}\);", scope_js, re.S)
    assert labels is not None, "the scope label table is missing"
    assert 'dir_recursive: "scope.dirRecursive"' in labels.group(1), (
        "'include subdirectories' has no copy key attached"
    )
    paths = re.search(r"function paths\(\) \{(.*?)\n  \}", scope_js, re.S)
    assert paths is not None, "paths() is missing from the shared scope control"
    # 2026-09-20: the listing is returned through cachedRecursive(), which refuses a listing loaded
    # for another directory (see test_web_scope.py::test_a_listing_is_an_answer_about_one_directory_only
    # and section 6 of scope_strip_harness.mjs). The line that really returns it is still what this
    # pins, so deleting the return branch still fails here.
    assert "value() === SCOPE.DIR_RECURSIVE) return cachedRecursive() || [];" in paths.group(1), (
        "selecting 'include subdirectories' does not return the recursive list"
    )
    assert "async function ensureRecursive()" in scope_js, "the recursive list is never fetched"
    assert "recursive.paths && recursive.dir === getDirKey()" in scope_js, (
        "the recursive list is not cached per directory (every refresh reopens the request), or the "
        "cache is not tied to the directory it was loaded for"
    )
    assert 'n === null ? "…"' in scope_js, (
        "an unloaded recursive scope shows 0 images (making people think the subdirectories are empty)"
    )

    app = app_text()
    assert "loadDirRecursive:" in app, "app.js does not hand the recursive list's data source to the control"
    assert "getDirKey:" in app, "app.js does not provide the 'which directory is current' key"
    # 2026-09-20: this used to count "...scopeProviders," == 2 - the two panels each building their
    # own control over their own <select>, which is exactly how "which images?" got two answers.
    # One control now serves the column; test_web_scope.py counts the construction sites over the
    # whole frontend and runs both panels over one instance.
    assert "const scope = createScopeControl({" in app, (
        "app.js does not build the column's one scope control"
    )
    assert "...scopeProviders," not in app, (
        "a panel is spreading the providers into a scope control of its own again"
    )


def test_model_list_shows_installed_downloadable_and_blocked() -> None:
    """§4.7: a model entry carries installed / size_bytes / downloadable, and the frontend shows three states."""
    text = autotag_text()
    assert "model.installed" in text, "does not read §4.7's installed"
    assert "model.downloadable" in text, "does not read §4.7's downloadable"
    assert SIZE_CALL_RE.search(text), "the downloadable state does not show size (size_bytes)"
    assert "model.available" in text, "the old backend's available fallback is gone (the spelling before installed appeared)"
    body = MODEL_LIST_RE.search(text)
    assert body is not None, "no paintModelList function body found in autotag.js"
    paint = body.group(1)
    assert "model.description" in paint, (
        "the description copy must be localized under the model id, keeping the backend string as a fallback (otherwise it shows Chinese in the English UI)"
    )
    assert "model.download_size_bytes" in paint, (
        "an uninstalled model does not read §4.7's download_size_bytes -- size_bytes "
        "is always 0 when the model is not installed locally, and reading only it "
        "makes every row show 'unknown size'; this is exactly how the field once "
        "became dead"
    )
    # Asserting only "the literal appears" is an empty criterion: the definition
    # can stay while the use site is changed back, and it still passes. Where it is
    # **used** must be pinned.
    assert re.search(r"advertisedSize\s*=\s*Number\(model\.download_size_bytes\)", paint), (
        "advertisedSize is not derived from model.download_size_bytes"
    )
    assert "size: advertisedSize" in paint, (
        "the downloadable branch does not use the size derived from download_size_bytes (defined but unused = dead field)"
    )
    assert 't("autotag.modelStatusInstalledSize"' in paint, (
        "an installed model does not show its local size (size_bytes's use in the installed branch)"
    )
    keys = strings_keys()
    for key in (
        "autotag.modelStatusInstalled",
        "autotag.modelStatusInstalledSize",
        "autotag.modelStatusDownloadable",
        "autotag.modelStatusBlocked",
        "autotag.modelBlockedReason",
        "autotag.modelDownload",
        "autotag.modelDownloading",
        "autotag.modelRefresh",
        "autotag.sizeUnknown",
    ):
        assert key in keys, "missing model-status copy %s" % key


def test_download_flow_reuses_the_stream_and_surfaces_failures() -> None:
    """§4.7: download -> the same SSE -> on completion refresh the list and select that model; failures must be visible."""
    text = autotag_text()
    body = DOWNLOAD_MODEL_RE.search(text)
    assert body is not None, "no downloadModel function body found in autotag.js"
    assert "api.autotagDownload(" in body.group(1), "downloadModel does not call the download endpoint"
    assert "startStream(jobId, PHASE_DOWNLOAD)" in body.group(1), "the download does not reuse the same SSE channel"
    assert "button.disabled = running;" in text, "other download buttons must be disabled while downloading"
    assert "if (running) {" in body.group(1), "downloadModel is not re-entrant-safe (only one task at a time)"

    finish = FINISH_JOB_RE.search(text)
    assert finish is not None, "no finishJob function body found in autotag.js"
    finish_body = finish.group(1)
    assert "loadModels(" in finish_body, "the model list is not refreshed after the download completes"
    assert "autotag.downloadFailed" in finish_body, "a download failure does not surface an error"
    assert "autotag.downloadErrors" in finish_body, "a download failure does not report errors"
    assert "formatError(" in finish_body, "errors entries are not rendered as {path,message}"

    keys = strings_keys()
    assert "autotag.downloadFailed" in keys and "autotag.downloadErrors" in keys


def test_cancel_reuses_the_frozen_endpoint_with_download_wording() -> None:
    """§4.4's cancel endpoint is reused by the download; only the copy changes with phase."""
    text = autotag_text()
    assert "api.autotagCancel(" in text, "cancel does not call POST /api/autotag/cancel"
    assert 'taskKind === PHASE_DOWNLOAD ? "autotag.cancelDownloadRequested" : "autotag.cancelRequested"' in text, (
        "the cancel copy does not switch with phase"
    )
    keys = strings_keys()
    assert "autotag.cancelRequested" in keys and "autotag.cancelDownloadRequested" in keys
    assert "autotag.cancelTimeout" in keys, "the cancel-watchdog copy is gone (§4.4: when the end event never arrives, clean up)"


# ---------------------------------------------------------------------------
# 10. §4.9: export dataset.toml (endpoint + params + warnings/skipped rendering + copy/download)
# ---------------------------------------------------------------------------
#: the export panel's render function bodies (nested in createExportPanel, closed by a 2-space indent)
EXPORT_PAINT_RE = re.compile(r"function paintReport\([^)]*\)\s*\{(.*?)\n  \}", re.S)
EXPORT_SUBSETS_RE = re.compile(r"function paintSubsets\([^)]*\)\s*\{(.*?)\n  \}", re.S)
EXPORT_PARAMS_RE = re.compile(r"function collectExportParams\(\)\s*\{(.*?)\n  \}", re.S)
EXPORT_COPY_RE = re.compile(r"async function copyToml\(\)\s*\{(.*?)\n  \}", re.S)
EXPORT_DOWNLOAD_RE = re.compile(r"function downloadToml\(\)\s*\{(.*?)\n  \}", re.S)
#: pure functions at app.js's top level (closed by a 0 indent)
EXPORT_GROUP_WARNINGS_RE = re.compile(r"^function groupExportWarnings\([^)]*\)\s*\{(.*?)^\}", re.M | re.S)
EXPORT_GROUP_SKIPPED_RE = re.compile(r"^function groupExportSkipped\([^)]*\)\s*\{(.*?)^\}", re.M | re.S)
EXPORT_SUBSET_PROBLEMS_RE = re.compile(r"^function subsetProblems\([^)]*\)\s*\{(.*?)^\}", re.M | re.S)
EXPORT_WARNING_CATEGORY_RE = re.compile(r"^function warningCategory\([^)]*\)\s*\{(.*?)^\}", re.M | re.S)
EXPORT_WARNING_TEXT_RE = re.compile(r"^function warningText\([^)]*\)\s*\{(.*?)^\}", re.M | re.S)
#: frozen warning / skip codes (§4.9; messages look like "[missing_caption] 3 images have no caption")
FROZEN_WARNING_CODES = (
    "root_images",
    "broken_image",
    "empty_dir",
    "unreadable_dir",
    "missing_caption",
    "escaped_comma",
    "oversize_image",
    "alpha_images",
    "too_few_images",
)
#: the warning category table in the zh-CN pack (exported as FALLBACK_GROUPS, re-forwarded by the engine under the WARNING_GROUPS alias)
WARNING_GROUPS_RE = re.compile(r"export const FALLBACK_GROUPS = Object\.freeze\(\[(.*?)\n\]\);", re.S)
WARNING_GROUP_ID_RE = re.compile(r'\{\s*id:\s*"(\w+)"')


def app_text() -> str:
    path = JS_ROOT / "app.js"
    assert path.is_file(), "app.js not found: %s" % path
    return read(path)


def string_value(key: str) -> str:
    """The Chinese value of a key in the zh-CN pack (only values without escaped quotes are supported)."""
    match = re.search(r'"%s":\s*"([^"]*)"' % re.escape(key), read(STRINGS_PATH))
    assert match is not None, "the zh-CN pack has no %s" % key
    return match.group(1)


def export_body(pattern: re.Pattern[str], what: str) -> str:
    match = pattern.search(app_text())
    assert match is not None, "no %s function body found in app.js (the regex or function name drifted)" % what
    return match.group(1)


def test_dataset_toml_endpoint_is_declared_and_called() -> None:
    """§4.9: POST /api/dataset/toml must be declared in api.js and really called by the export panel."""
    assert ("POST", "/api/dataset/toml") in spec_routes(), (
        "POST /api/dataset/toml is not in p0-spec §4.9; without it, the frontend calling this endpoint is judged as contract drift"
    )
    assert ("POST", "/api/dataset/toml") in api_routes(), "api.js does not declare the export endpoint"
    assert "body: { root, params }" in read(API_PATH), (
        "api.js does not assemble the request body as {root, params} per §4.9"
    )
    assert "api.datasetToml(" in app_text(), "the export panel does not call POST /api/dataset/toml"


def test_export_panel_renders_warnings_and_skipped_not_just_counts() -> None:
    """warnings / skipped are this feature's main value to the user: they must be read, grouped, and landed in the DOM."""
    code = export_body(EXPORT_PAINT_RE, "paintReport")
    assert "payload.warnings" in code, "the render path does not read §4.9's warnings"
    assert "payload.skipped" in code, "the render path does not read §4.9's skipped"
    assert "groupExportWarnings(payload.warnings" in code, "warnings are not grouped, just piled together"
    assert "groupExportSkipped(payload.skipped" in code, "skipped is not grouped by reason"
    assert "payload.warning_items" in code, "the render path does not read warning_items"
    assert "payload.skipped_items" in code, "the render path does not read skipped_items"
    assert "append(" in code and "replaceChildren(" in code, "warnings/skips do not really land in the DOM"
    for key in ("export.warningsTitle", "export.warningsNone", "export.skippedTitle", "export.skippedNone"):
        assert 't("%s"' % key in code, "the render path is missing copy %s" % key
        assert key in strings_keys(), "strings.js has no %s" % key


def test_export_warning_categories_all_have_labels() -> None:
    """The grouping key is the frozen [code]: none of the 7 codes may be missing, and each must have a Chinese label."""
    match = WARNING_GROUPS_RE.search(read(STRINGS_PATH))
    assert match is not None, "FALLBACK_GROUPS not found in the zh-CN pack"
    ids = WARNING_GROUP_ID_RE.findall(match.group(1))
    assert len(ids) >= 3, "only %d categories parsed out of FALLBACK_GROUPS" % len(ids)
    assert "other" in ids, "no fallback category; warnings that do not match would be quietly dropped"
    missing_codes = [code for code in FROZEN_WARNING_CODES if code not in ids]
    assert not missing_codes, "WARNING_GROUPS is missing frozen codes: %s" % missing_codes
    keys = strings_keys()
    missing = [group_id for group_id in ids if "export.group.%s" % group_id not in keys]
    assert not missing, "These warning categories have no copy: %s" % missing
    assert 't("export.group." + group.id' in app_text(), "the group heading does not take its copy by category"


def test_export_classification_prefers_the_frozen_code() -> None:
    """§4.9: messages start with [code]; classification looks at the code first, keywords are only secondary, and the fallback must not be dropped."""
    text = app_text()
    assert re.search(r"const WARNING_CODE_RE = /\^", text), "no regex anchored to the [code] at the start of the message"
    code_re = re.search(r"const WARNING_CODE_RE = /(.+?)/;", text)
    assert code_re is not None, "WARNING_CODE_RE is not defined in app.js"

    body = export_body(EXPORT_WARNING_CATEGORY_RE, "warningCategory")
    assert "WARNING_CODE_RE.exec(" in body, "warningCategory does not parse the leading [code]"
    assert "WARNING_GROUP_IDS.has(" in body, "does not check whether the code is in the frozen set"
    assert 'return "other"' in body, "when no code is found and no keyword matches it must fall to other, not be dropped"
    code_at = body.index("WARNING_CODE_RE.exec(")
    keyword_at = body.index("group.match")
    assert code_at < keyword_at, "keyword matching must come after the code (a code hit wins)"

    # Unrecognized codes and messages with no code at all must still render
    paint = export_body(EXPORT_PAINT_RE, "paintReport")
    assert "groupExportWarnings(payload.warnings" in paint
    group_body = export_body(EXPORT_GROUP_WARNINGS_RE, "groupExportWarnings")
    assert "warningText(" in group_body, "the rendered text does not go through warningText (the code prefix must be stripped)"


def test_export_strips_only_known_codes_from_the_printed_line() -> None:
    """The code is already the group heading, so repeating it in the body is noise; but an unrecognized code must be kept verbatim."""
    body = export_body(EXPORT_WARNING_TEXT_RE, "warningText")
    assert "WARNING_CODE_RE.exec(" in body, "warningText does not parse [code]"
    assert "WARNING_GROUP_IDS.has(" in body, "only codes in the frozen set may be stripped"
    assert "slice(" in body, "the prefix is not trimmed by the code length"
    assert "return raw" in body, "when no code is found it must return the raw text, not swallow the message"


def test_export_grouping_reads_the_frozen_fields() -> None:
    """§4.9's warnings is [str], with another copy per subsets[]; skipped is {image_dir, reason}."""
    warnings_code = export_body(EXPORT_GROUP_WARNINGS_RE, "groupExportWarnings")
    assert "subset.warnings" in warnings_code, "subsets[].warnings is not merged into the groups"
    assert "subset.image_dir" in warnings_code, "a subdirectory warning is not labelled with the subset it came from"
    # Measured on a real response: the top level is an aggregate of subsets[].warnings, so rendering the top level first prints every line twice
    assert "claimed" in warnings_code, "the top-level aggregate and subsets[].warnings are not de-duplicated (the same line prints twice)"
    subset_at = warnings_code.index("subset.warnings")
    aggregate_at = warnings_code.index("Array.isArray(warnings)")
    assert subset_at < aggregate_at, (
        "the subsets[].warnings carrying the directory must be rendered first, then "
        "the top-level aggregate fills in the gaps, otherwise directory attribution "
        "is lost and every line repeats"
    )
    skipped_code = export_body(EXPORT_GROUP_SKIPPED_RE, "groupExportSkipped")
    assert "entry.image_dir" in skipped_code, "the skipped group does not read image_dir"
    assert "entry.reason" in skipped_code, "the skipped group does not read reason"
    assert "warningCategory(reason)" in skipped_code, (
        "skipped.reason is not classified by [code] (two wordings of the same problem split into two groups)"
    )


def test_export_params_form_covers_the_frozen_knobs() -> None:
    """§4.9's body is {root, params}; the form must at least produce these four keys, with defaults matching §3.9."""
    code = export_body(EXPORT_PARAMS_RE, "collectExportParams")
    for key in ("resolution", "batch_size", "num_repeats", "caption_extension"):
        assert key in code, "the params form does not produce params.%s" % key
    assert "1536" in code, "resolution's default is not §3.9's 1536"
    assert '".txt"' in code, "caption_extension's default is not §3.9's .txt"
    assert "api.datasetToml(root, collectExportParams())" in app_text(), (
        "the endpoint call does not send root together with params"
    )


def test_export_new_codes_are_their_own_bucket() -> None:
    """Two codes newly added by A: a single broken image is broken_image, and its keyword fallback must not be stolen by "directory unreadable"."""
    match = WARNING_GROUPS_RE.search(read(STRINGS_PATH))
    assert match is not None, "FALLBACK_GROUPS not found in the zh-CN pack"
    ids = WARNING_GROUP_ID_RE.findall(match.group(1))
    assert "root_images" in ids, "missing the [root_images] group (the root itself has images = none get trained)"
    assert "broken_image" in ids, "missing the [broken_image] group (a single image's size cannot be read)"
    assert "unreadable_image" not in ids, (
        "a broken single image must not be called unreadable_image: in the keyword fallback table, unreadable belongs to the 'directory unreadable' group and would be misclassified"
    )
    assert ids.index("broken_image") < ids.index("unreadable_dir"), (
        "broken_image must come before unreadable_dir, otherwise a code-less broken-image message gets stolen by 'directory unreadable'"
    )
    keys = strings_keys()
    for key in ("export.group.root_images", "export.group.broken_image"):
        assert key in keys, "missing copy %s" % key


def test_export_alpha_mask_is_opt_in_and_per_subset() -> None:
    """§3.9: alpha_mask applies per subset and, like caption_dropout_*, is written only when true."""
    code = export_body(EXPORT_PARAMS_RE, "collectExportParams")
    assert "alpha_mask" in code, "the params form does not produce params.alpha_mask"
    assert "if (alphaMask.checked)" in code, "alpha_mask is not sent only when checked"
    assert "params.alpha_mask = true" in code, "checking it does not write true"
    assert "params.alpha_mask = false" not in code, (
        "false would be written into the request too; §3.9's rule is write only when true, so no such key line should appear"
    )
    assert "alphaMask.checked = false" in app_text(), "the checkbox must default to unchecked"

    keys = strings_keys()
    for key in ("export.alphaMask", "export.alphaMaskHint"):
        assert key in keys, "missing copy %s" % key
    label = string_value("export.alphaMask")
    hint = string_value("export.alphaMaskHint")
    assert "[[datasets.subsets]]" in label or "[[datasets.subsets]]" in hint, (
        "the copy does not make clear it is written inside [[datasets.subsets]] (A measured that placing it in [[datasets]] is rejected by the trainer)"
    )
    assert "全局" not in label and "全局" not in hint, (
        "alpha_mask is per subset and must not be written as a global switch"
    )


def test_export_copy_and_download_paths_both_exist() -> None:
    """§4.9's P0 decision: the server does not write to disk, so both the copy and the download paths must really exist."""
    copy_code = export_body(EXPORT_COPY_RE, "copyToml")
    assert "navigator.clipboard.writeText" in copy_code, "copy does not use the clipboard API"
    assert 'execCommand("copy")' in copy_code, "the fallback copy for when the clipboard API is absent is gone"
    assert "tomlArea.value" in copy_code, "the copied content is not the toml the API returned"

    download_code = export_body(EXPORT_DOWNLOAD_RE, "downloadToml")
    assert "new Blob(" in download_code and "URL.createObjectURL" in download_code, (
        "the download does not turn the toml into a blob"
    )
    assert "link.download = EXPORT_FILE_NAME" in download_code, (
        "the download does not set a file name (the browser saves it under a random blob name)"
    )
    assert "URL.revokeObjectURL" in download_code, "the object URL is not revoked after the download"

    text = app_text()
    assert 'generateButton.addEventListener("click", () => { void runExport(); })' in text
    assert 'copyButton.addEventListener("click", () => { void copyToml(); })' in text, "the copy button is not wired"
    assert 'downloadButton.addEventListener("click", () => { void downloadToml(); })' in text, (
        "the download button is not wired"
    )
    for key in ("export.copy", "export.download", "export.copied", "export.copyFailed", "export.downloaded"):
        assert key in strings_keys(), "missing copy %s" % key


def test_every_frozen_export_field_has_a_landing_spot() -> None:
    """Every §4.9 response field must have a landing spot in the frontend, otherwise only half the contract is implemented."""
    text = app_text()
    for field in ("toml", "subset_count", "image_count", "subsets", "warnings", "skipped"):
        assert "payload.%s" % field in text, "§4.9's payload.%s has no landing spot in the frontend" % field
    code = export_body(EXPORT_SUBSETS_RE, "paintSubsets")
    for field in ("image_dir", "image_count", "caption_count", "num_repeats", "warnings"):
        assert "subset.%s" % field in code, "subsets[].%s is not displayed" % field
    assert "EXPORT_PREVIEW_SUBSETS" in code, "the subset preview has no cap (219 directories would fill the sidebar)"
    assert "export.subsetMore" in strings_keys(), "missing the 'N more subsets not listed' copy"


def test_export_flags_the_non_recursive_failure_mode() -> None:
    """§1.1: when a subset's image_dir points at the current directory itself the training set is empty, and the frontend must say so on the trainer's behalf."""
    code = export_body(EXPORT_SUBSET_PROBLEMS_RE, "subsetProblems")
    assert "subset.image_dir" in code, "does not check each subset's image_dir"
    keys = strings_keys()
    assert "export.consistencyOk" in keys, "missing the 'one directory per subset check passed' confirmation copy"
    assert "export.consistencyRoot" in keys, "missing the 'image_dir points at the root itself' error copy"
    assert "export.consistencyDuplicate" in keys, "missing the 'duplicate image_dir' error copy"
    assert "subsetProblems(subsets, root)" in export_body(EXPORT_PAINT_RE, "paintReport"), (
        "the consistency check is not hooked into the render path"
    )


# ---------------------------------------------------------------------------
# 11. §4.9 addendum 4: localizing backend messages (msg.<code> / msg.skip.<code> + hasKey fallback)
#: the error localization keys (the frontend's describeError uses msg.<code>)
CURATED_ERROR_CODES = (
    "outside_roots",
    "not_found",
    "not_a_directory",
    "remote_path_config_disabled",
    "not_a_file",
    "unsupported_image",
    "bad_size",
    "bad_write_mode",
    "bad_op",
    "unknown_job",
    "no_images",
    "unreadable",
    "missing_tag_payload",
    "empty_image_list",
    "bad_path",
)


def test_every_frozen_message_code_has_a_localization_key() -> None:
    """Every frozen warning code has a msg.<code> in the zh-CN pack; skip reasons get their own msg.skip.<code>."""
    keys = strings_keys()
    missing = [code for code in FROZEN_WARNING_CODES if "msg." + code not in keys]
    assert not missing, "missing warning copy msg.<code>: %s" % missing
    for code in ("empty_dir", "unreadable_dir"):
        assert "msg.skip." + code in keys, "missing skip-reason copy msg.skip.%s" % code
    for code in CURATED_ERROR_CODES:
        assert "msg." + code in keys, "missing error copy msg.%s" % code


def test_backend_messages_degrade_to_the_raw_text_not_a_bare_key() -> None:
    """When the code is unrecognized / there is no translation / placeholders cannot be filled, it must fall back to the backend's own text and must not render a bare key."""
    text = app_text()
    assert "hasKey(" in text, "localization does not check whether a translation exists with hasKey first"
    helper_at = text.index("function localizeBackendMessage(")
    helper = text[helper_at:text.index("\n}\n", helper_at)]
    assert "hasKey(" in helper, "localizeBackendMessage does not use hasKey"
    assert "return null" in helper, "it does not return null when no translation is found (which renders a bare key)"
    assert "return /\\{\\w+\\}/.test(text) ? null : text;" in helper, "it does not fall back when placeholders cannot be filled"

    describe_at = text.index("function describeError(")
    describe = text[describe_at:text.index("\n}\n", describe_at)]
    assert 'localizeBackendMessage("msg."' in describe, "error display does not try localization first"
    assert "err.message" in describe, "when there is no translation it must fall back to the backend message"

