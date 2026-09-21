/**
 * Headless behaviour harness for the sub directory cards gallery.js renders
 * (p0-spec section 4.13).
 *
 * The static tests can see that the cards are wired up, but not that the mosaic
 * adapts to the folder (1 / 2x2 / 3x3), that the name and the image count are
 * rendered, that clicking a card calls back with that directory, or that
 * switching directory releases the folder bitmaps. Those are the parts users
 * see, so this runs the real module against a minimal fake DOM - no jsdom, no
 * browser.
 *
 * Usage: node folder_grid_harness.mjs <absolute path to web/>
 */
import { pathToFileURL } from "node:url";

const WEB = process.argv[2];
if (!WEB) throw new Error("usage: node folder_grid_harness.mjs <web dir>");

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
    this.style = {};
    this.scrollTop = 0;
    this.scrollHeight = 0;
    this.clientHeight = 0;
    // No layout engine here: the harness assigns these, which is what lets
    // navscroll.js (the navigation scroll) run for real.
    this.layoutTop = 0;
    this.layoutHeight = 0;
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
  // gallery.js sets img.src directly and clears it with removeAttribute("src").
  removeAttribute(name) {
    delete this.attributes[name];
    if (name === "src" || name === "srcset") this[name] = "";
  }
  addEventListener(type, handler) { (this.listeners[type] ||= []).push(handler); }
  removeEventListener(type, handler) {
    this.listeners[type] = (this.listeners[type] || []).filter((item) => item !== handler);
  }
  fire(type, event) {
    for (const handler of [...(this.listeners[type] || [])]) handler(event || { preventDefault() {} });
  }
  closest() { return null; }
  contains(node) { return this === node || find(this, (item) => item === node).length > 0; }
  focus() {}
  scrollIntoView() {}
  getBoundingClientRect() {
    return {
      left: 0, top: this.layoutTop, right: 0, bottom: this.layoutBottom || 0,
      width: 0, height: this.layoutHeight,
    };
  }
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
// No IntersectionObserver on purpose: gallery.js then loads the thumbnails
// immediately, which is what lets this harness observe the img src.
globalThis.window = { setTimeout, clearTimeout, addEventListener() {}, removeEventListener() {} };

const galleryMod = await import(pathToFileURL(WEB + "/js/gallery.js").href);

const container = new FakeNode("div");
const centerPanel = new FakeNode("section");
const marquee = new FakeNode("div");
marquee.hidden = true;
const openedDirs = [];
const gallery = galleryMod.createGallery({
  container,
  centerPanel,
  marquee,
  onOpenDir: (path) => { openedDirs.push(path); },
});

const shot = (index) => "/data/1_post/" + index + ".jpeg";
const SAMPLES = [1, 2, 3, 4, 5, 6, 7, 8, 9].map(shot);
gallery.setImages([], [
  { name: "solo", path: "/data/solo", image_count: 1, preview_images: [shot(1)] },
  { name: "trio", path: "/data/trio", image_count: 3, preview_images: [shot(1), shot(2), shot(3)] },
  { name: "1_post", path: "/data/1_post", image_count: 12, preview_images: SAMPLES },
  // more samples than a 3x3 can hold: the extras must be dropped, not wrapped
  { name: "overflow", path: "/data/overflow", image_count: 20, preview_images: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12].map(shot) },
]);

const cards = byClass(container, "dir-card");
if (cards.length !== 4) throw new Error("expected 4 folder cards, got " + cards.length);
const [solo, trio, post, overflow] = cards;
const gridOf = (card) => byClass(card, "dir-thumbs")[0];

