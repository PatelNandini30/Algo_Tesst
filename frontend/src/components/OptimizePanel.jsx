/**
 * OptimizePanel — modal for configuring & launching a parameter sweep.
 *
 * Props:
 *   isOpen        — show/hide
 *   onClose       — closer
 *   basePayload   — the live strategy payload (built by StrategyBuilder.buildPayload())
 *   nLegs         — count of legs in the current strategy
 *   onJobQueued   — ({ jobId, totalCombos, objective }) callback after enqueue
 */
import React, { useEffect, useMemo, useState } from 'react';
import { X, Play, AlertCircle, Loader2, Beaker, Check } from 'lucide-react';
import {
  expandSchemaForLegs,
} from '../utils/strategyParamSchema';
import { buildZipNaming, buildRulesInfo } from '../utils/optimRulesInfo';
import Toggle from './ui/Toggle';

const DEFAULT_METHOD = 'exhaustive';
const DEFAULT_OBJECTIVE = 'total_pnl';

/** Read a dotted path ('midcap_spot_adjustment.units') off an object. */
/** Split 'legs[2].spot_adjustment.units' into ['legs', 2, 'spot_adjustment', 'units'].
 *  Plain dot-paths with no brackets are unaffected. */
function _splitPath(path) {
  const out = [];
  for (const seg of String(path).split('.')) {
    const m = seg.match(/^([^[]+)((?:\[\d+\])*)$/);
    if (!m) { out.push(seg); continue; }
    out.push(m[1]);
    for (const idx of m[2].matchAll(/\[(\d+)\]/g)) out.push(Number(idx[1]));
  }
  return out;
}

function _getByDotPath(obj, path) {
  // A plain `.split('.')` treated "legs[2]" as ONE literal key — never a
  // property on the object (the array lives at `.legs`, indexed by 2) — so
  // EVERY per-leg unitPayloadPath ('legs[I].spot_adjustment.units') read back
  // as undefined regardless of what the user actually set, and the panel
  // silently fell back to 'percent' (see the unitChoice seeding effect below).
  return _splitPath(path).reduce((o, k) => (o == null ? undefined : o[k]), obj);
}

/** Write a bracket-aware path, cloning each nested level (object OR array) so
 *  the source object is never mutated. */
function _setByDotPath(root, path, value) {
  const keys = _splitPath(path);
  const last = keys.pop();
  let cur = root;
  for (const k of keys) {
    const isArrIdx = typeof k === 'number';
    const existing = cur[k];
    cur[k] = existing != null && typeof existing === 'object'
      ? (Array.isArray(existing) ? [...existing] : { ...existing })
      : (isArrIdx ? [] : {});
    cur = cur[k];
  }
  cur[last] = value;
  return root;
}

function combosForSpec(spec) {
  if (spec.kind === 'enum') return (spec.values || []).length;
  const { min, max, step } = spec;
  if (!Number.isFinite(min) || !Number.isFinite(max) || !Number.isFinite(step) || step <= 0) return 0;
  if (max < min) return 0;
  return Math.floor((max - min) / step + 1e-9) + 1;
}

function totalCombos(selected) {
  let total = 1;
  for (const s of selected) total *= combosForSpec(s);
  return total;
}

function specsToPayload(specs) {
  return specs.map((s) => {
    if (s.kind === 'enum') {
      return { path: s.path, kind: 'enum', values: s.values };
    }
    return {
      path: s.path,
      kind: 'range',
      min: Number(s.min),
      max: Number(s.max),
      step: Number(s.step),
    };
  });
}

export default function OptimizePanel({
  isOpen,
  onClose,
  basePayload,
  nLegs,
  onJobQueued,
  // Lifted state — owned by StrategyBuilder so settings survive panel close/reopen
  checked, setChecked,
  savedValues, setSavedValues,
  unitChoice = {}, setUnitChoice,
  method, setMethod,
  sampleN, setSampleN,
  algorithm, setAlgorithm,
  objective, setObjective,
  parallelism, setParallelism,
  filterName,
  nodeId, // LAN remote-worker routing (see remote-worker/); null = run locally
}) {
  // Midcap params (Global — Midcap Spot Adjustment) only appear when the
  // strategy actually has a Midcap leg.
  const _hasMidcapLeg = Array.isArray(basePayload?.midcap_legs) && basePayload.midcap_legs.length > 0;
  // MIDCPNIFTY is a TRADED index leg (unlike the Midcap100 overlay, which lives
  // in midcap_legs), so gate its spot-adjustment axes on a real leg carrying
  // index === 'MIDCPNIFTY'. Absent that leg the group is hidden entirely.
  const _hasMidcpniftyLeg = (Array.isArray(basePayload?.legs) ? basePayload.legs : []).some(
    l => l && String(l.segment || '').toLowerCase() !== 'midcap100'
      && String(l.index || basePayload?.index || '').toUpperCase() === 'MIDCPNIFTY');
  // Per-leg strike mode (STRIKE_TYPE / PCT_OF_ATM / STRADDLE_WIDTH / REL_LEG / …)
  // exactly as chosen in the backtest builder, so the optimizer only offers the
  // strike params that actually apply to each leg (hides the confusing rest).
  const _legStrikeModes = useMemo(() => {
    const legs = Array.isArray(basePayload?.legs) ? basePayload.legs : [];
    return legs.map(l => String(l?.strike_selection?.type || '').toUpperCase());
  }, [basePayload]);
  // A futures leg has no strike, so every Strike-group sweep (offset / wing /
  // strike-type / straddle-width / straddle-direction — all tagged strikeModes)
  // is a no-op for it and just inflates the grid. Hide them for futures legs;
  // the Expiry window param (no strikeModes) stays valid (the future rolls).
  const _legIsFuture = useMemo(() => {
    const legs = Array.isArray(basePayload?.legs) ? basePayload.legs : [];
    return legs.map(l => String(l?.segment || '').toUpperCase() === 'FUTURES');
  }, [basePayload]);
  // Per-leg "own" spot adjustment is only sweepable for a leg that has its OWN
  // adjustment turned on in the Strategy Builder (leg.spot_adjustment.enabled).
  // Otherwise the per-leg SA axes are hidden — you opt the leg in first, then
  // sweep its threshold/direction/unit here.
  const _legHasOwnSA = useMemo(() => {
    const legs = Array.isArray(basePayload?.legs) ? basePayload.legs : [];
    return legs.map(l => Boolean(l?.spot_adjustment?.enabled));
  }, [basePayload]);
  // Per-leg contract-gap-schedule axis is only sweepable for a leg that actually
  // carries a per-contract schedule (Per-Contract Schedule set in StrategyBuilder).
  const _legHasYearlySchedule = useMemo(() => {
    const legs = Array.isArray(basePayload?.legs) ? basePayload.legs : [];
    return legs.map(l => Array.isArray(l?.yearly_contract_schedule) && l.yearly_contract_schedule.length > 0);
  }, [basePayload]);
  const allParams = useMemo(
    () => expandSchemaForLegs(nLegs || 1)
      .filter(p => !p.midcapOnly || _hasMidcapLeg)
      .filter(p => !p.midcpniftyOnly || _hasMidcpniftyLeg)
      // Per-leg spot-adjustment axes show ONLY when the leg's own adjustment is
      // enabled in the strategy (mirrors strike-mode gating below).
      .filter(p => !p.requiresLegSpotAdj || _legHasOwnSA[p.legIndex])
      // Per-leg gap-schedule axis shows ONLY when the leg has a per-contract schedule.
      .filter(p => !p.requiresLegYearlySchedule || _legHasYearlySchedule[p.legIndex])
      // Strike-mode gating: a param tagged with strikeModes only shows when the
      // leg's actual strike mode matches. If we can't resolve the leg's mode
      // (no basePayload legs yet), fall back to showing it rather than hiding.
      .filter(p => {
        if (!Array.isArray(p.strikeModes)) return true;
        // Futures leg: hide the whole Strike group (no strike exists).
        if (_legIsFuture[p.legIndex]) return false;
        const mode = _legStrikeModes[p.legIndex];
        if (!mode) return true;
        return p.strikeModes.includes(mode);
      })
      // Option-only per-leg axes (e.g. per-leg spot adjustment) are hidden for a
      // futures leg, which has no per-leg spot-adjustment of its own.
      .filter(p => !(p.optionOnly && _legIsFuture[p.legIndex])),
    [nLegs, _hasMidcapLeg, _hasMidcpniftyLeg, _legStrikeModes, _legIsFuture, _legHasOwnSA],
  );
  const grouped = useMemo(() => {
    const m = new Map();
    for (const p of allParams) {
      if (!m.has(p.group)) m.set(p.group, []);
      m.get(p.group).push(p);
    }
    return Array.from(m.entries());
  }, [allParams]);

  const [objectives, setObjectives] = useState([]);
  const [submitting, setSubmitting] = useState(false);
  // Auto-download opt-in — jobs launched with this on are adopted by
  // AutoDownloadQueue (this tab, sibling tabs on this PC). Persisted to
  // localStorage (per-browser) so it remembers your last choice instead of
  // silently resetting to OFF every time this panel is reopened — that reset
  // was confusing: turning it on for one run gave no indication it wouldn't
  // carry over to the next, so a later run could look "broken" when it was
  // actually just off again (see auto_download=False on jobs the user
  // expected to auto-download, 2026-07-06).
  const _AUTO_DL_KEY = 'algotest.optim.autoDownload.v1';
  const [autoDownload, setAutoDownload] = useState(() => {
    try { return localStorage.getItem(_AUTO_DL_KEY) === '1'; } catch { return false; }
  });
  const handleAutoDownloadToggle = (v) => {
    setAutoDownload(v);
    try { localStorage.setItem(_AUTO_DL_KEY, v ? '1' : '0'); } catch { /* ignore */ }
  };
  // Which download artifact (ZIP + WOW/MOM + summary) the worker actually
  // builds after the sweep — building only ONE instead of always building
  // both roughly halves finalization time on large sweeps. Defaults to
  // 'patchwise' if the user never touches this, per the standing rule that
  // patchwise is the safe default when nothing is explicitly chosen.
  const _DL_MODE_KEY = 'algotest.optim.downloadMode.v1';
  const [downloadMode, setDownloadMode] = useState(() => {
    try {
      const v = localStorage.getItem(_DL_MODE_KEY);
      return v === 'overall' ? 'overall' : 'patchwise';
    } catch { return 'patchwise'; }
  });
  const handleDownloadModeChange = (v) => {
    setDownloadMode(v);
    try { localStorage.setItem(_DL_MODE_KEY, v); } catch { /* ignore */ }
  };
  const [error, setError] = useState(null);
  const [preview, setPreview] = useState(null);
  const [cpuInfo, setCpuInfo] = useState({ cpu_count: 8, default_parallelism: 4 });
  // Strike-shift override: defaults to whatever StrategyBuilder put in
  // basePayload.strike_shift_max_steps. User can override per optimization run.
  const [strikeShiftOverride, setStrikeShiftOverride] = useState(
    typeof basePayload?.strike_shift_max_steps === 'number'
      ? basePayload.strike_shift_max_steps
      : 1
  );
  // %/pts choice for params that declare unitOptions (the NIFTY + Midcap spot
  // adjustment thresholds). Keyed by param path. Seeded below from whatever
  // the strategy builder already had, so opening this panel never silently
  // flips the base strategy's units.


  useEffect(() => {
    if (!isOpen) return;
    setUnitChoice((prev) => {
      let next = prev;
      for (const p of allParams) {
        if (!p.unitPayloadPath || prev[p.path]) continue;
        const fromBase = String(_getByDotPath(basePayload, p.unitPayloadPath) || '').toLowerCase();
        if (next === prev) next = { ...prev };
        next[p.path] = fromBase === 'points' ? 'points' : 'percent';
      }
      // Return the SAME object when nothing was seeded — basePayload is rebuilt
      // on every StrategyBuilder render, so a fresh object here would re-render
      // this panel forever.
      return next;
    });
  }, [isOpen, allParams, basePayload]);

  // The unit a param is currently being swept in, plus its matching preset.
  function unitFor(p) {
    return unitChoice[p.path] || 'percent';
  }

  /** Schema defaults for a param, resolved to the unit it's currently set to
   *  (so a points-mode axis seeds 50–500, not the percent preset 0.5–5). */
  function defaultsFor(p) {
    const opt = (p.unitOptions || []).find((o) => o.key === unitFor(p));
    return {
      path: p.path,
      label: p.label,
      kind: p.kind,
      min: opt ? opt.min : p.min,
      max: opt ? opt.max : p.max,
      step: opt ? opt.step : p.step,
      values: p.values ? [...p.values] : undefined,
      unit: opt ? opt.unit : p.unit,
    };
  }

  function changeUnit(p, key) {
    const opt = (p.unitOptions || []).find((o) => o.key === key);
    if (!opt) return;
    setUnitChoice((prev) => ({ ...prev, [p.path]: key }));
    // Swap in the range preset for that unit — 0.5–5 (%) and 50–500 (pts) are
    // not interchangeable numbers, so carrying the old range across a unit
    // switch would silently produce a nonsense sweep.
    setSavedValues((sv) => ({
      ...sv,
      [p.path]: {
        ...(sv[p.path] || { path: p.path, label: p.label, kind: p.kind }),
        path: p.path,
        label: p.label,
        kind: p.kind,
        min: opt.min,
        max: opt.max,
        step: opt.step,
        unit: opt.unit,
      },
    }));
  }

  useEffect(() => {
    if (!isOpen) return;
    fetch('/api/optimize/objectives')
      .then((r) => r.json())
      .then((d) => setObjectives(d.objectives || []))
      .catch(() => setObjectives([]));
    fetch('/api/optimize/system-info')
      .then((r) => r.json())
      .then((d) => {
        const info = { cpu_count: d.cpu_count || 8, default_parallelism: d.default_parallelism || 4 };
        setCpuInfo(info);
        setParallelism((cur) => (cur == null ? info.default_parallelism : cur));
      })
      .catch(() => {});
  }, [isOpen]);

  // Derive the active selected specs (only checked params)
  const selectedList = useMemo(() => {
    return allParams
      .filter((p) => checked[p.path])
      .map((p) => savedValues[p.path] || defaultsFor(p));
    // defaultsFor reads unitChoice, so the memo must track it too.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [allParams, checked, savedValues, unitChoice]);

  const gridSize = useMemo(() => totalCombos(selectedList), [selectedList]);
  // Planned runs = what the sweep will ACTUALLY execute. The raw product counted
  // here in the browser can be far larger than that: parameters gated by another
  // (a leg's spot-adjustment pct/direction under its on/off, or straddle
  // direction under a 0 multiplier) collapse to one combo, and only the server
  // knows that rule. A real 18-axis grid reads 262,144 here but runs 63,504.
  // So prefer the server's count once Validate has been run, and keep the local
  // product purely as the "Grid size" figure beside it.
  const previewRuns =
    preview && Number.isFinite(Number(preview.planned_runs))
      ? Number(preview.planned_runs)
      : null;
  const localPlanned =
    method === 'exhaustive' ? gridSize : Math.min(gridSize || sampleN, Number(sampleN) || 0);
  const plannedRuns = previewRuns != null ? previewRuns : localPlanned;
  // Prefer the server's estimate: it is calibrated from this box's completed
  // jobs and counts the serial ZIP/WOW-MOM/summary tail, which the flat 0.2 s
  // guess here ignores entirely. Falls back to the local guess before Validate.
  const estSeconds =
    preview && Number.isFinite(Number(preview.estimated_seconds))
      ? Number(preview.estimated_seconds)
      : Math.round(plannedRuns * 0.2);

  // A server count describes the grid it was asked about. Editing the sweep
  // afterwards invalidates it, so drop it — otherwise "Planned runs" would keep
  // showing the old number while "Grid size" moves, which reads as a bug and,
  // worse, hides that the new grid was never validated.
  useEffect(() => {
    setPreview(null);
  }, [selectedList, method, sampleN]);

  function toggleParam(p) {
    setChecked((prev) => {
      const next = { ...prev };
      if (next[p.path]) {
        // Unchecking — values already in savedValues, just uncheck
        delete next[p.path];
      } else {
        // Checking — initialize savedValues if not already saved
        setSavedValues((sv) => {
          if (sv[p.path]) return sv; // keep existing saved values
          return { ...sv, [p.path]: defaultsFor(p) };
        });
        next[p.path] = true;
      }
      return next;
    });
  }

  function updateField(path, field, value) {
    setSavedValues((sv) => ({
      ...sv,
      [path]: { ...sv[path], [field]: value },
    }));
  }

  function updateEnum(path, csv) {
    const arr = String(csv || '')
      .split(',')
      .map((x) => x.trim())
      .filter(Boolean);
    setSavedValues((sv) => ({ ...sv, [path]: { ...sv[path], values: arr } }));
  }

  // Merge the strike-shift override into basePayload so it's applied per run.
  function basePayloadWithOverrides() {
    const out = {
      ...(basePayload || {}),
      strike_shift_max_steps: Number(strikeShiftOverride) || 0,
      download_mode: downloadMode,
    };
    // Pin the %/pts unit for every spot-adjustment axis that is actually being
    // swept. Without this the base payload's units decide, and a points sweep
    // (e.g. 150) launched off a percent strategy would be read as 150% and
    // clamped to 5% by the engine.
    for (const p of allParams) {
      if (!p.unitPayloadPath || !checked[p.path]) continue;
      _setByDotPath(out, p.unitPayloadPath, unitFor(p));
    }
    return out;
  }

  async function launch() {
    setSubmitting(true);
    setError(null);
    try {
      const _baseForNaming = basePayloadWithOverrides();
      const body = {
        base_payload: _baseForNaming,
        param_specs: specsToPayload(selectedList),
        method,
        sample_n: method === 'exhaustive' ? null : Number(sampleN) || 0,
        objective,
        algorithm: method === 'smart' ? algorithm : null,
        parallelism: parallelism || cpuInfo.default_parallelism,
        zip_naming: buildZipNaming(_baseForNaming, filterName, selectedList),
        node_id: nodeId || null,
        auto_download: autoDownload,
      };
      const res = await fetch('/api/optimize/jobs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const t = await res.text();
        throw new Error(t || `HTTP ${res.status}`);
      }
      const data = await res.json();
      if (onJobQueued) {
        const objLabel = (objectives.find((o) => o.name === objective) || {}).label || objective;
        onJobQueued({
          jobId: data.job_id,
          totalCombos: data.total_combos,
          objective: data.objective,
          method: data.method,
          runConfig: {
            paramSpecs: selectedList,
            method,
            methodLabel: { exhaustive: 'Exhaustive', random: `Random (N=${sampleN})`, smart: `Smart · ${algorithm}` }[method] || method,
            sampleN: method !== 'exhaustive' ? Number(sampleN) : null,
            algorithm: method === 'smart' ? algorithm : null,
            objective,
            objectiveLabel: objLabel,
            totalCombos: data.total_combos,
          },
          ruleConfig: buildRulesInfo(_baseForNaming, filterName, selectedList),
          autoDownload,
        });
      }
      onClose && onClose();
    } catch (e) {
      setError(String(e.message || e));
    } finally {
      setSubmitting(false);
    }
  }

  async function runPreview() {
    setError(null);
    try {
      const body = {
        base_payload: basePayloadWithOverrides(),
        param_specs: specsToPayload(selectedList),
        method,
        sample_n: method === 'exhaustive' ? null : Number(sampleN) || 0,
      };
      const res = await fetch('/api/optimize/preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        setPreview(null);
        setError(await res.text());
        return;
      }
      setPreview(await res.json());
    } catch (e) {
      setError(String(e.message || e));
    }
  }

  if (!isOpen) return null;

  const inputStyle = {
    background: 'var(--bg-input, #fff)',
    color: 'var(--text-primary, #111)',
    border: '1px solid var(--border-strong, #d1d5db)',
    borderRadius: 4,
  };

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
          width: 'min(960px, 96vw)',
          maxHeight: '92vh',
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
            background: 'var(--bg-elevated, #f9fafb)',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <Beaker size={18} color="var(--accent, #2563eb)" />
            <h2 style={{ fontSize: 16, fontWeight: 600, margin: 0 }}>
              Optimize Strategy
            </h2>
            <span
              style={{
                fontFamily: 'IBM Plex Mono, monospace',
                fontSize: '0.6rem',
                opacity: 0.6,
                marginLeft: 8,
              }}
            >
              AmiBroker-style parameter sweep
            </span>
          </div>
          <button
            onClick={onClose}
            aria-label="Close"
            style={{ background: 'transparent', border: 0, cursor: 'pointer', color: 'var(--text-primary, #111)' }}
          >
            <X size={18} />
          </button>
        </div>

        {/* Body */}
        <div style={{ display: 'flex', minHeight: 0, flex: 1 }}>
          {/* Left: parameter picker */}
          <div
            style={{
              width: '52%',
              borderRight: '1px solid var(--border-strong, #e5e7eb)',
              overflowY: 'auto',
              padding: 16,
            }}
          >
            <div style={{ fontSize: 12, opacity: 0.7, marginBottom: 8 }}>
              Tick parameters to include in the sweep. Your typed values are
              remembered even when unchecked.
            </div>
            {grouped.map(([groupName, items]) => (
              <div key={groupName} style={{ marginBottom: 18 }}>
                <div
                  style={{
                    fontSize: 11,
                    fontWeight: 700,
                    textTransform: 'uppercase',
                    letterSpacing: '0.06em',
                    color: 'var(--text-muted, #6b7280)',
                    marginBottom: 6,
                  }}
                >
                  {groupName}
                </div>
                {items.map((p) => {
                  const isChecked = Boolean(checked[p.path]);
                  const spec = savedValues[p.path] || defaultsFor(p);
                  return (
                    <div
                      key={p.path}
                      style={{
                        padding: '6px 0',
                        borderBottom: '1px solid var(--border-strong, #eee)',
                        opacity: isChecked ? 1 : 0.65,
                      }}
                    >
                      <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
                        <input
                          type="checkbox"
                          checked={isChecked}
                          onChange={() => toggleParam(p)}
                        />
                        <span style={{ fontSize: 12, fontWeight: 500 }}>{p.label}</span>
                        {p.unitOptions ? (
                          // %/pts selector — sweeps the threshold as a percent
                          // move or an absolute index-point move.
                          <span style={{ display: 'inline-flex', gap: 2, marginLeft: 4 }}>
                            {p.unitOptions.map((o) => {
                              const active = unitFor(p) === o.key;
                              return (
                                <button
                                  key={o.key}
                                  type="button"
                                  title={o.key === 'points' ? 'Absolute index points' : 'Percent move'}
                                  onClick={(e) => {
                                    // Inside a <label> — don't let the click
                                    // fall through and toggle the checkbox.
                                    e.preventDefault();
                                    e.stopPropagation();
                                    changeUnit(p, o.key);
                                  }}
                                  style={{
                                    fontSize: 10,
                                    lineHeight: 1,
                                    padding: '3px 6px',
                                    borderRadius: 4,
                                    cursor: 'pointer',
                                    border: `1px solid ${active ? 'var(--accent, #2563eb)' : 'var(--border-strong, #ddd)'}`,
                                    background: active ? 'var(--accent, #2563eb)' : 'transparent',
                                    color: active ? '#fff' : 'inherit',
                                    opacity: active ? 1 : 0.6,
                                  }}
                                >
                                  {o.unit}
                                </button>
                              );
                            })}
                          </span>
                        ) : p.unit && (
                          <span style={{ fontSize: 10, opacity: 0.5, marginLeft: 4 }}>
                            ({p.unit})
                          </span>
                        )}
                        {isChecked && (
                          <span
                            style={{
                              marginLeft: 'auto',
                              fontSize: 10,
                              color: 'var(--accent, #2563eb)',
                              fontFamily: 'IBM Plex Mono, monospace',
                            }}
                          >
                            {combosForSpec(spec)} values
                          </span>
                        )}
                      </label>
                      {/* Show range/enum editor for ALL params that have saved values or are checked */}
                      {(isChecked || savedValues[p.path]) && spec.kind === 'range' && (
                        <div style={{ display: 'flex', gap: 6, marginTop: 6, marginLeft: 24 }}>
                          <RangeNum
                            label="min"
                            value={spec.min}
                            onChange={(v) => updateField(p.path, 'min', v)}
                          />
                          <RangeNum
                            label="max"
                            value={spec.max}
                            onChange={(v) => updateField(p.path, 'max', v)}
                          />
                          <RangeNum
                            label="step"
                            value={spec.step}
                            onChange={(v) => updateField(p.path, 'step', v)}
                          />
                        </div>
                      )}
                      {(isChecked || savedValues[p.path]) && spec.kind === 'enum' && (
                        <div style={{ marginTop: 6, marginLeft: 24 }}>
                          <EnumChips
                            options={p.values || spec.values || []}
                            selected={spec.values || []}
                            valueLabels={p.valueLabels}
                            onChange={(arr) => {
                              setSavedValues((sv) => ({ ...sv, [p.path]: { ...sv[p.path], values: arr } }));
                            }}
                          />
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            ))}
          </div>

          {/* Right: configuration */}
          <div style={{ flex: 1, padding: 16, display: 'flex', flexDirection: 'column', overflowY: 'auto' }}>
            <Section title="Search Method">
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                <RadioRow
                  name="method"
                  value="exhaustive"
                  current={method}
                  onChange={setMethod}
                  label="Exhaustive grid"
                  hint="Try every combination. Best for ≤ 100k combos."
                />
                <RadioRow
                  name="method"
                  value="random"
                  current={method}
                  onChange={setMethod}
                  label="Random sampling"
                  hint="Uniformly sample N from the full grid. Seeded → reproducible."
                />
                <RadioRow
                  name="method"
                  value="smart"
                  current={method}
                  onChange={setMethod}
                  label="Smart (CMA-ES / PSO / GA)"
                  hint="Evolutionary search. Best for huge / continuous spaces."
                />
              </div>
              {(method === 'random' || method === 'smart') && (
                <div style={{ marginTop: 8, display: 'flex', gap: 8, alignItems: 'center' }}>
                  <label style={{ fontSize: 11, color: 'var(--text-primary)' }}>
                    {method === 'random' ? 'Sample N' : 'Budget'}
                  </label>
                  <input
                    type="number"
                    min={1}
                    value={sampleN}
                    onChange={(e) => setSampleN(e.target.value)}
                    style={{ width: 90, padding: 4, fontSize: 12, ...inputStyle }}
                  />
                  {method === 'smart' && (
                    <>
                      <label style={{ fontSize: 11, marginLeft: 8, color: 'var(--text-primary)' }}>Algorithm</label>
                      <select
                        value={algorithm}
                        onChange={(e) => setAlgorithm(e.target.value)}
                        style={{ fontSize: 12, padding: '3px 6px', ...inputStyle }}
                      >
                        <option value="cma-es">CMA-ES</option>
                        <option value="pso">PSO</option>
                        <option value="ga">GA (DE)</option>
                      </select>
                    </>
                  )}
                </div>
              )}
            </Section>

            <Section title="Workers">
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <input
                  type="number"
                  min={1}
                  max={cpuInfo.cpu_count}
                  value={parallelism || cpuInfo.default_parallelism}
                  onChange={(e) => {
                    const n = Math.max(1, Math.min(cpuInfo.cpu_count, Number(e.target.value) || 1));
                    setParallelism(n);
                  }}
                  style={{ width: 70, padding: 4, fontSize: 12, ...inputStyle }}
                />
                <span style={{ fontSize: 11, color: 'var(--text-secondary, #6b7280)' }}>
                  of {cpuInfo.cpu_count} CPU cores · default {cpuInfo.default_parallelism}
                </span>
              </div>
              <div style={{ fontSize: 10, color: 'var(--text-muted, #9ca3af)', marginTop: 4 }}>
                More workers = faster, but each one loads market data so heavy on RAM.
              </div>
            </Section>

            {/* Strike Shift on Missing Contract section removed — engine
                now always walks TOWARD ATM on zero-turnover strikes and the
                tradesheet shows the shift reason in its own column. */}

            <Section title="Ranking Objective">
              <select
                value={objective}
                onChange={(e) => setObjective(e.target.value)}
                style={{ width: '100%', padding: 6, fontSize: 12, ...inputStyle }}
              >
                {(objectives.length
                  ? objectives
                  : [{ name: 'total_pnl', label: 'Net P&L (Sum)' }]
                ).map((o) => (
                  <option key={o.name} value={o.name}>
                    {o.label}
                  </option>
                ))}
              </select>
            </Section>

            <Section title="Plan">
              <Stat label="Selected params" value={selectedList.length} />
              <Stat label="Grid size" value={gridSize.toLocaleString()} />
              <Stat label="Planned runs" value={plannedRuns.toLocaleString()} />
              <Stat
                label="Est. runtime"
                value={
                  estSeconds < 60
                    ? `${estSeconds}s`
                    : `${Math.round(estSeconds / 60)} min`
                }
              />
              <div style={{ marginTop: 8 }}>
                <button
                  onClick={runPreview}
                  style={{
                    fontSize: 11,
                    padding: '4px 10px',
                    border: '1px solid var(--border-strong, #d1d5db)',
                    background: 'var(--bg-elevated, transparent)',
                    color: 'var(--text-primary, #111)',
                    borderRadius: 4,
                    cursor: 'pointer',
                  }}
                >
                  Validate (preview only)
                </button>
                {preview && (
                  <div
                    style={{
                      marginTop: 6,
                      fontSize: 11,
                      color: 'var(--text-secondary)',
                      fontFamily: 'IBM Plex Mono, monospace',
                    }}
                  >
                    OK — server confirms {preview.planned_runs.toLocaleString()} runs
                    {preview.estimated_seconds
                      ? ` (~${Math.round(preview.estimated_seconds / 60)} min)`
                      : ''}
                  </div>
                )}
              </div>
            </Section>

            {error && (
              <div
                style={{
                  marginTop: 'auto',
                  padding: 8,
                  border: '1px solid var(--loss-border, #fecaca)',
                  background: 'var(--loss-bg, #fef2f2)',
                  color: 'var(--loss, #991b1b)',
                  borderRadius: 6,
                  fontSize: 12,
                  display: 'flex',
                  gap: 6,
                  alignItems: 'flex-start',
                }}
              >
                <AlertCircle size={14} style={{ flexShrink: 0, marginTop: 2 }} />
                <div>{error}</div>
              </div>
            )}

            <div
              style={{
                marginTop: 8,
                paddingTop: 10,
                borderTop: '1px solid var(--border, #eef1f4)',
                display: 'flex',
                alignItems: 'center',
                gap: 10,
              }}
            >
              <Toggle enabled={autoDownload} onToggle={handleAutoDownloadToggle} size="sm" />
              <div>
                <div style={{ fontSize: 12, color: 'var(--text-primary)' }}>Auto-download when finished</div>
                <div style={{ fontSize: 10.5, color: 'var(--text-secondary, #667085)' }}>
                  ZIP, WOW/MOM &amp; Summary Excel download automatically — this tab or any open sibling tab on this PC. Remembers your last choice.
                </div>
              </div>
            </div>

            <div
              style={{
                marginTop: 8,
                paddingTop: 10,
                borderTop: '1px solid var(--border, #eef1f4)',
                display: 'flex',
                alignItems: 'center',
                gap: 10,
              }}
            >
              <div style={{ display: 'flex', flexShrink: 0, border: '1px solid var(--border-strong, #d1d5db)', borderRadius: 6, overflow: 'hidden' }}>
                {['patchwise', 'overall'].map((m) => (
                  <button
                    key={m}
                    type="button"
                    onClick={() => handleDownloadModeChange(m)}
                    style={{
                      padding: '5px 10px',
                      fontSize: 11,
                      border: 0,
                      cursor: 'pointer',
                      flexShrink: 0,
                      whiteSpace: 'nowrap',
                      background: downloadMode === m ? 'var(--accent, #2563eb)' : 'transparent',
                      color: downloadMode === m ? '#fff' : 'var(--text-primary)',
                    }}
                  >
                    {m === 'patchwise' ? 'Patchwise' : 'Overall'}
                  </button>
                ))}
              </div>
              <div style={{ minWidth: 0 }}>
                <div style={{ fontSize: 12, color: 'var(--text-primary)' }}>Download build (DD basis)</div>
                <div style={{ fontSize: 10.5, color: 'var(--text-secondary, #667085)' }}>
                  Only the selected one is built after the sweep — building both would take longer. Defaults to Patchwise if you forget to pick.
                </div>
              </div>
            </div>

            <div
              style={{
                marginTop: 'auto',
                paddingTop: 12,
                display: 'flex',
                gap: 8,
                justifyContent: 'flex-end',
              }}
            >
              <button
                onClick={onClose}
                style={{
                  padding: '8px 14px',
                  fontSize: 12,
                  background: 'var(--bg-elevated, transparent)',
                  color: 'var(--text-primary, #111)',
                  border: '1px solid var(--border-strong, #d1d5db)',
                  borderRadius: 6,
                  cursor: 'pointer',
                }}
              >
                Cancel
              </button>
              <button
                onClick={launch}
                disabled={submitting || selectedList.length === 0}
                style={{
                  padding: '8px 16px',
                  fontSize: 12,
                  background: 'var(--accent, #2563eb)',
                  color: '#fff',
                  border: 0,
                  borderRadius: 6,
                  cursor: submitting || selectedList.length === 0 ? 'not-allowed' : 'pointer',
                  opacity: submitting || selectedList.length === 0 ? 0.5 : 1,
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 6,
                }}
              >
                {submitting ? (
                  <>
                    <Loader2 size={14} className="animate-spin" />
                    Queueing…
                  </>
                ) : (
                  <>
                    <Play size={14} />
                    Launch Optimization
                  </>
                )}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function Section({ title, children }) {
  return (
    <div style={{ marginBottom: 14 }}>
      <div
        style={{
          fontSize: 11,
          fontWeight: 700,
          textTransform: 'uppercase',
          letterSpacing: '0.06em',
          color: 'var(--text-muted, #6b7280)',
          marginBottom: 6,
        }}
      >
        {title}
      </div>
      {children}
    </div>
  );
}

function RadioRow({ name, value, current, onChange, label, hint }) {
  return (
    <label style={{ display: 'flex', alignItems: 'flex-start', gap: 8, cursor: 'pointer' }}>
      <input
        type="radio"
        name={name}
        checked={current === value}
        onChange={() => onChange(value)}
        style={{ marginTop: 3 }}
      />
      <span>
        <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-primary)' }}>{label}</div>
        {hint && (
          <div style={{ fontSize: 10, color: 'var(--text-muted, #9ca3af)' }}>{hint}</div>
        )}
      </span>
    </label>
  );
}

function Stat({ label, value }) {
  return (
    <div
      style={{
        display: 'flex',
        justifyContent: 'space-between',
        padding: '3px 0',
        fontSize: 12,
        borderBottom: '1px solid var(--border-strong, #eee)',
      }}
    >
      <span style={{ color: 'var(--text-secondary, #6b7280)' }}>{label}</span>
      <span style={{ fontFamily: 'IBM Plex Mono, monospace', fontWeight: 600, color: 'var(--text-primary)' }}>
        {value}
      </span>
    </div>
  );
}

function RangeNum({ label, value, onChange }) {
  return (
    <label style={{ flex: 1, fontSize: 10 }}>
      <span style={{ color: 'var(--text-muted, #9ca3af)', display: 'block', marginBottom: 2 }}>{label}</span>
      <input
        type="number"
        value={value}
        onChange={(e) => onChange(e.target.value === '' ? '' : Number(e.target.value))}
        style={{
          width: '100%',
          padding: 3,
          fontSize: 11,
          background: 'var(--bg-input, #fff)',
          color: 'var(--text-primary, #111)',
          border: '1px solid var(--border-strong, #d1d5db)',
          borderRadius: 3,
        }}
      />
    </label>
  );
}

/**
 * EnumChips — click-to-toggle selector for an enum parameter.
 *
 * Shows every available value as a checkbox chip (pre-selected by default),
 * so the user just clicks to drop the values they don't want instead of
 * typing or editing a comma-separated string. Selection order follows the
 * canonical `options` order regardless of click order.
 *
 *   options  — full list of allowed values (from the param schema)
 *   selected — currently-included subset
 *   onChange — (nextSelectedArray) => void
 */
function EnumChips({ options, selected, onChange, valueLabels }) {
  const opts = options && options.length ? options : selected || [];
  const selSet = new Set(selected || []);
  const allOn = opts.length > 0 && opts.every((o) => selSet.has(o));
  const noneOn = (selected || []).length === 0;

  const toggle = (opt) => {
    const next = new Set(selSet);
    if (next.has(opt)) next.delete(opt);
    else next.add(opt);
    // Keep canonical option order, not click order.
    onChange(opts.filter((o) => next.has(o)));
  };

  const linkStyle = (disabled) => ({
    background: 'transparent',
    border: 0,
    padding: 0,
    fontSize: 10,
    letterSpacing: '0.04em',
    textTransform: 'uppercase',
    fontFamily: 'IBM Plex Mono, monospace',
    color: disabled ? 'var(--text-muted, #9ca3af)' : 'var(--accent, #2563eb)',
    opacity: disabled ? 0.45 : 1,
    cursor: disabled ? 'default' : 'pointer',
  });

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
        <button type="button" onClick={() => onChange([...opts])} disabled={allOn} style={linkStyle(allOn)}>
          All
        </button>
        <span style={{ fontSize: 10, opacity: 0.3 }}>·</span>
        <button type="button" onClick={() => onChange([])} disabled={noneOn} style={linkStyle(noneOn)}>
          None
        </button>
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
        {opts.map((opt) => {
          const on = selSet.has(opt);
          return (
            <button
              key={opt}
              type="button"
              onClick={() => toggle(opt)}
              aria-pressed={on}
              title={on ? `Remove ${valueLabels && valueLabels[opt] != null ? valueLabels[opt] : String(opt)}` : `Add ${valueLabels && valueLabels[opt] != null ? valueLabels[opt] : String(opt)}`}
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: 6,
                padding: '3px 9px 3px 7px',
                fontSize: 11,
                lineHeight: 1.4,
                borderRadius: 6,
                cursor: 'pointer',
                fontFamily: 'IBM Plex Mono, monospace',
                color: on ? 'var(--text-primary, #111)' : 'var(--text-muted, #9ca3af)',
                border: on
                  ? '1px solid var(--accent, #2563eb)'
                  : '1px solid var(--border-strong, #d1d5db)',
                background: on
                  ? 'color-mix(in srgb, var(--accent, #2563eb) 14%, transparent)'
                  : 'transparent',
                transition: 'background 120ms ease, border-color 120ms ease, color 120ms ease',
              }}
            >
              <span
                aria-hidden="true"
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  width: 13,
                  height: 13,
                  borderRadius: 3,
                  color: '#fff',
                  border: on
                    ? '1px solid var(--accent, #2563eb)'
                    : '1px solid var(--border-strong, #d1d5db)',
                  background: on ? 'var(--accent, #2563eb)' : 'transparent',
                }}
              >
                {on ? <Check size={10} strokeWidth={3} /> : null}
              </span>
              {valueLabels && valueLabels[opt] != null ? valueLabels[opt] : String(opt)}
            </button>
          );
        })}
      </div>
    </div>
  );
}
