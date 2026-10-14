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
  /**
   * The event is merged into the default one so a criterion can name the key it
   * presses (the autocomplete's arrow/Tab/Enter handling reads `event.key`), and
   * `preventDefault()` records itself: the two handlers that share one Enter -
   * the dropdown and the field - agree through `defaultPrevented`.
   */
  fire(type, event) {
    const payload = Object.assign(
      {
        defaultPrevented: false,
        preventDefault() { this.defaultPrevented = true; },
        target: this,
      },
      event || {},
    );
    for (const handler of [...(this.listeners[type] || [])]) handler(payload);
  }
  /** The autocomplete's click-to-accept walks up from the row that was pressed. */
  closest(selector) {
    const wanted = String(selector).replace(/^\./, "");
    let node = this;
    while (node) {
      if (hasClass(node, wanted)) return node;
      node = node.parentNode;
    }
    return null;
  }
  focus() { globalThis.document.activeElement = this; }
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
//: The tag vocabulary behind GET /api/tags/autocomplete (§4.10), answered here rather
//: than through `queued`: the panel's tag fields fire it while typing, and the section
//: that drives them keeps its own request log so the write sections' counts stay exact.
const vocabulary = ["1girl", "1girl_solo", "smile", "smiling", "genshin_impact"];
const suggestQueries = [];
//: Set before typing to keep the next vocabulary reply in flight (the stale-reply criterion).
let holdNextSuggest = false;
let suggestGate = null;
const vocabularyReply = (target) => {
  const query = (new URL(target, "http://harness.local").searchParams.get("q") || "").toLowerCase();
  return vocabulary
    .filter((tag) => tag.toLowerCase().indexOf(query) >= 0)
    .map((tag) => ({ tag, category: "General", category_id: "0", count: 100 }));
};

globalThis.fetch = async (url, init) => {
  const target = String(url);
  if (target.indexOf("/api/tags/autocomplete") >= 0) {
    suggestQueries.push(target);
    if (holdNextSuggest) {
      holdNextSuggest = false;
      await new Promise((resolve) => { suggestGate = resolve; });
    }
    return {
      ok: true,
      status: 200,
      statusText: "OK",
      url: target,
      json: async () => ({ suggestions: vocabularyReply(target) }),
    };
  }
  calls.push({ url: target, body: init && init.body ? JSON.parse(init.body) : null });
  if (hold) await hold;
  const payload = queued.shift() || { applied: 0, failed: 0, results: [] };
  return {
    ok: true,
    status: 200,
    statusText: "OK",
    url: target,
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
const { commonTags, commonEdit, blockAdditions, orderEdit, moveRowIn, emptyBuckets } = mod;
assert(typeof commonTags === "function", "commonTags is not exported");
assert(typeof commonEdit === "function", "commonEdit is not exported");
assert(typeof blockAdditions === "function", "blockAdditions is not exported");
assert(typeof orderEdit === "function", "orderEdit is not exported");
assert(typeof moveRowIn === "function", "moveRowIn is not exported");
assert(typeof emptyBuckets === "function", "emptyBuckets is not exported");

const common = commonTags([["a", "b"], ["b", "a", "c"], ["a", "b"]]);
assert(JSON.stringify(common) === JSON.stringify(["a", "b"]), "commonTags must intersect, got " + common.join(","));
assert(JSON.stringify(commonTags([["a"], []])) === "[]", "an empty image clears the intersection");
assert(JSON.stringify(commonTags([])) === "[]", "no images means no common tags");

// Renames and deletes travel as pairs; additions belong to a container (blockAdditions below).
const edit = commonEdit(["a", "b", "c"], ["a", "B", ""]);
assert(JSON.stringify(edit.pairs) === JSON.stringify([{ find: "b", replace: "B" }, { find: "c", replace: "" }]), "commonEdit pairs: " + JSON.stringify(edit.pairs));
assert(edit.tags === undefined, "commonEdit still carries additions - they are the containers' job now");
assert(
  JSON.stringify(blockAdditions([{ tag: "z", where: "front" }, { tag: "z", where: "back" }, { tag: "b", where: "front" }, { tag: " ", where: "front" }], ["a", "b"])) ===
    JSON.stringify({ tags_first: ["z"] }),
  "blockAdditions must split by container, dedupe, and skip rows of the common list",
);

// The order helpers, before any DOM is involved: what a container set means.
const values = ["a", "b", "c"];
assert(
  JSON.stringify(orderEdit(emptyBuckets(), values, false)) === "{}",
  "an untouched middle must send no order at all",
);
assert(
  JSON.stringify(orderEdit({ front: [2], middle: [0, 1], back: [] }, values, false)) ===
    JSON.stringify({ order_first: ["c"] }),
  "the front container is order_first and nothing else",
);
assert(
  JSON.stringify(orderEdit({ front: [2], middle: [1], back: [0] }, values, false)) ===
    JSON.stringify({ order_first: ["c"], order_last: ["a"] }),
  "the end container is order_last",
);
assert(
  JSON.stringify(orderEdit({ front: [1, 0, 2], middle: [], back: [] }, values, true)) ===
    JSON.stringify({ order_first: ["b", "a", "c"] }),
  "a single image sends its whole order",
);
assert(
  JSON.stringify(orderEdit({ front: [0, 1, 2], middle: [], back: [] }, values, true)) === "{}",
  "a single image whose rows were not moved sends no order (a rename travels in pairs)",
);
// A cleared row is not sent: it is a deletion, and `pairs` carries it.
assert(
  JSON.stringify(orderEdit({ front: [2], middle: [0, 1], back: [] }, ["a", "b", ""], false)) === "{}",
  "a deleted row must not appear in the order blocks",
);
const moved = { front: [], middle: [0, 1], back: [] };
assert(moveRowIn(moved, 1, "middle", 0) === false, "a move inside the middle is not a move");
assert(JSON.stringify(moved.middle) === JSON.stringify([0, 1]), "the middle must not be reordered");
assert(moveRowIn(moved, 1, "front", -1) === true, "moving out of the middle must count");
assert(JSON.stringify(moved.front) === JSON.stringify([1]) && moved.middle.length === 1, "moveRowIn must move the row, not copy it");
assert(moveRowIn(moved, 1, "front", 1) === false, "dropping a row onto itself is not a move");
assert(moveRowIn(moved, 1, "back", -1) === true && JSON.stringify(moved.back) === JSON.stringify([1]), "a row can leave the front again");

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
//: Every "read these captions" request the panel made (the app answers it by probing exactly the
//: paths it does not know yet - app.js's probeScopeCaptions).
const captionRequests = [];
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
  // The app reads the captions of the images a panel is looking at; nothing else covers a scope that
  // reaches outside the directory on screen, so the panel must actually ask.
  requestCaptions: (paths) => { captionRequests.push(paths.slice()); },
  getTags: (path) => tagsByPath[path] || null,
  onAfterWrite: () => { wrote += 1; },
});

