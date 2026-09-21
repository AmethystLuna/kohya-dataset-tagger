# The sample dataset

A miniature kohya-style training set, committed so that a fresh clone can run the whole unit suite
with no setup at all: `local_paths.ini.example` points `dataset` here, and the criteria that need a
**positive** case (a dataset with subdirectories, images and captions) find one.

    3 subset directories, 6 images (24x24, about 1.4 KB in total), 5 captions
    one image deliberately has no caption   -> 04_beta.png
    one caption uses the escaped-parenthesis convention -> 01_alpha.txt
    cache_text_encoder/ and latent_cache/ inside a subset, plus one at the root
        -> so the filter in core/paths.py (and acceptance A3) is really exercised,
           not merely trusted

It is **read-only for the criteria**: every test that writes copies into `tmp_path` first, and A1
samples a fingerprint before the run and compares it afterwards.

Point `dataset` in `local_paths.ini` at your own training set to run the criteria against real data;
nothing else changes. Regenerate this tree with the snippet in the repository's
`.github/memory/changelog/open-source-readiness.md` if you ever need to.
