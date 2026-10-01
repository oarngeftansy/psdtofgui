import { createHash } from "node:crypto";
import { inflateRawSync } from "node:zlib";
import { afterEach, describe, expect, it } from "vitest";

import { ProjectWorkflowClient } from "../src/project-client";
import { startFastApiService, type FastApiService } from "./fastapi-service";
import { createPluginHarness, type FigmaHarnessNode } from "./plugin-harness";

let service: FastApiService | undefined;

const screenshotPng = new Uint8Array(Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=",
  "base64",
));

afterEach(async () => {
  await service?.stop();
  service = undefined;
});

function selectedNode(): FigmaHarnessNode {
  return {
    id: "private:selection-root",
    name: "Checkout",
    type: "FRAME",
    visible: true,
    absoluteBoundingBox: { x: 0, y: 0, width: 600, height: 300 },
    exportAsync: async () => screenshotPng,
    children: [{
      id: "private:vector",
      name: "Mark",
      type: "VECTOR",
      visible: true,
      absoluteBoundingBox: { x: 0, y: 0, width: 2, height: 2 },
      children: [],
      exportAsync: async () => screenshotPng,
    }],
  };
}

describe("real plugin selection harness", () => {
  it("creates and updates checked downloads through the plugin public HTTP contract", async () => {
    service = await startFastApiService();
    const requests: string[] = [];
    const fetchImpl = (url: string, init: RequestInit) => {
      requests.push(new URL(url).pathname);
      return service!.fetch(url, init);
    };
    const plugin = createPluginHarness([selectedNode()]);
    const selection = await plugin.exportSelection();
    const client = new ProjectWorkflowClient({ serverOrigin: service.baseUrl, pluginToken: service.pluginToken, fetchImpl, wait: (ms) => new Promise((resolve) => setTimeout(resolve, Math.min(ms, 10))) });
    const templateHash = await service.templateSha256();

    await expect(client.options()).resolves.toEqual([expect.objectContaining({ templateId: "fgui-2024-web" })]);
    const created = await client.runCreate(selection.manifest, selection.resources, { templateId: "fgui-2024-web", projectName: "Quiz" });
    expect(created.downloadName).toMatch(/^Quiz-Figma新建-\d{8}-\d{4}\.zip$/);
    const createdArchive = await openZip(created.blob);
    expect(createdArchive.get(`${created.project.packages[0]!.name}/package.xml`)).toContain("<package");
    expect([...createdArchive.keys()].filter((name) => name.endsWith(".xml"))).toHaveLength(2);
    expect([...createdArchive.keys()].some((name) => /\/assets\/.+\.(?:png|svg)$/i.test(name))).toBe(true);

    const archive = new File([service.projectArchive], "Existing.zip", { type: "application/zip" });
    const archiveHash = sha256(await archive.arrayBuffer());
    const updated = await client.runUpdate(selection.manifest, selection.resources, archive);
    expect(updated.downloadName).toMatch(/^Existing-Figma更新-\d{8}-\d{4}\.zip$/);
    expect(updated.downloadName).not.toBe(created.downloadName);
    const updatedArchive = await openZip(updated.blob);
    expect(updatedArchive.get(`${updated.project.packages[0]!.name}/package.xml`)).toContain("<package");
    expect([...updatedArchive.keys()].filter((name) => name.endsWith(".xml"))).toHaveLength(2);
    expect([...updatedArchive.keys()].some((name) => /\/assets\/.+\.(?:png|svg)$/i.test(name))).toBe(true);
    expect(sha256(await archive.arrayBuffer())).toBe(archiveHash);
    expect(await service.templateSha256()).toBe(templateHash);
    expect(requests.some((path) => path.startsWith("/v1/agents/"))).toBe(false);

    expect((await service.fetch("/v1/figma/selections/uploads", { method: "POST" })).status).toBe(401);
    expect(JSON.stringify([created.selection, updated.selection])).not.toMatch(/token|private:|asset-1|path/i);
    expect(JSON.stringify(selection.manifest)).not.toContain("private:");
  });

  it.each([
    { workflow: "structure-only", aiScenario: "structure_success", consent: undefined },
    { workflow: "consented screenshot", aiScenario: "screenshot_success", consent: true },
    { workflow: "declined screenshot", aiScenario: "screenshot_success", consent: false },
    { workflow: "AI failure fallback", aiScenario: "ai_failure", consent: undefined },
  ] as const)("proves $workflow output against the rules baseline", async ({ aiScenario, consent }) => {
    const baselineService = await startFastApiService();
    const baselinePlugin = createPluginHarness([selectedNode()]);
    const baselineSelection = await baselinePlugin.exportSelection();
    const baselineClient = new ProjectWorkflowClient({
      serverOrigin: baselineService.baseUrl,
      pluginToken: baselineService.pluginToken,
      fetchImpl: baselineService.fetch,
      wait: (ms) => new Promise((resolve) => setTimeout(resolve, Math.min(ms, 10))),
    });
    let baselineArchive: Map<string, string>;
    try {
      const baseline = await baselineClient.runCreate(
        baselineSelection.manifest,
        baselineSelection.resources,
        { templateId: "fgui-2024-web", projectName: "Semantic" },
      );
      baselineArchive = await openZip(baseline.blob);
    } finally {
      await baselineService.stop();
    }

    service = await startFastApiService({ aiScenario });
    const plugin = createPluginHarness([selectedNode()]);
    const selection = await plugin.exportSelection();
    const client = new ProjectWorkflowClient({
      serverOrigin: service.baseUrl,
      pluginToken: service.pluginToken,
      fetchImpl: service.fetch,
      wait: (ms) => new Promise((resolve) => setTimeout(resolve, Math.min(ms, 10))),
    });
    let consentRequests = 0;
    let screenshotRequests = 0;
    const options = consent === undefined ? {} : {
      onScreenshotConsent: async () => { consentRequests += 1; return consent; },
      requestScreenshot: async () => { screenshotRequests += 1; return plugin.exportSemanticScreenshot(); },
    };

    const result = await client.runCreate(
      selection.manifest,
      selection.resources,
      { templateId: "fgui-2024-web", projectName: "Semantic" },
      () => {},
      options,
    );

    const archive = await openZip(result.blob);
    const diagnostics = result.package.diagnostics.map((item) => item.code);
    expect(result.package.stage).toBe("ready");
    expect(archive.size).toBeGreaterThan(0);
    expect(consentRequests).toBe(consent === undefined ? 0 : 1);
    expect(screenshotRequests).toBe(consent === true ? 1 : 0);
    if (aiScenario === "structure_success") {
      expect(diagnostics).toContain("semantic.ai_applied");
      expect([...archive.values()].some((content) => content.includes("Panel_Semantic_AI"))).toBe(true);
    } else if (consent === true) {
      expect(diagnostics).toContain("semantic.ai_applied");
      expect([...archive.values()].some((content) => content.includes("Panel_Semantic_Shot"))).toBe(true);
    } else {
      expect([...archive.entries()]).toEqual([...baselineArchive.entries()]);
      expect(diagnostics).not.toContain("semantic.ai_applied");
      if (consent === false) expect(diagnostics).toContain("semantic.screenshot_declined");
      if (aiScenario === "ai_failure") {
        expect(diagnostics).toContain("ai.http_status");
        expect(diagnostics).toContain("semantic.fallback");
      }
    }
  });

  it("opens a ZIP64 entry when only the local-offset field uses a sentinel", async () => {
    const entries = await openZip(zipWithZip64LocalOffsetOnly());

    expect(entries.get("item.txt")).toBe("hello");
  });

  it("uses ZIP64 EOCD values when the legacy directory fields are sentinels", async () => {
    const entries = await openZip(zipWithZip64CentralDirectory());

    expect(entries.get("item.txt")).toBe("hello");
  });
});

