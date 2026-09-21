"""End-to-end smoke: **real uvicorn + real HTTP + real dataset** (not TestClient).

TestClient goes through in-process calls, bypassing the real HTTP stack, static files, SSE
and timeout behavior. This script uses httpx against a real port and makes **content**
assertions on every response, not just status codes.
Read-only: the data write path is not verified here (that needs a copy).
"""
import json, sys, time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from local_paths import dataset_root

BASE = "http://127.0.0.1:3111"
DATA = dataset_root()
BS = chr(92)
ok = fail = 0

def check(label, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print("  ok    %-42s %s" % (label, detail))
    else:
        fail += 1
        print("  FAIL  %-42s %s" % (label, detail))

with httpx.Client(base_url=BASE, timeout=120.0, follow_redirects=True) as c:
    r = c.get("/api/health")
    check("GET /api/health", r.status_code == 200 and r.json().get("ok") is True, r.text[:80])

    r = c.get("/api/roots")
    roots = r.json().get("roots", [])
    check("GET /api/roots", r.status_code == 200 and len(roots) == 1, str(roots)[:90])

    t0 = time.time()
    r = c.get("/api/fs/list", params={"path": str(DATA)})
    dt = time.time() - t0
    body = r.json()
    check("GET /api/fs/list root dir", r.status_code == 200 and len(body.get("dirs", [])) == 219,
          "%d subdirectories, %.0f ms" % (len(body.get("dirs", [])), dt * 1000))
    check("  root dir itself holds no images (trainer semantics)", body["counts"]["images"] == 0, str(body["counts"]))

    sub = body["dirs"][0]["path"]
    r = c.get("/api/fs/list", params={"path": sub})
    b2 = r.json()
    check("GET /api/fs/list single subdir", r.status_code == 200 and b2["counts"]["images"] > 0,
          "%s -> %s" % (Path(sub).name[:22], b2["counts"]))
    check("  images[] carry has_caption", all(isinstance(i.get("has_caption"), bool) for i in b2["images"]),
          "sum=%d" % sum(1 for i in b2["images"] if i["has_caption"]))

    img = b2["images"][0]["path"]
    r = c.get("/api/images/meta", params={"path": img})
    m = r.json()
    check("GET /api/images/meta", r.status_code == 200 and m["width"] > 0,
          "%dx%d %s" % (m["width"], m["height"], m["mode"]))

    t0 = time.time(); r = c.get("/api/images/thumb", params={"path": img, "size": "grid"}); cold = time.time() - t0
    check("GET /api/images/thumb (cold)", r.status_code == 200 and r.content[:4] == b"RIFF",
          "%.0f ms, %d B WebP" % (cold * 1000, len(r.content)))
    etag = r.headers.get("etag")
    t0 = time.time(); r2 = c.get("/api/images/thumb", params={"path": img, "size": "grid"}); warm = time.time() - t0
    check("GET /api/images/thumb (hit)", r2.status_code == 200 and r2.headers.get("etag") == etag,
          "%.0f ms" % (warm * 1000))

    r = c.get("/api/captions", params={"path": img})
    cap = r.json()
    trainer_split = [t.strip() for t in cap["text"].strip().split(",") if t.strip()]
    check("GET /api/captions", r.status_code == 200 and cap["exists"] is True,
          "%d tags" % len(cap["tags"]))
    check("  tags equal the trainer's split semantics", cap["tags"] == trainer_split, "")

    t0 = time.time()
    r = c.get("/api/tags/frequency", params={"path": str(DATA), "recursive": "true"})
    dt = time.time() - t0
    freq = r.json()
    top = freq["tags"][:3]
    check("GET /api/tags/frequency (whole library, recursive)", r.status_code == 200 and freq["total_images"] == 1653,
          "%.1f s, %d distinct tags, top=%s" % (dt, len(freq["tags"]), [t["tag"] for t in top]))

    # This deliberately uses genshin rather than the beginning of some tag: character tags in
    # the vocabulary look like ganyu_(genshin_impact), and no tag starts with genshin. What users
    # remember is "this character is from Genshin", not its exact prefix in the vocabulary, so
    # autocomplete must be a **substring** match (§4.10). This is how the prefix-matching
    # implementation was caught back then.
    r = c.get("/api/tags/autocomplete", params={"q": "genshin", "limit": 3})
    sug = r.json()["suggestions"]
    check("GET /api/tags/autocomplete (substring)", r.status_code == 200 and len(sug) > 0,
          str([(s["tag"], s["category"]) for s in sug])[:90])

    r = c.get("/api/cache/status", params={"path": str(DATA), "recursive": "true"})
    check("GET /api/cache/status", r.status_code == 200, "stale=%d" % len(r.json().get("stale", [])))

    r = c.get("/api/autotag/models")
    models = r.json()["models"]
    inst = [m["id"] for m in models if m.get("installed")]
    check("GET /api/autotag/models", r.status_code == 200 and inst,
          "%d installed: %s" % (len(inst), inst[:3]))

    for route, params in [("/api/fs/list", {"path": r"C:\Windows"}),
                          ("/api/images/thumb", {"path": "../../etc/passwd"}),
                          ("/api/captions", {"path": r"C:\Windows\win.ini"})]:
        rr = c.get(route, params=params)
        check("403 %s" % route, rr.status_code == 403, str(params)[:40])

    r = c.get("/")
    ct_html = r.headers.get("content-type", "")
    check("GET / static page", r.status_code == 200 and "text/html" in ct_html, ct_html)
    r = c.get("/js/api.js")
    check("GET /js/api.js has ES module MIME", r.status_code == 200 and "javascript" in r.headers.get("content-type", ""),
          r.headers.get("content-type", ""))

print()
print("RESULT ok=%d fail=%d" % (ok, fail))
sys.exit(0 if fail == 0 else 1)
