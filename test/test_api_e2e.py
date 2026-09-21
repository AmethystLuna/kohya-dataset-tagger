"""HTTP end to end: the assembly layer + the captions/tags/autotag route groups (p0-spec §4, §4.1-§4.4).

Why test the **really assembled app**
------------------------------------
The assembly layer's easiest mistakes only show up at the app level:
- the allowlist is not published to `core.paths` → every request gets 403 (calling only `config.load()` does not help);
- the router prefix is added a second time → `/api/api/...`;
- the wrong `.js` MIME for static assets → the ES module fails to load.

Read-only principle
-------------------
- Read endpoints hit the configured dataset (`local_paths.ini`) — **read-only**, with the thumbnail cache in tmp;
- every write operation (PUT / batch / run / cache invalidate) happens on a copy under `tmp_path`;
- `/api/autotag/preview`'s read-only property is backstopped by a separate subprocess fingerprint
  criterion (`tools/dataset_manifest.py --check`).
"""
from __future__ import annotations

import contextlib
import http.server
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import local_paths
from kohya_dataset_tagger import __version__, config
from kohya_dataset_tagger.app import create_app
from kohya_dataset_tagger.core import paths

REPO = Path(__file__).resolve().parents[1]
#: The dataset and the model roots come from `local_paths.ini` (gitignored; the committed template
#: points at the sample dataset inside this repository). A criterion that reads real data says so and
#: skips when there is none, instead of turning green on an empty directory.
DATA = local_paths.dataset_root()
HAS_DATASET = DATA.is_dir()
MANIFEST_TOOL = REPO / "tools" / "dataset_manifest.py"
MODEL_ROOTS = local_paths.model_roots()

TE_CACHE_DIRNAME = "cache_text_encoder"

#: The §4.5 ① translation table (CSV category numeric string → category name). Same source as core.vocab.CATEGORY_NAMES.
CATEGORY_NAMES = {"0": "General", "1": "Artist", "3": "Copyright", "4": "Character", "5": "Meta", "9": "Rating"}
MODEL_ID = "wd-swinv2-tagger-v3"
CAPTION_EXT = ".txt"

#: The §4 frozen route table + §4.4's cancel + §4.9's export + §4.11's runtime path configuration.
#: (19 paths / 24 methods; this set makes only a **subset** assertion, so extra registered routes are not a failure.)
EXPECTED_ROUTES = {
    ("GET", "/api/health"),
    ("GET", "/api/roots"),
    ("POST", "/api/roots"),
    ("DELETE", "/api/roots"),
    ("GET", "/api/fs/list"),
    ("GET", "/api/images/thumb"),
    ("GET", "/api/images/meta"),
    ("GET", "/api/captions"),
    ("PUT", "/api/captions"),
    ("POST", "/api/captions/batch"),
    ("POST", "/api/captions/read"),
    ("GET", "/api/tags/frequency"),
    ("GET", "/api/tags/autocomplete"),
    ("GET", "/api/autotag/models"),
    ("GET", "/api/autotag/model-roots"),
    ("POST", "/api/autotag/model-roots"),
    ("DELETE", "/api/autotag/model-roots"),
    ("POST", "/api/autotag/preview"),
    ("POST", "/api/autotag/run"),
    ("POST", "/api/autotag/download"),
    ("GET", "/api/autotag/stream"),
    ("POST", "/api/autotag/cancel"),
    ("GET", "/api/cache/status"),
    ("POST", "/api/cache/invalidate"),
    ("POST", "/api/dataset/toml"),
}

#: The §4.1 frozen defaults: **all** 0.35 (no higher values for character/copyright), remove_underscore defaults to True.
THRESHOLDS = {"general": 0.35, "character": 0.35, "copyright": 0.35, "artist": 0.35, "meta": 0.35}
POSTPROCESS = {
    "remove_underscore": True,
    "escape_parens": True,
    "undesired_tags": [],
    "always_first_tags": [],
    "character_tags_first": False,
    "character_tag_expand": False,
    "rating": "none",
}

#: The fields of each /api/autotag/models entry frozen by §4 + §4.7 (the old fields + the download foursome).
MODEL_FIELDS = {
    "id", "available", "installed", "size_bytes", "download_size_bytes", "downloadable",
    "onnx_path", "vocab_path", "description",
}
#: The SSE event fields frozen by §4.3 + §4.7 (phase was added in §4.7 and tagging events must carry it too).
PROGRESS_FIELDS = {"phase", "done", "total", "current", "errors"}
DONE_FIELDS = {"phase", "done", "total", "finished", "errors"}


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


def _model_roots() -> list[str]:
    """Exactly what `local_paths.ini` configures - no machine path, no hidden fallback."""
    return [str(path) for path in MODEL_ROOTS]


@pytest.fixture(autouse=True)
def _restore_global_config():
    """Restore the process-level allowlist/cache directory after each test so it does not pollute other test files (config is a global singleton)."""
    before = config.current()
    roots = list(paths.get_roots())
    yield
    config.load(
        roots=roots,
        thumb_cache_dir=before.thumb_cache_dir,
        model_search_roots=list(before.model_search_roots),
        host=before.host,
        port=before.port,
    )


@pytest.fixture(scope="session")
def thumb_dir(tmp_path_factory):
    """Thumbnail cache directory: must be outside the dataset (§3.4 / hard boundary 3)."""
    return tmp_path_factory.mktemp("thumbs")


def _sample_subdirs() -> list[Path]:
    return paths.list_subdirs(DATA)


def _sample_image(require_caption: bool = True) -> Path:
    """One image from the real dataset (optionally requiring a caption). Read-only."""
    for directory in _sample_subdirs():
        for image in paths.iter_images(directory):
            if not require_caption or image.with_suffix(CAPTION_EXT).is_file():
                return image
    raise AssertionError("no sample image found in the dataset: %s" % DATA)

def _copy_sample_image(post: Path) -> str:
    """Put one captioned image named `sample.*` into `post`; return its file name.

    Normally it is one image copied out of the real read-only test set. Without that set the image
    is **synthesized** instead: the criteria built on the `workspace` fixture are about the write
    path (caption write -> cache invalidation -> read back, batch operations, previews), and none
    of them looks at the picture. Skipping them on every machine but one would cost real cover.
    """
    if not HAS_DATASET:
        name = "sample.jpeg"
        Image.new("RGB", (96, 64), (200, 40, 40)).save(post / name)
        (post / ("sample" + CAPTION_EXT)).write_text("1girl, solo, long hair", encoding="utf-8")
        return name
    sample_src = _sample_image(require_caption=True)
    shutil.copy2(sample_src, post / sample_src.name)
    shutil.copy2(sample_src.with_suffix(CAPTION_EXT), post / (sample_src.stem + CAPTION_EXT))
    return sample_src.name


@pytest.fixture()
def dataset_client(thumb_dir):
    """A client over the real dataset (read-only).

    With no local test set (KOHYA_TAGGER_TEST_DATASET) every criterion that reads **real data**
    skips: its subject is the content of a real dataset, so a synthetic stand-in would not be the
    same criterion. The criteria that only need "some root" use plain tmp_path and keep running.
    """
    if not HAS_DATASET:
        pytest.skip("no read-only test set at %s (set KOHYA_TAGGER_TEST_DATASET)" % DATA)
    config.load(roots=[DATA], thumb_cache_dir=thumb_dir, model_search_roots=_model_roots())
    application = create_app()
    assert application.state.router_errors == [], application.state.router_errors
    with TestClient(application) as client:
        yield client


@pytest.fixture()
def workspace(tmp_path: Path):
    """A mini dataset in tmp: 1 copy of a real image (with caption) + 12 synthetic images. Writes happen only here."""
    root = tmp_path / "dataset"
    post = root / "1_post"
    post.mkdir(parents=True)
    sample_name = _copy_sample_image(post)
    for index in range(1, 13):
        color = (index * 17 % 256, 40, 200)
        Image.new("RGB", (64, 64), color).save(post / ("synth%02d.png" % index))

    cache = tmp_path / "thumbs"
    config.load(roots=[root], thumb_cache_dir=cache, model_search_roots=_model_roots())
    with TestClient(create_app()) as client:
        yield SimpleNamespace(
            client=client,
            root=root,
            post=post,
            cache=cache,
            sample=post / sample_name,
            synth=sorted(post.glob("synth*.png")),
        )


def _require_onnxruntime() -> None:
    """Real inference needs §7's onnxruntime. When the environment lacks it, **skip** (and say why) rather than pretending to pass.

    2026-09-17 19:36 onnxruntime briefly vanished from this venv (no such package in `pip list`),
    and every real-model case turned into a 500 `model_load_failed`—that was an environment problem,
    not an interface regression.
    """
    pytest.importorskip("onnxruntime", reason="the environment has no onnxruntime (a runtime dependency of p0-spec §7)")

def _require_vocabulary(client) -> None:
    """The autocomplete criteria need a **vocabulary**, which comes from an available tagger model.

    Without one the endpoint answers an empty list - correct behaviour, not a defect - so these
    criteria skip and say why instead of reddening a machine that has no model downloaded.
    """
    models = {model["id"]: model for model in client.get("/api/autotag/models").json()["models"]}
    if not any(entry["available"] for entry in models.values()):
        pytest.skip("no tagger model, and so no vocabulary, under the configured roots %s" % (MODEL_ROOTS,))


def _require_local_model(client) -> None:
    """Real inference needs a tagger model **on this machine** (the stub_tagger cases do not).

    Without one the run endpoints answer 409 `model_unavailable`, so the criteria that assert on a
    real run skip instead of reddening a machine that simply has no model downloaded.
    """
    models = {model["id"]: model for model in client.get("/api/autotag/models").json()["models"]}
    if not models[MODEL_ID]["available"]:
        pytest.skip("this machine has no %s (model search roots %s)" % (MODEL_ID, _model_roots()))


def _manifest_run(*args: str) -> subprocess.CompletedProcess:
    """Run the read-only fingerprint tool once (§0.4 / A1).

    The criterion samples "before the run" with --out and compares "after the run" against **the
    same file** with --check—**not** against the 2026-09 snapshot in the repository: the dataset is
    alive, and a user adding images or fixing captions is legitimate, so the snapshot goes stale
    while the invariant (this piece of code did not write the dataset) holds for any data state.
    """
    return subprocess.run(
        [sys.executable, str(MANIFEST_TOOL), "--root", str(DATA), *args],
        capture_output=True,
        text=True,
        # Explicit UTF-8: the default follows the locale, and subprocess's reader thread raises
        # PytestUnhandledThreadExceptionWarning on non-UTF-8 output, which can turn into a failure on
        # another machine.
        encoding="utf-8",
        errors="replace",
    )


def _read_stream(client: TestClient, job_id: str) -> list[tuple[str, dict]]:
    """Read the SSE stream to the end (the server closes it itself once `done` is sent)."""
    events: list[tuple[str, dict]] = []
    name = ""
    with client.stream("GET", "/api/autotag/stream", params={"job_id": job_id}) as response:
        assert response.status_code == 200, response.read()[:300]
        assert response.headers["content-type"].startswith("text/event-stream")
        for line in response.iter_lines():
            if line.startswith("event: "):
                name = line[len("event: "):].strip()
            elif line.startswith("data: "):
                events.append((name, json.loads(line[len("data: "):])))
    return events


def _run_payload(images: list[Path], write_mode: str) -> dict:
    return {
        "images": [str(image) for image in images],
        "model": MODEL_ID,
        "thresholds": dict(THRESHOLDS),
        "postprocess": dict(POSTPROCESS),
        "write_mode": write_mode,
    }


