---
name: wd14-tagger
description: WD14 v3 ONNX inference contract: preprocessing, classification thresholds, post-processing options, output policy
metadata:
  type: topic
---

# WD14 v3 Inference Contract

## What it is

SmilingWolf's WD14 tagger v3 series: an ONNX model plus a `selected_tags.csv` vocabulary.
Multi-label classification: each image gets an independent probability per tag, and a **classification threshold** decides whether it is kept.

See [local-model-inventory](local-model-inventory.md) for the model file locations on this machine — **no network download is needed**.

## Preprocessing (copy it, do not "improve" it)

Reference implementation: sd-scripts `finetune/tag_images_by_wd14_tagger.py:37-64`.

```python
# 1. Composite alpha onto a white background
if image.mode in ("RGBA", "LA") or "transparency" in image.info:
    image = image.convert("RGBA")
elif image.mode != "RGB":
    image = image.convert("RGB")
if image.mode == "RGBA":
    bg = Image.new("RGB", image.size, (255, 255, 255))
    bg.paste(image, mask=image.split()[3])
    image = bg

# 2. RGB -> BGR
arr = np.array(image)[:, :, ::-1]

# 3. Pad to a square centered on white (255)
size = max(arr.shape[0:2])
pad_x, pad_y = size - arr.shape[1], size - arr.shape[0]
arr = np.pad(arr, ((pad_y // 2, pad_y - pad_y // 2),
                   (pad_x // 2, pad_x - pad_x // 2), (0, 0)),
             mode="constant", constant_values=255)

# 4. Resize to 448 and convert to float32
arr = resize_image(arr, arr.shape[0], arr.shape[1], 448, 448).astype(np.float32)
```

**Four things that must not be wrong**:

| Item | WD14 v3 | Common mistake |
|---|---|---|
| Channel order | **BGR** | Using RGB → systematic errors on color tags |
| Padding value | **255 (white)** | Padding 0 (black) → dark-background tags are activated |
| Value range | **raw 0–255 float32** | Dividing by 255 → probabilities collapse across the image |
| Tensor layout | **NHWC** `(N,448,448,3)` | Converting to CHW → an immediate shape error or garbage tags |

For batched inference, just `np.stack(images)`; take the output with `ort_sess.run(None, {input_name: imgs})[0]`.

## Vocabulary and categories

The columns of `selected_tags.csv` are `tag_id,name,category,count`:

| category | Meaning | Use |
|---|---|---|
| 0 | General | Default threshold 0.35 |
| 1 | Artist | Separate threshold; style LoRAs care about it |
| 3 | Copyright | Work/series name |
| 4 | Character | Character name; supports `chara_(series)` expansion |
| 5 | Meta | Metadata |
| 9 | Rating | **The first 4 labels are ratings**; take one with argmax, no threshold |

This CSV is **both** the vocabulary source for tag autocomplete and the category basis for Danbooru-order sorting
(see the tag order section of [dataset-contract](dataset-contract.md)). The `count` column gives tag popularity,
which can be used to rank autocomplete candidates.

Note that the filename is not consistent: in HF repos it is `selected_tags.csv`, while ComfyUI's copy is `wd-vit-tagger-v3.csv`.
The registry must discover it as `*.csv` in the same directory, rather than hard-coding the filename.

## Post-processing options (aligned with sd-scripts semantics)

| Option | Behavior |
|---|---|
| Per-category thresholds | general / character / copyright / artist / meta each adjustable |
| rating strategy | drop / first / last |
| `remove_underscore` | underscore → space |
| `undesired_tags` | global blocklist (the comparison happens **after** underscore replacement) |
| `always_first_tags` | e.g. `1girl` — force it to the front when it appears in the result |
| `character_tags_first` | move character tags to the front as a group |
| `character_tag_expand` | `chara_(series)` → `chara, series` |
| `tag_replacement` | replacement table, applied **after** `character_tag_expand` |
| Output separator | `", "` (consistent with the caption sidecar contract) |

**Confidence scores must be persisted.** Besides writing `.txt`, also store `[{tag, score, category}]` into a sidecar index,
otherwise tuning the threshold later means re-running the whole batch.

## Output policy

| Strategy | Semantics |
|---|---|
| overwrite | overwrite directly |
| append | append to the end of the existing caption |
| prepend | put first |
| only_if_empty | write only when there is no caption file |
| merge | merge and de-duplicate (stable order: existing tags first) |

