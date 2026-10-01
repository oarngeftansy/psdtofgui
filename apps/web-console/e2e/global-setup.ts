import { spawn, type ChildProcess } from "node:child_process";
import { randomBytes } from "node:crypto";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { createConnection } from "node:net";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { setTimeout as sleep } from "node:timers/promises";

const root = resolve(import.meta.dirname, "../../..");
export const playwrightBaseUrl = "http://127.0.0.1:8766";
const instanceHeader = "X-Figma-To-FGUI-Instance";

export function serverArguments(dataDir: string, webDist: string, pluginSecretFile: string): string[] {
  return [
    "-m",
    "figma_to_fgui.cli",
    "serve",
    "--data-dir",
    dataDir,
    "--port",
    "8766",
    "--web-dist",
    webDist,
    "--plugin-access-token-file",
    pluginSecretFile,
  ];
}

export async function assertPortAvailable(port = 8766): Promise<void> {
  await new Promise<void>((resolve, reject) => {
    const socket = createConnection({ host: "127.0.0.1", port });
    const timeout = setTimeout(() => {
      socket.destroy();
      reject(new Error(`Timed out checking whether 127.0.0.1:${port} is available`));
    }, 1_000);
    socket.once("connect", () => {
      clearTimeout(timeout);
      socket.destroy();
      reject(new Error(`127.0.0.1:${port} already accepts connections; refusing to reuse it`));
    });
    socket.once("error", (error) => {
      clearTimeout(timeout);
      if ((error as NodeJS.ErrnoException).code === "ECONNREFUSED") resolve();
      else reject(error);
    });
  });
}

export async function waitForServer(server: ChildProcess, instanceToken: string): Promise<void> {
  for (let attempt = 0; attempt < 100; attempt += 1) {
    if (server.exitCode !== null) throw new Error(`Playwright server exited with ${server.exitCode}`);
    let healthy = false;
    try {
      const response = await fetch(`${playwrightBaseUrl}/health`);
      healthy = response.ok && response.headers.get(instanceHeader) === instanceToken;
    } catch {
      // The process may still be binding its loopback port.
    }
    if (healthy) {
      if (server.exitCode !== null) throw new Error(`Playwright server exited with ${server.exitCode}`);
      return;
    }
    await sleep(100);
  }
  throw new Error("Playwright server did not become healthy");
}

export async function stopServer(server: ChildProcess): Promise<void> {
  const pid = server.pid;
  if (server.exitCode === null) {
    server.kill();
    await waitForProcessExit(pid, 1_000);
  }
  if (await processIsAlive(pid)) {
    await forceTerminate(pid);
    await waitForProcessExit(pid, 5_000);
  }
  if (await processIsAlive(pid)) throw new Error("Playwright server could not be terminated");
}

async function removeDataDir(dataDir: string): Promise<void> {
  for (let attempt = 0; attempt < 20; attempt += 1) {
    try {
      await rm(dataDir, { force: true, recursive: true });
      if (!existsSync(dataDir)) return;
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "EBUSY" || attempt === 19) throw error;
    }
    await sleep(100);
  }
  throw new Error("Playwright temporary data directory could not be removed");
}

export default async function globalSetup() {
  await assertPortAvailable();
  const instanceToken = randomBytes(32).toString("hex");
  const dataDir = await mkdtemp(join(tmpdir(), "figma-to-fgui-playwright-"));
  const pluginSecretFile = join(dataDir, "plugin-secret.bin");
  const pluginToken = randomBytes(32).toString("hex");
  await writeFile(pluginSecretFile, pluginToken, "utf8");
  process.env.FGUI_E2E_PLUGIN_TOKEN = pluginToken;
  const server = spawn(
    existsSync(join(root, ".venv", "Scripts", "python.exe"))
      ? join(root, ".venv", "Scripts", "python.exe")
      : join(root, "..", "..", ".venv", "Scripts", "python.exe"),
    serverArguments(dataDir, join(root, "apps", "web-console", "dist"), pluginSecretFile),
    {
      cwd: root,
      env: {
        ...process.env,
        FIGMA_TO_FGUI_HEALTH_INSTANCE_TOKEN: instanceToken,
        PYTHONPATH: join(root, "src"),
      },
      stdio: "ignore",
      windowsHide: true,
    },
  );
  try {
    await waitForServer(server, instanceToken);
  } catch (error) {
    await stopServer(server);
    await removeDataDir(dataDir);
    throw error;
  }

  return async () => {
    let failure: unknown;
    try {
      await stopServer(server);
    } catch (error) {
      failure = error;
    }
    try {
      await removeDataDir(dataDir);
    } catch (error) {
      failure ??= error;
    }
    if (failure) throw failure;
  };
}

async function forceTerminate(pid: number | undefined): Promise<void> {
  if (pid === undefined) return;
  const killer = spawn("taskkill.exe", ["/pid", String(pid), "/T", "/F"], {
    stdio: "ignore",
    windowsHide: true,
  });
  await Promise.race([
    new Promise<void>((resolve) => killer.once("close", () => resolve())),
    sleep(5_000),
  ]);
}

async function waitForProcessExit(pid: number | undefined, timeoutMs: number): Promise<void> {
  for (let elapsed = 0; elapsed < timeoutMs; elapsed += 50) {
    if (!(await processIsAlive(pid))) return;
    await sleep(50);
  }
}

async function processIsAlive(pid: number | undefined): Promise<boolean> {
  if (pid === undefined) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return (error as NodeJS.ErrnoException).code !== "ESRCH";
  }
}