def _assert_frozen_error(response, status: int, code: str) -> None:
    assert response.status_code == status, response.text[:300]
    body = response.json()
    assert set(body) == {"error"}, body
    # 2026-09-19: the error shape gained params (§4 addendum); the old fields are unchanged word for word.
    assert set(body["error"]) == {"code", "message", "params"}, body
    assert isinstance(body["error"]["params"], dict)
    assert body["error"]["code"] == code, body


# ---------------------------------------------------------------------------
# Assembly layer: dynamic discovery, allowlist publication, static assets
# ---------------------------------------------------------------------------


def test_dynamic_discovery_registers_every_frozen_route(dataset_client) -> None:
    schema = dataset_client.app.openapi()["paths"]
    registered = {(method.upper(), path) for path, ops in schema.items() for method in ops}
    missing = sorted(EXPECTED_ROUTES - registered)
    assert not missing, "app.py did not register these routes: %s" % missing
    assert not [path for path in schema if path.startswith("/api/api")], "the router prefix was added twice"


def test_router_modules_are_discovered(dataset_client) -> None:
    assert set(dataset_client.app.state.router_modules) >= {
        "fs", "images", "captions", "tags", "autotag", "dataset", "roots",
    }


def test_static_assets_use_module_mime(dataset_client) -> None:
    """§6 hard convention: `web/` is mounted at `/`, and `.js` must be text/javascript (otherwise the ES module does not load)."""
    index = dataset_client.get("/")
    assert index.status_code == 200
    assert index.headers["content-type"].startswith("text/html")
    assert "<html" in index.text.lower()

    script = dataset_client.get("/js/api.js")
    assert script.status_code == 200
    assert script.headers["content-type"].split(";")[0].strip() == "text/javascript"
    assert "ROUTES" in script.text

    css = dataset_client.get("/css/app.css")
    assert css.status_code == 200
    assert css.headers["content-type"].split(";")[0].strip() == "text/css"


def test_static_assets_are_revalidated_so_a_reload_picks_up_changes(dataset_client) -> None:
    """`web/` has no build step: after changing CSS/JS a plain refresh must pick it up.

    Starlette's `StaticFiles` sends only `ETag`/`Last-Modified`, not `Cache-Control`; the browser
    then caches by heuristic freshness, so after a change F5 still uses the old styles—reproduced
    with CDP on 2026-09-18 (swap app.css for an old version and roll back its mtime; the computed
    style after refresh is still the old one). `no-cache` means "revalidate at the origin before
    use", not "do not cache": unchanged → 304.
    """
    for path in ("/", "/css/app.css", "/js/app.js"):
        response = dataset_client.get(path)
        assert response.status_code == 200, path
        assert response.headers.get("cache-control") == "no-cache", (
            "%s lacks Cache-Control: no-cache; the browser caches heuristically and a refresh after a change picks up the old file" % path
        )


def test_fail_closed_when_no_root_is_published(tmp_path: Path) -> None:
    """When the allowlist is not published every path request is 403—the symptom of "constructing a Config without loading it".

    2026-09-19: the empty allowlist has its own stable code `paths_unconfigured` (no longer the
    generic `outside_roots`); it is a fixed localizable phrase (§4.9 addendum 4). The 403 itself is
    unchanged.
    """
    config.load(roots=[], thumb_cache_dir=tmp_path / "thumbs", model_search_roots=[])
    with TestClient(create_app()) as client:
        assert client.get("/api/roots").json() == {"roots": []}
        _assert_frozen_error(client.get("/api/fs/list"), 403, "paths_unconfigured")
        _assert_frozen_error(client.get("/api/tags/frequency"), 403, "paths_unconfigured")


# ---------------------------------------------------------------------------
# 1. Read-only endpoints: 2xx + §4 shapes (real dataset)
# ---------------------------------------------------------------------------


def test_health_and_roots(dataset_client) -> None:
    health = dataset_client.get("/api/health")
    assert health.status_code == 200
    body = health.json()
    # The two fields frozen by §4 stay where they are (adding fields is not breaking: an old frontend reads only the first two)
    assert body["ok"] is True and body["version"] == __version__
    # §4.12 addendum: the frontend uses this to decide whether the shape selector is shown or greyed out
    assert set(body["path_config"]) == {"allowed", "host", "reason"}

    roots = dataset_client.get("/api/roots")
    assert roots.status_code == 200
    body = roots.json()
    assert set(body) == {"roots"}
    assert [Path(item["path"]).resolve() for item in body["roots"]] == [DATA.resolve()]
    # §4.11 added three markers to {path,name}: the UI must be able to show "is this still on disk / was it remembered"
    assert set(body["roots"][0]) == {"path", "name", "exists", "is_dir", "persisted"}
    assert body["roots"][0]["name"] == DATA.name
    assert body["roots"][0]["exists"] is True and body["roots"][0]["is_dir"] is True


def test_fs_list_shape_and_has_caption(dataset_client) -> None:
    response = dataset_client.get("/api/fs/list", params={"path": str(DATA)})
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"path", "parent", "dirs", "images", "counts"}
    assert Path(body["path"]).resolve() == DATA.resolve()
    assert body["parent"] is None, "the allowlist boundary must not hand out a parent directory"
    assert body["dirs"], "the configured dataset has no subdirectory at all: %s" % DATA
    # §4.13 added preview_images to dirs entries (a 3x3 sample, at most 9)
    assert set(body["dirs"][0]) == {"name", "path", "image_count", "preview_images"}
    assert body["images"] == [], "the trainer's glob_images does not recurse (§1.1)"
    assert body["counts"] == {"images": 0, "dirs": len(body["dirs"]), "captions": 0, "missing_captions": 0}

    # The first entry of an arbitrary dataset may be empty; take one that carries images.
    subdir = Path(next(item["path"] for item in body["dirs"] if item["image_count"]))
    inner = dataset_client.get("/api/fs/list", params={"path": str(subdir)}).json()
    assert inner["images"], "the subdirectory should have images"
    for item in inner["images"]:
        assert set(item) == {"name", "path", "size", "mtime", "has_caption"}, "§4.2 frozen fields"
        assert isinstance(item["has_caption"], bool)
        assert Path(item["path"]).with_suffix(CAPTION_EXT).is_file() is item["has_caption"]
    counts = inner["counts"]
    assert counts["captions"] + counts["missing_captions"] == counts["images"] == len(inner["images"])


def test_images_thumb_and_meta(dataset_client, thumb_dir) -> None:
    image = _sample_image(require_caption=False)
    thumb = dataset_client.get("/api/images/thumb", params={"path": str(image), "size": "grid"})
    assert thumb.status_code == 200
    assert thumb.headers["content-type"] == "image/webp"
    assert thumb.content[:4] == b"RIFF" and thumb.content[8:12] == b"WEBP"

    meta = dataset_client.get("/api/images/meta", params={"path": str(image)})
    assert meta.status_code == 200
    body = meta.json()
    assert set(body) == {
        "path", "width", "height", "display_width", "display_height", "bytes", "mtime", "mode", "has_alpha",
    }
    assert body["width"] > 0 and body["height"] > 0 and body["bytes"] > 0
    # Section 4.17: the same size twice for an image without an EXIF orientation, and the crop
    # dialog measures against the display one (see test_crop_api for the rotated case).
    assert (body["display_width"], body["display_height"]) == (body["width"], body["height"])
    assert body["mode"]
    assert isinstance(body["has_alpha"], bool)
    assert thumb_dir.is_dir(), "the thumbnail cache must live outside the dataset (and really be used)"
    assert list(thumb_dir.glob("*.webp")), "the thumbnail cache directory has no files"


def test_captions_get_shape_matches_trainer_split(dataset_client) -> None:
    image = _sample_image(require_caption=True)
    response = dataset_client.get("/api/captions", params={"path": str(image)})
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"image", "caption_path", "exists", "text", "tags", "multiline_warning"}
    assert Path(body["image"]) == image
    assert Path(body["caption_path"]) == image.with_suffix(CAPTION_EXT)
    assert body["exists"] is True
    assert body["multiline_warning"] is False, "the captions in the dataset are single-line"
    # Word for word the same as the trainer's rule (§3.2): [t.strip() for t in text.strip().split(",") if t.strip()]
    assert body["tags"] == [part.strip() for part in body["text"].strip().split(",") if part.strip()]
    assert len(body["tags"]) > 1


def test_tags_frequency_shape(dataset_client) -> None:
    subdir = next(directory for directory in _sample_subdirs() if paths.iter_images(directory))
    response = dataset_client.get("/api/tags/frequency", params={"path": str(subdir)})
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"total_images", "tags"}
    assert body["total_images"] == len(paths.iter_images(subdir))
    assert body["tags"], "captions in a real post directory should have tags"
    for entry in body["tags"]:
        assert set(entry) == {"tag", "count"}
        assert entry["count"] >= 1 and entry["tag"]
    counts = [entry["count"] for entry in body["tags"]]
    assert counts == sorted(counts, reverse=True), "the frequency table is sorted by count descending"


def test_tags_autocomplete_shape(dataset_client) -> None:
    _require_vocabulary(dataset_client)
    response = dataset_client.get("/api/tags/autocomplete", params={"q": "1girl", "limit": 5})
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"suggestions"}
    suggestions = body["suggestions"]
    assert 1 <= len(suggestions) <= 5
    for item in suggestions:
        assert set(item) == {"tag", "category", "category_id", "count"}   # §4.5 ①
        assert "1girl" in item["tag"].lower(), "§4.10: substring match (no longer prefix)"
    # §4.10 ordering: prefix hits **as a whole block** come before substring hits; only within a group is it count-descending
    prefix_hits = [item for item in suggestions if item["tag"].lower().startswith("1girl")]
    others = [item for item in suggestions if not item["tag"].lower().startswith("1girl")]
    assert prefix_hits, "1girl is itself a prefix hit"
    assert suggestions[: len(prefix_hits)] == prefix_hits, "prefix hits must come first: %r" % suggestions
    for group in (prefix_hits, others):
        counts = [item["count"] for item in group]
        assert counts == sorted(counts, reverse=True), "within a group, count-descending: %r" % counts
    assert suggestions[0]["tag"] == "1girl"
    assert suggestions[0]["category"] == "General", "§4.5 ①: category is a category name, not a numeric code"
    assert suggestions[0]["category_id"] == "0"

    # Empty q = the hottest few (used when the frontend input gains focus)
    warm = dataset_client.get("/api/tags/autocomplete", params={"q": "", "limit": 3}).json()
    assert len(warm["suggestions"]) == 3


def test_autocomplete_category_name_and_id_stay_in_sync(dataset_client) -> None:
    """§4.5 ①: `category` = the category name (displayed directly by the frontend), `category_id` = the CSV numeric string.

    Both fields must come from the same translation table—otherwise the frontend's dropdown and the
    vocabulary would each say their own thing.
    """
    _require_vocabulary(dataset_client)
    suggestions = dataset_client.get("/api/tags/autocomplete", params={"q": "", "limit": 200}).json()["suggestions"]
    assert len(suggestions) == 200
    seen_categories = set()
    for item in suggestions:
        assert item["category_id"].isdigit(), "category_id must be the CSV numeric string: %r" % item
        assert not item["category"].isdigit(), "category must be a category name, not a numeric code: %r" % item
        assert CATEGORY_NAMES.get(item["category_id"], item["category_id"]) == item["category"], item
        seen_categories.add(item["category"])
    assert "General" in seen_categories, "the hottest 200 tags should include the General category"


# ---------------------------------------------------------------------------
# 1b. §4.10 autocomplete matching semantics: case-insensitive substring match (synthetic vocabulary, fully controlled)
# ---------------------------------------------------------------------------


