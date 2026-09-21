/**
 * Headless harness for the i18n engine (web/js/strings.js).
 *
 * The static contract test can see that every pack has the same keys; only
 * running the engine can show that locale resolution actually honours
 * ?lang= > localStorage > navigator, that setLocale() persists and relabels the
 * static DOM, and that placeholder interpolation survives a language switch.
 *
 * Usage: node i18n_harness.mjs <absolute path to web/>
 */
import { pathToFileURL } from "node:url";

const WEB = process.argv[2];
if (!WEB) throw new Error("usage: node i18n_harness.mjs <web dir>");

function assert(condition, message) {
  if (!condition) throw new Error("i18n harness: " + message);
}

// ---- minimal browser surface --------------------------------------------
class FakeStorage {
  constructor() { this.map = new Map(); }
  getItem(key) { return this.map.has(key) ? this.map.get(key) : null; }
  setItem(key, value) { this.map.set(key, String(value)); }
}

class FakeEl {
  constructor(attrs) {
    this.attrs = attrs;
    this.textContent = "";
    this.placeholder = "";
    this.title = "";
    this.alt = "";
    this.ariaLabel = "";
  }
  getAttribute(name) { return Object.prototype.hasOwnProperty.call(this.attrs, name) ? this.attrs[name] : null; }
  setAttribute(name, value) {
    // Mirror the real DOM: setAttribute on these reflects onto the property.
    this.attrs[name] = value;
    if (name === "aria-label") this.ariaLabel = value;
    else if (name === "placeholder") this.placeholder = value;
    else if (name === "title") this.title = value;
    else if (name === "alt") this.alt = value;
  }
}

const storage = new FakeStorage();
const copyEl = new FakeEl({ "data-copy": "app.title" });
const placeholderEl = new FakeEl({ "data-copy-placeholder": "settings.rootPathPlaceholder" });
const ariaEl = new FakeEl({ "data-copy-aria": "lang.label" });
const bySelector = {
  "[data-copy]": [copyEl],
  "[data-copy-placeholder]": [placeholderEl],
  "[data-copy-title]": [],
  "[data-copy-alt]": [],
  "[data-copy-aria]": [ariaEl],
};
const root = { querySelectorAll: (selector) => bySelector[selector] || [] };

const events = [];
globalThis.CustomEvent = class CustomEvent {
  constructor(type, options) { this.type = type; this.detail = options && options.detail; }
};
globalThis.window = { localStorage: storage, location: { search: "" } };
// Node 24 exposes a getter-only globalThis.navigator, so it must be replaced
// with defineProperty rather than assigned.
Object.defineProperty(globalThis, "navigator", {
  value: { languages: ["en-US", "en"], language: "en-US" },
  configurable: true,
  writable: true,
});
globalThis.document = {
  documentElement: { lang: "" },
  title: "",
  querySelectorAll: (selector) => bySelector[selector] || [],
  addEventListener: (type, handler) => { events.push({ type, handler }); },
  removeEventListener: () => {},
  dispatchEvent: (event) => { for (const item of events) if (item.type === event.type) item.handler(event); },
};

const mod = await import(pathToFileURL(WEB + "/js/strings.js").href);
const { t, applyCopy, setLocale, getLocale, initLocale, normalizeTag, onLocaleChange, LOCALES } = mod;

// ---- registry -----------------------------------------------------------
const tags = LOCALES.map((locale) => locale.tag);
assert(tags.join(",") === "zh-CN,en,ja", "LOCALES must be zh-CN,en,ja, got " + tags);
for (const locale of LOCALES) {
  assert(locale.label && locale.label.length > 0, "locale " + locale.tag + " has no endonym label");
}

