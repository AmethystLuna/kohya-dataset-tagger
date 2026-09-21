/**
 * Application assembly: routing between roots, sub directories and the gallery,
 * plus the wiring of the tag editor, the tag filter, the autotag panel and the
 * cache status panel.
 *
 * Data flow for one directory:
 *   GET /api/fs/list            -> dirs + images + counts
 *   GET /api/captions (xN, <=6) -> per image: exists / tags / multiline warning
 *   GET /api/tags/frequency     -> frequency table of the filter panel
 *   GET /api/cache/status       -> stale text encoder caches
 * The gallery itself only ever pulls /api/images/thumb?size=grid.
 */

import { api, thumbUrl, THUMB_SIZE } from "./api.js";
import {
  t,
  hasKey,
  applyCopy,
  WARNING_GROUPS,
  LOCALES,
  getLocale,
  setLocale,
  initLocale,
  onLocaleChange,
} from "./strings.js";
import { createGallery } from "./gallery.js";
import { createTagEditor } from "./tags.js";
import { createFilterPanel } from "./filter.js";
import { createBatchPanel } from "./batch.js";
import { createAutotagPanel } from "./autotag.js";
import { createRootsPanel, createModelRootsPanel } from "./settings.js";
import { createConfirm } from "./confirm.js";
import { createJobShelf } from "./jobs.js";
import { forgetJob, readRunningJobs, rememberJob } from "./jobstore.js";
import { cacheStreamUrl } from "./api.js";
import { createPicker } from "./picker.js";
import { createZoom } from "./zoom.js";
import { createResizers } from "./resizer.js";
import { createScopeControl } from "./scope.js";
import { createReadinessStrip, cacheStatusText } from "./readiness.js";
import { createScalePanel } from "./scale.js";
import { createCropPanel } from "./crop.js";
import { createTrainParams } from "./trainparams.js";
import { childOnPath, createScrollMemory, revealRow, samePath } from "./navscroll.js";
import { captionProbeChunks, applyCaptionResults } from "./captionprobe.js";
import { createCommandPalette } from "./palette.js";
import { createThemeSwitch } from "./theme.js";

const TOAST_MS = 6000;
//: How often the connection pill re-checks the service. It used to be painted
//: once at boot and never again, so it kept claiming "connected" after the
//: backend died - and every action then failed with a toast that vanished.
const HEALTH_POLL_MS = 10000;

const $ = (id) => document.getElementById(id);

const state = {
  root: null,
  rootName: "",
  roots: [],
  dir: null,
  parent: null,
  images: [],
  dirs: [],
  counts: null,
  openToken: 0,
  activeToken: 0,
};

const captionInfo = new Map();
/** Paths that have no caption at all; set by applyFilter, read by the filter panel. */
let missingPaths = new Set();

// Section 4.12: the graphical dataset picker plus the capability the server
// reports for it. A non-loopback bind disables the picker *and* the roots
// mutations, so the UI greys its entry points out instead of failing on click.
const pathConfig = { allowed: false, host: "", reason: null };

// Resolve the locale BEFORE the first panel is constructed.
//
// Every factory below runs at module scope (this is a type=module script), and
// each one calls t() while painting its construction-time copy. If the locale is
// still the default zh-CN at that moment, those strings are frozen in Chinese:
// boot()'s applyCopy() only fills elements carrying data-copy* attributes, so it
// cannot touch what a factory already painted, and relabel() only runs on a
// locale CHANGE, never on first paint. Driving the real UI at ?lang=en showed
// 103 unique CJK runs in the right-hand panel (batch/export/scale stayed fully
// Chinese even after a zh-CN -> en switch).
//
// initLocale() only reads window.location / localStorage / navigator and writes
// document.documentElement.lang - all of which exist while a type=module script
// is being evaluated - so it is safe to call here.
initLocale();

// The theme (js/theme.js). The attribute is already on <html> - js/theme-boot.js, a classic <head>
// script, put it there before the first paint - and this takes over from here: it adopts the
// switch, and from now on it is the single writer of both the attribute and the checkbox. Built at
// module scope, next to initLocale(), because the theme is not a panel and nothing about it is
// render-time state.
const themeSwitch = createThemeSwitch({ input: $("theme-dark") });

// Navigation scroll (see navscroll.js). Two listings can be the one the user was
// reading - the folder cards in the centre at a folder-only level, the left
// column otherwise - so both remember their position per directory, and both
// prefer the folder a navigation came out of over that remembered position.
const dirScroll = createScrollMemory();
const gridScroll = createScrollMemory();

const picker = createPicker({ notify: (message, kind) => notify(message, kind) });

// ---------------------------------------------------------------------------
// toasts
// ---------------------------------------------------------------------------
//: One in-app confirmation dialog for the whole app: the browser's window.confirm
//: cannot name the action, and its buttons follow the browser language, not ours.
const confirmDialog = createConfirm();
const askConfirm = (spec) => confirmDialog.ask(spec);

//: One shelf for every long job, whichever tab started it (js/jobs.js). The panels push the numbers they
//: already paint, so a running job is never visible only from the tab that began it.
//: Set once the palette exists (it is built after the panels it reaches into); the shelf calls it
//: when the SET of running jobs changes, so an entry disabled with "a job is running" stops saying
//: so the moment the job ends - a reason on screen is a claim, and a stale claim is a lie.
let paletteRefresh = () => {};

const jobShelf = createJobShelf({
  host: $("jobs"),
  onOpen: (kind) => { openTab(kind); },
  onChange: () => { paletteRefresh(); },
});
//: Set by setupTabs(); the shelf uses it to open the tab that owns a job.
let openTab = () => {};

/** EventSource payloads are JSON strings; a malformed one must not throw mid-stream. */
function parseStreamData(raw) {
  try {
    return JSON.parse(raw);
  } catch (err) {
    return null;
  }
}

function notify(message, kind, action) {
  const host = $("toasts");
  const box = document.createElement("div");
  box.className = "toast toast-" + (kind || "info");
  // #toasts is a polite live region; an error interrupts instead, so a screen
  // reader hears a failed write without waiting for the queue to drain.
  box.setAttribute("role", kind === "error" ? "alert" : "status");
  const text = document.createElement("span");
  text.className = "toast-text";
  text.textContent = message;
  box.append(text);
  const close = () => box.remove();
  // An error that retires on its own leaves the user with nothing to act on:
  // errors stay until dismissed, and a caller that can repeat the action says so.
  if (action && typeof action.retry === "function") {
    const retry = document.createElement("button");
    retry.type = "button";
    retry.className = "btn btn-mini";
    retry.textContent = t("app.retry");
    retry.addEventListener("click", () => { close(); action.retry(); });
    box.append(retry);
  }
  // A caller that can take the action back says so, with its own label: "undo" is not
  // "retry", and the toast is the only place the escape hatch can live once the write
  // has already happened.
  // A caller that can take the action back says so, with its own label: "undo" is not
  // "retry", and the toast is the only place the escape hatch can live once the write
  // has already happened.
  if (action && typeof action.run === "function" && action.label) {
    const run = document.createElement("button");
    run.type = "button";
    run.className = "btn btn-mini";
    run.textContent = action.label;
    run.addEventListener("click", () => { close(); action.run(); });
    box.append(run);
  }
  if (kind === "error") {
    const dismiss = document.createElement("button");
    dismiss.type = "button";
    dismiss.className = "btn btn-mini";
    dismiss.textContent = t("app.dismiss");
    dismiss.addEventListener("click", close);
    box.append(dismiss);
  }
  host.append(box);
  // Errors stay until dismissed, and so does anything carrying an action: an escape
  // hatch that retires while the user is still reading it is not an escape hatch.
  const sticky = kind === "error" || Boolean(action && typeof action.run === "function");
  if (!sticky) window.setTimeout(close, TOAST_MS);
}

/**
 * Render a backend-originated message from its frozen [code] + params.
 *
 * Returns null when the pack has no translation, or when the template still
 * holds an unfilled {placeholder} - the backend's own text then carries more
 * than the template would. Callers fall back to that raw text, so a message is
 * never dropped and never replaced by a bare key.
 */
function localizeBackendMessage(prefix, code, params) {
  const name = String(code === null || code === undefined ? "" : code);
  if (name.length === 0) return null;
  const key = prefix + name;
  if (!hasKey(key)) return null;
  const values = params && typeof params === "object" ? params : {};
  const text = t(key, values);
  return /\{\w+\}/.test(text) ? null : text;
}

function describeError(err) {
  if (!err) return String(err);
  const localized = localizeBackendMessage("msg.", err.code, err.params);
  if (localized !== null) return localized;
  return err.message ? err.message : String(err);
}

// ---------------------------------------------------------------------------
// components
// ---------------------------------------------------------------------------
const gallery = createGallery({
  container: $("gallery"),
  marquee: $("marquee"),
  centerPanel: $("center-panel"),
  onSelectionChange: (info) => {
    const el = $("selection-info");
    if (info.count === 0) el.textContent = t("gallery.selectionNone");
    else if (info.count === 1) el.textContent = t("gallery.selectionOne");
    else el.textContent = t("gallery.selectionMany", { n: info.count });
    autotagPanel.refreshScopes();
    // Selection is one of the filter layers, so a selection change can change
    // what the gallery shows - but only while "selection only" is on.
    if (filterPanel.hasSelectionFilter()) applyFilter();
  },
  onActiveChange: (path) => { void activateImage(path); },
  // Double-click a tile: enlarged preview with the tags beside it (editable).
  onOpen: (path, paths) => { void zoom.open(path, paths); },
  // Section 4.13: a folder card opens that sub directory in place.
  onOpenDir: (path) => { void openDir(path, state.rootName); },
  onThumbError: (item) => notify(t("gallery.thumbFailed", { name: item.name }), "warn"),
});

const tagEditor = createTagEditor({
  chips: $("tag-chips"),
  input: $("tag-input"),
  suggest: $("tag-suggest"),
  addButton: $("btn-add-tag"),
  saveButton: $("btn-save-tags"),
  state: $("save-state"),
  warning: $("caption-warning"),
  captionFile: $("caption-file"),
  rawToggle: $("raw-toggle"),
  rawText: $("raw-text"),
  // The dock's control block: hid while no image is active, so the panel shows
  // the short empty state instead of a disabled, empty form.
  form: $("caption-form"),
  // A sidebar write changes the tags the filter, the frequency table and the
  // missing-caption badge are derived from. Without this the app keeps showing
  // the pre-edit caption as if it were current (the enlarged view already
  // refreshes; the sidebar used to be the one that did not).
  confirm: askConfirm,
  onSaved: () => {
    const path = tagEditor.getPath();
    if (!path) return;
    void refreshCaption(path);
    void loadFrequency();
  },
  onError: (message) => notify(message, "error"),
  onNotify: (message, kind) => notify(message, kind),
});

/**
 * Enlarged preview (double-click in the center workspace).
 *
 * It hosts a second tag editor instance, so the sidebar's is flushed and
 * disabled while it is open - two live copies of one caption would overwrite
 * each other. On close the sidebar follows the image that was last shown.
 */
const zoom = createZoom({
  notify: (message, kind) => notify(message, kind),
  confirm: askConfirm,
  onOpen: async () => {
    await tagEditor.flush();
    tagEditor.disable();
  },
  onClose: async (path) => {
    const active = gallery.getActivePath();
    if (path && path !== active) {
      // setActive announces the change, which reloads the sidebar editor.
      gallery.setActive(path);
      return;
    }
    if (active) await activateImage(active);
    else tagEditor.disable();
  },
  onSaved: (path) => { void refreshCaption(path); },
});

const filterPanel = createFilterPanel({
  list: $("frequency-list"),
  // The chips and the count live in the center panel's strip, not on this tab: they are
  // what the GRID is showing and why, so they belong next to the grid. One host, painted
  // by the panel's own paintActive()/paintCount() - the Filter tab keeps the controls
  // that choose conditions.
  strip: $("gallery-filter"),
  active: $("filter-active"),
  count: $("filter-count"),
  scope: $("frequency-scope"),
  empty: $("frequency-empty"),
  search: $("filter-search"),
  sort: $("filter-sort"),
  selection: $("filter-selection"),
  visibleScope: $("filter-scope-visible"),
  isSelected: (path) => gallery.isSelected(path),
  isMissing: (path) => missingPaths.has(path),
  onFilterChange: () => { applyFilter(); },
});

// ---------------------------------------------------------------------------
// the scope providers, shared by every panel that acts on the grid
// ---------------------------------------------------------------------------
// The tagger, the batch panel, the crop panel and the scale panel all ask "which images?"
// through the same control (scope.js), so they read their answers from this one object. A
// second copy of these closures is exactly how two answers drifted apart: the tagger offered
// four scopes with counts, while the batch panel inherited one of them and printed a bare
// number - and the scale panel did not ask at all, exporting the whole directory instead.
const scopeProviders = {
  getSelectedPaths: () => gallery.getSelectedPaths(),
  getVisiblePaths: () => gallery.getVisiblePaths(),
  getDirPaths: () => state.images.map((item) => item.path),
  getDirKey: () => state.dir,
  // The "directory + subdirectories" scope needs the same recursive listing the topbar
  // toggle uses; the images array it returns is every image under the directory, so no
  // new endpoint is involved.
  loadDirRecursive: async () => {
    if (!state.dir) return { dir: null, paths: [] };
    const payload = await api.listDir(state.dir, true);
    return {
      dir: payload.path || state.dir,
      paths: (payload.images || []).map((item) => item.path),
    };
  },
};

/**
 * The column's single scope control (scope.js, the #scope-strip above the tab panels).
 *
 * It is built ONCE, here, and the instance is handed to every panel that acts on it (the
 * tagger, the batch panel, the crop panel and the scale panel). Until 2026-09-20 each panel
 * built its own over its own <select>: "selected: 12 images" in the tagger and the button in
 * the batch panel then acted on different sets of images, which is the one thing both panels
 * are asking about. Scope.js stays the single implementation - the panels subscribe() to this
 * instance instead of constructing one.
 *
 * The default is the control's own (the picked images), not the batch panel's old one. With
 * a shared value there can only be one default, and this is the safe end of the range: an
 * untouched scope selects nothing, so every panel starts with its action button disabled
 * instead of the tagger's Run silently covering the whole directory - or, as the export did
 * until 2026-09-20, writing a copy of every image below the open one. The value is on screen
 * on every tab, so the answer is readable before anything is clicked.
 */
