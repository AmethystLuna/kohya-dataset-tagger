---
name: project-architecture
description: Process/directory layering of the standalone companion tool, and its filesystem contract with the trainer
metadata:
  type: topic
---

# Architecture: separate process + filesystem contract

## What it is

This tool is a **companion tool** to `Anima-Standalone-Trainer`, not a plugin. There is exactly one relationship between them:

    trainer  ←── filesystem ──→  this tool
             dataset directory (images + .txt captions)
             dataset.toml (which image_dirs exist)
             cache_text_encoder/ · latent_cache/ (caches to invalidate)

There are no inter-process calls, no shared memory, and no importing each other's code. Breaking this boundary makes both sides hard to change.

## Why standalone

1. The trainer's UI is Express plus a single 3690-line `app.js` (`training-ui/public/js/app.js`),
   with no build and no modules. Cramming a virtual-scrolling gallery + tagger queue into it would push it past maintainability.
2. Thumbnails and the tagger's **performance requirements point to a resident Python process**: the model must stay in VRAM/RAM,
   and the decoder and thumbnail cache must be reused across requests. The trainer currently **execs one Python process per image**
   (`training-ui/server.js:1815`), which is unacceptable in a gallery scenario.
3. Once standalone, it can be restarted and tested on its own, without dragging the trainer along.

## How it's used (layering and responsibilities)

| Layer | Directory | Responsibility | Not allowed |
|---|---|---|---|
| Entry point | `__main__.py` | argument parsing, log color decision, uvicorn startup | business logic |
| Assembly | `app.py` | FastAPI instance, static assets, route registration | business logic |
| Config | `config.py` | root allowlist, thumbnail cache location, **the order of the model search roots**, read/write of the two persistent files (§4.11) | implicit state other than environment variables |
| Console | `console.py` | whether logs are colored (opens the console VT itself on Windows) | touching business state |
| Interface | `api/` | HTTP semantics, parameter validation, SSE; `api/roots.py` is the only one that **writes config** (§4.11) | touching the filesystem directly (go through `core/`) |
| Core | `core/` | path validation, caption read/write, cache invalidation, thumbnails, vocabulary | depending on FastAPI |
| Inference | `autotag/` | model loading and `predict()` | depending on FastAPI or any layer outside `core/` |
| Frontend | `web/` | native ES modules, no build | pulling in a bundler |

**Dependencies point one way, downward**: `api → core / autotag`, and `core` and `tagger` don't depend on each other.
`core/paths.py` is the single source of truth for path resolution - anything that accepts a user path must go through it.

`api/roots.py` is an **exception to watch**: it is the only module that changes config at runtime (adding/removing allowlist roots and model search roots, §4.11).
It changes in-memory state, writes the two persistent files, and then **reports the result truthfully** (`persisted` / `warning`) - a failed file write is not rolled back,
because that would make the state shown in the UI disagree with the state actually in effect, which is far worse than being one restart late.

## The actual relationship with the trainer (just these two)

    this tool ──generates──→ dataset.toml ──read by──→ trainer
    this tool ──deletes────→ cache_text_encoder/<name>_anima_te.safetensors

**No** inter-process calls, **no** reading the trainer's job config, **no** being embedded in the trainer's page.

> While tidying the docs on 2026-09-17 we found this used to say "read `dataset.toml` and list the current job's dataset in the sidebar" -
> that was an early idea that was **never implemented**. It has been deleted, so the next person doesn't go looking for something that doesn't exist.

If integration is wanted later (e.g. an "open editor" button in the trainer), the right approach is for the trainer to
**open a new browser tab** pointing at this tool's URL; don't embed this tool in the trainer's page,
because that would tangle the two lifecycles together.

---

Related: [dataset-contract](dataset-contract.md), [encoder-cache-invalidation](encoder-cache-invalidation.md)
