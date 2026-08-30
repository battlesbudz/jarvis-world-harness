import { defineConfig, devices } from "@playwright/test";

const productionBaseUrl = process.env.H2_BASE_URL?.replace(/\/?$/, "/");

export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 30_000,
  expect: { timeout: 8_000 },
  fullyParallel: false,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: process.env.CI ? [["line"], ["html", { open: "never" }]] : "line",
  outputDir: "../.harness/evidence/h2/playwright",
  use: {
    baseURL: productionBaseUrl ?? "http://127.0.0.1:4173",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "desktop-chromium",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 720 } },
    },
    {
      name: "android-folded",
      use: {
        browserName: "chromium",
        viewport: { width: 360, height: 800 },
        deviceScaleFactor: 2.5,
        hasTouch: true,
        isMobile: true,
        userAgent: devices["Pixel 7"].userAgent,
      },
    },
    {
      name: "android-unfolded-landscape",
      use: {
        browserName: "chromium",
        viewport: { width: 800, height: 600 },
        deviceScaleFactor: 2,
        hasTouch: true,
        isMobile: true,
        userAgent: devices["Pixel 7"].userAgent,
      },
    },
  ],
  webServer: productionBaseUrl ? undefined : {
    command: "npm run dev -- --host 127.0.0.1",
    url: "http://127.0.0.1:4173",
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
