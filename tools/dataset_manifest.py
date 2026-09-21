"""Read-only dataset fingerprint - the "untouched" criterion for acceptance.

Why it is needed
----------------
Acceptance runs against a real dataset (the one local_paths.ini configures, 12.5 GiB),
but **no code path is allowed to write to it**. "We will be careful" is not a criterion, so this hashes
a sorted list of (relative path + size + mtime_ns) into one fingerprint: sample once before and once after the run,
and any mismatch means something wrote to it.

The fingerprint reads only metadata, not file contents - hashing 12.5 GiB of content on every run is unrealistic,
while metadata is enough to catch creation, deletion, renaming and overwriting (size or mtime must change).

Usage
-----
    python tools/dataset_manifest.py --root "<dataset>" --out test/fixtures/<name>.json
    python tools/dataset_manifest.py --root "<dataset>" --check test/fixtures/<name>.json

--check exits with code 1 when the fingerprint differs, and prints the first few differences.
Exit code 2 means the path is unusable.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

#: Fingerprint format version. Change the line format and you must change this, or old and new fingerprints will be misjudged as "inconsistent".
MANIFEST_VERSION = 1


def _iter_files(root: Path):
    """Yield files sorted by relative path, ignoring directories themselves."""
    entries = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            full = Path(dirpath) / name
            try:
                st = full.stat()
            except OSError:
                continue
            entries.append((full.relative_to(root).as_posix(), st.st_size, st.st_mtime_ns))
    entries.sort(key=lambda e: e[0])
    return entries


def build(root: Path) -> dict:
    entries = _iter_files(root)
    digest = hashlib.sha256()
    per_ext: dict[str, list[int]] = {}
    for rel, size, mtime_ns in entries:
        digest.update(("%s\0%d\0%d\n" % (rel, size, mtime_ns)).encode("utf-8"))
        ext = os.path.splitext(rel)[1].lower() or "(none)"
        slot = per_ext.setdefault(ext, [0, 0])
        slot[0] += 1
        slot[1] += size
    return {
        "manifest_version": MANIFEST_VERSION,
        "root": str(root),
        "file_count": len(entries),
        "total_bytes": sum(e[1] for e in entries),
        "manifest_sha256": digest.hexdigest(),
        "per_extension": {k: {"count": v[0], "bytes": v[1]} for k, v in sorted(per_ext.items())},
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Read-only dataset fingerprint")
    ap.add_argument("--root", required=True, type=Path)
    ap.add_argument("--out", type=Path, help="write the fingerprint to this JSON")
    ap.add_argument("--check", type=Path, help="compare against the fingerprint in this JSON")
    args = ap.parse_args(argv)

    root = args.root
    if not root.is_dir():
        print("Dataset directory unusable: %s" % root, file=sys.stderr)
        return 2

    current = build(root)

    if args.check:
        expected = json.loads(args.check.read_text(encoding="utf-8"))
        if expected.get("manifest_sha256") == current["manifest_sha256"]:
            print("OK  fingerprint matches  files=%d bytes=%d sha256=%s"
                  % (current["file_count"], current["total_bytes"], current["manifest_sha256"][:16]))
            return 0
        print("FAIL dataset has been modified", file=sys.stderr)
        for key in ("file_count", "total_bytes", "manifest_sha256"):
            if expected.get(key) != current.get(key):
                print("  %s: expected %s actual %s" % (key, expected.get(key), current.get(key)), file=sys.stderr)
        return 1

    print(json.dumps(current, indent=2, ensure_ascii=False))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(current, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print("wrote %s" % args.out, file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
