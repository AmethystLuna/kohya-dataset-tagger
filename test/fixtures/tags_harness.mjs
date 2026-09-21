/**
 * Headless behaviour harness for web/js/tags.js - the two ways a caption edit
 * could be thrown away without the user ever agreeing to it.
 *
 * 1. A failed PUT used to be reported and then ignored: the next tile click
 *    reloaded the editor and the typed tags were gone. flush() now tells the
 *    caller that the write failed, so activateImage can refuse to navigate.
 * 2. Leaving raw text mode re-parses only the first line, which deletes
 *    everything below it - and the multiline warning disappears in the same
 *    tick, so the guardrail vanished exactly when it was needed.
 *
 * Usage: node tags_harness.mjs <absolute path to web/>
 */
import { pathToFileURL } from "node:url";

const WEB = process.argv[2];
if (!WEB) throw new Error("usage: node tags_harness.mjs <web dir>");

function assert(condition, message) {
  if (!condition) throw new Error("tags harness: " + message);
}

class FakeClassList {
  constructor(node) { this.node = node; }
  get set() { return new Set(String(this.node.className || "").split(/\s+/).filter(Boolean)); }
  add(...names) { const next = this.set; for (const name of names) next.add(name); this.node.className = Array.from(next).join(" "); }
  remove(...names) { const next = this.set; for (const name of names) next.delete(name); this.node.className = Array.from(next).join(" "); }
  contains(name) { return this.set.has(name); }
  toggle(name, on) { if (on) this.add(name); else this.remove(name); }
}

class FakeNode {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase();
    this.children = [];
    this.className = "";
    this.textContent = "";
    this.dataset = {};
    this.attributes = {};
    this.listeners = {};
    this.style = {};
    this.hidden = false;
    this.disabled = false;
    this.value = "";
    this.checked = false;
    this.title = "";
    this.parentNode = null;
    this.classList = new FakeClassList(this);
  }
  append(...nodes) { for (const node of nodes) { if (node && typeof node === "object") node.parentNode = this; this.children.push(node); } }
  replaceChildren(...nodes) { this.children = []; this.append(...nodes); }
  insertAdjacentElement(position, node) {
    const parent = this.parentNode;
    if (!parent || position !== "afterend") return null;
    const at = parent.children.indexOf(this);
    parent.children.splice(at + 1, 0, node);
    node.parentNode = parent;
    return node;
  }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name]; }
  contains(node) {
    if (node === this) return true;
    for (const child of this.children) if (child && child.contains && child.contains(node)) return true;
    return false;
  }
  focus() { globalThis.document.activeElement = this; }
  addEventListener(type, handler) { (this.listeners[type] ||= []).push(handler); }
  removeEventListener(type, handler) { this.listeners[type] = (this.listeners[type] || []).filter((item) => item !== handler); }
  fire(type, event) { for (const handler of [...(this.listeners[type] || [])]) handler(event || { preventDefault() {} }); }
}

const body = new FakeNode("body");
const confirmAnswers = [];
let confirmCalls = [];
globalThis.document = {
  body,
  activeElement: null,
  createElement: (tag) => new FakeNode(tag),
  createTextNode: (text) => ({ nodeType: 3, textContent: text }),
  addEventListener() {},
  removeEventListener() {},
};
globalThis.window = {
  setTimeout,
  clearTimeout,
  confirm: (message) => { confirmCalls.push(message); return confirmAnswers.length ? confirmAnswers.shift() : false; },
};

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
const el = (tag, className) => { const node = new FakeNode(tag); if (className) node.className = className; return node; };

const tagsMod = await import(pathToFileURL(WEB + "/js/tags.js").href);
const apiMod = await import(pathToFileURL(WEB + "/js/api.js").href);

// ---- 1. the pure decision -------------------------------------------------
const { wouldDropLines } = tagsMod;
assert(typeof wouldDropLines === "function", "wouldDropLines is not exported");
assert(wouldDropLines("a\nb") === true, "a second line with text must count as loss");
assert(wouldDropLines("a\n\n  \n") === false, "trailing blank lines are not loss");
assert(wouldDropLines("a") === false, "a single line is not loss");
assert(wouldDropLines("") === false, "an empty buffer is not loss");

