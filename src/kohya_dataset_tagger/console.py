"""Console color decision: whether ANSI will actually be interpreted (spec §7 "console and ANSI").

Why this module is needed
-------------------------
With `use_colors=None`, uvicorn's formatter **only asks `sys.stdout.isatty()`**. Measured on
2026-09-17: double-clicking `start.bat` makes `isatty()` true (a real console), while conhost's
`ENABLE_VIRTUAL_TERMINAL_PROCESSING` **is off** (`GetConsoleMode` = `0x3`), so the ANSI uvicorn
emitted was rendered verbatim as the literal `←[32mINFO←[0m`.

The fix is not to turn color off but to **open VT ourselves** - on the same machine,
`SetConsoleMode(h, mode | 0x4)` succeeded when tested (giving `0x7` afterwards). Only when that
fails does it fall back to no color. The decision is written to uvicorn **explicitly**, never left
as `None`: that would hand it back to a default already proven to guess wrong.

When each of the three branches happens
---------------------------------------
| Case | `isatty()` | Verdict |
|---|---|---|
| Real console + VT can be enabled | True | colored (the normal case) |
| Old system / console taken over, VT cannot be enabled | True | **no color** (this is the point of this module) |
| Output redirected to a file / pipe | False | no color (no escape sequences in a file) |

`--color always` bypasses all of the above (useful when piping logs into CI), and `never` is the reverse.
"""
from __future__ import annotations

import ctypes
import importlib
import os
import sys
from typing import Any, Mapping

__all__ = [
    "COLOR_MODES",
    "ENABLE_VIRTUAL_TERMINAL_PROCESSING",
    "enable_ansi_console",
    "resolve_use_colors",
    "colored_log_config",
]

#: The VT bit for `SetConsoleMode` (Win32 `ENABLE_VIRTUAL_TERMINAL_PROCESSING`).
ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004

#: The value domain of `--color`.
COLOR_MODES = ("auto", "always", "never")

_STD_OUTPUT_HANDLE = -11
#: The pseudo-handle returned when `GetStdHandle` fails.
_INVALID_HANDLE_VALUE = -1


def enable_ansi_console(*, kernel32: Any = None, is_windows: bool | None = None) -> bool:
    """Enable VT processing for the Windows console and return "will ANSI be interpreted".

    - Non-Windows: a POSIX terminal interprets ANSI anyway, so True;
    - No console (output redirected / started as a service): False - in that case uvicorn emits no
      color either;
    - `kernel32` / `is_windows` are injectable: acceptance can walk all three branches with fake
      objects, without actually changing a machine's console state.
    """
    windows = (os.name == "nt") if is_windows is None else bool(is_windows)
    if not windows:
        return True
    api = kernel32
    if api is None:
        if not hasattr(ctypes, "windll"):  # pragma: no cover - non-Windows already returned early
            return False
        api = ctypes.windll.kernel32
    try:
        handle = api.GetStdHandle(_STD_OUTPUT_HANDLE)
        if not handle or handle == _INVALID_HANDLE_VALUE:
            return False
        mode = ctypes.c_uint32()
        if not api.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        if mode.value & ENABLE_VIRTUAL_TERMINAL_PROCESSING:
            return True
        if not api.SetConsoleMode(handle, mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING):
            return False
    except Exception:  # noqa: BLE001 - a failed console call must never keep the service from starting
        return False
    return True


def resolve_use_colors(mode: str = "auto", stream: Any = None, *, console: bool | None = None) -> bool:
    """The `--color` decision: returns True/False, **never None**.

    `console` is an injectable answer to "will ANSI be interpreted"; None = ask the system
    (`enable_ansi_console`). This parameter exists so the criterion can cover the branch that is
    very hard to produce on a real machine: "isatty is true but VT cannot be enabled".
    """
    if mode not in COLOR_MODES:
        raise ValueError("unknown color mode: %r; available: %s" % (mode, list(COLOR_MODES)))
    if mode == "always":
        return True
    if mode == "never":
        return False
    target = sys.stdout if stream is None else stream
    try:
        if not target.isatty():
            return False
    except (AttributeError, ValueError, OSError):
        return False
    if console is not None:
        return bool(console)
    return enable_ansi_console()


def _accepts_use_colors(spec: Mapping[str, Any]) -> bool:
    """Whether this formatter recognizes `use_colors`.

    **Looking at whether the key exists is not enough**: the local uvicorn's `access` formatter does
    not write this key at all, but its class `AccessFormatter(ColourizedFormatter)` does accept the
    parameter - so "only change the ones that declare it" would leave access logs still guessing by
    `isatty()`, with half the logs garbled. The criterion is **whether the factory is a subclass of
    `ColourizedFormatter`**, which is exactly what makes the parameter its own definition.

    When uvicorn is not importable, fall back to "the key exists": do not add a hard dependency just
    for log coloring.
    """
    if "use_colors" in spec:
        return True
    factory = spec.get("()")
    if factory is None:
        return False
    try:
        from uvicorn.logging import ColourizedFormatter
    except Exception:  # noqa: BLE001 - must not raise when uvicorn is absent
        return False
    if isinstance(factory, type):
        candidate: Any = factory
    elif isinstance(factory, str):
        module_name, _, attr = factory.rpartition(".")
        try:
            module = importlib.import_module(module_name)
        except Exception:  # noqa: BLE001 - leave an unrecognized factory alone
            return False
        candidate = getattr(module, attr, None)
    else:
        return False
    try:
        return isinstance(candidate, type) and issubclass(candidate, ColourizedFormatter)
    except TypeError:
        return False


def colored_log_config(log_config: Mapping[str, Any], use_colors: bool) -> dict[str, Any]:
    """Write `use_colors` **explicitly** into uvicorn's coloring formatters (`default` and `access`).

    The two levels of copying are necessary: `uvicorn.config.LOGGING_CONFIG` is a module-level
    global, and changing it in place would affect any other uvicorn usage in the same process
    (acceptance really starts servers). Only formatters that recognize the parameter are touched -
    a plain `logging.Formatter` raises TypeError when it receives `use_colors`.
    """
    merged: dict[str, Any] = dict(log_config)
    formatters: dict[str, Any] = {}
    for name, spec in (log_config.get("formatters") or {}).items():
        copied = dict(spec)
        if _accepts_use_colors(copied):
            copied["use_colors"] = bool(use_colors)
        formatters[name] = copied
    merged["formatters"] = formatters
    return merged
