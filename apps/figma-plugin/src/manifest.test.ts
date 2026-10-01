import { describe, expect, it } from "vitest";
import { buildManifest, validatePluginAccessToken } from "../scripts/build-manifest.mjs";

describe("plugin manifest security", () => {
  it("allows exactly one HTTPS company origin", () => {
    expect(buildManifest("https://fgui.corp.example", "123456789").networkAccess.allowedDomains).toEqual([
      "https://fgui.corp.example",
    ]);
  });

  it("places the local development origin in Figma devAllowedDomains", () => {
    expect(buildManifest("http://localhost:8765", "123456789").networkAccess).toEqual({
      allowedDomains: ["none"],
      devAllowedDomains: ["http://localhost:8765"],
    });
  });

  it("uses Figma's wildcard network pattern for an explicit RFC1918 LAN origin", () => {
    expect(buildManifest("http://192.168.1.100:8780", "123456789", { allowPrivateHttp: true }).networkAccess).toEqual({
      allowedDomains: ["*"],
      reasoning: "Connects to the organization's private LAN Figma-to-FairyGUI service.",
    });
    expect(() => buildManifest("http://192.168.1.100:8780", "123456789")).toThrow();
    expect(() => buildManifest("https://fgui.corp.example", "123456789", { allowPrivateHttp: true })).toThrow();
  });

  it.each(["http://8.8.8.8:8780", "http://169.254.1.1:8780", "http://172.32.0.1:8780"])(
    "rejects non-private HTTP origin %s even in LAN mode",
    (origin) => expect(() => buildManifest(origin, "123456789", { allowPrivateHttp: true })).toThrow(),
  );

  it.each(["http://fgui.corp.example", "http://127.0.0.1:8765", "http://0.0.0.0:8765", "*", "https://one.example,https://two.example"])(
    "rejects unsafe origin %s",
    (origin) => expect(() => buildManifest(origin, "123456789")).toThrow(),
  );

  it.each(["", "plugin-id", "*"])("rejects non-numeric plugin id %s", (pluginId) => {
    expect(() => buildManifest("https://fgui.corp.example", pluginId)).toThrow();
  });

  it("requires a header-safe deployment token", () => {
    expect(validatePluginAccessToken("a".repeat(32))).toBe("a".repeat(32));
    for (const token of ["short", `a${"b".repeat(31)}\n`, "密".repeat(32), "a".repeat(257)]) {
      expect(() => validatePluginAccessToken(token)).toThrow(/32-256 printable ASCII/);
    }
  });
});
