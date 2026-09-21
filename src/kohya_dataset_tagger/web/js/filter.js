/**
 * Tag filter: tri-state tags (only / include / exclude), a tag search box, a
 * sort switch, an optional "selection only" filter and the tag frequency table
 * from GET /api/tags/frequency.
 *
 * Semantics (documented in the UI via filter.semantics):
 *   only    - image must contain at least one of the "only" tags (OR group)
 *   include - image must contain every "include" tag (AND group)
 *   exclude - image must contain none of the "exclude" tags
 * Clicking a tag cycles: only -> include -> exclude -> off.
 *
 * The frequency table is a *navigator* over 1500+ tags, so the search box is the
 * primary affordance: rows are filtered by substring BEFORE the row cap, or a
 * rare tag could never be reached (the cap would hide it).
 *
 * Three filter layers, deliberately separate:
 *   1. tag tri-state  -> which images match (apply())
 *   2. selection only -> intersection with the gallery selection
 *   3. visible scope  -> whether the frequency table counts every image in the
 *      directory or only the images currently on screen (a display choice, not
 *      an image filter - it must not change apply()).
 *
 * The pure helpers (matchesFilter / countTags / selectTagRows / cycleState) are
 * exported so the headless harness can test the filtering logic without a DOM.
 */

import { t } from "./strings.js";

export const FILTER_STATE = Object.freeze({
  ONLY: "only",
  INCLUDE: "include",
  EXCLUDE: "exclude",
});

export const FILTER_SORT = Object.freeze({ COUNT: "count", NAME: "name" });

const CYCLE = [FILTER_STATE.ONLY, FILTER_STATE.INCLUDE, FILTER_STATE.EXCLUDE];

const STATE_CLASS = {
  only: "chip-state-only",
  include: "chip-state-include",
  exclude: "chip-state-exclude",
};

const STATE_LABEL_KEY = {
  only: "filter.only",
  include: "filter.include",
  exclude: "filter.exclude",
};

const STATE_LABEL_CLASS = {
  only: "chip-label-only",
  include: "chip-label-include",
  exclude: "chip-label-exclude",
};

export const MAX_FREQUENCY_ROWS = 400;

/** Clicking a tag moves it one step round the tri-state cycle, then off. */
export function cycleState(current) {
  if (current === undefined || current === null) return CYCLE[0];
  const index = CYCLE.indexOf(current);
  if (index < 0 || index === CYCLE.length - 1) return null;
  return CYCLE[index + 1];
}

/**
 * Tri-state predicate. `state` is {only:[], include:[], exclude:[]}; the only
 * group is an OR, the include group an AND, exclude wins over both.
 */
export function matchesFilter(state, tags) {
  const present = new Set(Array.isArray(tags) ? tags : []);
  const exclude = (state && state.exclude) || [];
  for (const tag of exclude) {
    if (present.has(tag)) return false;
  }
  const include = (state && state.include) || [];
  for (const tag of include) {
    if (!present.has(tag)) return false;
  }
  const only = (state && state.only) || [];
  if (only.length > 0 && !only.some((tag) => present.has(tag))) return false;
  return true;
}

/**
 * Frequency rows from per image tag lists -> [{tag, count}], unsorted.
 * Used to scope the table to the images currently on screen.
 */
export function countTags(tagLists) {
  const counter = new Map();
  for (const list of Array.isArray(tagLists) ? tagLists : []) {
    if (!Array.isArray(list)) continue;
    for (const tag of list) {
      if (!tag) continue;
      counter.set(tag, (counter.get(tag) || 0) + 1);
    }
  }
  return Array.from(counter, ([tag, count]) => ({ tag, count }));
}

/**
 * Filter rows by a case-insensitive substring, then sort them.
 *   COUNT -> most frequent first, ties by name; NAME -> case-insensitive A-Z.
 * The search runs first on purpose: the caller caps the row count afterwards,
 * so a rare tag stays reachable while the list is narrowed.
 */
export function selectTagRows(rows, query, sort) {
  const needle = String(query || "").trim().toLowerCase();
  const list = (Array.isArray(rows) ? rows : []).filter(
    (row) => !needle || String(row.tag).toLowerCase().indexOf(needle) >= 0
  );
  const byName = (a, b) => {
    const left = String(a.tag).toLowerCase();
    const right = String(b.tag).toLowerCase();
    if (left < right) return -1;
    if (left > right) return 1;
    return String(a.tag) < String(b.tag) ? -1 : (String(a.tag) > String(b.tag) ? 1 : 0);
  };
  if (sort === FILTER_SORT.NAME) return list.slice().sort(byName);
  return list.slice().sort((a, b) => (b.count || 0) - (a.count || 0) || byName(a, b));
}

