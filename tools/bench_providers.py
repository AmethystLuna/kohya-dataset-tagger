"""provider performance benchmark.

The previous benchmark had two methodological errors that almost led to the opposite
conclusion, so this time they are pinned down in the code:

1. **Insufficient warm-up**: DirectML / CUDA compile kernels on first use, so image 0 can be
   3-4x the steady state. This fixes 5 warm-up images and discards them.
2. **Measuring under a different load**: last time the CPU round happened to run while the
   test suite was going, measuring 1.49 s/image (0.63-0.79 when measured quietly), so the
   "GPU is 5.4x faster" figure had load difference mixed in. This prints the machine load as
   well, and gives **each provider its own process** so they do not contaminate each other.

It also reports the median and p10/p90 instead of the mean - per-image time jitters a lot
(0.24-0.42), and the mean alone gets buried by the tail.

Usage:
    python tools/bench_providers.py --provider DmlExecutionProvider
    python tools/bench_providers.py --provider CPUExecutionProvider --threads 4
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from local_paths import dataset_root, model_roots

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

MODELS = list(model_roots())
DATA = dataset_root()


def sample_images(n: int) -> list[Path]:
    from kohya_dataset_tagger.core import paths

    paths.configure([DATA])
    imgs: list[Path] = []
    for d in paths.list_subdirs(DATA):
        imgs.extend(paths.iter_images(d))
        if len(imgs) >= n:
            break
    return imgs[:n]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", required=True)
    ap.add_argument("--model", default="wd-swinv2-tagger-v3")
    ap.add_argument("--count", type=int, default=24)
    ap.add_argument("--warmup", type=int, default=5)
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--threads", type=int, default=None)
    args = ap.parse_args(argv)

    import os
    import onnxruntime as ort

    from kohya_dataset_tagger.autotag import registry
    from kohya_dataset_tagger.autotag.wd14 import WD14Tagger

    print("=" * 68)
    print("provider      :", args.provider)
    print("onnxruntime   :", ort.__version__)
    print("claimed       :", ort.get_available_providers())
    print("logical cores :", os.cpu_count())
    if args.threads is not None:
        print("threads       :", args.threads)
    print("samples       : %d images (warm up %d discarded, %d rounds)" % (args.count, args.warmup, args.rounds))

    onnx_path, vocab_path = registry.resolve(args.model, MODELS)
    imgs = sample_images(args.count)
    assert len(imgs) == args.count

    kwargs = {"providers": [args.provider]}
    if args.threads is not None:
        kwargs["intra_op_num_threads"] = args.threads

    t0 = time.perf_counter()
    tagger = WD14Tagger(onnx_path, vocab_path, **kwargs)
    load_s = time.perf_counter() - t0
    actual = list(tagger.session.get_providers())
    print("actually used :", actual)

    for p in imgs[: args.warmup]:
        tagger.predict(p)

    times: list[float] = []
    for _ in range(args.rounds):
        for p in imgs:
            t0 = time.perf_counter()
            tagger.predict(p)
            times.append(time.perf_counter() - t0)

    times.sort()
    med = statistics.median(times)
    p10 = times[int(len(times) * 0.1)]
    p90 = times[int(len(times) * 0.9)]
    print("-" * 68)
    print("load          : %.2f s" % load_s)
    print("median        : %.3f s/image" % med)
    print("p10 / p90     : %.3f / %.3f s/image" % (p10, p90))
    print("mean          : %.3f s/image" % statistics.fmean(times))
    print("projection for 1653 images: %.1f minutes (by median)" % (med * 1653 / 60))
    print("-" * 68)
    print("RESULT %s %.3f %.3f %.3f %.1f" % (args.provider, med, p10, p90, med * 1653 / 60))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
