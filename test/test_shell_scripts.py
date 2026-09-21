"""Criteria for the cross-platform launcher scripts (p0-spec §7 "cross-platform launcher" / acceptance A32).

Why this has its own test module
--------------------------------
This whole class of problems is **completely invisible** on this machine (Git Bash / PowerShell 7):

1. A single non-ASCII byte in a `.bat` makes cmd.exe decode it with the console code page (936 on this machine),
   turning Chinese into mojibake -- while the editor, Git Bash, and PowerShell all display it fine.
   (Measured 2026-09-17: `start.bat` was UTF-8 without a BOM, with `e5 ...` following `rem `.)
2. With LF line endings, cmd.exe mis-parses a block like `if errorlevel 1 ( ... )`,
   while the working tree looks completely normal (`.gitattributes` pins it with `*.bat text eol=crlf`).
3. `.sh` is for Linux/macOS, and **this machine has no WSL distribution** (`wsl -l -v` exits 1),
   so real execution cannot be verified. All that can be verified is `bash -n` and the argv assembly of `--dry-run` on Git Bash 5.2.

`--dry-run` was added for point 3: it prints the argv that would be executed to stdout, **one item per line**,
so "is the argument assembly right" can be asserted on Windows too. stdout has only lines starting with `DRY_RUN_`,
and every human-facing hint goes to stderr.

Criteria must be able to fail
-----------------------------
Every assertion function has a **falsification test**: a CRLF `.sh`, an LF `.bat`, a `.bat` containing Chinese,
a dry-run output that forwards `--extra-models` back to the command line, an index profile using the wrong source, and two onnxruntime variants installed at once.
Testing only "it passes right now" does not count as a criterion.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SH_FILES = sorted(REPO.glob("*.sh")) + sorted(REPO.glob("scripts/*.sh"))
BAT_FILES = sorted(REPO.glob("*.bat")) + sorted(REPO.glob("scripts/*.bat"))
ROOT_WRAPPERS = {
    "start.sh": "scripts/start.sh",
    "setup_env.sh": "scripts/setup_env.sh",
    "setup_env_cn.sh": "scripts/setup_env.sh",   # thin wrapper + --index cn
}
LAUNCHERS = ("scripts/start.sh", "scripts/start.ps1")

PIP_PYPI = "https://pypi.org/simple"
# The cn profile's first choice: on 2026-09-19 a local Range probe measured the 245 MB onnxruntime-gpu --
# USTC 8.4-11 MB/s, Aliyun 0.86-1.25 MB/s, pypi.org/files.pythonhosted 0.55-1.01 MB/s;
# TUNA's simple index page returns 200, and a direct request for the wheel file returns 403 (probably hotlink protection), so it is not in the profile.
CN_PRIMARY = "https://mirrors.ustc.edu.cn/pypi/simple"
DOMESTIC_MIRROR_HOSTS = ("mirrors.ustc.edu.cn", "mirrors.aliyun.com", "pypi.tuna.tsinghua.edu.cn")

BASH = shutil.which("bash")
needs_bash = pytest.mark.skipif(BASH is None, reason="no bash on this machine (Linux / macOS / Git Bash)")
POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")
needs_powershell = pytest.mark.skipif(POWERSHELL is None, reason="no PowerShell on this machine")


# --------------------------------------------------------------------------- helpers
def posix_path(path: os.PathLike | str) -> str:
    """The path as the scripts see it: Git Bash accepts C:/x (**not** \\x), and leaves it as is on Linux."""
    return str(path).replace("\\", "/")


def msys_posix_path(path: os.PathLike | str) -> str:
    """Only the "fake Linux" cases need this: turn C:/x into MSYS's /c/x.

    **It must not contain a colon** -- if the script splits on POSIX's os.pathsep=':', a Windows drive letter tears itself apart
    (C:/a becomes C and /a, then reports "directory does not exist"). On real Linux this function is the identity transform.
    """
    text = posix_path(path)
    if len(text) > 2 and text[1] == ":" and text[0].isalpha() and text[2] == "/":
        return "/%s%s" % (text[0].lower(), text[2:])
    return text


def is_absolute_sh_path(text: str) -> bool:
    """Is this an absolute path **as the shell sees it**?

    Three forms are legitimate, and all three really occur:

      * /f/repo/.venv/Scripts/python.exe - what Git Bash prints in this repository, and the only
        form on Linux / macOS;
      * F:/repo/.venv/Scripts/python.exe - the drive-letter form Git Bash prints when the
        environment hands it a Windows PWD. **Measured on a GitHub windows-latest runner
        (2026-09-21)**: the two start.sh --dry-run criteria went red there while Ubuntu stayed
        green, because they accepted only the first form. This machine happens to print the first;
      * F:\\repo\\.venv\\Scripts\\python.exe - the same, with backslashes.

    A bare relative path (.venv/Scripts/python.exe) is what this gate is for, and it still fails.
    """
    if text.startswith("/"):
        return True
    return len(text) > 2 and text[1] == ":" and text[0].isalpha() and text[2] in "/\\"


def run_script(args, *, env_extra=None, cwd=REPO, timeout=120):
    """Run a .sh. **encoding must be explicit**: this machine's locale is GBK, and when the script's output contains Chinese
    subprocess's reader thread raises UnicodeDecodeError, after which stdout simply becomes None
    (this repository has hit that pitfall; see AGENTS.md). stdin is DEVNULL: never let a criterion hang on an interactive prompt."""
    env = os.environ.copy()
    env["MSYS_NO_PATHCONV"] = "1"      # so MSYS does not rewrite path-like arguments
    for key in ("PORT", "KOHYA_TAGGER_ROOTS", "KOHYA_TAGGER_ROOTS_FILE",
                "KOHYA_TAGGER_MODEL_PATHS_FILE", "KOHYA_TAGGER_PIP_INDEX"):
        env.pop(key, None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [BASH, *[str(a) for a in args]],
        cwd=str(cwd), env=env, stdin=subprocess.DEVNULL, capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=timeout,
    )


def run_ps1(args, *, env_extra=None, cwd=REPO, timeout=120):
    """Run a .ps1 (Windows PowerShell 5.1) -- it decodes byte for byte, the same path as actually running it."""
    assert POWERSHELL is not None
    env = os.environ.copy()
    env.pop("KOHYA_TAGGER_PIP_INDEX", None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         *[str(a) for a in args]],
        cwd=str(cwd), env=env, stdin=subprocess.DEVNULL, capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=timeout,
    )


# --------------------------------------------------------------------------- checkers
def line_ending_problems(path: Path, *, want: str) -> list[str]:
    """want='lf' (.sh) or 'crlf' (.bat). Returns problem descriptions; an empty list = pass."""
    raw = path.read_bytes()
    crlf = raw.count(b"\r\n")
    bare_lf = raw.count(b"\n") - crlf
    bare_cr = raw.count(b"\r") - crlf
    problems: list[str] = []
    if want == "lf":
        if crlf or bare_cr:
            problems.append("%s: expected LF, found %d CRLF and %d bare CR" % (path.name, crlf, bare_cr))
    elif want == "crlf":
        if bare_lf:
            problems.append(
                "%s: expected CRLF, found %d bare LF -- cmd.exe mis-parses "
                "a multi-line block like `if errorlevel 1 ( ... )`" % (path.name, bare_lf))
    else:
        raise ValueError("want must be 'lf' or 'crlf': %r" % want)
    if raw and not raw.endswith(b"\n"):
        problems.append("%s: the last line has no newline" % path.name)
    return problems


def non_ascii_problems(path: Path) -> list[str]:
    """Find non-ASCII **by byte**. cmd.exe decodes .bat with the console code page, so only bytes count."""
    problems: list[str] = []
    for lineno, line in enumerate(path.read_bytes().split(b"\n"), start=1):
        bad = [b for b in line if b > 0x7F]
        if not bad:
            continue
        first = line.index(bad[0]) + 1
        problems.append(
            "%s:%d has %d non-ASCII bytes (the first at column %d, bytes %s; decoded as UTF-8 it is %r) --"
            "cmd.exe decodes with the console code page (936 on this machine), so it shows up as mojibake on screen"
            % (path.name, lineno, len(bad), first, bytes(bad[:3]).hex(" "),
               line.decode("utf-8", errors="replace").strip()[:40]))
    return problems


@dataclass(frozen=True)
class DryRun:
    python: str | None = None
    port: str | None = None
    args: tuple[str, ...] = ()
    gpu: str | None = None
    pips: tuple[str, ...] = ()
    index: str | None = None          # first choice (kept for old assertions)
    indexes: tuple[str, ...] = ()     # all choices, in try order; the cn profile has more than one


def parse_dry_run(stdout: str) -> DryRun:
    """Parse DRY_RUN_* lines. argv keeps its order (the order itself is what is asserted).

    Any other line is an error: --dry-run's stdout is for machines, and human-facing hints must be on stderr."""
    python = port = gpu = None
    args: list[str] = []
    pips: list[str] = []
    indexes: list[str] = []
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        key, sep, value = line.partition("=")
        if not sep or not key.startswith("DRY_RUN_"):
            raise AssertionError("a non-DRY_RUN_ line appeared on dry-run's stdout: %r" % line)
        if key == "DRY_RUN_PYTHON":
            python = value
        elif key == "DRY_RUN_PORT":
            port = value
        elif key == "DRY_RUN_ARG":
            args.append(value)
        elif key == "DRY_RUN_GPU":
            gpu = value
        elif key == "DRY_RUN_PIP":
            pips.append(value)
        elif key == "DRY_RUN_INDEX":
            indexes.append(value)          # the cn profile prints several lines, in try order
        else:
            raise AssertionError("unrecognized dry-run line: %r" % line)
    return DryRun(python=python, port=port, args=tuple(args), gpu=gpu,
                  pips=tuple(pips), index=(indexes[0] if indexes else None),
                  indexes=tuple(indexes))