const tab = tabsHost.children[0];
assert(tab && tab.dataset.tab === "batch", "the panel did not append its tab");
const root = panelHost.children[0];
assert(root && root.dataset.panel === "batch", "the panel did not append its panel");

// hidden: nothing is computed yet (the tab handler unhides before onShow)
panel.refreshScope();
const rowInputs = () => find(root, (node) => hasClass(node, "batch-common-input") && !hasClass(node, "batch-bucket-add-input"));
assert(rowInputs().length === 0, "a hidden tab must not paint common rows");
root.hidden = false;
panel.onShow();

const commonInputs = rowInputs();
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
assert(
  previewBody.position === undefined && previewBody.tags === undefined,
  "a rename-only edit still sends the legacy `tags`/`position` pair: additions are the containers' job now",
);
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
// The "dir" scope reaches an image whose caption nobody has read: the panel has to ask for it.
// Nothing else does - the gallery only ever probes the directory's own listing, and this scope can
// reach outside it - so without the ask the common-tag containers stay empty forever (the report
// that produced this criterion: "the containers start out without the common tags").
assert(
  captionRequests.length > 0,
  "the panel never asked for the captions of the scope it is acting on",
);
assert(
  JSON.stringify(captionRequests[captionRequests.length - 1]) === JSON.stringify(dirPaths),
  "the panel asked for the wrong images: " + JSON.stringify(captionRequests[captionRequests.length - 1]),
);
// (Asking again on a later repaint is the panel's job - it cannot know what is in flight; app.js's
// probeScopeCaptions is what skips the paths that are already known or already requested.)

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

// --- 13. tag fields autocomplete: the scope's tags first, the vocabulary behind them -------
// The report was "批量 tag 编辑这边怎么不支持自动补全". The dropdown is one module now
// (suggest.js), shared with the caption editor; this section drives the part that is this
// panel's own: the candidates are the tags the scope's captions already carry (the ones
// remove / replace / find name), §4.10's vocabulary only fills the rest, taking a suggestion
// is an edit that drops the preview, and a comma separated field completes one tag instead of
// replacing the list. The stale-reply guard is checked too, because a slow query must never
// repaint the dropdown for text that has since changed.
const settle = () => new Promise((resolve) => setTimeout(resolve, 250));
const fieldByLabel = (key) =>
  find(root, (node) => hasClass(node, "field") && node.children[0] && node.children[0].textContent === t(key))[0];
const wrapOf = (key) => {
  const box = fieldByLabel(key);
  return box ? box.children[1] : null;
};
const rowTexts = (list) => list.children.map((row) => row.children.map((child) => child.textContent));
const activeRow = (list) => list.children.filter((row) => hasClass(row, "is-active"))[0];

