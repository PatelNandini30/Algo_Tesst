/**
 * OptimizationResults — polls the optimize job, renders the master-summary
 * table, and exports the 37-col Excel.
 *
 * Props:
 *   jobId         — running job id (falsy = closed)
 *   totalCombos   — expected total runs (from enqueue)
 *   objective     — ranking metric key
 *   onClose       — closer
 *   onApplyCombo  — (combo) callback used to push a row's parameter overrides
 *                   back into the StrategyBuilder state
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Download, X, Loader2, Settings, ArrowDown, ArrowUp, FileDown } from 'lucide-react';
import ExcelJS from 'exceljs';
import { MASTER_SUMMARY_COLUMNS } from '../utils/strategyParamSchema';
import buildTradeExcel from '../utils/buildTradeExcel';

const POLL_MS = 1500;
const FETCH_LIMIT = 500;

// Build a human-readable column header from a payload path like
// "legs.0.stopLoss.value" → "Leg 1 SL %".
function friendlyParamLabel(path) {
  if (!path) return '';
  const parts = String(path).split('.');
  // legs.<i>.<field>.value | trigger | move | mode | etc.
  if (parts[0] === 'legs' && parts.length >= 3) {
    const idx = Number(parts[1]);
    const field = parts[2];
    const sub = parts[3] || '';
    const legName = `Leg ${isFinite(idx) ? idx + 1 : '?'}`;
    const friendlyField = {
      stopLoss: 'SL',
      targetProfit: 'TP',
      trailSL: 'Trail',
      slWithBuffer: 'SLB',
      lots: 'Lots',
      strike_interval: 'Strike Interval',
      strike_selection: 'Strike',
    }[field] || field;
    const friendlySub = sub === 'value' ? '%'
      : sub === 'trigger' ? 'Trigger'
      : sub === 'move' ? 'Move'
      : sub === 'mode' ? 'Mode'
      : sub === 'buffer_pct' ? 'Buffer %'
      : sub ? sub : '';
    return [legName, friendlyField, friendlySub].filter(Boolean).join(' ');
  }
  // Top-level keys
  const FRIENDLY_TOP = {
    entry_dte: 'Entry DTE',
    exit_dte: 'Exit DTE',
    slippage_pct: 'Slippage %',
    overall_sl_value: 'Overall SL',
    overall_target_value: 'Overall TP',
    buffer_strike_value: 'Buffer Strike',
    spot_adjustment_pct: 'Spot Adj %',
    spot_adjustment_direction: 'Spot Adj Dir',
  };
  if (FRIENDLY_TOP[path]) return FRIENDLY_TOP[path];
  // Fallback: title-case the dotted path
  return parts
    .map((p) => p.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase()))
    .join(' ');
}

export default function OptimizationResults({
  jobId,
  totalCombos,
  objective: defaultObjective,
  runConfig,
  onClose,
  onApplyCombo,
}) {
  const [meta, setMeta] = useState(null);
  const [jobStatus, setJobStatus] = useState('queued');
  const [rows, setRows] = useState([]);
  const [sortKey, setSortKey] = useState(defaultObjective || 'total_pnl');
  const [order, setOrder] = useState('desc');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [downloadingRows, setDownloadingRows] = useState(new Set());
  const [configExpanded, setConfigExpanded] = useState(false);
  const [zipDownloading, setZipDownloading] = useState(false);
  const [zipProgress, setZipProgress] = useState(null);
  const pollRef = useRef(null);

  const fetchAll = useCallback(async () => {
    if (!jobId) return;
    const url = `/api/optimize/jobs/${jobId}/results?offset=0&limit=${FETCH_LIMIT}&sort_by=${sortKey}&order=${order}`;
    try {
      const r = await fetch(url);
      if (!r.ok) throw new Error(await r.text());
      const data = await r.json();
      setRows(data.rows || []);
      setMeta(data.meta || null);
    } catch (e) {
      setError(String(e.message || e));
    }
  }, [jobId, sortKey, order]);

  // Poll while running.
  useEffect(() => {
    if (!jobId) return undefined;
    setLoading(true);
    fetchAll().finally(() => setLoading(false));
    pollRef.current = setInterval(async () => {
      try {
        const r = await fetch(`/api/optimize/jobs/${jobId}`);
        if (!r.ok) return;
        const data = await r.json();
        setJobStatus(data.status || 'queued');
        setMeta(data.meta || null);
        if (data.status === 'success' || data.status === 'failed') {
          clearInterval(pollRef.current);
          pollRef.current = null;
          fetchAll();
        } else if (data.status !== 'queued') {
          // Refresh partial results so the table fills in as combos complete.
          fetchAll();
        }
      } catch {
        // swallow — polling will retry
      }
    }, POLL_MS);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [jobId, fetchAll]);

  const done = meta?.done ?? 0;
  const total = meta?.total ?? totalCombos ?? 0;
  const progressPct = total > 0 ? Math.round((done / total) * 100) : 0;
  const status = meta?.status || jobStatus || 'queued';
  const eta = meta?.eta_seconds;
  const phase = meta?.phase || 'running';

  // Discover optimized-parameter columns from rows. Each row carries the
  // combo dict under `row.combo` — keys are payload paths like
  // "legs.0.stopLoss.value". We render one column per unique key, in the
  // order they first appear across all rows, immediately after Sr. No.
  const paramColumns = useMemo(() => {
    const seen = [];
    const seenSet = new Set();
    for (const r of rows) {
      const combo = r.combo || {};
      for (const k of Object.keys(combo)) {
        if (!seenSet.has(k)) {
          seenSet.add(k);
          seen.push({ key: k, label: friendlyParamLabel(k) });
        }
      }
    }
    return seen;
  }, [rows]);

  // Detect which leg types are present so conditional columns can be hidden.
  // A leg type is "present" if any row has a non-zero value for its P&L key.
  const legPresence = useMemo(() => {
    let hasCE = false;
    let hasPE = false;
    let hasSpot = false;
    for (const r of rows) {
      const s = r.summary || {};
      if (!hasCE && s.ce_pnl_total != null && Math.abs(Number(s.ce_pnl_total)) > 0.01) hasCE = true;
      if (!hasPE && s.pe_pnl_total != null && Math.abs(Number(s.pe_pnl_total)) > 0.01) hasPE = true;
      if (!hasSpot && s.long_spot_pnl != null && Math.abs(Number(s.long_spot_pnl)) > 0.01) hasSpot = true;
      if (hasCE && hasPE && hasSpot) break;
    }
    return { hasCE, hasPE, hasSpot };
  }, [rows]);

  // Filter MASTER_SUMMARY_COLUMNS based on which leg types are actually present.
  const visibleColumns = useMemo(() => {
    return MASTER_SUMMARY_COLUMNS.filter((c) => {
      if (!c.conditional) return true;
      return legPresence[c.conditional] === true;
    });
  }, [legPresence]);

  // Per-combo timing stats — computed from the elapsed_ms field each row carries.
  const timingStats = useMemo(() => {
    const durs = rows.map((r) => Number(r.elapsed_ms)).filter((d) => isFinite(d) && d > 0);
    if (!durs.length) return null;
    const totalMs = durs.reduce((a, b) => a + b, 0);
    const avg = totalMs / durs.length;
    const min = Math.min(...durs);
    const max = Math.max(...durs);
    return { totalMs, avgMs: avg, minMs: min, maxMs: max, count: durs.length };
  }, [rows]);

  function toggleSort(key) {
    if (sortKey === key) {
      setOrder(order === 'desc' ? 'asc' : 'desc');
    } else {
      setSortKey(key);
      setOrder('desc');
    }
  }

  async function exportExcel() {
    const wb = new ExcelJS.Workbook();
    const ws = wb.addWorksheet('Optimization Summary');
    // Build header with Sr.No first, then optimized-param cols, then Duration, then master summary.
    const headers = [
      'Sr. No.',
      ...paramColumns.map((p) => p.label),
      'Duration (ms)',
      ...visibleColumns.filter((c) => c.key !== 'sr_no').map((c) => c.label),
    ];
    ws.addRow(headers);
    ws.getRow(1).font = { bold: true, color: { argb: 'FFFFFFFF' } };
    ws.getRow(1).fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FF1E3A8A' } };
    ws.getRow(1).alignment = { horizontal: 'center', vertical: 'middle' };
    ws.views = [{ state: 'frozen', xSplit: 1 + paramColumns.length, ySplit: 1 }];

    rows.forEach((row, i) => {
      const summary = row.summary || {};
      const cols = row.combo_columns || {};
      const combo = row.combo || {};
      const arr = [
        i + 1,
        ...paramColumns.map((p) => combo[p.key]),
        row.elapsed_ms != null ? Number(row.elapsed_ms) : null,
        ...visibleColumns.filter((c) => c.key !== 'sr_no').map((c) => {
          if (c.key in cols) return cols[c.key];
          if (c.key in summary) return summary[c.key];
          return summary[c.key];
        }),
      ];
      ws.addRow(arr);
    });
    ws.columns.forEach((col) => {
      col.width = 16;
    });
    const buf = await wb.xlsx.writeBuffer();
    const blob = new Blob([buf], {
      type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `optimize_${jobId}.xlsx`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  }

  async function downloadTradesheets() {
    if (zipDownloading) return;
    setZipDownloading(true);
    setZipProgress({ done: 0, total: 0, elapsed: 0 });
    try {
      // Backend builds ZIP in background.  200 → ready (file body), 202 →
      // still building (poll progress).
      const start = Date.now();
      const maxWaitMs = 20 * 60 * 1000;  // 20 min hard cap
      while (true) {
        const r = await fetch(`/api/optimize/jobs/${jobId}/tradesheets.zip`);
        if (r.status === 200) {
          const blob = await r.blob();
          const filename =
            r.headers.get('x-filename') ||
            `optimize_${jobId.slice(0, 8)}_tradesheets.zip`;
          const a = document.createElement('a');
          a.href = URL.createObjectURL(blob);
          a.download = filename;
          document.body.appendChild(a);
          a.click();
          document.body.removeChild(a);
          setTimeout(() => URL.revokeObjectURL(a.href), 10000);
          return;
        }
        if (r.status === 202) {
          const info = await r.json().catch(() => ({}));
          setZipProgress({
            done: info.done || 0,
            total: info.total || 0,
            elapsed: info.elapsed_seconds || 0,
          });
          if (Date.now() - start > maxWaitMs) {
            alert('ZIP build is taking longer than expected — refresh and try again.');
            return;
          }
          await new Promise((res) => setTimeout(res, 2000));
          continue;
        }
        const err = await r.json().catch(() => ({}));
        alert(err.detail || err.error || 'Failed to download tradesheets');
        return;
      }
    } catch (e) {
      alert(`Failed to download tradesheets: ${e?.message || e}`);
    } finally {
      setZipDownloading(false);
      setZipProgress(null);
    }
  }

  async function cancelJob() {
    if (!jobId) return;
    try {
      await fetch(`/api/optimize/jobs/${jobId}`, { method: 'DELETE' });
    } catch {}
    onClose && onClose();
  }

  async function downloadComboTradesheet(comboId) {
    if (downloadingRows.has(comboId)) return;
    setDownloadingRows((prev) => new Set(prev).add(comboId));
    try {
      const r = await fetch(`/api/optimize/jobs/${jobId}/combo/${comboId}/tradesheet`);
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        alert(err.detail || `Failed to download tradesheet for combo ${comboId}`);
        return;
      }

      // Parse CSV text into array of objects
      const csvText = await r.text();
      const lines = csvText.split('\n').filter(Boolean);
      if (lines.length < 2) {
        alert(`No trade data returned for combo ${comboId}`);
        return;
      }

      // Simple CSV parser — handles quoted fields containing commas/newlines
      const parseCSVLine = (line) => {
        const fields = [];
        let cur = '';
        let inQuote = false;
        for (let i = 0; i < line.length; i++) {
          const ch = line[i];
          if (ch === '"') {
            if (inQuote && line[i + 1] === '"') { cur += '"'; i++; }
            else { inQuote = !inQuote; }
          } else if (ch === ',' && !inQuote) {
            fields.push(cur);
            cur = '';
          } else {
            cur += ch;
          }
        }
        fields.push(cur);
        return fields;
      };

      const headers = parseCSVLine(lines[0]);
      const parsedTrades = lines.slice(1).map(line => {
        const values = parseCSVLine(line);
        const obj = {};
        headers.forEach((h, i) => { obj[h.trim()] = values[i] !== undefined ? values[i].trim() : ''; });
        return obj;
      });

      // Find the corresponding summary and combo label from rows state
      const matchingRow = rows.find(row => (row.combo_id ?? null) === comboId || String(row.combo_id) === String(comboId));
      const summary     = matchingRow?.summary || {};
      const comboLabel  = matchingRow?.combo_label || `Combo ${comboId}`;

      const blob = await buildTradeExcel(parsedTrades, summary, {
        comboLabel,
        runConfig,
        comboValues: matchingRow?.combo || {},
      });

      // Derive filename from Content-Disposition or fallback
      const disposition = r.headers.get('Content-Disposition') || '';
      const match = disposition.match(/filename="?([^"]+)"?/);
      const csvFilename = match ? match[1] : `combo_${comboId}_tradesheet.csv`;
      const xlsxFilename = csvFilename.replace(/\.csv$/i, '.xlsx');

      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = xlsxFilename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
    } catch (e) {
      alert(`Failed to download tradesheet for combo ${comboId}: ${e?.message || e}`);
    } finally {
      setDownloadingRows((prev) => {
        const next = new Set(prev);
        next.delete(comboId);
        return next;
      });
    }
  }

  if (!jobId) return null;

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(0,0,0,0.55)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 1000,
      }}
    >
      <div
        style={{
          width: 'min(1320px, 98vw)',
          maxHeight: '94vh',
          background: 'var(--bg-surface, #fff)',
          color: 'var(--text-primary, #111)',
          borderRadius: 12,
          boxShadow: '0 24px 80px rgba(0,0,0,0.45)',
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
          border: '1px solid var(--border-strong, #d1d5db)',
        }}
      >
        {/* Header */}
        <div
          style={{
            padding: '14px 20px',
            borderBottom: '1px solid var(--border-strong, #e5e7eb)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            gap: 12,
          }}
        >
          <div>
            <h2 style={{ fontSize: 16, fontWeight: 600, margin: 0 }}>
              Optimization Results
            </h2>
            <div style={{ fontSize: 11, opacity: 0.6, marginTop: 2 }}>
              Job <code>{jobId.slice(0, 8)}</code> · ranking by{' '}
              <strong>{sortKey}</strong>
            </div>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            {(status === 'running' || status === 'queued') && (
              <span
                style={{
                  fontSize: 11,
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 6,
                }}
              >
                <Loader2 size={12} className="animate-spin" />
                {status === 'queued'
                  ? 'Queued — waiting for worker…'
                  : phase === 'loading_data'
                  ? 'Loading market data…'
                  : `${done}/${total} (${progressPct}%)${eta ? ` · ETA ~${Math.round(eta / 60)}m` : ''}`}
              </span>
            )}
            {timingStats && (
              <span
                style={{
                  fontSize: 10,
                  fontFamily: 'monospace',
                  background: 'var(--bg-elevated, #f0fdf4)',
                  border: '1px solid var(--border-strong, #d1d5db)',
                  borderRadius: 4,
                  padding: '4px 8px',
                  color: 'var(--text-secondary, #166534)',
                }}
                title="Per-combo runtime — sampled from completed combos so far"
              >
                avg {timingStats.avgMs.toFixed(1)}ms · min {timingStats.minMs.toFixed(1)} · max {timingStats.maxMs.toFixed(1)}
              </span>
            )}
            <button
              onClick={exportExcel}
              disabled={rows.length === 0}
              style={{
                padding: '6px 12px',
                fontSize: 11,
                border: '1px solid var(--border-strong, #d1d5db)',
                borderRadius: 6,
                background: 'transparent',
                cursor: rows.length === 0 ? 'not-allowed' : 'pointer',
                display: 'inline-flex',
                alignItems: 'center',
                gap: 6,
                opacity: rows.length === 0 ? 0.4 : 1,
              }}
            >
              <Download size={12} /> Export XLSX
            </button>
            <button
              onClick={downloadTradesheets}
              disabled={status !== 'success' || rows.length === 0 || zipDownloading}
              style={{
                padding: '6px 12px',
                fontSize: 11,
                border: '1px solid var(--border-strong, #d1d5db)',
                borderRadius: 6,
                background: 'transparent',
                cursor: (status !== 'success' || rows.length === 0 || zipDownloading) ? 'not-allowed' : 'pointer',
                display: 'inline-flex',
                alignItems: 'center',
                gap: 6,
                opacity: (status !== 'success' || rows.length === 0) ? 0.4 : 1,
              }}
              title={
                status !== 'success'
                  ? 'Available after run completes'
                  : zipDownloading
                    ? 'ZIP is being built…'
                    : 'Download all tradesheets as ZIP'
              }
            >
              <Download size={12} />
              {zipDownloading
                ? (zipProgress && zipProgress.total > 0
                    ? `Building ZIP… ${zipProgress.done}/${zipProgress.total} (${Math.round(zipProgress.elapsed)}s)`
                    : `Building ZIP…${zipProgress ? ` ${Math.round(zipProgress.elapsed)}s` : ''}`)
                : 'Download Tradesheets ZIP'}
            </button>
            {status === 'running' && (
              <button
                onClick={cancelJob}
                style={{
                  padding: '6px 12px',
                  fontSize: 11,
                  border: '1px solid var(--border-strong, #d1d5db)',
                  borderRadius: 6,
                  background: 'transparent',
                  cursor: 'pointer',
                }}
              >
                Cancel run
              </button>
            )}
            <button
              onClick={onClose}
              aria-label="Close"
              style={{
                background: 'transparent',
                border: 0,
                cursor: 'pointer',
              }}
            >
              <X size={18} />
            </button>
          </div>
        </div>

        {/* Progress bar */}
        {(status === 'running' || status === 'queued') && (
          <div
            style={{ height: 3, background: 'var(--border, #f1f5f9)' }}
          >
            <div
              style={{
                width: status === 'queued' ? '100%' : `${progressPct}%`,
                height: '100%',
                background: status === 'queued' ? 'var(--border-strong, #94a3b8)' : 'var(--accent, #2563eb)',
                transition: 'width 0.3s ease',
                animation: status === 'queued' ? 'pulse 1.5s ease-in-out infinite' : 'none',
              }}
            />
          </div>
        )}

        {/* Run config bar */}
        {runConfig && (
          <div
            style={{
              borderBottom: '1px solid var(--border-strong, #e5e7eb)',
              background: 'var(--bg-elevated, #f8fafc)',
              fontSize: 11,
              color: 'var(--text-secondary, #6b7280)',
            }}
          >
            <div
              onClick={() => setConfigExpanded((x) => !x)}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 10,
                padding: '6px 16px',
                cursor: 'pointer',
                userSelect: 'none',
              }}
            >
              <span style={{ fontSize: 10, opacity: 0.5 }}>{configExpanded ? '▼' : '▶'}</span>
              <span style={{ fontWeight: 600, color: 'var(--text-primary, #111)' }}>Run Config</span>
              <span style={{ opacity: 0.4 }}>·</span>
              <span>Method: <strong style={{ color: 'var(--text-primary, #111)' }}>{runConfig.methodLabel}</strong></span>
              <span style={{ opacity: 0.4 }}>·</span>
              <span>Objective: <strong style={{ color: 'var(--text-primary, #111)' }}>{runConfig.objectiveLabel}</strong></span>
              <span style={{ opacity: 0.4 }}>·</span>
              <span><strong style={{ color: 'var(--text-primary, #111)' }}>{(runConfig.totalCombos || 0).toLocaleString()}</strong> combos</span>
              <span style={{ marginLeft: 'auto', opacity: 0.4, fontSize: 10 }}>click to {configExpanded ? 'hide' : 'show'} param ranges</span>
            </div>
            {configExpanded && runConfig.paramSpecs && runConfig.paramSpecs.length > 0 && (
              <div
                style={{
                  padding: '4px 16px 10px 32px',
                  display: 'flex',
                  flexWrap: 'wrap',
                  gap: '6px 20px',
                }}
              >
                {runConfig.paramSpecs.map((s) => (
                  <div
                    key={s.path}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 4,
                      fontSize: 11,
                      background: 'var(--bg-surface, #fff)',
                      border: '1px solid var(--border-strong, #e5e7eb)',
                      borderRadius: 4,
                      padding: '2px 8px',
                    }}
                  >
                    <span style={{ fontWeight: 600, color: 'var(--text-primary, #111)' }}>{s.label}</span>
                    <span style={{ opacity: 0.5 }}>:</span>
                    {s.kind === 'enum' ? (
                      <span style={{ fontFamily: 'monospace' }}>{(s.values || []).join(', ')}</span>
                    ) : (
                      <span style={{ fontFamily: 'monospace' }}>
                        {s.min} → {s.max}
                        <span style={{ opacity: 0.6 }}> step {s.step}</span>
                        {s.unit ? <span style={{ opacity: 0.5 }}> {s.unit}</span> : null}
                      </span>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Body */}
        <div style={{ flex: 1, overflow: 'auto', padding: 0 }}>
          {error && (
            <div
              style={{
                margin: 12,
                padding: 8,
                border: '1px solid var(--loss-border, #fecaca)',
                background: 'var(--loss-bg, #fef2f2)',
                color: 'var(--loss, #991b1b)',
                borderRadius: 6,
                fontSize: 12,
              }}
            >
              {error}
            </div>
          )}
          <table
            style={{
              width: '100%',
              borderCollapse: 'collapse',
              fontSize: 11,
              fontFamily: 'IBM Plex Mono, monospace',
            }}
          >
            <thead
              style={{
                position: 'sticky',
                top: 0,
                background: 'var(--bg-surface, #fff)',
                zIndex: 1,
                boxShadow: '0 1px 0 var(--border-strong, #e5e7eb)',
              }}
            >
              <tr>
                <th
                  style={{
                    padding: '8px 6px',
                    borderBottom: '1px solid var(--border-strong, #e5e7eb)',
                    width: 32,
                    textAlign: 'center',
                  }}
                  title="Download individual tradesheet"
                />
                {visibleColumns.map((c, idx) => {
                  const th = (
                    <th
                      key={`${c.key}-${idx}`}
                      onClick={() => c.key !== 'sr_no' && toggleSort(c.key)}
                      style={{
                        padding: '8px 6px',
                        textAlign: 'left',
                        borderBottom: '1px solid var(--border-strong, #e5e7eb)',
                        cursor: c.key === 'sr_no' ? 'default' : 'pointer',
                        whiteSpace: 'nowrap',
                        fontSize: 10,
                        letterSpacing: '0.03em',
                        background:
                          c.key === 'sr_no'
                            ? 'var(--bg-elevated,#f9fafb)'
                            : 'transparent',
                      }}
                    >
                      {c.label}
                      {sortKey === c.key && !c.dup && (
                        order === 'desc' ? (
                          <ArrowDown size={10} style={{ marginLeft: 4 }} />
                        ) : (
                          <ArrowUp size={10} style={{ marginLeft: 4 }} />
                        )
                      )}
                    </th>
                  );
                  // Inject the optimized-param headers right after the Sr. No.
                  if (c.key === 'sr_no') {
                    return [
                      th,
                      ...paramColumns.map((p, pi) => (
                        <th
                          key={`pcol-${p.key}-${pi}`}
                          style={{
                            padding: '8px 6px',
                            textAlign: 'left',
                            borderBottom: '1px solid var(--border-strong, #e5e7eb)',
                            whiteSpace: 'nowrap',
                            fontSize: 10,
                            letterSpacing: '0.03em',
                            background: 'var(--bg-elevated,#fef3c7)',
                            color: '#92400e',
                            fontWeight: 600,
                          }}
                          title={`Optimized parameter: ${p.key}`}
                        >
                          {p.label}
                        </th>
                      )),
                      <th
                        key="duration-header"
                        style={{
                          padding: '8px 6px',
                          textAlign: 'right',
                          borderBottom: '1px solid var(--border-strong, #e5e7eb)',
                          whiteSpace: 'nowrap',
                          fontSize: 10,
                          letterSpacing: '0.03em',
                          background: 'var(--bg-elevated,#dcfce7)',
                          color: 'var(--accent, #166534)',
                          fontWeight: 600,
                        }}
                        title="Wall-clock time spent running this single combination"
                      >
                        Duration (ms)
                      </th>,
                    ];
                  }
                  return th;
                })}
                <th
                  style={{
                    padding: '8px 6px',
                    borderBottom: '1px solid var(--border-strong, #e5e7eb)',
                    textAlign: 'right',
                  }}
                >
                  Apply
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 && (
                <tr>
                  <td
                    colSpan={visibleColumns.length + paramColumns.length + 2}
                    style={{ padding: 20, textAlign: 'center', opacity: 0.5 }}
                  >
                    {status === 'running'
                      ? 'Waiting for first results…'
                      : 'No results.'}
                  </td>
                </tr>
              )}
              {rows.map((row, i) => {
                const summary = row.summary || {};
                const cols = row.combo_columns || {};
                const combo = row.combo || {};
                const comboId = row.combo_id ?? (i + 1);
                const isDownloading = downloadingRows.has(comboId);
                return (
                  <tr key={row.combo_id ?? i}>
                    <td
                      style={{
                        padding: '4px 6px',
                        borderBottom: '1px solid var(--border, #f1f5f9)',
                        textAlign: 'center',
                        width: 32,
                      }}
                    >
                      <button
                        onClick={() => downloadComboTradesheet(comboId)}
                        disabled={isDownloading}
                        title={`Download tradesheet for combo ${comboId}`}
                        style={{
                          background: 'transparent',
                          border: 'none',
                          cursor: isDownloading ? 'wait' : 'pointer',
                          padding: 2,
                          color: 'var(--accent, #2563eb)',
                          opacity: isDownloading ? 0.5 : 1,
                          display: 'inline-flex',
                          alignItems: 'center',
                        }}
                      >
                        {isDownloading
                          ? <Loader2 size={13} className="animate-spin" />
                          : <FileDown size={13} />}
                      </button>
                    </td>
                    {visibleColumns.map((c, idx) => {
                      let v;
                      if (c.key === 'sr_no') v = i + 1;
                      else if (c.key in cols) v = cols[c.key];
                      else v = summary[c.key];
                      const cell = (
                        <td
                          key={`${c.key}-${idx}`}
                          style={{
                            padding: '6px 6px',
                            borderBottom: '1px solid var(--border, #f1f5f9)',
                            whiteSpace: 'nowrap',
                          }}
                        >
                          {formatCell(v)}
                        </td>
                      );
                      if (c.key === 'sr_no') {
                        const dur = row.elapsed_ms;
                        return [
                          cell,
                          ...paramColumns.map((p, pi) => (
                            <td
                              key={`pcol-${p.key}-${pi}`}
                              style={{
                                padding: '6px 6px',
                                borderBottom: '1px solid var(--border, #f1f5f9)',
                                whiteSpace: 'nowrap',
                                background: 'var(--bg-elevated,#fffbeb)',
                                fontWeight: 500,
                              }}
                            >
                              {formatCell(combo[p.key])}
                            </td>
                          )),
                          <td
                            key="duration-cell"
                            style={{
                              padding: '6px 6px',
                              borderBottom: '1px solid var(--border, #f1f5f9)',
                              whiteSpace: 'nowrap',
                              textAlign: 'right',
                              background: 'var(--bg-elevated,#f0fdf4)',
                              fontFamily: 'monospace',
                              fontSize: 10,
                            }}
                          >
                            {dur != null ? Number(dur).toFixed(1) : '—'}
                          </td>,
                        ];
                      }
                      return cell;
                    })}
                    <td
                      style={{
                        padding: '6px 6px',
                        borderBottom: '1px solid var(--border, #f1f5f9)',
                        textAlign: 'right',
                      }}
                    >
                      <button
                        onClick={() =>
                          onApplyCombo && onApplyCombo(row.combo || {})
                        }
                        title="Apply this combination back to the strategy builder"
                        style={{
                          background: 'transparent',
                          border: '1px solid var(--border-strong, #d1d5db)',
                          borderRadius: 4,
                          padding: '2px 6px',
                          cursor: 'pointer',
                          fontSize: 10,
                        }}
                      >
                        <Settings size={10} style={{ marginRight: 4 }} />
                        Apply
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function formatCell(v) {
  if (v == null || v === '') return '—';
  if (typeof v === 'number') {
    if (Number.isInteger(v)) return v.toString();
    return v.toFixed(2);
  }
  return String(v);
}
