import React from 'react';

const Toggle = ({ enabled, onToggle, size = 'md' }) => {
  const isSmall = size === 'sm';

  const trackW = isSmall ? 28 : 36;
  const trackH = isSmall ? 16 : 20;
  const dotSize = isSmall ? 10 : 14;
  const dotOff = 3;
  const dotOn = trackW - dotSize - dotOff;

  return (
    <button
      type="button"
      onClick={() => onToggle(!enabled)}
      style={{
        position: 'relative',
        display: 'inline-flex',
        alignItems: 'center',
        width: trackW,
        height: trackH,
        flexShrink: 0,
        borderRadius: trackH,
        border: `1.5px solid ${enabled ? 'var(--accent)' : 'var(--border-strong)'}`,
        background: enabled
          ? 'var(--accent)'
          : 'var(--bg-elevated)',
        cursor: 'pointer',
        transition: 'background 0.18s ease, border-color 0.18s ease',
        outline: 'none',
        boxShadow: enabled
          ? '0 0 0 3px var(--accent-glow)'
          : 'inset 0 1px 3px rgba(0,0,0,0.08)',
        padding: 0,
      }}
    >
      <span
        style={{
          position: 'absolute',
          width: dotSize,
          height: dotSize,
          borderRadius: '50%',
          background: enabled ? '#ffffff' : 'var(--text-muted)',
          transform: `translateX(${enabled ? dotOn : dotOff}px)`,
          transition: 'transform 0.18s cubic-bezier(0.34, 1.56, 0.64, 1), background 0.15s ease',
          boxShadow: enabled
            ? '0 1px 4px rgba(0,0,0,0.25)'
            : '0 1px 2px rgba(0,0,0,0.15)',
        }}
      />
    </button>
  );
};

export default Toggle;
