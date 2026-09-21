/**
 * Headless behaviour harness for the readiness strip (web/js/readiness.js).
 *
 * The defect this pins: "can I train yet?" was answerable only by visiting three tabs and reading
 * three panels, and the three answers were three different kinds of state. The strip shows all three
 * on every tab - and, more important than showing them, it must show **the same numbers** those
 * panels show. A strip that counts for itself is a fourth source of truth, so every criterion below
 * compares the painted text with the wording and the value the owning surface uses, not with a
 * literal. The one that has to go red when the two disagree is the cache segment: its text must equal
 * cacheStatusText(view), the exact function app.js paints the cache tab's status line with.
 *
 * Usage: node readiness_harness.mjs <absolute path to web/>
 */
import { pathToFileURL } from "node:url";

const WEB = process.argv[2];
if (!WEB) throw new Error("usage: node readiness_harness.mjs <web dir>");
const moduleUrl = (rel) => pathToFileURL(WEB + "/" + rel).href;

function assert(condition, message) {
  if (!condition) throw new Error("readiness harness: " + message);
}

class FakeClassList {
  constructor() { this.items = new Set(); }
  add(name) { this.items.add(name); }
  remove(name) { this.items.delete(name); }
  contains(name) { return this.items.has(name); }
  toggle(name, on) { if (on) this.items.add(name); else this.items.delete(name); }
}

class FakeNode {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase();
    this.children = [];
    this.classList = new FakeClassList();
    this.dataset = {};
    this.attributes = {};
    this.listeners = {};
    this.style = {};
    this.textContent = "";
    this.className = "";
    this.hidden = false;
    this.disabled = false;
    this.type = "";
    this.parentNode = null;
  }
  append(...nodes) {
    for (const node of nodes) {
      if (node && typeof node === "object") node.parentNode = this;
      this.children.push(node);
    }
  }
  replaceChildren(...nodes) { this.children = []; this.append(...nodes); }
  setAttribute(name, value) { this.attributes[name] = value; }
  getAttribute(name) { return this.attributes[name]; }
  addEventListener(type, handler) { (this.listeners[type] ||= []).push(handler); }
  removeEventListener(type, handler) {
    this.listeners[type] = (this.listeners[type] || []).filter((item) => item !== handler);
  }
  fire(type) {
    for (const handler of [...(this.listeners[type] || [])]) handler({ preventDefault() {}, target: this });
  }
}

globalThis.window = {
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  addEventListener() {},
  removeEventListener() {},
};
globalThis.CustomEvent = class CustomEvent {
  constructor(type, init) { this.type = type; this.detail = init && init.detail; }
};
globalThis.document = {
  documentElement: { lang: "" },
  title: "",
  body: new FakeNode("body"),
  createElement: (tag) => new FakeNode(tag),
  querySelectorAll: () => [],
  addEventListener() {},
  removeEventListener() {},
  dispatchEvent() { return true; },
};

const strings = await import(moduleUrl("js/strings.js"));
const readinessMod = await import(moduleUrl("js/readiness.js"));
const en = (await import(moduleUrl("js/locales/en.js"))).STRINGS;
const { t } = strings;

/** The English pack's own value with its placeholders filled - what a painted label must equal after
 *  a switch to en, without asking t() (which would make the comparison true by construction). */
function fromPack(pack, key, params) {
  return String(pack[key]).replace(/\{(\w+)\}/g, (match, name) => (
    Object.prototype.hasOwnProperty.call(params || {}, name) ? String(params[name]) : match
  ));
}

// ---- the strip, built the way app.js builds it -----------------------------
const host = new FakeNode("div");
const clicks = [];
const strip = readinessMod.createReadinessStrip({
  host,
  onSelectMissingCaptions: () => clicks.push("captions"),
  onCacheAction: () => clicks.push("cache"),
  onOpenExport: () => clicks.push("export"),
});
const segment = (name) => host.children.find((node) => node.dataset.segment === name);
const textOf = (name) => segment(name).textContent;

