import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // same-origin /api in dev, exactly like nginx does in Docker
    proxy: { '/api': 'http://localhost:8000' },
  },
})
