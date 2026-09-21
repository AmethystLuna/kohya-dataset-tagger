/**
 * Headless behaviour harness for web/js/captionprobe.js.
 *
 * A static test can see that a batch-read endpoint exists; it cannot see that 691
 * images really become two requests instead of 691, or that a response is folded
 * onto the right chunk with the right failure classification. Those two are the
 * whole point of the module, so drive it directly.
 *
 * Usage: node captionprobe_harness.mjs <absolute path to web/>
 */
import { pathToFileURL } from "node:url";
import path from "node:path";

const WEB = process.argv[2];
if (!WEB) throw new Error("usage: node captionprobe_harness.mjs <web dir>");

const mod = await import(pathToFileURL(path.join(WEB, "js", "captionprobe.js")).href);
const { CAPTION_READ_CHUNK, captionProbeChunks, applyCaptionResults } = mod;

function assert(ok, message) {
  if (!ok) throw new Error(message);
}

assert(CAPTION_READ_CHUNK === 500, "the chunk size changed: " + CAPTION_READ_CHUNK);

// 691 (the reference recursive scope) -> two requests, order preserved
const items = Array.from({ length: 691 }, (_, index) => ({ path: "img" + index + ".png" }));
const chunks = captionProbeChunks(items);
assert(chunks.length === 2, "691 images must be two requests, got " + chunks.length);
assert(chunks[0].length === 500 && chunks[1].length === 191, "chunk sizes are wrong");
assert(chunks[0][0].path === "img0.png" && chunks[1][0].path === "img500.png", "chunking lost the order");

assert(captionProbeChunks([]).length === 0, "an empty list must produce no requests");
assert(captionProbeChunks(items, 200).length === 4, "an explicit chunk size must be honoured");
assert(captionProbeChunks(items, 0).length === 2, "a non-positive chunk size must fall back to the default");

// Folding: an ok entry, an ok:false entry, and an entry that never came back
const chunk = [{ path: "a.png" }, { path: "b.png" }, { path: "c.png" }];
const folded = applyCaptionResults(chunk, {
  results: [
    { image: "a.png", ok: true, exists: true, tags: ["1girl"] },
    { image: "b.png", ok: false, error: { code: "not_found", message: "gone" } },
  ],
});
assert(folded.ok.length === 1 && folded.ok[0].item.path === "a.png", "the ok entry was not folded");
assert(folded.failed.length === 2, "expected two failures, got " + folded.failed.length);
assert(folded.failed[0].item.path === "b.png" && folded.failed[0].message === "gone", "the server message was lost");
assert(folded.failed[1].item.path === "c.png" && folded.failed[1].message === "", "a missing entry must fail with an empty message");

// A malformed body must be reported, not thrown
const malformed = applyCaptionResults([{ path: "z.png" }], null);
assert(malformed.ok.length === 0 && malformed.failed.length === 1, "a malformed response must be one failure");

console.log("captionprobe harness OK " + JSON.stringify({ chunks: chunks.length, failures: folded.failed.length }));