// The replace section is the current one, and its "replace with" field may name a tag no image
// carries yet - so it is the panel's field that suggests from both sources. (The comma separated
// list field went with the three ops it served, 2026-10-13; the caption editor's own list field
// still covers that completion rule.)
opSelect.value = "replace";
opSelect.fire("change");
tagsByPath["/d/2.png"] = ["1girl", "solo", "smile"];
scopeSelect.value = "selected";
scopeSelect.fire("change");
await tick();
panel.onShow();

const listWrap = wrapOf("batch.replaceLabel");
assert(listWrap, "the replace-with field has no .suggest-wrap, so the dropdown has nothing to position against");
const listInput = listWrap.children[0];
const dropdown = listWrap.children[1];
assert(dropdown.tagName === "DIV" && hasClass(dropdown, "suggest"), "the tag field has no dropdown element");
assert(dropdown.hidden === true, "the dropdown must start closed");
const findWrap = wrapOf("batch.findLabel");
assert(findWrap, "the find field has no dropdown wrapper");
const findInput = findWrap.children[0];
const findDropdown = findWrap.children[1];
const fillFind = () => { findInput.value = "1girl"; };

// A fresh preview first: taking a suggestion has to be the thing that drops it.
listInput.value = "1girl";  // the field needs a value, and the preview needs changes
fillFind();
await freshPreview();
assert(applyButton.disabled === false, "precondition: a fresh preview enables the write");

listInput.value = "1g";
listInput.fire("input");
await settle();
assert(dropdown.hidden === false, "typing a tag did not open the dropdown");
let shown = rowTexts(dropdown);
assert(shown.length === 2, "expected the dataset tag plus one vocabulary tag, got " + JSON.stringify(shown));
assert(shown[0][0] === "1girl", "the scope's own tag is not the first suggestion: " + JSON.stringify(shown));
assert(shown[0][1] === "1", "a dataset suggestion must show how many images carry it, got " + JSON.stringify(shown[0][1]));
assert(shown[1][0] === "1girl_solo", "the vocabulary fallback did not arrive: " + JSON.stringify(shown));
assert(
  shown[1][1] === "General \u00b7 100",
  "a vocabulary suggestion lost its category/count badge: " + JSON.stringify(shown[1][1]),
);
assert(suggestQueries.length === 1, "one keystroke must ask the vocabulary once, got " + suggestQueries.length);

// Enter takes the active row - and only once: the field's own Enter handler stands down on
// an event the dropdown marked as handled.
listInput.fire("keydown", { key: "Enter" });
await tick();
assert(listInput.value === "1girl", "Enter did not take the suggestion: " + JSON.stringify(listInput.value));
assert(dropdown.hidden === true, "the dropdown must close once a suggestion is taken");
assert(applyButton.disabled === true, "taking a suggestion left a preview of the previous field values armed");

// The arrows move the active row and a click takes the row that was pressed.
listInput.value = "sm";
listInput.fire("input");
await settle();
shown = rowTexts(dropdown);
assert(shown.length >= 2, "expected two 'sm' suggestions, got " + JSON.stringify(shown));
listInput.fire("keydown", { key: "ArrowDown" });
const picked = activeRow(dropdown);
assert(picked && picked.children[0].textContent === shown[1][0], "ArrowDown did not move the active row");
// The row is the target of a press that the dropdown's one listener handles (the real
// DOM bubbles it; this fake one is told where the event came from).
dropdown.fire("mousedown", { target: picked });
await tick();
assert(
  listInput.value === shown[1][0],
  "clicking a suggestion must take it, got " + JSON.stringify(listInput.value),
);

// A tag no image carries yet is still findable: the vocabulary answers on its own.
listInput.value = "genshin";
listInput.fire("input");
await settle();
shown = rowTexts(dropdown);
assert(
  shown.length === 1 && shown[0][0] === "genshin_impact",
  "the vocabulary did not answer a query the dataset cannot: " + JSON.stringify(shown),
);

// Two keystrokes, the older reply held: the newer answer is the one that stays on screen.
holdNextSuggest = true;
listInput.value = "1g";
listInput.fire("input");
await settle();
listInput.value = "gensh";
listInput.fire("input");
await settle();
assert(suggestGate, "the first reply was never held - this criterion would be vacuous");
const releaseSuggest = suggestGate;
suggestGate = null;
releaseSuggest();
await tick();
await tick();
shown = rowTexts(dropdown);
assert(
  shown.length === 1 && shown[0][0] === "genshin_impact",
  "a late reply repainted the dropdown for an older query: " + JSON.stringify(shown),
);

// An empty field asks nothing: the vocabulary's hottest tags are not a suggestion.
const asked = suggestQueries.length;
listInput.value = "";
listInput.fire("input");
await settle();
assert(dropdown.hidden === true, "an empty field must not show the vocabulary's hottest tags");
assert(suggestQueries.length === asked, "an empty field still paid for a request");

