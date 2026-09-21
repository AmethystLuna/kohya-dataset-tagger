/**
 * Shared image-processing parameters for the export panel (section 4.9), the image
 * scaling panel (section 4.14) and the crop panel (section 4.17).
 *
 * Why one store instead of three sets of inputs: the resolution and the caption
 * extension mean the same thing everywhere. The dataset.toml advertises a
 * resolution and names a caption_extension, while the exported images are
 * actually scaled to that resolution and get sidecars with that extension. If the
 * panels kept private copies, one of them would quietly drift and the trainer
 * would look for sidecars that are not there.
 *
 * The same argument covers the rest of the pipeline settings (resample filter,
 * output format, quality, optimize, "do not enlarge"). The crop panel does not
 * own a second copy of them: it reads what the scaling panel shows, so "the image
 * this tool writes" looks the same whichever panel wrote it - and a value that is
 * inherited is a value the user can still see somewhere.
 *
 * The store is a tiny observable: panels write through the setters and
 * subscribe for changes, so an edit in one tab shows up in the other.
 *
 * It also **remembers**. These are values a user re-enters on every visit - a target resolution,
 * a format, a quality - and losing them on every reload is the state loss the theme, the language
 * and the panel widths stopped doing long ago. One localStorage key holds them
 * (`TRAIN_PARAMS_STORAGE_KEY`), read once when the store is built and written whenever a setter
 * accepts a value, so a reload lands the panel where the user left it. `writeToml` is remembered
 * with them: it is a parameter of the same job.
 *
 * What is deliberately **not** remembered is anything that decides *where* something is written:
 * the target directory (a stored destination is a location nobody chose this session) and
 * `overwrite_dataset_toml` (a standing permission to replace a hand-tuned file is exactly what
 * "never replaced silently" is about - the panel offers it per run, and the run that skips the file
 * says so). Both are typed or ticked in the session that uses them.
 */

export const TRAIN_PARAM_DEFAULTS = Object.freeze({
  width: 1536,
  height: 1536,
  captionExtension: ".txt",
  //: The pipeline defaults, identical to the scaling panel's own initial control values.
  noUpscale: true,
  resample: "lanczos",
  format: "png",
  quality: 95,
  optimize: true,
});

//: Where the remembered parameters live. One key for the whole store, so a reload reads one entry.
export const TRAIN_PARAMS_STORAGE_KEY = "kdt.trainParams";

/**
 * The resample filters and output formats the backend accepts (p0-spec §4.14, pinned to
 * `scaler.RESAMPLE_NAMES` / `scaler.IMAGE_FORMATS` by test_web_scale.py).
 *
 * They live here because this module owns the pipeline settings: a **stored** name is validated
 * against them when it is read back, so a name this build does not know - an entry from an older
 * version whose list was shorter, or a hand-edited one - falls back to the default instead of
 * reaching the request and coming back as a 422.
 */
export const RESAMPLE_NAMES = Object.freeze([
  "lanczos", "bilinear", "bicubic", "nearest", "box", "hamming",
]);
export const IMAGE_FORMATS = Object.freeze(["png", "jpeg", "webp"]);

