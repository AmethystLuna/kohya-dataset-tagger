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
 *     text box would, which is why this is a row list). The same rows are also
 *     the **manual order** editor (2026-10-13): their two order containers are
 *     the backend's `order_first` / `order_last`, and the middle one means "leave
 *     these where they are" - so it is never sent. A role="list" of rows beside
 *     this editor would be a second live copy of the same tags.
 *
 * The pure helpers (commonTags / commonEdit / blockAdditions / orderEdit / moveRowIn)
 * are exported so the headless harness can test them without a DOM.
 */

import { api, BATCH_OP, batchStreamUrl, thumbUrl, THUMB_SIZE } from "./api.js";
import { forgetJob, rememberJob } from "./jobstore.js";
import { t } from "./strings.js";
import { rankSuggestions, createSuggest } from "./suggest.js";
import { renderCaptionDiff } from "./autotag.js";

/** Local pseudo-op: edit the tags common to every visible image. */
export const BATCH_COMMON = "common";

//: diff rows rendered before the table says "and N more". Diff tables grow
//: fast (one row per image) and this panel lives in a side column.
export const PREVIEW_ROWS = 8;

//: editable common-tag rows rendered. 1653 image datasets can share thousands
//: of tags; the search-able frequency table is the place to look those up.
export const COMMON_ROWS = 200;

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
 * Turn the edited common-tag rows into rename pairs: a row whose value changed is a rename, a row
 * cleared to empty is a delete (`replace: ""`).
 *
 * Additions are **not** here any more (2026-10-13): a new tag belongs to a container, and the two
 * containers carry their own additions (`blockAdditions` below).
 */
export function commonEdit(original, values) {
  const source = Array.isArray(original) ? original : [];
  const rows = Array.isArray(values) ? values : [];
  const pairs = [];
  source.forEach((tag, index) => {
    const raw = rows[index] === undefined ? tag : rows[index];
    const value = String(raw).trim();
    if (value !== tag) pairs.push({ find: tag, replace: value });
  });
  return { pairs };
}

/**
 * The additions of the two order blocks, as `edit_tags`' `tags_first` / `tags_last`.
 *
 * `added` is `[{tag, where}]` in the order the user typed them, `where` being one of the two
 * container names. A tag that is already a row of the common list is skipped: it is not an addition,
 * it is a tag to move, and the row already knows how to do that (drag it). Duplicates count once.
 */
export function blockAdditions(added, rows) {
  const known = new Set((Array.isArray(rows) ? rows : []).map((tag) => String(tag)));
  const first = [];
  const last = [];
  const seen = new Set();
  for (const item of Array.isArray(added) ? added : []) {
    const tag = String(item && item.tag !== undefined ? item.tag : item || "").trim();
    if (!tag || known.has(tag) || seen.has(tag)) continue;
    seen.add(tag);
    if (item && item.where === "back") last.push(tag);
    else first.push(tag);
  }
  // An empty block is not sent, for the same reason `orderEdit` leaves an unmoved middle out: the
  // request should carry what the user did, and `tags_last: []` reads like a decision.
  const out = {};
  if (first.length > 0) out.tags_first = first;
  if (last.length > 0) out.tags_last = last;
  return out;
}

function describeError(err) {
  return err && err.message ? err.message : String(err);
}

/** The three containers of the manual order editor, in the order they are rendered. */
export const ORDER_BUCKETS = ["front", "middle", "back"];

/** An empty container set: everything in the middle, i.e. "nothing has been moved". */
export function emptyBuckets() {
  return { front: [], middle: [], back: [] };
}

/**
 * Move one row inside the containers. Returns true when something really moved.
 *
 * Rows are identified by their index in the common-tag list, so a rename or a
 * delete (which are carried by the edit's `pairs`) cannot make a row's container
 * point at a different tag. `before` is the row to insert in front of, or -1 for
 * "at the end".
 *
 * Two rules come straight from what the containers mean:
 *   - a move **inside the middle** does nothing at all. The middle is "leave these
 *     where they are", so it has no order of its own to change (and the backend is
 *     never sent it), while a move *into* or *out of* the middle is a real move;
 *   - dropping a row onto itself is not a move.
 */
