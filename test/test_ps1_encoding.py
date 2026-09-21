"""The repository's .ps1 files must carry a UTF-8 BOM and must parse under PowerShell **byte for byte**.

Why this has its own test module
--------------------------------
Without a BOM, Windows PowerShell 5.1 decodes a .ps1's contents as ANSI (GBK on this machine),
turning any Chinese in the file into mojibake, and **the script fails to parse outright**. pwsh 7 defaults to UTF-8 and never exposes the problem --
so "it runs on my machine" is not verification; a criterion has to watch it.

On 2026-09-17 we were actually burned twice:

1. After the BOM was first written, the script was edited once more -- **edit strips the BOM**.
2. The "syntax check" at the time was `Get-Content -Raw | PSParser::Tokenize`. **That was fake**:
   `Get-Content` decodes first, which is a different path from the engine's byte-wise decode,
   so the check passed while the script would not run.

The correct approach is the second criterion here: use PowerShell's own
`[System.Management.Automation.Language.Parser]::ParseFile()`,
which **reads the file byte for byte**, the same path as actually running the script.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PS1_FILES = sorted(REPO.glob("scripts/*.ps1")) + sorted(REPO.glob("*.ps1"))


def test_the_gate_actually_covers_something() -> None:
    """The criterion must not idle -- if not a single .ps1 is found, the next two tests stay green forever."""
    assert PS1_FILES, "no .ps1 found at all"


@pytest.mark.parametrize("path", PS1_FILES, ids=lambda p: p.name)
def test_ps1_starts_with_a_utf8_bom(path: Path) -> None:
    assert path.read_bytes()[:3] == b"\xef\xbb\xbf", (
        "%s has no UTF-8 BOM. Windows PowerShell 5.1 decodes it as ANSI(GBK),"
        "so Chinese makes the script fail to parse -- and pwsh 7 cannot show that.\n"
        "Fix: python -c \"import pathlib;p=pathlib.Path(r'%s');"
        "p.write_text(p.read_text(encoding='utf-8'),encoding='utf-8-sig')\"" % (path.name, path)
    )


@pytest.mark.parametrize("path", PS1_FILES, ids=lambda p: p.name)
def test_ps1_parses_the_way_the_engine_reads_it(path: Path) -> None:
    """Use ParseFile -- it reads **byte for byte**, the same path as actually running the script.

    This is the criterion that can catch "the BOM is gone"; `Get-Content | Tokenize` cannot (see the module docstring).
    """
    ps = shutil.which("powershell") or shutil.which("pwsh")
    if ps is None:
        pytest.skip("no PowerShell on this machine")
    script = (
        "$errs = $null; "
        "[System.Management.Automation.Language.Parser]::ParseFile("
        "'%s', [ref]$null, [ref]$errs) | Out-Null; "
        "if ($errs.Count -gt 0) { $errs | ForEach-Object { $_.Message }; exit 1 }"
        % str(path).replace("'", "''")
    )
    proc = subprocess.run([ps, "-NoProfile", "-Command", script],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert proc.returncode == 0, (
        "%s  failed to parse in PowerShell (after byte-wise decoding):\n%s\n%s"
        % (path.name, proc.stdout, proc.stderr))