#: Synthetic vocabulary: count / prefix / case are all controlled, so the semantic cases do not depend on this machine's WD14 CSV contents.
AUTOCOMPLETE_ROWS = (
    ("1girl", "0", 5113288),                 # hottest when q=""
    ("long_hair", "0", 5000),                # a **substring** hit for "hair", but hotter than any hair*
    ("genshin_impact", "3", 3000),           # a **prefix** hit for "genshin"
    ("ganyu_(genshin_impact)", "4", 1200),   # a substring hit (the §4.10 phenomenon)
    ("hu_tao_(genshin_impact)", "4", 1100),
    ("hair_ornament", "0", 900),             # a prefix hit for "hair"
    ("hair_between_eyes", "0", 800),
    ("hair_ribbon", "0", 700),
)


@pytest.fixture()
def autocomplete_client(tmp_path: Path, monkeypatch):
    """Swap the vocabulary for a synthetic CSV and then start the app: verify semantics only, not bound by the real vocabulary's contents."""
    from kohya_dataset_tagger.api import tags as tags_api
    from kohya_dataset_tagger.core import vocab as vocab_module

    csv_path = tmp_path / "selected_tags.csv"
    rows = ["tag_id,name,category,count"]
    rows += [
        "%d,%s,%s,%d" % (index, name, category, count)
        for index, (name, category, count) in enumerate(AUTOCOMPLETE_ROWS)
    ]
    csv_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    monkeypatch.setattr(tags_api, "get_vocabulary", lambda: vocab_module.Vocabulary.from_csv(csv_path))

    config.load(roots=[tmp_path], thumb_cache_dir=tmp_path / "thumbs", model_search_roots=[])
    with TestClient(create_app()) as client:
        yield client


def _suggest(client: TestClient, q: str, limit: int = 50) -> list:
    response = client.get("/api/tags/autocomplete", params={"q": q, "limit": limit})
    assert response.status_code == 200, response.text[:300]
    return response.json()["suggestions"]


def test_autocomplete_matches_substrings(autocomplete_client) -> None:
    """§4.10: genshin must find character tags hit **in the middle** such as ganyu_(genshin_impact)."""
    tags = [item["tag"] for item in _suggest(autocomplete_client, "genshin")]
    assert tags == ["genshin_impact", "ganyu_(genshin_impact)", "hu_tao_(genshin_impact)"], tags
    # The hit is in the middle ("genshin" is preceded by "(" and followed by "_", i.e. the shape after \(_ that §4.10 talks about)
    middle = [item["tag"] for item in _suggest(autocomplete_client, "(genshin_impact)")]
    assert middle == ["ganyu_(genshin_impact)", "hu_tao_(genshin_impact)"], middle
    assert _suggest(autocomplete_client, "nothing-matches-this") == []


def test_autocomplete_puts_prefix_hits_first(autocomplete_client) -> None:
    """§4.10 ordering: prefix hits > count descending > name lexicographic (a prefix hit can beat a hotter substring hit)."""
    items = _suggest(autocomplete_client, "hair")
    assert [item["tag"] for item in items] == [
        "hair_ornament", "hair_between_eyes", "hair_ribbon", "long_hair",
    ], items
    # count is not globally descending—this proves it is "group by prefix first, then sort by heat"
    assert [item["count"] for item in items] == [900, 800, 700, 5000]


def test_autocomplete_is_case_insensitive(autocomplete_client) -> None:
    """Case-insensitive (the vocabulary is all lowercase; the user may type any shape)."""
    lower = _suggest(autocomplete_client, "genshin")
    for query in ("GENSHIN", "Genshin", "gEnShIn"):
        assert _suggest(autocomplete_client, query) == lower, query


def test_autocomplete_is_deterministic_and_respects_limit(autocomplete_client) -> None:
    """Two identical queries give exactly the same result; limit truncates **after** sorting."""
    first = _suggest(autocomplete_client, "genshin")
    assert _suggest(autocomplete_client, "genshin") == first, "two identical queries must give the same result"
    assert [item["tag"] for item in _suggest(autocomplete_client, "genshin", limit=2)] == [
        "genshin_impact", "ganyu_(genshin_impact)",
    ]
    assert _suggest(autocomplete_client, "genshin", limit=1) == first[:1]


def test_autocomplete_empty_query_keeps_the_old_behaviour(autocomplete_client) -> None:
    """q="" = the hottest few in the vocabulary (count descending / same count by name), limit applies—no regression."""
    items = _suggest(autocomplete_client, "", limit=3)
    assert [item["tag"] for item in items] == ["1girl", "long_hair", "genshin_impact"], items
    counts = [item["count"] for item in items]
    assert counts == sorted(counts, reverse=True)
    assert _suggest(autocomplete_client, "   ", limit=2) == items[:2], "only whitespace == empty query"


def test_autocomplete_genshin_on_the_real_vocabulary(dataset_client) -> None:
    """The §4.10 phenomenon itself (the real WD14 vocabulary): q=genshin must find ganyu_(genshin_impact).

    The precondition check **reads the raw CSV** directly, bypassing the autocomplete path under test—otherwise a
    broken implementation would become "skipped" instead of "failed".
    """
    from kohya_dataset_tagger.api import tags as tags_api

    path = tags_api.vocabulary_path()
    if path is None:
        pytest.skip("no usable vocabulary on this machine (model search roots %s)" % _model_roots())
    raw = path.read_text(encoding="utf-8-sig", errors="replace")
    if "ganyu_(genshin_impact)" not in raw:
        pytest.skip("this machine's vocabulary has no ganyu_(genshin_impact), cannot verify the §4.10 phenomenon")

    tags = [item["tag"] for item in _suggest(dataset_client, "genshin", limit=200)]
    assert "ganyu_(genshin_impact)" in tags, "§4.10: substring matching is not taking effect"
    assert "hu_tao_(genshin_impact)" in tags, tags
    prefix = [tag for tag in tags if tag.lower().startswith("genshin")]
    assert tags[: len(prefix)] == prefix, "prefix hits must come as a block first: %r" % tags

    # In the real vocabulary hair* prefix hits are much colder than long_hair — the ordering rule is
    # easiest to see here. Measured (2026-09-17, wd-swinv2-tagger-v3's 10861 rows): q=hair has 265
    # hits in total, 51 of them prefix hits, all at the very front; long_hair (the hottest substring
    # hit) comes after them.
    hair = [item["tag"] for item in _suggest(dataset_client, "hair", limit=200)]
    hair_prefix = [tag for tag in hair if tag.lower().startswith("hair")]
    assert hair_prefix, "the vocabulary should have hair* prefix-hit tags"
    assert hair[: len(hair_prefix)] == hair_prefix, "prefix hits must come as a block first: %r" % hair[:60]
    rest = hair[len(hair_prefix):]
    if rest:  # when prefix hits fill the whole page this criterion is meaningless (the real vocabulary is 51/265, so it will not)
        assert all(not tag.lower().startswith("hair") for tag in rest), rest[:10]
        assert "long_hair" in rest, "long_hair is the hottest substring hit and must come after the prefix hits: %r" % rest[:10]


def test_autotag_models_marks_swinv2_available(dataset_client) -> None:
    """Hard convention 6: `wd-swinv2-tagger-v3` must be listed as available; §4.7's download trio is pinned down item by item."""
    response = dataset_client.get("/api/autotag/models")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"models"}
    by_id = {model["id"]: model for model in body["models"]}
    for model in body["models"]:
        assert set(model) == MODEL_FIELDS, "§4.7 froze the fields of each models entry"
        assert isinstance(model["available"], bool)
        assert isinstance(model["installed"], bool)
        assert isinstance(model["size_bytes"], int) and model["size_bytes"] >= 0
        assert isinstance(model["download_size_bytes"], int) and model["download_size_bytes"] >= 0
        assert isinstance(model["downloadable"], bool)
        # §4.7: installed and available share one source (both from the registry's resolution result)
        assert model["installed"] is model["available"]
        if not model["installed"]:
            assert model["size_bytes"] == 0, "an uninstalled model's size_bytes must be 0"
            assert model["onnx_path"] is None and model["vocab_path"] is None
    assert MODEL_ID in by_id, "the built-in model table has no %s: %s" % (MODEL_ID, sorted(by_id))
    entry = by_id[MODEL_ID]
    if not entry["available"]:
        pytest.skip("this machine has no %s (model search roots %s)" % (MODEL_ID, _model_roots()))
    assert Path(entry["onnx_path"]).is_file()
    assert Path(entry["vocab_path"]).is_file()
    assert entry["description"]
    # §4.7: size_bytes = the byte count of the local ONNX (a GET makes no network request, see the fake-endpoint case in section 7)
    assert entry["size_bytes"] == Path(entry["onnx_path"]).stat().st_size
    assert entry["size_bytes"] > 0
    # Static size table: measured ones are non-zero, unmeasured ones must be 0 (never fill in from memory)
    assert by_id["wd-eva02-large-tagger-v3"]["download_size_bytes"] == 1260435999
    assert by_id["wd-convnext-tagger-v3"]["download_size_bytes"] == 0, "an unmeasured model must stay 0"
    # The installed one is the file in the repository: the static table and the local size should agree (a mismatch = stale table, re-measure)
    assert entry["download_size_bytes"] == entry["size_bytes"], \
        "%s's static download size does not match the local file: did the repository change revision? Re-measure DOWNLOAD_SIZE_BYTES" % MODEL_ID


def test_cache_status_shape(dataset_client) -> None:
    response = dataset_client.get("/api/cache/status", params={"path": str(DATA)})
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"stale"}
    for entry in body["stale"]:
        assert set(entry) == {"image", "caption", "cache"}


# ---------------------------------------------------------------------------
# 2. Out of bounds is always 403
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("route,params", [
    ("/api/fs/list", {"path": r"C:\Windows"}),
    ("/api/images/thumb", {"path": "../../etc/passwd"}),
    ("/api/images/meta", {"path": r"C:\Windows\win.ini"}),
    ("/api/captions", {"path": r"C:\Windows\win.ini"}),
    ("/api/cache/status", {"path": r"C:\Windows"}),
    ("/api/tags/frequency", {"path": r"C:\Windows"}),
])
def test_out_of_root_queries_are_403(dataset_client, route: str, params: dict) -> None:
    _assert_frozen_error(dataset_client.get(route, params=params), 403, "outside_roots")


@pytest.mark.parametrize("route,payload", [
    ("/api/autotag/preview", {"images": [r"C:\Windows\win.ini"], "model": MODEL_ID}),
    ("/api/autotag/run", {"images": [r"C:\Windows\win.ini"], "model": MODEL_ID}),
    ("/api/cache/invalidate", {"images": [r"C:\Windows\win.ini"]}),
])
def test_out_of_root_body_paths_are_403(dataset_client, route: str, payload: dict) -> None:
    _assert_frozen_error(dataset_client.post(route, json=payload), 403, "outside_roots")


def test_validation_and_unknown_route_use_the_frozen_shape(dataset_client) -> None:
    _assert_frozen_error(dataset_client.post("/api/autotag/preview", json={}), 422, "validation_error")
    _assert_frozen_error(dataset_client.get("/api/definitely-not-a-route"), 404, "not_found")


# ---------------------------------------------------------------------------
# 3. PUT /api/captions on a tmp copy: the end-to-end version of invariant number one
# ---------------------------------------------------------------------------


