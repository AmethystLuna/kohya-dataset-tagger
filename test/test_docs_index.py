"""Documentation structure gate: AGENTS.md stays an index, the indexes stay honest, skills stay loadable.

Why this exists
---------------
A resident instruction file is injected into the context of every session. Once it grows into a
manual, every subsystem detail is paid for again on every turn - while the real fix is to move
details into topic files and leave only pointers behind.

This repository uses two levels of indexing: `AGENTS.md` -> `.github/memory/MEMORY.md` ->
`INDEX-<category>.md` -> topic files. This gate keeps that structure from rotting back into a manual.

Checks (scan returns **every** problem it finds, not just the first)
-------------------------------------------------------------------
 1. `AGENTS.md` is within INDEX_MAX_BYTES.
 2. Every relative markdown link in `AGENTS.md` / `MEMORY.md` / `INDEX-*.md` resolves.
 3. Every topic file under `.github/memory/` is linked from at least one `INDEX-*.md` (no orphans).
 4. Every `INDEX-*.md` is linked from both `MEMORY.md` and the routing table in `AGENTS.md`.
 5. Every `.agents/skills/<name>/SKILL.md` has spec-compliant frontmatter: spec fields only,
    `name` equal to the directory name and matching lowercase-hyphen syntax, `description`
    non-empty and not too long, and a non-empty body.
 6. The three front pages (`README.md`, `README.zh-CN.md`, `README.ja.md`) stay in step: one
    centred language navigation under the title, the same heading structure, the same tables, the
    same commands inside the same code blocks, and the same link targets. Only the prose and the
    comments inside code blocks are translated - a command or a path is not.

This repository has **no** `CLAUDE.md` bridge file, so there is no "bridge budget" rule -
AGENTS.md is the single source of truth for resident instructions.

Falsification
-------------
`scan()` takes the repository root as an argument, so every rule also runs against mutated copies
of a **miniature documentation tree**: one test asserts the real repository is clean, and each rule
has its own test that mutates that tree and asserts the matching problem appears. A rule that stops
measuring makes its own falsification test fail.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MEMORY_DIRNAME = ".github/memory"
SKILLS_DIRNAME = ".agents/skills"

INDEX_FILE = "AGENTS.md"
MEMORY_FILE = "MEMORY.md"

#: Budget for the resident instructions. The point is to push new details into topic files, not to make people shave words in the index.
INDEX_MAX_BYTES = 28 * 1024

#: Top-level fields the Agent Skills frontmatter spec allows; client extensions go under metadata.
SPEC_FIELDS = frozenset(
    {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
)
SPEC_SKILL_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
NAME_MAX_CHARS = 64
DESCRIPTION_MAX_CHARS = 1024

#: The three front pages, in navigation order. Editing one is editing all three.
README_FILES = ("README.md", "README.zh-CN.md", "README.ja.md")
README_NAV_ORDER = ("README.zh-CN.md", "README.md", "README.ja.md")

LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
SKIP_PREFIXES = ("http://", "https://", "mailto:", "#", "page:")
FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---[ \t]*\n", re.S)
TOP_LEVEL_KEY_RE = re.compile(r"^([A-Za-z0-9_-]+):[ \t]*(.*)$")
BLOCK_SCALARS = (">", "|", ">-", "|-", ">+", "|+")


def _links(text: str) -> list[str]:
    return [t for t in LINK_RE.findall(text) if not t.startswith(SKIP_PREFIXES)]


def _index_files(memory_dir: Path) -> list[Path]:
    return sorted(memory_dir.glob("INDEX-*.md"))


def _topic_files(memory_dir: Path) -> list[Path]:
    """Markdown files that should be reachable from some category index."""
    return sorted(
        path
        for path in memory_dir.rglob("*.md")
        if path.name != MEMORY_FILE and not path.name.startswith("INDEX-")
    )


def _frontmatter(text: str) -> dict[str, str] | None:
    """Return the top-level frontmatter fields, or None when there is no block.

    Deliberately line-scans instead of using a YAML library: all we care about is *which keys
    exist*; folded scalars and nested mappings only need to be recognized, not interpreted.
    """
    match = FRONTMATTER_RE.match(text)
    if match is None:
        return None
    fields: dict[str, str] = {}
    current = ""
    folded = False
    for line in match.group(1).splitlines():
        if line[:1] in (" ", "\t"):
            if current and folded:
                fields[current] = (fields[current] + " " + line.strip()).strip()
            continue
        key_match = TOP_LEVEL_KEY_RE.match(line)
        if key_match is None:
            continue
        current = key_match.group(1)
        value = key_match.group(2).strip()
        folded = value in BLOCK_SCALARS
        fields[current] = "" if folded else value
    return fields


def scan_skills(root: Path) -> list[str]:
    """Check every skill against the Agent Skills frontmatter spec."""
    problems: list[str] = []
    skills_dir = root / SKILLS_DIRNAME
    if not skills_dir.is_dir():
        return ["%s is missing" % SKILLS_DIRNAME]

    files = sorted(skills_dir.glob("*/SKILL.md"))
    if not files:
        problems.append("no <name>/SKILL.md under %s" % SKILLS_DIRNAME)

    for path in files:
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8")
        fields = _frontmatter(text)
        if fields is None:
            problems.append("%s has no YAML frontmatter" % rel)
            continue

        unknown = sorted(set(fields) - SPEC_FIELDS)
        if unknown:
            problems.append(
                "%s frontmatter has non-spec keys %s - only %s are in the standard; "
                "client extras belong under metadata" % (rel, unknown, sorted(SPEC_FIELDS))
            )

        name = fields.get("name", "")
        if not name:
            problems.append("%s has no name" % rel)
        else:
            if len(name) > NAME_MAX_CHARS:
                problems.append("%s name is %d chars (max %d)" % (rel, len(name), NAME_MAX_CHARS))
            if not SPEC_SKILL_NAME.match(name):
                problems.append(
                    "%s name %r is not lowercase letters, digits and hyphens" % (rel, name)
                )
            if name != path.parent.name:
                problems.append(
                    "%s name %r must equal its directory name %r" % (rel, name, path.parent.name)
                )

        description = fields.get("description", "")
        if not description:
            problems.append("%s has no description" % rel)
        elif len(description) > DESCRIPTION_MAX_CHARS:
            problems.append(
                "%s description is %d chars (max %d)"
                % (rel, len(description), DESCRIPTION_MAX_CHARS)
            )

        body = FRONTMATTER_RE.sub("", text, count=1).strip()
        if not body:
            problems.append("%s has an empty body" % rel)

    return problems


# --------------------------------------------------------------------------
# Rule 6: the three front pages
# --------------------------------------------------------------------------

HREF_RE = re.compile(r'href="([^"]+)"')


def _readme_head(text: str):
    """`(title, navigation block)` - the `<h1>` and the centred `<p>` directly below it, or None."""
    match = re.match(r"\A<h1[^>]*>(.*?)</h1>[ \t]*\n+(<p[^>]*>.*?</p>)", text, re.S)
    if match is None:
        return None
    return match.group(1).strip(), match.group(2).strip()


def _heading_shape(text: str) -> tuple:
    """The sequence of `##` / `###` levels: a language-independent skeleton, so it must match."""
    return tuple(len(match.group(1)) for match in re.finditer(r"^(#{2,3}) .+$", text, re.M))


def _tables(text: str) -> tuple:
    """`(rows, columns)` of every markdown table, in order. Cells are translated; the shape is not."""
    tables: list = []
    run: list = []
    for line in text.splitlines() + [""]:
        if line.lstrip().startswith("|"):
            run.append(line)
        elif run:
            tables.append((len(run), run[0].count("|") - 1))
            run = []
    return tuple(tables)


def _without_comment(line: str) -> str:
    """A code line with its trailing comment removed - the part that must not be translated."""
    stripped = line.rstrip()
    match = re.search(r"(?:^|\s)#", stripped)
    return (stripped[: match.start()] if match else stripped).rstrip()


def _code_blocks(text: str) -> tuple:
    """Every fenced block as its non-empty command lines (comments and blank lines dropped)."""
    blocks: list = []
    current: list = []
    inside = False
    for line in text.splitlines():
        if line.startswith("```"):
            if inside:
                blocks.append(tuple(c for c in map(_without_comment, current) if c))
                current = []
            inside = not inside
            continue
        if inside:
            current.append(line)
    return tuple(blocks)


def _first_difference(left, right):
    for index, (a, b) in enumerate(zip(left, right)):
        if a != b:
            return index
    return None


def scan_readmes(root: Path) -> list:
    """Rule 6: the front page exists three times, and the copies must not drift apart.

    What is compared is what a reader cannot translate away: the navigation, the skeleton, the
    tables' shape, the commands and the link targets. The prose and the comments inside code
    blocks are expected to differ - that is the point of three files.
    """
    problems: list = []
    texts: dict = {}
    for name in README_FILES:
        path = root / name
        if not path.is_file():
            problems.append("%s is missing" % name)
        else:
            texts[name] = path.read_text(encoding="utf-8")
    if len(texts) < 2:
        return problems

    navigations: dict = {}
    for name, text in texts.items():
        head = _readme_head(text)
        if head is None:
            problems.append(
                "%s does not open with an <h1> and the centred language navigation" % name
            )
            continue
        navigations[name] = head[1]
        hrefs = HREF_RE.findall(head[1])
        if hrefs != list(README_NAV_ORDER):
            problems.append(
                "%s language navigation links %s - expected %s in that order"
                % (name, hrefs, list(README_NAV_ORDER))
            )
    if len(set(navigations.values())) > 1:
        problems.append(
            "the language navigation differs between the front pages: %s"
            % ", ".join(sorted(navigations))
        )

    reference = README_FILES[0] if README_FILES[0] in texts else sorted(texts)[0]
    for label, shape_of in (
        ("heading structure", _heading_shape),
        ("tables", _tables),
        ("code blocks", _code_blocks),
    ):
        shapes = {name: shape_of(text) for name, text in texts.items()}
        expected = shapes[reference]
        for name, shape in sorted(shapes.items()):
            if name == reference or shape == expected:
                continue
            index = _first_difference(shape, expected)
            if index is None:
                detail = "%d vs %d entries" % (len(shape), len(expected))
            else:
                detail = "position %d: %r vs %r" % (index, shape[index], expected[index])
            problems.append("%s %s differs from %s (%s)" % (name, label, reference, detail))

    targets = {name: sorted(set(_links(text))) for name, text in texts.items()}
    expected_targets = targets[reference]
    for name, found in sorted(targets.items()):
        if name != reference and found != expected_targets:
            problems.append(
                "%s and %s link to different targets (+%s / -%s)"
                % (
                    name,
                    reference,
                    sorted(set(found) - set(expected_targets)),
                    sorted(set(expected_targets) - set(found)),
                )
            )
    for name, text in sorted(texts.items()):
        for target in _links(text):
            if not (root / target).exists():
                problems.append("%s links to missing %s" % (name, target))

    return problems


def scan(root: Path) -> list[str]:
    """Return every structural problem in this documentation tree (empty list = clean)."""
    problems: list[str] = []

    memory_dir = root / MEMORY_DIRNAME
    index_path = root / INDEX_FILE
    memory_path = memory_dir / MEMORY_FILE

    # Rule 1: the resident index budget
    index_text = ""
    if not index_path.is_file():
        problems.append("%s is missing" % INDEX_FILE)
    else:
        index_text = index_path.read_text(encoding="utf-8")
        size = index_path.stat().st_size
        if size > INDEX_MAX_BYTES:
            problems.append(
                "%s is %d bytes (max %d) - move detail into a topic file"
                % (INDEX_FILE, size, INDEX_MAX_BYTES)
            )

    memory_text = ""
    if not memory_path.is_file():
        problems.append("%s/%s is missing" % (MEMORY_DIRNAME, MEMORY_FILE))
    else:
        memory_text = memory_path.read_text(encoding="utf-8")

    # Rule 2: relative links in the index and the category tables must resolve
    for label, path, text in (
        (INDEX_FILE, index_path, index_text),
        ("%s/%s" % (MEMORY_DIRNAME, MEMORY_FILE), memory_path, memory_text),
    ):
        if not text:
            continue
        for target in _links(text):
            if not (path.parent / target).exists():
                problems.append("%s links to missing %s" % (label, target))

    indexes = _index_files(memory_dir)
    if not indexes:
        problems.append("no INDEX-*.md under %s" % MEMORY_DIRNAME)

    for idx in indexes:
        for target in _links(idx.read_text(encoding="utf-8")):
            if not (idx.parent / target).exists():
                problems.append(
                    "%s links to missing %s" % (idx.relative_to(root).as_posix(), target)
                )

    # Rule 3: no orphan topic files
    linked: set[Path] = set()
    for idx in indexes:
        for target in _links(idx.read_text(encoding="utf-8")):
            linked.add((idx.parent / target).resolve())
    for topic in _topic_files(memory_dir):
        if topic.resolve() not in linked:
            problems.append(
                "orphan topic %s is not linked from any INDEX-*.md"
                % topic.relative_to(root).as_posix()
            )

    # Rule 4: category indexes must be reachable from both sides
    for idx in indexes:
        if "(%s)" % idx.name not in memory_text:
            problems.append("%s is not linked from %s" % (idx.name, MEMORY_FILE))
        rel = idx.relative_to(root).as_posix()
        if "(%s)" % rel not in index_text:
            problems.append("%s is not linked from %s" % (rel, INDEX_FILE))

    # Rule 5: skill spec
    problems.extend(scan_skills(root))

    # Rule 6: the three front pages stay in step
    problems.extend(scan_readmes(root))

    return problems


# --------------------------------------------------------------------------
# Miniature documentation tree: used to falsify each rule
# --------------------------------------------------------------------------

#: The miniature front pages: three copies of one skeleton, exactly as the real ones must be.
def _mini_readme(prose: str, comment: str) -> str:
    return (
        '<h1 align="center">Mini</h1>\n\n'
        '<p align="center">\n'
        '  <a href="README.zh-CN.md">zh</a> ·\n'
        '  <a href="README.md">en</a> ·\n'
        '  <a href="README.ja.md">ja</a>\n'
        '</p>\n\n'
        "## Status\n\n"
        + prose
        + "\n\n```text\nmini --flag   # "
        + comment
        + "\n```\n\n"
        "| a | b |\n|---|---|\n| 1 | 2 |\n\n"
        "See [topic-a](.github/memory/topic-a.md).\n"
    )


MINI_FILES = {
    "README.md": _mini_readme("the English page", "english note"),
    "README.zh-CN.md": _mini_readme("the Chinese page", "中文注释"),
    "README.ja.md": _mini_readme("the Japanese page", "日本語の注記"),
    "AGENTS.md": (
        "# AGENTS\n\n"
        "| scope | [INDEX-a.md](.github/memory/INDEX-a.md) | covers a |\n"
    ),
    ".github/memory/MEMORY.md": (
        "---\nname: MEMORY\ndescription: root\n---\n\n"
        "| a | [INDEX-a.md](INDEX-a.md) |\n"
    ),
    ".github/memory/INDEX-a.md": (
        "---\nname: INDEX-a\ndescription: idx\n---\n\n"
        "| [topic-a](topic-a.md) | one line |\n"
    ),
    ".github/memory/topic-a.md": (
        "---\nname: topic-a\ndescription: one line\n---\n\nbody\n"
    ),
    ".agents/skills/skill-a/SKILL.md": (
        "---\nname: skill-a\ndescription: a skill\n---\n\nbody\n"
    ),
}


def _build_mini(root: Path) -> Path:
    for rel, text in MINI_FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _assert_problem(problems: list[str], needle: str) -> None:
    assert any(needle in problem for problem in problems), (
        "expected a problem containing %r, got %r" % (needle, problems)
    )


# --------------------------------------------------------------------------
# The real repository
# --------------------------------------------------------------------------


def test_real_repository_is_clean() -> None:
    problems = scan(REPO_ROOT)
    assert problems == [], "documentation structure problems:\n" + "\n".join(problems)


# --------------------------------------------------------------------------
# Falsification: each rule has a mutation that turns it red
# --------------------------------------------------------------------------


def test_miniature_tree_is_clean(tmp_path: Path) -> None:
    """The gate itself must not produce false positives - otherwise the real-repository assertion means nothing."""
    assert scan(_build_mini(tmp_path)) == []


def test_oversized_index_is_detected(tmp_path: Path) -> None:
    _build_mini(tmp_path)
    _write(tmp_path, "AGENTS.md", "# AGENTS\n\n" + "filler\n" * (INDEX_MAX_BYTES // 6))
    _assert_problem(scan(tmp_path), "max")


def test_broken_link_is_detected(tmp_path: Path) -> None:
    _build_mini(tmp_path)
    _write(
        tmp_path,
        "AGENTS.md",
        "# AGENTS\n\n[INDEX-a.md](.github/memory/INDEX-a.md) [gone](nowhere.md)\n",
    )
    _assert_problem(scan(tmp_path), "links to missing nowhere.md")


def test_orphan_topic_is_detected(tmp_path: Path) -> None:
    _build_mini(tmp_path)
    _write(
        tmp_path,
        ".github/memory/topic-b.md",
        "---\nname: topic-b\ndescription: orphan\n---\n\nbody\n",
    )
    _assert_problem(scan(tmp_path), "orphan topic .github/memory/topic-b.md")


def test_index_missing_from_routing_table_is_detected(tmp_path: Path) -> None:
    _build_mini(tmp_path)
    _write(
        tmp_path,
        ".github/memory/INDEX-b.md",
        "---\nname: INDEX-b\ndescription: idx\n---\n\nno topics\n",
    )
    _write(
        tmp_path,
        ".github/memory/MEMORY.md",
        "---\nname: MEMORY\ndescription: root\n---\n\n"
        "| a | [INDEX-a.md](INDEX-a.md) |\n| b | [INDEX-b.md](INDEX-b.md) |\n",
    )
    _assert_problem(scan(tmp_path), "INDEX-b.md is not linked from AGENTS.md")


def test_skill_name_mismatch_is_detected(tmp_path: Path) -> None:
    _build_mini(tmp_path)
    _write(
        tmp_path,
        ".agents/skills/skill-a/SKILL.md",
        "---\nname: not-the-dir\ndescription: a skill\n---\n\nbody\n",
    )
    _assert_problem(scan(tmp_path), "must equal its directory name")


def test_skill_non_spec_field_is_detected(tmp_path: Path) -> None:
    _build_mini(tmp_path)
    _write(
        tmp_path,
        ".agents/skills/skill-a/SKILL.md",
        "---\nname: skill-a\ndescription: a skill\nwhen_to_use: always\n---\n\nbody\n",
    )
    _assert_problem(scan(tmp_path), "non-spec keys")


def test_skill_missing_description_is_detected(tmp_path: Path) -> None:
    _build_mini(tmp_path)
    _write(
        tmp_path,
        ".agents/skills/skill-a/SKILL.md",
        "---\nname: skill-a\n---\n\nbody\n",
    )
    _assert_problem(scan(tmp_path), "has no description")

def test_readme_navigation_drift_is_detected(tmp_path: Path) -> None:
    _build_mini(tmp_path)
    _write(
        tmp_path,
        "README.ja.md",
        MINI_FILES["README.ja.md"].replace('  <a href="README.ja.md">ja</a>\n', ""),
    )
    _assert_problem(scan(tmp_path), "language navigation")


def test_readme_section_drift_is_detected(tmp_path: Path) -> None:
    _build_mini(tmp_path)
    _write(tmp_path, "README.md", MINI_FILES["README.md"] + "\n## Extra\n\nthe English page only\n")
    _assert_problem(scan(tmp_path), "heading structure differs")


def test_readme_table_drift_is_detected(tmp_path: Path) -> None:
    _build_mini(tmp_path)
    _write(
        tmp_path,
        "README.zh-CN.md",
        MINI_FILES["README.zh-CN.md"].replace("| 1 | 2 |\n", "| 1 | 2 |\n| 3 | 4 |\n"),
    )
    _assert_problem(scan(tmp_path), "tables differs")


def test_readme_command_drift_is_detected(tmp_path: Path) -> None:
    _build_mini(tmp_path)
    _write(
        tmp_path,
        "README.ja.md",
        MINI_FILES["README.ja.md"].replace("mini --flag", "mini --other"),
    )
    _assert_problem(scan(tmp_path), "code blocks differs")


def test_readme_link_drift_is_detected(tmp_path: Path) -> None:
    _build_mini(tmp_path)
    _write(
        tmp_path,
        "README.md",
        MINI_FILES["README.md"].replace(
            "See [topic-a](.github/memory/topic-a.md).", "See the topic."
        ),
    )
    _assert_problem(scan(tmp_path), "link to different targets")


def test_broken_link_in_a_front_page_is_detected(tmp_path: Path) -> None:
    _build_mini(tmp_path)
    _write(
        tmp_path,
        "README.md",
        MINI_FILES["README.md"].replace("(.github/memory/topic-a.md)", "(nowhere.md)"),
    )
    _assert_problem(scan(tmp_path), "README.md links to missing nowhere.md")

