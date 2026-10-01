# Standard FairyGUI Package Layout Design

**Date:** 2026-08-31

## Goal

Every newly generated standalone FairyGUI 6.1.4 project must expose the same business-package structure in the FairyGUI editor:

```text
{PackageName}/
├─ Component/
├─ Img/
├─ Panel/
└─ package.xml
```

All generated image resources must be real PNG files. The writer must preserve this structure even when one of the three directories has no generated files.

## Current Behavior

The new-project writer currently emits component XML under `components/` and image payloads under `resources/`. Root selection components and reusable component definitions share the same directory. The generated archive also contains no explicit entry for an empty logical directory.

This differs from the established FairyGUI package convention already used by the legacy generation path and by the target project shown by the user:

- top-level screens belong in `Panel/`;
- reusable components belong in `Component/`;
- image resources belong in `Img/`.

Although most current selection exports are PNG, the manifest models still permit other image formats. A path-only rename would therefore not establish a trustworthy PNG-only output contract.

## Chosen Approach

Apply the standard layout as a first-class writer invariant, not as a ZIP post-processing rename.

The manifest compiler will classify every generated component from its existing source kind:

- `sourceComponentKind == "root"` -> `Panel/{ReadableName}-{StableId}.xml`
- `sourceComponentKind == "definition"` -> `Component/{ReadableName}-{StableId}.xml`
- every raster resource -> `Img/{ReadableName}-{StableId}.png`

The package serializer, component serializer, preview endpoints, project-file serializer, archive validator, and manifest validator will continue to consume the manifest paths. This keeps one path authority and prevents the ZIP, `package.xml`, and component references from drifting apart.

The generated project keeps its existing outer layout:

```text
{ProjectName}.fairy
assets/{PackageName}/...
```

Only the contents of each FairyGUI business package change to the standard directory names.

## Path and Reference Rules

Path generation is case-sensitive and fixed:

| Artifact | Manifest relative path | `package.xml` path |
|---|---|---|
| Root screen | `Panel/<name>.xml` | `/Panel/` |
| Reusable definition | `Component/<name>.xml` | `/Component/` |
| Image | `Img/<name>.png` | `/Img/` |

Component XML references must use the same manifest paths:

- image `fileName` values use `Img/...png`;
- generated component `fileName` values use either `Panel/...xml` or `Component/...xml` according to the referenced component;
- resource IDs and deterministic file-name suffixes remain unchanged.

The HTTP route names used to fetch previews are API concepts and do not need to be renamed. They resolve resources by manifest ID, so changing their public URLs would add risk without changing the generated package.

## PNG-Only Resource Contract

The new-project pipeline will guarantee all image outputs as PNG at the boundary where a committed selection becomes a buildable plan:

1. Figma visual exports used as FairyGUI image resources are requested/rendered as PNG.
2. The plan and manifest must declare `exportFormat="png"` and `mimeType="image/png"`.
3. The validated payload must have a PNG signature and dimensions matching the declared resource facts.
4. The generated file name must end in `.png` and live under `Img/`.

The writer must never turn JPEG, WebP, or SVG bytes into a `.png` file by extension-only renaming. If a non-PNG payload reaches the writer, the build fails before publication with the existing safe validation error. Vector content that requires raster fallback is rendered to PNG by the selection-export stage; editable vector content remains an FGUI object and creates no image file.

This preserves deterministic hashes and avoids introducing a second lossy transcoding implementation in the archive writer.

## Empty Directory Preservation

ZIP archives do not preserve empty filesystem directories unless they contain explicit directory entries. The archive publisher will therefore add deterministic directory members for:

```text
assets/{PackageName}/Component/
assets/{PackageName}/Img/
assets/{PackageName}/Panel/
```

These entries are structural only:

- they are not registered as resources in `package.xml`;
- they do not appear as manifest components or image resources;
- they use the same deterministic ZIP metadata as generated files;
- archive validation permits exactly these required package-directory entries and verifies that all three exist.

After extraction, FairyGUI sees all three folders even when one is empty.

## Validation

Validation will reject a generated candidate when any of these invariants is violated:

- a root component is outside `Panel/`;
- a definition component is outside `Component/`;
- an image resource is outside `Img/`;
- an image name does not end in `.png`;
- an image declares a non-PNG MIME type or export format;
- image bytes do not have a valid PNG signature;
- `package.xml` registers a resource under an old or mismatched path;
- a component XML `fileName` disagrees with its manifest target;
- any required package directory is absent from the completed archive;
- old `components/` or `resources/` members are emitted by the new-project writer.

Validation remains fail-closed and occurs before the archive is published to the user.

## Compatibility and Migration

This becomes the only output layout for newly generated standalone projects. No user-facing compatibility switch is added.

- Existing FairyGUI projects on disk are not rewritten.
- Existing upload and preview API contracts remain stable.
- Existing IDs, ordering, file-name sanitization, and collision rules remain stable.
- Tests and fixtures that assert the old new-project paths migrate to the new invariant.
- The separate legacy create/update pipeline already using `Panel/` and `Img/` is not reorganized by this change.

## Test Strategy

Implementation follows test-driven development:

1. Add focused path tests proving roots use `Panel/`, definitions use `Component/`, and resources use `Img/*.png`.
2. Add manifest-validation tests rejecting wrong component kinds, old directories, and non-PNG declarations.
3. Add XML-dialect tests proving `package.xml` and `fileName` references use the new paths.
4. Add archive tests proving all three directory entries exist, including empty directories, and proving legacy directory members are absent.
5. Add payload tests proving non-PNG bytes cannot be published as PNG.
6. Update workflow and end-to-end assertions, then run the focused new-project suite and the broader regression suite.

## Acceptance Criteria

A generated package named `Shop` opens with `Component`, `Img`, and `Panel` directly beneath `Shop`. Root selection screens are editable XML files in `Panel`; extracted reusable definitions are editable XML files in `Component`; every generated image is a valid PNG in `Img`. The same paths appear consistently in the archive, manifest, `package.xml`, component XML references, previews, and validators, and all three directories remain present when empty.
