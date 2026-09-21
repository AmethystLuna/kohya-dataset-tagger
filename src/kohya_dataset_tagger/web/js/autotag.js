/**
 * Tagger panel: model choice, image scope, thresholds, diff preview and the
 * SSE progress stream.
 *
 * Contract (p0-spec section 4, download fields added by section 4.7):
 *   GET  /api/autotag/models   -> {models:[{id,available,onnx_path,vocab_path,description,
 *                                         installed,size_bytes,downloadable}]}
 *   POST /api/autotag/preview  -> {items:[{image,before,after,tags:[{tag,score,category}]}]}
 *   POST /api/autotag/run      -> {job_id}
 *   POST /api/autotag/download -> {job_id}
 *   GET  /api/autotag/stream?job_id=  SSE: {phase,done,total,current,errors[]}
 *
 * Section 4.7 reuses the inference stream for downloads instead of adding a
 * second stream: both carry "phase", and done/total switch unit with it -
 * bytes while phase is "download", images while it is "infer". Every progress
 * paint therefore goes through phaseOf(); showing a byte count as an image
 * count is exactly the confusion that field exists to prevent.
 *
 * Section 4.1 freezes the sub-shape of thresholds / postprocess as well; this list
 * mirrors it verbatim (defaults included) and is checked by
 * test/test_web_contract.py::test_frozen_postprocess_keys_are_exposed:
 *
 *   thresholds:  {general:0.35, character:0.35, copyright:0.35, artist:0.35, meta:0.35}
 *   postprocess: {remove_underscore:true, undesired_tags:[], always_first_tags:[],
 *                 character_tags_first:false, character_tag_expand:false,
 *                 escape_parens:true, rating:"none"}
 *   rating:      "none" | "first" | "last"
 *
 * /api/autotag/preview must not write to disk; nothing here writes either - the
 * only write call is /api/autotag/run behind an explicit confirmation.
 */

import { api, WRITE_MODE, thumbUrl, autotagStreamUrl, THUMB_SIZE } from "./api.js";
import { forgetJob, rememberJob } from "./jobstore.js";
import { hasKey, t } from "./strings.js";

// Defaults are frozen by p0-spec section 4.1: every category is 0.35, matching
// sd-scripts --thresh. Raising character/copyright silently drops legitimate
// character tags and makes the output impossible to compare with
// finetune/tag_images_by_wd14_tagger.py.
const CATEGORIES = [
  { key: "general", default: 0.35 },
  { key: "character", default: 0.35 },
  { key: "copyright", default: 0.35 },
  { key: "artist", default: 0.35 },
  { key: "meta", default: 0.35 },
];

//: escape_parens sits next to remove_underscore on purpose: both rewrite the text
//: that lands in the .txt, and section 4.1 makes both default to true.
const OPTIONS = [
  { key: "remove_underscore", stringKey: "autotag.opt.removeUnderscore", default: true },
  {
    key: "escape_parens",
    stringKey: "autotag.opt.escapeParens",
    hintKey: "autotag.opt.escapeParensHint",
    default: true,
  },
  { key: "character_tags_first", stringKey: "autotag.opt.characterTagsFirst", default: false },
  { key: "character_tag_expand", stringKey: "autotag.opt.characterTagExpand", default: false },
];

const RATING_MODES = [
  { value: "none", stringKey: "autotag.rating.none" },
  { value: "first", stringKey: "autotag.rating.first" },
  { value: "last", stringKey: "autotag.rating.last" },
];

const WRITE_MODES = [
  { value: WRITE_MODE.BACKUP_OVERWRITE, stringKey: "autotag.write.backupOverwrite" },
  { value: WRITE_MODE.OVERWRITE, stringKey: "autotag.write.overwrite" },
  { value: WRITE_MODE.APPEND, stringKey: "autotag.write.append" },
  { value: WRITE_MODE.PREPEND, stringKey: "autotag.write.prepend" },
  { value: WRITE_MODE.ONLY_IF_EMPTY, stringKey: "autotag.write.onlyIfEmpty" },
  { value: WRITE_MODE.MERGE, stringKey: "autotag.write.merge" },
];

//: Write modes that leave a backup before overwriting. The confirmation and the
//: hint must follow this set: when backup_overwrite became the default the old
//: confirmation still claimed "no automatic backup", which was simply false.
const BACKUP_WRITE_MODES = new Set([WRITE_MODE.BACKUP_OVERWRITE]);

const STREAM_ERROR_LIMIT = 3;
//: section 4.4: after a cancel request the backend finishes the current image and
//: still sends the final done event - if that never arrives, stop waiting.
const CANCEL_WATCHDOG_MS = 30000;
//: Section 4.5: /api/autotag/preview is synchronous (~0.8 s per image) and the
//: backend rejects more than 32 images with 400 too_many_images. The click is
//: blocked client side first so it never looks dead; whole-directory tagging
//: goes to /run instead (behind an explicit confirmation).
const PREVIEW_MAX_IMAGES = 32;
const DIFF_TAG_LIMIT = 24;

//: Section 4.7 gives the shared SSE stream a "phase" field with exactly two
//: values. Anything else means a backend that predates the field, and then the
//: kind of job that opened the stream decides the unit.
const PHASE_DOWNLOAD = "download";
const PHASE_INFER = "infer";

//: Size unit convention: 1024-based steps under the KB/MB/GB labels.
//: Two concrete reasons. (a) p0-spec section 4.6 and the request for this panel
//: both quote the eva02 onnx as "1.26 GB", which is 1.26 GiB - a 1024 divisor
//: printed with a GB label. (b) app.js:formatBytes already divides by 1024
//: while labelling MB, so a second, decimal convention inside the same panel
//: would print two different numbers for one file. One convention, stated once
//: here; test/test_web_contract.py pins it so the two cannot drift apart.
const SIZE_UNITS = ["B", "KB", "MB", "GB", "TB"];
const SIZE_STEP = 1024;

/**
 * Format a byte count for display. size_bytes is 0 in the manifest when the
 * backend does not know the size (section 4.7), so <= 0 renders a placeholder
 * instead of a fake "0 B".
 */
