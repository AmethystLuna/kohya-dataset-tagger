---
name: remembered-panel-parameters
description: 2026-09-21, the scaling panel's parameters survive a reload - the shared store (trainparams.js) reads one localStorage entry when it is built and writes it whenever a setter accepts a value, so the resolution / caption extension / resample / format / quality / optimize / no-upscale and "generate dataset.toml" come back on the next visit, while the target directory and overwrite_dataset_toml deliberately do not and a remembered name this build no longer offers falls back to the default
metadata:
  type: topic
---

# The parameters remember themselves (2026-09-21)

## The report

"这个缩放参数需要持久化，不能每次打开网页都恢复成默认值" - the scaling panel came back as the defaults on
every page load, so the resolution, the format and the quality were re-entered on every visit.

Nothing was remembering them. `web/js/trainparams.js` was an in-memory observable - the shared source of
truth for the resolution and the caption extension, read by the §4.9 export panel, the §4.14 scaling panel
and the §4.17 crop panel - while the theme, the language, the panel widths and the caption dock's fold each
remembered themselves. The one set of values a user retypes every session was the one nobody stored.

## The change

- **The store remembers**, because the store is what owns the values: one key (`kdt.trainParams`), read once
  in `createTrainParams()` and written by a single `persist()` whenever a setter accepts a value. There is no
  second copy to keep in sync, and a panel that reached for storage itself would be exactly the drift this
  store exists to prevent (a criterion forbids `localStorage` in `scale.js` / `crop.js`).
- **The remembered field list is explicit** - resolution, caption extension, the pipeline settings
  (resample / format / quality / optimize / no-upscale) and the scaling panel's own `writeToml` - and a
  criterion pins it, because the interesting part is what is **not** in it:
  - the **target directory**: a stored destination is a location nobody chose in this session, and the export
    would write into last week's folder without anyone saying so;
  - **`overwrite_dataset_toml`**: a standing permission to replace a hand-tuned file is exactly what
    "dataset.toml is never silently replaced" is about. It is a decision for one run; the panel offers it per
    run (disabled while generation is off), and the run that skips the file reports it.
- **A remembered name is validated against the domain.** `RESAMPLE_NAMES` / `IMAGE_FORMATS` moved into the
  store (they are its own settings' domain, and a criterion still pins them to the backend's
  `scaler.RESAMPLE_NAMES` / `scaler.IMAGE_FORMATS`), and both the stored seed and `setProcessing()` accept a
  name only when it is in the list. Without that, an entry written by an older build whose list was shorter
  would land in a `<select>` with no such option - the control reads empty and the request carries the dead
  name to a 422 - and the crop panel, which inherits the same settings, would send it too.
- **Bad storage is not a broken page**: an absent, corrupt, foreign (`null`, `[]`, a bare string) or
  unreadable entry reads as "nothing stored" (the defaults are a working panel), and a storage that throws on
  read or write (private mode, a full quota) leaves the store working.
- **An explicit seed still wins over the memory** (`Object.assign({}, remembered, initial)`): a caller that
  names a value means it, and the tests that seed a store keep working.

## Evidence

- `test/fixtures/scale_harness.mjs` section 2b: build the store twice over one fake `localStorage` and compare
  (the reload case), an explicit seed beating the memory, five kinds of corrupt entry, and a hostile storage.
- `test/fixtures/scale_harness.mjs` section 10: seed **only** storage, construct the panel, and read the
  remembered values off the controls the user reads (the two resolution fields, two selects, four switches,
  the caption extension input) and then out of the request body the panel builds - including
  `overwrite_dataset_toml: false`, the permission that must never come back.
- `test/test_web_scale.py::test_the_parameters_are_remembered_and_the_permissions_are_not`: the static half -
  the key, the reader, the writer, the precedence line, the snapshot's field list, `persist()` in every
  setter, and no panel owning storage of its own.

## Falsification (what was deliberately broken, and what turned red)

1. **`persist()` turned into a no-op**: "the resolution did not survive the reload".
2. **`scaleOptions()` returning the permission too**: "the destructive opt-in was remembered: a permission
   for one run is not a preference".
3. **The stored seed trusting any name** (no domain check): "a name this build does not have was kept".
4. **`overwriteToml` added to the persisted snapshot**: the static criterion - "the remembered field list
   changed - is the new field a parameter, or a permission?".

Each break was restored and the affected criteria re-run green (harness OK; test_web_scale.py 14 passed).

## Known limits

- **The target directory is not remembered**, deliberately (above). A user who exports into the same folder
  every day names it again - the one field that stays a decision per run.
- **Nothing is scoped per dataset**: the parameters are machine-wide, like the theme and the layout widths, so
  a second dataset with a different training resolution starts from the first one's value.
- **One `setItem` per accepted value, no debounce** - including every keystroke in the resolution field. Not
  measured beyond its obvious order of magnitude (a short JSON string); if it ever shows up in a profile the
  fix is a debounce inside `persist()`, not a per-panel cache.
- **The store is not the whole panel**: the scope strip's value, the target directory and the running job are
  session state by design, so "open the page and everything is where I left it" is true of the parameters, not
  of the workspace.
