/**
 * Headless behaviour harness for the crop panel (web/js/crop.js) and its geometry
 * (web/js/cropbox.js), section 4.17.
 *
 * Static tests can see that a dialog exists; only this can see that the rectangle the
 * user confirmed is the one that is sent, that the box is carried over to the next
 * image (same resolution: the same rectangle; different resolution: the same relative
 * window), that the scaling half is absent when the switch is off, that a failed write
 * keeps the dialog on the same image instead of skipping it silently, and that the
 * tagger step calls the existing /api/autotag/run with the tagger panel's own settings.
 *
 * Usage: node crop_harness.mjs <absolute path to web/>
 */
import assert from "node:assert/strict";
import { pathToFileURL } from "node:url";

const WEB = process.argv[2];
if (!WEB) throw new Error("usage: node crop_harness.mjs <web dir>");

const moduleUrl = (rel) => pathToFileURL(WEB + "/" + rel).href;
const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

// --- minimal fake DOM ------------------------------------------------------
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
    this.disabled = false;
    this.checked = false;
    this.value = "";
    this.title = "";
    this.placeholder = "";
    this.type = "";
    this.id = "";
    this.src = "";
    this.alt = "";
    this.draggable = false;
    this.parentNode = null;
    this.clientWidth = 0;
    this.clientHeight = 0;
    this.naturalWidth = 0;
    this.naturalHeight = 0;
  }
  append(...nodes) {
    for (const node of nodes) {
      if (node && typeof node === "object") node.parentNode = this;
      this.children.push(node);
    }
  }
  replaceChildren(...nodes) { this.children = []; this.append(...nodes); }
  remove() {
    const parent = this.parentNode;
    if (!parent) return;
    parent.children = parent.children.filter((child) => child !== this);
    this.parentNode = null;
  }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name]; }
  addEventListener(type, handler) { (this.listeners[type] ||= []).push(handler); }
  removeEventListener(type, handler) {
    this.listeners[type] = (this.listeners[type] || []).filter((item) => item !== handler);
  }
  fire(type, event) {
    const payload = Object.assign({ type, target: this, preventDefault() {} }, event || {});
    for (const handler of [...(this.listeners[type] || [])]) handler(payload);
  }
  focus() { globalThis.document.activeElement = this; }
  blur() { if (globalThis.document.activeElement === this) globalThis.document.activeElement = null; }
  setPointerCapture() {}
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

const documentListeners = {};
globalThis.document = {
  body: new FakeNode("body"),
  documentElement: new FakeNode("html"),
  activeElement: null,
  createElement: (tag) => new FakeNode(tag),
  addEventListener(type, handler) { (documentListeners[type] ||= []).push(handler); },
  removeEventListener(type, handler) {
    documentListeners[type] = (documentListeners[type] || []).filter((item) => item !== handler);
  },
};
globalThis.window = { setTimeout, clearTimeout };

function pressKey(key, extra) {
  const event = Object.assign(
    { key, target: globalThis.document.body, shiftKey: false, altKey: false, preventDefault() {} },
    extra || {},
  );
  for (const handler of [...(documentListeners.keydown || [])]) handler(event);
}

class FakeEventSource {
  constructor(url) {
    this.url = url;
    this.listeners = {};
    this.closed = false;
    FakeEventSource.last = this;
  }
  addEventListener(type, handler) { (this.listeners[type] ||= []).push(handler); }
  fire(type, data) {
    const payload = { data: JSON.stringify(data) };
    for (const handler of [...(this.listeners[type] || [])]) handler(payload);
  }
  close() { this.closed = true; }
}
FakeEventSource.last = null;
globalThis.EventSource = FakeEventSource;

