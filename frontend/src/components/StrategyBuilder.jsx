import React, { useState, useEffect, useMemo, useRef, useCallback } from 'react';
import { Play, Plus, Trash2, Info, Save, AlertTriangle, Loader2, RefreshCw, Sun, Moon, Beaker, LayoutGrid, BarChart3, SlidersHorizontal, Cpu, Upload, FileText, X } from 'lucide-react';
import { format, parse, isValid } from 'date-fns';
import ResultsPanel from './ResultsPanel';
import SuperTrendFilter from './SuperTrendFilter';
import OptimizePanel from './OptimizePanel';
import OptimizationResults from './OptimizationResults';
import AutoDownloadQueue from './AutoDownloadQueue';
import { appendToQueue, loadQueue } from '../utils/optimQueueStore';
import Toggle from './ui/Toggle';
import Dropdown from './ui/Dropdown';
import CalendarPicker from './ui/CalendarPicker';

// Convert DD/MM/YYYY to YYYY-MM-DD for API
const toApiDate = (displayStr) => {
  if (!displayStr) return '';
  try {
    const d = parse(displayStr, 'dd/MM/yyyy', new Date());
    if (!isValid(d)) return '';
    return format(d, 'yyyy-MM-dd');
  } catch {
    return displayStr;
  }
};

const getApiStartDate = (startDate) => toApiDate(startDate);
const getApiEndDate = (endDate) => toApiDate(endDate);

// Validate DD/MM/YYYY format
const isValidDisplayDate = (dateStr) => {
  if (!dateStr) return false;
  try {
    const d = parse(dateStr, 'dd/MM/yyyy', new Date());
    return isValid(d);
  } catch {
    return false;
  }
};

const INDEX_CONFIG = {
  NIFTY: {
    label: 'NIFTY',
    subtitle: 'Weekly, monthly & yearly expiries',
    group: 'weekly_monthly',
    backtestEnabled: true,
    // 'yearly' = NSE's long-dated DECEMBER contract. NIFTY-only: it has 24 such
    // contracts (2010-2030) while BANKNIFTY/MIDCPNIFTY/FINNIFTY have none, so
    // listing it here alone is what keeps it off the other indices.
    expiryBases: ['weekly', 'monthly', 'yearly'],
    defaultExpiryBasis: 'weekly',
    defaultOptionExpiry: 'weekly',
    strikeInterval: 50,
  },
  SENSEX: {
    label: 'SENSEX',
    subtitle: 'Data not available',
    group: 'weekly_monthly',
    backtestEnabled: false,
    expiryBases: ['weekly', 'monthly'],
    defaultExpiryBasis: 'weekly',
    defaultOptionExpiry: 'weekly',
    strikeInterval: 100,
  },
  MIDCPNIFTY: {
    label: 'MIDCPNIFTY',
    subtitle: 'Weekly (till Nov 2024) & monthly',
    group: 'weekly_monthly',
    backtestEnabled: true,
    expiryBases: ['weekly', 'monthly'],
    // Default stays MONTHLY (current NSE regime; weeklies ended Nov 2024) so
    // existing behaviour is unchanged — weekly is opt-in for the historical era.
    defaultExpiryBasis: 'monthly',
    defaultOptionExpiry: 'monthly',
    strikeInterval: 25,
  },
  BANKNIFTY: {
    label: 'BANKNIFTY',
    subtitle: 'Monthly expiry only',
    group: 'monthly_only',
    backtestEnabled: true,
    expiryBases: ['monthly'],
    defaultExpiryBasis: 'monthly',
    defaultOptionExpiry: 'monthly',
    strikeInterval: 100,
  },
};

const INDEX_GROUPS = [
  {
    key: 'weekly_monthly',
    title: 'Weekly & Monthly Expiries',
    symbols: ['NIFTY', 'SENSEX', 'MIDCPNIFTY'],
  },
  {
    key: 'monthly_only',
    title: 'Monthly Only Expiry',
    symbols: ['BANKNIFTY'],
  },
];

const EXPIRY_BASIS_LABELS = {
  weekly: 'Weekly Expiry',
  monthly: 'Monthly Expiry',
  yearly: 'Yearly Expiry (December)',
};

const WEEKLY_OPTION_EXPIRIES = [
  { value: 'weekly', label: 'Weekly' },
  { value: 'next_weekly', label: 'Next Weekly' },
  { value: 'monthly', label: 'Monthly' },
  { value: 'next_monthly', label: 'Next Monthly' },
];

// The Yearly choice ADDED to a leg's normal list when the strategy basis is
// YEARLY. It is additive, not exclusive: each leg picks its own contract, so a
// basket can mix (e.g. CE SELL weekly + PE BUY yearly). The engine pins the
// December contract only to legs set to Yearly; the rest trade their cadence
// contract. The roll cadence stays a strategy-level control (rolloverCadence).
const YEARLY_LEG_EXPIRY = { value: 'yearly', label: 'Yearly (December)' };

const MONTHLY_OPTION_EXPIRIES = [
  { value: 'monthly', label: 'Monthly' },
  { value: 'next_monthly', label: 'Next Monthly' },
];

const FUTURES_EXPIRIES = MONTHLY_OPTION_EXPIRIES;

const getIndexConfig = (symbol) => INDEX_CONFIG[String(symbol || 'NIFTY').toUpperCase()] || INDEX_CONFIG.NIFTY;

// `basis` is optional: callers that omit it get the pre-yearly behaviour
// unchanged. Under a YEARLY basis the only contract a leg can hold is the
// December one, so the per-leg dropdown collapses to a single choice.
const getOptionExpiryOptions = (symbol, basis) => {
  const config = getIndexConfig(symbol);
  const base = config.expiryBases.includes('weekly') ? WEEKLY_OPTION_EXPIRIES : MONTHLY_OPTION_EXPIRIES;
  // Under a YEARLY basis, Yearly is APPENDED to the normal list rather than
  // replacing it, so each leg chooses independently. Replacing the list forced
  // every leg to Yearly, which made a mixed basket impossible.
  return String(basis || '').toLowerCase() === 'yearly' ? [...base, YEARLY_LEG_EXPIRY] : base;
};

const normalizeReEntryMode = (mode) => {
  const value = String(mode || 'RE_ASAP').toUpperCase().trim();
  return value || 'RE_ASAP';
};

const normalizeExpiryForIndex = (expiry, symbol, segment = 'options', basis) => {
  const options = segment === 'futures' ? FUTURES_EXPIRIES : getOptionExpiryOptions(symbol, basis);
  const current = String(expiry || '').toLowerCase();
  if (options.some(opt => opt.value === current)) return current;
  if (segment === 'futures') return 'monthly';
  return String(basis || '').toLowerCase() === 'yearly'
    ? 'yearly'
    : getIndexConfig(symbol).defaultOptionExpiry;
};

const DATE_YEAR_MIN = 1900;
const DATE_YEAR_MAX = 2100;
const DIGIT_LIMIT = 8;

const hasValidDateParts = (yearStr, monthStr, dayStr) => {
  if (!yearStr || !monthStr || !dayStr) return false;
  const year = Number(yearStr);
  const month = Number(monthStr);
  const day = Number(dayStr);
  if (!Number.isInteger(year) || !Number.isInteger(month) || !Number.isInteger(day)) return false;
  if (year < DATE_YEAR_MIN || year > DATE_YEAR_MAX) return false;
  if (month < 1 || month > 12) return false;
  if (day < 1 || day > 31) return false;
  const candidate = new Date(year, month - 1, day);
  return (
    !Number.isNaN(candidate.getTime()) &&
    candidate.getUTCFullYear() === year &&
    candidate.getUTCMonth() === month - 1 &&
    candidate.getUTCDate() === day
  );
};

const extractDigits = (value) => {
  if (!value) return '';
  return value.replace(/\D/g, '').slice(0, DIGIT_LIMIT);
};

const formatFromDigits = (digits) => {
  if (!digits) return '';
  const day = digits.slice(0, 2);
  const month = digits.slice(2, 4);
  const year = digits.slice(4, 8);
  const segments = [];
  if (day) segments.push(day);
  if (digits.length > 2) segments.push(month);
  if (digits.length > 4) segments.push(year);
  return segments.join('/');
};

const countDigitsBefore = (value = '', position = 0) => {
  return (value.slice(0, position).match(/\d/g) || []).length;
};

const caretPositionForDigitIndex = (digitIndex, digits) => {
  const clampedIndex = Math.min(Math.max(digitIndex, 0), digits.length);
  let position = clampedIndex;
  if (digits.length > 2 && clampedIndex > 2) position += 1;
  if (digits.length > 4 && clampedIndex > 4) position += 1;
  return position;
};

const tryParseYearFirstDigits = (digits) => {
  if (digits.length < 8) return null;
  const year = digits.slice(0, 4);
  const month = digits.slice(4, 6);
  const day = digits.slice(6, 8);
  if (!hasValidDateParts(year, month, day)) return null;
  return `${day}${month}${year}`;
};

const normalizePastedDigits = (text) => {
  const digitsOnly = text.replace(/\D/g, '').slice(0, DIGIT_LIMIT);
  if (!digitsOnly) return '';
  const yearFirst = tryParseYearFirstDigits(digitsOnly);
  return yearFirst || digitsOnly;
};

const DateInput = ({ value, onChange, placeholder }) => {
  const [localDigits, setLocalDigits] = useState(extractDigits(value));
  const [localDisplay, setLocalDisplay] = useState(formatFromDigits(localDigits));
  const [isFocused, setIsFocused] = useState(false);
  const inputRef = useRef(null);
  const lastCommittedRef = useRef(value || '');

  useEffect(() => {
    if (!isFocused) {
      const nextDigits = extractDigits(value);
      setLocalDigits(nextDigits);
      setLocalDisplay(formatFromDigits(nextDigits));
      lastCommittedRef.current = value || '';
    }
  }, [value, isFocused]);

  const commitValue = useCallback(
    (formatted) => {
      if (formatted !== lastCommittedRef.current) {
        lastCommittedRef.current = formatted;
        onChange(formatted);
      }
      return formatted;
    },
    [onChange]
  );

  const updateDisplay = useCallback((digits, caretDigits) => {
    const formatted = formatFromDigits(digits);
    setLocalDigits(digits);
    setLocalDisplay(formatted);
    requestAnimationFrame(() => {
      if (!inputRef.current) return;
      const caret = caretPositionForDigitIndex(caretDigits, digits);
      const bounded = Math.min(caret, formatted.length);
      inputRef.current.setSelectionRange(bounded, bounded);
    });
    if (digits.length === DIGIT_LIMIT && hasValidDateParts(digits.slice(4, 8), digits.slice(2, 4), digits.slice(0, 2))) {
      commitValue(formatted);
    }
    return formatted;
  }, [commitValue]);

  const handleChange = (e) => {
    const rawValue = e.target.value;
    const digitsBeforeCursor = countDigitsBefore(rawValue, e.target.selectionStart || 0);
    const nextDigits = extractDigits(rawValue);
    updateDisplay(nextDigits, digitsBeforeCursor);
  };

  const handleBlur = () => {
    setIsFocused(false);
    const formatted = formatFromDigits(localDigits);
    setLocalDisplay(formatted);
    commitValue(formatted);
  };

  const handleFocus = () => {
    setIsFocused(true);
  };

  const handlePaste = (e) => {
    const pasted = e.clipboardData?.getData('text') || '';
    if (!pasted) return;
    const nextDigits = normalizePastedDigits(pasted);
    if (!nextDigits) return;
    e.preventDefault();
    updateDisplay(nextDigits, nextDigits.length);
  };

  const handleKeyDown = (e) => {
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    const allowedKeys = new Set(['Backspace', 'Delete', 'ArrowLeft', 'ArrowRight', 'Home', 'End', 'Tab']);
    if (e.key === 'Enter') {
      e.preventDefault();
      inputRef.current?.blur();
      return;
    }
    if (allowedKeys.has(e.key)) return;
    if (/^\d$/.test(e.key)) {
      if (localDigits.length >= DIGIT_LIMIT && e.target.selectionStart === e.target.selectionEnd) {
        e.preventDefault();
      }
      return;
    }
    e.preventDefault();
  };

  return (
    <input
      ref={inputRef}
      type="text"
      placeholder={placeholder || 'DD / MM / YYYY'}
      value={localDisplay}
      onChange={handleChange}
      onFocus={handleFocus}
      onBlur={handleBlur}
      onPaste={handlePaste}
      onKeyDown={handleKeyDown}
      style={{
        fontFamily: 'IBM Plex Mono, monospace',
        fontSize: '0.82rem',
        fontWeight: 500,
        letterSpacing: '0.04em',
        width: '148px',
        height: '38px',
        padding: '0 12px',
        borderRadius: '8px',
        border: `1.5px solid ${isFocused ? 'var(--accent)' : 'var(--border-default)'}`,
        backgroundColor: 'var(--bg-input)',
        color: 'var(--text-primary)',
        boxShadow: isFocused ? '0 0 0 3px var(--accent-glow)' : '0 1px 2px rgba(0,0,0,0.04)',
        outline: 'none',
        transition: 'border-color 0.15s ease, box-shadow 0.15s ease',
      }}
    />
  );
};

const parseDisplayDateForLot = (tradeDate) => {
  if (!tradeDate) return new Date();
  if (typeof tradeDate === 'string' && tradeDate.includes('/')) {
    const parsed = parse(tradeDate, 'dd/MM/yyyy', new Date());
    return isValid(parsed) ? parsed : new Date();
  }
  const parsed = new Date(tradeDate);
  return Number.isNaN(parsed.getTime()) ? new Date() : parsed;
};

const getLotSize = (index, tradeDate) => {
  const d = parseDisplayDateForLot(tradeDate);
  if (index === 'NIFTY') {
    // Flat 65 — MIRRORS backend services/index_metadata.py::get_lot_size_for_index,
    // which returns 65 for NIFTY at every date. The UI previously carried a dated
    // schedule (200 / 50 / 75 / 65) that the engine does not, so a pre-Nov-2019
    // backtest displayed "75 units" while the tradesheet priced Qty 65. The two
    // must agree: P&L is lot-scaled, so a mismatch here misreports the position.
    // If NIFTY's historical tiers are ever restored, change BOTH sides together.
    return 65;
  }
  if (index === 'BANKNIFTY') {
    if (d < new Date('2010-10-01')) return 50;
    if (d < new Date('2015-10-29')) return 25;
    if (d < new Date('2019-11-01')) return 20;
    if (d < new Date('2023-07-01')) return 25;
    if (d < new Date('2024-11-20')) return 15;
    if (d < new Date('2025-07-01')) return 30;
    if (d < new Date('2026-01-01')) return 35;
    return 30;
  }
  if (index === 'FINNIFTY')   return d < new Date('2026-01-01') ? 65 : 60;
  if (index === 'MIDCPNIFTY') {
    if (d < new Date('2024-11-20')) return 75;
    if (d < new Date('2025-07-01')) return 120;
    if (d < new Date('2026-01-01')) return 140;
    return 120;
  }
  if (index === 'SENSEX')     return 20;
  if (index === 'BANKEX')     return 30;
  return 1;
};

const STRIKE_INTERVALS = Object.fromEntries(
  Object.entries(INDEX_CONFIG).map(([symbol, config]) => [symbol, config.strikeInterval])
);
const STRIKE_INTERVAL_OPTIONS = [25, 50, 100, 500, 1000];
// December-contract years selectable in the yearly per-contract schedule table.
const YEAR_OPTIONS = Array.from({ length: 21 }, (_, i) => 2015 + i);
// Per-index selectable gaps: MIDCPNIFTY trades at every 25 points so it gets
// 25/50/100; other indices only ever trade at their own native gap (50 or
// 100), so showing 25 there would be a selectable-but-wrong option. Every index
// also gets the coarse gaps 500 and 1000 — when a coarse strike is
// illiquid/unlisted the backend liquidity-shift walks a finer per-index step
// (NIFTY 100, MIDCPNIFTY 50) toward ATM to find a tradeable strike.
//
// 1000 exists chiefly for YEARLY (the long-dated December contract): in the
// 26-Dec-2019 contract round-1000 strikes are ~59% liquid vs ~12% for every
// other strike, because long-dated open interest only collects on round-1000s.
const strikeIntervalOptionsForIndex = (symbol) => {
  const native = STRIKE_INTERVALS[String(symbol || 'NIFTY').toUpperCase()] ?? 50;
  return native === 25 ? [25, 50, 100, 500, 1000] : [50, 100, 500, 1000];
};
const normalizeStrikeInterval = (value, symbol) => {
  const parsed = Number(value);
  const options = symbol ? strikeIntervalOptionsForIndex(symbol) : STRIKE_INTERVAL_OPTIONS;
  if (options.includes(parsed)) return parsed;
  return symbol ? (STRIKE_INTERVALS[String(symbol).toUpperCase()] ?? 50) : 50;
};

const StrikeIntervalSelect = ({ value, onChange, className = '', index = null }) => {
  const options = index ? strikeIntervalOptionsForIndex(index) : STRIKE_INTERVAL_OPTIONS;
  return (
  <select
    value={normalizeStrikeInterval(value, index)}
    onChange={e => onChange(normalizeStrikeInterval(e.target.value, index))}
    className={className}
  >
    {options.map(interval => (
      <option key={interval} value={interval}>{interval}</option>
    ))}
  </select>
  );
};

function getBufferPreview(value, unit, applyTo, posAbove, posBelow, indexName = 'NIFTY') {
  const spot = 25000;
  const interval = STRIKE_INTERVALS[indexName?.toUpperCase()] ?? 50;
  const numericValue = Number(value) || 0;
  const bufPts = unit === 'percent' ? spot * (numericValue / 100) : numericValue;
  const parts = [];

  // CE: Above checkbox gates it, always moves UP (more OTM, away from spot)
  if ((applyTo === 'call' || applyTo === 'both') && posAbove) {
    const ref = spot + bufPts;
    const strike = Math.ceil(ref / interval) * interval;
    const bufOnStrike = unit === 'percent' ? strike * (numericValue / 100) : numericValue;
    const refPrice = (strike + bufOnStrike).toFixed(2);
    parts.push(`CALL → ${strike.toLocaleString('en-IN')} CE (ref ${refPrice})`);
  }
  // PE: Below checkbox gates it, always moves DOWN (more OTM, away from spot)
  if ((applyTo === 'put' || applyTo === 'both') && posBelow) {
    const ref = spot - bufPts;
    const strike = Math.floor(ref / interval) * interval;
    const bufOnStrike = unit === 'percent' ? strike * (numericValue / 100) : numericValue;
    const refPrice = (strike - bufOnStrike).toFixed(2);
    parts.push(`PUT → ${strike.toLocaleString('en-IN')} PE (ref ${refPrice})`);
  }

  if (parts.length === 0) return 'No buffer applied (enable Above for CE, Below for PE)';
  return `e.g. spot 25,000: ${parts.join(' | ')}`;
}

const Tooltip = ({ text }) => {
  const [show, setShow] = useState(false);
  return (
    <span className="relative inline-flex">
      <button
        type="button"
        className="text-muted hover:text-secondary focus:outline-none"
        onMouseEnter={() => setShow(true)}
        onMouseLeave={() => setShow(false)}
      >
        <Info size={12} />
      </button>
      {show && (
        <span className="absolute left-5 top-0 z-50 w-56 rounded bg-base p-2.5 text-xs text-secondary shadow-xl whitespace-normal leading-relaxed border border-subtle">
          {text}
        </span>
      )}
    </span>
  );
};