assert(host.children.length === 3, "the strip must have exactly three segments, got " + host.children.length);
assert(host.hidden === true, "the strip must start hidden: no directory is open yet");
for (const name of ["captions", "cache", "toml"]) {
  assert(segment(name), "the strip has no " + name + " segment");
  assert(segment(name).disabled !== undefined, "a segment must be a button (disabled is settable)");
}

// ---- segment 1: the caption count the top bar already paints ---------------
strip.setCounts({ images: 5, captions: 3, missing_captions: 2 });
assert(host.hidden === false, "a directory with counts must show the strip");
assert(
  textOf("captions") === t("readiness.captionsMissing", { n: 2 }),
  "the caption segment must state the missing count, got " + JSON.stringify(textOf("captions"))
);
assert(segment("captions").disabled === false, "the caption segment is an action while captions are missing");
assert(segment("captions").classList.contains("is-todo"), "a missing caption must read as work to do");

segment("captions").fire("click");
assert(clicks.join(",") === "captions", "the caption segment must run the select-missing action, got " + clicks.join(","));

strip.setCounts({ images: 5, captions: 5, missing_captions: 0 });
assert(
  textOf("captions") === t("readiness.captionsOk"),
  "no missing caption must read as done, got " + JSON.stringify(textOf("captions"))
);
assert(segment("captions").disabled === true, "nothing to select: the segment is an answer, not an action");

// Recovery: a later listing with missing captions arms it again.
strip.setCounts({ images: 5, captions: 3, missing_captions: 2 });
assert(segment("captions").disabled === false, "the segment must come back after the count comes back");

// A directory with no images has nothing to say about captions. The dataset root is exactly that
// case - a level of folder cards, where everyone starts - and "captions complete" over it is a
// comfort nobody earned.
strip.setCounts({ images: 0, captions: 0, missing_captions: 0 });
assert(host.hidden === false, "a directory with no images still has caches and a toml to report");
assert(
  segment("captions").hidden === true,
  "a directory with no images must hide the caption segment, got " + JSON.stringify(textOf("captions"))
);
assert(
  textOf("captions") === "",
  "a hidden caption segment still carries a claim: " + JSON.stringify(textOf("captions"))
);
// Recovery: the next directory has images again.
strip.setCounts({ images: 5, captions: 5, missing_captions: 0 });
assert(segment("captions").hidden === false, "the caption segment must come back with a directory that has images");
assert(
  textOf("captions") === t("readiness.captionsOk"),
  "the recovered segment must read as done, got " + JSON.stringify(textOf("captions"))
);

// A count nobody reported is not a zero: the segment says nothing instead of claiming completeness.
strip.setCounts({ images: 5, captions: 3 });
assert(segment("captions").hidden === true, "an unknown caption count must hide the segment, not report 0");
assert(
  textOf("captions") === "",
  "an unknown caption count was painted as a claim: " + JSON.stringify(textOf("captions"))
);
strip.setCounts(null);
assert(host.hidden === true, "closing the directory must close the strip");

// ---- segment 2: the cache number the cache tab already prints ---------------
// THE agreement criterion: the strip's sentence is the very string app.js paints into
// #cache-status-line (paintCacheStatus() calls cacheStatusText(cacheView())), so a strip that
// counted for itself - or a tab that stopped sharing - turns this red.
const staleView = { busy: false, done: 0, total: 0, failed: false, stale: 2 };
strip.setCache(staleView);
assert(
  textOf("cache") === readinessMod.cacheStatusText(staleView),
  "the strip and the cache tab disagree about the stale count: " + JSON.stringify(textOf("cache"))
);
assert(
  textOf("cache") === t("cache.statusLine", { n: 2 }),
  "the cache segment must be the cache tab's own wording, got " + JSON.stringify(textOf("cache"))
);
assert(segment("cache").disabled === false, "stale caches are an action");
segment("cache").fire("click");
assert(clicks.join(",") === "captions,cache", "the cache segment must run the cache action");

