---
name: changelog/bootstrap
description: How the repository scaffolding and the seven scope decisions were settled (2026-09-17)
metadata:
  type: topic
---

# 2026-09-17 Scaffolding and scope settle

## Background

The requirement was "a dataset editor that also covers folder browsing, image preview, tag editing, tag filtering, and calling a tagger model to emit prompt files".
We did a round of research first (the output lives in the trainer repo's `DATASET_EDITOR_RESEARCH.md`), then fixed the scope and opened the repository.

## Key facts found during research

Verification against the trainer's source (not recollection):

1. **Editing a caption does not invalidate the TE cache.** Confirmed by an executable experiment — build a valid cache file → call the real
   `is_disk_cached_outputs_expected()` → edit the `.txt` → it still returns `True`.
   This got promoted from "research finding" to the repository's **invariant #1**.
2. `image_dir` contains a mix of `latent_cache/` and `cache_text_encoder/`, so the browser must filter them.
3. The trainer has no WD14 tagger script (neither `finetune/` nor `tools/` exists), so we write our own.
4. `Anima-Standalone-Trainer/venv` is missing `onnxruntime` / `onnx` / `timm`; Pillow / numpy / torch /
   transformers / cv2 are all there. That venv is **Python 3.10.11**.
5. This machine already has several WD14 v3 model files, so no network is needed (see [local-model-inventory](../local-model-inventory.md)).
6. The trainer's `/api/jobs/:name/samples/*` route concatenates paths without validating `..` (`training-ui/server.js:1800`)
   — this repository does not copy that pattern.
7. The trainer's image thumbnailing execs a Python process per image (`server.js:1815`) — unacceptable for a gallery.

## The seven decisions and why

Summary in [scope-and-decisions](../scope-and-decisions.md). Key points of the decision process:

- **Output = `.txt` sidecar**: the user chose the former between a "`.txt` sidecar" and a "`sample_prompts.txt`-style prompt list".
  The latter is the trainer's Prompts tab's job, not this tool's.
- **Accept onnxruntime**: the user accepted the ~200 MB dependency and asked whether the "v3 model is already in the parent folder's webui"
  — verified true, and more than one copy. So the default model became `wd-swinv2-tagger-v3`.
- **Danbooru tags only**: drop VLM natural-language captions, for dependency size and to "get the loop working first".
- **Thumbnails "performance first + maximum clarity"**: the original words, which translate into the implementation as
  less decoding in `draft()` + LANCZOS + DPR 2x + disk cache. See [thumbnail-pipeline](../thumbnail-pipeline.md).
- **On the order of a few hundred images**: so we do not chase extreme virtual scrolling, but the thumbnail disk cache still has to happen.
- **No compatibility with non-kohya layouts**: no promises about `metadata.json` / DreamBooth directory-name prefixes.
- **A standalone page**: not one tab of the trainer → which directly decided that this repository exists on its own.

## Repository scaffolding

Following the development-workflow framework of the author's earlier project (a checkout outside this repository):

- `AGENTS.md` permanent index (28 KB budget) + a `.github/memory/` second-level index
- `.agents/skills/` holds the workflow skills
- `test/test_docs_index.py` documentation-structure gate

**One difference from the reference framework**: no Claude compatibility is needed, so there is **no** `CLAUDE.md` bridge file,
and the gate has no "bridge budget" rule. AGENTS.md is the single source of truth for permanent instructions.

## Not started yet

This entry records the setup of the scaffolding. The backend and the frontend are both empty — `src/kohya_dataset_tagger/` has not been started.

---

Related: [scope-and-decisions](../scope-and-decisions.md), [INDEX-history](../INDEX-history.md)
