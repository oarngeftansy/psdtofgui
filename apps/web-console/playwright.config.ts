import { defineConfig } from "playwright/test";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { playwrightBaseUrl } from "./e2e/global-setup";

export default defineConfig({
  testDir: "./e2e",
  testMatch: "**/*.spec.ts",
  // globalSetup owns a fresh server for every run; an existing server is never reused.
  globalSetup: "./e2e/global-setup.ts",
  outputDir: join(tmpdir(), "figma-to-fgui-playwright-results"),
  timeout: 30_000,
  use: { baseURL: playwrightBaseUrl, channel: "msedge" },
});
