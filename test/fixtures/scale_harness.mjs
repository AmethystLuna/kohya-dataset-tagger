/**
 * Headless behaviour harness for web/js/scale.js (section 4.14) plus the shared
 * parameter store it reads its resolution and caption extension from.
 *
 * Static tests can see that a "no upscale" checkbox and a start button exist,
 * never that the panel sends the frozen request body, that a done event without
 * finished:true is ignored, that the shared resolution really reaches the body,
 * or that cancel calls the frozen endpoint. Those are the parts a user acts on,
 * so this drives the real module against a minimal fake DOM, a fake fetch and a
 * fake EventSource.
 *
 * Usage: node scale_harness.mjs <absolute path to web/>
 */
import assert from "node:assert/strict";
import { pathToFileURL } from "node:url";

const WEB = process.argv[2];
if (!WEB) throw new Error("usage: node scale_harness.mjs <web dir>");

const moduleUrl = (rel) => pathToFileURL(WEB + "/" + rel).href;
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

// --- minimal fake DOM ------------------------------------------------------
class FakeNode {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase();
    this.children = [];
    this.dataset = {};
    this.attributes = {};
    this.listeners = {};
    this.style = {};
    this.textContent = "";
    this.className = "";
    this.hidden = false;
    this.disabled = false;
    this.checked = false;
    this.value = "";
    this.title = "";
    this.placeholder = "";
    this.type = "";
  }
  append(...nodes) {
    for (const node of nodes) this.children.push(node);
  }
  replaceChildren(...nodes) {
    this.children = [];
    this.append(...nodes);
  }
  setAttribute(name, value) {
    this.attributes[name] = value;
  }
  getAttribute(name) {
    return this.attributes[name];
  }
  /** HTMLSelectElement.options: what the shared scope control iterates (scope.js paint()). */
  get options() {
    return this.children.filter((node) => node && node.tagName === "OPTION");
  }
  addEventListener(type, handler) {
    (this.listeners[type] ||= []).push(handler);
  }
  fire(type) {
    for (const handler of [...(this.listeners[type] || [])]) handler({ preventDefault() {}, target: this });
  }
}

function findAll(node, predicate, out = []) {
  if (!node || typeof node !== "object") return out;
  if (predicate(node)) out.push(node);
  for (const child of node.children || []) findAll(child, predicate, out);
  return out;
}

function findOne(node, predicate) {
  const found = findAll(node, predicate);
  return found.length > 0 ? found[0] : null;
}

globalThis.document = {
  body: new FakeNode("body"),
  activeElement: null,
  createElement: (tag) => new FakeNode(tag),
};
// The cancel watchdog arms a timer; without a window the panel would throw on
// the cancel path exactly as a browser never would.
globalThis.window = { setTimeout, clearTimeout };

class FakeEventSource {
  constructor(url) {
    this.url = url;
    this.listeners = {};
    this.closed = false;
    FakeEventSource.last = this;
  }
  addEventListener(type, handler) {
    (this.listeners[type] ||= []).push(handler);
  }
  close() {
    this.closed = true;
  }
  fire(type, payload) {
    for (const handler of [...(this.listeners[type] || [])]) {
      handler({ data: JSON.stringify(payload) });
    }
  }
}
FakeEventSource.last = null;
globalThis.EventSource = FakeEventSource;

const calls = [];
//: What /api/scale/cancel answers. "cancelled: false" means the job was already
//: gone when the request arrived - the case that used to lock the panel.
let cancelAnswer = { cancelled: true };
globalThis.fetch = async (url, init) => {
  calls.push({ url: String(url), init: init || {} });
  if (String(url).indexOf("/api/scale/run") >= 0) {
    return { ok: true, status: 202, url, json: async () => ({ job_id: "J1", total: 2 }) };
  }
  if (String(url).indexOf("/api/scale/cancel") >= 0) {
    return { ok: true, status: 202, url, json: async () => cancelAnswer };
  }
  return { ok: true, status: 200, url, json: async () => ({}) };
};