def test_put_caption_writes_and_removes_te_cache(workspace) -> None:
    image = workspace.sample
    cache_dir = workspace.post / TE_CACHE_DIRNAME
    cache_dir.mkdir(exist_ok=True)
    safetensors = cache_dir / (image.stem + "_anima_te.safetensors")
    npz = cache_dir / (image.stem + "_anima_te.npz")
    safetensors.write_bytes(b"stale")
    npz.write_bytes(b"stale")

    response = workspace.client.put("/api/captions", json={"image": str(image), "tags": ["1girl", "solo"]})
    assert response.status_code == 200, response.text[:400]
    body = response.json()
    assert set(body) == {"caption_path", "changed", "cache_removed", "cache_remove_failed", "multiline_warning"}
    assert Path(body["caption_path"]) == image.with_suffix(CAPTION_EXT)
    assert body["changed"] is True
    assert body["cache_remove_failed"] == [], "a successful write should have no removal failures"
    assert set(body["cache_removed"]) == {str(safetensors), str(npz)}
    # Invariant number one: the end-to-end version
    assert not safetensors.exists() and not npz.exists(), "the TE cache must be gone after PUT"
    assert image.with_suffix(CAPTION_EXT).read_text(encoding="utf-8") == "1girl, solo"

    # Content unchanged: no write, no cache removal, changed=False
    again = workspace.client.put("/api/captions", json={"image": str(image), "tags": ["1girl", "solo"]})
    assert again.status_code == 200
    assert again.json()["changed"] is False

    # Read back matches the trainer's rule
    got = workspace.client.get("/api/captions", params={"path": str(image)}).json()
    assert got["tags"] == ["1girl", "solo"] and got["exists"] is True


def test_put_caption_text_mode_multiline_and_rejections(workspace) -> None:
    image = workspace.sample
    text = "1girl, solo\nsecond line is not trained"
    body = workspace.client.put("/api/captions", json={"image": str(image), "text": text}).json()
    assert body["multiline_warning"] is True
    assert image.with_suffix(CAPTION_EXT).read_text(encoding="utf-8") == text

    # A tag containing a comma must be rejected (§3.2 / A6): once written the trainer would read it as two tags
    _assert_frozen_error(
        workspace.client.put("/api/captions", json={"image": str(image), "tags": ["a,b", "c"]}),
        400,
        "invalid_tag",
    )
    # Neither tags nor text given: a separate code split out of the fixed template (params empty, still localizable)
    _assert_frozen_error(workspace.client.put("/api/captions", json={"image": str(image)}), 400, "missing_tag_payload")
    # Missing / not an image
    _assert_frozen_error(
        workspace.client.put("/api/captions", json={"image": str(workspace.post / "ghost.png"), "tags": ["x"]}),
        404,
        "not_found",
    )
    notes = workspace.post / "notes.txt"
    notes.write_text("not an image", encoding="utf-8")
    _assert_frozen_error(
        workspace.client.put("/api/captions", json={"image": str(notes), "tags": ["x"]}),
        400,
        "unsupported_image",
    )


def test_actionable_4xx_errors_carry_localizable_params(workspace) -> None:
    """§4.9 addendum 4: the params of an actionable 4xx must carry every template variable.

    Asserting only the code would not go red when params drift—yet params are exactly the half that
    makes the English/Japanese UI able to use the translations (when placeholders cannot be filled
    the frontend falls back to the backend's Chinese). So params are compared item by item here.
    """
    from kohya_dataset_tagger.api.autotag import WRITE_MODES
    from kohya_dataset_tagger.api.captions import BATCH_OPS

    image = workspace.sample
    notes = workspace.post / "notes.txt"
    notes.write_text("not an image", encoding="utf-8")

    def frozen(response, status: int, code: str, params: dict) -> None:
        assert response.status_code == status, response.text[:300]
        body = response.json()["error"]
        assert body["code"] == code, body
        assert body["message"], body
        assert body["params"] == params, body

    frozen(
        workspace.client.get("/api/images/thumb", params={"path": str(image), "size": "huge"}),
        400, "bad_size", {"sizes": "grid|preview"},
    )
    frozen(
        workspace.client.get("/api/images/thumb", params={"path": str(notes)}),
        400, "unsupported_image", {"name": "notes.txt"},
    )
    frozen(
        workspace.client.get("/api/images/thumb", params={"path": str(workspace.post)}),
        400, "not_a_file", {"path": str(workspace.post)},
    )
    frozen(
        workspace.client.post("/api/captions/batch", json={"images": [str(image)], "op": "nope"}),
        400, "bad_op", {"op": "nope", "available": str(list(BATCH_OPS))},
    )
    frozen(
        workspace.client.put("/api/captions", json={"image": str(image)}),
        400, "missing_tag_payload", {},
    )
    frozen(
        workspace.client.post("/api/autotag/run", json={"images": []}),
        400, "empty_image_list", {},
    )
    frozen(
        workspace.client.post(
            "/api/autotag/run", json={"images": [str(image)], "model": "any-model", "write_mode": "sideways"}
        ),
        400, "bad_write_mode", {"modes": str(list(WRITE_MODES))},
    )
    frozen(
        workspace.client.get("/api/autotag/stream", params={"job_id": "no-such-job"}),
        404, "unknown_job", {"job_id": "no-such-job"},
    )


def test_path_errors_carry_the_offending_path(workspace) -> None:
    """The params of out-of-bounds / missing / not-a-directory must carry path — asserting only the code cannot catch this bug.

    Root-cause criterion: `PathOutsideRoots` once carried no structured payload, so the api site could
    only hard-code `("outside_roots", str(exc))`; the frontend's `msg.outside_roots` `{path}` could
    therefore never be filled and it silently fell back to the backend's Chinese—only the
    `dataset.toml` path could be localized. Here params are asserted endpoint by endpoint, not just
    the code.
    """
    # (1) Out of bounds (GET query param) → 403 outside_roots, path is the final resolved path
    outside = workspace.root.parent / "definitely-outside"
    response = workspace.client.get("/api/fs/list", params={"path": str(outside)})
    assert response.status_code == 403, response.text[:300]
    error = response.json()["error"]
    assert error["code"] == "outside_roots"
    assert error["params"] == {"path": str(outside.resolve())}, error

    # (2) Out of bounds (POST body) → the same exception payload must exit through the api layer's other try
    response = workspace.client.post("/api/cache/invalidate", json={"images": [str(outside)]})
    assert response.status_code == 403, response.text[:300]
    error = response.json()["error"]
    assert error["code"] == "outside_roots"
    assert error["params"] == {"path": str(outside.resolve())}, error

    # (3) Missing → 404 not_found, path is the one given in the request
    missing = workspace.post / "ghost.png"
    response = workspace.client.get("/api/images/thumb", params={"path": str(missing)})
    assert response.status_code == 404, response.text[:300]
    error = response.json()["error"]
    assert error["code"] == "not_found"
    assert error["params"] == {"path": str(missing)}, error

    # (4) Exists but is a file → 400 not_a_directory, path is that file
    notes = workspace.post / "notes.txt"
    notes.write_text("not a directory", encoding="utf-8")
    response = workspace.client.get("/api/fs/list", params={"path": str(notes)})
    assert response.status_code == 400, response.text[:300]
    error = response.json()["error"]
    assert error["code"] == "not_a_directory"
    assert error["params"] == {"path": str(notes.resolve())}, error


def test_batch_normalize_and_bad_op(workspace) -> None:
    image = workspace.sample
    workspace.client.put("/api/captions", json={"image": str(image), "text": "b tag,  a   tag , b tag"})
    response = workspace.client.post("/api/captions/batch", json={"images": [str(image)], "op": "normalize"})
    assert response.status_code == 200, response.text[:300]
    body = response.json()
    assert set(body) == {"applied", "failed", "results"}
    assert body["applied"] == 1 and body["failed"] == 0
    assert body["results"][0]["ok"] is True and body["results"][0]["image"] == str(image)
    assert image.with_suffix(CAPTION_EXT).read_text(encoding="utf-8") == "b tag, a tag"

    _assert_frozen_error(
        workspace.client.post("/api/captions/batch", json={"images": [str(image)], "op": "nope"}),
        400,
        "bad_op",
    )


def test_cache_status_and_invalidate_on_workspace(workspace) -> None:
    image = workspace.sample
    caption = image.with_suffix(CAPTION_EXT)
    cache_dir = workspace.post / TE_CACHE_DIRNAME
    cache_dir.mkdir(exist_ok=True)
    cache = cache_dir / (image.stem + "_anima_te.safetensors")
    cache.write_bytes(b"x")
    # The criterion is that caption.mtime is **strictly later** than cache.mtime, so roll the cache time back
    old = caption.stat().st_mtime - 60
    os.utime(cache, (old, old))

    status = workspace.client.get("/api/cache/status", params={"path": str(workspace.post)})
    assert status.status_code == 200
    stale = status.json()["stale"]
    assert [entry["image"] for entry in stale] == [str(image)]
    assert stale[0]["caption"] == str(caption) and stale[0]["cache"] == str(cache)

    invalidated = workspace.client.post("/api/cache/invalidate", json={"images": [str(image)]})
    assert invalidated.status_code == 200
    body = invalidated.json()
    assert set(body) == {"removed", "failed"}
    assert body["removed"] == [str(cache)] and body["failed"] == []
    assert not cache.exists()
    assert workspace.client.get("/api/cache/status", params={"path": str(workspace.post)}).json()["stale"] == []


# ---------------------------------------------------------------------------
# 4. autotag/preview: read-only + shape (real dataset, fingerprint backstop)
# ---------------------------------------------------------------------------


def test_preview_is_read_only_and_shape_is_frozen(dataset_client, tmp_path) -> None:
    _require_onnxruntime()
    models = {model["id"]: model for model in dataset_client.get("/api/autotag/models").json()["models"]}
    if not models.get(MODEL_ID, {}).get("available"):
        pytest.skip("this machine has no %s, cannot run the read-only preview's fingerprint criterion" % MODEL_ID)

    image = _sample_image(require_caption=True)
    caption = image.with_suffix(CAPTION_EXT)
    before_stat = (caption.stat().st_size, caption.stat().st_mtime_ns)

    # The read-only criterion is **relative**: sample a fingerprint before the run and compare against
    # it after. Deliberately not compared against a stored snapshot — one would go stale the moment the
    # user legitimately adds images or fixes captions, and this case needs to prove "preview did not
    # write the dataset", not "the dataset is still the one from months ago".
    snapshot = tmp_path / "manifest-before.json"
    made = _manifest_run("--out", str(snapshot))
    assert made.returncode == 0, "cannot sample a fingerprint before the run:\n%s\n%s" % (made.stdout, made.stderr)

    response = dataset_client.post("/api/autotag/preview", json={
        "images": [str(image)],
        "model": MODEL_ID,
        "thresholds": dict(THRESHOLDS),
        "postprocess": dict(POSTPROCESS),
    })
    assert response.status_code == 200, response.text[:500]
    body = response.json()
    assert set(body) == {"items"} and len(body["items"]) == 1
    item = body["items"][0]
    assert set(item) == {"image", "before", "after", "tags"}
    assert item["image"] == str(image)
    assert item["before"] == caption.read_text(encoding="utf-8-sig"), "before = the existing caption verbatim"
    for tag in item["tags"]:
        assert set(tag) == {"tag", "score", "category"}
        assert 0.0 <= tag["score"] <= 1.0
        assert tag["category"] in {"General", "Character", "Copyright", "Artist", "Meta", "Rating"}
        if len(tag["tag"]) >= 4:
            assert "_" not in tag["tag"], "§4.1: remove_underscore must be on by default"
        # §4.1 escape_parens defaults to true: every parenthesis must carry a backslash (a literal rule, not a check of whether it was already escaped)
        assert tag["tag"].count("(") == tag["tag"].count("\\("), "a bare opening parenthesis was not escaped: %r" % tag["tag"]
        assert tag["tag"].count(")") == tag["tag"].count("\\)"), "a bare closing parenthesis was not escaped: %r" % tag["tag"]
    if item["tags"]:
        assert item["after"] == ", ".join(tag["tag"] for tag in item["tags"])
        scores = [tag["score"] for tag in item["tags"]]
        assert scores == sorted(scores, reverse=True)
    else:
        assert item["after"] == ""

    assert (caption.stat().st_size, caption.stat().st_mtime_ns) == before_stat, "preview changed the caption"
    after = _manifest_run("--check", str(snapshot))
    assert after.returncode == 0, "the dataset fingerprint changed after preview:\n%s\n%s" % (after.stdout, after.stderr)


