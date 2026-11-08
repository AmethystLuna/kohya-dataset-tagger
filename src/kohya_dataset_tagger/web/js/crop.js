/**
 * Crop panel (section 4.17): confirm a crop box image by image, archive the result next to
 * the source, then optionally let the tagger caption the new files.
 *
 * What this panel is
 * ------------------
 * The scaling panel acts on a whole dataset root and writes into a target directory. This one
 * acts on the shared scope strip's image list, one image at a time, and **writes where the
 * image already lives**: <stem>_<n><ext> beside the source, always numbered, never an
 * overwrite, never a caption inherited (that is section 4.17's contract). The two halves of the
 * image pipeline - crop and area-normalized scaling - can be used together (the "scale after
 * crop" box) or apart (this panel with it off, the scaling panel unchanged).
 *
 * Why a dialog per image
 * ----------------------
 * A crop is the one decision a program cannot make for the user, and a wrong one is expensive
 * (it is a new file in the dataset). So the rectangle is confirmed by hand; everything that can
 * be derived is derived, and everything derived is shown next to the box:
 *
 *  - the box is carried over to the next image. Same display resolution gives the same
 *    rectangle. A different resolution gives the same **relative** window with the same ratio
 *    (cropbox.js propagate), which is what Lightroom's copy-settings and Capture One's
 *    apply-crop do;
 *  - "normalize to the target size" makes the box area exactly the training resolution's area,
 *    so with scaling on the factor is 1.0 and the pixels are not resampled at all;
 *  - the ratio presets (cropbox.js ASPECT_PRESETS) lock the box's shape while dragging;
 *  - the box snaps to the image edges, the centre and the thirds - the same lines the overlay
 *    draws (the guide lines of a crop tool).
 *
 * The tagger step is a reuse, not a second implementation: it calls the existing
 * /api/autotag/run with the images it just archived and the settings the tagger panel is
 * showing (the panel hands them over through getTaggerParams). It runs **after** the dialog
 * closes - one job for the whole batch, so the model loads once and the shelf shows one row.
 */
import { api, autotagStreamUrl, rawUrl, thumbUrl, THUMB_SIZE } from "./api.js";
import { forgetJob, rememberJob } from "./jobstore.js";
import { t } from "./strings.js";
import {
  ASPECT_PRESETS,
  CROP_HANDLES,
  applyNumeric,
  areaOf,
  clampRect,
  clampView,
  fitCanvas,
  fitPreview,
  formatRatio,
  frameOf,
  guidesOf,
  largestWithRatio,
  moveBy,
  normalizeArea,
  propagate,
  ratioOf,
  resizeByHandle,
  snapDelta,
  wholeFrame,
  zoomView,
} from "./cropbox.js";

//: The archive format (section 4.17): "keep" writes the source's own format, .bmp falling back to PNG.
export const CROP_ARCHIVE_FORMAT = "keep";
//: Panel defaults. Scaling after the crop is on - that is the "combined with scaling" case, and
//: with the box normalized to the target it is a no-op on the pixels. Tagging is opt-in.
export const CROP_DEFAULTS = Object.freeze({
  aspect: "free",
  scale: true,
  tag: false,
});
//: Smallest crop box, in source pixels: below this a drag is a mis-click, not an intention.
export const CROP_MIN_SIZE = 8;
//: Snapping distance in **display** pixels; converted to source pixels against the drawn image.
export const CROP_SNAP_PX = 6;
//: Wheel zoom rate: one 100-pixel notch is about x1.16, fine enough for a trackpad.
const CROP_ZOOM_RATE = 0.0015;
//: Error rows rendered before the panel says "and N more".
export const CROP_ERROR_ROWS = 20;
//: Consecutive EventSource failures tolerated while tagging before giving up.
const STREAM_ERROR_LIMIT = 3;
//: How long an accepted cancel may go without a terminal event before the panel unlocks itself.
const CANCEL_WATCHDOG_MS = 30000;

let nextDialogId = 0;

function describeError(err) {
  return err && err.message ? err.message : String(err);
}

function baseName(path) {
  const parts = String(path || "").split(/[\\/]+/);
  return parts.length > 0 ? parts[parts.length - 1] : String(path || "");
}

function parseData(raw) {
  try {
    return JSON.parse(String(raw));
  } catch (err) {
    return {};
  }
}

function integerOr(value, fallback) {
  const parsed = Number.parseInt(String(value === undefined ? "" : value).trim(), 10);
  return Number.isFinite(parsed) ? parsed : fallback;
}

/**
 * Pure builder for the POST /api/crop/apply body. Exported so the headless harness can drive it
 * without a DOM: the panel only hands it the rectangle, the two switches and the shared
 * parameters, and everything that decides what lands on disk is in here.
 *
 * The scaling half is present exactly when it is switched on (null otherwise) - section 4.17
 * has no "enabled" inside it, so "scale was asked for" and "scale happened" cannot disagree.
 */
export function buildCropRequest(input) {
  const source = input || {};
  const rect = source.rect || {};
  const scale = source.scale === true;
  const resolution = source.resolution || [];
  const processing = source.processing || {};
  return {
    src: String(source.src || ""),
    crop: {
      x: integerOr(rect.x, 0),
      y: integerOr(rect.y, 0),
      width: Math.max(1, integerOr(rect.width, 1)),
      height: Math.max(1, integerOr(rect.height, 1)),
    },
    format: CROP_ARCHIVE_FORMAT,
    scale: scale
      ? {
          target_width: integerOr(resolution[0], 1536),
          target_height: integerOr(resolution[1], 1536),
          no_upscale: processing.noUpscale !== false,
          resample: String(processing.resample || "lanczos"),
          quality: Math.min(100, Math.max(1, integerOr(processing.quality, 95))),
          optimize: processing.optimize !== false,
        }
      : null,
  };
}