const scope = createScopeControl({
  select: $("scope-select"),
  providers: scopeProviders,
  // The directory the recursive scope counts, and how to list it - both live in the same
  // providers object, which is why the panels used to spread it into their own controls.
  getDirKey: scopeProviders.getDirKey,
  loadRecursive: scopeProviders.loadDirRecursive,
});

// The batch panel is built here (not in index.html) like the export panel, so the feature
// stays inside the files it owns; setupTabs() picks the appended tab up.
const batchPanel = createBatchPanel({
  tabsHost: document.querySelector(".tabs"),
  // .tab-panels, not .panel-right: the caption dock is pinned below the panels
  // and must stay the last thing in the column whichever panel is showing.
  panelHost: document.querySelector(".tab-panels"),
  notify: (message, kind) => notify(message, kind),
  confirm: askConfirm,
  scope,
  jobs: jobShelf,
  // A scope can cover images whose captions were never probed (the visible set is probed
  // only when a filter is on), and the common-tag editor needs their tag lists.
  onScopeChange: () => { void ensureProbed(); },
  getTags: (path) => {
    const info = captionInfo.get(path);
    return info && Array.isArray(info.tags) ? info.tags : null;
  },
  onAfterWrite: () => { void reloadCaptions(); void loadFrequency(); },
});

const autotagPanel = createAutotagPanel({
  modelSelect: $("autotag-model"),
  modelNote: $("autotag-model-note"),
  thresholds: $("autotag-thresholds"),
  options: $("autotag-options"),
  undesired: $("autotag-undesired"),
  alwaysFirst: $("autotag-always-first"),
  writeModeSelect: $("autotag-write-mode"),
  previewButton: $("btn-autotag-preview"),
  runButton: $("btn-autotag-run"),
  cancelButton: $("btn-autotag-cancel"),
  progress: $("autotag-progress"),
  progressFill: $("autotag-progress-fill"),
  progressText: $("autotag-progress-text"),
  errors: $("autotag-errors"),
  diffTitle: $("autotag-diff-title"),
  diffSummary: $("autotag-diff-summary"),
  diff: $("autotag-diff"),
  scope,
  jobs: jobShelf,
  notify: (message, kind) => notify(message, kind),
  onAfterWrite: () => { void reloadCaptions(); },
  confirm: askConfirm,
});

// Section 4.11: the whitelist roots and the model search roots are rendered by
// settings.js; this file only routes the clicks. openDir is a hoisted function
// declaration, so referring to it from the callbacks above the definition is fine.
const rootsPanel = createRootsPanel({
  confirm: askConfirm,
  notify: (message, kind) => notify(message, kind),
  onOpen: (root) => { void openDir(root.path, root.name || root.path); },
  isActive: (path) => state.root === path,
  onPickDataset: () => { void requestPick(); },
});

// ---------------------------------------------------------------------------
// captions
// ---------------------------------------------------------------------------
/**
 * Section 4.13: the center workspace shows sub directories as 3x3 preview
 * cards only at a folder-only level - when the directory has no images of its
 * own (typically the dataset root). With images on screen the grid is exactly
 * what it was before, so nothing about normal browsing changes.
 */
function galleryFolders() {
  return state.images.length === 0 ? state.dirs : [];
}

function paintEmptyState() {
  const el = $("gallery-empty");
  const clear = $("btn-gallery-clear-filter");
  if (state.images.length === 0) {
    // At a folder-only level the cards are the content, so the "no images"
    // note would be a lie. Keep its text current either way.
    el.textContent = t("gallery.empty");
    el.hidden = gallery.getFolderCount() > 0;
    clear.hidden = true;
    return;
  }
  if (gallery.getVisiblePaths().length === 0) {
    el.hidden = false;
    el.textContent = t("gallery.emptyFiltered");
    // Guidance, not instruction: everything that leads out of a filter lives on the
    // Filter tab, which is not on screen here, so the empty state carries the action.
    clear.hidden = false;
    return;
  }
  el.hidden = true;
  clear.hidden = true;
}

function applyFilter() {
  const paths = state.images.map((item) => item.path);
  missingPaths = new Set(state.images.filter((item) => item.hasCaption === false).map((item) => item.path));
  const visible = filterPanel.apply(paths, (path) => {
    if (!captionInfo.has(path)) return null;
    const info = captionInfo.get(path);
    if (!info || info.failed) return null;
    return Array.isArray(info.tags) ? info.tags : [];
  });
  gallery.setVisiblePaths(visible);
  paintEmptyState();
  // The batch panel acts on exactly what is visible, so it follows the filter.
  batchPanel.refreshScope();
  // A visible-scoped frequency table needs every caption too, not just a
  // tri-state filter - both are reasons to pay for the probe.
  if (filterPanel.hasFilter() || filterPanel.isVisibleScope()) void ensureProbed();
}

/**
 * What the caption dock's status line currently says, held as data rather than
 * as text: the line is painted at render time by activateImage(), so a language
 * switch has to be able to repaint it from state in hand - otherwise it (and the
 * empty state it doubles as) stays in the previous language. This is the
 * registration relabelAfterLocaleChange() exists for.
 */
let activeMeta = { kind: "empty" };

/**
 * Whether the caption dock's control block is folded away.
 *
 * The dock is permanent, and on a short window it would keep half the column
 * from whichever panel is showing (measured in a real browser: a 720px viewport
 * leaves the panels 242px of 655; at 600px, 185px). Every panel scrolls, so
 * nothing becomes unreachable - but the space is the user's to give, and the
 * choice is remembered like the panel widths are.
 */
const CAPTION_COLLAPSED_KEY = "kdt.captionCollapsed";
let captionCollapsed = false;

function readCaptionCollapsed() {
  try {
    return window.localStorage.getItem(CAPTION_COLLAPSED_KEY) === "1";
  } catch (err) {
    return false; // private mode / disabled storage: the dock just starts expanded
  }
}

/** The toggle's label is painted from the state, so relabel() has to repaint it. */
function paintCaptionToggle() {
  const button = $("btn-caption-toggle");
  if (!button) return;
  button.textContent = captionCollapsed ? t("caption.expand") : t("caption.collapse");
  button.setAttribute("aria-expanded", captionCollapsed ? "false" : "true");
}

function setCaptionCollapsed(value) {
  captionCollapsed = value === true;
  const dock = $("caption-dock");
  if (dock) dock.classList.toggle("is-collapsed", captionCollapsed);
  try {
    window.localStorage.setItem(CAPTION_COLLAPSED_KEY, captionCollapsed ? "1" : "0");
  } catch (err) {
    /* the choice just does not persist */
  }
  paintCaptionToggle();
}

function paintActiveMeta() {
  const el = $("active-meta");
  if (!el) return;
  if (activeMeta.kind === "loading") { el.textContent = t("app.loading"); return; }
  if (activeMeta.kind === "failed") {
    el.textContent = t("common.loadFailed", { message: activeMeta.message });
    return;
  }
  if (activeMeta.kind === "image") {
    const base = t("caption.metaLine", {
      width: activeMeta.width,
      height: activeMeta.height,
      size: activeMeta.size,
      mode: activeMeta.mode,
    });
    el.textContent = activeMeta.alpha ? t("caption.metaAlpha", { base }) : base;
    return;
  }
  // Nothing active: this line IS the dock's empty state - #caption-form is
  // hidden, so it is the only thing the dock shows.
  el.textContent = t("caption.noActiveImage");
}

async function activateImage(path) {
  const token = ++state.activeToken;
  const flushed = await tagEditor.flush();
  if (token !== state.activeToken) return;
  if (flushed === "failed") {
    // The edit on screen was never written. Loading another image into the
    // editor would throw the user's typing away with nothing but a toast to
    // remember it by, so ask - staying keeps the text and the error state, and
    // the gallery marker goes back to the image still being edited.
    const leave = await askConfirm({
      body: t("caption.saveBlocked"),
      confirmLabel: t("caption.saveBlockedLeave"),
      danger: true,
    });
    if (!leave) {
      const current = tagEditor.getPath();
      if (current) gallery.setActive(current, false);
      return;
    }
  }
  const preview = $("preview-img");
  if (!path) {
    preview.removeAttribute("src");
    activeMeta = { kind: "empty" };
    paintActiveMeta();
    tagEditor.disable();
    autotagPanel.refreshScopes();
    return;
  }
  preview.src = thumbUrl(path, THUMB_SIZE.PREVIEW);
  activeMeta = { kind: "loading" };
  paintActiveMeta();
  try {
    const [meta, caption] = await Promise.all([api.imageMeta(path), tagEditor.load(path)]);
    if (token !== state.activeToken) return;
    activeMeta = {
      kind: "image",
      width: meta.width,
      height: meta.height,
      size: formatBytes(meta.bytes),
      mode: meta.mode,
      alpha: meta.has_alpha === true,
    };
    paintActiveMeta();
    captionInfo.set(path, caption);
    const item = state.images.find((entry) => entry.path === path);
    if (item) item.hasCaption = caption.exists;
    gallery.setCaptionState(path, { exists: caption.exists });
    autotagPanel.refreshScopes();
  } catch (err) {
    if (token !== state.activeToken) return;
    // The failure stays on the dock's status line, which sits outside the form
    // and is therefore still visible after disable() hides the controls.
    activeMeta = { kind: "failed", message: describeError(err) };
    paintActiveMeta();
    tagEditor.disable();
  }
}

function formatBytes(bytes) {
  const value = Number(bytes) || 0;
  if (value < 1024) return value + " B";
  if (value < 1024 * 1024) return (value / 1024).toFixed(1) + " KB";
  return (value / (1024 * 1024)).toFixed(2) + " MB";
}

let probeFailures = 0;
//: which directory generation already had its captions probed one by one.
//: Section 4.2 froze images[].has_caption, so the red dot no longer needs a
//: request per image; the per-image caption is only fetched when the tag filter
//: actually needs the tag lists (or when the backend omits has_caption).
let probedToken = -1;

function paintBadgesFromListing() {
  for (const item of state.images) {
    if (typeof item.hasCaption === "boolean") {
      gallery.setCaptionState(item.path, { exists: item.hasCaption });
    }
  }
}

async function ensureProbed() {
  const token = state.openToken;
  if (probedToken === token) return null;
  probedToken = token;
  return probeCaptions(state.images, token);
}

async function probeCaptions(images, token) {
  probeFailures = 0;
  if (images.length === 0) return 0;
  let notified = false;
  const markFailed = (item, message) => {
    probeFailures += 1;
    captionInfo.set(item.path, { exists: null, tags: [], failed: true });
    if (!notified) {
      notified = true;
      notify(t("common.loadFailed", { message }), "error");
    }
  };
  // captionProbeChunks owns the chunk size; applyCaptionResults owns the folding rule.
  for (const chunk of captionProbeChunks(images)) {
    if (token !== state.openToken) return null;
    let payload;
    try {
      payload = await api.readCaptions(chunk.map((item) => item.path));
    } catch (err) {
      if (token !== state.openToken) return null;
      const message = describeError(err);
      for (const item of chunk) markFailed(item, message);
      // the failed chunk is pending too: repaint so the count and the scope match what is on screen
      applyFilter();
      continue;
    }
    if (token !== state.openToken) return null;
    const folded = applyCaptionResults(chunk, payload);
    for (const { item, entry } of folded.ok) {
      captionInfo.set(item.path, entry);
      item.hasCaption = entry.exists;
      gallery.setCaptionState(item.path, { exists: entry.exists });
    }
    for (const { item, message } of folded.failed) markFailed(item, message);
    // One filter pass per chunk, not per image: re-filtering after every response was
    // O(N^2) over the directory and repainted the gallery (and the batch panel's scope)
    // each time.
    applyFilter();
  }
  return probeFailures;
}

function applyListingToExisting(payload) {
  const fresh = new Map((payload.images || []).map((item) => [item.path, item]));
  for (const item of state.images) {
    const match = fresh.get(item.path);
    if (match && typeof match.has_caption === "boolean") {
      item.hasCaption = match.has_caption;
      gallery.setCaptionState(item.path, { exists: match.has_caption });
    }
  }
  renderCounts(payload.counts, $("recursive-toggle").checked);
}

/** Called after the tagger wrote captions: refresh badges and counts. */
async function reloadCaptions() {
  if (!state.dir) return;
  probedToken = -1;
  captionInfo.clear();
  // an op rewrote captions, so the frequency table is stale too
  void loadFrequency();
  if (filterPanel.hasFilter()) {
    // the filter needs real tag lists again
    applyFilter();
    return;
  }
  try {
    const payload = await api.listDir(state.dir, $("recursive-toggle").checked);
    if (payload.path !== state.dir) return;
    applyListingToExisting(payload);
  } catch (err) {
    notify(t("common.loadFailed", { message: describeError(err) }), "warn");
  }
  applyFilter();
}

/** Re-read one caption after the enlarged view wrote it (badge + filter cache). */
async function refreshCaption(path) {
  try {
    const payload = await api.getCaption(path);
    captionInfo.set(path, payload);
    const item = state.images.find((entry) => entry.path === path);
    if (item) item.hasCaption = payload.exists;
    gallery.setCaptionState(path, { exists: payload.exists });
    applyFilter();
  } catch (err) {
    // the editor already surfaced the failure; refreshing a badge is best effort
  }
}

// ---------------------------------------------------------------------------
// directory browsing
// ---------------------------------------------------------------------------
function sep() {
  return state.root && state.root.indexOf("\\") >= 0 ? "\\" : "/";
}

/**
 * Paint the sub directories of the level on screen.
 *
 * `revealPath` is the folder this navigation came out of, when it came from
 * below (see navscroll.js): that row is marked, and openDir() scrolls to it, so
 * "up one level" lands on the folder the user just left instead of on the top of
 * a list that can hold 200+ entries. Returns the marked row, or null.
 */
