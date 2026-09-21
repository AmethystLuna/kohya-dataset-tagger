/**
 * The readiness strip (T1.4): the one place that answers "can I train yet?".
 *
 * Three things decide it, and each segment is the action that fixes it:
 *
 *   - images with no caption     -> the gallery's own "select the images missing a caption" action
 *   - stale text-encoder caches  -> the cache rebuild: the same confirmation and the same request the
 *                                   cache tab's own button makes
 *   - dataset.toml               -> whether the dataset root really has one (a read-only stat the
 *                                   backend answers, section 4.16) plus the export panel's own
 *                                   checks, opened at the panel that ran them
 *
 * **Every number on the strip is a number another surface already owns.** The caption count is the
 * counts object /api/fs/list returned (app.js paints it into #dir-counts from the same call), the
 * cache segment is the string the cache tab's own status line is painted with (cacheStatusText
 * below), and the export segment is the summary the export panel computed for its own summary line.
 * Nothing here fetches, counts or recomputes: a fourth source of truth would drift against the other
 * three, and the drift would be invisible.
 *
 * The strip is a page-header row (index.html, inside .topbar), so it is on screen on every tab - that
 * is the whole point - and it stays out of the right column, where the scope strip and the caption
 * dock already compete for height.
 */
import { samePath } from "./navscroll.js";
import { t } from "./strings.js";

//: One state class at a time, so a repaint cannot leave the previous state behind.
const STATES = ["is-ok", "is-todo", "is-bad", "is-busy"];

/**
 * How many caches are stale, in the one wording the cache tab and the strip share.
 *
 * The cache panel's status line and the strip's cache segment are the same claim about the same
 * number, so they are the same function: a second formatting site is how the tab ends up saying
 * "3 possibly stale entries" while the strip says 2.
 *
 * view is app.js's cacheView(): {busy, done, total, failed, stale}.
 */
export function cacheStatusText(view) {
  const state = view || {};
  if (state.busy) {
    return t("cache.progress", { done: Number(state.done) || 0, total: Number(state.total) || 0 });
  }
  return state.failed ? t("cache.loadFailed") : t("cache.statusLine", { n: Number(state.stale) || 0 });
}

/**
 * Build the strip over `host` and return its four setters.
 *
 * The strip owns no state of its own: every setter takes a value the app already holds, and every
 * paint reads only what was handed in. relabel() repaints from those values with no request, which is
 * what a language switch needs.
 */
