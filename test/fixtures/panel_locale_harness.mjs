/**
 * Headless harness for the construction-locale defect (web/js/batch.js and
 * web/js/scale.js).
 *
 * The static contract test can see that initLocale() exists in app.js; only
 * running the panels can show that the copy they paint AT CONSTRUCTION comes
 * from the resolved locale, and that a later language switch repaints it.
 *
 * The defect this pins: every panel factory runs at module scope, before
 * boot() ever resolved the locale, so their construction-time strings froze in
 * the default zh-CN. Driving the real UI at ?lang=en showed 103 unique CJK runs
 * in the right-hand panel, and batch/export/scale stayed Chinese even after a
 * zh-CN -> en switch because nothing repainted their tab buttons.
 *
 * Usage: node panel_locale_harness.mjs <absolute path to web/>
 */
import { pathToFileURL } from "node:url";

const WEB = process.argv[2];
if (!WEB) throw new Error("usage: node panel_locale_harness.mjs <web dir>");
const moduleUrl = (rel) => pathToFileURL(WEB + "/" + rel).href;

function assert(condition, message) {
  if (!condition) throw new Error("panel locale harness: " + message);
}

// ---- minimal fake DOM ----------------------------------------------------
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
    this.title = "";
    this.placeholder = "";
    this.parentNode = null;
  }
  append(...nodes) {
    for (const node of nodes) {
      if (node && typeof node === "object") node.parentNode = this;
      this.children.push(node);
    }
  }
  /** HTMLSelectElement.options: what the shared scope control iterates (scope.js paint()). */
  get options() {
    return this.children.filter((node) => node && node.tagName === "OPTION");
  }
  replaceChildren(...nodes) { this.children = []; this.append(...nodes); }
  // tags.js (reused inside the zoom overlay) inserts a derived counter node.
  insertAdjacentElement(position, node) {
    const parent = this.parentNode;
    if (!parent) return node;
    if (node && typeof node === "object") node.parentNode = parent;
    const index = parent.children.indexOf(this);
    if (position === "afterend") parent.children.splice(index + 1, 0, node);
    else if (position === "beforebegin") parent.children.splice(index, 0, node);
    else parent.children.push(node);
    return node;
  }
  setAttribute(name, value) { this.attributes[name] = value; }
  getAttribute(name) { return this.attributes[name]; }
  addEventListener(type, handler) { (this.listeners[type] ||= []).push(handler); }
  removeEventListener(type, handler) {
    this.listeners[type] = (this.listeners[type] || []).filter((item) => item !== handler);
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

const storage = {
  map: new Map(),
  getItem(key) { return this.map.has(key) ? this.map.get(key) : null; },
  setItem(key, value) { this.map.set(key, String(value)); },
};

globalThis.window = {
  location: { search: "?lang=en", hash: "" },
  localStorage: storage,
  setTimeout,
  clearTimeout,
  addEventListener() {},
  removeEventListener() {},
};
// Node exposes a getter-only globalThis.navigator, so it needs defineProperty.
Object.defineProperty(globalThis, "navigator", {
  value: { languages: ["zh-CN", "zh"], language: "zh-CN" },
  configurable: true,
  writable: true,
});
globalThis.document = {
  documentElement: { lang: "" },
  title: "",
  body: new FakeNode("body"),
  activeElement: null,
  createElement: (tag) => new FakeNode(tag),
  createDocumentFragment: () => new FakeNode("fragment"),
  createTextNode: (text) => ({ nodeType: 3, textContent: text }),
  querySelectorAll: () => [],
  addEventListener() {},
  removeEventListener() {},
  dispatchEvent() { return true; },
};

let fetchCalls = 0;
globalThis.fetch = async (url) => {
  fetchCalls += 1;
  return { ok: true, status: 200, url: String(url), json: async () => ({}) };
};

// ---- resolve the locale BEFORE any panel is constructed -------------------
const strings = await import(moduleUrl("js/strings.js"));
const en = (await import(moduleUrl("js/locales/en.js"))).STRINGS;
const zh = (await import(moduleUrl("js/locales/zh-CN.js"))).STRINGS;

assert(strings.initLocale() === "en", "?lang=en must resolve to en, got " + strings.initLocale());
assert(strings.getLocale() === "en", "the resolved locale must be en");

const batch = await import(moduleUrl("js/batch.js"));
const scale = await import(moduleUrl("js/scale.js"));
const scopeMod = await import(moduleUrl("js/scope.js"));
const { createTrainParams } = await import(moduleUrl("js/trainparams.js"));

// ---- the column's one scope control, built the way app.js builds it -------
// Both panels are handed this instance; its <select> is the strip's, so its option labels are
// construction-time copy like the tab labels and have to come from the resolved pack.
const scopeHost = new FakeNode("div");
const scopeSelect = new FakeNode("select");
scopeHost.append(scopeSelect);
const sharedScope = scopeMod.createScopeControl({ select: scopeSelect });

// ---- batch panel: construction copy --------------------------------------
const batchTabs = new FakeNode("nav");
const batchHost = new FakeNode("aside");
const batchPanel = batch.createBatchPanel({
  tabsHost: batchTabs,
  panelHost: batchHost,
  notify() {},
  confirm: () => true,
  scope: sharedScope,
  getTags: () => null,
  onAfterWrite() {},
});
assert(typeof batchPanel.relabel === "function", "the batch panel must expose relabel()");

const batchTab = batchTabs.children[0];
assert(batchTab, "the batch panel did not append its tab");
assert(
  batchTab.textContent === en["tab.batch"],
  "the batch tab label must be English at construction, got " + JSON.stringify(batchTab.textContent),
);
assert(batchTab.textContent !== zh["tab.batch"], "the batch tab label froze in Chinese at construction");

const batchLegend = findOne(batchHost, (node) => node.tagName === "LEGEND");
assert(
  batchLegend && batchLegend.textContent === en["batch.commonTitle"],
  "the batch section legend must be English at construction, got " + (batchLegend && batchLegend.textContent),
);
const batchPlaceholder = findOne(batchHost, (node) => node.placeholder === en["batch.commonAddPlaceholder"]);
assert(batchPlaceholder, "the batch add-tag placeholder must be English at construction");
assert(
  findOne(batchHost, (node) => node.textContent === en["batch.hint"]),
  "the batch hint must be English at construction",
);

// relabel must be safe before any data has loaded and must not hit the network.
batchPanel.relabel();
assert(fetchCalls === 0, "relabel() must not issue a request");

// ---- scale panel: construction copy --------------------------------------
const scaleTabs = new FakeNode("nav");
const scaleHost = new FakeNode("aside");
const params = createTrainParams();
const scalePanel = scale.createScalePanel({
  tabsHost: scaleTabs,
  panelHost: scaleHost,
  notify() {},
  getRoot: () => null,
  getParams: () => params,
  pickTarget: async () => null,
});
assert(typeof scalePanel.relabel === "function", "the scale panel must expose relabel()");

const scaleTab = scaleTabs.children[0];
assert(scaleTab, "the scale panel did not append its tab");
assert(
  scaleTab.textContent === en["tab.scale"],
  "the scale tab label must be English at construction, got " + JSON.stringify(scaleTab.textContent),
);
const scaleLegend = findOne(scaleHost, (node) => node.tagName === "LEGEND");
assert(
  scaleLegend && scaleLegend.textContent === en["scale.params"],
  "the scale section legend must be English at construction, got " + (scaleLegend && scaleLegend.textContent),
);
scalePanel.relabel();
assert(fetchCalls === 0, "the scale relabel() must not issue a request");

// ---- the module-scope overlays (fix 1 covers them: they paint t() at
// construction, so resolving the locale first is what makes them English).
// They are modal, so they get no runtime relabel - only this first-paint check.
const pickerMod = await import(moduleUrl("js/picker.js"));
pickerMod.createPicker({ notify() {} });
const pickerHeadings = findAll(
  document.body,
  (node) => node.tagName === "H2" && node.textContent === en["picker.title"],
);
assert(
  pickerHeadings.length === 1,
  "the picker title must be English at construction (found " + pickerHeadings.length + ")",
);

const zoomMod = await import(moduleUrl("js/zoom.js"));
zoomMod.createZoom({ notify() {} });
const zoomHeadings = findAll(
  document.body,
  (node) => node.tagName === "H2" && node.textContent === en["zoom.title"],
);
assert(
  zoomHeadings.length === 1,
  "the zoom title must be English at construction (found " + zoomHeadings.length + ")",
);

// ---- a switch must repaint construction-time copy via relabel() ----------
assert(strings.setLocale("zh-CN", { notify: false }) === true, "setLocale(zh-CN) must succeed");
assert(
  batchTab.textContent === en["tab.batch"],
  "sanity: without relabel() the tab label is still in the old language",
);
batchPanel.relabel();
scalePanel.relabel();
assert(
  batchTab.textContent === zh["tab.batch"],
  "batch relabel() did not repaint the tab label, got " + batchTab.textContent,
);
assert(
  batchLegend.textContent === zh["batch.commonTitle"],
  "batch relabel() did not repaint the section legend, got " + batchLegend.textContent,
);
assert(
  batchPlaceholder.placeholder === zh["batch.commonAddPlaceholder"],
  "batch relabel() did not repaint the placeholder, got " + batchPlaceholder.placeholder,
);
assert(
  findOne(batchHost, (node) => node.tagName === "OPTION" && node.textContent === zh["batch.opCommon"]),
  "batch relabel() did not repaint the op option labels",
);
assert(
  scaleTab.textContent === zh["tab.scale"],
  "scale relabel() did not repaint the tab label, got " + scaleTab.textContent,
);
assert(
  scaleLegend.textContent === zh["scale.params"],
  "scale relabel() did not repaint the section legend, got " + scaleLegend.textContent,
);
assert(fetchCalls === 0, "a language switch must not issue requests");

// ---- and back again -------------------------------------------------------
strings.setLocale("en", { notify: false });
batchPanel.relabel();
scalePanel.relabel();
assert(batchTab.textContent === en["tab.batch"], "switch back to en left the batch tab stale");
assert(scaleTab.textContent === en["tab.scale"], "switch back to en left the scale tab stale");

// ---- the tagger panel: it must be *constructible* --------------------------
// A missing import is a ReferenceError the moment the factory runs, and this harness is the only place
// that runs one: on 2026-09-19 autotag.js called createScopeControl without importing it, the whole
// suite stayed green, and the app could not start. Constructing the panel here is what closes that hole.
const autotag = await import(moduleUrl("js/autotag.js"));
const ELEMENT_NAMES = [
  "modelSelect", "modelNote", "thresholds", "options", "undesired", "alwaysFirst",
  "writeModeSelect", "previewButton", "runButton", "cancelButton", "progress", "progressFill",
  "progressText", "errors", "diffTitle", "diffSummary", "diff",
];
const elementOptions = {};
for (const name of ELEMENT_NAMES) {
  const tag = /Select$/.test(name) ? "select" : "div";
  elementOptions[name] = new FakeNode(tag);
}
elementOptions.notify = () => {};
elementOptions.confirm = () => true;
// The same control the batch panel reads: one value for the column.
elementOptions.scope = sharedScope;
const autotagPanel = autotag.createAutotagPanel(elementOptions);
assert(typeof autotagPanel.resume === "function", "the tagger panel must expose resume() for a reload");
assert(typeof autotagPanel.refreshScopes === "function", "the tagger panel must expose refreshScopes()");

const taggerScopes = sharedScope.select.children;
assert(taggerScopes.length === 4, "the shared scope control must offer four scopes, got " + taggerScopes.length);
// The label carries a count, so the expected text is the pack's template with its placeholder filled.
const fillParams = (text, params) =>
  String(text).replace(/\{(\w+)\}/g, (whole, key) => (key in params ? String(params[key]) : whole));
assert(
  taggerScopes[3].textContent === fillParams(en["scope.filtered"], { n: 0 }),
  "the tagger's scope labels must come from the active pack (en here), got " + JSON.stringify(taggerScopes[3].textContent),
);
assert(
  taggerScopes[3].textContent !== zh["scope.filtered"],
  "the tagger's scope label froze in Chinese at construction",
);
assert(
  taggerScopes[3].value === "filtered",
  "the scope options must carry the shared values, got " + JSON.stringify(taggerScopes[3].value),
);

// ---- the strip is repainted by a language switch ---------------------------
// Its option labels carry the live counts, so they are painted at render time; app.js repaints
// the control itself in relabelAfterLocaleChange() (scope.paint()), which is a pure redraw.
strings.setLocale("zh-CN", { notify: false });
sharedScope.paint();
assert(
  scopeSelect.options[3].textContent === fillParams(zh["scope.filtered"], { n: 0 }),
  "the shared scope control was not repainted on the language switch, got " +
    JSON.stringify(scopeSelect.options[3].textContent),
);
strings.setLocale("en", { notify: false });
sharedScope.paint();
assert(
  scopeSelect.options[3].textContent === fillParams(en["scope.filtered"], { n: 0 }),
  "switching back left the shared scope control stale, got " +
    JSON.stringify(scopeSelect.options[3].textContent),
);
assert(fetchCalls === 0, "repainting the scope control must not issue a request");

console.log("panel locale harness OK");
