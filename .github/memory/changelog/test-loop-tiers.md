# Test-loop tiers: relevant tests while iterating, the whole gate once

**When to read this**: changing the gates, adding tests, or wondering why a round takes minutes.

## Why (2026-09-20)

Measured on this machine: the unit suite is **979 criteria in ~80 s**, and about a quarter of that is
**ten tests doing real work** - ONNX inference (8.1 s), a live HuggingFace endpoint (5.1 s), the
end-to-end HTTP shape (3.2 s), a real dataset scan (1.3 s). Meanwhile a round ran the whole suite two to
four times: the implementer before and after its edits, and the accepting side again. The criteria are
not slow; running all of them after every edit is.

## The decision

Two tiers, one command each:

- **Inner loop** - `python tools/check_relevant.py`: takes the changed files (tracked and untracked) and
  runs the tests and node harnesses that *name* them, plus the family nets (`test_web_contract.py` for
  anything under `web/`, `test_api_e2e.py` for anything under `src/`, `test_web_i18n.py` for a locale
  pack or `strings.js`, `test_web_a11y.py` for markup or CSS). A frontend change costs ~1.5 s instead of
  80 s; `--dry-run` prints the plan and runs nothing.
- **The gate, once** - `python tools/check_relevant.py --full`: the whole suite plus the documentation
  gate, and `acceptance/` when the frozen contract moved - the rule AGENTS.md already states.

Selection is **evidence-based, not a hand-written map**: a test counts as cover when its text names the
changed file, so a new test picks itself up and a renamed module cannot fall out silently. Node harnesses
count as cover too - a module exercised only through a fixture would otherwise be called uncovered while
the one thing that runs it is skipped.

**Anything no test and no harness names is printed as UNCOVERED** - the tool's most important line. A
selector's dangerous failure is not running too much; it is running too little and saying nothing.

## Evidence

- `test/test_check_relevant.py` - 10 criteria over a temporary tree: a module selects its own test and the
  contract net; a locale pack adds i18n; markup and CSS add a11y; a backend module selects what imports it
  plus the end-to-end net; docs route to the docs gate and the frozen contract to acceptance; an
  unmentioned source file is reported as UNCOVERED; a changed test or harness is selected directly; a
  module covered only by a harness is covered; every command the tool builds points at a file that exists;
  `--dry-run` runs nothing.
- **Falsified five ways**: dropping the UNCOVERED report -> red; matching the bare stem instead of the file
  name -> red (`app.js` dragged in every test that says "app"); handing the docs gate its bare file name ->
  red; ignoring fixture cover -> red; a common-word stem -> red.
- Two of those were found *by* the falsification rather than by review: the bare-stem rule only failed once
  a criterion with a common-word stem existed, and the docs gate was unreachable from `--full` until a
  criterion asserted that every command points at a real file.

## The gate itself is parallel now (2026-09-20)

`pytest-xdist` was added as a **test-only** dependency (`pyproject.toml`'s `test` extra and
`requirements.txt`), and `--full` uses `-n auto` **when the plugin is present** - the same command still
works on a machine without it. Measured back to back on one tree (1005 criteria): **74.5 s serial vs
25.2 s parallel**, identical results (1005 passed, 2 skipped). That the whole suite passes under sixteen
workers at all is itself evidence about the criteria: none depends on another's leftovers or on the order
they run in. Warnings are repeated per worker (2 become 32) - noise, not a difference. The inner loop stays
serial: process startup costs more than the seconds it would save.

## Where it is documented

`AGENTS.md`'s command block (two lines, no detail), and Step 2 of the `kohya-dataset-tagger-workflow` skill
- which is what an agent reads before running anything: it now says do not run the whole suite while
iterating, and run it once before reporting.

## Known limits

- The selector is a **heuristic superset**: a test that exercises a module without naming it (through a
  generic runner) is missed, and a markup or stylesheet change fans out to the whole frontend family by
  design. That is why the gate stays mandatory rather than optional.
- It reads the working tree, so a change made *during* a run is not in the plan; re-run it.
- `--full` goes parallel only when `pytest-xdist` is installed (see above); without it the suite runs
  serially in ~75 s.
