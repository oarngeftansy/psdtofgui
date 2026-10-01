from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import shutil
import stat
import tempfile
import uuid
from asyncio import CancelledError
from collections.abc import Callable
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event, Thread
from typing import Annotated, Literal, TypeVar, cast
from zipfile import BadZipFile, ZipFile

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from lxml import etree
from pydantic import BaseModel, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)

from figma_to_fgui.ai_client import MAX_SCREENSHOT_BYTES
from figma_to_fgui.artifacts import ArtifactIntegrityError, ArtifactStore
from figma_to_fgui.designer_preview import (
    DesignerPreview,
    build_designer_preview,
    is_designer_image,
)
from figma_to_fgui.fairygui_editor_verify import (
    FairyGuiEditorVerificationError,
    verify_in_fairygui_editor,
)
from figma_to_fgui.fairygui_editor_verify import (
    screenshot_path as editor_screenshot_path,
)
from figma_to_fgui.fgui_conversion_dispositions import (
    build_blocked_dispositions,
    build_conversion_dispositions,
)
from figma_to_fgui.fgui_new_project_review import (
    NewProjectDesignerReview,
    build_blocked_new_project_designer_review,
    build_new_project_designer_review,
    strategy_allowed_for_check,
)
from figma_to_fgui.fgui_new_project_workflow import (
    NewProjectWorkflowError,
    build_selection_new_project,
)
from figma_to_fgui.figma_pairing import PairingError, PairingStore, require_scope
from figma_to_fgui.figma_selection import (
    SelectionError,
    SelectionManifest,
    SelectionTopLevelSummary,
    SelectionView,
)
from figma_to_fgui.filesystem_paths import io_path
from figma_to_fgui.fixed_fonts import (
    PROJECT_FIXED_FONTS,
    FixedFontSpec,
    check_fixed_fonts,
    windows_font_roots,
)
from figma_to_fgui.hifi_patch import HifiPatchError
from figma_to_fgui.hifi_project_inspector import inspect_hifi_targets
from figma_to_fgui.hifi_replacement_models import (
    HifiEditorChecks,
    HifiEditorVerification,
    HifiMappingDecision,
    HifiMappingDraft,
    HifiProjectTreeView,
    HifiPsdBatchReplacementCreate,
    HifiPsdReplacementCreate,
    HifiReplacementBuildRequest,
    HifiReplacementCreate,
    HifiReplacementRejectRequest,
    HifiReplacementReview,
    HifiReplacementView,
)
from figma_to_fgui.hifi_replacement_store import (
    HifiReplacementStore,
    HifiReplacementStoreError,
    StoredHifiReplacement,
)
from figma_to_fgui.hifi_replacement_workflow import HifiReplacementWorkflow
from figma_to_fgui.image_preview import encode_webp_preview
from figma_to_fgui.job_store import (
    InvalidTransition,
    JobStore,
    NotFound,
    PackageConsentConflict,
    PackageRequestConflict,
    ScreenshotAnalysisStartState,
    StoredNewProject,
    StoredPackage,
    StoreError,
)
from figma_to_fgui.models import (
    ChangeSet,
    ClassificationDecision,
    Diagnostic,
    NormalizedNode,
    Severity,
)
from figma_to_fgui.normalize import SelectionAsset, selection_conversion_document
from figma_to_fgui.pipeline import ConversionLimitError, SemanticAnalyzer, convert_document
from figma_to_fgui.plugin_access import PluginAccess
from figma_to_fgui.project_package import build_project_package
from figma_to_fgui.project_store import ProjectIntegrityError, ProjectStore
from figma_to_fgui.project_templates import TemplateCatalog, TemplateNotFound
from figma_to_fgui.project_upload import (
    DEFAULT_UPLOAD_LIMITS,
    UploadError,
    _upload_error,
    extract_project_archive,
    project_archive_suffix,
)
from figma_to_fgui.psd_intake import PsdIntakeError, inspect_psd
from figma_to_fgui.psd_source_store import PsdSourceStore, PsdSourceStoreError
from figma_to_fgui.selection_store import SelectionStore
from figma_to_fgui.semantic_models import SemanticAnalysisOutcome
from figma_to_fgui.semantic_screenshot_storage import (
    sweep_semantic_screenshot_orphans as _sweep_semantic_screenshot_orphans,
)
from figma_to_fgui.semantic_screenshot_storage import (
    unlink_semantic_screenshot as _unlink_semantic_screenshot,
)
from figma_to_fgui.semantic_screenshot_storage import (
    write_semantic_screenshot as _write_semantic_screenshot,
)
from figma_to_fgui.service_contracts import (
    AgentRegistration,
    ApplyResult,
    ChangeBundle,
    ChangeFile,
    ConsolePairingStatusView,
    CreateTemplateProject,
    FigmaDeviceView,
    FileOperation,
    JobCreate,
    JobStatus,
    JobSummary,
    JobView,
    NewFguiProjectRequest,
    NewFguiProjectStage,
    NewFguiProjectView,
    NewProjectAdjustment,
    NewProjectAdjustmentRequest,
    NewProjectAdjustmentStrategy,
    NewProjectApprovalRequest,
    NewProjectRegenerateRequest,
    NewProjectRejectRequest,
    PackageView,
    PairingCodeView,
    PairingExchange,
    PluginCredentialView,
    PluginPrincipal,
    PluginScope,
    ProjectBinding,
    ProjectJobCreate,
    ProjectOptionsView,
    ProjectPackageRequest,
    ProjectPackageStage,
    ProjectPackageView,
    ProjectUploadView,
    ScreenshotConsentRequest,
    SelectionProjectJobCreate,
)
from figma_to_fgui.uploaded_project import UploadedProjectVersion, index_uploaded_project

_UPLOAD_CHUNK_BYTES = 64 * 1024
_PROJECT_NOT_FOUND_MESSAGE = "鎵句笉鍒拌繖涓?FairyGUI 宸ョ▼銆?"
_ASSET_NOT_FOUND_MESSAGE = "鎵句笉鍒拌繖寮犻瑙堝浘鐗囥€俙"
_VITE_HASHED_ASSET = re.compile(r"^.+-[A-Za-z0-9_-]{8,}\.[A-Za-z0-9]+$")
_PAIRING_MESSAGE = "Pairing request could not be completed."
_SELECTION_MESSAGE = "Selection upload could not be completed."
_SELECTION_MANIFEST_BYTES = 5 * 1024 * 1024
_MAX_PSD_BYTES = 3 * 1024 * 1024 * 1024
_MAX_CHANGE_BUNDLE_BYTES = 8 * 1024 * 1024
_BUNDLED_PLUGIN_DEVICE_ID = "bundled-figma-plugin"
_PACKAGE_LEASE_DURATION = timedelta(minutes=2)
MAX_SEMANTIC_SCREENSHOT_BYTES = MAX_SCREENSHOT_BYTES
_StrictPayload = TypeVar("_StrictPayload", bound=BaseModel)
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _cleanup_semantic_screenshot(
    store: JobStore,
    job_id: str,
    generation: int,
    digest: str | None,
    path: Path | None,
    allowed_root: Path,
) -> None:
    protected_path = False
    removed = path is None
    if path is not None:
        with suppress(StoreError):
            current = store.get_package(job_id)
            protected_path = current.screenshot_path == path and (
                current.generation != generation or current.screenshot_digest != digest
            )
    if path is not None and not protected_path:
        removed = _unlink_semantic_screenshot(path, allowed_root)
    if digest is not None and removed:
        with suppress(StoreError):
            store.clear_screenshot_path(job_id, generation, digest)


class _ImmutableStaticFiles(StaticFiles):
    def file_response(
        self,
        full_path: str | os.PathLike[str],
        stat_result: os.stat_result,
        scope: Scope,
        status_code: int = 200,
    ) -> Response:
        response = super().file_response(full_path, stat_result, scope, status_code)
        if _VITE_HASHED_ASSET.fullmatch(Path(full_path).name):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


_PLUGIN_ACCESS_ROUTES = (
    ("POST", re.compile(r"^/v1/figma/selections/uploads$")),
    ("PUT", re.compile(r"^/v1/figma/selections/uploads/[^/]+/manifest$")),
    ("PUT", re.compile(r"^/v1/figma/selections/uploads/[^/]+/resources/[^/]+$")),
    ("POST", re.compile(r"^/v1/figma/selections/uploads/[^/]+/commit$")),
    ("GET", re.compile(r"^/v1/figma/selections/[^/]+$")),
    ("GET", re.compile(r"^/v1/figma/selections/[^/]+/previews/[^/]+$")),
    ("GET", re.compile(r"^/v1/figma/selections/[^/]+/resources/[^/]+$")),
    ("POST", re.compile(r"^/v1/figma/selections/[^/]+/new-fgui-projects$")),
    ("GET", re.compile(r"^/v1/new-fgui-projects/[^/]+$")),
    ("GET", re.compile(r"^/v1/new-fgui-projects/[^/]+/review$")),
    ("GET", re.compile(r"^/v1/new-fgui-projects/[^/]+/download$")),
    ("GET", re.compile(r"^/v1/new-fgui-projects/[^/]+/previews/resources/[^/]+$")),
    ("GET", re.compile(r"^/v1/new-fgui-projects/[^/]+/previews/components/[^/]+$")),
    ("POST", re.compile(r"^/v1/new-fgui-projects/[^/]+/adjustments$")),
    ("POST", re.compile(r"^/v1/new-fgui-projects/[^/]+/regenerate$")),
    ("POST", re.compile(r"^/v1/new-fgui-projects/[^/]+/approve$")),
    ("POST", re.compile(r"^/v1/new-fgui-projects/[^/]+/reject$")),
    ("POST", re.compile(r"^/v1/figma/selections/[^/]+/projects/[^/]+/jobs$")),
    ("POST", re.compile(r"^/v1/projects/uploads$")),
    ("GET", re.compile(r"^/v1/figma/project-options$")),
    ("POST", re.compile(r"^/v1/projects/from-template$")),
    ("GET", re.compile(r"^/v1/projects/[^/]+$")),
    ("GET", re.compile(r"^/v1/projects/[^/]+/packages$")),
    ("GET", re.compile(r"^/v1/projects/[^/]+/assets/[^/]+/thumbnail$")),
    ("GET", re.compile(r"^/v1/projects/[^/]+/hifi-targets$")),
    ("POST", re.compile(r"^/v1/hifi-sources/psd/inspect$")),
    ("POST", re.compile(r"^/v1/hifi-sources/psd$")),
    ("GET", re.compile(r"^/v1/hifi-sources/psd/[0-9a-f]{64}$")),
    ("GET", re.compile(r"^/v1/hifi-sources/psd/[0-9a-f]{64}/composite$")),
    ("GET", re.compile(r"^/v1/hifi-sources/fonts$")),
    ("POST", re.compile(r"^/v1/hifi-replacements$")),
    ("POST", re.compile(r"^/v1/hifi-replacements/from-psd$")),
    ("POST", re.compile(r"^/v1/hifi-replacements/from-psd-batch$")),
    ("GET", re.compile(r"^/v1/hifi-replacements/[^/]+$")),
    ("GET", re.compile(r"^/v1/hifi-replacements/[^/]+/mapping$")),
    ("POST", re.compile(r"^/v1/hifi-replacements/[^/]+/mapping-decisions$")),
    ("POST", re.compile(r"^/v1/hifi-replacements/[^/]+/build$")),
    ("GET", re.compile(r"^/v1/hifi-replacements/[^/]+/review$")),
    ("GET", re.compile(r"^/v1/hifi-replacements/[^/]+/candidate/download$")),
    ("POST", re.compile(r"^/v1/hifi-replacements/[^/]+/approve$")),
    ("POST", re.compile(r"^/v1/hifi-replacements/[^/]+/reject$")),
    ("GET", re.compile(r"^/v1/hifi-replacements/[^/]+/download$")),
    ("GET", re.compile(r"^/v1/jobs/[^/]+$")),
    ("POST", re.compile(r"^/v1/jobs/[^/]+/package$")),
    ("GET", re.compile(r"^/v1/jobs/[^/]+/package$")),
    ("GET", re.compile(r"^/v1/jobs/[^/]+/package/download$")),
    ("POST", re.compile(r"^/v1/jobs/[^/]+/semantic-screenshot-consent$")),
    ("POST", re.compile(r"^/v1/jobs/[^/]+/semantic-screenshot$")),
)


@dataclass(frozen=True)
class _ConversionContext:
    raw: dict[str, object]
    project_id: str
    package_name: str
    project_root: Path
    project_fingerprint: str | None
    package_names: tuple[str, ...]
    selection_assets: tuple[SelectionAsset, ...]


class _CapturingSemanticAnalyzer:
    def __init__(self, analyzer: SemanticAnalyzer) -> None:
        self._analyzer = analyzer
        self.outcome: SemanticAnalysisOutcome | None = None

    def analyze(
        self,
        roots: tuple[NormalizedNode, ...],
        *,
        rule_candidates: tuple[ClassificationDecision, ...],
        screenshot: bytes | None = None,
    ) -> SemanticAnalysisOutcome:
        outcome = self._analyzer.analyze(
            roots,
            rule_candidates=rule_candidates,
            screenshot=screenshot,
        )
        if isinstance(outcome, SemanticAnalysisOutcome):
            self.outcome = outcome
        return outcome


def _is_plugin_route(method: str, path: str) -> bool:
    return any(
        method == allowed and pattern.fullmatch(path) for allowed, pattern in _PLUGIN_ACCESS_ROUTES
    )


