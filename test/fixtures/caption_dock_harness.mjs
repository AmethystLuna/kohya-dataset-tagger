/**
 * Headless behaviour harness for the pinned caption editor (the dock that
 * replaced the right column's "Tags" tab).
 *
 * The static tests can see that the dock markup sits outside .tab-panels. Only
 * running the modules can show the two things a user actually experiences:
 *
 *  1. nothing active => the control block is HIDDEN (the dock then shows one
 *     short empty-state line) instead of being painted disabled and empty;
 *  2. the editor follows the gallery's active image - including the keyboard
 *     route, where an arrow key moves the grid cursor and the caption under the
 *     editor changes with it, which is what "walk the grid fixing captions one
 *     image after another" means;
 *  3. the write still goes out through the one caption endpoint, and a write
 *     answer carrying cache_remove_failed is surfaced instead of swallowed
 *     (this repository's invariant number one, seen from the frontend side).
 *
 * The gallery is wired to the editor the way app.js wires it; the real line in
 * app.js is pinned separately by test_web_caption_editor.py.
 *
 * Usage: node caption_dock_harness.mjs <absolute path to web/>
 */
import { pathToFileURL } from "node:url";

const WEB = process.argv[2];
if (!WEB) throw new Error("usage: node caption_dock_harness.mjs <web dir>");

function assert(condition, message) {
  if (!condition) throw new Error("caption dock harness: " + message);
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
    this.src = "";
    this.parentNode = null;
    this.offsetTop = 0;
    this.scrollTop = 0;
    this.scrollHeight = 0;
    this.clientHeight = 0;
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
  removeAttribute(name) { delete this.attributes[name]; if (name === "src") this.src = ""; }
  contains(node) {
    if (node === this) return true;
    for (const child of this.children) if (child && child.contains && child.contains(node)) return true;
    return false;
  }
  closest(selector) {
    const name = String(selector || "").replace(/^\./, "");
    let node = this;
    while (node) {
      if (String(node.className || "").split(/\s+/).includes(name)) return node;
      node = node.parentNode;
    }
    return null;
  }
  focus() { globalThis.document.activeElement = this; }
  select() {}
  getBoundingClientRect() { return { left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0 }; }
  addEventListener(type, handler) { (this.listeners[type] ||= []).push(handler); }
  removeEventListener(type, handler) { this.listeners[type] = (this.listeners[type] || []).filter((item) => item !== handler); }
  /** Dispatch one event, bubbling up the parent chain like the real DOM does:
   *  a click on a tile is delivered to the gallery container that listens. */
  fire(type, event) {
    const payload = event || { preventDefault() {} };
    if (!payload.target) payload.target = this;
    let node = this;
    while (node) {
      for (const handler of [...(node.listeners[type] || [])]) handler(payload);
      node = node.parentNode;
    }
  }
}

const body = new FakeNode("body");
globalThis.document = {
  body,
  documentElement: { lang: "" },
  activeElement: null,
  createElement: (tag) => new FakeNode(tag),
  createDocumentFragment: () => new FakeNode("fragment"),
  createTextNode: (text) => ({ nodeType: 3, textContent: text }),
  addEventListener() {},
  removeEventListener() {},
  querySelectorAll: () => [],
};
// No IntersectionObserver: gallery.js then requests the thumbnails immediately.
globalThis.window = { setTimeout, clearTimeout, addEventListener() {}, removeEventListener() {} };

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

function findAll(node, predicate, out = []) {
  if (!node || typeof node !== "object") return out;
  if (predicate(node)) out.push(node);
  for (const child of node.children || []) findAll(child, predicate, out);
  return out;
}
const hasClass = (node, name) => String(node.className || "").split(/\s+/).includes(name);
const el = (tag, className) => { const node = new FakeNode(tag); if (className) node.className = className; return node; };

const tagsMod = await import(pathToFileURL(WEB + "/js/tags.js").href);
const apiMod = await import(pathToFileURL(WEB + "/js/api.js").href);
const galleryMod = await import(pathToFileURL(WEB + "/js/gallery.js").href);

// ---- a caption store behind the real api object ---------------------------
const TAGS = { "/a/one.png": ["1girl", "solo"], "/a/two.png": ["1girl", "smile"] };
const puts = [];
let cacheRemoveFailed = [];
apiMod.api.getCaption = async (path) => ({
  image: path,
  caption_path: path.replace(/\.png$/, ".txt"),
  exists: Object.prototype.hasOwnProperty.call(TAGS, path),
  text: (TAGS[path] || []).join(", "),
  tags: (TAGS[path] || []).slice(),
  multiline_warning: false,
});
apiMod.api.putCaption = async (payload) => {
  puts.push(payload);
  const tags = Array.isArray(payload.tags) ? payload.tags.slice() : [];
  TAGS[payload.image] = tags;
  return {
    image: payload.image,
    caption_path: payload.image.replace(/\.png$/, ".txt"),
    cache_removed: [],
    cache_remove_failed: cacheRemoveFailed.slice(),
  };
};
apiMod.api.tagAutocomplete = async () => ({ suggestions: [] });

