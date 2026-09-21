/**
 * Runtime editable path configuration (p0-spec section 4.11).
 *
 * Two panels live here because both edit a server side list and both must print
 * the server's own warning instead of swallowing it:
 *
 *   createRootsPanel      - the whitelist roots of the left column, backed by
 *                           GET / POST / DELETE /api/roots
 *   createModelRootsPanel - the autotag panel's model search roots behind the
 *                           "model folders" dialog, backed by
 *                           GET / POST / DELETE /api/autotag/model-roots
 *
 * Both factories look their elements up with document.getElementById at call
 * time (never at module scope, so importing this file touches no DOM) and both
 * tolerate a missing element: one renamed id must not blank the whole page.
 * Every request goes through api.js - no path literal and no network call here.
 *
 * Section 4.11 keeps the server authoritative: a POST answers with the full new
 * list plus {changed, persisted, warning}, so both panels repaint from the
 * response instead of guessing, and a non empty warning is always shown.
 */
import { api } from "./api.js";
import { t } from "./strings.js";

/** Section 3.7.1: the five origins a model search root can have. */
const SOURCE_KEYS = {
  repo: "settings.sourceRepo",
  file: "settings.sourceFile",
  cli: "settings.sourceCli",
  explicit: "settings.sourceExplicit",
  auto: "settings.sourceAuto",
};

/** Server error messages (ApiError.message) are shown verbatim, never rewritten. */
function describeError(err) {
  return err && err.message ? err.message : String(err);
}

function sourceKey(source) {
  const key = SOURCE_KEYS[String(source)];
  return key === undefined ? "settings.sourceUnknown" : key;
}

