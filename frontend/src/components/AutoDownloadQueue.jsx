/**
 * AutoDownloadQueue — watches every optimize job (however it was queued) and,
 * the instant each finishes, automatically downloads its tradesheets ZIP,
 * WOW/MOM Excel, and Optimization Summary Excel — all PATCHWISE by default,
 * no click required. Always mounted (independent of whether the results
 * panel for any one job is open), so it keeps working even if the user
 * navigates away or the browser tab is left running while the job finishes
 * server-side.
 *
 * SCOPE — THIS PC ONLY: a PC auto-downloads ONLY the optim jobs launched from
 * this browser. It does NOT discover or adopt jobs that ran on other PCs — if
 * you run an optim on PC A, only PC A downloads its files; PC B (running its
 * own optims) never sees or downloads PC A's files, and vice-versa. (An
 * earlier version polled a system-wide GET /api/optimize/jobs list and adopted
 * every job that existed anywhere — that made every open tab download every
 * PC's files, which was wrong; it has been removed.)
 *
 * CROSS-TAB (same PC only): the job list is persisted to localStorage
 * (utils/optimQueueStore) and merged on every poll tick, so a job queued in
 * tab A is picked up by tab B (or a freshly opened/refreshed tab) on the SAME
 * machine too — localStorage is per-origin-per-machine, so this never leaks
 * across PCs. A claim protocol in the same store keeps two simultaneously-open
 * tabs on the same PC from both auto-downloading the same finished job.
 *
 * Props:
 *   jobs        — [{ jobId, ruleConfig, totalCombos, objective }], appended to
 *                 (never replaced) each time THIS tab queues an auto-download
 *                 optim (StrategyBuilder only appends when the user turned the
 *                 Optimize panel's auto-download toggle ON).
 *   patchwise   — default true; which variant to auto-download
 */
import React, { useEffect, useRef, useState } from 'react';
import { CheckCircle2, Loader2, XCircle, ChevronDown, ChevronUp, X } from 'lucide-react';
import { buildSummaryWorkbookBlob, rulesFilename, fetchBlobWithPoll, triggerBlobDownload } from '../utils/optimSummaryExport';
import { mergeWithStoredQueue, tryClaim, markStatus, getStatus, onStoreChange, loadDownloadLog, appendDownloadLog, clearDownloadLog, removeJobsFromQueue } from '../utils/optimQueueStore';
import { resolveDownloadBase } from '../utils/downloadBase';

const POLL_MS = 3000;
// Small stagger between the 3 downloads of one job (and between jobs) so the
// browser doesn't treat a burst of same-tick downloads as a popup flood.
const DOWNLOAD_STAGGER_MS = 600;

async function fetchAllRows(jobId, totalCombos) {
  // No sort_by: the backend's sort_key indexes into each row's `summary` dict
  // (not top-level combo_id), so omitting it just returns natural insertion
  // order — fine here since only the full row SET matters for the export.
  const limit = Math.min(2000, Math.max(100, totalCombos || 500));
  const r = await fetch(`/api/optimize/jobs/${jobId}/results?offset=0&limit=${limit}`);
  if (!r.ok) throw new Error(await r.text());
  const data = await r.json();
  return data.rows || [];
}

/** Downloads all 3 files for a finished job and returns the filenames written
 * (for the persistent download log — see optimQueueStore.appendDownloadLog). */
