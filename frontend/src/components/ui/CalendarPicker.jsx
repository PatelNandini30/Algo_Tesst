import React, { useState, useRef, useEffect, useCallback } from 'react';
import { Calendar, ChevronLeft, ChevronRight } from 'lucide-react';

/* ─── Constants ─────────────────────────────────────────────────────────── */
const MONTHS = ['January','February','March','April','May','June','July','August','September','October','November','December'];
const DAYS   = ['Su','Mo','Tu','We','Th','Fr','Sa'];
const SEGS   = ['dd', 'mm', 'yyyy'];

/* ─── Helpers ────────────────────────────────────────────────────────────── */
const pad = (n, len) => String(n).padStart(len, '0');

const parseValue = (v) => {
  if (!v) return null;
  const p = v.split('/');
  if (p.length !== 3) return null;
  const d = parseInt(p[0]), m = parseInt(p[1]) - 1, y = parseInt(p[2]);
  if (isNaN(d) || isNaN(m) || isNaN(y) || y < 1900 || y > 2100) return null;
  const dt = new Date(y, m, d);
  if (dt.getFullYear() !== y || dt.getMonth() !== m || dt.getDate() !== d) return null;
  return dt;
};

const fmtDate = (d) =>
  `${pad(d.getDate(),2)}/${pad(d.getMonth()+1,2)}/${d.getFullYear()}`;

const valueToSegs = (v) => {
  const d = parseValue(v);
  if (!d) return { dd: null, mm: null, yyyy: null };
  return { dd: d.getDate(), mm: d.getMonth() + 1, yyyy: d.getFullYear() };
};

const segsToDate = (s) => {
  if (s.dd === null || s.mm === null || s.yyyy === null) return null;
  const dt = new Date(s.yyyy, s.mm - 1, s.dd);
  if (dt.getFullYear() !== s.yyyy || dt.getMonth() !== s.mm - 1 || dt.getDate() !== s.dd) return null;
  return dt;
};

// First digit that forces immediate single-digit commit (no valid 2-digit value starts with this digit)
const isImmediateDigit = (seg, d) => {
  if (seg === 'dd') return d >= 4;  // days: max 31, so 4x-9x invalid
  if (seg === 'mm') return d >= 2;  // months: max 12, so 2x-9x invalid
  return false;
};

const clamp = (seg, val) => {
  const mins = { dd: 1, mm: 1, yyyy: 1990 };
  const maxs = { dd: 31, mm: 12, yyyy: 2100 };
  return Math.max(mins[seg], Math.min(maxs[seg], val));
};

const navBtn = {
  width: 28, height: 28, borderRadius: 6, border: 'none',
  background: 'transparent', cursor: 'pointer', display: 'flex',
  alignItems: 'center', justifyContent: 'center',
  color: 'var(--text-secondary)', transition: 'background 0.12s ease, color 0.12s ease',
  flexShrink: 0,
};

