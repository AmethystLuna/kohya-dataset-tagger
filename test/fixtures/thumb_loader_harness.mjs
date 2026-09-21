/**
 * Headless behaviour harness for gallery.js's ThumbLoader.
 *
 * Why: nothing static can see that a tile whose bitmap is still in flight says
 * so. Without the .is-loading marker an undecoded tile shows the frame's
 * transparency checkerboard, which reads as "this image has no pixels" rather
 * than "this image is on its way" - and the concurrency cap is what keeps a 200
 * image directory from opening 200 sockets. Both are behaviour, so this drives
 * the real module against a fake <img>.
 *
 * Usage: node thumb_loader_harness.mjs <absolute path to web/>
 */
import { pathToFileURL } from "node:url";

const WEB = process.argv[2];
if (!WEB) throw new Error("usage: node thumb_loader_harness.mjs <web dir>");

function assert(condition, message) {
  if (!condition) throw new Error("thumb loader harness: " + message);
}

class FakeClassList {
  constructor() { this.items = new Set(); }
  add(...names) { for (const name of names) this.items.add(name); }
  remove(...names) { for (const name of names) this.items.delete(name); }
  contains(name) { return this.items.has(name); }
}

class FakeImg {
  constructor(name) {
    this.name = name;
    this.classList = new FakeClassList();
    this.listeners = {};
    this.src = null;
  }
  addEventListener(type, handler) {
    (this.listeners[type] || (this.listeners[type] = [])).push(handler);
  }
  removeAttribute(name) { if (name === "src") this.src = null; }
  fire(type) {
    for (const handler of [...(this.listeners[type] || [])]) handler();
  }
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
    this.parentNode = null;
  }
  append(...nodes) { for (const node of nodes) { if (node && typeof node === "object") node.parentNode = this; this.children.push(node); } }
  addEventListener() {}
  removeEventListener() {}
  setAttribute(name, value) { this.attributes[name] = value; }
  getAttribute(name) { return this.attributes[name]; }
  querySelectorAll() { return []; }
  replaceChildren(...nodes) { this.children = nodes; }
}

globalThis.document = {
  body: new FakeNode("body"),
  createElement: (tag) => new FakeNode(tag),
  createDocumentFragment: () => new FakeNode("fragment"),
  createTextNode: (text) => ({ nodeType: 3, textContent: text }),
  addEventListener() {},
  removeEventListener() {},
};
globalThis.window = { setTimeout, clearTimeout, addEventListener() {}, removeEventListener() {} };

const mod = await import(pathToFileURL(WEB + "/js/gallery.js").href);
assert(typeof mod.ThumbLoader === "function", "ThumbLoader is not exported");
assert(Number.isInteger(mod.THUMB_CONCURRENCY) && mod.THUMB_CONCURRENCY > 0,
  "THUMB_CONCURRENCY must be a positive integer, got " + mod.THUMB_CONCURRENCY);

const { ThumbLoader, THUMB_CONCURRENCY } = mod;

// ---- a tile in flight says so, and clears the mark when the bitmap lands ---
{
  const loader = new ThumbLoader(2);
  const img = new FakeImg("a");
  loader.request(img, "/thumb/a");
  assert(img.src === "/thumb/a", "request must set src");
  assert(img.classList.contains("is-loading"), "a requested thumbnail must carry is-loading");
  assert(!img.classList.contains("is-loaded"), "a thumbnail cannot be loaded before its load event");
  img.fire("load");
  assert(!img.classList.contains("is-loading"), "is-loading must be cleared once loaded");
  assert(img.classList.contains("is-loaded"), "a loaded thumbnail must carry is-loaded");
}

// ---- a failed thumbnail is an error, not a silent blank -------------------
{
  const loader = new ThumbLoader(2);
  const img = new FakeImg("b");
  loader.request(img, "/thumb/b");
  img.fire("error");
  assert(!img.classList.contains("is-loading"), "a failed thumbnail must not keep is-loading");
  assert(img.classList.contains("is-error"), "a failed thumbnail must carry is-error");
  assert(!img.classList.contains("is-loaded"), "a failed thumbnail must not claim is-loaded");
}

