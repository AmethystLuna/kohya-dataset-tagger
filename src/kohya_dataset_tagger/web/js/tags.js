/**
 * Tag / caption editor: chips with inline edit, autocomplete, autosave and the
 * multiline caption warning.
 *
 * Contract: GET /api/captions?path=  ->  PUT /api/captions {image, tags|text}
 * The PUT response is WriteResult spread out, so cache_remove_failed has to be
 * surfaced instead of swallowed (first invariant of the repository).
 */

import { api } from "./api.js";
import { t } from "./strings.js";

const SUGGEST_LIMIT = 8;
const SUGGEST_DEBOUNCE_MS = 150;

function joinTags(list) {
  return list.join(", ");
}

function normalizeTag(raw) {
  return String(raw).replace(/\s+/g, " ").trim();
}

/**
 * Would leaving raw text mode delete text the user typed?
 *
 * The chip editor can only represent one line, so the raw buffer is re-parsed
 * from its first line - everything below is dropped, taking the multiline
 * warning with it (nothing on screen says "multiline" any more), after which the
 * autosave writes the truncation. Exported because "trailing empty lines are
 * not loss" is a decision worth testing on its own.
 */
export function wouldDropLines(text) {
  const lines = String(text).split("\n");
  return lines.slice(1).some((line) => line.trim().length > 0);
}

