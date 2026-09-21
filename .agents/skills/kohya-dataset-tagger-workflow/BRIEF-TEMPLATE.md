# Dispatch brief template

Copy everything below the horizontal rule into the implementer's prompt and fill the `<...>` slots.
It exists because four rounds of this repository were dispatched on premises the code contradicted, and
because a brief that omits one of these blocks reliably produces a report the accepting side cannot
accept. The reasoning behind each block is in [design-principles](../../../.github/memory/design-principles.md)
under "The process paradigm".

---

You are working in the repository root (Python FastAPI backend + build-free
native ES-module frontend: a dataset tagger for kohya-style training sets). Work on the current working
tree. **Do not commit** - leave the changes in the tree and report; the parent reviews and commits.

## Read first, in this order

1. `AGENTS.md` - architecture, hard constraints, the gate rules.
2. `.github/memory/design-principles.md` - the paradigm, the invariants, the process. What the parent will
   check your change against.
3. `.github/memory/current-state.md` - item `<n>` of "Suggested next steps" is this task.
4. `<the two or three changelogs of the rounds this one extends - they carry the pattern you are repeating
   and the invariants you must not break>`
5. `.github/memory/ui-copy.md` before writing or changing any user-visible string.
6. If your skill catalog has `kohya-dataset-tagger-workflow`, load it; its Step 1 and Step 2 are the loop.

Read the actual code; do not trust memory.

## Premise check (before writing code, and answer it in your report)

<State the premise as facts to verify: "X exists and does Y", "the panel already handles Z".>

Confirm or refute each with `file:line` evidence. **If I am wrong, say so and propose the corrected task**
- that is a good answer, not a failure. Three of the last six rounds here were dispatched on a premise the
code contradicted.

## The task

<One paragraph: what exists today, what must be true when you are done, and what the user can then do that
they cannot do now. Name the surfaces involved.>

## Non-negotiable constraints

- <task-specific invariants, each with its reason>
- The invariants in `design-principles.md`: one live copy; the control lives where its effect is visible;
  one number, one source; unavailable is shown with its reason; no privileged paths (a new surface calls the
  same function the existing control calls); copy only in the locale packs, render-time strings registered
  for relabel.
- No backend change <unless the task is a contract change - then say so and name `p0-spec.md` + `acceptance/`>;
  no new dependency; no build step; minimal diff, no reformatting of code you are not changing.

## Blast radius (check every one, and list in your report which you changed)

<the tests, harnesses, fixtures and doc sentences that name the modules this touches>

## Tests and evidence

- Inner loop while iterating: `python tools/check_relevant.py` (seconds). The gate, once before reporting:
  `python tools/check_relevant.py --full`.
- **A criterion only counts if it can fail**: for every one, break the code, confirm red, restore, confirm
  green, and report the observation. This repository has repeatedly caught criteria that were green for the
  wrong reason - look for yours before the accepting side does.
- Cover the normal, failure and recovery paths.
- Name the **evidence class** for every claim: *measured in a real browser*
  (`tools/measure_web_layout.py` serves the real page over canned `/api/*`), *read from CSS and a fake DOM*,
  or *not verified*. A report that blurs them is worse than one that admits a gap.
- Two traps this repository has already paid for: never rebuild a file from `read` output (it truncates
  very long lines and once silently deleted five tests); and a restore anchor must be **unique** in the
  file (two rounds lost time to anchors that matched several times).

## Documentation (same round, or the gate turns red)

- `.github/memory/changelog/<short-name>.md` in the neighbours' style: **When to read this** / why / the
  decision / evidence / falsification / known limits.
- One row in `.github/memory/INDEX-history.md`, matching the existing table shape.
- `.github/memory/current-state.md`: a "Built and verified" row, the gate numbers, and the item you shipped.
- The topic file the change belongs to (sync it), or a new one plus its `INDEX-<category>.md` row.
- `python -m pytest test/test_docs_index.py -q`.

## Danger

Never write outside the repository; never write to a dataset directory (tests use `tmp_path` and fixtures).
Do not run `git commit`, `git checkout`, `git reset`, or delete files. <task-specific scratch rules>

## Report back (what gets accepted or rejected)

1. Files changed, with `git diff --stat`.
2. What you reused rather than reimplemented, and the evidence that the relevant invariant holds.
3. Every criterion added, with its falsification observation; say plainly if one stayed green when it
   should have failed.
4. The exact gate output lines.
5. Every judgement call you want confirmed, and anything you could not verify.

---

## How the accepting side uses this (not part of the copy)

1. **Re-run the gates yourself**; never accept the report's numbers.
2. **Re-do at least one falsification by hand**, on the property you care most about - the report's
   falsifications are claims like any other.
3. Read the diff: minimal, single-subject, the invariants intact, copy identical in the three packs.
4. If the premise turned out to be wrong, fix the brief rather than blaming the report.
5. Commit with a message that carries the *why*, and record what stays unverified.
