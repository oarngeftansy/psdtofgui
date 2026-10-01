from __future__ import annotations

import logging
import os
from pathlib import Path

import httpx
from pydantic import Field, field_validator

from figma_to_fgui.apply import ApplyError, SourceConflict, apply_bundle, verify_pre_write
from figma_to_fgui.models import Diagnostic, FrozenModel, Severity
from figma_to_fgui.service_contracts import (
    AgentRegistration,
    ApplyResult,
    ApplyStatus,
    ChangeBundle,
    JobView,
    ProjectBinding,
)
from figma_to_fgui.uploaded_project import _fingerprint, _packages, _source_paths

_LOCAL_PROJECT_CHANGED_MESSAGE = "本地工程已有新修改，请重新上传最新版"
_SELECTION_REQUIRED_MESSAGE = "找不到对应的本地工程，请在本地助手中选择一次"
logger = logging.getLogger(__name__)


def fingerprint_local_project(path: Path) -> str:
    root = path.resolve()
    return _fingerprint(root, _source_paths(root))


class BoundProject(FrozenModel):
    path: Path
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    package_names: tuple[str, ...]


def bind_local_project(path: Path) -> BoundProject:
    root = path.resolve()
    packages, _, _ = _packages(root)
    return BoundProject(
        path=root,
        fingerprint=fingerprint_local_project(root),
        package_names=tuple(package.name for package in packages),
    )


class AgentConfig(FrozenModel):
    version: int = 1
    agent_id: str
    name: str
    api_url: str = "http://127.0.0.1:8765"
    projects: dict[str, BoundProject] = Field(default_factory=dict)

    @field_validator("projects", mode="before")
    @classmethod
    def migrate_path_bindings(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        return {
            str(project_id): bind_local_project(Path(binding)) if isinstance(binding, str) else binding
            for project_id, binding in value.items()
        }

    @classmethod
    def load(cls, path: Path) -> AgentConfig:
        return cls.model_validate_json(path.read_text("utf-8"))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(self.model_dump_json(indent=2), "utf-8")
        os.replace(temporary, path)

    def select_project(self, assignment: JobView) -> Path | None:
        if assignment.project_fingerprint is None:
            binding = self.projects.get(assignment.project_id)
            return binding.path if binding is not None else None
        exact = {
            binding.path
            for binding in self.projects.values()
            if _current_fingerprint_matches(binding.path, assignment.project_fingerprint)
        }
        if len(exact) == 1:
            return next(iter(exact))
        compatible = [
            binding
            for binding in self.projects.values()
            if binding.package_names == assignment.package_names
        ]
        logger.debug("Project selection requires explicit confirmation; candidates=%d", len(compatible))
        return None


def _current_fingerprint_matches(path: Path, fingerprint: str) -> bool:
    try:
        return fingerprint_local_project(path) == fingerprint
    except OSError as error:
        logger.debug("Could not fingerprint local project at %s", path, exc_info=error)
        return False


def default_config_path() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data is None:
        raise RuntimeError("LOCALAPPDATA is unavailable")
    return Path(local_app_data) / "FigmaToFGUI" / "agent.json"


class AgentClient:
    def __init__(
        self,
        config: AgentConfig,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.config = config
        self.transport = transport

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self.config.api_url,
            transport=self.transport,
            timeout=httpx.Timeout(30, connect=5),
        )

    def register(self) -> AgentRegistration:
        registration = AgentRegistration(agent_id=self.config.agent_id, name=self.config.name)
        with self._client() as client:
            response = client.post("/v1/agents/register", json=registration.model_dump(mode="json"))
            response.raise_for_status()
        return registration

    def bind(self, project_id: str) -> ProjectBinding:
        binding = ProjectBinding(project_id=project_id, agent_id=self.config.agent_id)
        with self._client() as client:
            response = client.post("/v1/projects/bind", json=binding.model_dump(mode="json"))
            response.raise_for_status()
        return binding

    def poll_once(self) -> ApplyResult | None:
        with self._client() as client:
            response = client.get(f"/v1/agents/{self.config.agent_id}/assignments/next")
            if response.status_code == 204:
                return None
            response.raise_for_status()
            job = JobView.model_validate(response.json())
            project_path = self.config.select_project(job)
            if project_path is None:
                result = self._failure(job, "selection_required", _SELECTION_REQUIRED_MESSAGE)
            else:
                bundle_response = client.get(
                    f"/v1/agents/{self.config.agent_id}/assignments/{job.job_id}/artifact"
                )
                bundle_response.raise_for_status()
                bundle = ChangeBundle.model_validate(bundle_response.json())
                result = self._apply(job, bundle, project_path)
            report = client.post(
                f"/v1/jobs/{job.job_id}/apply-result",
                json=result.model_dump(mode="json"),
            )
            report.raise_for_status()
            return result

    def _apply(self, job: JobView, bundle: ChangeBundle, project_path: Path) -> ApplyResult:
        try:
            verify_pre_write(project_path, bundle)
            summary = apply_bundle(project_path, bundle)
        except SourceConflict as error:
            logger.debug("Local project pre-write verification failed at %s", project_path, exc_info=error)
            return self._failure(job, "local_project_changed", _LOCAL_PROJECT_CHANGED_MESSAGE)
        except (ApplyError, OSError) as error:
            rollback = getattr(error, "rollback_succeeded", None)
            return self._failure(job, type(error).__name__, "Local apply failed", rollback)
        return ApplyResult(
            job_id=job.job_id,
            agent_id=self.config.agent_id,
            project_id=job.project_id,
            status=ApplyStatus.APPLIED,
            changed_paths=summary.changed_paths,
        )

    def _failure(
        self,
        job: JobView,
        code: str,
        message: str,
        rollback_succeeded: bool | None = None,
    ) -> ApplyResult:
        return ApplyResult(
            job_id=job.job_id,
            agent_id=self.config.agent_id,
            project_id=job.project_id,
            status=ApplyStatus.FAILED,
            rollback_succeeded=rollback_succeeded,
            diagnostics=(Diagnostic(code=code, severity=Severity.ERROR, message=message),),
        )