export function moveRowIn(buckets, index, bucket, before) {
  if (!buckets || !buckets[bucket]) return false;
  if (before === index) return false;
  const from = ORDER_BUCKETS.find((name) => (buckets[name] || []).indexOf(index) >= 0);
  if (from === "middle" && bucket === "middle") return false;
  for (const name of ORDER_BUCKETS) {
    const at = (buckets[name] || []).indexOf(index);
    if (at >= 0) buckets[name].splice(at, 1);
  }
  const target = buckets[bucket];
  let at = before === undefined || before === null ? -1 : target.indexOf(before);
  if (at < 0) at = target.length;
  target.splice(at, 0, index);
  return true;
}

/**
 * The order half of an `edit_tags` request, from the containers (`{front, middle, back}`
 * of row indices) and the rows' current values.
 *
 * An empty object means "send no order at all", which is the honest answer in two
 * cases: nothing was moved out of the middle (a multi-image scope), or the rows are
 * still in their original sequence (a single image, where a rename is carried by
 * `pairs` and not by the order). Sending an order that changes nothing would make
 * every preview say "0 changed" instead of "nothing to do".
 *
 * With a single image in scope the one container **is** the caption's order, so it
 * is sent whole: that is what lets the middleware-free reorder be a plain "this,
 * then that".
 */