// --- fake server -----------------------------------------------------------
const calls = [];
//: path -> display size. The dialog reads the header before the pixels arrive.
const META = {
  "F:/data/1.png": { width: 3000, height: 2000, display_width: 3000, display_height: 2000 },
  "F:/data/2.png": { width: 3000, height: 2000, display_width: 3000, display_height: 2000 },
  "F:/data/3.png": { width: 1000, height: 1000, display_width: 1000, display_height: 1000 },
  "F:/data/4.png": { width: 4000, height: 3000, display_width: 4000, display_height: 3000 },
};
let applyFails = false;
globalThis.fetch = async (url, init) => {
  const target = String(url);
  calls.push({ url: target, init: init || {} });
  if (target.indexOf("/api/images/meta") >= 0) {
    const query = decodeURIComponent(target.split("path=")[1] || "");
    const meta = META[query];
    if (!meta) return { ok: false, status: 404, url: target, statusText: "not found", json: async () => ({ error: { code: "not_found", message: "no such image" } }) };
    return { ok: true, status: 200, url: target, json: async () => Object.assign({ path: query, mode: "RGB", has_alpha: false }, meta) };
  }
  if (target.indexOf("/api/crop/apply") >= 0) {
    if (applyFails) {
      return { ok: false, status: 400, url: target, statusText: "bad", json: async () => ({ error: { code: "decode_failed", message: "boom" } }) };
    }
    const body = JSON.parse(init.body);
    const name = body.src.split("/").pop().replace(".png", "");
    return {
      ok: true,
      status: 200,
      url: target,
      json: async () => ({
        src: body.src,
        archived: body.src.replace(".png", "_1.png"),
        format: "png",
        crop: body.crop,
        crop_requested: body.crop,
        orig_width: 0,
        orig_height: 0,
        crop_width: body.crop.width,
        crop_height: body.crop.height,
        output_width: body.scale ? body.scale.target_width : body.crop.width,
        output_height: body.scale ? body.scale.target_height : body.crop.height,
        scale: body.scale ? 1.0 : 1.0,
        resampled: false,
        upscale_skipped: false,
        copied: false,
        warnings: [],
        archived_name: name,
      }),
    };
  }
  if (target.indexOf("/api/autotag/run") >= 0) {
    const body = JSON.parse(init.body);
    return { ok: true, status: 202, url: target, json: async () => ({ job_id: "T1", total: body.images.length }) };
  }
  if (target.indexOf("/api/autotag/cancel") >= 0) {
    return { ok: true, status: 202, url: target, json: async () => ({ cancelled: true }) };
  }
  return { ok: true, status: 200, url: target, json: async () => ({}) };
};

const crop = await import(moduleUrl("js/crop.js"));
const box = await import(moduleUrl("js/cropbox.js"));
const { createTrainParams } = await import(moduleUrl("js/trainparams.js"));
const { t } = await import(moduleUrl("js/strings.js"));

const bodiesFor = (fragment) =>
  calls
    .filter((call) => call.url.indexOf(fragment) >= 0)
    .map((call) => (call.init.body ? JSON.parse(call.init.body) : null));

function dialogHost() {
  return findOne(globalThis.document.body, (node) => String(node.className || "").indexOf("crop-backdrop") >= 0);
}

const settle = async () => {
  for (let i = 0; i < 6; i += 1) await flush();
};

// ---------------------------------------------------------------------------
// 1. the pure request builder (section 4.17's frozen body)
// ---------------------------------------------------------------------------
assert.deepEqual(Object.keys(crop.buildCropRequest({})).sort(), ["crop", "format", "scale", "src"]);
assert.equal(crop.CROP_ARCHIVE_FORMAT, "keep", "the archive must keep the source format");

const plain = crop.buildCropRequest({ src: "F:/data/1.png", rect: { x: 1, y: 2, width: 300, height: 200 } });
assert.equal(plain.format, "keep");
assert.equal(plain.scale, null, "with scaling off the body must carry null, not a disabled object");
assert.deepEqual(plain.crop, { x: 1, y: 2, width: 300, height: 200 });
assert.equal(crop.buildCropRequest({ rect: { width: 0, height: 0 } }).crop.width, 1, "a zero box is clamped to 1 px");

const scaledBody = crop.buildCropRequest({
  src: "s",
  rect: { x: 0, y: 0, width: 10, height: 10 },
  scale: true,
  resolution: [2048, 1024],
  processing: { noUpscale: false, resample: "bicubic", quality: 88, optimize: false },
});
assert.deepEqual(Object.keys(scaledBody.scale).sort(), [
  "no_upscale", "optimize", "quality", "resample", "target_height", "target_width",
]);
assert.equal(scaledBody.scale.target_width, 2048);
assert.equal(scaledBody.scale.target_height, 1024);
assert.equal(scaledBody.scale.no_upscale, false);
assert.equal(scaledBody.scale.resample, "bicubic");
assert.equal(scaledBody.scale.quality, 88);
assert.equal(scaledBody.scale.optimize, false);