function renderDirs(dirs, revealPath) {
  const list = $("dir-list");
  list.replaceChildren();
  if (!dirs || dirs.length === 0) {
    const li = document.createElement("li");
    li.className = "hint";
    li.textContent = t("browse.noDirs");
    list.append(li);
    return null;
  }
  const reveal = revealPath === null || revealPath === undefined ? null : revealPath;
  let revealed = null;
  for (const dir of dirs) {
    const li = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.title = dir.path;
    if (state.dir === dir.path) button.classList.add("is-active");
    if (reveal !== null && samePath(dir.path, reveal)) {
      button.classList.add("is-revealed");
      revealed = button;
    }
    const name = document.createElement("span");
    name.className = "li-name";
    name.textContent = dir.name;
    const count = document.createElement("span");
    count.className = "li-count";
    count.textContent = t("browse.dirImageCount", { n: dir.image_count });
    button.append(name, count);
    button.addEventListener("click", () => { void openDir(dir.path, state.rootName); });
    li.append(button);
    list.append(li);
  }
  return revealed;
}

/** Repaint the list from state without losing where it is scrolled to. */
function repaintDirs() {
  const list = $("dir-list");
  const top = list.scrollTop;
  renderDirs(state.dirs);
  list.scrollTop = top;
}

/** Remember where both listings of `dir` stand, before the next render wipes them. */
function rememberScroll(dir) {
  if (!dir) return;
  dirScroll.remember(dir, $("dir-list").scrollTop);
  gridScroll.remember(dir, $("gallery").scrollTop);
}

function setScrollTop(element, top) {
  if (top === null || top === undefined) return;
  element.scrollTop = Math.max(0, Number(top) || 0);
}

/**
 * Put both listings back after a navigation (see navscroll.js). The folder we
 * came out of wins over the position a directory was last left at: that is the
 * one the user is looking for when they press "up". The remembered position is
 * the fallback, so a refresh or a jump back to a directory does not move it
 * either.
 */
function restoreScroll(dir, reveal, revealedRow) {
  const list = $("dir-list");
  if (revealedRow) revealRow(list, revealedRow);
  else setScrollTop(list, dirScroll.recall(dir));
  if (reveal !== null && gallery.revealDir(reveal)) return;
  setScrollTop($("gallery"), gridScroll.recall(dir));
}

/**
 * Breadcrumb, in the spirit of the one between VS Code's tabs and its editor:
 * every segment drops down the entries that sit NEXT TO it, so hopping to a
 * sibling folder no longer means going up and back down.
 *
 * VS Code's version does much more (symbol levels, several workspace folders,
 * drag and drop). Only the part that pays for itself here is implemented:
 * the dataset segment lists the whitelist roots, every other segment lists the
 * siblings of that directory, and the current entry is marked.
 */
let crumbMenu = null;
let crumbSeq = 0;

function baseName(path) {
  const parts = String(path || "").split(/[\\/]+/).filter((part) => part.length > 0);
  return parts.length > 0 ? parts[parts.length - 1] : String(path || "");
}

function closeCrumbMenu() {
  if (!crumbMenu) return;
  if (crumbMenu.menu && crumbMenu.menu.parentNode) crumbMenu.menu.remove();
  if (crumbMenu.slot) crumbMenu.slot.classList.remove("is-open");
  crumbMenu = null;
}

/** The siblings of one segment: the roots for the dataset segment, else api.listDir. */
async function crumbEntries(segment) {
  if (segment.kind === "root") {
    const roots = state.roots.length > 0
      ? state.roots
      : [{ path: segment.path, name: baseName(segment.path) }];
    return roots.map((root) => ({
      label: root.name || baseName(root.path),
      path: root.path,
      current: root.path === segment.path,
    }));
  }
  const payload = await api.listDir(segment.parent, false);
  return (payload.dirs || []).map((dir) => ({
    label: dir.name,
    path: dir.path,
    current: dir.path === segment.path,
  }));
}

function paintCrumbMenu(slot, segment, entries) {
  const menu = document.createElement("div");
  menu.className = "crumb-menu";
  let currentItem = null;
  for (const entry of entries) {
    const item = document.createElement("button");
    item.type = "button";
    item.className = "crumb-item" + (entry.current ? " is-current" : "");
    item.title = entry.path;
    const name = document.createElement("span");
    name.className = "crumb-name";
    name.textContent = entry.label;
    item.append(name);
    if (entry.current) {
      const mark = document.createElement("span");
      mark.className = "crumb-mark";
      mark.textContent = t("breadcrumb.current");
      item.append(mark);
      currentItem = item;
    }
    item.addEventListener("click", () => {
      closeCrumbMenu();
      const name = segment.kind === "root" ? entry.label : state.rootName;
      void openDir(entry.path, name);
    });
    menu.append(item);
  }
  if (segment.kind === "root") {
    const pick = document.createElement("button");
    pick.type = "button";
    pick.className = "crumb-item crumb-choose";
    pick.textContent = t("breadcrumb.choose");
    pick.addEventListener("click", () => { closeCrumbMenu(); void requestPick(); });
    menu.append(pick);
  }
  slot.append(menu);
  slot.classList.add("is-open");
  crumbMenu = { slot, menu };
  // 200+ siblings do not fit in the dropdown, and the one the user is looking
  // for is the current one: open the menu on it instead of at the top.
  if (currentItem) revealRow(menu, currentItem);
}

async function toggleCrumbMenu(slot, segment) {
  const wasOpen = crumbMenu !== null && crumbMenu.slot === slot;
  closeCrumbMenu();
  if (wasOpen) return;
  const mine = ++crumbSeq;
  let entries;
  try {
    entries = await crumbEntries(segment);
  } catch (err) {
    notify(t("breadcrumb.loadFailed", { message: describeError(err) }), "warn");
    return;
  }
  if (mine !== crumbSeq) return;
  paintCrumbMenu(slot, segment, entries);
}

document.addEventListener("click", (event) => {
  if (!crumbMenu) return;
  if (crumbMenu.slot.contains(event.target)) return;
  closeCrumbMenu();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && crumbMenu) closeCrumbMenu();
});

function appendCrumbSegment(crumb, segment, withSeparator) {
  if (withSeparator) {
    const slash = document.createElement("span");
    slash.className = "sep";
    slash.textContent = "/";
    crumb.append(slash);
  }
  const slot = document.createElement("span");
  slot.className = "crumb-slot";
  const button = document.createElement("button");
  button.type = "button";
  button.className = "crumb-seg" + (segment.current ? " current" : "");
  button.textContent = segment.label;
  button.title = t("breadcrumb.jump", { path: segment.path });
  button.addEventListener("click", (event) => {
    event.stopPropagation();
    void toggleCrumbMenu(slot, segment);
  });
  slot.append(button);
  crumb.append(slot);
}

function renderBreadcrumb() {
  closeCrumbMenu();
  const crumb = $("breadcrumb");
  crumb.replaceChildren();
  if (state.root) {
    // The label is derived from the path, never from state.rootName: after a
    // deep link or a refresh that field held the whole path, which is how the
    // breadcrumb ended up showing a full path next to a folder name.
    appendCrumbSegment(crumb, {
      kind: "root",
      label: baseName(state.root),
      path: state.root,
      parent: null,
      current: state.dir === state.root,
    }, false);
  }
  if (state.dir && state.root && state.dir !== state.root) {
    const rel = String(state.dir).slice(state.root.length).split(/[\\/]+/).filter((part) => part.length > 0);
    let acc = state.root;
    rel.forEach((part, index) => {
      const parent = acc;
      acc = acc + sep() + part;
      appendCrumbSegment(crumb, {
        kind: "dir",
        label: part,
        path: acc,
        parent,
        current: index === rel.length - 1,
      }, true);
    });
  }
  $("btn-parent").disabled = !state.parent;
}

function renderCounts(counts, recursive) {
  // One call, two surfaces: the census line in the breadcrumb row and the readiness strip's caption
  // segment are both painted from this counts object, so they cannot disagree - whichever row each
  // of them lives in. The missing-caption count is the strip's alone: it used to be printed here
  // too, one row above the strip that says it as an action, which was the same fact twice.
  readiness.setCounts(counts || null);
  if (!counts) { $("dir-counts").textContent = ""; return; }
  const key = recursive ? "browse.countsRecursive" : "browse.counts";
  $("dir-counts").textContent = t(key, {
    images: counts.images || 0,
    captions: counts.captions || 0,
  });
}

async function openDir(path, rootName) {
  if (!path) return;
  // A new directory invalidates the enlarged view's path list.
  await zoom.dismiss();
  await tagEditor.flush();
  const token = ++state.openToken;
  const recursive = $("recursive-toggle").checked;
  // Where this navigation starts: the listing it is about to replace is the one
  // whose scroll position has to be kept (navscroll.js).
  const from = state.dir;
  rememberScroll(from);
  let payload;
  setScanning(true);
  try {
    payload = await api.listDir(path, recursive);
  } catch (err) {
    notify(t("browse.loadFailed", { message: describeError(err) }), "error", {
      retry: () => { void openDir(path, rootName); },
    });
    return;
  } finally {
    setScanning(false);
  }
  if (token !== state.openToken) return;
  state.dir = payload.path;
  state.parent = payload.parent;
  const insideRoot = state.root !== null && payload.path.indexOf(state.root) === 0;
  if (!insideRoot) {
    // navigated outside the previous root (root switch or hash deep link)
    state.root = payload.path;
    state.rootName = rootName || payload.path;
  } else if (rootName) {
    state.rootName = rootName;
  }
  state.images = (payload.images || []).map((item) => ({
    path: item.path,
    name: item.name,
    size: item.size,
    mtime: item.mtime,
    hasCaption: typeof item.has_caption === "boolean" ? item.has_caption : null,
  }));
  state.dirs = (payload.dirs || []).map((item) => ({
    name: item.name,
    path: item.path,
    image_count: Number(item.image_count) || 0,
    preview_images: Array.isArray(item.preview_images) ? item.preview_images : [],
  }));
  state.counts = payload.counts || null;
  captionInfo.clear();
  gallery.setImages(state.images, galleryFolders());
  // the roots list itself is owned by settings.js; only the active highlight moves
  rootsPanel.repaint();
  renderBreadcrumb();
  // A navigation that came out of a folder left of this one (up one level, or a
  // breadcrumb jump to an ancestor) brings that folder back into view.
  const reveal = childOnPath(state.dir, from);
  const revealedRow = renderDirs(state.dirs, reveal);
  renderCounts(payload.counts, recursive);
  // A fresh listing just replaced the previous one, so the scope control's cached "directory +
  // subdirectories" listing describes a tree that may have changed since (a crop writes archives
  // beside their source, a hand edit or the trainer adds and removes images). The control cannot
  // see the filesystem; "a fresh listing arrived" is the signal it gets, and this is the only
  // place one is installed - navigation, the refresh action, the external-change poll and the crop
  // panel's own post-run refresh all land here.
  scope.invalidate();
  applyFilter();
  restoreScroll(state.dir, reveal, revealedRow);
  window.location.hash = "#" + encodeURIComponent(state.dir);
  void loadFrequency();
  void loadCacheStatus();
  // the exported toml always belongs to the directory on screen
  exportPanel.setRoot(state.dir);
  // ...but the file the trainer reads belongs to the dataset root, so the strip's third segment asks
  // about that one (section 4.16). A fresh stat on every navigation: a toml written by hand, by the
  // trainer or by an export must never be answered from the previous directory's answer.
  void refreshTomlStatus();
  paintBadgesFromListing();
  if (state.images.some((item) => item.hasCaption === null)) {
    // fallback for a backend without images[].has_caption (section 4.2)
    void ensureProbed();
  }
}

/** Section 4.11: the panel owns the list; this keeps state.roots in sync. */
async function loadRoots() {
  state.roots = await rootsPanel.reload();
  return state.roots;
}

/**
 * Section 4.12: choose the dataset directory graphically. When the server
 * reports the picker is unavailable (non-loopback bind), say why instead of
 * opening a dialog whose only possible answer is a 403.
 */
async function requestPick() {
  if (!pathConfig.allowed) {
    notify(t("picker.disabled", { host: pathConfig.host || "?" }), "warn");
    return null;
  }
  const start = state.dir || (state.roots[0] && state.roots[0].path) || "";
  const picked = await picker.open(start);
  if (picked) await switchDataset(picked);
  return picked;
}

/**
 * Open `path` as the dataset: make sure it is inside the whitelist first
 * (POST /api/roots is idempotent - changed:false when it already is) and then
 * load it. This is what turns clicking the directory you are already looking
 * at into "switch to another dataset" in one step.
 */
async function switchDataset(path) {
  if (!path) return;
  try {
    await api.addRoot(path);
  } catch (err) {
    notify(t("picker.selectFailed", { message: describeError(err) }), "error");
    return;
  }
  await loadRoots();
  const parts = String(path).split(/[\\/]+/).filter((part) => part.length > 0);
  await openDir(path, parts.length > 0 ? parts[parts.length - 1] : path);
}

/** Push the server-reported capability onto every picker entry point. */
function applyPickerAvailability() {
  const enabled = pathConfig.allowed;
  const title = enabled ? t("picker.open") : t("picker.disabled", { host: pathConfig.host || "?" });
  rootsPanel.setPickerEnabled(enabled, title);
  exportPanel.setPickerEnabled(enabled, title);
  scalePanel.setPickerEnabled(enabled, title);
}

async function checkHealth() {
  const pill = $("server-status");
  try {
    const payload = await api.health();
    pill.className = "pill pill-ok";
    pill.textContent = t("app.connected", { version: (payload && payload.version) || "?" });
    // Section 4.12: the server owns this decision; the UI only mirrors it.
    const capability = payload && payload.path_config;
    if (capability) {
      pathConfig.allowed = capability.allowed === true;
      pathConfig.host = String(capability.host || "");
      pathConfig.reason = capability.reason || null;
    }
  } catch (err) {
    pill.className = "pill pill-bad";
    pill.textContent = t("app.disconnected");
  }
}

/**
 * A listing is a real request over a possibly large tree, and the panels it is
 * about to replace must not look final while it runs: the directory list, the
 * gallery and the breadcrumb dim, and assistive tech is told why.
 */
