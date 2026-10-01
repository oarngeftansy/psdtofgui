import { describe, expect, it } from "vitest";
import { parseNineSliceAnnotation } from "./nine-slice";

const bounds = { width: 100, height: 60 };

describe("nine-slice annotation", () => {
  it("parses a valid trailing annotation and removes it from the display name", () => {
    expect(parseNineSliceAnnotation("Primary Button @9s(12,8,12,8)", bounds)).toEqual({
      displayName: "Primary Button",
      insets: { left: 12, top: 8, right: 12, bottom: 8 },
      diagnostic: null,
    });
  });

  it.each([
    ["Panel", null],
    ["Panel @9s(1,2,3)", "nine_slice_invalid"],
    ["Panel @9s(1.5,2,3,4)", "nine_slice_invalid"],
    ["Panel @9s(-1,2,3,4)", "nine_slice_invalid"],
    ["Panel @9s(1,2,3,4) @9s(1,2,3,4)", "nine_slice_invalid"],
    ["Panel @9s(50,2,50,4)", "nine_slice_out_of_bounds"],
    ["Panel @9s(1,30,3,30)", "nine_slice_out_of_bounds"],
  ])("handles %s", (name, diagnostic) => {
    const result = parseNineSliceAnnotation(name, bounds);
    expect(result.insets).toBeNull();
    expect(result.diagnostic).toBe(diagnostic);
  });
});
