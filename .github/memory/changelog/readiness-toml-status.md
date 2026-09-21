# The readiness strip stops shrugging about dataset.toml

**When to read this**: changing the readiness strip's third segment, `web/js/readiness.js`, the
`dataset.toml` status endpoint (p0-spec §4.16), or anything that decides what the app knows about the
dataset root's file.

## Why (2026-09-20)

The readiness strip shipped the day before with one honest hole in it. Two of its three segments
counted something real - images with no caption, stale text-encoder caches - while the third could
only say `dataset.toml 未检查`, because **no endpoint could tell the app whether the file exists**.
That is the most important of the three questions ("is this dataset trainable?"), and the answer was
a shrug.

**The premise check** (asked for, and worth doing: the previous round was dispatched on a premise
that turned out false). All three claims were verified in the code, not from memory:

1. `POST /api/dataset/toml` is pure computation and never reads the file - `api/dataset.py` says so
   in its docstring and its body only calls `dataset_toml.plan_dataset_toml`.
2. `dataset_toml_skipped` is §4.14's **export** summary field: it describes a run that already
   happened (`api/scale.py:_write_dataset_toml`), not the state of the dataset root.
3. `app.js` holds the dataset root in `state.root` (`openDir`), and the file's name was already
   defined once, as `DATASET_TOML_NAME` in `api/scale.py`.

**One refinement of the premise.** The export panel's subject is `state.dir` - `openDir()` calls
`exportPanel.setRoot(state.dir)` - not `state.root`. So the old segment's report described the
directory on screen, and at a subdirectory it described a *different file* from the one the trainer
reads. "The subject is the dataset root" therefore had to fix a subject, not merely add a fact.

## The decision

### One read-only stat: `GET /api/dataset/toml/status?root=` (§4.16)

`{root, path, exists, mtime, size}`. The root is resolved through `resolve_under_roots` like every
other path parameter (403 out of bounds / no allowlist, 404 missing, 400 not a directory); `exists`
means **a regular file** is there (`stat.S_ISREG`), which is what the trainer's `open()` needs;
`mtime` / `size` are the provenance of the fact and are `null` when it is absent. **The contents
are never returned.**

Read-only, and provably so: one `os.stat` is the whole interaction with the filesystem. The file is
never opened, a missing one is **not created** (that would answer the next question with a lie), and
nothing is backed up, touched or renamed. The explicit `stat` rather than `Path.is_file()` is
deliberate: `is_file()` swallows `OSError`, which would quietly turn "the root cannot be read" into
"no dataset.toml" - so a stat failure is 500 `stat_failed` instead.

`DATASET_TOML_NAME` moved into `core/dataset_toml.py` (the module that generates the file), and
`api/scale.py` now names it through that constant: the export, the status endpoint and the frontend
download are three surfaces describing **one** file, and two spellings of the name would let them
drift apart with nothing to notice.

### The segment: the fact gates the claim, the report may only make it worse

| state | copy | meaning |
|---|---|---|
| no answer yet | `readiness.tomlUnchecked` | nothing was asked, or the dataset changed under the held answer |
| the request failed | `readiness.tomlUnread` | "could not ask" is never "not there" - the cache segment's rule |
| `exists: false` | `readiness.tomlMissing` | the dataset root has no `dataset.toml` - a fact now, not a guess |
| `exists: true` | `readiness.tomlReady`, or the export panel's own `export.warningsTitle` / `export.skippedTitle` / `readiness.tomlInconsistent` | the file is there; the panel's report is what is left that can be wrong with it |

- **A clean export report can never produce `dataset.toml 就绪` on its own.** The export endpoint
  writes nothing, so "the checks passed and there is no file on disk" is reported as 未生成. That
  disagreement is the point of the round, and both the harness and the accepting side's own probe
  score it.
- The report counts only while `samePath(report.root, fact.root)`: the panel describes the directory
  it was pointed at, the fact describes the dataset root, and a report for another directory is not
  evidence about this file. This is also what keeps a nested subdirectory from answering for its
  dataset.
- `readiness.js` still fetches nothing: `setTomlFile(fact)` is handed in by `app.js`, so the strip
  remains a painter of values other surfaces own.

