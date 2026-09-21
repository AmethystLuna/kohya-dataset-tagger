"""Console color decision (`console.py`, spec §7 "console and ANSI" / A31).

Why this criterion must exist
-----------------------------
With `use_colors=None`, uvicorn only asks `sys.stdout.isatty()`. Measured on a real console on
2026-09-17: `isatty()` = True and `DefaultFormatter(use_colors=None).use_colors` = True, while
`GetConsoleMode(stdout)` = `0x3` (the VT bit is off) - so the ANSI uvicorn emitted was printed by
conhost **verbatim** as the literal `←[32mINFO←[0m`. That is what the user reported.

So two things are asserted here:
 1. the tri-state decision always yields a **boolean** (`None` would put back that default already
    proven to guess wrong);
 2. the branch hardest to produce on a real machine - "isatty is true but VT cannot be enabled" -
    is covered with an injected `console=False`.
"""
from __future__ import annotations

import ctypes
import io

import pytest

from kohya_dataset_tagger import console


class _FakeConsole:
    """Fake kernel32: implements only the three calls `enable_ansi_console` uses."""

    def __init__(self, handle: int = 1, mode: int = 0x3, set_ok: bool = True) -> None:
        self.handle = handle
        self.mode = mode
        self.set_ok = set_ok
        self.set_calls: list[int] = []

    def GetStdHandle(self, which: int) -> int:
        self.asked = which
        return self.handle

    def GetConsoleMode(self, handle: int, ref) -> int:
        ctypes.cast(ref, ctypes.POINTER(ctypes.c_uint32))[0] = self.mode
        return 1

    def SetConsoleMode(self, handle: int, value: int) -> int:
        self.set_calls.append(value)
        if not self.set_ok:
            return 0
        self.mode = value
        return 1


class _Tty(io.StringIO):
    """A fake stream whose `isatty()` is true - a real console does not exist under pytest."""

    def isatty(self) -> bool:
        return True


# ---------------------------------------------------------------------------
# Tri-state decision
# ---------------------------------------------------------------------------


def test_always_and_never_ignore_the_stream() -> None:
    assert console.resolve_use_colors("always", io.StringIO()) is True
    assert console.resolve_use_colors("never", _Tty()) is False


def test_unknown_mode_is_rejected_not_silently_defaulted() -> None:
    with pytest.raises(ValueError):
        console.resolve_use_colors("rainbow")


def test_auto_without_a_tty_is_off() -> None:
    """When output is redirected to a file, no escape sequences should be written (CI logs would be full of junk)."""
    assert console.resolve_use_colors("auto", io.StringIO()) is False


def test_auto_on_a_tty_follows_the_console_answer() -> None:
    """The hardest branch to produce on a real machine: **isatty is true but VT cannot be enabled**.

    Without injection, this one could only be hit by luck on a machine with VT turned off - which
    is exactly the situation the user was in.
    """
    assert console.resolve_use_colors("auto", _Tty(), console=True) is True
    assert console.resolve_use_colors("auto", _Tty(), console=False) is False


def test_stream_without_isatty_is_not_a_crash() -> None:
    """`--color auto` hitting an object without isatty (such as an already-closed stream) must not throw."""

    class _Broken:
        def isatty(self):
            raise ValueError("I/O operation on closed file")

    assert console.resolve_use_colors("auto", _Broken()) is False
    assert console.resolve_use_colors("auto", object()) is False


# ---------------------------------------------------------------------------
# enable_ansi_console: all three branches must be reachable
# ---------------------------------------------------------------------------


def test_posix_is_always_ansi_capable() -> None:
    assert console.enable_ansi_console(is_windows=False) is True


def test_no_console_handle_means_no_ansi() -> None:
    assert console.enable_ansi_console(kernel32=_FakeConsole(handle=0), is_windows=True) is False
    assert console.enable_ansi_console(
        kernel32=_FakeConsole(handle=-1), is_windows=True
    ) is False


