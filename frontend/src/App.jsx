import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import ConfigPanel from './components/ConfigPanel';

const queryClient = new QueryClient();

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <div className="min-h-screen bg-gray-50">
        <ConfigPanel />
      </div>
    </QueryClientProvider>
  );
}

export default App;