function makeElement(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

/**
 * The whitelist roots of the left column.
 *
 * Returns { reload, current, repaint }:
 *   reload()  -> GET /api/roots, repaints, returns the roots array
 *   current() -> the roots array of the last successful load
 *   repaint() -> repaints from the cached array (no request), so the caller can
 *                refresh the active highlight after navigating
 *
 * Options: notify(message, kind), onOpen(root), isActive(path).
 */
export function createRootsPanel(options) {
  const config = options || {};
  const notify = config.notify || (() => {});
  const onOpen = config.onOpen || (() => {});
  const isActive = config.isActive || (() => false);
  const confirmFn = config.confirm || (() => true);
  // Section 4.12: when absent the graphical picker entry is simply not built.
  const onPickDataset = typeof config.onPickDataset === "function" ? config.onPickDataset : null;

  const listEl = document.getElementById("root-list");
  const addButton = document.getElementById("btn-add-root");
  const form = document.getElementById("root-add-form");
  const input = document.getElementById("root-path-input");
  const okButton = document.getElementById("btn-root-add-ok");
  const cancelButton = document.getElementById("btn-root-add-cancel");

  let pickerTitle = "";

  // Section 4.12: the picker entry sits next to the manual add button. Built in
  // JavaScript rather than index.html because that markup is pinned by
  // test_web_settings' id list; a missing button must not blank the panel.
  const pickButton = document.createElement("button");
  pickButton.type = "button";
  // Named so the command palette can gate on this button's own enabled state: the palette holds a
  // control per command and reads its disabled state as the last word (see app.js's command table).
  pickButton.id = "btn-pick-dataset";
  pickButton.className = "btn btn-mini";
  paintPickButton();
  pickButton.hidden = onPickDataset === null;
  if (onPickDataset) pickButton.addEventListener("click", () => { onPickDataset(); });
  if (addButton && addButton.parentNode) addButton.parentNode.insertBefore(pickButton, addButton);

  let roots = [];
  let busy = false;

  /** The picker entry's own copy; extracted so relabel() reuses both keys. */
  function paintPickButton() {
    pickButton.textContent = t("picker.openShort");
    pickButton.title = pickerTitle || t("picker.open");
  }

  function setFormOpen(open) {
    if (!form) return;
    form.hidden = !open;
    if (open && input) {
      input.value = "";
      input.focus();
    }
  }

  function paint() {
    if (!listEl) return;
    listEl.replaceChildren();
    if (roots.length === 0) {
      listEl.append(makeElement("li", "hint", t("browse.noRoots")));
      return;
    }
    // Section 4.11: deleting the last root is a 400 last_root, because an empty
    // whitelist turns every browse call into a 403 - the button says so up front.
    const removable = roots.length > 1;
    for (const root of roots) {
      const item = makeElement("li", "root-item");

      const entry = makeElement("button", "btn-link");
      entry.type = "button";
      entry.dataset.path = root.path;
      entry.append(makeElement("span", "li-name", root.name || root.path));
      if (root.exists === false || root.is_dir === false) {
        entry.append(makeElement("span", "li-count root-missing", t("settings.rootMissing")));
      }
      // Section 4.12: clicking the root that is already open is a no-op, so that
      // is where "switch to another dataset" belongs instead of a dead click.
      const active = isActive(root.path);
      const canPick = onPickDataset !== null && active;
      entry.title = canPick ? t("picker.open") : root.path;
      if (active) entry.classList.add("is-active");
      entry.addEventListener("click", () => {
        if (canPick) { onPickDataset(); return; }
        onOpen(root);
      });

      const remove = makeElement("button", "root-remove", t("settings.rootsRemove"));
      remove.type = "button";
      remove.disabled = !removable || busy;
      remove.title = removable
        ? t("settings.rootsRemoveTitle", { path: root.path })
        : t("settings.lastRoot");
      if (!removable) item.title = t("settings.lastRoot");
      remove.addEventListener("click", () => { void removeRoot(root); });

      item.append(entry, remove);
      listEl.append(item);
    }
  }

  function adopt(payload) {
    if (payload && Array.isArray(payload.roots)) roots = payload.roots;
  }

  function reportWarning(payload) {
    if (payload && payload.warning) notify(String(payload.warning), "info");
  }

  // Section 4.11: persisted=false without a warning means the root is in the list
  // but only for this run (it came from --roots). Saying just "already in the list"
  // would let the user believe it survives a restart.
  function reportNotPersisted(payload) {
    if (payload && payload.persisted === false && !payload.warning) {
      notify(t("settings.sessionOnly"), "warn");
    }
  }

  async function reload() {
    if (!listEl) return roots;
    try {
      const payload = await api.roots();
      roots = payload && Array.isArray(payload.roots) ? payload.roots : [];
    } catch (err) {
      roots = [];
      paint();
      notify(describeError(err), "error");
      return roots;
    }
    paint();
    return roots;
  }

  async function submitAdd() {
    if (busy) return;
    const path = input ? input.value.trim() : "";
    if (path.length === 0) {
      notify(t("settings.rootPathEmpty"), "warn");
      if (input) input.focus();
      return;
    }
    busy = true;
    paint();
    let payload = null;
    try {
      payload = await api.addRoot(path);
    } catch (err) {
      busy = false;
      paint();
      notify(describeError(err), "error");
      return;
    }
    busy = false;
    adopt(payload);
    paint();
    if (payload && payload.changed === false) notify(t("settings.rootsUnchanged", { path }), "info");
    else notify(t("settings.rootsAdded", { path }), "ok");
    setFormOpen(false);
    reportWarning(payload);
    reportNotPersisted(payload);
  }

  async function removeRoot(root) {
    if (busy || !root || !root.path) return;
    // Rewriting roots.txt is not undoable from here, and this panel is the only
    // place in the app that edits a user-authored file without asking.
    const ok = await confirmFn({
      body: t("settings.rootRemoveConfirm", { path: root.path }),
      confirmLabel: t("settings.rootRemoveOk"),
      danger: true,
    });
    if (!ok) return;
    busy = true;
    paint();
    let payload = null;
    try {
      payload = await api.removeRoot(root.path);
    } catch (err) {
      busy = false;
      paint();
      notify(describeError(err), "error");
      return;
    }
    busy = false;
    adopt(payload);
    paint();
    notify(t("settings.rootsRemoved", { path: root.path }), "ok");
    reportWarning(payload);
  }

  if (addButton) addButton.addEventListener("click", () => { setFormOpen(form ? form.hidden : true); });
  if (cancelButton) cancelButton.addEventListener("click", () => { setFormOpen(false); });
  if (form) {
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      void submitAdd();
    });
  } else if (okButton) {
    okButton.addEventListener("click", () => { void submitAdd(); });
  }
  if (input) {
    input.addEventListener("keydown", (event) => {
      if (event.key === "Escape") setFormOpen(false);
    });
  }

  /** Section 4.12: the app mirrors the server's capability onto the entry point. */
  function setPickerEnabled(enabled, title) {
    pickerTitle = title || "";
    pickButton.disabled = enabled !== true;
    paintPickButton();
  }

  /**
   * Re-apply the current locale to the strings this panel renders itself.
   * paint() repaints the list from the cached roots array - no request - and
   * shows the empty hint only when the cache really is empty.
   */
  function relabel() {
    paintPickButton();
    paint();
  }

  return { reload, current: () => roots, repaint: paint, setPickerEnabled, relabel };
}

