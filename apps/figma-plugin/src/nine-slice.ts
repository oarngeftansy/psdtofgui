export type NineSliceInsets = { left: number; top: number; right: number; bottom: number };
export type NineSliceDiagnostic = "nine_slice_invalid" | "nine_slice_out_of_bounds";
export type NineSliceParseResult = { displayName: string; insets: NineSliceInsets | null; diagnostic: NineSliceDiagnostic | null };

const MARKER = /@9s\(([^)]*)\)/g;
const VALID_TRAILING = /\s*@9s\((\d+),(\d+),(\d+),(\d+)\)\s*$/;

export function parseNineSliceAnnotation(name: string, bounds: { width: number; height: number }): NineSliceParseResult {
  const markers = [...name.matchAll(MARKER)];
  if (!name.includes("@9s")) return { displayName: name, insets: null, diagnostic: null };
  const match = VALID_TRAILING.exec(name);
  if (!match || markers.length !== 1) return { displayName: name, insets: null, diagnostic: "nine_slice_invalid" };
  const [left, top, right, bottom] = match.slice(1).map(Number) as [number, number, number, number];
  const displayName = name.slice(0, match.index).trimEnd();
  if (left + right >= bounds.width || top + bottom >= bounds.height) {
    return { displayName, insets: null, diagnostic: "nine_slice_out_of_bounds" };
  }
  return { displayName, insets: { left, top, right, bottom }, diagnostic: null };
}
