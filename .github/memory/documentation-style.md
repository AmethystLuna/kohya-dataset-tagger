---
name: documentation-style
description: Repository documentation writing rules: indexes only route, topics carry the body, frontmatter shape, honest labeling and resolvable links
metadata:
  type: topic
---

# Repository documentation writing rules

## What it is

This repository's documentation is a two-level index tree:

    AGENTS.md (resident, <=28 KB) -> MEMORY.md (category table) -> INDEX-<category>.md (topic list) -> topic files

The rules are enforced by `test/test_docs_index.py`; this file explains "why it's written this way and where new things go".
Violating the structure isn't an aesthetic problem: the resident index is paid for on every conversation, so writing detail back into it means buying it again every session.

## Hard rules (enforced by the gate)

1. **Indexes only route; topics carry the body.** `AGENTS.md` / `MEMORY.md` / `INDEX-*.md` hold only table rows and pointers -
   no arguments, no code, no history; detail always goes into a topic file.
2. **A new topic file must get a row in some `INDEX-<category>.md`.** Without that row it's an orphan file and the gate turns red.
3. **A new category hangs in two places at once.** It needs a row in the category table in `MEMORY.md` and in the "Deep-dive map" in `AGENTS.md`;
   the two write the path differently: the former writes `INDEX-x.md` relative to `.github/memory/`, the latter writes `.github/memory/INDEX-x.md`.
4. **`AGENTS.md` carries no detail.** Budget is 28 KB; over that the gate reports the file size directly.
5. **Every relative link must resolve.** Links in the index start from `.github/memory/`.
   To mention the repository-root `test/test_docs_index.py`, write it as inline code - **don't make it a markdown link**, or the gate reports a missing file.

## Where new facts go

| Type of fact | Destination |
|---|---|
| Commands / hard constraints / directory map used by every change | `AGENTS.md` (add one line or one bullet only, not a paragraph) |
| "Which category to search" | the category table in `MEMORY.md` |
| "Which topics to read in this category" | `INDEX-<category>.md` |
| Concrete mechanisms, parameters, evidence | topic files; for a new fact prefer **creating** a topic over bloating an old file |
| The background of a value or constraint | the `history` changelogs |

## frontmatter shape

Indexes and topic files use the same YAML frontmatter, with three core fields:

    ---
    name: <file name without .md>          # e.g. documentation-style
    description: <one sentence; index rows quote it>
    metadata:
      type: index | topic           # INDEX-*.md uses index, everything else uses topic
    ---

- `name` must **equal the file name without `.md`** (`INDEX-docs`, `current-state`).
- `description` is a one-sentence statement, and it **is** the source of the index's "one-liner" column; change both together.
- The body must be non-empty.

## How to write an index row

One row per topic; this repository's INDEX tables have three columns: topic / one-liner / when to read.

    | [documentation-style](documentation-style.md) | Repository documentation writing rules: ... | Before writing/changing a topic file |

- Link text is the file name (without `.md`); the target is a path relative to `.github/memory/`.
- The one-liner copies that file's frontmatter `description` (the gate doesn't enforce it, but the maintenance rules require keeping them in sync).
- "When to read" states the **trigger condition**, not a content summary - it's the routing signal for the next agent.
- Counter-example -> good example:
  - Bad: `| [style](documentation-style.md) | doc rules | docs |`
  - Good: `| [documentation-style](documentation-style.md) | Repository documentation writing rules: indexes only route, topics carry the body | Before writing/changing any memory file |`

## A wrong fact is worse than none

Anything labeled "verified", "measured" or "validated" must actually have been validated; if it wasn't, write "unverified" or delete the sentence.
This repository has been burned twice (vocabulary row count, subdirectory image count limit), both found when a subagent reviewed.
Conversely, **explicitly writing "unverified / known gap" is encouraged** - an honestly labeled blank is more useful than fabricated evidence.
Counter-example -> good example:
- Bad: the table says "verified", but nobody ever ran that command.
- Good: the table says "unverified: only produced once on `tmp_path`".

## skill frontmatter rules

`.agents/skills/<name>/SKILL.md` carries only spec fields, and the gate's `scan_skills` checks each one:

- `name` == directory name, lowercase letters, digits and hyphens only (`^[a-z0-9]+(?:-[a-z0-9]+)*$`), e.g. `kohya-dataset-tagger-workflow`.
- The top level holds **only** spec fields: `name` / `description` / `license` / `compatibility` / `metadata` / `allowed-tools`.
  Client extensions always go under `metadata`.
  Counter-example -> good example: adding `when_to_use:` at the top level -> the gate reports `non-spec keys`; moving it into `metadata.when_to_use` -> passes.
- `description` is non-empty and <=1024 characters, and should say "when to use it", not just "what it is"; the body must be non-empty.
- SKILL.md carries the process only; concrete parameters and conclusions stay in memory topic files - don't repeat them.

## General technical writing rules

Taken from GOV.UK "Writing for user interfaces", the Microsoft Writing Style Guide and plain-language guidance,
and reduced to the following in this repository (they apply to mixed Chinese/English text too):

- **Say the important thing first, and say less.** Cut every word you can; one idea per sentence, don't cram three things into one.
- **Use the reader's words.** Write "changing a caption requires deleting the cache", not "the invalidation semantics of the cache consistency contract".
- **Talk like a person, as if explaining to a colleague.** Avoid the passive voice and piles of jargon; explain a term the first time it appears.
- **Put the conclusion first.** A paragraph's first sentence is the point; the rest only supports it.
- **Active voice, concrete nouns.** Write "`config.load()` reads the file once", not "file reading occurs during the initialization phase".
- **Exclude no reader.** Don't write "obviously" or "as everyone knows"; don't assume the reader knows kohya or this repository's history.
- Counter-example -> good example:
  - Bad: Because of the cache invalidation mechanism, after a caption is written the text encoder cache should be deleted synchronously.
  - Good: Writing a caption must synchronously delete the text encoder cache; if you don't, training silently uses the old tags.

---

Related: [agent-reporting](agent-reporting.md), [INDEX-docs.md](INDEX-docs.md)
