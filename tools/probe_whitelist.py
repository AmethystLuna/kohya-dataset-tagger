"""Independently verify that the path allowlist blocks Windows reparse points and same-prefix sibling directories.

Does not rely on the implementer's tests: this creates its own junction and its own same-prefix directory, and asks core.paths directly.
"""
import shutil, subprocess, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from kohya_dataset_tagger.core import paths

base = Path(tempfile.mkdtemp(prefix="ds-whitelist-"))
root = base / "root"
outside = base / "outside"
evil_sibling = base / "root_evil"
for d in (root, outside, evil_sibling):
    d.mkdir()
(outside / "secret.txt").write_text("secret", encoding="utf-8")
(evil_sibling / "secret.txt").write_text("secret", encoding="utf-8")
(root / "ok.png").write_bytes(b"")

paths.configure([root])

def expect_block(label, raw):
    try:
        got = paths.resolve_under_roots(raw)
    except Exception as exc:
        print("  BLOCKED  %-28s %s: %s" % (label, type(exc).__name__, str(exc)[:60]))
        return True
    print("  LEAKED   %-28s -> %s" % (label, got))
    return False

results = []
print("baseline:")
try:
    print("  OK       in-root file ->", paths.resolve_under_roots(root / "ok.png"))
    results.append(True)
except Exception as e:
    print("  FAILED   in-root file should resolve:", e); results.append(False)

print("attacks:")
results.append(expect_block("sibling same-prefix", evil_sibling / "secret.txt"))
results.append(expect_block("parent traversal", root / ".." / "outside" / "secret.txt"))
results.append(expect_block("absolute outside", outside / "secret.txt"))

link = root / "link"
mk = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                    capture_output=True, text=True)
if mk.returncode == 0 and link.exists():
    print("  (junction created: %s -> %s)" % (link, outside))
    print("  (junction really reaches outside:", (link / "secret.txt").exists(), ")")
    results.append(expect_block("junction escape", link / "secret.txt"))
else:
    print("  SKIP     junction not creatable:", (mk.stdout + mk.stderr).strip()[:80])

ok = all(results)
print()
print("RESULT:", "ALL BLOCKED" if ok else "WHITELIST HAS A HOLE")

# Clean up the temp directory we created ourselves (under %TEMP%, does not touch any user data)
shutil.rmtree(base, ignore_errors=True)
raise SystemExit(0 if ok else 1)
