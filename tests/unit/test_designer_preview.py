from __future__ import annotations

import base64
import hashlib
from pathlib import Path

from figma_to_fgui.designer_preview import build_designer_preview
from figma_to_fgui.models import Diagnostic, Severity
from figma_to_fgui.service_contracts import ChangeBundle, ChangeFile, FileOperation


def change(
    operation: FileOperation, relative_path: str, content: bytes, before: str | None = None
) -> ChangeFile:
    return ChangeFile(
        operation=operation,
        relative_path=relative_path,
        before_sha256=before,
        after_sha256="a" * 64,
        content_b64=base64.b64encode(content).decode("ascii"),
    )


def test_preview_uses_designer_labels_and_hides_technical_details(tmp_path: Path) -> None:
    before_root = tmp_path / "before"
    (before_root / "Sample" / "Panel").mkdir(parents=True)
    (before_root / "Sample" / "Panel" / "Main.xml").write_text("<component/>", "utf-8")
    bundle = ChangeBundle(
        job_id="job-1",
        project_id="project-1",
        files=(
            change(
                FileOperation.REPLACE,
                "Sample/Panel/Main.xml",
                b"<component name='Main'/>",
                "b" * 64,
            ),
            change(FileOperation.CREATE, "Sample/assets/Hero.png", b"new image"),
        ),
    )

    preview = build_designer_preview(
        before_root,
        bundle,
        (Diagnostic(code="geometry.rounded", severity=Severity.WARNING, message="Rounded x"),),
    )

    assert preview.summary == "1 项新增 · 1 项更新"
    assert {item.label for item in preview.changes} == {"界面：Main", "图片：Hero"}
    assert preview.changes[1].thumbnail_available is False
    assert preview.checks[0].status == "warning"
    normal_json = preview.model_dump_json()
    for forbidden in (
        "sha256",
        "changeset",
        "resource_id",
        "<component",
        "relative_path",
        "Sample/Panel",
    ):
        assert forbidden not in normal_json


def test_preview_marks_uploaded_tiff_images_as_thumbnail_aware(tmp_path: Path) -> None:
    before_root = tmp_path / "before"
    relative_path = "Sample/assets/Hero.tiff"
    preview_name = hashlib.sha256(relative_path.encode("utf-8")).hexdigest()[:16] + ".webp"
    thumbnail = before_root / ".figma-to-fgui-preview" / preview_name
    thumbnail.parent.mkdir(parents=True)
    thumbnail.write_bytes(b"thumbnail")
    bundle = ChangeBundle(
        job_id="job-1",
        project_id="project-1",
        files=(change(FileOperation.REPLACE, relative_path, b"image", "b" * 64),),
    )

    preview = build_designer_preview(before_root, bundle, ())

    assert preview.changes[0].label == "图片：Hero"
    assert preview.changes[0].thumbnail_available is True


def test_preview_exposes_only_opaque_job_scoped_image_urls(tmp_path: Path) -> None:
    before = tmp_path / "Sample" / "assets" / "Hero.png"
    before.parent.mkdir(parents=True)
    before.write_bytes(b"before image")
    bundle = ChangeBundle(
        job_id="job-1",
        project_id="project-1",
        files=(change(FileOperation.REPLACE, "Sample/assets/Hero.png", b"after image", "b" * 64),),
    )

    preview = build_designer_preview(
        tmp_path,
        bundle,
        (),
        image_url=lambda index, side: f"/v1/jobs/job-1/designer-preview/images/{index}/{side}",
    )

    image = preview.changes[0]
    assert image.before_image_url == "/v1/jobs/job-1/designer-preview/images/0/before"
    assert image.after_image_url == "/v1/jobs/job-1/designer-preview/images/0/after"
    serialized = preview.model_dump_json()
    assert "Sample/assets" not in serialized
    assert "sha256" not in serialized


def test_preview_translates_diagnostics_without_rule_or_path_details(tmp_path: Path) -> None:
    preview = build_designer_preview(
        tmp_path,
        ChangeBundle(job_id="job-1", project_id="project-1", files=()),
        (
            Diagnostic(
                code="geometry.rounded",
                severity=Severity.WARNING,
                message="Rounded x in Sample/Panel/Main.xml",
                path="Sample/Panel/Main.xml",
                rule_id="geometry.integer-output",
                rule_version=1,
            ),
        ),
    )

    assert preview.checks[0].status == "warning"
    assert preview.checks[0].message == "存在需要确认的调整"
    assert "Sample/Panel/Main.xml" not in preview.model_dump_json()
    assert "geometry.integer-output" not in preview.model_dump_json()