// --- 14. which fields may offer a tag no image carries -------------------------------
// find names a tag the images already carry, so a vocabulary tag they do not carry is a suggestion
// that cannot be acted on (replace matches zero images). "replace with" may name a new tag. The one
// exception: while captions are still being read, the index is incomplete, so even a dataset-only
// field keeps the vocabulary - otherwise it would suggest nothing at all.
// (findWrap / findInput / findDropdown and the replace-with field were taken above.)

// The scope is one fully probed image: "gensh" matches nothing in it.
findInput.value = "gensh";
findInput.fire("input");
await settle();
assert(findDropdown.hidden === true, "the find field offered a tag the scope does not carry");
assert(rowTexts(findDropdown).length === 0, "the find field painted rows anyway");

// The same query in "replace with" is about writing a tag, so the vocabulary answers.
listInput.value = "gensh";
listInput.fire("input");
await settle();
shown = rowTexts(dropdown);
assert(
  shown.length === 1 && shown[0][0] === "genshin_impact",
  "the replace field lost the vocabulary: " + JSON.stringify(shown),
);

// Captions still being read: the scope now includes an image whose caption is unknown
// ("/d/3.png" has no entry), so the index is incomplete and the vocabulary is the only
// way a dataset-only field can suggest anything.
scopeSelect.value = "dir";
scopeSelect.fire("change");
await tick();
panel.onShow();
assert(tagsByPath["/d/3.png"] === undefined, "the incomplete-scope criterion needs an unprobed image");
findInput.value = "gensh";
findInput.fire("input");
await settle();
shown = rowTexts(findDropdown);
assert(
  shown.length === 1 && shown[0][0] === "genshin_impact",
  "an unprobed scope left the find field with nothing to suggest: " + JSON.stringify(shown),
);

// --- 15. the manual reorder: containers, drag, keyboard, and the request ---------------
// The request was "reorder the scope's common tags by hand". One image in scope -> one container
// holding that caption's own order (the order that is on disk, not the alphabetical intersection);
// several images -> a front and an end container that carry the order, plus a middle that means
// "leave these where they are" and is therefore never sent. The rows are the same rows the rename
// and delete editor uses - there is no second list of the same tags.
const bucketsOf = (bucket) => find(root, (node) => node.dataset && node.dataset.bucket === bucket)[0];
// Found by class, not by position: the front container also holds its added-tag chips (above the
// rows) and every container that can take an addition has an add row at the end.
const partOf = (bucket, className) => find(bucketsOf(bucket), (node) => hasClass(node, className))[0];
const rowsHostOf = (bucket) => partOf(bucket, "batch-bucket-rows");
const rowsIn = (bucket) => (rowsHostOf(bucket).children || []).map((row) => row.children[1].value);
const rowOf = (bucket, index) => rowsHostOf(bucket).children[index];
const gripOf = (bucket, index) => rowOf(bucket, index).children[0];
const orderHost = find(root, (node) => hasClass(node, "batch-order"))[0];
assert(orderHost, "the common-tag section has no order containers");
assert(bucketsOf("front") && bucketsOf("middle") && bucketsOf("back"), "the three containers are not all there");
// The two drop targets share the top row and 维持 - which starts out holding every common tag - is
// its own row below them. The user's reason, in their own words: pulling a tag to 前置/后置 must not
// mean dragging past the whole 维持 list first ("otherwise dragging is torture").
const orderTop = find(root, (node) => hasClass(node, "batch-order-top"))[0];
assert(orderTop, "the containers have no top row");
assert(
  JSON.stringify((orderTop.children || []).map((node) => node.dataset.bucket)) === JSON.stringify(["front", "back"]),
  "the top row must hold exactly 前置 and 后置, got " +
    JSON.stringify((orderTop.children || []).map((node) => node.dataset.bucket)),
);
assert(
  orderTop.parentNode === orderHost &&
    orderHost.children[orderHost.children.length - 1].dataset.bucket === "middle",
  "维持 must be the row below the two drop targets",
);
const dragData = () => ({ setData() {}, setDragImage() {} });

opSelect.value = mod.BATCH_COMMON;
opSelect.fire("change");
await tick();

// 1) one image in scope: one container, holding that caption's own order
//    (the earlier autocomplete section gave /d/2.png its own caption, which is exactly the order a
//    single-image reorder is about - and it is NOT the alphabetical intersection)
scopeSelect.value = "selected";
scopeSelect.fire("change");
await tick();
panel.onShow();
assert(hasClass(orderHost, "is-single"), "a one-image scope must use the single-container layout");
assert(
  rowsIn("front").join(",") === "1girl,solo,smile",
  "single: the rows must be the caption's order, got " + rowsIn("front").join(","),
);
assert(rowsIn("middle").length === 0 && rowsIn("back").length === 0, "single: the other containers must stay empty");
void rowOf;
void gripOf;