// ---- the editor, with the dock's control block ----------------------------
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
  form: el("div", "caption-form"),
};
nodes.chips.parentNode = body;
const editor = tagsMod.createTagEditor(Object.assign({}, nodes, {
  confirm: async () => true,
  onSaved: () => {},
  onError: (message) => errors.push(message),
  onNotify: () => {},
}));

// ---- 1. nothing active: an empty state, not an empty form -----------------
// The editor is built before any image is active, which is the state the dock
// opens in - the panels are constructed at module scope, long before a title.
assert(nodes.form.hidden === true, "an editor with no image must hide its control block");
assert(editor.getPath() === null, "a freshly built editor must have no path");

// ---- 2. an active image fills the editor and shows the controls -----------
await editor.load("/a/one.png");
assert(nodes.form.hidden === false, "loading the active image must show the control block");
assert(JSON.stringify(editor.getTags()) === JSON.stringify(["1girl", "solo"]), "load did not fill the chips");

// ---- 3. losing the active image goes back to the empty state --------------
editor.disable();
assert(nodes.form.hidden === true, "disable() (filtered away, failed load) must hide the control block again");
editor.enable();
assert(nodes.form.hidden === false, "enable() must bring the control block back");

// ---- 4. the editor follows the gallery's active image ---------------------
// Wired the way app.js wires it: the gallery announces the active path, the app
// loads it into this one editor, and "no path" is the empty state.
const container = el("div", "gallery");
const centerPanel = el("section");
const marquee = el("div");
const gallery = galleryMod.createGallery({
  container,
  centerPanel,
  marquee,
  onActiveChange: (path) => {
    if (path) { void editor.load(path); return; }
    editor.disable();
  },
});

gallery.setImages([{ path: "/a/one.png" }, { path: "/a/two.png" }]);
assert(nodes.form.hidden === true, "a fresh listing has nothing active, so the dock must be empty");

// the click route: a tile click makes that image active
const tiles = findAll(container, (node) => hasClass(node, "tile"));
const tileFor = (path) => tiles.filter((tile) => tile.dataset.path === path)[0] || null;
const firstTile = tileFor("/a/one.png");
assert(firstTile, "the harness did not build the tiles it expects");
firstTile.fire("click", { target: firstTile, shiftKey: false, ctrlKey: false, metaKey: false });
await tick();
assert(nodes.form.hidden === false, "clicking a tile must load it into the editor");
assert(editor.getPath() === "/a/one.png", "the editor must follow the clicked image, got " + editor.getPath());
assert(JSON.stringify(editor.getTags()) === JSON.stringify(["1girl", "solo"]), "the editor shows the wrong caption");

// the keyboard route: an arrow key moves the grid cursor and the editor with it
container.fire("keydown", { key: "ArrowRight", preventDefault() {} });
await tick();
assert(editor.getPath() === "/a/two.png", "the arrow key must move the editor to the next image, got " + editor.getPath());
assert(JSON.stringify(editor.getTags()) === JSON.stringify(["1girl", "smile"]), "the editor shows the wrong caption after paging");
container.fire("keydown", { key: "ArrowRight", preventDefault() {} });
await tick();
assert(editor.getPath() === "/a/two.png", "paging past the end of the grid must stay on the last image");

// a caption write invalidates what the filter and the badges are derived from,
// so the app reloads the caption; the dock must survive that reload
await editor.load("/a/two.png");
assert(nodes.form.hidden === false, "reloading the same image must keep the controls on screen");

gallery.setImages([{ path: "/a/one.png" }]);
assert(nodes.form.hidden === true, "navigating away leaves nothing active -> the empty state");

// ---- 5. one write path, and its cache answer is not swallowed -------------
gallery.setActive("/a/one.png");
await tick();
nodes.input.value = "outdoors";
nodes.input.fire("keydown", { key: "Enter", preventDefault() {} });
await tick();
assert(editor.isDirty(), "adding a tag must mark the caption dirty");
cacheRemoveFailed = ["./one_anima_te.safetensors"];
const answer = await editor.flush();
assert(answer !== null, "the flush must really have sent the caption");
assert(puts.length === 1, "the dock must write through the one caption endpoint, got " + puts.length + " writes");
assert(puts[0].image === "/a/one.png" && Array.isArray(puts[0].tags), "the write payload must stay {image, tags}");
assert(JSON.stringify(TAGS["/a/one.png"]) === JSON.stringify(["1girl", "solo", "outdoors"]), "the tag did not reach the caption");
assert(errors.length === 1, "a write that could not drop the text encoder cache must be reported, got " + errors.length);

console.log("caption dock harness OK");
