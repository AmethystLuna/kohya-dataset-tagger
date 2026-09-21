/**
 * Batched caption probing (p0-spec section 4.5 supplement).
 *
 * The tag filter and the visible-scope frequency table need every image's tag
 * list. Asking GET /api/captions once per image was 691 requests on the
 * reference recursive scope, and the old probe re-ran the whole filter after
 * every single response (O(N^2) over the directory, with the gallery repainted
 * each time). This module keeps the reusable half: cut the list into chunks,
 * fold one response onto its chunk, and let the caller refresh once per chunk.
 *
 * Both helpers are pure and exported so the headless harness can exercise them
 * without a DOM or a network.
 */

//: How many captions one POST /api/captions/read asks for. Large enough that a
//: whole directory is one or two requests, small enough that a single response
//: (and its JSON) stays bounded.
export const CAPTION_READ_CHUNK = 500;

/** Cut items into consecutive chunks of at most size (empty input -> no chunks). */
export function captionProbeChunks(items, size) {
  const list = Array.from(items || []);
  const step = Math.max(1, Math.floor(Number(size)) || CAPTION_READ_CHUNK);
  const chunks = [];
  for (let start = 0; start < list.length; start += step) {
    chunks.push(list.slice(start, start + step));
  }
  return chunks;
}

/**
 * Fold one response body onto the chunk it was requested for.
 * Returns {ok: [{item, entry}], failed: [{item, message}]}. A result the server
 * marked ok:false, or one that is missing entirely, is a failure carrying the
 * server's message (possibly empty, never undefined).
 */
export function applyCaptionResults(chunk, payload) {
  const byPath = new Map(
    ((payload && payload.results) || []).map((entry) => [entry.image, entry])
  );
  const ok = [];
  const failed = [];
  for (const item of chunk) {
    const entry = byPath.get(item.path);
    if (!entry || entry.ok !== true) {
      failed.push({ item, message: (entry && entry.error && entry.error.message) || "" });
    } else {
      ok.push({ item, entry });
    }
  }
  return { ok, failed };
}
