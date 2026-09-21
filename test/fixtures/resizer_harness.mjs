/**
 * Headless checks for web/js/resizer.js.
 *
 * The interesting part of the resizer is arithmetic plus state, not pixels:
 * which way a drag moves a panel, where it clamps, what an arrow key does, and
 * what survives a reload. So this drives the real createResizers() through a
 * minimal fake DOM (elements, classList, style, pointer capture, storage) and
 * checks the painted --left-w / --right-w and the persisted JSON.
 *
 * Usage: node resizer_harness.mjs <absolute path to web/>
 */
import { pathToFileURL } from "node:url";

const WEB = process.argv[2];
if (!WEB) throw new Error("usage: node resizer_harness.mjs <web dir>");

class FakeClassList {
  constructor() { this.items = new Set(); }
  add(...names) { for (const name of names) this.items.add(name); }
  remove(...names) { for (const name of names) this.items.delete(name); }
  contains(name) { return this.items.has(name); }
}

class FakeStyle {
  constructor() { this.props = {}; }
  setProperty(key, value) { this.props[key] = String(value); }
  getPropertyValue(key) { return this.props[key]; }
}

class FakeNode {
  constructor(width) {
    this.style = new FakeStyle();
    this.classList = new FakeClassList();
    this.attrs = {};
    this.listeners = {};
    this.captured = new Set();
    this.clientWidth = width || 0;
    this.width = width || 0;
  }
  addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); }
  removeEventListener(type, fn) {
    this.listeners[type] = (this.listeners[type] || []).filter((item) => item !== fn);
  }
  dispatch(type, event) { for (const fn of (this.listeners[type] || []).slice()) fn(event); }
  setAttribute(key, value) { this.attrs[key] = String(value); }
  getAttribute(key) { return this.attrs[key]; }
  setPointerCapture(id) { this.captured.add(id); }
  releasePointerCapture(id) { this.captured.delete(id); }
  hasPointerCapture(id) { return this.captured.has(id); }
  getBoundingClientRect() { return { width: this.width }; }
}

const resizeListeners = [];
globalThis.window = {
  addEventListener(type, fn) { if (type === "resize") resizeListeners.push(fn); },
};
globalThis.document = { body: new FakeNode(0) };

function makeStorage(initial) {
  const data = new Map(Object.entries(initial || {}));
  return {
    getItem: (key) => (data.has(key) ? data.get(key) : null),
    setItem: (key, value) => { data.set(key, String(value)); },
  };
}

const mod = await import(pathToFileURL(WEB + "/js/resizer.js").href);

function expect(actual, wanted, what) {
  if (JSON.stringify(actual) !== JSON.stringify(wanted)) {
    throw new Error(what + ": got " + JSON.stringify(actual) + " expected " + JSON.stringify(wanted));
  }
}

// --- pure arithmetic ------------------------------------------------------
expect(mod.clampWidth("left", 100), 200, "clamp below min");
expect(mod.clampWidth("left", 9999), 560, "clamp above max");
expect(mod.clampWidth("left", 300, 260), 260, "clamp against the room left");
expect(mod.clampWidth("left", 300, 100), 200, "a tiny room must never go under min");
expect(mod.clampWidth("left", "abc"), null, "garbage clamps to null");
expect(mod.clampWidth("nope", 300), null, "unknown side clamps to null");

expect(mod.dragWidth("left", 300, 40), 340, "dragging right widens the left panel");
expect(mod.dragWidth("right", 400, 40), 360, "dragging right narrows the right panel");

expect(mod.keyboardWidth("left", 300, "ArrowLeft"), 284, "left panel arrow-left");
expect(mod.keyboardWidth("left", 300, "ArrowRight"), 316, "left panel arrow-right");
expect(mod.keyboardWidth("right", 400, "ArrowLeft"), 416, "right panel arrow-left");
expect(mod.keyboardWidth("right", 400, "ArrowRight"), 384, "right panel arrow-right");
expect(mod.keyboardWidth("left", 300, "Enter"), null, "a foreign key is not ours");

