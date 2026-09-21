"""The machine-local paths the criteria and the development tools need - kept out of the repository.

Why this exists
---------------
Hardcoding `F:\\...` paths in tests ties them to one machine, and publishing those paths ties the
project to one person. Everything that needs a path outside this repository reads it from
`local_paths.ini`, which is **gitignored**:

    [paths]
    dataset = D:\\datasets\\my-lora     # a kohya-style dataset directory (read-only for the criteria)
    models  = D:\\taggers;E:\\more      # model search roots, separated by os.pathsep
    hf_hub  =                          # a HuggingFace cache (optional)
    trainer =                          # a checkout of the trainer (optional; acceptance A23 scores against it)
    scratch =                          # a scratch directory for the destructive benches (optional)

`local_paths.ini.example` is the committed template, and its defaults point **inside this
repository**: the sample dataset under `test/fixtures/sample_dataset/` and the `models/` directory.
A fresh clone therefore needs no setup - and the criteria whose subject cannot exist without a
downloaded tagger model or a real vocabulary skip themselves and say so.

Relative paths resolve against the repository root. Print what is in effect with:

    python local_paths.py
"""
from __future__ import annotations

import configparser
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent

#: The private file wins; the committed template is the fallback, so a fresh clone always resolves.
INI_NAME = "local_paths.ini"
EXAMPLE_NAME = "local_paths.ini.example"

#: What the template points at, repeated here so a missing/edited template cannot leave a path unset.
DEFAULT_DATASET = REPO_ROOT / "test" / "fixtures" / "sample_dataset"
DEFAULT_MODELS = REPO_ROOT / "models"

SECTION = "paths"
KEYS = ("dataset", "models", "hf_hub", "trainer", "scratch")


def _config() -> configparser.ConfigParser:
    """The effective configuration. Interpolation is off: a Windows path may contain `%`."""
    parser = configparser.ConfigParser(interpolation=None)
    for name in (INI_NAME, EXAMPLE_NAME):
        path = REPO_ROOT / name
        if path.is_file():
            parser.read(path, encoding="utf-8")
            break
    return parser


def raw(key: str) -> str:
    """One configured value as written (empty string when unset)."""
    parser = _config()
    if not parser.has_section(SECTION):
        return ""
    return (parser.get(SECTION, key, fallback="") or "").strip()


def has_private_file() -> bool:
    """Whether `local_paths.ini` exists - i.e. whether this checkout is configured for a real machine."""
    return (REPO_ROOT / INI_NAME).is_file()


def _resolve(text: str) -> Path:
    path = Path(os.path.expandvars(text)).expanduser()
    return path if path.is_absolute() else (REPO_ROOT / path)


def path(key: str) -> Path | None:
    """One path, or None when the key is unset. Relative values resolve against the repository root."""
    text = raw(key)
    return _resolve(text) if text else None


def paths(key: str) -> tuple[Path, ...]:
    """An `os.pathsep`-separated list of paths (empty entries ignored)."""
    return tuple(_resolve(part) for part in raw(key).split(os.pathsep) if part.strip())


def dataset_root() -> Path:
    """The kohya-style dataset the criteria read. Never None: it falls back to the sample dataset."""
    return path("dataset") or DEFAULT_DATASET


def model_roots() -> tuple[Path, ...]:
    """Where the tagger models are looked for. Never empty: it falls back to the repository's `models/`."""
    return paths("models") or (DEFAULT_MODELS,)


def hf_hub() -> Path | None:
    """A HuggingFace cache holding the real vocabulary, when one is configured."""
    return path("hf_hub")


def trainer_root() -> Path | None:
    """A checkout of the trainer, for the acceptance criteria that score against its own `config_util`."""
    return path("trainer")


def scratch_root() -> Path | None:
    """A directory the destructive benches may write below. They refuse to run without one."""
    return path("scratch")


def main() -> int:
    source = INI_NAME if has_private_file() else EXAMPLE_NAME
    print("local_paths.py - reading %s" % source)
    for key in KEYS:
        value = raw(key)
        resolved = paths(key) if key == "models" else (path(key),)
        shown = ", ".join(str(item) for item in resolved if item) or "(unset)"
        print("  %-8s %-42s -> %s" % (key, value or "(unset)", shown))
    print()
    print("  dataset_root() -> %s  (exists: %s)" % (dataset_root(), dataset_root().is_dir()))
    print("  model_roots()  -> %s" % (", ".join(str(item) for item in model_roots()),))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
