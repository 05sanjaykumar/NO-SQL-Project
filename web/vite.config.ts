import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Production build is served by FastAPI at /ui (see src/fraudsim/api/main.py).
// In development (`npm run dev`) the API runs on :8000 and is proxied.
export default defineConfig(({ command }) => ({
  base: command === 'build' ? '/ui/' : '/',
  plugins: [react()],
  build: { chunkSizeWarningLimit: 1500 },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
      '/ws': { target: 'ws://localhost:8000', ws: true },
    },
  },
}));
