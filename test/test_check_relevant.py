"""The relevance selector is the inner loop's whole design: relevant tests now, the gate once.

What is checked here needs no git and no subprocess - the selection rules run against a temporary
tree, which is the point: the rules are pure, so they can be falsified like anything else.

 1. a changed frontend module selects the test that names the file, plus the copy/contract net;
 2. a changed locale pack adds the i18n net, a changed index.html or stylesheet adds the a11y net;
 3. a changed backend module selects what imports it, plus the end-to-end net;
 4. a documentation change runs the docs gate; a change to the frozen contract also implies acceptance;
 5. a source file NO test mentions is reported as UNCOVERED rather than silently dropped - that is
    the one thing this tool must never do;
 6. a changed test or harness is selected directly;
 7. --dry-run runs nothing at all.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TOOL = REPO / "tools" / "check_relevant.py"


def load_tool():
    """Import tools/check_relevant.py by path: tools/ is a script directory, not a package."""
    assert TOOL.is_file(), "missing %s" % TOOL
    spec = importlib.util.spec_from_file_location("check_relevant", TOOL)
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_relevant"] = module
    spec.loader.exec_module(module)
    return module


def build_tree(tmp_path: Path):
    """A miniature repo: two frontend modules, a locale pack, markup, one backend module."""
    web = tmp_path / "src" / "kohya_dataset_tagger" / "web"
    tests = tmp_path / "test"
    (web / "js" / "locales").mkdir(parents=True)
    (web / "css").mkdir()
    (tmp_path / "src" / "kohya_dataset_tagger" / "api").mkdir(parents=True)
    tests.mkdir()
    (web / "js" / "readiness.js").write_text("export const x = 1;", encoding="utf-8")
    (web / "js" / "app.js").write_text("export const z = 3;", encoding="utf-8")
    (web / "js" / "palette.js").write_text("export const y = 2;", encoding="utf-8")
    (web / "js" / "locales" / "zh-CN.js").write_text("export default {}", encoding="utf-8")
    (web / "index.html").write_text("<html></html>", encoding="utf-8")
    (web / "css" / "app.css").write_text(".a{}", encoding="utf-8")
    (tmp_path / "src" / "kohya_dataset_tagger" / "api" / "dataset.py").write_text("x = 1", encoding="utf-8")
    (tests / "test_web_readiness.py").write_text('import "readiness.js";', encoding="utf-8")
    (tests / "test_web_contract.py").write_text("index.html app.css locales", encoding="utf-8")
    (tests / "test_web_i18n.py").write_text("locales zh-CN.js", encoding="utf-8")
    (tests / "test_web_a11y.py").write_text("index.html", encoding="utf-8")
    (tests / "test_dataset_status_api.py").write_text(
        "from kohya_dataset_tagger.api import dataset\n", encoding="utf-8"
    )
    (tests / "test_api_e2e.py").write_text("end to end", encoding="utf-8")
    return tests


WEB_PREFIX = "src/kohya_dataset_tagger/web"


def test_a_frontend_module_selects_its_own_test_and_the_contract_net(tmp_path):
    tool = load_tool()
    tests = build_tree(tmp_path)
    plan = tool.select([WEB_PREFIX + "/js/readiness.js"], tests, WEB_PREFIX)
    assert plan["tests"] == ["test_web_contract.py", "test_web_readiness.py"], plan["tests"]
    assert plan["uncovered"] == []


def test_a_locale_pack_adds_the_i18n_net_and_markup_adds_the_a11y_net(tmp_path):
    tool = load_tool()
    tests = build_tree(tmp_path)
    plan = tool.select([WEB_PREFIX + "/js/locales/zh-CN.js"], tests, WEB_PREFIX)
    assert "test_web_i18n.py" in plan["tests"] and "test_web_contract.py" in plan["tests"]
    plan = tool.select([WEB_PREFIX + "/index.html"], tests, WEB_PREFIX)
    assert "test_web_a11y.py" in plan["tests"] and "test_web_i18n.py" not in plan["tests"]
    plan = tool.select([WEB_PREFIX + "/css/app.css"], tests, WEB_PREFIX)
    assert "test_web_a11y.py" in plan["tests"]


def test_a_backend_module_selects_what_imports_it_and_the_end_to_end_net(tmp_path):
    tool = load_tool()
    tests = build_tree(tmp_path)
    plan = tool.select(["src/kohya_dataset_tagger/api/dataset.py"], tests, WEB_PREFIX)
    assert plan["tests"] == ["test_api_e2e.py", "test_dataset_status_api.py"], plan["tests"]


def test_documentation_and_the_frozen_contract_route_to_their_own_gates(tmp_path):
    tool = load_tool()
    tests = build_tree(tmp_path)
    plan = tool.select([".github/memory/changelog/x.md"], tests, WEB_PREFIX)
    assert plan["tests"] == [] and plan["docs"] is True and plan["acceptance"] is False
    plan = tool.select([".github/memory/p0-spec.md"], tests, WEB_PREFIX)
    assert plan["docs"] is True and plan["acceptance"] is True
    plan = tool.select(["acceptance/test_p0_acceptance.py"], tests, WEB_PREFIX)
    assert plan["acceptance"] is True


def test_a_source_file_no_test_mentions_is_reported_not_dropped(tmp_path):
    tool = load_tool()
    tests = build_tree(tmp_path)
    plan = tool.select([WEB_PREFIX + "/js/palette.js"], tests, WEB_PREFIX)
    assert plan["uncovered"] == [WEB_PREFIX + "/js/palette.js"], plan
    assert "UNCOVERED" in "\n".join(tool.plan_lines(plan, [WEB_PREFIX + "/js/palette.js"]))


def test_a_changed_test_or_harness_is_selected_directly(tmp_path):
    tool = load_tool()
    tests = build_tree(tmp_path)
    plan = tool.select(["test/test_web_readiness.py", "test/fixtures/x_harness.mjs"], tests, WEB_PREFIX)
    assert "test_web_readiness.py" in plan["tests"]
    assert plan["harnesses"] == ["x_harness.mjs"]


def test_dry_run_plans_without_running_anything(tmp_path, monkeypatch, capsys):
    tool = load_tool()
    tests = build_tree(tmp_path)
    monkeypatch.setattr(tool, "TESTS", tests)
    monkeypatch.setattr(tool, "WEB_PREFIX", WEB_PREFIX)
    monkeypatch.setattr(tool, "changed_files", lambda base: [WEB_PREFIX + "/js/readiness.js"])
    calls = []
    monkeypatch.setattr(tool, "run", lambda cmd: calls.append(cmd) or 0)
    assert tool.main(["--dry-run"]) == 0
    assert calls == [], calls
    assert "test_web_readiness.py" in capsys.readouterr().out


def test_a_common_word_stem_does_not_drag_in_unrelated_tests(tmp_path):
    """app.js must not select every test that says "app": the file NAME is the token.

    This is the rule that keeps the inner loop short - a bare-stem match on a word like app or tags
    selects most of the suite, which is the behaviour this tool exists to remove.
    """
    tool = load_tool()
    tests = build_tree(tmp_path)
    (tests / "test_web_breadcrumb.py").write_text("# the app boots and paints a crumb", encoding="utf-8")
    plan = tool.select([WEB_PREFIX + "/js/app.js"], tests, WEB_PREFIX)
    assert "test_web_breadcrumb.py" not in plan["tests"], plan["tests"]
    assert plan["tests"] == ["test_web_contract.py"], plan["tests"]


def test_every_command_the_tool_builds_points_at_a_file_that_exists(monkeypatch, capsys):
    """A gate command naming a file that is not there fails as "file or directory not found".

    That is how the docs gate was silently unreachable from --full: the argument was the bare file
    name instead of its path. Any argument that looks like a path has to resolve.
    """
    tool = load_tool()
    monkeypatch.setattr(tool, "changed_files", lambda base: [".github/memory/p0-spec.md"])
    calls = []
    monkeypatch.setattr(tool, "run", lambda cmd: calls.append(cmd) or 0)
    tool.main([])
    capsys.readouterr()
    paths = [arg for cmd in calls for arg in cmd if arg.startswith(("test/", "test_", "acceptance/"))]
    assert paths, calls
    assert all((REPO / item).exists() for item in paths), paths


def test_a_module_covered_only_by_a_harness_is_covered(tmp_path):
    """jobs.js is exercised through test/fixtures/jobs_harness.mjs, not through a test module.

    Counting only test files would call that module UNCOVERED and skip the one thing that runs it.
    """
    tool = load_tool()
    tests = build_tree(tmp_path)
    (tests / "fixtures").mkdir()
    (tests / "fixtures" / "palette_harness.mjs").write_text(
        'import { createPalette } from "../js/palette.js";', encoding="utf-8"
    )
    plan = tool.select([WEB_PREFIX + "/js/palette.js"], tests, WEB_PREFIX)
    assert plan["harnesses"] == ["palette_harness.mjs"], plan
    assert plan["uncovered"] == [], plan


def test_the_full_gate_goes_parallel_only_when_the_plugin_is_there(monkeypatch):
    """-n auto is measured at ~3x (74.5 s -> 25.2 s on 1005 criteria), but a machine without
    pytest-xdist must still be able to run the same command, so the flag is conditional."""
    tool = load_tool()
    calls = []
    monkeypatch.setattr(tool, "changed_files", lambda base: [])
    monkeypatch.setattr(tool, "run", lambda cmd: calls.append(cmd) or 0)
    monkeypatch.setattr(tool.importlib.util, "find_spec", lambda name: object())
    tool.main(["--full"])
    assert any("-n" in cmd for cmd in calls if "test/" in cmd), calls
    calls.clear()
    monkeypatch.setattr(tool.importlib.util, "find_spec", lambda name: None)
    tool.main(["--full"])
    assert not any("-n" in cmd for cmd in calls), calls
