/**
 * Tag autocomplete: one dropdown for every input that takes a tag.
 *
 * Why it is a module: the behaviour used to live inside the per-image tag editor
 * (tags.js), where nothing but an editor could reach it - so the batch panel had
 * no autocomplete at all, a wiring fact rather than a decision. Two hosts use
 * this now: the caption editor (the dock and the zoom overlay) and the batch
 * panel's tag fields.
 *
 * What the host owns: the input, the dropdown element (it is positioned by the
 * host's own `.suggest-wrap`, which is `position: relative`), the candidate
 * source, and what an accepted tag means - the editor appends a chip, the batch
 * panel fills the field.
 *
 * Keyboard ownership: this module listens for keydown itself - it owns the arrow
 * keys, Tab, Escape and the Enter that takes the active row, and it marks those
 * events as handled (`event.preventDefault()`). Enter means something else on
 * both hosts ("add the tag that was typed"), so a host that presses Enter too
 * must return early on `event.defaultPrevented`; two handlers acting on one Enter
 * would add the tag twice. Escape closes the dropdown and leaves focus in the
 * input it came from; a click outside (a blur) closes it too.
 */

export const SUGGEST_LIMIT = 8;
export const SUGGEST_DEBOUNCE_MS = 150;

/**
 * §4.10's ordering applied to a candidate list the host built itself: prefix
 * hits first, then count descending, then the tag name. The vocabulary endpoint
 * already answers in this order; a host ranking its own list (the batch panel's
 * dataset tags) must use this, so one dropdown never mixes two orders.
 */
export function rankSuggestions(entries, query, limit) {
  const needle = String(query || "").trim().toLowerCase();
  const matched = [];
  for (const entry of Array.isArray(entries) ? entries : []) {
    if (!entry || !entry.tag) continue;
    const name = String(entry.tag).toLowerCase();
    if (needle && name.indexOf(needle) < 0) continue;
    matched.push({ entry, rank: needle && !name.startsWith(needle) ? 1 : 0 });
  }
  matched.sort((a, b) => {
    if (a.rank !== b.rank) return a.rank - b.rank;
    const left = Number(a.entry.count) || 0;
    const right = Number(b.entry.count) || 0;
    if (left !== right) return right - left;
    return String(a.entry.tag).localeCompare(String(b.entry.tag));
  });
  const cap = Number(limit) > 0 ? Number(limit) : SUGGEST_LIMIT;
  return matched.slice(0, cap).map((item) => item.entry);
}

/**
 * One dropdown over one input.
 *
 * `source(query, limit)` may be sync or async and returns
 * `[{tag, category?, category_id?, count?}]`. `exclude()` returns tags that must
 * not be offered (the editor withholds the ones the image already has).
 * `query(value)` turns the input's value into what is being typed - the default is
 * the trimmed value, and a field holding a comma separated list passes the
 * fragment after its last comma, since the whole list matches nothing.
 * `onAccept(tag, item)` performs the host's own completion; the default writes
 * the tag into the input. Returns `{ close }` - the dropdown answers for itself
 * everywhere else.
 */
export function createSuggest(options) {
  const config = options || {};
  const input = config.input;
  const list = config.list;
  if (!input || !list) throw new Error("createSuggest needs an input and a list element");
  const source = config.source || (() => []);
  const exclude = config.exclude || (() => []);
  const readQuery = config.query || ((value) => String(value || "").trim());
  const onAccept = config.onAccept || ((tag) => { input.value = tag; });
  const limit = Number(config.limit) > 0 ? Number(config.limit) : SUGGEST_LIMIT;
  const debounceMs = Number(config.debounceMs) >= 0 ? Number(config.debounceMs) : SUGGEST_DEBOUNCE_MS;

  let timer = null;
  let active = -1;
  let items = [];
  //: Every refresh takes a token; a reply whose token is stale is dropped, so a
  //: slow query cannot repaint the dropdown for text that has since changed.
  let token = 0;

  function cancelTimer() {
    if (timer) {
      window.clearTimeout(timer);
      timer = null;
    }
  }

  function close() {
    cancelTimer();
    list.hidden = true;
    list.replaceChildren();
    items = [];
    active = -1;
  }

  function paint() {
    list.replaceChildren();
    items.forEach((item, index) => {
      const row = document.createElement("div");
      row.className = "suggest-item" + (index === active ? " is-active" : "");
      const name = document.createElement("span");
      name.textContent = item.tag;
      const cat = document.createElement("span");
      cat.className = "s-cat";
      // Section 4.5: category is the human readable name ("General") and
      // category_id the numeric code ("0"); fall back to the id so an older
      // backend still renders something instead of an empty badge. A host list
      // that has no category at all (the batch panel's dataset tags) shows the
      // count alone.
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
      list.append(row);
    });
    list.hidden = items.length === 0;
  }

  function accept(index) {
    const item = items[index];
    if (!item) return false;
    // Close before handing the tag over: the host may set input.value, which
    // fires no "input" event, and a pending debounce would reopen the dropdown
    // with the query it was scheduled for.
    close();
    onAccept(item.tag, item);
    input.focus();
    return true;
  }

  async function refresh() {
    const query = readQuery(input.value);
    if (!query) { close(); return; }
    const mine = ++token;
    let suggested = [];
    try {
      suggested = (await source(query, limit)) || [];
    } catch (err) {
      suggested = [];
    }
    if (mine !== token) return;
    const blocked = new Set((exclude() || []).map((tag) => String(tag)));
    items = suggested.filter((item) => item && item.tag && !blocked.has(String(item.tag)));
    active = items.length > 0 ? 0 : -1;
    paint();
  }

  list.addEventListener("mousedown", (event) => {
    // keep focus in the input: a blur would commit the half-typed query first
    event.preventDefault();
    const row = event.target && event.target.closest ? event.target.closest(".suggest-item") : null;
    if (row) accept(Number(row.dataset.index));
  });

  input.addEventListener("input", () => {
    cancelTimer();
    timer = window.setTimeout(() => { void refresh(); }, debounceMs);
  });

  input.addEventListener("blur", () => { close(); });

  /** The keys the dropdown answers for; `event.defaultPrevented` is the handshake. */
  function handleKeydown(event) {
    if (event.defaultPrevented) return;
    if (event.key === "Escape") {
      if (list.hidden) return;
      close();
      event.preventDefault();
      return;
    }
    if (list.hidden || items.length === 0) return;
    if (event.key === "ArrowDown") {
      active = (active + 1) % items.length;
      paint();
      event.preventDefault();
      return;
    }
    if (event.key === "ArrowUp") {
      active = (active - 1 + items.length) % items.length;
      paint();
      event.preventDefault();
      return;
    }
    if (event.key === "Tab" || (event.key === "Enter" && active >= 0)) {
      if (accept(active)) event.preventDefault();
    }
  }

  input.addEventListener("keydown", handleKeydown);

  close();

  return { close };
}
