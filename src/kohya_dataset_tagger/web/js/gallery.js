/**
 * Gallery: thumbnail grid, multi selection (click / ctrl / shift / marquee),
 * keyboard navigation and caption-presence badges.
 *
 * Hard performance rules (decision #4 in the spec):
 *  - the gallery never requests the original image; every <img> src comes from
 *    thumbUrl(path, THUMB_SIZE.GRID) which is /api/images/thumb?size=grid;
 *  - at most THUMB_CONCURRENCY thumbnail requests are in flight at a time, so a
 *    219 entry directory can never open 219 sockets;
 *  - a tile the user has scrolled onto is served before the tiles that are only
 *    inside the prefetch margin, so the first screen's backlog never outranks
 *    what is actually on screen;
 *  - switching directories drops every img src and abandons queued requests;
 *  - since section 4.13 the same container also renders one card per sub
 *    directory (a 3x3 grid of that directory's own thumbnails) whenever the
 *    caller passes dirs in - the folder-only level of the dataset.
 */

import { thumbUrl, THUMB_SIZE } from "./api.js";
import { t } from "./strings.js";
import { samePath, revealRow } from "./navscroll.js";

export const THUMB_CONCURRENCY = 6;

const ROOT_MARGIN_PX = 400;
const MARQUEE_MIN_PX = 4;
const THUMB_PLACEHOLDER_PX = 320;

/**
 * Put one record where it belongs: whatever the user is looking at is served
 * before what is only near the viewport, and inside a band the requests keep
 * the order they arrived in.
 */
function rankRequest(queue, rec) {
  const index = queue.indexOf(rec);
  if (index >= 0) queue.splice(index, 1);
  if (!rec.onScreen) {
    queue.push(rec);
    return;
  }
  let target = 0;
  while (target < queue.length && queue[target].onScreen) target += 1;
  queue.splice(target, 0, rec);
}

/** Bounded-concurrency thumbnail loader. Abandoned generations are ignored. */
export class ThumbLoader {
  constructor(limit) {
    this.limit = limit || THUMB_CONCURRENCY;
    this.active = 0;
    this.queue = [];
    this.generation = 0;
  }

  reset() {
    this.generation += 1;
    for (const rec of this.queue) rec.img.removeAttribute("src");
    this.queue = [];
    this.active = 0;
  }

  /**
   * Queue one thumbnail. onScreen marks a tile the user is looking at right
   * now: it goes ahead of every tile that is merely inside the prefetch margin,
   * so a scroll after the first screen was queued does not wait behind
   * prefetches the user has already passed.
   */
  request(img, url, onScreen) {
    const rec = { img, url, generation: this.generation, onScreen: onScreen === true };
    rankRequest(this.queue, rec);
    this.pump();
  }

  /**
   * Re-rank a request that is still waiting. A request already in flight cannot
   * be reordered, so an image that is not waiting is a no-op (false).
   */
  prioritize(img, onScreen) {
    const rec = this.queue.find((item) => item.img === img);
    if (!rec) return false;
    rec.onScreen = onScreen === true;
    rankRequest(this.queue, rec);
    return true;
  }

  pump() {
    while (this.active < this.limit && this.queue.length > 0) {
      const rec = this.queue.shift();
      if (rec.generation !== this.generation) continue;
      const img = rec.img;
      this.active += 1;
      const done = () => {
        if (rec.generation !== this.generation) return;
        this.active = Math.max(0, this.active - 1);
        this.pump();
      };
      // A tile whose bitmap has not arrived must not look like an image with no
      // pixels: it wears the animated loading surface (.is-loading) until load
      // or error fires, and fades in once it has one.
      img.classList.remove("is-error", "is-loaded");
      img.classList.add("is-loading");
      img.addEventListener("load", () => {
        img.classList.remove("is-loading");
        img.classList.add("is-loaded");
        done();
      }, { once: true });
      img.addEventListener("error", () => {
        img.classList.remove("is-loading");
        img.classList.add("is-error");
        done();
      }, { once: true });
      img.src = rec.url;
    }
  }
}