function setScanning(value) {
  const scanning = value === true;
  for (const el of [$("dir-list"), $("gallery"), $("breadcrumb")]) {
    if (!el) continue;
    el.classList.toggle("is-busy", scanning);
    if (scanning) el.setAttribute("aria-busy", "true");
    else el.removeAttribute("aria-busy");
  }
}

// ---------------------------------------------------------------------------
// filter / frequency
// ---------------------------------------------------------------------------
async function loadFrequency() {
  if (!state.dir) return;
  const recursive = $("recursive-toggle").checked;
  try {
    const payload = await api.tagFrequency(state.dir, recursive);
    filterPanel.setFrequency(payload, recursive ? t("filter.scopeRecursive") : t("filter.scopeDir"));
    applyFilter();
  } catch (err) {
    // The previous directory's table must not stay on screen as if it were this
    // one's, so the panel is told the load failed, not just left alone.
    filterPanel.setFrequencyFailed(t("common.loadFailed", { message: describeError(err) }));
    notify(t("filter.applyFailed", { message: describeError(err) }), "warn", {
      retry: () => { void loadFrequency(); },
    });
  }
}

// ---------------------------------------------------------------------------
// cache status
// ---------------------------------------------------------------------------
let staleCaches = [];
//: Rebuilding is a single POST that can cover every stale cache in the
//: directory; while it is in flight the panel says so and refuses to start a
//: second one.
let cacheBusy = false;
let cacheBusyCount = 0;
//: A failed status load clears the list; without this flag the panel would then
//: claim "no stale cache", which is a different statement from "could not ask".
let cacheFailed = false;
//: The shelf row for the running rebuild, so the job stays visible from every tab.
let cacheShelfJob = null;
//: The rebuild job's own state: the status line, the cancel button and relabel()
//: all paint from these.
let cacheJobId = null;
let cacheStream = null;
let cacheJobDone = 0;
let cacheJobTotal = 0;
let cacheCancelling = false;
//: How long an accepted cancel may go without a terminal event before the panel hands
//: the button back. The tagger and the scale panel arm the same watchdog; this panel
//: used to lock itself for good when the cancel request itself failed.
const CACHE_CANCEL_WATCHDOG_MS = 30000;
let cacheCancelWatchdog = null;

/**
 * The cache panel's status line and cancel button. Split out of renderCacheList()
 * because the stream updates it per image, and rebuilding the whole list for every
 * progress event would be wasteful on a large directory.
 */
function paintShelfCacheJob() {
  if (cacheShelfJob) {
    cacheShelfJob.update({ done: cacheJobDone, total: cacheJobTotal, current: "" });
  } else if (cacheBusy) {
    cacheShelfJob = jobShelf.start({
      kind: "cache",
      label: t("tab.cache"),
      cancelLabel: t("cache.cancel"),
      total: cacheJobTotal,
      cancel: () => { void cancelCacheRebuild(); },
    });
  }
}

/**
 * The cache panel's state in one object.
 *
 * The panel's status line and the readiness strip's cache segment both paint from this, through
 * cacheStatusText(): one view, one wording, one number. A second conversion site is exactly how the
 * tab would end up saying "3 possibly stale entries" while the strip says 2.
 */
function cacheView() {
  return {
    busy: cacheBusy,
    done: cacheJobDone,
    total: cacheJobTotal,
    failed: cacheFailed,
    stale: staleCaches.length,
  };
}

function paintCacheStatus() {
  paintShelfCacheJob();
  const cancel = $("btn-cache-cancel");
  const status = $("cache-status-line");
  const view = cacheView();
  // The strip is on screen on every tab, so it is painted here rather than from renderCacheList():
  // this is the one function every cache state change already goes through.
  readiness.setCache(view);
  if (cacheBusy) {
    status.textContent = cacheStatusText(view);
    cancel.hidden = false;
    cancel.disabled = cacheCancelling;
    cancel.textContent = cacheCancelling ? t("cache.cancelling") : t("cache.cancel");
    return;
  }
  cancel.hidden = true;
  cancel.disabled = false;
  status.textContent = cacheStatusText(view);
}

function renderCacheList() {
  const list = $("cache-list");
  list.replaceChildren();
  paintCacheStatus();
  $("btn-cache-rebuild-all").disabled = cacheBusy || cacheFailed || staleCaches.length === 0;
  $("cache-empty").hidden = cacheBusy || cacheFailed || staleCaches.length !== 0;
  for (const entry of staleCaches) {
    const row = document.createElement("div");
    row.className = "cache-item";
    const path = document.createElement("span");
    path.className = "c-path";
    path.textContent = entry.image;
    path.title = entry.cache;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "btn btn-mini";
    button.textContent = t("cache.rebuildOne");
    button.disabled = cacheBusy;
    button.addEventListener("click", async () => {
      const ok = await askConfirm({
        body: t("cache.confirmOne", { cache: entry.cache }),
        confirmLabel: t("cache.rebuildOne"),
        danger: true,
      });
      if (!ok) return;
      void rebuildCaches([entry.image]);
    });
    row.append(path, button);
    list.append(row);
  }
}

async function loadCacheStatus() {
  if (!state.dir) {
    staleCaches = [];
    renderCacheList();
    $("cache-status-line").textContent = t("cache.notApplicable");
    return;
  }
  const recursive = $("recursive-toggle").checked;
  try {
    const payload = await api.cacheStatus(state.dir, recursive);
    staleCaches = (payload && payload.stale) || [];
    cacheFailed = false;
    renderCacheList();
  } catch (err) {
    // The previous directory's caches must not stay on screen as if they were
    // this one's - a failure used to look exactly like a successful load.
    staleCaches = [];
    cacheFailed = true;
    renderCacheList();
    notify(t("common.loadFailed", { message: describeError(err) }), "error", {
      retry: () => { void loadCacheStatus(); },
    });
  }
}

function clearCacheCancelWatchdog() {
  if (cacheCancelWatchdog === null) return;
  window.clearTimeout(cacheCancelWatchdog);
  cacheCancelWatchdog = null;
}

function armCacheCancelWatchdog() {
  clearCacheCancelWatchdog();
  cacheCancelWatchdog = window.setTimeout(() => {
    cacheCancelWatchdog = null;
    if (!cacheBusy) return;
    closeCacheStream();
    cacheBusy = false;
    cacheCancelling = false;
    notify(t("cache.cancelTimeout"), "error");
    // What the rebuild already deleted is unknown from here, so ask again instead of
    // repainting the pre-cancel list as if it were current.
    void loadCacheStatus();
  }, CACHE_CANCEL_WATCHDOG_MS);
}

function closeCacheStream() {
  clearCacheCancelWatchdog();
  if (cacheShelfJob) {
    cacheShelfJob.finish();
    cacheShelfJob = null;
  }
  if (cacheJobId) {
    forgetJob("cache", cacheJobId);
  }
  if (cacheStream) {
    cacheStream.close();
    cacheStream = null;
  }
  cacheJobId = null;
}

async function finishCacheJob(payload) {
  closeCacheStream();
  cacheBusy = false;
  cacheBusyCount = 0;
  cacheCancelling = false;
  const summary = (payload && payload.summary) || {};
  const removed = Number(summary.removed) || 0;
  const failed = Number(summary.failed) || 0;
  if (payload && payload.cancelled) {
    // "Stopped early", never "undone": the caches already deleted stay deleted.
    notify(t("cache.cancelled", { done: Number(summary.processed) || 0, total: cacheJobTotal }), "warn");
  } else if (failed > 0) {
    notify(t("cache.rebuiltWithFailed", { removed, failed }), "error");
  } else {
    notify(t("cache.rebuilt", { removed }), "ok");
  }
  await loadCacheStatus();
  renderCacheList();
}

function openCacheStream(id, total) {
  cacheJobId = String(id);
  rememberJob({ panel: "cache", jobId: cacheJobId, total: Number(total) || 0 });
  cacheJobTotal = Number(total) || cacheJobTotal;
  let streamErrors = 0;
  cacheStream = new EventSource(cacheStreamUrl(cacheJobId));
  cacheStream.addEventListener("progress", (event) => {
    streamErrors = 0;
    const payload = parseStreamData(event.data);
    if (!payload) return;
    cacheJobDone = Number(payload.done) || cacheJobDone;
    cacheJobTotal = Number(payload.total) || cacheJobTotal;
    paintCacheStatus();
  });
  cacheStream.addEventListener("done", (event) => {
    const payload = parseStreamData(event.data);
    // Section 4.3: the end of a job is the finished flag, never done >= total.
    if (!payload || payload.finished !== true) return;
    void finishCacheJob(payload);
  });
  cacheStream.onerror = () => {
    if (!cacheStream) return;
    streamErrors += 1;
    if (streamErrors >= 3) {
      closeCacheStream();
      cacheBusy = false;
      cacheCancelling = false;
      notify(t("cache.streamLost"), "error");
      // The rebuild deleted an unknown number of caches before the stream died, so the
      // list on screen now claims something nobody has checked. Drop it and re-read; if
      // that read fails too, cacheFailed stops the panel from claiming "no stale cache".
      staleCaches = [];
      cacheFailed = true;
      renderCacheList();
      void loadCacheStatus();
    }
  };
}

/**
 * Pick a rebuild back up after a page reload: the stream replays its complete event log, so the panel
 * only has to look busy again and open it.
 */
function resumeCacheJob(jobId, total) {
  cacheBusy = true;
  cacheJobDone = 0;
  cacheJobTotal = Number(total) || 0;
  cacheCancelling = false;
  cacheFailed = false;
  renderCacheList();
  openCacheStream(jobId, cacheJobTotal);
}

/**
 * Jobs that were running when the page was reloaded (jobstore.js). The backend kept going and every job
 * stream replays from the start, so the panels pick them back up instead of the app pretending nothing
 * is happening - and a job that ended while the page was loading replays its terminal event immediately.
 */
function resumeRunningJobs() {
  for (const entry of readRunningJobs()) {
    if (entry.panel === "batch") batchPanel.resume(entry.jobId, entry.total);
    else if (entry.panel === "autotag") autotagPanel.resume(entry.jobId, entry.total, entry.task);
    else if (entry.panel === "scale") scalePanel.resume(entry.jobId, entry.total);
    else if (entry.panel === "crop") cropPanel.resume(entry.jobId, entry.total);
    else if (entry.panel === "cache") resumeCacheJob(entry.jobId, entry.total);
  }
}

/**
 * Delete every stale cache the panel is currently listing.
 *
 * The cache tab's own "rebuild all" button and the readiness strip's cache segment both run this:
 * one confirmation, one job, one request. The readiness strip is on screen on every tab, so a click
 * there must not be a second, slightly different rebuild.
 */
async function rebuildAllStaleCaches() {
  if (cacheBusy || cacheFailed || staleCaches.length === 0) return;
  const ok = await askConfirm({
    body: t("cache.confirmAll", { n: staleCaches.length }),
    confirmLabel: t("cache.rebuildAll"),
    danger: true,
  });
  if (!ok) return;
  void rebuildCaches(staleCaches.map((entry) => entry.image));
}

/**
 * Stop a running cache rebuild. The cache tab's own button and the command palette both run this:
 * one request, one "cancelling" state, one watchdog. It used to be a closure inside setupToolbar,
 * which is the same action reachable only through the one button that happened to be next to it.
 */
async function cancelCacheRebuild() {
  if (!cacheJobId || cacheCancelling) return;
  cacheCancelling = true;
  paintCacheStatus();
  armCacheCancelWatchdog();
  try {
    await api.cacheInvalidateCancel(cacheJobId);
  } catch (err) {
    // The request failed, so the job is still running and the button has to come
    // back: without this the panel said "cancelling" with no way to try again until
    // the job ended on its own.
    clearCacheCancelWatchdog();
    cacheCancelling = false;
    paintCacheStatus();
    notify(t("cache.cancelFailed", { message: describeError(err) }), "error");
  }
}

/**
 * The readiness strip's cache segment: retry the read when it failed, rebuild what is stale
 * otherwise. Which of the two it is depends on the panel's state, so the decision stays here.
 */
function fixCaches() {
  if (cacheFailed) {
    void loadCacheStatus();
    return;
  }
  void rebuildAllStaleCaches();
}

async function rebuildCaches(images) {
  if (cacheBusy) return;
  cacheBusy = true;
  cacheBusyCount = images.length;
  cacheJobDone = 0;
  cacheJobTotal = images.length;
  cacheCancelling = false;
  renderCacheList();
  try {
    // Section 4.5 supplement: the rebuild is a job now, so the panel can show
    // real progress and stop it between images.
    const started = await api.cacheInvalidateRun(images);
    const id = started && (started.job_id || started.jobId);
    if (!id) throw new Error(t("common.noJobId"));
    openCacheStream(id, started.total);
  } catch (err) {
    cacheBusy = false;
    cacheBusyCount = 0;
    notify(t("cache.rebuildFailed", { message: describeError(err) }), "error");
    renderCacheList();
  }
}

// ---------------------------------------------------------------------------
// dataset.toml export (section 4.9)
//
// The panel gets its own tab instead of sharing the autotag one: exporting is a
// read-only planning step, tagging writes captions. It is built here rather
// than in index.html so the feature stays inside the files this task owns;
// setupTabs() picks the new tab up automatically.
// ---------------------------------------------------------------------------
//: subsets listed in the preview table before it says "and N more".
const EXPORT_PREVIEW_SUBSETS = 8;
//: warning lines rendered per category, and paths per skipped reason.
const EXPORT_PREVIEW_WARNINGS = 30;
//: the trainer reads this file next to the training config.
const EXPORT_FILE_NAME = "dataset.toml";

/** Comparison key for two paths: separators, trailing separator, case. */
function normalizePathKey(path) {
  return String(path === null || path === undefined ? "" : path)
    .replace(/[\\/]+/g, "/")
    .replace(/\/+$/, "")
    .toLowerCase();
}

/** Lookup key pairing a skipped entry with its structured counterpart. */
function skippedKey(imageDir, reason) {
  return normalizePathKey(imageDir) + "\u0000" + String(reason === null || reason === undefined ? "" : reason);
}

//: section 4.9: every warning and every skipped reason leads with "[code] ".
const WARNING_CODE_RE = /^\s*\[([A-Za-z0-9_]+)\]\s*/;
//: the frozen code set; "other" is the fallback bucket, not a code.
const WARNING_GROUP_IDS = new Set(WARNING_GROUPS.map((group) => group.id).filter((id) => id !== "other"));

