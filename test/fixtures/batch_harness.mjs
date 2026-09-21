/**
 * Headless behaviour harness for web/js/batch.js.
 *
 * The promise this panel makes is narrow and testable: preview sends the SAME
 * op with dry_run:true, write sends it WITHOUT dry_run, write stays disabled
 * until a fresh preview exists, and the common-tag editor turns row edits into
 * rename pairs instead of rewriting the whole list. A static test can only see
 * that the strings "dry_run" and "batchCaptions" appear somewhere - not which
 * request carried which flag.
 *
 * api.js is not stubbed: only globalThis.fetch is, so the real request builder
 * and the real JSON body are under test.
 *
 * Usage: node batch_harness.mjs <absolute path to web/>
 */
import { pathToFileURL } from "node:url";

const WEB = process.argv[2];
if (!WEB) throw new Error("usage: node batch_harness.mjs <web dir>");

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
    this.style = {};
    this.listeners = {};
    this.textContent = "";
    this.className = "";
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
  /** HTMLSelectElement.options: what the shared scope control iterates (scope.js paint()). */
  get options() {
    return this.children.filter((node) => node && node.tagName === "OPTION");
  }
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

const hasClass = (node, name) =>
  (node.classList && node.classList.contains(name)) || String(node.className || "").split(/\s+/).includes(name);

function assert(condition, message) {
  if (!condition) throw new Error("batch harness: " + message);
}

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

globalThis.document = {
  body: new FakeNode("body"),
  createElement: (tag) => new FakeNode(tag),
  createDocumentFragment: () => new FakeNode("fragment"),
  createTextNode: (text) => ({ nodeType: 3, textContent: text }),
  addEventListener() {},
  removeEventListener() {},
};
globalThis.window = { setTimeout, clearTimeout, addEventListener() {}, removeEventListener() {} };

const calls = [];
let queued = [];
//: Set to a promise to keep the next response in flight, so the harness can look
//: at the panel while a write is still running. release() lets it finish.
let hold = null;
let release = null;
const holdNext = () => { hold = new Promise((resolve) => { release = resolve; }); };
const releaseHeld = () => { const done = release; hold = null; release = null; done(); };
globalThis.fetch = async (url, init) => {
  calls.push({ url: String(url), body: init && init.body ? JSON.parse(init.body) : null });
  if (hold) await hold;
  const payload = queued.shift() || { applied: 0, failed: 0, results: [] };
  return {
    ok: true,
    status: 200,
    statusText: "OK",
    url: String(url),
    json: async () => payload,
  };
};

// The panel opens an EventSource for the write job; the harness drives it by hand.
class FakeEventSource {
  constructor(url) {
    this.url = url;
    this.listeners = {};
    this.closed = false;
    FakeEventSource.last = this;
  }
  addEventListener(type, handler) { (this.listeners[type] ||= []).push(handler); }
  close() { this.closed = true; }
  fire(type, data) {
    for (const handler of [...(this.listeners[type] || [])]) handler({ data: JSON.stringify(data) });
    // The real EventSource also carries onerror / onmessage as properties.
    const direct = this["on" + type];
    if (typeof direct === "function") direct({ data: JSON.stringify(data) });
  }
}
FakeEventSource.last = null;
globalThis.EventSource = FakeEventSource;

const jobsMod = await import(pathToFileURL(WEB + "/js/jobs.js").href);
const scopeMod = await import(pathToFileURL(WEB + "/js/scope.js").href);
const mod = await import(pathToFileURL(WEB + "/js/batch.js").href);
const strings = await import(pathToFileURL(WEB + "/js/strings.js").href);
const t = strings.t;

// ---- pure helpers --------------------------------------------------------
const { commonTags, commonEdit, splitTagInput } = mod;
assert(typeof commonTags === "function", "commonTags is not exported");
assert(typeof commonEdit === "function", "commonEdit is not exported");
assert(typeof splitTagInput === "function", "splitTagInput is not exported");

