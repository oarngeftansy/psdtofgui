import { useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { HifiTargetPicker, type HifiTargetSelection } from "./HifiTargetPicker";

const project = { projectId: "a".repeat(32), displayName: "Old.zip", packages: [] };
const tree = {
  projectId: project.projectId,
  projectFingerprint: "b".repeat(64),
  packages: [{ packageId: "pkg", name: "MyVillage", directories: [
    { path: "Panel", selectable: true, components: [{ resourceId: "root", name: "Panel_Root", relativePath: "assets/MyVillage/Panel/Panel_Root.xml", selectable: true }] },
    { path: "Component", selectable: true, components: [{ resourceId: "card", name: "Card", relativePath: "assets/MyVillage/Component/Card.xml", selectable: true }] },
  ] }],
};

describe("HifiTargetPicker", () => {
  it("selects a root and clears it when another directory is selected", async () => {
    const changed = vi.fn();
    function Harness() {
      const [value, setValue] = useState<HifiTargetSelection>();
      return <HifiTargetPicker project={project} tree={tree} value={value} onChange={(next) => { changed(next); setValue(next); }} />;
    }
    render(<Harness />);
    expect(screen.queryByRole("button", { name: /Panel_Root/ })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /MyVillage.*2 个目录/ }));
    await userEvent.click(screen.getByRole("button", { name: /Panel.*1 个组件/ }));
    await userEvent.click(screen.getByRole("button", { name: /Panel_Root/ }));
    expect(screen.getByText("MyVillage / Panel")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: /Component.*1 个组件/ }));
    expect(changed).toHaveBeenLastCalledWith([0, 1, -1]);
    expect(screen.queryByText("MyVillage / Panel")).not.toBeInTheDocument();
  });
});