/**
 * Bucket one warning / skipped reason.
 *
 * The leading [code] wins, because that is the frozen contract and it survives
 * a reworded message. Only when there is no code - or a code this build does
 * not know yet - does the keyword table get a say (a secondary net while the
 * backend rolls this out); a message that matches neither lands in "other", so
 * nothing is ever dropped.
 */
function warningCategory(message, code) {
  const label = String(code === null || code === undefined ? "" : code);
  if (label.length > 0 && WARNING_GROUP_IDS.has(label)) return label;
  const raw = String(message === null || message === undefined ? "" : message);
  const found = WARNING_CODE_RE.exec(raw);
  if (found && WARNING_GROUP_IDS.has(found[1])) return found[1];
  const text = raw.toLowerCase();
  for (const group of WARNING_GROUPS) {
    for (const keyword of group.match) {
      if (text.indexOf(String(keyword).toLowerCase()) >= 0) return group.id;
    }
  }
  return "other";
}

/**
 * The line that gets printed. The code is already the group heading, so it is
 * stripped from recognized messages; unknown or missing codes keep the full
 * text, which is what makes a brand new code readable instead of invisible.
 */
function warningText(message, item) {
  const localized = item ? localizeBackendMessage("msg.", item.code, item.params) : null;
  if (localized !== null) return localized;
  const raw = String(message === null || message === undefined ? "" : message);
  const code = WARNING_CODE_RE.exec(raw);
  if (!code || !WARNING_GROUP_IDS.has(code[1])) return raw;
  const rest = raw.slice(code[0].length).trim();
  return rest.length > 0 ? rest : raw;
}

/**
 * Merge the per-subset warnings and the top level aggregate into one list per
 * category.
 *
 * Section 4.9 freezes the top level list as the *union* of the per-subset ones,
 * so the per-subset entries are used first - they are the ones that name the
 * directory the problem lives in - and the aggregate only fills in what no
 * subset claimed. Rendering the aggregate first would print every line twice
 * and would leave the user without a location for any of them.
 */
function groupExportWarnings(warnings, subsets, warningItems) {
  const buckets = new Map();
  for (const group of WARNING_GROUPS) buckets.set(group.id, []);
  // Section 4.9 additive: index the structured warning_items by their text.
  // A per-subset line exists only as a string, but its text is byte-identical to
  // the top level item, so this pairing recovers code + params for it too.
  const byText = new Map();
  for (const item of Array.isArray(warningItems) ? warningItems : []) {
    if (item && typeof item === "object" && typeof item.text === "string") {
      byText.set(item.text, item);
    }
  }
  const normalize = (message) => (
    typeof message === "string" ? message : String((message && message.message) || "")
  );
  const push = (raw, dir) => {
    if (raw.length === 0) return;
    const item = byText.get(raw) || null;
    const code = item ? item.code : "";
    buckets.get(warningCategory(raw, code)).push({ text: warningText(raw, item), dir: dir || "" });
  };
  const claimed = new Set();
  for (const subset of Array.isArray(subsets) ? subsets : []) {
    if (!subset || typeof subset !== "object") continue;
    const dir = String(subset.image_dir || "");
    const list = Array.isArray(subset.warnings) ? subset.warnings : [];
    for (const message of list) {
      const raw = normalize(message);
      if (raw.length === 0) continue;
      push(raw, dir);
      claimed.add(raw);
    }
  }
  for (const message of Array.isArray(warnings) ? warnings : []) {
    const raw = normalize(message);
    if (claimed.has(raw)) continue;
    push(raw, "");
  }
  return WARNING_GROUPS
    .map((group) => ({ id: group.id, items: buckets.get(group.id) }))
    .filter((group) => group.items.length > 0);
}

/**
 * Bucket skipped entries by the [code] of their reason, so two wordings of the
 * same problem share one group (and the directory list is what the user acts
 * on). A reason without a recognized code keeps its raw text as the key.
 */
function groupExportSkipped(skipped, skippedItems) {
  const buckets = new Map();
  // Section 4.9 additive: index the structured skipped_items by (path, reason)
  // so each legacy entry finds its code + params. Raw text is already the same.
  const byKey = new Map();
  for (const item of Array.isArray(skippedItems) ? skippedItems : []) {
    if (!item || typeof item !== "object") continue;
    byKey.set(skippedKey(item.image_dir, item.reason), item);
  }
  for (const entry of Array.isArray(skipped) ? skipped : []) {
    if (!entry || typeof entry !== "object") continue;
    const dir = String(entry.image_dir || "");
    const reason = String(entry.reason || "").trim();
    const item = byKey.get(skippedKey(entry.image_dir, entry.reason)) || null;
    const code = item ? item.code : "";
    const id = code.length > 0 && WARNING_GROUP_IDS.has(code)
      ? code
      : (reason.length > 0 ? warningCategory(reason) : "other");
    if (!buckets.has(id)) buckets.set(id, { id, reason, dirs: [], details: new Set() });
    const bucket = buckets.get(id);
    bucket.dirs.push(dir);
    const localized = item ? localizeBackendMessage("msg.skip.", item.code, item.params) : null;
    const detail = localized !== null ? localized : warningText(reason);
    if (id !== "other" && detail.length > 0 && detail !== reason) bucket.details.add(detail);
  }
  return Array.from(buckets.values()).map((bucket) => ({
    id: bucket.id,
    reason: bucket.reason,
    details: Array.from(bucket.details),
    dirs: bucket.dirs,
  }));
}

/**
 * The section 1.1 check, run on the plan the backend actually returned: one
 * subset per directory, and never the exported root itself. The trainer globs a
 * single level, so a subset pointing at the parent would train on zero images.
 * Returns [] when the plan looks right.
 */
function subsetProblems(subsets, root) {
  const counts = new Map();
  let rootUsed = false;
  const rootKey = normalizePathKey(root);
  for (const subset of Array.isArray(subsets) ? subsets : []) {
    if (!subset || typeof subset !== "object") continue;
    const key = normalizePathKey(subset.image_dir);
    if (key.length > 0 && key === rootKey) rootUsed = true;
    counts.set(key, (counts.get(key) || 0) + 1);
  }
  const duplicates = Array.from(counts.values()).filter((count) => count > 1).length;
  const problems = [];
  if (rootUsed) problems.push({ key: "export.consistencyRoot", params: {} });
  if (duplicates > 0) problems.push({ key: "export.consistencyDuplicate", params: { n: duplicates } });
  return problems;
}