def start_arg_problems(dry: DryRun, *, must_have=(), must_not_have=()) -> list[str]:
    problems: list[str] = []
    if not dry.python:
        problems.append("no DRY_RUN_PYTHON line")
    elif not is_absolute_sh_path(dry.python):
        problems.append("DRY_RUN_PYTHON is not an absolute path: %r" % dry.python)
    if not dry.port or not dry.port.isdigit():
        problems.append("DRY_RUN_PORT is not a port number: %r" % dry.port)
    if list(dry.args[:2]) != ["-m", "kohya_dataset_tagger"]:
        problems.append("argv does not start with `-m kohya_dataset_tagger`: %r" % (list(dry.args[:2]),))
    if "--port" not in dry.args:
        problems.append("there is no --port in argv (the chosen port must really be passed down)")
    elif dry.port not in dry.args:
        problems.append("DRY_RUN_PORT=%r does not appear in argv" % dry.port)
    for item in must_have:
        if item not in dry.args:
            problems.append("argv is missing %r" % item)
    for item in must_not_have:
        if item in dry.args:
            problems.append("argv must not contain %r (p0-spec §3.7: model_paths.txt is read by the server itself)" % item)
    return problems


def setup_pip_problems(dry: DryRun, *, want_gpu: str, must_have=(),
                       want_index: str = PIP_PYPI) -> list[str]:
    """want_index is the source that must be hit **first**: the pypi profile uses the official source, the cn profile a domestic mirror.

    Every pip call must pass an explicit --index-url: without it, global pip config takes over, and a global source can be so slow it
    "looks like a hang" (measured 2026-09-17: Aliyun managed only 0.07 MB/s for the 245 MB onnxruntime-gpu).
    """
    problems: list[str] = []
    if dry.gpu != want_gpu:
        problems.append("DRY_RUN_GPU=%r, expected %r" % (dry.gpu, want_gpu))
    if not dry.pips:
        problems.append("no DRY_RUN_PIP line")
    for item in must_have:
        if item not in dry.pips:
            problems.append("DRY_RUN_PIP is missing %r (actual: %s)" % (item, ", ".join(dry.pips)))
    variants = [p for p in dry.pips if p in ("onnxruntime", "onnxruntime-gpu", "onnxruntime-directml")]
    if len(variants) > 1:
        problems.append("more than one onnxruntime variant installed at once: %s (the three variants are mutually exclusive)" % variants)
    if dry.index != want_index:
        problems.append(
            "DRY_RUN_INDEX's first choice is %r, expected %r (an explicit --index-url is required; falling back to global pip config is not allowed)"
            % (dry.index, want_index))
    return problems


