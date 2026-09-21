---
name: local-model-inventory
description: Where the WD14 models and vocabulary files already present on this machine live, and how the registry discovers them (no download needed)
metadata:
  type: topic
---

# Model Inventory on This Machine

Scanned 2026-09-17 on the development machine. **These files are already on this machine; no network download is needed.**

## WD14 v3 (recommended)

| Model | ONNX | Vocabulary | Source |
|---|---|---|---|
| `wd-swinv2-tagger-v3` | 445.8 MB | `selected_tags.csv` | HuggingFace cache |
| `wd-vit-tagger-v3` | 361.0 MB | `selected_tags.csv` | HuggingFace cache |
| `wd-vit-tagger-v3` | 361.0 MB | `wd-vit-tagger-v3.csv` | ComfyUI plugin directory |

Exact paths:

> 2026-09-17: these **machine paths are no longer hard-coded in `config.py`** (the old `_MACHINE_MODEL_HINTS`
> was deleted); they now come from `model_paths.txt` at the repo root (a local file, not committed, editable via the 'Model directory…' dialog in the UI).
> This file is still the record of where the models actually are on this machine.

    # wd-swinv2-tagger-v3 — pick this by default (the strongest v3 available locally)
    the HuggingFace cache\models--SmilingWolf--wd-swinv2-tagger-v3\
        snapshots\627aef95638667ddcaa3ac8ae625e88ea5b02f51\model.onnx
        snapshots\627aef95638667ddcaa3ac8ae625e88ea5b02f51\selected_tags.csv

    # wd-vit-tagger-v3 (HF cache)
    the HuggingFace cache\models--SmilingWolf--wd-vit-tagger-v3\
        snapshots\7f6b584d0bd3f55c4531f14ba3d4761b2bccdc0f\model.onnx
        snapshots\7f6b584d0bd3f55c4531f14ba3d4761b2bccdc0f\selected_tags.csv

    # wd-vit-tagger-v3 (ComfyUI copy; different CSV filename)
    a local ComfyUI installation's WD14-Tagger model directory\wd-vit-tagger-v3.onnx
    a local ComfyUI installation's WD14-Tagger model directory\wd-vit-tagger-v3.csv

## Older versions (not recommended, kept for reference only)

    # wd-v1-4-vit-tagger-v2 — v2, weaker than v3
    the HuggingFace cache\models--SmilingWolf--wd-v1-4-vit-tagger-v2\
        snapshots\1f3f3e8ae769634e31e1ef696df11ec37493e4f2\model.onnx
        snapshots\1f3f3e8ae769634e31e1ef696df11ec37493e4f2\selected_tags.csv

## Not present on this machine

- `wd-eva02-large-tagger-v3` (the best-quality v3 variant) — **not** on this machine.
  Using it requires a network download (about 1.2 GB). V0.1 does not make it the default; it stays an optional registry entry.
- `wd-convnext-tagger-v3` — not on this machine.
- Camie Tagger / JoyTag / ML-Danbooru — none of them.
- `animetimm/*` (the AnimeTIMM family) — none of them; `caformer_b36.dbv4-full` among them is
  the backbone of `sorryhyun/anima-tagger` and is **GPL-3.0 + gated**.

For the models that appeared in 2026 (anima-tagger / pixai v1.0 / kagami-24k and so on) and their trade-offs,
see [tagger-model-landscape](tagger-model-landscape.md).

## Notes about paths

1. **Do not hard-code snapshot hashes.** The `snapshots\<sha>\` part of an HF cache path changes when the model is re-downloaded.
   The registry should discover via the glob `models--SmilingWolf--<name>\snapshots\*\model.onnx`,
   or store the **directory** in config instead of a full file path.
2. **Do not write into dataset directories.** These are external resources referenced read-only.
3. `onnxruntime` is **not** in the trainer checkout's `venv` (verified missing), so it needs to be installed.
   The other two environments already have it: the local ComfyUI installation's bundled Python environment,
   a local webui's bundled Python environment (only for checking version compatibility; do not import from another venv directly).

---

Related: [wd14-tagger](wd14-tagger.md)
