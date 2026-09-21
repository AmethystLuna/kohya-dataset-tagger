/**
 * Batch tag editing (the core of the reference dataset-tag-editor): one op
 * applied to every image the filter left on screen, via POST /api/captions/batch.
 *
 * Three rules shape this module:
 *
 *  1. The scope is the column's one scope control (scope.js, the #scope-strip
 *     above the tab panels): the picked images, the open directory, that
 *     directory with its subdirectories, or what the filter left visible. This
 *     panel used to build its own copy of that control, which is exactly how the
 *     tagger and this panel came to act on different sets of images.
 *  2. Nothing is written before a dry run. "Preview" calls the same endpoint
 *     with dry_run:true, which runs the SAME server side op function, so the
 *     diff cannot drift from what apply would write. Any input change drops the
 *     preview and re-disables write, like the autotag panel's stale check.
 *  3. The common-tag editor is position free: one input per common tag. Editing
 *     a line renames that tag, clearing a line removes it, and deleting a line
 *     does NOT shift the meaning of the lines after it (a plain comma separated
 *     text box would, which is why this is a row list).
 *
 * The pure helpers (commonTags / commonEdit / splitTagInput) are exported so the
 * headless harness can test them without a DOM.
 */

import { api, BATCH_OP, batchStreamUrl, thumbUrl, THUMB_SIZE } from "./api.js";
import { forgetJob, rememberJob } from "./jobstore.js";
import { t } from "./strings.js";
import { renderCaptionDiff } from "./autotag.js";

/** Local pseudo-op: edit the tags common to every visible image. */
export const BATCH_COMMON = "common";

//: diff rows rendered before the table says "and N more". Diff tables grow
//: fast (one row per image) and this panel lives in a side column.
export const PREVIEW_ROWS = 8;

//: editable common-tag rows rendered. 1653 image datasets can share thousands
//: of tags; the search-able frequency table is the place to look those up.
export const COMMON_ROWS = 200;

/** Parse a comma separated tag input the way the backend splits captions. */
export function splitTagInput(raw) {
  return String(raw || "").split(",").map((part) => part.trim()).filter((part) => part.length > 0);
}

/**
 * Tags present in EVERY list, ordered case-insensitively by name.
 * An empty array means "nothing is common" (including the no-images case).
 */
export function commonTags(tagLists) {
  const lists = (Array.isArray(tagLists) ? tagLists : []).filter((list) => Array.isArray(list));
  if (lists.length === 0) return [];
  let acc = new Set(lists[0]);
  for (const list of lists.slice(1)) {
    const present = new Set(list);
    acc = new Set(Array.from(acc).filter((tag) => present.has(tag)));
    if (acc.size === 0) return [];
  }
  return Array.from(acc).sort(compareTags);
}

function compareTags(a, b) {
  const left = String(a).toLowerCase();
  const right = String(b).toLowerCase();
  if (left < right) return -1;
  if (left > right) return 1;
  return String(a) < String(b) ? -1 : (String(a) > String(b) ? 1 : 0);
}

/**
 * Turn the edited common-tag rows into an edit_tags request body.
 *   - a row whose value changed -> rename pair
 *   - a row cleared to empty    -> delete pair (replace: "")
 *   - extra tags               -> appended (deduped, order kept)
 */
export function commonEdit(original, values, added) {
  const source = Array.isArray(original) ? original : [];
  const rows = Array.isArray(values) ? values : [];
  const pairs = [];
  source.forEach((tag, index) => {
    const raw = rows[index] === undefined ? tag : rows[index];
    const value = String(raw).trim();
    if (value !== tag) pairs.push({ find: tag, replace: value });
  });
  const known = new Set(source);
  const tags = [];
  for (const raw of Array.isArray(added) ? added : []) {
    const tag = String(raw).trim();
    if (!tag || known.has(tag) || tags.indexOf(tag) >= 0) continue;
    tags.push(tag);
  }
  return { pairs, tags };
}

function describeError(err) {
  return err && err.message ? err.message : String(err);
}

function cacheFailureCount(result) {
  const entries = (result && result.results) || [];
  let total = 0;
  for (const entry of entries) {
    const failed = entry && Array.isArray(entry.cache_remove_failed) ? entry.cache_remove_failed.length : 0;
    total += failed;
  }
  return total;
}

//: Consecutive EventSource failures tolerated before giving up (same as scale.js).
const STREAM_ERROR_LIMIT = 3;