const scale = await import(moduleUrl("js/scale.js"));
const scopeMod = await import(moduleUrl("js/scope.js"));
const trainparams = await import(moduleUrl("js/trainparams.js"));
const { createTrainParams } = trainparams;
const { t } = await import(moduleUrl("js/strings.js"));

/**
 * The column's scope control, built the way app.js builds it, over a mutable list so the harness
 * can change "which images?" the way picking images or switching scope does.
 */
function makeScope(paths, dirKey) {
  const select = new FakeNode("select");
  return scopeMod.createScopeControl({
    select,
    providers: {
      getSelectedPaths: () => paths.slice(),
      getVisiblePaths: () => paths.slice(),
      getDirPaths: () => paths.slice(),
    },
    getDirKey: () => dirKey,
  });
}

// --- 1. pure request builder ----------------------------------------------
const FROZEN_KEYS = [
  "caption_extension", "images", "no_upscale", "optimize", "output_format",
  "overwrite_dataset_toml", "quality",
  "resample", "root", "target_dir", "target_height", "target_width",
  "write_dataset_toml",
];
assert.deepEqual(Object.keys(scale.buildScaleRequest({})).sort(), FROZEN_KEYS);
assert.deepEqual(
  scale.buildScaleRequest({}).images,
  [],
  "an unset list must stay empty: the endpoint refuses an empty list rather than widening it",
);
assert.deepEqual(
  scale.buildScaleRequest({ images: ["F:/d/1.png"] }).images,
  ["F:/d/1.png"],
  "the builder must carry the scope's list through untouched",
);
const defaults = scale.buildScaleRequest({});
assert.equal(defaults.no_upscale, true, "no_upscale must default to true");
assert.equal(defaults.output_format, "png");
assert.equal(defaults.resample, "lanczos");
assert.equal(defaults.quality, 95);
assert.equal(defaults.optimize, true);
assert.equal(defaults.caption_extension, ".txt");
assert.equal(defaults.write_dataset_toml, false);
assert.equal(defaults.overwrite_dataset_toml, false, "replacing an existing dataset.toml must be opt-in");
assert.equal(defaults.target_width, 1536);
// The resample/format domains live in the store now (it validates a remembered name against them).
assert.deepEqual(trainparams.IMAGE_FORMATS, ["png", "jpeg", "webp"]);
assert.deepEqual(trainparams.RESAMPLE_NAMES, ["lanczos", "bilinear", "bicubic", "nearest", "box", "hamming"]);

assert.equal(scale.buildScaleRequest({ noUpscale: false }).no_upscale, false);
assert.equal(scale.buildScaleRequest({ quality: 0 }).quality, 1);
assert.equal(scale.buildScaleRequest({ quality: 400 }).quality, 100);
assert.equal(scale.buildScaleRequest({ quality: "abc" }).quality, 95);
assert.equal(scale.buildScaleRequest({ targetDir: "  F:/out  " }).target_dir, "F:/out");
assert.equal(scale.buildScaleRequest({ captionExtension: "caption" }).caption_extension, "caption");
assert.equal(scale.buildScaleRequest({ captionExtension: "" }).caption_extension, ".txt");
assert.equal(scale.buildScaleRequest({ writeDatasetToml: true }).write_dataset_toml, true);
assert.equal(scale.buildScaleRequest({ overwriteToml: true }).overwrite_dataset_toml, true);
assert.equal(scale.buildScaleRequest({ targetWidth: 0 }).target_width, 1536);