def test_already_enabled_is_not_touched() -> None:
    fake = _FakeConsole(mode=0x7)
    assert console.enable_ansi_console(kernel32=fake, is_windows=True) is True
    assert fake.set_calls == [], "must not change the console mode again when VT is already on"


def test_disabled_vt_is_enabled_in_place() -> None:
    """The step measured on a real machine: `0x3` -> `0x7` after adding `0x4`."""
    fake = _FakeConsole(mode=0x3)
    assert console.enable_ansi_console(kernel32=fake, is_windows=True) is True
    assert fake.set_calls == [0x3 | console.ENABLE_VIRTUAL_TERMINAL_PROCESSING] == [0x7]


def test_setconsolemode_failure_falls_back_to_no_colors() -> None:
    fake = _FakeConsole(mode=0x3, set_ok=False)
    assert console.enable_ansi_console(kernel32=fake, is_windows=True) is False


def test_kernel32_explosion_is_not_fatal() -> None:
    """A blown-up console call must never block service startup."""

    class _Boom:
        def GetStdHandle(self, which):
            raise OSError("no console")

    assert console.enable_ansi_console(kernel32=_Boom(), is_windows=True) is False


# ---------------------------------------------------------------------------
# The log_config handed to uvicorn
# ---------------------------------------------------------------------------


def test_uvicorn_shipped_formatters_are_still_colourizable() -> None:
    """**Anti-corrosion layer**: if uvicorn one day swaps these two formatters for classes that do not
    recognize `use_colors`, we would silently lose control and go back to "it guesses by isatty()" -
    which is exactly why this module exists.

    Note the criterion is the **class**, not "is the key present": the local uvicorn's `access`
    formatter does not write this key at all, so an assertion of "the key must exist" would be red,
    while the real problem is that it guesses by isatty.
    """
    from uvicorn.config import LOGGING_CONFIG

    for name in ("default", "access"):
        spec = LOGGING_CONFIG["formatters"][name]
        assert console._accepts_use_colors(spec), (
            "uvicorn's %s formatter no longer accepts use_colors; console.colored_log_config will lose its effect" % name
        )


def test_colored_log_config_writes_a_boolean_and_copies() -> None:
    from uvicorn.config import LOGGING_CONFIG

    before = LOGGING_CONFIG["formatters"]["default"]["use_colors"]
    merged = console.colored_log_config(LOGGING_CONFIG, True)
    assert merged["formatters"]["default"]["use_colors"] is True
    # access does **not** have this key in the local uvicorn, but it must be written - otherwise access logs stay garbled
    assert merged["formatters"]["access"]["use_colors"] is True
    assert LOGGING_CONFIG["formatters"]["default"]["use_colors"] == before, (
        "uvicorn's global LOGGING_CONFIG was modified in place - other uvicorn usages in the same process would be affected"
    )
    assert LOGGING_CONFIG["formatters"] is not merged["formatters"]

    off = console.colored_log_config(LOGGING_CONFIG, False)
    assert off["formatters"]["default"]["use_colors"] is False


def test_colored_log_config_leaves_foreign_formatters_alone() -> None:
    """Write the value only into formatters that recognize `use_colors`: a plain logging.Formatter raises TypeError when it receives it."""
    config = {
        "formatters": {
            "default": {"()": "uvicorn.logging.DefaultFormatter", "use_colors": None},
            "plain": {"format": "%(message)s"},
        },
        "handlers": {"h": {"formatter": "default"}},
    }
    merged = console.colored_log_config(config, True)
    assert merged["formatters"]["default"]["use_colors"] is True
    assert "use_colors" not in merged["formatters"]["plain"]
    assert merged["handlers"] == config["handlers"]
    assert config["formatters"]["default"]["use_colors"] is None, "input was modified in place"


def test_cli_exposes_the_frozen_color_modes() -> None:
    """The three values of `--color` are part of the CLI contract (§7)."""
    from kohya_dataset_tagger.__main__ import build_parser

    for mode in console.COLOR_MODES:
        assert build_parser().parse_args(["--color", mode]).color == mode
    assert build_parser().parse_args([]).color == "auto"
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--color", "rainbow"])