function createExportPanel(options) {
  const notify = options.notify;
  const getRoot = options.getRoot;
  const params = options.params || null;
  //: Told what the panel just computed, so the readiness strip can report the same numbers without
  //: recomputing them - one object, two surfaces, no drift. null means "no report for this root".
  const onReport = typeof options.onReport === "function" ? options.onReport : null;
  let currentRoot = null;
  let pickerAllowed = false;

  const make = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
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
  // lives inside the painter, so the switch site never repeats the key; the
  // panel is built at module scope, so without this the tab/section copy would
  // freeze in whatever locale was current at construction.
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
  const labelled = (labelKey, input) => {
    const field = make("label", "field");
    field.append(paintText(make("span"), labelKey), input);
    return field;
  };

  const tab = make("button", "tab");
  tab.type = "button";
  tab.setAttribute("role", "tab");
  tab.dataset.tab = "export";
  paintText(tab, "tab.export");

  const panel = make("div", "tab-panel");
  panel.dataset.panel = "export";
  panel.hidden = true;

  // Section 4.12: the dataset path is now the picker's entry point, so it is a
  // button - it used to be the one place that showed the current directory and
  // did nothing when clicked.
  const rootLine = make("button", "btn path-line mono");
  rootLine.type = "button";
  rootLine.addEventListener("click", () => {
    if (options.onPickDataset) options.onPickDataset();
  });
  const emptyNote = paintText(make("p", "hint"), "export.empty");

  // Section 4.14 shares these with the scaling panel: the toml advertises a
  // resolution and the exported images are scaled to it, so one store feeds both.
  const sharedResolution = params ? params.resolution() : [1536, 1536];
  const resolutionWidth = numberInput(sharedResolution[0], 64, 64);
  const resolutionHeight = numberInput(sharedResolution[1], 64, 64);
  const resolutionGrid = make("div", "threshold-grid");
  const widthField = make("label");
  widthField.append(paintText(make("span"), "export.resWidth"), resolutionWidth);
  const heightField = make("label");
  heightField.append(paintText(make("span"), "export.resHeight"), resolutionHeight);
  resolutionGrid.append(widthField, heightField);
  const resolutionField = make("div", "field");
  resolutionField.append(paintText(make("span"), "export.resolution"), resolutionGrid);

  const batchSize = numberInput(1, 1, 1);
  const numRepeats = numberInput(1, 1, 1);
  const captionExtension = make("input");
  captionExtension.type = "text";
  captionExtension.spellcheck = false;
  captionExtension.value = params ? params.captionExtension() : ".txt";
  // section 3.9: alpha_mask belongs to [[datasets.subsets]] and, like the
  // caption_dropout_* pair, is only written into the toml when it is true.
  const alphaMask = make("input");
  alphaMask.type = "checkbox";
  alphaMask.checked = false;
  const alphaMaskField = make("label", "field-inline");
  alphaMaskField.append(alphaMask, paintText(make("span"), "export.alphaMask"));

  const paramsBox = make("fieldset", "box");
  paramsBox.append(
    paintText(make("legend"), "export.params"),
    resolutionField,
    labelled("export.batchSize", batchSize),
    labelled("export.numRepeats", numRepeats),
    labelled("export.captionExtension", captionExtension),
    alphaMaskField,
    paintText(make("p", "hint"), "export.alphaMaskHint"),
  );

  const generateButton = paintText(make("button", "btn btn-primary"), "export.generate");
  generateButton.type = "button";
  generateButton.id = "btn-export-generate";
  generateButton.disabled = true;
  const copyButton = paintText(make("button", "btn"), "export.copy");
  copyButton.type = "button";
  copyButton.disabled = true;
  const downloadButton = paintText(make("button", "btn"), "export.download");
  downloadButton.type = "button";
  downloadButton.disabled = true;
  const actions = make("div", "row");
  actions.append(generateButton, make("span", "spacer"), copyButton, downloadButton);

  const summary = make("div", "mono");
  const consistency = make("div", "hint");
  const warningsBox = make("div", "warning");
  warningsBox.hidden = true;
  const warningsHead = make("strong");
  const warningsBody = make("div");
  warningsBox.append(warningsHead, paintText(make("p", "hint"), "export.codeHint"), warningsBody);
  const skippedBox = make("div", "warning");
  skippedBox.hidden = true;
  skippedBox.style.borderLeftColor = "var(--danger)";
  const skippedHead = make("strong");
  const skippedBody = make("div");
  skippedBox.append(skippedHead, skippedBody);
  const subsetTitle = make("h3", "panel-title");
  subsetTitle.hidden = true;
  const subsetHost = make("div", "diff");
  const subsetMore = make("p", "hint");
  const tomlTitle = paintText(make("h3", "panel-title"), "export.tomlTitle");
  const tomlArea = make("textarea", "raw-text");
  tomlArea.readOnly = true;
  tomlArea.spellcheck = false;
  tomlArea.rows = 12;

  panel.append(
    paintText(make("h3", "panel-title"), "export.title"),
    paintText(make("p", "hint"), "export.hint"),
    rootLine,
    emptyNote,
    paramsBox,
    actions,
    summary,
    consistency,
    warningsBox,
    skippedBox,
    subsetTitle,
    subsetHost,
    subsetMore,
    tomlTitle,
    tomlArea,
  );

  paintLabels();

  function paintWarningGroup(group) {
    const box = make("div");
    box.append(
      make("strong", "", t("export.group." + group.id)),
      make("span", "hint", " " + t("export.warningCount", { n: group.items.length })),
    );
    const list = make("div");
    for (const item of group.items.slice(0, EXPORT_PREVIEW_WARNINGS)) {
      list.append(make("div", "mono", item.text));
      if (item.dir) list.append(make("div", "hint", t("export.warningFrom", { dir: item.dir })));
    }
    if (group.items.length > EXPORT_PREVIEW_WARNINGS) {
      list.append(make("div", "hint", t("export.warningMore", { n: group.items.length - EXPORT_PREVIEW_WARNINGS })));
    }
    box.append(list);
    return box;
  }

  function paintSkippedGroup(group) {
    const box = make("div");
    // "other" means the reason carried no code we know: print it verbatim
    const heading = group.id === "other"
      ? (group.reason.length > 0 ? group.reason : t("export.skippedNoReason"))
      : t("export.group." + group.id);
    box.append(
      make("strong", "", heading),
      make("span", "hint", " " + t("export.warningCount", { n: group.dirs.length })),
    );
    if (group.details.length > 0) box.append(make("div", "hint", group.details.join(" / ")));
    const list = make("div");
    for (const dir of group.dirs.slice(0, EXPORT_PREVIEW_WARNINGS)) {
      list.append(make("div", "mono", dir));
    }
    if (group.dirs.length > EXPORT_PREVIEW_WARNINGS) {
      list.append(make("div", "hint", t("export.warningMore", { n: group.dirs.length - EXPORT_PREVIEW_WARNINGS })));
    }
    box.append(list);
    return box;
  }

  function paintSubsets(subsets, total) {
    subsetHost.replaceChildren();
    const rows = subsets.filter((entry) => entry && typeof entry === "object");
    if (rows.length === 0) {
      subsetTitle.hidden = true;
      subsetMore.textContent = "";
      return;
    }
    const shown = rows.slice(0, EXPORT_PREVIEW_SUBSETS);
    subsetTitle.hidden = false;
    subsetTitle.textContent = t("export.subsetPreview", { n: shown.length });
    const table = make("table");
    const head = make("tr");
    const columns = ["export.th.imageDir", "export.th.imageCount", "export.th.captionCount", "export.th.numRepeats", "export.th.warnings"];
    for (const key of columns) head.append(make("th", "", t(key)));
    table.append(head);
    for (const subset of shown) {
      const warnings = Array.isArray(subset.warnings) ? subset.warnings : [];
      const warningCell = make("td");
      warningCell.append(make("span", "", t("export.warningCount", { n: warnings.length })));
      if (warnings.length > 0) warningCell.title = warnings.join("\n");
      const row = make("tr");
      row.append(
        make("td", "mono", String(subset.image_dir || "")),
        make("td", "", String(subset.image_count || 0)),
        make("td", "", String(subset.caption_count || 0)),
        make("td", "", String(subset.num_repeats || 0)),
        warningCell,
      );
      table.append(row);
    }
    subsetHost.append(table);
    const hidden = Math.max(0, (Number(total) || rows.length) - shown.length);
    subsetMore.textContent = hidden > 0 ? t("export.subsetMore", { n: hidden }) : "";
  }

  function paintConsistency(subsets, problems) {
    consistency.replaceChildren();
    if (problems.length === 0) {
      const distinct = new Set(subsets.map((subset) => normalizePathKey(subset.image_dir))).size;
      consistency.className = "hint";
      consistency.textContent = t("export.consistencyOk", { n: distinct });
      return;
    }
    consistency.className = "errors";
    for (const problem of problems) {
      consistency.append(make("div", "", t(problem.key, problem.params)));
    }
  }

  //: The last painted report, kept so a locale switch can repaint it in the new
  //: language instead of leaving the warnings in the old one.
  let lastReport = null;

  function paintReport(payload, root) {
    const subsets = Array.isArray(payload.subsets) ? payload.subsets : [];
    const skipped = Array.isArray(payload.skipped) ? payload.skipped : [];
    const groups = groupExportWarnings(payload.warnings, subsets, payload.warning_items);
    const skippedGroups = groupExportSkipped(payload.skipped, payload.skipped_items);
    lastReport = { payload, root };
    const warningCount = groups.reduce((total, group) => total + group.items.length, 0);
    const problems = subsetProblems(subsets, root);
    // One object, painted here and handed to the readiness strip unchanged: the segment on the strip
    // shows the numbers of this panel's own summary line, not a second count of the same thing.
    const computed = {
      subsets: Number(payload.subset_count) || subsets.length,
      images: Number(payload.image_count) || 0,
      skipped: skipped.length,
      warnings: warningCount,
    };

    summary.textContent = t("export.summary", computed);
    paintConsistency(subsets, problems);
    if (onReport) onReport({ root: root, summary: computed, problems: problems.length });

    warningsHead.textContent = t("export.warningsTitle", { n: warningCount });
    warningsBody.replaceChildren();
    if (groups.length === 0) warningsBody.append(make("p", "hint", t("export.warningsNone")));
    for (const group of groups) warningsBody.append(paintWarningGroup(group));
    warningsBox.hidden = false;

    skippedHead.textContent = t("export.skippedTitle", { n: skipped.length });
    skippedBody.replaceChildren();
    if (skipped.length === 0) skippedBody.append(make("p", "hint", t("export.skippedNone")));
    for (const group of skippedGroups) skippedBody.append(paintSkippedGroup(group));
    skippedBox.hidden = false;

    paintSubsets(subsets, payload.subset_count);
    tomlArea.value = typeof payload.toml === "string" ? payload.toml : "";
    copyButton.disabled = tomlArea.value.length === 0;
    downloadButton.disabled = tomlArea.value.length === 0;
    emptyNote.hidden = true;
  }

  function clearReport() {
    lastReport = null;
    // The report belonged to another directory (or a different dataset), so the readiness strip must
    // stop reporting it: a stale "0 warnings" is exactly the drift the strip must not have.
    if (onReport) onReport(null);
    tomlArea.value = "";
    summary.textContent = "";
    consistency.className = "hint";
    consistency.textContent = "";
    warningsBox.hidden = true;
    skippedBox.hidden = true;
    warningsBody.replaceChildren();
    skippedBody.replaceChildren();
    subsetTitle.hidden = true;
    subsetHost.replaceChildren();
    subsetMore.textContent = "";
    copyButton.disabled = true;
    downloadButton.disabled = true;
    emptyNote.textContent = t("export.empty");
    emptyNote.hidden = false;
  }

  /**
   * Section 4.9 exposes four knobs in the form; every other field of
   * DatasetTomlParams keeps the frozen server side default.
   */
  function collectExportParams() {
    const integer = (input, fallback) => {
      const value = Number.parseInt(String(input.value).trim(), 10);
      return Number.isFinite(value) && value > 0 ? value : fallback;
    };
    const extension = String(captionExtension.value).trim();
    const params = {
      resolution: [integer(resolutionWidth, 1536), integer(resolutionHeight, 1536)],
      batch_size: integer(batchSize, 1),
      num_repeats: integer(numRepeats, 1),
      caption_extension: extension.length > 0 ? extension : ".txt",
    };
    // section 3.9: only true is written - an unchecked box must not put a
    // single alpha_mask key into the generated toml.
    if (alphaMask.checked) params.alpha_mask = true;
    return params;
  }

  async function runExport() {
    const root = getRoot();
    if (!root) {
      notify(t("export.notApplicable"), "warn");
      return;
    }
    generateButton.disabled = true;
    generateButton.textContent = t("export.generating");
    try {
      const payload = await api.datasetToml(root, collectExportParams());
      paintReport(payload || {}, root);
      const warned = Array.isArray(payload && payload.warnings) && payload.warnings.length > 0;
      notify(t("export.done", {
        subsets: Number((payload && payload.subset_count) || 0),
        images: Number((payload && payload.image_count) || 0),
      }), warned ? "warn" : "ok");
    } catch (err) {
      notify(t("export.failed", { message: describeError(err) }), "error");
    } finally {
      generateButton.disabled = false;
      generateButton.textContent = t("export.generate");
    }
  }

  async function copyToml() {
    const text = tomlArea.value;
    if (text.length === 0) return;
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        const helper = make("textarea");
        helper.value = text;
        helper.setAttribute("readonly", "readonly");
        helper.style.position = "fixed";
        helper.style.top = "-1000px";
        document.body.append(helper);
        helper.select();
        const copied = document.execCommand("copy");
        helper.remove();
        if (!copied) throw new Error(t("export.copyUnsupported"));
      }
      notify(t("export.copied", { n: text.length }), "ok");
    } catch (err) {
      notify(t("export.copyFailed", { message: describeError(err) }), "error");
    }
  }

  function downloadToml() {
    const text = tomlArea.value;
    if (text.length === 0) return;
    try {
      const blob = new Blob([text], { type: "application/toml" });
      const url = URL.createObjectURL(blob);
      const link = make("a");
      link.href = url;
      link.download = EXPORT_FILE_NAME;
      document.body.append(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      notify(t("export.downloaded", { name: EXPORT_FILE_NAME }), "ok");
    } catch (err) {
      notify(t("export.downloadFailed", { message: describeError(err) }), "error");
    }
  }

  function setRoot(path) {
    const next = path || null;
    const changed = normalizePathKey(next) !== normalizePathKey(currentRoot);
    currentRoot = next;
    rootLine.textContent = next ? t("export.root", { path: next }) : t("export.notApplicable");
    rootLine.disabled = !next || !pickerAllowed;
    generateButton.disabled = !next;
    if (changed) clearReport();
  }

  /** Section 4.12: grey the path button out when the picker is unavailable. */
  function setPickerEnabled(enabled, title) {
    pickerAllowed = enabled === true;
    rootLine.title = title || "";
    setRoot(currentRoot);
  }

  function onShow() {
    setRoot(getRoot());
    if (lastReport && normalizePathKey(lastReport.root) === normalizePathKey(currentRoot)) {
      paintReport(lastReport.payload, lastReport.root);
    }
  }

  /**
   * Section 4.14: the export panel and the scaling panel edit one resolution
   * and one caption extension. Writes go through the store; remote changes are
   * painted back unless the user is typing in that very field.
   */
  function publishSharedParams() {
    if (!params) return;
    params.setResolution(resolutionWidth.value, resolutionHeight.value);
    params.setCaptionExtension(captionExtension.value);
  }

  function paintSharedParams() {
    if (!params) return;
    const resolution = params.resolution();
    if (document.activeElement !== resolutionWidth) resolutionWidth.value = String(resolution[0]);
    if (document.activeElement !== resolutionHeight) resolutionHeight.value = String(resolution[1]);
    if (document.activeElement !== captionExtension) captionExtension.value = params.captionExtension();
  }

  resolutionWidth.addEventListener("input", publishSharedParams);
  resolutionHeight.addEventListener("input", publishSharedParams);
  captionExtension.addEventListener("input", publishSharedParams);
  if (params) params.subscribe(paintSharedParams);

  generateButton.addEventListener("click", () => { void runExport(); });
  copyButton.addEventListener("click", () => { void copyToml(); });
  downloadButton.addEventListener("click", () => { void downloadToml(); });

  if (options.tabsHost) options.tabsHost.append(tab);
  if (options.panelHost) options.panelHost.append(panel);

  /**
   * Relabel contract: repaint every construction-time string in the current
   * language, plus the root line and the last report when they were already
   * painted. Held state only - no request, no re-render, and safe before any
   * data has loaded (an empty rootLine is simply left empty).
   */
  function relabel() {
    paintLabels();
    if (rootLine.textContent) setRoot(currentRoot);
    if (lastReport) paintReport(lastReport.payload, lastReport.root);
  }

  return {
    onShow,
    setRoot,
    setPickerEnabled,
    relabel,
    //: The command palette runs the panel's own Generate (the function its button calls) and
    //: reads the button's own gate to say why it is unavailable.
    generate: runExport,
    status: () => ({
      root: currentRoot,
      canGenerate: !generateButton.disabled,
    }),
  };
}

// ---------------------------------------------------------------------------
// readiness strip (T1.4)
// ---------------------------------------------------------------------------
/**
 * The one place that answers "can I train yet?" - three segments, and each one is the action that
 * fixes it: the gallery's own "select the images missing a caption", the cache rebuild the cache tab
 * already performs, and the export panel's checks.
 *
 * **Every number is handed in from the surface that already owns it** - the listing's own counts
 * object, the cache panel's own view, the export panel's own report - so the strip cannot fetch,
 * count or recompute anything the panels would then have to agree with. A fourth source of truth
 * would drift against the other three, and the drift would be invisible.
 *
 * It lives in the page header (index.html) rather than in the right column, so it is on screen on
 * every tab without competing with the scope strip or the caption dock for column height.
 */
const readiness = createReadinessStrip({
  host: $("readiness"),
  onSelectMissingCaptions: () => { selectMissingCaptions(); },
  onCacheAction: () => { fixCaches(); },
  // The export tab is where the toml is generated, read and copied, so that part never changes; the
  // re-ask is what makes a failed status recoverable from the segment itself (the cache segment's
  // "the click is the retry" rule), and it keeps a click from opening a stale answer.
  onOpenExport: () => { void refreshTomlStatus(); openTab("export"); },
});

// ---------------------------------------------------------------------------
// dataset.toml: the file itself, not the plan (section 4.16)
// ---------------------------------------------------------------------------
/**
 * What the backend last said about <dataset root>/dataset.toml.
 *
 * The strip's third segment answers "is this dataset trainable?", and the trainer reads dataset.toml
 * next to the **dataset root** - not next to the directory being browsed, and not the export panel's
 * plan (that endpoint is pure computation and writes nothing, so its clean report is not a file on
 * disk). The subject is therefore state.root, and the fact is asked for again - a stat, never a
 * guess - wherever it can have changed: a directory or root is opened, the 15 s poll ticks, an export
 * that writes a dataset.toml finishes, and when the segment itself is clicked.
 */
//: Which dataset root the held fact belongs to; a fact about another dataset is never shown.
let tomlFactRoot = null;
//: Guards against an older answer painting over a newer one (two refreshes in flight).
let tomlStatusToken = 0;

async function refreshTomlStatus() {
  const root = state.root;
  const token = ++tomlStatusToken;
  if (!root) {
    tomlFactRoot = null;
    readiness.setTomlFile(null);
    return;
  }
  if (tomlFactRoot !== null && !samePath(tomlFactRoot, root)) {
    // The held fact belongs to another dataset. Keeping it would report one dataset's file while
    // looking at another - the same reason clearReport() empties the export panel on a root change.
    tomlFactRoot = null;
    readiness.setTomlFile(null);
  }
  let payload = null;
  try {
    payload = await api.datasetTomlStatus(root);
  } catch (err) {
    if (token !== tomlStatusToken) return; // a newer question is already in flight
    tomlFactRoot = root;
    // "Could not ask" is not "not there" (the cache segment's rule): the segment says so instead of
    // claiming a file state, and its click retries.
    readiness.setTomlFile({ root: root, failed: true });
    return;
  }
  if (token !== tomlStatusToken) return;
  tomlFactRoot = String((payload && payload.root) || root);
  readiness.setTomlFile({ root: tomlFactRoot, exists: Boolean(payload && payload.exists) });
}

// Section 4.14: the resolution and the caption extension are shared with the
// scaling panel, so one store is created here and handed to both.
const trainParams = createTrainParams();

const exportPanel = createExportPanel({
  tabsHost: document.querySelector(".tabs"),
  panelHost: document.querySelector(".tab-panels"),
  notify: (message, kind) => notify(message, kind),
  getRoot: () => state.dir,
  onPickDataset: () => { void requestPick(); },
  params: trainParams,
  // The readiness strip's third segment is this panel's own report, not a copy of it.
  onReport: (report) => { readiness.setToml(report); },
});

/**
 * Section 4.14: choose the folder the scaled images are written into. A folder
 * outside the whitelist is added as a root first (POST /api/roots is idempotent)
 * - the same move switchDataset() makes for the dataset root. Without it the
 * export would answer 403 and the user could not act on the error from here.
 */
async function ensureTargetRoot(path) {
  const key = normalizePathKey(path);
  const covered = (state.roots || []).some((root) => {
    const rootKey = normalizePathKey(root.path);
    return key === rootKey || key.indexOf(rootKey + "/") === 0;
  });
  if (covered) return;
  try {
    await api.addRoot(path);
    await loadRoots();
  } catch (err) {
    notify(t("picker.selectFailed", { message: describeError(err) }), "error");
  }
}

