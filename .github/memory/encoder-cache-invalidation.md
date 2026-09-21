---
name: encoder-cache-invalidation
description: Changing a caption does not invalidate the training cache automatically — this repo's invariant #1 and its executable evidence
metadata:
  type: topic
---

# Writing a caption must invalidate the training cache (invariant #1)

## Conclusion

**The trainer does not rebuild its cache because the contents of a `.txt` changed.** Edit tags with this tool and start training right away,
and training will **silently use the text embeddings of the old caption**, with no error and no warning.

Therefore: **every code path that writes a caption must delete the corresponding TE cache file in the same operation.**

## Evidence (executable, not reasoning)

Experiment: build a structurally valid cache file → call the trainer's **real**
`AnimaTextEncoderOutputsCachingStrategy.is_disk_cached_outputs_expected()` → change the same-named `.txt` → call it again.

    cache path      : <image_dir>/cache_text_encoder/sample01_anima_te.safetensors
    cached-before-edit: True
    cached-after-edit : True      ← caption changed, the cache is still valid
    same cache path  : True

Code basis:

| Fact | Location |
|---|---|
| The cache path depends only on the image path | `library/strategy_anima.py:284-293` |
| The validity check only looks at whether the 4 keys exist | `library/strategy_anima.py:295-349` |
| No hash / mtime / sha1 check anywhere in the file | grep confirms 0 hits |
| The training UI enables disk TE caching by default | `training-ui/server.js:363` (`?? true`) |
| Latent cache directory | `library/strategy_base.py:431` |

## What to implement

### When writing a caption

1. Atomically write the `.txt`
2. **Delete** `<image_dir>/cache_text_encoder/<stem>_anima_te.safetensors`
   and the same-named `.npz` variant (`strategy_anima.py:299-300` reads both paths)
3. A failed delete (locked/permission) must **report an error to the user** and must not be swallowed — a silent failure means silently using the old tags

### After batch operations

- After a batch edit / tagger write, invalidate one by one; do not delete everything and start over
  (the latent cache needs no invalidation: it depends only on image pixels, not on captions)
- But changes to a subset's `flip_aug` / `alpha_mask` / `random_crop` do affect the latent cache
  (`strategy_anima.py:436` onward), so invalidate `latent_cache/` when changing those parameters

### Provide visibility

- Status panel: compare the `.txt` mtime with the cache file mtime and list entries that look stale
- Pre-training warning: give a clear warning when stale entries are found (this tool only warns; it does not modify the trainer)

## All three paths must be verified

1. **Normal**: edit a caption → the cache file disappears → the next training run rebuilds it
2. **Failure**: the cache file is locked/read-only → the user sees a clear error and the caption is not corrupted either
3. **Recovery**: delete the whole `cache_text_encoder/` directory → the status panel no longer reports staleness

Do round-trip tests only in a temporary directory; do **not** test against a real dataset.

---

Related: [dataset-contract](dataset-contract.md), [thumbnail-pipeline](thumbnail-pipeline.md)