async function autoDownloadJob(job, patchwise, onLabel) {
  const { jobId, ruleConfig } = job;
  const suffix = patchwise ? '_patchwise' : '_overall';
  const files = [];

  // On-disk artifacts (ZIP / WOW-MOM / patchwise summary CSVs) live on the node
  // that RAN the job — its own remote-api for a remote job, else same-origin.
  const base = await resolveDownloadBase(jobId);

  // 1) Tradesheets ZIP
  onLabel('Downloading tradesheets ZIP…');
  const zipUrl = patchwise
    ? `${base}/api/optimize/jobs/${jobId}/tradesheets.zip?patchwise=true`
    : `${base}/api/optimize/jobs/${jobId}/tradesheets.zip`;
  const { blob: zipBlob, filename: zipName } = await fetchBlobWithPoll(zipUrl);
  const zipFile = zipName || `optimize_${jobId.slice(0, 8)}_tradesheets${suffix}.zip`;
  triggerBlobDownload(zipBlob, zipFile);
  files.push(zipFile);
  await new Promise((res) => setTimeout(res, DOWNLOAD_STAGGER_MS));

  // 2) WOW/MOM Excel
  onLabel('Downloading WOW/MOM…');
  const wmUrl = `${base}/api/optimize/jobs/${jobId}/wow_mom.xlsx${patchwise ? '?patchwise=true' : ''}`;
  const { blob: wmBlob, filename: wmName } = await fetchBlobWithPoll(wmUrl);
  const wmFile = wmName || `optimize_${jobId.slice(0, 8)}_WOW_MOM${suffix}.xlsx`;
  triggerBlobDownload(wmBlob, wmFile);
  files.push(wmFile);
  await new Promise((res) => setTimeout(res, DOWNLOAD_STAGGER_MS));

  // 3) Optimization Summary Excel (patchwise-recomputed metrics when selected —
  // same compute path the manual Export-XLSX button uses).
  onLabel('Downloading summary Excel…');
  let summaryByCombo = null;
  if (patchwise) {
    const sr = await fetch(`${base}/api/optimize/jobs/${jobId}/summary?patchwise=true`);
    if (sr.ok) {
      const sdata = await sr.json();
      summaryByCombo = new Map((sdata.rows || []).map((x) => [String(x.combo_id), x.summary || {}]));
    }
  }
  const rows = await fetchAllRows(jobId, job.totalCombos);
  const wb = await buildSummaryWorkbookBlob(rows, ruleConfig, summaryByCombo);
  const summaryFile = rulesFilename(ruleConfig, jobId, suffix);
  triggerBlobDownload(wb, summaryFile);
  files.push(summaryFile);

  return files;
}

