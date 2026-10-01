import { describe, expect, it, vi } from "vitest";
import { isSupportedProjectArchive, ProjectWorkflowClient, WorkflowError, type WorkflowRunOptions } from "./project-client";

it("accepts a supported archive extension regardless of browser MIME label", () => {
  expect(isSupportedProjectArchive(new File(["rar"], "HIFI_Replace.rar", { type: "application/x-rar" }))).toBe(true);
  expect(isSupportedProjectArchive(new File(["other"], "HIFI_Replace.txt", { type: "application/vnd.rar" }))).toBe(false);
});
import type { SelectionManifest } from "./selection";

const manifest: SelectionManifest = {
  version: 1,
  display_name: "Checkout",
  top_level_nodes: [{ id: "node-1", name: "Checkout", type: "FRAME", bounds: { x: 0, y: 0, width: 1, height: 1 }, children: [], resource_keys: ["asset-1"] }],
  resources: [{ key: "asset-1", mime_type: "image/png", size: 1 }],
  warnings: [],
};
const resources = [{ key: "asset-1", mime_type: "image/png" as const, bytes: new Uint8Array([1]) }];
const projectId = "1".repeat(32);
const jobId = "2".repeat(32);
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const project = (name = "Quiz") => ({ version: 1, project_id: projectId, display_name: name, packages: [{ name: "Quiz", resource_count: 1 }] });
const selection = { version: 1, selection_id: "a".repeat(32), display_name: "Checkout", top_level_summaries: [], preview_urls: [], warnings: [] };
const job = { version: 1, job_id: jobId, project_id: projectId, status: "ready_for_review" };
const packageView = (status: string, download_name: string | null = null) => ({ version: 1, job_id: jobId, status, stage: status, progress: status === "ready" ? 100 : 90, download_name, sha256: status === "ready" ? "b".repeat(64) : null, diagnostics: [] });
const writerCandidate = (status = "awaiting_review", build_id = "4".repeat(32), generation = 1) => ({
  version: 1, build_id, status, stage: status, progress: 100, download_name: "Quiz-FairyGUI.zip",
  sha256: "4a70fe9aa6436e02c2dea340fbd1e352e4ef2d8ce6ca52ad25d4b95471fc8bf2", byte_size: 3, artifact_ready: true, diagnostics: [], generation,
});
const writerReview = (build_id = "4".repeat(32), generation = 1) => ({
  version: 1, build_id, generation,
  dispositions: [{ version: 1, id: "disposition:0011223344556677", sourceNodeId: "figma:node", sourceName: "Hero", sourceType: "TEXT", level: "editable_risk", reason: "rich_text_runs", defaultStrategy: "preserve-editable", allowedStrategies: ["preserve-editable", "rasterize-subtree"], visualImpact: "may_differ", editabilityImpact: "unchanged", componentImpact: "unchanged", blocksApproval: false, details: { runCount: 2, preservedProperties: ["content"], unsupportedProperties: ["fontSize"] } }],
  image_reviews: [{ resource_id: "asset", source_node_id: "figma:node", label: "Hero", evidence_kind: "source-image", source_preview_url: `/v1/figma/selections/${"a".repeat(32)}/resources/asset`, generated_asset_url: `/v1/new-fgui-projects/${build_id}/previews/resources/asset`, width: 1, height: 1, nine_slice: false, crop_bounds_match: true, transparency_preserved: true }],
  component_reviews: [{ component_id: "component", label: "Screen", evidence_kind: "structured-summary", rendered_preview_url: null, object_count: 2, text_count: 1, resource_refs: 1, component_refs: 0, hierarchy_valid: true, geometry_valid: true, text_valid: true }],
  package_review: { package_name: "Generated", fairy_gui_version: "6.1.4", publish_target: "unity", components_added: 1, resources_added: 1, component_names: ["Screen"], resource_names: ["Hero"], resource_closure_valid: true, naming_conflicts: [], integrity_valid: true },
  checks: [{ id: "review:0123456789abcdef", severity: "WARNING", message: "Review node", issue_id: "review:0123456789abcdef", issue_kind: "raster-fallback", uir_node_id: "uir:node", source_node_id: "figma:node", actionable: true, allowed_strategies: ["preserve-editable"] }],
  warning_ids: ["review:0123456789abcdef"], approvable: true,
});

const pairedScreenshotCallbacks: WorkflowRunOptions = {
  onScreenshotConsent: async () => false,
  requestScreenshot: async () => ({ mimeType: "image/png", bytes: new Uint8Array([1]) }),
};
// @ts-expect-error Screenshot workflow callbacks must be supplied as a pair.
const incompleteScreenshotCallbacks: WorkflowRunOptions = { onScreenshotConsent: async () => false };
void pairedScreenshotCallbacks;
void incompleteScreenshotCallbacks;

function successfulFetch(mode: "create" | "update") {
  return vi.fn(async (url: string, init: RequestInit) => {
    const path = new URL(url).pathname;
    if (path === "/v1/projects/from-template") return json(project(), 201);
    if (path === "/v1/projects/uploads") return json(project("GameUI.zip"), 201);
    if (path === "/v1/figma/selections/uploads") return json({ version: 1, upload_id: "3".repeat(32) }, 201);
    if (path.endsWith("/manifest")) return json({ version: 1, state: "manifest_received" });
    if (path.includes("/resources/")) return json({ version: 1, state: "resources_pending" });
    if (path.endsWith("/commit")) return json(selection);
    if (path.endsWith("/jobs")) return json(job);
    if (path.endsWith("/package") && init.method === "POST") return json(packageView("checking"), 202);
    if (path.endsWith("/package")) return json(packageView("ready", `${mode === "create" ? "Quiz-Figma新建" : "GameUI-Figma更新"}-20260730-1530.zip`));
    if (path.endsWith("/download")) return new Response(new Blob(["zip"]), { headers: { "Content-Type": "application/zip", "Content-Disposition": `attachment; filename*=UTF-8''${encodeURIComponent(mode === "create" ? "Quiz-Figma新建-20260730-1530.zip" : "GameUI-Figma更新-20260730-1530.zip")}` } });
    throw new Error(`unexpected ${init.method} ${path}`);
  });
}

