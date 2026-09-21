/**
 * Headless behaviour harness for web/js/zoom.js - the double-click enlargement.
 *
 * It drives the real module AND the real tag editor (tags.js) against a fake DOM,
 * because the failure this guards against is "double-click does nothing": a static
 * test can prove the wiring exists, not that opening loads the image and its tags.
 *
 * Usage: node zoom_harness.mjs <absolute path to web/>
 */
import { pathToFileURL } from "node:url";

const WEB = process.argv[2];
if (!WEB) throw new Error("usage: node zoom_harness.mjs <web dir>");

class FakeClassList {
  constructor(node) { this.node = node; }
  get set() {
    return new Set(String(this.node.className || "").split(/\s+/).filter(Boolean));
  }
  add(...names) {
    const next = this.set;
    for (const name of names) next.add(name);
    this.node.className = Array.from(next).join(" ");
  }
  remove(...names) {
    const next = this.set;
    for (const name of names) next.delete(name);
    this.node.className = Array.from(next).join(" ");
  }
  contains(name) { return this.set.has(name); }
  toggle(name, on) { if (on) this.add(name); else this.remove(name); }
}

const docListeners = {};

class FakeNode {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase();
    this.children = [];
    this.className = "";
    this.textContent = "";
    this.dataset = {};
    this.attributes = {};
    this.listeners = {};
    this.hidden = false;
    this.disabled = false;
    this.value = "";
    this.checked = false;
    this.parentNode = null;
    this.classList = new FakeClassList(this);
    // viewer surface: transform styles, bitmap size and pointer capture
    this.style = {};
    this.clientWidth = 0;
    this.clientHeight = 0;
    this.captured = null;
  }
  append(...nodes) {
    for (const node of nodes) {
      if (node && typeof node === "object") node.parentNode = this;
      this.children.push(node);
    }
  }
  replaceChildren(...nodes) { this.children = []; this.append(...nodes); }
  insertAdjacentElement(position, node) {
    const parent = this.parentNode;
    if (!parent || position !== "afterend") return null;
    const at = parent.children.indexOf(this);
    parent.children.splice(at + 1, 0, node);
    node.parentNode = parent;
    return node;
  }
  contains(node) {
    if (node === this) return true;
    for (const child of this.children) {
      if (child && typeof child === "object" && child.contains && child.contains(node)) return true;
    }
    return false;
  }
  setAttribute(name, value) { this.attributes[name] = value; }
  removeAttribute(name) { this[name] = ""; }
  // Enough DOM for the viewer's "did this click land on a control?" test: only
  // the button selector is answered; every other selector walks up to null, so
  // the tag editor's ".chip"/".suggest-item" lookups behave exactly as before.
  closest(selector) {
    if (selector === "button" && this.tagName === "BUTTON") return this;
    return this.parentNode && typeof this.parentNode.closest === "function"
      ? this.parentNode.closest(selector)
      : null;
  }
  // A fixed 800x600 stage: the viewer's zoom anchor is derived from this rect.
  getBoundingClientRect() { return { left: 0, top: 0, right: 800, bottom: 600, width: 800, height: 600 }; }
  setPointerCapture(id) { this.captured = id; }
  releasePointerCapture(id) { if (this.captured === id) this.captured = null; }
  focus() { globalThis.document.activeElement = this; }
  addEventListener(type, handler) { (this.listeners[type] ||= []).push(handler); }
  removeEventListener(type, handler) {
    this.listeners[type] = (this.listeners[type] || []).filter((item) => item !== handler);
  }
  fire(type, event) { for (const handler of this.listeners[type] || []) handler(event || {}); }
}

const body = new FakeNode("body");
globalThis.document = {
  body,
  activeElement: null,
  createElement: (tag) => new FakeNode(tag),
  createTextNode: (text) => ({ nodeType: 3, textContent: text }),
  addEventListener: (type, handler) => { (docListeners[type] ||= []).push(handler); },
  removeEventListener: (type, handler) => {
    docListeners[type] = (docListeners[type] || []).filter((item) => item !== handler);
  },
};
globalThis.window = { setTimeout, clearTimeout };

const byClass = (root, name, out = []) => {
  if (root.classList && root.classList.contains(name)) out.push(root);
  for (const child of root.children || []) {
    if (child && typeof child === "object" && child.classList) byClass(child, name, out);
  }
  return out;
};
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

