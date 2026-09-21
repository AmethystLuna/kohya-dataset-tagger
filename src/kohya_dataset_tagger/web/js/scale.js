/**
 * Image export / area normalized scaling panel (p0-spec section 4.14).
 *
 * Ported from the standalone image_scaler_gui.py: every image is resized so that
 * new_width * new_height is as close as possible to the target area while the
 * aspect ratio stays untouched. It is NOT a crop.
 *
 * Two deliberate departures from the source script:
 *   1. "no upscale" is a first class option (on by default): an image whose area
 *      is below the target keeps its original size instead of being enlarged.
 *   2. the sidecar files are no longer configurable. The panel always copies the
 *      caption next to each exported image (same stem, shared caption_extension),
 *      because that is what this tool's dataset contract uses.
 *
 * What it acts on
 * ---------------
 * The column's shared scope strip (scope.js) - the same control the tagger, the batch panel and
 * the crop panel read - snapshotted when the run starts, so a file this very run writes is never
 * picked up mid-run. Until 2026-09-20 the panel took the open directory instead and the endpoint
 * walked everything below it recursively: an export could cover far more than the control on
 * screen said, which is the one thing a control answering "which images?" exists to prevent. The
 * endpoint now takes the list itself (api/scale.py: ScaleRequest.images), so there is nothing left
 * for the panel to widen on its own.
 *
 * Like the export panel this one is built in JavaScript instead of index.html so
 * the feature stays inside the files it owns; setupTabs() picks the appended tab
 * up automatically.
 */
import { api, scaleStreamUrl } from "./api.js";
import { forgetJob, rememberJob } from "./jobstore.js";
import { t } from "./strings.js";
//: The resample filters and output formats accepted by /api/scale/run. They live in the store
//: (trainparams.js) because it validates a **remembered** name against them - two lists that must
//: agree with each other and with the backend are one list.
import { IMAGE_FORMATS, RESAMPLE_NAMES } from "./trainparams.js";

//: Panel defaults. noUpscale is on: training data should not be invented.
//: The first five are also the shared store's defaults (trainparams.js) - the panel's own
//: controls are what edits them, so both have to start from the same value or the first
//: repaint would move the checkbox under the user.
export const SCALE_DEFAULTS = Object.freeze({
  resample: "lanczos",
  format: "png",
  quality: 95,
  optimize: true,
  noUpscale: true,
  writeToml: false,
  //: Replace a dataset.toml that is already at the destination. Off by default: the export must not
  //: throw away a file the user may have hand-tuned, the same rule it applies to the images.
  overwriteToml: false,
});
//: Error rows rendered before the panel says "and N more".
export const SCALE_ERROR_ROWS = 30;
//: Consecutive EventSource failures tolerated before giving up.
const STREAM_ERROR_LIMIT = 3;
//: How long an accepted cancel may go without a terminal event before the panel
//: unlocks itself (the autotag panel's CANCEL_WATCHDOG_MS uses the same value).
const CANCEL_WATCHDOG_MS = 30000;

