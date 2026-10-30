import { defineConfig, devices } from "@playwright/test";

const port = process.env.E2E_PORT ?? "8765";

export default defineConfig({
  testDir: "e2e",
  workers: 1,
  fullyParallel: false,
  retries: 0,
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  use: { baseURL: `http://127.0.0.1:${port}` },
  webServer: {
    command: "uv run python ../scripts/e2e_server.py",
    url: `http://127.0.0.1:${port}/api/health?t=e2e-token`,
    timeout: 120_000,
    reuseExistingServer: false,
  },
});
