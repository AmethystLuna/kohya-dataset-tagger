"""Runtime configuration: allowlist roots, thumbnail cache directory, model search directories.

Environment variables (spec §3.7)
---------------------
`KOHYA_TAGGER_ROOTS`        roots that may be browsed, multiple separated by `os.pathsep` (`;` on Windows)
`KOHYA_TAGGER_THUMB_CACHE`  thumbnail cache directory
`KOHYA_TAGGER_MODELS`       model/vocabulary search directories, multiple separated by `os.pathsep`
`KOHYA_TAGGER_EXTRA_MODELS` model search directories appended **extra** (appended, not replaced)
`KOHYA_TAGGER_ROOTS_FILE`        location of `roots.txt` (default: repository root)
`KOHYA_TAGGER_MODEL_PATHS_FILE`  location of `model_paths.txt` (default: repository root)
`KOHYA_TAGGER_ALLOW_REMOTE_PATH_CONFIG`  also open path configuration and the graphical picker on non-loopback addresses (off by default)

On a non-loopback address (`--host` is not 127.0.0.1 / ::1 / localhost), allowlist edits and the
graphical directory picker are **403 by default**: both can bypass the allowlist to enumerate/read
arbitrary directories, and keeping them on loopback is this tool's trust boundary.

Windows paths contain `:` (drive letters), so the separator can only be `;`, not `:`. A whole
value with spaces or quotes is accepted (strip the quotes, then split on `;`).

`load()` **publishes** the roots to `core.paths` (the single source of truth for the allowlist).
Otherwise "roots in the config" and "roots in paths" would disagree, and every endpoint only trusts the latter.
"""
from __future__ import annotations

import ipaddress
import logging
import os
import threading
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping, Sequence

from .core import paths

logger = logging.getLogger("kohya_dataset_tagger.config")

__all__ = [
    "Config",
    "load",
    "current",
    "default_thumb_cache_dir",
    "default_model_search_roots",
    "ENV_ROOTS",
    "ENV_THUMB_CACHE",
    "ENV_MODELS",
    "ENV_EXTRA_MODELS",
    "ENV_ROOTS_FILE",
    "ENV_MODEL_PATHS_FILE",
    "ENV_ALLOW_REMOTE_PATH_CONFIG",
    "is_loopback_host",
    "path_config_allowed",
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "THUMB_CACHE_DIRNAME",
    "REPO_ROOT",
    "PathConfigError",
    "NotADirectory",
    "LastRootError",
    "UnknownRoot",
    "norm_key",
    "read_roots_file",
    "write_roots_file",
    "read_model_paths_file",
    "append_model_path_line",
    "remove_model_path_line",
    "persisted_roots",
    "rebuild_model_roots",
    "classify_model_roots",
    "add_root",
    "remove_root",
    "add_model_root",
    "remove_model_root",
]

ENV_ROOTS = "KOHYA_TAGGER_ROOTS"
ENV_THUMB_CACHE = "KOHYA_TAGGER_THUMB_CACHE"
ENV_MODELS = "KOHYA_TAGGER_MODELS"
#: **Extra** model scan paths (semicolon/comma separated). Difference from ENV_MODELS:
#: ENV_MODELS **replaces** the default list wholesale; this **inserts** after the default list, and both can be used together.
ENV_EXTRA_MODELS = "KOHYA_TAGGER_EXTRA_MODELS"
#: Location of `roots.txt` / `model_paths.txt`. **For tests**: if unset it falls back to the repository root.
ENV_ROOTS_FILE = "KOHYA_TAGGER_ROOTS_FILE"
ENV_MODEL_PATHS_FILE = "KOHYA_TAGGER_MODEL_PATHS_FILE"
#: Whether to also open path configuration (§4.11 allowlist edits) and the graphical directory picker on non-loopback addresses.
ENV_ALLOW_REMOTE_PATH_CONFIG = "KOHYA_TAGGER_ALLOW_REMOTE_PATH_CONFIG"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 3001

#: Environment variable values treated as "true" (case-insensitive).
_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in _TRUTHY