function positive(value, fallback) {
  const parsed = Number.parseInt(String(value), 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function boundedQuality(value, fallback) {
  const parsed = Number.parseInt(String(value), 10);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.min(100, Math.max(1, parsed));
}

function oneOf(value, allowed, fallback) {
  const text = String(value === undefined || value === null ? "" : value).trim();
  return allowed.includes(text) ? text : fallback;
}

/** The copy lives in the packs (no CJK here); the extension is free-form, but empty is not a value. */
function extension(value, fallback) {
  const text = String(value === undefined || value === null ? "" : value).trim();
  return text.length > 0 ? text : fallback;
}

/** localStorage, or null when the browser refuses it (private mode, a policy) - accessing it can throw. */
function defaultStorage() {
  try {
    return globalThis.localStorage || null;
  } catch (err) {
    return null;
  }
}

/**
 * The remembered parameters, or null when there is nothing usable there. **Never throws**: an
 * absent, unreadable or corrupt entry reads as "nothing stored", which is the same state as a
 * first visit - and the defaults are a working panel, so a broken key must not be a broken page.
 */
export function readStoredTrainParams(storage) {
  const store = storage === undefined ? defaultStorage() : storage;
  if (!store) return null;
  let raw = null;
  try {
    raw = store.getItem(TRAIN_PARAMS_STORAGE_KEY);
  } catch (err) {
    return null;
  }
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null;
    return parsed;
  } catch (err) {
    return null;
  }
}

/** Remembering is a convenience, never a requirement: a full quota must not break the panel. */
function writeStoredTrainParams(storage, values) {
  if (!storage) return;
  try {
    storage.setItem(TRAIN_PARAMS_STORAGE_KEY, JSON.stringify(values));
  } catch (err) {
    // A quota error, or a storage that refuses writes, must not break the panel it describes.
  }
}

/**
 * Build the store the panels share.
 *
 * `initial` is an explicit seed and **wins over what was remembered** (a caller that names a value
 * means it); `options.storage` is for tests, and defaults to `localStorage`.
 */
export function createTrainParams(initial, options) {
  const config = options || {};
  const storage = config.storage === undefined ? defaultStorage() : config.storage;
  const remembered = readStoredTrainParams(storage) || {};
  const seed = Object.assign({}, remembered, initial || {});
  let width = positive(seed.width, TRAIN_PARAM_DEFAULTS.width);
  let height = positive(seed.height, TRAIN_PARAM_DEFAULTS.height);
  let captionExtension = extension(seed.captionExtension, TRAIN_PARAM_DEFAULTS.captionExtension);
  //: The scaling panel's own switch (it adds a file; it cannot replace one, which is why its
  //: sibling `overwriteToml` is not stored - see the module header).
  let writeToml = seed.writeToml === true;
  //: The pipeline settings, seeded once and then owned by whoever's controls are on screen
  //: (the scaling panel). `!== false` is the whole convention: absent means "on".
  const processing = {
    noUpscale: seed.noUpscale !== false,
    resample: oneOf(seed.resample, RESAMPLE_NAMES, TRAIN_PARAM_DEFAULTS.resample),
    format: oneOf(seed.format, IMAGE_FORMATS, TRAIN_PARAM_DEFAULTS.format),
    quality: boundedQuality(seed.quality, TRAIN_PARAM_DEFAULTS.quality),
    optimize: seed.optimize !== false,
  };
  const listeners = new Set();

  function emit() {
    for (const listener of Array.from(listeners)) listener();
  }

  /** The one place a value reaches storage: the snapshot the next page load reads back. */
  function persist() {
    writeStoredTrainParams(storage, Object.assign({
      width,
      height,
      captionExtension,
      writeToml,
    }, processing));
  }

  return {
    /** [width, height] - the frozen two element shape of params.resolution. */
    resolution() {
      return [width, height];
    },
    captionExtension() {
      return captionExtension;
    },
    /**
     * The pipeline settings the crop panel inherits instead of owning a second copy.
     * Returns a copy: a caller that mutated the live object would bypass emit().
     */
    processing() {
      return {
        noUpscale: processing.noUpscale,
        resample: processing.resample,
        format: processing.format,
        quality: processing.quality,
        optimize: processing.optimize,
      };
    },
    /**
     * The scaling panel's own switch, remembered with the rest. `overwrite_dataset_toml` is
     * deliberately not here: it is a permission for one run, not a preference (module header).
     */
    scaleOptions() {
      return { writeToml };
    },
    setScaleOptions(patch) {
      const source = patch || {};
      if (source.writeToml === undefined || Boolean(source.writeToml) === writeToml) return;
      writeToml = Boolean(source.writeToml);
      persist();
      emit();
    },
    setResolution(nextWidth, nextHeight) {
      const w = positive(nextWidth, width);
      const h = positive(nextHeight, height);
      if (w === width && h === height) return;
      width = w;
      height = h;
      persist();
      emit();
    },
    setCaptionExtension(value) {
      const next = extension(value, "");
      if (next.length === 0 || next === captionExtension) return;
      captionExtension = next;
      persist();
      emit();
    },
    /**
     * Merge a patch into the pipeline settings. Unknown keys are ignored rather than
     * stored: this object is read by another panel, so a typo must not become a value
     * that silently changes what gets written.
     */
    setProcessing(patch) {
      const source = patch || {};
      let changed = false;
      if (source.noUpscale !== undefined && Boolean(source.noUpscale) !== processing.noUpscale) {
        processing.noUpscale = Boolean(source.noUpscale);
        changed = true;
      }
      if (source.optimize !== undefined && Boolean(source.optimize) !== processing.optimize) {
        processing.optimize = Boolean(source.optimize);
        changed = true;
      }
      // A name this build does not offer is not a value it can honour: the domain is the same one
      // the stored entry is validated against, so a typo cannot become something the panel shows
      // in an empty <select> and the request carries as-is.
      if (source.resample !== undefined) {
        const next = oneOf(source.resample, RESAMPLE_NAMES, processing.resample);
        if (next !== processing.resample) {
          processing.resample = next;
          changed = true;
        }
      }
      if (source.format !== undefined) {
        const next = oneOf(source.format, IMAGE_FORMATS, processing.format);
        if (next !== processing.format) {
          processing.format = next;
          changed = true;
        }
      }
      if (source.quality !== undefined) {
        const next = boundedQuality(source.quality, processing.quality);
        if (next !== processing.quality) {
          processing.quality = next;
          changed = true;
        }
      }
      if (changed) {
        persist();
        emit();
      }
    },
    /** Returns an unsubscribe function. */
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
}
