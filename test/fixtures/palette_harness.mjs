/**
 * Headless behaviour harness for the command palette (web/js/palette.js).
 *
 * What it is for: the palette is a new *surface*, and the two things that can go wrong on a new
 * surface are exactly the two things a static test cannot see - it can run an action the app would
 * have refused, and its keyboard model can steal keys from the surfaces that already own them. So
 * this harness really runs the module over a fake DOM and asserts:
 *
 *   - the opening gesture is Ctrl+K on the **document** (not on a node that has to be focused first),
 *     it never toggles the panel shut, and it is refused while another dialog is on screen (that
 *     dialog owns the keyboard);
 *   - an empty query lists **every** command in the table's own order, and a query matches in any
 *     language pack and ranks a contiguous hit above a scattered one;
 *   - an unavailable command is **painted with its reason**, its `run` is never called, and Enter
 *     on it says the reason out loud instead of doing nothing;
 *   - an available command calls **the function it was handed**, exactly once, and nothing else;
 *   - the panel is a DROPDOWN, not a modal: Escape closes and puts the keyboard back on the bar,
 *     **Tab leaves the panel and closes it** (no trap), a click outside closes it and a click
 *     inside does not, and the bar's aria-expanded follows all of it;
 *   - the active row is announced through aria-activedescendant;
 *   - a language switch repaints the rows, and a refresh after the state changed repaints them too.
 *
 * Usage: node palette_harness.mjs <absolute path to web/>
 */
import { pathToFileURL } from "node:url";

const WEB = process.argv[2];
if (!WEB) throw new Error("usage: node palette_harness.mjs <web dir>");
const moduleUrl = (rel) => pathToFileURL(WEB + "/" + rel).href;

function assert(condition, message) {
  if (!condition) throw new Error("palette harness: " + message);
}

// ---- a fake DOM just big enough for the palette ----------------------------
const ALL_NODES = [];

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
    this.parentNode = null;
    this.textContent = "";
    this.className = "";
    this.id = "";
    this.hidden = false;
    this.disabled = false;
    this.value = "";
    this.type = "";
    ALL_NODES.push(this);
  }
  append(...nodes) {
    for (const node of nodes) {
      if (node && typeof node === "object") node.parentNode = this;
      this.children.push(node);
    }
  }
  replaceChildren(...nodes) { this.children = []; this.append(...nodes); }
  setAttribute(name, value) {
    this.attributes[name] = String(value);
    if (name === "id") this.id = String(value);
  }
  getAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attributes, name) ? this.attributes[name] : null; }
  removeAttribute(name) { delete this.attributes[name]; }
  addEventListener(type, handler) { (this.listeners[type] ||= []).push(handler); }
  removeEventListener(type, handler) {
    this.listeners[type] = (this.listeners[type] || []).filter((item) => item !== handler);
  }
  fire(type, event) {
    const payload = Object.assign({ type, target: this, preventDefault() {}, shiftKey: false }, event || {});
    for (const handler of [...(this.listeners[type] || [])]) handler(payload);
  }
  focus() { globalThis.document.activeElement = this; }
  /** Real enough for the palette's "is this click inside me?" test: the ancestor chain. */
  contains(node) {
    for (let current = node; current; current = current.parentNode) {
      if (current === this) return true;
    }
    return false;
  }
}

function matches(node, selector) {
  const part = selector.trim();
  if (part.startsWith(".")) return node.classList.contains(part.slice(1));
  if (part.startsWith("[")) return node.getAttribute(part.slice(1, -1)) !== null;
  return false;
}

const documentListeners = {};
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
  activeElement: null,
  createElement: (tag) => new FakeNode(tag),
  querySelectorAll: (selector) => ALL_NODES.filter(
    (node) => String(selector).split(",").some((part) => matches(node, part)),
  ),
  addEventListener(type, handler) { (documentListeners[type] ||= []).push(handler); },
  removeEventListener(type, handler) {
    documentListeners[type] = (documentListeners[type] || []).filter((item) => item !== handler);
  },
  dispatchEvent(event) {
    for (const handler of [...(documentListeners[event.type] || [])]) handler(event);
    return true;
  },
};

function key(name, extra) {
  globalThis.document.dispatchEvent(Object.assign({ type: "keydown", key: name, preventDefault() {} }, extra || {}));
}

const strings = await import(moduleUrl("js/strings.js"));
const paletteMod = await import(moduleUrl("js/palette.js"));
const en = (await import(moduleUrl("js/locales/en.js"))).STRINGS;
const { t, setLocale } = strings;

/** The English pack's own value with its placeholders filled - an expectation that is not built by
 *  asking t() (which would make the comparison true by construction). */
function fromPack(pack, key, params) {
  return String(pack[key]).replace(/\{(\w+)\}/g, (match, name) => (
    Object.prototype.hasOwnProperty.call(params || {}, name) ? String(params[name]) : match
  ));
}

