---
name: tagger-model-landscape
description: Tagger model landscape (2026-09): the WD family stopped at v3, plus anima-tagger built for Anima
metadata:
  type: topic
---

# Tagger Model Landscape (verified 2026-09-17)

## One line

**The WD family really did stop at v3** — `wd-eva02-large-tagger-v3` is SmilingWolf's last release (2024-07-28),
with no v4 for over two years. But better things appeared in 2026, and one of them is **made specifically for Anima**.

## The WD family (SmilingWolf) is no longer updated

All releases sorted by lastModified:

| Model | Released | Downloads | Notes |
|---|---|---|---|
| wd-eva02-large-tagger-v3 | 2024-07-28 | 19,554 | **Strongest in the family**, 8,106 vocabulary |
| wd-vit-large-tagger-v3 | 2024-07-26 | 1,093 | |
| wd-vit-tagger-v3 | 2024-03-16 | 10,205 | |
| wd-swinv2-tagger-v3 | 2024-03-16 | **696,198** | De facto standard, already on this machine |
| wd-convnext-tagger-v3 | 2024-03-16 | 913 | |

Two years and two months with no new release. **The v3 option set (`--remove_underscore` / `undesired_tags` /
`always_first_tags` / `tag_replacement` …) is still the lingua franca of the kohya ecosystem**, and that has not changed.

## What is new in 2026

| Model | Released | License | Form | Why it is worth a look |
|---|---|---|---|---|
| **`sorryhyun/anima-tagger`** | 2026-09-13 | MIT (the in-house part) | PyTorch/timm | **The output is exactly the caption format Anima trains on**, see the next section |
| `pixai-labs/pixai-tagger-v1.0` | 2026-09-16 | Unspecified | transformers | 30,877 vocabulary, cutoff 2026-05, 486M @1008px |
| `Redstonexs/kagami-24k` | 2026-08-27 | Apache-2.0 | ONNX | EVA02-L fine-tune + 24k vocabulary; the author's own test beats eva02 |
| `Redstonexs/danbooru-tagger-v1` | 2026-08-25 | Apache-2.0 | ONNX | Same, previous release |
| `Camais03/camie-tagger-v2` | 2026-01-01 | GPL-3.0 | safetensors/ONNX | 70,527 vocabulary; but third-party evaluations find it **worse than WD v3** |
| `animetimm/caformer_b36.dbv4-full` | 2025-06-02 | **GPL-3.0 and gated** | ONNX + safetensors | Part of the AnimeTIMM family, and the backbone of anima-tagger |

## Three evaluations (different measurement rules, do not compare across them)

**Redstonexs' evaluation** (11,639 images newer than every model's training cutoff, using the 7,779 tags in the intersection of seven vocabularies):

| Model | micro-F1 | macro-F1 | macro-AP | Vocabulary |
|---|---|---|---|---|
| kagami-24k | **0.6710** | **0.5138** | **0.5086** | 24,000 |
| danbooru-tagger-v1 | 0.6569 | 0.4889 | 0.4672 | 24,000 |
| wd-eva02-large-v3 | 0.6369 | 0.4728 | 0.4668 | 8,106 |
| wd-vit-large-v3 | 0.6366 | 0.4574 | 0.4576 | 8,106 |
| wd-swinv2-v3 | 0.6380 | 0.4522 | 0.4549 | 8,106 |
| **camie-tagger-v2** | 0.5848 | 0.3592 | 0.3509 | 30,841 |

**PixAI's evaluation** (8,407 shared general tags, 50,416 images):

| Model | micro-F1 | macro-F1 | mAP |
|---|---|---|---|
| pixai-tagger-v1.0 | **0.6660** | **0.3885** | **0.3807** |
| AnimeTIMM CAFormer B36 | 0.6435 | 0.3539 | 0.3324 |
| AnimeTIMM EVA Giant | 0.6410 | 0.3796 | 0.3391 |
| AnimeTIMM EVA02 Large | 0.6401 | 0.3742 | 0.3423 |
| pixai-tagger-v0.9 | 0.5980 | 0.3286 | 0.3251 |
| **camie-tagger-v2** | 0.5775 | 0.2109 | 0.2245 |

**Both independent evaluations agree that camie-tagger-v2 is below WD v3**, even though its own model card claims 67.3% micro F1.
This is a textbook example of test-set and vocabulary differences — **do not trust the self-reported numbers on a model card**.

## Trade-offs for this repo

### Default: unchanged, `wd-swinv2-tagger-v3`

Zero cost (already on this machine), a one-to-one match for sd-scripts' option semantics, and ONNX runs fine. **It is the default not because it is the best,
but because it is the baseline** — any replacement must be compared against it.

### Recommended upgrade: `sorryhyun/anima-tagger` (built for Anima)

It is currently the only tagger whose **output format is exactly the caption Anima trains on**:

    rating, count, characters, copyrights, general tags

- A 2,532-tag **Anima vocabulary** (not WD's 8,106), with categories, emit order, and 60 tag groups
- Per-tag thresholds (median 0.32) and mutually exclusive softmax groups (eye/hair color is 'at most one')
- **count tag rules** (`1girl`/`2girls`…) — exactly the slots Anima captions need
- `rules.yaml`: alias folding (`silver hair` folded into `grey hair`), always-remove, clothing dedup
- The in-house part is only a few MB (vocabulary + rules + sidecar head); the backbone comes from `hf_hub_download`

**Costs and limitations** (written down because they become implementation constraints):

| Item | Content |
|---|---|
| backbone | `animetimm/caformer_b36.dbv4-full`, **GPL-3.0 and gated** — requires `hf auth login` and accepting the upstream terms |
| Dependencies | Requires `timm` (the local venv does **not** have it); a PyTorch path, not ONNX |
| No artist | dbv4 has no artist category, so the artist slot of an Anima caption is **always empty**; 92 artist tags are hard-disabled |
| artist OC | Always outputs `original` |
| Small vocabulary | 2,532 vs WD's 8,106 — for Anima this is a **feature** (close to what the model knows), but detail coverage is lower |

### Not adopted for now

- `kagami-24k` / `danbooru-tagger-v1`: the best numbers, but a 24k vocabulary is far from Anima's distribution,
  and they are new 2026-08 repos (4 likes / 0 downloads) with no community validation. Usable as controlled experiments, not as defaults.
- `pixai-tagger-v1.0`: released only a day ago, 486M parameters @1008px is slow, and `trust_remote_code=True`
  **executes remote code from the repository** — it must be evaluated separately before adoption.
- `camie-tagger-v2`: both independent evaluations put it below WD v3.

## Architectural conclusion (important)

**The abstraction surface of `autotag/base.py` can only go as far as 'output `[{tag, score, category}]`', not as far as 'preprocessing'.**

| | WD14 v3 | anima-tagger | pixai v1.0 |
|---|---|---|---|
| Framework | ONNX Runtime | PyTorch + timm | transformers |
| Input | 448, BGR, 0-255, NHWC | 384, its own `preprocess.json` | 1008, its own processor |
| Vocabulary | 8,106 | 2,532 | 30,877 |
| Post-processing | kohya option set | rules/groups/count-tag/sidecar | per-category thresholds |

No item in the four columns can share a code path. The current design keeping preprocessing inside each implementation is **right**,
and that conclusion was confirmed when the second model was integrated.

---

Related: [wd14-tagger](wd14-tagger.md), [local-model-inventory](local-model-inventory.md)
