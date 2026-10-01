---
name: launcher-roots-encoding
description: 2026-10-01, start.bat refused to start once a root was added from the UI - scripts/start.ps1 read roots.txt with Get-Content's ANSI default while the server writes it BOM-less UTF-8, so a Japanese path came back as mojibake and "does not exist"; the same expression also crashed outright on an empty roots.txt
metadata:
  type: topic
---

# start.bat and the Japanese dataset root (2026-10-01)

## The symptom

`start.bat` stopped starting the service. What it printed **is** the diagnosis, but only if you notice
that the path in it is not the path on disk:

    These directories do not exist or are not directories:
        E:\Downloads\fanbox\銇汇亞銇嶆槦

`E:\Downloads\fanbox\ほうき星` exists: Python's `config.read_roots_file` resolves it (`Path.is_dir()`
is true), and the folder is in Explorer. The launcher had decoded `roots.txt` with the wrong encoding
and then asked the filesystem about the mojibake.

## The root cause: two writers, one of them without a BOM

| Writer of `roots.txt` | Bytes |
|---|---|
| The server - `config._write_atomic`, i.e. every add/remove from the UI | UTF-8, **no BOM** |
| The launcher's own first interactive run - `Set-Content -Encoding UTF8` | UTF-8 **with** BOM (PowerShell 5.1) |

`scripts/start.ps1` read the file with `Get-Content` and no `-Encoding`, and **this script runs under
Windows PowerShell 5.1** - `start.bat` calls `powershell`, not `pwsh` - where that default is the ANSI
code page (936 / GB2312 on this machine). Measured on the two shapes of the same file:

| Read | Result |
|---|---|
| `Get-Content` (5.1 default), BOM-less file | `E:\Downloads\fanbox\銇汇亞銇嶆槦` |
| `Get-Content -Encoding UTF8` | `E:\Downloads\fanbox\ほうき星` |
| `Get-Content` (5.1 default), file **with** BOM | `E:\Downloads\fanbox\ほうき星` |

The third row is why this stayed hidden for as long as it did, and it is worth stating plainly: **the
first interactive run writes a BOM, and a BOM makes 5.1 guess UTF-8 correctly.** The bug could only
appear after a root was added from the UI, because the server rewrites the file BOM-less - a fact this
repository already asserted from the other side in
`test/test_roots_api.py::test_roots_file_with_a_bom_is_still_read`. PowerShell 7 does not reproduce it
either (`[System.Text.Encoding]::Default` is UTF-8 there, measured `acp=utf-8`), so a `pwsh` habit
cannot see this class of bug at all.

The `.sh` launcher reads bytes and was never affected; this was the Windows half of the "both launchers
read the same `roots.txt`" promise (p0-spec §7 / A32) quietly not holding.

**Nothing ever executed `scripts/start.ps1`.** The criteria ran `scripts/start.sh --dry-run` (its
`roots.txt` reading included, with a BOM **and** with CRLF), exercised `setup_env.ps1 -DryRun`, and
checked the ps1 files statically - but the argv assembly of the Windows launcher and its reading of
`roots.txt` had no criterion between them, and every fixture path was ASCII.

## The fix

`scripts/start.ps1` reads `roots.txt` once, with an explicit encoding:

```powershell
$fileRoots = @()
if (Test-Path $RootsFile) {
    $fileRoots = @(Get-Content $RootsFile -Encoding UTF8) | Where-Object { $_.Trim() } | ForEach-Object { $_.Trim() }
}
```

`-Encoding UTF8` was measured to read **both** shapes identically in 5.1 and in 7.6.5 (first character
`X`, last `星`, length 7 - i.e. no leftover BOM character), so the file's encoding no longer depends on
which writer touched it last. `[System.IO.File]::ReadAllLines($p, [System.Text.Encoding]::UTF8)`
behaves the same; `Get-Content` keeps the diff small.

## The second defect on the same line

The truthiness test used to be `(Get-Content $RootsFile -Raw).Trim()`. **`Get-Content -Raw` returns
`$null` for an empty file**, so `.Trim()` raised *"You cannot call a method on a null-valued
expression"* before the prompt below it could ever run. Measured on the old script with a real stdin
(`input=<an existing directory>`): rc=1, the directory never read, nothing remembered - an empty
`roots.txt` meant the launcher could not ask for a root even with a person sitting at the keyboard. The
new array read makes "empty" and "blank lines only" fall through to the prompt (measured: rc=0, root
read and written back).

