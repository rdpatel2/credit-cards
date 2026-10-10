import { defineConfig, devices } from "@playwright/test";
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 2,
  reporter: "list",
  use: {
    baseURL: "http://127.0.0.1:3100",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    ...devices["Desktop Chrome"],
    channel: process.env.PLAYWRIGHT_CHANNEL || undefined,
  },
  webServer: {
    command: "node .next/standalone/server.js",
    url: "http://127.0.0.1:3100",
    reuseExistingServer: false,
    env: { MARKET_DATA_MODE: "demo", HOSTNAME: "127.0.0.1", PORT: "3100" },
    timeout: 60000,
  },
});