def test_preview_reports_bad_model_and_bad_options(dataset_client) -> None:
    # No model is needed for the option cases: the request's shape is validated **before** the
    # environment is consulted, so a bad threshold is a 400 whether or not a tagger is installed.
    image = _sample_image(require_caption=False)
    models = {model["id"]: model for model in dataset_client.get("/api/autotag/models").json()["models"]}

    _assert_frozen_error(
        dataset_client.post("/api/autotag/preview", json={"images": [str(image)], "model": "no-such-model"}),
        400,
        "unknown_model",
    )
    unavailable = sorted(model_id for model_id, entry in models.items() if not entry["available"])
    if unavailable:
        _assert_frozen_error(
            dataset_client.post("/api/autotag/preview", json={"images": [str(image)], "model": unavailable[0]}),
            409,
            "model_unavailable",
        )
    # Threshold/rating out of range must be blocked before the model loads (otherwise a pointless 1.5 s wait)
    _assert_frozen_error(
        dataset_client.post("/api/autotag/preview", json={"images": [str(image)], "thresholds": {"general": 2.5}}),
        400,
        "bad_request",
    )
    _assert_frozen_error(
        dataset_client.post("/api/autotag/preview", json={
            "images": [str(image)], "postprocess": {"rating": "middle"},
        }),
        400,
        "bad_request",
    )


# ---------------------------------------------------------------------------
# 5. autotag/run + SSE + cancel (all writes on a tmp copy)
# ---------------------------------------------------------------------------


def test_run_writes_and_streams_progress_until_finished(workspace) -> None:
    _require_onnxruntime()
    _require_local_model(workspace.client)
    images = workspace.synth[:3]
    for image in images:  # first write a recognizable seed caption to prove overwrite really happened
        image.with_suffix(CAPTION_EXT).write_text("seed tag", encoding="utf-8")

    start = workspace.client.post("/api/autotag/run", json=_run_payload(images, "overwrite"))
    assert start.status_code == 200, start.text[:400]
    job_id = start.json()["job_id"]
    assert isinstance(job_id, str) and job_id

    events = _read_stream(workspace.client, job_id)
    names = [name for name, _data in events]
    assert names.count("done") == 1, "there can be only one done at the end: %s" % names
    progress = [data for name, data in events if name == "progress"]
    assert len(progress) == len(images), "one progress must be emitted per processed image (§4.3)"
    assert [item["done"] for item in progress] == [1, 2, 3]
    for item in progress:
        assert set(item) == PROGRESS_FIELDS
        assert item["phase"] == "infer", "§4.7: existing tagging events must carry phase too"
        assert item["total"] == len(images) and Path(item["current"]).is_file()
    final = [data for name, data in events if name == "done"][0]
    assert set(final) == DONE_FIELDS
    assert final["phase"] == "infer"
    assert final["finished"] is True, "§4.3: the end must emit finished: true"
    assert final["done"] == final["total"] == len(images)
    assert final["errors"] == []

    for image in images:
        caption = image.with_suffix(CAPTION_EXT)
        assert caption.is_file()
        assert "seed tag" not in caption.read_text(encoding="utf-8"), "overwrite did not take effect"


def test_infer_events_carry_phase_infer(workspace, stub_tagger) -> None:
    """§4.7: tagging-path events must carry phase:"infer" too (the unit is **images**, not bytes).

    Runs with a stub session, so it does not depend on onnxruntime—this contract should not go
    unverified just because the environment lacks ORT (that is what happened on 2026-09-17 while
    this venv switched to the GPU build of ORT).
    """
    _require_local_model(workspace.client)

    images = workspace.synth[:3]
    started = workspace.client.post("/api/autotag/run", json=_run_payload(images, "overwrite"))
    assert started.status_code == 200, started.text[:300]
    events = _read_stream(workspace.client, started.json()["job_id"])

    progress = [data for name, data in events if name == "progress"]
    assert len(progress) == len(images), "one progress per processed image"
    for item in progress:
        assert set(item) == PROGRESS_FIELDS
        assert item["phase"] == "infer", "the tagging events are missing phase=infer"
        assert item["total"] == len(images), "with infer, total is in images"
        assert 1 <= item["done"] <= len(images)
    final = [data for name, data in events if name == "done"][0]
    assert set(final) == DONE_FIELDS
    assert final["phase"] == "infer"
    assert final["finished"] is True
    assert final["done"] == final["total"] == len(images)
    assert final["errors"] == []


def test_run_write_modes_merge_and_only_if_empty(workspace) -> None:
    _require_onnxruntime()
    _require_local_model(workspace.client)
    image = workspace.synth[0]
    caption = image.with_suffix(CAPTION_EXT)

    caption.write_text("keep me", encoding="utf-8")
    only_empty = workspace.client.post("/api/autotag/run", json=_run_payload([image], "only_if_empty"))
    assert only_empty.status_code == 200
    final = [data for name, data in _read_stream(workspace.client, only_empty.json()["job_id"]) if name == "done"][0]
    assert final["finished"] is True
    assert caption.read_text(encoding="utf-8") == "keep me", "only_if_empty must not touch an existing caption"

    merge = workspace.client.post("/api/autotag/run", json=_run_payload([image], "merge"))
    assert merge.status_code == 200
    final = [data for name, data in _read_stream(workspace.client, merge.json()["job_id"]) if name == "done"][0]
    assert final["finished"] is True
    merged = caption.read_text(encoding="utf-8")
    assert merged.startswith("keep me"), "merge must keep the existing tags: %r" % merged
    assert merged == "keep me" or merged.startswith("keep me, ")

    _assert_frozen_error(
        workspace.client.post("/api/autotag/run", json=_run_payload([image], "sideways")),
        400,
        "bad_write_mode",
    )
    _assert_frozen_error(
        workspace.client.post("/api/autotag/run", json=_run_payload([], "overwrite")),
        400,
        "empty_image_list",
    )


def test_cancel_stops_mid_way_and_still_sends_finished(workspace) -> None:
    _require_onnxruntime()
    _require_local_model(workspace.client)
    images = workspace.synth  # 12 images: the cancel request is guaranteed to land before the first one
    start = workspace.client.post("/api/autotag/run", json=_run_payload(images, "overwrite"))
    assert start.status_code == 200, start.text[:300]
    job_id = start.json()["job_id"]

    cancel = workspace.client.post("/api/autotag/cancel", json={"job_id": job_id})
    assert cancel.status_code == 202, cancel.text[:300]
    assert cancel.json() == {"cancelled": True}

    events = _read_stream(workspace.client, job_id)
    done = [data for name, data in events if name == "done"]
    assert len(done) == 1, "one done must be emitted after a cancel too (§4.4)"
    assert done[0]["finished"] is True
    assert done[0]["total"] == len(images)
    assert done[0]["done"] < done[0]["total"], "the cancel did not really take effect: ran to completion %d/%d" % (done[0]["done"], done[0]["total"])

    # Cancel a nonexistent job: 202 + cancelled=false (the frontend uses that to finish up, not as an error)
    ghost = workspace.client.post("/api/autotag/cancel", json={"job_id": "no-such-job"})
    assert ghost.status_code == 202
    assert ghost.json() == {"cancelled": False}


def test_stream_unknown_job_is_404(dataset_client) -> None:
    _assert_frozen_error(
        dataset_client.get("/api/autotag/stream", params={"job_id": "no-such-job"}),
        404,
        "unknown_job",
    )

def test_get_caption_reports_missing_sidecar(workspace) -> None:
    """An image with no caption: `exists=false` + empty text/tags (the frontend uses that to draw a red dot)."""
    image = workspace.synth[0]
    response = workspace.client.get("/api/captions", params={"path": str(image)})
    assert response.status_code == 200
    body = response.json()
    assert Path(body["caption_path"]) == image.with_suffix(CAPTION_EXT)
    assert body["exists"] is False
    assert body["text"] == "" and body["tags"] == []
    assert body["multiline_warning"] is False



def test_batch_read_returns_the_same_entry_as_the_single_get(workspace) -> None:
    """POST /api/captions/read is the N-images-in-one-request form of GET /api/captions.

    The tag filter and the visible-scope frequency table need every image's tags; asking one by one
    was 691 requests on the recursive scope of the reference dataset. The batched entry must be the
    single entry (the frontend stores it under the same keys), and reading must write nothing.
    """
    images = [workspace.sample, workspace.synth[0]]
    captured = {
        image: image.with_suffix(CAPTION_EXT).stat().st_mtime_ns
        for image in images
        if image.with_suffix(CAPTION_EXT).is_file()
    }

    response = workspace.client.post(
        "/api/captions/read", json={"images": [str(image) for image in images]}
    )
    assert response.status_code == 200, response.text
    results = response.json()["results"]
    assert [entry["image"] for entry in results] == [str(image) for image in images]
    assert all(entry["ok"] is True for entry in results)

    for entry, image in zip(results, images):
        single = workspace.client.get("/api/captions", params={"path": str(image)}).json()
        assert {key: entry[key] for key in single} == single, entry

    for image, mtime in captured.items():
        assert image.with_suffix(CAPTION_EXT).stat().st_mtime_ns == mtime, "reading a caption touched it"


def test_batch_read_reports_one_missing_file_without_failing_the_rest(workspace) -> None:
    """One vanished file is that entry's error; the other entries still come back."""
    ghost = workspace.post / "ghost.png"
    response = workspace.client.post(
        "/api/captions/read", json={"images": [str(workspace.sample), str(ghost)]}
    )
    assert response.status_code == 200, response.text
    results = response.json()["results"]
    assert results[0]["ok"] is True
    assert results[1]["ok"] is False
    assert results[1]["error"]["code"] == "not_found"


def test_batch_read_out_of_root_is_403(dataset_client) -> None:
    """Out of bounds is a security problem, so it rejects the whole request (same rule as /captions/batch)."""
    _assert_frozen_error(
        dataset_client.post("/api/captions/read", json={"images": [r"C:\Windows\win.ini"]}),
        403,
        "outside_roots",
    )


def test_put_caption_never_swallows_a_failed_cache_removal(workspace) -> None:
    """Write succeeds but the cache cannot be removed: must be 200 + non-empty `cache_remove_failed` (the frontend shows a red toast).

    This is the second half of invariant number one. On Windows, holding a read handle is enough
    to make os.remove raise PermissionError.
    """
    image = workspace.sample
    cache_dir = workspace.post / TE_CACHE_DIRNAME
    cache_dir.mkdir(exist_ok=True)
    locked = cache_dir / (image.stem + "_anima_te.safetensors")
    locked.write_bytes(b"locked")

    with open(locked, "rb"):
        response = workspace.client.put(
            "/api/captions", json={"image": str(image), "tags": ["1girl", "locked-cache"]}
        )
        assert response.status_code == 200, "a successful write should be 200; a cache that was not removed is not a write failure"
        body = response.json()
        if not body["cache_remove_failed"]:
            pytest.skip("this platform allows deleting a file that is in use, so the failure path cannot be constructed")
        assert body["changed"] is True
        assert body["cache_removed"] == []
        assert body["cache_remove_failed"] == [str(locked)]
        assert locked.exists(), "it reported a removal failure but the file is gone—the report was false"
        got = workspace.client.get("/api/captions", params={"path": str(image)}).json()
        assert got["tags"] == ["1girl", "locked-cache"], "the caption must still be written correctly even if the cache cannot be removed"

