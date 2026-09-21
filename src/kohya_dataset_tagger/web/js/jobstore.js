/**
 * Remembering which jobs are running, across a page reload.
 *
 * The shelf (jobs.js) made a running job visible from every tab; a reload still wiped it - while the
 * backend kept writing captions. Nothing about the job needs reconstructing: every job stream **replays
 * its complete event log from the start** (the same property that lets an EventSource connect after the
 * POST that started it), so the id is the only thing missing. That is what this stores.
 *
 * sessionStorage, not localStorage: the entry describes what *this tab* is watching, and a brand new tab
 * should not inherit somebody else's job. Every operation tolerates the storage being absent or hostile
 * (a headless fixture, private mode, a full quota) - remembering is a convenience, never a requirement,
 * and a job must not fail because its bookmark could not be written.
 */
const KEY = "kdt.runningJobs";

function storage() {
  try {
    return globalThis.sessionStorage || null;
  } catch (err) {
    return null;
  }
}

/** The jobs this tab was watching, as `{panel, jobId, total, task}`. A corrupt entry reads as "none". */
export function readRunningJobs() {
  const store = storage();
  if (!store) return [];
  let raw = null;
  try {
    raw = store.getItem(KEY);
  } catch (err) {
    return [];
  }
  if (!raw) return [];
  let parsed = null;
  try {
    parsed = JSON.parse(raw);
  } catch (err) {
    return [];
  }
  if (!Array.isArray(parsed)) return [];
  return parsed
    .filter((entry) => entry && typeof entry === "object" && entry.panel && entry.jobId)
    .map((entry) => ({
      panel: String(entry.panel),
      jobId: String(entry.jobId),
      total: Number(entry.total) || 0,
      task: entry.task ? String(entry.task) : "",
    }));
}

function write(entries) {
  const store = storage();
  if (!store) return;
  try {
    if (entries.length === 0) store.removeItem(KEY);
    else store.setItem(KEY, JSON.stringify(entries));
  } catch (err) {
    // A quota error must not break the job it was describing.
  }
}

/** A panel is single-flight, so one entry per panel: the new job replaces that panel's old one. */
export function rememberJob(entry) {
  const source = entry || {};
  if (!source.panel || !source.jobId) return;
  const rest = readRunningJobs().filter((item) => item.panel !== source.panel);
  rest.push({
    panel: String(source.panel),
    jobId: String(source.jobId),
    total: Number(source.total) || 0,
    task: source.task ? String(source.task) : "",
  });
  write(rest);
}

/** Called from the one place each panel ends a job, so no ending can leave a bookmark behind. */
export function forgetJob(panel, jobId) {
  if (!panel) return;
  write(
    readRunningJobs().filter(
      (item) => !(item.panel === panel && (!jobId || item.jobId === String(jobId)))
    )
  );
}
