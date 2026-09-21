/**
 * Headless behaviour harness for the gallery's scroll priority (gallery.js).
 *
 * Why: the loader's queue is bounded (THUMB_CONCURRENCY) while one directory
 * screen queues far more than that, so the ORDER the queue drains in is what the
 * user experiences. Before this, a tile that came into view on scroll was queued
 * behind every tile the first screen had already queued, and the images the user
 * was looking at were the last to arrive. The mechanism is two
 * IntersectionObservers - one 400px prefetch margin, one with none - and only
 * running the module can show that the second one really re-ranks the queue.
 *
 * Usage: node gallery_scroll_harness.mjs <absolute path to web/>
 */
import { pathToFileURL } from "node:url";

const WEB = process.argv[2];
if (!WEB) throw new Error("usage: node gallery_scroll_harness.mjs <web dir>");

function assert(condition, message) {
  if (!condition) throw new Error("gallery scroll harness: " + message);
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
    this.textContent = "";
    this.className = "";
    this.hidden = false;
    this.parentNode = null;
    this.value = "";
  }
  append(...nodes) {
    for (const node of nodes) {
      if (node && typeof node === "object") node.parentNode = this;
      this.children.push(node);
    }
  }
  replaceChildren(...nodes) { this.children = []; this.append(...nodes); }
  setAttribute(name, value) { this.attributes[name] = value; }
  getAttribute(name) { return this.attributes[name]; }
  removeAttribute(name) {
    delete this.attributes[name];
    if (name === "src" || name === "srcset") this[name] = "";
  }
  addEventListener(type, handler) { (this.listeners[type] ||= []).push(handler); }
  removeEventListener() {}
  fire(type, event) {
    for (const handler of [...(this.listeners[type] || [])]) handler(event || { preventDefault() {} });
  }
  closest() { return null; }
  focus() {}
  scrollIntoView() {}
}

function find(node, predicate, out = []) {
  if (!node || typeof node !== "object") return out;
  if (predicate(node)) out.push(node);
  for (const child of node.children || []) find(child, predicate, out);
  return out;
}

const hasClass = (node, name) =>
  (node.classList && node.classList.contains(name)) ||
  String(node.className || "").split(/\s+/).includes(name);
const byClass = (root, name) => find(root, (node) => hasClass(node, name));

globalThis.document = {
  body: new FakeNode("body"),
  createElement: (tag) => new FakeNode(tag),
  createDocumentFragment: () => new FakeNode("fragment"),
  addEventListener() {},
  removeEventListener() {},
};
globalThis.window = { setTimeout, clearTimeout, addEventListener() {}, removeEventListener() {} };

/** Records its targets and lets the harness deliver crossings by hand. */
class FakeIntersectionObserver {
  constructor(callback, options) {
    this.callback = callback;
    this.options = options || {};
    this.targets = [];
    FakeIntersectionObserver.instances.push(this);
  }
  observe(target) { this.targets.push(target); }
  unobserve(target) { this.targets = this.targets.filter((item) => item !== target); }
  disconnect() { this.targets = []; }
  crossing(target, isIntersecting) {
    this.callback([{ target, isIntersecting }], this);
  }
}
FakeIntersectionObserver.instances = [];
globalThis.IntersectionObserver = FakeIntersectionObserver;

const galleryMod = await import(pathToFileURL(WEB + "/js/gallery.js").href);

const container = new FakeNode("div");
const centerPanel = new FakeNode("section");
const marquee = new FakeNode("div");
marquee.hidden = true;
const gallery = galleryMod.createGallery({
  container,
  centerPanel,
  marquee,
  thumbConcurrency: 2,
});

const ITEMS = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9].map((n) => ({
  name: n + ".jpeg",
  path: "/data/1_post/" + n + ".jpeg",
}));
gallery.setImages(ITEMS, []);

const imgs = byClass(container, "tile-img");
assert(imgs.length === ITEMS.length, "expected " + ITEMS.length + " tiles, got " + imgs.length);

