// @vitest-environment node
import { createServer } from "node:http";
import type { ChildProcess } from "node:child_process";
import { once } from "node:events";
import { afterEach, describe, expect, it, vi } from "vitest";

import { assertPortAvailable, serverArguments, stopServer, waitForServer } from "./global-setup";

let occupiedPort: ReturnType<typeof createServer> | undefined;

afterEach(async () => {
  vi.unstubAllGlobals();
  if (occupiedPort) {
    occupiedPort.close();
    await once(occupiedPort, "close");
    occupiedPort = undefined;
  }
});

describe("Playwright server setup", () => {
  it("starts the live workflow with an isolated plugin secret", () => {
    expect(serverArguments("data", "dist", "plugin-secret")).toContain("--plugin-access-token-file");
    expect(serverArguments("data", "dist", "plugin-secret")).toContain("plugin-secret");
  });

  it("fails before spawn when the configured port is already occupied", async () => {
    let requests = 0;
    occupiedPort = createServer((_request, response) => {
      requests += 1;
      response.end("existing service");
    });
    occupiedPort.listen(0, "127.0.0.1");
    await once(occupiedPort, "listening");
    const address = occupiedPort.address();
    if (address === null || typeof address === "string") throw new Error("Expected a TCP port");

    await expect(assertPortAvailable(address.port)).rejects.toThrow(
      `127.0.0.1:${address.port} already accepts connections`,
    );
    expect(requests).toBe(0);
    await expect(fetch(`http://127.0.0.1:${address.port}`)).resolves.toMatchObject({ ok: true });
  });

  it("fails readiness when the spawned child has already exited", async () => {
    await expect(waitForServer({ exitCode: 7 } as ChildProcess, "expected-token")).rejects.toThrow(
      "Playwright server exited with 7",
    );
  });

  it("fails readiness when the child exits immediately after health succeeds", async () => {
    let checks = 0;
    const server = {
      get exitCode() {
        checks += 1;
        return checks === 1 ? null : 9;
      },
    } as ChildProcess;
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(null, {
          status: 200,
          headers: { "X-Figma-To-FGUI-Instance": "expected-token" },
        }),
      ),
    );

    await expect(waitForServer(server, "expected-token")).rejects.toThrow(
      "Playwright server exited with 9",
    );
  });

  it("rejects a healthy response from a different server instance", async () => {
    let checks = 0;
    const losingServer = {
      get exitCode() {
        checks += 1;
        return checks < 3 ? null : 17;
      },
    } as ChildProcess;
    const fetchHealth = vi.fn().mockResolvedValue(
      new Response(null, {
        status: 200,
        headers: { "X-Figma-To-FGUI-Instance": "winner-token" },
      }),
    );
    vi.stubGlobal("fetch", fetchHealth);

    await expect(waitForServer(losingServer, "loser-token")).rejects.toThrow(
      "Playwright server exited with 17",
    );
    expect(fetchHealth).toHaveBeenCalledTimes(2);
  });

  it("waits for its child to die before teardown can remove the temporary data", async () => {
    let exitCode: number | null = null;
    const server = {
      pid: undefined,
      get exitCode() { return exitCode; },
      kill: vi.fn(() => { exitCode = 0; return true; }),
    } as unknown as ChildProcess;

    await stopServer(server);

    expect(server.kill).toHaveBeenCalledOnce();
    expect(server.exitCode).toBe(0);
  });
});