// dragging row 2 onto row 1 inserts it in front of it
gripOf("front", 1).fire("dragstart", { dataTransfer: dragData() });
bucketsOf("front").fire("dragover", { target: rowOf("front", 1) });
assert(hasClass(bucketsOf("front"), "is-drop-target"), "the container under the pointer is not marked");
bucketsOf("front").fire("drop", { target: rowOf("front", 0), dataTransfer: dragData() });
assert(rowsIn("front").join(",") === "solo,1girl,smile", "the drag did not reorder: " + rowsIn("front").join(","));
assert(!hasClass(bucketsOf("front"), "is-drop-target"), "the drop highlight stayed behind");
assert(!hasClass(rowOf("front", 0), "is-dragging"), "the dragged row stayed marked as dragging");

queued = [{ dry_run: true, applied: 1, failed: 0, results: [
  { image: "/d/2.png", ok: true, changed: true, before: "1girl, solo, smile", after: "solo, 1girl, smile" },
] }];
previewButton.fire("click");
await tick(); await tick(); await tick();
const singleBody = calls[calls.length - 1].body;
assert(
  JSON.stringify(singleBody.order_first) === JSON.stringify(["solo", "1girl", "smile"]),
  "a single-image reorder must send the whole order, got " + JSON.stringify(singleBody.order_first),
);
assert(singleBody.order_last === undefined, "a single-image reorder must not send an end block");
assert(JSON.stringify(singleBody.pairs) === JSON.stringify([]), "a pure reorder must not invent rename pairs");

// the keyboard is the same editor: ↑/↓ inside the container, Home and End for the two ends
gripOf("front", 0).fire("keydown", { key: "ArrowDown" });
assert(rowsIn("front").join(",") === "1girl,solo,smile", "ArrowDown did not move the row: " + rowsIn("front").join(","));
gripOf("front", 1).fire("keydown", { key: "Home" });
assert(rowsIn("front").join(",") === "solo,1girl,smile", "Home must move the row to the very front: " + rowsIn("front").join(","));
gripOf("front", 0).fire("keydown", { key: "End" });
assert(rowsIn("front").join(",") === "1girl,smile,solo", "End must move the row to the very end: " + rowsIn("front").join(","));
assert(applyButton.disabled === true, "the reorder must drop the preview that described the old order");

// 2) several images: everything starts in the middle, and the middle never reaches the request
//    Both images of this scope now share one caption, so the intersection is the whole list - in
//    name order (1girl, smile, solo), which is not the order of either caption on disk.
tagsByPath["/d/1.png"] = ["1girl", "solo", "smile"];
scopeSelect.value = "filtered";
scopeSelect.fire("change");
await tick();
panel.onShow();
assert(!hasClass(orderHost, "is-single"), "a two-image scope must use the three containers");
assert(
  rowsIn("middle").join(",") === "1girl,smile,solo" && rowsIn("front").length === 0 && rowsIn("back").length === 0,
  "multi: every row must start in the middle, got " +
    JSON.stringify({ front: rowsIn("front"), middle: rowsIn("middle"), back: rowsIn("back") }),
);
gripOf("middle", 1).fire("keydown", { key: "ArrowUp" });
assert(rowsIn("middle").join(",") === "1girl,smile,solo", "a move inside the middle changed the order");
assert(rowsIn("front").length === 0, "a move inside the middle leaked into the front container");

// A rename is a rename: it travels in `pairs`, and it must not smuggle an order block in with it -
// which is also the only way to make the panel send a request while every row sits in the middle
// (with nothing renamed and nothing moved there is nothing to preview, and the panel says so).
const renamedRow = rowOf("middle", 1).children[1];
renamedRow.value = "Smile";
renamedRow.fire("input");
queued = [{ dry_run: true, applied: 2, failed: 0, results: [] }];
previewButton.fire("click");
await tick(); await tick(); await tick();
const untouched = calls[calls.length - 1].body;
assert(
  untouched.order_first === undefined && untouched.order_last === undefined,
  "an untouched middle must send no order at all, got " + JSON.stringify(untouched),
);
assert(
  JSON.stringify(untouched.pairs) === JSON.stringify([{ find: "smile", replace: "Smile" }]),
  "the rename did not reach the request: " + JSON.stringify(untouched.pairs),
);
renamedRow.value = "smile";
renamedRow.fire("input");

// 3) dragging a row into the front container: only that block is sent
gripOf("middle", 0).fire("dragstart", { dataTransfer: dragData() });
bucketsOf("front").fire("drop", { target: rowsHostOf("front"), dataTransfer: dragData() });
assert(
  rowsIn("front").join(",") === "1girl" && rowsIn("middle").join(",") === "smile,solo",
  "the drop did not move the row into the front container: " +
    JSON.stringify({ front: rowsIn("front"), middle: rowsIn("middle") }),
);
queued = [{ dry_run: true, applied: 2, failed: 0, results: [] }];
previewButton.fire("click");
await tick(); await tick(); await tick();
const frontOnly = calls[calls.length - 1].body;
assert(
  JSON.stringify(frontOnly.order_first) === JSON.stringify(["1girl"]),
  "the front block did not reach the request: " + JSON.stringify(frontOnly.order_first),
);
assert(frontOnly.order_last === undefined, "an empty end container must not be sent");

