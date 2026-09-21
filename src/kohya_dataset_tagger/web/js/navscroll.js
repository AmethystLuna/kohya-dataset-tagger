/**
 * Where a directory list's scroll position goes when you navigate.
 *
 * Every listing in this UI is rebuilt wholesale on navigation
 * (`replaceChildren`), which resets its scroll container to the top. Going up
 * one level therefore dropped the user at the top of the parent listing with no
 * trace of the folder they had just left - and with 200+ sibling folders that
 * means scrolling again to find your place. That is the complaint this module
 * answers.
 *
 * Four pieces, shared by every list that navigates directories (the left
 * column, the folder cards in the centre, the breadcrumb dropdown and the
 * picker dialog):
 *
 *   childOnPath(parent, child)  the one segment of `child` directly under
 *                               `parent` - "the folder we came out of"
 *   samePath(a, b)              separator- and case-insensitive path equality
 *   createScrollMemory()        per directory: where its list was left
 *   revealRow(scroller, row)    move one scroller so that one row is in view
 *
 * Importing this module touches no DOM, makes no request and carries no copy
 * (the static contract test forbids CJK outside the locale packs).
 */

const SEPARATORS = /[\\/]+/;

/** The last scroll memory size we keep: one entry per visited directory. */
const SCROLL_MEMORY_LIMIT = 200;

function asText(value) {
  return value === null || value === undefined ? "" : String(value);
}

function segments(path) {
  return asText(path)
    .split(SEPARATORS)
    .filter((part) => part.length > 0);
}

function withoutTrailingSeparators(path) {
  return asText(path).replace(/[\\/]+$/, "");
}

/** The separator `path` itself is written with, so a child keeps the same style. */
function separatorOf(path) {
  return asText(path).indexOf("\\") >= 0 ? "\\" : "/";
}

/**
 * Path equality for two paths that came from the server: separators and case are
 * ignored, because Windows returns either form for the same directory. Returns
 * false for two empty strings - "no path" is never the same directory.
 */
export function samePath(a, b) {
  const left = segments(a);
  const right = segments(b);
  if (left.length === 0 || left.length !== right.length) return false;
  for (let i = 0; i < left.length; i += 1) {
    if (left[i].toLowerCase() !== right[i].toLowerCase()) return false;
  }
  return true;
}

/**
 * The segment of `child` that sits directly under `parent` - the folder a
 * navigation up one (or more) levels came out of - or null when `child` is not
 * strictly below `parent` (a descent, a refresh, a sibling jump).
 *
 * The result is rebuilt from the `parent` string, so it keeps that string's own
 * separators and drive-letter case: the caller matches it against the paths in a
 * listing, and on Windows those must be byte-identical.
 */
export function childOnPath(parent, child) {
  const parentText = asText(parent);
  if (parentText.length === 0) return null;
  const parentSegments = segments(parentText);
  const childSegments = segments(child);
  if (childSegments.length <= parentSegments.length) return null;
  for (let i = 0; i < parentSegments.length; i += 1) {
    if (parentSegments[i].toLowerCase() !== childSegments[i].toLowerCase()) return null;
  }
  // The filesystem root ("/" - on Windows the drive root still has a segment):
  // its only possible child is the first segment.
  if (parentSegments.length === 0) return separatorOf(parentText) + childSegments[0];
  const base = withoutTrailingSeparators(parentText);
  return base + separatorOf(parentText) + childSegments[parentSegments.length];
}

/**
 * Remember where each directory's list was left, so a refresh or a jump back to
 * a directory does not move it. Bounded: the oldest entry is dropped once the
 * limit is reached, because a session can walk through hundreds of directories
 * and the map must not grow forever.
 *
 * Returns { remember(path, top), recall(path), clear(), size() }. recall() gives
 * null for an unknown directory - never 0, which is a real position.
 */
export function createScrollMemory(limit) {
  const max = Number.isFinite(limit) && limit > 0 ? Math.floor(limit) : SCROLL_MEMORY_LIMIT;
  const positions = new Map();

  return {
    remember(path, top) {
      const key = asText(path);
      if (key.length === 0) return false;
      // Re-inserting keeps the Map in least-recently-stored order for the trim.
      if (positions.has(key)) positions.delete(key);
      const value = Number(top);
      positions.set(key, Number.isFinite(value) && value > 0 ? value : 0);
      while (positions.size > max) {
        const oldest = positions.keys().next();
        if (oldest.done) break;
        positions.delete(oldest.value);
      }
      return true;
    },
    recall(path) {
      const key = asText(path);
      if (key.length === 0) return null;
      return positions.has(key) ? positions.get(key) : null;
    },
    clear() {
      positions.clear();
    },
    size() {
      return positions.size;
    },
  };
}

/**
 * Move `scroller` so that `row` is visible, without touching any other container:
 * `scrollIntoView` would also scroll the ancestors, including the page itself.
 *
 * Alignment: "center" (the default) puts the row in the middle of the visible
 * part, so the folders around it give context; "nearest" moves the scroller as
 * little as possible - an already visible row stays where it is, one above the
 * fold is aligned to the top edge and one below it to the bottom edge.
 *
 * Returns false when the geometry cannot be read (a detached row, a scroller
 * that is not laid out, a headless DOM): the caller then falls back to the
 * remembered position instead of pretending the row was revealed.
 */
export function revealRow(scroller, row, options) {
  if (!scroller || !row) return false;
  if (typeof scroller.getBoundingClientRect !== "function") return false;
  if (typeof row.getBoundingClientRect !== "function") return false;
  const view = scroller.getBoundingClientRect();
  const box = row.getBoundingClientRect();
  if (!(view.height > 0) || !(box.height > 0)) return false;

  const offset = box.top - view.top;
  const fits = offset >= 0 && offset + box.height <= view.height;
  const align = (options || {}).align;
  if (align === "nearest" && fits) return true;

  let top = scroller.scrollTop + offset - (view.height - box.height) / 2;
  if (align === "nearest") {
    top = offset < 0
      ? scroller.scrollTop + offset
      : scroller.scrollTop + offset - (view.height - box.height);
  }
  const scrollHeight = Number(scroller.scrollHeight);
  const clientHeight = Number(scroller.clientHeight);
  if (Number.isFinite(scrollHeight) && Number.isFinite(clientHeight)) {
    top = Math.min(top, Math.max(0, scrollHeight - clientHeight));
  }
  scroller.scrollTop = Math.max(0, top);
  return true;
}