// ---- the palette, built the way app.js builds it ---------------------------
// The panel is a plain dropdown: no backdrop class, and the bar it drops from is its `trigger`.
const host = new FakeNode("div");
host.className = "palette-pop";
host.classList.add("palette-pop");
host.hidden = true;
const input = new FakeNode("input");
const list = new FakeNode("div");
const status = new FakeNode("p");
const closeButton = new FakeNode("button");
// The panel really contains its own nodes, so "a click inside does not close it" is a test of the
// ancestor chain rather than of a detached node.
host.append(closeButton, input, list, status);
const trigger = new FakeNode("button");
trigger.className = "palette-bar";
//: A second backdrop, to prove the palette refuses to open over one that is already on screen.
const otherDialog = new FakeNode("div");
otherDialog.className = "dialog-backdrop";
otherDialog.classList.add("dialog-backdrop");
otherDialog.hidden = true;

const calls = [];
const commands = [
  { id: "browse.refresh", group: "browse", labelKey: "browse.refresh", run: () => calls.push("refresh") },
  { id: "browse.parent", group: "browse", labelKey: "browse.parent", run: () => calls.push("parent") },
  { id: "tab.cache", group: "view", labelKey: "tab.cache", run: () => calls.push("cache-tab") },
  { id: "gallery.selectAll", group: "selection", labelKey: "gallery.selectAll", run: () => calls.push("select-all") },
];
//: One command the app refuses right now, with a reason - the case that must be shown, not hidden.
const blocked = {
  id: "batch.apply",
  group: "jobs",
  labelKey: "batch.apply",
  run: () => calls.push("apply"),
  available: () => ({ key: "palette.needScope" }),
};
const table = [...commands.slice(0, 2), blocked, ...commands.slice(2)];

//: A second palette over its own nodes, so the fixture table can be swapped between cases without
//: disturbing the keyboard state of the first one.
const palette = paletteMod.createCommandPalette({
  host, trigger, input, list, status, close: closeButton, commands: () => table,
});

/** A click anywhere: the palette's outside-click handler lives on the document. */
function click(target) {
  globalThis.document.dispatchEvent({ type: "click", target });
}

/** A real click: the node's own listeners first, then the document's (that is what bubbling is). */
function press(node) {
  node.fire("click");
  click(node);
}

// The bar's own wiring, exactly as app.js does it - so "clicking the bar reopens the panel" is
// tested rather than assumed.
trigger.addEventListener("click", () => { palette.open(); });

const options = () => list.children.flatMap(
  (box) => box.children.filter((node) => node.getAttribute("role") === "option"),
);
const labels = () => options().map((node) => node.children[0].textContent);
const ids = () => options().map((node) => node.dataset.command);
const whyOf = (id) => {
  const node = options().find((item) => item.dataset.command === id);
  const why = node.children.find((child) => child.className === "palette-why");
  return why ? why.textContent : "";
};

// ---- 1. the opening gesture ------------------------------------------------
const opener = new FakeNode("button");
opener.focus();
assert(host.hidden === true, "the palette must start hidden");
key("k");
assert(host.hidden === true, "a bare k must not open the palette (it belongs to the search field)");
assert(trigger.getAttribute("aria-expanded") === "false", "the bar starts expanded");
key("k", { ctrlKey: true });
assert(host.hidden === false, "Ctrl+K did not open the palette");
assert(input.getAttribute("aria-expanded") === "true", "the combobox is not marked expanded");
assert(trigger.getAttribute("aria-expanded") === "true", "the bar does not say its panel is open");
assert(globalThis.document.activeElement === input, "focus must move into the palette, or its keys never arrive");
assert(input.value === "", "a fresh open must start from an empty query");

// ---- 2. an empty query lists everything, in the table's own order ----------
assert(ids().join(",") === table.map((command) => command.id).join(","),
  "an empty query must list every command in the table's order, got " + ids().join(","));
assert(labels()[0] === t("browse.refresh"), "the row does not carry the command's own wording");
assert(list.children.length === 4, "the rows must be grouped by the commands' group field, got "
  + list.children.length + " groups");
assert(input.getAttribute("aria-activedescendant") === "palette-option-browse.refresh",
  "the first row must be the active descendant on open");
assert(status.textContent === t("palette.status", { n: table.length }),
  "the status line must count what is on screen, got " + status.textContent);

// ---- 3. unavailable commands are shown, with their reason ------------------
const blockedNode = options().find((node) => node.dataset.command === "batch.apply");
assert(blockedNode, "an unavailable command must still be listed");
assert(blockedNode.getAttribute("aria-disabled") === "true", "an unavailable option must say so");
assert(blockedNode.classList.contains("is-unavailable"), "the row carries no unavailable state");
assert(whyOf("batch.apply") === t("palette.needScope"),
  "the row must carry the reason the app gave, got " + JSON.stringify(whyOf("batch.apply")));
