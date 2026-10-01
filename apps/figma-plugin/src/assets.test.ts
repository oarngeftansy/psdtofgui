import { describe, expect, it } from "vitest";
import { exportDeclaredAssets } from "./assets";
import { serializeSelection } from "./selection";

function assetNode(type: string, name: string, bytes: Uint8Array, delay = 0) {
  return {
    id: `raw:${name}`,
    name,
    type,
    visible: true,
    locked: false,
    opacity: 1,
    absoluteBoundingBox: { x: 0, y: 0, width: 4, height: 4 },
    fills: [{ type: "IMAGE" }],
    exportAsync: async (settings: { format: string }) => {
      if (delay) await new Promise((resolve) => setTimeout(resolve, delay));
      return bytes;
    },
  } as unknown as SceneNode;
}

describe("declared asset export", () => {
  it("exports each opaque declared key once using PNG for vectors and raster nodes", async () => {
    const vector = assetNode("VECTOR", "Mark", new Uint8Array([1]));
    const raster = assetNode("RECTANGLE", "Hero", new Uint8Array([2, 3]));
    const manifest = serializeSelection([vector, raster]);
    const calls: string[] = [];
    for (const selected of [vector, raster]) {
      (selected as unknown as { exportAsync: (settings: { format: string }) => Promise<Uint8Array> }).exportAsync = async (settings) => {
        calls.push(settings.format);
        return new Uint8Array([calls.length]);
      };
    }
    const lookup = new Map(manifest.resources.map((resource, index) => [resource.key, [vector, raster][index]! ]));

    const resources = [];
    for await (const resource of exportDeclaredAssets(manifest, lookup)) resources.push(resource);

    expect(calls).toEqual(["PNG", "PNG"]);
    expect(resources.map((resource) => [resource.key, resource.mime_type, resource.bytes.length])).toEqual([
      ["asset-1", "image/png", 1],
      ["asset-2", "image/png", 1],
    ]);
  });

  it("limits simultaneous exports to four and reports only a selected display label on failure", async () => {
    let active = 0;
    let maximum = 0;
    const selected = Array.from({ length: 5 }, (_, index) => {
      const result = assetNode("RECTANGLE", `Card ${index + 1}`, new Uint8Array([index]), 5);
      (result as unknown as { exportAsync: () => Promise<Uint8Array> }).exportAsync = async () => {
        active += 1;
        maximum = Math.max(maximum, active);
        await new Promise((resolve) => setTimeout(resolve, 5));
        active -= 1;
        if (index === 4) throw new Error("raw:secret-node");
        return new Uint8Array([index]);
      };
      return result;
    });
    const manifest = serializeSelection(selected);
    const lookup = new Map(manifest.resources.map((resource, index) => [resource.key, selected[index]! ]));

    await expect(async () => {
      for await (const _ of exportDeclaredAssets(manifest, lookup)) { /* drain */ }
    }).rejects.toThrow("Card 5");
    expect(maximum).toBeLessThanOrEqual(4);
  });

  it("exports one shared asset for identical image render recipes", async () => {
    const first = assetNode("RECTANGLE", "First", new Uint8Array([1]));
    const second = assetNode("RECTANGLE", "Second", new Uint8Array([2]));
    const exports: string[] = [];
    for (const selected of [first, second]) {
      (selected as unknown as { exportAsync: () => Promise<Uint8Array> }).exportAsync = async () => {
        exports.push(selected.name);
        return new Uint8Array([exports.length]);
      };
    }
    const manifest = serializeSelection([
      Object.assign(first, { fills: [{ type: "IMAGE", imageHash: "shared-image" }] }),
      Object.assign(second, { fills: [{ type: "IMAGE", imageHash: "shared-image" }] }),
    ]);
    const lookup = new Map(manifest.resources.map((resource, index) => [resource.key, [first, second][index]!]));

    const resources = [];
    for await (const resource of exportDeclaredAssets(manifest, lookup)) resources.push(resource);

    expect(manifest.resources).toHaveLength(1);
    expect(resources).toHaveLength(1);
    expect(exports).toEqual(["First"]);
  });

  it("exports vector resources as PNG for the new-project Writer", async () => {
    const vector = assetNode("VECTOR", "Mark", new Uint8Array([1]));
    const manifest = serializeSelection([vector]);
    const calls: string[] = [];
    (vector as unknown as { exportAsync: (settings: { format: string }) => Promise<Uint8Array> }).exportAsync = async (settings) => {
      calls.push(settings.format);
      return new Uint8Array([1]);
    };

    const resources = [];
    for await (const resource of exportDeclaredAssets(manifest, new Map([["asset-1", vector]]))) resources.push(resource);

    expect(calls).toEqual(["PNG"]);
    expect(resources).toEqual([{ key: "asset-1", mime_type: "image/png", bytes: new Uint8Array([1]) }]);
  });

  it("exports ordinary vector and boolean layers without image fills as PNG using the shared resource plan", async () => {
    const vector = assetNode("VECTOR", "Vector", new Uint8Array([1]));
    const boolean = assetNode("BOOLEAN_OPERATION", "Boolean", new Uint8Array([2]));
    Object.assign(vector, { fills: [] });
    Object.assign(boolean, { fills: [] });
    const calls: string[] = [];
    for (const selected of [vector, boolean]) {
      (selected as unknown as { exportAsync: (settings: { format: string }) => Promise<Uint8Array> }).exportAsync = async ({ format }) => {
        calls.push(format);
        return new Uint8Array([calls.length]);
      };
    }
    const manifest = serializeSelection([vector, boolean]);
    const lookup = new Map(manifest.resources.map((resource, index) => [resource.key, [vector, boolean][index]! ]));

    const resources = [];
    for await (const resource of exportDeclaredAssets(manifest, lookup)) resources.push(resource);

    expect(calls).toEqual(["PNG", "PNG"]);
    expect(resources.map((resource) => resource.mime_type)).toEqual(["image/png", "image/png"]);
  });

  it("fails closed when Figma rejects PNG export for a boolean operation", async () => {
    const boolean = assetNode("BOOLEAN_OPERATION", "Union", new Uint8Array([9]));
    Object.assign(boolean, { fills: [] });
    const calls: string[] = [];
    (boolean as unknown as { exportAsync: (settings: { format: string }) => Promise<Uint8Array> }).exportAsync = async ({ format }) => {
      calls.push(format);
      throw new Error(`Figma rejected this boolean ${format}`);
    };
    const manifest = serializeSelection([boolean]);
    const lookup = new Map([[manifest.resources[0]!.key, boolean]]);

    await expect(async () => {
      for await (const _resource of exportDeclaredAssets(manifest, lookup)) { /* drain */ }
    }).rejects.toThrow("Union");
    expect(calls).toEqual(["PNG"]);
  });

  it("preserves a privacy-safe bridge stage code while redacting ordinary errors", async () => {
    const raster = assetNode("RECTANGLE", "Card", new Uint8Array([1]));
    const manifest = serializeSelection([raster]);
    const safe = Object.assign(new Error("private details"), { safeCode: "resource_canvas_export_failed" });

    await expect(async () => {
      for await (const _resource of exportDeclaredAssets(
        manifest,
        new Map([[manifest.resources[0]!.key, raster]]),
        async () => { throw safe; },
      )) { /* drain */ }
    }).rejects.toBe(safe);
  });
});