// Failure: "could not ask" is never "none".
const failedView = { busy: false, done: 0, total: 0, failed: true, stale: 0 };
strip.setCache(failedView);
assert(
  textOf("cache") === t("cache.loadFailed"),
  "a failed status load must say so, got " + JSON.stringify(textOf("cache"))
);
assert(
  textOf("cache") !== t("cache.statusLine", { n: 0 }),
  "a failed /api/cache/status was painted as 'no stale cache' - the exact claim the strip must not make"
);
assert(segment("cache").disabled === false, "a failed status load must stay clickable: the click is the retry");
assert(segment("cache").classList.contains("is-bad"), "a failed status load must not look like a result");

// Recovery: the retry succeeded.
strip.setCache({ busy: false, done: 0, total: 0, failed: false, stale: 0 });
assert(
  textOf("cache") === t("cache.statusLine", { n: 0 }),
  "after a successful retry the segment must report the real count, got " + JSON.stringify(textOf("cache"))
);
assert(segment("cache").disabled === true, "nothing stale: not an action");

// While a rebuild runs the segment follows the same progress line the cache tab prints.
strip.setCache({ busy: true, done: 3, total: 10, failed: false, stale: 2 });
assert(
  textOf("cache") === t("cache.progress", { done: 3, total: 10 }),
  "a running rebuild must show its progress, got " + JSON.stringify(textOf("cache"))
);
assert(segment("cache").disabled === true, "a running rebuild cannot be started again");

// ---- segment 3: the file the trainer reads (section 4.16) -------------------
// Two inputs: the read-only fact about <dataset root>/dataset.toml (app.js asks the backend for it)
// and the export panel's own report. The fact gates every claim about the file; the report may only
// make the segment worse, never better.
const ROOT = "F:/dataset";
const OTHER_ROOT = "F:/other-dataset";

strip.setToml(null);
strip.setTomlFile(null);
assert(
  textOf("toml") === t("readiness.tomlUnchecked"),
  "without an answer the segment must say it has not checked, got " + JSON.stringify(textOf("toml"))
);
segment("toml").fire("click");
assert(clicks.join(",") === "captions,cache,export", "the toml segment must open the export panel");

// A request that failed is not a file that is missing: "could not ask" may never turn into a claim -
// the rule the cache segment already follows.
strip.setTomlFile({ root: ROOT, failed: true });
assert(
  textOf("toml") === t("readiness.tomlUnread"),
  "a failed status request must say so, got " + JSON.stringify(textOf("toml"))
);
assert(
  textOf("toml") !== t("readiness.tomlMissing"),
  "a failed /api/dataset/toml/status was painted as 'no dataset.toml' - the exact claim it must not make"
);
assert(segment("toml").classList.contains("is-bad"), "a failed status request must not look like a result");
assert(segment("toml").disabled === false, "the export tab must stay reachable from a failed segment");
segment("toml").fire("click");
assert(clicks.join(",") === "captions,cache,export,export", "the failed segment must still open the export tab");

// Recovery: the retry answered, and the answer is "there is no file".
strip.setTomlFile({ root: ROOT, exists: false });
assert(
  textOf("toml") === t("readiness.tomlMissing"),
  "the backend said the dataset root has no dataset.toml, got " + JSON.stringify(textOf("toml"))
);

// THE drift criterion: the export panel plans a file, it does not write one. A clean report with no
// dataset.toml on disk must never read as ready - that is the disagreement the strip exists to show.
const clean = { root: ROOT, summary: { subsets: 219, images: 1653, skipped: 0, warnings: 0 }, problems: 0 };
strip.setToml(clean);
assert(
  textOf("toml") === t("readiness.tomlMissing"),
  "a clean export report painted the segment ready while the dataset root has no dataset.toml: "
    + JSON.stringify(textOf("toml"))
);
assert(
  textOf("toml") !== t("readiness.tomlReady"),
  "the export panel's report alone produced 'dataset.toml ready' - the report describes a plan, not the disk"
);

