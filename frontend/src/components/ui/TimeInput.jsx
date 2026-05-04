import React, { useState, useRef } from 'react';
import { Clock } from 'lucide-react';

const pad = (n) => String(n).padStart(2, '0');

const parseTime = (val) => {
  if (!val) return { h: 9, m: 15 };
  const [hStr, mStr] = val.split(':');
  return { h: parseInt(hStr) || 0, m: parseInt(mStr) || 0 };
};

const fmt24 = (h, m) => `${pad(h)}:${pad(m)}`;

export default function TimeInput({ value, onChange, min, max, step = 60 }) {
  const [focused, setFocused] = useState(false);
  const [hRaw, setHRaw] = useState(null);
  const [mRaw, setMRaw] = useState(null);
  const hRef = useRef(null);
  const mRef = useRef(null);

  const { h, m } = parseTime(value);
  const period = h >= 12 ? 'PM' : 'AM';
  const h12 = h % 12 === 0 ? 12 : h % 12;

  const clamp = (newH, newM) => {
    let total = newH * 60 + newM;
    if (min) { const [mh, mm] = min.split(':').map(Number); total = Math.max(total, mh * 60 + mm); }
    if (max) { const [mh, mm] = max.split(':').map(Number); total = Math.min(total, mh * 60 + mm); }
    onChange(fmt24(Math.floor(total / 60), total % 60));
  };

  const stepMins = step / 60 || 1;
  const incHour = () => clamp(h + 1, m);
  const decHour = () => clamp(h - 1, m);
  const incMin  = () => clamp(h, m + stepMins);
  const decMin  = () => clamp(h, m - stepMins);
  const togglePeriod = () => clamp(h >= 12 ? h - 12 : h + 12, m);

  const commitHour = (raw) => {
    const parsed = parseInt(raw);
    if (!isNaN(parsed)) {
      const h24 = period === 'PM'
        ? (parsed === 12 ? 12 : parsed + 12)
        : (parsed === 12 ? 0 : parsed);
      clamp(Math.max(0, Math.min(23, h24)), m);
    }
    setHRaw(null);
  };

  const commitMin = (raw) => {
    const parsed = parseInt(raw);
    if (!isNaN(parsed)) clamp(h, Math.max(0, Math.min(59, parsed)));
    setMRaw(null);
  };

  const arrowBtn = {
    background: 'transparent', border: 'none', cursor: 'pointer',
    color: 'var(--text-muted)', padding: '1px 3px', lineHeight: 1,
    fontSize: '0.58rem', display: 'block', transition: 'color 0.1s',
  };

  const inputStyle = {
    fontFamily: 'IBM Plex Mono, monospace', fontSize: '0.85rem', fontWeight: 500,
    color: 'var(--text-primary)', background: 'transparent', border: 'none',
    outline: 'none', width: 22, textAlign: 'center', padding: 0,
    caretColor: 'var(--accent)',
  };

  const col = (inputEl, onUp, onDown) => (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 0 }}>
      <button style={arrowBtn} onClick={onUp} type="button"
        onMouseDown={e => e.preventDefault()}
        onMouseEnter={e => e.currentTarget.style.color='var(--accent)'}
        onMouseLeave={e => e.currentTarget.style.color='var(--text-muted)'}>▲</button>
      {inputEl}
      <button style={arrowBtn} onClick={onDown} type="button"
        onMouseDown={e => e.preventDefault()}
        onMouseEnter={e => e.currentTarget.style.color='var(--accent)'}
        onMouseLeave={e => e.currentTarget.style.color='var(--text-muted)'}>▼</button>
    </div>
  );

  return (
    <div
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 8,
        height: 38, padding: '0 10px 0 12px',
        borderRadius: 8,
        border: `1.5px solid ${focused ? 'var(--accent)' : 'var(--border-default)'}`,
        background: 'var(--bg-input)',
        boxShadow: focused ? '0 0 0 3px var(--accent-glow)' : '0 1px 2px rgba(0,0,0,0.04)',
        transition: 'all 0.15s ease',
        cursor: 'text',
        minWidth: 148,
      }}
    >
      <Clock size={13} style={{ color: 'var(--accent)', flexShrink: 0 }} />
      <div style={{ display: 'flex', alignItems: 'center', gap: 4, flex: 1 }}>
        {col(
          <input
            ref={hRef}
            type="text"
            inputMode="numeric"
            style={inputStyle}
            value={hRaw !== null ? hRaw : pad(h12)}
            onFocus={() => { setFocused(true); setHRaw(pad(h12)); hRef.current?.select(); }}
            onBlur={() => { commitHour(hRaw ?? pad(h12)); setFocused(false); }}
            onChange={e => setHRaw(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Enter') { commitHour(hRaw ?? pad(h12)); mRef.current?.focus(); }
              if (e.key === 'ArrowUp') { e.preventDefault(); incHour(); }
              if (e.key === 'ArrowDown') { e.preventDefault(); decHour(); }
              if (e.key === 'Tab' && !e.shiftKey) { e.preventDefault(); commitHour(hRaw ?? pad(h12)); mRef.current?.focus(); }
            }}
          />,
          incHour, decHour
        )}
        <span style={{ fontFamily: 'IBM Plex Mono, monospace', fontSize: '0.85rem', fontWeight: 500, color: 'var(--text-muted)', marginBottom: 1 }}>:</span>
        {col(
          <input
            ref={mRef}
            type="text"
            inputMode="numeric"
            style={inputStyle}
            value={mRaw !== null ? mRaw : pad(m)}
            onFocus={() => { setFocused(true); setMRaw(pad(m)); mRef.current?.select(); }}
            onBlur={() => { commitMin(mRaw ?? pad(m)); setFocused(false); }}
            onChange={e => setMRaw(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Enter') commitMin(mRaw ?? pad(m));
              if (e.key === 'ArrowUp') { e.preventDefault(); incMin(); }
              if (e.key === 'ArrowDown') { e.preventDefault(); decMin(); }
            }}
          />,
          incMin, decMin
        )}
        <button onClick={togglePeriod} type="button" style={{
          marginLeft: 4, padding: '2px 6px', borderRadius: 5,
          fontFamily: 'Outfit, sans-serif', fontSize: '0.6rem', fontWeight: 700,
          letterSpacing: '0.06em', border: '1px solid var(--border-default)',
          background: 'var(--accent-bg)', color: 'var(--accent)', cursor: 'pointer',
          transition: 'all 0.12s',
        }}>
          {period}
        </button>
      </div>
    </div>
  );
}