def is_loopback_host(host: str | None) -> bool:
    """Whether `--host` listens only on this machine (loopback).

    Recognizes `localhost`, IPv4/IPv6 loopback literals, and IPv4-mapped forms such as
    `::ffff:127.0.0.1`. An empty or unrecognized value => **False** (fail closed: if it is not
    recognized, it is not treated as local).
    """
    text = (host or "").strip().lower()
    if not text:
        return False
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    if text == "localhost":
        return True
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return False
    if address.is_loopback:
        return True
    mapped = getattr(address, "ipv4_mapped", None)
    return bool(mapped is not None and mapped.is_loopback)

#: Repository root (the file `<repo>/src/kohya_dataset_tagger/config.py` is three levels up).
#: The default locations of `models/`, `roots.txt`, and `model_paths.txt` are all derived from it.
REPO_ROOT = Path(__file__).resolve().parents[2]

#: The persistent file for allowlist roots. **Rewritten whole** - it is managed by the launcher and
#: the API, and comments are not recognized (§4.11).
DEFAULT_ROOTS_FILENAME = "roots.txt"
#: The persistent file for **extra** model scan directories. **Only path lines are added/removed** -
#: the explanatory text at the top is a user asset (§4.11).
DEFAULT_MODEL_PATHS_FILENAME = "model_paths.txt"

#: Lock for changing runtime configuration. Endpoints run in a thread pool, so two concurrent POSTs must not each read a half-updated state.
_LOCK = threading.RLock()

#: Thumbnail directory name inside the user cache (spec §3.7: `<user cache>/kohya-dataset-tagger-thumbs`).
THUMB_CACHE_DIRNAME = "kohya-dataset-tagger-thumbs"

# 2026-09-17: there used to be a `_MACHINE_MODEL_HINTS` here (two local F: paths, Windows only).
# **Removed** - product code must not be tied to one machine. Machine-specific locations now come
# from the repository-root `model_paths.txt` (a gitignored local file, editable in the UI too; see §4.11).
# So `default_model_search_roots()` has only four sources left, valid on any machine.

#: The current config published by `load()`; `current()` uses it to avoid rebuilding on every request.
_CONFIG: "Config | None" = None


@dataclass
class Config:
    """Runtime configuration. Field order matches spec §3.7 and it is positionally constructible."""

    roots: tuple[Path, ...]
    thumb_cache_dir: Path
    model_search_roots: tuple[Path, ...]
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    default_caption_ext: str = paths.CAPTION_EXT_DEFAULT
    # --- Added 2026-09-17: runtime-editable path configuration (spec §3.7.1 / §4.11) ---
    # **Always append at the end**: this class is constructed positionally, and inserting in the middle silently changes the meaning of existing calls.
    roots_file: Path | None = None
    model_paths_file: Path | None = None
    #: `--models` / `KOHYA_TAGGER_MODELS`: **replaces** the list wholesale. None = not given.
    explicit_model_roots: tuple[Path, ...] | None = None
    #: `--extra-models` / `KOHYA_TAGGER_EXTRA_MODELS`: appended, and **cannot** be persistently removed from the UI.
    pinned_model_roots: tuple[Path, ...] = ()
    #: Read from `model_paths.txt`: appended, and can be persistently removed from the UI.
    file_model_roots: tuple[Path, ...] = ()
    #: Roots removed during this run (normalized paths). Valid only in this process - no exclusion table.
    excluded_model_roots: frozenset[str] = frozenset()
    #: Environment variables used when rebuilding the search list. Not part of equality comparison and not in repr (it is the whole os.environ).
    environ: Mapping[str, str] | None = field(default=None, repr=False, compare=False)
    #: Whether to also open path configuration and the graphical directory picker on non-loopback
    #: addresses (`--allow-remote-path-config`).
    #: Default False: when bound to a non-loopback address, allowlist edits and the picker are 403.
    allow_remote_path_config: bool = False


def _dedupe_paths(candidates: Sequence[Path]) -> tuple[Path, ...]:
    """Deduplicate by normalized path, keeping first-seen order. Order is priority, so it must not be sorted."""
    seen: set[str] = set()
    unique: list[Path] = []
    for path in candidates:
        key = os.path.normcase(str(path))
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return tuple(unique)


