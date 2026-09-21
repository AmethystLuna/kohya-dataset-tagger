---
name: MEMORY
description: Project memory second-level index root: category table + usage + maintenance rules
metadata:
  type: index
---

# Project memory (second-level index root)

**How to use**: pick a category in this table → open `INDEX-<category>.md` for the topic list → read only the one or two you need.
Don't read the whole directory at once. The project map, commands and hard constraints resident in AGENTS.md are **not repeated here**.
Division of labor between the two levels: this table answers "which category to search", the category index answers "which topic to read", and the topic file is the real content.

| Category | Second-level index | Covers | When to read |
|---|---|---|---|
| scope | [INDEX-scope.md](INDEX-scope.md) | **Current state**, scope, architecture, data contract, settled decisions | **First stop when taking over** (read current-state first); confirming "should we do this" and what files a dataset directory holds |
| pipeline | [INDEX-pipeline.md](INDEX-pipeline.md) | Thumbnail/export pipeline, cache invalidation | Changing image reading, thumbnail generation and caching, image scaling export, cache consistency after a caption write |
| tagging | [INDEX-tagging.md](INDEX-tagging.md) | WD14 inference, vocabulary, local model inventory | Wiring up a tagger, tuning thresholds and post-processing, finding model files already on this machine |
| history | [INDEX-history.md](INDEX-history.md) | Changelog and historical decisions | You want to know how a constraint or value came about |
| docs | [INDEX-docs.md](INDEX-docs.md) | Documentation writing rules, agent reporting rules | Before writing/changing AGENTS.md, MEMORY.md, INDEX-*.md, a topic file or SKILL.md, and before reporting a conclusion |

## Maintenance rules

1. After adding a topic file, add a row to the matching `INDEX-<category>.md`; an orphan file turns `test/test_docs_index.py` red.
2. An index row's one-liner is taken from the topic file's frontmatter `description`; change both together.
3. A new category requires changing both this table and the "Deep-dive map" table in AGENTS.md.
4. Topic files uniformly use frontmatter (`name` / `description` / `metadata.type`), with a body that says "what it is + why + how to use it".
