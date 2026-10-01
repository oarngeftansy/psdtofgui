# Writer Review Workbench Layout Design

## Goal

Replace the dense all-in-one Writer screen with a 640×800 portrait workflow that presents automatic conversion first, recommended review second, and final confirmation last. Produce an animated standalone HTML demo first; only apply the approved interaction to the Figma plugin afterward.

## Scope

- New-project Writer review only.
- No Project Binding or existing-project workflow changes.
- No village, page-name, or node-ID special cases; village content is realistic demo data only.
- This revision changes presentation and review interaction, not conversion policy or mapping rules.

## Viewport and Navigation

- Fixed portrait demo viewport: 640×800 CSS pixels.
- Three separate steps: `自动转换` → `建议审核` → `确认下载`.
- Only the current step's content is rendered; there is no persistent review directory, dashboard grid, or four-tab evidence matrix.
- The header contains only context and step progress. The footer contains only actions relevant to the current step.
- Each step has at most one vertical scroll surface, with no nested scrolling and no footer overlap.

## Step 1: Automatic Conversion

- Lead with the conversion result rather than configuration or technical diagnostics.
- Show a concise total and a vertical list of automatically handled items.
- Each item has a visual before/after illustration, the conversion method (`原生转换` or `视觉保真图片`), and its editability impact.
- Technical package details are collapsed under `工程详情` at the bottom and are not part of the primary workflow.
- Primary action: `查看建议审核`.

## Step 2: Recommended Review

- Review one item at a time; show `第 n / total 项` with previous/next controls instead of a permanent directory.
- The review image is a contextual crop: it includes the target node plus enough surrounding artwork to understand placement. The target is outlined and labelled.
- The main evidence area gives most of the viewport to two large, equal-size portrait previews: `Figma 原图` and `FairyGUI 结果`.
- Below the previews, explain why review is recommended, what visual or editability impact exists, and only the server-declared adjustment choices.
- Each review item includes an illustration. A text-only warning is not acceptable.
- Users can acknowledge the current recommendation or choose an allowed adjustment.
- Primary secondary action: `复制到 Figma 审核区`.

## Figma Review Area

- Copying is explicit and user-triggered; opening the review step does not mutate the Figma document.
- The copied review area is placed to the right of the currently selected frame on the same Figma page.
- It is a separate top-level frame named `FairyGUI 待审核`, not a child of or mutation to the production artwork.
- Each copied item contains: original contextual crop, generated preview, highlighted target, issue explanation, and suggested action.
- Repeating the action updates/reuses the same review frame for the current candidate rather than creating unbounded duplicates.
- The HTML demo animates this operation as a canvas preview and confirmation state; it does not call the Figma API.

## Step 3: Confirm and Download

- Show resolved review count, remaining blockers, project name, archive summary, and final unified check.
- Approval/download is enabled only when all required acknowledgements are complete and no blocked item remains.
- Package, component, and resource diagnostics appear here under `工程详情`, not as top-level review navigation.

## Blocked Items

- A blocked conversion uses the same illustrated one-item review layout.
- It shows the exact source context, the reason conversion cannot safely continue, and actions to locate or fix the source.
- Download remains locked; the UI never presents a blocked item as merely optional review.

## Motion

- Step changes use a short horizontal slide/fade that reinforces forward/backward movement.
- Review-item navigation crossfades the evidence pair without moving the footer.
- `复制到 Figma 审核区` animates the evidence cards into a miniature Figma-canvas frame and then reports success.
- Motion lasts 160–280ms, does not loop, and is disabled under `prefers-reduced-motion`.

## Visual Direction

- Production-tool aesthetic: cool gray workspace, white evidence surfaces, restrained Figma blue actions.
- Green, amber, and red remain semantic only: automatic, recommended, and blocked.
- Portrait evidence is the visual focus; configuration and diagnostics stay visually quiet.
- Signature element: the illustrated contextual target outline persists across the Figma/FairyGUI pair, making the reason for review immediately legible.

## Demo States and Content

- The self-contained demo includes at least two recommended-review items and one automatic-conversion list.
- Controls must actually switch steps, move between review items, acknowledge a recommendation, expand engineering details, and play the copy-to-Figma animation.
- Demo illustrations are embedded CSS/SVG shapes and require no external files or network access.

## Acceptance Criteria

- The demo measures exactly 640×800 and is optimized for portrait usage.
- Automatic conversion is the first content screen; recommended review is a separate screen.
- No permanent review directory or evidence tab matrix appears inside the workbench.
- Recommended and blocked items always include visible before/after illustrations.
- Both portrait previews are large, equal, aligned, and remain readable without horizontal scrolling.
- The current item indicator and previous/next controls are clear without consuming evidence space.
- The copy-to-Figma action visibly demonstrates creation of a separate review frame to the right of the selected frame.
- There is one scroll surface per step, no nested scrollbar, and no footer overlap.
- Keyboard focus is visible, interactive controls work by keyboard, and reduced-motion is respected.
- The HTML is self-contained and can be opened locally without a server.
