import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
export default defineConfig({
 plugins:[react(),tailwindcss()],
 server:{proxy:{'/api':{target:process.env.API_PROXY_TARGET || 'http://localhost:8000',changeOrigin:true},'/health':{target:process.env.API_PROXY_TARGET || 'http://localhost:8000'}}},
 test:{environment:'jsdom',setupFiles:['./src/tests/setup.ts'],include:['src/**/*.test.{ts,tsx}']},
});
