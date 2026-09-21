# What this changes

<!-- One paragraph. Say what a user can now do (or no longer breaks doing) that they could not before. -->

## Which layer

<!-- Tick the one that applies; the layer decides which memory topic file must be updated with it. -->

- [ ] Backend: a module under `src/kohya_dataset_tagger/` (api / core / autotag)
- [ ] Frontend: `src/kohya_dataset_tagger/web/` (native ES modules, no build step)
- [ ] UI copy: a locale pack under `web/js/locales/` (all packs, identical keys and placeholders)
- [ ] Launchers / setup scripts (`*.bat`, `*.sh`, `scripts/`)
- [ ] Documentation or CI only (no behavior change)

## Checks

<!-- Paste the real command and its real result. "It works" is not a result. -->

- [ ] `python tools/check_relevant.py --full` is green (whole suite + documentation gate)
- [ ] New or changed criteria were falsified: deliberately broken -> confirmed red -> restored
- [ ] The normal, the failure and the recovery path were all exercised
- [ ] Behavior/parameter change -> the matching `.github/memory/` topic file says so too
- [ ] A new topic file has a row in its `INDEX-<category>.md`

## Hard constraints

- [ ] A caption write still invalidates the text encoder cache in the same request
- [ ] Every user-supplied path still goes through `core/paths.py`
- [ ] Image files in the dataset are still never modified (the crop archive beside the source is the one addition)
- [ ] All disk writes are still atomic (temp file + `os.replace`)

## Notes for the reviewer

<!-- What you are unsure about, what you deliberately did not do, what you had to guess. -->