// The report belongs to the directory the panel was pointed at, which is not always this dataset's
// root: a report for another directory must not attach its numbers to this root's file.
strip.setTomlFile({ root: ROOT, exists: true });
strip.setToml({ root: OTHER_ROOT, summary: { subsets: 1, images: 4, skipped: 0, warnings: 3 }, problems: 2 });
assert(
  textOf("toml") === t("readiness.tomlReady"),
  "a report for another directory was attached to this root's file: " + JSON.stringify(textOf("toml"))
);

// With the file there, the panel's own checks are what is left to be wrong - in the panel's words.
const warned = { root: ROOT, summary: { subsets: 219, images: 1653, skipped: 0, warnings: 2 }, problems: 0 };
strip.setToml(warned);
assert(
  textOf("toml") === t("export.warningsTitle", { n: warned.summary.warnings }),
  "the warnings segment must carry the export panel's own heading and number, got " + JSON.stringify(textOf("toml"))
);

const skippedReport = { root: ROOT, summary: { subsets: 219, images: 1653, skipped: 1, warnings: 0 }, problems: 0 };
strip.setToml(skippedReport);
assert(
  textOf("toml") === t("export.skippedTitle", { n: skippedReport.summary.skipped }),
  "a skipped directory outranks a warning, got " + JSON.stringify(textOf("toml"))
);

const inconsistent = { root: ROOT, summary: { subsets: 219, images: 1653, skipped: 1, warnings: 3 }, problems: 2 };
strip.setToml(inconsistent);
assert(
  textOf("toml") === t("readiness.tomlInconsistent", { n: inconsistent.problems }),
  "an inconsistent plan must be reported by its own count, got " + JSON.stringify(textOf("toml"))
);
assert(segment("toml").classList.contains("is-bad"), "an inconsistent plan must not look like a warning");

strip.setToml(clean);
assert(
  textOf("toml") === t("readiness.tomlReady"),
  "a clean report with the file on disk must read as ready, got " + JSON.stringify(textOf("toml"))
);
assert(segment("toml").disabled === false, "the toml is always worth opening - that is where it is copied from");

// Recovery in the other direction: the file is deleted under the app, and the next stat says so.
strip.setTomlFile({ root: ROOT, exists: false });
assert(
  textOf("toml") === t("readiness.tomlMissing"),
  "a deleted dataset.toml must go back to 'not generated', got " + JSON.stringify(textOf("toml"))
);

// ---- a language switch repaints all three from held state ------------------
strip.setCounts({ images: 5, captions: 3, missing_captions: 2 });
strip.setCache(staleView);
strip.setTomlFile({ root: ROOT, exists: true });
strip.setToml(warned);
const beforeSwitch = textOf("captions");
assert(strings.setLocale("en"), "the harness must be able to switch to English");
assert(
  textOf("captions") === beforeSwitch,
  "setLocale() must not repaint the strip by itself; relabel() is the contract"
);
strip.relabel();
assert(
  textOf("captions") === fromPack(en, "readiness.captionsMissing", { n: 2 }),
  "relabel() must paint the caption segment in the new language, got " + JSON.stringify(textOf("captions"))
);
assert(
  textOf("cache") === fromPack(en, "cache.statusLine", { n: 2 }),
  "relabel() must paint the cache segment in the new language, got " + JSON.stringify(textOf("cache"))
);
assert(
  textOf("toml") === fromPack(en, "export.warningsTitle", { n: warned.summary.warnings }),
  "relabel() must paint the toml segment in the new language, got " + JSON.stringify(textOf("toml"))
);
assert(
  textOf("toml") !== fromPack(en, "readiness.tomlUnchecked"),
  "the toml segment fell back to 'not checked' on a language switch"
);

console.log("readiness harness OK");