const zoomMod = await import(pathToFileURL(WEB + "/js/zoom.js").href);
const apiMod = await import(pathToFileURL(WEB + "/js/api.js").href);

const TAGS = {
  "/a/one.png": ["1girl", "solo"],
  "/a/two.png": ["1girl", "smile"],
};
const asked = [];
apiMod.api.getCaption = async (path) => {
  asked.push(path);
  return {
    image: path,
    caption_path: path.replace(/\.png$/, ".txt"),
    exists: true,
    text: (TAGS[path] || []).join(", "),
    tags: (TAGS[path] || []).slice(),
    multiline_warning: false,
  };
};

const events = [];
const zoom = zoomMod.createZoom({
  notify: (message) => events.push("notify:" + message),
  onOpen: () => { events.push("open"); },
  onClose: (path) => { events.push("close:" + path); },
  onSaved: () => { events.push("saved"); },
});

const backdrop = byClass(body, "zoom-backdrop")[0];
if (!backdrop) throw new Error("createZoom did not build a backdrop");
if (!backdrop.hidden) throw new Error("the enlargement must start hidden");

await zoom.open("/a/one.png", ["/a/one.png", "/a/two.png"]);
if (backdrop.hidden) throw new Error("open() did not show the overlay");
if (events[0] !== "open") throw new Error("onOpen was not called before loading");

const image = byClass(backdrop, "zoom-img")[0];
const expected = apiMod.thumbUrl("/a/one.png", apiMod.THUMB_SIZE.PREVIEW);
if (image.src !== expected) throw new Error("image src " + image.src + " expected " + expected);
if (image.src.indexOf("size=preview") < 0) throw new Error("the enlargement must use the preview thumbnail");

const chips = byClass(backdrop, "zoom-chips")[0];
const chipTexts = () => chips.children.filter((n) => n.classList && n.classList.contains("chip"))
  .map((chip) => chip.children[0].textContent);
if (JSON.stringify(chipTexts()) !== JSON.stringify(["1girl", "solo"])) {
  throw new Error("the editor did not load the caption tags: " + JSON.stringify(chipTexts()));
}

// the editor got its own elements: the sidebar keeps none of this state
const tagColumn = byClass(backdrop, "zoom-tags")[0];
if (!tagColumn) throw new Error("no side-by-side tag column");
if (!byClass(tagColumn, "zoom-input")[0]) throw new Error("no tag input in the enlarged view");
if (!byClass(tagColumn, "zoom-raw")[0]) throw new Error("no raw-text toggle in the enlarged view");

// paging lives on the picture: the arrows are children of the stage (a gallery
// overlay), NOT header buttons, and their accessible name is the user copy
const stage = byClass(backdrop, "zoom-stage")[0];
if (!stage) throw new Error("no image stage");
const head = byClass(backdrop, "zoom-head")[0];
if (!head) throw new Error("no dialog header");
if (byClass(head, "zoom-nav").length) throw new Error("paging arrows must not stay in the header");
const prevArrow = byClass(stage, "zoom-nav-prev")[0];
const nextArrow = byClass(stage, "zoom-nav-next")[0];
if (!prevArrow || !nextArrow) throw new Error("the stage has no side paging arrows");
if (prevArrow.attributes["aria-label"] !== "上一张") throw new Error("prev arrow lost its accessible name");
if (nextArrow.attributes["aria-label"] !== "下一张") throw new Error("next arrow lost its accessible name");

// the bitmap is an image viewer: the wheel zooms around the cursor, drag pans
image.clientWidth = 1600;
image.clientHeight = 1200;
const wheelHandlers = stage.listeners.wheel || [];
if (!wheelHandlers.length) throw new Error("no wheel handler on the stage");
wheelHandlers[0]({ deltaY: -100, clientX: 400, clientY: 300, preventDefault() {} });
if (image.style.transform.indexOf("scale(") < 0) {
  throw new Error("the wheel did not zoom: " + JSON.stringify(image.style.transform));
}
if (image.style.transform.indexOf("translate(0px, 0px)") !== 0) {
  throw new Error("zooming at the stage centre should not pan: " + image.style.transform);
}
if (!stage.classList.contains("is-zoomed")) throw new Error("the is-zoomed state class is missing");

