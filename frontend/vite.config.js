import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  // Vite writes bundled config temp files while loading the build config.
  // The repo's checked-in node_modules tree is owned by another uid, so the
  // default temp/cache path under node_modules/.vite-temp is not writable here.
  // Point it at /tmp so local builds and deploys can complete normally.
  cacheDir: '/tmp/algotest-vite-cache',
  // Keep the build independent of frontend/public/, which is also owned by a
  // different uid in this workspace. The app now inlines its favicon.
  publicDir: false,
  build: {
    outDir: 'build',
    emptyOutDir: true,
  },
  server: {
    port: 3000,
    host: '0.0.0.0',  // accessible from network
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      }
    }
  }
})
