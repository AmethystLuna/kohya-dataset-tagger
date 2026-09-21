/**
 * Headless behaviour harness for web/js/filter.js.
 *
 * Static tests can see that a search box exists, never that the tri-state
 * groups combine the way the panel claims, that the row cap happens AFTER the
 * search (or a rare tag would be unreachable), or that "selection only"
 * intersects instead of replacing the tag filter. Those are the parts users
 * act on, so this drives the real module against a minimal fake DOM.
 *
 * Usage: node filter_harness.mjs <absolute path to web/>
 */
import { pathToFileURL } from "node:url";

const WEB = process.argv[2];
if (!WEB) throw new Error("usage: node filter_harness.mjs <web dir>");

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
    this.textContent = "";
    this.className = "";
    this.hidden = false;
    this.value = "";
    this.checked = false;
    this.parentNode = null;
  }
  append(...nodes) {
    for (const node of nodes) {
      if (node && typeof node === "object") node.parentNode = this;
      this.children.push(node);
    }
  }
  replaceChildren(...nodes) { this.children = []; this.append(...nodes); }
  /**
   * filter.js walks up from the click target: event.target.closest(".chip-remove"),
   * then remove.closest(".chip"). Only single class selectors are asked for, so that
   * is all this supports.
   */
  closest(selector) {
    const name = String(selector).replace(/^\./, "");
    let node = this;
    while (node) {
      if (hasClass(node, name)) return node;
      node = node.parentNode;
    }
    return null;
  }
  /** HTMLSelectElement.options: what paintSortOptions() iterates. */
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
   * Dispatch, bubbling up the parent chain like the real DOM. The chips are painted by
   * a click DELEGATE on the container, so a click on a chip's remove button only
   * reaches filter.js if the event bubbles; event.target stays the button, which is
   * what the delegate reads (closest(".chip")).
   */
  fire(type) {
    const event = { preventDefault() {}, target: this };
    for (let node = this; node; node = node.parentNode) {
      for (const handler of [...(node.listeners[type] || [])]) handler(event);
    }
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
const byClass = (root, name) => find(root, (node) => hasClass(node, name));

function assert(condition, message) {
  if (!condition) throw new Error("filter harness: " + message);
}

globalThis.document = {
  body: new FakeNode("body"),
  createElement: (tag) => new FakeNode(tag),
  createDocumentFragment: () => new FakeNode("fragment"),
  createTextNode: (text) => ({ nodeType: 3, textContent: text }),
  // applyCopy() walks [data-copy*]; the packs are never applied here, but a locale
  // switch (setLocale) calls it, and the strip's labels must survive one.
  querySelectorAll: () => [],
  dispatchEvent() { return true; },
  addEventListener() {},
  removeEventListener() {},
};
globalThis.window = { setTimeout, clearTimeout, addEventListener() {}, removeEventListener() {} };

const mod = await import(pathToFileURL(WEB + "/js/filter.js").href);
const strings = await import(pathToFileURL(WEB + "/js/strings.js").href);
const { t } = strings;

// ---- pure helpers --------------------------------------------------------
const { FILTER_STATE, FILTER_SORT, matchesFilter, countTags, selectTagRows, cycleState } = mod;
assert(typeof matchesFilter === "function", "matchesFilter is not exported");
assert(typeof countTags === "function", "countTags is not exported");
assert(typeof selectTagRows === "function", "selectTagRows is not exported");
assert(typeof cycleState === "function", "cycleState is not exported");

// only = OR, include = AND, exclude wins over both
assert(matchesFilter({ only: ["a"], include: [], exclude: [] }, ["a", "z"]) === true, "only must match");
assert(matchesFilter({ only: ["a", "b"], include: [], exclude: [] }, ["b"]) === true, "only group must be OR");
assert(matchesFilter({ only: ["a", "b"], include: [], exclude: [] }, ["c"]) === false, "only group must reject");
assert(matchesFilter({ only: [], include: ["a", "b"], exclude: [] }, ["a", "b"]) === true, "include group must be AND");
assert(matchesFilter({ only: [], include: ["a", "b"], exclude: [] }, ["a"]) === false, "include group must need every tag");
assert(matchesFilter({ only: ["a"], include: [], exclude: ["a"] }, ["a"]) === false, "exclude must win");

// cycle: only -> include -> exclude -> off
assert(cycleState(undefined) === FILTER_STATE.ONLY, "first click is only");
assert(cycleState(FILTER_STATE.ONLY) === FILTER_STATE.INCLUDE, "only -> include");
assert(cycleState(FILTER_STATE.INCLUDE) === FILTER_STATE.EXCLUDE, "include -> exclude");
assert(cycleState(FILTER_STATE.EXCLUDE) === null, "exclude -> off");

// search is case-insensitive substring and runs BEFORE any row cap
const rows = [
  { tag: "ganyu_(genshin_impact)", count: 3 },
  { tag: "1girl", count: 900 },
  { tag: "solo", count: 500 },
  { tag: "keqing_(genshin_impact)", count: 2 },
];
const searched = selectTagRows(rows, "GENSHIN", FILTER_SORT.COUNT);
assert(searched.length === 2, "search must be a case-insensitive substring, got " + searched.length);
assert(searched[0].tag === "ganyu_(genshin_impact)", "count order must survive the search");
const byName = selectTagRows(rows, "", FILTER_SORT.NAME).map((row) => row.tag);
assert(byName[0] === "1girl", "name sort must start at 1girl, got " + byName[0]);
assert(byName[byName.length - 1] === "solo", "name sort must end at solo, got " + byName[byName.length - 1]);
const counted = countTags([["a", "b"], ["a"], []]);
const aCount = counted.find((row) => row.tag === "a");
assert(aCount && aCount.count === 2, "countTags must count across images");

// ---- panel --------------------------------------------------------------
/**
 * One node set per panel. Two panels sharing one fake DOM would let the second panel's
 * paint be read as the first one's - the real app has one filter panel and one strip.
 */
function makeNodes() {
  return {
    list: new FakeNode("div"),
    // The strip and its two hosts live in the center panel, next to #gallery: filter.js
    // hides the strip and paints the condition chips into #filter-active.
    strip: new FakeNode("div"),
    active: new FakeNode("div"),
    count: new FakeNode("span"),
    scope: new FakeNode("div"),
    empty: new FakeNode("p"),
    search: new FakeNode("input"),
    sort: new FakeNode("select"),
    selection: new FakeNode("input"),
    visibleScope: new FakeNode("input"),
  };
}
const nodes = makeNodes();
const selected = new Set();
let changes = 0;
const panel = mod.createFilterPanel(Object.assign({}, nodes, {
  isSelected: (path) => selected.has(path),
  onFilterChange: () => { changes += 1; },
}));

const paths = ["/d/1.png", "/d/2.png", "/d/3.png"];
const tags = {
  "/d/1.png": ["a", "b"],
  "/d/2.png": ["a", "c"],
  "/d/3.png": ["b", "z"],
};
const getTags = (path) => tags[path] || null;

panel.cycle("a");
let visible = panel.apply(paths, getTags);
assert(JSON.stringify(visible) === JSON.stringify(["/d/1.png", "/d/2.png"]), "only=a must keep 1 and 2, got " + visible.join(","));

panel.cycle("a");
assert(panel.getState().include.indexOf("a") >= 0, "second click must become include");
visible = panel.apply(paths, getTags);
assert(JSON.stringify(visible) === JSON.stringify(["/d/1.png", "/d/2.png"]), "include=a must keep 1 and 2");

panel.cycle("a");
visible = panel.apply(paths, getTags);
assert(JSON.stringify(visible) === JSON.stringify(["/d/3.png"]), "exclude=a must keep only 3");

panel.cycle("a");
assert(panel.hasFilter() === false, "fourth click must clear the tag");
visible = panel.apply(paths, getTags);
assert(visible.length === 3, "no filter must show every image");

// selection only intersects the tag filter, it does not replace it
selected.add("/d/1.png");
nodes.selection.checked = true;
nodes.selection.fire("change");
visible = panel.apply(paths, getTags);
assert(JSON.stringify(visible) === JSON.stringify(["/d/1.png"]), "selection only must intersect, got " + visible.join(","));
assert(panel.hasFilter() === true, "selection only counts as a filter");

// visible-scoped frequency counts only the images on screen
panel.clearAll();
assert(nodes.selection.checked === false && nodes.search.value === "", "clearAll must reset search and selection");
panel.cycle("a");
nodes.visibleScope.checked = true;
nodes.visibleScope.fire("change");
assert(panel.isVisibleScope() === true, "visible scope must be reported");
visible = panel.apply(paths, getTags);
assert(visible.length === 2, "only=a still shows 2 images");
let painted = byClass(nodes.list, "freq-item").map((node) => node.dataset.tag);
assert(painted.indexOf("z") < 0, "visible-scoped table must not offer a tag that left the screen: " + painted.join(","));
assert(painted.length === 3, "visible-scoped table must hold a/b/c, got " + painted.join(","));
assert(nodes.scope.textContent.indexOf("3") >= 0, "scope line must report the distinct tag count");

// The rows are the only way to narrow the set, so they must be real buttons: a
// span with a click delegate cannot be reached or pressed from the keyboard.
const freqRows = byClass(nodes.list, "freq-item");
for (const row of freqRows) {
  assert(row.tagName === "BUTTON", "a frequency row is a <" + row.tagName + ">, not a button");
  assert(row.type === "button", "a frequency row button has no explicit type");
}
const rowA = freqRows.find((node) => node.dataset.tag === "a");
assert(rowA, "the row for tag a is missing");
const spoken = rowA.children.filter((node) => hasClass(node, "sr-only"));
assert(spoken.length === 1, "a frequency row must carry exactly one spoken state, got " + spoken.length);
assert(spoken[0].textContent === t("filter.only"), "the row does not say its tri-state out loud: " + spoken[0].textContent);
const rowB = freqRows.find((node) => node.dataset.tag === "b");
assert(rowB, "the row for tag b is missing");
const spokenB = rowB.children.filter((node) => hasClass(node, "sr-only"));
assert(spokenB[0].textContent === t("filter.stateOff"), "an unfiltered row must say so: " + spokenB[0].textContent);

// searching the table can empty it, and says so instead of "no data yet"
nodes.search.value = "zzz";
nodes.search.fire("input");
assert(byClass(nodes.list, "freq-item").length === 0, "search must filter the rows");
assert(nodes.empty.hidden === false, "an empty search must show the empty note");

// A failed load is not "no data yet": the old table must go and the note must
// say what happened, not invite the user to open a directory they already opened.
panel.setFrequencyFailed("load failed: boom");
assert(byClass(nodes.list, "freq-item").length === 0, "a failed load must clear the previous directory rows");
assert(nodes.empty.hidden === false, "a failed load must say something");
// The table failing is not the filter failing: the conditions the grid is under are not
// the table's state, and a strip that emptied itself here would quietly widen the grid.
assert(
  nodes.strip.hidden === false && byClass(nodes.active, "chip").length === 1,
  "a failed frequency load must not drop the conditions the grid is under",
);
assert(
  nodes.empty.textContent === "load failed: boom",
  "the failed state must carry the reason, got " + nodes.empty.textContent,
);
assert(nodes.scope.textContent === "", "a failed load must not keep the old scope counts");
nodes.search.value = "";
nodes.search.fire("input");
// (the table is visible-scoped at this point, and a failed load cleared the
// visible tags; turn the scope off so the payload is what gets painted)
nodes.visibleScope.checked = false;
nodes.visibleScope.fire("change");
panel.setFrequency({ total_images: 3, tags: [{ tag: "a", count: 2 }] }, "dir");
assert(byClass(nodes.list, "freq-item").length === 1, "a successful load must paint the new table");
assert(nodes.empty.hidden === true, "a successful load must clear the failed state");

assert(changes > 0, "onFilterChange was never called");
// --- A caption-less image is outside the tag filter's domain ----------------
// "does not contain X" must not list an image that has no tags at all.
panel.clearAll();
const domainPaths = ["/d/plain.png", "/d/sans.png", "/d/tagged.png"];
const domainTags = { "/d/plain.png": [], "/d/sans.png": ["y"], "/d/tagged.png": ["x"] };
const domainTagsFor = (path) => domainTags[path];
let domainVisible = panel.apply(domainPaths, domainTagsFor);
assert(domainVisible.length === 3, "with no tag filter every image is visible, got " + domainVisible.join(","));
panel.cycle("x");
domainVisible = panel.apply(domainPaths, domainTagsFor);
assert(JSON.stringify(domainVisible) === JSON.stringify(["/d/tagged.png"]), "only=x must keep the tagged image, got " + domainVisible.join(","));
panel.cycle("x");
panel.cycle("x");
domainVisible = panel.apply(domainPaths, domainTagsFor);
assert(JSON.stringify(domainVisible) === JSON.stringify(["/d/sans.png"]), "exclude=x must keep the other tagged image and drop the one with no tags, got " + domainVisible.join(","));
panel.clearAll();

// --- The strip: every condition that narrows the grid, where the grid is ----------
// The chips and the count used to live on the Filter tab, so a gallery showing 3 of 691
// images could not say why - the reason and the way out were on another tab. They are now
// the center panel's #gallery-filter strip, which is on screen exactly while a condition
// is. Missing mode and "show selected images only" narrow the grid as much as a tag does,
// so they are conditions of the same kind, shown and removed the same way.
const missingNodes = makeNodes();
const missingSet = new Set(["/d/plain.png"]);
const panel2 = mod.createFilterPanel(Object.assign({}, missingNodes, {
  isSelected: (path) => selected.has(path),
  isMissing: (path) => missingSet.has(path),
}));
function chip(kind) {
  const found = find(missingNodes.active, (node) => node.dataset && node.dataset.condition === kind);
  return found.length > 0 ? found[0] : null;
}
function removeChip(kind) {
  const target = chip(kind);
  assert(target, "the strip has no " + kind + " condition, so it cannot be undone from the grid");
  const remove = target.children.filter((node) => hasClass(node, "chip-remove"))[0];
  assert(remove, "the " + kind + " chip carries no remove button");
  remove.fire("click");
}

assert(missingNodes.strip.hidden === true, "a panel with no condition must not show the strip");

// Normal path: one condition, one chip, and removing it gives the whole grid back.
panel2.cycle("a");
assert(missingNodes.strip.hidden === false, "a tag condition must put the strip on screen");
assert(chip("tag"), "the tag condition is not in the strip");
assert(missingNodes.active.children.length === 1, "one condition must be one chip, got " + missingNodes.active.children.length);
removeChip("tag");
assert(panel2.hasFilter() === false, "removing the only chip must leave no filter");
assert(missingNodes.strip.hidden === true, "the strip must go away with its last condition");
assert(panel2.apply(domainPaths, domainTagsFor).length === 3, "the grid must be whole again once the chip is removed");

// Missing mode is a condition like any other, and it names itself with its own label.
panel2.setMissingOnly(true);
assert(panel2.hasFilter() === true, "missing mode must count as a filter, or there is no way out of it");
assert(panel2.getState().missingOnly === true, "the mode must be readable back");
assert(missingNodes.strip.hidden === false, "missing mode must show the strip");
const modeChip = chip("missing");
assert(modeChip, "missing mode is not one of the strip's conditions");
assert(
  modeChip.children[0].textContent === t("filter.modeMissing"),
  "the mode chip must carry the mode's own label, got " + modeChip.children[0].textContent,
);
const mVisible = panel2.apply(domainPaths, domainTagsFor);
assert(JSON.stringify(mVisible) === JSON.stringify(["/d/plain.png"]), "missing mode must show only the image with no caption, got " + mVisible.join(","));
// ...and the count line is the count: the chip names the mode, once, where it is removed.
assert(
  missingNodes.count.textContent === t("filter.count", { n: 1, total: 3 }),
  "the count line must not restate the mode: " + missingNodes.count.textContent,
);

// "Show selected images only" narrows the grid too, so it is a condition of the strip:
// otherwise the grid would be short with nothing on screen to say why. Two conditions are
// two chips, and removing one of them must leave the other one on. (A tag never coexists
// with missing mode - entering a tag filter leaves the mode, see below - so the pair that
// stacks is the two modes, which apply() intersects.)
missingNodes.selection.checked = true;
missingNodes.selection.fire("change");
assert(missingNodes.strip.hidden === false, "the selection condition must put the strip on screen");
assert(missingNodes.active.children.length === 2, "two conditions must be two chips, got " + missingNodes.active.children.length);
const selectionChip = chip("selection");
assert(selectionChip, "the selection condition is not in the strip");
assert(
  selectionChip.children[0].textContent === t("filter.onlySelected"),
  "the selection chip must name the checkbox it stands for, got " + selectionChip.children[0].textContent,
);
removeChip("missing");
assert(panel2.getState().missingOnly === false, "removing the mode chip must leave the mode");
assert(panel2.hasSelectionFilter() === true, "removing one condition must not remove the other");
assert(missingNodes.strip.hidden === false, "a condition still narrows the grid, so the strip stays");
removeChip("selection");
assert(panel2.hasSelectionFilter() === false, "removing the selection chip must leave the condition");
assert(missingNodes.selection.checked === false, "the chip and the checkbox it stands for must not disagree");
assert(missingNodes.strip.hidden === true, "nothing narrows the grid any more");

// Entering a tag filter is what leaves missing mode (missing-caption-mode.md), and the
// mode's chip goes with it.
panel2.setMissingOnly(true);
panel2.cycle("y");
assert(panel2.getState().missingOnly === false, "touching a tag must leave missing mode");
assert(chip("missing") === null, "the mode chip must go with the mode");
assert(chip("tag"), "the tag condition it became must be in the strip");
removeChip("tag");
assert(panel2.hasFilter() === false, "the last chip is still the way out");

// A language switch repaints the strip: its labels are painted at render time, and
// filterPanel.relabel() is what app.js's relabelAfterLocaleChange() calls.
const en = (await import(pathToFileURL(WEB + "/js/locales/en.js").href)).STRINGS;
panel2.setMissingOnly(true);
strings.setLocale("en", { persist: false, notify: false });
panel2.relabel();
assert(
  chip("missing").children[0].textContent === en["filter.modeMissing"],
  "relabel() must repaint the mode chip, got " + chip("missing").children[0].textContent,
);
strings.setLocale("zh-CN", { persist: false, notify: false });
panel2.relabel();
assert(
  chip("missing").children[0].textContent === t("filter.modeMissing"),
  "switching back must repaint the mode chip too, got " + chip("missing").children[0].textContent,
);

console.log("filter harness OK " + JSON.stringify({ painted, changes }));