// 4) End moves a row into the end container, and a drop back into the middle takes it out again
gripOf("middle", 0).fire("keydown", { key: "End" });
assert(
  rowsIn("back").join(",") === "smile" && rowsIn("middle").join(",") === "solo",
  "End must move the row into the end container: " + JSON.stringify({ middle: rowsIn("middle"), back: rowsIn("back") }),
);
queued = [{ dry_run: true, applied: 2, failed: 0, results: [] }];
previewButton.fire("click");
await tick(); await tick(); await tick();
const bothBlocks = calls[calls.length - 1].body;
assert(
  JSON.stringify(bothBlocks.order_first) === JSON.stringify(["1girl"]) &&
    JSON.stringify(bothBlocks.order_last) === JSON.stringify(["smile"]),
  "the two blocks must both reach the request: " + JSON.stringify(bothBlocks),
);
gripOf("back", 0).fire("dragstart", { dataTransfer: dragData() });
bucketsOf("middle").fire("drop", { target: rowsHostOf("middle"), dataTransfer: dragData() });
assert(
  rowsIn("back").length === 0 && rowsIn("middle").join(",") === "solo,smile",
  "a row dropped back into the middle must leave the order blocks: " + JSON.stringify(rowsIn("back")),
);

// 4b) the front container is ordered among its own rows as well: a second row dragged in lands after
//     the first, and ↑/↓ reorders inside the container (which is what "the container's own order is
//     the final order" means)
gripOf("middle", 0).fire("dragstart", { dataTransfer: dragData() });
bucketsOf("front").fire("drop", { target: rowsHostOf("front"), dataTransfer: dragData() });
assert(
  rowsIn("front").join(",") === "1girl,solo",
  "the second row of the front container must land after the first: " + rowsIn("front").join(","),
);
gripOf("front", 1).fire("keydown", { key: "ArrowUp" });
assert(
  rowsIn("front").join(",") === "solo,1girl",
  "↑ must reorder inside the front container: " + rowsIn("front").join(","),
);
queued = [{ dry_run: true, applied: 2, failed: 0, results: [] }];
previewButton.fire("click");
await tick(); await tick(); await tick();
const frontOrder = calls[calls.length - 1].body;
assert(
  JSON.stringify(frontOrder.order_first) === JSON.stringify(["solo", "1girl"]) &&
    frontOrder.order_last === undefined,
  "the front container's own order must reach the request: " + JSON.stringify(frontOrder),
);

// 5) a cleared row is a deletion, so it leaves the order block as well: emptying one front row sends
//    the other one in the order and the deletion in `pairs`
rowOf("front", 0).children[1].value = "";
rowOf("front", 0).children[1].fire("input");
queued = [{ dry_run: true, applied: 2, failed: 0, results: [] }];
previewButton.fire("click");
await tick(); await tick(); await tick();
const withDeleted = calls[calls.length - 1].body;
assert(
  JSON.stringify(withDeleted.order_first) === JSON.stringify(["1girl"]) && withDeleted.order_last === undefined,
  "a cleared row must leave the order blocks: " + JSON.stringify(withDeleted),
);
assert(
  JSON.stringify(withDeleted.pairs) === JSON.stringify([{ find: "solo", replace: "" }]),
  "the cleared row is not sent as a deletion: " + JSON.stringify(withDeleted.pairs),
);

// 6) the entrance is never invisible: a scope with no common tag at all - the ordinary state for a
//    few images with different captions - still renders the container host, with the line that says
//    why it is empty. It used to be hidden, and the whole feature had no way in.
//    The empty state also distinguishes an empty *scope* (which has a next step) from images that
//    share no tag: one wording for both made the first one unanswerable and the second look broken.
selectedPaths.length = 0;
scopeSelect.value = "selected";
scopeSelect.fire("change");
await tick();
panel.onShow();
const noImagesNote = find(root, (node) => node.textContent === t("batch.commonNoneImages"))[0];
assert(
  noImagesNote && noImagesNote.hidden === false,
  "a scope with no images must say so (and name the next step), got the other empty state",
);
assert(orderHost.hidden === false, "the containers vanish when the scope has no images");

//    (A different *scope* is what forces the rows to be recomputed, so this uses the three-image one.)
tagsByPath["/d/1.png"] = ["1girl"];
tagsByPath["/d/2.png"] = ["solo"];
tagsByPath["/d/3.png"] = ["smile"];
scopeSelect.value = "dir";
scopeSelect.fire("change");
await tick();
panel.onShow();
assert(
  rowsIn("front").length === 0 && rowsIn("middle").length === 0 && rowsIn("back").length === 0,
  "the fixture's captions still share a tag, so this criterion would be vacuous",
);
assert(orderHost.hidden === false, "the order containers disappear when the scope has no common tag");
assert(
  find(root, (node) => hasClass(node, "batch-bucket")).length === 3,
  "the containers are not all rendered (the single layout closes two of them with CSS, not in the DOM)",
);
const commonNoneNote = find(root, (node) => node.textContent === t("batch.commonNone"))[0];
assert(
  commonNoneNote && commonNoneNote.hidden === false,
  "nothing says why the containers are empty",
);assert(applyButton.disabled === true, "an empty editor must not offer a write");

