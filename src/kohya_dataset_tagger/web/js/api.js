/**
 * Thin fetch wrapper over the frozen HTTP contract (p0-spec.md section 4).
 *
 * ROUTES is the single place where an endpoint path appears. It stores
 * "METHOD /api/path" pairs verbatim so the static contract test can diff this
 * table against the route table in the spec without executing any JavaScript.
 *
 * Every path parameter of the contract is passed as a query string; nothing
 * here resolves or joins filesystem paths client side.
 */

export const ROUTES = Object.freeze({
  health: "GET /api/health",
  roots: "GET /api/roots",
  rootsAdd: "POST /api/roots",
  rootsRemove: "DELETE /api/roots",
  fsList: "GET /api/fs/list",
  fsPick: "GET /api/fs/pick",
  fsPickMkdir: "POST /api/fs/pick/mkdir",
  imageThumb: "GET /api/images/thumb",
  imageMeta: "GET /api/images/meta",
  captions: "GET /api/captions",
  captionsWrite: "PUT /api/captions",
  captionsBatch: "POST /api/captions/batch",
  captionsRead: "POST /api/captions/read",
  captionsRestore: "POST /api/captions/restore",
  captionsBatchRun: "POST /api/captions/batch/run",
  captionsBatchStream: "GET /api/captions/batch/stream",
  captionsBatchCancel: "POST /api/captions/batch/cancel",
  tagsFrequency: "GET /api/tags/frequency",
  tagsAutocomplete: "GET /api/tags/autocomplete",
  autotagModels: "GET /api/autotag/models",
  autotagModelRoots: "GET /api/autotag/model-roots",
  autotagModelRootsAdd: "POST /api/autotag/model-roots",
  autotagModelRootsRemove: "DELETE /api/autotag/model-roots",
  autotagPreview: "POST /api/autotag/preview",
  autotagRun: "POST /api/autotag/run",
  autotagStream: "GET /api/autotag/stream",
  autotagCancel: "POST /api/autotag/cancel",
  autotagDownload: "POST /api/autotag/download",
  cacheStatus: "GET /api/cache/status",
  cacheInvalidate: "POST /api/cache/invalidate",
  cacheInvalidateRun: "POST /api/cache/invalidate/run",
  cacheInvalidateStream: "GET /api/cache/invalidate/stream",
  cacheInvalidateCancel: "POST /api/cache/invalidate/cancel",
  datasetToml: "POST /api/dataset/toml",
  datasetTomlStatus: "GET /api/dataset/toml/status",
  scaleRun: "POST /api/scale/run",
  scaleStream: "GET /api/scale/stream",
  scaleCancel: "POST /api/scale/cancel",
  cropApply: "POST /api/crop/apply",
});

/** Thumbnail sizes accepted by /api/images/thumb. The gallery only ever uses GRID. */
export const THUMB_SIZE = Object.freeze({ GRID: "grid", PREVIEW: "preview" });

/**
 * Caption batch operations accepted by /api/captions/batch (p0-spec section
 * 4.5 item 3, extended with prepend / edit_tags / regex_replace_text).
 * REGEX_REPLACE edits each tag; REGEX_REPLACE_TEXT edits the whole caption.
 */
export const BATCH_OP = Object.freeze({
  REPLACE: "replace",
  APPEND: "append",
  PREPEND: "prepend",
  REMOVE: "remove",
  REGEX_REPLACE: "regex_replace",
  REGEX_REPLACE_TEXT: "regex_replace_text",
  EDIT_TAGS: "edit_tags",
  NORMALIZE: "normalize",
  SORT_DANBOORU: "sort_danbooru",
});

/** Caption write modes accepted by /api/autotag/run. The first entry is the
default the panel preselects (and the backend's default when the field is omitted). */
export const WRITE_MODE = Object.freeze({
  BACKUP_OVERWRITE: "backup_overwrite",
  OVERWRITE: "overwrite",
  APPEND: "append",
  PREPEND: "prepend",
  ONLY_IF_EMPTY: "only_if_empty",
  MERGE: "merge",
});

export class ApiError extends Error {
  constructor(message, code, status, url, params) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
    this.url = url;
    // p0-spec section 4: the frozen error body gained an additive "params".
    // describeError() uses code + params to pick a localized message and falls
    // back to the raw message when there is no translation (or params are
    // incomplete), so nothing is ever dropped.
    this.params = params && typeof params === "object" ? params : {};
  }
}