export function createFilterPanel(options) {
  const listEl = options.list;
  //: The chips, in the center panel's strip next to the grid they narrow - not on the
  //: Filter tab, which only chooses conditions. One host, painted by one painter.
  const activeEl = options.active;
  const countEl = options.count;
  //: The strip's host, hidden while nothing narrows the grid (see paintActive).
  const stripEl = options.strip;
  const scopeEl = options.scope;
  const emptyEl = options.empty;
  const searchEl = options.search;
  const sortEl = options.sort;
  const selectionEl = options.selection;
  const visibleScopeEl = options.visibleScope;
  const isSelected = options.isSelected || (() => false);
  const isMissing = options.isMissing || (() => false);
  const onFilterChange = options.onFilterChange || (() => {});

  const state = new Map();
  let frequency = { total_images: 0, tags: [] };
  //: Set when a load failed; cleared by the next successful setFrequency().
  let frequencyError = "";
  let scopeLabel = "";
  let summary = { visible: 0, total: 0, pending: 0 };
  let search = "";
  let sortMode = FILTER_SORT.COUNT;
  let selectionOnly = false;
  let missingOnly = false;
  let visibleScope = false;
  //: paths + tags of the last apply(): the base for a visible-scoped table.
  let lastVisible = [];
  let lastTags = new Map();

  function entries(states) {
    return Array.from(state.entries())
      .filter(([, value]) => value === states)
      .map(([tag]) => tag);
  }

  function hasTagConditions(conditions) {
    return (conditions.only.length + conditions.include.length + conditions.exclude.length) > 0;
  }

  function filterState() {
    return { only: entries(FILTER_STATE.ONLY), include: entries(FILTER_STATE.INCLUDE), exclude: entries(FILTER_STATE.EXCLUDE) };
  }

  function hasFilter() {
    return state.size > 0 || selectionOnly || missingOnly;
  }

  function matches(tags) {
    return matchesFilter(filterState(), tags);
  }

  /**
   * Filter a list of paths. getTags(path) returns the tag array, or null while
   * the caption probe for that image is still in flight - unknown images are
   * kept so a slow probe never hides a tile.
   */
  function apply(paths, getTags) {
    const visible = [];
    const tagsByPath = new Map();
    const conditions = filterState();
    let pending = 0;
    for (const path of paths) {
      if (selectionOnly && !isSelected(path)) continue;
      if (missingOnly && !isMissing(path)) continue;
      const tags = getTags(path);
      tagsByPath.set(path, tags);
      if (tags === null || tags === undefined) {
        pending += 1;
        visible.push(path);
        continue;
      }
      //: An image with no caption at all is outside the tag filter's domain: a
      //: "does not contain X" view must not list images that have no tags.
      if (Array.isArray(tags) && tags.length === 0 && hasTagConditions(conditions)) continue;
      if (matchesFilter(conditions, tags)) visible.push(path);
    }
    lastVisible = visible;
    lastTags = tagsByPath;
    summary = { visible: visible.length, total: paths.length, pending };
    paintCount();
    if (visibleScope) paintFrequency();
    return visible;
  }

  /** How much of the directory survived the conditions. The conditions themselves are named by their chips. */
  function paintCount() {
    if (summary.pending > 0) {
      countEl.textContent = t("filter.countPending", { n: summary.visible, total: summary.total });
    } else {
      countEl.textContent = t("filter.count", { n: summary.visible, total: summary.total });
    }
  }

  /** The sort <option> labels are built once, so relabel() has to revisit them. */
  function paintSortOptions() {
    if (!sortEl) return;
    for (const option of sortEl.options) {
      option.textContent = t(option.value === FILTER_SORT.NAME ? "filter.sortName" : "filter.sortCount");
    }
  }

  /**
   * Every condition that narrows the grid as one removable chip - and the strip that
   * carries them is on screen exactly while one of them is, next to the grid it narrows.
   *
   * The tag tri-state, the missing-caption mode and "show selected images only" all
   * narrow the grid, so all three are shown and undone here; a condition the strip
   * cannot show would be a grid that cannot say why it is short. Removing a chip drops
   * that one condition (removing the mode chips leaves the mode).
   */
  function paintActive() {
    const conditions = [];
    for (const [tag, value] of state) conditions.push({ kind: "tag", tag, state: value });
    if (missingOnly) conditions.push({ kind: "missing", label: t("filter.modeMissing") });
    if (selectionOnly) conditions.push({ kind: "selection", label: t("filter.onlySelected") });
    if (stripEl) stripEl.hidden = conditions.length === 0;
    activeEl.replaceChildren();
    for (const condition of conditions) {
      const chip = document.createElement("span");
      chip.className = condition.state ? "chip " + STATE_CLASS[condition.state] : "chip";
      chip.dataset.condition = condition.kind;
      if (condition.kind === "tag") {
        chip.dataset.tag = condition.tag;
        const label = document.createElement("span");
        label.textContent = t(STATE_LABEL_KEY[condition.state]);
        const name = document.createElement("span");
        name.textContent = condition.tag;
        name.className = STATE_LABEL_CLASS[condition.state];
        chip.append(label, name);
      } else {
        const name = document.createElement("span");
        name.textContent = condition.label;
        chip.append(name);
      }
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "chip-remove";
      remove.textContent = "\u00d7";
      remove.title = t("common.cancel");
      // "×" is the accessible name otherwise; say which condition is being cleared.
      const removed = condition.kind === "tag" ? condition.tag : condition.label;
      remove.setAttribute("aria-label", t("caption.remove").concat(" ").concat(removed));
      chip.append(remove);
      activeEl.append(chip);
    }
  }

  /** The table either counts the whole directory or only the visible images. */
  function currentFrequency() {
    if (!visibleScope) return frequency;
    const lists = [];
    for (const path of lastVisible) {
      const tags = lastTags.get(path);
      if (Array.isArray(tags)) lists.push(tags);
    }
    return { total_images: lists.length, tags: countTags(lists) };
  }

  /**
   * A failed load is not "no data yet": the previous directory's table must go,
   * and the note has to say what happened rather than invite the user to open a
   * directory they have already opened.
   */
  function setFrequencyFailed(message) {
    frequencyError = String(message || "");
    frequency = { total_images: 0, tags: [] };
    lastVisible = [];
    lastTags = new Map();
    paintFrequency();
  }

  function paintFrequency() {
    listEl.replaceChildren();
    if (frequencyError) {
      emptyEl.hidden = false;
      emptyEl.textContent = frequencyError;
      scopeEl.textContent = "";
      return;
    }
    const source = currentFrequency();
    const all = Array.isArray(source.tags) ? source.tags : [];
    const rows = selectTagRows(all, search, sortMode);
    if (rows.length === 0) {
      emptyEl.hidden = false;
      emptyEl.textContent = search.trim().length > 0 ? t("filter.searchEmpty") : t("filter.frequencyEmpty");
      scopeEl.textContent = "";
      return;
    }
    emptyEl.hidden = true;
    const frag = document.createDocumentFragment();
    for (const item of rows.slice(0, MAX_FREQUENCY_ROWS)) {
      const value = state.get(item.tag);
      // A real <button>: the tri-state cycle is the only way to narrow the set,
      // and a span with a click delegate is unreachable from the keyboard.
      const row = document.createElement("button");
      row.type = "button";
      row.className = "freq-item" + (value ? " " + STATE_CLASS[value] : "");
      row.dataset.tag = item.tag;
      row.title = t("filter.cycleHint");
      const name = document.createElement("span");
      name.textContent = item.tag;
      const count = document.createElement("span");
      count.className = "f-count";
      count.textContent = String(item.count);
      // On screen the state is a colour (and a strikethrough for exclude); a
      // screen reader needs it as words.
      const spoken = document.createElement("span");
      spoken.className = "sr-only";
      spoken.textContent = value ? t(STATE_LABEL_KEY[value]) : t("filter.stateOff");
      row.append(name, count, spoken);
      frag.append(row);
    }
    listEl.append(frag);
    scopeEl.textContent = t("filter.frequencyScope", {
      scope: scopeLabel,
      n: source.total_images || 0,
      tags: all.length,
    });
  }

  function cycle(tag) {
    const next = cycleState(state.get(tag));
    if (next === null) state.delete(tag);
    else state.set(tag, next); missingOnly = false;
    paintActive();
    paintFrequency();
    onFilterChange(getState());
  }

  if (searchEl) {
    searchEl.addEventListener("input", () => {
      search = searchEl.value;
      paintFrequency();
    });
  }

  if (sortEl) {
    for (const mode of [FILTER_SORT.COUNT, FILTER_SORT.NAME]) {
      const option = document.createElement("option");
      option.value = mode;
      sortEl.append(option);
    }
    paintSortOptions();
    sortEl.value = sortMode;
    sortEl.addEventListener("change", () => {
      sortMode = sortEl.value === FILTER_SORT.NAME ? FILTER_SORT.NAME : FILTER_SORT.COUNT;
      paintFrequency();
    });
  }

  if (selectionEl) {
    selectionEl.addEventListener("change", () => {
      selectionOnly = selectionEl.checked === true;
      // It is one of the conditions in the strip, so the strip follows the checkbox.
      paintActive();
      onFilterChange(getState());
    });
  }

  if (visibleScopeEl) {
    visibleScopeEl.addEventListener("change", () => {
      visibleScope = visibleScopeEl.checked === true;
      // Not an image filter, but turning it on may need every caption probed.
      onFilterChange(getState());
    });
  }

  listEl.addEventListener("click", (event) => {
    const row = event.target.closest(".freq-item");
    if (row) cycle(row.dataset.tag);
  });

  /**
   * Drop the one condition a chip stands for; every other condition stays. Removing
   * the missing-caption chip leaves that mode, and removing the selection chip clears
   * the checkbox that turned it on - otherwise the panel would contradict the control
   * it obeys.
   */
  function removeCondition(kind, tag) {
    if (kind === "missing") missingOnly = false;
    else if (kind === "selection") {
      selectionOnly = false;
      if (selectionEl) selectionEl.checked = false;
    } else state.delete(tag);
  }

  activeEl.addEventListener("click", (event) => {
    const remove = event.target.closest(".chip-remove");
    if (!remove) return;
    const chip = remove.closest(".chip");
    if (!chip) return;
    removeCondition(chip.dataset.condition, chip.dataset.tag);
    paintActive();
    paintFrequency();
    onFilterChange(getState());
  });

  function clearTags() {
    missingOnly = false;
    state.clear();
    paintActive();
    paintFrequency();
    onFilterChange(getState());
  }

  /** Clear every image filter: tri-state tags, tag search and selection only. */
  function clearAll() {
    missingOnly = false;
    state.clear();
    search = "";
    if (searchEl) searchEl.value = "";
    selectionOnly = false;
    if (selectionEl) selectionEl.checked = false;
    paintActive();
    paintFrequency();
    onFilterChange(getState());
  }

  /**
   * Re-apply the current locale to every string the panel paints dynamically.
   * All three paints read state the panel already holds, so this is safe before
   * any directory or frequency payload has arrived: it repaints the empty state
   * that is already on screen, and issues no request.
   */
  function relabel() {
    paintActive();
    paintCount();
    paintFrequency();
    paintSortOptions();
  }

  function getState() {
    const out = { only: [], include: [], exclude: [] };
    for (const [tag, value] of state) out[value].push(tag);
    out.search = search;
    out.sort = sortMode;
    out.selectionOnly = selectionOnly;
  out.missingOnly = missingOnly;
    out.visibleScope = visibleScope;
    return out;
  }

  function setFrequency(payload, label) {
    frequency = payload || { total_images: 0, tags: [] };
    scopeLabel = label || "";
    // A successful load is what clears a previous failure.
    frequencyError = "";
    paintFrequency();
  }

  function setSummary(next) {
    summary = next;
    paintCount();
  }

  paintActive();
  paintFrequency();

  return {
    apply,
    clearTags,
    clearAll,
    relabel,
    cycle,
    getState,
    hasFilter,
    hasSelectionFilter: () => selectionOnly,
    hasMissingFilter: () => missingOnly,
    //: Painted here, not by the caller: whoever sets the mode, the strip shows it.
    setMissingOnly: (value) => { missingOnly = value === true; paintActive(); },
    isVisibleScope: () => visibleScope,
    matches,
    setFrequency,
    setFrequencyFailed,
    setSummary,
    setScopeLabel: (label) => { scopeLabel = label; },
  };
}