describe("ProjectWorkflowClient", () => {
  it("loads the two registered local font identities", async () => {
    const fetchImpl = vi.fn(async () => json({
      version: 1,
      fonts: [
        { family: "HYZhengYuan-75S", postscript_name: "HYZhengYuan-GES", source_filename: "HYZhengYuan-75S.ttf", sha256: "0".repeat(64), installed: true, matched_filename: "HYZhengYuan-75S.ttf" },
        { family: "CoreSansESW01-55Medium", postscript_name: "CoreSansESW01-55Medium", source_filename: "core sans es w01_55 medium.ttf", sha256: "a".repeat(64), installed: false, matched_filename: null },
      ],
    }));
    const client = new ProjectWorkflowClient({ serverOrigin: "https://fgui.test", pluginToken: "token", fetchImpl });

    const fonts = await client.fixedFonts();

    expect(fonts[0]).toMatchObject({ family: "HYZhengYuan-75S", installed: true, matchedFilename: "HYZhengYuan-75S.ttf" });
    expect(fonts[1]).toMatchObject({ family: "CoreSansESW01-55Medium", installed: false });
    expect(fonts[1]).not.toHaveProperty("matchedFilename");
  });

  it("uploads a PSD for local inspection and parses lossless blockers", async () => {
    const fetchImpl = vi.fn(async (url: string, init: RequestInit) => {
      expect(new URL(url).pathname).toBe("/v1/hifi-sources/psd/inspect");
      expect(init.method).toBe("POST");
      expect(init.body).toBeInstanceOf(FormData);
      return json({
        source_name: "SampleHomePage.psd",
        byte_size: 266052490,
        sha256: "e".repeat(64),
        width: 1080,
        height: 2340,
        depth: 16,
        color_mode: "RGB",
        layer_count: 438,
        kind_counts: { curves: 3, group: 66, huesaturation: 2, pixel: 57, shape: 230, smartobject: 44, type: 36 },
        text_layer_count: 36,
        smart_object_count: 44,
        adjustment_layer_count: 5,
        effect_layer_count: 140,
        blocking_issues: ["smart_objects_require_equivalence_check"],
        warnings: ["16_bit_pixels_must_not_be_downconverted"],
      });
    });
    const client = new ProjectWorkflowClient({ serverOrigin: "https://fgui.test", pluginToken: "token", fetchImpl });

    const report = await client.inspectPsd(new File(["8BPS"], "SampleHomePage.psd"));

    expect(report).toMatchObject({ width: 1080, height: 2340, depth: 16, layerCount: 438, textLayerCount: 36, smartObjectCount: 44 });
    expect(report.blockingIssues).toEqual(["smart_objects_require_equivalence_check"]);
  });

  it("persists a PSD source and parses its HIFI layer inventory", async () => {
    const fetchImpl = vi.fn(async (url: string, init: RequestInit) => {
      expect(new URL(url).pathname).toBe("/v1/hifi-sources/psd");
      expect(init.method).toBe("POST");
      expect(init.body).toBeInstanceOf(FormData);
      return json({
        version: 1,
        source_id: "e".repeat(64),
        inspection: {
          source_name: "screen.psd", byte_size: 16, sha256: "e".repeat(64), width: 1080, height: 2340,
          depth: 8, color_mode: "RGB", layer_count: 1, kind_counts: { type: 1 }, text_layer_count: 1,
          smart_object_count: 0, adjustment_layer_count: 0, effect_layer_count: 0, blocking_issues: [], warnings: [],
        },
        layers: [{
          id: `psd-layer:${"e".repeat(64)}:11`, native_id: 11, parent_id: null, document_index: 0,
          sibling_index: 0, name: "Title", path: ["Title"], kind: "type", bounds: [40, 50, 440, 110],
          visible: true, effective_visible: true, opacity: 255, blend_mode: "normal", clipping: false,
          text: "开始游戏", has_pixel_mask: false, has_vector_mask: false, has_effects: false,
          text_style: { runs: [{ start: 0, length: 4, font_name: "HYZhengYuan-GES", font_size: 36, faux_bold: false, faux_italic: false, leading: 42, tracking: 0, fill_rgba: [1, .8, .2, 1] }], transform: [1, 0, 0, 1, 40, 50], paragraph_justification: 1, anti_alias: 4 },
          effects: [{ kind: "ColorOverlay", enabled: true, blend_mode: "normal", opacity: 100, color_rgba: [1, .8, .2, 1], size: null, angle: null, distance: null, spread: null, choke: null, position: null }],
        }],
      }, 201);
    });
    const client = new ProjectWorkflowClient({ serverOrigin: "https://fgui.test", pluginToken: "token", fetchImpl });

    const source = await client.uploadPsd(new File(["8BPS"], "screen.psd"));

    expect(source.sourceId).toBe("e".repeat(64));
    expect(source.inspection.layerCount).toBe(1);
    expect(source.layers[0]).toMatchObject({ nativeId: 11, name: "Title", text: "开始游戏", bounds: [40, 50, 440, 110] });
    expect(source.layers[0]?.textStyle?.runs[0]).toMatchObject({ fontName: "HYZhengYuan-GES", fontSize: 36, fillRgba: [1, .8, .2, 1] });
    expect(source.layers[0]?.effects[0]).toMatchObject({ kind: "ColorOverlay", enabled: true, blendMode: "normal", opacity: 100, colorRgba: [1, .8, .2, 1] });
  });

  it("downloads the authenticated PSD composite audit baseline", async () => {
    const sourceId = "e".repeat(64);
    const fetchImpl = vi.fn(async (url: string, init: RequestInit) => {
      expect(new URL(url).pathname).toBe(`/v1/hifi-sources/psd/${sourceId}/composite`);
      expect(init.method).toBe("GET");
      return new Response(new Blob(["png"]), {
        headers: { "Content-Type": "image/png", "X-PSD-Source-SHA256": sourceId },
      });
    });
    const client = new ProjectWorkflowClient({ serverOrigin: "https://fgui.test", pluginToken: "token", fetchImpl });

    const composite = await client.psdComposite(sourceId);

    expect(composite.size).toBe(3);
    expect(composite.type).toBe("image/png");
  });

  it("starts mapping directly from a persisted PSD source", async () => {
    const sourceId = "e".repeat(64);
    const sessionId = "d".repeat(32);
    const target = {
      version: 1 as const, projectId, projectFingerprint: "f".repeat(64), packageId: "pkg", packageName: "Tower",
      directory: "Panel", componentId: "main", componentName: "Panel_Tower_Main", componentRelativePath: "assets/Tower/Panel/Panel_Tower_Main.xml",
    };
    const fetchImpl = vi.fn(async (url: string, init: RequestInit) => {
      const path = new URL(url).pathname;
      if (path === "/v1/hifi-replacements/from-psd") return json({
        version: 1, session_id: sessionId, status: "mapping", selection_id: sourceId,
        target: { version: 1, project_id: projectId, project_fingerprint: target.projectFingerprint, package_id: "pkg", package_name: "Tower", directory: "Panel", component_id: "main", component_name: "Panel_Tower_Main", component_relative_path: target.componentRelativePath },
        mapping_revision: 1, unresolved_count: 1, artifact_ready: false,
      }, 201);
      if (path.endsWith("/mapping")) return json({ version: 1, mapping_revision: 1, unresolved_count: 1, items: [{ version: 1, item_id: "old:title", old_object_id: "title", old_name: "Title", old_object_type: "text", old_resource_id: null, figma_node_id: `psd-layer:${sourceId}:11`, figma_name: "Title", status: "suggested", score: .8, evidence: { version: 1, name_score: 1, position_score: .8, size_score: .8, type_score: 1, parent_score: 1, order_score: 1 }, action: null, candidates: [`psd-layer:${sourceId}:11`], old_bounds: [.1, .1, .2, .1], figma_bounds: [.1, .1, .2, .1] }] });
      throw new Error(`unexpected ${init.method} ${path}`);
    });
    const client = new ProjectWorkflowClient({ serverOrigin: "https://fgui.test", pluginToken: "token", fetchImpl });

    const started = await client.createPsdHifiReplacement(sourceId, { projectId, displayName: "old.zip", packages: [] }, target);

    expect(started.replacement.selectionId).toBe(sourceId);
    expect(started.mapping.items[0]?.figmaNodeId).toBe(`psd-layer:${sourceId}:11`);
  });

  it("loads selectable HIFI targets and normalized mapping bounds", async () => {
    const sessionId = "d".repeat(32);
    const fetchImpl = vi.fn(async (url: string) => {
      const path = new URL(url).pathname;
      if (path.endsWith("/hifi-targets")) return json({
        version: 1,
        project_id: projectId,
        project_fingerprint: "e".repeat(64),
        packages: [{ version: 1, package_id: "pkg", name: "MyVillage", directories: [{ version: 1, path: "Panel", selectable: true, reason: null, components: [{ version: 1, resource_id: "cmp", name: "Root", relative_path: "assets/MyVillage/Panel/Root.xml", selectable: true, reason: null }] }] }],
      });
      if (path.endsWith("/mapping")) return json({
        version: 1,
        policy_revision: 24,
        mapping_revision: 2,
        old_canvas_size: [800, 600],
        source_canvas_size: [900, 1800],
        unresolved_count: 1,
        items: [{ version: 1, item_id: "old:title", old_object_id: "title", old_name: "Title", old_object_type: "text", old_resource_id: null, default_visible: false, preserve_runtime_text: true, figma_node_id: "12:4", figma_name: "Title", status: "suggested", score: .82, evidence: { version: 1, name_score: 1, position_authoritative: false, position_score: .9, size_score: .8, type_score: 1, parent_score: 1, order_score: 1 }, action: null, candidates: ["12:4"], old_bounds: [.1, .2, .3, .4], figma_bounds: [.12, .2, .3, .4], owned_source_ids: ["12:4", "12:5"], owned_group_id: "12:3", retained_source_ids: ["12:6"], generated_state: false, graph_conversion_proven: true, out_of_scope: true }],
      });
      throw new Error(`unexpected ${path}`);
    });
    const client = new ProjectWorkflowClient({ serverOrigin: "https://fgui.test", pluginToken: "token", fetchImpl });

    const targets = await client.hifiTargets(projectId);
    const mapping = await client.hifiMapping(sessionId);

    expect(targets.packages[0]?.directories[0]?.components[0]).toMatchObject({ name: "Root", selectable: true });
    expect(mapping.items[0]).toMatchObject({ itemId: "old:title", oldBounds: [.1, .2, .3, .4], figmaBounds: [.12, .2, .3, .4] });
    expect(mapping.policyRevision).toBe(24);
    expect(mapping.oldCanvasSize).toEqual({ width: 800, height: 600 });
    expect(mapping.sourceCanvasSize).toEqual({ width: 900, height: 1800 });
    expect(mapping.items[0].ownedSourceIds).toEqual(["12:4", "12:5"]);
    expect(mapping.items[0]).toMatchObject({ defaultVisible: false, preserveRuntimeText: true, graphConversionProven: true, positionAuthoritative: false, outOfScope: true });
  });

  it("parses object-level HIFI review evidence and binds approval to its candidate hash", async () => {
    const sessionId = "d".repeat(32);
    const candidateSha256 = "e".repeat(64);
    const target = { version: 1, project_id: projectId, project_fingerprint: "f".repeat(64), package_id: "pkg", package_name: "MyVillage", directory: "Panel", component_id: "cmp", component_name: "Root", component_relative_path: "assets/MyVillage/Panel/Root.xml" };
    const fetchImpl = vi.fn(async (url: string, init: RequestInit) => {
      const path = new URL(url).pathname;
      if (path.endsWith("/review")) return json({ version: 1, session_id: sessionId, mapping_revision: 2, target, changed_files: [{ version: 1, relative_path: target.component_relative_path, operation: "replace", before_sha256: "a".repeat(64), after_sha256: "b".repeat(64), summary: "changed" }], object_diffs: [{ version: 1, item_id: "old:title", kind: "changed", old_object_id: "title", old_name: "Title", figma_node_id: "12:4", figma_name: "Title", changed_fields: ["xy"], summary: "修改视觉字段：xy" }], protected_checks_passed: true, parse_coverage_complete: true, approvable: true, candidate_sha256: candidateSha256, warnings: [], editor_check_required: true });
      if (path.endsWith("/editor-verify")) return json({ version: 1, session_id: sessionId, candidate_sha256: candidateSha256, editor_found: true, editor_version: "6.1.4", project_opened: true, component_opened: true, render_captured: true, screenshot_url: `/v1/hifi-replacements/${sessionId}/editor-screenshot`, screenshot_sha256: "c".repeat(64), screenshot_width: 1078, screenshot_height: 1855, expected_width: 1080, expected_height: 1920, full_frame: false, mean_pixel_difference: null, approvable: false, warnings: ["尚未获得完整画面。"] });
      if (path.endsWith("/editor-screenshot")) return new Response(new Blob(["png"]), { status: 200, headers: { "Content-Type": "image/png" } });
      if (path.endsWith("/approve")) return json({ version: 1, session_id: sessionId, status: "approved", selection_id: "a".repeat(32), target, mapping_revision: 2, unresolved_count: 0, artifact_ready: true });
      throw new Error(`unexpected ${init.method} ${path}`);
    });
    const client = new ProjectWorkflowClient({ serverOrigin: "https://fgui.test", pluginToken: "token", fetchImpl });

    const review = await client.reviewHifiReplacement(sessionId);
    const verification = await client.verifyHifiReplacementInEditor(sessionId);
    const screenshot = await client.hifiEditorScreenshot(sessionId);
    await client.approveHifiReplacement(sessionId, review.candidateSha256!);

    expect(review.objectDiffs[0]).toMatchObject({ kind: "changed", changedFields: ["xy"] });
    expect(review.approvable).toBe(false); // Legacy review policy must be revalidated.
    expect(verification).toMatchObject({ renderCaptured: true, fullFrame: false, screenshotWidth: 1078, expectedWidth: 1080 });
    expect(screenshot.type).toBe("image/png");
    const approve = (fetchImpl.mock.calls as unknown as Array<[string, RequestInit]>).find(([url]) => new URL(url).pathname.endsWith("/approve"))!;
    expect(JSON.parse(String(approve[1].body))).toMatchObject({ editor_version: "6.1.4", candidate_sha256: candidateSha256 });
  });

  it("uploads a selection and starts the strict Writer request without legacy fields", async () => {
    const buildId = "4".repeat(32);
    const candidate = writerCandidate("awaiting_review", buildId);
    const fetchImpl = vi.fn(async (url: string, init: RequestInit) => {
      const path = new URL(url).pathname;
      if (path === "/v1/figma/selections/uploads") return json({ version: 1, upload_id: "3".repeat(32) }, 201);
      if (path.endsWith("/manifest")) return json({ version: 1, state: "manifest_received" });
      if (path.includes("/resources/")) return json({ version: 1, state: "resources_pending" });
      if (path.endsWith("/commit")) return json(selection);
      if (path.endsWith("/new-fgui-projects")) return json(candidate, 202);
      throw new Error(`unexpected ${init.method} ${path}`);
    });
    const client = new ProjectWorkflowClient({ serverOrigin: "https://fgui.test", pluginToken: "token", fetchImpl });

    const result = await client.createNewProjectCandidate(manifest, resources, "Quiz");

    const start = (fetchImpl.mock.calls as unknown as Array<[string, RequestInit]>).find(([url]) => new URL(url).pathname.endsWith("/new-fgui-projects"))!;
    expect(JSON.parse(String(start[1].body))).toEqual({ version: 1, project_name: "Quiz" });
    expect(result.candidate).toMatchObject({ buildId, generation: 1, status: "awaiting_review" });
  });

  it("rejects an invalid Writer project name before uploading selection data", async () => {
    const fetchImpl = vi.fn();
    const client = new ProjectWorkflowClient({ serverOrigin: "https://fgui.test", pluginToken: "token", fetchImpl });

    await expect(
      client.createNewProjectCandidate(manifest, resources, "Village UI"),
    ).rejects.toMatchObject({ code: "validation" });
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("does not misreport a rejected selection manifest as an invalid project name", async () => {
    const fetchImpl = vi.fn(async (url: string) => {
      const path = new URL(url).pathname;
      if (path === "/v1/figma/selections/uploads") return json({ version: 1, upload_id: "3".repeat(32) }, 201);
      if (path.endsWith("/manifest")) return json({ detail: { code: "invalid_selection_manifest" } }, 400);
      throw new Error(`unexpected ${path}`);
    });
    const client = new ProjectWorkflowClient({ serverOrigin: "https://fgui.test", pluginToken: "token", fetchImpl });

    await expect(client.createNewProjectCandidate(manifest, resources, "合法工程名"))
      .rejects.toMatchObject({ code: "selection_invalid" });
  });

  it("strictly parses review evidence and permits only server-declared adjustment strategies", async () => {
    const buildId = "4".repeat(32);
    const responses = [json(writerReview()), json(writerCandidate("adjusting"))];
    const fetchImpl = vi.fn().mockImplementation(async () => responses.shift()!);
    const client = new ProjectWorkflowClient({ serverOrigin: "https://fgui.test", pluginToken: "token", fetchImpl });
    const candidate = { buildId, generation: 1, status: "awaiting_review", stage: "awaiting_review", progress: 100, downloadName: "Quiz-FairyGUI.zip", sha256: "a".repeat(64), byteSize: 3, diagnostics: [] } as const;

    const review = await client.reviewNewProject(candidate);
    expect(review.imageReviews[0]).toMatchObject({ sourceNodeId: "figma:node", evidenceKind: "source-image", label: "Hero" });
    expect(review.componentReviews[0]).toMatchObject({ evidenceKind: "structured-summary" });
    expect(review.dispositions[0]).toMatchObject({ level: "editable_risk", reason: "rich_text_runs", sourceName: "Hero", details: { runCount: 2, preservedProperties: ["content"], unsupportedProperties: ["fontSize"] } });
    await expect(client.adjustNewProject(candidate, review, review.checks[0]!.id, "rasterize-subtree")).rejects.toMatchObject({ code: "validation" });
    const adjusted = await client.adjustNewProject(candidate, review, review.checks[0]!.id, "preserve-editable");
    expect(adjusted.status).toBe("adjusting");
    expect(JSON.parse(String((fetchImpl.mock.calls.at(-1)?.[1] as RequestInit).body))).toEqual({ version: 1, candidate_id: buildId, generation: 1, issue_id: "review:0123456789abcdef", uir_node_id: "uir:node", strategy: "preserve-editable" });
  });

  it("accepts an automatic static-layout disposition with explicit reflow impact", async () => {
    const payload = writerReview();
    payload.dispositions[0] = {
      ...payload.dispositions[0],
      sourceType: "FRAME",
      level: "native",
      reason: "native_static_layout",
      defaultStrategy: null,
      allowedStrategies: [],
      visualImpact: "unchanged",
      editabilityImpact: "layout_reflow_not_editable",
      details: null,
    } as never;
    const client = new ProjectWorkflowClient({ serverOrigin: "https://fgui.test", pluginToken: "token", fetchImpl: vi.fn().mockResolvedValue(json(payload)) });

    const review = await client.reviewNewProject({ buildId: "4".repeat(32), generation: 1, status: "awaiting_review", stage: "awaiting_review", progress: 100, downloadName: "Quiz-FairyGUI.zip", sha256: "a".repeat(64), byteSize: 3, diagnostics: [] });

    expect(review.dispositions[0]).toMatchObject({ reason: "native_static_layout", editabilityImpact: "layout_reflow_not_editable" });
  });

  it("accepts a transform wrapper as an automatic native structure", async () => {
    const payload = writerReview();
    payload.dispositions[0] = {
      ...payload.dispositions[0],
      sourceType: "TRANSFORM_GROUP",
      level: "native",
      reason: "native_structure",
      defaultStrategy: null,
      allowedStrategies: [],
      visualImpact: "unchanged",
      editabilityImpact: "unchanged",
      details: null,
    } as never;
    const client = new ProjectWorkflowClient({ serverOrigin: "https://fgui.test", pluginToken: "token", fetchImpl: vi.fn().mockResolvedValue(json(payload)) });

    const review = await client.reviewNewProject({ buildId: "4".repeat(32), generation: 1, status: "awaiting_review", stage: "awaiting_review", progress: 100, downloadName: "Quiz-FairyGUI.zip", sha256: "a".repeat(64), byteSize: 3, diagnostics: [] });

    expect(review.dispositions[0]).toMatchObject({ sourceType: "TRANSFORM_GROUP", reason: "native_structure" });
  });

  it.each([
    { level: "unknown" },
    { reason: "unknown" },
    { allowedStrategies: ["rasterize-subtree", "rasterize-subtree"] },
    { defaultStrategy: "include-contained-definition" },
    { blocksApproval: true },
    { details: { runCount: 2, preservedProperties: ["content", "content"], unsupportedProperties: ["fontSize"] } },
    { details: { runCount: 10_000_000_000, preservedProperties: ["content"], unsupportedProperties: ["fontSize"] } },
    { details: { runCount: 1.5, preservedProperties: ["content"], unsupportedProperties: ["fontSize"] } },
    { details: { runCount: 2, preservedProperties: ["content"], unsupportedProperties: Array.from({ length: 33 }, (_, index) => `fact-${index}`) } },
    { details: { runCount: 2, preservedProperties: ["content"], unsupportedProperties: ["fontSize"], privateFact: "forbidden" } },
  ])("rejects an invalid conversion disposition %#", async (update) => {
    const payload = writerReview();
    payload.dispositions[0] = { ...payload.dispositions[0], ...update } as never;
    const client = new ProjectWorkflowClient({ serverOrigin: "https://fgui.test", pluginToken: "token", fetchImpl: vi.fn().mockResolvedValue(json(payload)) });
    await expect(client.reviewNewProject({ buildId: "4".repeat(32), generation: 1, status: "awaiting_review", stage: "awaiting_review", progress: 100, downloadName: "Quiz-FairyGUI.zip", sha256: "a".repeat(64), byteSize: 3, diagnostics: [] })).rejects.toMatchObject({ code: "invalid_response" });
  });

  it("rejects a conversion disposition with missing details", async () => {
    const payload = writerReview();
    delete (payload.dispositions[0] as Partial<(typeof payload.dispositions)[number]>).details;
    const client = new ProjectWorkflowClient({ serverOrigin: "https://fgui.test", pluginToken: "token", fetchImpl: vi.fn().mockResolvedValue(json(payload)) });
    await expect(client.reviewNewProject({ buildId: "4".repeat(32), generation: 1, status: "awaiting_review", stage: "awaiting_review", progress: 100, downloadName: "Quiz-FairyGUI.zip", sha256: "a".repeat(64), byteSize: 3, diagnostics: [] })).rejects.toMatchObject({ code: "invalid_response" });
  });

  it("rejects rich-text details on a non-rich disposition", async () => {
    const payload = writerReview();
    payload.dispositions[0] = { ...payload.dispositions[0], sourceType: "FRAME", level: "raster_preserved", reason: "visual_effect", defaultStrategy: "rasterize-subtree", allowedStrategies: ["rasterize-subtree"], visualImpact: "visual_preserved", editabilityImpact: "subtree_not_editable" };
    const client = new ProjectWorkflowClient({ serverOrigin: "https://fgui.test", pluginToken: "token", fetchImpl: vi.fn().mockResolvedValue(json(payload)) });
    await expect(client.reviewNewProject({ buildId: "4".repeat(32), generation: 1, status: "awaiting_review", stage: "awaiting_review", progress: 100, downloadName: "Quiz-FairyGUI.zip", sha256: "a".repeat(64), byteSize: 3, diagnostics: [] })).rejects.toMatchObject({ code: "invalid_response" });
  });

  it("accepts null details for a legacy explicit-raster rich-text risk", async () => {
    const payload = writerReview();
    payload.dispositions[0] = { ...payload.dispositions[0], defaultStrategy: "rasterize-subtree", allowedStrategies: ["rasterize-subtree", "preserve-editable"], visualImpact: "visual_preserved", editabilityImpact: "text_not_editable", details: null } as never;
    const client = new ProjectWorkflowClient({ serverOrigin: "https://fgui.test", pluginToken: "token", fetchImpl: vi.fn().mockResolvedValue(json(payload)) });

    const review = await client.reviewNewProject({ buildId: "4".repeat(32), generation: 1, status: "awaiting_review", stage: "awaiting_review", progress: 100, downloadName: "Quiz-FairyGUI.zip", sha256: "a".repeat(64), byteSize: 3, diagnostics: [] });

    expect(review.dispositions[0]?.details).toBeNull();
  });

  it("rejects null details for a fabricated non-text legacy rich-text risk", async () => {
    const payload = writerReview();
    payload.dispositions[0] = { ...payload.dispositions[0], sourceType: "FRAME", defaultStrategy: "rasterize-subtree", allowedStrategies: ["rasterize-subtree", "preserve-editable"], visualImpact: "visual_preserved", editabilityImpact: "text_not_editable", details: null } as never;
    const client = new ProjectWorkflowClient({ serverOrigin: "https://fgui.test", pluginToken: "token", fetchImpl: vi.fn().mockResolvedValue(json(payload)) });

    await expect(client.reviewNewProject({ buildId: "4".repeat(32), generation: 1, status: "awaiting_review", stage: "awaiting_review", progress: 100, downloadName: "Quiz-FairyGUI.zip", sha256: "a".repeat(64), byteSize: 3, diagnostics: [] })).rejects.toMatchObject({ code: "invalid_response" });
  });

  it("rejects null details for a reviewed preserve-editable rich-text risk", async () => {
    const payload = writerReview();
    payload.dispositions[0] = { ...payload.dispositions[0], details: null } as never;
    const client = new ProjectWorkflowClient({ serverOrigin: "https://fgui.test", pluginToken: "token", fetchImpl: vi.fn().mockResolvedValue(json(payload)) });

    await expect(client.reviewNewProject({ buildId: "4".repeat(32), generation: 1, status: "awaiting_review", stage: "awaiting_review", progress: 100, downloadName: "Quiz-FairyGUI.zip", sha256: "a".repeat(64), byteSize: 3, diagnostics: [] })).rejects.toMatchObject({ code: "invalid_response" });
  });

  it("rejects image evidence without stable source-node provenance", async () => {
    const payload = writerReview();
    delete (payload.image_reviews[0] as Partial<(typeof payload.image_reviews)[number]>).source_node_id;
    const client = new ProjectWorkflowClient({ serverOrigin: "https://fgui.test", pluginToken: "token", fetchImpl: vi.fn().mockResolvedValue(json(payload)) });
    await expect(client.reviewNewProject({ buildId: "4".repeat(32), generation: 1, status: "awaiting_review", stage: "awaiting_review", progress: 100, downloadName: "Quiz-FairyGUI.zip", sha256: "a".repeat(64), byteSize: 3, diagnostics: [] })).rejects.toMatchObject({ code: "invalid_response" });
  });

  it.each(["converting", "checking", "packaging", "regenerating"])("accepts %s without artifact metadata", async (status) => {
    const payload = { version: 1, build_id: "4".repeat(32), generation: 1, status, stage: status, progress: 25, download_name: null, sha256: null, byte_size: null, artifact_ready: false, diagnostics: [] };
    const client = new ProjectWorkflowClient({ serverOrigin: "https://fgui.test", pluginToken: "token", fetchImpl: vi.fn().mockResolvedValue(json(payload, 202)) });
    await expect(client.getNewProject("4".repeat(32), 1)).resolves.toMatchObject({ status });
  });

  it("accepts awaiting_review without artifact metadata", async () => {
    const status = "awaiting_review";
    const payload = { version: 1, build_id: "4".repeat(32), generation: 1, status, stage: status, progress: 100, download_name: null, sha256: null, byte_size: null, artifact_ready: false, diagnostics: [] };
    const client = new ProjectWorkflowClient({ serverOrigin: "https://fgui.test", pluginToken: "token", fetchImpl: vi.fn().mockResolvedValue(json(payload)) });
    await expect(client.getNewProject("4".repeat(32), 1)).resolves.toMatchObject({ status, artifactReady: false });
  });

  it("rejects approved without artifact metadata", async () => {
    const status = "approved";
    const payload = { version: 1, build_id: "4".repeat(32), generation: 1, status, stage: status, progress: 100, download_name: null, sha256: null, byte_size: null, artifact_ready: false, diagnostics: [] };
    const client = new ProjectWorkflowClient({ serverOrigin: "https://fgui.test", pluginToken: "token", fetchImpl: vi.fn().mockResolvedValue(json(payload)) });
    await expect(client.getNewProject("4".repeat(32), 1)).rejects.toMatchObject({ code: "invalid_response" });
  });

  it("invalidates the old generation and requires exact warning acknowledgement", async () => {
    const old = { buildId: "4".repeat(32), generation: 1, status: "adjusting", stage: "adjusting", progress: 100, downloadName: "Quiz-FairyGUI.zip", sha256: "a".repeat(64), byteSize: 3, diagnostics: [] } as const;
    const nextRaw = writerCandidate("awaiting_review", "6".repeat(32), 2);
    const started = { version: 1, build_id: "6".repeat(32), generation: 2, status: "regenerating", stage: "regenerating", progress: 5, download_name: null, sha256: null, byte_size: null, artifact_ready: false, diagnostics: [] };
    const fetchImpl = vi.fn().mockResolvedValueOnce(json(started, 202)).mockResolvedValueOnce(json(nextRaw)).mockResolvedValueOnce(json(writerReview("6".repeat(32), 2))).mockResolvedValueOnce(json(writerCandidate("approved", "6".repeat(32), 2)));
    const client = new ProjectWorkflowClient({ serverOrigin: "https://fgui.test", pluginToken: "token", fetchImpl });
    const next = await client.regenerateNewProject(old);
    expect(next).toMatchObject({ buildId: "6".repeat(32), generation: 2 });
    expect(fetchImpl.mock.calls.slice(0, 2).map(([url, init]) => [new URL(String(url)).pathname, init.method])).toEqual([
      [`/v1/new-fgui-projects/${old.buildId}/regenerate`, "POST"],
      [`/v1/new-fgui-projects/${"6".repeat(32)}`, "GET"],
    ]);
    const review = await client.reviewNewProject(next);
    await expect(client.approveNewProject(next, review, [])).rejects.toMatchObject({ code: "review_required" });
    await expect(client.approveNewProject(next, review, review.warningIds)).resolves.toMatchObject({ status: "approved" });
  });

  it("gates download on approval and verifies exact name, byte size and SHA-256", async () => {
    const approved = { buildId: "4".repeat(32), generation: 1, status: "approved", stage: "approved", progress: 100, downloadName: "Quiz-FairyGUI.zip", sha256: "4a70fe9aa6436e02c2dea340fbd1e352e4ef2d8ce6ca52ad25d4b95471fc8bf2", byteSize: 3, diagnostics: [] } as const;
    const response = new Response(new Blob(["zip"]), { headers: { "Content-Type": "application/zip", "Content-Disposition": "attachment; filename=Quiz-FairyGUI.zip" } });
    const client = new ProjectWorkflowClient({ serverOrigin: "https://fgui.test", pluginToken: "token", fetchImpl: vi.fn().mockResolvedValue(response) });
    await expect(client.downloadNewProject({ ...approved, status: "awaiting_review", stage: "awaiting_review" })).rejects.toMatchObject({ code: "review_required" });
    await expect(client.downloadNewProject(approved)).resolves.toMatchObject({ downloadName: "Quiz-FairyGUI.zip" });
  });

  it("verifies a Writer download when the Figma UI sandbox has no WebCrypto", async () => {
    const approved = { buildId: "4".repeat(32), generation: 1, status: "approved", stage: "approved", progress: 100, downloadName: "Quiz-FairyGUI.zip", sha256: "4a70fe9aa6436e02c2dea340fbd1e352e4ef2d8ce6ca52ad25d4b95471fc8bf2", byteSize: 3, diagnostics: [] } as const;
    const response = new Response(new Blob(["zip"]), { headers: { "Content-Type": "application/zip", "Content-Disposition": "attachment; filename=Quiz-FairyGUI.zip" } });
    const originalCrypto = globalThis.crypto;
    Object.defineProperty(globalThis, "crypto", { configurable: true, value: undefined });
    try {
      const client = new ProjectWorkflowClient({ serverOrigin: "https://fgui.test", pluginToken: "token", fetchImpl: vi.fn().mockResolvedValue(response) });
      await expect(client.downloadNewProject(approved)).resolves.toMatchObject({ downloadName: "Quiz-FairyGUI.zip" });
    } finally {
      Object.defineProperty(globalThis, "crypto", { configurable: true, value: originalCrypto });
    }
  });

  it("enforces one Writer deadline across a hung selection upload", async () => {
    const aborted = vi.fn();
    const fetchImpl = vi.fn((_url: string, init: RequestInit) => new Promise<Response>(() => init.signal?.addEventListener("abort", aborted, { once: true })));
    const client = new ProjectWorkflowClient({ serverOrigin: "https://fgui.test", pluginToken: "token", fetchImpl });
    await expect(client.createNewProjectCandidate(manifest, resources, "Quiz", { timeoutMs: 20 })).rejects.toMatchObject({ code: "timeout" });
    expect(aborted).toHaveBeenCalledOnce();
  });

  it("fetches generated and owned-selection previews with plugin authentication", async () => {
    const fetchImpl = vi.fn().mockImplementation(async () => new Response(new Blob(["image"]), { headers: { "Content-Type": "image/png" } }));
    const client = new ProjectWorkflowClient({ serverOrigin: "https://fgui.test", pluginToken: "token", fetchImpl });
    const buildId = "4".repeat(32);
    await client.newProjectPreview(buildId, `/v1/new-fgui-projects/${buildId}/previews/resources/asset`);
    await client.newProjectPreview(buildId, `/v1/figma/selections/${"a".repeat(32)}/previews/0`);
    await client.newProjectPreview(buildId, `/v1/figma/selections/${"a".repeat(32)}/resources/asset-1`);
    expect(fetchImpl.mock.calls.every(([, init]) => new Headers(init.headers).get("X-Figma-Plugin-Token") === "token")).toBe(true);
    await expect(client.newProjectPreview(buildId, "/v1/figma/selections/other/previews/0")).rejects.toMatchObject({ code: "invalid_response" });
    await expect(client.newProjectPreview(buildId, `/v1/figma/selections/${"a".repeat(32)}/resources/../secret`)).rejects.toMatchObject({ code: "invalid_response" });
  });

  it("accepts a validated SVG image resource preview", async () => {
    const response = new Response(
      new Blob(['<svg xmlns="http://www.w3.org/2000/svg"/>'], { type: "image/svg+xml" }),
      { headers: { "Content-Type": "image/svg+xml" } },
    );
    const client = new ProjectWorkflowClient({ serverOrigin: "https://fgui.test", pluginToken: "token", fetchImpl: vi.fn().mockResolvedValue(response) });
    const buildId = "4".repeat(32);

    await expect(client.newProjectPreview(buildId, `/v1/new-fgui-projects/${buildId}/previews/resources/vector`)).resolves.toHaveProperty("type", "image/svg+xml");
  });

  it.each([
    [{ ...writerReview(), extra: true }, "review"],
    [(({ generation: _generation, ...value }) => ({ ...value, extra: true }))(writerCandidate()), "candidate"],
  ])("rejects extra Writer response fields %#", async (payload, kind) => {
    const client = new ProjectWorkflowClient({ serverOrigin: "https://fgui.test", pluginToken: "token", fetchImpl: vi.fn().mockResolvedValue(json(payload)) });
    const candidate = { buildId: "4".repeat(32), generation: 1, status: "awaiting_review", stage: "awaiting_review", progress: 100, downloadName: "Quiz-FairyGUI.zip", sha256: "a".repeat(64), byteSize: 3, diagnostics: [] } as const;
    const call = kind === "review" ? client.reviewNewProject(candidate) : client.getNewProject(candidate.buildId, 1);
    await expect(call).rejects.toMatchObject({ code: "invalid_response" });
  });
  it("runs create mode without uploading a ZIP and authenticates absolute requests", async () => {
    const fetchImpl = successfulFetch("create");
    const stages = vi.fn();
    const client = new ProjectWorkflowClient({ serverOrigin: "https://fgui.test/some/path", pluginToken: "plugin-token", fetchImpl, wait: async () => {} });

    const result = await client.runCreate(manifest, resources, { projectName: "Quiz", templateId: "fgui-2024-web" }, stages);

    const calls = fetchImpl.mock.calls as unknown as Array<[string, RequestInit]>;
    expect(calls.map(([url]) => new URL(url).pathname)).not.toContain("/v1/projects/uploads");
    expect(calls.every(([url, init]) => url.startsWith("https://fgui.test/") && new Headers(init.headers).get("X-Figma-Plugin-Token") === "plugin-token")).toBe(true);
    expect(JSON.parse(String(calls.find(([url]) => new URL(url).pathname === "/v1/projects/from-template")?.[1].body))).toEqual({ version: 1, template_id: "fgui-2024-web", project_name: "Quiz" });
    expect(JSON.parse(String(calls.find(([url]) => new URL(url).pathname.endsWith("/jobs"))?.[1].body))).toEqual({ version: 1, selection_id: "a".repeat(32), project_id: projectId, package_name: "Quiz" });
    expect(JSON.parse(String(calls.find(([url, init]) => new URL(url).pathname.endsWith("/package") && init.method === "POST")?.[1].body))).toEqual({ version: 1, mode: "create", project_name: "Quiz" });
    expect(new Headers(calls.find(([url]) => new URL(url).pathname.endsWith("/manifest"))?.[1].headers).get("Content-Type")).toBe("application/json");
    expect(new Headers(calls.find(([url]) => new URL(url).pathname.includes("/resources/"))?.[1].headers).get("Content-Type")).toBe("image/png");
    expect(result.downloadName).toBe("Quiz-Figma新建-20260730-1530.zip");
    expect(result.blob).toBeInstanceOf(Blob);
    expect(stages).toHaveBeenCalledWith(expect.objectContaining({ stage: "ready", progress: 100 }));
  });

  it("runs update mode with the original ZIP in FormData", async () => {
    const fetchImpl = successfulFetch("update");
    const client = new ProjectWorkflowClient({ serverOrigin: "https://fgui.test", pluginToken: "plugin-token", fetchImpl, wait: async () => {} });
    const archive = new File(["project"], "My Game.zip", { type: "application/zip" });

    const result = await client.runUpdate(manifest, resources, archive);

    const calls = fetchImpl.mock.calls as unknown as Array<[string, RequestInit]>;
    const upload = calls.find(([url]) => new URL(url).pathname === "/v1/projects/uploads")!;
    expect(upload[1].body).toBeInstanceOf(FormData);
    expect((upload[1].body as FormData).get("project")).toBe(archive);
    expect(new Headers(upload[1].headers).has("Content-Type")).toBe(false);
    expect(calls.map(([url]) => new URL(url).pathname)).not.toContain("/v1/projects/from-template");
    expect(JSON.parse(String(calls.find(([url, init]) => new URL(url).pathname.endsWith("/package") && init.method === "POST")?.[1].body)).project_name).toBe("My-Game");
    expect(result.downloadName).toBe("GameUI-Figma更新-20260730-1530.zip");
  });

  it("targets the sole feature package instead of infrastructure packages", async () => {
    const fetchImpl = successfulFetch("update");
    fetchImpl.mockImplementationOnce(async () => json({
      version: 1,
      project_id: projectId,
      display_name: "figma2fgui.zip",
      packages: [
        { name: "Base0", resource_count: 199 },
        { name: "Common", resource_count: 511 },
        { name: "Icons", resource_count: 71 },
        { name: "MyVillage", resource_count: 12 },
      ],
    }, 201));
    const client = new ProjectWorkflowClient({ serverOrigin: "https://fgui.test", pluginToken: "plugin-token", fetchImpl, wait: async () => {} });

    await client.runUpdate(manifest, resources, new File(["project"], "figma2fgui.zip"));

    const calls = fetchImpl.mock.calls as unknown as Array<[string, RequestInit]>;
    expect(JSON.parse(String(calls.find(([url]) => new URL(url).pathname.endsWith("/jobs"))?.[1].body)).package_name).toBe("MyVillage");
  });

  it("loads and validates configured template options", async () => {
    const fetchImpl = vi.fn().mockResolvedValue(json({ version: 1, options: [{ template_id: "fgui-2024-web", fairygui_version: "2024.2", target_platform: "web", display_name: "Web" }] }));
    const client = new ProjectWorkflowClient({ serverOrigin: "https://fgui.test", pluginToken: "token", fetchImpl });
    await expect(client.options()).resolves.toEqual([{ templateId: "fgui-2024-web", fairyguiVersion: "2024.2", targetPlatform: "web", displayName: "Web" }]);
    expect(fetchImpl).toHaveBeenCalledWith("https://fgui.test/v1/figma/project-options", expect.objectContaining({ method: "GET" }));
  });

  it("calls the native fetch with the global receiver", async () => {
    const nativeFetch = vi.fn(function (this: typeof globalThis) {
      if (this !== globalThis) throw new TypeError("Illegal invocation");
      return Promise.resolve(json({ version: 1, options: [] }));
    });
    vi.stubGlobal("fetch", nativeFetch);
    const client = new ProjectWorkflowClient({ serverOrigin: "https://fgui.test", pluginToken: "token" });

    await expect(client.options()).resolves.toEqual([]);
    expect(nativeFetch).toHaveBeenCalledOnce();
  });

  it("times out package polling and can abort an injected wait", async () => {
    const fetchImpl = vi.fn(async () => json(packageView("packaging")));
    const client = new ProjectWorkflowClient({ serverOrigin: "https://fgui.test", pluginToken: "token", fetchImpl, wait: async () => {} });
    await expect(client.waitForPackage(jobId, { timeoutMs: 0 })).rejects.toMatchObject({ code: "timeout" });

    const controller = new AbortController();
    const waiting = new ProjectWorkflowClient({
      serverOrigin: "https://fgui.test",
      pluginToken: "token",
      fetchImpl,
      wait: (_ms, signal) => new Promise((_resolve, reject) => signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), { once: true })),
    }).waitForPackage(jobId, { signal: controller.signal });
    await vi.waitFor(() => expect(fetchImpl).toHaveBeenCalled());
    controller.abort();
    await expect(waiting).rejects.toMatchObject({ code: "aborted" });
  });

  it("retries safe reads once but never repeats project or ZIP creation", async () => {
    const optionsFetch = vi.fn().mockRejectedValueOnce(new TypeError("offline")).mockResolvedValueOnce(json({ version: 1, options: [] }));
    await new ProjectWorkflowClient({ serverOrigin: "https://fgui.test", pluginToken: "token", fetchImpl: optionsFetch }).options();
    expect(optionsFetch).toHaveBeenCalledTimes(2);

    const createFetch = vi.fn().mockRejectedValue(new TypeError("offline"));
    const client = new ProjectWorkflowClient({ serverOrigin: "https://fgui.test", pluginToken: "token", fetchImpl: createFetch });
    await expect(client.createProject({ projectName: "Quiz", templateId: "fgui-2024-web" })).rejects.toMatchObject({ code: "network" });
    await expect(client.uploadProject(new File(["zip"], "Quiz.zip", { type: "application/zip" }))).rejects.toMatchObject({ code: "network" });
    expect(createFetch).toHaveBeenCalledTimes(2);
  });

  it.each([
    [401, "anything", "unauthorized"],
    [400, "invalid_archive", "invalid_zip"],
    [404, "template_not_found", "unknown_template"],
    [409, "package_request_conflict", "conversion_conflict"],
  ])("maps HTTP %i/%s to a safe distinct error", async (status, serverCode, expectedCode) => {
    const fetchImpl = vi.fn().mockResolvedValue(json({ detail: { code: serverCode, message: "C:\\secret\\raw:node plugin-token" } }, status));
    const client = new ProjectWorkflowClient({ serverOrigin: "https://fgui.test", pluginToken: "plugin-token", fetchImpl });
    const call = status === 404 ? client.createProject({ projectName: "Quiz", templateId: "missing" }) : status === 400 ? client.uploadProject(new File(["bad"], "bad.zip", { type: "application/zip" })) : client.buildPackage(jobId, "update", "Quiz");
    const error = await call.catch((reason: unknown) => reason) as WorkflowError;
    expect(error.code).toBe(expectedCode);
    expect(error.message).not.toMatch(/secret|raw:node|plugin-token/);
  });

  it("rejects malformed JSON, failed conversion and failed packages distinctly", async () => {
    const malformed = new ProjectWorkflowClient({ serverOrigin: "https://fgui.test", pluginToken: "token", fetchImpl: vi.fn().mockResolvedValue(json({ version: 1, options: "wrong" })) });
    await expect(malformed.options()).rejects.toMatchObject({ code: "invalid_response" });

    const conversion = new ProjectWorkflowClient({ serverOrigin: "https://fgui.test", pluginToken: "token", fetchImpl: vi.fn().mockResolvedValue(json({ ...job, status: "conversion_failed" })) });
    await expect(conversion.createJob(selection.selection_id, { projectId, displayName: "Quiz", packages: [{ name: "Quiz", resourceCount: 1 }] }, "Quiz")).rejects.toMatchObject({ code: "conversion_failed" });

    const packaging = new ProjectWorkflowClient({ serverOrigin: "https://fgui.test", pluginToken: "token", fetchImpl: vi.fn().mockResolvedValue(json(packageView("failed"))) });
    await expect(packaging.waitForPackage(jobId)).rejects.toMatchObject({ code: "package_failed" });
  });

  it("uses a fallback filename for hostile Content-Disposition and forwards abort to download", async () => {
    const fetchImpl = vi.fn(async (_url: string, init: RequestInit) => {
      if (init.signal?.aborted) throw new DOMException("Aborted", "AbortError");
      return new Response(new Blob(["zip"]), { headers: { "Content-Type": "application/zip", "Content-Disposition": "attachment; filename=../../secret.exe" } });
    });
    const client = new ProjectWorkflowClient({ serverOrigin: "https://fgui.test", pluginToken: "token", fetchImpl });
    await expect(client.downloadPackage(jobId)).resolves.toMatchObject({ downloadName: "FairyGUI-project.zip" });
    const controller = new AbortController();
    const blobStarted = vi.fn();
    const bodyResponse = new Response(null, { headers: { "Content-Type": "application/zip" } });
    Object.defineProperty(bodyResponse, "blob", { value: () => new Promise<Blob>((_resolve, reject) => {
      blobStarted();
      controller.signal.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), { once: true });
    }) });
    const downloading = new ProjectWorkflowClient({ serverOrigin: "https://fgui.test", pluginToken: "token", fetchImpl: vi.fn().mockResolvedValue(bodyResponse) }).downloadPackage(jobId, controller.signal);
    await vi.waitFor(() => expect(blobStarted).toHaveBeenCalled());
    controller.abort();
    await expect(downloading).rejects.toMatchObject({ code: "aborted" });
  });

  it("forwards abort through project upload and package polling requests", async () => {
    const controller = new AbortController();
    const fetchImpl = vi.fn((_url: string, init: RequestInit) => new Promise<Response>((_resolve, reject) => {
      expect(init.signal).toBe(controller.signal);
      init.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), { once: true });
    }));
    const client = new ProjectWorkflowClient({ serverOrigin: "https://fgui.test", pluginToken: "token", fetchImpl });
    const uploading = client.uploadProject(new File(["zip"], "Quiz.zip", { type: "application/zip" }), controller.signal);
    await vi.waitFor(() => expect(fetchImpl).toHaveBeenCalled());
    controller.abort();
    await expect(uploading).rejects.toMatchObject({ code: "aborted" });
    const pollingController = new AbortController();
    let pollingSignal: AbortSignal | undefined;
    const pollFetch = vi.fn((_url: string, init: RequestInit) => new Promise<Response>((_resolve, reject) => {
      pollingSignal = init.signal ?? undefined;
      init.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")), { once: true });
    }));
    const polling = new ProjectWorkflowClient({ serverOrigin: "https://fgui.test", pluginToken: "token", fetchImpl: pollFetch }).waitForPackage(jobId, { signal: pollingController.signal });
    await vi.waitFor(() => expect(pollFetch).toHaveBeenCalled());
    pollingController.abort();
    await expect(polling).rejects.toMatchObject({ code: "aborted" });
    expect(pollingSignal?.aborted).toBe(true);
  });

  it("enforces the overall deadline across a hung status fetch and aborts it", async () => {
    vi.useFakeTimers();
    try {
      const aborted = vi.fn();
      const fetchImpl = vi.fn((_url: string, init: RequestInit) => new Promise<Response>(() => {
        init.signal?.addEventListener("abort", aborted, { once: true });
      }));
      const pending = new ProjectWorkflowClient({ serverOrigin: "https://fgui.test", pluginToken: "token", fetchImpl }).waitForPackage(jobId, { timeoutMs: 50 });
      const assertion = expect(pending).rejects.toMatchObject({ code: "timeout" });
      await vi.advanceTimersByTimeAsync(50);
      await assertion;
      expect(aborted).toHaveBeenCalledOnce();
      expect(vi.getTimerCount()).toBe(0);
    } finally { vi.useRealTimers(); }
  });

  it("enforces the overall deadline across a hung injected wait", async () => {
    const waitAborted = vi.fn();
    const client = new ProjectWorkflowClient({
      serverOrigin: "https://fgui.test",
      pluginToken: "token",
      fetchImpl: vi.fn(async () => json(packageView("packaging"))),
      wait: (_ms, signal) => new Promise<void>(() => signal?.addEventListener("abort", waitAborted, { once: true })),
    });
    await expect(client.waitForPackage(jobId, { timeoutMs: 20 })).rejects.toMatchObject({ code: "timeout" });
    expect(waitAborted).toHaveBeenCalledOnce();
  });

  it("cleans the linked outer abort listener when polling completes", async () => {
    const controller = new AbortController();
    const add = vi.spyOn(controller.signal, "addEventListener");
    const remove = vi.spyOn(controller.signal, "removeEventListener");
    const client = new ProjectWorkflowClient({ serverOrigin: "https://fgui.test", pluginToken: "token", fetchImpl: vi.fn(async () => json(packageView("ready", "Quiz.zip"))) });
    await client.waitForPackage(jobId, { signal: controller.signal });
    const abortListener = add.mock.calls.find(([type]) => type === "abort")?.[1];
    expect(abortListener).toBeDefined();
    expect(remove).toHaveBeenCalledWith("abort", abortListener);
  });

  it("declines screenshot consent once and resumes polling without requesting an export", async () => {
    const waiting = { ...packageView("awaiting_screenshot_consent"), screenshot_reason: "Visual hierarchy is ambiguous." };
    const fetchImpl = vi.fn()
      .mockResolvedValueOnce(json(waiting))
      .mockResolvedValueOnce(json(packageView("packaging"), 202))
      .mockResolvedValueOnce(json(packageView("ready", "Quiz.zip")));
    const onScreenshotConsent = vi.fn().mockResolvedValue(false);
    const requestScreenshot = vi.fn();
    const client = new ProjectWorkflowClient({ serverOrigin: "https://fgui.test", pluginToken: "token", fetchImpl, wait: async () => {} });

    await expect(client.waitForPackage(jobId, { onScreenshotConsent, requestScreenshot })).resolves.toMatchObject({ status: "ready" });

    expect(onScreenshotConsent).toHaveBeenCalledOnce();
    expect(onScreenshotConsent).toHaveBeenCalledWith(expect.objectContaining({ jobId, reason: "Visual hierarchy is ambiguous." }));
    expect(fetchImpl).toHaveBeenCalledWith(
      expect.stringContaining(`/v1/jobs/${jobId}/semantic-screenshot-consent`),
      expect.objectContaining({ method: "POST", body: JSON.stringify({ version: 1, approved: false }) }),
    );
    expect(requestScreenshot).not.toHaveBeenCalled();
  });

  it("records approval before exporting and uploading only PNG bytes", async () => {
    const calls: string[] = [];
    const screenshot = { mimeType: "image/png" as const, bytes: new Uint8Array([137, 80, 78, 71]) };
    const fetchImpl = vi.fn(async (url: string, init: RequestInit) => {
      const path = new URL(url).pathname;
      if (path.endsWith("/package") && init.method === "GET" && !calls.includes("polled")) {
        calls.push("polled");
        return json({ ...packageView("awaiting_screenshot_consent"), screenshot_reason: "Need pixels." });
      }
      if (path.endsWith("/semantic-screenshot-consent")) {
        calls.push(`consent:${String(init.body)}`);
        return json({ ...packageView("awaiting_screenshot_consent"), screenshot_reason: "Need pixels." }, 202);
      }
      if (path.endsWith("/semantic-screenshot")) {
        calls.push("upload");
        expect(new Headers(init.headers).get("Content-Type")).toBe("image/png");
        expect(init.body).toBe(screenshot.bytes);
        return json(packageView("packaging"), 202);
      }
      if (path.endsWith("/package")) return json(packageView("ready", "Quiz.zip"));
      throw new Error(`unexpected ${init.method} ${path}`);
    });
    const requestScreenshot = vi.fn(async () => { calls.push("export"); return screenshot; });
    const client = new ProjectWorkflowClient({ serverOrigin: "https://fgui.test", pluginToken: "token", fetchImpl, wait: async () => {} });

    await client.waitForPackage(jobId, { onScreenshotConsent: async () => true, requestScreenshot });

    expect(calls).toEqual([
      "polled",
      `consent:${JSON.stringify({ version: 1, approved: true })}`,
      "export",
      "upload",
    ]);
    expect(requestScreenshot).toHaveBeenCalledWith(jobId, expect.any(AbortSignal));
  });

  it("falls back to deterministic packaging when screenshot export fails", async () => {
    const consentBodies: string[] = [];
    let polled = false;
    const fetchImpl = vi.fn(async (url: string, init: RequestInit) => {
      const path = new URL(url).pathname;
      if (path.endsWith("/package") && !polled) {
        polled = true;
        return json({ ...packageView("awaiting_screenshot_consent"), screenshot_reason: "Need pixels." });
      }
      if (path.endsWith("/semantic-screenshot-consent")) {
        consentBodies.push(String(init.body));
        return json(
          consentBodies.length === 1
            ? { ...packageView("awaiting_screenshot_consent"), screenshot_reason: "Need pixels." }
            : packageView("packaging"),
          202,
        );
      }
      if (path.endsWith("/package")) return json(packageView("ready", "Quiz.zip"));
      throw new Error(`unexpected ${init.method} ${path}`);
    });
    const requestScreenshot = vi.fn().mockRejectedValue(new WorkflowError("validation"));
    const client = new ProjectWorkflowClient({ serverOrigin: "https://fgui.test", pluginToken: "token", fetchImpl, wait: async () => {} });

    await expect(client.waitForPackage(jobId, { onScreenshotConsent: async () => true, requestScreenshot })).resolves.toMatchObject({ status: "ready" });

    expect(consentBodies).toEqual([
      JSON.stringify({ version: 1, approved: true }),
      JSON.stringify({ version: 1, approved: false }),
    ]);
    expect(fetchImpl.mock.calls.map(([url]) => String(url))).not.toEqual(expect.arrayContaining([expect.stringMatching(/semantic-screenshot$/)]));
  });

  it("falls back to deterministic packaging when screenshot upload fails", async () => {
    const consentBodies: string[] = [];
    let polled = false;
    const fetchImpl = vi.fn(async (url: string, init: RequestInit) => {
      const path = new URL(url).pathname;
      if (path.endsWith("/package") && !polled) {
        polled = true;
        return json({ ...packageView("awaiting_screenshot_consent"), screenshot_reason: "Need pixels." });
      }
      if (path.endsWith("/semantic-screenshot-consent")) {
        consentBodies.push(String(init.body));
        return json(
          consentBodies.length === 1
            ? { ...packageView("awaiting_screenshot_consent"), screenshot_reason: "Need pixels." }
            : packageView("packaging"),
          202,
        );
      }
      if (path.endsWith("/semantic-screenshot")) throw new TypeError("network down");
      if (path.endsWith("/package")) return json(packageView("ready", "Quiz.zip"));
      throw new Error(`unexpected ${init.method} ${path}`);
    });
    const requestScreenshot = vi.fn().mockResolvedValue({ mimeType: "image/png" as const, bytes: new Uint8Array([137, 80, 78, 71]) });
    const client = new ProjectWorkflowClient({ serverOrigin: "https://fgui.test", pluginToken: "token", fetchImpl, wait: async () => {} });

    await expect(client.waitForPackage(jobId, { onScreenshotConsent: async () => true, requestScreenshot })).resolves.toMatchObject({ status: "ready" });

    expect(consentBodies).toEqual([
      JSON.stringify({ version: 1, approved: true }),
      JSON.stringify({ version: 1, approved: false }),
    ]);
  });

  it("rejects a waiting screenshot stage without its bounded reason", async () => {
    const client = new ProjectWorkflowClient({ serverOrigin: "https://fgui.test", pluginToken: "token", fetchImpl: vi.fn().mockResolvedValue(json(packageView("awaiting_screenshot_consent"))) });
    await expect(client.waitForPackage(jobId, { onScreenshotConsent: async () => false, requestScreenshot: vi.fn() })).rejects.toMatchObject({ code: "invalid_response" });
  });

  it("aborts a pending consent choice without posting a decision or exporting", async () => {
    const controller = new AbortController();
    let consentSignal: AbortSignal | undefined;
    const fetchImpl = vi.fn().mockResolvedValue(json({
      ...packageView("awaiting_screenshot_consent"),
      screenshot_reason: "Need pixels.",
    }));
    const onScreenshotConsent = vi.fn(({ signal }: { signal: AbortSignal }) => {
      consentSignal = signal;
      return new Promise<boolean>(() => {});
    });
    const requestScreenshot = vi.fn();
    const pending = new ProjectWorkflowClient({ serverOrigin: "https://fgui.test", pluginToken: "token", fetchImpl })
      .waitForPackage(jobId, { signal: controller.signal, onScreenshotConsent, requestScreenshot });
    await vi.waitFor(() => expect(onScreenshotConsent).toHaveBeenCalledOnce());

    controller.abort();

    await expect(pending).rejects.toMatchObject({ code: "aborted" });
    expect(consentSignal?.aborted).toBe(true);
    expect(requestScreenshot).not.toHaveBeenCalled();
    expect(fetchImpl.mock.calls.map(([url]) => String(url))).not.toEqual(expect.arrayContaining([expect.stringContaining("semantic-screenshot-consent")]));
  });

  it("does not post a fallback decision when the user aborts an approved export", async () => {
    const controller = new AbortController();
    const consentBodies: string[] = [];
    const fetchImpl = vi.fn(async (url: string, init: RequestInit) => {
      const path = new URL(url).pathname;
      if (path.endsWith("/semantic-screenshot-consent")) {
        consentBodies.push(String(init.body));
        return json({ ...packageView("awaiting_screenshot_consent"), screenshot_reason: "Need pixels." }, 202);
      }
      if (path.endsWith("/package")) return json({ ...packageView("awaiting_screenshot_consent"), screenshot_reason: "Need pixels." });
      throw new Error(`unexpected ${init.method} ${path}`);
    });
    const requestScreenshot = vi.fn((_jobId: string, signal: AbortSignal) => new Promise<never>(() => {
      signal.addEventListener("abort", () => {}, { once: true });
    }));
    const pending = new ProjectWorkflowClient({ serverOrigin: "https://fgui.test", pluginToken: "token", fetchImpl })
      .waitForPackage(jobId, { signal: controller.signal, onScreenshotConsent: async () => true, requestScreenshot });
    await vi.waitFor(() => expect(requestScreenshot).toHaveBeenCalledOnce());

    controller.abort();

    await expect(pending).rejects.toMatchObject({ code: "aborted" });
    expect(consentBodies).toEqual([JSON.stringify({ version: 1, approved: true })]);
  });

  it("times out a pending approved bridge export and never uploads bytes", async () => {
    const consentBodies: string[] = [];
    const bridgeAborted = vi.fn();
    const fetchImpl = vi.fn(async (url: string, init: RequestInit) => {
      const path = new URL(url).pathname;
      if (path.endsWith("/semantic-screenshot-consent")) {
        consentBodies.push(String(init.body));
        return json({ ...packageView("awaiting_screenshot_consent"), screenshot_reason: "Need pixels." }, 202);
      }
      if (path.endsWith("/package")) return json({ ...packageView("awaiting_screenshot_consent"), screenshot_reason: "Need pixels." });
      throw new Error(`unexpected screenshot upload: ${path}`);
    });
    const requestScreenshot = vi.fn((_jobId: string, signal: AbortSignal) => new Promise<never>(() => {
      signal.addEventListener("abort", bridgeAborted, { once: true });
    }));
    const pending = new ProjectWorkflowClient({ serverOrigin: "https://fgui.test", pluginToken: "token", fetchImpl })
      .waitForPackage(jobId, { timeoutMs: 20, onScreenshotConsent: async () => true, requestScreenshot });

    await expect(pending).rejects.toMatchObject({ code: "timeout" });
    expect(bridgeAborted).toHaveBeenCalledOnce();
    expect(consentBodies).toEqual([
      JSON.stringify({ version: 1, approved: true }),
      JSON.stringify({ version: 1, approved: false }),
    ]);
    expect(fetchImpl.mock.calls.map(([url]) => String(url))).not.toEqual(expect.arrayContaining([expect.stringMatching(/semantic-screenshot$/)]));
  });

  it.each([
    [null],
    [[]],
    [{ version: 2, options: [] }],
    [{ version: 1, options: [{ template_id: 4, fairygui_version: "2024", target_platform: "web", display_name: "Web" }] }],
  ])("maps malformed options payload %j to invalid_response", async (payload) => {
    const client = new ProjectWorkflowClient({ serverOrigin: "https://fgui.test", pluginToken: "token", fetchImpl: vi.fn().mockResolvedValue(json(payload)) });
    await expect(client.options()).rejects.toMatchObject({ code: "invalid_response" });
  });

  it.each([
    [{ ...project(), project_id: "short" }],
    [{ ...project(), packages: [{ name: "Quiz", resource_count: -1 }] }],
    [{ ...job, job_id: "bad" }],
    [{ ...job, project_id: "4".repeat(32) }],
    [{ ...job, status: "surprise" }],
    [{ ...packageView("ready", "Quiz.zip"), progress: 101 }],
    [{ ...packageView("ready", "Quiz.zip"), sha256: "bad" }],
    [{ ...packageView("ready", null) }],
    [{ ...packageView("ready", "CON.zip") }],
    [{ ...packageView("packaging"), diagnostics: [{ code: "x", severity: "SECRET", message: "bad" }] }],
  ])("rejects drifted project/job/package contract %#", async (payload) => {
    const fetchImpl = vi.fn().mockResolvedValue(json(payload));
    const client = new ProjectWorkflowClient({ serverOrigin: "https://fgui.test", pluginToken: "token", fetchImpl });
    const data = payload as Record<string, unknown>;
    const call = "packages" in data ? client.createProject({ templateId: "x", projectName: "Quiz" }) : "progress" in data ? client.waitForPackage(jobId) : client.createJob(selection.selection_id, { projectId, displayName: "Quiz", packages: [{ name: "Quiz", resourceCount: 1 }] }, "Quiz");
    await expect(call).rejects.toMatchObject({ code: "invalid_response" });
  });

  it.each([
    "CON.zip",
    "com1.zip",
    "CON.txt.zip",
    "LPT9.zip",
    "Quiz .zip",
    "Quiz..zip",
    "Quiz\u202Ecod.zip",
    "Quiz\u0001.zip",
    `${"x".repeat(181)}.zip`,
    "Quiz.txt",
  ])("replaces unsafe Windows download name %s", async (filename) => {
    const response = new Response(new Blob(["zip"]), { headers: { "Content-Type": "application/zip", "Content-Disposition": `attachment; filename*=UTF-8''${encodeURIComponent(filename)}` } });
    const client = new ProjectWorkflowClient({ serverOrigin: "https://fgui.test", pluginToken: "token", fetchImpl: vi.fn().mockResolvedValue(response) });
    await expect(client.downloadPackage(jobId)).resolves.toMatchObject({ downloadName: "FairyGUI-project.zip" });
  });
});

it.each(["hifi_shared_scope_violation", "hifi_shared_instance_conflict", "hifi_nested_geometry_unverified", "hifi_instance_override_conflict", "hifi_type_conversion_not_authorized", "psd_stroke_only_raster_unsupported"])("preserves in-place blocker %s", async (code) => {
  const client = new ProjectWorkflowClient({ serverOrigin: "https://fgui.test", pluginToken: "token", fetchImpl: vi.fn().mockResolvedValue(json({ detail: { code, message: "blocked" } }, 409)) });
  await expect(client.buildHifiReplacement("a".repeat(32), 1)).rejects.toMatchObject({ code });
});