function zipWithZip64LocalOffsetOnly(): Blob {
  const encoder = new TextEncoder();
  const name = encoder.encode("item.txt");
  const content = encoder.encode("hello");
  const localLength = 30 + name.length + content.length;
  const centralLength = 46 + name.length + 12;
  const centralOffset = localLength;
  const eocdOffset = centralOffset + centralLength;
  const bytes = new Uint8Array(eocdOffset + 22);
  const view = new DataView(bytes.buffer);

  view.setUint32(0, 0x04034b50, true);
  view.setUint16(4, 45, true);
  view.setUint32(18, content.length, true);
  view.setUint32(22, content.length, true);
  view.setUint16(26, name.length, true);
  bytes.set(name, 30);
  bytes.set(content, 30 + name.length);

  view.setUint32(centralOffset, 0x02014b50, true);
  view.setUint16(centralOffset + 4, 45, true);
  view.setUint16(centralOffset + 6, 45, true);
  view.setUint32(centralOffset + 20, content.length, true);
  view.setUint32(centralOffset + 24, content.length, true);
  view.setUint16(centralOffset + 28, name.length, true);
  view.setUint16(centralOffset + 30, 12, true);
  view.setUint32(centralOffset + 42, 0xffffffff, true);
  bytes.set(name, centralOffset + 46);
  const extraOffset = centralOffset + 46 + name.length;
  view.setUint16(extraOffset, 0x0001, true);
  view.setUint16(extraOffset + 2, 8, true);
  view.setBigUint64(extraOffset + 4, 0n, true);

  view.setUint32(eocdOffset, 0x06054b50, true);
  view.setUint16(eocdOffset + 8, 1, true);
  view.setUint16(eocdOffset + 10, 1, true);
  view.setUint32(eocdOffset + 12, centralLength, true);
  view.setUint32(eocdOffset + 16, centralOffset, true);

  return new Blob([bytes], { type: "application/zip" });
}

