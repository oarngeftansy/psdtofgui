import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { NewProjectWriterPanel } from "./NewProjectWriterPanel";

const client = {
  createNewProjectCandidate: vi.fn(),
  reviewNewProject: vi.fn(),
  adjustNewProject: vi.fn(),
  regenerateNewProject: vi.fn(),
  approveNewProject: vi.fn(),
  rejectNewProject: vi.fn(),
  downloadNewProject: vi.fn(),
  newProjectPreview: vi.fn(),
};

describe("HIFI product visibility", () => {
  it("does not show the HIFI entry when the product is hidden", () => {
    render(<NewProjectWriterPanel client={client} postToFigma={vi.fn()} />);
    expect(screen.queryByRole("tab", { name: "HIFI 替换" })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "新建工程" })).toBeVisible();
  });

  it("keeps the existing HIFI entry reusable when it is explicitly enabled", () => {
    render(<NewProjectWriterPanel client={client} postToFigma={vi.fn()} onOpenHifi={vi.fn()} />);
    expect(screen.getByRole("tab", { name: "HIFI 替换" })).toBeVisible();
  });
});