function buildQuery(query) {
  if (!query) return "";
  const parts = [];
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === "") continue;
    parts.push(encodeURIComponent(key) + "=" + encodeURIComponent(String(value)));
  }
  return parts.length ? "?" + parts.join("&") : "";
}

export function routeUrl(routeKey, query) {
  const route = ROUTES[routeKey];
  if (route === undefined) throw new Error("unknown route: " + routeKey);
  const [method, path] = route.split(" ");
  void method;
  return path + buildQuery(query);
}

async function readError(response) {
  let payload = null;
  try {
    payload = await response.json();
  } catch (err) {
    payload = null;
  }
  if (payload && payload.error && typeof payload.error === "object") {
    return new ApiError(
      String(payload.error.message || response.statusText),
      String(payload.error.code || "http_" + response.status),
      response.status,
      response.url,
      payload.error.params,
    );
  }
  return new ApiError(response.statusText || ("HTTP " + response.status), "http_" + response.status, response.status, response.url);
}

async function request(routeKey, options) {
  const opts = options || {};
  const [method] = ROUTES[routeKey].split(" ");
  const init = { method, headers: { Accept: "application/json" }, credentials: "same-origin" };
  if (opts.body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(opts.body);
  }
  let response;
  try {
    response = await fetch(routeUrl(routeKey, opts.query), init);
  } catch (err) {
    throw new ApiError(err && err.message ? err.message : String(err), "network_error", 0, routeUrl(routeKey, opts.query));
  }
  if (!response.ok) throw await readError(response);
  if (response.status === 204) return null;
  try {
    return await response.json();
  } catch (err) {
    throw new ApiError("invalid JSON in response", "bad_json", response.status, response.url);
  }
}

/** Thumbnail URL. size must be one of THUMB_SIZE; the gallery passes GRID only. */
export function thumbUrl(path, size, cacheBust) {
  return routeUrl("imageThumb", { path, size, v: cacheBust });
}

