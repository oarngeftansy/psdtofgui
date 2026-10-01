from __future__ import annotations

import hashlib
import os
import re
import tempfile
from contextlib import suppress
from pathlib import Path

from figma_to_fgui.service_contracts import ChangeBundle

_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class ArtifactIntegrityError(ValueError):
    pass


class ArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    @staticmethod
    def _serialize(bundle: ChangeBundle) -> bytes:
        return bundle.model_dump_json(exclude_none=True).encode("utf-8")

    def put(self, bundle: ChangeBundle) -> str:
        payload = self._serialize(bundle)
        digest = hashlib.sha256(payload).hexdigest()
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.root / f"{digest}.json"
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{digest}-", suffix=".tmp", dir=self.root
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary, target)
            except FileExistsError:
                existing = target.read_bytes()
                if hashlib.sha256(existing).hexdigest() != digest:
                    raise ArtifactIntegrityError("artifact digest mismatch")
        finally:
            with suppress(OSError):
                temporary.unlink(missing_ok=True)
        return digest

    def get(self, digest: str) -> ChangeBundle:
        if not _DIGEST.fullmatch(digest):
            raise ArtifactIntegrityError("invalid artifact digest")
        payload = (self.root / f"{digest}.json").read_bytes()
        if hashlib.sha256(payload).hexdigest() != digest:
            raise ArtifactIntegrityError("artifact digest mismatch")
        return ChangeBundle.model_validate_json(payload)

