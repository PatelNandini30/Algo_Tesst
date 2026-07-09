import React, { useEffect, useRef, useState } from 'react';
import { ChevronDown } from 'lucide-react';

/**
 * Minimal header-style dropdown, styled to match the app-header buttons
 * (Theme Toggle / Save). `options` is [{ value, label, sublabel? }].
 * Controlled: `value` + `onChange(value)`.
 */
const Dropdown = ({ label, value, options, onChange, minWidth = 150 }) => {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    if (!open) return;
    const onDocClick = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener('mousedown', onDocClick);
    return () => document.removeEventListener('mousedown', onDocClick);
  }, [open]);

  const selected = options.find((o) => o.value === value) || options[0];

  return (
    <div ref={ref} style={{ position: 'relative', display: 'inline-block' }}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        title={label}
        className="flex items-center gap-2 px-3 py-1.5 rounded-md transition-all"
        style={{
          fontFamily: 'Outfit, sans-serif', fontSize: '0.6rem', fontWeight: 700,
          letterSpacing: '0.08em', textTransform: 'uppercase',
          color: 'var(--text-secondary)', background: 'var(--bg-elevated)',
          border: `1px solid ${open ? 'var(--accent)' : 'var(--border-default)'}`,
          cursor: 'pointer', minWidth,
        }}
      >
        <span style={{ opacity: 0.7 }}>{label}</span>
        <span style={{ color: 'var(--text-primary)', fontWeight: 700 }}>
          {selected ? selected.label : '—'}
        </span>
        <ChevronDown size={12} />
      </button>
      {open && (
        <div
          style={{
            position: 'absolute', top: '100%', right: 0, marginTop: 4, zIndex: 50,
            minWidth: Math.max(minWidth, 220), background: 'var(--bg-surface)',
            border: '1px solid var(--border-default)', borderRadius: 8,
            boxShadow: '0 12px 32px rgba(0,0,0,0.25)', overflow: 'hidden',
          }}
        >
          {options.map((opt) => (
            <button
              key={opt.value ?? 'null'}
              type="button"
              disabled={opt.disabled}
              onClick={() => { if (opt.disabled) return; onChange(opt.value); setOpen(false); }}
              className="flex items-center justify-between w-full"
              style={{
                padding: '8px 12px', textAlign: 'left', background: opt.value === value ? 'var(--bg-elevated)' : 'transparent',
                border: 0, cursor: opt.disabled ? 'not-allowed' : 'pointer', fontFamily: 'Outfit, sans-serif', fontSize: '0.72rem',
                color: opt.disabled ? 'var(--text-muted)' : 'var(--text-primary)',
                opacity: opt.disabled ? 0.6 : 1,
              }}
              onMouseEnter={(e) => { if (!opt.disabled) e.currentTarget.style.background = 'var(--bg-elevated)'; }}
              onMouseLeave={(e) => { e.currentTarget.style.background = opt.value === value ? 'var(--bg-elevated)' : 'transparent'; }}
            >
              <span>{opt.label}</span>
              {opt.sublabel && (
                <span style={{ fontFamily: 'IBM Plex Mono, monospace', fontSize: '0.62rem', color: opt.disabled ? 'var(--danger, #e5484d)' : 'var(--text-secondary)', marginLeft: 10 }}>
                  {opt.sublabel}
                </span>
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  );
};

export default Dropdown;
