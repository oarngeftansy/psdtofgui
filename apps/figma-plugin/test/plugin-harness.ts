import type { ExportedResource } from "../src/assets";
import { startPlugin } from "../src/code";
import type { FigmaSceneNode, SelectionManifest } from "../src/selection";

export type FigmaHarnessNode = Omit<FigmaSceneNode, "children"> & {
  id?: string;
  children?: readonly FigmaHarnessNode[];
  exportAsync?(settings: { format: "PNG" | "SVG" }): Promise<Uint8Array>;
};

type PluginMessage = { type?: unknown; [key: string]: unknown };

/** A Figma API fake that deliberately exercises the same serializer/exporter/uploader as the plugin. */
export function createPluginHarness(selection: readonly FigmaHarnessNode[]) {
  let activeSession: {
    messages: PluginMessage[];
    runtime: { ui: { onmessage?: (message: unknown, props: OnMessageProperties) => void } };
  } | undefined;
  const exportCurrentSelection = async (): Promise<{ manifest: SelectionManifest; resources: ExportedResource[] }> => {
    const messages: PluginMessage[] = [];
    const runtime = {
      showUI: () => {},
      on: () => {},
      createFrame: () => { throw new Error("multi-node screenshot is not supported by this harness"); },
      currentPage: { selection },
      ui: {
        onmessage: undefined as ((message: unknown, props: OnMessageProperties) => void) | undefined,
        postMessage: (message: PluginMessage) => { messages.push(message); },
      },
    };
    activeSession = { messages, runtime };
    (globalThis as typeof globalThis & { __html__?: string }).__html__ = "";
    startPlugin(runtime);
    const preflight = await waitForMessage(messages, "selection-preflight") as { preflight: { manifest: SelectionManifest } };
    runtime.ui.onmessage?.({ type: "selection-export", attempt: "harness-attempt" }, { origin: "null" } as OnMessageProperties);
    const exported = await waitForMessage(messages, "selection-export") as {
      manifest: SelectionManifest;
      resources: ExportedResource[];
    };
    if (!preflight.preflight.manifest) throw new Error("expected a sendable Figma selection");
    return { manifest: exported.manifest, resources: exported.resources };
  };

  const exportSemanticScreenshot = async () => {
    if (!activeSession?.runtime.ui.onmessage) throw new Error("selection must be exported first");
    activeSession.runtime.ui.onmessage(
      { type: "semantic-screenshot-export", attempt: "harness-attempt" },
      { origin: "null" } as OnMessageProperties,
    );
    const exported = await waitForMessage(activeSession.messages, "semantic-screenshot-export") as {
      mimeType: "image/png";
      bytes: Uint8Array;
    };
    return { mimeType: exported.mimeType, bytes: exported.bytes };
  };

  return {
    exportSelection: exportCurrentSelection,
    exportSemanticScreenshot,
  };
}

async function waitForMessage(
  messages: PluginMessage[], type: string, matches: (message: PluginMessage) => boolean = () => true,
): Promise<PluginMessage> {
  for (let attempt = 0; attempt < 50; attempt += 1) {
    const message = messages.find((candidate) => candidate.type === type && matches(candidate));
    if (message) return message;
    await new Promise((resolve) => setTimeout(resolve, 1));
  }
  throw new Error(`plugin harness did not receive ${type}`);
}
