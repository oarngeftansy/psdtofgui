import { readFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { test, expect } from "playwright/test";

const root = resolve(import.meta.dirname, "../../..");
const placeholderOrigin = "https://fgui.corp.example";
const placeholderToken = "not-a-secret-public-test-placeholder-000000";

test("completes the HIFI replacement workflow in the real bundled plugin UI", async ({ page }, testInfo) => {
  const apiFailures: string[] = [];
  page.on("response", (response) => {
    if (response.url().includes("/v1/") && response.status() >= 400) apiFailures.push(`${response.status()} ${response.url()}`);
  });
  page.on("requestfailed", (request) => apiFailures.push(`${request.failure()?.errorText ?? "request failed"} ${request.url()}`));
  const manifest = JSON.parse(await readFile(join(root, "tests", "fixtures", "hifi_replacement", "hifi-selection.json"), "utf8"));
  const hifiMaterial = [...await readFile(join(root, "tests", "fixtures", "hifi_replacement", "selection", "resources", "hifi-board"))];
  const pluginToken = process.env.FGUI_E2E_PLUGIN_TOKEN;
  if (!pluginToken) throw new Error("FGUI_E2E_PLUGIN_TOKEN is missing");
  const bundled = (await readFile(join(root, "apps", "figma-plugin", "dist", "ui.html"), "utf8"))
    .replaceAll(placeholderOrigin, "http://127.0.0.1:8766")
    .replaceAll(placeholderToken, pluginToken);

  const installFigmaBridge = ({ selectionManifest, resourceBytes }: { selectionManifest: typeof manifest; resourceBytes: number[] }) => {
    window.addEventListener("message", (event) => {
      const message = event.data?.pluginMessage;
      if (!message) return;
      if (message.type === "selection-preflight" && !message.preflight) {
        window.setTimeout(() => window.postMessage({ pluginMessage: { type: "selection-preflight", preflight: {
          sendable: true,
          nodeCount: 7,
          assetCount: 1,
          estimatedBytes: resourceBytes.length,
          warnings: [],
          manifest: selectionManifest,
        } } }, "*"), 0);
      }
      if (message.type === "selection-export" && !message.manifest) {
        window.setTimeout(() => window.postMessage({ pluginMessage: {
          type: "selection-export",
          attempt: message.attempt,
          manifest: selectionManifest,
          resources: [{ key: "hifi-board", mime_type: "image/png", bytes: new Uint8Array(resourceBytes) }],
        } }, "*"), 0);
      }
    });
  };
  await page.setViewportSize({ width: 640, height: 800 });
  await page.route("**/__hifi-plugin-test", (route) => route.fulfill({ status: 200, contentType: "text/html", body: bundled }));
  await page.goto("http://127.0.0.1:8766/__hifi-plugin-test", { waitUntil: "domcontentloaded" });
  await page.evaluate(installFigmaBridge, { selectionManifest: manifest, resourceBytes: hifiMaterial });

  await page.getByRole("tab", { name: "HIFI 替换" }).click();
  await expect(page.getByText("速记板 / HIFI v4")).toBeVisible();
  await page.getByLabel("旧 FairyGUI 工程 ZIP").setInputFiles(join(root, "tests", "fixtures", "hifi_replacement", "OldVillage.zip"));
  await page.waitForTimeout(300);
  const uploadError = page.getByRole("alert");
  if (await uploadError.count()) throw new Error(`${await uploadError.innerText()} | ${apiFailures.join(" | ")}`);
  await page.getByRole("button", { name: /Panel_MyVillage_Sketchboard/ }).click();
  await expect(page.getByText("MyVillage / Panel")).toBeVisible();
  await expect(page.getByText(/新增图片/)).toHaveCount(0);
  await page.getByRole("button", { name: "开始盘点与映射" }).click();

  await expect(page.getByText("组件对齐工作台")).toBeVisible();
  await expect(page.getByRole("region", { name: "结构视图" })).toBeVisible();
  await expect(page.locator(".hifi-canvas-object.is-active")).toHaveCount(2);

  for (let step = 0; step < 6; step += 1) {
    await expect(page.getByRole("button", { name: "取消当前操作" })).toHaveCount(0);
    if (await page.getByText("0 项待确认", { exact: true }).count()) break;
    const before = Number.parseInt(await page.locator(".hifi-mapping-heading > strong").innerText(), 10);
    const card = page.locator(".hifi-current-card");
    const text = await card.innerText();
    if (await card.getByRole("button", { name: "应用对应" }).count()) {
      await card.getByRole("button", { name: "应用对应" }).click();
    } else if (text.includes("ProgressBubble") && await card.getByRole("button", { name: "允许新增视觉节点" }).count()) {
      await card.getByRole("button", { name: "允许新增视觉节点" }).click();
    } else {
      await card.getByRole("button", { name: /列为例外/ }).click();
    }
    await expect.poll(async () => Number.parseInt(await page.locator(".hifi-mapping-heading > strong").innerText(), 10)).toBeLessThan(before);
  }
  await expect(page.getByText("0 项待确认", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "生成候选工程" }).click();

  await expect(page.getByRole("heading", { name: "候选差异审核" })).toBeVisible();
  await expect(page.getByText("对象差异")).toBeVisible();
  await expect(page.getByText(/原组件身份、层级和行为引用未变化/)).toBeVisible();
  await testInfo.attach("hifi-replacement-review", { body: await page.screenshot(), contentType: "image/png" });
  const candidateDownload = page.waitForEvent("download");
  await page.getByRole("button", { name: "下载候选 ZIP" }).click();
  const candidate = await candidateDownload;

  for (const label of ["布局与图层顺序正确", "图片与共享组件引用正常", "Controller、Gear、Transition 正常"]) {
    await page.getByRole("checkbox", { name: label }).check();
  }
  const deliveryDownload = page.waitForEvent("download");
  await page.getByRole("button", { name: "确认并交付 ZIP" }).click();
  const delivered = await deliveryDownload;
  await expect(page.getByText("已交付 HIFI 替换工程")).toBeVisible();

  const candidateBytes = await readFile(await candidate.path());
  const deliveredBytes = await readFile(await delivered.path());
  expect(deliveredBytes.equals(candidateBytes)).toBe(true);
});