// ---- tag normalisation --------------------------------------------------
assert(normalizeTag("zh-Hans-CN") === "zh-CN", "zh-Hans-CN must map to zh-CN");
assert(normalizeTag("zh_CN") === "zh-CN", "zh_CN must map to zh-CN");
assert(normalizeTag("ja-JP") === "ja", "ja-JP must map to ja");
assert(normalizeTag("en-US") === "en", "en-US must map to en");
assert(normalizeTag("EN") === "en", "normalisation must be case-insensitive");
assert(normalizeTag("fr-FR") === null, "an unsupported language must not silently resolve");
assert(normalizeTag("") === null, "an empty tag must not resolve");

// ---- default + lookup ---------------------------------------------------
assert(getLocale() === "zh-CN", "the default locale must be zh-CN, got " + getLocale());
const zhTitle = t("app.title");
assert(zhTitle.indexOf("\u6570\u636e\u96c6") >= 0, "zh-CN app.title looks wrong: " + zhTitle);
assert(t("browse.dirImageCount", { n: 7 }).indexOf("7") >= 0, "placeholder {n} was not interpolated");
assert(t("browse.dirImageCount", { n: 7 }) !== t("browse.dirImageCount"), "missing params must not crash");
assert(t("no.such.key") === "no.such.key", "a missing key must return the key, not undefined");
assert(mod.hasKey("app.title") === true, "hasKey must see a defined key");
assert(mod.hasKey("no.such.key") === false, "hasKey must reject an undefined key");

// ---- initLocale: navigator fallback ------------------------------------
assert(initLocale() === "en", "initLocale must fall back to the navigator language");
assert(t("app.title") !== zhTitle, "the English pack was not applied");
assert(document.documentElement.lang === "en", "documentElement.lang was not updated");

// ---- initLocale: localStorage wins over navigator ----------------------
storage.setItem("kdt.lang", "ja");
assert(initLocale() === "ja", "localStorage must take precedence over navigator");
assert(document.documentElement.lang === "ja", "lang attribute not updated for ja");

// ---- initLocale: ?lang= wins over everything and is persisted ----------
window.location.search = "?lang=zh-CN";
assert(initLocale() === "zh-CN", "?lang= must take precedence over localStorage");
assert(storage.getItem("kdt.lang") === "zh-CN", "?lang= must be persisted");

// ---- setLocale: switch, persist, relabel, notify -----------------------
storage.setItem("kdt.lang", "ja");
let notified = null;
const off = onLocaleChange((event) => { notified = event.detail.tag; });
assert(setLocale("en") === true, "setLocale(en) must succeed");
assert(getLocale() === "en", "setLocale did not switch the locale");
assert(storage.getItem("kdt.lang") === "en", "setLocale did not persist the choice");
assert(document.documentElement.lang === "en", "setLocale did not update documentElement.lang");
assert(notified === "en", "setLocale did not notify listeners, got " + notified);
assert(t("app.title") !== zhTitle, "t() still returns the old language after setLocale");
assert(setLocale("fr") === false, "setLocale must reject an unsupported tag");
assert(setLocale(null) === false, "setLocale must reject a missing tag");
off();

// ---- applyCopy ----------------------------------------------------------
setLocale("zh-CN");
applyCopy(root);
assert(copyEl.textContent === t("app.title"), "applyCopy did not fill data-copy");
assert(copyEl.textContent === zhTitle, "applyCopy wrote the wrong language");
assert(placeholderEl.placeholder === t("settings.rootPathPlaceholder"), "applyCopy did not fill placeholder");
assert(ariaEl.ariaLabel === t("lang.label"), "applyCopy did not fill aria-label");
assert(document.title === t("app.title"), "applyCopy did not set document.title");
setLocale("ja");
applyCopy(root);
assert(copyEl.textContent !== zhTitle, "applyCopy kept the old language after a switch");

// ---- every key exists in every pack (engine-level, not just static) -----
const zh = LOCALES.find((locale) => locale.tag === "zh-CN").pack;
for (const locale of LOCALES) {
  const missing = Object.keys(zh).filter((key) => !(key in locale.pack));
  assert(missing.length === 0, locale.tag + " is missing keys: " + missing.slice(0, 5));
}

console.log("i18n harness OK");
