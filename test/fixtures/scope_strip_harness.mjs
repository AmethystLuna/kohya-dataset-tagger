/**
 * Headless behaviour harness for the column's ONE scope control (web/js/scope.js) as both
 * panels use it: the batch panel and the tagger are built over a single createScopeControl
 * instance, exactly the way app.js builds them.
 *
 * The defect this pins: until 2026-09-20 each panel constructed its own control over its own
 * <select>, so "selected: 12 images" in the tagger and the button in the batch panel acted on
 * different sets of images - one question, two answers. A static test can see that both panels
 * mention a scope; only running them over one control can show that one change of the value is
 * what both of them obey.
 *
 * api.js is not stubbed: only globalThis.fetch is, so the real request builder and the real
 * JSON body are under test.
 *
 * Usage: node scope_strip_harness.mjs <absolute path to web/>
 */
import { pathToFileURL } from "node:url";

const WEB = process.argv[2];
if (!WEB) throw new Error("usage: node scope_strip_harness.mjs <web dir>");

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
    this.value = "";
    this.checked = false;
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
  // autotag.js inserts the over-cap note before the action row (a real DOM API it uses).
  insertBefore(node, reference) {
    if (node && typeof node === "object") node.parentNode = this;
    const index = this.children.indexOf(reference);
    if (index < 0) this.children.push(node);
    else this.children.splice(index, 0, node);
    return node;
  }
  get options() { return this.children.filter((node) => node && node.tagName === "OPTION"); }
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

function find(node, predicate, out = []) {
  if (!node || typeof node !== "object") return out;
  if (predicate(node)) out.push(node);
  for (const child of node.children || []) find(child, predicate, out);
  return out;
}

function assert(condition, message) {
  if (!condition) throw new Error("scope strip harness: " + message);
}

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

globalThis.document = {
  body: new FakeNode("body"),
  activeElement: null,
  createElement: (tag) => new FakeNode(tag),
  createDocumentFragment: () => new FakeNode("fragment"),
  createTextNode: (text) => ({ nodeType: 3, textContent: text }),
  addEventListener() {},
  removeEventListener() {},
};
globalThis.window = { setTimeout, clearTimeout, addEventListener() {}, removeEventListener() {} };

const calls = [];
let queued = [];
globalThis.fetch = async (url, init) => {
  calls.push({ url: String(url), body: init && init.body ? JSON.parse(init.body) : null });
  const payload = queued.shift() || {};
  return { ok: true, status: 200, statusText: "OK", url: String(url), json: async () => payload };
};
globalThis.EventSource = class {
  constructor(url) { this.url = url; this.listeners = {}; this.closed = false; }
  addEventListener(type, handler) { (this.listeners[type] ||= []).push(handler); }
  close() { this.closed = true; }
};

const scopeMod = await import(pathToFileURL(WEB + "/js/scope.js").href);
const batchMod = await import(pathToFileURL(WEB + "/js/batch.js").href);
const autotagMod = await import(pathToFileURL(WEB + "/js/autotag.js").href);
const { t } = await import(pathToFileURL(WEB + "/js/strings.js").href);

// ---- one control, the way app.js builds it ---------------------------------
//: 40 images in the directory: over the tagger's 32-image preview cap, which is what makes the
//: two panels' reactions to ONE change distinguishable from each other.
const selectedPaths = ["/d/2.png"];
const visiblePaths = ["/d/1.png", "/d/2.png"];
const dirPaths = Array.from({ length: 40 }, (_, index) => "/d/" + (index + 1) + ".png");
const recursivePaths = dirPaths.concat(["/d/sub/41.png"]);
//: Which directory the control is looking at, and what the recursive listing holds per directory.
//: Both are mutable: section 6 is about the cache being an answer for ONE directory, and about
//: what happens when that directory's contents change under it.
let currentDir = "/d";
const recursiveByDir = { "/d": recursivePaths.slice(), "/e": ["/e/1.png", "/e/2.png"] };
let recursiveFetches = 0;

const strip = new FakeNode("div");
const select = new FakeNode("select");
strip.append(select);
const scope = scopeMod.createScopeControl({
  select,
  providers: {
    getSelectedPaths: () => selectedPaths.slice(),
    getVisiblePaths: () => visiblePaths.slice(),
    getDirPaths: () => dirPaths.slice(),
  },
  getDirKey: () => currentDir,
  loadRecursive: async () => {
    recursiveFetches += 1;
    return { dir: currentDir, paths: (recursiveByDir[currentDir] || []).slice() };
  },
});

assert(select.children.length === 4, "the control must offer the four scopes once, got " + select.children.length);
assert(select.value === "selected", "the shared default must be the picked images, got " + select.value);