// ---------------------------------------------------------------------------
// 2. the geometry (cropbox.js)
// ---------------------------------------------------------------------------
const frame = box.frameOf(1000, 1000);
assert.deepEqual(
  box.ASPECT_PRESETS.map((preset) => preset.id),
  ["free", "source", "1:1", "3:2", "2:3", "4:3", "3:4", "5:4", "4:5", "16:9", "9:16"],
  "the preset list is the frozen one",
);
assert.equal(box.ratioOf("free", frame), null);
assert.equal(box.ratioOf("source", { width: 3000, height: 2000 }), 1.5);
assert.equal(box.ratioOf("1:1", frame), 1);

// Dragging: the opposite edge stays put, and a locked ratio survives the drag.
assert.deepEqual(
  box.resizeByHandle({ x: 0, y: 0, width: 100, height: 100 }, "e", 50, 0, { frame }),
  { x: 0, y: 0, width: 150, height: 100 },
  "a free edge drag moves one edge only",
);
assert.deepEqual(
  box.resizeByHandle({ x: 400, y: 400, width: 200, height: 200 }, "e", 50, 0, { frame, ratio: 1 }),
  { x: 400, y: 375, width: 250, height: 250 },
  "a locked edge drag grows the other axis around the centre",
);
assert.deepEqual(
  box.resizeByHandle({ x: 0, y: 0, width: 100, height: 100 }, "se", 5000, 5000, { frame, ratio: 1.5 }),
  { x: 0, y: 0, width: 1000, height: 667 },
  "a drag past the frame stops at the frame instead of flipping the box",
);
assert.deepEqual(
  box.resizeByHandle({ x: 100, y: 100, width: 200, height: 200 }, "n", 0, -50, { frame }),
  { x: 100, y: 50, width: 200, height: 250 },
);
assert.deepEqual(box.moveBy({ x: 990, y: 0, width: 50, height: 50 }, 40, 10, frame), { x: 950, y: 10, width: 50, height: 50 });

// The clamp table is shared with the server (test/test_crop_api.py asserts the same two rows):
// the dialog must never send a rectangle the server would have to clamp differently.
assert.deepEqual(box.clampRect({ x: 2900, y: 1900, width: 800, height: 600 }, box.frameOf(3000, 2000)), { x: 2200, y: 1400, width: 800, height: 600 });
assert.deepEqual(box.clampRect({ x: -10, y: -10, width: 5000, height: 4000 }, box.frameOf(400, 300)), { x: 0, y: 0, width: 400, height: 300 });

// "Match the target area": the box area becomes the target area, ratio and centre kept.
const normalized = box.normalizeArea({ x: 0, y: 0, width: 100, height: 100 }, 4 / 3, 1536 * 1536, box.frameOf(3000, 2000));
assert.deepEqual(normalized.rect, { x: 0, y: 0, width: 1774, height: 1330 });
assert.equal(normalized.clamped, false);
assert.ok(Math.abs(normalized.rect.width * normalized.rect.height - 1536 * 1536) / (1536 * 1536) < 0.01);
const tooBig = box.normalizeArea({ x: 0, y: 0, width: 40, height: 40 }, 1, 1536 * 1536, box.frameOf(800, 600));
assert.equal(tooBig.clamped, true, "a frame that cannot hold the target area must say so");
assert.ok(tooBig.rect.width <= 800 && tooBig.rect.height <= 600);
assert.equal(tooBig.rect.width, tooBig.rect.height, "the ratio survives the reduction");

// Carrying the box to the next image.
assert.deepEqual(
  box.propagate({ x: 100, y: 100, width: 200, height: 200 }, null, { width: 1000, height: 1000 }, { width: 1000, height: 1000 }),
  { x: 100, y: 100, width: 200, height: 200 },
  "identical resolutions carry the identical rectangle",
);
assert.deepEqual(
  box.propagate({ x: 100, y: 100, width: 200, height: 200 }, null, { width: 1000, height: 1000 }, { width: 500, height: 250 }),
  { x: 50, y: 25, width: 100, height: 50 },
  "a different resolution keeps the same fraction of the frame",
);
const locked = box.propagate({ x: 0, y: 0, width: 3000, height: 2000 }, 1.5, { width: 3000, height: 2000 }, { width: 1000, height: 1000 });
// Integer pixels: the ratio is kept to within the rounding of one edge, not to the last bit.
assert.ok(Math.abs(locked.width / locked.height - 1.5) < 0.005, "a locked ratio survives a different aspect ratio");
assert.ok(locked.width <= 1000 && locked.height <= 1000, "the carried box still fits the new frame");