const common = commonTags([["a", "b"], ["b", "a", "c"], ["a", "b"]]);
assert(JSON.stringify(common) === JSON.stringify(["a", "b"]), "commonTags must intersect, got " + common.join(","));
assert(JSON.stringify(commonTags([["a"], []])) === "[]", "an empty image clears the intersection");
assert(JSON.stringify(commonTags([])) === "[]", "no images means no common tags");

const edit = commonEdit(["a", "b", "c"], ["a", "B", ""], ["z", "z", "b"]);
assert(JSON.stringify(edit.pairs) === JSON.stringify([{ find: "b", replace: "B" }, { find: "c", replace: "" }]), "commonEdit pairs: " + JSON.stringify(edit.pairs));
assert(JSON.stringify(edit.tags) === JSON.stringify(["z"]), "commonEdit must dedupe added tags and skip existing ones: " + JSON.stringify(edit.tags));
assert(JSON.stringify(splitTagInput(" a , , b ")) === JSON.stringify(["a", "b"]), "splitTagInput must drop blanks");

// ---- panel ---------------------------------------------------------------
const tabsHost = new FakeNode("nav");
const panelHost = new FakeNode("aside");
const paths = ["/d/1.png", "/d/2.png"];
const tagsByPath = {
  "/d/1.png": ["a", "b"],
  "/d/2.png": ["a", "b"],
};
let wrote = 0;
//: The toasts and the dialog are part of the panel's contract now: "is this write
//: reversible, and does it ask first?" is decided by the backup checkbox, and the
//: only place the escape hatch can appear is the toast.
const notices = [];
let confirms = 0;
//: Three more answers to "which images?" - the scope control has to reach all four, and the request
//: has to follow whichever one is chosen (the panel used to have exactly one answer).
//: The job shelf this panel registers with (js/jobs.js), so "what is running" is on screen from any tab.
const shelfHost = new FakeNode("div");
shelfHost.hidden = true;
const shelf = jobsMod.createJobShelf({ host: shelfHost, onOpen: () => {} });
const selectedPaths = ["/d/2.png"];
const dirPaths = ["/d/1.png", "/d/2.png", "/d/3.png"];
const recursivePaths = dirPaths.concat(["/d/sub/4.png"]);
let scopeChanges = 0;
// The panel subscribes to the column's scope control instead of building one, so the harness
// builds it the way app.js does and hands over the instance.
const scopeHost = new FakeNode("div");
const scopeSelect = new FakeNode("select");
scopeHost.append(scopeSelect);
const scope = scopeMod.createScopeControl({
  select: scopeSelect,
  providers: {
    getVisiblePaths: () => paths.slice(),
    getSelectedPaths: () => selectedPaths.slice(),
    getDirPaths: () => dirPaths.slice(),
  },
  getDirKey: () => "/d",
  loadRecursive: async () => ({ dir: "/d", paths: recursivePaths.slice() }),
});
const panel = mod.createBatchPanel({
  tabsHost,
  panelHost,
  notify: (message, kind, action) => { notices.push({ message, kind, action }); },
  confirm: () => { confirms += 1; return true; },
  jobs: shelf,
  scope,
  onScopeChange: () => { scopeChanges += 1; },
  getTags: (path) => tagsByPath[path] || null,
  onAfterWrite: () => { wrote += 1; },
});

const tab = tabsHost.children[0];
assert(tab && tab.dataset.tab === "batch", "the panel did not append its tab");
const root = panelHost.children[0];
assert(root && root.dataset.panel === "batch", "the panel did not append its panel");

// hidden: nothing is computed yet (the tab handler unhides before onShow)
panel.refreshScope();
assert(find(root, (node) => hasClass(node, "batch-common-input")).length === 0, "a hidden tab must not paint common rows");
root.hidden = false;
panel.onShow();

const commonInputs = find(root, (node) => hasClass(node, "batch-common-input"));
assert(commonInputs.length === 2, "expected 2 common rows, got " + commonInputs.length);
assert(commonInputs[0].value === "a" && commonInputs[1].value === "b", "common rows must be the shared tags in name order");

