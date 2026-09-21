"""Long-running operations: what the panel says while a job is in flight, and how it ends.

Three findings from the 2026-09 UX audit, all the same shape - the app started something slow
and then looked exactly like an app that had done nothing:

1. "Rebuild all caches" fired one POST for every stale entry with nothing disabled, so a second
   click started a second one, and the button never said it was working.
2. A batch write is a single POST too, so its button label is the only status line there is - and
   the table it produced was cleared, throwing away the only way to locate a failed image.
3. Scale/export cancel ignored the server answer. "cancelled: false" (the job was already gone)
   locked the panel for good, because the terminal event it was waiting for would never come; and
   a cancelled job still painted a 100% bar.

The behaviour lives in `test/fixtures/batch_harness.mjs` and `test/fixtures/scale_harness.mjs`;
this file pins the wiring that the harnesses cannot reach (the cache panel lives in app.js, which
has no headless harness because it touches the DOM at module scope).
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
HTML = WEB / "index.html"
CSS = WEB / "css" / "app.css"
HARNESS = REPO / "test" / "fixtures" / "jobs_harness.mjs"
STORE_HARNESS = REPO / "test" / "fixtures" / "jobstore_harness.mjs"


def source(name: str) -> str:
    return (JS / name).read_text(encoding="utf-8")


def test_rebuilding_caches_says_so_and_is_single_flight() -> None:
    app = source("app.js")
    assert "let cacheBusy = false;" in app, "the cache panel has no in-flight state"
    # The progress wording moved into readiness.js:cacheStatusText on 2026-09-20 (the readiness strip's
    # cache segment shows the same line); paintCacheStatus still paints it in its busy branch.
    readiness = source("readiness.js")
    assert 't("cache.progress", { done: Number(state.done) || 0, total: Number(state.total) || 0 })' in readiness, (
        "the status line never says how far the rebuild got"
    )
    assert "status.textContent = cacheStatusText(view);" in app, (
        "the cache tab stopped painting the shared status wording"
    )
    assert "api.cacheInvalidateRun(images)" in app, "the rebuild still uses the synchronous POST"
    assert "new EventSource(cacheStreamUrl(cacheJobId))" in app, "the panel never opens the job stream"
    assert "api.cacheInvalidateCancel(cacheJobId)" in app, "a running rebuild cannot be cancelled"
    assert 't("cache.cancelled", { done: Number(summary.processed) || 0, total: cacheJobTotal })' in app, (
        "the terminal copy must say how far it got, not pretend it was undone"
    )
    assert "if (cacheBusy) return;" in app, "a second rebuild click would start a second job"
    assert "button.disabled = cacheBusy;" in app, "the per-row rebuild buttons stay live during a rebuild"
    assert '$("btn-cache-rebuild-all").disabled = cacheBusy || cacheFailed || staleCaches.length === 0;' in app, (
        "rebuild-all is still clickable while a rebuild is running (or after a failed status load)"
    )
    assert "cacheBusy = false;" in app, "the busy state is never released"


def test_a_write_in_flight_locks_the_panel_and_says_what_it_is_doing() -> None:
    body = source("batch.js")
    assert 't("batch.writing", { n: writingCount })' in body, "the write button never reports the write"
    assert "if (!lastPreview || writing) return;" in body, "a second apply could fire a second POST"
    assert "setWriting(true, paths.length);" in body, "the write is not wrapped in the busy state"
    assert "api.batchCaptionsRun(payload)" in body, "the write still uses the synchronous POST"
    assert "new EventSource(batchStreamUrl(jobId))" in body, "the panel never opens the job stream"
    assert "appendResultRow(payload.entry);" in body, (
        "the outcome table is not built from the job's own per-image events"
    )
    assert 't("batch.cancelled", { applied, total: jobTotal })' in body, (
        "the terminal copy must say how far it got, not pretend it was undone"
    )
    assert "api.batchCaptionsCancel(jobId)" in body, "a running write cannot be cancelled"


def test_scale_cancel_reads_the_answer_and_arms_a_watchdog() -> None:
    src = source("scale.js")
    assert "answer && answer.cancelled === false" in src, "the cancel answer is ignored again"
    assert "armWatchdog();" in src, "an accepted cancel that never completes would lock the panel"
    assert "CANCEL_WATCHDOG_MS" in src, "there is no watchdog constant"
    assert "if (!data.cancelled) {" in src, "a cancelled job paints a full progress bar"


def test_the_job_shelf_is_outside_every_panel() -> None:
    """The shelf's whole point is that a running job is visible from **every** tab.

    That is structural, not behavioural: #jobs is a sibling of the layout, so hiding a panel cannot hide
    the job. A shelf rendered inside the owning panel would be exactly the problem it solves.
    """
    markup = HTML.read_text(encoding="utf-8")
    assert '<div id="jobs" class="job-shelf"' in markup, "index.html has no job shelf host"
    assert '<body>' in markup and "</main>" in markup
    assert markup.index('id="jobs"') > markup.index("</main>"), (
        "the shelf host sits inside <main> - a shelf inside a panel disappears with it"
    )
    css = CSS.read_text(encoding="utf-8")
    assert ".job-shelf {" in css, "the shelf host has no styling"
    assert "position: fixed" in css.split(".job-shelf {")[1].split("}")[0], (
        "the shelf is in the document flow, so it would scroll away with the panel content"
    )


def test_every_panel_registers_and_closes_its_shelf_row() -> None:
    """A row that is opened and never closed is a lie about what is running; a job that never opens one
    is invisible from every other tab."""
    app = source("app.js")
    assert "createJobShelf({" in app and 'host: $("jobs")' in app, "app.js does not create the shelf"
    assert app.count("jobs: jobShelf,") == 4, (
        "the four panels that run a job (batch/autotag/scale/crop) must all be handed the shelf, got %d"
        % app.count("jobs: jobShelf,")
    )
    assert "cacheShelfJob = jobShelf.start({" in app, "the cache rebuild never registers a row"
    assert "paintShelfCacheJob();" in app, "the cache row is never repainted or updated"

    for name in ("autotag.js", "batch.js", "scale.js", "crop.js"):
        body = source(name)
        assert "shelf.start({" in body, "%s never registers a job with the shelf" % name
        assert "shelfJob.update(" in body, "%s never pushes progress to its row" % name
    # Every terminal path (done, a lost stream, a cancel, the watchdog) goes through one function per
    # panel; closing the row anywhere else would leave a ghost on the shelf for the paths that skip it.
    for name, marker in (
        ("autotag.js", "function closeStream() {"),
        ("batch.js", "function closeWriteStream() {"),
        ("scale.js", "function closeStream() {"),
        ("crop.js", "function closeTagStream() {"),
    ):
        body = source(name)
        assert marker in body, "%s has no single job-end function" % name
        assert "shelfJob.finish();" in body.split(marker)[1][:400], (
            "%s does not close its shelf row in the function every terminal path goes through" % name
        )


def test_a_shelf_cancel_never_locks_the_panel_for_good() -> None:
    """A row's cancel goes through the panel's own path, so it inherits that path's watchdog."""
    batch = source("batch.js")
    assert "const CANCEL_WATCHDOG_MS = 30000;" in batch, "the batch cancel has no watchdog (the others do)"
    assert "function armWriteCancelWatchdog() {" in batch, "nothing unlocks a cancel that never answers"
    assert "armWriteCancelWatchdog();" in batch, "the watchdog is defined but never armed"
    assert "async function cancelWrite() {" in batch, "the panel has no single cancel path for the shelf to call"
    assert 'cancelButton.addEventListener("click", () => { void cancelWrite(); });' in batch, (
        "the button and the shelf would take two different paths"
    )
    assert 'cancel: () => { void cancelWrite(); },' in batch, "the shelf row does not call the panel's cancel"


