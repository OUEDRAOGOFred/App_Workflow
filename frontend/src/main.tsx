import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import App from './App';
import { initialize } from './api/client';
import '@xyflow/react/dist/style.css';
import './style.css';
const client = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, staleTime: 10000, refetchOnWindowFocus: true },
  },
});
void initialize();
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={client}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