// 1 image -> one full cell; 2-4 -> 2x2; 5+ -> 3x3
if (!hasClass(gridOf(solo), "dir-thumbs-1")) throw new Error("a one-image folder must use a single cell");
if (byClass(solo, "dir-thumb").length !== 1) throw new Error("solo folder rendered the wrong number of thumbnails");
if (!hasClass(gridOf(trio), "dir-thumbs-2")) throw new Error("a three-image folder must use a 2x2 mosaic");
if (byClass(trio, "dir-thumb").length !== 3) throw new Error("trio folder rendered the wrong number of thumbnails");
if (!hasClass(gridOf(post), "dir-thumbs-3")) throw new Error("a twelve-image folder must use a 3x3 mosaic");
if (!hasClass(gridOf(overflow), "dir-thumbs-3")) throw new Error("an oversized sample must still use a 3x3 mosaic");

const imgs = byClass(post, "dir-thumb");
if (imgs.length !== 9) throw new Error("expected a 3x3 grid of 9 thumbnails, got " + imgs.length);
if (byClass(overflow, "dir-thumb").length !== 9) {
  throw new Error("samples beyond the 3x3 were rendered: " + byClass(overflow, "dir-thumb").length);
}
if (String(imgs[0].src).indexOf("size=grid") < 0) {
  throw new Error("folder thumbnails must use the grid size: " + imgs[0].src);
}
if (String(imgs[0].src).indexOf(encodeURIComponent(SAMPLES[0])) < 0) {
  throw new Error("thumbnail src does not point at the sampled image: " + imgs[0].src);
}
if (imgs[0].loading !== "lazy") throw new Error("folder thumbnails must be lazy");

const name = byClass(post, "dir-name")[0];
if (!name || name.textContent !== "1_post") throw new Error("folder name not rendered");
const count = byClass(post, "dir-count")[0];
if (!count || count.textContent.indexOf("12") < 0) {
  throw new Error("image count not rendered: " + (count && count.textContent));
}

// clicking a card opens that directory
post.fire("click");
if (openedDirs[openedDirs.length - 1] !== "/data/1_post") {
  throw new Error("click did not open the directory: " + openedDirs.join(", "));
}

// Navigation scroll (navscroll.js): revealDir marks the folder a navigation came
// out of and scrolls the workspace to it. The card sits at content offset 500 in
// a 100px window, so centring it means scrollTop 500 - (100 - 30) / 2 = 465.
container.scrollHeight = 1000;
container.clientHeight = 100;
container.layoutHeight = 100;
post.layoutTop = 500;
post.layoutHeight = 30;
if (gallery.revealDir("/data/missing") !== false) {
  throw new Error("revealDir claimed a directory that has no card");
}
if (gallery.revealDir("/data/1_post") !== true) {
  throw new Error("revealDir did not find the card of the directory it was given");
}
if (!hasClass(post, "is-revealed")) throw new Error("the revealed folder card is not marked");
if (container.scrollTop !== 465) {
  throw new Error("the workspace did not scroll to the card: scrollTop " + container.scrollTop);
}
if (hasClass(solo, "is-revealed")) throw new Error("an unrelated card was marked as revealed");

// A card the workspace cannot measure (no layout) is reported as not revealed,
// so the caller falls back to the position the listing was left at.
container.layoutHeight = 0;
container.scrollTop = 42;
if (gallery.revealDir("/data/1_post") !== false) {
  throw new Error("revealDir claimed success without any layout to measure");
}
if (container.scrollTop !== 42) throw new Error("a failed reveal moved the workspace anyway");
container.layoutHeight = 100;

// switching directories drops both the cards and their bitmaps
gallery.setImages([], []);
if (byClass(container, "dir-card").length !== 0) throw new Error("folder cards survived a new listing");
if (imgs.some((img) => img.src)) throw new Error("folder thumbnail src was not dropped when switching");

// the caller passes dirs only at a folder-only level; with an image grid there are none
gallery.setImages([{ path: "/data/1_post/1.jpeg", name: "1.jpeg" }], []);
if (byClass(container, "dir-card").length !== 0) throw new Error("folder cards rendered in an image grid");
if (byClass(container, "tile").length !== 1) throw new Error("image tiles stopped rendering");

console.log("folder grid harness OK " + JSON.stringify({ openedDirs }));
