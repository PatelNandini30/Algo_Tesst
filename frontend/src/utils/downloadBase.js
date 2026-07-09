// Resolves where a given optimize job's file downloads (ZIP / WOW-MOM /
// per-combo tradesheets) should be fetched from. Files live on the disk of the
// worker that RAN the job: for a LAN remote-worker job that's the remote PC's
// own API, not the main box. The backend maps job -> node and returns the base
// URL. Empty string means "this box ran it" — use same-origin relative URLs
// (unchanged behavior). Result is cached per job_id.
const _cache = {};

export async function resolveDownloadBase(jobId) {
  if (!jobId) return '';
  if (jobId in _cache) return _cache[jobId];
  let base = '';
  try {
    const r = await fetch(`/api/optimize/jobs/${jobId}/download-base`);
    if (r.ok) {
      const d = await r.json();
      base = d.download_base || '';
    }
  } catch {
    // Fall back to same-origin (main box) on any error.
  }
  _cache[jobId] = base;
  return base;
}