// ---- the two panels over that one control ----------------------------------
const tagsByPath = { "/d/2.png": ["a", "b"], "/d/1.png": ["a", "b"] };
const notices = [];
const batchTabs = new FakeNode("nav");
const batchHost = new FakeNode("aside");
const batchPanel = batchMod.createBatchPanel({
  tabsHost: batchTabs,
  panelHost: batchHost,
  notify: (message, kind) => { notices.push({ message, kind }); },
  confirm: () => true,
  scope,
  getTags: (path) => tagsByPath[path] || null,
  onScopeChange: () => {},
});
const batchRoot = batchHost.children[0];
batchRoot.hidden = false;
batchPanel.onShow();

const elementOptions = {};
for (const name of [
  "modelSelect", "modelNote", "thresholds", "options", "undesired", "alwaysFirst",
  "writeModeSelect", "previewButton", "runButton", "cancelButton", "progress", "progressFill",
  "progressText", "errors", "diffTitle", "diffSummary", "diff",
]) {
  elementOptions[name] = new FakeNode(/Select$/.test(name) ? "select" : "div");
}
// The scope note is inserted before the action row, so the row needs a parent to live in.
const actionRow = new FakeNode("div");
actionRow.append(elementOptions.previewButton, elementOptions.runButton);
const taggerRoot = new FakeNode("aside");
taggerRoot.append(actionRow);
elementOptions.notify = () => {};
elementOptions.confirm = () => true;
elementOptions.scope = scope;
const tagger = autotagMod.createAutotagPanel(elementOptions);
await tick();

const panelButton = (root, text) =>
  find(root, (node) => node.tagName === "BUTTON" && node.textContent === text)[0];

// A model has to be installed before the tagger's buttons mean anything.
queued = [{ models: [{ id: "wd14", installed: true, onnx_path: "/m/wd14.onnx" }] }];
await tagger.loadModels();
await tick();

// ---- 1. one change of the scope, both panels obey it -----------------------
// Nothing here touches the tagger's own state: the preconditions are read from the batch panel
// and from the tagger's public scopePaths(), and then a SINGLE change of the shared select is
// what has to move both of them.
assert(
  JSON.stringify(tagger.scopePaths()) === JSON.stringify(selectedPaths),
  "the tagger must act on the control's value: " + JSON.stringify(tagger.scopePaths()),
);
assert(
  elementOptions.previewButton.disabled === false,
  "1 image is under the preview cap, so the tagger's Preview must be live",
);
assert(
  elementOptions.runButton.disabled === true,
  "with no preview yet and a scope under the cap, Run must stay disabled",
);

select.value = "dir";
select.fire("change");
await tick(); await tick();

assert(select.value === "dir", "the select must hold the new value");
assert(
  JSON.stringify(tagger.scopePaths()) === JSON.stringify(dirPaths),
  "the tagger did not follow the one change: " + JSON.stringify(tagger.scopePaths().length),
);
assert(
  elementOptions.previewButton.disabled === true,
  "40 images are over the tagger's 32-image preview cap, so Preview must be disabled by the same change",
);
assert(
  elementOptions.runButton.disabled === false,
  "over the cap /run is the only path (p5 4.5), so Run must be offered",
);
assert(
  find(taggerRoot, (node) => node.textContent === t("autotag.previewTooManyNote", { n: 40, max: 32 })).length === 1,
  "the tagger must say why Preview is unavailable at the new scope",
);

// The batch panel: the same one change, observed in the request it builds.
queued = [{ dry_run: true, applied: 0, failed: 0, results: [] }];
// The common-tag editor needs every caption in the scope before it can preview (it disables
// Preview until they are read); a stateless op is the shortest path to a request body, and the
// scope it carries is the whole point here.
const opSelect = find(
  batchRoot,
  (node) => node.tagName === "SELECT" && (node.children || []).some((child) => child.value === batchMod.BATCH_COMMON),
)[0];
assert(opSelect, "the batch panel has no op select");
opSelect.value = "normalize";
opSelect.fire("change");
const batchPreviewButton = panelButton(batchRoot, t("batch.preview"));
assert(batchPreviewButton && batchPreviewButton.disabled === false, "the batch Preview must be live with 40 images in scope");
const before = calls.length;
batchPreviewButton.fire("click");
await tick(); await tick(); await tick();
assert(calls.length === before + 1, "the batch preview must issue exactly one request");
assert(
  JSON.stringify(calls[calls.length - 1].body.images) === JSON.stringify(dirPaths),
  "the batch panel must act on the SAME value the tagger reads, got " +
    JSON.stringify(calls[calls.length - 1].body.images.length) + " images",
);