# ---------------------------------------------------------------------------
# 6. §4.5 frozen domains: preview limit, escape_parens passthrough (without hitting a real model)
# ---------------------------------------------------------------------------


class _StubTagger:
    """A deterministic fake tagger: verifies only the HTTP → postprocessing wiring, without loading a 445 MB model or using CPU."""

    name = "stub"

    def __init__(self, scores) -> None:
        self._scores = list(scores)
        self.calls: list[str] = []

    def predict(self, image):
        self.calls.append(str(image))
        return list(self._scores)


def _stub_scores():
    from kohya_dataset_tagger.autotag.base import TagScore

    return [
        TagScore(tag="keqing_(genshin_impact)", score=0.90, category="Character"),
        TagScore(tag="1girl", score=0.88, category="General"),
        TagScore(tag="long_hair", score=0.60, category="General"),
        TagScore(tag="general", score=0.99, category="Rating"),
    ]


@pytest.fixture()
def stub_tagger(monkeypatch):
    """Replace the resident session with a stub: `_session()` checks the cache first, so it never touches registry / onnxruntime."""
    from kohya_dataset_tagger.api import autotag as autotag_api

    stub = _StubTagger(_stub_scores())
    monkeypatch.setitem(autotag_api._SESSIONS, MODEL_ID, stub)
    return stub


def test_preview_forwards_escape_parens(workspace, stub_tagger) -> None:
    r"""§4.1: `escape_parens` defaults to true → `keqing \(genshin impact\)`; explicitly false keeps it as-is.

    This pins down **whether the HTTP layer drops this key**: the frontend `autotag.js` sends it, and
    once pydantic ignores it the UI checkbox becomes decorative (exactly what the wave-2 report
    described).
    """
    image = workspace.synth[0]
    base = {"images": [str(image)], "model": MODEL_ID}

    default = workspace.client.post("/api/autotag/preview", json=base)
    assert default.status_code == 200, default.text[:300]
    item = default.json()["items"][0]
    assert item["after"] == "keqing \\(genshin impact\\), 1girl, long hair", item["after"]
    assert item["tags"][0]["category"] == "Character"
    # The rating tag does not enter the result under rating="none"
    assert "general" not in [tag["tag"] for tag in item["tags"]]

    bare = workspace.client.post(
        "/api/autotag/preview", json=dict(base, postprocess={"escape_parens": False})
    )
    assert bare.status_code == 200, bare.text[:300]
    assert bare.json()["items"][0]["after"] == "keqing (genshin impact), 1girl, long hair"

    # remove_underscore=false is unrelated to escape_parens: underscores are kept, parentheses still escaped
    raw = workspace.client.post(
        "/api/autotag/preview",
        json=dict(base, postprocess={"remove_underscore": False, "escape_parens": False}),
    )
    assert raw.json()["items"][0]["after"] == "keqing_(genshin_impact), 1girl, long_hair"


def test_preview_caps_at_32_images(workspace, stub_tagger) -> None:
    """§4.5 ②: >32 images → 400 `too_many_images` (the safety valve for a synchronous request), =32 images passes."""
    extra = []
    for index in range(20):
        path = workspace.post / ("extra%02d.png" % index)
        Image.new("RGB", (16, 16), (index, 100, 30)).save(path)
        extra.append(path)
    exactly_32 = workspace.synth + extra
    assert len(exactly_32) == 32

    ok = workspace.client.post(
        "/api/autotag/preview", json={"images": [str(path) for path in exactly_32], "model": MODEL_ID}
    )
    assert ok.status_code == 200, ok.text[:300]
    assert len(ok.json()["items"]) == 32
    assert len(stub_tagger.calls) == 32

    too_many = workspace.client.post(
        "/api/autotag/preview",
        json={"images": [str(path) for path in exactly_32] + [str(workspace.sample)], "model": MODEL_ID},
    )
    _assert_frozen_error(too_many, 400, "too_many_images")
    message = too_many.json()["error"]["message"]
    assert "33" in message and "32" in message, "the error message must carry the actual count and the limit: %r" % message

    # The limit check precedes path resolution: 33 nonexistent paths must also be too_many_images, not 404
    bogus = workspace.client.post("/api/autotag/preview", json={
        "images": [str(workspace.post / ("ghost%02d.png" % index)) for index in range(33)],
        "model": MODEL_ID,
    })
    _assert_frozen_error(bogus, 400, "too_many_images")
    assert len(stub_tagger.calls) == 32, "a request blocked by 400 must not run inference"

def test_preview_rejects_unknown_postprocess_keys(workspace, stub_tagger) -> None:
    """An unknown postprocess key must be 422, not silently dropped.

    This is the regression test for the `escape_parens` pitfall: pydantic ignores extra keys by
    default, so "the frontend sent it, the backend did not pick it up" shows up as **a checkbox that
    does nothing and no error at all**.
    """
    response = workspace.client.post("/api/autotag/preview", json={
        "images": [str(workspace.synth[0])],
        "model": MODEL_ID,
        "postprocess": {"escape_parens": True, "no_such_option": 1},
    })
    _assert_frozen_error(response, 422, "validation_error")
    assert "no_such_option" in response.json()["error"]["message"]
    assert stub_tagger.calls == [], "a request blocked by 422 must not run inference"

def test_batch_replace_and_regex_use_the_frozen_parameter_names(workspace) -> None:
    """§4.5 ③: `replace(find, replace, ignore_case)`, `regex_replace(pattern, replacement)`."""
    image = workspace.sample
    workspace.client.put("/api/captions", json={"image": str(image), "text": "Blue Eyes, blue eyes, red hair"})

    def batch(**extra):
        payload = {"images": [str(image)], "op": extra.pop("op")}
        payload.update(extra)
        response = workspace.client.post("/api/captions/batch", json=payload)
        assert response.status_code == 200, response.text[:300]
        return image.with_suffix(CAPTION_EXT).read_text(encoding="utf-8")

    assert batch(op="replace", find="blue eyes", replace="azure eyes") == "Blue Eyes, azure eyes, red hair"
    assert batch(op="replace", find="blue eyes", replace="azure eyes", ignore_case=True) == (
        "azure eyes, azure eyes, red hair"
    )
    assert batch(op="regex_replace", pattern="azure", replacement="crimson") == (
        "crimson eyes, crimson eyes, red hair"
    )
    # append / remove use tags
    assert batch(op="append", tags=["solo"]) == "crimson eyes, crimson eyes, red hair, solo"
    assert batch(op="remove", tags=["crimson eyes"]) == "red hair, solo"

    _assert_frozen_error(
        workspace.client.post("/api/captions/batch", json={"images": [str(image)], "op": "replace"}),
        400,
        "bad_request",
    )

def test_batch_edit_tags_is_single_pass_and_position_aware(workspace) -> None:
    """`edit_tags` / `prepend`: renamed groups (empty string = delete), single-pass mapping, where new tags land.

    Single-pass mapping is deliberate: when a→b and b→c are given together, the original a only
    becomes b. If it were chained, the user's "first rename a to b, then b to c" would also turn a
    into c—invisible in the UI.
    """
    image = workspace.sample
    workspace.client.put("/api/captions", json={"image": str(image), "text": "a, b, c"})

    def batch(**payload):
        response = workspace.client.post("/api/captions/batch", json={"images": [str(image)], **payload})
        assert response.status_code == 200, response.text[:300]
        return image.with_suffix(CAPTION_EXT).read_text(encoding="utf-8")

    # Group rename: b→B, the rest untouched
    assert batch(op="edit_tags", pairs=[{"find": "b", "replace": "B"}]) == "a, B, c"
    # Empty string = delete; deleting the 2nd must not make the 3rd "shift-rename"
    assert batch(op="edit_tags", pairs=[{"find": "B", "replace": ""}]) == "a, c"
    # New tags land at the end by default
    assert batch(op="edit_tags", tags=["z"]) == "a, c, z"
    # position=prepend lands at the front
    assert batch(op="edit_tags", tags=["y"], position="prepend") == "y, a, c, z"
    # Single-pass mapping: a→b and b→c given together, the original a only becomes b
    assert batch(
        op="edit_tags", pairs=[{"find": "a", "replace": "b"}, {"find": "b", "replace": "c"}]
    ) == "y, b, c, z"
    # The standalone prepend op is symmetric with append
    assert batch(op="prepend", tags=["head"]) == "head, y, b, c, z"
    # ignore_case applies to pairs too
    assert batch(
        op="edit_tags", pairs=[{"find": "HEAD", "replace": "HEAD2"}], ignore_case=True
    ) == "HEAD2, y, b, c, z"

    # Incomplete / illegal parameters are always 400, and not a single byte is written
    for bad in (
        {"op": "edit_tags"},
        {"op": "edit_tags", "tags": ["x"], "position": "middle"},
        {"op": "edit_tags", "pairs": [{"find": "", "replace": "x"}]},
    ):
        _assert_frozen_error(
            workspace.client.post("/api/captions/batch", json={"images": [str(image)], **bad}),
            400,
            "bad_request",
        )
    assert image.with_suffix(CAPTION_EXT).read_text(encoding="utf-8") == "HEAD2, y, b, c, z"


def test_batch_regex_replace_can_target_the_whole_caption(workspace) -> None:
    """`regex_replace_text` whole-caption replacement: commas in the result are split into new tags (the trainer's rule)."""
    image = workspace.sample
    workspace.client.put("/api/captions", json={"image": str(image), "text": "1boy, solo, smile"})

    def batch(**payload):
        response = workspace.client.post("/api/captions/batch", json={"images": [str(image)], **payload})
        assert response.status_code == 200, response.text[:300]
        return image.with_suffix(CAPTION_EXT).read_text(encoding="utf-8")

    # A regex for a single tag replaces only within that tag
    assert batch(op="regex_replace", pattern=r"1boy", replacement="1girl") == "1girl, solo, smile"
    # A whole-caption regex can replace a stretch of text with "multiple comma-separated tags"
    assert batch(
        op="regex_replace_text", pattern=r"solo, smile", replacement="solo, grin, closed mouth"
    ) == "1girl, solo, grin, closed mouth"
    # Whole-caption replacement with a string containing no comma
    assert batch(
        op="regex_replace_text", pattern=r", closed mouth$", replacement=""
    ) == "1girl, solo, grin"
    # An invalid regex / a missing pattern is always 400
    for bad in (
        {"op": "regex_replace_text", "pattern": "([", "replacement": "x"},
        {"op": "regex_replace_text"},
    ):
        _assert_frozen_error(
            workspace.client.post("/api/captions/batch", json={"images": [str(image)], **bad}),
            400,
            "bad_request",
        )