def test_the_job_shelf_behaves_headlessly() -> None:
    """Two jobs at once, one row each, cancel once, and the shelf empties when the rows close."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("no node on this machine")
    assert HARNESS.is_file(), "missing headless behavior fixture %s" % HARNESS
    done = subprocess.run(
        [node, str(HARNESS), str(WEB)],
        capture_output=True, encoding="utf-8", errors="replace",
    )
    assert done.returncode == 0, "job shelf behavior criteria failed:\n%s\n%s" % (done.stdout, done.stderr)
    assert "jobs harness OK" in done.stdout


def _run_harness(path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("no node on this machine")
    assert path.is_file(), "missing headless behavior fixture %s" % path
    done = subprocess.run(
        [node, str(path), str(WEB)],
        capture_output=True, encoding="utf-8", errors="replace",
    )
    assert done.returncode == 0, "%s failed:\n%s\n%s" % (path.name, done.stdout, done.stderr)


def test_a_running_job_survives_a_page_reload() -> None:
    """A reload used to wipe the running jobs while the backend kept writing. The stream replays its whole
    event log, so the id is the only thing missing - and it is bookmarked in sessionStorage."""
    store = source("jobstore.js")
    assert "export function readRunningJobs()" in store, "nothing reads the bookmarked jobs"
    assert "export function rememberJob(entry)" in store, "a job is never bookmarked"
    assert "export function forgetJob(panel, jobId)" in store, "a finished job keeps its bookmark"
    assert "globalThis.sessionStorage" in store, "the bookmark does not use session storage"

    for name, panel in (("autotag.js", "autotag"), ("batch.js", "batch"), ("scale.js", "scale")):
        body = source(name)
        assert "rememberJob({ panel: \"%s\"" % panel in body, "%s never bookmarks its job" % name
        assert 'forgetJob("%s"' % panel in body, "%s never clears its bookmark" % name
        assert "resume(" in body, "%s cannot pick a job back up after a reload" % name
        assert "runningJobId" in body, "%s does not track the id it bookmarked" % name

    app = source("app.js")
    assert 'rememberJob({ panel: "cache"' in app, "the cache rebuild is never bookmarked"
    assert 'forgetJob("cache"' in app, "the cache rebuild keeps its bookmark after it ends"
    assert "function resumeRunningJobs()" in app, "nothing reads the bookmarks at startup"
    assert "resumeRunningJobs();" in app.split("async function boot()")[1], (
        "the bookmarks are never read while booting, so a reload still shows nothing"
    )
    for call in (
        'batchPanel.resume(entry.jobId, entry.total)',
        'autotagPanel.resume(entry.jobId, entry.total, entry.task)',
        'scalePanel.resume(entry.jobId, entry.total)',
        'resumeCacheJob(entry.jobId, entry.total)',
    ):
        assert call in app, "a resumed job is not handed to its panel: %s" % call


def test_the_jobstore_behaves_headlessly() -> None:
    """One entry per panel, and a storage that is absent, corrupt or hostile reads as "no jobs"."""
    _run_harness(STORE_HARNESS)


def test_every_module_imports_the_helpers_it_calls() -> None:
    """An undefined identifier is a ReferenceError the moment the factory runs.

    On 2026-09-19 autotag.js called createScopeControl without importing it: the module still *parsed*, no
    harness constructed the tagger panel, and the whole suite stayed green while the app could not start.
    The locale harness now constructs every panel; this is the general net underneath it.
    """
    exported: dict[str, str] = {}
    for path in sorted(JS.glob("*.js")):
        for name in re.findall(r"^export (?:async )?(?:function|const|class) (\w+)", path.read_text(encoding="utf-8"), re.M):
            exported[name] = path.name
    offenders = []
    for path in sorted(JS.glob("*.js")):
        body = path.read_text(encoding="utf-8")
        # Imports can be multi-line (app.js's strings.js import is), so the whole statement is matched
        # rather than the lines that start with "import" - the first version of this criterion reported
        # six false offenders in app.js for exactly that reason.
        imports = " ".join(re.findall(r"import\s+[^;]*?from\s+\"[^\"]+\"", body, re.S))
        # A file may define its own helper under a name another module also exports (tags.js has its own
        # normalizeTag, strings.js exports one): a local definition is not a missing import.
        defined = set(re.findall(r"(?:function|const|let|var|class)\s+(\w+)", body))
        for name, module in exported.items():
            if module == path.name or name in defined:
                continue
            if re.search(r"(?<![\w.])%s\s*\(" % re.escape(name), body) and not re.search(
                r"\b%s\b" % re.escape(name), imports
            ):
                offenders.append("%s calls %s() (exported by %s) without importing it" % (path.name, name, module))
    assert not offenders, "\n".join(sorted(set(offenders)))


def test_the_new_job_copy_exists_in_every_pack() -> None:
    keys = (
        "cache.rebuilding", "batch.writing", "scale.cancelAlreadyGone", "scale.cancelTimeout",
        "cache.cancelTimeout", "autotag.cancelling", "autotag.modelsLoadFailed",
        "autotag.previewTooManyNote", "autotag.diffStaleScope", "batch.cancelTimeout",
    )
    for name in ("zh-CN.js", "en.js", "ja.js"):
        text = (LOCALES / name).read_text(encoding="utf-8")
        for key in keys:
            match = re.search(r'"' + re.escape(key) + r'":\s*"([^"]*)"', text)
            assert match is not None, "%s has no %s" % (name, key)
            assert match.group(1).strip(), "%s %s is empty" % (name, key)


def test_a_preview_that_found_nothing_does_not_re_arm_run() -> None:
    """An empty preview is not "no preview yet": /run would only answer autotag.previewEmpty."""
    body = source("autotag.js")
    assert "if (preview && preview.items.length === 0) {" in body, (
        "the run button is re-armed by refreshActionButtons() right after an empty preview"
    )
    assert "else refreshRunButton();" not in body, "the empty-preview rule still lives at the call site"


def test_an_over_limit_scope_disables_preview_and_says_why() -> None:
    """Section 4.5 (2): the backend 400s a preview over the cap, so the click must not be how you learn it."""
    body = source("autotag.js")
    assert "function previewTooLarge() {\n    return scopePaths().length > PREVIEW_MAX_IMAGES;\n  }" in body, (
        "the preview button does not actually consult the cap"
    )
    assert "previewButton.disabled = running || !models.some(isInstalled) || previewTooLarge();" in body, (
        "preview is still clickable on a scope the backend refuses"
    )
    assert 't("autotag.previewTooManyNote", { n, max: PREVIEW_MAX_IMAGES })' in body, (
        "the disabled button gives no reason on screen (a title is not reachable for everyone)"
    )
    assert "refreshActionButtons();\n  }" in body, "a scope change never re-evaluates the preview button"


def test_a_cancel_in_flight_says_so_and_keeps_its_lock() -> None:
    """The backend finishes the current image after accepting a cancel: tagging and stopping must not look alike."""
    body = source("autotag.js")
    assert "function paintCancelButton() {" in body, "the cancel button has no state of its own"
    assert "cancelling = true;\n      paintCancelButton();" in body, "an accepted cancel still looks like a running job"
    assert "if (!accepted) cancelButton.disabled = false;" in body, (
        "a refused cancel leaves the button dead for the rest of the job"
    )
    assert "cancelling = false;\n    paintCancelButton();" in body, "no terminal path clears the cancelling state"
    assert "paintCancelButton();\n    paintDiff();" in body, (
        "a language switch repaints the diff but leaves the cancel label in the old state"
    )


def test_the_cache_cancel_can_be_retried_and_never_locks_for_good() -> None:
    app = source("app.js")
    assert "const CACHE_CANCEL_WATCHDOG_MS = 30000;" in app, (
        "the cache panel has no watchdog while the tagger and the scale panel both do"
    )
    assert "function armCacheCancelWatchdog() {" in app, "an accepted cancel that never completes locks the panel"
    # The cancel itself is a module-scope function now (the command palette runs the same one the
    # button does), so its body is indented by two - the criterion follows the code, not the
    # indentation it used to have while it was a closure inside setupToolbar.
    assert "armCacheCancelWatchdog();\n  try {" in app, "the watchdog is defined but never armed"
    assert "clearCacheCancelWatchdog();\n    cacheCancelling = false;\n    paintCacheStatus();" in app, (
        "a failed cancel request leaves the panel claiming it is cancelling"
    )
    assert '$("btn-cache-cancel").addEventListener("click", () => { void cancelCacheRebuild(); });' in app, (
        "the cancel button no longer runs the shared cancel function"
    )
    assert "function closeCacheStream() {\n  clearCacheCancelWatchdog();" in app, (
        "the watchdog outlives the stream it was armed for"
    )