// --- 2. shared parameter store --------------------------------------------
const store = createTrainParams();
assert.deepEqual(store.resolution(), [1536, 1536]);
assert.equal(store.captionExtension(), ".txt");
let seen = 0;
const unsubscribe = store.subscribe(() => { seen += 1; });
store.setResolution(2048, 1024);
store.setResolution(2048, 1024); // unchanged -> no event
assert.equal(seen, 1, "identical writes must not notify");
store.setCaptionExtension("   ");
assert.equal(store.captionExtension(), ".txt", "an empty extension must not replace the default");
store.setCaptionExtension(".caption");
assert.equal(seen, 2);
unsubscribe();
store.setResolution(512, 512);
assert.equal(seen, 2, "unsubscribe must stop notifications");

// --- 2b. the store remembers (the reload case) ------------------------------
// The report this round answers: "these parameters must not come back as the defaults every time
// the page is opened". The store is what remembers them - one key, read when the store is built,
// written when a setter accepts a value - so the criterion is literally "build it twice and look".
// A fake localStorage is installed (Node has none) so the DEFAULT path is the one under test.
const fakeCells = new Map();
globalThis.localStorage = {
  getItem: (key) => (fakeCells.has(key) ? fakeCells.get(key) : null),
  setItem: (key, value) => { fakeCells.set(key, String(value)); },
  removeItem: (key) => { fakeCells.delete(key); },
};

const remembered = createTrainParams();
remembered.setResolution(2048, 1024);
remembered.setCaptionExtension(".caption");
remembered.setProcessing({ resample: "bicubic", format: "jpeg", quality: 80, noUpscale: false, optimize: false });
remembered.setScaleOptions({ writeToml: true });

const afterReload = createTrainParams();
assert.deepEqual(afterReload.resolution(), [2048, 1024], "the resolution did not survive the reload");
assert.equal(afterReload.captionExtension(), ".caption", "the caption extension did not survive the reload");
assert.deepEqual(
  afterReload.processing(),
  { noUpscale: false, resample: "bicubic", format: "jpeg", quality: 80, optimize: false },
  "the pipeline settings did not survive the reload",
);
assert.equal(afterReload.scaleOptions().writeToml, true, "the panel's own switch did not survive the reload");
assert.equal(
  afterReload.scaleOptions().overwriteToml,
  undefined,
  "the destructive opt-in was remembered: a permission for one run is not a preference",
);

// An explicit seed still wins over the memory: a caller that names a value means it.
assert.deepEqual(
  createTrainParams({ width: 512, height: 512 }).resolution(),
  [512, 512],
  "an explicit seed lost to the remembered value",
);

// A corrupt, foreign or out-of-domain entry reads as "nothing stored": the defaults are a working
// panel, and a broken key must not be a broken page. A name this build does not offer - an entry
// written by an older version - must not reach a <select> that has no such option.
for (const raw of [
  "{",
  "null",
  "[]",
  '"text"',
  JSON.stringify({ resample: "magic", format: "tiff", quality: 400, width: -3 }),
]) {
  fakeCells.set(trainparams.TRAIN_PARAMS_STORAGE_KEY, raw);
  const recovered = createTrainParams();
  assert.deepEqual(recovered.resolution(), [1536, 1536], "a bad entry leaked into the resolution: " + raw);
  assert.equal(recovered.processing().resample, "lanczos", "a name this build does not have was kept: " + raw);
  assert.equal(recovered.processing().format, "png", "an unknown format was kept: " + raw);
  // An out-of-range number is clamped into the domain rather than dropped: 400 becomes 100, which
  // is a value the panel can show and the backend accepts. What must never happen is 400 surviving.
  assert.ok(
    recovered.processing().quality >= 1 && recovered.processing().quality <= 100,
    "an out-of-range quality survived the read: " + raw + " -> " + recovered.processing().quality,
  );
}

// A storage that refuses to be read or written is not a broken store (private mode, a full quota).
const hostile = createTrainParams({}, {
  storage: { getItem() { throw new Error("denied"); }, setItem() { throw new Error("quota"); } },
});
hostile.setResolution(1024, 1024);
assert.deepEqual(hostile.resolution(), [1024, 1024], "a hostile storage broke the store itself");

