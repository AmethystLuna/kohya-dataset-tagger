"""Report the provider onnxruntime **actually** uses - not the one it claims is available.

Why this tool is needed
-----------------------
When a CUDA/DirectML provider fails to load, onnxruntime **does not raise, it silently falls
back to CPU**: `get_available_providers()` still lists CUDA (it means "compiled in", not
"loadable"), and the only real fact lives in `InferenceSession.get_providers()`.

Measured on 2026-09-17: with onnxruntime-gpu installed but cublasLt64_12.dll missing,
get_available_providers() said CUDA was there, while the real session was
['CPUExecutionProvider']; inference still ran, just 5x slower - **you will never find out
unless you actively check**.

Usage:
    python tools/check_provider.py                 # use this machine's wd-swinv2-tagger-v3
    python tools/check_provider.py --model <id> --provider CUDAExecutionProvider

Exit codes: 0 = got the requested provider; 1 = fell back to another (prints the real value);
2 = environment unusable.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from local_paths import model_roots

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

#: This machine's model search roots first (local_paths.ini), then the conventional per-user cache.
DEFAULT_MODELS = [
    *model_roots(),
    Path.home() / ".cache" / "huggingface" / "hub",
]
GPU_PROVIDERS = ("CUDAExecutionProvider", "DmlExecutionProvider", "TensorrtExecutionProvider")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Report the provider onnxruntime actually uses")
    ap.add_argument("--model", default="wd-swinv2-tagger-v3")
    ap.add_argument("--provider", default=None, help="provider wanted; defaults to the first available GPU, or CPU if there is none")
    ap.add_argument("--roots", nargs="*", type=Path, default=None)
    args = ap.parse_args(argv)

    try:
        import onnxruntime as ort
    except Exception as exc:
        print("onnxruntime unavailable: %s" % exc, file=sys.stderr)
        return 2

    from kohya_dataset_tagger.autotag import registry

    roots = args.roots or DEFAULT_MODELS
    try:
        onnx_path, vocab_path = registry.resolve(args.model, roots)
    except Exception as exc:
        print("Model %s not found (%s) - cannot do a real session check." % (args.model, str(exc)[:80]))
        print("claimed available providers:", ort.get_available_providers())
        print("Note: **claimed available does not mean actually usable**; only building a real session with a real model counts.")
        return 2

    wanted = args.provider
    if wanted is None:
        wanted = next((p for p in ort.get_available_providers() if p in GPU_PROVIDERS), "CPUExecutionProvider")

    from kohya_dataset_tagger.autotag.wd14 import WD14Tagger

    tagger = WD14Tagger(onnx_path, vocab_path, providers=[wanted])
    actual = list(tagger.session.get_providers())

    print("model       :", args.model)
    print("onnxruntime :", ort.__version__)
    print("claimed     :", ort.get_available_providers())
    print("requested   :", wanted)
    print("actual      :", actual)

    if wanted in actual:
        print("verdict     : OK - the requested provider really took effect")
        return 0
    if any(p in actual for p in GPU_PROVIDERS):
        print("verdict     : fell back to another GPU provider (%s), which is still not the requested one" % actual[0])
        return 1
    print()
    print("verdict     : **silently fell back to CPU** - inference runs, but about 5x slower, with no error.")
    print("Where to look:")
    print("  1) The CUDA route needs the cuDNN 9.* and CUDA 12.* runtime DLLs (cublasLt64_12.dll etc.).")
    print("     If this machine has torch(cu12x) installed, just put its lib directory on PATH:")
    print("       <torch install>/Lib/site-packages/torch/lib")
    print("     Measured: onnxruntime-gpu 245 MB + torch/lib on PATH -> CUDAExecutionProvider takes effect.")
    print("  2) Or just use DirectML (~50 MB, no CUDA/cuDNN needed):")
    print("       scripts/setup_env.ps1 -Gpu directml")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
