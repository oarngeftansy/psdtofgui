"""Run the automated HIFI replacement delivery acceptance flow."""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import sys
import time
from collections import Counter
from io import BytesIO
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

_ROOT = Path(__file__).resolve(strict=True).parents[1]
for _path in (_ROOT, _ROOT / "src"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from fastapi.testclient import TestClient
from httpx import Response
from lxml import etree

from figma_to_fgui.api import create_app

_FIXTURE = _ROOT / "tests" / "fixtures" / "hifi_replacement"
_TOKEN = b"hifi-acceptance-token"
_HEADERS = {"X-Figma-Plugin-Token": _TOKEN.decode()}
_COMPONENT = "assets/MyVillage/Panel/Panel_MyVillage_Sketchboard.xml"


def _request(client: TestClient, method: str, path: str, **kwargs: Any) -> Response:
    headers = {**_HEADERS, **kwargs.pop("headers", {})}
    response = client.request(method, path, headers=headers, **kwargs)
    if response.status_code >= 400:
        raise RuntimeError(f"{method} {path} failed with {response.status_code}")
    return response


def _project_zip() -> bytes:
    output = BytesIO()
    source = _FIXTURE / "old_project"
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for path in sorted(source.rglob("*")):
            if path.is_file() and ".figma-to-fgui-preview" not in path.parts:
                archive.writestr(path.relative_to(source).as_posix(), path.read_bytes())
    return output.getvalue()


def _target(tree: dict[str, Any], project_id: str) -> dict[str, object]:
    package = next(item for item in tree["packages"] if item["name"] == "MyVillage")
    directory = next(item for item in package["directories"] if item["path"] == "Panel")
    component = next(item for item in directory["components"] if item["resource_id"] == "sketch01")
    return {
        "version": 1,
        "project_id": project_id,
        "project_fingerprint": tree["project_fingerprint"],
        "package_id": package["package_id"],
        "package_name": package["name"],
        "directory": directory["path"],
        "component_id": component["resource_id"],
        "component_name": component["name"],
        "component_relative_path": component["relative_path"],
    }


def _decision(item: dict[str, Any]) -> tuple[str, str | None]:
    if item["status"] in {"suggested", "uncertain"}:
        return "retarget", str(item["candidates"][0])
    if item["status"] == "hifi_added":
        return ("add_visual", None) if item["figma_node_id"] == "progress-bubble" else ("exception", None)
    if item["status"] == "blocked":
        return "exception", None
    return "keep_old", None


def run_acceptance(workspace: Path, candidate_output: Path | None = None) -> dict[str, object]:
    with TemporaryDirectory(prefix="hifi-acceptance-") as raw:
        data_dir = Path(raw) / "data"
        client = TestClient(
            create_app(
                data_dir=data_dir,
                fixtures_root=workspace / "tests" / "fixtures",
                rules_path=workspace / "rules" / "default" / "classification.yaml",
                plugin_access_token=_TOKEN,
            )
        )
        client.__enter__()
        project = _request(
            client,
            "POST",
            "/v1/projects/uploads",
            files={"project": ("OldVillage.zip", _project_zip(), "application/zip")},
        ).json()
        tree = _request(client, "GET", f"/v1/projects/{project['project_id']}/hifi-targets").json()

        upload = _request(
            client,
            "POST",
            "/v1/figma/selections/uploads",
            json={"version": 1, "idempotency_key": "hifi-acceptance-selection"},
        ).json()
        manifest = json.loads((_FIXTURE / "hifi-selection.json").read_text("utf-8"))
        _request(
            client,
            "PUT",
            f"/v1/figma/selections/uploads/{upload['upload_id']}/manifest",
            content=json.dumps(manifest, ensure_ascii=False).encode(),
        )
        hifi_material = (_FIXTURE / "selection" / "resources" / "hifi-board").read_bytes()
        _request(
            client,
            "PUT",
            f"/v1/figma/selections/uploads/{upload['upload_id']}/resources/hifi-board",
            content=hifi_material,
            headers={"content-type": "image/png"},
        )
        selection = _request(
            client, "POST", f"/v1/figma/selections/uploads/{upload['upload_id']}/commit"
        ).json()
        created = _request(
            client,
            "POST",
            "/v1/hifi-replacements",
            json={
                "version": 1,
                "project_id": project["project_id"],
                "selection_id": selection["selection_id"],
                "target": _target(tree, project["project_id"]),
                "idempotency_key": "hifi-acceptance-replacement",
            },
        ).json()
        session_id = created["session_id"]
        initial = _request(client, "GET", f"/v1/hifi-replacements/{session_id}/mapping").json()
        initial_counts = Counter(str(item["status"]) for item in initial["items"])

        while True:
            mapping = _request(client, "GET", f"/v1/hifi-replacements/{session_id}/mapping").json()
            item = next((value for value in mapping["items"] if value["action"] is None), None)
            if item is None:
                break
            action, node_id = _decision(item)
            payload = {
                "version": 1,
                "mapping_revision": mapping["mapping_revision"],
                "item_id": item["item_id"],
                "action": action,
            }
            if node_id is not None:
                payload["figma_node_id"] = node_id
            _request(
                client,
                "POST",
                f"/v1/hifi-replacements/{session_id}/mapping-decisions",
                json=payload,
            )

        mapping = _request(client, "GET", f"/v1/hifi-replacements/{session_id}/mapping").json()
        _request(
            client,
            "POST",
            f"/v1/hifi-replacements/{session_id}/build",
            json={"version": 1, "mapping_revision": mapping["mapping_revision"]},
        )
        review = _request(client, "GET", f"/v1/hifi-replacements/{session_id}/review").json()
        candidate = _request(client, "GET", f"/v1/hifi-replacements/{session_id}/candidate/download").content
        candidate_sha256 = hashlib.sha256(candidate).hexdigest()
        if candidate_output is not None:
            candidate_output.parent.mkdir(parents=True, exist_ok=True)
            candidate_output.write_bytes(candidate)

        stale = client.post(
            f"/v1/hifi-replacements/{session_id}/approve",
            headers=_HEADERS,
            json={
                "version": 1,
                "layout_checked": True,
                "references_checked": True,
                "interactions_checked": True,
                "editor_version": "6.1.4",
                "candidate_sha256": "0" * 64,
            },
        )
        if stale.status_code != 409 or stale.json()["detail"]["code"] != "hifi_candidate_stale":
            raise RuntimeError("stale candidate hash was not rejected")
        approval = client.post(
            f"/v1/hifi-replacements/{session_id}/approve", headers=_HEADERS,
            json={"version": 1, "layout_checked": True, "references_checked": True,
                  "interactions_checked": True, "editor_version": "6.1.4",
                  "candidate_sha256": candidate_sha256},
        )
        delivery = client.get(f"/v1/hifi-replacements/{session_id}/download", headers=_HEADERS)
        if approval.status_code != 409 or delivery.status_code != 409:
            raise RuntimeError("Unverified candidate was allowed for delivery")

        with ZipFile(BytesIO(candidate)) as archive:
            names = set(archive.namelist())
            candidate_xml = etree.fromstring(archive.read(_COMPONENT))
        original_xml = etree.parse(str(_FIXTURE / "old_project" / _COMPONENT)).getroot()
        original_ids = {str(node.attrib["id"]) for node in original_xml.xpath("./displayList/*[@id]")}
        candidate_ids = {str(node.attrib["id"]) for node in candidate_xml.xpath("./displayList/*[@id]")}
        original_shared = original_xml.xpath("./displayList/*[@id='btn_next']")[0].attrib["src"]
        candidate_shared = candidate_xml.xpath("./displayList/*[@id='btn_next']")[0].attrib["src"]
        board = candidate_xml.xpath("./displayList/*[@id='board_bg']")[0]
        with ZipFile(BytesIO(candidate)) as archive:
            package = etree.fromstring(archive.read("assets/MyVillage/package.xml"))
            hifi_resource = package.xpath(f"./resources/image[@id='{board.attrib['src']}']")[0]
            hifi_path = str(
                PurePosixPath("assets/MyVillage")
                / hifi_resource.attrib["path"].strip("/")
                / hifi_resource.attrib["name"]
            )
            hifi_bytes = archive.read(hifi_path)
        allowed_prefix = "assets/MyVillage/Img/HIFI/Panel_MyVillage_Sketchboard/"
        changed_paths = [str(item["relative_path"]) for item in review["changed_files"]]
        scope_valid = all(
            path in {_COMPONENT, "assets/MyVillage/package.xml"} or path.startswith(allowed_prefix)
            for path in changed_paths
        )
        action_counts = Counter(str(item["action"]) for item in mapping["items"])
        object_counts = Counter(str(item["kind"]) for item in review["object_diffs"])
        result = {
            "schemaVersion": 1,
            "automatedStatus": "PASS",
            "editorStatus": "NOT_RUN",
            "editorReason": "This fixture runner does not perform an Editor verification; approval must remain blocked.",
            "mappingCounts": dict(sorted(initial_counts.items())),
            "decisionCounts": dict(sorted(action_counts.items())),
            "objectDiffCounts": dict(sorted(object_counts.items())),
            "candidateSha256": candidate_sha256,
            "deliveryBlocked": delivery.status_code == 409,
            "reviewSha256": review["candidate_sha256"],
            "approvalBlocked": approval.status_code == 409,
            "protectedIdentityUnchanged": original_ids <= candidate_ids,
            "sharedReferenceUnchanged": original_shared == candidate_shared,
            "lowfiReferenceReplaced": board.attrib["src"] != "imgboard01",
            "hifiResourceRegistered": hifi_path in names,
            "hifiResourceBytesMatch": hifi_bytes == hifi_material,
            "scopeValid": scope_valid,
            "archiveContainsTarget": _COMPONENT in names,
            "changedPaths": changed_paths,
            "warnings": review["warnings"],
        }
        client.__exit__(None, None, None)
        gc.collect()
        time.sleep(0.05)
        return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, default=_ROOT)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--candidate", type=Path)
    args = parser.parse_args()
    result = run_acceptance(args.workspace.resolve(), args.candidate)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes((json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode())
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