/**
 * Paint one caption badge from a caption state. An unknown (or absent) state is
 * the default a freshly built tile starts in, so makeBadge reuses it too.
 */
function paintBadge(badge, state) {
  if (!state || state.exists === null || state.exists === undefined) {
    badge.className = "badge badge-unknown";
    badge.textContent = "?";
    badge.title = t("gallery.badgeUnknown");
    return;
  }
  if (state.exists) {
    badge.className = "badge badge-present";
    badge.textContent = "\u2713";
    badge.title = t("gallery.badgePresent");
  } else {
    badge.className = "badge badge-missing";
    badge.textContent = "!";
    badge.title = t("gallery.badgeMissing");
  }
}

function makeBadge() {
  const badge = document.createElement("span");
  paintBadge(badge, null);
  return badge;
}

function makeTile(item, index, onError) {
  const figure = document.createElement("figure");
  figure.className = "tile";
  figure.dataset.path = item.path;
  figure.dataset.index = String(index);

  const frame = document.createElement("div");
  frame.className = "tile-frame";

  const img = document.createElement("img");
  img.className = "tile-img";
  img.loading = "lazy";
  img.decoding = "async";
  img.width = THUMB_PLACEHOLDER_PX;
  img.height = THUMB_PLACEHOLDER_PX;
  // The figcaption below repeats the file name, so announcing it here too would
  // read every tile twice; the alt stays empty and the caption carries the name.
  img.alt = "";
  img.title = item.name;
  img.addEventListener("error", () => onError(item));

  const badge = makeBadge();
  const idxLabel = document.createElement("span");
  idxLabel.className = "tile-index";
  idxLabel.textContent = String(index + 1);

  frame.append(img, badge, idxLabel);
  const caption = document.createElement("figcaption");
  caption.className = "tile-name";
  caption.textContent = item.name;
  figure.append(frame, caption);
  return { figure, img, badge };
}

/** The two locale-dependent strings on a sub-directory card (section 4.13). */
function paintDirCard(card, count, dir) {
  card.title = t("folder.openTitle", { name: dir.name });
  count.textContent = t("browse.dirImageCount", { n: dir.image_count });
}

/**
 * Section 4.13: one sub directory as a card - a 3x3 grid of that directory's
 * own thumbnails plus its name and image count.
 *
 * It is a real <button>: Enter and Space reach it without any extra key
 * handling, and the gallery's own click handler ignores it (it looks for
 * .tile), so the two kinds of cards never interfere.
 */
function makeDirCard(dir, onOpenDir) {
  const card = document.createElement("button");
  card.type = "button";
  card.className = "dir-card";
  card.dataset.path = dir.path;

  const thumbs = document.createElement("span");
  const samples = Array.isArray(dir.preview_images) ? dir.preview_images : [];
  // Section 4.13: the mosaic follows how many thumbnails the folder can offer -
  // one image fills the card, two to four make a 2x2, five and up a 3x3 - so a
  // one-image folder is not a tiny square in the corner of an empty 3x3.
  const side = samples.length >= 5 ? 3 : (samples.length >= 2 ? 2 : 1);
  thumbs.className = "dir-thumbs dir-thumbs-" + side;
  const images = [];
  for (const sample of samples.slice(0, side * side)) {
    const img = document.createElement("img");
    img.className = "dir-thumb";
    img.loading = "lazy";
    img.decoding = "async";
    img.alt = "";
    img.dataset.path = sample;
    img.addEventListener("error", () => { img.classList.add("is-error"); });
    thumbs.append(img);
    images.push(img);
  }

  const name = document.createElement("span");
  name.className = "dir-name";
  name.textContent = dir.name;
  const count = document.createElement("span");
  count.className = "dir-count";
  paintDirCard(card, count, dir);

  card.append(thumbs, name, count);
  card.addEventListener("click", () => onOpenDir(dir.path, dir));
  return { card, images, count };
}