// ---- 2. a scope change after a preview marks the tagger's diff stale --------
select.value = "selected";
select.fire("change");
await tick();
queued = [{ items: [{ image: "/d/2.png", before: "a", after: "a, b", tags: [] }] }];
elementOptions.previewButton.fire("click");
await tick(); await tick(); await tick();
assert(
  String(elementOptions.diffSummary.textContent).indexOf(t("autotag.diffStaleScope")) < 0,
  "a fresh preview of the current scope must not be reported as stale: " + elementOptions.diffSummary.textContent,
);
assert(elementOptions.runButton.disabled === false, "a fresh preview makes Run available");

// One change of the shared value invalidates what the diff describes...
select.value = "filtered";
select.fire("change");
await tick();
assert(
  elementOptions.diffSummary.textContent === t("autotag.diffStaleScope"),
  "a scope change must say the diff describes other images, got " + elementOptions.diffSummary.textContent,
);
// ...and the same change re-gates the buttons through the cap. Run stays available on purpose:
// with a preview that no longer covers the scope /run writes the CURRENT scope behind a
// confirmation that names the count (autotag.js: freshPreview ? preview.paths : scopePaths()).
select.value = "dir";
select.fire("change");
await tick();
assert(
  elementOptions.previewButton.disabled === true,
  "the over-cap scope must disable Preview again",
);
assert(
  elementOptions.runButton.disabled === false,
  "over the cap /run is the way through, so it must stay offered",
);
// Recovery: pointing the one control back at what the diff describes makes it current again.
select.value = "selected";
select.fire("change");
await tick();
assert(
  elementOptions.diffSummary.textContent !== t("autotag.diffStaleScope"),
  "the diff must stop claiming to be stale once the scope covers it again, got " +
    elementOptions.diffSummary.textContent,
);
assert(elementOptions.previewButton.disabled === false, "the under-cap scope must give Preview back");

// ---- 3. the counts are live, and painted by the one painter -----------------
const labelOf = (value) => select.options.filter((option) => option.value === value)[0].textContent;
assert(
  labelOf("selected") === t("scope.selected", { n: 1 }),
  "the selected count is not live: " + labelOf("selected"),
);
assert(labelOf("dir") === t("scope.dir", { n: 40 }), "the directory count is not live: " + labelOf("dir"));
// Changing what a provider reports and repainting must move the label - that is the half the
// per-chunk filter pass depends on (the gallery re-applies the filter once per caption chunk).
visiblePaths.push("/d/3.png");
scope.paint();
assert(
  labelOf("filtered") === t("scope.filtered", { n: 3 }),
  "a repaint must re-read the providers, got " + labelOf("filtered"),
);
// The recursive scope asks for its listing and shows "..." until it has one - never a fake 0.
select.value = "dir_recursive";
select.fire("change");
assert(
  labelOf("dir_recursive") === t("scope.dirRecursive", { n: "\u2026" }),
  "an unloaded recursive listing must read as unknown, got " + labelOf("dir_recursive"),
);
await tick(); await tick(); await tick();
assert(
  labelOf("dir_recursive") === t("scope.dirRecursive", { n: 41 }),
  "the recursive count never arrived: " + labelOf("dir_recursive"),
);
assert(
  JSON.stringify(tagger.scopePaths()) === JSON.stringify(recursivePaths),
  "both panels must read the recursive listing from the same control",
);

// ---- 4. the counts follow the filter from any tab ---------------------------
// The gallery re-applies the filter once per caption chunk and tells the batch panel to
// refreshScope(). That panel is usually closed, and the strip is on screen on every tab - so the
// counts must not sit behind its hidden early-return, which is where the panel's own work belongs.
select.value = "filtered";
select.fire("change");
await tick();
visiblePaths.push("/d/4.png");
batchRoot.hidden = true;
batchPanel.refreshScope();
assert(
  labelOf("filtered") === t("scope.filtered", { n: 4 }),
  "a filter change while the batch tab is closed left the strip stale: " + labelOf("filtered"),
);
batchRoot.hidden = false;
batchPanel.onShow();

// ---- 5. painting again does not build a second control ----------------------
// The strip is pinned for the whole session, and paint()/refresh() run on every directory,
// selection and caption-probe change: a control that appended its options while painting would
// grow a duplicate set of them instead of relabelling the four it has.
for (let index = 0; index < 5; index += 1) scope.refresh();
await tick();
assert(select.children.length === 4, "repainting added options: " + select.children.length);