const previewButton = find(root, (node) => node.tagName === "BUTTON" && node.textContent === t("batch.preview"))[0];
const applyButton = find(root, (node) => node.tagName === "BUTTON" && node.textContent === t("batch.apply"))[0];
// The panel now holds two selects: the scope control (scope.js) and the op. Identify the op by its
// options rather than by position - the position changed the day the scope control arrived.
const isOpSelect = (node) => node.tagName === "SELECT" && (node.children || []).some((child) => child.value === mod.BATCH_COMMON);
const opSelect = find(root, isOpSelect)[0];
// The scope select is NOT in this panel any more: it belongs to the column (the strip above the
// tab panels), and the panel reads the value through the control it was handed.
assert(scopeSelect, "the harness has no scope control");
assert(
  scopeSelect.children.length === 4,
  "the scope control must offer the four shared scopes, got " + scopeSelect.children.length,
);
assert(
  find(root, (node) => node === scopeSelect).length === 0,
  "the panel owns a scope select of its own again - two answers to 'which images?'",
);
assert(
  scopeSelect.value === "selected",
  "the shared default must be the picked images (nothing is written until the scope points at something), got " + scopeSelect.value,
);
assert(previewButton && applyButton && opSelect, "panel buttons / op select not found");
assert(applyButton.disabled === true, "write must start disabled");

// editing a common row must become a rename pair, and the preview must dry run
commonInputs[1].value = "B";
commonInputs[1].fire("input");
queued = [{ dry_run: true, applied: 2, failed: 0, results: [
  { image: "/d/1.png", ok: true, changed: true, before: "a, b", after: "a, B" },
  { image: "/d/2.png", ok: true, changed: true, before: "a, b", after: "a, B" },
] }];
previewButton.fire("click");
await tick(); await tick(); await tick();
assert(calls.length === 1, "preview must issue exactly one request, got " + calls.length);
const previewBody = calls[0].body;
assert(previewBody.op !== mod.BATCH_COMMON, "the local UI mode leaked into the request op");
assert(calls[0].url.indexOf("/api/captions/batch") >= 0, "wrong endpoint: " + calls[0].url);
assert(previewBody.op === "edit_tags", "the common-tag editor must send op=edit_tags, got " + previewBody.op);
assert(previewBody.dry_run === true, "preview must set dry_run");
assert(JSON.stringify(previewBody.pairs) === JSON.stringify([{ find: "b", replace: "B" }]), "preview pairs: " + JSON.stringify(previewBody.pairs));
assert(
  JSON.stringify(previewBody.images) === JSON.stringify(selectedPaths),
  "the preview must carry the scope the shared control holds, got " + JSON.stringify(previewBody.images),
);
assert(previewBody.position === "append", "new tags append by default");
assert(applyButton.disabled === false, "a successful preview with changes must enable write");

// --- 7. writing starts a job, and the panel paints its stream -------------
// The write used to be one long POST with no progress channel. It is a job now:
// the run request answers 202, the panel opens the SSE stream, paints done/total
// and builds the outcome table from the per-image events.
queued = [{ job_id: "J1", total: 2 }];
holdNext();
applyButton.fire("click");
await tick();
assert(
  applyButton.textContent === t("batch.writing", { n: selectedPaths.length }),
  "a write in flight must say what it is doing, got " + applyButton.textContent,
);
assert(applyButton.disabled === true, "the write button must be locked while the job starts");
assert(previewButton.disabled === true, "preview must be locked while a write is in flight");
applyButton.fire("click");
await tick();
assert(calls.length === 2, "a second click must not start a second job, got " + calls.length);

releaseHeld();
await tick(); await tick(); await tick();
const applyBody = calls[1].body;
assert(calls[1].url.indexOf("/api/captions/batch/run") >= 0, "the write must start the job: " + calls[1].url);
assert(applyBody.dry_run === undefined, "write must NOT carry dry_run");
assert(applyBody.backup === true, "the write must carry the backup flag the checkbox shows (default on)");
assert(applyBody.op === "edit_tags", "write op must be edit_tags too, got " + applyBody.op);
assert(JSON.stringify(applyBody.pairs) === JSON.stringify([{ find: "b", replace: "B" }]), "write pairs changed");

const writeStream = FakeEventSource.last;
assert(writeStream && writeStream.url.indexOf("/api/captions/batch/stream") >= 0, "the panel must open the job stream: " + (writeStream && writeStream.url));

