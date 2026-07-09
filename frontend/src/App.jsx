import React, { useState, useEffect, useRef } from 'react';
import StrategyBuilder from './components/StrategyBuilder';

const HEALTH_POLL_MS = 2000;
const HEALTH_CHECK_TIMEOUT_MS = 4000;

function App() {
  const [resetKey, setResetKey] = useState(0);
  // Tracks whether the backend is reachable right now. Polled continuously
  // (not just on mount) so a backend rebuild/restart mid-session — e.g. while
  // deploying a fix — is caught immediately: the overlay below blocks all
  // interaction with the app underneath, so no backtest/optim can be
  // submitted while the backend is unavailable. Starts true (optimistic) so
  // a normal page load doesn't flash the overlay before the first check.
  const [backendUp, setBackendUp] = useState(true);
  const pollRef = useRef(null);

  useEffect(() => {
    const onKey = (e) => {
      if (e.ctrlKey && e.shiftKey && (e.key === 'R' || e.key === 'r')) {
        e.preventDefault();
        setResetKey((k) => k + 1);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  useEffect(() => {
    let cancelled = false;
    async function checkHealth() {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), HEALTH_CHECK_TIMEOUT_MS);
      try {
        const r = await fetch('/health', { signal: controller.signal, cache: 'no-store' });
        if (!cancelled) setBackendUp(r.ok);
      } catch {
        if (!cancelled) setBackendUp(false);
      } finally {
        clearTimeout(timer);
      }
    }
    checkHealth();
    pollRef.current = setInterval(checkHealth, HEALTH_POLL_MS);
    return () => {
      cancelled = true;
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  return (
    <>
      <StrategyBuilder key={resetKey} />
      {!backendUp && (
        <div
          role="alert"
          aria-live="assertive"
          style={{
            position: 'fixed',
            inset: 0,
            zIndex: 999999,
            background: 'rgba(8, 10, 16, 0.88)',
            backdropFilter: 'blur(2px)',
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            gap: 16,
            color: '#fff',
            textAlign: 'center',
            padding: 24,
          }}
        >
          <div
            style={{
              width: 44,
              height: 44,
              border: '3px solid rgba(255,255,255,0.25)',
              borderTopColor: '#2563eb',
              borderRadius: '50%',
              animation: 'app-health-spin 0.8s linear infinite',
            }}
          />
          <style>{'@keyframes app-health-spin { to { transform: rotate(360deg); } }'}</style>
          <div style={{ fontSize: 16, fontWeight: 600 }}>Server is restarting…</div>
          <div style={{ fontSize: 13, color: 'rgba(255,255,255,0.7)', maxWidth: 360 }}>
            Backend is being updated. Backtests and optimizations can't be started right now —
            this page will unlock automatically as soon as it's back.
          </div>
        </div>
      )}
    </>
  );
}

export default App;