stage.fire("pointerdown", { pointerId: 1, button: 0, clientX: 400, clientY: 300, target: stage, preventDefault() {} });
stage.fire("pointermove", { pointerId: 1, clientX: 450, clientY: 320, preventDefault() {} });
if (image.style.transform.indexOf("translate(50px, 20px)") !== 0) {
  throw new Error("the drag did not pan: " + image.style.transform);
}
if (!stage.classList.contains("is-panning")) throw new Error("the is-panning state class is missing");
stage.fire("pointerup", { pointerId: 1, clientX: 450, clientY: 320 });
if (stage.classList.contains("is-panning")) throw new Error("is-panning outlived the gesture");

// double-click toggles back to fit
stage.fire("dblclick", { clientX: 400, clientY: 300, preventDefault() {} });
if (image.style.transform !== "") throw new Error("double-click did not reset the view");
if (stage.classList.contains("is-zoomed")) throw new Error("the is-zoomed state outlived the reset");

// BUT a double-click that lands on a paging arrow is "next, next" - the arrows
// are inside the stage, so their dblclick bubbles up and used to zoom
stage.fire("dblclick", { clientX: 400, clientY: 300, target: nextArrow, preventDefault() {} });
if (image.style.transform !== "") {
  throw new Error("a double-click on a paging arrow zoomed the picture");
}

// keyboard zoom runs through the real key handler (target must not be the input)
const keyTarget = byClass(backdrop, "zoom-dialog")[0];
for (const handler of docListeners.keydown) handler({ key: "+", preventDefault() {}, target: keyTarget });
if (image.style.transform.indexOf("scale(") < 0) throw new Error("'+' did not zoom");
for (const handler of docListeners.keydown) handler({ key: "0", preventDefault() {}, target: keyTarget });
if (image.style.transform !== "") throw new Error("'0' did not reset the view");

// paging reloads the other image in place
wheelHandlers[0]({ deltaY: -100, clientX: 500, clientY: 300, preventDefault() {} });
if (image.style.transform === "") throw new Error("the second wheel zoom did nothing");
nextArrow.fire("click");
await tick(); await tick();
if (asked[asked.length - 1] !== "/a/two.png") throw new Error("next did not load the next image");
if (JSON.stringify(chipTexts()) !== JSON.stringify(["1girl", "smile"])) {
  throw new Error("paging did not reload the tags: " + JSON.stringify(chipTexts()));
}
if (image.src !== apiMod.thumbUrl("/a/two.png", apiMod.THUMB_SIZE.PREVIEW)) {
  throw new Error("paging did not change the image");
}
if (image.style.transform !== "") {
  throw new Error("a new picture inherited the old zoom/pan: " + image.style.transform);
}

// arrow keys page - but the tag input keeps them, otherwise typing breaks
if (!(docListeners.keydown || []).length) throw new Error("no document key handler while open");
const dialog = byClass(backdrop, "zoom-dialog")[0];
const input = byClass(backdrop, "zoom-input")[0];
for (const handler of docListeners.keydown) handler({ key: "ArrowLeft", preventDefault() {}, target: dialog });
await tick(); await tick();
if (asked[asked.length - 1] !== "/a/one.png") throw new Error("ArrowLeft did not page back");
for (const handler of docListeners.keydown) handler({ key: "ArrowRight", preventDefault() {}, target: input });
await tick(); await tick();
if (asked[asked.length - 1] !== "/a/one.png") {
  throw new Error("an arrow key was stolen from the tag input");
}

// Escape closes and hands the last image to the sidebar
for (const handler of docListeners.keydown) handler({ key: "Escape", preventDefault() {}, target: null });
await tick(); await tick();
if (!backdrop.hidden) throw new Error("Escape did not close the overlay");
if (events[events.length - 1] !== "close:/a/one.png") {
  throw new Error("onClose did not get the last image: " + JSON.stringify(events));
}
if (image.src) throw new Error("the enlarged bitmap was not released on close");
if ((docListeners.keydown || []).length) throw new Error("the key handler outlived the overlay");

// a lone image has nothing to page to: the arrows must disappear, not just sit
// there disabled where they would draw the eye for nothing
await zoom.open("/a/one.png", ["/a/one.png"]);
const lonePrev = byClass(backdrop, "zoom-nav-prev")[0];
const loneNext = byClass(backdrop, "zoom-nav-next")[0];
if (!lonePrev.hidden || !loneNext.hidden) throw new Error("paging arrows must be hidden for a single image");
await zoom.close();

console.log("zoom harness OK " + JSON.stringify({ asked }));
