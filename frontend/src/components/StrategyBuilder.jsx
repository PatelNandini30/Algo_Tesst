import React, { useState, useEffect, useMemo, useRef, useCallback } from 'react';
import { Play, Plus, Trash2, Info, Save, AlertTriangle, Loader2, RefreshCw, Sun, Moon } from 'lucide-react';
import { format, parse, isValid } from 'date-fns';
import ResultsPanel from './ResultsPanel';
import SuperTrendFilter from './SuperTrendFilter';
import Toggle from './ui/Toggle';
import CalendarPicker from './ui/CalendarPicker';
import TimeInput from './ui/TimeInput';
import IntradaySlowPathWarning from './IntradaySlowPathWarning';

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
    subtitle: 'Weekly & monthly expiries',
    group: 'weekly_monthly',
    backtestEnabled: true,
    expiryBases: ['weekly', 'monthly'],
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
    subtitle: 'Monthly expiry only',
    group: 'monthly_only',
    backtestEnabled: true,
    expiryBases: ['monthly'],
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
    symbols: ['NIFTY', 'SENSEX'],
  },
  {
    key: 'monthly_only',
    title: 'Monthly Only Expiry',
    symbols: ['MIDCPNIFTY', 'BANKNIFTY'],
  },
];

const WEEKLY_OPTION_EXPIRIES = [
  { value: 'weekly', label: 'Weekly' },
  { value: 'next_weekly', label: 'Next Weekly' },
  { value: 'monthly', label: 'Monthly' },
  { value: 'next_monthly', label: 'Next Monthly' },
];

const MONTHLY_OPTION_EXPIRIES = [
  { value: 'monthly', label: 'Monthly' },
  { value: 'next_monthly', label: 'Next Monthly' },
];

const FUTURES_EXPIRIES = MONTHLY_OPTION_EXPIRIES;

const getIndexConfig = (symbol) => INDEX_CONFIG[String(symbol || 'NIFTY').toUpperCase()] || INDEX_CONFIG.NIFTY;

const getOptionExpiryOptions = (symbol) => {
  const config = getIndexConfig(symbol);
  return config.expiryBases.includes('weekly') ? WEEKLY_OPTION_EXPIRIES : MONTHLY_OPTION_EXPIRIES;
};

const normalizeReEntryMode = (mode) => {
  const value = String(mode || 'RE_ASAP').toUpperCase().trim();
  return value || 'RE_ASAP';
};