# --------------------------------------------------------------------------- 0. keep the criteria from idling
def test_the_gate_actually_covers_something() -> None:
    assert SH_FILES, "not a single .sh found; the next few tests stay green forever"
    assert BAT_FILES, "not a single .bat found; the next few tests stay green forever"
    missing = [name for name in ROOT_WRAPPERS if not (REPO / name).exists()]
    assert not missing, "these launchers are missing from the repository root: %s" % missing


# --------------------------------------------------------------------------- 1. encoding and line endings
@pytest.mark.parametrize("path", SH_FILES, ids=lambda p: p.name)
def test_shell_scripts_are_lf(path: Path) -> None:
    assert not line_ending_problems(path, want="lf")


@pytest.mark.parametrize("path", BAT_FILES, ids=lambda p: p.name)
def test_bat_files_are_crlf(path: Path) -> None:
    assert not line_ending_problems(path, want="crlf")


@pytest.mark.parametrize("path", BAT_FILES, ids=lambda p: p.name)
def test_bat_files_are_pure_ascii(path: Path) -> None:
    """This is the executable criterion for "no mojibake in cmd.exe": Chinese is left to PowerShell, and a .bat may contain only ASCII."""
    assert not non_ascii_problems(path)


@pytest.mark.parametrize("path", SH_FILES, ids=lambda p: p.name)
@needs_bash
def test_shell_scripts_parse(path: Path) -> None:
    proc = subprocess.run([BASH, "-n", str(path)], cwd=str(REPO), stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert proc.returncode == 0, "bash -n failed: %s\n%s" % (path.name, proc.stderr)


# --------------------------------------------------------------------------- 2. the two-layer structure
@pytest.mark.parametrize("name,target", sorted(ROOT_WRAPPERS.items()))
def test_root_wrapper_delegates_to_scripts(name: str, target: str) -> None:
    path = REPO / name
    assert path.exists(), "the repository root has no %s (Windows has %s; the two must be symmetric)" % (name, name.replace(".sh", ".bat"))
    text = path.read_text(encoding="utf-8")
    assert target in text, "%s does not delegate the work to %s" % (name, target)
    assert text.startswith("#!/usr/bin/env bash")
    assert "set -euo pipefail" in text
    assert 'cd "$(dirname "$0")"' in text
    assert '"$@"' in text, "%s does not pass the arguments through verbatim" % name


def test_shell_scripts_are_executable_in_the_git_index() -> None:
    """The executable bit is invisible in a Windows working tree; the git index is the real source (git ls-files -s must show 100755)."""
    git = shutil.which("git")
    if git is None:
        pytest.skip("no git on this machine")
    names = sorted(ROOT_WRAPPERS) + ["scripts/start.sh", "scripts/setup_env.sh"]
    proc = subprocess.run([git, "ls-files", "-s", "--", *names], cwd=str(REPO),
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert proc.returncode == 0, proc.stderr
    modes = {}
    for line in proc.stdout.splitlines():
        meta, _, tracked = line.partition("\t")
        if meta.split():
            modes[tracked] = meta.split()[0]
    untracked = [n for n in names if n not in modes]
    assert not untracked, "these .sh files are not in the git index yet, so the executable bit cannot be verified: %s" % untracked
    wrong = {n: m for n, m in modes.items() if m != "100755"}
    assert not wrong, "the mode in the git index is not 100755: %s\nFix: git update-index --chmod=+x <file>" % wrong


@pytest.mark.parametrize("name", LAUNCHERS)
def test_launchers_do_not_forward_model_paths(name: str) -> None:
    """§3.7: a launcher **must not** turn model_paths.txt into command-line arguments, otherwise a line deleted in the UI
    gets overridden by the command line on the next start. Its location may still be printed."""
    text = (REPO / name).read_text(encoding="utf-8-sig")
    assert "model_paths.txt" in text, "%s does not even mention its location?" % name
    assert "--extra-models" not in text, "%s forwards it back again (p0-spec §3.7)" % name


# --------------------------------------------------------------------------- 3. start.sh --dry-run
@needs_bash
def test_start_sh_dry_run_builds_the_server_command(tmp_path: Path) -> None:
    root = tmp_path / "dataset root"       # with a space: argv is passed item by item, so it must not be joined into one line
    root.mkdir()
    proc = run_script(["scripts/start.sh", "--dry-run"],
                      env_extra={"KOHYA_TAGGER_ROOTS": posix_path(root)})
    assert proc.returncode == 0, proc.stderr
    dry = parse_dry_run(proc.stdout)       # a non-DRY_RUN_ line makes it fail outright
    problems = start_arg_problems(dry, must_have=("--roots", posix_path(root)),
                                  must_not_have=("--extra-models",))
    assert not problems, "%s\nstdout:\n%s\nstderr:\n%s" % ("\n".join(problems), proc.stdout, proc.stderr)


@needs_bash
def test_start_sh_dry_run_does_not_forward_model_paths(tmp_path: Path) -> None:
    root = tmp_path / "ds"
    root.mkdir()
    models = tmp_path / "my-taggers"
    models.mkdir()
    model_paths = tmp_path / "model_paths.txt"
    model_paths.write_text("# comment\n%s\n" % posix_path(models), encoding="utf-8")
    proc = run_script(["scripts/start.sh", "--dry-run"], env_extra={
        "KOHYA_TAGGER_ROOTS": posix_path(root),
        "KOHYA_TAGGER_MODEL_PATHS_FILE": posix_path(model_paths),
    })
    assert proc.returncode == 0, proc.stderr
    dry = parse_dry_run(proc.stdout)
    assert posix_path(models) not in dry.args, "a path from model_paths.txt was forwarded: %r" % (dry.args,)
    assert start_arg_problems(dry, must_not_have=("--extra-models",)) == []


@needs_bash
def test_start_sh_reads_a_bom_crlf_roots_file(tmp_path: Path) -> None:
    """PowerShell 5.1's Set-Content -Encoding UTF8 writes exactly BOM + CRLF.
    Those bytes turn the first root into a "nonexistent path", and the error message is completely nonsensical."""
    root = tmp_path / "ds"
    root.mkdir()
    roots_file = tmp_path / "roots.txt"
    roots_file.write_bytes(b"\xef\xbb\xbf" + posix_path(root).encode("utf-8") + b"\r\n")
    proc = run_script(["scripts/start.sh", "--dry-run"],
                      env_extra={"KOHYA_TAGGER_ROOTS_FILE": posix_path(roots_file)})
    assert proc.returncode == 0, proc.stderr
    dry = parse_dry_run(proc.stdout)
    assert posix_path(root) in dry.args, "the BOM/CRLF was not stripped: %r" % (dry.args,)


@needs_bash
def test_start_sh_trims_whitespace_around_roots(tmp_path: Path) -> None:
    """roots.txt is edited by hand, so lines may have leading or trailing spaces. Without trimming, `  /data  ` is treated as
    a nonexistent path -- and the error message looks nothing like the real cause.

    (BOM + spaces on both sides + CRLF together: all three have been hit, and "the trim was not actually in effect" was
    caught by measurement in this repository -- the original trim's regex had one extra space, making it a no-op.)"""
    root = tmp_path / "ds"
    root.mkdir()
    roots_file = tmp_path / "roots.txt"
    roots_file.write_bytes(b"\xef\xbb\xbf  " + posix_path(root).encode("utf-8") + b"  \r\n")
    proc = run_script(["scripts/start.sh", "--dry-run"],
                      env_extra={"KOHYA_TAGGER_ROOTS_FILE": posix_path(roots_file)})
    assert proc.returncode == 0, proc.stderr
    args = parse_dry_run(proc.stdout).args
    assert posix_path(root) in args, "the whitespace on both sides was not removed: %r" % (args,)


@needs_bash
def test_start_sh_port_priority(tmp_path: Path) -> None:
    """--port > PORT > 3001, and the chosen port must really be passed down."""
    root = tmp_path / "ds"
    root.mkdir()
    env = {"KOHYA_TAGGER_ROOTS": posix_path(root), "PORT": "34567"}

    from_wrapper = parse_dry_run(run_script(["start.sh", "--dry-run"], env_extra=env).stdout)
    assert from_wrapper.port == "34567", "when --port is not given, $PORT should be used"

    from_flag = parse_dry_run(run_script(["start.sh", "--dry-run", "--port", "34568"], env_extra=env).stdout)
    assert from_flag.port == "34568", "--port should override $PORT"
    assert "--port" in from_flag.args and "34568" in from_flag.args


@needs_bash
def test_start_sh_roots_priority(tmp_path: Path) -> None:
    """command line > KOHYA_TAGGER_ROOTS > roots.txt > interactive."""
    env_root = tmp_path / "from-env"
    flag_root = tmp_path / "from-flag"
    for directory in (env_root, flag_root):
        directory.mkdir()
    env = {"KOHYA_TAGGER_ROOTS": posix_path(env_root)}

    by_env = parse_dry_run(run_script(["scripts/start.sh", "--dry-run"], env_extra=env).stdout)
    assert posix_path(env_root) in by_env.args, "KOHYA_TAGGER_ROOTS was not read: %r" % (by_env.args,)

    by_flag = parse_dry_run(run_script(
        ["scripts/start.sh", "--dry-run", "--roots", posix_path(flag_root)], env_extra=env).stdout)
    assert posix_path(flag_root) in by_flag.args
    assert posix_path(env_root) not in by_flag.args, "the command line should override KOHYA_TAGGER_ROOTS"


@needs_bash
def test_start_sh_splits_colon_separated_roots_when_uname_says_linux(tmp_path: Path) -> None:
    """On Linux, KOHYA_TAGGER_ROOTS is joined with os.pathsep=':' (the server's _split_multi splits it that way).
    This machine has no WSL distribution, so this branch can only be verified under a fake uname; on Windows it **must never** split on :,
    otherwise C:/data is split into C and /data."""
    shim_dir = _posix_uname_shim(tmp_path)
    if shim_dir is None:
        pytest.skip("the fake uname on PATH did not take effect")
    one = tmp_path / "one"
    two = tmp_path / "two"
    for directory in (one, two):
        directory.mkdir()
    first, second = msys_posix_path(one), msys_posix_path(two)
    proc = run_script(["scripts/start.sh", "--dry-run"], env_extra={
        "PATH": _shim_path(shim_dir),
        "KOHYA_TAGGER_ROOTS": "%s:%s" % (first, second),
    })
    assert proc.returncode == 0, proc.stderr
    args = parse_dry_run(proc.stdout).args
    assert first in args and second in args, "did not split on : %r" % (args,)


@needs_bash
def test_start_sh_refuses_a_missing_root(tmp_path: Path) -> None:
    """A nonexistent root must exit **now**: starting the service with a bad path only yields a screen full of 403s."""
    proc = run_script(["scripts/start.sh", "--dry-run"],
                      env_extra={"KOHYA_TAGGER_ROOTS": posix_path(tmp_path / "nope")})
    assert proc.returncode != 0
    assert "do not exist" in proc.stderr, proc.stderr
    assert "DRY_RUN_" not in proc.stdout, "dry-run results must not be printed on error"


@needs_bash
def test_start_sh_without_a_venv_says_what_to_run(tmp_path: Path) -> None:
    fake = tmp_path / "fake-repo"
    (fake / "scripts").mkdir(parents=True)
    shutil.copy(REPO / "scripts" / "start.sh", fake / "scripts" / "start.sh")
    proc = run_script([fake / "scripts" / "start.sh", "--dry-run"],
                      env_extra={"KOHYA_TAGGER_ROOTS": posix_path(tmp_path)}, cwd=fake)
    assert proc.returncode != 0, "exits 0 with no .venv?"
    assert "setup_env.sh" in proc.stderr, "did not tell the user to run ./setup_env.sh first: %s" % proc.stderr


# --------------------------------------------------------------------------- 4. setup_env.sh --dry-run
@needs_bash
def test_setup_env_dry_run_cuda_pins_pypi_and_the_gpu_variant() -> None:
    proc = run_script(["scripts/setup_env.sh", "--dry-run", "--gpu", "cuda"])
    assert proc.returncode == 0, proc.stderr
    dry = parse_dry_run(proc.stdout)
    problems = setup_pip_problems(dry, want_gpu="cuda",
                                  must_have=("onnxruntime-gpu", "fastapi", "pillow"))
    assert not problems, "%s\nstdout:\n%s" % ("\n".join(problems), proc.stdout)


@needs_bash
def test_setup_env_dry_run_cpu() -> None:
    proc = run_script(["setup_env.sh", "--dry-run", "--gpu", "cpu"])
    assert proc.returncode == 0, proc.stderr
    dry = parse_dry_run(proc.stdout)
    problems = setup_pip_problems(dry, want_gpu="cpu", must_have=("onnxruntime",))
    assert not problems, "\n".join(problems)


@needs_bash
def test_setup_env_dry_run_installs_nothing(tmp_path: Path) -> None:
    """--dry-run must not touch the venv: given a nonexistent --venv, the directory must not be created either."""
    venv = tmp_path / "nowhere" / ".venv"
    proc = run_script(["scripts/setup_env.sh", "--dry-run", "--gpu", "cpu",
                       "--venv", posix_path(venv)])
    assert proc.returncode == 0, proc.stderr
    assert not venv.exists(), "--dry-run actually created a venv"


@pytest.mark.skipif(os.name == "nt", reason="directml is a Windows-only variant, and os.name == 'nt' on this machine")
@needs_bash
def test_setup_env_rejects_directml_off_windows() -> None:
    proc = run_script(["scripts/setup_env.sh", "--dry-run", "--gpu", "directml"])
    assert proc.returncode != 0, "--gpu directml must exit with an error on non-Windows"


@needs_bash
def test_setup_env_rejects_directml_when_uname_says_linux(tmp_path: Path) -> None:
    """Real Linux cannot be verified (this machine has no WSL distribution), so a fake uname is prepended to PATH to exercise this branch."""
    shim_dir = _posix_uname_shim(tmp_path)
    if shim_dir is None:
        pytest.skip("the fake uname on PATH did not take effect (does MSYS refuse to execute an extensionless script?)")
    proc = run_script(["scripts/setup_env.sh", "--dry-run", "--gpu", "directml"],
                      env_extra={"PATH": _shim_path(shim_dir)})
    assert proc.returncode != 0
    assert "Windows" in proc.stderr, proc.stderr
    assert "DRY_RUN_" not in proc.stdout


@needs_bash
def test_setup_env_dry_run_never_deletes_the_venv(tmp_path: Path) -> None:
    """--dry-run means "do nothing": even with --recreate -y (which would normally delete outright), it must not touch anything."""
    victim = tmp_path / ".venv"
    victim.mkdir()
    (victim / "keep.txt").write_text("do not delete me", encoding="utf-8")
    proc = run_script(["scripts/setup_env.sh", "--dry-run", "--recreate", "-y",
                       "--venv", posix_path(victim)])
    assert proc.returncode == 0, proc.stderr
    assert victim.is_dir() and (victim / "keep.txt").exists(), "--dry-run actually performed the deletion"


@needs_bash
def test_setup_env_refuses_to_delete_a_non_venv_directory(tmp_path: Path) -> None:
    """--recreate is a deletion: a path not ending in .venv/venv must be refused, and not a single byte may change."""
    victim = tmp_path / "not-a-venv"
    victim.mkdir()
    (victim / "keep.txt").write_text("do not delete me", encoding="utf-8")
    proc = run_script(["scripts/setup_env.sh", "--recreate", "-y", "--venv", posix_path(victim)])
    assert proc.returncode != 0, "it actually agreed to delete a non-venv directory"
    assert victim.is_dir() and (victim / "keep.txt").exists(), "it really deleted it!"


def _shim_path(shim_dir: Path) -> str:
    return "%s%s%s" % (posix_path(shim_dir), os.pathsep, os.environ.get("PATH", ""))


def _posix_uname_shim(tmp_path: Path) -> Path | None:
    """Create a fake uname that prints Linux, and confirm bash can really execute it (return None if not)."""
    shim_dir = tmp_path / "fakebin"
    shim_dir.mkdir()
    shim = shim_dir / "uname"
    shim.write_bytes(b"#!/usr/bin/env bash\nprintf 'Linux\\n'\n")
    shim.chmod(0o755)
    probe = subprocess.run(
        [BASH, "-c", "uname -s"], stdin=subprocess.DEVNULL, capture_output=True, text=True,
        encoding="utf-8", errors="replace", env={**os.environ, "PATH": _shim_path(shim_dir)})

    return shim_dir if probe.stdout.strip() == "Linux" else None


# --------------------------------------------------------------------------- 4b. index profiles: standard / cn
@needs_bash
def test_setup_env_cn_dry_run_uses_domestic_mirrors_with_a_pypi_fallback() -> None:
    proc = run_script(["scripts/setup_env.sh", "--dry-run", "--gpu", "cpu", "--index", "cn"])
    assert proc.returncode == 0, proc.stderr
    dry = parse_dry_run(proc.stdout)
    assert dry.indexes, "the cn profile printed no DRY_RUN_INDEX at all"
    assert dry.index == CN_PRIMARY, "the cn profile's first choice is not the fastest measured domestic mirror: %r" % (dry.indexes,)
    assert all(any(host in url for host in DOMESTIC_MIRROR_HOSTS) for url in dry.indexes[:-1]), \
        "a non-domestic mirror got into the cn profile's fallback chain: %r" % (dry.indexes,)
    assert dry.indexes[-1] == PIP_PYPI, "the cn profile has no official source as the last fallback: %r" % (dry.indexes,)
    problems = setup_pip_problems(dry, want_gpu="cpu", must_have=("fastapi",), want_index=CN_PRIMARY)
    assert not problems, "%s\nstdout:\n%s" % ("\n".join(problems), proc.stdout)


@needs_bash
def test_setup_env_cn_wrapper_sh_is_the_same_script_plus_the_cn_profile() -> None:
    proc = run_script(["setup_env_cn.sh", "--dry-run", "--gpu", "cpu"])
    assert proc.returncode == 0, proc.stderr
    dry = parse_dry_run(proc.stdout)
    assert dry.index == CN_PRIMARY, "setup_env_cn.sh did not use the cn profile: %r" % (dry.indexes,)
    assert dry.indexes[-1] == PIP_PYPI, "setup_env_cn.sh has no official source as the last fallback: %r" % (dry.indexes,)


def test_setup_env_cn_wrappers_delegate_and_inject_the_flag() -> None:
    """Both cn entry points must be thin wrappers: they hand the work to the same implementation and only inject one "use the cn profile" switch."""
    sh = (REPO / "setup_env_cn.sh").read_text(encoding="utf-8")
    assert "scripts/setup_env.sh" in sh, "setup_env_cn.sh does not delegate to scripts/setup_env.sh"
    assert "--index cn" in sh, "setup_env_cn.sh does not inject --index cn"
    assert '"$@"' in sh, "setup_env_cn.sh does not pass the arguments through verbatim"
    bat = (REPO / "setup_env_cn.bat").read_text(encoding="ascii")
    assert "scripts\\setup_env.ps1" in bat, "setup_env_cn.bat does not delegate to scripts/setup_env.ps1"
    assert "-Index cn" in bat, "setup_env_cn.bat does not inject -Index cn"
    assert "%*" in bat, "setup_env_cn.bat does not pass the arguments through verbatim"


@needs_bash
def test_setup_env_index_env_override_replaces_the_profile() -> None:
    proc = run_script(
        ["scripts/setup_env.sh", "--dry-run", "--gpu", "cpu", "--index", "cn"],
        env_extra={"KOHYA_TAGGER_PIP_INDEX": "https://mirror.example/simple,https://pypi.org/simple"})
    assert proc.returncode == 0, proc.stderr
    dry = parse_dry_run(proc.stdout)
    assert dry.indexes == ("https://mirror.example/simple", PIP_PYPI), dry.indexes


@needs_bash
def test_setup_env_rejects_an_unknown_index_profile() -> None:
    proc = run_script(["scripts/setup_env.sh", "--dry-run", "--index", "mirror"])
    assert proc.returncode != 0, "an unknown --index exits 0?"
    assert "DRY_RUN_" not in proc.stdout, "dry-run results must not be printed on error"


@needs_powershell
def test_setup_env_ps1_dry_run_uses_the_cn_profile() -> None:
    """The Windows implementation must be assertable too -- the .ps1 and .sh index profiles must match."""
    proc = run_ps1(["scripts/setup_env.ps1", "-Index", "cn", "-Gpu", "cpu", "-DryRun"])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    dry = parse_dry_run(proc.stdout)
    assert dry.index == CN_PRIMARY, "the ps1 cn profile's first choice is wrong: %r" % (dry.indexes,)
    assert dry.indexes[-1] == PIP_PYPI, "the ps1 cn profile has no official source as the last fallback: %r" % (dry.indexes,)
    problems = setup_pip_problems(dry, want_gpu="cpu", must_have=("fastapi", "onnxruntime"),
                                  want_index=CN_PRIMARY)
    assert not problems, "%s\nstdout:\n%s" % ("\n".join(problems), proc.stdout)


@needs_powershell
def test_setup_env_ps1_dry_run_installs_nothing(tmp_path: Path) -> None:
    """-DryRun writes not a single byte: given a nonexistent -VenvPath, the directory must not be created either."""
    venv = tmp_path / "nowhere" / ".venv"
    proc = run_ps1(["scripts/setup_env.ps1", "-Gpu", "cpu", "-DryRun", "-VenvPath", str(venv)])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not venv.exists(), "-DryRun actually created a venv"


# --------------------------------------------------------------------------- 5. falsification: deliberately broken input must go red
def test_line_ending_gate_goes_red_on_a_crlf_shell_script(tmp_path: Path) -> None:
    broken = tmp_path / "broken.sh"
    broken.write_bytes(b"#!/usr/bin/env bash\r\nset -euo pipefail\r\necho hi\r\n")
    good = tmp_path / "good.sh"
    good.write_bytes(b"#!/usr/bin/env bash\nset -euo pipefail\necho hi\n")
    assert line_ending_problems(broken, want="lf"), "a CRLF .sh passes?"
    assert not line_ending_problems(good, want="lf")


def test_line_ending_gate_goes_red_on_an_lf_bat(tmp_path: Path) -> None:
    broken = tmp_path / "broken.bat"
    broken.write_bytes(b"@echo off\nif errorlevel 1 (\n  pause\n)\n")
    good = tmp_path / "good.bat"
    good.write_bytes(b"@echo off\r\nif errorlevel 1 (\r\n  pause\r\n)\r\n")
    assert line_ending_problems(broken, want="crlf"), "an LF .bat passes?"
    assert not line_ending_problems(good, want="crlf")


def test_ascii_gate_goes_red_on_a_chinese_bat(tmp_path: Path) -> None:
    broken = tmp_path / "broken.bat"
    broken.write_bytes("echo 启动失败，看上面的信息。\r\n".encode("utf-8"))
    good = tmp_path / "good.bat"
    good.write_bytes(b"echo Startup failed. See the message above.\r\n")
    assert non_ascii_problems(broken), "a .bat containing Chinese passes?"
    assert not non_ascii_problems(good)


def test_gates_go_red_on_a_missing_trailing_newline(tmp_path: Path) -> None:
    broken = tmp_path / "broken.sh"
    broken.write_bytes(b"#!/usr/bin/env bash")     # no trailing newline, and the line ending is right
    assert line_ending_problems(broken, want="lf"), "a missing trailing newline passes?"


def test_argv_gate_goes_red_when_model_paths_are_forwarded_again() -> None:
    forwarded = "DRY_RUN_PYTHON=/repo/.venv/bin/python\nDRY_RUN_PORT=3001\n" + "".join(
        "DRY_RUN_ARG=%s\n" % a for a in
        ("-m", "kohya_dataset_tagger", "--port", "3001", "--roots", "/data",
         "--extra-models", "/models"))
    problems = start_arg_problems(parse_dry_run(forwarded), must_not_have=("--extra-models",))
    assert problems, "it forwards --extra-models back again but nobody complains"

    clean = forwarded.replace("DRY_RUN_ARG=--extra-models\n", "").replace("DRY_RUN_ARG=/models\n", "")
    assert not start_arg_problems(parse_dry_run(clean), must_not_have=("--extra-models",))


def test_the_interpreter_path_may_be_any_absolute_form_but_not_a_relative_one() -> None:
    """Both absolute spellings a shell can produce are accepted; a relative one never is.

    Git Bash prints /f/repo/... here and D:/a/repo/... on a GitHub windows-latest runner (the
    environment hands it a Windows PWD). The gate used to test `startswith("/")`, so the runner's
    two --dry-run criteria failed while Ubuntu stayed green - the value was absolute all along.
    """
    args = ("-m", "kohya_dataset_tagger", "--port", "3001")
    for good in ("/home/runner/work/kdt/.venv/bin/python",                     # Linux / macOS
                 "/f/ai-alchemy/kdt/.venv/Scripts/python.exe",                 # Git Bash here
                 "D:/a/kdt/kdt/.venv/Scripts/python.exe",                      # Git Bash on the runner
                 r"D:\\a\\kdt\\.venv\\Scripts\\python.exe"):         # ...with backslashes
        assert not start_arg_problems(DryRun(python=good, port="3001", args=args)), good
    relative = start_arg_problems(DryRun(python=".venv/Scripts/python.exe", port="3001", args=args))
    assert relative, "a relative interpreter path is the thing this gate exists to catch"


def test_argv_gate_goes_red_when_the_port_is_not_passed_down() -> None:
    text = ("DRY_RUN_PYTHON=/repo/.venv/bin/python\nDRY_RUN_PORT=3002\n"
            "DRY_RUN_ARG=-m\nDRY_RUN_ARG=kohya_dataset_tagger\nDRY_RUN_ARG=--port\nDRY_RUN_ARG=3001\n")
    assert start_arg_problems(parse_dry_run(text)), "the chosen port and the port passed down disagree but nobody complains"


def test_pip_gate_goes_red_on_the_wrong_variant() -> None:
    bad = "DRY_RUN_GPU=cpu\nDRY_RUN_PIP=onnxruntime\nDRY_RUN_INDEX=https://pypi.org/simple\n"
    assert setup_pip_problems(parse_dry_run(bad), want_gpu="cuda", must_have=("onnxruntime-gpu",))
    good = "DRY_RUN_GPU=cuda\nDRY_RUN_PIP=onnxruntime-gpu\nDRY_RUN_INDEX=https://pypi.org/simple\n"
    assert not setup_pip_problems(parse_dry_run(good), want_gpu="cuda", must_have=("onnxruntime-gpu",))


def test_pip_gate_goes_red_on_two_onnxruntime_variants() -> None:
    bad = ("DRY_RUN_GPU=cuda\nDRY_RUN_PIP=onnxruntime\nDRY_RUN_PIP=onnxruntime-gpu\n"
           "DRY_RUN_INDEX=https://pypi.org/simple\n")
    assert setup_pip_problems(parse_dry_run(bad), want_gpu="cuda"), "two variants are installed at once but nobody complains"


def test_pip_gate_goes_red_on_the_wrong_index_for_the_profile() -> None:
    """The index profile is a **contract**: the standard profile mixing in a mirror, or the cn profile putting the official source first, must both go red."""
    pypi_plan = "DRY_RUN_GPU=cpu\nDRY_RUN_PIP=onnxruntime\nDRY_RUN_INDEX=%s\n" % PIP_PYPI
    cn_plan = ("DRY_RUN_GPU=cpu\nDRY_RUN_PIP=onnxruntime\n"
               "DRY_RUN_INDEX=%s\nDRY_RUN_INDEX=%s\n" % (CN_PRIMARY, PIP_PYPI))
    assert setup_pip_problems(parse_dry_run(cn_plan), want_gpu="cpu"), "the standard profile falls back to a domestic mirror but nobody complains"
    assert setup_pip_problems(parse_dry_run(pypi_plan), want_gpu="cpu", want_index=CN_PRIMARY), \
        "the cn profile puts the official source first but nobody complains"
    assert not setup_pip_problems(parse_dry_run(cn_plan), want_gpu="cpu", want_index=CN_PRIMARY)
    assert not setup_pip_problems(parse_dry_run(pypi_plan), want_gpu="cpu")


def test_dry_run_parser_goes_red_on_human_output() -> None:
    with pytest.raises(AssertionError):
        parse_dry_run("==> Kohya Dataset Tagger\nDRY_RUN_PYTHON=/repo/.venv/bin/python\n")