fakeCells.clear();   // the panel sections below start from the defaults
// --- 3. the column's scope strip decides what is exported -------------------
// The panel used to take the open directory instead and the endpoint walked it recursively, so
// "which images?" had a different answer here than in every other panel. It reads the same
// control as the others now (scope.js, built by app.js), snapshotted when the run starts.
const scopePaths = [];
const scopePathsAlt = ["F:/data/post/3.png"];
let scopePathsLive = scopePaths;
const scopeSelect = new FakeNode("select");
const scopeHost = new FakeNode("div");
scopeHost.append(scopeSelect);
const scope = scopeMod.createScopeControl({
  select: scopeSelect,
  providers: {
    getSelectedPaths: () => scopePathsLive.slice(),
    getVisiblePaths: () => scopePathsLive.slice(),
    getDirPaths: () => scopePathsLive.slice(),
  },
  getDirKey: () => "F:/data/post",
});
assert.equal(scopeSelect.options.length, 4, "the control must offer the four shared scopes");
assert.equal(scopeSelect.value, "selected", "the shared default is the picked images");

const tabsHost = new FakeNode("nav");
const panelHost = new FakeNode("aside");
const params = createTrainParams();
const notices = [];
const panel = scale.createScalePanel({
  tabsHost,
  panelHost,
  notify: (message, kind) => notices.push([kind, message]),
  getRoot: () => "F:/data/post",
  getParams: () => params,
  pickTarget: async () => null,
  scope,
});
assert.equal(tabsHost.children.length, 1, "the panel must append its tab");
assert.equal(panelHost.children.length, 1, "the panel must append its own panel");

const startButton = findOne(panelHost, (node) => node.className === "btn btn-primary");
const cancelButton = findOne(panelHost, (node) => node.className === "btn btn-danger");
const targetInput = findOne(panelHost, (node) => node.placeholder === t("scale.targetPlaceholder"));
const resolutionFields = findAll(panelHost, (node) => node.type === "number" && node.min === "8");
assert.ok(startButton && cancelButton && targetInput, "panel controls must exist");
assert.equal(resolutionFields.length, 2, "resolution needs a width and a height field");

// The reason line is the panel's own copy, painted at construction and toggled by the count.
const scopeLine = findOne(panelHost, (node) => node.textContent === t("scale.scopeNone"));
assert.ok(scopeLine, "the panel must say why it cannot start");
assert.equal(scopeLine.hidden, false, "an empty scope has to state its reason");

panel.onShow();
assert.equal(startButton.disabled, true, "an empty scope cannot start: that is the whole point");
targetInput.value = "F:/out";
targetInput.fire("input");
assert.equal(startButton.disabled, true, "a target folder alone is not enough any more");

// The strip is above the tabs, so it can be changed without leaving this panel: the panel has to
// follow it live, not only when the tab is shown again.
scopePathsLive = scopePathsAlt;
scopeSelect.fire("change");
assert.equal(scopeLine.hidden, true, "the reason must go away once there is something to export");
assert.equal(startButton.disabled, false, "the scope now has images, so Start is usable");

startButton.fire("click");
await flush();
assert.equal(calls.length, 1, "start must POST exactly once");
const started = JSON.parse(calls[0].init.body);
assert.equal(calls[0].init.method, "POST");
assert.ok(calls[0].url.indexOf("/api/scale/run") >= 0, "start must hit the frozen route");
assert.equal(started.root, "F:/data/post");
assert.equal(started.target_dir, "F:/out");
assert.deepEqual(
  started.images,
  scopePathsAlt,
  "the export must carry the scope's images, not a directory for the server to walk",
);
assert.equal(started.no_upscale, true);
assert.equal(started.caption_extension, ".txt");

const stream = FakeEventSource.last;
assert.ok(stream && stream.url.indexOf("job_id=J1") >= 0, "progress must reuse the SSE endpoint");

