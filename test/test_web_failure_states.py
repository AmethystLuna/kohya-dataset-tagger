"""Failure states: what the app shows when something did not work.

Three findings from the 2026-09 UX audit, all "a failure is indistinguishable from success":

1. A failed cache-status or frequency load left the **previous directory** data on screen - same rows,
   same counts, no marker - so the user acted on data belonging to a directory they had left.
2. The connection pill was painted once at boot and never re-checked, so it kept claiming
   "connected" after the backend died, while every action failed with a toast that vanished.
3. Error toasts removed themselves after 12 s and carried no action: nothing to retry, nothing to
   dismiss deliberately, and `app.retry` / `app.dismiss` sat unused in all three packs.

The frequency half is behavioural in `test/fixtures/filter_harness.mjs` (a failed load clears the
rows, says why, and a successful load clears that state); the rest is pinned here statically,
because `app.js` has no headless harness.
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WEB = REPO / "src" / "kohya_dataset_tagger" / "web"
JS = WEB / "js"
CSS = WEB / "css" / "app.css"


def source(name: str) -> str:
    return (JS / name).read_text(encoding="utf-8")


def test_errors_are_sticky_and_carry_retry_and_dismiss() -> None:
    app = source("app.js")
    assert 'retry.textContent = t("app.retry");' in app, "an error toast offers no retry"
    assert 'dismiss.textContent = t("app.dismiss");' in app, "an error toast cannot be dismissed"
    assert 'const sticky = kind === "error" || Boolean(action && typeof action.run === "function");' in app, (
        "errors must not retire on their own (and the transient kinds still must)"
    )
    assert "if (!sticky) window.setTimeout(close, TOAST_MS);" in app, (
        "the sticky rule is computed but never applied to the timer"
    )
    assert 'run.textContent = action.label;' in app, (
        "a caller that can take the action back has no way to say so on the toast"
    )
    css = CSS.read_text(encoding="utf-8")
    assert "max-height: 60vh; overflow: auto;" in css, (
        "the toast stack has no ceiling, so sticky errors can cover the page"
    )


def test_the_connection_pill_is_re_checked() -> None:
    app = source("app.js")
    assert "const HEALTH_POLL_MS = 10000;" in app, "no health re-check interval"
    assert "window.setInterval(() => { void checkHealth(); }, HEALTH_POLL_MS);" in app, (
        "the interval is never started, so the pill can lie about the server"
    )


def test_a_failed_cache_load_clears_the_previous_directory_subject() -> None:
    app = source("app.js")
    assert "let cacheFailed = false;" in app, "a failed load has no state of its own"
    assert "function paintCacheStatus() {" in app, "there is no single place that paints the cache status"
    # The wording moved into readiness.js:cacheStatusText on 2026-09-20, because the readiness strip
    # shows the same claim about the same number and a second wording site is how they drift apart.
    readiness = source("readiness.js")
    assert 'state.failed ? t("cache.loadFailed") : t("cache.statusLine"' in readiness, (
        "the status line reports a count for a load that failed"
    )
    assert "status.textContent = cacheStatusText(view);" in app, (
        "the cache tab no longer paints through the shared status wording"
    )
    assert '$("cache-empty").hidden = cacheBusy || cacheFailed || staleCaches.length !== 0;' in app, (
        "a failed load still claims there is no stale cache"
    )
    assert "staleCaches = [];\n    cacheFailed = true;" in app, "the previous listing survives a failed load"
    assert "cacheFailed = false;" in app, "a successful load never clears the failure"
    assert 'retry: () => { void loadCacheStatus(); },' in app, "a failed cache load cannot be retried"


def test_a_failed_frequency_load_tells_the_panel() -> None:
    app = source("app.js")
    assert 'filterPanel.setFrequencyFailed(t("common.loadFailed", { message: describeError(err) }));' in app, (
        "the frequency table keeps the previous directory rows after a failure"
    )
    assert 'retry: () => { void loadFrequency(); },' in app, "a failed frequency load cannot be retried"
    body = source("filter.js")
    assert "function setFrequencyFailed(message) {" in body, "the panel has no failed state"
    assert "frequencyError = \"\";" in body, "a successful load does not clear the failed state"
    assert "setFrequencyFailed," in body, "the failed state is not on the panel API"


def test_a_directory_scan_is_visible() -> None:
    """A listing request that takes a second must not look like a finished empty directory."""
    app = source("app.js")
    assert "function setScanning(value) {" in app, "the scan has no in-flight state"
    assert 'el.setAttribute("aria-busy", "true");' in app, "a screen reader is not told the panel is loading"
    assert "setScanning(true);\n  try {\n    payload = await api.listDir(path, recursive);" in app, (
        "the directory request is not wrapped in the busy state"
    )
    assert "} finally {\n    setScanning(false);\n  }" in app, "the busy state is never released"
    css = CSS.read_text(encoding="utf-8")
    assert "#dir-list.is-busy, .gallery.is-busy, .breadcrumb.is-busy { opacity: .55; }" in css, (
        "the busy class has no styling, so nothing changes on screen"
    )


def test_a_failed_model_list_is_not_an_empty_model_list() -> None:
    """The toast retires; the difference between "could not ask" and "nothing is installed" must not."""
    body = source("autotag.js")
    assert "let modelsFailed = false;" in body, "a failed model listing has no state of its own"
    assert "modelsFailed = true;\n      paintModelList();\n      paintModelNote();" in body, (
        "the failure is announced only by a toast that removes itself"
    )
    assert "modelsFailed = false;" in body, "a successful reload never clears the failure"
    assert 'modelNote.textContent = t("autotag.modelsLoadFailed");' in body, (
        "the note under the dropdown still reads as 'nothing installed'"
    )
    assert 'row.append(el("span", "hint", t("autotag.modelsLoadFailed")));' in body, (
        "the model list stays blank, so the panel looks like a build without models"
    )


def test_a_lost_cache_stream_is_not_reported_as_a_fact() -> None:
    """The rebuild deleted an unknown number of caches: repainting the old rows states what nobody has checked."""
    app = source("app.js")
    assert "staleCaches = [];\n      cacheFailed = true;\n      renderCacheList();\n      void loadCacheStatus();" in app, (
        "a lost stream leaves the pre-rebuild list on screen as if it were current"
    )
