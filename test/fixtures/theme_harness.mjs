/**
 * Headless behaviour harness for the theme (web/js/theme.js + web/js/theme-boot.js).
 *
 * What this pins, and why it is a fixture rather than a browser test: a stylesheet cannot show
 * *behaviour over time or under a hostile environment*. Four of the five things below are paths a
 * happy-path screenshot never reaches:
 *
 *   1. the boot script and the module resolve the SAME stored key (that is the only reason the key
 *      literal exists twice - the boot cannot import the module, which is what would make it a module,
 *      and a module is too late to paint);
 *   2. a storage that throws (Safari private mode, disabled storage) still themes the session and
 *      never raises - on read and on write;
 *   3. a garbage stored value falls back to light without throwing;
 *   4. the toggle writes the key and updates the attribute, and the checkbox is checked exactly when
 *      the attribute says dark - checked at every step, because "one module writes both" is the
 *      property, not "both get written eventually";
 *   5. with no boot script at all, the module still themes the session (only the first paint is the
 *      browser's own).
 *
 * Usage: node theme_harness.mjs <absolute path to web/>
 */
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

const WEB = process.argv[2];
if (!WEB) throw new Error("usage: node theme_harness.mjs <web dir>");
const moduleUrl = (rel) => pathToFileURL(WEB + "/" + rel).href;

function assert(condition, message) {
  if (!condition) throw new Error("theme harness: " + message);
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
    this.type = "";
    this.checked = false;
    this.parentNode = null;
  }
  append(...nodes) {
    for (const node of nodes) {
      if (node && typeof node === "object") node.parentNode = this;
      this.children.push(node);
    }
  }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) {
    return Object.prototype.hasOwnProperty.call(this.attributes, name) ? this.attributes[name] : null;
  }
  addEventListener(type, handler) { (this.listeners[type] ||= []).push(handler); }
  removeEventListener(type, handler) {
    this.listeners[type] = (this.listeners[type] || []).filter((item) => item !== handler);
  }
  fire(type, event) {
    const payload = Object.assign({ type, target: this, preventDefault() {} }, event || {});
    for (const handler of [...(this.listeners[type] || [])]) handler(payload);
  }
  descendants() {
    const out = [];
    for (const child of this.children) {
      if (!child || typeof child !== "object") continue;
      out.push(child, ...child.descendants());
    }
    return out;
  }
  querySelectorAll(selector) {
    const match = /^\[([a-z-]+)\]$/.exec(String(selector).trim());
    assert(match, "the harness's fake querySelectorAll only understands [attribute] selectors, got " + selector);
    return this.descendants().filter((node) => node.getAttribute && node.getAttribute(match[1]) !== null);
  }
  closest(selector) {
    const wanted = String(selector).trim();
    let node = this;
    while (node) {
      if (wanted.startsWith(".") && node.classList.contains(wanted.slice(1))) return node;
      if (node.tagName && node.tagName.toLowerCase() === wanted.toLowerCase()) return node;
      node = node.parentNode;
    }
    return null;
  }
}

globalThis.CustomEvent = class CustomEvent {
  constructor(type, init) { this.type = type; this.detail = init && init.detail; }
};
globalThis.window = { localStorage: null, addEventListener() {}, removeEventListener() {} };
globalThis.document = {
  documentElement: new FakeNode("html"),
  title: "",
  body: new FakeNode("body"),
  createElement: (tag) => new FakeNode(tag),
  querySelectorAll: () => [],
  addEventListener() {},
  removeEventListener() {},
  dispatchEvent() { return true; },
};

/** A working localStorage with an initial state. */
function storageOf(initial) {
  const data = new Map(Object.entries(initial || {}));
  return {
    getItem: (key) => (data.has(key) ? data.get(key) : null),
    setItem: (key, value) => { data.set(key, String(value)); },
    removeItem: (key) => { data.delete(key); },
    data,
  };
}

/** Private mode / storage disabled: every access throws. */
function hostileStorage() {
  const boom = () => { throw new Error("storage is disabled"); };
  return { getItem: boom, setItem: boom, removeItem: boom };
}

const themeMod = await import(moduleUrl("js/theme.js"));
const zh = (await import(moduleUrl("js/locales/zh-CN.js"))).STRINGS;

const bootSource = readFileSync(WEB + "/js/theme-boot.js", "utf8");
assert(!/^\s*(import|export)\b/m.test(bootSource), "theme-boot.js became a module, so it would be deferred");

/** Run the real boot script the way a browser does: as a classic script, in the global scope. */
function runBoot(storage) {
  globalThis.window.localStorage = storage;
  globalThis.document.documentElement = new FakeNode("html");
  new Function(bootSource)();
  return globalThis.document.documentElement.getAttribute("data-theme");
}

/** One switch over a fresh document, adopted from what the boot script would have painted. */
function world(storage) {
  const root = new FakeNode("html");
  root.setAttribute("data-theme", themeMod.readTheme(storage));
  const input = new FakeNode("input");
  input.type = "checkbox";
  const label = new FakeNode("label");
  label.className = "field-inline theme-switch";
  const span = new FakeNode("span");
  span.setAttribute("data-copy", "theme.dark");
  label.append(input, span);
  const sw = themeMod.createThemeSwitch({ input, label, storage, root });
  return { root, input, span, sw, storage };
}

