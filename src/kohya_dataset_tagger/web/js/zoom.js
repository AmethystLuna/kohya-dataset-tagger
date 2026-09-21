/**
 * Enlarged preview of one gallery image, with its tags beside it and editable
 * in place. Opened by double-clicking a tile in the center workspace.
 *
 * The editor is the SAME module the sidebar uses, re-instantiated against the
 * nodes built here: tags.js is fully parameterised by DOM elements, so a second
 * instance needs no new editing logic and inherits autosave, autocomplete and
 * the caption-cache invalidation that goes with every write.
 *
 * While this is open the sidebar editor is flushed and disabled (two live copies
 * of one caption would overwrite each other) and reloaded on close. The image is
 * THUMB_SIZE.PREVIEW (1600px), the same size the sidebar preview uses - neither
 * view ever loads the original file.
 */
import { api, thumbUrl, THUMB_SIZE } from "./api.js";
import { t } from "./strings.js";
import { createTagEditor } from "./tags.js";

// The enlarged preview doubles as an image viewer: the wheel zooms around the
// cursor and dragging pans. MIN_SCALE = 1 means "fit the stage" - the only state
// where the whole picture is guaranteed visible, and so the reset point. The
// bitmap is transformed, never the layout: paging stays a plain src swap.
const MIN_SCALE = 1;
const MAX_SCALE = 8;
//: Zoom per wheel unit; exp() keeps every notch the same ratio (symmetric in/out).
const WHEEL_ZOOM = 0.0015;
//: What a double-click jumps to when the image is at fit.
const DOUBLE_CLICK_SCALE = 2;

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

function button(className, text) {
  const node = el("button", className, text);
  node.type = "button";
  return node;
}

/**
 * Paging arrow overlaid on one side of the picture. The glyph is decoration:
 * the accessible name (aria-label) is the user copy, so a screen reader still
 * announces the control while the button itself stays a bare chevron.
 */
function navButton(className, label, glyph) {
  const node = button("zoom-nav " + className, glyph);
  node.setAttribute("aria-label", label);
  node.title = label;
  return node;
}

function describeError(err) {
  return err && err.message ? err.message : String(err);
}