stream.fire("progress", {
  phase: "scale", done: 1, total: 2, current: "F:/data/post/1.png",
  image: { src: "F:/data/post/1.png", orig_width: 3000, orig_height: 2000, new_width: 1881, new_height: 1254 },
  errors: [{ path: "F:/data/post/2.png", message: "boom" }],
});
const progressText = findOne(panelHost, (node) => node.textContent.indexOf("1/2") >= 0);
assert.ok(progressText, "progress must render done/total");
const errorsBox = findOne(panelHost, (node) => node.className === "errors");
assert.ok(errorsBox.children.length >= 2, "errors must render a heading and the failing path");

// A done event without finished:true must be ignored (section 4.3).
stream.fire("done", { phase: "scale", done: 2, total: 2, finished: false, errors: [] });
assert.equal(stream.closed, false, "an unfinished done event must not close the stream");
stream.fire("done", {
  phase: "scale", done: 2, total: 2, finished: true, cancelled: false, errors: [],
  summary: { target_dir: "F:/out", processed: 2, scaled: 1, kept: 1, captions: 2, failed: 0, dataset_toml: "F:/out/dataset.toml" },
});
assert.equal(stream.closed, true, "the finished job must close the stream");
const summary = findOne(panelHost, (node) => node.textContent.indexOf("2") >= 0 && node.textContent.indexOf(t("scale.summary", { processed: 2, scaled: 1, kept: 1, captions: 2, failed: 0 }).slice(0, 4)) >= 0);
assert.ok(summary, "the summary line must be rendered");
assert.ok(notices.some(([kind]) => kind === "ok"), "a clean run must notify success");

// --- 4. the shared resolution reaches the next request ---------------------
params.setResolution(2048, 1024);
await flush();
assert.equal(resolutionFields[0].value, "2048", "the panel must follow the shared resolution");
assert.equal(resolutionFields[1].value, "1024");
startButton.fire("click");
await flush();
const second = JSON.parse(calls[calls.length - 1].init.body);
assert.equal(second.target_width, 2048);
assert.equal(second.target_height, 1024);

// --- 5. cancel calls the frozen endpoint ------------------------------------
cancelButton.fire("click");
await flush();
const cancelCall = calls.find((call) => call.url.indexOf("/api/scale/cancel") >= 0);
assert.ok(cancelCall, "cancel must POST /api/scale/cancel");
assert.deepEqual(JSON.parse(cancelCall.init.body), { job_id: "J1" });

// --- 6. a cancelled job must not claim it processed everything --------------
FakeEventSource.last.fire("progress", { phase: "scale", done: 1, total: 2, errors: [] });
const fill = findOne(panelHost, (node) => node.className === "progress-fill");
assert.ok(fill, "the panel must have a progress fill");
const half = fill.style.width;
assert.notEqual(half, "100%", "the progress event must move the bar off zero first");
FakeEventSource.last.fire("done", {
  phase: "scale", done: 1, total: 2, finished: true, cancelled: true, errors: [],
  summary: { processed: 1, scaled: 1, kept: 0, captions: 1, failed: 0 },
});
assert.equal(fill.style.width, half, "a cancelled job keeps the real progress instead of painting 100%");
assert.equal(cancelButton.hidden, true, "a terminal event unlocks the panel");

// --- 7. a cancel the server refuses must unlock the panel too ---------------
// Nothing will ever send done for a job that was already gone, so waiting for
// one would leave Start and Cancel disabled for good.
cancelAnswer = { cancelled: false };
startButton.fire("click");
await flush();
assert.equal(cancelButton.hidden, false, "a new job shows the cancel button again");
cancelButton.fire("click");
await flush();
assert.equal(cancelButton.hidden, true, "a refused cancel must still unlock the panel");
assert.equal(startButton.disabled, false, "start must be usable again after a refused cancel");
assert.ok(
  notices.some(([kind, message]) => kind === "warn" && message === t("scale.cancelAlreadyGone")),
  "the panel must say the job was already gone",
);

