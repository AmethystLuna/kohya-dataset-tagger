/**
 * The theme: one stored preference, one attribute, one switch.
 *
 * Light is the default for every visitor on every OS, and dark is a choice this app
 * remembers (localStorage, key THEME_KEY). There is deliberately no
 * prefers-color-scheme branch and no "follow the system" third state - the theme is
 * never a guess about someone else's desktop - and no ?theme= query parameter: that
 * would be a second way in, and it could not be applied before the first paint
 * without a second copy of the resolution order.
 *
 * The attribute is applied twice, on purpose. js/theme-boot.js (a classic <head>
 * script, because a module is deferred and runs after the first paint) puts
 * data-theme on <html> before anything is painted; this module then owns it for the
 * rest of the session. Both read the same key - see the module's docstring for why
 * that literal exists twice, and test/test_web_theme.py for the criterion that keeps
 * them equal.
 *
 * The checkbox and the attribute are written by ONE function (paint), from one value,
 * so "the switch says dark while the page is light" is not a state this module can be
 * in.
 */

import { applyCopy } from "./strings.js";

/** Where the choice lives. js/theme-boot.js carries the same literal. */
export const THEME_KEY = "kdt.theme";

//: The two themes. "light" is first because it is the default.
export const THEMES = Object.freeze(["light", "dark"]);

export const DEFAULT_THEME = "light";

function safeStorage() {
  try {
    return window.localStorage;
  } catch (err) {
    return null;
  }
}

function safeGet(storage, key) {
  if (!storage) return null;
  try {
    return storage.getItem(key);
  } catch (err) {
    return null;
  }
}

function safeSet(storage, key, value) {
  if (!storage) return;
  try {
    storage.setItem(key, value);
  } catch (err) {
    // Private mode / quota / disabled storage: the choice still applies to this
    // session, it just does not survive the next load.
  }
}

/**
 * The stored theme, or DEFAULT_THEME.
 *
 * A missing key, a garbage value ("Dark", "1", a JSON blob) and a storage whose
 * getItem throws all read as light - the same answer js/theme-boot.js gives, which
 * is what keeps the pre-paint state and this module from disagreeing.
 */
export function readTheme(storage) {
  const store = storage === undefined ? safeStorage() : storage;
  return safeGet(store, THEME_KEY) === "dark" ? "dark" : DEFAULT_THEME;
}

/** Put a theme on the document and return the theme that is now in effect. */
export function applyTheme(value, root) {
  const theme = value === "dark" ? "dark" : DEFAULT_THEME;
  const host = root || (typeof document !== "undefined" ? document.documentElement : null);
  if (host) host.setAttribute("data-theme", theme);
  return theme;
}

/**
 * Wire the switch.
 *
 * Options: { input, label?, storage?, root? }. Missing input is tolerated (one
 * renamed id must not kill the page); storage defaults to window.localStorage.
 * Returns { relabel(), set(value), value(), input }.
 */
export function createThemeSwitch(options) {
  const config = options || {};
  const input = config.input || null;
  const root = config.root || (typeof document !== "undefined" ? document.documentElement : null);
  const storage = config.storage === undefined ? safeStorage() : config.storage;

  /** The single writer: one value in, the attribute and the checkbox out together. */
  function paint(value) {
    const theme = applyTheme(value, root);
    if (input) input.checked = theme === "dark";
    return theme;
  }

  let current = paint(readTheme(storage));

  /** Choose a theme and remember it. */
  function set(value) {
    current = paint(value);
    safeSet(storage, THEME_KEY, current);
    return current;
  }

  if (input) {
    input.addEventListener("change", () => { set(input.checked ? "dark" : "light"); });
  }

  /**
   * The label's words are a static data-copy node, so the locale engine's own pass
   * already reaches them; this exists because the label IS copy a language switch has
   * to repaint, and app.js's relabelAfterLocaleChange() calls relabel() on every
   * component it knows about.
   */
  function relabel() {
    const label = config.label || (input && typeof input.closest === "function" ? input.closest("label") : null);
    applyCopy(label || undefined);
  }

  return { relabel, set, value: () => current, input };
}