function zipWithZip64CentralDirectory(): Blob {
  const encoder = new TextEncoder();
  const name = encoder.encode("item.txt");
  const content = encoder.encode("hello");
  const centralOffset = 30 + name.length + content.length;
  const centralLength = 46 + name.length;
  const zip64End = centralOffset + centralLength;
  const locator = zip64End + 56;
  const eocd = locator + 20;
  const bytes = new Uint8Array(eocd + 22);
  const view = new DataView(bytes.buffer);

  view.setUint32(0, 0x04034b50, true);
  view.setUint16(4, 45, true);
  view.setUint32(18, content.length, true);
  view.setUint32(22, content.length, true);
  view.setUint16(26, name.length, true);
  bytes.set(name, 30);
  bytes.set(content, 30 + name.length);

  view.setUint32(centralOffset, 0x02014b50, true);
  view.setUint16(centralOffset + 4, 45, true);
  view.setUint16(centralOffset + 6, 45, true);
  view.setUint32(centralOffset + 20, content.length, true);
  view.setUint32(centralOffset + 24, content.length, true);
  view.setUint16(centralOffset + 28, name.length, true);
  bytes.set(name, centralOffset + 46);

  view.setUint32(zip64End, 0x06064b50, true);
  view.setBigUint64(zip64End + 4, 44n, true);
  view.setUint16(zip64End + 12, 45, true);
  view.setUint16(zip64End + 14, 45, true);
  view.setBigUint64(zip64End + 24, 1n, true);
  view.setBigUint64(zip64End + 32, 1n, true);
  view.setBigUint64(zip64End + 40, BigInt(centralLength), true);
  view.setBigUint64(zip64End + 48, BigInt(centralOffset), true);

  view.setUint32(locator, 0x07064b50, true);
  view.setBigUint64(locator + 8, BigInt(zip64End), true);
  view.setUint32(locator + 16, 1, true);

  view.setUint32(eocd, 0x06054b50, true);
  view.setUint16(eocd + 8, 0xffff, true);
  view.setUint16(eocd + 10, 0xffff, true);
  view.setUint32(eocd + 12, 0xffffffff, true);
  view.setUint32(eocd + 16, 0xffffffff, true);

  return new Blob([bytes], { type: "application/zip" });
}

function sha256(bytes: ArrayBuffer): string {
  return createHash("sha256").update(new Uint8Array(bytes)).digest("hex");
}

async function openZip(blob: Blob): Promise<Map<string, string>> {
  const bytes = new Uint8Array(await blob.arrayBuffer());
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const entries = new Map<string, string>();
  const end = findEndOfCentralDirectory(view);
  const directory = centralDirectory(view, end);
  let cursor = directory.offset;
  const directoryEnd = directory.offset + directory.size;
  if (directoryEnd > view.byteLength) throw new Error("ZIP central directory exceeds the archive");
  for (let index = 0; index < directory.count; index += 1) {
    if (cursor + 46 > directoryEnd) throw new Error("ZIP central directory entry is truncated");
    expect(view.getUint32(cursor, true)).toBe(0x02014b50);
    const compression = view.getUint16(cursor + 10, true);
    let compressedSize = view.getUint32(cursor + 20, true);
    const uncompressedSize = view.getUint32(cursor + 24, true);
    const nameLength = view.getUint16(cursor + 28, true);
    const extraLength = view.getUint16(cursor + 30, true);
    const commentLength = view.getUint16(cursor + 32, true);
    let localOffset = view.getUint32(cursor + 42, true);
    const extraStart = cursor + 46 + nameLength;
    if (extraStart + extraLength > directoryEnd) throw new Error("ZIP central directory extra data is truncated");
    if (uncompressedSize === 0xffffffff || compressedSize === 0xffffffff || localOffset === 0xffffffff) {
      const zip64 = zip64Values(view, extraStart, extraLength, {
        uncompressedSize: uncompressedSize === 0xffffffff,
        compressedSize: compressedSize === 0xffffffff,
        localOffset: localOffset === 0xffffffff,
      });
      if (compressedSize === 0xffffffff) {
        if (zip64.compressedSize === undefined) throw new Error("ZIP64 compressed size is missing");
        compressedSize = zip64.compressedSize;
      }
      if (localOffset === 0xffffffff) {
        if (zip64.localOffset === undefined) throw new Error("ZIP64 local header offset is missing");
        localOffset = zip64.localOffset;
      }
    }
    const name = new TextDecoder().decode(bytes.slice(cursor + 46, cursor + 46 + nameLength));
    expect(view.getUint32(localOffset, true)).toBe(0x04034b50);
    const localNameLength = view.getUint16(localOffset + 26, true);
    const localExtraLength = view.getUint16(localOffset + 28, true);
    const dataStart = localOffset + 30 + localNameLength + localExtraLength;
    const compressed = bytes.slice(dataStart, dataStart + compressedSize);
    const content = compression === 0 ? compressed : compression === 8 ? inflateRawSync(compressed) : undefined;
    expect(content, `unsupported ZIP compression for ${name}`).toBeDefined();
    entries.set(name, new TextDecoder().decode(content!));
    cursor += 46 + nameLength + extraLength + commentLength;
  }
  expect(entries.size).toBeGreaterThan(0);
  return entries;
}

