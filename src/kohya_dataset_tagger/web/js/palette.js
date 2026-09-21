/**
 * The command palette (T3): one keyboard route to the app's own actions.
 *
 * The app has six tabs and the actions that matter live behind them - preview/run the tagger,
 * preview/write a batch, rebuild the caches, generate the dataset.toml, start the export. Reaching
 * one means knowing which tab owns it. The palette puts them in one list on one keystroke.
 *
 * **The palette owns no action.** It is handed a table of commands and renders it; every entry's
 * `run` is the same function the corresponding button's own listener calls, and this module never
 * imports the API, never touches app state and never implements anything twice. That is the
 * property the whole feature stands on: a palette that can do something the UI cannot is a second,
 * invisible interface to the dataset.
 *
 * Unavailable commands are **shown, not hidden**, with the reason on the row: "no directory is
 * open" is an answer, a vanished entry is a puzzle. The reasons come from the surface that owns
 * the action (app.js), and the owning button's own disabled/hidden state has the last word - see
 * `available` in app.js's command table.
 *
 * Keyboard: Ctrl+K (or Cmd+K) opens and focuses the input - and never closes it again, because a
 * gesture that toggles throws away what the reader has typed; ArrowDown/ArrowUp move the active
 * row; Enter runs it (or, on an unavailable row, says why); Escape closes and hands focus back to
 * the bar. **Tab LEAVES the panel and closes it**: this is a dropdown, not a modal - the page
 * behind it stays reachable, nothing is trapped, and a click anywhere outside closes it. Nothing
 * else is claimed (the gallery keeps its own arrows, Space, z and Ctrl+A), and the palette still
 * refuses to open at all while another dialog is on screen, because that dialog owns the keyboard
 * while it is open.
 *
 * Roles: the input is the combobox (aria-controls + aria-activedescendant), the list is a listbox
 * of options grouped with role="group", and the options are never focusable - the active
 * descendant is what the screen reader announces, reason included.
 *
 * The matcher is this module's own, with no dependency: see matchScore below.
 */
import { LOCALES, t } from "./strings.js";
import { revealRow } from "./navscroll.js";

/** Case, width and accent folding: NFKC turns full-width Latin into ASCII, then lower-case. */
export function normalizeQuery(text) {
  const raw = text === null || text === undefined ? "" : String(text);
  return raw.normalize("NFKC").toLowerCase().trim();
}

/**
 * How well one term matches one label, or -1 when it does not.
 *
 * Two rules, in order: a **contiguous substring** wins, and the earlier it starts the better; only
 * if there is none, the term may match as a **subsequence** (its characters in order, gaps allowed)
 * and then the tighter the gaps the better. Every contiguous hit scores above every scattered one,
 * so "re" finds "Refresh" before "Rebuild all stale caches".
 */
export function matchScore(text, term) {
  const hay = normalizeQuery(text);
  const needle = normalizeQuery(term);
  if (needle.length === 0) return 0;
  const at = hay.indexOf(needle);
  if (at >= 0) return 1000 - at;
  let cursor = 0;
  let spread = 0;
  for (const ch of needle) {
    const found = hay.indexOf(ch, cursor);
    if (found < 0) return -1;
    spread += found - cursor;
    cursor = found + 1;
  }
  return Math.max(1, 400 - spread);
}

/**
 * Every wording of one command, in every locale: someone using a Chinese interface with an English
 * keyboard can type "refresh" and still find the command whose zh-CN label is the other word for it.
 * The packs already carry the same key set, so this is the same copy read three ways - not a second
 * list of synonyms to keep in sync.
 */
function haystacks(command) {
  const out = [];
  for (const locale of LOCALES) {
    const value = locale.pack ? locale.pack[command.labelKey] : undefined;
    if (typeof value === "string" && value.length > 0 && out.indexOf(value) < 0) out.push(value);
  }
  return out;
}

/**
 * The commands matching `query`, best first.
 *
 * The query is split on whitespace and **every term must match** (any locale's wording counts).
 * An **empty query matches everything**, in the table's own order - the palette opened on nothing
 * is the list of everything the app can do, which is what makes it a way to find out.
 */
export function rankCommands(commands, query) {
  const terms = normalizeQuery(query).split(/\s+/).filter((term) => term.length > 0);
  const scored = [];
  (commands || []).forEach((command, index) => {
    const labels = haystacks(command);
    let total = 0;
    for (const term of terms) {
      let best = -1;
      for (const label of labels) best = Math.max(best, matchScore(label, term));
      if (best < 0) return; // every term has to land somewhere
      total += best;
    }
    scored.push({ command, index, score: total });
  });
  scored.sort((a, b) => (b.score - a.score) || (a.index - b.index));
  return scored.map((entry) => entry.command);
}

/** The row id of one command; also what aria-activedescendant points at. */
function rowId(command) {
  return "palette-option-" + command.id;
}

