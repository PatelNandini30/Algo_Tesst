import React, { useState, useEffect } from 'react';
import StrategyBuilder from './components/StrategyBuilder';

function App() {
  const [resetKey, setResetKey] = useState(0);

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

  return <StrategyBuilder key={resetKey} />;
}

export default App;