export const api = {
  health: () => request("health"),
  roots: () => request("roots"),
  /**
   * Section 4.11: adding a root widens the whitelist and is persisted to
   * roots.txt; there is no "persist" switch. 400 not_a_directory when the path
   * is missing or is not a directory, and the answer is idempotent (changed:
   * false) when the path is already whitelisted.
   */
  addRoot: (path) => request("rootsAdd", { body: { path } }),
  /** Section 4.11: DELETE carries its parameter in the query string, not a body. */
  removeRoot: (path) => request("rootsRemove", { query: { path } }),
  listDir: (path, recursive) => request("fsList", { query: { path, recursive: recursive ? "true" : "false" } }),
  /**
   * Section 4.12: list the sub directories of any directory (directories only,
   * no files and no counts) so the dataset root can be chosen graphically.
   * 403 remote_path_config_disabled when the server is not bound to a loopback
   * address and --allow-remote-path-config was not given.
   */
  pick: (path) => request("fsPick", { query: { path } }),
  /**
   * Section 4.12: create one sub directory inside `parent` ("new directory" in
   * the picker). Same loopback gate as GET /api/fs/pick; the answer is
   * {path, created} and an existing directory is idempotent (created: false).
   * 400 bad_name when the name is not a single path component.
   */
  pickMkdir: (parent, name) => request("fsPickMkdir", { body: { parent, name } }),
  imageMeta: (path) => request("imageMeta", { query: { path } }),
  getCaption: (path) => request("captions", { query: { path } }),
  /**
   * Section 4.5 supplement: many captions in one round trip. The tag filter and the
   * visible-scope frequency table need every image's tag list, and one GET per image
   * was 691 requests on the recursive scope; this returns the same entries plus ok.
   */
  readCaptions: (images) => request("captionsRead", { body: { images } }),
  /**
   * The inverse of the write path's backup (section 4.5 (5)): put each image's
   * <caption>.bak back, consume it, and invalidate the text-encoder cache in the
   * same call. This is what lets a batch rewrite be undone instead of confirmed.
   */
  restoreCaptions: (images) => request("captionsRestore", { body: { images } }),
  putCaption: (payload) => request("captionsWrite", { body: payload }),
  batchCaptions: (payload) => request("captionsBatch", { body: payload }),
  /**
   * Section 4.5 supplement: the job form of the batch write. Answer 202
   * {job_id, total}; progress reuses batchStreamUrl(). The synchronous
   * batchCaptions() above stays for the dry-run preview.
   */
  batchCaptionsRun: (payload) => request("captionsBatchRun", { body: payload }),
  /** Section 4.5 supplement: cancel a batch write; always 202 {cancelled}. */
  batchCaptionsCancel: (jobId) => request("captionsBatchCancel", { body: { job_id: jobId } }),
  tagFrequency: (path, recursive) => request("tagsFrequency", { query: { path, recursive: recursive ? "true" : "false" } }),
  tagAutocomplete: (q, limit) => request("tagsAutocomplete", { query: { q, limit } }),
  autotagModels: () => request("autotagModels"),
  /** Section 4.11: every model search root with its source and resolved models. */
  modelRoots: () => request("autotagModelRoots"),
  addModelRoot: (path) => request("autotagModelRootsAdd", { body: { path } }),
  removeModelRoot: (path) => request("autotagModelRootsRemove", { query: { path } }),
  autotagPreview: (payload) => request("autotagPreview", { body: payload }),
  autotagRun: (payload) => request("autotagRun", { body: payload }),
  autotagCancel: (jobId) => request("autotagCancel", { body: { job_id: jobId } }),
  /** Section 4.7: start a model download job -> {job_id}; progress reuses autotagStreamUrl(). */
  autotagDownload: (model) => request("autotagDownload", { body: { model } }),
  cacheStatus: (path, recursive) => request("cacheStatus", { query: { path, recursive: recursive ? "true" : "false" } }),
  cacheInvalidate: (images) => request("cacheInvalidate", { body: { images } }),
  /** Section 4.5 supplement: the job form of the cache rebuild (same contract as the batch job). */
  cacheInvalidateRun: (images) => request("cacheInvalidateRun", { body: { images } }),
  cacheInvalidateCancel: (jobId) => request("cacheInvalidateCancel", { body: { job_id: jobId } }),
  /**
   * Section 4.9: plan a dataset.toml for `root`. Pure computation server side -
   * no file is written; the panel offers copy and download instead.
   * params carries the trainer knobs; the answer is
   * {toml, subset_count, image_count, subsets, warnings, skipped}.
   */
  datasetToml: (root, params) => request("datasetToml", { body: { root, params } }),
  /**
   * Section 4.16: whether the **dataset root** already has a dataset.toml. Read-only - one stat
   * server side, no contents, and it never creates the file. The answer is
   * {root, path, exists, mtime, size}; the strip paints existence only.
   */
  datasetTomlStatus: (root) => request("datasetTomlStatus", { query: { root } }),
  /**
   * Section 4.14: start a background image export/scale job. The request body
   * carries the target folder, the target resolution and the export options;
   * the answer is 202 {job_id, total}. Progress reuses scaleStreamUrl().
   */
  scaleRun: (payload) => request("scaleRun", { body: payload }),
  /** Section 4.14: cancel a running export job; always 202 {cancelled}. */
  scaleCancel: (jobId) => request("scaleCancel", { body: { job_id: jobId } }),
  /**
   * Section 4.17: crop one image and archive the result beside the source. Synchronous on
   * purpose - the confirmation dialog is one image at a time, and the answer carries the
   * archived path, the rectangle that was really applied and the real output size.
   */
  cropApply: (payload) => request("cropApply", { body: payload }),
};

/** SSE endpoint for autotag progress; consumed with EventSource, not fetch. */
export function autotagStreamUrl(jobId) {
  return routeUrl("autotagStream", { job_id: jobId });
}

/** SSE endpoint for the image export job (section 4.14). */
export function scaleStreamUrl(jobId) {
  return routeUrl("scaleStream", { job_id: jobId });
}

/** SSE endpoint for a batch caption write (section 4.5 supplement). */
export function batchStreamUrl(jobId) {
  return routeUrl("captionsBatchStream", { job_id: jobId });
}

/** SSE endpoint for a cache rebuild (section 4.5 supplement). */
export function cacheStreamUrl(jobId) {
  return routeUrl("cacheInvalidateStream", { job_id: jobId });
}