/**
 * The autotag panel's model search roots (section 4.11).
 *
 * Returns { open, close, reload }. Every successful mutation repaints the list
 * and calls onModelsChanged(), so the autotag panel's own model dropdown is reloaded
 * from the same server state instead of a second, drifting copy of it.
 *
 * Options: notify(message, kind), onModelsChanged().
 */
export function createModelRootsPanel(options) {
  const config = options || {};
  const notify = config.notify || (() => {});
  const onModelsChanged = config.onModelsChanged || (() => {});
  const confirmFn = config.confirm || (() => true);

  const openButton = document.getElementById("btn-model-roots");
  const dialog = document.getElementById("model-roots-dialog");
  const listEl = document.getElementById("model-roots-list");
  const input = document.getElementById("model-root-path-input");
  const addButton = document.getElementById("btn-model-root-add");
  const closeButton = document.getElementById("btn-model-roots-close");

  let roots = [];
  let busy = false;

  //: Where focus was before the dialog opened (it is opened by a button in the
  //: autotag panel, which must get the keyboard back when the dialog closes).
  let restoreFocusTo = null;

  function close() {
    if (dialog) dialog.hidden = true;
    if (restoreFocusTo && typeof restoreFocusTo.focus === "function") restoreFocusTo.focus();
    restoreFocusTo = null;
  }

  function open() {
    if (!dialog) return;
    restoreFocusTo = document.activeElement || null;
    dialog.hidden = false;
    if (input) input.focus();
    void reload();
  }

  function paint() {
    if (!listEl) return;
    listEl.replaceChildren();
    if (roots.length === 0) {
      listEl.append(makeElement("li", "hint", t("settings.modelRootsEmpty")));
      return;
    }
    for (const root of roots) {
      const item = makeElement("li", "model-root-item");

      const path = makeElement("span", "li-name mono", root.path);
      path.title = root.path;

      const models = Array.isArray(root.models) ? root.models : [];
      const parsed = Number(root.model_count);
      const modelCount = Number.isFinite(parsed) && parsed > 0 ? parsed : models.length;
      const count = makeElement("span", "li-count");
      count.textContent = modelCount > 0
        ? t("settings.modelRootCount", { n: modelCount })
        : t("settings.modelRootNoModels");
      if (models.length > 0) count.title = models.join(", ");

      // Section 4.11: file_backed is the frontend's single question - only a
      // "file" root is persisted, everything else comes back after a restart.
      const fileBacked = root.file_backed === true;
      const badge = makeElement("span", "badge-source " + (fileBacked ? "is-file" : "is-session"));
      badge.textContent = t(sourceKey(root.source));
      badge.title = fileBacked
        ? t("settings.modelRootRemoveTitle", { path: root.path })
        : t("settings.modelRootRemoveSessionTitle", { path: root.path });

      const remove = makeElement("button", "model-root-remove");
      remove.type = "button";
      remove.textContent = fileBacked ? t("settings.modelRootRemove") : t("settings.modelRootRemoveSession");
      remove.title = fileBacked
        ? t("settings.modelRootRemoveTitle", { path: root.path })
        : t("settings.modelRootRemoveSessionTitle", { path: root.path });
      remove.disabled = busy;
      remove.addEventListener("click", () => { void removeRoot(root); });

      item.append(path, count, badge);
      if (!fileBacked) item.append(makeElement("span", "badge-session", t("settings.sessionOnly")));
      item.append(remove);
      listEl.append(item);
    }
  }

  function adopt(payload) {
    if (payload && Array.isArray(payload.roots)) roots = payload.roots;
  }

  function reportWarning(payload) {
    if (payload && payload.warning) notify(String(payload.warning), "info");
  }

  /** See createRootsPanel.reportNotPersisted - same rule, same reason. */
  function reportNotPersisted(payload) {
    if (payload && payload.persisted === false && !payload.warning) {
      notify(t("settings.sessionOnly"), "warn");
    }
  }

  async function reload() {
    if (!listEl) return roots;
    try {
      const payload = await api.modelRoots();
      roots = payload && Array.isArray(payload.roots) ? payload.roots : [];
    } catch (err) {
      roots = [];
      paint();
      notify(describeError(err), "error");
      return roots;
    }
    paint();
    return roots;
  }

  async function submitAdd() {
    if (busy) return;
    const path = input ? input.value.trim() : "";
    if (path.length === 0) {
      notify(t("settings.rootPathEmpty"), "warn");
      if (input) input.focus();
      return;
    }
    busy = true;
    paint();
    let payload = null;
    try {
      payload = await api.addModelRoot(path);
    } catch (err) {
      busy = false;
      paint();
      notify(describeError(err), "error");
      return;
    }
    busy = false;
    adopt(payload);
    paint();
    if (payload && payload.changed === false) notify(t("settings.modelRootUnchanged", { path }), "info");
    else notify(t("settings.modelRootAdded", { path }), "ok");
    reportWarning(payload);
    reportNotPersisted(payload);
    if (input) input.value = "";
    onModelsChanged();
  }

  async function removeRoot(root) {
    if (busy || !root || !root.path) return;
    const fileBacked = root.source === "file";
    const ok = await confirmFn({
      body: fileBacked
        ? t("settings.modelRootRemoveConfirm", { path: root.path })
        : t("settings.modelRootRemoveSessionConfirm", { path: root.path }),
      // Deleting from model_paths.txt is permanent; the action button says so,
      // while the list button stays short.
      confirmLabel: fileBacked
        ? t("settings.modelRootRemoveOk")
        : t("settings.modelRootRemoveSessionOk"),
      danger: fileBacked,
    });
    if (!ok) return;
    busy = true;
    paint();
    let payload = null;
    try {
      payload = await api.removeModelRoot(root.path);
    } catch (err) {
      busy = false;
      paint();
      notify(describeError(err), "error");
      return;
    }
    busy = false;
    adopt(payload);
    paint();
    const removed = payload && payload.removed ? payload.removed : root.path;
    if (payload && payload.removed_scope === "session") {
      notify(t("settings.removedSession", { path: removed }), "info");
    } else {
      notify(t("settings.removedFile", { path: removed }), "ok");
    }
    reportWarning(payload);
    onModelsChanged();
  }

  if (openButton) openButton.addEventListener("click", open);
  if (closeButton) closeButton.addEventListener("click", close);
  if (addButton) addButton.addEventListener("click", () => { void submitAdd(); });
  if (input) {
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        void submitAdd();
      }
    });
  }
  if (dialog) dialog.addEventListener("click", (event) => { if (event.target === dialog) close(); });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && dialog && !dialog.hidden) close();
  });

  /**
   * Re-apply the current locale to the list this panel owns. paint() renders
   * from the cached roots array, so relabeling costs no request.
   */
  function relabel() {
    paint();
  }

  return { open, close, reload, relabel };
}