class _ApiAccessMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        gateway_secret: bytes | None,
        plugin_access: PluginAccess | None,
        plugin_origins: tuple[str, ...],
    ) -> None:
        self.app = app
        self.gateway_secret = gateway_secret
        self.plugin_access = plugin_access
        self.plugin_origins = plugin_origins

    def _has_gateway_access(self, scope: Scope) -> bool:
        if self.gateway_secret is None:
            return False
        tokens = [
            value for name, value in scope["headers"] if name.lower() == b"x-figma-gateway-token"
        ]
        return len(tokens) == 1 and hmac.compare_digest(tokens[0], self.gateway_secret)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or not scope["path"].startswith("/v1/")
            or scope["method"] == "OPTIONS"
        ):
            await self.app(scope, receive, send)
            return
        if self.plugin_access is not None and _is_plugin_route(scope["method"], scope["path"]):
            try:
                self.plugin_access.require(Request(scope))
            except HTTPException as error:
                if not self._has_gateway_access(scope):
                    origin = dict(scope["headers"]).get(b"origin", b"").decode("latin-1")
                    headers = (
                        {"Access-Control-Allow-Origin": origin, "Vary": "Origin"}
                        if origin in self.plugin_origins
                        else None
                    )
                    await JSONResponse(
                        {"detail": error.detail}, status_code=error.status_code, headers=headers
                    )(scope, receive, send)
                    return
        elif self.gateway_secret is not None and not self._has_gateway_access(scope):
            await JSONResponse({"detail": "Unauthorized"}, status_code=401)(scope, receive, send)
            return
        await self.app(scope, receive, send)


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


async def _strict_json_body(request: Request, model: type[_StrictPayload]) -> _StrictPayload:
    max_bytes = 16 * 1024
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > max_bytes:
                raise _error(
                    413,
                    "new_project_request_too_large",
                    "The new-project request is too large.",
                )
        except ValueError:
            raise _error(422, "invalid_new_project_request", "The new-project request is invalid.") from None
    validated: _StrictPayload | None = None
    invalid = False
    try:
        content = bytearray()
        async for chunk in request.stream():
            remaining = max_bytes - len(content)
            if len(chunk) > remaining:
                raise _error(
                    413,
                    "new_project_request_too_large",
                    "The new-project request is too large.",
                )
            content.extend(chunk)
        raw = bytes(content)
        payload = json.loads(raw, object_pairs_hook=_reject_duplicate_pairs)
        if not isinstance(payload, dict):
            raise TypeError("JSON object required")
        validated = model.model_validate_json(raw)
    except (TypeError, UnicodeDecodeError, ValueError, ValidationError, json.JSONDecodeError):
        invalid = True
    if invalid or validated is None:
        raise _error(
            422,
            "invalid_new_project_request",
            "The new-project request is invalid.",
        )
    return validated


def _verified_new_project_artifact(project: StoredNewProject, artifacts_root: Path) -> Path:
    path = project.artifact_path
    view = project.view
    if path is None or view.download_name is None or view.sha256 is None or view.byte_size is None:
        raise OSError("artifact metadata is incomplete")
    root = Path(os.path.abspath(artifacts_root))
    expected_directory = root / project.view.build_id
    expected = expected_directory / "artifact.zip"
    if Path(os.path.abspath(path)) != expected:
        raise OSError("artifact path is outside its candidate directory")
    for candidate, must_be_directory in (
        (root, True),
        (expected_directory, True),
        (expected, False),
    ):
        metadata = candidate.lstat()
        if candidate.is_symlink() or (getattr(metadata, "st_file_attributes", 0) & _REPARSE_POINT):
            raise OSError("artifact path contains a link or reparse point")
        if must_be_directory != stat.S_ISDIR(metadata.st_mode) and (
            must_be_directory or not stat.S_ISREG(metadata.st_mode)
        ):
            raise OSError("artifact path has the wrong file type")
    before_path = expected.lstat()
    digest = hashlib.sha256()
    size = 0
    with expected.open("rb") as source:
        before_handle = os.fstat(source.fileno())
        if not stat.S_ISREG(before_handle.st_mode) or (
            before_handle.st_dev,
            before_handle.st_ino,
        ) != (before_path.st_dev, before_path.st_ino):
            raise OSError("artifact identity changed before reading")
        while chunk := source.read(64 * 1024):
            size += len(chunk)
            if size > view.byte_size:
                raise OSError("artifact size changed")
            digest.update(chunk)
        after_handle = os.fstat(source.fileno())
    after_path = expected.lstat()
    identities = {
        (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns)
        for item in (before_path, before_handle, after_handle, after_path)
    }
    if (
        len(identities) != 1
        or size != view.byte_size
        or digest.hexdigest() != view.sha256
        or expected.is_symlink()
        or (getattr(after_path, "st_file_attributes", 0) & _REPARSE_POINT)
    ):
        raise OSError("artifact identity or content changed")
    return expected