export function orderEdit(buckets, values, single) {
  const state = buckets || emptyBuckets();
  const rows = Array.isArray(values) ? values : [];
  const list = (name) =>
    (state[name] || [])
      .map((index) => String(rows[index] === undefined ? "" : rows[index]).trim())
      .filter((tag) => tag.length > 0);
  const body = {};
  if (single) {
    const order = state.front || [];
    const original = order.slice().sort((a, b) => a - b);
    if (order.join(",") === original.join(",")) return body;
  } else if ((state.front || []).length === 0 && (state.back || []).length === 0) {
    return body;
  }
  const first = list("front");
  const last = single ? [] : list("back");
  if (first.length > 0) body.order_first = first;
  if (last.length > 0) body.order_last = last;
  return body;
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
  //: Ask the app to read the captions of these paths (app.js's probeScopeCaptions). The common-tag
  //: editor can compute nothing without them, and a scope can reach outside the directory on screen
  //: ("current directory + subdirectories", a selection), so "somebody else has probed it" is not an
  //: assumption this panel may make - without this it sat on its "reading caption…" note with empty
  //: containers forever (reported 2026-10-13).
  const requestCaptions = config.requestCaptions || (() => {});
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
  //: Every autocomplete this panel built, so a mode switch can close them all.
  const tagSuggests = [];
  /**
   * A tag field: the input plus the shared dropdown under it (suggest.js).
   *
   * The wrapper is built here, not inside suggest.js, because the dropdown is
   * positioned by the host's own `.suggest-wrap` and this panel's CSS expects the
   * input where it always was. `accept` is the host's own completion - a plain
   * field takes the tag as its whole value, the comma separated list has one -
   * and `mode` says which half of the candidates the field may offer (see
   * suggestTags): a function when the answer depends on the current op, which is
   * how one field can serve append, prepend and remove.
   */
  const tagField = (className, options) => {
    const opts = options || {};
    // The mode may depend on the current op (one field serves append, prepend and
    // remove), so it is read per query rather than captured once.
    const readMode = () => (typeof opts.mode === "function" ? opts.mode() : opts.mode);
    const input = textInput(className);
    const list = make("div", "suggest");
    list.hidden = true;
    const wrap = make("div", "suggest-wrap");
    wrap.append(input, list);
    tagSuggests.push(createSuggest({
      input,
      list,
      source: (query, limit) => suggestTags(query, limit, readMode()),
      query: opts.query,
      onAccept: (tag) => {
        if (opts.accept) opts.accept(input, tag);
        else input.value = tag;
        // A programmatic value change fires no "input" event, and the preview on
        // screen describes the parameters of the previous field values.
        invalidatePreview();
      },
    }));
    return { input, wrap };
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
  //: Guards the repaint path below against re-entering refreshScope (which repaints the control
  //: itself, and that notifies onPaint again).
  let refreshingScope = false;

  /**
   * A repaint of the control can mean the **answer** changed, not only the label: the recursive
   * listing arriving, or another panel's selection change, moves the scope's paths while the select's
   * value stays the same - and `onChange` never fires for that. The signature is what tells the two
   * apart, and the guard is what keeps the inner `scope.paint()` (inside refreshScope) from looping.
   */
  function onScopePaint() {
    if (panel.hidden) return;
    refreshButtons();
    if (refreshingScope) return;
    if (scope.paths().join("\n") === lastSig) return;
    refreshingScope = true;
    try {
      refreshScope();
    } finally {
      refreshingScope = false;
    }
  }

  scope.subscribe({
    // The control is shared, so it is repainted by whichever panel noticed the change; the buttons
    // of this panel are only on screen while its tab is. Running scopeInfo() for a panel nobody is
    // looking at would walk the whole scope once per caption chunk - the O(N^2) the chunked probe
    // exists to avoid - and onShow() recomputes them when the tab comes back.
    onPaint: onScopePaint,
    onChange: () => {
      // A different scope is a different diff: the preview described another set of images.
      invalidatePreview();
      refreshScope();
      onScopeChange();
    },
  });
  const pendingNote = paintText(make("p", "hint"), "batch.commonPending");
  pendingNote.hidden = true;

  // The op list is the panel's own: the common-tag editor covers "add a tag", "put a tag in front"
  // and "remove a tag" (its add rows, its containers, and clearing a row), so the three standalone
  // ops were dropped on 2026-10-13 (the common-tag editor covers them). A tag that is not
  // common is still removable with the replace op (leave the replacement empty).
  const OP_CHOICES = [
    [BATCH_COMMON, "batch.opCommon"],
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

  // ---- section: common tags (one editable, orderable row per tag) --------
  // The rows ARE the tags: one input per common tag (up to COMMON_ROWS). A renamed row renames that
  // tag, a cleared row deletes it, a deleted row does not shift the ones after it, and the row's
  // grip sets the order. There is deliberately no second list anywhere - a separate "reorder" panel
  // beside this editor would be two live copies of the same tags, and they would drift apart.
  //
  // The containers are the backend's two order blocks plus a middle that means "leave these where
  // they are", so the middle is never sent. With a single image in scope the middle is pointless -
  // that image's own order IS the answer - so the same rows live in one container.
  const orderHost = make("div", "batch-order");
  // The two **drop targets** share the top row, and the middle - which starts out holding every
  // common tag - sits below them: dragging a tag to the front / end container must not mean dragging past a 200-row
  // list first, and this way the two targets are the first thing on screen. That row is sticky in CSS,
  // so scrolling the long middle list cannot take the targets away mid-drag either.
  const orderTop = make("div", "batch-order-top");
  orderHost.append(orderTop);
  const orderHint = make("p", "hint");
  const bucketNodes = {};
  const bucketTitles = {};
  const bucketRows = {};
  const bucketAdded = {};
  const bucketAdd = {};
  for (const name of ORDER_BUCKETS) {
    const bucket = make("section", "batch-bucket");
    bucket.dataset.bucket = name;
    const title = make("div", "batch-bucket-title");
    const rows = make("div", "batch-bucket-rows");
    // The tags **added** to this block: they are not rows of the common list (they are not in every
    // caption), so they are chips - and they live in the container they belong to, which is the whole
    // point of choosing a container. They render on the block's **outer** side (above the rows in the
    // front container, below them in the end one), because that is where they land in the caption.
    //: The additions of this block - real rows, painted by paintAdded(), on the block's outer side.
    const chips = make("div", "batch-bucket-added");
    // The chips sit on the block's **outer** side - above the rows in the front container, below them
    // in the end one - because that is where the added tags land in the caption (`tags_first` is
    // spliced before the moved tags, `tags_last` after them).
    if (name === "front") bucket.append(title, chips, rows);
    else bucket.append(title, rows, chips);
    //: The front and end containers each carry a **permanent** add row: one full-width dashed `+`, the
    //: whole row and always there ("the dashed box can stay put"). Clicking it does not turn it into a box - it
    //: **appends a new element** to the container (an addition row, exactly the shape of a tag row), and
    //: an element left empty and blurred removes itself again. The middle gets none: an addition can
    //: only be expressed at one of the two ends.
    if (name !== "middle") {
      const addButton = make("button", "batch-bucket-add");
      addButton.type = "button";
      addButton.textContent = "+";
      // `setAttribute`, not `paintAttr`: `aria-label` is not a property of an element, so assigning it
      // would set a useless JS field and the accessible name would never exist. The painter below
      // repaints it on a language switch.
      addButton.setAttribute("aria-label", t("batch.commonAdd"));
      addButton.title = t("batch.commonAdd");
      addButton.addEventListener("click", () => { addBucketRow(name); });
      const addRow = make("div", "batch-common-row batch-bucket-add-row");
      addRow.append(addButton);
      bucket.append(addRow);
      bucketAdd[name] = addButton;
    }
    // The whole container is the drop target, not only its rows: an empty front container has to be
    // droppable, and "the container you are over" is what the highlight marks.
    bucket.addEventListener("dragover", (event) => {
      if (dragging === null && draggingAdd === null) return;
      if (typeof event.preventDefault === "function") event.preventDefault();
      paintDropTarget(name);
    });
    bucket.addEventListener("dragleave", () => { clearDropTarget(); });
    bucket.addEventListener("drop", (event) => {
      if (dragging === null && draggingAdd === null) return;
      if (typeof event.preventDefault === "function") event.preventDefault();
      // An addition dropped here is added at *this* end instead; the middle is not somewhere an
      // addition can live, so dropping one there takes it out again.
      if (draggingAdd !== null) {
        const item = draggingAdd;
        endDrag();
        moveAddTo(item, name);
        return;
      }
      const moved = dragging;
      const before = rowIndexUnder(event.target, bucket);
      endDrag();
      if (moveRowIn(orderBuckets, moved, name, before)) { layoutBuckets(); invalidatePreview(); }
    });
    // The two order blocks go side by side on the top row; the middle - the long one - below them.
    if (name === "middle") orderHost.append(bucket);
    else orderTop.append(bucket);
    bucketNodes[name] = bucket;
    bucketTitles[name] = title;
    bucketRows[name] = rows;
    bucketAdded[name] = chips;
  }
  //: The front container's own name and the hint both depend on the mode, and the mode is runtime
  //: state - so they are painted by one function that reads it, registered here for a language switch
  //: and called by layoutBuckets() whenever the mode could have changed.
  painters.push(paintModeCopy);
  //: The two + rows are the containers' permanent add control; their accessible name is copy too.
  painters.push(() => {
    for (const addName of ORDER_BUCKETS) {
      const node = bucketAdd[addName];
      if (!node) continue;
      node.setAttribute("aria-label", t("batch.commonAdd"));
      node.title = t("batch.commonAdd");
    }
  });
  paintText(bucketTitles.middle, "batch.orderMiddle");
  paintText(bucketTitles.back, "batch.orderBack");

  // The empty state names *which* nothing this is: "no images in the scope" and "the images share no
  // tag" are different facts, and the first one has a next step (choose a scope) while the second one
  // is a statement about the data. One key for both made the second read like a broken feature - and
  // painting it at construction time made it lie about a scope that was filled in later.
  const commonNone = make("p", "hint");
  //: The "N more common tags are not listed" note, rebuilt by paintCommonRows (COMMON_ROWS cap).
  const commonNote = make("div");
  const commonSection = make("fieldset", "box batch-section");
  commonSection.append(
    paintText(make("legend"), "batch.commonTitle"),
    paintText(make("p", "hint"), "batch.commonHint"),
    orderHost,
    commonNone,
    commonNote,
    orderHint,
  );

  // ---- section: replace -------------------------------------------------
  // "Find" names a tag the scope already carries, so it suggests from the scope
  // only; "replace with" may be a tag no image has yet, so the vocabulary is
  // there for it.
  const findField = tagField(null, { mode: "dataset" });
  const findInput = findField.input;
  const replaceField = tagField(null);
  const replaceInput = replaceField.input;
  const replaceIgnoreCase = checkbox();
  const replaceSection = make("fieldset", "box batch-section");
  replaceSection.append(
    field("batch.findLabel", findField.wrap),
    field("batch.replaceLabel", replaceField.wrap),
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
  //: The three containers (row indices) and the order inside them; index-aligned with
  //: commonOriginal / commonRows, so a rename cannot make a container point at another tag.
  let orderBuckets = emptyBuckets();
  //: One image in scope -> one container holding that caption's own order (see orderEdit).
  let reorderSingle = true;
  //: How many images the scope holds right now; the empty state says which nothing this is.
  let scopeImageCount = 0;
  //: The row being dragged (an index), or null. Row 0 is a real row: never test truthiness.
  let dragging = null;
  //: The addition row being dragged (the grip of a row in `bucketAdded`): see startAddDrag.
  let draggingAdd = null;
  //: The row elements, index-aligned with commonOriginal: `{row, grip, input, remove}`.
  let rowNodes = [];

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

  //: Tag (lowercased) -> {tag, count} over the scope's captions; null = not built.
  let scopeTagIndex = null;
  //: Images of the scope whose caption is not known yet: while this is > 0 the index
  //: is incomplete, and a "dataset" field still falls back to the vocabulary.
  let scopeTagMissing = 0;

  /** How many images of the scope carry each tag (one image counts once per tag). */
  function buildScopeTagIndex(lists) {
    const index = new Map();
    for (const tags of lists) {
      for (const tag of new Set(tags)) {
        const key = String(tag).toLowerCase();
        const known = index.get(key);
        if (known) known.count += 1;
        else index.set(key, { tag: String(tag), count: 1 });
      }
    }
    return index;
  }

  /**
   * The index is rebuilt wherever the scope is re-read (a scope change, a caption
   * probe chunk, a finished write) and never per keystroke: the walk is the same
   * order of work the common-tag rows already do there, and a suggestion list that
   * is one chunk behind is worse than a slow one.
   */
  function scopeTagIndexNow() {
    if (!scopeTagIndex) {
      const info = scopeInfo();
      scopeTagIndex = buildScopeTagIndex(info.lists);
      scopeTagMissing = info.missing;
    }
    return scopeTagIndex;
  }

  /**
   * Candidates for a tag field: the tags this scope actually uses first, then the
   * WD14 vocabulary through §4.10's endpoint.
   *
   * The dataset half comes first because it costs no request and is the useful
   * answer for remove / replace / find - those ops name a tag the images already
   * carry. The vocabulary half is what finds a tag no image carries yet, and it is
   * allowed to answer nothing: with no tagger model installed §4.10 returns an
   * empty list, and a dataset tag must still be suggested then.
   *
   * `mode` is what a field asks for:
   *   "both" (default) - the dataset's tags, then the vocabulary (append, prepend,
   *     replace, the common-tag add field: all of them may name a tag no image has
   *     yet, or write one in the vocabulary's own spelling);
   *   "dataset" - the scope's tags only (find, and remove's list). A tag no image
   *     carries matches nothing in those ops, so offering it is a suggestion that
   *     cannot be acted on.
   * The one exception is a scope whose captions are still being read: the index is
   * incomplete then, and suppressing the vocabulary would mean suggesting nothing
   * at all - so the vocabulary is allowed until the probe finishes.
   */
  async function suggestTags(query, limit, mode) {
    const needle = String(query || "").trim();
    if (!needle) return [];
    const local = rankSuggestions(Array.from(scopeTagIndexNow().values()), needle, limit);
    if (local.length >= limit) return local;
    if (mode === "dataset" && scopeTagMissing === 0) return local;
    let vocabulary = [];
    try {
      const payload = await api.tagAutocomplete(needle, limit);
      vocabulary = (payload && payload.suggestions) || [];
    } catch (err) {
      vocabulary = [];  // an addition, never a reason to show nothing at all
    }
    const seen = new Set(local.map((item) => String(item.tag).toLowerCase()));
    const merged = local.slice();
    for (const item of vocabulary) {
      if (!item || !item.tag) continue;
      const key = String(item.tag).toLowerCase();
      if (seen.has(key)) continue;
      seen.add(key);
      merged.push(item);
      if (merged.length >= limit) break;
    }
    return merged;
  }

  /**
   * Paint the additions of both blocks as **editable rows**, inside the container each belongs to.
   *
   * They are rows, not chips (the user: "implement it by adding a new element"): clicking the permanent + of
   * a container appends one of these - the same shape as a tag row (grip, input, the × that removes it) -
   * so adding a tag is the same gesture as editing one, and an element left empty and blurred is simply
   * taken away again. A row here is an *addition*: it is not in every caption, which is why it is not one
   * of the common rows and why it travels in the request as `tags_first` / `tags_last`.
   *
   * An existing row is **reused and only re-labelled**, not rebuilt: `relabel()` runs this on a language
   * switch, and a rebuild would throw away a half-typed tag (it would also lose the focus). The copy is
   * therefore painted here on every call rather than registered as a painter.
   */
  function paintAdded() {
    for (const name of ORDER_BUCKETS) bucketAdded[name].replaceChildren();
    for (const item of addedTags) {
      const row = item.row || buildAddedRow(item);
      const label = item.tag || t("batch.commonAddPlaceholder");
      item.input.placeholder = t("batch.commonAddPlaceholder");
      item.grip.setAttribute("aria-label", t("batch.dragHandle", { tag: label }));
      // The visible glyph is "×"; name the button after the tag it removes.
      item.remove.setAttribute("aria-label", t("caption.remove").concat(" ").concat(label));
      bucketAdded[item.where === "back" ? "back" : "front"].append(row);
    }
  }

  /** Build one addition row and wire it once; paintAdded() re-labels it from then on. */
  function buildAddedRow(item) {
    const row = make("div", "batch-common-row batch-added-row");
    const grip = make("span", "batch-grip", "\u28ff");
    grip.setAttribute("role", "button");
    grip.tabIndex = 0;
    grip.draggable = true;
    const input = textInput("batch-common-input batch-added-input");
    input.value = item.tag;
    const remove = button("btn btn-mini batch-common-remove", "\u00d7");
    remove.title = t("caption.remove");
    input.addEventListener("input", () => {
      item.tag = input.value.trim();
      invalidatePreview();
    });
    input.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        dropAdded(item);
        return;
      }
      if (event.key !== "Enter" || event.defaultPrevented) return;
      event.preventDefault();
      commitAdded(item);
    });
    // Nothing typed and the focus is gone: the element was never wanted, so it goes away by itself.
    input.addEventListener("blur", () => { commitAdded(item); });
    remove.addEventListener("click", () => { dropAdded(item); });
    // The grip takes the addition to the other container - choosing the end is the one thing an
    // addition can be *moved* for (the two containers carry `tags_first` / `tags_last`).
    grip.addEventListener("dragstart", (event) => { startAddDrag(item, row, event); });
    grip.addEventListener("dragend", () => { endDrag(); });
    row.append(grip, input, remove);
    item.row = row;
    item.grip = grip;
    item.input = input;
    item.remove = remove;
    return row;
  }

  /** Append an empty addition row to one of the two blocks and put the cursor in it. */
  function addBucketRow(where) {
    const item = { tag: "", where };
    addedTags = addedTags.concat([item]);
    paintAdded();
    if (item.input && typeof item.input.focus === "function") item.input.focus();
    invalidatePreview();
  }

  /** Take an addition out again (the ×, Escape, an empty blur, or a drop into the middle). */
  function dropAdded(item) {
    addedTags = addedTags.filter((value) => value !== item);
    paintAdded();
    invalidatePreview();
  }

  /**
   * The addition is done being typed. An empty one disappears; one that names a tag of the common list
   * is refused - that tag is already in the list, and the row is the control for *moving* it.
   */
  function commitAdded(item) {
    const tag = String(item.tag || "").trim();
    if (!tag) {
      dropAdded(item);
      return;
    }
    if (commonOriginal.indexOf(tag) >= 0) {
      notify(t("batch.addIsCommon", { tag }), "warn");
      dropAdded(item);
      return;
    }
    invalidatePreview();
  }

  /** Move an addition to the other block; the middle is not somewhere an addition can live. */
  function moveAddTo(item, where) {
    if (where === "middle") {
      dropAdded(item);
      return;
    }
    if (item.where === where) return;
    item.where = where;
    paintAdded();
    invalidatePreview();
  }

  /** Rewrite the three containers from the state: a moved row is in exactly one of them, in order. */
  function layoutBuckets() {
    for (const name of ORDER_BUCKETS) {
      bucketRows[name].replaceChildren(...orderBuckets[name].map((index) => rowNodes[index].row));
    }
    orderHost.classList.toggle("is-single", reorderSingle);
    // The front container's name and the hint are **mode** copy, and the mode is runtime state: a
    // construction-time painting left the single-image wording in place while three containers were
    // on screen (seen in a screenshot: the single-container title above the two other containers).
    paintModeCopy();
  }

  /** The two strings that depend on the mode; also registered as painters (see `painters`). */
  function paintModeCopy() {
    bucketTitles.front.textContent = t(reorderSingle ? "batch.orderSingle" : "batch.orderFront");
    orderHint.textContent = t(reorderSingle ? "batch.orderHintSingle" : "batch.orderHintMulti");
  }

  /** Which container a row is in right now. */
  function bucketOf(index) {
    return ORDER_BUCKETS.find((name) => orderBuckets[name].indexOf(index) >= 0) || null;
  }

  /** The row index an event landed on, walking up to `host`; -1 = the container's own background. */
  function rowIndexUnder(node, host) {
    for (let current = node; current && current !== host; current = current.parentNode) {
      const index = rowNodes.findIndex((entry) => entry.row === current);
      if (index >= 0) return index;
    }
    return -1;
  }

  function paintDropTarget(name) {
    for (const other of ORDER_BUCKETS) bucketNodes[other].classList.toggle("is-drop-target", other === name);
  }

  function clearDropTarget() {
    for (const other of ORDER_BUCKETS) bucketNodes[other].classList.remove("is-drop-target");
  }

  function startDrag(index, row, event) {
    dragging = index;
    draggingAdd = null;
    row.classList.add("is-dragging");
    // The drag image is the whole row, not the little grip: dragging "the handle" alone would read
    // as if the handle were what moves. Both calls are optional (a browser may refuse either).
    const transfer = event ? event.dataTransfer : null;
    if (transfer) {
      if (typeof transfer.setData === "function") transfer.setData("text/plain", String(index));
      if (typeof transfer.setDragImage === "function") transfer.setDragImage(row, 8, 8);
    }
  }

  /** The same gesture for an **addition** row: what moves is which end the tag is added to. */
  function startAddDrag(item, row, event) {
    dragging = null;
    draggingAdd = item;
    row.classList.add("is-dragging");
    const transfer = event ? event.dataTransfer : null;
    if (transfer) {
      if (typeof transfer.setData === "function") transfer.setData("text/plain", String(item.tag || ""));
      if (typeof transfer.setDragImage === "function") transfer.setDragImage(row, 8, 8);
    }
  }

  function endDrag() {
    dragging = null;
    if (draggingAdd && draggingAdd.row) draggingAdd.row.classList.remove("is-dragging");
    draggingAdd = null;
    clearDropTarget();
    for (const entry of rowNodes) entry.row.classList.remove("is-dragging");
  }

  /** Move a row one position inside its own container. Returns nothing; the middle never moves. */
  function moveWithinBucket(index, delta) {
    const name = bucketOf(index);
    if (name === null || name === "middle") return;
    const list = orderBuckets[name];
    const at = list.indexOf(index);
    const next = at + delta;
    if (at < 0 || next < 0 || next >= list.length) return;
    list.splice(at, 1);
    list.splice(next, 0, index);
    layoutBuckets();
    invalidatePreview();
  }

  /**
   * The keyboard's half of the reorder, which is the same three containers the mouse has:
   * ↑/↓ move a row inside its container, Home puts it at the very front and End at the very end.
   * A control that only a mouse can reach would be a dead end for everyone else.
   *
   * Home/End target the *containers*, not a position: with one image in scope there is only one
   * container, so both mean "the first / the last row of it" - the hidden containers must never
   * become a place a row can be sent to.
   */
  function onGripKey(index, event) {
    const key = event ? event.key : "";
    let target = null;
    let before = -1;
    if (key === "ArrowUp" || key === "ArrowDown") {
      moveWithinBucket(index, key === "ArrowUp" ? -1 : 1);
    } else if (key === "Home") {
      target = "front";
      before = (orderBuckets.front || [])[0];
    } else if (key === "End") {
      target = reorderSingle ? "front" : "back";
      before = -1;
    } else {
      return;
    }
    if (target !== null && moveRowIn(orderBuckets, index, target, before === undefined ? -1 : before)) {
      layoutBuckets();
      invalidatePreview();
    }
    if (typeof event.preventDefault === "function") event.preventDefault();
  }

  /** The per-row names, painted from the held row list so a language switch reaches them too. */
  function paintRowTools() {
    rowNodes.forEach((entry, index) => {
      const tag = String(commonOriginal[index] === undefined ? "" : commonOriginal[index]);
      entry.grip.title = t("batch.dragHandle", { tag });
      entry.grip.setAttribute("aria-label", t("batch.dragHandle", { tag }));
      entry.remove.title = t("caption.remove");
      entry.remove.setAttribute("aria-label", t("caption.remove") + " " + tag);
    });
  }

  /**
   * Which nothing the containers are empty for. `rowCount` is passed in rather than read from
   * `commonRows`, because the call happens *before* the rows of the next paint are built.
   */
  function paintEmptyNote(rowCount) {
    commonNone.textContent = t(scopeImageCount === 0 ? "batch.commonNoneImages" : "batch.commonNone");
    commonNone.hidden = rowCount > 0;
  }

  function paintCommonRows(tags) {
    commonRows = [];
    rowNodes = [];
    for (const name of ORDER_BUCKETS) bucketRows[name].replaceChildren();
    commonNote.replaceChildren();
    // The container host stays on screen even with nothing in it. It used to be hidden whenever the
    // scope had no common tags, which is the ordinary state (a few images with different captions
    // share nothing) - and then the whole manual-order feature had no entrance at all: nothing on
    // screen said it existed. An empty container plus the line that says *why* it is empty is the
    // honest version of "there is nothing to order here".
    orderHost.hidden = false;
    orderHint.hidden = tags.length === 0;
    paintEmptyNote(tags.length);
    if (tags.length === 0) {
      orderBuckets = emptyBuckets();
      layoutBuckets();
      return;
    }
    for (const tag of tags.slice(0, COMMON_ROWS)) {
      const index = commonRows.length;
      const row = make("div", "batch-common-row");
      const grip = make("span", "batch-grip", "\u28ff");
      grip.setAttribute("role", "button");
      grip.tabIndex = 0;
      grip.draggable = true;
      const input = textInput("batch-common-input");
      input.value = tag;
      input.dataset.original = tag;
      input.addEventListener("input", () => {
        input.classList.toggle("is-removed", input.value.trim().length === 0);
        invalidatePreview();
      });
      const remove = button("btn btn-mini batch-common-remove", "\u00d7");
      remove.addEventListener("click", () => {
        input.value = "";
        input.classList.add("is-removed");
        invalidatePreview();
      });
      grip.addEventListener("dragstart", (event) => { startDrag(index, row, event); });
      grip.addEventListener("dragend", () => { endDrag(); });
      grip.addEventListener("keydown", (event) => { onGripKey(index, event); });
      row.append(grip, input, remove);
      commonRows.push(input);
      rowNodes.push({ row, grip, input, remove });
    }
    // The starting containers: a single image's own order in one container, a multi-image scope
    // with everything in the middle (nothing has been moved yet, so nothing will be sent).
    const all = rowNodes.map((entry, index) => index);
    orderBuckets = reorderSingle
      ? { front: all, middle: [], back: [] }
      : { front: [], middle: all, back: [] };
    layoutBuckets();
    paintRowTools();
    if (tags.length > COMMON_ROWS) {
      commonNote.append(make("p", "hint", t("batch.commonMore", { n: tags.length - COMMON_ROWS })));
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
    // A field of the section that just closed must not keep a dropdown open over
    // the section that just opened.
    for (const suggest of tagSuggests) suggest.close();
    commonSection.hidden = op !== BATCH_COMMON;
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
    // One image in scope is a different editor from several: its own tag order is what a reorder can
    // change, while a multi-image scope can only say "these first / these last" (the middle keeps
    // every caption's own order). A scope that grew or shrank therefore rebuilds the rows even when
    // the tag list happens to look the same.
    const single = info.paths.length === 1;
    const modeChanged = single !== reorderSingle;
    reorderSingle = single;
    scopeImageCount = info.paths.length;
    // The tag fields suggest from these lists; rebuilding here keeps them as
    // fresh as the common-tag rows, and a keystroke still costs no rebuild.
    scopeTagIndex = buildScopeTagIndex(info.lists);
    scopeTagMissing = info.missing;
    lastMissing = info.missing;
    lastSig = sig;
    pendingNote.hidden = info.missing === 0;
    // Ask the app for the captions this scope needs. Nothing else does it for images outside the
    // directory on screen, and the common-tag editor has nothing to show until they arrive.
    if (info.missing > 0) requestCaptions(info.paths);
    if (currentOp() === BATCH_COMMON) {
      if (info.missing > 0) {
        if (changedScope) { commonOriginal = []; paintCommonRows([]); }
      } else if (changedScope || becameReady || modeChanged) {
        // A single image needs its **own** order (the caption's), not the alphabetical intersection:
        // the whole point of the single container is to show the order that is on disk.
        commonOriginal = single ? (info.lists[0] || []).slice() : commonTags(info.lists);
        paintCommonRows(commonOriginal);
      }
    }
    if (changedScope || becameReady || modeChanged) invalidatePreview();
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
      const edit = commonEdit(commonOriginal, values);
      // The order half is empty unless the user really moved a row (orderEdit explains both cases).
      const order = orderEdit(orderBuckets, values, reorderSingle);
      // The two container add rows are additions, and each names its own block.
      const added = blockAdditions(addedTags, commonOriginal);
      const nothing = edit.pairs.length === 0
        && !added.tags_first && !added.tags_last
        && !order.order_first && !order.order_last;
      if (nothing) {
        notify(t("batch.noChange"), "warn");
        return null;
      }
      return Object.assign(base, { pairs: edit.pairs }, added, order);
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
  for (const input of [findInput, replaceInput, patternInput, replacementInput]) {
    input.addEventListener("input", invalidatePreview);
  }
  for (const box of [replaceIgnoreCase, regexIgnoreCase]) {
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
    // The rows' own names (the grip's "drag to reorder <tag>" and the remove button's) are painted
    // with t() when a row is built, so a language switch has to repaint them from the held list -
    // without rebuilding the rows, which would throw away the order the user arranged.
    paintRowTools();
    // The empty state's wording depends on the scope and the rows, which are held state too.
    paintEmptyNote(commonRows.length);
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