Plus a **protected list**: the tags listed there are never overwritten (manual tags win).

**A diff preview must be produced before writing** (a table from old caption → new caption); write only after the user confirms.
Writing goes through the [encoder-cache-invalidation](encoder-cache-invalidation.md) flow.

Changes must be marked on **both sides** of the table (added 2026-09-18): added tags get a **green box** in the new caption column,
deleted tags get a **red box + strikethrough** in the old caption column, and unchanged ones keep the original color.

- Marking only the right side is not enough: before the change, the `before` cell is one block of plain text, and **deleted tags are simply invisible**,
  yet they are exactly what you lose by pressing the button; the CSS rule `.diff .del` was even dead code with **no JS call site at all**.
- An empty cell is marked 'none'; when **there was a caption and this side will be cleared**, that 'none' gets a **red box**
  (both sides empty = no loss at all, so no red box — otherwise it is a false positive).
- Both sides share one render function `renderCaptionDiff` (exported by `web/js/autotag.js`), so there is no
  'only one side was changed' drift; the separator is always the trainer's `", "`.
- Criterion: `test/test_web_diff.py` + the headless fixture `test/fixtures/diff_harness.mjs`
  (a fake DOM runs it for real, asserting **which chip gets which class** — a static criterion can only prove the class name appears).

## Three behaviors settled by measurement (2026-09-17)

### `escape_parens` defaults to true — not a taste, evidence

The vocabulary has `keqing_(genshin_impact)`, and after `remove_underscore` it is `keqing (genshin impact)`, **unescaped**.
But both conventions in this repo use the escaped form:

1. **1049 files** in the dataset write `hu tao \(genshin impact\)` and `asuna \(blue archive\)`;
2. The repo's own inference prompt `configs/sample_prompts.txt:4` writes `mika \(blue archive\)`.

**Both the training side and the inference side write `\(`, so the tagger output must also be `\(`**, otherwise the captions it writes do not match existing captions.
This applies only to tagger output; it does not touch tags typed by the user (where `(masterpiece:1.2)` weight syntax may appear).

### sd-scripts' `tag_replacement` is a no-op (upstream bug, we do not replicate it)

In `process_tag_replacement` of `finetune/tag_images_by_wd14_tagger.py`,
`tags = tag_replacements_arg.split(",")` **shadows the `tags` parameter**,
so the replacement branch always acts on a 2-element local table — **documented, but never actually done**.
This repo implements the documented intent. Anyone comparing the two outputs must know this difference first.

### Two ordering quirks we do not replicate

sd-scripts' `always_first_tags` inserts each entry with `insert(0)`, which **reverses** the given order,
and `character_tags_first` also reverses the relative order of character tags. This repo keeps the given order.

### Thread knob for CPU inference (measured 2.5x)

On a machine with 16 logical cores **that is also running training**:

| Setting | Inference time |
|---|---|
| ORT default (uses all cores) | 3.45 s/image |
| `intra_op_num_threads=4` | **1.39 s/image** |
| `intra_op_num_threads=1` | 2.75 s/image |

On this machine training is usually running, so this knob has to exist (a keyword argument of `WD14Tagger.__init__`,
configured via `KOHYA_TAGGER_ORT_THREADS`). Batch budget (1653 images): about 22 minutes when quiet, 68–88 minutes under heavy load.
Also note: **on CPU, larger batches are slower** (0.45 → 0.92 s/image), so batch jobs should just run one image at a time.

## How to use it

- Models and sessions are **long-lived**: loading a 360–450 MB ONNX happens once, not per job
- Try providers in order **`CUDAExecutionProvider` → `DmlExecutionProvider` (DirectML) → `CPUExecutionProvider`** (`wd14.DEFAULT_PROVIDERS`, filtered by `select_providers`), and show in the UI which one is **actually** in use. DirectML was missing here until 2026-09-19, so on the DirectML install the Windows setup scripts produce the tagger silently ran on CPU (0.75 s/image vs 0.196): [changelog/tagger-gpu-provider](changelog/tagger-gpu-provider.md)
- Push progress over SSE (once per image processed); support pause/resume/cancel
- Persist the processed list: a re-run after an interruption does not append duplicates (idempotent)
- Log failures per image and continue; do not abort the whole job

---

Related: [local-model-inventory](local-model-inventory.md), [encoder-cache-invalidation](encoder-cache-invalidation.md)
