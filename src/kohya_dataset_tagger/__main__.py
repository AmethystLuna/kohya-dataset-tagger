"""CLI entry point: `python -m kohya_dataset_tagger`.

Startup order (the assembly conventions of p0-spec §6)
------------------------------------------------------
1. `config.load()` - **must** be called: it publishes the roots to `core.paths` (the single source
   of truth for the allowlist). Without it `paths.get_roots()` is empty and every request is 403.
2. `create_app(cfg)` - dynamically discover the routers in `api/` and mount `web/` at `/`.
3. `uvicorn.run(...)`.

`--dump-config` only prints the configuration that would be published and exits (it does not start
the service), for checking the allowlist / cache directory / model search roots - the typical
symptom of a misconfigured allowlist is "every request is 403", and this shows it directly.

Before startup it does one more thing unrelated to configuration but likewise only possible at
startup: **decide whether logs should be colored**, and open the console's VT processing on Windows
(`console.py`, spec §7). uvicorn itself only asks `sys.stdout.isatty()`, and that answer is wrong
in cmd.exe.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from . import __version__, app as app_module, config, console
from .core import paths

__all__ = ["main", "build_parser"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kohya-dataset-tagger",
        description="A local dataset tagger for kohya-style training sets (LoRA / finetune / DreamBooth).",
    )
    parser.add_argument(
        "--roots",
        action="append",
        metavar="PATH",
        help="roots that may be browsed, repeatable; multiple can also be separated by %r. Defaults to KOHYA_TAGGER_ROOTS." % os.pathsep,
    )
    parser.add_argument(
        "--models",
        action="append",
        metavar="PATH",
        help="model/vocabulary search roots, repeatable; multiple can also be separated by %r. Defaults to KOHYA_TAGGER_MODELS." % os.pathsep,
    )
    parser.add_argument(
        "--extra-models",
        action="append",
        metavar="PATH",
        help=(
            "**extra** model scan paths to append, repeatable; multiple can also be separated by %r. "
            "Difference from --models: --models replaces the search list wholesale, while this "
            "appends after it. Defaults to KOHYA_TAGGER_EXTRA_MODELS." % os.pathsep
        ),
    )
    parser.add_argument(
        "--thumb-cache",
        metavar="PATH",
        help="thumbnail cache directory (must be outside the dataset directories). Defaults to KOHYA_TAGGER_THUMB_CACHE.",
    )
    parser.add_argument(
        "--roots-file",
        metavar="PATH",
        help=("the file that remembers the dataset roots (§4.11 rewrites it whole). "
              "Defaults to <repo>/roots.txt, overridable with KOHYA_TAGGER_ROOTS_FILE."),
    )
    parser.add_argument(
        "--model-paths-file",
        metavar="PATH",
        help=("the file for extra model scan paths (§4.11 only adds/removes path lines in it). "
              "Defaults to <repo>/model_paths.txt, overridable with KOHYA_TAGGER_MODEL_PATHS_FILE."),
    )
    parser.add_argument("--host", help="listen address, default %s." % config.DEFAULT_HOST)
    parser.add_argument("--port", type=int, help="listen port, default %d." % config.DEFAULT_PORT)
    parser.add_argument(
        "--log-level",
        default="info",
        choices=("critical", "error", "warning", "info", "debug", "trace"),
        help="uvicorn log level, default info.",
    )
    parser.add_argument(
        "--color",
        default="auto",
        choices=console.COLOR_MODES,
        help=(
            "log coloring: auto (default, color when VT can be enabled) / always / never. "
            "On Windows, auto opens the console's VT processing itself; when it cannot, it "
            "**actively turns color off**, otherwise conhost prints the escape sequences literally "
            "as ←[32m."
        ),
    )
    parser.add_argument(
        "--allow-remote-path-config",
        action="store_true",
        help=(
            "also open path configuration (allowlist edits) and the graphical directory picker on "
            "non-loopback addresses. Off by default: when --host points at an external address, "
            "these endpoints are always 403."
        ),
    )
    parser.add_argument(
        "--dump-config",
        action="store_true",
        help="only print the configuration that would be published (JSON) and exit; do not start the service.",
    )
    return parser


def _split_multi(values: list[str] | None) -> list[str] | None:
    """Flatten repeatable option values: `--roots "A;B" --roots C` -> `[A, B, C]`."""
    if not values:
        return None
    out: list[str] = []
    for value in values:
        text = value.replace("\r", os.pathsep).replace("\n", os.pathsep)
        for part in text.split(os.pathsep):
            cleaned = part.strip().strip('"').strip("'")
            if cleaned:
                out.append(cleaned)
    return out or None


def _print_banner(cfg: config.Config, use_colors: bool) -> None:
    """Print the "actually published allowlist" before startup - the first place to look when debugging a 403."""
    print("kohya-dataset-tagger %s" % __version__)
    published = paths.get_roots()
    if published:
        for root in published:
            print("  root        : %s" % root)
    else:
        print("  root        : (empty) every /api request will be 403; specify with --roots or KOHYA_TAGGER_ROOTS")
    print("  thumb cache : %s" % cfg.thumb_cache_dir)
    print("  roots.txt   : %s" % (cfg.roots_file or "(not set)"))
    print("  model_paths : %s" % (cfg.model_paths_file or "(not set)"))
    for index, (root, source) in enumerate(config.classify_model_roots(cfg)):
        mark = "" if root.is_dir() else "  (does not exist, ignored)"
        print("  models[%d]   : %-8s %s%s" % (index, source, root, mark))
    print("  color       : %s" % ("on" if use_colors else "off"))
    print("  listening   : http://%s:%d/" % (cfg.host, cfg.port))
    # A non-loopback address is the only genuinely dangerous state of this thing: path configuration = read/write access to any directory.
    if not config.is_loopback_host(cfg.host):
        if cfg.allow_remote_path_config:
            print("  path config : non-loopback address, and arbitrary directory browsing and allowlist writes "
                  "are enabled (--allow-remote-path-config)")
        else:
            print("  path config : non-loopback address, path configuration and the graphical picker are disabled "
                  "(add --allow-remote-path-config to enable)")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # This call is the only entry point where the allowlist takes effect (config.load -> paths.configure).
    cfg = config.load(
        roots=_split_multi(args.roots),
        thumb_cache_dir=args.thumb_cache,
        model_search_roots=_split_multi(args.models),
        extra_model_roots=_split_multi(args.extra_models),
        roots_file=args.roots_file,
        model_paths_file=args.model_paths_file,
        host=args.host,
        port=args.port,
        allow_remote_path_config=args.allow_remote_path_config,
    )

    if args.dump_config:
        print(json.dumps(
            {
                "version": __version__,
                "roots": [str(root) for root in paths.get_roots()],
                "configured_roots": [str(root) for root in cfg.roots],
                "thumb_cache_dir": str(cfg.thumb_cache_dir),
                "roots_file": str(cfg.roots_file) if cfg.roots_file else None,
                "model_paths_file": str(cfg.model_paths_file) if cfg.model_paths_file else None,
                "model_search_roots": [str(root) for root in cfg.model_search_roots],
                "model_search_sources": [source for _root, source in config.classify_model_roots(cfg)],
                "host": cfg.host,
                "port": cfg.port,
                "allow_remote_path_config": cfg.allow_remote_path_config,
                "path_config_allowed": config.path_config_allowed(),
                "default_caption_ext": cfg.default_caption_ext,
            },
            indent=2,
            ensure_ascii=False,
        ))
        return 0

    # The color decision must be made **before** the service starts: it has to open the console's VT
    # first (§7 "console and ANSI"). The result is handed to uvicorn explicitly, not left as
    # use_colors=None - that would let it guess by isatty(), and in cmd.exe isatty() is true while
    # VT is off, so the guessed answer is that pile of ←[32m on screen.
    use_colors = console.resolve_use_colors(args.color)
    _print_banner(cfg, use_colors)
    import uvicorn  # lazy import: --dump-config does not need it
    from uvicorn.config import LOGGING_CONFIG

    uvicorn.run(
        app_module.create_app(cfg),
        host=cfg.host,
        port=cfg.port,
        log_level=args.log_level,
        log_config=console.colored_log_config(LOGGING_CONFIG, use_colors),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
