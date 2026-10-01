# ZIP Project Web Console Design

## Goal

Replace the fixture-project and Swagger workflow with a designer-facing Web Console. A UX designer uploads a FairyGUI project ZIP, the server parses it automatically, presents understandable visual changes, and sends an approved update to the Windows Agent for safe application to the matching local project.

This phase continues to use fixture Figma JSON. Live Figma API access and the Figma selection plugin remain a separate following phase.

## Product Language

The primary interface is written for UX designers. It uses terms such as “上传工程 ZIP”, “本次会发生什么”, “当前工程”, “更新以后”, and “确认更新到本地工程”.

Implementation terms including snapshot, SHA-256, changeset, XML, resource IDs, and file operations are hidden by default. They are available only under “高级详情” for developers and support work.

## User Flow

1. The user opens the Web Console in a desktop browser.
2. The user drags a FairyGUI project ZIP into the upload area.
3. The server validates and safely extracts the ZIP into isolated storage.
4. The server identifies the project root, packages, component XML, images, and resource relationships automatically.
5. The user selects the target Package when the ZIP contains more than one.
6. The existing conversion core runs against the uploaded project baseline and fixture Figma JSON.
7. The Web Console presents the resulting changes in a three-column review workspace.
8. The user approves or rejects the complete update. Individual files cannot be excluded.
9. The Windows Agent matches the uploaded project to a previously bound local project. If no confident match exists, it asks the user to choose the folder once.
10. The Agent verifies the local project is still the uploaded version, creates a backup, applies the update atomically, validates it, and reports the result.
11. If the local project changed after upload, the Agent refuses to overwrite it and the Web Console asks for a new ZIP.

## Architecture

### Web Console

Use React, TypeScript, and Vite. The production build is served by the FastAPI application so users open one server URL. The frontend consumes versioned JSON APIs and does not read local paths directly.

The first release has three routes:

- `/` — upload and recent tasks
- `/jobs/{jobId}` — three-column review workspace
- `/projects/{projectId}` — uploaded project summary and Agent binding status

### API Server

Extend the current FastAPI service with project upload, safe ZIP parsing, project indexing, preview summaries, approval, and static Web Console hosting. SQLite continues to store metadata and state. Extracted project artifacts remain in the existing content-addressed local artifact store.

### Windows Agent

The Agent continues to own all local filesystem writes. It stores local project bindings and derives a project fingerprint from stable FairyGUI metadata and relative paths. The fingerprint is internal and is never shown in the normal Web Console.

The Python CLI Agent remains the delivery vehicle for this phase. Migration to the planned .NET 8 tray application follows after the protocol and user flow are stable.

## ZIP Upload and Project Parsing

The Web Console accepts `.zip` files only. The default limits are:

- 500 MB compressed upload
- 2 GB total uncompressed content
- 20,000 entries
- 100 MB per file
- 20:1 maximum aggregate compression ratio

All limits are server configuration values. Exceeding a limit rejects the upload without partial project creation.

The ZIP parser:

- rejects absolute paths, drive-qualified paths, `..` segments, NUL bytes, duplicate normalized paths, and symbolic-link entries;
- extracts only into a newly created isolated directory;
- resolves every final output path and verifies it remains inside that directory;
- ignores `.figma-to-fgui/`, temporary files, operating-system metadata, and existing backup folders;
- accepts either a project at the ZIP root or one harmless wrapping directory;
- locates FairyGUI package descriptors and validates XML before recording a project;
- computes internal hashes and metadata after extraction, never trusting ZIP-supplied manifests.

Invalid or ambiguous archives produce a designer-readable error and no usable project record.

## Uploaded Project Model

An uploaded project version records:

- project and version identifiers;
- original upload name and upload time;
- detected packages;
- normalized relative file paths;
- internal size and SHA-256 values;
- component XML and package descriptors;
- image dimensions and generated thumbnails;
- a private project fingerprint used for Agent matching.

Original images remain in isolated artifact storage. The browser receives thumbnails by default and downloads an original only when a detailed preview requires it.

## Review Workspace

The approved layout uses three columns:

### Left: “本次会发生什么”

- Groups changes by interface, component, and image rather than raw file operation.
- Uses “新增” and “更新”; delete remains unsupported.
- Shows a simple total such as “3 项新增 · 2 项更新”.

### Center: Visual Difference

- Images use side-by-side before/after previews.
- Components show rendered preview when available and a structured property summary otherwise.
- Package changes appear as human-readable resource additions and updates.
- Raw XML is available only under advanced details.

