"""Validate a dataset.toml with the trainer's own config validator and emit **structured JSON**.

Why not just run `python -m library.config_util` and count the log:
its output is a pprint that **wraps at a width** - there are 219 `image_dir=` entries but only
218 are countable in the log (one is wrapped at the end of a line). Using the human-readable
log as a criterion is brittle, so this emits the structured result instead.

Must run in the **trainer's venv** (it needs transformers).

Usage:
    <trainer venv>python tools/verify_toml_with_trainer.py <dataset.toml>

Success -> one line of JSON on stdout: {"ok": true, "subsets": N, "image_dirs": [...]}, exit code 0
Failure -> one line of JSON on stdout: {"ok": false, "error": "..."}, exit code 1
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from local_paths import trainer_root

TRAINER_ROOT = trainer_root()
if TRAINER_ROOT is None:
    print(json.dumps({"ok": False, "error": "no trainer checkout configured: set 'trainer' in local_paths.ini"}))
    raise SystemExit(1)
if not TRAINER_ROOT.is_dir():
    print(json.dumps({"ok": False, "error": "trainer directory does not exist: %s" % TRAINER_ROOT}))
    raise SystemExit(1)

sys.path.insert(0, str(TRAINER_ROOT))

try:
    import library.config_util as cu
    import library.train_util as tu
except Exception as exc:                       # noqa: BLE001
    print(json.dumps({"ok": False, "error": "failed to import the trainer: %s" % exc}))
    raise SystemExit(1)


def main(argv):
    if len(argv) != 2:
        print(json.dumps({"ok": False, "error": "usage: verify_toml_with_trainer.py <dataset.toml>"}))
        return 1
    toml_path = argv[1]

    try:
        parser = argparse.ArgumentParser()
        tu.add_dataset_arguments(parser, True, False, True)
        tu.add_training_arguments(parser, True)
        namespace = parser.parse_args([])
        tu.prepare_dataset_args(namespace, False)

        user_config = cu.load_user_config(toml_path)
        sanitizer = cu.ConfigSanitizer(True, False, False, True)
        sanitizer.sanitize_user_config(user_config)
        blueprint = cu.BlueprintGenerator(sanitizer).generate(user_config, namespace)
    except Exception as exc:                   # noqa: BLE001
        print(json.dumps({"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}))
        return 1

    # Structure (library/config_util.py:143-162):
    #   Blueprint.dataset_group -> DatasetGroupBlueprint.datasets
    #     -> DatasetBlueprint.subsets -> SubsetBlueprint.params.image_dir
    image_dirs = []
    for dataset in blueprint.dataset_group.datasets:
        for subset in dataset.subsets:
            image_dirs.append(subset.params.image_dir)

    print(json.dumps({"ok": True, "subsets": len(image_dirs), "image_dirs": image_dirs}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
