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

const DEFAULT_METHOD = 'exhaustive';
const DEFAULT_OBJECTIVE = 'total_pnl';

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
  method, setMethod,
  sampleN, setSampleN,
  algorithm, setAlgorithm,
  objective, setObjective,
  parallelism, setParallelism,
}) {
  const allParams = useMemo(() => expandSchemaForLegs(nLegs || 1), [nLegs]);
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
      .map((p) => savedValues[p.path] || {
        path: p.path, label: p.label, kind: p.kind,
        min: p.min, max: p.max, step: p.step,
        values: p.values ? [...p.values] : undefined,
        unit: p.unit,
      });
  }, [allParams, checked, savedValues]);

  const gridSize = useMemo(() => totalCombos(selectedList), [selectedList]);
  const plannedRuns =
    method === 'exhaustive' ? gridSize : Math.min(gridSize || sampleN, Number(sampleN) || 0);
  const estSeconds = Math.round(plannedRuns * 0.2);

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
          return {
            ...sv,
            [p.path]: {
              path: p.path, label: p.label, kind: p.kind,
              min: p.min, max: p.max, step: p.step,
              values: p.values ? [...p.values] : undefined,
              unit: p.unit,
            },
          };
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
    return {
      ...(basePayload || {}),
      strike_shift_max_steps: Number(strikeShiftOverride) || 0,
    };
  }

  async function launch() {
    setSubmitting(true);
    setError(null);
    try {
      const body = {
        base_payload: basePayloadWithOverrides(),
        param_specs: specsToPayload(selectedList),
        method,
        sample_n: method === 'exhaustive' ? null : Number(sampleN) || 0,
        objective,
        algorithm: method === 'smart' ? algorithm : null,
        parallelism: parallelism || cpuInfo.default_parallelism,
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
                  const spec = savedValues[p.path] || p;
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
                        {p.unit && (
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
function EnumChips({ options, selected, onChange }) {
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
              title={on ? `Remove ${opt}` : `Add ${opt}`}
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
              {opt}
            </button>
          );
        })}
      </div>
    </div>
  );
}