async function pickExportTarget(startPath) {
  if (!pathConfig.allowed) {
    notify(t("picker.disabled", { host: pathConfig.host || "?" }), "warn");
    return null;
  }
  const picked = await picker.open(startPath || state.dir || "");
  if (!picked) return null;
  await ensureTargetRoot(picked);
  return picked;
}

const scalePanel = createScalePanel({
  tabsHost: document.querySelector(".tabs"),
  panelHost: document.querySelector(".tab-panels"),
  jobs: jobShelf,
  notify: (message, kind) => notify(message, kind),
  // The column's one scope strip, the same instance the tagger, the batch and the crop panels
  // read. The endpoint takes the list now (api/scale.py), so this panel obeys it like the rest.
  scope,
  getRoot: () => state.dir,
  getParams: () => trainParams,
  pickTarget: (startPath) => pickExportTarget(startPath),
  // The export is the one job in this app that writes a dataset.toml (into the target root, which can
  // be the dataset root itself when the source is a subdirectory). The file is the backend's fact, so
  // a finished job re-asks for it instead of assuming one appeared.
  onFinished: () => { void refreshTomlStatus(); },
});

/**
 * Section 4.17: the crop panel. It takes its images from the column's shared scope strip (the
 * same control every other panel answers "which images?" with), inherits the scaling
 * parameters from the same store the scaling panel writes, and tags the archives with the
 * tagger panel's own settings - three existing owners, no second copy of any of them.
 */
async function refreshAfterCrop() {
  if (!state.dir) return;
  // New files exist in the directory on screen and captions may have been written next to them, so
  // the listing (and with it the readiness count), the caption probe and the cache status are all
  // stale. openDir() is the one place that rebuilds a listing, so it is reused, not patched.
  await openDir(state.dir);
  await reloadCaptions();
  await loadCacheStatus();
}

const cropPanel = createCropPanel({
  tabsHost: document.querySelector(".tabs"),
  panelHost: document.querySelector(".tab-panels"),
  jobs: jobShelf,
  notify: (message, kind) => notify(message, kind),
  scope,
  getParams: () => trainParams,
  getTaggerParams: () => autotagPanel.taggerParams(),
  onArchived: () => { void refreshAfterCrop(); },
});


// ---------------------------------------------------------------------------
// the command palette (T3)
// ---------------------------------------------------------------------------
//: Why a command cannot run, as the copy key that says it. The wording lives here (the palette's
//: business); the facts behind it belong to the surface that owns the action - see the table below.
const REASON = Object.freeze({
  noDir: { key: "palette.needDir" },
  atRoot: { key: "palette.atRoot" },
  noImages: { key: "palette.needImages" },
  noSelection: { key: "palette.needSelection" },
  noFilter: { key: "palette.noFilter" },
  noActiveImage: { key: "palette.needActiveImage" },
  noScope: { key: "palette.needScope" },
  needModel: { key: "palette.needModel" },
  needPreview: { key: "palette.needPreview" },
  nothingToWrite: { key: "palette.nothingToWrite" },
  needCaptions: { key: "palette.needCaptions" },
  nothingStale: { key: "palette.nothingStale" },
  needTarget: { key: "palette.needTarget" },
  busy: { key: "palette.busy" },
  notReady: { key: "palette.notReady" },
});

//: Each of these answers "why not?" with a REASON when it applies, and null when it does not.
const whyNoParent = () => (state.parent ? null : (state.dir ? REASON.atRoot : REASON.noDir));
const whyNoImages = () => (gallery.getVisiblePaths().length > 0 ? null : REASON.noImages);
const whyNoSelection = () => (gallery.getSelectedPaths().length > 0 ? null : REASON.noSelection);
const whyNoFilter = () => (filterPanel.hasFilter() ? null : REASON.noFilter);
const whyNoActiveImage = () => (tagEditor.getPath() ? null : REASON.noActiveImage);
//: The picker's own capability, the same flag that greys its buttons out (section 4.12).
const whyPickerOff = () => (
  pathConfig.allowed ? null : { key: "picker.disabled", params: { host: pathConfig.host || "?" } }
);
//: The model-roots dialog is built in boot(); until then there is nothing to open.
const whyNoModelRoots = () => (modelRootsPanel ? null : REASON.notReady);

/**
 * The tagger's two actions. The facts are the panel's own (running / installed / scope / cap /
 * an empty preview); the words are the palette's. Nothing is re-derived here: the panel computes
 * these once and hands them over, so the palette cannot disagree with the panel's buttons.
 */
function whyTaggerPreview() {
  const status = autotagPanel.status();
  if (status.running) return REASON.busy;
  if (!status.installed) return REASON.needModel;
  if (status.scope === 0) return REASON.noScope;
  if (status.previewTooLarge) {
    // The button's own note, reused: one claim about the cap, one wording.
    return { key: "autotag.previewTooManyNote", params: { n: status.scope, max: status.max } };
  }
  return null;
}

function whyTaggerRun() {
  const status = autotagPanel.status();
  if (status.running) return REASON.busy;
  if (status.scope === 0) return REASON.noScope;
  if (status.previewEmpty) return REASON.nothingToWrite;
  return status.canRun ? null : REASON.needPreview;
}

function whyBatchPreview() {
  const status = batchPanel.status();
  if (status.writing) return REASON.busy;
  if (status.scope === 0) return REASON.noScope;
  return status.needsCaptions ? REASON.needCaptions : null;
}

function whyBatchApply() {
  const status = batchPanel.status();
  if (status.writing) return REASON.busy;
  if (status.scope === 0) return REASON.noScope;
  if (status.previewEmpty) return REASON.nothingToWrite;
  return status.canApply ? null : REASON.needPreview;
}

function whyCacheRebuild() {
  if (cacheBusy) return REASON.busy;
  // "Could not read the status" is not "nothing is stale": the rebuild button is disabled in both
  // cases, and only one of them has an answer the user can act on.
  if (cacheFailed) return { key: "cache.loadFailed" };
  return staleCaches.length === 0 ? REASON.nothingStale : null;
}

function whyExportGenerate() {
  const status = exportPanel.status();
  if (!status.root) return REASON.noDir;
  return status.canGenerate ? null : REASON.busy;
}

function whyScaleStart() {
  const status = scalePanel.status();
  if (status.running) return REASON.busy;
  if (!state.dir) return REASON.noDir;
  // An empty scope is a different answer from a missing target folder, and the panel hands over
  // both counts rather than making the palette re-derive them.
  if (status.scope === 0) return REASON.noScope;
  return status.canStart ? null : REASON.needTarget;
}

function whyCropStart() {
  const status = cropPanel.status();
  if (status.running) return REASON.busy;
  return status.canStart ? null : REASON.noImages;
}

/**
 * A command's availability: the first reason that applies, and then the control's own state.
 *
 * The reasons are what the reader gets ("no directory is open" is an answer). The control is the
 * **authority**: a disabled or hidden button is not a route to its action, whatever the reasons
 * believe, so the palette can never offer more than the UI does. If the two ever disagree the entry
 * goes unavailable rather than becoming the privileged path this design forbids.
 */
function gate(control, ...reasons) {
  return () => {
    for (const reason of reasons) {
      const why = reason();
      if (why) return why;
    }
    const node = control ? $(control) : null;
    if (node && (node.disabled || node.hidden)) return REASON.notReady;
    return null;
  };
}

/**
 * Every action the palette offers - and for each one **the same function its button calls**.
 *
 * This table is data, never a second implementation: `run` is a single call to a function that
 * exists elsewhere in the frontend and is wired to the control named next to it
 * (`test_web_palette.py` fails if an entry starts calling something else, and forbids a body of
 * any kind). Nothing here reaches the API, the filesystem or the dataset - the palette cannot do
 * anything the UI cannot.
 *
 * What is deliberately absent: value and mode controls (the language, "include subdirectories",
 * the scope, the tagger's thresholds) because their meaning is their current value and the palette
 * is not a settings editor; the form fields; the cancels (the job shelf carries every running job's
 * own cancel, on screen on every tab, and it calls the panel's own cancel path); and everything
 * that lives inside a dialog (the picker, the zoom viewer, a confirmation), because a modal owns
 * the keyboard while it is open and its actions mean nothing outside it.
 */
const commands = [
  // view: the six tabs. Reaching a panel without knowing which tab owns it is the point.
  {
    id: "view.filter",
    group: "view",
    labelKey: "tab.filter",
    control: "tab-filter",
    run: () => openTab("filter"),
    available: gate("tab-filter"),
  },
  {
    id: "view.autotag",
    group: "view",
    labelKey: "tab.autotag",
    control: "tab-autotag",
    run: () => openTab("autotag"),
    available: gate("tab-autotag"),
  },
  {
    id: "view.cache",
    group: "view",
    labelKey: "tab.cache",
    control: "tab-cache",
    run: () => openTab("cache"),
    available: gate("tab-cache"),
  },
  {
    id: "view.batch",
    group: "view",
    labelKey: "tab.batch",
    control: "tab-batch",
    run: () => openTab("batch"),
    available: gate("tab-batch"),
  },
  {
    id: "view.export",
    group: "view",
    labelKey: "tab.export",
    control: "tab-export",
    run: () => openTab("export"),
    available: gate("tab-export"),
  },
  {
    id: "view.scale",
    group: "view",
    labelKey: "tab.scale",
    control: "tab-scale",
    run: () => openTab("scale"),
    available: gate("tab-scale"),
  },
  {
    id: "view.crop",
    group: "view",
    labelKey: "tab.crop",
    control: "tab-crop",
    run: () => openTab("crop"),
    available: gate("tab-crop"),
  },
  // browse: where the listing comes from.
  {
    id: "browse.refresh",
    group: "browse",
    labelKey: "browse.refresh",
    control: "btn-refresh",
    run: () => refreshBrowse(),
    available: gate("btn-refresh"),
  },
  {
    id: "browse.parent",
    group: "browse",
    labelKey: "browse.parent",
    control: "btn-parent",
    run: () => goToParent(),
    available: gate("btn-parent", whyNoParent),
  },
  {
    id: "browse.pickDataset",
    group: "browse",
    labelKey: "picker.openShort",
    control: "btn-pick-dataset",
    run: () => requestPick(),
    available: gate("btn-pick-dataset", whyPickerOff),
  },
  {
    id: "settings.modelRoots",
    group: "settings",
    labelKey: "settings.modelRootsOpen",
    control: "btn-model-roots",
    run: () => modelRootsPanel.open(),
    available: gate("btn-model-roots", whyNoModelRoots),
  },
  // selection: what the panels that write captions will act on.
  {
    id: "gallery.selectAll",
    group: "selection",
    labelKey: "gallery.selectAll",
    control: "btn-select-all",
    run: () => gallery.selectAll(),
    available: gate("btn-select-all", whyNoImages),
  },
  {
    id: "gallery.selectMissing",
    group: "selection",
    labelKey: "gallery.selectMissing",
    control: "btn-select-missing",
    run: () => selectMissingCaptions(),
    available: gate("btn-select-missing", whyNoImages),
  },
  {
    id: "gallery.clearSelection",
    group: "selection",
    labelKey: "gallery.clearSelection",
    control: "btn-clear-selection",
    run: () => gallery.clearSelection(),
    available: gate("btn-clear-selection", whyNoSelection),
  },
  {
    id: "filter.clearAll",
    group: "selection",
    labelKey: "filter.clearAll",
    control: "btn-filter-clear-all",
    run: () => filterPanel.clearAll(),
    available: gate("btn-filter-clear-all", whyNoFilter),
  },
  // caption: the dock's save, reachable without the dock being in view.
  {
    id: "caption.save",
    group: "caption",
    labelKey: "caption.saveNow",
    control: "btn-save-tags",
    run: () => tagEditor.save(),
    available: gate("btn-save-tags", whyNoActiveImage),
  },
  // jobs: the panels' own actions, the ones that used to require their tab.
  {
    id: "autotag.preview",
    group: "jobs",
    labelKey: "autotag.preview",
    control: "btn-autotag-preview",
    run: () => autotagPanel.preview(),
    available: gate("btn-autotag-preview", whyTaggerPreview),
  },
  {
    id: "autotag.run",
    group: "jobs",
    labelKey: "autotag.run",
    control: "btn-autotag-run",
    run: () => autotagPanel.run(),
    available: gate("btn-autotag-run", whyTaggerRun),
  },
  {
    id: "batch.preview",
    group: "jobs",
    labelKey: "batch.preview",
    control: "btn-batch-preview",
    run: () => batchPanel.preview(),
    available: gate("btn-batch-preview", whyBatchPreview),
  },
  {
    id: "batch.apply",
    group: "jobs",
    labelKey: "batch.apply",
    control: "btn-batch-apply",
    run: () => batchPanel.apply(),
    available: gate("btn-batch-apply", whyBatchApply),
  },
  {
    id: "cache.refresh",
    group: "jobs",
    labelKey: "cache.refresh",
    control: "btn-cache-refresh",
    run: () => loadCacheStatus(),
    available: gate("btn-cache-refresh"),
  },
  {
    id: "cache.rebuildAll",
    group: "jobs",
    labelKey: "cache.rebuildAll",
    control: "btn-cache-rebuild-all",
    run: () => rebuildAllStaleCaches(),
    available: gate("btn-cache-rebuild-all", whyCacheRebuild),
  },
  {
    id: "export.generate",
    group: "jobs",
    labelKey: "export.generate",
    control: "btn-export-generate",
    run: () => exportPanel.generate(),
    available: gate("btn-export-generate", whyExportGenerate),
  },
  {
    id: "scale.start",
    group: "jobs",
    labelKey: "scale.start",
    control: "btn-scale-start",
    run: () => scalePanel.start(),
    available: gate("btn-scale-start", whyScaleStart),
  },
  {
    id: "crop.start",
    group: "jobs",
    labelKey: "crop.start",
    control: "btn-crop-start",
    run: () => cropPanel.start(),
    available: gate("btn-crop-start", whyCropStart),
  },
];

/**
 * The palette itself. It is handed the table and the nodes it paints into - and nothing else:
 * no request, no app state, no second copy of any action. `trigger` is the command bar: the panel
 * describes its expanded state through that bar and hands the keyboard back to it when it closes.
 */
