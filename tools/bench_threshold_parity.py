"""Whether CPU and DirectML give the same **post-threshold** output - that is what actually lands in the caption."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from local_paths import dataset_root, hf_hub

sys.path.insert(0, "src")
from kohya_dataset_tagger.core import paths
from kohya_dataset_tagger.autotag import registry
from kohya_dataset_tagger.autotag.wd14 import WD14Tagger

HUB = hf_hub()
if HUB is None:
    raise SystemExit("set 'hf_hub' in local_paths.ini")
DATA = dataset_root()
paths.configure([DATA])
onnx_path, vocab_path = registry.resolve("wd-swinv2-tagger-v3", [HUB])

imgs = []
for d in paths.list_subdirs(DATA):
    imgs.extend(paths.iter_images(d))
    if len(imgs) >= 8:
        break
imgs = imgs[:8]

cpu = WD14Tagger(onnx_path, vocab_path, providers=["CPUExecutionProvider"])
dml = WD14Tagger(onnx_path, vocab_path, providers=["DmlExecutionProvider"])

for thr in (0.35, 0.5):
    diffs = 0
    worst = 0.0
    for p in imgs:
        a = {t.tag for t in cpu.predict(p) if t.score >= thr}
        b = {t.tag for t in dml.predict(p) if t.score >= thr}
        if a != b:
            diffs += 1
            print("   %s threshold %.2f differs: CPU only %r / DML only %r"
                  % (p.name, thr, sorted(a - b)[:4], sorted(b - a)[:4]))
        # also look at the maximum score difference
        sa = {t.tag: t.score for t in cpu.predict(p)}
        sb = {t.tag: t.score for t in dml.predict(p)}
        worst = max(worst, max(abs(sa[k] - sb[k]) for k in sa))
    print("  threshold %.2f: %d of 8 images have differing tag sets | max score diff %.2e"
          % (thr, diffs, worst))