const shows = (text) => find(root, (node) => String(node.textContent).indexOf(text) >= 0).length > 0;
writeStream.fire("progress", {
  phase: "captions", done: 1, total: 2, current: "/d/1.png", errors: [],
  entry: { image: "/d/1.png", ok: true, cache_removed: [], cache_remove_failed: [] },
});
await tick();
assert(shows(t("batch.running", { done: 1, total: 2 })), "the progress line must show 1/2");
assert(shows("1.png"), "the progress line must name the file it is writing");
assert(find(root, (node) => node.tagName === "TABLE").length === 1, "the first written image must appear in the outcome table");

writeStream.fire("progress", {
  phase: "captions", done: 2, total: 2, current: "/d/2.png", errors: [],
  entry: { image: "/d/2.png", ok: false, error: { code: "write_failed", message: "boom" } },
});
await tick();
assert(shows(t("batch.running", { done: 2, total: 2 })), "the progress line must reach 2/2");

writeStream.fire("done", {
  phase: "captions", done: 2, total: 2, finished: true, cancelled: false, errors: [],
  summary: { op: "edit_tags", processed: 2, applied: 1, failed: 1 },
});
await tick();
assert(writeStream.closed === true, "the panel must close the stream once the job is done");
assert(wrote === 1, "onAfterWrite was not called after the job finished");
assert(applyButton.textContent === t("batch.apply"), "the label must come back after the job, got " + applyButton.textContent);
assert(applyButton.disabled === true, "the write button must be disabled again after the job ran");
assert(
  find(root, (node) => node.tagName === "TABLE").length === 1,
  "the job's outcome table must stay on screen - a failed image is only findable there",
);

// a stale preview cannot be written: switching op drops it
opSelect.value = "normalize";
opSelect.fire("change");
assert(applyButton.disabled === true, "changing the op must drop the preview");
queued = [{ dry_run: true, applied: 0, failed: 0, results: [] }];
previewButton.fire("click");
await tick(); await tick(); await tick();
assert(calls.length === 3, "normalize preview must still hit the endpoint");
assert(calls[2].body.op === "normalize" && calls[2].body.dry_run === true, "normalize preview body: " + JSON.stringify(calls[2].body));
assert(applyButton.disabled === true, "a preview with no changes must not enable write");

// --- 8. the cancel button asks the server to stop the job ------------------
queued = [{ dry_run: true, applied: 2, failed: 0, results: [
  { image: "/d/1.png", ok: true, changed: true, before: "a, b", after: "a, B" },
  { image: "/d/2.png", ok: true, changed: true, before: "a, b", after: "a, B" },
] }];
previewButton.fire("click");
await tick(); await tick(); await tick();
assert(applyButton.disabled === false, "a fresh preview must enable the write again");
queued = [{ job_id: "J2", total: 2 }];
applyButton.fire("click");
await tick(); await tick(); await tick();
const secondStream = FakeEventSource.last;
const cancelButton = find(root, (node) => node.tagName === "BUTTON" && String(node.className).indexOf("btn-danger") >= 0)[0];
assert(cancelButton, "a running job needs a cancel button");
assert(cancelButton.hidden === false, "the cancel button must be visible while the job runs");
cancelButton.fire("click");
await tick(); await tick();
const cancelCall = calls[calls.length - 1];
assert(cancelCall.url.indexOf("/api/captions/batch/cancel") >= 0, "cancel must POST the cancel endpoint: " + cancelCall.url);
assert(JSON.stringify(cancelCall.body) === JSON.stringify({ job_id: "J2" }), "cancel body: " + JSON.stringify(cancelCall.body));
assert(cancelButton.textContent === t("batch.cancelling"), "the button must say it is cancelling, got " + cancelButton.textContent);

secondStream.fire("done", {
  phase: "captions", done: 1, total: 2, finished: true, cancelled: true, errors: [],
  summary: { op: "edit_tags", processed: 1, applied: 1, failed: 0 },
});
await tick();
assert(cancelButton.hidden === true, "a finished job must hide the cancel button");
assert(applyButton.textContent === t("batch.apply"), "the label must be back after a cancelled job");