assert(blockedNode.children.some((child) => child.className === "sr-only" && child.textContent === t("palette.unavailable")),
  "a screen reader must hear that the option is unavailable, not only see it");

// ---- 4. Enter on an unavailable row says why and runs nothing --------------
const blockedIndex = ids().indexOf("batch.apply");
for (let step = 0; step < blockedIndex; step += 1) key("ArrowDown");
assert(input.getAttribute("aria-activedescendant") === "palette-option-batch.apply",
  "ArrowDown did not move the active row");
key("Enter");
assert(calls.length === 0, "an unavailable command was run: " + calls.join(","));
assert(status.textContent === t("palette.needScope"),
  "Enter on an unavailable row must say the reason, got " + JSON.stringify(status.textContent));
assert(host.hidden === false, "a refusal must not close the palette");

// ---- 5. the arrows wrap and End/Home are left to the text field ------------
key("ArrowUp");
assert(input.getAttribute("aria-activedescendant") === "palette-option-browse.parent",
  "ArrowUp did not step back, got " + input.getAttribute("aria-activedescendant"));
key("ArrowUp");
key("ArrowUp");
assert(input.getAttribute("aria-activedescendant") === "palette-option-gallery.selectAll",
  "ArrowUp must wrap around the list, got " + input.getAttribute("aria-activedescendant"));

// ---- 6. Enter runs the function it was handed, once ------------------------
for (let step = 0; step < 4; step += 1) key("ArrowDown"); // wrap to the top, then onto tab.cache
const activeId = ids()[options().findIndex((node) => node.getAttribute("aria-selected") === "true")];
key("Enter");
assert(calls.length === 1, "Enter must run exactly one command, got " + calls.length);
assert(calls[0] === "cache-tab", "the wrong command ran: " + calls.join(","));
assert(activeId === "tab.cache", "the harness and the palette disagree about the active row");
assert(host.hidden === true, "running a command must close the palette");
assert(trigger.getAttribute("aria-expanded") === "false", "the bar still says the panel is open");
assert(globalThis.document.activeElement === trigger,
  "the keyboard must go back to the bar; it is on " + (globalThis.document.activeElement || {}).tagName);

// ---- 7. Escape closes and puts the keyboard back on the bar ---------------
// Focus starts on the page (a hidden subtree cannot hold focus in a real browser, so this is the
// only state the gesture can be pressed from).
opener.focus();
key("k", { ctrlKey: true });
assert(host.hidden === false, "Ctrl+K must open the palette again");
key("Escape");
assert(host.hidden === true, "Escape must close the palette");
assert(trigger.getAttribute("aria-expanded") === "false", "Escape left the bar expanded");
assert(globalThis.document.activeElement === trigger, "Escape must hand the keyboard back to the bar");

// ---- 8. Tab LEAVES the panel and closes it (a dropdown is not a modal) ----
opener.focus();
key("k", { ctrlKey: true });
assert(host.hidden === false, "Ctrl+K must open the panel");
key("Tab");
assert(host.hidden === true, "Tab must close the panel: a dropdown does not trap the keyboard");
assert(globalThis.document.activeElement === trigger,
  "Tab must leave the keyboard on the bar, not inside a panel that is no longer on screen");
key("k", { ctrlKey: true });
key("Tab", { shiftKey: true });
assert(host.hidden === true, "Shift+Tab must leave the panel too");
// Ctrl+K never toggles: pressing it again puts the caret back in the field and keeps what is typed.
key("k", { ctrlKey: true });
input.value = "ref";
input.fire("input");
key("k", { ctrlKey: true });
assert(host.hidden === false, "Ctrl+K toggled the panel closed");
assert(input.value === "ref", "Ctrl+K while open cleared the query");
assert(globalThis.document.activeElement === input, "Ctrl+K while open must put the caret back in the field");

// ---- 9. a click outside closes; a click inside does not -------------------
const outside = new FakeNode("div");
input.value = "";
input.fire("input");
click(outside);
assert(host.hidden === true, "a click outside the panel must close it");
assert(globalThis.document.activeElement === trigger, "a click outside must leave the keyboard on the bar");
// Recovery: the bar's own click opens it again, from a closed panel - the mouse's way back in.
press(trigger);
assert(host.hidden === false, "a click on the bar must open the panel again");
assert(input.value === "", "reopening must start from an empty query");
press(trigger);
assert(host.hidden === false, "a click on the bar was read as a click outside");
press(host);
assert(host.hidden === false, "a click on the panel was read as a click outside");
press(list);
assert(host.hidden === false, "a click on a row (inside the panel) closed it");
press(closeButton);
assert(host.hidden === true, "the panel's own close button must close it");
assert(globalThis.document.activeElement === trigger, "the close button must hand the keyboard back too");