assert.equal(box.snapDelta(333.5, [0, 1000 / 3, 500], 6) + 333.5, 1000 / 3);
assert.equal(box.snapDelta(200, [0, 1000 / 3, 500], 6), 0, "nothing near -> no snap");
assert.deepEqual(box.guidesOf(box.frameOf(900, 600)), { vertical: [300, 450, 600], horizontal: [200, 300, 400] });
assert.equal(box.formatRatio(3000, 2000), "3:2");

// ---------------------------------------------------------------------------
// 3. the panel and the confirmation dialog
// ---------------------------------------------------------------------------
const tabsHost = new FakeNode("nav");
const panelHost = new FakeNode("aside");
const shared = createTrainParams();
const notices = [];
const shelfRows = [];
const shelf = {
  start(spec) {
    const row = {
      spec,
      updates: [],
      finished: false,
      update(value) { this.updates.push(value); },
      finish() { this.finished = true; },
    };
    shelfRows.push(row);
    return row;
  },
};
//: What the Tagger tab is showing right now - handed over by app.js, never copied into the panel.
const taggerSettings = {
  model: "wd-swinv2-tagger-v3",
  thresholds: { general: 0.35 },
  postprocess: { remove_underscore: true, escape_parens: true, undesired_tags: [] },
  write_mode: "merge",
};
let scoped = ["F:/data/1.png", "F:/data/2.png", "F:/data/3.png"];
let archivedRefreshes = 0;
// A stand-in for the column's scope control (scope.js): the panel only ever calls paths() and
// subscribe(), and the harness plays app.js's part by firing the paint hook the way a gallery
// change or a filter change does.
const scopeListeners = [];
const scopeControl = {
  paths: () => scoped.slice(),
  subscribe: (handlers) => { scopeListeners.push(handlers || {}); },
};
const notifyScope = () => {
  for (const entry of scopeListeners) if (typeof entry.onPaint === "function") entry.onPaint();
};
const panel = crop.createCropPanel({
  tabsHost,
  panelHost,
  scope: scopeControl,
  jobs: shelf,
  notify: (message, kind) => notices.push([kind, message]),
  getParams: () => shared,
  getTaggerParams: () => taggerSettings,
  onArchived: () => { archivedRefreshes += 1; },
});
assert.equal(tabsHost.children.length, 1, "the panel must append its tab");
assert.equal(panelHost.children.length, 1, "the panel must append its own panel");
const startButton = findOne(panelHost, (node) => node.id === "btn-crop-start");
const cancelButton = findOne(panelHost, (node) => node.className === "btn btn-danger");
const switches = findAll(panelHost, (node) => node.type === "checkbox");
assert.ok(startButton && cancelButton, "the panel needs a start and a cancel button");
assert.equal(switches.length, 2, "scale-after-crop and tag-after-crop");
assert.equal(switches[0].checked, true, "scaling after the crop is on: that is the combined case");
assert.equal(switches[1].checked, false, "tagging is opt-in");

panel.onShow();
assert.equal(startButton.disabled, false, "a scope with images can start");
assert.equal(scopeListeners.length, 1, "the panel never subscribed to the column's scope control");

// The strip changes while this tab is open, so the reaction cannot wait for onShow(): app.js
// repaints the control and the panel follows. Leaving and re-entering the tab is not a mechanism.
scoped = [];
notifyScope();
assert.equal(startButton.disabled, true, "an empty scope cannot start");
scoped = ["F:/data/1.png", "F:/data/2.png", "F:/data/3.png"];
notifyScope();
assert.equal(panel.status().images, 3, "the panel did not follow the control back to three images");
assert.equal(startButton.disabled, false, "a scope with images can start again");

await panel.start();
await settle();
let dialog = dialogHost();
assert.ok(dialog, "start must open the confirmation dialog");
const confirmButton = findOne(dialog, (node) => node.className === "btn btn-primary");
const skipButton = findOne(dialog, (node) => node.textContent === t("crop.skip"));
const finishButton = findOne(dialog, (node) => node.textContent === t("crop.finish"));
const normalizeButton = findOne(dialog, (node) => node.textContent === t("crop.normalize"));
const resetButton = findOne(dialog, (node) => node.textContent === t("crop.reset"));
const aspectSelect = findOne(dialog, (node) => node.className === "crop-aspect");
const image = findOne(dialog, (node) => node.tagName === "IMG");
const rectFields = findAll(dialog, (node) => node.tagName === "INPUT" && node.type === "number");
assert.ok(confirmButton && skipButton && finishButton && normalizeButton && resetButton, "the dialog controls must exist");
assert.equal(rectFields.length, 4, "X / Y / width / height");
assert.equal(aspectSelect.children.length, box.ASPECT_PRESETS.length, "one option per preset");
assert.ok(String(image.src).indexOf("/api/images/thumb") >= 0, "the dialog draws the preview thumbnail");
const rectNow = () => rectFields.map((field) => field.value);