/** EventSource payloads are JSON strings; a malformed one must not throw mid-stream. */
function parseStreamData(raw) {
  try {
    return JSON.parse(raw);
  } catch (err) {
    return null;
  }
}

export function createBatchPanel(options) {
  const config = options || {};
  const notify = config.notify || (() => {});
  const confirmFn = config.confirm || (() => true);
  //: The column's scope control (scope.js), built once in app.js and shared with the tagger.
  const scope = config.scope;
  const getTags = config.getTags || (() => null);
  //: Called after the scope changes; the app uses it to probe captions for the new set.
  const onScopeChange = config.onScopeChange || (() => {});
  //: The job shelf (js/jobs.js); optional so the panel still runs headless without one.
  const shelf = config.jobs || null;
  //: Same value as the tagger, the scale panel and the cache panel.
  const CANCEL_WATCHDOG_MS = 30000;
  const onAfterWrite = config.onAfterWrite || (() => {});

  const make = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  };
  const button = (className, text) => {
    const node = make("button", className, text);
    node.type = "button";
    return node;
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
  const field = (labelKey, control) => {
    const box = make("label", "field");
    box.append(paintText(make("span"), labelKey), control);
    return box;
  };
  const textInput = (className) => {
    const input = make("input", className);
    input.type = "text";
    input.spellcheck = false;
    input.autocomplete = "off";
    return input;
  };
  const checkbox = (className) => {
    const input = make("input", className);
    input.type = "checkbox";
    return input;
  };
  const inline = (control, labelKey) => {
    const box = make("label", "field-inline");
    box.append(control, paintText(make("span"), labelKey));
    return box;
  };
  const copyButton = (className, key) => paintText(button(className), key);

  // ---- tab + panel shell ------------------------------------------------
  const tab = make("button", "tab");
  tab.type = "button";
  tab.setAttribute("role", "tab");
  tab.dataset.tab = "batch";
  paintText(tab, "tab.batch");

  const panel = make("div", "tab-panel");
  panel.dataset.panel = "batch";
  panel.hidden = true;

  // "Which images?" is answered once for the whole column: app.js builds the control (the strip
  // above the tab panels) and hands the same instance to the tagger, so both panels act on one
  // value. This panel used to construct its own over its own <select> - two answers to one
  // question, and the one the button obeyed was not the one the other panel showed.
  scope.subscribe({
    // The control is shared, so it is repainted by whichever panel noticed the change; the buttons
    // of this panel are only on screen while its tab is. Running scopeInfo() for a panel nobody is
    // looking at would walk the whole scope once per caption chunk - the O(N^2) the chunked probe
    // exists to avoid - and onShow() recomputes them when the tab comes back.
    onPaint: () => { if (!panel.hidden) refreshButtons(); },
    onChange: () => {
      // A different scope is a different diff: the preview described another set of images.
      invalidatePreview();
      refreshScope();
      onScopeChange();
    },
  });
  const pendingNote = paintText(make("p", "hint"), "batch.commonPending");
  pendingNote.hidden = true;

  const OP_CHOICES = [
    [BATCH_COMMON, "batch.opCommon"],
    [BATCH_OP.APPEND, "batch.opAppend"],
    [BATCH_OP.PREPEND, "batch.opPrepend"],
    [BATCH_OP.REMOVE, "batch.opRemove"],
    [BATCH_OP.REPLACE, "batch.opReplace"],
    [BATCH_OP.REGEX_REPLACE, "batch.opRegexTag"],
    [BATCH_OP.REGEX_REPLACE_TEXT, "batch.opRegexText"],
    [BATCH_OP.NORMALIZE, "batch.opNormalize"],
    [BATCH_OP.SORT_DANBOORU, "batch.opSort"],
  ];
  const opSelect = make("select");
  for (const [value, key] of OP_CHOICES) {
    const option = make("option");
    option.value = value;
    paintText(option, key);
    opSelect.append(option);
  }

  // ---- section: common tags --------------------------------------------
  const commonList = make("div", "batch-common-list");
  const commonNone = paintText(make("p", "hint"), "batch.commonNone");
  const commonAddInput = textInput("batch-common-add");
  paintAttr(commonAddInput, "placeholder", "batch.commonAddPlaceholder");
  const commonAddButton = copyButton("btn", "batch.commonAdd");
  const addedList = make("div", "chips batch-added");
  const commonPrepend = checkbox();
  const commonSection = make("fieldset", "box batch-section");
  commonSection.append(
    paintText(make("legend"), "batch.commonTitle"),
    paintText(make("p", "hint"), "batch.commonHint"),
    commonList,
    commonNone,
    addedList,
    (() => { const row = make("div", "row"); row.append(commonAddInput, commonAddButton); return row; })(),
    inline(commonPrepend, "batch.commonPrepend"),
  );

  // ---- section: plain tag list -----------------------------------------
  const tagsInput = textInput();
  const tagsSection = make("fieldset", "box batch-section");
  tagsSection.append(
    field("batch.tagsLabel", tagsInput),
    paintText(make("p", "hint"), "batch.tagsHint"),
  );

  // ---- section: replace -------------------------------------------------
  const findInput = textInput();
  const replaceInput = textInput();
  const replaceIgnoreCase = checkbox();
  const replaceSection = make("fieldset", "box batch-section");
  replaceSection.append(
    field("batch.findLabel", findInput),
    field("batch.replaceLabel", replaceInput),
    inline(replaceIgnoreCase, "batch.ignoreCase"),
  );

  // ---- section: regex ---------------------------------------------------
  const patternInput = textInput();
  const replacementInput = textInput();
  const regexIgnoreCase = checkbox();
  const regexSection = make("fieldset", "box batch-section");
  regexSection.append(
    field("batch.patternLabel", patternInput),
    field("batch.replacementLabel", replacementInput),
    inline(regexIgnoreCase, "batch.ignoreCase"),
  );

  // ---- preview / apply --------------------------------------------------
  const previewButton = copyButton("btn btn-primary", "batch.preview");
  previewButton.id = "btn-batch-preview";
  // The apply button starts the write job (section 4.5 supplement); while the job
  // runs, this label plus the progress block below are the status line. The t()
  // call lives inside the painter, so a language switch repaints it from state.
  const applyButton = button("btn");
  applyButton.id = "btn-batch-apply";
  painters.push(() => {
    applyButton.textContent = writing
      ? t("batch.writing", { n: writingCount })
      : t("batch.apply");
  });
  applyButton.disabled = true;
  const actions = make("div", "row");
  actions.append(previewButton, applyButton);

  // ---- the write job's progress (section 4.5 supplement) -----------------
  const progressTrack = make("div", "progress-track");
  const progressFill = make("div", "progress-fill");
  progressFill.setAttribute("role", "progressbar");
  progressFill.setAttribute("aria-valuemin", "0");
  progressFill.setAttribute("aria-valuemax", "100");
  progressFill.setAttribute("aria-valuenow", "0");
  progressTrack.append(progressFill);
  const progressText = make("div", "hint");
  progressText.setAttribute("role", "status");
  progressText.setAttribute("aria-live", "polite");
  const cancelButton = button("btn btn-danger");
  cancelButton.hidden = true;
  painters.push(() => {
    cancelButton.textContent = cancelling ? t("batch.cancelling") : t("batch.cancel");
    progressText.textContent = jobCurrent
      ? t("batch.running", { done: jobDone, total: jobTotal })
        + " · " + t("batch.current", { name: jobCurrent })
      : t("batch.running", { done: jobDone, total: jobTotal });
  });
  const progressRow = make("div", "progress");
  progressRow.append(progressTrack, progressText, cancelButton);
  progressRow.hidden = true;
  const applyHint = paintText(make("p", "hint"), "batch.applyHint");
  // Parity with the tagger's default (backup_overwrite, p0-spec §4.5 (4)): a batch rewrite
  // overwrites the same files irreversibly, so the safety net is what you get without asking.
  const backupCheck = checkbox();
  backupCheck.checked = true;
  const backupRow = inline(backupCheck, "batch.backup");

  const diffTitle = paintText(make("h3", "panel-title"), "batch.diffTitle");
  diffTitle.hidden = true;
  const diffSummary = make("div", "hint");
  const diff = make("div", "diff");

  panel.append(
    paintText(make("h3", "panel-title"), "batch.title"),
    paintText(make("p", "hint"), "batch.hint"),
    pendingNote,
    field("batch.op", opSelect),
    commonSection,
    tagsSection,
    replaceSection,
    regexSection,
    actions,
    applyHint,
    backupRow,
    progressRow,
    diffTitle,
    diffSummary,
    diff,
  );

  if (config.tabsHost) config.tabsHost.append(tab);
  if (config.panelHost) config.panelHost.append(panel);

  // ---- state ------------------------------------------------------------
  let lastPreview = null;
  let writing = false;
  let writingCount = 0;
  // The job's own state, so relabel() and the progress line paint from it.
  let jobId = null;
  let stream = null;
  let streamErrors = 0;
  let jobDone = 0;
  let jobTotal = 0;
  let jobCurrent = "";
  let cancelling = false;
  let jobCacheFailures = 0;
  //: Which images this run actually wrote. The undo is offered for exactly this set, so a
  //: second run (or a scope change) cannot make the toast restore something else.
  let appliedImages = [];
  let undoing = false;
  //: This job's row on the shelf, so a write stays visible (and cancellable) from every tab.
  let shelfJob = null;
  //: The id this tab has bookmarked, so a reload can pick the job back up (jobstore.js).
  let runningJobId = null;
  let writeCancelWatchdog = null;
  let lastSig = null;
  let lastMissing = 0;
  let commonOriginal = [];
  let commonRows = [];
  let addedTags = [];

  function currentOp() {
    return opSelect.value || BATCH_COMMON;
  }

  /** The images this panel acts on, plus what is known about their captions. */
  function scopeInfo() {
    const paths = scope.paths();
    const lists = [];
    let missing = 0;
    for (const path of paths) {
      const tags = getTags(path);
      if (Array.isArray(tags)) lists.push(tags);
      else missing += 1;
    }
    return { paths, lists, missing };
  }

  function paintAdded() {
    addedList.replaceChildren();
    for (const tag of addedTags) {
      const chip = make("span", "chip", tag);
      const remove = button("chip-remove", "\u00d7");
      remove.title = t("caption.remove");
      // The visible glyph is "×"; name the button after the tag it removes.
      remove.setAttribute("aria-label", t("caption.remove").concat(" ").concat(tag));
      remove.addEventListener("click", () => {
        addedTags = addedTags.filter((value) => value !== tag);
        paintAdded();
        invalidatePreview();
      });
      chip.append(remove);
      addedList.append(chip);
    }
  }

  function addCommonTag() {
    const tag = commonAddInput.value.trim();
    if (!tag) return;
    if (addedTags.indexOf(tag) < 0 && commonOriginal.indexOf(tag) < 0) {
      addedTags = addedTags.concat([tag]);
      paintAdded();
    }
    commonAddInput.value = "";
    invalidatePreview();
  }

  function paintCommonRows(tags) {
    commonRows = [];
    commonList.replaceChildren();
    commonNone.hidden = tags.length > 0;
    if (tags.length === 0) return;
    const frag = document.createDocumentFragment();
    for (const tag of tags.slice(0, COMMON_ROWS)) {
      const row = make("div", "batch-common-row");
      const input = textInput("batch-common-input");
      input.value = tag;
      input.dataset.original = tag;
      input.addEventListener("input", () => {
        input.classList.toggle("is-removed", input.value.trim().length === 0);
        invalidatePreview();
      });
      const remove = button("btn btn-mini batch-common-remove", "\u00d7");
      remove.title = t("caption.remove");
      remove.addEventListener("click", () => {
        input.value = "";
        input.classList.add("is-removed");
        invalidatePreview();
      });
      row.append(input, remove);
      commonRows.push(input);
      frag.append(row);
    }
    commonList.append(frag);
    if (tags.length > COMMON_ROWS) {
      commonList.append(make("p", "hint", t("batch.commonMore", { n: tags.length - COMMON_ROWS })));
    }
  }

  function invalidatePreview() {
    lastPreview = null;
    diffTitle.hidden = true;
    diffSummary.textContent = "";
    diff.replaceChildren();
    applyButton.disabled = true;
  }

  function refreshButtons() {
    const info = scopeInfo();
    const needsCaptions = currentOp() === BATCH_COMMON && info.missing > 0;
    previewButton.disabled = writing || info.paths.length === 0 || needsCaptions;
    applyButton.disabled = writing || !lastPreview || lastPreview.applied <= 0;
  }

  function setWriting(value, count) {
    writing = value === true;
    if (writing) writingCount = count;
    paintLabels();
    // The label is the status line; this is what actually locks the panel.
    refreshButtons();
  }

  function paintSections() {
    const op = currentOp();
    commonSection.hidden = op !== BATCH_COMMON;
    tagsSection.hidden = ![BATCH_OP.APPEND, BATCH_OP.PREPEND, BATCH_OP.REMOVE].includes(op);
    replaceSection.hidden = op !== BATCH_OP.REPLACE;
    regexSection.hidden = ![BATCH_OP.REGEX_REPLACE, BATCH_OP.REGEX_REPLACE_TEXT].includes(op);
  }

  /**
   * Re-read the visible set. Cheap while the tab is hidden (the signature is
   * reset, so showing the tab always recomputes).
   */
  function refreshScope() {
    // The counts live in the shared control's labels and change as captions are probed, and the
    // strip they are painted into is on screen whichever tab is showing - so it is repainted here
    // even while this panel is closed. Everything below stays hidden-only: scopeInfo() walks the
    // whole scope, and the gallery re-applies the filter once per caption chunk.
    scope.paint();
    if (panel.hidden) {
      // Nothing else to do while the tab is closed; dropping the signature makes
      // the next show recompute.
      lastSig = null;
      return;
    }
    const info = scopeInfo();
    const sig = info.paths.join("\n");
    const changedScope = sig !== lastSig;
    const becameReady = info.missing === 0 && lastMissing > 0;
    lastMissing = info.missing;
    lastSig = sig;
    pendingNote.hidden = info.missing === 0;
    if (currentOp() === BATCH_COMMON) {
      if (info.missing > 0) {
        if (changedScope) { commonOriginal = []; paintCommonRows([]); }
      } else if (changedScope || becameReady) {
        commonOriginal = commonTags(info.lists);
        paintCommonRows(commonOriginal);
      }
    }
    if (changedScope || becameReady) invalidatePreview();
    refreshButtons();
  }

  /** Called by the tab handler after the panel is unhidden. */
  function onShow() {
    paintSections();
    refreshScope();
  }

  // ---- request building -------------------------------------------------
  /**
   * The select value is the op EXCEPT for the common-tag editor, whose choice
   * is the local pseudo-op "common": that is a UI mode, not something the
   * backend knows. It must be mapped to edit_tags before the request goes out,
   * or the user gets a 400 bad_op after clicking preview.
   */
  function requestOp(op) {
    return op === BATCH_COMMON ? BATCH_OP.EDIT_TAGS : op;
  }

  function buildPayload(paths) {
    const op = currentOp();
    const base = { images: paths, op: requestOp(op), backup: backupCheck.checked };
    if (op === BATCH_COMMON) {
      const values = commonRows.map((input) => input.value);
      const edit = commonEdit(commonOriginal, values, addedTags);
      if (edit.pairs.length === 0 && edit.tags.length === 0) {
        notify(t("batch.noChange"), "warn");
        return null;
      }
      return Object.assign(base, {
        pairs: edit.pairs,
        tags: edit.tags,
        position: commonPrepend.checked ? "prepend" : "append",
      });
    }
    if (op === BATCH_OP.APPEND || op === BATCH_OP.PREPEND || op === BATCH_OP.REMOVE) {
      const tags = splitTagInput(tagsInput.value);
      if (tags.length === 0) { notify(t("batch.missingParam"), "warn"); return null; }
      return Object.assign(base, { tags });
    }
    if (op === BATCH_OP.REPLACE) {
      const find = findInput.value.trim();
      if (!find) { notify(t("batch.missingParam"), "warn"); return null; }
      return Object.assign(base, {
        find,
        replace: replaceInput.value.trim(),
        ignore_case: replaceIgnoreCase.checked,
      });
    }
    if (op === BATCH_OP.REGEX_REPLACE || op === BATCH_OP.REGEX_REPLACE_TEXT) {
      const pattern = patternInput.value.trim();
      if (!pattern) { notify(t("batch.missingParam"), "warn"); return null; }
      return Object.assign(base, {
        pattern,
        replacement: replacementInput.value,
        ignore_case: regexIgnoreCase.checked,
      });
    }
    return base;
  }

  // ---- the diff table (shared by the preview and the write stream) ------
  //: The table currently on screen; null when nothing has been painted.
  let diffTable = null;
  //: How many rows this stream has appended, and whether the "more" note is in.
  let painted = 0;
  let moreNote = false;

  /** One outcome row. The preview table and the write stream must not drift apart. */
  function buildDiffRow(entry) {
    const row = make("tr");
    row.title = String(entry.image || "");
    const cellImage = make("td");
    if (entry.image) {
      // The row is identified by a title attribute otherwise, which a screen
      // reader never reads: name the file in the cell itself.
      cellImage.append(make("span", "sr-only", String(entry.image)));
      const img = make("img", "diff-img");
      img.loading = "lazy";
      img.decoding = "async";
      img.width = 48;
      img.height = 48;
      img.alt = "";
      img.src = thumbUrl(entry.image, THUMB_SIZE.GRID);
      cellImage.append(img);
    }
    const cellBefore = make("td", "before");
    const cellAfter = make("td", "after");
    if (entry.ok === false) {
      cellBefore.textContent = t("autotag.diffNone");
      cellAfter.textContent = String((entry.error && entry.error.message) || entry.error || "");
    } else {
      renderCaptionDiff(cellBefore, cellAfter, String(entry.before || ""), String(entry.after || ""));
    }
    row.append(cellImage, cellBefore, cellAfter);
    return row;
  }

  function resetDiffTable() {
    diff.replaceChildren();
    diffTable = null;
    painted = 0;
    moreNote = false;
  }

  function appendResultRow(entry) {
    if (!diffTable) {
      diffTable = make("table");
      const head = make("tr");
      for (const key of ["autotag.diffHead.image", "autotag.diffHead.before", "autotag.diffHead.after"]) {
        head.append(make("th", null, t(key)));
      }
      diffTable.append(head);
      diff.append(diffTable);
    }
    diffTable.append(buildDiffRow(entry));
    diffTitle.hidden = false;
  }

  // ---- preview ----------------------------------------------------------
  function paintDiff(result) {
    const entries = (result && result.results) || [];
    const failed = Number(result && result.failed) || 0;
    const changed = Number(result && result.applied) || 0;
    const same = Math.max(0, entries.length - failed - changed);
    diffSummary.textContent = t("batch.diffSummary", { changed, same, failed });
    resetDiffTable();
    const rows = entries.filter((entry) => entry && (entry.ok === false || entry.changed)).slice(0, PREVIEW_ROWS);
    diffTitle.hidden = rows.length === 0;
    if (rows.length === 0) return;
    for (const entry of rows) appendResultRow(entry);
    if (entries.filter((entry) => entry && (entry.ok === false || entry.changed)).length > PREVIEW_ROWS) {
      diff.append(make("p", "hint", t("batch.diffMore", { n: entries.length - PREVIEW_ROWS })));
    }
  }

  async function runPreview() {
    const paths = scope.paths();
    if (paths.length === 0) { notify(t("batch.noImages"), "warn"); return; }
    const payload = buildPayload(paths);
    if (!payload) return;
    previewButton.disabled = true;
    const label = previewButton.textContent;
    previewButton.textContent = t("batch.previewRunning");
    try {
      const result = await api.batchCaptions(Object.assign({ dry_run: true }, payload));
      lastPreview = result;
      paintDiff(result);
    } catch (err) {
      notify(t("batch.previewFailed", { message: describeError(err) }), "error");
      invalidatePreview();
    } finally {
      previewButton.textContent = label;
      refreshButtons();
    }
  }

  // ---- the write job (section 4.5 supplement) ---------------------------
  /**
   * The write is a background job now, so the panel paints progress from the
   * stream and can stop it. The table is rebuilt from the job's own events: the
   * preview it replaces is stale, and a failed image can only be located from
   * what the job reports.
   */
  function paintJobProgress(done, total, current) {
    jobDone = Number(done) || 0;
    jobTotal = Number(total) || jobTotal;
    jobCurrent = String(current || "");
    const pct = jobTotal > 0 ? Math.min(100, Math.round((100 * jobDone) / jobTotal)) : 0;
    progressFill.style.width = pct + "%";
    progressFill.setAttribute("aria-valuenow", String(pct));
    // The progress line and the cancel label live in painters, so this repaint
    // is also what a language switch uses.
    paintLabels();
  }

  function clearWriteCancelWatchdog() {
    if (writeCancelWatchdog === null) return;
    window.clearTimeout(writeCancelWatchdog);
    writeCancelWatchdog = null;
  }

  /**
   * A cancel the server accepted but never answered must not leave the panel - or the shelf row -
   * locked for good. Same value and the same shape as the tagger, the scale panel and the cache panel.
   */
  function armWriteCancelWatchdog() {
    clearWriteCancelWatchdog();
    writeCancelWatchdog = window.setTimeout(() => {
      writeCancelWatchdog = null;
      if (!writing) return;
      closeWriteStream();
      cancelling = false;
      cancelButton.hidden = true;
      cancelButton.disabled = false;
      progressRow.hidden = true;
      setWriting(false, 0);
      notify(t("batch.cancelTimeout"), "error");
    }, CANCEL_WATCHDOG_MS);
  }

  /** The one place a write job ends, whatever ended it (done, a lost stream, the watchdog). */
  function closeWriteStream() {
    clearWriteCancelWatchdog();
    if (shelfJob) {
      shelfJob.finish();
      shelfJob = null;
    }
    if (runningJobId) {
      forgetJob("batch", runningJobId);
      runningJobId = null;
    }
    if (stream) {
      stream.close();
      stream = null;
    }
    jobId = null;
  }

  function finishWriteJob(payload) {
    closeWriteStream();
    const summary = (payload && payload.summary) || {};
    const applied = Number(summary.applied) || 0;
    const failed = Number(summary.failed) || 0;
    const cancelled = Boolean(payload && payload.cancelled);
    // The write is reversible exactly when it kept a backup, and the undo is offered for
    // the images this job wrote - including a cancelled run, where it is what the user
    // most likely wants. The list is snapshotted **here**: the next run clears the live
    // one, and an undo that reads it at click time would restore nothing.
    const written = appliedImages.slice();
    const undo = backupCheck.checked && written.length > 0
      ? { label: t("app.undo"), run: () => { void undoWrite(written); } }
      : undefined;
    if (jobCacheFailures > 0) {
      notify(t("batch.doneWithCache", { applied, n: jobCacheFailures }), "error", undo);
    } else if (cancelled) {
      // "Stopped early", never "undone": what landed is already on disk - and the undo
      // is how the user takes that back.
      notify(t("batch.cancelled", { applied, total: jobTotal }), "warn", undo);
    } else {
      notify(t("batch.done", { applied, failed }), failed > 0 ? "warn" : "ok", undo);
    }
    cancelling = false;
    cancelButton.hidden = true;
    cancelButton.disabled = false;
    progressRow.hidden = true;
    setWriting(false, 0);
    onAfterWrite();
    refreshScope();
  }

  function openWriteStream(id, total) {
    jobId = String(id);
    runningJobId = jobId;
    rememberJob({ panel: "batch", jobId, total: Number(total) || 0 });
    streamErrors = 0;
    jobCacheFailures = 0;
    appliedImages = [];
    lastPreview = null;
    applyButton.disabled = true;
    resetDiffTable();
    diffSummary.textContent = "";
    diffTitle.hidden = true;
    progressRow.hidden = false;
    cancelButton.hidden = false;
    cancelButton.disabled = false;
    paintJobProgress(0, Number(total) || 0, "");
    shelfJob = shelf
      ? shelf.start({
          kind: "batch",
          label: t("tab.batch"),
          cancelLabel: t("batch.cancel"),
          total: Number(total) || 0,
          cancel: () => { void cancelWrite(); },
        })
      : null;
    stream = new EventSource(batchStreamUrl(jobId));
    stream.addEventListener("progress", (event) => {
      streamErrors = 0;
      const payload = parseStreamData(event.data);
      if (!payload || !payload.entry) return;
      jobCacheFailures += cacheFailureCount(payload.entry);
      if (payload.entry && payload.entry.ok === true && payload.entry.image) {
        appliedImages.push(String(payload.entry.image));
      }
      if (painted < PREVIEW_ROWS) {
        appendResultRow(payload.entry);
        painted += 1;
      } else if (!moreNote) {
        moreNote = true;
        diff.append(make("p", "hint", t("batch.diffMore", { n: Math.max(0, jobTotal - PREVIEW_ROWS) })));
      }
      paintJobProgress(payload.done, payload.total, payload.current);
    if (shelfJob) {
      shelfJob.update({ done: payload.done, total: payload.total, current: payload.current });
    }
    });
    stream.addEventListener("done", (event) => {
      const payload = parseStreamData(event.data);
      // Section 4.3: the end of a job is the finished flag, never done >= total.
      if (!payload || payload.finished !== true) return;
      finishWriteJob(payload);
    });
    stream.onerror = () => {
      // The stream closes itself after done; anything after that is noise.
      if (!stream) return;
      streamErrors += 1;
      if (streamErrors >= STREAM_ERROR_LIMIT) {
        closeWriteStream();
        notify(t("batch.writeFailed", { message: t("batch.streamLost") }), "error");
        cancelling = false;
        cancelButton.hidden = true;
        progressRow.hidden = true;
        setWriting(false, 0);
      }
    };
  }

  async function runApply() {
    if (!lastPreview || writing) return;
    const paths = scope.paths();
    // With the backup on the write is reversible, and the panel already made the user
    // look at the diff before this button went live - so that path is not gated by a
    // dialog; the copy says what is about to happen, and the toast carries the undo.
    // Turning the backup off makes the write irreversible again, and then it asks.
    if (!backupCheck.checked) {
      const ok = await confirmFn({
        body: t("batch.confirm", { n: paths.length }),
        confirmLabel: t("batch.apply"),
        danger: true,
      });
      if (!ok) return;
    }
    const payload = buildPayload(paths);
    if (!payload) return;
    setWriting(true, paths.length);
    try {
      const started = await api.batchCaptionsRun(payload);
      const id = started && (started.job_id || started.jobId);
      if (!id) {
        notify(t("batch.writeFailed", { message: t("common.noJobId") }), "error");
        setWriting(false, 0);
        return;
      }
      openWriteStream(id, started.total);
    } catch (err) {
      notify(t("batch.writeFailed", { message: describeError(err) }), "error");
      setWriting(false, 0);
    }
  }

  /**
   * Put this run's backups back. The images are the ones the job reported as written, and
   * the request is the same shape as the write: per-image results, so a partial undo is
   * reported as a partial undo.
   */
  async function undoWrite(paths) {
    if (undoing || paths.length === 0) return;
    undoing = true;
    notify(t("batch.undoing", { n: paths.length }), "info");
    try {
      const result = await api.restoreCaptions(paths);
      const restored = Number(result && result.restored) || 0;
      const failed = Number(result && result.failed) || 0;
      if (failed > 0) notify(t("batch.undoPartial", { restored, failed }), "warn");
      else notify(t("batch.undone", { restored }), "ok");
      onAfterWrite();
      refreshScope();
    } catch (err) {
      notify(t("batch.undoFailed", { message: describeError(err) }), "error");
    } finally {
      undoing = false;
    }
  }

  // ---- wiring -----------------------------------------------------------
  for (const input of [tagsInput, findInput, replaceInput, patternInput, replacementInput, commonAddInput]) {
    input.addEventListener("input", invalidatePreview);
  }
  for (const box of [replaceIgnoreCase, regexIgnoreCase, commonPrepend]) {
    box.addEventListener("change", invalidatePreview);
  }
  /**
   * Ask the job to stop. The stream's done event is what tears the panel down (and closes the shelf
   * row), so a cancel that is refused or fails has to hand the button back.
   */
  async function cancelWrite() {
    if (!jobId || cancelling) return;
    cancelling = true;
    cancelButton.disabled = true;
    paintLabels();
    armWriteCancelWatchdog();
    try {
      // The job keeps writing the image in hand; the stream's done event is what
      // says how far it got.
      await api.batchCaptionsCancel(jobId);
    } catch (err) {
      clearWriteCancelWatchdog();
      cancelling = false;
      cancelButton.disabled = false;
      paintLabels();
      notify(t("batch.writeFailed", { message: describeError(err) }), "error");
    }
  }
  cancelButton.addEventListener("click", () => { void cancelWrite(); });
  commonAddButton.addEventListener("click", addCommonTag);
  commonAddInput.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    addCommonTag();
  });
  opSelect.addEventListener("change", () => {
    paintSections();
    invalidatePreview();
    refreshScope();
  });
  previewButton.addEventListener("click", () => { void runPreview(); });
  applyButton.addEventListener("click", () => { void runApply(); });

  paintLabels();
  paintSections();
  refreshScope();

  /**
   * Relabel contract: repaint every construction-time string (tab button,
   * legends, labels, options) plus the added-tag chips, all from held state.
   * No request is issued and it is safe before any data has loaded.
   */
  function relabel() {
    paintLabels();
    paintAdded();
  }

  return {
    onShow,
    refreshScope,
    relabel,
    //: The command palette runs these three - the very functions this panel's buttons call.
    preview: runPreview,
    apply: runApply,
    cancel: cancelWrite,
    /**
     * Why one of them is unavailable, in this panel's own terms: a write in flight, an empty
     * scope, or a common-tag edit whose images have no captions to read. can* are the buttons'
     * own gates, so the palette cannot offer more than the panel would accept.
     */
    status: () => {
      const info = scopeInfo();
      return {
        writing,
        scope: info.paths.length,
        needsCaptions: currentOp() === BATCH_COMMON && info.missing > 0,
        // A preview that found nothing to change is not "no preview yet": /run would only answer
        // nothing-to-do, so the two get different reasons.
        previewEmpty: Boolean(lastPreview && lastPreview.applied <= 0),
        canPreview: !previewButton.disabled,
        canApply: !applyButton.disabled,
      };
    },
    /**
     * Pick a job back up after a page reload: the stream replays its whole event log, so the panel only
     * has to look busy again and open it - a job that finished meanwhile replays `done` immediately.
     */
    resume(jobId, total) {
      const count = Number(total) || 0;
      setWriting(true, count);
      openWriteStream(jobId, count);
    },
  };
}