/**
 * Build the palette over the markup in index.html and return its controls.
 *
 * Options: host (the panel), trigger (the command bar this panel drops down from - what its
 * aria-expanded describes and where the keyboard goes when it closes), input, list, status (the
 * live count/no-match line), close (the panel's own close button), commands (a function read at
 * every paint, so availability is never a snapshot from boot).
 */
export function createCommandPalette(options) {
  const config = options || {};
  const host = config.host;
  const trigger = config.trigger || null;
  const input = config.input;
  const list = config.list;
  const status = config.status;
  const closeButton = config.close;
  const read = typeof config.commands === "function" ? config.commands : () => config.commands || [];

  let opened = false;
  //: The painted rows, with the reason each one is unavailable (null when it is not).
  let rows = [];
  //: The painted option elements, index-aligned with rows.
  let nodes = [];
  let active = -1;

  /** `{key, params}` when the app says this command cannot run now, else null. */
  function whyNot(command) {
    if (typeof command.available !== "function") return null;
    const why = command.available();
    return why && why.key ? why : null;
  }

  function reasonText(why) {
    return t(why.key, why.params);
  }

  /**
   * Another dialog is on screen, so it owns the keyboard: it has a focus trap, its own Escape and
   * its own keys. Opening over it would take focus out of a modal that claims aria-modal="true",
   * and the two key handlers would fight. The zoom overlay is a modal too, with its own class.
   *
   * The palette is not one of these any more (it is a dropdown with no backdrop class), which is
   * why the guard no longer has to except its own node.
   */
  function anotherDialogOpen() {
    if (typeof document === "undefined" || typeof document.querySelectorAll !== "function") return false;
    for (const el of document.querySelectorAll(".dialog-backdrop, .zoom-backdrop")) {
      if (el.hidden !== true) return true;
    }
    return false;
  }

  function paintRow(row, index) {
    const item = document.createElement("div");
    item.className = "palette-item";
    item.id = rowId(row.command);
    item.setAttribute("role", "option");
    item.dataset.command = row.command.id;
    const label = document.createElement("span");
    label.className = "palette-label";
    label.textContent = t(row.command.labelKey);
    item.append(label);
    if (row.why) {
      // Shown, with the reason. A vanished command is a puzzle; "no directory is open" is an answer.
      item.classList.add("is-unavailable");
      item.setAttribute("aria-disabled", "true");
      const spoken = document.createElement("span");
      spoken.className = "sr-only";
      spoken.textContent = t("palette.unavailable");
      const why = document.createElement("span");
      why.className = "palette-why";
      why.textContent = reasonText(row.why);
      item.append(spoken, why);
    } else {
      item.setAttribute("aria-disabled", "false");
    }
    const isActive = index === active;
    item.setAttribute("aria-selected", isActive ? "true" : "false");
    if (isActive) item.classList.add("is-active");
    item.addEventListener("click", () => {
      // A click is a choice, not a keystroke: it activates the row it landed on and runs it.
      activate(index);
      run();
    });
    return item;
  }

  function paintList() {
    list.replaceChildren();
    nodes = [];
    let group = null;
    let box = null;
    for (const [index, row] of rows.entries()) {
      if (row.command.group !== group) {
        group = row.command.group;
        box = document.createElement("div");
        box.className = "palette-group";
        box.setAttribute("role", "group");
        box.setAttribute("aria-label", t("palette.group." + group));
        const head = document.createElement("div");
        head.className = "palette-group-head";
        // The visible heading is decoration; the group's accessible name is the same words above,
        // so a screen reader hears it once and the listbox still owns nothing but options.
        head.setAttribute("aria-hidden", "true");
        head.textContent = t("palette.group." + group);
        box.append(head);
        list.append(box);
      }
      const item = paintRow(row, index);
      nodes.push(item);
      box.append(item);
    }
  }

  function paintStatus() {
    status.textContent = rows.length === 0 ? t("palette.noMatch") : t("palette.status", { n: rows.length });
  }

  function paintActive() {
    for (const [index, node] of nodes.entries()) {
      const isActive = index === active;
      node.setAttribute("aria-selected", isActive ? "true" : "false");
      node.classList.toggle("is-active", isActive);
    }
    const node = active >= 0 ? nodes[active] : null;
    if (node) {
      input.setAttribute("aria-activedescendant", node.id);
      // "nearest": an already visible row stays put, so the list does not jump under the cursor.
      revealRow(list, node, { align: "nearest" });
    } else {
      input.removeAttribute("aria-activedescendant");
    }
  }

  /**
   * Repaint from the current query. `keepActive` follows the active command by id, so a background
   * repaint (a job ending) does not move the highlight; a new query starts at the best match.
   */
  function paint(keepActive) {
    const wanted = keepActive && active >= 0 && rows[active] ? rows[active].command.id : null;
    const query = input ? input.value : "";
    rows = rankCommands(read(), query).map((command) => ({ command, why: whyNot(command) }));
    active = 0;
    if (wanted !== null) {
      const found = rows.findIndex((row) => row.command.id === wanted);
      if (found >= 0) active = found;
    }
    if (rows.length === 0) active = -1;
    paintList();
    paintStatus();
    paintActive();
  }

  function activate(index) {
    if (rows.length === 0) {
      active = -1;
      paintActive();
      return;
    }
    active = ((index % rows.length) + rows.length) % rows.length;
    paintActive();
  }

  function run() {
    const row = rows[active];
    if (!row) return;
    if (row.why) {
      // Enter on something the app cannot do says why rather than doing nothing: silence reads as
      // a broken key. The reason is already on the row, and this line is the live region that
      // speaks it.
      status.textContent = reasonText(row.why);
      return;
    }
    // Hand the keyboard back BEFORE running: a command that opens a dialog (the directory picker)
    // remembers what had focus when it opened, and that should be the page, not a hidden palette.
    close();
    row.command.run();
  }

  function onDocumentKeyDown(event) {
    const key = String((event && event.key) || "");
    if ((event.ctrlKey || event.metaKey) && !event.altKey && (key === "k" || key === "K")) {
      event.preventDefault();
      // Never a toggle: open() only re-focuses the input when the panel is already open.
      open();
      return;
    }
    if (!opened) return;
    if (key === "Escape") {
      event.preventDefault();
      close();
      return;
    }
    if (key === "Tab") {
      // A dropdown does not trap Tab, so leaving the panel is a dismissal. The default is NOT
      // prevented: close() has just put focus back on the bar, and the browser moves on from there
      // - which is what "Tab leaves the panel" means for the keyboard.
      close();
      return;
    }
    if (key === "ArrowDown") {
      event.preventDefault();
      activate(active + 1);
      return;
    }
    if (key === "ArrowUp") {
      event.preventDefault();
      activate(active - 1);
      return;
    }
    if (key === "Enter") {
      event.preventDefault();
      run();
    }
    // Everything else belongs to the search field (Home/End included: they move the caret there).
  }

  function open() {
    if (!host || !input) return false;
    if (opened) {
      // Already open - Ctrl+K again, or a second click on the bar. It puts the caret back in the
      // field and deliberately does not close: a gesture that toggles throws away a typed query.
      input.focus();
      return true;
    }
    if (anotherDialogOpen()) return false;
    opened = true;
    host.hidden = false;
    input.setAttribute("aria-expanded", "true");
    if (trigger) trigger.setAttribute("aria-expanded", "true");
    input.value = "";
    paint(false);
    input.focus();
    return true;
  }

  function close() {
    if (!opened) return;
    opened = false;
    host.hidden = true;
    if (input) {
      input.value = "";
      input.setAttribute("aria-expanded", "false");
      input.removeAttribute("aria-activedescendant");
    }
    if (trigger) trigger.setAttribute("aria-expanded", "false");
    rows = [];
    nodes = [];
    active = -1;
    // Nothing is left holding the last claim: a closed panel that keeps its row list (or its
    // "3 commands" line) is the same defect the readiness strip fixed for hidden segments.
    list.replaceChildren();
    if (status) status.textContent = "";
    // The keyboard goes back to the bar - the control this panel belongs to, and where the next
    // keystroke is expected. Escape in a dropdown is "put me back", not "leave me in a hidden box".
    if (trigger && typeof trigger.focus === "function") trigger.focus();
  }

  /**
   * A click anywhere outside the panel - and outside the bar it drops from - closes it: the mouse's
   * Escape. A dropdown left open while the reader works somewhere else is a panel nobody dismissed.
   * The bar is an exception because its own click handler opens the panel, and a click there must
   * not read as "outside" a moment later.
   */
  function onDocumentClick(event) {
    if (!opened) return;
    const target = event ? event.target : null;
    if (host && typeof host.contains === "function" && host.contains(target)) return;
    if (trigger && typeof trigger.contains === "function" && trigger.contains(target)) return;
    close();
  }

  if (closeButton) closeButton.addEventListener("click", () => { close(); });
  if (input) {
    input.addEventListener("input", () => { paint(false); });
  }
  // On the document, not on the panel: the opening gesture has to work while the palette is
  // closed and focus is anywhere on the page, and a click "outside" is only visible from up there.
  document.addEventListener("keydown", onDocumentKeyDown);
  document.addEventListener("click", onDocumentClick);
  // One writer for both aria-expanded values: the bar's (is the panel open?) and the input's
  // (does the combobox have a list?). They are set together in open() and close() above.
  input.setAttribute("aria-expanded", "false");
  if (trigger) trigger.setAttribute("aria-expanded", "false");

  return {
    open,
    close,
    isOpen: () => opened,
    /** Repaint from held state (a job ended, the locale changed); never a request. */
    refresh: () => { if (opened) paint(true); },
    relabel: () => { if (opened) paint(true); },
  };
}
