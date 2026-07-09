/**
 * Cross-tab persistence for the optimize auto-download queue.
 *
 * Why: React state in one tab does not survive a refresh/close, and is not
 * visible to sibling tabs. For a workflow of "queue 10-20 optims across 5-6
 * tabs and walk away," that means a refreshed/closed tab silently stops
 * auto-downloading its jobs. localStorage is shared across all tabs of the
 * same origin, so persisting the job list here lets ANY open tab (including
 * one opened fresh after the others were closed) pick up and finish tracking
 * every job ever queued this browser session.
 *
 * A lightweight claim/TTL protocol on the status map keeps two tabs that are
 * BOTH open at the moment a job finishes from double-downloading it — not a
 * true distributed lock, just good enough that the rare loser sees the claim
 * and backs off instead of firing a duplicate set of downloads.
 */

const QUEUE_KEY = 'algotest.optim.queue.v1';
const STATUS_KEY = 'algotest.optim.status.v1';
const MAX_QUEUE_ENTRIES = 300; // generous cap so months of use can't blow past localStorage limits
const STATUS_TTL_MS = 7 * 24 * 60 * 60 * 1000; // prune terminal statuses older than this
const CLAIM_TTL_MS = 20000; // a stale claim (tab crashed mid-download) is up for grabs after this

let _tabId = null;
export function getTabId() {
  if (_tabId) return _tabId;
  try {
    _tabId = (crypto.randomUUID && crypto.randomUUID()) || `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  } catch {
    _tabId = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  }
  return _tabId;
}

function readJson(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return fallback;
    return JSON.parse(raw);
  } catch {
    return fallback;
  }
}

function writeJson(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // localStorage unavailable/full — auto-download degrades to in-memory-only
    // for this tab, which still works as long as the tab itself stays open.
  }
}

export function loadQueue() {
  const arr = readJson(QUEUE_KEY, []);
  return Array.isArray(arr) ? arr : [];
}

/** Append a job (dedup by jobId) and persist. Returns the merged array. */
export function appendToQueue(job) {
  const cur = loadQueue();
  if (cur.some((j) => j.jobId === job.jobId)) return cur;
  const next = [...cur, { ...job, queuedAt: job.queuedAt || Date.now() }];
  // Trim oldest entries beyond the cap (keep the most recent — old completed
  // jobs are still downloadable manually from the UI, just stop being tracked
  // for auto-download once the list is this long).
  const trimmed = next.length > MAX_QUEUE_ENTRIES ? next.slice(next.length - MAX_QUEUE_ENTRIES) : next;
  writeJson(QUEUE_KEY, trimmed);
  return trimmed;
}

/** Permanently drop the given job IDs from the persisted queue (e.g. the
 * user dismissing resolved done/failed entries so the "active" widget list
 * doesn't grow forever). Does NOT touch the download log — clearing clutter
 * from the active view is independent of the download history. */
export function removeJobsFromQueue(jobIds) {
  const drop = new Set(jobIds);
  const next = loadQueue().filter((j) => !drop.has(j.jobId));
  writeJson(QUEUE_KEY, next);
  return next;
}

/** Merge a locally-known job list with whatever localStorage has (sibling
 * tabs may have appended more since we last read), deduped by jobId. */
export function mergeWithStoredQueue(localJobs) {
  const stored = loadQueue();
  const byId = new Map();
  for (const j of localJobs || []) byId.set(j.jobId, j);
  for (const j of stored) if (!byId.has(j.jobId)) byId.set(j.jobId, j);
  return Array.from(byId.values());
}

function loadStatusMap() {
  const obj = readJson(STATUS_KEY, {});
  return obj && typeof obj === 'object' ? obj : {};
}

function saveStatusMap(map) {
  // Prune old terminal entries so the map doesn't grow forever.
  const now = Date.now();
  const pruned = {};
  for (const [jid, s] of Object.entries(map)) {
    if ((s.status === 'done' || s.status === 'failed') && now - (s.at || 0) > STATUS_TTL_MS) continue;
    pruned[jid] = s;
  }
  writeJson(STATUS_KEY, pruned);
}

export function getStatus(jobId) {
  return loadStatusMap()[jobId] || null;
}

/**
 * Attempt to claim jobId for auto-downloading in THIS tab. Returns true if
 * this tab should proceed, false if another tab already owns/finished it.
 * Best-effort: writes a claim, waits briefly, re-reads to detect a
 * simultaneous claim from another tab and yields to the lexicographically
 * earlier (claimedAt, tabId) pair so at most one tab proceeds in practice.
 */
export async function tryClaim(jobId) {
  const map = loadStatusMap();
  const existing = map[jobId];
  const now = Date.now();
  if (existing && (existing.status === 'done' || existing.status === 'failed')) return false;
  if (existing && existing.status === 'claimed' && now - existing.at < CLAIM_TTL_MS && existing.tabId !== getTabId()) {
    return false; // another tab is actively handling it
  }
  const mine = { status: 'claimed', tabId: getTabId(), at: now };
  map[jobId] = mine;
  saveStatusMap(map);

  // Brief settle window to catch a same-tick claim race from a sibling tab.
  await new Promise((res) => setTimeout(res, 150));
  const after = loadStatusMap()[jobId];
  if (!after) return false;
  if (after.tabId === getTabId()) return true;
  // Someone else's claim landed too — the earlier `at` (tie-broken by tabId
  // string) wins; if we lost, back off.
  if (after.at < mine.at || (after.at === mine.at && after.tabId < mine.tabId)) return false;
  return true;
}

export function markStatus(jobId, status, extra) {
  const map = loadStatusMap();
  map[jobId] = { status, tabId: getTabId(), at: Date.now(), ...extra };
  saveStatusMap(map);
}

/** Subscribe to cross-tab changes (fires in OTHER tabs, not the writer). */
export function onStoreChange(cb) {
  const handler = (e) => {
    if (e.key === QUEUE_KEY || e.key === STATUS_KEY || e.key === LOG_KEY) cb();
  };
  window.addEventListener('storage', handler);
  return () => window.removeEventListener('storage', handler);
}

// ── Download log — persistent, visible record of every auto-download ───────
// So the user can see what got downloaded, when, and its file names, without
// having to have watched the (transient) "Auto-download queue" widget at the
// moment it happened. Shared across tabs on this machine the same way the
// queue/status maps are.
const LOG_KEY = 'algotest.optim.downloadLog.v1';
const MAX_LOG_ENTRIES = 500;

export function loadDownloadLog() {
  const arr = readJson(LOG_KEY, []);
  return Array.isArray(arr) ? arr : [];
}

/** Record a completed (or failed) auto-download. `files` is an array of the
 * actual filenames written to disk (or omitted/partial on failure). */
export function appendDownloadLog(entry) {
  const cur = loadDownloadLog();
  const next = [{ ...entry, at: entry.at || Date.now(), tabId: getTabId() }, ...cur];
  const trimmed = next.length > MAX_LOG_ENTRIES ? next.slice(0, MAX_LOG_ENTRIES) : next;
  writeJson(LOG_KEY, trimmed);
  return trimmed;
}

/** Wipe the download log only — does NOT touch the queue or status maps, so
 * clearing history has no effect on which jobs are actively tracked/claimed. */
export function clearDownloadLog() {
  writeJson(LOG_KEY, []);
}