export function createTagEditor(options) {
  const chipsEl = options.chips;
  const inputEl = options.input;
  const suggestEl = options.suggest;
  const addButton = options.addButton;
  const saveButton = options.saveButton;
  const stateEl = options.state;
  const warningEl = options.warning;
  const captionFileEl = options.captionFile;
  const rawToggle = options.rawToggle;
  const rawTextEl = options.rawText;
  // The block of controls. Optional: an editor that is hosted in a panel of its
  // own passes it so that "no active image" can hide the form instead of showing
  // it disabled and empty, which is the one state where an empty form is
  // indistinguishable from a form that is about to work. The zoom overlay does
  // not pass it - it is modal, so it always has an image while it is visible.
  const formEl = options.form || null;
  const onSaved = options.onSaved || (() => {});
  const onError = options.onError || (() => {});
  const confirmFn = options.confirm || (() => true);
  const onNotify = options.onNotify || (() => {});

  const countEl = document.createElement("p");
  countEl.className = "hint";
  paintCountHint();
  chipsEl.insertAdjacentElement("afterend", countEl);

  let currentPath = null;
  let loadedText = "";
  let tags = [];
  let dirty = false;
  let saving = null;
  // "nothing to save" and "the write failed" must not look the same to a caller
  // that is about to navigate away from unsaved text.
  let lastSaveFailed = false;
  // Kept until the next successful write: the failure must stay on screen, not
  // flash for one frame before the dirty-state repaint replaces it.
  let saveError = null;
  // Caption path of the last render: undefined = nothing painted yet, null = no
  // caption file. Kept so relabel() can re-translate that line with no request.
  let captionPath;

  function inRawMode() {
    return rawToggle.checked;
  }

  function pendingText() {
    return inRawMode() ? rawTextEl.value : joinTags(tags);
  }

  function recomputeDirty() {
    dirty = currentPath !== null && pendingText() !== loadedText;
    paintState();
  }

  function paintState() {
    stateEl.classList.remove("is-dirty", "is-saved", "is-error");
    if (currentPath === null) { stateEl.textContent = ""; return; }
    if (saving) { stateEl.textContent = t("caption.saving"); return; }
    if (saveError) {
      stateEl.classList.add("is-error");
      stateEl.textContent = t("caption.saveFailed", { message: saveError.message });
      return;
    }
    if (dirty) { stateEl.classList.add("is-dirty"); stateEl.textContent = t("caption.dirty"); return; }
    stateEl.classList.add("is-saved");
    stateEl.textContent = t("caption.saved");
  }

  function paintCount() {
    if (currentPath === null) { countEl.textContent = ""; return; }
    countEl.textContent = t("caption.tagCount", { n: tags.length });
  }

  /** Hover hint of the tag counter; extracted so relabel() reuses the key. */
  function paintCountHint() {
    countEl.title = t("caption.editHint");
  }

  function paintWarning() {
    const text = inRawMode() ? rawTextEl.value : joinTags(tags);
    const multiline = text.indexOf("\n") >= 0;
    warningEl.hidden = !multiline;
  }

  /** The caption file line; rendered from captionPath so relabel() can repaint it. */
  function paintCaptionFile() {
    if (!captionFileEl) return;
    if (captionPath === undefined) { captionFileEl.textContent = ""; return; }
    captionFileEl.textContent = t("caption.captionPath", {
      path: captionPath || t("caption.noCaptionFile"),
    });
  }

  function makeChip(tag, index) {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.dataset.tag = tag;
    chip.dataset.index = String(index);

    const label = document.createElement("span");
    label.className = "chip-text";
    label.textContent = tag;

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "chip-remove";
    remove.textContent = "\u00d7";
    remove.title = t("caption.remove").concat(" ").concat(tag);
    // Content beats title in the accessible-name computation, so without this a
    // screen reader announces the button as "times".
    remove.setAttribute("aria-label", remove.title);

    chip.append(label, remove);
    return chip;
  }

  function paintChips() {
    chipsEl.replaceChildren();
    if (currentPath === null) return;
    if (tags.length === 0) {
      const empty = document.createElement("span");
      empty.className = "hint";
      empty.textContent = t("caption.noTags");
      chipsEl.append(empty);
      return;
    }
    tags.forEach((tag, index) => chipsEl.append(makeChip(tag, index)));
    paintCount();
    paintWarning();
  }

  function setTags(next, markDirty) {
    tags = next.slice();
    if (markDirty !== false) {
      paintChips();
      recomputeDirty();
    }
  }

  function addTag(raw) {
    const parts = String(raw).split(",").map(normalizeTag).filter((part) => part.length > 0);
    if (parts.length === 0) {
      onNotify(t("caption.emptyTag"), "warn");
      return false;
    }
    let changed = false;
    for (const part of parts) {
      if (tags.indexOf(part) >= 0) {
        onNotify(t("caption.duplicateTag"), "warn");
        continue;
      }
      tags.push(part);
      changed = true;
    }
    if (changed) {
      inputEl.value = "";
      paintChips();
      recomputeDirty();
    }
    return changed;
  }

  function removeTagAt(index) {
    if (index < 0 || index >= tags.length) return;
    tags.splice(index, 1);
    paintChips();
    recomputeDirty();
  }

  function beginEdit(chip) {
    const index = Number(chip.dataset.index);
    if (!Number.isFinite(index)) return;
    const original = tags[index];
    const edit = document.createElement("input");
    edit.type = "text";
    edit.value = original;
    edit.spellcheck = false;
    chip.classList.add("is-editing");
    chip.replaceChildren(edit);
    edit.focus();
    edit.select();
    let done = false;
    const commit = (accept) => {
      if (done) return;
      done = true;
      if (accept) {
        const value = normalizeTag(edit.value);
        if (value.length === 0) {
          onNotify(t("caption.emptyTag"), "warn");
        } else if (value !== original && tags.indexOf(value) >= 0) {
          onNotify(t("caption.duplicateTag"), "warn");
        } else {
          tags[index] = value;
        }
      }
      paintChips();
      recomputeDirty();
    };
    edit.addEventListener("keydown", (event) => {
      if (event.key === "Enter") { event.preventDefault(); commit(true); }
      else if (event.key === "Escape") { event.preventDefault(); commit(false); }
    });
    edit.addEventListener("blur", () => commit(true));
  }

  chipsEl.addEventListener("click", (event) => {
    const remove = event.target.closest(".chip-remove");
    if (remove) {
      const chip = remove.closest(".chip");
      removeTagAt(Number(chip.dataset.index));
      return;
    }
  });

  chipsEl.addEventListener("dblclick", (event) => {
    const chip = event.target.closest(".chip");
    if (chip) beginEdit(chip);
  });

  // ---- autocomplete ------------------------------------------------------
  let suggestTimer = null;
  let suggestIndex = -1;
  let suggestItems = [];

  function closeSuggest() {
    suggestEl.hidden = true;
    suggestEl.replaceChildren();
    suggestItems = [];
    suggestIndex = -1;
  }

  function paintSuggest() {
    suggestEl.replaceChildren();
    suggestItems.forEach((item, index) => {
      const row = document.createElement("div");
      row.className = "suggest-item" + (index === suggestIndex ? " is-active" : "");
      const name = document.createElement("span");
      name.textContent = item.tag;
      const cat = document.createElement("span");
      cat.className = "s-cat";
      // Section 4.5: category is the human readable name ("General") and
      // category_id the numeric code ("0"); fall back to the id so an older
      // backend still renders something instead of an empty badge.
      const parts = [];
      if (item.category !== undefined && item.category !== null && item.category !== "") {
        parts.push(String(item.category));
      } else if (item.category_id !== undefined && item.category_id !== null) {
        parts.push(String(item.category_id));
      }
      if (item.count !== undefined && item.count !== null) parts.push(String(item.count));
      cat.textContent = parts.join(" \u00b7 ");
      row.append(name, cat);
      row.dataset.index = String(index);
      suggestEl.append(row);
    });
    suggestEl.hidden = suggestItems.length === 0;
  }

  function acceptSuggest(index) {
    const item = suggestItems[index];
    if (!item) return;
    addTag(item.tag);
    closeSuggest();
    inputEl.focus();
  }

  suggestEl.addEventListener("mousedown", (event) => {
    // keep focus in the input so the blur handler does not commit the query
    event.preventDefault();
    const row = event.target.closest(".suggest-item");
    if (row) acceptSuggest(Number(row.dataset.index));
  });

  async function refreshSuggest() {
    const query = inputEl.value.trim();
    if (!query || currentPath === null) { closeSuggest(); return; }
    try {
      const payload = await api.tagAutocomplete(query, SUGGEST_LIMIT);
      const list = (payload && payload.suggestions) || [];
      suggestItems = list.filter((item) => tags.indexOf(item.tag) < 0);
      suggestIndex = suggestItems.length > 0 ? 0 : -1;
      paintSuggest();
    } catch (err) {
      closeSuggest();
    }
  }

  inputEl.addEventListener("input", () => {
    if (suggestTimer) window.clearTimeout(suggestTimer);
    suggestTimer = window.setTimeout(refreshSuggest, SUGGEST_DEBOUNCE_MS);
  });

  inputEl.addEventListener("keydown", (event) => {
    if (!suggestEl.hidden && suggestItems.length > 0) {
      if (event.key === "ArrowDown") {
        suggestIndex = (suggestIndex + 1) % suggestItems.length;
        paintSuggest();
        event.preventDefault();
        return;
      }
      if (event.key === "ArrowUp") {
        suggestIndex = (suggestIndex - 1 + suggestItems.length) % suggestItems.length;
        paintSuggest();
        event.preventDefault();
        return;
      }
      if (event.key === "Tab") {
        acceptSuggest(suggestIndex);
        event.preventDefault();
        return;
      }
    }
    if (event.key === "Enter") {
      event.preventDefault();
      if (!suggestEl.hidden && suggestIndex >= 0) {
        acceptSuggest(suggestIndex);
        return;
      }
      addTag(inputEl.value);
      closeSuggest();
    } else if (event.key === "Escape") {
      closeSuggest();
    }
  });

  // Autosave on blur: the requirement is blur / image switch, not a timer.
  inputEl.addEventListener("blur", () => {
    if (inputEl.value.trim().length > 0) addTag(inputEl.value);
    closeSuggest();
    if (dirty) void save();
  });

  chipsEl.addEventListener("focusout", () => {
    window.setTimeout(() => {
      if (!chipsEl.contains(document.activeElement) && dirty) void save();
    }, 0);
  });

  if (addButton) {
    addButton.addEventListener("click", () => {
      addTag(inputEl.value);
      inputEl.focus();
    });
  }

  rawToggle.addEventListener("change", async () => {
    if (inRawMode()) {
      rawTextEl.value = joinTags(tags);
      rawTextEl.hidden = false;
      chipsEl.hidden = true;
      countEl.hidden = true;
    } else {
      const drop = wouldDropLines(rawTextEl.value)
        && !(await confirmFn({
          body: t("caption.dropLinesConfirm"),
          confirmLabel: t("caption.dropLinesOk"),
          danger: true,
        }));
      if (drop) {
        rawToggle.checked = true;
        return;
      }
      const parsed = rawTextEl.value.split("\n")[0].split(",").map(normalizeTag).filter((part) => part.length > 0);
      tags = parsed;
      rawTextEl.hidden = true;
      chipsEl.hidden = false;
      countEl.hidden = false;
      paintChips();
    }
    recomputeDirty();
    paintWarning();
  });

  rawTextEl.addEventListener("input", () => {
    recomputeDirty();
    paintWarning();
  });
  rawTextEl.addEventListener("blur", () => { if (dirty) void save(); });

  // ---- save --------------------------------------------------------------
  function handleWriteResult(result) {
    if (!result) return;
    if (result.caption_path && captionFileEl) {
      captionPath = result.caption_path;
      paintCaptionFile();
    }
    if (result.multiline_warning) {
      warningEl.hidden = false;
      onNotify(t("caption.multilineAfterSave"), "warn");
    }
    if (Array.isArray(result.cache_remove_failed) && result.cache_remove_failed.length > 0) {
      onError(t("caption.removeCacheFailed", { n: result.cache_remove_failed.length }), result);
    } else if (Array.isArray(result.cache_removed) && result.cache_removed.length > 0) {
      onNotify(t("caption.removedCaches", { n: result.cache_removed.length }), "info");
    }
    onSaved(result);
  }

  async function save() {
    if (currentPath === null || !dirty) return null;
    if (saving) return saving;
    const path = currentPath;
    const payload = inRawMode()
      ? { image: path, text: rawTextEl.value }
      : { image: path, tags: tags.slice() };
    const sentText = pendingText();
    saving = (async () => {
      paintState();
      try {
        const result = await api.putCaption(payload);
        lastSaveFailed = false;
        saveError = null;
        if (path === currentPath) {
          loadedText = sentText;
          // re-derive instead of forcing dirty=false: the user may have typed
          // while the request was in flight, and that change is still unsaved
          recomputeDirty();
          paintState();
        }
        handleWriteResult(result);
        return result;
      } catch (err) {
        lastSaveFailed = true;
        if (path === currentPath) {
          saveError = err;
          paintState();
        }
        onError(t("caption.saveFailed", { message: err.message }), err);
        throw err;
      } finally {
        saving = null;
      }
    })();
    const pending = saving;
    try {
      await pending;
    } catch (err) {
      // already reported
    }
    if (path === currentPath) paintState();
    return pending.catch(() => null);
  }

  async function load(path) {
    currentPath = null;
    tags = [];
    loadedText = "";
    dirty = false;
    saveError = null;
    closeSuggest();
    chipsEl.replaceChildren();
    countEl.textContent = "";
    warningEl.hidden = true;
    captionPath = undefined;
    paintCaptionFile();
    stateEl.textContent = "";
    if (!path) {
      disable();
      return null;
    }
    enable();
    const payload = await api.getCaption(path);
    currentPath = path;
    tags = Array.isArray(payload.tags) ? payload.tags.slice() : [];
    loadedText = typeof payload.text === "string" ? payload.text : joinTags(tags);
    if (inRawMode()) rawTextEl.value = loadedText;
    else rawTextEl.value = joinTags(tags);
    dirty = false;
    if (captionFileEl) {
      captionPath = payload.caption_path || null;
      paintCaptionFile();
    }
    warningEl.hidden = !payload.multiline_warning && loadedText.indexOf("\n") < 0;
    paintChips();
    paintCount();
    paintState();
    paintWarning();
    return payload;
  }

  function enable() {
    if (formEl) formEl.hidden = false;
    inputEl.disabled = false;
    rawToggle.disabled = false;
    rawTextEl.disabled = false;
    if (saveButton) saveButton.disabled = false;
    if (addButton) addButton.disabled = false;
  }

  /**
   * No image to edit: the controls go away rather than sit there disabled, and
   * the host panel's status line (index.html #active-meta) is left as the empty
   * state. Relabel-safe: disable() only repaints this from state in hand.
   */
  function disable() {
    if (formEl) formEl.hidden = true;
    inputEl.disabled = true;
    rawToggle.disabled = true;
    rawTextEl.disabled = true;
    if (saveButton) saveButton.disabled = true;
    if (addButton) addButton.disabled = true;
    stateEl.textContent = t("caption.noActiveImage");
  }

  if (saveButton) {
    saveButton.addEventListener("click", () => { void save(); });
  }

  /**
   * Re-apply the current locale to every string this module rendered from t().
   * Repaints from state already in hand: no request, no write, and safe before
   * the first load (the editor is disabled, so that is what gets repainted).
   */
  function relabel() {
    paintCountHint();
    paintCaptionFile();
    if (inputEl.disabled) {
      // already presenting the disabled state; disable() only repaints it
      disable();
      return;
    }
    paintChips();
    paintState();
    paintWarning();
  }

  disable();

  return {
    load,
    save,
    // "clean" (nothing to write), "saved", or "failed" - activateImage refuses to
    // reload the editor over unsaved text, so it needs the third state.
    flush: async () => {
      if (currentPath === null || !dirty) return "clean";
      const result = await save();
      if (result !== null) return "saved";
      return lastSaveFailed ? "failed" : "clean";
    },
    isDirty: () => dirty,
    getTags: () => tags.slice(),
    getPath: () => currentPath,
    disable,
    enable,
    relabel,
  };
}