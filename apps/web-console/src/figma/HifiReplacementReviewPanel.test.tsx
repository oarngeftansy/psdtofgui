import { useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { HifiReplacementReview } from "../../../figma-plugin/src/project-client";
import { HifiReplacementReviewActions, HifiReplacementReviewPanel, type HifiEditorCheckState } from "./HifiReplacementReviewPanel";

const review: HifiReplacementReview = {
  sessionId: "a".repeat(32), mappingRevision: 3, changedFiles: [{ relativePath: "assets/MyVillage/Panel/Root.xml", operation: "replace", summary: "更新视觉" }],
  objectDiffs: [
    { itemId: "old:title", kind: "changed", oldObjectId: "title", oldName: "Title", figmaNodeId: "figma-title", figmaName: "Title", changedFields: ["xy"], summary: "修改视觉字段：xy" },
    { itemId: "old:legacy", kind: "kept", oldObjectId: "legacy", oldName: "Legacy", changedFields: [], summary: "保留旧对象" },
  ],
  protectedChecksPassed: true, parseCoverageComplete: true, approvable: true, candidateSha256: "b".repeat(64), warnings: [], editorCheckRequired: true,
};

describe("HifiReplacementReviewPanel", () => {
  it("shows object-level evidence and gates delivery on all Editor checks", async () => {
    const approve = vi.fn();
    function Harness() {
      const [checks, setChecks] = useState<HifiEditorCheckState>({ layout: false, references: false, interactions: false });
      return <><HifiReplacementReviewPanel review={review} checks={checks} busy={false} onChecksChange={setChecks} onDownloadCandidate={vi.fn()} onReject={vi.fn()} /><footer><HifiReplacementReviewActions review={review} checks={checks} busy={false} onReturn={vi.fn()} onApprove={approve} /></footer></>;
    }
    render(<Harness />);
    expect(screen.getByText("修改视觉字段：xy")).toBeVisible();
    expect(screen.getByText("b".repeat(64))).toBeVisible();
    const deliver = screen.getByRole("button", { name: "确认并交付 ZIP" });
    expect(deliver).toBeDisabled();
    for (const label of ["布局与图层顺序正确", "图片与共享组件引用正常", "Controller、Gear、Transition 正常"]) await userEvent.click(screen.getByRole("checkbox", { name: label }));
    expect(deliver).toBeDisabled();
    await userEvent.click(deliver);
    expect(approve).not.toHaveBeenCalled();
  });
});
