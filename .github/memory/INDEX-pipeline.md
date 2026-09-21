---
name: INDEX-pipeline
description: Image pipeline (thumbnails/scale export) and cache consistency — topic list for this category (second-level index)
metadata:
  type: index
---

# Image Pipeline and Cache Consistency (pipeline)

> Parent: [MEMORY.md](MEMORY.md) · When to read this category: changing image loading, thumbnail generation and caching, image scaling/export, or cache consistency after a caption is written

| Topic | One line | When to read |
|---|---|---|
| [thumbnail-pipeline](thumbnail-pipeline.md) | Performance-first thumbnail pipeline: PIL draft fast decode + LANCZOS scaling + disk cache + 2x DPR | Changing thumbnail size, the cache key, or the decode path, or complaining that images are blurry/slow |
| [encoder-cache-invalidation](encoder-cache-invalidation.md) | Changing a caption does not invalidate the training cache automatically — this repo's invariant #1 and its executable evidence | **Must read** before touching any code that writes captions |
| [image-scaling](image-scaling.md) | Dataset image scaling/export (§4.14): area normalization, no-upscale option, automatic caption attachment, destination-directory allowlist, never writing into the source directory | Changing scaling/export, adding output formats, or adjusting caption attachment rules |
| [image-cropping](image-cropping.md) | Dataset image cropping (§4.17): confirm a rectangle per image and archive it beside the source as `<stem>_N`; the display-coordinate (EXIF) rule, keep-format / no-caption-inherited, the box inheritance rule across resolutions, and the tagger step that reuses `/api/autotag/run` | Changing the crop endpoint, the archive naming, the confirmation dialog or its geometry, or anything that decides how a crop box is carried between images |

## Related but not in this category

- The format rules of the caption files themselves → [dataset-contract](dataset-contract.md) (see [INDEX-scope.md](INDEX-scope.md))
