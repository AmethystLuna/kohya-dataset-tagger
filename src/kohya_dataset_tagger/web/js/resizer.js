/**
 * Draggable side panels (layout controls).
 *
 * The layout row is [left] [handle] [center] [handle] [right]. The center takes
 * what is left; each side panel is sized by the --left-w / --right-w custom
 * property set on the layout element, so one assignment moves both its
 * flex-basis and its width.
 *
 * Everything that needs a real pointer lives in createResizers. The arithmetic
 * (drag direction, clamping, the arrow-key step, the stored JSON) is exported
 * as pure functions so it can be judged without a layout engine - and the
 * factory itself is driven through a fake DOM in test/fixtures/resizer_harness.mjs.
 *
 * Widths are remembered in localStorage. A stored value that no longer fits is
 * clamped on load and again on window resize, instead of pushing the gallery
 * down to zero.
 */

//: The CSS defaults (see :root) are the same numbers; the module re-applies them
//: on start so the DOM cannot drift away from this table.
export const RESIZER_DEFAULTS = Object.freeze({ left: 260, right: 380 });

//: Hard limits per side. The effective maximum can be smaller when the other
//: side panel plus the minimum gallery width leave no room (see effectiveMax).
export const RESIZER_LIMITS = Object.freeze({
  left: Object.freeze({ min: 200, max: 560 }),
  right: Object.freeze({ min: 280, max: 680 }),
});

//: Arrow-key step, in CSS pixels.
export const RESIZER_STEP = 16;

//: The gallery keeps at least this much room; a drag may not eat into it.
export const CENTER_MIN_WIDTH = 320;

export const RESIZER_STORAGE_KEY = "kohya-dataset-tagger.layout";

const SIDES = ["left", "right"];

/** Clamp one width into its side's [min, max]. Garbage or a bad side -> null. */
export function clampWidth(side, value, max) {
  const limits = RESIZER_LIMITS[side];
  if (!limits) return null;
  const number = Math.round(Number(value));
  if (!Number.isFinite(number)) return null;
  const high = Number.isFinite(max) ? Math.min(limits.max, Number(max)) : limits.max;
  return Math.min(Math.max(number, limits.min), Math.max(limits.min, high));
}

/** Width a drag ends at, before clamping. deltaX grows to the right. */
export function dragWidth(side, startWidth, deltaX) {
  return side === "right" ? startWidth - deltaX : startWidth + deltaX;
}

/**
 * Width an arrow key ends at, before clamping; null when the key is not ours.
 * ArrowLeft always moves the separator itself to the left, so it widens the
 * right panel and narrows the left one.
 */
export function keyboardWidth(side, width, key, step = RESIZER_STEP) {
  if (key !== "ArrowLeft" && key !== "ArrowRight") return null;
  const left = key === "ArrowLeft";
  if (side === "right") return left ? width + step : width - step;
  return left ? width - step : width + step;
}

/** Parse the stored JSON defensively: a corrupt blob must not break the layout. */
export function parseStoredWidths(raw) {
  if (typeof raw !== "string" || raw.length === 0) return null;
  let data;
  try {
    data = JSON.parse(raw);
  } catch (err) {
    return null;
  }
  if (!data || typeof data !== "object") return null;
  const out = {};
  for (const side of SIDES) {
    const value = clampWidth(side, data[side]);
    if (value !== null) out[side] = value;
  }
  return Object.keys(out).length > 0 ? out : null;
}

export function serializeWidths(widths) {
  return JSON.stringify({ left: widths.left, right: widths.right });
}

function safeStorage() {
  try {
    return window.localStorage;
  } catch (err) {
    return null;
  }
}

function safeGet(storage, key) {
  try {
    return storage.getItem(key);
  } catch (err) {
    return null;
  }
}

/**
 * Wire the two drag handles.
 *
 * Options: { layout, left: {handle, panel}, right: {handle, panel}, storage? }
 * Returns { reset(side?), current() }. Missing handles are tolerated (one
 * renamed id must not kill the page); storage defaults to window.localStorage.
 */
