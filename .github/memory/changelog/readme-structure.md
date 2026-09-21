# The front page: rewritten to the house rules, and gated in three languages

**When to read this**: you are wondering why the README is ordered the way it is, why a listing is a table
instead of a code block, or what keeps the three languages from drifting apart.

## Why (2026-09-21)

The three front pages were written during the publishing round
([open-source-readiness](open-source-readiness.md)) and had drifted from the rules the rest of the
documentation follows ([documentation-style](../documentation-style.md)). Measured against them:

- **It read like a manual.** The English page was 238 lines, bold on nearly every sentence, and carried two
  paragraphs of self-description ("the number-one feature", "these two are the reason this tool exists").
- **The two audiences were interleaved.** Acceptance and the documentation layout sat between the user's
  sections, and the gate commands were printed a second time after \`CONTRIBUTING.md\` had already listed them.
- **It misquoted the UI.** The panel is titled "Root directories" and the dialog "Model directories…", not
  "Roots" and "Model directory…". A quoted name has to match the visible label exactly
  ([ui-copy](../ui-copy.md)).
- **Nothing held the three copies together.** "Changing one is changing all three" was a sentence in
  \`CONTRIBUTING.md\`, checked once, by hand.

## The page now

One path through the page, in the order a reader meets it: what it is → status → what the trainer will not
tell you → install and run → GPU → tagger models → datasets and model directories without a restart →
development → contributing → license. 238 → 190 lines. The two contributor sections moved under
**Development**, and the detail they carried became a link.

| Was | Is |
|---|---|
| six code blocks for four entry points | one table (task × Windows / Linux) |
| the documentation tree as a fenced listing | a two-column table - a fenced block may not carry translated prose (see below) |
| the gate commands here and in \`CONTRIBUTING.md\` | four lines here; the keys, the CI jobs and the rules live there |
| "measured on this machine" | "measured on the reference machine", and the status block is fenced and dated |
| bold on most sentences | emphasis only where it carries the section |
| \`"Roots"\`, \`"Model directory…"\` | \`"Root directories"\`, \`"Model directories…"\`; the zh and ja pages quote their own packs (「删除」/「削除」, \`＋\`) |
| a remembered status number | the CI measurement, refreshed below |

## Rule 6 of the documentation gate

\`test/test_docs_index.py\` gained a sixth rule: the three front pages must open with the same byte-identical
centred language navigation, and must agree on

1. the heading structure (the sequence of \`##\` / \`###\` levels),
2. every table's row and column count,
3. every code block's command lines,
4. the set of link targets - which must also resolve against the repository.

Comments inside code blocks are stripped before (3): they are translated. Prose, comments and table cells
are deliberately **not** compared. One consequence is written into
[documentation-style](../documentation-style.md): a fenced block holds **commands, not prose** - a listing
that is translated line by line (the documentation layout, for instance) belongs in a table, where the shape
is checked and its cells stay free.

Each of the six criteria has a falsification against the miniature tree in \`_build_mini\`.

## The page does not name anybody else's project (2026-09-21)

The three READMEs opened with "a companion tool to **Anima-Standalone-Trainer**" and `AGENTS.md` called it
"a sibling project", which reads as the same author's second repository. The first correction replaced that
with an explicit "a separate project by another author" - still more than this repository needs to say. It
does not describe itself relative to that project at all, so the name is gone from the three READMEs,
`AGENTS.md`, `CONTRIBUTING.md`, `local_paths.ini.example`, [project-architecture](../project-architecture.md),
[changelog/bootstrap](bootstrap.md), [local-model-inventory](../local-model-inventory.md) and the A23 comment
in `acceptance/test_p0_acceptance.py`.

What a reader still needs is kept, in this repository's own words: it is a standalone tool with its own
process and its own page, it works directly on the files a kohya-style training set already has (the `.txt`
caption beside each image, the training caches, `dataset.toml`), and acceptance A23 needs a trainer checkout
to score the generated `dataset.toml`. The name survives only where a **file** or a prompt style is the
subject: `strategy_anima.py`'s `_anima.safetensors` cache suffix in the cache code, and the
escape-parentheses hint in the three locale packs.

## No straw men (2026-09-21)

The same habit produced most of the page's other bad sentences: **set up a target the reader never raised,
then answer it.** "It is not a plugin", "the feature still works instead of pretending", "a download that
cannot happen is not a dead end", "(an endpoint that lists any directory would void the allowlist)", "which
is why it is not the default on Windows", "the default list holds no path that exists on only one machine" -
and, for a whole section, the heading "What the trainer will not tell you". A reader who never considered
the alternative only wonders what the text swerved for.

All of them are statements now: "Two failures that cost a training run", "a caption write leaves the text
encoder cache stale", "image enumeration stops one level down", "paste an absolute path", "to use a model
without downloading it", "the default list contains only locations inside this repository and your own user
cache".

The rule is in [documentation-style](../documentation-style.md) ("Do not answer an alternative the reader
never raised") with the counter-examples. **Real warnings were left alone** - "do not install into the
trainer's venv", "onnxruntime falls back to CPU silently", "a root that does not exist only makes every
request 403 or 404": a specific action to avoid is not a rhetorical contrast.

## Evidence

- \`python -m pytest test/test_docs_index.py -q\` → **15 passed** (9 before: 6 new criteria).
- **Falsified on the real files**: \`--extra-models\` → \`--extra-model\` in one line of \`README.ja.md\` turned
  \`test_real_repository_is_clean\` red with \`README.ja.md code blocks differs from README.md (position 4: …)\`;
  restoring the file byte for byte turned it green again (the restore was compared byte-wise).
- **A fresh clone** (a copy with no \`local_paths.ini\`, no \`.venv\`, no downloaded model, a new venv and
  \`pip install -r requirements.txt\`): \`pytest test/ -q -n auto -m "not network"\` → **1079 passed,
  31 skipped, 0 failed**. The same copy without \`.git\` failed
  \`test_shell_scripts_are_executable_in_the_git_index\` alone - that criterion reads the git index, so a real
  clone has it and the copy needed \`.git\` to be faithful.
- **This machine**, the same command with \`local_paths.ini\` in place: **1108 passed, 2 skipped** (the two are
  the criteria that the clone's 31 skips include - a real dataset and a downloaded model).
- **The three-way audit after the rewrite**: identical heading sequences (\`2,2,2,2,2,2,2,3,3,3,2,2\`), identical
  table shapes (5×3, 5×3, 6×3, 8×2), seven code blocks whose command lines are identical, and identical link
  targets. The audit was a script; the gate now makes it permanent.

## Known limits

- **The gate compares the three copies with each other, so a mistake present in all three passes.** Learned
  here: the rewrite was generated with a placeholder character standing in for the backtick, and the one real
  section reference (\`§3.7\`) in each file was silently turned into a stray backtick. All three were wrong in
  the same way, rule 6 was green, and proofreading found it. Read the diff, not only the siblings.
- The page still quotes numbers measured on the reference machine (the provider table, the mirror speeds).
  They are labelled as such and are not re-measured every release.
- Rule 6 does not compare inline code spans or table cells, so a language that names an identifier the other
  two do not is not caught.