def test_batch_dry_run_previews_without_writing(workspace) -> None:
    """`dry_run` is "take a look before applying": no write, no mtime change, no TE cache deletion."""
    image = workspace.sample
    caption = image.with_suffix(CAPTION_EXT)
    workspace.client.put("/api/captions", json={"image": str(image), "text": "blue eyes, red hair"})
    cache_dir = workspace.post / TE_CACHE_DIRNAME
    cache_dir.mkdir(exist_ok=True)
    cache = cache_dir / (image.stem + "_anima_te.safetensors")
    cache.write_bytes(b"stale")
    before_bytes = caption.read_bytes()
    before_mtime = caption.stat().st_mtime_ns

    response = workspace.client.post("/api/captions/batch", json={
        "images": [str(image)], "op": "replace",
        "find": "blue eyes", "replace": "azure eyes", "dry_run": True,
    })
    assert response.status_code == 200, response.text[:300]
    body = response.json()
    assert body["dry_run"] is True
    assert body["applied"] == 1 and body["failed"] == 0
    entry = body["results"][0]
    assert entry["changed"] is True
    assert entry["before"] == "blue eyes, red hair"
    assert entry["after"] == "azure eyes, red hair"
    assert caption.read_bytes() == before_bytes, "dry_run changed the caption"
    assert caption.stat().st_mtime_ns == before_mtime, "dry_run touched the caption's mtime"
    assert cache.is_file(), "dry_run deleted the training cache"

    # An op that matches nothing: changed=false, and not counted in applied
    body = workspace.client.post("/api/captions/batch", json={
        "images": [str(image)], "op": "replace", "find": "nope", "replace": "x", "dry_run": True,
    }).json()
    assert body["applied"] == 0 and body["results"][0]["changed"] is False

    # A new tag containing a comma: invalid_tag is reported in the preview rather than failing at write time
    body = workspace.client.post("/api/captions/batch", json={
        "images": [str(image)], "op": "edit_tags",
        "pairs": [{"find": "red hair", "replace": "a, b"}], "dry_run": True,
    }).json()
    assert body["failed"] == 1
    assert body["results"][0]["error"]["code"] == "invalid_tag"
    assert caption.read_bytes() == before_bytes

    # Only a real write touches the caption and the cache
    written = workspace.client.post("/api/captions/batch", json={
        "images": [str(image)], "op": "replace", "find": "blue eyes", "replace": "azure eyes",
    }).json()
    assert written.get("dry_run") is None
    assert caption.read_text(encoding="utf-8") == "azure eyes, red hair"
    assert not cache.is_file(), "a real write did not delete the TE cache = invariant number one is broken"


# ---------------------------------------------------------------------------
# 7. §4.6-§4.8 the HTTP surface of model download (local fake endpoint + the **real** autotag/download.py)
# ---------------------------------------------------------------------------
#
# eva02's onnx is 1.26 GB, so this section downloads not a single real byte:
# - endpoint: a temporary HTTP server on 127.0.0.1 serving §4.6's two paths (manifest + resolve/main);
# - implementation: the **real** autotag/download.py (module C), with only its DEFAULT_ENDPOINTS and
#   KOHYA_TAGGER_HF_ENDPOINT pointed at the local fake endpoint. So endpoints() leaves only the local
#   one, and there is **no** code path in the test that "falls back to huggingface.co"—otherwise the
#   failure cases would quietly download 445 MB. Every case asserts this precondition (see
#   _download_world).
# - on disk: the model root is tmp_path. Neither the real model cache directory nor the real dataset
#   is touched.


#: A fake model and fake vocabulary: a 3 MiB onnx (larger than download.CHUNK_SIZE=1 MiB, so it can
#: exercise multi-chunk download, progress throttling, and "cancel midway") plus a few-dozen-byte vocabulary.
ONNX_BYTES = b"ONNX" * (768 * 1024)
VOCAB_BYTES = b"tag_id,name,category,count\n1,1girl,0,5113288\n"
FAKE_REPO = "SmilingWolf/wd-swinv2-tagger-v3"