def _split_paths(value: str) -> tuple[Path, ...]:
    """Split a path list on `os.pathsep` (also tolerating newlines), stripping whitespace and surrounding quotes."""
    text = value.replace("\r", os.pathsep).replace("\n", os.pathsep)
    parts = [item.strip().strip('"').strip("'") for item in text.split(os.pathsep)]
    return tuple(Path(item) for item in parts if item)


def default_thumb_cache_dir(environ: Mapping[str, str] | None = None) -> Path:
    """`<user cache>/kohya-dataset-tagger-thumbs`. Windows uses `%LOCALAPPDATA%`, other platforms use XDG.

    `environ` is injectable so tests do not have to touch the real environment variables.
    """
    env: Mapping[str, str] = os.environ if environ is None else environ
    base = env.get("LOCALAPPDATA") or env.get("APPDATA")
    if base:
        return Path(base) / THUMB_CACHE_DIRNAME
    xdg = env.get("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg) / THUMB_CACHE_DIRNAME
    return Path.home() / ".cache" / THUMB_CACHE_DIRNAME


def default_model_search_roots(
    environ: Mapping[str, str] | None = None,
    extra: Sequence[str | Path] = (),
) -> tuple[Path, ...]:
    """Default model search directories. **Order is priority** (the first hit wins for a duplicate model name).

    1. `models/` inside the repository - the directory shown to users (a placeholder file in it says "put models here")
    2. `extra` - paths the user **additionally** specified in the configuration (`model_paths.txt` / `--extra-models`)
    3. `%LOCALAPPDATA%/kohya-dataset-tagger/models` - our own download directory
    4. **the HF cache last**

    Item 4 was adjusted on 2026-09-17: the HF cache used to be first, so "a model the user
    deliberately placed" would be shadowed by a same-named copy in the cache. **Explicit >
    auto-discovered** - the cache is only something that happens to be there.

    **This default list must not contain paths that only exist on one machine.** The previous
    hard-coded local hints were removed on 2026-09-17; the criterion is
    `test/test_extra_models.py`: default roots may only fall inside the repository or the current
    user's cache area, and third-party directories are always provided by `model_paths.txt` (so
    switching machines needs neither a code change nor an environment variable).

    Nonexistent directories are left for `autotag.registry.discover()` to ignore - no existence
    filtering here, because "the directory does not exist yet" and "the directory has no models"
    are the same thing to the caller.
    """
    env: Mapping[str, str] = os.environ if environ is None else environ
    candidates: list[Path] = [Path(__file__).resolve().parents[2] / "models"]
    candidates.extend(Path(p) for p in extra)
    local = env.get("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / "kohya-dataset-tagger" / "models")
    hub = env.get("HF_HUB_CACHE") or env.get("HUGGINGFACE_HUB_CACHE")
    if hub:
        candidates.append(Path(hub))
    else:
        hf_home = env.get("HF_HOME")
        candidates.append(Path(hf_home) / "hub" if hf_home else Path.home() / ".cache" / "huggingface" / "hub")
    return _dedupe_paths(candidates)


def _build(
    env: Mapping[str, str] | None,
    roots: Sequence[str | Path] | None,
    thumb_cache_dir: str | Path | None,
    model_search_roots: Sequence[str | Path] | None,
    host: str | None,
    port: int | None,
    default_caption_ext: str | None,
    extra_model_roots: Sequence[str | Path] | None = None,
    roots_file: str | Path | None = None,
    model_paths_file: str | Path | None = None,
    allow_remote_path_config: bool | None = None,
) -> Config:
    environ: Mapping[str, str] = os.environ if env is None else env

    # Whether to also open path configuration / the graphical picker on non-loopback. Keyword takes precedence over the environment variable (same rule as the other fields).
    if allow_remote_path_config is None:
        allow_remote_path_config = _truthy(environ.get(ENV_ALLOW_REMOTE_PATH_CONFIG))

    if roots is None:
        raw_roots = environ.get(ENV_ROOTS, "")
        roots = _split_paths(raw_roots) if raw_roots else ()
    if thumb_cache_dir is None:
        raw_cache = environ.get(ENV_THUMB_CACHE, "")
        thumb_cache_dir = Path(raw_cache.strip().strip('"')) if raw_cache.strip() else default_thumb_cache_dir(environ)

    # Locations of the two persistent files (§4.11). Acceptance and unit tests point them at
    # tmp_path, otherwise one test run would rewrite the repository-root roots.txt.
    resolved_roots_file = _resolve_file(roots_file, environ.get(ENV_ROOTS_FILE), DEFAULT_ROOTS_FILENAME)
    resolved_model_paths_file = _resolve_file(
        model_paths_file, environ.get(ENV_MODEL_PATHS_FILE), DEFAULT_MODEL_PATHS_FILENAME
    )

    # **Extra** scan paths come in two kinds (§3.7.1):
    #   file   - the ones in model_paths.txt, which the UI can persistently add/remove;
    #   pinned - --extra-models / KOHYA_TAGGER_EXTRA_MODELS, valid only for this run.
    # When both give the same path, **the file wins**: otherwise "removed a line in the UI" would be
    # pushed back by the command-line argument on the next start, while the user saw the UI say it was removed.
    if extra_model_roots is not None:
        cli_extras = tuple(Path(p) for p in extra_model_roots)
    else:
        raw_extra = environ.get(ENV_EXTRA_MODELS, "")
        cli_extras = _split_paths(raw_extra) if raw_extra else ()
    file_extras = read_model_paths_file(resolved_model_paths_file)
    file_keys = {norm_key(p) for p in file_extras}
    pinned = _dedupe_paths([Path(p) for p in cli_extras if norm_key(p) not in file_keys])

    explicit: tuple[Path, ...] | None = None
    if model_search_roots is None:
        raw_models = environ.get(ENV_MODELS, "")
        # The user explicitly listed the full list - that is their say; extras are still appended after it
        explicit = tuple(_split_paths(raw_models)) if raw_models else None
    else:
        explicit = tuple(Path(p) for p in model_search_roots)

    config = Config(
        roots=tuple(Path(p) for p in roots),
        thumb_cache_dir=Path(thumb_cache_dir),
        model_search_roots=(),
        host=host or DEFAULT_HOST,
        port=int(port) if port is not None else DEFAULT_PORT,
        default_caption_ext=default_caption_ext or paths.CAPTION_EXT_DEFAULT,
        roots_file=resolved_roots_file,
        model_paths_file=resolved_model_paths_file,
        explicit_model_roots=explicit,
        pinned_model_roots=tuple(pinned),
        file_model_roots=tuple(file_extras),
        excluded_model_roots=frozenset(),
        environ=environ,
        allow_remote_path_config=bool(allow_remote_path_config),
    )
    # The search list is **computed by rebuild**: the startup path and runtime edits share the same
    # code; computing it in two places is a recipe for certain drift.
    return replace(config, model_search_roots=rebuild_model_roots(config))


def load(
    env: Mapping[str, str] | None = None,
    *,
    roots: Sequence[str | Path] | None = None,
    thumb_cache_dir: str | Path | None = None,
    model_search_roots: Sequence[str | Path] | None = None,
    host: str | None = None,
    port: int | None = None,
    default_caption_ext: str | None = None,
    extra_model_roots: Sequence[str | Path] | None = None,
    roots_file: str | Path | None = None,
    model_paths_file: str | Path | None = None,
    allow_remote_path_config: bool | None = None,
) -> Config:
    """Build the config and publish it as the current config (also writing `roots` into the `core.paths` allowlist).

    Keyword arguments take precedence over environment variables; `env=None` reads `os.environ`
    (pass a dict to inject in tests).

    Difference between `model_search_roots` and `extra_model_roots`:
    the former is a **complete list** (if given, it is used as-is), the latter is a few paths
    **appended** to the list.

    `roots_file` / `model_paths_file` are the locations of the two persistent files from §4.11;
    if not passed they fall at the repository root (tests **must** pass tmp_path, otherwise they
    would rewrite the user's roots.txt).
    """
    config = _build(env, roots, thumb_cache_dir, model_search_roots, host, port,
                    default_caption_ext, extra_model_roots, roots_file, model_paths_file,
                    allow_remote_path_config)
    with _LOCK:
        _publish(config)
    return config


def _publish(config: Config) -> None:
    """Publish the config as the current config and push `roots` to `core.paths` (the single source of truth for the allowlist)."""
    global _CONFIG
    _CONFIG = config
    paths.configure(config.roots)


def current() -> Config:
    """Return the current config; if `load()` was never called, build one from the environment variables.

    This function does **not** change the `core.paths` allowlist - the allowlist is published only
    by `load()`, so a read on a request thread cannot overwrite roots someone configured by hand.
    """
    global _CONFIG
    if _CONFIG is None:
        _CONFIG = _build(None, None, None, None, None, None, None)
    return _CONFIG


def path_config_allowed() -> bool:
    """Whether path configuration (§4.11 allowlist edits) and the graphical directory picker are available right now.

    Loopback binding => available; non-loopback => available only when `--allow-remote-path-config` is explicitly on.
    """
    config = current()
    return bool(config.allow_remote_path_config or is_loopback_host(config.host))


# ---------------------------------------------------------------------------
# §4.11 runtime-editable path configuration
# ---------------------------------------------------------------------------
# This whole section exists for one thing: switching datasets or adding a model directory
# **without restarting the service**. Three hard rules:
#
# 1. Changing in-memory state and writing the persistent file are **two steps**, and a failure in
#    the second is only reported, never rolled back. Reason: the feature already took effect, and
#    rolling back would turn "the UI clearly added it" into "it is gone after a refresh"; reporting
#    persisted=false + warning honestly at least tells the user what state they are in.
# 2. Every path comparison goes through `norm_key()`. Comparing strings directly would yield two
#    different roots for `F:/x` and `F:\x`.
# 3. The allowlist is **published before the file is written**: if the file write fails, the feature
#    still works.


class PathConfigError(Exception):
    """Base failure class for runtime-editable path configuration. `code` / `status` / `params` map
    directly onto the §4 error shape.

    `params` is the structured variable data (p0-spec §4.9 supplement 4): the two fixed template
    messages of `_target_path` (`path must not be empty`, `cannot resolve path %r: %s`) are
    filled in here and passed through verbatim by the §4.11 `/api/roots` endpoint. The code of
    these two messages is still `bad_request` (frozen contract, do not rename), so they currently
    still show the backend's own wording - `params` is the structured half, and the fallback string
    stays as is.
    """

    code = "bad_request"
    status = 400

    def __init__(self, message: str, params: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.params: dict[str, Any] = dict(params or {})


class NotADirectory(PathConfigError):
    """The path does not exist, or exists but is not a directory.

    **An allowlist root may not be a directory that does not exist yet** - configured that way,
    every request is 403/404 while the user thinks they set it up. This rule is far more useful
    than "accept it leniently".
    """

    code = "not_a_directory"


class LastRootError(PathConfigError):
    """The allowlist must not be emptied.

    Emptied, every `/api/fs/*` is 403 - exactly the hardest kind of "why won't anything open"
    symptom to diagnose. To switch datasets, **add first, then remove**.
    """

    code = "last_root"


class UnknownRoot(PathConfigError):
    """The path to delete is not in the current list."""

    code = "not_found"
    status = 404


def norm_key(path: str | Path) -> str:
    """A path's identity key: expand `~`, make absolute, normalize per **platform** case rules.

    `os.path.normcase` lowercases on Windows and is **identity** on POSIX - which is correct,
    because on POSIX `/data/A` and `/data/a` are two genuinely different directories, and merging
    them would be the mistake (the difference measured on real Ubuntu on 2026-09-17; see
    `test/test_roots_api.py::test_norm_key_follows_the_platform_case_rules`).

    This deliberately does **not** use `Path.resolve()`: that touches the disk (resolving symlinks),
    while this function only answers "are these two spellings the same place". What actually decides
    the allowlist is `paths.configure()`, which has already resolved.
    """
    return os.path.normcase(os.path.abspath(os.path.expanduser(str(path))))


def _resolve_file(explicit: str | Path | None, env_value: str | None, default_name: str) -> Path:
    """Location of a persistent file: `--x-file` > environment variable > the default name under the repository root."""
    raw: str | Path | None = explicit if explicit is not None else env_value
    if raw is None or not str(raw).strip():
        return REPO_ROOT / default_name
    return Path(str(raw).strip().strip('"').strip("'")).expanduser()


def _read_lines(path: str | Path) -> list[str]:
    """Read a text file line by line; if it does not exist, return an empty list (the caller only
    cares about "what is in the file").

    Read with `utf-8-sig`: PowerShell 5.1's `Set-Content -Encoding UTF8` writes a BOM, and with a
    BOM the first line becomes `\\ufeff# comment`, so the comment gets treated as a path.
    """
    try:
        return Path(path).read_text(encoding="utf-8-sig").splitlines()
    except FileNotFoundError:
        return []
    except OSError as exc:
        logger.warning("cannot read %s: %s", path, exc)
        return []


def _newline_of(path: str | Path) -> str:
    """The original file's newline style. `model_paths.txt` only has lines added/removed, so CRLF
    must not be casually changed to LF.

    **Must read bytes**: `read_text()` performs newline translation in text mode (`\\r\\n` -> `\\n`),
    so the detection would always return LF - which is exactly how CRLF was silently changed to LF
    on this machine.
    """
    try:
        return "\r\n" if b"\r\n" in Path(path).read_bytes() else "\n"
    except OSError:
        return os.linesep


def _write_atomic(path: str | Path, text: str) -> None:
    """Atomic write: a temp file in the same directory + `os.replace`.

    A single bad write would leave the **launcher** unable to read the roots, so it must not
    truncate and rewrite in place.
    """
    target = Path(path)
    tmp = target.with_name(target.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)
    os.replace(tmp, target)


def read_roots_file(path: str | Path) -> tuple[Path, ...]:
    """Read `roots.txt`: one root per line, **comments not recognized** (that is how the launcher reads it).

    Recognizing one more form here would make the two sides disagree about "which are the roots" -
    exactly the hardest kind of inconsistency to track down.
    """
    found: list[Path] = []
    for line in _read_lines(path):
        item = line.strip().strip('"').strip("'")
        if item:
            found.append(Path(item))
    return tuple(found)


def write_roots_file(path: str | Path, roots: Sequence[str | Path]) -> None:
    """Rewrite `roots.txt` **whole**.

    It is the server's authoritative state file (the launcher reads it line by line and does not
    recognize comments), so a full rewrite is its semantics; the one whose comments must be
    preserved is `model_paths.txt`, which is hand-written by the user (see `remove_model_path_line`).
    """
    _write_atomic(path, "".join("%s\n" % root for root in roots))


def read_model_paths_file(path: str | Path) -> tuple[Path, ...]:
    """Read `model_paths.txt`: ignore blank lines and `#` comments; semicolons inside a line are also recognized (same rule as `start.ps1`)."""
    roots: list[Path] = []
    for line in _read_lines(path):
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        roots.extend(Path(part.strip()) for part in text.split(";") if part.strip())
    return tuple(roots)


def append_model_path_line(path: str | Path, root: str | Path) -> None:
    """Append one line to `model_paths.txt`. **Only this line is added**; the explanatory text at the top is left untouched."""
    target = Path(path)
    newline = _newline_of(target)
    lines = _read_lines(target)
    _write_atomic(target, newline.join(lines + [str(root)]) + newline)


def remove_model_path_line(path: str | Path, root: str | Path) -> bool:
    """Remove the segment pointing at `root` from `model_paths.txt`, returning whether the file was actually changed.

    Only the matching segment is touched: when a line reads `A;B`, A is kept. Comment lines are
    always preserved verbatim - a full rewrite would eat the explanatory text at the top of the
    file, which is a user asset (this is the A29 criterion).
    """
    target = Path(path)
    key = norm_key(root)
    newline = _newline_of(target)
    kept: list[str] = []
    changed = False
    for line in _read_lines(target):
        text = line.strip()
        if not text or text.startswith("#"):
            kept.append(line)
            continue
        parts = [part.strip() for part in line.split(";") if part.strip()]
        remaining = [part for part in parts if norm_key(part) != key]
        if len(remaining) == len(parts):
            kept.append(line)
            continue
        changed = True
        if remaining:
            kept.append(";".join(remaining))
    if not changed:
        return False
    _write_atomic(target, newline.join(kept) + newline)
    return True


def _replace_current(config: Config) -> None:
    """Swap only the current config, **leaving the allowlist alone** - model search roots and the browse allowlist are two different things."""
    global _CONFIG
    _CONFIG = config


def persisted_roots(config: Config) -> set[str]:
    """The entries recorded in `roots.txt` (normalized keys). If the file cannot be read, the set is empty."""
    path = config.roots_file or REPO_ROOT / DEFAULT_ROOTS_FILENAME
    return {norm_key(root) for root in read_roots_file(path)}


def rebuild_model_roots(config: Config) -> tuple[Path, ...]:
    """Recompute the model search list in the §3.7 order.

    The startup path and runtime edits **share this code** - computing it in two places is a recipe
    for certain drift.
    Order: `explicit` or [repo models/ + (file + pinned) + auto-discovery], then drop excluded, then dedupe.
    """
    extras = tuple(config.file_model_roots) + tuple(config.pinned_model_roots)
    if config.explicit_model_roots is None:
        base = default_model_search_roots(config.environ, extras)
    else:
        base = _dedupe_paths(tuple(config.explicit_model_roots) + extras)
    if config.excluded_model_roots:
        base = tuple(path for path in base if norm_key(path) not in config.excluded_model_roots)
    return base


def classify_model_roots(config: Config) -> list[tuple[Path, str]]:
    """`[(root, source)]`, source ∈ `repo` / `explicit` / `file` / `cli` / `auto` (§4.11).

    When one path belongs to two classes at once, the earlier one in that order wins: **a more
    specific source beats auto-discovery**. This is not academic - `file_backed` directly decides
    "will it come back after a restart once deleted".
    """
    repo_key = norm_key(REPO_ROOT / "models")
    explicit = {norm_key(path) for path in (config.explicit_model_roots or ())}
    file_roots = {norm_key(path) for path in config.file_model_roots}
    pinned = {norm_key(path) for path in config.pinned_model_roots}
    out: list[tuple[Path, str]] = []
    for root in config.model_search_roots:
        key = norm_key(root)
        if key == repo_key:
            source = "repo"
        elif key in explicit:
            source = "explicit"
        elif key in file_roots:
            source = "file"
        elif key in pinned:
            source = "cli"
        else:
            source = "auto"
        out.append((root, source))
    return out


def _target_path(raw: str | Path) -> Path:
    """A user-supplied path -> absolute path. **Not required to exist** (removing a root that is no longer on disk is legitimate)."""
    text = str(raw or "").strip().strip('"').strip("'")
    if not text:
        raise PathConfigError("path must not be empty", params={})
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    try:
        return candidate.resolve()
    except OSError as exc:
        raise PathConfigError(
            "cannot resolve path %r: %s" % (text, exc),
            params={"path": text, "detail": str(exc)},
        ) from exc


def _directory_or_raise(raw: str | Path) -> Path:
    candidate = _target_path(raw)
    if not candidate.is_dir():
        raise NotADirectory("not a directory (or does not exist): %s" % candidate)
    return candidate


def _persist_roots(config: Config, roots: Sequence[Path]) -> tuple[bool, str | None]:
    """Write `roots.txt`. Failure does **not** raise: the allowlist already took effect, and an honest report is more useful than a rollback."""
    path = config.roots_file or REPO_ROOT / DEFAULT_ROOTS_FILENAME
    try:
        write_roots_file(path, roots)
    except OSError as exc:
        logger.warning("failed to write %s: %s", path, exc)
        return False, "allowlist is already in effect, but it could not be written to %s (%s); this entry will be lost after a restart" % (path, exc)
    return True, None


def _persist_model_line(config: Config, root: Path, *, add: bool) -> tuple[bool, str | None]:
    """Add/remove one line in `model_paths.txt`. A failure is likewise only reported."""
    path = config.model_paths_file or REPO_ROOT / DEFAULT_MODEL_PATHS_FILENAME
    try:
        if add:
            append_model_path_line(path, root)
        else:
            remove_model_path_line(path, root)
    except OSError as exc:
        logger.warning("failed to write %s: %s", path, exc)
        return False, "it took effect for this run, but it could not be written to %s (%s); this change will be lost after a restart" % (path, exc)
    return True, None


def add_root(raw: str | Path) -> dict[str, Any]:
    """Add an allowlist root, effective immediately and written back to `roots.txt` (§4.11's POST /api/roots)."""
    target = _directory_or_raise(raw)
    with _LOCK:
        config = current()
        published = paths.get_roots() or config.roots
        key = norm_key(target)
        if any(norm_key(root) == key for root in published):
            # Idempotent: already present is not a change, and the file is **not written** (to avoid things like squeezing out hand-written comments)
            return {"changed": False, "persisted": key in persisted_roots(config), "warning": None}
        _publish(replace(config, roots=tuple(published) + (target,)))
        ok, warning = _persist_roots(config, paths.get_roots())
        return {"changed": True, "persisted": ok, "warning": warning}


def remove_root(raw: str | Path) -> dict[str, Any]:
    """Remove a root from the allowlist and rewrite `roots.txt` (§4.11's DELETE /api/roots)."""
    target = _target_path(raw)
    with _LOCK:
        config = current()
        published = paths.get_roots() or config.roots
        key = norm_key(target)
        hit = next((root for root in published if norm_key(root) == key), None)
        if hit is None:
            raise UnknownRoot("this path is not in the allowlist: %s" % target)
        if len(published) <= 1:
            raise LastRootError("this is the last root; deleting it would make everything unopenable; add a new one before deleting it")
        _publish(replace(config, roots=tuple(root for root in published if norm_key(root) != key)))
        ok, warning = _persist_roots(config, paths.get_roots())
        return {"removed": str(hit), "persisted": ok, "warning": warning}


def add_model_root(raw: str | Path) -> dict[str, Any]:
    """Add an **extra** model scan directory, effective immediately and appended to `model_paths.txt` (§4.11).

    Appended after existing `file` entries - that is, still ranked **before** auto-discovery
    (explicit > auto-discovered).
    """
    target = _directory_or_raise(raw)
    with _LOCK:
        config = current()
        key = norm_key(target)
        if any(norm_key(root) == key for root in config.model_search_roots):
            return {
                "changed": False,
                "persisted": any(norm_key(root) == key for root in config.file_model_roots),
                "warning": None,
            }
        updated = replace(
            config,
            file_model_roots=tuple(config.file_model_roots) + (target,),
            # If it was deleted before, clear the "deleted" mark, otherwise adding it back would have no effect
            excluded_model_roots=config.excluded_model_roots - {key},
        )
        _replace_current(replace(updated, model_search_roots=rebuild_model_roots(updated)))
        ok, warning = _persist_model_line(updated, target, add=True)
        return {"changed": True, "persisted": ok, "warning": warning}


def remove_model_root(raw: str | Path) -> dict[str, Any]:
    """Remove a model search root (§4.11's DELETE /api/autotag/model-roots).

    `file` source -> remove the line in the file too, so it will not come back after a restart;
    the rest (`cli` / `auto`) -> effective only for this run, and **honestly state** that it will
    come back after a restart. No exclusion table: one more persistent file is one more place that
    can be inconsistent.
    """
    target = _target_path(raw)
    with _LOCK:
        config = current()
        key = norm_key(target)
        hit = next((root for root in config.model_search_roots if norm_key(root) == key), None)
        if hit is None:
            raise UnknownRoot("this path is not in the model search list: %s" % target)
        if any(norm_key(root) == key for root in config.file_model_roots):
            scope = "file"
            file_roots = tuple(root for root in config.file_model_roots if norm_key(root) != key)
        else:
            scope = "session"
            file_roots = config.file_model_roots
        updated = replace(
            config,
            file_model_roots=file_roots,
            excluded_model_roots=frozenset(config.excluded_model_roots | {key}),
        )
        _replace_current(replace(updated, model_search_roots=rebuild_model_roots(updated)))
        if scope == "file":
            ok, warning = _persist_model_line(updated, hit, add=False)
        else:
            ok = False
            warning = "this one comes from auto-discovery or a command-line argument, so it only applies to this run; it will be back after a restart"
        return {"removed": str(hit), "removed_scope": scope, "persisted": ok, "warning": warning}