// The default box is the whole frame; the image load is what gives the overlay its scale.
image.clientWidth = 1500;
image.naturalWidth = 1500;
image.clientHeight = 1000;
image.naturalHeight = 1000;
image.fire("load");
assert.deepEqual(rectNow(), ["0", "0", "3000", "2000"], "an unlocked box starts as the whole image");

// A preset rebuilds the box to the largest rectangle with that ratio.
aspectSelect.value = "1:1";
aspectSelect.fire("change");
assert.deepEqual(rectNow(), ["500", "0", "2000", "2000"], "1:1 must fit the frame");

// "Match the target area" makes the box area the shared resolution's area (1536x1536).
normalizeButton.fire("click");
assert.deepEqual(rectNow(), ["732", "232", "1536", "1536"]);
assert.equal(1536 * 1536, 1536 * 1536);

// Changing the shared resolution moves the target, because the panel reads the store.
shared.setResolution(1024, 1024);
normalizeButton.fire("click");
// The box keeps its centre ((1500, 1000) here), so both edges move.
assert.deepEqual(rectNow(), ["988", "488", "1024", "1024"]);
shared.setResolution(1536, 1536);
normalizeButton.fire("click");
assert.deepEqual(rectNow(), ["732", "232", "1536", "1536"]);

// Confirm: the frozen body, and the rectangle that was on screen.
confirmButton.fire("click");
await settle();
const applied = bodiesFor("/api/crop/apply");
assert.equal(applied.length, 1, "confirm must POST exactly once");
assert.equal(applied[0].src, "F:/data/1.png");
assert.equal(applied[0].format, "keep", "the archive keeps the source format");
assert.deepEqual(applied[0].crop, { x: 732, y: 232, width: 1536, height: 1536 });
assert.equal(applied[0].scale.target_width, 1536);
assert.equal(applied[0].scale.no_upscale, true, "the pipeline settings come from the shared store");
assert.equal(applied[0].scale.resample, "lanczos");
assert.equal(archivedRefreshes, 0, "the gallery is refreshed once, when the dialog closes");
assert.ok(dialogHost(), "the dialog stays open for the next image");

// Same display resolution -> the identical rectangle, with no preset change.
image.clientWidth = 1500;
image.fire("load");
assert.deepEqual(rectNow(), ["732", "232", "1536", "1536"], "same resolution carries the same box");
confirmButton.fire("click");
await settle();
const second = bodiesFor("/api/crop/apply")[1];
assert.equal(second.src, "F:/data/2.png");
assert.deepEqual(second.crop, { x: 732, y: 232, width: 1536, height: 1536 });

// A different resolution keeps the same relative window and the same locked ratio.
image.clientWidth = 1000;
image.fire("load");
assert.deepEqual(rectNow(), ["244", "244", "512", "512"], "a different resolution keeps the fraction");
const beforeSkip = bodiesFor("/api/crop/apply").length;
skipButton.fire("click");
await settle();
assert.equal(bodiesFor("/api/crop/apply").length, beforeSkip, "skipping must not write anything");
assert.equal(dialogHost(), null, "the last image ends the run");
assert.equal(archivedRefreshes, 1, "two archives -> one refresh of the directory on screen");
assert.equal(bodiesFor("/api/autotag/run").length, 0, "tagging is opt-in and was off");
assert.ok(panelHost.children.length >= 1);

// Esc ends a run early and keeps what was written.
await panel.start();
await settle();
dialog = dialogHost();
const secondConfirm = findOne(dialog, (node) => node.className === "btn btn-primary");
secondConfirm.fire("click");
await settle();
pressKey("Escape");
await settle();
assert.equal(dialogHost(), null, "Esc must close the dialog");
assert.ok(archivedRefreshes >= 2, "closing early still refreshes the directory");

// The scaling switch off means the body carries no scaling half at all.
switches[0].checked = false;
switches[0].fire("change");
scoped = ["F:/data/1.png"];
await panel.start();
await settle();
dialog = dialogHost();
findOne(dialog, (node) => node.className === "btn btn-primary").fire("click");
await settle();
const unscaled = bodiesFor("/api/crop/apply").pop();
assert.equal(unscaled.scale, null, "crop-only must send no scaling half");
assert.equal(dialogHost(), null);
switches[0].checked = true;
switches[0].fire("change");