const palette = createCommandPalette({
  host: $("palette"),
  trigger: $("btn-palette"),
  input: $("palette-input"),
  list: $("palette-list"),
  status: $("palette-status"),
  close: $("btn-palette-close"),
  commands: () => commands,
});
paletteRefresh = () => { palette.refresh(); };

/**
 * Open the palette from the command bar or from Ctrl+K (js/palette.js owns the gesture).
 *
 * The breadcrumb's dropdown is the other thing that can be open on this page; a mouse click
 * anywhere else closes it through the document's own click handler, and this is the keyboard's
 * equivalent - the palette is the foreground surface while it is open.
 */
function openPalette() {
  closeCrumbMenu();
  palette.open();
}

// ---------------------------------------------------------------------------
// layout controls
// ---------------------------------------------------------------------------
// The two drag handles between the side panels and the gallery. Widths are
// remembered in localStorage; the handles are keyboard operable too (arrows to
// nudge, Home or a double click to go back to the default).
createResizers({
  layout: document.querySelector(".layout"),
  left: { handle: $("resizer-left"), panel: document.querySelector(".panel-left") },
  right: { handle: $("resizer-right"), panel: document.querySelector(".panel-right") },
});

// ---------------------------------------------------------------------------
// tabs and toolbar
// ---------------------------------------------------------------------------
function setupTabs() {
  const tabs = Array.from(document.querySelectorAll(".tab"));
  const panels = Array.from(document.querySelectorAll(".tab-panel"));

  // role="tab" without aria-selected tells a screen reader nothing about which
  // panel is showing, and the panels never said they were panels. Wired here so
  // the tabs the panels append at construction get it too.
  for (const tab of tabs) {
    const name = tab.dataset.tab;
    const panel = panels.find((node) => node.dataset.panel === name);
    tab.id = "tab-" + name;
    tab.setAttribute("aria-controls", "panel-" + name);
    if (!panel) continue;
    panel.id = "panel-" + name;
    panel.setAttribute("role", "tabpanel");
    panel.setAttribute("aria-labelledby", tab.id);
  }

  function selectTab(tab, options) {
    const focus = Boolean(options && options.focus);
    const runHooks = !options || options.runHooks !== false;
    for (const other of tabs) {
      const active = other === tab;
      other.classList.toggle("is-active", active);
      other.setAttribute("aria-selected", active ? "true" : "false");
      // Roving tabindex: only the selected tab is in the tab order, which is what
      // makes Left/Right the way to move between them.
      other.tabIndex = active ? 0 : -1;
    }
    for (const panel of panels) panel.hidden = panel.dataset.panel !== tab.dataset.tab;
    if (focus) tab.focus();
    if (!runHooks) return;
    if (tab.dataset.tab === "autotag") {
      void autotagPanel.loadModelsOnce();
      autotagPanel.refreshScopes();
    }
    if (tab.dataset.tab === "cache") void loadCacheStatus();
    if (tab.dataset.tab === "batch") batchPanel.onShow();
    if (tab.dataset.tab === "export") exportPanel.onShow();
    if (tab.dataset.tab === "scale") scalePanel.onShow();
    if (tab.dataset.tab === "crop") cropPanel.onShow();
  }

  for (const [index, tab] of tabs.entries()) {
    tab.addEventListener("click", () => selectTab(tab));
    tab.addEventListener("keydown", (event) => {
      const step = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
      let target = null;
      if (step !== 0) target = tabs[(index + step + tabs.length) % tabs.length];
      else if (event.key === "Home") target = tabs[0];
      else if (event.key === "End") target = tabs[tabs.length - 1];
      if (!target) return;
      event.preventDefault();
      selectTab(target, { focus: true });
    });
  }

  // The tab marked active in the markup is the one that is showing; paint its
  // state without re-running the show hooks boot has not reached yet.
  const initial = tabs.find((tab) => tab.classList.contains("is-active")) || tabs[0];
  if (initial) selectTab(initial, { runHooks: false });

  // The job shelf can take the user to the tab that owns a running job, so "where do I look" has an
  // answer that does not depend on remembering which panel was clicked.
  openTab = (kind) => {
    const tab = tabs.find((entry) => entry.dataset.tab === kind);
    if (tab) selectTab(tab);
  };
}

/**
 * The caption-less images, put on screen and selected.
 *
 * The gallery toolbar's own button and the readiness strip's caption segment both run this - the
 * strip does not invent a second way to find them. Selecting them is a change of "what I am looking
 * at", not a second layer on top of the tag filter: entering the mode is the mutual exclusion.
 */
function selectMissingCaptions() {
  filterPanel.setMissingOnly(true);
  applyFilter();
  const count = gallery.selectAll();
  if (count === 0) notify(t("gallery.selectMissingNone"), "info");
}

/**
 * Re-list what is on screen (or the roots, when nothing is). A named function rather than a
 * closure inside setupToolbar, because the command palette's entry for it has to be the same
 * function this button's listener calls - a second copy is a second behaviour.
 */
function refreshBrowse() {
  if (state.dir) void openDir(state.dir, state.rootName);
  else void loadRoots();
}

/** Go up one level. Named for the same reason as refreshBrowse. */
function goToParent() {
  if (state.parent) void openDir(state.parent, state.rootName);
}

function setupToolbar() {
  $("btn-refresh").addEventListener("click", refreshBrowse);
  $("btn-parent").addEventListener("click", goToParent);
  $("btn-palette").addEventListener("click", () => { openPalette(); });
  $("btn-select-all").addEventListener("click", () => gallery.selectAll());
  $("btn-clear-selection").addEventListener("click", () => gallery.clearSelection());
  $("btn-filter-clear").addEventListener("click", () => filterPanel.clearTags());
  // The same action, offered where the user discovers the dead end (paintEmptyState).
  $("btn-gallery-clear-filter").addEventListener("click", () => filterPanel.clearAll());
  $("btn-filter-clear-all").addEventListener("click", () => filterPanel.clearAll());
  $("btn-select-missing").addEventListener("click", () => { selectMissingCaptions(); });
  $("recursive-toggle").addEventListener("change", () => {
    if (state.dir) void openDir(state.dir, state.rootName);
  });
  $("btn-cache-refresh").addEventListener("click", () => { void loadCacheStatus(); });
  $("btn-caption-toggle").addEventListener("click", () => { setCaptionCollapsed(!captionCollapsed); });
  $("btn-cache-cancel").addEventListener("click", () => { void cancelCacheRebuild(); });
  $("btn-cache-rebuild-all").addEventListener("click", () => { void rebuildAllStaleCaches(); });
  window.addEventListener("hashchange", () => {
    const path = decodeURIComponent(window.location.hash.replace(/^#/, ""));
    if (path && path !== state.dir) void openDir(path, state.rootName);
  });
}

// ---------------------------------------------------------------------------
// language switching (i18n)
// ---------------------------------------------------------------------------
// setLocale() relabels every static data-copy* node and fires a locale-change
// event; everything produced by t() at render time has to be repainted here.
// A page reload would be simpler but would throw away unsaved caption edits,
// which is exactly the moment a user is most likely to be switching language.
//
// The list below is deliberately explicit: a component that gains dynamic copy
// and is not registered here will silently stay in the old language, so the
// missing-relabel path warns loudly instead of failing quiet.
//: The model-search-roots dialog is created in boot(); it is captured here so a
//: language switch can relabel its source badges (repo / file / CLI).
let modelRootsPanel = null;

function relabelComponent(component) {
  if (component && typeof component.relabel === "function") {
    component.relabel();
    return;
  }
  console.warn("[i18n] panel has no relabel(); its dynamic copy will be stale");
}

function setupLanguageSelect() {
  const select = $("lang-select");
  select.replaceChildren();
  for (const locale of LOCALES) {
    const option = document.createElement("option");
    option.value = locale.tag;
    option.textContent = locale.label; // endonym, intentionally untranslated
    select.append(option);
  }
  select.value = getLocale();
  select.addEventListener("change", () => {
    if (setLocale(select.value)) void relabelAfterLocaleChange();
  });
  onLocaleChange(() => { select.value = getLocale(); });
}

async function relabelAfterLocaleChange() {
  await checkHealth();
  // The cache status line and its cancel label are painted imperatively; the
  // static data-copy pass cannot know about them.
  paintCacheStatus();
  relabelComponent(rootsPanel);
  relabelComponent(modelRootsPanel);
  renderBreadcrumb();
  repaintDirs();
  renderCounts(state.counts, $("recursive-toggle").checked);
  relabelComponent(gallery);
  relabelComponent(tagEditor);
  // The dock's status line (the active image's size, a load failure, or the
  // empty state) and its collapse toggle are painted at render time, which a
  // language switch does not trigger - so both are repainted here from the state
  // the app recorded.
  relabelComponent(filterPanel);
  paintActiveMeta();
  paintCaptionToggle();
  relabelComponent(autotagPanel);
  // batch/export/scale paint their tab button and section labels at module
  // scope, so they must be relabelled whether or not their tab happens to be
  // active. onShow() below only refreshes the data-derived parts of the panel.
  relabelComponent(batchPanel);
  relabelComponent(exportPanel);
  relabelComponent(scalePanel);
  relabelComponent(cropPanel);
  // The strip's own copy is painted at render time: its <select> options are written by
  // scope.js:paint() (label + live count), and applyCopy() only reaches the static data-copy
  // nodes. paint() is a pure redraw from the providers, so it issues no request.
  scope.paint();
  // The readiness strip's three segments are all render-time copy (counts, a status line, a report
  // summary) and none of them is a data-copy node, so the static pass cannot reach them.
  readiness.relabel();
  // The theme switch's label is one data-copy node, which setLocale()'s own pass already reaches;
  // it is registered anyway, because a component whose copy is not on this list is a component a
  // later change can silently leave in the old language.
  relabelComponent(themeSwitch);
  // The palette's rows are render-time copy (the command names, the reasons) and it is a modal, so
  // the language switcher is behind it - but a switch can still happen between opening and painting,
  // and relabel() is a pure redraw from the query it already holds.
  palette.relabel();
  renderCacheList();
  applyPickerAvailability();
  const active = document.querySelector(".tab.is-active");
  const tab = active ? active.dataset.tab : "";
  if (tab === "batch") batchPanel.onShow();
  if (tab === "export") exportPanel.onShow();
  if (tab === "scale") scalePanel.onShow();
  if (tab === "crop") cropPanel.onShow();
}

// ---------------------------------------------------------------------------
// boot
// ---------------------------------------------------------------------------
async function boot() {
  // initLocale() already ran at module scope, above the first factory: the
  // panels paint t() at construction, so resolving the locale here would be too
  // late (and a second resolution could disagree with the first).
  applyCopy();
  setupTabs();
  resumeRunningJobs();
// The pill is a claim about the server; keep it true.
window.setInterval(() => { void checkHealth(); }, HEALTH_POLL_MS);

/** How often an open directory is re-checked for changes made outside this app. */
const CHANGE_POLL_MS = 15000;
//: { dir, stamp } of the last listing this poll has seen; an empty stamp means "not seen yet".
let changeStamp = { dir: "", stamp: "" };

/** Count + newest mtime: enough to notice an image or a caption appearing, leaving or changing. */
function listingStamp(payload) {
  const images = payload.images || [];
  let newest = 0;
  for (const item of images) newest = Math.max(newest, Number(item.mtime) || 0);
  return images.length + ":" + newest;
}

/**
 * Follow a dataset that another tool (or the trainer) is editing. This is a poll,
 * not a watcher: the backend has no change notification, and inventing one would
 * be a second source of truth for "what changed".
 *
 * Three situations must never trigger the reload, because the reload would be the
 * thing that does damage: our own writes (a running job changes mtimes every
 * second), a background tab nobody is looking at, and a field somebody is typing in.
 */
async function pollForExternalChanges() {
  if (!state.dir) return;
  // A dataset.toml appearing or vanishing is invisible to the listing fingerprint below (the file
  // holds no images, and at the dataset root there are none at all), so the fact is re-asked on
  // every tick. It is a stat rather than a re-list, which is why it is asked before the three
  // reload guards: those exist to protect the editor from openDir(), and a question damages nothing.
  void refreshTomlStatus();
  if (jobShelf.active().length > 0) return;
  if (document.visibilityState !== "visible") return;
  const focused = document.activeElement;
  if (focused && /^(INPUT|TEXTAREA|SELECT)$/.test(focused.tagName)) return;
  let payload;
  try {
    payload = await api.listDir(state.dir);
  } catch (error) {
    return; // a poll that fails is not worth interrupting anyone over
  }
  const stamp = listingStamp(payload);
  if (changeStamp.dir !== state.dir || changeStamp.stamp === "") {
    changeStamp = { dir: state.dir, stamp };
    return; // first sighting of a directory is the baseline, not a change
  }
  if (stamp === changeStamp.stamp) return;
  changeStamp = { dir: state.dir, stamp };
  await openDir(state.dir, state.rootName);
  notify(t("gallery.datasetChanged"), "info");
}
window.setInterval(() => { void pollForExternalChanges(); }, CHANGE_POLL_MS);
  setupToolbar();
  // Remembered like the panel widths: a user who folded the editor away on a
  // short window should not have to fold it again after every reload.
  setCaptionCollapsed(readCaptionCollapsed());
  setupLanguageSelect();
  // Section 4.11: the model search roots dialog. Created here, after autotagPanel
  // exists, because adding or removing a search root changes what
  // GET /api/autotag/models lists - onModelsChanged reloads that dropdown through
  // the autotag panel's own loadModels() instead of a second copy of the logic.
  modelRootsPanel = createModelRootsPanel({
    confirm: askConfirm,
    notify: (message, kind) => notify(message, kind),
    onModelsChanged: () => { void autotagPanel.loadModels(); },
  });
  await checkHealth();
  applyPickerAvailability();
  const roots = await loadRoots();
  const fromHash = decodeURIComponent(window.location.hash.replace(/^#/, ""));
  if (fromHash) {
    await openDir(fromHash, state.rootName);
    return;
  }
  if (roots.length > 0) await openDir(roots[0].path, roots[0].name || roots[0].path);
}

window.addEventListener("error", (event) => {
  notify(t("app.fatal", { message: event.message || "unknown" }), "error");
});

void boot();
