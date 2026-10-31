import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig, devices } from '@playwright/test'

const PORT = 4173
const LIVE_API_PORT = 8001
const LIVE = process.env.RUN_LIVE_TESTS === '1'
const REPO_ROOT = fileURLToPath(new URL('..', import.meta.url))
const PYTHON =
  process.env.E2E_PYTHON ??
  path.join(
    REPO_ROOT,
    '.venv',
    process.platform === 'win32' ? 'Scripts' : 'bin',
    process.platform === 'win32' ? 'python.exe' : 'python',
  )

const vite = {
  command: `npm run dev -- --host 127.0.0.1 --port ${PORT} --strictPort`,
  url: `http://127.0.0.1:${PORT}`,
  reuseExistingServer: false,
  timeout: 120_000,
  env: LIVE
    ? ({
        ...process.env,
        VITE_DEV_API_TARGET: `http://127.0.0.1:${LIVE_API_PORT}`,
      } as Record<string, string>)
    : undefined,
}

const liveApi = {
  command: `"${PYTHON}" "${path.join(REPO_ROOT, 'scripts', 'e2e_live_app.py')}"`,
  url: `http://127.0.0.1:${LIVE_API_PORT}/health`,
  reuseExistingServer: false,
  timeout: 120_000,
  cwd: REPO_ROOT,
}

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: 'list',
  outputDir: 'test-results',
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'off',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: LIVE ? [vite, liveApi] : [vite],
})
