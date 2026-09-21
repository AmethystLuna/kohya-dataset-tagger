---
name: agent-reporting
description: Agent reporting rules: conclusion first, distinguish verified/inferred/unverified, report the command verbatim and its result
metadata:
  type: topic
---

# Agent reporting rules

## What it is

The unified format for an agent (including subagents) in this repository reporting conclusions to the user or to another agent.
The goal is for the reader to know three things within ten seconds: what the conclusion is, what can be trusted, and what to do next.

## Rule one: conclusion first

- The first line is the answer or the conclusion. **No** preamble like "OK", "Let me take a look", "You asked me to ...".
- Don't restate the request - the other side already has it.
- Counter-example -> good example:
  - Bad: `I first checked the relevant files, got a feel for the requirements, and then I found ...`
  - Good: `Docs gate passed: 9 passed. Added the docs category and two topic files.`

## Rule two: distinguish evidence levels

Three classes must be labeled explicitly and never mixed together:

| Level | How to write it | Basis |
|---|---|---|
| Verified | state it directly, with the **file path + the tool that produced the evidence** | `read .github/memory/MEMORY.md`; `pytest ... -> 9 passed` |
| Inferred | label it "inferred" and give the reasoning chain | which verified facts it follows from |
| Unverified | label it "unverified" and say what evidence is still missing | never ran it, read it, or checked it on a real machine/browser |

- **Never claim to have read something you didn't read.** If you didn't open a file, say so; don't guess content from the file name.
- Cite files with a **path relative to the repository root**, with line numbers when useful.
- Counter-example -> good example:
  - Bad: `I checked the tests, should be fine.`
  - Good: `test/test_docs_index.py read; this round only checked structure - the actual run result is in the next bullet.`

## Rule three: quote command results verbatim

- Report the **command verbatim + the result**: `python -m pytest test/test_docs_index.py -q -> 9 passed`.
- **If a gate is red, say red first**, then explain why; don't bury the red behind a pile of completed items.
- Report skips too: the skipped in `725 passed, 2 skipped` is real information.
- If a command wasn't run, write "not run" - don't use "should pass".

## Rule four: name the files you changed

- List the **main** changed paths, separating new files from edits.
- One sentence per file on what changed; don't list unrelated temporary files.

## Rule five: the wrap-up must be short and scannable

Four fixed blocks, one or two lines each:

1. **What was done** - one sentence.
2. **What was proven** - command + result.
3. **What wasn't verified** - explicitly write "unverified"; write "none" if there is none.
4. **The next decision** - give only **one**, don't throw out a string of options.

## Rule six: never fabricate

- Write "unverified" for numbers you didn't check; don't pass an estimate off as a measurement.
- Don't invent file paths, line numbers, or command output.
- When unsure whether something is "inferred" or "unverified", write the lower of the two.

---

Related: [documentation-style](documentation-style.md), [INDEX-docs.md](INDEX-docs.md)