// ---- 2. an editor over a stub API ----------------------------------------
const TAGS = { "/a/one.png": ["1girl", "solo"] };
let putFails = false;
const puts = [];
apiMod.api.getCaption = async (path) => ({
  image: path,
  caption_path: path.replace(/\.png$/, ".txt"),
  exists: true,
  text: (TAGS[path] || []).join(", "),
  tags: (TAGS[path] || []).slice(),
  multiline_warning: false,
});
apiMod.api.putCaption = async (payload) => {
  puts.push(payload);
  if (putFails) throw new Error("disk is read-only");
  const tags = Array.isArray(payload.tags) ? payload.tags.slice() : [];
  TAGS[payload.image] = tags;
  return { image: payload.image, caption_path: payload.image.replace(/\.png$/, ".txt"), cache_removed: [] };
};

const errors = [];
const nodes = {
  chips: el("div", "chips"),
  input: el("input"),
  suggest: el("div", "suggest"),
  addButton: el("button"),
  saveButton: el("button"),
  state: el("span", "save-state"),
  warning: el("div", "warning"),
  captionFile: el("div", "hint"),
  rawToggle: el("input"),
  rawText: el("textarea"),
};
nodes.chips.parentNode = body;
const editor = tagsMod.createTagEditor(Object.assign({}, nodes, {
  // The raw-mode exit asks through the in-app dialog; the harness answers it.
  confirm: async (spec) => {
    confirmCalls.push(spec && spec.body);
    return confirmAnswers.length ? confirmAnswers.shift() : false;
  },
  onSaved: () => {},
  onError: (message) => errors.push(message),
  onNotify: () => {},
}));

await editor.load("/a/one.png");
assert(JSON.stringify(editor.getTags()) === JSON.stringify(["1girl", "solo"]), "load did not fill the chips");
assert(editor.isDirty() === false, "a freshly loaded caption must not be dirty");

// nothing to write is not a failure
assert((await editor.flush()) === "clean", "flushing a clean editor must report clean");

// ---- 3. a failed write is reported, and the text survives ----------------
nodes.input.value = "smile";
nodes.input.fire("keydown", { key: "Enter", preventDefault() {} });
await tick();
assert(editor.isDirty(), "adding a tag must mark the caption dirty");
putFails = true;
const failed = await editor.flush();
assert(failed === "failed", "a failed write must be reported as failed, got " + failed);
assert(editor.isDirty(), "the text must survive a failed write");
assert(nodes.state.classList.contains("is-error"), "the editor must keep showing the failure");
assert(errors.length === 1, "the failure must be reported once, got " + errors.length);
assert(JSON.stringify(editor.getTags()) === JSON.stringify(["1girl", "solo", "smile"]), "the typed tag must still be there");

// ---- 4. retrying succeeds and clears the state ---------------------------
putFails = false;
assert((await editor.flush()) === "saved", "a retry must report saved");
assert(editor.isDirty() === false, "a successful write clears dirty");
assert(!nodes.state.classList.contains("is-error"), "the error state must clear after a successful write");
assert(puts.length === 2, "the retry must really have been sent");

// ---- 5. leaving raw mode with text below line 1 asks first ---------------
nodes.rawToggle.checked = true;
nodes.rawToggle.fire("change");
nodes.rawText.value = "1girl, solo\nsmile";
const tagsBefore = JSON.stringify(editor.getTags());
confirmAnswers.push(false);
confirmCalls = [];
nodes.rawToggle.checked = false;
nodes.rawToggle.fire("change");
// the handler awaits the dialog now, so the outcome lands a microtask later
await tick();
assert(confirmCalls.length === 1, "leaving raw mode with extra lines must ask");
assert(nodes.rawToggle.checked === true, "declining must put the editor back in raw mode");
assert(JSON.stringify(editor.getTags()) === tagsBefore, "declining must not touch the tag buffer");
assert(nodes.rawText.value.indexOf("smile") >= 0, "declining must leave the raw buffer alone");

confirmAnswers.push(true);
confirmCalls = [];
nodes.rawToggle.checked = false;
nodes.rawToggle.fire("change");
await tick();
assert(confirmCalls.length === 1, "accepting must still be a decision, not a silent truncation");
assert(JSON.stringify(editor.getTags()) === JSON.stringify(["1girl", "solo"]), "accepting keeps the first line only");

// trailing blank lines are not worth a question
nodes.rawToggle.checked = true;
nodes.rawToggle.fire("change");
nodes.rawText.value = "1girl, solo\n\n   \n";
confirmCalls = [];
nodes.rawToggle.checked = false;
nodes.rawToggle.fire("change");
await tick();
assert(confirmCalls.length === 0, "blank trailing lines must not trigger a confirmation");
assert(JSON.stringify(editor.getTags()) === JSON.stringify(["1girl", "solo"]), "blank lines must not become tags");

console.log("tags harness OK");