class FakeHfEndpoint:
    """A local stand-in for huggingface.co (§4.6's two paths). **Listens on 127.0.0.1 only.**

    - GET  /api/models/<repo>?blobs=true       -> {"siblings":[{"rfilename","size"}]}
    - GET|HEAD /<repo>/resolve/main/<file>     -> the file bytes

    With truncate=True it deliberately "declares a full Content-Length but writes only half the
    body"—§4.8's required "size mismatch / transfer interrupted" checks are produced by a **real
    truncation**, not by the test raising an exception itself.

    gate is used to pin down the moment "the download is in progress": the body writes
    GATE_AFTER_BYTES first, then stops and waits for a signal. The cancel case clears it, so the
    timing is not gambled on with sleep.
    """

    #: How many bytes are written before the gate opens. **Must exceed download.CHUNK_SIZE (1 MiB)**:
    #: httpx's iter_bytes(1 MiB) only yields data once a full chunk accumulates, and if we write less
    #: it will never see the first progress callback (the first version got stuck exactly here: the
    #: server wrote 256 KiB and waited for the gate, the client could not yield a single chunk →
    #: deadlock).
    GATE_AFTER_BYTES = 2 * 1024 * 1024

    def __init__(self, files: dict, *, repo: str, truncate: bool = False) -> None:
        self.files = dict(files)
        self.repo = repo
        self.truncate = truncate
        self.requests: list[str] = []
        self.base_url = ""
        self.gate = threading.Event()
        self.gate.set()
        self._server: http.server.ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> str:
        endpoint = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, *_args) -> None:  # silence: do not flood the test output
                pass

            def do_GET(self) -> None:
                endpoint.handle(self)

            def do_HEAD(self) -> None:
                endpoint.handle(self, head_only=True)

        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self.base_url = "http://127.0.0.1:%d" % self._server.server_address[1]
        return self.base_url

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def handle(self, request, *, head_only: bool = False) -> None:
        path = urllib.parse.urlparse(request.path).path
        self.requests.append(path)
        if path.startswith("/api/models/"):
            repo = urllib.parse.unquote(path[len("/api/models/"):])
            if repo != self.repo:
                return self._send(request, 404, b'{"error": "no such repo"}', head_only=head_only)
            manifest = {
                "id": repo,
                "siblings": [{"rfilename": name, "size": len(data)} for name, data in self.files.items()],
            }
            return self._send(
                request, 200, json.dumps(manifest).encode("utf-8"), "application/json", head_only=head_only
            )
        if "/resolve/main/" in path:
            name = urllib.parse.unquote(path.split("/resolve/main/", 1)[1])
            body = self.files.get(name)
            if body is None:
                return self._send(request, 404, b"not found", head_only=head_only)
            payload = body[: max(1, len(body) // 2)] if self.truncate else body
            return self._send(
                request, 200, payload, "application/octet-stream",
                content_length=len(body), head_only=head_only, gated=True,
            )
        return self._send(request, 404, b"not found", head_only=head_only)

    #: How long the gate waits before giving up. **Deliberately large**: this is headroom for "the
    #: test thread starved by full concurrency", not a criterion of the case. When it was too small
    #: (30 s in the first version), a starved test thread could not release it in time, the server
    #: disconnected early → the download implementation treated it as "transfer interrupted" → errors
    #: non-empty → the case went falsely red.
    GATE_TIMEOUT = 300.0

    def _send(
        self,
        request,
        status: int,
        body: bytes,
        content_type: str = "text/plain",
        content_length: int | None = None,
        head_only: bool = False,
        gated: bool = False,
    ) -> None:
        request.send_response(status)
        request.send_header("Content-Type", content_type)
        request.send_header("Content-Length", str(len(body) if content_length is None else content_length))
        request.end_headers()
        if head_only:
            return
        if not gated:
            request.wfile.write(body)
            return
        head, rest = body[: self.GATE_AFTER_BYTES], body[self.GATE_AFTER_BYTES:]
        request.wfile.write(head)
        request.wfile.flush()
        if rest:
            if not self.gate.wait(timeout=self.GATE_TIMEOUT):
                return  # if the gate never opened (test failed), do not hang the serving thread forever either
            request.wfile.write(rest)
            request.wfile.flush()


@contextlib.contextmanager
def _download_world(tmp_path: Path, monkeypatch, *, truncate: bool = False):
    """A temporary model root + local fake endpoint + the real download implementation; on exit it stops the server and restores the endpoints.

    **Safety precondition**: both download.DEFAULT_ENDPOINTS and KOHYA_TAGGER_HF_ENDPOINT point at
    the fake endpoint, so endpoints() contains no real site. This asserts it directly, lest a later
    change to the implementation's default make the failure case actually download 445 MB.
    """
    from kohya_dataset_tagger.autotag import download as download_module

    endpoint = FakeHfEndpoint(
        {"model.onnx": ONNX_BYTES, "selected_tags.csv": VOCAB_BYTES}, repo=FAKE_REPO, truncate=truncate
    )
    endpoint.start()
    monkeypatch.setattr(download_module, "DEFAULT_ENDPOINTS", (endpoint.base_url,))
    monkeypatch.setenv(download_module.HF_ENDPOINT_ENV, endpoint.base_url)
    assert download_module.endpoints() == (endpoint.base_url,), "the endpoints are not fully pinned to the local fake endpoint"

    model_root = tmp_path / "models"
    model_root.mkdir()
    # Declare the model root explicitly: it pins _download_root's priority 1 (KOHYA_TAGGER_MODELS) and
    # guarantees that **any** download destination is under tmp—never %LOCALAPPDATA% or the HF cache
    # directory.
    monkeypatch.setenv(config.ENV_MODELS, str(model_root))
    config.load(roots=[tmp_path], thumb_cache_dir=tmp_path / "thumbs", model_search_roots=[model_root])
    from kohya_dataset_tagger.api import autotag as autotag_api

    assert autotag_api._download_root() == model_root, \
        "the download destination is not the model root in tmp (it would write to a real directory): %s" % autotag_api._download_root()
    try:
        with TestClient(create_app()) as client:
            yield SimpleNamespace(
                client=client, model_root=model_root, endpoint=endpoint, download=download_module,
                onnx=model_root / MODEL_ID / "model.onnx",
            )
    finally:
        endpoint.stop()


@pytest.fixture()
def download_env(tmp_path: Path, monkeypatch):
    with _download_world(tmp_path, monkeypatch) as world:
        yield world


def _model_entry(client: TestClient, model_id: str) -> dict:
    body = client.get("/api/autotag/models").json()
    return {model["id"]: model for model in body["models"]}[model_id]


def _start_download(world, model: str = MODEL_ID) -> str:
    started = world.client.post("/api/autotag/download", json={"model": model})
    assert started.status_code == 202, started.text[:300]
    body = started.json()
    assert set(body) == {"job_id"}, "§4.7: the 202 response body contains only job_id"
    job_id = body["job_id"]
    assert isinstance(job_id, str) and job_id
    return job_id


def _download_events(world, job_id: str) -> tuple:
    events = _read_stream(world.client, job_id)
    assert [name for name, _data in events].count("done") == 1, "there can be only one done at the end"
    return (
        [data for name, data in events if name == "progress"],
        [data for name, data in events if name == "done"][0],
    )


def _wait_for_download_to_start(job_id: str, timeout: float = 120.0):
    """Wait until **the job snapshot really starts receiving bytes**—state polling with a timeout, not a fixed sleep / fixed number of rounds.

    The timeout is only a failure bound (the test thread may be starved under full concurrency, so it
    is generous); the assertion itself depends on state (job.done > 0), not time. It also reports the
    two "precondition does not hold" situations separately: the job is gone vs the job ended before
    the cancel.
    """
    from kohya_dataset_tagger.api import autotag as autotag_api

    deadline = time.monotonic() + timeout
    while True:
        job = autotag_api.get_job(job_id)
        if job is None:
            raise AssertionError("job %s is already gone (never registered or evicted)" % job_id)
        if job.finished:
            raise AssertionError("download job %s ended before the cancel—the cancel case's precondition does not hold" % job_id)
        if job.done > 0:
            return job
        if time.monotonic() >= deadline:
            raise AssertionError("the download did not start (job %s's done has been 0 bytes, waited %.0f s)" % (job_id, timeout))
        time.sleep(0.02)


def test_models_get_never_touches_the_network(download_env) -> None:
    """§4.7: size_bytes is 0 when unavailable and downloadable is a purely local determination—a GET sends no network request at all."""
    entry = _model_entry(download_env.client, MODEL_ID)
    assert entry["installed"] is False and entry["available"] is False
    assert entry["size_bytes"] == 0, "not installed means 0 (do not fetch the HF manifest for one byte count)"
    assert entry["downloadable"] is True, "the real implementation provides is_downloadable(model_id)"
    assert download_env.endpoint.requests == [], \
        "GET /api/autotag/models sent a network request: %s" % download_env.endpoint.requests


def test_download_streams_byte_progress_and_writes_flat_layout(download_env) -> None:
    """§4.7: POST 202 {job_id} → SSE receives phase=download progress and a finished done."""
    progress, final = _download_events(download_env, _start_download(download_env))

    expected_total = len(ONNX_BYTES) + len(VOCAB_BYTES)
    assert progress, "the download emitted not a single progress"
    previous = 0
    for item in progress:
        assert set(item) == PROGRESS_FIELDS, "the progress fields frozen by §4.7"
        assert item["phase"] == "download", "a download progress must carry phase=download"
        assert item["current"] in {"model.onnx", "selected_tags.csv"}, "current is a file name"
        assert item["total"] == expected_total, "with download, total is the whole job's byte count"
        assert previous <= item["done"] <= item["total"], "done must be monotonically non-decreasing: %r" % item
        previous = item["done"]
    assert {item["current"] for item in progress} == {"model.onnx", "selected_tags.csv"}, \
        "both files must report progress: %r" % progress
    assert progress[-1]["done"] == expected_total, "the last progress should already be fully downloaded"

    assert set(final) == DONE_FIELDS
    assert final["phase"] == "download"
    assert final["finished"] is True, "§4.3/§4.7: the end must emit finished: true"
    assert final["errors"] == [], final
    assert final["done"] == expected_total

    onnx = download_env.onnx
    vocab = onnx.with_name("selected_tags.csv")
    assert onnx.read_bytes() == ONNX_BYTES, "the bytes written to disk do not match what the fake endpoint gave"
    assert vocab.read_bytes() == VOCAB_BYTES
    assert not list(download_env.model_root.rglob("*.part")), "the success path must not leave a .part either"


def test_download_marks_the_model_installed_and_is_idempotent(download_env) -> None:
    """§4.7/§4.8: after the download → installed turns true in models, size_bytes is non-zero; downloading again does not rewrite the file."""
    _download_events(download_env, _start_download(download_env))

    entry = _model_entry(download_env.client, MODEL_ID)
    assert entry["size_bytes"] == len(ONNX_BYTES), "size_bytes must equal the byte count of the local ONNX"
    assert entry["installed"] is True, \
        "downloaded but the registry still cannot resolve it—§4.6's flat layout <root>/<model_id>/model.onnx was not recognized (A21)"
    assert entry["available"] is True
    assert Path(entry["onnx_path"]).resolve() == download_env.onnx.resolve()
    assert Path(entry["vocab_path"]).name == "selected_tags.csv"

    before = download_env.onnx.stat().st_mtime_ns
    _progress, final = _download_events(download_env, _start_download(download_env))
    assert final["errors"] == [] and final["finished"] is True
    assert download_env.onnx.stat().st_mtime_ns == before, "an existing file of the correct size must not be rewritten (§4.8 idempotency)"


def test_download_failure_reports_errors_and_leaves_no_half_model(tmp_path, monkeypatch) -> None:
    """§4.7: a failure (a real truncation here) → errors non-empty + done{finished:true}, and no "success" event is emitted."""
    with _download_world(tmp_path, monkeypatch, truncate=True) as world:
        assert world.download.endpoints() == (world.endpoint.base_url,)
        progress, final = _download_events(world, _start_download(world))

        assert set(final) == DONE_FIELDS
        assert final["phase"] == "download"
        assert final["finished"] is True, "a failure must emit the finish signal too, otherwise the frontend waits forever"
        assert final["errors"], "the download failed but there are no errors"
        assert final["errors"][0]["path"] == MODEL_ID, "the errors path records the model id"
        message = final["errors"][0]["message"]
        assert "download failed" in message, message
        assert world.endpoint.base_url in message, "the failure message should name that local endpoint: %s" % message
        for item in progress:
            assert item["done"] < item["total"], "the failure path reported 100%%: %r" % item
        assert not world.onnx.exists(), "it failed but left half a model behind"
        assert not list(world.model_root.rglob("*.part")), "§4.8: the failure path must delete the .part"


def test_download_cancel_stops_midway_and_still_sends_finished(download_env) -> None:
    """§4.4's cancel works for downloads too: it stops, emits done afterwards, leaves no .part, and is not an error."""
    download_env.endpoint.gate.clear()  # the server stops after writing the first chunk
    job_id = _start_download(download_env)
    _wait_for_download_to_start(job_id)

    cancel = download_env.client.post("/api/autotag/cancel", json={"job_id": job_id})
    assert cancel.status_code == 202 and cancel.json() == {"cancelled": True}, cancel.text[:300]
    download_env.endpoint.gate.set()  # release: the download implementation should stop at its next cancel-flag check

    _progress, final = _download_events(download_env, job_id)
    assert final["finished"] is True, "one done must be emitted after a cancel too (§4.4)"
    assert final["phase"] == "download"
    assert final["errors"] == [], "a user-initiated cancel is not a failure and must not go into errors"
    assert final["done"] < final["total"], "the cancel did not really take effect: %s/%s" % (final["done"], final["total"])
    assert not download_env.onnx.exists(), "a cancel must not leave half a model behind"
    assert not list(download_env.model_root.rglob("*.part")), "§4.8: the cancel path must delete the .part"


def test_download_of_the_same_model_reuses_the_running_job(download_env) -> None:
    """Double-clicking the same model: the same job_id is returned (no parallel writes to the same .part); it can start again after finishing/cancelling."""
    download_env.endpoint.gate.clear()
    first = _start_download(download_env)
    _wait_for_download_to_start(first)

    again = download_env.client.post("/api/autotag/download", json={"model": MODEL_ID})
    assert again.status_code == 202, again.text[:300]
    assert again.json() == {"job_id": first}, "clicking download again must not start a second job (it would corrupt the same .part)"

    assert download_env.client.post("/api/autotag/cancel", json={"job_id": first}).json() == {"cancelled": True}
    download_env.endpoint.gate.set()
    _progress, final = _download_events(download_env, first)
    assert final["finished"] is True and final["errors"] == []

    # The bookkeeping must be cleared once the job ends: clicking again must yield a new job_id (retry after cancel/failure)
    retry = _start_download(download_env)
    assert retry != first, "the previous job already ended and must not be reused"
    _progress, final = _download_events(download_env, retry)
    assert final["errors"] == [] and final["finished"] is True
    assert download_env.onnx.read_bytes() == ONNX_BYTES, "the retry should really download it"


def test_download_rejects_unknown_model_and_malformed_body(download_env) -> None:
    """400 unknown_model / 422 validation_error: the request body is a frozen contract (§4.7 recognizes only model)."""
    _assert_frozen_error(
        download_env.client.post("/api/autotag/download", json={"model": "no-such-model"}),
        400,
        "unknown_model",
    )
    _assert_frozen_error(download_env.client.post("/api/autotag/download", json={}), 422, "validation_error")
    _assert_frozen_error(
        download_env.client.post("/api/autotag/download", json={"model": MODEL_ID, "force": True}),
        422,
        "validation_error",
    )
    assert download_env.endpoint.requests == [], "a blocked request must not start a download"


def test_download_is_503_without_the_implementation(tmp_path, monkeypatch) -> None:
    """When §4.8's implementation cannot be wired up: 503 download_unavailable, neither 500 nor a silent 404."""
    from kohya_dataset_tagger.api import autotag as autotag_api

    # The constant must really point at autotag/download.py (one wrong dot and the real implementation never connects)
    assert importlib.util.find_spec(autotag_api.DOWNLOAD_MODULE_NAME) is not None, \
        "DOWNLOAD_MODULE_NAME does not resolve to the real implementation: %r" % autotag_api.DOWNLOAD_MODULE_NAME

    monkeypatch.setattr(autotag_api, "_download_module", lambda: None)
    model_root = tmp_path / "models"
    model_root.mkdir()
    config.load(roots=[tmp_path], thumb_cache_dir=tmp_path / "thumbs", model_search_roots=[model_root])
    with TestClient(create_app()) as client:
        assert _model_entry(client, MODEL_ID)["downloadable"] is False, "with no download implementation it must not claim to be downloadable"
        _assert_frozen_error(
            client.post("/api/autotag/download", json={"model": MODEL_ID}), 503, "download_unavailable"
        )


def test_download_without_a_model_root_is_409(tmp_path) -> None:
    """No model search root configured: 409 no_model_root—there is nowhere to put it, so no job doomed to fail should start."""
    config.load(roots=[tmp_path], thumb_cache_dir=tmp_path / "thumbs", model_search_roots=[])
    with TestClient(create_app()) as client:
        assert _model_entry(client, MODEL_ID)["downloadable"] is False, "with no destination root it must not claim to be downloadable"
        _assert_frozen_error(
            client.post("/api/autotag/download", json={"model": MODEL_ID}), 409, "no_model_root"
        )


def test_download_root_never_picks_the_hf_cache(tmp_path, monkeypatch) -> None:
    """Download destination: our own directory wins, and the **HF hub cache directory is never chosen**.

    One eva02 download is 1.26 GB of **flat** <model_id>/model.onnx—HF itself does not recognize that
    shape, and stuffing it into the hub cache only soils someone else's directory. This contract must
    hold in tests too (every destination is under tmp).
    """
    from kohya_dataset_tagger.api import autotag as autotag_api

    monkeypatch.delenv(config.ENV_MODELS, raising=False)
    hub = tmp_path / "huggingface" / "hub"
    hub.mkdir(parents=True)
    own_candidates = [path for path in config.default_model_search_roots() if path.parent.name == "kohya-dataset-tagger"]
    if not own_candidates:
        pytest.skip("this machine has no %%LOCALAPPDATA%%, so config produces no own-model-directory candidate")
    own = own_candidates[0]

    # Our own directory must win even when it comes **after** the HF cache
    config.load(roots=[tmp_path], thumb_cache_dir=tmp_path / "thumbs", model_search_roots=[hub, own])
    assert autotag_api._download_root() == own, "our own model directory must take priority over the HF cache directory"

    # Same for the in-repo models/
    repo_models = REPO / "models"
    config.load(roots=[tmp_path], thumb_cache_dir=tmp_path / "thumbs",
                model_search_roots=[hub, repo_models])
    assert autotag_api._download_root() == repo_models, "the in-repo models/ must also beat the HF cache directory"

    # Only HF cache roots left: rather not download (409) than write into someone else's cache directory
    config.load(roots=[tmp_path], thumb_cache_dir=tmp_path / "thumbs", model_search_roots=[hub])
    assert autotag_api._download_root() is None
    with TestClient(create_app()) as client:
        _assert_frozen_error(
            client.post("/api/autotag/download", json={"model": MODEL_ID}), 409, "no_model_root"
        )
    assert list(hub.rglob("*")) == [], "something was added to the HF cache directory: %s" % list(hub.rglob("*"))