// --- 9. a reversible write does not ask: the toast carries the undo ----------------
// The backup is on by default, so the write can be taken back - and the panel already made
// the user look at the diff before the button went live. Two writes ran in this harness
// (one finished, one cancelled) and neither was gated by a dialog.
const backupLabel = find(root, (node) => node.textContent === t("batch.backup"))[0];
assert(backupLabel, "the backup checkbox is not on screen");
const backupBox = backupLabel.parentNode.children[0];
assert(backupBox && backupBox.type === "checkbox", "the backup row holds no checkbox");
assert(backupBox.checked === true, "the backup must default to on");
assert(confirms === 0, "a reversible write must not be gated by a confirmation dialog");

const undoNotice = notices.filter((notice) => notice.action && typeof notice.action.run === "function").pop();
assert(undoNotice, "a reversible write must offer the undo");
assert(
  undoNotice.action.label === t("app.undo"),
  "the offered action must be labelled as an undo, got " + undoNotice.action.label,
);
queued = [{ restored: 1, failed: 0, results: [{ image: "/d/1.png", ok: true, restored: true }] }];
undoNotice.action.run();
await tick(); await tick();
const undoCall = calls[calls.length - 1];
assert(
  undoCall.url.indexOf("/api/captions/restore") >= 0,
  "the undo must POST /api/captions/restore, got " + undoCall.url,
);
assert(
  JSON.stringify(undoCall.body.images) === JSON.stringify(["/d/1.png"]),
  "the undo must restore exactly the images the job wrote, got " + JSON.stringify(undoCall.body),
);
assert(wrote === 3, "the undo must refresh the gallery's caption state through onAfterWrite, got " + wrote);

// --- 10. without the backup the write is irreversible, and then it asks ------------
backupBox.checked = false;
backupBox.fire("change");
queued = [{ dry_run: true, applied: 2, failed: 0, results: [
  { image: "/d/1.png", ok: true, changed: true, before: "a, b", after: "a, B" },
  { image: "/d/2.png", ok: true, changed: true, before: "a, b", after: "a, B" },
] }];
previewButton.fire("click");
await tick(); await tick(); await tick();
queued = [{ job_id: "J3", total: 2 }];
applyButton.fire("click");
await tick(); await tick();
assert(confirms === 1, "an irreversible write must still ask, got " + confirms + " dialogs");

// --- 11. the scope control decides what the panel acts on --------------------------
// The panel used to act on the visible set because that was the only answer it had. The scope is a
// choice now (scope.js, shared with the tagger): the choice has to reach the request, the recursive
// entry has to actually load, and a scope change has to drop a preview of the previous set.
assert(scopeSelect.options.length === 4, "the panel must offer the four shared scopes");

// Close out the write section 10 started first. A job in flight keeps every button locked for a
// reason that has nothing to do with the scope, and an assertion made in that state is green
// whatever the scope does - this is how the scope criterion below had quietly stopped testing
// anything when the write became a background job.
FakeEventSource.last.fire("done", {
  phase: "captions", done: 2, total: 2, finished: true, cancelled: false, errors: [],
  summary: { op: "edit_tags", processed: 2, applied: 2, failed: 0 },
});
await tick();
assert(applyButton.disabled === true, "a finished write leaves no preview behind");

scopeSelect.value = "filtered";
scopeSelect.fire("change");
await tick();
assert(scopeChanges === 1, "the app was never told the scope changed, so the new set is never probed");
queued = [{ dry_run: true, applied: 1, failed: 0, results: [
  { image: "/d/2.png", ok: true, changed: true, before: "a, b", after: "a, B" },
] }];
previewButton.fire("click");
await tick(); await tick(); await tick();
assert(applyButton.disabled === false, "a fresh preview of the chosen scope must enable the write");
const scopedPreview = calls[calls.length - 1].body;
assert(
  JSON.stringify(scopedPreview.images) === JSON.stringify(paths),
  "the preview must cover the chosen scope, got " + JSON.stringify(scopedPreview.images),
);