const normalizeExpiryForIndex = (expiry, symbol, segment = 'options') => {
  const options = segment === 'futures' ? FUTURES_EXPIRIES : getOptionExpiryOptions(symbol);
  const current = String(expiry || '').toLowerCase();
  if (options.some(opt => opt.value === current)) return current;
  return segment === 'futures' ? 'monthly' : getIndexConfig(symbol).defaultOptionExpiry;
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
    if (d < new Date('2010-10-01')) return 200;
    if (d < new Date('2015-10-29')) return 50;
    if (d < new Date('2019-11-01')) return 75;
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
const STRIKE_INTERVAL_OPTIONS = [50, 100];
const normalizeStrikeInterval = (value) => {
  const parsed = Number(value);
  return STRIKE_INTERVAL_OPTIONS.includes(parsed) ? parsed : 50;
};

const StrikeIntervalSelect = ({ value, onChange, className = '' }) => (
  <select
    value={normalizeStrikeInterval(value)}
    onChange={e => onChange(normalizeStrikeInterval(e.target.value))}
    className={className}
  >
    {STRIKE_INTERVAL_OPTIONS.map(interval => (
      <option key={interval} value={interval}>{interval}</option>
    ))}
  </select>
);

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
  target_mode: 'POINTS',
  target_value: 0,
  stop_loss_enabled: false,
  stop_loss_mode: 'POINTS',
  stop_loss_value: 0,
  trail_sl_enabled: false,
  trail_sl_mode: 'POINTS',
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
    return saved ? saved === 'dark' : false;
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
  const [rolloverToggle, setRolloverToggle] = useState(false);
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
  const indexConfig = useMemo(() => getIndexConfig(instrument), [instrument]);
  const expiryBasisOptions = useMemo(
    () => indexConfig.expiryBases.map(value => ({
      value,
      label: value === 'weekly' ? 'Weekly Expiry' : 'Monthly Expiry',
    })),
    [indexConfig]
  );
  const optionExpiryOptions = useMemo(() => getOptionExpiryOptions(instrument), [instrument]);
  const defaultOptionExpiry = indexConfig.defaultOptionExpiry;
  const unsupportedIndexMessage = `${instrument} backtest data is not available. Import option quotes and expiry calendar before running this index.`;

  const normalizeLegForSelectedIndex = useCallback((leg) => ({
    ...leg,
    expiry: normalizeExpiryForIndex(leg.expiry, instrument, leg.segment),
    strike_interval: normalizeStrikeInterval(leg.strike_interval),
    re_entry_target_mode: normalizeReEntryMode(leg.re_entry_target_mode),
    re_entry_sl_mode: normalizeReEntryMode(leg.re_entry_sl_mode),
  }), [instrument]);

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
  const [bufferStrikeEnabled, setBufferStrikeEnabled] = useState(false);
  const [bufferStrikeValue, setBufferStrikeValue] = useState(0.5);
  const [bufferStrikeUnit, setBufferStrikeUnit] = useState('percent');
  const [bufferStrikeApplyTo, setBufferStrikeApplyTo] = useState('both');
  const [bufferPositionAbove, setBufferPositionAbove] = useState(true);
  const [bufferPositionBelow, setBufferPositionBelow] = useState(true);
const [slippagePct, setSlippagePct] = useState(0);
  const [chargesEnabled, setChargesEnabled] = useState(false);

  const clampSpotAdjustmentValue = useCallback((value) => {
    const numeric = Number(value);
    if (!Number.isFinite(numeric)) {
      return 0.25;
    }
    return Math.min(5, Math.max(0.25, numeric));
  }, []);

  const normalizedSpotAdjustmentValue = useMemo(
    () => clampSpotAdjustmentValue(spotAdjustmentValue),
    [clampSpotAdjustmentValue, spotAdjustmentValue]
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
    pct_direction: '-',
    pct_value: 0,
    atm_straddle_prem_pct: 0,
    straddle_multiplier: 0.5,
    straddle_direction: '+',
  });

  useEffect(() => {
    setExpiryBasis(prev => indexConfig.expiryBases.includes(prev) ? prev : indexConfig.defaultExpiryBasis);
    setDraftLeg(prev => normalizeLegForSelectedIndex(prev));
    setLegs(prev => prev.map(normalizeLegForSelectedIndex));
    setLazyLegs(prev => Object.fromEntries(
      Object.entries(prev).map(([id, leg]) => [id, normalizeLegForSelectedIndex(leg)])
    ));
  }, [indexConfig, normalizeLegForSelectedIndex]);

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
    configId: '5x1',
    configLabel: 'STR 5,1',
    summary: null,
    segments: [],
    entryMode: 'dte',
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
  const [isRecalculating, setIsRecalculating] = useState(false);
  const [validationError, setValidationError] = useState(null);
  const [trailSLWarning, setTrailSLWarning] = useState(null);
  const warmCacheTimerRef = useRef(null);
  const jobPollRef = useRef(null);
  const errorTimerRef = useRef(null);
  const [jobId, setJobId] = useState(null);
  const [jobStatusLabel, setJobStatusLabel] = useState('');
  const [jobState, setJobState] = useState('idle'); // 'idle' | 'queued' | 'running' | 'completed'
  const [cacheWarmReady, setCacheWarmReady] = useState(false);
  const [cacheWarmLabel, setCacheWarmLabel] = useState('');
  const [backtestMode, setBacktestMode] = useState('eod'); // 'eod' | 'intraday'
  const [intradayEntryTime, setIntradayEntryTime] = useState('09:20');
  const [intradaySquareOffTime, setIntradaySquareOffTime] = useState('15:15');
  const [slowPath, setSlowPath] = useState(false);

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
          setSlippagePct(payload?.meta?.slippage_pct ?? 0);
          setChargesEnabled(payload?.meta?.charges_enabled ?? false);
          setResults(payload);
          if (strFilter.enabled && Array.isArray(payload?.trades) && payload.trades.length === 0) {
            setError(`No trades matched the ${strFilter.configLabel} filter for this date range. Try a different filter or widen the date range.`);
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
  }, [stopJobPolling, strFilter.configLabel, strFilter.enabled]);

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
    if (expiryBasis === 'monthly') {
      const weeklyLegs = legs.filter(l => l.segment !== 'futures' && ['weekly', 'next_weekly'].includes(l.expiry));
      if (weeklyLegs.length > 0) {
        const legNumbers = weeklyLegs.map((_, i) => i + 1).join(', ');
        showValidationError(`Monthly expiry basis selected — Leg(s) ${legNumbers} have weekly expiry. Change leg expiry to Monthly or Next Monthly.`);
        return false;
      }
    }
    if (expiryBasis === 'weekly') {
      const monthlyLegs = legs.filter(l => l.segment !== 'futures' && ['monthly', 'next_monthly'].includes(l.expiry));
      if (monthlyLegs.length > 0) {
        const legNumbers = monthlyLegs.map((_, i) => i + 1).join(', ');
        showValidationError(`Weekly expiry basis selected — Leg(s) ${legNumbers} have monthly expiry. Change leg expiry to Weekly or Next Weekly.`);
        return false;
      }
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
          slippage_pct: Number(slippagePct) || 0,
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
          slippage_pct: Number(slippagePct) || 0,
          charges_enabled: chargesEnabled,
        },
      };
      setDisplayResults(nextResults);
      setResults(nextResults);
    } catch (err) {
      setError(err.message || 'Recalculate failed');
    } finally {
      setIsRecalculating(false);
    }
  }, [rawResults, slippagePct, chargesEnabled]);

  const addLegFromDraft = () => {
    if (legs.length >= 6) return;
    const normalizedDraft = normalizeLegForSelectedIndex(draftLeg);
    setLegs(prev => [...prev, {
      id: Date.now(),
      ...normalizedDraft,
      target_enabled: false, target_mode: 'POINTS', target_value: 0,
      stop_loss_enabled: false, stop_loss_mode: 'POINTS', stop_loss_value: 0,
      trail_sl_enabled: false, trail_sl_mode: 'POINTS', trail_sl_trigger: 0, trail_sl_move: 0,
      re_entry_target_enabled: false, re_entry_target_mode: 'RE_ASAP', re_entry_target_count: 1,
      re_entry_sl_enabled: false, re_entry_sl_mode: 'RE_ASAP', re_entry_sl_count: 1,
      lazy_leg_sl_id: null,
      lazy_leg_target_id: null,
      simple_momentum_enabled: false, simple_momentum_mode: 'POINTS_UP', simple_momentum_value: 0,
      straddle_multiplier: normalizedDraft.straddle_multiplier ?? 0.5,
      straddle_direction: normalizedDraft.straddle_direction ?? '+',
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
      pct_direction: '-',
      pct_value: 0,
      expiry: normalizeExpiryForIndex(prev.expiry, instrument, prev.segment),
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
  const updateLeg = (id, field, value) => setLegs(prev => prev.map(l => {
    if (l.id !== id) return l;
    const next = { ...l, [field]: value };
    if (field === 'segment' || field === 'expiry') {
      next.expiry = normalizeExpiryForIndex(next.expiry, instrument, next.segment);
    }
    return next;
  }));
  const handleLegChange = (legIndex, nextLeg) => setLegs(prev => prev.map((leg, idx) => idx === legIndex ? { ...leg, ...nextLeg } : leg));
  const totalLazyLegCount = Object.keys(lazyLegs).length;
  const lazyLegList = Object.values(lazyLegs);

  const updateLazyLeg = (id, field, value) => {
    setLazyLegs(prev => {
      if (!prev[id]) return prev;
      const nextLeg = { ...prev[id], [field]: value };
      if (field === 'expiry' || field === 'segment') {
        nextLeg.expiry = normalizeExpiryForIndex(nextLeg.expiry, instrument, nextLeg.segment);
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
      expiry: normalizeExpiryForIndex(ll.expiry || defaultOptionExpiry, instrument, 'options').toUpperCase(),
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

  const buildPayload = () => {
    const legsPayload = legs.map(l => {
      const segmentType = (l.segment || '').toLowerCase();
      const leg = {
        segment: segmentType.toUpperCase(),
        position: l.position.toUpperCase(),
        lots: l.lot || 1,
      };

      if (segmentType === 'options') {
        // Normalize 'call'/'put' UI values to 'CE'/'PE' for the backend
        const rawOpt = (l.option_type || '').toLowerCase();
        leg.option_type = rawOpt === 'call' ? 'CE' : rawOpt === 'put' ? 'PE' : l.option_type.toUpperCase();
        leg.expiry = normalizeExpiryForIndex(l.expiry, instrument, 'options').toUpperCase();
        leg.strike_interval = normalizeStrikeInterval(l.strike_interval);
        leg.strike_selection = {
          type: l.strike_criteria.toUpperCase(),
          strike_type: l.strike_type.toUpperCase(),
          strike_interval: normalizeStrikeInterval(l.strike_interval),
          premium: l.premium_value,
          lower: l.premium_min,
          upper: l.premium_max,
        };
        if (l.strike_criteria === 'pct_of_atm') {
          leg.strike_selection.value = Number(l.pct_value) || 0;
          leg.strike_selection.direction = String(l.pct_direction || '-');
        }
        if (l.strike_criteria === 'atm_straddle_prem_pct') {
          leg.strike_selection.value = Number(l.atm_straddle_prem_pct) || 0;
        }
        if (l.strike_criteria === 'straddle_width') {
          leg.straddle_multiplier = l.straddle_multiplier ?? 0.5;
          leg.straddle_direction = l.straddle_direction ?? '+';
        }
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

    const allFuturesNextMonthly = (
      legsPayload.length > 0 &&
      legsPayload.every(l => String(l.segment || '').toUpperCase() === 'FUTURES') &&
      legsPayload.some(l => String(l.expiry || '').toLowerCase() === 'next_monthly')
    );
    const effectiveExpiryType = allFuturesNextMonthly ? 'NEXT_MONTHLY' : expiryBasis.toUpperCase();

    return {
      index: instrument,
      underlying,
      strategy_type: strategyType,
      expiry_window: expiryBasis === 'weekly' ? 'weekly_expiry' : 'monthly_expiry',
      rollover_toggle: rolloverToggle && ['weekly', 'monthly'].includes(expiryBasis),
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
      buffer_strike_apply_to: String(bufferStrikeApplyTo || 'both'),
      buffer_position_above: Boolean(bufferPositionAbove),
      buffer_position_below: Boolean(bufferPositionBelow),
      slippage_pct: Math.max(0, Number(slippagePct) || 0),
      charges_enabled: chargesEnabled,
      legs: legsPayload,
      // Overall SL/TGT - send flat structure with correct field names expected by backend
      overall_sl_type: overallSLEnabled ? overallSLType : null,
      overall_sl_value: overallSLEnabled ? (overallSLValue === '' ? 0 : overallSLValue) : null,
      overall_target_type: overallTgtEnabled ? overallTgtType : null,
      overall_target_value: overallTgtEnabled ? (overallTgtValue === '' ? 0 : overallTgtValue) : null,
      date_from: getApiStartDate(startDate),
      date_to: getApiEndDate(endDate),
      expiry_type: effectiveExpiryType,
      filter: strFilter.enabled ? strFilter.configId : null,
      filter_config: strFilter.enabled ? strFilter.configId : null,
      filter_segments: strFilter.enabled && strFilter.segments ? strFilter.segments : [],
      super_trend_config: (strFilter.enabled && strFilter.configId !== 'custom') ? strFilter.configId : 'None',
      filter_entry_mode: strFilter.enabled ? (strFilter.entryMode || 'dte') : 'dte',
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

    if (hasCurrentExpiryLegs && !rolloverToggle) {
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
      setJobId(data.job_id);
      const queueDepth = Number(data.queue_depth);
      setJobStatusLabel(Number.isFinite(queueDepth) && queueDepth > 0 ? `Queued (${queueDepth} ahead)…` : 'Queued…');
      pollJobStatus(data.job_id);
    } catch (err) {
      setLoading(false);
      setJobStatusLabel('');
      setError(err.message || 'Backtest queue failed');
    }
  }, [legs, loading, startDate, endDate, entryDaysBefore, exitDaysBefore, expiryBasis, rolloverToggle, validateExpiry, buildPayload, pollJobStatus, stopJobPolling]);

  const strikeTypeToIntradayOffset = (strikeType, optType) => {
    if (!strikeType || strikeType === 'atm') return 0;
    const m = String(strikeType).toLowerCase().match(/^(itm|otm)(\d+)$/);
    if (!m) return 0;
    const n = parseInt(m[2], 10);
    const isCE = (optType || '').toUpperCase() === 'CE';
    if (m[1] === 'otm') return isCE ? n : -n;
    return isCE ? -n : n;
  };

  const runIntradayBacktest = useCallback(async () => {
    if (legs.length === 0) { setError('Please add at least one leg'); return; }
    if (loading) return;
    setLoading(true);
    setError(null);
    setResults(null);
    setRawResults(null);
    setDisplayResults(null);
    setSlowPath(false);
    const optLegs = legs.filter(l => l.segment !== 'futures');
    if (optLegs.length === 0) { setError('Intraday mode requires at least one options leg'); setLoading(false); return; }
    if (instrument !== 'NIFTY') { setError('Intraday backtest is currently available for NIFTY only. Import data for other symbols to enable them.'); setLoading(false); return; }
    const payload = {
      symbol: instrument,
      date_from: toApiDate(startDate),
      date_to: toApiDate(endDate),
      entry_time: intradayEntryTime,
      square_off_time: intradaySquareOffTime,
      legs: optLegs.map(l => {
        const optType = l.option_type === 'call' ? 'CE' : 'PE';
        const slMode = (l.stop_loss_mode || 'POINTS').includes('PERCENT') ? 'percent' : 'points';
        const tgtMode = (l.target_mode || 'POINTS').includes('PERCENT') ? 'percent' : 'points';
        return {
          opt_type: optType,
          action: l.position === 'sell' ? 'SELL' : 'BUY',
          strike_selection: { mode: 'ATM', value: strikeTypeToIntradayOffset(l.strike_type, optType) },
          expiry: (l.expiry || 'weekly').toUpperCase(),
          quantity: l.lot || 1,
          sl: l.stop_loss_enabled && l.stop_loss_value != null
            ? { type: slMode, value: Number(l.stop_loss_value) }
            : null,
          target: l.target_enabled && l.target_value != null
            ? { type: tgtMode, value: Number(l.target_value) }
            : null,
        };
      }),
    };
    try {
      const submitRes = await fetch('/api/intraday/backtest', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!submitRes.ok) {
        const errBody = await submitRes.json().catch(() => null);
        throw new Error(errBody?.detail || `Server error (${submitRes.status})`);
      }
      setSlowPath(submitRes.headers.get('X-Slow-Path') === 'true');

      // The intraday API returns either:
      //   200 + Arrow IPC body when result is cached in Redis (synchronous return), or
      //   202 + JSON {"job_id":...} when work was queued — must be polled.
      const submitCt = submitRes.headers.get('content-type') || '';
      if (submitCt.includes('arrow') || submitCt.includes('octet-stream')) {
        const buffer = await submitRes.arrayBuffer();
        const { decodeTradesheet } = await import('../utils/arrowDecoder.js');
        const trades = decodeTradesheet(buffer);
        setResults(trades);
        setDisplayResults(trades);
        return;
      }

      const { job_id } = await submitRes.json();

      // Poll until done or failed
      const POLL_INTERVAL_MS = 800;
      const MAX_POLLS = 150; // ~2 minutes
      let polls = 0;
      while (polls < MAX_POLLS) {
        await new Promise(r => setTimeout(r, POLL_INTERVAL_MS));
        polls++;
        const pollRes = await fetch(`/api/intraday/jobs/${job_id}`);
        const ct = pollRes.headers.get('content-type') || '';
        if (ct.includes('arrow') || ct.includes('octet-stream')) {
          const buffer = await pollRes.arrayBuffer();
          const { decodeTradesheet } = await import('../utils/arrowDecoder.js');
          const trades = decodeTradesheet(buffer);
          setResults(trades);
          setDisplayResults(trades);
          return;
        }
        const body = await pollRes.json().catch(() => ({}));
        if (body.status === 'failed') throw new Error(body.error || 'Backtest failed');
        // still queued/running — keep polling
      }
      throw new Error('Intraday backtest timed out after 2 minutes');
    } catch (err) {
      setError(err.message || 'Intraday backtest failed');
    } finally {
      setLoading(false);
    }
  }, [legs, loading, startDate, endDate, intradayEntryTime, intradaySquareOffTime, instrument]);

  return (
    <div className="min-h-screen bg-base">
      {/* Header */}
      <header className="app-header px-6 py-3">
        <div className="max-w-screen-2xl mx-auto flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="logo-mark"><span>SL</span></div>
            <div className="flex flex-col leading-none">
              <span className="app-name">StrategyLab</span>
              <span className="app-tagline mt-0.5">Options Backtester</span>
            </div>
          </div>
          <div className="flex items-center gap-4 text-sm">
            <div className="flex items-center gap-2">
              <span style={{ fontFamily: 'IBM Plex Mono, monospace', fontSize: '0.7rem', fontWeight: 700, color: 'var(--accent)', letterSpacing: '-0.01em' }}>{instrument}</span>
              <span className="text-muted text-xs">•</span>
              <span style={{ fontFamily: 'IBM Plex Mono, monospace', fontSize: '0.68rem', color: 'var(--text-secondary)' }}>{legs.length} leg{legs.length !== 1 ? 's' : ''}</span>
            </div>
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
        {/* Instrument Strip */}
        <div className="mb-4 bg-surface border border-default rounded-lg">
          <div className="grid grid-cols-1 md:grid-cols-2">
            {INDEX_GROUPS.map(group => (
              <div key={group.key} className="px-4 py-3 border-b md:border-b-0 md:border-r last:border-r-0 border-subtle">
                <div className="mb-2" style={{ fontFamily: 'Outfit, sans-serif', fontSize: '0.6rem', fontWeight: 600, letterSpacing: '0.06em', textTransform: 'uppercase', color: 'var(--text-muted)' }}>{group.title}</div>
                <div className="flex flex-wrap gap-x-1 gap-y-1">
                  {group.symbols.map(symbol => {
                    const cfg = getIndexConfig(symbol);
                    const active = instrument === symbol;
                    return (
                      <button
                        key={symbol}
                        type="button"
                        onClick={() => selectInstrument(symbol)}
                        className="instrument-btn"
                        style={active ? { color: 'var(--accent)', background: 'var(--accent-bg)' } : {}}
                        title={cfg.subtitle}
                      >
                        {symbol}
                      </button>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="grid grid-cols-12 gap-4">

          {/* LEFT COLUMN - Configuration */}
          <div className="col-span-5 space-y-3">
            {/* Configuration Card */}
            <div className="bg-surface rounded-lg border border-default shadow-sm">
                <div className="px-4 py-3 border-b border-subtle">
                  <h3 className="section-heading">Configuration</h3>
            </div>
            <div className="p-4 space-y-4">
                {/* Backtest Mode toggle */}
                <div>
                  <label className="field-label">Backtest Mode</label>
                  <div className="mode-pill">
                    {[{ v: 'eod', label: 'EOD' }, { v: 'intraday', label: 'Intraday' }].map(({ v, label }) => (
                      <button
                        key={v}
                        type="button"
                        onClick={() => setBacktestMode(v)}
                        className={backtestMode === v ? 'active' : ''}
                      >
                        {label}
                      </button>
                    ))}
                  </div>
                </div>

                {/* Intraday-specific timing fields */}
                {backtestMode === 'intraday' && (
                  <>
                    <IntradaySlowPathWarning visible={slowPath} />
                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <label className="field-label">Entry Time</label>
                        <TimeInput value={intradayEntryTime} onChange={setIntradayEntryTime} min="09:15" max="15:14" step={60} />
                      </div>
                      <div>
                        <label className="field-label">Square-off Time</label>
                        <TimeInput value={intradaySquareOffTime} onChange={setIntradaySquareOffTime} min="09:16" max="15:30" step={60} />
                      </div>
                    </div>
                  </>
                )}


                {/* Instrument */}
                <div>
                  <label className="field-label">Index</label>
                  <div className="h-9 px-3 border border-default rounded text-sm bg-base flex items-center justify-between">
                    <span className="font-semibold text-primary">{instrument}</span>
                    <span className="text-xs text-muted">{indexConfig.subtitle}</span>
                  </div>
                </div>

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

                {/* Entry/Exit Days — EOD only */}
                {backtestMode === 'eod' && (
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

                {/* Rollover Toggle — EOD weekly/monthly only */}
                {backtestMode === 'eod' && ['weekly', 'monthly'].includes(expiryBasis) && (
                  <div className="bg-surface shadow-sm border border-default rounded-xl p-4">
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
                        Spot Adjustment
                      </span>
                      <Tooltip text="Exit the trade on the day the closing spot price crosses your set percentage from the entry spot. Rise exits when spot closes above target, Fall exits when spot closes below target, Both exits on either breach." />
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
                            max={spotAdjustmentUnits === 'percent' ? 20 : 10000}
                            step={spotAdjustmentUnits === 'percent' ? 0.25 : 50}
                            value={spotAdjustmentValue}
                            onChange={e => {
                              const nextValue = e.target.value;
                              if (nextValue === '') { setSpotAdjustmentValue(''); return; }
                              const numeric = Number(nextValue);
                              setSpotAdjustmentValue(Number.isNaN(numeric) ? '' : numeric);
                            }}
                            onBlur={() => setSpotAdjustmentValue(prev => clampSpotAdjustmentValue(prev))}
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
                <div>
                  <label className="field-label">Slippage %</label>
                  <div className="flex items-center gap-2">
                    <input
                      type="number"
                      min={0}
                      step={0.01}
                      value={slippagePct}
                      onChange={e => setSlippagePct(e.target.value === '' ? '' : Math.max(0, Number(e.target.value)))}
                      onBlur={() => setSlippagePct(prev => {
                        const numeric = Number(prev);
                        return Number.isFinite(numeric) && numeric >= 0 ? numeric : 0;
                      })}
                      className="w-full h-9 px-3 border border-default rounded text-sm bg-surface"
                    />
                    <span className="text-xs text-muted">%</span>
                  </div>
                  <p className="mt-1 text-xs text-muted">
                    Applied on both entry and exit. Sell gets worse by lower entry and higher exit. Buy gets worse by higher entry and lower exit.
                  </p>
                </div>
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

            {/* Legwise Controls Card */}
            <div className="bg-surface rounded-lg border border-default shadow-sm">
                <div className="px-4 py-3 border-b border-subtle">
                  <h3 className="section-heading">Legwise Controls</h3>
              </div>
              <div className="p-4 space-y-3">
                <div className="flex items-center justify-between">
                  <span className="text-xs text-secondary">Square Off Mode</span>
                  <SegBtn
                    options={[{ value: 'partial', label: 'Partial' }, { value: 'complete', label: 'Complete' }]}
                    value={squareOffMode}
                    onChange={setSquareOffMode}
                    size="sm"
                  />
                </div>

              </div>
            </div>

            {/* Overall Settings Card */}
            <div className="bg-surface rounded-lg border border-default shadow-sm">
                <div className="px-4 py-3 border-b border-subtle">
                  <h3 className="section-heading">Overall Settings</h3>
              </div>
              <div className="p-4 space-y-4">
                {/* Overall Stop Loss */}
                <div>
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-xs font-medium text-secondary">Overall Stop Loss</span>
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
                    <span className="text-xs font-medium text-secondary">Overall Target</span>
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
          <div className="col-span-7 space-y-3">

            {/* ── Top configurator panel ── */}
            <div className="bg-surface rounded-lg border border-default shadow-sm">
              <div className="px-4 py-2.5 border-b border-subtle flex items-center gap-2">
                <h3 className="section-heading">Leg Builder</h3>
                <Tooltip text="Configure your leg settings then click Add Leg." />
              </div>
              <div className="px-4 py-3 flex flex-wrap items-end gap-3">

                {/* Segment */}
                <div>
                  <label className="field-label">Select segments</label>
                  <SegBtn
                    options={[{ value: 'futures', label: 'Futures' }, { value: 'options', label: 'Options' }]}
                    value={draftLeg.segment}
                    onChange={v => setDraftLeg(prev => ({
                      ...prev,
                      segment: v,
                      expiry: normalizeExpiryForIndex(v === 'futures' ? 'monthly' : prev.expiry, instrument, v),
                    }))}
                  />
                </div>

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

                {/* Expiry */}
                <div>
                  <label className="field-label">Expiry</label>
                  <select value={draftLeg.expiry}
                    onChange={e => setDraftLeg(prev => ({ ...prev, expiry: e.target.value }))}
                    className="h-8 px-2 border border-default rounded text-xs bg-surface focus:outline-none focus:ring-2 focus:ring-accent/40 w-36">
                    {(draftLeg.segment === 'options' ? optionExpiryOptions : FUTURES_EXPIRIES).map(opt => (
                      <option key={opt.value} value={opt.value}>{opt.label}</option>
                    ))}
                  </select>
                </div>

                {/* Strike Criteria */}
                {draftLeg.segment === 'options' && (
                  <div>
                    <label className="field-label">Strike Criteria</label>
                    <select value={draftLeg.strike_criteria}
                      onChange={e => setDraftLeg(prev => ({ ...prev, strike_criteria: e.target.value }))}
                      className="h-8 px-2 border border-default rounded text-xs bg-surface text-secondary focus:outline-none focus:ring-2 focus:ring-accent/40 w-44">
                      <option value="strike_type">Strike Type</option>
                      <option value="premium_range">Premium Range</option>
                      <option value="closest_premium">Closest Premium</option>
                      <option value="premium_gte">Premium &gt;=</option>
                      <option value="premium_lte">Premium &lt;=</option>
                      <option value="straddle_width">Straddle Width</option>
                      <option value="pct_of_atm">% of ATM</option>
                      <option value="synthetic_future">Synthetic Future</option>
                      <option value="atm_straddle_prem_pct">ATM Straddle Premium %</option>
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

                    {(draftLeg.strike_criteria === 'closest_premium' || draftLeg.strike_criteria === 'premium_gte' || draftLeg.strike_criteria === 'premium_lte') && (
                      <>
                        <label className="field-label">Premium</label>
                        <input type="number" min={0} placeholder="Premium" value={draftLeg.premium_value || ''}
                          onChange={e => setDraftLeg(prev => ({ ...prev, premium_value: +e.target.value }))}
                          className="w-24 h-8 px-2 border border-default rounded text-xs text-center" />
                      </>
                    )}

                    {draftLeg.strike_criteria === 'pct_of_atm' && (
                      <>
                        <label className="field-label">&nbsp;</label>
                        <div className="flex items-center gap-1 h-8">
                          <span className="text-xs text-muted whitespace-nowrap">ATM</span>
                          <select
                            value={draftLeg.pct_direction ?? '-'}
                            onChange={e => setDraftLeg(prev => ({ ...prev, pct_direction: e.target.value }))}
                            className="h-8 px-2 border border-default rounded text-xs bg-surface"
                          >
                            <option value="-">-</option>
                            <option value="+">+</option>
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
                  </div>
                )}

                {/* Add Leg */}
                <div className="ml-auto">
                  <button type="button" onClick={addLegFromDraft} disabled={legs.length >= 6}
                    className="run-btn add-leg-btn h-9 px-6">
                    <Plus size={13} />
                    Add Leg
                  </button>
                </div>
              </div>
            </div>

            {/* ── Added legs list ── */}
            {legs.length > 0 && (
              <div className="bg-surface rounded-lg border border-default shadow-sm">
                <div className="px-4 py-2.5 border-b border-subtle">
                  <h3 className="section-heading">Legs <span style={{ fontWeight: 400, fontSize: '0.55rem', color: 'var(--text-muted)', marginLeft: '4px' }}>({legs.length}/6)</span></h3>
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
                              : 'FUTURE'}
                          </span>
                          <span style={{ color: 'var(--text-muted)', fontSize: '0.65rem' }}>·</span>
                          <span style={{ fontFamily: 'Outfit, sans-serif', fontSize: '0.68rem', color: 'var(--text-secondary)', textTransform: 'capitalize' }}>{leg.expiry}</span>
                        </div>
                        <div className="flex items-center gap-3">
                          <span style={{ fontFamily: 'IBM Plex Mono, monospace', fontSize: '0.65rem', fontWeight: 700, color: 'var(--accent)' }}>{leg.lot * getLotSize(instrument, startDate)} units</span>
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
                          <div>
                            <label className="field-label">Segment</label>
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
                          <div>
                            <label className="field-label">Lots</label>
                            <input type="number" min={1} value={leg.lot}
                              onChange={e => updateLeg(leg.id, 'lot', parseInt(e.target.value) || 1)}
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
                          <div>
                            <label className="field-label">Expiry</label>
                            <select value={leg.expiry} onChange={e => updateLeg(leg.id, 'expiry', e.target.value)}
                              className="h-7 px-2 border border-default rounded text-xs bg-surface w-28">
                              {(leg.segment === 'options' ? optionExpiryOptions : FUTURES_EXPIRIES).map(opt => (
                                <option key={opt.value} value={opt.value}>{opt.label}</option>
                              ))}
                            </select>
                          </div>
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
                                  <option value="straddle_width">Straddle Width</option>
                                  <option value="pct_of_atm">% of ATM</option>
                                  <option value="synthetic_future">Synthetic Future</option>
                                  <option value="atm_straddle_prem_pct">ATM Straddle Premium %</option>
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
                              {backtestMode === 'eod' && (
                                <div>
                                  <label className="field-label">Strike Gap</label>
                                  <StrikeIntervalSelect
                                    value={leg.strike_interval}
                                    onChange={value => updateLeg(leg.id, 'strike_interval', value)}
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

                                  {(leg.strike_criteria === 'closest_premium' || leg.strike_criteria === 'premium_gte' || leg.strike_criteria === 'premium_lte') && (
                                    <>
                                      <label className="field-label">Premium</label>
                                      <input type="number" min={0} placeholder="Premium" value={leg.premium_value || ''}
                                        onChange={e => updateLeg(leg.id, 'premium_value', +e.target.value)}
                                        className="w-20 h-7 px-1 border border-default rounded text-xs text-center" />
                                    </>
                                  )}

                                  {leg.strike_criteria === 'pct_of_atm' && (
                                    <>
                                      <label className="field-label">&nbsp;</label>
                                      <div className="flex items-center gap-1">
                                        <span className="text-xs text-muted whitespace-nowrap">ATM</span>
                                        <select
                                          value={leg.pct_direction ?? '-'}
                                          onChange={e => updateLeg(leg.id, 'pct_direction', e.target.value)}
                                          className="h-7 px-2 border border-default rounded text-xs bg-surface"
                                        >
                                          <option value="-">-</option>
                                          <option value="+">+</option>
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
                                </div>
                              )}
                            </>
                          )}
                        </div>

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
                              <Toggle enabled={leg.stop_loss_enabled} onToggle={(val) => updateLeg(leg.id, 'stop_loss_enabled', val !== undefined ? Boolean(val) : !leg.stop_loss_enabled)} size="sm" />
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
                                <select value={ll.target_mode || 'POINTS'} onChange={e => updateLazyLeg(ll.id, 'target_mode', e.target.value)} className="h-7 px-1 border border-default rounded text-xs bg-surface">
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
                                <select value={ll.stop_loss_mode || 'POINTS'} onChange={e => updateLazyLeg(ll.id, 'stop_loss_mode', e.target.value)} className="h-7 px-1 border border-default rounded text-xs bg-surface">
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
                            <select value={ll.target_mode || 'POINTS'} onChange={e => updateLazyLeg(ll.id, 'target_mode', e.target.value)} className="h-7 px-1 border border-default rounded text-xs bg-surface">
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
                            <select value={ll.stop_loss_mode || 'POINTS'} onChange={e => updateLazyLeg(ll.id, 'stop_loss_mode', e.target.value)} className="h-7 px-1 border border-default rounded text-xs bg-surface">
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
                            <select value={ll.trail_sl_mode || 'POINTS'} onChange={e => updateLazyLeg(ll.id, 'trail_sl_mode', e.target.value)} className="w-16 h-7 px-1 border border-default rounded text-xs bg-surface">
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
          <div className="mt-4">
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
              showStrSegment={strFilter.enabled}
            />
            <div className="flex flex-wrap items-center gap-4 px-4 py-3 bg-surface border border-default border-t-0 rounded-b-xl">
              <div className="flex items-center gap-2">
                <label className="text-xs font-medium text-secondary whitespace-nowrap">
                  Slippage (%)
                </label>
                <input
                  type="number"
                  min="0"
                  max="10"
                  step="0.1"
                  value={slippagePct}
                  onChange={e => setSlippagePct(e.target.value === '' ? '' : Math.max(0, Number(e.target.value)))}
                  onBlur={() => setSlippagePct(prev => Number(prev) || 0)}
                  className="w-20 px-2 py-1.5 text-sm border border-default rounded bg-surface text-primary text-center"
                  placeholder="0"
                />
              </div>
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
              {(displayResults?.meta?.slippage_pct > 0 || displayResults?.meta?.charges_enabled) && (
                <span className="text-xs text-secondary">
                  {displayResults.meta.slippage_pct > 0 && (
                    <>Applied: <strong>{displayResults.meta.slippage_pct}%</strong> slippage</>
                  )}
                  {displayResults.meta.slippage_pct > 0 && displayResults.meta.charges_enabled && ' + '}
                  {displayResults.meta.charges_enabled && <strong>Txn charges</strong>}
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

        {/* Run Backtest Button — fixed bottom-center */}
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50 flex flex-col items-center gap-2">
          <button
            onClick={backtestMode === 'intraday' ? runIntradayBacktest : runBacktest}
            disabled={!canRunBacktest}
            className="run-btn px-10 py-3 rounded-full"
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
          {(jobStatusLabel || cacheWarmLabel) && (
            <div style={{ fontFamily: 'IBM Plex Mono, monospace', fontSize: '0.6rem', color: 'var(--text-secondary)', letterSpacing: '0.04em', textAlign: 'center' }}>
              {jobStatusLabel || cacheWarmLabel}
            </div>
          )}
        </div>
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
