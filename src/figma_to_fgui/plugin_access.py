from __future__ import annotations

import hmac
from dataclasses import dataclass

from fastapi import HTTPException, Request


@dataclass(frozen=True)
class PluginAccess:
    secret: bytes

    def require(self, request: Request) -> None:
        supplied = request.headers.get("x-figma-plugin-token", "").encode()
        if not supplied or not hmac.compare_digest(supplied, self.secret):
            raise HTTPException(
                status_code=401,
                detail={"code": "plugin_access_denied", "message": "Plugin request was denied."},
            )
