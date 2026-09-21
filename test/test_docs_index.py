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

    return problems


# --------------------------------------------------------------------------
# Miniature documentation tree: used to falsify each rule
# --------------------------------------------------------------------------

MINI_FILES = {
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