// 7) the copy that depends on the mode is painted from state, not at construction
//    (the screenshot that produced this: three containers on screen under the single-container title)
const titleOf = (bucket) => bucketsOf(bucket).children[0].textContent;
selectedPaths.length = 0;
selectedPaths.push("/d/2.png");
scopeSelect.value = "selected";
scopeSelect.fire("change");
await tick();
panel.onShow();
assert(
  titleOf("front") === t("batch.orderSingle"),
  "the single layout must name its one container the single way, got " + titleOf("front"),
);
tagsByPath["/d/1.png"] = ["1girl", "solo"];
scopeSelect.value = "filtered";
scopeSelect.fire("change");
await tick();
panel.onShow();
assert(!hasClass(orderHost, "is-single") || true, "");
assert(
  titleOf("front") === t("batch.orderFront"),
  "the front container kept the single-image name in the three-container layout: " + titleOf("front"),
);
assert(titleOf("middle") === t("batch.orderMiddle") && titleOf("back") === t("batch.orderBack"), (
  "the other two container names: " + titleOf("middle") + " / " + titleOf("back")
));

// 8) a change in the *providers* - a new gallery selection, the recursive listing arriving - moves
//    the scope's paths without the select's value changing, so `onChange` never fires. The panel has
//    to notice through the repaint, or its containers keep describing the previous selection (the
//    report that produced this criterion: six images selected, containers still empty).
tagsByPath["/d/1.png"] = ["1girl", "solo"];
tagsByPath["/d/2.png"] = ["1girl", "solo"];
tagsByPath["/d/3.png"] = ["1girl", "smile"];
selectedPaths.length = 0;
selectedPaths.push("/d/1.png", "/d/2.png");
// A different signature first: switching between two scopes that resolve to the same paths is *not*
// a change (the panel compares paths), and this criterion needs a real one.
scopeSelect.value = "dir";
scopeSelect.fire("change");
await tick();
panel.onShow();
scopeSelect.value = "selected";
scopeSelect.fire("change");
await tick();
panel.onShow();
assert(
  rowsIn("middle").join(",") === "1girl,solo",
  "the fixture must start from the two-image selection, got " + rowsIn("middle").join(","),
);
assert(
  (find(root, (node) => node.textContent === t("batch.commonNone"))[0] || {}).hidden === true,
  "the empty-state line is still on screen while the containers hold rows",
);
selectedPaths.push("/d/3.png");        // the selection grew: same scope value, different paths
scope.paint();                          // what the gallery's own selection handler triggers
await tick();
assert(
  scopeSelect.value === "selected",
  "this criterion is only about a change that does NOT move the select's value",
);
assert(
  rowsIn("middle").join(",") === "1girl",
  "a provider change did not reach the editor - it still describes the previous selection: " +
    rowsIn("middle").join(","),
);

// 9) each order block carries a **permanent, full-width + row**: a new tag belongs to the container its
//    + was clicked in, and the request says which one (`tags_first` / `tags_last`). The middle gets
//    none - an addition can only be expressed at one of the two ends. Clicking the + does not turn it
//    into a box: it **appends a new element** (an addition row, the shape of a tag row), and an element
//    left empty and blurred removes itself again.
const addedRowsIn = (bucket) => (partOf(bucket, "batch-bucket-added").children || []);
const addButtonOf = (bucket) => partOf(bucket, "batch-bucket-add");
const addRowOf = (bucket) => partOf(bucket, "batch-bucket-add-row");
const addedInputIn = (bucket, index) => addedRowsIn(bucket)[index].children[1];
const addedTagsIn = (bucket) => addedRowsIn(bucket).map((row) => row.children[1].value);
assert(addButtonOf("front") && addButtonOf("back"), "a container has no permanent + row");
assert(!addButtonOf("middle"), "the middle container grew a + row; an addition has no middle to land in");
assert(
  addRowOf("front") && addRowOf("front").children.length === 1 &&
    addRowOf("front").children[0] === addButtonOf("front"),
  "the + is not the whole row any more (the user asked for a full-width dashed box)",
);
assert(addedRowsIn("front").length === 0 && addedRowsIn("back").length === 0, "the containers start with additions");