def create_app(
    data_dir: Path,
    fixtures_root: Path,
    rules_path: Path,
    web_dist: Path | None = None,
    health_instance_token: str | None = None,
    plugin_secret: bytes | None = None,
    plugin_access_token: bytes | None = None,
    plugin_origins: tuple[str, ...] = ("null",),
    gateway_secret: bytes | None = None,
    public_origin: str | None = None,
    allow_fixture_jobs: bool = False,
    templates_root: Path | None = None,
    package_clock: Callable[[], datetime] | None = None,
    package_lease_duration: timedelta = _PACKAGE_LEASE_DURATION,
    package_owner_id: str | None = None,
    semantic_analyzer: SemanticAnalyzer | None = None,
    local_app_access_token: bytes | None = None,
    fixed_font_specs: tuple[FixedFontSpec, ...] = PROJECT_FIXED_FONTS,
    fixed_font_search_roots: tuple[Path, ...] | None = None,
) -> FastAPI:
    index_html: Path | None = None
    assets_dir: Path | None = None
    if web_dist is not None:
        index_html = web_dist / "index.html"
        assets_dir = web_dist / "assets"
        if not web_dist.is_dir() or not index_html.is_file() or not assets_dir.is_dir():
            raise ValueError("web_dist must be a directory containing index.html")
    package_owner_id = package_owner_id or uuid.uuid4().hex
    store = JobStore(
        data_dir / "server.db",
        clock=package_clock or _utc_now,
        package_lease_duration=package_lease_duration,
    )
    store.initialize()
    store.recover_expired_packages()
    store.recover_expired_new_projects()
    store.cleanup_terminal_screenshot_paths()
    _sweep_semantic_screenshot_orphans(
        data_dir / "semantic-screenshots", store.list_screenshot_paths()
    )
    artifacts = ArtifactStore(data_dir / "artifacts")
    project_store = ProjectStore(data_dir)
    template_catalog = TemplateCatalog(templates_root)
    selection_store = SelectionStore(data_dir)
    hifi_replacement_store = HifiReplacementStore(data_dir)
    psd_source_store = PsdSourceStore(data_dir)
    hifi_replacement_workflow = HifiReplacementWorkflow(
        data_dir,
        project_store,
        selection_store,
        psd_source_store,
        hifi_replacement_store,
    )
    new_project_artifacts = data_dir / "new-fgui-projects" / "artifacts"
    for stored_project in store.list_new_project_artifacts():
        try:
            _verified_new_project_artifact(stored_project, new_project_artifacts)
        except OSError:
            store.invalidate_new_project_artifact(stored_project.view.build_id)
    pairing_store = (
        PairingStore(data_dir / "server.db", plugin_secret, lambda: datetime.now(UTC))
        if plugin_secret is not None
        else None
    )
    if pairing_store is not None:
        pairing_store.initialize()
    plugin_access = PluginAccess(plugin_access_token) if plugin_access_token is not None else None
    if local_app_access_token is not None and (
        plugin_access_token is None
        or not hmac.compare_digest(local_app_access_token, plugin_access_token)
    ):
        raise ValueError("local app access token must match plugin access token")
    app = FastAPI(title="Figma to FGUI Local Service", version="0.1.0")
    if plugin_access is not None:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(plugin_origins),
            allow_methods=["GET", "POST", "PUT", "OPTIONS"],
            allow_headers=["content-type", "x-figma-plugin-token", "x-idempotency-key"],
            expose_headers=["content-disposition", "content-length"],
            max_age=600,
        )
    elif public_origin is not None:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[public_origin],
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
            allow_headers=["authorization", "content-type", "x-figma-console-session"],
            max_age=600,
        )
    if gateway_secret is not None or plugin_access is not None:
        app.add_middleware(
            _ApiAccessMiddleware,
            gateway_secret=gateway_secret,
            plugin_access=plugin_access,
            plugin_origins=plugin_origins,
        )

    def pairing_error(error: PairingError) -> HTTPException:
        status = 429 if error.code == "pairing_rate_limited" else 401
        if error.code.startswith("pairing_code_"):
            status = 400
        return _error(status, error.code, _PAIRING_MESSAGE)

    def selection_error(error: SelectionError) -> HTTPException:
        status = 400
        if error.code in {"selection_not_found", "selection_owner_denied"}:
            status = 404
        elif error.code in {"selection_upload_state", "selection_resource_duplicate"}:
            status = 409
        elif error.code == "selection_upload_expired":
            status = 410
        code = "selection_not_found" if error.code == "selection_owner_denied" else error.code
        return _error(status, code, _SELECTION_MESSAGE)

    def configured_pairing_store() -> PairingStore:
        if pairing_store is None:
            raise _error(503, "plugin_credential_invalid", _PAIRING_MESSAGE)
        return pairing_store

    def authenticate_plugin(
        authorization: str | None, *, required_scope: PluginScope | str | None = None
    ) -> PluginPrincipal:
        if authorization is None:
            raise PairingError("plugin_credential_invalid")
        scheme, separator, credential = authorization.partition(" ")
        if scheme != "Bearer" or separator == "" or not credential or " " in credential:
            raise PairingError("plugin_credential_invalid")
        if pairing_store is None:
            raise PairingError("plugin_credential_invalid")
        principal = pairing_store.authenticate(credential)
        return principal if required_scope is None else require_scope(principal, required_scope)

    app.state.authenticate_plugin = authenticate_plugin
    app.state.data_dir = data_dir
    app.state.package_owner_id = package_owner_id
    app.state.job_store = store
    app.state.selection_store = selection_store
    app.state.hifi_replacement_store = hifi_replacement_store
    app.state.hifi_replacement_workflow = hifi_replacement_workflow

    def selection_view(selection_id: str, device_id: str) -> SelectionView:
        version = selection_store.get(selection_id, device_id)
        return SelectionView(
            selection_id=version.selection_id,
            display_name=version.manifest.display_name,
            top_level_summaries=tuple(
                SelectionTopLevelSummary(name=node.name, type=node.type)
                for node in version.manifest.top_level_nodes
            ),
            preview_urls=tuple(
                f"/v1/figma/selections/{version.selection_id}/previews/{index}"
                for index in range(version.preview_count)
            ),
            warnings=version.manifest.warnings,
        )

    def selection_principal(request: Request, scope: PluginScope) -> PluginPrincipal:
        try:
            principal = authenticate_plugin(
                request.headers.get("authorization"), required_scope=scope
            )
            selection_store.expire_uploads()
            return principal
        except PairingError as error:
            raise pairing_error(error) from error

    def plugin_device(request: Request, scope: PluginScope) -> str:
        if plugin_access is None:
            return selection_principal(request, scope).device_id
        selection_store.expire_uploads()
        return _BUNDLED_PLUGIN_DEVICE_ID

    def console_device(request: Request) -> str:
        try:
            return configured_pairing_store().console_device_id(
                request.headers.get("x-figma-console-session", "")
            )
        except PairingError as error:
            raise pairing_error(error) from error

    def console_selection_view(device_id: str) -> SelectionView | None:
        selection = selection_store.latest_for_device(device_id)
        if selection is None:
            return None
        view = selection_view(selection.selection_id, device_id)
        return view.model_copy(
            update={
                "preview_urls": tuple(
                    f"/v1/figma/pairings/current/selections/{selection.selection_id}/previews/{index}"
                    for index in range(selection.preview_count)
                )
            }
        )

    def load_job(job_id: str) -> JobView:
        try:
            return store.get_job(job_id)
        except NotFound as error:
            raise _error(404, error.code, str(error)) from error

    def load_bundle(job_id: str) -> ChangeBundle:
        job = load_job(job_id)
        if job.artifact_sha256 is None:
            raise _error(409, "artifact_unavailable", "job has no applicable changeset")
        return load_bundle_artifact(job.artifact_sha256)

    def load_bundle_artifact(artifact_sha256: str) -> ChangeBundle:
        try:
            return artifacts.get(artifact_sha256)
        except (ArtifactIntegrityError, OSError) as error:
            raise _error(409, "artifact_integrity", "changeset artifact is unavailable") from error

    def preview_root(job: JobView) -> Path:
        if job.project_fingerprint is None:
            return fixtures_root / "fgui"
        try:
            return project_store.artifact_path(job.project_id)
        except ProjectIntegrityError as error:
            raise _error(404, "project_not_found", _PROJECT_NOT_FOUND_MESSAGE) from error

    def preview_image(
        job: JobView, bundle: ChangeBundle, change_index: int, side: Literal["before", "after"]
    ) -> bytes | None:
        if change_index < 0 or change_index >= len(bundle.files):
            return None
        change = bundle.files[change_index]
        if not is_designer_image(change.relative_path):
            return None
        if side == "before":
            try:
                content = (preview_root(job) / change.relative_path).read_bytes()
            except OSError:
                return None
        else:
            try:
                content = base64.b64decode(change.content_b64, validate=True)
            except ValueError:
                return None
        rendered = encode_webp_preview(content)
        return None if rendered is None else rendered[2]

    def job_summary(job: JobView) -> JobSummary:
        return JobSummary(job_id=job.job_id, project_id=job.project_id, status=job.status)

    def redacted_bundle(job_id: str, response: Response, migration_link: str) -> ChangeBundle:
        job = load_job(job_id)
        response.headers["Deprecation"] = "true"
        response.headers["Link"] = f'<{migration_link}>; rel="successor-version"'
        return ChangeBundle(job_id=job.job_id, project_id=job.project_id, files=())

    def project_view(version: UploadedProjectVersion) -> ProjectUploadView:
        return ProjectUploadView(
            project_id=version.project_id,
            display_name=version.original_name,
            packages=tuple(
                PackageView(
                    name=package.name,
                    resource_count=sum(file.package_name == package.name for file in version.files),
                )
                for package in version.packages
            ),
        )

    def load_uploaded_project(project_id: str) -> UploadedProjectVersion:
        try:
            return project_store.get(project_id)
        except ProjectIntegrityError as error:
            raise _error(404, "project_not_found", _PROJECT_NOT_FOUND_MESSAGE) from error

    def hifi_error(error: HifiReplacementStoreError) -> HTTPException:
        status = 404 if error.code == "hifi_replacement_not_found" else 409
        return _error(status, error.code, "HIFI replacement request could not be completed.")

    def load_hifi_replacement(
        replacement_id: str, request: Request
    ) -> StoredHifiReplacement:
        owner = plugin_device(request, PluginScope.SELECTION_READ_OWN_STATUS)
        try:
            return hifi_replacement_store.get(replacement_id, owner)
        except HifiReplacementStoreError as error:
            raise hifi_error(error) from error

    def authorize_job_access(job_id: str, request: Request) -> JobView:
        job = load_job(job_id)
        console_session = request.headers.get("x-figma-console-session")
        if console_session:
            device_id = console_device(request)
        elif gateway_secret is not None and hmac.compare_digest(
            request.headers.get("x-figma-gateway-token", "").encode(), gateway_secret
        ):
            return job
        elif plugin_access is not None:
            device_id = _BUNDLED_PLUGIN_DEVICE_ID
        else:
            device_id = selection_principal(
                request, PluginScope.SELECTION_READ_OWN_STATUS
            ).device_id
        try:
            selection_id = store.get_job_selection_id(job_id)
            if selection_id is None:
                raise SelectionError("selection_not_found")
            selection_store.get(selection_id, device_id)
        except (NotFound, SelectionError) as error:
            raise _error(404, "not_found", "resource not found") from error
        return job

    @app.get("/health")
    def health(response: Response) -> dict[str, str]:
        if health_instance_token is not None:
            response.headers["X-Figma-To-FGUI-Instance"] = health_instance_token
        return {"status": "ok"}

    @app.get("/v1/local/bootstrap")
    def local_app_bootstrap(response: Response) -> dict[str, str | int]:
        if local_app_access_token is None:
            raise _error(404, "not_found", "resource not found")
        try:
            token = local_app_access_token.decode("ascii")
        except UnicodeDecodeError as error:
            raise _error(503, "local_app_unavailable", "Local app is unavailable.") from error
        response.headers["Cache-Control"] = "no-store"
        return {"version": 1, "access_token": token}

    @app.get("/v1/hifi-sources/fonts")
    def fixed_font_status() -> dict[str, object]:
        statuses = check_fixed_fonts(
            fixed_font_specs,
            fixed_font_search_roots
            if fixed_font_search_roots is not None
            else windows_font_roots(),
        )
        return {
            "version": 1,
            "fonts": [asdict(status) for status in statuses],
        }

    @app.post("/v1/figma/pairings", status_code=201)
    def create_pairing(request: Request) -> PairingCodeView:
        try:
            client_address = request.client
            source_key = (
                client_address.host
                if client_address is not None and client_address.host
                else "unknown"
            )
            return configured_pairing_store().create_code(source_key=source_key)
        except PairingError as error:
            raise pairing_error(error) from error

    @app.get("/v1/figma/pairings/status")
    def console_pairing_status(request: Request) -> ConsolePairingStatusView:
        try:
            return configured_pairing_store().console_status(
                request.headers.get("x-figma-console-session", "")
            )
        except PairingError as error:
            raise pairing_error(error) from error

    @app.delete("/v1/figma/pairings/current", status_code=204)
    def cancel_console_pairing(request: Request) -> Response:
        try:
            configured_pairing_store().cancel_console_pairing(
                request.headers.get("x-figma-console-session", "")
            )
        except PairingError as error:
            raise pairing_error(error) from error
        return Response(status_code=204)

    @app.get("/v1/figma/pairings/current/selection", response_model=None)
    def current_console_selection(request: Request) -> SelectionView | Response:
        device_id = console_device(request)
        try:
            selection = console_selection_view(device_id)
            return Response(status_code=204) if selection is None else selection
        except SelectionError as error:
            raise selection_error(error) from error

    @app.get("/v1/figma/pairings/current/selections/{selection_id}/previews/{preview_index}")
    def current_console_selection_preview(
        selection_id: str, preview_index: int, request: Request
    ) -> FileResponse:
        device_id = console_device(request)
        try:
            selection_store.get(selection_id, device_id)
            return FileResponse(
                selection_store.preview_path(selection_id, device_id, preview_index),
                media_type="image/webp",
            )
        except SelectionError as error:
            raise selection_error(error) from error

    @app.post("/v1/figma/pairings/exchange")
    async def exchange_pairing(request: Request) -> PluginCredentialView:
        try:
            payload = await request.json()
            if not isinstance(payload, dict):
                raise TypeError("pairing exchange must be an object")
            if type(payload.get("version")) is not int or payload["version"] != 1:
                raise TypeError("pairing exchange version is invalid")
            exchange = PairingExchange.model_validate(payload)
        except (TypeError, ValidationError, ValueError):
            raise _error(400, "pairing_code_invalid", _PAIRING_MESSAGE) from None
        try:
            client_address = request.client
            source_key = (
                client_address.host
                if client_address is not None and client_address.host
                else "unknown"
            )
            return configured_pairing_store().exchange(
                exchange.code, exchange.device_name, source_key=source_key
            )
        except PairingError as error:
            raise pairing_error(error) from error

    @app.get("/v1/figma/devices")
    def list_figma_devices(request: Request) -> tuple[FigmaDeviceView, ...]:
        try:
            return configured_pairing_store().console_devices(
                request.headers.get("x-figma-console-session", "")
            )
        except PairingError as error:
            raise pairing_error(error) from error

    @app.delete("/v1/figma/devices/{device_id}")
    def revoke_figma_device(device_id: str, request: Request) -> FigmaDeviceView:
        try:
            return configured_pairing_store().revoke_console_device(
                request.headers.get("x-figma-console-session", ""), device_id
            )
        except PairingError as error:
            raise pairing_error(error) from error

    @app.post("/v1/figma/selections/uploads", status_code=201)
    async def create_selection_upload(request: Request) -> dict[str, str | int]:
        device_id = plugin_device(request, PluginScope.SELECTION_UPLOAD)
        try:
            payload = await request.json()
            if (
                not isinstance(payload, dict)
                or set(payload) != {"version", "idempotency_key"}
                or payload["version"] != 1
                or not isinstance(payload["idempotency_key"], str)
            ):
                raise ValueError("invalid upload request")
            upload = selection_store.create_upload(device_id, payload["idempotency_key"])
        except (SelectionError, TypeError, ValueError):
            error = SelectionError("invalid_selection_upload")
            raise selection_error(error) from None
        return {"version": 1, "upload_id": upload.upload_id}

    @app.put("/v1/figma/selections/uploads/{upload_id}/manifest")
    async def put_selection_manifest(upload_id: str, request: Request) -> dict[str, str | int]:
        device_id = plugin_device(request, PluginScope.SELECTION_UPLOAD)
        try:
            content = bytearray()
            async for received in request.stream():
                content.extend(received)
                if len(content) > _SELECTION_MANIFEST_BYTES:
                    raise SelectionError("selection_too_large")

            def no_duplicate_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
                document: dict[str, object] = {}
                for key, value in pairs:
                    if key in document:
                        raise SelectionError("invalid_selection_manifest")
                    document[key] = value
                return document

            payload = json.loads(content, object_pairs_hook=no_duplicate_object)
            if not isinstance(payload, dict):
                raise TypeError("manifest must be an object")
            manifest = SelectionManifest.model_validate(payload)
            upload = selection_store.put_manifest(upload_id, device_id, manifest)
        except (RecursionError, SelectionError, TypeError, ValidationError, ValueError) as error:
            if isinstance(error, ValidationError):
                safe_errors = [
                    {
                        "loc": ".".join(str(part) for part in item["loc"]),
                        "type": item["type"],
                    }
                    for item in error.errors(include_url=False, include_context=False, include_input=False)[:20]
                ]
                logger.warning("Selection manifest schema rejected upload=%s errors=%s", upload_id, safe_errors)
            else:
                logger.warning(
                    "Selection manifest rejected upload=%s error_type=%s code=%s",
                    upload_id,
                    type(error).__name__,
                    error.code if isinstance(error, SelectionError) else "invalid_selection_manifest",
                )
            selection = (
                error
                if isinstance(error, SelectionError)
                else SelectionError("invalid_selection_manifest")
            )
            raise selection_error(selection) from None
        return {"version": 1, "state": upload.state}

    @app.put("/v1/figma/selections/uploads/{upload_id}/resources/{resource_key}")
    async def put_selection_resource(
        upload_id: str, resource_key: str, request: Request
    ) -> dict[str, str | int]:
        device_id = plugin_device(request, PluginScope.SELECTION_UPLOAD)
        try:
            temporary_path = selection_store.prepare_resource(
                upload_id, device_id, resource_key, request.headers.get("content-type", "")
            )
            written = 0
            try:
                with temporary_path.open("wb") as destination:
                    async for received in request.stream():
                        for offset in range(0, len(received), _UPLOAD_CHUNK_BYTES):
                            chunk = received[offset : offset + _UPLOAD_CHUNK_BYTES]
                            written += len(chunk)
                            if written > selection_store.max_resource_bytes:
                                raise SelectionError("selection_too_large")
                            destination.write(chunk)
                upload = selection_store.put_resource_path(
                    upload_id,
                    device_id,
                    resource_key,
                    request.headers.get("content-type", ""),
                    temporary_path,
                )
            finally:
                with suppress(OSError):
                    temporary_path.unlink(missing_ok=True)
        except SelectionError as error:
            raise selection_error(error) from None
        return {"version": 1, "state": upload.state}

    @app.post("/v1/figma/selections/uploads/{upload_id}/commit")
    def commit_selection_upload(upload_id: str, request: Request) -> SelectionView:
        device_id = plugin_device(request, PluginScope.SELECTION_UPLOAD)
        try:
            version = selection_store.commit(upload_id, device_id)
            return selection_view(version.selection_id, device_id)
        except SelectionError as error:
            raise selection_error(error) from None

    @app.get("/v1/figma/selections/{selection_id}")
    def get_selection(selection_id: str, request: Request) -> SelectionView:
        device_id = plugin_device(request, PluginScope.SELECTION_READ_OWN_STATUS)
        try:
            return selection_view(selection_id, device_id)
        except SelectionError as error:
            raise selection_error(error) from None

    @app.get("/v1/figma/selections/{selection_id}/previews/{preview_index}")
    def get_selection_preview(
        selection_id: str, preview_index: int, request: Request
    ) -> FileResponse:
        device_id = plugin_device(request, PluginScope.SELECTION_READ_OWN_STATUS)
        try:
            return FileResponse(
                selection_store.preview_path(selection_id, device_id, preview_index),
                media_type="image/webp",
            )
        except SelectionError as error:
            raise selection_error(error) from None

    @app.get("/v1/figma/selections/{selection_id}/resources/{resource_key}")
    def get_selection_resource(selection_id: str, resource_key: str, request: Request) -> FileResponse:
        device_id = plugin_device(request, PluginScope.SELECTION_READ_OWN_STATUS)
        try:
            path, mime_type = selection_store.resource_path(selection_id, device_id, resource_key)
            return FileResponse(path, media_type=mime_type)
        except SelectionError as error:
            raise selection_error(error) from None

    def load_new_project(build_id: str, device_id: str) -> StoredNewProject:
        failure: Literal["missing", "state"] | None = None
        try:
            return store.get_new_project(build_id, device_id)
        except NotFound:
            failure = "missing"
        except StoreError:
            failure = "state"
        if failure == "missing":
            raise _error(
                404,
                "new_project_not_found",
                "The new-project candidate was not found.",
            )
        raise _error(
            409,
            "new_project_state_conflict",
            "The new-project candidate state changed.",
        )

    def require_current_artifact(project: StoredNewProject) -> Path:
        artifact: Path | None = None
        try:
            artifact = _verified_new_project_artifact(project, new_project_artifacts)
        except OSError:
            with suppress(StoreError):
                store.invalidate_new_project_artifact(project.view.build_id)
        if artifact is None:
            raise _error(
                409,
                "new_project_artifact_invalid",
                "The generated archive is no longer available.",
            )
        return artifact

    def workflow_failure_diagnostics(
        error: NewProjectWorkflowError | None,
    ) -> tuple[Diagnostic, ...]:
        allowed = {
            "fgui.component.definition_missing": (
                "A selected component needs a complete generated definition.",
                "Add the component definition or use an approved raster fallback.",
            ),
            "fgui.writer.workflow.mapping_conflict": (
                "The selected component mapping is ambiguous or conflicted.",
                "Resolve the component mapping and retry.",
            ),
            "fgui.writer.workflow.resource_mismatch": (
                "The committed selection resources no longer match the conversion plan.",
                "Re-export the selection resources and retry.",
            ),
            "fgui.writer.workflow.output_failed": (
                "The new-project archive could not be published.",
                "Retry the build.",
            ),
            "fgui.writer.workflow.validation_failed": (
                "The converted selection did not pass validation.",
                "Repair the selection and retry.",
            ),
            "fgui.writer.workflow.build_failed": (
                "The new-project writer could not produce a validated archive.",
                "Repair the selection and retry.",
            ),
            "fgui.writer.workflow.conversion_failed": (
                "The committed selection could not be converted.",
                "Review the selection and retry.",
            ),
        }
        codes: set[str] = set()
        if type(error) is NewProjectWorkflowError:
            try:
                codes = {
                    diagnostic.code
                    for diagnostic in error.diagnostics
                    if type(diagnostic) is Diagnostic and diagnostic.code in allowed
                }
            except Exception:  # noqa: BLE001 - hostile public exceptions fail closed.
                codes = set()
        if not codes:
            codes = {"fgui.writer.workflow.build_failed"}
        return tuple(
            Diagnostic(
                code=code,
                severity=Severity.ERROR,
                message=allowed[code][0],
                rule_id=code,
                rule_version=1,
                evidence=(f"workflow.code={code}",),
                suggested_action=allowed[code][1],
                blocks_binding=True,
            )
            for code in sorted(codes)
        )

    def selection_review_diagnostics(selection_id: str, device_id: str) -> tuple[Diagnostic, ...]:
        version = selection_store.get(selection_id, device_id)
        return tuple(
            Diagnostic(
                code="fgui.writer.review.selection_warning",
                severity=Severity.WARNING,
                message="The source selection reported a warning.",
                rule_id="fgui.writer.review.selection_warning",
                rule_version=1,
                evidence=(f"selection.warning={index}",),
                suggested_action="Review the generated candidate before approval.",
            )
            for index, _warning in enumerate(version.manifest.warnings)
        )

    def build_new_project_candidate(
        project: StoredNewProject, device_id: str
    ) -> NewFguiProjectView:
        output = new_project_artifacts / project.view.build_id
        failure: NewProjectWorkflowError | None = None
        built = None
        selection = None
        try:
            selection = selection_store.get(project.selection_id, device_id)
            if selection.fingerprint != project.selection_fingerprint:
                raise SelectionError("selection_not_found")
            selection_root = selection_store.artifact_path(project.selection_id)
            resources_root = (
                selection_root / "resources" if selection.manifest.resources else selection_root
            )
            heartbeat_stop = Event()

            def heartbeat() -> None:
                while not heartbeat_stop.wait(store.package_heartbeat_interval):
                    try:
                        store.renew_new_project_lease(
                            build_id=project.view.build_id,
                            owner_device_id=device_id,
                            lease_owner=package_owner_id,
                        )
                    except StoreError:
                        return

            heartbeat_thread = Thread(
                target=heartbeat,
                name=f"new-project-heartbeat-{project.view.build_id[:8]}",
                daemon=True,
            )
            heartbeat_thread.start()
            try:
                def persist_stage(stage: str, progress: int) -> None:
                    store.advance_new_project_stage(
                        build_id=project.view.build_id,
                        owner_device_id=device_id,
                        lease_owner=package_owner_id,
                        stage=NewFguiProjectStage(stage),
                        progress=progress,
                    )

                built = build_selection_new_project(
                    manifest=selection.manifest,
                    resources_root=resources_root,
                    selection_fingerprint=selection.fingerprint,
                    project_name=project.project_name,
                    output_directory=output,
                    mapping_catalog_path=rules_path.parent / "component-mapping-candidates.json",
                    adjustments=tuple(
                        NewProjectAdjustment.model_validate_json(
                            json.dumps(item, sort_keys=True, separators=(",", ":"))
                        )
                        for item in project.adjustments
                    ),
                    on_stage=persist_stage,
                )
            finally:
                heartbeat_stop.set()
                heartbeat_thread.join()
            store.renew_new_project_lease(
                build_id=project.view.build_id,
                owner_device_id=device_id,
                lease_owner=package_owner_id,
            )
        except NewProjectWorkflowError as error:
            failure = error
        except Exception:
            logger.exception(
                "New-project writer build failed for build_id=%s selection_id=%s",
                project.view.build_id,
                project.selection_id,
            )
            failure = None
        if built is None or selection is None or built.plan is None:
            if selection is not None and failure is not None:
                blocked_dispositions = build_blocked_dispositions(
                    selection.manifest, failure.diagnostics
                )
                if blocked_dispositions:
                    review = build_blocked_new_project_designer_review(
                        build_id=project.view.build_id,
                        generation=project.generation,
                        dispositions=blocked_dispositions,
                    )
                    view = NewFguiProjectView(
                        build_id=project.view.build_id,
                        generation=project.generation,
                        status="awaiting_review",
                        stage="awaiting_review",
                        progress=100,
                        artifact_ready=False,
                    )
                    try:
                        completed = store.complete_new_project_analysis(
                            build_id=project.view.build_id,
                            owner_device_id=device_id,
                            lease_owner=package_owner_id,
                            view=view,
                            review=review,
                        )
                        with suppress(OSError):
                            shutil.rmtree(output)
                        return completed.view
                    except StoreError:
                        pass
            diagnostics = workflow_failure_diagnostics(failure)
            try:
                result = store.fail_new_project(
                    build_id=project.view.build_id,
                    owner_device_id=device_id,
                    lease_owner=package_owner_id,
                    diagnostics=diagnostics,
                )
            except StoreError:
                store.recover_expired_new_projects(project.view.build_id)
                result = load_new_project(project.view.build_id, device_id).view
            with suppress(OSError):
                shutil.rmtree(output)
            return result

        target = output / "artifact.zip"
        try:
            os.replace(io_path(built.path), io_path(target))
            review = build_new_project_designer_review(
                built.manifest,
                built.plan,
                (*built.diagnostics, *selection_review_diagnostics(project.selection_id, device_id)),
                build_id=project.view.build_id,
                generation=project.generation,
                source_preview_urls_by_resource={
                    resource_id: f"/v1/figma/selections/{project.selection_id}/resources/{key}"
                    for resource_id, key in built.source_resource_keys.items()
                },
                source_node_ids=built.source_node_ids,
                dispositions=build_conversion_dispositions(
                    selection.manifest, built.plan, built.source_node_ids
                ),
            )
            view = NewFguiProjectView(
                build_id=project.view.build_id,
                generation=project.generation,
                status="awaiting_review",
                stage="awaiting_review",
                progress=100,
                download_name=built.download_name,
                sha256=built.sha256,
                byte_size=built.byte_size,
            )
            completed = store.complete_new_project(
                build_id=project.view.build_id,
                owner_device_id=device_id,
                lease_owner=package_owner_id,
                view=view,
                artifact_path=target,
                manifest=built.manifest,
                review=review,
            )
            require_current_artifact(completed)
            return completed.view
        except Exception:  # noqa: BLE001 - completion failures become a fresh public result.
            diagnostics = workflow_failure_diagnostics(None)
            with suppress(StoreError):
                store.fail_new_project(
                    build_id=project.view.build_id,
                    owner_device_id=device_id,
                    lease_owner=package_owner_id,
                    diagnostics=diagnostics,
                )
            with suppress(OSError):
                shutil.rmtree(output)
            return load_new_project(project.view.build_id, device_id).view

    def launch_new_project_candidate(project: StoredNewProject, device_id: str) -> None:
        Thread(
            target=build_new_project_candidate,
            args=(project, device_id),
            name=f"new-project-build-{project.view.build_id[:8]}",
            daemon=True,
        ).start()

    @app.post(
        "/v1/figma/selections/{selection_id}/new-fgui-projects",
        status_code=202,
    )
    async def start_new_fgui_project(selection_id: str, request: Request) -> NewFguiProjectView:
        device_id = plugin_device(request, PluginScope.SELECTION_READ_OWN_STATUS)
        payload = cast(
            NewFguiProjectRequest,
            await _strict_json_body(request, NewFguiProjectRequest),
        )
        try:
            selection = selection_store.get(selection_id, device_id)
        except SelectionError as error:
            raise selection_error(error) from None
        identity_payload = json.dumps(
            {
                "project_name": payload.project_name,
                "selection_fingerprint": selection.fingerprint,
                "version": payload.version,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        attempt = store.begin_new_project(
            build_id=uuid.uuid4().hex,
            owner_device_id=device_id,
            selection_id=selection.selection_id,
            selection_fingerprint=selection.fingerprint,
            request_identity=hashlib.sha256(identity_payload).hexdigest(),
            project_name=payload.project_name,
            lease_owner=package_owner_id,
        )
        if not attempt.should_build:
            return attempt.project.view
        launch_new_project_candidate(attempt.project, device_id)
        return attempt.project.view

    @app.get("/v1/new-fgui-projects/{build_id}")
    def get_new_fgui_project(build_id: str, request: Request) -> NewFguiProjectView:
        device_id = plugin_device(request, PluginScope.SELECTION_READ_OWN_STATUS)
        return load_new_project(build_id, device_id).view

    @app.get("/v1/new-fgui-projects/{build_id}/review")
    def get_new_fgui_project_review(build_id: str, request: Request) -> NewProjectDesignerReview:
        device_id = plugin_device(request, PluginScope.SELECTION_READ_OWN_STATUS)
        project = load_new_project(build_id, device_id)
        if project.superseded_by is not None or project.review is None:
            raise _error(
                409,
                "new_project_review_unavailable",
                "The candidate review is unavailable.",
            )
        if project.view.artifact_ready:
            require_current_artifact(project)
        return project.review

    @app.get("/v1/new-fgui-projects/{build_id}/previews/resources/{resource_id}")
    def get_new_fgui_project_resource_preview(
        build_id: str, resource_id: str, request: Request
    ) -> Response:
        device_id = plugin_device(request, PluginScope.SELECTION_READ_OWN_STATUS)
        project = load_new_project(build_id, device_id)
        if project.superseded_by is not None or project.manifest is None or project.review is None:
            raise _error(404, "new_project_preview_not_found", "Preview not found.")
        resource = next(
            (item for item in project.manifest.resources if item.id == resource_id), None
        )
        if resource is None:
            raise _error(404, "new_project_preview_not_found", "Preview not found.")
        artifact = require_current_artifact(project)
        try:
            with ZipFile(artifact) as archive:
                matches = [
                    name
                    for name in archive.namelist()
                    if name == resource.relative_path or name.endswith("/" + resource.relative_path)
                ]
                if len(matches) != 1:
                    raise BadZipFile("resource member is not unique")
                content = archive.read(matches[0])
        except (BadZipFile, KeyError, OSError):
            store.invalidate_new_project_artifact(build_id)
            raise _error(409, "new_project_artifact_invalid", "Preview not available.") from None
        return Response(content=content, media_type=resource.mime_type)

    @app.post("/v1/new-fgui-projects/{build_id}/adjustments")
    async def set_new_fgui_project_adjustment(
        build_id: str, request: Request
    ) -> NewFguiProjectView:
        device_id = plugin_device(request, PluginScope.SELECTION_READ_OWN_STATUS)
        payload = cast(
            NewProjectAdjustmentRequest,
            await _strict_json_body(request, NewProjectAdjustmentRequest),
        )
        project = load_new_project(build_id, device_id)
        if (
            payload.candidate_id != build_id
            or payload.generation != project.generation
            or project.review is None
            or project.superseded_by is not None
        ):
            raise _error(409, "new_project_adjustment_conflict", "Adjustment is stale.")
        check = next(
            (
                item
                for item in project.review.checks
                if item.issue_id == payload.issue_id and item.uir_node_id == payload.uir_node_id
            ),
            None,
        )
        strategy = NewProjectAdjustmentStrategy(payload.strategy)
        if check is None or not strategy_allowed_for_check(check, strategy):
            raise _error(
                409,
                "new_project_adjustment_conflict",
                "The requested adjustment does not match the review issue.",
            )
        require_current_artifact(project)
        if check.issue_kind is None or check.source_node_id is None:
            raise _error(
                409,
                "new_project_adjustment_conflict",
                "The review issue has no provable source adjustment.",
            )
        adjustment = NewProjectAdjustment(
            issueId=check.issue_id,
            sourceNodeId=check.source_node_id,
            issueKind=check.issue_kind,
            strategy=strategy,
        )
        try:
            adjusted = store.set_new_project_adjustment(
                build_id=build_id,
                owner_device_id=device_id,
                generation=payload.generation,
                adjustment=adjustment.model_dump(mode="json", by_alias=True),
            )
        except StoreError:
            raise _error(409, "new_project_state_conflict", "Candidate state changed.") from None
        return adjusted.view

    @app.post("/v1/new-fgui-projects/{build_id}/regenerate", status_code=202)
    async def regenerate_new_fgui_project(build_id: str, request: Request) -> NewFguiProjectView:
        device_id = plugin_device(request, PluginScope.SELECTION_READ_OWN_STATUS)
        payload = cast(
            NewProjectRegenerateRequest,
            await _strict_json_body(request, NewProjectRegenerateRequest),
        )
        old = load_new_project(build_id, device_id)
        if payload.generation != old.generation:
            raise _error(409, "new_project_generation_stale", "Candidate generation changed.")
        require_current_artifact(old)
        try:
            selection = selection_store.get(old.selection_id, device_id)
            if selection.fingerprint != old.selection_fingerprint:
                raise SelectionError("selection_not_found")
            regenerated = store.begin_new_project_regeneration(
                old_build_id=build_id,
                new_build_id=uuid.uuid4().hex,
                owner_device_id=device_id,
                generation=payload.generation,
                lease_owner=package_owner_id,
            )
        except (SelectionError, StoreError):
            raise _error(
                409, "new_project_regeneration_conflict", "Regeneration is unavailable."
            ) from None
        launch_new_project_candidate(regenerated, device_id)
        return regenerated.view

    @app.post("/v1/new-fgui-projects/{build_id}/approve")
    async def approve_new_fgui_project(build_id: str, request: Request) -> NewFguiProjectView:
        device_id = plugin_device(request, PluginScope.SELECTION_READ_OWN_STATUS)
        payload = cast(
            NewProjectApprovalRequest,
            await _strict_json_body(request, NewProjectApprovalRequest),
        )
        project = load_new_project(build_id, device_id)
        if (
            project.review is None
            or project.superseded_by is not None
            or project.review.generation != payload.generation
            or project.generation != payload.generation
            or tuple(payload.warning_ids) != project.review.warning_ids
            or not project.review.approvable
        ):
            raise _error(409, "new_project_approval_blocked", "Candidate cannot be approved.")
        require_current_artifact(project)
        try:
            selection = selection_store.get(project.selection_id, device_id)
            if selection.fingerprint != project.selection_fingerprint:
                raise SelectionError("selection_not_found")
            return store.decide_new_project(
                build_id=build_id,
                owner_device_id=device_id,
                generation=payload.generation,
                target="approved",
            ).view
        except (SelectionError, StoreError):
            raise _error(
                409, "new_project_approval_blocked", "Candidate cannot be approved."
            ) from None

    @app.post("/v1/new-fgui-projects/{build_id}/reject")
    async def reject_new_fgui_project(build_id: str, request: Request) -> NewFguiProjectView:
        device_id = plugin_device(request, PluginScope.SELECTION_READ_OWN_STATUS)
        payload = cast(
            NewProjectRejectRequest,
            await _strict_json_body(request, NewProjectRejectRequest),
        )
        try:
            return store.decide_new_project(
                build_id=build_id,
                owner_device_id=device_id,
                generation=payload.generation,
                target="rejected",
            ).view
        except NotFound:
            raise _error(404, "new_project_not_found", "Candidate not found.") from None
        except StoreError:
            raise _error(409, "new_project_state_conflict", "Candidate state changed.") from None

    @app.get("/v1/new-fgui-projects/{build_id}/download")
    def download_new_fgui_project(build_id: str, request: Request) -> FileResponse:
        device_id = plugin_device(request, PluginScope.SELECTION_READ_OWN_STATUS)
        project = load_new_project(build_id, device_id)
        if project.view.stage != "approved" or project.superseded_by is not None:
            raise _error(
                409,
                "new_project_download_blocked",
                "Approve the current candidate before download.",
            )
        artifact = require_current_artifact(project)
        return FileResponse(
            artifact,
            media_type="application/zip",
            filename=project.view.download_name,
        )

    @app.post("/v1/agents/register")
    def register_agent(agent: AgentRegistration) -> AgentRegistration:
        return store.register_agent(agent)

    @app.post("/v1/hifi-sources/psd/inspect")
    async def inspect_hifi_psd(
        psd: Annotated[UploadFile | None, File()] = None,
    ) -> dict[str, object]:
        filename = Path(psd.filename or "").name if psd is not None else ""
        if psd is None or Path(filename).suffix.lower() != ".psd":
            raise _error(400, "invalid_psd", "The PSD source is invalid.")

        intake_dir = data_dir / "psd-intake"
        intake_dir.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_upload = tempfile.mkstemp(
            prefix="inspect-", suffix=".psd", dir=intake_dir
        )
        upload_path = Path(temporary_upload)
        try:
            written = 0
            with os.fdopen(descriptor, "wb") as destination:
                while chunk := await psd.read(_UPLOAD_CHUNK_BYTES):
                    written += len(chunk)
                    if written > _MAX_PSD_BYTES:
                        raise _error(413, "psd_too_large", "The PSD source is too large.")
                    destination.write(chunk)
            report = await run_in_threadpool(inspect_psd, upload_path, source_name=filename)
            return cast(dict[str, object], asdict(report))
        except PsdIntakeError as error:
            raise _error(400, error.args[0], "The PSD source is invalid.") from error
        finally:
            with suppress(Exception):
                await psd.close()
            with suppress(Exception):
                upload_path.unlink(missing_ok=True)

    @app.post("/v1/hifi-sources/psd", status_code=201)
    async def upload_hifi_psd_source(
        psd: Annotated[UploadFile | None, File()] = None,
    ) -> dict[str, object]:
        filename = Path(psd.filename or "").name if psd is not None else ""
        if psd is None or Path(filename).suffix.lower() != ".psd":
            raise _error(400, "invalid_psd", "The PSD source is invalid.")

        intake_dir = data_dir / "psd-intake"
        intake_dir.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_upload = tempfile.mkstemp(
            prefix="source-", suffix=".psd", dir=intake_dir
        )
        upload_path = Path(temporary_upload)
        try:
            written = 0
            with os.fdopen(descriptor, "wb") as destination:
                while chunk := await psd.read(_UPLOAD_CHUNK_BYTES):
                    written += len(chunk)
                    if written > _MAX_PSD_BYTES:
                        raise _error(413, "psd_too_large", "The PSD source is too large.")
                    destination.write(chunk)
            source = await run_in_threadpool(
                psd_source_store.admit, upload_path, source_name=filename
            )
            return cast(dict[str, object], psd_source_store.payload(source))
        except PsdIntakeError as error:
            raise _error(400, error.args[0], "The PSD source is invalid.") from error
        finally:
            with suppress(Exception):
                await psd.close()
            with suppress(Exception):
                upload_path.unlink(missing_ok=True)

    @app.get("/v1/hifi-sources/psd/{source_id}")
    def get_hifi_psd_source(source_id: str) -> dict[str, object]:
        try:
            source = psd_source_store.get(source_id)
        except PsdSourceStoreError as error:
            status = 404 if error.code == "psd_source_not_found" else 409
            raise _error(status, error.code, "The PSD source is unavailable.") from error
        return cast(dict[str, object], psd_source_store.payload(source))

    @app.get("/v1/hifi-sources/psd/{source_id}/composite")
    def get_hifi_psd_composite(source_id: str) -> FileResponse:
        try:
            composite = psd_source_store.composite_path(source_id)
        except PsdSourceStoreError as error:
            status = 404 if error.code == "psd_source_not_found" else 409
            raise _error(status, error.code, "The PSD composite is unavailable.") from error
        return FileResponse(
            composite,
            media_type="image/png",
            headers={
                "Cache-Control": "private, max-age=31536000, immutable",
                "X-PSD-Source-SHA256": source_id,
            },
        )

    @app.post("/v1/projects/bind")
    def bind_project(binding: ProjectBinding) -> ProjectBinding:
        try:
            return store.bind_project(binding)
        except StoreError as error:
            raise _error(404, error.code, str(error)) from error

    @app.post("/v1/projects/uploads", status_code=201)
    async def upload_project(
        request: Request,
        project: Annotated[UploadFile | None, File()] = None,
    ) -> ProjectUploadView:
        if project is None:
            upload_error = _upload_error("invalid_fgui_project")
            raise _error(400, upload_error.code, upload_error.user_message)
        filename = project.filename or ""
        archive_suffix = project_archive_suffix(filename)
        # Windows browsers and WebView2 report inconsistent MIME types for local
        # archives. The archive reader validates the actual bytes after upload.
        if archive_suffix is None:
            raise _error(
                400, "invalid_fgui_project", _upload_error("invalid_fgui_project").user_message
            )

        uploads = data_dir / "uploads"
        uploads.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_upload = tempfile.mkstemp(
            prefix="upload-", suffix=archive_suffix, dir=uploads
        )
        upload_path = Path(temporary_upload)
        extracted_path = uploads / f"extract-{uuid.uuid4().hex}"
        try:
            written = 0
            with os.fdopen(descriptor, "wb") as destination:
                while chunk := await project.read(_UPLOAD_CHUNK_BYTES):
                    written += len(chunk)
                    if written > DEFAULT_UPLOAD_LIMITS.max_compressed_bytes:
                        raise _upload_error("archive_too_large")
                    destination.write(chunk)
            extracted = extract_project_archive(upload_path, extracted_path, filename)
            version = index_uploaded_project(extracted.root, Path(filename).name)
            return project_view(project_store.create(version, extracted.root))
        except UploadError as error:
            raise _error(400, error.code, error.user_message) from error
        except (KeyError, OSError, ProjectIntegrityError, ValueError) as error:
            upload_error = _upload_error("invalid_fgui_project")
            raise _error(400, upload_error.code, upload_error.user_message) from error
        finally:
            with suppress(Exception):
                await project.close()
            with suppress(Exception):
                upload_path.unlink(missing_ok=True)
            with suppress(Exception):
                shutil.rmtree(extracted_path)

    @app.get("/v1/figma/project-options")
    def get_project_options() -> ProjectOptionsView:
        return ProjectOptionsView(options=template_catalog.list_options())

    @app.post("/v1/projects/from-template", status_code=201)
    def create_project_from_template(payload: dict[str, object]) -> ProjectUploadView:
        try:
            request = CreateTemplateProject.model_validate(payload)
        except ValidationError as error:
            if any(item["loc"][-1] == "project_name" for item in error.errors()):
                raise _error(400, "invalid_project_name", "Project name is invalid.") from error
            raise _error(400, "invalid_template_request", "Template request is invalid.") from error
        destination = data_dir / "template-projects" / uuid.uuid4().hex
        try:
            root = template_catalog.create(request.template_id, request.project_name, destination)
            version = index_uploaded_project(root, request.project_name)
            return project_view(project_store.create(version, root))
        except TemplateNotFound as error:
            raise _error(404, "template_not_found", "Requested template was not found.") from error
        except (KeyError, OSError, ProjectIntegrityError, ValueError, etree.LxmlError) as error:
            raise _error(
                400, "invalid_fgui_project", _upload_error("invalid_fgui_project").user_message
            ) from error
        finally:
            with suppress(Exception):
                shutil.rmtree(destination)

    @app.get("/v1/projects/{project_id}")
    def get_project(project_id: str, request: Request) -> ProjectUploadView:
        return project_view(load_uploaded_project(project_id))

    @app.get("/v1/projects/{project_id}/packages")
    def get_project_packages(project_id: str, request: Request) -> ProjectUploadView:
        return project_view(load_uploaded_project(project_id))

    @app.get("/v1/projects/{project_id}/assets/{asset_id}/thumbnail")
    def get_asset_thumbnail(project_id: str, asset_id: str, request: Request) -> FileResponse:
        load_uploaded_project(project_id)
        try:
            thumbnail = project_store.thumbnail_path(project_id, asset_id)
        except ProjectIntegrityError as error:
            raise _error(404, "asset_not_found", _ASSET_NOT_FOUND_MESSAGE) from error
        return FileResponse(thumbnail, media_type="image/webp")

    @app.get("/v1/projects/{project_id}/hifi-targets")
    def get_hifi_targets(project_id: str, request: Request) -> HifiProjectTreeView:
        plugin_device(request, PluginScope.SELECTION_READ_OWN_STATUS)
        project = load_uploaded_project(project_id)
        return inspect_hifi_targets(project_store.artifact_path(project_id), project)

    @app.post("/v1/hifi-replacements", status_code=201)
    def create_hifi_replacement(
        payload: HifiReplacementCreate, request: Request
    ) -> HifiReplacementView:
        owner = plugin_device(request, PluginScope.SELECTION_READ_OWN_STATUS)
        if payload.project_id != payload.target.project_id:
            raise _error(
                400,
                "hifi_target_invalid",
                "HIFI replacement request could not be completed.",
            )
        try:
            stored = hifi_replacement_workflow.begin(
                owner,
                payload.selection_id,
                payload.target,
                payload.idempotency_key,
            )
        except HifiReplacementStoreError as error:
            raise hifi_error(error) from error
        except (ProjectIntegrityError, SelectionError, OSError, ValueError, etree.LxmlError) as error:
            raise _error(
                409,
                "hifi_target_stale",
                "HIFI replacement request could not be completed.",
            ) from error
        return stored.view

    @app.post("/v1/hifi-replacements/from-psd", status_code=201)
    def create_psd_hifi_replacement(
        payload: HifiPsdReplacementCreate, request: Request
    ) -> HifiReplacementView:
        owner = plugin_device(request, PluginScope.SELECTION_READ_OWN_STATUS)
        if payload.project_id != payload.target.project_id:
            raise _error(
                400,
                "hifi_target_invalid",
                "HIFI replacement request could not be completed.",
            )
        try:
            stored = hifi_replacement_workflow.begin_psd(
                owner,
                payload.psd_source_id,
                payload.target,
                payload.idempotency_key,
            )
        except HifiReplacementStoreError as error:
            raise hifi_error(error) from error
        except (ProjectIntegrityError, PsdSourceStoreError, OSError, ValueError, etree.LxmlError) as error:
            raise _error(
                409,
                "hifi_target_stale",
                "HIFI replacement request could not be completed.",
            ) from error
        return stored.view

    @app.post("/v1/hifi-replacements/from-psd-batch", status_code=201)
    def create_psd_hifi_replacement_batch(
        payload: HifiPsdBatchReplacementCreate, request: Request
    ) -> list[HifiReplacementView]:
        owner = plugin_device(request, PluginScope.SELECTION_READ_OWN_STATUS)
        try:
            stored = hifi_replacement_workflow.begin_psd_batch(
                owner,
                payload.psd_source_id,
                tuple(payload.targets),
                payload.idempotency_key,
            )
        except HifiReplacementStoreError as error:
            raise hifi_error(error) from error
        except (ProjectIntegrityError, PsdSourceStoreError, OSError, ValueError, etree.LxmlError) as error:
            raise _error(
                409,
                "hifi_target_stale",
                "HIFI replacement request could not be completed.",
            ) from error
        return [item.view for item in stored]

    @app.get("/v1/hifi-replacements/{replacement_id}")
    def get_hifi_replacement(
        replacement_id: str, request: Request
    ) -> HifiReplacementView:
        return load_hifi_replacement(replacement_id, request).view

    @app.get("/v1/hifi-replacements/{replacement_id}/mapping")
    def get_hifi_mapping(
        replacement_id: str, request: Request
    ) -> HifiMappingDraft:
        return load_hifi_replacement(replacement_id, request).mapping

    @app.post("/v1/hifi-replacements/{replacement_id}/mapping-decisions")
    def save_hifi_mapping_decision(
        replacement_id: str,
        payload: HifiMappingDecision,
        request: Request,
    ) -> HifiReplacementView:
        owner = plugin_device(request, PluginScope.SELECTION_READ_OWN_STATUS)
        try:
            return hifi_replacement_workflow.save_decision(
                replacement_id, owner, payload
            ).view
        except HifiReplacementStoreError as error:
            raise hifi_error(error) from error

    @app.post("/v1/hifi-replacements/{replacement_id}/build")
    def build_hifi_replacement(
        replacement_id: str,
        payload: HifiReplacementBuildRequest,
        request: Request,
    ) -> HifiReplacementView:
        owner = plugin_device(request, PluginScope.SELECTION_READ_OWN_STATUS)
        try:
            return hifi_replacement_workflow.build(
                replacement_id, owner, payload.mapping_revision
            ).view
        except HifiReplacementStoreError as error:
            raise hifi_error(error) from error
        except HifiPatchError as error:
            raise _error(409, error.code, "HIFI in-place replacement is blocked.") from error
        except PsdSourceStoreError as error:
            raise _error(409, error.code, "PSD raster export is blocked.") from error
        except (OSError, ValueError, etree.LxmlError) as error:
            raise _error(
                409,
                "hifi_build_failed",
                "HIFI replacement request could not be completed.",
            ) from error

    @app.get("/v1/hifi-replacements/{replacement_id}/review")
    def get_hifi_replacement_review(
        replacement_id: str, request: Request
    ) -> HifiReplacementReview:
        stored = load_hifi_replacement(replacement_id, request)
        if stored.review is None:
            raise _error(
                409,
                "hifi_review_unavailable",
                "HIFI replacement request could not be completed.",
            )
        return stored.review

    @app.get("/v1/hifi-replacements/{replacement_id}/candidate/download")
    def download_hifi_candidate(replacement_id: str, request: Request) -> FileResponse:
        stored = load_hifi_replacement(replacement_id, request)
        if stored.view.status not in {"review_ready", "approved"}:
            raise _error(
                409,
                "hifi_download_blocked",
                "HIFI replacement request could not be completed.",
            )
        try:
            artifact = hifi_replacement_store.verified_artifact(stored)
        except HifiReplacementStoreError as error:
            raise hifi_error(error) from error
        return FileResponse(
            artifact,
            media_type="application/zip",
            filename=stored.artifact_name,
        )

    @app.post("/v1/hifi-replacements/{replacement_id}/editor-verify")
    async def verify_hifi_replacement_in_editor(
        replacement_id: str, request: Request
    ) -> HifiEditorVerification:
        owner = plugin_device(request, PluginScope.SELECTION_READ_OWN_STATUS)
        try:
            stored = hifi_replacement_store.get(replacement_id, owner)
            if (
                stored.view.status not in {"review_ready", "approved"}
                or stored.artifact_sha256 is None
                or len(stored.view.selection_id) != 64
            ):
                raise HifiReplacementStoreError("hifi_review_unavailable")
            artifact = hifi_replacement_store.verified_artifact(stored)
            source = psd_source_store.get(stored.view.selection_id)
            expected_width = source.inspection.width
            expected_height = source.inspection.height
            reference = psd_source_store.composite_path(stored.view.selection_id)
            canvas = stored.mapping.old_canvas_size
            if canvas is not None:
                canvas_width, canvas_height = round(canvas[0]), round(canvas[1])
                if (
                    canvas_width <= expected_width
                    and canvas_height <= expected_height
                    and (canvas_width, canvas_height) != (expected_width, expected_height)
                ):
                    # The mapping wrote PSD coordinates relative to the
                    # component viewport, so the render is verified against
                    # that same cropped region, not the full PSD canvas.
                    reference = psd_source_store.composite_viewport_path(
                        stored.view.selection_id, canvas_width, canvas_height
                    )
                    expected_width, expected_height = canvas_width, canvas_height
            verification = await run_in_threadpool(
                lambda: verify_in_fairygui_editor(
                    data_dir=data_dir,
                    session_id=replacement_id,
                    candidate_sha256=cast(str, stored.artifact_sha256),
                    artifact=artifact,
                    target=stored.view.target,
                    reference=reference,
                    expected_width=expected_width,
                    expected_height=expected_height,
                )
            )
            from figma_to_fgui.psd_hifi_adapter import psd_lossless_blockers
            if "outside_canvas_content_requires_equivalence_check" in psd_lossless_blockers(source):
                # Out-of-canvas content cannot be proven by any screenshot;
                # the human editor check confirms the clipping intent.
                verification = verification.model_copy(update={
                    "warnings": (*verification.warnings,
                                 "PSD 存在超出画布的可见图层边界；画布内区域已整帧比对，画布外内容与裁切意图请在 Editor 检查中确认。"),
                })
            hifi_replacement_store.save_editor_verification(
                replacement_id, owner, verification
            )
            return verification
        except HifiReplacementStoreError as error:
            raise hifi_error(error) from error
        except (FairyGuiEditorVerificationError, OSError, ValueError) as error:
            code = getattr(error, "code", "fgui_editor_verification_failed")
            raise _error(409, code, "FairyGUI Editor verification failed.") from error

    @app.get("/v1/hifi-replacements/{replacement_id}/editor-screenshot")
    def get_hifi_editor_screenshot(replacement_id: str, request: Request) -> FileResponse:
        stored = load_hifi_replacement(replacement_id, request)
        if stored.artifact_sha256 is None:
            raise _error(409, "hifi_review_unavailable", "HIFI review is unavailable.")
        screenshot = editor_screenshot_path(data_dir, replacement_id, stored.artifact_sha256)
        if not screenshot.is_file():
            raise _error(
                404,
                "fgui_editor_screenshot_unavailable",
                "FairyGUI Editor screenshot is unavailable.",
            )
        return FileResponse(
            screenshot,
            media_type="image/png",
            headers={"Cache-Control": "private, no-store"},
        )

    @app.post("/v1/hifi-replacements/{replacement_id}/approve")
    def approve_hifi_replacement(
        replacement_id: str,
        payload: dict[str, object],
        request: Request,
    ) -> HifiReplacementView:
        owner = plugin_device(request, PluginScope.SELECTION_READ_OWN_STATUS)
        try:
            checks = HifiEditorChecks.model_validate(payload)
        except ValidationError as error:
            raise _error(
                409,
                "hifi_editor_checks_incomplete",
                "Complete every FairyGUI Editor check before approval.",
            ) from error
        try:
            stored = hifi_replacement_store.get(replacement_id, owner)
            if stored.artifact_sha256 != checks.candidate_sha256:
                raise HifiReplacementStoreError("hifi_candidate_stale")
            if not stored.approval_ready:
                raise HifiReplacementStoreError("hifi_download_blocked")
            if checks.export_mode == "overwrite":
                hifi_replacement_workflow.deliver_overwrite(
                    replacement_id, owner, checks.candidate_sha256
                )
            return hifi_replacement_store.approve(
                replacement_id, owner, checks.model_dump_json()
            ).view
        except HifiReplacementStoreError as error:
            raise hifi_error(error) from error
        except (HifiPatchError, PsdSourceStoreError, OSError, ValueError, etree.LxmlError) as error:
            raise _error(
                409,
                "hifi_overwrite_failed",
                "HIFI replacement request could not be completed.",
            ) from error

    @app.post("/v1/hifi-replacements/{replacement_id}/reject")
    def reject_hifi_replacement(
        replacement_id: str,
        payload: HifiReplacementRejectRequest,
        request: Request,
    ) -> HifiReplacementView:
        owner = plugin_device(request, PluginScope.SELECTION_READ_OWN_STATUS)
        try:
            return hifi_replacement_store.reject(replacement_id, owner).view
        except HifiReplacementStoreError as error:
            raise hifi_error(error) from error

    @app.get("/v1/hifi-replacements/{replacement_id}/download")
    def download_hifi_replacement(replacement_id: str, request: Request) -> FileResponse:
        stored = load_hifi_replacement(replacement_id, request)
        if stored.view.status != "approved" or not stored.approval_ready:
            raise _error(
                409,
                "hifi_download_blocked",
                "HIFI replacement request could not be completed.",
            )
        try:
            artifact = hifi_replacement_store.verified_artifact(stored)
        except HifiReplacementStoreError as error:
            raise hifi_error(error) from error
        return FileResponse(
            artifact,
            media_type="application/zip",
            filename=stored.artifact_name,
        )

    def conversion_bundle(
        job_id: str,
        context: _ConversionContext,
        screenshot: bytes | None = None,
    ) -> tuple[ChangeSet, ChangeBundle, SemanticAnalysisOutcome | None]:
        capturing = (
            _CapturingSemanticAnalyzer(semantic_analyzer) if semantic_analyzer is not None else None
        )
        with tempfile.TemporaryDirectory(dir=data_dir) as temporary:
            staging = Path(temporary) / "staging"
            result = convert_document(
                context.raw,
                context.project_root,
                context.package_name,
                staging,
                rules_path,
                context.selection_assets,
                capturing,
                screenshot,
            )
            files: list[ChangeFile] = []
            bundle_bytes = 0
            for generated in result.files:
                bundle_bytes += generated.size
                if bundle_bytes > _MAX_CHANGE_BUNDLE_BYTES:
                    raise ConversionLimitError("generated changeset is too large")

                source = staging / generated.relative_path
                with source.open("rb") as handle:
                    content = handle.read()
                if len(content) != generated.size:
                    raise OSError(f"generated file size changed: {generated.relative_path}")

                existing = context.project_root / generated.relative_path
                operation = FileOperation.REPLACE if existing.is_file() else FileOperation.CREATE
                before = (
                    hashlib.sha256(existing.read_bytes()).hexdigest()
                    if existing.is_file()
                    else None
                )
                files.append(
                    ChangeFile(
                        operation=operation,
                        relative_path=generated.relative_path,
                        before_sha256=before,
                        after_sha256=generated.sha256,
                        content_b64=base64.b64encode(content).decode("ascii"),
                    )
                )
        return (
            result,
            ChangeBundle(job_id=job_id, project_id=context.project_id, files=tuple(files)),
            capturing.outcome if capturing is not None else None,
        )

    def persisted_conversion_context(job_id: str) -> _ConversionContext:
        job = load_job(job_id)
        raw: dict[str, object]
        assets: tuple[SelectionAsset, ...]
        try:
            reference = store.get_job_conversion_reference(job_id)
            project_root = (
                fixtures_root / "fgui"
                if job.project_fingerprint is None
                else project_store.artifact_path(job.project_id)
            )
            if reference.source == "selection":
                if reference.selection_fingerprint is None:
                    raise ValueError("selection fingerprint is unavailable")
                selection_root = selection_store.artifact_path(reference.source_id)
                manifest = SelectionManifest.model_validate_json(
                    (selection_root / "manifest.json").read_text("utf-8")
                )
                document = selection_conversion_document(
                    manifest,
                    selection_root / "resources",
                    reference.selection_fingerprint,
                )
                raw = document.raw
                assets = document.assets
            elif reference.source == "fixture":
                if Path(reference.source_id).name != reference.source_id:
                    raise ValueError("invalid fixture reference")
                payload = json.loads(
                    (fixtures_root / "figma" / reference.source_id).read_text("utf-8")
                )
                if not isinstance(payload, dict):
                    raise ValueError("invalid fixture reference")
                raw = payload
                assets = ()
            else:
                raise ValueError("invalid conversion source")
        except (NotFound, OSError, ValueError, ProjectIntegrityError, SelectionError) as error:
            raise _error(
                409,
                "conversion_context_unavailable",
                "Conversion input is no longer available.",
            ) from error
        return _ConversionContext(
            raw=raw,
            project_id=job.project_id,
            package_name=reference.package_name,
            project_root=project_root,
            project_fingerprint=job.project_fingerprint,
            package_names=job.package_names,
            selection_assets=assets,
        )

    def create_conversion_job(
        raw: dict[str, object],
        project_id: str,
        package_name: str,
        project_root: Path,
        project_fingerprint: str | None = None,
        package_names: tuple[str, ...] = (),
        selection_id: str | None = None,
        selection_fingerprint: str | None = None,
        selection_assets: tuple[SelectionAsset, ...] = (),
        conversion_source: str = "fixture",
        conversion_source_id: str = "",
    ) -> JobView:
        job_id = uuid.uuid4().hex
        context = _ConversionContext(
            raw=raw,
            project_id=project_id,
            package_name=package_name,
            project_root=project_root,
            project_fingerprint=project_fingerprint,
            package_names=package_names,
            selection_assets=selection_assets,
        )
        try:
            result, bundle, semantic = conversion_bundle(job_id, context)
        except (OSError, ValueError) as error:
            code = (
                "conversion_too_large"
                if isinstance(error, ConversionLimitError)
                else "conversion_failed"
            )
            return store.create_job(
                JobView(
                    job_id=job_id,
                    project_id=project_id,
                    project_fingerprint=project_fingerprint,
                    package_names=package_names,
                    status=JobStatus.CONVERSION_FAILED,
                    diagnostics=(
                        Diagnostic(
                            code=code,
                            severity=Severity.ERROR,
                            message=type(error).__name__,
                        ),
                    ),
                ),
                selection_id,
                selection_fingerprint,
                conversion_source,
                conversion_source_id,
                package_name,
            )
        digest = artifacts.put(bundle)
        status = JobStatus.READY_FOR_REVIEW if result.applicable else JobStatus.CONVERSION_FAILED
        screenshot_reason = (
            semantic.screenshot_reason
            if semantic is not None and semantic.screenshot_recommended
            else None
        )
        job = store.create_job(
            JobView(
                job_id=job_id,
                project_id=project_id,
                project_fingerprint=project_fingerprint,
                package_names=package_names,
                status=status,
                diagnostics=result.diagnostics,
                artifact_sha256=digest,
            ),
            selection_id,
            selection_fingerprint,
            conversion_source,
            conversion_source_id,
            package_name,
            screenshot_reason,
        )
        return job

    if allow_fixture_jobs:

        def fixture_document(fixture_name: str) -> dict[str, object]:
            if Path(fixture_name).name != fixture_name:
                raise _error(400, "invalid_fixture", "fixture name must be a file name")
            source = fixtures_root / "figma" / fixture_name
            if not source.is_file():
                raise _error(400, "invalid_fixture", "fixture does not exist")
            payload = json.loads(source.read_text("utf-8"))
            if not isinstance(payload, dict):
                raise _error(400, "invalid_fixture", "fixture does not contain a document")
            return payload

        @app.post("/v1/jobs")
        def create_job(request: JobCreate) -> JobSummary:
            return job_summary(
                create_conversion_job(
                    fixture_document(request.fixture_name),
                    request.project_id,
                    request.package_name,
                    fixtures_root / "fgui",
                    package_names=(request.package_name,),
                    conversion_source="fixture",
                    conversion_source_id=request.fixture_name,
                )
            )

        @app.post("/v1/projects/{project_id}/jobs")
        def create_uploaded_project_job(project_id: str, request: ProjectJobCreate) -> JobSummary:
            if request.project_id != project_id:
                raise _error(400, "project_mismatch", "route and request project IDs differ")
            version = load_uploaded_project(project_id)
            try:
                project_root = project_store.artifact_path(project_id)
            except ProjectIntegrityError as error:
                raise _error(404, "project_not_found", _PROJECT_NOT_FOUND_MESSAGE) from error
            return job_summary(
                create_conversion_job(
                    fixture_document(request.fixture_name),
                    request.project_id,
                    request.package_name,
                    project_root,
                    version.fingerprint,
                    tuple(package.name for package in version.packages),
                    conversion_source="fixture",
                    conversion_source_id=request.fixture_name,
                )
            )

    @app.post("/v1/figma/selections/{selection_id}/projects/{project_id}/jobs")
    def create_selection_project_job(
        selection_id: str,
        project_id: str,
        request: SelectionProjectJobCreate,
        http_request: Request,
    ) -> JobSummary:
        device_id = (
            console_device(http_request)
            if http_request.headers.get("x-figma-console-session")
            else plugin_device(http_request, PluginScope.SELECTION_READ_OWN_STATUS)
        )
        if request.selection_id != selection_id or request.project_id != project_id:
            raise _error(400, "project_mismatch", "route and request IDs differ")
        version = load_uploaded_project(project_id)
        try:
            project_root = project_store.artifact_path(project_id)
            selection = selection_store.get(selection_id, device_id)
            selection_root = selection_store.artifact_path(selection_id)
            manifest = SelectionManifest.model_validate_json(
                (selection_root / "manifest.json").read_text("utf-8")
            )
            document = selection_conversion_document(
                manifest, selection_root / "resources", selection.fingerprint
            )
        except SelectionError as error:
            raise selection_error(error) from error
        except (OSError, ValueError) as error:
            raise _error(404, "selection_not_found", _SELECTION_MESSAGE) from error
        return job_summary(
            create_conversion_job(
                document.raw,
                project_id,
                request.package_name,
                project_root,
                version.fingerprint,
                tuple(package.name for package in version.packages),
                selection_id,
                selection.fingerprint,
                document.assets,
                conversion_source="selection",
                conversion_source_id=selection_id,
            )
        )

    @app.get("/v1/jobs/{job_id}/designer-preview")
    def designer_preview(
        job_id: str, details: str | None = None
    ) -> DesignerPreview | dict[str, object]:
        job = load_job(job_id)
        before_root = preview_root(job)
        bundle = load_bundle(job_id)

        def image_url(change_index: int, side: Literal["before", "after"]) -> str | None:
            if preview_image(job, bundle, change_index, side) is None:
                return None
            return f"/v1/jobs/{job_id}/designer-preview/images/{change_index}/{side}"

        preview = build_designer_preview(before_root, bundle, job.diagnostics, image_url=image_url)
        if details != "advanced":
            return preview
        files: list[dict[str, str | None]] = []
        for change in bundle.files:
            path = before_root / change.relative_path
            before_xml = (
                path.read_text("utf-8") if path.suffix == ".xml" and path.is_file() else None
            )
            content = base64.b64decode(change.content_b64)
            after_xml = content.decode("utf-8") if path.suffix == ".xml" else None
            files.append(
                {
                    "operation": change.operation,
                    "relative_path": change.relative_path,
                    "before_sha256": change.before_sha256,
                    "after_sha256": change.after_sha256,
                    "before_xml": before_xml,
                    "after_xml": after_xml,
                }
            )
        return {
            "preview": preview.model_dump(mode="json"),
            "details": {
                "files": files,
                "diagnostics": [item.model_dump(mode="json") for item in job.diagnostics],
            },
        }

    @app.get("/v1/jobs/{job_id}/designer-preview/images/{change_index}/{side}")
    def designer_preview_image(
        job_id: str, change_index: int, side: Literal["before", "after"]
    ) -> Response:
        job = load_job(job_id)
        bundle = load_bundle(job_id)
        content = preview_image(job, bundle, change_index, side)
        if content is None:
            raise _error(404, "preview_image_not_found", "preview image is unavailable")
        return Response(content=content, media_type="image/webp")

    @app.get("/v1/jobs/{job_id}/preview")
    def preview_job(job_id: str, response: Response) -> ChangeBundle:
        return redacted_bundle(job_id, response, f"/v1/jobs/{job_id}/designer-preview")

    @app.get("/v1/jobs/{job_id}")
    def get_job(job_id: str, request: Request) -> JobSummary:
        return job_summary(load_job(job_id))

    def package_request(payload: dict[str, object]) -> ProjectPackageRequest:
        try:
            return ProjectPackageRequest.model_validate(payload)
        except ValidationError as error:
            if any(item["loc"][-1] == "project_name" for item in error.errors()):
                raise _error(400, "invalid_project_name", "Project name is invalid.") from error
            raise _error(400, "invalid_package_request", "Package request is invalid.") from error

    def package_identity(request: ProjectPackageRequest) -> str:
        payload = request.model_dump_json(exclude_none=True).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def package_not_ready() -> HTTPException:
        return _error(409, "package_not_ready", "Project package is not ready.")

    def valid_package_artifact(package: StoredPackage) -> Path | None:
        artifact = package.artifact_path
        if (
            package.view.status is not ProjectPackageStage.READY
            or package.view.download_name is None
            or package.view.sha256 is None
            or artifact is None
        ):
            return None
        package_root = (data_dir / "project-packages").resolve()
        try:
            metadata = artifact.lstat()
            attributes = getattr(metadata, "st_file_attributes", 0)
            if artifact.is_symlink() or attributes & 0x400 or not stat.S_ISREG(metadata.st_mode):
                return None
            resolved = artifact.resolve(strict=True)
            resolved.relative_to(package_root)
            digest = hashlib.sha256()
            with resolved.open("rb") as source:
                while chunk := source.read(_UPLOAD_CHUNK_BYTES):
                    digest.update(chunk)
            return resolved if digest.hexdigest() == package.view.sha256 else None
        except (OSError, ValueError):
            return None

    def reconcile_package(package: StoredPackage) -> tuple[StoredPackage, Path | None]:
        if package.view.status is not ProjectPackageStage.READY:
            return package, None
        artifact = valid_package_artifact(package)
        if artifact is not None:
            return package, artifact
        failed = ProjectPackageView(
            job_id=package.view.job_id,
            status=ProjectPackageStage.FAILED,
            stage=ProjectPackageStage.FAILED,
            progress=package.view.progress,
            diagnostics=(
                Diagnostic(
                    code="package_artifact_invalid",
                    severity=Severity.ERROR,
                    message="Project package is unavailable or failed its integrity check.",
                ),
            ),
        )
        try:
            store.transition_package(
                package.view.job_id,
                package.request_identity,
                package.generation,
                None,
                (ProjectPackageStage.READY,),
                failed,
            )
        except StoreError:
            pass
        return store.get_package(package.view.job_id), None

    def stored_package_request(package: StoredPackage) -> ProjectPackageRequest:
        if package.request_payload is None:
            raise _error(
                409,
                "package_resume_unavailable",
                "Package request metadata is unavailable.",
            )
        try:
            return ProjectPackageRequest.model_validate_json(package.request_payload)
        except ValidationError as error:
            raise _error(
                409,
                "package_resume_unavailable",
                "Package request metadata is unavailable.",
            ) from error

    def run_package_build(
        job_id: str,
        identity: str,
        generation: int,
        package: ProjectPackageRequest,
        extra_diagnostics: tuple[Diagnostic, ...] = (),
        screenshot_path: Path | None = None,
        screenshot_digest: str | None = None,
    ) -> None:
        try:
            job = load_job(job_id)
            load_uploaded_project(job.project_id)
            project_root = project_store.artifact_path(job.project_id)
            current = store.get_package(job_id, identity)
            conversion_job = current.screenshot_candidate or job
            if conversion_job.artifact_sha256 is None:
                raise InvalidTransition("package conversion artifact is unavailable")
            conversion_bundle = load_bundle_artifact(conversion_job.artifact_sha256)
            packaging = current.view.model_copy(
                update={
                    "status": ProjectPackageStage.PACKAGING,
                    "stage": ProjectPackageStage.PACKAGING,
                    "progress": 90,
                    "screenshot_reason": None,
                }
            )
            if current.view.stage is ProjectPackageStage.CHECKING:
                store.transition_package(
                    job_id,
                    identity,
                    generation,
                    package_owner_id,
                    (ProjectPackageStage.CHECKING,),
                    packaging,
                )
            elif (
                current.view.stage is not ProjectPackageStage.PACKAGING
                or current.generation != generation
                or current.owner_id != package_owner_id
            ):
                raise InvalidTransition("package is not available to this builder")
            attempt_directory = hashlib.sha256(f"{job_id}:{generation}".encode()).hexdigest()[:12]
            heartbeat_stop = Event()

            def heartbeat() -> None:
                while not heartbeat_stop.wait(store.package_heartbeat_interval):
                    try:
                        store.renew_package_lease(job_id, identity, generation, package_owner_id)
                    except StoreError:
                        return

            heartbeat_thread = Thread(
                target=heartbeat,
                name=f"package-heartbeat-{job_id[:8]}",
                daemon=True,
            )
            heartbeat_thread.start()
            try:
                built = build_project_package(
                    project_root,
                    conversion_bundle,
                    package.mode,
                    package.project_name,
                    data_dir / "project-packages" / attempt_directory,
                )
            finally:
                heartbeat_stop.set()
                heartbeat_thread.join()
            store.renew_package_lease(job_id, identity, generation, package_owner_id)
            ready = ProjectPackageView(
                job_id=job_id,
                status=ProjectPackageStage.READY,
                stage=ProjectPackageStage.READY,
                progress=100,
                download_name=built.download_name,
                sha256=built.sha256,
                diagnostics=extra_diagnostics + conversion_job.diagnostics + built.diagnostics,
            )
            store.transition_package(
                job_id,
                identity,
                generation,
                package_owner_id,
                (ProjectPackageStage.PACKAGING,),
                ready,
                artifact_path=built.path,
            )
        except (Exception, CancelledError):  # noqa: BLE001
            failed = ProjectPackageView(
                job_id=job_id,
                status=ProjectPackageStage.FAILED,
                stage=ProjectPackageStage.FAILED,
                progress=90,
                diagnostics=(
                    Diagnostic(
                        code="package_failed",
                        severity=Severity.ERROR,
                        message="Project package could not be created.",
                    ),
                ),
            )
            with suppress(StoreError):
                store.transition_package(
                    job_id,
                    identity,
                    generation,
                    package_owner_id,
                    (ProjectPackageStage.CHECKING, ProjectPackageStage.PACKAGING),
                    failed,
                )
        finally:
            _cleanup_semantic_screenshot(
                store,
                job_id,
                generation,
                screenshot_digest,
                screenshot_path,
                data_dir / "semantic-screenshots",
            )

    @app.post("/v1/jobs/{job_id}/package", status_code=202)
    def create_project_package(
        job_id: str,
        payload: dict[str, object],
        request: Request,
        background_tasks: BackgroundTasks,
    ) -> ProjectPackageView:
        package = package_request(payload)
        job = authorize_job_access(job_id, request)
        identity = package_identity(package)
        initial = ProjectPackageView(
            job_id=job_id,
            status=ProjectPackageStage.CHECKING,
            stage=ProjectPackageStage.CHECKING,
            progress=70,
            diagnostics=job.diagnostics,
        )
        try:
            existing = store.get_package(job_id, identity)
            existing, _ = reconcile_package(existing)
            if existing.view.status is not ProjectPackageStage.FAILED:
                if (
                    existing.view.stage is ProjectPackageStage.AWAITING_SCREENSHOT_CONSENT
                    and existing.screenshot_completed
                ):
                    return recover_completed_screenshot(
                        existing,
                        background_tasks,
                        existing.generation,
                        existing.screenshot_digest,
                    )
                return existing.view
        except NotFound:
            existing = None
        except PackageRequestConflict as error:
            raise _error(409, error.code, "A different package request already exists.") from error

        try:
            load_bundle(job_id)
            load_uploaded_project(job.project_id)
            project_store.artifact_path(job.project_id)
        except HTTPException:
            if existing is not None:
                return existing.view
            raise
        except ProjectIntegrityError as error:
            if existing is not None:
                return existing.view
            raise _error(404, "project_not_found", _PROJECT_NOT_FOUND_MESSAGE) from error
        try:
            attempt = store.begin_package(
                job_id,
                identity,
                package_owner_id,
                initial,
                package.model_dump_json(),
            )
        except PackageRequestConflict as error:
            raise _error(409, error.code, "A different package request already exists.") from error
        if not attempt.should_build:
            current, _ = reconcile_package(store.get_package(job_id, identity))
            return current.view

        reason = store.get_job_screenshot_reason(job_id)
        if reason is not None:
            waiting = initial.model_copy(
                update={
                    "status": ProjectPackageStage.AWAITING_SCREENSHOT_CONSENT,
                    "stage": ProjectPackageStage.AWAITING_SCREENSHOT_CONSENT,
                    "screenshot_reason": reason,
                }
            )
            store.await_screenshot_consent(
                job_id,
                identity,
                attempt.generation,
                package_owner_id,
                waiting,
            )
            return waiting
        background_tasks.add_task(
            run_package_build,
            job_id,
            identity,
            attempt.generation,
            package,
        )
        return initial

    def screenshot_protocol_error(error: StoreError) -> HTTPException:
        if isinstance(error, NotFound):
            return _error(409, "package_not_ready", "Project package is not ready.")
        if isinstance(error, PackageConsentConflict):
            return _error(409, error.code, "A different screenshot decision already exists.")
        return _error(409, error.code, "Screenshot consent state has changed.")

    def resume_screenshot_package(
        stored: StoredPackage,
        background_tasks: BackgroundTasks,
        extra_diagnostics: tuple[Diagnostic, ...] = (),
    ) -> ProjectPackageView:
        package = stored_package_request(stored)
        packaging = stored.view.model_copy(
            update={
                "status": ProjectPackageStage.PACKAGING,
                "stage": ProjectPackageStage.PACKAGING,
                "progress": 90,
                "screenshot_reason": None,
            }
        )
        try:
            resumed = store.resume_package_after_screenshot(
                stored.view.job_id,
                stored.generation,
                package_owner_id,
                packaging,
            )
        except StoreError as error:
            try:
                observed = store.get_package(stored.view.job_id)
            except StoreError:
                raise screenshot_protocol_error(error) from error
            if (
                observed.generation == stored.generation
                and observed.screenshot_consent == stored.screenshot_consent
                and observed.screenshot_digest == stored.screenshot_digest
                and observed.view.stage
                in {
                    ProjectPackageStage.PACKAGING,
                    ProjectPackageStage.READY,
                    ProjectPackageStage.FAILED,
                }
            ):
                return observed.view
            raise screenshot_protocol_error(error) from error
        background_tasks.add_task(
            run_package_build,
            stored.view.job_id,
            stored.request_identity,
            stored.generation,
            package,
            extra_diagnostics,
            stored.screenshot_path,
            stored.screenshot_digest,
        )
        return resumed.view

    def is_idempotent_screenshot_acceptance(
        stored: StoredPackage,
        expected_generation: int,
        expected_digest: str | None,
    ) -> bool:
        return (
            stored.generation == expected_generation
            and expected_digest is not None
            and stored.screenshot_digest == expected_digest
            and stored.screenshot_consent is True
            and stored.screenshot_completed
        )

    def recover_completed_screenshot(
        stored: StoredPackage,
        background_tasks: BackgroundTasks,
        expected_generation: int,
        expected_digest: str | None,
    ) -> ProjectPackageView:
        try:
            observed = store.get_package(stored.view.job_id)
        except StoreError:
            return stored.view
        if (
            not is_idempotent_screenshot_acceptance(observed, expected_generation, expected_digest)
            or observed.view.stage is not ProjectPackageStage.AWAITING_SCREENSHOT_CONSENT
        ):
            return observed.view
        return resume_screenshot_package(
            observed,
            background_tasks,
            observed.screenshot_completion_diagnostics,
        )

    @app.post(
        "/v1/jobs/{job_id}/semantic-screenshot-consent",
        status_code=202,
    )
    def semantic_screenshot_consent(
        job_id: str,
        payload: ScreenshotConsentRequest,
        request: Request,
        background_tasks: BackgroundTasks,
    ) -> ProjectPackageView:
        authorize_job_access(job_id, request)
        try:
            current = store.get_package(job_id)
            consent = store.record_screenshot_consent(job_id, current.generation, payload.approved)
            recorded = consent.package
        except StoreError as error:
            raise screenshot_protocol_error(error) from error
        if payload.approved:
            return recover_completed_screenshot(
                recorded,
                background_tasks,
                recorded.generation,
                recorded.screenshot_digest,
            )
        if consent.cleanup_path is not None:
            removed = _unlink_semantic_screenshot(
                consent.cleanup_path, data_dir / "semantic-screenshots"
            )
            if removed and recorded.screenshot_digest is not None:
                store.clear_screenshot_path(job_id, recorded.generation, recorded.screenshot_digest)
                recorded = store.get_package(job_id)
        if recorded.view.stage is not ProjectPackageStage.AWAITING_SCREENSHOT_CONSENT:
            return recorded.view
        declined = Diagnostic(
            code="semantic.screenshot_declined",
            severity=Severity.WARNING,
            message="Screenshot analysis was declined; validated fallback rules remain active.",
        )
        return resume_screenshot_package(recorded, background_tasks, (declined,))

    async def bounded_screenshot_body(request: Request) -> bytes:
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                declared_length = int(content_length)
                if declared_length < 0:
                    raise ValueError
                if declared_length > MAX_SEMANTIC_SCREENSHOT_BYTES:
                    raise _error(413, "screenshot_too_large", "Screenshot is too large.")
            except ValueError as error:
                raise _error(400, "invalid_content_length", "Content length is invalid.") from error
        content = bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content) > MAX_SEMANTIC_SCREENSHOT_BYTES:
                raise _error(413, "screenshot_too_large", "Screenshot is too large.")
        if not content:
            raise _error(400, "empty_screenshot", "Screenshot is empty.")
        return bytes(content)

    def validate_screenshot(media_type: str, content: bytes) -> None:
        png = content.startswith(b"\x89PNG\r\n\x1a\n")
        webp = len(content) >= 12 and content.startswith(b"RIFF") and content[8:12] == b"WEBP"
        if (
            (media_type == "image/png" and not png)
            or (media_type == "image/webp" and not webp)
            or encode_webp_preview(content) is None
        ):
            raise _error(415, "invalid_screenshot", "Screenshot content is invalid.")

    @app.post("/v1/jobs/{job_id}/semantic-screenshot", status_code=202)
    async def semantic_screenshot(
        job_id: str,
        request: Request,
        background_tasks: BackgroundTasks,
    ) -> ProjectPackageView:
        authorize_job_access(job_id, request)
        try:
            current = store.get_package(job_id)
        except StoreError as error:
            raise screenshot_protocol_error(error) from error
        media_type = request.headers.get("content-type", "").strip().lower()
        if media_type not in {"image/png", "image/webp"}:
            raise _error(
                415,
                "unsupported_screenshot_type",
                "Screenshot must be PNG or WebP.",
            )
        request_generation = current.generation
        content = await bounded_screenshot_body(request)
        validate_screenshot(media_type, content)
        digest = hashlib.sha256(content).hexdigest()
        try:
            observed = store.get_package(job_id)
        except StoreError as error:
            raise screenshot_protocol_error(error) from error
        if observed.generation != request_generation:
            raise _error(
                409,
                "screenshot_generation_changed",
                "Screenshot upload belongs to an expired package attempt.",
            )
        if is_idempotent_screenshot_acceptance(observed, request_generation, digest):
            if (
                observed.screenshot_completed
                and observed.view.stage is ProjectPackageStage.AWAITING_SCREENSHOT_CONSENT
            ):
                return recover_completed_screenshot(
                    observed,
                    background_tasks,
                    request_generation,
                    digest,
                )
            return observed.view
        if observed.screenshot_consent is not True:
            raise _error(
                409,
                "screenshot_consent_required",
                "Screenshot upload requires prior approval.",
            )
        current = observed
        suffix = ".png" if media_type == "image/png" else ".webp"
        screenshot_root = data_dir / "semantic-screenshots"
        try:
            path = _write_semantic_screenshot(
                screenshot_root, job_id, current.generation, suffix, content
            )
        except OSError as error:
            raise _error(
                409,
                "screenshot_storage_unavailable",
                "Screenshot storage is unavailable.",
            ) from error
        owns_attachment = False
        try:
            analysis_owner_id = uuid.uuid4().hex
            start = store.attach_and_claim_screenshot_analysis(
                job_id,
                current.generation,
                digest,
                path,
                analysis_owner_id,
            )
            attached = start.package
            owns_attachment = attached.screenshot_path == path
            if not owns_attachment:
                _unlink_semantic_screenshot(path, screenshot_root)
            if start.state is ScreenshotAnalysisStartState.ACCEPTED:
                return recover_completed_screenshot(
                    attached,
                    background_tasks,
                    current.generation,
                    digest,
                )
            if start.state is ScreenshotAnalysisStartState.IN_PROGRESS:
                return attached.view
            try:
                context = persisted_conversion_context(job_id)
                try:
                    result, bundle, _ = conversion_bundle(job_id, context, content)
                    artifact_sha256 = artifacts.put(bundle)
                    previous = load_job(job_id)
                    updated_status = (
                        JobStatus.READY_FOR_REVIEW
                        if result.applicable
                        else JobStatus.CONVERSION_FAILED
                    )
                    completion = store.complete_screenshot_conversion(
                        job_id,
                        current.generation,
                        digest,
                        previous.model_copy(
                            update={
                                "status": updated_status,
                                "diagnostics": result.diagnostics,
                                "artifact_sha256": artifact_sha256,
                            }
                        ),
                        claim_owner_id=analysis_owner_id,
                    )
                    attached = completion.package
                except (OSError, ValueError):
                    fallback_diagnostics = (
                        Diagnostic(
                            code="semantic.screenshot_fallback",
                            severity=Severity.WARNING,
                            message=(
                                "Screenshot analysis was unavailable; validated fallback rules "
                                "remain active."
                            ),
                        ),
                    )
                    completion = store.complete_screenshot_conversion(
                        job_id,
                        current.generation,
                        digest,
                        None,
                        fallback_diagnostics,
                        claim_owner_id=analysis_owner_id,
                    )
                    attached = completion.package
                if not completion.committed:
                    return recover_completed_screenshot(
                        attached,
                        background_tasks,
                        current.generation,
                        digest,
                    )
                return resume_screenshot_package(
                    attached,
                    background_tasks,
                    attached.screenshot_completion_diagnostics,
                )
            finally:
                with suppress(StoreError):
                    store.release_screenshot_analysis_claim(
                        job_id,
                        current.generation,
                        digest,
                        analysis_owner_id,
                    )
        except StoreError as error:
            with suppress(StoreError):
                observed = store.get_package(job_id)
                if is_idempotent_screenshot_acceptance(
                    observed, current.generation, digest
                ) and observed.view.stage in {
                    ProjectPackageStage.PACKAGING,
                    ProjectPackageStage.READY,
                }:
                    if not owns_attachment:
                        _unlink_semantic_screenshot(path, screenshot_root)
                    return observed.view
            if owns_attachment:
                _cleanup_semantic_screenshot(
                    store,
                    job_id,
                    current.generation,
                    digest,
                    path,
                    screenshot_root,
                )
            else:
                _unlink_semantic_screenshot(path, screenshot_root)
            raise screenshot_protocol_error(error) from error
        except HTTPException:
            if owns_attachment:
                _cleanup_semantic_screenshot(
                    store,
                    job_id,
                    current.generation,
                    digest,
                    path,
                    screenshot_root,
                )
            else:
                _unlink_semantic_screenshot(path, screenshot_root)
            raise

    @app.get("/v1/jobs/{job_id}/package")
    def get_project_package(job_id: str, request: Request) -> ProjectPackageView:
        authorize_job_access(job_id, request)
        try:
            package, _ = reconcile_package(store.get_package(job_id))
            return package.view
        except NotFound as error:
            raise package_not_ready() from error

    @app.get("/v1/jobs/{job_id}/package/download")
    def download_project_package(job_id: str, request: Request) -> FileResponse:
        authorize_job_access(job_id, request)
        try:
            package, artifact = reconcile_package(store.get_package(job_id))
        except NotFound as error:
            raise package_not_ready() from error
        if (
            package.view.status is not ProjectPackageStage.READY
            or package.view.download_name is None
            or artifact is None
        ):
            raise package_not_ready()
        return FileResponse(
            artifact,
            media_type="application/zip",
            filename=package.view.download_name,
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/v1/jobs/{job_id}/approve")
    def approve_job(job_id: str) -> JobSummary:
        try:
            return job_summary(store.approve_job(job_id))
        except StoreError as error:
            status = 404 if isinstance(error, NotFound) else 409
            raise _error(status, error.code, str(error)) from error

    @app.post("/v1/jobs/{job_id}/reject")
    def reject_job(job_id: str) -> JobSummary:
        try:
            return job_summary(store.reject_job(job_id))
        except StoreError as error:
            status = 404 if isinstance(error, NotFound) else 409
            raise _error(status, error.code, str(error)) from error

    @app.get("/v1/agents/{agent_id}/assignments/next", response_model=None)
    def claim_next(agent_id: str) -> JobView | Response:
        job = store.claim_next(agent_id)
        return job if job is not None else Response(status_code=204)

    @app.get("/v1/agents/{agent_id}/assignments/{job_id}/artifact")
    def get_assignment_artifact(agent_id: str, job_id: str) -> ChangeBundle:
        try:
            store.get_assignment_artifact_job(agent_id, job_id)
        except StoreError as error:
            status = 404 if isinstance(error, NotFound) else 409
            raise _error(status, error.code, str(error)) from error
        return load_bundle(job_id)

    @app.get("/v1/jobs/{job_id}/changeset")
    def get_changeset(job_id: str, response: Response) -> ChangeBundle:
        return redacted_bundle(
            job_id,
            response,
            "/v1/agents/{agent_id}/assignments/next",
        )

    @app.post("/v1/jobs/{job_id}/apply-result")
    def record_result(job_id: str, result: ApplyResult) -> JobSummary:
        if job_id != result.job_id:
            raise _error(400, "job_mismatch", "route and result job IDs differ")
        try:
            return job_summary(store.record_apply_result(result))
        except StoreError as error:
            status = 404 if isinstance(error, NotFound) else 409
            raise _error(status, error.code, str(error)) from error

    if index_html is not None and assets_dir is not None:
        app.mount("/assets", _ImmutableStaticFiles(directory=assets_dir), name="web-assets")

        @app.get("/{client_route:path}", include_in_schema=False)
        def web_console(client_route: str) -> FileResponse:
            if client_route == "health" or client_route.startswith("v1/"):
                raise _error(404, "not_found", "resource not found")
            return FileResponse(
                index_html,
                media_type="text/html",
                headers={"Cache-Control": "no-store"},
            )

    return app