`Read-Host` returns `$null` too when stdin is not a console (`start.bat < nul`, a scheduled task), which
was the same null-method crash a second time; the guard is now
`if (-not $line -or -not $line.Trim())`, so the user gets the sentence the code already intended to give
("Without a dataset root we cannot start") instead of a PowerShell type error.

## Criteria and falsification

Five criteria in `test/test_shell_scripts.py` §3b, all driving the **real** `scripts/start.ps1` under
**Windows PowerShell 5.1** in a fake repository copy (a dummy `.venv\Scripts\python.exe` is enough for
`-DryRun`, and a free port is passed in so the launcher's "port is taken" warning cannot land on the
machine-readable stdout):

1. a **BOM-less** UTF-8 `roots.txt` with a non-ASCII directory - the shape the server writes;
2. the **BOM + CRLF** shape PowerShell 5.1 itself writes;
3. an empty `roots.txt` with a real stdin: it must ask, read and remember it;
4. stdin closed: the honest sentence, and no null-method crash;
5. an **ASCII** root with a space: argv carries the line from `roots.txt` character for character.

**Falsified by putting the pre-fix `start.ps1` back** (byte-exact backup, restored afterwards, sha256
checked): criteria 1, 3 and 4 turn red - criterion 2 stays green, which is exactly right, because that
is the shape the old code accidentally handled.

Three traps came out of writing them, all worth remembering:

- **A criterion must not compare a non-ASCII path that came back through PowerShell's stdout.** The
  first version of criteria 1 and 2 asserted `argv contains データセット` and passed - but only because
  the harness that ran them sets `[Console]::OutputEncoding` to UTF-8. Measured on the same command
  line: console code page **65001** -> the child writes UTF-8, **936** -> GBK, **437** (a typical CI
  runner) -> the katakana come back as `??????`, because the characters do not exist in that code page
  at all. Those criteria would have been **red on an ordinary console while the launcher was fine**.
  They now assert what is code-page independent - the launcher reached its dry-run print with one
  `--roots` and without "does not exist" - which is the whole signal, since mojibake cannot pass the
  launcher's own `Test-Path`; criterion 5 pins the exact value where a comparison is safe.
- **PowerShell wraps its error text mid-word**, so the "does it still crash?" criterion searched for
  `null-valued expression` in a message the console was displaying as `null-valued e` / `xpression`.
  The `flat_text` helper joins the break and has a criterion of its own.
- The first version of that helper used the regex `[ \\t]+` - space, backslash or the letter `t` - and
  silently deleted every `t` (`No da ase roo configured ye`). Its falsification test was green anyway,
  because the message it checked ("null-valued expression") happens to contain **no `t`**. The second
  assertion in that test exists for this reason.

`test/test_shell_scripts.py` + `test/test_ps1_encoding.py` -> **68 passed, 1 skipped** (the skip is the
DirectML variant check, which is not this platform's business), and the new criteria were run under
**both** code page 936 and 437 to make sure they are not encoding-lucky.

## End to end, through the real `start.bat`

`start.bat -NoBrowser` was then run in the repository root against the **user's own `roots.txt`** (the
one holding `E:\Downloads\fanbox\ほうき星`), and the service really came up: `GET /api/health` returned
`{"ok":true,"version":"0.1.0","path_config":{"allowed":true,...}}`, and `GET /api/roots` listed all
three roots as `"exists":true,"is_dir":true,"persisted":true` - the Japanese one included. The launcher
printed all three roots and the URL before starting uvicorn. The service was stopped afterwards, port
3001 is free again, and no process from that run is left behind.

## What this round did not verify

- The `.sh` launcher was not re-run on a real Linux/macOS machine this round; nothing in it changed.
- The criteria drive `scripts/start.ps1` from a **copy** in a fake repository (plus the one real
  `start.bat` run above), so they assert this script's behaviour rather than the `.bat` wrapper's,
  which is a two-line `cd` + `powershell` call.

---

Related: [p0-spec](../p0-spec.md) §7, [dataset-contract](../dataset-contract.md),
[changelog/open-source-readiness](open-source-readiness.md)
