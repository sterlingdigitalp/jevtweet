import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests",
  fullyParallel: true,
  forbidOnly: true,
  retries: 0,
  reporter: "list",
  use: {
    baseURL: process.env.JEVTWEET_E2E_URL ?? "http://127.0.0.1:5197",
    headless: true,
    trace: "off",
    screenshot: "off",
  },
  webServer: process.env.JEVTWEET_E2E_URL
    ? undefined
    : {
        command: "npm run dev -- --port 5197 --strictPort",
        url: "http://127.0.0.1:5197",
        reuseExistingServer: false,
      },
  projects: [
    {
      name: "chromium",
      use: { browserName: "chromium", viewport: { width: 1440, height: 1000 } },
    },
  ],
});