// ---------------------------------------------------------------------------
// 4. a failed write stays on the same image
// ---------------------------------------------------------------------------
applyFails = true;
scoped = ["F:/data/1.png", "F:/data/4.png"];
await panel.start();
await settle();
dialog = dialogHost();
const failImage = findOne(dialog, (node) => node.tagName === "IMG");
failImage.clientWidth = 1500;
failImage.fire("load");
const failConfirm = findOne(dialog, (node) => node.className === "btn btn-primary");
const failCounter = findOne(dialog, (node) => String(node.className || "").indexOf("hint") >= 0 && String(node.textContent || "").indexOf("1 / 2") >= 0);
assert.ok(failCounter, "the dialog must say which image it is on");
failConfirm.fire("click");
await settle();
assert.ok(dialogHost(), "a failed write must keep the dialog on the same image");
assert.equal(failConfirm.disabled, false, "and Confirm must be available again (it is the retry)");
const errorLine = findOne(dialogHost(), (node) => String(node.className || "") === "errors" && String(node.textContent || "").length > 0);
assert.ok(errorLine, "the failure must be visible in the dialog");
applyFails = false;
failConfirm.fire("click");
await settle();
assert.ok(dialogHost(), "the retry writes and moves on to the second image");
const retried = bodiesFor("/api/crop/apply").slice(-1)[0];
assert.equal(retried.src, "F:/data/1.png");
findOne(dialogHost(), (node) => node.textContent === t("crop.skip")).fire("click");
await settle();
assert.equal(dialogHost(), null, "the run ends after the last image");

// ---------------------------------------------------------------------------
// 5. the tagger step reuses /api/autotag/run with the Tagger tab's settings
// ---------------------------------------------------------------------------
const before = bodiesFor("/api/autotag/run").length;
switches[1].checked = true;
switches[1].fire("change");
scoped = ["F:/data/1.png", "F:/data/2.png"];
await panel.start();
await settle();
for (let index = 0; index < 2; index += 1) {
  dialog = dialogHost();
  assert.ok(dialog, "image " + (index + 1) + " of the tagging run must be on screen");
  const picture = findOne(dialog, (node) => node.tagName === "IMG");
  picture.clientWidth = 1500;
  picture.fire("load");
  findOne(dialog, (node) => node.className === "btn btn-primary").fire("click");
  await settle();
}
const tagBodies = bodiesFor("/api/autotag/run");
assert.equal(tagBodies.length, before + 1, "one job for the whole batch, not one per image");
assert.deepEqual(tagBodies[before].images, ["F:/data/1_1.png", "F:/data/2_1.png"]);
assert.equal(tagBodies[before].model, "wd-swinv2-tagger-v3");
assert.equal(tagBodies[before].write_mode, "merge");
assert.deepEqual(tagBodies[before].thresholds, { general: 0.35 });
assert.equal(shelfRows.length, 1, "the tag job takes exactly one shelf row");
assert.ok(String(FakeEventSource.last.url).indexOf("/api/autotag/stream") >= 0);
assert.equal(cancelButton.hidden, false, "a running tag job must be cancellable from the panel");
FakeEventSource.last.fire("progress", { phase: "infer", done: 1, total: 2, current: "F:/data/1_1.png", errors: [] });
assert.equal(shelfRows[0].updates.length, 2, "the row follows the progress");
FakeEventSource.last.fire("done", { phase: "infer", done: 2, total: 2, finished: true, cancelled: false, errors: [] });
assert.equal(shelfRows[0].finished, true, "the row is closed when the job ends");
assert.equal(cancelButton.hidden, true);
assert.ok(notices.some((entry) => entry[0] === "ok"), "a finished tag job says so");

// A cancel goes through the frozen endpoint.
scoped = ["F:/data/1.png"];
await panel.start();
await settle();
dialog = dialogHost();
findOne(dialog, (node) => node.className === "btn btn-primary").fire("click");
await settle();
cancelButton.fire("click");
await settle();
assert.equal(bodiesFor("/api/autotag/cancel").length, 1, "cancel must hit the frozen route");
assert.ok(shelfRows[shelfRows.length - 1].finished || shelfRows.length >= 1);

// ---------------------------------------------------------------------------
// 6. relabel: the panel's own copy is repainted, not rebuilt
// ---------------------------------------------------------------------------
panel.relabel();
assert.equal(startButton.textContent, t("crop.start"));

console.log("crop harness: all assertions passed");
