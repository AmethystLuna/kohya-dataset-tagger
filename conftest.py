"""Test bootstrap: put src/ on sys.path and make tests **not read the local config on the dev machine** by default.

Placed at the repository root rather than under test/: pytest loads the rootdir's conftest.py first,
so every test file can `import kohya_dataset_tagger...` directly, with no need to pip install -e . first
and no need for each module to assemble sys.path itself.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent / "src"
if SRC.is_dir() and str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture(autouse=True)
def isolate_local_config_files(monkeypatch: pytest.MonkeyPatch) -> None:
    """By default tests **do not read** the repository root's `roots.txt` / `model_paths.txt`.

    Those two are gitignored **local** files whose contents vary by person and machine. A case that implicitly depends on them
    turns into "green on my machine, red on someone else's". This really happened twice on 2026-09-17:

      1. after writing that machine's two model paths into `model_paths.txt`, **6 cases turned red at once**
         (they assert the **positions** in the search list, and the local file lengthened the list);
      2. an earlier time: `test_extra_models` implicitly depended on that file being empty.

    The approach is to swap both default filenames for names that do not exist, so `_resolve_file()`'s fallback lands on a
    nonexistent path and reads out as an empty list. **Cases that want to test file behavior pass their own** `model_paths_file=` /
    `roots_file=` pointing at tmp_path (that is what `test_roots_api.py` and the §4.11 fixture do).
    """
    from kohya_dataset_tagger import config as _config

    monkeypatch.setattr(_config, "DEFAULT_MODEL_PATHS_FILENAME", ".pytest-no-model-paths.txt")
    monkeypatch.setattr(_config, "DEFAULT_ROOTS_FILENAME", ".pytest-no-roots.txt")
