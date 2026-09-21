/**
 * Headless behaviour harness for web/js/jobstore.js.
 *
 * The promise: a running job's id survives a reload, **one entry per panel** (each panel is single-flight),
 * and nothing in here can break the job it describes - an absent sessionStorage (a headless fixture), a
 * browser that refuses it, a full quota or a corrupt value must all read as "no jobs", never throw.
 *
 * Usage: node jobstore_harness.mjs <absolute path to web/>
 */
import { pathToFileURL } from "node:url";

const WEB = process.argv[2];
if (!WEB) throw new Error("usage: node jobstore_harness.mjs <web dir>");

function assert(condition, message) {
  if (!condition) throw new Error("jobstore harness: " + message);
}

class FakeStorage {
  constructor() { this.map = new Map(); this.hostile = false; }
  getItem(key) {
    if (this.hostile) throw new Error("denied");
    return this.map.has(key) ? this.map.get(key) : null;
  }
  setItem(key, value) {
    if (this.hostile) throw new Error("quota");
    this.map.set(key, String(value));
  }
  removeItem(key) {
    if (this.hostile) throw new Error("denied");
    this.map.delete(key);
  }
}

const mod = await import(pathToFileURL(WEB + "/js/jobstore.js").href);
const { readRunningJobs, rememberJob, forgetJob } = mod;

// --- 1. no storage at all (a headless fixture, or a browser that refuses it) ---
delete globalThis.sessionStorage;
assert(readRunningJobs().length === 0, "no storage must read as no jobs");
rememberJob({ panel: "batch", jobId: "J1", total: 3 });
forgetJob("batch", "J1");
assert(readRunningJobs().length === 0, "no storage must stay empty");

// --- 2. remembering, one entry per panel -------------------------------------
const store = new FakeStorage();
globalThis.sessionStorage = store;
rememberJob({ panel: "batch", jobId: "J1", total: 3 });
rememberJob({ panel: "cache", jobId: "J2", total: 0 });
let jobs = readRunningJobs();
assert(jobs.length === 2, "two panels must be two entries, got " + jobs.length);
assert(jobs.find((job) => job.panel === "batch").jobId === "J1", "the batch job id did not survive");

rememberJob({ panel: "batch", jobId: "J9", total: 5 });
jobs = readRunningJobs();
assert(jobs.length === 2, "a panel is single-flight: its new job replaces its old entry, got " + jobs.length);
assert(jobs.find((job) => job.panel === "batch").jobId === "J9", "the old entry was not replaced");

// --- 3. forgetting -----------------------------------------------------------
forgetJob("batch", "J9");
assert(readRunningJobs().length === 1, "the batch entry must be gone");
forgetJob("cache");
assert(readRunningJobs().length === 0, "forgetJob(panel) without an id must drop that panel's entry");

// --- 4. a corrupt value is not a crash --------------------------------------
store.map.set("kdt.runningJobs", "{not json");
assert(readRunningJobs().length === 0, "a corrupt entry must read as no jobs");
store.map.set("kdt.runningJobs", JSON.stringify({ panel: "batch" }));
assert(readRunningJobs().length === 0, "a non-array value must read as no jobs");
store.map.set("kdt.runningJobs", JSON.stringify([{ jobId: "no-panel" }, null, { panel: "batch", jobId: "J7" }]));
jobs = readRunningJobs();
assert(jobs.length === 1 && jobs[0].jobId === "J7", "entries without a panel or a job id must be dropped");

// --- 5. a hostile storage does not break the job it describes ----------------
store.hostile = true;
assert(readRunningJobs().length === 0, "a throwing read must read as no jobs");
rememberJob({ panel: "scale", jobId: "J8", total: 2 });
forgetJob("scale", "J8");

console.log("jobstore harness OK");
