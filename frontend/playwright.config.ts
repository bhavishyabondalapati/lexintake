import { defineConfig } from "@playwright/test";

// The tests run against a live stack that `make seed-demo` has filled with demo documents.
// E2E_BASE_URL: where the frontend is (default http://localhost:3000).
// E2E_CHROME=1: use the Chrome that is already installed instead of Playwright's own download.
export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  retries: 0,
  reporter: "list",
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
    channel: process.env.E2E_CHROME ? "chrome" : undefined,
    screenshot: "only-on-failure",
  },
});
