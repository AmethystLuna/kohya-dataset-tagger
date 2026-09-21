/**
 * Graphical directory picker for the dataset root (p0-spec section 4.12).
 *
 * Why a modal built in JavaScript instead of markup in index.html: that markup
 * is pinned by another contract (test_web_settings owns its id list), and this
 * component is self-contained. It never touches the network directly - every
 * request goes through api.js, and every string through strings.js (the static
 * contract test forbids CJK literals in any other file).
 *
 * open(startPath) resolves to the chosen absolute path, or null when the user
 * cancels (Escape, the backdrop, the close or cancel button).
 */
import { api } from "./api.js";
import { t } from "./strings.js";
import { childOnPath, createScrollMemory, revealRow, samePath } from "./navscroll.js";

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

function describeError(err) {
  return err && err.message ? err.message : String(err);
}

export function createPicker(options) {
  const config = options || {};
  const notify = config.notify || (() => {});

  const backdrop = el("div", "dialog-backdrop picker-backdrop");
  backdrop.hidden = true;
  const dialog = el("div", "dialog picker-dialog");
  dialog.setAttribute("role", "dialog");
  dialog.setAttribute("aria-modal", "true");

  const closeButton = button("btn btn-mini", t("picker.close"));
  const head = el("div", "dialog-head");
  head.append(el("h2", "panel-title", t("picker.title")), el("span", "spacer"), closeButton);

  // The current path is an editable field, not a label: typing an absolute path
  // and pressing Enter is the manual route (the picker may be the only navigation
  // once the whitelist is empty). Enter outside this field still means "use it".
  const pathInput = el("input", "picker-path mono");
  pathInput.type = "text";
  pathInput.spellcheck = false;
  pathInput.placeholder = t("picker.pathPlaceholder");
  const goButton = button("btn btn-mini picker-go", t("picker.go"));
  const pathRow = el("div", "row picker-path-row");
  pathRow.append(pathInput, goButton);

  // New directory: an inline field instead of window.prompt (prompt cannot be
  // tested headlessly, cannot be translated and blocks the modal).
  const newNameInput = el("input", "picker-newname");
  newNameInput.type = "text";
  newNameInput.spellcheck = false;
  newNameInput.placeholder = t("picker.newDirPlaceholder");
  const newDirButton = button("btn picker-newdir", t("picker.newDir"));
  const newRow = el("div", "row picker-newdir-row");
  newRow.append(newNameInput, newDirButton);

  const jumps = el("div", "picker-jumps");
  const list = el("ul", "list picker-list");
  const emptyNote = el("p", "hint", t("picker.empty"));
  emptyNote.hidden = true;

  const upButton = button("btn picker-up", t("picker.up"));
  const useButton = button("btn btn-primary picker-use", t("picker.use"));
  const cancelButton = button("btn picker-cancel", t("picker.cancel"));
  const actions = el("div", "row");
  actions.append(upButton, el("span", "spacer"), useButton, cancelButton);

  dialog.append(head, el("p", "hint", t("picker.hint")), pathRow, newRow, jumps, list, emptyNote, actions);
  backdrop.append(dialog);
  document.body.append(backdrop);

  let currentPath = "";
  let parentPath = null;
  let pending = null;
  let busy = false;
  let token = 0;
  //: Where focus was before the dialog opened, so it can be handed back.
  let restoreFocusTo = null;
  // Navigation scroll (see navscroll.js): the listing is rebuilt on every step,
  // so "up one level" has to bring the directory it came out of back into view,
  // and a directory that was already visited keeps the position it was left at.
  const scrollMemory = createScrollMemory();

  /** Is this event coming from inside the dialog? */
  function insideDialog(node) {
    for (let current = node; current; current = current.parentNode) {
      if (current === dialog) return true;
    }
    return false;
  }

  function onKeyDown(event) {
    // The handler listens on the document, so it must ignore keys aimed at the
    // page behind the dialog: with focus left out there, an Enter meant for a
    // button back there would settle the picker and "use" whatever directory
    // happens to be on screen.
    if (!insideDialog(event.target)) return;
    if (event.key === "Escape") {
      event.preventDefault();
      settle(null);
      return;
    }
    // Enter while typing a path belongs to that field (go there), not to
    // "use the directory on screen".
    const target = event.target;
    if (target && String(target.tagName || "").toUpperCase() === "INPUT") return;
    if (event.key === "Enter" && !busy && currentPath) {
      event.preventDefault();
      settle(currentPath);
    }
  }

  function settle(value) {
    const resolve = pending;
    pending = null;
    backdrop.hidden = true;
    document.removeEventListener("keydown", onKeyDown);
    // Hand the keyboard back where it came from; a modal that swallows focus
    // leaves the user at the top of the page.
    if (restoreFocusTo && typeof restoreFocusTo.focus === "function") restoreFocusTo.focus();
    restoreFocusTo = null;
    if (resolve) resolve(value);
  }

  function renderJumps(roots, home) {
    jumps.replaceChildren();
    const targets = [];
    for (const root of Array.isArray(roots) ? roots : []) targets.push(String(root));
    if (home) {
      const key = String(home).toLowerCase();
      if (!targets.some((item) => item.toLowerCase() === key)) targets.push(String(home));
    }
    if (targets.length === 0) return;
    jumps.append(el("span", "hint", t("picker.jumps")));
    for (const target of targets) {
      const jump = button("btn btn-mini picker-jump", target);
      jump.title = target;
      jump.addEventListener("click", () => { void navigate(target, { from: currentPath }); });
      jumps.append(jump);
    }
  }

  function renderDirs(dirs, revealPath) {
    list.replaceChildren();
    emptyNote.hidden = dirs.length > 0;
    const reveal = revealPath === null || revealPath === undefined ? null : revealPath;
    let revealed = null;
    for (const dir of dirs) {
      const item = el("li", "picker-item");
      const entry = button("btn-link picker-dir");
      entry.title = t("picker.enterTitle", { name: dir.name });
      entry.append(el("span", "li-name", dir.name));
      if (reveal !== null && samePath(dir.path, reveal)) {
        entry.classList.add("is-revealed");
        revealed = entry;
      }
      entry.addEventListener("click", () => { void navigate(dir.path, { from: currentPath }); });
      item.append(entry);
      list.append(item);
    }
    return revealed;
  }

  function render(payload, from) {
    currentPath = String(payload.path || "");
    parentPath = payload.parent ? String(payload.parent) : null;
    pathInput.value = currentPath;
    upButton.disabled = parentPath === null;
    useButton.disabled = currentPath.length === 0;
    renderJumps(payload.roots, payload.home);
    // A step up (or a jump to an ancestor) shows the directory it came out of;
    // any other step falls back to the position this directory was left at.
    const reveal = from ? childOnPath(currentPath, from) : null;
    const revealed = renderDirs(Array.isArray(payload.dirs) ? payload.dirs : [], reveal);
    if (revealed) revealRow(list, revealed);
    else setScrollTop(scrollMemory.recall(currentPath));
  }

  function setScrollTop(top) {
    if (top === null || top === undefined) return;
    list.scrollTop = Math.max(0, Number(top) || 0);
  }

  async function navigate(path, options) {
    const mine = ++token;
    busy = true;
    list.classList.add("is-busy");
    if (currentPath) scrollMemory.remember(currentPath, list.scrollTop);
    const from = options && options.from ? String(options.from) : null;
    try {
      const payload = await api.pick(path);
      if (mine !== token) return;
      render(payload || {}, from);
    } catch (err) {
      if (mine !== token) return;
      notify(t("picker.loadFailed", { message: describeError(err) }), "error");
    } finally {
      if (mine === token) {
        busy = false;
        list.classList.remove("is-busy");
      }
    }
  }

  /** Manual route: go to whatever absolute path is typed in the field. */
  async function goToTyped() {
    const wanted = pathInput.value.trim();
    if (!wanted || wanted === currentPath) return;
    await navigate(wanted, { from: currentPath });
  }

  /** Create one sub directory under the displayed directory, then enter it. */
  async function createDir() {
    const name = newNameInput.value.trim();
    if (!name || !currentPath) return;
    const mine = ++token;
    busy = true;
    list.classList.add("is-busy");
    try {
      const payload = await api.pickMkdir(currentPath, name);
      if (mine !== token) return;
      newNameInput.value = "";
      const created = payload && payload.path ? String(payload.path) : "";
      if (created) await navigate(created, { from: currentPath });
    } catch (err) {
      if (mine !== token) return;
      notify(t("picker.createFailed", { message: describeError(err) }), "error");
    } finally {
      if (mine === token) {
        busy = false;
        list.classList.remove("is-busy");
      }
    }
  }

  /**
   * "Use this directory" resolves what the field shows: a path typed but not
   * yet navigated to must still be honoured (that is the whole point of the
   * manual route), but a failed navigation must not silently use the old one.
   */
  async function useCurrent() {
    const wanted = pathInput.value.trim();
    if (wanted && wanted !== currentPath) {
      await navigate(wanted, { from: currentPath });
      if (pathInput.value.trim() !== currentPath) return;
    }
    if (currentPath) settle(currentPath);
  }

  backdrop.addEventListener("click", (event) => { if (event.target === backdrop) settle(null); });
  closeButton.addEventListener("click", () => { settle(null); });
  cancelButton.addEventListener("click", () => { settle(null); });
  upButton.addEventListener("click", () => { if (parentPath) void navigate(parentPath, { from: currentPath }); });
  useButton.addEventListener("click", () => { void useCurrent(); });
  goButton.addEventListener("click", () => { void goToTyped(); });
  newDirButton.addEventListener("click", () => { void createDir(); });
  pathInput.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    if (event.stopPropagation) event.stopPropagation();
    void goToTyped();
  });
  newNameInput.addEventListener("keydown", (event) => {
    if (event.key !== "Enter") return;
    event.preventDefault();
    if (event.stopPropagation) event.stopPropagation();
    void createDir();
  });

  return {
    open(startPath) {
      if (pending) settle(null);
      restoreFocusTo = document.activeElement || null;
      backdrop.hidden = false;
      newNameInput.value = "";
      // Focus must move into the dialog, or nothing below the backdrop is
      // reachable and the key handler above never sees a target inside it.
      pathInput.focus();
      document.addEventListener("keydown", onKeyDown);
      const promise = new Promise((resolve) => { pending = resolve; });
      void navigate(startPath || "");
      return promise;
    },
    close() {
      if (pending) settle(null);
    },
  };
}