// The hook the hoist had to keep, isolated: while this tab is closed refreshScope() returns
// before its own signature check (the early return that keeps the per-chunk filter pass cheap),
// so only the subscription's own invalidatePreview() can drop the preview here. A stale preview
// must not survive a scope change even when nobody is looking at the panel.
root.hidden = true;
scopeSelect.value = "dir";
scopeSelect.fire("change");
await tick();
assert(scopeChanges === 2, "the app must be told about every scope change, got " + scopeChanges);
assert(
  applyButton.disabled === true,
  "a scope change while the batch tab was closed kept a preview of the previous set of images",
);
root.hidden = false;
panel.onShow();

scopeSelect.value = "dir_recursive";
scopeSelect.fire("change");
await tick(); await tick(); await tick();
assert(
  String(scopeSelect.options[2].textContent).indexOf("4") >= 0,
  "the recursive scope's count never arrived: " + scopeSelect.options[2].textContent,
);
queued = [{ dry_run: true, applied: 4, failed: 0, results: [] }];
previewButton.fire("click");
await tick(); await tick(); await tick();
const recursivePreview = calls[calls.length - 1].body;
assert(
  JSON.stringify(recursivePreview.images) === JSON.stringify(recursivePaths),
  "the recursive scope must reach the request, got " + JSON.stringify(recursivePreview.images),
);

// --- 12. the write registers a shelf row, and closes it on every ending -------------
// A job used to be visible only from the tab that started it. Now the panel registers the job it already
// owns, so switching tabs cannot hide it - and every way a job can end has to take the row away, or the
// shelf becomes a lie about what is running.
const shelfRows = () => find(shelfHost, (node) => node.className === "job-row");
const rowText = () => shelfRows().map((row) => row.children.map((child) => child.textContent).join(" ")).join(" || ");

const freshPreview = async () => {
  queued = [{ dry_run: true, applied: 2, failed: 0, results: [
    { image: "/d/1.png", ok: true, changed: true, before: "a, b", after: "a, B" },
    { image: "/d/2.png", ok: true, changed: true, before: "a, b", after: "a, B" },
  ] }];
  previewButton.fire("click");
  await tick(); await tick(); await tick();
};

await freshPreview();
queued = [{ job_id: "J4", total: 2 }];
applyButton.fire("click");
await tick(); await tick();
assert(shelfRows().length === 1, "a running write must have a shelf row, got " + shelfRows().length);
assert(rowText().indexOf(t("tab.batch")) >= 0, "the row must name the workspace that owns the job: " + rowText());

FakeEventSource.last.fire("progress", {
  phase: "captions", done: 1, total: 2, current: "/d/1.png", errors: [],
  entry: { image: "/d/1.png", ok: true, cache_removed: [], cache_remove_failed: [] },
});
await tick();
assert(rowText().indexOf("1/2") >= 0, "the row must follow the job's progress, got " + rowText());

const shelfCancel = shelfRows()[0].children.filter((child) => child.tagName === "BUTTON").pop();
shelfCancel.fire("click");
await tick(); await tick();
assert(
  calls[calls.length - 1].url.indexOf("/api/captions/batch/cancel") >= 0,
  "the shelf's cancel must go through the panel's own path, got " + calls[calls.length - 1].url,
);

FakeEventSource.last.fire("done", {
  phase: "captions", done: 2, total: 2, finished: true, cancelled: false, errors: [],
  summary: { op: "edit_tags", processed: 2, applied: 2, failed: 0 },
});
await tick();
assert(shelfRows().length === 0, "a finished write left a row behind: " + rowText());
assert(shelfHost.hidden === true, "an empty shelf must hide itself");

// A lost stream is the other ending that never sends done.
await freshPreview();
queued = [{ job_id: "J5", total: 2 }];
applyButton.fire("click");
await tick(); await tick();
assert(shelfRows().length === 1, "the second write must register its own row");
FakeEventSource.last.fire("error");
FakeEventSource.last.fire("error");
FakeEventSource.last.fire("error");
await tick();
assert(shelfRows().length === 0, "a lost stream left a ghost row on the shelf: " + rowText());

console.log("batch harness OK " + JSON.stringify({ common, calls: calls.length }));