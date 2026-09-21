/**
 * Headless behaviour harness for web/js/jobs.js (the job shelf).
 *
 * The promise is narrow and testable: a panel registers a job it already owns and pushes the numbers it
 * already paints; the shelf shows **one row per running job** - two jobs can run at once, so a single
 * "busy" flag would be a lie - and its cancel calls that panel's own cancel path, once.
 *
 * The reason the shelf exists is structural and is asserted statically (the host is outside every
 * panel); what this harness covers is what happens to a row.
 *
 * Usage: node jobs_harness.mjs <absolute path to web/>
 */
import { pathToFileURL } from "node:url";

const WEB = process.argv[2];
if (!WEB) throw new Error("usage: node jobs_harness.mjs <web dir>");

class FakeClassList {
  constructor() { this.items = new Set(); }
  add(name) { this.items.add(name); }
  remove(name) { this.items.delete(name); }
  contains(name) { return this.items.has(name); }
}

class FakeNode {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase();
    this.children = [];
    this.classList = new FakeClassList();
    this.dataset = {};
    this.style = {};
    this.listeners = {};
    this.textContent = "";
    this.className = "";
    this.hidden = false;
    this.disabled = false;
    this.title = "";
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
  addEventListener(type, handler) { (this.listeners[type] ||= []).push(handler); }
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
  if (!condition) throw new Error("jobs harness: " + message);
}

globalThis.document = {
  body: new FakeNode("body"),
  createElement: (tag) => new FakeNode(tag),
  createTextNode: (text) => ({ nodeType: 3, textContent: text }),
  addEventListener() {},
  removeEventListener() {},
};
globalThis.window = { setTimeout, clearTimeout };

const mod = await import(pathToFileURL(WEB + "/js/jobs.js").href);
assert(typeof mod.createJobShelf === "function", "createJobShelf is not exported");

const host = new FakeNode("div");
host.hidden = true;
const opened = [];
const shelf = mod.createJobShelf({ host, onOpen: (kind) => opened.push(kind) });

const rows = () => find(host, (node) => node.className === "job-row");
const rowText = (row) => row.children.map((child) => child.textContent).join(" | ");
const buttonIn = (row, label) => row.children.find((child) => child.tagName === "BUTTON" && child.textContent === label);
const cancelIn = (row) => row.children.filter((child) => child.tagName === "BUTTON").pop();

// --- 1. an empty shelf is not on screen ------------------------------------
assert(host.hidden === true, "an empty shelf must stay hidden");

let cancels = 0;
const first = shelf.start({
  kind: "batch",
  label: "Batch",
  cancelLabel: "Cancel write",
  total: 10,
  cancel: () => { cancels += 1; },
});
assert(host.hidden === false, "the shelf must appear when a job starts");
assert(rows().length === 1, "one job must be one row, got " + rows().length);
assert(rowText(rows()[0]).indexOf("0/10") >= 0, "the row must show done/total, got " + rowText(rows()[0]));

first.update({ done: 3, total: 10, current: "/d/3.png" });
assert(rowText(rows()[0]).indexOf("3/10") >= 0, "update() must move the count, got " + rowText(rows()[0]));
assert(rowText(rows()[0]).indexOf("/d/3.png") >= 0, "update() must name the current file");

// --- 2. two jobs at once: nothing serialises them, so "busy" is not a boolean --
const second = shelf.start({ kind: "scale", label: "Scale", cancelLabel: "Cancel export", total: 0, cancel: () => {} });
assert(rows().length === 2, "a second job must add a second row, got " + rows().length);
assert(rowText(rows()[1]).indexOf("0/?") >= 0, "an unknown total must read as ?, not 0: " + rowText(rows()[1]));

// --- 3. cancel goes through the panel's own path, once ----------------------
cancelIn(rows()[0]).fire("click");
assert(cancels === 1, "the row's cancel must call the panel's cancel, got " + cancels);
// The repaint replaces the button, so a user's second click lands on a fresh, disabled one.
const afterCancel = cancelIn(rows()[0]);
assert(afterCancel.disabled === true, "the cancel button must be disabled once the request is out");
afterCancel.fire("click");
assert(cancels === 1, "a second click must not fire a second request, got " + cancels);

// --- 4. the label is the way to the tab that owns the job -------------------
buttonIn(rows()[0], "Batch").fire("click");
assert(opened.length === 1 && opened[0] === "batch", "the label must open the owning tab, got " + JSON.stringify(opened));

// --- 5. closing a row is what ends it, and the shelf empties ----------------
const seen = shelf.active();
assert(seen.length === 2, "active() must report both jobs");
seen[0].done = 999;
assert(shelf.active()[0].done === 3, "active() must hand out copies, not the live rows");

first.finish();
assert(rows().length === 1, "finishing a job must remove its row, got " + rows().length);
second.finish();
assert(rows().length === 0, "finishing the last job must empty the shelf");
assert(host.hidden === true, "an empty shelf must hide itself again");

console.log("jobs harness OK");
