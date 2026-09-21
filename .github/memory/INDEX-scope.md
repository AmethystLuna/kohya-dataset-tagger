---
name: INDEX-scope
description: Scope, architecture, data contract and settled decisions - topic list for this category (second-level index)
metadata:
  type: index
---

# Scope, architecture and data contract (scope)

> Parent: [MEMORY.md](MEMORY.md) · When to read this category: confirming "should we do this", what files a dataset directory holds, caption format rules

| Topic | One-liner | When to read |
|---|---|---|
| [current-state](current-state.md) | **Current state and known gaps - first stop when taking over**: what is verified, what isn't, next steps | Before taking on any work; when you want to know "what can be trusted" |
| [project-architecture](project-architecture.md) | Process/directory layering of the standalone companion tool, and its filesystem contract with the trainer | Adding a module, changing layering, deciding which layer a piece of logic belongs in |
| [dataset-contract](dataset-contract.md) | What files really live in a dataset directory, caption sidecar naming and separator rules, cache directories that must be filtered | Writing directory scans, writing captions, defining counting rules |
| [scope-and-decisions](scope-and-decisions.md) | V0.1's seven settled scope decisions and their rationale (including explicit non-goals) | Before adding a feature or "just quietly supporting" something else |
| [p0-spec](p0-spec.md) | P0's frozen interface contract and acceptance criteria - the implementer's only source of truth | **Read before writing any P0 code**; when changing interfaces or acceptance criteria |
| [i18n](i18n.md) | Frontend localization and copy mechanism - locale pack layout, language resolution and switching, relabel contract, copy criteria and backend message localization | Changing the copy mechanism, adding a language, touching `web/strings.js` or panel rendering |
| [ui-copy](ui-copy.md) | User-facing UI copy rules - the one-word-per-concept table, per-language punctuation and spacing, the length budget for localizable strings, and templates for buttons, errors, empty states and status text | **Before adding or changing any string** in `web/js/locales/*.js`; when writing error, empty-state or progress copy |
| [design-principles](design-principles.md) | The design doctrine - the principle family this tool is built on, the invariants those principles turned into, and the loop (mechanism -> criterion -> falsification -> changelog) that keeps a later change honest. | Before adding a surface, an action, a number on screen, or a rule; and when reviewing whether a change belongs in this product |

## Related but not in this category

- Cache invalidation after a caption write → [INDEX-pipeline.md](INDEX-pipeline.md)
- Tagger and vocabulary → [INDEX-tagging.md](INDEX-tagging.md)
