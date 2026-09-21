"""Pick the tests that cover what you just changed, so the inner loop stays seconds long.

Why this exists
---------------
Running the whole suite after every edit wastes minutes: 979 criteria, of which about ten do real work
(ONNX inference, a live HuggingFace endpoint, a real dataset scan) and account for a quarter of the
wall clock. While iterating, what needs to run is the tests that mention the module you touched, plus
the cross-cutting nets that guard the invariants of its family. The full gate then runs ONCE, before
reporting.

    python tools/check_relevant.py              # inner loop: only what covers the change
    python tools/check_relevant.py --dry-run    # print the plan, run nothing
    python tools/check_relevant.py --full       # the gate: whole suite + docs (+ acceptance if the contract moved)

Selection is evidence-based, not a hand-written map: a test is relevant when its text mentions the
changed file's stem, so a new test picks itself up and a renamed module cannot fall out silently.
Anything a change touches that no test mentions is reported as UNCOVERED rather than quietly dropped.
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WEB = REPO / "src" / "kohya_dataset_tagger" / "web"
#: The frontend tree as a repo-relative posix prefix: what select() classifies against.
WEB_PREFIX = WEB.relative_to(REPO).as_posix()
TESTS = REPO / "test"
FIXTURES = TESTS / "fixtures"

#: Nets that guard a whole family rather than one module. A change inside a family always runs its
#: net, even when no test mentions the changed file: these are the invariants that break silently
#: across modules (copy parity and the no-CJK rule; the end-to-end HTTP shape).
CONTRACT_TEST = "test_web_contract.py"
I18N_TEST = "test_web_i18n.py"
A11Y_TEST = "test_web_a11y.py"
E2E_TEST = "test_api_e2e.py"
DOCS_TEST = "test_docs_index.py"
#: The docs gate as a path pytest can be handed from the repository root.
DOCS_TEST_PATH = "test/" + DOCS_TEST


def _mentions(text: str, name: str, stem: str, is_python: bool) -> bool:
    """True when a test names this file.

    The file NAME is the token, not the bare stem: a stem like app or tags is an English word that
    appears in half the suite, and matching it would select everything. A Python module is also
    matched when a single line both imports something and names the stem, which is how backend tests
    reach a module without writing its file name.
    """
    word = "(?<![A-Za-z0-9_])" + re.escape(name) + "(?![A-Za-z0-9_])"
    if re.search(word, text) is not None:
        return True
    if not is_python:
        return False
    stem_word = "(?<![A-Za-z0-9_])" + re.escape(stem) + "(?![A-Za-z0-9_])"
    for line in text.splitlines():
        if re.search("\\b(import|from)\\b", line) and re.search(stem_word, line):
            return True
    return False


def _index(directory: Path, pattern: str) -> dict:
    """Every test module's (or harness's) text, keyed by file name."""
    out = {}
    for path in sorted(directory.glob(pattern)):
        try:
            out[path.name] = path.read_text(encoding="utf-8")
        except OSError:
            continue
    return out


def _harnesses(tests_dir: Path) -> dict:
    """The node harnesses: they really import the modules, so they count as cover too."""
    return _index(tests_dir / "fixtures", "*_harness.mjs")


def changed_files(base: str, repo: Path = REPO) -> list:
    """Tracked modifications since base plus untracked files, as repo-relative posix paths."""
    out = set()
    for args in (["diff", "--name-only", base], ["ls-files", "--others", "--exclude-standard"]):
        proc = subprocess.run(
            ["git", *args], cwd=str(repo), capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
        if proc.returncode != 0:
            continue
        for line in proc.stdout.splitlines():
            line = line.strip()
            if line:
                out.add(Path(line).as_posix())
    return sorted(out)


def select(changed: list, tests_dir: Path, web_prefix: str) -> dict:
    """The plan for a set of changed paths, given the repo-relative prefix of the frontend tree.

    Pure apart from reading the test tree, so the selection rules can be tested on a tmp tree.
    """
    index = _index(tests_dir, "test_*.py")
    fixture_index = _harnesses(tests_dir)
    tests = set()
    harnesses = set()
    docs = False
    acceptance = False
    uncovered = []
    web_prefix = web_prefix.strip("/") + "/"

    def add_mentioning(name: str, stem: str, is_python: bool) -> int:
        found = 0
        for test_name, text in index.items():
            if _mentions(text, name, stem, is_python):
                tests.add(test_name)
                found += 1
        #: A module covered only through a fixture is covered: the harness imports it for real.
        for harness_name, text in fixture_index.items():
            if _mentions(text, name, stem, is_python):
                harnesses.add(harness_name)
                found += 1
        return found

    for rel in changed:
        path = Path(rel)
        posix = path.as_posix()
        stem = path.stem

        if posix.startswith("test/fixtures/") and posix.endswith(".mjs"):
            harnesses.add(path.name)
            continue
        if posix.startswith("test/") and posix.endswith(".py"):
            tests.add(path.name)
            continue
        if posix.startswith("acceptance/"):
            acceptance = True
            continue
        if posix.endswith(".md"):
            docs = True
            if "p0-spec" in posix:
                acceptance = True
            continue

        under_web = posix.startswith(web_prefix)
        under_src = posix.startswith("src/")
        found = (
            add_mentioning(path.name, stem, posix.endswith(".py"))
            if (under_web or under_src or posix.startswith("tools/"))
            else 0
        )

        if under_web:
            tests.add(CONTRACT_TEST)
            if "locales/" in posix or path.name == "strings.js":
                tests.add(I18N_TEST)
            if posix.endswith(".html") or "/css/" in posix:
                tests.add(A11Y_TEST)
        if under_src and posix.endswith(".py"):
            tests.add(E2E_TEST)
        if (under_src or posix.startswith("tools/")) and found == 0:
            uncovered.append(rel)

    existing = {name for name in tests if (tests_dir / name).exists()}
    return {
        "tests": sorted(existing),
        "missing": sorted(tests - existing),
        "harnesses": sorted(harnesses),
        "docs": docs,
        "acceptance": acceptance,
        "uncovered": sorted(uncovered),
    }


def plan_lines(plan: dict, changed: list) -> list:
    out = ["changed: %d file(s)" % len(changed)]
    out.append("tests: %s" % (", ".join(plan["tests"]) or "(none)"))
    if plan["harnesses"]:
        out.append("harnesses: %s" % ", ".join(plan["harnesses"]))
    if plan["docs"]:
        out.append("docs gate: yes")
    if plan["acceptance"]:
        out.append("acceptance: yes (the contract moved)")
    if plan["missing"]:
        out.append("MISSING test files (named but absent): %s" % ", ".join(plan["missing"]))
    if plan["uncovered"]:
        out.append("UNCOVERED (no test mentions these - only the full suite covers them): %s" % ", ".join(plan["uncovered"]))
    return out


def jobs() -> list:
    """The parallel flag, when pytest-xdist is installed.

    Measured on this machine: 1005 criteria in 74.5 s serially and 25.2 s with -n auto, same result.
    It is offered only when the plugin exists, so the same command works on a machine without it.
    The inner loop stays serial: process startup costs more than the few seconds it saves there.
    """
    if importlib.util.find_spec("xdist") is None:
        return []
    return ["-n", "auto"]


def run(cmd: list) -> int:
    print("$ " + " ".join(cmd), flush=True)
    return subprocess.call(cmd, cwd=str(REPO))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run only the tests that cover the current change.")
    parser.add_argument("--base", default="HEAD", help="git revision to diff against (default HEAD)")
    parser.add_argument("--dry-run", action="store_true", help="print the plan and run nothing")
    parser.add_argument("--full", action="store_true", help="the gate: full suite + docs (+ acceptance if it moved)")
    args = parser.parse_args(argv)

    python = sys.executable
    changed = changed_files(args.base)

    if args.full:
        code = run([python, "-m", "pytest", "test/", "-q", *jobs()])
        code |= run([python, "-m", "pytest", DOCS_TEST_PATH, "-q"])
        plan = select(changed, TESTS, WEB_PREFIX)
        if plan["acceptance"]:
            code |= run([python, "-m", "pytest", "acceptance/", "-q"])
        return code

    if not changed:
        print("nothing changed since %s - nothing to run (the gate is --full)" % args.base)
        return 0

    plan = select(changed, TESTS, WEB_PREFIX)
    for line in plan_lines(plan, changed):
        print(line)
    if args.dry_run:
        return 0

    code = 0
    if plan["tests"]:
        code |= run([python, "-m", "pytest", *[str(TESTS / name) for name in plan["tests"]], "-q"])
    for name in plan["harnesses"]:
        code |= run(["node", str(FIXTURES / name), str(WEB)])
    if plan["docs"]:
        code |= run([python, "-m", "pytest", DOCS_TEST_PATH, "-q"])
    if plan["acceptance"]:
        code |= run([python, "-m", "pytest", "acceptance/", "-q"])
    print()
    print("inner loop done (exit %d). Before reporting: python tools/check_relevant.py --full" % code)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
