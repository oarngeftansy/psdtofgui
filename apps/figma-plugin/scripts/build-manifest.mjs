import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const templatePath = fileURLToPath(new URL("../manifest.template.json", import.meta.url));

function isPrivateIpv4(hostname) {
  const parts = hostname.split(".").map(Number);
  if (parts.length !== 4 || parts.some((part) => !Number.isInteger(part) || part < 0 || part > 255)) return false;
  return parts[0] === 10 || (parts[0] === 172 && parts[1] >= 16 && parts[1] <= 31) || (parts[0] === 192 && parts[1] === 168);
}

export function normalizeServerOrigin(value, { allowPrivateHttp = false } = {}) {
  if (typeof value !== "string" || value.includes("*") || value.includes(",")) {
    throw new Error("FGUI_SERVER_ORIGIN must be one HTTPS origin");
  }
  let parsed;
  try {
    parsed = new URL(value);
  } catch {
    throw new Error("FGUI_SERVER_ORIGIN must be one HTTPS origin");
  }
  const loopbackHttp = parsed.protocol === "http:" && parsed.hostname === "localhost";
  const privateHttp = parsed.protocol === "http:" && allowPrivateHttp && isPrivateIpv4(parsed.hostname);
  if (
    (parsed.protocol !== "https:" && !loopbackHttp && !privateHttp) ||
    (allowPrivateHttp && !privateHttp) ||
    parsed.username ||
    parsed.password ||
    parsed.pathname !== "/" ||
    parsed.search ||
    parsed.hash
  ) {
    throw new Error("FGUI_SERVER_ORIGIN must be one HTTPS origin or an explicit loopback HTTP origin");
  }
  return parsed.origin;
}

export function validatePluginId(value) {
  if (typeof value !== "string" || !/^\d+$/.test(value)) {
    throw new Error("FIGMA_PLUGIN_ID must be an explicit numeric plugin ID");
  }
  return value;
}

export function validatePluginAccessToken(value) {
  if (typeof value !== "string" || value.length < 32 || value.length > 256 || !/^[\x21-\x7e]+$/.test(value)) {
    throw new Error("FGUI_PLUGIN_ACCESS_TOKEN must be 32-256 printable ASCII characters");
  }
  return value;
}

export function buildManifest(serverOrigin, pluginId, options = {}) {
  const origin = normalizeServerOrigin(serverOrigin, options);
  const id = validatePluginId(pluginId);
  const template = JSON.parse(readFileSync(templatePath, "utf8"));
  const privateLan = origin.startsWith("http://") && !origin.startsWith("http://localhost");
  return {
    ...template,
    id,
    networkAccess: origin.startsWith("http://localhost")
      ? { allowedDomains: ["none"], devAllowedDomains: [origin] }
      : privateLan
        ? {
            allowedDomains: ["*"],
            reasoning: "Connects to the organization's private LAN Figma-to-FairyGUI service.",
          }
        : { allowedDomains: [origin] },
  };
}