// --- 7b. the list is read again for every run -------------------------------
// The snapshot is per run, not a value the panel froze the first time it looked: a scope change
// between two runs has to reach the second request. (The panel is idle here, so Start works.)
scopePathsLive = ["F:/data/post/4.png"];
scope.paint();
startButton.fire("click");
await flush();
const reread = JSON.parse(calls[calls.length - 1].init.body);
assert.deepEqual(
  reread.images,
  ["F:/data/post/4.png"],
  "a scope change between runs must reach the next request",
);

// --- 8. what happened to an existing dataset.toml is three different statements -
// "written", "left alone" and "failed" must not share one line (or one colour).
FakeEventSource.last.fire("done", {
  phase: "scale", done: 2, total: 2, finished: true, cancelled: false, errors: [],
  summary: {
    target_dir: "F:/out", processed: 2, scaled: 1, kept: 1, captions: 2, failed: 0,
    dataset_toml: null, dataset_toml_warnings: ["dataset.toml already exists at F:/out/dataset.toml"],
    dataset_toml_backup: null, dataset_toml_skipped: true,
  },
});
assert.ok(
  findOne(panelHost, (node) => node.textContent === t("scale.datasetTomlSkipped")),
  "a dataset.toml that was left alone must say so instead of reading as a failure",
);
FakeEventSource.last.fire("done", {
  phase: "scale", done: 2, total: 2, finished: true, cancelled: false, errors: [],
  summary: {
    target_dir: "F:/out", processed: 2, scaled: 1, kept: 1, captions: 2, failed: 0,
    dataset_toml: "F:/out/dataset.toml", dataset_toml_warnings: [],
    dataset_toml_backup: "F:/out/dataset.toml.bak", dataset_toml_skipped: false,
  },
});
assert.ok(
  findOne(
    panelHost,
    (node) => node.textContent === t("scale.datasetTomlBackup", {
      path: "F:/out/dataset.toml", backup: "F:/out/dataset.toml.bak",
    }),
  ),
  "a replaced dataset.toml must say where the previous file went",
);

// --- 9. the end of a job is announced once (section 4.16 refresh hook) ------
// app.js re-reads whether a dataset.toml exists once an export finishes, because an export can write
// one into a directory that is itself a dataset root. The announcement has to come from the panel's
// single job-end function - and a done event without finished:true is not an ending (section 4.3).
const hookHosts = { tabs: new FakeNode("nav"), panels: new FakeNode("aside") };
const finished = [];
const hookPanel = scale.createScalePanel({
  tabsHost: hookHosts.tabs,
  panelHost: hookHosts.panels,
  notify: () => {},
  getRoot: () => "F:/data/post",
  getParams: () => createTrainParams(),
  pickTarget: async () => null,
  scope: makeScope(["F:/data/post/1.png"], "F:/data/post"),
  onFinished: (payload) => finished.push(payload),
});
const hookTarget = findOne(hookHosts.panels, (node) => node.placeholder === t("scale.targetPlaceholder"));
const hookStart = findOne(hookHosts.panels, (node) => node.className === "btn btn-primary");
assert.ok(hookTarget && hookStart, "the panel with a listener must still build its controls");
hookTarget.value = "F:/out";
hookTarget.fire("input");
hookStart.fire("click");
await flush();
FakeEventSource.last.fire("progress", { phase: "scale", done: 1, total: 2, errors: [] });
assert.equal(finished.length, 0, "a progress event is not the end of a job");
FakeEventSource.last.fire("done", { phase: "scale", done: 2, total: 2, finished: false, errors: [] });
assert.equal(finished.length, 0, "a done event without finished:true must not announce an ending");
const finalPayload = {
  phase: "scale", done: 2, total: 2, finished: true, cancelled: false, errors: [],
  summary: {
    target_dir: "F:/out", processed: 2, scaled: 2, kept: 0, captions: 2, failed: 0,
    dataset_toml: "F:/out/dataset.toml",
  },
};
FakeEventSource.last.fire("done", finalPayload);
assert.equal(finished.length, 1, "the end of a job must be announced exactly once");
assert.equal(
  finished[0].summary.dataset_toml,
  "F:/out/dataset.toml",
  "the announcement must carry the job's own summary, not a boolean the listener has to guess from",
);
hookPanel.close();