export function createResizers(options) {
  const config = options || {};
  const layout = config.layout;
  const idle = { reset: () => {}, current: () => ({ ...RESIZER_DEFAULTS }) };
  if (!layout) return idle;

  const sides = SIDES
    .map((side) => {
      const entry = config[side];
      if (!entry || !entry.handle || !entry.panel) return null;
      return { side, handle: entry.handle, panel: entry.panel };
    })
    .filter((entry) => entry !== null);
  if (sides.length === 0) return idle;

  const storage = config.storage === undefined ? safeStorage() : config.storage;
  const widths = { ...RESIZER_DEFAULTS };
  const stored = storage ? parseStoredWidths(safeGet(storage, RESIZER_STORAGE_KEY)) : null;
  if (stored) Object.assign(widths, stored);

  // The two handles live in the same row, so their widths are part of the
  // budget a drag has to respect.
  const handleWidth = sides.reduce(
    (sum, entry) => sum + entry.handle.getBoundingClientRect().width,
    0
  );

  function otherWidth(side) {
    return widths[side === "left" ? "right" : "left"];
  }

  /** Largest width this side may take without squashing the gallery. */
  function effectiveMax(side) {
    const room = layout.clientWidth - otherWidth(side) - handleWidth - CENTER_MIN_WIDTH;
    return Math.max(RESIZER_LIMITS[side].min, room);
  }

  function paint(side) {
    const entry = sides.find((item) => item.side === side);
    if (!entry) return;
    layout.style.setProperty("--" + side + "-w", widths[side] + "px");
    entry.handle.setAttribute("aria-valuenow", String(widths[side]));
    entry.handle.setAttribute("aria-valuemin", String(RESIZER_LIMITS[side].min));
    entry.handle.setAttribute("aria-valuemax", String(Math.round(effectiveMax(side))));
  }

  function paintAll() {
    for (const entry of sides) paint(entry.side);
  }

  function setWidth(side, value) {
    const entry = sides.find((item) => item.side === side);
    if (!entry) return;
    const next = clampWidth(side, value, effectiveMax(side));
    if (next === null) return;
    widths[side] = next;
    paint(side);
    // The other side's aria-valuemax depends on this width, so repaint it too.
    const other = side === "left" ? "right" : "left";
    if (sides.some((item) => item.side === other)) paint(other);
  }

  function persist() {
    if (!storage) return;
    try {
      storage.setItem(RESIZER_STORAGE_KEY, serializeWidths(widths));
    } catch (err) {
      // private mode / quota: the width still applies for this session
    }
  }

  function reset(side) {
    if (side) {
      if (widths[side] === undefined) return;
      widths[side] = RESIZER_DEFAULTS[side];
      paint(side);
    } else {
      Object.assign(widths, RESIZER_DEFAULTS);
      paintAll();
    }
    persist();
  }

  function beginDrag(entry, event) {
    if (event.button !== undefined && event.button !== 0) return;
    if (event.preventDefault) event.preventDefault();
    const startX = event.clientX;
    const startWidth = widths[entry.side];
    try {
      entry.handle.setPointerCapture(event.pointerId);
    } catch (err) {
      // A synthetic pointer event has no active pointer to capture; the drag
      // still works as long as the pointer stays over the handle.
    }
    entry.handle.classList.add("is-dragging");
    document.body.classList.add("is-resizing");

    const onMove = (moveEvent) => {
      setWidth(entry.side, dragWidth(entry.side, startWidth, moveEvent.clientX - startX));
    };
    const onEnd = () => {
      entry.handle.removeEventListener("pointermove", onMove);
      entry.handle.removeEventListener("pointerup", onEnd);
      entry.handle.removeEventListener("pointercancel", onEnd);
      entry.handle.classList.remove("is-dragging");
      document.body.classList.remove("is-resizing");
      if (entry.handle.hasPointerCapture(event.pointerId)) {
        entry.handle.releasePointerCapture(event.pointerId);
      }
      persist();
    };
    entry.handle.addEventListener("pointermove", onMove);
    entry.handle.addEventListener("pointerup", onEnd);
    entry.handle.addEventListener("pointercancel", onEnd);
  }

  for (const entry of sides) {
    entry.handle.addEventListener("pointerdown", (event) => { beginDrag(entry, event); });
    entry.handle.addEventListener("keydown", (event) => {
      const next = keyboardWidth(entry.side, widths[entry.side], event.key);
      if (next === null) return;
      if (event.preventDefault) event.preventDefault();
      setWidth(entry.side, next);
      persist();
    });
    // Double click is the "put it back" gesture; Home does the same from the keyboard.
    entry.handle.addEventListener("dblclick", () => { reset(entry.side); });
    entry.handle.addEventListener("keydown", (event) => {
      if (event.key === "Home") {
        if (event.preventDefault) event.preventDefault();
        reset(entry.side);
      }
    });
  }

  window.addEventListener("resize", () => {
    for (const entry of sides) {
      const clamped = clampWidth(entry.side, widths[entry.side], effectiveMax(entry.side));
      if (clamped !== null && clamped !== widths[entry.side]) {
        widths[entry.side] = clamped;
        paint(entry.side);
      }
    }
  });

  // A stored width may no longer fit (smaller window, wider other side); clamp
  // before the first paint so the gallery never starts at zero width.
  for (const entry of sides) {
    const clamped = clampWidth(entry.side, widths[entry.side], effectiveMax(entry.side));
    if (clamped !== null) widths[entry.side] = clamped;
  }

  paintAll();
  return { reset, current: () => ({ ...widths }) };
}
