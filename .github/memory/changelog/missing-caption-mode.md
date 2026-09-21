# Missing-caption mode

**When to read this**: changing the gallery toolbar, the filter panel's conditions, or anything that
decides what is visible and therefore what the batch panels will write to.

## The decision (2026-09-20)

The selection is always a subset of what is on screen: `selected ⊆ visible = filter result`.
Two ends of that one invariant were agreed in one conversation and shipped together:

1. **"Select images missing a caption" is a mode, not a second filter layer.** A tag filter talks
   about images that *have* captions; the button talks about the ones that do not. The domains are
   disjoint, so stacking them could only produce a selection full of images nobody can see - which
   the batch panels would then happily write to. Entering the mode **is** the mutual exclusion: the
   handler sets the mode, recomputes what is visible, and only then selects (`app.js`). Explicit
   exclusion beats greying the button out: a dead button teaches nothing.
2. **Pruning stays.** `setVisiblePaths` deleting selected paths the filter hides is the intended
   spec, not a defect. The earlier proposal to make filtering purely visual (selection untouched)
   was rejected - it would leave a selection that is not what is on screen.

## The defect this also fixed

`matchesFilter` is a pure predicate over a tag list, so an image with **no tags at all** satisfied an
`exclude`-only state: a "does not contain X" view listed images that have no caption. That decision
now lives in `apply()`, which knows about images rather than tag lists - with any tag condition
present, an empty tag list is out of the domain. Pending/failed probes (`null` tags) still stay
visible on purpose: a slow probe must never hide a tile.

## Evidence

- `test/fixtures/filter_harness.mjs` really runs the panel over three images (no caption / tag `y` /
  tag `x`): `exclude=x` keeps `y` **and drops the caption-less one**; missing mode shows only the
  caption-less image, reports itself through `hasFilter()` and `getState()`, names itself on the count
  line, and any tag click leaves the mode. (Since 2026-09-20 the mode names itself as a removable chip in
  the gallery strip and the count line is only the count - [changelog/gallery-filter-strip](gallery-filter-strip.md).)
- `test/test_web_filter.py`: the button must set the mode *before* `applyFilter()` and select *after*
  it; `gallery.selectMissing()` (the old select-across-the-directory call) must not come back; the
  panel must be told which images have no caption; all three packs can name the mode.
- **Falsified** (break -> red -> restore, each one): dropping the caption-less domain rule -> the
  harness throws on `exclude=x`; making the mode stop narrowing -> the harness throws on the
  caption-less image; making the button set the mode to `false` -> the wiring criterion goes red.
  Restored: harness OK, `13 passed`.

## Known limit

"Has no caption" is the gallery's `hasCaption === false`. An image whose caption file exists but is
empty counts as captioned and is therefore not offered by the mode - which matches the badge it
already shows.
