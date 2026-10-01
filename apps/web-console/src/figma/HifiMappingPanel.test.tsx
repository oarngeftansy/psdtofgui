import { useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { HifiMappingDraft } from "../../../figma-plugin/src/project-client";
import { HifiMappingPanel } from "./HifiMappingPanel";

const mapping: HifiMappingDraft = { mappingRevision: 2, unresolvedCount: 2, items: [
  { itemId: "old:done", oldObjectId: "done", oldName: "Done", figmaNodeId: "figma-done", figmaName: "Done", status: "matched", score: 1, action: "accept", candidates: ["figma-done"], oldBounds: [.02, .02, .06, .04], figmaBounds: [.02, .02, .06, .04] },
  { itemId: "old:title", oldObjectId: "title", oldName: "Title", figmaNodeId: "figma-title", figmaName: "Title", status: "uncertain", score: .7, candidates: ["figma-title"], oldBounds: [.1, .1, .2, .1], figmaBounds: [.1, .1, .2, .1] },
  { itemId: "new:dialog", figmaNodeId: "figma-dialog", figmaName: "Dialog", status: "blocked", score: 1, candidates: [], figmaBounds: [.4, .3, .3, .4] },
] };

describe("HifiMappingPanel", () => {
  it("explains automatically preserved structures without allowing manual structure waivers", () => {
    const structural: HifiMappingDraft = { mappingRevision: 1, unresolvedCount: 0, items: [{ itemId: "old:layout", oldObjectId: "layout", oldName: "Layout", oldObjectType: "group", status: "structural", action: "preserve_structure", score: 0, candidates: [], oldBounds: [0,0,1,1] }] };
    render(<HifiMappingPanel mapping={structural} busy={false} onCurrentChange={vi.fn()} onDecision={vi.fn()} />);
    expect(screen.getByText(/已自动保留 1 项非绘制结构/)).toBeVisible();
    expect(screen.queryByRole("button", { name: /保留旧对象不替换/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /就是这个图层/ })).not.toBeInTheDocument();
  });
  it("moves both structure highlights and resolves a blocked item as an exception", async () => {
    const decide = vi.fn();
    function Harness() {
      const [current, setCurrent] = useState("old:title");
      return <HifiMappingPanel mapping={mapping} currentItemId={current} busy={false} onCurrentChange={setCurrent} onDecision={decide} onLocate={vi.fn()} />;
    }
    render(<Harness />);
    expect(screen.getByRole("button", { name: "待确认 2" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("1 / 2")).toBeVisible();
    expect(screen.getByTestId("fgui-focus")).toHaveAccessibleName("旧 FGUI · Title");
    expect(screen.getByTestId("hifi-focus")).toHaveAccessibleName("HIFI · Title");
    await userEvent.click(screen.getByRole("button", { name: "下一项" }));
    expect(screen.getByText("2 / 2")).toBeVisible();
    expect(screen.getByTestId("fgui-focus")).toHaveTextContent("此侧无对应对象");
    expect(screen.getByTestId("hifi-focus")).toHaveAccessibleName("HIFI · Dialog");
    await userEvent.click(screen.getByRole("button", { name: "跳 · 列为例外，人工处理" }));
    expect(decide).toHaveBeenCalledWith(expect.objectContaining({ itemId: "new:dialog" }), "exception");
  });

  it("fits each preview to its own page dimensions and can focus the current object", async () => {
    const sized = { ...mapping, oldCanvasSize: { width: 1080, height: 1920 }, sourceCanvasSize: { width: 1080, height: 2340 } };
    render(<HifiMappingPanel mapping={sized} currentItemId="old:title" busy={false} onCurrentChange={vi.fn()} onDecision={vi.fn()} psdPreviewUrl="blob:psd-composite" />);

    const image = screen.getByRole("img", { name: "HIFI PSD 实际画面" });
    expect(image).toHaveAttribute("src", "blob:psd-composite");
    expect(image).toHaveStyle({ width: "100%", height: "100%" });
    expect(screen.getByLabelText("旧 FGUI 结构")).toHaveStyle({ aspectRatio: "1080 / 1920" });
    expect(screen.getByLabelText("HIFI 结构")).toHaveStyle({ aspectRatio: "1080 / 2340" });
    expect(screen.getByRole("button", { name: "完整页面" })).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(screen.getByRole("button", { name: "聚焦当前对象" }));
    expect(screen.getByRole("button", { name: "聚焦当前对象" })).toHaveAttribute("aria-pressed", "true");
    expect(image).not.toHaveStyle({ width: "100%" });
  });

  it("offers to map an unmatched PSD item to an existing FGUI object first", async () => {
    const decide = vi.fn();
    render(<HifiMappingPanel mapping={{ ...mapping, items: [...mapping.items, { itemId: "new:art", figmaNodeId: "figma-art", figmaName: "Artwork", status: "hifi_added", score: 1, candidates: [], figmaBounds: [.2, .2, .1, .1] }], unresolvedCount: 3 }} currentItemId="new:art" busy={false} onCurrentChange={vi.fn()} onDecision={decide} />);

    await userEvent.click(screen.getByRole("button", { name: "换 · 选另一个" }));
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "对应的旧 FGUI 对象" }), "old:title");
    await userEvent.click(screen.getByRole("button", { name: "建立一对一对应" }));
    expect(decide).toHaveBeenCalledWith(expect.objectContaining({ itemId: "old:title" }), "retarget", "figma-art");
  });

  it("does not offer direct visual addition in the PSD replacement workflow", () => {
    render(<HifiMappingPanel mapping={{ ...mapping, items: [...mapping.items, { itemId: "new:art", figmaNodeId: "figma-art", figmaName: "Artwork", status: "hifi_added", score: 1, candidates: [], figmaBounds: [.2, .2, .1, .1] }] }} currentItemId="new:art" busy={false} onCurrentChange={vi.fn()} onDecision={vi.fn()} allowVisualAddition={false} />);

    expect(screen.queryByRole("button", { name: "这是新增画面" })).not.toBeInTheDocument();
    expect(screen.getByText(/当前不能直接新增/)).toBeVisible();
    expect(screen.getByRole("button", { name: "跳 · 先不处理" })).toBeVisible();
  });

  it("stops manual review when automatic mapping exceeds five object decisions", () => {
    const excessive = { ...mapping, unresolvedCount: 6, items: Array.from({ length: 6 }, (_, index) => ({ ...mapping.items[1], itemId: `old:${index}`, oldObjectId: `${index}` })) };
    render(<HifiMappingPanel mapping={excessive} currentItemId="old:0" busy={false} onCurrentChange={vi.fn()} onDecision={vi.fn()} reviewLimit={5} />);

    expect(screen.getByText(/超过最多 5 个对象的人工判断上限/)).toBeVisible();
    expect(screen.getByLabelText("待处理记录分类")).toHaveTextContent("待处理是审计记录数，不等于独立人工决策");
    expect(screen.getByText("1 / 5")).toBeVisible();
    expect(screen.queryByRole("button", { name: /就是这个图层/ })).not.toBeInTheDocument();
  });

  it("can show extended review without offering old PSD visuals as a resolution", () => {
    const excessive = { ...mapping, unresolvedCount: 6, items: Array.from({ length: 6 }, (_, index) => ({ ...mapping.items[1], itemId: `old:${index}`, oldObjectId: `${index}` })) };
    render(<HifiMappingPanel mapping={excessive} currentItemId="old:0" busy={false} onCurrentChange={vi.fn()} onDecision={vi.fn()} reviewLimit={Infinity} allowKeepOld={false} />);
    expect(screen.getByText("1 / 6")).toBeVisible();
    expect(screen.getByRole("button", { name: "对 · 就是这个图层" })).toBeVisible();
    expect(screen.getByRole("button", { name: "换 · 选另一个" })).toBeVisible();
    expect(screen.getByText(/这一项不能跳过，也不能保留旧图/)).toBeVisible();
    expect(screen.queryByRole("button", { name: /保留旧对象不替换/ })).not.toBeInTheDocument();
  });

  it("offers PSD-undrawn old objects an explicit exception path", async () => {
    const decide = vi.fn();
    const unmapped = { ...mapping, unresolvedCount: 2, items: [mapping.items[0], { itemId: "old:loader", oldObjectId: "loader", oldName: "icon", oldObjectType: "loader", status: "fgui_only" as const, score: .4, candidates: [], oldBounds: [.1, .5, .05, .03] as [number, number, number, number] }] };
    render(<HifiMappingPanel mapping={unmapped} currentItemId="old:loader" busy={false} onCurrentChange={vi.fn()} onDecision={decide} />);

    expect(screen.getByText(/PSD 没有画这个旧对象/)).toBeVisible();
    const skip = screen.getByRole("button", { name: "跳 · PSD 没画它，隐藏旧视觉" });
    expect(skip).toBeVisible();
    await userEvent.click(skip);
    expect(decide).toHaveBeenCalledWith(expect.objectContaining({ itemId: "old:loader" }), "exception");
    expect(screen.queryByRole("button", { name: /就是这个图层/ })).not.toBeInTheDocument();
  });

  it("labels controller-hidden objects as non-default state work", () => {
    const hidden = { ...mapping, items: mapping.items.map((item) => item.itemId === "old:title" ? { ...item, defaultVisible: false } : item) };
    render(<HifiMappingPanel mapping={hidden} currentItemId="old:title" busy={false} onCurrentChange={vi.fn()} onDecision={vi.fn()} />);
    expect(screen.getByText(/1 条旧对象在控制器默认页不可见/)).toBeVisible();
    expect(screen.getByText(/单独验收非默认页的新视觉/)).toBeVisible();
  });
  it("selects a resolved canvas object by switching the list to all records", async () => {
    const select = vi.fn();
    function Harness() {
      const [current, setCurrent] = useState("old:title");
      return <HifiMappingPanel mapping={mapping} currentItemId={current} busy={false} onCurrentChange={(id) => { select(id); setCurrent(id); }} onDecision={vi.fn()} />;
    }
    render(<Harness />);
    await userEvent.click(screen.getByRole("button", { name: "旧 FGUI · Done" }));
    expect(screen.getByRole("button", { name: "全部 3" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("fgui-focus")).toHaveAccessibleName("旧 FGUI · Done");
    await userEvent.click(screen.getByRole("button", { name: "待确认 2" }));
    expect(select).toHaveBeenLastCalledWith("old:title");
    expect(screen.getByTestId("fgui-focus")).toHaveAccessibleName("旧 FGUI · Title");
  });

});