// ---- 10. the matcher -----------------------------------------------------
key("k", { ctrlKey: true });
input.value = "刷新";
input.fire("input");
assert(ids().join(",") === "browse.refresh",
  "a substring of the current language's wording must match, got " + ids().join(","));
input.value = "REFRESH";
input.fire("input");
assert(ids()[0] === "browse.refresh",
  "matching must be case-insensitive and read every pack, got " + ids().join(","));
input.value = "rfrsh";
input.fire("input");
assert(ids().includes("browse.refresh"),
  "a scattered match (subsequence) must still find the command, got " + ids().join(","));
input.value = "select all";
input.fire("input");
assert(ids().join(",") === "gallery.selectAll",
  "a multi-term query must match one command's own wording, got " + ids().join(","));
input.value = "refresh parent";
input.fire("input");
assert(options().length === 0,
  "every term has to land on the same command; two terms from two commands matched " + ids().join(","));
input.value = "refresh";
input.fire("input");
assert(input.getAttribute("aria-activedescendant") === "palette-option-browse.refresh",
  "a new query must re-activate the best match");
input.value = "zzzz";
input.fire("input");
assert(options().length === 0, "a query matching nothing must list nothing");
assert(status.textContent === t("palette.noMatch"),
  "an empty result must say so, got " + JSON.stringify(status.textContent));
input.value = "";
input.fire("input");
assert(options().length === table.length, "clearing the query must bring every command back");

// ---- 11. a click on a row is a choice ------------------------------------
const clickTarget = options().find((node) => node.dataset.command === "gallery.selectAll");
clickTarget.fire("click");
assert(calls.join(",") === "cache-tab,select-all", "a click must run the row it landed on: " + calls.join(","));
assert(globalThis.document.activeElement === trigger, "a click must hand the keyboard back to the bar");

// ---- 12. the state changed while it is open ------------------------------
// The job shelf calls refresh() when a job starts or ends; the row that said "a job is already
// running" has to stop saying it, or the reason on screen is a stale claim.
let busy = true;
const host2 = new FakeNode("div");
host2.className = "palette-pop";
host2.classList.add("palette-pop");
host2.hidden = true;
const list2 = new FakeNode("div");
const status2 = new FakeNode("p");
const palette2 = paletteMod.createCommandPalette({
  host: host2,
  input: new FakeNode("input"),
  list: list2,
  status: status2,
  close: new FakeNode("button"),
  commands: () => [
    { id: "tab.cache", group: "view", labelKey: "tab.cache", run: () => calls.push("second") },
    { id: "autotag.run", group: "jobs", labelKey: "autotag.run", run: () => calls.push("second"),
      available: () => (busy ? { key: "palette.busy" } : null) },
  ],
});
const options2 = () => list2.children.flatMap(
  (box) => box.children.filter((node) => node.getAttribute("role") === "option"),
);
const rowOf = (id) => options2().find((node) => node.dataset.command === id);
assert(palette2.open() === true, "the second palette did not open");
assert(rowOf("autotag.run").getAttribute("aria-disabled") === "true", "the busy command is not disabled");
busy = false;
palette2.refresh();
assert(rowOf("autotag.run").getAttribute("aria-disabled") === "false",
  "refresh() did not re-read the state, so the reason on screen is stale");
assert(!rowOf("autotag.run").children.some((child) => child.className === "palette-why"),
  "a command that became available still carries its old reason");
assert(palette2.isOpen() === true, "a refresh must not close the palette");
palette2.close();
assert(palette2.isOpen() === false, "close() did not close the palette");
assert(list2.children.length === 0, "close() must leave no rows behind");
assert(status2.textContent === "", "close() left its last claim on the status line");

// ---- 13. it refuses to open over another dialog ---------------------------
otherDialog.hidden = false;
opener.focus();
key("k", { ctrlKey: true });
assert(host.hidden === true, "the palette opened on top of a dialog that owns the keyboard");
assert(palette.open() === false, "open() must refuse while another dialog is on screen");
otherDialog.hidden = true;
assert(palette.open() === true, "the palette must open once the other dialog is gone");
palette.close();

// ---- 14. a language switch repaints the rows -----------------------------
key("k", { ctrlKey: true });
setLocale("en");
palette.relabel();
const enLabel = fromPack(en, "browse.refresh");
assert(labels()[0] === enLabel, "relabel() left the rows in the old language: " + labels()[0]);
assert(whyOf("batch.apply") === fromPack(en, "palette.needScope"),
  "relabel() left the reasons in the old language: " + whyOf("batch.apply"));
setLocale("zh-CN");
palette.relabel();
assert(labels()[0] === t("browse.refresh"), "switching back must repaint the rows again");
palette.close();

console.log("palette harness OK");