export function formatSize(bytes) {
  const value = Number(bytes);
  if (!Number.isFinite(value) || value <= 0) return t("autotag.sizeUnknown");
  let index = 0;
  let scaled = value;
  while (scaled >= SIZE_STEP && index < SIZE_UNITS.length - 1) {
    scaled /= SIZE_STEP;
    index += 1;
  }
  return scaled.toFixed(index === 0 ? 0 : 2) + " " + SIZE_UNITS[index];
}

/**
 * Section 4.7 froze "installed"; "available" is the same fact under its
 * pre-download name (an onnx plus a vocabulary csv found next to it).
 */
function isInstalled(model) {
  if (!model) return false;
  if (typeof model.installed === "boolean") return model.installed;
  return model.available === true;
}

function splitCaption(text) {
  if (!text) return [];
  return String(text).split(",").map((part) => part.trim()).filter((part) => part.length > 0);
}

function diffTags(before, after) {
  const beforeSet = new Set(splitCaption(before));
  const afterSet = new Set(splitCaption(after));
  const added = Array.from(afterSet).filter((tag) => !beforeSet.has(tag));
  const removed = Array.from(beforeSet).filter((tag) => !afterSet.has(tag));
  return { added, removed };
}

/**
 * Render caption tags into `cell` as chips separated by the caption separator
 * (", " - the same string the trainer splits on).
 *
 * classFor returns "add" / "del" / null, and that is the whole point of the
 * view: added tags are boxed green, removed ones red.
 */
function appendTagChips(cell, tags, classFor) {
  tags.forEach((tag, index) => {
    cell.append(el("span", classFor(tag), tag));
    if (index < tags.length - 1) cell.append(document.createTextNode(", "));
  });
}

/**
 * Fill the before/after cells of one preview row.
 *
 * Both sides matter: the right column shows what would be written, but the
 * left one shows what would be LOST. Removed tags used to be invisible (the
 * before cell was a single plain text node), so the table could only ever
 * look like pure addition.
 *
 * Exported so the headless harness (test/fixtures/diff_harness.mjs) can drive
 * it directly. A static test could only prove that the strings "add"/"del"
 * appear somewhere, never which chip got which class.
 */
