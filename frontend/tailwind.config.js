/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // Tailwind mappings to our CSS variables
        base: 'var(--bg-base)',
        surface: 'var(--bg-surface)',
        elevated: 'var(--bg-elevated)',
        hover: 'var(--bg-hover)',
        active: 'var(--bg-active)',
        input: 'var(--bg-input)',
        
        // Borders
        subtle: 'var(--border-subtle)',
        default: 'var(--border-default)',
        strong: 'var(--border-strong)',
        'accent-border': 'var(--border-accent)',
        
        // Text
        primary: 'var(--text-primary)',
        secondary: 'var(--text-secondary)',
        muted: 'var(--text-muted)',
        inverse: 'var(--text-inverse)',
        
        // Semantic Data
        profit: 'var(--profit)',
        'profit-dim': 'var(--profit-dim)',
        'profit-bg': 'var(--profit-bg)',
        loss: 'var(--loss)',
        'loss-dim': 'var(--loss-dim)',
        'loss-bg': 'var(--loss-bg)',
        warning: 'var(--warning)',
        'warning-bg': 'var(--warning-bg)',
        
        // Accent/Brand
        accent: 'var(--accent)',
        'accent-dim': 'var(--accent-dim)',
        'accent-bg': 'var(--accent-bg)',
        'accent-hover': 'var(--accent-hover)',
      },
      fontFamily: {
        sans:    ['Outfit', 'system-ui', 'sans-serif'],
        mono:    ['IBM Plex Mono', 'JetBrains Mono', 'monospace'],
        display: ['Outfit', 'system-ui', 'sans-serif'],
      },
      boxShadow: {
        'card':        '0 2px 12px rgba(0,0,0,0.35)',
        'card-hover':  '0 4px 24px rgba(0,0,0,0.5)',
        'glow-amber':  '0 0 20px rgba(232,160,48,0.3)',
        'glow-profit': '0 0 16px rgba(34,212,122,0.25)',
        'glow-loss':   '0 0 16px rgba(245,86,110,0.25)',
        'inner-glow':  'inset 0 1px 0 rgba(255,255,255,0.04)',
      },
      animation: {
        'fade-in': 'fadeIn 0.2s ease-out',
        'slide-up': 'slideUp 0.3s ease-out',
        'pulse-subtle': 'pulseSubtle 2s cubic-bezier(0.4, 0, 0.6, 1) infinite',
      },
      keyframes: {
        fadeIn: {
          '0%': { opacity: '0' },
          '100%': { opacity: '1' },
        },
        slideUp: {
          '0%': { transform: 'translateY(10px)', opacity: '0' },
          '100%': { transform: 'translateY(0)', opacity: '1' },
        },
        pulseSubtle: {
          '0%, 100%': { opacity: '1' },
          '50%': { opacity: '0.8' },
        },
      },
    },
  },
  plugins: [],
}