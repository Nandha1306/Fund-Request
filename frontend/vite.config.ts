import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'

export default defineConfig(({ mode }) => {
  // VITE_API_TARGET (shell env or .env file) points the dev proxy at another API; default: the usual 8000.
  const apiTarget = loadEnv(mode, '.', 'VITE_').VITE_API_TARGET ?? 'http://localhost:8000'
  return {
    plugins: [react()],
    server: {
      port: 5173,
      // same-origin /api in dev, exactly like nginx does in Docker
      proxy: { '/api': apiTarget },
    },
    build: {
      // Pages are lazy-loaded (see App.tsx); the largest remaining chunk is Ant Design's shared core
      // (~550 kB minified, ~185 kB gzipped), which is expected for this UI library.
      chunkSizeWarningLimit: 600,
    },
  }
})
