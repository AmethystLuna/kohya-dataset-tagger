# Contributing

Thanks for looking. This is a small, opinionated tool, and the most useful contribution is usually a
report of a dataset job you had to do by hand that this tool made harder than it should have been.

**Issues and pull requests in Chinese are welcome** — the maintainer reads both. Code, comments,
commit messages and documentation are written in English so that the repository stays readable to
everyone.

By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md). Security problems go
through [SECURITY.md](SECURITY.md), not the public issue tracker.

## What this project is, and what it is not

It prepares a **kohya-style dataset directory** for training: browse it, look at the images, edit the
`.txt` captions, filter by tag, batch-caption with WD14, and generate a `dataset.toml` the trainer
accepts. It talks to the trainer only through the filesystem — there is no shared process and no
plugin API.

Out of scope, deliberately: training itself, image generation, anything that edits an image file in
the dataset (the crop tool's `<stem>_<N>` archive beside the source is the single, bounded
exception), and cloud/remote operation — the service listens on `127.0.0.1` and expects your data
to stay on your machine.

## Setup

```bash
./setup_env.sh          # Linux / macOS
setup_env.bat           # Windows
```

Both are idempotent and install into the repository's own `.venv` (about 250 MB, **no torch**).
On a mainland-China network use `setup_env_cn.sh` / `setup_env_cn.bat` instead. Python 3.10 or
newer; a browser; nothing else. Node is **not** needed to run the tool — only the frontend harness
criteria use it, and they skip themselves when it is missing.

Run it with `start.bat` / `./start.sh`, or directly:

```bash
.venv/bin/python -m kohya_dataset_tagger --roots "<your dataset root>" --port 3001
```

## Before you change anything

Read [AGENTS.md](AGENTS.md) first. It is the resident index: the project map, the commands, and the
hard constraints that every change has to respect. It routes into `.github/memory/`, which holds the
detail — including *why* several things that look odd are the way they are. Changing a caption
without deleting the text-encoder cache, for instance, trains on stale tags with no error at all;
that rule and its evidence live there.

## The inner loop, and the gate you report with

Do not run the whole suite after every edit — it is about a minute, and the interesting part is a
handful of tests doing real work. `tools/check_relevant.py` picks the tests that name what you
changed:

```bash
python tools/check_relevant.py             # only what covers the change
python tools/check_relevant.py --dry-run   # print the plan, run nothing
python tools/check_relevant.py --full      # the gate: whole suite + documentation gate
```

Anything no test names is printed as **UNCOVERED** rather than quietly dropped — that is the signal
that only the full suite covers it.

```bash
python -m pytest test/ -q -n auto               # the unit suite
python -m pytest test/test_docs_index.py -q     # the documentation structure gate (standard library only)
python -m pytest acceptance/ -q                 # acceptance criteria (needs a real dataset)
```

### Pointing the criteria at your own data

Every path the tests and the tools need comes from `local_paths.ini`, which is **gitignored** - the
repository itself holds no machine path at all:

```bash
cp local_paths.ini.example local_paths.ini      # Windows: copy local_paths.ini.example local_paths.ini
```

The committed template points **inside this repository**: the sample dataset under
`test/fixtures/sample_dataset/` and the `models/` directory. That is why a fresh clone runs with no
setup - and the criteria whose subject cannot exist without a downloaded tagger model or the real
vocabulary skip themselves and say so.

| Key | What it is |
|---|---|
| `dataset` | a kohya-style dataset directory; everything that reads real data uses it, and nothing ever writes to it |
| `models` | model search roots, separated by the platform's path separator (`;` on Windows, `:` elsewhere) |
| `hf_hub` | a HuggingFace cache, for the criteria that assert the real 10861-row vocabulary |
| `trainer` | a checkout of Anima-Standalone-Trainer, which acceptance A23 feeds the generated `dataset.toml` to |
| `scratch` | a scratch directory the destructive benchmarks may write below |

`python local_paths.py` prints what is currently in effect.

The acceptance suite is different: it scores the generated `dataset.toml` against the trainer's own
`config_util`, so it needs both a real dataset and a checkout of the trainer. It stays a local gate
and does not run in CI.

## What CI runs

[.github/workflows/ci.yml](.github/workflows/ci.yml) has three jobs:

| Job | What it does |
|---|---|
| `docs` | the documentation structure gate — budget, resolvable links, no orphan topic files, skill frontmatter |
| `tests` | `pytest test/` on Ubuntu (Python 3.10 and 3.13) and on Windows (3.10), after building the repository's own `.venv` |
| `network` | the criteria that talk to huggingface.co / hf-mirror.com — **scheduled weekly**, not per push, so a third party cannot redden your pull request |

```bash
python -m pytest test/ -q -n auto -m "not network"   # what the tests job runs
```

## Rules a change has to respect

The full list is in `AGENTS.md`. The five that are broken most often:

1. **A caption write must invalidate the text-encoder cache in the same request.** Silently training
   on the old tags is the worst failure this tool can have.
2. **Every user-supplied path goes through `core/paths.py`.** No string concatenation, no exceptions.
3. **Image files in the dataset are never modified.** Captions are written atomically (temp file +
   `os.replace`); the crop archive is written *beside* its source and never overwrites anything.
4. **A criterion only counts if it can fail.** When you add one, deliberately break the behavior,
   watch it go red, then restore it. "It passes" is not evidence that it tests anything.
5. **No frontend build step and no new dependency without a written justification.** The frontend is
   native ES modules, and all user-visible copy lives in `web/js/locales/*.js` — every locale pack
   changes together, with identical keys and placeholders.

Verify the normal path, the failure path, and the recovery path. Verifying only "it runs" does not
count.

## Documentation duty

This repository keeps its design memory in the tree, and a behavior change that is not written down
there is treated as unfinished:

- Behavior or parameter change → update the matching topic file under `.github/memory/`, including
  its `description`.
- New topic file → add a row to the matching `.github/memory/INDEX-<category>.md`, or the
  documentation gate reports it as an orphan.
- The background of a value ("why is it 0.35", "why is the cache deleted rather than rebuilt") goes
  into `.github/memory/changelog/`.
- `AGENTS.md` itself stays an index with a 28 KB budget. Detail goes into a topic file.
- **The README exists in three languages** (`README.md`, `README.zh-CN.md`, `README.ja.md`). Changing one is
  changing all three: commands, paths, identifiers, numbers, table shapes and code blocks stay the same, and
  only the prose and the comments inside code blocks are translated. The documentation gate checks the
  language navigation, the heading structure, each table's row and column count, every code block's command
  lines and the set of link targets; how the page itself is ordered is in
  [.github/memory/documentation-style.md](.github/memory/documentation-style.md).

## Commits and pull requests

- One logical change per commit. Conventional-commit subjects, in the form the log already uses:
  `feat(scale): the export acts on the scope strip's list`, `fix(captions): ...`, `docs(memory): ...`.
- The body says **why**, and quotes the command and its result when a number is claimed.
- Run `python tools/check_relevant.py --full` before opening the pull request and paste what it
  printed. The [pull request template](.github/PULL_REQUEST_TEMPLATE.md) asks for the parts that
  have been missed before.
- Contributions are accepted under the [MIT License](LICENSE) — inbound equals outbound.