panel.close();
// --- 10. a remembered parameter is on the panel when the page loads ---------
// The complaint is about the *panel*, not about the store: open the page and the values are there.
// So this runs the whole path - storage -> store -> the controls the user reads -> the request the
// panel builds - with nothing but a seeded entry to start from (no onShow(), no interaction).
fakeCells.set(trainparams.TRAIN_PARAMS_STORAGE_KEY, JSON.stringify({
  width: 1024, height: 768, captionExtension: ".caption", writeToml: true,
  resample: "bicubic", format: "jpeg", quality: 80, noUpscale: false, optimize: false,
}));
const reloadHosts = { tabs: new FakeNode("nav"), panels: new FakeNode("aside") };
const reloadPanel = scale.createScalePanel({
  tabsHost: reloadHosts.tabs,
  panelHost: reloadHosts.panels,
  notify: () => {},
  getRoot: () => "F:/data/post",
  getParams: () => createTrainParams(),
  pickTarget: async () => null,
  scope: makeScope(["F:/data/post/1.png"], "F:/data/post"),
});
const reloadTarget = findOne(reloadHosts.panels, (node) => node.placeholder === t("scale.targetPlaceholder"));
const reloadStart = findOne(reloadHosts.panels, (node) => node.className === "btn btn-primary");
assert.ok(reloadTarget && reloadStart, "the reloaded panel must still build its controls");
const reloadFields = findAll(reloadHosts.panels, (node) => node.type === "number" && node.min === "8");
assert.deepEqual(
  reloadFields.map((node) => node.value),
  ["1024", "768"],
  "the remembered resolution is not on the panel",
);
const reloadSelects = findAll(reloadHosts.panels, (node) => node.tagName === "SELECT");
assert.deepEqual(
  reloadSelects.map((node) => node.value),
  ["bicubic", "jpeg"],
  "the remembered pipeline settings are not on the panel",
);
// scale.js builds its switches in this order: optimize, noUpscale, writeToml, overwriteToml.
const reloadSwitches = findAll(reloadHosts.panels, (node) => node.type === "checkbox");
assert.deepEqual(
  reloadSwitches.map((node) => node.checked),
  [false, false, true, false],
  "the remembered switches are not on the panel (or a permission was remembered with them)",
);
assert.ok(
  findOne(reloadHosts.panels, (node) => node.value === ".caption"),
  "the remembered caption extension is not on the panel",
);

reloadTarget.value = "F:/out";
reloadTarget.fire("input");
reloadStart.fire("click");
await flush();
const reloadBody = JSON.parse(calls[calls.length - 1].init.body);
assert.equal(reloadBody.target_width, 1024, "the request did not carry the remembered resolution");
assert.equal(reloadBody.target_height, 768);
assert.equal(reloadBody.resample, "bicubic");
assert.equal(reloadBody.output_format, "jpeg");
assert.equal(reloadBody.quality, 80);
assert.equal(reloadBody.no_upscale, false);
assert.equal(reloadBody.optimize, false);
assert.equal(reloadBody.caption_extension, ".caption");
assert.equal(reloadBody.write_dataset_toml, true, "the remembered switch did not reach the request");
assert.equal(
  reloadBody.overwrite_dataset_toml,
  false,
  "a permission for one run was remembered and carried into a later request",
);
reloadPanel.close();
fakeCells.clear();
console.log("scale harness OK");
