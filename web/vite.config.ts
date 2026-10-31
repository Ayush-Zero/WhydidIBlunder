import react from '@vitejs/plugin-react'
import { loadEnv } from 'vite'
import { defineConfig } from 'vitest/config'

// The browser only talks to /api (and /health for the shell status). In dev,
// Vite proxies those paths to the FastAPI process, so the browser stays
// same-origin and the API does not need a permissive CORS list just for local
// work. Set VITE_DEV_API_TARGET in web/.env.local if the API is elsewhere.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const apiTarget = env.VITE_DEV_API_TARGET || 'http://127.0.0.1:8000'

  return {
    plugins: [react()],
    server: {
      port: 5173,
      proxy: {
        '/api': { target: apiTarget, changeOrigin: true },
        '/health': { target: apiTarget, changeOrigin: true },
      },
    },
    test: {
      environment: 'jsdom',
      setupFiles: './src/test/setup.ts',
      // Playwright owns e2e/; Vitest must not try to execute those specs.
      include: ['src/**/*.{test,spec}.{ts,tsx}'],
      css: false,
      passWithNoTests: true,
      restoreMocks: true,
      // jsdom + MSW + fake-timer tests can exceed the 5s default on a busy
      // machine; a higher ceiling keeps the suite deterministic.
      testTimeout: 15_000,
    },
  }
})
