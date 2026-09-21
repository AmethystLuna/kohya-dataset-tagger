"""Baseline benchmark for this repository's batch operations.

Non-destructive by construction:
- writes only under --scratch (must sit below the scratch root configured in local_paths.ini),
- reads the real dataset read-only (only to sample images for the scale bench),
- never touches a real training cache and never writes into the dataset.

Usage:
    .venv/Scripts/python.exe tools/bench_batch_ops.py --count 200 --real-sample 24 --repeats 3
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from local_paths import dataset_root, scratch_root

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from kohya_dataset_tagger.api import captions as captions_api
from kohya_dataset_tagger.core import cache_invalidation, captions, scaler

#: None unless local_paths.ini configures one - every write below is refused without it.
SCRATCH_PREFIX = scratch_root()
REAL_ROOT = dataset_root()
TE_DIR = "cache_text_encoder"
LATENT_DIR = "latent_cache"
BASELINE = "1girl, solo, long_hair, blue_eyes, smile"
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")


def guard_scratch(raw):
    if SCRATCH_PREFIX is None:
        raise SystemExit("refusing to write without a scratch root: set 'scratch' in local_paths.ini")
    path = Path(raw).resolve()
    prefix = SCRATCH_PREFIX.resolve()
    text = str(path).lower()
    if not text.startswith(str(prefix).lower()):
        raise SystemExit("refusing to write outside the scratch root: " + str(path))
    if path == prefix:
        raise SystemExit("refusing to use the scratch root itself")
    return path


def build_dataset(root, count):
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    images = []
    for index in range(count):
        sub = root / ("sub%03d" % (index % 40))
        sub.mkdir(exist_ok=True)
        image = sub / ("img%05d.png" % index)
        Image.new("RGB", (32, 32), (index % 255, 40, 90)).save(image)
        image.with_suffix(".txt").write_text(BASELINE, encoding="utf-8")
        images.append(image)
    return images


def reset_captions(images):
    for image in images:
        image.with_suffix(".txt").write_text(BASELINE, encoding="utf-8")


def materialize_caches(images, size=4096):
    for image in images:
        for candidate in cache_invalidation.text_encoder_cache_paths(image):
            candidate.parent.mkdir(parents=True, exist_ok=True)
            candidate.write_bytes(b"x" * size)


def timed(fn, repeats, setup=None):
    samples = []
    for _ in range(repeats):
        if setup is not None:
            setup()
        start = time.perf_counter()
        fn()
        samples.append(time.perf_counter() - start)
    return {
        "median_s": round(statistics.median(samples), 4),
        "min_s": round(min(samples), 4),
        "max_s": round(max(samples), 4),
        "samples_s": [round(value, 4) for value in samples],
    }


def bench_preview_and_write(images, repeats):
    payload = captions_api.CaptionBatchRequest(
        images=[str(path) for path in images], op="append", tags=["bench_tag"]
    )

    def preview():
        for image in images:
            captions_api._batch_step(image, "append", payload, True)

    def write():
        for image in images:
            captions_api._batch_step(image, "append", payload, False)

    return {
        "images": len(images),
        "preview_dry_run": timed(preview, repeats, setup=lambda: reset_captions(images)),
        "write_sequential": timed(write, repeats, setup=lambda: reset_captions(images)),
    }


def bench_cache(images, repeats):
    def rebuild():
        for image in images:
            cache_invalidation.invalidate_text_encoder_cache(image)

    return {
        "images": len(images),
        "cache_invalidate_sequential": timed(
            rebuild, repeats, setup=lambda: materialize_caches(images)
        ),
    }


def sample_real_images(count):
    files = []
    for dirpath, dirnames, filenames in os.walk(REAL_ROOT):
        dirnames[:] = [
            name for name in dirnames if name not in (TE_DIR, LATENT_DIR) and not name.startswith(".")
        ]
        for name in sorted(filenames):
            if Path(name).suffix.lower() in IMAGE_EXTS:
                files.append(Path(dirpath) / name)
    if not files:
        return []
    step = max(1, len(files) // count)
    return files[::step][:count]


def bench_scale(images, target_dir, output_format, optimize, repeats):
    def run():
        if target_dir.exists():
            shutil.rmtree(target_dir)
        for src in images:
            dest_dir = scaler.destination_directory(src, REAL_ROOT, target_dir)
            scaler.process_one_image(
                src,
                dest_dir,
                target_width=1536,
                target_height=1536,
                no_upscale=True,
                resample="lanczos",
                output_format=output_format,
                quality=95,
                optimize=optimize,
                caption_extension=".txt",
            )

    result = timed(run, repeats)
    result["images"] = len(images)
    result["per_image_ms"] = round(1000.0 * result["median_s"] / max(1, len(images)), 2)
    return result


def bench_http(root, images):
    from fastapi.testclient import TestClient

    from kohya_dataset_tagger import config
    from kohya_dataset_tagger.app import create_app

    config.load(roots=[root], thumb_cache_dir=root.parent / "thumbs", model_search_roots=[])
    app = create_app()
    problems = getattr(app.state, "router_errors", [])
    if problems:
        return {"error": str(problems)}

    out = {"images": len(images)}
    with TestClient(app) as client:
        def seq_get():
            for image in images:
                response = client.get("/api/captions", params={"path": str(image)})
                response.raise_for_status()

        def batch_preview():
            response = client.post(
                "/api/captions/batch",
                json={
                    "images": [str(path) for path in images],
                    "op": "append",
                    "tags": ["bench_tag"],
                    "dry_run": True,
                },
            )
            response.raise_for_status()

        def batch_read():
            response = client.post(
                "/api/captions/read", json={"images": [str(path) for path in images]}
            )
            response.raise_for_status()

        out["get_caption_one_by_one"] = timed(seq_get, 1)
        out["get_caption_one_by_one"]["per_request_ms"] = round(
            1000.0 * out["get_caption_one_by_one"]["median_s"] / max(1, len(images)), 3
        )
        out["batch_dry_run_single_request"] = timed(batch_preview, 1)
        out["batch_read_single_request"] = timed(batch_read, 1)
    return out

def bench_write_parallel(images, workers, repeats):
    payload = captions_api.CaptionBatchRequest(
        images=[str(path) for path in images], op="append", tags=["bench_tag"]
    )

    def task(image):
        captions_api._batch_step(image, "append", payload, False)

    def run():
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(task, images))

    result = timed(run, repeats, setup=lambda: reset_captions(images))
    result["workers"] = workers
    result["per_image_ms"] = round(1000.0 * result["median_s"] / max(1, len(images)), 3)
    return result


def bench_scale_parallel(real, target, workers):
    def task(src):
        dest_dir = scaler.destination_directory(src, REAL_ROOT, target)
        scaler.process_one_image(
            src,
            dest_dir,
            target_width=1536,
            target_height=1536,
            no_upscale=True,
            resample="lanczos",
            output_format="png",
            quality=95,
            optimize=True,
            caption_extension=".txt",
        )

    def run():
        if target.exists():
            shutil.rmtree(target)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(task, real))

    result = timed(run, 1)
    result["workers"] = workers
    result["images"] = len(real)
    result["per_image_ms"] = round(1000.0 * result["median_s"] / max(1, len(real)), 2)
    return result

def pick_real_dir(min_images):
    """The real dataset directory with the most images (its images are the most realistic sample)."""
    best = None
    best_count = min_images - 1
    for dirpath, dirnames, filenames in os.walk(REAL_ROOT):
        dirnames[:] = [
            name for name in dirnames if name not in (TE_DIR, LATENT_DIR) and not name.startswith(".")
        ]
        count = sum(1 for name in filenames if Path(name).suffix.lower() in IMAGE_EXTS)
        if count > best_count:
            best, best_count = Path(dirpath), count
    return best


def bench_export_job(images, root, target, workers):
    """Drive the real api/scale.py _export job path (not just process_one_image)."""
    from kohya_dataset_tagger.api import scale as scale_api

    settings = {
        "target_width": 1536,
        "target_height": 1536,
        "no_upscale": True,
        "resample": "lanczos",
        "output_format": "png",
        "quality": 95,
        "optimize": True,
        "caption_extension": ".txt",
        "write_dataset_toml": False,
    }
    previous = scale_api.EXPORT_WORKERS
    scale_api.EXPORT_WORKERS = workers
    try:
        job = scale_api.ScaleJob("bench-%d" % workers, len(images), root, target)
        if target.exists():
            shutil.rmtree(target)
        start = time.perf_counter()
        scale_api._export(job, images, settings)
        elapsed = time.perf_counter() - start
    finally:
        scale_api.EXPORT_WORKERS = previous
    files = sum(1 for entry in target.rglob("*") if entry.is_file())
    return {
        "workers": workers,
        "images": len(images),
        "seconds": round(elapsed, 3),
        "per_image_ms": round(1000.0 * elapsed / max(1, len(images)), 2),
        "processed": job.done,
        "errors": len(job.errors),
        "files": files,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scratch",
        default=str(SCRATCH_PREFIX / "kohya-batch-perf") if SCRATCH_PREFIX is not None else "",
    )
    parser.add_argument("--count", type=int, default=200, help="synthetic images for the caption/cache benches")
    parser.add_argument("--real-sample", type=int, default=24, help="real images sampled for the scale bench")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--scale-repeats", type=int, default=1)
    parser.add_argument("--export-job", action="store_true", help="also bench the real api/scale _export job")
    parser.add_argument("--export-count", type=int, default=40, help="images for the export-job bench")
    parser.add_argument("--parallel", action="store_true", help="also bench ThreadPoolExecutor variants")
    parser.add_argument("--parallel-workers", default="1,2,4,8")
    parser.add_argument("--skip-scale", action="store_true")
    parser.add_argument("--skip-http", action="store_true")
    parser.add_argument("--json", default="")
    args = parser.parse_args()

    scratch = guard_scratch(args.scratch)
    scratch.mkdir(parents=True, exist_ok=True)
    root = scratch / "synthetic"

    result = {
        "scratch": str(scratch),
        "count": args.count,
        "real_sample": args.real_sample,
        "repeats": args.repeats,
        "cpu_count": os.cpu_count(),
    }
    images = build_dataset(root, args.count)
    result["caption_ops"] = bench_preview_and_write(images, args.repeats)
    result["cache_ops"] = bench_cache(images, args.repeats)

    if not args.skip_http:
        httproot = scratch / "httpds"
        http_images = build_dataset(httproot, min(args.count, 100))
        result["http"] = bench_http(httproot, http_images)

    if not args.skip_scale:
        real = sample_real_images(args.real_sample)
        result["scale_sample"] = {
            "images": len(real),
            "first": str(real[0]) if real else "",
            "total_mb": round(sum(path.stat().st_size for path in real) / 1e6, 1),
        }
        if real:
            result["scale_png"] = bench_scale(
                real, scratch / "scale_png", "png", True, args.scale_repeats
            )
            result["scale_png_fast"] = bench_scale(
                real, scratch / "scale_png_fast", "png", False, args.scale_repeats
            )

    if args.export_job:
        source_dir = pick_real_dir(args.export_count)
        result["export_job"] = {"source_dir": str(source_dir) if source_dir else ""}
        if source_dir is None:
            result["export_job"]["error"] = "no real directory with %d images" % args.export_count
        else:
            export_images = sorted(
                entry
                for entry in source_dir.iterdir()
                if entry.is_file() and entry.suffix.lower() in IMAGE_EXTS
            )[: args.export_count]
            result["export_job"]["runs"] = [
                bench_export_job(
                    export_images, source_dir, scratch / ("export_seq_%d" % workers), workers
                )
                for workers in (1, 4, max(1, min(8, os.cpu_count() or 1)))
            ]

    if args.parallel:
        workers = [int(part) for part in args.parallel_workers.split(",") if part.strip()]
        result["parallel"] = {
            "write": [bench_write_parallel(images, value, args.repeats) for value in workers],
            "workers": workers,
        }
        real_par = sample_real_images(args.real_sample)
        if real_par:
            result["parallel"]["scale_png"] = [
                bench_scale_parallel(real_par, scratch / ("scale_par_%d" % value), value)
                for value in workers
            ]

    text = json.dumps(result, indent=2, ensure_ascii=False)
    print(text)
    if args.json:
        Path(args.json).write_text(text, encoding="utf-8")
    return result


if __name__ == "__main__":
    main()
