export type PluginManifest = {
  name: string;
  id: string;
  api: string;
  main: string;
  ui: string;
  editorType: string[];
  documentAccess: string;
  networkAccess: { allowedDomains: string[]; devAllowedDomains?: string[]; reasoning?: string };
};

export type ManifestOptions = { allowPrivateHttp?: boolean };
export function normalizeServerOrigin(value: unknown, options?: ManifestOptions): string;
export function validatePluginId(value: unknown): string;
export function validatePluginAccessToken(value: unknown): string;
export function buildManifest(serverOrigin: string, pluginId: string, options?: ManifestOptions): PluginManifest;