const observers = FakeIntersectionObserver.instances;
assert(observers.length === 2, "the gallery must run two observers (prefetch + viewport), got " + observers.length);
const prefetch = observers.find((item) => item.options.rootMargin === "400px");
const viewport = observers.find((item) => item.options.rootMargin === "0px");
assert(prefetch, "there is no observer with the 400px prefetch margin");
assert(viewport, "there is no zero-margin observer, so nothing knows what is on screen");
assert(prefetch !== viewport, "the two observers must be different instances");
assert(viewport.targets.length === ITEMS.length, "the viewport observer must watch every tile, got " + viewport.targets.length);

const crossingOf = (observer, list, indices, isIntersecting) => {
  for (const index of indices) observer.crossing(list[index], isIntersecting);
};
const crossing = (observer, indices, isIntersecting) => crossingOf(observer, imgs, indices, isIntersecting);

// The first screen: the prefetch margin queues the first eight tiles while the
// viewport observer reports the two the user is actually looking at.
crossing(prefetch, [0, 1, 2, 3, 4, 5, 6, 7], true);
crossing(viewport, [0, 1], true);
assert(imgs[0].src && imgs[1].src, "the first on-screen tiles must start loading");
assert(!imgs[2].src, "off-screen tiles must wait while the concurrency is full");

// The user scrolls down one screen: tiles 4..7 arrive, tile 0 leaves. The next
// slot must go to a tile that is on screen, not to an earlier prefetch.
crossing(viewport, [4, 5, 6, 7], true);
crossing(viewport, [0], false);
imgs[0].fire("load");
assert(imgs[4].src, "after scrolling, the tile in view must be served before the first screen's prefetches");
assert(!imgs[2].src, "an earlier prefetch must not start while an on-screen tile is waiting");

// A tile that scrolls back out of view loses its priority again.
crossing(viewport, [4], false);
imgs[1].fire("load");
imgs[5].fire("load");
assert(imgs[6].src, "the remaining on-screen tiles must still be served before prefetches");

// ---- a folder level: the nine-grid previews get the same priority --------
// The folder cards share the loader and the same observers (p0-spec section
// 4.13), so a scroll on a folder level must re-rank their previews too - and
// switching level must build a fresh observer pair, because clearImages()
// disconnects the pair that belonged to the image grid.
const DIRS = [0, 1, 2, 3, 4, 5].map((n) => ({
  name: "d" + n,
  path: "/data/d" + n,
  image_count: 9,
  preview_images: [0, 1, 2, 3, 4, 5, 6, 7, 8].map((p) => "/data/d" + n + "/" + p + ".jpeg"),
}));
gallery.setImages([], DIRS);
assert(
  FakeIntersectionObserver.instances.length === 4,
  "switching level must build a fresh observer pair, got " + FakeIntersectionObserver.instances.length,
);
assert(byClass(container, "dir-card").length === DIRS.length, "expected " + DIRS.length + " folder cards");

const dirImgs = byClass(container, "dir-thumb");
assert(dirImgs.length === DIRS.length * 9, "expected 9 previews per folder card, got " + dirImgs.length);
const dirObservers = FakeIntersectionObserver.instances.slice(2);
const dirPrefetch = dirObservers.find((item) => item.options.rootMargin === "400px");
const dirViewport = dirObservers.find((item) => item.options.rootMargin === "0px");
assert(dirPrefetch && dirViewport, "the folder level must also run the prefetch + viewport pair");
assert(
  dirViewport.targets.length === dirImgs.length,
  "every folder preview must be watched by the viewport observer, got " + dirViewport.targets.length,
);

// The first screen queues the first two cards' previews (18 images) and shows
// the first card. Scrolling onto the second card must serve its previews first.
crossingOf(dirPrefetch, dirImgs, [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17], true);
crossingOf(dirViewport, dirImgs, [0, 1], true);
assert(dirImgs[0].src && dirImgs[1].src, "the first folder card's previews must start loading");
assert(!dirImgs[2].src, "the rest must wait while the concurrency is full");

crossingOf(dirViewport, dirImgs, [9, 10, 11, 12, 13, 14, 15, 16, 17], true);
crossingOf(dirViewport, dirImgs, [0], false);
dirImgs[0].fire("load");
assert(dirImgs[9].src, "a folder card scrolled onto must be served before the first card's earlier previews");
assert(!dirImgs[2].src, "an earlier folder preview must not start while the visible card is waiting");

console.log("gallery scroll harness OK " + JSON.stringify({ observers: FakeIntersectionObserver.instances.length }));