export function renderCaptionDiff(cellBefore, cellAfter, beforeText, afterText) {
  const beforeTags = splitCaption(beforeText);
  const afterTags = splitCaption(afterText);
  const beforeSet = new Set(beforeTags);
  const afterSet = new Set(afterTags);
  cellBefore.replaceChildren();
  cellAfter.replaceChildren();
  if (beforeTags.length === 0) {
    cellBefore.textContent = t("autotag.diffNone");
  } else {
    appendTagChips(cellBefore, beforeTags, (tag) => (afterSet.has(tag) ? null : "del"));
  }
  if (afterTags.length === 0) {
    // Clearing a caption that exists is a deletion, so the "none" is boxed red.
    // With nothing on the left either there is nothing to lose: neutral text.
    if (beforeTags.length > 0) cellAfter.append(el("span", "cleared", t("autotag.diffNone")));
    else cellAfter.textContent = t("autotag.diffNone");
  } else {
    appendTagChips(cellAfter, afterTags, (tag) => (beforeSet.has(tag) ? null : "add"));
  }
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

export function createAutotagPanel(options) {
  const modelSelect = options.modelSelect;
  const modelNote = options.modelNote;
  //: The column's scope control (scope.js), built once in app.js and shared with the batch panel.
  const scope = options.scope;
  const thresholdsEl = options.thresholds;
  const optionsEl = options.options;
  const undesiredEl = options.undesired;
  const alwaysFirstEl = options.alwaysFirst;
  const writeModeSelect = options.writeModeSelect;
  const previewButton = options.previewButton;
  const runButton = options.runButton;
  const cancelButton = options.cancelButton;
  const progressEl = options.progress;
  const progressFill = options.progressFill;
  const progressText = options.progressText;
  const errorsEl = options.errors;
  const diffTitle = options.diffTitle;
  const diffSummary = options.diffSummary;
  const diffEl = options.diff;
  const notify = options.notify || (() => {});
  const onAfterWrite = options.onAfterWrite || (() => {});
  const confirmFn = options.confirm || ((message) => window.confirm(message));

  // Section 4.7 model list. index.html belongs to another workstream, so the
  // list is built here and inserted next to the frozen #autotag-model-note
  // paragraph rather than adding markup or ids of its own.
  const modelListEl = el("div", "model-list");
  if (modelNote && modelNote.parentNode) {
    modelNote.parentNode.insertBefore(modelListEl, modelNote.nextSibling);
  }

  const thresholdInputs = new Map();
  const optionInputs = new Map();
  let ratingSelect = null;
  let models = [];
  let preview = null;
  let previewSignature = "";
  //: the staleness warning currently on screen ("" when the diff is in sync); held as the
  //: text rather than a boolean so reprinting the table cannot silently drop it
  let diffStaleText = "";
  let stream = null;
  let streamErrors = 0;
  let running = false;
  let currentJobId = null;
  let cancelWatchdog = null;
  //: a cancel the server accepted, still waiting for the terminal event
  let cancelling = false;
  //: which kind of job owns the shared SSE channel right now (or null)
  let taskKind = null;
  //: the model being downloaded; selected again once it reports installed
  let pendingModelId = null;
  //: whether /api/autotag/models has ever answered, so relabel() can tell
  //: "not loaded yet" (no note at all) from "loaded, nothing installed" (the
  //: frozen "no model" line).
  let modelsLoaded = false;
  //: the listing request itself failed; without this the panel shows the same empty
  //: list as "nothing is installed" once the toast has retired.
  let modelsFailed = false;
  //: The job shelf (js/jobs.js); optional so the panel still runs headless without one.
  const shelf = options.jobs || null;
  //: This job's row on the shelf, so a run stays visible (and cancellable) from every tab.
  let shelfJob = null;
  //: The id this tab bookmarked, so a reload can pick the run back up (jobstore.js).
  let runningJobId = null;
  //: every node painted from t() at build time; relabel() re-paints these
  //: instead of re-running the builders, which would append a second copy of
  //: each control.
  const textBindings = [];

  /** Paint one remembered binding from the pack currently in effect. */
  function paintTextBinding(binding) {
    binding.node[binding.prop] = t(binding.key, binding.params);
  }

  /**
   * Paint node[prop] (textContent by default) from t(key, params) now and
   * remember it for relabel(). Builders register their copy here instead of
   * calling t() directly, which keeps the build-time lookups in one place.
   */
  function bindText(node, key, params, prop) {
    const binding = { node, key, params, prop: prop || "textContent" };
    textBindings.push(binding);
    paintTextBinding(binding);
    return node;
  }

  /** Re-apply every build-time binding from the pack currently in effect. */
  function paintTextBindings() {
    for (const binding of textBindings) paintTextBinding(binding);
  }

  function buildThresholds() {
    for (const category of CATEGORIES) {
      const label = el("label");
      label.append(bindText(el("span"), "autotag.category." + category.key));
      const input = document.createElement("input");
      input.type = "number";
      input.min = "0";
      input.max = "1";
      input.step = "0.05";
      input.value = String(category.default);
      input.addEventListener("input", markStale);
      label.append(input);
      thresholdInputs.set(category.key, input);
      thresholdsEl.append(label);
    }
  }

  function buildOptions() {
    for (const option of OPTIONS) {
      const label = el("label");
      const input = document.createElement("input");
      input.type = "checkbox";
      input.checked = option.default;
      input.addEventListener("change", markStale);
      label.append(input, bindText(el("span"), option.stringKey));
      if (option.hintKey) bindText(label, option.hintKey, null, "title");
      optionInputs.set(option.key, input);
      optionsEl.append(label);
    }
    const ratingLabel = el("label");
    ratingSelect = document.createElement("select");
    for (const mode of RATING_MODES) {
      const opt = document.createElement("option");
      opt.value = mode.value;
      bindText(opt, mode.stringKey);
      ratingSelect.append(opt);
    }
    ratingSelect.value = "none";
    ratingSelect.addEventListener("change", markStale);
    ratingLabel.append(bindText(el("span"), "autotag.ratingMode"), ratingSelect);
    optionsEl.append(ratingLabel);
  }

  let writeModeHintEl = null;
  let scopeNoteEl = null;

  /**
   * Explain the selected write mode. Only backup modes get a hint: it must never
   * deny a backup that will happen, nor promise one that will not.
   */
  function paintWriteModeHint() {
    if (!writeModeHintEl) return;
    const key = BACKUP_WRITE_MODES.has(writeModeSelect.value) ? "autotag.writeHintBackup" : "";
    writeModeHintEl.textContent = key ? t(key) : "";
    writeModeHintEl.hidden = !key;
  }

  function buildWriteModes() {
    for (const mode of WRITE_MODES) {
      const opt = document.createElement("option");
      opt.value = mode.value;
      bindText(opt, mode.stringKey);
      writeModeSelect.append(opt);
    }
    writeModeSelect.value = WRITE_MODE.BACKUP_OVERWRITE;
    writeModeSelect.addEventListener("change", () => { paintWriteModeHint(); markStale(); });
    // The hint goes after the <label>, not inside it.
    writeModeHintEl = el("p", "hint");
    const field = writeModeSelect.parentNode;
    if (field && field.parentNode) field.parentNode.insertBefore(writeModeHintEl, field.nextSibling);
    paintWriteModeHint();
  }

  // One scope for the whole column: the same instance the batch panel reads, so "which images?"
  // has one answer and switching panels cannot change it. `scopePaths()` has to stay SYNCHRONOUS -
  // the request body is assembled on click - which is why the recursive listing is cached inside
  // the control instead of being awaited here.
  scope.subscribe({
    // Repainting the counts is also when the two buttons have to be re-evaluated: Run through the
    // preview signature and the cap, Preview through the cap itself.
    onPaint: () => { refreshDiffStale(); refreshActionButtons(); },
  });

  function scopePaths() {
    return scope.paths();
  }

  /** Public entry: repaint the counts after a directory or selection change, and load the
   *  "with subdirectories" listing while that scope is selected (the control does both). */
  function refreshScopes() {
    // The scope is what the diff describes, so a new directory or a new selection makes it stale even
    // though every setting in its signature is unchanged - the control's paint hook says so.
    scope.refresh();
  }
  function collectThresholds() {
    const out = {};
    for (const [key, input] of thresholdInputs) {
      const value = Number(input.value);
      out[key] = Number.isFinite(value) ? value : 0;
    }
    return out;
  }

  function collectPostprocess() {
    const out = {};
    for (const [key, input] of optionInputs) out[key] = input.checked;
    out.rating = ratingSelect ? ratingSelect.value : "none";
    out.undesired_tags = splitCaption(undesiredEl.value);
    out.always_first_tags = splitCaption(alwaysFirstEl.value);
    return out;
  }

  function currentModel() {
    return modelSelect.value || null;
  }

  function signature(paths) {
    return JSON.stringify({
      model: currentModel(),
      thresholds: collectThresholds(),
      postprocess: collectPostprocess(),
      // The write mode is deliberately absent: /preview reviews what the tagger produces
      // (§4.5 (1) lists its request body without one), and the mode only decides how /run
      // composes that with what is on disk. Putting it here would claim "the settings
      // changed - preview again" on a mode switch and lock Run over unchanged output.
      images: paths,
    });
  }

  /**
   * Run stays enabled when a fresh preview exists, or when the scope is larger
   * than the preview cap - in that case /run is the only path (section 4.5) and
   * it is guarded by a confirmation that says the diff is being skipped.
   */
  function refreshRunButton() {
    if (running) {
      runButton.disabled = true;
      return;
    }
    // A preview that found nothing to tag is not "no preview yet": /run would only
    // answer autotag.previewEmpty, so the button must not offer it.
    if (preview && preview.items.length === 0) {
      runButton.disabled = true;
      return;
    }
    if (preview && previewSignature === signature(preview.paths)) {
      runButton.disabled = false;
      return;
    }
    runButton.disabled = scopePaths().length <= PREVIEW_MAX_IMAGES;
  }

  /**
   * Inference and download jobs share the one SSE channel this panel opens, so
   * a running job of either kind locks the other actions out. Centralised in
   * one place so a cancel, a lost stream or a failed download cannot leave a
   * button stale.
   */
  /** The preview endpoint is synchronous and rejects more than PREVIEW_MAX_IMAGES (§4.5 (2)). */
  function previewTooLarge() {
    return scopePaths().length > PREVIEW_MAX_IMAGES;
  }

  /**
   * Say why preview is unavailable instead of letting the click find out. The backend
   * 400s an over-cap preview, and a button that can only fail is worse than a disabled
   * one that names the way through - whole-directory tagging is /run's job.
   */
  function paintScopeNote() {
    if (!scopeNoteEl) return;
    const n = scopePaths().length;
    const tooMany = n > PREVIEW_MAX_IMAGES;
    scopeNoteEl.textContent = tooMany ? t("autotag.previewTooManyNote", { n, max: PREVIEW_MAX_IMAGES }) : "";
    scopeNoteEl.hidden = !tooMany;
  }

  function refreshActionButtons() {
    previewButton.disabled = running || !models.some(isInstalled) || previewTooLarge();
    paintScopeNote();
    refreshRunButton();
  }

  /**
   * Whether the diff on screen still describes the images the panel would act on.
   *
   * signature() is signed with the preview's own paths, so on its own it can only
   * notice a settings change (another model, another threshold). A directory or a
   * selection change leaves that signature identical while the panel now points at a
   * different set of images, so the scope has to be compared separately.
   */
  /**
   * Why the diff no longer applies, or "" when it still does. The two causes are kept
   * apart because they mean different things to the reader: a settings change keeps the
   * old preview valid in shape, a scope change means the table is about other images.
   */
  function diffStaleMessage() {
    if (!preview) return "";
    if (!previewCoversScope()) return t("autotag.diffStaleScope");
    if (previewSignature !== signature(preview.paths)) return t("autotag.diffStale");
    return "";
  }

  function previewCoversScope() {
    if (!preview) return false;
    const now = scopePaths();
    if (now.length !== preview.paths.length) return false;
    for (let index = 0; index < now.length; index += 1) {
      if (now[index] !== preview.paths[index]) return false;
    }
    return true;
  }

  /**
   * Section 4.5: an existing preview stops describing what would be written as
   * soon as the inputs it was taken with change. Extracted from markStale() so
   * relabel() can keep the warning while repainting the diff table.
   *
   * Painted only when the answer flips: the gallery probes one caption at a time and
   * re-runs this per chunk, so rebuilding the table on every probe would reintroduce
   * the O(N^2) the caption probe exists to avoid.
   */
  function refreshDiffStale() {
    const message = diffStaleMessage();
    // Per-probe calls must stay cheap: the table is only rebuilt when the answer flips.
    if (message === diffStaleText) return;
    diffStaleText = message;
    if (message) {
      diffSummary.textContent = message;
      return;
    }
    // Back in sync: the table's own summary line replaces the warning.
    renderDiff(preview.paths);
  }

  function markStale() {
    // paint() runs the control's own paint hook, which is where the diff warning and the two
    // buttons are re-evaluated.
    scope.paint();
  }

  function buildPayload(paths) {
    return {
      images: paths,
      model: currentModel(),
      thresholds: collectThresholds(),
      postprocess: collectPostprocess(),
    };
  }

  // ---- diff table --------------------------------------------------------
  function renderDiff(paths) {
    diffEl.replaceChildren();
    const items = (preview && preview.items) || [];
    diffTitle.hidden = items.length === 0;
    if (items.length === 0) {
      diffSummary.textContent = "";
      return;
    }
    let added = 0;
    let removed = 0;
    const table = document.createElement("table");
    const head = document.createElement("tr");
    for (const key of ["autotag.diffHead.image", "autotag.diffHead.before", "autotag.diffHead.after", "autotag.diffHead.tags"]) {
      head.append(el("th", null, t(key)));
    }
    table.append(head);
    for (const item of items) {
      const delta = diffTags(item.before, item.after);
      added += delta.added.length;
      removed += delta.removed.length;
      const row = document.createElement("tr");
      row.title = item.image;

      const cellImage = document.createElement("td");
      // The row is identified by a title attribute otherwise, which a screen
      // reader never reads: name the file in the cell itself.
      cellImage.append(el("span", "sr-only", String(item.image)));
      const img = document.createElement("img");
      img.className = "diff-img";
      img.loading = "lazy";
      img.decoding = "async";
      img.width = 48;
      img.height = 48;
      img.alt = "";
      img.src = thumbUrl(item.image, THUMB_SIZE.GRID);
      cellImage.append(img);

      const cellBefore = el("td", "before");
      const cellAfter = el("td", "after");
      renderCaptionDiff(cellBefore, cellAfter, item.before, item.after);

      const cellTags = el("td", "tags");
      const tags = Array.isArray(item.tags) ? item.tags.slice(0, DIFF_TAG_LIMIT) : [];
      cellTags.textContent = tags
        .map((tag) => tag.tag + " " + Number(tag.score).toFixed(2) + " " + (tag.category || ""))
        .join(" | ");

      row.append(cellImage, cellBefore, cellAfter, cellTags);
      table.append(row);
    }
    diffEl.append(table);
    diffSummary.textContent = t("autotag.previewCount", { n: items.length, added, removed });
    void paths;
  }

  /**
   * Repaint the diff table and its summary from the preview already in memory.
   * renderDiff() owns that DOM, so it is reused rather than re-deriving the
   * rows; the stale warning is re-applied afterwards because renderDiff()
   * always prints the "N changed" summary line.
   */
  function paintDiff() {
    renderDiff(preview ? preview.paths : []);
    // renderDiff() has just reprinted the summary line, so the warning on screen is gone
    // whether or not it still applies: re-derive it instead of trusting the held text.
    diffStaleText = "";
    refreshDiffStale();
  }

  // ---- preview -----------------------------------------------------------
  async function runPreview() {
    const paths = scopePaths();
    if (!currentModel()) { notify(t("autotag.noModel"), "warn"); return; }
    if (paths.length === 0) { notify(t("autotag.previewEmpty"), "warn"); return; }
    if (paths.length > PREVIEW_MAX_IMAGES) {
      notify(t("autotag.previewTooMany", { n: paths.length, max: PREVIEW_MAX_IMAGES }), "warn");
      return;
    }
    previewButton.disabled = true;
    previewButton.textContent = t("autotag.previewRunning");
    try {
      const result = await api.autotagPreview(buildPayload(paths));
      preview = { items: (result && result.items) || [], paths };
      previewSignature = signature(paths);
      diffStaleText = "";
      renderDiff(paths);
      refreshRunButton();
    } catch (err) {
      // backend backstop: 400 too_many_images must never be silent
      if (err && err.code === "too_many_images") {
        notify(t("autotag.previewTooMany", { n: paths.length, max: PREVIEW_MAX_IMAGES }), "error");
      } else {
        notify(t("autotag.previewFailed", { message: err.message }), "error");
      }
      preview = null;
      refreshRunButton();
    } finally {
      previewButton.textContent = t("autotag.preview");
      refreshActionButtons();
    }
  }

  // ---- SSE ---------------------------------------------------------------
  function clearCancelWatchdog() {
    if (cancelWatchdog !== null) {
      window.clearTimeout(cancelWatchdog);
      cancelWatchdog = null;
    }
  }

  /** The one place a tagger job ends, whatever ended it (done, a lost stream, a cancel, the watchdog). */
  function closeStream() {
    clearCancelWatchdog();
    if (shelfJob) {
      shelfJob.finish();
      shelfJob = null;
    }
    if (runningJobId) {
      forgetJob("autotag", runningJobId);
      runningJobId = null;
    }
    if (stream) {
      stream.close();
      stream = null;
    }
  }

  /**
   * Section 4.7: while phase is "download" the done/total counters are BYTES
   * and current is a file name; while it is "infer" they are IMAGES. The unit
   * therefore comes from the event, never from the panel. A missing or unknown
   * phase (backend predating the field) falls back to the job that opened the
   * stream.
   */
  function phaseOf(payload) {
    const phase = payload && payload.phase;
    if (phase === PHASE_DOWNLOAD || phase === PHASE_INFER) return phase;
    return taskKind || PHASE_INFER;
  }

  /** One error entry out of {path,message} (section 4.3) as readable text. */
  function formatError(item) {
    if (item && typeof item === "object") {
      const where = item.path || item.file || "";
      const message = item.message || item.error || JSON.stringify(item);
      return where ? where + ": " + message : String(message);
    }
    return String(item);
  }

  function paintProgress(payload) {
    const phase = phaseOf(payload);
    const done = Number(payload.done || 0);
    const total = Number(payload.total || 0);
    const pct = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0;
    progressFill.style.width = pct + "%";
    progressFill.setAttribute("aria-valuenow", String(pct));
    const lines = [];
    if (phase === PHASE_DOWNLOAD) {
      // bytes on the wire, one file at a time
      lines.push(t("autotag.downloadProgress", { done: formatSize(done), total: formatSize(total), pct }));
      if (payload.current) lines.push(t("autotag.downloadCurrent", { current: payload.current }));
    } else {
      // one event per processed image (section 4.3)
      lines.push(t("autotag.progress", { done, total }));
      if (payload.current) lines.push(t("autotag.progressCurrent", { current: payload.current }));
    }
    progressText.textContent = lines.join(" | ");
    // Failures must be visible: both phases carry errors[] and neither is
    // allowed to swallow them.
    const errors = Array.isArray(payload.errors) ? payload.errors : [];
    errorsEl.replaceChildren();
    if (errors.length > 0) {
      errorsEl.append(el("div", null, t("autotag.errors")));
      for (const item of errors) errorsEl.append(el("div", null, formatError(item)));
    }
    if (shelfJob) shelfJob.update({ done, total, current: payload.current || "" });
    return { done, total };
  }

  /**
   * Terminal event. The kind of job decides the wording and the clean-up: a
   * finished download refreshes the model list and selects the model that just
   * landed; a finished inference run clears the diff and reloads captions.
   */
  function finishJob(finalPayload) {
    const kind = (finalPayload && finalPayload.phase === PHASE_DOWNLOAD) || taskKind === PHASE_DOWNLOAD
      ? PHASE_DOWNLOAD
      : PHASE_INFER;
    const modelId = pendingModelId;
    running = false;
    currentJobId = null;
    taskKind = null;
    pendingModelId = null;
    closeStream();
    cancelling = false;
    paintCancelButton();
    cancelButton.hidden = true;
    const done = Number((finalPayload && finalPayload.done) || 0);
    const total = Number((finalPayload && finalPayload.total) || 0);
    const errors = (finalPayload && Array.isArray(finalPayload.errors)) ? finalPayload.errors : [];

    if (kind === PHASE_DOWNLOAD) {
      // Section 4.7: a failed download (size mismatch included) arrives as
      // errors[] on the terminal event. Report it - never swallow it.
      if (errors.length > 0) {
        progressText.textContent = t("autotag.downloadErrors", { n: errors.length });
        notify(t("autotag.downloadFailed", { message: formatError(errors[0]) }), "error");
      } else {
        progressText.textContent = t("autotag.downloadDone", { model: modelId || "" });
        notify(t("autotag.downloadDone", { model: modelId || "" }), "ok");
      }
      refreshActionButtons();
      // Re-select the model only when it actually reported installed; on a
      // failure the refresh just re-paints the true state.
      void loadModels(errors.length > 0 ? null : modelId);
      return;
    }

    progressText.textContent = t("autotag.progressDone", { done, total, errors: errors.length });
    notify(t("autotag.streamDone"), errors.length > 0 ? "warn" : "ok");
    if (preview) { preview = null; previewSignature = ""; }
    diffEl.replaceChildren();
    diffTitle.hidden = true;
    diffSummary.textContent = "";
    refreshActionButtons();
    onAfterWrite();
  }

  function startStream(jobId, kind) {
    closeStream();
    streamErrors = 0;
    running = true;
    taskKind = kind || PHASE_INFER;
    currentJobId = jobId;
    runningJobId = String(jobId);
    // The total arrives with the first progress event, so the bookmark starts without one rather than
    // with a fabricated 0 (the same rule the shelf's "?" follows).
    rememberJob({ panel: "autotag", jobId: runningJobId, task: taskKind });
    // The total arrives with the first progress event; until then the shelf shows "?" rather than 0.
    shelfJob = shelf
      ? shelf.start({
          kind: "autotag",
          label: t("tab.autotag"),
          cancelLabel: t("autotag.cancel"),
          total: 0,
          cancel: () => { void cancelRun(); },
        })
      : null;
    refreshActionButtons();
    paintModelList();
    const seen = new Set();
    let source;
    try {
      source = new EventSource(autotagStreamUrl(jobId));
    } catch (err) {
      // No stream means no terminal event ever arrives: unlock the panel here
      // instead of leaving the buttons dead until a reload.
      running = false;
      currentJobId = null;
      taskKind = null;
      pendingModelId = null;
      cancelling = false;
      paintCancelButton();
      cancelButton.hidden = true;
      refreshActionButtons();
      paintModelList();
      notify(t("autotag.runFailed", { message: err.message }), "error");
      return;
    }
    stream = source;
    const handle = (raw) => {
      if (!raw) return;
      if (seen.has(raw)) return;
      seen.add(raw);
      let payload;
      try {
        payload = JSON.parse(raw);
      } catch (err) {
        return;
      }
      streamErrors = 0;
      const { done, total } = paintProgress(payload);
      if (payload.finished === true || payload.done_flag === true || (total > 0 && done >= total)) {
        finishJob(payload);
      }
    };
    source.onmessage = (event) => handle(event.data);
    for (const name of ["progress", "done", "error", "finished"]) {
      source.addEventListener(name, (event) => handle(event.data));
    }
    source.onerror = () => {
      if (!running) return;
      streamErrors += 1;
      if (source.readyState === EventSource.CLOSED || streamErrors >= STREAM_ERROR_LIMIT) {
        running = false;
        currentJobId = null;
        taskKind = null;
        pendingModelId = null;
        closeStream();
        cancelButton.hidden = true;
        refreshActionButtons();
        paintModelList();
        notify(t("autotag.streamLost"), "error");
      }
    };
  }

  async function runAutotag() {
    const freshPreview = !!(preview && previewSignature === signature(preview.paths));
    // With a fresh preview /run writes exactly the previewed images. Without one
    // (only reachable when the scope exceeds the preview cap) it writes the
    // current scope, and the confirmation says the diff is being skipped.
    const paths = freshPreview ? preview.paths.slice() : scopePaths();
    if (paths.length === 0) { notify(t("autotag.previewEmpty"), "warn"); return; }
    if (!currentModel()) { notify(t("autotag.noModel"), "warn"); return; }
    const backsUp = BACKUP_WRITE_MODES.has(writeModeSelect.value);
    const message = freshPreview
      ? t(backsUp ? "autotag.runConfirmBackup" : "autotag.runConfirm", { n: paths.length })
      : t(backsUp ? "autotag.runConfirmNoPreviewBackup" : "autotag.runConfirmNoPreview",
          { n: paths.length, max: PREVIEW_MAX_IMAGES });
    // The pause for confirmation happens before any state changes, so a "no"
    // leaves the panel exactly as it was.
    if (confirmFn && (await confirmFn({ body: message, confirmLabel: t("autotag.run"), danger: true })) === false) return;
    const payload = buildPayload(paths);
    payload.write_mode = writeModeSelect.value;
    runButton.disabled = true;
    try {
      const result = await api.autotagRun(payload);
      const jobId = result && (result.job_id || result.jobId);
      if (!jobId) {
        notify(t("autotag.runFailed", { message: t("common.noJobId") }), "error");
        refreshRunButton();
        return;
      }
      taskKind = PHASE_INFER;
      progressEl.hidden = false;
      errorsEl.replaceChildren();
      // images, not bytes: section 4.7 makes phase the unit switch
      paintProgress({ phase: PHASE_INFER, done: 0, total: paths.length, current: "", errors: [] });
      cancelButton.hidden = false;
      progressText.textContent = t("autotag.runStarted", { job: jobId });
      startStream(jobId, PHASE_INFER);
    } catch (err) {
      refreshRunButton();
      notify(t("autotag.runFailed", { message: err.message }), "error");
    }
  }

  /**
   * The cancel button's own state. Once the server has accepted a cancel the backend
   * still finishes the current image, and during that window "tagging" and "stopping"
   * must not look the same - nor may a second click fire a second request. Every
   * terminal path clears it.
   */
  function paintCancelButton() {
    cancelButton.textContent = cancelling ? t("autotag.cancelling") : t("autotag.cancel");
    cancelButton.disabled = cancelling;
  }

  function armCancelWatchdog() {
    clearCancelWatchdog();
    cancelWatchdog = window.setTimeout(() => {
      cancelWatchdog = null;
      if (!running) return;
      running = false;
      currentJobId = null;
      taskKind = null;
      pendingModelId = null;
      cancelling = false;
      paintCancelButton();
      closeStream();
      cancelButton.hidden = true;
      refreshActionButtons();
      paintModelList();
      notify(t("autotag.cancelTimeout"), "error");
    }, CANCEL_WATCHDOG_MS);
  }

  /**
   * Section 4.4: this button must really call POST /api/autotag/cancel - closing
   * the EventSource alone does not stop the backend. The stream deliberately
   * stays open: the backend finishes the current image and then sends the final
   * done event, which is what tears the UI down.
   */
  async function cancelRun() {
    const jobId = currentJobId;
    if (!jobId) {
      notify(running ? t("autotag.downloadBusy") : t("autotag.cancelIdle"), "info");
      return;
    }
    cancelButton.disabled = true;
    // Section 4.7 reuses this endpoint for downloads; only the wording differs.
    const cancelKey = taskKind === PHASE_DOWNLOAD ? "autotag.cancelDownloadRequested" : "autotag.cancelRequested";
    let accepted = false;
    try {
      const result = await api.autotagCancel(jobId);
      accepted = !!(result && result.cancelled);
      notify(accepted ? t(cancelKey) : t("autotag.cancelIdle"), accepted ? "warn" : "info");
    } catch (err) {
      notify(t("autotag.cancelFailed", { message: err.message }), "error");
    } finally {
      // A refused or failed request leaves the job running, so the button has to stay
      // usable; an accepted one keeps its stopping state until the terminal event.
      if (!accepted) cancelButton.disabled = false;
    }
    if (accepted) {
      cancelling = true;
      paintCancelButton();
      armCancelWatchdog();
      return;
    }
    // the job is gone (already finished or unknown): nothing more will arrive
    running = false;
    currentJobId = null;
    taskKind = null;
    pendingModelId = null;
    cancelling = false;
    paintCancelButton();
    closeStream();
    cancelButton.hidden = true;
    refreshActionButtons();
    paintModelList();
  }

  // ---- models ------------------------------------------------------------
  /**
   * Label of one dropdown option. It mirrors the three states of the list
   * below: an installed model is its own id, and an unavailable one says
   * whether it can still be downloaded (section 4.7).
   */
  function modelOptionText(model) {
    if (isInstalled(model)) return model.id;
    if (model.downloadable === true) return t("autotag.modelOptionDownloadable", { id: model.id });
    return t("autotag.modelUnavailable", { id: model.id });
  }

  /**
   * Re-apply the dropdown labels from the cached listing only. loadModels()
   * appends exactly one option per model, in order, and nothing else ever
   * touches modelSelect - so walking both by index rewrites the copy in place
   * and leaves the selection alone. A language switch must not re-run
   * /api/autotag/models.
   */
  function paintModelOptionLabels() {
    const options = Array.from(modelSelect.options);
    for (let index = 0; index < options.length && index < models.length; index += 1) {
      options[index].textContent = modelOptionText(models[index]);
    }
  }

  /**
   * Section 4.7 states per model: installed / size_bytes / downloadable.
   *
   * preferredId is the model a finished download wants active. It is honoured
   * only when the refreshed listing really reports it installed - the UI must
   * not claim a success the backend did not confirm.
   */
  async function loadModels(preferredId) {
    let payload;
    try {
      payload = await api.autotagModels();
    } catch (err) {
      modelsFailed = true;
      paintModelList();
      paintModelNote();
      refreshActionButtons();
      notify(t("common.loadFailed", { message: err.message }), "error");
      return;
    }
    models = (payload && payload.models) || [];
    modelsLoaded = true;
    modelsFailed = false;
    modelSelect.replaceChildren();
    const installed = models.filter(isInstalled);
    for (const model of models) {
      const opt = document.createElement("option");
      opt.value = model.id;
      opt.textContent = modelOptionText(model);
      opt.disabled = !isInstalled(model);
      modelSelect.append(opt);
    }
    paintModelList();
    const wanted = preferredId
      ? models.find((model) => model.id === preferredId && isInstalled(model))
      : null;
    if (installed.length > 0) modelSelect.value = wanted ? wanted.id : installed[0].id;
    paintModelNote();
    refreshActionButtons();
  }

  /**
   * Section 4.7 model list: installed / downloadable (with its size) /
   * blocked with a reason. Rows reuse the existing .cache-item / .c-path /
   * .hint / .btn-mini classes because index.html and app.css belong to another
   * workstream; no new stylesheet rule is introduced here.
   */
  function paintModelList() {
    modelListEl.replaceChildren();
    if (modelsFailed) {
      // One row that says the request failed and offers the retry, instead of an empty
      // list that reads as "this build has no models".
      const row = el("div", "row");
      row.append(el("span", "hint", t("autotag.modelsLoadFailed")));
      const retry = el("button", "btn btn-mini", t("autotag.modelRefresh"));
      retry.type = "button";
      retry.addEventListener("click", () => { void loadModels(); });
      row.append(retry);
      modelListEl.append(row);
      return;
    }
    if (models.length === 0) return;

    const head = el("div", "row");
    head.append(el("span", "hint", t("autotag.modelStatusTitle")));
    const refresh = el("button", "btn btn-mini", t("autotag.modelRefresh"));
    refresh.type = "button";
    refresh.addEventListener("click", () => { void loadModels(); });
    head.append(refresh);
    modelListEl.append(head);

    for (const model of models) {
      const row = el("div", "cache-item");
      row.title = model.onnx_path || model.id;
      row.append(el("span", "c-path", model.id));
      // size_bytes is the LOCAL onnx size: an uninstalled model reports 0 because
      // the listing never goes to the network. download_size_bytes is the static
      // advertised size, so it is what an uninstalled row can honestly show -
      // without it the row degraded to "unknown size" for every model.
      const localSize = Number(model.size_bytes) > 0 ? formatSize(model.size_bytes) : "";
      const advertisedSize = Number(model.download_size_bytes) > 0 ? formatSize(model.download_size_bytes) : "";
      if (isInstalled(model)) {
        row.append(el("span", "hint", localSize
          ? t("autotag.modelStatusInstalledSize", { size: localSize })
          : t("autotag.modelStatusInstalled")));
      } else if (model.downloadable === true) {
        // The copy is keyed by the stable model id; the backend's own
        // description stays as the permanent fallback, so a missing translation
        // degrades to that raw text rather than to a bare key.
        const key = "autotag.model.desc." + model.id;
        const text = hasKey(key) ? t(key) : String(model.description || "");
        if (text) row.append(el("span", "hint", text));
        row.append(el("span", "hint", t("autotag.modelStatusDownloadable", {
          size: advertisedSize || t("autotag.sizeUnknown"),
        })));
        const button = el("button", "btn btn-mini", null);
        button.type = "button";
        button.textContent = taskKind === PHASE_DOWNLOAD && pendingModelId === model.id
          ? t("autotag.modelDownloading")
          : t("autotag.modelDownload");
        // One job at a time: downloads and inference share the single SSE
        // channel, so any running job disables every download button.
        button.disabled = running;
        button.addEventListener("click", () => { void downloadModel(model.id); });
        row.append(button);
      } else {
        row.append(el("span", "hint", t("autotag.modelStatusBlocked")));
        const reason = typeof model.reason === "string" && model.reason
          ? model.reason
          : t("autotag.modelBlockedReason");
        row.append(el("span", "hint", reason));
      }
      modelListEl.append(row);
    }
  }

  /**
   * Section 4.7 download flow: POST /api/autotag/download -> {job_id}, then the
   * SAME SSE channel as inference, told apart by the phase field (bytes vs
   * images). Cancelling reuses POST /api/autotag/cancel - the backend stops the
   * job, deletes the .part file and still sends the terminal done event.
   */
  async function downloadModel(modelId) {
    if (running) {
      notify(t("autotag.downloadBusy"), "info");
      return;
    }
    const model = models.find((item) => item.id === modelId);
    if (!model) return;
    if (model.downloadable !== true) {
      notify(t("autotag.modelBlockedReason"), "warn");
      return;
    }

    // Lock the panel before the POST: two clicks in flight would open two
    // jobs and only one of them could own the stream.
    running = true;
    taskKind = PHASE_DOWNLOAD;
    pendingModelId = modelId;
    currentJobId = null;
    progressEl.hidden = false;
    errorsEl.replaceChildren();
    paintProgress({
      phase: PHASE_DOWNLOAD,
      done: 0,
      total: Number(model.size_bytes) || Number(model.download_size_bytes) || 0,
      current: "",
      errors: [],
    });
    progressText.textContent = t("autotag.downloadStarted", { model: modelId, job: "..." });
    cancelButton.hidden = true;
    refreshActionButtons();
    paintModelList();

    let jobId = null;
    try {
      const result = await api.autotagDownload(modelId);
      jobId = result && (result.job_id || result.jobId);
      if (!jobId) throw new Error(t("common.noJobId"));
    } catch (err) {
      running = false;
      taskKind = null;
      pendingModelId = null;
      notify(t("autotag.downloadFailed", { message: err.message }), "error");
      refreshActionButtons();
      paintModelList();
      return;
    }
    cancelButton.hidden = false;
    progressText.textContent = t("autotag.downloadStarted", { model: modelId, job: jobId });
    startStream(jobId, PHASE_DOWNLOAD);
  }

  /**
   * The note under the dropdown. Three states: not loaded yet (empty), loaded
   * but nothing installed (the frozen "no model" line), and a concrete model
   * (its onnx path, falling back to the backend description). Centralised so
   * relabel() repaints the same wording loadModels() chose.
   */
  function paintModelNote() {
    if (modelsFailed) {
      // "could not ask" and "nothing is installed" are different statements, and the
      // toast that carried the first one has already retired by now.
      modelNote.textContent = t("autotag.modelsLoadFailed");
      return;
    }
    if (!modelsLoaded) {
      modelNote.textContent = "";
      return;
    }
    if (models.filter(isInstalled).length === 0) {
      modelNote.textContent = t("autotag.modelNone");
      return;
    }
    const model = models.find((item) => item.id === modelSelect.value);
    if (!model) {
      modelNote.textContent = "";
      return;
    }
    if (model.onnx_path) {
      modelNote.textContent = t("autotag.modelMeta", { onnx: model.onnx_path });
      return;
    }
    const key = "autotag.model.desc." + model.id;
    modelNote.textContent = hasKey(key) ? t(key) : String(model.description || "");
  }

  modelSelect.addEventListener("change", () => { paintModelNote(); markStale(); });

  // The scope note is inserted before the action row it belongs to, and must exist before
  // the builders below paint it.
  scopeNoteEl = el("p", "hint");
  const actionsRow = previewButton.parentNode;
  if (actionsRow && actionsRow.parentNode) actionsRow.parentNode.insertBefore(scopeNoteEl, actionsRow);

  buildThresholds();
  buildOptions();
  buildWriteModes();

  previewButton.addEventListener("click", () => { void runPreview(); });
  runButton.addEventListener("click", () => { void runAutotag(); });
  cancelButton.addEventListener("click", cancelRun);

  /**
   * Re-apply the current translations to every string this panel painted from
   * t() at render time; the static data-copy nodes belong to strings.js. It
   * repaints from the state the panel already holds and is a pure redraw: no
   * request is issued, nothing is written back and no input is re-read, so it
   * is safe before the model list has loaded and while a job is running.
   *
   * Deliberately not covered: the live status line, its error list and the
   * toasts. Those come from streamed payloads the panel does not keep, and the
   * status line is replaced by terminal wording (runStarted / progressDone /
   * downloadDone ...) that paintProgress() cannot reproduce - repainting it
   * from the last tick would regress a finished job back to live-progress copy.
   */
  function relabel() {
    paintTextBindings();
    paintWriteModeHint();
    refreshScopes();
    paintModelOptionLabels();
    paintModelList();
    paintModelNote();
    paintCancelButton();
    paintDiff();
  }

  return {
    loadModels,
    loadModelsOnce: () => (models.length > 0 ? Promise.resolve(models) : loadModels()),
    relabel,
    /**
     * Pick a job back up after a page reload. The stream replays its complete event log, so the panel
     * only has to open it again; a job that finished meanwhile replays its terminal event immediately.
     */
    resume(jobId, _total, task) {
      startStream(jobId, task || PHASE_INFER);
    },
    refreshScopes,
    scopePaths,
    markStale,
    /**
     * Section 4.17: the settings the crop panel reuses when it tags the images it archived.
     * This is the panel's own state, read from its own controls - the alternative (a second
     * model picker on the crop panel) is exactly the drift this repository keeps paying for:
     * two answers to "which model, at which thresholds?" and no way to tell which one ran.
     * The shape is section 4.4's request body minus "images".
     */
    taggerParams: () => ({
      model: currentModel() || null,
      thresholds: collectThresholds(),
      postprocess: collectPostprocess(),
      write_mode: writeModeSelect.value,
    }),
    //: The command palette runs these three - they are the very functions the panel's own
    //: buttons call, so an entry cannot do anything the button would not.
    preview: runPreview,
    run: runAutotag,
    cancel: cancelRun,
    /**
     * What the palette needs to say why one of those three is unavailable. The facts are this
     * panel's own (a running job, no installed model, a scope over the preview cap), and the
     * can* flags ARE the buttons' gates read off the buttons - so the palette can never offer
     * more than the panel would accept.
     */
    status: () => ({
      running,
      installed: models.some(isInstalled),
      scope: scopePaths().length,
      // The cap is part of the reason ("{n} is over the preview limit of {max}"), so it travels
      // with the fact instead of being copied into the palette's own table.
      max: PREVIEW_MAX_IMAGES,
      previewTooLarge: previewTooLarge(),
      previewEmpty: Boolean(preview && preview.items.length === 0),
      canPreview: !previewButton.disabled,
      canRun: !runButton.disabled,
    }),
    reset: () => {
      preview = null;
      previewSignature = "";
      diffEl.replaceChildren();
      diffTitle.hidden = true;
      diffSummary.textContent = "";
      runButton.disabled = true;
      scope.paint();
    },
  };
}