/** The property this whole file exists for: one value, two surfaces, never disagreeing. */
function agree(state, where) {
  const attribute = state.root.getAttribute("data-theme");
  assert(attribute === "light" || attribute === "dark", "the attribute is " + attribute + " " + where);
  assert(
    state.input.checked === (attribute === "dark"),
    "checked and the attribute disagree " + where + ": checked=" + state.input.checked + " data-theme=" + attribute
  );
  assert(state.sw.value() === attribute, "the switch reports " + state.sw.value() + " while the document says " + attribute);
  return attribute;
}

// ---------------------------------------------------------------------------
// 1. The boot script and the module read the same key, over every stored state
// ---------------------------------------------------------------------------
const CASES = [
  ["a stored dark choice", storageOf({ "kdt.theme": "dark" }), "dark"],
  ["a stored light choice", storageOf({ "kdt.theme": "light" }), "light"],
  ["no stored choice at all", storageOf({}), "light"],
  ["a garbage value", storageOf({ "kdt.theme": "Dark" }), "light"],
  ["another garbage value", storageOf({ "kdt.theme": "{\"theme\":\"dark\"}" }), "light"],
  ["an empty string", storageOf({ "kdt.theme": "" }), "light"],
  ["a storage that throws on read", hostileStorage(), "light"],
];
for (const [label, storage, expected] of CASES) {
  const attribute = runBoot(storage);
  assert(
    attribute === expected,
    "the boot script resolved " + label + " to " + JSON.stringify(attribute) + ", expected " + JSON.stringify(expected)
  );
  assert(
    themeMod.readTheme(storage) === attribute,
    "the module and the boot script disagree about " + label + ": " + themeMod.readTheme(storage) + " vs " + attribute
  );
}
assert(themeMod.THEME_KEY === "kdt.theme", "the module's storage key changed: " + themeMod.THEME_KEY);
assert(themeMod.applyTheme("banana", new FakeNode("div")) === "light", "applyTheme does not fall back to light");
assert(themeMod.applyTheme("dark", new FakeNode("div")) === "dark", "applyTheme lost dark");

// ---------------------------------------------------------------------------
// 2. A stored choice is adopted by the switch, box and attribute together
// ---------------------------------------------------------------------------
const adopted = world(storageOf({ "kdt.theme": "dark" }));
assert(agree(adopted, "after construction from a stored dark choice") === "dark", "the switch did not adopt dark");
assert(adopted.input.checked === true, "a stored dark choice left the checkbox unchecked");

const fresh = world(storageOf({}));
assert(agree(fresh, "after construction with nothing stored") === "light", "the default is not light");
assert(fresh.input.checked === false, "the light default checked the checkbox");

// ---------------------------------------------------------------------------
// 3. The toggle: a real change event, the key written, both surfaces moved
// ---------------------------------------------------------------------------
fresh.input.checked = true;
fresh.input.fire("change");
assert(agree(fresh, "after toggling to dark") === "dark", "the toggle did not apply dark");
assert(fresh.storage.getItem("kdt.theme") === "dark", "the toggle did not write the key");
fresh.input.checked = false;
fresh.input.fire("change");
assert(agree(fresh, "after toggling back to light") === "light", "the toggle did not apply light");
assert(fresh.storage.getItem("kdt.theme") === "light", "toggling back did not write the key");

// ---------------------------------------------------------------------------
// 4. A hostile storage: the session is still themed, nothing throws
// ---------------------------------------------------------------------------
const hostile = world(hostileStorage());
assert(agree(hostile, "with a storage that throws") === "light", "a hostile storage did not fall back to light");
hostile.input.checked = true;
hostile.input.fire("change");
assert(agree(hostile, "after toggling with a hostile storage") === "dark", "a hostile storage stopped the theme");
hostile.input.checked = false;
hostile.input.fire("change");
assert(agree(hostile, "after toggling back with a hostile storage") === "light", "a hostile storage broke the toggle");

// ---------------------------------------------------------------------------
// 5. No boot script: the module still themes the session
// ---------------------------------------------------------------------------
const lateRoot = new FakeNode("html");
const lateInput = new FakeNode("input");
themeMod.createThemeSwitch({ input: lateInput, storage: storageOf({ "kdt.theme": "dark" }), root: lateRoot });
assert(lateRoot.getAttribute("data-theme") === "dark", "with no boot script the module left the document unthemed");
assert(lateInput.checked === true, "with no boot script the checkbox did not follow the stored choice");

// ---------------------------------------------------------------------------
// 6. relabel(): the label's copy is repainted from the packs
// ---------------------------------------------------------------------------
const relabelWorld = world(storageOf({}));
relabelWorld.span.textContent = "stale";
relabelWorld.sw.relabel();
assert(
  relabelWorld.span.textContent === zh["theme.dark"],
  "relabel() did not repaint the label, got " + JSON.stringify(relabelWorld.span.textContent)
);

console.log("theme harness OK");