expect(mod.parseStoredWidths('{"left":300,"right":9999}'), { left: 300, right: 680 }, "stored widths clamped");
expect(mod.parseStoredWidths("not json"), null, "corrupt blob ignored");
expect(mod.parseStoredWidths('{"left":"x"}'), null, "non numeric widths ignored");
expect(mod.parseStoredWidths(""), null, "empty blob ignored");
expect(mod.parseStoredWidths(mod.serializeWidths({ left: 300, right: 400 })), { left: 300, right: 400 }, "round trip");

// --- the factory against the fake DOM -------------------------------------
function boot(storage, layoutWidth) {
  const layout = new FakeNode(layoutWidth === undefined ? 1200 : layoutWidth);
  const leftHandle = new FakeNode(10);
  const rightHandle = new FakeNode(10);
  const resizers = mod.createResizers({
    layout,
    left: { handle: leftHandle, panel: new FakeNode(260) },
    right: { handle: rightHandle, panel: new FakeNode(380) },
    storage,
  });
  return { layout, leftHandle, rightHandle, resizers };
}

const store = makeStorage();
const env = boot(store);

expect(env.layout.style.props["--left-w"], "260px", "default left width painted");
expect(env.layout.style.props["--right-w"], "380px", "default right width painted");
expect(env.leftHandle.attrs["aria-valuenow"], "260", "aria-valuenow mirrors the width");
expect(env.leftHandle.attrs["aria-valuemin"], "200", "aria-valuemin is the side minimum");

// keyboard nudge on the right handle: ArrowLeft moves the separator left
env.rightHandle.dispatch("keydown", { key: "ArrowLeft", preventDefault() {} });
expect(env.layout.style.props["--right-w"], "396px", "arrow-left widened the right panel");
expect(JSON.parse(store.getItem(mod.RESIZER_STORAGE_KEY)), { left: 260, right: 396 }, "keyboard persisted");

// double click puts that one side back
env.rightHandle.dispatch("dblclick", {});
expect(env.layout.style.props["--right-w"], "380px", "double click reset the right panel");

// drag the left handle 60px to the right
env.leftHandle.dispatch("pointerdown", { button: 0, clientX: 500, pointerId: 1, preventDefault() {} });
expect(document.body.classList.contains("is-resizing"), true, "drag marks the body");
env.leftHandle.dispatch("pointermove", { clientX: 560 });
expect(env.layout.style.props["--left-w"], "320px", "drag widened the left panel");
env.leftHandle.dispatch("pointerup", { pointerId: 1 });
expect(document.body.classList.contains("is-resizing"), false, "drag cleared the body class");
expect(JSON.parse(store.getItem(mod.RESIZER_STORAGE_KEY)), { left: 320, right: 380 }, "drag persisted");

// a drag past the room left clamps at 1200 - 380 - 20 - 320 = 480
env.leftHandle.dispatch("pointerdown", { button: 0, clientX: 500, pointerId: 2, preventDefault() {} });
env.leftHandle.dispatch("pointermove", { clientX: 2500 });
env.leftHandle.dispatch("pointerup", { pointerId: 2 });
expect(env.layout.style.props["--left-w"], "480px", "drag clamped to leave the gallery its minimum");
expect(env.leftHandle.attrs["aria-valuemax"], "480", "aria-valuemax follows the room left");
expect(env.rightHandle.attrs["aria-valuemax"], "380", "the other side's maximum shrank too");

env.leftHandle.dispatch("dblclick", {});
expect(env.layout.style.props["--left-w"], "260px", "double click restored the left panel");

// stored widths are adopted on the next start, clamped to what fits
const env2 = boot(makeStorage({ [mod.RESIZER_STORAGE_KEY]: '{"left":9999,"right":300}' }), 1000);
expect(env2.layout.style.props["--left-w"], "360px", "stored width clamped to the room at load (1000-300-20-320)");
expect(env2.layout.style.props["--right-w"], "300px", "stored right width adopted");

// a corrupt blob falls back to the defaults instead of throwing
const env3 = boot(makeStorage({ [mod.RESIZER_STORAGE_KEY]: "{oops" }));
expect(env3.layout.style.props["--left-w"], "260px", "corrupt blob fell back to the default");

console.log("resizer harness OK " + JSON.stringify({
  left: env2.resizers.current().left,
  right: env2.resizers.current().right,
}));