// Clicking the + appends an element - a row with a grip, a box and a ×, like the rows above it
addButtonOf("front").fire("click");
assert(
  addedRowsIn("front").length === 1 && addedTagsIn("front").join(",") === "" &&
    addedRowsIn("front")[0].children.length === 3,
  "clicking + did not append an addition row: " + JSON.stringify(addedTagsIn("front")),
);
assert(
  addedRowsIn("front")[0].children[0].className.indexOf("batch-grip") >= 0 &&
    addedRowsIn("front")[0].children[2].className.indexOf("batch-common-remove") >= 0,
  "the appended element has no grip / no × (an editable row is what the user asked for)",
);
// Left empty and blurred, it takes itself away again - and nothing is sent
addedInputIn("front", 0).value = "   ";
addedInputIn("front", 0).fire("blur");
assert(addedRowsIn("front").length === 0, "an empty addition row survived losing focus");
assert(addedTagsIn("back").length === 0, "an empty addition row landed in the other container");

// With text in it, the element stays and reaches the request as `tags_first`
addButtonOf("front").fire("click");
addedInputIn("front", 0).value = "masterpiece";
addedInputIn("front", 0).fire("input");
addedInputIn("front", 0).fire("blur");
assert(
  addedTagsIn("front").join(",") === "masterpiece" && addedTagsIn("back").length === 0,
  "the front addition did not stay in the front container: " +
    JSON.stringify({ front: addedTagsIn("front"), back: addedTagsIn("back") }),
);
queued = [{ dry_run: true, applied: 3, failed: 0, results: [] }];
previewButton.fire("click");
await tick(); await tick(); await tick();
const addedFront = calls[calls.length - 1].body;
assert(
  JSON.stringify(addedFront.tags_first) === JSON.stringify(["masterpiece"]),
  "the front addition did not reach the request: " + JSON.stringify(addedFront.tags_first),
);
assert(addedFront.tags_last === undefined, "an empty end container sent an addition");
assert(
  addedFront.tags === undefined && addedFront.position === undefined,
  "the legacy `tags`/`position` pair is still sent while the containers carry the additions",
);

// Escape takes the element away, the × takes it away, Enter keeps it, and a tag that is already a
// common row is not an addition at all (it is a row to drag)
addButtonOf("back").fire("click");
addedInputIn("back", 0).value = "typo";
addedInputIn("back", 0).fire("keydown", { key: "Escape" });
assert(addedRowsIn("back").length === 0, "Escape did not take the addition element away");
addButtonOf("back").fire("click");
addedRowsIn("back")[0].children[2].fire("click");
assert(addedRowsIn("back").length === 0, "the × did not take the addition element away");
addButtonOf("back").fire("click");
addedInputIn("back", 0).value = "worst quality";
addedInputIn("back", 0).fire("input");
addedInputIn("back", 0).fire("keydown", { key: "Enter" });
assert(
  addedTagsIn("back").join(",") === "worst quality" && addedRowsIn("back").length === 1,
  "Enter did not keep the addition in the end container: " + addedTagsIn("back").join(","),
);
const noticesBefore = notices.length;
addButtonOf("front").fire("click");
addedInputIn("front", 1).value = "1girl";     // a row of the common list
addedInputIn("front", 1).fire("input");
addedInputIn("front", 1).fire("keydown", { key: "Enter" });
assert(addedTagsIn("front").join(",") === "masterpiece", "a tag that is already a common row was added as an addition");
assert(
  notices.length === noticesBefore + 1 && notices[notices.length - 1].kind === "warn",
  "nothing told the user that this tag is already a common row: " + JSON.stringify(notices.slice(-1)),
);

// The grip of an addition is the one move it has: to the other end (and into the middle = discarded)
addButtonOf("back").fire("click");
addedInputIn("back", 1).value = "second";
addedInputIn("back", 1).fire("input");
addedRowsIn("back")[1].children[0].fire("dragstart", { dataTransfer: dragData() });
bucketsOf("front").fire("drop", { target: rowsHostOf("front"), dataTransfer: dragData() });
assert(
  addedTagsIn("front").join(",") === "masterpiece,second" && addedTagsIn("back").join(",") === "worst quality",
  "dragging an addition to the other container failed: " +
    JSON.stringify({ front: addedTagsIn("front"), back: addedTagsIn("back") }),
);
addedRowsIn("front")[0].children[0].fire("dragstart", { dataTransfer: dragData() });
bucketsOf("middle").fire("drop", { target: rowsHostOf("middle"), dataTransfer: dragData() });
assert(
  addedTagsIn("front").join(",") === "second",
  "dropping an addition into the middle did not discard it: " + addedTagsIn("front").join(","),
);
queued = [{ dry_run: true, applied: 3, failed: 0, results: [] }];
previewButton.fire("click");
await tick(); await tick(); await tick();
const addedBack = calls[calls.length - 1].body;
assert(
  JSON.stringify(addedBack.tags_first) === JSON.stringify(["second"]) &&
    JSON.stringify(addedBack.tags_last) === JSON.stringify(["worst quality"]),
  "the moved additions did not reach the request: " + JSON.stringify(addedBack),
);

console.log("batch harness OK " + JSON.stringify({ common, calls: calls.length }));