### Right: Checks and Decision

- Shows plain-language results such as “工程结构正常” and “图片和组件均可用”.
- Warnings explain user impact, for example “2 处位置有轻微调整”.
- Explains that a backup is created and newer local work will not be overwritten.
- Provides “确认更新到本地工程” and “暂不更新”.

Approval always applies to the complete validated update. Per-file selection is not supported because package descriptors, components, and resources must remain consistent.

## API Additions

```text
POST /v1/projects/uploads
GET  /v1/projects/{projectId}
GET  /v1/projects/{projectId}/packages
GET  /v1/projects/{projectId}/assets/{assetId}/thumbnail
POST /v1/projects/{projectId}/jobs
GET  /v1/jobs/{jobId}/designer-preview
POST /v1/jobs/{jobId}/reject
```

Existing approval, assignment, changeset, and apply-result endpoints remain. Job creation now references an uploaded project version rather than a server fixture project. Fixture Figma JSON remains an explicit temporary request field until the Figma phase replaces it.

## Local Project Matching

The uploaded project and each Agent binding have an internal fingerprint based on stable FairyGUI package identity and a normalized path manifest. Matching rules are:

1. Exact project fingerprint match selects the binding automatically.
2. A single compatible binding with the same package identities may be offered for user confirmation.
3. Zero or multiple compatible bindings require the user to choose a local folder in the Agent.
4. The selected folder is revalidated and saved for later jobs.

Before applying an approved update, the Agent compares all affected pre-write file states against the uploaded project version. Any difference stops the update and reports “本地工程已有新修改，请重新上传最新版”.

## Errors and Recovery

Primary errors use designer-facing messages:

- “这个 ZIP 不是有效的 FairyGUI 工程”
- “压缩包过大或包含过多文件”
- “无法安全读取这个压缩包”
- “本地助手当前未连接”
- “找不到对应的本地工程，请在本地助手中选择一次”
- “本地工程已有新修改，请重新上传最新版”
- “更新没有完成，已自动恢复到原来的版本”
- “无法自动恢复，请从备份位置恢复”

Each error also carries a stable internal code and technical details for advanced mode and logs. Absolute local paths, file contents, and credentials never appear in server logs or normal API errors.

## Testing

### ZIP Security Tests

Cover valid root and wrapping-directory archives, traversal, drive paths, absolute paths, duplicate normalized entries, symlinks, NUL bytes, entry-count limits, size limits, compression-ratio limits, malformed ZIP data, and invalid XML.

### Project Parsing Tests

Cover multiple packages, components, images, resource relationships, thumbnails, deterministic fingerprints, and identical uploads producing the same internal manifest.

### API Tests

Cover upload lifecycle, invalid upload cleanup, package selection, job creation against the uploaded project, designer preview, whole-job approval/rejection, and protected artifact access.

### Agent Tests

Cover exact matching, ambiguous matching, explicit folder selection, changed-local-project refusal, backup, successful apply, and rollback.

### Web Tests

Cover upload progress, empty/loading/error states, keyboard navigation, accessible labels, three-column review, visual preview switching, advanced-detail disclosure, whole-job approval confirmation, and desktop responsive behavior.

### End-to-End Test

Upload a real test project ZIP, create a fixture-Figma conversion job, review and approve it through browser-level automation, let the Agent apply it to a copied local fixture, verify final files and backup, and confirm a later local edit blocks repeat application.

## Acceptance Criteria

- A UX designer can upload a FairyGUI project ZIP and complete the main workflow without seeing or understanding hashes, XML, internal IDs, or changeset terminology.
- The server automatically identifies valid FairyGUI packages, components, and images.
- The review workspace shows understandable additions, updates, visual differences, and warnings.
- Approval or rejection applies to the complete update.
- The Agent automatically matches an exact local binding or asks for a folder only when necessary.
- Newer local work is never overwritten.
- Failed writes restore the original project when rollback succeeds and clearly identify the backup when it does not.
- Unsafe or malformed ZIP files cannot write outside isolated server storage or create a usable project.
- The Web Console is fully usable on a desktop browser; mobile is view-only.

## Deferred Work

- Live Figma REST API and image download
- Figma selection plugin
- Per-file approval or partial changesets
- Multi-user authentication, team roles, and remote production deployment
- PostgreSQL, MinIO, and background workers
- .NET tray Agent, installer, code signing, and auto-update
- Automatic FairyGUI editor refresh