function integerOr(value, fallback) {
  const parsed = Number.parseInt(String(value === undefined ? "" : value).trim(), 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

/**
 * Pure builder for the POST /api/scale/run body. Exported so the headless
 * harness can drive it without a DOM (the panel just passes its inputs in).
 */
export function buildScaleRequest(input) {
  const source = input || {};
  const quality = Number.parseInt(String(source.quality === undefined ? "" : source.quality).trim(), 10);
  const extension = String(source.captionExtension || "").trim();
  return {
    // The scope strip's answer, in the order the control reports it. The endpoint rejects an empty
    // list (400 no_images) instead of falling back to the whole directory, so an empty scope can
    // never turn into "export everything" - the panel keeps its Start button disabled for it.
    images: Array.isArray(source.images) ? source.images.map((path) => String(path || "")) : [],
    root: String(source.root || ""),
    target_dir: String(source.targetDir || "").trim(),
    target_width: integerOr(source.targetWidth, 1536),
    target_height: integerOr(source.targetHeight, 1536),
    no_upscale: source.noUpscale !== false,
    resample: String(source.resample || SCALE_DEFAULTS.resample),
    output_format: String(source.format || SCALE_DEFAULTS.format),
    quality: Number.isFinite(quality) ? Math.min(100, Math.max(1, quality)) : SCALE_DEFAULTS.quality,
    optimize: source.optimize !== false,
    caption_extension: extension.length > 0 ? extension : ".txt",
    write_dataset_toml: source.writeDatasetToml === true,
    overwrite_dataset_toml: source.overwriteToml === true,
  };
}

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

export function createScalePanel(options) {
  const config = options || {};
  const notify = config.notify || (() => {});
  const getParams = config.getParams;
  //: Told when a job ends (app.js re-reads whether a dataset.toml now exists, section 4.16). Optional
  //: so the panel still runs headless without a listener.
  const onFinished = typeof config.onFinished === "function" ? config.onFinished : null;
  const targetHost = config.targetHost || document.body;

  let currentRoot = null;
  let running = false;
  let finished = true;
  let jobId = null;
  let stream = null;
  let streamErrors = 0;
  let watchdog = null;
  //: whether the RUNNING job was asked to write dataset.toml. Read from the request the
  //: run sent, never from the checkbox: ticking the box mid-run would otherwise report a
  //: finished job as a failed toml write.
  let jobWroteToml = false;
  let pickerAllowed = false;
  //: The column's shared scope strip (scope.js), built once in app.js. Optional so the panel still
  //: runs headless without one - but without it there is no image list, so Start stays disabled.
  const scope = config.scope || null;

  //: The job shelf (js/jobs.js); optional so the panel still runs headless without one.
  const shelf = config.jobs || null;
  //: This job's row on the shelf, so an export stays visible (and cancellable) from every tab.
  let shelfJob = null;
  //: The id this tab bookmarked, so a reload can pick the export back up (jobstore.js).
  let runningJobId = null;

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
  const numberInput = (value, min, step) => {
    const input = make("input");
    input.type = "number";
    input.min = String(min);
    input.step = String(step);
    input.value = String(value);
    return input;
  };
  // Every construction-time string is registered as a painter so relabel() can
  // repaint the panel from held state after a language switch. The t() call
  // lives inside the painter, so the switch site never repeats the key; without
  // this the tab label and section copy freeze in the construction locale.
  const painters = [];
  const paintText = (node, key, params) => {
    painters.push(() => { node.textContent = t(key, params); });
    return node;
  };
  const paintAttr = (node, attr, key, params) => {
    painters.push(() => { node[attr] = t(key, params); });
    return node;
  };
  function paintLabels() {
    for (const paint of painters) paint();
  }
  const copyButton = (className, key) => paintText(button(className), key);
  const labelled = (labelKey, input, className) => {
    const field = make("label", className || "field");
    field.append(paintText(make("span"), labelKey), input);
    return field;
  };
  const checkboxField = (key, checked) => {
    const box = make("input");
    box.type = "checkbox";
    box.checked = checked === true;
    const field = make("label", "field-inline");
    field.append(box, paintText(make("span"), key));
    return { field, box };
  };

  // ---- tab + panel shell ------------------------------------------------
  const tab = copyButton("tab", "tab.scale");
  tab.setAttribute("role", "tab");
  tab.dataset.tab = "scale";

  const panel = make("div", "tab-panel");
  panel.dataset.panel = "scale";
  panel.hidden = true;

  const sourceLine = paintText(make("div", "hint mono"), "scale.sourceNone");
  // The empty-scope reason. It is the panel's only scope copy: the strip above carries the live
  // counts, and a second count down here would be one fact printed twice. Hidden as soon as there
  // is something to export - a reason that stays on screen after it stopped applying is noise.
  const scopeLine = paintText(make("p", "hint"), "scale.scopeNone");
  scopeLine.hidden = true;

  const targetInput = make("input");
  targetInput.type = "text";
  targetInput.spellcheck = false;
  paintAttr(targetInput, "placeholder", "scale.targetPlaceholder");
  const browseButton = copyButton("btn", "scale.browse");
  const targetField = make("label", "field");
  targetField.append(paintText(make("span"), "scale.target"), targetInput);
  const targetRow = make("div", "row");
  targetRow.append(targetField, browseButton);

  // ---- scaling options --------------------------------------------------
  const resolutionWidth = numberInput(1536, 8, 64);
  const resolutionHeight = numberInput(1536, 8, 64);
  const resolutionGrid = make("div", "threshold-grid");
  const widthField = make("label");
  widthField.append(paintText(make("span"), "scale.resWidth"), resolutionWidth);
  const heightField = make("label");
  heightField.append(paintText(make("span"), "scale.resHeight"), resolutionHeight);
  resolutionGrid.append(widthField, heightField);
  const resolutionField = make("div", "field");
  resolutionField.append(paintText(make("span"), "scale.resolution"), resolutionGrid);

  // The caption extension is shared with the export panel (trainparams.js): it names the
  // sidecar this run copies next to every exported image AND the caption_extension this
  // run may write into dataset.toml. It used to be readable only on the export tab, so a
  // run's own output depended on a value the user could not see here.
  const captionExtInput = make("input");
  captionExtInput.type = "text";
  captionExtInput.spellcheck = false;
  const captionExtensionField = labelled("export.captionExtension", captionExtInput);

  const resampleSelect = make("select");
  for (const name of RESAMPLE_NAMES) {
    const option = make("option", "", name);
    option.value = name;
    resampleSelect.append(option);
  }
  resampleSelect.value = SCALE_DEFAULTS.resample;

  const formatSelect = make("select");
  for (const name of IMAGE_FORMATS) {
    const option = make("option", "", name.toUpperCase());
    option.value = name;
    formatSelect.append(option);
  }
  formatSelect.value = SCALE_DEFAULTS.format;

  const qualityInput = numberInput(SCALE_DEFAULTS.quality, 1, 1);
  qualityInput.max = "100";

  const optimize = checkboxField("scale.optimize", SCALE_DEFAULTS.optimize);
  const noUpscale = checkboxField("scale.noUpscale", SCALE_DEFAULTS.noUpscale);
  const writeToml = checkboxField("scale.writeToml", SCALE_DEFAULTS.writeToml);
  const overwriteToml = checkboxField("scale.overwriteToml", SCALE_DEFAULTS.overwriteToml);
  const overwriteTomlHint = paintText(make("p", "hint"), "scale.overwriteTomlHint");

  /** Replacing an existing dataset.toml only means anything once the file is being generated at all. */
  function paintTomlOptions() {
    overwriteToml.box.disabled = !writeToml.box.checked;
    overwriteTomlHint.hidden = !writeToml.box.checked;
  }

  const optionsBox = make("fieldset", "box");
  optionsBox.append(
    paintText(make("legend"), "scale.params"),
    resolutionField,
    captionExtensionField,
    labelled("scale.resample", resampleSelect),
    labelled("scale.format", formatSelect),
    labelled("scale.quality", qualityInput),
    optimize.field,
    noUpscale.field,
    paintText(make("p", "hint"), "scale.noUpscaleHint"),
    writeToml.field,
    paintText(make("p", "hint"), "scale.writeTomlHint"),
    overwriteToml.field,
    overwriteTomlHint,
  );

  // ---- actions + progress ----------------------------------------------
  const startButton = copyButton("btn btn-primary", "scale.start");
  startButton.id = "btn-scale-start";
  const cancelButton = copyButton("btn btn-danger", "scale.cancel");
  cancelButton.hidden = true;
  const actions = make("div", "row");
  actions.append(startButton, cancelButton);

  const progressFill = make("div", "progress-fill");
  const progressTrack = make("div", "progress-track");
  progressFill.setAttribute("role", "progressbar");
  progressFill.setAttribute("aria-labelledby", "scale-progress-text");
  progressFill.setAttribute("aria-valuemin", "0");
  progressFill.setAttribute("aria-valuemax", "100");
  progressFill.setAttribute("aria-valuenow", "0");
  progressTrack.append(progressFill);
  const progressText = make("div", "hint");
  progressText.id = "scale-progress-text";
  progressText.setAttribute("role", "status");
  progressText.setAttribute("aria-live", "polite");
  const currentText = make("div", "hint");
  const errorsBox = make("div", "errors");
  errorsBox.setAttribute("role", "alert");
  const progress = make("div", "progress");
  progress.append(progressTrack, progressText, currentText, errorsBox);
  progress.hidden = true;

  const summary = make("div", "mono");
  const tomlLine = make("div", "hint");

  panel.append(
    paintText(make("h3", "panel-title"), "scale.title"),
    paintText(make("p", "hint"), "scale.hint"),
    sourceLine,
    scopeLine,
    targetRow,
    paintText(make("p", "hint"), "scale.targetHint"),
    optionsBox,
    actions,
    progress,
    summary,
    tomlLine,
  );

  // ---- params bridge (section 4.9 shares resolution + caption_extension) --
  function sharedParams() {
    return typeof getParams === "function" ? getParams() : null;
  }

  /** The source line is derived state: repaint it from the held root. */
  function paintSource() {
    sourceLine.textContent = currentRoot
      ? t("scale.sourceValue", { path: currentRoot })
      : t("scale.sourceNone");
  }

  /** The images this panel would export right now: the column's scope strip, read where it matters. */
  function scopePaths() {
    if (!scope || typeof scope.paths !== "function") return [];
    return scope.paths() || [];
  }

  /**
   * The reason line and the Start button read the **same** count, so they cannot disagree about
   * whether there is anything to export. Called at construction, on every repaint of the strip
   * (subscribe below), when the tab is shown, and on a language switch.
   */
  function paintScope() {
    scopeLine.hidden = scopePaths().length > 0;
    startButton.disabled = running || !canStart();
  }

  function paintParams() {
    const params = sharedParams();
    if (!params) return;
    const resolution = params.resolution();
    if (document.activeElement !== resolutionWidth) resolutionWidth.value = String(resolution[0]);
    if (document.activeElement !== resolutionHeight) resolutionHeight.value = String(resolution[1]);
    if (document.activeElement !== captionExtInput) captionExtInput.value = String(params.captionExtension());
    // The pipeline settings live in the same store because the crop panel (section 4.17) inherits
    // them instead of owning a second copy: whoever shows the controls is the one that paints them.
    const processing = params.processing();
    if (document.activeElement !== resampleSelect) resampleSelect.value = String(processing.resample);
    if (document.activeElement !== formatSelect) formatSelect.value = String(processing.format);
    if (document.activeElement !== qualityInput) qualityInput.value = String(processing.quality);
    optimize.box.checked = processing.optimize === true;
    noUpscale.box.checked = processing.noUpscale === true;
    // The panel's own switch comes from the same store, so it is seeded from what was remembered
    // rather than from the constant - and the dependent box follows it.
    const panelOptions = typeof params.scaleOptions === "function" ? params.scaleOptions() : {};
    writeToml.box.checked = panelOptions.writeToml === true;
    // A format that arrived from elsewhere still has to disable the quality knob.
    onFormatChanged();
    paintTomlOptions();
  }

  /** Write one pipeline setting through to the shared store, which repaints every reader. */
  function publishProcessing(patch) {
    const params = sharedParams();
    if (!params) return;
    params.setProcessing(patch);
  }

  function publishResolution() {
    const params = sharedParams();
    if (!params) return;
    params.setResolution(resolutionWidth.value, resolutionHeight.value);
  }

  /** Write the extension through to the shared store, which repaints the other tab. */
  function publishCaptionExtension() {
    const params = sharedParams();
    if (!params) return;
    params.setCaptionExtension(captionExtInput.value);
  }

  /**
   * The panel's own switch goes through the same store - which is also what remembers it, so a
   * parameter set once survives the reload instead of being re-ticked on every export.
   * `overwriteToml` deliberately does not: it is a permission for one run, not a preference.
   */
  function publishScaleOptions(patch) {
    const params = sharedParams();
    if (!params || typeof params.setScaleOptions !== "function") return;
    params.setScaleOptions(patch);
  }

  // ---- rendering --------------------------------------------------------
  function renderErrors(errors) {
    const list = Array.isArray(errors) ? errors : [];
    errorsBox.replaceChildren();
    if (list.length === 0) return;
    errorsBox.append(make("strong", "", t("scale.errorsTitle", { n: list.length })));
    for (const item of list.slice(0, SCALE_ERROR_ROWS)) {
      const path = item && item.path ? String(item.path) : "";
      const message = item && item.message ? String(item.message) : "";
      errorsBox.append(make("div", "mono", path.length > 0 ? path + " - " + message : message));
    }
    if (list.length > SCALE_ERROR_ROWS) {
      errorsBox.append(make("div", "hint", t("scale.errorsMore", { n: list.length - SCALE_ERROR_ROWS })));
    }
  }

  function paintProgress(payload) {
    const data = payload || {};
    const done = Number(data.done) || 0;
    const total = Number(data.total) || 0;
    const pct = total > 0 ? Math.min(100, Math.round((100 * done) / total)) : 0;
    progressFill.style.width = pct + "%";
    progressFill.setAttribute("aria-valuenow", String(pct));
    progressText.textContent = t("scale.progress", { done, total });
    if (shelfJob) shelfJob.update({ done, total, current: data.current });
    currentText.textContent = data.current
      ? t("scale.current", { name: baseName(data.current) })
      : "";
    renderErrors(data.errors);
  }

  function summaryParams(summaryData) {
    return {
      processed: Number(summaryData.processed) || 0,
      scaled: Number(summaryData.scaled) || 0,
      kept: Number(summaryData.kept) || 0,
      captions: Number(summaryData.captions) || 0,
      failed: Number(summaryData.failed) || 0,
    };
  }

  function paintDone(payload) {
    const data = payload || {};
    const info = summaryParams(data.summary || {});
    summary.textContent = data.cancelled
      ? t("scale.summaryCancelled", info)
      : t("scale.summary", info);
    const toml = (data.summary || {}).dataset_toml;
    const tomlBackup = (data.summary || {}).dataset_toml_backup;
    if (toml) {
      // A replaced file is only safely replaced if the panel says where the previous one went.
      tomlLine.textContent = tomlBackup
        ? t("scale.datasetTomlBackup", { path: String(toml), backup: String(tomlBackup) })
        : t("scale.datasetToml", { path: String(toml) });
    } else if ((data.summary || {}).dataset_toml_skipped === true) {
      // Not the same statement as "failed": the file is there and was deliberately left alone.
      tomlLine.textContent = t("scale.datasetTomlSkipped");
    } else if (jobWroteToml) {
      const warnings = (data.summary || {}).dataset_toml_warnings || [];
      tomlLine.textContent = t("scale.datasetTomlFailed", {
        reason: warnings.length > 0 ? warnings.join(" / ") : t("scale.datasetTomlUnknown"),
      });
    } else {
      tomlLine.textContent = t("scale.datasetTomlNone");
    }
    renderErrors(data.errors);
    // A cancelled job did not process everything, so a full bar would claim it
    // did; the bar keeps the last value the user was watching.
    if (!data.cancelled) {
      progressFill.style.width = "100%";
      progressFill.setAttribute("aria-valuenow", "100");
    }
    if (info.failed > 0) notify(t("scale.doneFailed", { n: info.failed }), "warn");
    else if (data.cancelled) notify(t("scale.doneCancelled"), "warn");
    else notify(t("scale.done"), "ok");
  }

  // ---- job lifecycle ----------------------------------------------------
  /** The one place an export job ends, whatever ended it (done, a lost stream, the watchdog). */
  function closeStream() {
    clearWatchdog();
    if (shelfJob) {
      shelfJob.finish();
      shelfJob = null;
    }
    if (runningJobId) {
      forgetJob("scale", runningJobId);
      runningJobId = null;
    }
    if (stream) {
      stream.close();
      stream = null;
    }
    jobId = null;
  }

  /**
   * A cancel the server accepted is only half the story: the job stops when the
   * stream says done. If that never arrives - the worker was killed, the socket
   * died quietly - the panel would stay locked with Start and Cancel both
   * disabled, so the same 30 s watchdog the autotag panel uses unlocks it.
   */
  function clearWatchdog() {
    if (watchdog !== null) {
      window.clearTimeout(watchdog);
      watchdog = null;
    }
  }

  function armWatchdog() {
    clearWatchdog();
    watchdog = window.setTimeout(() => {
      watchdog = null;
      if (finished) return;
      closeStream();
      finished = true;
      setRunning(false);
      notify(t("scale.cancelTimeout"), "warn");
    }, CANCEL_WATCHDOG_MS);
  }

  function setRunning(value) {
    running = value === true;
    startButton.disabled = running || !canStart();
    startButton.textContent = running ? t("scale.starting") : t("scale.start");
    cancelButton.hidden = !running;
    cancelButton.disabled = false;
    cancelButton.textContent = t("scale.cancel");
  }

  function canStart() {
    // Three conditions, one button: a source directory, a target nobody else can guess, and
    // something to act on. The scope is the one that used to be missing - the export simply took
    // the whole directory instead, which is the defect this panel stopped repeating.
    return Boolean(currentRoot)
      && targetInput.value.trim().length > 0
      && scopePaths().length > 0;
  }

  function openStream(id, total) {
    jobId = String(id);
    runningJobId = jobId;
    rememberJob({ panel: "scale", jobId, total: Number(total) || 0 });
    finished = false;
    streamErrors = 0;
    shelfJob = shelf
      ? shelf.start({
          kind: "scale",
          label: t("tab.scale"),
          cancelLabel: t("scale.cancel"),
          total: Number(total) || 0,
          cancel: () => { void cancel(); },
        })
      : null;
    stream = new EventSource(scaleStreamUrl(jobId));
    stream.addEventListener("progress", (event) => {
      streamErrors = 0;
      paintProgress(parseData(event.data));
    });
    stream.addEventListener("done", (event) => {
      // Section 4.3: the end of a job is the finished flag, never just done >= total.
      const payload = parseData(event.data);
      if (payload.finished !== true) return;
      finishJob(payload);
    });
    stream.onerror = () => {
      // The stream closes itself after done; anything after that is noise.
      if (finished) return;
      streamErrors += 1;
      if (streamErrors >= STREAM_ERROR_LIMIT) {
        notify(t("scale.streamLost"), "error");
        closeStream();
        finished = true;
        setRunning(false);
      }
    };
  }

  function finishJob(payload) {
    finished = true;
    closeStream();
    setRunning(false);
    paintDone(payload || {});
    // The single end point of an export (the lost-stream and watchdog endings deliberately do not
    // call this - they know no result). It may have written a dataset.toml into a directory that is
    // itself a dataset root, so the app is told; it re-reads the fact rather than assuming one.
    if (onFinished) onFinished(payload || {});
  }

  function clearResults() {
    progress.hidden = false;
    progressFill.style.width = "0%";
    progressText.textContent = t("scale.progress", { done: 0, total: 0 });
    currentText.textContent = "";
    errorsBox.replaceChildren();
    summary.textContent = "";
    tomlLine.textContent = "";
  }

  async function start() {
    if (running) return;
    const root = config.getRoot ? config.getRoot() : null;
    if (!root) {
      notify(t("scale.noSource"), "warn");
      return;
    }
    // The snapshot: what the strip says at this moment is the whole job, and a scope change while
    // it runs cannot move the target it already started writing into.
    const images = scopePaths();
    if (images.length === 0) {
      // The same sentence the panel carries while the scope is empty: one fact, one wording.
      notify(t("scale.scopeNone"), "warn");
      return;
    }
    const targetDir = targetInput.value.trim();
    if (!targetDir) {
      notify(t("scale.noTarget"), "warn");
      return;
    }
    const params = sharedParams();
    const payload = buildScaleRequest({
      images,
      root,
      targetDir,
      targetWidth: resolutionWidth.value,
      targetHeight: resolutionHeight.value,
      noUpscale: noUpscale.box.checked,
      resample: resampleSelect.value,
      format: formatSelect.value,
      quality: qualityInput.value,
      optimize: optimize.box.checked,
      captionExtension: params ? params.captionExtension() : ".txt",
      writeDatasetToml: writeToml.box.checked,
      overwriteToml: overwriteToml.box.checked,
    });
    jobWroteToml = payload.write_dataset_toml === true;
    clearResults();
    setRunning(true);
    try {
      const result = await api.scaleRun(payload);
      const id = result && (result.job_id || result.jobId);
      if (!id) throw new Error(t("common.noJobId"));
      openStream(id, result.total);
    } catch (err) {
      setRunning(false);
      notify(t("scale.runFailed", { message: describeError(err) }), "error");
    }
  }

  async function cancel() {
    if (!running || !jobId) return;
    cancelButton.disabled = true;
    cancelButton.textContent = t("scale.cancelling");
    notify(t("scale.cancelRequested"), "info");
    let accepted = true;
    try {
      const answer = await api.scaleCancel(jobId);
      // "cancelled: false" means the job was already gone. Nothing will ever
      // send done for it, so waiting for one would lock the panel for good.
      accepted = !(answer && answer.cancelled === false);
    } catch (err) {
      notify(t("scale.runFailed", { message: describeError(err) }), "error");
    }
    if (!accepted) {
      closeStream();
      finished = true;
      setRunning(false);
      notify(t("scale.cancelAlreadyGone"), "warn");
      return;
    }
    armWatchdog();
  }

  async function pickTarget() {
    if (!pickerAllowed) {
      notify(t("scale.pickerDisabled"), "warn");
      return;
    }
    if (typeof config.pickTarget !== "function") return;
    const picked = await config.pickTarget(targetInput.value.trim());
    if (picked) {
      targetInput.value = String(picked);
      startButton.disabled = running || !canStart();
    }
  }

  // ---- wiring -----------------------------------------------------------
  function onFormatChanged() {
    // PNG is lossless: the quality knob means nothing there.
    qualityInput.disabled = formatSelect.value === "png";
  }

  startButton.addEventListener("click", () => { void start(); });
  cancelButton.addEventListener("click", () => { void cancel(); });
  browseButton.addEventListener("click", () => { void pickTarget(); });
  targetInput.addEventListener("input", () => {
    startButton.disabled = running || !canStart();
  });
  resolutionWidth.addEventListener("input", publishResolution);
  resolutionHeight.addEventListener("input", publishResolution);
  captionExtInput.addEventListener("input", publishCaptionExtension);
  formatSelect.addEventListener("change", () => {
    publishProcessing({ format: formatSelect.value });
    onFormatChanged();
  });
  resampleSelect.addEventListener("change", () => publishProcessing({ resample: resampleSelect.value }));
  qualityInput.addEventListener("input", () => publishProcessing({ quality: qualityInput.value }));
  optimize.box.addEventListener("change", () => publishProcessing({ optimize: optimize.box.checked }));
  noUpscale.box.addEventListener("change", () => publishProcessing({ noUpscale: noUpscale.box.checked }));
  writeToml.box.addEventListener("change", () => {
    publishScaleOptions({ writeToml: writeToml.box.checked });
    paintTomlOptions();
  });

  if (typeof getParams === "function" && getParams()) {
    getParams().subscribe(paintParams);
  }
  // One control, one reaction. The strip is above the tabs, so it can be changed while this panel
  // is the open one - a count read only when the tab appears would leave a Start button that is
  // disabled for a scope that has images (or worse, enabled for one that has none).
  if (scope && typeof scope.subscribe === "function") {
    scope.subscribe({ onPaint: paintScope, onChange: paintScope });
  }
  paintLabels();
  onFormatChanged();
  paintParams();
  paintTomlOptions();
  paintScope();

  if (config.tabsHost) config.tabsHost.append(tab);
  if (config.panelHost) config.panelHost.append(panel);
  else targetHost.append(panel);

  /**
   * Relabel contract: repaint every construction-time string, then the
   * state-derived labels (source line, start/cancel) from held state via the
   * existing painters. No request, and safe before any data has loaded.
   */
  function relabel() {
    paintLabels();
    paintSource();
    paintScope();
    setRunning(running);
  }

  return {
    relabel,
    //: The command palette runs these two - the very functions this panel's buttons call - and
    //: reads the buttons' own gates to say why one of them is unavailable.
    start,
    cancel,
    status: () => ({
      running,
      canStart: !startButton.disabled,
      //: What the run would cover, for the command palette's "why not?" (app.js whyScaleStart).
      scope: scopePaths().length,
    }),
    /** Pick an export back up after a page reload; the stream replays its whole event log. */
    resume(jobId, total) {
      clearResults();
      setRunning(true);
      finished = false;
      openStream(jobId, Number(total) || 0);
    },
    onShow() {
      currentRoot = config.getRoot ? config.getRoot() : null;
      paintSource();
      paintParams();
      paintScope();
    },
    setPickerEnabled(enabled, title) {
      pickerAllowed = enabled === true;
      browseButton.disabled = !pickerAllowed;
      browseButton.title = title || "";
    },
    close() {
      closeStream();
      finished = true;
      setRunning(false);
    },
  };
}