export default function AutoDownloadQueue({ jobs, patchwise = true }) {
  // jobId -> { status, label, error }
  const [track, setTrack] = useState({});
  const [mergedJobs, setMergedJobs] = useState(() => mergeWithStoredQueue(jobs));
  const [collapsed, setCollapsed] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const [view, setView] = useState('active'); // 'active' | 'log'
  const [logVersion, setLogVersion] = useState(0); // bump to force re-read of the log (same-tab writes don't fire 'storage')
  const inFlightRef = useRef(new Set()); // jobIds currently being auto-downloaded
  const pollRef = useRef(null);
  const scheduledRemovalRef = useRef(new Set()); // jobIds already scheduled for auto-removal

  // Terminal entries (done/failed/cancelled) auto-remove themselves from the
  // persisted queue a short while after resolving — the final status is still
  // visible for a bit, but it doesn't linger forever across page refreshes and
  // reappear on every screen (Build included) the way "Clear finished" being a
  // manual action allowed. The download log (View log) keeps the permanent
  // record regardless — this only prunes the "active" queue.
  const AUTO_REMOVE_DELAY_MS = 8000;
  function scheduleAutoRemove(jid) {
    if (scheduledRemovalRef.current.has(jid)) return;
    scheduledRemovalRef.current.add(jid);
    setTimeout(() => {
      removeJobsFromQueue([jid]);
      setMergedJobs((prev) => prev.filter((j) => j.jobId !== jid));
      scheduledRemovalRef.current.delete(jid);
    }, AUTO_REMOVE_DELAY_MS);
  }

  // Re-merge with localStorage whenever this tab's own list grows, AND when a
  // sibling tab (same PC) appends a job (fires the native 'storage' event in
  // OTHER tabs). This is the ONLY source of tracked jobs — jobs this browser
  // launched — so a PC never auto-downloads another PC's optims.
  useEffect(() => {
    setMergedJobs((prev) => mergeWithStoredQueue([...prev, ...(jobs || [])]));
  }, [jobs]);
  useEffect(() => onStoreChange(() => {
    setMergedJobs((prev) => mergeWithStoredQueue(prev));
    setLogVersion((v) => v + 1); // pick up log entries appended by sibling tabs too
  }), []);

  // CRITICAL FIX (2026-07-06): the polling effect used to depend on
  // [mergedJobs, patchwise] and called setMergedJobs(...) at the top of every
  // tick(). mergeWithStoredQueue() ALWAYS returns a brand-new array
  // (Array.from(byId.values())), so every tick produced a new reference,
  // which retriggered this effect (mergedJobs changed) BEFORE the 3s
  // setInterval delay elapsed — cleanup+recreate happens almost immediately,
  // so tick() actually fired in a tight loop with no real delay between
  // calls, not every POLL_MS. This is what caused hundreds of thousands of
  // requests to /api/optimize/jobs/{id} in a single session instead of one
  // every 3 seconds. Fix: the interval's lifecycle must NOT depend on state
  // that tick() itself mutates. mergedJobs/track are mirrored into refs so
  // tick() always reads the latest values without the effect depending on
  // them — the interval is created exactly once (or once per `patchwise`
  // change) and genuinely fires every POLL_MS.
  const mergedJobsRef = useRef(mergedJobs);
  useEffect(() => { mergedJobsRef.current = mergedJobs; }, [mergedJobs]);
  const trackRef = useRef(track);
  useEffect(() => { trackRef.current = track; }, [track]);

  useEffect(() => {
    async function tick() {
      // Pick up anything a sibling tab queued since our last merge, even
      // without a 'storage' event (e.g. this IS the tab that just queued
      // it). Updates the ref directly (not just via setMergedJobs) so this
      // tick's own loop below sees the fresh list immediately, without
      // waiting for a re-render.
      const merged = mergeWithStoredQueue(mergedJobsRef.current);
      mergedJobsRef.current = merged;
      setMergedJobs(merged);
      if (!merged || merged.length === 0) return;

      for (const job of merged) {
        const jid = job.jobId;
        const cur = trackRef.current[jid];
        if (cur && (cur.status === 'done' || cur.status === 'failed')) continue;
        if (inFlightRef.current.has(jid)) continue;

        // A sibling tab (or an earlier visit) may have already resolved this
        // job — reflect that instead of re-downloading.
        const stored = getStatus(jid);
        if (stored && (stored.status === 'done' || stored.status === 'failed')) {
          setTrack((t) => ({
            ...t,
            [jid]: {
              status: stored.status,
              label: stored.status === 'done' ? 'All files downloaded (another tab)' : (stored.error || 'Optimization failed'),
            },
          }));
          scheduleAutoRemove(jid);
          continue;
        }

        try {
          const r = await fetch(`/api/optimize/jobs/${jid}`);
          if (!r.ok) continue;
          const data = await r.json();
          const status = data.status || 'queued';

          if (status === 'success') {
            // Claim before downloading so a sibling tab open at the same
            // moment doesn't also fire the same 3 downloads.
            const won = await tryClaim(jid);
            if (!won) {
              setTrack((t) => ({ ...t, [jid]: { status: 'downloading', label: 'Being downloaded by another tab…' } }));
              continue;
            }
            inFlightRef.current.add(jid);
            setTrack((t) => ({ ...t, [jid]: { status: 'downloading', label: 'Preparing downloads…' } }));
            // Each job only pre-builds ONE artifact set — the download_mode
            // chosen at launch (OptimizePanel.jsx), stored in its own
            // base_payload. Use THAT job's choice (already have it in `data`
            // from the poll above) instead of the queue-wide default prop, so
            // a mixed batch (some patchwise, some overall) downloads the
            // fast, already-built variant for each job.
            const _jobMode = data?.meta?.base_payload?.download_mode;
            const jobPatchwise = _jobMode === 'overall' ? false : _jobMode === 'patchwise' ? true : patchwise;
            try {
              const files = await autoDownloadJob(job, jobPatchwise, (label) =>
                setTrack((t) => ({ ...t, [jid]: { status: 'downloading', label } }))
              );
              markStatus(jid, 'done');
              appendDownloadLog({ jobId: jid, status: 'done', patchwise: jobPatchwise, files });
              setLogVersion((v) => v + 1);
              setTrack((t) => ({ ...t, [jid]: { status: 'done', label: 'All files downloaded' } }));
              scheduleAutoRemove(jid);
            } catch (e) {
              const errMsg = `Auto-download failed: ${e?.message || e}`;
              markStatus(jid, 'failed', { error: errMsg });
              appendDownloadLog({ jobId: jid, status: 'failed', patchwise: jobPatchwise, error: errMsg });
              setLogVersion((v) => v + 1);
              setTrack((t) => ({ ...t, [jid]: { status: 'failed', label: errMsg } }));
              scheduleAutoRemove(jid);
            } finally {
              inFlightRef.current.delete(jid);
            }
          } else if (status === 'failed' || status === 'error') {
            markStatus(jid, 'failed', { error: 'Optimization failed' });
            setTrack((t) => ({ ...t, [jid]: { status: 'failed', label: 'Optimization failed' } }));
            scheduleAutoRemove(jid);
          } else if (status === 'unknown') {
            // Job meta is gone from Redis (deleted on cancel — see DELETE
            // /optimize/jobs/{id}) but the Celery task isn't simply "still
            // queued" (celery_state isn't PENDING/None either) — this is a
            // cancelled/removed job, NOT a running one. Without this branch
            // it fell into the catch-all below and showed "running" forever.
            markStatus(jid, 'failed', { error: 'Job was cancelled or no longer exists' });
            setTrack((t) => ({ ...t, [jid]: { status: 'failed', label: 'Cancelled / no longer exists' } }));
            scheduleAutoRemove(jid);
          } else {
            const done = data.meta?.done ?? 0;
            const total = data.meta?.total ?? job.totalCombos ?? 0;
            const label = status === 'running'
              ? (total ? `Running ${done}/${total}` : 'Running…')
              : 'Queued — waiting for a worker slot';
            setTrack((t) => ({ ...t, [jid]: { status: 'running', label } }));
          }
        } catch {
          // transient fetch error — next tick retries
        }
      }
    }

    tick();
    pollRef.current = setInterval(tick, POLL_MS);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
    // Intentionally NOT depending on mergedJobs/track — see the fix comment
    // above. tick() reads them via refs, so the interval is created exactly
    // once per `patchwise` change and genuinely fires every POLL_MS.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [patchwise]);

  const jobs_ = mergedJobs;
  const logEntries = loadDownloadLog(); // re-read fresh each render; logVersion forces the re-render
  // Show ONLY when there's an actively tracked job (i.e. the user turned the
  // Optimize panel's auto-download toggle ON for a run). Past download-log
  // history must NOT keep the widget visible on its own — a job that finished
  // and left log entries should not resurrect a "(0)" widget on every reload.
  // To review old history, launch another auto-download run and click
  // "View log" from there.
  if (!jobs_ || jobs_.length === 0) return null;
  if (dismissed) return null;

  const anyActive = jobs_.some((j) => {
    const s = track[j.jobId]?.status;
    return s !== 'done' && s !== 'failed';
  });

  return (
    <div
      style={{
        position: 'fixed',
        bottom: 16,
        right: 16,
        zIndex: 2000,
        width: 340,
        maxWidth: 'calc(100vw - 32px)',
        background: 'var(--surface, #fff)',
        border: '1px solid var(--border, #d0d5dd)',
        borderRadius: 10,
        boxShadow: '0 8px 24px rgba(0,0,0,0.18)',
        fontFamily: 'IBM Plex Mono, monospace',
        fontSize: '0.72rem',
        overflow: 'hidden',
      }}
    >
      <div
        style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          padding: '8px 10px', background: 'var(--accent-bg, #eff6ff)', cursor: 'pointer',
        }}
        onClick={() => setCollapsed((c) => !c)}
      >
        <span style={{ fontWeight: 600, color: 'var(--accent, #2563eb)' }}>
          {anyActive ? <Loader2 size={13} className="spin" style={{ marginRight: 6, verticalAlign: 'middle' }} /> : null}
          {view === 'active' ? `Auto-download queue (${jobs_.length})` : `Download log (${logEntries.length})`}
        </span>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
          {view === 'active' && jobs_.some((j) => {
            const s = track[j.jobId]?.status;
            return s === 'done' || s === 'failed';
          }) && (
            <span
              onClick={(e) => {
                e.stopPropagation();
                // Dismiss resolved (done/failed) entries from the ACTIVE view
                // only — permanently removed from the persisted queue so they
                // don't reappear on the next merge, but the download log
                // (actual history) is untouched.
                const resolvedIds = jobs_
                  .filter((j) => {
                    const s = track[j.jobId]?.status;
                    return s === 'done' || s === 'failed';
                  })
                  .map((j) => j.jobId);
                removeJobsFromQueue(resolvedIds);
                setMergedJobs((prev) => prev.filter((j) => !resolvedIds.includes(j.jobId)));
              }}
              style={{ fontSize: '0.62rem', textDecoration: 'underline', color: '#dc2626', cursor: 'pointer' }}
            >
              Clear finished
            </span>
          )}
          {view === 'log' && logEntries.length > 0 && (
            <span
              onClick={(e) => {
                e.stopPropagation();
                clearDownloadLog();
                setLogVersion((v) => v + 1);
              }}
              style={{ fontSize: '0.62rem', textDecoration: 'underline', color: '#dc2626', cursor: 'pointer' }}
            >
              Clear log
            </span>
          )}
          <span
            onClick={(e) => { e.stopPropagation(); setView((v) => (v === 'active' ? 'log' : 'active')); }}
            style={{ fontSize: '0.62rem', textDecoration: 'underline', color: 'var(--accent, #2563eb)', cursor: 'pointer' }}
          >
            {view === 'active' ? 'View log' : 'View active'}
          </span>
          {collapsed ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
          <X
            size={14}
            onClick={(e) => { e.stopPropagation(); setDismissed(true); }}
            style={{ cursor: 'pointer' }}
          />
        </div>
      </div>
      {!collapsed && view === 'active' && (
        <div style={{ maxHeight: 260, overflowY: 'auto' }}>
          {jobs_.map((j) => {
            const t = track[j.jobId] || { status: 'queued', label: 'Queued' };
            const icon =
              t.status === 'done' ? <CheckCircle2 size={13} color="#16a34a" /> :
              t.status === 'failed' ? <XCircle size={13} color="#dc2626" /> :
              <Loader2 size={13} className="spin" color="#2563eb" />;
            return (
              <div
                key={j.jobId}
                style={{
                  display: 'flex', gap: 8, alignItems: 'flex-start',
                  padding: '7px 10px', borderTop: '1px solid var(--border, #eef1f4)',
                }}
              >
                <div style={{ marginTop: 2 }}>{icon}</div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {j.jobId.slice(0, 8)}
                  </div>
                  <div style={{ color: 'var(--text-secondary, #667085)', fontSize: '0.65rem' }}>
                    {t.label}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
      {!collapsed && view === 'log' && (
        <div style={{ maxHeight: 300, overflowY: 'auto' }}>
          {logEntries.length === 0 && (
            <div style={{ padding: '10px', color: 'var(--text-secondary, #667085)' }}>No downloads logged yet.</div>
          )}
          {logEntries.map((entry, i) => {
            const when = new Date(entry.at).toLocaleString();
            const icon = entry.status === 'done'
              ? <CheckCircle2 size={13} color="#16a34a" />
              : <XCircle size={13} color="#dc2626" />;
            return (
              <div
                key={`${entry.jobId}-${entry.at}-${i}`}
                style={{
                  display: 'flex', gap: 8, alignItems: 'flex-start',
                  padding: '7px 10px', borderTop: '1px solid var(--border, #eef1f4)',
                }}
              >
                <div style={{ marginTop: 2 }}>{icon}</div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', gap: 6 }}>
                    <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {entry.jobId.slice(0, 8)} {entry.patchwise ? '(patchwise)' : '(overall)'}
                    </span>
                    <span style={{ color: 'var(--text-secondary, #667085)', fontSize: '0.6rem', flexShrink: 0 }}>{when}</span>
                  </div>
                  {entry.status === 'done' ? (
                    <div style={{ color: 'var(--text-secondary, #667085)', fontSize: '0.62rem' }}>
                      {(entry.files || []).map((f, fi) => <div key={fi} style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{f}</div>)}
                    </div>
                  ) : (
                    <div style={{ color: '#dc2626', fontSize: '0.62rem' }}>{entry.error}</div>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
      <style>{`
        .spin { animation: adq-spin 1s linear infinite; }
        @keyframes adq-spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
      `}</style>
    </div>
  );
}
