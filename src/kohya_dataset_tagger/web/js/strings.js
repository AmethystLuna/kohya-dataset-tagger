/**
 * i18n engine: locale registry, resolution, lookup and DOM copy application.
 *
 * Every user-visible string lives in ./locales/<tag>.js - one pack per language.
 * Nothing else under web/ may contain CJK (or any other non-Latin) copy; the
 * static contract test test_web_contract.py enforces:
 *   (a) no CJK literal outside web/js/locales/,
 *   (b) every data-copy-* attribute and t("...") key resolves to a real key,
 *   (c) every pack carries exactly the same key set and the same {placeholders}
 *       per key, and no value is empty.
 *
 * Packs are imported statically rather than fetched: the frontend has no build
 * step and must also work from file:// and under a strict CSP. Three small
 * modules cost less than an async boot path (and a failed fetch would leave the
 * whole UI untranslated).
 *
 * Locale resolution order: ?lang= > localStorage > navigator.languages > default.
 */

import { STRINGS as ZH_CN, FALLBACK_GROUPS } from "./locales/zh-CN.js";
import { STRINGS as EN } from "./locales/en.js";
import { STRINGS as JA } from "./locales/ja.js";

/**
 * Supported locales, in switcher order. The label is the language's endonym -
 * it is deliberately NOT translated, because a user who cannot read the current
 * UI language must still be able to recognise their own.
 */
export const LOCALES = Object.freeze([
  Object.freeze({ tag: "zh-CN", label: "\u7b80\u4f53\u4e2d\u6587", pack: ZH_CN }),
  Object.freeze({ tag: "en", label: "English", pack: EN }),
  Object.freeze({ tag: "ja", label: "\u65e5\u672c\u8a9e", pack: JA }),
]);

export const DEFAULT_LOCALE = "zh-CN";

const STORAGE_KEY = "kdt.lang";
export const LOCALE_CHANGE_EVENT = "kdt:localechange";

const PACKS = new Map(LOCALES.map((locale) => [locale.tag, locale.pack]));

let current = DEFAULT_LOCALE;

/** Map any BCP-47-ish tag ("zh-Hans-CN", "ja_JP", "en-US") onto a supported tag. */
export function normalizeTag(raw) {
  if (!raw) return null;
  const lower = String(raw).trim().toLowerCase().replace(/_/g, "-");
  for (const locale of LOCALES) {
    if (locale.tag.toLowerCase() === lower) return locale.tag;
  }
  const base = lower.split("-")[0];
  for (const locale of LOCALES) {
    if (locale.tag.toLowerCase().split("-")[0] === base) return locale.tag;
  }
  return null;
}

function packFor(tag) {
  return PACKS.get(tag) || PACKS.get(DEFAULT_LOCALE);
}

function readStored() {
  try {
    return window.localStorage.getItem(STORAGE_KEY);
  } catch (err) {
    return null;
  }
}

function writeStored(tag) {
  try {
    window.localStorage.setItem(STORAGE_KEY, tag);
  } catch (err) {
    /* private mode / disabled storage: the choice just does not persist */
  }
}

/** The locale currently in effect. Never throws, also usable outside a DOM. */
export function getLocale() {
  return current;
}

function applyDocumentLang(tag) {
  if (typeof document !== "undefined" && document.documentElement) {
    document.documentElement.lang = tag;
  }
}

/** Look up a string, interpolating {name} placeholders. */
export function t(key, params) {
  const raw = packFor(current)[key];
  if (raw === undefined) {
    const fallback = PACKS.get(DEFAULT_LOCALE)[key];
    if (fallback === undefined) {
      console.warn("[strings] missing key: " + key);
      return key;
    }
    return interpolate(fallback, params);
  }
  return interpolate(raw, params);
}

/**
 * True when the key is defined in the current pack (or in the source pack as a
 * fallback). Callers that render message codes coming from the backend use this
 * to decide between a localized rendering and the backend's own raw text - a
 * missing translation must degrade to the raw message, never to the bare key.
 */
export function hasKey(key) {
  return packFor(current)[key] !== undefined || PACKS.get(DEFAULT_LOCALE)[key] !== undefined;
}

function interpolate(raw, params) {
  if (!params) return raw;
  return raw.replace(/\{(\w+)\}/g, (match, name) => (
    Object.prototype.hasOwnProperty.call(params, name) ? String(params[name]) : match
  ));
}

const COPY_ATTRS = [
  ["data-copy", "textContent"],
  ["data-copy-placeholder", "placeholder"],
  ["data-copy-title", "title"],
  ["data-copy-alt", "alt"],
  ["data-copy-aria", "aria-label"],
];

/**
 * Fill every element carrying a data-copy* attribute from the current pack.
 * index.html therefore holds no copy at all, which is what makes the
 * "copy lives only in locale packs" contract test meaningful.
 */
export function applyCopy(root) {
  const scope = root || document;
  for (const [attr, target] of COPY_ATTRS) {
    for (const el of scope.querySelectorAll("[" + attr + "]")) {
      const value = t(el.getAttribute(attr));
      if (target === "textContent") el.textContent = value;
      else el.setAttribute(target, value);
    }
  }
  document.title = t("app.title");
  return scope;
}

/** Switch locale. Persists, relabels the static DOM and notifies listeners. */
export function setLocale(tag, options) {
  const opts = options || {};
  const next = normalizeTag(tag);
  if (!next) return false;
  current = next;
  if (opts.persist !== false) writeStored(next);
  applyDocumentLang(next);
  applyCopy(document);
  if (opts.notify !== false && typeof document !== "undefined") {
    document.dispatchEvent(new CustomEvent(LOCALE_CHANGE_EVENT, { detail: { tag: next } }));
  }
  return true;
}

/** Subscribe to locale changes; returns an unsubscribe function. */
export function onLocaleChange(callback) {
  document.addEventListener(LOCALE_CHANGE_EVENT, callback);
  return () => document.removeEventListener(LOCALE_CHANGE_EVENT, callback);
}

/**
 * Resolve the initial locale. Called from boot() - deliberately NOT at module
 * scope, so importing this module never touches the DOM.
 */
export function initLocale() {
  let chosen = null;
  if (typeof window !== "undefined") {
    try {
      chosen = normalizeTag(new URLSearchParams(window.location.search).get("lang"));
    } catch (err) {
      chosen = null;
    }
    if (chosen) writeStored(chosen);
  }
  if (!chosen) chosen = normalizeTag(readStored());
  if (!chosen && typeof navigator !== "undefined") {
    const candidates = navigator.languages || (navigator.language ? [navigator.language] : []);
    for (const candidate of candidates) {
      chosen = normalizeTag(candidate);
      if (chosen) break;
    }
  }
  current = chosen || DEFAULT_LOCALE;
  applyDocumentLang(current);
  return current;
}

/**
 * Keyword fallback for backend warning messages that carry no recognisable
 * "[code] " prefix. Language-specific by design (it matches the backend's
 * wording), hence it lives in the zh-CN pack.
 */
export const WARNING_GROUPS = FALLBACK_GROUPS;