// ---- 6. the recursive listing is an answer about ONE directory --------------
// Two ways this control used to describe a tree that was not the one on screen, both of them
// reachable in ordinary use:
//   (a) the cache outlived its directory - after navigating, "directory + subdirectories" went on
//       handing out the images of the directory the user had just left, and every panel acting on
//       the scope (tagger, batch, crop, export) acted on those;
//   (b) the cache survived a re-listing of the *same* directory - the crop archives its result
//       beside the source, and until the user happened to navigate away and back, the new files
//       were invisible to the count and to anything exporting that scope.
// A listing answers a question about one directory, so the directory is part of the question; and
// the cache is dropped when app.js installs a fresh listing (scope.invalidate()).
select.value = "dir_recursive";
select.fire("change");
await tick(); await tick(); await tick();
assert(
  labelOf("dir_recursive") === t("scope.dirRecursive", { n: 41 }),
  "the recursive listing did not load: " + labelOf("dir_recursive"),
);
const fetchesAfterLoad = recursiveFetches;

// A plain repaint keeps the cache: switching back and forth must stay free (that is why the
// listing is cached per directory in the first place, and it is the counterweight to (b)).
scope.refresh();
await tick(); await tick();
assert(recursiveFetches === fetchesAfterLoad, "a plain repaint re-fetched the listing");
assert(
  JSON.stringify(tagger.scopePaths()) === JSON.stringify(recursivePaths),
  "a repaint lost the listing the panels act on",
);

// (b) The same directory, re-listed - the crop case: it archives the result beside its source, so
// the tree under the open directory changes while the directory itself does not. app.js drops the
// cache when a fresh listing arrives, which is the only signal this control ever gets.
//
// Checked BEFORE (a) on purpose: dropping the cache is only what makes the difference while the
// cache still belongs to the directory on screen. Run after (a) - when the cache holds another
// directory - the old listing would read as unknown for the other reason, and a criterion written
// in that order passes whether or not invalidate() clears anything (it did, until this line moved).
recursiveByDir["/d"].push("/d/sub/42.png");
scope.invalidate();
assert(
  labelOf("dir_recursive") === t("scope.dirRecursive", { n: "…" }),
  "a dropped listing must read as unknown, not as the number from before: " + labelOf("dir_recursive"),
);
assert(
  JSON.stringify(tagger.scopePaths()) === "[]",
  "a dropped listing is still handed to the panels: " + JSON.stringify(tagger.scopePaths().length),
);
await tick(); await tick(); await tick();
assert(
  labelOf("dir_recursive") === t("scope.dirRecursive", { n: 42 }),
  "the re-listing never arrived, so a file written into the directory stays invisible: " +
    labelOf("dir_recursive"),
);
assert(
  JSON.stringify(tagger.scopePaths()) === JSON.stringify(recursiveByDir["/d"]),
  "the panels still act on the pre-crop listing",
);
assert(
  recursiveFetches === fetchesAfterLoad + 1,
  "dropping the cache must cost exactly one reload: " + recursiveFetches,
);

// (a) Another directory is another question - and the same rule in the other direction.
currentDir = "/e";
scope.refresh();
assert(
  scope.counts().dir_recursive === null,
  "the control reported the PREVIOUS directory's count: " + scope.counts().dir_recursive,
);
assert(
  labelOf("dir_recursive") === t("scope.dirRecursive", { n: "…" }),
  "a listing from another directory must read as unknown, got " + labelOf("dir_recursive"),
);
assert(
  JSON.stringify(tagger.scopePaths()) === "[]",
  "the panels were handed the images of the directory the user had just left: " +
    JSON.stringify(tagger.scopePaths().length),
);
await tick(); await tick(); await tick();
assert(
  labelOf("dir_recursive") === t("scope.dirRecursive", { n: 2 }),
  "the new directory's listing never arrived: " + labelOf("dir_recursive"),
);
assert(
  JSON.stringify(tagger.scopePaths()) === JSON.stringify(["/e/1.png", "/e/2.png"]),
  "the panels did not move to the new directory's listing",
);
currentDir = "/d";
scope.refresh();
assert(
  JSON.stringify(tagger.scopePaths()) === "[]",
  "navigating back handed out the directory that was left instead of reloading",
);
await tick(); await tick(); await tick();
assert(
  labelOf("dir_recursive") === t("scope.dirRecursive", { n: 42 }),
  "the listing for the directory we came back to never arrived: " + labelOf("dir_recursive"),
);
assert(
  recursiveFetches === fetchesAfterLoad + 3,
  "each directory change must cost exactly one reload: " + recursiveFetches,
);

console.log("scope strip harness OK " + JSON.stringify({ scopes: select.children.length, calls: calls.length }));