const SegBtn = ({ options, value, onChange, size = 'md' }) => {
  return (
    <div className="seg-group">
      {options.map((opt) => {
        const disabled = Boolean(opt.disabled);
        const isActive = value === opt.value;
        return (
          <button
            key={opt.value}
            type="button"
            onClick={() => { if (!disabled) onChange(opt.value); }}
            disabled={disabled}
            className={`seg-btn${isActive ? ' active' : ''}${disabled ? ' disabled' : ''}`}
            style={size === 'sm' ? { padding: '4px 10px', fontSize: '0.66rem' } : {}}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
};

const createDefaultLazyLeg = (index = 1) => ({
  id: null,
  name: `lazy${index}`,
  segment: 'options',
  position: 'sell',
  lot: 1,
  option_type: 'call',
  expiry: 'weekly',
  strike_criteria: 'strike_type',
  strike_type: 'atm',
  strike_interval: 50,
  premium_value: 0,
  premium_min: 0,
  premium_max: 0,
  target_enabled: false,
  target_mode: 'PERCENT',
  target_value: 0,
  stop_loss_enabled: false,
  stop_loss_mode: 'PERCENT',
  stop_loss_value: 0,
  sl_buffer_enabled: false,
  sl_buffer_mode: 'PERCENT',
  sl_buffer_value: null,
  sl_buffer_pct: null,
  trail_sl_enabled: false,
  trail_sl_mode: 'PERCENT',
  trail_sl_trigger: 0,
  trail_sl_move: 0,
  re_entry_target_enabled: false,
  re_entry_target_mode: 'RE_ASAP',
  re_entry_target_count: 1,
  re_entry_sl_enabled: false,
  re_entry_sl_mode: 'RE_ASAP',
  re_entry_sl_count: 1,
  simple_momentum_enabled: false,
  simple_momentum_mode: 'POINTS_UP',
  simple_momentum_value: 0,
  no_reentry_after: '',
  child_lazy_leg_sl_id: null,
  child_lazy_leg_target_id: null,
});

const LazyLegModal = ({
  isOpen,
  onClose,
  onSave,
  onConfigureChild,
  editingConfig,
  strikeTypeOpts,
  expiryOptions,
  defaultExpiry,
  totalLazyLegCount,
}) => {
  const [form, setForm] = useState(() => ({
    ...(editingConfig || createDefaultLazyLeg(totalLazyLegCount + 1)),
    expiry: (editingConfig || {}).expiry || defaultExpiry,
  }));
  const set = (key, value) => setForm(prev => ({ ...prev, [key]: value }));

  useEffect(() => {
    const next = editingConfig || createDefaultLazyLeg(totalLazyLegCount + 1);
    const allowed = expiryOptions?.some(opt => opt.value === next.expiry);
    setForm({
      ...next,
      expiry: allowed ? next.expiry : defaultExpiry,
    });
  }, [isOpen, editingConfig, expiryOptions, defaultExpiry, totalLazyLegCount]);

  if (!isOpen) return null;

  const numberValue = (value) => value ?? '';
  const riskModeOptions = (
    <>
      <option value="POINTS">Points</option>
      <option value="PERCENT">Percent</option>
    </>
  );
  const reEntryModeOptions = (
    <>
      <option value="RE_ASAP">RE ASAP</option>
      <option value="RE_ASAP_REV">RE ASAP &#8629;</option>
      <option value="RE_MOMENTUM">RE MOMENTUM</option>
      <option value="RE_MOMENTUM_REV">RE MOMENTUM &#8629;</option>
      <option value="LAZY_LEG">Lazy Leg</option>
    </>
  );
  const inputClass = 'h-8 px-2 border border-default rounded text-xs bg-surface';

  return (
    <div className="fixed inset-0 bg-black/40 z-[100] flex items-center justify-center p-4">
      <div className="bg-surface rounded-xl shadow-2xl border border-default w-full max-w-2xl max-h-[90vh] overflow-y-auto">
        <div className="sticky top-0 bg-surface border-b border-subtle px-5 py-4 flex items-center justify-between">
          <h3 className="text-base font-semibold text-primary">Create New Lazy Leg</h3>
          <button type="button" onClick={onClose} className="text-xl leading-none text-muted hover:text-primary">×</button>
        </div>

        <div className="p-5 space-y-5">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <label className="text-xs text-secondary">
              Name
              <input value={form.name || ''} onChange={e => set('name', e.target.value)} className={`${inputClass} mt-1 w-full`} />
            </label>
            <label className="text-xs text-secondary">
              Lots
              <input type="number" min={1} value={numberValue(form.lot)} onChange={e => set('lot', parseInt(e.target.value, 10) || 1)} className={`${inputClass} mt-1 w-full`} />
            </label>
            <div>
              <div className="text-xs text-secondary mb-1">Position</div>
              <SegBtn size="sm" value={form.position} onChange={v => set('position', v)} options={[{ label: 'Buy', value: 'buy' }, { label: 'Sell', value: 'sell' }]} />
            </div>
            <div>
              <div className="text-xs text-secondary mb-1">Option Type</div>
              <SegBtn size="sm" value={form.option_type} onChange={v => set('option_type', v)} options={[{ label: 'Call', value: 'call' }, { label: 'Put', value: 'put' }]} />
            </div>
            <label className="text-xs text-secondary">
              Expiry
              <select value={form.expiry} onChange={e => set('expiry', e.target.value)} className={`${inputClass} mt-1 w-full`}>
                {expiryOptions.map(opt => (
                  <option key={opt.value} value={opt.value}>{opt.label}</option>
                ))}
              </select>
            </label>
            <label className="text-xs text-secondary">
              Strike Criteria
              <select value={form.strike_criteria} onChange={e => set('strike_criteria', e.target.value)} className={`${inputClass} mt-1 w-full`}>
                <option value="strike_type">Strike Type</option>
                <option value="closest_premium">Closest Premium</option>
                <option value="premium_range">Premium Range</option>
              </select>
            </label>
            <label className="text-xs text-secondary">
              Strike Gap
              <StrikeIntervalSelect
                value={form.strike_interval}
                onChange={value => set('strike_interval', value)}
                className={`${inputClass} mt-1 w-full`}
              />
            </label>
            {form.strike_criteria === 'strike_type' && (
              <label className="text-xs text-secondary">
                Strike Type
                <select value={form.strike_type} onChange={e => set('strike_type', e.target.value)} className={`${inputClass} mt-1 w-full`}>
                  {strikeTypeOpts.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
              </label>
            )}
            {form.strike_criteria === 'closest_premium' && (
              <label className="text-xs text-secondary">
                Premium Value
                <input type="number" min={0} value={numberValue(form.premium_value)} onChange={e => set('premium_value', +e.target.value)} className={`${inputClass} mt-1 w-full`} />
              </label>
            )}
            {form.strike_criteria === 'premium_range' && (
              <>
                <label className="text-xs text-secondary">
                  Premium Min
                  <input type="number" min={0} value={numberValue(form.premium_min)} onChange={e => set('premium_min', +e.target.value)} className={`${inputClass} mt-1 w-full`} />
                </label>
                <label className="text-xs text-secondary">
                  Premium Max
                  <input type="number" min={0} value={numberValue(form.premium_max)} onChange={e => set('premium_max', +e.target.value)} className={`${inputClass} mt-1 w-full`} />
                </label>
              </>
            )}
          </div>

          <div className="border-t border-subtle pt-4 space-y-3">
            <h4 className="text-xs font-semibold text-secondary uppercase tracking-wide">Risk Controls</h4>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
              <div className="flex items-center gap-2">
                <Toggle enabled={form.target_enabled} onToggle={v => set('target_enabled', v !== undefined ? Boolean(v) : !form.target_enabled)} size="sm" />
                <span className="text-xs text-secondary">Target Profit</span>
              </div>
              {form.target_enabled && (
                <>
                  <select value={form.target_mode} onChange={e => set('target_mode', e.target.value)} className={inputClass}>{riskModeOptions}</select>
                  <input type="number" min={0} value={numberValue(form.target_value)} onChange={e => set('target_value', +e.target.value)} className={inputClass} />
                </>
              )}
              <div className="flex items-center gap-2">
                <Toggle enabled={form.stop_loss_enabled} onToggle={v => set('stop_loss_enabled', v !== undefined ? Boolean(v) : !form.stop_loss_enabled)} size="sm" />
                <span className="text-xs text-secondary">Stop Loss</span>
              </div>
              {form.stop_loss_enabled && (
                <>
                  <select value={form.stop_loss_mode} onChange={e => set('stop_loss_mode', e.target.value)} className={inputClass}>{riskModeOptions}</select>
                  <input type="number" min={0} value={numberValue(form.stop_loss_value)} onChange={e => set('stop_loss_value', +e.target.value)} className={inputClass} />
                </>
              )}
              <div className="flex items-center gap-2">
                <Toggle enabled={form.trail_sl_enabled} onToggle={v => set('trail_sl_enabled', v !== undefined ? Boolean(v) : !form.trail_sl_enabled)} size="sm" />
                <span className="text-xs text-secondary">Trail SL</span>
              </div>
              {form.trail_sl_enabled && (
                <>
                  <select value={form.trail_sl_mode} onChange={e => set('trail_sl_mode', e.target.value)} className={inputClass}>{riskModeOptions}</select>
                  <div className="flex gap-2">
                    <input type="number" min={0} placeholder="X" value={numberValue(form.trail_sl_trigger)} onChange={e => set('trail_sl_trigger', +e.target.value)} className={`${inputClass} w-full`} />
                    <input type="number" min={0} placeholder="Y" value={numberValue(form.trail_sl_move)} onChange={e => set('trail_sl_move', +e.target.value)} className={`${inputClass} w-full`} />
                  </div>
                </>
              )}
            </div>
          </div>

          <div className="border-t border-subtle pt-4 space-y-3">
            <h4 className="text-xs font-semibold text-secondary uppercase tracking-wide">Re-Entry</h4>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div className="space-y-2">
                <div className="flex items-center gap-2">
                  <Toggle enabled={form.re_entry_target_enabled} onToggle={v => set('re_entry_target_enabled', v !== undefined ? Boolean(v) : !form.re_entry_target_enabled)} size="sm" />
                  <span className="text-xs text-secondary">Re-entry on Target</span>
                </div>
                {form.re_entry_target_enabled && (
                  <div className="flex gap-2">
                    <select value={form.re_entry_target_mode} onChange={e => set('re_entry_target_mode', e.target.value)} className={`${inputClass} flex-1`}>{reEntryModeOptions}</select>
                    <input type="number" min={1} max={20} value={form.re_entry_target_count} onChange={e => set('re_entry_target_count', +e.target.value || 1)} className={`${inputClass} w-16`} />
                  </div>
                )}
                {form.re_entry_target_enabled && form.re_entry_target_mode === 'LAZY_LEG' && (
                  <button type="button" disabled={!form.id} onClick={() => onConfigureChild(form.id, 'target')} className="text-xs px-2 py-1 rounded border border-accent text-accent disabled:opacity-50">
                    {form.child_lazy_leg_target_id ? 'Edit Child Lazy Leg' : 'Configure Child Lazy Leg'}
                  </button>
                )}
              </div>
              <div className="space-y-2">
                <div className="flex items-center gap-2">
                  <Toggle enabled={form.re_entry_sl_enabled} onToggle={v => set('re_entry_sl_enabled', v !== undefined ? Boolean(v) : !form.re_entry_sl_enabled)} size="sm" />
                  <span className="text-xs text-secondary">Re-entry on SL</span>
                </div>
                {form.re_entry_sl_enabled && (
                  <div className="flex gap-2">
                    <select value={form.re_entry_sl_mode} onChange={e => set('re_entry_sl_mode', e.target.value)} className={`${inputClass} flex-1`}>{reEntryModeOptions}</select>
                    <input type="number" min={1} max={20} value={form.re_entry_sl_count} onChange={e => set('re_entry_sl_count', +e.target.value || 1)} className={`${inputClass} w-16`} />
                  </div>
                )}
                {form.re_entry_sl_enabled && form.re_entry_sl_mode === 'LAZY_LEG' && (
                  <button type="button" disabled={!form.id} onClick={() => onConfigureChild(form.id, 'sl')} className="text-xs px-2 py-1 rounded border border-accent text-accent disabled:opacity-50">
                    {form.child_lazy_leg_sl_id ? 'Edit Child Lazy Leg' : 'Configure Child Lazy Leg'}
                  </button>
                )}
              </div>
            </div>
          </div>

          <div className="border-t border-subtle pt-4 grid grid-cols-1 sm:grid-cols-3 gap-3">
            <div className="flex items-center gap-2">
              <Toggle enabled={form.simple_momentum_enabled} onToggle={v => set('simple_momentum_enabled', v !== undefined ? Boolean(v) : !form.simple_momentum_enabled)} size="sm" />
              <span className="text-xs text-secondary">Simple Momentum</span>
            </div>
            {form.simple_momentum_enabled && (
              <>
                <select value={form.simple_momentum_mode} onChange={e => set('simple_momentum_mode', e.target.value)} className={inputClass}>
                  <option value="POINTS_UP">Points Up</option>
                  <option value="POINTS_DOWN">Points Down</option>
                  <option value="PERCENT_UP">Percent Up</option>
                  <option value="PERCENT_DOWN">Percent Down</option>
                </select>
                <input type="number" min={0} value={numberValue(form.simple_momentum_value)} onChange={e => set('simple_momentum_value', +e.target.value)} className={inputClass} />
              </>
            )}
          </div>
        </div>

        <div className="sticky bottom-0 bg-surface border-t border-subtle px-5 py-4 flex justify-end gap-2">
          <button type="button" onClick={onClose} className="px-4 py-2 rounded border border-default text-sm text-secondary hover:bg-hover">Cancel</button>
          <button type="button" onClick={() => onSave(form)} className="px-4 py-2 rounded bg-accent text-white text-sm font-semibold hover:bg-accent-hover">Create and Select</button>
        </div>
      </div>
    </div>
  );
};

const StrategyBuilder = () => {
  const [isDark, setIsDark] = useState(() => {
    const saved = localStorage.getItem('sl-theme');
    return saved ? saved === 'dark' : true;
  });

  useEffect(() => {
    if (isDark) {
      document.body.classList.add('dark-mode');
    } else {
      document.body.classList.remove('dark-mode');
    }
    localStorage.setItem('sl-theme', isDark ? 'dark' : 'light');
  }, [isDark]);

  const [instrument, setInstrument] = useState('NIFTY');
  const [underlying, setUnderlying] = useState('cash');
  const [strategyType, setStrategyType] = useState('positional');
  const [expiryBasis, setExpiryBasis] = useState('weekly');
  // YEARLY only. The leg holds the December contract while the position is
  // re-booked on this cadence — contract and cadence are two different
  // calendars, which is what makes yearly different from every other basis.
  const [rolloverCadence, setRolloverCadence] = useState('monthly');
  // YEARLY only. Which long-dated expiry months the position rolls THROUGH.
  // Default December-only = existing behaviour. Add March/June/September to
  // alternate — the engine holds each contract until its own T-n, then rolls
  // into the next selected long-dated expiry. Only these 4 months have
  // long-dated NIFTY contracts. December stays the anchor and can't be removed.
  const [yearlyRollMonths, setYearlyRollMonths] = useState(['12']);
  // T-n MONTHS before the December expiry at which the yearly contract rolls to
  // the next December. 0 = hold to expiry (default).
  const [yearlyExitMonthsBefore, setYearlyExitMonthsBefore] = useState(0);
  const [rolloverToggle, setRolloverToggle] = useState(false);
  const [rolloverMinDaysToExpiry, setRolloverMinDaysToExpiry] = useState(0);
  // PER-LEG ROLLOVER (opt-in): each leg rolls on its OWN expiry + own exit T-n;
  // trade boundaries are the union of all legs' rolls (a carried leg is
  // marked-to-market). Additive — OFF sends a byte-identical payload.
  const [perLegRollover, setPerLegRollover] = useState(false);
  const [noRollover, setNoRollover] = useState(false);
  const [noRolloverMinDays, setNoRolloverMinDays] = useState(0);
  // YEARLY is meaningless without rollover: the engine only pins the December
  // contract when rollover is active (simulate.rs rollover gate), so selecting
  // yearly forces it on and clears min-DTE — which the engine REJECTS under
  // yearly because it would advance the contract to the next cadence element.
  // Switching away from yearly leaves the user's choices alone.
  useEffect(() => {
    if (expiryBasis !== 'yearly') return;
    setRolloverToggle(true);
    setNoRollover(false);
    setRolloverMinDaysToExpiry(0);
  }, [expiryBasis]);
  // Sync Weekly Roll (multi-index mixed weekly+monthly): default ON, but inert
  // unless the strategy qualifies (see syncRollQualifies) AND Re-entry Rollover
  // is on. When engaged, the shortest (weekly) leg drives ONE cadence, every leg
  // re-enters together each week, and monthly legs roll on their OWN monthly expiry.
  const [syncWeeklyRollEnabled, setSyncWeeklyRollEnabled] = useState(true);
  // Strike-shift fallback: when the requested strike has no contract or zero
  // turnover (stale price), shift this many strike intervals further from ATM
  // in the originally-requested direction.  0 = no shift (drop trade if
  // untradeable), 1 = try one step (default), 2+ = more aggressive.
  const [strikeShiftMaxSteps, setStrikeShiftMaxSteps] = useState(1);
  const [entryDaysBefore, setEntryDaysBefore] = useState(2);
  const [exitDaysBefore, setExitDaysBefore] = useState(0);
  const [delayTime, setDelayTime] = useState('09:15');
  const [squareOffMode, setSquareOffMode] = useState('partial');
  const [legs, setLegs] = useState([]);
  const [lazyLegs, setLazyLegs] = useState({});
  const [lazyLegModal, setLazyLegModal] = useState({
    open: false,
    parentLegId: null,
    trigger: null,
    editingLazyLegId: null,
    parentLazyLegId: null,
    childTrigger: null,
  });

  const hasFuturesLeg = useMemo(
    () => legs.some(l => l.segment === 'futures'),
    [legs]
  );
  const hasMidcapLeg = useMemo(
    () => legs.some(l => l.segment === 'midcap100'),
    [legs]
  );
  // A MIDCPNIFTY leg of ANY segment (options or futures) — unlike Midcap100, which
  // is an untradeable overlay, MIDCPNIFTY is a real index the strategy holds. Its
  // presence is what reveals the MIDCPNIFTY spot-adjustment block.
  const hasMidcpniftyLeg = useMemo(
    () => legs.some(l => l.segment !== 'midcap100'
                    && String(l.index || instrument).toUpperCase() === 'MIDCPNIFTY'),
    [legs, instrument]
  );
  // Any second-index adjustment block on screen -> the first block needs its
  // "· NIFTY" qualifier so the two aren't ambiguous.
  const hasSecondIndexSpotAdj = hasMidcapLeg || hasMidcpniftyLeg;
  const indexConfig = useMemo(() => getIndexConfig(instrument), [instrument]);
  const expiryBasisOptions = useMemo(
    () => indexConfig.expiryBases.map(value => ({
      value,
      label: EXPIRY_BASIS_LABELS[value] || 'Monthly Expiry',
    })),
    [indexConfig]
  );
  const optionExpiryOptions = useMemo(
    () => getOptionExpiryOptions(instrument, expiryBasis),
    [instrument, expiryBasis]
  );
  const defaultOptionExpiry = indexConfig.defaultOptionExpiry;
  const unsupportedIndexMessage = `${instrument} backtest data is not available. Import option quotes and expiry calendar before running this index.`;

  const normalizeLegForSelectedIndex = useCallback((leg) => (
    // Midcap100 overlay legs have no expiry/strike of their own — pass through
    // untouched so index changes don't rewrite them.
    leg.segment === 'midcap100' ? { ...leg } : {
      ...leg,
      // Passing expiryBasis coerces legs to 'yearly' when the basis is yearly
      // (and back off it when the user leaves), so a leg can never be left on a
      // weekly contract while the strategy claims to trade December.
      expiry: normalizeExpiryForIndex(leg.expiry, instrument, leg.segment, expiryBasis),
      strike_interval: normalizeStrikeInterval(leg.strike_interval, leg.index || instrument),
      re_entry_target_mode: normalizeReEntryMode(leg.re_entry_target_mode),
      re_entry_sl_mode: normalizeReEntryMode(leg.re_entry_sl_mode),
    }
  ), [instrument, expiryBasis]);

  const selectInstrument = useCallback((symbol) => {
    const nextConfig = getIndexConfig(symbol);
    setInstrument(symbol);
    setExpiryBasis(prev => nextConfig.expiryBases.includes(prev) ? prev : nextConfig.defaultExpiryBasis);
  }, []);

  const handleUnderlyingChange = useCallback(
    (value) => {
      if (value === 'cash' && hasFuturesLeg) {
        return;
      }
      setUnderlying(value);
    },
    [hasFuturesLeg, setUnderlying]
  );

  const [spotAdjustmentEnabled, setSpotAdjustmentEnabled] = useState(false);
  const [spotAdjustmentDirection, setSpotAdjustmentDirection] = useState('rise');
  const [spotAdjustmentValue, setSpotAdjustmentValue] = useState(1.0);
  const [spotAdjustmentUnits, setSpotAdjustmentUnits] = useState('percent');
  const [spotAdjustmentShowInfo, setSpotAdjustmentShowInfo] = useState(true);
  // Per-index spot adjustment for the Midcap100 overlay leg (applied by the
  // overlay, not the engine). Shown as a second checkbox when a Midcap leg exists.
  const [midcapSpotAdjEnabled, setMidcapSpotAdjEnabled] = useState(false);
  const [midcapSpotAdjDirection, setMidcapSpotAdjDirection] = useState('rise');
  const [midcapSpotAdjValue, setMidcapSpotAdjValue] = useState(1.0);
  const [midcapSpotAdjUnits, setMidcapSpotAdjUnits] = useState('percent');
  // Per-index spot adjustment for a MIDCPNIFTY leg. Unlike the Midcap100 block
  // above (an overlay), this index is genuinely traded, so its breach truncates
  // the whole trade and re-enters same-day exactly like the NIFTY one.
  const [midcpSpotAdjEnabled, setMidcpSpotAdjEnabled] = useState(false);
  const [midcpSpotAdjDirection, setMidcpSpotAdjDirection] = useState('rise');
  const [midcpSpotAdjValue, setMidcpSpotAdjValue] = useState(1.0);
  const [midcpSpotAdjUnits, setMidcpSpotAdjUnits] = useState('percent');
  // Combine mode for when BOTH NIFTY and Midcap spot adjustment are on:
  // 'earliest' (default) = whichever breaches first; 'confirm' = both must breach
  // the SAME direction within N trading days of each other.
  const [spotAdjCombineMode, setSpotAdjCombineMode] = useState('earliest');
  const [spotAdjConfirmDays, setSpotAdjConfirmDays] = useState(0);
  const [bufferStrikeEnabled, setBufferStrikeEnabled] = useState(false);
  const [bufferStrikeValue, setBufferStrikeValue] = useState(0.5);
  const [bufferStrikeUnit, setBufferStrikeUnit] = useState('percent');
  const [bufferStrikeApplyTo, setBufferStrikeApplyTo] = useState('both');
  const [bufferPositionAbove, setBufferPositionAbove] = useState(true);
  const [bufferPositionBelow, setBufferPositionBelow] = useState(true);
  const [chargesEnabled, setChargesEnabled] = useState(false);

  // Unit-aware. The 0.25–5 window is a PERCENT rule and mirrors the engine's
  // own percent clamp (engine_rust.py: max(0.25, min(5.0, pct)) applied only
  // when units == 'percent'). A points threshold has no engine-side ceiling, so
  // clamping it to 5 here silently turned a 1000-point threshold into 5 points
  // — points are user-defined, floored only at 1 to keep them positive.
  const clampSpotAdjustmentValue = useCallback((value, units = 'percent') => {
    const numeric = Number(value);
    const isPoints = String(units).toLowerCase() === 'points';
    if (!Number.isFinite(numeric)) {
      return isPoints ? 1 : 0.25;
    }
    if (isPoints) {
      return Math.max(1, numeric);
    }
    return Math.min(5, Math.max(0.25, numeric));
  }, []);

  const normalizedSpotAdjustmentValue = useMemo(
    () => clampSpotAdjustmentValue(spotAdjustmentValue, spotAdjustmentUnits),
    [clampSpotAdjustmentValue, spotAdjustmentValue, spotAdjustmentUnits]
  );

  const [draftLeg, setDraftLeg] = useState({
    segment: 'options',
    position: 'sell',
    lot: 1,
    option_type: 'call',
    expiry: 'weekly',
    strike_criteria: 'strike_type',
    strike_type: 'atm',
    strike_interval: 50,
    premium_value: 0,
    premium_min: 0,
    premium_max: 0,
    pct_atm_moneyness: 'OTM',
    pct_value: 0,
    atm_straddle_prem_pct: 0,
    straddle_multiplier: 0.5,
    straddle_direction: '+',
    // Relative-to-leg strike (Iron Condor / spread wing): strike is derived
    // from an earlier leg's resolved strike, shifted `offset` gaps further OTM.
    ref_leg: 1,
    offset: 0,
    // Midcap100 cross-index overlay leg (segment === 'midcap100'):
    midcap_mode: 'hypothetical',     // 'spot' | 'hypothetical'
    cost_pct_per_month: 0.5,         // carry cost for hypothetical mode
    // Per-leg spot adjustment. OFF by default, in which case the leg follows
    // the strategy-level Spot Adjustment exactly as before. Switched on, this
    // leg measures its own breach with its own threshold/unit/direction — so a
    // weekly leg can adjust on 2% while a yearly leg adjusts on 300 points.
    spot_adj_enabled: false,
    spot_adj_value: 2,
    spot_adj_units: 'percent',       // 'percent' | 'points'
    spot_adj_direction: 'rise',      // 'rise' | 'fall' | 'both'
  });

  useEffect(() => {
    setExpiryBasis(prev => indexConfig.expiryBases.includes(prev) ? prev : indexConfig.defaultExpiryBasis);
    setDraftLeg(prev => normalizeLegForSelectedIndex(prev));
    setLegs(prev => prev.map(normalizeLegForSelectedIndex));
    setLazyLegs(prev => Object.fromEntries(
      Object.entries(prev).map(([id, leg]) => [id, normalizeLegForSelectedIndex(leg)])
    ));
  }, [indexConfig, normalizeLegForSelectedIndex]);

  // Force any leg on a monthly-only index (e.g. MIDCPNIFTY) to monthly expiry —
  // both in display and payload. Guarded to avoid render loops.
  useEffect(() => {
    const _monthlyOnly = (idx) => !((getIndexConfig(idx) || {}).expiryBases || []).includes('weekly');
    // A CROSS-index overlay leg (multi-index feature) is exempt — it may run
    // weekly (date-aware). Only force monthly for SAME-index monthly-only legs.
    const _crossIdx = (idx) => String(idx || instrument).toUpperCase() !== String(instrument).toUpperCase();
    setLegs(prev => {
      let changed = false;
      const next = prev.map(l => {
        if (l.segment !== 'midcap100' && !_crossIdx(l.index) && _monthlyOnly(l.index || instrument) && l.expiry !== 'monthly') {
          changed = true;
          return { ...l, expiry: 'monthly' };
        }
        return l;
      });
      return changed ? next : prev;
    });
    setDraftLeg(prev => {
      if (prev.segment !== 'midcap100' && !_crossIdx(prev.index) && _monthlyOnly(prev.index || instrument) && prev.expiry !== 'monthly') {
        return { ...prev, expiry: 'monthly' };
      }
      return prev;
    });
  }, [legs, draftLeg.index, draftLeg.segment, instrument]);

  // The left-column Index selector was removed — the Leg Builder index tabs are now
  // the sole index control. The strategy's base index follows the FIRST real
  // (non-overlay) leg's index, so single-index detection and the expiry basis stay
  // correct (selectInstrument also refreshes the expiry basis for the new index).
  useEffect(() => {
    const firstReal = (legs || []).find(l => String(l.segment || '').toLowerCase() !== 'midcap100');
    const idx = firstReal && firstReal.index ? String(firstReal.index).toUpperCase() : null;
    if (idx && idx !== String(instrument).toUpperCase()) {
      selectInstrument(idx);
    }
  }, [legs, instrument, selectInstrument]);

  const [overallSLEnabled, setOverallSLEnabled] = useState(false);
  const [overallSLType, setOverallSLType] = useState('max_loss');
  const [overallSLValue, setOverallSLValue] = useState(0);
  const [overallTgtEnabled, setOverallTgtEnabled] = useState(false);
  const [overallTgtType, setOverallTgtType] = useState('max_profit');
  const [overallTgtValue, setOverallTgtValue] = useState(0);
  const [trailingEnabled, setTrailingEnabled] = useState(false);
  const [trailingType, setTrailingType] = useState('lock');
  const [trailingIfProfit, setTrailingIfProfit] = useState(0);
  const [trailingLockProfit, setTrailingLockProfit] = useState(1);
  const [reentryOnSL, setReentryOnSL] = useState(false);
  const [reentryOnSLMode, setReentryOnSLMode] = useState('re_asap');
  const [reentryOnSLCount, setReentryOnSLCount] = useState(1);
  const [reentryOnTgt, setReentryOnTgt] = useState(false);
  const [reentryOnTgtMode, setReentryOnTgtMode] = useState('re_asap');
  const [reentryOnTgtCount, setReentryOnTgtCount] = useState(1);
  const [strFilter, setStrFilter] = useState({
    enabled: false,
    configId: '',
    configLabel: '',
    filterName: '',
    summary: null,
    segments: [],
    entryMode: 'fixed',
  });
  const [strSegments, setStrSegments] = useState({ '5x1': [], '5x2': [] });
  const [startDate, setStartDate] = useState('01/08/2024');
  const [endDate, setEndDate] = useState('28/04/2026');
  // Track per-instrument whether the user has manually edited dates
  const userEditedDatesRef = useRef(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [results, setResults] = useState(null);
  const [rawResults, setRawResults] = useState(null);
  const [displayResults, setDisplayResults] = useState(null);
  // STRYK sidebar nav (cosmetic — does not gate any rendering/logic)
  const [activeView, setActiveView] = useState('build');
  const resultsRef = useRef(null);
  // Advanced rules & settings accordion — presentation only; controls stay mounted
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [legwiseOpen, setLegwiseOpen] = useState(false);
  const [overallOpen, setOverallOpen] = useState(false);
  const [isRecalculating, setIsRecalculating] = useState(false);
  const [validationError, setValidationError] = useState(null);
  const [trailSLWarning, setTrailSLWarning] = useState(null);
  const warmCacheTimerRef = useRef(null);
  const jobPollRef = useRef(null);
  const errorTimerRef = useRef(null);
  const [jobId, setJobId] = useState(null);
  // Tracks the MOST RECENTLY STARTED job, independent of React state timing.
  // The Midcap-overlay fetch below is un-awaited and can resolve after a
  // second run has already started and reset the panel — without this guard
  // job A's overlay result silently overwrote job B's results on screen (same
  // shape: valid trades/summary, just from the wrong run).
  const latestJobIdRef = useRef(null);
  // Snapshot of the payload/config that PRODUCED the results on screen, taken
  // at submission time — NOT re-derived from live state on every render. The
  // Rules sheet and download filename used to read buildPayload()/legs/etc
  // LIVE, so editing a leg after a run completed (without re-running) changed
  // the downloaded workbook's declared config while the trades in its body
  // stayed from the OLD run — the artifact asserted a configuration that
  // never produced those rows. rulesSubmittedPayloadRef is the value actually
  // POSTed to /api/algotest/jobs for the run currently on screen.
  const rulesSubmittedPayloadRef = useRef(null);
  const [resultsSnapshotConfig, setResultsSnapshotConfig] = useState(null);
  const [jobStatusLabel, setJobStatusLabel] = useState('');
  const [jobState, setJobState] = useState('idle'); // 'idle' | 'queued' | 'running' | 'completed'
  const [cacheWarmReady, setCacheWarmReady] = useState(false);
  const [cacheWarmLabel, setCacheWarmLabel] = useState('');
  const [backtestMode, setBacktestMode] = useState('eod'); // kept 'eod' always; guards EOD-only UI (intraday removed)
  // Optimization panel state
  const [optimPanelOpen, setOptimPanelOpen] = useState(false);
  const [optimJob, setOptimJob] = useState(null); // { jobId, totalCombos, objective, runConfig }
  // Every optimize job queued this session (appended, never replaced) — feeds
  // AutoDownloadQueue so a job queued behind an already-running one still gets
  // auto-downloaded (ZIP/WOW-MOM/summary, patchwise by default) the instant it
  // finishes, even if its results panel was never opened. Hydrated from
  // localStorage on mount so a refreshed tab (or one queued from a sibling
  // tab) immediately resumes tracking the full backlog, not just this tab's
  // own history.
  const [optimQueueJobs, setOptimQueueJobs] = useState(() => loadQueue());
  // Lifted optimizer panel state — survives panel close/reopen
  const [optimChecked, setOptimChecked] = useState({});
  const [optimSavedValues, setOptimSavedValues] = useState({});
  // %/pts choice per swept param. Previously declared LOCALLY inside
  // OptimizePanel, so it was the one piece of that panel's state that did NOT
  // survive close/reopen (optimChecked/optimSavedValues above already lived
  // here for exactly this reason) — closing the panel with a leg's spot
  // adjustment set to "points" and reopening it silently reset that param back
  // to "percent" (and therefore its min/max/step back to the percent preset),
  // discarding a range the user had explicitly entered, with nothing in the
  // UI indicating a reset had happened.
  const [optimUnitChoice, setOptimUnitChoice] = useState({});
  const [optimMethod, setOptimMethod] = useState('exhaustive');
  const [optimSampleN, setOptimSampleN] = useState(200);
  const [optimAlgorithm, setOptimAlgorithm] = useState('cma-es');
  const [optimObjective, setOptimObjective] = useState('total_pnl');
  const [optimParallelism, setOptimParallelism] = useState(null);

  // LAN remote-worker "Core:" picker — see remote-worker/ and
  // backend/services/node_registry.py. selectedNode is null = run locally
  // (unchanged default behavior); otherwise it's a node from /api/system/nodes.
  const [lanNodes, setLanNodes] = useState([]);
  const [selectedNodeId, setSelectedNodeId] = useState(null);
  const [selectedNodeCores, setSelectedNodeCores] = useState(null);

  useEffect(() => {
    let cancelled = false;
    const poll = () => {
      fetch('/api/system/nodes')
        .then((r) => (r.ok ? r.json() : { nodes: [] }))
        .then((d) => {
          if (cancelled) return;
          const nodes = Array.isArray(d?.nodes) ? d.nodes : [];
          setLanNodes(nodes);
          // Selected node dropped offline (heartbeat expired) OR went stale
          // (its image no longer matches this box's code) — fall back to Local
          // so a job is never routed to a node that would reject or mis-run it.
          setSelectedNodeId((cur) => {
            if (!cur) return cur;
            const n = nodes.find((x) => x.node_id === cur);
            return (!n || n.stale) ? null : cur;
          });
        })
        .catch(() => {});
    };
    poll();
    const interval = setInterval(poll, 15000);
    return () => { cancelled = true; clearInterval(interval); };
  }, []);

  const selectedNode = lanNodes.find((n) => n.node_id === selectedNodeId) || null;

  const latestEntrySpot = useMemo(() => {
    const firstTrade = displayResults?.trades?.[0];
    if (!firstTrade) return null;
    const value = firstTrade.entry_spot ?? firstTrade.entrySpot ?? firstTrade['Entry Spot'];
    const parsed = value != null ? Number(value) : null;
    return Number.isFinite(parsed) ? parsed : null;
  }, [displayResults]);

  const spotAdjustmentHelperText = useMemo(() => {
    const entrySpot = latestEntrySpot ?? 10791;
    const entryFormatted = entrySpot.toLocaleString('en-IN', { maximumFractionDigits: 2 });
    const thresholdLabel = Number.isInteger(normalizedSpotAdjustmentValue)
      ? normalizedSpotAdjustmentValue.toFixed(0)
      : normalizedSpotAdjustmentValue.toFixed(2);
    const riseTarget =
      spotAdjustmentUnits === 'percent'
        ? entrySpot * (1 + normalizedSpotAdjustmentValue / 100)
        : entrySpot + normalizedSpotAdjustmentValue;
    const fallTarget =
      spotAdjustmentUnits === 'percent'
        ? entrySpot * (1 - normalizedSpotAdjustmentValue / 100)
        : entrySpot - normalizedSpotAdjustmentValue;
    const formatNumber = (val) => val.toLocaleString('en-IN', { maximumFractionDigits: 2 });
    const riseFormatted = formatNumber(riseTarget);
    const fallFormatted = formatNumber(fallTarget);

    if (spotAdjustmentUnits === 'percent') {
      if (spotAdjustmentDirection === 'both') {
        return `Exits if spot moves ±${thresholdLabel}% from entry`;
      }
      const directionLabel = spotAdjustmentDirection === 'rise' ? 'rise target' : 'fall target';
      const targetValue = spotAdjustmentDirection === 'rise' ? riseFormatted : fallFormatted;
      return `e.g. entry ${entryFormatted} → ${directionLabel} ${targetValue} at ${thresholdLabel}%`;
    }

    if (spotAdjustmentDirection === 'both') {
      return `Exits if spot moves ±${thresholdLabel} pts from entry`;
    }
    const directionLabel = spotAdjustmentDirection === 'rise' ? 'rise target' : 'fall target';
    const targetValue = spotAdjustmentDirection === 'rise' ? riseFormatted : fallFormatted;
    return `e.g. entry ${entryFormatted} → ${directionLabel} ${targetValue} pts`;
  }, [latestEntrySpot, normalizedSpotAdjustmentValue, spotAdjustmentDirection, spotAdjustmentUnits]);

  // Validate date is in DD/MM/YYYY format
  const isValidDate = useCallback((dateStr) => {
    return isValidDisplayDate(dateStr);
  }, []);

  const evaluateDateValidation = useCallback((nextStart, nextEnd) => {
    const shouldValidateStart = !!nextStart?.trim();
    const shouldValidateEnd = !!nextEnd?.trim();
    const invalidStart = shouldValidateStart && !isValidDate(nextStart);
    const invalidEnd = shouldValidateEnd && !isValidDate(nextEnd);
    if (invalidStart || invalidEnd) {
      setValidationError('Invalid date format. Use DD/MM/YYYY');
    } else {
      setValidationError(null);
    }
  }, [isValidDate]);

  const handleStartDateChange = (formatted) => {
    userEditedDatesRef.current = true;
    setStartDate(formatted);
    evaluateDateValidation(formatted, endDate);
  };

  const handleEndDateChange = (formatted) => {
    userEditedDatesRef.current = true;
    setEndDate(formatted);
    evaluateDateValidation(startDate, formatted);
  };

  // On mount and on instrument change, default the end date to the last available
  // date in the data (and start date to ~2 years before it). Skip if user has
  // already edited the dates manually.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(`/api/data/dates?index=${encodeURIComponent(instrument)}`);
        if (!res.ok) return;
        const { min_date, max_date } = await res.json();
        if (cancelled || !max_date || userEditedDatesRef.current) return;
        const toDDMMYYYY = (iso) => {
          const [y, m, d] = iso.split('-');
          return `${d}/${m}/${y}`;
        };
        // Start defaults to 01/01/2025; end defaults to the last available data date.
        const startStr = '01/01/2025';
        const endStr = toDDMMYYYY(max_date);
        setStartDate(startStr);
        setEndDate(endStr);
        evaluateDateValidation(startStr, endStr);
      } catch (e) {
        // network or parse error — keep the hardcoded defaults
      }
    })();
    return () => { cancelled = true; };
  }, [instrument, evaluateDateValidation]);

  const stopJobPolling = useCallback(() => {
    if (jobPollRef.current) {
      clearTimeout(jobPollRef.current);
      jobPollRef.current = null;
    }
  }, []);

  // Free the worker if the user closes the tab, hard-refreshes, or navigates
  // away while a backtest is still running. `pagehide` fires on close/refresh/
  // navigation but NOT on a plain tab-switch, so we don't cancel jobs the user
  // means to keep. `keepalive` lets the DELETE outlive the page unload (and the
  // backend DELETE both revokes the Celery task and releases the memory gate).
  useEffect(() => {
    if (!jobId) return undefined;
    const cancelOnExit = () => {
      try {
        fetch(`/api/algotest/jobs/${jobId}`, { method: 'DELETE', keepalive: true });
      } catch {}
    };
    window.addEventListener('pagehide', cancelOnExit);
    return () => window.removeEventListener('pagehide', cancelOnExit);
  }, [jobId]);

  // ── Midcap cross-index overlay (additive) ─────────────────────────────────
  // After the NIFTY backtest completes, price the Midcap100 overlay leg(s) for
  // each trade cycle and attach the result as `payload.midcap`. No-op when no
  // Midcap leg is configured (so the existing flow is untouched).
  const _midcapNum = (v) => { const n = Number(v); return Number.isFinite(n) ? n : null; };

  const buildMidcapConfig = useCallback(() => {
    const midcap_legs = legs
      .filter(l => l.segment === 'midcap100')
      .map(l => ({
        midcap_mode: (l.midcap_mode || 'hypothetical').toLowerCase(),
        cost_pct_per_month: Number(l.cost_pct_per_month) || 0,
        position: (l.position || 'buy').toUpperCase(),
        lots: l.lot || 1,
        symbol: 'NIFTYMIDCAP100',
      }));
    const midcap_spot_adjustment = (midcap_legs.length && midcapSpotAdjEnabled)
      ? {
          enabled: true,
          direction: midcapSpotAdjDirection,
          pct: clampSpotAdjustmentValue(midcapSpotAdjValue, midcapSpotAdjUnits),
          units: midcapSpotAdjUnits,
        }
      : null;
    return { midcap_legs, midcap_spot_adjustment };
  }, [legs, midcapSpotAdjEnabled, midcapSpotAdjDirection, midcapSpotAdjValue, midcapSpotAdjUnits, clampSpotAdjustmentValue]);

  const projectTradesForOverlay = useCallback((trades) => {
    // One projected row per Trade id (matching the Excel export's grouping):
    // sum the NIFTY-leg P&L across all of that trade's rows (legs + re-entries),
    // span = earliest Entry Date → latest Exit Date. trade_id is String(Trade)
    // so it lines up with the export's per-trade map.
    const cmp = (d) => {
      const s = String(d || '').trim();
      let m = s.match(/^(\d{2})-(\d{2})-(\d{4})/);
      if (m) return `${m[3]}${m[2]}${m[1]}`;
      m = s.match(/^(\d{4})-(\d{2})-(\d{2})/);
      if (m) return `${m[1]}${m[2]}${m[3]}`;
      return s;
    };
    // Group the per-leg rows, then derive the trade-level values with the SAME
    // anchor rule the backend uses (backend/services/trade_anchor.py and
    // optimizer/excel_builder.py::_project_rows_for_midcap) so a direct backtest
    // and an optimizer combo produce identical Combined numbers.
    const rowsByKey = new Map();
    for (const t of trades || []) {
      const tid = t['Trade'] ?? t.trade;
      if (tid === undefined || tid === null || tid === '') continue;
      const key = String(tid);
      if (!rowsByKey.has(key)) rowsByKey.set(key, []);
      rowsByKey.get(key).push(t);
    }

    const isReEntry = (t) => Boolean(
      t['ReEntryIndex'] || t['ReEntryTrigger'] || t['ReEntryMode'] ||
      String(t['Leg'] ?? '').includes('.') || String(t['Index'] ?? '').includes('.')
    );
    const legNo = (t) => {
      const n = Number(t['Leg'] ?? t.leg);
      return Number.isFinite(n) ? n : 0;
    };

    const out = [];
    for (const [key, all] of rowsByKey) {
      const mains = all.filter(t => !isReEntry(t));
      const rows = mains.length ? mains : all;

      // ANCHOR row = LATEST Entry Date, ties broken by the LOWEST Leg number.
      // A CARRIED yearly leg holds an older entry date than the weekly leg that
      // re-enters each cycle, so taking "the first row" made the overlay window
      // and the % P&L denominator depend on the user's configured leg order.
      // Legs that enter together share one date, so the anchor is Leg 1 and
      // ordinary strategies are unchanged.
      let anchor = null;
      for (const t of rows) {
        const e = cmp(t['Entry Date'] ?? t.entry_date ?? '');
        if (!e) continue;
        if (anchor === null) { anchor = t; continue; }
        const ae = cmp(anchor['Entry Date'] ?? anchor.entry_date ?? '');
        if (e > ae || (e === ae && legNo(t) < legNo(anchor))) anchor = t;
      }
      if (!anchor) continue;

      const entry = anchor['Entry Date'] ?? anchor.entry_date;
      const exit = anchor['Exit Date'] ?? anchor['Leg Exit Date'] ?? anchor.exit_date;
      if (!entry || !exit) continue;

      // PARENT row = LOWEST Leg number — where the engine writes the trade
      // total (native/src/simulate.rs:1794-1806). Summing the Net P&L column
      // across rows double-counts, because non-parent rows carry their OWN
      // per-leg P&L (see backend/services/algotest_job.py:446-450).
      let parent = rows[0];
      for (const t of rows) if (legNo(t) < legNo(parent)) parent = t;
      let net = _midcapNum(parent['Net P&L'] ?? parent.net_pnl);
      if (net == null) {
        net = all.reduce((sum, t) => sum
          + (_midcapNum(t['CE P&L']) || 0)
          + (_midcapNum(t['PE P&L']) || 0)
          + (_midcapNum(t['FUT P&L']) || 0), 0);
      }
      const es = _midcapNum(anchor['Entry Spot'] ?? anchor.entry_spot) || 0;
      const pct = es ? Math.round((net / es) * 100.0 * 1e4) / 1e4 : 0;

      out.push({ trade_id: key, entry_date: entry, exit_date: exit, nifty_pnl: net, nifty_pnl_pct: pct });
    }
    return out;
  }, []);

  const applyMidcapOverlay = useCallback(async (payload) => {
    const { midcap_legs, midcap_spot_adjustment } = buildMidcapConfig();
    if (!midcap_legs.length || !Array.isArray(payload?.trades) || !payload.trades.length) {
      return payload;
    }
    const rows = projectTradesForOverlay(payload.trades);
    if (!rows.length) return payload;
    try {
      const res = await fetch('/api/midcap-overlay', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        // The engine already applies the Midcap spot-adjustment (truncates the
        // trade + re-enters), so the overlay must NOT re-apply it — it just
        // prices the Midcap leg over each trade's window. Pass null.
        body: JSON.stringify({ rows, midcap_legs, midcap_spot_adjustment: null, symbol: 'NIFTYMIDCAP100' }),
      });
      if (!res.ok) return payload;
      const data = await res.json();
      const byTrade = {};
      for (const r of (data.results || [])) {
        if (r && r.trade_id != null) byTrade[String(r.trade_id)] = r;
      }
      return {
        ...payload,
        midcap: {
          available: Boolean(data.available),
          summary: data.summary || {},
          byTrade,
          spot_adjustment: midcap_spot_adjustment,
          legs: midcap_legs,
        },
      };
    } catch (e) {
      console.warn('[midcap-overlay] failed', e);
      return payload;
    }
  }, [buildMidcapConfig, projectTradesForOverlay]);

  useEffect(() => {
    return () => stopJobPolling();
  }, [stopJobPolling]);

  const pollJobStatus = useCallback((jobId) => {
    stopJobPolling();
    setJobState('queued');
    const startedAt = Date.now();

    const fetchStatus = async () => {
      try {
        const res = await fetch(`/api/algotest/jobs/${jobId}`);
        if (!res.ok) throw new Error('Job status fetch failed');
        const data = await res.json();

        if (data.status === 'completed') {
          setJobState('completed');
          stopJobPolling();
          setJobStatusLabel('');
          setJobId(null);
          setLoading(false);
          const payload = data.result;
          console.log('[pollJobStatus] payload:', JSON.stringify(payload).slice(0, 500));
          if (!payload) {
            setError('Backtest completed without a result payload.');
            return;
          }
          setRawResults(payload);
          setDisplayResults(payload);
          setChargesEnabled(payload?.meta?.charges_enabled ?? false);
          setResults(payload);
          // Midcap overlay (additive): enrich with the cross-index leg if present.
          // Un-awaited, so a SECOND run can start (loading was already cleared
          // above) and finish before this resolves — guard against writing a
          // stale job's enriched results over the job that's on screen now.
          applyMidcapOverlay(payload).then(enriched => {
            if (jobId !== latestJobIdRef.current) return;
            if (enriched && enriched.midcap) {
              setRawResults(enriched);
              setDisplayResults(enriched);
              setResults(enriched);
            }
          });
          if (Array.isArray(payload?.trades) && payload.trades.length === 0) {
            // Don't blame the filter unless it's actually implicated. The old
            // message said "No trades matched the <filter> filter" for EVERY
            // zero-trade run with a filter on, which sent people hunting the
            // filter when the real cause was elsewhere — e.g. MIDCPNIFTY weekly
            // options end 25-Nov-2024, so a weekly leg after that date has no
            // contract to book while the filter covers the range perfectly.
            const toIso = (s) => {
              const m = String(s || '').match(/^(\d{2})\/(\d{2})\/(\d{4})$/);
              return m ? `${m[3]}-${m[2]}-${m[1]}` : String(s || '');
            };
            const from = toIso(startDate);
            const to = toIso(endDate);
            const segs = Array.isArray(strFilter.segments) ? strFilter.segments : [];
            const covering = segs.filter((s) => {
              const ss = String(s?.start || s?.Start || '');
              const se = String(s?.end || s?.End || '');
              return ss && se && ss <= to && se >= from;
            });
            if (strFilter.enabled && segs.length > 0 && covering.length === 0) {
              setError(`No ${strFilter.configLabel} segment overlaps ${from} → ${to}. Widen the date range or choose a different filter.`);
            } else if (strFilter.enabled && covering.length > 0) {
              setError(`0 trades for ${from} → ${to}. ${covering.length} ${strFilter.configLabel} segment(s) do cover this range, so the filter is not the cause — check that each leg's Expiry has contracts for this period (some indices stop publishing weekly expiries).`);
            } else {
              setError(`0 trades for ${from} → ${to}. Check that each leg's Expiry has contracts for this period, and that the date range is the one you meant.`);
            }
          } else {
            setError(null);
          }
          return;
        }

        if (data.status === 'failed') {
          setJobState('idle');
          stopJobPolling();
          setJobStatusLabel('');
          setJobId(null);
          setLoading(false);
          setError(data.error || 'Backtest job failed');
          return;
        }

        if (data.status === 'running') {
          setJobState('running');
          setJobStatusLabel(data.meta?.status || 'Running backtest…');
        } else {
          setJobState('queued');
          const depth = Number(data.queue_depth);
          setJobStatusLabel(Number.isFinite(depth) && depth > 0 ? `Queued (${depth} ahead)…` : 'Queued…');
        }

        const elapsedMs = Date.now() - startedAt;
        const intervalMs = elapsedMs < 5000
          ? 250
          : elapsedMs < 30000
            ? 1000
            : 2000;
        jobPollRef.current = setTimeout(fetchStatus, intervalMs);
      } catch (err) {
        setJobState('idle');
        stopJobPolling();
        setLoading(false);
        setJobStatusLabel('');
        setJobId(null);
        setError('Unable to poll backtest job status.');
      }
    };

    jobPollRef.current = setTimeout(fetchStatus, 0);
  }, [stopJobPolling, strFilter.configLabel, strFilter.enabled, strFilter.segments, startDate, endDate, applyMidcapOverlay]);

  const formatSummaryDateInput = (value) => {
    if (!value) return null;
    const parsed = value instanceof Date ? value : new Date(value);
    if (Number.isNaN(parsed.getTime())) return null;
    return format(parsed, 'dd/MM/yyyy');
  };

  // When filter is toggled ON, automatically set date range to filter's start/end
  // Default start is capped at 01/01/2019
  const STR_DEFAULT_START = '01/01/2019';
  const STR_DEFAULT_START_DATE = new Date('2019-01-01');
  const prevFilterEnabledRef = useRef(false);
  const lastAutoDateRef = useRef(null);
  const [showFullRange, setShowFullRange] = useState(false);
  useEffect(() => {
    if (strFilter.enabled && !prevFilterEnabledRef.current && strFilter.summary?.range) {
      const filterStart = formatSummaryDateInput(strFilter.summary.range.from);
      const filterEnd = formatSummaryDateInput(strFilter.summary.range.to);

      const filterStartDate = new Date(strFilter.summary.range.from);
      const needsLimited = filterStartDate < STR_DEFAULT_START_DATE;

      if (needsLimited && !showFullRange) {
        if (filterEnd) {
          setStartDate(STR_DEFAULT_START);
          setEndDate(filterEnd);
          lastAutoDateRef.current = `${STR_DEFAULT_START}|${filterEnd}`;
          userEditedDatesRef.current = true;
          evaluateDateValidation(STR_DEFAULT_START, filterEnd);
        }
      } else {
        if (filterStart && filterEnd) {
          setStartDate(filterStart);
          setEndDate(filterEnd);
          lastAutoDateRef.current = `${filterStart}|${filterEnd}`;
          userEditedDatesRef.current = true;
          evaluateDateValidation(filterStart, filterEnd);
        }
      }
    }
    prevFilterEnabledRef.current = strFilter.enabled;
    if (!strFilter.enabled) {
      setShowFullRange(false);
      userEditedDatesRef.current = false;
    }
  }, [strFilter.enabled, strFilter.summary?.range, evaluateDateValidation, showFullRange]);

  // Also update dates when range changes (e.g., different filter config selected)
  useEffect(() => {
    if (!strFilter.enabled || !strFilter.summary?.range) return;

    const filterStart = formatSummaryDateInput(strFilter.summary.range.from);
    const filterEnd = formatSummaryDateInput(strFilter.summary.range.to);

    const filterStartDate = new Date(strFilter.summary.range.from);
    const needsLimited = filterStartDate < STR_DEFAULT_START_DATE;

    const proposedStart = (needsLimited && !showFullRange) ? STR_DEFAULT_START : filterStart;

    if (!proposedStart || !filterEnd) return;
    const currentAutoKey = `${proposedStart}|${filterEnd}`;
    if (lastAutoDateRef.current === currentAutoKey) return;
    setStartDate(proposedStart);
    setEndDate(filterEnd);
    lastAutoDateRef.current = currentAutoKey;
    userEditedDatesRef.current = true;
    evaluateDateValidation(proposedStart, filterEnd);
  }, [
    strFilter.enabled,
    strFilter.summary?.range?.from,
    strFilter.summary?.range?.to,
    startDate,
    endDate,
    evaluateDateValidation,
    showFullRange,
  ]);

  // Memoize static derived values so they don't rebuild on every keystroke
  const daysOptions = useMemo(
    () => expiryBasis === 'weekly' ? [0, 1, 2, 3, 4] : Array.from({ length: 25 }, (_, i) => i),
    [expiryBasis]
  );

  const strikeTypeOpts = useMemo(() => [
    ...Array.from({ length: 20 }, (_, i) => ({ value: `itm${20 - i}`, label: `ITM ${20 - i}` })),
    { value: 'atm', label: 'ATM' },
    ...Array.from({ length: 20 }, (_, i) => ({ value: `otm${i + 1}`, label: `OTM ${i + 1}` })),
  ], []);

  useEffect(() => {
    let mounted = true;
    const loadSegments = async () => {
      const endpoints = ['/api/backtest/str-segments', '/api/str-segments', '/str-segments'];
      for (const url of endpoints) {
        try {
          const res = await fetch(url);
          if (!res.ok) continue;
          const data = await res.json();
          if (!mounted) return;
          setStrSegments({
            '5x1': Array.isArray(data?.['5x1']) ? data['5x1'] : [],
            '5x2': Array.isArray(data?.['5x2']) ? data['5x2'] : [],
          });
          return;
        } catch (_) {
          // Try next endpoint
        }
      }
      console.warn('STR segments fetch failed on all endpoints');
    };
    loadSegments();
    return () => {
      mounted = false;
    };
  }, []);

  useEffect(() => {
    if (warmCacheTimerRef.current) {
      clearTimeout(warmCacheTimerRef.current);
      warmCacheTimerRef.current = null;
    }
    if (!indexConfig.backtestEnabled || !isValidDate(startDate) || !isValidDate(endDate)) {
      setCacheWarmReady(true);
      setCacheWarmLabel('');
      return undefined;
    }

    const controller = new AbortController();
    setCacheWarmReady(false);
    setCacheWarmLabel('Preparing worker data cache...');
    warmCacheTimerRef.current = setTimeout(async () => {
      try {
        const res = await fetch('/api/backtest/warm-cache', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            index: instrument,
            from_date: getApiStartDate(startDate),
            to_date: getApiEndDate(endDate),
          }),
          signal: controller.signal,
        });
        if (!res.ok) throw new Error('Cache warm failed');
        const data = await res.json();
        if (controller.signal.aborted) return;
        if (data.status === 'ready') {
          const elapsed = Number(data.elapsed_seconds);
          setCacheWarmLabel(Number.isFinite(elapsed) ? `Worker cache ready (${elapsed.toFixed(1)}s)` : 'Worker cache ready');
        } else if (data.status === 'warming') {
          setCacheWarmLabel('Worker cache warming in queue...');
        } else {
          setCacheWarmLabel('');
        }
      } catch (_) {
        if (!controller.signal.aborted) setCacheWarmLabel('');
      } finally {
        if (!controller.signal.aborted) setCacheWarmReady(true);
      }
    }, 700);

    return () => {
      controller.abort();
      if (warmCacheTimerRef.current) {
        clearTimeout(warmCacheTimerRef.current);
        warmCacheTimerRef.current = null;
      }
    };
  }, [instrument, startDate, endDate, indexConfig.backtestEnabled, isValidDate]);

  // Upload management removed - handled by CsvUpload component

  const validationErrorTimerRef = useRef(null);
  const showValidationError = (msg, durationMs = 5000) => {
    if (validationErrorTimerRef.current) clearTimeout(validationErrorTimerRef.current);
    setValidationError(msg);
    validationErrorTimerRef.current = setTimeout(() => setValidationError(null), durationMs);
  };

  const showTimedError = (msg, durationMs = 5000) => {
    if (errorTimerRef.current) clearTimeout(errorTimerRef.current);
    setError(msg);
    errorTimerRef.current = setTimeout(() => setError(null), durationMs);
  };

  useEffect(() => {
    return () => {
      if (warmCacheTimerRef.current) clearTimeout(warmCacheTimerRef.current);
      if (validationErrorTimerRef.current) clearTimeout(validationErrorTimerRef.current);
      if (errorTimerRef.current) clearTimeout(errorTimerRef.current);
    };
  }, []);

  // Validate expiry mismatch
  const validateExpiry = () => {
    if (!indexConfig.backtestEnabled) {
      showValidationError(unsupportedIndexMessage);
      return false;
    }
    if (!indexConfig.expiryBases.includes(expiryBasis)) {
      showValidationError(`${instrument} supports ${indexConfig.defaultExpiryBasis} expiry only.`);
      return false;
    }
    if (!indexConfig.expiryBases.includes('weekly')) {
      const weeklyLegs = legs.filter(l => ['weekly', 'next_weekly'].includes(String(l.expiry || '').toLowerCase()));
      if (weeklyLegs.length > 0) {
        const legNumbers = weeklyLegs.map((_, i) => i + 1).join(', ');
        showValidationError(`${instrument} is monthly-only. Leg(s) ${legNumbers} cannot use weekly expiry.`);
        return false;
      }
    }
    // Multi-index / multi-expiry feature: when any leg is on a different index
    // than the strategy, legs are deliberately allowed to mix weekly + monthly
    // expiries (each leg runs on its own cadence), so skip the basis-match checks.
    const _stratIdx = String(instrument || 'NIFTY').toUpperCase();
    const _isMultiIndex = legs.some(l => String(l.index || _stratIdx).toUpperCase() !== _stratIdx);
    // Derived cadence: finest leg wins. Only options legs carry a real
    // weekly/monthly expiry — futures and Midcap100 legs do not (Midcap follows
    // the NIFTY trade's dates), so they never set or contradict the cadence.
    const _optLegs = legs.map((l, i) => ({ l, i })).filter(({ l }) => l.segment === 'options');
    const _cadenceIsWeekly =
      indexConfig.expiryBases.includes('weekly') &&
      _optLegs.some(({ l }) => ['weekly', 'next_weekly'].includes(l.expiry));
    // Legs coarser than the derived cadence — these get pinned to their own contract.
    const _mixedExpiryLegs = _cadenceIsWeekly
      ? _optLegs.filter(({ l }) => ['monthly', 'next_monthly'].includes(l.expiry))
      : [];
    const _isFixedEntryMode = strFilter.enabled && (strFilter.entryMode || 'fixed') === 'fixed';
    // Only options legs carry a real weekly/monthly expiry. Futures and Midcap100
    // legs do not (Midcap follows the NIFTY trade's dates), so exclude them here.
    // SAME-INDEX MIXED EXPIRY. Weekly + monthly legs on one index are now
    // supported: the cadence is DERIVED from the legs (finest wins) and any
    // coarser leg is pinned to its own contract, exactly as a YEARLY leg is.
    // The old basis-vs-leg blocks are gone — the basis no longer has to agree
    // with the legs, because the legs decide it.
    //
    // One hard requirement remains: only the fixed-entry scheduler resolves the
    // pin. The DTE / min-days schedulers pick every leg's contract out of the
    // cadence list, so a monthly leg there would silently get a WEEKLY contract.
    // The engine hard-fails on that; surface it here as a readable message.
    if (!_isMultiIndex && _mixedExpiryLegs.length > 0 && !_isFixedEntryMode) {
      const legNumbers = _mixedExpiryLegs.map(({ i }) => i + 1).join(', ');
      showValidationError(
        `Mixed expiry — Leg(s) ${legNumbers} are monthly while the strategy runs on a weekly cadence. ` +
        `This requires Fixed Entry: enable the Filter and set Entry Mode to "Fixed Entry — Pinned to segment start".`
      );
      return false;
    }
    setValidationError(null);
    return true;
  };

  // Clear expiry warning when user changes legs or basis so it doesn't persist after fix
  useEffect(() => { setValidationError(null); }, [legs, expiryBasis]); // eslint-disable-line react-hooks/exhaustive-deps

  const canRunBacktest = legs.length > 0 && !loading && indexConfig.backtestEnabled;

  const handleRecalculate = useCallback(async () => {
    if (!rawResults?.trades?.length) return;

    setIsRecalculating(true);
    setError(null);
    try {
      const res = await fetch('/api/backtest/recalculate-slippage', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(sanitizePayload({
          trades: rawResults.trades,
          charges_enabled: chargesEnabled,
          initial_capital: 100000,
        })),
      });

      if (!res.ok) {
        const payload = await res.json().catch(() => null);
        throw new Error(payload?.detail || payload?.error || `Recalculate failed (${res.status})`);
      }

      const data = await res.json();
      const nextResults = {
        ...rawResults,
        trades: data.trades || [],
        summary: data.summary || {},
        pivot: data.pivot || { headers: [], rows: [] },
        meta: {
          ...(rawResults.meta || {}),
          ...(data.meta || {}),
          charges_enabled: chargesEnabled,
        },
      };
      const enriched = await applyMidcapOverlay(nextResults);
      setDisplayResults(enriched);
      setResults(enriched);
    } catch (err) {
      setError(err.message || 'Recalculate failed');
    } finally {
      setIsRecalculating(false);
    }
  }, [rawResults, chargesEnabled, applyMidcapOverlay]);

  const addLegFromDraft = () => {
    if (legs.length >= 12) return;
    const normalizedDraft = normalizeLegForSelectedIndex(draftLeg);
    setLegs(prev => [...prev, {
      id: Date.now(),
      ...normalizedDraft,
      target_enabled: false, target_mode: 'PERCENT', target_value: 0,
      stop_loss_enabled: false, stop_loss_mode: 'PERCENT', stop_loss_value: 0,
      sl_buffer_enabled: false, sl_buffer_mode: 'PERCENT', sl_buffer_value: null, sl_buffer_pct: null,
      trail_sl_enabled: false, trail_sl_mode: 'PERCENT', trail_sl_trigger: 0, trail_sl_move: 0,
      re_entry_target_enabled: false, re_entry_target_mode: 'RE_ASAP', re_entry_target_count: 1,
      re_entry_sl_enabled: false, re_entry_sl_mode: 'RE_ASAP', re_entry_sl_count: 1,
      // This leg's own slippage toggle + %, independent of every other leg
      // (e.g. a futures hedge leg can stay off while an options leg has its
      // own value).
      slippage_enabled: false, slippage_pct: 0,
      lazy_leg_sl_id: null,
      lazy_leg_target_id: null,
      simple_momentum_enabled: false, simple_momentum_mode: 'POINTS_UP', simple_momentum_value: 0,
      straddle_multiplier: normalizedDraft.straddle_multiplier ?? 0.5,
      straddle_direction: normalizedDraft.straddle_direction ?? '+',
      rollover_strike_mode: 'fresh',
    }]);
    if (draftLeg.segment === 'futures') {
      setUnderlying('futures');
    }
    setDraftLeg(prev => ({
      ...prev,
      strike_type: 'atm',
      strike_interval: normalizeStrikeInterval(prev.strike_interval),
      premium_value: 0,
      premium_min: 0,
      premium_max: 0,
      pct_atm_moneyness: 'OTM',
      pct_value: 0,
      expiry: normalizeExpiryForIndex(prev.expiry, instrument, prev.segment, expiryBasis),
      atm_straddle_prem_pct: 0,
      straddle_multiplier: 0.5,
      straddle_direction: '+',
    }));
  };
  const addLeg = addLegFromDraft;

  const removeLeg = (id) => {
    const leg = legs.find(l => l.id === id);
    const lazyIds = [leg?.lazy_leg_sl_id, leg?.lazy_leg_target_id].filter(Boolean);
    setLegs(prev => prev.filter(l => l.id !== id));
    if (lazyIds.length) {
      setLazyLegs(prev => {
        const next = { ...prev };
        lazyIds.forEach(lazyId => delete next[lazyId]);
        return next;
      });
    }
  };
  const updateLeg = (id, field, value) => setLegs(prev => prev.map((l, idx) => {
    if (l.id !== id) return l;
    const next = { ...l, [field]: value };
    if (field === 'segment' || field === 'expiry') {
      next.expiry = normalizeExpiryForIndex(next.expiry, instrument, next.segment, expiryBasis);
    }
    // Relative-to-Leg (Iron Condor wing): when a leg becomes rel_leg or its
    // parent changes, auto-configure it as the protective wing of that parent —
    // same option type, opposite position (Sell short → Buy wing).
    if ((field === 'strike_criteria' && value === 'rel_leg') ||
        (field === 'ref_leg' && next.strike_criteria === 'rel_leg')) {
      const parent = prev[(Number(next.ref_leg) || 1) - 1];
      if (parent && parent.segment === 'options') {
        next.option_type = parent.option_type;
        next.position = parent.position === 'sell' ? 'buy' : 'sell';
      }
    }
    return next;
  }));
  const handleLegChange = (legIndex, nextLeg) => setLegs(prev => prev.map((leg, idx) => idx === legIndex ? { ...leg, ...nextLeg } : leg));
  const handleLegFilterUpload = async (legId, event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    updateLeg(legId, 'filter_uploading', true);
    updateLeg(legId, 'filter_error', '');
    try {
      const formData = new FormData();
      formData.append('file', file);
      const res = await fetch('/api/upload-filter-csv', { method: 'POST', body: formData });
      if (!res.ok) throw new Error(`Server error ${res.status}`);
      const data = await res.json();
      if (!data.success) throw new Error(data.message || 'Failed to parse CSV');
      const segs = (data.segments || []).map(s => ({
        start: s.start_date || s.start,
        end: s.end_date || s.end,
      }));
      if (!segs.length) throw new Error('No valid date ranges found');
      updateLeg(legId, 'filter_segments', segs);
      updateLeg(legId, 'filter_file_name', file.name);
    } catch (err) {
      updateLeg(legId, 'filter_error', err.message || 'Upload failed');
      updateLeg(legId, 'filter_segments', []);
    } finally {
      updateLeg(legId, 'filter_uploading', false);
      event.target.value = '';
    }
  };
  const totalLazyLegCount = Object.keys(lazyLegs).length;
  const lazyLegList = Object.values(lazyLegs);

  const updateLazyLeg = (id, field, value) => {
    setLazyLegs(prev => {
      if (!prev[id]) return prev;
      const nextLeg = { ...prev[id], [field]: value };
      if (field === 'expiry' || field === 'segment') {
        nextLeg.expiry = normalizeExpiryForIndex(nextLeg.expiry, instrument, nextLeg.segment, expiryBasis);
      }
      return { ...prev, [id]: nextLeg };
    });
  };

  const createLazyLegConfig = (overrides = {}) => {
    const id = `lazy_${Date.now()}_${Math.floor(Math.random() * 1000)}`;
    return normalizeLegForSelectedIndex({
      ...createDefaultLazyLeg(totalLazyLegCount + 1),
      id,
      name: `lazy${totalLazyLegCount + 1}`,
      ...overrides,
    });
  };

  const attachLazyLegToParent = (parentLegId, trigger, lazyId) => {
    updateLeg(parentLegId, trigger === 'sl' ? 'lazy_leg_sl_id' : 'lazy_leg_target_id', lazyId);
  };

  const attachLazyLegToLazyParent = (parentLazyLegId, trigger, lazyId) => {
    setLazyLegs(prev => prev[parentLazyLegId] ? ({
      ...prev,
      [parentLazyLegId]: {
        ...prev[parentLazyLegId],
        [trigger === 'sl' ? 'child_lazy_leg_sl_id' : 'child_lazy_leg_target_id']: lazyId,
      },
    }) : prev);
  };

  const ensureLazyLegForParent = (parentLegId, trigger) => {
    const parentLeg = legs.find(l => l.id === parentLegId);
    const existingId = trigger === 'sl' ? parentLeg?.lazy_leg_sl_id : parentLeg?.lazy_leg_target_id;
    if (existingId && lazyLegs[existingId]) return existingId;
    if (totalLazyLegCount >= 10) {
      alert('Maximum 10 lazy legs allowed per strategy.');
      return null;
    }
    const lazyLeg = createLazyLegConfig();
    setLazyLegs(prev => ({ ...prev, [lazyLeg.id]: lazyLeg }));
    attachLazyLegToParent(parentLegId, trigger, lazyLeg.id);
    return lazyLeg.id;
  };

  const ensureChildLazyLeg = (parentLazyLegId, trigger) => {
    const parentLazyLeg = lazyLegs[parentLazyLegId];
    const existingId = trigger === 'sl' ? parentLazyLeg?.child_lazy_leg_sl_id : parentLazyLeg?.child_lazy_leg_target_id;
    if (existingId && lazyLegs[existingId]) return existingId;
    if (totalLazyLegCount >= 10) {
      alert('Maximum 10 lazy legs allowed per strategy.');
      return null;
    }
    const lazyLeg = createLazyLegConfig();
    setLazyLegs(prev => ({
      ...prev,
      [lazyLeg.id]: lazyLeg,
      [parentLazyLegId]: {
        ...prev[parentLazyLegId],
        [trigger === 'sl' ? 'child_lazy_leg_sl_id' : 'child_lazy_leg_target_id']: lazyLeg.id,
      },
    }));
    return lazyLeg.id;
  };

  const openLazyLegModal = (parentLegId, trigger) => {
    const parentLeg = legs.find(l => l.id === parentLegId);
    const existingId = trigger === 'sl' ? parentLeg?.lazy_leg_sl_id : parentLeg?.lazy_leg_target_id;
    setLazyLegModal({ open: true, parentLegId, trigger, editingLazyLegId: existingId || null, parentLazyLegId: null, childTrigger: null });
  };

  // Open the lazy-leg popup when Lazy Leg is selected; saved lazy legs render as full rows below.
  const handleReEntryModeSelect = (legId, trigger, value) => {
    updateLeg(legId, trigger === 'sl' ? 're_entry_sl_mode' : 're_entry_target_mode', normalizeReEntryMode(value));
    if (value === 'LAZY_LEG') {
      openLazyLegModal(legId, trigger);
    }
  };

  const handleLazyReEntryModeSelect = (lazyId, trigger, value) => {
    updateLazyLeg(lazyId, trigger === 'sl' ? 're_entry_sl_mode' : 're_entry_target_mode', normalizeReEntryMode(value));
    if (value === 'LAZY_LEG') {
      openChildLazyLegModal(lazyId, trigger);
    }
  };

  const openLazyLegById = (lazyLegId) => {
    setLazyLegModal({ open: true, parentLegId: null, trigger: null, editingLazyLegId: lazyLegId, parentLazyLegId: null, childTrigger: null });
  };

  const openChildLazyLegModal = (parentLazyLegId, trigger) => {
    if (!parentLazyLegId) return;
    const parentLazyLeg = lazyLegs[parentLazyLegId];
    const existingId = trigger === 'sl' ? parentLazyLeg?.child_lazy_leg_sl_id : parentLazyLeg?.child_lazy_leg_target_id;
    setLazyLegModal({ open: true, parentLegId: null, trigger: null, editingLazyLegId: existingId || null, parentLazyLegId, childTrigger: trigger });
  };

  const closeLazyLegModal = () => {
    setLazyLegModal({ open: false, parentLegId: null, trigger: null, editingLazyLegId: null, parentLazyLegId: null, childTrigger: null });
  };

  const saveLazyLeg = (lazyLegConfig) => {
    const { parentLegId, trigger, editingLazyLegId, parentLazyLegId, childTrigger } = lazyLegModal;
    if (totalLazyLegCount >= 10 && !editingLazyLegId) {
      alert('Maximum 10 lazy legs allowed per strategy.');
      return;
    }
    const id = editingLazyLegId || `lazy_${Date.now()}`;
    const finalConfig = normalizeLegForSelectedIndex({ ...lazyLegConfig, id });

    setLazyLegs(prev => {
      const next = { ...prev, [id]: finalConfig };
      if (parentLazyLegId && next[parentLazyLegId]) {
        next[parentLazyLegId] = {
          ...next[parentLazyLegId],
          [childTrigger === 'sl' ? 'child_lazy_leg_sl_id' : 'child_lazy_leg_target_id']: id,
        };
      }
      return next;
    });

    if (parentLegId && trigger) {
      updateLeg(parentLegId, trigger === 'sl' ? 'lazy_leg_sl_id' : 'lazy_leg_target_id', id);
    }

    closeLazyLegModal();
  };

  const removeLazyLeg = (lazyLegId) => {
    setLazyLegs(prev => {
      const next = { ...prev };
      delete next[lazyLegId];
      Object.keys(next).forEach(id => {
        next[id] = {
          ...next[id],
          child_lazy_leg_sl_id: next[id].child_lazy_leg_sl_id === lazyLegId ? null : next[id].child_lazy_leg_sl_id,
          child_lazy_leg_target_id: next[id].child_lazy_leg_target_id === lazyLegId ? null : next[id].child_lazy_leg_target_id,
        };
      });
      return next;
    });
    setLegs(prev => prev.map(leg => ({
      ...leg,
      lazy_leg_sl_id: leg.lazy_leg_sl_id === lazyLegId ? null : leg.lazy_leg_sl_id,
      lazy_leg_target_id: leg.lazy_leg_target_id === lazyLegId ? null : leg.lazy_leg_target_id,
    })));
  };

  const serializeLazyLeg = (ll, registry, seen = new Set()) => {
    if (!ll || seen.has(ll.id)) return null;
    const nextSeen = new Set(seen);
    if (ll.id) nextSeen.add(ll.id);
    const optType = (ll.option_type || 'call').toLowerCase();
    const criteria = ll.strike_criteria || 'strike_type';
    const strikeType = criteria === 'closest_premium'
      ? 'CLOSEST_PREMIUM'
      : criteria === 'premium_range'
        ? 'PREMIUM_RANGE'
        : 'STRIKE_TYPE';
    const serialized = {
      segment: 'OPTIONS',
      position: (ll.position || 'sell').toUpperCase(),
      lots: ll.lot || 1,
      option_type: optType === 'call' ? 'CE' : 'PE',
      expiry: normalizeExpiryForIndex(ll.expiry || defaultOptionExpiry, instrument, 'options', expiryBasis).toUpperCase(),
      strike_interval: normalizeStrikeInterval(ll.strike_interval),
      strike_selection: {
        type: strikeType,
        strike_type: (ll.strike_type || 'atm').toUpperCase(),
        strike_interval: normalizeStrikeInterval(ll.strike_interval),
        premium: ll.premium_value || 0,
        lower: ll.premium_min || 0,
        upper: ll.premium_max || 0,
      },
      lazy_leg_name: ll.name,
      no_reentry_after: ll.no_reentry_after || null,
    };

    if (ll.target_enabled && ll.target_value > 0) {
      serialized.targetProfit = { mode: ll.target_mode, value: ll.target_value };
    }
    if (ll.stop_loss_enabled && ll.stop_loss_value > 0) {
      serialized.stopLoss = { mode: ll.stop_loss_mode, value: ll.stop_loss_value };
    }
    if (ll.sl_buffer_enabled && ll.sl_buffer_value > 0 && ll.sl_buffer_pct > 0) {
      serialized.slWithBuffer = { mode: ll.sl_buffer_mode, value: ll.sl_buffer_value, buffer_pct: ll.sl_buffer_pct };
    }
    if (ll.trail_sl_enabled) {
      serialized.trailSL = { mode: ll.trail_sl_mode, trigger: ll.trail_sl_trigger, move: ll.trail_sl_move };
    }
    if (ll.re_entry_sl_enabled) {
      const childConfig = ll.re_entry_sl_mode === 'LAZY_LEG' && ll.child_lazy_leg_sl_id
        ? serializeLazyLeg(registry[ll.child_lazy_leg_sl_id], registry, nextSeen)
        : null;
      serialized.reEntryOnSL = {
        mode: ll.re_entry_sl_mode,
        count: ll.re_entry_sl_count,
        ...(childConfig ? { lazyLegConfig: childConfig } : {}),
      };
    }
    if (ll.re_entry_target_enabled) {
      const childConfig = ll.re_entry_target_mode === 'LAZY_LEG' && ll.child_lazy_leg_target_id
        ? serializeLazyLeg(registry[ll.child_lazy_leg_target_id], registry, nextSeen)
        : null;
      serialized.reEntryOnTarget = {
        mode: ll.re_entry_target_mode,
        count: ll.re_entry_target_count,
        ...(childConfig ? { lazyLegConfig: childConfig } : {}),
      };
    }
    if (ll.simple_momentum_enabled) {
      serialized.simpleMomentum = { mode: ll.simple_momentum_mode, value: ll.simple_momentum_value };
    }
    return serialized;
  };

  // Does the current leg set qualify for the unified weekly cadence? True when the
  // legs span >1 index (multi-index) AND mix weekly + monthly expiries. Drives the
  // visibility of the Sync Weekly Roll toggle; single-index / non-mixed => hidden.
  const syncRollQualifies = useMemo(() => {
    const el = (legs || []).filter(l => String(l.segment || '').toUpperCase() !== 'MIDCAP100');
    if (!el.length) return false;
    const si = String(instrument || 'NIFTY').toUpperCase();
    const idx = new Set(el.map(l => String(l.index || si).toUpperCase()));
    const multi = idx.size > 1 || (idx.size === 1 && !idx.has(si));
    // Sync Roll is available for ANY multi-index run — mixed weekly+monthly (weekly
    // cadence) OR all-monthly (shared monthly cadence). The backend picks the cadence.
    return multi;
  }, [legs, instrument]);

  // Same shape as the strategyConfig object below — extracted so it can be
  // called at SUBMISSION time (see rulesSubmittedPayloadRef above) instead of
  // being rebuilt from live state on every ResultsPanel render.
  const buildStrategyConfigSnapshot = () => ({
    instrument,
    legs,
    entryDaysBefore,
    exitDaysBefore,
    expiryBasis,
    rolloverCadence,
    yearlyExitMonthsBefore,
    yearlyRollMonths,
    spotAdjustmentEnabled,
    spotAdjustmentDirection,
    spotAdjustmentValue: normalizedSpotAdjustmentValue,
    spotAdjustmentUnits,
    midcapSpotAdjustmentEnabled: midcapSpotAdjEnabled && legs.some(l => l.segment === 'midcap100'),
    midcapSpotAdjustmentDirection: midcapSpotAdjDirection,
    midcapSpotAdjustmentValue: clampSpotAdjustmentValue(midcapSpotAdjValue, midcapSpotAdjUnits),
    midcapSpotAdjustmentUnits: midcapSpotAdjUnits,
  });

  const buildPayload = () => {
    const legsPayload = legs.map(l => {
      const segmentType = (l.segment || '').toLowerCase();
      const leg = {
        segment: segmentType.toUpperCase(),
        position: l.position.toUpperCase(),
        lots: l.lot || 1,
        // Per-leg QUANTITY override (opt-in): direct P&L multiplier for this leg.
        // Emitted only when > 0, so legs without it send a byte-identical payload
        // and the engine falls back to lots × index-lot-size exactly as before.
        ...(Number(l.qty) > 0 ? { qty: Math.trunc(Number(l.qty)) } : {}),
        // Per-leg index (multi-index feature). Defaults to the strategy index,
        // so single-index strategies are unaffected.
        index: String(l.index || instrument).toUpperCase(),
        // This leg's own slippage % — independent of every other leg. Toggle
        // off always sends 0 regardless of whatever value is still typed in.
        slippage_pct: l.slippage_enabled ? Math.max(0, Number(l.slippage_pct) || 0) : 0,
      };

      if (segmentType === 'options') {
        // Normalize 'call'/'put' UI values to 'CE'/'PE' for the backend
        const rawOpt = (l.option_type || '').toLowerCase();
        leg.option_type = rawOpt === 'call' ? 'CE' : rawOpt === 'put' ? 'PE' : l.option_type.toUpperCase();
        // Respect the user's own Strike Gap selection for the leg's OWN index
        // (25/50/100 for MIDCPNIFTY, 50/100 elsewhere), falling back to that
        // index's default only when unset. Previously a CROSS-index leg (e.g.
        // MIDCPNIFTY on a NIFTY strategy) always forced the index default
        // (25) regardless of what the user picked in the Strike Gap dropdown
        // — silently discarding an explicit 50/100 choice.
        const _legIdx = String(l.index || instrument).toUpperCase();
        const _isCrossIndex = _legIdx !== String(instrument).toUpperCase();
        const _legCfg = getIndexConfig(_legIdx) || {};
        const _legMonthlyOnly = !(_legCfg.expiryBases || []).includes('weekly');
        const _legInterval = normalizeStrikeInterval(l.strike_interval, _legIdx);
        // Same-index monthly-only legs stay forced monthly (existing behaviour).
        // A CROSS-index overlay leg respects the user's weekly/monthly choice —
        // the multi-index feature prices weekly date-aware (e.g. MIDCPNIFTY
        // weeklies existed 2022->late-2024; falls back to monthly otherwise).
        // NOTE: pass expiryBasis — without it a YEARLY leg is not in the
        // weekly/monthly option list and would silently fall back to
        // defaultOptionExpiry ('WEEKLY'), trading the wrong contract.
        leg.expiry = _isCrossIndex
          ? String(l.expiry || _legCfg.defaultOptionExpiry || 'monthly').toUpperCase()
          : (_legMonthlyOnly ? 'MONTHLY' : normalizeExpiryForIndex(l.expiry, _legIdx, 'options', expiryBasis).toUpperCase());
        leg.strike_interval = _legInterval;
        // PER-LEG ROLLOVER: this leg's OWN exit T-n (defaults to the global exit
        // offset). The engine reads leg.exit_dte with the same fallback, so
        // omitting per-leg values is safe.
        if (perLegRollover) {
          leg.exit_dte = Number.isFinite(Number(l.exit_dte)) ? Number(l.exit_dte) : exitDaysBefore;
        }
        // Per-leg spot adjustment. Emitted ONLY when the leg opts in, so a
        // strategy that never touches this control sends a byte-identical
        // payload and the backend resolves every leg to the strategy-level
        // values exactly as before.
        if (l.spot_adj_enabled) {
          const _saUnits = l.spot_adj_units === 'points' ? 'points' : 'percent';
          const _saRaw = Number(l.spot_adj_value) || 0;
          leg.spot_adjustment = {
            enabled: true,
            // percent is clamped to the same [0.25, 5] band as the
            // strategy-level knob; points is a free positive number.
            pct: _saUnits === 'percent'
              ? Math.min(5, Math.max(0.25, _saRaw))
              : Math.max(0, _saRaw),
            units: _saUnits,
            direction: ['rise', 'fall', 'both'].includes(l.spot_adj_direction)
              ? l.spot_adj_direction
              : 'rise',
          };
        }
        // Per-contract schedule (yearly legs only): emit only when the leg is
        // yearly, the opt-in toggle is ON, AND there is ≥1 complete row (all
        // three fields filled). Toggle off / absent → backend stays on the
        // existing single-schedule fallback path (existing behaviour untouched).
        if (String(leg.expiry || '').toUpperCase() === 'YEARLY' && l.yearly_schedule_enabled) {
          // "None" = per-contract STRIKE GAP schedule with NO spot adjustment.
          const noAdj = l.yearly_schedule_direction === 'none';
          const sched = (l.yearly_contract_schedule || [])
            // Gap is always required; Spot Adj only when adjustment is on.
            .filter(r => r.year && Number.isFinite(Number(r.year)) && r.gap !== '' && r.gap != null && (noAdj || (r.adj !== '' && r.adj != null)))
            // Each range starts at its "From" December; the sticky backend runs it
            // until the next range's From (i.e. up to the "To" the user typed for
            // contiguous ranges). Earliest range is the baseline for anything before.
            // With adjustment off we still send a positive spot_adj_pct so the row
            // validates, but the leg's spot_adjustment is disabled below so it never fires.
            .map(r => ({ contract: String(Number(r.year)), strike_gap: Number(r.gap), spot_adj_pct: noAdj ? (Number(r.adj) > 0 ? Number(r.adj) : 1) : Number(r.adj), spot_adj_unit: r.adj_unit === 'points' ? 'points' : 'percent' }))
            .sort((a, b) => Number(a.contract) - Number(b.contract));
          if (sched.length) {
            leg.yearly_contract_schedule = sched;
            // The schedule table is the SINGLE source for a yearly leg (the outer
            // Strike Gap + Own Spot Adj are hidden). Derive the leg's base gap +
            // spot-adjustment from the earliest row so no base-vs-schedule conflict
            // is possible, and take the direction from the table's own control.
            const first = sched[0];
            leg.strike_interval = first.strike_gap;
            if (noAdj) {
              // Gap-only schedule: adjustment fully off, gaps still apply per range.
              leg.spot_adjustment = { enabled: false };
            } else {
              const dir = ['rise', 'fall', 'both'].includes(l.yearly_schedule_direction) ? l.yearly_schedule_direction : 'both';
              leg.spot_adjustment = {
                enabled: true,
                pct: first.spot_adj_unit === 'percent'
                  ? Math.min(5, Math.max(0.25, first.spot_adj_pct))
                  : Math.max(0, first.spot_adj_pct),
                units: first.spot_adj_unit,
                direction: dir,
              };
            }
          }
        }
        leg.strike_selection = {
          type: l.strike_criteria.toUpperCase(),
          strike_type: l.strike_type.toUpperCase(),
          strike_interval: _legInterval,
          premium: l.premium_value,
          lower: l.premium_min,
          upper: l.premium_max,
        };
        if (l.strike_criteria === 'pct_of_atm') {
          leg.strike_selection.value = Number(l.pct_value) || 0;
          // Compute +/- direction from ITM/OTM moneyness + option type.
          // CE OTM = above spot (+), CE ITM = below spot (-)
          // PE OTM = below spot (-), PE ITM = above spot (+)
          const _isCE = ['call', 'ce'].includes((l.option_type || '').toLowerCase());
          const _moneyness = l.pct_atm_moneyness
            || (l.pct_direction === '+' ? (_isCE ? 'OTM' : 'ITM') : (_isCE ? 'ITM' : 'OTM'));
          leg.strike_selection.direction = _isCE
            ? (_moneyness === 'OTM' ? '+' : '-')
            : (_moneyness === 'ITM' ? '+' : '-');
        }
        if (String(l.strike_criteria || '').startsWith('time_value')) {
          // OTM = intrinsic 0, ITM = intrinsic > 0, ATM = whole chain.
          leg.strike_selection.moneyness = (l.tv_moneyness || 'ATM').toUpperCase();
          // Range cap: |strike/entry_spot - 1| in %. Blank/0 = uncapped.
          leg.strike_selection.tv_range_pct = Number(l.tv_range_pct) || 0;
          leg.strike_selection.tv_units = l.tv_units || 'points';
        }
        if (l.strike_criteria === 'atm_straddle_prem_pct') {
          leg.strike_selection.value = Number(l.atm_straddle_prem_pct) || 0;
        }
        if (l.strike_criteria === 'rel_leg') {
          // Wing strike = leg #ref_leg's resolved strike ± offset*gap (+ CALL /
          // − PUT, applied engine-side). ref_leg is the 1-based leg number and
          // MUST reference an earlier leg (validated before run).
          leg.strike_selection.ref_leg = Number(l.ref_leg) || 1;
          leg.strike_selection.offset = Number(l.offset) || 0;
        }
        if (l.strike_criteria === 'rel_leg_premium') {
          // Premium target = leg #ref_leg's actual entry fill, rescaled by the
          // expiry-count ratio between the two legs and by their lot sizes.
          // ref_leg is 1-based and MUST reference an earlier leg.
          leg.strike_selection.ref_leg = Number(l.ref_leg) || 1;
        }
        if (l.strike_criteria === 'straddle_width') {
          leg.straddle_multiplier = l.straddle_multiplier ?? 0.5;
          leg.straddle_direction = l.straddle_direction ?? '+';
          // Engine (Rust + its Python mirror) reads these from inside
          // strike_selection, not the flat leg fields above — mirror both so
          // the configured width/direction actually reaches the engine.
          leg.strike_selection.straddle_multiplier = leg.straddle_multiplier;
          leg.strike_selection.straddle_direction = leg.straddle_direction;
        }
        if (rolloverToggle || noRollover) {
          leg.rollover_strike_mode = l.rollover_strike_mode || 'fresh';
        }
      }
      if (l.individual_filter && (l.filter_segments || []).length) {
        leg.filter_segments = l.filter_segments;
        // Propagate the flag so the Rules sheet (buildRulesSheet / rules_sheet.py,
        // which gate on individual_filter) actually shows the per-leg filter.
        // The engine keys on filter_segments, so this is display-only.
        leg.individual_filter = true;
      }
      if (segmentType === 'futures') {
        leg.expiry = (l.expiry || 'monthly').toLowerCase();
        Object.assign(leg, {
          fut_exit_mode: l.fut_exit_mode || 'ON_EXPIRY',
          fut_n_days: l.fut_n_days ?? 5,
          fut_with_filter: l.fut_with_filter !== false,
          fut_sl_override: l.fut_sl_override !== false,
          fut_target_override: l.fut_target_override !== false,
          fut_with_spot_adj: l.fut_with_spot_adj !== false,
        });
      }
      if (segmentType === 'midcap100') {
        // Cross-index overlay leg — routed OUT of the engine `legs` array below.
        leg.midcap_mode = (l.midcap_mode || 'hypothetical').toLowerCase();
        leg.cost_pct_per_month = Number(l.cost_pct_per_month) || 0;
        leg.symbol = 'NIFTYMIDCAP100';
      }

      // Target Profit - only send if enabled AND value is set
      if (l.target_enabled && l.target_value != null && l.target_value > 0) {
        leg.targetProfit = {
          mode: l.target_mode,
          value: l.target_value,
        };
      }

      // Stop Loss - only send if enabled AND value is set
      if (l.stop_loss_enabled && l.stop_loss_value != null && l.stop_loss_value > 0) {
        leg.stopLoss = {
          mode: l.stop_loss_mode,
          value: l.stop_loss_value,
        };
      }

      // SL with Buffer - only send if enabled AND both value and buffer% are set
      if (l.sl_buffer_enabled && l.sl_buffer_value != null && l.sl_buffer_value > 0 && l.sl_buffer_pct != null && l.sl_buffer_pct > 0) {
        leg.slWithBuffer = {
          mode: l.sl_buffer_mode,
          value: l.sl_buffer_value,
          buffer_pct: l.sl_buffer_pct,
        };
      }

      // Trail SL - only send if enabled
      if (l.trail_sl_enabled) {
        leg.trailSL = {
          mode: l.trail_sl_mode,
          trigger: l.trail_sl_trigger,
          move: l.trail_sl_move,
        };
      }

      // Re-entry on Target - only send if enabled
      if (l.re_entry_target_enabled) {
        leg.reEntryOnTarget = {
          mode: l.re_entry_target_mode,
          count: l.re_entry_target_count,
        };
        if (l.re_entry_target_mode === 'LAZY_LEG' && l.lazy_leg_target_id) {
          const lazyConfig = serializeLazyLeg(lazyLegs[l.lazy_leg_target_id], lazyLegs);
          if (lazyConfig) {
            leg.reEntryOnTarget.lazyLegConfig = lazyConfig;
          }
        }
      }

      // Re-entry on SL - only send if enabled
      if (l.re_entry_sl_enabled) {
        leg.reEntryOnSL = {
          mode: l.re_entry_sl_mode,
          count: l.re_entry_sl_count,
        };
        if (l.re_entry_sl_mode === 'LAZY_LEG' && l.lazy_leg_sl_id) {
          const lazyConfig = serializeLazyLeg(lazyLegs[l.lazy_leg_sl_id], lazyLegs);
          if (lazyConfig) {
            leg.reEntryOnSL.lazyLegConfig = lazyConfig;
          }
        }
      }

      // Simple Momentum - only send if enabled
      if (l.simple_momentum_enabled) {
        leg.simpleMomentum = {
          mode: l.simple_momentum_mode,
          value: l.simple_momentum_value,
        };
      }

      return leg;
    });

    // Split Midcap overlay legs out of the engine legs — the NIFTY engine
    // never sees them; they are priced afterward by the /midcap-overlay endpoint.
    const engineLegs = legsPayload.filter(l => String(l.segment || '').toUpperCase() !== 'MIDCAP100');
    const midcapLegs = legsPayload.filter(l => String(l.segment || '').toUpperCase() === 'MIDCAP100');

    const allFuturesNextMonthly = (
      engineLegs.length > 0 &&
      engineLegs.every(l => String(l.segment || '').toUpperCase() === 'FUTURES') &&
      engineLegs.some(l => String(l.expiry || '').toLowerCase() === 'next_monthly')
    );
    // SAME-INDEX MIXED EXPIRY: the cadence is DERIVED from the legs (finest
    // wins), not taken from the dropdown. A weekly leg beside a monthly leg
    // forces a WEEKLY cadence; the monthly leg is then pinned to its own
    // contract by the engine. Only fires when the basis and the legs actually
    // disagree AND a weekly leg is present, so every strategy whose legs match
    // its basis keeps sending exactly the same expiry_type as before.
    const _derivedWeeklyCadence = (
      expiryBasis === 'monthly' &&
      indexConfig.expiryBases.includes('weekly') &&
      engineLegs.some(l =>
        String(l.segment || '').toUpperCase() === 'OPTIONS' &&
        ['WEEKLY', 'NEXT_WEEKLY'].includes(String(l.expiry || '').toUpperCase()))
    );
    const effectiveExpiryType = allFuturesNextMonthly
      ? 'NEXT_MONTHLY'
      : (_derivedWeeklyCadence ? 'WEEKLY' : expiryBasis.toUpperCase());

    // Multi-index feature trigger (opt-in, conservative): ONLY when legs span
    // more than one index, or a leg's index differs from the strategy index.
    // Single-index strategies (even with mixed weekly/monthly legs) are NEVER
    // rerouted, so existing behaviour is byte-identical.
    const _stratIdx = String(instrument || 'NIFTY').toUpperCase();
    const _legIndices = new Set(engineLegs.map(l => String(l.index || _stratIdx).toUpperCase()));
    const multiIndexMode = engineLegs.length > 0 && (
      _legIndices.size > 1 || (_legIndices.size === 1 && !_legIndices.has(_stratIdx))
    );
    // Unified cadence: ANY multi-index run with rollover on. If any leg is weekly the
    // backend uses the weekly cadence; if all legs are monthly it uses the shared
    // monthly cadence. Every leg re-enters together each cycle. Scoped to multi-index
    // — single-index runs are unaffected.
    const syncWeeklyRoll = multiIndexMode
      && rolloverToggle
      && syncWeeklyRollEnabled;

    return {
      index: instrument,
      // Opt-in flag routing this run to the isolated multi-index feature on the
      // backend. Absent/false for every existing single-index strategy.
      multi_index_mode: multiIndexMode,
      // Routes multi-index mixed weekly+monthly rollover runs to the unified
      // weekly-cadence path (monthly legs roll on their own monthly expiry).
      sync_weekly_roll: syncWeeklyRoll,
      underlying,
      strategy_type: strategyType,
      expiry_window: expiryBasis === 'weekly' ? 'weekly_expiry' : 'monthly_expiry',
      // YEARLY needs rollover_toggle TRUE — that is the only thing that makes the
      // engine pin the December contract (simulate.rs rollover gate). Omitting
      // 'yearly' here silently stripped it from the payload even though the UI
      // toggle was on, so the run fell back to trading the cadence expiries.
      rollover_toggle: (rolloverToggle || noRollover) && ['weekly', 'monthly', 'yearly'].includes(expiryBasis),
      // min-DTE is REJECTED by the engine under yearly (it would advance the
      // contract to the next cadence element). T-n is the yearly roll-early knob.
      rollover_min_days_to_expiry: (rolloverToggle && expiryBasis !== 'yearly') ? rolloverMinDaysToExpiry : 0,
      no_rollover: noRollover && ['weekly', 'monthly'].includes(expiryBasis),
      no_rollover_min_days: (noRollover && expiryBasis !== 'yearly') ? noRolloverMinDays : 0,
      // PER-LEG ROLLOVER: each leg rolls on its own expiry + own exit T-n
      // (leg.exit_dte, stamped below). Union boundaries + carry are the engine's
      // resolve_per_leg_core. Additive — false ⇒ byte-identical payload.
      per_leg_rollover: perLegRollover,
      entry_dte: entryDaysBefore,
      exit_dte: exitDaysBefore,
      square_off_mode: squareOffMode,
      spot_adjustment_enabled: spotAdjustmentEnabled,
      spot_adjustment_direction: spotAdjustmentDirection,
      spot_adjustment_pct: normalizedSpotAdjustmentValue,
      spot_adjustment_units: spotAdjustmentUnits,
      spot_adjustment_use_entry_close: true,
      buffer_strike_enabled: Boolean(bufferStrikeEnabled),
      buffer_strike_value: Number(bufferStrikeValue) || 0,
      buffer_strike_unit: String(bufferStrikeUnit || 'percent'),
      // Strike-shift fallback steps: when the requested strike has no contract
      // (or zero turnover/stale close), shift this many intervals further from
      // ATM in the originally-requested direction.
      strike_shift_max_steps: Number(strikeShiftMaxSteps) || 0,
      buffer_strike_apply_to: String(bufferStrikeApplyTo || 'both'),
      buffer_position_above: Boolean(bufferPositionAbove),
      buffer_position_below: Boolean(bufferPositionBelow),
      charges_enabled: chargesEnabled,
      legs: engineLegs,
      // Midcap cross-index overlay (additive; ignored by the engine).
      midcap_legs: midcapLegs.length ? midcapLegs : null,
      midcap_spot_adjustment: (midcapLegs.length && midcapSpotAdjEnabled)
        ? {
            enabled: true,
            direction: midcapSpotAdjDirection,
            pct: clampSpotAdjustmentValue(midcapSpotAdjValue, midcapSpotAdjUnits),
            units: midcapSpotAdjUnits,
          }
        : null,
      // MIDCPNIFTY per-index spot adjustment. Engine reads this only when a
      // MIDCPNIFTY leg is present; absent/null keeps the existing paths intact.
      midcpnifty_spot_adjustment: (hasMidcpniftyLeg && midcpSpotAdjEnabled)
        ? {
            enabled: true,
            symbol: 'MIDCPNIFTY',
            direction: midcpSpotAdjDirection,
            pct: clampSpotAdjustmentValue(midcpSpotAdjValue, midcpSpotAdjUnits),
            units: midcpSpotAdjUnits,
          }
        : null,
      // Combine mode (only honored by the engine when BOTH NIFTY & Midcap spot
      // adjustment are active). Default 'earliest' = unchanged behaviour.
      spot_adjustment_combine_mode:
        (spotAdjustmentEnabled && midcapLegs.length && midcapSpotAdjEnabled)
          ? spotAdjCombineMode
          : 'earliest',
      spot_adjustment_confirm_days:
        (spotAdjustmentEnabled && midcapLegs.length && midcapSpotAdjEnabled && spotAdjCombineMode === 'confirm')
          ? Math.max(0, Number(spotAdjConfirmDays) || 0)
          : 0,
      // Overall SL/TGT - send flat structure with correct field names expected by backend
      overall_sl_type: overallSLEnabled ? overallSLType : null,
      overall_sl_value: overallSLEnabled ? (overallSLValue === '' ? 0 : overallSLValue) : null,
      overall_target_type: overallTgtEnabled ? overallTgtType : null,
      overall_target_value: overallTgtEnabled ? (overallTgtValue === '' ? 0 : overallTgtValue) : null,
      date_from: getApiStartDate(startDate),
      date_to: getApiEndDate(endDate),
      expiry_type: effectiveExpiryType,
      // YEARLY only (ignored by every other basis): the roll cadence and the
      // T-n months at which the December contract rolls to the next December.
      ...(effectiveExpiryType === 'YEARLY' ? {
        rollover_cadence: rolloverCadence,
        yearly_exit_months_before: Math.max(0, Math.min(11, Number(yearlyExitMonthsBefore) || 0)),
        // Long-dated months to roll through. December is always included (the
        // anchor); sorting keeps the payload stable so the cache key doesn't
        // churn on checkbox order. Absent/December-only == existing behaviour.
        yearly_roll_months: Array.from(new Set(['12', ...yearlyRollMonths])).sort(),
      } : {}),
      filter: strFilter.enabled ? strFilter.configId : null,
      filter_config: strFilter.enabled ? strFilter.configId : null,
      // configId is 'custom' for an uploaded CSV, which names nothing. filterName is
      // the CSV filename (or the preset's label) — the only real identity the filter
      // has — and the Rules sheet had no way to print it because it was never sent.
      filter_label: strFilter.enabled ? (strFilter.filterName || strFilter.configLabel || null) : null,
      filter_segments: strFilter.enabled && strFilter.segments ? strFilter.segments : [],
      super_trend_config: 'None',
      // Filter ON  → only 'fixed' is offered in the UI (DTE / Min-Days options removed).
      // Filter OFF → still 'dte': that is the engine's default entry schedule for every
      //              non-filter strategy and must not change. Backend keeps all 3 branches.
      filter_entry_mode: strFilter.enabled ? (strFilter.entryMode || 'fixed') : 'dte',
      fixed_late_entry: strFilter.enabled && strFilter.entryMode === 'fixed' ? Boolean(strFilter.lateEntry) : false,
      min_days_to_entry: strFilter.enabled && strFilter.entryMode === 'min_days'
        ? (parseInt(strFilter.minDaysToEntry) || 3)
        : 0,
      str_filter: strFilter.enabled
        ? { enabled: true, config: strFilter.configId }
        : { enabled: false },
    };
  };

  // Deep-clone and strip all non-serializable values from a payload object.
  // Safe against circular refs, DOM nodes, React fibers, functions, and symbols.
  const sanitizePayload = (obj, _seen = new WeakSet()) => {
    // Primitives and null pass through directly
    if (obj === null || obj === undefined) return obj;
    if (typeof obj === 'boolean') return obj;
    if (typeof obj === 'number') return Number.isFinite(obj) ? obj : null;
    if (typeof obj === 'string') return obj;
    // Skip non-serializable types entirely
    if (typeof obj === 'function') return undefined;
    if (typeof obj === 'symbol') return undefined;
    // Skip DOM nodes and React elements — these cause the circular ref error
    if (typeof window !== 'undefined') {
      if (obj instanceof Element) return undefined;
      if (obj instanceof Node) return undefined;
      if (obj instanceof Event) return undefined;
    }
    // Skip React synthetic events and fiber nodes
    if (obj && typeof obj === 'object' && obj.$$typeof) return undefined;
    // Handle Date objects
    if (obj instanceof Date) return obj.toISOString().split('T')[0];
    // Guard against circular references
    if (_seen.has(obj)) return undefined;
    _seen.add(obj);
    // Handle arrays
    if (Array.isArray(obj)) {
      return obj
        .map(item => sanitizePayload(item, _seen))
        .filter(item => item !== undefined);
    }
    // Handle plain objects — iterate key by key, never trust whole-object stringify
    const out = {};
    for (const key of Object.keys(obj)) {
      // Skip React internal keys
      if (key.startsWith('__react') || key.startsWith('_reactFiber') || key === '__reactInternals') {
        continue;
      }
      const sanitized = sanitizePayload(obj[key], _seen);
      if (sanitized !== undefined) {
        out[key] = sanitized;
      }
    }
    return out;
  };

  const runBacktest = useCallback(async () => {
    if (legs.length === 0) { setError('Please add at least one leg'); return; }
    if (loading) return;  // guard: ignore clicks while already running
    if (!isValidDate(startDate) || !isValidDate(endDate)) {
      const msg = 'Invalid date format. Use DD/MM/YYYY';
      setValidationError(msg);
      setError(msg);
      return;
    }
    setValidationError(null);
    
    // DTE validation: current weekly/monthly legs anchor entry and exit to the
    // same expiry, so equal or inverted DTE values produce no valid hold.
    const hasCurrentExpiryOptionLegs = legs
      .filter(l => l.segment !== 'futures')
      .some(l => l.expiry === 'weekly' || l.expiry === 'monthly');
    const hasCurrentExpiryFuturesLegs = legs
      .filter(l => l.segment === 'futures')
      .some(l => l.expiry === 'monthly');
    const hasCurrentExpiryLegs = hasCurrentExpiryOptionLegs || hasCurrentExpiryFuturesLegs;

    // Under Per-Leg Rollover the strategy-level entry/exit offsets are ignored
    // (each leg carries its own exit T-n; entry is the previous roll), so this
    // Entry>Exit check does not apply.
    if (hasCurrentExpiryLegs && !rolloverToggle && !noRollover && !perLegRollover) {
      const basisLabel = hasCurrentExpiryOptionLegs
        ? (expiryBasis === 'weekly' ? 'Weekly' : 'Monthly')
        : 'Monthly Futures';

      if (entryDaysBefore === exitDaysBefore) {
        setValidationError(null);
        showTimedError(
          `Invalid DTE: Entry Days (${entryDaysBefore}) equals Exit Days (${exitDaysBefore}) ` +
          `for ${basisLabel} leg(s). Both dates resolve to the same day, so no trades will execute. ` +
          `Set Entry Days > Exit Days (e.g., Entry=2, Exit=0).`
        );
        setLoading(false);
        return;
      }
      if (entryDaysBefore < exitDaysBefore) {
        setValidationError(null);
        showTimedError(
          `Invalid DTE: Entry Days (${entryDaysBefore}) is less than Exit Days (${exitDaysBefore}) ` +
          `for ${basisLabel} leg(s). Lower DTE means a later calendar date, so entry would fall after exit. ` +
          `Set Entry Days > Exit Days (e.g., Entry=2, Exit=0).`
        );
        setLoading(false);
        return;
      }
    }

    if (!validateExpiry()) return;

    // Relative-to-leg (Iron Condor wing) sanity: a rel_leg leg must reference an
    // EARLIER leg (1-based position < its own). A dangling reference — e.g. after
    // deleting the parent leg — would otherwise be silently dropped by the engine
    // (0 trades) with no explanation. Fail loudly instead.
    // rel_leg_premium shares the earlier-leg rule for the same reason, so it is
    // validated by the same pass.
    const relLegErrors = legs.reduce((acc, leg, idx) => {
      if (leg.strike_criteria !== 'rel_leg' && leg.strike_criteria !== 'rel_leg_premium') return acc;
      const ref = Number(leg.ref_leg) || 0;
      if (ref < 1 || ref > idx) acc.push(`Leg ${idx + 1}`);
      return acc;
    }, []);
    if (relLegErrors.length > 0) {
      showValidationError(
        `Relative-to-Leg strike on ${relLegErrors.join(', ')} points to a missing or later leg. ` +
        `Set it to reference an earlier leg (the short leg it protects).`,
        6000
      );
      return; // Hard block
    }

    const trailSLWarnings = legs.reduce((acc, leg, idx) => {
      if (!leg.trail_sl_enabled) return acc;
      const triggerVal = Number(leg.trail_sl_trigger);
      const moveVal = Number(leg.trail_sl_move);
      if (!Number.isFinite(triggerVal) || !Number.isFinite(moveVal)) return acc;
      if (triggerVal > 0 && moveVal > 0 && triggerVal < moveVal) {
        acc.push(`Leg ${idx + 1}`);
      }
      return acc;
    }, []);
    if (trailSLWarnings.length > 0) {
      const warningLegs = trailSLWarnings.join(', ');
      console.warn(`Trail SL X < Y on ${warningLegs}: results may differ from live.`);
      setTrailSLWarning(`Trail SL: X < Y on ${warningLegs} — backtest may not match live trading.`);
    } else {
      setTrailSLWarning(null);
    }

    const tslWithoutSLLegs = legs.reduce((acc, leg, idx) => {
      if (!leg.trail_sl_enabled) return acc;
      const hasStopLoss = leg.stop_loss_enabled && Number(leg.stop_loss_value) > 0;
      if (!hasStopLoss) acc.push(`Leg ${idx + 1}`);
      return acc;
    }, []);

    if (tslWithoutSLLegs.length > 0) {
      setError(null);
      showValidationError(
        `Trail SL needs Stop Loss. Turn on Stop Loss and set a value on ${tslWithoutSLLegs.join(', ')} to run backtest.`,
        5000
      );
      return; // Hard block — do not proceed to buildPayload() or API call
    }

    const payload = buildPayload();
    const sanitized = sanitizePayload(payload);
    // LAN remote-worker routing (see remote-worker/) — null = run locally, unchanged.
    sanitized.node_id = selectedNodeId || null;
    // Snapshot the config that IS being submitted, for the Rules sheet/filename
    // once results land — not the live state at download time, which may have
    // been edited since (see the ref's own comment for why this matters).
    rulesSubmittedPayloadRef.current = sanitized;
    setResultsSnapshotConfig(buildStrategyConfigSnapshot());
    stopJobPolling();
    setLoading(true);
    setError(null);
    setRawResults(null);
    setDisplayResults(null);
    setResults(null);
    console.log('[runBacktest] payload keys:', Object.keys(sanitized));
    console.log('[runBacktest] str_filter:', sanitized?.str_filter);
    console.log('[runBacktest] filter_segments:', sanitized?.filter_segments);
    console.log('[runBacktest] legs[0] keys:', sanitized?.legs?.[0] ? Object.keys(sanitized.legs[0]) : 'none');
    try {
      JSON.stringify(sanitized);
      console.log('[runBacktest] sanitized payload stringify SUCCESS');
    } catch (circErr) {
      console.error('[runBacktest] sanitized payload still has circular ref:', circErr);
      for (const k of Object.keys(sanitized ?? {})) {
        try { JSON.stringify(sanitized[k]); }
        catch { console.error('[runBacktest] circular ref in key:', k); }
      }
    }
    try {
      const res = await fetch('/api/algotest/jobs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(sanitized),
      });

      if (res.status === 504) {
        setLoading(false);
        setJobStatusLabel('');
        setError('Server is busy. Please wait 30 seconds and try again.');
        return;
      }

      if (!res.ok) {
        const errorPayload = await res.json().catch(() => null);
        throw new Error(errorPayload?.message || errorPayload?.detail || `Server error (${res.status})`);
      }

      const data = await res.json();
      if (!data?.job_id) {
        throw new Error('No job ID returned. Please try again.');
      }
      latestJobIdRef.current = data.job_id;
      setJobId(data.job_id);
      const queueDepth = Number(data.queue_depth);
      setJobStatusLabel(Number.isFinite(queueDepth) && queueDepth > 0 ? `Queued (${queueDepth} ahead)…` : 'Queued…');
      pollJobStatus(data.job_id);
    } catch (err) {
      setLoading(false);
      setJobStatusLabel('');
      setError(err.message || 'Backtest queue failed');
    }
  }, [legs, loading, startDate, endDate, entryDaysBefore, exitDaysBefore, expiryBasis, rolloverToggle, validateExpiry, buildPayload, pollJobStatus, stopJobPolling, selectedNodeId]);

  const strykNav = [
    { id: 'build', label: 'Build', Icon: LayoutGrid, onClick: () => { setActiveView('build'); window.scrollTo({ top: 0, behavior: 'smooth' }); } },
    { id: 'results', label: 'Results', Icon: BarChart3, disabled: !displayResults, onClick: () => { if (displayResults) { setActiveView('results'); resultsRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }); } } },
    { id: 'optimize', label: 'Optimize', Icon: SlidersHorizontal, onClick: () => setOptimPanelOpen(true) },
  ];

  return (
    <div className="min-h-screen bg-base flex">
      {/* STRYK Sidebar */}
      <aside className="stryk-sidebar shrink-0 sticky top-0 h-screen flex flex-col gap-1 px-3 py-4" style={{ width: 220, zIndex: 40 }}>
        <div className="flex items-center gap-2.5 px-2 pb-4">
          <div className="logo-mark" style={{ width: 32, height: 32 }}>
            <svg viewBox="0 0 48 48" aria-label="STRYK">
              <defs>
                <linearGradient id="strykLogoSide" x1="0" y1="0" x2="1" y2="1">
                  <stop offset="0%" stopColor="#5a9ee0" />
                  <stop offset="50%" stopColor="#387ed1" />
                  <stop offset="100%" stopColor="#2ecc71" />
                </linearGradient>
              </defs>
              <path d="M24 4 L40 13 L40 35 L24 44 L8 35 L8 13 Z" fill="none" stroke="url(#strykLogoSide)" strokeWidth="2" strokeLinejoin="round" opacity="0.45" />
              <path d="M27 9 L15 26 L22.5 26 L20 39 L33 21 L25 21 Z" fill="url(#strykLogoSide)" stroke="url(#strykLogoSide)" strokeWidth="1.4" strokeLinejoin="round" />
            </svg>
          </div>
          <div className="flex flex-col leading-none">
            <span className="app-name" style={{ fontSize: '1.05rem' }}>STRYK</span>
          </div>
        </div>
        {strykNav.map(({ id, label, Icon, onClick, disabled }) => (
          <button
            key={id}
            type="button"
            onClick={onClick}
            disabled={disabled}
            className={`stryk-nav ${activeView === id ? 'active' : ''}`}
            style={disabled ? { opacity: 0.4, cursor: 'not-allowed' } : undefined}
          >
            <Icon className="nav-ic" size={16} />
            {label}
          </button>
        ))}
        <div className="mt-auto px-2" style={{ fontSize: '0.55rem', color: 'var(--text-muted)', lineHeight: 1.7, letterSpacing: '0.04em' }}>
          STRYK Terminal<br />Midnight Teal
        </div>
      </aside>

      {/* Main column */}
      <div className="flex-1 min-w-0">
      {/* Header */}
      <header className="app-header px-6 py-3">
        <div className="max-w-screen-2xl mx-auto flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <span style={{ fontFamily: 'Outfit, sans-serif', fontSize: '0.92rem', fontWeight: 800, letterSpacing: '-0.01em', color: 'var(--text-primary)', textTransform: 'capitalize' }}>{activeView}</span>
            <span className="text-muted text-xs">/</span>
            <span style={{ fontFamily: 'Outfit, sans-serif', fontSize: '0.72rem', color: 'var(--text-secondary)' }}>Options Backtester</span>
          </div>
          <div className="flex items-center gap-4 text-sm">
            <div className="flex items-center gap-2">
              <span style={{ fontFamily: 'IBM Plex Mono, monospace', fontSize: '0.7rem', fontWeight: 700, color: 'var(--accent)', letterSpacing: '-0.01em' }}>{instrument}</span>
              <span className="text-muted text-xs">•</span>
              <span style={{ fontFamily: 'IBM Plex Mono, monospace', fontSize: '0.68rem', color: 'var(--text-secondary)' }}>{legs.length} leg{legs.length !== 1 ? 's' : ''}</span>
            </div>
            {/* Core: LAN remote-worker picker — null = run locally (unchanged default) */}
            <Dropdown
              label={<><Cpu size={11} style={{ marginRight: 4, verticalAlign: -1 }} />Core</>}
              value={selectedNodeId}
              onChange={(v) => {
                setSelectedNodeId(v);
                const node = lanNodes.find((n) => n.node_id === v);
                setSelectedNodeCores(node ? node.cpu_count : null);
              }}
              options={[
                { value: null, label: 'Local (this box)' },
                ...lanNodes.map((n) => ({
                  value: n.node_id,
                  disabled: !!n.stale,
                  label: (n.is_you ? `${n.hostname || n.ip} (you)` : (n.hostname || n.ip)) + (n.stale ? ' — update needed' : ''),
                  sublabel: n.stale ? 'outdated' : `${n.cpu_count} cores`,
                })),
              ]}
            />
            {selectedNode && (
              <Dropdown
                label="Cores"
                value={selectedNodeCores}
                onChange={setSelectedNodeCores}
                minWidth={90}
                options={Array.from({ length: selectedNode.cpu_count || 1 }, (_, i) => i + 1).map((n) => ({
                  value: n, label: String(n),
                }))}
              />
            )}
            {/* Theme Toggle */}
            <button
              type="button"
              onClick={() => setIsDark(d => !d)}
              title={isDark ? 'Switch to Light Mode' : 'Switch to Dark Mode'}
              className="flex items-center gap-2 px-3 py-1.5 rounded-md transition-all"
              style={{
                fontFamily: 'Outfit, sans-serif', fontSize: '0.6rem', fontWeight: 700,
                letterSpacing: '0.08em', textTransform: 'uppercase',
                color: 'var(--text-secondary)', background: 'var(--bg-elevated)',
                border: '1px solid var(--border-default)', cursor: 'pointer',
              }}
              onMouseEnter={e => { e.currentTarget.style.borderColor = 'var(--accent)'; e.currentTarget.style.color = 'var(--accent)'; }}
              onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--border-default)'; e.currentTarget.style.color = 'var(--text-secondary)'; }}
            >
              {isDark ? <Sun size={12} /> : <Moon size={12} />}
              {isDark ? 'Light' : 'Dark'}
            </button>
            <button className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-semibold transition-all"
              style={{ fontFamily: 'Outfit, sans-serif', color: 'var(--text-secondary)', background: 'transparent', border: '1px solid var(--border-default)', letterSpacing: '0.06em' }}
              onMouseEnter={e => { e.currentTarget.style.borderColor = 'var(--accent)'; e.currentTarget.style.color = 'var(--accent)'; }}
              onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--border-default)'; e.currentTarget.style.color = 'var(--text-secondary)'; }}>
              <Save size={12} />
              SAVE
            </button>
          </div>
        </div>
      </header>

      <div className="max-w-screen-2xl mx-auto px-6 py-4">

        <div className="grid grid-cols-12 gap-4">

          {/* LEFT COLUMN - Configuration */}
          <div className="col-span-5 space-y-4">
            {/* Configuration Card */}
            <div className="bg-surface rounded-lg border border-default shadow-sm">
                <div className="px-4 py-3 border-b border-subtle">
                  <h3 className="section-heading">Configuration</h3>
            </div>
            <div className="p-4 space-y-4">
                {/* Intraday backtest mode removed — handled by separate software.
                    backtestMode stays 'eod' always; EOD-only guards below are kept. */}

                {/* Index selector removed — the Leg Builder index tabs (right) are the
                    sole index control. The strategy's base index follows the first
                    real leg's index (see the derive-instrument effect). */}

                {/* Underlying */}
                <div>
                  <label className="field-label">Underlying</label>
                  <SegBtn
                    options={[
                      { value: 'cash', label: 'Cash', disabled: hasFuturesLeg },
                      { value: 'futures', label: 'Futures' },
                    ]}
                    value={underlying}
                    onChange={handleUnderlyingChange}
                  />
                </div>

                {/* Expiry Basis — EOD only */}
                {backtestMode === 'eod' && (
                  <div>
                    <label className="field-label">Expires on</label>
                    <select
                      value={expiryBasis}
                      onChange={e => setExpiryBasis(e.target.value)}
                      className="w-full h-9 px-3 border border-default rounded text-sm bg-surface"
                    >
                      {expiryBasisOptions.map(opt => (
                        <option key={opt.value} value={opt.value}>{opt.label}</option>
                      ))}
                    </select>
                  </div>
                )}

                {/* Yearly (December) — contract and roll cadence are two
                    different calendars, so both need their own control. */}
                {backtestMode === 'eod' && expiryBasis === 'yearly' && (
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className="field-label">Roll every</label>
                      <select
                        value={rolloverCadence}
                        onChange={e => setRolloverCadence(e.target.value)}
                        className="w-full h-9 px-3 border border-default rounded text-sm bg-surface"
                      >
                        <option value="monthly">Month (MOM)</option>
                        <option value="weekly">Week (WOW)</option>
                      </select>
                      <p className="text-[11px] text-secondary mt-1">
                        Re-books the position on this cadence while holding the long-dated contract.
                      </p>
                    </div>
                    <div>
                      <label className="field-label">Roll to next December</label>
                      <select
                        value={yearlyExitMonthsBefore}
                        onChange={e => setYearlyExitMonthsBefore(+e.target.value)}
                        className="w-full h-9 px-3 border border-default rounded text-sm bg-surface"
                      >
                        <option value={0}>T-0 (hold to expiry)</option>
                        {[1, 2, 3, 4, 5, 6].map(n => (
                          <option key={n} value={n}>{`T-${n} (${n} month${n === 1 ? '' : 's'} before)`}</option>
                        ))}
                      </select>
                      <p className="text-[11px] text-secondary mt-1">
                        {yearlyExitMonthsBefore === 0
                          ? 'Holds 26-Dec-2019 to expiry, then rolls to 31-Dec-2020.'
                          : `Exits ${yearlyExitMonthsBefore} month${yearlyExitMonthsBefore === 1 ? '' : 's'} early and re-enters the next long-dated contract at a fresh strike.`}
                      </p>
                    </div>
                  </div>
                )}

                {/* Roll-through months — YEARLY + rollover only. December is the
                    anchor and can't be removed; add Mar/Jun/Sep to alternate. */}
                {backtestMode === 'eod' && expiryBasis === 'yearly' && rolloverToggle && (
                  <div>
                    <label className="field-label">Roll through</label>
                    <div className="flex flex-wrap gap-2 mt-1">
                      {[['03', 'March'], ['06', 'June'], ['09', 'September'], ['12', 'December']].map(([m, label]) => {
                        const isDec = m === '12';
                        const on = isDec || yearlyRollMonths.includes(m);
                        return (
                          <button
                            key={m}
                            type="button"
                            disabled={isDec}
                            onClick={() => setYearlyRollMonths(prev =>
                              prev.includes(m) ? prev.filter(x => x !== m) : [...prev, m])}
                            className={`px-3 py-1 rounded-lg text-xs font-medium border transition-colors ${
                              on ? 'bg-accent text-white border-accent' : 'border-default text-secondary hover:bg-hover'
                            } ${isDec ? 'opacity-70 cursor-default' : ''}`}
                          >
                            {label}{isDec ? ' (anchor)' : ''}
                          </button>
                        );
                      })}
                    </div>
                    <p className="text-[11px] text-secondary mt-1">
                      {Array.from(new Set(['12', ...yearlyRollMonths])).length === 1
                        ? 'December only — rolls once a year (in November at T-1). Current behaviour.'
                        : `Alternates through ${Array.from(new Set(['12', ...yearlyRollMonths])).sort().map(x => ({'03':'Mar','06':'Jun','09':'Sep','12':'Dec'}[x])).join(' + ')} — rolls into whichever long-dated expiry comes next, T-n before each.`}
                    </p>
                  </div>
                )}

                {/* Entry/Exit Days — EOD only. Hidden under Per-Leg Rollover:
                    each leg carries its OWN exit T-n and entry is always the
                    previous roll / run start, so a single strategy-level
                    entry/exit offset is meaningless there. */}
                {backtestMode === 'eod' && !perLegRollover && (
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className="field-label">Entry (days before expiry)</label>
                      <select
                        value={entryDaysBefore}
                        onChange={e => setEntryDaysBefore(+e.target.value)}
                        className="w-full h-9 px-3 border border-default rounded text-sm bg-surface"
                      >
                        {daysOptions.map(d => <option key={d}>{d}</option>)}
                      </select>
                    </div>
                    <div>
                      <label className="field-label">Exit (days before expiry)</label>
                      <select
                        value={exitDaysBefore}
                        onChange={e => setExitDaysBefore(+e.target.value)}
                        className="w-full h-9 px-3 border border-default rounded text-sm bg-surface"
                      >
                        {daysOptions.map(d => <option key={d}>{d}</option>)}
                      </select>
                    </div>
                  </div>
                )}

                {backtestMode === 'eod' && legs.length > 0 && legs.every(l => l.segment === 'futures') && legs.some(l => l.expiry === 'next_monthly') && (
                  <div className="text-[11px] text-muted mt-1">
                    Futures Next Monthly: entry anchored to <strong>current</strong> monthly expiry, exit anchored to <strong>next</strong> monthly expiry. All Entry/Exit DTE combinations are valid.
                  </div>
                )}

              </div>
            </div>

            {/* Advanced Rules & Settings — its own card so it lines up with Legwise / Overall.
                Presentation only: every control below stays mounted (display toggle),
                so behavior is identical whether expanded or collapsed. */}
            <div className="bg-surface rounded-lg border border-default shadow-sm">
                <button
                  type="button"
                  className={`card-acc-header ${advancedOpen ? 'open' : ''}`}
                  onClick={() => setAdvancedOpen(o => !o)}
                  aria-expanded={advancedOpen}
                >
                  <h3 className="section-heading">{advancedOpen ? '▾' : '▸'} Advanced Rules &amp; Settings</h3>
                  {[rolloverToggle, noRollover, strFilter.enabled, spotAdjustmentEnabled, bufferStrikeEnabled, chargesEnabled].filter(Boolean).length > 0 && (
                    <span className="adv-badge">{[rolloverToggle, noRollover, strFilter.enabled, spotAdjustmentEnabled, bufferStrikeEnabled, chargesEnabled].filter(Boolean).length} on</span>
                  )}
                </button>
                <div className="p-4 space-y-4" style={{ display: advancedOpen ? 'block' : 'none' }}>
                {/* Rollover Toggle — EOD weekly/monthly/yearly.
                    YEARLY REQUIRES it: the engine only pins the December
                    contract when rollover is active (simulate.rs rollover gate),
                    so without this the run silently falls back to trading the
                    cadence expiries. */}
                {backtestMode === 'eod' && ['weekly', 'monthly', 'yearly'].includes(expiryBasis) && (
                  <div className="bg-surface shadow-sm border border-default rounded-xl p-4 space-y-3">
                    <div className="flex items-center justify-between">
                      <div className="flex flex-col gap-0.5">
                        <span className="text-xs font-semibold uppercase tracking-widest text-secondary border-l-4 border-accent-border pl-2">
                          Re-entry Rollover
                        </span>
                        <span className="text-[11px] text-muted pl-2">
                          Exit anchors to next expiry. Same-day rollover: exit one contract and enter the next on the same day.
                        </span>
                      </div>
                      <Toggle
                        enabled={rolloverToggle}
                        onToggle={(val) => setRolloverToggle(val !== undefined ? Boolean(val) : !rolloverToggle)}
                        size="sm"
                      />
                    </div>
                    {/* Min-DTE is meaningless (and REJECTED by the engine) under
                        YEARLY: it advances the contract to the next CADENCE
                        element, which would swap December for a weekly. T-n
                        ("Roll to next December") is the yearly roll-early knob. */}
                    {rolloverToggle && expiryBasis !== 'yearly' && (
                      <div className="space-y-2 pt-2 border-t border-default">
                        <p className="text-[11px] font-medium text-secondary pl-2">Min. days to expiry</p>
                        <p className="text-[10px] text-muted pl-2">
                          If entry falls within N days of current expiry, use next expiry's contract instead.
                        </p>
                        <div className="flex gap-2 pl-2">
                          {(expiryBasis === 'monthly' ? [0, 1, 2, 3, 4, 5, 6, 7] : [0, 1, 2, 3, 4]).map(n => (
                            <button
                              key={n}
                              type="button"
                              onClick={() => setRolloverMinDaysToExpiry(n)}
                              className={`px-3 py-1 rounded-lg text-xs font-medium border transition-colors ${
                                rolloverMinDaysToExpiry === n
                                  ? 'bg-accent text-white border-accent'
                                  : 'border-default text-secondary hover:bg-hover'
                              }`}
                            >
                              {n === 0 ? 'Off' : n}
                            </button>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                )}

                {/* Per-Leg Rollover — EOD. Each leg rolls on its OWN expiry +
                    own exit T-n; trade boundaries are the union of all legs'
                    rolls (a carried leg is marked-to-market). Additive/opt-in. */}
                {backtestMode === 'eod' && (
                  <div className="bg-surface shadow-sm border border-default rounded-xl p-4 space-y-3">
                    <div className="flex items-center justify-between">
                      <div className="flex flex-col gap-0.5">
                        <span className="text-xs font-semibold uppercase tracking-widest text-secondary border-l-4 border-accent-border pl-2">
                          Per-Leg Rollover
                        </span>
                        <span className="text-[11px] text-muted pl-2">
                          Each leg rolls on its own expiry &amp; exit T-n. Boundaries = union of all legs; a leg that isn&apos;t rolling carries (mark-to-market).
                        </span>
                      </div>
                      <Toggle
                        enabled={perLegRollover}
                        onToggle={(val) => setPerLegRollover(val !== undefined ? Boolean(val) : !perLegRollover)}
                        size="sm"
                      />
                    </div>
                    {perLegRollover && (
                      <div className="space-y-2 pt-2 border-t border-default">
                        <p className="text-[11px] font-medium text-secondary pl-2">Exit T-n per leg (trading days before that leg&apos;s expiry)</p>
                        <div className="space-y-1.5 pl-2">
                          {legs.filter(l => l.segment === 'options').map((l, i) => (
                            <div key={l.id} className="flex items-center justify-between gap-3">
                              <span className="text-[11px] text-secondary">
                                L{i + 1} · {l.option_type === 'call' ? 'CE' : 'PE'} · {String(l.expiry || 'weekly').toUpperCase()}
                              </span>
                              <input
                                type="number"
                                min={0}
                                value={l.exit_dte ?? exitDaysBefore}
                                onChange={e => updateLeg(l.id, 'exit_dte', Math.max(0, Number(e.target.value) || 0))}
                                className="h-8 px-2 w-20 text-center border border-default rounded text-xs bg-surface"
                              />
                            </div>
                          ))}
                        </div>
                        <p className="text-[10px] text-muted pl-2">
                          Entry is always the previous roll / run start. Spot-adjustment is not supported in this mode yet.
                        </p>
                      </div>
                    )}
                  </div>
                )}

                {/* Sync Roll — any multi-index run (weekly+monthly OR all-monthly) */}
                {backtestMode === 'eod' && syncRollQualifies && (
                  <div className="bg-surface shadow-sm border border-default rounded-xl p-4 space-y-3">
                    <div className="flex items-center justify-between">
                      <div className="flex flex-col gap-0.5">
                        <span className="text-xs font-semibold uppercase tracking-widest text-secondary border-l-4 border-accent-border pl-2">
                          Sync Roll
                        </span>
                        <span className="text-[11px] text-muted pl-2">
                          Multi-index: all legs square off and re-enter together on one shared cadence. Mixed weekly+monthly uses the weekly cadence; all-monthly uses the shared monthly cadence.
                        </span>
                      </div>
                      <Toggle
                        enabled={syncWeeklyRollEnabled}
                        onToggle={(val) => setSyncWeeklyRollEnabled(val !== undefined ? Boolean(val) : !syncWeeklyRollEnabled)}
                        size="sm"
                      />
                    </div>
                    {syncWeeklyRollEnabled && !rolloverToggle && (
                      <p className="text-[10px] text-amber-400 pl-2 pt-2 border-t border-default">
                        Turn on Re-entry Rollover above to activate the unified cadence.
                      </p>
                    )}
                  </div>
                )}

                {/* No Rollover */}
                {backtestMode === 'eod' && ['weekly', 'monthly'].includes(expiryBasis) && (
                  <div className="bg-surface shadow-sm border border-default rounded-xl p-4 space-y-3">
                    <div className="flex items-center justify-between">
                      <div className="flex flex-col gap-0.5">
                        <span className="text-xs font-semibold uppercase tracking-widest text-secondary border-l-4 border-accent-border pl-2">
                          No Rollover
                        </span>
                        <span className="text-[11px] text-muted pl-2">
                          Take only the first trade per segment. No re-entry until the next expiry cycle.
                        </span>
                      </div>
                      <Toggle
                        enabled={noRollover}
                        onToggle={() => setNoRollover(p => !p)}
                        size="sm"
                      />
                    </div>
                    {noRollover && (
                      <div className="space-y-2 pt-2 border-t border-default">
                        <p className="text-[11px] font-medium text-secondary pl-2">Min. days to expiry</p>
                        <p className="text-[10px] text-muted pl-2">
                          If segment starts within N days of expiry, exit extends to next expiry instead.
                        </p>
                        <div className="flex gap-2 pl-2">
                          {[0, 1, 2, 3, 4].map(n => (
                            <button
                              key={n}
                              type="button"
                              onClick={() => setNoRolloverMinDays(n)}
                              className={`px-3 py-1 rounded-lg text-xs font-medium border transition-colors ${
                                noRolloverMinDays === n
                                  ? 'bg-accent text-white border-accent'
                                  : 'border-default text-secondary hover:bg-hover'
                              }`}
                            >
                              {n === 0 ? 'Off' : n}
                            </button>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                )}

                {/* SuperTrend Filter */}
                <SuperTrendFilter
                  enabled={strFilter.enabled}
                  onToggle={(val) => setStrFilter(prev => ({ ...prev, enabled: val !== undefined ? Boolean(val) : !prev.enabled }))}
                  onFilterChange={(payload) => setStrFilter(prev => ({ ...prev, ...payload }))}
                />
                <div className="bg-surface shadow-sm border border-default rounded-xl p-4 space-y-3">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-semibold uppercase tracking-widest text-secondary border-l-4 border-accent-border pl-2">
                        Spot Adjustment{hasSecondIndexSpotAdj ? ' · NIFTY' : ''}
                      </span>
                      <Tooltip text="Exit the trade on the day the closing spot price crosses your set percentage from the entry spot. Rise exits when spot closes above target, Fall exits when spot closes below target, Both exits on either breach. With a Midcap100 leg present, this applies to NIFTY; use the Midcap100 toggle below for that leg — if both are set, whichever breaches first exits." />
                    </div>
                    <Toggle
                      enabled={spotAdjustmentEnabled}
                      onToggle={(val) => setSpotAdjustmentEnabled(prev => val !== undefined ? Boolean(val) : !prev)}
                      size="sm"
                    />
                  </div>

                  {spotAdjustmentEnabled && (
                    <div className="space-y-4 pt-1">
                      <div className="space-y-1">
                        <p className="text-xs font-semibold text-muted uppercase tracking-wide">Direction</p>
                        <div className="flex gap-2">
                          {[
                            { value: 'rise', label: '↑ Rise' },
                            { value: 'fall', label: '↓ Fall' },
                            { value: 'both', label: '↕ Both' },
                          ].map(opt => (
                            <button
                              key={opt.value}
                              type="button"
                              onClick={() => setSpotAdjustmentDirection(opt.value)}
                              className={`px-3 py-1 rounded-lg text-xs font-medium border transition-colors ${
                                spotAdjustmentDirection === opt.value
                                  ? 'bg-accent text-white border-accent'
                                  : 'border-default text-secondary hover:bg-hover'
                              }`}
                            >
                              {opt.label}
                            </button>
                          ))}
                        </div>
                      </div>

                      <div className="space-y-1">
                        <p className="text-xs font-semibold text-muted uppercase tracking-wide">Threshold</p>
                        <div className="flex items-center gap-2">
                          <input
                            type="number"
                            min={0.25}
                            max={spotAdjustmentUnits === 'percent' ? 5 : 10000}
                            step={spotAdjustmentUnits === 'percent' ? 0.25 : 50}
                            value={spotAdjustmentValue}
                            onChange={e => {
                              const nextValue = e.target.value;
                              if (nextValue === '') { setSpotAdjustmentValue(''); return; }
                              const numeric = Number(nextValue);
                              setSpotAdjustmentValue(Number.isNaN(numeric) ? '' : numeric);
                            }}
                            onBlur={() => setSpotAdjustmentValue(prev => clampSpotAdjustmentValue(prev, spotAdjustmentUnits))}
                            className="w-24 border border-default rounded-lg px-3 py-1.5 text-sm"
                          />
                          <div className="flex gap-1">
                            {['percent', 'points'].map(u => (
                              <button
                                key={u}
                                type="button"
                                onClick={() => {
                                  setSpotAdjustmentUnits(u);
                                  setSpotAdjustmentValue(u === 'percent' ? 1.0 : 200);
                                }}
                                className={`px-3 py-1 rounded-lg text-xs font-medium border transition-colors ${
                                  spotAdjustmentUnits === u
                                    ? 'bg-accent text-white border-accent'
                                    : 'border-default text-secondary hover:bg-hover'
                                }`}
                              >
                                {u === 'percent' ? '% Pct' : 'Pts'}
                              </button>
                            ))}
                          </div>
                        </div>
                      </div>

                      {spotAdjustmentHelperText && (
                        <p className="text-[11px] text-muted leading-relaxed bg-hover rounded-lg px-3 py-2">
                          {spotAdjustmentHelperText}
                        </p>
                      )}
                    </div>
                  )}

                  {/* Per-index: Midcap100 spot adjustment (applied by the overlay) */}
                  {hasMidcapLeg && (
                    <div className="pt-3 mt-1 border-t border-subtle space-y-3">
                      <div className="flex items-center justify-between">
                        <span className="text-[11px] font-semibold uppercase tracking-wide text-secondary">
                          Midcap100
                        </span>
                        <Toggle
                          enabled={midcapSpotAdjEnabled}
                          onToggle={(val) => setMidcapSpotAdjEnabled(prev => val !== undefined ? Boolean(val) : !prev)}
                          size="sm"
                        />
                      </div>
                      {midcapSpotAdjEnabled && (
                        <div className="space-y-4 pt-1">
                          <div className="space-y-1">
                            <p className="text-xs font-semibold text-muted uppercase tracking-wide">Direction</p>
                            <div className="flex gap-2">
                              {[
                                { value: 'rise', label: '↑ Rise' },
                                { value: 'fall', label: '↓ Fall' },
                                { value: 'both', label: '↕ Both' },
                              ].map(opt => (
                                <button
                                  key={opt.value}
                                  type="button"
                                  onClick={() => setMidcapSpotAdjDirection(opt.value)}
                                  className={`px-3 py-1 rounded-lg text-xs font-medium border transition-colors ${
                                    midcapSpotAdjDirection === opt.value
                                      ? 'bg-accent text-white border-accent'
                                      : 'border-default text-secondary hover:bg-hover'
                                  }`}
                                >
                                  {opt.label}
                                </button>
                              ))}
                            </div>
                          </div>
                          <div className="space-y-1">
                            <p className="text-xs font-semibold text-muted uppercase tracking-wide">Threshold</p>
                            <div className="flex items-center gap-2">
                              <input
                                type="number"
                                min={0.25}
                                max={midcapSpotAdjUnits === 'percent' ? 5 : 10000}
                                step={midcapSpotAdjUnits === 'percent' ? 0.25 : 50}
                                value={midcapSpotAdjValue}
                                onChange={e => {
                                  const v = e.target.value;
                                  if (v === '') { setMidcapSpotAdjValue(''); return; }
                                  const n = Number(v);
                                  setMidcapSpotAdjValue(Number.isNaN(n) ? '' : n);
                                }}
                                onBlur={() => setMidcapSpotAdjValue(prev => clampSpotAdjustmentValue(prev, midcapSpotAdjUnits))}
                                className="w-24 border border-default rounded-lg px-3 py-1.5 text-sm"
                              />
                              <div className="flex gap-1">
                                {['percent', 'points'].map(u => (
                                  <button
                                    key={u}
                                    type="button"
                                    onClick={() => {
                                      setMidcapSpotAdjUnits(u);
                                      setMidcapSpotAdjValue(u === 'percent' ? 1.0 : 200);
                                    }}
                                    className={`px-3 py-1 rounded-lg text-xs font-medium border transition-colors ${
                                      midcapSpotAdjUnits === u
                                        ? 'bg-accent text-white border-accent'
                                        : 'border-default text-secondary hover:bg-hover'
                                    }`}
                                  >
                                    {u === 'percent' ? '% Pct' : 'Pts'}
                                  </button>
                                ))}
                              </div>
                            </div>
                          </div>
                        </div>
                      )}
                      {/* Combine mode — only when BOTH NIFTY & Midcap spot adj are on */}
                      {spotAdjustmentEnabled && midcapSpotAdjEnabled && (
                        <div className="pt-3 mt-1 border-t border-subtle space-y-3">
                          <div className="space-y-1">
                            <p className="text-xs font-semibold text-muted uppercase tracking-wide">When both are on, trigger on</p>
                            <div className="flex gap-2">
                              {[
                                { value: 'earliest', label: 'Whichever first' },
                                { value: 'confirm', label: 'Whichever late' },
                              ].map(opt => (
                                <button
                                  key={opt.value}
                                  type="button"
                                  onClick={() => setSpotAdjCombineMode(opt.value)}
                                  className={`px-3 py-1 rounded-lg text-xs font-medium border transition-colors ${
                                    spotAdjCombineMode === opt.value
                                      ? 'bg-accent text-white border-accent'
                                      : 'border-default text-secondary hover:bg-hover'
                                  }`}
                                >
                                  {opt.label}
                                </button>
                              ))}
                            </div>
                            <p className="text-[11px] text-muted">
                              {spotAdjCombineMode === 'earliest'
                                ? 'Adjust when either NIFTY or Midcap breaches first (current behaviour).'
                                : 'Adjust only when BOTH breach the same direction within N trading days — on the later (confirming) breach.'}
                            </p>
                          </div>
                          {spotAdjCombineMode === 'confirm' && (
                            <div className="space-y-1">
                              <p className="text-xs font-semibold text-muted uppercase tracking-wide">Confirm within (N trading days)</p>
                              <input
                                type="number"
                                min={0}
                                max={20}
                                step={1}
                                value={spotAdjConfirmDays}
                                onChange={e => {
                                  const v = e.target.value;
                                  if (v === '') { setSpotAdjConfirmDays(''); return; }
                                  const n = parseInt(v, 10);
                                  setSpotAdjConfirmDays(Number.isNaN(n) ? '' : Math.max(0, Math.min(20, n)));
                                }}
                                onBlur={() => setSpotAdjConfirmDays(prev => Math.max(0, Math.min(20, parseInt(prev, 10) || 0)))}
                                className="w-24 border border-default rounded-lg px-3 py-1.5 text-sm"
                              />
                              <p className="text-[11px] text-muted">0 = both must breach the same day.</p>
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  )}

                  {/* Per-index: MIDCPNIFTY spot adjustment. Shown whenever the
                      strategy holds a MIDCPNIFTY leg (options OR futures). */}
                  {hasMidcpniftyLeg && (
                    <div className="pt-3 mt-1 border-t border-subtle space-y-3">
                      <div className="flex items-center justify-between">
                        <span className="text-[11px] font-semibold uppercase tracking-wide text-secondary">
                          MIDCPNIFTY
                        </span>
                        <Toggle
                          enabled={midcpSpotAdjEnabled}
                          onToggle={(val) => setMidcpSpotAdjEnabled(prev => val !== undefined ? Boolean(val) : !prev)}
                          size="sm"
                        />
                      </div>
                      {midcpSpotAdjEnabled && (
                        <div className="space-y-4 pt-1">
                          <div className="space-y-1">
                            <p className="text-xs font-semibold text-muted uppercase tracking-wide">Direction</p>
                            <div className="flex gap-2">
                              {[
                                { value: 'rise', label: '↑ Rise' },
                                { value: 'fall', label: '↓ Fall' },
                                { value: 'both', label: '↕ Both' },
                              ].map(opt => (
                                <button
                                  key={opt.value}
                                  type="button"
                                  onClick={() => setMidcpSpotAdjDirection(opt.value)}
                                  className={`px-3 py-1 rounded-lg text-xs font-medium border transition-colors ${
                                    midcpSpotAdjDirection === opt.value
                                      ? 'bg-accent text-white border-accent'
                                      : 'border-default text-secondary hover:bg-hover'
                                  }`}
                                >
                                  {opt.label}
                                </button>
                              ))}
                            </div>
                          </div>
                          <div className="space-y-1">
                            <p className="text-xs font-semibold text-muted uppercase tracking-wide">Threshold</p>
                            <div className="flex items-center gap-2">
                              <input
                                type="number"
                                min={0.25}
                                max={midcpSpotAdjUnits === 'percent' ? 5 : 10000}
                                step={midcpSpotAdjUnits === 'percent' ? 0.25 : 50}
                                value={midcpSpotAdjValue}
                                onChange={e => {
                                  const v = e.target.value;
                                  if (v === '') { setMidcpSpotAdjValue(''); return; }
                                  const n = Number(v);
                                  setMidcpSpotAdjValue(Number.isNaN(n) ? '' : n);
                                }}
                                onBlur={() => setMidcpSpotAdjValue(prev => clampSpotAdjustmentValue(prev, midcpSpotAdjUnits))}
                                className="w-24 border border-default rounded-lg px-3 py-1.5 text-sm"
                              />
                              <div className="flex gap-1">
                                {['percent', 'points'].map(u => (
                                  <button
                                    key={u}
                                    type="button"
                                    onClick={() => {
                                      setMidcpSpotAdjUnits(u);
                                      setMidcpSpotAdjValue(u === 'percent' ? 1.0 : 200);
                                    }}
                                    className={`px-3 py-1 rounded-lg text-xs font-medium border transition-colors ${
                                      midcpSpotAdjUnits === u
                                        ? 'bg-accent text-white border-accent'
                                        : 'border-default text-secondary hover:bg-hover'
                                    }`}
                                  >
                                    {u === 'percent' ? '% Pct' : 'Pts'}
                                  </button>
                                ))}
                              </div>
                            </div>
                            <p className="text-[11px] text-muted">
                              MIDCPNIFTY spot data starts 01-Jan-2020 — earlier ranges are rejected.
                            </p>
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                </div>
                <div className="bg-surface shadow-sm border border-default rounded-xl p-4 space-y-3">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-semibold uppercase tracking-widest text-secondary border-l-4 border-accent-border pl-2">
                        Buffer Strike
                      </span>
                      <Tooltip text="Shift the selected option strike away from ATM by a fixed percentage or points for call, put, or both sides. Use this when you want entries slightly away from the exact ATM strike." />
                    </div>
                    <Toggle enabled={bufferStrikeEnabled} onToggle={() => setBufferStrikeEnabled(prev => !prev)} size="sm" />
                  </div>

                  {bufferStrikeEnabled && (
                    <div className="space-y-4 pt-1">
                      <div className="space-y-1">
                        <p className="text-xs font-semibold text-muted uppercase tracking-wide">Apply to</p>
                        <div className="flex gap-2">
                          {['call', 'put', 'both'].map(opt => (
                            <button
                              key={opt}
                              type="button"
                              onClick={() => setBufferStrikeApplyTo(opt)}
                              className={`px-3 py-1 rounded-lg text-xs font-medium border transition-colors ${
                                bufferStrikeApplyTo === opt
                                  ? 'bg-accent text-white border-accent'
                                  : 'border-default text-secondary hover:bg-hover'
                              }`}
                            >
                              {opt === 'call' ? 'Call' : opt === 'put' ? 'Put' : 'Both'}
                            </button>
                          ))}
                        </div>
                      </div>

                      <div className="space-y-1">
                        <p className="text-xs font-semibold text-muted uppercase tracking-wide">Buffer</p>
                        <div className="flex items-center gap-2">
                          <input
                            type="number"
                            value={bufferStrikeValue}
                            onChange={e => setBufferStrikeValue(Math.max(0, parseFloat(e.target.value) || 0))}
                            step={bufferStrikeUnit === 'percent' ? 0.1 : 50}
                            min={bufferStrikeUnit === 'percent' ? 0.1 : 25}
                            max={bufferStrikeUnit === 'percent' ? 20 : 10000}
                            className="w-24 border border-default rounded-lg px-3 py-1.5 text-sm"
                          />
                          <div className="flex gap-1">
                            {['percent', 'points'].map(u => (
                              <button
                                key={u}
                                type="button"
                                onClick={() => {
                                  setBufferStrikeUnit(u);
                                  setBufferStrikeValue(u === 'percent' ? 0.5 : 500);
                                }}
                                className={`px-3 py-1 rounded-lg text-xs font-medium border transition-colors ${
                                  bufferStrikeUnit === u
                                    ? 'bg-accent text-white border-accent'
                                    : 'border-default text-secondary hover:bg-hover'
                                }`}
                              >
                                {u === 'percent' ? '% Pct' : 'Pts'}
                              </button>
                            ))}
                          </div>
                        </div>
                      </div>

                      <div className="space-y-1">
                        <p className="text-xs font-semibold text-muted uppercase tracking-wide">
                          Buffer position
                        </p>
                        <div className="flex flex-wrap gap-4">
                          <label className="flex items-center gap-2 cursor-pointer">
                            <input
                              type="checkbox"
                              checked={bufferPositionAbove}
                              onChange={e => setBufferPositionAbove(e.target.checked)}
                              className="accent-blue-600"
                            />
                            <span className="text-xs font-medium text-orange-700">↑ Above</span>
                            <span className="text-xs text-muted">CE moves up (more OTM)</span>
                          </label>
                          <label className="flex items-center gap-2 cursor-pointer">
                            <input
                              type="checkbox"
                              checked={bufferPositionBelow}
                              onChange={e => setBufferPositionBelow(e.target.checked)}
                              className="accent-blue-600"
                            />
                            <span className="text-xs font-medium text-blue-700">↓ Below</span>
                            <span className="text-xs text-muted">PE moves down (more OTM)</span>
                          </label>
                        </div>
                      </div>

                      <p className="text-[11px] text-muted leading-relaxed bg-hover rounded-lg px-3 py-2">
                        {getBufferPreview(
                          bufferStrikeValue,
                          bufferStrikeUnit,
                          bufferStrikeApplyTo,
                          bufferPositionAbove,
                          bufferPositionBelow,
                          instrument
                        )}
                      </p>
                    </div>
                  )}
                </div>
                {/* Strike Shift on Missing Contract control removed —
                    engine now always walks TOWARD ATM for zero-turnover
                    strikes and surfaces the reason in the tradesheet column. */}
                {/* Slippage % moved to per-leg control (each leg card below,
                    "Slippage %" in the Advanced controls section) — there is
                    no strategy-level slippage anymore. */}
                <div>
                  <div className="flex items-center justify-between mb-1">
                    <label className="text-xs font-medium text-secondary">Transaction Charges</label>
<Toggle enabled={chargesEnabled} onToggle={(val) => setChargesEnabled(prev => val !== undefined ? Boolean(val) : !prev)} size="sm" />
                  </div>
                  {chargesEnabled && (
                    <div className="rounded-md border border-subtle bg-hover p-2.5 space-y-1 text-xs text-muted">
                      <p className="font-medium text-secondary">Zerodha F&amp;O Options</p>
                      <div className="grid grid-cols-2 gap-x-3 gap-y-0.5">
                        <span>Brokerage</span><span className="text-right">₹20 / order</span>
                        <span>STT</span><span className="text-right">0.15% sell-side</span>
                        <span>Txn (NSE)</span><span className="text-right">0.03553%</span>
                        <span>SEBI</span><span className="text-right">₹10 / crore</span>
                        <span>Stamp</span><span className="text-right">0.003% buy-side</span>
                        <span>GST</span><span className="text-right">18% on brk+txn+SEBI</span>
                      </div>
                      <p className="text-muted pt-1">Charges are reflected as effective price adjustments. Click Re-calculate to apply.</p>
                    </div>
                  )}
                </div>
                </div>
            </div>

            {/* Legwise Controls Card — collapsible (presentation only; control stays mounted) */}
            <div className="bg-surface rounded-lg border border-default shadow-sm">
                <button
                  type="button"
                  className={`card-acc-header ${legwiseOpen ? 'open' : ''}`}
                  onClick={() => setLegwiseOpen(o => !o)}
                  aria-expanded={legwiseOpen}
                >
                  <h3 className="section-heading">{legwiseOpen ? '▾' : '▸'} Legwise Controls</h3>
                  <span className="card-acc-chip">{squareOffMode === 'complete' ? 'Complete' : 'Partial'}</span>
                </button>
              <div className="p-4 space-y-3" style={{ display: legwiseOpen ? 'block' : 'none' }}>
                <div className="flex items-center justify-between">
                  <div className="flex flex-col">
                    <span className="text-xs text-secondary">Square Off Mode</span>
                    <span className="ctrl-caption">Partial exits each leg on its own SL/target; Complete closes all legs together.</span>
                  </div>
                  <SegBtn
                    options={[{ value: 'partial', label: 'Partial' }, { value: 'complete', label: 'Complete' }]}
                    value={squareOffMode}
                    onChange={setSquareOffMode}
                    size="sm"
                  />
                </div>

              </div>
            </div>

            {/* Overall Settings Card — collapsible (presentation only; controls stay mounted) */}
            <div className="bg-surface rounded-lg border border-default shadow-sm">
                <button
                  type="button"
                  className={`card-acc-header ${overallOpen ? 'open' : ''}`}
                  onClick={() => setOverallOpen(o => !o)}
                  aria-expanded={overallOpen}
                >
                  <h3 className="section-heading">{overallOpen ? '▾' : '▸'} Overall Settings</h3>
                  {[overallSLEnabled, overallTgtEnabled].filter(Boolean).length > 0 && (
                    <span className="adv-badge">{[overallSLEnabled, overallTgtEnabled].filter(Boolean).length} on</span>
                  )}
                </button>
              <div className="p-4 space-y-4" style={{ display: overallOpen ? 'block' : 'none' }}>
                {/* Overall Stop Loss */}
                <div>
                  <div className="flex items-center justify-between mb-2">
                    <div className="flex flex-col">
                      <span className="text-xs font-medium text-secondary">Overall Stop Loss</span>
                      <span className="ctrl-caption">Exit the whole strategy when combined loss hits this limit.</span>
                    </div>
                    <Toggle enabled={overallSLEnabled} onToggle={(val) => setOverallSLEnabled(prev => val !== undefined ? Boolean(val) : !prev)} size="sm" />
                  </div>
                  {overallSLEnabled && (
                    <div className="flex gap-2">
                      <select
                        value={overallSLType}
                        onChange={e => setOverallSLType(e.target.value)}
                        className="flex-1 h-8 px-2 border border-default rounded text-xs bg-surface"
                      >
                        <option value="max_loss">Max Loss </option>
                        <option value="total_premium_pct"> Total Premium %</option>
                      </select>
                      <input
                        type="number"
                        value={overallSLValue}
                        onChange={e => setOverallSLValue(e.target.value === '' ? '' : +e.target.value)}
                        className="w-20 h-8 px-2 border border-default rounded text-xs text-center"
                        placeholder={overallSLType === 'max_loss' ? '₹' : '%'}
                      />
                    </div>
                  )}
                </div>

                {/* Overall Target */}
                <div>
                  <div className="flex items-center justify-between mb-2">
                    <div className="flex flex-col">
                      <span className="text-xs font-medium text-secondary">Overall Target</span>
                      <span className="ctrl-caption">Exit the whole strategy when combined profit hits this target.</span>
                    </div>
                    <Toggle enabled={overallTgtEnabled} onToggle={(val) => setOverallTgtEnabled(prev => val !== undefined ? Boolean(val) : !prev)} size="sm" />
                  </div>
                  {overallTgtEnabled && (
                    <div className="flex gap-2">
                      <select
                        value={overallTgtType}
                        onChange={e => setOverallTgtType(e.target.value)}
                        className="flex-1 h-8 px-2 border border-default rounded text-xs bg-surface"
                      >
                        <option value="max_profit">Max Profit</option>
                        <option value="total_premium_pct">% of Premium</option>
                      </select>
                      <input
                        type="number"
                        value={overallTgtValue}
                        onChange={e => setOverallTgtValue(e.target.value === '' ? '' : +e.target.value)}
                        className="w-20 h-8 px-2 border border-default rounded text-xs text-center"
                        placeholder={overallTgtType === 'max_profit' ? '₹' : '%'}
                      />
                    </div>
                  )}
                </div>


              </div>
            </div>
          </div>

          {/* RIGHT COLUMN - Leg Builder (AlgoTest style) */}
          <div className="col-span-7 space-y-4">

            {/* ── Top configurator panel ── */}
            <div className="bg-surface rounded-lg border border-default shadow-sm">
              {/* ── Index workspace tab header ── */}
              <div className="border-b border-subtle">
                <div className="px-4 pt-2.5 pb-1 flex items-center justify-between">
                  <h3 className="section-heading">Leg Builder</h3>
                  <Tooltip text="Pick an index tab to build a leg on that index, or pick Midcap100 Overlay for the hypothetical overlay leg." />
                </div>
                {/* Three tabs: NIFTY · MIDCPNIFTY · Midcap100 Overlay */}
                <div className="px-3 flex items-end gap-0" style={{ marginBottom: -1 }}>
                  {(() => {
                    const activeTabKey = draftLeg.segment === 'midcap100'
                      ? 'MIDCAP100'
                      : String(draftLeg.index || instrument).toUpperCase();
                    const tabs = [
                      { value: 'NIFTY',     label: 'NIFTY',              type: 'index',   color: 'var(--accent)' },
                      { value: 'MIDCPNIFTY',label: 'MIDCPNIFTY',         type: 'index',   color: '#2dd4bf' },
                      { value: 'BANKNIFTY', label: 'BANKNIFTY',          type: 'index',   color: '#f97316' },
                      { value: 'MIDCAP100', label: 'Midcap100 Overlay',  type: 'overlay', color: '#a78bfa' },
                    ];
                    return tabs.map(tab => {
                      const isActive = activeTabKey === tab.value;
                      const isOverlay = tab.type === 'overlay';
                      const legCount = isOverlay
                        ? legs.filter(l => l.segment === 'midcap100').length
                        : legs.filter(l => l.segment !== 'midcap100' && String(l.index || instrument).toUpperCase() === tab.value).length;
                      return (
                        <button
                          key={tab.value}
                          onClick={() => {
                            if (isOverlay) {
                              setDraftLeg(prev => ({ ...prev, segment: 'midcap100' }));
                            } else {
                              const cfg = getIndexConfig(tab.value) || {};
                              const monthlyOnly = !(cfg.expiryBases || []).includes('weekly');
                              setDraftLeg(prev => ({
                                ...prev,
                                index: tab.value,
                                segment: prev.segment === 'midcap100' ? 'options' : prev.segment,
                                // Keep the user's chosen Strike Gap if it's valid for this
                                // index (MIDCPNIFTY allows 25/50/100); only coerce when the
                                // current gap isn't offered for the new index. Previously this
                                // always reset to the index's native gap, silently discarding a
                                // user's explicit 50 → 25 every time the tab was clicked.
                                strike_interval: normalizeStrikeInterval(prev.strike_interval, tab.value),
                                expiry: monthlyOnly ? 'monthly' : (cfg.defaultOptionExpiry || 'weekly'),
                              }));
                            }
                          }}
                          style={{
                            fontFamily: isOverlay ? 'Outfit, sans-serif' : 'IBM Plex Mono, monospace',
                            fontSize: '0.67rem',
                            fontWeight: 700,
                            letterSpacing: isOverlay ? '0' : '0.05em',
                            padding: '6px 14px 8px',
                            borderRadius: '6px 6px 0 0',
                            border: isActive ? '1px solid var(--border-default)' : '1px solid transparent',
                            borderBottom: isActive ? '2px solid var(--bg-surface)' : '2px solid transparent',
                            background: isActive ? 'var(--bg-surface)' : 'transparent',
                            color: isActive ? tab.color : 'var(--text-muted)',
                            cursor: 'pointer',
                            display: 'flex',
                            alignItems: 'center',
                            gap: 7,
                            transition: 'color 0.15s',
                            position: 'relative',
                            zIndex: 1,
                            borderLeft: isOverlay ? '1px solid var(--border-subtle)' : undefined,
                            marginLeft: isOverlay ? 8 : 0,
                          }}
                        >
                          {isOverlay && (
                            <span style={{ fontSize: '0.6rem', opacity: 0.7 }}>◈</span>
                          )}
                          {tab.label}
                          {legCount > 0 && (
                            <span style={{
                              background: isActive ? `${tab.color}22` : 'var(--bg-elevated)',
                              color: isActive ? tab.color : 'var(--text-muted)',
                              border: `1px solid ${isActive ? `${tab.color}55` : 'var(--border-default)'}`,
                              borderRadius: 999,
                              fontSize: '0.58rem',
                              fontWeight: 700,
                              padding: '1px 6px',
                              minWidth: 18,
                              textAlign: 'center',
                              lineHeight: 1.6,
                            }}>
                              {legCount}
                            </span>
                          )}
                        </button>
                      );
                    });
                  })()}
                </div>
                {/* Context bar — updates per active tab */}
                {(() => {
                  const isMidcap100Tab = draftLeg.segment === 'midcap100';
                  const activeIdx = String(draftLeg.index || instrument).toUpperCase();
                  const isMidcp = !isMidcap100Tab && activeIdx === 'MIDCPNIFTY';
                  const barColor = isMidcap100Tab ? '#a78bfa' : (isMidcp ? '#2dd4bf' : 'var(--accent)');
                  const barText = isMidcap100Tab
                    ? 'Hypothetical overlay · follows NIFTY trade dates · no real strike or expiry'
                    : (expiryBasis === 'yearly'
                        ? 'Yearly: holds the December contract · round-1000 strikes are the liquid ones'
                        : (isMidcp ? 'Weekly (till Nov 2024) & monthly · Strike gap 25' : 'Weekly & monthly expiries · Strike gap 50'));
                  const barLabel = isMidcap100Tab ? 'MIDCAP100 OVERLAY' : activeIdx;
                  return (
                    <div style={{
                      padding: '4px 16px 5px',
                      background: 'var(--bg-elevated)',
                      borderTop: '1px solid var(--border-subtle)',
                      fontSize: '0.62rem',
                      color: 'var(--text-muted)',
                      fontFamily: 'Outfit, sans-serif',
                      display: 'flex',
                      alignItems: 'center',
                      gap: 6,
                    }}>
                      <span>Working in</span>
                      <span style={{ fontFamily: 'IBM Plex Mono, monospace', fontWeight: 700, fontSize: '0.63rem', color: barColor }}>{barLabel}</span>
                      <span style={{ color: 'var(--border-default)' }}>·</span>
                      <span>{barText}</span>
                    </div>
                  );
                })()}
              </div>
              <div className="px-4 py-3 flex flex-wrap items-end gap-3">

                {/* Instrument — only for real index tabs (not Midcap100 Overlay) */}
                {draftLeg.segment !== 'midcap100' && (
                  <div>
                    <label className="field-label">Instrument</label>
                    <SegBtn
                      options={[{ value: 'options', label: 'Options' }, { value: 'futures', label: 'Futures' }]}
                      value={draftLeg.segment}
                      onChange={v => setDraftLeg(prev => ({
                        ...prev,
                        segment: v,
                        expiry: normalizeExpiryForIndex(v === 'futures' ? 'monthly' : prev.expiry, draftLeg.index || instrument, v, expiryBasis),
                      }))}
                    />
                  </div>
                )}


                {/* Total Lot */}
                <div>
                  <label className="field-label">Total Lot</label>
                  <input type="number" min={1} value={draftLeg.lot}
                    onChange={e => setDraftLeg(prev => ({ ...prev, lot: Math.max(1, parseInt(e.target.value) || 1) }))}
                    className="w-16 h-8 px-2 border border-default rounded text-xs text-center bg-surface focus:outline-none focus:ring-2 focus:ring-accent/40"
                  />
                </div>

                {/* Position */}
                <div>
                  <label className="field-label">Position</label>
                  <SegBtn
                    options={[{ value: 'buy', label: 'Buy' }, { value: 'sell', label: 'Sell' }]}
                    value={draftLeg.position}
                    onChange={v => setDraftLeg(prev => ({ ...prev, position: v }))}
                  />
                </div>

                {/* Option Type */}
                {draftLeg.segment === 'options' && (
                  <div>
                    <label className="field-label">Option Type</label>
                    <SegBtn
                      options={[{ value: 'call', label: 'Call' }, { value: 'put', label: 'Put' }]}
                      value={draftLeg.option_type}
                      onChange={v => setDraftLeg(prev => ({ ...prev, option_type: v }))}
                    />
                  </div>
                )}

                {/* Expiry — hidden for Midcap100 (follows the NIFTY trade's dates) */}
                {draftLeg.segment !== 'midcap100' && (
                  <div>
                    <label className="field-label">Expiry</label>
                    <select value={draftLeg.expiry}
                      onChange={e => setDraftLeg(prev => ({ ...prev, expiry: e.target.value }))}
                      className="h-8 px-2 border border-default rounded text-xs bg-surface focus:outline-none focus:ring-2 focus:ring-accent/40 w-36">
                      {(draftLeg.segment === 'options' ? getOptionExpiryOptions(draftLeg.index || instrument, expiryBasis) : FUTURES_EXPIRIES).map(opt => (
                        <option key={opt.value} value={opt.value}>{opt.label}</option>
                      ))}
                    </select>
                  </div>
                )}

                {/* Midcap100 pricing mode */}
                {draftLeg.segment === 'midcap100' && (
                  <>
                    <div>
                      <label className="field-label">Pricing</label>
                      <SegBtn
                        options={[{ value: 'spot', label: 'Spot' }, { value: 'hypothetical', label: 'Hypothetical' }]}
                        value={draftLeg.midcap_mode}
                        onChange={v => setDraftLeg(prev => ({ ...prev, midcap_mode: v }))}
                      />
                    </div>
                    {draftLeg.midcap_mode === 'hypothetical' && (
                      <div>
                        <label className="field-label">Cost % / month</label>
                        <input type="number" min={0} step="0.05" value={draftLeg.cost_pct_per_month}
                          onChange={e => setDraftLeg(prev => ({ ...prev, cost_pct_per_month: Math.max(0, parseFloat(e.target.value) || 0) }))}
                          className="w-20 h-8 px-2 border border-default rounded text-xs text-center bg-surface focus:outline-none focus:ring-2 focus:ring-accent/40"
                        />
                      </div>
                    )}
                  </>
                )}

                {/* Strike Criteria */}
                {draftLeg.segment === 'options' && (
                  <div>
                    <label className="field-label">Strike Criteria</label>
                    <select value={draftLeg.strike_criteria}
                      onChange={e => setDraftLeg(prev => {
                        const next = { ...prev, strike_criteria: e.target.value };
                        // Auto-configure a Relative-to-Leg wing from its parent short:
                        // same option type, opposite position (Sell → Buy).
                        if (e.target.value === 'rel_leg') {
                          const parent = legs[(Number(prev.ref_leg) || 1) - 1];
                          if (parent && parent.segment === 'options') {
                            next.option_type = parent.option_type;
                            next.position = parent.position === 'sell' ? 'buy' : 'sell';
                          }
                        }
                        return next;
                      })}
                      className="h-8 px-2 border border-default rounded text-xs bg-surface text-secondary focus:outline-none focus:ring-2 focus:ring-accent/40 w-44">
                      <option value="strike_type">Strike Type</option>
                      <option value="premium_range">Premium Range</option>
                      <option value="closest_premium">Closest Premium</option>
                      <option value="premium_gte">Premium &gt;=</option>
                      <option value="premium_lte">Premium &lt;=</option>
                      <option value="time_value">Time Value (nearest)</option>
                      <option value="time_value_gte">Time Value &gt;=</option>
                      <option value="time_value_lte">Time Value &lt;=</option>
                      <option value="straddle_width">Straddle Width</option>
                      <option value="pct_of_atm">% of ATM</option>
                      <option value="synthetic_future">Synthetic Future</option>
                      <option value="atm_straddle_prem_pct">ATM Straddle Premium %</option>
                      {legs.length > 0 && <option value="rel_leg">Relative to Leg</option>}
                      {legs.length > 0 && <option value="rel_leg_premium">Relative to Leg Premium</option>}
                    </select>
                    {draftLeg.strike_criteria === 'straddle_width' && (
                      <div className="flex items-center gap-1 mt-2 text-xs text-secondary">
                        <span className="text-xs text-muted whitespace-nowrap">ATM Strike</span>
                        <select
                          value={draftLeg.straddle_direction ?? '+'}
                          onChange={e => setDraftLeg(prev => ({ ...prev, straddle_direction: e.target.value }))}
                          className="h-7 px-2 border border-default rounded text-xs bg-surface"
                        >
                          <option value="+">+</option>
                          <option value="-">-</option>
                        </select>
                        <span className="text-xs text-muted">(</span>
                        <input
                          type="number"
                          value={draftLeg.straddle_multiplier ?? 0.5}
                          onChange={e => setDraftLeg(prev => ({ ...prev, straddle_multiplier: parseFloat(e.target.value) || 0 }))}
                          step="0.1"
                          min="0"
                          max="10"
                          className="w-16 h-7 px-2 border border-default rounded text-xs text-center"
                        />
                        <span className="text-xs text-muted whitespace-nowrap">× ATM Straddle Price )</span>
                      </div>
                    )}
                  </div>
                )}

                {backtestMode === 'eod' && draftLeg.segment === 'options' && (
                  <div>
                    <label className="field-label">Strike Gap</label>
                    <StrikeIntervalSelect
                      value={draftLeg.strike_interval}
                      onChange={value => setDraftLeg(prev => ({ ...prev, strike_interval: value }))}
                      index={draftLeg.index || instrument}
                      className="h-8 px-2 border border-default rounded text-xs bg-surface focus:outline-none focus:ring-2 focus:ring-accent/40 w-24"
                    />
                  </div>
                )}

                {/* Strike Type / Premium */}
                {draftLeg.segment === 'options' && draftLeg.strike_criteria !== 'straddle_width' && (
                  <div>
                    {(draftLeg.strike_criteria === 'strike_type' || draftLeg.strike_criteria === 'synthetic_future') && (
                      <>
                        <label className="field-label">Strike Type</label>
                        <select value={draftLeg.strike_type}
                          onChange={e => setDraftLeg(prev => ({ ...prev, strike_type: e.target.value }))}
                          className="h-8 px-2 border border-default rounded text-xs bg-surface focus:outline-none focus:ring-2 focus:ring-accent/40 w-28">
                          {strikeTypeOpts.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                        </select>
                      </>
                    )}

                    {draftLeg.strike_criteria === 'premium_range' && (
                      <>
                        <div className="flex gap-2">
                          <div>
                            <label className="field-label">Lower Range</label>
                            <input type="number" min={0} placeholder="Lower" value={draftLeg.premium_min || ''}
                              onChange={e => setDraftLeg(prev => ({ ...prev, premium_min: +e.target.value }))}
                              className="w-20 h-8 px-2 border border-default rounded text-xs text-center" />
                          </div>
                          <div>
                            <label className="field-label">Upper Range</label>
                            <input type="number" min={0} placeholder="Upper" value={draftLeg.premium_max || ''}
                              onChange={e => setDraftLeg(prev => ({ ...prev, premium_max: +e.target.value }))}
                              className="w-20 h-8 px-2 border border-default rounded text-xs text-center" />
                          </div>
                        </div>
                      </>
                    )}

                    {(['closest_premium','premium_gte','premium_lte','time_value','time_value_gte','time_value_lte'].includes(draftLeg.strike_criteria)) && (
                      <>
                        <label className="field-label">{String(draftLeg.strike_criteria || '').startsWith('time_value') ? 'Time Value' : 'Premium'}</label>
                        <input type="number" min={0} placeholder={String(draftLeg.strike_criteria || '').startsWith('time_value') ? 'Time Value' : 'Premium'} value={draftLeg.premium_value ?? ''}
                          onChange={e => setDraftLeg(prev => ({ ...prev, premium_value: e.target.value === '' ? null : +e.target.value }))}
                          className="w-24 h-8 px-2 border border-default rounded text-xs text-center" />
                                      {String(draftLeg.strike_criteria || '').startsWith('time_value') && (
                                        <>
                                          <select value={draftLeg.tv_moneyness || 'ATM'}
                                            onChange={e => setDraftLeg(prev => ({ ...prev, tv_moneyness: e.target.value }))}
                                            className="h-8 px-2 border border-default rounded text-xs bg-surface">
                                            <option value="ATM">ATM (both sides)</option>
                                            <option value="OTM">OTM only</option>
                                            <option value="ITM">ITM only</option>
                                          </select>
                                          <label className="field-label">Range %</label>
                                          <input type="number" min={0} step="0.5" placeholder="all"
                                            value={draftLeg.tv_range_pct ?? ''}
                                            onChange={e => setDraftLeg(prev => ({ ...prev, tv_range_pct: e.target.value === '' ? null : +e.target.value }))}
                                            className="w-20 h-8 px-1 border border-default rounded text-xs text-center" />
                                            <select value={draftLeg.tv_units || 'points'}
                                              onChange={e => setDraftLeg(prev => ({ ...prev, tv_units: e.target.value }))}
                                              className="h-8 px-1 border border-default rounded text-xs bg-surface">
                                              <option value="points">pts</option>
                                              <option value="percent">%</option>
                                            </select>
                                        </>
                                      )}
                      </>
                    )}

                    {draftLeg.strike_criteria === 'pct_of_atm' && (
                      <>
                        <label className="field-label">&nbsp;</label>
                        <div className="flex flex-col gap-0.5">
                          <div className="flex items-center gap-1 h-8">
                            <select
                              value={draftLeg.pct_atm_moneyness ?? 'OTM'}
                              onChange={e => setDraftLeg(prev => ({ ...prev, pct_atm_moneyness: e.target.value }))}
                              className="h-8 px-2 border border-default rounded text-xs bg-surface font-medium"
                            >
                              <option value="OTM">OTM</option>
                              <option value="ITM">ITM</option>
                            </select>
                            <input
                              type="number"
                              min="0"
                              step="0.1"
                              value={draftLeg.pct_value ?? 0}
                              onChange={e => setDraftLeg(prev => ({ ...prev, pct_value: parseFloat(e.target.value) || 0 }))}
                              className="w-20 h-8 px-2 border border-default rounded text-xs text-center"
                            />
                            <span className="text-xs text-muted whitespace-nowrap">% of ATM</span>
                          </div>
                          <span className="text-xs text-muted">
                            {(() => {
                              const isCE = ['call', 'ce'].includes((draftLeg.option_type || '').toLowerCase());
                              const m = draftLeg.pct_atm_moneyness ?? 'OTM';
                              const up = isCE ? m === 'OTM' : m === 'ITM';
                              return `strike = ATM ${up ? '+' : '−'}${draftLeg.pct_value ?? 0}%  (${up ? 'above' : 'below'} spot)`;
                            })()}
                          </span>
                        </div>
                      </>
                    )}

                    {draftLeg.strike_criteria === 'atm_straddle_prem_pct' && (
                      <>
                        <label className="field-label">&nbsp;</label>
                        <div className="flex items-center gap-1 h-8">
                          <span className="text-xs text-muted whitespace-nowrap">ATM Straddle Premium %</span>
                          <input
                            type="number"
                            min="0"
                            step="0.1"
                            value={draftLeg.atm_straddle_prem_pct ?? 0}
                            onChange={e => setDraftLeg(prev => ({ ...prev, atm_straddle_prem_pct: parseFloat(e.target.value) || 0 }))}
                            className="w-20 h-8 px-2 border border-default rounded text-xs text-center"
                          />
                          <span className="text-xs text-muted whitespace-nowrap">%</span>
                        </div>
                      </>
                    )}

                    {draftLeg.strike_criteria === 'rel_leg' && (
                      <>
                        <label className="field-label">Relative To</label>
                        <div className="flex flex-col gap-0.5">
                          <div className="flex items-center gap-1 h-8">
                            <select
                              value={draftLeg.ref_leg ?? 1}
                              onChange={e => setDraftLeg(prev => {
                                const ref = parseInt(e.target.value, 10) || 1;
                                const next = { ...prev, ref_leg: ref };
                                const parent = legs[ref - 1];
                                if (parent && parent.segment === 'options') {
                                  next.option_type = parent.option_type;
                                  next.position = parent.position === 'sell' ? 'buy' : 'sell';
                                }
                                return next;
                              })}
                              className="h-8 px-2 border border-default rounded text-xs bg-surface font-medium"
                            >
                              {legs.map((_, i) => <option key={i} value={i + 1}>{`Leg ${i + 1}`}</option>)}
                            </select>
                            <span className="text-xs text-muted whitespace-nowrap">offset</span>
                            <input
                              type="number"
                              step="1"
                              value={draftLeg.offset ?? 0}
                              onChange={e => setDraftLeg(prev => ({ ...prev, offset: parseInt(e.target.value, 10) || 0 }))}
                              className="w-16 h-8 px-2 border border-default rounded text-xs text-center"
                            />
                            <span className="text-xs text-muted whitespace-nowrap">gaps</span>
                          </div>
                          <span className="text-xs text-muted">
                            {(() => {
                              const isCE = ['call', 'ce'].includes((draftLeg.option_type || '').toLowerCase());
                              const off = draftLeg.offset ?? 0;
                              const dir = isCE ? (off >= 0 ? '+' : '−') : (off >= 0 ? '−' : '+');
                              return `strike = Leg ${draftLeg.ref_leg ?? 1} ${dir}${Math.abs(off)} gaps (further OTM ⇒ larger offset)`;
                            })()}
                          </span>
                        </div>
                      </>
                    )}

                    {draftLeg.strike_criteria === 'rel_leg_premium' && (
                      <>
                        <label className="field-label">Source Leg</label>
                        <div className="flex flex-col gap-0.5">
                          <div className="flex items-center gap-1 h-8">
                            <select
                              value={draftLeg.ref_leg ?? 1}
                              onChange={e => setDraftLeg(prev => ({ ...prev, ref_leg: parseInt(e.target.value, 10) || 1 }))}
                              className="h-8 px-2 border border-default rounded text-xs bg-surface font-medium"
                            >
                              {legs.map((_, i) => <option key={i} value={i + 1}>{`Leg ${i + 1}`}</option>)}
                            </select>
                          </div>
                          <span className="text-xs text-muted">
                            {`Target premium = Leg ${draftLeg.ref_leg ?? 1} entry premium ÷ expiries in its life, adjusted for lot size`}
                          </span>
                        </div>
                      </>
                    )}
                  </div>
                )}

                {/* Add Leg */}
                <div className="ml-auto">
                  <button type="button" onClick={addLegFromDraft} disabled={legs.length >= 12}
                    className="run-btn add-leg-btn h-9 px-6">
                    <Plus size={13} />
                    Add Leg
                  </button>
                </div>
              </div>
            </div>

            {/* ── Empty state — keeps the right column balanced before any legs exist ── */}
            {legs.length === 0 && (
              <div
                className="bg-surface rounded-lg border border-default shadow-sm flex flex-col items-center justify-center text-center px-6"
                style={{ minHeight: 260, borderStyle: 'dashed' }}
              >
                <span className="leg-index-badge" style={{ width: 42, height: 42, fontSize: '1.1rem', marginBottom: 14 }}>+</span>
                <div style={{ fontFamily: 'Outfit, sans-serif', fontWeight: 600, fontSize: '0.92rem', color: 'var(--text-secondary)' }}>No legs added yet</div>
                <div style={{ fontFamily: 'Outfit, sans-serif', fontSize: '0.74rem', color: 'var(--text-muted)', marginTop: 5, maxWidth: 340, lineHeight: 1.5 }}>
                  Configure your leg above, then click <strong style={{ color: 'var(--accent-2)' }}>Add Leg</strong>. Added legs appear here — then Run Backtest.
                </div>
              </div>
            )}

            {/* ── Added legs list ── */}
            {legs.length > 0 && (
              <div className="bg-surface rounded-lg border border-default shadow-sm">
                <div className="px-4 py-2.5 border-b border-subtle">
                  <h3 className="section-heading">Legs <span style={{ fontWeight: 400, fontSize: '0.55rem', color: 'var(--text-muted)', marginLeft: '4px' }}>({legs.length}/12)</span></h3>
                </div>
                <div className="p-3 space-y-3">
                  {trailSLWarning && (
                    <div className="flex items-start gap-2 px-4 py-2 bg-yellow-50 border border-yellow-300 rounded-lg text-xs text-yellow-800 mb-2">
                      <AlertTriangle size={14} className="text-yellow-600 mt-0.5 flex-shrink-0" />
                      <span className="flex-1">{trailSLWarning}</span>
                      <button
                        onClick={() => setTrailSLWarning(null)}
                        className="text-yellow-500 hover:text-yellow-700 font-bold ml-1"
                      >
                        ×
                      </button>
                    </div>
                  )}
                  {legs.map((leg, idx) => (
                    <div key={leg.id} className="leg-card overflow-hidden">
                      <div className="px-3 py-2 flex items-center justify-between border-b border-subtle"
                        style={{ background: 'var(--bg-elevated)' }}>
                        <div className="flex items-center gap-2">
                          <span className="leg-index-badge">{idx + 1}</span>
                          <span style={{ fontFamily: 'IBM Plex Mono, monospace', fontSize: '0.68rem', fontWeight: 700, color: 'var(--text-primary)', letterSpacing: '0.04em' }}>
                            {leg.segment === 'options'
                              ? `${leg.position === 'sell' ? 'SELL' : 'BUY'} ${leg.option_type === 'call' ? 'CALL' : 'PUT'}`
                              : leg.segment === 'midcap100'
                                ? `${leg.position === 'sell' ? 'SELL' : 'BUY'} MIDCAP100`
                                : 'FUTURE'}
                          </span>
                          {leg.segment !== 'midcap100' && (() => {
                            const legIdx = String(leg.index || instrument).toUpperCase();
                            const isMidcp = legIdx === 'MIDCPNIFTY';
                            return (
                              <span style={{
                                fontFamily: 'IBM Plex Mono, monospace',
                                fontSize: '0.59rem',
                                fontWeight: 700,
                                letterSpacing: '0.05em',
                                padding: '1px 7px',
                                borderRadius: 4,
                                background: isMidcp ? 'rgba(45,212,191,0.12)' : 'var(--accent-bg)',
                                color: isMidcp ? '#2dd4bf' : 'var(--accent)',
                                border: `1px solid ${isMidcp ? 'rgba(45,212,191,0.35)' : 'color-mix(in srgb, var(--accent) 40%, transparent)'}`,
                              }}>
                                {legIdx}
                              </span>
                            );
                          })()}
                          <span style={{ color: 'var(--text-muted)', fontSize: '0.65rem' }}>·</span>
                          <span style={{ fontFamily: 'Outfit, sans-serif', fontSize: '0.68rem', color: 'var(--text-secondary)', textTransform: 'capitalize' }}>{leg.segment === 'midcap100' ? leg.midcap_mode : leg.expiry}</span>
                        </div>
                        <div className="flex items-center gap-3">
                          <span style={{ fontFamily: 'IBM Plex Mono, monospace', fontSize: '0.65rem', fontWeight: 700, color: 'var(--accent)' }}>{leg.lot * getLotSize(leg.index || instrument, startDate)} units</span>
                          <button onClick={() => removeLeg(leg.id)} className="p-1 rounded transition-colors"
                            style={{ color: 'var(--text-muted)' }}
                            onMouseEnter={e => { e.currentTarget.style.color = 'var(--loss)'; e.currentTarget.style.background = 'var(--loss-bg)'; }}
                            onMouseLeave={e => { e.currentTarget.style.color = 'var(--text-muted)'; e.currentTarget.style.background = 'transparent'; }}>
                            <Trash2 size={13} />
                          </button>
                        </div>
                      </div>

                      <div className="p-3 space-y-3">
                        {leg.strike_criteria === 'straddle_width' && (
                          <div className="text-xs text-muted px-1">
                            Straddle Width: ATM {leg.straddle_direction ?? '+'} ({leg.straddle_multiplier ?? 0.5} × Straddle)
                          </div>
                        )}
                        {/* Basic fields */}
                        <div className="flex flex-wrap items-end gap-3">
                          {leg.segment !== 'midcap100' && (
                            <div>
                              <label className="field-label">Instrument</label>
                              <SegBtn size="sm"
                                options={[{ value: 'options', label: 'Options' }, { value: 'futures', label: 'Futures' }]}
                                value={leg.segment}
                                onChange={v => {
                                  updateLeg(leg.id, 'segment', v);
                                  if (v === 'futures' && leg.expiry === 'weekly') {
                                    updateLeg(leg.id, 'expiry', 'monthly');
                                  }
                                }}
                              />
                            </div>
                          )}
                          <div>
                            <label className="field-label">Lots</label>
                            <input type="number" min={1} value={leg.lot}
                              onChange={e => updateLeg(leg.id, 'lot', parseInt(e.target.value) || 1)}
                              className="w-16 h-7 px-2 border border-default rounded text-xs text-center bg-surface" />
                          </div>
                          <div>
                            <label className="field-label">Quantity</label>
                            <input type="number" min={0} value={leg.qty ?? ''}
                              placeholder="auto"
                              title="Per-leg quantity — direct P&L multiplier for this leg. Blank = lots × index lot size (default)."
                              onChange={e => updateLeg(leg.id, 'qty', e.target.value === '' ? '' : (parseInt(e.target.value) || 0))}
                              className="w-16 h-7 px-2 border border-default rounded text-xs text-center bg-surface" />
                          </div>
                          <div>
                            <label className="field-label">Position</label>
                            <div className="flex items-center gap-2">
                              <SegBtn size="sm"
                                options={[{ value: 'buy', label: 'Buy' }, { value: 'sell', label: 'Sell' }]}
                                value={leg.position} onChange={v => updateLeg(leg.id, 'position', v)} />
                              {strFilter.enabled && (
                                <span className="px-2 py-0.5 rounded-full text-[10px] font-semibold" style={{ background: 'var(--accent-bg)', color: 'var(--accent)', border: '1px solid var(--accent)', opacity: 0.85 }}>
                                  STR
                                </span>
                              )}
                            </div>
                          </div>
                          {leg.segment === 'options' && (
                            <div>
                              <label className="field-label">Option Type</label>
                              <SegBtn size="sm"
                                options={[{ value: 'call', label: 'Call' }, { value: 'put', label: 'Put' }]}
                                value={leg.option_type} onChange={v => updateLeg(leg.id, 'option_type', v)} />
                            </div>
                          )}
                          {leg.segment !== 'midcap100' && (
                            <div>
                              <label className="field-label">Expiry</label>
                              <select value={leg.expiry} onChange={e => updateLeg(leg.id, 'expiry', e.target.value)}
                                className="h-7 px-2 border border-default rounded text-xs bg-surface w-28">
                                {(leg.segment === 'options' ? getOptionExpiryOptions(leg.index || instrument, expiryBasis) : FUTURES_EXPIRIES).map(opt => (
                                  <option key={opt.value} value={opt.value}>{opt.label}</option>
                                ))}
                              </select>
                            </div>
                          )}
                          {leg.segment === 'midcap100' && (
                            <>
                              <div>
                                <label className="field-label">Pricing</label>
                                <SegBtn size="sm"
                                  options={[{ value: 'spot', label: 'Spot' }, { value: 'hypothetical', label: 'Hypothetical' }]}
                                  value={leg.midcap_mode || 'hypothetical'}
                                  onChange={v => updateLeg(leg.id, 'midcap_mode', v)} />
                              </div>
                              {(leg.midcap_mode || 'hypothetical') === 'hypothetical' && (
                                <div>
                                  <label className="field-label">Cost % / month</label>
                                  <input type="number" min={0} step="0.05" value={leg.cost_pct_per_month ?? 0.5}
                                    onChange={e => updateLeg(leg.id, 'cost_pct_per_month', Math.max(0, parseFloat(e.target.value) || 0))}
                                    className="w-20 h-7 px-2 border border-default rounded text-xs text-center bg-surface" />
                                </div>
                              )}
                            </>
                          )}
                          {leg.segment === 'options' && (
                            <>
                              <div>
                                <label className="field-label">Strike Criteria</label>
                                <select value={leg.strike_criteria} onChange={e => updateLeg(leg.id, 'strike_criteria', e.target.value)}
                                  className="h-7 px-2 border border-default rounded text-xs bg-surface text-secondary w-36">
                                  <option value="strike_type">Strike Type</option>
                                  <option value="premium_range">Premium Range</option>
                                  <option value="closest_premium">Closest Premium</option>
                                  <option value="premium_gte">Premium &gt;=</option>
                                  <option value="premium_lte">Premium &lt;=</option>
                                  <option value="time_value">Time Value (nearest)</option>
                                  <option value="time_value_gte">Time Value &gt;=</option>
                                  <option value="time_value_lte">Time Value &lt;=</option>
                                  <option value="straddle_width">Straddle Width</option>
                                  <option value="pct_of_atm">% of ATM</option>
                                  <option value="synthetic_future">Synthetic Future</option>
                                  <option value="atm_straddle_prem_pct">ATM Straddle Premium %</option>
                                  {idx > 0 && <option value="rel_leg">Relative to Leg</option>}
                                  {idx > 0 && <option value="rel_leg_premium">Relative to Leg Premium</option>}
                                </select>
                                {leg.strike_criteria === 'straddle_width' && (
                                  <div className="flex items-center gap-1 mt-2 text-xs text-secondary">
                                    <span className="text-xs text-muted whitespace-nowrap">ATM Strike</span>
                                    <select
                                      value={leg.straddle_direction ?? '+'}
                                      onChange={e => updateLeg(leg.id, 'straddle_direction', e.target.value)}
                                      className="h-7 px-2 border border-default rounded text-xs bg-surface"
                                    >
                                      <option value="+">+</option>
                                      <option value="-">-</option>
                                    </select>
                                    <span className="text-xs text-muted">(</span>
                                    <input
                                      type="number"
                                      value={leg.straddle_multiplier ?? 0.5}
                                      onChange={e => updateLeg(leg.id, 'straddle_multiplier', parseFloat(e.target.value) || 0)}
                                      step="0.1"
                                      min="0"
                                      max="10"
                                      className="w-16 h-7 px-2 border border-default rounded text-xs text-center"
                                    />
                                    <span className="text-xs text-muted whitespace-nowrap">× ATM Straddle Price )</span>
                                  </div>
                                )}
                              </div>
                              {backtestMode === 'eod' && !(String(leg.expiry || '') === 'yearly' && leg.yearly_schedule_enabled) && (
                                <div>
                                  <label className="field-label">Strike Gap</label>
                                  <StrikeIntervalSelect
                                    value={leg.strike_interval}
                                    onChange={value => updateLeg(leg.id, 'strike_interval', value)}
                                    index={leg.index || instrument}
                                    className="h-7 px-2 border border-default rounded text-xs bg-surface w-20"
                                  />
                                </div>
                              )}
                              {leg.strike_criteria !== 'straddle_width' && (
                                <div>
                                  {(leg.strike_criteria === 'strike_type' || leg.strike_criteria === 'synthetic_future') && (
                                    <>
                                      <label className="field-label">Strike Type</label>
                                      <select value={leg.strike_type} onChange={e => updateLeg(leg.id, 'strike_type', e.target.value)}
                                        className="h-7 px-2 border border-default rounded text-xs bg-surface w-24">
                                        {strikeTypeOpts.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                                      </select>
                                    </>
                                  )}

                                  {leg.strike_criteria === 'premium_range' && (
                                    <div className="flex gap-2">
                                      <div>
                                        <label className="field-label">Lower Range</label>
                                        <input type="number" min={0} placeholder="Lower" value={leg.premium_min || ''}
                                          onChange={e => updateLeg(leg.id, 'premium_min', +e.target.value)}
                                          className="w-16 h-7 px-1 border border-default rounded text-xs text-center" />
                                      </div>
                                      <div>
                                        <label className="field-label">Upper Range</label>
                                        <input type="number" min={0} placeholder="Upper" value={leg.premium_max || ''}
                                          onChange={e => updateLeg(leg.id, 'premium_max', +e.target.value)}
                                          className="w-16 h-7 px-1 border border-default rounded text-xs text-center" />
                                      </div>
                                    </div>
                                  )}

                                  {(['closest_premium','premium_gte','premium_lte','time_value','time_value_gte','time_value_lte'].includes(leg.strike_criteria)) && (
                                    <>
                                      <label className="field-label">{String(leg.strike_criteria || '').startsWith('time_value') ? 'Time Value' : 'Premium'}</label>
                                      <input type="number" min={0} placeholder={String(leg.strike_criteria || '').startsWith('time_value') ? 'Time Value' : 'Premium'} value={leg.premium_value ?? ''}
                                        onChange={e => updateLeg(leg.id, 'premium_value', e.target.value === '' ? null : +e.target.value)}
                                        className="w-20 h-7 px-1 border border-default rounded text-xs text-center" />
                                      {String(leg.strike_criteria || '').startsWith('time_value') && (
                                        <>
                                          <select value={leg.tv_moneyness || 'ATM'}
                                            onChange={e => updateLeg(leg.id, 'tv_moneyness', e.target.value)}
                                            className="h-7 px-1 border border-default rounded text-xs bg-surface">
                                            <option value="ATM">ATM (both sides)</option>
                                            <option value="OTM">OTM only</option>
                                            <option value="ITM">ITM only</option>
                                          </select>
                                          <label className="field-label">Range %</label>
                                          <input type="number" min={0} step="0.5" placeholder="all"
                                            value={leg.tv_range_pct ?? ''}
                                            onChange={e => updateLeg(leg.id, 'tv_range_pct', e.target.value === '' ? null : +e.target.value)}
                                            className="w-16 h-7 px-1 border border-default rounded text-xs text-center" />
                                            <select value={leg.tv_units || 'points'}
                                              onChange={e => updateLeg(leg.id, 'tv_units', e.target.value)}
                                              className="h-7 px-1 border border-default rounded text-xs bg-surface">
                                              <option value="points">pts</option>
                                              <option value="percent">%</option>
                                            </select>
                                        </>
                                      )}
                                    </>
                                  )}

                                  {leg.strike_criteria === 'pct_of_atm' && (
                                    <>
                                      <label className="field-label">&nbsp;</label>
                                      <div className="flex flex-col gap-0.5">
                                        <div className="flex items-center gap-1">
                                          <select
                                            value={leg.pct_atm_moneyness ?? 'OTM'}
                                            onChange={e => updateLeg(leg.id, 'pct_atm_moneyness', e.target.value)}
                                            className="h-7 px-2 border border-default rounded text-xs bg-surface font-medium"
                                          >
                                            <option value="OTM">OTM</option>
                                            <option value="ITM">ITM</option>
                                          </select>
                                          <input
                                            type="number"
                                            min="0"
                                            step="0.1"
                                            value={leg.pct_value ?? 0}
                                            onChange={e => updateLeg(leg.id, 'pct_value', parseFloat(e.target.value) || 0)}
                                            className="w-14 h-7 px-1 border border-default rounded text-xs text-center"
                                          />
                                          <span className="text-xs text-muted whitespace-nowrap">% of ATM</span>
                                        </div>
                                        <span className="text-xs text-muted">
                                          {(() => {
                                            const isCE = ['call', 'ce'].includes((leg.option_type || '').toLowerCase());
                                            const m = leg.pct_atm_moneyness ?? 'OTM';
                                            const up = isCE ? m === 'OTM' : m === 'ITM';
                                            return `ATM ${up ? '+' : '−'}${leg.pct_value ?? 0}%`;
                                          })()}
                                        </span>
                                      </div>
                                    </>
                                  )}

                                  {leg.strike_criteria === 'atm_straddle_prem_pct' && (
                                    <>
                                      <label className="field-label">&nbsp;</label>
                                      <div className="flex items-center gap-1">
                                        <span className="text-xs text-muted whitespace-nowrap">ATM Straddle Premium %</span>
                                        <input
                                          type="number"
                                          min="0"
                                          step="0.1"
                                          value={leg.atm_straddle_prem_pct ?? 0}
                                          onChange={e => updateLeg(leg.id, 'atm_straddle_prem_pct', parseFloat(e.target.value) || 0)}
                                          className="w-14 h-7 px-1 border border-default rounded text-xs text-center"
                                        />
                                        <span className="text-xs text-muted whitespace-nowrap">%</span>
                                      </div>
                                    </>
                                  )}

                                  {leg.strike_criteria === 'rel_leg' && (
                                    <>
                                      <label className="field-label">Relative To</label>
                                      <div className="flex flex-col gap-0.5">
                                        <div className="flex items-center gap-1">
                                          <select
                                            value={leg.ref_leg ?? 1}
                                            onChange={e => updateLeg(leg.id, 'ref_leg', parseInt(e.target.value, 10) || 1)}
                                            className="h-7 px-2 border border-default rounded text-xs bg-surface font-medium"
                                          >
                                            {legs.slice(0, idx).map((_, i) => <option key={i} value={i + 1}>{`Leg ${i + 1}`}</option>)}
                                          </select>
                                          <span className="text-xs text-muted whitespace-nowrap">offset</span>
                                          <input
                                            type="number"
                                            step="1"
                                            value={leg.offset ?? 0}
                                            onChange={e => updateLeg(leg.id, 'offset', parseInt(e.target.value, 10) || 0)}
                                            className="w-14 h-7 px-1 border border-default rounded text-xs text-center"
                                          />
                                          <span className="text-xs text-muted whitespace-nowrap">gaps</span>
                                        </div>
                                        <span className="text-xs text-muted">
                                          {(() => {
                                            const isCE = ['call', 'ce'].includes((leg.option_type || '').toLowerCase());
                                            const off = leg.offset ?? 0;
                                            const dir = isCE ? (off >= 0 ? '+' : '−') : (off >= 0 ? '−' : '+');
                                            return `Leg ${leg.ref_leg ?? 1} ${dir}${Math.abs(off)} gaps`;
                                          })()}
                                        </span>
                                      </div>
                                    </>
                                  )}

                                  {leg.strike_criteria === 'rel_leg_premium' && (
                                    <>
                                      <label className="field-label">Source Leg</label>
                                      <div className="flex flex-col gap-0.5">
                                        <div className="flex items-center gap-1">
                                          <select
                                            value={leg.ref_leg ?? 1}
                                            onChange={e => updateLeg(leg.id, 'ref_leg', parseInt(e.target.value, 10) || 1)}
                                            className="h-7 px-2 border border-default rounded text-xs bg-surface font-medium"
                                          >
                                            {legs.slice(0, idx).map((_, i) => <option key={i} value={i + 1}>{`Leg ${i + 1}`}</option>)}
                                          </select>
                                        </div>
                                        <span className="text-xs text-muted">
                                          {`Leg ${leg.ref_leg ?? 1} premium ÷ expiries in its life`}
                                        </span>
                                      </div>
                                    </>
                                  )}
                                </div>
                              )}
                            </>
                          )}
                        </div>

                        {/* Strike on Rollover — shown when rollover or no-rollover is active */}
                        {leg.segment === 'options' && (rolloverToggle || noRollover) && (
                          <div className="flex items-center gap-2 pt-1">
                            <span className="text-xs text-muted whitespace-nowrap">Strike on Roll</span>
                            {['fresh', 'fixed'].map(mode => (
                              <button
                                key={mode}
                                type="button"
                                onClick={() => updateLeg(leg.id, 'rollover_strike_mode', mode)}
                                className={`px-3 py-1 rounded-lg text-xs font-medium border transition-colors ${
                                  (leg.rollover_strike_mode || 'fresh') === mode
                                    ? 'bg-accent text-white border-accent'
                                    : 'border-default text-secondary hover:bg-hover'
                                }`}
                              >
                                {mode === 'fresh' ? 'Fresh' : 'Fixed'}
                              </button>
                            ))}
                            <span className="text-[10px] text-muted">
                              {(leg.rollover_strike_mode || 'fresh') === 'fixed'
                                ? 'Keep same strike across expiries'
                                : 'Re-select strike each expiry'}
                            </span>
                          </div>
                        )}

                        {/* Per-leg Spot Adjustment — only meaningful while the
                            strategy-level Spot Adjustment section is in play.
                            OFF leaves the leg on the strategy-level settings.
                            Hidden for a yearly leg when its Per-Contract Schedule
                            is on — the schedule table is the single source. */}
                        {leg.segment === 'options' && !(String(leg.expiry || '') === 'yearly' && leg.yearly_schedule_enabled) && (
                          <div className="flex flex-wrap items-center gap-2 pt-1">
                            <Toggle
                              enabled={Boolean(leg.spot_adj_enabled)}
                              onToggle={(val) => updateLeg(leg.id, 'spot_adj_enabled', val !== undefined ? Boolean(val) : !leg.spot_adj_enabled)}
                              size="sm"
                            />
                            <span className="text-xs font-medium text-secondary whitespace-nowrap">Own Spot Adj</span>
                            {leg.spot_adj_enabled && (
                              <>
                                <input
                                  type="number"
                                  min={leg.spot_adj_units === 'points' ? 0 : 0.25}
                                  step={leg.spot_adj_units === 'points' ? 25 : 0.25}
                                  value={leg.spot_adj_value ?? 2}
                                  onChange={e => updateLeg(leg.id, 'spot_adj_value', parseFloat(e.target.value) || 0)}
                                  className="w-20 h-7 px-2 border border-default rounded text-xs text-center bg-surface"
                                />
                                <SegBtn
                                  size="sm"
                                  options={[{ value: 'percent', label: '%' }, { value: 'points', label: 'Points' }]}
                                  value={leg.spot_adj_units || 'percent'}
                                  onChange={v => updateLeg(leg.id, 'spot_adj_units', v)}
                                />
                                <SegBtn
                                  size="sm"
                                  options={[
                                    { value: 'rise', label: 'Rise' },
                                    { value: 'fall', label: 'Fall' },
                                    { value: 'both', label: 'Both' },
                                  ]}
                                  value={leg.spot_adj_direction || 'rise'}
                                  onChange={v => updateLeg(leg.id, 'spot_adj_direction', v)}
                                />
                                <span className="text-[10px] text-muted">
                                  measured from this trade’s entry spot
                                </span>
                              </>
                            )}
                            {!leg.spot_adj_enabled && (
                              <span className="text-[10px] text-muted">Uses strategy-level Spot Adjustment</span>
                            )}
                          </div>
                        )}

                        {/* Per-contract schedule — only on yearly legs, behind an opt-in toggle */}
                        {leg.segment === 'options' && String(leg.expiry || '') === 'yearly' && (
                          <div className="space-y-1.5">
                            <div className="flex items-center gap-2 pt-1">
                              <Toggle
                                enabled={Boolean(leg.yearly_schedule_enabled)}
                                onToggle={(val) => updateLeg(leg.id, 'yearly_schedule_enabled', val !== undefined ? Boolean(val) : !leg.yearly_schedule_enabled)}
                                size="sm"
                              />
                              <span className="text-xs font-medium text-secondary whitespace-nowrap">Per-Contract Schedule</span>
                              {!leg.yearly_schedule_enabled && (
                                <span className="text-[10px] text-muted">off — leg uses its single strike gap &amp; spot-adj for all contracts</span>
                              )}
                            </div>
                            {leg.yearly_schedule_enabled && (
                              <div className="rounded-lg border border-default bg-hover/40 px-3 py-2 space-y-1.5">
                                <div className="flex items-center justify-between gap-2">
                                  <div className="flex items-center gap-1.5">
                                    <span className="text-[10px] text-muted">Adjust on</span>
                                    <select
                                      value={leg.yearly_schedule_direction || 'both'}
                                      onChange={e => updateLeg(leg.id, 'yearly_schedule_direction', e.target.value)}
                                      className="h-7 px-1 border border-default rounded text-xs bg-surface"
                                      title="Spot-adjustment direction (applies to all rows)"
                                    >
                                      <option value="rise">Rise</option>
                                      <option value="fall">Fall</option>
                                      <option value="both">Both</option>
                                      <option value="none">None (gap only)</option>
                                    </select>
                                  </div>
                                  <button
                                    type="button"
                                    onClick={() => updateLeg(leg.id, 'yearly_contract_schedule', [...(leg.yearly_contract_schedule || []), { year: '', gap: '', adj: '', adj_unit: 'points' }])}
                                    className="flex items-center gap-1 px-2 py-0.5 text-xs font-medium text-accent border border-accent rounded hover:bg-accent hover:text-white transition-colors"
                                  >
                                    <Plus size={11} /> Add
                                  </button>
                                </div>
                                <div className="rounded bg-surface/60 border border-subtle px-2 py-1.5 text-[10px] leading-relaxed text-muted">
                                  <span className="font-semibold text-secondary">How to set it:</span> each row is one <span className="text-secondary">year range</span> — type the <span className="text-secondary">From</span> and <span className="text-secondary">To</span> December years (leave <span className="text-secondary">To</span> blank = onward), then its <span className="text-secondary">Strike Gap</span> and <span className="text-secondary">Spot Adj</span> + <span className="text-secondary">pt/%</span> unit (like Own Spot Adj).
                                  Click <span className="text-secondary">+ Add</span> for the next range and keep them back-to-back (next From = previous To + 1). <span className="text-secondary">Adjust on</span> (Rise/Fall/Both) applies to every range.
                                  <br />e.g. <span className="text-secondary">2019–2022 → gap 500, adj 200 pt</span>; <span className="text-secondary">2023–onward → gap 1000, adj 1000 pt</span>.
                                </div>
                                {(leg.yearly_contract_schedule || []).length === 0 && (
                                  <p className="text-[11px] text-muted">No ranges yet — click <span className="font-medium">+ Add</span> to create your first one.</p>
                                )}
                                {(leg.yearly_contract_schedule || []).length > 0 && (
                                  <div className="space-y-1">
                                    <div className={`grid gap-1.5 text-[10px] text-muted font-medium px-0.5 ${leg.yearly_schedule_direction === 'none' ? 'grid-cols-[64px_64px_1fr_24px]' : 'grid-cols-[64px_64px_1fr_1fr_50px_24px]'}`}>
                                      <span>From&nbsp;yr</span><span>To&nbsp;yr</span><span>Strike Gap</span>
                                      {leg.yearly_schedule_direction !== 'none' && (<><span>Spot Adj</span><span>Unit</span></>)}
                                      <span />
                                    </div>
                                    {(leg.yearly_contract_schedule || []).map((row, ri) => (
                                      <div key={ri} className={`grid gap-1.5 items-center ${leg.yearly_schedule_direction === 'none' ? 'grid-cols-[64px_64px_1fr_24px]' : 'grid-cols-[64px_64px_1fr_1fr_50px_24px]'}`}>
                                        <input
                                          type="number"
                                          placeholder="yr"
                                          min={2010} max={2040}
                                          value={row.year === 'start' ? '' : (row.year ?? '')}
                                          onChange={e => {
                                            const next = [...(leg.yearly_contract_schedule || [])];
                                            next[ri] = { ...next[ri], year: e.target.value };
                                            updateLeg(leg.id, 'yearly_contract_schedule', next);
                                          }}
                                          className="h-7 px-1.5 border border-default rounded text-xs text-center bg-surface w-full placeholder:text-muted/50"
                                        />
                                        <input
                                          type="number"
                                          placeholder="onward"
                                          min={2010} max={2040}
                                          value={row.to ?? ''}
                                          onChange={e => {
                                            const next = [...(leg.yearly_contract_schedule || [])];
                                            next[ri] = { ...next[ri], to: e.target.value };
                                            updateLeg(leg.id, 'yearly_contract_schedule', next);
                                          }}
                                          title="End year (leave blank = onward)"
                                          className="h-7 px-1.5 border border-default rounded text-xs text-center bg-surface w-full placeholder:text-muted/60"
                                        />
                                        <select
                                          value={row.gap ?? ''}
                                          onChange={e => {
                                            const next = [...(leg.yearly_contract_schedule || [])];
                                            next[ri] = { ...next[ri], gap: e.target.value };
                                            updateLeg(leg.id, 'yearly_contract_schedule', next);
                                          }}
                                          className="h-7 px-2 border border-default rounded text-xs text-center bg-surface w-full"
                                          title="Strike gap for this range"
                                        >
                                          <option value="">Gap</option>
                                          {strikeIntervalOptionsForIndex(leg.index || instrument).map(g => <option key={g} value={g}>{g}</option>)}
                                        </select>
                                        {leg.yearly_schedule_direction !== 'none' && (<>
                                        <input
                                          type="number"
                                          placeholder="Adj"
                                          value={row.adj ?? ''}
                                          onChange={e => {
                                            const next = [...(leg.yearly_contract_schedule || [])];
                                            next[ri] = { ...next[ri], adj: e.target.value };
                                            updateLeg(leg.id, 'yearly_contract_schedule', next);
                                          }}
                                          className="h-7 px-2 border border-default rounded text-xs text-center bg-surface w-full placeholder:text-muted/50"
                                        />
                                        <select
                                          value={row.adj_unit || 'points'}
                                          onChange={e => {
                                            const next = [...(leg.yearly_contract_schedule || [])];
                                            next[ri] = { ...next[ri], adj_unit: e.target.value };
                                            updateLeg(leg.id, 'yearly_contract_schedule', next);
                                          }}
                                          className="h-7 px-1 border border-default rounded text-xs bg-surface w-full"
                                          title="Spot-adjustment unit"
                                        >
                                          <option value="points">pt</option>
                                          <option value="percent">%</option>
                                        </select>
                                        </>)}
                                        <button
                                          type="button"
                                          onClick={() => {
                                            const next = [...(leg.yearly_contract_schedule || [])];
                                            next.splice(ri, 1);
                                            updateLeg(leg.id, 'yearly_contract_schedule', next);
                                          }}
                                          className="flex items-center justify-center h-7 w-6 text-muted hover:text-loss transition-colors"
                                          title="Remove range"
                                        >
                                          <Trash2 size={12} />
                                        </button>
                                      </div>
                                    ))}
                                  </div>
                                )}
                              </div>
                            )}
                          </div>
                        )}

                        {/* Advanced controls */}
                        <div className="pt-2 border-t border-subtle space-y-2">
                          <div className="flex flex-wrap gap-x-4 gap-y-2">
                            <div className="flex items-center gap-2">
                              <Toggle enabled={leg.target_enabled} onToggle={(val) => updateLeg(leg.id, 'target_enabled', val !== undefined ? Boolean(val) : !leg.target_enabled)} size="sm" />
                              <span className="text-xs font-medium text-secondary whitespace-nowrap">Target Profit</span>
                              {leg.target_enabled && (<>
                                <select value={leg.target_mode} onChange={e => updateLeg(leg.id, 'target_mode', e.target.value)} className="h-7 px-1 border border-default rounded text-xs bg-surface">
                                  <option value="POINTS">Points (Pts)</option>
                                  <option value="UNDERLYING_POINTS">Underlying Pts</option>
                                  <option value="PERCENT">Percent (%)</option>
                                  <option value="UNDERLYING_PERCENT">Underlying %</option>
                                </select>
                                <input type="number" min={0} value={leg.target_value ?? ''} onChange={e => updateLeg(leg.id, 'target_value', e.target.value === '' ? null : +e.target.value)} className="w-14 h-7 px-1 border border-default rounded text-xs text-center" />
                              </>)}
                            </div>
                            <div className="flex items-center gap-2">
                              <Toggle enabled={leg.stop_loss_enabled} onToggle={(val) => { const nv = val !== undefined ? Boolean(val) : !leg.stop_loss_enabled; updateLeg(leg.id, 'stop_loss_enabled', nv); if (nv) updateLeg(leg.id, 'sl_buffer_enabled', false); }} size="sm" />
                              <span className="text-xs font-medium text-secondary whitespace-nowrap">Stop Loss</span>
                              {leg.stop_loss_enabled && (<>
                                <select value={leg.stop_loss_mode} onChange={e => updateLeg(leg.id, 'stop_loss_mode', e.target.value)} className="h-7 px-1 border border-default rounded text-xs bg-surface">
                                  <option value="POINTS">Points (Pts)</option>
                                  <option value="UNDERLYING_POINTS">Underlying Pts</option>
                                  <option value="PERCENT">Percent (%)</option>
                                  <option value="UNDERLYING_PERCENT">Underlying %</option>
                                </select>
                                <input type="number" min={0} value={leg.stop_loss_value ?? ''} onChange={e => updateLeg(leg.id, 'stop_loss_value', e.target.value === '' ? null : +e.target.value)} className="w-14 h-7 px-1 border border-default rounded text-xs text-center" />
                              </>)}
                            </div>
                            <div className="flex items-center gap-2">
                              <Toggle enabled={leg.sl_buffer_enabled} onToggle={(val) => { const nv = val !== undefined ? Boolean(val) : !leg.sl_buffer_enabled; updateLeg(leg.id, 'sl_buffer_enabled', nv); if (nv) updateLeg(leg.id, 'stop_loss_enabled', false); }} size="sm" />
                              <span className="text-xs font-medium text-secondary whitespace-nowrap">SL with Buffer</span>
                              {leg.sl_buffer_enabled && (<>
                                <select value={leg.sl_buffer_mode} onChange={e => updateLeg(leg.id, 'sl_buffer_mode', e.target.value)} className="h-7 px-1 border border-default rounded text-xs bg-surface">
                                  <option value="POINTS">Points (Pts)</option>
                                  <option value="UNDERLYING_POINTS">Underlying Pts</option>
                                  <option value="PERCENT">Percent (%)</option>
                                  <option value="UNDERLYING_PERCENT">Underlying %</option>
                                </select>
                                <input type="number" min={0} value={leg.sl_buffer_value ?? ''} onChange={e => updateLeg(leg.id, 'sl_buffer_value', e.target.value === '' ? null : +e.target.value)} className="w-14 h-7 px-1 border border-default rounded text-xs text-center" />
                                <span className="text-xs text-secondary whitespace-nowrap">Buf%</span>
                                <input type="number" min={0} max={100} value={leg.sl_buffer_pct ?? ''} onChange={e => updateLeg(leg.id, 'sl_buffer_pct', e.target.value === '' ? null : +e.target.value)} className="w-12 h-7 px-1 border border-default rounded text-xs text-center" />
                              </>)}
                            </div>
                            <div className="flex items-center gap-2">
                              <Toggle enabled={leg.trail_sl_enabled} onToggle={(val) => updateLeg(leg.id, 'trail_sl_enabled', val !== undefined ? Boolean(val) : !leg.trail_sl_enabled)} size="sm" />
                              <span className="text-xs font-medium text-secondary whitespace-nowrap">Trail SL</span>
                              <Tooltip text="For every X profit, trail SL by Y." />
                              {leg.trail_sl_enabled && (<>
                                <select value={leg.trail_sl_mode} onChange={e => updateLeg(leg.id, 'trail_sl_mode', e.target.value)} className="w-16 h-7 px-1 border border-default rounded text-xs bg-surface">
                                  <option value="POINTS">Points</option>
                                  <option value="PERCENT">Percent</option>
                                </select>
                                <input type="number" min={0} placeholder="X" value={leg.trail_sl_trigger ?? ''} onChange={e => updateLeg(leg.id, 'trail_sl_trigger', e.target.value === '' ? null : +e.target.value)} className="w-12 h-7 px-1 border border-default rounded text-xs text-center" />
                                <input type="number" min={0} placeholder="Y" value={leg.trail_sl_move ?? ''} onChange={e => updateLeg(leg.id, 'trail_sl_move', e.target.value === '' ? null : +e.target.value)} className="w-12 h-7 px-1 border border-default rounded text-xs text-center" />
                              </>)}
                            </div>
                          </div>
                          <div className="flex flex-wrap gap-x-4 gap-y-2">
                            <div className="flex items-center gap-2">
                              <Toggle enabled={leg.re_entry_target_enabled} onToggle={(val) => updateLeg(leg.id, 're_entry_target_enabled', val !== undefined ? Boolean(val) : !leg.re_entry_target_enabled)} size="sm" />
                              <span className="text-xs font-medium text-secondary whitespace-nowrap">Re-entry on Tgt</span>
                              {leg.re_entry_target_enabled && (<>
                                <select value={leg.re_entry_target_mode} onChange={e => handleReEntryModeSelect(leg.id, 'target', e.target.value)} className="h-7 px-1 border border-default rounded text-xs bg-surface">
                                  <option value="RE_ASAP">RE ASAP</option>
                                  <option value="RE_ASAP_REV">RE ASAP &#8629;</option>
                                  <option value="RE_MOMENTUM">RE MOMENTUM</option>
                                  <option value="RE_MOMENTUM_REV">RE MOMENTUM &#8629;</option>
                                  <option value="LAZY_LEG">Lazy Leg</option>
                                </select>
                                <select value={leg.re_entry_target_count} onChange={e => updateLeg(leg.id, 're_entry_target_count', +e.target.value)} className="w-10 h-7 px-1 border border-default rounded text-xs bg-surface">
                                  {Array.from({ length: 20 }, (_, i) => i + 1).map(n => <option key={n} value={n}>{n}</option>)}
                                </select>
                              </>)}
                              {leg.re_entry_target_enabled && leg.re_entry_target_mode === 'LAZY_LEG' && (
                                <div className="flex items-center gap-1">
                                  <button
                                    type="button"
                                    onClick={() => openLazyLegModal(leg.id, 'target')}
                                    className="text-xs px-2 py-0.5 rounded border border-accent text-accent hover:bg-accent hover:text-white transition-colors"
                                  >
                                    {leg.lazy_leg_target_id
                                      ? `Edit: ${lazyLegs[leg.lazy_leg_target_id]?.name || 'lazy leg'}`
                                      : '+ Configure Lazy Leg'}
                                  </button>
                                  {leg.lazy_leg_target_id && (
                                    <span className="text-xs text-secondary">
                                      ({lazyLegs[leg.lazy_leg_target_id]?.option_type?.toUpperCase()} {lazyLegs[leg.lazy_leg_target_id]?.strike_type?.toUpperCase()})
                                    </span>
                                  )}
                                </div>
                              )}
                            </div>
                            <div className="flex items-center gap-2">
                              <Toggle enabled={leg.re_entry_sl_enabled} onToggle={(val) => updateLeg(leg.id, 're_entry_sl_enabled', val !== undefined ? Boolean(val) : !leg.re_entry_sl_enabled)} size="sm" />
                              <span className="text-xs font-medium text-secondary whitespace-nowrap">Re-entry on SL</span>
                              {leg.re_entry_sl_enabled && (<>
                                <select value={leg.re_entry_sl_mode} onChange={e => handleReEntryModeSelect(leg.id, 'sl', e.target.value)} className="h-7 px-1 border border-default rounded text-xs bg-surface">
                                  <option value="RE_ASAP">RE ASAP</option>
                                  <option value="RE_ASAP_REV">RE ASAP &#8629;</option>
                                  <option value="RE_MOMENTUM">RE MOMENTUM</option>
                                  <option value="RE_MOMENTUM_REV">RE MOMENTUM &#8629;</option>
                                  <option value="LAZY_LEG">Lazy Leg</option>
                                </select>
                                <select value={leg.re_entry_sl_count} onChange={e => updateLeg(leg.id, 're_entry_sl_count', +e.target.value)} className="w-10 h-7 px-1 border border-default rounded text-xs bg-surface">
                                  {Array.from({ length: 20 }, (_, i) => i + 1).map(n => <option key={n} value={n}>{n}</option>)}
                                </select>
                              </>)}
                              {leg.re_entry_sl_enabled && leg.re_entry_sl_mode === 'LAZY_LEG' && (
                                <div className="flex items-center gap-1">
                                  <button
                                    type="button"
                                    onClick={() => openLazyLegModal(leg.id, 'sl')}
                                    className="text-xs px-2 py-0.5 rounded border border-accent text-accent hover:bg-accent hover:text-white transition-colors"
                                  >
                                    {leg.lazy_leg_sl_id
                                      ? `Edit: ${lazyLegs[leg.lazy_leg_sl_id]?.name || 'lazy leg'}`
                                      : '+ Configure Lazy Leg'}
                                  </button>
                                  {leg.lazy_leg_sl_id && (
                                    <span className="text-xs text-secondary">
                                      ({lazyLegs[leg.lazy_leg_sl_id]?.option_type?.toUpperCase()} {lazyLegs[leg.lazy_leg_sl_id]?.strike_type?.toUpperCase()})
                                    </span>
                                  )}
                                </div>
                              )}
                            </div>
                          </div>
                          <div className="flex flex-wrap gap-x-4 gap-y-2">
                            <div className="flex items-center gap-2">
                              <Toggle
                                enabled={Boolean(leg.slippage_enabled)}
                                onToggle={(val) => updateLeg(leg.id, 'slippage_enabled', val !== undefined ? Boolean(val) : !leg.slippage_enabled)}
                                size="sm"
                              />
                              <span className="text-xs font-medium text-secondary whitespace-nowrap">Slippage %</span>
                              {leg.slippage_enabled && (<>
                                <input
                                  type="number"
                                  min={0}
                                  step={0.01}
                                  value={leg.slippage_pct ?? 0}
                                  onChange={e => updateLeg(leg.id, 'slippage_pct', e.target.value === '' ? '' : Math.max(0, Number(e.target.value)))}
                                  onBlur={() => updateLeg(leg.id, 'slippage_pct', Math.max(0, Number(leg.slippage_pct) || 0))}
                                  className="w-16 h-7 px-1 border border-default rounded text-xs text-center bg-surface"
                                />
                                <Tooltip text="This leg's own slippage — independent of every other leg (e.g. give an options leg slippage while a futures hedge leg stays off)." />
                              </>)}
                            </div>
                            {leg.segment !== 'midcap100' && (
                              <div className="flex items-center gap-2">
                                <Toggle
                                  enabled={Boolean(leg.individual_filter)}
                                  onToggle={(val) => {
                                    const next = val !== undefined ? Boolean(val) : !leg.individual_filter;
                                    updateLeg(leg.id, 'individual_filter', next);
                                    if (!next) {
                                      updateLeg(leg.id, 'filter_segments', []);
                                      updateLeg(leg.id, 'filter_file_name', '');
                                      updateLeg(leg.id, 'filter_error', '');
                                    }
                                  }}
                                  size="sm"
                                />
                                <span className="text-xs font-medium text-secondary whitespace-nowrap">Individual Filter</span>
                                <Tooltip text="Give this leg its own date file. It only subtracts: the strategy Filter still decides which trades exist, and this leg is skipped on trades outside its ranges. It exits at whichever comes first — its own range end or the trade's exit." />
                              </div>
                            )}
                          </div>
                          {leg.segment !== 'midcap100' && leg.individual_filter && (
                            <div className="rounded-lg border border-default bg-hover/40 px-3 py-2 space-y-1.5">
                              <div className="flex flex-wrap items-center gap-2">
                                <input
                                  type="file"
                                  accept=".csv"
                                  id={`leg-filter-csv-${leg.id}`}
                                  onChange={e => handleLegFilterUpload(leg.id, e)}
                                  className="hidden"
                                />
                                <label
                                  htmlFor={`leg-filter-csv-${leg.id}`}
                                  className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-secondary bg-hover border border-default rounded-lg hover:bg-base transition-colors cursor-pointer"
                                >
                                  {leg.filter_uploading ? (
                                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                                  ) : (
                                    <Upload className="w-3.5 h-3.5" />
                                  )}
                                  Upload CSV
                                </label>
                                {leg.filter_file_name && (
                                  <span className="flex items-center gap-1 text-xs text-profit">
                                    <FileText className="w-3 h-3" />
                                    {leg.filter_file_name}
                                    <button
                                      type="button"
                                      onClick={() => {
                                        updateLeg(leg.id, 'filter_segments', []);
                                        updateLeg(leg.id, 'filter_file_name', '');
                                        updateLeg(leg.id, 'filter_error', '');
                                      }}
                                      className="ml-1 hover:text-loss"
                                      title="Remove this leg's filter file"
                                    >
                                      <X className="w-3 h-3" />
                                    </button>
                                  </span>
                                )}
                                {!!(leg.filter_segments || []).length && (
                                  <span className="px-2 py-0.5 rounded-full bg-accent/10 text-accent text-[11px] font-medium">
                                    {leg.filter_segments.length} range{leg.filter_segments.length === 1 ? '' : 's'}
                                  </span>
                                )}
                              </div>
                              {leg.filter_error && (
                                <p className="text-[11px] text-loss">{leg.filter_error}</p>
                              )}
                              {!(leg.filter_segments || []).length && !leg.filter_error && (
                                <p className="text-[11px] text-muted">
                                  CSV with <span className="text-secondary">Start,End</span> (or Entry,Exit) columns, day-first dates.
                                </p>
                              )}
                            </div>
                          )}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {Object.keys(lazyLegs).length > 0 && (
              <div className="bg-surface rounded-lg border border-default shadow-sm overflow-hidden">
                <div className="px-4 py-2.5 border-b border-subtle">
                  <h3 className="section-heading">
                    Lazy Legs <span style={{ fontWeight: 400, fontSize: '0.55rem', color: 'var(--text-muted)', marginLeft: '4px' }}>({lazyLegList.length}/10)</span>
                  </h3>
                </div>
                <div className="divide-y divide-subtle">
                  {lazyLegList.map(ll => (
                    <div key={ll.id} className="px-4 py-3">
                      <div className="flex items-center justify-between mb-3">
                        <div className="flex items-center gap-2">
                          <span className="text-xs font-bold text-accent">{ll.name}</span>
                          <span className="text-xs font-semibold text-primary">
                            {ll.position?.toUpperCase()} {ll.option_type?.toUpperCase()} | {ll.expiry}
                          </span>
                        </div>
                        <button type="button" onClick={() => removeLazyLeg(ll.id)} className="w-5 h-5 rounded-full bg-loss text-white text-xs leading-none">
                          ×
                        </button>
                      </div>

                      <div className="flex flex-wrap items-end gap-3">
                        <label className="text-xs text-muted">
                          Lots
                          <input type="number" min={1} value={ll.lot || 1} onChange={e => updateLazyLeg(ll.id, 'lot', parseInt(e.target.value, 10) || 1)} className="block mt-1 w-14 h-7 px-1 border border-default rounded text-xs text-center bg-surface" />
                        </label>
                        <label className="text-xs text-muted">
                          Position
                          <select value={ll.position || 'sell'} onChange={e => updateLazyLeg(ll.id, 'position', e.target.value)} className="block mt-1 h-7 px-2 border border-default rounded text-xs bg-surface">
                            <option value="buy">Buy</option>
                            <option value="sell">Sell</option>
                          </select>
                        </label>
                        <label className="text-xs text-muted">
                          Option Type
                          <select value={ll.option_type || 'call'} onChange={e => updateLazyLeg(ll.id, 'option_type', e.target.value)} className="block mt-1 h-7 px-2 border border-default rounded text-xs bg-surface">
                            <option value="call">Call</option>
                            <option value="put">Put</option>
                          </select>
                        </label>
                        <label className="text-xs text-muted">
                          Expiry
                          <select value={ll.expiry || defaultOptionExpiry} onChange={e => updateLazyLeg(ll.id, 'expiry', e.target.value)} className="block mt-1 h-7 px-2 border border-default rounded text-xs bg-surface">
                            {optionExpiryOptions.map(opt => (
                              <option key={opt.value} value={opt.value}>{opt.label}</option>
                            ))}
                          </select>
                        </label>
                        <label className="text-xs text-muted">
                          Strike Criteria
                          <select value={ll.strike_criteria || 'strike_type'} onChange={e => updateLazyLeg(ll.id, 'strike_criteria', e.target.value)} className="block mt-1 h-7 px-2 border border-default rounded text-xs bg-surface">
                            <option value="strike_type">Strike Type</option>
                            <option value="closest_premium">Closest Premium</option>
                            <option value="premium_range">Premium Range</option>
                          </select>
                        </label>
                        <label className="text-xs text-muted">
                          Strike Gap
                          <StrikeIntervalSelect
                            value={ll.strike_interval}
                            onChange={value => updateLazyLeg(ll.id, 'strike_interval', value)}
                            index={instrument}
                            className="block mt-1 h-7 px-2 border border-default rounded text-xs bg-surface w-20"
                          />
                        </label>
                        {ll.strike_criteria === 'closest_premium' ? (
                          <label className="text-xs text-muted">
                            Premium
                            <input type="number" min={0} value={ll.premium_value || 0} onChange={e => updateLazyLeg(ll.id, 'premium_value', +e.target.value)} className="block mt-1 w-20 h-7 px-1 border border-default rounded text-xs text-center bg-surface" />
                          </label>
                        ) : ll.strike_criteria === 'premium_range' ? (
                          <div className="flex gap-2">
                            <label className="text-xs text-muted">
                              Min
                              <input type="number" min={0} value={ll.premium_min || 0} onChange={e => updateLazyLeg(ll.id, 'premium_min', +e.target.value)} className="block mt-1 w-16 h-7 px-1 border border-default rounded text-xs text-center bg-surface" />
                            </label>
                            <label className="text-xs text-muted">
                              Max
                              <input type="number" min={0} value={ll.premium_max || 0} onChange={e => updateLazyLeg(ll.id, 'premium_max', +e.target.value)} className="block mt-1 w-16 h-7 px-1 border border-default rounded text-xs text-center bg-surface" />
                            </label>
                          </div>
                        ) : (
                          <label className="text-xs text-muted">
                            Strike Type
                            <select value={ll.strike_type || 'atm'} onChange={e => updateLazyLeg(ll.id, 'strike_type', e.target.value)} className="block mt-1 h-7 px-2 border border-default rounded text-xs bg-surface w-24">
                              {strikeTypeOpts.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                            </select>
                          </label>
                        )}
                      </div>

                      <div className="pt-2 mt-3 border-t border-subtle space-y-2">
                        <div className="flex flex-wrap gap-x-4 gap-y-2">
                          <div className="flex items-center gap-2">
                            <Toggle enabled={ll.target_enabled} onToggle={(val) => updateLazyLeg(ll.id, 'target_enabled', val !== undefined ? Boolean(val) : !ll.target_enabled)} size="sm" />
                            <span className="text-xs font-medium text-secondary whitespace-nowrap">Target Profit</span>
                            {ll.target_enabled && (
                              <>
                                <select value={ll.target_mode || 'PERCENT'} onChange={e => updateLazyLeg(ll.id, 'target_mode', e.target.value)} className="h-7 px-1 border border-default rounded text-xs bg-surface">
                                  <option value="POINTS">Points (Pts)</option>
                                  <option value="PERCENT">Percent (%)</option>
                                </select>
                                <input type="number" min={0} value={ll.target_value ?? 0} onChange={e => updateLazyLeg(ll.id, 'target_value', +e.target.value)} className="w-14 h-7 px-1 border border-default rounded text-xs text-center" />
                              </>
                            )}
                          </div>
                          <div className="flex items-center gap-2">
                            <Toggle enabled={ll.stop_loss_enabled} onToggle={(val) => { const nv = val !== undefined ? Boolean(val) : !ll.stop_loss_enabled; updateLazyLeg(ll.id, 'stop_loss_enabled', nv); if (nv) updateLazyLeg(ll.id, 'sl_buffer_enabled', false); }} size="sm" />
                            <span className="text-xs font-medium text-secondary whitespace-nowrap">Stop Loss</span>
                            {ll.stop_loss_enabled && (
                              <>
                                <select value={ll.stop_loss_mode || 'PERCENT'} onChange={e => updateLazyLeg(ll.id, 'stop_loss_mode', e.target.value)} className="h-7 px-1 border border-default rounded text-xs bg-surface">
                                  <option value="POINTS">Points (Pts)</option>
                                  <option value="PERCENT">Percent (%)</option>
                                </select>
                                <input type="number" min={0} value={ll.stop_loss_value ?? 0} onChange={e => updateLazyLeg(ll.id, 'stop_loss_value', +e.target.value)} className="w-14 h-7 px-1 border border-default rounded text-xs text-center" />
                              </>
                            )}
                          </div>
                          <div className="flex items-center gap-2">
                            <Toggle enabled={ll.sl_buffer_enabled} onToggle={(val) => { const nv = val !== undefined ? Boolean(val) : !ll.sl_buffer_enabled; updateLazyLeg(ll.id, 'sl_buffer_enabled', nv); if (nv) updateLazyLeg(ll.id, 'stop_loss_enabled', false); }} size="sm" />
                            <span className="text-xs font-medium text-secondary whitespace-nowrap">SL with Buffer</span>
                            {ll.sl_buffer_enabled && (
                              <>
                                <select value={ll.sl_buffer_mode || 'PERCENT'} onChange={e => updateLazyLeg(ll.id, 'sl_buffer_mode', e.target.value)} className="h-7 px-1 border border-default rounded text-xs bg-surface">
                                  <option value="POINTS">Points (Pts)</option>
                                  <option value="PERCENT">Percent (%)</option>
                                </select>
                                <input type="number" min={0} value={ll.sl_buffer_value ?? ''} onChange={e => updateLazyLeg(ll.id, 'sl_buffer_value', e.target.value === '' ? null : +e.target.value)} className="w-14 h-7 px-1 border border-default rounded text-xs text-center" />
                                <span className="text-xs text-secondary whitespace-nowrap">Buf%</span>
                                <input type="number" min={0} max={100} value={ll.sl_buffer_pct ?? ''} onChange={e => updateLazyLeg(ll.id, 'sl_buffer_pct', e.target.value === '' ? null : +e.target.value)} className="w-12 h-7 px-1 border border-default rounded text-xs text-center" />
                              </>
                            )}
                          </div>
                          <div className="flex items-center gap-2">
                            <Toggle enabled={ll.trail_sl_enabled} onToggle={(val) => updateLazyLeg(ll.id, 'trail_sl_enabled', val !== undefined ? Boolean(val) : !ll.trail_sl_enabled)} size="sm" />
                            <span className="text-xs font-medium text-secondary whitespace-nowrap">Trail SL</span>
                          </div>
                        </div>
                        <div className="flex flex-wrap gap-x-4 gap-y-2">
                          <div className="flex items-center gap-2">
                            <Toggle enabled={ll.re_entry_target_enabled} onToggle={(val) => updateLazyLeg(ll.id, 're_entry_target_enabled', val !== undefined ? Boolean(val) : !ll.re_entry_target_enabled)} size="sm" />
                            <span className="text-xs font-medium text-secondary whitespace-nowrap">Re-entry on Tgt</span>
                            {ll.re_entry_target_enabled && (
                              <>
                                <select value={ll.re_entry_target_mode || 'RE_ASAP'} onChange={e => handleLazyReEntryModeSelect(ll.id, 'target', e.target.value)} className="h-7 px-1 border border-default rounded text-xs bg-surface">
                                  <option value="RE_ASAP">RE ASAP</option>
                                  <option value="RE_ASAP_REV">RE ASAP &#8629;</option>
                                  <option value="RE_MOMENTUM">RE MOMENTUM</option>
                                  <option value="RE_MOMENTUM_REV">RE MOMENTUM &#8629;</option>
                                  <option value="LAZY_LEG">Lazy Leg</option>
                                </select>
                                <select value={ll.re_entry_target_count || 1} onChange={e => updateLazyLeg(ll.id, 're_entry_target_count', +e.target.value)} className="w-10 h-7 px-1 border border-default rounded text-xs bg-surface">
                                  {Array.from({ length: 20 }, (_, i) => i + 1).map(n => <option key={n} value={n}>{n}</option>)}
                                </select>
                              </>
                            )}
                          </div>
                          <div className="flex items-center gap-2">
                            <Toggle enabled={ll.re_entry_sl_enabled} onToggle={(val) => updateLazyLeg(ll.id, 're_entry_sl_enabled', val !== undefined ? Boolean(val) : !ll.re_entry_sl_enabled)} size="sm" />
                            <span className="text-xs font-medium text-secondary whitespace-nowrap">Re-entry on SL</span>
                            {ll.re_entry_sl_enabled && (
                              <>
                                <select value={ll.re_entry_sl_mode || 'RE_ASAP'} onChange={e => handleLazyReEntryModeSelect(ll.id, 'sl', e.target.value)} className="h-7 px-1 border border-default rounded text-xs bg-surface">
                                  <option value="RE_ASAP">RE ASAP</option>
                                  <option value="RE_ASAP_REV">RE ASAP &#8629;</option>
                                  <option value="RE_MOMENTUM">RE MOMENTUM</option>
                                  <option value="RE_MOMENTUM_REV">RE MOMENTUM &#8629;</option>
                                  <option value="LAZY_LEG">Lazy Leg</option>
                                </select>
                                <select value={ll.re_entry_sl_count || 1} onChange={e => updateLazyLeg(ll.id, 're_entry_sl_count', +e.target.value)} className="w-10 h-7 px-1 border border-default rounded text-xs bg-surface">
                                  {Array.from({ length: 20 }, (_, i) => i + 1).map(n => <option key={n} value={n}>{n}</option>)}
                                </select>
                              </>
                            )}
                          </div>
                          <div className="flex items-center gap-2">
                            <Toggle enabled={ll.simple_momentum_enabled} onToggle={(val) => updateLazyLeg(ll.id, 'simple_momentum_enabled', val !== undefined ? Boolean(val) : !ll.simple_momentum_enabled)} size="sm" />
                            <span className="text-xs font-medium text-secondary whitespace-nowrap">Simple Momentum</span>
                          </div>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>

        {false && Object.keys(lazyLegs).length > 0 && (
          <div className="mt-4 bg-surface rounded-lg border border-default shadow-sm overflow-hidden">
            <div className="px-4 py-3 border-b border-subtle">
              <h3 className="text-sm font-semibold text-primary">Lazy Legs</h3>
            </div>
            <div className="divide-y divide-subtle">
              {lazyLegList.map(ll => (
                <div key={ll.id} className="relative px-4 py-4 bg-surface">
                  <div className="flex items-center justify-between mb-3">
                    <div className="flex items-center gap-2">
                      <span className="px-2 py-0.5 rounded bg-hover border border-subtle text-xs font-semibold text-primary">
                        {ll.name}
                      </span>
                      <span className="text-xs text-secondary">
                        {ll.position?.toUpperCase()} {ll.option_type?.toUpperCase()} {ll.expiry}
                      </span>
                    </div>
                    <div className="flex items-center gap-2">
                      <button type="button" onClick={() => openLazyLegById(ll.id)} className="text-xs text-accent hover:text-accent-hover">
                        Edit popup
                      </button>
                      <button type="button" onClick={() => removeLazyLeg(ll.id)} className="w-5 h-5 rounded-full bg-loss text-white text-xs leading-none">
                        ×
                      </button>
                    </div>
                  </div>

                  <div className="grid grid-cols-2 md:grid-cols-6 gap-3">
                    <label className="text-xs text-secondary">
                      Lots
                      <input type="number" min={1} value={ll.lot || 1} onChange={e => updateLazyLeg(ll.id, 'lot', parseInt(e.target.value, 10) || 1)} className="mt-1 w-full h-7 px-2 border border-default rounded text-xs bg-surface" />
                    </label>
                    <label className="text-xs text-secondary">
                      Position
                      <select value={ll.position || 'sell'} onChange={e => updateLazyLeg(ll.id, 'position', e.target.value)} className="mt-1 w-full h-7 px-2 border border-default rounded text-xs bg-surface">
                        <option value="buy">Buy</option>
                        <option value="sell">Sell</option>
                      </select>
                    </label>
                    <label className="text-xs text-secondary">
                      Option Type
                      <select value={ll.option_type || 'call'} onChange={e => updateLazyLeg(ll.id, 'option_type', e.target.value)} className="mt-1 w-full h-7 px-2 border border-default rounded text-xs bg-surface">
                        <option value="call">Call</option>
                        <option value="put">Put</option>
                      </select>
                    </label>
                    <label className="text-xs text-secondary">
                      Expiry
                      <select value={ll.expiry || defaultOptionExpiry} onChange={e => updateLazyLeg(ll.id, 'expiry', e.target.value)} className="mt-1 w-full h-7 px-2 border border-default rounded text-xs bg-surface">
                        {optionExpiryOptions.map(opt => (
                          <option key={opt.value} value={opt.value}>{opt.label}</option>
                        ))}
                      </select>
                    </label>
                    <label className="text-xs text-secondary">
                      Strike Criteria
                      <select value={ll.strike_criteria || 'strike_type'} onChange={e => updateLazyLeg(ll.id, 'strike_criteria', e.target.value)} className="mt-1 w-full h-7 px-2 border border-default rounded text-xs bg-surface">
                        <option value="strike_type">Strike Type</option>
                        <option value="closest_premium">Closest Premium</option>
                        <option value="premium_range">Premium Range</option>
                      </select>
                    </label>
                    <label className="text-xs text-secondary">
                      Strike Gap
                      <StrikeIntervalSelect
                        value={ll.strike_interval}
                        onChange={value => updateLazyLeg(ll.id, 'strike_interval', value)}
                        index={instrument}
                        className="mt-1 w-full h-7 px-2 border border-default rounded text-xs bg-surface"
                      />
                    </label>
                    {ll.strike_criteria === 'closest_premium' ? (
                      <label className="text-xs text-secondary">
                        Premium
                        <input type="number" min={0} value={ll.premium_value || 0} onChange={e => updateLazyLeg(ll.id, 'premium_value', +e.target.value)} className="mt-1 w-full h-7 px-2 border border-default rounded text-xs bg-surface" />
                      </label>
                    ) : ll.strike_criteria === 'premium_range' ? (
                      <div className="grid grid-cols-2 gap-2">
                        <label className="text-xs text-secondary">
                          Min
                          <input type="number" min={0} value={ll.premium_min || 0} onChange={e => updateLazyLeg(ll.id, 'premium_min', +e.target.value)} className="mt-1 w-full h-7 px-2 border border-default rounded text-xs bg-surface" />
                        </label>
                        <label className="text-xs text-secondary">
                          Max
                          <input type="number" min={0} value={ll.premium_max || 0} onChange={e => updateLazyLeg(ll.id, 'premium_max', +e.target.value)} className="mt-1 w-full h-7 px-2 border border-default rounded text-xs bg-surface" />
                        </label>
                      </div>
                    ) : (
                      <label className="text-xs text-secondary">
                        Strike Type
                        <select value={ll.strike_type || 'atm'} onChange={e => updateLazyLeg(ll.id, 'strike_type', e.target.value)} className="mt-1 w-full h-7 px-2 border border-default rounded text-xs bg-surface">
                          {strikeTypeOpts.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                        </select>
                      </label>
                    )}
                  </div>

                  <div className="mt-3 pt-3 border-t border-subtle space-y-2">
                    <div className="flex flex-wrap gap-x-4 gap-y-2">
                      <div className="flex items-center gap-2">
                        <Toggle enabled={ll.target_enabled} onToggle={(val) => updateLazyLeg(ll.id, 'target_enabled', val !== undefined ? Boolean(val) : !ll.target_enabled)} size="sm" />
                        <span className="text-xs font-medium text-secondary whitespace-nowrap">Target Profit</span>
                        {ll.target_enabled && (
                          <>
                            <select value={ll.target_mode || 'PERCENT'} onChange={e => updateLazyLeg(ll.id, 'target_mode', e.target.value)} className="h-7 px-1 border border-default rounded text-xs bg-surface">
                              <option value="POINTS">Points (Pts)</option>
                              <option value="PERCENT">Percent (%)</option>
                            </select>
                            <input type="number" min={0} value={ll.target_value ?? 0} onChange={e => updateLazyLeg(ll.id, 'target_value', +e.target.value)} className="w-14 h-7 px-1 border border-default rounded text-xs text-center" />
                          </>
                        )}
                      </div>
                      <div className="flex items-center gap-2">
                        <Toggle enabled={ll.stop_loss_enabled} onToggle={(val) => updateLazyLeg(ll.id, 'stop_loss_enabled', val !== undefined ? Boolean(val) : !ll.stop_loss_enabled)} size="sm" />
                        <span className="text-xs font-medium text-secondary whitespace-nowrap">Stop Loss</span>
                        {ll.stop_loss_enabled && (
                          <>
                            <select value={ll.stop_loss_mode || 'PERCENT'} onChange={e => updateLazyLeg(ll.id, 'stop_loss_mode', e.target.value)} className="h-7 px-1 border border-default rounded text-xs bg-surface">
                              <option value="POINTS">Points (Pts)</option>
                              <option value="PERCENT">Percent (%)</option>
                            </select>
                            <input type="number" min={0} value={ll.stop_loss_value ?? 0} onChange={e => updateLazyLeg(ll.id, 'stop_loss_value', +e.target.value)} className="w-14 h-7 px-1 border border-default rounded text-xs text-center" />
                          </>
                        )}
                      </div>
                      <div className="flex items-center gap-2">
                        <Toggle enabled={ll.trail_sl_enabled} onToggle={(val) => updateLazyLeg(ll.id, 'trail_sl_enabled', val !== undefined ? Boolean(val) : !ll.trail_sl_enabled)} size="sm" />
                        <span className="text-xs font-medium text-secondary whitespace-nowrap">Trail SL</span>
                        {ll.trail_sl_enabled && (
                          <>
                            <select value={ll.trail_sl_mode || 'PERCENT'} onChange={e => updateLazyLeg(ll.id, 'trail_sl_mode', e.target.value)} className="w-16 h-7 px-1 border border-default rounded text-xs bg-surface">
                              <option value="POINTS">Points</option>
                              <option value="PERCENT">Percent</option>
                            </select>
                            <input type="number" min={0} placeholder="X" value={ll.trail_sl_trigger ?? 0} onChange={e => updateLazyLeg(ll.id, 'trail_sl_trigger', +e.target.value)} className="w-12 h-7 px-1 border border-default rounded text-xs text-center" />
                            <input type="number" min={0} placeholder="Y" value={ll.trail_sl_move ?? 0} onChange={e => updateLazyLeg(ll.id, 'trail_sl_move', +e.target.value)} className="w-12 h-7 px-1 border border-default rounded text-xs text-center" />
                          </>
                        )}
                      </div>
                    </div>

                    <div className="flex flex-wrap gap-x-4 gap-y-2">
                      <div className="flex items-center gap-2">
                        <Toggle enabled={ll.re_entry_target_enabled} onToggle={(val) => updateLazyLeg(ll.id, 're_entry_target_enabled', val !== undefined ? Boolean(val) : !ll.re_entry_target_enabled)} size="sm" />
                        <span className="text-xs font-medium text-secondary whitespace-nowrap">Re-entry on Tgt</span>
                        {ll.re_entry_target_enabled && (
                          <>
                            <select value={ll.re_entry_target_mode || 'RE_ASAP'} onChange={e => handleLazyReEntryModeSelect(ll.id, 'target', e.target.value)} className="h-7 px-1 border border-default rounded text-xs bg-surface">
                              <option value="RE_ASAP">RE ASAP</option>
                              <option value="RE_ASAP_REV">RE ASAP &#8629;</option>
                              <option value="RE_MOMENTUM">RE MOMENTUM</option>
                              <option value="RE_MOMENTUM_REV">RE MOMENTUM &#8629;</option>
                              <option value="LAZY_LEG">Lazy Leg</option>
                            </select>
                            <select value={ll.re_entry_target_count || 1} onChange={e => updateLazyLeg(ll.id, 're_entry_target_count', +e.target.value)} className="w-10 h-7 px-1 border border-default rounded text-xs bg-surface">
                              {Array.from({ length: 20 }, (_, i) => i + 1).map(n => <option key={n} value={n}>{n}</option>)}
                            </select>
                            {ll.re_entry_target_mode === 'LAZY_LEG' && (
                              <select value={ll.child_lazy_leg_target_id || ''} onChange={e => attachLazyLegToLazyParent(ll.id, 'target', e.target.value)} className="h-7 px-1 border border-default rounded text-xs bg-surface">
                                <option value="">Select</option>
                                {lazyLegList.filter(opt => opt.id !== ll.id).map(opt => <option key={opt.id} value={opt.id}>{opt.name}</option>)}
                              </select>
                            )}
                          </>
                        )}
                      </div>
                      <div className="flex items-center gap-2">
                        <Toggle enabled={ll.re_entry_sl_enabled} onToggle={(val) => updateLazyLeg(ll.id, 're_entry_sl_enabled', val !== undefined ? Boolean(val) : !ll.re_entry_sl_enabled)} size="sm" />
                        <span className="text-xs font-medium text-secondary whitespace-nowrap">Re-entry on SL</span>
                        {ll.re_entry_sl_enabled && (
                          <>
                            <select value={ll.re_entry_sl_mode || 'RE_ASAP'} onChange={e => handleLazyReEntryModeSelect(ll.id, 'sl', e.target.value)} className="h-7 px-1 border border-default rounded text-xs bg-surface">
                              <option value="RE_ASAP">RE ASAP</option>
                              <option value="RE_ASAP_REV">RE ASAP &#8629;</option>
                              <option value="RE_MOMENTUM">RE MOMENTUM</option>
                              <option value="RE_MOMENTUM_REV">RE MOMENTUM &#8629;</option>
                              <option value="LAZY_LEG">Lazy Leg</option>
                            </select>
                            <select value={ll.re_entry_sl_count || 1} onChange={e => updateLazyLeg(ll.id, 're_entry_sl_count', +e.target.value)} className="w-10 h-7 px-1 border border-default rounded text-xs bg-surface">
                              {Array.from({ length: 20 }, (_, i) => i + 1).map(n => <option key={n} value={n}>{n}</option>)}
                            </select>
                            {ll.re_entry_sl_mode === 'LAZY_LEG' && (
                              <select value={ll.child_lazy_leg_sl_id || ''} onChange={e => attachLazyLegToLazyParent(ll.id, 'sl', e.target.value)} className="h-7 px-1 border border-default rounded text-xs bg-surface">
                                <option value="">Select</option>
                                {lazyLegList.filter(opt => opt.id !== ll.id).map(opt => <option key={opt.id} value={opt.id}>{opt.name}</option>)}
                              </select>
                            )}
                          </>
                        )}
                      </div>
                      <div className="flex items-center gap-2">
                        <Toggle enabled={ll.simple_momentum_enabled} onToggle={(val) => updateLazyLeg(ll.id, 'simple_momentum_enabled', val !== undefined ? Boolean(val) : !ll.simple_momentum_enabled)} size="sm" />
                        <span className="text-xs font-medium text-secondary whitespace-nowrap">Simple Momentum</span>
                        {ll.simple_momentum_enabled && (
                          <>
                            <select value={ll.simple_momentum_mode || 'POINTS_UP'} onChange={e => updateLazyLeg(ll.id, 'simple_momentum_mode', e.target.value)} className="h-7 px-1 border border-default rounded text-xs bg-surface">
                              <option value="POINTS_UP">Points Up</option>
                              <option value="POINTS_DOWN">Points Down</option>
                              <option value="PERCENT_UP">Percent Up</option>
                              <option value="PERCENT_DOWN">Percent Down</option>
                            </select>
                            <input type="number" min={0} value={ll.simple_momentum_value ?? 0} onChange={e => updateLazyLeg(ll.id, 'simple_momentum_value', +e.target.value)} className="w-14 h-7 px-1 border border-default rounded text-xs text-center" />
                          </>
                        )}
                      </div>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Date Range Bar */}
        <div className="mt-4 bg-surface rounded-lg border border-default shadow-sm px-5 py-4">
          <div className="flex items-end justify-between gap-4">
            <div className="flex items-end gap-3">
              {/* Start Date */}
              <div>
                <label className="field-label">Start Date</label>
                <CalendarPicker value={startDate} onChange={handleStartDateChange} />
              </div>
              {/* Arrow separator */}
              <div style={{
                height: '38px', display: 'flex', alignItems: 'center', paddingBottom: '1px',
                color: 'var(--text-muted)', fontSize: '0.8rem',
                fontFamily: 'IBM Plex Mono, monospace', userSelect: 'none',
              }}>→</div>
              {/* End Date */}
              <div>
                <label className="field-label">End Date</label>
                <CalendarPicker value={endDate} onChange={handleEndDateChange} />
              </div>
            </div>
            {strFilter.enabled && strFilter.summary?.range && (() => {
              const filterStart = new Date(strFilter.summary.range.from);
              const fiveYearsAgo = new Date();
              fiveYearsAgo.setFullYear(fiveYearsAgo.getFullYear() - 5);
              const needsLimited = filterStart < fiveYearsAgo;
              if (!needsLimited) return null;
              return (
                <button
                  type="button"
                  onClick={() => setShowFullRange(true)}
                  className="px-3 py-1.5 text-xs font-medium rounded-lg transition-colors"
                  style={{ color: 'var(--accent)', background: 'var(--accent-bg)', border: '1px solid var(--border-accent)' }}
                >
                  Load Full Range ({formatSummaryDateInput(strFilter.summary.range.from)} → {formatSummaryDateInput(strFilter.summary.range.to)})
                </button>
              );
            })()}
          </div>
        </div>
        {validationError && (
          <div className="mt-2 text-xs text-loss">{validationError}</div>
        )}

        {/* Results */}
        {displayResults && (
          <div className="mt-4" ref={resultsRef}>
            {displayResults?.meta?.str_enabled && (
              <div className="mb-3 text-xs text-accent rounded px-3 py-2 inline-block" style={{ background: 'var(--accent-bg)', border: '1px solid var(--accent)', opacity: 0.9 }}>
                STR {displayResults?.meta?.str_type}: {displayResults?.meta?.trades_before_str_filter} -&gt; {displayResults?.meta?.trades_after_str_filter}
              </div>
            )}
            <ResultsPanel
              results={displayResults}
              onClose={() => {
                setResults(null);
                setRawResults(null);
                setDisplayResults(null);
              }}
              showCloseButton={false}
              filterInfo={strFilter.enabled ? `Filtered by ${strFilter.configLabel}` : null}
              filterSegments={strFilter.enabled ? strFilter.segments : null}
              showStrSegment={strFilter.enabled}
              rulesPayload={rulesSubmittedPayloadRef.current || buildPayload()}
              rulesFilterName={strFilter.enabled ? strFilter.filterName : null}
              // Snapshot taken when THIS run was submitted (see
              // rulesSubmittedPayloadRef above) — not live legs/config, which
              // may have been edited since without re-running. Falls back to a
              // live build only if somehow no run has ever completed (should be
              // unreachable here since this block only renders when
              // displayResults is truthy).
              strategyConfig={resultsSnapshotConfig || buildStrategyConfigSnapshot()}
            />
            <div className="flex flex-wrap items-center gap-4 px-4 py-3 bg-surface border border-default border-t-0 rounded-b-xl">
              {/* Slippage is per-leg now (set on each leg card) and already
                  baked into these results — nothing to toggle here. */}
              <div className="flex items-center gap-2">
                <label className="text-xs font-medium text-secondary whitespace-nowrap">
                  Txn Charges
                </label>
                <Toggle enabled={chargesEnabled} onToggle={(val) => setChargesEnabled(prev => val !== undefined ? Boolean(val) : !prev)} size="sm" />
              </div>
              <button
                onClick={handleRecalculate}
                disabled={isRecalculating || !rawResults}
                className="flex items-center gap-1.5 px-4 py-1.5 text-sm font-medium rounded bg-accent text-white hover:bg-accent-hover disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
              >
                {isRecalculating ? (
                  <>
                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    Recalculating...
                  </>
                ) : (
                  <>
                    <RefreshCw className="w-3.5 h-3.5" />
                    Re-calculate
                  </>
                )}
              </button>
              {displayResults?.meta?.charges_enabled && (
                <span className="text-xs text-secondary">
                  Applied: <strong>Txn charges</strong>
                </span>
              )}
            </div>
          </div>
        )}

        {/* Error Alert - Above Button */}
        {error && (
          <div className="fixed bottom-24 left-1/2 transform -translate-x-1/2 z-50 flex items-center gap-2 px-4 py-2 bg-loss-bg border border-red-200 rounded-lg shadow-lg">
            <AlertTriangle size={16} className="text-loss flex-shrink-0" />
            <span className="text-sm text-red-700">{error}</span>
          </div>
        )}
        {!indexConfig.backtestEnabled && (
          <div className="fixed bottom-24 left-1/2 transform -translate-x-1/2 z-40 flex items-center gap-2 px-4 py-2 bg-loss-bg border border-red-200 rounded-lg shadow-lg">
            <AlertTriangle size={16} className="text-loss flex-shrink-0" />
            <span className="text-sm text-red-700">{unsupportedIndexMessage}</span>
          </div>
        )}

        {/* Run Backtest + Optimize Buttons — fixed bottom-center */}
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50 flex flex-col items-center gap-2">
          <div className="flex items-center gap-3">
            <button
              onClick={runBacktest}
              disabled={!canRunBacktest}
              className="run-btn px-9 py-3 rounded-xl"
            >
              {loading ? (
                <>
                  <Loader2 size={16} className="animate-spin" />
                  <span>Running…</span>
                </>
              ) : (
                <>
                  <Play size={15} />
                  <span>Run Backtest</span>
                </>
              )}
            </button>
            <button
              onClick={() => setOptimPanelOpen(true)}
              disabled={!canRunBacktest}
              className="run-btn px-7 py-3 rounded-xl"
              style={{
                background: 'var(--accent-bg, #eff6ff)',
                color: 'var(--accent, #2563eb)',
                border: '1px solid var(--accent, #2563eb)',
              }}
              title="Run an AmiBroker-style parameter sweep on this strategy"
            >
              <Beaker size={15} />
              <span>Optimize</span>
            </button>
          </div>
          {(jobStatusLabel || cacheWarmLabel) && (
            <div style={{ fontFamily: 'IBM Plex Mono, monospace', fontSize: '0.6rem', color: 'var(--text-secondary)', letterSpacing: '0.04em', textAlign: 'center' }}>
              {jobStatusLabel || cacheWarmLabel}
            </div>
          )}
        </div>
        {optimPanelOpen && (
          <OptimizePanel
            isOpen={optimPanelOpen}
            onClose={() => setOptimPanelOpen(false)}
            basePayload={buildPayload()}
            nLegs={legs.filter(l => String(l.segment || '').toLowerCase() !== 'midcap100').length}
            checked={optimChecked} setChecked={setOptimChecked}
            savedValues={optimSavedValues} setSavedValues={setOptimSavedValues}
            unitChoice={optimUnitChoice} setUnitChoice={setOptimUnitChoice}
            method={optimMethod} setMethod={setOptimMethod}
            sampleN={optimSampleN} setSampleN={setOptimSampleN}
            algorithm={optimAlgorithm} setAlgorithm={setOptimAlgorithm}
            objective={optimObjective} setObjective={setOptimObjective}
            parallelism={optimParallelism} setParallelism={setOptimParallelism}
            filterName={strFilter.enabled ? strFilter.filterName : null}
            nodeId={selectedNodeId}
            onJobQueued={(info) => {
              // Capture the Midcap overlay config at launch so per-combo
              // downloads can apply the same overlay as the backtest.
              const fullInfo = { ...info, midcapConfig: buildMidcapConfig() };
              setOptimJob(fullInfo);
              setOptimPanelOpen(false);
              // Only track for auto-download when the user explicitly turned
              // the toggle on in the Optimize panel — otherwise every ad-hoc
              // run would clutter the widget on every open tab/PC. Viewing
              // the just-launched job's progress (setOptimJob above) is
              // unaffected either way.
              if (info.autoDownload) {
                // Append (never replace) so AutoDownloadQueue keeps watching
                // every opted-in job queued this session, including ones
                // queued behind an already-running job. Also persist to
                // localStorage so a sibling tab (or this tab after a
                // refresh) picks it up too.
                setOptimQueueJobs((prev) => [...prev, fullInfo]);
                appendToQueue(fullInfo);
              }
            }}
          />
        )}
        {optimJob && (
          <OptimizationResults
            jobId={optimJob.jobId}
            totalCombos={optimJob.totalCombos}
            objective={optimJob.objective}
            runConfig={optimJob.runConfig}
            ruleConfig={optimJob.ruleConfig}
            midcapConfig={optimJob.midcapConfig}
            onClose={() => setOptimJob(null)}
            onApplyCombo={(combo) => {
              // Best-effort: surface the combo as a JSON note in the status
              // bar. Full "apply" semantics require remapping nested payload
              // paths back into the StrategyBuilder state graph — this can be
              // added incrementally.
              const summary = Object.entries(combo)
                .map(([k, v]) => `${k}=${v}`)
                .join('  ');
              setJobStatusLabel(`Applied combo: ${summary}`);
              setOptimJob(null);
            }}
          />
        )}
        {/* Strictly opt-in: this widget only ever tracks/shows a job when the
            user turned ON the Optimize panel's "Auto-download" toggle for
            that specific run (see OptimizePanel.jsx autoDownload state +
            StrategyBuilder's onJobQueued handler, which only pushes into
            optimQueueJobs when info.autoDownload is true). With the toggle
            left off (its default), optimQueueJobs stays empty and this
            renders nothing — no auto-download happens unless selected.
            NOTE: it is NOT gated on activeView — the Optimize panel and its
            Results panel are modals rendered OVER the Build screen (there is
            no separate "optimize" activeView), so hiding this on
            activeView==='build' would incorrectly hide it while a job the
            user just launched is actively running/downloading. */}
        <AutoDownloadQueue jobs={optimQueueJobs} patchwise />
        {lazyLegModal.open && (
          <LazyLegModal
            isOpen={lazyLegModal.open}
            onClose={closeLazyLegModal}
            onSave={saveLazyLeg}
            onConfigureChild={openChildLazyLegModal}
            editingConfig={lazyLegModal.editingLazyLegId ? lazyLegs[lazyLegModal.editingLazyLegId] : null}
            strikeTypeOpts={strikeTypeOpts}
            expiryOptions={optionExpiryOptions}
            defaultExpiry={defaultOptionExpiry}
            totalLazyLegCount={totalLazyLegCount}
          />
        )}
      </div>
      </div>
    </div>
  );
};

const ToggleOption = ({ label, tooltip, checked, onChange }) => (
  <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
    <div
      onClick={() => onChange(!checked)}
      style={{
        width: 32,
        height: 16,
        borderRadius: 8,
        background: checked ? "#0078ff" : "#ccc",
        position: "relative",
        cursor: "pointer",
        transition: "background 0.2s",
      }}
    >
      <div style={{
        position: "absolute",
        width: 12,
        height: 12,
        borderRadius: "50%",
        background: "#fff",
        top: 2,
        left: checked ? 18 : 2,
        transition: "left 0.2s",
      }} />
    </div>
    <span style={{ fontSize: 11, color: "#555" }} title={tooltip}>{label}</span>
  </div>
);

export default StrategyBuilder;
