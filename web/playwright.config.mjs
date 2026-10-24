import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  timeout: 5 * 60 * 1000,
  expect: { timeout: 30_000 },
  use: {
    baseURL: process.env.E2E_WEB_URL || 'http://localhost:5173',
    browserName: 'chromium',
    headless: true,
  },
  reporter: 'list',
});