export function createCropPanel(options) {
  const config = options || {};
  const notify = config.notify || (() => {});
  const scope = config.scope || null;
  const getParams = config.getParams;
  const getTaggerParams = typeof config.getTaggerParams === "function" ? config.getTaggerParams : null;
  const onArchived = typeof config.onArchived === "function" ? config.onArchived : null;
  const targetHost = config.targetHost || document.body;

  //: The job shelf (js/jobs.js); optional so the panel still runs headless without one.
  const shelf = config.jobs || null;
  let shelfJob = null;
  let runningJobId = null;

  let scaled = CROP_DEFAULTS.scale;
  let tagging = CROP_DEFAULTS.tag;
  let aspectId = CROP_DEFAULTS.aspect;
  //: Images of the current run: {paths, archived, failed}. Only ever touched by the run.
  let run = null;
  let dialog = null;
  let tagJob = null;
  let streamErrors = 0;
  let watchdog = null;

  const make = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const button = (className, text) => {
    const node = make("button", className, text);
    node.type = "button";
    return node;
  };
  const numberInput = (value, min, max) => {
    const input = make("input");
    input.type = "number";
    input.min = String(min);
    input.max = String(max);
    input.step = "1";
    input.value = String(value);
    return input;
  };
  const painters = [];
  const paintText = (node, key, params) => {
    painters.push(() => { node.textContent = t(key, params); });
    return node;
  };
  const copyButton = (className, key) => paintText(button(className), key);
  const checkboxField = (key, checked) => {
    const boxEl = make("input");
    boxEl.type = "checkbox";
    boxEl.checked = checked === true;
    const field = make("label", "field-inline");
    field.append(boxEl, paintText(make("span"), key));
    return { field, box: boxEl };
  };
  function paintLabels() {
    for (const paint of painters) paint();
  }

  // ---- tab + panel shell ------------------------------------------------
  const tab = copyButton("tab", "tab.crop");
  tab.setAttribute("role", "tab");
  tab.dataset.tab = "crop";

  const panel = make("div", "tab-panel");
  panel.dataset.panel = "crop";
  panel.hidden = true;

  const scopeLine = paintText(make("div", "hint mono"), "crop.scopeNone");
  const hint = paintText(make("p", "hint"), "crop.hint");

  const scaleField = checkboxField("crop.scaleAfter", scaled);
  const scaleHint = make("p", "hint");
  const tagField = checkboxField("crop.tagAfter", tagging);
  const tagHint = make("p", "hint");

  const optionsBox = make("fieldset", "box");
  optionsBox.append(
    paintText(make("legend"), "crop.options"),
    scaleField.field,
    scaleHint,
    tagField.field,
    tagHint,
  );

  const startButton = copyButton("btn btn-primary", "crop.start");
  startButton.id = "btn-crop-start";
  const cancelButton = copyButton("btn btn-danger", "crop.cancelTag");
  cancelButton.hidden = true;
  const actions = make("div", "row");
  actions.append(startButton, cancelButton);

  const progressFill = make("div", "progress-fill");
  const progressTrack = make("div", "progress-track");
  progressFill.setAttribute("role", "progressbar");
  progressFill.setAttribute("aria-valuemin", "0");
  progressFill.setAttribute("aria-valuemax", "100");
  progressFill.setAttribute("aria-valuenow", "0");
  progressTrack.append(progressFill);
  const progressText = make("div", "hint");
  progressText.setAttribute("role", "status");
  progressText.setAttribute("aria-live", "polite");
  const errorsBox = make("div", "errors");
  errorsBox.setAttribute("role", "alert");
  const progress = make("div", "progress");
  progress.append(progressTrack, progressText, errorsBox);
  progress.hidden = true;

  const summary = make("div", "mono");
  const tagLine = make("div", "hint");

  panel.append(
    paintText(make("h3", "panel-title"), "crop.title"),
    hint,
    scopeLine,
    optionsBox,
    actions,
    progress,
    summary,
    tagLine,
  );

  // ---- shared parameters (sections 4.9 / 4.14) ---------------------------
  function sharedParams() {
    return typeof getParams === "function" ? getParams() : null;
  }

  function resolution() {
    const params = sharedParams();
    const pair = params ? params.resolution() : [1536, 1536];
    return [integerOr(pair[0], 1536), integerOr(pair[1], 1536)];
  }

  function processing() {
    const params = sharedParams();
    return params
      ? params.processing()
      : { noUpscale: true, resample: "lanczos", quality: 95, optimize: true };
  }

  function taggerParams() {
    return getTaggerParams ? getTaggerParams() : null;
  }

  /** The images this panel would confirm right now, in the order the scope strip reports them. */
  function scopePaths() {
    if (!scope || typeof scope.paths !== "function") return [];
    const seen = new Set();
    const out = [];
    for (const path of scope.paths() || []) {
      const value = String(path || "");
      if (value.length === 0 || seen.has(value)) continue;
      seen.add(value);
      out.push(value);
    }
    return out;
  }

  function paintScope() {
    const count = scopePaths().length;
    scopeLine.textContent = count > 0 ? t("crop.scopeValue", { n: count }) : t("crop.scopeNone");
    startButton.disabled = count === 0 || tagJob !== null;
  }

  /**
   * Both inherited groups are painted from their owners, never remembered here: the resolution
   * and the pipeline settings come from the shared store, the model from the tagger panel. A
   * value the panel acts on but cannot show would be a setting the user has to guess at.
   */
  function paintOptions() {
    const size = resolution();
    scaleHint.textContent = scaled
      ? t("crop.scaleHintOn", { width: size[0], height: size[1] })
      : t("crop.scaleHintOff");
    const params = taggerParams();
    const model = params && params.model ? String(params.model) : "";
    tagHint.textContent = tagging
      ? t("crop.tagHintOn", { model: model || t("crop.taggerDefault") })
      : t("crop.tagHintOff");
  }

  // ---- the confirmation dialog ------------------------------------------
  /**
   * Build one dialog. Everything is created per run and thrown away afterwards, so the copy is
   * read at open time and there is nothing to register with the relabel contract.
   */
  function buildDialog() {
    // Where the keyboard was before the dialog took it: a modal that swallows focus leaves the
    // user at the top of the page when it closes (the picker / zoom / settings dialogs' rule).
    const restoreFocusTo = document.activeElement || null;
    const backdrop = make("div", "dialog-backdrop crop-backdrop");
    const shell = make("div", "dialog crop-dialog");
    shell.setAttribute("role", "dialog");
    shell.setAttribute("aria-modal", "true");
    const titleId = "crop-title-" + (nextDialogId += 1);
    shell.setAttribute("aria-labelledby", titleId);

    const head = make("div", "dialog-head");
    const title = make("h2", "panel-title", t("crop.dialogTitle"));
    title.id = titleId;
    const counter = make("span", "hint");
    head.append(title, counter);

    const aspectSelect = make("select", "crop-aspect");
    for (const preset of ASPECT_PRESETS) {
      const option = make("option", "", preset.labelKey ? t(preset.labelKey) : preset.label);
      option.value = preset.id;
      aspectSelect.append(option);
    }
    aspectSelect.value = aspectId;
    const aspectField = make("label", "field");
    aspectField.append(make("span", "", t("crop.aspect")), aspectSelect);

    const xInput = numberInput(0, -1e6, 1e6);
    const yInput = numberInput(0, -1e6, 1e6);
    const wInput = numberInput(1, 1, 1e6);
    const hInput = numberInput(1, 1, 1e6);
    const wrap = (key, input) => {
      const field = make("label");
      field.append(make("span", "", t(key)), input);
      return field;
    };
    const rectFields = make("div", "threshold-grid crop-rect");
    rectFields.append(wrap("crop.x", xInput), wrap("crop.y", yInput), wrap("crop.width", wInput), wrap("crop.height", hInput));

    const normalizeButton = button("btn", t("crop.normalize"));
    const resetButton = button("btn", t("crop.reset"));
    //: "Show me the crop": a view mode, not a crop parameter, so it sits at the end of the
    //: toolbar. While it is on every option is frozen and the canvas is a read-only projection.
    const previewBox = make("input", "crop-preview");
    previewBox.type = "checkbox";
    const previewToggle = make("label", "field-inline");
    previewToggle.append(previewBox, make("span", "", t("crop.preview")));
    const toolbar = make("div", "row crop-toolbar");
    toolbar.append(aspectField, rectFields, normalizeButton, resetButton, previewToggle);

    const image = make("img", "crop-image");
    image.alt = "";
    image.draggable = false;
    const boxEl = make("div", "crop-box");
    for (const guide of ["v1", "v2", "h1", "h2"]) {
      boxEl.append(make("div", "crop-guide " + guide));
    }
    const handles = new Map();
    for (const name of CROP_HANDLES) {
      const handle = make("div", "crop-handle " + name);
      handle.dataset.handle = name;
      handles.set(name, handle);
      boxEl.append(handle);
    }
    // The image and the box live in one canvas that is what zoom and pan move, so the box can
    // never drift away from the pixels it describes.
    const canvasEl = make("div", "crop-canvas");
    canvasEl.append(image, boxEl);
    const stage = make("div", "crop-stage");
    stage.append(canvasEl);

    const readout = make("div", "hint mono");
    //: The output-scaling switch, on the readout line: the sentence it changes is the one that
    //: says what the confirmed crop will write. Same value as the panel's switch (the two are
    //: written together in startRun), so "was scaling asked for" still has exactly one answer.
    const scaleBox = make("input", "crop-scale");
    scaleBox.type = "checkbox";
    scaleBox.checked = scaled;
    const scaleToggle = make("label", "field-inline");
    scaleToggle.append(scaleBox, make("span", "", t("crop.scaleAfter")));
    const outputRow = make("div", "row crop-output");
    outputRow.append(scaleToggle, readout);
    const wroteLine = make("div", "hint mono");
    const keyHint = make("div", "hint", t("crop.keys"));
    const errorLine = make("div", "errors");
    errorLine.setAttribute("role", "alert");

    const finishButton = button("btn", t("crop.finish"));
    const skipButton = button("btn", t("crop.skip"));
    const confirmButton = button("btn btn-primary", t("crop.confirm"));
    const dialogActions = make("div", "row crop-actions");
    dialogActions.append(finishButton, skipButton, confirmButton);

    shell.append(head, toolbar, stage, outputRow, wroteLine, keyHint, errorLine, dialogActions);
    backdrop.append(shell);
    document.body.append(backdrop);

    return {
      backdrop, shell, counter, aspectSelect, xInput, yInput, wInput, hInput, scaleBox,
      normalizeButton, resetButton, previewBox, image, stage, canvasEl, boxEl, handles, readout, wroteLine,
      keyHint, errorLine, finishButton, skipButton, confirmButton, restoreFocusTo,
    };
  }

  /** The stage viewport in CSS pixels. The headless harness has no layout: fall back to the frame. */
  function stageSize() {
    const frame = (dialog && dialog.frame) || { width: 1, height: 1 };
    const stage = dialog ? dialog.stage : null;
    const width = stage && stage.clientWidth ? stage.clientWidth : frame.width;
    const height = stage && stage.clientHeight ? stage.clientHeight : frame.height;
    return { width: Math.max(1, width), height: Math.max(1, height) };
  }

  /**
   * Paint the canvas - the image and the crop box travel together, so zooming and panning can
   * never move one without the other: the box keeps describing the same pixels, and the
   * confirmed rectangle is whatever the numeric fields say, not what the viewport shows.
   */
  function paintView() {
    if (!dialog || !dialog.frame) return;
    const stage = stageSize();
    if (dialog.preview && dialog.rect) {
      // Preview: the crop rectangle becomes the whole viewport, fitted and centred, and the
      // clip-path throws away the image around it - the viewport shows the crop and only the
      // crop. The crop keeps its own aspect; a mismatched stage shows as letterbox.
      const fit = fitPreview(dialog.frame, dialog.rect, stage);
      dialog.canvasSize = { width: fit.width, height: fit.height, base: { width: fit.width, height: fit.height } };
      dialog.canvasEl.style.width = fit.width + "px";
      dialog.canvasEl.style.height = fit.height + "px";
      dialog.canvasEl.style.left = fit.left + "px";
      dialog.canvasEl.style.top = fit.top + "px";
      dialog.canvasEl.style.clipPath =
        "inset(" + fit.clip.top + "px " + fit.clip.right + "px " + fit.clip.bottom + "px " + fit.clip.left + "px)";
      return;
    }
    const base = fitCanvas(dialog.frame, stage);
    dialog.view = clampView(dialog.view || { zoom: 1, x: 0, y: 0 }, base, stage);
    const view = dialog.view;
    const width = Math.max(1, Math.round(base.width * view.zoom));
    const height = Math.max(1, Math.round(base.height * view.zoom));
    dialog.canvasSize = { width, height, base };
    dialog.canvasEl.style.width = width + "px";
    dialog.canvasEl.style.height = height + "px";
    dialog.canvasEl.style.left = Math.round(view.x) + "px";
    dialog.canvasEl.style.top = Math.round(view.y) + "px";
    dialog.canvasEl.style.clipPath = "";
  }

  /** The drawing scale: CSS pixels per source pixel, read from the canvas (so it follows zoom). */
  function paintBox() {
    if (!dialog || !dialog.frame || !dialog.rect) return;
    const frame = dialog.frame;
    const size = dialog.canvasSize || { width: frame.width, height: frame.height };
    const cssWidth = Math.max(1, size.width);
    dialog.drawScale = cssWidth / frame.width;
    //: Source pixels per CSS pixel - what a pointer delta has to be multiplied by.
    dialog.pointerScale = frame.width / cssWidth;
    const k = dialog.drawScale;
    const rect = dialog.rect;
    dialog.boxEl.style.left = Math.round(rect.x * k) + "px";
    dialog.boxEl.style.top = Math.round(rect.y * k) + "px";
    dialog.boxEl.style.width = Math.max(1, Math.round(rect.width * k)) + "px";
    dialog.boxEl.style.height = Math.max(1, Math.round(rect.height * k)) + "px";
  }

  function paintNumeric() {
    if (!dialog) return;
    const rect = dialog.rect;
    const active = document.activeElement;
    if (active !== dialog.xInput) dialog.xInput.value = String(rect.x);
    if (active !== dialog.yInput) dialog.yInput.value = String(rect.y);
    if (active !== dialog.wInput) dialog.wInput.value = String(rect.width);
    if (active !== dialog.hInput) dialog.hInput.value = String(rect.height);
  }

  function paintReadout() {
    if (!dialog) return;
    const size = resolution();
    const rect = dialog.rect;
    dialog.readout.textContent = t("crop.readout", {
      frameWidth: dialog.frame.width,
      frameHeight: dialog.frame.height,
      width: rect.width,
      height: rect.height,
      ratio: formatRatio(rect.width, rect.height),
      output: dialog.scale
        ? t("crop.outputScaled", { width: size[0], height: size[1] })
        : t("crop.outputNative"),
    });
    dialog.wroteLine.textContent = dialog.lastWrite || "";
    dialog.counter.textContent = t("crop.counter", {
      index: dialog.index + 1,
      total: dialog.items.length,
      name: baseName(dialog.items[dialog.index]),
    });
  }

  function paintDialog() {
    if (!dialog || !dialog.rect) return;
    paintView();
    // The box is hidden in preview; drawing it against the preview's canvas scale would be a
    // rectangle nobody can see and a scale the next drag would read as the truth.
    if (!dialog.preview) paintBox();
    paintNumeric();
    paintReadout();
  }

  /** Apply a new rectangle, clamped to the frame, and repaint. The frame is the hard bound. */
  function setRect(rect) {
    if (!dialog) return;
    dialog.rect = clampRect(rect, dialog.frame);
    paintDialog();
  }

  function currentRatio() {
    return dialog ? ratioOf(dialog.aspectId, dialog.frame) : null;
  }

  /** The box a fresh image starts from: the preset's ratio if locked, else the whole frame. */
  function defaultRect() {
    const ratio = currentRatio();
    return ratio ? largestWithRatio(dialog.frame, ratio) : wholeFrame(dialog.frame);
  }

  // ---- dragging ---------------------------------------------------------
  /**
   * Snap the moving edges onto the guides. The threshold is a distance **on screen**, so it
   * stays the same six pixels however far the image is scaled down - a crop tool that snaps
   * harder on a large image is unusable.
   */
  function snapAdjust(rect, handle, dx, dy) {
    const frame = dialog.frame;
    const guides = guidesOf(frame);
    const targetsX = [0].concat(guides.vertical, [frame.width]);
    const targetsY = [0].concat(guides.horizontal, [frame.height]);
    // Six **screen** pixels, so a zoomed-in canvas snaps just as gently as a fitted one.
    const perPixel = dialog.pointerScale || frame.width / Math.max(1, dialog.canvasSize ? dialog.canvasSize.width : frame.width);
    const threshold = Math.max(1, Math.round(CROP_SNAP_PX * perPixel));
    let adjustX = 0;
    let adjustY = 0;
    if (handle === "move") {
      const left = snapDelta(rect.x + dx, targetsX, threshold);
      const right = snapDelta(rect.x + rect.width + dx, targetsX, threshold);
      const top = snapDelta(rect.y + dy, targetsY, threshold);
      const bottom = snapDelta(rect.y + rect.height + dy, targetsY, threshold);
      if (left !== 0) adjustX = left;
      else if (right !== 0) adjustX = right;
      if (top !== 0) adjustY = top;
      else if (bottom !== 0) adjustY = bottom;
    } else {
      if (handle.indexOf("w") >= 0) adjustX = snapDelta(rect.x + dx, targetsX, threshold);
      else if (handle.indexOf("e") >= 0) adjustX = snapDelta(rect.x + rect.width + dx, targetsX, threshold);
      if (handle.indexOf("n") >= 0) adjustY = snapDelta(rect.y + dy, targetsY, threshold);
      else if (handle.indexOf("s") >= 0) adjustY = snapDelta(rect.y + rect.height + dy, targetsY, threshold);
    }
    return { dx: dx + adjustX, dy: dy + adjustY };
  }

  /**
   * One pointer down decides which gesture this is, and the two never mix: a handle resizes and
   * the box moves (crop operations), everything else - the background, or anything at all while
   * Space or the middle button is held - pans the canvas. The box is the top layer, so a
   * full-frame crop leaves no background to grab: Space/middle is what keeps the canvas pannable
   * there, and the wheel (which bubbles up through the box) is what keeps it zoomable.
   */
  function onPointerDown(event) {
    if (!dialog || dialog.busy || dialog.drag || dialog.preview) return;
    const button = Number.isFinite(Number(event.button)) ? Number(event.button) : 0;
    if (button !== 0 && button !== 1) return;
    const target = event.target || {};
    const handle = target.dataset && target.dataset.handle ? String(target.dataset.handle) : null;
    const inside = target === dialog.boxEl || String(target.className || "").indexOf("crop-box") >= 0;
    const pan = button === 1 || dialog.space === true || (!handle && !inside);
    const mode = pan ? "pan" : handle || "move";
    if (event.preventDefault) event.preventDefault();
    dialog.drag = {
      mode,
      startX: Number(event.clientX) || 0,
      startY: Number(event.clientY) || 0,
      rect: dialog.rect,
      view: dialog.view ? { zoom: dialog.view.zoom, x: dialog.view.x, y: dialog.view.y } : null,
      pointerId: event.pointerId,
    };
    if (dialog.stage.setPointerCapture && event.pointerId !== undefined) {
      try {
        dialog.stage.setPointerCapture(event.pointerId);
      } catch (err) {
        // A pointer that is already gone cannot be captured; the drag still works.
      }
    }
  }

  function onPointerMove(event) {
    if (!dialog || !dialog.drag) return;
    if (dialog.drag.mode === "pan") {
      const start = dialog.drag.view || { zoom: 1, x: 0, y: 0 };
      dialog.view = {
        zoom: start.zoom,
        x: start.x + (Number(event.clientX) - dialog.drag.startX),
        y: start.y + (Number(event.clientY) - dialog.drag.startY),
      };
      paintView();
      return;
    }
    const scale = dialog.pointerScale || 1;
    const rawX = (Number(event.clientX) - dialog.drag.startX) * scale;
    const rawY = (Number(event.clientY) - dialog.drag.startY) * scale;
    const base = dialog.drag.rect;
    const snapped = snapAdjust(base, dialog.drag.mode, rawX, rawY);
    if (dialog.drag.mode === "move") {
      setRect(moveBy(base, snapped.dx, snapped.dy, dialog.frame));
      return;
    }
    setRect(resizeByHandle(base, dialog.drag.mode, snapped.dx, snapped.dy, {
      frame: dialog.frame,
      ratio: currentRatio(),
      minSize: CROP_MIN_SIZE,
    }));
  }

  function onPointerUp() {
    if (dialog) dialog.drag = null;
  }

  /**
   * The wheel zooms the canvas around the pointer. It is attached to the stage, which the box is
   * only a child of, so a crop box covering the whole viewport cannot swallow the gesture.
   */
  function onWheel(event) {
    if (!dialog || dialog.busy || dialog.preview || !dialog.frame || !dialog.view) return;
    if (event.preventDefault) event.preventDefault();
    const raw = Number(event.deltaY) || 0;
    // deltaMode: 0 = pixels, 1 = lines, 2 = pages. A line-mode wheel sends 3, not 100.
    const delta = event.deltaMode === 1 ? raw * 16 : event.deltaMode === 2 ? raw * 400 : raw;
    if (delta === 0) return;
    const stage = stageSize();
    const base = fitCanvas(dialog.frame, stage);
    const rect = dialog.stage.getBoundingClientRect ? dialog.stage.getBoundingClientRect() : { left: 0, top: 0 };
    const anchor = {
      x: (Number(event.clientX) || 0) - (Number(rect.left) || 0),
      y: (Number(event.clientY) || 0) - (Number(rect.top) || 0),
    };
    dialog.view = zoomView(dialog.view, base, stage, Math.exp(-delta * CROP_ZOOM_RATE), anchor);
    paintView();
    paintBox();
  }

  /** "0": back to the fitted, centred view - the way out of a zoom the user lost track of. */
  function resetView() {
    if (!dialog || !dialog.frame) return;
    dialog.view = { zoom: 1, x: 0, y: 0 };
    paintView();
    paintBox();
  }

  function onWindowResize() {
    if (!dialog) return;
    paintDialog();
  }

  // A window that loses focus while Space is held must not leave the next drag panning.
  function onWindowBlur() {
    if (dialog) dialog.space = false;
  }

  // ---- the dialog's own controls ----------------------------------------
  /**
   * Freeze the options. Two things freeze them: a write in flight (the body is already built)
   * and the crop preview (the rectangle is not editable while you look at its result). The
   * preview switch itself stays live - it is the way out of the preview.
   */
  function applyEnabled() {
    if (!dialog) return;
    const frozen = dialog.busy === true || dialog.preview === true || !dialog.frame;
    dialog.aspectSelect.disabled = frozen;
    for (const input of [dialog.xInput, dialog.yInput, dialog.wInput, dialog.hInput]) input.disabled = frozen;
    dialog.normalizeButton.disabled = frozen;
    dialog.resetButton.disabled = frozen;
    dialog.scaleBox.disabled = frozen;
    dialog.previewBox.disabled = dialog.busy === true || !dialog.frame;
  }

  /**
   * The crop preview: the viewport shows the crop's result and nothing else, and every option
   * is frozen because there is nothing to adjust while previewing. Leaving it restores the
   * canvas view the user had, untouched.
   */
  function setPreview(on) {
    if (!dialog) return;
    dialog.preview = on === true;
    dialog.drag = null;
    dialog.previewBox.checked = dialog.preview;
    dialog.boxEl.hidden = dialog.preview;
    dialog.stage.classList.toggle("is-preview", dialog.preview);
    applyEnabled();
    paintDialog();
  }

  function setBusy(value) {
    if (!dialog) return;
    dialog.busy = value === true;
    dialog.confirmButton.disabled = dialog.busy;
    dialog.skipButton.disabled = dialog.busy;
    dialog.finishButton.disabled = dialog.busy;
    dialog.confirmButton.textContent = dialog.busy
      ? t("crop.writing")
      : dialog.index + 1 >= dialog.items.length
        ? t("crop.confirmLast")
        : t("crop.confirm");
    applyEnabled();
  }

  /**
   * Choosing a ratio is a statement about the shape, so the box is rebuilt to the largest one
   * that fits rather than squeezed - a squeezed box would keep a shape the user did not ask for.
   */
  function applyAspect() {
    const ratio = currentRatio();
    setRect(ratio ? largestWithRatio(dialog.frame, ratio) : dialog.rect);
  }

  /** The one-click "make the crop box area the target size" (section 4.17). */
  function normalize() {
    if (!dialog) return;
    const size = resolution();
    const result = normalizeArea(dialog.rect, currentRatio(), size[0] * size[1], dialog.frame);
    setRect(result.rect);
    dialog.errorLine.textContent = result.clamped
      ? t("crop.normalizeClamped", { width: result.rect.width, height: result.rect.height })
      : "";
  }

  /** Which of the four numeric fields is this input? `null` for anything else. */
  function fieldOfInput(input) {
    if (!dialog || !input) return null;
    if (input === dialog.xInput) return "x";
    if (input === dialog.yInput) return "y";
    if (input === dialog.wInput) return "width";
    if (input === dialog.hInput) return "height";
    return null;
  }

  /**
   * Commit the four numeric fields.
   *
   * With a ratio locked the width and the height are one value: the edited box drives and its
   * partner is derived (cropbox.applyNumeric), so a typed 800 in a 1:1 crop puts 800 in the other
   * box too instead of leaving a box that no longer has the ratio the preset promises. A size the
   * frame cannot hold is capped by the largest rectangle of that ratio that fits, and `commit`
   * writes the applied numbers back into the boxes - a box showing a value the crop did not take
   * is the one thing a numeric field must not do.
   */
  function readNumeric(edited, commit) {
    if (!dialog || !dialog.frame || !dialog.rect) return;
    setRect(
      applyNumeric(
        {
          x: integerOr(dialog.xInput.value, dialog.rect.x),
          y: integerOr(dialog.yInput.value, dialog.rect.y),
          width: Math.max(1, integerOr(dialog.wInput.value, dialog.rect.width)),
          height: Math.max(1, integerOr(dialog.hInput.value, dialog.rect.height)),
        },
        edited,
        currentRatio(),
        dialog.frame,
      ),
    );
    if (!commit || !dialog) return;
    dialog.xInput.value = String(dialog.rect.x);
    dialog.yInput.value = String(dialog.rect.y);
    dialog.wInput.value = String(dialog.rect.width);
    dialog.hInput.value = String(dialog.rect.height);
  }

  function onDialogKeyDown(event) {
    if (!dialog) return;
    if (event.key === "Escape") {
      event.preventDefault();
      endRun();
      return;
    }
    const tag = String((event.target && event.target.tagName) || "");
    const typing = ["INPUT", "SELECT", "TEXTAREA"].indexOf(tag) >= 0;
    if (event.key === "Enter") {
      if (typing && event.target !== dialog.aspectSelect) {
        // Enter inside a number box commits that box; the button is how the crop is confirmed.
        readNumeric(fieldOfInput(event.target), true);
        if (event.target.blur) event.target.blur();
        return;
      }
      event.preventDefault();
      void confirmCurrent();
      return;
    }
    if (typing || dialog.preview) return;
    if (event.key === " ") {
      // Space is the pan modifier. It is also a button's activation key, and the confirm button
      // holds the focus - so the default is cancelled here and on keyup, or a pan would crop.
      event.preventDefault();
      dialog.space = true;
      return;
    }
    if (event.key === "0") {
      event.preventDefault();
      resetView();
      return;
    }
    const step = event.shiftKey ? 10 : 1;
    const dx = event.key === "ArrowRight" ? step : event.key === "ArrowLeft" ? -step : 0;
    const dy = event.key === "ArrowDown" ? step : event.key === "ArrowUp" ? -step : 0;
    if (dx === 0 && dy === 0) return;
    event.preventDefault();
    // Alt resizes from the bottom-right corner, everything else moves the box: nudging by one
    // pixel is the keyboard's answer to the mouse's snapping.
    if (event.altKey) {
      setRect(resizeByHandle(dialog.rect, "se", dx, dy, {
        frame: dialog.frame,
        ratio: currentRatio(),
        minSize: CROP_MIN_SIZE,
      }));
      return;
    }
    setRect(moveBy(dialog.rect, dx, dy, dialog.frame));
  }

  function onDialogKeyUp(event) {
    if (!dialog) return;
    if (event.key === " ") {
      if (event.preventDefault) event.preventDefault();
      dialog.space = false;
    }
  }

  // ---- the run ----------------------------------------------------------
  function resetDialogFor(index) {
    dialog.index = index;
    dialog.errorLine.textContent = "";
    dialog.thumbnailFallback = false;
    // The rectangle is placed on the original pixels, not on the downscaled thumbnail cache:
    // zooming in has to show the detail the crop will actually keep (§4.17).
    dialog.image.src = rawUrl(dialog.items[index]);
  }

  /**
   * Load one image's frame and point the dialog at it.
   *
   * The rectangle the previous image was confirmed with is carried over: identical display
   * resolution gives the identical rectangle, a different one gives the same relative window
   * with the same ratio (cropbox.js propagate). The header is read first, so the resolution the
   * box is measured against is known before the pixels arrive - and it is the *display*
   * resolution, so an EXIF-rotated photo is cropped the way it is shown.
   */
  async function showImage(index) {
    const path = dialog.items[index];
    let meta = null;
    try {
      meta = await api.imageMeta(path);
    } catch (err) {
      meta = null;
      if (dialog) dialog.errorLine.textContent = t("crop.metaFailed", { message: describeError(err) });
    }
    if (!dialog) return;
    if (!meta) {
      // Without a size there is no honest rectangle to show: the previous image's numbers would
      // read as this image's box. So the box and every field are cleared and Confirm is refused -
      // Skip (or the end button) is the way out, and the next image gets its own frame.
      dialog.frame = null;
      dialog.rect = null;
      dialog.view = null;
      dialog.canvasSize = null;
      leavePreview();
      resetDialogFor(index);
      dialog.readout.textContent = "";
      dialog.wroteLine.textContent = "";
      for (const field of [dialog.xInput, dialog.yInput, dialog.wInput, dialog.hInput]) field.value = "";
      dialog.confirmButton.textContent = t("crop.confirm");
      setBusy(false);
      dialog.confirmButton.disabled = true;
      return;
    }
    const frame = frameOf(
      meta.display_width || meta.width,
      meta.display_height || meta.height,
    );
    const previousFrame = dialog.frame;
    const previousRect = dialog.rect;
    dialog.frame = frame;
    dialog.rect = previousRect && previousFrame
      ? propagate(previousRect, currentRatio(), previousFrame, frame)
      : defaultRect();
    // Every image starts fitted and centred: the box is carried over, but a view zoomed into the
    // last image's detail would open this one on a random corner.
    dialog.view = { zoom: 1, x: 0, y: 0 };
    leavePreview();
    resetDialogFor(index);
    paintDialog();
    setBusy(false);
  }

  /** The next image opens editable: the preview is an explicit check, not a mode of the run. */
  function leavePreview() {
    if (!dialog) return;
    dialog.preview = false;
    dialog.previewBox.checked = false;
    dialog.boxEl.hidden = false;
    dialog.stage.classList.toggle("is-preview", false);
  }

  function closeDialog() {
    if (!dialog) return;
    const finished = dialog;
    dialog = null;
    document.removeEventListener("keydown", onDialogKeyDown);
    document.removeEventListener("keyup", onDialogKeyUp);
    if (typeof window !== "undefined" && typeof window.removeEventListener === "function") {
      window.removeEventListener("resize", onWindowResize);
      window.removeEventListener("blur", onWindowBlur);
    }
    if (finished.backdrop && finished.backdrop.remove) finished.backdrop.remove();
    if (finished.restoreFocusTo && typeof finished.restoreFocusTo.focus === "function") {
      finished.restoreFocusTo.focus();
    }
  }

  /**
   * End the run. One path for all three endings - the last image was confirmed, the end button
   * was pressed, Esc was pressed - because each of them owes the user the same three things: the
   * summary, a refreshed directory (the archives are new files in it) and the optional tagger
   * job. Closing the dialog on its own would silently drop all three.
   */
  function endRun() {
    closeDialog();
    void finishRun();
  }

  async function confirmCurrent() {
    if (!dialog || dialog.busy) return;
    const path = dialog.items[dialog.index];
    const request = buildCropRequest({
      src: path,
      rect: dialog.rect,
      scale: dialog.scale,
      resolution: resolution(),
      processing: processing(),
    });
    setBusy(true);
    dialog.errorLine.textContent = "";
    try {
      const result = await api.cropApply(request);
      const archived = result && result.archived ? String(result.archived) : "";
      if (archived) run.archived.push(archived);
      // A retry that worked is not a failure: counting the first attempt would report an image
      // as failed after the user fixed it, and the summary is the only place that number appears.
      run.failed = run.failed.filter((item) => item.path !== path);
      dialog.lastWrite = archived
        ? t("crop.wrote", {
            name: baseName(archived),
            width: Number(result.output_width) || 0,
            height: Number(result.output_height) || 0,
          })
        : "";
      const warnings = (result && result.warnings) || [];
      advance(warnings.join(" / "));
    } catch (err) {
      // Stay on this image: the rectangle is still on screen and Confirm is the retry.
      run.failed.push({ path, message: describeError(err) });
      dialog.errorLine.textContent = t("crop.writeFailed", { message: describeError(err) });
      setBusy(false);
    }
  }

  function advance(note) {
    if (!dialog) return;
    if (dialog.index + 1 >= dialog.items.length) {
      endRun();
      return;
    }
    void showImage(dialog.index + 1).then(() => {
      if (dialog && note) dialog.errorLine.textContent = note;
    });
  }

  function skipCurrent() {
    if (!dialog || dialog.busy) return;
    advance("");
  }

  async function startRun() {
    const paths = scopePaths();
    if (paths.length === 0) {
      notify(t("crop.noImages"), "warn");
      return;
    }
    run = { paths, archived: [], failed: [] };
    summary.textContent = "";
    tagLine.textContent = "";
    errorsBox.replaceChildren();
    progress.hidden = true;

    dialog = Object.assign(buildDialog(), {
      items: paths,
      index: 0,
      frame: null,
      rect: null,
      scale: scaled,
      aspectId,
      busy: false,
      preview: false,
      thumbnailFallback: false,
      drag: null,
      space: false,
      view: null,
      canvasSize: null,
      lastWrite: "",
    });
    dialog.stage.addEventListener("pointerdown", onPointerDown);
    dialog.stage.addEventListener("pointermove", onPointerMove);
    dialog.stage.addEventListener("pointerup", onPointerUp);
    dialog.stage.addEventListener("pointercancel", onPointerUp);
    // Not passive: the wheel must not scroll the page behind the dialog while it zooms.
    dialog.stage.addEventListener("wheel", onWheel, { passive: false });
    dialog.aspectSelect.addEventListener("change", () => {
      if (!dialog) return;
      dialog.aspectId = dialog.aspectSelect.value;
      aspectId = dialog.aspectId;
      applyAspect();
    });
    // One answer for "was scaling asked for" (§4.17): the dialog's switch writes the panel's, so
    // the rest of the run and the next run start from the choice made here - the same rule the
    // aspect preset already follows.
    dialog.scaleBox.addEventListener("change", () => {
      if (!dialog) return;
      scaled = dialog.scaleBox.checked === true;
      dialog.scale = scaled;
      scaleField.box.checked = scaled;
      paintOptions();
      // Without a frame (the metadata read failed) there is no readout to correct; the switch
      // still holds the value the next image will be confirmed with.
      if (dialog.frame && dialog.rect) paintReadout();
    });
    dialog.previewBox.addEventListener("change", () => {
      if (!dialog) return;
      setPreview(dialog.previewBox.checked === true);
    });
    dialog.normalizeButton.addEventListener("click", () => normalize());
    dialog.resetButton.addEventListener("click", () => setRect(defaultRect()));
    for (const input of [dialog.xInput, dialog.yInput, dialog.wInput, dialog.hInput]) {
      // `input` is the live half: the partner box follows while the number is typed, which is
      // what "the two boxes are locked" means. `change` commits (Enter or leaving the field) and
      // writes the applied values back, including a size the frame had to cap.
      input.addEventListener("input", () => readNumeric(fieldOfInput(input), false));
      input.addEventListener("change", () => readNumeric(fieldOfInput(input), true));
    }
    dialog.finishButton.addEventListener("click", () => endRun());
    dialog.skipButton.addEventListener("click", () => skipCurrent());
    dialog.confirmButton.addEventListener("click", () => { void confirmCurrent(); });
    dialog.image.addEventListener("load", () => paintDialog());
    dialog.image.addEventListener("error", () => {
      if (!dialog) return;
      // A source the browser cannot decode falls back to the preview thumbnail once - the
      // geometry is proportional, so the box still describes the same rectangle - and only a
      // second failure is reported. The box is drawn against the image that failed to arrive,
      // so a plausible-looking box over a missing image is exactly what must not stay on screen.
      if (dialog.thumbnailFallback !== true) {
        dialog.thumbnailFallback = true;
        dialog.image.src = thumbUrl(dialog.items[dialog.index], THUMB_SIZE.PREVIEW);
        return;
      }
      const name = baseName(dialog.items[dialog.index]);
      dialog.errorLine.textContent = t("crop.previewFailed", { name });
      dialog.confirmButton.disabled = true;
    });
    document.addEventListener("keydown", onDialogKeyDown);
    document.addEventListener("keyup", onDialogKeyUp);
    if (typeof window !== "undefined" && typeof window.addEventListener === "function") {
      window.addEventListener("resize", onWindowResize);
      window.addEventListener("blur", onWindowBlur);
    }
    paintScope();
    dialog.confirmButton.focus();
    await showImage(0);
  }

  /** The dialog is over: say what happened, refresh the gallery, then run the tagger if asked. */
  async function finishRun() {
    const finished = run;
    run = null;
    paintScope();
    const archived = finished ? finished.archived.length : 0;
    const failed = finished ? finished.failed.length : 0;
    if (finished) {
      summary.textContent = t("crop.summary", { archived, failed });
      for (const item of finished.failed.slice(0, CROP_ERROR_ROWS)) {
        errorsBox.append(make("div", "mono", baseName(item.path) + " - " + item.message));
      }
      if (failed > CROP_ERROR_ROWS) {
        errorsBox.append(make("div", "hint", t("crop.errorsMore", { n: failed - CROP_ERROR_ROWS })));
      }
    }
    // The archives are new files in the directory on screen: the gallery, the caption probe and
    // the frequency table are all stale now. The app owns that refresh.
    if (archived > 0 && onArchived) onArchived();
    if (archived > 0 && tagging) await startTagJob(finished.archived);
  }

  // ---- the tagger step (a reuse of section 4.4) --------------------------
  function closeTagStream() {
    clearWatchdog();
    if (shelfJob) {
      shelfJob.finish();
      shelfJob = null;
    }
    if (runningJobId) {
      forgetJob("crop", runningJobId);
      runningJobId = null;
    }
    if (tagJob && tagJob.stream) tagJob.stream.close();
    tagJob = null;
    cancelButton.hidden = true;
    cancelButton.disabled = false;
    paintScope();
  }

  function clearWatchdog() {
    if (watchdog !== null) {
      window.clearTimeout(watchdog);
      watchdog = null;
    }
  }

  /** A cancel the server accepted is half the story; without a terminal event the panel would stay locked. */
  function armWatchdog() {
    clearWatchdog();
    watchdog = window.setTimeout(() => {
      watchdog = null;
      if (!tagJob || tagJob.finished) return;
      closeTagStream();
      notify(t("crop.tagCancelTimeout"), "warn");
    }, CANCEL_WATCHDOG_MS);
  }

  function paintTagProgress(data) {
    const done = Number(data.done) || 0;
    const total = Number(data.total) || 0;
    const pct = total > 0 ? Math.min(100, Math.round((100 * done) / total)) : 0;
    progress.hidden = false;
    progressFill.style.width = pct + "%";
    progressFill.setAttribute("aria-valuenow", String(pct));
    progressText.textContent = t("crop.tagProgress", { done, total });
    if (shelfJob) shelfJob.update({ done, total, current: data.current });
  }

  function renderErrors(itemList) {
    const list = Array.isArray(itemList) ? itemList : [];
    errorsBox.replaceChildren();
    if (list.length === 0) return;
    errorsBox.append(make("strong", "", t("crop.errorsTitle", { n: list.length })));
    for (const item of list.slice(0, CROP_ERROR_ROWS)) {
      const path = item && item.path ? String(item.path) : "";
      const message = item && item.message ? String(item.message) : "";
      errorsBox.append(make("div", "mono", path.length > 0 ? baseName(path) + " - " + message : message));
    }
  }

  /**
   * Tag the images this run archived, with the tagger panel's current settings.
   *
   * The request is section 4.4's own body minus nothing: images, model, thresholds, postprocess
   * and write_mode are exactly what the tagger panel sends, so this is the same code path the
   * Tagger tab uses and not a second tagging implementation.
   */
  async function startTagJob(paths) {
    const params = taggerParams() || {};
    const payload = {
      images: paths.slice(),
      model: params.model || null,
      thresholds: params.thresholds || {},
      postprocess: params.postprocess || {},
      write_mode: params.write_mode || "backup_overwrite",
    };
    tagLine.textContent = t("crop.tagStarting", { n: paths.length });
    try {
      const result = await api.autotagRun(payload);
      const jobId = result && (result.job_id || result.jobId);
      if (!jobId) throw new Error(t("common.noJobId"));
      openTagStream(jobId, Number(result.total) || paths.length);
    } catch (err) {
      tagLine.textContent = "";
      notify(t("crop.tagFailed", { message: describeError(err) }), "error");
    }
  }

  function openTagStream(jobId, total) {
    tagJob = { jobId: String(jobId), finished: false, stream: null };
    runningJobId = tagJob.jobId;
    rememberJob({ panel: "crop", jobId: tagJob.jobId, total: Number(total) || 0 });
    streamErrors = 0;
    shelfJob = shelf
      ? shelf.start({
          kind: "crop",
          label: t("tab.crop"),
          cancelLabel: t("crop.cancelTag"),
          total: Number(total) || 0,
          cancel: () => { void cancelTag(); },
        })
      : null;
    cancelButton.hidden = false;
    cancelButton.disabled = false;
    cancelButton.textContent = t("crop.cancelTag");
    paintTagProgress({ done: 0, total });
    const stream = new EventSource(autotagStreamUrl(tagJob.jobId));
    tagJob.stream = stream;
    stream.addEventListener("progress", (event) => {
      streamErrors = 0;
      const data = parseData(event.data);
      // One SSE carries tagging and downloads (section 4.7); this job is always an infer job.
      if (data.phase && data.phase !== "infer") return;
      paintTagProgress(data);
      renderErrors(data.errors);
    });
    stream.addEventListener("done", (event) => {
      const payload = parseData(event.data);
      if (payload.finished !== true) return;
      finishTagJob(payload);
    });
    stream.onerror = () => {
      if (!tagJob || tagJob.finished) return;
      streamErrors += 1;
      if (streamErrors >= STREAM_ERROR_LIMIT) {
        notify(t("crop.tagStreamLost"), "error");
        closeTagStream();
        if (onArchived) onArchived();
      }
    };
  }

  function finishTagJob(payload) {
    if (!tagJob) return;
    tagJob.finished = true;
    const failed = ((payload && payload.errors) || []).length;
    closeTagStream();
    renderErrors(payload ? payload.errors : []);
    tagLine.textContent = payload && payload.cancelled
      ? t("crop.tagCancelled")
      : t("crop.tagDone", { tagged: Number(payload && payload.done) || 0, failed });
    progressFill.style.width = "100%";
    progressFill.setAttribute("aria-valuenow", "100");
    if (failed > 0) notify(t("crop.tagDoneFailed", { n: failed }), "warn");
    else if (payload && payload.cancelled) notify(t("crop.tagCancelled"), "warn");
    else notify(t("crop.tagOk"), "ok");
    // Captions changed: the caption probe and the frequency table are stale.
    if (onArchived) onArchived();
  }

  async function cancelTag() {
    if (!tagJob || tagJob.finished) return;
    cancelButton.disabled = true;
    cancelButton.textContent = t("crop.cancelling");
    notify(t("crop.cancelRequested"), "info");
    let accepted = true;
    try {
      const answer = await api.autotagCancel(tagJob.jobId);
      // "cancelled: false" means the job was already gone; nothing will ever send done for it.
      accepted = !(answer && answer.cancelled === false);
    } catch (err) {
      notify(t("crop.tagFailed", { message: describeError(err) }), "error");
    }
    if (!accepted) {
      closeTagStream();
      notify(t("crop.cancelAlreadyGone"), "warn");
      return;
    }
    armWatchdog();
  }

  // ---- wiring -----------------------------------------------------------
  scaleField.box.addEventListener("change", () => {
    scaled = scaleField.box.checked;
    paintOptions();
  });
  tagField.box.addEventListener("change", () => {
    tagging = tagField.box.checked;
    paintOptions();
  });
  startButton.addEventListener("click", () => { void startRun(); });
  cancelButton.addEventListener("click", () => { void cancelTag(); });
  // The strip is above the tabs, so it changes while this panel is the open one: picking images in
  // the gallery, changing the filter and opening another directory all repaint it. Without this
  // hook the line and the Start gate would only catch up after leaving the tab and coming back -
  // a control that is live everywhere but here. (The export panel had the same bug, one step
  // worse: it did not read the strip at all until 2026-09-20.)
  if (scope && typeof scope.subscribe === "function") {
    scope.subscribe({ onPaint: paintScope, onChange: paintScope });
  }

  if (typeof getParams === "function" && getParams()) {
    // The inherited resolution and pipeline settings are painted, not owned: repaint on any
    // change to the store (the scaling panel's own controls write to it).
    getParams().subscribe(() => {
      paintOptions();
      if (dialog) paintReadout();
    });
  }
  paintLabels();
  paintOptions();
  paintScope();

  if (config.tabsHost) config.tabsHost.append(tab);
  if (config.panelHost) config.panelHost.append(panel);
  else targetHost.append(panel);

  return {
    relabel() {
      paintLabels();
      paintOptions();
      paintScope();
    },
    //: The command palette runs this - the very function the panel's own button calls.
    start: startRun,
    cancel: cancelTag,
    status: () => ({
      running: tagJob !== null,
      canStart: !startButton.disabled,
      images: scopePaths().length,
    }),
    /** Pick a tag job back up after a page reload; its stream replays the whole event log. */
    resume(jobId, total) {
      progress.hidden = false;
      openTagStream(jobId, Number(total) || 0);
    },
    onShow() {
      paintOptions();
      paintScope();
    },
    close() {
      // A hard teardown: the run is dropped rather than finished, so no tag job starts here.
      closeDialog();
      run = null;
      closeTagStream();
    },
  };
}
