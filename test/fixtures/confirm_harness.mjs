/**
 * Headless behaviour harness for web/js/confirm.js - the dialog that replaced
 * window.confirm().
 *
 * The point of the replacement is that the buttons say what is about to happen
 * (and in the interface language), so this drives the real module against a fake
 * DOM and checks the three answers a user can give plus what each one does.
 *
 * Usage: node confirm_harness.mjs <absolute path to web/>
 */
import { pathToFileURL } from "node:url";

const WEB = process.argv[2];
if (!WEB) throw new Error("usage: node confirm_harness.mjs <web dir>");

function assert(condition, message) {
  if (!condition) throw new Error("confirm harness: " + message);
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
    this.attributes = {};
    this.listeners = {};
    this.hidden = false;
    this.parentNode = null;
    this.classList = new FakeClassList(this);
  }
  append(...nodes) { for (const node of nodes) { if (node && typeof node === "object") node.parentNode = this; this.children.push(node); } }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name]; }
  focus() { globalThis.document.activeElement = this; }
  remove() {
    const parent = this.parentNode;
    if (!parent) return;
    parent.children = parent.children.filter((node) => node !== this);
    this.parentNode = null;
  }
  addEventListener(type, handler) { (this.listeners[type] ||= []).push(handler); }
  removeEventListener(type, handler) { this.listeners[type] = (this.listeners[type] || []).filter((item) => item !== handler); }
  fire(type, event) { for (const handler of [...(this.listeners[type] || [])]) handler(event || { preventDefault() {} }); }
}

const body = new FakeNode("body");
const docListeners = {};
globalThis.document = {
  body,
  activeElement: null,
  createElement: (tag) => new FakeNode(tag),
  addEventListener: (type, handler) => { (docListeners[type] ||= []).push(handler); },
  removeEventListener: (type, handler) => { docListeners[type] = (docListeners[type] || []).filter((item) => item !== handler); },
};
globalThis.window = { setTimeout, clearTimeout };

const find = (root, predicate, out = []) => {
  if (predicate(root)) out.push(root);
  for (const child of root.children || []) {
    if (child && typeof child === "object" && child.children) find(child, predicate, out);
  }
  return out;
};
const byClass = (root, name) => find(root, (node) => node.classList && node.classList.contains(name));
const textOf = (node) => String(node.textContent || "");

const { createConfirm } = await import(pathToFileURL(WEB + "/js/confirm.js").href);
const { t } = await import(pathToFileURL(WEB + "/js/strings.js").href);
assert(typeof createConfirm === "function", "createConfirm is not exported");
const confirm = createConfirm();

// 1) the action button says what will happen, and the body carries the reason
const trigger = new FakeNode("button");
trigger.focus();
let answer = null;
const asked = confirm.ask({ body: "Delete 2 cache files?", confirmLabel: "Rebuild", danger: true });
asked.then((value) => { answer = value; });
const backdrop = byClass(body, "confirm-backdrop")[0];
assert(backdrop, "ask() did not build a dialog");
const buttons = find(backdrop, (node) => node.tagName === "BUTTON");
const labels = buttons.map(textOf);
assert(labels.indexOf("Rebuild") >= 0, "the action button does not name the action: " + labels.join(", "));
assert(labels.indexOf(t("common.cancel")) >= 0, "there is no way to say no: " + labels.join(", "));
// (the default labels come from the resolved locale, which is the whole point)
assert(find(backdrop, (node) => textOf(node).indexOf("Delete 2 cache files?") >= 0).length > 0, "the body is missing");

// 2) a destructive question focuses Cancel, so a stray Enter cannot delete
const cancelButton = buttons.find((node) => textOf(node) === t("common.cancel"));
const actionButton = buttons.find((node) => textOf(node) === "Rebuild");
assert(globalThis.document.activeElement === cancelButton, "a dangerous dialog must focus Cancel, not the action");

// 3) the action button is the only yes
actionButton.fire("click");
await Promise.resolve();
assert(answer === true, "the action button must resolve true, got " + answer);
assert(byClass(body, "confirm-backdrop").length === 0, "the dialog must be removed after answering");
assert(globalThis.document.activeElement === trigger, "focus must be handed back to the trigger");
assert((docListeners.keydown || []).length === 0, "the Escape listener must be removed with the dialog");

// 4) Escape, Cancel and a click on the backdrop all mean no
for (const [label, act] of [
  ["Escape", (ctx) => { for (const handler of [...(docListeners.keydown || [])]) handler({ key: "Escape", preventDefault() {} }); }],
  ["Cancel", (ctx) => { find(ctx.backdrop, (node) => textOf(node) === t("common.cancel"))[0].fire("click"); }],
  ["the backdrop", (ctx) => { ctx.backdrop.fire("click", { target: ctx.backdrop }); }],
]) {
  let result = null;
  void confirm.ask({ body: "Leave?", confirmLabel: "Leave" }).then((value) => { result = value; });
  const ctx = { backdrop: byClass(body, "confirm-backdrop")[0] };
  assert(ctx.backdrop, "no dialog for the " + label + " case");
  // a click inside the dialog itself must not count as "outside"
  const dialogNode = byClass(ctx.backdrop, "confirm-dialog")[0];
  ctx.backdrop.fire("click", { target: dialogNode });
  await Promise.resolve();
  assert(result === null, "a click inside the dialog must not answer it (" + label + " case)");
  act(ctx);
  await Promise.resolve();
  assert(result === false, label + " must resolve false, got " + result);
  assert(byClass(body, "confirm-backdrop").length === 0, label + " must remove the dialog");
}

// 5) a second question supersedes the first (which resolves false)
let first = null;
void confirm.ask({ body: "first", confirmLabel: "One" }).then((value) => { first = value; });
let second = null;
void confirm.ask({ body: "second", confirmLabel: "Two" }).then((value) => { second = value; });
await Promise.resolve();
assert(first === false, "the superseded question must resolve false, got " + first);
assert(second === null, "the new question must still be open");
const secondButtons = find(byClass(body, "confirm-backdrop")[0], (node) => node.tagName === "BUTTON");
secondButtons.find((node) => textOf(node) === "Two").fire("click");
await Promise.resolve();
assert(second === true, "the second question must be answerable, got " + second);

console.log("confirm harness OK");