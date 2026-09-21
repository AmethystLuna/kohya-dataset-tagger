/**
 * The image-scope control shared by the two panels that write captions.
 *
 * Both panels answer the same question before they touch anything - "which images?" - and they used to
 * answer it differently: the tagger offered a choice with live counts, while the batch panel inherited
 * whatever the filter happened to leave on screen and printed a bare number somewhere below its fields.
 * One question, two truths, and the batch one was invisible from the panel that acted on it.
 *
 * The four scopes are the tagger's, unchanged: the picked images, the open directory, that directory
 * including its subdirectories, and what the filter left visible. Two rules the counts have to keep:
 *
 *  - a count is **live** - the gallery probes captions in chunks and the filter changes what is visible,
 *    so the label is repainted from the providers rather than remembered;
 *  - the recursive scope shows "…" until its listing has loaded, **never a fabricated 0**, because a 0
 *    reads as "there is nothing below this directory".
 *
 * The only asynchronous provider is the recursive one (a second listing request); it is cached per
 * directory, so switching back and forth does not refetch. A cached listing is an answer about
 * **one** directory though, and it stops being one the moment the directory on screen is re-listed:
 * the files may have changed (our own crop writes archives beside their source, the trainer or a
 * hand edit can add and remove images). So the cache is only used while its directory is still the
 * current one, and `invalidate()` - called by app.js when a fresh listing arrives - drops it and
 * reloads it if that scope is the selected one.
 *
 * **One control per column, not per panel.** The value belongs to whoever acts on it, and both write
 * panels act on the same images, so app.js builds the single control (the strip pinned above the tab
 * panels) and hands the *instance* to both panels. A second control - even over a second select - is
 * two answers to one question again. Panels attach their reactions with subscribe(): handlers is a
 * list, because two panels have to be able to hook the same control without one replacing the other.
 */
import { t } from "./strings.js";

export const SCOPE = Object.freeze({
  SELECTED: "selected",
  DIR: "dir",
  DIR_RECURSIVE: "dir_recursive",
  FILTERED: "filtered",
});

//: One key per scope. The label carries the count, so the same four keys serve both panels.
export const SCOPE_LABEL_KEYS = Object.freeze({
  selected: "scope.selected",
  dir: "scope.dir",
  dir_recursive: "scope.dirRecursive",
  filtered: "scope.filtered",
});

/**
 * Fill `select` with the four scopes and keep them counted.
 *
 * `providers` are plain functions returning path arrays - both panels get the *same* object from app.js,
 * which is the point: a second copy of these is exactly how the two answers drifted apart.
 * `loadRecursive` is the async one and `getDirKey` is what tells its cache that the directory changed.
 */
export function createScopeControl(options) {
  const config = options || {};
  const select = config.select;
  const providers = config.providers || {};
  const getDirKey = config.getDirKey || (() => null);
  const loadRecursive = config.loadRecursive || null;
  //: One entry per subscriber; both panels of the column subscribe, and the hooks must not
  //: overwrite each other (the single onPaint/onChange options could only ever hold one panel's).
  const listeners = [];
  if (config.onPaint || config.onChange) {
    listeners.push({ onPaint: config.onPaint, onChange: config.onChange });
  }
  //: {dir, paths} of the "with subdirectories" listing; paths === null means "not loaded yet".
  let recursive = { dir: null, paths: null };

  /**
   * The cached listing - but only when it is an answer for the directory on screen.
   *
   * It used to be trusted whenever it was non-empty, whatever directory it came from: after
   * navigating, "directory + subdirectories" kept reporting the images of the directory the user
   * had just left, and every panel acting on the scope (tagger, batch, crop, export) acted on
   * those. A listing answers a question about one directory, so the directory is part of the
   * question - and "loaded for another directory" reads as **unknown** ("…"), never as 0.
   */
  function cachedRecursive() {
    return recursive.paths && recursive.dir === getDirKey() ? recursive.paths : null;
  }

  /** Let one panel react to this control. The panels live as long as the page does, so there is
   *  nothing to unsubscribe: this is a list of hooks, not an event bus. */
  function subscribe(handlers) {
    listeners.push(handlers || {});
  }

  function notify(name) {
    for (const entry of listeners.slice()) {
      if (typeof entry[name] === "function") entry[name]();
    }
  }

  for (const value of Object.values(SCOPE)) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = t(SCOPE_LABEL_KEYS[value], { n: 0 });
    select.append(option);
  }
  select.value = config.defaultScope || SCOPE.SELECTED;

  function pathsOf(provider) {
    return typeof provider === "function" ? provider() || [] : [];
  }

  function value() {
    return select.value || SCOPE.SELECTED;
  }

  function paths() {
    if (value() === SCOPE.DIR) return pathsOf(providers.getDirPaths);
    // Nothing rather than another directory's images: an action that writes (the crop, the
    // caption batch) must never run over the tree the user has just left.
    if (value() === SCOPE.DIR_RECURSIVE) return cachedRecursive() || [];

    if (value() === SCOPE.FILTERED) return pathsOf(providers.getVisiblePaths);
    return pathsOf(providers.getSelectedPaths);
  }

  function counts() {
    const listed = cachedRecursive();
    return {
      selected: pathsOf(providers.getSelectedPaths).length,
      dir: pathsOf(providers.getDirPaths).length,
      dir_recursive: listed ? listed.length : null,
      filtered: pathsOf(providers.getVisiblePaths).length,
    };
  }

  function paint() {
    const now = counts();
    for (const option of Array.from(select.options)) {
      const n = now[option.value];
      option.textContent = t(SCOPE_LABEL_KEYS[option.value], { n: n === null ? "…" : (n || 0) });
    }
    notify("onPaint");
  }

  /** Load (or reuse) the "with subdirectories" listing. Only that scope ever asks for it. */
  async function ensureRecursive() {
    if (!loadRecursive) return;
    if (cachedRecursive()) return;
    const key = getDirKey();
    try {
      const loaded = await loadRecursive();
      const dir = loaded && loaded.dir !== undefined ? loaded.dir : key;
      recursive = { dir, paths: (loaded && loaded.paths) || [] };
    } catch (err) {
      recursive = { dir: key, paths: [] };
    }
    paint();
  }

  /**
   * The directory on screen was re-listed, so the listing under it is not the answer any more:
   * drop the cache, and reload right away when that is the selected scope (the label reads "…"
   * while the new listing is in flight, which is what "not known yet" looks like here).
   *
   * It is the caller's job because the control cannot see the filesystem. The one signal it gets
   * is "a fresh listing replaced the old one", and app.js owns that: `openDir()` is the only place
   * a listing is installed (navigation, the refresh action, the external-change poll, and the crop
   * panel's own post-run refresh all go through it), so one call there covers every writer.
   */
  function invalidate() {
    recursive = { dir: null, paths: null };
    if (value() === SCOPE.DIR_RECURSIVE) {
      paint();
      void ensureRecursive();
    }
  }

  /** Repaint the counts, and load the recursive listing when that is the selected scope. */
  function refresh() {
    paint();
    if (value() === SCOPE.DIR_RECURSIVE) void ensureRecursive();
  }

  select.addEventListener("change", () => {
    refresh();
    notify("onChange");
  });

  return { select, value, paths, counts, paint, refresh, ensureRecursive, invalidate, subscribe };
}
