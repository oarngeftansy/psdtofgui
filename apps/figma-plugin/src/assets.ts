import type { FigmaSceneNode, SelectionManifest } from "./selection";

export type ExportedResource = { key: string; mime_type: "image/png" | "image/svg+xml"; bytes: Uint8Array };
type ExportableNode = FigmaSceneNode & { exportAsync(settings: { format: "PNG" | "SVG" }): Promise<Uint8Array> };

export class AssetExportError extends Error {
  constructor(label: string) { super(`无法导出图层：${label}`); this.name = "AssetExportError"; }
}

export async function* exportDeclaredAssets(
  manifest: SelectionManifest,
  lookup: ReadonlyMap<string, FigmaSceneNode>,
  exportOverride?: (node: FigmaSceneNode, resourceKey: string, format: "PNG" | "SVG") => Promise<Uint8Array>,
): AsyncIterable<ExportedResource> {
  const results = new Array<ExportedResource>(manifest.resources.length);
  let cursor = 0;
  const worker = async () => {
    while (cursor < manifest.resources.length) {
      const index = cursor++;
      const resource = manifest.resources[index]!;
      const node = lookup.get(resource.key) as ExportableNode | undefined;
      if (!node) throw new AssetExportError("所选图层");
      try {
        const format = resource.mime_type === "image/svg+xml" ? "SVG" : "PNG";
        const bytes = exportOverride
          ? await exportOverride(node, resource.key, format)
          : await node.exportAsync({ format });
        results[index] = { key: resource.key, mime_type: resource.mime_type, bytes };
      } catch (error) {
        // The plugin bridge attaches privacy-safe resource_* stage codes.
        // Preserve those exact errors so the UI can locate a Figma API
        // failure; continue redacting every unclassified exception.
        if (error && typeof error === "object" && "safeCode" in error
          && typeof (error as { safeCode?: unknown }).safeCode === "string"
          && (error as { safeCode: string }).safeCode.startsWith("resource_")) throw error;
        throw new AssetExportError(node.name || "所选图层");
      }
    }
  };
  await Promise.all(Array.from({ length: Math.min(4, manifest.resources.length) }, worker));
  for (const resource of results) yield resource;
}