// ---- the concurrency cap holds, and the queue drains in order -------------
{
  const loader = new ThumbLoader(2);
  const imgs = [1, 2, 3, 4].map((n) => new FakeImg("i" + n));
  for (const img of imgs) loader.request(img, "/thumb/" + img.name);
  const started = imgs.filter((img) => img.src !== null);
  assert(started.length === 2, "at most " + 2 + " requests may be in flight, got " + started.length);
  assert(started[0] === imgs[0] && started[1] === imgs[1], "the queue must drain in request order");
  imgs[0].fire("load");
  assert(imgs[2].src === "/thumb/i3", "finishing one request must start the next queued one");
}

// ---- a directory switch abandons the queue --------------------------------
// Division of responsibility, pinned so neither half silently drops it: reset()
// clears the *queued* requests, while the in-flight one is dropped by
// gallery.clearImages(), which strips the src of every tile it is about to
// throw away. A stale generation must never start the next request.
{
  const loader = new ThumbLoader(1);
  const first = new FakeImg("first");
  const second = new FakeImg("second");
  loader.request(first, "/thumb/first");
  loader.request(second, "/thumb/second");
  assert(second.src === null, "the second request must wait for the cap to free up");
  loader.reset();
  assert(second.src === null, "reset must not start a queued request");
  first.fire("load");
  assert(second.src === null, "a stale generation must not start after reset");
}

// ---- the default cap is the module constant ------------------------------
{
  const loader = new ThumbLoader();
  const imgs = [];
  for (let i = 0; i < THUMB_CONCURRENCY + 3; i += 1) {
    const img = new FakeImg("n" + i);
    imgs.push(img);
    loader.request(img, "/thumb/n" + i);
  }
  const started = imgs.filter((img) => img.src !== null);
  assert(started.length === THUMB_CONCURRENCY,
    "the default loader must honour THUMB_CONCURRENCY, got " + started.length);
}

// ---- what is on screen is served before what is merely near it -----------
// The gallery queues a whole screen at a time and prefetches 400px ahead, so the
// tiles the user scrolled onto must not sit behind the prefetches queued for the
// first screen.
{
  const loader = new ThumbLoader(1);
  const running = new FakeImg("running");
  const prefetch = new FakeImg("prefetch");
  const visible = new FakeImg("visible");
  loader.request(running, "/thumb/running");
  loader.request(prefetch, "/thumb/prefetch");
  loader.request(visible, "/thumb/visible", true);
  assert(prefetch.src === null, "the prefetch must wait behind the running request");
  running.fire("load");
  assert(visible.src === "/thumb/visible", "an on-screen tile must start before an earlier prefetch");
  assert(prefetch.src === null, "the prefetch must still wait");
}

// ---- a tile that scrolls into view overtakes the queue -------------------
{
  const loader = new ThumbLoader(2);
  const imgs = [0, 1, 2, 3, 4, 5].map((n) => new FakeImg("q" + n));
  loader.request(imgs[0], "/thumb/q0", true);
  loader.request(imgs[1], "/thumb/q1", true);
  for (const img of imgs.slice(2)) loader.request(img, "/thumb/" + img.name);
  assert(imgs[0].src && imgs[1].src, "the on-screen pair starts immediately");
  assert(imgs[4].src === null, "q4 is only a prefetch so far");

  // the user scrolls: q4 is on screen now, q0 has left
  assert(loader.prioritize(imgs[4], true) === true, "prioritize must re-rank a waiting request");
  loader.prioritize(imgs[0], false);
  imgs[0].fire("load");
  assert(imgs[4].src === "/thumb/q4", "the tile that scrolled into view must beat the prefetches queued before it");
  assert(imgs[2].src === null, "an earlier prefetch must wait for the on-screen tile");
  assert(loader.prioritize(imgs[1], false) === false, "an in-flight request cannot be re-ranked");
}

// ---- a promotion that scrolls away again goes back behind the prefetches --
{
  const loader = new ThumbLoader(1);
  const running = new FakeImg("r");
  const first = new FakeImg("first");
  const second = new FakeImg("second");
  const scrolled = new FakeImg("scrolled");
  loader.request(running, "/thumb/r");
  loader.request(first, "/thumb/first");
  loader.request(second, "/thumb/second");
  loader.request(scrolled, "/thumb/scrolled");
  loader.prioritize(scrolled, true);
  loader.prioritize(scrolled, false);
  running.fire("load");
  assert(first.src === "/thumb/first", "a demoted request must fall behind the prefetches already waiting");
  first.fire("load");
  assert(second.src === "/thumb/second", "after a demotion the queue is back to arrival order");
}

console.log("thumb loader harness OK");