export function createReadinessStrip(options) {
  const config = options || {};
  const host = config.host;
  const segments = {};
  //: The counts object of the open directory, the cache panel's view, and the export panel's last
  //: report - each one owned by the surface that produced it, only borrowed here.
  let counts = null;
  let cache = null;
  //: The read-only fact about <dataset root>/dataset.toml (app.js asks section 4.16's endpoint for it)
  //: and the export panel's own report for the directory it was run on. They are kept apart on
  //: purpose: one is what is on disk, the other is what a plan would be.
  let fileFact = null;
  let report = null;

  const make = (name) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "btn btn-mini readiness-item";
    button.dataset.segment = name;
    host.append(button);
    return button;
  };
  segments.captions = make("captions");
  segments.cache = make("cache");
  segments.toml = make("toml");

  function setState(segment, state) {
    for (const name of STATES) segment.classList.toggle(name, name === state);
  }

  function fire(handler) {
    if (typeof handler === "function") handler();
  }

  segments.captions.addEventListener("click", () => { fire(config.onSelectMissingCaptions); });
  // Retry or rebuild? That is app state, not strip state - the strip only reports the click.
  segments.cache.addEventListener("click", () => { fire(config.onCacheAction); });
  segments.toml.addEventListener("click", () => { fire(config.onOpenExport); });

  function paintCaptions() {
    const segment = segments.captions;
    const images = counts && typeof counts.images === "number" ? counts.images : null;
    const missing = counts && typeof counts.missing_captions === "number" ? counts.missing_captions : null;
    // No number, no claim: a count nobody reported hides the segment instead of reporting a zero -
    // the same rule the cache segment follows when the status request failed. And a directory with
    // no images at all has nothing to say about captions: "all captioned" above a level of folder
    // cards (the dataset root, where everyone starts) would be a comfort nobody earned.
    segment.hidden = missing === null || images === 0;
    if (segment.hidden) {
      // A hidden segment says nothing at all. Leaving the last claim in the node would make "hidden
      // means no claim" depend on the order the states happened to be painted in.
      segment.textContent = "";
      segment.disabled = true;
      return;
    }
    if (missing > 0) {
      segment.textContent = t("readiness.captionsMissing", { n: missing });
      segment.disabled = false;
      setState(segment, "is-todo");
    } else {
      segment.textContent = t("readiness.captionsOk");
      segment.disabled = true;
      setState(segment, "is-ok");
    }
  }

  function paintCache() {
    const segment = segments.cache;
    const view = cache || { busy: false, done: 0, total: 0, failed: false, stale: 0 };
    segment.textContent = cacheStatusText(view);
    if (view.busy) {
      segment.disabled = true;
      setState(segment, "is-busy");
    } else if (view.failed) {
      // "Could not ask" is not "none": the segment stays clickable, because the click is the retry.
      segment.disabled = false;
      setState(segment, "is-bad");
    } else if (Number(view.stale) > 0) {
      segment.disabled = false;
      setState(segment, "is-todo");
    } else {
      segment.disabled = true;
      setState(segment, "is-ok");
    }
  }

  /**
   * The third segment: does the dataset root really have the file the trainer reads?
   *
   * Two inputs, and the order between them is the point. `fileFact` is what the backend stat'ed for
   * the **dataset root**; `report` is the export panel's own checks. The export endpoint is pure
   * computation - it writes nothing - so a clean report is **not** a file on disk, and "ready" may
   * only be said once something is really there. The fact therefore gates the claim, and the report
   * can only make the segment worse (warnings, skipped directories, an inconsistent plan), never
   * better - and only while it describes that same root.
   */
  function paintToml() {
    const segment = segments.toml;
    // Always clickable: even a clean state is worth opening, because that is where the toml is made.
    segment.disabled = false;
    if (!fileFact) {
      // No answer yet (nothing asked, or the dataset changed under it): the strip's own state, and
      // deliberately not a claim about the file.
      segment.textContent = t("readiness.tomlUnchecked");
      setState(segment, "is-todo");
      return;
    }
    if (fileFact.failed) {
      // "Could not ask" is never "not there" - the rule the cache segment already follows. The click
      // retries (app.js's action), so this state is not a dead end either.
      segment.textContent = t("readiness.tomlUnread");
      setState(segment, "is-bad");
      return;
    }
    if (!fileFact.exists) {
      // A fact now, not a guess: the backend looked at <dataset root>/dataset.toml.
      segment.textContent = t("readiness.tomlMissing");
      setState(segment, "is-todo");
      return;
    }
    // The file is there. The panel's report is the only thing left that can be wrong with it - and
    // only when it was run on this same root; a report for the directory on screen describes another
    // file and must not answer for this one.
    const checked = report && samePath(report.root, fileFact.root) ? report : null;
    const summary = (checked && checked.summary) || {};
    const problems = checked ? Number(checked.problems) || 0 : 0;
    const skipped = Number(summary.skipped) || 0;
    const warnings = Number(summary.warnings) || 0;
    if (problems > 0) {
      segment.textContent = t("readiness.tomlInconsistent", { n: problems });
      setState(segment, "is-bad");
    } else if (skipped > 0) {
      // The export panel's own heading with the export panel's own number - the most severe thing it
      // is currently reporting, in the words it is reporting it in.
      segment.textContent = t("export.skippedTitle", { n: skipped });
      setState(segment, "is-todo");
    } else if (warnings > 0) {
      segment.textContent = t("export.warningsTitle", { n: warnings });
      setState(segment, "is-todo");
    } else {
      segment.textContent = t("readiness.tomlReady");
      setState(segment, "is-ok");
    }
  }

  function relabel() {
    paintCaptions();
    paintCache();
    paintToml();
  }

  /** The open directory's counts, exactly as the listing returned them - null closes the strip. */
  function setCounts(next) {
    counts = next || null;
    // Without a directory there is no question to answer, so the row is not spent.
    host.hidden = counts === null;
    paintCaptions();
  }

  function setCache(view) {
    cache = view || null;
    paintCache();
  }

  /**
   * The read-only fact about <dataset root>/dataset.toml, exactly as the backend reported it:
   * {root, exists, failed}. null = nothing is known (no directory, or a dataset that changed).
   */
  function setTomlFile(fact) {
    fileFact = fact || null;
    paintToml();
  }

  /** The export panel's own report ({root, summary, problems}); it only counts while the file is there. */
  function setToml(next) {
    report = next || null;
    paintToml();
  }

  relabel();
  host.hidden = true;
  return { setCounts, setCache, setToml, setTomlFile, relabel };
}