export function createGallery(options) {
  const container = options.container;
  const marqueeEl = options.marquee;
  const centerPanel = options.centerPanel || container.parentElement;
  const onSelectionChange = options.onSelectionChange || (() => {});
  const onActiveChange = options.onActiveChange || (() => {});
  const onThumbError = options.onThumbError || (() => {});
  const onOpen = options.onOpen || (() => {});
  const onOpenDir = options.onOpenDir || (() => {});

  const loader = new ThumbLoader(options.thumbConcurrency);
  const tiles = new Map();
  let items = [];
  let dirs = [];
  let dirImgs = [];
  let dirCards = new Map();
  let visible = [];
  let selected = new Set();
  let activePath = null;
  let cursorIndex = -1;
  let anchorIndex = -1;
  let suppressClick = false;
  let observer = null;
  let viewportObserver = null;
  //: Whether each tile is inside the viewport right now. The two observers can
  //: deliver in either order, so the prefetch callback may read this before the
  //: viewport callback has recorded anything (a missing entry means prefetch).
  const tileOnScreen = new WeakMap();
  //: Tiles already handed to the loader. The observers keep observing now, so a
  //: tile crossing the prefetch margin twice must not be requested twice.
  const requested = new WeakSet();

  function ensureObserver() {
    if (observer || typeof IntersectionObserver !== "function") return observer;
    observer = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        const img = entry.target;
        const path = img.dataset.path;
        if (!path || requested.has(img)) continue;
        requested.add(img);
        loader.request(img, thumbUrl(path, THUMB_SIZE.GRID), tileOnScreen.get(img) === true);
      }
    }, { root: null, rootMargin: ROOT_MARGIN_PX + "px", threshold: 0.01 });
    // The prefetch observer is 400px early on purpose, and that margin is
    // exactly what must NOT decide priority. This second observer has no
    // margin, so isIntersecting means "the user can see it"; the loader is told
    // after every crossing, which is how a tile scrolled into view overtakes
    // the prefetches that were queued for the first screen.
    viewportObserver = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        tileOnScreen.set(entry.target, entry.isIntersecting);
        loader.prioritize(entry.target, entry.isIntersecting);
      }
    }, { root: null, rootMargin: "0px", threshold: 0.01 });
    return observer;
  }

  /** Hand one tile to the observers, or straight to the loader when there are none. */
  function observeTile(img) {
    const io = ensureObserver();
    if (!io) {
      loader.request(img, thumbUrl(img.dataset.path, THUMB_SIZE.GRID));
      return;
    }
    io.observe(img);
    viewportObserver.observe(img);
  }

  function notify() {
    onSelectionChange({
      count: selected.size,
      paths: Array.from(selected),
      active: activePath,
      visibleCount: visible.length,
      total: items.length,
    });
  }

  function setActive(path, announce) {
    if (activePath === path) return;
    activePath = path;
    for (const [tilePath, tile] of tiles) {
      tile.figure.classList.toggle("is-active", tilePath === path);
    }
    if (announce !== false) onActiveChange(path);
  }

  function paintSelection() {
    for (const [path, tile] of tiles) {
      tile.figure.classList.toggle("is-selected", selected.has(path));
    }
  }

  function indexOfPath(path) {
    return visible.indexOf(path);
  }

  function setCursor(index, scroll) {
    if (visible.length === 0) { cursorIndex = -1; return; }
    cursorIndex = Math.max(0, Math.min(index, visible.length - 1));
    for (const [path, tile] of tiles) {
      tile.figure.classList.toggle("is-cursor", path === visible[cursorIndex]);
    }
    if (scroll) {
      const tile = tiles.get(visible[cursorIndex]);
      // revealRow instead of scrollIntoView: that one also scrolls the ancestors
      // (the page included), while the keyboard cursor only has to be brought
      // into the gallery - "nearest" keeps an already visible tile where it is.
      if (tile) revealRow(container, tile.figure, { align: "nearest" });
    }
  }

  function columns() {
    if (visible.length === 0) return 1;
    const first = tiles.get(visible[0]);
    if (!first) return 1;
    const top = first.figure.offsetTop;
    let count = 0;
    for (const path of visible) {
      const tile = tiles.get(path);
      if (!tile || tile.figure.offsetTop !== top) break;
      count += 1;
    }
    return Math.max(1, count);
  }

  function selectRange(from, to, additive) {
    const lo = Math.min(from, to);
    const hi = Math.max(from, to);
    const next = additive ? new Set(selected) : new Set();
    for (let i = lo; i <= hi; i += 1) next.add(visible[i]);
    selected = next;
    paintSelection();
    notify();
  }

  function togglePath(path) {
    if (selected.has(path)) selected.delete(path);
    else selected.add(path);
    paintSelection();
    notify();
  }

  function selectAll() {
    selected = new Set(visible);
    paintSelection();
    notify();
    return selected.size;
  }

  function clearSelection() {
    selected = new Set();
    paintSelection();
    notify();
  }

  function onTileClick(event, path) {
    if (suppressClick) { suppressClick = false; return; }
    container.focus();
    const index = indexOfPath(path);
    setCursor(index, false);
    setActive(path);
    if (event.shiftKey && anchorIndex >= 0) {
      selectRange(anchorIndex, index, event.ctrlKey || event.metaKey);
    } else if (event.ctrlKey || event.metaKey) {
      togglePath(path);
      anchorIndex = index;
    } else {
      selected = new Set([path]);
      anchorIndex = index;
      paintSelection();
      notify();
    }
  }

  container.addEventListener("click", (event) => {
    const figure = event.target.closest(".tile");
    if (!figure || !container.contains(figure)) return;
    onTileClick(event, figure.dataset.path);
  });

  container.addEventListener("dblclick", (event) => {
    const figure = event.target.closest(".tile");
    if (!figure) return;
    event.preventDefault();
    // The visible list travels with the path so the enlarged view can page
    // through exactly what the filter left on screen.
    onOpen(figure.dataset.path, visible.slice());
  });

  container.addEventListener("keydown", (event) => {
    if (visible.length === 0) return;
    const key = event.key;
    if ((event.ctrlKey || event.metaKey) && (key === "a" || key === "A")) {
      selectAll();
      event.preventDefault();
      return;
    }
    // Double-click is the mouse route; a keyboard route has to exist too, or
    // the enlarged view is unreachable without a pointer.
    if (key === "z" || key === "Z") {
      const path = visible[cursorIndex >= 0 ? cursorIndex : 0];
      if (!path) return;
      onOpen(path, visible.slice());
      event.preventDefault();
      return;
    }
    if (key === "Escape") {
      clearSelection();
      return;
    }
    if (key === " " || key === "Spacebar" || key === "Enter") {
      const path = visible[cursorIndex >= 0 ? cursorIndex : 0];
      if (!path) return;
      setActive(path);
      if (key === "Enter") {
        selected = new Set([path]);
        paintSelection();
        notify();
      } else {
        togglePath(path);
      }
      anchorIndex = indexOfPath(path);
      event.preventDefault();
      return;
    }
    let next = cursorIndex >= 0 ? cursorIndex : 0;
    const cols = columns();
    if (key === "ArrowRight") next += 1;
    else if (key === "ArrowLeft") next -= 1;
    else if (key === "ArrowDown") next += cols;
    else if (key === "ArrowUp") next -= cols;
    else if (key === "Home") next = 0;
    else if (key === "End") next = visible.length - 1;
    else return;
    setCursor(next, true);
    const path = visible[cursorIndex];
    if (!path) return;
    setActive(path);
    if (event.shiftKey) {
      const from = anchorIndex >= 0 ? anchorIndex : cursorIndex;
      selectRange(from, cursorIndex, false);
    } else {
      selected = new Set([path]);
      anchorIndex = cursorIndex;
      paintSelection();
      notify();
    }
    event.preventDefault();
  });

  // ---- marquee (rubber band) selection -----------------------------------
  let drag = null;

  function localPoint(event) {
    const rect = centerPanel.getBoundingClientRect();
    return { x: event.clientX - rect.left, y: event.clientY - rect.top };
  }

  function marqueeRect() {
    const lo = { x: Math.min(drag.start.x, drag.last.x), y: Math.min(drag.start.y, drag.last.y) };
    const hi = { x: Math.max(drag.start.x, drag.last.x), y: Math.max(drag.start.y, drag.last.y) };
    return { left: lo.x, top: lo.y, right: hi.x, bottom: hi.y, width: hi.x - lo.x, height: hi.y - lo.y };
  }

  function hitPaths(rect) {
    const hits = [];
    for (const path of visible) {
      const tile = tiles.get(path);
      if (!tile) continue;
      const box = tile.figure.getBoundingClientRect();
      const base = centerPanel.getBoundingClientRect();
      const left = box.left - base.left;
      const top = box.top - base.top;
      const right = box.right - base.left;
      const bottom = box.bottom - base.top;
      if (right < rect.left || left > rect.right || bottom < rect.top || top > rect.bottom) continue;
      hits.push(path);
    }
    return hits;
  }

  container.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    drag = { start: localPoint(event), last: localPoint(event), additive: event.ctrlKey || event.metaKey, moved: false };
  });

  window.addEventListener("pointermove", (event) => {
    if (!drag) return;
    drag.last = localPoint(event);
    const dist = Math.abs(drag.last.x - drag.start.x) + Math.abs(drag.last.y - drag.start.y);
    if (!drag.moved && dist < MARQUEE_MIN_PX) return;
    drag.moved = true;
    const rect = marqueeRect();
    marqueeEl.hidden = false;
    marqueeEl.style.left = rect.left + "px";
    marqueeEl.style.top = rect.top + "px";
    marqueeEl.style.width = rect.width + "px";
    marqueeEl.style.height = rect.height + "px";
  });

  window.addEventListener("pointerup", (event) => {
    if (!drag) return;
    const wasDrag = drag.moved;
    const additive = drag.additive || event.ctrlKey || event.metaKey;
    const rect = marqueeRect();
    drag = null;
    marqueeEl.hidden = true;
    if (!wasDrag) return;
    suppressClick = true;
    window.setTimeout(() => { suppressClick = false; }, 250);
    const hits = hitPaths(rect);
    selected = additive ? new Set([...selected, ...hits]) : new Set(hits);
    paintSelection();
    notify();
    if (hits.length > 0) {
      const last = hits[hits.length - 1];
      setCursor(indexOfPath(last), false);
      setActive(last);
    }
  });

  // ---- public API --------------------------------------------------------
  function clearImages() {
    loader.reset();
    if (observer) { observer.disconnect(); observer = null; }
    if (viewportObserver) { viewportObserver.disconnect(); viewportObserver = null; }
    for (const tile of tiles.values()) {
      // Drop the src attribute so the decoded bitmap of the previous directory
      // can be collected instead of piling up in the image cache.
      tile.img.removeAttribute("src");
      tile.img.removeAttribute("srcset");
    }
    // Section 4.13: the folder cards own their own bitmaps - drop them too.
    for (const img of dirImgs) {
      img.removeAttribute("src");
      img.removeAttribute("srcset");
    }
    dirImgs = [];
    dirCards.clear();
    dirs = [];
    tiles.clear();
    container.replaceChildren();
  }

  function render() {
    const frag = document.createDocumentFragment();
    for (const dir of dirs) {
      const built = makeDirCard(dir, onOpenDir);
      dirImgs.push(...built.images);
      dirCards.set(dir.path, { card: built.card, count: built.count, dir });
      frag.append(built.card);
      for (const img of built.images) observeTile(img);
    }
    items.forEach((item, index) => {
      const tile = makeTile(item, index, onThumbError);
      tile.img.dataset.path = item.path;
      tiles.set(item.path, tile);
      frag.append(tile.figure);
      observeTile(tile.img);
    });
    container.append(frag);
  }

  /**
   * Replace the whole contents. nextDirs is optional: the caller only passes
   * sub directories at a folder-only level (section 4.13), and the gallery
   * renders exactly what it is given.
   */
  function setImages(nextItems, nextDirs) {
    clearImages();
    items = Array.isArray(nextItems) ? nextItems.slice() : [];
    dirs = Array.isArray(nextDirs) ? nextDirs.slice() : [];
    visible = items.map((item) => item.path);
    selected = new Set();
    activePath = null;
    cursorIndex = -1;
    anchorIndex = -1;
    render();
    notify();
    onActiveChange(null);
  }

  function setVisiblePaths(paths) {
    const keep = new Set(paths || []);
    const previousCursor = cursorIndex >= 0 && cursorIndex < visible.length ? visible[cursorIndex] : null;
    visible = items.map((item) => item.path).filter((path) => keep.has(path));
    let pruned = false;
    for (const path of Array.from(selected)) {
      if (!keep.has(path)) { selected.delete(path); pruned = true; }
    }
    for (const [path, tile] of tiles) tile.figure.hidden = !keep.has(path);
    if (pruned) paintSelection();
    if (activePath && !keep.has(activePath)) setActive(null);
    // keep the keyboard cursor where it was when the filter is re-applied
    const nextCursor = previousCursor === null ? -1 : visible.indexOf(previousCursor);
    setCursor(nextCursor >= 0 ? nextCursor : (visible.length > 0 ? 0 : -1), false);
    paintSelection();
    notify();
  }

  function setCaptionState(path, state) {
    const tile = tiles.get(path);
    if (!tile) return;
    tile.state = state;
    paintBadge(tile.badge, state);
  }

  /**
   * Navigation scroll (see navscroll.js): bring the card of one sub directory
   * into view and mark it as the folder this navigation came out of.
   *
   * Returns false when this level does not show that folder as a card (an image
   * grid, an empty listing, a card that is not laid out) - the caller then
   * restores the remembered position of the listing instead.
   */
  function revealDir(path) {
    const entry = findDirCard(path);
    if (!entry) return false;
    entry.card.classList.add("is-revealed");
    return revealRow(container, entry.card);
  }

  function findDirCard(path) {
    if (dirCards.has(path)) return dirCards.get(path);
    // The server may write the same directory with a different separator or
    // drive-letter case than the path a navigation came from.
    for (const [key, entry] of dirCards) {
      if (samePath(key, path)) return entry;
    }
    return null;
  }

  /**
   * Re-apply the current locale to every string the gallery paints dynamically.
   * It reads only state the gallery already holds - the caption state carried by
   * each tile and the directories it was last given - so it never issues a
   * request and is safe before any data has been loaded.
   */
  function relabel() {
    for (const tile of tiles.values()) {
      paintBadge(tile.badge, tile.state);
    }
    for (const entry of dirCards.values()) {
      paintDirCard(entry.card, entry.count, entry.dir);
    }
  }

  function selectPaths(paths) {
    selected = new Set(paths || []);
    paintSelection();
    notify();
  }

  return {
    setImages,
    setVisiblePaths,
    setCaptionState,
    relabel,
    selectPaths,
    selectAll,
    clearSelection,
    getSelectedPaths: () => Array.from(selected),
    //: O(1) per-path check for the filter panel's "selection only" layer.
    isSelected: (path) => selected.has(path),
    getActivePath: () => activePath,
    getAllPaths: () => items.map((item) => item.path),
    getVisiblePaths: () => visible.slice(),
    getFolderCount: () => dirs.length,
    revealDir,
    setActive: (path) => setActive(path),
    clearImages,
    focus: () => container.focus(),
  };
}
