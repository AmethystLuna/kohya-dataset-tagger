---
name: INDEX-docs
description: Repository documentation writing rules and agent reporting rules - topic list for this category (second-level index)
metadata:
  type: index
---

# Documentation writing and agent output rules (docs)

> Parent: [MEMORY.md](MEMORY.md) · When to read this category: writing/changing AGENTS.md, MEMORY.md, INDEX-*.md, a topic file or SKILL.md, and when reporting a conclusion to the user

| Topic | One-liner | When to read |
|---|---|---|
| [documentation-style](documentation-style.md) | Repository documentation writing rules: indexes only route, topics carry the body, frontmatter shape, honest labeling and resolvable links | Before adding/changing a topic file, an index row, AGENTS.md or SKILL.md |
| [agent-reporting](agent-reporting.md) | Agent reporting rules: conclusion first, distinguish verified/inferred/unverified, report the command verbatim and its result | Any time you report a conclusion, run a gate, or wrap up after changing files |

## Related but not in this category

- The structure gate itself is `test/test_docs_index.py` (the rules are not repeated here; changing the documentation rules means updating it too).