function findEndOfCentralDirectory(view: DataView): number {
  for (let offset = view.byteLength - 22; offset >= Math.max(0, view.byteLength - 65_557); offset -= 1) {
    if (view.getUint32(offset, true) === 0x06054b50) return offset;
  }
  throw new Error("ZIP end of central directory was not found");
}

function centralDirectory(view: DataView, end: number): { count: number; size: number; offset: number } {
  const count = view.getUint16(end + 10, true);
  const size = view.getUint32(end + 12, true);
  const offset = view.getUint32(end + 16, true);
  if (count !== 0xffff && size !== 0xffffffff && offset !== 0xffffffff) return { count, size, offset };

  const locator = end - 20;
  if (locator < 0 || view.getUint32(locator, true) !== 0x07064b50) {
    throw new Error("ZIP64 end of central directory locator was not found");
  }
  const zip64End = zip64Number(view, locator + 8, "ZIP64 end of central directory offset");
  if (zip64End + 56 > view.byteLength || view.getUint32(zip64End, true) !== 0x06064b50) {
    throw new Error("ZIP64 end of central directory was not found");
  }
  const recordSize = zip64Number(view, zip64End + 4, "ZIP64 end of central directory size");
  if (recordSize < 44 || zip64End + 12 + recordSize > view.byteLength) {
    throw new Error("ZIP64 end of central directory is truncated");
  }
  const disk = view.getUint32(zip64End + 16, true);
  const centralDisk = view.getUint32(zip64End + 20, true);
  const entriesOnDisk = zip64Number(view, zip64End + 24, "ZIP64 entries on disk");
  const entries = zip64Number(view, zip64End + 32, "ZIP64 entry count");
  if (disk !== 0 || centralDisk !== 0 || entriesOnDisk !== entries) {
    throw new Error("multi-disk ZIP archives are not supported");
  }
  return {
    count: count === 0xffff ? entries : count,
    size: size === 0xffffffff ? zip64Number(view, zip64End + 40, "ZIP64 central directory size") : size,
    offset: offset === 0xffffffff ? zip64Number(view, zip64End + 48, "ZIP64 central directory offset") : offset,
  };
}

type Zip64RequiredFields = {
  uncompressedSize: boolean;
  compressedSize: boolean;
  localOffset: boolean;
};

function zip64Values(
  view: DataView,
  start: number,
  length: number,
  required: Zip64RequiredFields,
): { compressedSize?: number; localOffset?: number } {
  let cursor = start;
  const end = start + length;
  while (cursor + 4 <= end) {
    const id = view.getUint16(cursor, true);
    const size = view.getUint16(cursor + 2, true);
    if (cursor + 4 + size > end) throw new Error("ZIP extra data is truncated");
    if (id === 0x0001) {
      let value = cursor + 4;
      const extraEnd = value + size;
      const read = (field: string): number => {
        if (value + 8 > extraEnd) throw new Error(`ZIP64 ${field} is truncated`);
        const result = zip64Number(view, value, `ZIP64 ${field}`);
        value += 8;
        return result;
      };
      if (required.uncompressedSize) read("uncompressed size");
      const compressedSize = required.compressedSize ? read("compressed size") : undefined;
      const localOffset = required.localOffset ? read("local header offset") : undefined;
      return { compressedSize, localOffset };
    }
    cursor += 4 + size;
  }
  throw new Error("ZIP64 central directory data was not found");
}

function zip64Number(view: DataView, offset: number, label: string): number {
  if (offset + 8 > view.byteLength) throw new Error(`${label} is truncated`);
  const value = view.getBigUint64(offset, true);
  if (value > BigInt(Number.MAX_SAFE_INTEGER)) throw new Error(`${label} exceeds JavaScript safe integer range`);
  return Number(value);
}