### Where the fact is asked for again (and why it can never be guessed)

Four refresh sites, one per way the file can appear or vanish under the app:

- **opening a directory or a root** - `openDir()` re-asks on every navigation, so a toml written by
  hand, by the trainer or by an earlier session is seen immediately. A held fact belonging to another
  root is dropped before the new answer arrives (the same rule that empties the export panel's report).
- **the 15 s external-change poll** - the poll's fingerprint is "image count + newest mtime", and a
  `dataset.toml` holds no images: at the dataset root there are *no* images at all, so the file can
  appear without the fingerprint ever moving. The tick therefore re-asks the fact (a stat, not a
  re-list), and it does so *before* the poll's three "do not reload" guards, which exist to protect
  the editor from `openDir()` - asking damages nothing.
- **a finished export** - the scale panel is the one job in this app that writes a `dataset.toml`,
  into its target root. `target_inside_root` forbids a target *inside* the source but not the
  source's parent, so the target can be the dataset root itself while the user browses a
  subdirectory. The panel's single job-end function (`finishJob`) now calls an `onFinished` option,
  and `app.js` re-reads rather than assuming a file appeared.
- **a click on the segment** - it still opens the export tab (where the toml is generated, read and
  copied) and now also re-asks. That is what gives the failed state the cache segment's recovery:
  the click is the retry.

`refreshTomlStatus()` never sets a state optimistically: a write is followed by a stat, and a
late answer from a previous root is discarded (a token, and the root the fact belongs to).

## Evidence

**Backend** - 12 criteria in `test/test_dataset_status_api.py` (TestClient over the real app, real
files in a tmp sandbox): the absent/present stat with the file's own mtime and size, the subject
(a nested directory answers about its own file), the empty-root fallback, a directory named
`dataset.toml` not counting as a file, **the read-only property scored as a whole-tree
`(size, mtime_ns)` fingerprint before and after every call in both states**, no `.bak`, a failed
stat being 500 `stat_failed` rather than "not there" (and recovery on the next call), the 403/404/400
codes, the fail-closed empty allowlist, and the name being defined exactly once in the whole backend.

**Acceptance** - **A43** scores the chain end to end against the real app (`exists` flips with the
real stat, both facts in order, a directory fingerprint around every call, 403 out of bounds), and
**A43b** runs an **independent node probe** (written by the accepting side, not the implementer's
fixture) over the real `readiness.js`: a failed request paints its own wording, a clean report with
no file paints 未生成, another directory's report is not attached, and the same root's report is
reused in the panel's own words.

**Frontend, headless** - `test/fixtures/readiness_harness.mjs` drives the real module through
unchecked -> failed -> missing -> (clean report, still missing) -> exists -> foreign report ignored ->
warning/skipped/inconsistent -> ready -> deleted again, plus the relabel of the new states;
`test/fixtures/scale_harness.mjs` section 9 drives the real panel and pins that the ending is
announced **once**, only on `finished:true`, and carries the job's own summary.

**Frontend, static** - `test/test_web_readiness.py`: the missing-file wording is painted from exactly
one branch, the not-exists branch returns before the report is consulted, "ready" is unreachable
before both, the failure wording differs from the missing wording in all three packs, the fact is
asked for `state.root` (never `state.dir`), no code path writes `exists: true`, and the four
refresh sites exist.

**A real browser** - `tools/measure_web_layout.py` gained a canned
`/api/dataset/toml/status` answer and a `--toml` flag. Headless Chrome, the real `index.html`:

| run | third segment on all six tabs | after clicking Generate |
|---|---|---|
| default (no file) | `dataset.toml 未生成` [is-todo] | **still `dataset.toml 未生成`** - the panel reported its plan (1 warning), the disk has no file |
| `--toml` | `dataset.toml 就绪` [is-ok] | `警告 1 条（训练器会静默接受）` [is-todo] - the panel's own heading, now gated on the file existing |
| `--toml --root-images 0` | `dataset.toml 就绪` while the caption segment is hidden and empty (a real, folder-only dataset root) | - |

0 uncaught errors, 0 unhandled rejections, and every `/api/*` request answered by the canned API.

**Falsified 18 ways** (break -> red -> restore -> green, each observed):

| break | red |
|---|---|
| the status endpoint creates the file it was asked about | `test_asking_never_writes_creates_or_touches_anything` **and A43** |
| `is_file()` instead of the explicit stat | `test_a_failed_stat_is_not_reported_as_a_missing_file` |
| `scale.py` gets its own `DATASET_TOML_NAME` literal back | `test_the_file_name_is_defined_once_in_the_whole_backend` |
| the endpoint claims `exists: true` always | three backend criteria |
| the report is consulted before the fact | the static criterion, the harness **and A43b** |
| a failed request falls back to the missing claim | the harness **and A43b** |
| `openDir()` stops re-asking | the refresh-site criterion |
| the fact is asked for `state.dir` instead of `state.root` | same criterion |
| the app assumes `exists: true` instead of asking | same criterion |
| the external-change poll stops re-asking | same criterion |
| the report is attached without the same-root check | the harness **and A43b** |
| a finished export announces nothing | `scale_harness.mjs` (exit 1) and the wiring criterion |
| the ending is announced from a progress event too | `scale_harness.mjs` (exit 1) |
| the ja pack renames the failure key | `test_web_contract` (key parity + placeholders) |
| the failure and missing states share one sentence | the wording criterion and the harness |
| the probe's canned status defaults to "the file is there" | `test_the_canned_dataset_toml_status_has_both_states` |
| the download saves under a different name than the trainer reads | `test_the_file_name_the_strip_reports_is_the_name_the_download_saves` (added after the copy was reworded, and falsified again) |
| the failure and missing states collide (re-run after the copy became `检查失败` / `の確認に失敗`) | the wording criterion and the harness |

## Judgement calls

1. **The export panel's report is kept, but demoted.** Dropping it would lose the warning number the
   previous round shipped; the task asked that the two surfaces *cannot drift*, which needs both of
   them present and ordered. The fact gates, the report may only downgrade.
2. **A report for another directory is ignored by the segment.** At a subdirectory the panel's report
   is about that subdirectory's plan - genuinely useful on the panel, and not evidence about the
   dataset root's file. The strip therefore shows the root's file state and no warning count there.
3. **`就绪` with no report claims existence, not validity.** The panel's checks are a check of the
   *plan*; the strip's claim is that the file the trainer reads is there. The pre-existing key was
   reused rather than renamed.
4. **The segment's click re-asks as well as opening the export tab.** The shipped action survives in
   every state, and the failed state gains the recovery the cache segment has. The criterion that
   pinned the old one-line handler was rewritten to follow the code, not weakened.
5. **`exists` means a regular file.** A *directory* named `dataset.toml` reads as "not generated",
   because it is not a dataset the trainer can read - pinned by a criterion.
6. **`mtime` / `size` are returned but not painted.** They are the fact's provenance: they are what
   makes "this is a real stat and not a canned guess" assertable, which A43 does.

## Known limits

- **The fact is as fresh as the last ask.** Up to 15 s if the file appears while the app sits idle
  (that is the poll's period); immediate on navigation, on a finished export, and on a click. There is
  no watcher, for the same reason the gallery has none. The ask sits **before** the poll's three
  reload guards, so it also happens in a background tab - it is one stat, and the health pill has
  polled every 10 s regardless of visibility since 2026-09-19.
- **The report's own staleness is unchanged.** The panel's report can be older than the directory
  (known limit of the previous round); what changed is that its numbers can no longer stand in for
  the file's existence.
- **One more stat per poll tick** (~1 ms) and one per navigation / click. Nothing was batched into
  `/api/fs/list`: the listing's subject is the directory on screen, and this fact's subject is the
  dataset root.
- **The status endpoint's failure state was not driven in a real browser.** The canned API always
  answers 200 for it; the wording is covered by the harness and by A43b, and the failed
  `/api/cache/status` remains the browser-measured example of the same rule.

---

Related: [readiness-strip-and-layout-probe](readiness-strip-and-layout-probe.md),
[scale-toml-safety](scale-toml-safety.md), [i18n](../i18n.md), [ui-copy](../ui-copy.md)