export function createZoom(options) {
  const config = options || {};
  const notify = config.notify || (() => {});
  const onOpen = config.onOpen || (() => {});
  const onClose = config.onClose || (() => {});
  const onSaved = config.onSaved || (() => {});

  const backdrop = el("div", "zoom-backdrop");
  backdrop.hidden = true;
  const dialog = el("div", "zoom-dialog");
  dialog.setAttribute("role", "dialog");
  dialog.setAttribute("aria-modal", "true");
  // Focus lands here, not in the tag input: arrow keys must page immediately.
  // (The input keeps them while it is focused, which is what typing needs.)
  dialog.tabIndex = -1;

  const heading = el("h2", "panel-title", t("zoom.title"));
  const pathLine = el("span", "zoom-path mono");
  const counter = el("span", "zoom-counter hint");
  const closeButton = button("btn btn-mini zoom-close", t("zoom.close"));
  const head = el("div", "zoom-head");
  head.append(heading, pathLine, counter, el("span", "spacer"), closeButton);

  const image = el("img", "zoom-img");
  image.alt = "";
  image.draggable = false; // native image drag would fight the pan gesture
  const emptyNote = el("p", "hint", t("zoom.empty"));
  emptyNote.hidden = true;
  // Paging used to be two small buttons in the header, far from the picture they
  // change; now it is a lightbox-style arrow on each side of the image itself.
  const prevButton = navButton("zoom-nav-prev", t("zoom.prev"), "\u2039");
  const nextButton = navButton("zoom-nav-next", t("zoom.next"), "\u203A");
  const stage = el("div", "zoom-stage");
  stage.append(image, emptyNote, prevButton, nextButton);

  const chips = el("div", "chips zoom-chips");
  const input = el("input", "zoom-input");
  input.type = "text";
  input.autocomplete = "off";
  input.spellcheck = false;
  input.placeholder = t("caption.inputPlaceholder");
  const suggest = el("div", "suggest zoom-suggest");
  suggest.hidden = true;
  const suggestWrap = el("div", "suggest-wrap");
  suggestWrap.append(input, suggest);
  const addButton = button("btn zoom-add", t("caption.add"));
  const inputRow = el("div", "tag-input-row");
  inputRow.append(suggestWrap, addButton);

  const rawToggle = el("input", "zoom-raw");
  rawToggle.type = "checkbox";
  const rawLabel = el("label", "field-inline");
  rawLabel.append(rawToggle, el("span", null, t("caption.rawMode")));
  const stateEl = el("span", "save-state zoom-state");
  const saveButton = button("btn zoom-save", t("caption.saveNow"));
  const actions = el("div", "row");
  actions.append(rawLabel, el("span", "spacer"), stateEl, saveButton);

  const rawText = el("textarea", "raw-text zoom-rawtext");
  rawText.spellcheck = false;
  rawText.hidden = true;

  const warning = el("div", "warning zoom-warning");
  warning.hidden = true;
  warning.append(el("strong", null, t("caption.multilineTitle")), el("span", null, t("caption.multilineBody")));

  const captionFile = el("p", "hint mono zoom-file");
  const hint = el("p", "hint", t("zoom.hint"));

  const tagColumn = el("div", "zoom-tags");
  tagColumn.append(chips, inputRow, actions, rawText, warning, captionFile, hint);

  const body = el("div", "zoom-body");
  body.append(stage, tagColumn);
  dialog.append(head, body);
  backdrop.append(dialog);
  document.body.append(backdrop);

  const editor = createTagEditor({
    chips,
    input,
    suggest,
    addButton,
    saveButton,
    state: stateEl,
    warning,
    captionFile,
    rawToggle,
    rawText,
    confirm: config.confirm || (() => true),
    onSaved: (result) => {
      const path = editor.getPath();
      if (path) onSaved(path, result);
    },
    onError: (message) => notify(message, "error"),
    onNotify: (message, kind) => notify(message, kind),
  });

  let list = [];
  let index = -1;
  let opened = false;
  let token = 0;

  function currentPath() {
    return index >= 0 && index < list.length ? list[index] : null;
  }

  function paintHeader() {
    const path = currentPath();
    pathLine.textContent = path || "";
    pathLine.title = path || "";
    counter.textContent = path ? t("zoom.position", { i: index + 1, n: list.length }) : "";
    const many = list.length > 1;
    prevButton.disabled = !many;
    nextButton.disabled = !many;
    prevButton.hidden = !many;
    nextButton.hidden = !many;
  }

  // --- viewer state -------------------------------------------------------
  let scale = MIN_SCALE;
  let offsetX = 0;
  let offsetY = 0;
  let pan = null;
  //: A drag that ends must not be read as a backdrop click (that would close).
  let suppressClose = false;

  function stageCenter() {
    const rect = stage.getBoundingClientRect();
    return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
  }

  /** Keep the picture from being dragged completely off the stage. */
  function clampOffset() {
    const rect = stage.getBoundingClientRect();
    const width = image.clientWidth;
    const height = image.clientHeight;
    if (!width || !height) return; // not decoded yet: nothing to clamp against
    const limitX = Math.max(0, (width * scale - rect.width) / 2);
    const limitY = Math.max(0, (height * scale - rect.height) / 2);
    offsetX = Math.min(limitX, Math.max(-limitX, offsetX));
    offsetY = Math.min(limitY, Math.max(-limitY, offsetY));
  }

  function paintView() {
    const moved = scale !== MIN_SCALE || offsetX !== 0 || offsetY !== 0;
    image.style.transform = moved
      ? `translate(${offsetX}px, ${offsetY}px) scale(${scale})`
      : "";
    stage.classList.toggle("is-zoomed", scale > MIN_SCALE);
  }

  /** Back to "fit"; every paging step and every close returns here. */
  function resetView() {
    scale = MIN_SCALE;
    offsetX = 0;
    offsetY = 0;
    pan = null;
    stage.classList.remove("is-panning");
    paintView();
  }

  /** Zoom to `next` keeping the stage point (clientX, clientY) fixed on screen. */
  function zoomAt(next, clientX, clientY) {
    const target = Math.min(MAX_SCALE, Math.max(MIN_SCALE, next));
    if (target === scale) return;
    const center = stageCenter();
    const dx = clientX - center.x;
    const dy = clientY - center.y;
    const ratio = target / scale;
    offsetX = dx - (dx - offsetX) * ratio;
    offsetY = dy - (dy - offsetY) * ratio;
    scale = target;
    if (scale === MIN_SCALE) {
      offsetX = 0;
      offsetY = 0;
    } else {
      clampOffset();
    }
    paintView();
  }

  function onWheel(event) {
    if (!opened) return;
    event.preventDefault(); // the viewer owns the wheel while it is open
    zoomAt(scale * Math.exp(-event.deltaY * WHEEL_ZOOM), event.clientX, event.clientY);
  }

  function onPointerDown(event) {
    // Panning a picture that already fits the stage has nothing to reveal.
    if (!opened || scale <= MIN_SCALE) return;
    if (event.button !== undefined && event.button !== 0) return;
    const target = event.target;
    if (target && typeof target.closest === "function" && target.closest("button")) return;
    pan = { id: event.pointerId, x: event.clientX, y: event.clientY, offsetX, offsetY, moved: false };
    if (typeof stage.setPointerCapture === "function" && event.pointerId !== undefined) {
      stage.setPointerCapture(event.pointerId);
    }
    stage.classList.add("is-panning");
    event.preventDefault();
  }

  function onPointerMove(event) {
    if (!pan || event.pointerId !== pan.id) return;
    offsetX = pan.offsetX + (event.clientX - pan.x);
    offsetY = pan.offsetY + (event.clientY - pan.y);
    if (event.clientX !== pan.x || event.clientY !== pan.y) pan.moved = true;
    clampOffset();
    paintView();
    event.preventDefault();
  }

  function onPointerUp(event) {
    if (!pan || (event.pointerId !== undefined && event.pointerId !== pan.id)) return;
    const moved = pan.moved;
    pan = null;
    stage.classList.remove("is-panning");
    if (typeof stage.releasePointerCapture === "function" && event.pointerId !== undefined) {
      stage.releasePointerCapture(event.pointerId);
    }
    if (moved) {
      suppressClose = true;
      setTimeout(() => { suppressClose = false; }, 0);
    }
  }

  function onDoubleClick(event) {
    if (!opened) return;
    // The paging arrows are <button>s inside the stage, so two quick clicks on
    // one of them bubble up here as a dblclick: that gesture means "next, next",
    // NOT "zoom in". Only the picture / empty background toggles zoom.
    const target = event.target;
    if (target && typeof target.closest === "function" && target.closest("button")) return;
    zoomAt(scale > MIN_SCALE ? MIN_SCALE : DOUBLE_CLICK_SCALE, event.clientX, event.clientY);
  }

  async function show(nextIndex) {
    if (nextIndex < 0 || nextIndex >= list.length) return;
    const mine = ++token;
    await editor.flush();
    if (mine !== token) return;
    index = nextIndex;
    const path = list[index];
    image.src = thumbUrl(path, THUMB_SIZE.PREVIEW);
    // The path is already on screen; the alt only has to identify the image.
    image.alt = String(path).split(/[\\/]/).pop() || "";
    resetView(); // a new picture must not inherit the last zoom/pan
    paintHeader();
    try {
      await editor.load(path);
    } catch (err) {
      if (mine !== token) return;
      notify(t("zoom.loadFailed", { message: describeError(err) }), "error");
    }
  }

  async function open(path, paths) {
    if (opened || !path) return;
    const items = Array.isArray(paths) && paths.length > 0 ? paths.slice() : [path];
    let start = items.indexOf(path);
    if (start < 0) {
      items.unshift(path);
      start = 0;
    }
    list = items;
    opened = true;
    // Remembered so closing can hand the keyboard back: the viewer covers the
    // gallery, and without this focus falls to the body and the user loses their
    // place in the grid.
    restoreFocusTo = document.activeElement || null;
    backdrop.hidden = false;
    document.addEventListener("keydown", onKeyDown);
    await onOpen(path);
    await show(start);
    dialog.focus();
  }

  //: Where focus was before the viewer opened.
  let restoreFocusTo = null;

  /** Hide and release the bitmap, resolving with the image that was showing. */
  async function teardown() {
    const path = currentPath();
    opened = false;
    token += 1;
    backdrop.hidden = true;
    document.removeEventListener("keydown", onKeyDown);
    await editor.flush();
    editor.disable();
    resetView();
    image.removeAttribute("src");
    list = [];
    index = -1;
    if (restoreFocusTo && typeof restoreFocusTo.focus === "function") restoreFocusTo.focus();
    restoreFocusTo = null;
    return path;
  }

  async function close() {
    if (!opened) return;
    const path = await teardown();
    await onClose(path);
  }

  /** Close without asking the sidebar to reload: the caller is switching directory. */
  async function dismiss() {
    if (!opened) return;
    await teardown();
  }

  function onKeyDown(event) {
    if (!opened) return;
    if (event.key === "Escape") {
      event.preventDefault();
      void close();
      return;
    }
    const target = event.target;
    const tag = target && target.tagName ? String(target.tagName).toUpperCase() : "";
    if (tag === "INPUT" || tag === "TEXTAREA") return; // typing wins over paging
    if (event.key === "+" || event.key === "=") {
      event.preventDefault();
      const center = stageCenter();
      zoomAt(scale * 1.25, center.x, center.y);
      return;
    }
    if (event.key === "-" || event.key === "_") {
      event.preventDefault();
      const center = stageCenter();
      zoomAt(scale / 1.25, center.x, center.y);
      return;
    }
    if (event.key === "0") {
      event.preventDefault();
      resetView();
      return;
    }
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      void show(index - 1);
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      void show(index + 1);
    }
  }

  backdrop.addEventListener("click", (event) => {
    if (suppressClose) { suppressClose = false; return; }
    if (event.target === backdrop) void close();
  });
  closeButton.addEventListener("click", () => { void close(); });
  prevButton.addEventListener("click", () => { void show(index - 1); });
  nextButton.addEventListener("click", () => { void show(index + 1); });

  stage.addEventListener("wheel", onWheel, { passive: false });
  stage.addEventListener("pointerdown", onPointerDown);
  stage.addEventListener("pointermove", onPointerMove);
  stage.addEventListener("pointerup", onPointerUp);
  stage.addEventListener("pointercancel", onPointerUp);
  stage.addEventListener("dblclick", onDoubleClick);

  editor.disable();

  return {
    open,
    close,
    dismiss,
    isOpen: () => opened,
    current: currentPath,
  };
}
