import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: './e2e',
  timeout: 180000,
  expect: { timeout: 15000 },
  workers: 1,
  use: {
    baseURL: process.env.UI_URL || 'http://localhost:8080',
    headless: true,
    actionTimeout: 20000,
    navigationTimeout: 30000,
    viewport: { width: 1440, height: 1000 },
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
    launchOptions: {
      executablePath: process.env.CHROME_PATH || undefined,
      args: ['--no-sandbox', '--disable-dev-shm-usage'],
    },
  },
  reporter: [['list']],
});
