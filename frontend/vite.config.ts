import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // same-origin /api in dev, exactly like nginx does in Docker
    proxy: { '/api': 'http://localhost:8000' },
  },
  build: {
    // Pages are lazy-loaded (see App.tsx); the largest remaining chunk is Ant Design's shared core
    // (~550 kB minified, ~185 kB gzipped), which is expected for this UI library.
    chunkSizeWarningLimit: 600,
  },
})
