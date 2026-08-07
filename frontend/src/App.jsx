import React, { useState, useEffect, useRef } from 'react';
import StrategyBuilder from './components/StrategyBuilder';

const HEALTH_POLL_MS = 2000;
const HEALTH_CHECK_TIMEOUT_MS = 15000;
// The blocking overlay is shown ONLY when the backend explicitly declares
// maintenance (`{"maintenance": true}` from /health, set by an operator before a
// rebuild). It is NEVER inferred from failed or slow health polls.
//
// Why: a heavy optimize sweep saturates the CPU enough that /health can take
// >4s, the old client timeout aborted it, three aborts in a row were read as
// "the backend is down", and users got a full-screen "Server is restarting…"
// while nothing had restarted and their jobs were running normally. Guessing
// from latency cannot distinguish a busy server from a dead one — so it no
// longer guesses. The timeout above is now generous for the same reason.

function App() {
  const [resetKey, setResetKey] = useState(0);
  // False ONLY while the backend declares maintenance (operator-set flag, or
  // still warming up after a start). The overlay blocks interaction so nobody
  // submits a job into a box that is being rebuilt. A busy/slow backend leaves
  // this true — see the health poll below.
  const [backendUp, setBackendUp] = useState(true);
  const [maintenanceMsg, setMaintenanceMsg] = useState('');
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
        if (cancelled) return;
        // 503 = still warming up; both it and an explicit flag set maintenance.
        const body = await r.json().catch(() => ({}));
        if (body && body.maintenance === true) {
          setMaintenanceMsg(body.message || 'Maintenance in progress');
          setBackendUp(false);
        } else {
          setMaintenanceMsg('');
          setBackendUp(true);
        }
      } catch {
        // Timeout / network error / server busy: do NOTHING. A slow or briefly
        // unreachable backend is not maintenance, and blocking the whole UI on
        // that guess is what scared users mid-sweep. The next poll re-checks.
        if (cancelled) return;
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
          <div style={{ fontSize: 16, fontWeight: 600 }}>Maintenance in progress…</div>
          <div style={{ fontSize: 13, color: 'rgba(255,255,255,0.7)', maxWidth: 360 }}>
            {maintenanceMsg ||
              "The system is being updated. Backtests and optimizations can't be started right now — this page will unlock automatically when it's done."}
          </div>
        </div>
      )}
    </>
  );
}

export default App;
