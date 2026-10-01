# Vector PNG and Group Geometry Design

## Goal

Make arbitrary Figma vectors visually deterministic in FairyGUI Editor 6.1.4 while preserving the source hierarchy and geometry. Nodes that were previously exported as SVG become transparent PNG resources. Simple shapes that FairyGUI can represent reliably remain editable graphs.

This is a generic conversion rule. It must not depend on page names, layer names, node IDs, or the current village regression sample.

## Scope

### Rasterized vector boundary

- `VECTOR`, `BOOLEAN_OPERATION`, `STAR`, `POLYGON`, and every other node currently classified as a `vector_asset` with MIME `image/svg+xml` must be exported by the Figma plugin as `image/png`.
- Figma performs the rasterization through its native export API. The backend must not introduce a second SVG renderer.
- Plain solid rectangles, ellipses, supported strokes, and supported uniform corners continue to use editable FairyGUI `graph` objects.
- Existing PNG/JPEG image content remains image content.

### Geometry and rotation

- The committed selection records whether exported pixels already contain the source rotation. This fact must be explicit; the Writer must not infer it from node type or filename.
- For rotation-baked PNGs, the FairyGUI object uses the exported axis-aligned bounds and writes zero object rotation.
- For non-baked assets, the object retains source-local geometry and applies the source rotation exactly once.
- Decoded PNG width and height, committed resource metadata, Plan resource metadata, and the final Writer resource must close exactly. Any mismatch fails before publishing a ZIP.
- Transparent padding produced by Figma export is part of the resource and must not be cropped or independently rescaled.

## Nested Group and Layer Semantics

The reported group discrepancy is part of the required fix, not a separate cosmetic issue.

- Canonical Figma sibling order remains unchanged. FairyGUI panel display direction must not be used to reverse serialization order.
- Every child keeps its Figma parent relationship and is converted from parent-local geometry to component-local XML geometry exactly once.
- A FairyGUI `group` may derive its Editor selection bounds from members. The Writer must not use that derived group bound to rescale, reposition, or rotate members.
- Resource intrinsic size must not replace the node's intended display bounds. Intrinsic size validates payload identity; display bounds control layout.
- A rasterized child must not expand its parent or sibling geometry merely because its transparent PNG canvas is larger than the visible path.
- Plain groups remain after their members in XML, and mask sources retain their required ordering. These topology constraints do not authorize reordering ordinary siblings.

## Data Flow

1. The plugin classifies an arbitrary vector as PNG-backed raster content.
2. Figma exports the node as transparent PNG at `1x` design scale and supplies bounded PNG bytes.
3. The committed selection records PNG MIME, decoded dimensions, export parameters, and the rotation-baked disposition.
4. Normalize and UIR preserve the source hierarchy, parent-local bounds, and explicit raster disposition.
5. Plan chooses an image/raster node rather than a graph for this vector class.
6. Writer validates the PNG payload, projects geometry once, emits the original sibling/group topology, and publishes only after XML and archive closure pass.

## Failure Handling

- SVG bytes for a vector class covered by this policy are rejected rather than silently accepted.
- Invalid PNG, dimension mismatch, missing baked-rotation metadata, non-finite geometry, or double-rotation state prevents ZIP publication.
- Diagnostics identify the generic failed contract and stable public node identity without leaking source bytes or local paths.
- No fallback may silently convert the whole page or parent group into one image.

## Verification

### Positive cases

- Unrotated arbitrary vector becomes a transparent PNG at the same position and size.
- Rotated vector retains the same visible angle and axis-aligned placement with rotation applied exactly once.
- Boolean path and irregular SVG path preserve transparent edges without becoming a graph.
- Nested group with PNG-backed vector, graph circles, images, and text preserves source hierarchy and visual overlap.
- Simple rectangle and ellipse remain editable graphs.

### Negative cases

- SVG payload is rejected for a class that must now be PNG.
- Rotation-baked pixels plus non-zero Writer rotation are rejected.
- Swapped or mismatched PNG dimensions are rejected.
- Transparent padding cannot alter sibling or group layout bounds.
- Ordinary sibling reversal fails the ordering regression.
- Mask-source and group-topology violations fail XML validation.

### Required gates

- Focused plugin export and contract tests.
- Normalize, UIR, Plan, Writer, XML, and archive positive and negative tests.
- Replay of the current generic regression selection with no sample-specific branches.
- Full Python, plugin, and web self-checks in the sandbox before any completion claim.
- Final visual status remains pending until a newly generated ZIP is opened in FairyGUI Editor by the user.

## Exclusions

- Project Binding or updating an existing FairyGUI project.
- Path-level editing of arbitrary vectors inside FairyGUI.
- Page-name, layer-name, node-ID, or village-specific behavior.
- Whole-page rasterization.
