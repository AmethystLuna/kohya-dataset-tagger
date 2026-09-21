---
name: INDEX-tagging
description: WD14 inference, model landscape, vocabularies, and the local model inventory — topic list for this category (second-level index)
metadata:
  type: index
---

# WD14 Inference, Model Landscape and Model Inventory (tagging)

> Parent: [MEMORY.md](MEMORY.md) · When to read this category: integrating a tagger, choosing a model, tuning thresholds and post-processing, finding model files already on this machine

| Topic | One line | When to read |
|---|---|---|
| [tagger-model-landscape](tagger-model-landscape.md) | Tagger model landscape (2026-09): the WD family stopped at v3, plus anima-tagger built for Anima | Wanting to switch models, wondering whether v3 is outdated, or integrating a second tagger |
| [wd14-tagger](wd14-tagger.md) | WD14 v3 ONNX inference contract: preprocessing, classification thresholds, post-processing options, output policy | Implementing or changing a tagger, tuning thresholds, aligning behavior with sd-scripts |
| [local-model-inventory](local-model-inventory.md) | Where the WD14 models and vocabulary files already present on this machine live, and how the registry discovers them (no download needed) | Setting the default model, or finding out whether a download is needed |

## Related but not in this category

- Data source of the tag-autocomplete vocabulary → [dataset-contract](dataset-contract.md) (see [INDEX-scope.md](INDEX-scope.md))
- Cache invalidation after tagger output is written to disk → [encoder-cache-invalidation](encoder-cache-invalidation.md) (see [INDEX-pipeline.md](INDEX-pipeline.md))