/* ─── Component ──────────────────────────────────────────────────────────── */
export default function CalendarPicker({ value, onChange }) {
  const [segs,       setSegs]       = useState(() => valueToSegs(value));
  const [activeSeg,  setActiveSeg]  = useState(null);
  const [buffer,     setBuffer]     = useState('');
  const [open,       setOpen]       = useState(false);
  const [openUp,     setOpenUp]     = useState(false);
  const [viewYear,   setViewYear]   = useState(() => parseValue(value)?.getFullYear()  ?? new Date().getFullYear());
  const [viewMonth,  setViewMonth]  = useState(() => parseValue(value)?.getMonth()     ?? new Date().getMonth());
  const [pickYear,   setPickYear]   = useState(false);
  const wrapRef = useRef(null);

  /* Sync state when prop changes externally */
  useEffect(() => {
    if (activeSeg !== null) return;
    setSegs(valueToSegs(value));
    const d = parseValue(value);
    if (d) { setViewYear(d.getFullYear()); setViewMonth(d.getMonth()); }
  }, [value]); // eslint-disable-line

  /* Outside click */
  useEffect(() => {
    const down = (e) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) {
        flushBuffer();
        setActiveSeg(null);
        setBuffer('');
        setOpen(false);
        setPickYear(false);
      }
    };
    document.addEventListener('mousedown', down);
    return () => document.removeEventListener('mousedown', down);
  });

  /* ── Commit helpers ──── */
  const applySegs = useCallback((next) => {
    setSegs(next);
    const dt = segsToDate(next);
    if (dt) {
      const str = fmtDate(dt);
      onChange(str);
      setViewYear(dt.getFullYear());
      setViewMonth(dt.getMonth());
    }
    return next;
  }, [onChange]);

  const commitSegVal = useCallback((seg, raw, curSegs) => {
    const num = parseInt(raw);
    if (isNaN(num) || raw === '') return curSegs;
    return applySegs({ ...curSegs, [seg]: clamp(seg, num) });
  }, [applySegs]);

  const flushBuffer = useCallback(() => {
    if (activeSeg && buffer !== '') {
      commitSegVal(activeSeg, buffer, segs);
    }
  }, [activeSeg, buffer, segs, commitSegVal]);

  /* ── Segment click ──── */
  const activateSeg = (seg, curSegs) => {
    setActiveSeg(seg);
    setBuffer('');
    setSegs(curSegs);
    wrapRef.current?.focus();
  };

  const handleSegClick = (e, seg) => {
    e.preventDefault();
    e.stopPropagation();
    const flushed = activeSeg && buffer !== '' ? commitSegVal(activeSeg, buffer, segs) : segs;
    activateSeg(seg, flushed);
  };

  /* ── Keyboard ──── */
  const handleKeyDown = (e) => {
    if (!activeSeg) return;
    const key = e.key;
    const idx = SEGS.indexOf(activeSeg);

    if (/^\d$/.test(key)) {
      e.preventDefault();
      const digit = parseInt(key);
      const newBuf = buffer + key;
      const maxLen = activeSeg === 'yyyy' ? 4 : 2;

      if (buffer === '' && isImmediateDigit(activeSeg, digit)) {
        // Single-digit commit, advance
        const next = commitSegVal(activeSeg, key, segs);
        setBuffer('');
        setActiveSeg(idx < SEGS.length - 1 ? SEGS[idx + 1] : null);
        setSegs(next);
        return;
      }

      if (newBuf.length < maxLen) {
        setBuffer(newBuf);
      } else {
        // Buffer full
        const next = commitSegVal(activeSeg, newBuf, segs);
        setBuffer('');
        setActiveSeg(idx < SEGS.length - 1 ? SEGS[idx + 1] : null);
        setSegs(next);
      }
      return;
    }

    if (key === 'Backspace') {
      e.preventDefault();
      if (buffer.length > 0) { setBuffer(b => b.slice(0, -1)); return; }
      const next = applySegs({ ...segs, [activeSeg]: null });
      setSegs(next);
      if (idx > 0) { setActiveSeg(SEGS[idx - 1]); setBuffer(''); }
      return;
    }

    if (key === 'ArrowLeft' || (key === 'Tab' && e.shiftKey)) {
      e.preventDefault();
      if (buffer !== '') { const n = commitSegVal(activeSeg, buffer, segs); setSegs(n); setBuffer(''); }
      setActiveSeg(idx > 0 ? SEGS[idx - 1] : null);
      return;
    }

    if (key === 'ArrowRight' || (key === 'Tab' && !e.shiftKey)) {
      e.preventDefault();
      if (buffer !== '') { const n = commitSegVal(activeSeg, buffer, segs); setSegs(n); setBuffer(''); }
      setActiveSeg(idx < SEGS.length - 1 ? SEGS[idx + 1] : null);
      return;
    }

    if (key === 'ArrowUp' || key === 'ArrowDown') {
      e.preventDefault();
      const delta = key === 'ArrowUp' ? 1 : -1;
      const cur = buffer ? parseInt(buffer) : (segs[activeSeg] ?? (delta > 0 ? 0 : 0));
      const next = applySegs({ ...segs, [activeSeg]: clamp(activeSeg, (cur || 0) + delta) });
      setSegs(next); setBuffer('');
      return;
    }

    if (key === 'Enter') { e.preventDefault(); if (buffer !== '') { const n = commitSegVal(activeSeg, buffer, segs); setSegs(n); } setActiveSeg(null); setBuffer(''); return; }
    if (key === 'Escape') { setBuffer(''); setActiveSeg(null); setOpen(false); return; }
  };

  /* ── Calendar ──── */
  const openCalendar = () => {
    if (wrapRef.current) {
      const r = wrapRef.current.getBoundingClientRect();
      setOpenUp(window.innerHeight - r.bottom < 330);
    }
    setOpen(true); setPickYear(false);
  };

  const handleDay = (day) => {
    const dt = new Date(viewYear, viewMonth, day);
    onChange(fmtDate(dt));
    setSegs({ dd: dt.getDate(), mm: dt.getMonth() + 1, yyyy: dt.getFullYear() });
    setActiveSeg(null); setBuffer(''); setOpen(false); setPickYear(false);
  };

  /* ── Display ──── */
  const today = new Date();
  const selected    = parseValue(value);
  const daysInMonth = new Date(viewYear, viewMonth + 1, 0).getDate();
  const firstWkday  = new Date(viewYear, viewMonth, 1).getDay();
  const yearRange   = Array.from({ length: 32 }, (_, i) => today.getFullYear() - 10 + i);
  const prevMonth   = () => viewMonth === 0 ? (setViewMonth(11), setViewYear(y => y-1)) : setViewMonth(m => m-1);
  const nextMonth   = () => viewMonth === 11 ? (setViewMonth(0), setViewYear(y => y+1)) : setViewMonth(m => m+1);
  const isSel = (d) => selected && selected.getFullYear()===viewYear && selected.getMonth()===viewMonth && selected.getDate()===d;
  const isTod = (d) => today.getFullYear()===viewYear && today.getMonth()===viewMonth && today.getDate()===d;

  const isFocused = activeSeg !== null;

  const renderSeg = (seg) => {
    const isActive = activeSeg === seg;
    const padLen = seg === 'yyyy' ? 4 : 2;
    const placeholder = seg.toUpperCase();

    let display;
    if (isActive) {
      display = buffer || '';
    } else {
      display = segs[seg] !== null ? pad(segs[seg], padLen) : null;
    }

    const isEmpty = display === null || display === '';

    return (
      <span
        key={seg}
        onMouseDown={e => handleSegClick(e, seg)}
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          justifyContent: 'center',
          minWidth: seg === 'yyyy' ? 32 : 18,
          height: 20,
          padding: '0 2px',
          borderRadius: 3,
          background: isActive ? 'var(--accent)' : 'transparent',
          color: isActive
            ? '#fff'
            : !isEmpty
              ? 'var(--text-primary)'
              : 'var(--text-muted)',
          fontFamily: 'Outfit, sans-serif',
          fontSize: '0.8rem',
          fontWeight: isActive ? 600 : 500,
          letterSpacing: '0.01em',
          cursor: 'default',
          userSelect: 'none',
          transition: 'background 0.1s',
          whiteSpace: 'pre',
        }}
      >
        {isEmpty ? placeholder : display}
      </span>
    );
  };

  return (
    <div
      ref={wrapRef}
      tabIndex={0}
      onKeyDown={handleKeyDown}
      onBlur={(e) => {
        if (!wrapRef.current?.contains(e.relatedTarget)) {
          if (activeSeg && buffer !== '') commitSegVal(activeSeg, buffer, segs);
          setActiveSeg(null);
          setBuffer('');
        }
      }}
      style={{ position: 'relative', display: 'inline-block', outline: 'none' }}
    >
      {/* ── Input row ── */}
      <div style={{
        display: 'inline-flex',
        alignItems: 'center',
        height: 38,
        paddingLeft: 10,
        paddingRight: 12,
        gap: 6,
        borderRadius: 8,
        border: `1.5px solid ${isFocused || open ? 'var(--accent)' : 'var(--border-default)'}`,
        background: 'var(--bg-input)',
        boxShadow: isFocused || open ? '0 0 0 3px var(--accent-glow)' : '0 1px 2px rgba(0,0,0,0.04)',
        transition: 'border-color 0.15s, box-shadow 0.15s',
        cursor: 'default',
        userSelect: 'none',
      }}>
        {/* Calendar icon — opens popup */}
        <button
          type="button"
          tabIndex={-1}
          onMouseDown={e => e.preventDefault()}
          onClick={(e) => { e.stopPropagation(); open ? (setOpen(false), setPickYear(false)) : openCalendar(); }}
          style={{
            background: 'transparent', border: 'none', cursor: 'pointer',
            display: 'flex', alignItems: 'center', padding: 0, flexShrink: 0,
          }}
        >
          <Calendar size={13} style={{ color: 'var(--accent)' }} />
        </button>

        {/* Segments: DD / MM / YYYY */}
        <div style={{ display: 'inline-flex', alignItems: 'center', gap: 0 }}>
          {renderSeg('dd')}
          <span style={{ color: 'var(--border-strong)', fontSize: '0.8rem', fontFamily: 'Outfit', userSelect: 'none', padding: '0 1px' }}>/</span>
          {renderSeg('mm')}
          <span style={{ color: 'var(--border-strong)', fontSize: '0.8rem', fontFamily: 'Outfit', userSelect: 'none', padding: '0 1px' }}>/</span>
          {renderSeg('yyyy')}
        </div>
      </div>

      {/* ── Calendar popup ── */}
      {open && (
        <div style={{
          position: 'absolute',
          ...(openUp ? { bottom: 44 } : { top: 44 }),
          left: 0, zIndex: 9999,
          background: 'var(--bg-surface)',
          border: '1px solid var(--border-default)',
          borderRadius: 14,
          boxShadow: '0 16px 48px rgba(0,0,0,0.13), 0 2px 8px rgba(0,0,0,0.07)',
          padding: 14, width: 264,
          animation: `${openUp ? 'fadeSlideDown' : 'fadeSlideUp'} 0.14s ease-out both`,
        }}>
          {pickYear ? (
            <>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
                <span style={{ fontFamily: 'Outfit', fontWeight: 700, fontSize: '0.85rem', color: 'var(--text-primary)' }}>Select Year</span>
                <button tabIndex={-1} onMouseDown={e=>e.preventDefault()} onClick={() => setPickYear(false)} style={{ ...navBtn, color: 'var(--accent)' }}>✕</button>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 4, maxHeight: 200, overflowY: 'auto' }}>
                {yearRange.map(yr => (
                  <button key={yr} tabIndex={-1} onMouseDown={e=>e.preventDefault()} onClick={() => { setViewYear(yr); setPickYear(false); }} style={{
                    padding: '6px 0', borderRadius: 6, border: 'none', cursor: 'pointer',
                    background: yr === viewYear ? 'var(--accent)' : yr === today.getFullYear() ? 'var(--accent-bg)' : 'transparent',
                    color: yr === viewYear ? '#fff' : yr === today.getFullYear() ? 'var(--accent)' : 'var(--text-primary)',
                    fontFamily: 'IBM Plex Mono, monospace', fontSize: '0.72rem', fontWeight: yr === viewYear ? 600 : 400,
                    transition: 'background 0.1s',
                  }}>{yr}</button>
                ))}
              </div>
            </>
          ) : (
            <>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
                <button tabIndex={-1} onMouseDown={e=>e.preventDefault()} onClick={prevMonth} style={navBtn}
                  onMouseEnter={e => { e.currentTarget.style.background='var(--bg-hover)'; e.currentTarget.style.color='var(--text-primary)'; }}
                  onMouseLeave={e => { e.currentTarget.style.background='transparent'; e.currentTarget.style.color='var(--text-secondary)'; }}>
                  <ChevronLeft size={14} />
                </button>
                <button tabIndex={-1} onMouseDown={e=>e.preventDefault()} onClick={() => setPickYear(true)} style={{
                  fontFamily: 'Outfit, sans-serif', fontWeight: 700, fontSize: '0.85rem',
                  color: 'var(--text-primary)', background: 'transparent', border: 'none',
                  cursor: 'pointer', padding: '2px 8px', borderRadius: 6, transition: 'background 0.12s',
                }}
                  onMouseEnter={e => e.currentTarget.style.background='var(--bg-hover)'}
                  onMouseLeave={e => e.currentTarget.style.background='transparent'}
                >{MONTHS[viewMonth]} {viewYear}</button>
                <button tabIndex={-1} onMouseDown={e=>e.preventDefault()} onClick={nextMonth} style={navBtn}
                  onMouseEnter={e => { e.currentTarget.style.background='var(--bg-hover)'; e.currentTarget.style.color='var(--text-primary)'; }}
                  onMouseLeave={e => { e.currentTarget.style.background='transparent'; e.currentTarget.style.color='var(--text-secondary)'; }}>
                  <ChevronRight size={14} />
                </button>
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)', gap: 2, marginBottom: 4 }}>
                {DAYS.map(d => (
                  <div key={d} style={{ textAlign: 'center', fontFamily: 'Outfit', fontSize: '0.58rem', fontWeight: 700, letterSpacing: '0.06em', color: 'var(--text-muted)', paddingBottom: 4 }}>{d}</div>
                ))}
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)', gap: 2 }}>
                {Array(firstWkday).fill(null).map((_, i) => <div key={`e${i}`} />)}
                {Array.from({ length: daysInMonth }, (_, i) => i + 1).map(day => {
                  const sel = isSel(day), tod = isTod(day);
                  return (
                    <button key={day} tabIndex={-1}
                      onMouseDown={e => e.preventDefault()}
                      onClick={() => handleDay(day)}
                      style={{
                        height: 32, width: '100%', borderRadius: 7, border: 'none',
                        background: sel ? 'var(--accent)' : tod ? 'var(--accent-bg)' : 'transparent',
                        color: sel ? '#fff' : tod ? 'var(--accent)' : 'var(--text-primary)',
                        fontFamily: 'IBM Plex Mono, monospace', fontSize: '0.75rem',
                        fontWeight: sel ? 600 : 400, cursor: 'pointer', transition: 'all 0.1s',
                        boxShadow: sel ? '0 2px 8px rgba(37,99,235,0.3)' : 'none',
                      }}
                      onMouseEnter={e => { if (!sel) e.currentTarget.style.background = 'var(--bg-hover)'; }}
                      onMouseLeave={e => { if (!sel) e.currentTarget.style.background = tod ? 'var(--accent-bg)' : 'transparent'; }}
                    >{day}</button>
                  );
                })}
              </div>

              <div style={{ marginTop: 10, paddingTop: 10, borderTop: '1px solid var(--border-subtle)', textAlign: 'center' }}>
                <button tabIndex={-1} onMouseDown={e=>e.preventDefault()}
                  onClick={() => { onChange(fmtDate(today)); setSegs({ dd: today.getDate(), mm: today.getMonth()+1, yyyy: today.getFullYear() }); setOpen(false); }}
                  style={{ fontFamily: 'Outfit', fontSize: '0.65rem', fontWeight: 600, color: 'var(--accent)', background: 'transparent', border: 'none', cursor: 'pointer', letterSpacing: '0.04em' }}>
                  Today — {fmtDate(today)}
                </button>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
